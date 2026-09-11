import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Button {
    id: control
    property bool primary: false
    property bool quiet: false
    property string glyph: ""
    property string hint: ""
    property var colors: desktop.colors
    implicitHeight: Math.max(38, row.implicitHeight + 16)
    implicitWidth: row.implicitWidth + 28
    leftPadding: 14
    rightPadding: 14
    opacity: enabled ? 1 : 0.5
    font.pixelSize: 14
    font.letterSpacing: 0
    hoverEnabled: true
    contentItem: Item {
        implicitWidth: row.implicitWidth
        implicitHeight: row.implicitHeight
        RowLayout {
            id: row
            anchors.centerIn: parent
            width: Math.min(implicitWidth, parent.width)
            spacing: 8
            Image {
                visible: control.glyph !== ""
                source: visible ? "icons/" + control.glyph + "-" + (control.primary || control.colors.dark ? "white" : "blue") + ".svg" : ""
                sourceSize.width: 19
                sourceSize.height: 19
                Layout.preferredWidth: 19
                Layout.preferredHeight: 19
            }
            Label {
                visible: control.text !== ""
                text: control.text
                color: control.primary ? "white" : control.colors.accent
                font: control.font
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                Layout.fillWidth: true
            }
        }
    }
    background: Rectangle {
        radius: 6
        color: control.primary ? (control.down ? control.colors.primaryPressed : control.hovered ? control.colors.primaryHover : control.colors.primary) : control.hovered ? control.colors.selected : "transparent"
        border.width: control.activeFocus ? 2 : control.quiet || control.primary ? 0 : 1
        border.color: control.activeFocus ? control.colors.accent : control.colors.border
    }
    ToolTip.text: hint
    ToolTip.visible: hovered && hint !== ""
    ToolTip.delay: 500
}
