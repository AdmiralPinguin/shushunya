import QtQuick

Item {
    id: inscription
    property string label: ""
    property string heading: ""
    property string detail: ""
    property color accent: "#745080"
    property color headingColor: "#e4d9c6"
    property color detailColor: "#a89da8"
    property int headingPixelSize: 31
    property int detailPixelSize: 14
    property int headingLines: 4
    property int detailLines: 3
    property bool centered: false
    property bool reverseCut: false
    implicitHeight: textColumn.implicitHeight + 30
    height: implicitHeight

    Rectangle {
        id: longCut
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 1
        color: inscription.accent
        opacity: .52
    }

    Rectangle {
        width: Math.max(42, parent.width * .12)
        height: 3
        anchors.top: parent.top
        anchors.left: inscription.reverseCut ? undefined : parent.left
        anchors.right: inscription.reverseCut ? parent.right : undefined
        color: inscription.accent
        opacity: .92
    }

    Rectangle {
        width: 2
        height: 27
        anchors.top: parent.top
        anchors.left: inscription.reverseCut ? undefined : parent.left
        anchors.right: inscription.reverseCut ? parent.right : undefined
        color: inscription.accent
        opacity: .68
        rotation: inscription.reverseCut ? 18 : -18
        transformOrigin: inscription.reverseCut ? Item.TopRight : Item.TopLeft
    }

    Column {
        id: textColumn
        anchors.left: parent.left
        anchors.leftMargin: inscription.centered ? 16 : 26
        anchors.right: parent.right
        anchors.rightMargin: inscription.centered ? 16 : 26
        anchors.top: parent.top
        anchors.topMargin: 17
        spacing: 7

        Text {
            width: parent.width
            visible: inscription.label.length > 0
            text: inscription.label.toUpperCase()
            color: inscription.accent
            font.family: "DejaVu Sans"
            font.pixelSize: 11
            font.bold: true
            font.letterSpacing: 2.7
            horizontalAlignment: inscription.centered ? Text.AlignHCenter : inscription.reverseCut ? Text.AlignRight : Text.AlignLeft
        }

        Text {
            width: parent.width
            text: inscription.heading
            color: inscription.headingColor
            font.family: "DejaVu Serif"
            font.pixelSize: inscription.headingPixelSize
            font.weight: Font.Medium
            lineHeight: 1.12
            wrapMode: Text.WordWrap
            maximumLineCount: inscription.headingLines
            elide: Text.ElideRight
            horizontalAlignment: inscription.centered ? Text.AlignHCenter : inscription.reverseCut ? Text.AlignRight : Text.AlignLeft
        }

        Text {
            width: parent.width
            visible: inscription.detail.length > 0
            text: inscription.detail
            color: inscription.detailColor
            font.family: "DejaVu Sans"
            font.pixelSize: inscription.detailPixelSize
            lineHeight: 1.28
            wrapMode: Text.WordWrap
            maximumLineCount: inscription.detailLines
            elide: Text.ElideRight
            horizontalAlignment: inscription.centered ? Text.AlignHCenter : inscription.reverseCut ? Text.AlignRight : Text.AlignLeft
        }
    }
}
