import QtQuick

Item {
    id: seal
    property real intensity: 1.0
    property bool motion: true

    Image {
        id: chaosStar
        anchors.centerIn: parent
        width: parent.width
        height: width
        source: Qt.resolvedUrl("../../assets/heresy/chaos-star.svg")
        sourceSize.width: Math.max(512, Math.round(width * 1.5))
        sourceSize.height: Math.max(512, Math.round(height * 1.5))
        opacity: .58 * seal.intensity
        rotation: -7
        transformOrigin: Item.Center
        asynchronous: true
        cache: true

        RotationAnimator on rotation {
            running: seal.motion
            from: -7
            to: 353
            duration: 270000
            loops: Animation.Infinite
        }
    }

    Image {
        id: horusEye
        anchors.centerIn: parent
        width: parent.width * .46
        height: width
        source: Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
        sourceSize.width: Math.max(384, Math.round(width * 1.8))
        sourceSize.height: Math.max(384, Math.round(height * 1.8))
        opacity: (.88 + backend.pulse * .08) * seal.intensity
        scale: .995 + backend.pulse * .012
        asynchronous: true
        cache: true
    }
}
