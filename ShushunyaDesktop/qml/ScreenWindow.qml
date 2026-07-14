import QtQuick
import QtQuick.Window
import "components"
import "views"

Window {
    id: root
    visible: false
    width: 1920
    height: 1080
    color: "#06040b"
    flags: Qt.Window | Qt.FramelessWindowHint
    title: "Shushunya · " + root.screenRole

    property string screenRole: "presence"
    property string screenKey: ""
    property string screenLabel: ""
    property int screenOrdinal: 0
    property int displayCount: 2
    property bool previewMode: false
    property real screenOriginX: 0
    property real screenOriginY: 0
    property real virtualOriginX: 0
    property real virtualOriginY: 0
    property real virtualDesktopWidth: width
    property real virtualDesktopHeight: height
    property real scaleMultiplier: 1.0
    property real extraSafeLeft: 0
    property real extraSafeRight: 0
    property real extraSafeTop: 0
    property real extraSafeBottom: 0
    readonly property real shortEdge: Math.min(width, height)
    readonly property real baseSafeHorizontal: Math.max(12, Math.min(24, shortEdge * .015))
    readonly property real safeLeft: Math.round(baseSafeHorizontal + Math.max(0, extraSafeLeft))
    readonly property real safeRight: Math.round(baseSafeHorizontal + Math.max(0, extraSafeRight))
    readonly property real safeTop: Math.round(Math.max(10, Math.min(18, shortEdge * .012)) + Math.max(0, extraSafeTop))
    readonly property real safeBottom: Math.round(Math.max(28, Math.min(48, shortEdge * .035)) + Math.max(0, extraSafeBottom))
    readonly property real designWidth: width >= height ? 1920 : 1080
    readonly property real designHeight: width >= height ? 1080 : 1920
    readonly property real naturalScale: Math.min(
        Math.max(1, width - safeLeft - safeRight) / designWidth,
        Math.max(1, height - safeTop - safeBottom) / designHeight
    )
    readonly property real contentScale: Math.max(.62, Math.min(1.18, naturalScale * scaleMultiplier))

    LivingEnvironment {
        anchors.fill: parent
        variant: root.screenRole
        ordinal: root.screenOrdinal
        motion: !root.previewMode
        screenX: root.screenOriginX
        screenY: root.screenOriginY
        virtualX: root.virtualOriginX
        virtualY: root.virtualOriginY
        virtualWidth: root.virtualDesktopWidth
        virtualHeight: root.virtualDesktopHeight
        intensity: root.screenRole === "ambient" ? 1.0
                 : root.screenRole === "presence" ? .92
                 : root.screenRole === "mind" ? .72 : .82
    }

    Item {
        id: safeArea
        anchors.fill: parent
        anchors.leftMargin: root.safeLeft
        anchors.rightMargin: root.safeRight
        anchors.topMargin: root.safeTop
        anchors.bottomMargin: root.safeBottom

        Item {
            id: stage
            anchors.centerIn: parent
            width: safeArea.width / root.contentScale
            height: safeArea.height / root.contentScale
            scale: root.contentScale
            transformOrigin: Item.Center

            Loader {
                anchors.fill: parent
                sourceComponent: root.screenRole === "presence" ? presenceComponent
                               : root.screenRole === "mind" ? mindComponent
                               : root.screenRole === "canvas" ? canvasComponent
                               : ambientComponent
            }
        }
    }

    Component {
        id: presenceComponent
        PresenceView {
            screenLabel: root.screenLabel
            viewportWidth: root.width
            viewportHeight: root.height
            motionEnabled: !root.previewMode
        }
    }
    Component {
        id: mindComponent
        MindView {
            screenLabel: root.screenLabel
            viewportWidth: root.width
            viewportHeight: root.height
            totalScreens: root.displayCount
            motionEnabled: !root.previewMode
        }
    }
    Component {
        id: canvasComponent
        CanvasView {
            screenLabel: root.screenLabel
            viewportWidth: root.width
            viewportHeight: root.height
            motionEnabled: !root.previewMode
        }
    }
    Component {
        id: ambientComponent
        AmbientView {
            screenLabel: root.screenLabel
            viewportWidth: root.width
            viewportHeight: root.height
            motionEnabled: !root.previewMode
            screenOrdinal: root.screenOrdinal
        }
    }

    Shortcut { sequence: "Ctrl+Shift+Q"; onActivated: backend.requestQuit() }
    Shortcut { sequence: "Ctrl+Shift+S"; onActivated: backend.requestSnapshot() }
    Shortcut {
        sequence: "F11"
        onActivated: {
            if (root.visibility === Window.FullScreen)
                root.showNormal()
            else
                root.showFullScreen()
        }
    }
}
