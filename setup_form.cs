using System;
using System.Collections.Generic;
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

[assembly: AssemblyTitle("AgriVisio Setup")]
[assembly: AssemblyDescription("AgriVisio Precision Optical Grain Assessment Installer")]
[assembly: AssemblyConfiguration("")]
[assembly: AssemblyCompany("AgriVisio")]
[assembly: AssemblyProduct("AgriVisio Precision Optical Grain Assessment")]
[assembly: AssemblyCopyright("Copyright © 2026 AgriVisio. All rights reserved.")]
[assembly: AssemblyTrademark("")]
[assembly: AssemblyCulture("")]
[assembly: ComVisible(false)]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace AgriVisioSetup
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

        // Configurable expected SHA-256 checksum for agrivisio_payload.zip
        // If empty or null, integrity verification uses embedded manifest or validates stream readable
        public static string ExpectedPayloadSha256 = "780AD0E1D27490806EEE18EF61113D313779E080D9D0A5DB0D5F0D5046E60BAF";

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
            this.Text = "AgriVisio Setup - Precision Optical Grain Assessment";
            this.Size = new Size(580, 420);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = true;
            this.BackColor = Color.FromArgb(13, 15, 20);
            this.ForeColor = Color.FromArgb(248, 250, 252);

            try
            {
                this.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            }
            catch
            {
                string iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "agrivisio_icon.ico");
                if (!File.Exists(iconPath)) iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_icon.ico");
                if (File.Exists(iconPath))
                {
                    this.Icon = new Icon(iconPath);
                }
            }

            PictureBox picLogo = new PictureBox();
            picLogo.Location = new Point(30, 20);
            picLogo.Size = new Size(56, 56);
            picLogo.SizeMode = PictureBoxSizeMode.Zoom;
            try
            {
                using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("app_emblem.png"))
                {
                    if (stream != null)
                    {
                        picLogo.Image = Image.FromStream(stream);
                    }
                }
            }
            catch { }
            if (picLogo.Image == null)
            {
                try
                {
                    string emblemPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "frontend", "assets", "emblem.png");
                    if (File.Exists(emblemPath))
                    {
                        picLogo.Image = Image.FromFile(emblemPath);
                    }
                    else if (this.Icon != null)
                    {
                        picLogo.Image = this.Icon.ToBitmap();
                    }
                }
                catch { }
            }
            this.Controls.Add(picLogo);

            lblTitle = new Label();
            lblTitle.Text = "AgriVisio Setup";
            lblTitle.Font = new Font("Segoe UI", 18, FontStyle.Bold);
            lblTitle.ForeColor = Color.FromArgb(34, 197, 94);
            lblTitle.Location = new Point(98, 20);
            lblTitle.Size = new Size(430, 32);
            this.Controls.Add(lblTitle);

            lblDesc = new Label();
            lblDesc.Text = "Install AgriVisio Precision Optical Grain Assessment desktop application on this computer.";
            lblDesc.Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);
            lblDesc.ForeColor = Color.FromArgb(148, 163, 184);
            lblDesc.Location = new Point(100, 54);
            lblDesc.Size = new Size(430, 38);
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
                "AgriVisio"
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
                        txtInstallPath.Text = Path.Combine(fbd.SelectedPath, "AgriVisio");
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
            chkLaunchNow.Text = "Launch AgriVisio after installation completes";
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
            btnInstall.Text = "Install AgriVisio";
            btnInstall.Font = new Font("Segoe UI", 10.5f, FontStyle.Bold);
            btnInstall.Location = new Point(364, 325);
            btnInstall.Size = new Size(170, 40);
            btnInstall.FlatStyle = FlatStyle.Flat;
            btnInstall.BackColor = Color.FromArgb(34, 197, 94);
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

        public static int GracefulShutdownTimeoutMs = 8000;
        public static int ForceKillTimeoutMs = 3000;

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint processAccess, bool bInheritHandle, int processId);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Auto)]
        private static extern bool QueryFullProcessImageName(IntPtr hProcess, int dwFlags, StringBuilder lpExeName, ref int lpdwSize);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr hObject);

        private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

        private static string GetProcessPath(Process p)
        {
            try
            {
                if (p.MainModule != null)
                {
                    return Path.GetFullPath(p.MainModule.FileName);
                }
            }
            catch
            {
                // Fall back to QueryFullProcessImageName for 32-bit / 64-bit cross-architecture access
            }

            try
            {
                IntPtr hProc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, p.Id);
                if (hProc != IntPtr.Zero)
                {
                    try
                    {
                        var sb = new StringBuilder(1024);
                        int size = sb.Capacity;
                        if (QueryFullProcessImageName(hProc, 0, sb, ref size))
                        {
                            return Path.GetFullPath(sb.ToString());
                        }
                    }
                    finally
                    {
                        CloseHandle(hProc);
                    }
                }
            }
            catch
            {
            }
            return null;
        }

        private static string GetProcessCommandLine(Process p)
        {
            try
            {
                using (var searcher = new System.Management.ManagementObjectSearcher(
                    string.Format("SELECT CommandLine FROM Win32_Process WHERE ProcessId = {0}", p.Id)))
                using (var collection = searcher.Get())
                {
                    foreach (var item in collection)
                    {
                        object val = item["CommandLine"];
                        if (val != null)
                        {
                            return val.ToString();
                        }
                    }
                }
            }
            catch
            {
                // Access denied or WMI unavailable
            }
            return null;
        }

        private static string[] SplitCommandLineArguments(string commandLine)
        {
            var parts = new List<string>();
            if (string.IsNullOrEmpty(commandLine))
                return parts.ToArray();

            var current = new StringBuilder();
            bool inQuotes = false;
            for (int i = 0; i < commandLine.Length; i++)
            {
                char c = commandLine[i];
                if (c == '"')
                {
                    inQuotes = !inQuotes;
                }
                else if (char.IsWhiteSpace(c) && !inQuotes)
                {
                    if (current.Length > 0)
                    {
                        parts.Add(current.ToString());
                        current.Length = 0;
                    }
                    continue;
                }
                current.Append(c);
            }
            if (current.Length > 0)
            {
                parts.Add(current.ToString());
            }
            return parts.ToArray();
        }

        private static bool IsAgriVisioPythonProcess(string cmdLine)
        {
            if (string.IsNullOrWhiteSpace(cmdLine))
                return false;

            string[] tokens = SplitCommandLineArguments(cmdLine);
            bool nextIsModule = false;
            for (int i = 1; i < tokens.Length; i++)
            {
                string token = tokens[i].Trim('"', ' ');
                if (string.IsNullOrEmpty(token))
                    continue;

                if (nextIsModule)
                {
                    if (string.Equals(token, "backend.main", StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(token, "backend.ipc_worker", StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(token, "main", StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                    nextIsModule = false;
                    continue;
                }

                if (string.Equals(token, "-m", StringComparison.OrdinalIgnoreCase))
                {
                    nextIsModule = true;
                    continue;
                }

                if (token.StartsWith("-"))
                {
                    continue;
                }

                string scriptName = Path.GetFileName(token);
                if (string.Equals(scriptName, "ipc_worker.py", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(scriptName, "main.py", StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }

                return false;
            }

            return false;
        }

        private static bool IsAgriVisioInstallation(string targetDir)
        {
            if (string.IsNullOrEmpty(targetDir) || !Directory.Exists(targetDir)) return false;
            return File.Exists(Path.Combine(targetDir, "AgriVisio.exe")) ||
                   File.Exists(Path.Combine(targetDir, "AgriVisio.bat")) ||
                   File.Exists(Path.Combine(targetDir, "backend", "ipc_worker.py")) ||
                   (File.Exists(Path.Combine(targetDir, "electron", "main.cjs")) &&
                    (File.Exists(Path.Combine(targetDir, "frontend", "index.html")) ||
                     File.Exists(Path.Combine(targetDir, "backend", "main.py"))));
        }

        /// <summary>
        /// Identifies all running processes originating from or holding modules inside target directory.
        /// Never matches or terminates unrelated system or third-party processes.
        /// </summary>
        public static List<Process> GetAgriVisioProcessesInDirectory(string targetDir)
        {
            bool unused;
            return GetAgriVisioProcessesInDirectory(targetDir, null, out unused);
        }

        public static List<Process> GetAgriVisioProcessesInDirectory(string targetDir, HashSet<int> knownPids, out bool hasUnverifiedProcesses)
        {
            hasUnverifiedProcesses = false;
            var matching = new List<Process>();
            if (string.IsNullOrEmpty(targetDir)) return matching;

            string canonicalTarget;
            try
            {
                canonicalTarget = Path.GetFullPath(targetDir).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            }
            catch
            {
                return matching;
            }

            string prefixWithSep = canonicalTarget + Path.DirectorySeparatorChar;
            bool isVerifiedAgriVisio = IsAgriVisioInstallation(canonicalTarget);
            int currentPid = Process.GetCurrentProcess().Id;

            foreach (Process p in Process.GetProcesses())
            {
                try
                {
                    if (p.Id == currentPid)
                        continue;

                    string exePath = GetProcessPath(p);

                    if (!string.IsNullOrEmpty(exePath))
                    {
                        if (exePath.StartsWith(prefixWithSep, StringComparison.OrdinalIgnoreCase) ||
                            string.Equals(exePath, canonicalTarget, StringComparison.OrdinalIgnoreCase))
                        {
                            string relPath = exePath.StartsWith(prefixWithSep, StringComparison.OrdinalIgnoreCase)
                                ? exePath.Substring(prefixWithSep.Length).Replace('/', '\\')
                                : Path.GetFileName(exePath);

                            // Only close recognized AgriVisio processes under known installation paths (CR-08 & Finding 15)
                            bool isAgriVisioProcess = false;
                            if (string.Equals(relPath, "AgriVisio.exe", StringComparison.OrdinalIgnoreCase) ||
                                string.Equals(relPath, "AgriVisio.bat", StringComparison.OrdinalIgnoreCase))
                            {
                                isAgriVisioProcess = true;
                            }
                            else if (isVerifiedAgriVisio && (
                                string.Equals(relPath, @"electron\dist\electron.exe", StringComparison.OrdinalIgnoreCase)))
                            {
                                isAgriVisioProcess = true;
                            }
                            else if (isVerifiedAgriVisio && (
                                string.Equals(relPath, @"backend\python\python.exe", StringComparison.OrdinalIgnoreCase) ||
                                string.Equals(relPath, @"backend\python\pythonw.exe", StringComparison.OrdinalIgnoreCase)))
                            {
                                if (knownPids != null && knownPids.Contains(p.Id))
                                {
                                    // Recheck liveness of already identified worker independently of WMI
                                    if (!p.HasExited)
                                    {
                                        isAgriVisioProcess = true;
                                    }
                                }
                                else
                                {
                                    string cmdLine = GetProcessCommandLine(p);
                                    if (cmdLine == null)
                                    {
                                        // WMI lookup failed or unreadable: keep distinct from confirmed non-match
                                        hasUnverifiedProcesses = true;
                                    }
                                    else if (IsAgriVisioPythonProcess(cmdLine))
                                    {
                                        isAgriVisioProcess = true;
                                    }
                                }
                            }

                            if (isAgriVisioProcess)
                            {
                                matching.Add(p);
                            }
                        }
                    }
                }
                catch
                {
                    // Process may have exited during enumeration
                }
            }

            return matching;
        }

        /// <summary>
        /// Gracefully requests any running AgriVisio processes in the target installation folder to exit.
        /// Waits up to GracefulShutdownTimeoutMs, and prompts the user if processes remain unresponsive.
        /// Returns true if all processes in targetDir are verified closed; otherwise false.
        /// </summary>
        private bool EnsureProcessesClosed(string targetDir)
        {
            string canonicalTarget = Path.GetFullPath(targetDir).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            HashSet<int> knownPids = new HashSet<int>();

            while (true)
            {
                bool hasUnverified = false;
                List<Process> procs = GetAgriVisioProcessesInDirectory(canonicalTarget, knownPids, out hasUnverified);
                foreach (Process p in procs)
                {
                    knownPids.Add(p.Id);
                }

                if (procs.Count == 0 && !hasUnverified)
                {
                    return true;
                }

                if (hasUnverified && procs.Count == 0)
                {
                    DialogResult choice = DialogResult.None;
                    this.Invoke((MethodInvoker)delegate
                    {
                        choice = MessageBox.Show(
                            string.Format(
                                "An application process running from '{0}' could not be verified automatically.\n\n" +
                                "Please close all running applications and click 'Retry', or click 'Cancel' to abort installation safely.",
                                canonicalTarget
                            ),
                            "Process Verification Required",
                            MessageBoxButtons.RetryCancel,
                            MessageBoxIcon.Warning
                        );
                    });
                    if (choice == DialogResult.Cancel)
                    {
                        return false;
                    }
                    continue;
                }

                // Phase 1: Request graceful exit
                foreach (Process p in procs)
                {
                    try
                    {
                        if (p.MainWindowHandle != IntPtr.Zero)
                        {
                            p.CloseMainWindow();
                        }
                    }
                    catch { }
                }

                // Phase 2: Wait and poll for process termination using monotonic Stopwatch
                Stopwatch sw = Stopwatch.StartNew();
                int waitIntervalMs = 250;
                bool allExited = false;

                while (sw.ElapsedMilliseconds < GracefulShutdownTimeoutMs)
                {
                    Thread.Sleep(waitIntervalMs);

                    List<Process> current = GetAgriVisioProcessesInDirectory(canonicalTarget, knownPids, out hasUnverified);
                    if (current.Count == 0 && !hasUnverified)
                    {
                        allExited = true;
                        break;
                    }
                }

                if (allExited)
                {
                    return true;
                }

                // Phase 3: Processes still running after graceful timeout
                List<Process> remaining = GetAgriVisioProcessesInDirectory(canonicalTarget, knownPids, out hasUnverified);
                if (remaining.Count == 0 && !hasUnverified)
                {
                    return true;
                }

                StringBuilder sb = new StringBuilder();
                foreach (Process p in remaining)
                {
                    sb.AppendLine(string.Format("• {0} (PID: {1})", p.ProcessName, p.Id));
                }

                DialogResult dlgChoice = DialogResult.None;
                this.Invoke((MethodInvoker)delegate
                {
                    dlgChoice = MessageBox.Show(
                        string.Format(
                            "AgriVisio is currently running and could not be closed automatically:\n\n{0}\n" +
                            "Installation files in '{1}' remain locked.\n\n" +
                            "Please save your work and close AgriVisio to continue installation.\n\n" +
                            "• Click 'Retry' once AgriVisio has been closed.\n" +
                            "• Click 'Cancel' to abort installation safely.",
                            sb.ToString(), canonicalTarget
                        ),
                        "AgriVisio Currently Running",
                        MessageBoxButtons.RetryCancel,
                        MessageBoxIcon.Warning
                    );
                });

                if (dlgChoice == DialogResult.Cancel)
                {
                    return false;
                }
                else if (dlgChoice == DialogResult.Retry)
                {
                    // Loop back and re-check process state
                    continue;
                }
            }
        }

        /// <summary>
        /// Backward-compatible wrapper for closing AgriVisio processes.
        /// </summary>
        private void CloseExistingAgriVisioProcesses(string targetDir)
        {
            EnsureProcessesClosed(targetDir);
        }

        /// <summary>
        /// Extracts a zip archive entry with retries and exponential backoff to handle transient Windows file locks.
        /// </summary>
        public static void ExtractEntryWithRetry(ZipArchiveEntry entry, string destinationPath, int maxRetries = 5, int delayMs = 250)
        {
            if (maxRetries < 1)
            {
                throw new ArgumentOutOfRangeException("maxRetries", "maxRetries must be at least 1.");
            }
            if (delayMs < 0)
            {
                throw new ArgumentOutOfRangeException("delayMs", "delayMs must be non-negative.");
            }

            bool wasReadOnly = false;
            if (File.Exists(destinationPath))
            {
                try
                {
                    var attrs = File.GetAttributes(destinationPath);
                    wasReadOnly = (attrs & FileAttributes.ReadOnly) == FileAttributes.ReadOnly;
                }
                catch { }
            }

            try
            {
                for (int attempt = 1; attempt <= maxRetries; attempt++)
                {
                    string tempPath = destinationPath + ".tmp." + Guid.NewGuid().ToString("N");
                    try
                    {
                        // 1. Extract entry to a temporary file in destination directory
                        entry.ExtractToFile(tempPath, true);

                        // 2. Clear ReadOnly attribute on destination if present
                        if (File.Exists(destinationPath))
                        {
                            var attrs = File.GetAttributes(destinationPath);
                            if ((attrs & FileAttributes.ReadOnly) == FileAttributes.ReadOnly)
                            {
                                File.SetAttributes(destinationPath, attrs & ~FileAttributes.ReadOnly);
                            }
                        }

                        // 3. Atomically replace destination only after extraction completed successfully
                        File.Copy(tempPath, destinationPath, true);

                        // 4. Restore read-only attribute if applicable
                        if (wasReadOnly)
                        {
                            File.SetAttributes(destinationPath, File.GetAttributes(destinationPath) | FileAttributes.ReadOnly);
                        }
                        return;
                    }
                    catch (UnauthorizedAccessException uex)
                    {
                        if (attempt == maxRetries)
                        {
                            throw new IOException(string.Format(
                                "Cannot write to file '{0}' due to access permissions or read-only attribute. Details: {1}",
                                Path.GetFileName(destinationPath),
                                uex.Message
                            ), uex);
                        }
                        int sleepDuration = (int)Math.Min((long)delayMs * attempt, (long)int.MaxValue);
                        Thread.Sleep(sleepDuration);
                    }
                    catch (IOException ex)
                    {
                        int win32ErrorCode = System.Runtime.InteropServices.Marshal.GetHRForException(ex) & 0xFFFF;
                        if (win32ErrorCode != 32 && win32ErrorCode != 33)
                        {
                            throw;
                        }

                        if (attempt == maxRetries)
                        {
                            throw new IOException(string.Format(
                                "Cannot access file '{0}' because it is in use by another process. Please ensure all AgriVisio instances are closed and antivirus is not locking the file.\n\nDetails: {1}",
                                Path.GetFileName(destinationPath),
                                ex.Message
                            ), ex);
                        }
                        int sleepDuration = (int)Math.Min((long)delayMs * attempt, (long)int.MaxValue);
                        Thread.Sleep(sleepDuration);
                    }
                    finally
                    {
                        if (File.Exists(tempPath))
                        {
                            try { File.Delete(tempPath); } catch { }
                        }
                    }
                }
        }
        finally
        {
            if (wasReadOnly && File.Exists(destinationPath))
            {
                try
                {
                    File.SetAttributes(destinationPath, File.GetAttributes(destinationPath) | FileAttributes.ReadOnly);
                }
                catch { }
            }
        }
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
                MessageBox.Show("Cannot install directly to the root of drive " + root + ". Please choose a subfolder (e.g. " + Path.Combine(root, "AgriVisio") + ").", "Invalid Installation Path", MessageBoxButtons.OK, MessageBoxIcon.Warning);
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
                        lblStatus.Text = "Checking for existing AgriVisio instances...";
                    });

                    if (!EnsureProcessesClosed(targetDir))
                    {
                        this.Invoke((MethodInvoker)delegate
                        {
                            lblStatus.Text = "Installation canceled: existing processes could not be closed.";
                            lblStatus.ForeColor = Color.FromArgb(239, 68, 68);
                            btnInstall.Enabled = true;
                            txtInstallPath.Enabled = true;
                            chkDesktopShortcut.Enabled = true;
                            chkLaunchNow.Enabled = true;
                            progressBar.Visible = false;
                        });
                        return;
                    }
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
                    using (Stream zipStream = asm.GetManifestResourceStream("agrivisio_payload.zip"))
                    {
                        if (zipStream == null)
                        {
                            throw new FileNotFoundException("Embedded installation payload 'agrivisio_payload.zip' not found in executable.");
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
                            // Re-check for running AgriVisio processes immediately before the first file write
                            if (!EnsureProcessesClosed(targetDir))
                            {
                                this.Invoke((MethodInvoker)delegate
                                {
                                    lblStatus.Text = "Installation canceled: existing processes could not be closed.";
                                    lblStatus.ForeColor = Color.FromArgb(239, 68, 68);
                                    btnInstall.Enabled = true;
                                    txtInstallPath.Enabled = true;
                                    chkDesktopShortcut.Enabled = true;
                                    chkLaunchNow.Enabled = true;
                                    progressBar.Visible = false;
                                });
                                return;
                            }

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

                                    ExtractEntryWithRetry(entry, fullDestPath);
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
                        string successMsg = "AgriVisio has been successfully installed!\n\nLocation: " + targetDir;
                        if (!string.IsNullOrEmpty(shortcutWarning))
                        {
                            successMsg += "\n\nNote: " + shortcutWarning;
                        }
                        MessageBox.Show(
                            successMsg,
                            "AgriVisio Installed",
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
                    else if (ex is IOException)
                    {
                        IOException ioEx = (IOException)ex;
                        int win32ErrorCode = System.Runtime.InteropServices.Marshal.GetHRForException(ioEx) & 0xFFFF;
                        if (ioEx.InnerException is IOException)
                        {
                            int innerCode = System.Runtime.InteropServices.Marshal.GetHRForException((IOException)ioEx.InnerException) & 0xFFFF;
                            if (innerCode == 32 || innerCode == 33 || innerCode == 112)
                            {
                                win32ErrorCode = innerCode;
                            }
                        }

                        if (win32ErrorCode == 32 || win32ErrorCode == 33)
                        {
                            errDetail = "File access or locking error during extraction:\n\n" + ex.Message + "\n\nPlease ensure AgriVisio and all background services are completely closed, and retry.";
                        }
                        else if (win32ErrorCode == 112)
                        {
                            errDetail = "Insufficient disk space on target drive during extraction:\n\n" + ex.Message + "\n\nPlease free up disk space on the installation drive and retry.";
                        }
                        else if (ex.InnerException is UnauthorizedAccessException)
                        {
                            errDetail = "Access denied while writing files to:\n" + targetDir + "\n\nThis may be caused by Windows Defender or insufficient permissions. Try running as administrator or selecting a folder inside your user profile.";
                        }
                        else
                        {
                            errDetail = "I/O error during extraction:\n\n" + ex.Message;
                        }
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
                string exePath = Path.Combine(targetDir, "AgriVisio.exe");
                string electronExe = Path.Combine(targetDir, "electron", "dist", "electron.exe");
                string mainScript = Path.Combine(targetDir, "electron", "main.cjs");
                string iconPath = File.Exists(Path.Combine(targetDir, "agrivisio_icon.ico")) ? Path.Combine(targetDir, "agrivisio_icon.ico") : Path.Combine(targetDir, "app_icon.ico");
                string desktopPath = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                string shortcutPath = Path.Combine(desktopPath, "AgriVisio.lnk");

                // Native Win32 Shell Link creation via IShellLink COM interface
                // Avoids Windows Script Host and dynamic script execution (Issue 3 / ASR rules)
                IShellLinkW link = (IShellLinkW)new ShellLink();
                link.SetDescription("AgriVisio - Precision Optical Grain Assessment");
                if (File.Exists(electronExe) && File.Exists(mainScript))
                {
                    link.SetPath(electronExe);
                    link.SetArguments("\"" + mainScript + "\"");
                }
                else
                {
                    link.SetPath(exePath);
                }
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
                shortcutWarning = "Desktop shortcut creation was blocked (likely by Windows Controlled Folder Access / Ransomware Protection).\nAgriVisio can be launched directly from:\n" + targetDir;
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

                    string testSub = Path.Combine(parent, "agrivisio_perm_probe_" + Guid.NewGuid().ToString("N"));
                    Directory.CreateDirectory(testSub);
                    Directory.Delete(testSub);
                    return true;
                }

                string testFile = Path.Combine(targetDir, "agrivisio_perm_probe_" + Guid.NewGuid().ToString("N") + ".tmp");
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
                string electronExe = Path.Combine(targetDir, "electron", "dist", "electron.exe");
                string mainScript = Path.Combine(targetDir, "electron", "main.cjs");
                string exePath = Path.Combine(targetDir, "AgriVisio.exe");

                if (File.Exists(electronExe) && File.Exists(mainScript))
                {
                    ProcessStartInfo psi = new ProcessStartInfo();
                    psi.FileName = electronExe;
                    psi.Arguments = "\"" + mainScript + "\"";
                    psi.WorkingDirectory = targetDir;
                    Process.Start(psi);
                }
                else if (File.Exists(exePath))
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
#if TEST_HARNESS
            if (args != null && args.Length > 0)
            {
                if (args[0] == "--test-extract-retry")
                {
                    if (args.Length < 6)
                    {
                        Console.Error.WriteLine("Usage: --test-extract-retry <zipPath> <entryName> <destPath> <maxRetries> <delayMs>");
                        Environment.Exit(1);
                        return;
                    }
                    try
                    {
                        string zipPath = args[1];
                        string entryName = args[2];
                        string destPath = args[3];
                        int maxRetries = int.Parse(args[4]);
                        int delayMs = int.Parse(args[5]);
                        using (ZipArchive zip = ZipFile.OpenRead(zipPath))
                        {
                            ZipArchiveEntry entry = zip.GetEntry(entryName);
                            if (entry == null)
                            {
                                Console.Error.WriteLine("Entry not found: " + entryName);
                                Environment.Exit(2);
                            }
                            ExtractEntryWithRetry(entry, destPath, maxRetries, delayMs);
                        }
                        Environment.Exit(0);
                    }
                    catch (Exception ex)
                    {
                        Console.Error.WriteLine("ExtractEntryWithRetry failed: " + ex.Message);
                        Environment.Exit(1);
                    }
                    return;
                }
                if (args[0] == "--test-get-procs")
                {
                    if (args.Length < 2)
                    {
                        Console.Error.WriteLine("Usage: --test-get-procs <targetDir>");
                        Environment.Exit(1);
                        return;
                    }
                    string targetDir = args[1];
                    List<Process> procs = GetAgriVisioProcessesInDirectory(targetDir);
                    foreach (Process p in procs)
                    {
                        try
                        {
                            Console.WriteLine(string.Format("{0}:{1}", p.Id, p.ProcessName));
                        }
                        catch { }
                    }
                    Environment.Exit(0);
                    return;
                }
            }
#endif

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
