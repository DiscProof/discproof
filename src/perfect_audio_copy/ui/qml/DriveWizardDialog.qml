// Drive setup: test caching / accurate stream / C2 / overread and find the read offset.
import QtQuick
import QtQuick.Layouts
import Pac

PDialog {
    id: root
    heading: qsTr("Set up this drive")
    readonly property var st: wizard.status
    readonly property var res: st.result || {}

    component Fact: RowLayout {
        property string label
        property string value
        property color tone: Theme.text
        property string hint: ""
        Layout.fillWidth: true
        spacing: Theme.s3
        PLabel { Layout.preferredWidth: 190; color: Theme.textMuted; text: parent.label }
        PLabel { kind: "strong"; color: parent.tone; text: parent.value }
        PLabel { Layout.fillWidth: true; kind: "caption"; text: parent.hint }
    }

    ColumnLayout {
        width: parent.width
        spacing: Theme.s3

        PLabel {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
            color: Theme.textMuted
            visible: !root.st.running && !root.st.hasResult
            text: qsTr("Perfect Audio Copy tests how %1 reads audio: whether it caches, whether reads line up exactly, whether its error flags can be trusted, and its read offset. It takes a minute or two with an audio CD inserted; a CD that is in AccurateRip lets it confirm the offset.")
                .arg(backend.driveProfile.name || qsTr("this drive"))
        }

        // Progress
        Repeater {
            model: root.st.running ? root.st.steps : []
            delegate: RowLayout {
                required property string modelData
                required property int index
                spacing: Theme.s2
                PIcon {
                    name: index === root.st.steps.length - 1 ? "loader-circle" : "circle-check"
                    color: index === root.st.steps.length - 1 ? Theme.accent : Theme.positive
                    size: Theme.iconSm
                    RotationAnimation on rotation { running: index === root.st.steps.length - 1; from: 0; to: 360; duration: 1100; loops: Animation.Infinite }
                }
                PLabel { text: modelData }
            }
        }

        // Results
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.s2
            visible: root.st.hasResult && !root.st.running
            Fact {
                label: qsTr("Caches audio")
                value: root.res.caches ? qsTr("Yes") : qsTr("No")
                tone: root.res.caches ? Theme.warning : Theme.positive
                hint: root.res.caches ? (root.res.fuaWorks ? qsTr("emptied with FUA") : root.res.seekAway ? qsTr("emptied by reading elsewhere (FUA ignored)") : qsTr("could not be emptied")) : ""
            }
            Fact { label: qsTr("Accurate stream"); value: root.res.accurateStream ? qsTr("Yes") : qsTr("No"); tone: root.res.accurateStream ? Theme.positive : Theme.warning; hint: root.res.accurateStream ? "" : qsTr("reads are aligned in software") }
            Fact {
                label: qsTr("C2 error pointers")
                value: !root.res.c2Supported ? qsTr("Not supported") : root.res.c2Trusted ? qsTr("Trusted") : qsTr("Supported")
                tone: root.res.c2Trusted ? Theme.positive : Theme.textMuted
                hint: root.res.c2Supported && !root.res.c2Tested ? qsTr("not trusted until tested with a scratched CD") : ""
            }
            Fact { label: qsTr("Overread lead-in/out"); value: root.res.overread ? qsTr("Yes") : qsTr("No"); tone: Theme.text; hint: root.res.overread ? "" : qsTr("missing edge samples become silence") }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border; Layout.topMargin: Theme.s1; Layout.bottomMargin: Theme.s1 }
            Fact { label: qsTr("AccurateRip drive list"); value: root.res.listOffset !== "" && root.res.listOffset !== undefined ? (root.res.listOffset > 0 ? "+" : "") + root.res.listOffset : qsTr("—"); hint: root.res.listInfo || "" }
            Fact { label: qsTr("This disc"); value: ""; hint: root.res.discNote || "" }
            RowLayout {
                spacing: Theme.s3
                PLabel { Layout.preferredWidth: 190; color: Theme.textMuted; text: qsTr("Read offset (samples)") }
                PTextField {
                    id: offsetField
                    Layout.preferredWidth: 96
                    num: true
                    horizontalAlignment: TextInput.AlignRight
                    text: String(root.st.offset)
                    validator: IntValidator { bottom: -5000; top: 5000 }
                    onEditingFinished: wizard.setOffset(parseInt(text))
                }
                Badge { visible: root.res.listAgrees === true; text: qsTr("matches this disc"); tone: Theme.positive; iconName: "badge-check" }
            }
            Repeater {
                model: root.res.notes || []
                delegate: PLabel { required property string modelData; Layout.fillWidth: true; kind: "caption"; color: Theme.warning; text: modelData; wrapMode: Text.WordWrap; elide: Text.ElideNone }
            }
        }
        PLabel { Layout.fillWidth: true; color: Theme.negative; text: root.st.error || ""; visible: text !== ""; wrapMode: Text.WordWrap; elide: Text.ElideNone }
    }
    footerItem: [
        PButton { text: root.st.hasResult ? qsTr("Close") : qsTr("Later"); onClicked: root.close() },
        PButton {
            variant: root.st.hasResult ? "secondary" : "primary"
            iconName: "wand-sparkles"
            text: root.st.hasResult ? qsTr("Test again") : qsTr("Start")
            enabled: !root.st.running && backend.discPresent
            onClicked: wizard.start()
        },
        PButton {
            visible: root.st.hasResult
            variant: "primary"
            iconName: "check"
            text: qsTr("Save")
            enabled: !root.st.running
            onClicked: { wizard.setOffset(parseInt(offsetField.text)); if (wizard.save()) root.close() }
        }
    ]
}
