using System;
using System.IO;
using System.Diagnostics;

class STLauncher {
    [STAThread]
    static void Main() {
        string dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
        string main = Path.Combine(dir, "main.py");
        string pythonw = FindPythonW();

        if (pythonw == null || !File.Exists(main)) return;

        var psi = new ProcessStartInfo(pythonw, "\"" + main + "\"");
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        Process.Start(psi);
    }

    static string FindPythonW() {
        // 1. Check sibling python_path.txt written by the app at runtime
        string dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
        string cfg = Path.Combine(dir, "python_path.txt");
        if (File.Exists(cfg)) {
            string p = File.ReadAllText(cfg).Trim();
            if (File.Exists(p)) return p;
        }

        // 2. Scan common install locations
        string user = Environment.UserName;
        string[] vers = { "313", "312", "311", "310", "39" };
        string[] bases = {
            @"C:\Program Files\Python",
            @"C:\Program Files (x86)\Python",
            @"C:\Users\" + user + @"\AppData\Local\Programs\Python\Python",
        };
        foreach (string ver in vers)
            foreach (string b in bases) {
                string candidate = b + ver + @"\pythonw.exe";
                if (File.Exists(candidate)) return candidate;
            }

        // 3. where.exe fallback
        try {
            var wp = Process.Start(new ProcessStartInfo("where", "pythonw.exe") {
                RedirectStandardOutput = true, UseShellExecute = false, CreateNoWindow = true
            });
            string line = wp.StandardOutput.ReadLine();
            if (line != null && File.Exists(line.Trim())) return line.Trim();
        } catch {}

        return null;
    }
}
