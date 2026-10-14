# -*- coding: utf-8 -*-
"""
Comportement de l'avion simulé (mode Direct) : suiveur à dynamique de véhicule.

Le moteur d'offset (core/offset.py) calcule un PLAN : le décalage visé W_p(t) (vecteur est/nord
par rapport à la position GPS réelle). Ce module fait voler l'avion simulé vers le point visé
T = réel + W_p en respectant les limites d'un véhicule, ce qui garde un comportement réaliste
quand l'ordre change en cours de route (Appliquer / Annuler / ± / inversion) ou quand le GPS réel
fait quelque chose que l'avion simulé ne peut pas faire (stagner, aller trop vite, virer trop serré).

Comportements
-------------
- LIBRE (« translation exacte ») : W = W_p, aucune dynamique (copie décalée pure de la trace réelle).
- AVION : V min ≤ V ≤ V max (jamais à l'arrêt, jamais en marche arrière), taux de virage
  ≤ g·tan φ / V (inclinaison max φ), accélération le long de la route ≤ a max. Une cible derrière
  impose un virage. Si le point visé avance plus lentement que V min : circuit d'attente en
  HIPPODROME attaché au point visé (voir plus bas).
- HÉLICO : 0 ≤ V ≤ V max (stationnaire possible), accélération vectorielle ≤ a max ; au-dessus de
  V_TRANSLATION, taux de virage ≤ taux max ; en dessous, translation libre dans toutes les directions.

Poursuite (hors hippodrome)
---------------------------
Vitesse désirée  v_d = v_T + û·min(|e|/τ, √(2·a·|e|))   avec e = W_p − W, v_T = vitesse du point visé
(anticipation) ; la racine borne la vitesse de rapprochement pour pouvoir freiner à temps (pas de
dépassement). v_d est ensuite contrainte par le véhicule (vitesse, virage, accélération).
Intégration en sous-pas de PAS_S entre deux époques GPS ; W_p interpolé linéairement.

Hippodrome (avion)
------------------
Repère mobile lié au point visé (il avance à v_T). Circuit : virage de 180° (rayon
R = V_h²/(g·tan φ)) du côté opposé au vrai avion, branche d'éloignement de longueur ℓ, virage de
180°, branche de rapprochement de longueur ℓ qui se termine au repère (position relative au point
visé à l'entrée). Vitesse relative V_h = V min + |v_T| (vitesse sol ≥ V min) bornée par V max.
Branches standard : 1 min. La première boucle ramène progressivement le repère sur le point visé.
- Dès que |v_T| ≥ V min·(1 + MARGE_SORTIE) (le GPS réel a repris une vitesse d'avion) : sortie,
  retour à la poursuite.
- À chaque passage au repère, si le temps τ_A avant le franchissement de la barrière A par le vrai
  avion est connu : si τ_A est plus court qu'une boucle minimale → sortie (poursuite directe) ;
  si τ_A tient en moins de deux boucles standard → BOUCLE FINALE : branche d'éloignement recalée en
  continu (ℓ = (τ_A·V_h + σ − 2πR)/2) puis vitesse ajustée (distance restante / τ_A, dans les
  bornes de vitesse et d'inclinaison) pour arriver au repère en même temps que le vrai avion.
Le vrai avion franchissant A, le moteur d'offset recale l'avion simulé sur lui (recaler()).

Bypass : plan nul et suiveur convergé → W = 0, vitesse = vitesse réelle (trace émise = réelle).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

COMPORTEMENT_AVION = "avion"
COMPORTEMENT_HELICO = "helico"
COMPORTEMENT_LIBRE = "libre"
COMPORTEMENTS = (COMPORTEMENT_AVION, COMPORTEMENT_HELICO, COMPORTEMENT_LIBRE)

G = 9.80665
PAS_S = 0.1                     # sous-pas d'intégration (s)
TAU_AVION_S = 8.0               # constante de rappel de position (s)
TAU_HELICO_S = 5.0
V_TRANSLATION = 10.0            # m/s (~20 kt) : en dessous, l'hélico se translate librement
MARGE_SORTIE = 0.05             # sortie d'hippodrome quand le point visé dépasse V min de 5 %
BRANCHE_STD_S = 60.0            # branches d'hippodrome standard : 1 minute
CONVERGE_M = 1.0                # convergence vers le plan (retour au bypass)
CONVERGE_MS = 0.5

Vec = tuple  # (est, nord) en m ou m/s


def _norme(v: Vec) -> float:
    return math.hypot(v[0], v[1])


def _cap(v: Vec, defaut: float = 0.0) -> float:
    """Cap (rad, depuis le nord, sens horaire) d'un vecteur ; `defaut` si nul."""
    return math.atan2(v[0], v[1]) if _norme(v) > 1e-9 else defaut


def _polaire(norme: float, cap: float) -> Vec:
    return (norme * math.sin(cap), norme * math.cos(cap))


def _ecart_angle(a: float, b: float) -> float:
    """a − b ramené dans ]−π ; π]."""
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


def _borne(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


@dataclass
class LimitesVehicule:
    comportement: str = COMPORTEMENT_AVION
    vmin_ms: float = 25.72          # 50 kt
    vmax_ms: float = 77.17          # 150 kt
    inclinaison_deg: float = 30.0   # avion : taux de virage = g·tan φ / V
    taux_virage_dps: float = 20.0   # hélico, au-dessus de V_TRANSLATION
    accel_ms2: float = 1.0
    hippodrome: bool = True


class Suiveur:
    """Fait voler l'avion simulé vers le point visé, époque GPS par époque GPS."""

    def __init__(self, limites: Optional[LimitesVehicule] = None):
        self.limites = limites or LimitesVehicule()
        self.recaler()

    # ---- état -------------------------------------------------------------------------
    def recaler(self, v_reel: Vec = (0.0, 0.0)) -> None:
        """Avion simulé confondu avec le vrai (bypass) : W = 0, même vitesse."""
        self.W: Vec = (0.0, 0.0)            # position relative au vrai avion (m)
        self.vs: Vec = v_reel               # vitesse sol de l'avion simulé (m/s)
        self.mode = "bypass"                # bypass | poursuite | hippodrome
        self._Wp_prec: Optional[Vec] = None
        self._vt_prec: Vec = v_reel
        # hippodrome
        self.boucles = 0
        self._h: Optional[dict] = None

    # ---- une époque ---------------------------------------------------------------------
    def avancer(self, d_reel: Vec, dt: float, Wp: Vec, tau_A: Optional[float] = None) -> Vec:
        """Fait avancer l'avion simulé de `dt` secondes.

        d_reel : déplacement du vrai avion depuis l'époque précédente (est, nord, m) ;
        Wp     : décalage visé par le plan à cette époque ;
        tau_A  : temps (s) avant le franchissement de la barrière A par le vrai avion (None = inconnu).
        Renvoie le décalage effectif W (est, nord) à appliquer à la position réelle."""
        lim = self.limites
        Wp_prec = self._Wp_prec if self._Wp_prec is not None else Wp
        self._Wp_prec = Wp
        if dt <= 1e-6:
            return self.W
        vr = (d_reel[0] / dt, d_reel[1] / dt)
        if lim.comportement == COMPORTEMENT_LIBRE:
            self.W, self.vs, self.mode = Wp, vr, ("bypass" if _norme(Wp) <= 0.5 else "poursuite")
            return self.W
        plan_nul = _norme(Wp) <= 0.5 and _norme(Wp_prec) <= 0.5
        if self.mode == "bypass":
            self.vs = vr                                    # confondu avec le vrai avion jusque-là
            if plan_nul:
                return self.W
            self.mode = "poursuite"
        dWp = ((Wp[0] - Wp_prec[0]) / dt, (Wp[1] - Wp_prec[1]) / dt)
        vt = (vr[0] + dWp[0], vr[1] + dWp[1])              # vitesse sol du point visé
        n = max(1, min(600, int(math.ceil(dt / PAS_S))))
        h = dt / n
        for i in range(n):
            f = (i + 1) / n
            Wp_i = (Wp_prec[0] + f * (Wp[0] - Wp_prec[0]), Wp_prec[1] + f * (Wp[1] - Wp_prec[1]))
            if self.mode == "hippodrome":
                self._pas_hippodrome(Wp_i, vt, vr, h, tau_A - (i * h) if tau_A is not None else None)
            else:
                self._pas_poursuite(Wp_i, vt, vr, h)
        # retour au bypass : plan nul et avion simulé revenu sur la trace réelle
        if plan_nul and self.mode == "poursuite" and _norme(self.W) <= CONVERGE_M \
                and _norme((self.vs[0] - vr[0], self.vs[1] - vr[1])) <= CONVERGE_MS:
            self.recaler(vr)
        return self.W

    # ---- poursuite ----------------------------------------------------------------------
    def _pas_poursuite(self, Wp: Vec, vt: Vec, vr: Vec, h: float) -> None:
        lim = self.limites
        e = (Wp[0] - self.W[0], Wp[1] - self.W[1])
        de = _norme(e)
        tau = TAU_HELICO_S if lim.comportement == COMPORTEMENT_HELICO else TAU_AVION_S
        v_rap = min(de / tau, math.sqrt(2.0 * max(lim.accel_ms2, 0.05) * de)) if de > 1e-6 else 0.0
        vd = (vt[0] + (e[0] / de) * v_rap if de > 1e-6 else vt[0],
              vt[1] + (e[1] / de) * v_rap if de > 1e-6 else vt[1])
        if lim.comportement == COMPORTEMENT_HELICO:
            self.vs = self._dyn_helico(vd, h)
        else:
            self.vs = self._dyn_avion(vd, h)
            if lim.hippodrome and _norme(vt) < lim.vmin_ms * (1.0 - MARGE_SORTIE) \
                    and de <= 2.0 * self._rayon(self._v_hippo(vt)) + 200.0:
                self._entrer_hippodrome(Wp, vt)
        self.W = (self.W[0] + (self.vs[0] - vr[0]) * h, self.W[1] + (self.vs[1] - vr[1]) * h)

    def _dyn_avion(self, vd: Vec, h: float) -> Vec:
        """Vitesse bornée [V min ; V max], virage ≤ g·tan φ / V, accélération ≤ a."""
        lim = self.limites
        s = _norme(self.vs)
        c = _cap(self.vs, _cap(vd))
        sd = _borne(_norme(vd), lim.vmin_ms, lim.vmax_ms)
        cd = _cap(vd, c)
        omega = G * math.tan(math.radians(_borne(lim.inclinaison_deg, 1.0, 80.0))) / max(s, 5.0)
        c2 = c + _borne(_ecart_angle(cd, c), -omega * h, omega * h)
        s2 = s + _borne(sd - s, -lim.accel_ms2 * h, lim.accel_ms2 * h)
        s2 = min(s2, max(lim.vmax_ms, s))                 # pas d'accélération au-delà de V max
        return _polaire(max(0.0, s2), c2)

    def _dyn_helico(self, vd: Vec, h: float) -> Vec:
        """Accélération vectorielle ≤ a, vitesse ≤ V max ; virage limité au-dessus de V_TRANSLATION."""
        lim = self.limites
        dv = (vd[0] - self.vs[0], vd[1] - self.vs[1])
        ndv = _norme(dv)
        amax = lim.accel_ms2 * h
        if ndv > amax:
            dv = (dv[0] * amax / ndv, dv[1] * amax / ndv)
        v2 = (self.vs[0] + dv[0], self.vs[1] + dv[1])
        s2 = _norme(v2)
        if s2 > lim.vmax_ms:
            v2 = (v2[0] * lim.vmax_ms / s2, v2[1] * lim.vmax_ms / s2)
            s2 = lim.vmax_ms
        s = _norme(self.vs)
        if s > V_TRANSLATION and s2 > V_TRANSLATION:      # vol de translation : virage coordonné limité
            c = _cap(self.vs)
            om = math.radians(max(1.0, lim.taux_virage_dps)) * h
            c2 = c + _borne(_ecart_angle(_cap(v2, c), c), -om, om)
            v2 = _polaire(s2, c2)
        return v2

    # ---- hippodrome ---------------------------------------------------------------------
    def _v_hippo(self, vt: Vec) -> float:
        lim = self.limites
        return min(lim.vmax_ms, lim.vmin_ms + _norme(vt))

    def _rayon(self, v: float) -> float:
        return v * v / (G * math.tan(math.radians(_borne(self.limites.inclinaison_deg, 1.0, 80.0))))

    def _entrer_hippodrome(self, Wp: Vec, vt: Vec) -> None:
        """Circuit attaché au point visé : repère = position relative actuelle, axe = route relative
        actuelle, virages du côté opposé au vrai avion (W relatif au plan, sinon à droite)."""
        rel = (self.vs[0] - vt[0], self.vs[1] - vt[1])
        axe = _cap(rel, _cap(self.vs))
        # côté : celui où se trouve le point visé par rapport au vrai avion (on s'en éloigne)
        lat = Wp[0] * math.cos(axe) - Wp[1] * math.sin(axe)      # composante à droite de l'axe
        cote = 1.0 if lat >= 0.0 else -1.0
        vh = self._v_hippo(vt)
        self._h = {"q0": (self.W[0] - Wp[0], self.W[1] - Wp[1]), "axe": axe, "cote": cote,
                   "vh": vh, "R": self._rayon(vh), "branche": vh * BRANCHE_STD_S, "sigma": 0.0,
                   "final": False}
        self.mode = "hippodrome"
        self.boucles = 0

    def _position_boucle(self, sigma: float) -> tuple[float, float, float]:
        """Position (x le long de l'axe, y côté virage) et cap relatif (rad) sur la boucle."""
        H = self._h
        R, l = H["R"], H["branche"]
        if sigma < math.pi * R:                                 # 1er virage (180°)
            t = sigma / R
            return R * math.sin(t), R * (1.0 - math.cos(t)), t
        sigma -= math.pi * R
        if sigma < l:                                           # branche d'éloignement
            return -sigma, 2.0 * R, math.pi
        sigma -= l
        if sigma < math.pi * R:                                 # 2e virage (180°)
            t = sigma / R
            return -l - R * math.sin(t), R + R * math.cos(t), math.pi + t
        sigma -= math.pi * R                                    # branche de rapprochement
        return -l + min(sigma, l), 0.0, 0.0

    def _vitesse_boucle(self, vt: Vec, tau_A: Optional[float]) -> float:
        """Vitesse relative (repère mobile) sur la boucle au point courant.

        Bornes : vitesse sol dans [V min ; V max] compte tenu de la vitesse v_T du point visé et de
        la direction locale, et en virage vitesse sol ≤ √(g·R·tan φ) (inclinaison max). Hors boucle
        finale : vitesse sol = V min (dérive minimale). Boucle finale : distance restante / τ_A,
        pour arriver au repère en même temps que le vrai avion franchit la barrière A."""
        H, lim = self._h, self.limites
        R = H["R"]
        _, _, c_rel = self._position_boucle(H["sigma"])
        cap = H["axe"] + H["cote"] * c_rel
        u = (math.sin(cap), math.cos(cap))
        vt_u = vt[0] * u[0] + vt[1] * u[1]
        vt_p = abs(vt[0] * u[1] - vt[1] * u[0])

        def relative(v_sol: float) -> float:              # vitesse relative donnant cette vitesse sol
            return -vt_u + math.sqrt(max(0.0, v_sol * v_sol - vt_p * vt_p))
        vh_min = max(0.5, relative(lim.vmin_ms))
        v_sol_max = lim.vmax_ms
        sig = H["sigma"]
        en_virage = sig < math.pi * R or math.pi * R + H["branche"] <= sig < 2.0 * math.pi * R + H["branche"]
        if en_virage:
            v_sol_max = min(v_sol_max, math.sqrt(G * R * math.tan(math.radians(_borne(lim.inclinaison_deg, 1.0, 80.0)))))
        vh_max = max(vh_min, relative(v_sol_max))
        if H.get("final") and tau_A is not None and tau_A > 0.05:
            reste = 2.0 * math.pi * R + 2.0 * H["branche"] - sig
            return _borne(reste / tau_A, vh_min, vh_max)
        return vh_min

    def _pas_hippodrome(self, Wp: Vec, vt: Vec, vr: Vec, h: float, tau_A: Optional[float]) -> None:
        H = self._h
        lim = self.limites
        if _norme(vt) >= lim.vmin_ms * (1.0 + MARGE_SORTIE):
            # le point visé a repris une vitesse d'avion : sortie immédiate, la poursuite rejoint
            # (attendre la fin de boucle obligerait à des vitesses relatives irréalistes)
            self.mode, self._h = "poursuite", None
            self._pas_poursuite(Wp, vt, vr, h)
            return
        H["vh"] = self._vitesse_boucle(vt, tau_A)
        H["sigma"] += H["vh"] * h
        perimetre = 2.0 * math.pi * H["R"] + 2.0 * H["branche"]
        if H["sigma"] >= perimetre:                             # passage au repère : décision
            H["sigma"] -= perimetre
            self.boucles += 1
            H["final"] = False
            if tau_A is not None and tau_A > 0.0:
                v_nom = self._v_hippo(vt)
                t_min = 2.0 * math.pi * H["R"] / v_nom
                if tau_A < t_min:                                # plus le temps d'une boucle : on rejoint
                    self.mode, self._h = "poursuite", None
                    self._pas_poursuite(Wp, vt, vr, h)
                    return
                if tau_A < t_min + 2.0 * 2.0 * BRANCHE_STD_S:     # dernière boucle calée sur τ_A
                    H["final"] = True
                else:
                    H["branche"] = v_nom * BRANCHE_STD_S
        if H["final"] and tau_A is not None and tau_A > 0.0:
            # recalage continu de la branche d'éloignement tant qu'elle n'est pas terminée :
            # distance restant à parcourir sur la boucle = τ_A · V_h → arrivée au repère avec le vrai avion
            R, sig = H["R"], H["sigma"]
            l_vise = max(0.0, (tau_A * self._v_hippo(vt) + sig - 2.0 * math.pi * R) / 2.0)
            if sig < math.pi * R:
                H["branche"] = l_vise
            elif sig < math.pi * R + H["branche"]:
                H["branche"] = max(l_vise, sig - math.pi * R)
        x, y, c_rel = self._position_boucle(H["sigma"])
        a, s = H["axe"], H["cote"]
        ux, uy = (math.sin(a), math.cos(a)), (s * math.cos(a), -s * math.sin(a))   # axe, côté virage
        # repère : position relative d'entrée, ramenée progressivement sur le point visé pendant la
        # première boucle (la boucle se referme alors exactement sur le point visé)
        perimetre = 2.0 * math.pi * H["R"] + 2.0 * H["branche"]
        fondu = max(0.0, 1.0 - H["sigma"] / perimetre) if self.boucles == 0 else 0.0
        rep_ = (H["q0"][0] * fondu, H["q0"][1] * fondu)
        q = (rep_[0] + x * ux[0] + y * uy[0], rep_[1] + x * ux[1] + y * uy[1])
        cap = a + s * c_rel
        W_nouveau = (Wp[0] + q[0], Wp[1] + q[1])
        self.vs = (vt[0] + H["vh"] * math.sin(cap), vt[1] + H["vh"] * math.cos(cap))
        self.W = W_nouveau
