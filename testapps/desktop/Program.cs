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
        private DialogForm activeDialog;

        public TestForm()
        {
            Name = "mainWindow";
            Text = "RPA Core Desktop Demo";
            ClientSize = new Size(440, 260);
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

            resultText = new Label
            {
                Name = "resultText",
                Text = "Ready",
                Location = new Point(100, 166),
                Width = 300,
                Height = 40,
                BorderStyle = BorderStyle.FixedSingle
            };

            Controls.Add(nameLabel);
            Controls.Add(nameInput);
            Controls.Add(generateButton);
            Controls.Add(dialogButton);
            Controls.Add(resultText);
        }

        private void GenerateGreeting(object sender, EventArgs args)
        {
            resultText.Text = nameInput.Text;
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
