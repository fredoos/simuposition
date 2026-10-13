# SimuPosition — cahier des charges (v0, à valider)

## 1. Objectif
Générer une position GNSS simulée le long d'une trajectoire définie par des points de passage,
avec cinématique réaliste (vitesse, taux de montée/descente), et la restituer aux équipements
sous test sous forme de trames NMEA (et formats dérivés), avec une cartographie de contrôle.

## 2. Exigences fonctionnelles
- E1  Carte : tuiles en ligne (IGN Géoplateforme, OpenTopoMap, OSM, Esri) et MBTiles hors ligne.
- E2  Points de passage : nombre illimité ; saisie par clic carte ou coordonnées ; altitude par point optionnelle ; édition, réordonnancement, import/export GPX/KML/JSON.
- E3  Cinématique : vitesse (kt / km/h), taux de montée/descente (ft/min / m/s), altitude cible globale ou par point ; Démarrer / Pause / Arrêt ; vitesse de simulation ×1…×50.
- E4  Altitude : hauteur ellipsoïdale et altitude MSL distinctes (séparation géoïde EGM96 approchée) ; altitude-pression optionnelle pour cohérence avec AltiSim.
- E5  Constellations : activation GPS / GLONASS / Galileo / BeiDou ; satellites visibles calculés (almanach), masque d'élévation, C/N0 simulé, DOP réels, fix 2D/3D.
- E6  Sorties : NMEA 0183 configurable (phrases, cadence 1–10 Hz, talkers), COM réel/virtuel, TCP/UDP, journal ; fichier motion gps-sdr-sim.
- E7  Persistance : profils JSON ; réglages mémorisés.

## 3. Exigences non fonctionnelles
- Windows 10/11 ; un seul paquet Python ; EXE PyInstaller + Inno Setup à terme.
- Hors ligne complet avec MBTiles + almanach embarqué.
- Code commenté en français ; fichiers complets.

## 4. Décisions (2026-10-12)
- Q1  Sorties v1 : NMEA 0183 sur port COM (réel ou virtuel) **et** TCP/UDP. Pas d'UBX ni de fichier motion gps-sdr-sim en v1.
- Q2  Réalisme GNSS : **oui, tout** — satellites calculés (constellation nominale Walker hors ligne + TLE CelesTrak téléchargées/cachées, propagation sgp4), GPS/GLONASS/Galileo/BeiDou, masque d'élévation, C/N0 simulé, DOP géométriques, fix 2D/3D.
- Q3  Carte : **QtLocation natif** (schéma MapExtract v6 : Plugin osm + `osm.mapping.custom.host` vers proxy local, Loader par source, cache isolé par source) ; Leaflet/WebEngine = backend optionnel ultérieur, derrière une interface commune.
- Q4  Interpolation : **les deux**, orthodromie (Vincenty WGS84) ou loxodromie, au choix dans le profil.
- Q5  Dépôt public ; **pas de licence** (décision du 2026-10-13) : tous droits réservés, © Frédéric Marbach — EVX / RSAS. Aucun fichier LICENSE ne doit être ajouté.

## 5. Points d'architecture retenus de MapExtract v6
- Proxy de tuiles HTTP local `127.0.0.1:PORT/<source>/{z}/{x}/{y}.png`, 204 sur erreur réseau, User-Agent dédié.
- MBTiles : SQLite, `SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?` avec `tile_row = (2**z-1)-y`.
- QML : `osm.mapping.providersrepository.disabled=true`, sélection du `MapType.CustomMap`, cache `osm.mapping.cache.directory` par source, `QSG_RENDER_LOOP=threaded`, nudge de reprojection après recréation de la Map.
