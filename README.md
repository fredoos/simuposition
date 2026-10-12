# SimuPosition

Simulateur GNSS/GPS à trajectoire sur carte (MBTiles hors ligne ou tuiles en ligne),
destiné au test de récepteurs et d'équipements consommant une position : sortie NMEA 0183
(COM réel ou virtuel, TCP/UDP), gestion d'altitude, constellations, points de passage sans limite.

Stack : Python 3.11+, PySide6 (QtLocation pour la carte, comme MapExtract v6), pyserial, numpy.

## Fonctions visées (v1)

| Domaine | Contenu |
|---|---|
| Carte | QtLocation natif + proxy de tuiles local (IGN Géoplateforme WMTS, OpenTopoMap, OSM, Esri) ; MBTiles local (SQLite, schéma TMS) ; cache disque par source |
| Points de passage | illimités ; ajout par clic sur la carte ou saisie lat/lon (DD, DMM, DMS, UTM) ; altitude optionnelle par point ; glisser-déposer, réordonner ; import/export GPX, KML, JSON |
| Cinématique | vitesse en kt ou km/h, taux de montée/descente (ft/min ou m/s), altitude cible globale ou par point ; trajectoire grande-cercle entre points ; cap et vitesse sol calculés ; Démarrer / Pause / Arrêt / saut à un point |
| GNSS | choix des constellations (GPS, GLONASS, Galileo, BeiDou) ; satellites en vue calculés depuis almanach (élévation, azimut, C/N0) avec angle de masque ; DOP géométriques réels ; type de fix (2D/3D/DGPS) ; nombre de satellites utilisés |
| Sorties | NMEA 0183 (GGA, RMC, GSA, GSV, VTG, GLL, ZDA, HDT) 1–10 Hz, talkers GP/GL/GA/GB/GN ; port COM (réel ou paire com0com) ; TCP serveur / UDP ; journal fichier ; export fichier « motion » pour gps-sdr-sim |
| Persistance | profils JSON (points, cinématique, sorties) ; réglages QSettings |

## Lancement

```bat
py -m pip install -r requirements.txt
py -m simuposition
```

## Arborescence

```
simuposition/
  core/      moteur de trajectoire, géodésie, almanach, DOP
  outputs/   NMEA, série, réseau, gps-sdr-sim
  map/       proxy de tuiles, MBTiles, sources
  ui/        fenêtre principale (PySide6 + QML QtLocation)
docs/        cahier des charges, protocoles
profils/     exemples de profils de vol
tools/       scripts annexes (conversion almanach, com0com)
tests/       tests unitaires (géodésie, NMEA, Gillham…)
```

Voir `docs/CAHIER_DES_CHARGES.md` pour la spécification et les points ouverts.
