using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("AgriVisio")]
[assembly: AssemblyDescription("AgriVisio - Precision Optical Grain Assessment")]
[assembly: AssemblyConfiguration("")]
[assembly: AssemblyCompany("AgriVisio")]
[assembly: AssemblyProduct("AgriVisio")]
[assembly: AssemblyCopyright("Copyright © 2026 AgriVisio. All rights reserved.")]
[assembly: AssemblyTrademark("")]
[assembly: AssemblyCulture("")]
[assembly: ComVisible(false)]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace AgriVisioLauncher
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
                        "AgriVisio application files are incomplete.\n\n" +
                        "Missing: " + electronExe + "\n\n" +
                        "Please verify the installation directory.",
                        "AgriVisio - Missing Component",
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
                    "Unable to launch AgriVisio:\n\n" + ex.Message,
                    "AgriVisio Launch Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }
        }
    }
}
