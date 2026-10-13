# -*- coding: utf-8 -*-
"""Tests du moteur d'offset (mode LIVE) : bypass, pilotage auto (séparation/retrouvaille pile sur
le waypoint), pilotage manuel (rampes, continuité), garde-fou d'interception."""
import math

from simuposition.core import geodesie as geo
from simuposition.core.modele import (ConfigOffset, OFFSET_LATERAL, OFFSET_LONGITUDINAL,
                                       OFFSET_RELEVEMENT, PILOTAGE_AUTO, PILOTAGE_MANUEL)
from simuposition.core.offset import MoteurOffset, smoothstep, angle_interception_deg
from simuposition.core.source_gps import Fix


LAT0, LON0 = 49.0, 1.0
COG = 90.0                       # cap est
V = 100.0                        # m/s
DEG_PAR_M_LON = 1.0 / (111320.0 * math.cos(math.radians(LAT0)))


def fix_est(i_m, sog=V, cog=COG, alt=1000.0):
    """Fix sur la parallèle LAT0, à i_m mètres à l'est de (LAT0, LON0)."""
    return Fix(lat=LAT0, lon=LON0 + i_m * DEG_PAR_M_LON, alt_m=alt, sog_ms=sog, cog_deg=cog)


def ecart_m(sortie, fix):
    """Distance (m) entre la sortie décalée et la position réelle."""
    return geo.inverse(fix.lat, fix.lon, sortie.lat, sortie.lon).distance


# --- briques élémentaires -----------------------------------------------------------------
def test_smoothstep_bornes_et_milieu():
    assert smoothstep(-1) == 0.0
    assert smoothstep(0.0) == 0.0
    assert smoothstep(1.0) == 1.0
    assert smoothstep(2) == 1.0
    assert abs(smoothstep(0.5) - 0.5) < 1e-9


def test_angle_interception():
    # v_lat pic = 1.5*M/T ; M=100, T=30, v=100 → atan(1.5*100/3000)=atan(0.05)
    a = angle_interception_deg(100.0, 30.0, 100.0)
    assert abs(a - math.degrees(math.atan(0.05))) < 1e-6
    assert angle_interception_deg(100.0, 0.0, 100.0) == 90.0


# --- bypass (offset nul) ------------------------------------------------------------------
def test_bypass_magnitude_nulle():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, type_offset=OFFSET_LATERAL, magnitude_m=0.0)
    mo = MoteurOffset(cfg)
    f = fix_est(0.0)
    s = mo.calculer(f, 1.0)
    assert s.lat == f.lat and s.lon == f.lon          # trace émise = trace réelle
    assert s.magnitude_m == 0.0


# --- pilotage AUTO ------------------------------------------------------------------------
def _scenario_auto(magnitude=500.0, t_sep=10.0, t_ret=10.0, vitesse=V, pas_m=100.0,
                   depart_m=2000.0, arrivee_m=8000.0, total_m=10000.0):
    cfg = ConfigOffset(pilotage=PILOTAGE_AUTO, type_offset=OFFSET_LATERAL, magnitude_m=magnitude,
                       signe=1, t_separation_s=t_sep, t_retrouvaille_s=t_ret,
                       depart=(LAT0, LON0 + depart_m * DEG_PAR_M_LON),
                       arrivee=(LAT0, LON0 + arrivee_m * DEG_PAR_M_LON), rayon_capture_m=60.0)
    mo = MoteurOffset(cfg)
    journal = []
    mo.rappel = lambda e, i: journal.append((e, i))
    pts = []
    x = 0.0
    while x <= total_m + 1e-6:
        v = vitesse(x) if callable(vitesse) else vitesse
        f = fix_est(x, sog=v)
        s = mo.calculer(f, pas_m / v)        # pas de distance constant ; dt = pas / vitesse
        pts.append((x, s.magnitude_m, ecart_m(s, f), s.phase))
        x += pas_m
    return cfg, pts, journal


def test_auto_avant_depart_pas_doffset():
    _, pts, _ = _scenario_auto()
    for x, mag, ec, _ in pts:
        if x < 1900.0:
            assert mag < 1.0 and ec < 1.0


def test_auto_croisiere_atteint_la_magnitude():
    cfg, pts, _ = _scenario_auto()
    croisiere = [mag for x, mag, ec, ph in pts if ph == "croisiere"]
    assert croisiere, "la phase croisière doit exister"
    assert abs(max(croisiere) - cfg.magnitude_m) < 2.0
    # l'écart réel vaut la magnitude en croisière (offset latéral plein)
    ec_crois = [ec for x, mag, ec, ph in pts if ph == "croisiere"]
    assert abs(max(ec_crois) - cfg.magnitude_m) < 5.0


def test_auto_retrouvaille_tombe_sur_le_waypoint():
    cfg, pts, _ = _scenario_auto(arrivee_m=8000.0, total_m=8000.0)
    # au plus près du waypoint d'arrivée, l'offset est quasi nul
    proche = min(pts, key=lambda p: abs(p[0] - 8000.0))
    assert proche[1] < 20.0, f"offset résiduel au waypoint : {proche[1]:.1f} m"


def test_auto_retrouvaille_robuste_a_la_vitesse():
    # la vitesse double pendant la phase de retrouvaille : on doit quand même retomber sur le WP
    def profil_vitesse(x):
        return V if x < 7200.0 else 2 * V
    cfg, pts, _ = _scenario_auto(vitesse=profil_vitesse, arrivee_m=8000.0, total_m=8000.0)
    proche = min(pts, key=lambda p: abs(p[0] - 8000.0))
    assert proche[1] < 30.0, f"offset résiduel au waypoint (vitesse variable) : {proche[1]:.1f} m"


def test_auto_profil_monotone_sans_a_coup():
    # la magnitude monte de 0 à M puis redescend à 0, sans saut brutal entre deux époques
    _, pts, _ = _scenario_auto()
    mags = [mag for _, mag, _, _ in pts]
    sauts = [abs(b - a) for a, b in zip(mags, mags[1:])]
    assert max(sauts) < 120.0, f"saut d'offset trop grand : {max(sauts):.1f} m"


# --- pilotage MANUEL ----------------------------------------------------------------------
def test_manuel_appliquer_puis_annuler():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, type_offset=OFFSET_LATERAL, magnitude_m=500.0,
                       t_manuel_s=10.0)
    mo = MoteurOffset(cfg)
    f = fix_est(0.0)
    mo.calculer(f, 1.0)                 # fixe la vitesse courante (garde-fou)
    mo.appliquer_manuel()
    mags = []
    for _ in range(14):
        mags.append(mo.calculer(f, 1.0).magnitude_m)
    assert abs(mags[-1] - 500.0) < 2.0           # atteint la magnitude
    assert all(b >= a - 1e-6 for a, b in zip(mags, mags[1:]))   # montée monotone
    mo.annuler_manuel()
    for _ in range(14):
        m = mo.calculer(f, 1.0).magnitude_m
    assert m < 2.0                                # revenu à 0


def test_manuel_continuite_pas_de_saut():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, type_offset=OFFSET_LATERAL, magnitude_m=800.0,
                       t_manuel_s=8.0)
    mo = MoteurOffset(cfg)
    f = fix_est(0.0)
    mo.calculer(f, 1.0)
    mo.appliquer_manuel()
    prec = 0.0
    for _ in range(12):
        m = mo.calculer(f, 1.0).magnitude_m
        assert abs(m - prec) < 200.0             # pas de téléportation d'offset
        prec = m


def test_manuel_bypass_au_depart():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, magnitude_m=500.0, t_manuel_s=10.0)
    mo = MoteurOffset(cfg)
    f = fix_est(0.0)
    s = mo.calculer(f, 1.0)              # avant toute commande : offset nul
    assert ecart_m(s, f) < 1.0


# --- garde-fou ---------------------------------------------------------------------------
def test_garde_fou_interception_serree():
    # M=2000 m en T=5 s à 100 m/s → angle ≈ 80° > 40° : alerte
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, type_offset=OFFSET_LATERAL, magnitude_m=2000.0,
                       t_manuel_s=5.0, angle_garde_deg=40.0)
    mo = MoteurOffset(cfg)
    alertes = []
    mo.rappel = lambda e, i: alertes.append((e, i))
    mo.calculer(fix_est(0.0), 1.0)
    mo.appliquer_manuel()
    assert any(e == "interception_serree" for e, _ in alertes)


# --- direction longitudinale / relèvement ------------------------------------------------
def test_offset_longitudinal_devant():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, type_offset=OFFSET_LONGITUDINAL, magnitude_m=300.0,
                       signe=1, t_manuel_s=1.0)
    mo = MoteurOffset(cfg)
    f = fix_est(0.0, cog=90.0)
    mo.calculer(f, 1.0)
    mo.appliquer_manuel()
    s = mo.calculer(f, 5.0)              # transition finie
    # décalage vers l'est (devant) : longitude plus grande, même latitude
    assert s.lon > f.lon and abs(s.lat - f.lat) < 1e-4
    assert abs(ecart_m(s, f) - 300.0) < 5.0


def test_offset_relevement_fixe():
    cfg = ConfigOffset(pilotage=PILOTAGE_MANUEL, type_offset=OFFSET_RELEVEMENT, magnitude_m=400.0,
                       relevement_deg=0.0, t_manuel_s=1.0)
    mo = MoteurOffset(cfg)
    f = fix_est(0.0, cog=90.0)
    mo.calculer(f, 1.0)
    mo.appliquer_manuel()
    s = mo.calculer(f, 5.0)
    # relèvement 0° = plein nord : latitude plus grande, longitude ~inchangée
    assert s.lat > f.lat and abs(s.lon - f.lon) < 1e-4
