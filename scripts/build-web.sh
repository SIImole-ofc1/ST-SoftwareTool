#!/usr/bin/env bash
# Build a clean Cloudflare Pages publish folder (website only).
# Used by the Cloudflare Pages Git build (project: stsoftwaretoolofc).
# Never copies the Python app, installers, or tools.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf publish
mkdir -p publish

cp index.html style.css _headers publish/
cp ./*.js publish/
cp -r assets privacy download publish/

# Guards: required pages must exist or the deploy is wrong
test -f publish/index.html
test -f publish/privacy/index.html
for js in $(grep -ohE 'src="/?[a-z0-9-]+\.js"' index.html privacy/index.html | sed -E 's/src="\/?//; s/"//' | sort -u); do
  test -f "publish/$js" || { echo "Missing script referenced by HTML: $js"; exit 1; }
done

echo "Publish contents:"
find publish -type f | sort
