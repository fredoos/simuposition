# -*- coding: utf-8 -*-
"""
Simulateur de GPS (banc de test des offsets) — mode caché.

Fenêtre qui émet un flux NMEA en boucle locale (UDP vers 127.0.0.1:port), comme le ferait un
vrai récepteur. On règle SimuPosition en mode Direct avec une entrée GPS UDP sur le même port :
le simulateur joue l'avion « réel » (trace verte) et SimuPosition applique l'offset (trace bleue).
Permet d'exercer les pilotages Auto (waypoints) et Manuel sans matériel.

Deux sources de mouvement :
  - Route du profil principal (réutilise le moteur de trajectoire) ;
  - Cap / vitesse manuels, pilotables en direct (vitesse, cap, taux de virage) pour voir l'offset
    latéral rester perpendiculaire quand « l'avion » tourne.
"""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QGroupBox, QRadioButton,
                               QComboBox, QSpinBox, QDoubleSpinBox, QSlider, QPushButton, QLabel,
                               QLineEdit, QPlainTextEdit, QWidget)

from ..core import geodesie as geo
from ..core.modele import MODE_LIVE
from ..core.moteur import Moteur, EN_ROUTE
from ..outputs.nmea import GenerateurNMEA, Solution
from ..outputs.canaux import CanalUDP


class FenetreSimuGPS(QDialog):
    """Banc de test : émet un flux NMEA simulé (UDP) pour alimenter le mode Direct."""

    def __init__(self, fenetre_principale):
        super().__init__(fenetre_principale)
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
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tic)

    # ---- interface -----------------------------------------------------------------
    def _construire(self, depart):
        v = QVBoxLayout(self)
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
        self.sl_vit = QSlider(Qt.Horizontal); self.sl_vit.setRange(0, 300); self.sl_vit.setValue(90)
        self.lbl_vit = QLabel("90 kt")
        form.addRow("Vitesse sol", self._ligne(self.sl_vit, self.lbl_vit))
        self.sp_cap = QSpinBox(); self.sp_cap.setRange(0, 359); self.sp_cap.setValue(90); self.sp_cap.setSuffix(" °")
        form.addRow("Cap", self.sp_cap)
        self.sl_vir = QSlider(Qt.Horizontal); self.sl_vir.setRange(-9, 9); self.sl_vir.setValue(0)
        self.lbl_vir = QLabel("0 °/s")
        form.addRow("Taux de virage", self._ligne(self.sl_vir, self.lbl_vir))
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

        self.sl_vit.valueChanged.connect(lambda x: self.lbl_vit.setText(f"{x} kt"))
        self.sl_vir.valueChanged.connect(lambda x: self.lbl_vir.setText(f"{x} °/s"))
        self.sp_cap.valueChanged.connect(lambda x: setattr(self, "_cap", float(x)))
        self.btn_emettre.toggled.connect(self._basculer)
        self.btn_reinit.clicked.connect(self._replacer)
        self.btn_brancher.clicked.connect(self._brancher_appli)
        self.rb_route.toggled.connect(self._maj_mode)
        self._maj_mode()

    @staticmethod
    def _ligne(*ws) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        for x in ws:
            h.addWidget(x)
        return w

    def _maj_mode(self):
        manuel = self.rb_manuel.isChecked()
        for w in (self.sp_lat, self.sp_lon, self.sl_vit, self.sp_cap, self.sl_vir, self.sp_alt):
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
                self._moteur.demarrer()
            else:
                self._replacer()
            self._n = 0
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
        dt = 1.0 / self.cmb_cadence.currentData()
        if self.rb_route.isChecked() and self._moteur is not None:
            e = self._moteur.avancer(dt)
            lat, lon, alt, cap, vit = e.lat, e.lon, e.alt_m, e.cap, e.vitesse_ms
        else:
            vit = self.sl_vit.value() * geo.KT_MS
            self._cap = (self._cap + self.sl_vir.value() * dt) % 360.0
            if self.sl_vir.value() != 0:                      # refléter l'évolution du cap dans le champ
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

    def closeEvent(self, ev):
        self.btn_emettre.setChecked(False)
        super().closeEvent(ev)
