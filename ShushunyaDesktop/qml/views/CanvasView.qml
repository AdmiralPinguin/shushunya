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
    readonly property color relicColor: backend.companion.hasResults ? "#9b7658" : "#60416c"
    clip: true

    Image {
        width: view.portrait ? view.width * .72 : view.height * .64
        height: width
        x: view.portrait ? view.width * .38 : view.width * .67
        y: view.portrait ? view.height * .11 : view.height * .16
        source: Qt.resolvedUrl("../../assets/heresy/horned-skull.svg")
        sourceSize.width: Math.max(512, Math.round(width * 1.4))
        sourceSize.height: Math.max(512, Math.round(height * 1.4))
        opacity: .13
        rotation: -8
        asynchronous: true
        cache: true
    }

    LivingSeal {
        id: altarSeal
        width: view.portrait
               ? Math.min(view.width * .57, view.height * .31)
               : Math.min(view.width * .31, view.height * .55)
        height: width
        x: view.portrait ? view.width * .48 : view.width * .67
        y: view.portrait ? view.height * .30 : view.height * .34
        stateColor: view.relicColor
        intensity: backend.companion.hasResults ? .64 : .38
        motion: view.motionEnabled
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
        label: backend.companion.hasResults ? "РЕЛИКВИЯ ДОБЫТА" : "ПЕЧАТЬ НЕ НАРУШЕНА"
        heading: backend.companion.hasResults ? backend.companion.latestResult : "Здесь пока тихо."
        detail: backend.companion.hasResults ? "Не процесс. Не отчёт. Только то, что я принёс тебе." : ""
        accent: view.relicColor
        headingPixelSize: view.compact ? 33 : 46
        detailPixelSize: view.compact ? 13 : 15
        headingLines: 5
        detailLines: 2
    }

    Row {
        visible: backend.companion.hasResults
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
                    opacity: index === 0 ? .44 : .22
                    rotation: index * 17 - 8
                }
            }
        }
    }
}
