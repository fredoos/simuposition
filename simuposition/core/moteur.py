# -*- coding: utf-8 -*-
"""
Moteur de trajectoire : intègre la position le long de la route du profil.

Indépendant de Qt : l'interface appelle `avancer(dt_reel)` à cadence fixe (100 ms) et lit
`etat`. Les événements (point atteint, fin de route…) sont remontés par un rappel optionnel.

Cinématique :
  - vitesse sol constante (modifiable à la volée, ou imposée par un point de passage) ;
  - cap recalculé à chaque pas vers le point suivant (orthodromie ou loxodromie) ;
  - virages arrondis (profil.inclinaison_max_deg > 0) : le mobile suit un chemin lissé construit
    géométriquement (core/chemin.py) — raccords circulaires tangents de rayon R = v²/(g·tan φ),
    réduits si les tronçons sont trop courts (jamais de boucle) ; le cap est continu. Inclinaison 0 =
    ancien comportement (cap instantané, passage exact sur chaque point) ;
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
from .chemin import Chemin, construire, ecart_cap, rayon_virage, G_MS2  # noqa: F401  (ré-exportés)
from .modele import Profil, PointPassage

ARRET, EN_ROUTE, PAUSE, TERMINE = "arret", "en_route", "pause", "termine"

PAS_SOUS_S = 0.5            # pas d'intégration interne max (s simulées)


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
        self._chemin: Optional[Chemin] = None    # chemin lissé en cours de suivi
        self._s = 0.0                            # abscisse courante sur le chemin
        self._ij = 0                             # prochain jalon (point de passage) à franchir
        self._sig = None                         # signature du profil au moment de la construction
        self._cap_blend = False                  # cap de sortie en rattrapage (après reconstruction)
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
        self._chemin = None
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
        self._chemin = None
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
        self._chemin = None
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
        self._chemin = None
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
        self._chemin = None
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

    def _signature(self):
        p = self.profil
        return (tuple((q.lat, q.lon, q.vitesse_ms) for q in p.points), p.boucle, p.interpolation, p.inclinaison_max_deg)

    def _construire_chemin(self, sig) -> None:
        """(Re)construit le chemin lissé depuis la position courante vers la cible courante."""
        e = self.etat
        self._chemin = construire(self.profil, (e.lat, e.lon), e.idx_cible, self.vitesse_ms, tours=20)
        self._s = 0.0
        self._ij = 0
        self._sig = sig
        # cap de départ du chemin différent du cap courant → rattrapage à taux limité (cap NMEA continu)
        self._cap_blend = abs(ecart_cap(self._chemin.cap[0], e.cap)) > 0.5 if self._chemin.cap else False

    def _avancer_position(self, h: float) -> None:
        """Avance la position de `h` secondes simulées (sous-pas)."""
        e = self.etat
        reste = e.vitesse_ms * h
        if self.profil.inclinaison_max_deg <= 0.0 or e.vitesse_ms <= 0.0 or len(self.profil.points) < 2:
            self._avancer_instantane(reste)
            return
        sig = self._signature()
        if self._chemin is None or sig != self._sig:
            self._construire_chemin(sig)
        ch = self._chemin
        if not ch.s or ch.longueur <= 0.0:
            return
        self._s += reste
        e.distance_m += reste
        # points de passage franchis (au plus près, milieu du raccord)
        while self._ij < len(ch.jalons) and self._s >= ch.jalons[self._ij][0]:
            _, idx, serre = ch.jalons[self._ij]
            self._ij += 1
            if serre is not None:
                self.rappel("virage_serre", {"idx": idx, "nom": self.profil.points[idx].nom, "incl_deg": serre})
            self._point_atteint(avancer_cap=False)
            if e.etat != EN_ROUTE:
                break
        s_pos = min(self._s, ch.longueur)
        e.lat, e.lon, cap = ch.position(s_pos)
        if self._cap_blend:
            omega = math.degrees(e.vitesse_ms / max(rayon_virage(e.vitesse_ms, self.profil.inclinaison_max_deg), 1.0))
            err = ecart_cap(cap, e.cap)
            e.cap = (e.cap + max(-omega * h, min(omega * h, err))) % 360.0
            if abs(ecart_cap(cap, e.cap)) < 0.3:
                self._cap_blend = False
        else:
            e.cap = cap
        if e.etat == EN_ROUTE and self._s >= ch.longueur:       # fin d'un chemin en boucle : on le reconstruit
            self._chemin = None

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
    """Polyligne (lat, lon) de la route telle qu'elle sera volée (virages arrondis, même géométrie
    que le moteur). Inclinaison nulle ou moins de 3 points : tracé droit."""
    pts = profil.points
    if len(pts) < 3 or profil.inclinaison_max_deg <= 0 or profil.vitesse_ms <= 0:
        return profil.trace(pas_m)
    ch = construire(profil, (pts[0].lat, pts[0].lon), 1, profil.vitesse_ms, tours=1, pas_m=pas_m)
    return list(zip(ch.lat, ch.lon)) if ch.lat else profil.trace(pas_m)
