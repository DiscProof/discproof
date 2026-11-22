// Track status as a badge: Pending, Reading, Re-reading, Testing, Encoding, OK,
// Suspicious, AR ✓ N, Error. Text always carries the meaning (not colour alone).
import QtQuick

Badge {
    property string status: "pending"
    property int confidence: 0
    text: status === "pending" ? qsTr("Pending")
        : status === "reading" ? qsTr("Reading")
        : status === "testing" ? qsTr("Testing")
        : status === "rereading" ? qsTr("Re-reading")
        : status === "encoding" ? qsTr("Encoding")
        : status === "ok" ? qsTr("OK")
        : status === "accurate" ? qsTr("AR ✓ %1").arg(confidence)
        : status === "notaccurate" ? qsTr("AR ✗")
        : status === "suspicious" ? qsTr("Suspicious")
        : status === "skipped" ? qsTr("Skipped")
        : status === "error" ? qsTr("Error") : status
    neutral: status === "pending" || status === "skipped"
    tone: status === "reading" || status === "testing" || status === "encoding" ? Theme.accent
        : status === "rereading" ? Theme.warning
        : status === "ok" || status === "accurate" ? Theme.positive
        : status === "notaccurate" ? Theme.warning
        : Theme.negative
    solid: status === "error"
    iconName: status === "accurate" ? "badge-check"
        : status === "suspicious" || status === "error" ? "triangle-alert"
        : status === "rereading" ? "refresh-cw" : ""
}
