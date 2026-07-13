import QtQuick
import QtQuick.Controls
import "../components"

Item {
    id: view
    property string screenLabel: ""
    property real viewportWidth: width
    property real viewportHeight: height
    property int totalScreens: 2
    property bool motionEnabled: true
    readonly property bool compact: viewportWidth < 1000
    readonly property bool carriesResults: totalScreens < 3

    LivingSeal {
        width: Math.min(view.width * .94, view.height * .52)
        height: width
        anchors.horizontalCenter: parent.horizontalCenter
        y: view.height * .24
        intensity: .25
        motion: view.motionEnabled
    }

    Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight + 70
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        clip: true

        Column {
            id: content
            width: scroll.width - (view.compact ? 38 : 58)
            x: (scroll.width - width) / 2
            y: view.compact ? 22 : 30
            spacing: view.compact ? 17 : 23

            QuietHeader {
                width: parent.width
                title: backend.companion.name
                subtitle: "то, что шевелится внутри"
                accent: backend.companion.presence === "waiting" ? "#b52a46" : "#a855f7"
            }

            Column {
                width: parent.width
                spacing: 8
                Text {
                    text: "ВНУТРИ МЕНЯ"
                    color: "#c6a0df"
                    font.family: "DejaVu Sans"
                    font.pixelSize: 12
                    font.bold: true
                    font.letterSpacing: 3
                }
                Text {
                    width: parent.width
                    text: "Сейчас и дальше"
                    color: "#f2e7d2"
                    font.family: "DejaVu Serif"
                    font.pixelSize: view.compact ? 34 : 43
                    font.bold: true
                    wrapMode: Text.WordWrap
                }
            }

            RitualInscription {
                visible: backend.companion.ownerRequest.length > 0
                width: parent.width
                label: "нужно от тебя"
                heading: backend.companion.ownerRequest
                accent: "#b52a46"
                glyphSource: Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
                headingPixelSize: view.compact ? 20 : 24
                headingLines: 5
                backgroundColor: "#8c160713"
            }

            Text {
                text: "ЧЕМ Я ЗАНЯТ"
                color: "#4debff"
                font.family: "DejaVu Sans"
                font.pixelSize: 12
                font.bold: true
                font.letterSpacing: 2.6
            }

            RitualInscription {
                visible: !backend.companion.hasActivities
                width: parent.width
                label: "тишина"
                heading: "Сейчас я ничего не тащу за кулисами. Я рядом."
                accent: "#6d5a7b"
                glyphSource: Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
                headingPixelSize: view.compact ? 21 : 25
                backgroundColor: "#3707040c"
            }

            Repeater {
                model: backend.companion.activities
                delegate: Item {
                    id: activityRow
                    required property string text
                    required property string detail
                    required property string phase
                    width: content.width
                    implicitHeight: activityNode.implicitHeight
                    height: implicitHeight

                    RitualInscription {
                        id: activityNode
                        width: parent.width
                        label: activityRow.phase === "waiting" ? "жду тебя" : activityRow.phase === "now" ? "сейчас" : "держу в работе"
                        heading: activityRow.text
                        detail: activityRow.detail
                        accent: activityRow.phase === "waiting" ? "#b52a46" : activityRow.phase === "now" ? "#4debff" : "#b89552"
                        glyphSource: activityRow.phase === "waiting"
                                     ? Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
                                     : Qt.resolvedUrl("../../assets/heresy/chaos-star.svg")
                        headingPixelSize: view.compact ? 19 : 23
                        detailPixelSize: view.compact ? 12 : 14
                        headingLines: 4
                        detailLines: 4
                        backgroundColor: activityRow.phase === "waiting" ? "#72150713" : "#4d07040c"
                    }
                }
            }

            Rectangle { width: parent.width; height: 1; color: "#4fb89552" }

            Text {
                text: "ДАЛЬШЕ"
                color: "#b89552"
                font.family: "DejaVu Sans"
                font.pixelSize: 12
                font.bold: true
                font.letterSpacing: 2.6
            }

            RitualInscription {
                visible: !backend.companion.hasAgenda
                width: parent.width
                label: "без тайного плана"
                heading: "Пока не строю замыслов за твоей спиной."
                accent: "#7a687d"
                glyphSource: Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
                headingPixelSize: view.compact ? 20 : 24
                backgroundColor: "#3707040c"
            }

            Repeater {
                model: backend.companion.agenda
                delegate: Item {
                    id: agendaRow
                    required property string text
                    required property string detail
                    required property string phase
                    width: content.width
                    implicitHeight: agendaNode.implicitHeight
                    height: implicitHeight

                    RitualInscription {
                        id: agendaNode
                        width: parent.width
                        label: agendaRow.phase === "now" ? "уже начал" : agendaRow.phase === "next" ? "следующее" : "потом"
                        heading: agendaRow.text
                        detail: agendaRow.detail
                        accent: agendaRow.phase === "now" ? "#4debff" : agendaRow.phase === "next" ? "#b89552" : "#8b6da0"
                        glyphSource: Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
                        headingPixelSize: view.compact ? 19 : 22
                        detailPixelSize: view.compact ? 12 : 13
                        headingLines: 3
                        detailLines: 3
                        backgroundColor: "#4307040c"
                    }
                }
            }

            Rectangle {
                visible: view.carriesResults
                width: parent.width
                height: visible ? 1 : 0
                color: "#4fa855f7"
            }

            Text {
                visible: view.carriesResults
                text: "ЧТО Я ПРИНЁС"
                color: "#b782e2"
                font.family: "DejaVu Sans"
                font.pixelSize: 12
                font.bold: true
                font.letterSpacing: 2.6
            }

            RitualInscription {
                visible: view.carriesResults && !backend.companion.hasResults
                width: parent.width
                label: "пока пусто"
                heading: "Ничего нового ещё не принёс."
                accent: "#77647c"
                glyphSource: Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
                headingPixelSize: view.compact ? 20 : 24
                backgroundColor: "#3707040c"
            }

            RitualInscription {
                visible: view.carriesResults && backend.companion.hasResults
                width: parent.width
                label: "последняя печать"
                heading: backend.companion.latestResult
                accent: "#4debff"
                glyphSource: Qt.resolvedUrl("../../assets/heresy/chaos-star.svg")
                headingPixelSize: view.compact ? 20 : 23
                headingLines: 3
                backgroundColor: "#5007040c"
            }
        }

        ScrollBar.vertical: ScrollBar {
            policy: scroll.contentHeight > scroll.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
            width: 3
            opacity: .28
        }

        SequentialAnimation {
            running: scroll.contentHeight > scroll.height + 24
            loops: Animation.Infinite
            PauseAnimation { duration: 5200 }
            NumberAnimation {
                target: scroll
                property: "contentY"
                to: Math.max(0, scroll.contentHeight - scroll.height)
                duration: Math.max(6500, (scroll.contentHeight - scroll.height) * 16)
                easing.type: Easing.InOutSine
            }
            PauseAnimation { duration: 4200 }
            NumberAnimation {
                target: scroll
                property: "contentY"
                to: 0
                duration: 1400
                easing.type: Easing.InOutCubic
            }
        }
    }
}
