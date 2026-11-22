// A labelled album field bound to backend.album[key]; commits on editing finished.
import QtQuick
import QtQuick.Layouts
import Pac

ColumnLayout {
    id: root
    property string label
    property string key
    property bool num: false
    property string placeholder: ""
    spacing: Theme.s1
    function value() { const v = backend.album[root.key]; return v === undefined ? "" : String(v) }

    SectionHeader { text: root.label; topPadding: 0 }
    PTextField {
        id: field
        Layout.fillWidth: true
        num: root.num
        placeholderText: root.placeholder
        text: root.value()
        enabled: backend.album.present === true && backend.state !== "ripping"
        onEditingFinished: if (text !== root.value()) backend.setAlbumField(root.key, text)
        Connections {
            target: backend
            function onAlbumChanged() { if (!field.activeFocus) field.text = root.value() }
        }
    }
}
