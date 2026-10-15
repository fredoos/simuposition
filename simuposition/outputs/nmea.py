# -*- coding: utf-8 -*-
"""
Génération de trames NMEA 0183 (GGA, RMC, GSA, GSV, VTG, GLL, ZDA).

Deux dialectes :
  - « GN » (NMEA 4.10 multi-GNSS) : phrases de position avec talker GN, une GSA par système
    (avec identifiant de système : 1 GPS, 2 GLONASS, 3 Galileo, 4 BeiDou), GSV par talker
    (GP/GL/GA/GB) avec identifiant de signal ;
  - « GP » (NMEA 2.3, récepteurs anciens) : tout en talker GP, satellites GPS seulement.

Identifiants satellites NMEA : GPS PRN 1–32, GLONASS 65–96 (64 + numéro de slot),
Galileo 1–36 (talker GA), BeiDou 1–63 (talker GB).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..core import geodesie as geo

TALKERS = {"GPS": "GP", "GLONASS": "GL", "Galileo": "GA", "BeiDou": "GB"}
ID_SYSTEME = {"GPS": 1, "GLONASS": 2, "Galileo": 3, "BeiDou": 4}
ID_SIGNAL = {"GPS": 1, "GLONASS": 1, "Galileo": 7, "BeiDou": 1}   # L1 C/A, G1, E1 B/C, B1I
PHRASES = ("GGA", "RMC", "GSA", "GSV", "VTG", "GLL", "ZDA")


@dataclass
class Satellite:
    systeme: str          # clé de TALKERS
    ident: int            # identifiant NMEA
    elevation: float      # °
    azimut: float         # °
    cn0: float            # dB-Hz
    utilise: bool = True  # participe à la solution


@dataclass
class Solution:
    """Instantané de navigation à convertir en trames."""
    temps: datetime
    lat: float
    lon: float
    alt_msl_m: float
    ondulation_m: float
    vitesse_ms: float
    cap: float
    fix: int = 1                          # 0 aucun, 1 GPS, 2 DGPS
    mode_3d: int = 3                      # 1 pas de fix, 2 = 2D, 3 = 3D
    pdop: float = 1.8
    hdop: float = 1.0
    vdop: float = 1.5
    satellites: list[Satellite] = field(default_factory=list)
    declinaison: Optional[float] = None   # ° (positif = Est) ; None = champ vide

    @property
    def nb_utilises(self) -> int:
        return sum(1 for s in self.satellites if s.utilise)


def somme_controle(corps: str) -> str:
    """Checksum NMEA : XOR des caractères entre '$' et '*'."""
    x = 0
    for ch in corps:
        x ^= ord(ch)
    return f"{x:02X}"


def phrase(corps: str) -> str:
    """Encapsule un corps 'GPGGA,...' en '$GPGGA,...*CS\\r\\n'."""
    return f"${corps}*{somme_controle(corps)}\r\n"


def verifier(trame: str) -> bool:
    """Vrai si la trame '$...*CS' a un checksum correct."""
    t = trame.strip()
    if not t.startswith("$") or "*" not in t:
        return False
    corps, cs = t[1:].rsplit("*", 1)
    return somme_controle(corps) == cs.upper()


def _deg_min(v: float) -> tuple[int, float]:
    """(degrés, minutes) arrondis à 1e-5 min SANS produire « 60.00000 » (49°59.999999' → 50°00.00000')."""
    m_tot = round(abs(v) * 60.0, 5)
    d = int(m_tot // 60.0)
    m = round(m_tot - d * 60.0, 5)
    if m >= 60.0:
        d, m = d + 1, 0.0
    return d, m


def _lat(v: float) -> tuple[str, str]:
    h = "N" if v >= 0 else "S"
    d, m = _deg_min(v)
    return f"{d:02d}{m:08.5f}", h


def _lon(v: float) -> tuple[str, str]:
    h = "E" if v >= 0 else "W"
    d, m = _deg_min(v)
    return f"{d:03d}{m:08.5f}", h


def _heure(t: datetime) -> str:
    return t.strftime("%H%M%S") + f".{t.microsecond // 10000:02d}"


def _date(t: datetime) -> str:
    return t.strftime("%d%m%y")


class GenerateurNMEA:
    def __init__(self, dialecte: str = "GN", phrases: Optional[set[str]] = None):
        self.dialecte = dialecte                       # "GN" (4.10) ou "GP" (2.3)
        self.phrases = set(phrases) if phrases else set(PHRASES)

    # ---- phrases élémentaires --------------------------------------------------------
    def _talker(self) -> str:
        return "GN" if self.dialecte == "GN" else "GP"

    def _sats(self, sol: Solution) -> list[Satellite]:
        if self.dialecte == "GN":
            return sol.satellites
        return [s for s in sol.satellites if s.systeme == "GPS"]

    def gga(self, sol: Solution) -> str:
        la, lah = _lat(sol.lat)
        lo, loh = _lon(sol.lon)
        nb = min(99, sum(1 for s in self._sats(sol) if s.utilise))
        fix = sol.fix if nb >= 4 or sol.fix == 0 else sol.fix
        return phrase(f"{self._talker()}GGA,{_heure(sol.temps)},{la},{lah},{lo},{loh},{fix},{nb:02d},"
                      f"{sol.hdop:.1f},{sol.alt_msl_m:.1f},M,{sol.ondulation_m:.1f},M,,")

    def rmc(self, sol: Solution) -> str:
        la, lah = _lat(sol.lat)
        lo, loh = _lon(sol.lon)
        statut = "A" if sol.fix > 0 else "V"
        mode = {0: "N", 1: "A", 2: "D"}.get(sol.fix, "A")
        decl = ("", "") if sol.declinaison is None else (f"{abs(sol.declinaison):.1f}", "E" if sol.declinaison >= 0 else "W")
        corps = (f"{self._talker()}RMC,{_heure(sol.temps)},{statut},{la},{lah},{lo},{loh},"
                 f"{sol.vitesse_ms / geo.KT_MS:.2f},{sol.cap:.1f},{_date(sol.temps)},{decl[0]},{decl[1]},{mode}")
        if self.dialecte == "GN":
            corps += ",V"          # NMEA 4.10 : statut de navigation (V = non validé)
        return phrase(corps)

    def vtg(self, sol: Solution) -> str:
        mode = {0: "N", 1: "A", 2: "D"}.get(sol.fix, "A")
        return phrase(f"{self._talker()}VTG,{sol.cap:.1f},T,,M,{sol.vitesse_ms / geo.KT_MS:.2f},N,"
                      f"{sol.vitesse_ms * 3.6:.2f},K,{mode}")

    def gll(self, sol: Solution) -> str:
        la, lah = _lat(sol.lat)
        lo, loh = _lon(sol.lon)
        statut = "A" if sol.fix > 0 else "V"
        mode = {0: "N", 1: "A", 2: "D"}.get(sol.fix, "A")
        return phrase(f"{self._talker()}GLL,{la},{lah},{lo},{loh},{_heure(sol.temps)},{statut},{mode}")

    def zda(self, sol: Solution) -> str:
        t = sol.temps
        return phrase(f"{self._talker()}ZDA,{_heure(t)},{t.day:02d},{t.month:02d},{t.year:04d},00,00")

    def gsa(self, sol: Solution) -> list[str]:
        """Une GSA par système en dialecte GN (avec identifiant de système), une seule en GP."""
        mode_fix = sol.mode_3d if sol.fix > 0 else 1
        out = []
        sats = self._sats(sol)
        if self.dialecte == "GN":
            systemes = [s for s in TALKERS if any(x.systeme == s for x in sats)]
            for sy in systemes:
                ids = [x.ident for x in sats if x.systeme == sy and x.utilise][:12]
                champs = ",".join(f"{i:02d}" for i in ids) + "," * (12 - len(ids))
                out.append(phrase(f"GNGSA,A,{mode_fix},{champs},{sol.pdop:.1f},{sol.hdop:.1f},{sol.vdop:.1f},{ID_SYSTEME[sy]}"))
        else:
            ids = [x.ident for x in sats if x.utilise][:12]
            champs = ",".join(f"{i:02d}" for i in ids) + "," * (12 - len(ids))
            out.append(phrase(f"GPGSA,A,{mode_fix},{champs},{sol.pdop:.1f},{sol.hdop:.1f},{sol.vdop:.1f}"))
        return out

    def gsv(self, sol: Solution) -> list[str]:
        """GSV par talker, 4 satellites par phrase, identifiant de signal en dialecte GN."""
        out = []
        for sy, talker in TALKERS.items():
            sats = [s for s in self._sats(sol) if s.systeme == sy]
            if not sats:
                continue
            sats.sort(key=lambda s: s.ident)
            total = max(1, math.ceil(len(sats) / 4))
            for n in range(total):
                bloc = sats[n * 4:(n + 1) * 4]
                corps = f"{talker}GSV,{total},{n + 1},{len(sats):02d}"
                for s in bloc:
                    corps += f",{s.ident:02d},{int(round(s.elevation)):02d},{int(round(s.azimut)) % 360:03d},{int(round(s.cn0)):02d}"
                if self.dialecte == "GN":
                    corps += f",{ID_SIGNAL[sy]}"
                out.append(phrase(corps))
        return out

    # ---- lot complet --------------------------------------------------------------------
    def trames(self, sol: Solution, avec_satellites: bool = True) -> list[str]:
        """Toutes les phrases activées, dans l'ordre habituel des récepteurs."""
        out: list[str] = []
        if "RMC" in self.phrases:
            out.append(self.rmc(sol))
        if "VTG" in self.phrases:
            out.append(self.vtg(sol))
        if "GGA" in self.phrases:
            out.append(self.gga(sol))
        if "GSA" in self.phrases and avec_satellites:
            out.extend(self.gsa(sol))
        if "GSV" in self.phrases and avec_satellites:
            out.extend(self.gsv(sol))
        if "GLL" in self.phrases:
            out.append(self.gll(sol))
        if "ZDA" in self.phrases:
            out.append(self.zda(sol))
        return out
