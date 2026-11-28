"""Build the encrypted app.pack and core/_pack_key.py (see core/vault.py).

    python scripts/pack_assets.py <out_dir>

writes <out_dir>/app.pack (every app picture, big logos made smaller first) and
core/_pack_key.py (its key, split so it isn't one readable string). Each build makes a new
random key and compiles it into that build's app, so the key is never stored anywhere — and
neither file is committed. (ST_PACK_KEY=<64 hex> fixes the key, e.g. for a test.)

Sealing a special icon with its code (run once, locally, when a special icon changes):

    python scripts/pack_assets.py --special special2 '<code>' path/to/icon.png

writes assets/app-icon-special2.enc. The plain picture must never be committed.
"""
from __future__ import annotations

import io
import os
import secrets
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import vault  # noqa: E402

ASSETS = os.path.join(ROOT, 'assets')
# Files the app loads (anything else in assets/, e.g. the website's logos, stays out)
APP_FILES = ('STsoftwareterminalLOGO.png.png', 'STsoftwaretoolVPNLOGO.png.png',
             'STsoftwaretoolantivirusLOGO.png.png', 'app-icon-light.png', 'app-icon-dark.png',
             'app-icon-special.enc', 'app-icon-special2.enc')
MAX_SIDE = 512          # the big logos are shown small: no need to ship 2000 px pictures


def _shrink(raw: bytes) -> bytes:
    try:
        from PIL import Image
    except ImportError:
        return raw
    img = Image.open(io.BytesIO(raw))
    if max(img.size) <= MAX_SIDE:
        return raw
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, 'PNG', optimize=True)
    return out.getvalue() if len(out.getvalue()) < len(raw) else raw


def build(out_dir: str) -> None:
    key_hex = os.environ.get('ST_PACK_KEY', '').strip()
    key = bytes.fromhex(key_hex) if len(key_hex) == 64 else secrets.token_bytes(32)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as z:
        for name in APP_FILES:
            with open(os.path.join(ASSETS, name), 'rb') as f:
                raw = f.read()
            z.writestr(name, _shrink(raw) if name.endswith('.png') else raw)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'app.pack'), 'wb') as f:
        f.write(vault.seal(key, buf.getvalue()))
    # the key, as two random-looking halves
    mask = secrets.token_bytes(32)
    other = bytes(a ^ b for a, b in zip(key, mask))
    with open(os.path.join(ROOT, 'core', '_pack_key.py'), 'w') as f:
        f.write('# Written by scripts/pack_assets.py during the build. Never commit this file.\n'
                f'_A = {list(mask)!r}\n_B = {list(other)!r}\n\n\n'
                'def key() -> bytes:\n    return bytes(a ^ b for a, b in zip(_A, _B))\n')
    print('packed', len(APP_FILES), 'files into', os.path.join(out_dir, 'app.pack'))


def seal_special(name: str, code: str, picture: str) -> None:
    with open(picture, 'rb') as f:
        raw = f.read()
    blob = vault.seal(vault.special_key(code, name), raw)
    out = os.path.join(ASSETS, f'app-icon-{name}.enc')
    with open(out, 'wb') as f:
        f.write(blob)
    assert vault.unseal(vault.special_key(code, name), blob) == raw
    print('sealed', picture, '->', out)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--special']:
        seal_special(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'build', 'pack'))
