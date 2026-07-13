import QtQuick

Item {
    id: control
    property url iconSource
    property string label: ""
    property bool selected: false
    property color accent: selected ? "#65f2e7" : "#c8a45d"
    signal clicked()

    width: 86
    height: 84

    Rectangle {
        anchors.fill: parent
        color: control.selected ? "#492b1638" : mouse.containsMouse ? "#361b1326" : "#b5100b16"
        border.color: Qt.rgba(control.accent.r, control.accent.g, control.accent.b, control.selected ? 0.9 : 0.42)
        border.width: control.selected ? 2 : 1
        radius: 8
        scale: mouse.pressed ? 0.97 : 1.0
        Behavior on scale { NumberAnimation { duration: 90 } }
    }

    Image {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 10
        width: 40
        height: 40
        source: control.iconSource
        fillMode: Image.PreserveAspectFit
        opacity: control.selected ? 1.0 : 0.74
    }

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 9
        text: control.label.toUpperCase()
        color: control.selected ? "#f2e5cd" : "#aa9aa9"
        font.family: "DejaVu Sans Mono"
        font.pixelSize: 9
        font.bold: control.selected
        font.letterSpacing: 1.1
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: control.clicked()
    }
}
