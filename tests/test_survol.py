# -*- coding: utf-8 -*-
"""Tests du survol (« fly-over », inclinaison négative) : passage exact sur les points, cap continu,
pas de boucle, repli propre quand la branche est trop courte."""
import math

import pytest

from simuposition.core import geodesie as geo
from simuposition.core.chemin import construire, ecart_cap
from simuposition.core.modele import Profil, PointPassage
from simuposition.core.moteur import Moteur, trace_lissee

KT = geo.KT_MS
EVREUX = [(49.044357, 1.238178), (49.046177, 1.200107), (49.032673, 1.194149),
          (49.016714, 1.271603), (49.007321, 1.256029)]


def profil(points, incl, vit_kt=90.0, boucle=False):
    p = Profil()
    p.points = [PointPassage(la, lo, None, f"WP{i + 1}") for i, (la, lo) in enumerate(points)]
    p.vitesse_ms = vit_kt * KT
    p.inclinaison_max_deg = incl
    p.boucle = boucle
    return p


def distance_min(ch, lat, lon):
    return min(geo.inverse(lat, lon, a, b).distance for a, b in zip(ch.lat, ch.lon))


@pytest.mark.parametrize("vit_kt", [60.0, 90.0])
def test_survol_passe_sur_chaque_point(vit_kt):
    p = profil(EVREUX, -20.0, vit_kt)
    ch = construire(p, EVREUX[0], 1, p.vitesse_ms, tours=1, pas_m=200.0)
    for la, lo in EVREUX[1:]:
        assert distance_min(ch, la, lo) < 3.0          # le chemin passe sur le point (nœud exact)


def test_survol_cap_continu_sans_boucle():
    p = profil(EVREUX, -20.0, 90.0)
    ch = construire(p, EVREUX[0], 1, p.vitesse_ms, tours=1, pas_m=200.0)
    sauts = [abs(ecart_cap(b, a)) for a, b in zip(ch.cap, ch.cap[1:])]
    assert max(sauts) < 6.0                              # nœuds d'arc tous les 2° : pas de cassure de cap
    route = sum(geo.inverse(a[0], a[1], b[0], b[1]).distance for a, b in zip(EVREUX, EVREUX[1:]))
    assert ch.longueur < route * 1.6                     # pas d'orbite ni de boucle parasite


def test_survol_depasse_le_point_contrairement_au_fly_by():
    # au même sommet, le fly-by coupe le virage (passe à distance), le survol passe dessus
    pts = [(49.0, 1.0), (49.0, 1.05), (49.05, 1.05)]       # virage à gauche de 90°
    fb = construire(profil(pts, 25.0), pts[0], 1, 90 * KT, pas_m=200.0)
    fo = construire(profil(pts, -25.0), pts[0], 1, 90 * KT, pas_m=200.0)
    assert distance_min(fb, *pts[1]) > 50.0
    assert distance_min(fo, *pts[1]) < 3.0


def test_survol_rejoint_la_branche_suivante():
    pts = [(49.0, 1.0), (49.0, 1.05), (49.05, 1.05)]
    ch = construire(profil(pts, -25.0), pts[0], 1, 90 * KT, pas_m=200.0)
    # le dernier point est atteint exactement, au cap de la branche (nord)
    assert geo.inverse(ch.lat[-1], ch.lon[-1], *pts[2]).distance < 1.0
    assert abs(ecart_cap(ch.cap[-1], 0.0)) < 2.0


def test_survol_branche_trop_courte_repli_fly_by():
    # branche de sortie de 300 m : la rejointe ne tient pas → repli sur le virage anticipé, sans boucle
    pts = [(49.0, 1.0), (49.0, 1.05), (49.0027, 1.05), (49.0027, 1.10)]
    ch = construire(profil(pts, -25.0, 120.0), pts[0], 1, 120 * KT, pas_m=200.0)
    sauts = [abs(ecart_cap(b, a)) for a, b in zip(ch.cap, ch.cap[1:])]
    assert max(sauts) < 6.0
    assert geo.inverse(ch.lat[-1], ch.lon[-1], *pts[-1]).distance < 1.0


def test_moteur_survol_signale_les_points_et_termine():
    p = profil(EVREUX, -20.0, 120.0)
    evts = []
    m = Moteur(p, lambda e, i: evts.append((e, i)))
    m.demarrer()
    for _ in range(4000):
        m.avancer(0.5)
        if m.etat.etat == "termine":
            break
    assert m.etat.etat == "termine"
    atteints = [i["idx"] for e, i in evts if e == "point_atteint"]
    assert atteints[:4] == [1, 2, 3, 4]


def test_trace_lissee_survol_et_json():
    p = profil(EVREUX, -20.0)
    assert len(trace_lissee(p)) > len(EVREUX)
    q = Profil.depuis_dict(p.vers_dict())
    assert q.inclinaison_max_deg == -20.0                # le signe survit au JSON
