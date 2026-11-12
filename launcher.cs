using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("AgriScan")]
[assembly: AssemblyDescription("AgriScan - Precision Optical Grain Assessment")]
[assembly: AssemblyConfiguration("")]
[assembly: AssemblyCompany("AgriScan")]
[assembly: AssemblyProduct("AgriScan")]
[assembly: AssemblyCopyright("Copyright © 2026 AgriScan. All rights reserved.")]
[assembly: AssemblyTrademark("")]
[assembly: AssemblyCulture("")]
[assembly: ComVisible(false)]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace AgriScanLauncher
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            try
            {
                string appDir = AppDomain.CurrentDomain.BaseDirectory;
                string electronDir = Path.Combine(appDir, "electron");
                string electronExe = Path.Combine(electronDir, "dist", "electron.exe");
                string mainScript = Path.Combine(electronDir, "main.cjs");

                if (!File.Exists(electronExe))
                {
                    MessageBox.Show(
                        "AgriScan application files are incomplete.\n\n" +
                        "Missing: " + electronExe + "\n\n" +
                        "Please verify the installation directory.",
                        "AgriScan - Missing Component",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Warning
                    );
                    return;
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = electronExe;
                psi.Arguments = "\"" + mainScript + "\"";
                psi.WorkingDirectory = appDir;
                psi.WindowStyle = ProcessWindowStyle.Normal;
                psi.CreateNoWindow = false;
                psi.UseShellExecute = false;

                Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to launch AgriScan:\n\n" + ex.Message,
                    "AgriScan Launch Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }
        }
    }
}
