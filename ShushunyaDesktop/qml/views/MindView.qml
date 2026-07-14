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
    readonly property string visualState: backend.visualState
    readonly property bool compact: viewportWidth < 1000
    readonly property bool carriesResults: totalScreens < 3
    readonly property color mindColor: visualState === "sleep" ? "#706773"
                                       : visualState === "attention" ? "#58a2a8"
                                       : visualState === "thinking" ? "#75508a"
                                       : visualState === "forging" ? "#d49a50"
                                       : visualState === "waiting" ? "#b32a43"
                                       : visualState === "speaking" ? "#3b8f99"
                                       : visualState === "triumph" ? "#c39a55"
                                       : visualState === "wounded" ? "#b01830" : "#81747d"
    readonly property real spineOpacity: visualState === "sleep" ? .06
                                         : visualState === "attention" ? .18
                                         : visualState === "thinking" ? .38
                                         : visualState === "forging" ? .30
                                         : visualState === "waiting" ? .52
                                         : visualState === "speaking" ? .18
                                         : visualState === "triumph" ? .22
                                         : visualState === "wounded" ? .54 : .05
    clip: true

    Shape {
        anchors.fill: parent
        opacity: view.spineOpacity + (view.visualState === "thinking" ? backend.pulse * .05 : 0)
        antialiasing: true

        Behavior on opacity { NumberAnimation { duration: 720 } }

        ShapePath {
            fillColor: "transparent"
            strokeColor: view.mindColor
            strokeWidth: view.visualState === "waiting" || view.visualState === "wounded" ? 3.4 : 2.2
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
            strokeColor: view.visualState === "forging" || view.visualState === "triumph" ? "#d49a50" : "#9b7658"
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
        x: (view.width - width) / 2 + (view.visualState === "wounded" ? view.width * .03 : 0)
        y: view.height * (view.visualState === "attention" ? .24
                         : view.visualState === "forging" ? .31
                         : view.visualState === "speaking" ? .34 : .285)
        stateColor: view.mindColor
        visualState: view.visualState
        intensity: view.visualState === "sleep" ? .55
                   : view.visualState === "sealing" ? .65
                   : view.visualState === "triumph" ? .88 : .82
        motion: view.motionEnabled

        Behavior on x { NumberAnimation { duration: view.visualState === "wounded" ? 120 : 650; easing.type: Easing.OutCubic } }
        Behavior on y { NumberAnimation { duration: 650; easing.type: Easing.OutCubic } }
    }

    IncisedText {
        id: currentThought
        width: view.width * .82
        x: view.width * .09
        y: view.height * .045
        label: view.visualState === "sleep" ? "ТИШИНА ВНУТРИ"
             : view.visualState === "attention" ? "Я УСЛЫШАЛ"
             : view.visualState === "thinking" ? "СЕЙЧАС"
             : view.visualState === "forging" ? "ЗАМЫСЕЛ СТАЛ ДЕЙСТВИЕМ"
             : view.visualState === "speaking" ? "ГОЛОС"
             : view.visualState === "triumph" ? "ЗАВЕРШЕНО"
             : view.visualState === "wounded" ? "РАЗРЫВ"
             : view.visualState === "sealing" ? "ПАМЯТЬ ЗАПЕЧАТЫВАЕТСЯ" : "СЕЙЧАС"
        heading: view.visualState === "speaking" || view.visualState === "sleep" || view.visualState === "attention"
                 ? backend.companion.utterance
                 : view.visualState === "triumph" || view.visualState === "wounded"
                 ? backend.companion.latestResult
                 : backend.companion.currentActivity
        accent: view.mindColor
        headingPixelSize: view.compact ? 27 : 32
        headingLines: 4
        opacity: view.visualState === "sealing" ? .10
                 : view.visualState === "triumph" ? .38
                 : view.visualState === "waiting" ? .36 : 1.0

        Behavior on opacity { NumberAnimation { duration: view.visualState === "sealing" ? 1500 : 650 } }
    }

    IncisedText {
        visible: view.visualState === "waiting" && backend.companion.ownerRequest.length > 0
        width: view.width * .76
        x: view.width * .12
        y: view.height * .205
        label: "МНЕ НУЖНО ТВОЁ РЕШЕНИЕ"
        heading: backend.companion.ownerRequest
        accent: "#b32a43"
        headingPixelSize: view.compact ? 20 : 23
        headingLines: 4
        reverseCut: true
    }

    Column {
        id: agendaSpine
        visible: view.visualState === "thinking" || view.visualState === "forging"
        opacity: view.visualState === "thinking" ? 1.0 : .72
        width: view.width * .82
        x: view.width * .09
        y: view.height * .69
        spacing: view.compact ? 14 : 18

        Repeater {
            model: backend.companion.agenda
            delegate: Item {
                id: agendaRow
                required property int index
                required property string text
                required property string detail
                required property string phase
                visible: index < (view.visualState === "forging" ? 1 : 2)
                width: agendaSpine.width
                implicitHeight: agendaNode.implicitHeight
                height: visible ? implicitHeight : 0

                IncisedText {
                    id: agendaNode
                    width: parent.width
                    label: agendaRow.phase === "now" ? "УЖЕ НАЧАЛ" : agendaRow.phase === "next" ? "СЛЕДУЮЩЕЕ" : "ПОТОМ"
                    heading: agendaRow.text
                    detail: agendaRow.index === 0 ? agendaRow.detail : ""
                    accent: view.visualState === "forging" ? "#d49a50"
                            : agendaRow.phase === "now" ? "#3b7f89" : "#9b7658"
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
        visible: (view.visualState === "triumph" || view.visualState === "wounded")
                 && backend.companion.hasResults
        width: view.width * .72
        x: view.width * .14
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 16
        label: view.visualState === "wounded" ? "НЕ ЗАВЕРШЕНО" : "ПРИНЕСЕНО"
        heading: backend.companion.latestResult
        accent: view.visualState === "wounded" ? "#b01830" : "#c39a55"
        headingPixelSize: view.compact ? 18 : 21
        headingLines: 3
        centered: true
    }
}
