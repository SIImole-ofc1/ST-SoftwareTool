"""Checks the macOS Task Manager and Advanced Startup backends on a real Mac.

Run by .github/workflows/mac-checks.yml on GitHub's macOS runners (which have passwordless
sudo, so the administrator paths run without a password dialog). Creates throw-away test
items and removes them again. Exit code 1 if any check fails.
"""
import json
import os
import plistlib
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import psutil  # noqa: E402

from core.startup_manager import StartupEntry  # noqa: E402
from core.startup_manager_mac import (  # noqa: E402
    MacStartupManager, _DISABLED_LOGIN_ITEMS, _disabled_overrides,
)
from core.task_manager import TaskManagerBackend  # noqa: E402

failures = []


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else f'   -> {detail!r}'), flush=True)
    if not cond:
        failures.append(name)


def section(title):
    print(f'\n=== {title} ===', flush=True)


def find(entries, name, kind=None):
    return next((e for e in entries if e.name == name and (kind is None or e.location_type == kind)), None)


def gone(pid):
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


# ── Task Manager ──────────────────────────────────────────────────────────────

def test_task_manager():
    section('Running from source')
    from core.osinfo import mac_app_bundle
    check("Python.app is not mistaken for the app bundle (updater safety)", mac_app_bundle() is None,
          mac_app_bundle())

    section('Task Manager: process list')
    b = TaskManagerBackend()
    check('collector command found', bool(b._cmd), b._cmd)
    busy = subprocess.Popen([sys.executable, '-c', 'while True: pass'])
    try:
        check('collector starts', b.start_monitor())
        snap = None
        for _ in range(3):           # CPU % needs two samples
            snap = b.read_procs() or snap
        snap = snap or []
        by_pid = {p.pid: p for p in snap}
        check('lists all processes', len(snap) > 100, len(snap))
        with_ram = sum(1 for p in snap if p.ram_mb > 0)
        check('RAM shown for (almost) every process, incl. system ones',
              with_ram >= 0.95 * len(snap), f'{with_ram}/{len(snap)}')
        launchd = by_pid.get(1)
        check('launchd (root) shows its memory', bool(launchd and launchd.ram_mb > 0),
              launchd and launchd.ram_mb)
        long_names = [p.name for p in snap if len(p.name) > 16]
        check('full process names (not cut at 16 characters)', bool(long_names), long_names[:3])
        bp = by_pid.get(busy.pid)
        min_cpu = 50.0 / (os.cpu_count() or 1)
        check(f'busy process shows CPU (>= {min_cpu:.0f}%)', bool(bp and bp.cpu_percent >= min_cpu),
              bp and bp.cpu_percent)
        check('busy process shows a start time', bool(bp and bp.create_time), bp and bp.create_time)
        print('sample:', [(p.name, p.pid, p.cpu_percent, p.ram_mb, p.status)
                          for p in sorted(snap, key=lambda p: -p.ram_mb)[:5]])
    finally:
        busy.kill()
        b.stop_monitor()

    section('Task Manager: app binary mode (--proc-monitor)')
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'main.py'), '--proc-monitor'],
                         stdout=subprocess.PIPE)
    try:
        data = json.loads(p.stdout.readline() or b'[]')
    finally:
        p.terminate()
    check('main.py --proc-monitor streams the process list', len(data) > 100, len(data))

    section('Task Manager: End Task')
    s = subprocess.Popen(['/bin/sleep', '600'])
    ok, err = b.kill_process(s.pid)
    check('ends a normal process', ok and gone(s.pid), err)

    stubborn = subprocess.Popen([sys.executable, '-c',
                                 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                                 'time.sleep(600)'])
    time.sleep(1.0)
    t0 = time.monotonic()
    ok, err = b.kill_process(stubborn.pid)
    check('forces a process that ignores "quit" (after waiting for it)',
          ok and gone(stubborn.pid) and time.monotonic() - t0 >= 2.5, (err, time.monotonic() - t0))

    out = subprocess.run(['sudo', '-n', '/bin/sh', '-c', '/bin/sleep 600 >/dev/null 2>&1 & echo $!'],
                         capture_output=True, text=True).stdout.strip()
    rpid = int(out)
    time.sleep(0.5)
    ok, err = b.kill_process(rpid)
    check('root process: normal End Task reports access_denied',
          not ok and err == 'access_denied' and not gone(rpid), (ok, err))
    ok, err = b.kill_process_as_admin(rpid)
    time.sleep(0.5)
    check('root process: ended with administrator rights', ok and gone(rpid), err)

    check('refuses launchd (pid 1)', b.kill_process(1) == (False, 'protected'))
    ws = next((p.pid for p in psutil.process_iter(['name']) if p.info['name'] == 'WindowServer'), None)
    if ws:
        check('refuses WindowServer', b.kill_process(ws) == (False, 'protected'))
        check('refuses WindowServer with admin too', b.kill_process_as_admin(ws) == (False, 'protected'))


# ── Advanced Startup ──────────────────────────────────────────────────────────

def test_startup():
    m = MacStartupManager()
    uid = os.getuid()

    section('Startup: Launch Agent (your account)')
    label = 'com.stsoftwaretool.citest'
    agent = os.path.expanduser(f'~/Library/LaunchAgents/{label}.plist')
    os.makedirs(os.path.dirname(agent), exist_ok=True)
    with open(agent, 'wb') as f:
        plistlib.dump({'Label': label, 'ProgramArguments': ['/bin/sleep', '600'], 'RunAtLoad': True}, f)
    try:
        e = find(m.scan_all(), label, 'Launch Agent')
        check('found, enabled, "Starts at login"', bool(e and e.enabled and 'login' in e.note.lower()),
              e and (e.enabled, e.note))
        check('command shown', bool(e and e.command == '/bin/sleep 600'), e and e.command)
        ok, err = m.disable_entry(e)
        check('disable', ok and _disabled_overrides(f'gui/{uid}').get(label) is True, err)
        e = find(m.scan_all(), label, 'Launch Agent')
        check('shows as disabled after rescan', bool(e and not e.enabled))
        ok, err = m.enable_entry(e)
        check('enable', ok and _disabled_overrides(f'gui/{uid}').get(label) is False, err)
    finally:
        os.remove(agent)

    section('Startup: Launch Daemon (needs administrator)')
    dlabel = 'com.stsoftwaretool.citestd'
    dpath = f'/Library/LaunchDaemons/{dlabel}.plist'
    subprocess.run(['sudo', '-n', 'tee', dpath], check=True, stdout=subprocess.DEVNULL,
                   input=plistlib.dumps({'Label': dlabel, 'ProgramArguments': ['/usr/bin/true'],
                                         'KeepAlive': True}))
    try:
        d = find(m.scan_all(), dlabel, 'Launch Daemon')
        check('found, runs as root', bool(d and d.enabled and 'root' in d.note), d and d.note)
        ok, err = m.disable_entry(d)
        check('disable (admin)', ok and _disabled_overrides('system').get(dlabel) is True, err)
        ok, err = m.enable_entry(d)
        check('enable (admin)', ok and _disabled_overrides('system').get(dlabel) is False, err)
    finally:
        subprocess.run(['sudo', '-n', 'rm', '-f', dpath])

    section('Startup: unsafe names are never run')
    marker = '/tmp/st-pwned'
    bad = StartupEntry('x', '', '/tmp/x', 'Launch Daemon', True, '',
                       extra={'label': f'x; touch {marker}', 'domain': 'system'})
    ok, _ = m.disable_entry(bad)
    check('label with shell characters refused', not m.can_toggle(bad) and not ok
          and not os.path.exists(marker))

    section('Startup: Login Item')
    app = '/System/Applications/Calculator.app'
    r = subprocess.run(['osascript', '-e', 'tell application "System Events" to make login item at end '
                        f'with properties {{path:"{app}", hidden:true}}'], capture_output=True, text=True,
                       timeout=60)
    if r.returncode != 0:
        print('SKIP  could not add a test login item:', r.stderr.strip())
    else:
        try:
            li = find(m.scan_all(), 'Calculator', 'Login Item')
            # (macOS 13+ dropped "hide on launch" for login items, so "hidden" isn't checked)
            check('found and enabled', bool(li and li.enabled), m.warnings)
            ok, err = m.disable_entry(li)
            check('disable (removed from Login Items)', ok, err)
            li = find(m.scan_all(), 'Calculator', 'Login Item')
            check('still listed, as disabled', bool(li and not li.enabled), li and li.location)
            ok, err = m.enable_entry(li)
            li = find(m.scan_all(), 'Calculator', 'Login Item')
            check('enable (added back)', ok and bool(li and li.enabled), err)
        finally:
            subprocess.run(['osascript', '-e',
                            'tell application "System Events" to delete login item "Calculator"'],
                           capture_output=True, timeout=60)
            if os.path.exists(_DISABLED_LOGIN_ITEMS):
                os.remove(_DISABLED_LOGIN_ITEMS)

    section('Startup: Cron Jobs, extensions')
    try:
        r = subprocess.run(['crontab', '-'], input='*/30 * * * * /usr/bin/true\n@reboot /bin/echo hi\n',
                           text=True, capture_output=True, timeout=20)
        cron_ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        cron_ok = False
    if not cron_ok:
        print('SKIP  could not install a test crontab')
    else:
        try:
            crons = [e for e in m.scan_all() if e.location_type == 'Cron Job']
            check('cron jobs listed', len(crons) == 2, [(c.name, c.note) for c in crons])
            check('@reboot job explained', any('starts' in c.note for c in crons))
        finally:
            subprocess.run(['crontab', '-r'], capture_output=True, timeout=20)
    entries = m.scan_all()
    kexts = [e for e in entries if e.location_type == 'Kernel Extension']
    check('kernel extensions listed', len(kexts) >= 1, len(kexts))
    print('scan warnings:', m.warnings)
    print('types found:', sorted({e.location_type for e in entries}))


def test_gpu():
    section('Performance: GPU on macOS')
    from core.performance_monitor import PerformanceMonitor
    mon = PerformanceMonitor()
    value = mon.gpu_percent()
    print('GPU utilisation:', value, '| GPU name:', mon.gpu_details().get('name'))
    # Virtual Macs may have no GPU statistics; real Macs report 0-100
    check('GPU reading is a percentage or unavailable', value is None or 0 <= value <= 100, value)


if __name__ == '__main__':
    for test in (test_task_manager, test_startup, test_gpu):
        try:
            test()
        except Exception as exc:  # report and carry on with the next group
            import traceback
            traceback.print_exc()
            check(f'{test.__name__} ran without crashing', False, exc)
    print(f'\n{len(failures)} failed check(s)' + (': ' + ', '.join(failures) if failures else ''))
    sys.exit(1 if failures else 0)
