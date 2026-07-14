import QtQuick
import "../components"

Item {
    id: view
    property string screenLabel: ""
    property real viewportWidth: width
    property real viewportHeight: height
    property bool motionEnabled: true
    property int screenOrdinal: 0
    readonly property string visualState: backend.visualState
    readonly property bool portrait: viewportHeight > viewportWidth
    readonly property color ambientColor: visualState === "sleep" ? "#706773"
                                          : visualState === "attention" ? "#58a2a8"
                                          : visualState === "thinking" ? "#75508a"
                                          : visualState === "forging" ? "#d49a50"
                                          : visualState === "waiting" ? "#b32a43"
                                          : visualState === "speaking" ? "#3b8f99"
                                          : visualState === "triumph" ? "#c39a55"
                                          : visualState === "wounded" ? "#b01830" : "#81747d"
    readonly property real outwardShift: visualState === "attention" ? .08
                                          : visualState === "triumph" ? .05
                                          : visualState === "sealing" ? -.12 : 0
    clip: true

    Image {
        width: Math.min(view.width, view.height) * (view.portrait ? 1.22 : .88)
        height: width
        x: view.screenOrdinal % 2 === 0 ? -width * .38 : view.width - width * .62
        y: view.portrait ? view.height * .04 : -height * .18
        source: Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
        sourceSize.width: Math.max(512, Math.round(width * 1.3))
        sourceSize.height: Math.max(512, Math.round(height * 1.3))
        opacity: view.visualState === "sleep" ? .07
                 : view.visualState === "triumph" ? .34
                 : view.visualState === "sealing" ? .04 : .20
        rotation: view.screenOrdinal % 2 === 0 ? -19 : 24
        asynchronous: true
        cache: true

        Behavior on opacity { NumberAnimation { duration: 760 } }
    }

    LivingSeal {
        id: fragment
        width: Math.min(view.width * (view.portrait ? 1.30 : .88), view.height * (view.portrait ? .73 : 1.18))
        height: width
        x: (view.screenOrdinal % 2 === 0 ? -width * .24 : view.width - width * .76)
           + (view.screenOrdinal % 2 === 0 ? view.width : -view.width) * view.outwardShift
           + (view.visualState === "wounded" ? view.width * .03 : 0)
        y: view.portrait ? view.height * .16 : -view.height * .06
        stateColor: view.ambientColor
        visualState: view.visualState
        intensity: view.visualState === "sleep" ? .28
                   : view.visualState === "attention" ? .96
                   : view.visualState === "thinking" ? .68
                   : view.visualState === "forging" ? .86
                   : view.visualState === "waiting" ? .62
                   : view.visualState === "speaking" ? .92
                   : view.visualState === "triumph" ? 1.02
                   : view.visualState === "wounded" ? .72 : .30
        motion: view.motionEnabled

        Behavior on x { NumberAnimation { duration: view.visualState === "sealing" ? 1500 : 680; easing.type: Easing.OutCubic } }
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
        opacity: view.visualState === "sleep" || view.visualState === "sealing" ? .04
                 : view.visualState === "triumph" ? .22 : .13
        rotation: view.visualState === "wounded" ? 26 : 13

        Behavior on opacity { NumberAnimation { duration: 760 } }
        Behavior on rotation { NumberAnimation { duration: 160 } }
    }
}
