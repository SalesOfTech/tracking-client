import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ColumnLayout {
    id: guide
    objectName: "embeddedGuide"
    property var content: bridge.view.guide
    property var colors: desktop.colors
    spacing: 18

    Label {
        objectName: "guideIntro"
        text: guide.content.intro
        textFormat: Text.PlainText
        color: guide.colors.text
        wrapMode: Text.Wrap
        Layout.fillWidth: true
    }
    Label {
        visible: text.length > 0
        text: guide.content.note
        textFormat: Text.PlainText
        color: guide.colors.warning
        wrapMode: Text.Wrap
        Layout.fillWidth: true
    }
    Repeater {
        model: guide.content.sections
        delegate: ColumnLayout {
            property var section: modelData
            objectName: "guideSection_" + section.id
            Layout.fillWidth: true
            spacing: 12
            Rectangle { Layout.fillWidth: true; height: 1; color: guide.colors.border }
            Label {
                text: section.title
                textFormat: Text.PlainText
                color: guide.colors.text
                font.pixelSize: 18
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            BrowserLinks { visible: section.id === "browsers"; showFeedback: true; Layout.fillWidth: true }
            Repeater {
                model: section.steps
                delegate: RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Label {
                        text: (index + 1) + "."
                        color: guide.colors.muted
                        Layout.preferredWidth: 24
                        Layout.alignment: Qt.AlignTop
                    }
                    Label {
                        text: modelData
                        textFormat: Text.PlainText
                        color: guide.colors.text
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
            }
        }
    }
}
