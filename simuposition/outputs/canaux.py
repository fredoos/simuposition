# -*- coding: utf-8 -*-
"""
Canaux de sortie : port série (pyserial), serveur TCP (diffusion aux clients connectés),
UDP (datagrammes vers hôte:port), journal fichier. Interface commune :

    canal.ouvrir() -> bool ; canal.ecrire(bytes) ; canal.fermer() ; canal.description

Tous les canaux sont tolérants : une erreur d'écriture est consignée via le rappel
`journal(msg)` et ne lève pas d'exception vers l'appelant.
"""
from __future__ import annotations

import socket
import threading
from typing import Callable, Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # pragma: no cover
    serial = None

Journal = Callable[[str], None]


def ports_serie() -> list[str]:
    """Ports série détectés (COMx sous Windows, /dev/tty* ailleurs)."""
    if serial is None:
        return []
    return [p.device for p in serial.tools.list_ports.comports()]


class CanalSerie:
    def __init__(self, port: str, vitesse: int = 9600, journal: Optional[Journal] = None):
        self.port_nom, self.vitesse = port, vitesse
        self.journal = journal or (lambda m: None)
        self.port = None

    @property
    def description(self) -> str:
        return f"série {self.port_nom} @ {self.vitesse} 8N1"

    def ouvrir(self) -> bool:
        if serial is None:
            self.journal("pyserial absent : sortie série indisponible")
            return False
        try:
            self.port = serial.Serial(self.port_nom, baudrate=self.vitesse, bytesize=8, parity="N",
                                      stopbits=1, timeout=0, write_timeout=0.5)
            self.journal(f"{self.description} : ouvert")
            return True
        except Exception as e:  # noqa: BLE001
            self.journal(f"{self.description} : ouverture impossible ({e})")
            self.port = None
            return False

    def ecrire(self, data: bytes) -> None:
        if self.port is None:
            return
        try:
            self.port.write(data)
        except Exception as e:  # noqa: BLE001
            self.journal(f"{self.description} : erreur d'écriture ({e})")
            self.fermer()

    def fermer(self) -> None:
        if self.port is not None:
            try:
                self.port.close()
            except Exception:  # noqa: BLE001
                pass
            self.port = None
            self.journal(f"{self.description} : fermé")


class CanalTCPServeur:
    """Serveur TCP : chaque client connecté reçoit le flux (comme un gpsd simplifié)."""

    def __init__(self, hote: str = "0.0.0.0", port: int = 10110, journal: Optional[Journal] = None):
        self.hote, self.port = hote, port
        self.journal = journal or (lambda m: None)
        self._srv: Optional[socket.socket] = None
        self._clients: list[socket.socket] = []
        self._verrou = threading.Lock()
        self._actif = False

    @property
    def description(self) -> str:
        return f"TCP serveur {self.hote}:{self.port}"

    @property
    def nb_clients(self) -> int:
        with self._verrou:
            return len(self._clients)

    def ouvrir(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.hote, self.port))
            s.listen(8)
            s.settimeout(0.5)
        except OSError as e:
            self.journal(f"{self.description} : impossible ({e})")
            return False
        self._srv = s
        self._actif = True
        threading.Thread(target=self._accepter, daemon=True, name="tcp-accept").start()
        self.journal(f"{self.description} : en écoute")
        return True

    def _accepter(self) -> None:
        while self._actif and self._srv is not None:
            try:
                c, adr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            c.setblocking(False)
            with self._verrou:
                self._clients.append(c)
            self.journal(f"{self.description} : client {adr[0]}:{adr[1]} connecté")

    def ecrire(self, data: bytes) -> None:
        morts = []
        with self._verrou:
            for c in self._clients:
                try:
                    c.sendall(data)
                except (BlockingIOError, InterruptedError):
                    pass                       # tampon plein : on saute cette trame
                except OSError:
                    morts.append(c)
            for c in morts:
                self._clients.remove(c)
                try:
                    c.close()
                except OSError:
                    pass
        if morts:
            self.journal(f"{self.description} : {len(morts)} client(s) déconnecté(s)")

    def fermer(self) -> None:
        self._actif = False
        with self._verrou:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
            self._srv = None
            self.journal(f"{self.description} : fermé")


class CanalUDP:
    """Émission de datagrammes vers hôte:port (unicast ou broadcast 255.255.255.255)."""

    def __init__(self, hote: str = "127.0.0.1", port: int = 10110, journal: Optional[Journal] = None):
        self.hote, self.port = hote, port
        self.journal = journal or (lambda m: None)
        self._sock: Optional[socket.socket] = None

    @property
    def description(self) -> str:
        return f"UDP → {self.hote}:{self.port}"

    def ouvrir(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if self.hote.endswith(".255") or self.hote == "255.255.255.255":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock = s
            self.journal(f"{self.description} : actif")
            return True
        except OSError as e:
            self.journal(f"{self.description} : impossible ({e})")
            return False

    def ecrire(self, data: bytes) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendto(data, (self.hote, self.port))
        except OSError as e:
            self.journal(f"{self.description} : erreur d'envoi ({e})")

    def fermer(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
            self.journal(f"{self.description} : fermé")


class CanalFichier:
    """Journal NMEA brut (ajout en fin de fichier)."""

    def __init__(self, chemin: str, journal: Optional[Journal] = None):
        self.chemin = chemin
        self.journal = journal or (lambda m: None)
        self._f = None

    @property
    def description(self) -> str:
        return f"fichier {self.chemin}"

    def ouvrir(self) -> bool:
        try:
            self._f = open(self.chemin, "ab", buffering=0)
            self.journal(f"{self.description} : ouvert")
            return True
        except OSError as e:
            self.journal(f"{self.description} : impossible ({e})")
            return False

    def ecrire(self, data: bytes) -> None:
        if self._f is not None:
            try:
                self._f.write(data)
            except OSError as e:
                self.journal(f"{self.description} : erreur d'écriture ({e})")
                self.fermer()

    def fermer(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None
            self.journal(f"{self.description} : fermé")


class Diffuseur:
    """Regroupe les canaux ouverts et leur distribue chaque lot de trames."""

    def __init__(self):
        self.canaux: list = []

    def ajouter(self, canal) -> bool:
        if canal.ouvrir():
            self.canaux.append(canal)
            return True
        return False

    def retirer(self, canal) -> None:
        if canal in self.canaux:
            canal.fermer()
            self.canaux.remove(canal)

    def diffuser(self, trames: list[str]) -> None:
        if not self.canaux:
            return
        data = "".join(trames).encode("ascii", errors="replace")
        for c in list(self.canaux):
            c.ecrire(data)

    def tout_fermer(self) -> None:
        for c in list(self.canaux):
            c.fermer()
        self.canaux.clear()
