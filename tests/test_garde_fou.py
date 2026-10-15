# -*- coding: utf-8 -*-
"""Tests du garde-fou de sortie et de la continuité de la position émise (aucun saut).
© skydev 2026"""
import math
import random
from datetime import datetime, timedelta, timezone

from simuposition.core import geodesie as geo
from simuposition.core.garde_fou import GardeFou, V_GARDE_MIN, VZ_GARDE_MIN
from simuposition.core.modele import (Profil, PointPassage, ConfigOffset, MODE_SIM, MODE_LIVE,
                                       PILOTAGE_AUTO, PILOTAGE_MANUEL)
from simuposition.core.moteur import Moteur, EN_ROUTE
from simuposition.core.source_gps import Fix

T0 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
LAT0, LON0 = 49.0, 1.0


# --- garde-fou seul ----------------------------------------------------------------------------
def test_premiere_sortie_libre_puis_saut_horizontal_borne():
    evts = []
    g = GardeFou(rappel=lambda e, i: evts.append((e, i)))
    assert g.filtrer(LAT0, LON0, 1000.0, T0, 100.0, 5.0) == (LAT0, LON0, 1000.0)
    la, lo, _ = geo.direct(LAT0, LON0, 90.0, 20000.0)               # 20 km en 1 s : aberrant
    s = g.filtrer(la, lo, 1000.0, T0 + timedelta(seconds=1), 100.0, 5.0)
    d = geo.inverse(LAT0, LON0, s[0], s[1]).distance
    assert abs(d - (1.3 * 100.0 + 20.0)) < 0.5                       # borné à V_garde · dt
    assert len(evts) == 1 and evts[0][0] == "garde_fou" and evts[0][1]["saut_m"] > 19000.0
    # la sortie converge vers la valeur calculée, un seul signalement pour l'épisode
    for k in range(2, 200):
        s = g.filtrer(la, lo, 1000.0, T0 + timedelta(seconds=k), 100.0, 5.0)
    assert geo.inverse(la, lo, s[0], s[1]).distance < 1e-3 and len(evts) == 1 and g.retenue_m == 0.0


def test_saut_d_altitude_borne_et_nan_tenu():
    g = GardeFou()
    g.filtrer(LAT0, LON0, 1000.0, T0, 50.0, 5.0)
    s = g.filtrer(LAT0, LON0, 1300.0, T0 + timedelta(seconds=1), 50.0, 5.0)  # +300 m en 1 s
    assert abs(s[2] - (1000.0 + VZ_GARDE_MIN)) < 1e-6
    s2 = g.filtrer(float("nan"), LON0, 1000.0, T0 + timedelta(seconds=2), 50.0, 5.0)
    assert s2 == (s[0], s[1], s[2])                                  # valeur non finie : sortie tenue


def test_mouvement_legitime_non_modifie():
    g = GardeFou()
    lat, lon, alt = LAT0, LON0, 1000.0
    for k in range(100):                                             # 150 m/s, 10 m/s de montée
        lat, lon, _ = geo.direct(lat, lon, 45.0, 150.0)
        alt += 10.0
        assert g.filtrer(lat, lon, alt, T0 + timedelta(seconds=k), 150.0, 10.0) == (lat, lon, alt)
    assert g.nb_episodes == 0
    assert V_GARDE_MIN > 0


# --- moteur : continuité de la sortie ------------------------------------------------------------
class _Source:
    def __init__(self):
        self.f, self.n, self.age = None, 0, 0.0

    def dernier_fix(self):
        return self.f, self.n

    def age_s(self):
        return self.age

    def pousser(self, f):
        self.f, self.n, self.age = f, self.n + 1, 0.0


def _direct(**kw):
    p = Profil()
    p.offset = ConfigOffset(**{**dict(pilotage=PILOTAGE_MANUEL, comportement="avion", magnitude_m=0.0,
                                      vitesse_min_ms=40.0, vitesse_max_ms=120.0, accel_max_ms2=1.0), **kw})
    m = Moteur(p)
    m.definir_mode(MODE_LIVE)
    src = _Source()
    m.brancher_source(src)
    m.demarrer()
    return m, src


def _fix(i, alt=1000.0, v=100.0):
    la, lo, _ = geo.direct(LAT0, LON0, 90.0, v * i)
    return Fix(lat=la, lon=lo, alt_m=alt, sog_ms=v, cog_deg=90.0, utc=T0 + timedelta(seconds=i))


def _vitesse_max_sortie(m, src, debut, fin, action=None, alt=lambda i: 1000.0):
    """Plus grandes vitesses horizontale / verticale de la sortie entre deux époques consécutives."""
    vh = vz = 0.0
    prec = None
    for i in range(debut, fin):
        if action is not None and i == action[0]:
            action[1]()
        src.pousser(_fix(i, alt(i))); m.avancer(1.0)
        e = m.etat
        if prec is not None:
            vh = max(vh, geo.inverse(prec[0], prec[1], e.lat, e.lon).distance)
            vz = max(vz, abs(e.alt_m - prec[2]))
        prec = (e.lat, e.lon, e.alt_m)
    return vh, vz


def test_balise_deplacee_en_emission_sans_saut():
    """Pilotage auto, offset établi : déplacer A (nouveau plan) ne fait plus sauter la sortie."""
    m, src = _direct(pilotage=PILOTAGE_AUTO, magnitude_m=9260.0,
                     depart=geo.direct(LAT0, LON0, 90.0, 2000.0)[:2],
                     arrivee=geo.direct(LAT0, LON0, 90.0, 200000.0)[:2])
    for i in range(0, 200):
        src.pousser(_fix(i)); m.avancer(1.0)
    assert m.offset.magnitude_courante > 5000.0                      # offset en place

    def deplacer():
        m.profil.offset.arrivee = geo.direct(LAT0, LON0, 90.0, 260000.0)[:2]
        m.offset.reinit_plan()
    vh, _ = _vitesse_max_sortie(m, src, 200, 260, (210, deplacer))
    assert vh <= 1.3 * 120.0 + 20.0 + 1.0 and m.offset.suiveur.mode != "bypass"


def test_mode_inchange_sans_effet():
    m, src = _direct(magnitude_m=5000.0, offset_initial=True)
    m.arreter(); m.demarrer()
    for i in range(0, 20):
        src.pousser(_fix(i)); m.avancer(1.0)
    vh, _ = _vitesse_max_sortie(m, src, 20, 40, (25, lambda: m.definir_mode(MODE_LIVE)))
    assert m.etat.etat == EN_ROUTE and vh <= 101.0                   # offset conservé : 100 m/s


def test_palier_d_altitude_du_gps_reel_borne():
    m, src = _direct()
    _, vz = _vitesse_max_sortie(m, src, 0, 60, alt=lambda i: 1000.0 if i < 30 else 1300.0)
    assert vz <= VZ_GARDE_MIN + 1e-6 and m.garde.nb_episodes >= 1
    assert abs(m.etat.alt_m - 1300.0) < 1e-6                         # rejointe ensuite


def test_aller_au_point_en_route_sans_saut_meme_sans_inclinaison():
    p = Profil()
    p.points = [PointPassage(49.0, 1.0, None, "A"), PointPassage(49.0, 1.2, None, "B"),
                PointPassage(49.1, 1.2, None, "C")]
    p.vitesse_ms, p.inclinaison_max_deg = 100.0, 0.0
    m = Moteur(p)
    m.demarrer()
    for _ in range(50):
        m.avancer(0.1)
    la, lo = m.etat.lat, m.etat.lon
    m.sauter(2)
    m.avancer(0.1)
    assert geo.inverse(la, lo, m.etat.lat, m.etat.lon).distance <= 100.0 * 0.1 + 0.5


def test_martelage_aleatoire_sans_saut():
    """Actions opérateur au hasard en Direct (offset, balises, pilotage, maintien, mode…) avec un GPS
    réel qui vire, accélère et fait des paliers d'altitude : la sortie reste dans les bornes."""
    r = random.Random(7)
    m, src = _direct(magnitude_m=5000.0, depart=(49.0, 1.03), arrivee=(49.0, 1.6))
    o = m.profil.offset
    lat, lon, cap, v, alt = LAT0, LON0, 90.0, 100.0, 1000.0
    prec = None
    for i in range(1500):
        if r.random() < 0.05:
            k = r.randrange(7)
            if k == 0:
                o.pilotage = r.choice([PILOTAGE_AUTO, PILOTAGE_MANUEL]); m.offset.reinit_plan()
            elif k == 1:
                o.arrivee = (49.0 + r.uniform(-0.05, 0.05), 1.3 + r.uniform(0, 0.4)); m.offset.reinit_plan()
            elif k == 2:
                o.magnitude_m = r.choice([0.0, 2000.0, 9000.0]); m.offset.appliquer_manuel()
            elif k == 3:
                m.offset.relacher_maintien() if m.offset.maintien else m.offset.engager_maintien()
            elif k == 4:
                m.definir_mode(MODE_LIVE)
            elif k == 5:
                o.comportement = r.choice(["avion", "helico", "libre"])
            else:
                o.vertical_m = r.choice([0.0, 300.0, -150.0])
        if r.random() < 0.02:
            cap = (cap + r.choice([-90.0, 45.0, 90.0])) % 360.0
        if r.random() < 0.01:
            alt += r.choice([-300.0, 300.0])
        lat, lon, _ = geo.direct(lat, lon, cap, v)
        src.pousser(Fix(lat=lat, lon=lon, alt_m=alt, sog_ms=v, cog_deg=cap, utc=T0 + timedelta(seconds=i)))
        m.avancer(1.0)
        e = m.etat
        assert all(math.isfinite(x) for x in (e.lat, e.lon, e.alt_m))
        if prec is not None:
            assert geo.inverse(prec[0], prec[1], e.lat, e.lon).distance <= 1.3 * 120.0 + 20.0 + 1.0
            assert abs(e.alt_m - prec[2]) <= VZ_GARDE_MIN + 1e-6
        prec = (e.lat, e.lon, e.alt_m)


# --- repositionnements volontaires : fix déclaré invalide, jamais un saut en trame valide -----------
def _sim():
    p = Profil()
    p.points = [PointPassage(49.0, 1.0, 300.0, "A"), PointPassage(49.0, 1.3, 600.0, "B"),
                PointPassage(49.2, 1.3, 600.0, "C")]
    p.vitesse_ms, p.inclinaison_max_deg = 100.0, 25.0
    evts = []
    m = Moteur(p, lambda e, i: evts.append(e))
    return m, evts


def _emission_sans_saut_valide(m, n, dt=0.1):
    """Avance n tics ; vérifie qu'aucune paire de sorties VALIDES consécutives ne saute."""
    prec = None
    for _ in range(n):
        m.avancer(dt)
        e = m.etat
        if m.sans_fix_s > 0.0:
            prec = None                    # fix invalide : continuité non exigée
            continue
        if prec is not None:
            v_garde, _ = GardeFou.bornes(max(m.vitesse_ms, 120.0), m.taux_ms)
            assert geo.inverse(prec[0], prec[1], e.lat, e.lon).distance <= v_garde * dt + 0.5
        prec = (e.lat, e.lon)


def test_reinitialiser_en_vol_declare_le_fix_invalide():
    m, evts = _sim()
    m.demarrer()
    _emission_sans_saut_valide(m, 3000)                               # 300 s de vol
    m.reinitialiser(m.etat.temps)
    m.avancer(0.1)
    assert "rupture" in evts and m.sans_fix_s > 2.5                   # trame déjà invalide
    _emission_sans_saut_valide(m, 100)
    assert m.sans_fix_s == 0.0


def test_stop_puis_aller_au_point_declare_le_fix_invalide():
    m, evts = _sim()
    m.demarrer()
    _emission_sans_saut_valide(m, 2000)
    m.arreter()
    m.avancer(0.1)
    assert "rupture" not in evts                                       # Stop : position conservée
    m.sauter(2)
    m.avancer(0.1)
    assert "rupture" in evts and m.sans_fix_s > 0.0


def test_passage_simulation_vers_direct_declare_le_fix_invalide():
    m, evts = _sim()
    m.demarrer()
    _emission_sans_saut_valide(m, 300)
    m.definir_mode(MODE_LIVE)
    src = _Source()
    m.brancher_source(src)
    m.demarrer()
    src.pousser(_fix(0)); m.avancer(1.0)                              # vrai avion à 30 km de là
    assert "rupture" in evts and m.sans_fix_s > 0.0


def test_altitude_inconnue_rien_emis_puis_altitude_reelle():
    m, src = _direct()
    f = _fix(0)
    src.pousser(Fix(lat=f.lat, lon=f.lon, alt_m=None, sog_ms=100.0, cog_deg=90.0, utc=f.utc)); m.avancer(1.0)
    assert not m.sortie_prete                                          # jamais une altitude 0 inventée
    src.pousser(_fix(1, alt=3000.0)); m.avancer(1.0)
    assert m.sortie_prete and abs(m.etat.alt_m - 3000.0) < 1e-6
    f = _fix(2, alt=3000.0)
    src.pousser(Fix(lat=f.lat, lon=f.lon, alt_m=None, sog_ms=100.0, cog_deg=90.0, utc=f.utc)); m.avancer(1.0)
    assert m.sortie_prete and abs(m.etat.alt_m - 3000.0) < 1e-6      # GGA manquante : dernière connue


def test_recalage_en_A_sans_convergence_sans_saut():
    """Vrai avion plus rapide que V max : l'avion simulé décroche ; au franchissement de A, plus de
    recalage brutal (avant : saut de ~13 km) — rejointe en vol, aucune intervention du garde-fou."""
    m, src = _direct(pilotage=PILOTAGE_AUTO, magnitude_m=1852.0, vitesse_max_ms=77.0,
                     depart=geo.direct(LAT0, LON0, 90.0, 2000.0)[:2],
                     arrivee=geo.direct(LAT0, LON0, 90.0, 60000.0)[:2])
    prec = None
    vh = 0.0
    for i in range(2200):                  # 13 km de retard repris à +20 m/s (V max portée à 1,2 × V réelle)
        src.pousser(_fix(i)); m.avancer(1.0)
        e = m.etat
        if prec is not None:
            vh = max(vh, geo.inverse(prec[0], prec[1], e.lat, e.lon).distance)
        prec = (e.lat, e.lon)
    assert vh <= 1.3 * 100.0 + 20.0 and m.garde.nb_episodes == 0
    assert m.offset.suiveur.mode == "bypass"                          # rejoint au bout du compte


def test_reprise_progressive_sans_saut_d_altitude():
    m, src = _direct(magnitude_m=0.0)
    for i in range(30):
        src.pousser(_fix(i)); m.avancer(1.0)
    for k in range(40):                                                # perte 40 s ; le vrai monte de 200 m
        src.age += 1.0; m.avancer(1.0)
    alts = []
    for i in range(70, 200):
        src.pousser(_fix(i, alt=1200.0)); m.avancer(1.0)
        alts.append(m.etat.alt_m)
    assert max(abs(b - a) for a, b in zip(alts, alts[1:])) <= 5.0 + 1e-6   # VZ_REJOINTE
    assert abs(alts[-1] - 1200.0) < 1e-6 and m.garde.nb_episodes == 0


def test_chemin_lisse_continu_sur_routes_aleatoires():
    """Chemin lissé (virage d'entrée, fly-by, survol, demi-tours bornés) : deux nœuds consécutifs
    ne sont jamais plus éloignés que l'abscisse qui les sépare (+2 m) — avant : jusqu'à 457 m."""
    from simuposition.core import chemin as chm
    r = random.Random(1)
    for _ in range(300):
        p = Profil()
        n = r.randint(3, 7)
        p.points = [PointPassage(49 + r.uniform(-.05, .05), 1.2 + r.uniform(-.08, .08), None, "") for _ in range(n)]
        p.boucle = r.random() < .5
        p.inclinaison_max_deg = r.choice([25.0, -25.0, 11.0, 45.0])
        p.interpolation = r.choice([geo.ORTHODROMIE, geo.LOXODROMIE])
        cap0 = r.uniform(0, 360) if r.random() < .7 else None
        dep = (49 + r.uniform(-.05, .05), 1.2 + r.uniform(-.08, .08))
        ch = chm.construire(p, dep, r.randrange(n), r.choice([60.0, 103.0, 150.0]), tours=2, cap_initial=cap0)
        for k in range(1, len(ch.s)):
            d = geo.inverse(ch.lat[k - 1], ch.lon[k - 1], ch.lat[k], ch.lon[k], p.interpolation).distance
            assert d <= (ch.s[k] - ch.s[k - 1]) * 1.01 + 2.0
