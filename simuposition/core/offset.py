# -*- coding: utf-8 -*-
"""
Moteur d'offset (mode Direct) : la trace émise est la trace GPS réelle TRANSLATÉE d'un vecteur.

Principe
--------
- Le décalage est un vecteur (est, nord, haut) FIXE dans le repère terrestre, orienté une seule
  fois au déclenchement : repère de la barrière D en pilotage Auto, cap sol au moment de la
  commande en pilotage Manuel. Il ne pivote pas avec les virages du vrai avion : l'avion simulé
  parcourt la trace réelle décalée de XX, avec exactement les mêmes cap et vitesse en croisière.
  (Un offset recalculé sur le cap instantané faisait tourner le point décalé autour du vrai avion
  à chaque virage : à 9 NM, un changement de cap de 60° déplaçait l'avion simulé de 9 NM.)
- Chaque transition (séparation, retrouvaille, commande manuelle) est répartie sur une DISTANCE L
  parcourue par le vrai avion, la plus courte qui respecte à la fois :
    · l'angle d'interception θ (écart de route de l'avion simulé) : L ≥ 1,5·|Δlatéral| / tan θ ;
    · les vitesses limites de l'avion simulé V min ≤ V sim ≤ V max (contraintes de l'avion) ;
      V sim = v · |(1 + ρ·Δlong/L , ρ·Δlat/L)| avec ρ = S'(x) ∈ [0 ; 1,5] le long de la rampe ;
      L est cherchée par dichotomie (contrainte monotone en 1/L) ;
    · une pente verticale maximale : L ≥ 1,5·|Δvertical| / pente_max.
  La durée en découle (L / v). Forme smoothstep : au début et à la fin de chaque transition, cap
  et vitesse de l'avion simulé sont égaux à ceux du vrai avion. Les longueurs sont calculées
  avec la vitesse réelle du moment (retrouvaille : recalculée jusqu'à son engagement) ; si la
  vitesse réelle elle-même sort de [V min ; V max], seule la contrainte d'angle s'applique et
  l'alerte « vitesse_limite » est émise. En croisière, l'avion simulé a la vitesse réelle.

Pilotage AUTO : barrières
-------------------------
- D et A sont des barrières : segments perpendiculaires à un cap de passage, de demi-largeur w,
  centrés sur la balise. Une barrière ne compte que franchie dans le sens du cap de passage.
  Cap de passage par défaut : axe D→A (l'interface le remplace par la tangente à la route quand
  la balise est accrochée au trajet) ; réglable.
- Franchissement de D → séparation : k = S(s / L_sep), s = distance parcourue depuis la barrière.
- Retrouvaille : k = S(x_A / L_arr), x_A = distance restant jusqu'à la ligne A mesurée le long du
  cap de passage de A (le vrai avion doit être devant la barrière : |travers| ≤ w + x_A).
  k = 0 EXACTEMENT sur la ligne : position, cap et vitesse simulés = réels au point de
  franchissement, où qu'il soit sur la barrière.
- Les deux contraintes se combinent : k = min(S_sep, S_arr). Barrières trop rapprochées → offset
  partiel sans jamais dépasser θ (alerte « barrieres_proches »).
- Après A : réarmement au prochain franchissement de D.

Pilotage MANUEL
---------------
Appliquer / Annuler / ± : transition du vecteur courant vers le vecteur visé sur la distance L.
À l'arrêt ou au sol (vitesse < V_MIN_MANUEL) la transition progresse comme à V_MIN_MANUEL, pour
rester testable avec un GPS immobile.

Comportement
------------
Le vecteur ci-dessus est un PLAN. L'avion simulé le suit avec la dynamique du comportement choisi
(avion / hélico / translation exacte) : voir core/comportement.py (vitesses, virages, accélération,
hippodrome quand le GPS réel est trop lent). Au franchissement de la barrière A, l'avion simulé est
recalé sur le vrai avion.

Offset nul ≡ bypass (trace émise = trace réelle).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from . import geodesie as geo
from .comportement import Suiveur, LimitesVehicule, COMPORTEMENT_HELICO
from .modele import (ConfigOffset, OFFSET_LATERAL, OFFSET_LONGITUDINAL, PILOTAGE_AUTO)

EPS_M = 0.5                     # en deçà, l'offset est considéré nul (≡ bypass)
PENTE_VERTICALE_MAX = 0.05      # 5 % (≈ 2,9°) : pente max ajoutée par une transition verticale
V_MIN_MANUEL = 10.0             # m/s : progression minimale d'une transition manuelle (GPS immobile)
L_MIN_M = 1.0                   # longueur minimale d'une transition (m)


def smoothstep(x: float) -> float:
    """Transition douce S(x) = 3x² − 2x³ sur [0, 1] (dérivée nulle en 0 et 1, pente max 1,5)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)


# ---- vecteurs (est, nord, haut) en mètres ------------------------------------------------
Vecteur = tuple  # (e, n, u)
NUL: Vecteur = (0.0, 0.0, 0.0)


def _combiner(a: Vecteur, b: Vecteur, t: float) -> Vecteur:
    """a + t·(b − a)."""
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2]))


def _norme_h(v: Vecteur) -> float:
    return math.hypot(v[0], v[1])


def decomposer(v: Vecteur, cap_deg: float) -> tuple[float, float]:
    """(longitudinal, latéral) du vecteur horizontal dans le repère d'un cap (latéral + = droite)."""
    c = math.radians(cap_deg)
    lon_ = v[0] * math.sin(c) + v[1] * math.cos(c)
    lat_ = v[0] * math.cos(c) - v[1] * math.sin(c)
    return lon_, lat_


def vecteur_offset(config: ConfigOffset, cap_ref_deg: float) -> Vecteur:
    """Vecteur d'offset complet de la configuration, dans le repère du cap de référence."""
    m = max(0.0, config.magnitude_m)
    if config.type_offset == OFFSET_LATERAL:
        az = cap_ref_deg + (90.0 if config.signe >= 0 else -90.0)
    elif config.type_offset == OFFSET_LONGITUDINAL:
        az = cap_ref_deg if config.signe >= 0 else cap_ref_deg + 180.0
    else:                                                # relèvement fixe (absolu)
        az = config.relevement_deg
    a = math.radians(az)
    return (m * math.sin(a), m * math.cos(a), config.vertical_m)


_N_ECH = 24                     # échantillons du taux ρ ∈ [0 ; 1,5] (extrêmes de vitesse et d'angle)


def _profil_transition(delta: Vecteur, cap_ref_deg: float, longueur_m: float) -> tuple[float, float, float]:
    """(facteur de vitesse min, max, écart de route max en °) de l'avion simulé sur la transition.

    Vitesse simulée / réelle = |(1 + ρ·Δlong/L , ρ·Δlat/L)| où ρ = S'(x) parcourt [0 ; 1,5] ;
    l'écart de route est l'angle de ce vecteur avec la route réelle (> 90° = marche arrière)."""
    lon_, lat_ = decomposer(delta, cap_ref_deg)
    u = 1.0 / max(longueur_m, L_MIN_M)
    f_min, f_max, dev = float("inf"), 0.0, 0.0
    for i in range(_N_ECH + 1):
        r = 1.5 * i / _N_ECH
        al, tr = 1.0 + r * lon_ * u, r * lat_ * u
        f = math.hypot(al, tr)
        f_min, f_max = min(f_min, f), max(f_max, f)
        dev = max(dev, math.degrees(math.atan2(abs(tr), al)))
    return f_min, f_max, dev


def facteur_vitesse(delta: Vecteur, cap_ref_deg: float, longueur_m: float) -> tuple[float, float]:
    """(min, max) du rapport vitesse simulée / vitesse réelle sur toute la transition."""
    f_min, f_max, _ = _profil_transition(delta, cap_ref_deg, longueur_m)
    return f_min, f_max


def longueur_transition(delta: Vecteur, cap_ref_deg: float, angle_deg: float, v_ms: float = 0.0,
                        vmin_ms: float = 0.0, vmax_ms: float = 0.0) -> tuple[float, bool]:
    """Distance (m) la plus courte sur laquelle répartir la variation `delta`, et si les vitesses
    limites sont tenues.

    Contraintes : écart de route de l'avion simulé ≤ angle d'interception (ce qui interdit aussi la
    marche arrière), V min ≤ V sim ≤ V max (si la vitesse réelle et une limite sont connues),
    pente verticale ≤ PENTE_VERTICALE_MAX. Renvoie (L, faisable) ; faisable = False si la vitesse
    réelle est elle-même hors limites : seules l'angle et la pente s'appliquent alors."""
    theta = max(1.0, min(80.0, angle_deg))
    L0 = max(L_MIN_M, 1.5 * abs(delta[2]) / PENTE_VERTICALE_MAX)
    if _norme_h(delta) <= EPS_M:
        return L0, True
    faisable = True
    q_min, q_max = 0.0, float("inf")
    if v_ms > 0.1 and (vmin_ms > 0.0 or vmax_ms > 0.0):
        q_min = vmin_ms / v_ms if vmin_ms > 0.0 else 0.0
        q_max = vmax_ms / v_ms if vmax_ms > 0.0 else float("inf")
        if not (q_min <= 1.0 <= q_max):                  # le vrai avion est déjà hors limites
            faisable, q_min, q_max = False, 0.0, float("inf")

    def tenu(longueur: float) -> bool:
        f_min, f_max, dev = _profil_transition(delta, cap_ref_deg, longueur)
        return dev <= theta + 1e-9 and f_min >= q_min - 1e-9 and f_max <= q_max + 1e-9

    if tenu(L0):
        return L0, faisable
    bas, haut = L0, L0 * 2.0
    while not tenu(haut):                                # borne haute : on double jusqu'à tenir
        bas, haut = haut, haut * 2.0
        if haut > 1e9:
            return haut, False
    for _ in range(40):                                  # dichotomie (contrainte monotone en L)
        mil = 0.5 * (bas + haut)
        if tenu(mil):
            haut = mil
        else:
            bas = mil
    return haut, faisable


# ---- barrière ------------------------------------------------------------------------------
@dataclass
class Barriere:
    """Segment perpendiculaire au cap de passage, centré sur (lat, lon), de demi-largeur w."""
    lat: float
    lon: float
    cap_deg: float
    demi_largeur_m: float

    def repere(self, lat: float, lon: float, mode: str) -> tuple[float, float]:
        """(avance, travers) du point : avance > 0 = au-delà de la barrière dans le sens du passage,
        travers > 0 = à droite du cap de passage."""
        inv = geo.inverse(self.lat, self.lon, lat, lon, mode)
        if inv.distance <= 0.0:
            return 0.0, 0.0
        a = math.radians(inv.cap_depart - self.cap_deg)
        return inv.distance * math.cos(a), inv.distance * math.sin(a)

    def franchie(self, prec: tuple[float, float], cour: tuple[float, float]) -> bool:
        """Franchissement dans le sens du passage, à l'intérieur de la barrière, entre deux reperes."""
        a0, t0 = prec
        a1, t1 = cour
        if not (a0 < 0.0 <= a1):
            return False
        t = t0 + (t1 - t0) * (-a0) / (a1 - a0)
        return abs(t) <= self.demi_largeur_m

    def extremites(self, mode: str) -> tuple[tuple[float, float], tuple[float, float]]:
        g = geo.direct(self.lat, self.lon, self.cap_deg - 90.0, self.demi_largeur_m, mode)
        d = geo.direct(self.lat, self.lon, self.cap_deg + 90.0, self.demi_largeur_m, mode)
        return (g[0], g[1]), (d[0], d[1])


@dataclass
class SortieOffset:
    lat: float
    lon: float
    alt_m: float
    magnitude_m: float          # magnitude horizontale réellement appliquée
    bearing_deg: float
    phase: str


class MoteurOffset:
    """Calcule la position décalée époque par époque, selon le pilotage choisi."""

    def __init__(self, config: ConfigOffset, mode: str = geo.ORTHODROMIE,
                 rappel: Optional[Callable[[str, dict], None]] = None):
        self.config = config
        self.mode = mode
        self.rappel = rappel or (lambda e, i: None)
        self.reinit()

    def reinit(self) -> None:
        self.phase = "avant"            # avant | separation | croisiere | retrouvaille | apres
        self._V: Vecteur = NUL          # vecteur visé par le plan
        self.suiveur = Suiveur()        # dynamique de l'avion simulé
        self.W_effectif: Vecteur = NUL  # vecteur réellement appliqué (après dynamique)
        self._prec_pos: Optional[tuple] = None
        self._v = 0.0                   # dernière vitesse sol connue (m/s)
        self._cog = 0.0                 # dernier cap sol connu (°)
        # AUTO
        self._V_plein: Vecteur = NUL
        self._L_sep = L_MIN_M
        self._L_arr = L_MIN_M
        self._s = 0.0                   # distance parcourue depuis le franchissement de D
        self._arr_engagee = False       # retrouvaille engagée (longueur figée)
        self._alerte_arr = False
        self._prec_rep: Optional[tuple] = None   # (repère D, repère A) de l'époque précédente
        self._sig_barrieres: Optional[tuple] = None
        self.k = 0.0                    # fraction d'offset appliquée (auto)
        self.avant_D: Optional[float] = None     # distance restant jusqu'à la ligne D (m, lecture UI)
        self.avant_A: Optional[float] = None     # distance restant jusqu'à la ligne A (m, lecture UI)
        # MANUEL
        self._V0: Vecteur = NUL
        self._V1: Vecteur = NUL
        self._L_man = L_MIN_M
        self._s_man = 0.0
        self._cap_man = 0.0
        self.magnitude_courante = 0.0   # magnitude horizontale appliquée au dernier calcul (lecture UI)

    # ---- barrières (pilotage auto) -----------------------------------------------------
    def caps_barrieres(self) -> Optional[tuple[float, float]]:
        """Caps de passage effectifs de D et A (réglés, sinon axe D→A), None si D ou A absent."""
        c = self.config
        if not c.depart or not c.arrivee:
            return None
        inv = geo.inverse(c.depart[0], c.depart[1], c.arrivee[0], c.arrivee[1], self.mode)
        cap_d = c.cap_depart_deg if c.cap_depart_deg is not None else inv.cap_depart
        cap_a = c.cap_arrivee_deg if c.cap_arrivee_deg is not None else inv.cap_arrivee
        return cap_d % 360.0, cap_a % 360.0

    def barrieres(self) -> Optional[tuple[Barriere, Barriere]]:
        caps = self.caps_barrieres()
        if caps is None:
            return None
        c = self.config
        w = max(1.0, c.demi_largeur_m)
        return (Barriere(c.depart[0], c.depart[1], caps[0], w),
                Barriere(c.arrivee[0], c.arrivee[1], caps[1], w))

    def plan(self, vitesse_ms: Optional[float] = None) -> Optional[dict]:
        """Prévision des transitions auto pour l'interface : longueurs, durées à la vitesse donnée,
        facteur de vitesse simulée, et si la distance D→A suffit pour établir l'offset complet."""
        b = self.barrieres()
        if b is None:
            return None
        c = self.config
        bd, ba = b
        V = vecteur_offset(c, bd.cap_deg)
        moins_V = (-V[0], -V[1], -V[2])
        v = vitesse_ms if vitesse_ms is not None else self._v
        l_sep, ok_sep = self._longueur(V, bd.cap_deg, v)
        l_arr, ok_arr = self._longueur(moins_V, ba.cap_deg, v)
        d_da = geo.inverse(bd.lat, bd.lon, ba.lat, ba.lon, self.mode).distance
        f1, f2 = facteur_vitesse(V, bd.cap_deg, l_sep)
        f3, f4 = facteur_vitesse(moins_V, ba.cap_deg, l_arr)
        return {"l_sep": l_sep, "l_arr": l_arr, "d_da": d_da, "suffisant": l_sep + l_arr <= d_da,
                "t_sep": l_sep / v if v > 0.1 else None, "t_arr": l_arr / v if v > 0.1 else None,
                "f_min": min(f1, f3), "f_max": max(f2, f4), "v": v, "limites_ok": ok_sep and ok_arr}

    def _longueur(self, delta: Vecteur, cap_deg: float, v_ms: float) -> tuple[float, bool]:
        lim = self._limites()                            # hélico : V min = 0
        return longueur_transition(delta, cap_deg, self.config.angle_interception_deg, v_ms,
                                   lim.vmin_ms, lim.vmax_ms)

    # ---- commandes du pilotage manuel --------------------------------------------------
    def _lancer_manuel(self, cible: Vecteur) -> None:
        c = self.config
        depart = (self.suiveur.W[0], self.suiveur.W[1], self._V[2])   # on repart d'où est l'avion simulé
        self._V0, self._V1 = depart, cible
        delta = (cible[0] - depart[0], cible[1] - depart[1], cible[2] - depart[2])
        self._L_man, ok = self._longueur(delta, self._cog, self._v)
        if not ok:
            self.rappel("vitesse_limite", {"v": self._v, "vmin": c.vitesse_min_ms, "vmax": c.vitesse_max_ms})
        self._s_man = 0.0
        self._cap_man = self._cog

    def appliquer_manuel(self) -> None:
        """Transition vers l'offset de la configuration, orienté sur le cap sol courant."""
        self._lancer_manuel(vecteur_offset(self.config, self._cog))

    def annuler_manuel(self) -> None:
        """Transition vers 0 (retour sur la trace réelle)."""
        self._lancer_manuel(NUL)

    def consigne_manuelle_m(self) -> float:
        """Magnitude horizontale visée par la commande manuelle en cours (lecture UI)."""
        return _norme_h(self._V1)

    def longueur_manuelle_m(self) -> float:
        return self._L_man

    # ---- calcul d'une époque -----------------------------------------------------------
    def calculer(self, fix, dt_s: float) -> SortieOffset:
        """Position décalée pour le `fix` reçu ; `dt_s` = temps réel écoulé depuis l'époque précédente."""
        c = self.config
        if fix.sog_ms is not None and fix.sog_ms > 0.1:
            self._v = fix.sog_ms
        if fix.cog_deg is not None and (fix.sog_ms is None or fix.sog_ms > 0.5):
            self._cog = fix.cog_deg
        ds, d_reel = 0.0, (0.0, 0.0)
        if self._prec_pos is not None:
            inv = geo.inverse(self._prec_pos[0], self._prec_pos[1], fix.lat, fix.lon, self.mode)
            ds = inv.distance
            az = math.radians(inv.cap_depart)
            d_reel = (ds * math.sin(az), ds * math.cos(az))
        self._prec_pos = (fix.lat, fix.lon)
        phase_prec = self.phase

        if c.pilotage == PILOTAGE_AUTO:
            k = self._maj_auto(fix, ds)
            self._V = (k * self._V_plein[0], k * self._V_plein[1], k * self._V_plein[2])
            phase = self.phase
        else:
            self._s_man += max(ds, V_MIN_MANUEL * max(0.0, dt_s))
            t = smoothstep(self._s_man / self._L_man)
            self._V = self._V1 if t >= 1.0 else _combiner(self._V0, self._V1, t)
            phase = "manuel"

        # dynamique de l'avion simulé (suiveur) ; recalage exact au franchissement de A
        self.suiveur.limites = self._limites()
        dt = dt_s if dt_s > 1e-3 else 1.0
        if c.pilotage == PILOTAGE_AUTO and self.phase == "apres" and phase_prec != "apres":
            self.suiveur.recaler((d_reel[0] / dt, d_reel[1] / dt))
            W = (0.0, 0.0)
        else:
            W = self.suiveur.avancer(d_reel, dt, (self._V[0], self._V[1]), self._tau_A(d_reel, dt))
        self.W_effectif = (W[0], W[1], self._V[2])
        mag = math.hypot(W[0], W[1])
        bearing = math.degrees(math.atan2(W[0], W[1])) % 360.0
        if mag <= EPS_M:
            lat, lon = fix.lat, fix.lon
        else:
            lat, lon, _ = geo.direct(fix.lat, fix.lon, bearing, mag, self.mode)
        alt = (fix.alt_m if fix.alt_m is not None else 0.0) + self._V[2]
        self.magnitude_courante = mag
        return SortieOffset(lat, lon, alt, mag, bearing, phase)

    def _limites(self) -> LimitesVehicule:
        c = self.config
        helico = c.comportement == COMPORTEMENT_HELICO
        return LimitesVehicule(comportement=c.comportement,
                               vmin_ms=0.0 if helico else c.vitesse_min_ms,
                               vmax_ms=c.vitesse_max_ms if c.vitesse_max_ms > 0.0 else 1e4,
                               inclinaison_deg=c.inclinaison_max_deg, taux_virage_dps=c.taux_virage_max_dps,
                               accel_ms2=c.accel_max_ms2, hippodrome=c.hippodrome and not helico)

    def _tau_A(self, d_reel: Vecteur, dt: float) -> Optional[float]:
        """Temps (s) avant le franchissement de la ligne A par le vrai avion, à sa vitesse actuelle
        projetée sur le cap de passage de A (None hors pilotage auto actif ou si elle n'approche pas)."""
        if self.config.pilotage != PILOTAGE_AUTO or self.phase not in ("separation", "croisiere", "retrouvaille"):
            return None
        caps = self.caps_barrieres()
        if caps is None or self.avant_A is None or self.avant_A <= 0.0:
            return None
        ca = math.radians(caps[1])
        v_axe = (d_reel[0] * math.sin(ca) + d_reel[1] * math.cos(ca)) / dt
        return self.avant_A / v_axe if v_axe > 0.3 else None

    # ---- pilotage AUTO (barrières) ------------------------------------------------------
    def _maj_auto(self, fix, ds: float) -> float:
        c = self.config
        b = self.barrieres()
        if b is None:
            self.phase, self.k, self._prec_rep = "avant", 0.0, None
            self.avant_D = self.avant_A = None
            return 0.0
        bd, ba = b
        sig = (bd.lat, bd.lon, round(bd.cap_deg, 3), ba.lat, ba.lon, round(ba.cap_deg, 3), bd.demi_largeur_m)
        if sig != self._sig_barrieres:                   # barrières déplacées : on repart de « avant »
            self._sig_barrieres = sig
            self.phase, self.k, self._prec_rep = "avant", 0.0, None
        rd = bd.repere(fix.lat, fix.lon, self.mode)
        ra = ba.repere(fix.lat, fix.lon, self.mode)
        prec, self._prec_rep = self._prec_rep, (rd, ra)
        self.avant_D, self.avant_A = -rd[0], -ra[0]

        if self.phase in ("avant", "apres"):
            if prec is not None and bd.franchie(prec[0], rd):
                self._demarrer_auto(bd, ba, max(0.0, rd[0]))
            else:
                self.k = 0.0
                return 0.0
        else:
            if prec is not None and ba.franchie(prec[1], ra):
                self.phase, self.k = "apres", 0.0
                self.rappel("barriere", {"quel": "A"})
                return 0.0
            self._s += ds

        k_sep = smoothstep(self._s / self._L_sep)
        x_a, travers_a = -ra[0], ra[1]
        if not self._arr_engagee:                        # longueur de retrouvaille à la vitesse du moment
            self._L_arr, ok = self._longueur((-self._V_plein[0], -self._V_plein[1], -self._V_plein[2]),
                                             ba.cap_deg, self._v)
            if not ok and not self._alerte_arr:
                self._alerte_arr = True
                self.rappel("vitesse_limite", {"v": self._v, "vmin": c.vitesse_min_ms, "vmax": c.vitesse_max_ms})
        if 0.0 <= x_a <= self._L_arr and abs(travers_a) <= ba.demi_largeur_m + x_a:
            self._arr_engagee = True                     # longueur figée jusqu'à la ligne A
            k_arr = smoothstep(x_a / self._L_arr)        # 0 pile sur la ligne A
        else:
            k_arr = 1.0
        self.k = min(k_sep, k_arr)
        if self.k >= 1.0:
            self.phase = "croisiere"
        elif k_arr < k_sep:
            self.phase = "retrouvaille"
        else:
            self.phase = "separation"
        return self.k

    def _demarrer_auto(self, bd: Barriere, ba: Barriere, s0: float) -> None:
        """Franchissement de la barrière D : fige le vecteur d'offset dans le repère de D."""
        c = self.config
        self._V_plein = vecteur_offset(c, bd.cap_deg)
        self._L_sep, ok = self._longueur(self._V_plein, bd.cap_deg, self._v)
        if not ok:
            self.rappel("vitesse_limite", {"v": self._v, "vmin": c.vitesse_min_ms, "vmax": c.vitesse_max_ms})
        self._L_arr, _ = self._longueur((-self._V_plein[0], -self._V_plein[1], -self._V_plein[2]), ba.cap_deg, self._v)
        self._arr_engagee = False
        self._alerte_arr = False
        self._s = s0
        self.phase = "separation"
        self.rappel("barriere", {"quel": "D"})
        d_da = geo.inverse(bd.lat, bd.lon, ba.lat, ba.lon, self.mode).distance
        if self._L_sep + self._L_arr > d_da:
            self.rappel("barrieres_proches", {"d_da": d_da, "l_sep": self._L_sep, "l_arr": self._L_arr})
