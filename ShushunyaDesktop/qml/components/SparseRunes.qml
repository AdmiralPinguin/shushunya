import QtQuick

Item {
    id: field
    property string variant: "presence"
    property int ordinal: 0
    property real intensity: 1.0
    readonly property real shortEdge: Math.max(1, Math.min(width, height))
    readonly property var marks: variant === "mind" ? [
        { x: .10, y: .16, s: .11, r: -17, o: .15, asset: "horned-skull.svg" },
        { x: .87, y: .12, s: .09, r: 22, o: .18, asset: "warp-eye.svg" },
        { x: .08, y: .48, s: .08, r: 11, o: .13, asset: "broken-halo-rune.svg" },
        { x: .91, y: .42, s: .12, r: -28, o: .16, asset: "fractured-chaos-seal.svg" },
        { x: .14, y: .79, s: .10, r: 31, o: .17, asset: "warp-eye.svg" },
        { x: .84, y: .84, s: .13, r: -9, o: .14, asset: "horned-skull.svg" },
        { x: .52, y: .94, s: .08, r: 18, o: .12, asset: "broken-halo-rune.svg" }
    ] : variant === "canvas" ? [
        { x: .05, y: .14, s: .08, r: -13, o: .14, asset: "warp-eye.svg" },
        { x: .94, y: .09, s: .11, r: 24, o: .18, asset: "horned-skull.svg" },
        { x: .72, y: .21, s: .09, r: -30, o: .12, asset: "broken-halo-rune.svg" },
        { x: .89, y: .72, s: .14, r: 11, o: .17, asset: "fractured-chaos-seal.svg" },
        { x: .09, y: .83, s: .10, r: 19, o: .12, asset: "horned-skull.svg" },
        { x: .58, y: .92, s: .08, r: -8, o: .15, asset: "warp-eye.svg" }
    ] : variant === "ambient" ? [
        { x: .07, y: .13, s: .15, r: -22, o: .19, asset: "horned-skull.svg" },
        { x: .90, y: .16, s: .12, r: 31, o: .17, asset: "warp-eye.svg" },
        { x: .17, y: .59, s: .11, r: 8, o: .14, asset: "broken-halo-rune.svg" },
        { x: .91, y: .66, s: .16, r: -17, o: .18, asset: "fractured-chaos-seal.svg" },
        { x: .35, y: .88, s: .09, r: 25, o: .13, asset: "warp-eye.svg" },
        { x: .73, y: .90, s: .13, r: -33, o: .16, asset: "horned-skull.svg" }
    ] : [
        { x: .05, y: .17, s: .10, r: -19, o: .16, asset: "horned-skull.svg" },
        { x: .93, y: .13, s: .08, r: 27, o: .17, asset: "warp-eye.svg" },
        { x: .12, y: .61, s: .09, r: 16, o: .13, asset: "broken-halo-rune.svg" },
        { x: .87, y: .55, s: .12, r: -24, o: .15, asset: "fractured-chaos-seal.svg" },
        { x: .24, y: .89, s: .08, r: -8, o: .14, asset: "warp-eye.svg" },
        { x: .78, y: .86, s: .10, r: 21, o: .13, asset: "horned-skull.svg" },
        { x: .52, y: .08, s: .07, r: 12, o: .11, asset: "broken-halo-rune.svg" }
    ]

    Repeater {
        model: field.marks
        delegate: Image {
            required property var modelData
            width: field.shortEdge * modelData.s
            height: width
            x: field.width * modelData.x - width / 2
            y: field.height * modelData.y - height / 2
            rotation: modelData.r + field.ordinal * 7
            source: Qt.resolvedUrl("../../assets/heresy/" + modelData.asset)
            sourceSize.width: Math.max(128, Math.round(width * 1.5))
            sourceSize.height: Math.max(128, Math.round(height * 1.5))
            opacity: modelData.o * field.intensity * (.88 + backend.pulse * .08)
            asynchronous: true
            cache: true
        }
    }
}
