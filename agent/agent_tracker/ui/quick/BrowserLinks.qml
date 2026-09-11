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
            Layout.fillWidth: true
            spacing: 16
            property string status: !modelData.installed ? links.labels.browser_not_installed
                                    : modelData.signed_package_required ? links.labels.firefox_pending
                                    : modelData.support_level === "native_host_verification_required"
                                    ? links.labels.browser_verify_host : links.labels.browser_manual_setup
            ActionButton {
                objectName: "openBrowser_" + modelData.family
                text: modelData.family
                glyph: "ExternalLink"
                hint: parent.status
                enabled: modelData.installed && !bridge.view.busy
                onClicked: bridge.openBrowser(modelData.family)
                Layout.preferredWidth: 150
            }
            Label { text: parent.status; color: desktop.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
        }
    }
    Label {
        visible: links.showFeedback && text !== ""
        text: bridge.view.browserMessage
        textFormat: Text.PlainText
        color: desktop.colors.muted
        Layout.fillWidth: true
        wrapMode: Text.Wrap
    }
}
