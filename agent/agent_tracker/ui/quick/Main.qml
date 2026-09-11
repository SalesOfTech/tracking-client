import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: window
    objectName: "trackingWindow"
    width: 960; height: 720
    minimumWidth: 740; minimumHeight: 580
    visible: false
    title: "SOFT Tracking"
    flags: Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
    color: colors.background
    font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
    font.pixelSize: 14
    property var vm: bridge.view
    property var labels: window.vm.labels
    property var colors: desktop.colors
    property int page: 0
    property bool detailsOpen: false
    palette.window: colors.background
    palette.windowText: colors.text
    palette.base: colors.background
    palette.alternateBase: colors.surface
    palette.text: colors.text
    palette.button: colors.surface
    palette.buttonText: colors.text
    palette.highlight: colors.primary
    palette.highlightedText: "white"
    onPageChanged: scroll.contentItem.contentY = 0
    onClosing: function(close) { if (desktop.hideOnClose) { close.accepted = false; window.hide(); } }

    Rectangle { anchors.fill: parent; color: window.colors.background }
    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            color: window.colors.surface
            Layout.preferredWidth: window.width < 820 ? 168 : 186
            Layout.fillHeight: true
            Rectangle { anchors.right: parent.right; height: parent.height; width: 1; color: window.colors.border }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                anchors.topMargin: 28
                spacing: 6
                Repeater {
                    model: [{text: labels.connection, icon:"Link"}, {text:labels.browsers,icon:"Globe"}, {text:labels.settings,icon:"Settings"}, {text:labels.help,icon:"CircleHelp"}]
                    delegate: Button {
                        id: nav
                        objectName: "navigation" + index
                        Layout.fillWidth: true
                        implicitHeight: 45
                        hoverEnabled: true
                        text: modelData.text
                        onClicked: window.page = index
                        background: Rectangle { radius: 5; color: window.page === index ? window.colors.selected : nav.hovered ? window.colors.hover : "transparent"; border.width: nav.activeFocus ? 2 : 0; border.color: window.colors.accent }
                        contentItem: RowLayout {
                            spacing: 11
                            Image { source: "icons/"+modelData.icon+"-"+(window.colors.dark?"white":window.page===index?"blue":"neutral")+".svg"; sourceSize.width: 21; sourceSize.height: 21; Layout.preferredWidth: 21; Layout.preferredHeight: 21; Layout.leftMargin: 9 }
                            Label { text: modelData.text; color: window.page===index?window.colors.accent:window.colors.muted; font.pixelSize: 14; font.weight: window.page===index?Font.DemiBold:Font.Normal; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 28
                Layout.bottomMargin: 16
                spacing: 16
                Label { text: [labels.connection,labels.browsers,labels.settings,labels.guide][window.page]; color: window.colors.text; font.pixelSize: 26; font.weight: Font.Bold; Layout.fillWidth: true; wrapMode: Text.Wrap }
                LanguagePicker { Layout.preferredWidth: 142; Layout.minimumWidth: 142; Layout.alignment: Qt.AlignVCenter }
            }
            ScrollView {
                id: scroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: scroll.availableWidth
                    spacing: 0
                    ColumnLayout {
                        visible: window.page === 0
                        Layout.fillWidth: true
                        Layout.leftMargin: 28
                        Layout.rightMargin: 28
                        spacing: 0
                        Stage { number: "01"; title: labels.company; detail: window.vm.company; complete: window.vm.enrolled || !window.vm.needsCode; Layout.fillWidth: true }
                        Entry { id: code; objectName: "companyCode"; visible: window.vm.needsCode && !window.vm.enrolled; placeholderText: labels.company_code; Layout.fillWidth: true; Layout.topMargin: 12 }
                        Stage { number: "02"; title: labels.employee; detail: window.vm.employee; complete: window.vm.enrolled; Layout.fillWidth: true }
                        ActionButton { objectName: "switchEmployee"; visible: window.vm.enrolled; text: labels.switch_employee; glyph: "Link"; quiet: true; enabled: window.vm.canManage && !window.vm.busy; onClicked: employeeDialog.open(); Layout.topMargin: 8; Layout.fillWidth: true }
                        RowLayout {
                            visible: !window.vm.enrolled
                            Layout.fillWidth: true; Layout.topMargin: 12; Layout.bottomMargin: 10; spacing: 8
                            Entry { id: employeeKey; objectName: "employeeKey"; placeholderText: labels.employee_key; echoMode: TextInput.Password; Layout.fillWidth: true; enabled: !window.vm.busy; onAccepted: connect.clicked() }
                            ActionButton { glyph: "ClipboardPaste"; hint: labels.paste; Accessible.name: labels.paste; enabled: !window.vm.busy; onClicked: employeeKey.text = bridge.paste() }
                            ActionButton { id: connect; text: window.vm.busy?labels.connecting:labels.connect; primary: true; enabled: !window.vm.busy && employeeKey.text.length>0; onClicked: { bridge.enroll(code.text,employeeKey.text); employeeKey.clear(); } }
                        }
                        Stage { number: "03"; title: labels.browser; detail: window.vm.browserReady ? window.vm.browserName + " · " + labels.browser_connected : labels.await_browser; complete: window.vm.browserReady; Layout.fillWidth: true }
                        RowLayout {
                            Layout.fillWidth: true; Layout.topMargin: 18; Layout.bottomMargin: 20; spacing: 20
                            Rectangle { Layout.preferredWidth: 3; Layout.fillHeight: true; radius: 1; color: window.colors.accent }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 12
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 16
                                    Rectangle { width: 38; height: 38; radius: 19; color: window.colors.selected; Label { anchors.centerIn: parent; text: "04"; color: window.colors.accent; font.pixelSize: 14; font.weight: Font.DemiBold } }
                                    Label { text: labels.receiving; font.pixelSize: 17; font.weight: Font.DemiBold; color: window.colors.text; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                }
                                Label { objectName: "deliveryStatus"; text: window.vm.deliveryLabel; color: window.vm.healthy ? window.colors.success : window.colors.warning; font.pixelSize: 15; Layout.fillWidth: true; wrapMode: Text.Wrap; Layout.leftMargin: 54 }
                                ColumnLayout {
                                    visible: window.vm.received; Layout.fillWidth: true; Layout.leftMargin: 54; Layout.topMargin: 8; spacing: 8
                                    Label { text: labels.last_session; color: window.colors.muted; font.pixelSize: 12 }
                                    Label { text: window.vm.hostname; color: window.colors.text; font.pixelSize: 15; Layout.fillWidth: true; elide: Text.ElideMiddle; ToolTip.text: text; ToolTip.visible: domainMouse.containsMouse; MouseArea { id: domainMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton } }
                                    Rectangle { height: 5; Layout.fillWidth: true; radius: 2; color: window.colors.success }
                                    RowLayout {
                                        Layout.fillWidth: true; spacing: 12
                                        Label { text: window.vm.sessionStart + " - " + window.vm.sessionEnd; color: window.colors.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                        Label { text: window.vm.duration + " " + labels.seconds; color: window.colors.success; font.pixelSize: 12 }
                                    }
                                    Label { text: labels.last_confirmation + ": " + window.vm.confirmedAt; color: window.colors.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                }
                                GridLayout {
                                    Layout.fillWidth: true; Layout.leftMargin: 54; Layout.topMargin: 6; columnSpacing: 8; rowSpacing: 8
                                    columns: width < 470 ? 1 : 2
                                    ActionButton { objectName:"checkConnection"; text: window.vm.busy?labels.checking:labels.check_again; glyph: "RefreshCw"; primary: true; enabled: !window.vm.busy; onClicked: bridge.check(); Layout.fillWidth: true }
                                    ActionButton { text: labels.open_dashboard; glyph: "ExternalLink"; quiet: true; enabled: window.vm.dashboardAvailable; onClicked: bridge.open("dashboard"); Layout.fillWidth: true }
                                }
                            }
                        }
                    }
                    ColumnLayout {
                        visible: window.page === 1
                        Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; spacing: 12
                        Repeater {
                            model: window.vm.browsers
                            delegate: ColumnLayout {
                                Layout.fillWidth: true; spacing: 7
                                Stage { Layout.fillWidth: true; number: (index+1).toString(); title: modelData.family + "  " + modelData.version; detail: modelData.label; complete: modelData.connected }
                                Label { text: labels.last_contact + ": " + modelData.lastContact; color: window.colors.muted; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            }
                        }
                        Label { visible: window.vm.browsers.length===0; text: labels.browser_waiting; color: window.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; Layout.bottomMargin: 10 }
                        ActionButton { text: labels.connect_browsers; glyph: "Link"; primary: true; enabled: !window.vm.busy; onClicked: bridge.repair(); Layout.fillWidth: true }
                        ActionButton { text: labels.extension_folder; glyph: "FolderOpen"; onClicked: bridge.open("extension"); Layout.fillWidth: true }
                        Label { visible: window.vm.browserSetup.length > 0; text: labels.browser_setup; color: window.colors.text; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.topMargin: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        BrowserLinks { Layout.fillWidth: true }
                    }
                    ColumnLayout {
                        visible: window.page === 2
                        Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; spacing: 12
                        Label { text: labels.company_rules; font.pixelSize: 16; font.weight: Font.DemiBold; color:window.colors.text }
                        Repeater {
                            model: window.vm.policy
                            delegate: RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                Label { text: modelData.name; color: window.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                Label { text: modelData.enabled?labels.on:labels.off; color:modelData.enabled?window.colors.success:window.colors.muted }
                            }
                        }
                        Label { text: labels.allowed_sites; font.pixelSize: 16; font.weight: Font.DemiBold; color:window.colors.text; Layout.topMargin: 18 }
                        Label { text: window.vm.domains.length?window.vm.domains.join("\n"):labels.no_sites; color: window.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        ActionButton { text: labels.retry; glyph: "RefreshCw"; enabled: !window.vm.busy && window.vm.rejected>0; onClicked: bridge.retry(); Layout.topMargin: 12; Layout.fillWidth: true }
                        Rectangle { Layout.fillWidth: true; height: 1; color: window.colors.border; Layout.topMargin: 16 }
                        Label { text: labels.administration; font.pixelSize: 16; font.weight: Font.DemiBold; color: window.colors.text; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Label { text: labels.per_user_notice; color: window.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Label { text: labels.user_autostart + ": " + (window.vm.admin.autostart.error ? labels.status_unknown : window.vm.admin.autostart.registered === true ? labels.autostart_registered : window.vm.admin.autostart.registered === false ? labels.autostart_missing : labels.status_unknown); color: window.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        ActionButton { objectName: "stopAgent"; text: labels.stop_agent; glyph: "Settings"; enabled: window.vm.canManage && !window.vm.busy; onClicked: bridge.requestStop(); Layout.fillWidth: true }
                    }
                    Guide { visible: window.page === 3; Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.bottomMargin: 28 }
                    Label { visible: window.page !== 3 && (window.vm.error !== "" || window.vm.message !== ""); text: window.vm.error || window.vm.message; color: window.colors.warning; font.pixelSize: 13; Layout.fillWidth: true; Layout.margins: 28; Layout.topMargin: 10; wrapMode: Text.Wrap }
                    ColumnLayout {
                        visible: window.page !== 3
                        Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.bottomMargin: 16; spacing: 8
                        ActionButton { text: labels.details; glyph: "ChevronRight"; quiet:true; onClicked: window.detailsOpen=!window.detailsOpen }
                        Label { visible: window.detailsOpen; text: labels.saved_local + ": " + window.vm.pending + "\n" + labels.needs_attention + ": " + window.vm.rejected; font.pixelSize: 13; color:window.colors.muted; Layout.fillWidth:true; wrapMode:Text.Wrap }
                    }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: window.colors.border }
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.topMargin: 14; Layout.bottomMargin: 14; spacing: 10
                Image { source: "icons/RefreshCw-" + window.colors.icon + ".svg"; sourceSize.width: 16; sourceSize.height: 16; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                Label { text: labels.auto_update + " · " + window.vm.updateLabel; color: window.colors.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                Label { text: window.vm.version; color: window.colors.muted; font.pixelSize: 12 }
            }
        }
    }
    Dialog {
        id: employeeDialog
        objectName: "employeeDialog"
        parent: Overlay.overlay
        title: labels.switch_employee
        modal: true
        width: Math.min(520, window.width - 64)
        x: (parent.width - width) / 2
        y: Math.max(16, (parent.height - height) / 2)
        onOpened: nextEmployeeKey.forceActiveFocus()
        onClosed: { nextEmployeeKey.clear(); nextCompanyCode.clear(); }
        contentItem: ColumnLayout {
            spacing: 16
            Label { text: labels.switch_notice; color: window.colors.muted; textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Entry { id: nextCompanyCode; objectName: "switchCompanyCode"; visible: window.vm.needsCode; placeholderText: labels.company_code; Accessible.name: labels.company_code; Layout.fillWidth: true }
            Entry { id: nextEmployeeKey; objectName: "switchEmployeeKey"; placeholderText: labels.employee_key; Accessible.name: labels.employee_key; echoMode: TextInput.Password; Layout.fillWidth: true }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                ActionButton { text: labels.cancel_action; onClicked: employeeDialog.close(); Layout.fillWidth: true }
                ActionButton { objectName: "confirmEmployeeSwitch"; text: labels.switch_employee; primary: true; enabled: !window.vm.busy && nextEmployeeKey.text.length > 0 && (!window.vm.needsCode || nextCompanyCode.text.length > 0); onClicked: { bridge.switchEmployee(nextCompanyCode.text, nextEmployeeKey.text); employeeDialog.close(); } Layout.fillWidth: true }
            }
        }
    }
}
