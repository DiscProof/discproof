// Phase 0 placeholder window. The real disc view arrives in Phase 6.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root

    required property string appVersion
    required property bool kirigamiAvailable
    required property string styleName

    width: 1100
    height: 700
    minimumWidth: 720
    minimumHeight: 480
    visible: true
    title: qsTr("Perfect Audio Copy")

    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 6
            spacing: 8

            Label {
                text: qsTr("Perfect Audio Copy")
                font.bold: true
                font.pointSize: Qt.application.font.pointSize * 1.15
            }
            Item { Layout.fillWidth: true }
            ComboBox {
                id: driveSelector
                enabled: false
                model: [qsTr("No drive detected")]
                Layout.preferredWidth: 240
            }
            ToolButton {
                icon.name: "media-eject"
                enabled: false
                ToolTip.text: qsTr("Eject")
                ToolTip.visible: hovered
            }
            ToolButton {
                icon.name: "configure"
                enabled: false
                ToolTip.text: qsTr("Settings")
                ToolTip.visible: hovered
            }
        }
    }

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 16

        Image {
            source: Qt.resolvedUrl("../icons/io.github.perfectaudiocopy.PerfectAudioCopy.svg")
            sourceSize.width: 128
            sourceSize.height: 128
            opacity: 0.85
            Layout.alignment: Qt.AlignHCenter
        }
        Label {
            text: qsTr("Insert an audio CD")
            font.pointSize: Qt.application.font.pointSize * 1.6
            Layout.alignment: Qt.AlignHCenter
        }
        Label {
            text: qsTr("Perfect Audio Copy will identify the album, rip every track securely and verify it with AccurateRip.")
            opacity: 0.7
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            Layout.maximumWidth: 460
            Layout.alignment: Qt.AlignHCenter
        }
        Button {
            text: qsTr("Rip")
            icon.name: "media-record"
            highlighted: true
            enabled: false
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 8
        }
    }

    footer: ToolBar {
        Label {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 12
            opacity: 0.6
            text: qsTr("v%1 · style %2 · Kirigami %3")
                .arg(root.appVersion)
                .arg(root.styleName)
                .arg(root.kirigamiAvailable ? qsTr("available") : qsTr("not found"))
        }
    }
}
