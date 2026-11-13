using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("AgriScan Setup")]
[assembly: AssemblyDescription("AgriScan Precision Optical Grain Assessment Installer")]
[assembly: AssemblyConfiguration("")]
[assembly: AssemblyCompany("AgriScan")]
[assembly: AssemblyProduct("AgriScan Precision Optical Grain Assessment")]
[assembly: AssemblyCopyright("Copyright © 2026 AgriScan. All rights reserved.")]
[assembly: AssemblyTrademark("")]
[assembly: AssemblyCulture("")]
[assembly: ComVisible(false)]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

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
        public static string ExpectedPayloadSha256 = "977E249CD36AAD287006745D1070AA47D344B96CFC1C9FFD0C471E881C7359D4";

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

        public SetupForm(string initialPath = null)
        {
            InitializeComponent();
            if (!string.IsNullOrWhiteSpace(initialPath))
            {
                txtInstallPath.Text = initialPath.Trim('"', ' ');
            }
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
        /// Gracefully requests any currently open AgriScan application instances in target directory to close.
        /// Does not perform forceful process termination.
        /// </summary>
        private void CloseExistingAgriScanProcesses(string targetDir)
        {
            try
            {
                string canonicalTarget = Path.GetFullPath(targetDir).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);

                // Gracefully request AgriScan processes to close their main window
                foreach (var p in Process.GetProcessesByName("AgriScan"))
                {
                    try
                    {
                        if (p.MainModule != null)
                        {
                            string exePath = Path.GetFullPath(p.MainModule.FileName);
                            if (exePath.StartsWith(canonicalTarget, StringComparison.OrdinalIgnoreCase))
                            {
                                p.CloseMainWindow();
                                p.WaitForExit(1500);
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
            string rawPath = txtInstallPath.Text.Trim().Trim('"', ' ', '\t');
            if (string.IsNullOrEmpty(rawPath))
            {
                MessageBox.Show("Please specify an installation path.", "Invalid Path", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            string targetDir;
            try
            {
                targetDir = Path.GetFullPath(rawPath);
            }
            catch (Exception ex)
            {
                MessageBox.Show("The specified installation path is invalid:\n\n" + ex.Message, "Invalid Path", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            // Path Safety Check: Prevent installing directly to drive roots or Windows system folders
            string root = Path.GetPathRoot(targetDir);
            if (string.Equals(targetDir.TrimEnd('\\', '/'), root.TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase))
            {
                MessageBox.Show("Cannot install directly to the root of drive " + root + ". Please choose a subfolder (e.g. " + Path.Combine(root, "AgriScan") + ").", "Invalid Installation Path", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            string winDir = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            if (!string.IsNullOrEmpty(winDir) && targetDir.StartsWith(winDir, StringComparison.OrdinalIgnoreCase))
            {
                MessageBox.Show("Installation inside the Windows directory is strictly prohibited.", "Protected System Directory", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            // Pre-flight Write Permission & Elevation Check
            if (!CanWriteToDirectory(targetDir))
            {
                if (!IsAdministrator())
                {
                    DialogResult elevateChoice = MessageBox.Show(
                        "The selected directory requires administrator privileges:\n\n" + targetDir + "\n\nWould you like to restart the installer with administrator permissions to continue?",
                        "Administrator Privileges Required",
                        MessageBoxButtons.YesNo,
                        MessageBoxIcon.Question
                    );
                    if (elevateChoice == DialogResult.Yes)
                    {
                        RelaunchAsAdmin(targetDir);
                        return;
                    }
                    else
                    {
                        return;
                    }
                }
                else
                {
                    MessageBox.Show(
                        "Cannot write to the target directory:\n\n" + targetDir + "\n\nAccess was denied. Please verify folder permissions or check if Windows Controlled Folder Access (Ransomware Protection) is blocking writes.",
                        "Write Permission Denied",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return;
                }
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

                    string shortcutWarning = null;
                    if (createShortcut)
                    {
                        this.Invoke((MethodInvoker)delegate
                        {
                            lblStatus.Text = "Creating Desktop shortcut...";
                        });
                        CreateShortcuts(targetDir, out shortcutWarning);
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
                        string successMsg = "AgriScan has been successfully installed!\n\nLocation: " + targetDir;
                        if (!string.IsNullOrEmpty(shortcutWarning))
                        {
                            successMsg += "\n\nNote: " + shortcutWarning;
                        }
                        MessageBox.Show(
                            successMsg,
                            "AgriScan Installed",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Information
                        );
                        this.Close();
                    });
                }
                catch (Exception ex)
                {
                    string errDetail = ex.Message;
                    if (ex is UnauthorizedAccessException)
                    {
                        errDetail = "Access denied while writing files to:\n" + targetDir + "\n\nThis may be caused by Windows Defender Controlled Folder Access (Ransomware Protection) or insufficient permissions. Try running as administrator or selecting a folder inside your user profile.";
                    }
                    this.Invoke((MethodInvoker)delegate
                    {
                        progressBar.Visible = false;
                        lblStatus.Text = "Installation failed: " + ex.Message;
                        lblStatus.ForeColor = Color.FromArgb(239, 68, 68);
                        btnInstall.Enabled = true;
                        txtInstallPath.Enabled = true;
                        chkDesktopShortcut.Enabled = true;
                        chkLaunchNow.Enabled = true;
                        MessageBox.Show(
                            "Installation failed:\n\n" + errDetail,
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

        private bool CreateShortcuts(string targetDir, out string shortcutWarning)
        {
            shortcutWarning = null;
            try
            {
                string exePath = Path.Combine(targetDir, "AgriScan.exe");
                string iconPath = Path.Combine(targetDir, "app_icon.ico");
                string desktopPath = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                string shortcutPath = Path.Combine(desktopPath, "AgriScan.lnk");

                // Native Win32 Shell Link creation via IShellLink COM interface
                // Avoids Windows Script Host and dynamic script execution (Issue 3 / ASR rules)
                IShellLinkW link = (IShellLinkW)new ShellLink();
                link.SetDescription("AgriScan - Precision Optical Grain Assessment");
                link.SetPath(exePath);
                link.SetWorkingDirectory(targetDir);
                if (File.Exists(iconPath))
                {
                    link.SetIconLocation(iconPath, 0);
                }
                IPersistFile file = (IPersistFile)link;
                file.Save(shortcutPath, false);
                return true;
            }
            catch (UnauthorizedAccessException)
            {
                shortcutWarning = "Desktop shortcut creation was blocked (likely by Windows Controlled Folder Access / Ransomware Protection).\nAgriScan can be launched directly from:\n" + targetDir;
                return false;
            }
            catch (Exception ex)
            {
                shortcutWarning = "Shortcut creation failed: " + ex.Message;
                return false;
            }
        }

        public static bool IsAdministrator()
        {
            try
            {
                using (var identity = System.Security.Principal.WindowsIdentity.GetCurrent())
                {
                    var principal = new System.Security.Principal.WindowsPrincipal(identity);
                    return principal.IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator);
                }
            }
            catch
            {
                return false;
            }
        }

        public static bool CanWriteToDirectory(string targetDir)
        {
            try
            {
                if (!Directory.Exists(targetDir))
                {
                    string parent = Path.GetDirectoryName(targetDir.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
                    while (!string.IsNullOrEmpty(parent) && !Directory.Exists(parent))
                    {
                        parent = Path.GetDirectoryName(parent);
                    }
                    if (string.IsNullOrEmpty(parent) || !Directory.Exists(parent))
                        return false;

                    string testSub = Path.Combine(parent, "agriscan_perm_probe_" + Guid.NewGuid().ToString("N"));
                    Directory.CreateDirectory(testSub);
                    Directory.Delete(testSub);
                    return true;
                }

                string testFile = Path.Combine(targetDir, "agriscan_perm_probe_" + Guid.NewGuid().ToString("N") + ".tmp");
                File.WriteAllText(testFile, "probe");
                File.Delete(testFile);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static void RelaunchAsAdmin(string targetDir)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = Application.ExecutablePath;
                psi.Arguments = "\"" + targetDir + "\"";
                psi.Verb = "runas";
                psi.UseShellExecute = true;
                Process.Start(psi);
                Application.Exit();
            }
            catch (Exception ex)
            {
                MessageBox.Show("Unable to request administrator elevation:\n" + ex.Message, "Elevation Error", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
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
        public static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string initialPath = (args != null && args.Length > 0 && !string.IsNullOrWhiteSpace(args[0])) ? args[0].Trim('"', ' ') : null;
            Application.Run(new SetupForm(initialPath));
        }
    }

    [ComImport]
    [Guid("00021401-0000-0000-C000-000000000046")]
    internal class ShellLink { }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("000214F9-0000-0000-C000-000000000046")]
    internal interface IShellLinkW
    {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cchMaxPath, out IntPtr pfd, uint fFlags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cchMaxName);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cchMaxPath);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cchMaxPath);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        void GetHotkey(out short pwHotkey);
        void SetHotkey(short wHotkey);
        void GetShowCmd(out int piShowCmd);
        void SetShowCmd(int iShowCmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cchIconPath, out int piIcon);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
        void Resolve(IntPtr hwnd, uint fFlags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("0000010b-0000-0000-C000-000000000046")]
    internal interface IPersistFile
    {
        void GetClassID(out Guid pClassID);
        void IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        void GetCurFile([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder ppszFileName);
    }
}
