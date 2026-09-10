import QtQuick 2.15
import QtQuick.Controls 2.15

TextField {
    id: field
    implicitHeight: 40
    implicitWidth: 240
    font.pixelSize: 14
    color: "#202b3a"
    selectByMouse: true
    leftPadding: 12
    rightPadding: 12
    background: Rectangle {
        color: field.enabled ? "white" : "#f5f7fa"
        radius: 6
        border.color: field.activeFocus ? "#087ee8" : "#ced8e2"
        border.width: field.activeFocus ? 2 : 1
    }
}
