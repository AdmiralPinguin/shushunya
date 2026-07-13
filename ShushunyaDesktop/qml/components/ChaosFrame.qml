import QtQuick

Item {
    id: frame
    default property alias content: contentHost.data
    property string title: ""
    property string kicker: ""
    property color accent: "#c8a45d"
    property color fillColor: "#c9120d18"
    property real cornerSize: 18

    Rectangle {
        anchors.fill: parent
        color: frame.fillColor
        border.color: Qt.rgba(frame.accent.r, frame.accent.g, frame.accent.b, 0.52)
        border.width: 1
        radius: 8
    }

    Rectangle { width: frame.cornerSize; height: 2; color: frame.accent; anchors.left: parent.left; anchors.top: parent.top }
    Rectangle { width: 2; height: frame.cornerSize; color: frame.accent; anchors.left: parent.left; anchors.top: parent.top }
    Rectangle { width: frame.cornerSize; height: 2; color: frame.accent; anchors.right: parent.right; anchors.top: parent.top }
    Rectangle { width: 2; height: frame.cornerSize; color: frame.accent; anchors.right: parent.right; anchors.top: parent.top }
    Rectangle { width: frame.cornerSize; height: 2; color: frame.accent; anchors.left: parent.left; anchors.bottom: parent.bottom }
    Rectangle { width: 2; height: frame.cornerSize; color: frame.accent; anchors.left: parent.left; anchors.bottom: parent.bottom }
    Rectangle { width: frame.cornerSize; height: 2; color: frame.accent; anchors.right: parent.right; anchors.bottom: parent.bottom }
    Rectangle { width: 2; height: frame.cornerSize; color: frame.accent; anchors.right: parent.right; anchors.bottom: parent.bottom }

    Column {
        anchors.left: parent.left
        anchors.leftMargin: 18
        anchors.top: parent.top
        anchors.topMargin: 14
        spacing: 2
        visible: frame.title.length > 0 || frame.kicker.length > 0

        Text {
            text: frame.kicker.toUpperCase()
            color: "#9a8ea2"
            font.family: "DejaVu Sans Mono"
            font.pixelSize: 10
            font.letterSpacing: 2
        }
        Text {
            text: frame.title.toUpperCase()
            color: "#f2e5cd"
            font.family: "DejaVu Serif"
            font.bold: true
            font.pixelSize: 16
            font.letterSpacing: 1
        }
    }

    Item {
        id: contentHost
        anchors.fill: parent
        anchors.leftMargin: 18
        anchors.rightMargin: 18
        anchors.topMargin: frame.title.length > 0 || frame.kicker.length > 0 ? 62 : 18
        anchors.bottomMargin: 18
    }
}
