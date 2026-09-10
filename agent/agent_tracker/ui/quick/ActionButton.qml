import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Button {
    id: control
    property bool primary: false
    property bool quiet: false
    property string glyph: ""
    property string hint: ""
    implicitHeight: 38
    implicitWidth: row.implicitWidth + 28
    leftPadding: 14
    rightPadding: 14
    opacity: enabled ? 1 : 0.5
    font.pixelSize: 14
    font.letterSpacing: 0
    hoverEnabled: true
    contentItem: RowLayout {
        id: row
        spacing: 8
        Image {
            visible: control.glyph !== ""
            source: visible ? "icons/" + control.glyph + "-" + (control.primary ? "white" : "blue") + ".svg" : ""
            sourceSize.width: 19
            sourceSize.height: 19
            Layout.preferredWidth: 19
            Layout.preferredHeight: 19
        }
        Label {
            text: control.text
            color: control.primary ? "white" : "#1675c9"
            font: control.font
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
        }
    }
    background: Rectangle {
        radius: 6
        color: control.primary ? (control.down ? "#0966c1" : control.hovered ? "#0876dc" : "#087ee8") : control.hovered ? "#edf5fc" : "transparent"
        border.width: control.activeFocus ? 2 : control.quiet || control.primary ? 0 : 1
        border.color: control.activeFocus ? "#087ee8" : "#dce3e9"
    }
    ToolTip.text: hint
    ToolTip.visible: hovered && hint !== ""
    ToolTip.delay: 500
}
