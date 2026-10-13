# -*- coding: utf-8 -*-
"""
Modèle de données de SimuPosition : points de passage, profil de vol, unités,
sérialisation JSON et import/export GPX / KML.

Conventions internes : latitudes/longitudes en degrés décimaux, altitudes en mètres
(hauteur au-dessus du niveau moyen de la mer, MSL), vitesses en m/s, taux verticaux en
m/s. Les unités d'affichage (kt / km/h, ft/min / m/s, ft / m) ne vivent que dans le
profil (champs `unite_*`) et dans l'interface.
"""
from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Optional

from . import geodesie as geo

# --- Unités d'affichage -----------------------------------------------------------
UNITES_VITESSE = {"kt": geo.KT_MS, "km/h": geo.KMH_MS, "m/s": 1.0}
UNITES_TAUX = {"ft/min": geo.FT_M / 60.0, "m/s": 1.0, "m/min": 1.0 / 60.0}
UNITES_ALT = {"ft": geo.FT_M, "m": 1.0}


def vers_si(valeur: float, unite: str, table: dict) -> float:
    """Convertit une valeur exprimée dans `unite` vers l'unité SI de la table."""
    return valeur * table[unite]


def depuis_si(valeur: float, unite: str, table: dict) -> float:
    """Convertit une valeur SI vers l'unité d'affichage."""
    return valeur / table[unite]


# --- Points de passage --------------------------------------------------------------
@dataclass
class PointPassage:
    lat: float
    lon: float
    alt_m: Optional[float] = None      # altitude MSL cible à ce point ; None = inchangée
    nom: str = ""
    vitesse_ms: Optional[float] = None  # vitesse imposée à partir de ce point ; None = courante
    taux_ms: Optional[float] = None     # taux vertical imposé ; None = courant

    def vers_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}

    @classmethod
    def depuis_dict(cls, d: dict) -> "PointPassage":
        # Compatibilité : alt_ft accepté en entrée (profils écrits à la main)
        alt = d.get("alt_m")
        if alt is None and d.get("alt_ft") is not None:
            alt = float(d["alt_ft"]) * geo.FT_M
        return cls(lat=float(d["lat"]), lon=float(d["lon"]), alt_m=alt,
                   nom=str(d.get("nom", "")),
                   vitesse_ms=d.get("vitesse_ms"), taux_ms=d.get("taux_ms"))


# --- Profil de vol ------------------------------------------------------------------
@dataclass
class Profil:
    nom: str = "Profil sans nom"
    points: list[PointPassage] = field(default_factory=list)
    vitesse_ms: float = 90 * geo.KT_MS           # vitesse sol par défaut
    taux_ms: float = 500 * geo.FT_M / 60.0       # taux vertical max par défaut (500 ft/min)
    alt_initiale_m: float = 100.0                # altitude MSL au départ
    interpolation: str = geo.ORTHODROMIE         # ORTHODROMIE | LOXODROMIE
    boucle: bool = False                         # reprendre au premier point après le dernier
    ondulation_geoide_m: float = geo.ONDULATION_GEOIDE_DEFAUT_M
    inclinaison_max_deg: float = 25.0            # virages arrondis (fly-by) ; 0 = changement de cap instantané
    unite_vitesse: str = "kt"
    unite_taux: str = "ft/min"
    unite_alt: str = "ft"

    # ---- sérialisation JSON -------------------------------------------------------
    def vers_dict(self) -> dict:
        d = asdict(self)
        d["points"] = [p.vers_dict() for p in self.points]
        d["version"] = 1
        return d

    @classmethod
    def depuis_dict(cls, d: dict) -> "Profil":
        p = cls()
        p.nom = d.get("nom", p.nom)
        p.points = [PointPassage.depuis_dict(x) for x in d.get("points", [])]
        # Compatibilité avec le profil d'exemple initial : {"vitesse": {"valeur", "unite"}}
        if isinstance(d.get("vitesse"), dict):
            v = d["vitesse"]
            p.unite_vitesse = v.get("unite", "kt")
            p.vitesse_ms = vers_si(float(v.get("valeur", 90)), p.unite_vitesse, UNITES_VITESSE)
        else:
            p.vitesse_ms = float(d.get("vitesse_ms", p.vitesse_ms))
            p.unite_vitesse = d.get("unite_vitesse", p.unite_vitesse)
        if isinstance(d.get("taux_vertical"), dict):
            t = d["taux_vertical"]
            p.unite_taux = t.get("unite", "ft/min")
            p.taux_ms = vers_si(float(t.get("valeur", 500)), p.unite_taux, UNITES_TAUX)
        else:
            p.taux_ms = float(d.get("taux_ms", p.taux_ms))
            p.unite_taux = d.get("unite_taux", p.unite_taux)
        p.alt_initiale_m = float(d.get("alt_initiale_m", p.points[0].alt_m if p.points and p.points[0].alt_m is not None else p.alt_initiale_m))
        p.interpolation = d.get("interpolation", p.interpolation)
        p.boucle = bool(d.get("boucle", False))
        p.ondulation_geoide_m = float(d.get("ondulation_geoide_m", p.ondulation_geoide_m))
        p.inclinaison_max_deg = max(0.0, min(60.0, float(d.get("inclinaison_max_deg", p.inclinaison_max_deg))))
        p.unite_alt = d.get("unite_alt", p.unite_alt)
        return p

    def enregistrer(self, chemin: str) -> None:
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(self.vers_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def charger(cls, chemin: str) -> "Profil":
        with open(chemin, encoding="utf-8") as f:
            return cls.depuis_dict(json.load(f))

    # ---- géométrie ----------------------------------------------------------------
    def longueur_m(self) -> float:
        """Longueur totale de la route (somme des segments)."""
        return sum(geo.inverse(a.lat, a.lon, b.lat, b.lon, self.interpolation).distance
                   for a, b in zip(self.points, self.points[1:]))

    def trace(self, pas_m: float = 2000.0) -> list[tuple[float, float]]:
        """Polyligne (lat, lon) de la route pour l'affichage carte."""
        pts: list[tuple[float, float]] = []
        for a, b in zip(self.points, self.points[1:]):
            seg = geo.echantillonner(a.lat, a.lon, b.lat, b.lon, pas_m, self.interpolation)
            pts.extend(seg if not pts else seg[1:])
        if self.boucle and len(self.points) > 2:
            a, b = self.points[-1], self.points[0]
            pts.extend(geo.echantillonner(a.lat, a.lon, b.lat, b.lon, pas_m, self.interpolation)[1:])
        return pts if pts else [(p.lat, p.lon) for p in self.points]


# --- Analyse de coordonnées saisies -------------------------------------------------
_RE_DMS = re.compile(r"""^\s*
    (?P<deg>-?\d+(?:[.,]\d+)?)\s*[°:\s]?\s*
    (?:(?P<min>\d+(?:[.,]\d+)?)\s*['′:\s]?\s*)?
    (?:(?P<sec>\d+(?:[.,]\d+)?)\s*["″]?\s*)?
    (?P<hemi>[NSEWnsewOo])?\s*$""", re.X)


def analyser_coordonnee(texte: str, est_lat: bool) -> float:
    """Interprète « 49.0287 », « 49°01'43.3"N », « 49 01.72 N », « 1°13.19'E »… en degrés décimaux.
    'O' est accepté comme Ouest (usage français). Lève ValueError si illisible."""
    t = texte.strip().replace(",", ".")
    m = _RE_DMS.match(t)
    if not m:
        raise ValueError(f"coordonnée illisible : {texte!r}")
    deg = float(m.group("deg"))
    signe = -1.0 if deg < 0 else 1.0
    val = abs(deg) + (float(m.group("min") or 0) / 60.0) + (float(m.group("sec") or 0) / 3600.0)
    hemi = (m.group("hemi") or "").upper()
    if hemi in ("S", "W", "O"):
        signe = -1.0
    val *= signe
    lim = 90.0 if est_lat else 180.0
    if abs(val) > lim:
        raise ValueError(f"coordonnée hors plage : {texte!r}")
    return val


def analyser_paire(texte: str) -> tuple[float, float]:
    """« 49.0287, 1.2199 » ou « 49°01'43"N 1°13'12"E » → (lat, lon)."""
    t = texte.strip()
    # séparation : virgule entre deux nombres, ou espace devant le second groupe hémisphère
    parts = [s for s in re.split(r"\s*[,;]\s*", t) if s]
    if len(parts) != 2:
        m = re.match(r"^(.*?[NSns])\s+(.*?[EWOewo])$", t)
        if not m:
            m = re.match(r"^(\S+)\s+(\S+)$", t)
        if not m:
            raise ValueError(f"paire de coordonnées illisible : {texte!r}")
        parts = [m.group(1), m.group(2)]
    return analyser_coordonnee(parts[0], True), analyser_coordonnee(parts[1], False)


def formater_dms(valeur: float, est_lat: bool) -> str:
    """Degrés décimaux → « 49°01'43.3"N »."""
    hemi = ("N" if valeur >= 0 else "S") if est_lat else ("E" if valeur >= 0 else "W")
    v = abs(valeur)
    d = int(v)
    mnt = (v - d) * 60.0
    m = int(mnt)
    s = (mnt - m) * 60.0
    return f"{d}°{m:02d}'{s:04.1f}\"{hemi}"


def formater_dmm(valeur: float, est_lat: bool) -> str:
    """Degrés décimaux → « 49°01.722'N » (format aéronautique / GPS)."""
    hemi = ("N" if valeur >= 0 else "S") if est_lat else ("E" if valeur >= 0 else "W")
    v = abs(valeur)
    d = int(v)
    return f"{d}°{(v - d) * 60.0:06.3f}'{hemi}"


# --- Import / export GPX et KML -------------------------------------------------------
_NS_GPX = "http://www.topografix.com/GPX/1/1"
_NS_KML = "http://www.opengis.net/kml/2.2"


def importer_gpx(chemin: str) -> list[PointPassage]:
    """Lit les <rtept> (route), sinon <trkpt> (trace), sinon <wpt> d'un fichier GPX."""
    arbre = ET.parse(chemin)
    racine = arbre.getroot()
    ns = {"g": racine.tag.split("}")[0].strip("{")} if racine.tag.startswith("{") else {"g": _NS_GPX}
    for balise in ("g:rte/g:rtept", "g:trk/g:trkseg/g:trkpt", "g:wpt"):
        noeuds = racine.findall(balise, ns)
        if noeuds:
            out = []
            for n in noeuds:
                ele = n.find("g:ele", ns)
                nom = n.find("g:name", ns)
                out.append(PointPassage(float(n.get("lat")), float(n.get("lon")),
                                        float(ele.text) if ele is not None and ele.text else None,
                                        nom.text.strip() if nom is not None and nom.text else ""))
            return out
    return []


def exporter_gpx(points: list[PointPassage], chemin: str, nom: str = "SimuPosition") -> None:
    """Écrit la liste comme route GPX 1.1 (<rte>)."""
    ET.register_namespace("", _NS_GPX)
    gpx = ET.Element(f"{{{_NS_GPX}}}gpx", version="1.1", creator="SimuPosition")
    rte = ET.SubElement(gpx, f"{{{_NS_GPX}}}rte")
    ET.SubElement(rte, f"{{{_NS_GPX}}}name").text = nom
    for p in points:
        pt = ET.SubElement(rte, f"{{{_NS_GPX}}}rtept", lat=f"{p.lat:.7f}", lon=f"{p.lon:.7f}")
        if p.alt_m is not None:
            ET.SubElement(pt, f"{{{_NS_GPX}}}ele").text = f"{p.alt_m:.1f}"
        if p.nom:
            ET.SubElement(pt, f"{{{_NS_GPX}}}name").text = p.nom
    ET.ElementTree(gpx).write(chemin, encoding="utf-8", xml_declaration=True)


def importer_kml(chemin: str) -> list[PointPassage]:
    """Lit les Placemark/Point puis les LineString d'un KML (coordonnées lon,lat[,alt])."""
    racine = ET.parse(chemin).getroot()
    ns = {"k": racine.tag.split("}")[0].strip("{")} if racine.tag.startswith("{") else {"k": _NS_KML}
    out: list[PointPassage] = []
    for pm in racine.iter(f"{{{ns['k']}}}Placemark"):
        nom_el = pm.find("k:name", ns)
        nom = nom_el.text.strip() if nom_el is not None and nom_el.text else ""
        for coord in pm.iter(f"{{{ns['k']}}}coordinates"):
            triplets = [c for c in re.split(r"\s+", (coord.text or "").strip()) if c]
            for i, t in enumerate(triplets):
                parts = t.split(",")
                lon, lat = float(parts[0]), float(parts[1])
                alt = float(parts[2]) if len(parts) > 2 and parts[2] else None
                out.append(PointPassage(lat, lon, alt, nom if (len(triplets) == 1 or i == 0) else ""))
    return out


def exporter_kml(points: list[PointPassage], chemin: str, nom: str = "SimuPosition") -> None:
    """Écrit les points (Placemark) et la route (LineString) en KML 2.2."""
    ET.register_namespace("", _NS_KML)
    kml = ET.Element(f"{{{_NS_KML}}}kml")
    doc = ET.SubElement(kml, f"{{{_NS_KML}}}Document")
    ET.SubElement(doc, f"{{{_NS_KML}}}name").text = nom
    for i, p in enumerate(points, 1):
        pm = ET.SubElement(doc, f"{{{_NS_KML}}}Placemark")
        ET.SubElement(pm, f"{{{_NS_KML}}}name").text = p.nom or f"WP{i}"
        pt = ET.SubElement(pm, f"{{{_NS_KML}}}Point")
        ET.SubElement(pt, f"{{{_NS_KML}}}coordinates").text = f"{p.lon:.7f},{p.lat:.7f},{p.alt_m or 0:.1f}"
    pm = ET.SubElement(doc, f"{{{_NS_KML}}}Placemark")
    ET.SubElement(pm, f"{{{_NS_KML}}}name").text = "Route"
    ls = ET.SubElement(pm, f"{{{_NS_KML}}}LineString")
    ET.SubElement(ls, f"{{{_NS_KML}}}tessellate").text = "1"
    ET.SubElement(ls, f"{{{_NS_KML}}}coordinates").text = " ".join(
        f"{p.lon:.7f},{p.lat:.7f},{p.alt_m or 0:.1f}" for p in points)
    ET.ElementTree(kml).write(chemin, encoding="utf-8", xml_declaration=True)
