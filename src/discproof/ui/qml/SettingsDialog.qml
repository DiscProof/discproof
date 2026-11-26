// Settings apply immediately (saved to settings.toml).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as B
import QtQuick.Dialogs
import DiscProof

PDialog {
    id: root
    heading: qsTr("Settings")
    width: Math.min(640, parent ? parent.width - 2 * Theme.s7 : 640)
    readonly property var s: backend.settings
    function set(key, value) { backend.setSetting(key, value) }

    component Row2: RowLayout {
        property alias label: lbl.text
        property alias sub: subl.text
        default property alias control: holder.data
        Layout.fillWidth: true
        spacing: Theme.s4
        Column {
            Layout.fillWidth: true
            PLabel { id: lbl; width: parent.width }
            PLabel { id: subl; kind: "caption"; width: parent.width; visible: text !== ""; wrapMode: Text.WordWrap; elide: Text.ElideNone }
        }
        Item { id: holder; Layout.preferredWidth: childrenRect.width; Layout.preferredHeight: childrenRect.height }
    }

    B.ScrollView {
        width: parent.width
        height: Math.min(580, root.parent ? root.parent.height - 200 : 580)
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width - Theme.s3
            spacing: Theme.s3

            SectionHeader { text: qsTr("Library") }
            RowLayout {
                Layout.fillWidth: true
                PTextField {
                    id: lib
                    Layout.fillWidth: true
                    text: root.s.library
                    onEditingFinished: root.set("library", text)
                }
                PButton { iconName: "folder-open"; text: qsTr("Choose…"); onClicked: folderDialog.open() }
            }
            SectionHeader { text: qsTr("File names") }
            PTextField {
                id: tpl
                Layout.fillWidth: true
                num: true
                text: root.s.template
                onEditingFinished: root.set("template", text)
            }
            PLabel {
                Layout.fillWidth: true
                kind: "caption"
                font.family: Theme.fontNum
                // Depends on the album too, so the preview follows lookups/edits.
                text: { backend.album.title; backend.album.artist; root.s.disc_subfolders; return backend.templatePreview(tpl.text) }
                color: text.startsWith("⚠") ? Theme.negative : Theme.textMuted
                elide: Text.ElideMiddle
            }
            PLabel {
                Layout.fillWidth: true
                kind: "caption"
                color: Theme.textFaint
                wrapMode: Text.WordWrap
                elide: Text.ElideNone
                text: qsTr("Fields: {albumartist} {album} {year} {title} {artist} {tracknumber:02} {disc_prefix} {discnumber} {genre} {label}. Use / for folders.")
            }
            PSwitch {
                Layout.fillWidth: true
                text: qsTr("Put each disc of a multi-disc set in its own folder (Disc 1, Disc 2…)")
                checked: root.s.disc_subfolders
                onToggled: root.set("disc_subfolders", checked)
            }

            SectionHeader { text: qsTr("Ripping") }
            Row2 {
                label: qsTr("Error recovery quality")
                sub: qsTr("Rounds of 16 re-reads per bad sector: 1, 3 or 5")
                PComboBox {
                    width: 160
                    model: [qsTr("Low"), qsTr("Medium"), qsTr("High")]
                    currentIndex: ["low", "medium", "high"].indexOf(root.s.quality)
                    onActivated: (i) => root.set("quality", ["low", "medium", "high"][i])
                }
            }
            Row2 {
                label: qsTr("Hidden track before track 1")
                sub: qsTr("Audio hidden in track 1's pre-gap (HTOA)")
                PComboBox {
                    width: 160
                    model: [qsTr("Discard"), qsTr("Rip as track 00")]
                    currentIndex: root.s.htoa === "track00" ? 1 : 0
                    onActivated: (i) => root.set("htoa", i === 1 ? "track00" : "discard")
                }
            }
            PSwitch {
                Layout.fillWidth: true
                text: qsTr("Detect pre-gaps before ripping (for the CUE sheet and log)")
                checked: root.s.detect_gaps
                onToggled: root.set("detect_gaps", checked)
            }
            Row2 {
                label: qsTr("When the album folder exists")
                PComboBox {
                    width: 160
                    model: [qsTr("Ask"), qsTr("New folder"), qsTr("Overwrite"), qsTr("Skip")]
                    currentIndex: ["ask", "suffix", "overwrite", "skip"].indexOf(root.s.existing)
                    onActivated: (i) => root.set("existing", ["ask", "suffix", "overwrite", "skip"][i])
                }
            }
            PSwitch { Layout.fillWidth: true; text: qsTr("Eject the disc when done"); checked: root.s.eject_when_done; onToggled: root.set("eject_when_done", checked) }
            PSwitch { Layout.fillWidth: true; text: qsTr("Notify when a rip finishes"); checked: root.s.notify; onToggled: root.set("notify", checked) }

            SectionHeader { text: qsTr("Output") }
            Row2 {
                label: qsTr("FLAC compression level")
                sub: qsTr("Lossless at every level; 8 makes the smallest files")
                PComboBox {
                    width: 160
                    model: ["0", "1", "2", "3", "4", "5", "6", "7", "8"]
                    currentIndex: root.s.flac_level
                    onActivated: (i) => root.set("flac_level", i)
                }
            }
            PSwitch { Layout.fillWidth: true; text: qsTr("Add ReplayGain (album and track)"); checked: root.s.replaygain; onToggled: root.set("replaygain", checked) }

            SectionHeader { text: qsTr("Metadata") }
            PSwitch { Layout.fillWidth: true; text: qsTr("Look up the album when a disc is inserted"); checked: root.s.auto_lookup; onToggled: root.set("auto_lookup", checked) }
            PSwitch { Layout.fillWidth: true; text: qsTr("Rip automatically when the match is unambiguous"); checked: root.s.auto_rip; onToggled: root.set("auto_rip", checked) }
            PSwitch { Layout.fillWidth: true; text: qsTr("Use MusicBrainz and the Cover Art Archive"); checked: root.s.use_musicbrainz; onToggled: root.set("use_musicbrainz", checked) }
            PSwitch { Layout.fillWidth: true; text: qsTr("Use Discogs as a fallback"); checked: root.s.use_discogs; onToggled: root.set("use_discogs", checked) }
            Row2 {
                label: qsTr("Discogs personal token")
                sub: qsTr("Optional; raises Discogs' rate limit")
                PTextField {
                    width: 220
                    echoMode: TextInput.Password
                    placeholderText: qsTr("not set")
                    text: root.s.discogs_token
                    enabled: root.s.use_discogs
                    onEditingFinished: root.set("discogs_token", text)
                }
            }
        }
    }
    footerItem: [ PButton { variant: "primary"; text: qsTr("Done"); onClicked: root.close() } ]

    FolderDialog {
        id: folderDialog
        currentFolder: "file://" + root.s.library
        onAccepted: root.set("library", decodeURIComponent(String(selectedFolder).replace("file://", "")))
    }
}
