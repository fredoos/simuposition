# -*- coding: utf-8 -*-
"""
Proxy de tuiles HTTP local pour la carte QtLocation (schéma repris de MapExtract v6).

La carte QML (plugin « osm », paramètre osm.mapping.custom.host) demande
    GET http://127.0.0.1:PORT/<cle_source>/{z}/{x}/{y}.png
Le proxy convertit en URL amont (XYZ ou WMTS IGN Géoplateforme), télécharge, met en cache
sur disque (un dossier par source) et renvoie la tuile telle quelle. Toute erreur réseau
renvoie 204 (tuile vide) : le serveur ne meurt jamais.

Source « mbtiles:<chemin> » : lecture directe dans le fichier SQLite (schéma TMS : la ligne
est inversée, tile_row = 2^z − 1 − y).
"""
from __future__ import annotations

import hashlib
import http.server
import os
import re
import socket
import sqlite3
import ssl
import threading
import time
import urllib.request
from typing import Optional

UA = "SimuPosition/0.1 (PySide6 QtLocation)"
_TIMEOUT = 20

_ssl_ctx = ssl.create_default_context()
try:
    import certifi
    _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
except Exception:  # noqa: BLE001
    pass

_IGN_WMTS = ("https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
             "&LAYER={layer}&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT={fmt}")

# Catalogue des sources en ligne : clé → (libellé, URL XYZ avec {z}/{x}/{y}, zoom max, attribution)
SOURCES = {
    "ign_plan": ("Plan IGN v2", _IGN_WMTS.format(layer="GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", fmt="image/png", z="{z}", x="{x}", y="{y}"), 19, "IGN Géoplateforme"),
    "ign_ortho": ("Orthophotos IGN", _IGN_WMTS.format(layer="ORTHOIMAGERY.ORTHOPHOTOS", fmt="image/jpeg", z="{z}", x="{x}", y="{y}"), 19, "IGN Géoplateforme"),
    "ign_oaci": ("Carte OACI-VFR (IGN, à vérifier)", _IGN_WMTS.format(layer="GEOGRAPHICALGRIDSYSTEMS.MAPS.SCAN-OACI", fmt="image/jpeg", z="{z}", x="{x}", y="{y}"), 11, "IGN / SIA"),
    "otm": ("OpenTopoMap", "https://tile.openmaps.fr/opentopomap/{z}/{x}/{y}.png", 17, "OpenTopoMap (CC-BY-SA)"),
    "osm": ("OpenStreetMap", "https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19, "© OpenStreetMap"),
    "esri_sat": ("Esri World Imagery", "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", 19, "Esri"),
    "esri_topo": ("Esri World Topo", "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", 19, "Esri"),
}

_RE_TUILE = re.compile(r"^/([A-Za-z0-9_\-]+)/(\d+)/(\d+)/(\d+)\.(png|jpg|jpeg)(?:\?.*)?$")


def _port_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MBTiles:
    """Accès en lecture à un fichier MBTiles (une connexion par thread)."""

    def __init__(self, chemin: str):
        self.chemin = chemin
        self._local = threading.local()
        self.metadata = self._lire_metadata()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(f"file:{self.chemin}?mode=ro", uri=True, check_same_thread=False)
            self._local.conn = c
        return c

    def _lire_metadata(self) -> dict:
        try:
            rows = self._conn().execute("SELECT name, value FROM metadata").fetchall()
            return {k: v for k, v in rows}
        except sqlite3.Error:
            return {}

    @property
    def format(self) -> str:
        return (self.metadata.get("format") or "png").lower()

    def zooms(self) -> tuple[int, int]:
        try:
            z0, z1 = self._conn().execute("SELECT MIN(zoom_level), MAX(zoom_level) FROM tiles").fetchone()
            return int(z0 or 0), int(z1 or 0)
        except sqlite3.Error:
            return 0, 0

    def centre(self) -> Optional[tuple[float, float]]:
        """(lat, lon) depuis la métadonnée center ou bounds."""
        c = self.metadata.get("center")
        if c:
            try:
                lon, lat = [float(v) for v in c.split(",")[:2]]
                return lat, lon
            except ValueError:
                pass
        b = self.metadata.get("bounds")
        if b:
            try:
                lon0, lat0, lon1, lat1 = [float(v) for v in b.split(",")]
                return (lat0 + lat1) / 2, (lon0 + lon1) / 2
            except ValueError:
                pass
        return None

    def tuile(self, z: int, x: int, y: int) -> Optional[bytes]:
        try:
            r = self._conn().execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                                     (z, x, (2 ** z - 1) - y)).fetchone()
            return bytes(r[0]) if r else None
        except sqlite3.Error:
            return None


class ProxyTuiles:
    """Serveur HTTP local multi-sources avec cache disque."""

    def __init__(self, dossier_cache: str):
        self.dossier_cache = dossier_cache
        os.makedirs(dossier_cache, exist_ok=True)
        self.port = _port_libre()
        self.mbtiles: dict[str, MBTiles] = {}       # clé "mbt_<hash>" → fichier
        self.stats = {"ok": 0, "cache": 0, "erreur": 0}
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silencieux
                pass

            def do_GET(self):
                m = _RE_TUILE.match(self.path)
                if not m:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                cle, z, x, y = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
                data, ctype = proxy.obtenir(cle, z, x, y)
                if data is None:
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True, name="proxy-tuiles").start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def url_source(self, cle: str) -> str:
        """Hôte à donner à osm.mapping.custom.host (le plugin ajoute %z/%x/%y.png)."""
        return f"{self.base_url}{cle}/"

    def ajouter_mbtiles(self, chemin: str) -> str:
        """Enregistre un MBTiles et retourne sa clé de source."""
        cle = "mbt_" + hashlib.md5(os.path.abspath(chemin).encode("utf-8")).hexdigest()[:10]
        if cle not in self.mbtiles:
            self.mbtiles[cle] = MBTiles(chemin)
        return cle

    def obtenir(self, cle: str, z: int, x: int, y: int) -> tuple[Optional[bytes], str]:
        if cle in self.mbtiles:
            mb = self.mbtiles[cle]
            data = mb.tuile(z, x, y)
            if data is None:
                return None, ""
            self.stats["ok"] += 1
            return data, "image/jpeg" if mb.format in ("jpg", "jpeg") else "image/png"
        if cle not in SOURCES:
            return None, ""
        libelle, gabarit, zmax, _ = SOURCES[cle]
        if z > zmax:
            return None, ""
        ctype = "image/jpeg" if ("jpeg" in gabarit or cle.startswith("esri")) else "image/png"
        chemin = os.path.join(self.dossier_cache, cle, str(z), str(x), f"{y}.bin")
        if os.path.exists(chemin):
            try:
                with open(chemin, "rb") as f:
                    self.stats["cache"] += 1
                    return f.read(), ctype
            except OSError:
                pass
        url = gabarit.format(z=z, x=x, y=y)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://simuposition.local/"})
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ssl_ctx) as r:
                data = r.read()
                ctype_amont = r.headers.get("Content-Type", "")
                if ctype_amont.startswith("image/"):
                    ctype = ctype_amont.split(";")[0]
        except Exception:  # noqa: BLE001
            self.stats["erreur"] += 1
            return None, ""
        if not data or len(data) < 50:
            return None, ""
        try:
            os.makedirs(os.path.dirname(chemin), exist_ok=True)
            tmp = chemin + f".{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, chemin)
        except OSError:
            pass
        self.stats["ok"] += 1
        return data, ctype

    def taille_cache_mo(self) -> float:
        total = 0
        for racine, _, fichiers in os.walk(self.dossier_cache):
            for f in fichiers:
                try:
                    total += os.path.getsize(os.path.join(racine, f))
                except OSError:
                    pass
        return total / 1e6

    def purger_cache(self, max_mo: float = 500.0, max_jours: float = 60.0) -> int:
        """Supprime les tuiles plus vieilles que max_jours, puis les plus anciennes au-delà de max_mo."""
        maintenant = time.time()
        entrees = []
        for racine, _, fichiers in os.walk(self.dossier_cache):
            for f in fichiers:
                p = os.path.join(racine, f)
                try:
                    st = os.stat(p)
                    entrees.append((st.st_mtime, st.st_size, p))
                except OSError:
                    pass
        supprimes = 0
        total = sum(e[1] for e in entrees)
        entrees.sort()
        for mtime, taille, p in entrees:
            if (maintenant - mtime) / 86400.0 > max_jours or total > max_mo * 1e6:
                try:
                    os.remove(p)
                    total -= taille
                    supprimes += 1
                except OSError:
                    pass
            else:
                break
        return supprimes

    def arreter(self) -> None:
        try:
            self._srv.shutdown()
            self._srv.server_close()
        except Exception:  # noqa: BLE001
            pass
