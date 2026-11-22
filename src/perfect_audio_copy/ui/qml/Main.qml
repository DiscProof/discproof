// Main window: header (drive, eject, setup, settings), album pane, track table,
// results and the rip bar. Styling comes only from the Pac theme (design tokens).
import QtQuick
import QtQuick.Controls.Basic as B
import QtQuick.Layouts
import Pac

B.ApplicationWindow {
    id: win
    required property string appVersion
    width: 1280
    height: 820
    minimumWidth: 980
    minimumHeight: 620
    visible: true
    title: backend.album.present && backend.album.title
           ? qsTr("%1 — %2 · Perfect Audio Copy").arg(backend.album.artist).arg(backend.album.title)
           : qsTr("Perfect Audio Copy")
    color: Theme.bg
    font.family: Theme.fontUi
    font.pixelSize: Theme.sizeBody

    readonly property bool showDisc: backend.album.present === true
    readonly property bool busyEmpty: !showDisc && (backend.state === "reading" || backend.state === "lookup")

    Shortcut { sequences: [StandardKey.Paste]; enabled: win.showDisc && !(win.activeFocusItem instanceof TextInput); onActivated: backend.pasteCover() }
    Shortcut { sequence: "F5"; onActivated: backend.loadDisc() }
    Shortcut { sequence: "Ctrl+,"; onActivated: settingsDialog.open() }

    header: Rectangle {
        height: Theme.headerH
        color: Theme.bg
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.windowPad
            anchors.rightMargin: Theme.windowPad - Theme.s1
            spacing: Theme.s2
            PIcon { name: "disc-3"; color: Theme.accent; size: 22 }
            PLabel { kind: "title"; text: qsTr("Perfect Audio Copy") }
            Rectangle { width: 1; height: 20; color: Theme.border; Layout.leftMargin: Theme.s2; Layout.rightMargin: Theme.s2 }
            PIcon {
                name: backend.state === "error" ? "triangle-alert" : (backend.state === "reading" || backend.state === "lookup" || backend.state === "ripping") ? "loader-circle" : "circle-dot"
                size: Theme.iconSm
                color: backend.state === "error" ? Theme.negative
                     : backend.state === "done" ? (backend.results.tone === "positive" ? Theme.positive
                         : backend.results.tone === "negative" ? Theme.negative
                         : backend.results.tone === "warning" ? Theme.warning : Theme.info)
                     : backend.state === "ready" ? Theme.positive
                     : backend.state === "reading" || backend.state === "lookup" || backend.state === "ripping" ? Theme.accent : Theme.textFaint
                RotationAnimation on rotation { running: backend.state === "reading" || backend.state === "lookup" || backend.state === "ripping"; from: 0; to: 360; duration: 1100; loops: Animation.Infinite }
            }
            PLabel { Layout.fillWidth: true; color: Theme.textMuted; text: backend.message }

            PComboBox {
                Layout.preferredWidth: 260
                model: backend.drives
                textRole: "name"
                currentIndex: backend.driveIndex
                displayText: backend.drives.length ? currentText : qsTr("No drive")
                enabled: backend.drives.length > 1 && backend.state !== "ripping"
                onActivated: (i) => backend.selectDrive(i)
            }
            PIconButton { iconName: "eject"; tip: qsTr("Eject"); enabled: backend.drives.length > 0 && backend.state !== "ripping"; onClicked: backend.eject() }
            PIconButton { iconName: "refresh-cw"; tip: qsTr("Read the disc again (F5)"); enabled: backend.state !== "ripping"; onClicked: backend.refreshDrives() }
            PIconButton {
                iconName: "gauge"
                tip: qsTr("Drive setup")
                iconColor: backend.driveProfile.known && !backend.driveProfile.configured ? Theme.warning : (hovered ? Theme.text : Theme.textMuted)
                enabled: backend.state !== "ripping"
                onClicked: wizardDialog.open()
            }
            PIconButton { iconName: "settings"; tip: qsTr("Settings (Ctrl+,)"); onClicked: settingsDialog.open() }
        }
    }

    // Drive-not-set-up banner
    Rectangle {
        id: banner
        visible: backend.driveProfile.known === true && !backend.driveProfile.configured && win.showDisc && backend.state !== "ripping"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Theme.windowPad
        height: visible ? 44 : 0
        radius: Theme.radiusMd
        color: Theme.rowTint(Theme.warning)
        border.color: Theme.tint(Theme.warning, 0.35, Theme.surface)
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.s3
            anchors.rightMargin: Theme.s2
            spacing: Theme.s2
            PIcon { name: "triangle-alert"; color: Theme.warning; size: Theme.iconSm }
            PLabel {
                Layout.fillWidth: true
                text: backend.driveProfile.offsetSource === "accuraterip-list"
                    ? qsTr("%1 hasn't been set up yet. Using read offset %2 from the AccurateRip drive list; cache behaviour is assumed.")
                        .arg(backend.driveProfile.name || qsTr("This drive"))
                        .arg((backend.driveProfile.offset > 0 ? "+" : "") + backend.driveProfile.offset)
                    : qsTr("%1 hasn't been set up yet. Its read offset is unknown and its cache behaviour is assumed.")
                        .arg(backend.driveProfile.name || qsTr("This drive"))
            }
            PButton { compact: true; variant: "primary"; iconName: "wand-sparkles"; text: qsTr("Set up drive"); onClicked: wizardDialog.open() }
        }
    }

    // Empty / loading states
    EmptyState {
        anchors.centerIn: parent
        visible: !win.showDisc
        busy: win.busyEmpty
        iconName: backend.state === "nodrive" ? "hard-drive" : backend.state === "error" ? "triangle-alert" : "disc-3"
        title: backend.state === "nodrive" ? qsTr("No CD drive found")
             : backend.state === "error" ? qsTr("Something went wrong")
             : win.busyEmpty ? qsTr("Reading the disc…")
             : qsTr("Insert an audio CD")
        text: backend.state === "nodrive" ? qsTr("Connect a CD or DVD drive. It is picked up automatically.")
            : backend.state === "error" ? backend.message
            : win.busyEmpty ? "" : qsTr("Perfect Audio Copy identifies the album, rips every track securely and verifies it with AccurateRip.")
    }

    // Disc view
    RowLayout {
        visible: win.showDisc
        anchors.fill: parent
        anchors.margins: Theme.windowPad
        anchors.topMargin: banner.visible ? banner.height + 2 * Theme.windowPad - Theme.s1 : Theme.windowPad
        spacing: Theme.sectionGap

        B.ScrollView {
            Layout.preferredWidth: Theme.sidePaneW
            Layout.fillHeight: true
            contentWidth: availableWidth
            B.ScrollBar.horizontal.policy: B.ScrollBar.AlwaysOff
            AlbumPane {
                width: Theme.sidePaneW
                onChooseRelease: releaseDialog.open()
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.sectionGap
            ResultsCard {
                Layout.fillWidth: true
                visible: backend.results.visible === true
                onViewLog: logDialog.open()
            }
            TrackTable { Layout.fillWidth: true; Layout.fillHeight: true }
            RipBar { Layout.fillWidth: true }
        }
    }

    footer: Rectangle {
        height: 26
        color: Theme.bg
        Rectangle { width: parent.width; height: 1; color: Theme.border }
        PLabel {
            anchors.left: parent.left
            anchors.leftMargin: Theme.windowPad
            anchors.verticalCenter: parent.verticalCenter
            kind: "caption"
            color: Theme.textFaint
            text: backend.driveProfile.known
                  ? qsTr("%1 · offset %2 · %3 · v%4")
                      .arg(backend.driveProfile.name)
                      .arg((backend.driveProfile.offset > 0 ? "+" : "") + backend.driveProfile.offset)
                      .arg(backend.driveProfile.caches ? qsTr("caches audio") : qsTr("no audio cache"))
                      .arg(win.appVersion)
                  : qsTr("v%1").arg(win.appVersion)
        }
    }

    SettingsDialog { id: settingsDialog }
    DriveWizardDialog { id: wizardDialog }
    ReleaseDialog { id: releaseDialog }
    ConflictDialog { id: conflictDialog }
    LogDialog { id: logDialog }

    Connections {
        target: backend
        function onChooseReleaseRequested() { releaseDialog.open() }
        function onFolderConflict(path) { conflictDialog.folder = path; conflictDialog.open() }
    }
    function openDialog(name) {
        const d = { settings: settingsDialog, wizard: wizardDialog, release: releaseDialog, log: logDialog, conflict: conflictDialog }[name]
        if (d) d.open()
    }
    Component.onCompleted: backend.refreshDrives()
}
