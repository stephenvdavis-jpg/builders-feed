#!/bin/bash
# Hourly (launchd com.stephen.builders-feed): rebuild the feed, then force-push
# the page and its data as one fresh commit on the `site` branch, which GitHub
# Pages serves. Code history stays on main; the hourly data keeps no history.
set -euo pipefail
cd "$(dirname "$0")"
/usr/bin/python3 build.py
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
mkdir -p "$out/data"
cp index.html must-see.xml if-you-have-time.xml inoreader/builders.opml "$out/"
cp data/feed.json "$out/data/"
touch "$out/.nojekyll"
git -C "$out" init -q -b site
git -C "$out" add -A
git -C "$out" -c user.name="builders-feed" -c user.email="stephenvdavis-jpg@users.noreply.github.com" commit -qm "Feed $(date -u +%FT%TZ)"
git -C "$out" push -qf "$(git remote get-url origin)" site
echo "published $(date)"
