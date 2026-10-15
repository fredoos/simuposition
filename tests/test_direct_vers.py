# -*- coding: utf-8 -*-
"""Reconstruction de trajectoire en vol (mode Simulation) : « Direct vers le point sélectionné » et
changement d'interpolation — virage d'avion depuis le cap courant, sans saut de position ni nez de
travers."""
import math

from simuposition.core import geodesie as geo
from simuposition.core.chemin import ecart_cap, rayon_virage
from simuposition.core.modele import Profil, PointPassage
from simuposition.core.moteur import Moteur, EN_ROUTE, ARRET

KT = geo.KT_MS
EVREUX = [(49.044357, 1.238178), (49.046177, 1.200107), (49.032673, 1.194149),
          (49.016714, 1.271603), (49.007321, 1.256029), (49.0229, 1.1546), (49.0554, 1.1689)]
V = 218.0 * KT
INCL = 25.0


def moteur():
    p = Profil()
    p.points = [PointPassage(la, lo, None, f"WP{i + 1}") for i, (la, lo) in enumerate(EVREUX)]
    p.vitesse_ms, p.inclinaison_max_deg, p.boucle = V, INCL, True
    evts = []
    m = Moteur(p, rappel=lambda e, i: evts.append((e, i)))
    m.pas_sous_s = 0.1
    return m, evts


def verifier_vol(m, secondes, dt=0.5, virage=False, jusqu_a=None, evts=None):
    """Avance et vérifie : pas de saut de position, nez aligné sur la trajectoire ; `virage` : taux de
    virage d'avion (la route elle-même peut imposer des virages serrés signalés, hors direct-to) ;
    `jusqu_a` : arrêt dès que ce point est atteint."""
    e = m.etat
    omega_max = math.degrees(V / rayon_virage(V, INCL))            # °/s
    prec = (e.lat, e.lon, e.cap)
    for _ in range(int(secondes / dt)):
        m.avancer(dt)
        inv = geo.inverse(prec[0], prec[1], e.lat, e.lon)
        assert inv.distance <= V * dt * 1.05 + 1.0, inv.distance           # pas de téléportation
        if inv.distance > 5.0:
            assert abs(ecart_cap(inv.cap_depart, e.cap)) < 6.0             # nez dans l'axe de la trajectoire
        if virage:
            assert abs(ecart_cap(e.cap, prec[2])) <= omega_max * dt * 1.3 + 0.5  # virage d'avion
        prec = (e.lat, e.lon, e.cap)
        if jusqu_a is not None and any(ev == "point_atteint" and i["idx"] == jusqu_a for ev, i in evts):
            return True
    return False


def test_direct_vers_un_point_en_vol_vire_puis_rejoint():
    m, evts = moteur()
    assert m.demarrer()
    verifier_vol(m, 40.0)
    evts.clear()
    m.sauter(5)                                    # WP6, loin derrière / sur le côté
    assert m.etat.etat == EN_ROUTE and m.etat.idx_cible == 5
    assert ("direct_vers", {"idx": 5, "nom": "WP6"}) in evts
    assert verifier_vol(m, 400.0, virage=True, jusqu_a=5, evts=evts)   # virage d'avion jusqu'au point
    verifier_vol(m, 60.0)                                                # puis la route continue


def test_changement_d_interpolation_en_vol_sans_cassure():
    # bascule ortho ↔ loxo toutes les 5 s pendant 3 min : tombe aussi en plein virage, où le chemin
    # reconstruit partait autrefois en ligne droite vers le point suivant (nez de travers)
    m, _ = moteur()
    m.demarrer()
    for _ in range(36):
        verifier_vol(m, 5.0)
        m.profil.interpolation = geo.LOXODROMIE if m.profil.interpolation == geo.ORTHODROMIE else geo.ORTHODROMIE


def test_saut_a_l_arret_reste_un_placement():
    m, _ = moteur()
    assert m.etat.etat == ARRET
    m.sauter(3)
    assert abs(m.etat.lat - EVREUX[3][0]) < 1e-9 and m.etat.idx_cible == 4
