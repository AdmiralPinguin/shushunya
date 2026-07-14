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
    readonly property color moodColor: backend.companion.presence === "waiting" ? "#88152d"
                                      : backend.companion.presence === "thinking" ? "#745080"
                                      : backend.companion.presence === "speaking" ? "#3b7f89"
                                      : "#b6aa96"
    readonly property string moodText: backend.companion.presence === "waiting" ? "МОЙ ХОД ОСТАНОВЛЕН ТВОИМ СЛОВОМ"
                                       : backend.companion.presence === "thinking" ? "МЫСЛЬ ШЕВЕЛИТСЯ"
                                       : backend.companion.presence === "speaking" ? "ГОЛОС"
                                       : "ПРИСУТСТВИЕ"
    clip: true

    LivingSeal {
        id: presenceSeal
        width: view.portrait
               ? Math.min(view.width * .88, view.height * .56)
               : Math.min(view.width * .53, view.height * .84)
        height: width
        x: view.portrait ? (view.width - width) / 2 : (view.width - width) / 2 + view.width * .035
        y: view.portrait ? view.height * .07 : -view.height * .015
        stateColor: view.moodColor
        intensity: .97
        motion: view.motionEnabled
    }

    Rectangle {
        visible: backend.companion.presence === "waiting"
        width: 3
        height: presenceSeal.height * .64
        x: presenceSeal.x + presenceSeal.width * .69
        y: presenceSeal.y + presenceSeal.height * .18
        rotation: 42
        color: "#88152d"
        opacity: .30 + backend.pulse * .28
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: parent.height * .43
        gradient: Gradient {
            GradientStop { position: 0; color: "#00020104" }
            GradientStop { position: .46; color: "#98020104" }
            GradientStop { position: 1; color: "#f2020104" }
        }
    }

    IncisedText {
        width: Math.min(view.width * (view.compact ? .82 : .67), 1180)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: view.compact ? 26 : 40
        label: view.moodText
        heading: backend.companion.utterance
        detail: backend.companion.currentActivity
        accent: view.moodColor
        headingPixelSize: view.compact ? 31 : 39
        detailPixelSize: view.compact ? 13 : 15
        headingLines: view.compact ? 3 : 4
        detailLines: 2
        centered: true
    }
}
