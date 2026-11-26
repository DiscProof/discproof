import QtQuick
import QtQuick.Layouts
import DiscProof

PDialog {
    id: root
    property string folder
    heading: qsTr("The album folder already exists")
    ColumnLayout {
        width: parent.width
        spacing: Theme.s3
        PLabel { Layout.fillWidth: true; kind: "num"; color: Theme.textMuted; text: root.folder; elide: Text.ElideMiddle }
        PLabel {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
            text: qsTr("Rip into a new folder next to it, replace the files with the same names, or skip this rip.")
        }
    }
    footerItem: [
        PButton { text: qsTr("Skip"); onClicked: { backend.resolveConflict("skip"); root.close() } },
        PButton { variant: "danger"; text: qsTr("Overwrite"); onClicked: { backend.resolveConflict("overwrite"); root.close() } },
        PButton { variant: "primary"; text: qsTr("New folder"); onClicked: { backend.resolveConflict("suffix"); root.close() } }
    ]
}
