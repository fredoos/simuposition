# -*- coding: utf-8 -*-
"""
Barre de titre des panneaux (QDockWidget) avec flèche de repli.

▾ / ▸ (ou double-clic sur la barre) : replie le panneau à sa seule barre de titre — le contenu est
masqué et la place libérée profite aux panneaux voisins et à la carte — puis le déplie. Les boutons
« détacher » (⧉) et « fermer » (✕) de la barre d'origine sont conservés. L'état replié est mémorisé
par panneau (clé « replie/<objectName> »).

© skydev 2026
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QMainWindow, QToolButton, QWidget

HAUTEUR_MAX = 16777215          # QWIDGETSIZE_MAX


class BarreTitreDock(QWidget):
    """Barre de titre : flèche de repli, titre, détacher, fermer."""

    def __init__(self, dock: QDockWidget, settings: QSettings | None = None):
        super().__init__(dock)
        self.dock = dock
        self.settings = settings
        self.replie = False
        self._hauteur_depliee = 0           # hauteur avant repli (rendue au dépliage)
        self.setObjectName("barreDock")
        self.setAttribute(Qt.WA_StyledBackground, True)
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(4)
        self.btn_replier = self._bouton("▾", "Replier le panneau à sa barre (double-clic sur la barre)")
        self.btn_replier.clicked.connect(self.basculer)
        self.lbl = QLabel(dock.windowTitle())
        self.lbl.setObjectName("titreDock")
        dock.windowTitleChanged.connect(self.lbl.setText)
        self.btn_flottant = self._bouton("⧉", "Détacher / rattacher le panneau")
        self.btn_flottant.clicked.connect(lambda: dock.setFloating(not dock.isFloating()))
        self.btn_fermer = self._bouton("✕", "Fermer le panneau (menu Affichage pour le rouvrir)")
        self.btn_fermer.clicked.connect(dock.close)
        h.addWidget(self.btn_replier)
        h.addWidget(self.lbl, 1)
        h.addWidget(self.btn_flottant)
        h.addWidget(self.btn_fermer)

    @staticmethod
    def _bouton(texte: str, aide: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("btnBarreDock")
        b.setText(texte)
        b.setToolTip(aide)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.NoFocus)
        return b

    def mouseDoubleClickEvent(self, ev):
        self.basculer()
        ev.accept()

    # ---- repli ------------------------------------------------------------------------
    def basculer(self):
        self.regler(not self.replie)

    def regler(self, replie: bool, memoriser: bool = True):
        """Replie (contenu masqué, hauteur = barre) ou déplie le panneau."""
        if replie and not self.replie and self.dock.height() > 60:
            self._hauteur_depliee = self.dock.height()
        self.replie = replie
        contenu = self.dock.widget()
        if contenu is not None:
            contenu.setVisible(not replie)
        if replie:
            hb = self.sizeHint().height() + 2
            self.dock.setMinimumHeight(hb)
            self.dock.setMaximumHeight(hb)
        else:
            self.dock.setMinimumHeight(0)
            self.dock.setMaximumHeight(HAUTEUR_MAX)
            QTimer.singleShot(0, self._rendre_hauteur)
        self.btn_replier.setText("▸" if replie else "▾")
        self.btn_replier.setToolTip("Déplier le panneau" if replie
                                    else "Replier le panneau à sa barre (double-clic sur la barre)")
        if memoriser and self.settings is not None and self.dock.objectName():
            self.settings.setValue(f"replie/{self.dock.objectName()}", replie)

    def _rendre_hauteur(self):
        """Au dépliage, le panneau reprend sa hauteur d'avant (les voisins avaient pris la place)."""
        fen = self.dock.parentWidget()
        if self.replie or self.dock.isFloating() or not isinstance(fen, QMainWindow):
            return
        h = self._hauteur_depliee or 250
        fen.resizeDocks([self.dock], [h], Qt.Vertical)


def installer(dock: QDockWidget, settings: QSettings | None = None) -> BarreTitreDock:
    """Remplace la barre de titre du panneau par une barre avec flèche de repli."""
    barre = BarreTitreDock(dock, settings)
    dock.setTitleBarWidget(barre)
    return barre


def restaurer(barre: BarreTitreDock) -> None:
    """Réapplique l'état replié mémorisé (après restauration de la disposition)."""
    s = barre.settings
    if s is None or not barre.dock.objectName():
        return
    v = s.value(f"replie/{barre.dock.objectName()}", False)
    barre.regler(str(v).lower() in ("true", "1"), memoriser=False)
