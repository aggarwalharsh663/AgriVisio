using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

namespace AgriScanSetup
{
    public class SetupForm : Form
    {
        private ProgressBar progressBar;
        private Label lblStatus;
        private Label lblTitle;
        private Label lblDesc;
        private Button btnInstall;
        private CheckBox chkDesktopShortcut;
        private CheckBox chkLaunchNow;
        private TextBox txtInstallPath;

        public SetupForm()
        {
            InitializeComponent();
        }

        private void InitializeComponent()
        {
            this.Text = "AgriScan Setup - Precision Optical Grain Assessment";
            this.Size = new Size(580, 420);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = true;
            this.BackColor = Color.FromArgb(13, 15, 20);
            this.ForeColor = Color.FromArgb(248, 250, 252);

            try
            {
                string iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_icon.ico");
                if (File.Exists(iconPath))
                {
                    this.Icon = new Icon(iconPath);
                }
            }
            catch { }

            lblTitle = new Label();
            lblTitle.Text = "AgriScan Setup";
            lblTitle.Font = new Font("Segoe UI", 18, FontStyle.Bold);
            lblTitle.ForeColor = Color.FromArgb(245, 158, 11);
            lblTitle.Location = new Point(30, 25);
            lblTitle.Size = new Size(500, 38);
            this.Controls.Add(lblTitle);

            lblDesc = new Label();
            lblDesc.Text = "Install AgriScan Precision Optical Grain Assessment desktop application on this computer.";
            lblDesc.Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);
            lblDesc.ForeColor = Color.FromArgb(148, 163, 184);
            lblDesc.Location = new Point(32, 68);
            lblDesc.Size = new Size(500, 35);
            this.Controls.Add(lblDesc);

            Label lblPathTag = new Label();
            lblPathTag.Text = "Installation Directory:";
            lblPathTag.Font = new Font("Segoe UI", 9, FontStyle.Bold);
            lblPathTag.ForeColor = Color.FromArgb(226, 232, 240);
            lblPathTag.Location = new Point(32, 120);
            lblPathTag.Size = new Size(200, 22);
            this.Controls.Add(lblPathTag);

            string defaultDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                "AgriScan"
            );

            txtInstallPath = new TextBox();
            txtInstallPath.Text = defaultDir;
            txtInstallPath.Font = new Font("Segoe UI", 9.5f);
            txtInstallPath.Location = new Point(32, 145);
            txtInstallPath.Size = new Size(400, 28);
            txtInstallPath.BackColor = Color.FromArgb(23, 27, 38);
            txtInstallPath.ForeColor = Color.White;
            txtInstallPath.BorderStyle = BorderStyle.FixedSingle;
            this.Controls.Add(txtInstallPath);

            Button btnBrowse = new Button();
            btnBrowse.Text = "Browse...";
            btnBrowse.Font = new Font("Segoe UI", 9);
            btnBrowse.Location = new Point(440, 144);
            btnBrowse.Size = new Size(95, 29);
            btnBrowse.FlatStyle = FlatStyle.Flat;
            btnBrowse.BackColor = Color.FromArgb(30, 41, 59);
            btnBrowse.ForeColor = Color.FromArgb(226, 232, 240);
            btnBrowse.Click += (s, e) =>
            {
                using (FolderBrowserDialog fbd = new FolderBrowserDialog())
                {
                    fbd.SelectedPath = txtInstallPath.Text;
                    if (fbd.ShowDialog() == DialogResult.OK)
                    {
                        txtInstallPath.Text = Path.Combine(fbd.SelectedPath, "AgriScan");
                    }
                }
            };
            this.Controls.Add(btnBrowse);

            chkDesktopShortcut = new CheckBox();
            chkDesktopShortcut.Text = "Create Desktop shortcut";
            chkDesktopShortcut.Font = new Font("Segoe UI", 9.5f);
            chkDesktopShortcut.ForeColor = Color.FromArgb(226, 232, 240);
            chkDesktopShortcut.Checked = true;
            chkDesktopShortcut.Location = new Point(32, 190);
            chkDesktopShortcut.Size = new Size(300, 25);
            this.Controls.Add(chkDesktopShortcut);

            chkLaunchNow = new CheckBox();
            chkLaunchNow.Text = "Launch AgriScan after installation completes";
            chkLaunchNow.Font = new Font("Segoe UI", 9.5f);
            chkLaunchNow.ForeColor = Color.FromArgb(226, 232, 240);
            chkLaunchNow.Checked = true;
            chkLaunchNow.Location = new Point(32, 220);
            chkLaunchNow.Size = new Size(400, 25);
            this.Controls.Add(chkLaunchNow);

            progressBar = new ProgressBar();
            progressBar.Location = new Point(32, 265);
            progressBar.Size = new Size(502, 18);
            progressBar.Style = ProgressBarStyle.Continuous;
            progressBar.Visible = false;
            this.Controls.Add(progressBar);

            lblStatus = new Label();
            lblStatus.Text = "Ready to install.";
            lblStatus.Font = new Font("Segoe UI", 9);
            lblStatus.ForeColor = Color.FromArgb(148, 163, 184);
            lblStatus.Location = new Point(32, 290);
            lblStatus.Size = new Size(500, 22);
            this.Controls.Add(lblStatus);

            btnInstall = new Button();
            btnInstall.Text = "Install AgriScan";
            btnInstall.Font = new Font("Segoe UI", 10.5f, FontStyle.Bold);
            btnInstall.Location = new Point(364, 325);
            btnInstall.Size = new Size(170, 40);
            btnInstall.FlatStyle = FlatStyle.Flat;
            btnInstall.BackColor = Color.FromArgb(245, 158, 11);
            btnInstall.ForeColor = Color.FromArgb(13, 15, 20);
            btnInstall.Cursor = Cursors.Hand;
            btnInstall.Click += BtnInstall_Click;
            this.Controls.Add(btnInstall);
        }

        private void KillExistingProcesses()
        {
            try
            {
                foreach (var p in Process.GetProcessesByName("AgriScan"))
                {
                    try { p.Kill(); } catch { }
                }
                foreach (var p in Process.GetProcessesByName("electron"))
                {
                    try { p.Kill(); } catch { }
                }
            }
            catch { }
        }

        private void BtnInstall_Click(object sender, EventArgs e)
        {
            string targetDir = txtInstallPath.Text.Trim();
            if (string.IsNullOrEmpty(targetDir))
            {
                MessageBox.Show("Please specify an installation path.", "Invalid Path", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            btnInstall.Enabled = false;
            txtInstallPath.Enabled = false;
            chkDesktopShortcut.Enabled = false;
            chkLaunchNow.Enabled = false;
            progressBar.Visible = true;
            progressBar.Value = 0;

            bool createShortcut = chkDesktopShortcut.Checked;
            bool launch = chkLaunchNow.Checked;

            Thread worker = new Thread(() =>
            {
                try
                {
                    this.Invoke((MethodInvoker)delegate
                    {
                        lblStatus.Text = "Closing any active AgriScan instances...";
                    });
                    KillExistingProcesses();
                    Thread.Sleep(500);

                    this.Invoke((MethodInvoker)delegate
                    {
                        lblStatus.Text = "Preparing installation directory...";
                    });
                    if (!Directory.Exists(targetDir))
                    {
                        Directory.CreateDirectory(targetDir);
                    }

                    Assembly asm = Assembly.GetExecutingAssembly();
                    using (Stream zipStream = asm.GetManifestResourceStream("agriscan_payload.zip"))
                    {
                        if (zipStream == null)
                        {
                            throw new Exception("Embedded installation payload 'agriscan_payload.zip' not found in executable.");
                        }

                        using (ZipArchive archive = new ZipArchive(zipStream, ZipArchiveMode.Read))
                        {
                            int total = archive.Entries.Count;
                            int count = 0;

                            foreach (ZipArchiveEntry entry in archive.Entries)
                            {
                                count++;
                                string fullDestPath = Path.Combine(targetDir, entry.FullName);

                                if (string.IsNullOrEmpty(entry.Name))
                                {
                                    if (!Directory.Exists(fullDestPath))
                                    {
                                        Directory.CreateDirectory(fullDestPath);
                                    }
                                }
                                else
                                {
                                    string dir = Path.GetDirectoryName(fullDestPath);
                                    if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                                    {
                                        Directory.CreateDirectory(dir);
                                    }
                                    entry.ExtractToFile(fullDestPath, true);
                                }

                                if (count % 300 == 0 || count == total)
                                {
                                    int pct = (int)((count * 100.0) / total);
                                    this.Invoke((MethodInvoker)delegate
                                    {
                                        progressBar.Value = pct;
                                        lblStatus.Text = string.Format("Extracting components ({0}%)... {1}", pct, entry.Name);
                                    });
                                }
                            }
                        }
                    }

                    if (createShortcut)
                    {
                        this.Invoke((MethodInvoker)delegate
                        {
                            lblStatus.Text = "Creating Desktop shortcut...";
                        });
                        CreateShortcuts(targetDir);
                    }

                    this.Invoke((MethodInvoker)delegate
                    {
                        progressBar.Value = 100;
                        lblStatus.Text = "Installation completed successfully!";
                        lblStatus.ForeColor = Color.FromArgb(16, 185, 129);
                    });

                    if (launch)
                    {
                        LaunchApp(targetDir);
                    }

                    this.Invoke((MethodInvoker)delegate
                    {
                        MessageBox.Show(
                            "AgriScan has been successfully installed!\n\nLocation: " + targetDir,
                            "AgriScan Installed",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Information
                        );
                        this.Close();
                    });
                }
                catch (Exception ex)
                {
                    this.Invoke((MethodInvoker)delegate
                    {
                        progressBar.Visible = false;
                        lblStatus.Text = "Installation failed: " + ex.Message;
                        lblStatus.ForeColor = Color.FromArgb(239, 68, 68);
                        btnInstall.Enabled = true;
                        MessageBox.Show(
                            "Installation encountered an error:\n\n" + ex.ToString(),
                            "Installation Error",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Error
                        );
                    });
                }
            });

            worker.IsBackground = true;
            worker.Start();
        }

        private void CreateShortcuts(string targetDir)
        {
            try
            {
                string exePath = Path.Combine(targetDir, "AgriScan.exe");
                string iconPath = Path.Combine(targetDir, "app_icon.ico");
                string desktopPath = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                string shortcutPath = Path.Combine(desktopPath, "AgriScan.lnk");

                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType != null)
                {
                    dynamic shell = Activator.CreateInstance(shellType);
                    dynamic shortcut = shell.CreateShortcut(shortcutPath);
                    shortcut.TargetPath = exePath;
                    shortcut.WorkingDirectory = targetDir;
                    shortcut.Description = "AgriScan - Precision Optical Grain Assessment";
                    if (File.Exists(iconPath))
                    {
                        shortcut.IconLocation = iconPath + ",0";
                    }
                    shortcut.Save();
                }
            }
            catch { }
        }

        private void LaunchApp(string targetDir)
        {
            try
            {
                string exePath = Path.Combine(targetDir, "AgriScan.exe");
                if (File.Exists(exePath))
                {
                    ProcessStartInfo psi = new ProcessStartInfo();
                    psi.FileName = exePath;
                    psi.WorkingDirectory = targetDir;
                    Process.Start(psi);
                }
            }
            catch { }
        }

        [STAThread]
        public static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm());
        }
    }
}
