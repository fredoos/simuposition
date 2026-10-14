# -*- coding: utf-8 -*-
"""Tests de la mesure d'écart avion simulé / avion réel (mode Direct)."""
import math

from simuposition.core import geodesie as geo
from simuposition.core.mesure import MesureEcart, texte_ecart

LAT0, LON0 = 49.0, 1.0
DEG_PAR_M_LON = 1.0 / (111320.0 * math.cos(math.radians(LAT0)))


def test_ecarts_horizontal_vertical_vitesses():
    m = MesureEcart()
    reel = (LAT0, LON0, 1000.0, 100.0)
    sim = (LAT0, LON0 + 1852.0 * DEG_PAR_M_LON, 1000.0 + 500 * geo.FT_M, 110.0)   # 1 NM à l'est, 500 ft au-dessus
    e = m.mesurer(reel, sim, 0.0)
    assert abs(e.horizontal_m - 1852.0) < 10.0 and abs(e.relevement_deg - 90.0) < 0.5
    assert abs(e.vertical_m / geo.FT_M - 500.0) < 1e-6 and abs(e.dv_ms - 10.0) < 1e-9
    assert e.taux_ms is None
    t = texte_ecart(e)
    assert "+500 ft" in t and "NM" in t


def test_taux_d_ecart_signe_et_lisse():
    m = MesureEcart()
    for i in range(30):                                   # l'écart grandit de 10 m/s
        reel = (LAT0, LON0, 0.0, 50.0)
        sim = (LAT0, LON0 + (500.0 + 10.0 * i) * DEG_PAR_M_LON, 0.0, 50.0)
        e = m.mesurer(reel, sim, float(i))
    assert abs(e.taux_ms - 10.0) < 0.3 and "s'éloigne" in texte_ecart(e)
    for i in range(30, 80):                               # puis diminue de 5 m/s
        sim = (LAT0, LON0 + (790.0 - 5.0 * (i - 29)) * DEG_PAR_M_LON, 0.0, 50.0)
        e = m.mesurer((LAT0, LON0, 0.0, 50.0), sim, float(i))
    assert abs(e.taux_ms + 5.0) < 0.3 and "se rapproche" in texte_ecart(e)
