#!/usr/bin/env bash
# Download the official Tor Expert Bundle for macOS and verify its OpenPGP signature.
# Usage: scripts/fetch-tor-macos.sh <arm64|x86_64> <destination folder>
# Result: <dest>/tor/tor, <dest>/tor/pluggable_transports/…, <dest>/data/geoip…, <dest>/VERSION
set -euo pipefail

arch="${1:?arch (arm64 or x86_64)}"
dest="${2:?destination folder}"
case "$arch" in
  arm64)  tarch=aarch64 ;;
  x86_64) tarch=x86_64 ;;
  *) echo "unknown arch: $arch" >&2; exit 1 ;;
esac

# Tor Browser Developers signing key — it signs the Expert Bundles
# (https://support.torproject.org/tbb/how-to-verify-signature/)
KEY_FPR=EF6E286DDA85EA2A4BA7DE684E2C6E8793298290
base=https://dist.torproject.org/torbrowser
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Newest stable release that has a macOS Expert Bundle for this chip
versions=$(curl -fsSL "$base/" | grep -oE 'href="[0-9]+\.[0-9]+(\.[0-9]+)?/"' \
           | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | sort -u -t. -k1,1nr -k2,2nr -k3,3nr)
ver="" ; file=""
for v in $versions; do
  name="tor-expert-bundle-macos-${tarch}-${v}.tar.gz"
  if curl -fsIL "$base/$v/$name" >/dev/null 2>&1; then ver=$v; file=$name; break; fi
done
[ -n "$ver" ] || { echo "No macOS Expert Bundle found on $base" >&2; exit 1; }
echo "Tor Expert Bundle $ver ($tarch)"

curl -fsSL -o "$tmp/$file"     "$base/$ver/$file"
curl -fsSL -o "$tmp/$file.asc" "$base/$ver/$file.asc"

command -v gpg >/dev/null || brew install gnupg >/dev/null
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
"$dest/tor/tor" --version | head -n 1
ls "$dest/tor/pluggable_transports" 2>/dev/null || true
