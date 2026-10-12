# -*- coding: utf-8 -*-
"""Tests de la géodésie : valeurs de référence connues et cohérence direct/inverse."""
import math

from simuposition.core import geodesie as g


def test_vincenty_paris_new_york():
    # Paris (48.8566, 2.3522) → New York (40.7128, -74.0060) : ≈ 5 853 km (géodésique WGS84 ;
    # la valeur sphérique haversine est ≈ 5 837 km, ~16 km de moins)
    inv = g.vincenty_inverse(48.8566, 2.3522, 40.7128, -74.0060)
    assert abs(inv.distance - 5_853_000) < 2_000
    assert abs(g.spherique_inverse(48.8566, 2.3522, 40.7128, -74.0060).distance - 5_837_000) < 2_000
    assert 290 < inv.cap_depart < 295          # azimut initial ≈ 291,8°


def test_direct_inverse_coherence():
    for lat, lon, cap, d in [(49.0287, 1.2199, 37.0, 12_345.0), (-33.9, 151.2, 250.0, 800_000.0),
                              (0.0, 0.0, 90.0, 1_000_000.0), (60.0, -170.0, 300.0, 2_500_000.0)]:
        la2, lo2, _ = g.vincenty_direct(lat, lon, cap, d)
        inv = g.vincenty_inverse(lat, lon, la2, lo2)
        assert abs(inv.distance - d) < 0.01
        assert abs((inv.cap_depart - cap + 180) % 360 - 180) < 1e-6


def test_loxodromie_cap_constant():
    inv = g.loxodromie_inverse(50.0, 0.0, 51.0, 1.0)
    la2, lo2, cap_fin = g.loxodromie_direct(50.0, 0.0, inv.cap_depart, inv.distance)
    assert abs(la2 - 51.0) < 1e-6 and abs(lo2 - 1.0) < 1e-6
    assert abs(cap_fin - inv.cap_depart) < 1e-9


def test_loxodromie_plus_longue_que_orthodromie():
    o = g.inverse(40.0, -74.0, 48.0, 2.0, g.ORTHODROMIE).distance
    l = g.inverse(40.0, -74.0, 48.0, 2.0, g.LOXODROMIE).distance
    assert l > o and (l - o) / o < 0.05


def test_point_intermediaire_et_echantillonnage():
    la, lo, _ = g.point_intermediaire(0.0, 0.0, 0.0, 10.0, 0.5)
    assert abs(la) < 1e-6 and abs(lo - 5.0) < 1e-6
    pts = g.echantillonner(49.0, 1.0, 49.5, 1.5, pas_m=5000)
    assert pts[0] == (49.0, 1.0) and pts[-1] == (49.5, 1.5) and len(pts) > 5


def test_ecef_enu():
    x, y, z = g.geodesique_vers_ecef(0.0, 0.0, 0.0)
    assert abs(x - g.A) < 1e-6 and abs(y) < 1e-6 and abs(z) < 1e-6
    # un point 1000 m au-dessus de (45, 10) vu depuis (45, 10) est « en haut »
    x0, y0, z0 = g.geodesique_vers_ecef(45.0, 10.0, 0.0)
    x1, y1, z1 = g.geodesique_vers_ecef(45.0, 10.0, 1000.0)
    e, n, u = g.ecef_vers_enu(x1 - x0, y1 - y0, z1 - z0, 45.0, 10.0)
    assert abs(e) < 1e-6 and abs(n) < 1e-6 and abs(u - 1000.0) < 1e-6


def test_unites():
    assert abs(100 * g.KT_MS - 51.444) < 0.01
    assert abs(g.NM_M / g.FT_M - 6076.1) < 0.1
