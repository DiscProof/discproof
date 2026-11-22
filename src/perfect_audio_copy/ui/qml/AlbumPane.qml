// Left pane: cover art (drop or paste to replace) and the editable album details.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as B
import Pac

ColumnLayout {
    id: root
    signal chooseRelease()
    spacing: Theme.sectionGap

    // Cover
    Card {
        Layout.fillWidth: true
        Layout.preferredHeight: width
        padV: 0; padH: 0
        clip: true
        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: Theme.radiusLg - 1
            color: Theme.surface2
            visible: cover.status !== Image.Ready
            Column {
                anchors.centerIn: parent
                spacing: Theme.s2
                PIcon { anchors.horizontalCenter: parent.horizontalCenter; name: "disc-3"; size: 56; color: Theme.textFaint }
                PLabel {
                    anchors.horizontalCenter: parent.horizontalCenter
                    kind: "caption"
                    color: Theme.textFaint
                    text: backend.album.present ? qsTr("No cover — drop or paste an image") : ""
                }
            }
        }
        Image {
            id: cover
            anchors.fill: parent
            anchors.margins: 1
            source: backend.coverUrl
            sourceSize.width: 640
            sourceSize.height: 640
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: false
            visible: status === Image.Ready
        }
        Rectangle {  // drop / hover affordance
            anchors.fill: parent
            radius: Theme.radiusLg
            color: drop.containsDrag ? Theme.tint(Theme.accent, 0.25, Theme.bg) : "transparent"
            opacity: drop.containsDrag ? 0.85 : 0
            border.width: drop.containsDrag ? 2 : 0
            border.color: Theme.accent
            PLabel { anchors.centerIn: parent; kind: "strong"; text: qsTr("Drop to use as cover"); visible: drop.containsDrag }
        }
        DropArea {
            id: drop
            anchors.fill: parent
            enabled: backend.album.present === true
            onDropped: (d) => { if (d.hasUrls) backend.setCoverFromUrl(d.urls[0]) }
        }
        Row {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: Theme.s2
            spacing: Theme.s1
            visible: hover.hovered && backend.album.present === true
            PIconButton {
                iconName: "image"; tip: qsTr("Paste image (Ctrl+V)")
                background: Rectangle { radius: Theme.radiusMd; color: Qt.rgba(0.05, 0.07, 0.09, 0.8) }
                onClicked: backend.pasteCover()
            }
            PIconButton {
                iconName: "x"; tip: qsTr("Remove cover")
                visible: backend.coverUrl !== ""
                background: Rectangle { radius: Theme.radiusMd; color: Qt.rgba(0.05, 0.07, 0.09, 0.8) }
                onClicked: backend.removeCover()
            }
        }
        HoverHandler { id: hover }
    }
    PLabel {
        Layout.topMargin: -Theme.s2
        kind: "caption"
        color: Theme.textFaint
        text: backend.coverInfo ? qsTr("Cover %1 px").arg(backend.coverInfo) : ""
        visible: text !== ""
    }

    // Album details
    Card {
        Layout.fillWidth: true
        Layout.preferredHeight: form.implicitHeight + 2 * padV
        ColumnLayout {
            id: form
            anchors.left: parent.left
            anchors.right: parent.right
            spacing: Theme.s3

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2
                Badge {
                    visible: backend.album.present === true
                    text: backend.album.source || ""
                    tone: backend.album.source === "MusicBrainz" || backend.album.source === "Discogs"
                          ? (backend.album.exact ? Theme.positive : Theme.info) : Theme.warning
                    iconName: backend.album.exact ? "badge-check" : ""
                }
                PLabel {
                    Layout.fillWidth: true
                    kind: "caption"
                    text: backend.album.exact ? qsTr("Exact disc match") : backend.album.source === "Manual" ? qsTr("Not found — edit below") : ""
                }
                PButton {
                    visible: backend.releases.length > 1
                    variant: backend.needsChoice ? "primary" : "ghost"
                    compact: true
                    text: qsTr("%1 matches").arg(backend.releases.length)
                    iconName: "list-music"
                    onClicked: root.chooseRelease()
                }
            }
            AlbumField { Layout.fillWidth: true; label: qsTr("Album artist"); key: "artist" }
            AlbumField { Layout.fillWidth: true; label: qsTr("Album"); key: "title" }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2
                AlbumField { Layout.fillWidth: true; label: qsTr("Date"); key: "year"; num: true; placeholder: "YYYY-MM-DD" }
                AlbumField { Layout.preferredWidth: 56; label: qsTr("Disc"); key: "discNumber"; num: true }
                AlbumField { Layout.preferredWidth: 56; label: qsTr("Of"); key: "discTotal"; num: true }
            }
            AlbumField { Layout.fillWidth: true; label: qsTr("Genre"); key: "genre" }
            PLabel {
                Layout.fillWidth: true
                kind: "caption"
                text: backend.album.summary || ""
                visible: text !== ""
                wrapMode: Text.WordWrap
                elide: Text.ElideNone
            }
        }
    }
    Item { Layout.fillHeight: true }
}
