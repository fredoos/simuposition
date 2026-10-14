// Carte native QtLocation de SimuPosition (schéma MapExtract v6 : plugin « osm » pointé sur le
// proxy de tuiles local, Map recréée par un Loader à chaque changement de source pour que le
// cache QtLocation — isolé par dossier de source — ne mélange jamais deux fonds).
//
// Objet Python exposé dans le contexte : `pont` (propriétés hoteTuiles, cleSource, dossierCache,
// zoomMax, route, points, mobile, modeAjout, suivreMobile ; signaux recentrer(lat, lon, zoom) ;
// slots cliqueCarte(lat, lon), pointDeplace(idx, lat, lon), pointSelectionne(idx), vueChangee(lat, lon, zoom)).
import QtQuick
import QtLocation
import QtPositioning

Rectangle {
    id: root
    color: "#1b1f2a"

    // État de vue conservé à travers les recréations de la Map
    property real vueLat: 49.0287
    property real vueLon: 1.2199
    property real vueZoom: 11
    property bool pret: false
    readonly property var carte: chargeur.item ? chargeur.item.mapRef : null

    function recentrer(lat, lon, zoom) {
        vueLat = lat; vueLon = lon
        if (zoom > 0) vueZoom = zoom
        if (carte) { carte.center = QtPositioning.coordinate(lat, lon); if (zoom > 0) carte.zoomLevel = zoom }
    }

    function ajusterSurRoute() {
        if (!carte || !pont || pont.route.length < 1) return
        var minLat = 90, maxLat = -90, minLon = 180, maxLon = -180
        for (var i = 0; i < pont.route.length; i++) {
            var p = pont.route[i]
            minLat = Math.min(minLat, p.lat); maxLat = Math.max(maxLat, p.lat)
            minLon = Math.min(minLon, p.lon); maxLon = Math.max(maxLon, p.lon)
        }
        if (pont.route.length === 1) { recentrer(minLat, minLon, 12); return }
        var rect = QtPositioning.rectangle(QtPositioning.coordinate(maxLat, minLon), QtPositioning.coordinate(minLat, maxLon))
        carte.visibleRegion = rect
        carte.zoomLevel = Math.max(carte.minimumZoomLevel, carte.zoomLevel - 0.3)
        vueLat = carte.center.latitude; vueLon = carte.center.longitude; vueZoom = carte.zoomLevel
    }

    Connections {
        target: pont
        function onRecentrer(lat, lon, zoom) { root.recentrer(lat, lon, zoom) }
        function onAjusterRoute() { root.ajusterSurRoute() }
        function onMobileChanged() {
            if (pont.suivreMobile && carte && pont.mobile.valide)
                carte.center = QtPositioning.coordinate(pont.mobile.lat, pont.mobile.lon)
        }
    }

    // Recréation de la carte à chaque changement de source (clé + hôte)
    Loader {
        id: chargeur
        anchors.fill: parent
        sourceComponent: pont && pont.hoteTuiles.length > 0 ? composantCarte : null
        onLoaded: root.pret = true
    }
    Connections {
        target: pont
        function onCleSourceChanged() {
            if (carte) { vueLat = carte.center.latitude; vueLon = carte.center.longitude; vueZoom = carte.zoomLevel }
            chargeur.sourceComponent = null
            chargeur.sourceComponent = composantCarte
        }
    }

    Component {
        id: composantCarte
        Item {
            anchors.fill: parent
            property alias mapRef: laCarte

            Plugin {
                id: pluginTuiles
                name: "osm"
                PluginParameter { name: "osm.useragent"; value: "SimuPosition/0.1" }
                PluginParameter { name: "osm.mapping.providersrepository.disabled"; value: true }
                PluginParameter { name: "osm.mapping.custom.host"; value: pont.hoteTuiles }
                PluginParameter { name: "osm.mapping.highdpi_tiles"; value: false }
                PluginParameter { name: "osm.mapping.cache.directory"; value: pont.dossierCache + "/" + pont.cleSource }
                PluginParameter { name: "osm.mapping.cache.disk.cost_strategy"; value: "bytesize" }
                PluginParameter { name: "osm.mapping.cache.disk.size"; value: 52428800 }
            }

            Map {
                id: laCarte
                anchors.fill: parent
                plugin: pluginTuiles
                center: QtPositioning.coordinate(root.vueLat, root.vueLon)
                zoomLevel: root.vueZoom
                minimumZoomLevel: 2
                maximumZoomLevel: pont.zoomMax
                copyrightsVisible: false
                color: "#1b1f2a"

                function choisirFondPersonnalise() {
                    for (var i = 0; i < supportedMapTypes.length; i++)
                        if (supportedMapTypes[i].style === MapType.CustomMap) { activeMapType = supportedMapTypes[i]; return }
                    if (supportedMapTypes.length > 0) activeMapType = supportedMapTypes[supportedMapTypes.length - 1]
                }
                Component.onCompleted: choisirFondPersonnalise()
                onMapReadyChanged: if (mapReady) { choisirFondPersonnalise(); nudge.restart() }
                onCenterChanged: { root.vueLat = laCarte.center.latitude; root.vueLon = laCarte.center.longitude; pont.vueChangee(laCarte.center.latitude, laCarte.center.longitude, laCarte.zoomLevel) }
                onZoomLevelChanged: { root.vueZoom = laCarte.zoomLevel; pont.vueChangee(laCarte.center.latitude, laCarte.center.longitude, laCarte.zoomLevel) }

                // Nudge de re-projection après recréation (cf. MapExtract v6)
                Timer { id: nudge; interval: 0; repeat: false; onTriggered: { laCarte.pan(1, 0); nudgeRetour.restart() } }
                Timer { id: nudgeRetour; interval: 32; repeat: false; onTriggered: laCarte.pan(-1, 0) }

                // --- navigation : pan, molette, clic (ajout de point) -------------------
                MouseArea {
                    id: zoneNav
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    hoverEnabled: true
                    property real x0: 0
                    property real y0: 0
                    property bool deplace: false
                    cursorShape: (pont.modeAjout || pont.modePlacement.length > 0) ? Qt.CrossCursor : Qt.OpenHandCursor
                    onPressed: function(m) { x0 = m.x; y0 = m.y; deplace = false }
                    onPositionChanged: function(m) {
                        if (pressed) {
                            if (Math.abs(m.x - x0) + Math.abs(m.y - y0) > 3) deplace = true
                            laCarte.pan(x0 - m.x, y0 - m.y); x0 = m.x; y0 = m.y
                        }
                        var c = laCarte.toCoordinate(Qt.point(m.x, m.y), false)
                        lectureCoord.text = c.latitude.toFixed(5) + "  " + c.longitude.toFixed(5)
                    }
                    onReleased: function(m) {
                        if (!deplace && m.button === Qt.LeftButton) {
                            var c = laCarte.toCoordinate(Qt.point(m.x, m.y), false)
                            pont.cliqueCarte(c.latitude, c.longitude)
                        }
                    }
                    onWheel: function(w) {
                        var d = w.angleDelta.y > 0 ? 0.5 : -0.5
                        var avant = laCarte.toCoordinate(Qt.point(w.x, w.y), false)
                        laCarte.zoomLevel = Math.max(laCarte.minimumZoomLevel, Math.min(laCarte.maximumZoomLevel, laCarte.zoomLevel + d))
                        var apres = laCarte.toCoordinate(Qt.point(w.x, w.y), false)
                        laCarte.center = QtPositioning.coordinate(laCarte.center.latitude + avant.latitude - apres.latitude,
                                                                  laCarte.center.longitude + avant.longitude - apres.longitude)
                    }
                }

                // --- route ---------------------------------------------------------------
                MapPolyline {
                    id: traceRoute
                    line.width: 3
                    line.color: "#ff9f1c"
                    path: {
                        var p = []
                        for (var i = 0; i < pont.route.length; i++) p.push(QtPositioning.coordinate(pont.route[i].lat, pont.route[i].lon))
                        return p
                    }
                }

                // --- trace GPS réelle (mode LIVE, vert) -----------------------------------------
                MapPolyline {
                    id: traceReelle
                    line.width: 7          // plus large que la bleue : visible dessous quand les traces se superposent
                    line.color: "#2ecc71"
                    z: 1
                    Component.onCompleted: {
                        var t = pont.traceReelleComplete()
                        for (var i = 0; i < t.length; i++) addCoordinate(QtPositioning.coordinate(t[i].lat, t[i].lon))
                    }
                }
                Connections {
                    target: pont
                    function onTraceReellePoint(lat, lon) { traceReelle.addCoordinate(QtPositioning.coordinate(lat, lon)) }
                    function onTraceReelleEffacee() { traceReelle.path = [] }
                }

                // --- trace émise (simulée, bleu) ------------------------------------------------
                MapPolyline {
                    id: traceVol
                    line.width: 3
                    line.color: "#3da5ff"
                    z: 2
                    Component.onCompleted: {
                        var t = pont.traceComplete()
                        for (var i = 0; i < t.length; i++) addCoordinate(QtPositioning.coordinate(t[i].lat, t[i].lon))
                    }
                }
                Connections {
                    target: pont
                    function onTracePoint(lat, lon) { traceVol.addCoordinate(QtPositioning.coordinate(lat, lon)) }
                    function onTraceEffacee() { traceVol.path = [] }
                }

                // --- points de passage (déplaçables) --------------------------------------
                MapItemView {
                    model: pont.points
                    delegate: MapQuickItem {
                        id: itemPoint
                        coordinate: QtPositioning.coordinate(modelData.lat, modelData.lon)
                        anchorPoint.x: 11
                        anchorPoint.y: 11
                        zoomLevel: 0
                        sourceItem: Item {
                            width: 22; height: 22
                            Rectangle {
                                anchors.centerIn: parent
                                width: 22; height: 22; radius: 11
                                color: modelData.selectionne ? "#ffd166" : (modelData.couleur ? modelData.couleur : "#2ec4b6")
                                border.color: "#10222a"; border.width: 2
                                Text { anchors.centerIn: parent; text: modelData.idx + 1; color: "#10222a"; font.pixelSize: 11; font.bold: true }
                            }
                            Text {
                                anchors.left: parent.right; anchors.leftMargin: 4; anchors.verticalCenter: parent.verticalCenter
                                text: modelData.nom; color: "#ffffff"; font.pixelSize: 12
                                style: Text.Outline; styleColor: "#10222a"
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.SizeAllCursor
                                property bool glisse: false
                                onPressed: { glisse = false; pont.pointSelectionne(modelData.idx) }
                                onPositionChanged: function(m) {
                                    if (!pressed) return
                                    glisse = true
                                    var pt = mapToItem(laCarte, m.x, m.y)
                                    itemPoint.coordinate = laCarte.toCoordinate(Qt.point(pt.x, pt.y), false)
                                }
                                onReleased: {
                                    if (glisse) pont.pointDeplace(modelData.idx, itemPoint.coordinate.latitude, itemPoint.coordinate.longitude)
                                }
                            }
                        }
                    }
                }

                // --- waypoints d'offset (mode LIVE auto : départ / arrivée) ---------------------
                MapItemView {
                    model: pont.pointsOffset
                    delegate: MapQuickItem {
                        id: itemBalise
                        coordinate: QtPositioning.coordinate(modelData.lat, modelData.lon)
                        anchorPoint.x: 13; anchorPoint.y: 13
                        zoomLevel: 0
                        z: 2
                        sourceItem: Item {
                            width: 26; height: 26
                            Rectangle {
                                anchors.centerIn: parent
                                width: 26; height: 26; radius: 13
                                color: "#cc10141c"; border.color: modelData.couleur; border.width: 3
                                Text { anchors.centerIn: parent; text: modelData.label; color: modelData.couleur; font.pixelSize: 12; font.bold: true
                                       style: Text.Outline; styleColor: "#10222a" }
                            }
                            // balise déplaçable à la souris (accrochée au trajet au relâcher)
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.SizeAllCursor
                                property bool glisse: false
                                onPressed: glisse = false
                                onPositionChanged: function(m) {
                                    if (!pressed) return
                                    glisse = true
                                    var pt = mapToItem(laCarte, m.x, m.y)
                                    itemBalise.coordinate = laCarte.toCoordinate(Qt.point(pt.x, pt.y), false)
                                }
                                onReleased: {
                                    if (glisse) pont.pointOffsetDeplace(modelData.label, itemBalise.coordinate.latitude, itemBalise.coordinate.longitude)
                                }
                            }
                        }
                    }
                }

                // --- position GPS réelle (mode Direct) : flèche verte orientée au cap ------------
                // masquée quand elle est confondue avec la position émise (halo vert sur la flèche rouge)
                MapQuickItem {
                    id: itemMobileReel
                    visible: pont.mobileReel.valide && !pont.mobileReel.aligne
                    coordinate: QtPositioning.coordinate(pont.mobileReel.lat, pont.mobileReel.lon)
                    anchorPoint.x: 14
                    anchorPoint.y: 14
                    zoomLevel: 0
                    z: 3
                    sourceItem: Item {
                        width: 28; height: 28
                        Canvas {
                            anchors.fill: parent
                            rotation: pont.mobileReel.cap
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.reset()
                                ctx.fillStyle = "#2ecc71"; ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2
                                ctx.beginPath(); ctx.moveTo(14, 2); ctx.lineTo(24, 26); ctx.lineTo(14, 20); ctx.lineTo(4, 26); ctx.closePath()
                                ctx.fill(); ctx.stroke()
                            }
                        }
                    }
                }

                // --- mobile simulé ----------------------------------------------------------
                MapQuickItem {
                    id: itemMobile
                    visible: pont.mobile.valide
                    coordinate: QtPositioning.coordinate(pont.mobile.lat, pont.mobile.lon)
                    anchorPoint.x: 24
                    anchorPoint.y: 24
                    zoomLevel: 0
                    z: 4
                    sourceItem: Item {
                        width: 48; height: 48
                        // halo vert pulsé : GPS réel confondu avec la position émise (offset nul)
                        Rectangle {
                            id: haloAligne
                            anchors.centerIn: parent
                            width: 44; height: 44; radius: 22
                            color: "#332ecc71"; border.color: "#2ecc71"; border.width: 3
                            visible: pont.mobileReel.valide && pont.mobileReel.aligne === true
                            SequentialAnimation on opacity {
                                running: haloAligne.visible
                                loops: Animation.Infinite
                                NumberAnimation { from: 1.0; to: 0.35; duration: 800; easing.type: Easing.InOutQuad }
                                NumberAnimation { from: 0.35; to: 1.0; duration: 800; easing.type: Easing.InOutQuad }
                            }
                        }
                        Canvas {
                            anchors.centerIn: parent
                            width: 28; height: 28
                            rotation: pont.mobile.cap
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.reset()
                                ctx.fillStyle = "#ff3b3b"; ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2
                                ctx.beginPath(); ctx.moveTo(14, 2); ctx.lineTo(24, 26); ctx.lineTo(14, 20); ctx.lineTo(4, 26); ctx.closePath()
                                ctx.fill(); ctx.stroke()
                            }
                        }
                    }
                }
            }
        }
    }

    // --- languettes de repli des panneaux (gauche / droite / bas) ---------------------------
    Rectangle {
        visible: pont && pont.zones.gauche_existe === true
        anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
        width: 18; height: 60; radius: 5; z: 10
        color: mGauche.containsMouse ? "#e62ec4b6" : "#cc10141c"; border.color: "#3a4255"
        Text { anchors.centerIn: parent; text: pont && pont.zones.gauche ? "\u25C0" : "\u25B6"; color: "#dfe6f3"; font.pixelSize: 12 }
        MouseArea { id: mGauche; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: pont.basculerZone("gauche") }
    }
    Rectangle {
        visible: pont && pont.zones.droite_existe === true
        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        width: 18; height: 60; radius: 5; z: 10
        color: mDroite.containsMouse ? "#e62ec4b6" : "#cc10141c"; border.color: "#3a4255"
        Text { anchors.centerIn: parent; text: pont && pont.zones.droite ? "\u25B6" : "\u25C0"; color: "#dfe6f3"; font.pixelSize: 12 }
        MouseArea { id: mDroite; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: pont.basculerZone("droite") }
    }
    Rectangle {
        visible: pont && pont.zones.bas_existe === true
        anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter
        width: 60; height: 18; radius: 5; z: 10
        color: mBas.containsMouse ? "#e62ec4b6" : "#cc10141c"; border.color: "#3a4255"
        Text { anchors.centerIn: parent; text: pont && pont.zones.bas ? "\u25BC" : "\u25B2"; color: "#dfe6f3"; font.pixelSize: 12 }
        MouseArea { id: mBas; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: pont.basculerZone("bas") }
    }

    // --- lecture des coordonnées sous le curseur + échelle -------------------------------
    Rectangle {
        anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 8
        width: lectureCoord.implicitWidth + 16; height: 22; radius: 4
        color: "#cc10141c"; border.color: "#3a4255"
        Text { id: lectureCoord; anchors.centerIn: parent; text: "—"; color: "#dfe6f3"; font.pixelSize: 12; font.family: "Consolas" }
    }
    Rectangle {
        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 8
        width: txtZoom.implicitWidth + 16; height: 22; radius: 4
        color: "#cc10141c"; border.color: "#3a4255"
        Text { id: txtZoom; anchors.centerIn: parent; color: "#dfe6f3"; font.pixelSize: 12; font.family: "Consolas"
               text: "z " + root.vueZoom.toFixed(1) + (pont.modePlacement === "depart" ? "   · clic = placer le DÉPART (D)"
                     : pont.modePlacement === "arrivee" ? "   · clic = placer l'ARRIVÉE (A)"
                     : pont.modeAjout ? "   · clic = ajouter un point" : "") }
    }
}
