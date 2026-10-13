# -*- coding: utf-8 -*-
"""Vue du ciel (sky plot) : satellites en élévation/azimut, colorés par constellation."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PySide6.QtWidgets import QWidget

from . import theme

COULEURS = {"GPS": QColor("#2ec4b6"), "GLONASS": QColor("#ff6b6b"), "Galileo": QColor("#4d96ff"), "BeiDou": QColor("#ffd166")}

COULEURS_CLAIR = {"GPS": QColor("#0e9f92"), "GLONASS": QColor("#d64545"), "Galileo": QColor("#2f6fd6"), "BeiDou": QColor("#c98a00")}


class VueCiel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.satellites = []        # liste de nmea.Satellite
        self.masque_deg = 5.0
        self.setMinimumSize(220, 220)

    def maj(self, satellites, masque_deg: float) -> None:
        self.satellites = list(satellites)
        self.masque_deg = masque_deg
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = theme.pal()
        couleurs = COULEURS_CLAIR if c["nom"] == "clair" else COULEURS
        p.fillRect(self.rect(), QColor(c["fond"]))
        marge = 18
        cote = min(self.width(), self.height()) - 2 * marge
        if cote < 40:
            return
        cx, cy, r = self.width() / 2, self.height() / 2, cote / 2
        # cercles d'élévation 0/30/60 et masque
        p.setPen(QPen(QColor(c["bordure_champ"]), 1))
        for el in (0, 30, 60):
            rr = r * (90 - el) / 90.0
            p.drawEllipse(QPointF(cx, cy), rr, rr)
        rm = r * (90 - self.masque_deg) / 90.0
        p.setPen(QPen(QColor(c["alerte"]), 1, Qt.DashLine))
        p.drawEllipse(QPointF(cx, cy), rm, rm)
        # axes N/E/S/W
        p.setPen(QPen(QColor(c["bordure_champ"]), 1))
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
        p.setPen(QColor(c["discret"]))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRectF(cx - 10, cy - r - 16, 20, 14), Qt.AlignCenter, "N")
        p.drawText(QRectF(cx + r + 2, cy - 7, 16, 14), Qt.AlignCenter, "E")
        p.drawText(QRectF(cx - 10, cy + r + 2, 20, 14), Qt.AlignCenter, "S")
        p.drawText(QRectF(cx - r - 18, cy - 7, 16, 14), Qt.AlignCenter, "W")
        # satellites
        f = QFont("Consolas", 8)
        p.setFont(f)
        for s in self.satellites:
            rr = r * (90 - s.elevation) / 90.0
            a = math.radians(s.azimut)
            x, y = cx + rr * math.sin(a), cy - rr * math.cos(a)
            coul = couleurs.get(s.systeme, QColor("#cccccc"))
            taille = 5 + (s.cn0 - 20) / 32.0 * 6     # 5..11 px selon C/N0
            if s.utilise:
                p.setBrush(QBrush(coul))
                p.setPen(QPen(QColor(c["panneau"]), 1))
            else:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(coul, 1.5))
            p.drawEllipse(QPointF(x, y), taille, taille)
            p.setPen(QColor(c["texte"]))
            prefixe = {"GPS": "G", "GLONASS": "R", "Galileo": "E", "BeiDou": "C"}.get(s.systeme, "")
            p.drawText(QPointF(x + taille + 2, y + 4), f"{prefixe}{s.ident}")
        # légende
        y0 = 14
        for nom, coul in couleurs.items():
            p.setBrush(QBrush(coul))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(12, y0 - 3), 4, 4)
            p.setPen(QColor(c["texte"]))
            p.drawText(QPointF(20, y0), nom)
            y0 += 14
