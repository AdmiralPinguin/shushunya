import QtQuick
import QtQuick.Layouts

Item {
    id: header
    property string section: "ОКО"
    property string screenLabel: ""
    height: 76

    Rectangle {
        anchors.fill: parent
        color: "#df08050d"
        border.color: "#5bc8a45d"
        border.width: 1
    }
    Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 2; gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: "#009b4dff" } GradientStop { position: .5; color: "#c8a45d" } GradientStop { position: 1; color: "#0065f2e7" } } }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 20
        anchors.rightMargin: 20
        spacing: 16

        Image { source: Qt.resolvedUrl("../../assets/sigils/eye.svg"); Layout.preferredWidth: 46; Layout.preferredHeight: 46; fillMode: Image.PreserveAspectFit }
        ColumnLayout {
            spacing: 0
            Text { text: "SHUSHUNYA"; color: "#f2e5cd"; font.family: "DejaVu Serif"; font.pixelSize: 22; font.bold: true; font.letterSpacing: 2 }
            Text { text: header.section.toUpperCase() + "  //  " + header.screenLabel; color: "#9a8ea2"; font.family: "DejaVu Sans Mono"; font.pixelSize: 9; font.letterSpacing: 1 }
        }
        Item { Layout.fillWidth: true }
        RowLayout {
            spacing: 9
            Rectangle { Layout.preferredWidth: 7; Layout.preferredHeight: 7; radius: 4; color: backend.systemState === "СТАБИЛЬНА" ? "#65f2e7" : "#c43a50"; opacity: .55 + backend.pulse * .45 }
            Text { text: "ВАРП-КАНАЛ  " + backend.systemState; color: "#c8a45d"; font.family: "DejaVu Sans Mono"; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1 }
        }
        Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 38; color: "#4bc8a45d" }
        ColumnLayout {
            spacing: 0
            Text { Layout.alignment: Qt.AlignRight; text: backend.clockText; color: "#f2e5cd"; font.family: "DejaVu Sans Mono"; font.pixelSize: 22; font.bold: true }
            Text { Layout.alignment: Qt.AlignRight; text: backend.dateText; color: "#9a8ea2"; font.family: "DejaVu Sans Mono"; font.pixelSize: 9; font.letterSpacing: 1.3 }
        }
    }
}
