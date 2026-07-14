import QtQuick
import QtQuick.Shapes

Item {
    id: veins
    property real screenX: 0
    property real screenY: 0
    property real virtualX: 0
    property real virtualY: 0
    property real virtualWidth: width
    property real virtualHeight: height
    property color nerveColor: "#5e2b70"
    property real intensity: 1.0
    clip: true

    Shape {
        id: globalLattice
        x: veins.virtualX - veins.screenX
        y: veins.virtualY - veins.screenY
        width: Math.max(veins.width, veins.virtualWidth)
        height: Math.max(veins.height, veins.virtualHeight)
        opacity: (.16 + backend.pulse * .035) * veins.intensity
        antialiasing: true

        ShapePath {
            fillColor: "transparent"
            strokeColor: veins.nerveColor
            strokeWidth: 3
            capStyle: ShapePath.RoundCap
            startX: globalLattice.width * -.03
            startY: globalLattice.height * .34
            PathCubic {
                x: globalLattice.width * 1.05
                y: globalLattice.height * .29
                control1X: globalLattice.width * .24
                control1Y: globalLattice.height * .08
                control2X: globalLattice.width * .72
                control2Y: globalLattice.height * .62
            }
        }

        ShapePath {
            fillColor: "transparent"
            strokeColor: "#9c7657"
            strokeWidth: 1.4
            capStyle: ShapePath.RoundCap
            startX: globalLattice.width * -.06
            startY: globalLattice.height * .73
            PathCubic {
                x: globalLattice.width * 1.08
                y: globalLattice.height * .61
                control1X: globalLattice.width * .31
                control1Y: globalLattice.height * .96
                control2X: globalLattice.width * .67
                control2Y: globalLattice.height * .38
            }
        }

        ShapePath {
            fillColor: "transparent"
            strokeColor: "#3b6570"
            strokeWidth: 1.8
            capStyle: ShapePath.RoundCap
            startX: globalLattice.width * .18
            startY: globalLattice.height * -.04
            PathCubic {
                x: globalLattice.width * .78
                y: globalLattice.height * 1.05
                control1X: globalLattice.width * .36
                control1Y: globalLattice.height * .24
                control2X: globalLattice.width * .55
                control2Y: globalLattice.height * .74
            }
        }

        ShapePath {
            fillColor: "transparent"
            strokeColor: "#55172b"
            strokeWidth: 2.2
            capStyle: ShapePath.RoundCap
            startX: globalLattice.width * .94
            startY: globalLattice.height * -.03
            PathCubic {
                x: globalLattice.width * .44
                y: globalLattice.height * 1.08
                control1X: globalLattice.width * .78
                control1Y: globalLattice.height * .34
                control2X: globalLattice.width * .61
                control2Y: globalLattice.height * .68
            }
        }
    }
}
