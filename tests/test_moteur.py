# -*- coding: utf-8 -*-
"""Tests du modèle et du moteur de trajectoire."""
import json
import math
import os
import tempfile

from simuposition.core import geodesie as geo
from simuposition.core.modele import (Profil, PointPassage, analyser_coordonnee, analyser_paire,
                                      formater_dms, formater_dmm, exporter_gpx, importer_gpx,
                                      exporter_kml, importer_kml)
from simuposition.core.moteur import Moteur, EN_ROUTE, TERMINE, ARRET, PAUSE, rayon_virage, ecart_cap, trace_lissee, G_MS2


def profil_test(**kw):
    kw.setdefault("inclinaison_max_deg", 0.0)     # tests historiques : virage instantané
    p = Profil(nom="test", points=[
        PointPassage(49.0, 1.0, 100.0, "A"),
        PointPassage(49.0, 1.1, 600.0, "B"),      # ≈ 7,3 km plein est, montée 500 m
        PointPassage(49.05, 1.1, None, "C"),      # ≈ 5,6 km plein nord
    ], vitesse_ms=100.0, taux_ms=5.0, **kw)
    return p


def simuler(m: Moteur, duree_s: float, dt: float = 0.1):
    evts = []
    m.rappel = lambda e, i: evts.append(e)
    n = int(duree_s / dt)
    for _ in range(n):
        m.avancer(dt)
    return evts


def test_parcours_complet():
    p = profil_test()
    m = Moteur(p)
    assert m.etat.etat == ARRET and abs(m.etat.cap - 90.0) < 0.5
    assert m.demarrer()
    evts = simuler(m, 200.0)          # 12,9 km à 100 m/s ≈ 129 s
    e = m.etat
    assert e.etat == TERMINE
    assert "point_atteint" in evts and "termine" in evts
    assert abs(e.lat - 49.05) < 1e-9 and abs(e.lon - 1.1) < 1e-9
    assert abs(e.alt_m - 600.0) < 1e-6            # montée 500 m à 5 m/s = 100 s, atteinte avant la fin
    assert abs(e.distance_m - p.longueur_m()) < 1.0
    assert e.vitesse_ms == 0.0


def test_taux_vertical_limite():
    p = profil_test()
    m = Moteur(p)
    m.demarrer()
    simuler(m, 10.0)
    # après 10 s à 5 m/s : +50 m
    assert abs(m.etat.alt_m - 150.0) < 1e-6 and m.etat.vz_ms == 5.0


def test_pause_et_arret():
    m = Moteur(profil_test())
    m.demarrer()
    simuler(m, 5.0)
    d1 = m.etat.distance_m
    m.basculer_pause()
    assert m.etat.etat == PAUSE
    simuler(m, 5.0)
    assert m.etat.distance_m == d1
    m.basculer_pause()
    simuler(m, 5.0)
    assert m.etat.distance_m > d1
    m.arreter()
    assert m.etat.etat == ARRET and m.etat.vitesse_ms == 0.0


def test_facteur_temps_et_horloge():
    m = Moteur(profil_test())
    m.facteur_temps = 10.0
    t0 = m.etat.temps
    m.demarrer()
    simuler(m, 2.0)                       # 20 s simulées → 2 000 m
    assert abs(m.etat.distance_m - 2000.0) < 1.0
    assert abs((m.etat.temps - t0).total_seconds() - 20.0) < 1e-6


def test_boucle_et_saut():
    p = profil_test(boucle=True)
    m = Moteur(p)
    m.demarrer()
    evts = simuler(m, 400.0)
    assert m.etat.etat == EN_ROUTE and evts.count("point_atteint") >= 4
    # en route : DIRECT vers le point (aucun saut de position), la cible devient ce point
    la, lo = m.etat.lat, m.etat.lon
    m.sauter(1)
    assert (m.etat.lat, m.etat.lon) == (la, lo) and m.etat.idx_cible == 1
    # à l'arrêt : placement immédiat sur le point, cible = point suivant
    m.arreter()
    m.sauter(1)
    assert abs(m.etat.lat - 49.0) < 1e-9 and m.etat.idx_cible == 2


def test_consignes_par_point():
    p = profil_test()
    p.points[1].vitesse_ms = 50.0        # à partir du passage en B, 50 m/s
    m = Moteur(p)
    m.demarrer()
    simuler(m, 80.0)                     # B atteint à ~73 s
    assert m.etat.vitesse_ms == 50.0


def test_json_aller_retour_et_compat():
    p = profil_test(interpolation=geo.LOXODROMIE)
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "p.json")
        p.enregistrer(f)
        q = Profil.charger(f)
        assert q.nom == "test" and len(q.points) == 3 and q.interpolation == geo.LOXODROMIE
        assert q.points[1].alt_m == 600.0 and q.vitesse_ms == 100.0
        # format « exemple » initial (vitesse/taux en dict, alt_ft)
        with open(f, "w", encoding="utf-8") as fh:
            json.dump({"nom": "ex", "vitesse": {"valeur": 90, "unite": "kt"},
                       "taux_vertical": {"valeur": 500, "unite": "ft/min"},
                       "points": [{"lat": 49, "lon": 1, "alt_ft": 1000}, {"lat": 49.1, "lon": 1}]}, fh)
        r = Profil.charger(f)
        assert abs(r.vitesse_ms - 90 * geo.KT_MS) < 1e-9
        assert abs(r.taux_ms - 500 * geo.FT_M / 60) < 1e-9
        assert abs(r.points[0].alt_m - 304.8) < 1e-9 and r.points[1].alt_m is None


def test_gpx_kml_aller_retour():
    pts = profil_test().points
    with tempfile.TemporaryDirectory() as d:
        g = os.path.join(d, "r.gpx")
        exporter_gpx(pts, g)
        back = importer_gpx(g)
        assert [(round(x.lat, 6), round(x.lon, 6), x.nom) for x in back] == [(49.0, 1.0, "A"), (49.0, 1.1, "B"), (49.05, 1.1, "C")]
        assert back[1].alt_m == 600.0 and back[2].alt_m is None
        k = os.path.join(d, "r.kml")
        exporter_kml(pts, k)
        back = importer_kml(k)
        assert len(back) >= 3 and back[0].nom == "A" and abs(back[1].alt_m - 600.0) < 1e-6


def test_analyse_coordonnees():
    assert abs(analyser_coordonnee("49.0287", True) - 49.0287) < 1e-9
    assert abs(analyser_coordonnee("49°01'43.3\"N", True) - 49.02869) < 1e-4
    assert abs(analyser_coordonnee("1°13.194'E", False) - 1.2199) < 1e-4
    assert analyser_coordonnee("1 13.194 O", False) < 0           # O = Ouest
    assert abs(analyser_coordonnee("-74.0060", False) + 74.006) < 1e-9
    la, lo = analyser_paire("49.0287, 1.2199")
    assert abs(la - 49.0287) < 1e-9 and abs(lo - 1.2199) < 1e-9
    la, lo = analyser_paire("49°01'43\"N 1°13'12\"E")
    assert abs(la - 49.02861) < 1e-4 and abs(lo - 1.22) < 1e-4
    assert formater_dms(49.0287, True).startswith("49°01'43")
    assert formater_dmm(-1.2199, False) == "1°13.194'W"


# --- virages arrondis -----------------------------------------------------------------
def _suivre_caps(m: Moteur, duree_s: float, dt: float = 0.1):
    caps, pos = [m.etat.cap], [(m.etat.lat, m.etat.lon)]
    for _ in range(int(duree_s / dt)):
        m.avancer(dt)
        caps.append(m.etat.cap)
        pos.append((m.etat.lat, m.etat.lon))
        if m.etat.etat != EN_ROUTE:
            break
    return caps, pos


def test_rayon_virage():
    r = rayon_virage(100.0, 25.0)                     # 100 m/s, 25° → ≈ 2,19 km
    assert abs(r - 100.0 ** 2 / (G_MS2 * math.tan(math.radians(25)))) < 1e-6
    assert rayon_virage(100.0, 0.0) == 0.0 and rayon_virage(0.0, 25.0) == 0.0


def test_virage_cap_continu_et_taux_limite():
    p = profil_test(inclinaison_max_deg=25.0)         # virage de 90° en B (est → nord)
    m = Moteur(p)
    m.demarrer()
    caps, _ = _suivre_caps(m, 200.0)
    omega = math.degrees(100.0 / rayon_virage(100.0, 25.0))        # ≈ 2,6 °/s
    pas_max = max(abs(ecart_cap(b, a)) for a, b in zip(caps, caps[1:]))
    assert pas_max <= omega * 0.1 + 1e-6               # jamais plus que ω·dt par pas
    assert m.etat.etat == TERMINE
    assert abs(caps[0] - 90.0) < 0.5 and abs(ecart_cap(caps[-2], 0.0)) < 1.0


def test_virage_coupe_le_coin_et_reste_proche():
    p = profil_test(inclinaison_max_deg=25.0)
    m = Moteur(p)
    m.demarrer()
    _, pos = _suivre_caps(m, 200.0)
    B = (49.0, 1.1)
    dmin = min(geo.inverse(la, lo, B[0], B[1]).distance for la, lo in pos)
    R = rayon_virage(100.0, 25.0)
    # fly-by 90° : écart au sommet = R(1/cos45° − 1)·… ≈ 0,41 R, jamais plus d'un rayon
    assert 0.1 * R < dmin < 1.0 * R
    assert m.etat.distance_m < p.longueur_m()          # trajet raccourci par l'arrondi
    assert abs(m.etat.lat - 49.05) < 1e-9              # mais arrivée exacte au dernier point


def test_virage_inclinaison_nulle_identique_ancien():
    a = Moteur(profil_test(inclinaison_max_deg=0.0))
    a.demarrer()
    caps, _ = _suivre_caps(a, 200.0)
    assert any(abs(ecart_cap(y, x)) > 80 for x, y in zip(caps, caps[1:]))      # saut de 90°


def test_virage_demi_tour_boucle_sans_orbite():
    p = Profil(points=[PointPassage(49.0, 1.0, 100.0), PointPassage(49.0, 1.2, 100.0),
                       PointPassage(49.0, 1.0, 100.0)],
               vitesse_ms=100.0, inclinaison_max_deg=25.0)
    m = Moteur(p)
    m.demarrer()
    caps, _ = _suivre_caps(m, 600.0)
    assert m.etat.etat == TERMINE                       # pas de spirale autour du point


def test_trace_lissee():
    p = profil_test(inclinaison_max_deg=25.0)
    t = trace_lissee(p, 500.0)
    assert abs(t[0][0] - 49.0) < 1e-9 and abs(t[0][1] - 1.0) < 1e-9
    assert abs(t[-1][0] - 49.05) < 1e-9 and abs(t[-1][1] - 1.1) < 1e-9
    dmin = min(geo.inverse(la, lo, 49.0, 1.1).distance for la, lo in t)
    assert dmin > 500.0                                 # le sommet B est coupé
    assert len(trace_lissee(profil_test(), 500.0)) == len(profil_test().trace(500.0))   # 0° = tracé droit


def test_json_inclinaison():
    p = profil_test(inclinaison_max_deg=18.0)
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "p.json")
        p.enregistrer(f)
        assert Profil.charger(f).inclinaison_max_deg == 18.0


def test_trace_lissee_segments_courts_pas_de_spaghetti():
    # 250 kt / 25° → R ≈ 3,6 km ; segments de 2 à 5 km : la trace doit rester bornée et finie
    p = Profil(points=[PointPassage(49.0287, 1.2199), PointPassage(49.045, 1.25), PointPassage(49.02, 1.27),
                       PointPassage(49.0287, 1.2199), PointPassage(49.0193, 1.2332)],
               vitesse_ms=250 * geo.KT_MS, inclinaison_max_deg=25.0, interpolation=geo.LOXODROMIE, boucle=True)
    t = trace_lissee(p, 500.0)
    assert 20 < len(t) < 9000
    assert all(math.isfinite(a) and math.isfinite(b) for a, b in t)
    long_trace = sum(geo.inverse(a[0], a[1], b[0], b[1]).distance for a, b in zip(t, t[1:]))
    assert long_trace < 10 * p.longueur_m()


ROUTE_EVREUX = [(49.0287, 1.2199), (49.045, 1.25), (49.02, 1.27), (49.0287, 1.2199), (49.0193, 1.2332), (49.005, 1.19), (49.04, 1.17)]


def _profil_evreux(kt, incl, **kw):
    return Profil(points=[PointPassage(la, lo, None, f"WP{i + 1}") for i, (la, lo) in enumerate(ROUTE_EVREUX)],
                  vitesse_ms=kt * geo.KT_MS, inclinaison_max_deg=incl, **kw)


def test_route_courte_aucune_aberration():
    """Tronçons courts devant le rayon de virage : chemin fini, sans boucle, cap continu, fin atteinte."""
    for kt, incl in ((250, 10), (250, 25), (90, 10), (90, 30), (250, 1)):
        p = _profil_evreux(kt, incl)
        evts = []
        m = Moteur(p, lambda e, i: evts.append(e))
        m.demarrer()
        caps = [m.etat.cap]
        for _ in range(60000):
            m.avancer(0.1)
            caps.append(m.etat.cap)
            if m.etat.etat == TERMINE:
                break
        assert m.etat.etat == TERMINE, (kt, incl)
        assert m.etat.distance_m < p.longueur_m() * 1.02          # jamais plus long que la route brute
        assert evts.count("point_atteint") == 6
        # cap continu : pas de saut instantané (> 8° en 0,1 s ≈ 80°/s)
        assert max(abs(ecart_cap(b, a)) for a, b in zip(caps, caps[1:])) < 8.0, (kt, incl)


def test_virage_serre_signale():
    p = _profil_evreux(250, 10)
    evts = []
    m = Moteur(p, lambda e, i: evts.append((e, i)))
    m.demarrer()
    for _ in range(20000):
        m.avancer(0.1)
        if m.etat.etat == TERMINE:
            break
    serres = [i for e, i in evts if e == "virage_serre"]
    assert serres and all(i["incl_deg"] > 10.0 for i in serres)


def test_chemin_longs_troncons_rayon_nominal():
    """Tronçons longs : aucun virage serré, rayon = v²/(g·tan φ)."""
    p = profil_test(inclinaison_max_deg=25.0)
    evts = []
    m = Moteur(p, lambda e, i: evts.append(e))
    m.demarrer()
    for _ in range(3000):
        m.avancer(0.1)
    assert "virage_serre" not in evts and m.etat.etat == TERMINE


def test_boucle_avec_virages_plusieurs_tours():
    p = _profil_evreux(90, 25, boucle=True)
    evts = []
    m = Moteur(p, lambda e, i: evts.append(e))
    m.demarrer()
    caps = [m.etat.cap]
    for _ in range(80000):                      # ~8 000 s à ×1 : plusieurs tours (reconstruction du chemin)
        m.avancer(0.1)
        caps.append(m.etat.cap)
    assert m.etat.etat == EN_ROUTE and evts.count("point_atteint") > 14
    assert max(abs(ecart_cap(b, a)) for a, b in zip(caps, caps[1:])) < 8.0


def test_edition_en_vol_reconstruit_sans_saut_de_cap():
    p = _profil_evreux(90, 25)
    m = Moteur(p)
    m.demarrer()
    caps = []
    for k in range(3000):
        m.avancer(0.1)
        caps.append(m.etat.cap)
        if k == 1000:
            p.points[3].lat += 0.004          # le point 4 est déplacé en cours de vol
    assert max(abs(ecart_cap(b, a)) for a, b in zip(caps, caps[1:])) < 8.0
