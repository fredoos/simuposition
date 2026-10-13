# -*- coding: utf-8 -*-
"""
Moteur d'offset (mode LIVE) : décale la trace émise par rapport à la trace GPS réelle.

Deux pilotages :

  - AUTO (waypoints) : l'offset part de 0, monte jusqu'à la magnitude après le passage du
    waypoint de départ (rampe sur D_sep = v·T_sep), tient en croisière, puis redescend à 0 pour
    tomber *exactement* sur le waypoint d'arrivée. Le déclenchement de la retrouvaille est calculé
    sur la vitesse (D0 = v·T_retrouvaille) mais la forme est pilotée par la distance restante au
    waypoint : l'offset s'annule pile au waypoint même si la vitesse varie en cours de manœuvre.

  - MANUEL (bouton) : l'offset est commandé en temps réel (Appliquer / Annuler / +−), avec une
    rampe temporelle sur T_manuel. Pas de waypoint.

offset nul ≡ bypass (trace émise = trace réelle). Toutes les transitions sont en smoothstep
(dérivée nulle aux extrémités → pas de discontinuité de vitesse). Un garde-fou signale un angle
d'interception équivalent trop grand (waypoints trop rapprochés ou offset trop grand pour le
temps imparti), au lieu de produire une sortie violente.

Repères : magnitude horizontale et décalage vertical en mètres ; azimuts en degrés ; la direction
de l'offset est recalculée à chaque époque sur le cap sol courant (sauf relèvement fixe), ce qui
garde un offset latéral perpendiculaire à la route quand l'avion tourne.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from . import geodesie as geo
from .modele import (ConfigOffset, OFFSET_LATERAL, OFFSET_LONGITUDINAL, OFFSET_RELEVEMENT,
                     PILOTAGE_AUTO)

EPS_M = 0.5                     # en deçà, l'offset est considéré nul (≡ bypass)


def smoothstep(x: float) -> float:
    """Transition douce S(x) = 3x² − 2x³ sur [0, 1] (dérivée nulle en 0 et 1)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)


def _bearing(type_offset: str, signe: int, relevement_deg: float, cog_deg: float) -> float:
    """Azimut (°) du décalage selon le type, le signe et le cap sol."""
    if type_offset == OFFSET_LATERAL:
        return (cog_deg + (90.0 if signe >= 0 else -90.0)) % 360.0
    if type_offset == OFFSET_LONGITUDINAL:
        return cog_deg % 360.0 if signe >= 0 else (cog_deg + 180.0) % 360.0
    return relevement_deg % 360.0


def angle_interception_deg(magnitude_m: float, duree_s: float, vitesse_ms: float) -> float:
    """Angle d'interception équivalent au pic d'une rampe smoothstep.

    Vitesse latérale de pointe = 1,5·M/T ; angle = atan(v_lat / v_sol)."""
    if vitesse_ms <= 0.1 or duree_s <= 0.0:
        return 90.0
    return math.degrees(math.atan(1.5 * magnitude_m / (duree_s * vitesse_ms)))


@dataclass
class SortieOffset:
    lat: float
    lon: float
    alt_m: float
    magnitude_m: float          # magnitude horizontale réellement appliquée
    bearing_deg: float
    phase: str


@dataclass
class _Transition:
    """Rampe smoothstep d'une valeur scalaire sur une durée (pilotage manuel)."""
    v0: float
    v1: float
    t0: float
    duree: float
    dir_sig: Optional[tuple] = None    # direction (type, signe, relevement) à activer au départ

    def valeur(self, now: float) -> float:
        if self.duree <= 0.0:
            return self.v1
        return self.v0 + (self.v1 - self.v0) * smoothstep((now - self.t0) / self.duree)

    def finie(self, now: float) -> bool:
        return now - self.t0 >= self.duree


class MoteurOffset:
    """Calcule la position décalée époque par époque, selon le pilotage choisi."""

    def __init__(self, config: ConfigOffset, mode: str = geo.ORTHODROMIE,
                 rappel: Optional[Callable[[str, dict], None]] = None):
        self.config = config
        self.mode = mode
        self.rappel = rappel or (lambda e, i: None)
        self.reinit()

    def reinit(self) -> None:
        # pilotage AUTO
        self.phase = "avant"            # avant | separation | croisiere | retrouvaille | apres
        self._D_sep = 0.0
        self._D0 = 0.0
        self._depart_pos: Optional[tuple] = None
        self._k = 0.0                   # fraction d'offset appliquée (0..1)
        self._k_entree = 1.0            # fraction au début de la retrouvaille
        self._d_arr_prec = float("inf")
        # pilotage MANUEL
        self._now = 0.0                 # horloge cumulée (s) des rampes manuelles
        self._mag = 0.0                 # magnitude horizontale appliquée (m)
        self._vert = 0.0                # décalage vertical appliqué (m)
        self._dir: Optional[tuple] = None
        self._tr_mag: Optional[_Transition] = None
        self._tr_vert: Optional[_Transition] = None
        self._file: list[_Transition] = []
        self._v = 0.0                   # dernière vitesse sol connue (pour le garde-fou)

    # ---- commandes du pilotage manuel ----------------------------------------------
    def _dir_sig(self) -> tuple:
        c = self.config
        return (c.type_offset, c.signe, round(c.relevement_deg, 3))

    def _cible_mag(self) -> float:
        if self._file:
            return self._file[-1].v1
        if self._tr_mag is not None:
            return self._tr_mag.v1
        return self._mag

    def appliquer_manuel(self) -> None:
        """Rampe vers l'offset courant de la configuration (sur T_manuel)."""
        c = self.config
        cible = max(0.0, c.magnitude_m)
        dsig = self._dir_sig()
        self._file.clear()
        if self._mag > EPS_M and self._dir is not None and dsig != self._dir:
            # changement de direction : on rentre à 0 (ancienne direction) puis on ressort (nouvelle)
            self._tr_mag = _Transition(self._mag, 0.0, self._now, c.t_manuel_s * 0.5)
            self._file.append(_Transition(0.0, cible, 0.0, c.t_manuel_s * 0.5, dsig))
        else:
            self._dir = dsig
            self._tr_mag = _Transition(self._mag, cible, self._now, c.t_manuel_s, dsig)
        self._tr_vert = _Transition(self._vert, c.vertical_m, self._now, c.t_manuel_s)
        self._verifier_garde(abs(cible - self._mag), c.t_manuel_s, "offset manuel")

    def annuler_manuel(self) -> None:
        """Rampe l'offset vers 0 (sur T_manuel)."""
        c = self.config
        self._file.clear()
        self._tr_mag = _Transition(self._mag, 0.0, self._now, c.t_manuel_s, self._dir)
        self._tr_vert = _Transition(self._vert, 0.0, self._now, c.t_manuel_s)

    def nudge(self, d_m: float) -> None:
        """Ajuste la magnitude cible de ±d_m (sur T_manuel), même direction."""
        c = self.config
        if self._dir is None:
            self._dir = self._dir_sig()
        cible = max(0.0, self._cible_mag() + d_m)
        self._file.clear()
        self._tr_mag = _Transition(self._mag, cible, self._now, c.t_manuel_s, self._dir)
        self._verifier_garde(abs(cible - self._mag), c.t_manuel_s, "offset manuel")

    # ---- calcul d'une époque -------------------------------------------------------
    def calculer(self, fix, dt_s: float) -> SortieOffset:
        """Position décalée pour le `fix` reçu ; `dt_s` = temps réel écoulé depuis l'époque précédente."""
        c = self.config
        v = fix.sog_ms if (fix.sog_ms is not None and fix.sog_ms > 0.1) else self._v
        if v > 0.1:
            self._v = v
        cog = fix.cog_deg if fix.cog_deg is not None else 0.0
        self._now += max(0.0, dt_s)

        if c.pilotage == PILOTAGE_AUTO:
            k = self._maj_auto(fix, self._v)
            mag = k * max(0.0, c.magnitude_m)
            vert = k * c.vertical_m
            bearing = _bearing(c.type_offset, c.signe, c.relevement_deg, cog)
            phase = self.phase
        else:
            self._maj_manuel()
            mag = self._mag
            vert = self._vert
            typ, signe, rel = self._dir or self._dir_sig()
            bearing = _bearing(typ, signe, rel, cog)
            phase = "manuel"

        if mag <= EPS_M:
            lat, lon = fix.lat, fix.lon
        else:
            lat, lon, _ = geo.direct(fix.lat, fix.lon, bearing, mag, self.mode)
        alt = (fix.alt_m if fix.alt_m is not None else 0.0) + vert
        return SortieOffset(lat, lon, alt, mag, bearing, phase)

    # ---- pilotage AUTO (piloté par la distance aux waypoints) ----------------------
    def _maj_auto(self, fix, v: float) -> float:
        c = self.config
        if not c.depart or not c.arrivee:
            self.phase = "avant"
            self._k = 0.0
            return 0.0
        d_dep = geo.inverse(fix.lat, fix.lon, c.depart[0], c.depart[1], self.mode).distance
        d_arr = geo.inverse(fix.lat, fix.lon, c.arrivee[0], c.arrivee[1], self.mode).distance

        if self.phase == "avant":
            if d_dep <= c.rayon_capture_m:
                self._D_sep = max(v * c.t_separation_s, 1.0)
                self._depart_pos = (fix.lat, fix.lon)
                self.phase = "separation"
                self._verifier_garde(c.magnitude_m, c.t_separation_s, "séparation")
            else:
                self._k = 0.0
                return 0.0

        if self.phase == "separation":
            # bascule anticipée si l'arrivée est déjà proche (waypoints rapprochés)
            if d_arr <= max(v * c.t_retrouvaille_s, 1.0):
                self._D0 = max(d_arr, 1.0)
                self._k_entree = self._k
                self._d_arr_prec = d_arr
                self.phase = "retrouvaille"
                self._verifier_garde(self._k * c.magnitude_m, c.t_retrouvaille_s, "retrouvaille")
            else:
                d_done = geo.inverse(self._depart_pos[0], self._depart_pos[1],
                                     fix.lat, fix.lon, self.mode).distance
                if d_done >= self._D_sep:
                    self.phase = "croisiere"
                    self._k = 1.0
                    return 1.0
                self._k = smoothstep(d_done / self._D_sep)
                return self._k

        if self.phase == "croisiere":
            if d_arr <= max(v * c.t_retrouvaille_s, 1.0):
                self._D0 = max(v * c.t_retrouvaille_s, 1.0)
                self._k_entree = 1.0
                self._d_arr_prec = d_arr
                self.phase = "retrouvaille"
                self._verifier_garde(c.magnitude_m, c.t_retrouvaille_s, "retrouvaille")
            else:
                self._k = 1.0
                return 1.0

        if self.phase == "retrouvaille":
            # arrivé au waypoint, ou on s'en éloigne (passage à côté) → offset annulé
            if d_arr <= 1.0 or d_arr > self._d_arr_prec + 0.5:
                self.phase = "apres"
                self._k = 0.0
                return 0.0
            self._d_arr_prec = d_arr
            self._k = self._k_entree * smoothstep(min(1.0, d_arr / self._D0))
            return self._k

        self._k = 0.0           # phase « apres »
        return 0.0

    # ---- pilotage MANUEL (rampes temporelles) --------------------------------------
    def _maj_manuel(self) -> None:
        if self._tr_mag is not None:
            self._mag = self._tr_mag.valeur(self._now)
            if self._tr_mag.finie(self._now):
                self._mag = self._tr_mag.v1
                self._tr_mag = None
                if self._file:
                    suiv = self._file.pop(0)
                    suiv.t0 = self._now
                    if suiv.dir_sig is not None:
                        self._dir = suiv.dir_sig
                    self._tr_mag = suiv
        if self._tr_vert is not None:
            self._vert = self._tr_vert.valeur(self._now)
            if self._tr_vert.finie(self._now):
                self._vert = self._tr_vert.v1
                self._tr_vert = None

    # ---- garde-fou -----------------------------------------------------------------
    def _verifier_garde(self, magnitude_m: float, duree_s: float, phase: str) -> None:
        ang = angle_interception_deg(magnitude_m, duree_s, self._v)
        if ang > self.config.angle_garde_deg:
            self.rappel("interception_serree", {"phase": phase, "angle_deg": ang,
                                                 "magnitude_m": magnitude_m, "duree_s": duree_s})
