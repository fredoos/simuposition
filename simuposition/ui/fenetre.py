# -*- coding: utf-8 -*-
"""Fenêtre principale de SimuPosition (PySide6 + carte QML QtLocation)."""
from __future__ import annotations

import json
import math
import os
import time
from datetime import timezone

from PySide6.QtCore import Qt, QTimer, QSettings, QSize, QUrl, QStandardPaths, QLocale
from PySide6.QtGui import QAction, QActionGroup, QFont, QKeySequence, QColor, QShortcut
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
    QAbstractItemView, QScrollArea, QSizePolicy, QTabBar, QTabWidget, QSlider, QGraphicsDropShadowEffect,
)

from .. import __version__
from ..core import geodesie as geo
from ..core.modele import (Profil, PointPassage, UNITES_VITESSE, UNITES_TAUX, UNITES_ALT, vers_si, depuis_si,
                           analyser_paire, analyser_coordonnee, formater_dmm, importer_gpx, exporter_gpx,
                           importer_kml, exporter_kml,
                           MODE_SIM, MODE_LIVE, PILOTAGE_AUTO, PILOTAGE_MANUEL,
                           OFFSET_LATERAL, OFFSET_LONGITUDINAL, OFFSET_RELEVEMENT)
from . import theme
from ..core.moteur import Moteur, ARRET, EN_ROUTE, PAUSE, TERMINE, trace_lissee
from ..core.source_gps import creer_source
from ..core.constellation import Constellations, SYSTEMES, age_cache_tle
from ..map.proxy_tuiles import ProxyTuiles, SOURCES
from ..outputs.nmea import GenerateurNMEA, Solution, PHRASES
from ..outputs.canaux import CanalSerie, CanalTCPServeur, CanalUDP, CanalFichier, Diffuseur, ports_serie
from .pont_carte import PontCarte
from .ciel import VueCiel
from .simu_gps import FenetreSimuGPS

PERIODE_MS = 100
QML_CARTE = os.path.join(os.path.dirname(__file__), "qml", "Carte.qml")


def dossier_cache() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.CacheLocation) or os.path.expanduser("~/.simuposition")
    d = os.path.join(base, "simuposition") if not base.lower().endswith("simuposition") else base
    os.makedirs(d, exist_ok=True)
    return d


class Fenetre(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"SimuPosition {__version__} — simulateur GNSS")
        self.settings = QSettings("skydev", "SimuPosition")
        self._taille_police = int(self.settings.value("police_pt", theme.TAILLE_DEFAUT))
        self.cache = dossier_cache()
        self.source_gps = None                 # entrée GPS réelle en cours (mode LIVE)
        # --- modèle et services
        self.profil = Profil()
        self.moteur = Moteur(self.profil, self._evt_moteur)
        self.constellations = Constellations(os.path.join(self.cache, "tle"))
        self.generateur = GenerateurNMEA("GN")
        self.diffuseur = Diffuseur()
        self.proxy = ProxyTuiles(os.path.join(self.cache, "tuiles"))
        self.pont = PontCarte(self)
        self.ciel = None
        self._t_prec = time.monotonic()
        self._acc_sortie = 0.0
        self._acc_ciel = 9.0
        self._maj_table_en_cours = False
        self._trace_dernier = None
        self._trace_n = 0
        self._trace_reel_dernier = None
        self._trace_reel_n = 0
        self._idx_selection = -1
        self._chrono_s = 0.0                   # temps de vol (s) affiché dans le bandeau
        self._placement = ""                   # balise d'offset en attente d'un clic carte ("depart"/"arrivee")
        self._zones_masquees = {}              # docks repliés par les languettes de la carte
        self.canaux = {}
        # --- construction
        self._construire()
        self._connecter()
        self._charger_reglages()
        self._maj_table()
        self._maj_carte_route()
        self._maj_affichage()
        self._timer = QTimer(self)
        self._timer.setInterval(PERIODE_MS)
        self._timer.timeout.connect(self._tic)
        self._timer.start()
        # répartition initiale : la table des points reçoit plus de hauteur que la cinématique
        self.resizeDocks([self.dock_route, self.dock_cine], [360, 440], Qt.Vertical)
        self.resizeDocks([self.dock_route, self.dock_gnss], [470, 430], Qt.Horizontal)
        self.resizeDocks([self.dock_route, self.dock_sorties], [650, 190], Qt.Vertical)
        self._rafraichir_halos()
        self._restaurer_disposition()
        # simulateur GPS caché (banc de test des offsets) : raccourci Ctrl+Shift+G
        self._fen_simgps = None
        QTimer.singleShot(0, self._maj_fleches)

    def _ouvrir_simu_gps(self):
        if self._fen_simgps is None:
            self._fen_simgps = FenetreSimuGPS(self)
        self._fen_simgps.show(); self._fen_simgps.raise_(); self._fen_simgps.activateWindow()
        self._log("simulateur GPS (banc de test offset) ouvert — Ctrl+Shift+G")

    # =====================================================================================
    # Construction de l'interface
    # =====================================================================================
    def _construire(self):
        self.setDockNestingEnabled(True)
        # --- carte centrale
        self.carte = QQuickWidget(self)
        self.carte.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.carte.rootContext().setContextProperty("pont", self.pont)
        self.pont.dossierCache = os.path.join(self.cache, "qtlocation").replace("\\", "/")
        self.carte.setSource(QUrl.fromLocalFile(QML_CARTE))
        self.setCentralWidget(self.carte)
        # --- barre Simulation (ligne du haut) : Démarrer/Stop (bascule), Pause, Réinitialiser ;
        #     au centre : chrono de vol, point passé → point visé, heure UTC (GPS si disponible)
        tbs = QToolBar("Simulation")
        tbs.setObjectName("barre_sim")
        tbs.setMovable(False)
        tbs.setIconSize(QSize(20, 20))
        self.tb_sim = tbs
        self.addToolBar(Qt.TopToolBarArea, tbs)
        self.btn_demarrer_h = QPushButton("Démarrer"); self.btn_demarrer_h.setProperty("role", "primaire")
        self.btn_demarrer_h.setToolTip("Démarrer la simulation ; pendant le vol le bouton devient Stop (F5)")
        self.btn_pause_h = QPushButton("Pause")
        self.btn_reinit_h = QPushButton("Réinitialiser")
        for b in (self.btn_demarrer_h, self.btn_pause_h, self.btn_reinit_h):
            b.setMinimumHeight(32)
            tbs.addWidget(b)
        self._marche_affiche = None
        g = QWidget(); g.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); tbs.addWidget(g)
        self.ico_chrono, self.lbl_chrono = QLabel(), QLabel("00:00:00")
        self.ico_wp, self.lbl_wp = QLabel(), QLabel("—")
        self.ico_utc, self.lbl_utc = QLabel(), QLabel("--:--:-- UTC")
        self.lbl_chrono.setToolTip("Chrono de vol (s'arrête en pause, remis à zéro par Réinitialiser)")
        self.lbl_wp.setToolTip("Dernier point passé → point visé")
        self.lbl_utc.setToolTip("Heure UTC : celle du GPS reçu en mode Direct, sinon heure de la simulation")
        for ico, lbl in ((self.ico_chrono, self.lbl_chrono), (self.ico_wp, self.lbl_wp), (self.ico_utc, self.lbl_utc)):
            lbl.setObjectName("infohaut")
            tbs.addWidget(ico); tbs.addWidget(lbl); tbs.addWidget(QLabel("  "))
        d = QWidget(); d.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); tbs.addWidget(d)
        self.lbl_etat_h = QLabel("Arrêt"); self.lbl_etat_h.setObjectName("etat"); self.lbl_etat_h.setProperty("etat", ARRET)
        tbs.addWidget(self.lbl_etat_h)
        self.addToolBarBreak(Qt.TopToolBarArea)
        # --- barre d'outils carte (deuxième ligne)
        tb = QToolBar("Carte")
        tb.setObjectName("barre_carte")
        tb.setMovable(False)
        self.tb_carte = tb
        self.addToolBar(Qt.TopToolBarArea, tb)
        tb.addWidget(QLabel(" Fond : "))
        self.cmb_source = QComboBox()
        for cle, (lib, _, _, _) in SOURCES.items():
            self.cmb_source.addItem(lib, cle)
        tb.addWidget(self.cmb_source)
        self.btn_mbtiles = QPushButton("MBTiles…")
        tb.addWidget(self.btn_mbtiles)
        tb.addSeparator()
        self.cb_mode_ajout = QCheckBox("Clic = ajouter un point")
        tb.addWidget(self.cb_mode_ajout)
        self.cb_suivre = QCheckBox("Suivre le mobile")
        self.cb_suivre.setChecked(True)
        tb.addWidget(self.cb_suivre)
        self.btn_ajuster = QPushButton("Ajuster la vue à la route")
        tb.addWidget(self.btn_ajuster)
        self.btn_centrer_mobile = QPushButton("Centrer sur le mobile")
        tb.addWidget(self.btn_centrer_mobile)
        tb.addSeparator()
        self.cb_trace = QPushButton("Trace du vol")      # bouton d'enclenchement (s'allume quand actif)
        self.cb_trace.setCheckable(True); self.cb_trace.setChecked(True)
        tb.addWidget(self.cb_trace)
        self.btn_effacer_trace = QPushButton("Effacer la trace")
        self.btn_effacer_trace.clicked.connect(self._effacer_trace)
        tb.addWidget(self.btn_effacer_trace)
        ressort = QWidget(); ressort.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(ressort)
        self.btn_theme = QPushButton("Thème")
        self.btn_theme.setToolTip("Basculer thème clair / sombre (choix « Système » : menu Affichage ▸ Thème)")
        self.btn_theme.clicked.connect(self._basculer_theme)
        tb.addWidget(self.btn_theme)

        # --- dock Route (gauche)
        self.dock_route = QDockWidget("Points de passage", self)
        self.dock_route.setObjectName("dock_route")
        w = QWidget()
        v = QVBoxLayout(w)
        self.table = QTableWidget(0, 6)
        self.table.setMinimumHeight(150); self.table.setAlternatingRowColors(True); self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalHeaderLabels(["Nom", "Latitude", "Longitude", "Alt", "Vit.", "Taux"])
        eh = self.table.horizontalHeader()
        eh.setSectionResizeMode(QHeaderView.ResizeToContents)     # colonnes à la largeur du contenu (pas de texte tronqué)
        eh.setSectionResizeMode(0, QHeaderView.Stretch)           # le nom prend le reste
        eh.setMinimumSectionSize(60)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        v.addWidget(self.table, 1)
        g = QGridLayout()
        self.ed_coord = QLineEdit()
        self.ed_coord.setPlaceholderText("lat, lon  (49.0287, 1.2199 ou 49°01'43\"N 1°13'12\"E)")
        self.ed_alt = QLineEdit()
        self.ed_alt.setPlaceholderText("alt (vide = inchangée)")
        self.ed_alt.setMaximumWidth(120)
        self.ed_nom = QLineEdit()
        self.ed_nom.setPlaceholderText("nom")
        self.ed_nom.setMaximumWidth(110)
        self.btn_ajouter = QPushButton("Ajouter")
        g.addWidget(self.ed_coord, 0, 0, 1, 3)
        g.addWidget(self.ed_nom, 1, 0)
        g.addWidget(self.ed_alt, 1, 1)
        g.addWidget(self.btn_ajouter, 1, 2)
        v.addLayout(g)
        h = QHBoxLayout()
        self.btn_suppr = QPushButton("Supprimer")
        self.btn_monter = QPushButton("")
        self.btn_descendre = QPushButton("")
        self.btn_vider = QPushButton("Vider")
        self.btn_centrer_pt = QPushButton("Centrer")
        for b in (self.btn_suppr, self.btn_monter, self.btn_descendre, self.btn_centrer_pt, self.btn_vider):
            h.addWidget(b)
        v.addLayout(h)
        h2 = QHBoxLayout()
        self.btn_ouvrir = QPushButton("Ouvrir…")
        self.btn_enreg = QPushButton("Enregistrer…")
        self.btn_export_gpx = QPushButton("GPX…")
        self.btn_export_kml = QPushButton("KML…")
        for b in (self.btn_ouvrir, self.btn_enreg, self.btn_export_gpx, self.btn_export_kml):
            h2.addWidget(b)
        v.addLayout(h2)
        self.lbl_route = QLabel("—")
        v.addWidget(self.lbl_route)
        self.dock_route.setWidget(self._defilant(w))
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_route)

        # --- dock Cinématique (gauche, sous Route)
        self.dock_cine = QDockWidget("Cinématique", self)
        self.dock_cine.setObjectName("dock_cine")
        w = QWidget()
        v = QVBoxLayout(w)
        form = QFormLayout()
        self.sp_vitesse = QDoubleSpinBox(); self.sp_vitesse.setRange(0, 3000); self.sp_vitesse.setDecimals(1)
        self.cmb_u_vit = QComboBox(); self.cmb_u_vit.addItems(list(UNITES_VITESSE))
        form.addRow("Vitesse sol", self._ligne(self.sp_vitesse, self.cmb_u_vit))
        self.sl_vitesse = QSlider(Qt.Horizontal); self.sl_vitesse.setRange(0, 600)
        form.addRow("", self.sl_vitesse)
        self.sp_taux = QDoubleSpinBox(); self.sp_taux.setRange(0, 20000); self.sp_taux.setDecimals(1)
        self.cmb_u_taux = QComboBox(); self.cmb_u_taux.addItems(list(UNITES_TAUX))
        form.addRow("Taux vertical max", self._ligne(self.sp_taux, self.cmb_u_taux))
        self.sl_taux = QSlider(Qt.Horizontal); self.sl_taux.setRange(0, 4000)
        form.addRow("", self.sl_taux)
        self.sp_alt_init = QDoubleSpinBox(); self.sp_alt_init.setRange(-2000, 150000); self.sp_alt_init.setDecimals(0)
        self.cmb_u_alt = QComboBox(); self.cmb_u_alt.addItems(list(UNITES_ALT))
        form.addRow("Altitude initiale", self._ligne(self.sp_alt_init, self.cmb_u_alt))
        self.sp_alt_cible = QDoubleSpinBox(); self.sp_alt_cible.setRange(-2000, 150000); self.sp_alt_cible.setDecimals(0)
        self.btn_alt_cible = QPushButton("Rejoindre")
        form.addRow("Altitude cible manuelle", self._ligne(self.sp_alt_cible, self.btn_alt_cible))
        self.cmb_interp = QComboBox(); self.cmb_interp.addItem("Orthodromie (grand cercle)", geo.ORTHODROMIE); self.cmb_interp.addItem("Loxodromie (cap constant)", geo.LOXODROMIE)
        form.addRow("Interpolation", self.cmb_interp)
        self.sp_incl = QDoubleSpinBox(); self.sp_incl.setRange(-60, 60); self.sp_incl.setDecimals(0); self.sp_incl.setSuffix(" °")
        self.sp_incl.setToolTip("Inclinaison dans les virages, rayon R = v²/(g·tan |φ|).\n"
                                "Positive : virage anticipé avant le point (fly-by, coupe la courbe).\n"
                                "Négative : survol exact du point puis virage au-delà (fly-over).\n"
                                "0 : changement de cap instantané aux points de passage.")
        form.addRow("Inclinaison max (virages)", self.sp_incl)
        self.sl_incl = QSlider(Qt.Horizontal); self.sl_incl.setRange(-60, 60)
        form.addRow("", self.sl_incl)
        self.cb_boucle = QCheckBox("Reprendre au premier point après le dernier")
        form.addRow("", self.cb_boucle)
        self.sp_geoide = QDoubleSpinBox(); self.sp_geoide.setRange(-110, 90); self.sp_geoide.setDecimals(1); self.sp_geoide.setSuffix(" m")
        form.addRow("Séparation géoïde", self.sp_geoide)
        v.addLayout(form)
        h = QHBoxLayout()
        self.btn_demarrer = QPushButton("Démarrer")
        self.btn_demarrer.setProperty("role", "primaire")
        self.btn_pause = QPushButton("Pause")
        self.btn_arret = QPushButton("Arrêt")
        self.btn_arret.setProperty("role", "danger")
        self.btn_reinit = QPushButton("Réinitialiser")
        self.btn_sauter = QPushButton("Sauter au point")
        for b in (self.btn_demarrer, self.btn_pause, self.btn_arret, self.btn_reinit, self.btn_sauter):
            h.addWidget(b)
        v.addLayout(h)
        self.lbl_alt = QLabel("—")
        f = QFont("Consolas"); f.setPointSize(16); f.setBold(True)
        self.lbl_alt.setObjectName("altitude")
        self.lbl_position = QLabel("—")
        self.lbl_position.setObjectName("mono")
        self.lbl_etat = QLabel("Arrêt")
        self.lbl_etat.setObjectName("etat")
        self.lbl_etat.setProperty("etat", ARRET)
        self.lbl_cine = QLabel("—")
        self.lbl_cine.setObjectName("mono")
        self.barre = QProgressBar(); self.barre.setRange(0, 1000); self.barre.setTextVisible(False)
        v.addWidget(self.lbl_etat)
        v.addWidget(self.lbl_position)
        v.addWidget(self.lbl_alt)
        v.addWidget(self.lbl_cine)
        v.addWidget(self.barre)
        self.dock_cine.setWidget(self._defilant(w))
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_cine)

        # --- dock GNSS (droite)
        self.dock_gnss = QDockWidget("Constellations GNSS", self)
        self.dock_gnss.setObjectName("dock_gnss")
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGridLayout()
        self.cb_sys = {}
        for i, sy in enumerate(SYSTEMES):
            cb = QCheckBox(sy); cb.setChecked(True)
            self.cb_sys[sy] = cb
            g.addWidget(cb, i // 2, i % 2)
        v.addLayout(g)
        form = QFormLayout()
        self.cmb_source_gnss = QComboBox()
        self.cmb_source_gnss.addItem("Constellation nominale (hors ligne)", "nominal")
        self.cmb_source_gnss.addItem("TLE CelesTrak (réelles)", "tle")
        form.addRow("Source", self.cmb_source_gnss)
        self.btn_tle = QPushButton("Télécharger les TLE")
        self.lbl_tle = QLabel("—")
        form.addRow(self.btn_tle, self.lbl_tle)
        self.sp_masque = QDoubleSpinBox(); self.sp_masque.setRange(0, 60); self.sp_masque.setValue(5); self.sp_masque.setSuffix(" °")
        form.addRow("Masque d'élévation", self.sp_masque)
        self.sp_canaux = QSpinBox(); self.sp_canaux.setRange(4, 64); self.sp_canaux.setValue(32)
        form.addRow("Canaux de poursuite", self.sp_canaux)
        self.sp_decroch = QDoubleSpinBox(); self.sp_decroch.setRange(0, 90); self.sp_decroch.setSuffix(" %")
        form.addRow("Décrochages aléatoires", self.sp_decroch)
        v.addLayout(form)
        self.vue_ciel = VueCiel()
        v.addWidget(self.vue_ciel, 1)
        self.lbl_gnss = QLabel("—")
        self.lbl_gnss.setObjectName("mono")
        v.addWidget(self.lbl_gnss)
        self.dock_gnss.setWidget(self._defilant(w))
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock_gnss)

        # --- dock Sorties + Journal (bas)
        self.dock_sorties = QDockWidget("Sorties NMEA", self)
        self.dock_sorties.setObjectName("dock_sorties")
        onglets = QTabWidget()
        self.onglets_sorties = onglets
        onglets.setAutoFillBackground(True)          # évite la bande noire à droite des onglets
        onglets.setIconSize(QSize(18, 18))
        # onglet trames
        w = QWidget()
        g = QGridLayout(w)
        self.cmb_dialecte = QComboBox()
        self.cmb_dialecte.addItem("GN — NMEA 4.10 multi-GNSS", "GN")
        self.cmb_dialecte.addItem("GP — NMEA 2.3 (GPS seul)", "GP")
        g.addWidget(QLabel("Dialecte"), 0, 0); g.addWidget(self.cmb_dialecte, 0, 1)
        self.cmb_cadence = QComboBox()
        for hz in (1, 2, 5, 10):
            self.cmb_cadence.addItem(f"{hz} Hz", float(hz))
        g.addWidget(QLabel("Cadence"), 1, 0); g.addWidget(self.cmb_cadence, 1, 1)
        self.cb_phrases = {}
        hp = QHBoxLayout()
        for ph in PHRASES:
            cb = QCheckBox(ph); cb.setChecked(True)
            self.cb_phrases[ph] = cb
            hp.addWidget(cb)
        g.addLayout(hp, 2, 0, 1, 4)
        self.cb_actif = QPushButton("Émission active")   # bouton d'enclenchement
        self.cb_actif.setCheckable(True); self.cb_actif.setChecked(True)
        g.addWidget(self.cb_actif, 0, 2)
        self.apercu = QPlainTextEdit(); self.apercu.setReadOnly(True); self.apercu.setFont(QFont("Consolas", 9))
        self.apercu.setMaximumBlockCount(40)
        g.addWidget(self.apercu, 3, 0, 1, 4)
        onglets.addTab(w, "Trames")
        # onglet canaux
        w = QWidget()
        g = QGridLayout(w)
        self.cb_serie = QCheckBox("Port série")
        self.cmb_port = QComboBox(); self.cmb_port.setEditable(True)
        self.btn_ports = QPushButton("")
        self.btn_ports.setMaximumWidth(30)
        self.cmb_baud = QComboBox()
        for b in (4800, 9600, 19200, 38400, 57600, 115200):
            self.cmb_baud.addItem(str(b), b)
        self.cmb_baud.setCurrentText("9600")
        g.addWidget(self.cb_serie, 0, 0); g.addWidget(self.cmb_port, 0, 1); g.addWidget(self.btn_ports, 0, 2); g.addWidget(self.cmb_baud, 0, 3)
        self.cb_tcp = QCheckBox("Serveur TCP, port")
        self.sp_tcp = QSpinBox(); self.sp_tcp.setRange(1, 65535); self.sp_tcp.setValue(10110)
        self.lbl_tcp = QLabel("0 client")
        g.addWidget(self.cb_tcp, 1, 0); g.addWidget(self.sp_tcp, 1, 1); g.addWidget(self.lbl_tcp, 1, 2, 1, 2)
        self.cb_udp = QCheckBox("UDP vers")
        self.ed_udp_hote = QLineEdit("127.0.0.1")
        self.sp_udp = QSpinBox(); self.sp_udp.setRange(1, 65535); self.sp_udp.setValue(10111)
        g.addWidget(self.cb_udp, 2, 0); g.addWidget(self.ed_udp_hote, 2, 1); g.addWidget(self.sp_udp, 2, 2, 1, 2)
        self.cb_fichier = QCheckBox("Journal NMEA")
        self.ed_fichier = QLineEdit()
        self.btn_fichier = QPushButton("")
        self.btn_fichier.setMaximumWidth(30)
        g.addWidget(self.cb_fichier, 3, 0); g.addWidget(self.ed_fichier, 3, 1, 1, 2); g.addWidget(self.btn_fichier, 3, 3)
        g.setRowStretch(4, 1)
        onglets.addTab(w, "Canaux")
        # onglet journal
        self.journal = QPlainTextEdit(); self.journal.setReadOnly(True); self.journal.setFont(QFont("Consolas", 9))
        self.journal.setMaximumBlockCount(1000)
        onglets.addTab(self.journal, "Journal")
        fond = QWidget()                     # conteneur peint : évite la bande noire à droite des onglets
        fond.setObjectName("fondOnglets")
        fond.setAttribute(Qt.WA_StyledBackground, True)
        lf = QVBoxLayout(fond); lf.setContentsMargins(0, 0, 0, 0); lf.addWidget(onglets)
        self.dock_sorties.setWidget(fond)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_sorties)

        # --- dock Mode LIVE (GPS réel + offset)
        self._construire_live()

        # --- menu
        m = self.menuBar().addMenu("Fichier")
        for texte, cible in (("Ouvrir un profil…", self._ouvrir), ("Enregistrer le profil…", self._enregistrer), (None, None), ("Quitter", self.close)):
            if texte is None:
                m.addSeparator()
                continue
            a = QAction(texte, self); a.triggered.connect(cible); m.addAction(a)
        m = self.menuBar().addMenu("Affichage")
        for d in (self.dock_route, self.dock_cine, self.dock_gnss, self.dock_sorties, self.dock_live):
            m.addAction(d.toggleViewAction())
        m.addSeparator()
        md = m.addMenu("Disposition")
        a = QAction("Étendue (panneaux côte à côte)", self); a.triggered.connect(lambda: self._disposition(False)); md.addAction(a)
        a = QAction("Compacte (panneaux en onglets, carte agrandie)", self); a.triggered.connect(lambda: self._disposition(True)); md.addAction(a)
        for d in (self.dock_route, self.dock_cine, self.dock_gnss, self.dock_sorties, self.dock_live):
            d.visibilityChanged.connect(lambda _v=False: QTimer.singleShot(0, self._icones_docks))
            d.dockLocationChanged.connect(lambda _l=None: QTimer.singleShot(0, self._icones_docks))
            d.visibilityChanged.connect(lambda _v=False: QTimer.singleShot(0, self._maj_fleches))
            d.dockLocationChanged.connect(lambda _l=None: QTimer.singleShot(0, self._maj_fleches))
        mt = m.addMenu("Thème")
        self._groupe_theme = QActionGroup(self)
        courant = str(self.settings.value("theme", "sombre"))
        for cle, lib in (("systeme", "Système"), ("sombre", "Sombre"), ("clair", "Clair")):
            a = QAction(lib, self, checkable=True)
            a.setChecked(cle == courant)
            a.triggered.connect(lambda _=False, c=cle: self._changer_theme(c))
            self._groupe_theme.addAction(a)
            mt.addAction(a)
        mp = m.addMenu("Taille du texte")
        self._groupe_taille = QActionGroup(self)
        for lib, pt in theme.TAILLES.items():
            a = QAction(lib, self, checkable=True)
            a.setChecked(pt == self._taille_police)
            a.triggered.connect(lambda _=False, p=pt: self._changer_taille(p))
            self._groupe_taille.addAction(a)
            mp.addAction(a)
        self._enregistrer_icones()
        m = self.menuBar().addMenu("Aide")
        md = m.addMenu("Mode développeur")
        a = QAction("Simulateur GPS (banc de test des offsets)", self)
        a.setShortcut(QKeySequence("Ctrl+Shift+G")); a.setShortcutContext(Qt.ApplicationShortcut)
        a.triggered.connect(self._ouvrir_simu_gps); md.addAction(a); self.addAction(a)
        m.addSeparator()
        a = QAction("À propos", self); a.triggered.connect(self._a_propos); m.addAction(a)
        self.statusBar().showMessage("Prêt")

    # ---- panneau Mode LIVE (GPS réel + offset) -------------------------------------------
    def _construire_live(self):
        self.dock_live = QDockWidget("Mode LIVE (GPS réel)", self)
        self.dock_live.setObjectName("dock_live")
        w = QWidget()
        v = QVBoxLayout(w)

        # source de position
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("Simulation (trajectoire)", MODE_SIM)
        self.cmb_mode.addItem("Direct (GPS réel + offset)", MODE_LIVE)
        self.lbl_mode_chip = QLabel("SIMULATION")        # pastille de mode (bleu = simu, vert = direct)
        self.lbl_mode_chip.setProperty("chip", "sim")
        self.lbl_mode_chip.setAlignment(Qt.AlignCenter)
        fm = QFormLayout()
        fm.addRow("Source de position", self.cmb_mode)
        fm.addRow("", self.lbl_mode_chip)
        v.addLayout(fm)

        # entrée GPS réelle
        self.grp_gps = QGroupBox("Entrée GPS réel")
        fg = QFormLayout(self.grp_gps)
        self.cmb_gps_transport = QComboBox()
        self.cmb_gps_transport.addItem("Série (COM)", "serie")
        self.cmb_gps_transport.addItem("TCP (client)", "tcp")
        self.cmb_gps_transport.addItem("UDP (écoute)", "udp")
        fg.addRow("Transport", self.cmb_gps_transport)
        self.cmb_gps_port = QComboBox(); self.cmb_gps_port.setEditable(True)
        self.btn_gps_ports = QPushButton(""); self.btn_gps_ports.setMaximumWidth(30)
        self.cmb_gps_baud = QComboBox()
        for b in (4800, 9600, 19200, 38400, 57600, 115200):
            self.cmb_gps_baud.addItem(str(b), b)
        self.cmb_gps_baud.setCurrentText("9600")
        self.ligne_serie = self._ligne(self.cmb_gps_port, self.btn_gps_ports, self.cmb_gps_baud)
        fg.addRow("Port série", self.ligne_serie)
        self.ed_gps_hote = QLineEdit("127.0.0.1")
        self.lbl_gps_hote = QLabel("Hôte TCP")
        fg.addRow(self.lbl_gps_hote, self.ed_gps_hote)
        self.sp_gps_port = QSpinBox(); self.sp_gps_port.setRange(1, 65535); self.sp_gps_port.setValue(10111)
        fg.addRow("Port réseau", self.sp_gps_port)
        self.cb_gps_actif = QPushButton("Recevoir le GPS")   # bouton d'enclenchement (vert en Direct)
        self.cb_gps_actif.setCheckable(True); self.cb_gps_actif.setProperty("mode", "live")
        self.lbl_gps_badge = QLabel("GPS —")                 # petit logo d'état GPS (satellite + couleur)
        fg.addRow(self.cb_gps_actif, self.lbl_gps_badge)
        self.lbl_gps_etat = QLabel("—"); self.lbl_gps_etat.setObjectName("mono"); self.lbl_gps_etat.setWordWrap(True)
        fg.addRow(self.lbl_gps_etat)
        v.addWidget(self.grp_gps)

        # offset
        self.grp_offset = QGroupBox("Offset de la trace émise (0 = bypass)")
        fo = QFormLayout(self.grp_offset)
        self.cmb_pilotage = QComboBox()
        self.cmb_pilotage.addItem("Auto (waypoints départ/arrivée)", PILOTAGE_AUTO)
        self.cmb_pilotage.addItem("Manuel (bouton)", PILOTAGE_MANUEL)
        fo.addRow("Pilotage", self.cmb_pilotage)
        self.cmb_otype = QComboBox()
        self.cmb_otype.addItem("Latéral (perpendiculaire)", OFFSET_LATERAL)
        self.cmb_otype.addItem("Longitudinal (le long de la route)", OFFSET_LONGITUDINAL)
        self.cmb_otype.addItem("Relèvement fixe", OFFSET_RELEVEMENT)
        fo.addRow("Type", self.cmb_otype)
        self.sp_omag = QDoubleSpinBox(); self.sp_omag.setRange(0, 999); self.sp_omag.setDecimals(2)
        self.cmb_omag_unite = QComboBox(); self.cmb_omag_unite.addItems(["NM", "km"])
        fo.addRow("Distance", self._ligne(self.sp_omag, self.cmb_omag_unite))
        self.cmb_ocote = QComboBox()        # rempli selon le type (droite/gauche ou avant/arrière)
        self.lbl_ocote = QLabel("Côté")
        fo.addRow(self.lbl_ocote, self.cmb_ocote)
        self.sp_orelev = QDoubleSpinBox(); self.sp_orelev.setRange(0, 359); self.sp_orelev.setDecimals(0); self.sp_orelev.setSuffix(" °")
        self.lbl_orelev = QLabel("Relèvement")
        fo.addRow(self.lbl_orelev, self.sp_orelev)
        self.sp_overt = QDoubleSpinBox(); self.sp_overt.setRange(-30000, 30000); self.sp_overt.setDecimals(0)
        self.cmb_overt_unite = QComboBox(); self.cmb_overt_unite.addItems(["ft", "m"])
        fo.addRow("Vertical", self._ligne(self.sp_overt, self.cmb_overt_unite))
        self.sp_angle_garde = QDoubleSpinBox(); self.sp_angle_garde.setRange(5, 89); self.sp_angle_garde.setDecimals(0); self.sp_angle_garde.setSuffix(" °")
        self.sp_angle_garde.setValue(40); self.sp_angle_garde.setToolTip("Alerte si l'angle d'interception équivalent dépasse ce seuil (offset trop grand ou waypoints trop proches).")
        fo.addRow("Garde interception", self.sp_angle_garde)
        # bloc AUTO
        self.w_auto = QWidget()
        fa = QFormLayout(self.w_auto); fa.setContentsMargins(0, 0, 0, 0)
        self.sp_tsep = QDoubleSpinBox(); self.sp_tsep.setRange(1, 600); self.sp_tsep.setDecimals(0); self.sp_tsep.setSuffix(" s"); self.sp_tsep.setValue(30)
        fa.addRow("Temps séparation", self.sp_tsep)
        self.sp_tret = QDoubleSpinBox(); self.sp_tret.setRange(1, 600); self.sp_tret.setDecimals(0); self.sp_tret.setSuffix(" s"); self.sp_tret.setValue(30)
        fa.addRow("Temps retrouvaille", self.sp_tret)
        self.btn_odepart = QPushButton("Placer le départ (D) sur la carte")
        self.btn_oarrivee = QPushButton("Placer l'arrivée (A) sur la carte")
        for b in (self.btn_odepart, self.btn_oarrivee):
            b.setCheckable(True)
            b.setToolTip("Puis cliquez sur la carte, n'importe où sur le trajet (accrochage à la route à moins de 400 m).\n"
                         "Les balises D et A se déplacent ensuite à la souris.")
        fa.addRow(self.btn_odepart)
        fa.addRow(self.btn_oarrivee)
        self.lbl_owp = QLabel("départ —   arrivée —"); self.lbl_owp.setObjectName("mono"); self.lbl_owp.setWordWrap(True)
        fa.addRow(self.lbl_owp)
        fo.addRow(self.w_auto)
        # bloc MANUEL
        self.w_manuel = QWidget()
        fman = QFormLayout(self.w_manuel); fman.setContentsMargins(0, 0, 0, 0)
        self.sp_tmanuel = QDoubleSpinBox(); self.sp_tmanuel.setRange(1, 600); self.sp_tmanuel.setDecimals(0); self.sp_tmanuel.setSuffix(" s"); self.sp_tmanuel.setValue(30)
        fman.addRow("Temps de rampe", self.sp_tmanuel)
        self.btn_oappliquer = QPushButton("Appliquer")
        self.btn_oappliquer.setProperty("role", "primaire")
        self.btn_oannuler = QPushButton("Annuler (retour à 0)")
        self.btn_omoins = QPushButton("−"); self.btn_oplus = QPushButton("+")
        fman.addRow(self._ligne(self.btn_oappliquer, self.btn_oannuler))
        fman.addRow("Ajuster", self._ligne(self.btn_omoins, self.btn_oplus))
        fo.addRow(self.w_manuel)
        self.lbl_offset_etat = QLabel("—"); self.lbl_offset_etat.setObjectName("mono"); self.lbl_offset_etat.setWordWrap(True)
        fo.addRow(self.lbl_offset_etat)
        v.addWidget(self.grp_offset)
        v.addStretch(1)

        # petit logo d'état GPS dans la barre d'état (toujours visible : en route / capté / perdu)
        self.lbl_gps_statut = QLabel("")
        self.lbl_gps_statut.setVisible(False)
        self.statusBar().addPermanentWidget(self.lbl_gps_statut)

        self.dock_live.setWidget(self._defilant(w))
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock_live)
        self.tabifyDockWidget(self.dock_gnss, self.dock_live)
        self.dock_gnss.raise_()

    # ---- retour visuel : halos, pastilles, logo GPS --------------------------------------
    @staticmethod
    def _repolish(w: QWidget):
        w.style().unpolish(w); w.style().polish(w)

    def _halo(self, w: QWidget, couleur, rayon: int = 20):
        """Pose (ou retire si couleur None) un halo lumineux coloré autour d'un widget enclenché."""
        if not couleur:
            w.setGraphicsEffect(None)
            return
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(rayon)
        eff.setOffset(0, 0)
        eff.setColor(QColor(couleur))
        w.setGraphicsEffect(eff)

    def _maj_gps_badge(self, etat: str):
        """Logo d'état GPS : off / wait (en route) / ok (capté) / lost (perdu)."""
        infos = {"wait": (theme.pal()["avert"], "GPS en route"),
                 "ok": (theme.pal()["direct"], "GPS capté"),
                 "lost": (theme.pal()["alerte"], "GPS perdu")}
        if etat not in infos:
            self.lbl_gps_badge.setText("GPS —"); self.lbl_gps_badge.setProperty("gps", "")
            self.lbl_gps_statut.setVisible(False)
        else:
            coul, txt = infos[etat]
            self.lbl_gps_badge.setText(txt); self.lbl_gps_badge.setProperty("gps", etat)
            self.lbl_gps_statut.setPixmap(theme.pixmap("satellite", coul, 16))
            self.lbl_gps_statut.setToolTip(txt)
            self.lbl_gps_statut.setVisible(True)
        self._repolish(self.lbl_gps_badge)

    def _rafraichir_halos(self):
        """Réapplique les halos selon l'état courant (après construction et changement de thème)."""
        acc, direct = theme.pal()["accent"], theme.pal()["direct"]
        self._halo(self.cb_actif, acc if self.cb_actif.isChecked() else None)
        self._halo(self.cb_trace, acc if self.cb_trace.isChecked() else None)
        live = self.moteur.mode_source == MODE_LIVE
        self._halo(self.lbl_mode_chip, direct if live else acc, 22)
        if self.source_gps is not None:
            self._halo(self.cb_gps_actif, direct)
            self._halo(self.grp_gps, direct, 22)

    # ---- icônes et thème -----------------------------------------------------------------
    def _enregistrer_icones(self):
        """Associe chaque bouton / onglet à son icône (re-appliquée à chaque changement de thème)."""
        avec_texte = [('btn_mbtiles', 'couches'), ('btn_effacer_trace', 'vider'), ('btn_ajuster', 'ajuster'), ('btn_centrer_mobile', 'mobile'), ('btn_ajouter', 'plus'), ('btn_suppr', 'corbeille'), ('btn_vider', 'vider'), ('btn_centrer_pt', 'cible'), ('btn_ouvrir', 'dossier'), ('btn_enreg', 'disque'), ('btn_export_gpx', 'exporter'), ('btn_export_kml', 'exporter'), ('btn_alt_cible', 'monter'), ('btn_demarrer', 'lecture'), ('btn_pause', 'pause'), ('btn_arret', 'stop'), ('btn_reinit', 'reinit'), ('btn_sauter', 'saut'), ('btn_tle', 'telecharger'), ('btn_pause_h', 'pause'), ('btn_reinit_h', 'reinit')]
        seules = [('btn_monter', 'haut', 'Monter le point'), ('btn_descendre', 'bas', 'Descendre le point'), ('btn_ports', 'actualiser', 'Actualiser la liste des ports'), ('btn_fichier', 'dossier', 'Choisir le fichier…')]
        self._icones = []
        for attr, nom in avec_texte:
            b = getattr(self, attr)
            b.setIconSize(QSize(18, 18))
            self._icones.append((b, nom))
        for attr, nom, tip in seules:
            b = getattr(self, attr)
            b.setIconSize(QSize(18, 18)); b.setToolTip(tip); b.setProperty("icone", "seule")
            self._icones.append((b, nom))
        self.btn_theme.setIconSize(QSize(18, 18))
        self._onglets_icones = [(0, "liste"), (1, "prise"), (2, "journal")]
        self._appliquer_icones()

    def _appliquer_icones(self):
        for b, nom in self._icones:
            b.setIcon(theme.icone(nom, "primaire" if b.property("role") == "primaire" else ""))
        for i, nom in self._onglets_icones:
            self.onglets_sorties.setTabIcon(i, theme.icone(nom))
        sombre = theme.pal()["nom"] == "sombre"
        self.btn_theme.setIcon(theme.icone("soleil" if sombre else "lune"))      # l'icône montre le thème cible
        self.btn_theme.setText("Clair" if sombre else "Sombre")
        acc = theme.pal()["accent"]
        for ico, nom in ((self.ico_chrono, "chrono"), (self.ico_wp, "avion"), (self.ico_utc, "horloge")):
            ico.setPixmap(theme.pixmap(nom, acc, 20))
        self._marche_affiche = None                       # force le redessin du bouton Démarrer/Stop
        self._maj_bouton_marche()
        self._icones_docks()

    # ---- bouton bascule Démarrer / Stop et bandeau central ------------------------------
    def _marche_arret(self):
        if self.moteur.etat.etat in (EN_ROUTE, PAUSE):
            self.moteur.arreter()
        else:
            self._demarrer()

    def _maj_bouton_marche(self):
        """Vert « Démarrer » à l'arrêt, rouge « Stop » pendant la simulation (en route ou en pause)."""
        marche = self.moteur.etat.etat in (EN_ROUTE, PAUSE)
        if marche == self._marche_affiche:
            return
        self._marche_affiche = marche
        b = self.btn_demarrer_h
        b.setProperty("role", "stop" if marche else "primaire")
        b.setText("Stop" if marche else "Démarrer")
        b.setIcon(theme.icone("stop" if marche else "lecture", "stop" if marche else "primaire"))
        self._repolish(b)

    def _nom_point(self, i: int) -> str:
        pts = self.profil.points
        return (pts[i].nom or f"WP{i + 1}") if 0 <= i < len(pts) else "—"

    def _maj_bandeau(self):
        """Chrono de vol, point passé → point visé, heure UTC (GPS si disponible)."""
        e = self.moteur.etat
        t = int(self._chrono_s)
        self.lbl_chrono.setText(f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}")
        n = len(self.profil.points)
        if self.moteur.mode_source == MODE_LIVE:
            mo = self.moteur.offset
            phase = mo.phase if mo.config.pilotage == PILOTAGE_AUTO else "manuel"
            self.lbl_wp.setText(f"Direct · offset {mo.magnitude_courante:.0f} m · {phase}")
        elif n < 2:
            self.lbl_wp.setText("—")
        elif e.etat == TERMINE:
            self.lbl_wp.setText(f"{self._nom_point(n - 1)} · arrivé")
        else:
            cible = min(e.idx_cible, n - 1)
            prec = cible - 1 if cible > 0 else (n - 1 if self.profil.boucle else 0)
            self.lbl_wp.setText(f"{self._nom_point(prec)} → {self._nom_point(cible)}")
        f = self.moteur.dernier_fix if self.moteur.mode_source == MODE_LIVE else None
        if f is not None and f.utc is not None:
            self.lbl_utc.setText(f"{f.utc.astimezone(timezone.utc):%H:%M:%S} UTC · GPS")
        else:
            self.lbl_utc.setText(f"{e.temps.astimezone(timezone.utc):%H:%M:%S} UTC")

    # ---- barres d'outils et repli des panneaux -------------------------------------------
    def _restaurer_disposition(self):
        """Restaure taille et disposition sauvegardées, une fois la fenêtre entièrement construite.

        Restaurer en cours de construction (avant les tailles par défaut des panneaux et les halos)
        faisait planter Qt 6.10 sous Windows au premier affichage (violation d'accès) et laissait les
        tailles par défaut écraser les tailles sauvegardées. --reinit : disposition par défaut."""
        if os.environ.get("SIMUPOSITION_REINIT") != "1":
            s = self.settings
            geo_ = s.value("geometrie")
            if geo_ is not None:
                self.restoreGeometry(geo_)
            etat = s.value("etat_fenetre")
            if etat is not None:
                self.restoreState(etat)
        self._placer_barres()

    def _placer_barres(self):
        """Barre Simulation sur sa propre ligne, au-dessus de la barre carte (même après restoreState)."""
        for tb in (self.tb_sim, self.tb_carte):
            self.removeToolBar(tb)
        self.addToolBar(Qt.TopToolBarArea, self.tb_sim)
        self.addToolBarBreak(Qt.TopToolBarArea)
        self.addToolBar(Qt.TopToolBarArea, self.tb_carte)
        self.tb_sim.show(); self.tb_carte.show()

    _AIRES = {"gauche": Qt.LeftDockWidgetArea, "droite": Qt.RightDockWidgetArea, "bas": Qt.BottomDockWidgetArea}

    def _docks_zone(self, zone: str) -> list:
        tous = (self.dock_route, self.dock_cine, self.dock_gnss, self.dock_sorties, self.dock_live)
        return [d for d in tous if not d.isFloating() and self.dockWidgetArea(d) == self._AIRES[zone]]

    def _basculer_zone(self, zone: str):
        """Languette de la carte : replie (masque) ou déplie les panneaux d'un côté."""
        if zone not in self._AIRES:
            return
        docks = self._docks_zone(zone)
        ouverts = [d for d in docks if not d.isHidden()]
        if ouverts:
            self._zones_masquees[zone] = ouverts
            for d in ouverts:
                d.hide()
        else:
            for d in (self._zones_masquees.pop(zone, None) or docks):
                d.show()
        QTimer.singleShot(0, self._maj_fleches)

    def _maj_fleches(self):
        z = {}
        for zone in self._AIRES:
            docks = self._docks_zone(zone)
            z[zone] = any(not d.isHidden() for d in docks)
            z[zone + "_existe"] = bool(docks)
        self.pont.zones = z

    def _icones_docks(self):
        """Icônes sur les onglets de panneaux (visibles quand des panneaux sont regroupés en onglets)."""
        par_titre = {self.dock_route.windowTitle(): "route", self.dock_cine.windowTitle(): "cinematique",
                     self.dock_gnss.windowTitle(): "satellite", self.dock_sorties.windowTitle(): "sorties",
                     self.dock_live.windowTitle(): "mobile"}
        for tb in self.findChildren(QTabBar):
            if tb.parent() is not self:
                continue
            tb.setIconSize(QSize(18, 18))
            for i in range(tb.count()):
                nom = par_titre.get(tb.tabText(i))
                if nom:
                    tb.setTabIcon(i, theme.icone(nom))

    def _disposition(self, compacte: bool):
        """Étendue : route/cinématique à gauche, GNSS à droite. Compacte : trois panneaux en onglets à gauche."""
        for d in (self.dock_route, self.dock_cine, self.dock_gnss, self.dock_sorties, self.dock_live):
            d.setFloating(False)
            d.show()
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_route)
        if compacte:
            self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_cine)
            self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_gnss)
            self.tabifyDockWidget(self.dock_route, self.dock_cine)
            self.tabifyDockWidget(self.dock_cine, self.dock_gnss)
            self.tabifyDockWidget(self.dock_gnss, self.dock_live)
            self.dock_route.raise_()
            self.resizeDocks([self.dock_route], [440], Qt.Horizontal)
            self.resizeDocks([self.dock_route, self.dock_sorties], [650, 190], Qt.Vertical)
        else:
            self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_cine)
            self.splitDockWidget(self.dock_route, self.dock_cine, Qt.Vertical)
            self.addDockWidget(Qt.RightDockWidgetArea, self.dock_gnss)
            self.addDockWidget(Qt.RightDockWidgetArea, self.dock_live)
            self.tabifyDockWidget(self.dock_gnss, self.dock_live)
            self.dock_gnss.raise_()
            self.resizeDocks([self.dock_route, self.dock_cine], [360, 440], Qt.Vertical)
            self.resizeDocks([self.dock_route, self.dock_gnss], [470, 430], Qt.Horizontal)
            self.resizeDocks([self.dock_route, self.dock_sorties], [650, 190], Qt.Vertical)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_sorties)
        self._zones_masquees.clear()
        self._placer_barres()
        QTimer.singleShot(0, self._icones_docks)
        QTimer.singleShot(0, self._maj_fleches)

    def _basculer_theme(self):
        mode = "clair" if theme.pal()["nom"] == "sombre" else "sombre"
        for a in self._groupe_theme.actions():
            a.setChecked(a.text().lower() == mode)
        self._changer_theme(mode)

    def _changer_theme(self, mode: str):
        theme.appliquer(QApplication.instance(), mode, self._taille_police)
        self.settings.setValue("theme", mode)
        self._appliquer_icones()
        self.vue_ciel.update()
        for w in self.findChildren(QWidget):                    # re-polish des propriétés dynamiques (badge, rôles)
            if w.property("role") or w.property("etat") or w.property("icone") or w.property("chip") or w.property("gps") or w.property("actif") or w.property("mode"):
                w.style().unpolish(w); w.style().polish(w)
        self._rafraichir_halos()

    def _changer_taille(self, pt: int):
        self._taille_police = pt
        self.settings.setValue("police_pt", pt)
        self._changer_theme(str(self.settings.value("theme", "sombre")))

    @staticmethod
    def _defilant(w: QWidget) -> QScrollArea:
        """Contenu de panneau dans une zone défilante : jamais de chevauchement quand la fenêtre est basse."""
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(w)
        w.setMinimumWidth(300)
        return sa

    @staticmethod
    def _ligne(*widgets) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for x in widgets:
            h.addWidget(x)
        return w

    # =====================================================================================
    # Connexions
    # =====================================================================================
    def _connecter(self):
        # carte
        self.cmb_source.currentIndexChanged.connect(self._changer_source)
        self.btn_mbtiles.clicked.connect(self._choisir_mbtiles)
        self.cb_mode_ajout.toggled.connect(lambda on: setattr(self.pont, "modeAjout", on))
        self.cb_suivre.toggled.connect(lambda on: setattr(self.pont, "suivreMobile", on))
        self.btn_ajuster.clicked.connect(self.pont.ajusterRoute.emit)
        self.btn_centrer_mobile.clicked.connect(lambda: self.pont.recentrer.emit(self.moteur.etat.lat, self.moteur.etat.lon, -1))
        self.pont.carteCliquee.connect(self._clic_carte)
        self.pont.pointDeplaceSignal.connect(self._point_deplace)
        self.pont.pointSelectionneSignal.connect(self._selection_carte)
        self.pont.zoneBasculee.connect(self._basculer_zone)
        # route
        self.btn_ajouter.clicked.connect(self._ajouter_saisie)
        self.ed_coord.returnPressed.connect(self._ajouter_saisie)
        self.btn_suppr.clicked.connect(self._supprimer)
        self.btn_monter.clicked.connect(lambda: self._deplacer(-1))
        self.btn_descendre.clicked.connect(lambda: self._deplacer(+1))
        self.btn_vider.clicked.connect(self._vider)
        self.btn_centrer_pt.clicked.connect(self._centrer_point)
        self.btn_ouvrir.clicked.connect(self._ouvrir)
        self.btn_enreg.clicked.connect(self._enregistrer)
        self.btn_export_gpx.clicked.connect(lambda: self._exporter("gpx"))
        self.btn_export_kml.clicked.connect(lambda: self._exporter("kml"))
        self.table.itemChanged.connect(self._cellule_modifiee)
        self.table.itemSelectionChanged.connect(self._selection_table)
        # cinématique
        self.sp_vitesse.valueChanged.connect(self._maj_consignes)
        self.sp_taux.valueChanged.connect(self._maj_consignes)
        self.sp_alt_init.valueChanged.connect(self._maj_consignes)
        self.sp_geoide.valueChanged.connect(self._maj_consignes)
        self.cmb_u_vit.currentTextChanged.connect(self._changer_unites)
        self.cmb_u_taux.currentTextChanged.connect(self._changer_unites)
        self.cmb_u_alt.currentTextChanged.connect(self._changer_unites)
        self.cmb_interp.currentIndexChanged.connect(self._maj_consignes)
        self.cb_boucle.toggled.connect(self._maj_consignes)
        self.sp_incl.valueChanged.connect(self._maj_consignes)
        # curseurs ↔ champs chiffrés (vitesse sol, taux vertical, inclinaison)
        self.sl_vitesse.valueChanged.connect(lambda v: self.sp_vitesse.setValue(float(v)))
        self.sl_taux.valueChanged.connect(lambda v: self.sp_taux.setValue(float(v)))
        self.sl_incl.valueChanged.connect(lambda v: self.sp_incl.setValue(float(v)))
        self.sp_vitesse.valueChanged.connect(self._sync_curseurs)
        self.sp_taux.valueChanged.connect(self._sync_curseurs)
        self.sp_incl.valueChanged.connect(self._sync_curseurs)
        self.btn_alt_cible.clicked.connect(lambda: self.moteur.regler_alt_cible(vers_si(self.sp_alt_cible.value(), self.cmb_u_alt.currentText(), UNITES_ALT)))
        self.btn_demarrer.clicked.connect(self._demarrer)
        self.btn_demarrer.setShortcut(QKeySequence("F5"))
        self.btn_pause.clicked.connect(self.moteur.basculer_pause)
        self.btn_arret.clicked.connect(self.moteur.arreter)
        self.btn_reinit.clicked.connect(lambda: self.moteur.reinitialiser())
        self.btn_sauter.clicked.connect(self._sauter)
        # barre Simulation (haut) : mêmes actions que le panneau Cinématique
        self.btn_demarrer_h.clicked.connect(self._marche_arret)
        self.btn_pause_h.clicked.connect(self.moteur.basculer_pause)
        self.btn_reinit_h.clicked.connect(lambda: self.moteur.reinitialiser())
        # GNSS
        for sy, cb in self.cb_sys.items():
            cb.toggled.connect(lambda on, s=sy: self._maj_gnss())
        self.cmb_source_gnss.currentIndexChanged.connect(self._maj_gnss)
        self.btn_tle.clicked.connect(self._telecharger_tle)
        self.sp_masque.valueChanged.connect(self._maj_gnss)
        self.sp_canaux.valueChanged.connect(self._maj_gnss)
        self.sp_decroch.valueChanged.connect(self._maj_gnss)
        # sorties
        self.cmb_dialecte.currentIndexChanged.connect(self._maj_nmea)
        for cb in self.cb_phrases.values():
            cb.toggled.connect(self._maj_nmea)
        self.cb_serie.toggled.connect(self._basculer_serie)
        self.btn_ports.clicked.connect(self._rafraichir_ports)
        self.cb_tcp.toggled.connect(self._basculer_tcp)
        self.cb_udp.toggled.connect(self._basculer_udp)
        self.cb_fichier.toggled.connect(self._basculer_fichier)
        self.btn_fichier.clicked.connect(self._choisir_fichier)
        # halos d'enclenchement sur les boutons toggle
        self.cb_actif.toggled.connect(lambda on: self._halo(self.cb_actif, theme.pal()["accent"] if on else None))
        self.cb_trace.toggled.connect(lambda on: self._halo(self.cb_trace, theme.pal()["accent"] if on else None))
        self._rafraichir_ports()
        self._connecter_live()

    # =====================================================================================
    # Boucle de simulation
    # =====================================================================================
    def _tic(self):
        t = time.monotonic()
        dt = t - self._t_prec
        self._t_prec = t
        e = self.moteur.avancer(dt)
        # ciel recalculé toutes les secondes (satellites lents) ou au premier tic
        self._acc_ciel += dt
        if e.etat == EN_ROUTE:
            self._chrono_s += dt
        if self.ciel is None or self._acc_ciel >= 1.0:
            self._acc_ciel = 0.0
            h = e.alt_m + self.profil.ondulation_geoide_m
            self.ciel = self.constellations.ciel(e.lat, e.lon, h, e.temps)
            self.vue_ciel.maj(self.ciel.satellites, self.constellations.masque_deg)
            c = self.ciel
            fix = {0: "pas de fix", 2: "fix 2D", 3: "fix 3D"}.get(c.mode_3d if c.fix else 0, "—")
            self.lbl_gnss.setText(f"{len(c.satellites)} visibles / {c.nb_utilises} utilisés / {c.nb_suivis} à l'horizon  ·  {fix}\n"
                                  f"HDOP {c.hdop:.2f}  VDOP {c.vdop:.2f}  PDOP {c.pdop:.2f}  GDOP {c.gdop:.2f}")
        # émission NMEA : en SIM à la cadence choisie ; en LIVE à chaque nouvelle époque GPS reçue
        if self.moteur.mode_source == MODE_LIVE:
            if self.moteur.sortie_prete:
                self._emettre()
        else:
            self._acc_sortie += dt
            if self._acc_sortie >= 1.0 / self.cmb_cadence.currentData():
                self._acc_sortie = 0.0
                self._emettre()
        self._maj_trace()
        self._maj_live_etat()
        self._maj_affichage()

    def _solution(self) -> Solution:
        e = self.moteur.etat
        c = self.ciel
        return Solution(temps=e.temps, lat=e.lat, lon=e.lon, alt_msl_m=e.alt_m,
                        ondulation_m=self.profil.ondulation_geoide_m, vitesse_ms=e.vitesse_ms, cap=e.cap,
                        fix=c.fix if c else 1, mode_3d=c.mode_3d if c else 3,
                        pdop=c.pdop if c else 1.5, hdop=c.hdop if c else 0.9, vdop=c.vdop if c else 1.2,
                        satellites=c.satellites if c else [])

    def _emettre(self):
        if not self.cb_actif.isChecked():
            return
        sol = self._solution()
        trames = self.generateur.trames(sol)
        self.diffuseur.diffuser(trames)
        self.apercu.setPlainText("".join(trames).replace("\r\n", "\n").rstrip())
        if "tcp" in self.canaux:
            self.lbl_tcp.setText(f"{self.canaux['tcp'].nb_clients} client(s)")

    # =====================================================================================
    # Affichage
    # =====================================================================================
    def _maj_affichage(self):
        e = self.moteur.etat
        ua, uv, ut = self.cmb_u_alt.currentText(), self.cmb_u_vit.currentText(), self.cmb_u_taux.currentText()
        self.lbl_position.setText(f"{formater_dmm(e.lat, True)}  {formater_dmm(e.lon, False)}   ({e.lat:.6f}, {e.lon:.6f})")
        self.lbl_alt.setText(f"{depuis_si(e.alt_m, ua, UNITES_ALT):,.0f} {ua}".replace(",", " "))
        textes = {ARRET: "Arrêt", EN_ROUTE: "En route", PAUSE: "Pause", TERMINE: "Route terminée"}
        cible = ""
        if e.etat in (EN_ROUTE, PAUSE) and e.idx_cible < len(self.profil.points):
            p = self.profil.points[e.idx_cible]
            cible = f"  →  point {e.idx_cible + 1} {p.nom}"
        mode_txt = "  ·  Direct (GPS réel)" if self.moteur.mode_source == MODE_LIVE else "  ·  Simulation"
        self.lbl_etat.setText(f"{textes.get(e.etat, e.etat)}{cible}   ·   {e.temps.astimezone(timezone.utc).strftime('%H:%M:%S')} UTC{mode_txt}")
        if self.lbl_etat.property("etat") != e.etat:        # badge d'état coloré (re-polish du style)
            self.lbl_etat.setProperty("etat", e.etat)
            self.lbl_etat.style().unpolish(self.lbl_etat)
            self.lbl_etat.style().polish(self.lbl_etat)
        self.lbl_etat_h.setText(textes.get(e.etat, e.etat))   # badge de la barre Simulation (haut)
        if self.lbl_etat_h.property("etat") != e.etat:
            self.lbl_etat_h.setProperty("etat", e.etat)
            self._repolish(self.lbl_etat_h)
        reste = self.moteur.distance_restante_m()
        eta = self.moteur.temps_restant_s()
        eta_txt = "—" if eta == float("inf") or e.etat != EN_ROUTE else f"{int(eta // 60)} min {int(eta % 60):02d} s"
        self.lbl_cine.setText(f"cap {e.cap:05.1f}°  sol {depuis_si(e.vitesse_ms, uv, UNITES_VITESSE):.1f} {uv}  "
                              f"vz {depuis_si(e.vz_ms, ut, UNITES_TAUX):+.0f} {ut}\nreste {reste / geo.NM_M:.1f} NM ({reste / 1000:.1f} km)  ETA {eta_txt}")
        self.barre.setValue(int(self.moteur.progression() * 1000))
        self.btn_pause.setText("Reprendre" if e.etat == PAUSE else "Pause")
        self.btn_pause.setEnabled(e.etat in (EN_ROUTE, PAUSE))
        self.btn_arret.setEnabled(e.etat in (EN_ROUTE, PAUSE))
        self.btn_pause_h.setText(self.btn_pause.text())
        self.btn_pause_h.setEnabled(e.etat in (EN_ROUTE, PAUSE))
        self._maj_bouton_marche()
        self._maj_bandeau()
        self.pont.mobile = {"lat": e.lat, "lon": e.lon, "cap": e.cap, "valide": True}

    def _maj_table(self):
        self._maj_table_en_cours = True
        ua, uv, ut = self.cmb_u_alt.currentText(), self.cmb_u_vit.currentText(), self.cmb_u_taux.currentText()
        self.table.setHorizontalHeaderLabels(["Nom", "Latitude", "Longitude", f"Alt ({ua})", f"Vit. ({uv})", f"Taux ({ut})"])
        self.table.setRowCount(len(self.profil.points))
        for r, p in enumerate(self.profil.points):
            vals = [p.nom, f"{p.lat:.6f}", f"{p.lon:.6f}",
                    "" if p.alt_m is None else f"{depuis_si(p.alt_m, ua, UNITES_ALT):.0f}",
                    "" if p.vitesse_ms is None else f"{depuis_si(p.vitesse_ms, uv, UNITES_VITESSE):.1f}",
                    "" if p.taux_ms is None else f"{depuis_si(p.taux_ms, ut, UNITES_TAUX):.0f}"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c > 0:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)
        self._maj_table_en_cours = False
        L = self.profil.longueur_m()
        self.lbl_route.setText(f"{len(self.profil.points)} point(s) · {L / geo.NM_M:.1f} NM ({L / 1000:.1f} km)")

    def _couleur_point(self, i: int, n: int) -> str:
        """Départ (premier) vert, arrivée (dernier) rouge, intermédiaires turquoise."""
        if i == 0:
            return "#2ecc71"
        if i == n - 1:
            return "#ff5252"
        return "#2ec4b6"

    def _dicts_points(self) -> list:
        n = len(self.profil.points)
        return [{"lat": p.lat, "lon": p.lon, "nom": p.nom, "idx": i, "selectionne": i == self._idx_selection,
                 "couleur": self._couleur_point(i, n)} for i, p in enumerate(self.profil.points)]

    def _maj_carte_route(self):
        self.pont.route = [{"lat": la, "lon": lo} for la, lo in trace_lissee(self.profil)]
        self.pont.points = self._dicts_points()
        self._maj_table()

    # =====================================================================================
    # Route : édition
    # =====================================================================================
    def _ajouter_point(self, p: PointPassage):
        self.profil.points.append(p)
        if len(self.profil.points) == 1:
            self.moteur.reinitialiser()
        self._maj_carte_route()
        self._log(f"point {len(self.profil.points)} ajouté : {p.lat:.5f}, {p.lon:.5f}" + (f" ({p.nom})" if p.nom else ""))

    def _ajouter_saisie(self):
        try:
            lat, lon = analyser_paire(self.ed_coord.text())
        except ValueError as e:
            QMessageBox.warning(self, "Coordonnées", str(e))
            return
        alt = None
        if self.ed_alt.text().strip():
            try:
                alt = vers_si(float(self.ed_alt.text().replace(",", ".")), self.cmb_u_alt.currentText(), UNITES_ALT)
            except ValueError:
                QMessageBox.warning(self, "Altitude", "altitude illisible")
                return
        self._ajouter_point(PointPassage(lat, lon, alt, self.ed_nom.text().strip()))
        self.ed_coord.clear(); self.ed_nom.clear()

    def _clic_carte(self, lat: float, lon: float):
        if self._placement:                                # placement d'une balise d'offset prioritaire
            quel = self._placement
            self._poser_balise(quel, lat, lon)
            (self.btn_odepart if quel == "depart" else self.btn_oarrivee).setChecked(False)
            return
        if self.cb_mode_ajout.isChecked():
            self._ajouter_point(PointPassage(lat, lon, None, f"WP{len(self.profil.points) + 1}"))

    def _point_deplace(self, idx: int, lat: float, lon: float):
        if 0 <= idx < len(self.profil.points):
            self.profil.points[idx].lat, self.profil.points[idx].lon = lat, lon
            self._maj_carte_route()
            self._log(f"point {idx + 1} déplacé : {lat:.5f}, {lon:.5f}")

    def _selection_carte(self, idx: int):
        self._idx_selection = idx
        self.table.selectRow(idx)

    def _selection_table(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        self._idx_selection = next(iter(rows)) if rows else -1
        self.pont.points = self._dicts_points()

    def _cellule_modifiee(self, it: QTableWidgetItem):
        if self._maj_table_en_cours:
            return
        r, c = it.row(), it.column()
        if not (0 <= r < len(self.profil.points)):
            return
        p = self.profil.points[r]
        txt = it.text().strip().replace(",", ".")
        try:
            if c == 0:
                p.nom = it.text().strip()
            elif c == 1:
                p.lat = analyser_coordonnee(it.text(), True)
            elif c == 2:
                p.lon = analyser_coordonnee(it.text(), False)
            elif c == 3:
                p.alt_m = None if not txt else vers_si(float(txt), self.cmb_u_alt.currentText(), UNITES_ALT)
            elif c == 4:
                p.vitesse_ms = None if not txt else vers_si(float(txt), self.cmb_u_vit.currentText(), UNITES_VITESSE)
            elif c == 5:
                p.taux_ms = None if not txt else vers_si(float(txt), self.cmb_u_taux.currentText(), UNITES_TAUX)
        except ValueError:
            self.statusBar().showMessage("valeur illisible, ignorée", 4000)
        self._maj_carte_route()

    def _supprimer(self):
        r = self._idx_selection
        if 0 <= r < len(self.profil.points):
            del self.profil.points[r]
            self._idx_selection = -1
            self._maj_carte_route()

    def _deplacer(self, d: int):
        r = self._idx_selection
        r2 = r + d
        if 0 <= r < len(self.profil.points) and 0 <= r2 < len(self.profil.points):
            pts = self.profil.points
            pts[r], pts[r2] = pts[r2], pts[r]
            self._idx_selection = r2
            self._maj_carte_route()
            self.table.selectRow(r2)

    def _vider(self):
        if self.profil.points and QMessageBox.question(self, "Vider", "Supprimer tous les points ?") != QMessageBox.Yes:
            return
        self.profil.points.clear()
        self._idx_selection = -1
        self._maj_carte_route()

    def _centrer_point(self):
        r = self._idx_selection
        if 0 <= r < len(self.profil.points):
            p = self.profil.points[r]
            self.pont.recentrer.emit(p.lat, p.lon, -1)

    def _sauter(self):
        if 0 <= self._idx_selection < len(self.profil.points):
            self.moteur.sauter(self._idx_selection)

    # =====================================================================================
    # Profil : fichiers
    # =====================================================================================
    def _ouvrir(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Ouvrir un profil", self.settings.value("dossier", ""),
                                            "Profils (*.json *.gpx *.kml);;JSON (*.json);;GPX (*.gpx);;KML (*.kml)")
        if not fn:
            return
        self.settings.setValue("dossier", os.path.dirname(fn))
        try:
            ext = os.path.splitext(fn)[1].lower()
            if ext == ".gpx":
                self.profil.points = importer_gpx(fn)
            elif ext == ".kml":
                self.profil.points = importer_kml(fn)
            else:
                self.profil = Profil.charger(fn)
                self.moteur.profil = self.profil
                self._profil_vers_widgets()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Ouverture", f"Lecture impossible :\n{e}")
            return
        self._idx_selection = -1
        self.moteur.reinitialiser()
        self._live_vers_widgets()
        self._brancher_profil_moteur()
        self._maj_live_mode()
        self._maj_carte_route()
        self.pont.ajusterRoute.emit()
        self._log(f"profil chargé : {fn} ({len(self.profil.points)} points)")

    def _enregistrer(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Enregistrer le profil", os.path.join(self.settings.value("dossier", ""), "profil.json"), "JSON (*.json)")
        if not fn:
            return
        self.settings.setValue("dossier", os.path.dirname(fn))
        self._widgets_vers_profil()
        self.profil.enregistrer(fn)
        self._log(f"profil enregistré : {fn}")

    def _exporter(self, fmt: str):
        fn, _ = QFileDialog.getSaveFileName(self, f"Exporter en {fmt.upper()}", os.path.join(self.settings.value("dossier", ""), f"route.{fmt}"), f"{fmt.upper()} (*.{fmt})")
        if not fn:
            return
        (exporter_gpx if fmt == "gpx" else exporter_kml)(self.profil.points, fn, self.profil.nom)
        self._log(f"route exportée : {fn}")

    # =====================================================================================
    # Consignes et unités
    # =====================================================================================
    def _widgets_vers_profil(self):
        p = self.profil
        p.unite_vitesse, p.unite_taux, p.unite_alt = self.cmb_u_vit.currentText(), self.cmb_u_taux.currentText(), self.cmb_u_alt.currentText()
        p.vitesse_ms = vers_si(self.sp_vitesse.value(), p.unite_vitesse, UNITES_VITESSE)
        p.taux_ms = vers_si(self.sp_taux.value(), p.unite_taux, UNITES_TAUX)
        p.alt_initiale_m = vers_si(self.sp_alt_init.value(), p.unite_alt, UNITES_ALT)
        p.interpolation = self.cmb_interp.currentData()
        p.boucle = self.cb_boucle.isChecked()
        p.ondulation_geoide_m = self.sp_geoide.value()
        p.inclinaison_max_deg = self.sp_incl.value()

    def _profil_vers_widgets(self):
        p = self.profil
        for w in (self.sp_vitesse, self.sp_taux, self.sp_alt_init, self.sp_geoide, self.sp_incl, self.cmb_u_vit, self.cmb_u_taux, self.cmb_u_alt, self.cmb_interp, self.cb_boucle):
            w.blockSignals(True)
        self.cmb_u_vit.setCurrentText(p.unite_vitesse)
        self.cmb_u_taux.setCurrentText(p.unite_taux)
        self.cmb_u_alt.setCurrentText(p.unite_alt)
        self.sp_vitesse.setValue(depuis_si(p.vitesse_ms, p.unite_vitesse, UNITES_VITESSE))
        self.sp_taux.setValue(depuis_si(p.taux_ms, p.unite_taux, UNITES_TAUX))
        self.sp_alt_init.setValue(depuis_si(p.alt_initiale_m, p.unite_alt, UNITES_ALT))
        self.sp_geoide.setValue(p.ondulation_geoide_m)
        self.sp_incl.setValue(p.inclinaison_max_deg)
        self.cmb_interp.setCurrentIndex(0 if p.interpolation == geo.ORTHODROMIE else 1)
        self.cb_boucle.setChecked(p.boucle)
        for w in (self.sp_vitesse, self.sp_taux, self.sp_alt_init, self.sp_geoide, self.sp_incl, self.cmb_u_vit, self.cmb_u_taux, self.cmb_u_alt, self.cmb_interp, self.cb_boucle):
            w.blockSignals(False)
        self._maj_plages_curseurs()

    def _maj_consignes(self):
        self._widgets_vers_profil()
        self.moteur.regler_vitesse(self.profil.vitesse_ms)
        self.moteur.regler_taux(self.profil.taux_ms)
        if self.moteur.etat.etat == ARRET and self.moteur.etat.distance_m == 0.0:
            self.moteur.etat.alt_m = self.profil.alt_initiale_m if not (self.profil.points and self.profil.points[0].alt_m is not None) else self.profil.points[0].alt_m
        self._maj_carte_route()

    def _changer_unites(self):
        """Les valeurs SI ne changent pas : on réaffiche les champs dans la nouvelle unité.

        On fixe d'abord les unités du profil depuis les combos (sinon `_profil_vers_widgets`
        rétablirait l'ancienne unité), puis on réaffiche chaque valeur convertie."""
        p = self.profil
        p.unite_vitesse = self.cmb_u_vit.currentText()
        p.unite_taux = self.cmb_u_taux.currentText()
        p.unite_alt = self.cmb_u_alt.currentText()
        self._profil_vers_widgets()
        self._maj_table()

    # ---- curseurs (vitesse sol, taux vertical, inclinaison) ------------------------------
    _MAX_VIT = {"kt": 600, "km/h": 1100, "m/s": 300}        # plage des curseurs selon l'unité
    _MAX_TAUX = {"ft/min": 4000, "m/s": 20, "m/min": 1200}

    def _sync_curseurs(self):
        """Champ chiffré → curseur (sans réémettre : pas de boucle)."""
        if not hasattr(self, "sl_vitesse"):
            return
        for sl, sp in ((self.sl_vitesse, self.sp_vitesse), (self.sl_taux, self.sp_taux), (self.sl_incl, self.sp_incl)):
            sl.blockSignals(True)
            sl.setValue(int(round(min(sp.value(), sl.maximum()))))
            sl.blockSignals(False)

    def _maj_plages_curseurs(self):
        """Adapte la plage des curseurs de vitesse/taux à l'unité d'affichage courante."""
        if not hasattr(self, "sl_vitesse"):
            return
        self.sl_vitesse.blockSignals(True)
        self.sl_vitesse.setRange(0, self._MAX_VIT.get(self.cmb_u_vit.currentText(), 600))
        self.sl_vitesse.blockSignals(False)
        self.sl_taux.blockSignals(True)
        self.sl_taux.setRange(0, self._MAX_TAUX.get(self.cmb_u_taux.currentText(), 4000))
        self.sl_taux.blockSignals(False)
        self._sync_curseurs()

    # ---- trace du vol (chemin réellement parcouru) ---------------------------------------
    TRACE_PAS_MIN_M = 20.0           # un point tous les 20 m au moins
    TRACE_MAX_PTS = 12000

    def _effacer_trace(self):
        self._trace_dernier = None
        self._trace_n = 0
        self._trace_reel_dernier = None
        self._trace_reel_n = 0
        self.pont.effacer_trace()
        self.pont.effacer_trace_reelle()

    def _ajout_point_trace(self, lat: float, lon: float, reel: bool):
        """Ajoute un point à une trace (émise bleue ou réelle verte) avec décimation à ≥ 20 m et
        repli circulaire quand la trace devient très longue."""
        if reel:
            dern, n = self._trace_reel_dernier, self._trace_reel_n
            ajouter, effacer, lire = self.pont.ajouter_trace_reelle, self.pont.effacer_trace_reelle, self.pont.trace_reelle
        else:
            dern, n = self._trace_dernier, self._trace_n
            ajouter, effacer, lire = self.pont.ajouter_trace, self.pont.effacer_trace, self.pont.trace_vol
        if dern is not None:
            dy = (lat - dern[0]) * 111320.0
            dx = (lon - dern[1]) * 111320.0 * math.cos(math.radians(lat))
            if dx * dx + dy * dy < self.TRACE_PAS_MIN_M ** 2:
                return
        if n >= self.TRACE_MAX_PTS:                        # trace très longue : on repart des derniers points
            reste = lire()[-self.TRACE_MAX_PTS // 4:]
            effacer()
            for la, lo in reste:
                ajouter(la, lo)
            n = len(reste)
        ajouter(lat, lon)
        if reel:
            self._trace_reel_dernier, self._trace_reel_n = (lat, lon), n + 1
        else:
            self._trace_dernier, self._trace_n = (lat, lon), n + 1

    def _maj_trace(self):
        """Trace émise (bleu = EtatSim) et, en mode LIVE, trace GPS réelle (vert = dernier fix)."""
        if not self.cb_trace.isChecked() or self.moteur.etat.etat != EN_ROUTE:
            return
        e = self.moteur.etat
        self._ajout_point_trace(e.lat, e.lon, reel=False)
        if self.moteur.mode_source == MODE_LIVE and self.moteur.dernier_fix is not None:
            f = self.moteur.dernier_fix
            self._ajout_point_trace(f.lat, f.lon, reel=True)

    def _demarrer(self):
        self._widgets_vers_profil()
        if self.moteur.demarrer():
            self._log(f"démarrage : {self.profil.vitesse_ms / geo.KT_MS:.0f} kt, taux {self.profil.taux_ms / geo.FT_M * 60:.0f} ft/min, {self.profil.interpolation}")

    def _evt_moteur(self, evt: str, info: dict):
        if evt == "point_atteint":
            self._log(f"point {info['idx'] + 1} atteint" + (f" ({info['nom']})" if info.get("nom") else ""))
        elif evt == "virage_serre":
            self._log(f"virage serré au point {info['idx'] + 1}" + (f" ({info['nom']})" if info.get("nom") else "")
                      + f" : tronçons trop courts pour l'inclinaison demandée → inclinaison équivalente ≈ {info['incl_deg']:.0f}°"
                      + " (réduire la vitesse ou espacer les points)")
        elif evt == "interception_serree":
            self._log(f"interception serrée ({info.get('phase', '')}) : angle équivalent ≈ {info['angle_deg']:.0f}° "
                      "— réduire l'offset, allonger le temps, ou espacer les waypoints")
        elif evt == "termine":
            self._log("route terminée : mobile immobile au dernier point")
        elif evt == "erreur":
            QMessageBox.warning(self, "Simulation", info.get("msg", ""))
        elif evt in ("pause", "reprise", "arret"):
            self._log(evt)
        elif evt == "reinitialise" and hasattr(self, "pont"):
            self._chrono_s = 0.0
            self._effacer_trace()

    # =====================================================================================
    # Mode LIVE (GPS réel + offset)
    # =====================================================================================
    _UNITE_MAG = {"NM": geo.NM_M, "km": 1000.0}
    ECART_ALIGNE_M = 15.0          # en deçà, positions réelle et émise considérées confondues (halo vert)

    def _connecter_live(self):
        self.cmb_mode.currentIndexChanged.connect(self._maj_live_mode)
        self.cmb_gps_transport.currentIndexChanged.connect(self._maj_gps_transport)
        self.btn_gps_ports.clicked.connect(self._rafraichir_ports_gps)
        self.cb_gps_actif.toggled.connect(self._basculer_source_gps)
        self.cmb_pilotage.currentIndexChanged.connect(self._maj_pilotage)
        self.cmb_otype.currentIndexChanged.connect(self._maj_type_offset)
        for wdg in (self.sp_omag, self.sp_orelev, self.sp_overt, self.sp_tsep, self.sp_tret,
                    self.sp_tmanuel, self.sp_angle_garde):
            wdg.valueChanged.connect(self._maj_offset)
        for cmb in (self.cmb_omag_unite, self.cmb_overt_unite, self.cmb_ocote):
            cmb.currentIndexChanged.connect(self._maj_offset)
        self.btn_odepart.toggled.connect(lambda on: self._armer_placement("depart", on))
        self.btn_oarrivee.toggled.connect(lambda on: self._armer_placement("arrivee", on))
        self.pont.pointOffsetDeplaceSignal.connect(
            lambda lab, la, lo: self._poser_balise("depart" if lab == "D" else "arrivee", la, lo))
        self.btn_oappliquer.clicked.connect(self._appliquer_offset_manuel)
        self.btn_oannuler.clicked.connect(lambda: self.moteur.offset.annuler_manuel())
        self.btn_omoins.clicked.connect(lambda: self._nudge_offset(-1))
        self.btn_oplus.clicked.connect(lambda: self._nudge_offset(+1))
        self._rafraichir_ports_gps()
        self._maj_type_offset()
        self._maj_pilotage()
        self._maj_live_mode()

    def _mag_unite_m(self) -> float:
        return self._UNITE_MAG.get(self.cmb_omag_unite.currentText(), geo.NM_M)

    def _rafraichir_ports_gps(self):
        courant = self.cmb_gps_port.currentText()
        self.cmb_gps_port.clear()
        self.cmb_gps_port.addItems(ports_serie())
        if courant:
            self.cmb_gps_port.setCurrentText(courant)

    def _maj_gps_transport(self):
        t = self.cmb_gps_transport.currentData()
        self.ligne_serie.setEnabled(t == "serie")
        self.lbl_gps_hote.setEnabled(t == "tcp")
        self.ed_gps_hote.setEnabled(t == "tcp")

    def _maj_live_mode(self):
        live = self.cmb_mode.currentData() == MODE_LIVE
        self.moteur.definir_mode(MODE_LIVE if live else MODE_SIM)
        self.grp_gps.setEnabled(live)
        self.grp_offset.setEnabled(live)
        # pastille de mode + bordures colorées + halo (Simu bleu / Direct vert)
        acc, direct = theme.pal()["accent"], theme.pal()["direct"]
        self.lbl_mode_chip.setText("DIRECT (GPS réel)" if live else "SIMULATION")
        self.lbl_mode_chip.setProperty("chip", "live" if live else "sim")
        self._repolish(self.lbl_mode_chip)
        self._halo(self.lbl_mode_chip, direct if live else acc, 22)
        self.grp_gps.setProperty("actif", "live" if live else "")
        self.grp_offset.setProperty("actif", "live" if live else "")
        self._repolish(self.grp_gps); self._repolish(self.grp_offset)
        self._halo(self.grp_offset, None)
        if not live:
            self._annuler_placement()
            if self.cb_gps_actif.isChecked():
                self.cb_gps_actif.setChecked(False)       # ferme la source en quittant le mode Direct
            self.pont.mobileReel = {"lat": 0.0, "lon": 0.0, "cap": 0.0, "valide": False, "aligne": False}
            self._halo(self.grp_gps, None)
            self._maj_gps_badge("off")
        self._maj_offset()
        self._maj_pointsOffset()

    def _maj_pilotage(self):
        auto = self.cmb_pilotage.currentData() == PILOTAGE_AUTO
        if not auto:
            self._annuler_placement()
        self.w_auto.setVisible(auto)
        self.w_manuel.setVisible(not auto)
        self.moteur.offset.reinit()                        # changement de pilotage → on repart proprement
        self._maj_offset()
        self._maj_pointsOffset()

    def _maj_type_offset(self):
        t = self.cmb_otype.currentData()
        self.cmb_ocote.blockSignals(True)
        self.cmb_ocote.clear()
        if t == OFFSET_LATERAL:
            self.cmb_ocote.addItem("Droite", 1); self.cmb_ocote.addItem("Gauche", -1)
            self.lbl_ocote.setText("Côté"); self.cmb_ocote.setEnabled(True)
        elif t == OFFSET_LONGITUDINAL:
            self.cmb_ocote.addItem("Avant", 1); self.cmb_ocote.addItem("Arrière", -1)
            self.lbl_ocote.setText("Sens"); self.cmb_ocote.setEnabled(True)
        else:
            self.cmb_ocote.addItem("—", 1); self.cmb_ocote.setEnabled(False)
        self.cmb_ocote.blockSignals(False)
        rel = t == OFFSET_RELEVEMENT
        self.lbl_orelev.setEnabled(rel); self.sp_orelev.setEnabled(rel)
        self._maj_offset()

    def _maj_offset(self):
        if not hasattr(self, "cmb_mode"):
            return
        o = self.profil.offset
        o.pilotage = self.cmb_pilotage.currentData()
        o.type_offset = self.cmb_otype.currentData()
        o.magnitude_m = self.sp_omag.value() * self._mag_unite_m()
        o.unite_magnitude = self.cmb_omag_unite.currentText()
        o.signe = int(self.cmb_ocote.currentData() or 1)
        o.relevement_deg = self.sp_orelev.value()
        o.unite_vertical = self.cmb_overt_unite.currentText()
        o.vertical_m = vers_si(self.sp_overt.value(), o.unite_vertical, UNITES_ALT)
        o.t_separation_s = self.sp_tsep.value()
        o.t_retrouvaille_s = self.sp_tret.value()
        o.t_manuel_s = self.sp_tmanuel.value()
        o.angle_garde_deg = self.sp_angle_garde.value()
        self.moteur.offset.config = o
        self.profil.mode_source = self.cmb_mode.currentData()

    def _appliquer_offset_manuel(self):
        self._maj_offset()
        self.moteur.offset.appliquer_manuel()

    def _nudge_offset(self, sens: int):
        self.sp_omag.setValue(max(0.0, self.sp_omag.value() + sens * 0.1))   # pas de 0,1 dans l'unité courante
        self._maj_offset()
        self.moteur.offset.appliquer_manuel()

    ACCROCHE_TRAJET_M = 400.0      # une balise posée à moins de cette distance de la route s'y accroche

    def _armer_placement(self, quel: str, on: bool):
        """Bouton « Placer … sur la carte » : le prochain clic carte pose la balise."""
        if on:
            autre = self.btn_oarrivee if quel == "depart" else self.btn_odepart
            autre.blockSignals(True); autre.setChecked(False); autre.blockSignals(False)
            self._placement = quel
            self.statusBar().showMessage(f"Cliquez sur la carte pour placer {'le départ (D)' if quel == 'depart' else 'l’arrivée (A)'}")
        elif self._placement == quel:
            self._placement = ""
            self.statusBar().clearMessage()
        self.pont.modePlacement = self._placement

    def _annuler_placement(self):
        for b in (self.btn_odepart, self.btn_oarrivee):
            b.setChecked(False)

    def _poser_balise(self, quel: str, lat: float, lon: float):
        """Pose la balise D ou A ; accrochage au trajet prévu si le point est proche de la route."""
        trajet = trace_lissee(self.profil, 200.0) if len(self.profil.points) >= 2 else []
        la, lo, d = geo.point_le_plus_proche(lat, lon, trajet)
        accroche = bool(trajet) and d <= self.ACCROCHE_TRAJET_M
        pos = (la, lo) if accroche else (lat, lon)
        o = self.profil.offset
        if quel == "depart":
            o.depart = pos
        else:
            o.arrivee = pos
        self.moteur.offset.reinit()
        self._maj_pointsOffset()
        nom = "départ (D)" if quel == "depart" else "arrivée (A)"
        self._log(f"balise {nom} : {pos[0]:.5f}, {pos[1]:.5f}" + (f" — accrochée au trajet ({d:.0f} m)" if accroche else " — hors trajet"))
        if o.depart and o.arrivee:
            ecart = geo.inverse(o.depart[0], o.depart[1], o.arrivee[0], o.arrivee[1]).distance
            if ecart < 2.0 * o.rayon_capture_m:
                self._log(f"attention : départ et arrivée distants de {ecart:.0f} m seulement — l'offset n'aura pas le temps de s'établir")

    def _maj_pointsOffset(self):
        if not hasattr(self, "cmb_mode"):
            return
        o = self.profil.offset
        pts = []
        if o.pilotage == PILOTAGE_AUTO:                    # balises visibles dès le pilotage Auto
            if o.depart:
                pts.append({"lat": o.depart[0], "lon": o.depart[1], "label": "D", "couleur": "#2ecc71"})
            if o.arrivee:
                pts.append({"lat": o.arrivee[0], "lon": o.arrivee[1], "label": "A", "couleur": "#ff5252"})
        self.pont.pointsOffset = pts
        d = "—" if not o.depart else f"{o.depart[0]:.4f}, {o.depart[1]:.4f}"
        a = "—" if not o.arrivee else f"{o.arrivee[0]:.4f}, {o.arrivee[1]:.4f}"
        self.lbl_owp.setText(f"départ {d}\narrivée {a}")

    def _config_gps(self):
        c = self.profil.source_gps
        c.transport = self.cmb_gps_transport.currentData()
        c.port_serie = self.cmb_gps_port.currentText().strip()
        c.vitesse_serie = self.cmb_gps_baud.currentData()
        c.hote = self.ed_gps_hote.text().strip() or "127.0.0.1"
        c.port_reseau = self.sp_gps_port.value()
        return c

    def _basculer_source_gps(self, on: bool):
        if on:
            cfg = self._config_gps()
            cfg.actif = True
            self.source_gps = creer_source(cfg, self._log)
            self.source_gps.ouvrir()
            self.moteur.brancher_source(self.source_gps)
            self.pont.effacer_trace_reelle()
            self._trace_reel_dernier, self._trace_reel_n = None, 0
            self._halo(self.cb_gps_actif, theme.pal()["direct"], 18)
            self._halo(self.grp_gps, theme.pal()["direct"], 22)
            self._maj_gps_badge("wait")
            self._log(f"entrée GPS ouverte : {self.source_gps.description}")
        else:
            if self.source_gps is not None:
                self.source_gps.fermer()
                self._log(f"entrée GPS fermée : {self.source_gps.description}")
            self.source_gps = None
            self.moteur.brancher_source(None)
            self.profil.source_gps.actif = False
            self._halo(self.cb_gps_actif, None)
            self._halo(self.grp_gps, None)
            self._maj_gps_badge("off")

    def _maj_live_etat(self):
        if self.moteur.mode_source != MODE_LIVE:
            return
        f = self.moteur.dernier_fix
        if f is not None and self.source_gps is not None:
            age = self.source_gps.age_s()
            capte = age < 3.0
            self._maj_gps_badge("ok" if capte else "lost")
            self.lbl_gps_etat.setText(f"fix {'capté' if capte else f'perdu ({age:.0f} s)'}  ·  {f.lat:.5f}, {f.lon:.5f}\n"
                                      f"sol {(f.sog_ms or 0.0) / geo.KT_MS:.0f} kt  cap {(f.cog_deg or 0.0):.0f}°")
            e = self.moteur.etat                      # position émise ; confondue avec la réelle si écart < 15 m
            ecart = geo.inverse(f.lat, f.lon, e.lat, e.lon).distance
            self.pont.mobileReel = {"lat": f.lat, "lon": f.lon, "cap": f.cog_deg or 0.0, "valide": True,
                                    "aligne": ecart < self.ECART_ALIGNE_M}
        elif self.source_gps is not None:
            self._maj_gps_badge("wait")
            self.lbl_gps_etat.setText("en attente d'un fix…")
        mo = self.moteur.offset
        pil = "auto" if mo.config.pilotage == PILOTAGE_AUTO else "manuel"
        phase = mo.phase if mo.config.pilotage == PILOTAGE_AUTO else "—"
        self.lbl_offset_etat.setText(f"offset appliqué {mo.magnitude_courante:.0f} m  ·  {pil}  ·  phase {phase}")
        self._halo(self.grp_offset, theme.pal()["avert"] if mo.magnitude_courante > 5.0 else None, 20)

    def _live_vers_widgets(self):
        p = self.profil
        blocs = (self.cmb_mode, self.cmb_gps_transport, self.cmb_gps_port, self.cmb_gps_baud, self.ed_gps_hote,
                 self.sp_gps_port, self.cmb_pilotage, self.cmb_otype, self.sp_omag, self.cmb_omag_unite,
                 self.cmb_ocote, self.sp_orelev, self.sp_overt, self.cmb_overt_unite, self.sp_tsep, self.sp_tret,
                 self.sp_tmanuel, self.sp_angle_garde)
        for w in blocs:
            w.blockSignals(True)
        self.cmb_mode.setCurrentIndex(max(0, self.cmb_mode.findData(p.mode_source)))
        g = p.source_gps
        self.cmb_gps_transport.setCurrentIndex(max(0, self.cmb_gps_transport.findData(g.transport)))
        self.cmb_gps_port.setCurrentText(g.port_serie)
        self.cmb_gps_baud.setCurrentText(str(g.vitesse_serie))
        self.ed_gps_hote.setText(g.hote)
        self.sp_gps_port.setValue(g.port_reseau)
        o = p.offset
        self.cmb_pilotage.setCurrentIndex(max(0, self.cmb_pilotage.findData(o.pilotage)))
        self.cmb_otype.setCurrentIndex(max(0, self.cmb_otype.findData(o.type_offset)))
        self.cmb_omag_unite.setCurrentText(o.unite_magnitude if o.unite_magnitude in ("NM", "km") else "NM")
        self.sp_omag.setValue(o.magnitude_m / self._mag_unite_m())
        self._maj_type_offset()                            # peuple cmb_ocote selon le type
        self.cmb_ocote.setCurrentIndex(max(0, self.cmb_ocote.findData(o.signe)))
        self.sp_orelev.setValue(o.relevement_deg)
        uv = o.unite_vertical if o.unite_vertical in ("ft", "m") else "ft"
        self.cmb_overt_unite.setCurrentText(uv)
        self.sp_overt.setValue(depuis_si(o.vertical_m, uv, UNITES_ALT))
        self.sp_tsep.setValue(o.t_separation_s)
        self.sp_tret.setValue(o.t_retrouvaille_s)
        self.sp_tmanuel.setValue(o.t_manuel_s)
        self.sp_angle_garde.setValue(o.angle_garde_deg)
        for w in blocs:
            w.blockSignals(False)
        self._maj_gps_transport()
        self._maj_pilotage()
        self._maj_offset()

    def _widgets_vers_live(self):
        self._maj_offset()
        self._config_gps()

    def _brancher_profil_moteur(self):
        self.moteur.profil = self.profil
        self.moteur.offset.config = self.profil.offset
        self.moteur.offset.mode = self.profil.interpolation
        self.moteur.definir_mode(self.profil.mode_source)

    # =====================================================================================
    # Carte : sources
    # =====================================================================================
    def _changer_source(self):
        cle = self.cmb_source.currentData()
        if not cle:
            return
        if cle in SOURCES:
            zmax = SOURCES[cle][2]
        else:
            mb = self.proxy.mbtiles.get(cle)
            zmax = mb.zooms()[1] if mb else 19
        self.pont.definir_source(cle, self.proxy.url_source(cle), zmax)
        self.settings.setValue("source", cle)

    def _choisir_mbtiles(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Ouvrir un MBTiles", self.settings.value("dossier_mbt", ""), "MBTiles (*.mbtiles)")
        if not fn:
            return
        self.settings.setValue("dossier_mbt", os.path.dirname(fn))
        self._ajouter_mbtiles(fn, selectionner=True)

    def _ajouter_mbtiles(self, fn: str, selectionner: bool):
        try:
            cle = self.proxy.ajouter_mbtiles(fn)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "MBTiles", f"Ouverture impossible :\n{e}")
            return
        if self.cmb_source.findData(cle) < 0:
            self.cmb_source.addItem(f"MBTiles : {os.path.basename(fn)}", cle)
        mbts = self.settings.value("mbtiles", []) or []
        if isinstance(mbts, str):
            mbts = [mbts]
        if fn not in mbts:
            self.settings.setValue("mbtiles", list(mbts) + [fn])
        if selectionner:
            self.cmb_source.setCurrentIndex(self.cmb_source.findData(cle))
            c = self.proxy.mbtiles[cle].centre()
            if c:
                self.pont.recentrer.emit(c[0], c[1], max(2, self.proxy.mbtiles[cle].zooms()[0]))
        self._log(f"MBTiles : {fn}")

    # =====================================================================================
    # GNSS
    # =====================================================================================
    def _maj_gnss(self):
        c = self.constellations
        for sy, cb in self.cb_sys.items():
            c.actifs[sy] = cb.isChecked()
        c.source = self.cmb_source_gnss.currentData()
        c.masque_deg = self.sp_masque.value()
        c.max_canaux = self.sp_canaux.value()
        c.decrochage_pct = self.sp_decroch.value()
        if c.source == "tle" and not c._tle:
            n = c.charger_cache_tle()
            if n == 0:
                self._log("aucune TLE en cache : constellation nominale utilisée (téléchargez les TLE)")
        self._acc_ciel = 9.0
        self._maj_lbl_tle()

    def _maj_lbl_tle(self):
        age = age_cache_tle(self.constellations.dossier_cache)
        self.lbl_tle.setText("aucune TLE en cache" if age is None else f"TLE en cache : {age:.1f} j")

    def _telecharger_tle(self):
        self.btn_tle.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            n = self.constellations.telecharger(journal=self._log)
            self._log(f"{n} satellites chargés depuis les TLE")
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_tle.setEnabled(True)
        self._maj_lbl_tle()
        self._acc_ciel = 9.0

    # =====================================================================================
    # Sorties
    # =====================================================================================
    def _maj_nmea(self):
        self.generateur.dialecte = self.cmb_dialecte.currentData()
        self.generateur.phrases = {ph for ph, cb in self.cb_phrases.items() if cb.isChecked()}

    def _rafraichir_ports(self):
        courant = self.cmb_port.currentText()
        self.cmb_port.clear()
        self.cmb_port.addItems(ports_serie())
        if courant:
            self.cmb_port.setCurrentText(courant)

    def _basculer_canal(self, cle: str, on: bool, fabrique, case: QCheckBox):
        if on:
            canal = fabrique()
            if self.diffuseur.ajouter(canal):
                self.canaux[cle] = canal
            else:
                case.blockSignals(True); case.setChecked(False); case.blockSignals(False)
        else:
            canal = self.canaux.pop(cle, None)
            if canal is not None:
                self.diffuseur.retirer(canal)

    def _basculer_serie(self, on: bool):
        self._basculer_canal("serie", on, lambda: CanalSerie(self.cmb_port.currentText(), self.cmb_baud.currentData(), self._log), self.cb_serie)

    def _basculer_tcp(self, on: bool):
        self._basculer_canal("tcp", on, lambda: CanalTCPServeur("0.0.0.0", self.sp_tcp.value(), self._log), self.cb_tcp)
        if not on:
            self.lbl_tcp.setText("0 client")

    def _basculer_udp(self, on: bool):
        self._basculer_canal("udp", on, lambda: CanalUDP(self.ed_udp_hote.text().strip(), self.sp_udp.value(), self._log), self.cb_udp)

    def _basculer_fichier(self, on: bool):
        if on and not self.ed_fichier.text().strip():
            self._choisir_fichier()
            if not self.ed_fichier.text().strip():
                self.cb_fichier.blockSignals(True); self.cb_fichier.setChecked(False); self.cb_fichier.blockSignals(False)
                return
        self._basculer_canal("fichier", on, lambda: CanalFichier(self.ed_fichier.text().strip(), self._log), self.cb_fichier)

    def _choisir_fichier(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Journal NMEA", os.path.join(self.settings.value("dossier", ""), "simuposition.nmea"), "NMEA (*.nmea *.txt *.log)")
        if fn:
            self.ed_fichier.setText(fn)

    # =====================================================================================
    # Divers
    # =====================================================================================
    def _log(self, msg: str):
        self.journal.appendPlainText(time.strftime("%H:%M:%S ") + msg)

    def _a_propos(self):
        QMessageBox.information(self, "SimuPosition", f"SimuPosition {__version__}\nSimulateur GNSS à trajectoire sur carte.\n"
                                "Carte : QtLocation + proxy de tuiles local (IGN, OpenTopoMap, OSM, Esri, MBTiles).\n"
                                "Sorties : NMEA 0183 (COM, TCP, UDP, fichier).\n© skydev 2026")

    # ---- réglages persistants -------------------------------------------------------
    def _charger_reglages(self):
        s = self.settings
        try:
            self.profil = Profil.depuis_dict(json.loads(s.value("profil", "{}")))
        except Exception:  # noqa: BLE001
            self.profil = Profil()
        if not self.profil.points:
            self.profil.points = [PointPassage(49.0287, 1.2199, 140.0, "LFOE"), PointPassage(49.0450, 1.2500, 450.0, "Vent arrière"),
                                  PointPassage(49.0200, 1.2700, 450.0, "Base"), PointPassage(49.0287, 1.2199, 140.0, "Retour")]
        self.moteur.profil = self.profil
        self._profil_vers_widgets()
        self.moteur.reinitialiser()
        self._live_vers_widgets()
        self._brancher_profil_moteur()
        self._maj_live_mode()
        mbts = s.value("mbtiles", []) or []
        if isinstance(mbts, str):          # QSettings Windows : une liste à un élément revient en str
            mbts = [mbts]
        for fn in mbts:
            if os.path.exists(fn):
                self._ajouter_mbtiles(fn, selectionner=False)
        cle = s.value("source", "ign_plan")
        i = self.cmb_source.findData(cle)
        self.cmb_source.setCurrentIndex(max(0, i))
        self._changer_source()
        self.cmb_dialecte.setCurrentIndex(max(0, self.cmb_dialecte.findData(s.value("dialecte", "GN"))))
        self.cmb_cadence.setCurrentIndex(max(0, self.cmb_cadence.findData(float(s.value("cadence", 1.0)))))
        self.cmb_port.setCurrentText(s.value("port", ""))
        self.cmb_baud.setCurrentText(str(s.value("baud", "9600")))
        self.sp_tcp.setValue(int(s.value("tcp", 10110)))
        self.ed_udp_hote.setText(s.value("udp_hote", "127.0.0.1"))
        self.sp_udp.setValue(int(s.value("udp_port", 10111)))
        self.ed_fichier.setText(s.value("fichier", ""))
        self.cmb_source_gnss.setCurrentIndex(max(0, self.cmb_source_gnss.findData(s.value("gnss_source", "nominal"))))
        self.sp_masque.setValue(float(s.value("masque", 5.0)))
        self._maj_nmea()
        self._maj_gnss()
        vue = s.value("vue")
        if vue:
            try:
                lat, lon, z = [float(x) for x in vue.split(",")]
                self.pont.recentrer.emit(lat, lon, z)
            except ValueError:
                pass
        self.pont.vueChangeeSignal.connect(lambda la, lo, z: setattr(self, "_vue", (la, lo, z)))
        self._vue = None

    def closeEvent(self, ev):
        s = self.settings
        self._widgets_vers_profil()
        self._widgets_vers_live()
        if self.source_gps is not None:
            self.source_gps.fermer()
        s.setValue("profil", json.dumps(self.profil.vers_dict()))
        s.setValue("dialecte", self.cmb_dialecte.currentData())
        s.setValue("cadence", self.cmb_cadence.currentData())
        s.setValue("port", self.cmb_port.currentText())
        s.setValue("baud", self.cmb_baud.currentText())
        s.setValue("tcp", self.sp_tcp.value())
        s.setValue("udp_hote", self.ed_udp_hote.text())
        s.setValue("udp_port", self.sp_udp.value())
        s.setValue("fichier", self.ed_fichier.text())
        s.setValue("gnss_source", self.cmb_source_gnss.currentData())
        s.setValue("masque", self.sp_masque.value())
        s.setValue("geometrie", self.saveGeometry())
        s.setValue("etat_fenetre", self.saveState())
        if getattr(self, "_vue", None):
            s.setValue("vue", ",".join(f"{v:.6f}" for v in self._vue))
        self._timer.stop()
        self.diffuseur.tout_fermer()
        self.proxy.arreter()
        # décharge le QML avant la destruction du pont (sinon avalanche de TypeError « null »)
        self.carte.setSource(QUrl())
        super().closeEvent(ev)
