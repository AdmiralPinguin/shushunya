import QtQuick

Item {
    id: mark
    property string status: "checking"
    property string label: ""
    property string detail: ""
    property color liveColor: status === "online" ? "#65f2e7" : status === "guarded" ? "#c8a45d" : status === "checking" ? "#9b4dff" : "#c43a50"
    height: 42

    Rectangle {
        id: dot
        width: 8
        height: 8
        radius: 4
        color: mark.liveColor
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        opacity: 0.55 + backend.pulse * 0.45
    }

    Column {
        anchors.left: dot.right
        anchors.leftMargin: 11
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: 1
        Text {
            width: parent.width
            text: mark.label
            elide: Text.ElideRight
            color: "#f2e5cd"
            font.family: "DejaVu Sans"
            font.pixelSize: 13
            font.bold: true
        }
        Text {
            width: parent.width
            text: mark.detail
            elide: Text.ElideRight
            color: mark.liveColor
            font.family: "DejaVu Sans Mono"
            font.pixelSize: 9
            font.letterSpacing: 1
        }
    }
}
