import os
import re
import shlex
from typing import List, Optional
from .manager import AppManager
from .window_ops import close_app, min_app


class CommandResult:
    def __init__(self, success: bool, message: str = "", data=None, action: str = ""):
        self.success = success
        self.message = message
        self.data = data
        self.action = action  # "clear", "switch_gui", "exit", "scan", "theme_dark", "theme_light"


HELP_TEXT = """\
ST-SoftwareTool  —  Command Reference
══════════════════════════════════════════════════
All commands require  >_/  prefix:

App management:
  >_/app:list                         List all apps
  >_/app:list_pinned                  List pinned apps only
  >_/app:list_cat("Category")         List apps in category
  >_/from:open_app("Name")            Open / launch an app
  >_/from:add_app("path")             Add an app
  >_/from:remove_app("Name")          Remove an app
  >_/from:pin_app("Name")             Pin (favorite) an app
  >_/from:unpin_app("Name")           Unpin an app
  >_/from:rename_app("old", "new")    Rename an app

Search & Info:
  >_/find:search("query")             Search apps by name
  >_/find:info("Name")                Show app details
  >_/cat:list                         List categories

System:
  >_/sys:scan                         Scan registry for apps
  >_/sys:clear                        Clear the terminal
  >_/sys:exit                         Exit AppManager

Window control:
  >_/cut:close_app("Name")            Close a running app
  >_/out:min_app("Name")              Minimize a running app
  >_/gui:on_[True]                    Switch to GUI mode
  >_/gui:on_[False]                   Return to terminal mode

Themes  (1 = Dark B&G · 2 = Dark B&W · 3 = Light · 4 = High Contrast · 5 = Classic):
  >_/look=gui:theme_1  through  >_/look=gui:theme_5

Type  >_/help:show  to show this reference.\
"""

COMMAND_HELP = {
    "list":     "list [--pinned | -p] [-c <category>]\n  List apps, optionally filtered.",
    "launch":   "launch <name>\n  Launch a registered app by name.",
    "add":      "add <path> [--name <n>] [--category <c>] [--desc <d>]\n  Register an app.",
    "remove":   "remove <name>\n  Remove an app from the registry.",
    "pin":      "pin <name>\n  Mark an app as a favorite.",
    "unpin":    "unpin <name>\n  Remove the favorite mark from an app.",
    "search":   "search <query>\n  Search by name, category, or description.",
    "info":     "info <name>\n  Show full details for an app.",
    "rename":   "rename <old> <new>\n  Rename a registered app.",
    "category": "category list|add <n>|rm <n>\n  Manage categories.",
    "scan":     "scan\n  Scan the Windows registry and import installed programs.",
    "clear":    "clear\n  Clear all terminal output.",
    "switch":   "switch\n  Switch to the graphical (GUI) view.",
    "theme":    "theme dark|light\n  Change the colour theme.",
    "exit":     "exit\n  Close AppManager.",
}


class CommandProcessor:
    def __init__(self, manager: AppManager):
        self.manager = manager
        self._dispatch = {
            "help":     self._cmd_help,
            "list":     self._cmd_list,
            "launch":   self._cmd_launch,
            "run":      self._cmd_launch,
            "add":      self._cmd_add,
            "remove":   self._cmd_remove,
            "rm":       self._cmd_remove,
            "delete":   self._cmd_remove,
            "pin":      self._cmd_pin,
            "unpin":    self._cmd_unpin,
            "search":   self._cmd_search,
            "find":     self._cmd_search,
            "info":     self._cmd_info,
            "rename":   self._cmd_rename,
            "category": self._cmd_category,
            "cat":      self._cmd_category,
            "scan":     self._cmd_scan,
            "clear":    self._cmd_clear,
            "cls":      self._cmd_clear,
            "switch":   self._cmd_switch,
            "gui":      self._cmd_switch,
            "theme":    self._cmd_theme,
            "exit":     self._cmd_exit,
            "quit":     self._cmd_exit,
        }

    def process(self, raw: str) -> CommandResult:
        raw = raw.strip()
        if not raw:
            return CommandResult(True)
        try:
            parts = shlex.split(raw)
        except ValueError as e:
            return CommandResult(False, f"Parse error: {e}")

        cmd, args = parts[0].lower(), parts[1:]

        if cmd in self._dispatch:
            return self._dispatch[cmd](args)

        return CommandResult(False, f"Unknown command '{cmd}'. Type 'help' for a list.")

    # ── commands ──────────────────────────────────────────────────────────────

    def _cmd_help(self, args: List[str]) -> CommandResult:
        if args:
            key = args[0].lower()
            if key in COMMAND_HELP:
                return CommandResult(True, COMMAND_HELP[key])
            return CommandResult(False, f"No help entry for '{key}'.")
        return CommandResult(True, HELP_TEXT)

    def _cmd_list(self, args: List[str]) -> CommandResult:
        pinned = "--pinned" in args or "-p" in args
        category: Optional[str] = None
        for flag in ("-c", "--category"):
            if flag in args:
                idx = args.index(flag)
                if idx + 1 < len(args):
                    category = args[idx + 1]

        apps = self.manager.get_apps(category=category, pinned_only=pinned)
        if not apps:
            tag = "pinned " if pinned else ""
            cat = f"in '{category}' " if category else ""
            return CommandResult(True, f"No {tag}apps {cat}registered.")

        header = "Pinned Apps" if pinned else (f"Category: {category}" if category else f"All Apps  ({len(apps)})")
        lines = [header, "─" * 52]
        for a in apps:
            mark = "★ " if a.pinned else "  "
            lines.append(f"{mark}{a.name:<32} [{a.category}]")
        return CommandResult(True, "\n".join(lines), data=apps)

    def _cmd_launch(self, args: List[str]) -> CommandResult:
        if not args:
            return CommandResult(False, "Usage: launch <name>")
        ok, msg = self.manager.launch_app(" ".join(args))
        return CommandResult(ok, msg)

    def _cmd_add(self, args: List[str]) -> CommandResult:
        if not args:
            return CommandResult(False, "Usage: add <path> [--name <n>] [--category <c>] [--desc <d>]")
        path = name = desc = None
        category = "General"
        i = 0
        while i < len(args):
            if args[i] == "--name" and i + 1 < len(args):
                name = args[i + 1]; i += 2
            elif args[i] == "--category" and i + 1 < len(args):
                category = args[i + 1]; i += 2
            elif args[i] == "--desc" and i + 1 < len(args):
                desc = args[i + 1]; i += 2
            else:
                path = args[i]; i += 1

        if not path:
            return CommandResult(False, "A path is required.")
        if not name:
            name = os.path.splitext(os.path.basename(path))[0]

        ok, msg = self.manager.add_app(name, path, category, desc or "")
        return CommandResult(ok, msg)

    def _cmd_remove(self, args: List[str]) -> CommandResult:
        if not args:
            return CommandResult(False, "Usage: remove <name>")
        ok, msg = self.manager.remove_app(" ".join(args))
        return CommandResult(ok, msg)

    def _cmd_pin(self, args: List[str]) -> CommandResult:
        if not args:
            return CommandResult(False, "Usage: pin <name>")
        ok, msg = self.manager.pin_app(" ".join(args), True)
        return CommandResult(ok, msg)

    def _cmd_unpin(self, args: List[str]) -> CommandResult:
        if not args:
            return CommandResult(False, "Usage: unpin <name>")
        ok, msg = self.manager.pin_app(" ".join(args), False)
        return CommandResult(ok, msg)

    def _cmd_search(self, args: List[str]) -> CommandResult:
        if not args:
            return CommandResult(False, "Usage: search <query>")
        query = " ".join(args)
        apps = self.manager.search_apps(query)
        if not apps:
            return CommandResult(True, f"No results for '{query}'.")
        lines = [f"Results for '{query}'  ({len(apps)})", "─" * 52]
        for a in apps:
            mark = "★ " if a.pinned else "  "
            lines.append(f"{mark}{a.name:<32} [{a.category}]")
        return CommandResult(True, "\n".join(lines), data=apps)

    def _cmd_info(self, args: List[str]) -> CommandResult:
        if not args:
            return CommandResult(False, "Usage: info <name>")
        app = self.manager._find_app(" ".join(args))
        if not app:
            return CommandResult(False, f"App '{' '.join(args)}' not found.")
        lines = [
            f"  Name        {app.name}",
            f"  Path        {app.path}",
            f"  Category    {app.category}",
            f"  Pinned      {'Yes ★' if app.pinned else 'No'}",
            f"  Description {app.description or '(none)'}",
        ]
        return CommandResult(True, "\n".join(lines), data=app)

    def _cmd_rename(self, args: List[str]) -> CommandResult:
        if len(args) < 2:
            return CommandResult(False, "Usage: rename <old> <new>")
        ok, msg = self.manager.rename_app(args[0], " ".join(args[1:]))
        return CommandResult(ok, msg)

    def _cmd_category(self, args: List[str]) -> CommandResult:
        sub = args[0].lower() if args else "list"

        if sub == "list":
            cats = sorted(self.manager.categories)
            lines = [f"Categories  ({len(cats)})", "─" * 40]
            for c in cats:
                n = len(self.manager.get_apps(category=c))
                lines.append(f"  {c:<28} {n} app{'s' if n != 1 else ''}")
            return CommandResult(True, "\n".join(lines))

        if sub in ("add",) and len(args) >= 2:
            ok, msg = self.manager.add_category(" ".join(args[1:]))
            return CommandResult(ok, msg)

        if sub in ("rm", "remove", "delete") and len(args) >= 2:
            ok, msg = self.manager.remove_category(" ".join(args[1:]))
            return CommandResult(ok, msg)

        return CommandResult(False, "Usage: category list | add <name> | rm <name>")

    def _cmd_scan(self, _args: List[str]) -> CommandResult:
        return CommandResult(True, "Starting registry scan…", action="scan")

    def _cmd_clear(self, _args: List[str]) -> CommandResult:
        return CommandResult(True, action="clear")

    def _cmd_switch(self, _args: List[str]) -> CommandResult:
        return CommandResult(True, "Switching to GUI mode…", action="switch_gui")

    def _cmd_theme(self, args: List[str]) -> CommandResult:
        _valid = ("dark", "dark_bw", "light", "hc", "win95")
        if not args or args[0].lower() not in _valid:
            return CommandResult(False, f"Usage: theme {'|'.join(_valid)}")
        t = args[0].lower()
        self.manager.settings["theme"] = t
        self.manager.save()
        return CommandResult(True, f"Theme set to '{t}'.", action=f"theme_{t}")

    def _cmd_exit(self, _args: List[str]) -> CommandResult:
        return CommandResult(True, "Goodbye!", action="exit")

    # ── >_/ script syntax ─────────────────────────────────────────────────────

    # Theme number → key
    _THEME_NUM = {"1": "dark", "2": "dark_bw", "3": "light", "4": "hc", "5": "win95"}

    def process_script(self, raw: str) -> CommandResult:
        if not raw.startswith(">_/"):
            return CommandResult(False, "Commands must start with  >_/")

        body = raw[3:].strip()

        # ── help:show ─────────────────────────────────────────────────────────
        if body == "help:show":
            return self._cmd_help([])

        # ── app:list / app:list_pinned / app:list_cat("cat") ──────────────────
        if body == "app:list":
            return self._cmd_list([])
        if body == "app:list_pinned":
            return self._cmd_list(["--pinned"])
        m = re.fullmatch(r'app:list_cat\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            return self._cmd_list(["-c", m.group(1)])

        # ── from:open_app("Name") ─────────────────────────────────────────────
        m = re.fullmatch(r'from:open_app\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            return self._cmd_launch([m.group(1)])

        # ── from:add_app("path") ──────────────────────────────────────────────
        m = re.fullmatch(r'from:add_app\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            return self._cmd_add([m.group(1)])

        # ── from:remove_app("Name") ───────────────────────────────────────────
        m = re.fullmatch(r'from:remove_app\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            return self._cmd_remove([m.group(1)])

        # ── from:pin_app("Name") ──────────────────────────────────────────────
        m = re.fullmatch(r'from:pin_app\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            return self._cmd_pin([m.group(1)])

        # ── from:unpin_app("Name") ────────────────────────────────────────────
        m = re.fullmatch(r'from:unpin_app\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            return self._cmd_unpin([m.group(1)])

        # ── from:rename_app("old", "new") ─────────────────────────────────────
        m = re.fullmatch(
            r'from:rename_app\(["\']?(.+?)["\']?,\s*["\']?(.+?)["\']?\)',
            body, re.IGNORECASE,
        )
        if m:
            return self._cmd_rename([m.group(1), m.group(2)])

        # ── find:search("query") ──────────────────────────────────────────────
        m = re.fullmatch(r'find:search\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            return self._cmd_search([m.group(1)])

        # ── find:info("Name") ─────────────────────────────────────────────────
        m = re.fullmatch(r'find:info\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            return self._cmd_info([m.group(1)])

        # ── cat:list ──────────────────────────────────────────────────────────
        if body == "cat:list":
            return self._cmd_category(["list"])

        # ── sys:scan / sys:clear / sys:exit ───────────────────────────────────
        if body == "sys:scan":
            return self._cmd_scan([])
        if body == "sys:clear":
            return self._cmd_clear([])
        if body == "sys:exit":
            return self._cmd_exit([])

        # ── cut:close_app("Name") ─────────────────────────────────────────────
        m = re.fullmatch(r'cut:close_app\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            ok, msg = close_app(m.group(1))
            return CommandResult(ok, msg)

        # ── out:min_app("Name") ───────────────────────────────────────────────
        m = re.fullmatch(r'out:min_app\(["\']?(.+?)["\']?\)', body, re.IGNORECASE)
        if m:
            ok, msg = min_app(m.group(1))
            return CommandResult(ok, msg)

        # ── gui:on_[True] / gui:on_[False] ────────────────────────────────────
        m = re.fullmatch(r'gui:on_\[(True|False)\]', body, re.IGNORECASE)
        if m:
            if m.group(1).lower() == "true":
                return CommandResult(True, "Switching to GUI mode.", action="switch_gui")
            return CommandResult(True, "Already in terminal mode.", action="")

        # ── look=gui:theme_1..5 ───────────────────────────────────────────────
        m = re.fullmatch(r'look=gui:theme_([1-5])', body, re.IGNORECASE)
        if m:
            t = self._THEME_NUM[m.group(1)]
            self.manager.settings["theme"] = t
            self.manager.save()
            names = {"dark": "Dark B&G", "dark_bw": "Dark B&W",
                     "light": "Light", "hc": "High Contrast", "win95": "Classic"}
            return CommandResult(True, f"Theme set: {names[t]}", action=f"theme_{t}")

        return CommandResult(
            False,
            f"Unknown command: '>_/{body}'\n"
            "Type  >_/help:show  to see all commands."
        )
