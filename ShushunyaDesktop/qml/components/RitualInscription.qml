import QtQuick

Item {
    id: inscription
    property string label: ""
    property string heading: ""
    property string detail: ""
    property color accent: "#a855f7"
    property color headingColor: "#f2e7d2"
    property color detailColor: "#b9aebe"
    property color backgroundColor: "#5607040c"
    property url glyphSource: Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
    property int headingPixelSize: 24
    property int detailPixelSize: 13
    property int headingLines: 4
    property int detailLines: 5
    property bool centered: false
    implicitHeight: copy.implicitHeight + 42
    height: implicitHeight

    Rectangle {
        anchors.fill: parent
        color: inscription.backgroundColor
    }

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 2
        color: inscription.accent
        opacity: .68
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 1
        color: inscription.accent
        opacity: .32
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: inscription.accent
        opacity: .18
    }

    Image {
        width: 25
        height: 25
        anchors.left: parent.left
        anchors.leftMargin: 13
        anchors.top: parent.top
        anchors.topMargin: 14
        source: inscription.glyphSource
        sourceSize.width: 96
        sourceSize.height: 96
        opacity: .76
    }

    Rectangle {
        width: 9
        height: 9
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.top: parent.top
        anchors.topMargin: 15
        rotation: 45
        color: inscription.accent
        opacity: .52
    }

    Column {
        id: copy
        anchors.left: parent.left
        anchors.leftMargin: 52
        anchors.right: parent.right
        anchors.rightMargin: 24
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8

        Text {
            width: parent.width
            visible: inscription.label.length > 0
            text: inscription.label.toUpperCase()
            color: inscription.accent
            opacity: .9
            font.family: "DejaVu Sans"
            font.pixelSize: 11
            font.bold: true
            font.letterSpacing: 2.4
            horizontalAlignment: inscription.centered ? Text.AlignHCenter : Text.AlignLeft
        }

        Text {
            width: parent.width
            text: inscription.heading
            color: inscription.headingColor
            font.family: "DejaVu Serif"
            font.pixelSize: inscription.headingPixelSize
            font.weight: Font.Medium
            lineHeight: 1.16
            wrapMode: Text.WordWrap
            maximumLineCount: inscription.headingLines
            elide: Text.ElideRight
            horizontalAlignment: inscription.centered ? Text.AlignHCenter : Text.AlignLeft
        }

        Text {
            width: parent.width
            visible: inscription.detail.length > 0
            text: inscription.detail
            color: inscription.detailColor
            font.family: "DejaVu Sans"
            font.pixelSize: inscription.detailPixelSize
            lineHeight: 1.3
            wrapMode: Text.WordWrap
            maximumLineCount: inscription.detailLines
            elide: Text.ElideRight
            horizontalAlignment: inscription.centered ? Text.AlignHCenter : Text.AlignLeft
        }
    }
}
