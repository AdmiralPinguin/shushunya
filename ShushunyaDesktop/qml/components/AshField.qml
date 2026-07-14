import QtQuick

Item {
    id: ash
    property string variant: "presence"
    property int ordinal: 0
    property bool motion: true
    property real intensity: 1.0
    readonly property int particleCount: variant === "mind" ? 12 : variant === "canvas" ? 16 : 20
    clip: true

    Repeater {
        model: ash.particleCount
        delegate: Rectangle {
            id: ember
            required property int index
            readonly property real seedX: ((index * 197 + ash.ordinal * 71) % 997) / 997
            readonly property real startY: ash.height * (1.03 + ((index * 31) % 19) / 70)
            readonly property real endY: -ash.height * (.04 + ((index * 17) % 23) / 80)
            width: index % 5 === 0 ? 3 : 2
            height: index % 4 === 0 ? 8 : 4
            radius: width / 2
            x: ash.width * seedX
            y: startY
            color: index % 7 === 0 ? "#8f3150" : index % 3 === 0 ? "#7b4b88" : "#b29c7c"
            opacity: (.08 + (index % 4) * .025) * ash.intensity
            rotation: 17 + (index * 29) % 130

            SequentialAnimation on y {
                running: ash.motion
                loops: Animation.Infinite
                PauseAnimation { duration: 300 + (ember.index % 9) * 410 }
                NumberAnimation {
                    from: ember.startY
                    to: ember.endY
                    duration: 15000 + (ember.index % 8) * 2600
                    easing.type: Easing.Linear
                }
            }
        }
    }
}
