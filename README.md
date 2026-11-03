# builders-feed

A phone-friendly grid of other people's projects: Show HN, r/coolgithubprojects, DEV #showdev, r/selfhosted, GitHub Trending and more, split into **Must see** and **If you have time**.

- `build.py` fetches every source into `data/feed.json` plus `must-see.xml` and `if-you-have-time.xml` (RSS, for Inoreader). Standard library only.
- `index.html` is the page.
- `publish.sh` builds and force-pushes the page and data to the `site` branch, which GitHub Pages serves. It runs hourly on the Mac mini from `~/Library/LaunchAgents/com.stephen.builders-feed.plist`, because Reddit answers the Mac and blocks GitHub's servers.

Live: https://stephenvdavis-jpg.github.io/builders-feed/
