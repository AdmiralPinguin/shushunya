import QtQuick

Item {
    id: card
    default property alias content: contentHost.data
    property string label: ""
    property color accent: "#65f2e7"
    property color fillColor: "#ba0c0912"

    Rectangle {
        anchors.fill: parent
        radius: 24
        color: card.fillColor
        border.color: Qt.rgba(card.accent.r, card.accent.g, card.accent.b, .26)
        border.width: 1
    }

    Text {
        id: labelText
        visible: card.label.length > 0
        anchors.left: parent.left
        anchors.leftMargin: 22
        anchors.top: parent.top
        anchors.topMargin: 18
        text: card.label.toUpperCase()
        color: card.accent
        opacity: .82
        font.family: "DejaVu Sans"
        font.pixelSize: 12
        font.bold: true
        font.letterSpacing: 2
    }

    Item {
        id: contentHost
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: card.label.length > 0 ? labelText.bottom : parent.top
        anchors.bottom: parent.bottom
        anchors.leftMargin: 22
        anchors.rightMargin: 22
        anchors.topMargin: card.label.length > 0 ? 13 : 20
        anchors.bottomMargin: 20
    }
}
