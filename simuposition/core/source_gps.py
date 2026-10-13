# -*- coding: utf-8 -*-
"""
Entrée GPS réel (mode LIVE) : réception de trames NMEA 0183 et extraction d'un point de
navigation (`Fix`) par époque.

Transports (réception, symétriques des sorties de outputs/canaux.py) :
  - série (pyserial) : COMx / /dev/tty* à la vitesse choisie ;
  - TCP client : connexion à hote:port, lecture du flux (cas d'un GPS relayé par un serveur) ;
  - UDP : écoute sur un port local (datagrammes NMEA).

Le lecteur tourne dans un thread, reconstitue les lignes « $....*CS\\r\\n », les analyse et
publie le dernier `Fix` avec un numéro de séquence croissant. Le moteur lit `dernier_fix()` et
ne (re)calcule une sortie qu'au changement de séquence : une sortie par époque GPS, à la cadence
réelle du récepteur, sans répétition ni latence ajoutée.

Assemblage d'époque : on retient la phrase « porteuse » du flux (RMC de préférence, sinon GGA,
sinon GLL) ; chaque occurrence déclenche un `Fix`, enrichi de l'altitude (GGA), de la vitesse et
du cap (RMC/VTG) et de la qualité de fix (GGA) les plus récents. Analyse tolérante : une phrase
illisible ou un checksum faux est ignoré sans interrompre la réception.
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .modele import ConfigGPS

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None

Journal = Callable[[str], None]


@dataclass
class Fix:
    """Point de navigation reçu du GPS réel (vérité terrain)."""
    lat: float
    lon: float
    alt_m: Optional[float] = None       # altitude MSL (GGA) ; None si inconnue
    sog_ms: Optional[float] = None      # vitesse sol (m/s)
    cog_deg: Optional[float] = None     # cap sol (°)
    utc: Optional[datetime] = None      # horodatage UTC
    qualite: int = 1                    # indicateur de fix GGA (0 aucun, 1 GPS, 2 DGPS…)
    nb_sat: Optional[int] = None
    valide: bool = True


def _deg(champ: str, hemi: str) -> Optional[float]:
    """« ddmm.mmmm » + hémisphère → degrés décimaux signés."""
    if not champ or not hemi:
        return None
    point = champ.find(".")
    if point < 2:
        return None
    deg = int(champ[:point - 2])
    minutes = float(champ[point - 2:])
    val = deg + minutes / 60.0
    if hemi in ("S", "W", "O"):
        val = -val
    return val


def _flt(x: str) -> Optional[float]:
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def _heure_utc(hhmmss: str, date_ddmmyy: str = "") -> Optional[datetime]:
    """Combine l'heure NMEA (hhmmss[.ss]) et éventuellement la date (ddmmyy) en UTC.

    Sans date, la date du jour (UTC) est utilisée."""
    if not hhmmss or len(hhmmss) < 6:
        return None
    try:
        h, m = int(hhmmss[0:2]), int(hhmmss[2:4])
        s = float(hhmmss[4:])
        if date_ddmmyy and len(date_ddmmyy) >= 6:
            jj, mm, aa = int(date_ddmmyy[0:2]), int(date_ddmmyy[2:4]), int(date_ddmmyy[4:6])
            annee = 2000 + aa if aa < 80 else 1900 + aa   # pivot usuel de l'année NMEA sur 2 chiffres
        else:
            auj = datetime.now(timezone.utc)
            jj, mm, annee = auj.day, auj.month, auj.year
        return datetime(annee, mm, jj, h, m, int(s), int((s - int(s)) * 1e6), tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class SourceGPS:
    """Réception NMEA et publication du dernier `Fix` (thread-safe)."""

    def __init__(self, config: ConfigGPS, journal: Optional[Journal] = None):
        self.config = config
        self.journal = journal or (lambda m: None)
        self._fix: Optional[Fix] = None
        self._seq = 0
        self._verrou = threading.Lock()
        self._actif = False
        self._thread: Optional[threading.Thread] = None
        self._t_dernier = 0.0
        # reconstitution des lignes et champs d'époque
        self._buf = b""
        self._porteuse: Optional[str] = None     # "RMC" | "GGA" | "GLL" (détectée dynamiquement)
        self._date = ""                          # dernière date RMC (pour horodater les GGA/GLL)
        self._alt: Optional[float] = None
        self._sog: Optional[float] = None
        self._cog: Optional[float] = None
        self._qualite = 1
        self._nb_sat: Optional[int] = None

    # ---- cycle de vie --------------------------------------------------------------
    @property
    def description(self) -> str:
        c = self.config
        if c.transport == "serie":
            return f"GPS série {c.port_serie} @ {c.vitesse_serie}"
        if c.transport == "tcp":
            return f"GPS TCP {c.hote}:{c.port_reseau}"
        return f"GPS UDP :{c.port_reseau}"

    def ouvrir(self) -> bool:
        if self._actif:
            return True
        self._actif = True
        self._thread = threading.Thread(target=self._boucle, daemon=True, name="gps-in")
        self._thread.start()
        self.journal(f"{self.description} : réception démarrée")
        return True

    def fermer(self) -> None:
        self._actif = False
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.0)
        self._thread = None

    # ---- lecture par le moteur -----------------------------------------------------
    def dernier_fix(self) -> tuple[Optional[Fix], int]:
        with self._verrou:
            return self._fix, self._seq

    def age_s(self) -> float:
        """Temps écoulé depuis le dernier fix (grand = perte de signal) ; inf si jamais reçu."""
        if self._t_dernier <= 0.0:
            return float("inf")
        return time.monotonic() - self._t_dernier

    # ---- thread de réception -------------------------------------------------------
    def _boucle(self) -> None:
        if self.config.transport == "serie":
            self._boucle_serie()
        elif self.config.transport == "tcp":
            self._boucle_tcp()
        else:
            self._boucle_udp()

    def _boucle_serie(self) -> None:
        if serial is None:
            self.journal("pyserial absent : entrée série GPS indisponible")
            return
        while self._actif:
            try:
                port = serial.Serial(self.config.port_serie, baudrate=self.config.vitesse_serie,
                                     bytesize=8, parity="N", stopbits=1, timeout=0.3)
            except Exception as e:  # noqa: BLE001
                self.journal(f"{self.description} : ouverture impossible ({e})")
                self._attendre(2.0)
                continue
            self.journal(f"{self.description} : ouvert")
            try:
                while self._actif:
                    data = port.read(512)
                    if data:
                        self._traiter(data)
            except Exception as e:  # noqa: BLE001
                self.journal(f"{self.description} : lecture interrompue ({e})")
            finally:
                try:
                    port.close()
                except Exception:  # noqa: BLE001
                    pass

    def _boucle_tcp(self) -> None:
        while self._actif:
            try:
                s = socket.create_connection((self.config.hote, self.config.port_reseau), timeout=3.0)
                s.settimeout(0.5)
            except OSError as e:
                self.journal(f"{self.description} : connexion impossible ({e})")
                self._attendre(2.0)
                continue
            self.journal(f"{self.description} : connecté")
            try:
                while self._actif:
                    try:
                        data = s.recv(1024)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    self._traiter(data)
            except OSError as e:
                self.journal(f"{self.description} : lecture interrompue ({e})")
            finally:
                try:
                    s.close()
                except OSError:
                    pass
            self._attendre(1.0)

    def _boucle_udp(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", self.config.port_reseau))
            s.settimeout(0.5)
        except OSError as e:
            self.journal(f"{self.description} : écoute impossible ({e})")
            return
        self.journal(f"{self.description} : en écoute")
        try:
            while self._actif:
                try:
                    data, _ = s.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._traiter(data)
        finally:
            try:
                s.close()
            except OSError:
                pass

    def _attendre(self, secondes: float) -> None:
        """Attente fragmentée pour rester réactif à la demande d'arrêt."""
        fin = time.monotonic() + secondes
        while self._actif and time.monotonic() < fin:
            time.sleep(0.1)

    # ---- analyse NMEA --------------------------------------------------------------
    def _traiter(self, data: bytes) -> None:
        self._buf += data
        if len(self._buf) > 65536:                       # garde-fou : flux sans fin de ligne
            self._buf = self._buf[-4096:]
        while b"\n" in self._buf:
            ligne, self._buf = self._buf.split(b"\n", 1)
            self._traiter_ligne(ligne.decode("ascii", errors="replace").strip())

    def _traiter_ligne(self, ligne: str) -> None:
        if not ligne.startswith("$") or "*" not in ligne:
            return
        corps, _, cs = ligne[1:].partition("*")
        if len(cs) >= 2:                                 # checksum présent : on le vérifie
            x = 0
            for ch in corps:
                x ^= ord(ch)
            if f"{x:02X}" != cs[:2].upper():
                return
        champs = corps.split(",")
        if len(champs) < 2 or len(champs[0]) < 5:
            return
        phrase = champs[0][2:]                            # retire le talker (GP/GN/GL/GA/GB)
        try:
            if phrase == "RMC":
                self._rmc(champs)
            elif phrase == "GGA":
                self._gga(champs)
            elif phrase == "VTG":
                self._vtg(champs)
            elif phrase == "GLL":
                self._gll(champs)
        except (ValueError, IndexError):
            return

    def _rmc(self, c: list[str]) -> None:
        # $..RMC,hhmmss,A,lat,N,lon,E,sog_kn,cog,ddmmyy,...
        if c[2] == "V":                                   # fix invalide : on ignore l'époque
            return
        lat = _deg(c[3], c[4])
        lon = _deg(c[5], c[6])
        if lat is None or lon is None:
            return
        self._date = c[9] if len(c) > 9 and c[9] else self._date
        sog = _flt(c[7])
        self._sog = sog * 1852.0 / 3600.0 if sog is not None else self._sog
        cog = _flt(c[8])
        self._cog = cog if cog is not None else self._cog
        self._porteuse = "RMC"                            # la RMC prime (vitesse, cap, date, heure)
        self._publier(lat, lon, _heure_utc(c[1], self._date))

    def _gga(self, c: list[str]) -> None:
        # $..GGA,hhmmss,lat,N,lon,E,qual,nbsat,hdop,alt,M,ondul,M,...
        qual = int(c[6]) if len(c) > 6 and c[6].isdigit() else self._qualite
        self._qualite = qual
        self._nb_sat = int(c[7]) if len(c) > 7 and c[7].isdigit() else self._nb_sat
        alt = _flt(c[9]) if len(c) > 9 else None
        if alt is not None:
            self._alt = alt
        if qual == 0:
            return
        lat = _deg(c[2], c[3])
        lon = _deg(c[4], c[5])
        if lat is None or lon is None:
            return
        if self._porteuse is None:
            self._porteuse = "GGA"
        if self._porteuse == "GGA":
            self._publier(lat, lon, _heure_utc(c[1], self._date))

    def _vtg(self, c: list[str]) -> None:
        # $..VTG,cog_true,T,cog_mag,M,sog_kn,N,sog_kmh,K,mode
        cog = _flt(c[1])
        if cog is not None:
            self._cog = cog
        sog = _flt(c[5])
        if sog is not None:
            self._sog = sog * 1852.0 / 3600.0

    def _gll(self, c: list[str]) -> None:
        # $..GLL,lat,N,lon,E,hhmmss,A,mode
        if len(c) < 7 or c[6] == "V":
            return
        lat = _deg(c[1], c[2])
        lon = _deg(c[3], c[4])
        if lat is None or lon is None:
            return
        if self._porteuse is None:
            self._porteuse = "GLL"
        if self._porteuse == "GLL":
            self._publier(lat, lon, _heure_utc(c[5], self._date))

    def _publier(self, lat: float, lon: float, utc: Optional[datetime]) -> None:
        fix = Fix(lat=lat, lon=lon, alt_m=self._alt, sog_ms=self._sog, cog_deg=self._cog,
                  utc=utc or datetime.now(timezone.utc), qualite=self._qualite,
                  nb_sat=self._nb_sat, valide=True)
        with self._verrou:
            self._fix = fix
            self._seq += 1
        self._t_dernier = time.monotonic()


def creer_source(config: ConfigGPS, journal: Optional[Journal] = None) -> SourceGPS:
    """Fabrique une source GPS d'après la configuration (sans l'ouvrir)."""
    return SourceGPS(config, journal)
