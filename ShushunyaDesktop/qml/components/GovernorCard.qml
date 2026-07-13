import QtQuick

Item {
    id: card
    property string serviceKey: ""
    property string governorName: ""
    property string domain: ""
    property string designation: ""
    property string serviceStatus: backend.serviceState(serviceKey)
    property color accent: serviceStatus === "online" ? "#65f2e7" : serviceStatus === "guarded" ? "#c8a45d" : "#c43a50"

    height: 126

    Rectangle {
        anchors.fill: parent
        color: "#dc100b16"
        border.color: Qt.rgba(card.accent.r, card.accent.g, card.accent.b, 0.55)
        border.width: 1
        radius: 8
    }
    Rectangle { width: 5; anchors.top: parent.top; anchors.bottom: parent.bottom; color: card.accent; opacity: .75 }
    Image {
        source: Qt.resolvedUrl("../../assets/sigils/warbands.svg")
        width: 70
        height: 70
        anchors.left: parent.left
        anchors.leftMargin: 23
        anchors.verticalCenter: parent.verticalCenter
        fillMode: Image.PreserveAspectFit
        opacity: .8
    }
    Column {
        anchors.left: parent.left
        anchors.leftMargin: 112
        anchors.right: parent.right
        anchors.rightMargin: 22
        anchors.verticalCenter: parent.verticalCenter
        spacing: 5
        Text { text: card.designation.toUpperCase(); color: "#9a8ea2"; font.family: "DejaVu Sans Mono"; font.pixelSize: 9; font.letterSpacing: 1.8 }
        Text { text: card.governorName.toUpperCase(); color: "#f2e5cd"; font.family: "DejaVu Serif"; font.pixelSize: 22; font.bold: true; font.letterSpacing: .7 }
        Text { text: card.domain; color: "#c8a45d"; font.family: "DejaVu Sans"; font.pixelSize: 12 }
        Row {
            spacing: 8
            Rectangle { width: 7; height: 7; radius: 4; color: card.accent; anchors.verticalCenter: parent.verticalCenter; opacity: .55 + backend.pulse * .45 }
            Text { text: card.serviceStatus === "online" ? "НА СВЯЗИ" : card.serviceStatus === "guarded" ? "ПОД ПЕЧАТЬЮ" : "МОЛЧИТ"; color: card.accent; font.family: "DejaVu Sans Mono"; font.pixelSize: 9; font.bold: true; font.letterSpacing: 1 }
        }
    }
    Connections {
        target: backend
        function onHealthChanged() { card.serviceStatus = backend.serviceState(card.serviceKey) }
    }
}
