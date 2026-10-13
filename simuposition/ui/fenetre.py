# -*- coding: utf-8 -*-
"""Fenêtre principale de SimuPosition (PySide6 + carte QML QtLocation)."""
from __future__ import annotations

import json
import os
import time
from datetime import timezone

from PySide6.QtCore import Qt, QTimer, QSettings, QSize, QUrl, QStandardPaths, QLocale
from PySide6.QtGui import QAction, QActionGroup, QFont, QKeySequence
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
    QAbstractItemView, QScrollArea, QSizePolicy, QTabBar, QTabWidget,
)

from .. import __version__
from ..core import geodesie as geo
from ..core.modele import (Profil, PointPassage, UNITES_VITESSE, UNITES_TAUX, UNITES_ALT, vers_si, depuis_si,
                           analyser_paire, analyser_coordonnee, formater_dmm, importer_gpx, exporter_gpx,
                           importer_kml, exporter_kml)
from . import theme
from ..core.moteur import Moteur, ARRET, EN_ROUTE, PAUSE, TERMINE, trace_lissee
from ..core.constellation import Constellations, SYSTEMES, age_cache_tle
from ..map.proxy_tuiles import ProxyTuiles, SOURCES
from ..outputs.nmea import GenerateurNMEA, Solution, PHRASES
from ..outputs.canaux import CanalSerie, CanalTCPServeur, CanalUDP, CanalFichier, Diffuseur, ports_serie
from .pont_carte import PontCarte
from .ciel import VueCiel

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
        self.cache = dossier_cache()
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
        self._idx_selection = -1
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
        # --- barre d'outils carte
        tb = QToolBar("Carte")
        tb.setObjectName("barre_carte")
        tb.setMovable(False)
        self.addToolBar(tb)
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
        self.sp_taux = QDoubleSpinBox(); self.sp_taux.setRange(0, 20000); self.sp_taux.setDecimals(1)
        self.cmb_u_taux = QComboBox(); self.cmb_u_taux.addItems(list(UNITES_TAUX))
        form.addRow("Taux vertical max", self._ligne(self.sp_taux, self.cmb_u_taux))
        self.sp_alt_init = QDoubleSpinBox(); self.sp_alt_init.setRange(-2000, 150000); self.sp_alt_init.setDecimals(0)
        self.cmb_u_alt = QComboBox(); self.cmb_u_alt.addItems(list(UNITES_ALT))
        form.addRow("Altitude initiale", self._ligne(self.sp_alt_init, self.cmb_u_alt))
        self.sp_alt_cible = QDoubleSpinBox(); self.sp_alt_cible.setRange(-2000, 150000); self.sp_alt_cible.setDecimals(0)
        self.btn_alt_cible = QPushButton("Rejoindre")
        form.addRow("Altitude cible manuelle", self._ligne(self.sp_alt_cible, self.btn_alt_cible))
        self.cmb_interp = QComboBox(); self.cmb_interp.addItem("Orthodromie (grand cercle)", geo.ORTHODROMIE); self.cmb_interp.addItem("Loxodromie (cap constant)", geo.LOXODROMIE)
        form.addRow("Interpolation", self.cmb_interp)
        self.sp_incl = QDoubleSpinBox(); self.sp_incl.setRange(0, 60); self.sp_incl.setDecimals(0); self.sp_incl.setSuffix(" °")
        self.sp_incl.setToolTip("Inclinaison maximale dans les virages (rayon R = v²/(g·tan φ)). 0 = changement de cap instantané aux points de passage.")
        form.addRow("Inclinaison max (virages)", self.sp_incl)
        self.cb_boucle = QCheckBox("Reprendre au premier point après le dernier")
        form.addRow("", self.cb_boucle)
        self.cmb_facteur = QComboBox()
        for f in (1, 2, 5, 10, 20, 50, 100):
            self.cmb_facteur.addItem(f"× {f}", float(f))
        form.addRow("Facteur de temps", self.cmb_facteur)
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
        self.cb_actif = QCheckBox("Émission active")
        self.cb_actif.setChecked(True)
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

        # --- menu
        m = self.menuBar().addMenu("Fichier")
        for texte, cible in (("Ouvrir un profil…", self._ouvrir), ("Enregistrer le profil…", self._enregistrer), (None, None), ("Quitter", self.close)):
            if texte is None:
                m.addSeparator()
                continue
            a = QAction(texte, self); a.triggered.connect(cible); m.addAction(a)
        m = self.menuBar().addMenu("Affichage")
        for d in (self.dock_route, self.dock_cine, self.dock_gnss, self.dock_sorties):
            m.addAction(d.toggleViewAction())
        m.addSeparator()
        md = m.addMenu("Disposition")
        a = QAction("Étendue (panneaux côte à côte)", self); a.triggered.connect(lambda: self._disposition(False)); md.addAction(a)
        a = QAction("Compacte (panneaux en onglets, carte agrandie)", self); a.triggered.connect(lambda: self._disposition(True)); md.addAction(a)
        for d in (self.dock_route, self.dock_cine, self.dock_gnss, self.dock_sorties):
            d.visibilityChanged.connect(lambda _v=False: QTimer.singleShot(0, self._icones_docks))
            d.dockLocationChanged.connect(lambda _l=None: QTimer.singleShot(0, self._icones_docks))
        mt = m.addMenu("Thème")
        self._groupe_theme = QActionGroup(self)
        courant = str(self.settings.value("theme", "sombre"))
        for cle, lib in (("systeme", "Système"), ("sombre", "Sombre"), ("clair", "Clair")):
            a = QAction(lib, self, checkable=True)
            a.setChecked(cle == courant)
            a.triggered.connect(lambda _=False, c=cle: self._changer_theme(c))
            self._groupe_theme.addAction(a)
            mt.addAction(a)
        self._enregistrer_icones()
        m = self.menuBar().addMenu("Aide")
        a = QAction("À propos", self); a.triggered.connect(self._a_propos); m.addAction(a)
        self.statusBar().showMessage("Prêt")

    # ---- icônes et thème -----------------------------------------------------------------
    def _enregistrer_icones(self):
        """Associe chaque bouton / onglet à son icône (re-appliquée à chaque changement de thème)."""
        avec_texte = [('btn_mbtiles', 'couches'), ('btn_ajuster', 'ajuster'), ('btn_centrer_mobile', 'mobile'), ('btn_ajouter', 'plus'), ('btn_suppr', 'corbeille'), ('btn_vider', 'vider'), ('btn_centrer_pt', 'cible'), ('btn_ouvrir', 'dossier'), ('btn_enreg', 'disque'), ('btn_export_gpx', 'exporter'), ('btn_export_kml', 'exporter'), ('btn_alt_cible', 'monter'), ('btn_demarrer', 'lecture'), ('btn_pause', 'pause'), ('btn_arret', 'stop'), ('btn_reinit', 'reinit'), ('btn_sauter', 'saut'), ('btn_tle', 'telecharger')]
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
        self._icones_docks()

    def _icones_docks(self):
        """Icônes sur les onglets de panneaux (visibles quand des panneaux sont regroupés en onglets)."""
        par_titre = {self.dock_route.windowTitle(): "route", self.dock_cine.windowTitle(): "cinematique",
                     self.dock_gnss.windowTitle(): "satellite", self.dock_sorties.windowTitle(): "sorties"}
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
        for d in (self.dock_route, self.dock_cine, self.dock_gnss, self.dock_sorties):
            d.setFloating(False)
            d.show()
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_route)
        if compacte:
            self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_cine)
            self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_gnss)
            self.tabifyDockWidget(self.dock_route, self.dock_cine)
            self.tabifyDockWidget(self.dock_cine, self.dock_gnss)
            self.dock_route.raise_()
            self.resizeDocks([self.dock_route], [440], Qt.Horizontal)
            self.resizeDocks([self.dock_route, self.dock_sorties], [650, 190], Qt.Vertical)
        else:
            self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_cine)
            self.splitDockWidget(self.dock_route, self.dock_cine, Qt.Vertical)
            self.addDockWidget(Qt.RightDockWidgetArea, self.dock_gnss)
            self.resizeDocks([self.dock_route, self.dock_cine], [360, 440], Qt.Vertical)
            self.resizeDocks([self.dock_route, self.dock_gnss], [470, 430], Qt.Horizontal)
            self.resizeDocks([self.dock_route, self.dock_sorties], [650, 190], Qt.Vertical)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_sorties)
        QTimer.singleShot(0, self._icones_docks)

    def _basculer_theme(self):
        mode = "clair" if theme.pal()["nom"] == "sombre" else "sombre"
        for a in self._groupe_theme.actions():
            a.setChecked(a.text().lower() == mode)
        self._changer_theme(mode)

    def _changer_theme(self, mode: str):
        theme.appliquer(QApplication.instance(), mode)
        self.settings.setValue("theme", mode)
        self._appliquer_icones()
        self.vue_ciel.update()
        for w in self.findChildren(QWidget):                    # re-polish des propriétés dynamiques (badge, rôles)
            if w.property("role") or w.property("etat") or w.property("icone"):
                w.style().unpolish(w); w.style().polish(w)

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
        self.cmb_facteur.currentIndexChanged.connect(lambda: setattr(self.moteur, "facteur_temps", self.cmb_facteur.currentData()))
        self.btn_alt_cible.clicked.connect(lambda: self.moteur.regler_alt_cible(vers_si(self.sp_alt_cible.value(), self.cmb_u_alt.currentText(), UNITES_ALT)))
        self.btn_demarrer.clicked.connect(self._demarrer)
        self.btn_demarrer.setShortcut(QKeySequence("F5"))
        self.btn_pause.clicked.connect(self.moteur.basculer_pause)
        self.btn_arret.clicked.connect(self.moteur.arreter)
        self.btn_reinit.clicked.connect(lambda: self.moteur.reinitialiser())
        self.btn_sauter.clicked.connect(self._sauter)
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
        self._rafraichir_ports()

    # =====================================================================================
    # Boucle de simulation
    # =====================================================================================
    def _tic(self):
        t = time.monotonic()
        dt = t - self._t_prec
        self._t_prec = t
        e = self.moteur.avancer(dt)
        # ciel recalculé toutes les secondes simulées (satellites lents) ou au premier tic
        self._acc_ciel += dt * self.moteur.facteur_temps
        if self.ciel is None or self._acc_ciel >= 1.0:
            self._acc_ciel = 0.0
            h = e.alt_m + self.profil.ondulation_geoide_m
            self.ciel = self.constellations.ciel(e.lat, e.lon, h, e.temps)
            self.vue_ciel.maj(self.ciel.satellites, self.constellations.masque_deg)
            c = self.ciel
            fix = {0: "pas de fix", 2: "fix 2D", 3: "fix 3D"}.get(c.mode_3d if c.fix else 0, "—")
            self.lbl_gnss.setText(f"{len(c.satellites)} visibles / {c.nb_utilises} utilisés / {c.nb_suivis} à l'horizon  ·  {fix}\n"
                                  f"HDOP {c.hdop:.2f}  VDOP {c.vdop:.2f}  PDOP {c.pdop:.2f}  GDOP {c.gdop:.2f}")
        # émission NMEA à la cadence choisie (temps réel, pas temps simulé)
        self._acc_sortie += dt
        periode = 1.0 / self.cmb_cadence.currentData()
        if self._acc_sortie >= periode:
            self._acc_sortie = 0.0
            self._emettre()
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
        self.lbl_etat.setText(f"{textes.get(e.etat, e.etat)}{cible}   ·   {e.temps.astimezone(timezone.utc).strftime('%H:%M:%S')} UTC   ·   × {self.moteur.facteur_temps:g}")
        if self.lbl_etat.property("etat") != e.etat:        # badge d'état coloré (re-polish du style)
            self.lbl_etat.setProperty("etat", e.etat)
            self.lbl_etat.style().unpolish(self.lbl_etat)
            self.lbl_etat.style().polish(self.lbl_etat)
        reste = self.moteur.distance_restante_m()
        eta = self.moteur.temps_restant_s()
        eta_txt = "—" if eta == float("inf") or e.etat != EN_ROUTE else f"{int(eta // 60)} min {int(eta % 60):02d} s"
        self.lbl_cine.setText(f"cap {e.cap:05.1f}°  sol {depuis_si(e.vitesse_ms, uv, UNITES_VITESSE):.1f} {uv}  "
                              f"vz {depuis_si(e.vz_ms, ut, UNITES_TAUX):+.0f} {ut}\nreste {reste / geo.NM_M:.1f} NM ({reste / 1000:.1f} km)  ETA {eta_txt}")
        self.barre.setValue(int(self.moteur.progression() * 1000))
        self.btn_pause.setText("Reprendre" if e.etat == PAUSE else "Pause")
        self.btn_pause.setEnabled(e.etat in (EN_ROUTE, PAUSE))
        self.btn_arret.setEnabled(e.etat in (EN_ROUTE, PAUSE))
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

    def _maj_carte_route(self):
        self.pont.route = [{"lat": la, "lon": lo} for la, lo in trace_lissee(self.profil)]
        self.pont.points = [{"lat": p.lat, "lon": p.lon, "nom": p.nom, "idx": i, "selectionne": i == self._idx_selection}
                            for i, p in enumerate(self.profil.points)]
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
        self.pont.points = [{"lat": p.lat, "lon": p.lon, "nom": p.nom, "idx": i, "selectionne": i == self._idx_selection}
                            for i, p in enumerate(self.profil.points)]

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

    def _maj_consignes(self):
        self._widgets_vers_profil()
        self.moteur.regler_vitesse(self.profil.vitesse_ms)
        self.moteur.regler_taux(self.profil.taux_ms)
        if self.moteur.etat.etat == ARRET and self.moteur.etat.distance_m == 0.0:
            self.moteur.etat.alt_m = self.profil.alt_initiale_m if not (self.profil.points and self.profil.points[0].alt_m is not None) else self.profil.points[0].alt_m
        self._maj_carte_route()

    def _changer_unites(self):
        """Les valeurs SI ne changent pas : on réaffiche les champs dans la nouvelle unité."""
        self._profil_vers_widgets()
        self._maj_table()

    def _demarrer(self):
        self._widgets_vers_profil()
        if self.moteur.demarrer():
            self._log(f"démarrage : {self.profil.vitesse_ms / geo.KT_MS:.0f} kt, taux {self.profil.taux_ms / geo.FT_M * 60:.0f} ft/min, {self.profil.interpolation}")

    def _evt_moteur(self, evt: str, info: dict):
        if evt == "point_atteint":
            self._log(f"point {info['idx'] + 1} atteint" + (f" ({info['nom']})" if info.get("nom") else ""))
        elif evt == "termine":
            self._log("route terminée : mobile immobile au dernier point")
        elif evt == "erreur":
            QMessageBox.warning(self, "Simulation", info.get("msg", ""))
        elif evt in ("pause", "reprise", "arret"):
            self._log(evt)

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
        self.cmb_facteur.setCurrentIndex(0)
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
        geo_ = s.value("geometrie")
        if geo_ is not None:
            self.restoreGeometry(geo_)
        etat = s.value("etat_fenetre")
        if etat is not None:
            self.restoreState(etat)
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
