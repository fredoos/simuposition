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

## 4. Points ouverts (à trancher avant codage du moteur)
- Q1  Sorties exigées en v1 : NMEA série seule, ou aussi TCP/UDP, UBX (u-blox NAV-PVT), motion gps-sdr-sim ?
- Q2  Réalisme GNSS : ciel calculé depuis almanach (YUMA/SEM GPS, TLE pour les autres) ou simple nombre de satellites et DOP fixés ?
- Q3  Moteur carte : QtLocation natif (MapExtract v6) — recommandé — ou Leaflet/WebEngine (v3) ?
- Q4  Interpolation entre points : grande-cercle (recommandé) ou loxodromie ?
- Q5  Dépôt : public ou privé ; licence.
