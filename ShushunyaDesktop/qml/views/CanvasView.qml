import QtQuick
import QtQuick.Controls
import "../components"

Item {
    id: view
    property string screenLabel: ""
    property real viewportWidth: width
    property real viewportHeight: height
    property bool motionEnabled: true
    readonly property bool compact: viewportWidth < 1250 || viewportHeight < 820
    readonly property bool portrait: viewportHeight > viewportWidth
    readonly property bool wide: viewportWidth / Math.max(1, viewportHeight) > 1.35
    clip: true

    LivingSeal {
        id: relicSeal
        width: view.wide
               ? Math.min(view.width * .58, view.height * .92)
               : Math.min(view.width * .92, view.height * .62)
        height: width
        x: view.wide ? view.width - width * .91 : (view.width - width) / 2
        y: view.wide ? view.height * .02 : view.height * .17
        intensity: view.wide ? .82 : .48
        motion: view.motionEnabled
    }

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: view.wide ? parent.width * .66 : parent.width
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#e8030107" }
            GradientStop { position: .72; color: view.wide ? "#b8030107" : "#9e030107" }
            GradientStop { position: 1; color: view.wide ? "#00030107" : "#65030107" }
        }
    }

    QuietHeader {
        id: header
        anchors.left: parent.left
        anchors.leftMargin: view.compact ? 18 : 34
        anchors.top: parent.top
        anchors.topMargin: view.compact ? 12 : 24
        title: backend.companion.name
        subtitle: "добытое, завершённое, найденное"
        accent: backend.companion.hasResults ? "#4debff" : "#a855f7"
    }

    Flickable {
        id: resultsScroll
        anchors.left: parent.left
        anchors.top: header.bottom
        anchors.bottom: parent.bottom
        anchors.leftMargin: view.compact ? 24 : 46
        anchors.topMargin: view.compact ? 4 : 12
        anchors.bottomMargin: view.compact ? 18 : 28
        width: view.wide ? parent.width * .57 : parent.width - anchors.leftMargin - (view.compact ? 24 : 46)
        contentWidth: width
        contentHeight: resultsColumn.implicitHeight + 24
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        clip: true

        Column {
            id: resultsColumn
            width: resultsScroll.width
            spacing: view.compact ? 15 : 20

            Column {
                width: parent.width
                spacing: 7

                Text {
                    text: "РЕЛИКВАРИЙ"
                    color: backend.companion.hasResults ? "#4debff" : "#a855f7"
                    font.family: "DejaVu Sans"
                    font.pixelSize: 12
                    font.bold: true
                    font.letterSpacing: 3
                }

                Text {
                    width: parent.width
                    text: "Что я принёс"
                    color: "#f2e7d2"
                    font.family: "DejaVu Serif"
                    font.pixelSize: view.compact ? 35 : 48
                    font.bold: true
                    wrapMode: Text.WordWrap
                }
            }

            RitualInscription {
                visible: !backend.companion.hasResults
                width: parent.width
                label: "печать не нарушена"
                heading: "Здесь пока тихо"
                detail: "Когда я закончу работу, найду или соберу что-то для тебя, результат проступит здесь."
                accent: "#8b6da0"
                glyphSource: Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
                headingPixelSize: view.compact ? 23 : 29
                detailPixelSize: view.compact ? 13 : 15
                backgroundColor: "#7607040c"
            }

            Repeater {
                model: backend.companion.results
                delegate: Item {
                    id: resultRow
                    required property string text
                    required property string detail
                    required property string phase
                    width: resultsColumn.width
                    implicitHeight: resultNode.implicitHeight
                    height: implicitHeight

                    RitualInscription {
                        id: resultNode
                        width: parent.width
                        label: resultRow.phase === "done" ? "завершено" : resultRow.phase === "failed" ? "печать сорвана" : "результат"
                        heading: resultRow.text
                        detail: resultRow.detail
                        accent: resultRow.phase === "done" ? "#4debff" : resultRow.phase === "failed" ? "#b52a46" : "#a855f7"
                        glyphSource: resultRow.phase === "failed"
                                     ? Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
                                     : Qt.resolvedUrl("../../assets/heresy/chaos-star.svg")
                        headingPixelSize: view.compact ? 21 : 27
                        detailPixelSize: view.compact ? 12 : 14
                        headingLines: 4
                        detailLines: 6
                        backgroundColor: resultRow.phase === "failed" ? "#76150713" : "#7607040c"
                    }
                }
            }
        }

        ScrollBar.vertical: ScrollBar {
            policy: resultsScroll.contentHeight > resultsScroll.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
            width: 3
            opacity: .28
        }

        SequentialAnimation {
            running: resultsScroll.contentHeight > resultsScroll.height + 24
            loops: Animation.Infinite
            PauseAnimation { duration: 5200 }
            NumberAnimation {
                target: resultsScroll
                property: "contentY"
                to: Math.max(0, resultsScroll.contentHeight - resultsScroll.height)
                duration: Math.max(6500, (resultsScroll.contentHeight - resultsScroll.height) * 16)
                easing.type: Easing.InOutSine
            }
            PauseAnimation { duration: 4200 }
            NumberAnimation {
                target: resultsScroll
                property: "contentY"
                to: 0
                duration: 1400
                easing.type: Easing.InOutCubic
            }
        }
    }
}
