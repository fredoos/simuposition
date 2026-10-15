# Audit : ergonomie en vol et sûreté de la position émise

15/10/2026 — SimuPosition, état du dépôt après le commit `e592f80`, puis corrections. © skydev 2026

Deux questions :

1. **Sûreté de la sortie.** La trace simulée émise (NMEA) peut-elle faire un **saut** de position ou
   d'altitude, quoi que fasse l'opérateur et quoi que fasse le GPS réel ?
2. **Ergonomie en vol.** L'application est-elle utilisable dans un avion : vibrations, turbulence,
   tablette ou portable posé sur les genoux, éclairage variable ?

## 1. Méthode

**Sûreté.**

- **Banc de martelage** (`banc_sauts.py`, hors dépôt) : 60 campagnes de 15 min. Chacune enchaîne, au
  hasard, 26 actions opérateur sur le cœur du programme :
  - démarrer / stop / pause / réinitialiser ;
  - changer de mode, aller au point ;
  - inclinaison, vitesse, points déplacés ou supprimés ;
  - distance, type, pilotage et comportement de l'offset ;
  - balises D / A, maintien, appliquer / annuler, offset initial ;
  - altitude cible, offset vertical, estime, reprise, boucle.

  Un GPS réel factice vire, accélère, fait des paliers d'altitude de ±300 m et perd le signal. On
  mesure l'écart entre deux sorties **valides** consécutives pendant une émission continue. Seuil de
  saut : plus de 520 kt ou plus de 8000 ft/min.
- **Une quarantaine de scénarios ciblés** : Simulation, Direct, franchissement de la barrière A,
  pertes GPS, trames sans altitude, champs « nan ».
- **Revue du code** de chaque chemin qui écrit la position émise.

**Ergonomie.**

- Application lancée hors écran en 1366 × 768. Tailles mesurées en pixels puis en millimètres
  (1 px ≈ 0,2 mm sur un portable 14″ FHD à 125 %).
- Contrastes calculés selon WCAG.
- Revue de chaque interaction possible pendant l'émission.

## 2. Sûreté : sauts trouvés et corrections

Avant correction, le banc trouvait **64 sauts en 30 campagnes**, jusqu'à **27 700 kt** et **295 000 ft/min**.

| Scénario | Saut avant | Cause | Correction |
|---|---|---|---|
| Balise D/A déplacée ou pilotage changé en pleine émission (Direct) | toute la distance d'offset (1–20 NM) et l'offset vertical, en une trame | `offset.reinit()` recréait l'avion simulé (W = 0) | `reinit_plan()` : nouveau plan, mais l'avion simulé (position, vitesse, maintien), le dernier fix et l'estime sont conservés. Rejointe en vol vers le nouveau point visé, altitude à 1000 ft/min au plus. L'hippodrome en cours est abandonné proprement. |
| Clic sur le mode déjà actif (Direct → Direct) | toute la distance d'offset | `definir_mode` réinitialisait même sans changement | Sans effet si le mode est inchangé (sauf au chargement d'un profil). |
| Même source GPS rebranchée en pleine émission | dernier fix rejoué, saut de 60 à 30 000 m | séquence remise à zéro, fix périmé consommé | Même source : sans effet. Un fix plus vieux que 2,5 s n'est jamais consommé comme frais. |
| « Aller au point » en vol avec inclinaison 0 (ou avant le 1er pas) | 20 km | téléportation si `_en_vol` était faux | Toujours un **direct-to** en route ou en pause (cap instantané à 0°, sans saut). Placement seulement à l'arrêt. |
| Franchissement de A sans convergence (hippodrome en cours, vrai avion plus rapide que V max) | 0,25 à 14,5 km | recalage exact forcé en A | Recalage seulement si l'avion simulé est à moins de 1 m du vrai. Sinon, rejointe en vol vers le vrai avion. V max portée à 1,2 × V réelle pour cette rejointe, sinon il ne le rattraperait jamais. |
| Perte GPS > 30 s puis reprise | 6,3 km, survitesse de 6 km/s de l'avion simulé | pas de temps remis à zéro par les sorties à l'estime | Pas de suivi = temps depuis le dernier fix **réel** (horodatages GPS jusqu'à 1 h). |
| Reprise progressive après perte | 200 m d'altitude en une trame | rattrapage vertical calculé sur toute la durée de la perte | Pas de rattrapage vertical sur l'époque de reprise. |
| Reprise après perte + offset initial au même instant | 9 km | offset initial posé à la place de la reprise | La reprise est prioritaire. |
| Translation exacte : plan qui bondit, passage Avion → Translation en hippodrome | 1,6 à 2,7 km | W = plan, sans dynamique | Un bond de plus de 50 m passe par une rejointe dynamique. |
| 1re trame RMC avant toute GGA (altitude inconnue) | 3000 m d'altitude (altitude 0 inventée) | `alt_m = None` traité comme 0 | Rien n'est émis tant qu'aucune altitude réelle n'est connue. Ensuite, la dernière altitude connue est utilisée. |
| Champ NMEA « nan » / « inf » | altitude NaN dans la GGA | `float()` les accepte | Rejetés à la lecture (et minutes ≥ 60). |
| Chemin lissé : demi-tour > 175° ou longs raccords à faible inclinaison | jusqu'à 457 m en un pas | fin d'arc « recollée » d'un coup sur la sortie géodésique | Écart réparti le long de l'arc, entrée et sortie. Au-delà de 50 m, la suite part du bout réel de l'arc. Testé sur 300 routes aléatoires. |
| Altitude initiale réglée à l'arrêt (Simulation) | 2700 m en une trame | altitude réécrite pendant l'émission | Repositionnement déclaré (voir § 3). |
| Coordonnées NMEA « 4860.00000 » | trame non conforme | arrondi des minutes | Arrondi en minutes entières, report sur les degrés. |

## 3. Garde-fou de sortie (`core/garde_fou.py`)

C'est un dernier filtre, indépendant du reste du programme, appliqué à **chaque** position émise dans
les deux modes.

- **Bornes.**
  - Horizontale : V_garde = max(1,3 × V_réf + 20 m/s, 60 m/s), avec V_réf = max(consigne, V max de
    l'avion simulé, vitesse Doppler du GPS réel). La vitesse calculée depuis les positions n'entre
    pas dans V_réf : une position aberrante ne peut pas élargir sa propre borne.
  - Verticale : Vz_garde = max(1,5 × taux max, 30 m/s ≈ 5900 ft/min).
  - Valeur non finie (NaN, ∞) : la sortie précédente est tenue.
- **Écrêtage.** Une sortie hors bornes est ramenée sur le segment qui la relie à la précédente, à la
  distance maximale permise. La sortie rejoint ensuite la valeur calculée à vitesse bornée. Chaque
  épisode est journalisé une fois (« ⚠ GARDE-FOU ») et affiché dans la bande d'alarme.
- **Repositionnements volontaires.** Sont concernés : Réinitialiser, placement à l'arrêt, changement
  de mode, Démarrer en Direct, reprise *non progressive* après une perte GPS.

  Ils ne sont jamais émis comme un saut en trame valide. Si la nouvelle position est hors de portée
  de la dernière émise, le **fix est déclaré invalide 3 s** (RMC « V », GGA qualité 0). Le récepteur
  voit une perte puis une réacquisition, comme avec un vrai GPS.
- **Horloges monotones.** Le garde-fou ne mesure pas le temps avec l'heure émise, qui peut reculer
  (Réinitialiser) ou sauter (horodatage GPS). Il utilise des secondes simulées en Simulation et du
  temps réel en Direct.

## 4. Résultat

- **Banc de martelage** : **0 saut** en 60 campagnes de 15 min.
  - Le garde-fou n'intervient plus que sur les **paliers d'altitude aberrants injectés dans le GPS
    réel factice** : ±300 m en 1 s, ramenés à 30 m/s. C'est son rôle.
  - Toutes les autres causes ont été corrigées en amont.
- **Scénarios ciblés** : plus aucun saut en trame valide. Les cas restants sont des repositionnements
  volontaires, émis en fix invalide.
- **Tests** : 119 tests (15 nouveaux dans `tests/test_garde_fou.py`).
  - Ils couvrent les bornes, NaN, le changement de balise en émission, le mode inchangé, le palier
    d'altitude, l'aller-au-point sans inclinaison et la réacquisition déclarée.
  - Ils couvrent aussi l'altitude inconnue, la barrière A sans convergence, la reprise progressive et
    la continuité du chemin lissé.
  - Un martelage aléatoire de 1500 époques termine la série.

## 5. Ergonomie en vol : constats

Les constats sont classés du plus grave au moins grave.

| # | Constat | État |
|---|---|---|
| C1 | **La molette change les réglages au simple survol.** En faisant défiler le panneau Direct, un cran sur « Source de position » arrêtait l'émission. Le même geste passait le pilotage en Manuel, l'appareil sur C-130J ou la distance de 20 à 19 NM. | **Corrigé** : filtre d'application. La molette ne modifie plus un champ, une liste ou un curseur qui n'a pas le focus ; elle fait défiler le panneau. |
| C2 | **Une seule action coupe l'émission ou téléporte le mobile.** C'est le cas de Simulation / Direct (bouton à 1,6 mm de son voisin), de Réinitialiser (à 1,6 mm de Pause), de F5 = Stop (voisine de F6 = Maintien), de la fermeture de la fenêtre et du bouton « Émission active ». | **Corrigé.** En marche : mode et Réinitialiser verrouillés. **Stop = appui maintenu 0,8 s** (décompte sur le bouton) ou **Maj+F5**, F5 ne fait que démarrer. Réinitialiser est écarté d'environ 10 mm. La fermeture et la coupure de l'émission sont confirmées, avec « Non » par défaut. |
| C3 | **Carte : le moindre tremblement pendant l'appui déplace un point ou une balise**, sans annulation. « Clic = ajouter » ajoutait un point à chaque appui. | **Corrigé.** Un marqueur ne se déplace qu'après un **appui immobile de 0,5 s** (curseur main fermée), puis au-delà de 12 px. Le seuil du clic carte passe de 3 à 10 px. « Clic = ajouter » est décoché au démarrage. *Annuler (Ctrl+Z) : proposé.* |
| C4 | **Les alarmes ne ressortent pas.** GPS perdu, hippodrome et rejointe ne s'affichaient qu'en texte ordinaire. Ce texte était de plus rejeté dans le débordement « » » de la barre. | **Corrigé.** **Bande d'alarme** en 2e étage : grand texte, un mot, une couleur. Elle affiche ÉMISSION COUPÉE, GPS PERDU, RÉACQUISITION, ESTIME, GARDE-FOU ou HIPPODROME. Sa place est réservée : rien ne bouge quand une alarme apparaît. Le texte d'état est tronqué « … » au lieu de disparaître. |
| C5 | **Cibles tactiles trop petites** pour 9 à 15 mm recommandés : flèches des champs 3,5 × 2,4 mm, cases 3,1 mm, boutons 6,3 mm de haut. | **Corrigé (préréglage).** *Affichage ▸ Taille du texte ▸ Cockpit (vol, grandes cibles)* : texte 13 pt, boutons ≥ 40 px (≈ 8–9 mm), champs ≥ 36 px, flèches 34 px de large, cases 26 px, barres de défilement 22 px, boutons de panneau agrandis. |
| C6 | **Zoom de la carte à la molette seulement**, rien en tactile. | Proposé : pincement et boutons +/− de 48 px. |
| C7 | **Lisibilité.** Le texte par défaut de 9 pt est sous le minimum (MIL-STD-1472). En thème clair, plusieurs pastilles d'état sont à 2,5–3,3:1 au soleil pour 4,5:1 visés. En thème sombre, le texte très clair éblouit la nuit. | En partie : le préréglage Cockpit règle la taille. Proposé : palettes « soleil » (fonds pleins foncés, texte blanc) et « nuit » (texte atténué, halos coupés, carte assombrie). |
| C8 | **Information portée par la couleur seule** : flèches simulée rouge / réelle verte, route orange / trace verte. Elles sont indiscernables pour un daltonien deutéranope. | Proposé : formes différentes (réel en contour creux), étiquettes SIM / GPS, route en pointillés. |
| C9 | **Charge cognitive.** Le panneau Direct fait 3,4 hauteurs d'écran et mélange une trentaine de réglages de sol avec les commandes de vol. Le mode se choisit à deux endroits. | Proposé : onglets « Préparation » et « Vol » (6 commandes au plus, état en tête), une seule commande de mode, raccourcis F7 (Appliquer / Annuler), F8 / F9 (−/+), Ctrl+R (Recevoir le GPS). |

## 6. Limites et suite

- En **Cockpit sur un écran de 1366 px**, la première ligne de la barre du haut déborde : le chrono
  reste visible, l'heure UTC passe dans « » ». C'est confortable à partir de 1600 px.
- La reprise *non progressive* après une perte GPS reste un recalage voulu par la configuration. Il
  est désormais émis comme une réacquisition (fix invalide 3 s), plus jamais comme un saut.
- Non traités ici :
  - l'heure émise n'est pas contrôlée en monotonie (horodatages GPS en recul recopiés tels quels) ;
  - changer la séparation géoïde en vol fait varier la hauteur ellipsoïdale ;
  - supprimer le point visé en vol laisse le mobile immobile alors que la vitesse émise est
    conservée.
