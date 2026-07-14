import QtQuick
import "../components"

Item {
    id: view
    property string screenLabel: ""
    property real viewportWidth: width
    property real viewportHeight: height
    property bool motionEnabled: true
    property int screenOrdinal: 0
    readonly property bool portrait: viewportHeight > viewportWidth
    readonly property color ambientColor: backend.companion.presence === "waiting" ? "#88152d" : "#5e3c69"
    clip: true

    Image {
        width: Math.min(view.width, view.height) * (view.portrait ? 1.22 : .88)
        height: width
        x: view.screenOrdinal % 2 === 0 ? -width * .38 : view.width - width * .62
        y: view.portrait ? view.height * .04 : -height * .18
        source: Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
        sourceSize.width: Math.max(512, Math.round(width * 1.3))
        sourceSize.height: Math.max(512, Math.round(height * 1.3))
        opacity: .23
        rotation: view.screenOrdinal % 2 === 0 ? -19 : 24
        asynchronous: true
        cache: true
    }

    LivingSeal {
        id: fragment
        width: Math.min(view.width * (view.portrait ? 1.30 : .88), view.height * (view.portrait ? .73 : 1.18))
        height: width
        x: view.screenOrdinal % 2 === 0 ? -width * .24 : view.width - width * .76
        y: view.portrait ? view.height * .16 : -view.height * .06
        stateColor: view.ambientColor
        intensity: .76
        motion: view.motionEnabled
    }

    Image {
        width: Math.min(view.width, view.height) * .32
        height: width
        anchors.right: parent.right
        anchors.rightMargin: -width * .14
        anchors.bottom: parent.bottom
        anchors.bottomMargin: -height * .06
        source: Qt.resolvedUrl("../../assets/heresy/horned-skull.svg")
        sourceSize.width: 384
        sourceSize.height: 384
        opacity: .16
        rotation: 13
    }
}
