# -*- coding: utf-8 -*-
"""
Géodésie WGS84 pour SimuPosition.

Deux modes d'interpolation entre points de passage :
  - orthodromie (grande-cercle / géodésique) : Vincenty inverse + direct sur l'ellipsoïde
    WGS84, repli sphérique si Vincenty ne converge pas (points quasi antipodaux) ;
  - loxodromie (rhumb line) : cap constant, calcul sur la sphère (erreur < 0,5 % sur les
    distances courantes, largement suffisant pour une trajectoire simulée).

Toutes les latitudes/longitudes sont en degrés décimaux, les distances en mètres,
les caps en degrés [0, 360).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# --- Ellipsoïde WGS84 -------------------------------------------------------------
A = 6_378_137.0                 # demi-grand axe (m)
F = 1 / 298.257_223_563         # aplatissement
B = A * (1 - F)                 # demi-petit axe (m)
R_MOYEN = 6_371_008.8           # rayon moyen (m) pour les calculs sphériques

# --- Unités -----------------------------------------------------------------------
NM_M = 1852.0                   # 1 mille nautique en mètres
FT_M = 0.3048                   # 1 pied en mètres
KT_MS = NM_M / 3600.0           # 1 nœud en m/s
KMH_MS = 1000.0 / 3600.0        # 1 km/h en m/s


def normaliser_cap(deg: float) -> float:
    """Ramène un cap dans [0, 360)."""
    return deg % 360.0


def normaliser_lon(deg: float) -> float:
    """Ramène une longitude dans [-180, 180)."""
    return (deg + 180.0) % 360.0 - 180.0


# ==================================================================================
# Orthodromie — Vincenty (WGS84)
# ==================================================================================
@dataclass(frozen=True)
class Inverse:
    distance: float      # m
    cap_depart: float    # ° (azimut initial)
    cap_arrivee: float   # ° (azimut final)


def vincenty_inverse(lat1: float, lon1: float, lat2: float, lon2: float,
                     tol: float = 1e-12, max_iter: int = 200) -> Inverse:
    """Distance géodésique et azimuts entre deux points (Vincenty, 1975).

    Lève ValueError si l'itération ne converge pas (cas quasi antipodal) ; l'appelant
    peut alors se replier sur `spherique_inverse`.
    """
    if abs(lat1 - lat2) < 1e-12 and abs(normaliser_lon(lon1 - lon2)) < 1e-12:
        return Inverse(0.0, 0.0, 0.0)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    L = math.radians(normaliser_lon(lon2 - lon1))
    U1 = math.atan((1 - F) * math.tan(phi1))
    U2 = math.atan((1 - F) * math.tan(phi2))
    sinU1, cosU1 = math.sin(U1), math.cos(U1)
    sinU2, cosU2 = math.sin(U2), math.cos(U2)
    lam = L
    for _ in range(max_iter):
        sinlam, coslam = math.sin(lam), math.cos(lam)
        sin_sigma = math.hypot(cosU2 * sinlam, cosU1 * sinU2 - sinU1 * cosU2 * coslam)
        if sin_sigma == 0.0:
            return Inverse(0.0, 0.0, 0.0)
        cos_sigma = sinU1 * sinU2 + cosU1 * cosU2 * coslam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cosU1 * cosU2 * sinlam / sin_sigma
        cos2_alpha = 1.0 - sin_alpha * sin_alpha
        cos_2sigma_m = cos_sigma - 2.0 * sinU1 * sinU2 / cos2_alpha if cos2_alpha != 0.0 else 0.0
        C = F / 16.0 * cos2_alpha * (4.0 + F * (4.0 - 3.0 * cos2_alpha))
        lam_prev = lam
        lam = L + (1.0 - C) * F * sin_alpha * (
            sigma + C * sin_sigma * (cos_2sigma_m + C * cos_sigma * (-1.0 + 2.0 * cos_2sigma_m ** 2)))
        if abs(lam - lam_prev) < tol:
            break
    else:
        raise ValueError("Vincenty inverse : non convergence")
    u2 = cos2_alpha * (A * A - B * B) / (B * B)
    k1 = (math.sqrt(1.0 + u2) - 1.0) / (math.sqrt(1.0 + u2) + 1.0)
    Acoef = (1.0 + 0.25 * k1 * k1) / (1.0 - k1)
    Bcoef = k1 * (1.0 - 3.0 / 8.0 * k1 * k1)
    delta_sigma = Bcoef * sin_sigma * (cos_2sigma_m + Bcoef / 4.0 * (
        cos_sigma * (-1.0 + 2.0 * cos_2sigma_m ** 2)
        - Bcoef / 6.0 * cos_2sigma_m * (-3.0 + 4.0 * sin_sigma ** 2) * (-3.0 + 4.0 * cos_2sigma_m ** 2)))
    s = B * Acoef * (sigma - delta_sigma)
    alpha1 = math.atan2(cosU2 * sinlam, cosU1 * sinU2 - sinU1 * cosU2 * coslam)
    alpha2 = math.atan2(cosU1 * sinlam, -sinU1 * cosU2 + cosU1 * sinU2 * coslam)
    return Inverse(s, normaliser_cap(math.degrees(alpha1)), normaliser_cap(math.degrees(alpha2)))


def vincenty_direct(lat1: float, lon1: float, cap: float, distance: float,
                    tol: float = 1e-12, max_iter: int = 200) -> tuple[float, float, float]:
    """Point d'arrivée (lat, lon, cap final) depuis (lat1, lon1), cap initial et distance."""
    if distance == 0.0:
        return lat1, lon1, normaliser_cap(cap)
    phi1 = math.radians(lat1)
    alpha1 = math.radians(cap)
    sin_alpha1, cos_alpha1 = math.sin(alpha1), math.cos(alpha1)
    tanU1 = (1 - F) * math.tan(phi1)
    cosU1 = 1.0 / math.sqrt(1.0 + tanU1 * tanU1)
    sinU1 = tanU1 * cosU1
    sigma1 = math.atan2(tanU1, cos_alpha1)
    sin_alpha = cosU1 * sin_alpha1
    cos2_alpha = 1.0 - sin_alpha * sin_alpha
    u2 = cos2_alpha * (A * A - B * B) / (B * B)
    k1 = (math.sqrt(1.0 + u2) - 1.0) / (math.sqrt(1.0 + u2) + 1.0)
    Acoef = (1.0 + 0.25 * k1 * k1) / (1.0 - k1)
    Bcoef = k1 * (1.0 - 3.0 / 8.0 * k1 * k1)
    sigma = distance / (B * Acoef)
    for _ in range(max_iter):
        cos_2sigma_m = math.cos(2.0 * sigma1 + sigma)
        sin_sigma, cos_sigma = math.sin(sigma), math.cos(sigma)
        delta_sigma = Bcoef * sin_sigma * (cos_2sigma_m + Bcoef / 4.0 * (
            cos_sigma * (-1.0 + 2.0 * cos_2sigma_m ** 2)
            - Bcoef / 6.0 * cos_2sigma_m * (-3.0 + 4.0 * sin_sigma ** 2) * (-3.0 + 4.0 * cos_2sigma_m ** 2)))
        sigma_prev = sigma
        sigma = distance / (B * Acoef) + delta_sigma
        if abs(sigma - sigma_prev) < tol:
            break
    sin_sigma, cos_sigma = math.sin(sigma), math.cos(sigma)
    cos_2sigma_m = math.cos(2.0 * sigma1 + sigma)
    tmp = sinU1 * sin_sigma - cosU1 * cos_sigma * cos_alpha1
    phi2 = math.atan2(sinU1 * cos_sigma + cosU1 * sin_sigma * cos_alpha1,
                      (1 - F) * math.sqrt(sin_alpha * sin_alpha + tmp * tmp))
    lam = math.atan2(sin_sigma * sin_alpha1, cosU1 * cos_sigma - sinU1 * sin_sigma * cos_alpha1)
    C = F / 16.0 * cos2_alpha * (4.0 + F * (4.0 - 3.0 * cos2_alpha))
    L = lam - (1.0 - C) * F * sin_alpha * (
        sigma + C * sin_sigma * (cos_2sigma_m + C * cos_sigma * (-1.0 + 2.0 * cos_2sigma_m ** 2)))
    lon2 = normaliser_lon(lon1 + math.degrees(L))
    alpha2 = math.atan2(sin_alpha, -tmp)
    return math.degrees(phi2), lon2, normaliser_cap(math.degrees(alpha2))


# ==================================================================================
# Sphère (repli et loxodromie)
# ==================================================================================
def spherique_inverse(lat1: float, lon1: float, lat2: float, lon2: float) -> Inverse:
    """Distance (haversine) et azimuts initial/final sur la sphère de rayon R_MOYEN."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(normaliser_lon(lon2 - lon1))
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2.0 * R_MOYEN * math.asin(min(1.0, math.sqrt(a)))
    cap1 = math.atan2(math.sin(dl) * math.cos(p2),
                      math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))
    cap2 = math.atan2(math.sin(-dl) * math.cos(p1),
                      math.cos(p2) * math.sin(p1) - math.sin(p2) * math.cos(p1) * math.cos(-dl))
    return Inverse(d, normaliser_cap(math.degrees(cap1)), normaliser_cap(math.degrees(cap2) + 180.0))


def spherique_direct(lat1: float, lon1: float, cap: float, distance: float) -> tuple[float, float, float]:
    """Point d'arrivée sur la sphère (lat, lon, cap final)."""
    p1 = math.radians(lat1)
    l1 = math.radians(lon1)
    th = math.radians(cap)
    d = distance / R_MOYEN
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(th))
    l2 = l1 + math.atan2(math.sin(th) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    # cap final = azimut inverse + 180
    cap_fin = math.atan2(math.sin(l1 - l2) * math.cos(p1),
                         math.cos(p2) * math.sin(p1) - math.sin(p2) * math.cos(p1) * math.cos(l1 - l2))
    return math.degrees(p2), normaliser_lon(math.degrees(l2)), normaliser_cap(math.degrees(cap_fin) + 180.0)


def loxodromie_inverse(lat1: float, lon1: float, lat2: float, lon2: float) -> Inverse:
    """Distance et cap constant (loxodromie) sur la sphère."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(normaliser_lon(lon2 - lon1))
    dpsi = math.log(math.tan(math.pi / 4 + p2 / 2) / math.tan(math.pi / 4 + p1 / 2)) if p1 != p2 else 0.0
    q = (p2 - p1) / dpsi if abs(dpsi) > 1e-12 else math.cos(p1)
    d = math.sqrt((p2 - p1) ** 2 + (q * dl) ** 2) * R_MOYEN
    cap = normaliser_cap(math.degrees(math.atan2(dl, dpsi)))
    return Inverse(d, cap, cap)


def loxodromie_direct(lat1: float, lon1: float, cap: float, distance: float) -> tuple[float, float, float]:
    """Point d'arrivée en suivant un cap constant sur la sphère."""
    p1, l1 = math.radians(lat1), math.radians(lon1)
    th = math.radians(cap)
    delta = distance / R_MOYEN
    p2 = p1 + delta * math.cos(th)
    # garde-fou : on ne dépasse pas les pôles
    p2 = max(-math.pi / 2 + 1e-9, min(math.pi / 2 - 1e-9, p2))
    dpsi = math.log(math.tan(math.pi / 4 + p2 / 2) / math.tan(math.pi / 4 + p1 / 2))
    q = (p2 - p1) / dpsi if abs(dpsi) > 1e-12 else math.cos(p1)
    l2 = l1 + delta * math.sin(th) / q
    return math.degrees(p2), normaliser_lon(math.degrees(l2)), normaliser_cap(cap)


# ==================================================================================
# Façade : interpolation selon le mode
# ==================================================================================
ORTHODROMIE = "orthodromie"
LOXODROMIE = "loxodromie"


def inverse(lat1: float, lon1: float, lat2: float, lon2: float, mode: str = ORTHODROMIE) -> Inverse:
    """Distance et caps entre deux points selon le mode choisi."""
    if mode == LOXODROMIE:
        return loxodromie_inverse(lat1, lon1, lat2, lon2)
    try:
        return vincenty_inverse(lat1, lon1, lat2, lon2)
    except ValueError:
        return spherique_inverse(lat1, lon1, lat2, lon2)


def direct(lat1: float, lon1: float, cap: float, distance: float, mode: str = ORTHODROMIE) -> tuple[float, float, float]:
    """Point atteint depuis (lat1, lon1) au cap donné après `distance` mètres."""
    if mode == LOXODROMIE:
        return loxodromie_direct(lat1, lon1, cap, distance)
    try:
        return vincenty_direct(lat1, lon1, cap, distance)
    except (ValueError, ZeroDivisionError):
        return spherique_direct(lat1, lon1, cap, distance)


def point_intermediaire(lat1: float, lon1: float, lat2: float, lon2: float, fraction: float,
                        mode: str = ORTHODROMIE) -> tuple[float, float, float]:
    """Point situé à `fraction` (0..1) du trajet entre deux points, avec le cap local."""
    inv = inverse(lat1, lon1, lat2, lon2, mode)
    return direct(lat1, lon1, inv.cap_depart, inv.distance * fraction, mode)


def echantillonner(lat1: float, lon1: float, lat2: float, lon2: float, pas_m: float = 2000.0,
                   mode: str = ORTHODROMIE) -> list[tuple[float, float]]:
    """Liste de points (lat, lon) le long du segment pour le tracé sur la carte."""
    inv = inverse(lat1, lon1, lat2, lon2, mode)
    n = max(1, int(inv.distance // pas_m))
    pts = [(lat1, lon1)]
    for i in range(1, n):
        la, lo, _ = direct(lat1, lon1, inv.cap_depart, inv.distance * i / n, mode)
        pts.append((la, lo))
    pts.append((lat2, lon2))
    return pts


# ==================================================================================
# Conversions ECEF / ENU (utilisées par le module constellation)
# ==================================================================================
E2 = F * (2 - F)  # excentricité au carré


def geodesique_vers_ecef(lat: float, lon: float, h: float) -> tuple[float, float, float]:
    """(lat°, lon°, hauteur ellipsoïdale m) → ECEF (m)."""
    p, l = math.radians(lat), math.radians(lon)
    sp, cp = math.sin(p), math.cos(p)
    n = A / math.sqrt(1 - E2 * sp * sp)
    return (n + h) * cp * math.cos(l), (n + h) * cp * math.sin(l), (n * (1 - E2) + h) * sp


def ecef_vers_enu(dx: float, dy: float, dz: float, lat: float, lon: float) -> tuple[float, float, float]:
    """Vecteur ECEF (différence) → repère local Est/Nord/Haut au point (lat, lon)."""
    p, l = math.radians(lat), math.radians(lon)
    sp, cp, sl, cl = math.sin(p), math.cos(p), math.sin(l), math.cos(l)
    e = -sl * dx + cl * dy
    n = -sp * cl * dx - sp * sl * dy + cp * dz
    u = cp * cl * dx + cp * sl * dy + sp * dz
    return e, n, u


# Séparation géoïde–ellipsoïde par défaut (m). EGM96 n'est pas embarqué : la valeur est
# un réglage du profil (≈ 45 m en Normandie, 44–52 m sur la France métropolitaine).
ONDULATION_GEOIDE_DEFAUT_M = 45.0


# ==================================================================================
# Projection d'un point sur une polyligne (accrochage au trajet)
# ==================================================================================
def projection_sur_polyligne(lat: float, lon: float, polyligne) -> tuple[float, float, float, float]:
    """Projection de (lat, lon) sur la polyligne [(lat, lon), …] : (lat, lon, distance m, cap °).

    Projection orthogonale segment par segment dans un plan local équirectangulaire centré sur le
    point (exact à mieux que 0,1 % sur quelques dizaines de km). Le cap est l'azimut du segment
    retenu, dans le sens de parcours de la polyligne (tangente à la route au point projeté).
    Polyligne vide : le point lui-même, cap 0. Un seul sommet : ce sommet, cap 0."""
    if not polyligne:
        return lat, lon, 0.0, 0.0
    k = math.cos(math.radians(lat))
    m_deg = math.pi * R_MOYEN / 180.0
    def plan(la, lo):
        return (normaliser_lon(lo - lon) * k * m_deg, (la - lat) * m_deg)
    if len(polyligne) == 1:
        return polyligne[0][0], polyligne[0][1], math.hypot(*plan(*polyligne[0])), 0.0
    meilleur = (polyligne[0][0], polyligne[0][1], float("inf"), 0.0)
    for (la0, lo0), (la1, lo1) in zip(polyligne, polyligne[1:]):
        ax, ay = plan(la0, lo0)
        bx, by = plan(la1, lo1)
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 <= 0.0:
            continue
        t = max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
        px, py = ax + t * dx, ay + t * dy
        d = math.hypot(px, py)
        if d < meilleur[2]:
            cap = math.degrees(math.atan2(dx, dy)) % 360.0
            meilleur = (la0 + t * (la1 - la0), lo0 + t * normaliser_lon(lo1 - lo0), d, cap)
    return meilleur


def point_le_plus_proche(lat: float, lon: float, polyligne) -> tuple[float, float, float]:
    """Point de la polyligne [(lat, lon), …] le plus proche de (lat, lon), et sa distance (m)."""
    la, lo, d, _ = projection_sur_polyligne(lat, lon, polyligne)
    return la, lo, d
