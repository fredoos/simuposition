# -*- coding: utf-8 -*-
"""
Mesure d'écart entre l'avion simulé (position émise) et l'avion réel (GPS) — mode Direct.

Grandeurs :
  - écart horizontal (m) et relèvement réel → simulé (°, vrai) ;
  - écart vertical simulé − réel (m, positif = simulé au-dessus) ;
  - vitesses sol des deux avions (m/s) et leur différence ;
  - taux d'écart : variation de l'écart horizontal (m/s, positif = les avions s'éloignent),
    lissé exponentiellement (constante TAU_TAUX_S) pour gommer la gigue des époques GPS.

© skydev 2026
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from . import geodesie as geo

TAU_TAUX_S = 3.0                # lissage du taux d'écart (s)


@dataclass
class Ecart:
    horizontal_m: float
    relevement_deg: float       # du réel vers le simulé
    vertical_m: float           # simulé − réel
    v_sim_ms: float
    v_reel_ms: float
    taux_ms: Optional[float]    # d(écart horizontal)/dt lissé ; None tant qu'il n'est pas connu

    @property
    def dv_ms(self) -> float:
        return self.v_sim_ms - self.v_reel_ms


class MesureEcart:
    """Calcule l'écart à chaque appel ; mémorise l'écart précédent pour le taux."""

    def __init__(self):
        self.reinit()

    def reinit(self) -> None:
        self._prec: Optional[tuple] = None      # (t, écart horizontal)
        self._taux: Optional[float] = None

    def mesurer(self, reel: tuple, sim: tuple, t: float, mode: str = geo.ORTHODROMIE) -> Ecart:
        """reel / sim : (lat, lon, alt_m, vitesse_ms) ; t : instant (s, horloge monotone ou GPS)."""
        inv = geo.inverse(reel[0], reel[1], sim[0], sim[1], mode)
        d = inv.distance
        if self._prec is not None:
            dt = t - self._prec[0]
            if dt > 1e-3:
                brut = (d - self._prec[1]) / dt
                a = 1.0 - math.exp(-dt / TAU_TAUX_S)
                self._taux = brut if self._taux is None else self._taux + a * (brut - self._taux)
                self._prec = (t, d)
        else:
            self._prec = (t, d)
        return Ecart(d, inv.cap_depart if d > 0.5 else 0.0, sim[2] - reel[2], sim[3], reel[3], self._taux)


def texte_ecart(e: Ecart) -> str:
    """Texte compact (bandeau / carte) : « 2.31 NM · +500 ft · 254/250 kt · s'éloigne 12 kt »."""
    h = f"{e.horizontal_m / geo.NM_M:.2f} NM" if e.horizontal_m >= 0.1 * geo.NM_M else f"{e.horizontal_m:.0f} m"
    v = e.vertical_m / geo.FT_M
    morceaux = [h, f"{v:+.0f} ft", f"{e.v_sim_ms / geo.KT_MS:.0f}/{e.v_reel_ms / geo.KT_MS:.0f} kt"]
    if e.taux_ms is not None and abs(e.taux_ms) >= 0.5 * geo.KT_MS:
        morceaux.append(("s'éloigne " if e.taux_ms > 0 else "se rapproche ") + f"{abs(e.taux_ms) / geo.KT_MS:.0f} kt")
    return " · ".join(morceaux)
