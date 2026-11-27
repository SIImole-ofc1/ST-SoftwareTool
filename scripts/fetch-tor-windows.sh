#!/usr/bin/env bash
# Download the official Tor Expert Bundle for Windows (x86_64) and verify its OpenPGP signature.
# Runs in Git Bash on the GitHub Windows runner (which has curl, tar and gpg).
# Usage: scripts/fetch-tor-windows.sh <destination folder>
# Result: <dest>/tor/tor.exe, <dest>/tor/pluggable_transports/…, <dest>/data/geoip…, <dest>/VERSION
set -euo pipefail

dest="${1:?destination folder}"

# Tor Browser Developers signing key — it signs the Expert Bundles
# (https://support.torproject.org/tbb/how-to-verify-signature/)
KEY_FPR=EF6E286DDA85EA2A4BA7DE684E2C6E8793298290
base=https://dist.torproject.org/torbrowser
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Newest stable release that has a Windows x86_64 Expert Bundle
versions=$(curl -fsSL "$base/" | grep -oE 'href="[0-9]+\.[0-9]+(\.[0-9]+)?/"' \
           | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | sort -u -t. -k1,1nr -k2,2nr -k3,3nr)
ver="" ; file=""
for v in $versions; do
  name="tor-expert-bundle-windows-x86_64-${v}.tar.gz"
  if curl -fsIL "$base/$v/$name" >/dev/null 2>&1; then ver=$v; file=$name; break; fi
done
[ -n "$ver" ] || { echo "No Windows Expert Bundle found on $base" >&2; exit 1; }
echo "Tor Expert Bundle $ver (windows-x86_64)"

curl -fsSL -o "$tmp/$file"     "$base/$ver/$file"
curl -fsSL -o "$tmp/$file.asc" "$base/$ver/$file.asc"

export GNUPGHOME="$tmp/gnupg"; mkdir -m 700 "$GNUPGHOME"
gpg --batch --quiet --auto-key-locate nodefault,wkd --locate-keys torbrowser@torproject.org \
  || gpg --batch --quiet --keyserver hkps://keys.openpgp.org --recv-keys "$KEY_FPR"
gpg --batch --status-fd 1 --verify "$tmp/$file.asc" "$tmp/$file" > "$tmp/status" 2>/dev/null || true
# VALIDSIG's last field is the primary key fingerprint: it must be the Tor Browser key
if ! grep -q "^\[GNUPG:\] VALIDSIG .* $KEY_FPR\$" "$tmp/status"; then
  echo "Signature check FAILED for $file" >&2; cat "$tmp/status" >&2; exit 1
fi
echo "Signature OK (Tor Browser Developers, $KEY_FPR)"

rm -rf "$dest"; mkdir -p "$dest"
tar -xzf "$tmp/$file" -C "$dest"
echo "$ver" > "$dest/VERSION"
"$dest/tor/tor.exe" --version | head -n 1
ls "$dest/tor/pluggable_transports" 2>/dev/null || true
