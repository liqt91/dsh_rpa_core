/* Qt Quick Controls Material 风格 demo —— 展示 QML 层真实动画/阴影/圆角。

复刻编辑器三栏：左侧指令树、中部流程步骤（含拖拽换序暗示）、右侧属性面板。
纯 QML，由 python_worker 用 QQuickView 加载；视觉来自 QtQuick.Controls.Material。
*/
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: root
    width: 980
    height: 600
    visible: true
    title: "Qt Quick Material · 编辑器观感 demo"

    // Material 全局主题：深色 + 主色
    Material.theme: Material.Dark
    Material.accent: Material.Blue

    // 顶部标题
    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            spacing: 10
            Label {
                text: "RPA 编辑器"
                font.pixelSize: 18
                font.bold: true
                color: Material.foreground
            }
            Label {
                text: "QtQuick.Controls.Material"
                font.pixelSize: 12
                opacity: 0.6
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 12

        // 左：指令树
        Frame {
            Layout.preferredWidth: 240
            Layout.fillHeight: true
            ListView {
                anchors.fill: parent
                model: ListModel {
                    ListElement { g: "数据处理"; c: "data.setVar" }
                    ListElement { g: ""; c: "data.readText" }
                    ListElement { g: ""; c: "data.appendText" }
                    ListElement { g: "流程控制"; c: "workflow.sleep" }
                    ListElement { g: ""; c: "workflow.if" }
                    ListElement { g: "桌面会话"; c: "desktop.attachWindow" }
                    ListElement { g: ""; c: "desktop.click" }
                }
                delegate: ItemDelegate {
                    width: parent.width
                    height: holderHeight
                    property bool holder: false
                    contentItem: Column {
                        spacing: 2
                        Label {
                            visible: g !== ""
                            text: g
                            font.bold: true
                            font.pixelSize: 12
                            color: Material.accent
                            topPadding: holder ? 6 : 0
                        }
                        Label { text: c; color: Material.foreground; elide: Text.ElideRight }
                    }
                }
            }
        }

        // 中：流程步骤列表（Material 卡片 + 上移/下移）
        Frame {
            Layout.fillWidth: true
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent
                ListView {
                    id: stepList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 6
                    clip: true
                    model: ListModel {
                        ListElement { t: "打开网页" }
                        ListElement { t: "读取文本" }
                        ListElement { t: "写入变量" }
                        ListElement { t: "延时等待" }
                    }
                    // 模拟拖拽：轻按长按高亮再松开放大，暗示在此区域拖拽
                    delegate: Frame {
                        id: card
                        width: ListView.view.width
                        contentItem: RowLayout {
                            Label {
                                text: index + 1
                                Layout.preferredWidth: 28
                                horizontalAlignment: Text.AlignHCenter
                                color: Material.accent
                                font.bold: true
                            }
                            Label { text: t; color: Material.foreground }
                            Item { Layout.fillWidth: true }
                            Button { text: "↑" ; onClicked: move(-1) }
                            Button { text: "↓" ; onClicked: move(1) }
                        }
                        Behavior on scale { NumberAnimation { duration: 120 } }
                    }
                    function move(d) {
                        var i = currentIndex + d
                        if (i < 0 || i >= count) return
                        model.move(currentIndex, i, 1)
                        currentIndex = i
                    }
                }
            }
        }

        // 右：属性面板（Material 表单控件）
        Frame {
            Layout.preferredWidth: 300
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent
                spacing: 10
                Label {
                    text: "data.setVar"
                    font.bold: true
                    color: Material.accent
                }
                TextField { Layout.fillWidth: true; placeholderText: "变量名（新名=定义，已有名=赋值）" }
                ComboBox {
                    Layout.fillWidth: true
                    model: ["字符串", "数字", "布尔", "对象", "数组"]
                    currentIndex: 0
                }
                TextField { Layout.fillWidth: true; text: ""; placeholderText: "值" }
                Switch { text: "启用调试"; checked: true }
                Slider {
                    Layout.fillWidth: true
                    from: 0; to: 100; value: 64
                }
                Item { Layout.fillHeight: true }
                Button {
                    Layout.fillWidth: true
                    highlighted: true
                    text: "保存"
                }
            }
        }
    }

    // 底部状态
    footer: ToolBar {
        Label {
            anchors.centerIn: parent
            text: "QtQuick.Controls.Material · LGPL · 与 Python 能力层同进程加载"
            font.pixelSize: 11
            opacity: 0.6
        }
    }
}