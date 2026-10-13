# -*- coding: utf-8 -*-
"""
Moteur de trajectoire : intègre la position le long de la route du profil.

Indépendant de Qt : l'interface appelle `avancer(dt_reel)` à cadence fixe (100 ms) et lit
`etat`. Les événements (point atteint, fin de route…) sont remontés par un rappel optionnel.

Cinématique :
  - vitesse sol constante (modifiable à la volée, ou imposée par un point de passage) ;
  - cap recalculé à chaque pas vers le point suivant (orthodromie ou loxodromie) ;
  - virages arrondis (profil.inclinaison_max_deg > 0) : passage « fly-by » — le mobile amorce
    le virage avant le point (distance R·tan(Δcap/2)) et tourne à taux constant ω = g·tan(φ)/v
    (rayon R = v²/(g·tan φ)) ; le cap est donc continu. Inclinaison 0 = ancien comportement
    (cap instantané, passage exact sur chaque point) ;
  - altitude : rejoint l'altitude cible (celle du prochain point qui en définit une) au taux
    vertical maximal, de façon linéaire ;
  - facteur de temps (×1 … ×100) pour accélérer la simulation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import geodesie as geo
from .modele import Profil, PointPassage

ARRET, EN_ROUTE, PAUSE, TERMINE = "arret", "en_route", "pause", "termine"

G_MS2 = 9.80665
PAS_SOUS_S = 0.5            # pas d'intégration interne max (s simulées) — le cap évolue à chaque sous-pas
INCLINAISON_MAX_DEG = 80.0  # borne de sécurité (tan φ)


def rayon_virage(vitesse_ms: float, inclinaison_deg: float) -> float:
    """Rayon de virage en palier stabilisé R = v²/(g·tan φ) ; 0 si virage instantané."""
    phi = math.radians(min(max(inclinaison_deg, 0.0), INCLINAISON_MAX_DEG))
    if phi < 1e-4 or vitesse_ms <= 0:
        return 0.0
    return vitesse_ms * vitesse_ms / (G_MS2 * math.tan(phi))


def ecart_cap(a: float, b: float) -> float:
    """Écart signé a − b ramené dans ]−180, 180]."""
    return (a - b + 540.0) % 360.0 - 180.0


@dataclass
class EtatSim:
    lat: float = 49.0287
    lon: float = 1.2199
    alt_m: float = 100.0            # altitude MSL
    cap: float = 0.0                # route vraie (°)
    vitesse_ms: float = 0.0         # vitesse sol courante (0 à l'arrêt)
    vz_ms: float = 0.0              # vitesse verticale courante
    temps: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    distance_m: float = 0.0         # distance parcourue depuis Démarrer
    idx_cible: int = 0              # index du prochain point de passage
    etat: str = ARRET
    alt_cible_m: Optional[float] = None


class Moteur:
    """Simulateur de déplacement le long d'un profil."""

    def __init__(self, profil: Profil, rappel: Optional[Callable[[str, dict], None]] = None):
        self.profil = profil
        self.rappel = rappel or (lambda evt, info: None)
        self.facteur_temps = 1.0
        self._d_switch = 0.0                     # distance parcourue depuis le dernier changement de cible
        self._d_ref = 0.0                        # distance à la cible à ce moment (garde-fou anti-orbite)
        self.pas_sous_s = PAS_SOUS_S             # pas d'intégration interne (réglable : tracé lissé grossier)
        self.vitesse_ms = profil.vitesse_ms      # consigne courante (peut être changée en vol)
        self.taux_ms = profil.taux_ms            # taux vertical max courant
        self.etat = EtatSim()
        self.reinitialiser()

    # ---- commandes -----------------------------------------------------------------
    def reinitialiser(self, temps: Optional[datetime] = None) -> None:
        """Place le mobile au premier point (ou position actuelle du profil) à l'arrêt."""
        e = self.etat
        if self.profil.points:
            p0 = self.profil.points[0]
            e.lat, e.lon = p0.lat, p0.lon
            e.alt_m = p0.alt_m if p0.alt_m is not None else self.profil.alt_initiale_m
        else:
            e.alt_m = self.profil.alt_initiale_m
        e.cap = self._cap_vers(1) if len(self.profil.points) > 1 else 0.0
        e.vitesse_ms = 0.0
        e.vz_ms = 0.0
        e.distance_m = 0.0
        e.idx_cible = 1 if len(self.profil.points) > 1 else 0
        e.alt_cible_m = None
        e.etat = ARRET
        e.temps = temps or datetime.now(timezone.utc)
        self.vitesse_ms = self.profil.vitesse_ms
        self.taux_ms = self.profil.taux_ms
        self.rappel("reinitialise", {})

    def placer(self, lat: float, lon: float, alt_m: Optional[float] = None) -> None:
        """Position fixe arbitraire (mode statique / saut direct)."""
        e = self.etat
        e.lat, e.lon = lat, lon
        if alt_m is not None:
            e.alt_m = alt_m
        e.vitesse_ms = 0.0
        e.vz_ms = 0.0
        e.etat = ARRET
        self.rappel("place", {"lat": lat, "lon": lon})

    def demarrer(self) -> bool:
        """Lance (ou relance) le déplacement vers le prochain point. False si rien à suivre."""
        e = self.etat
        if len(self.profil.points) < 2:
            self.rappel("erreur", {"msg": "il faut au moins deux points de passage"})
            return False
        if e.etat == TERMINE:
            self.reinitialiser(e.temps)
        if e.idx_cible >= len(self.profil.points):
            e.idx_cible = len(self.profil.points) - 1
        e.etat = EN_ROUTE
        e.vitesse_ms = self.vitesse_ms
        self._appliquer_consignes_point(e.idx_cible)
        self._d_switch = 0.0
        cible = self.profil.points[e.idx_cible]
        self._d_ref = geo.inverse(e.lat, e.lon, cible.lat, cible.lon, self.profil.interpolation).distance
        self.rappel("demarre", {"cible": e.idx_cible})
        return True

    def basculer_pause(self) -> None:
        e = self.etat
        if e.etat == EN_ROUTE:
            e.etat = PAUSE
            e.vitesse_ms = 0.0
            e.vz_ms = 0.0
            self.rappel("pause", {})
        elif e.etat == PAUSE:
            e.etat = EN_ROUTE
            e.vitesse_ms = self.vitesse_ms
            self.rappel("reprise", {})

    def arreter(self) -> None:
        """Arrêt sur place : la position courante est conservée, vitesse nulle."""
        e = self.etat
        e.etat = ARRET
        e.vitesse_ms = 0.0
        e.vz_ms = 0.0
        self.rappel("arret", {})

    def sauter(self, idx: int) -> None:
        """Saute directement sur le point `idx` ; la cible devient idx+1."""
        pts = self.profil.points
        if not (0 <= idx < len(pts)):
            return
        e = self.etat
        p = pts[idx]
        e.lat, e.lon = p.lat, p.lon
        if p.alt_m is not None:
            e.alt_m = p.alt_m
        e.idx_cible = idx + 1 if idx + 1 < len(pts) else (0 if self.profil.boucle else idx)
        e.alt_cible_m = None
        if e.etat == EN_ROUTE:
            self._appliquer_consignes_point(e.idx_cible)
        e.cap = self._cap_vers(e.idx_cible) if len(pts) > 1 else e.cap
        self.rappel("saut", {"idx": idx})

    def regler_vitesse(self, vitesse_ms: float) -> None:
        self.vitesse_ms = max(0.0, vitesse_ms)
        if self.etat.etat == EN_ROUTE:
            self.etat.vitesse_ms = self.vitesse_ms

    def regler_taux(self, taux_ms: float) -> None:
        self.taux_ms = max(0.0, taux_ms)

    def regler_alt_cible(self, alt_m: Optional[float]) -> None:
        """Altitude cible immédiate (None = conserver l'altitude courante)."""
        self.etat.alt_cible_m = alt_m

    # ---- intégration ---------------------------------------------------------------
    def avancer(self, dt_reel: float) -> EtatSim:
        """Fait progresser la simulation de `dt_reel` secondes réelles (× facteur de temps)."""
        e = self.etat
        dt = dt_reel * self.facteur_temps
        e.temps += timedelta(seconds=dt)
        if e.etat != EN_ROUTE:
            return e
        restant = dt
        while restant > 1e-9 and e.etat == EN_ROUTE:
            h = min(self.pas_sous_s, restant)
            restant -= h
            self._avancer_position(h)
        # altitude : rejoint la cible au taux maximal
        if e.alt_cible_m is not None and abs(e.alt_cible_m - e.alt_m) > 1e-6:
            pas = self.taux_ms * dt
            if e.alt_cible_m > e.alt_m:
                e.alt_m = min(e.alt_cible_m, e.alt_m + pas)
                e.vz_ms = self.taux_ms
            else:
                e.alt_m = max(e.alt_cible_m, e.alt_m - pas)
                e.vz_ms = -self.taux_ms
            if abs(e.alt_cible_m - e.alt_m) <= 1e-6:
                e.vz_ms = 0.0
                self.rappel("altitude_atteinte", {"alt_m": e.alt_m})
        else:
            e.vz_ms = 0.0
        return e

    # ---- interne -------------------------------------------------------------------
    def _suivant(self, idx: int) -> Optional[int]:
        """Index du point qui suit `idx` dans la route (None après le dernier hors boucle)."""
        n = len(self.profil.points)
        if idx + 1 < n:
            return idx + 1
        return 0 if (self.profil.boucle and n > 1) else None

    def _avancer_position(self, h: float) -> None:
        """Avance la position de `h` secondes simulées (sous-pas)."""
        e = self.etat
        pts = self.profil.points
        mode = self.profil.interpolation
        reste = e.vitesse_ms * h
        R = rayon_virage(e.vitesse_ms, self.profil.inclinaison_max_deg)
        if R <= 0.0:
            self._avancer_instantane(reste)
            return
        # 1) changement de point cible : amorce du virage à R·tan(Δ/2) avant le point (fly-by)
        for _ in range(50):
            if e.idx_cible >= len(pts):
                return
            cible = pts[e.idx_cible]
            inv = geo.inverse(e.lat, e.lon, cible.lat, cible.lon, mode)
            suiv = self._suivant(e.idx_cible)
            if suiv is None:                                  # dernier point : on s'y pose
                if inv.distance <= reste:
                    e.lat, e.lon = cible.lat, cible.lon
                    e.distance_m += inv.distance
                    self._point_atteint()
                    return
                break
            sortie = geo.inverse(cible.lat, cible.lon, pts[suiv].lat, pts[suiv].lon, mode)
            delta = abs(ecart_cap(sortie.cap_depart, e.cap))
            seuil = R * math.tan(math.radians(min(delta, 170.0)) / 2.0)
            seuil = min(seuil, 0.5 * sortie.distance)
            err_c = abs(ecart_cap(inv.cap_depart, e.cap))
            # (1) amorce normale du virage ; (2) point « dépassé » (derrière, dans le cercle de virage) ;
            # (3) garde-fou anti-orbite : trop de distance parcourue sans atteindre la cible
            depasse = err_c > 90.0 and inv.distance < 2.0 * R
            orbite = self._d_switch > 3.0 * self._d_ref + 4.0 * math.pi * R
            if inv.distance <= max(seuil, reste) or depasse or orbite:
                self._point_atteint(avancer_cap=False)        # le virage se fait via le contrôleur de cap
                continue
            break
        if e.idx_cible >= len(pts) or e.etat != EN_ROUTE:
            return
        cible = pts[e.idx_cible]
        inv = geo.inverse(e.lat, e.lon, cible.lat, cible.lon, mode)
        # 2) contrôleur de cap à taux limité ω = v/R
        err = ecart_cap(inv.cap_depart, e.cap)
        omega = math.degrees(e.vitesse_ms / R)               # °/s
        if self._suivant(e.idx_cible) is None and inv.distance < R and abs(err) > 90.0:
            pas_cap = err                                      # cas dégénéré (point dans le cercle de virage)
        else:
            pas_cap = max(-omega * h, min(omega * h, err))
        cap_moy = geo.normaliser_cap(e.cap + pas_cap / 2.0)
        la, lo, _ = geo.direct(e.lat, e.lon, cap_moy, reste, mode)
        e.lat, e.lon = la, lo
        e.cap = geo.normaliser_cap(e.cap + pas_cap)
        e.distance_m += reste
        self._d_switch += reste

    def _avancer_instantane(self, reste: float) -> None:
        """Ancien comportement : cap instantané, passage exact sur chaque point."""
        e = self.etat
        pts = self.profil.points
        for _ in range(50):                # garde-fou : au plus 50 points franchis par pas
            if e.idx_cible >= len(pts):
                break
            cible = pts[e.idx_cible]
            inv = geo.inverse(e.lat, e.lon, cible.lat, cible.lon, self.profil.interpolation)
            if inv.distance <= reste:
                e.lat, e.lon = cible.lat, cible.lon
                e.distance_m += inv.distance
                reste -= inv.distance
                self._point_atteint()
                if e.etat != EN_ROUTE:
                    break
                continue
            la, lo, _ = geo.direct(e.lat, e.lon, inv.cap_depart, reste, self.profil.interpolation)
            e.lat, e.lon = la, lo
            e.cap = inv.cap_depart
            e.distance_m += reste
            break

    def _cap_vers(self, idx: int) -> float:
        pts = self.profil.points
        if not (0 <= idx < len(pts)):
            return self.etat.cap
        e = self.etat
        return geo.inverse(e.lat, e.lon, pts[idx].lat, pts[idx].lon, self.profil.interpolation).cap_depart

    def _appliquer_consignes_point(self, idx: int) -> None:
        """Consignes (vitesse, taux, altitude) attachées au segment menant au point idx."""
        pts = self.profil.points
        if not (0 <= idx < len(pts)):
            return
        p = pts[idx]
        if p.vitesse_ms is not None:
            self.vitesse_ms = p.vitesse_ms
            self.etat.vitesse_ms = p.vitesse_ms
        if p.taux_ms is not None:
            self.taux_ms = p.taux_ms
        if p.alt_m is not None:
            self.etat.alt_cible_m = p.alt_m

    def _point_atteint(self, avancer_cap: bool = True) -> None:
        e = self.etat
        pts = self.profil.points
        idx = e.idx_cible
        self.rappel("point_atteint", {"idx": idx, "nom": pts[idx].nom})
        if idx + 1 < len(pts):
            e.idx_cible = idx + 1
        elif self.profil.boucle and len(pts) > 1:
            e.idx_cible = 0
        else:
            e.etat = TERMINE
            e.vitesse_ms = 0.0
            e.vz_ms = 0.0
            self.rappel("termine", {})
            return
        self._appliquer_consignes_point(e.idx_cible)
        self._d_switch = 0.0
        cible = pts[e.idx_cible]
        self._d_ref = geo.inverse(e.lat, e.lon, cible.lat, cible.lon, self.profil.interpolation).distance
        if avancer_cap:
            e.cap = self._cap_vers(e.idx_cible)

    # ---- informations ---------------------------------------------------------------
    def distance_restante_m(self) -> float:
        """Distance jusqu'à la fin de la route (depuis la position courante)."""
        pts = self.profil.points
        e = self.etat
        if e.idx_cible >= len(pts) or e.etat == TERMINE:
            return 0.0
        d = geo.inverse(e.lat, e.lon, pts[e.idx_cible].lat, pts[e.idx_cible].lon, self.profil.interpolation).distance
        for a, b in zip(pts[e.idx_cible:], pts[e.idx_cible + 1:]):
            d += geo.inverse(a.lat, a.lon, b.lat, b.lon, self.profil.interpolation).distance
        return d

    def temps_restant_s(self) -> float:
        v = self.vitesse_ms
        return self.distance_restante_m() / v if v > 0 else math.inf

    def progression(self) -> float:
        total = self.profil.longueur_m()
        return 1.0 if total <= 0 else max(0.0, min(1.0, 1.0 - self.distance_restante_m() / total))


# --- Tracé lissé (affichage carte) ------------------------------------------------------
def trace_lissee(profil: Profil, pas_m: float = 2000.0) -> list[tuple[float, float]]:
    """Polyligne (lat, lon) de la route telle qu'elle sera réellement volée (virages arrondis).

    Simule la route entière avec le même moteur que le vol (cap à taux limité, amorce des virages) ;
    le pas d'intégration s'adapte pour rester ≤ ~3000 pas, donc le calcul reste rapide sur les longues
    routes. Si l'inclinaison est nulle (ou < 3 points), retourne le tracé droit."""
    pts = profil.points
    n = len(pts)
    v0 = profil.vitesse_ms
    if n < 3 or profil.inclinaison_max_deg <= 0 or v0 <= 0:
        return profil.trace(pas_m)
    boucle = profil.boucle
    longueur = profil.longueur_m()
    if boucle:
        longueur += geo.inverse(pts[-1].lat, pts[-1].lon, pts[0].lat, pts[0].lon, profil.interpolation).distance
    R = rayon_virage(v0, profil.inclinaison_max_deg)
    omega = math.degrees(v0 / R)                          # °/s
    pas = min(20.0, max(0.5, 3.0 / omega, longueur / v0 / 3000.0))
    m = Moteur(profil)
    m.pas_sous_s = pas
    evts: list[int] = []
    m.rappel = lambda e, i: evts.append(1) if e == "point_atteint" else None
    if not m.demarrer():
        return profil.trace(pas_m)
    trace = [(m.etat.lat, m.etat.lon)]
    for _ in range(8000):
        m.avancer(pas)
        trace.append((m.etat.lat, m.etat.lon))
        if m.etat.etat != EN_ROUTE or (boucle and len(evts) >= n):
            break
    if not boucle:
        trace.append((pts[-1].lat, pts[-1].lon))
    return trace
