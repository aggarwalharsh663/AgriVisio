using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security;
using System.Security.Cryptography;
using System.Text;
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

        // Configurable expected SHA-256 checksum for agriscan_payload.zip
        // If empty or null, integrity verification uses embedded manifest or validates stream readable
        public static string ExpectedPayloadSha256 = "D27EFCE32AF202472AA455781C53AB0EC8C8B7959913DB046399380BF5BF63B4";

                /// <summary>
        /// Constant-time comparison of two hex hash strings to prevent timing side-channels (S-03).
        /// Accumulates differences across all characters without early exit.
        /// </summary>
        public static bool FixedTimeHexEquals(string a, string b)
        {
            if (a == null || b == null || a.Length != b.Length)
                return false;
            int diff = 0;
            for (int i = 0; i < a.Length; i++)
            {
                diff |= char.ToUpperInvariant(a[i]) ^ char.ToUpperInvariant(b[i]);
            }
            return diff == 0;
        }

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

        /// <summary>
        /// Validates a zip entry to prevent Zip Slip (F-02).
        /// Rejects traversal sequences, absolute paths, drive letters, and entries escaping targetDir.
        /// </summary>
        public static bool IsSafeZipEntry(string entryFullName, string targetDir, out string canonicalDestPath)
        {
            canonicalDestPath = null;
            if (string.IsNullOrEmpty(entryFullName)) return false;
            if (entryFullName.IndexOf('\0') >= 0) return false;

            // Reject explicit directory traversal sequences
            if (entryFullName.Contains("..") || entryFullName.StartsWith("/") || entryFullName.StartsWith("\\"))
            {
                return false;
            }

            // Reject rooted paths, drive letters, and UNC network paths
            if (Path.IsPathRooted(entryFullName) || entryFullName.Contains(":"))
            {
                return false;
            }

            string normalizedEntry = entryFullName.Replace(Path.AltDirectorySeparatorChar, Path.DirectorySeparatorChar);

            string canonicalRoot = Path.GetFullPath(targetDir);
            if (!canonicalRoot.EndsWith(Path.DirectorySeparatorChar.ToString()))
            {
                canonicalRoot += Path.DirectorySeparatorChar;
            }

            string combinedPath = Path.Combine(canonicalRoot, normalizedEntry);
            string fullDest = Path.GetFullPath(combinedPath);

            // Ensure the destination strictly resides within the target directory boundary
            if (!fullDest.StartsWith(canonicalRoot, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            canonicalDestPath = fullDest;
            return true;
        }

        /// <summary>
        /// Safely terminates only running AgriScan instances located in the target directory.
        /// NEVER terminates unrelated system Electron applications.
        /// </summary>
        private void CloseExistingAgriScanProcesses(string targetDir)
        {
            try
            {
                string canonicalTarget = Path.GetFullPath(targetDir).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);

                // Terminate AgriScan launcher/app processes
                foreach (var p in Process.GetProcessesByName("AgriScan"))
                {
                    try
                    {
                        try
                        {
                            if (p.MainModule != null)
                            {
                                string exePath = Path.GetFullPath(p.MainModule.FileName);
                                if (exePath.StartsWith(canonicalTarget, StringComparison.OrdinalIgnoreCase))
                                {
                                    p.CloseMainWindow();
                                    if (!p.WaitForExit(1000)) { p.Kill(); }
                                }
                            }
                        }
                        catch
                        {
                            p.CloseMainWindow();
                        }
                    }
                    catch { }
                }

                // Check Electron processes: only terminate if their executable is inside the AgriScan target directory!
                foreach (var p in Process.GetProcessesByName("electron"))
                {
                    try
                    {
                        if (p.MainModule != null)
                        {
                            string exePath = Path.GetFullPath(p.MainModule.FileName);
                            if (exePath.StartsWith(canonicalTarget, StringComparison.OrdinalIgnoreCase))
                            {
                                p.CloseMainWindow();
                                if (!p.WaitForExit(1000)) { p.Kill(); }
                            }
                        }
                    }
                    catch { }
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
                        lblStatus.Text = "Checking for existing AgriScan instances...";
                    });
                    CloseExistingAgriScanProcesses(targetDir);
                    Thread.Sleep(300);

                    this.Invoke((MethodInvoker)delegate
                    {
                        lblStatus.Text = "Preparing installation directory...";
                    });
                    if (!Directory.Exists(targetDir))
                    {
                        Directory.CreateDirectory(targetDir);
                    }

                    // Check for reparse point (junction/symlink) at the root
                    DirectoryInfo targetInfo = new DirectoryInfo(targetDir);
                    if ((targetInfo.Attributes & FileAttributes.ReparsePoint) != 0)
                    {
                        throw new SecurityException("The selected installation directory is a junction or symbolic link. Installation aborted.");
                    }

                    Assembly asm = Assembly.GetExecutingAssembly();
                    using (Stream zipStream = asm.GetManifestResourceStream("agriscan_payload.zip"))
                    {
                        if (zipStream == null)
                        {
                            throw new FileNotFoundException("Embedded installation payload 'agriscan_payload.zip' not found in executable.");
                        }

                        this.Invoke((MethodInvoker)delegate
                        {
                            lblStatus.Text = "Verifying package integrity (SHA-256)...";
                        });

                        byte[] payloadBytes = new byte[zipStream.Length];
                        int bytesRead = 0;
                        while (bytesRead < payloadBytes.Length)
                        {
                            int read = zipStream.Read(payloadBytes, bytesRead, payloadBytes.Length - bytesRead);
                            if (read <= 0) break;
                            bytesRead += read;
                        }

                        // Compute SHA-256 of payload
                        string computedHash = "";
                        using (SHA256 sha = SHA256.Create())
                        {
                            byte[] hashBytes = sha.ComputeHash(payloadBytes);
                            StringBuilder sb = new StringBuilder();
                            for (int i = 0; i < hashBytes.Length; i++)
                            {
                                sb.Append(hashBytes[i].ToString("X2"));
                            }
                            computedHash = sb.ToString();
                        }

                        // Fail-closed payload integrity verification (R-05)
                        if (string.IsNullOrWhiteSpace(ExpectedPayloadSha256) ||
                            ExpectedPayloadSha256.Trim().Length != 64 ||
                            !System.Text.RegularExpressions.Regex.IsMatch(ExpectedPayloadSha256.Trim(), "^[0-9a-fA-F]{64}$"))
                        {
                            throw new SecurityException(
                                "Installer integrity verification failed: Expected payload SHA-256 hash is unconfigured or invalid. Installation cannot proceed safely."
                            );
                        }

                        if (!FixedTimeHexEquals(computedHash, ExpectedPayloadSha256.Trim()))
                        {
                            throw new SecurityException(string.Format(
                                "Security Alert: Payload integrity mismatch detected!\nExpected SHA-256: {0}\nComputed SHA-256: {1}\nThe installer package may be corrupted or tampered with. Installation aborted.",
                                ExpectedPayloadSha256.Trim(), computedHash
                            ));
                        }

                        using (MemoryStream ms = new MemoryStream(payloadBytes))
                        using (ZipArchive archive = new ZipArchive(ms, ZipArchiveMode.Read))
                        {
                            int total = archive.Entries.Count;
                            int count = 0;

                            foreach (ZipArchiveEntry entry in archive.Entries)
                            {
                                count++;

                                // Validate zip entry against Zip Slip (F-02)
                                string fullDestPath;
                                if (!IsSafeZipEntry(entry.FullName, targetDir, out fullDestPath))
                                {
                                    throw new SecurityException(string.Format(
                                        "Security Alert: Malicious or invalid entry path detected in package: '{0}'. Extraction aborted.",
                                        entry.FullName
                                    ));
                                }

                                if (string.IsNullOrEmpty(entry.Name))
                                {
                                    // Directory entry
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

                                    // Verify no reparse points in destination directory
                                    DirectoryInfo dirInfo = new DirectoryInfo(dir);
                                    if ((dirInfo.Attributes & FileAttributes.ReparsePoint) != 0)
                                    {
                                        throw new SecurityException(string.Format(
                                            "Reparse point detected in target folder '{0}'. Extraction aborted.",
                                            dir
                                        ));
                                    }

                                    entry.ExtractToFile(fullDestPath, true);
                                }

                                if (count % 100 == 0 || count == total)
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
                            "Installation failed:\n\n" + ex.Message,
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
