# SimuPosition

Simulateur GNSS/GPS à trajectoire sur carte (MBTiles hors ligne ou tuiles en ligne), destiné
au test de récepteurs et d'équipements consommant une position : sortie NMEA 0183 (port COM
réel ou virtuel, serveur TCP, UDP, fichier), altitude et taux vertical, constellations
GPS / GLONASS / Galileo / BeiDou calculées, points de passage sans limite. Mode **LIVE** :
réception d'un GPS réel et émission d'une trace décalée d'un offset réglable (0 = bypass).

Stack : Python 3.11+, PySide6 (carte QtLocation native + proxy de tuiles local, schéma
MapExtract v6), pyserial, numpy, sgp4.

## Installation et lancement

```bat
git clone https://github.com/fredoos/simuposition
cd simuposition
py -m pip install -r requirements.txt
py -m simuposition
py -m pytest          # 93 tests : géodésie, moteur, NMEA, constellations, proxy, offset, entrée GPS
```

**Barre du haut** (seul emplacement des commandes de marche) : bouton bascule **Démarrer** (vert) / **Stop** (rouge) pendant le vol, raccourci **F5**, Pause / Reprendre, Réinitialiser ; au centre le **chrono de vol**, le **point passé → point visé** et l'**heure UTC** (heure GPS en mode Direct). Des **languettes ◀ ▶ ▼** sur les bords de la carte replient ou déplient les panneaux pour agrandir la carte. **Mode développeur** : `Aide ▸ Mode développeur ▸ Simulateur GPS` (Ctrl+Shift+G).

Interface : thèmes **Système / Sombre / Clair** au choix (menu `Affichage ▸ Thème`, mémorisé), **taille du texte** Compact / Normal / Grand (menu `Affichage ▸ Taille du texte`, s'adapte au facteur d'échelle DPI de l'OS), icônes vectorielles sur les boutons et les onglets (SVG intégrés recolorés selon le thème, aucune ressource externe), badge d'état coloré, altitude en grand (`ui/theme.py`). **Retour visuel d'enclenchement** : les boutons *Émission active*, *Recevoir le GPS*, *Trace du vol* s'allument (halo coloré) quand ils sont actifs ; une **pastille de mode** indique *Simulation* (bleu) ou *Direct* (vert), avec halo et bordure verte sur les panneaux du mode Direct ; un **petit logo GPS** (satellite) dans la barre d'état passe à l'ambre (*GPS en route*) puis au vert (*GPS capté*) ou au rouge (*GPS perdu*). Curseurs synchronisés pour la vitesse sol, le taux vertical et l'inclinaison. `Affichage ▸ Disposition` : *étendue* (route + cinématique à gauche, GNSS + LIVE à droite) ou *compacte* (panneaux en onglets avec icônes, carte agrandie) ; les panneaux restent déplaçables et regroupables à la souris, la disposition est mémorisée.

Réglages, profil courant, dernier fond et vue sont mémorisés (`HKCU\Software\skydev\SimuPosition`).
Caches (tuiles, TLE, QtLocation) : `%LOCALAPPDATA%\skydev\SimuPosition\cache\simuposition\`.

## Utilisation

| Zone | Contenu |
|---|---|
| **Barre carte** | Fond (Plan IGN v2, Orthophotos IGN, OACI, OpenTopoMap, OSM, Esri imagerie/topo) ; `MBTiles…` ajoute un fichier local (SQLite, schéma TMS) ; `Clic = ajouter un point` ; `Suivre le mobile` ; ajuster la vue ; centrer sur le mobile. |
| **Points de passage** | Table éditable (nom, lat, lon, altitude, vitesse et taux optionnels par point — vide = consigne courante). Ajout par clic carte ou par saisie `49.0287, 1.2199` / `49°01'43"N 1°13'12"E` / `49 01.72 N` (`O` accepté pour Ouest). Points déplaçables à la souris sur la carte. Supprimer, ▲ ▼, Centrer, Vider. Ouvrir (JSON, GPX, KML) / Enregistrer (JSON) / exporter GPX, KML. |
| **Cinématique** | Vitesse sol (kt, km/h, m/s), taux vertical max (ft/min, m/s, m/min), altitude initiale, altitude cible manuelle (`Rejoindre`), interpolation orthodromie (Vincenty WGS84) ou loxodromie, boucle, séparation géoïde (défaut 45 m, Normandie), **inclinaison max en virage** (défaut 25°) : positive = virage anticipé avant le point (*fly-by*), **négative = survol exact du point puis virage au-delà** (*fly-over* : interception de la branche suivante à 30°, ou direct vers le point suivant si la branche est trop courte), 0 = cap instantané. La simulation tourne en temps réel (pas d'accéléré, condition du couplage avec un GPS réel). Marche / arrêt depuis la barre du haut (F5) ; ici `Sauter au point` sélectionné. Affichage : état, position DMM + DD, altitude, cap, vitesse sol, vz, distance restante, ETA, heure simulée UTC. |
| **Constellations** | Activation par système ; source **nominale** (Walker, hors ligne) ou **TLE CelesTrak** (bouton de téléchargement, cache local, propagation SGP4) ; masque d'élévation ; canaux de poursuite ; décrochages aléatoires. Vue du ciel (élévation/azimut, taille = C/N0, plein = utilisé), visibles/utilisés, HDOP/VDOP/PDOP/GDOP, fix 2D/3D. |
| **Sorties NMEA** | Dialecte `GN` (NMEA 4.10 multi-GNSS : GNRMC/GNGGA…, GSA par système, GSV par talker GP/GL/GA/GB) ou `GP` (NMEA 2.3, GPS seul). Phrases GGA RMC GSA GSV VTG GLL ZDA, cadence 1/2/5/10 Hz, aperçu du dernier lot. Canaux cumulables : port série (COM réel ou paire com0com), serveur TCP (port 10110, clients multiples), UDP (unicast/broadcast), journal fichier. |
| **Mode LIVE** | Source *GPS réel + offset* : réception NMEA (série / TCP client / UDP), trace réelle (vert) et trace émise (bleu) sur la même carte, marqueur GPS réel. Offset de la trace émise (0 = bypass) : type latéral / longitudinal / relèvement + composante verticale, en NM ou km. Pilotage **Auto** (waypoints départ/arrivée) ou **Manuel** (Appliquer / Annuler / ±). |

**Traces** : la route prévue (orange) est tracée comme elle sera volée (virages arrondis, calcul avec le même moteur) ; la **trace du vol** (bleu) montre le chemin réellement parcouru (un point tous les 20 m, case « Trace du vol » et bouton « Effacer la trace » dans la barre de la carte ; effacée par Réinitialiser). Si un point ne peut pas être atteint par un virage borné (segments plus courts que le rayon, point dans le cercle de virage), il est enchaîné (dépassé) au lieu de faire tourner le mobile en orbite.

**Virages** : le mobile suit un chemin lissé construit géométriquement (`core/chemin.py`) : chaque sommet est remplacé par un raccord circulaire tangent aux deux tronçons, de rayon R = v²/(g·tan φ) (φ = inclinaison max ; ex. 90 kt / 25° ≈ 470 m, 250 kt / 25° ≈ 3,6 km). Si les tronçons sont trop courts pour ce rayon, le raccord est réduit pour tenir (jamais de boucle ni d'orbite) et le journal l'indique : « virage serré … inclinaison équivalente ≈ N° » — réduire alors la vitesse ou espacer les points. Le cap NMEA (RMC/VTG) est continu ; la trace orange montre exactement la trajectoire qui sera volée. Un point de passage est « atteint » (consignes de vitesse/altitude, événement) au plus près du sommet ; le dernier point est rejoint exactement. À 0° : ancien comportement (cap instantané).

Le mobile part du premier point (altitude du point ou altitude initiale). Chaque appui sur
`Démarrer` poursuit vers le prochain point ; la route terminée laisse le mobile immobile au
dernier point (fix maintenu). Les altitudes par point sont rejointes au taux vertical maximal
dès le passage du point précédent.

**Mode LIVE (GPS réel + offset)** — panneau *Mode LIVE*. On choisit la source *GPS réel + offset (LIVE)*, on connecte le récepteur (série / TCP client / UDP) et on reçoit son flux NMEA. La position émise par SimuPosition = position réelle **+ un offset** ; offset nul ⇒ la trace émise est identique à la trace réelle (**bypass**), à la cadence du récepteur et sans discontinuité (une sortie par époque reçue, re-synthèse par le générateur NMEA interne). Activer *Recevoir le GPS* lance l'émission ; **Stop** (F5) la fige — rien n'est émis, la position réelle et la trace verte continuent d'être suivies — et **Démarrer** la reprend. Une ligne d'état sous l'offset (reprise dans le bandeau du haut) dit à tout moment pourquoi les traces sont confondues ou décalées : *distance = 0*, *placez D et A*, *en attente du franchissement de la barrière D (à x NM)*, *séparation*, *croisière*, *retrouvaille*, *hippodrome*, *terminé*. En mode Direct, le panneau *Cinématique* est grisé (vitesse, cap et altitude viennent du GPS réel) ; seules les unités d'affichage et la séparation géoïde restent actives.

**Principe de l'offset** : la trace émise est la trace GPS réelle **translatée** d'un vecteur figé dans le repère terrestre au déclenchement (il ne pivote pas avec les virages du vrai avion). Chaque transition (séparation, retrouvaille, commande manuelle) est répartie sur une **distance** calculée — la plus courte qui respecte l'**angle d'interception** (défaut 30°), les **vitesses V min / V max** de l'avion simulé et une pente verticale de 5 % — d'où la durée et la vitesse simulée, affichées sous les balises. L'offset se pilote de deux façons :

- **Auto (barrières)** : les balises **D** (départ) et **A** (arrivée) se posent d'un clic sur la carte, n'importe où — accrochage à la route à moins de 400 m — et se déplacent à la souris. Chacune est une **barrière** : un segment perpendiculaire à un **cap de passage** (tangente à la route si la balise est accrochée, sinon axe D→A, réglable), de demi-largeur réglable (défaut ± 2 NM), qui ne compte que franchi **dans le sens du passage** ; la carte trace la barrière et une flèche du sens. Le franchissement de D lance la séparation (si l'avion est déjà entre D et A, dans le couloir de D, et fait route vers A, D compte comme passée : départ tardif) ; une fois D passée, la barrière A devient la barrière visée (mise en avant sur la carte, distance « → A » dans le bandeau) ; la retrouvaille ramène l'offset à 0 **exactement sur la ligne A**, où que le vrai avion la franchisse : position, cap et vitesse simulés y sont ceux du vrai avion. D et A trop proches : offset partiel, sans dépasser l'angle (alerte). Réarmement au franchissement suivant de D.
- **Manuel (bouton)** : `Appliquer` / `Annuler` / `±` ajustent l'offset en temps réel, orienté sur le cap sol au moment de la commande ; transition sur la distance calculée (au sol immobile, elle progresse comme à 10 m/s).

**Comportement de l'avion simulé** (`core/comportement.py`) : le décalage ci-dessus est un *plan* ; l'avion simulé le poursuit avec une dynamique de véhicule, ce qui garde un vol réaliste quand l'ordre change en cours de route.
- **Avion** : V min ≤ V ≤ V max (jamais d'arrêt ni de marche arrière), taux de virage ≤ g·tan φ / V (inclinaison max), accélération ≤ a max ; une cible derrière impose un virage. **Hippodrome** : si le point visé avance plus lentement que V min (GPS réel qui stagne ou trop lent), l'avion simulé tient un circuit d'attente attaché au point visé (branches d'1 min, virages du côté opposé au vrai avion, vitesse sol = V min) ; dès que le GPS réel reprend une vitesse d'avion il reprend la poursuite ; si l'arrivée en A approche, la dernière boucle est **calée sur le temps restant** (branche d'éloignement recalée en continu puis vitesse ajustée) pour arriver sur la barrière A **en même temps** que le vrai avion.
- **Hélicoptère** : 0 ≤ V ≤ V max (stationnaire possible), accélération vectorielle ≤ a max, translation libre sous 20 kt, taux de virage limité au-dessus.
- **Translation exacte** : copie décalée pure, sans dynamique.
Changer de comportement charge ses valeurs par défaut (avion générique 50–150 kt, 30°, 1 m/s², hippodrome ; hélico 0–140 kt, 20°/s, 2 m/s²). Préréglages d'appareil : **C-130J** (V max 365 kt = croisière max publiée par Lockheed Martin) et **A400M** (V max 420 kt ≈ 421 KTAS / M0,72, SKYbrary), V min 140 kt, 30°, 0,5–0,6 m/s² — V min, inclinaison et accélération sont des hypothèses de travail : les remplacer par les valeurs de procédure. Offset nul = bypass exact (aucun filtrage).

**Banc de test des offsets (simulateur GPS, mode caché)** : raccourci **Ctrl+Shift+G**. Une fenêtre émet un flux NMEA « GPS réel » simulé en boucle locale (UDP 127.0.0.1) — trajectoire manuelle pilotable (vitesse avec pas de ±10 kt, cap circulaire avec pas de ±10°, virage réglable avec virages standard 3 °/s et « Droit » en un clic) ou route du profil principal. Le bouton *Brancher l'appli en Direct sur ce flux* règle SimuPosition en Direct, entrée UDP sur le bon port, et active la réception. **Réduire** (bouton en haut de la fenêtre, ou bouton de réduction de la barre de titre) la masque sans arrêter l'émission : un bouton vert **clignotant** « Simulateur GPS · émet · 120 kt · cap 090° » apparaît dans la barre du haut, un clic la rouvre ; fermer la fenêtre arrête l'émission. On voit alors la trace « réelle » (vert, plus large, donc visible sous la bleue quand elles se superposent) et la trace émise décalée (bleu) diverger puis se rejoindre — sans matériel. Pour qu'elles divergent : une **Distance** d'offset non nulle, puis en Auto les balises D et A posées sur le trajet (l'offset monte au passage à D), ou en Manuel le bouton *Appliquer*.

Type d'offset : **latéral** (perpendiculaire au cap, droite/gauche), **longitudinal** (le long de la route, avant/arrière), **relèvement fixe**, plus une **composante verticale** (défaut 0). Toutes les transitions sont douces (smoothstep : cap et vitesse simulés égaux aux réels au début et à la fin de chaque transition). Les waypoints sont colorés — **départ vert, arrivée rouge, intermédiaires turquoise** — et en pilotage Auto les marqueurs **D** / **A** repèrent le départ et l'arrivée de l'offset. Sur la carte, en mode Direct : **flèche rouge** = position émise (avion simulé), **flèche verte** = vraie position GPS ; quand les deux sont confondues (offset nul, écart < 15 m), seule la flèche rouge reste, entourée d'un **halo vert pulsé**. La simulation étant en temps réel strict, le couplage avec le GPS réel reste synchrone.

## Architecture

```
simuposition/
  core/geodesie.py       Vincenty direct/inverse WGS84, loxodromie, ECEF/ENU
  core/modele.py         PointPassage, Profil, unités, JSON, GPX/KML, analyse de coordonnées
  core/chemin.py         chemin lissé (raccords circulaires tangents, jalons, virages serrés)
  core/moteur.py         intégration 10 Hz temps réel, consignes, Démarrer/Pause/Arrêt/saut ; mode LIVE (fix + offset)
  core/source_gps.py     entrée GPS réel : réception NMEA série/TCP/UDP, parseur RMC/GGA/VTG/GLL → Fix
  core/offset.py         plan d'offset : translation, barrières D/A, transitions (angle, V min/V max), manuel
  core/comportement.py   dynamique de l'avion simulé : avion / hélico / translation exacte, hippodrome
  core/constellation.py  Walker nominal + J2, TLE/SGP4, GMST, visibilité, C/N0, DOP
  outputs/nmea.py        GGA RMC GSA GSV VTG GLL ZDA, checksum, dialectes GN/GP
  outputs/canaux.py      série, TCP serveur, UDP, fichier, diffuseur
  map/proxy_tuiles.py    proxy HTTP local 127.0.0.1:port/<source>/z/x/y.png, MBTiles, cache
  ui/theme.py            thèmes sombre/clair/système + taille du texte (palettes, QSS généré, icônes SVG)
  ui/fenetre.py          fenêtre PySide6 (docks route/cinématique/GNSS/sorties/LIVE), boucle de simulation, réglages
  ui/qml/Carte.qml       carte QtLocation (plugin osm → proxy), route, points colorés, traces émise+réelle, mobiles
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
