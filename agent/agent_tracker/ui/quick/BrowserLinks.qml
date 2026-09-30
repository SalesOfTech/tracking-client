import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ColumnLayout {
    id: links
    objectName: "browserLinks"
    property var labels: bridge.view.labels
    property bool showFeedback: false
    spacing: 8
    Repeater {
        model: bridge.view.browserSetup
        delegate: RowLayout {
            id: browserRow
            Layout.fillWidth: true
            spacing: 16
            property string status: !modelData.installed ? links.labels.browser_not_installed
                                    : modelData.signed_package_required ? links.labels.firefox_pending
                                    : modelData.support_level === "native_host_verification_required"
                                    ? links.labels.browser_verify_host : links.labels.browser_manual_setup
            ActionButton {
                objectName: "copyBrowser_" + modelData.family
                text: modelData.family
                glyph: "Copy"
                hint: links.labels.browser_copy_address + ": " + modelData.address
                Accessible.name: modelData.family + ": " + links.labels.browser_copy_address
                enabled: bridge.view.canCopyBrowser
                onClicked: bridge.openBrowser(modelData.family)
                Layout.preferredWidth: 150
                Layout.minimumWidth: 150
                Layout.maximumWidth: 150
                Layout.alignment: Qt.AlignTop
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Label { objectName: "browserAddress_" + modelData.family; text: modelData.address; textFormat: Text.PlainText; color: desktop.colors.text; Layout.fillWidth: true; wrapMode: Text.Wrap }
                Label { objectName: "browserInstruction_" + modelData.family; text: modelData.instruction; textFormat: Text.PlainText; color: desktop.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
                Label { text: browserRow.status; color: desktop.colors.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                Label {
                    objectName: "browserFeedback_" + modelData.family
                    visible: links.showFeedback && bridge.view.copiedBrowser === modelData.family && text !== ""
                    text: bridge.view.browserMessage
                    textFormat: Text.PlainText
                    color: desktop.colors.muted
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
