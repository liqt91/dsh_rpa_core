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

        public TestForm()
        {
            Name = "mainWindow";
            Text = "RPA Core Desktop Test";
            ClientSize = new Size(440, 220);
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
                Name = "nameInput",
                Location = new Point(100, 26),
                Width = 300
            };

            var generateButton = new Button
            {
                Name = "generateButton",
                Text = "Generate greeting",
                Location = new Point(100, 72),
                Width = 160
            };
            generateButton.Click += GenerateGreeting;

            resultText = new Label
            {
                Name = "resultText",
                Text = "Ready",
                Location = new Point(100, 126),
                Width = 300,
                Height = 40,
                BorderStyle = BorderStyle.FixedSingle
            };

            Controls.Add(nameLabel);
            Controls.Add(nameInput);
            Controls.Add(generateButton);
            Controls.Add(resultText);
        }

        private void GenerateGreeting(object sender, EventArgs args)
        {
            resultText.Text = "Hello, " + nameInput.Text;
        }
    }
}
