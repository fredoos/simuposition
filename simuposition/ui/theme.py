# -*- coding: utf-8 -*-
"""Thème sombre moderne de SimuPosition (Fusion + palette + feuille de style Qt).

Couleurs alignées sur la carte (Carte.qml) et la vue du ciel :
fond #141821, panneaux #1b1f2a, bordures #2c3344, accent #2ec4b6 (turquoise)."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

FOND = "#141821"
PANNEAU = "#1b1f2a"
CHAMP = "#222838"
BORDURE = "#2c3344"
TEXTE = "#dfe6f3"
TEXTE_DISCRET = "#8d99b5"
ACCENT = "#2ec4b6"
ACCENT_SURVOL = "#3fdccd"
ALERTE = "#ff6b6b"
AVERT = "#ffd166"

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; color: {TEXTE}; }}
QMainWindow, QDialog {{ background: {FOND}; }}
QWidget {{ background: transparent; }}
QDockWidget > QWidget {{ background: {PANNEAU}; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {FOND}; padding: 7px 10px; border-bottom: 2px solid {ACCENT};
                      font-weight: 600; letter-spacing: 0.5px; color: {TEXTE}; }}
QMainWindow::separator {{ background: {FOND}; width: 4px; height: 4px; }}
QMainWindow::separator:hover {{ background: {ACCENT}; }}

QToolBar {{ background: {PANNEAU}; border: none; border-bottom: 1px solid {BORDURE}; padding: 5px 8px; spacing: 8px; }}
QToolBar::separator {{ background: {BORDURE}; width: 1px; margin: 4px 6px; }}
QMenuBar {{ background: {FOND}; }}
QMenuBar::item {{ padding: 6px 12px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {CHAMP}; }}
QMenu {{ background: {PANNEAU}; border: 1px solid {BORDURE}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT}; color: #0b1a1c; }}
QStatusBar {{ background: {FOND}; color: {TEXTE_DISCRET}; border-top: 1px solid {BORDURE}; }}

QLabel {{ background: transparent; }}
QLabel#etat {{ background: {CHAMP}; border: 1px solid {BORDURE}; border-radius: 10px; padding: 6px 12px; font-weight: 600; }}
QLabel#etat[etat="en_route"] {{ background: rgba(46,196,182,0.16); border-color: {ACCENT}; color: {ACCENT_SURVOL}; }}
QLabel#etat[etat="pause"] {{ background: rgba(255,209,102,0.14); border-color: {AVERT}; color: {AVERT}; }}
QLabel#etat[etat="termine"] {{ background: rgba(255,107,107,0.12); border-color: {ALERTE}; color: {ALERTE}; }}
QLabel#altitude {{ color: {ACCENT_SURVOL}; font-family: Consolas, "Cascadia Mono", monospace; font-size: 22pt; font-weight: 700; }}
QLabel#mono {{ font-family: Consolas, "Cascadia Mono", monospace; color: {TEXTE}; }}

QPushButton {{ background: {CHAMP}; border: 1px solid {BORDURE}; border-radius: 8px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; background: #283047; }}
QPushButton:pressed {{ background: {BORDURE}; }}
QPushButton:disabled {{ color: #5b6580; background: {PANNEAU}; }}
QPushButton[role="primaire"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #08201e; font-weight: 700; padding: 7px 18px; }}
QPushButton[role="primaire"]:hover {{ background: {ACCENT_SURVOL}; }}
QPushButton[role="danger"]:hover {{ border-color: {ALERTE}; color: {ALERTE}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {CHAMP}; border: 1px solid {BORDURE}; border-radius: 7px; padding: 4px 8px;
    selection-background-color: {ACCENT}; selection-color: #08201e; min-height: 20px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {PANNEAU}; border: 1px solid {BORDURE}; selection-background-color: {ACCENT}; selection-color: #08201e; outline: none; }}
QPlainTextEdit {{ font-family: Consolas, "Cascadia Mono", monospace; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #435070; background: {CHAMP}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}

QTableWidget {{ background: {PANNEAU}; alternate-background-color: #1f2432; gridline-color: transparent;
                border: 1px solid {BORDURE}; border-radius: 8px; selection-background-color: rgba(46,196,182,0.28); selection-color: {TEXTE}; }}
QTableWidget::item {{ padding: 4px 6px; border: none; }}
QHeaderView::section {{ background: {FOND}; color: {TEXTE_DISCRET}; border: none; border-bottom: 1px solid {BORDURE}; padding: 6px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {FOND}; border: none; }}

QProgressBar {{ background: {CHAMP}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 #4d96ff); border-radius: 4px; }}

QGroupBox {{ border: 1px solid {BORDURE}; border-radius: 10px; margin-top: 14px; padding: 12px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {TEXTE_DISCRET}; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {BORDURE}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 7px 16px; color: {TEXTE_DISCRET}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXTE}; border-bottom-color: {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXTE}; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3a4560; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #3a4560; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {PANNEAU}; color: {TEXTE}; border: 1px solid {ACCENT}; border-radius: 6px; padding: 5px 8px; }}
"""


def appliquer(app: QApplication) -> None:
    """Applique le style Fusion sombre + feuille de style à toute l'application."""
    app.setStyle("Fusion")
    p = QPalette()
    for role, coul in ((QPalette.Window, FOND), (QPalette.Base, CHAMP), (QPalette.AlternateBase, PANNEAU),
                       (QPalette.Button, CHAMP), (QPalette.ToolTipBase, PANNEAU)):
        p.setColor(role, QColor(coul))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText):
        p.setColor(role, QColor(TEXTE))
    p.setColor(QPalette.Highlight, QColor(ACCENT))
    p.setColor(QPalette.HighlightedText, QColor("#08201e"))
    p.setColor(QPalette.PlaceholderText, QColor(TEXTE_DISCRET))
    app.setPalette(p)
    f = QFont("Segoe UI")
    f.setPointSize(10)
    app.setFont(f)
    app.setStyleSheet(QSS)
