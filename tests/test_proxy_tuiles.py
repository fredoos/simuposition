# -*- coding: utf-8 -*-
"""Tests du proxy de tuiles avec un MBTiles synthétique (pas de réseau)."""
import os
import sqlite3
import tempfile
import urllib.request

from simuposition.map.proxy_tuiles import ProxyTuiles, MBTiles, SOURCES

PNG_1x1 = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ff"
                        "ff3f0300060405fa6ed2a60000000049454e44ae426082")


def creer_mbtiles(chemin: str):
    c = sqlite3.connect(chemin)
    c.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    c.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    c.executemany("INSERT INTO metadata VALUES (?,?)", [("name", "test"), ("format", "png"), ("bounds", "1.0,49.0,1.5,49.2")])
    # tuile z=10 x=515 y=350 en XYZ → tile_row TMS = 1023-350 = 673
    c.execute("INSERT INTO tiles VALUES (?,?,?,?)", (10, 515, 673, PNG_1x1))
    c.commit()
    c.close()


def test_mbtiles_et_proxy():
    # Windows : les connexions SQLite des threads du serveur (une par thread, lecture seule) gardent
    # le .mbtiles ouvert à la sortie ; on tolère l'échec du nettoyage du dossier temporaire.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        mb = os.path.join(d, "t.mbtiles")
        creer_mbtiles(mb)
        m = MBTiles(mb)
        assert m.format == "png" and m.zooms() == (10, 10)
        lat, lon = m.centre()
        assert abs(lat - 49.1) < 1e-9 and abs(lon - 1.25) < 1e-9
        assert m.tuile(10, 515, 350) == PNG_1x1 and m.tuile(10, 0, 0) is None
        proxy = ProxyTuiles(os.path.join(d, "cache"))
        cle = proxy.ajouter_mbtiles(mb)
        assert cle.startswith("mbt_") and proxy.url_source(cle).endswith(f"/{cle}/")
        with urllib.request.urlopen(proxy.url_source(cle) + "10/515/350.png", timeout=5) as r:
            assert r.status == 200 and r.headers["Content-Type"] == "image/png" and r.read() == PNG_1x1
        with urllib.request.urlopen(proxy.url_source(cle) + "10/1/1.png", timeout=5) as r:
            assert r.status == 204
        try:
            urllib.request.urlopen(proxy.base_url + "n_importe_quoi", timeout=5)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404
        # source inconnue → 204 ; zoom au-delà du max → 204 (sans accès réseau)
        with urllib.request.urlopen(proxy.base_url + "inconnue/1/0/0.png", timeout=5) as r:
            assert r.status == 204
        with urllib.request.urlopen(proxy.base_url + "osm/25/0/0.png", timeout=5) as r:
            assert r.status == 204
        assert "ign_plan" in SOURCES and "osm" in SOURCES
        proxy.arreter()
        m.fermer()
        # fichier libéré : supprimable sous Windows ; une lecture après fermeture rouvre
        assert proxy.mbtiles[cle]._connexions == [] and m._connexions == []
        assert m.tuile(10, 515, 350) == PNG_1x1
        m.fermer()
        os.remove(mb)
