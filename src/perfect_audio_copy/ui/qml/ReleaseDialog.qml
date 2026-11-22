// Several releases match: pick one (country, date, label, catalog no., barcode, format).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as B
import Pac

PDialog {
    id: root
    heading: qsTr("Choose the release")
    property int picked: backend.releaseIndex
    onOpened: picked = Math.max(0, backend.releaseIndex)

    ColumnLayout {
        width: parent.width
        spacing: Theme.s2
        PLabel {
            Layout.fillWidth: true
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
            text: qsTr("More than one release matches this disc. Pick the one you own: the barcode and catalog number are printed on the case.")
        }
        Repeater {
            model: backend.releases
            delegate: Rectangle {
                required property var modelData
                required property int index
                Layout.fillWidth: true
                implicitHeight: 56
                radius: Theme.radiusMd
                color: root.picked === index ? Theme.tint(Theme.accent, Theme.selectedAlpha, Theme.surface2)
                     : hov.hovered ? Theme.surface3 : Theme.surface
                border.color: root.picked === index ? Theme.accent : Theme.border
                HoverHandler { id: hov }
                TapHandler { onTapped: root.picked = index; onDoubleTapped: { root.picked = index; root.accept() } }
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s3
                    anchors.rightMargin: Theme.s3
                    spacing: Theme.s3
                    Column {
                        Layout.fillWidth: true
                        PLabel { width: parent.width; kind: "strong"; text: modelData.title }
                        PLabel { width: parent.width; kind: "caption"; text: modelData.summary }
                    }
                    Badge { visible: modelData.exact; text: qsTr("Disc ID"); tone: Theme.positive; iconName: "badge-check" }
                    Badge { text: modelData.source; neutral: true }
                }
            }
        }
    }
    footerItem: [
        PButton { text: qsTr("Cancel"); onClicked: root.reject() },
        PButton { variant: "primary"; text: qsTr("Use this release"); onClicked: root.accept() }
    ]
    onAccepted: backend.chooseRelease(picked)
}
