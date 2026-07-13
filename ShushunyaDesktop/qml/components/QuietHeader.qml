import QtQuick

Item {
    id: header
    property string title: backend.companion.name
    property string subtitle: ""
    property color accent: "#65f2e7"
    height: 72

    Row {
        anchors.left: parent.left
        anchors.leftMargin: 8
        anchors.top: parent.top
        anchors.topMargin: 7
        spacing: 13

        Rectangle {
            width: 8
            height: 8
            radius: 4
            anchors.verticalCenter: parent.verticalCenter
            color: header.accent
            opacity: .42 + backend.pulse * .36
        }

        Column {
            spacing: 3
            Text {
                text: header.title
                color: "#f2e5cd"
                font.family: "DejaVu Serif"
                font.pixelSize: 20
                font.bold: true
                font.letterSpacing: 1.2
            }
            Text {
                visible: header.subtitle.length > 0
                text: header.subtitle
                color: "#aa9bac"
                font.family: "DejaVu Sans"
                font.pixelSize: 12
            }
        }
    }
}
