import QtQuick 2.15
import QtQuick.Controls 2.15

TextField {
    id: field
    property var colors: desktop.colors
    implicitHeight: 40
    implicitWidth: 240
    font.pixelSize: 14
    color: colors.text
    placeholderTextColor: colors.muted
    selectionColor: colors.primary
    selectedTextColor: "white"
    selectByMouse: true
    leftPadding: 12
    rightPadding: 12
    background: Rectangle {
        color: field.enabled ? field.colors.background : field.colors.surface
        radius: 6
        border.color: field.activeFocus ? field.colors.accent : field.colors.border
        border.width: field.activeFocus ? 2 : 1
    }
}
