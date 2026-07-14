import QtQuick
import QtQuick.Shapes
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
    readonly property color mindColor: backend.companion.presence === "waiting" ? "#88152d" : "#745080"
    clip: true

    Shape {
        anchors.fill: parent
        opacity: .30 + backend.pulse * .05
        antialiasing: true

        ShapePath {
            fillColor: "transparent"
            strokeColor: view.mindColor
            strokeWidth: 2.2
            capStyle: ShapePath.RoundCap
            startX: view.width * .52
            startY: -20
            PathLine { x: view.width * .47; y: view.height * .18 }
            PathLine { x: view.width * .54; y: view.height * .34 }
            PathLine { x: view.width * .48; y: view.height * .51 }
            PathLine { x: view.width * .55; y: view.height * .70 }
            PathLine { x: view.width * .49; y: view.height * 1.04 }
        }

        ShapePath {
            fillColor: "transparent"
            strokeColor: "#9b7658"
            strokeWidth: 1
            startX: view.width * .22
            startY: view.height * .13
            PathLine { x: view.width * .50; y: view.height * .30 }
            PathLine { x: view.width * .82; y: view.height * .43 }
            PathLine { x: view.width * .50; y: view.height * .57 }
            PathLine { x: view.width * .18; y: view.height * .77 }
        }
    }

    LivingSeal {
        id: thoughtCore
        width: Math.min(view.width * .82, view.height * .43)
        height: width
        x: (view.width - width) / 2
        y: view.height * .285
        stateColor: view.mindColor
        intensity: .82
        motion: view.motionEnabled
    }

    IncisedText {
        id: currentThought
        width: view.width * .82
        x: view.width * .09
        y: view.height * .045
        label: backend.companion.hasActivities ? "СЕЙЧАС" : "ТИШИНА ВНУТРИ"
        heading: backend.companion.currentActivity.length > 0
                 ? backend.companion.currentActivity
                 : "Я ничего не изображаю. Я просто рядом."
        accent: backend.companion.hasActivities ? "#8b7194" : "#786f76"
        headingPixelSize: view.compact ? 27 : 32
        headingLines: 4
    }

    IncisedText {
        visible: backend.companion.ownerRequest.length > 0
        width: view.width * .76
        x: view.width * .12
        y: view.height * .205
        label: "МНЕ НУЖНО ТВОЁ РЕШЕНИЕ"
        heading: backend.companion.ownerRequest
        accent: "#9b1d37"
        headingPixelSize: view.compact ? 20 : 23
        headingLines: 4
        reverseCut: true
    }

    Column {
        id: agendaSpine
        width: view.width * .82
        x: view.width * .09
        y: view.height * .69
        spacing: view.compact ? 14 : 18

        IncisedText {
            visible: !backend.companion.hasAgenda
            width: parent.width
            label: "ДАЛЬШЕ"
            heading: "Пока не строю замыслов за твоей спиной."
            accent: "#746678"
            headingPixelSize: view.compact ? 21 : 24
            headingLines: 2
        }

        Repeater {
            model: backend.companion.agenda
            delegate: Item {
                id: agendaRow
                required property int index
                required property string text
                required property string detail
                required property string phase
                visible: index < 2
                width: agendaSpine.width
                implicitHeight: agendaNode.implicitHeight
                height: visible ? implicitHeight : 0

                IncisedText {
                    id: agendaNode
                    width: parent.width
                    label: agendaRow.phase === "now" ? "УЖЕ НАЧАЛ" : agendaRow.phase === "next" ? "СЛЕДУЮЩЕЕ" : "ПОТОМ"
                    heading: agendaRow.text
                    detail: agendaRow.index === 0 ? agendaRow.detail : ""
                    accent: agendaRow.phase === "now" ? "#3b7f89" : agendaRow.phase === "next" ? "#9b7658" : "#70527a"
                    headingPixelSize: view.compact ? 19 : 22
                    detailPixelSize: 12
                    headingLines: 3
                    detailLines: 2
                    reverseCut: agendaRow.index % 2 === 1
                }
            }
        }
    }

    IncisedText {
        visible: view.carriesResults && backend.companion.hasResults
        width: view.width * .72
        x: view.width * .14
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 16
        label: "ПРИНЕСЕНО"
        heading: backend.companion.latestResult
        accent: "#3b7f89"
        headingPixelSize: view.compact ? 18 : 21
        headingLines: 2
        centered: true
    }
}
