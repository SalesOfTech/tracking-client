import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: stage
    property string number: ""
    property string title: ""
    property string detail: ""
    property bool complete: false
    property var colors: desktop.colors
    implicitHeight: Math.max(84, body.implicitHeight + 32)
    RowLayout {
        anchors.fill: parent
        anchors.topMargin: 16
        anchors.bottomMargin: 16
        spacing: 22
        Rectangle {
            Layout.preferredWidth: 38; Layout.preferredHeight: 38; radius: 19
            color: stage.colors.surface
            Layout.alignment: Qt.AlignTop
            Label { anchors.centerIn: parent; text: stage.number; color: stage.colors.muted; font.pixelSize: 14; font.weight: Font.DemiBold }
        }
        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: 6
            Label { text: stage.title; textFormat: Text.PlainText; color: stage.colors.text; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.Wrap }
            Label { text: stage.detail; textFormat: Text.PlainText; color: stage.colors.muted; font.pixelSize: 14; Layout.fillWidth: true; wrapMode: Text.Wrap }
        }
        Image { visible: stage.complete; source: "icons/CircleCheck-green.svg"; sourceSize.width: 23; sourceSize.height: 23; Layout.preferredWidth: 23; Layout.preferredHeight: 23 }
    }
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: stage.colors.border }
}
