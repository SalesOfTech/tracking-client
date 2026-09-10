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
    color: "white"
    font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
    font.pixelSize: 14
    property var vm: bridge.view
    property var labels: window.vm.labels
    property int page: 0
    property bool detailsOpen: false
    onClosing: function(close) { if (desktop.hideOnClose) { close.accepted = false; window.hide(); } }

    Rectangle { anchors.fill: parent; color: "white" }
    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            color: "#f5f7fa"
            Layout.preferredWidth: window.width < 820 ? 168 : 186
            Layout.fillHeight: true
            Rectangle { anchors.right: parent.right; height: parent.height; width: 1; color: "#e3e8ee" }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                anchors.topMargin: 28
                spacing: 6
                Repeater {
                    model: [{text: labels.connection, icon:"Link"}, {text:labels.browsers,icon:"Globe"}, {text:labels.settings,icon:"Settings"}]
                    delegate: Button {
                        id: nav
                        objectName: "navigation" + index
                        Layout.fillWidth: true
                        implicitHeight: 45
                        hoverEnabled: true
                        text: modelData.text
                        onClicked: window.page = index
                        background: Rectangle { radius: 5; color: window.page === index ? "#e6f1fe" : nav.hovered ? "#ebeff4" : "transparent"; border.width: nav.activeFocus ? 2 : 0; border.color: "#087ee8" }
                        contentItem: RowLayout {
                            spacing: 11
                            Image { source: "icons/"+modelData.icon+"-"+(window.page===index?"blue":"neutral")+".svg"; sourceSize.width: 21; sourceSize.height: 21; Layout.leftMargin: 9 }
                            Label { text: modelData.text; color: window.page===index?"#0878db":"#536171"; font.pixelSize: 14; font.weight: window.page===index?Font.DemiBold:Font.Normal; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
                ActionButton { text: labels.help; glyph: "CircleHelp"; quiet: true; onClicked: bridge.open("guide"); Layout.fillWidth: true }
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
                Label { text: [labels.connection,labels.browsers,labels.settings][window.page]; color: "#111b2a"; font.pixelSize: 26; font.weight: Font.Bold; Layout.fillWidth: true; wrapMode: Text.Wrap }
                Image { source: "icons/Globe-neutral.svg"; sourceSize.width: 19; sourceSize.height: 19 }
                ComboBox {
                    id: language; objectName: "language"; model: window.vm.languages
                    currentIndex: window.vm.languageIndex; onActivated: bridge.setLanguage(currentIndex)
                    Layout.preferredWidth: 128; implicitHeight: 36; font.pixelSize: 13; Accessible.name: labels.language
                    background: Rectangle { radius: 5; color: language.hovered ? "#f5f8fb" : "white"; border.color: language.activeFocus ? "#1689ee" : "#dce2e9" }
                }
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
                            Rectangle { Layout.preferredWidth: 3; Layout.fillHeight: true; radius: 1; color: "#1689ee" }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 12
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 16
                                    Rectangle { width: 38; height: 38; radius: 19; color: "#e8f3fe"; Label { anchors.centerIn: parent; text: "04"; color: "#087ee8"; font.pixelSize: 14; font.weight: Font.DemiBold } }
                                    Label { text: labels.receiving; font.pixelSize: 17; font.weight: Font.DemiBold; color: "#182333"; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                }
                                Label { objectName: "deliveryStatus"; text: window.vm.deliveryLabel; color: window.vm.healthy ? "#208b65" : "#7a683e"; font.pixelSize: 15; Layout.fillWidth: true; wrapMode: Text.Wrap; Layout.leftMargin: 54 }
                                ColumnLayout {
                                    visible: window.vm.received; Layout.fillWidth: true; Layout.leftMargin: 54; Layout.topMargin: 8; spacing: 8
                                    Label { text: labels.last_session; color: "#788496"; font.pixelSize: 12 }
                                    Label { text: window.vm.hostname; color: "#1b283b"; font.pixelSize: 15; Layout.fillWidth: true; elide: Text.ElideMiddle; ToolTip.text: text; ToolTip.visible: domainMouse.containsMouse; MouseArea { id: domainMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton } }
                                    Rectangle { height: 5; Layout.fillWidth: true; radius: 2; color: "#36a677" }
                                    RowLayout {
                                        Layout.fillWidth: true; spacing: 12
                                        Label { text: window.vm.sessionStart + " - " + window.vm.sessionEnd; color: "#697689"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                        Label { text: window.vm.duration + " " + labels.seconds; color: "#208b65"; font.pixelSize: 12 }
                                    }
                                    Label { text: labels.last_confirmation + ": " + window.vm.confirmedAt; color: "#697689"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; Layout.leftMargin: 54; Layout.topMargin: 6; spacing: 8
                                    ActionButton { objectName:"checkConnection"; text: window.vm.busy?labels.checking:labels.check_again; glyph: "RefreshCw"; primary: true; enabled: !window.vm.busy; onClicked: bridge.check() }
                                    ActionButton { text: labels.open_dashboard; glyph: "ExternalLink"; quiet: true; enabled: window.vm.dashboardAvailable; onClicked: bridge.open("dashboard") }
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
                                Label { text: labels.last_contact + ": " + modelData.lastContact; color: "#788496"; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            }
                        }
                        Label { visible: window.vm.browsers.length===0; text: labels.browser_waiting; color: "#6c7889"; Layout.fillWidth: true; wrapMode: Text.Wrap; Layout.bottomMargin: 10 }
                        ActionButton { text: labels.connect_browsers; glyph: "Link"; primary: true; enabled: !window.vm.busy; onClicked: bridge.repair() }
                        ActionButton { text: labels.extension_folder; glyph: "FolderOpen"; onClicked: bridge.open("extension") }
                    }
                    ColumnLayout {
                        visible: window.page === 2
                        Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; spacing: 12
                        Label { text: labels.company_rules; font.pixelSize: 16; font.weight: Font.DemiBold; color:"#203049" }
                        Repeater {
                            model: window.vm.policy
                            delegate: RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                Label { text: modelData.name; color: "#4f5b6e"; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                Label { text: modelData.enabled?labels.on:labels.off; color:modelData.enabled?"#208b65":"#8590a0" }
                            }
                        }
                        Label { text: labels.allowed_sites; font.pixelSize: 16; font.weight: Font.DemiBold; color:"#203049"; Layout.topMargin: 18 }
                        Label { text: window.vm.domains.length?window.vm.domains.join("\n"):labels.no_sites; color: "#5f6d82"; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        ActionButton { text: labels.retry; enabled: !window.vm.busy && window.vm.rejected>0; onClicked: bridge.retry(); Layout.topMargin: 12 }
                    }
                    Label { visible: window.vm.error !== "" || window.vm.message !== ""; text: window.vm.error || window.vm.message; color: "#8b5e32"; font.pixelSize: 13; Layout.fillWidth: true; Layout.margins: 28; Layout.topMargin: 10; wrapMode: Text.Wrap }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.bottomMargin: 16; spacing: 8
                        ActionButton { text: labels.details; glyph: "ChevronRight"; quiet:true; onClicked: window.detailsOpen=!window.detailsOpen }
                        Label { visible: window.detailsOpen; text: labels.saved_local + ": " + window.vm.pending + "\n" + labels.needs_attention + ": " + window.vm.rejected; font.pixelSize: 13; color:"#68778b"; Layout.fillWidth:true; wrapMode:Text.Wrap }
                    }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: "#e3e8ee" }
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.topMargin: 14; Layout.bottomMargin: 14; spacing: 10
                Image { source: "icons/RefreshCw-neutral.svg"; sourceSize.width: 16; sourceSize.height: 16 }
                Label { text: labels.auto_update + " · " + window.vm.updateLabel; color: "#6c7889"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
                Label { text: window.vm.version; color: "#6c7889"; font.pixelSize: 12 }
            }
        }
    }
}
