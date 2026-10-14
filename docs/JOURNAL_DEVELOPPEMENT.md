<!-- Journal de développement (mémoire de session) : décisions, état et prochaines étapes,
     pour reprendre le travail sur n'importe quel poste. © skydev 2026 -->

# Session : conception-initiale
Date début : 2026-10-12 22:30
Projet : simuposition
Dernière mise à jour : 2026-10-14 22:00 (fin de journée : tout poussé, dernier commit e049592)

## 1. Objectif
Concevoir et développer SimuPosition : simulateur GNSS/GPS à trajectoire sur carte (MBTiles hors ligne ou tuiles en ligne), avec points de passage illimités (clic carte ou coordonnées), cinématique (vitesse kt/km/h, taux de montée/descente, altitude), gestion des constellations, Démarrer/Pause, et restitution NMEA vers des équipements sous test. Travail multi-postes via le dépôt GitHub fredoos/simuposition.

## 2. Contexte technique
- Dépôt : https://github.com/fredoos/simuposition (public, branche main, créé 2026-10-12 ; 1er commit c3bb942 = squelette).
- Stack retenue : Python 3.11+, PySide6, pyserial, numpy ; EXE PyInstaller + Inno Setup à terme.
- Référence cartographique : MapExtract v6 (dépôt fredoos/MapExtract, MapExtract_v6/map_extract_v6.py + v6_tileproxy.py) — carte QtLocation native (QML Map) alimentée par un proxy de tuiles HTTP local (127.0.0.1) ; catalogue IGN Géoplateforme WMTS (data.geopf.fr, TILEMATRIXSET=PM), OpenTopoMap, OSM, Esri ; `MBTileServer` SQLite avec inversion TMS `tile_row = (2**z-1)-y` ; cache disque par source ; lecture GPS série via pyserial déjà présente (readline NMEA). v3 utilisait Leaflet dans QWebEngineView.
- Projets liés dans la même conversation : AltiSim (simulateur d'alticodeur, /home/claude/altisim, PySide6 ; sorties série ALT xxxxx 9600 pour TT31/GTX 335 ; protocole GIL vers Arduino) — SimuPosition devra rester cohérent (altitude-pression vs altitude GNSS).
- Analyses faites : capteur de pression TRT800H (modèle non publié, probable MS55xx/56xx) et TT21/TC20 (capteur thermostaté, ETSO-C88a, sortie série pin 4 Icarus/Garmin 9600).

## 3. Décisions prises
- Nom du projet : SimuPosition ; dépôt public fredoos/simuposition.
- Arborescence : simuposition/{core,outputs,map,ui}, docs/, profils/, tools/, tests/.
- Q1 sorties v1 = NMEA sur COM + TCP/UDP (pas d'UBX ni gps-sdr-sim en v1).
- Q2 réalisme GNSS = « oui tout » : constellation nominale Walker hors ligne + TLE CelesTrak (cache, sgp4), GPS/GLONASS/Galileo/BeiDou, masque, C/N0, DOP, fix 2D/3D.
- Q3 carte = QtLocation natif (schéma MapExtract v6) ; Leaflet optionnel plus tard derrière une interface commune.
- Q4 interpolation = orthodromie (Vincenty WGS84) ET loxodromie, au choix dans le profil.
- Q5 licence : AUCUNE (décidé 2026-10-13), tous droits réservés © skydev 2026 (aucun nom ni marque dans le dépôt) ; ne pas ajouter de LICENSE ; dépôt public.
- Séparation géoïde : pas d'EGM96 embarqué, réglage du profil (défaut 45 m).
- Dépendances : PySide6, pyserial, numpy, sgp4, pytest.

## 4. État actuel
- GitHub main = 09abf44 : application complète v0.1 (29 tests verts) — core (géodésie, modèle, moteur, constellations), outputs (NMEA GN/GP, série, TCP, UDP, fichier), map (proxy de tuiles + MBTiles), ui (fenêtre PySide6 avec docks, carte QML QtLocation, points déplaçables, vue du ciel, boucle 100 ms, QSettings), README d'utilisation.
- Validé hors écran (offscreen, backend logiciel) : QML sans erreur, tuiles IGN/OTM chargées via le proxy, route/points/mobile affichés, NMEA validé par pynmea2, TCP/UDP testés.
- NON testé sous Windows avec GPU réel : QQuickWidget + QtLocation (MapExtract v6 utilise QQmlApplicationEngine ; si flash/noir → passer la carte en QQuickView + createWindowContainer), ports COM, com0com.
- Tâches 1–7 terminées pour la v0.1.

## 5. Prochaines étapes
1. Test utilisateur sous Windows : lancement, carte (fluidité, changement de fond, MBTiles), ajout/déplacement de points, Démarrer/Pause/Arrêt, sorties COM (adaptateur USB-série ou com0com) et TCP/UDP vers un récepteur/logiciel (OpenCPN, u-center, gpsd…), téléchargement TLE.
2. Retours → corrections ; ajustements ergonomie (hauteur des docks, colonnes de la table).
3. Options restantes du cahier des charges : backend Leaflet optionnel, couche OACI IGN (clé), EGM96, vitesse de simulation > ×100, mode « position fixe » explicite.
4. EXE PyInstaller (--onefile --windowed, inclure ui/qml/Carte.qml et plugins geoservices/osm) + Inno Setup, sur demande après tests.

## 6. Fichiers produits
- Dépôt fredoos/simuposition (main 09abf44) : README.md, docs/CAHIER_DES_CHARGES.md, requirements.txt, pytest.ini, .gitignore, simuposition/__main__.py, core/{geodesie,modele,moteur,constellation}.py, outputs/{nmea,canaux}.py, map/proxy_tuiles.py, ui/{fenetre,pont_carte,ciel}.py, ui/qml/Carte.qml, tests/test_{geodesie,moteur,nmea,constellation,proxy_tuiles}.py, profils/exemple_evreux_local.json.
- /home/claude/altisim/ : AltiSim 0.1.1 (altisim.py, README.md, requirements.txt, profil_exemple.json, gillham_arduino/gillham_arduino.ino) ; livré AltiSim_0.1.1.zip.

## 7. Notes libres
- Création de dépôt impossible depuis la session (API limitée aux dépôts rattachés) : le dépôt a été créé par l'utilisateur, puis rattaché et poussé.
- Reprise sur un autre poste : `git clone https://github.com/fredoos/simuposition` → `py -m pip install -r requirements.txt` → `py -m pytest` (7 tests) → ouvrir Claude dans le dossier et dire « reprends la session conception-initiale » avec ce fichier ; attaquer l'étape 1 (modèle + moteur).
- Référence QML à relire avant l'UI : fredoos/MapExtract → MapExtract_v6/v6_poc.qml (Plugin osm minimal) et V6NativeMap.qml lignes ~1420–1470 (Loader par source, cache isolé, selectCustom(), nudge de reprojection) ; map_extract_v6.py ~8955 (QSG_RENDER_LOOP=threaded).
- CelesTrak injoignable depuis la session cloud (connexion réinitialisée) : les TLE seront téléchargées par l'application sur le PC ; le mode Walker nominal sert de repli hors ligne.
- Points d'implémentation à connaître : QML `pont` (PontCarte, propriétés hoteTuiles/cleSource/route/points/mobile) ; changement de fond = cleSource change → Loader recrée la Map ; cache QtLocation isolé par source ; `osm.mapping.custom.host` = http://127.0.0.1:PORT/<cle>/ ; MBTiles clé mbt_<md5> ; GLONASS TLE sans slot → id 65+ séquentiel ; DOP une seule colonne d'horloge ; géoïde constant réglable ; teardown : setSource(QUrl()) avant destruction du pont.
- Test Paris→New York : 5 853 km en géodésique WGS84 (Vincenty) contre 5 837 km en sphérique — ne pas confondre les deux références.
- Idée : réutiliser la sortie série pin 4 du TT21 et le format ALT xxxxx d'AltiSim pour un banc cohérent position + altitude.
- Artefacts publiés : manuel TRT800H FR https://claude.ai/artifact/R2f7SphwMM3sy9bFG7AY17 ; simulateur Gillham https://claude.ai/artifact/8bKM33Bj6oGbQALNuMUKre.


## Checkpoint 2026-10-13
- Licence : AUCUNE (tous droits réservés © skydev 2026) ; aucun nom ni marque dans le dépôt ni les commits (auteur git « skydev »). Clé registre HKCU\Software\skydev\SimuPosition.
- Virages arrondis livrés (commit c0c5a0d) : fly-by, inclinaison max 25° par défaut (0 = ancien), R=v²/(g·tan φ), amorce R·tan(Δ/2), contrôleur de cap ω=v/R, `trace_lissee()` pour la carte, spinbox « Inclinaison max (virages) ». 36 tests OK.
- Prochaines étapes : test Windows (carte QQuickWidget, COM/com0com, TCP/UDP, TLE) ; éventuellement Leaflet, EGM96, EXE PyInstaller + Inno Setup (SimuPosition + AltiSim) quand l'utilisateur le dit.

## Checkpoint 2026-10-13 (UI v0.2 + mode LIVE v0.3.0)
Commits poussés depuis c0c5a0d : thème (cc0f803) → icônes+thèmes (55c4100) → disposition (59f1a6b) → onglets (fb30966) → bouton thème (b20a999) → v0.2.0 (8cbd2f7) → chevauchements (dc167a6) → trace du vol (132c820) → chemin lissé géométrique (db329e6) → **mode LIVE cœur (bad93ce)** → **mode LIVE UI v0.3.0 (343cd7f)**. main = 343cd7f.

**UI v0.2** : thèmes Système/Sombre/Clair (QSettings `theme`, bouton bascule + menu), icônes SVG recolorées (`ui/theme.py` ICONES), dispositions Étendue/Compacte (tabifyDockWidget, icônes d'onglets), zones défilantes anti-chevauchement, trace du vol bleue incrémentale.

**Virages géométriques** (`core/chemin.py`) : raccords circulaires tangents, R=v²/(g·tan φ), T=R·tan(Δ/2) borné à la moitié des tronçons, arc en plan tangent (nœuds 2°), jalon au milieu, alerte `virage_serre`. `trace_lissee` = même géométrie, tours=1. Remplace la poursuite (fini les boucles/orbites).

**Mode LIVE v0.3.0** (demande du 2026-10-13) — GPS réel + offset de la trace émise :
- `core/source_gps.py` : entrée NMEA série/TCP client/UDP, parseur RMC/GGA/VTG/GLL → `Fix`, une publication par époque + séquence (RMC prime). `creer_source(config)`.
- `core/offset.py` : `MoteurOffset`. Type latéral/longitudinal/relèvement + vertical. offset nul ≡ bypass. AUTO (séparation `D_sep=v·T_sep` au WP départ ; retrouvaille **pile** sur le WP arrivée via déclenchement `D0=v·T_retrouvaille` + forme pilotée par la distance restante, robuste à la vitesse) et MANUEL (rampes `T_manuel`, appliquer/annuler/nudge, pas de saut, changement de direction = rentrée puis ressortie). smoothstep partout ; garde-fou `interception_serree` si angle `atan(1,5·M/(T·v))` > seuil (défaut 40°). `magnitude_courante` pour l'UI.
- `core/modele.py` : `MODE_SIM/MODE_LIVE`, `ConfigGPS`, `ConfigOffset`, champs Profil `mode_source/source_gps/offset` (round-trip JSON).
- `core/moteur.py` : `definir_mode`, `brancher_source`, `_avancer_live` (position=fix+offset dans EtatSim → pipeline NMEA/constellation réutilisé ; cap/vitesse par différence finie ; `sortie_prete`/`dernier_fix` ; horodatage `fix.utc or now`). **Vitesse accélérée supprimée** (temps réel strict, requis par le couplage).
- UI (`ui/fenetre.py`) : dock « Mode LIVE » (source SIM/LIVE, entrée GPS, offset auto/manuel, WP départ/arrivée = point sélectionné, boutons manuels), émission à chaque époque en LIVE, **taille du texte** Compact/Normal/Grand (`theme.appliquer(mode, taille_pt)`, QSettings `police_pt`, défaut 9).
- Carte : `pont_carte` trace réelle (vert) + `mobileReel` + `pointsOffset` ; `Carte.qml` 2e polyligne verte, marqueurs D/A, waypoints colorés (départ vert, arrivée rouge, interm. turquoise).
- Décisions utilisateur : offset « tous les cas » (latéral/longitudinal/relèvement + vertical) ; retrouvaille = WP point de rencontre exact, déclenchement selon la vitesse ; altitude présente (défaut 0) ; mode manuel sans waypoint au bouton ; défauts ft et T=30 s. Transport GPS : les trois (COM/TCP/UDP).
- Tests : `tests/test_offset.py`, `tests/test_source_gps.py` ; **64 tests OK** au total. UI validée hors-écran (construction, bascule LIVE, source factice, offset manuel, marqueurs).

**À tester par l'utilisateur sous Windows** (`git pull` à la racine → `py -m simuposition`, titre « SimuPosition 0.3.0 ») : vrai GPS sur COM (ou rejoué en TCP/UDP), bypass sans coupure, offset auto entre deux waypoints, offset manuel, double trace sur la carte, police. Question en suspens : seuil d'inclinaison équivalente (ex. 45°) refusant/plafonnant en mode SIM ? (indépendant du LIVE).

## Checkpoint 2026-10-14 (correctifs Windows + offset v2)
- Corrigé et poussé : plantage au démarrage Qt 6.10 (restauration disposition en fin de __init__, 9f0b58a), Stop en mode Direct + état d'offset explicite + traces lisibles (cbe49e1), test MBTiles sous Windows (cd6b4c5).
- Offset v2 (demandes du 14/10) — poussé : 49823a0, d2e0d7c (disposition restaurée après le 1er affichage : nouvel abandon Windows au show()), fb87024 (cap/vitesse conservés après reinit) :
  - trace émise = trace réelle TRANSLATÉE (vecteur figé au déclenchement, ne pivote plus avec le cap instantané) ;
  - D et A = barrières orientées (cap de passage = tangente route si accrochée, sinon axe D→A ; demi-largeur réglable, défaut 2 NM ; franchissement dans le sens seulement) ; retrouvaille → 0 pile sur la ligne A ;
  - transitions sur une DISTANCE calculée : angle d'interception (défaut 30°) + V min/V max de l'avion simulé + pente 5 % (dichotomie) ;
  - core/comportement.py : suiveur à dynamique de véhicule (Avion : V min..V max, g·tanφ/V, a max ; Hélico : stationnaire, translation < 20 kt ; Translation exacte) ; HIPPODROME (avion) quand le point visé < V min, boucle finale calée sur le temps restant avant A (écart mesuré ~0 m en test à 10 m/s) ;
  - panneau Cinématique grisé en mode Direct (sauf unités et géoïde).
- Décision : limites de l'avion simulé en ABSOLU (l'avion simulé est un appareil distinct du porteur GPS) ; bypass exact quand offset nul.
- Tests : 89 (OK aussi sur le PC Windows, appli relancée 17:01).
- À faire : retour utilisateur sur le comportement en vol réel / banc ; rapport de l'audit indépendant non reçu dans cette session.
- NOTE POUR PLUS TARD (demande du 14/10 17:32, non implémentée) : dans le bandeau du haut, indications de vitesse et d'offset en mode Direct ; si le vrai avion doit ralentir (ou adapter sa vitesse) pour que l'avion simulé suive, indicateur de vitesse VERT = OK, ROUGE = ralentir, avec la vitesse idéale entre parenthèses (ex. « 132 kt (115) »).
  Définition proposée, à valider : vitesse idéale = vitesse réelle qui laisse l'avion simulé tenir son plan (≤ V max moins la marge de rattrapage ; en retrouvaille, celle qui fait arriver les deux sur A ensemble). Question ouverte : afficher aussi « accélérer » (orange ?) quand le réel est sous V min et déclenche l'hippodrome.
- Préférence auteur commits : skydev ; pas de LICENSE.

## Checkpoint 2026-10-14 soir (tout poussé, dernier commit e049592, 99 tests)
- Ajouts depuis le checkpoint précédent : départ tardif barrière D + barrière A visée ; estimation du temps de retrouvaille ; correctif hippodrome déclenché à tort (dt sur horodatage GPS, hystérésis 95 % / 100 % de V min) ; simulateur GPS lisible en thème clair, champs à pas au lieu des curseurs, réductible en bouton clignotant ; préréglages C-130J (V max 365 kt) / A400M (420 kt) — V min / inclinaison / accélération = hypothèses ; maintien cap/vitesse/alt (F6) + rejointe progressive ; perte GPS : estime 1 Hz puis reprise progressive (options), sinon coupure + position masquée ; bouton « Couper le signal » du simulateur ; mesure d'écart (horizontal, vertical, vitesses, taux) ; choix Simulation / Direct GPS dans la barre du haut.
- Propositions d'ergonomie en attente de choix utilisateur : (1) panneau Direct en sections repliables, (2) disposition par mode, (3) « Recevoir le GPS » dans la barre du haut, (4) liste de démarrage Direct.
- Note pour plus tard (déjà notée) : indicateur vitesse vert/rouge + vitesse idéale entre parenthèses.
- PC utilisateur : %USERPROFILE%\Dev\simuposition (à jour jusqu'à de47486 seulement) et %USERPROFILE%\Dev\simuposition → `git pull`.
