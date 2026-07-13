import QtQuick
import "../components"

Item {
    id: view
    property string screenLabel: ""
    property real viewportWidth: width
    property real viewportHeight: height
    property bool motionEnabled: true
    readonly property bool compact: viewportWidth < 1500 || viewportHeight < 820
    readonly property bool portrait: viewportHeight > viewportWidth
    readonly property color moodColor: backend.companion.presence === "waiting" ? "#b52a46"
                                      : backend.companion.presence === "thinking" ? "#a855f7"
                                      : backend.companion.presence === "speaking" ? "#4debff"
                                      : "#e8dec7"
    readonly property string moodText: backend.companion.presence === "waiting" ? "жду тебя"
                                       : backend.companion.presence === "thinking" ? "мыслю"
                                       : backend.companion.presence === "speaking" ? "говорю"
                                       : "рядом"

    LivingSeal {
        id: presenceSeal
        width: Math.min(view.width * (view.portrait ? .86 : .61), view.height * (view.portrait ? .54 : .86))
        height: width
        anchors.horizontalCenter: parent.horizontalCenter
        y: view.portrait ? view.height * .04 : -view.height * .015
        intensity: .96
        motion: view.motionEnabled
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: parent.height * .44
        gradient: Gradient {
            GradientStop { position: 0; color: "#00030107" }
            GradientStop { position: 1; color: "#f2030107" }
        }
    }

    QuietHeader {
        anchors.left: parent.left
        anchors.leftMargin: 20
        anchors.top: parent.top
        anchors.topMargin: 13
        title: backend.companion.name
        subtitle: view.moodText
        accent: view.moodColor
    }

    RitualInscription {
        width: Math.min(view.width * (view.compact ? .86 : .72), 1220)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: view.compact ? 28 : 45
        label: view.moodText
        heading: backend.companion.utterance
        detail: backend.companion.currentActivity
        accent: view.moodColor
        glyphSource: Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
        headingPixelSize: view.compact ? 27 : 35
        detailPixelSize: view.compact ? 13 : 15
        headingLines: view.compact ? 3 : 4
        centered: true
        backgroundColor: "#c407040d"
    }
}
