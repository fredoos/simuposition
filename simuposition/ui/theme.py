# -*- coding: utf-8 -*-
"""Thèmes de SimuPosition (sombre / clair / système) et jeu d'icônes vectorielles.

- Deux palettes ; la feuille de style Qt (QSS) est générée depuis la palette active.
- Icônes : SVG « trait » (24×24) intégrés, recolorés selon le thème → aucune ressource externe.
- Les petites images référencées par le QSS (coche, chevrons) sont générées dans un dossier
  temporaire à chaque changement de thème."""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

# --- palettes -------------------------------------------------------------------------------
SOMBRE = {
    "nom": "sombre", "fond": "#141821", "panneau": "#1b1f2a", "alterne": "#1f2432", "champ": "#222838",
    "survol": "#283047", "bordure": "#2c3344", "bordure_champ": "#435070", "texte": "#dfe6f3",
    "discret": "#8d99b5", "desactive": "#5b6580", "accent": "#2ec4b6", "accent_survol": "#3fdccd",
    "sur_accent": "#08201e", "alerte": "#ff6b6b", "avert": "#ffd166", "poignee": "#3a4560",
    "fin_degrade": "#4d96ff", "direct": "#2ecc71", "sur_direct": "#06231a",
}
CLAIR = {
    "nom": "clair", "fond": "#eef1f6", "panneau": "#ffffff", "alterne": "#f5f7fb", "champ": "#f1f4f9",
    "survol": "#e3e8f1", "bordure": "#d3d9e6", "bordure_champ": "#aab4c8", "texte": "#1b2233",
    "discret": "#5d6a85", "desactive": "#a4adc0", "accent": "#0e9f92", "accent_survol": "#0b8478",
    "sur_accent": "#ffffff", "alerte": "#d64545", "avert": "#b9770e", "poignee": "#c2cadb",
    "fin_degrade": "#2f6fd6", "direct": "#179e5c", "sur_direct": "#ffffff",
}
MODES = ("systeme", "sombre", "clair")

_courante: dict = SOMBRE
_cache_icones: dict = {}
_dossier_img = os.path.join(tempfile.gettempdir(), "skydev_simuposition_theme")


def pal() -> dict:
    """Palette active (utilisée aussi par les widgets dessinés à la main)."""
    return _courante


def resoudre(mode: str) -> dict:
    """Mode (« systeme » | « sombre » | « clair ») → palette."""
    if mode == "clair":
        return CLAIR
    if mode == "sombre":
        return SOMBRE
    try:                                                  # Qt ≥ 6.5 : schéma de couleurs du système
        if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light:
            return CLAIR
    except Exception:  # noqa: BLE001
        pass
    return SOMBRE


# --- icônes ---------------------------------------------------------------------------------
_P = lambda d: f'<path d="{d}"/>'   # noqa: E731
ICONES = {
    "lecture": '<polygon points="6 4 20 12 6 20 6 4"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "reinit": _P("M3 12a9 9 0 1 0 3-6.7L3 8") + _P("M3 3v5h5"),
    "saut": '<polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/>',
    "plus": _P("M12 5v14M5 12h14"),
    "corbeille": _P("M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6"),
    "vider": '<circle cx="12" cy="12" r="9"/>' + _P("M9 9l6 6M15 9l-6 6"),
    "haut": _P("M6 15l6-6 6 6"),
    "bas": _P("M6 9l6 6 6-6"),
    "cible": '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/>' + _P("M12 2v4M12 18v4M2 12h4M18 12h4"),
    "dossier": _P("M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"),
    "disque": _P("M5 3h11l3 3v15H5z") + _P("M8 3v6h8V3M8 21v-7h8v7"),
    "telecharger": _P("M12 3v12M7 10l5 5 5-5M4 20h16"),
    "exporter": _P("M12 15V3M7 8l5-5 5 5M4 20h16"),
    "couches": '<polygon points="12 3 3 8 12 13 21 8 12 3"/>' + _P("M3 13l9 5 9-5"),
    "ajuster": _P("M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"),
    "mobile": '<polygon points="12 3 19 21 12 17 5 21 12 3"/>',
    "actualiser": _P("M21 12a9 9 0 1 1-3-6.7L21 8") + _P("M21 3v5h-5"),
    "monter": _P("M3 17l6-6 4 4 8-8M15 7h6v6"),
    "liste": _P("M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"),
    "prise": _P("M12 22v-5M9 8V2M15 8V2M6 8h12v4a6 6 0 0 1-12 0z"),
    "journal": _P("M6 3h8l4 4v14H6z") + _P("M14 3v4h4M9 12h6M9 16h6"),
    "route": '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/>' + _P("M8 19h6a4 4 0 0 0 0-8h-4a4 4 0 0 1 0-8h6"),
    "cinematique": _P("M12 14l4-4M3.3 17a9 9 0 1 1 17.4 0"),
    "satellite": _P("M13 7L9 3 5 7l4 4M17 11l4 4-4 4-4-4M8 12l4 4M16 8l3-3M9 21a6 6 0 0 0-6-6"),
    "sorties": '<circle cx="12" cy="12" r="2"/>' + _P("M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19 5a10 10 0 0 1 0 14M5 19A10 10 0 0 1 5 5"),
    "soleil": '<circle cx="12" cy="12" r="4"/>' + _P("M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"),
    "lune": _P("M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"),
    "coche": _P("M5 12.5l4.5 4.5L19 7.5"),
    "chrono": '<circle cx="12" cy="14" r="8"/>' + _P("M12 10v4l2.5 2M9 2h6M12 2v4M19 7l1.5-1.5"),
    "horloge": '<circle cx="12" cy="12" r="9"/>' + _P("M12 7v5l3 3"),
    "avion": _P("M10.5 21l1.5-1 1.5 1v-1.5L12.8 18v-4.5l8.2 3v-2l-8.2-5V4.2a.8.8 0 0 0-1.6 0V9.5l-8.2 5v2l8.2-3V18l-.7 1.5z"),
}


def _svg(nom: str, couleur: str, epaisseur: float = 2.0) -> bytes:
    plein = nom in ("lecture", "pause", "stop")
    attrs = (f'fill="{couleur}" stroke="{couleur}"' if plein else f'fill="none" stroke="{couleur}"')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" {attrs} stroke-width="{epaisseur}" '
            f'stroke-linecap="round" stroke-linejoin="round">{ICONES[nom]}</svg>').encode()


def _rendre(nom: str, couleur: str, taille: int = 18, ratio: int = 2, epaisseur: float = 2.0) -> QPixmap:
    pm = QPixmap(taille * ratio, taille * ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(_svg(nom, couleur, epaisseur))).render(p, QRectF(0, 0, taille * ratio, taille * ratio))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icone(nom: str, role: str = "") -> QIcon:
    """Icône du thème courant ; `role="primaire"` = pictogramme sur fond d'accent."""
    cle = (_courante["nom"], nom, role)
    if cle not in _cache_icones:
        c = _courante
        ic = QIcon()
        normal = c["sur_accent"] if role == "primaire" else ("#ffffff" if role == "stop" else c["texte"])
        actif = c["sur_accent"] if role == "primaire" else ("#ffffff" if role == "stop" else c["accent"])
        ic.addPixmap(_rendre(nom, normal), QIcon.Normal, QIcon.Off)
        ic.addPixmap(_rendre(nom, actif), QIcon.Active, QIcon.Off)
        ic.addPixmap(_rendre(nom, c["desactive"]), QIcon.Disabled, QIcon.Off)
        _cache_icones[cle] = ic
    return _cache_icones[cle]


def pixmap(nom: str, couleur: str, taille: int = 16) -> QPixmap:
    """Pictogramme du jeu d'icônes rendu dans une couleur arbitraire (ex. indicateur d'état)."""
    return _rendre(nom, couleur, taille)


def _generer_images() -> dict:
    """Images référencées par le QSS (coche, chevrons) → chemins (séparateur « / »)."""
    os.makedirs(_dossier_img, exist_ok=True)
    c = _courante
    chemins = {}
    for cle, nom, coul, ep in (("coche", "coche", c["sur_accent"], 3.2), ("chevron_bas", "bas", c["discret"], 2.4),
                               ("chevron_haut", "haut", c["discret"], 2.4)):
        f = os.path.join(_dossier_img, f"{c['nom']}_{cle}.png")
        _rendre(nom, coul, 14, 2, ep).save(f, "PNG")
        chemins[cle] = f.replace("\\", "/")
    return chemins


# --- feuille de style -------------------------------------------------------------------------
TAILLES = {"Compact": 9, "Normal": 10, "Grand": 12}      # préréglages de taille du texte (points)
TAILLE_DEFAUT = 9


def construire_qss(c: dict, taille_pt: int = TAILLE_DEFAUT) -> str:
    img = _generer_images()
    alt_pt = round(taille_pt * 2.2)                       # bandeau altitude proportionnel à la base
    sel = QColor(c["accent"])
    sel_alpha = f"rgba({sel.red()},{sel.green()},{sel.blue()},0.26)"
    badge = {k: QColor(c[k]) for k in ("accent", "avert", "alerte", "direct")}
    fond_b = {k: f"rgba({v.red()},{v.green()},{v.blue()},0.15)" for k, v in badge.items()}
    fond_chip = {k: f"rgba({v.red()},{v.green()},{v.blue()},0.18)" for k, v in badge.items()}
    stop_survol = QColor(c["alerte"]).darker(115).name()
    return f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: {taille_pt}pt; color: {c['texte']}; }}
QWidget {{ background: transparent; }}
/* après la règle QWidget (même spécificité : la dernière l'emporte) — sinon une fenêtre de premier
   niveau comme le simulateur GPS reste transparente, donc noire sous Windows */
QMainWindow, QDialog {{ background: {c['fond']}; }}
QDockWidget {{ background: {c['panneau']}; }}
QDockWidget > QWidget {{ background: {c['panneau']}; }}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QTabWidget {{ background: {c['panneau']}; }}
QWidget#fondOnglets {{ background: {c['panneau']}; }}
QWidget#barreDock {{ background: {c['fond']}; border-bottom: 2px solid {c['accent']}; }}
QLabel#titreDock {{ font-weight: 600; padding-left: 2px; }}
QToolButton#btnBarreDock {{ border: none; border-radius: 4px; padding: 1px 6px; color: {c['texte']}; font-weight: 700; }}
QToolButton#btnBarreDock:hover {{ background: {c['accent']}; color: {c['sur_accent']}; }}
QDockWidget::title {{ background: {c['fond']}; padding: 7px 10px; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}
QMainWindow::separator {{ background: {c['fond']}; width: 4px; height: 4px; }}
QMainWindow::separator:hover {{ background: {c['accent']}; }}

QToolBar {{ background: {c['panneau']}; border: none; border-bottom: 1px solid {c['bordure']}; padding: 5px 8px; spacing: 8px; }}
QToolBar::separator {{ background: {c['bordure']}; width: 1px; margin: 4px 6px; }}
QMenuBar {{ background: {c['fond']}; }}
QMenuBar::item {{ padding: 6px 12px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {c['champ']}; }}
QMenu {{ background: {c['panneau']}; border: 1px solid {c['bordure']}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['accent']}; color: {c['sur_accent']}; }}
QMenu::separator {{ height: 1px; background: {c['bordure']}; margin: 5px 8px; }}
QStatusBar {{ background: {c['fond']}; color: {c['discret']}; border-top: 1px solid {c['bordure']}; }}

QLabel {{ background: transparent; }}
QLabel#etat {{ background: {c['champ']}; border: 1px solid {c['bordure']}; border-radius: 10px; padding: 6px 12px; font-weight: 600; }}
QLabel#etat[etat="en_route"] {{ background: {fond_b['accent']}; border-color: {c['accent']}; color: {c['accent']}; }}
QLabel#etat[etat="pause"] {{ background: {fond_b['avert']}; border-color: {c['avert']}; color: {c['avert']}; }}
QLabel#etat[etat="termine"] {{ background: {fond_b['alerte']}; border-color: {c['alerte']}; color: {c['alerte']}; }}
QLabel#altitude {{ color: {c['accent']}; font-family: Consolas, "Cascadia Mono", monospace; font-size: {alt_pt}pt; font-weight: 700; }}
QLabel#mono {{ font-family: Consolas, "Cascadia Mono", monospace; }}

QPushButton {{ background: {c['champ']}; border: 1px solid {c['bordure']}; border-radius: 8px; padding: 4px 12px; }}
QPushButton:hover {{ border-color: {c['accent']}; background: {c['survol']}; }}
QPushButton:pressed {{ background: {c['bordure']}; }}
QPushButton:disabled {{ color: {c['desactive']}; background: {c['panneau']}; }}
QPushButton[role="primaire"] {{ background: {c['accent']}; border-color: {c['accent']}; color: {c['sur_accent']}; font-weight: 700; padding: 5px 16px; }}
QPushButton[role="primaire"]:hover {{ background: {c['accent_survol']}; border-color: {c['accent_survol']}; }}
QPushButton[role="danger"]:hover {{ border-color: {c['alerte']}; color: {c['alerte']}; }}
QPushButton[role="stop"] {{ background: {c['alerte']}; border-color: {c['alerte']}; color: #ffffff; font-weight: 700; padding: 5px 16px; }}
QPushButton[role="stop"]:hover {{ background: {stop_survol}; border-color: {stop_survol}; }}
QLabel#infohaut {{ font-family: Consolas, "Cascadia Mono", monospace; font-size: {taille_pt + 3}pt; font-weight: 700;
    background: {c['champ']}; border: 1px solid {c['bordure']}; border-radius: 8px; padding: 3px 10px; }}
QPushButton[icone="seule"] {{ padding: 4px 10px; }}
QPushButton:checkable {{ padding: 4px 14px; }}
QPushButton:checked {{ background: {c['accent']}; border-color: {c['accent']}; color: {c['sur_accent']}; font-weight: 700; }}
QPushButton:checked:hover {{ background: {c['accent_survol']}; border-color: {c['accent_survol']}; }}
QPushButton#btnModeSim, QPushButton#btnModeDirect {{ font-weight: 600; padding: 5px 14px; }}
QPushButton#btnModeSim:checked {{ background: {c['accent']}; border-color: {c['accent']}; color: {c['sur_accent']}; font-weight: 700; }}
QPushButton#btnModeDirect:checked {{ background: {c['direct']}; border-color: {c['direct']}; color: {c['sur_direct']}; font-weight: 700; }}
QPushButton#btnMaintien:checked {{ background: {c['champ']}; border: 2px solid {c['direct']}; color: {c['direct']}; font-weight: 700; }}
QPushButton#btnMaintien[clignote="1"] {{ background: {c['direct']}; color: {c['sur_direct']}; }}
QPushButton#btnSimGps {{ border: 2px solid {c['direct']}; color: {c['direct']}; font-weight: 700; padding: 5px 14px; }}
QPushButton#btnSimGps[clignote="1"] {{ background: {c['direct']}; color: {c['sur_direct']}; }}
QPushButton[mode="live"]:checked {{ background: {c['direct']}; border-color: {c['direct']}; color: {c['sur_direct']}; }}

QGroupBox[actif="true"] {{ border-color: {c['accent']}; }}
QGroupBox[actif="true"]::title {{ color: {c['accent']}; }}
QGroupBox[actif="live"] {{ border-color: {c['direct']}; }}
QGroupBox[actif="live"]::title {{ color: {c['direct']}; }}

QLabel[chip="sim"] {{ background: {fond_chip['accent']}; border: 1px solid {c['accent']}; color: {c['accent']};
    border-radius: 11px; padding: 3px 12px; font-weight: 700; }}
QLabel[chip="live"] {{ background: {fond_chip['direct']}; border: 1px solid {c['direct']}; color: {c['direct']};
    border-radius: 11px; padding: 3px 12px; font-weight: 700; }}
QLabel[gps="wait"] {{ background: {fond_chip['avert']}; border: 1px solid {c['avert']}; color: {c['avert']};
    border-radius: 10px; padding: 2px 10px; font-weight: 700; }}
QLabel[gps="ok"] {{ background: {fond_chip['direct']}; border: 1px solid {c['direct']}; color: {c['direct']};
    border-radius: 10px; padding: 2px 10px; font-weight: 700; }}
QLabel[gps="lost"] {{ background: {fond_chip['alerte']}; border: 1px solid {c['alerte']}; color: {c['alerte']};
    border-radius: 10px; padding: 2px 10px; font-weight: 700; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {c['champ']}; border: 1px solid {c['bordure']}; border-radius: 7px; padding: 2px 8px;
    selection-background-color: {c['accent']}; selection-color: {c['sur_accent']}; min-height: 18px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {c['accent']}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 18px; border: none; background: transparent; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; border: none; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({img['chevron_haut']}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({img['chevron_bas']}); width: 10px; height: 10px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({img['chevron_bas']}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {c['panneau']}; border: 1px solid {c['bordure']}; selection-background-color: {c['accent']}; selection-color: {c['sur_accent']}; outline: none; }}
QPlainTextEdit {{ font-family: Consolas, "Cascadia Mono", monospace; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {c['bordure_champ']}; background: {c['champ']}; }}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({img['coche']}); }}

QTableWidget {{ background: {c['panneau']}; alternate-background-color: {c['alterne']}; gridline-color: transparent;
                border: 1px solid {c['bordure']}; border-radius: 8px; selection-background-color: {sel_alpha}; selection-color: {c['texte']}; }}
QTableWidget::item {{ padding: 4px 6px; border: none; }}
QHeaderView::section {{ background: {c['fond']}; color: {c['discret']}; border: none; border-bottom: 1px solid {c['bordure']}; padding: 6px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['fond']}; border: none; }}

QProgressBar {{ background: {c['champ']}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['accent']}, stop:1 {c['fin_degrade']}); border-radius: 4px; }}

QGroupBox {{ border: 1px solid {c['bordure']}; border-radius: 10px; margin-top: 14px; padding: 12px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {c['discret']}; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {c['bordure']}; border-radius: 8px; top: -1px; background: {c['panneau']}; }}
QTabWidget > QStackedWidget, QTabWidget > QStackedWidget > QWidget {{ background: {c['panneau']}; }}
QMainWindow > QTabBar {{ background: {c['fond']}; }}
QTabWidget QTabBar {{ background: {c['panneau']}; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; color: {c['discret']}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {c['texte']}; border-bottom-color: {c['accent']}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {c['texte']}; background: {c['survol']}; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['poignee']}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['poignee']}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {c['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {c['panneau']}; color: {c['texte']}; border: 1px solid {c['accent']}; border-radius: 6px; padding: 5px 8px; }}
"""


def appliquer(app: QApplication, mode: str = "sombre", taille_pt: int = TAILLE_DEFAUT) -> dict:
    """Applique le thème (« systeme » | « sombre » | « clair ») : Fusion + palette + QSS + taille du texte.

    `taille_pt` : taille de base du texte en points (s'adapte ensuite au facteur d'échelle DPI de l'OS).
    Retourne la palette."""
    global _courante
    _courante = resoudre(mode)
    _cache_icones.clear()
    c = _courante
    app.setStyle("Fusion")
    p = QPalette()
    for role, coul in ((QPalette.Window, c["fond"]), (QPalette.Base, c["champ"]), (QPalette.AlternateBase, c["panneau"]),
                       (QPalette.Button, c["champ"]), (QPalette.ToolTipBase, c["panneau"])):
        p.setColor(role, QColor(coul))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText):
        p.setColor(role, QColor(c["texte"]))
    p.setColor(QPalette.Highlight, QColor(c["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(c["sur_accent"]))
    p.setColor(QPalette.PlaceholderText, QColor(c["discret"]))
    app.setPalette(p)
    f = QFont("Segoe UI")
    f.setPointSize(taille_pt)
    app.setFont(f)
    app.setStyleSheet(construire_qss(c, taille_pt))
    return c
