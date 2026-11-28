"""The app's own pictures, encrypted inside the app.

A released app carries one file, assets/app.pack, instead of loose pictures: opening the app
("Show Package Contents" / "Open file location") shows nothing readable. It is decrypted in
memory when needed. Its key is not in the (public) source code: every build makes a new
random key, writes it to core/_pack_key.py and Nuitka compiles that into the app's machine code
(scripts/pack_assets.py).

The special app icons are sealed a second time, with a key that comes from their unlock code:
without the code they can't be opened at all — not even with the app's own key. Unlocking stores
that icon's key in the settings (see ui/app_icon.py).

Cipher (standard library only): SHAKE-256 keystream, then HMAC-SHA-256 over nonce + ciphertext
(encrypt-then-MAC, separate sub-keys); a wrong key or a changed file is detected.

Running from source without a pack (development): the plain files in assets/ are used.
"""
from __future__ import annotations

import atexit
import hashlib
import hmac
import io
import os
import shutil
import sys
import tempfile
import zipfile
from typing import Optional

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(_HERE, 'assets')
PACK = os.path.join(ASSETS, 'app.pack')
MAGIC = b'STPK1\n'
_SPECIAL_SALT = b'ST-SoftwareTool special icon '
_SPECIAL_ROUNDS = 300_000


# ── the cipher ───────────────────────────────────────────────────────────────

def _subkeys(key: bytes) -> tuple:
    return hashlib.sha256(b'enc' + key).digest(), hashlib.sha256(b'mac' + key).digest()


def _xor(data: bytes, stream: bytes) -> bytes:
    return (int.from_bytes(data, 'little') ^ int.from_bytes(stream, 'little')).to_bytes(len(data), 'little')


def seal(key: bytes, data: bytes) -> bytes:
    enc, mac = _subkeys(key)
    nonce = os.urandom(16)
    body = _xor(data, hashlib.shake_256(enc + nonce).digest(len(data))) if data else b''
    tag = hmac.new(mac, nonce + body, 'sha256').digest()
    return MAGIC + nonce + tag + body


def unseal(key: bytes, blob: bytes) -> Optional[bytes]:
    """The plain data, or None when the key is wrong or the data was changed."""
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 48:
        return None
    blob = blob[len(MAGIC):]
    nonce, tag, body = blob[:16], blob[16:48], blob[48:]
    enc, mac = _subkeys(key)
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + body, 'sha256').digest()):
        return None
    return _xor(body, hashlib.shake_256(enc + nonce).digest(len(body))) if body else b''


def special_key(code: str, name: str) -> bytes:
    """The key of a special icon, from its unlock code (slow on purpose: guessing is costly)."""
    normal = (code or '').strip().lower().encode('utf-8')
    return hashlib.pbkdf2_hmac('sha256', normal, _SPECIAL_SALT + name.encode(), _SPECIAL_ROUNDS)


# ── the app's files ──────────────────────────────────────────────────────────

_files: Optional[dict] = None


def _pack_key() -> Optional[bytes]:
    try:
        from core import _pack_key           # written by the build, never in the repository
        return _pack_key.key()
    except Exception:
        return None


def _load() -> dict:
    global _files
    if _files is not None:
        return _files
    _files = {}
    key = _pack_key()
    if key and os.path.isfile(PACK):
        with open(PACK, 'rb') as f:
            plain = unseal(key, f.read())
        if plain is not None:
            with zipfile.ZipFile(io.BytesIO(plain)) as z:
                _files = {n: z.read(n) for n in z.namelist()}
    return _files


def packed() -> bool:
    return bool(_load())


def data(name: str) -> Optional[bytes]:
    """The bytes of an app file (e.g. 'app-icon-light.png'), or None."""
    files = _load()
    if files:
        return files.get(name)
    path = os.path.join(ASSETS, name)                # running from source
    try:
        with open(path, 'rb') as f:
            return f.read()
    except OSError:
        return None


def pixmap(name: str):
    from PySide6.QtGui import QPixmap
    pm = QPixmap()
    raw = data(name)
    if raw:
        pm.loadFromData(raw)
    return pm


def icon(name: str):
    from PySide6.QtGui import QIcon
    pm = pixmap(name)
    return QIcon(pm) if not pm.isNull() else QIcon()


# Some system calls want a file (e.g. the Finder icon of the app): a private temporary copy,
# readable only by this user and removed when the app quits.
_tmp_dir: Optional[str] = None


def file(name: str, raw: Optional[bytes] = None) -> Optional[str]:
    global _tmp_dir
    raw = raw if raw is not None else data(name)
    if raw is None:
        return None
    if not packed() and os.path.isfile(os.path.join(ASSETS, name)):
        return os.path.join(ASSETS, name)
    if _tmp_dir is None:
        _tmp_dir = tempfile.mkdtemp(prefix='st-')          # mode 0700
        atexit.register(shutil.rmtree, _tmp_dir, True)
    path = os.path.join(_tmp_dir, os.path.basename(name))
    with open(path, 'wb') as f:
        f.write(raw)
    if sys.platform != 'win32':
        os.chmod(path, 0o600)
    return path


def special(name: str, key_hex: Optional[str]) -> Optional[bytes]:
    """A special icon's picture (name: 'special' / 'special2'), given the key its code made."""
    if not key_hex:
        return None
    blob = data(f'app-icon-{name}.enc')
    try:
        return unseal(bytes.fromhex(key_hex), blob) if blob else None
    except ValueError:
        return None
