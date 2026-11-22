// Track list: checkbox, #, title (inline edit), artist, length, status chip, CRC,
// and a sector-map strip along the bottom of each row while/after ripping.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as B
import Pac

Card {
    id: root
    padV: 0; padH: 0
    readonly property bool editable: backend.state !== "ripping"
    readonly property int wCheck: 40
    readonly property int wNum: 36
    readonly property int wArtist: Math.max(140, width * 0.22)
    readonly property int wLength: 64
    readonly property int wGap: 64
    readonly property int wStatus: 128
    readonly property int wCrc: 92

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        // Header row
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 36
            color: Theme.surface2
            radius: Theme.radiusLg
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: parent.radius; color: parent.color }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.s3
                anchors.rightMargin: Theme.s4
                spacing: Theme.s2
                Item {
                    Layout.preferredWidth: root.wCheck - Theme.s3
                    Layout.fillHeight: true
                    PCheckBox {
                        anchors.verticalCenter: parent.verticalCenter
                        enabled: root.editable
                        checked: true
                        onToggled: backend.selectAll(checked)
                    }
                }
                PLabel { Layout.preferredWidth: root.wNum; kind: "strong"; color: Theme.textMuted; text: "#"; horizontalAlignment: Text.AlignRight }
                PLabel { Layout.fillWidth: true; kind: "strong"; color: Theme.textMuted; text: qsTr("Title"); leftPadding: Theme.s2 }
                PLabel { Layout.preferredWidth: root.wArtist; kind: "strong"; color: Theme.textMuted; text: qsTr("Artist") }
                PLabel { Layout.preferredWidth: root.wLength; kind: "strong"; color: Theme.textMuted; text: qsTr("Length"); horizontalAlignment: Text.AlignRight }
                PLabel { Layout.preferredWidth: root.wGap; kind: "strong"; color: Theme.textMuted; text: qsTr("Gap"); horizontalAlignment: Text.AlignRight }
                PLabel { Layout.preferredWidth: root.wStatus; kind: "strong"; color: Theme.textMuted; text: qsTr("Status"); leftPadding: Theme.s3 }
                PLabel { Layout.preferredWidth: root.wCrc; kind: "strong"; color: Theme.textMuted; text: qsTr("CRC"); horizontalAlignment: Text.AlignRight }
            }
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: backend.trackModel
            boundsBehavior: Flickable.StopAtBounds
            B.ScrollBar.vertical: B.ScrollBar { policy: B.ScrollBar.AsNeeded }

            delegate: Rectangle {
                id: row
                required property int index
                required property int number
                required property string title
                required property string artist
                required property string length
                required property string pregap
                required property bool selected
                required property string status
                required property string crc
                required property int confidence
                required property int mapVersion
                required property bool dataTrack
                readonly property bool active: status === "reading" || status === "testing" || status === "rereading"
                width: ListView.view.width
                height: Theme.rowH
                color: active ? Theme.tint(Theme.accent, Theme.selectedAlpha)
                     : rowHover.hovered ? Theme.tint(Theme.accent, Theme.hoverAlpha)
                     : "transparent"
                HoverHandler { id: rowHover }
                Rectangle {  // 2px left bar on the track being read
                    visible: row.active
                    width: 2; height: parent.height
                    color: row.status === "rereading" ? Theme.warning : Theme.accent
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s3
                    anchors.rightMargin: Theme.s4
                    spacing: Theme.s2
                    Item {
                        Layout.preferredWidth: root.wCheck - Theme.s3
                        Layout.fillHeight: true
                        PCheckBox {
                            anchors.verticalCenter: parent.verticalCenter
                            checked: row.selected
                            enabled: root.editable && !row.dataTrack
                            onToggled: backend.setTrackSelected(row.number, checked)
                        }
                    }
                    PLabel {
                        Layout.preferredWidth: root.wNum
                        kind: "num"
                        color: row.selected ? Theme.textMuted : Theme.textFaint
                        text: row.number
                        horizontalAlignment: Text.AlignRight
                    }
                    PTextField {
                        Layout.fillWidth: true
                        bare: true
                        implicitHeight: Theme.controlHCompact
                        text: row.title
                        color: row.selected ? Theme.text : Theme.textFaint
                        readOnly: !root.editable || row.dataTrack
                        onEditingFinished: if (text !== row.title) backend.setTrackField(row.number, "title", text)
                    }
                    PLabel {
                        Layout.preferredWidth: root.wArtist
                        text: row.artist
                        color: row.selected ? Theme.textMuted : Theme.textFaint
                    }
                    PLabel {
                        Layout.preferredWidth: root.wLength
                        kind: "num"
                        text: row.length
                        color: row.selected ? Theme.text : Theme.textFaint
                        horizontalAlignment: Text.AlignRight
                    }
                    PLabel {
                        Layout.preferredWidth: root.wGap
                        kind: "num"
                        text: row.pregap
                        color: row.pregap === "—" ? Theme.textFaint : Theme.textMuted
                        horizontalAlignment: Text.AlignRight
                    }
                    Item {
                        Layout.preferredWidth: root.wStatus
                        Layout.fillHeight: true
                        StatusChip {
                            x: Theme.s3
                            anchors.verticalCenter: parent.verticalCenter
                            status: row.selected || row.status !== "pending" ? row.status : "skipped"
                            confidence: row.confidence
                        }
                    }
                    PLabel {
                        Layout.preferredWidth: root.wCrc
                        kind: "num"
                        text: row.crc || "—"
                        color: row.crc ? Theme.textMuted : Theme.textFaint
                        horizontalAlignment: Text.AlignRight
                    }
                }
                // Sector map: green first-pass match, amber recovered, red suspicious
                Image {
                    visible: row.mapVersion > 0
                    anchors.bottom: parent.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: root.wCheck + root.wNum + Theme.s3
                    anchors.rightMargin: Theme.s4
                    height: 3
                    source: visible ? "image://sectormap/" + row.number + "/" + row.mapVersion : ""
                    sourceSize.width: Math.max(1, width)
                    sourceSize.height: 3
                    cache: false
                    asynchronous: false
                    smooth: false
                }
                Rectangle {
                    visible: row.mapVersion === 0
                    anchors.bottom: parent.bottom
                    width: parent.width; height: 1
                    color: Theme.border
                    opacity: 0.6
                }
            }
        }
    }
}
