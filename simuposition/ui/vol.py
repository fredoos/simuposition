# -*- coding: utf-8 -*-
"""
Protections d'ergonomie pour l'utilisation EN VOL (vibrations, turbulence, tablette tactile).

  - FiltreMolette : la molette (ou le défilement deux doigts) ne modifie JAMAIS un champ numérique,
    une liste déroulante ou un curseur qui n'a pas le focus — elle fait défiler le panneau. Sans ce
    filtre, faire défiler le panneau Direct changeait au passage le mode, le pilotage, l'appareil ou
    la distance d'offset, en pleine émission ;
  - confirmer() : question Oui / Non dont le bouton par défaut est « Non » (Entrée ou un appui
    tremblé ne valident pas une action qui coupe l'émission) ;
  - AppuiLong : action déclenchée seulement après un appui maintenu (Stop pendant l'émission), avec
    décompte visible sur le bouton.

© skydev 2026
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QMessageBox,
                               QPushButton, QSlider, QWidget)

TYPES_PROTEGES = (QAbstractSpinBox, QComboBox, QSlider)


class FiltreMolette(QObject):
    """Filtre d'application : molette sur un champ sans focus → défilement du panneau parent."""

    def eventFilter(self, obj, ev):  # noqa: N802 (API Qt)
        if ev.type() == QEvent.Wheel and isinstance(obj, TYPES_PROTEGES) and not obj.hasFocus():
            zone = obj.parentWidget()
            while zone is not None and not isinstance(zone, QAbstractScrollArea):
                zone = zone.parentWidget()
            if zone is not None:                       # le panneau défile, le champ ne change pas
                QApplication.sendEvent(zone.verticalScrollBar(), ev)
            return True
        return False


def proteger_champs(racine: QWidget) -> None:
    """Champs numériques, listes et curseurs : focus au clic ou au clavier seulement (pas à la molette)."""
    for w in racine.findChildren(QWidget):
        if isinstance(w, TYPES_PROTEGES):
            w.setFocusPolicy(Qt.StrongFocus)


def confirmer(parent: QWidget, titre: str, texte: str, oui: str = "Oui", non: str = "Non") -> bool:
    """Question Oui / Non, « Non » par défaut et sur Échap."""
    b = QMessageBox(QMessageBox.Warning, titre, texte, parent=parent)
    b_oui = b.addButton(oui, QMessageBox.DestructiveRole)
    b_non = b.addButton(non, QMessageBox.RejectRole)
    b.setDefaultButton(b_non)
    b.setEscapeButton(b_non)
    b.exec()
    return b.clickedButton() is b_oui


class AppuiLong(QObject):
    """Déclenche `action` après un appui maintenu `duree_ms` sur `bouton`, si `actif()` est vrai ; sinon
    le clic normal reste à la charge de l'appelant. Le texte du bouton affiche le décompte."""

    PAS_MS = 100

    def __init__(self, bouton: QPushButton, actif: Callable[[], bool], action: Callable[[], None],
                 duree_ms: int = 800, texte_maintien: str = "Maintenir…"):
        super().__init__(bouton)
        self.bouton, self.actif, self.action = bouton, actif, action
        self.duree_ms, self.texte_maintien = duree_ms, texte_maintien
        self._ecoule = 0
        self._texte = ""
        self.declenche = False                 # vrai juste après un déclenchement (le clic qui suit est ignoré)
        self._timer = QTimer(self)
        self._timer.setInterval(self.PAS_MS)
        self._timer.timeout.connect(self._pas)
        bouton.pressed.connect(self._appui)
        bouton.released.connect(self._relache)

    def _appui(self):
        self.declenche = False
        if not self.actif():
            return
        self._ecoule = 0
        self._texte = self.bouton.text()
        self._timer.start()
        self._afficher()

    def _pas(self):
        self._ecoule += self.PAS_MS
        if self._ecoule >= self.duree_ms:
            self._timer.stop()
            self.bouton.setText(self._texte)
            self.declenche = True
            self.action()
        else:
            self._afficher()

    def _afficher(self):
        reste = max(0, self.duree_ms - self._ecoule)
        self.bouton.setText(f"{self.texte_maintien} {reste / 1000:.1f} s")

    def _relache(self):
        if self._timer.isActive():               # relâché trop tôt : rien
            self._timer.stop()
            self.bouton.setText(self._texte)
