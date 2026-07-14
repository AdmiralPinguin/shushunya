import QtQuick

Item {
    id: environment
    property string variant: "presence"
    property string visualState: backend.visualState
    property int ordinal: 0
    property bool motion: true
    property real intensity: 1.0
    property real screenX: 0
    property real screenY: 0
    property real virtualX: 0
    property real virtualY: 0
    property real virtualWidth: width
    property real virtualHeight: height

    readonly property bool quiet: visualState === "sleep" || visualState === "waiting" || visualState === "sealing"
    readonly property color nerveColor: visualState === "sleep" ? "#34283d"
                                         : visualState === "attention" ? "#58a2a8"
                                         : visualState === "thinking" ? "#75508a"
                                         : visualState === "forging" ? "#b54a25"
                                         : visualState === "waiting" ? "#971c36"
                                         : visualState === "speaking" ? "#3b8f99"
                                         : visualState === "triumph" ? "#c39a55"
                                         : visualState === "wounded" ? "#b01830"
                                         : visualState === "sealing" ? "#55465b" : "#5e2b70"
    readonly property real panoramaLevel: visualState === "sleep" ? .20
                                          : visualState === "attention" ? .62
                                          : visualState === "thinking" ? .48
                                          : visualState === "forging" ? .75
                                          : visualState === "waiting" ? .39
                                          : visualState === "speaking" ? .58
                                          : visualState === "triumph" ? .82
                                          : visualState === "wounded" ? .57
                                          : visualState === "sealing" ? .17 : .52
    readonly property real veinLevel: visualState === "sleep" ? .10
                                      : visualState === "attention" ? .62
                                      : visualState === "thinking" ? .95
                                      : visualState === "forging" ? .82
                                      : visualState === "waiting" ? 1.05
                                      : visualState === "speaking" ? .76
                                      : visualState === "triumph" ? .88
                                      : visualState === "wounded" ? 1.25
                                      : visualState === "sealing" ? .18 : .55
    readonly property real runeLevel: visualState === "sleep" ? .16
                                      : visualState === "attention" ? .55
                                      : visualState === "thinking" ? .58
                                      : visualState === "forging" ? .82
                                      : visualState === "waiting" ? .27
                                      : visualState === "speaking" ? .42
                                      : visualState === "triumph" ? .95
                                      : visualState === "wounded" ? .42
                                      : visualState === "sealing" ? .10 : .58
    clip: true

    Rectangle {
        anchors.fill: parent
        color: "#020104"
    }

    Image {
        id: sanctum
        anchors.fill: parent
        source: Qt.resolvedUrl("../../assets/chaos-sanctum-panorama.png")
        sourceSize.width: 2172
        sourceSize.height: 724
        fillMode: Image.PreserveAspectCrop
        horizontalAlignment: environment.variant === "canvas" ? Image.AlignRight
                             : environment.variant === "mind" ? Image.AlignLeft
                             : environment.variant === "ambient" && environment.ordinal % 2 ? Image.AlignRight
                             : Image.AlignHCenter
        verticalAlignment: Image.AlignVCenter
        opacity: environment.panoramaLevel * environment.intensity
        scale: 1.012 + backend.pulse * (environment.quiet ? .001 : .004)
        asynchronous: true
        cache: true

        Behavior on opacity { NumberAnimation { duration: environment.visualState === "sealing" ? 1500 : 820 } }
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop {
                position: 0
                color: environment.variant === "canvas" ? "#e6030107"
                     : environment.variant === "mind" ? "#94030107" : "#72030107"
            }
            GradientStop { position: .48; color: environment.variant === "presence" ? "#4a16051d" : "#72100618" }
            GradientStop {
                position: 1
                color: environment.variant === "canvas" ? "#42030107"
                     : environment.variant === "mind" ? "#c0030107" : "#70030107"
            }
        }
    }

    Rectangle {
        id: stateWash
        anchors.fill: parent
        color: environment.visualState === "forging" ? "#32a22f14"
             : environment.visualState === "triumph" ? "#22d09b4f"
             : environment.visualState === "wounded" ? "#3eb01830"
             : environment.visualState === "waiting" ? "#20971c36"
             : environment.visualState === "speaking" ? "#123b8f99"
             : environment.visualState === "sealing" ? "#72000000"
             : environment.visualState === "sleep" ? "#50000000" : "#00000000"

        Behavior on color { ColorAnimation { duration: environment.visualState === "waiting" ? 210 : 800 } }
    }

    WarpVeins {
        anchors.fill: parent
        screenX: environment.screenX
        screenY: environment.screenY
        virtualX: environment.virtualX
        virtualY: environment.virtualY
        virtualWidth: environment.virtualWidth
        virtualHeight: environment.virtualHeight
        nerveColor: environment.nerveColor
        intensity: environment.veinLevel * environment.intensity
    }

    SparseRunes {
        anchors.fill: parent
        variant: environment.variant
        ordinal: environment.ordinal
        intensity: environment.runeLevel * environment.intensity
    }

    AshField {
        anchors.fill: parent
        variant: environment.variant
        visualState: environment.visualState
        ordinal: environment.ordinal
        motion: environment.motion && !environment.quiet
        intensity: environment.variant === "mind" ? .72 : .95
    }

    Rectangle {
        visible: environment.visualState === "attention"
        width: parent.width * .55
        height: Math.max(1, Math.min(parent.width, parent.height) * .002)
        x: -width * .14
        y: parent.height * .29
        rotation: 21
        color: "#58a2a8"
        opacity: .16 + backend.pulse * .05
    }

    Rectangle {
        visible: environment.visualState === "attention"
        width: parent.width * .55
        height: Math.max(1, Math.min(parent.width, parent.height) * .002)
        anchors.right: parent.right
        anchors.rightMargin: -width * .14
        y: parent.height * .29
        rotation: -21
        color: "#a18a73"
        opacity: .13 + backend.pulse * .04
    }

    Image {
        anchors.centerIn: parent
        width: Math.min(parent.width, parent.height) * (environment.visualState === "wounded" ? 1.42 : 1.12)
        height: width
        source: Qt.resolvedUrl("../../assets/heresy/wound-fracture.svg")
        sourceSize.width: Math.max(512, Math.round(width * 1.2))
        sourceSize.height: Math.max(512, Math.round(height * 1.2))
        visible: environment.visualState === "waiting" || environment.visualState === "wounded"
        opacity: environment.visualState === "wounded" ? .70 : .28
        rotation: environment.ordinal % 2 === 0 ? -31 : 26
        asynchronous: true
        cache: true

        Behavior on opacity { NumberAnimation { duration: 150 } }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: parent.height * .23
        gradient: Gradient {
            GradientStop { position: 0; color: "#d9020104" }
            GradientStop { position: 1; color: "#00020104" }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: parent.height * .31
        gradient: Gradient {
            GradientStop { position: 0; color: "#00020104" }
            GradientStop { position: 1; color: "#ed020104" }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: parent.width * .10
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#d6020104" }
            GradientStop { position: 1; color: "#00020104" }
        }
    }

    Rectangle {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: parent.width * .10
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#00020104" }
            GradientStop { position: 1; color: "#d6020104" }
        }
    }
}
