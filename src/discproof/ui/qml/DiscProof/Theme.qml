// Design tokens: the single source of truth for colors, type, spacing and sizes.
// Ported from portfolio-tracker's ui/theme/tokens.py so both apps look alike.
// See docs/DESIGN_SYSTEM.md. Components never hard-code a color, size or spacing.
pragma Singleton
import QtQuick

QtObject {
    // Surfaces & lines (elevation = stepping bg -> surface -> surface2 -> surface3)
    readonly property color bg: "#0E1116"
    readonly property color surface: "#151A21"
    readonly property color surface2: "#1B222C"
    readonly property color surface3: "#232B37"
    readonly property color border: "#2A3340"
    readonly property color borderStrong: "#3A4555"
    // Text
    readonly property color text: "#E6EDF3"
    readonly property color textMuted: "#8B97A6"
    readonly property color textFaint: "#5D6876"
    readonly property color textOnAccent: "#0B0E12"
    // Semantic: colour always means something
    readonly property color accent: "#4C9AFF"      // primary action, focus, selection, reading
    readonly property color positive: "#2FBF71"    // matched / accurate / OK
    readonly property color negative: "#F2545B"    // suspicious / errors
    readonly property color warning: "#E3B341"     // recovered after re-reads, needs attention
    readonly property color info: "#9A8CFF"        // informational (fuzzy match, CD-Text)

    // Tints (semantic colour over surface)
    readonly property real softAlpha: 0.12
    readonly property real rowAlpha: 0.06
    readonly property real hoverAlpha: 0.06
    readonly property real selectedAlpha: 0.14
    function tint(c, a, over) { return Qt.tint(over === undefined ? surface : over, Qt.rgba(c.r, c.g, c.b, a)) }
    function soft(c) { return tint(c, softAlpha) }
    function rowTint(c) { return tint(c, rowAlpha) }

    // Spacing: 4px grid
    readonly property int s1: 4
    readonly property int s2: 8
    readonly property int s3: 12
    readonly property int s4: 16
    readonly property int s5: 20
    readonly property int s6: 24
    readonly property int s7: 32
    readonly property int s8: 40
    readonly property int radiusSm: 4
    readonly property int radiusMd: 6
    readonly property int radiusLg: 10

    // Sizes
    readonly property int headerH: 56
    readonly property int controlH: 32
    readonly property int controlHCompact: 28
    readonly property int rowH: 40
    readonly property int pillH: 22
    readonly property int progressH: 4
    readonly property int sectorMapH: 6
    readonly property int windowPad: 16
    readonly property int sectionGap: 12
    readonly property int cardPadV: 12
    readonly property int cardPadH: 16
    readonly property int iconSm: 16
    readonly property int iconMd: 18
    readonly property int brandMark: 28   // app mark in the header
    readonly property int coverSize: 288
    readonly property int sidePaneW: 320
    readonly property int dialogW: 560

    // Type: Inter for UI, JetBrains Mono (tabular) for numbers, CRCs, times
    readonly property string fontUi: Qt.application.font.family
    readonly property string fontNum: (typeof pacMonoFamily !== "undefined") ? pacMonoFamily : "monospace"
    readonly property int sizeDisplay: 28
    readonly property int sizeKpi: 20
    readonly property int sizeTitle: 15
    readonly property int sizeBody: 13
    readonly property int sizeCaption: 11
    readonly property real captionTracking: 0.44   // 0.04em at 11px, in px
}
