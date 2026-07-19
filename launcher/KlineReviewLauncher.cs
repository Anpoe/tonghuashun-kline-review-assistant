using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: AssemblyTitle("同花顺 K线复盘助手")]
[assembly: AssemblyDescription("启动仓库中的同花顺 K线复盘助手当前源码")]
[assembly: AssemblyProduct("同花顺 K线复盘助手")]
[assembly: AssemblyCompany("Kline Review Assistant contributors")]
[assembly: AssemblyCopyright("Released under the MIT License")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace KlineReviewAssistantLauncher
{
    internal sealed class LauncherForm : Form
    {
        private readonly Label statusLabel;
        private readonly Label detailLabel;
        private readonly ProgressBar progressBar;

        internal LauncherForm()
        {
            Text = "同花顺 K线复盘助手";
            ClientSize = new Size(430, 154);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(21, 23, 28);
            ForeColor = Color.FromArgb(243, 244, 246);
            Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);

            try
            {
                Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            }
            catch
            {
                // The executable still works if Windows cannot read its icon.
            }

            var accent = new Panel
            {
                BackColor = Color.FromArgb(255, 122, 26),
                Location = new Point(20, 20),
                Size = new Size(4, 30)
            };
            Controls.Add(accent);

            var titleLabel = new Label
            {
                AutoSize = true,
                Location = new Point(36, 17),
                Text = "同花顺 K线复盘助手",
                Font = new Font("Microsoft YaHei UI", 13F, FontStyle.Bold, GraphicsUnit.Point),
                ForeColor = Color.White
            };
            Controls.Add(titleLabel);

            statusLabel = new Label
            {
                AutoSize = false,
                Location = new Point(22, 64),
                Size = new Size(386, 24),
                Text = "正在启动当前版本...",
                ForeColor = Color.FromArgb(243, 244, 246)
            };
            Controls.Add(statusLabel);

            detailLabel = new Label
            {
                AutoSize = false,
                Location = new Point(22, 89),
                Size = new Size(386, 22),
                Text = "首次运行可能需要几分钟准备本地环境",
                ForeColor = Color.FromArgb(156, 163, 175),
                Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular, GraphicsUnit.Point)
            };
            Controls.Add(detailLabel);

            progressBar = new ProgressBar
            {
                Location = new Point(22, 121),
                Size = new Size(386, 8),
                Style = ProgressBarStyle.Marquee,
                MarqueeAnimationSpeed = 28
            };
            Controls.Add(progressBar);

            Shown += async delegate { await StartCurrentVersionAsync(); };
        }

        private async Task StartCurrentVersionAsync()
        {
            var root = AppDomain.CurrentDomain.BaseDirectory;
            var startScript = Path.Combine(root, "start_recorder.bat");
            if (!File.Exists(startScript))
            {
                ShowFailure("启动文件不完整", "没有找到 start_recorder.bat。请保留 EXE 与仓库文件在同一目录。");
                return;
            }

            var localPython = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
            if (File.Exists(localPython))
            {
                statusLabel.Text = "正在启动当前源码...";
                detailLabel.Text = "更新代码后无需重新生成或安装 EXE";
            }
            else
            {
                statusLabel.Text = "首次启动：正在准备运行环境...";
                detailLabel.Text = "请保持网络连接，完成后会自动打开助手";
            }

            string output;
            string error;
            int exitCode;
            try
            {
                var startInfo = new ProcessStartInfo
                {
                    FileName = Environment.GetEnvironmentVariable("COMSPEC") ?? "cmd.exe",
                    Arguments = "/d /s /c \"call \"\"" + startScript + "\"\" --no-pause\"",
                    WorkingDirectory = root,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };

                using (var process = Process.Start(startInfo))
                {
                    if (process == null)
                    {
                        throw new InvalidOperationException("Windows 无法创建启动进程。");
                    }

                    var outputTask = process.StandardOutput.ReadToEndAsync();
                    var errorTask = process.StandardError.ReadToEndAsync();
                    await Task.Run(delegate { process.WaitForExit(); });
                    output = await outputTask;
                    error = await errorTask;
                    exitCode = process.ExitCode;
                }
            }
            catch (Exception exception)
            {
                ShowFailure("无法启动助手", exception.Message);
                return;
            }

            if (exitCode != 0)
            {
                var details = (output + Environment.NewLine + error).Trim();
                if (details.Length > 1200)
                {
                    details = details.Substring(details.Length - 1200);
                }
                ShowFailure(
                    "运行环境准备失败",
                    "请确认已安装 Python 3.11 或更高版本，然后重试。" +
                    (details.Length > 0 ? Environment.NewLine + Environment.NewLine + details : string.Empty)
                );
                return;
            }

            progressBar.Style = ProgressBarStyle.Continuous;
            progressBar.Value = 100;
            statusLabel.Text = "助手已启动";
            detailLabel.Text = "此窗口将自动关闭";
            await Task.Delay(450);
            Close();
        }

        private void ShowFailure(string title, string details)
        {
            progressBar.Style = ProgressBarStyle.Continuous;
            progressBar.Value = 0;
            statusLabel.Text = title;
            detailLabel.Text = "请查看错误提示后重试";
            MessageBox.Show(this, details, title, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new LauncherForm());
        }
    }
}
