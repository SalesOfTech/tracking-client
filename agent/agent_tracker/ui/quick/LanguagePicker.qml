import QtQuick 2.15
import QtQuick.Controls 2.15

ComboBox {
    id: control
    objectName: "language"
    property var colors: desktop.colors
    model: bridge.view.languages
    currentIndex: bridge.view.languageIndex
    onActivated: bridge.setLanguage(currentIndex)
    implicitWidth: 142
    implicitHeight: 38
    leftPadding: 12
    rightPadding: 32
    font.pixelSize: 14
    Accessible.name: bridge.view.labels.language
    contentItem: Text {
        objectName: "languageText"
        text: control.displayText
        textFormat: Text.PlainText
        font: control.font
        color: control.colors.text
        horizontalAlignment: Text.AlignLeft
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Image {
        x: control.width - width - 12
        y: (control.height - height) / 2
        width: 16
        height: 16
        source: "icons/ChevronRight-" + control.colors.icon + ".svg"
        rotation: 90
        sourceSize.width: 16
        sourceSize.height: 16
    }
    delegate: ItemDelegate {
        width: control.width
        height: 38
        leftPadding: 12
        rightPadding: 12
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: modelData
            font: control.font
            textFormat: Text.PlainText
            color: control.colors.text
            horizontalAlignment: Text.AlignLeft
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle { color: parent.highlighted ? control.colors.selected : control.colors.surface }
    }
    background: Rectangle {
        radius: 5
        color: control.hovered ? control.colors.hover : control.colors.background
        border.color: control.activeFocus ? control.colors.accent : control.colors.border
    }
}
