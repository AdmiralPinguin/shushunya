import QtQuick
import "../components"

Item {
    id: view
    property string screenLabel: ""
    property real viewportWidth: width
    property real viewportHeight: height
    property bool motionEnabled: true
    readonly property string visualState: backend.visualState
    readonly property bool compact: viewportWidth < 1500 || viewportHeight < 820
    readonly property bool portrait: viewportHeight > viewportWidth
    readonly property color moodColor: visualState === "sleep" ? "#706773"
                                       : visualState === "attention" ? "#a18a73"
                                       : visualState === "thinking" ? "#75508a"
                                       : visualState === "forging" ? "#d49a50"
                                       : visualState === "waiting" ? "#b32a43"
                                       : visualState === "speaking" ? "#3b8f99"
                                       : visualState === "triumph" ? "#c39a55"
                                       : visualState === "wounded" ? "#b01830"
                                       : visualState === "sealing" ? "#81747d" : "#b6aa96"
    readonly property string moodText: visualState === "sleep" ? "СОН ПОД ПЕЧАТЬЮ"
                                        : visualState === "attention" ? "Я СЛЫШУ ТЕБЯ"
                                        : visualState === "thinking" ? "МЫСЛЬ ШЕВЕЛИТСЯ"
                                        : visualState === "forging" ? "КОВКА"
                                        : visualState === "waiting" ? "ТВОЁ СЛОВО НУЖНО"
                                        : visualState === "speaking" ? "ГОЛОС"
                                        : visualState === "triumph" ? "ДОБЫЧА ПРИНЕСЕНА"
                                        : visualState === "wounded" ? "РАНЕНИЕ"
                                        : visualState === "sealing" ? "ЗАПЕЧАТЫВАНИЕ" : "ПРИСУТСТВИЕ"
    readonly property string mainText: visualState === "waiting" && backend.companion.ownerRequest.length > 0
                                       ? backend.companion.ownerRequest
                                       : backend.companion.utterance
    readonly property string detailText: visualState === "triumph" || visualState === "wounded"
                                         ? backend.companion.latestResult
                                         : visualState === "attention" || visualState === "speaking" || visualState === "sleep"
                                         ? "" : backend.companion.currentActivity
    clip: true

    LivingSeal {
        id: presenceSeal
        width: view.portrait
               ? Math.min(view.width * .88, view.height * .56)
               : Math.min(view.width * .53, view.height * .84)
        height: width
        x: (view.width - width) / 2
           + (view.visualState === "forging" ? view.width * .04
           : view.visualState === "waiting" ? -view.width * .04
           : view.visualState === "wounded" ? view.width * .03 : view.width * .015)
        y: (view.portrait ? view.height * .07 : -view.height * .015)
           + (view.visualState === "sleep" ? -view.height * .04
           : view.visualState === "thinking" || view.visualState === "triumph" ? -view.height * .03
           : view.visualState === "forging" || view.visualState === "wounded" ? view.height * .02
           : view.visualState === "sealing" ? view.height * .05 : 0)
        stateColor: view.moodColor
        visualState: view.visualState
        intensity: .97
        motion: view.motionEnabled

        Behavior on x { NumberAnimation { duration: view.visualState === "wounded" ? 120 : 680; easing.type: Easing.OutCubic } }
        Behavior on y { NumberAnimation { duration: view.visualState === "sealing" ? 1500 : 680; easing.type: Easing.OutCubic } }
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
        id: voice
        width: Math.min(view.width * (view.compact ? .82 : .67), 1180)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: view.compact ? 26 : 40
        label: view.moodText
        heading: view.mainText
        detail: view.detailText
        accent: view.moodColor
        headingColor: view.visualState === "wounded" ? "#e3b7ae" : "#e4d9c6"
        detailColor: view.visualState === "forging" ? "#c7a67c" : "#a89da8"
        headingPixelSize: view.compact ? 31 : view.visualState === "speaking" ? 43 : 39
        detailPixelSize: view.compact ? 13 : 15
        headingLines: view.compact ? 3 : 4
        detailLines: 2
        centered: true
        opacity: view.visualState === "sleep" ? .28
                 : view.visualState === "sealing" ? .12
                 : view.visualState === "thinking" ? .82 : 1.0

        Behavior on opacity { NumberAnimation { duration: view.visualState === "sealing" ? 1500 : 620 } }
    }
}
