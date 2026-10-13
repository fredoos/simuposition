# -*- coding: utf-8 -*-
"""Objet de liaison Python ↔ QML pour la carte (exposé sous le nom `pont`)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Property, Signal, Slot


class PontCarte(QObject):
    hoteTuilesChanged = Signal()
    cleSourceChanged = Signal()
    dossierCacheChanged = Signal()
    zoomMaxChanged = Signal()
    routeChanged = Signal()
    pointsChanged = Signal()
    mobileChanged = Signal()
    modeAjoutChanged = Signal()
    suivreMobileChanged = Signal()
    # vers QML
    recentrer = Signal(float, float, float)
    ajusterRoute = Signal()
    tracePoint = Signal(float, float)      # point ajouté à la trace du vol
    traceEffacee = Signal()
    # depuis QML (relayés à la fenêtre)
    carteCliquee = Signal(float, float)
    pointDeplaceSignal = Signal(int, float, float)
    pointSelectionneSignal = Signal(int)
    vueChangeeSignal = Signal(float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hote = ""
        self._cle = ""
        self._cache = ""
        self._zoom_max = 19
        self._route: list = []
        self._points: list = []
        self._mobile = {"lat": 0.0, "lon": 0.0, "cap": 0.0, "valide": False}
        self._mode_ajout = False
        self._suivre = True
        self._trace: list = []

    # ---- trace du vol (incrémentale : un point à la fois, jamais la liste complète) ----------
    def ajouter_trace(self, lat: float, lon: float) -> None:
        self._trace.append((lat, lon))
        self.tracePoint.emit(lat, lon)

    def effacer_trace(self) -> None:
        self._trace.clear()
        self.traceEffacee.emit()

    def trace_vol(self) -> list:
        return self._trace

    @Slot(result="QVariantList")
    def traceComplete(self):
        """Relecture complète (recréation de la carte après changement de fond)."""
        return [{"lat": a, "lon": b} for a, b in self._trace]

    # ---- propriétés lues par QML ------------------------------------------------------
    def _get_hote(self): return self._hote
    def _set_hote(self, v):
        if v != self._hote:
            self._hote = v
            self.hoteTuilesChanged.emit()
    hoteTuiles = Property(str, _get_hote, _set_hote, notify=hoteTuilesChanged)

    def _get_cle(self): return self._cle
    def _set_cle(self, v):
        if v != self._cle:
            self._cle = v
            self.cleSourceChanged.emit()
    cleSource = Property(str, _get_cle, _set_cle, notify=cleSourceChanged)

    def _get_cache(self): return self._cache
    def _set_cache(self, v):
        self._cache = v
        self.dossierCacheChanged.emit()
    dossierCache = Property(str, _get_cache, _set_cache, notify=dossierCacheChanged)

    def _get_zoom_max(self): return self._zoom_max
    def _set_zoom_max(self, v):
        self._zoom_max = int(v)
        self.zoomMaxChanged.emit()
    zoomMax = Property(int, _get_zoom_max, _set_zoom_max, notify=zoomMaxChanged)

    def _get_route(self): return self._route
    def _set_route(self, v):
        self._route = list(v)
        self.routeChanged.emit()
    route = Property("QVariantList", _get_route, _set_route, notify=routeChanged)

    def _get_points(self): return self._points
    def _set_points(self, v):
        self._points = list(v)
        self.pointsChanged.emit()
    points = Property("QVariantList", _get_points, _set_points, notify=pointsChanged)

    def _get_mobile(self): return self._mobile
    def _set_mobile(self, v):
        self._mobile = dict(v)
        self.mobileChanged.emit()
    mobile = Property("QVariantMap", _get_mobile, _set_mobile, notify=mobileChanged)

    def _get_mode_ajout(self): return self._mode_ajout
    def _set_mode_ajout(self, v):
        self._mode_ajout = bool(v)
        self.modeAjoutChanged.emit()
    modeAjout = Property(bool, _get_mode_ajout, _set_mode_ajout, notify=modeAjoutChanged)

    def _get_suivre(self): return self._suivre
    def _set_suivre(self, v):
        self._suivre = bool(v)
        self.suivreMobileChanged.emit()
    suivreMobile = Property(bool, _get_suivre, _set_suivre, notify=suivreMobileChanged)

    # ---- slots appelés par QML ----------------------------------------------------------
    @Slot(float, float)
    def cliqueCarte(self, lat: float, lon: float):
        self.carteCliquee.emit(lat, lon)

    @Slot(int, float, float)
    def pointDeplace(self, idx: int, lat: float, lon: float):
        self.pointDeplaceSignal.emit(idx, lat, lon)

    @Slot(int)
    def pointSelectionne(self, idx: int):
        self.pointSelectionneSignal.emit(idx)

    @Slot(float, float, float)
    def vueChangee(self, lat: float, lon: float, zoom: float):
        self.vueChangeeSignal.emit(lat, lon, zoom)

    # ---- aides côté Python ----------------------------------------------------------
    def definir_source(self, cle: str, hote: str, zoom_max: int) -> None:
        """Change de fond : met à jour l'hôte et la clé (la clé déclenche la recréation QML)."""
        self._hote = hote
        self._zoom_max = int(zoom_max)
        self.hoteTuilesChanged.emit()
        self.zoomMaxChanged.emit()
        self._set_cle(cle)
