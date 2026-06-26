# ST.spec — PyInstaller build spec for ST-SoftwareTool
# Run with:  pyinstaller ST.spec
#
# Produces dist\ST\  containing:
#   ST.exe            — main windowed application
#   proc_monitor.exe  — subprocess helper for Task Manager tab
#   assets\           — icons
#   core\tor_bundle\  — Tor binaries for VPN tab

block_cipher = None

# ── Main Application ──────────────────────────────────────────────────────────

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('assets',           'assets'),
        ('core/tor_bundle',  'core/tor_bundle'),
    ],
    hiddenimports=[
        'winreg',
        'psutil',
        'ctypes',
        'ctypes.wintypes',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ST',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/STsoftwareterminalLOGO.ico',
    uac_admin=True,
)

# ── Process Monitor Subprocess ────────────────────────────────────────────────
# Spawned by core/task_manager.py to run psutil off the main process's GIL.

b = Analysis(
    ['core/proc_monitor.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['psutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz_b = PYZ(b.pure, b.zipped_data, cipher=block_cipher)

exe_b = EXE(
    pyz_b,
    b.scripts,
    [],
    exclude_binaries=True,
    name='proc_monitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
)

# ── Collect both executables into a single dist\ST\ folder ───────────────────

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    exe_b,
    b.binaries,
    b.zipfiles,
    b.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ST',
)
