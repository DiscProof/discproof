import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as B
import Pac

PDialog {
    id: root
    heading: qsTr("Rip log")
    width: Math.min(860, parent ? parent.width - 2 * Theme.s7 : 860)
    onOpened: area.text = backend.logText()
    B.ScrollView {
        width: parent.width
        height: Math.min(560, root.parent ? root.parent.height - 240 : 560)
        B.TextArea {
            id: area
            readOnly: true
            color: Theme.text
            font.family: Theme.fontNum
            font.pixelSize: 12
            selectByMouse: true
            wrapMode: TextEdit.NoWrap
            background: Rectangle { color: Theme.surface; radius: Theme.radiusMd; border.color: Theme.border }
            padding: Theme.s3
        }
    }
    footerItem: [ PButton { text: qsTr("Close"); onClicked: root.close() } ]
}
