# SimuPosition

Simulateur GNSS/GPS à trajectoire sur carte (MBTiles hors ligne ou tuiles en ligne), destiné
au test de récepteurs et d'équipements consommant une position : sortie NMEA 0183 (port COM
réel ou virtuel, serveur TCP, UDP, fichier), altitude et taux vertical, constellations
GPS / GLONASS / Galileo / BeiDou calculées, points de passage sans limite.

Stack : Python 3.11+, PySide6 (carte QtLocation native + proxy de tuiles local, schéma
MapExtract v6), pyserial, numpy, sgp4.

## Installation et lancement

```bat
git clone https://github.com/fredoos/simuposition
cd simuposition
py -m pip install -r requirements.txt
py -m simuposition
py -m pytest          # 29 tests : géodésie, moteur, NMEA, constellations, proxy
```

Réglages, profil courant, dernier fond et vue sont mémorisés (`HKCU\Software\skydev\SimuPosition`).
Caches (tuiles, TLE, QtLocation) : `%LOCALAPPDATA%\skydev\SimuPosition\cache\simuposition\`.

## Utilisation

| Zone | Contenu |
|---|---|
| **Barre carte** | Fond (Plan IGN v2, Orthophotos IGN, OACI, OpenTopoMap, OSM, Esri imagerie/topo) ; `MBTiles…` ajoute un fichier local (SQLite, schéma TMS) ; `Clic = ajouter un point` ; `Suivre le mobile` ; ajuster la vue ; centrer sur le mobile. |
| **Points de passage** | Table éditable (nom, lat, lon, altitude, vitesse et taux optionnels par point — vide = consigne courante). Ajout par clic carte ou par saisie `49.0287, 1.2199` / `49°01'43"N 1°13'12"E` / `49 01.72 N` (`O` accepté pour Ouest). Points déplaçables à la souris sur la carte. Supprimer, ▲ ▼, Centrer, Vider. Ouvrir (JSON, GPX, KML) / Enregistrer (JSON) / exporter GPX, KML. |
| **Cinématique** | Vitesse sol (kt, km/h, m/s), taux vertical max (ft/min, m/s, m/min), altitude initiale, altitude cible manuelle (`Rejoindre`), interpolation orthodromie (Vincenty WGS84) ou loxodromie, boucle, facteur de temps ×1 … ×100, séparation géoïde (défaut 45 m, Normandie). `Démarrer` (F5) / `Pause` / `Arrêt` (figé sur place) / `Réinitialiser` / `Sauter au point` sélectionné. Affichage : état, position DMM + DD, altitude, cap, vitesse sol, vz, distance restante, ETA, heure simulée UTC. |
| **Constellations** | Activation par système ; source **nominale** (Walker, hors ligne) ou **TLE CelesTrak** (bouton de téléchargement, cache local, propagation SGP4) ; masque d'élévation ; canaux de poursuite ; décrochages aléatoires. Vue du ciel (élévation/azimut, taille = C/N0, plein = utilisé), visibles/utilisés, HDOP/VDOP/PDOP/GDOP, fix 2D/3D. |
| **Sorties NMEA** | Dialecte `GN` (NMEA 4.10 multi-GNSS : GNRMC/GNGGA…, GSA par système, GSV par talker GP/GL/GA/GB) ou `GP` (NMEA 2.3, GPS seul). Phrases GGA RMC GSA GSV VTG GLL ZDA, cadence 1/2/5/10 Hz, aperçu du dernier lot. Canaux cumulables : port série (COM réel ou paire com0com), serveur TCP (port 10110, clients multiples), UDP (unicast/broadcast), journal fichier. |

Le mobile part du premier point (altitude du point ou altitude initiale). Chaque appui sur
`Démarrer` poursuit vers le prochain point ; la route terminée laisse le mobile immobile au
dernier point (fix maintenu). Les altitudes par point sont rejointes au taux vertical maximal
dès le passage du point précédent.

## Architecture

```
simuposition/
  core/geodesie.py       Vincenty direct/inverse WGS84, loxodromie, ECEF/ENU
  core/modele.py         PointPassage, Profil, unités, JSON, GPX/KML, analyse de coordonnées
  core/moteur.py         intégration 10 Hz, consignes, Démarrer/Pause/Arrêt/saut, facteur de temps
  core/constellation.py  Walker nominal + J2, TLE/SGP4, GMST, visibilité, C/N0, DOP
  outputs/nmea.py        GGA RMC GSA GSV VTG GLL ZDA, checksum, dialectes GN/GP
  outputs/canaux.py      série, TCP serveur, UDP, fichier, diffuseur
  map/proxy_tuiles.py    proxy HTTP local 127.0.0.1:port/<source>/z/x/y.png, MBTiles, cache
  ui/fenetre.py          fenêtre PySide6 (docks), boucle de simulation, réglages
  ui/qml/Carte.qml       carte QtLocation (plugin osm → proxy), route, points déplaçables, mobile
  ui/pont_carte.py       liaison Python ↔ QML ;  ui/ciel.py : vue du ciel
docs/CAHIER_DES_CHARGES.md   exigences, décisions, points d'architecture
profils/                 exemples
tests/                   pytest
```

## Limites connues / suite

- Carte OACI IGN : couche à vérifier (peut nécessiter une clé Géoplateforme) ; le proxy renvoie
  une tuile vide en cas d'échec.
- Pas d'EGM96 embarqué : séparation géoïde réglable (constante).
- GLONASS en mode TLE : numéros de slot non présents dans les TLE → identifiants NMEA 65+ séquentiels.
- Une seule colonne d'horloge dans le calcul des DOP multi-constellations.
- Prévu ensuite : backend Leaflet optionnel, EXE PyInstaller + Inno Setup sur demande.

## Licence

Aucune licence : tous droits réservés, © skydev 2026. Le code est publié pour consultation ; toute réutilisation ou redistribution nécessite un accord écrit de l'auteur.
