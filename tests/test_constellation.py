# -*- coding: utf-8 -*-
"""Tests des constellations : temps sidéral, géométrie nominale, visibilité, DOP, TLE."""
import math
from datetime import datetime, timezone, timedelta

from simuposition.core import constellation as cst


def test_gmst_reference():
    # Vallado, exemple 3-5 : 1992-08-20 12:14:00 UTC → GMST = 152.578 787 1°
    t = datetime(1992, 8, 20, 12, 14, 0, tzinfo=timezone.utc)
    assert abs(math.degrees(cst.gmst_rad(t)) - 152.5787871) < 1e-3


def test_geo_nominal_reste_sur_sa_longitude():
    sats = [s for s in cst.constellation_nominale() if s.systeme == "BeiDou" and s.ident <= 3]
    for s, lon_att in zip(sats, (80.0, 110.5, 140.0)):
        for jours in (0, 10, 100):
            t = cst.EPOQUE_NOMINALE + timedelta(days=jours, hours=5)
            x, y, z = cst.eci_vers_ecef(*s.position_eci(t), t)
            lon = math.degrees(math.atan2(y, x))
            assert abs(lon - lon_att) < 0.05, (s.ident, jours, lon)
            assert abs(z) < 1000.0


def test_periode_gps():
    s = [x for x in cst.constellation_nominale() if x.systeme == "GPS"][0]
    t0 = cst.EPOQUE_NOMINALE
    p0 = s.position_eci(t0)
    # demi-jour sidéral ≈ 43 082 s : le satellite revient (presque) au même point inertiel
    p1 = s.position_eci(t0 + timedelta(seconds=43082))
    assert math.dist(p0, p1) < 60_000.0            # < 60 km après une période (dérive J2 incluse)


def test_ciel_nominal_visibilite_et_dop():
    c = cst.Constellations()
    t = datetime(2026, 10, 13, 9, 0, 0, tzinfo=timezone.utc)
    ciel = c.ciel(49.0287, 1.2199, 150.0, t)
    assert 25 <= len(ciel.satellites) <= 45          # ~8–12 par constellation en Normandie
    par_sys = {sy: sum(1 for s in ciel.satellites if s.systeme == sy) for sy in cst.SYSTEMES}
    assert all(6 <= n <= 14 for sy, n in par_sys.items() if sy != "BeiDou"), par_sys
    assert all(c.masque_deg <= s.elevation <= 90.0 and 0 <= s.azimut < 360 for s in ciel.satellites)
    assert all(20.0 <= s.cn0 <= 52.0 for s in ciel.satellites)
    assert ciel.fix == 1 and ciel.mode_3d == 3
    assert 0.3 < ciel.hdop < 2.0 and 0.5 < ciel.vdop < 3.0 and ciel.pdop < 3.0   # 32 canaux multi-GNSS : HDOP ≈ 0,5
    assert ciel.nb_utilises <= c.max_canaux


def test_gps_seul_et_masque():
    c = cst.Constellations()
    for sy in cst.SYSTEMES:
        c.actifs[sy] = (sy == "GPS")
    t = datetime(2026, 10, 13, 9, 0, 0, tzinfo=timezone.utc)
    ciel = c.ciel(49.0, 1.0, 0.0, t)
    assert all(s.systeme == "GPS" for s in ciel.satellites)
    assert 6 <= len(ciel.satellites) <= 13
    c.masque_deg = 60.0
    ciel2 = c.ciel(49.0, 1.0, 0.0, t)
    assert len(ciel2.satellites) < len(ciel.satellites)
    assert ciel2.hdop > ciel.hdop               # géométrie dégradée quand on ne garde que le zénith


def test_pas_de_fix_sans_satellites():
    c = cst.Constellations()
    c.masque_deg = 89.9
    ciel = c.ciel(49.0, 1.0, 0.0, datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc))
    assert ciel.fix == 0 and ciel.nb_utilises < 3


def test_chargement_tle_et_sgp4():
    # TLE réel d'un satellite GPS (format CelesTrak 3 lignes) : vérifie l'analyse et l'altitude
    texte = """GPS BIIR-2  (PRN 13)
1 24876U 97035A   24001.50000000  .00000000  00000-0  00000-0 0  9990
2 24876  55.0000 100.0000 0050000  90.0000 270.0000  2.00561000195000
"""
    sats = cst.charger_tle(texte, "GPS")
    assert len(sats) == 1 and sats[0].ident == 13 and sats[0].nom.startswith("GPS BIIR-2")
    x, y, z = sats[0].position_eci(datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc))
    r = math.sqrt(x * x + y * y + z * z)
    assert 25_500_000 < r < 27_600_000             # orbite GPS ≈ 26 560 km (e = 0,005)
    assert cst._ident_depuis_nom("Galileo", "GSAT0101 (PRN E11)", 0) == 11
    assert cst._ident_depuis_nom("GLONASS", "COSMOS 2545 (757K)", 3) == 68
