import QtQuick

Item {
    id: seal
    property color stateColor: "#745080"
    property real intensity: 1.0
    property bool motion: true

    Image {
        anchors.centerIn: parent
        width: parent.width * .98
        height: width
        source: Qt.resolvedUrl("../../assets/heresy/fractured-chaos-seal.svg")
        sourceSize.width: Math.max(512, Math.round(width * 1.5))
        sourceSize.height: Math.max(512, Math.round(height * 1.5))
        opacity: .075 * seal.intensity
        scale: 1.075 + backend.pulse * .008
        asynchronous: true
        cache: true
    }

    Image {
        id: outerStar
        anchors.centerIn: parent
        width: parent.width * .95
        height: width
        source: Qt.resolvedUrl("../../assets/heresy/fractured-chaos-seal.svg")
        sourceSize.width: Math.max(512, Math.round(width * 1.5))
        sourceSize.height: Math.max(512, Math.round(height * 1.5))
        opacity: .52 * seal.intensity
        rotation: -2.2
        transformOrigin: Item.Center
        asynchronous: true
        cache: true

        SequentialAnimation on rotation {
            running: seal.motion
            loops: Animation.Infinite
            NumberAnimation { from: -2.2; to: 2.4; duration: 17000; easing.type: Easing.InOutSine }
            NumberAnimation { from: 2.4; to: -2.2; duration: 19000; easing.type: Easing.InOutSine }
        }
    }

    Image {
        id: brokenHalo
        anchors.centerIn: parent
        width: parent.width * .69
        height: width
        source: Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
        sourceSize.width: Math.max(384, Math.round(width * 1.6))
        sourceSize.height: Math.max(384, Math.round(height * 1.6))
        opacity: .23 * seal.intensity
        rotation: 18
        transformOrigin: Item.Center
        asynchronous: true
        cache: true

        SequentialAnimation on rotation {
            running: seal.motion
            loops: Animation.Infinite
            NumberAnimation { from: 18; to: 13; duration: 23000; easing.type: Easing.InOutSine }
            NumberAnimation { from: 13; to: 18; duration: 21000; easing.type: Easing.InOutSine }
        }
    }

    Rectangle {
        anchors.centerIn: parent
        width: parent.width * .365
        height: width
        radius: width / 2
        color: "#09030b"
        opacity: .96 * seal.intensity
        border.width: Math.max(1, parent.width * .0015)
        border.color: "#3e2148"
    }

    Rectangle {
        anchors.centerIn: parent
        width: parent.width * .375
        height: width
        radius: width / 2
        color: "transparent"
        border.width: Math.max(1, parent.width * .0022)
        border.color: seal.stateColor
        opacity: (.08 + backend.pulse * .09) * seal.intensity
        scale: .98 + backend.pulse * .025
    }

    Image {
        id: eye
        anchors.centerIn: parent
        width: parent.width * .335
        height: width
        source: Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
        sourceSize.width: Math.max(384, Math.round(width * 1.8))
        sourceSize.height: Math.max(384, Math.round(height * 1.8))
        opacity: (.88 + backend.pulse * .07) * seal.intensity
        scale: .992 + backend.pulse * .014
        asynchronous: true
        cache: true
    }
}
