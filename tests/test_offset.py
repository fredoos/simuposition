# -*- coding: utf-8 -*-
"""Tests du moteur d'offset (mode Direct) : translation de la trace réelle, barrières D/A
(franchissement orienté, largeur), transitions calculées par l'angle d'interception, retrouvaille
pile sur la ligne A, pilotage manuel, Stop en mode Direct."""
import math

from simuposition.core import geodesie as geo
from simuposition.core.modele import (ConfigOffset, OFFSET_LATERAL, OFFSET_LONGITUDINAL,
                                       OFFSET_RELEVEMENT, PILOTAGE_AUTO, PILOTAGE_MANUEL)
from simuposition.core.offset import (MoteurOffset, smoothstep, decomposer, vecteur_offset,
                                      longueur_transition, facteur_vitesse, Barriere)
from simuposition.core.source_gps import Fix


LAT0, LON0 = 49.0, 1.0
V = 100.0                        # m/s
M_PAR_DEG_LAT = 111200.0
DEG_PAR_M_LON = 1.0 / (111320.0 * math.cos(math.radians(LAT0)))


def pos(x_m, y_m=0.0):
    """(lat, lon) à x_m mètres à l'est et y_m mètres au nord de (LAT0, LON0) (plan local)."""
    return LAT0 + y_m / M_PAR_DEG_LAT, LON0 + x_m * DEG_PAR_M_LON


def fix_xy(x_m, y_m=0.0, cog=90.0, sog=V, alt=1000.0):
    la, lo = pos(x_m, y_m)
    return Fix(lat=la, lon=lo, alt_m=alt, sog_ms=sog, cog_deg=cog)


def ecart_m(sortie, fix):
    return geo.inverse(fix.lat, fix.lon, sortie.lat, sortie.lon).distance


def azimut_ecart(sortie, fix):
    return geo.inverse(fix.lat, fix.lon, sortie.lat, sortie.lon).cap_depart


def cfg_auto(mag=1000.0, d_x=2000.0, a_x=20000.0, **kw):
    """Barrières D et A sur la parallèle LAT0 (axe D→A plein est). Par défaut : translation exacte
    (comportement « libre ») pour tester la géométrie du plan seule."""
    kw.setdefault("comportement", "libre")
    return ConfigOffset(pilotage=PILOTAGE_AUTO, type_offset=OFFSET_LATERAL, magnitude_m=mag, signe=1,
                        depart=pos(d_x), arrivee=pos(a_x), **kw)


def vol_est(mo, x0, x1, pas=100.0, y=0.0):
    """Vol plein est de x0 à x1 ; renvoie [(fix, sortie)]."""
    res = []
    x = x0
    while x <= x1 + 1e-6:
        f = fix_xy(x, y)
        res.append((f, mo.calculer(f, pas / V)))
        x += pas
    return res


# --- briques élémentaires -----------------------------------------------------------------
def test_smoothstep_bornes_et_milieu():
    assert smoothstep(-1.0) == 0.0 and smoothstep(0.0) == 0.0
    assert smoothstep(1.0) == 1.0 and smoothstep(2.0) == 1.0
    assert abs(smoothstep(0.5) - 0.5) < 1e-12


def test_vecteur_et_decomposition():
    c = ConfigOffset(type_offset=OFFSET_LATERAL, magnitude_m=1000.0, signe=1)
    v = vecteur_offset(c, 90.0)                       # droite d'un cap est = sud
    assert abs(v[0]) < 1e-9 and abs(v[1] + 1000.0) < 1e-9
    lon_, lat_ = decomposer(v, 90.0)
    assert abs(lon_) < 1e-9 and abs(lat_ - 1000.0) < 1e-9
    c.type_offset, c.signe = OFFSET_LONGITUDINAL, -1  # arrière d'un cap est = ouest
    v = vecteur_offset(c, 90.0)
    assert abs(v[0] + 1000.0) < 1e-9


def test_longueur_par_angle_d_interception():
    v = (0.0, -1000.0, 0.0)                           # 1 km à droite d'un cap est
    L, ok = longueur_transition(v, 90.0, 30.0)
    assert ok and abs(L - 1.5 * 1000.0 / math.tan(math.radians(30.0))) < 1e-3


def test_longueur_bornee_par_la_vitesse_max():
    v, q = 70.0, 75.0 / 70.0                          # V max 75 m/s, réel 70 m/s
    L, ok = longueur_transition((0.0, -1000.0, 0.0), 90.0, 30.0, v, 0.0, 75.0)
    assert ok and abs(L - 1500.0 / math.sqrt(q * q - 1.0)) < 1.0
    assert facteur_vitesse((0.0, -1000.0, 0.0), 90.0, L)[1] <= q + 1e-6


def test_longueur_bornee_par_la_vitesse_min_offset_arriere():
    # 1 km vers l'arrière (ouest) : l'avion simulé ralentit ; V min 40 m/s pour un réel à 50 m/s
    L, ok = longueur_transition((-1000.0, 0.0, 0.0), 90.0, 30.0, 50.0, 40.0, 100.0)
    assert ok and abs(L - 1500.0 / 0.2) < 1.0
    # sans V min : seule l'interdiction de marche arrière (écart de route ≤ angle) s'applique
    L2, _ = longueur_transition((-1000.0, 0.0, 0.0), 90.0, 30.0)
    assert abs(L2 - 1500.0) < 1.0 and L2 < L


def test_longueur_reel_hors_limites():
    _, ok = longueur_transition((0.0, -1000.0, 0.0), 90.0, 30.0, 90.0, 20.0, 75.0)
    assert not ok
    # réel sous V min : V min ignorée mais V max toujours tenue (pas de saut instantané)
    L, ok = longueur_transition((556.0, 0.0, 0.0), 90.0, 30.0, 10.0, 25.0, 77.0)
    assert not ok and facteur_vitesse((556.0, 0.0, 0.0), 90.0, L)[1] <= 7.7 + 1e-6 and L > 100.0


def test_manuel_apres_reinit_garde_cap_et_vitesse_du_vrai_avion():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, comportement="libre", type_offset=OFFSET_LONGITUDINAL,
                       magnitude_m=556.0, signe=1)
    mo = MoteurOffset(cfg)
    mo.calculer(fix_xy(0.0, sog=50.0), 1.0)
    mo.reinit()                                       # changement de pilotage, balise déplacée…
    mo.appliquer_manuel()
    assert mo.longueur_manuelle_m() > 1000.0          # vitesse réelle connue : transition progressive
    s = mo.calculer(fix_xy(50.0, sog=50.0), 1.0)
    assert s.magnitude_m < 10.0


def test_barriere_franchissement_oriente_et_largeur():
    b = Barriere(LAT0, LON0, 90.0, 500.0)
    av = b.repere(*pos(-50.0), geo.ORTHODROMIE)
    ap = b.repere(*pos(50.0), geo.ORTHODROMIE)
    assert av[0] < 0.0 < ap[0]
    assert b.franchie(av, ap)                         # dans le sens du cap
    assert not b.franchie(ap, av)                     # à contresens
    av2 = b.repere(*pos(-50.0, 800.0), geo.ORTHODROMIE)
    ap2 = b.repere(*pos(50.0, 800.0), geo.ORTHODROMIE)
    assert not b.franchie(av2, ap2)                   # à 800 m du centre : hors barrière (± 500 m)


def test_bypass_magnitude_nulle():
    mo = MoteurOffset(cfg_auto(mag=0.0))
    for f, s in vol_est(mo, 0.0, 22000.0, 500.0):
        assert ecart_m(s, f) < 0.01


# --- pilotage auto : barrières ------------------------------------------------------------
def test_auto_rien_avant_la_barriere_D():
    mo = MoteurOffset(cfg_auto())
    for f, s in vol_est(mo, 0.0, 1900.0):
        assert s.magnitude_m == 0.0
    assert mo.phase == "avant" and abs(mo.avant_D - 100.0) < 2.0


def test_auto_barriere_D_manquee_hors_largeur():
    mo = MoteurOffset(cfg_auto(demi_largeur_m=500.0))
    for f, s in vol_est(mo, 0.0, 6000.0, y=1500.0):  # passe à 1,5 km du centre de D
        assert s.magnitude_m == 0.0
    assert mo.phase == "avant"


def test_auto_separation_respecte_l_angle_et_atteint_la_distance():
    cfg = cfg_auto(mag=1000.0, angle_interception_deg=30.0)
    mo = MoteurOffset(cfg)
    res = vol_est(mo, 0.0, 12000.0, pas=50.0)
    L = 1.5 * 1000.0 / math.tan(math.radians(30.0))   # ≈ 2598 m
    # pleine distance une fois L parcourue après D (x = 2000 + L)
    for f, s in res:
        x = (f.lon - LON0) / DEG_PAR_M_LON
        if x > 2000.0 + L + 60.0:
            assert abs(s.magnitude_m - 1000.0) < 1e-6
    # angle de l'avion simulé par rapport à la trace réelle (plein est) ≤ 30° (+ tolérance)
    ang_max = 0.0
    for (f0, s0), (f1, s1) in zip(res, res[1:]):
        cap = geo.inverse(s0.lat, s0.lon, s1.lat, s1.lon).cap_depart
        ang_max = max(ang_max, abs((cap - 90.0 + 180.0) % 360.0 - 180.0))
    assert 25.0 < ang_max <= 30.5, ang_max
    assert mo.phase == "croisiere"


def test_auto_offset_translate_ne_pivote_pas_en_virage():
    mo = MoteurOffset(cfg_auto(mag=1000.0, a_x=60000.0))
    vol_est(mo, 0.0, 8000.0)
    assert mo.phase == "croisiere"
    # virage au nord : l'offset reste au sud du vrai avion (vecteur figé), il ne passe pas à l'est
    traj = [fix_xy(8000.0, y, cog=0.0) for y in range(0, 3001, 100)]
    sorties = [mo.calculer(f, 1.0) for f in traj]
    for f, s in zip(traj, sorties):
        assert abs(ecart_m(s, f) - 1000.0) < 1.0
        assert abs(azimut_ecart(s, f) - 180.0) < 0.5
    # même vitesse que le vrai avion (translation)
    for (f0, s0), (f1, s1) in zip(zip(traj, sorties), list(zip(traj, sorties))[1:]):
        d_r = geo.inverse(f0.lat, f0.lon, f1.lat, f1.lon).distance
        d_s = geo.inverse(s0.lat, s0.lon, s1.lat, s1.lon).distance
        assert abs(d_s - d_r) < 0.5


def test_auto_retrouvaille_pile_sur_la_ligne_A_meme_decentree():
    cfg = cfg_auto(mag=1000.0, a_x=20000.0, demi_largeur_m=3000.0)
    mo = MoteurOffset(cfg)
    # trajectoire réelle décalée de 1,2 km au nord de l'axe : franchit A loin de son centre
    res = vol_est(mo, 0.0, 20500.0, pas=50.0, y=1200.0)
    for f, s in res:
        x = (f.lon - LON0) / DEG_PAR_M_LON
        if 19940.0 <= x < 20000.0:
            assert ecart_m(s, f) < 2.0               # quasi rejoint juste avant la ligne
        if x >= 20000.0 + 1.0:
            assert ecart_m(s, f) < 0.01              # confondu dès la ligne franchie
    assert mo.phase == "apres"
    # pas d'à-coup : écart décroissant pendant la retrouvaille
    ecarts = [ecart_m(s, f) for f, s in res if 16000.0 < (f.lon - LON0) / DEG_PAR_M_LON < 20000.0]
    assert all(b <= a + 1e-6 for a, b in zip(ecarts, ecarts[1:]))


def test_auto_cap_et_vitesse_egaux_au_franchissement():
    # le cap simulé converge vers le cap réel en approchant la ligne (smoothstep : pente nulle en A)
    mo = MoteurOffset(cfg_auto(mag=1000.0))
    vol_est(mo, 0.0, 19900.0, pas=50.0)
    res = vol_est(mo, 19950.0, 19999.0, pas=1.0)
    (f0, s0), (f1, s1) = res[-2], res[-1]             # deux dernières époques avant la ligne
    d_r = geo.inverse(f0.lat, f0.lon, f1.lat, f1.lon)
    d_s = geo.inverse(s0.lat, s0.lon, s1.lat, s1.lon)
    assert abs(d_s.distance - d_r.distance) < 0.1
    assert abs(((d_s.cap_depart - d_r.cap_depart) + 180.0) % 360.0 - 180.0) < 0.5


def test_auto_depart_tardif_entre_D_et_A():
    evts = []
    mo = MoteurOffset(cfg_auto(mag=1000.0, d_x=2000.0, a_x=30000.0), rappel=lambda e, i: evts.append((e, i)))
    res = vol_est(mo, 5000.0, 6000.0)                # l'avion démarre déjà au-delà de D, cap vers A
    assert mo.phase == "separation" and ("barriere", {"quel": "D", "tardif": True}) in evts
    assert res[0][1].magnitude_m < 1.0               # la séparation part de là, sans saut
    res = vol_est(mo, 6100.0, 31000.0)
    assert max(s.magnitude_m for f, s in res) > 999.0 and mo.phase == "apres"


def test_auto_pas_de_depart_tardif_si_route_opposee_a_A():
    mo = MoteurOffset(cfg_auto(d_x=2000.0, a_x=30000.0))
    for i in range(10):
        mo.calculer(fix_xy(6000.0 - 100.0 * i, cog=270.0), 1.0)
    assert mo.phase == "avant"


def test_auto_contresens_ne_declenche_pas():
    mo = MoteurOffset(cfg_auto())
    x = 25000.0
    while x >= 0.0:                                  # vol plein ouest : D franchie à contresens
        mo.calculer(fix_xy(x, cog=270.0), 1.0)
        x -= 100.0
    assert mo.phase == "avant" and mo.magnitude_courante == 0.0


def test_auto_barrieres_proches_offset_partiel_et_alerte():
    evts = []
    cfg = cfg_auto(mag=1000.0, d_x=2000.0, a_x=4000.0)   # 2 km entre D et A (< 2·L)
    mo = MoteurOffset(cfg, rappel=lambda e, i: evts.append(e))
    res = vol_est(mo, 0.0, 4500.0, pas=20.0)
    assert "barrieres_proches" in evts
    mags = [s.magnitude_m for f, s in res]
    assert 0.0 < max(mags) < 1000.0
    assert res[-1][1].magnitude_m == 0.0 and mo.phase == "apres"


def test_auto_rearme_au_tour_suivant():
    mo = MoteurOffset(cfg_auto(mag=500.0, d_x=2000.0, a_x=12000.0))
    for tour in range(2):
        res = vol_est(mo, 0.0, 12500.0)
        assert max(s.magnitude_m for f, s in res) > 499.0, f"tour {tour + 1}"
        assert mo.phase == "apres"


def test_caps_barrieres_axe_par_defaut_et_reglage():
    cfg = cfg_auto()
    mo = MoteurOffset(cfg)
    cd, ca = mo.caps_barrieres()
    assert abs(cd - 90.0) < 0.2 and abs(ca - 90.0) < 0.5
    cfg.cap_depart_deg = 45.0
    assert abs(mo.caps_barrieres()[0] - 45.0) < 1e-9
    p = mo.plan(V)
    assert p["suffisant"] and abs(p["t_sep"] - p["l_sep"] / V) < 1e-9


# --- pilotage manuel ------------------------------------------------------------------------
def test_manuel_appliquer_puis_annuler_sur_la_distance_calculee():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, comportement="libre", type_offset=OFFSET_LATERAL, magnitude_m=500.0, signe=1)
    mo = MoteurOffset(cfg)
    x = 0.0
    mo.calculer(fix_xy(x), 1.0)
    mo.appliquer_manuel()
    L = mo.longueur_manuelle_m()
    assert abs(L - 1.5 * 500.0 / math.tan(math.radians(30.0))) < 1e-6
    mags = []
    while x < L + 500.0:
        x += 100.0
        mags.append(mo.calculer(fix_xy(x), 1.0).magnitude_m)
    assert abs(mags[-1] - 500.0) < 1e-6
    assert all(b >= a - 1e-9 for a, b in zip(mags, mags[1:]))      # monotone, sans saut
    assert max(b - a for a, b in zip(mags, mags[1:])) < 1.5 * 500.0 * 100.0 / L + 1e-6
    mo.annuler_manuel()
    for _ in range(int(L / 100.0) + 3):
        x += 100.0
        s = mo.calculer(fix_xy(x), 1.0)
    assert s.magnitude_m == 0.0


def test_manuel_translation_figee_au_cap_de_la_commande():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, comportement="libre", type_offset=OFFSET_LATERAL, magnitude_m=300.0, signe=1)
    mo = MoteurOffset(cfg)
    mo.calculer(fix_xy(0.0), 1.0)
    mo.appliquer_manuel()
    x = 0.0
    while x < 2000.0:
        x += 100.0
        mo.calculer(fix_xy(x), 1.0)
    f = fix_xy(x, 500.0, cog=0.0)                    # virage au nord
    s = mo.calculer(f, 1.0)
    assert abs(azimut_ecart(s, f) - 180.0) < 0.5     # toujours au sud (droite du cap à la commande)


def test_manuel_progresse_gps_immobile():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, comportement="libre", type_offset=OFFSET_LATERAL, magnitude_m=100.0)
    mo = MoteurOffset(cfg)
    f = fix_xy(0.0, sog=0.0)
    mo.calculer(f, 1.0)
    mo.appliquer_manuel()
    L = mo.longueur_manuelle_m()
    for _ in range(int(L / 10.0) + 2):               # 10 m/s minimum de progression
        s = mo.calculer(f, 1.0)
    assert abs(s.magnitude_m - 100.0) < 1e-6


def test_offset_relevement_et_vertical():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, comportement="libre", type_offset=OFFSET_RELEVEMENT, magnitude_m=400.0,
                       relevement_deg=0.0, vertical_m=100.0)
    mo = MoteurOffset(cfg)
    mo.calculer(fix_xy(0.0), 1.0)
    mo.appliquer_manuel()
    x = 0.0
    for _ in range(100):
        x += 100.0
        f = fix_xy(x)
        s = mo.calculer(f, 1.0)
    assert abs(ecart_m(s, f) - 400.0) < 1.0 and abs(azimut_ecart(s, f)) < 0.5
    assert abs(s.alt_m - (f.alt_m + 100.0)) < 1e-6


# --- comportement de l'avion simulé (avion / hélico / hippodrome) ---------------------------
G = 9.80665


def _cinematique(sorties, dt):
    """Vitesses (m/s) et caps (°) de l'avion simulé entre sorties consécutives."""
    vit, caps = [], []
    for s0, s1 in zip(sorties, sorties[1:]):
        inv = geo.inverse(s0.lat, s0.lon, s1.lat, s1.lon)
        vit.append(inv.distance / dt)
        caps.append(inv.cap_depart)
    return vit, caps


def test_avion_bypass_exact_meme_sous_vmin():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, comportement="avion", magnitude_m=0.0)
    mo = MoteurOffset(cfg)
    for i in range(30):                              # roulage à 5 m/s, bien sous V min
        f = fix_xy(5.0 * i, sog=5.0)
        assert ecart_m(mo.calculer(f, 1.0), f) < 0.01


def test_avion_changements_d_ordre_restent_un_vol_d_avion():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, comportement="avion", type_offset=OFFSET_LATERAL,
                       magnitude_m=2000.0, signe=1, vitesse_min_ms=25.0, vitesse_max_ms=75.0,
                       inclinaison_max_deg=30.0, accel_max_ms2=1.0)
    mo = MoteurOffset(cfg)
    sorties = []
    for i in range(400):                              # vrai avion plein est à 50 m/s
        if i == 1:
            mo.appliquer_manuel()                     # 2 km à droite
        elif i == 40:
            mo.annuler_manuel()                       # ordre inversé en cours de route
        elif i == 60:
            cfg.signe = -1
            mo.appliquer_manuel()                     # puis 2 km à gauche
        sorties.append(mo.calculer(fix_xy(50.0 * i, sog=50.0), 1.0))
    vit, caps = _cinematique(sorties, 1.0)
    assert min(vit[1:]) >= 25.0 * 0.97 and max(vit) <= 75.0 * 1.03   # jamais à l'arrêt ni au-delà de V max
    for (c0, c1), v in zip(zip(caps, caps[1:]), vit[1:]):
        taux = abs((c1 - c0 + 180.0) % 360.0 - 180.0)
        assert taux <= math.degrees(G * math.tan(math.radians(30.0)) / max(v, 5.0)) * 1.15 + 1.0
    # à la fin : 2 km à gauche (nord) du vrai avion
    f = fix_xy(50.0 * 399, sog=50.0)
    assert abs(ecart_m(sorties[-1], f) - 2000.0) < 20.0 and abs((azimut_ecart(sorties[-1], f) + 180.0) % 360.0 - 180.0) < 2.0


def test_helico_peut_ralentir_jusqu_au_stationnaire():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, comportement="helico", type_offset=OFFSET_LONGITUDINAL,
                       magnitude_m=3000.0, signe=-1, vitesse_max_ms=70.0, accel_max_ms2=2.0)
    mo = MoteurOffset(cfg)
    sorties = []
    for i in range(400):                              # réel à 30 m/s ; l'hélico doit reculer de 3 km
        if i == 1:
            mo.appliquer_manuel()
        sorties.append(mo.calculer(fix_xy(30.0 * i, sog=30.0), 1.0))
    vit, _ = _cinematique(sorties, 1.0)
    assert min(vit) < 25.0                            # sous la V min d'un avion : possible en hélico
    assert max(vit) <= 70.0 * 1.03
    f = fix_xy(30.0 * 399, sog=30.0)
    assert abs(ecart_m(sorties[-1], f) - 3000.0) < 20.0


def test_avion_hippodrome_quand_le_gps_stagne_puis_rejoint_en_A():
    cfg = cfg_auto(mag=1000.0, d_x=1000.0, a_x=30000.0, comportement="avion",
                   vitesse_min_ms=25.0, vitesse_max_ms=75.0, inclinaison_max_deg=30.0, hippodrome=True)
    mo = MoteurOffset(cfg)
    traj = [fix_xy(50.0 * i, sog=50.0) for i in range(200)]          # 10 km à 50 m/s
    traj += [fix_xy(50.0 * 199, sog=0.0)] * 300                      # GPS immobile 5 min
    traj += [fix_xy(50.0 * 199 + 50.0 * i, sog=50.0) for i in range(1, 450)]   # reprise jusqu'après A
    sorties, modes = [], []
    for f in traj:
        sorties.append(mo.calculer(f, 1.0))
        modes.append(mo.suiveur.mode)
    assert "hippodrome" in modes[200:500]
    vit, _ = _cinematique(sorties, 1.0)
    assert min(vit[205:500]) >= 25.0 * 0.95          # l'avion simulé ne passe jamais sous V min
    for f, s in zip(traj[200:500], sorties[200:500]):            # il reste près du point visé
        assert ecart_m(s, f) < 1000.0 + 2500.0
    assert mo.phase == "apres"
    assert ecart_m(sorties[-1], traj[-1]) < 0.01      # confondu après la barrière A


def test_avion_hippodrome_cale_l_arrivee_sur_le_vrai_avion_lent():
    # le vrai « avion » avance à 10 m/s (sous V min = 25 m/s) jusqu'à A : l'avion simulé tourne en
    # hippodrome et la dernière boucle est calée pour arriver avec lui sur la barrière A
    cfg = cfg_auto(mag=1000.0, d_x=1000.0, a_x=30000.0, comportement="avion",
                   vitesse_min_ms=25.0, vitesse_max_ms=75.0, inclinaison_max_deg=30.0, hippodrome=True)
    mo = MoteurOffset(cfg)
    traj = [fix_xy(50.0 * i, sog=50.0) for i in range(200)]
    traj += [fix_xy(50.0 * 199 + 10.0 * i, sog=10.0) for i in range(1, 2200)]
    sorties, ecart_avant_A = [], None
    for f in traj:
        ph = mo.phase
        W_prec = mo.suiveur.W
        sorties.append(mo.calculer(f, 1.0))
        if ph != "apres" and mo.phase == "apres":
            ecart_avant_A = math.hypot(*W_prec)
            break
    assert ecart_avant_A is not None
    assert ecart_avant_A < 30.0, ecart_avant_A       # rendez-vous à la barrière, à une époque près
    vit, _ = _cinematique(sorties[205:-1], 1.0)
    assert min(vit) >= 25.0 * 0.95 and max(vit) <= 75.0 * 1.03


# --- accrochage au trajet --------------------------------------------------------------------
def test_point_le_plus_proche_projection_orthogonale():
    trajet = [pos(0.0), pos(2000.0)]                 # 2 km vers l'est
    la, lo, d = geo.point_le_plus_proche(*pos(1000.0, 300.0), trajet)
    assert abs(d - 300.0) < 3.0
    assert abs(la - LAT0) < 1e-6
    assert abs((lo - LON0) / DEG_PAR_M_LON - 1000.0) < 5.0
    la, lo, d = geo.point_le_plus_proche(*pos(2500.0), trajet)
    assert abs((lo - LON0) / DEG_PAR_M_LON - 2000.0) < 1.0 and abs(d - 500.0) < 3.0


def test_projection_donne_le_cap_du_segment():
    trajet = [pos(0.0), pos(2000.0), pos(2000.0, 2000.0)]   # est puis nord
    *_, cap = geo.projection_sur_polyligne(*pos(1000.0, 100.0), trajet)
    assert abs(cap - 90.0) < 0.5
    *_, cap = geo.projection_sur_polyligne(*pos(2100.0, 1000.0), trajet)
    assert abs(cap) < 0.5 or abs(cap - 360.0) < 0.5


# --- mode Direct : Stop / Démarrer -----------------------------------------------------------
def test_direct_stop_fige_la_sortie_et_demarrer_reprend():
    from simuposition.core.modele import Profil, MODE_LIVE
    from simuposition.core.moteur import Moteur, EN_ROUTE, ARRET

    class Src:
        def __init__(self): self.f, self.n = None, 0
        def dernier_fix(self): return self.f, self.n
        def pousser(self, f): self.f, self.n = f, self.n + 1

    m = Moteur(Profil())
    m.definir_mode(MODE_LIVE)
    src = Src(); m.brancher_source(src)
    assert m.etat.etat == ARRET
    src.pousser(fix_xy(0.0)); m.avancer(1.0)
    assert not m.sortie_prete                       # à l'arrêt : rien n'est émis
    assert m.dernier_fix is not None                # mais la position réelle est suivie
    assert m.demarrer() and m.etat.etat == EN_ROUTE # pas besoin de route en mode Direct
    src.pousser(fix_xy(100.0)); m.avancer(1.0)
    assert m.sortie_prete
    lon_emise = m.etat.lon
    m.arreter()
    src.pousser(fix_xy(200.0)); m.avancer(1.0)
    assert m.etat.etat == ARRET                     # un nouveau fix ne relance plus la simulation
    assert not m.sortie_prete and m.etat.lon == lon_emise
    m.demarrer(); src.pousser(fix_xy(300.0)); m.avancer(1.0)
    assert m.sortie_prete and m.etat.lon > lon_emise
