// Bottom bar: the Rip split-button (mode menu) or, while ripping, live progress.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as B
import Pac

Card {
    id: root
    implicitHeight: backend.rip.active ? 92 : 64
    readonly property string mode: backend.settings.mode === "burst" ? "burst"
        : backend.settings.test_and_copy ? "paranoid" : "secure"
    readonly property string modeLabel: mode === "burst" ? qsTr("Burst") : mode === "paranoid" ? qsTr("Paranoid") : qsTr("Secure")
    function setMode(m) {
        backend.setSetting("mode", m === "burst" ? "burst" : "secure")
        backend.setSetting("test_and_copy", m === "paranoid")
    }

    // Idle: selection summary + Rip
    RowLayout {
        anchors.fill: parent
        spacing: Theme.s3
        visible: !backend.rip.active
        PIcon { name: "info"; color: Theme.textFaint; visible: hint.text !== "" }
        PLabel {
            id: hint
            Layout.fillWidth: true
            color: Theme.textMuted
            text: !backend.discPresent ? (backend.results.visible ? qsTr("Insert the next CD to continue") : "")
                : backend.driveProfile.known && !backend.driveProfile.configured
                  ? qsTr("Drive not set up yet: ripping uses safe defaults")
                  : root.mode === "burst" ? qsTr("Burst: one fast pass, relies on AccurateRip to confirm")
                  : root.mode === "paranoid" ? qsTr("Paranoid: secure rip plus a full test pass; both CRCs must match")
                  : qsTr("Secure: every sector read until two reads agree")
        }
        Row {
            spacing: 1
            enabled: backend.discPresent && (backend.state === "ready" || backend.state === "done")
            PButton {
                id: ripButton
                variant: "primary"
                iconName: "disc-3"
                text: qsTr("Rip · %1").arg(root.modeLabel)
                implicitHeight: 36
                background: Rectangle {
                    radius: Theme.radiusMd
                    color: !ripButton.enabled ? Theme.surface3 : ripButton.down ? Qt.darker(Theme.accent, 1.15) : ripButton.hovered ? Qt.lighter(Theme.accent, 1.08) : Theme.accent
                    Rectangle { anchors.right: parent.right; width: Theme.radiusMd; height: parent.height; color: parent.color }
                }
                onClicked: backend.startRip("")
            }
            PButton {
                id: modeButton
                variant: "primary"
                iconName: "chevron-down"
                implicitHeight: 36
                implicitWidth: 32
                background: Rectangle {
                    radius: Theme.radiusMd
                    color: !modeButton.enabled ? Theme.surface3 : modeButton.down || modeMenu.visible ? Qt.darker(Theme.accent, 1.15) : modeButton.hovered ? Qt.lighter(Theme.accent, 1.08) : Theme.accent
                    Rectangle { anchors.left: parent.left; width: Theme.radiusMd; height: parent.height; color: parent.color }
                }
                onClicked: modeMenu.open()
                B.Menu {
                    id: modeMenu
                    y: -height - 4
                    x: parent.width - width
                    width: 300
                    padding: 4
                    background: Rectangle { color: Theme.surface2; radius: Theme.radiusMd; border.color: Theme.border }
                    Repeater {
                        model: [
                            { key: "secure", label: qsTr("Secure"), sub: qsTr("Re-read until two reads agree (default)") },
                            { key: "paranoid", label: qsTr("Paranoid"), sub: qsTr("Secure plus a test pass (Test & Copy)") },
                            { key: "burst", label: qsTr("Burst"), sub: qsTr("One fast pass, no re-reads") }
                        ]
                        delegate: B.MenuItem {
                            required property var modelData
                            width: modeMenu.width - 8
                            height: 48
                            onTriggered: root.setMode(modelData.key)
                            contentItem: RowLayout {
                                spacing: Theme.s2
                                PIcon { name: "check"; size: Theme.iconSm; color: Theme.accent; opacity: root.mode === modelData.key ? 1 : 0 }
                                Column {
                                    Layout.fillWidth: true
                                    PLabel { kind: "strong"; text: modelData.label }
                                    PLabel { kind: "caption"; text: modelData.sub }
                                }
                            }
                            background: Rectangle { radius: Theme.radiusSm; color: parent.highlighted ? Theme.surface3 : "transparent" }
                        }
                    }
                }
            }
        }
    }

    // Ripping: progress
    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.s2
        visible: backend.rip.active === true
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s3
            PIcon {
                name: backend.rip.paused ? "pause" : "loader-circle"
                color: Theme.accent
                RotationAnimation on rotation { running: backend.rip.active === true && !backend.rip.paused; from: 0; to: 360; duration: 1100; loops: Animation.Infinite }
            }
            PLabel {
                kind: "strong"
                text: backend.rip.paused ? qsTr("Paused")
                    : backend.rip.track ? qsTr("Track %1").arg(backend.rip.track) + (backend.rip.pass === "test" ? qsTr(" · test pass") : "")
                    : qsTr("Starting…")
            }
            PLabel { kind: "num"; color: Theme.textMuted; text: backend.rip.speed || "" }
            Item { Layout.fillWidth: true }
            PIcon { name: "clock"; size: Theme.iconSm }
            PLabel { kind: "num"; color: Theme.textMuted; text: backend.rip.elapsed + (backend.rip.eta ? qsTr("  ·  %1 left").arg(backend.rip.eta) : "") }
            PButton {
                compact: true
                iconName: backend.rip.paused ? "play" : "pause"
                text: backend.rip.paused ? qsTr("Resume") : qsTr("Pause")
                onClicked: backend.rip.paused ? backend.resumeRip() : backend.pauseRip()
            }
            PButton { compact: true; variant: "danger"; iconName: "square"; text: qsTr("Cancel"); onClicked: backend.cancelRip() }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s3
            PProgressBar { Layout.fillWidth: true; value: backend.rip.overall || 0 }
            PLabel { kind: "num"; color: Theme.textMuted; text: Math.round((backend.rip.overall || 0) * 100) + "%"; Layout.preferredWidth: 40; horizontalAlignment: Text.AlignRight }
        }
    }
}
