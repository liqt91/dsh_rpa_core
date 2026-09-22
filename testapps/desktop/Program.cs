using System;
using System.Drawing;
using System.Windows.Forms;

namespace RpaCoreDesktopTest
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new TestForm());
        }
    }

    internal sealed class TestForm : Form
    {
        private readonly TextBox nameInput;
        private readonly Label resultText;
        private readonly Label countLabel;
        private readonly Label menuStatus;
        private readonly Label listStatus;
        private readonly Label comboStatus;
        private readonly Label dragStatus;
        private readonly ListBox optionsList;
        private readonly ComboBox optionsCombo;
        private readonly Label dragHandle;
        private readonly TextBox noteInput;
        private DialogForm activeDialog;
        private int clickCount;
        private bool dragging;
        private Point dragOrigin;

        public TestForm()
        {
            Name = "mainWindow";
            Text = "RPA Core Desktop Demo";
            ClientSize = new Size(620, 360);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;

            var nameLabel = new Label
            {
                Name = "nameLabel",
                Text = "Name",
                Location = new Point(24, 30),
                AutoSize = true
            };

            nameInput = new TextBox
            {
                Name = "queryInput",
                Location = new Point(100, 26),
                Width = 300
            };

            var generateButton = new Button
            {
                Name = "submitButton",
                Text = "Submit",
                Location = new Point(100, 72),
                Width = 160
            };
            generateButton.Click += GenerateGreeting;

            var dialogButton = new Button
            {
                Name = "dialogButton",
                Text = "Open Dialog",
                Location = new Point(100, 112),
                Width = 160
            };
            dialogButton.Click += OpenDialog;

            // 计数器（M38 S2.1）：**非幂等**靶子。点击累加、读数只增不减，
            // 于是「这个节点到底跑了几次」变成可断言的硬证据——暂停后「继续」若把
            // 已完成的点击节点重跑一遍，读数会变成 2 而不是 1。
            var countButton = new Button
            {
                Name = "countButton",
                Text = "Count",
                Location = new Point(280, 72),
                Width = 140
            };
            countButton.Click += CountUp;

            countLabel = new Label
            {
                Name = "countLabel",
                Text = "0",
                Location = new Point(280, 114),
                Width = 140,
                Height = 24,
                BorderStyle = BorderStyle.FixedSingle
            };

            resultText = new Label
            {
                Name = "resultText",
                Text = "Ready",
                Location = new Point(100, 166),
                Width = 300,
                Height = 40,
                BorderStyle = BorderStyle.FixedSingle
            };

            // 以下四组控件是 M38 S2 的靶子补齐（2026-09-22）。此前「select 只有负路径」
            // 「menuSelect 只有负路径」「drag 只有负路径」不是用例偷懒，而是靶子上根本没有
            // 对应形态的控件：没有列表/下拉就没有 SelectionItem，没有菜单栏就没有 HMENU，
            // 没有可拖控件就没有可观测的拖拽。补齐之后这三条命令才谈得上正路径。
            //
            // 每个控件都把「操作真的落到了它身上」写进一个只读状态 Label——命令返回值
            // 证明不了这一点（`desktop.select` 的实现把异常吞掉后照样返回 success），
            // 状态 Label 才是那个能区分「生效」与「静默成功」的读侧探针。
            optionsList = new ListBox
            {
                Name = "optionsList",
                Location = new Point(420, 26),
                Size = new Size(170, 100)
            };
            optionsList.Items.AddRange(new object[] { "alpha", "beta", "gamma" });
            optionsList.SelectedIndexChanged += ListSelectionChanged;

            optionsCombo = new ComboBox
            {
                Name = "optionsCombo",
                Location = new Point(420, 142),
                Width = 170,
                DropDownStyle = ComboBoxStyle.DropDownList
            };
            optionsCombo.Items.AddRange(new object[] { "one", "two", "three" });
            optionsCombo.SelectedIndexChanged += ComboSelectionChanged;

            dragHandle = new Label
            {
                Name = "dragHandle",
                Text = "Drag",
                Location = new Point(420, 180),
                Size = new Size(170, 60),
                BorderStyle = BorderStyle.FixedSingle,
                TextAlign = ContentAlignment.MiddleCenter
            };
            dragHandle.MouseDown += DragStart;
            dragHandle.MouseMove += DragMove;
            dragHandle.MouseUp += DragEnd;

            menuStatus = StatusLabel("menuStatus", 216);
            listStatus = StatusLabel("listStatus", 246);
            comboStatus = StatusLabel("comboStatus", 276);
            dragStatus = StatusLabel("dragStatus", 306);

            // 只读输入框，文本**恒定不变**。win32 定位器里 `title` 比的是控件的窗口文本，
            // 而 WinForms 的 Edit 在 win32 层的窗口文本就是它的**内容**：queryInput 初始为空
            // （空串过不了 minLength），一旦被写过又跟着变——两种情况下都没法当定位锚点。
            // 内容不动的 Edit 才给得出一个稳定的 title（也就能按值断言 getText 的读回值）。
            noteInput = new TextBox
            {
                Name = "readOnlyNote",
                Text = "note-ready",
                Location = new Point(420, 250),
                Width = 170,
                ReadOnly = true
            };

            // 原生菜单栏（HMENU，由 MainMenu 创建）。**不能用 MenuStrip**：它是托管控件，
            // 画在客户区里，`GetMenu(hwnd)` 拿不到——`desktop.win32.menuSelect` 走的正是
            // pywinauto 的原生菜单路径，MenuStrip 在它眼里等于「没有菜单」。
            var actionsMenu = new MenuItem("Actions");
            actionsMenu.MenuItems.Add(new MenuItem("Increment", MenuIncrement));
            var nestedMenu = new MenuItem("Nested");
            nestedMenu.MenuItems.Add(new MenuItem("Deep", MenuDeep));
            actionsMenu.MenuItems.Add(nestedMenu);
            var mainMenu = new MainMenu();
            mainMenu.MenuItems.Add(actionsMenu);
            Menu = mainMenu;

            Controls.Add(nameLabel);
            Controls.Add(nameInput);
            Controls.Add(generateButton);
            Controls.Add(dialogButton);
            Controls.Add(countButton);
            Controls.Add(countLabel);
            Controls.Add(resultText);
            Controls.Add(optionsList);
            Controls.Add(optionsCombo);
            Controls.Add(dragHandle);
            Controls.Add(noteInput);
            Controls.Add(menuStatus);
            Controls.Add(listStatus);
            Controls.Add(comboStatus);
            Controls.Add(dragStatus);
        }

        private static Label StatusLabel(string name, int top)
        {
            return new Label
            {
                Name = name,
                Text = "none",
                Location = new Point(100, top),
                Width = 300,
                Height = 24,
                BorderStyle = BorderStyle.FixedSingle
            };
        }

        private void GenerateGreeting(object sender, EventArgs args)
        {
            resultText.Text = nameInput.Text;
        }

        private void CountUp(object sender, EventArgs args)
        {
            clickCount++;
            countLabel.Text = clickCount.ToString();
        }

        private void ListSelectionChanged(object sender, EventArgs args)
        {
            listStatus.Text = "list:" + optionsList.SelectedIndex + ":" + optionsList.SelectedItem;
        }

        private void ComboSelectionChanged(object sender, EventArgs args)
        {
            comboStatus.Text = "combo:" + optionsCombo.SelectedIndex + ":" + optionsCombo.SelectedItem;
        }

        private void DragStart(object sender, MouseEventArgs args)
        {
            dragging = true;
            dragOrigin = args.Location;
        }

        private void DragMove(object sender, MouseEventArgs args)
        {
            if (!dragging)
            {
                return;
            }
            int dx = args.X - dragOrigin.X;
            int dy = args.Y - dragOrigin.Y;
            dragHandle.Location = new Point(dragHandle.Location.X + dx, dragHandle.Location.Y + dy);
            dragStatus.Text = "moved:" + dx + "," + dy;
        }

        private void DragEnd(object sender, MouseEventArgs args)
        {
            dragging = false;
            dragStatus.Text = "up:" + dragHandle.Location.X + "," + dragHandle.Location.Y;
        }

        private void MenuIncrement(object sender, EventArgs args)
        {
            // 刻意**不**碰计数器：countLabel 是暂停/继续用例的判据（读到 2 就是重跑），
            // 菜单点击去动它会把那条用例变成顺序相关。
            menuStatus.Text = "menu:increment";
        }

        private void MenuDeep(object sender, EventArgs args)
        {
            menuStatus.Text = "menu:deep";
        }

        private void OpenDialog(object sender, EventArgs args)
        {
            if (activeDialog != null)
            {
                activeDialog.Dispose();
                activeDialog = null;
            }
            activeDialog = new DialogForm();
            activeDialog.ClosedFromDialog += payload =>
            {
                resultText.Text = "dialog:" + payload;
                activeDialog.Dispose();
                activeDialog = null;
            };
            activeDialog.Show(this);
        }
    }

    internal sealed class DialogForm : Form
    {
        private readonly TextBox input;

        public event Action<string> ClosedFromDialog;

        public DialogForm()
        {
            Text = "RPA Core Desktop Dialog";
            ClientSize = new Size(300, 120);
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;

            input = new TextBox
            {
                Name = "dialogInput",
                Location = new Point(24, 24),
                Width = 250
            };

            var accept = new Button
            {
                Name = "acceptButton",
                Text = "Accept",
                Location = new Point(160, 66),
                Width = 114
            };
            accept.Click += AcceptClicked;

            Controls.Add(input);
            Controls.Add(accept);
            AcceptButton = accept;
        }

        private void AcceptClicked(object sender, EventArgs args)
        {
            var handler = ClosedFromDialog;
            if (handler != null)
            {
                handler(input.Text);
            }
            Close();
        }
    }
}
