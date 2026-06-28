import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# When running as a compiled binary (PyInstaller or Nuitka), use %AppData% so
# user data lands in a writable location.  In dev mode use the local data/ dir.
# NOTE: Nuitka does NOT set sys.frozen — detect it via proc_monitor.exe sibling.
def _is_compiled() -> bool:
    if getattr(sys, 'frozen', False):          # PyInstaller
        return True
    # Nuitka: proc_monitor.exe is compiled alongside ST.exe
    return os.path.exists(
        os.path.join(os.path.dirname(sys.executable), 'proc_monitor.exe')
    )

if _is_compiled():
    DATA_DIR = Path(os.environ.get('APPDATA', Path.home())) / 'ST-SoftwareTool'
else:
    DATA_DIR = Path(__file__).parent.parent / "data"

DATA_FILE = DATA_DIR / "apps.json"

DEFAULT_CATEGORIES = ["General", "Games", "Tools", "Browser", "Media", "Development", "Office"]


class App:
    def __init__(self, name: str, path: str, category: str = "General",
                 pinned: bool = False, description: str = ""):
        self.name = name
        self.path = path
        self.category = category
        self.pinned = pinned
        self.description = description

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "category": self.category,
            "pinned": self.pinned,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "App":
        return cls(
            name=d["name"],
            path=d["path"],
            category=d.get("category", "General"),
            pinned=d.get("pinned", False),
            description=d.get("description", ""),
        )


class AppManager:
    def __init__(self):
        self.apps: List[App] = []
        self.categories: List[str] = list(DEFAULT_CATEGORIES)
        self.settings: dict = {"theme": "win95", "default_view": "terminal"}
        self.load()

    def load(self):
        if DATA_FILE.exists():
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                apps = []
                for a in data.get("apps", []):
                    try:
                        apps.append(App.from_dict(a))
                    except (KeyError, TypeError):
                        pass
                self.apps = apps
                self.categories = data.get("categories", list(DEFAULT_CATEGORIES))
                self.settings = data.get("settings", self.settings)
            except (json.JSONDecodeError, OSError):
                pass

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "apps": [a.to_dict() for a in self.apps],
                    "categories": self.categories,
                    "settings": self.settings,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

    def _find_app(self, name: str) -> Optional[App]:
        name_lower = name.lower()
        for app in self.apps:
            if app.name.lower() == name_lower:
                return app
        return None

    def add_app(self, name: str, path: str, category: str = "General",
                description: str = "") -> Tuple[bool, str]:
        if self._find_app(name):
            return False, f"App '{name}' already exists."
        if not os.path.exists(path):
            return False, f"Path does not exist: {path}"
        if category not in self.categories:
            self.categories.append(category)
        self.apps.append(App(name, path, category, description=description))
        self.save()
        return True, f"Added '{name}' to '{category}'."

    def remove_app(self, name: str) -> Tuple[bool, str]:
        app = self._find_app(name)
        if not app:
            return False, f"App '{name}' not found."
        self.apps.remove(app)
        self.save()
        return True, f"Removed '{name}'."

    # Well-known Windows system apps that don't appear in the registry
    _SYSTEM_APPS = {
        "settings":          "ms-settings:",
        "windows settings":  "ms-settings:",
        "store":             "ms-windows-store:",
        "calculator":        "calc.exe",
        "notepad":           "notepad.exe",
        "paint":             "mspaint.exe",
        "wordpad":           "wordpad.exe",
        "task manager":      "taskmgr.exe",
        "taskmanager":       "taskmgr.exe",
        "control panel":     "control.exe",
        "control":           "control.exe",
        "regedit":           "regedit.exe",
        "registry editor":   "regedit.exe",
        "cmd":               "cmd.exe",
        "command prompt":    "cmd.exe",
        "powershell":        "powershell.exe",
        "explorer":          "explorer.exe",
        "snipping tool":     "snippingtool.exe",
        "snip":              "snippingtool.exe",
        "camera":            "microsoft.windows.camera:",
        "maps":              "bingmaps:",
        "mail":              "outlookmail:",
        "photos":            "ms-photos:",
        "clock":             "ms-clock:",
        "weather":           "bingweather:",
    }

    def launch_app(self, name: str) -> Tuple[bool, str]:
        app = self._find_app(name)
        if not app:
            return self._launch_by_name(name)
        if not os.path.exists(app.path):
            return False, f"Executable not found: {app.path}"
        try:
            os.startfile(app.path)
            return True, f"Launched '{name}'."
        except Exception:
            try:
                subprocess.Popen([app.path],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
                return True, f"Launched '{name}'."
            except Exception as e2:
                return False, f"Failed to launch '{name}': {e2}"

    def _launch_by_name(self, name: str) -> Tuple[bool, str]:
        import shutil
        key = name.lower().strip()

        # 1. Known Windows system app map (URIs + short exe names)
        target = self._SYSTEM_APPS.get(key)
        if target:
            try:
                os.startfile(target)
                return True, f"Launched '{name}'."
            except Exception:
                pass

        # 2. PATH lookup (covers notepad.exe, calc.exe, etc.)
        found = shutil.which(name) or shutil.which(name + ".exe")
        if found:
            try:
                subprocess.Popen([found], creationflags=0x00000008)  # DETACHED_PROCESS
                return True, f"Launched '{name}'."
            except Exception:
                pass

        # 3. ShellExecute with just the name — Windows resolves system apps
        try:
            os.startfile(name)
            return True, f"Launched '{name}'."
        except Exception:
            pass

        return (
            False,
            f"App '{name}' not found.\n"
            "  • Run  >_/sys:scan  to import installed apps, or\n"
            "  • Use  >_/from:add_app(\"C:\\path\\to\\app.exe\")  to add manually."
        )

    def pin_app(self, name: str, pin: bool = True) -> Tuple[bool, str]:
        app = self._find_app(name)
        if not app:
            return False, f"App '{name}' not found."
        app.pinned = pin
        self.save()
        return True, f"{'Pinned' if pin else 'Unpinned'} '{name}'."

    def rename_app(self, old: str, new: str) -> Tuple[bool, str]:
        app = self._find_app(old)
        if not app:
            return False, f"App '{old}' not found."
        if self._find_app(new):
            return False, f"App '{new}' already exists."
        app.name = new
        self.save()
        return True, f"Renamed '{old}' to '{new}'."

    def search_apps(self, query: str) -> List[App]:
        q = query.lower()
        return [
            a for a in self.apps
            if q in a.name.lower() or q in a.category.lower() or q in a.description.lower()
        ]

    def get_apps(self, category: Optional[str] = None, pinned_only: bool = False) -> List[App]:
        apps = self.apps
        if pinned_only:
            apps = [a for a in apps if a.pinned]
        if category and category.lower() not in ("all", ""):
            apps = [a for a in apps if a.category.lower() == category.lower()]
        return sorted(apps, key=lambda a: a.name.lower())

    def get_pinned(self) -> List[App]:
        return self.get_apps(pinned_only=True)

    def add_category(self, name: str) -> Tuple[bool, str]:
        if name in self.categories:
            return False, f"Category '{name}' already exists."
        self.categories.append(name)
        self.save()
        return True, f"Added category '{name}'."

    def remove_category(self, name: str) -> Tuple[bool, str]:
        if name not in self.categories:
            return False, f"Category '{name}' not found."
        if name == "General":
            return False, "Cannot remove the 'General' category."
        self.categories.remove(name)
        for app in self.apps:
            if app.category == name:
                app.category = "General"
        self.save()
        return True, f"Removed '{name}'. Affected apps moved to General."

    # ── categorisation ────────────────────────────────────────────────────────

    def _map_category(self, text: str) -> str:
        """Guess a category from a folder name or app name."""
        t = text.lower()
        if any(k in t for k in (
            'game', 'steam', 'epic', 'gog', 'uplay', 'ea app', 'ubisoft',
            'battle', 'xbox', 'riot', 'minecraft', 'blizzard', 'origin',
        )):
            return "Games"
        if any(k in t for k in (
            'browser', 'internet', 'chrome', 'firefox', 'edge', 'opera',
            'brave', 'vivaldi', 'tor browser',
        )):
            return "Browser"
        if any(k in t for k in (
            'visual studio', 'vscode', 'code', 'develop', 'python', 'node',
            'git ', 'jetbrain', 'rider', 'intellij', 'pycharm', 'eclipse',
            'android studio', 'arduino', 'postman', 'docker', 'putty',
            'winscpwin', 'winscp', 'filezilla',
        )):
            return "Development"
        if any(k in t for k in (
            'vlc', 'spotify', 'itunes', 'media', 'music', 'video', 'photo',
            'image', 'player', 'audacity', 'gimp', 'paint', 'premiere',
            'lightroom', 'photoshop', 'winamp', 'potplayer', 'kodi', 'obs',
        )):
            return "Media"
        if any(k in t for k in (
            'word', 'excel', 'powerpoint', 'outlook', 'office', 'libreoffice',
            'writer', 'calc', 'onenote', 'teams', 'notion', 'obsidian',
        )):
            return "Office"
        if any(k in t for k in (
            'tool', 'util', 'system', 'security', 'antivirus', 'driver',
            'hardware', 'maintenance', 'backup', 'cleaner', '7-zip', 'winrar',
            'winzip', 'cpu', 'gpu', 'fan', 'benchmark', 'malware',
        )):
            return "Tools"
        return "General"

    # ── scanning ──────────────────────────────────────────────────────────────

    def scan_registry(self) -> List[Tuple[str, str, str]]:
        """Scan Windows registry. Returns (name, path, category) tuples."""
        try:
            import winreg
        except ImportError:
            return []

        raw = []
        reg_keys = [
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, key_path in reg_keys:
            try:
                key = winreg.OpenKey(hive, key_path)
                i = 0
                while True:
                    try:
                        sub_key = winreg.OpenKey(key, winreg.EnumKey(key, i))
                        try:
                            name = winreg.QueryValueEx(sub_key, "DisplayName")[0]
                            try:
                                path = winreg.QueryValueEx(sub_key, "DisplayIcon")[0]
                                if "," in path:
                                    path = path.rsplit(",", 1)[0].strip()
                                path = path.strip('"')
                                if path.lower().endswith(".exe") and os.path.exists(path):
                                    raw.append((name.strip(), path))
                            except (FileNotFoundError, OSError):
                                pass
                        except (FileNotFoundError, OSError):
                            pass
                        winreg.CloseKey(sub_key)
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(key)
            except (FileNotFoundError, OSError):
                pass

        seen: set = set()
        result = []
        for name, path in raw:
            if name not in seen:
                seen.add(name)
                result.append((name, path, self._map_category(name)))
        return sorted(result, key=lambda x: x[0].lower())

    def scan_start_menu(self) -> List[Tuple[str, str, str]]:
        """Scan Start Menu + Desktop shortcuts for ALL user profiles.
        Returns (name, path, category) tuples."""
        ps_code = r"""
$shell = New-Object -ComObject WScript.Shell
$dirs = [System.Collections.Generic.List[string]]::new()

# Start Menu: current user + all users
$dirs.Add([Environment]::GetFolderPath('ApplicationData') + '\Microsoft\Windows\Start Menu\Programs')
$dirs.Add([Environment]::GetFolderPath('CommonApplicationData') + '\Microsoft\Windows\Start Menu\Programs')

# Desktop: current user + public
$dirs.Add([Environment]::GetFolderPath('Desktop'))
$dirs.Add([Environment]::GetFolderPath('CommonDesktopDirectory'))

# All user profile desktops (may need admin for some)
if (Test-Path 'C:\Users') {
    Get-ChildItem 'C:\Users' -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $d = "$($_.FullName)\Desktop"
        if (Test-Path $d) { $dirs.Add($d) }
        $sm = "$($_.FullName)\AppData\Roaming\Microsoft\Windows\Start Menu\Programs"
        if (Test-Path $sm) { $dirs.Add($sm) }
    }
}

$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($base in ($dirs | Select-Object -Unique)) {
    if (-not (Test-Path $base -ErrorAction SilentlyContinue)) { continue }
    Get-ChildItem $base -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $target = $shell.CreateShortcut($_.FullName).TargetPath
            if ($target -and ($target -like '*.exe') -and (Test-Path -LiteralPath $target -ErrorAction SilentlyContinue)) {
                if ($seen.Add($target)) {
                    $rel = $_.DirectoryName -replace [regex]::Escape($base),'' -replace '^\\',''
                    Write-Output "$($_.BaseName)|$target|$rel"
                }
            }
        } catch {}
    }
}
"""
        try:
            proc = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_code],
                capture_output=True, text=True, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            found = []
            seen_names: set = set()
            for line in proc.stdout.splitlines():
                parts = line.strip().split('|', 2)
                if len(parts) == 3:
                    name, path, rel = parts[0].strip(), parts[1].strip(), parts[2].strip()
                    if name and path and name not in seen_names and os.path.exists(path):
                        seen_names.add(name)
                        cat = self._map_category(rel if rel else name)
                        found.append((name, path, cat))
            return found
        except Exception:
            return []

    def scan_all(self) -> List[Tuple[str, str, str]]:
        """Registry + Start Menu + Desktop for all users, deduplicated by exe path."""
        by_path: dict = {}  # exe_path.lower() -> (name, path, category)

        for name, path, cat in self.scan_registry():
            by_path[path.lower()] = (name, path, cat)

        for name, path, cat in self.scan_start_menu():
            key = path.lower()
            if key not in by_path:
                by_path[key] = (name, path, cat)
            else:
                # Start Menu gives better folder-based categories; prefer it
                existing = by_path[key]
                if existing[2] in ("General",):
                    by_path[key] = (name, path, cat)

        return sorted(by_path.values(), key=lambda x: x[0].lower())
