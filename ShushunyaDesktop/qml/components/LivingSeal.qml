import QtQuick

Item {
    id: seal
    property color stateColor: "#745080"
    property string visualState: backend.visualState
    property real intensity: 1.0
    property bool motion: true

    readonly property bool closed: visualState === "sleep" || visualState === "sealing"
    readonly property bool wounded: visualState === "wounded"
    readonly property bool quiet: visualState === "sleep" || visualState === "waiting" || visualState === "sealing"
    readonly property real stateScale: visualState === "sleep" ? .82
                                       : visualState === "attention" ? 1.06
                                       : visualState === "thinking" ? .96
                                       : visualState === "forging" ? 1.04
                                       : visualState === "waiting" ? .94
                                       : visualState === "speaking" ? 1.03
                                       : visualState === "triumph" ? 1.09
                                       : visualState === "wounded" ? .91
                                       : visualState === "sealing" ? .70 : 1.0
    readonly property real stateRotation: visualState === "sleep" ? -7
                                          : visualState === "thinking" ? -2
                                          : visualState === "forging" ? 5
                                          : visualState === "waiting" ? -4
                                          : visualState === "wounded" ? 11
                                          : visualState === "sealing" ? -12 : 0
    readonly property real stateOpacity: visualState === "sleep" ? .42
                                         : visualState === "sealing" ? .38
                                         : visualState === "wounded" ? .76 : 1.0
    readonly property real outerOpacity: visualState === "sleep" ? .24
                                         : visualState === "attention" ? .58
                                         : visualState === "thinking" ? .50
                                         : visualState === "forging" ? .63
                                         : visualState === "waiting" ? .44
                                         : visualState === "speaking" ? .57
                                         : visualState === "triumph" ? .71
                                         : visualState === "wounded" ? .47
                                         : visualState === "sealing" ? .19 : .52
    readonly property real haloOpacity: visualState === "sleep" ? .07
                                        : visualState === "attention" ? .32
                                        : visualState === "thinking" ? .24
                                        : visualState === "forging" ? .44
                                        : visualState === "waiting" ? .15
                                        : visualState === "speaking" ? .38
                                        : visualState === "triumph" ? .58
                                        : visualState === "wounded" ? .10
                                        : visualState === "sealing" ? .05 : .23

    Item {
        id: body
        anchors.fill: parent
        transformOrigin: Item.Center
        scale: seal.stateScale
        rotation: seal.stateRotation
        opacity: seal.stateOpacity

        Behavior on scale { NumberAnimation { duration: 720; easing.type: Easing.OutCubic } }
        Behavior on rotation { NumberAnimation { duration: seal.wounded ? 130 : 680; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: seal.visualState === "sealing" ? 1500 : 620 } }

        Image {
            anchors.centerIn: parent
            anchors.horizontalCenterOffset: seal.wounded ? 9 : 0
            anchors.verticalCenterOffset: seal.wounded ? -6 : 0
            width: parent.width * .98
            height: width
            source: Qt.resolvedUrl("../../assets/heresy/fractured-chaos-seal.svg")
            sourceSize.width: Math.max(512, Math.round(width * 1.5))
            sourceSize.height: Math.max(512, Math.round(height * 1.5))
            opacity: (seal.wounded ? .24 : .075) * seal.intensity
            scale: 1.075 + backend.pulse * .008
            asynchronous: true
            cache: true

            Behavior on anchors.horizontalCenterOffset { NumberAnimation { duration: 160 } }
            Behavior on anchors.verticalCenterOffset { NumberAnimation { duration: 160 } }
        }

        Image {
            id: outerStar
            anchors.centerIn: parent
            width: parent.width * .95
            height: width
            source: Qt.resolvedUrl("../../assets/heresy/fractured-chaos-seal.svg")
            sourceSize.width: Math.max(512, Math.round(width * 1.5))
            sourceSize.height: Math.max(512, Math.round(height * 1.5))
            opacity: seal.outerOpacity * seal.intensity
            rotation: -2.2
            transformOrigin: Item.Center
            asynchronous: true
            cache: true

            Behavior on opacity { NumberAnimation { duration: 760 } }
            SequentialAnimation on rotation {
                running: seal.motion && !seal.quiet
                loops: Animation.Infinite
                NumberAnimation {
                    from: -2.2
                    to: seal.visualState === "forging" ? 5.8 : 2.4
                    duration: seal.visualState === "forging" ? 6200 : 17000
                    easing.type: Easing.InOutSine
                }
                NumberAnimation {
                    from: seal.visualState === "forging" ? 5.8 : 2.4
                    to: -2.2
                    duration: seal.visualState === "forging" ? 6800 : 19000
                    easing.type: Easing.InOutSine
                }
            }
        }

        Image {
            id: brokenHalo
            anchors.centerIn: parent
            width: parent.width * (seal.visualState === "triumph" ? .78 : .69)
            height: width
            source: Qt.resolvedUrl("../../assets/heresy/broken-halo-rune.svg")
            sourceSize.width: Math.max(384, Math.round(width * 1.6))
            sourceSize.height: Math.max(384, Math.round(height * 1.6))
            opacity: seal.haloOpacity * seal.intensity
            rotation: 18
            transformOrigin: Item.Center
            asynchronous: true
            cache: true

            Behavior on width { NumberAnimation { duration: 850; easing.type: Easing.OutBack } }
            Behavior on opacity { NumberAnimation { duration: seal.visualState === "triumph" ? 1200 : 720 } }
            SequentialAnimation on rotation {
                running: seal.motion && !seal.quiet
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
            id: stateRing
            anchors.centerIn: parent
            width: parent.width * .375
            height: width
            radius: width / 2
            color: "transparent"
            border.width: Math.max(1, parent.width * (seal.visualState === "speaking" ? .004 : .0022))
            border.color: seal.stateColor
            opacity: (seal.visualState === "sleep" || seal.visualState === "sealing") ? .035 * seal.intensity
                     : (seal.visualState === "speaking" ? .16 + backend.pulse * .28
                     : seal.visualState === "triumph" ? .22 + backend.pulse * .15
                     : .08 + backend.pulse * .09) * seal.intensity
            scale: seal.visualState === "speaking" ? .97 + backend.pulse * .055
                   : .98 + backend.pulse * .025

            Behavior on border.color { ColorAnimation { duration: 720 } }
        }

        Image {
            id: openEye
            anchors.centerIn: parent
            width: parent.width * .335
            height: width
            source: Qt.resolvedUrl("../../assets/heresy/horus-eye.svg")
            sourceSize.width: Math.max(384, Math.round(width * 1.8))
            sourceSize.height: Math.max(384, Math.round(height * 1.8))
            opacity: (seal.closed ? 0 : .88 + backend.pulse * .07) * seal.intensity
            scale: .992 + backend.pulse * .014
            asynchronous: true
            cache: true

            Behavior on opacity { NumberAnimation { duration: 170 } }
        }

        Image {
            id: closedEye
            anchors.centerIn: parent
            width: parent.width * .335
            height: width
            source: Qt.resolvedUrl("../../assets/heresy/horus-eye-closed.svg")
            sourceSize.width: Math.max(384, Math.round(width * 1.8))
            sourceSize.height: Math.max(384, Math.round(height * 1.8))
            opacity: (seal.closed ? .62 : 0) * seal.intensity
            scale: .94
            asynchronous: true
            cache: true

            Behavior on opacity { NumberAnimation { duration: 170 } }
        }

        Image {
            anchors.centerIn: parent
            width: parent.width * .72
            height: width
            source: Qt.resolvedUrl("../../assets/heresy/wound-fracture.svg")
            sourceSize.width: Math.max(384, Math.round(width * 1.4))
            sourceSize.height: Math.max(384, Math.round(height * 1.4))
            visible: seal.wounded
            opacity: .76 * seal.intensity
            rotation: -18
            asynchronous: true
            cache: true
        }
    }
}
