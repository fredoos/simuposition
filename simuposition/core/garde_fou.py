# -*- coding: utf-8 -*-
"""
Garde-fou de sortie : dernier filtre avant l'émission NMEA, indépendant du reste du programme.

Pendant une émission CONTINUE, la position et l'altitude émises ne peuvent varier, d'une sortie à
la suivante, que dans des bornes physiques :

  - vitesse horizontale ≤ V_garde = max(1,3 · V_ref + 20 m/s, V_GARDE_MIN), où V_ref est la plus grande
    des vitesses légitimes du moment (consigne, V max de l'avion simulé, vitesse Doppler du GPS réel) ;
  - vitesse verticale   ≤ Vz_garde = max(1,5 · taux vertical max, VZ_GARDE_MIN) ;
  - valeurs non finies (NaN, ∞) : la sortie précédente est tenue.

Une sortie hors bornes n'est pas émise telle quelle : elle est ramenée sur le segment qui la relie à
la sortie précédente, à la distance (et à l'écart d'altitude) maximale permise. La sortie converge
ensuite vers la valeur calculée à la vitesse bornée ; chaque épisode est signalé une fois
(rappel « garde_fou ») avec l'ampleur du saut évité. Le garde-fou ne crée aucun saut : il ne fait que
retenir.

Repositionnement VOLONTAIRE (Réinitialiser, placement à l'arrêt, changement de mode, Démarrer…) :
l'appelant le déclare par `reinit()`. La sortie suivante est alors libre ; si elle est loin de la
dernière position émise (au-delà de ce que le mobile aurait pu parcourir entre-temps), le garde-fou
le signale (rappel « rupture ») : l'émetteur déclare alors le fix invalide quelques secondes (RMC « V »,
GGA qualité 0), comme un récepteur qui perd puis réacquiert le signal — jamais un saut en trame valide.

Le temps fourni à `filtrer` est une horloge monotone (secondes de simulation ou temps réel), pas
l'heure émise, qui peut reculer (Réinitialiser) ou sauter (horodatage GPS).

© skydev 2026
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from . import geodesie as geo

V_GARDE_MIN = 60.0              # m/s (~115 kt) : borne horizontale minimale
VZ_GARDE_MIN = 30.0             # m/s (~5900 ft/min) : borne verticale minimale (descente d'assaut)
DT_MIN = 0.05                   # s : pas de temps minimal pris en compte (horodatages identiques)


@dataclass
class Sortie:
    lat: float
    lon: float
    alt_m: float
    temps: datetime


class GardeFou:
    """Limiteur de pente de la position émise (horizontale et verticale)."""

    def __init__(self, rappel: Optional[Callable[[str, dict], None]] = None):
        self.rappel = rappel or (lambda e, i: None)
        self.nb_episodes = 0
        self._prec: Optional[Sortie] = None
        self._ancre: Optional[Sortie] = None     # dernière sortie avant un repositionnement volontaire
        self.reinit()

    def reinit(self) -> None:
        """Repositionnement volontaire : la prochaine sortie est libre (nouvelle acquisition)."""
        if self._prec is not None:
            self._ancre = self._prec
        self._prec = None
        self._episode = False
        self.retenue_m = 0.0                     # écart actuel entre la sortie et la valeur calculée

    @staticmethod
    def bornes(v_ref_ms: float, taux_ms: float) -> tuple[float, float]:
        """(V_garde, Vz_garde) en m/s."""
        return (max(1.3 * max(v_ref_ms, 0.0) + 20.0, V_GARDE_MIN),
                max(1.5 * max(taux_ms, 0.0), VZ_GARDE_MIN))

    def filtrer(self, lat: float, lon: float, alt_m: float, temps: datetime,
                v_ref_ms: float, taux_ms: float, mode: str = geo.ORTHODROMIE) -> tuple[float, float, float]:
        """Sortie bornée à émettre pour la valeur calculée (lat, lon, alt_m) à l'instant `temps`.

        ValueError si la valeur n'est pas finie et qu'aucune sortie n'est connue (rien à émettre)."""
        p = self._prec
        if not all(math.isfinite(x) for x in (lat, lon, alt_m)):
            q = p or self._ancre
            if q is None:
                raise ValueError("sortie non finie sans sortie précédente")
            self._signaler("valeur non finie", 0.0, 0.0)
            self._prec = Sortie(q.lat, q.lon, q.alt_m, temps)
            return q.lat, q.lon, q.alt_m
        v_garde, vz_garde = self.bornes(v_ref_ms, taux_ms)
        if p is None:                                         # nouvelle acquisition
            a = self._ancre
            if a is not None:
                dt = max(DT_MIN, (temps - a.temps).total_seconds())
                d = geo.inverse(a.lat, a.lon, lat, lon, mode).distance
                dz = alt_m - a.alt_m
                if d > v_garde * dt or abs(dz) > vz_garde * dt:
                    self.rappel("rupture", {"saut_m": d, "saut_alt_m": dz})
            self._ancre = None
            self._prec = Sortie(lat, lon, alt_m, temps)
            self.retenue_m = 0.0
            return lat, lon, alt_m
        dt = max(DT_MIN, (temps - p.temps).total_seconds())
        inv = geo.inverse(p.lat, p.lon, lat, lon, mode)
        d_max = v_garde * dt
        dz_max = vz_garde * dt
        dz = alt_m - p.alt_m
        h_hors, v_hors = inv.distance > d_max, abs(dz) > dz_max
        la, lo, al = lat, lon, alt_m
        if h_hors:
            la, lo, _ = geo.direct(p.lat, p.lon, inv.cap_depart, d_max, mode)
        if v_hors:
            al = p.alt_m + math.copysign(dz_max, dz)
        if h_hors or v_hors:
            self._signaler("saut retenu", inv.distance, dz)
        else:
            self._episode = False
        self.retenue_m = geo.inverse(la, lo, lat, lon, mode).distance if h_hors else 0.0
        self._prec = Sortie(la, lo, al, temps)
        return la, lo, al

    def _signaler(self, motif: str, d_m: float, dz_m: float) -> None:
        if not self._episode:                    # un signalement par épisode
            self._episode = True
            self.nb_episodes += 1
            self.rappel("garde_fou", {"motif": motif, "saut_m": d_m, "saut_alt_m": dz_m})
