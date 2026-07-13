import QtQuick
import "../components"

Item {
    id: view
    property string screenLabel: ""
    property real viewportWidth: width
    property real viewportHeight: height
    property bool motionEnabled: true
    readonly property bool compact: viewportWidth < 1250 || viewportHeight < 820
    readonly property bool portrait: viewportHeight > viewportWidth

    LivingSeal {
        id: ambientSeal
        width: Math.min(view.width * (view.portrait ? 1.18 : .78), view.height * (view.portrait ? .77 : 1.08))
        height: width
        anchors.horizontalCenter: parent.horizontalCenter
        y: view.portrait ? view.height * .08 : -view.height * .08
        intensity: .94
        motion: view.motionEnabled
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: parent.height * .38
        gradient: Gradient {
            GradientStop { position: 0; color: "#00030107" }
            GradientStop { position: 1; color: "#f0030107" }
        }
    }

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: view.compact ? 18 : 34
        text: "Ш У Ш У Н Я"
        color: "#d9c9b2"
        opacity: .22
        font.family: "DejaVu Serif"
        font.pixelSize: view.compact ? 13 : 16
        font.bold: true
        font.letterSpacing: 7
    }

    RitualInscription {
        width: Math.min(view.width * (view.portrait ? .88 : .68), 1180)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: view.compact ? 24 : 42
        label: "присутствие"
        heading: backend.companion.utterance
        detail: backend.companion.currentActivity
        accent: backend.companion.presence === "waiting" ? "#b52a46" : "#a855f7"
        glyphSource: Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
        headingPixelSize: view.compact ? 22 : 29
        detailPixelSize: view.compact ? 12 : 14
        headingLines: 3
        detailLines: 2
        centered: true
        backgroundColor: "#b807040d"
    }
}
