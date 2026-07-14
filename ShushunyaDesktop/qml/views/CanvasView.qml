import QtQuick
import "../components"

Item {
    id: view
    property string screenLabel: ""
    property real viewportWidth: width
    property real viewportHeight: height
    property bool motionEnabled: true
    readonly property string visualState: backend.visualState
    readonly property bool compact: viewportWidth < 1250 || viewportHeight < 820
    readonly property bool portrait: viewportHeight > viewportWidth
    readonly property color relicColor: visualState === "sleep" ? "#706773"
                                        : visualState === "attention" ? "#58a2a8"
                                        : visualState === "thinking" ? "#75508a"
                                        : visualState === "forging" ? "#d49a50"
                                        : visualState === "waiting" ? "#b32a43"
                                        : visualState === "speaking" ? "#3b8f99"
                                        : visualState === "triumph" ? "#c39a55"
                                        : visualState === "wounded" ? "#b01830" : "#81747d"
    readonly property bool showRelics: (visualState === "triumph" || visualState === "wounded")
                                        && backend.companion.hasResults
    readonly property string ritualLabel: visualState === "sleep" ? "КУЗНЯ СПИТ"
                                          : visualState === "attention" ? "РУКИ ЖДУТ ЗАМЫСЛА"
                                          : visualState === "thinking" ? "ФОРМА ЕЩЁ В МЫСЛИ"
                                          : visualState === "forging" ? "КУЗНЯ ОТКРЫТА"
                                          : visualState === "waiting" ? "КОВКА ОСТАНОВЛЕНА"
                                          : visualState === "speaking" ? "ГОЛОС ПРОХОДИТ СКВОЗЬ КУЗНЮ"
                                          : visualState === "triumph" ? "РЕЛИКВИЯ ДОБЫТА"
                                          : visualState === "wounded" ? "ПЕЧАТЬ РАЗОРВАНА"
                                          : "КУЗНЯ ЗАПЕЧАТЫВАЕТСЯ"
    readonly property string ritualHeading: visualState === "waiting" ? backend.companion.ownerRequest
                                            : visualState === "triumph" || visualState === "wounded" ? backend.companion.latestResult
                                            : visualState === "speaking" || visualState === "sleep" || visualState === "attention" ? backend.companion.utterance
                                            : backend.companion.currentActivity
    clip: true

    Image {
        id: altarSkull
        width: view.portrait ? view.width * .72 : view.height * .64
        height: width
        x: view.portrait ? view.width * .38 : view.width * .67
        y: view.portrait ? view.height * .11 : view.height * .16
        source: Qt.resolvedUrl("../../assets/heresy/horned-skull.svg")
        sourceSize.width: Math.max(512, Math.round(width * 1.4))
        sourceSize.height: Math.max(512, Math.round(height * 1.4))
        opacity: view.visualState === "sleep" ? .04
                 : view.visualState === "forging" ? .22
                 : view.visualState === "triumph" ? .18
                 : view.visualState === "wounded" ? .20
                 : view.visualState === "sealing" ? .03 : .10
        rotation: view.visualState === "wounded" ? 8 : -8
        asynchronous: true
        cache: true

        Behavior on opacity { NumberAnimation { duration: 760 } }
        Behavior on rotation { NumberAnimation { duration: 180 } }
    }

    LivingSeal {
        id: altarSeal
        width: view.portrait
               ? Math.min(view.width * .57, view.height * .31)
               : Math.min(view.width * .31, view.height * .55)
        height: width
        x: (view.portrait ? view.width * .48 : view.width * .67)
           + (view.visualState === "forging" ? -view.width * .04
           : view.visualState === "sealing" ? view.width * .08 : 0)
        y: view.portrait ? view.height * .30 : view.height * .34
        stateColor: view.relicColor
        visualState: view.visualState
        intensity: view.visualState === "sleep" ? .20
                   : view.visualState === "attention" ? .42
                   : view.visualState === "thinking" ? .55
                   : view.visualState === "forging" ? 1.06
                   : view.visualState === "waiting" ? .55
                   : view.visualState === "speaking" ? .34
                   : view.visualState === "triumph" ? 1.0
                   : view.visualState === "wounded" ? .82 : .28
        motion: view.motionEnabled

        Behavior on x { NumberAnimation { duration: view.visualState === "sealing" ? 1500 : 680; easing.type: Easing.OutCubic } }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: view.portrait ? parent.width : parent.width * .64
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#ed020104" }
            GradientStop { position: .70; color: view.portrait ? "#b8020104" : "#c4020104" }
            GradientStop { position: 1; color: view.portrait ? "#4b020104" : "#00020104" }
        }
    }

    IncisedText {
        id: resultInscription
        width: view.portrait ? view.width * .82 : view.width * .55
        x: view.portrait ? view.width * .09 : view.width * .055
        y: view.portrait ? view.height * .57 : view.height * .18
        label: view.ritualLabel
        heading: view.ritualHeading
        detail: view.visualState === "triumph" ? "Не процесс. Не отчёт. Только то, что я принёс тебе."
                : view.visualState === "forging" ? "Огонь поднят. Результат ещё не объявлен."
                : view.visualState === "wounded" ? "Я не стану называть поражение победой." : ""
        accent: view.relicColor
        headingColor: view.visualState === "wounded" ? "#e3b7ae" : "#e4d9c6"
        headingPixelSize: view.compact ? 33 : 46
        detailPixelSize: view.compact ? 13 : 15
        headingLines: 5
        detailLines: 2
        opacity: view.visualState === "sleep" ? .15
                 : view.visualState === "attention" ? .40
                 : view.visualState === "thinking" ? .55
                 : view.visualState === "speaking" ? .22
                 : view.visualState === "sealing" ? .05 : 1.0

        Behavior on opacity { NumberAnimation { duration: view.visualState === "sealing" ? 1500 : 700 } }
    }

    Row {
        visible: view.showRelics
        spacing: view.compact ? 20 : 30
        x: resultInscription.x + 24
        anchors.bottom: parent.bottom
        anchors.bottomMargin: view.compact ? 28 : 46

        Repeater {
            model: backend.companion.results
            delegate: Item {
                required property int index
                required property string phase
                visible: index < 3
                width: visible ? (view.compact ? 68 : 84) : 0
                height: width

                Image {
                    anchors.fill: parent
                    source: phase === "failed"
                            ? Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
                            : Qt.resolvedUrl("../../assets/heresy/fractured-chaos-seal.svg")
                    sourceSize.width: 192
                    sourceSize.height: 192
                    opacity: index === 0 ? .56 : .26
                    rotation: index * 17 - 8
                }
            }
        }
    }
}
