import QtQuick

Item {
    id: field
    property string variant: "presence"
    property int ordinal: 0
    property int density: variant === "ambient" ? 52 : variant === "mind" ? 36 : variant === "canvas" ? 44 : 42
    property real intensity: 1.0
    property bool motion: true
    clip: true

    Rectangle {
        anchors.fill: parent
        color: "#030107"
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: field.variant === "canvas" ? "#d005020a" : "#a005020a" }
            GradientStop { position: .46; color: "#4b130525" }
            GradientStop { position: 1; color: field.variant === "mind" ? "#b0030107" : "#87020106" }
        }
    }

    Rectangle {
        width: parent.width * .54
        height: parent.height * 1.25
        x: field.variant === "canvas" ? parent.width * .46 : parent.width * .23
        y: -parent.height * .12
        rotation: field.variant === "mind" ? -9 : 7
        opacity: .42 * field.intensity
        gradient: Gradient {
            GradientStop { position: 0; color: "#0018072a" }
            GradientStop { position: .5; color: "#7a32105d" }
            GradientStop { position: 1; color: "#0018072a" }
        }
    }

    Repeater {
        model: 7
        delegate: Rectangle {
            required property int index
            x: ((index * 173 + field.ordinal * 97) % 1000) / 1000 * field.width
            y: -field.height * .16
            width: index % 3 === 0 ? 2 : 1
            height: field.height * 1.35
            rotation: -24 + ((index * 19 + field.ordinal * 11) % 48)
            color: index % 3 === 0 ? "#6b2ba2" : index % 2 === 0 ? "#4debff" : "#8c1028"
            opacity: (.035 + (index % 4) * .012) * field.intensity
        }
    }

    Repeater {
        model: field.density
        delegate: Image {
            id: glyph
            required property int index
            readonly property real shortEdge: Math.max(1, Math.min(field.width, field.height))
            readonly property real glyphSize: shortEdge * (.038 + ((index * 13 + 5) % 9) * .009)
            readonly property real baseRotation: (index * 47 + field.ordinal * 29) % 360
            width: glyphSize
            height: glyphSize
            x: ((index * 277 + field.ordinal * 83) % 1000) / 1000 * (field.width + width * .7) - width * .35
            y: ((index * 419 + field.ordinal * 137) % 1000) / 1000 * (field.height + height * .7) - height * .35
            source: index % 11 === 0 ? Qt.resolvedUrl("../../assets/heresy/horned-skull.svg")
                  : index % 4 === 0 ? Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
                  : index % 3 === 0 ? Qt.resolvedUrl("../../assets/heresy/chaos-star.svg")
                  : Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
            sourceSize.width: Math.max(72, Math.round(width * 1.8))
            sourceSize.height: Math.max(72, Math.round(height * 1.8))
            opacity: (.032 + ((index * 17) % 7) * .014) * field.intensity
            rotation: baseRotation
            transformOrigin: Item.Center
            asynchronous: true
            cache: true

            RotationAnimator on rotation {
                running: field.motion && glyph.index % 5 === 0
                from: glyph.baseRotation
                to: glyph.baseRotation + (glyph.index % 2 === 0 ? 360 : -360)
                duration: 150000 + (glyph.index % 9) * 17000
                loops: Animation.Infinite
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: parent.height * .18
        gradient: Gradient {
            GradientStop { position: 0; color: "#c6030107" }
            GradientStop { position: 1; color: "#00030107" }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: parent.height * .31
        gradient: Gradient {
            GradientStop { position: 0; color: "#00030107" }
            GradientStop { position: 1; color: "#e8030107" }
        }
    }
}
