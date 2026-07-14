import QtQuick

Item {
    id: ash
    property string variant: "presence"
    property string visualState: backend.visualState
    property int ordinal: 0
    property bool motion: true
    property real intensity: 1.0
    readonly property bool falling: visualState === "wounded"
    readonly property real energy: visualState === "sleep" ? .08
                                   : visualState === "attention" ? .35
                                   : visualState === "thinking" ? .28
                                   : visualState === "forging" ? 1.55
                                   : visualState === "waiting" ? .08
                                   : visualState === "speaking" ? .30
                                   : visualState === "triumph" ? 1.15
                                   : visualState === "wounded" ? .38
                                   : visualState === "sealing" ? .03 : .45
    readonly property int particleCount: Math.max(4, Math.round(
        (variant === "mind" ? 13 : variant === "canvas" ? 18 : 22)
        * Math.max(.34, energy)
    ))
    clip: true

    Repeater {
        model: ash.particleCount
        delegate: Rectangle {
            id: ember
            required property int index
            property real progress: ((index * 61 + ash.ordinal * 13) % 97) / 97
            readonly property real seedX: ((index * 197 + ash.ordinal * 71) % 997) / 997
            readonly property real drift: (((index * 43) % 101) / 101 - .5) * ash.width * .08
            readonly property real lowY: ash.height * (1.03 + ((index * 31) % 19) / 70)
            readonly property real highY: -ash.height * (.04 + ((index * 17) % 23) / 80)
            width: index % 5 === 0 ? 3 : 2
            height: index % 4 === 0 ? (ash.visualState === "forging" ? 13 : 8) : 4
            radius: width / 2
            x: ash.width * seedX + drift * progress
            y: ash.falling
               ? highY + (lowY - highY) * progress
               : lowY + (highY - lowY) * progress
            color: ash.visualState === "forging" ? (index % 3 === 0 ? "#f2b15d" : "#b84c2b")
                 : ash.visualState === "triumph" ? (index % 3 === 0 ? "#e3cf9e" : "#b88a43")
                 : ash.visualState === "wounded" ? (index % 2 === 0 ? "#c32843" : "#65203a")
                 : index % 7 === 0 ? "#8f3150"
                 : index % 3 === 0 ? "#7b4b88" : "#b29c7c"
            opacity: (.075 + (index % 4) * .03) * ash.intensity * ash.energy
            rotation: 17 + (index * 29) % 130

            Behavior on color { ColorAnimation { duration: 720 } }
            Behavior on opacity { NumberAnimation { duration: 720 } }
            SequentialAnimation on progress {
                running: ash.motion
                loops: Animation.Infinite
                PauseAnimation { duration: 200 + (ember.index % 9) * 260 }
                NumberAnimation {
                    from: 0
                    to: 1
                    duration: ash.visualState === "forging" ? 4700 + (ember.index % 7) * 420
                            : ash.visualState === "triumph" ? 6800 + (ember.index % 7) * 580
                            : 14500 + (ember.index % 8) * 2200
                    easing.type: Easing.Linear
                }
            }
        }
    }
}
