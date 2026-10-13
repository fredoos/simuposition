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
- Q5  Dépôt public ; **pas de licence** (décision du 2026-10-13) : tous droits réservés, © skydev 2026. Aucun fichier LICENSE ne doit être ajouté.

## 5. Points d'architecture retenus de MapExtract v6
- Proxy de tuiles HTTP local `127.0.0.1:PORT/<source>/{z}/{x}/{y}.png`, 204 sur erreur réseau, User-Agent dédié.
- MBTiles : SQLite, `SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?` avec `tile_row = (2**z-1)-y`.
- QML : `osm.mapping.providersrepository.disabled=true`, sélection du `MapType.CustomMap`, cache `osm.mapping.cache.directory` par source, `QSG_RENDER_LOOP=threaded`, nudge de reprojection après recréation de la Map.

## Virages arrondis (v0.2)
- Décision : chemin lissé construit géométriquement (raccords circulaires tangents), suivi à la vitesse sol — remplace la poursuite de point à taux limité (abandonnée : boucles/orbites quand les tronçons sont plus courts que le rayon de virage).
- Rayon R = v²/(g·tan φ), φ = `Profil.inclinaison_max_deg` (défaut 25°, 0 = ancien comportement instantané). Longueur d'entrée T = R·tan(Δ/2), bornée à la moitié des tronçons adjacents (rayon réduit, événement `virage_serre` avec inclinaison équivalente). Δ borné à 175°.
- Arcs dans le plan tangent au sommet, entrée/sortie par `direct` géodésique ; nœuds tous les 2° ; cap interpolé ; jalon = milieu du raccord.
- Reconstruction du chemin si le profil est édité en vol (cap de sortie rattrapé à taux limité : pas de saut) ; boucle : 20 tours construits, puis reconstruction.
- Hors périmètre : couplage montée/inclinaison, vent, rayon variable au sein d'un virage.

## Mode LIVE : GPS réel + offset (v0.3)
- Décision (2026-10-13) : deux modes de source. **SIM** = trajectoire synthétique (l'existant). **LIVE** = GPS réel reçu + offset de la trace émise ; offset nul ≡ **bypass** (trace émise = trace réelle).
- Temps réel strict : la vitesse accélérée est supprimée (condition du couplage synchrone avec un GPS réel).
- Entrée GPS (`core/source_gps.py`) : réception NMEA série / TCP client / UDP, parseur RMC/GGA/VTG/GLL → `Fix` (lat, lon, alt, SOG, COG, UTC, qualité), une publication par époque avec numéro de séquence. Le moteur ne recalcule une sortie qu'au changement de séquence (une sortie par époque GPS, cadence réelle du récepteur, re-synthèse par le générateur NMEA interne — pas de répétition, latence négligeable sur TCP/UDP et série ≥ 38400).
- Offset (`core/offset.py`) : position émise = fix réel + vecteur d'offset. Type **latéral** (⊥ cap, droite/gauche), **longitudinal** (le long de la route, avant/arrière), **relèvement fixe**, + composante **verticale**. Direction recalculée chaque époque sur le COG (sauf relèvement).
  - Pilotage **AUTO (waypoints)** : séparation au passage du WP départ (rampe sur `D_sep = v·T_sep`), croisière, retrouvaille tombant **exactement** sur le WP arrivée — déclenchement calculé sur la vitesse (`D0 = v·T_retrouvaille`), forme **pilotée par la distance restante** → annulation pile au waypoint même à vitesse variable.
  - Pilotage **MANUEL (bouton)** : Appliquer / Annuler / ± en temps réel, rampe temporelle sur `T_manuel`, sans waypoint ; toute commande part de la valeur courante (pas de saut), changement de direction = rentrée à 0 puis ressortie.
  - Transitions en smoothstep (dérivée nulle aux extrémités). Garde-fou : événement `interception_serree` si l'angle d'interception équivalent `atan(1,5·M/(T·v))` dépasse le seuil réglable (défaut 40°).
- Intégration moteur : en LIVE la position décalée est écrite dans `EtatSim` → pipeline NMEA / constellation / sorties réutilisé tel quel ; cap et vitesse sol par différence finie des positions de sortie. `brancher_source` / `definir_mode`.
- Carte : trace réelle (vert) + trace émise (bleu), marqueur GPS réel (vert), marqueurs d'offset **D**/**A** ; waypoints colorés (départ vert, arrivée rouge, intermédiaires turquoise).
- UI : taille du texte réglable (Compact/Normal/Grand, menu Affichage), s'adapte au DPI.
- Hors périmètre : spoofing d'un système tiers (outil de banc pour les équipements de l'utilisateur), lissage du raccord entre SIM et LIVE en vol, offset vertical couplé à un profil barométrique.
