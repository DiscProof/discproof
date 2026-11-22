// After a rip: headline, KPI cards, and actions.
import QtQuick
import QtQuick.Layouts
import Pac

Card {
    id: root
    signal viewLog()
    readonly property var r: backend.results
    readonly property color toneColor: r.tone === "positive" ? Theme.positive
        : r.tone === "negative" ? Theme.negative
        : r.tone === "warning" ? Theme.warning : Theme.info
    implicitHeight: col.implicitHeight + 2 * padV
    border.color: Theme.tint(toneColor, 0.35, Theme.surface)
    color: Theme.rowTint(toneColor)

    ColumnLayout {
        id: col
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Theme.s3
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s3
            PIcon {
                name: root.r.tone === "positive" ? "circle-check" : root.r.tone === "negative" ? "octagon-alert" : "info"
                color: root.toneColor
                size: 22
            }
            PLabel { kind: "title"; text: root.r.headline || ""; Layout.fillWidth: true }
            PButton { iconName: "file-text"; text: qsTr("View log"); onClicked: root.viewLog() }
            PButton { variant: "primary"; iconName: "folder-open"; text: qsTr("Open folder"); onClicked: backend.openFolder() }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s3
            KpiCard {
                Layout.fillWidth: true
                label: qsTr("AccurateRip")
                value: (root.r.accurate || 0) + " / " + (root.r.total || 0)
                valueColor: root.r.accurate === root.r.total && root.r.total > 0 ? Theme.positive : Theme.text
                sub: root.r.confidence ? qsTr("best confidence %1").arg(root.r.confidence) : qsTr("tracks verified")
            }
            KpiCard {
                Layout.fillWidth: true
                label: qsTr("Suspicious")
                value: String(root.r.suspicious || 0)
                valueColor: root.r.suspicious ? Theme.negative : Theme.text
                sub: root.r.suspicious ? qsTr("see the log") : qsTr("tracks")
            }
            KpiCard {
                Layout.fillWidth: true
                label: qsTr("Time")
                value: root.r.elapsed || "—"
                sub: qsTr("total")
            }
        }
        PLabel {
            Layout.fillWidth: true
            kind: "caption"
            color: Theme.negative
            visible: (root.r.errors || []).length > 0
            text: (root.r.errors || []).join("\n")
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
        }
        PLabel {
            Layout.fillWidth: true
            kind: "caption"
            text: root.r.folder || ""
            elide: Text.ElideMiddle
        }
    }
}
