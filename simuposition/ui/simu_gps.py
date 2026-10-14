# -*- coding: utf-8 -*-
"""
Simulateur de GPS (banc de test des offsets) — mode caché.

Fenêtre qui émet un flux NMEA en boucle locale (UDP vers 127.0.0.1:port), comme le ferait un
vrai récepteur. On règle SimuPosition en mode Direct avec une entrée GPS UDP sur le même port :
le simulateur joue l'avion « réel » (trace verte) et SimuPosition applique l'offset (trace bleue).
Permet d'exercer les pilotages Auto (waypoints) et Manuel sans matériel.

Deux sources de mouvement :
  - Route du profil principal (réutilise le moteur de trajectoire) ;
  - Cap / vitesse manuels, pilotables en direct : vitesse (champ + pas de ±10 kt), cap (champ
    circulaire + pas de ±10°), virage (taux réglable, virages standard 3 °/s et « droit » en un clic).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer, Signal, QEvent
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QGroupBox, QRadioButton,
                               QComboBox, QSpinBox, QDoubleSpinBox, QPushButton, QLabel,
                               QLineEdit, QPlainTextEdit, QWidget)

from ..core import geodesie as geo
from ..core.modele import MODE_LIVE, MODE_SIM
from ..core.moteur import Moteur, EN_ROUTE
from ..outputs.nmea import GenerateurNMEA, Solution
from ..outputs.canaux import CanalUDP


class FenetreSimuGPS(QDialog):
    """Banc de test : émet un flux NMEA simulé (UDP) pour alimenter le mode Direct.

    « Réduire » (bouton ou bouton de réduction de la barre de titre) masque la fenêtre sans arrêter
    l'émission ; la fenêtre principale affiche alors un bouton clignotant dans la barre du haut qui
    la rouvre. Fermer la fenêtre arrête l'émission."""

    reduit = Signal()                           # fenêtre réduite (masquée, l'émission continue)

    def __init__(self, fenetre_principale):
        super().__init__(fenetre_principale)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        self.app = fenetre_principale
        self.setWindowTitle("Simulateur GPS — banc de test des offsets")
        self.setMinimumWidth(420)
        self._gen = GenerateurNMEA("GN", {"RMC", "GGA", "VTG", "GLL"})
        self._canal = None
        self._moteur = None
        self._n = 0
        # état du modèle manuel (position courante)
        p = self.app.profil
        depart = (p.points[0].lat, p.points[0].lon) if p.points else (49.0287, 1.2199)
        self._lat, self._lon = depart
        self._alt = p.alt_initiale_m
        self._cap = 90.0
        self._ondul = p.ondulation_geoide_m
        self._construire(depart)
        self._t_prec = None                 # instant (monotone) de la dernière émission
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tic)

    # ---- interface -----------------------------------------------------------------
    def _construire(self, depart):
        v = QVBoxLayout(self)
        haut = QHBoxLayout()
        haut.addStretch(1)
        self.btn_reduire = QPushButton("▁  Réduire")
        self.btn_reduire.setAutoDefault(False)
        self.btn_reduire.setToolTip("Masque la fenêtre sans arrêter l'émission : un bouton clignotant dans la barre\n"
                                    "du haut de SimuPosition la rouvre.")
        self.btn_reduire.clicked.connect(self.reduire)
        haut.addWidget(self.btn_reduire)
        v.addLayout(haut)
        intro = QLabel("Émet un GPS « réel » simulé en NMEA (UDP 127.0.0.1). Branche SimuPosition "
                       "en mode Direct, entrée UDP sur le même port, puis « Recevoir le GPS ».")
        intro.setWordWrap(True)
        v.addWidget(intro)

        grp_flux = QGroupBox("Flux de sortie (UDP)")
        f = QFormLayout(grp_flux)
        self.ed_hote = QLineEdit("127.0.0.1")
        f.addRow("Hôte", self.ed_hote)
        self.sp_port = QSpinBox(); self.sp_port.setRange(1, 65535); self.sp_port.setValue(10111)
        f.addRow("Port", self.sp_port)
        self.cmb_cadence = QComboBox()
        for hz in (1, 2, 5, 10):
            self.cmb_cadence.addItem(f"{hz} Hz", float(hz))
        f.addRow("Cadence", self.cmb_cadence)
        v.addWidget(grp_flux)

        grp_mvt = QGroupBox("Mouvement de l'avion « réel »")
        fm = QVBoxLayout(grp_mvt)
        self.rb_manuel = QRadioButton("Cap / vitesse manuels (pilotable)")
        self.rb_route = QRadioButton("Suivre la route du profil principal")
        self.rb_manuel.setChecked(True)
        fm.addWidget(self.rb_manuel)
        fm.addWidget(self.rb_route)
        form = QFormLayout()
        self.sp_lat = QDoubleSpinBox(); self.sp_lat.setRange(-90, 90); self.sp_lat.setDecimals(6); self.sp_lat.setValue(depart[0])
        self.sp_lon = QDoubleSpinBox(); self.sp_lon.setRange(-180, 180); self.sp_lon.setDecimals(6); self.sp_lon.setValue(depart[1])
        form.addRow("Départ lat", self.sp_lat)
        form.addRow("Départ lon", self.sp_lon)
        # vitesse : champ (molette ±5 kt) + boutons de pas
        self.sp_vit = QSpinBox(); self.sp_vit.setRange(0, 999); self.sp_vit.setSingleStep(5); self.sp_vit.setValue(90)
        self.sp_vit.setSuffix(" kt")
        boutons_vit = [self._bouton_pas(t, lambda d=d: self.sp_vit.setValue(self.sp_vit.value() + d))
                       for t, d in (("−10", -10), ("+10", 10))]
        form.addRow("Vitesse sol", self._ligne(boutons_vit[0], self.sp_vit, boutons_vit[1]))
        # cap : champ circulaire (359 → 0) + boutons de pas
        self.sp_cap = QSpinBox(); self.sp_cap.setRange(0, 359); self.sp_cap.setWrapping(True)
        self.sp_cap.setSingleStep(5); self.sp_cap.setValue(90); self.sp_cap.setSuffix(" °")
        boutons_cap = [self._bouton_pas(t, lambda d=d: self.sp_cap.setValue((self.sp_cap.value() + d) % 360))
                       for t, d in (("−10°", -10), ("+10°", 10))]
        form.addRow("Cap", self._ligne(boutons_cap[0], self.sp_cap, boutons_cap[1]))
        # virage : taux réglable + virages standard (3 °/s) et « tout droit » en un clic
        self.sp_vir = QDoubleSpinBox(); self.sp_vir.setRange(-20, 20); self.sp_vir.setDecimals(1)
        self.sp_vir.setSingleStep(0.5); self.sp_vir.setValue(0.0); self.sp_vir.setSuffix(" °/s")
        self.sp_vir.setToolTip("Taux de virage : négatif = à gauche, positif = à droite (3 °/s = virage standard).")
        b_g = self._bouton_pas("◀ 3°/s", lambda: self.sp_vir.setValue(-3.0))
        b_0 = self._bouton_pas("Droit", lambda: self.sp_vir.setValue(0.0))
        b_d = self._bouton_pas("3°/s ▶", lambda: self.sp_vir.setValue(3.0))
        form.addRow("Virage", self._ligne(b_g, self.sp_vir, b_d))
        form.addRow("", self._ligne(b_0))
        self.sp_alt = QDoubleSpinBox(); self.sp_alt.setRange(-500, 50000); self.sp_alt.setDecimals(0); self.sp_alt.setValue(self._alt); self.sp_alt.setSuffix(" m")
        form.addRow("Altitude (MSL)", self.sp_alt)
        fm.addLayout(form)
        v.addWidget(grp_mvt)

        h = QHBoxLayout()
        self.btn_emettre = QPushButton("Émettre"); self.btn_emettre.setCheckable(True); self.btn_emettre.setProperty("mode", "live")
        self.btn_reinit = QPushButton("Replacer au départ")
        self.btn_brancher = QPushButton("Brancher l'appli en Direct sur ce flux"); self.btn_brancher.setProperty("role", "primaire")
        h.addWidget(self.btn_emettre); h.addWidget(self.btn_reinit)
        v.addLayout(h)
        v.addWidget(self.btn_brancher)

        self.lbl_pos = QLabel("—"); self.lbl_pos.setObjectName("mono"); self.lbl_pos.setWordWrap(True)
        v.addWidget(self.lbl_pos)

        self.sp_cap.valueChanged.connect(lambda x: setattr(self, "_cap", float(x)))
        self.btn_emettre.toggled.connect(self._basculer)
        self.btn_reinit.clicked.connect(self._replacer)
        self.btn_brancher.clicked.connect(self._brancher_appli)
        self.rb_route.toggled.connect(self._maj_mode)
        self._maj_mode()

    @staticmethod
    def _bouton_pas(texte: str, action) -> QPushButton:
        b = QPushButton(texte)
        b.setAutoDefault(False)                 # Entrée dans un champ ne déclenche pas le bouton
        b.clicked.connect(action)
        return b

    @staticmethod
    def _ligne(*ws) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        for x in ws:
            h.addWidget(x)
        return w

    def _maj_mode(self):
        manuel = self.rb_manuel.isChecked()
        grp = self.sp_vit.parentWidget().parentWidget()        # le formulaire du mouvement manuel
        for w in grp.findChildren(QWidget):
            if w not in (self.rb_manuel, self.rb_route):
                w.setEnabled(manuel)

    # ---- émission ------------------------------------------------------------------
    def _replacer(self):
        self._lat, self._lon = self.sp_lat.value(), self.sp_lon.value()
        self._alt = self.sp_alt.value()
        self._cap = float(self.sp_cap.value())
        if self._moteur is not None:
            self._moteur.reinitialiser()

    def _basculer(self, on: bool):
        if on:
            self._canal = CanalUDP(self.ed_hote.text().strip() or "127.0.0.1", self.sp_port.value())
            if not self._canal.ouvrir():
                self.btn_emettre.setChecked(False)
                self._canal = None
                return
            if self.rb_route.isChecked():
                self._moteur = Moteur(self.app.profil)
                self._moteur.definir_mode(MODE_SIM)          # le « GPS réel » suit la route, même si l'appli est en Direct
                self._moteur.demarrer()
            else:
                self._replacer()
            self._n = 0
            self._t_prec = None
            self._timer.start(int(1000 / self.cmb_cadence.currentData()))
            self.btn_emettre.setText("Arrêter l'émission")
        else:
            self._timer.stop()
            if self._canal is not None:
                self._canal.fermer()
                self._canal = None
            self._moteur = None
            self.btn_emettre.setText("Émettre")

    def _tic(self):
        # pas de temps MESURÉ : le déplacement émis reste cohérent avec l'heure UTC des trames
        # (un pas fixe de 1/cadence, alors que le minuteur gigue, faussait la vitesse vue par l'appli)
        nominal = 1.0 / self.cmb_cadence.currentData()
        t = time.monotonic()
        dt = nominal if self._t_prec is None else min(max(t - self._t_prec, 0.2 * nominal), 5.0 * nominal)
        self._t_prec = t
        if self.rb_route.isChecked() and self._moteur is not None:
            e = self._moteur.avancer(dt)
            lat, lon, alt, cap, vit = e.lat, e.lon, e.alt_m, e.cap, e.vitesse_ms
        else:
            vit = self.sp_vit.value() * geo.KT_MS
            self._cap = (self._cap + self.sp_vir.value() * dt) % 360.0
            if self.sp_vir.value() != 0:                      # refléter l'évolution du cap dans le champ
                self.sp_cap.blockSignals(True); self.sp_cap.setValue(int(round(self._cap)) % 360); self.sp_cap.blockSignals(False)
            if vit > 0:
                self._lat, self._lon, _ = geo.direct(self._lat, self._lon, self._cap, vit * dt)
            lat, lon, alt, cap = self._lat, self._lon, self.sp_alt.value(), self._cap
        sol = Solution(temps=datetime.now(timezone.utc), lat=lat, lon=lon, alt_msl_m=alt,
                       ondulation_m=self._ondul, vitesse_ms=vit, cap=cap, fix=1, satellites=[])
        trames = self._gen.trames(sol, avec_satellites=False)
        if self._canal is not None:
            self._canal.ecrire("".join(trames).encode("ascii", errors="replace"))
            self._n += 1
        self.lbl_pos.setText(f"émis {self._n} · {lat:.5f}, {lon:.5f}  ·  cap {cap:04.0f}°  "
                             f"sol {vit / geo.KT_MS:.0f} kt")

    def _brancher_appli(self):
        """Configure SimuPosition en Direct, entrée UDP sur ce port, et active la réception."""
        a = self.app
        a.cmb_mode.setCurrentIndex(a.cmb_mode.findData(MODE_LIVE))
        a.cmb_gps_transport.setCurrentIndex(a.cmb_gps_transport.findData("udp"))
        a.sp_gps_port.setValue(self.sp_port.value())
        a.cb_gps_actif.setChecked(False)
        a.cb_gps_actif.setChecked(True)                       # (ré)ouvre l'entrée sur le bon port

    def reduire(self):
        """Masque la fenêtre (l'émission continue) et le signale à la fenêtre principale."""
        self.hide()
        self.reduit.emit()

    @property
    def emet(self) -> bool:
        return self._canal is not None

    def resume(self) -> str:
        """Texte court pour le bouton de la barre du haut."""
        if not self.emet:
            return "arrêté"
        v = self.sp_vit.value() if self.rb_manuel.isChecked() else None
        return f"émet · {v} kt · cap {self.sp_cap.value():03d}°" if v is not None else "émet · route"

    def changeEvent(self, ev):
        # bouton de réduction de la barre de titre : même comportement que « Réduire »
        if ev.type() == QEvent.WindowStateChange and self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
            QTimer.singleShot(0, self.reduire)
        super().changeEvent(ev)

    def closeEvent(self, ev):
        self.btn_emettre.setChecked(False)
        super().closeEvent(ev)
