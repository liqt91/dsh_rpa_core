using System;
using System.Drawing;
using System.Windows.Automation;
using System.Windows.Forms;

namespace RpaCore.DesktopFixtureApp
{
    internal sealed class MainForm : Form
    {
        private readonly TextBox _queryInput;
        private readonly Button _submitButton;
        private readonly Label _resultText;

        public MainForm()
        {
            Text = "RPA Core Desktop Demo";
            Width = 520;
            Height = 240;
            StartPosition = FormStartPosition.CenterScreen;

            var prompt = new Label
            {
                AutoSize = true,
                Location = new Point(20, 20),
                Text = "Query"
            };

            _queryInput = new TextBox
            {
                Name = "queryInput",
                Location = new Point(20, 48),
                Width = 360,
                AccessibleName = "queryInput"
            };
            AutomationProperties.SetAutomationId(_queryInput, "queryInput");

            _submitButton = new Button
            {
                Name = "submitButton",
                Location = new Point(390, 46),
                Width = 90,
                Text = "Submit",
                AccessibleName = "submitButton"
            };
            AutomationProperties.SetAutomationId(_submitButton, "submitButton");
            _submitButton.Click += (_, _) => _resultText.Text = _queryInput.Text;

            _resultText = new Label
            {
                Name = "resultText",
                Location = new Point(20, 92),
                AutoSize = true,
                Text = "",
                AccessibleName = "resultText"
            };
            AutomationProperties.SetAutomationId(_resultText, "resultText");

            Controls.Add(prompt);
            Controls.Add(_queryInput);
            Controls.Add(_submitButton);
            Controls.Add(_resultText);
        }
    }
}
