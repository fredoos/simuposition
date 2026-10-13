# -*- coding: utf-8 -*-
"""
Constellations GNSS : positions des satellites, visibilité, C/N0 simulé, DOP.

Deux sources de données :
  - « nominal » : constellations de Walker aux paramètres orbitaux nominaux (GPS 6 plans,
    GLONASS 3 plans, Galileo 3 plans, BeiDou-3 MEO 3 plans + IGSO + GEO), propagées en
    mouvement circulaire képlérien avec dérive séculaire J2 du nœud ascendant. Aucune
    donnée externe : fonctionne hors ligne, géométrie et DOP réalistes, mais les
    satellites ne sont pas les vrais à un instant donné ;
  - « tle » : éléments à deux lignes CelesTrak (groupes gps-ops, glo-ops, galileo, beidou)
    propagés par SGP4 (paquet `sgp4`) : positions réelles à quelques km près. Les fichiers
    sont téléchargés par l'application et mis en cache localement (`cache_tle/`).

Repère : positions inertielles (TEME/ECI) → ECEF par rotation du temps sidéral de Greenwich
(GMST, IAU 1982, UTC ≈ UT1) → ENU au point d'observation → élévation / azimut.

DOP : matrice de géométrie H = [-cos(el)·sin(az), -cos(el)·cos(az), -sin(el), 1] par
satellite utilisé, Q = (HᵀH)⁻¹, HDOP = √(Q11+Q22), VDOP = √Q33, PDOP, TDOP, GDOP.
Une seule colonne d'horloge est utilisée pour toutes les constellations (biais
inter-systèmes supposés connus), ce qui est l'hypothèse des récepteurs modernes.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np

from . import geodesie as geo
from ..outputs.nmea import Satellite

MU = 3.986_004_418e14        # m³/s²
J2 = 1.082_63e-3
RE = 6_378_137.0
SYSTEMES = ("GPS", "GLONASS", "Galileo", "BeiDou")
EPOQUE_NOMINALE = datetime(2026, 1, 1, tzinfo=timezone.utc)

URLS_CELESTRAK = {
    "GPS": "https://celestrak.org/NORAD/elements/gp.php?GROUP=gps-ops&FORMAT=tle",
    "GLONASS": "https://celestrak.org/NORAD/elements/gp.php?GROUP=glo-ops&FORMAT=tle",
    "Galileo": "https://celestrak.org/NORAD/elements/gp.php?GROUP=galileo&FORMAT=tle",
    "BeiDou": "https://celestrak.org/NORAD/elements/gp.php?GROUP=beidou&FORMAT=tle",
}


# ==================================================================================
# Temps
# ==================================================================================
def jour_julien(t: datetime) -> float:
    """Date julienne (UTC)."""
    t = t.astimezone(timezone.utc)
    return 2440587.5 + (t - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() / 86400.0


def gmst_rad(t: datetime) -> float:
    """Temps sidéral moyen de Greenwich (IAU 1982) en radians."""
    jd = jour_julien(t)
    T = (jd - 2451545.0) / 36525.0
    secondes = 67310.54841 + (876600.0 * 3600 + 8640184.812866) * T + 0.093104 * T * T - 6.2e-6 * T ** 3
    return math.radians((secondes / 240.0) % 360.0)


def eci_vers_ecef(x: float, y: float, z: float, t: datetime) -> tuple[float, float, float]:
    th = gmst_rad(t)
    c, s = math.cos(th), math.sin(th)
    return c * x + s * y, -s * x + c * y, z


# ==================================================================================
# Satellites nominaux (Walker)
# ==================================================================================
@dataclass
class SatNominal:
    systeme: str
    ident: int
    a: float            # demi-grand axe (m)
    inc: float          # inclinaison (rad)
    raan0: float        # nœud ascendant à l'époque (rad)
    u0: float           # argument de latitude à l'époque (rad)

    def position_eci(self, t: datetime) -> tuple[float, float, float]:
        dt = (t - EPOQUE_NOMINALE).total_seconds()
        raan_dot, u_dot = derives_seculaires(self.a, self.inc)
        raan = self.raan0 + raan_dot * dt
        u = self.u0 + u_dot * dt
        cu, su = math.cos(u), math.sin(u)
        cO, sO, ci, si = math.cos(raan), math.sin(raan), math.cos(self.inc), math.sin(self.inc)
        x = self.a * (cu * cO - su * sO * ci)
        y = self.a * (cu * sO + su * cO * ci)
        z = self.a * su * si
        return x, y, z


OMEGA_TERRE = 7.292_115_0e-5   # rad/s


def derives_seculaires(a: float, inc: float) -> tuple[float, float]:
    """Dérives séculaires J2 d'une orbite circulaire : (Ω̇, u̇) avec u = ω + M.
    Ω̇ = −3/2 n J2 (Re/a)² cos i ; ω̇ = 3/4 n J2 (Re/a)² (5cos²i − 1) ; Ṁ = n[1 + 3/4 J2 (Re/a)² (3cos²i − 1)]."""
    n = math.sqrt(MU / a ** 3)
    q = J2 * (RE / a) ** 2
    c2 = math.cos(inc) ** 2
    raan_dot = -1.5 * n * q * math.cos(inc)
    u_dot = n * (1.0 + 0.75 * q * (5.0 * c2 - 1.0) + 0.75 * q * (3.0 * c2 - 1.0))
    return raan_dot, u_dot


def demi_grand_axe_geosynchrone(inc_deg: float) -> float:
    """Demi-grand axe tel que la dérive de longitude (u̇ + Ω̇ − ω_terre) soit nulle (J2 inclus)."""
    a = (MU / OMEGA_TERRE ** 2) ** (1.0 / 3.0)
    inc = math.radians(inc_deg)
    for _ in range(5):                         # itération de point fixe (converge en 2 pas)
        raan_dot, u_dot = derives_seculaires(a, inc)
        facteur = (u_dot + raan_dot) / math.sqrt(MU / a ** 3)   # = n_effectif / n_képlérien
        a = (MU / (OMEGA_TERRE / facteur) ** 2) ** (1.0 / 3.0)
    return a


def _walker(systeme: str, total: int, plans: int, phase: int, a_m: float, inc_deg: float,
            raan_offset_deg: float, ident_depart: int) -> list[SatNominal]:
    """Constellation de Walker T/P/F (delta)."""
    out = []
    par_plan = total // plans
    for p in range(plans):
        raan = math.radians(raan_offset_deg + 360.0 * p / plans)
        for k in range(par_plan):
            u = math.radians(360.0 * k / par_plan + 360.0 * phase * p / total)
            idx = p * par_plan + k
            out.append(SatNominal(systeme, ident_depart + idx, a_m, math.radians(inc_deg), raan, u))
    return out


def constellation_nominale() -> list[SatNominal]:
    sats: list[SatNominal] = []
    # GPS : 6 plans, 55°, a = 26 559,7 km, 30 satellites (PRN 1–30), phasage Walker 30/6/1
    sats += _walker("GPS", 30, 6, 1, 26_559_700.0, 55.0, 0.0, 1)
    # GLONASS : 24/3/1, 64,8°, a = 25 508 km, slots 1–24 → identifiants NMEA 65–88
    sats += _walker("GLONASS", 24, 3, 1, 25_508_000.0, 64.8, 20.0, 65)
    # Galileo : 24/3/1, 56°, a = 29 600 km, E1–E24
    sats += _walker("Galileo", 24, 3, 1, 29_600_000.0, 56.0, 40.0, 1)
    # BeiDou-3 : MEO 24/3/1, 55°, a = 27 906 km (C19–C42) ; IGSO ×3 (C6–C8) ; GEO ×3 (C1–C3)
    sats += _walker("BeiDou", 24, 3, 1, 27_906_000.0, 55.0, 60.0, 19)
    th0 = gmst_rad(EPOQUE_NOMINALE)
    a_geo = demi_grand_axe_geosynchrone(55.0)
    for k in range(3):
        # IGSO : inclinaison 55°, période sidérale, nœuds espacés de 120° et phasés pour que
        # chaque satellite croise l'équateur vers 118°E (trace en « 8 » sur l'Asie)
        raan = th0 + math.radians(118.0 + 120.0 * k)
        sats.append(SatNominal("BeiDou", 6 + k, a_geo, math.radians(55.0), raan, math.radians(-120.0 * k)))
    a_geo0 = demi_grand_axe_geosynchrone(0.0)
    for k, lon_deg in enumerate((80.0, 110.5, 140.0)):
        # GEO : inclinaison 0, position fixée par la longitude à l'époque
        sats.append(SatNominal("BeiDou", 1 + k, a_geo0, 0.0, 0.0, th0 + math.radians(lon_deg)))
    return sats


# ==================================================================================
# Satellites TLE (SGP4)
# ==================================================================================
@dataclass
class SatTLE:
    systeme: str
    ident: int
    nom: str
    satrec: object

    def position_eci(self, t: datetime) -> tuple[float, float, float]:
        jd = jour_julien(t)
        fr = jd - math.floor(jd)
        e, r, _ = self.satrec.sgp4(math.floor(jd), fr)
        if e != 0:
            raise ValueError(f"SGP4 erreur {e} sur {self.nom}")
        return r[0] * 1000.0, r[1] * 1000.0, r[2] * 1000.0


_RE_PRN = re.compile(r"PRN\s*([A-Z]?)(\d+)")


def _ident_depuis_nom(systeme: str, nom: str, rang: int) -> int:
    m = _RE_PRN.search(nom)
    if m and systeme in ("GPS", "Galileo"):
        return int(m.group(2))
    if systeme == "GLONASS":
        return 65 + rang                     # pas de numéro de slot dans les TLE : séquentiel
    if systeme == "BeiDou":
        n = nom.upper()
        if " G" in n or "GEO" in n:
            return 1 + rang % 5
        if " IGSO" in n or "-I" in n:
            return 6 + rang % 13
        return 19 + rang % 45
    return 1 + rang


def charger_tle(texte: str, systeme: str) -> list[SatTLE]:
    """Analyse un fichier TLE 3 lignes (nom, L1, L2) ; les objets non propagés sont ignorés."""
    from sgp4.api import Satrec
    lignes = [l.rstrip() for l in texte.splitlines() if l.strip()]
    out: list[SatTLE] = []
    i, rang = 0, 0
    while i < len(lignes):
        if i + 1 < len(lignes) and lignes[i].startswith("1 ") and lignes[i + 1].startswith("2 "):
            nom, l1, l2 = f"{systeme}-{rang + 1}", lignes[i], lignes[i + 1]        # format 2 lignes
            i += 2
        elif i + 2 < len(lignes) and lignes[i + 1].startswith("1 ") and lignes[i + 2].startswith("2 "):
            nom, l1, l2 = lignes[i].strip(), lignes[i + 1], lignes[i + 2]           # format 3 lignes
            i += 3
        else:
            i += 1
            continue
        try:
            sr = Satrec.twoline2rv(l1, l2)
        except Exception:  # noqa: BLE001
            continue
        out.append(SatTLE(systeme, _ident_depuis_nom(systeme, nom, rang), nom, sr))
        rang += 1
    return out


def telecharger_tle(dossier_cache: str, systemes=SYSTEMES, delai_s: float = 20.0, journal=None) -> dict[str, str]:
    """Télécharge les TLE CelesTrak dans `dossier_cache` ; retourne {système: chemin}."""
    os.makedirs(dossier_cache, exist_ok=True)
    out = {}
    for sy in systemes:
        chemin = os.path.join(dossier_cache, f"{sy.lower()}.tle")
        try:
            req = urllib.request.Request(URLS_CELESTRAK[sy], headers={"User-Agent": "SimuPosition/0.1"})
            with urllib.request.urlopen(req, timeout=delai_s) as r:
                data = r.read()
            with open(chemin, "wb") as f:
                f.write(data)
            out[sy] = chemin
            if journal:
                journal(f"TLE {sy} : {len(data)} octets")
        except Exception as e:  # noqa: BLE001
            if journal:
                journal(f"TLE {sy} : échec ({e})")
    return out


def age_cache_tle(dossier_cache: str) -> Optional[float]:
    """Âge (jours) du fichier TLE le plus ancien du cache, None si absent."""
    ages = []
    for sy in SYSTEMES:
        p = os.path.join(dossier_cache, f"{sy.lower()}.tle")
        if os.path.exists(p):
            ages.append((time.time() - os.path.getmtime(p)) / 86400.0)
    return max(ages) if ages else None


# ==================================================================================
# Calcul du ciel
# ==================================================================================
@dataclass
class Ciel:
    satellites: list[Satellite] = field(default_factory=list)   # visibles (au-dessus du masque)
    pdop: float = 99.9
    hdop: float = 99.9
    vdop: float = 99.9
    tdop: float = 99.9
    gdop: float = 99.9
    fix: int = 0            # 0 aucun, 1 GPS
    mode_3d: int = 1        # 1 aucun, 2 = 2D, 3 = 3D
    nb_suivis: int = 0      # satellites au-dessus de l'horizon, masque compris

    @property
    def nb_utilises(self) -> int:
        return sum(1 for s in self.satellites if s.utilise)


class Constellations:
    """Gestionnaire des satellites et du calcul de visibilité."""

    def __init__(self, dossier_cache: str = "cache_tle"):
        self.dossier_cache = dossier_cache
        self.actifs = {s: True for s in SYSTEMES}
        self.source = "nominal"               # "nominal" | "tle"
        self.masque_deg = 5.0
        self.max_canaux = 32                  # canaux de poursuite du récepteur simulé
        self.cn0_min_utilise = 28.0           # dB-Hz : en dessous, suivi mais non utilisé
        self.bruit_cn0_db = 1.5
        self.decrochage_pct = 0.0             # % de satellites visibles « perdus » aléatoirement
        self._nominal = constellation_nominale()
        self._tle: dict[str, list[SatTLE]] = {}
        self.erreur_tle = ""

    # ---- données -----------------------------------------------------------------
    def charger_cache_tle(self) -> int:
        """Charge les TLE présents dans le cache ; retourne le nombre de satellites."""
        self._tle = {}
        n = 0
        for sy in SYSTEMES:
            p = os.path.join(self.dossier_cache, f"{sy.lower()}.tle")
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8", errors="ignore") as f:
                        self._tle[sy] = charger_tle(f.read(), sy)
                    n += len(self._tle[sy])
                except Exception as e:  # noqa: BLE001
                    self.erreur_tle = f"{sy} : {e}"
        return n

    def telecharger(self, journal=None) -> int:
        telecharger_tle(self.dossier_cache, SYSTEMES, journal=journal)
        return self.charger_cache_tle()

    def _liste(self):
        if self.source == "tle" and self._tle:
            return [s for sy, lst in self._tle.items() for s in lst if self.actifs.get(sy)]
        return [s for s in self._nominal if self.actifs.get(s.systeme)]

    # ---- calcul ------------------------------------------------------------------
    def ciel(self, lat: float, lon: float, h_m: float, t: datetime) -> Ciel:
        """Satellites visibles, C/N0, satellites utilisés et DOP au point et à l'instant donnés."""
        xo, yo, zo = geo.geodesique_vers_ecef(lat, lon, h_m)
        visibles: list[tuple[Satellite, float, float]] = []
        suivis = 0
        for s in self._liste():
            try:
                xi, yi, zi = s.position_eci(t)
            except ValueError:
                continue
            xs, ys, zs = eci_vers_ecef(xi, yi, zi, t)
            e, n, u = geo.ecef_vers_enu(xs - xo, ys - yo, zs - zo, lat, lon)
            r = math.sqrt(e * e + n * n + u * u)
            el = math.degrees(math.asin(u / r))
            if el <= 0.0:
                continue
            suivis += 1
            if el < self.masque_deg:
                continue
            az = math.degrees(math.atan2(e, n)) % 360.0
            cn0 = self._cn0(s.systeme, s.ident, el, t)
            visibles.append((Satellite(s.systeme, s.ident, el, az, cn0, False), el, az))
        # décrochages aléatoires (déterministes à la minute) si demandé
        if self.decrochage_pct > 0:
            visibles = [v for v in visibles if self._alea(v[0].systeme, v[0].ident, t, "drop") * 100.0 >= self.decrochage_pct]
        # sélection des satellites utilisés : meilleurs C/N0, dans la limite des canaux
        visibles.sort(key=lambda v: -v[0].cn0)
        utilises = 0
        for sat, el, az in visibles:
            if utilises < self.max_canaux and sat.cn0 >= self.cn0_min_utilise:
                sat.utilise = True
                utilises += 1
        ciel = Ciel(satellites=[v[0] for v in visibles], nb_suivis=suivis)
        self._dop(ciel, [(el, az) for sat, el, az in visibles if sat.utilise])
        return ciel

    def _alea(self, systeme: str, ident: int, t: datetime, sel: str) -> float:
        """Pseudo-aléa déterministe dans [0,1) stable sur une minute (pas de scintillement)."""
        clef = f"{sel}:{systeme}:{ident}:{t.strftime('%Y%m%d%H%M')}".encode()
        return int.from_bytes(hashlib.blake2b(clef, digest_size=4).digest(), "big") / 2 ** 32

    def _cn0(self, systeme: str, ident: int, el_deg: float, t: datetime) -> float:
        """C/N0 (dB-Hz) : ~30 à l'horizon, ~47 au zénith, bruit déterministe ±bruit_cn0_db."""
        base = 30.0 + 17.0 * math.sin(math.radians(el_deg))
        bruit = (self._alea(systeme, ident, t, "cn0") - 0.5) * 2.0 * self.bruit_cn0_db
        return max(20.0, min(52.0, base + bruit))

    @staticmethod
    def _dop(ciel: Ciel, utilises: list[tuple[float, float]]) -> None:
        n = len(utilises)
        if n < 3:
            ciel.fix, ciel.mode_3d = 0, 1
            return
        H = np.zeros((n, 4))
        for i, (el, az) in enumerate(utilises):
            e, a = math.radians(el), math.radians(az)
            H[i] = (-math.cos(e) * math.sin(a), -math.cos(e) * math.cos(a), -math.sin(e), 1.0)
        try:
            if n == 3:
                # 2D : hauteur supposée connue → on retire la colonne verticale
                Q = np.linalg.inv(H[:, [0, 1, 3]].T @ H[:, [0, 1, 3]])
                ciel.hdop = math.sqrt(Q[0, 0] + Q[1, 1])
                ciel.vdop = 99.9
                ciel.pdop = ciel.hdop
                ciel.tdop = math.sqrt(Q[2, 2])
                ciel.gdop = math.sqrt(np.trace(Q))
                ciel.fix, ciel.mode_3d = 1, 2
                return
            Q = np.linalg.inv(H.T @ H)
        except np.linalg.LinAlgError:
            ciel.fix, ciel.mode_3d = 0, 1
            return
        ciel.hdop = math.sqrt(Q[0, 0] + Q[1, 1])
        ciel.vdop = math.sqrt(Q[2, 2])
        ciel.pdop = math.sqrt(Q[0, 0] + Q[1, 1] + Q[2, 2])
        ciel.tdop = math.sqrt(Q[3, 3])
        ciel.gdop = math.sqrt(np.trace(Q))
        ciel.fix, ciel.mode_3d = 1, 3
