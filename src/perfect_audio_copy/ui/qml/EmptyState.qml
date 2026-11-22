// Centred icon, title and explanation for "no drive", "no disc" and loading states.
import QtQuick
import QtQuick.Layouts
import Pac

ColumnLayout {
    id: root
    property string iconName: "disc-3"
    property string title
    property string text
    property bool busy: false
    spacing: Theme.s3

    Rectangle {
        Layout.alignment: Qt.AlignHCenter
        width: 88; height: 88; radius: 44
        color: Theme.surface
        border.color: Theme.border
        PIcon {
            id: glyph
            anchors.centerIn: parent
            name: root.busy ? "loader-circle" : root.iconName
            size: 40
            color: root.busy ? Theme.accent : Theme.textMuted
            RotationAnimation on rotation {
                running: root.busy
                from: 0; to: 360; duration: 1100; loops: Animation.Infinite
            }
        }
    }
    PLabel {
        Layout.alignment: Qt.AlignHCenter
        Layout.topMargin: Theme.s2
        kind: "title"
        text: root.title
    }
    PLabel {
        Layout.alignment: Qt.AlignHCenter
        Layout.maximumWidth: 440
        color: Theme.textMuted
        text: root.text
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
        visible: text !== ""
    }
}
