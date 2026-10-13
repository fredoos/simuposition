# -*- coding: utf-8 -*-
"""Chemin lissé : route à virages arrondis construite géométriquement (pas de poursuite de point).

Chaque sommet intérieur est remplacé par un raccord circulaire tangent aux deux tronçons (« fly-by »)
de rayon R = v²/(g·tan φ) (φ = inclinaison max). Si les tronçons sont trop courts pour ce rayon, le
raccord est réduit pour tenir (rayon plus petit = virage plus serré, signalé), il ne déborde jamais :
pas de boucle, pas d'orbite, cap continu. Le mobile suit ensuite ce chemin à la vitesse sol.

Représentation : nœuds (lat, lon, cap) avec abscisse curviligne cumulée `s` (m) ; jalons = abscisse du
passage au plus près de chaque point de passage (milieu du raccord)."""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Optional

from . import geodesie as geo

G_MS2 = 9.80665
INCLINAISON_MAX_DEG = 80.0
DELTA_MAX_DEG = 175.0           # au-delà, le raccord est borné (demi-tour quasi complet)
PAS_ARC_DEG = 2.0               # un nœud tous les 2° de virage
_A = 6378137.0
_E2 = 0.00669437999014


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
class Chemin:
    lat: list = field(default_factory=list)
    lon: list = field(default_factory=list)
    cap: list = field(default_factory=list)
    s: list = field(default_factory=list)
    jalons: list = field(default_factory=list)        # (s, idx_point, phi_equivalent_deg ou None)

    @property
    def longueur(self) -> float:
        return self.s[-1] if self.s else 0.0

    def position(self, s: float) -> tuple[float, float, float]:
        """(lat, lon, cap) à l'abscisse s (bornée au chemin)."""
        if not self.s:
            return 0.0, 0.0, 0.0
        if s <= 0.0:
            return self.lat[0], self.lon[0], self.cap[0]
        if s >= self.s[-1]:
            return self.lat[-1], self.lon[-1], self.cap[-1]
        i = bisect.bisect_right(self.s, s) - 1
        ds = self.s[i + 1] - self.s[i]
        f = 0.0 if ds <= 0 else (s - self.s[i]) / ds
        cap = (self.cap[i] + f * ecart_cap(self.cap[i + 1], self.cap[i])) % 360.0
        return (self.lat[i] + f * (self.lat[i + 1] - self.lat[i]),
                self.lon[i] + f * (self.lon[i + 1] - self.lon[i]), cap)


def _enu_vers_geo(lat0: float, lon0: float, e: float, n: float) -> tuple[float, float]:
    """Décalage local (est, nord) en mètres → (lat, lon) ; plan tangent en (lat0, lon0)."""
    p = math.radians(lat0)
    w = 1.0 - _E2 * math.sin(p) ** 2
    m = _A * (1.0 - _E2) / w ** 1.5
    nn = _A / math.sqrt(w)
    return lat0 + math.degrees(n / m), lon0 + math.degrees(e / (nn * math.cos(p)))


def construire(profil, depart: tuple[float, float], idx_cible: int, vitesse_ms: float,
               tours: int = 1, pas_m: float = 1000.0) -> Chemin:
    """Chemin lissé depuis `depart` vers le point `idx_cible` puis la suite de la route.

    Hors boucle : jusqu'au dernier point. En boucle : `tours` tours complets (le dernier sommet n'est
    pas raccordé). `vitesse_ms` : vitesse sol en vigueur (les points peuvent imposer la leur)."""
    pts = profil.points
    n = len(pts)
    mode = profil.interpolation
    incl = profil.inclinaison_max_deg
    if profil.boucle and n > 1:
        seq = [(idx_cible + j) % n for j in range(n * max(1, tours))]
    else:
        seq = list(range(min(max(idx_cible, 0), n), n))
    # sommets : départ puis points, en fusionnant les doublons (< 1 m) — leurs jalons sont conservés
    Q = [depart]
    ids: list[list[int]] = [[]]
    vit = [vitesse_ms]
    for i in seq:
        p = pts[i]
        v = p.vitesse_ms if p.vitesse_ms else vitesse_ms
        if geo.inverse(Q[-1][0], Q[-1][1], p.lat, p.lon, mode).distance < 1.0:
            ids[-1].append(i)
            vit[-1] = v if len(Q) > 1 else vit[-1]
            continue
        Q.append((p.lat, p.lon))
        ids.append([i])
        vit.append(v)
    ch = Chemin()
    ch.lat.append(Q[0][0]); ch.lon.append(Q[0][1]); ch.s.append(0.0)
    if len(Q) < 2:
        ch.cap.append(0.0)
        return ch
    legs = [geo.inverse(Q[k][0], Q[k][1], Q[k + 1][0], Q[k + 1][1], mode) for k in range(len(Q) - 1)]
    ch.cap.append(legs[0].cap_depart)
    m = len(Q)
    pos = Q[0]                    # position courante du chemin (début du prochain tronçon droit)
    s = 0.0

    def droit(a, b, leg=None):
        """Ajoute les nœuds du tronçon droit a → b (b inclus). Retourne la longueur."""
        nonlocal s
        inv = leg or geo.inverse(a[0], a[1], b[0], b[1], mode)
        L = inv.distance
        if L < 0.01:
            return 0.0
        cnt = max(1, int(L // pas_m))
        for i in range(1, cnt):
            la, lo, cf = geo.direct(a[0], a[1], inv.cap_depart, L * i / cnt, mode)
            ch.lat.append(la); ch.lon.append(lo); ch.cap.append(cf % 360.0); ch.s.append(s + L * i / cnt)
        ch.lat.append(b[0]); ch.lon.append(b[1]); ch.cap.append(inv.cap_arrivee % 360.0); ch.s.append(s + L)
        s += L
        return L

    for k in range(1, m):
        v = vit[k]
        finale = (k == m - 1)
        raccord = None
        if not finale:
            h_in, h_out = legs[k - 1].cap_arrivee, legs[k].cap_depart
            delta = ecart_cap(h_out, h_in)
            ad = min(abs(delta), DELTA_MAX_DEG)
            R = rayon_virage(v, incl)
            if R > 0 and ad > 0.05:
                t_max = min(legs[k - 1].distance * (1.0 if k == 1 else 0.5),
                            legs[k].distance * (1.0 if k == m - 2 else 0.5))
                tan_h = math.tan(math.radians(ad) / 2.0)
                T = R * tan_h
                serre = None
                if T > t_max:
                    T = t_max
                    Rp = T / tan_h
                    serre = math.degrees(math.atan(v * v / (G_MS2 * Rp)))
                else:
                    Rp = R
                if T > 0.5:
                    raccord = (h_in, h_out, delta, ad, T, Rp, serre)
        if raccord is None:
            droit(pos, Q[k], legs[k - 1] if pos == Q[k - 1] else None)
            pos = Q[k]
            for i in ids[k]:
                ch.jalons.append((s, i, None))
            continue
        h_in, h_out, delta, ad, T, Rp, serre = raccord
        E = geo.direct(Q[k][0], Q[k][1], h_in + 180.0, T, mode)[:2]
        X = geo.direct(Q[k][0], Q[k][1], h_out, T, mode)[:2]
        droit(pos, E)
        # arc dans le plan tangent au sommet (est, nord), centre du côté du virage
        sgn = 1.0 if delta > 0 else -1.0
        he = math.radians(h_in)
        ex, ey = -T * math.sin(he), -T * math.cos(he)                  # entrée (relative au sommet)
        cx, cy = ex + sgn * Rp * math.cos(he), ey - sgn * Rp * math.sin(he)
        pas = max(1, math.ceil(ad / PAS_ARC_DEG))
        dth = ad / pas
        i_mid = pas // 2
        idx_mid = len(ch.lat) - 1
        for j in range(1, pas + 1):
            hh = math.radians(h_in + sgn * j * dth)
            px = cx - sgn * Rp * math.cos(hh)
            py = cy + sgn * Rp * math.sin(hh)
            la, lo = _enu_vers_geo(Q[k][0], Q[k][1], px, py)
            s += Rp * math.radians(dth)
            ch.lat.append(la); ch.lon.append(lo); ch.cap.append((h_in + sgn * j * dth) % 360.0); ch.s.append(s)
            if j == i_mid:
                idx_mid = len(ch.lat) - 1
        if i_mid == 0:
            idx_mid = len(ch.lat) - pas - 1
        # fermeture exacte sur la sortie géodésique X (écart de quelques mètres au plus)
        ch.lat[-1], ch.lon[-1], ch.cap[-1] = X[0], X[1], h_out % 360.0
        for i in ids[k]:
            ch.jalons.append((ch.s[idx_mid], i, serre))
        pos = X
    ch.jalons.sort(key=lambda j: j[0])
    return ch
