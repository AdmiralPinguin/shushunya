import QtQuick

Item {
    id: environment
    property string variant: "presence"
    property int ordinal: 0
    property bool motion: true
    property real intensity: 1.0
    property real screenX: 0
    property real screenY: 0
    property real virtualX: 0
    property real virtualY: 0
    property real virtualWidth: width
    property real virtualHeight: height
    readonly property color nerveColor: backend.companion.presence === "waiting" ? "#88152d"
                                         : backend.companion.presence === "speaking" ? "#3b7f89"
                                         : "#5e2b70"
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
        opacity: (environment.variant === "ambient" ? .64 : environment.variant === "presence" ? .58 : .48) * environment.intensity
        scale: 1.012 + backend.pulse * .004
        asynchronous: true
        cache: true
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

    WarpVeins {
        anchors.fill: parent
        screenX: environment.screenX
        screenY: environment.screenY
        virtualX: environment.virtualX
        virtualY: environment.virtualY
        virtualWidth: environment.virtualWidth
        virtualHeight: environment.virtualHeight
        nerveColor: environment.nerveColor
        intensity: environment.intensity
    }

    SparseRunes {
        anchors.fill: parent
        variant: environment.variant
        ordinal: environment.ordinal
        intensity: environment.variant === "mind" ? .72 : .9
    }

    AshField {
        anchors.fill: parent
        variant: environment.variant
        ordinal: environment.ordinal
        motion: environment.motion
        intensity: environment.variant === "mind" ? .65 : .9
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
