#!/usr/bin/env python3
"""Builds data/feed.json (and two RSS files) from the project sites.

Runs hourly on the Mac mini (launchd), because Reddit answers it and blocks
GitHub's servers. A source that fails keeps its items from the last build.
Standard library only.
"""
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import format_datetime, parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
FEED = DATA / "feed.json"
OG_CACHE = DATA / "og-cache.json"
SITE = "https://stephenvdavis-jpg.github.io/builders-feed/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130 Safari/537.36")
NOW = int(time.time())
SLOW = {"selfhst": 21, "console": 21, "lobsters": 21, "openalt": 10, "opensource": 8, "beautiful": 8}   # days a post stays (default 4); trending lists never age out

# tier 1 = Must see, tier 2 = If you have time
SOURCES = {
    "hn":        {"name": "Show HN", "tier": 1, "color": "#E8692C", "home": "https://news.ycombinator.com/show"},
    "cgp":       {"name": "r/coolgithubprojects", "tier": 1, "color": "#3E6AE1", "home": "https://www.reddit.com/r/coolgithubprojects/"},
    "showdev":   {"name": "DEV #showdev", "tier": 1, "color": "#1B1B1B", "home": "https://dev.to/t/showdev"},
    "selfhosted": {"name": "r/selfhosted", "tier": 1, "color": "#2F8F6B", "home": "https://www.reddit.com/r/selfhosted/"},
    "trending":  {"name": "GitHub Trending", "tier": 1, "color": "#24292F", "home": "https://github.com/trending"},
    "sideproject": {"name": "r/SideProject", "tier": 2, "color": "#C2410C", "home": "https://www.reddit.com/r/SideProject/"},
    "hf":        {"name": "Hugging Face Spaces", "tier": 2, "color": "#D99A00", "home": "https://huggingface.co/spaces"},
    "selfhst":   {"name": "selfh.st", "tier": 2, "color": "#0E7490", "home": "https://selfh.st/apps/"},
    "lobsters":  {"name": "Lobsters", "tier": 2, "color": "#AC130D", "home": "https://lobste.rs/t/show"},
    "console":   {"name": "Console.dev", "tier": 2, "color": "#4B5563", "home": "https://console.dev/"},
    "openalt":   {"name": "OpenAlternative", "tier": 2, "color": "#6D28D9", "home": "https://openalternative.co/"},
    "opensource": {"name": "r/opensource", "tier": 2, "color": "#15803D", "home": "https://www.reddit.com/r/opensource/"},
    "beautiful": {"name": "r/InternetIsBeautiful", "tier": 2, "color": "#BE185D", "home": "https://www.reddit.com/r/InternetIsBeautiful/"},
    "itch":      {"name": "itch.io", "tier": 2, "color": "#E0484B", "home": "https://itch.io/games/new-and-popular"},
}

# Sites with no usable feed: shown as link tiles between posts
LINKS = [
    {"name": "daily.dev", "tier": 1, "url": "https://app.daily.dev/", "color": "#0F1217", "line": "Your dev feed"},
    {"name": "CodePen Trending", "tier": 2, "url": "https://codepen.io/trending", "color": "#131417", "line": "Pens people are forking"},
    {"name": "showlog", "tier": 2, "url": "https://showlog.dev/", "color": "#E8692C", "line": "Every Show HN, by topic"},
    {"name": "OSS Insight", "tier": 2, "url": "https://ossinsight.io/collections/", "color": "#1E1B4B", "line": "Fastest-rising repos"},
    {"name": "DevHunt", "tier": 2, "url": "https://devhunt.org/", "color": "#0B1120", "line": "This week's dev tool launches"},
    {"name": "Peerlist Launchpad", "tier": 2, "url": "https://peerlist.io/launchpad", "color": "#00AA45", "line": "Weekly builder launches"},
    {"name": "Awesome lists", "tier": 2, "url": "https://github.com/sindresorhus/awesome", "color": "#FC60A8", "line": "Go deep on one topic"},
    {"name": "LibHunt", "tier": 2, "url": "https://www.libhunt.com/", "color": "#2563EB", "line": "Projects by category"},
    {"name": "Devpost", "tier": 2, "url": "https://devpost.com/software", "color": "#003E54", "line": "Hackathon builds"},
    {"name": "Hackaday.io", "tier": 2, "url": "https://hackaday.io/discover", "color": "#1A1A1A", "line": "Hardware build logs"},
    {"name": "Indie Hackers", "tier": 2, "url": "https://www.indiehackers.com/", "color": "#0E2439", "line": "Side projects as businesses"},
    {"name": "Repoz", "tier": 2, "url": "https://www.repoz.io/", "color": "#7C3AED", "line": "Repos, one at a time"},
    {"name": "Your GitHub feed", "tier": 2, "url": "https://github.com/", "color": "#24292F", "line": "What people you follow star"},
    {"name": "Bluesky", "tier": 2, "url": "https://bsky.app/", "color": "#0A7AFF", "line": "Builders who left X"},
    {"name": "Fosstodon", "tier": 2, "url": "https://fosstodon.org/explore", "color": "#3E4A59", "line": "Open-source Mastodon"},
]

REDDIT = {"cgp": ("coolgithubprojects", "day"), "selfhosted": ("selfhosted", "day"),
          "sideproject": ("SideProject", "day"), "opensource": ("opensource", "week"),
          "beautiful": ("InternetIsBeautiful", "week")}


def get(url, timeout=20, limit=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit) if limit else r.read()


def text_of(fragment, n=220):
    """Plain text from an HTML fragment, cut to about n characters."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", fragment or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def ts_of(s):
    if not s:
        return NOW
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        try:
            return int(parsedate_to_datetime(s).timestamp())
        except (TypeError, ValueError):
            return NOW


def gh_card(url):
    m = re.match(r"https?://github\.com/([\w.-]+)/([\w.-]+)", url or "")
    if m and m.group(1) not in ("orgs", "sponsors", "topics", "features"):
        return f"https://opengraph.githubassets.com/1/{m.group(1)}/{m.group(2).removesuffix('.git')}"
    return None


def item(src, title, url, **kw):
    it = {"id": f"{src}:{kw.pop('key', url)}", "src": src, "title": title.strip(), "url": url,
          "text": "", "img": None, "discuss": None, "score": None, "comments": None, "ts": NOW}
    it.update({k: v for k, v in kw.items() if v is not None})
    if not it["img"]:
        it["img"] = gh_card(url)
    return it


# ---- sources ---------------------------------------------------------------

def hn():
    since = NOW - 3 * 86400
    q = f"https://hn.algolia.com/api/v1/search?tags=show_hn&numericFilters=points%3E25,created_at_i%3E{since}&hitsPerPage=40"
    hits = json.loads(get(q))["hits"]
    hits.sort(key=lambda h: -h.get("points", 0))
    out = []
    for h in hits:
        disc = f"https://news.ycombinator.com/item?id={h['objectID']}"
        title = re.sub(r"^Show HN:\s*", "", h["title"])
        out.append(item("hn", title, h.get("url") or disc, key=h["objectID"], discuss=disc,
                        text=text_of(h.get("story_text") or ""), score=h.get("points"),
                        comments=h.get("num_comments"), ts=h.get("created_at_i")))
    return out


ATOM = "{http://www.w3.org/2005/Atom}"


def reddit(src):
    """Reddit's logged-out page fragment: scores, comments, exact times, images.
    Answers this Mac; Reddit blocks GitHub's servers. RSS is the fallback."""
    raw = get(f"https://www.reddit.com/svc/shreddit/community-more-posts/top/?name={REDDIT[src][0]}&t={REDDIT[src][1]}")
    out = []
    for chunk in raw.decode("utf-8", "replace").split("<shreddit-post ")[1:16]:
        chunk = chunk.split("</shreddit-post>")[0]
        attr = lambda k: html.unescape((re.search(rf'\s{k}="([^"]*)"', chunk) or [None, ""])[1])
        if not attr("permalink") or re.search(r"\s(stickied|nsfw)\b", chunk.split(">")[0]):
            continue
        disc = "https://www.reddit.com" + attr("permalink")
        href = attr("content-href") or disc
        url = "https://www.reddit.com" + href if href.startswith("/") else href
        body = re.search(r'slot="text-body"[^>]*>(.{0,3000})', chunk, re.S)
        body = body.group(1) if body else ""
        img = None
        if re.search(r"\.(jpe?g|png|gif|webp)(\?|$)", url):
            img = url
        else:
            pic = re.search(r'src="(https://(?:preview|i)\.redd\.it/[^"]+)"', chunk)
            gh = re.search(r'href="(https://github\.com/[^"#?]+)"', body)
            img = html.unescape(pic.group(1)) if pic else gh_card(html.unescape(gh.group(1))) if gh else None
        n = lambda k: int(attr(k)) if attr(k).lstrip("-").isdigit() else None
        out.append(item(src, attr("post-title"), url, key=attr("id"), discuss=disc, text=text_of(body),
                        img=img, score=n("score"), comments=n("comment-count"),
                        ts=ts_of(attr("created-timestamp"))))
    return out


def reddit_rss(src):
    raw = get(f"https://www.reddit.com/r/{REDDIT[src][0]}/top/.rss?t={REDDIT[src][1]}&limit=15")
    out = []
    for e in ET.fromstring(raw).findall(f"{ATOM}entry"):
        disc = e.find(f"{ATOM}link").get("href")
        body = e.findtext(f"{ATOM}content") or ""
        link = re.search(r'<a href="([^"]+)">\[link\]</a>', body)
        url = html.unescape(link.group(1)) if link else disc
        md = re.search(r'(?s)<div class="md">(.*?)</div>', body)
        img = url if re.search(r"\.(jpe?g|png|gif|webp)(\?|$)", url) else None
        pid = re.search(r"/comments/([a-z0-9]+)", disc)
        out.append(item(src, e.findtext(f"{ATOM}title") or "", url, key="t3_" + pid.group(1) if pid else disc,
                        discuss=disc, text=text_of(md.group(1) if md else ""), img=img,
                        ts=ts_of(e.findtext(f"{ATOM}published"))))
    return out


def showdev():
    arts = json.loads(get("https://dev.to/api/articles?tag=showdev&top=4&per_page=25"))
    return [item("showdev", a["title"], a["url"], key=a["id"], text=a.get("description") or "",
                 img=a.get("cover_image") or a.get("social_image"), discuss=a["url"] + "#comments",
                 score=a.get("positive_reactions_count"), comments=a.get("comments_count"),
                 ts=ts_of(a.get("published_at"))) for a in arts]


def trending():
    page = get("https://github.com/trending").decode("utf-8", "replace")
    out = []
    for row in page.split('<article class="Box-row">')[1:20]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"]+)"', row)
        if not m:
            continue
        repo = m.group(1)
        desc = re.search(r'<p class="col-9[^"]*">(.*?)</p>', row, re.S)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', row)
        today = re.search(r"([\d,]+) stars? today", row)
        out.append(item("trending", repo.replace("/", " / "), f"https://github.com/{repo}", key=repo,
                        text=text_of(desc.group(1) if desc else ""),
                        score=int(today.group(1).replace(",", "")) if today else None,
                        lang=lang.group(1).strip() if lang else None, evergreen=True))
    return out


def hf():
    spaces = json.loads(get("https://huggingface.co/api/spaces?sort=trendingScore&limit=24"
                            "&expand[]=cardData&expand[]=likes&expand[]=createdAt"))
    out = []
    for s in spaces:
        card = s.get("cardData") or {}
        out.append(item("hf", card.get("title") or s["id"].split("/")[-1], f"https://huggingface.co/spaces/{s['id']}",
                        key=s["id"], text=card.get("short_description") or "", score=s.get("likes"),
                        img=f"https://cdn-thumbnails.huggingface.co/social-thumbnails/spaces/{s['id']}.png",
                        emoji=card.get("emoji"), evergreen=True))
    return out


def rss(src, url, n=12):
    root = ET.fromstring(get(url))
    out = []
    for i in root.iter("item"):
        link = (i.findtext("link") or "").strip()
        desc = i.findtext("description") or ""
        img = i.findtext("imageurl")
        enc = i.find("enclosure")
        media = i.find("{http://search.yahoo.com/mrss/}content")
        if not img and enc is not None and (enc.get("type") or "").startswith("image"):
            img = enc.get("url")
        if not img and media is not None:
            img = media.get("url")
        if not img:
            m = re.search(r'<img[^>]+src="([^"]+)"', desc)
            img = html.unescape(m.group(1)) if m else None
        title = i.findtext("plainTitle") or i.findtext("title") or ""
        out.append(item(src, html.unescape(title), link, text=text_of(desc), img=img,
                        ts=ts_of(i.findtext("pubDate") or i.findtext("updateDate")),
                        evergreen=src == "itch"))
        if len(out) >= n:
            break
    return out


def lobsters():
    stories = json.loads(get("https://lobste.rs/t/show.json"))
    return [item("lobsters", s["title"], s.get("url") or s["comments_url"], key=s["short_id"],
                 discuss=s["comments_url"], text=text_of(s.get("description") or ""),
                 score=s.get("score"), comments=s.get("comment_count"), ts=ts_of(s.get("created_at")))
            for s in stories[:15]]


FETCHERS = {
    "hn": hn, "showdev": showdev, "trending": trending, "hf": hf, "lobsters": lobsters,
    "selfhst": lambda: rss("selfhst", "https://selfh.st/rss/", 6),
    "console": lambda: rss("console", "https://console.dev/rss.xml", 6),
    "openalt": lambda: rss("openalt", "https://openalternative.co/rss.xml", 12),
    "itch": lambda: rss("itch", "https://itch.io/games/new-and-popular.xml", 12),
}


# ---- og:image for posts without a picture -----------------------------------

def og_lookup(url):
    try:
        page = get(url, timeout=8, limit=300_000).decode("utf-8", "replace")
    except Exception:
        return {"img": None, "t": NOW}
    m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
         or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page)
         or re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)', page))
    img = urllib.parse.urljoin(url, html.unescape(m.group(1))) if m else None
    return {"img": img, "t": NOW}


def add_og_images(items):
    cache = json.loads(OG_CACHE.read_text()) if OG_CACHE.exists() else {}
    want = [it["url"] for it in items
            if not it["img"] and it["url"] not in cache and "reddit.com" not in it["url"]
            and "news.ycombinator.com" not in it["url"]][:60]
    with ThreadPoolExecutor(8) as pool:
        for url, res in zip(want, pool.map(og_lookup, want)):
            cache[url] = res
    for it in items:
        if not it["img"] and cache.get(it["url"], {}).get("img"):
            it["img"] = cache[it["url"]]["img"]
    keep = {it["url"] for it in items}
    cache = {k: v for k, v in cache.items() if k in keep or NOW - v["t"] < 7 * 86400}
    OG_CACHE.write_text(json.dumps(cache, indent=0))


# ---- assemble ---------------------------------------------------------------

def write_rss(path, title, items):
    rows = []
    for it in items[:60]:
        desc = html.escape(it["text"] or "")
        if it["img"]:
            desc = html.escape(f'<img src="{it["img"]}"><br>') + desc
        rows.append(f"<item><title>{html.escape(it['title'])}</title><link>{html.escape(it['url'])}</link>"
                    f"<guid isPermaLink=\"false\">{html.escape(it['id'])}</guid>"
                    f"<category>{html.escape(SOURCES[it['src']]['name'])}</category>"
                    f"<pubDate>{format_datetime(datetime.fromtimestamp(it['seen'], timezone.utc))}</pubDate>"
                    f"<description>{desc}</description></item>")
    path.write_text('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
                    f"<title>{title}</title><link>{SITE}</link><description>{title}</description>"
                    + "".join(rows) + "</channel></rss>\n")


def main():
    DATA.mkdir(exist_ok=True)
    prev = json.loads(FEED.read_text()) if FEED.exists() else {"items": []}
    seen = {it["id"]: it.get("seen", NOW) for it in prev["items"]}
    status, items = {}, []

    def run(src):
        try:
            return src, FETCHERS[src](), None
        except Exception as exc:
            return src, None, f"{type(exc).__name__}: {exc}"[:200]

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(run, FETCHERS))
    for src in REDDIT:   # one at a time, spaced, so Reddit doesn't rate-limit
        results.append(run_reddit(src))
        time.sleep(6)

    for src, got, err in results:
        if got:
            status[src] = "ok"
            items += got
        else:
            status[src] = err or "empty"
            items += [it for it in prev["items"] if it["src"] == src]

    fresh, ids = [], set()
    for it in items:
        if it["id"] in ids:
            continue
        ids.add(it["id"])
        it["seen"] = seen.get(it["id"], NOW)
        if it.get("evergreen") or NOW - it["ts"] < SLOW.get(it["src"], 4) * 86400:
            fresh.append(it)

    add_og_images(fresh)
    feed = {"built": NOW, "sources": SOURCES, "links": LINKS, "status": status, "items": fresh}
    FEED.write_text(json.dumps(feed, ensure_ascii=False, separators=(",", ":")))
    by_new = sorted(fresh, key=lambda it: -it["seen"])
    write_rss(ROOT / "must-see.xml", "Builders: Must see", [i for i in by_new if SOURCES[i["src"]]["tier"] == 1])
    write_rss(ROOT / "if-you-have-time.xml", "Builders: If you have time", [i for i in by_new if SOURCES[i["src"]]["tier"] == 2])
    bad = {k: v for k, v in status.items() if v != "ok"}
    print(f"{len(fresh)} items; failed: {bad or 'none'}")
    return 0


def run_reddit(src):
    err = None
    for fetch in (reddit, reddit_rss):
        try:
            got = fetch(src)
            if got:
                return src, got, None
        except Exception as exc:
            err = f"{fetch.__name__} {type(exc).__name__}: {exc}"[:200]
        time.sleep(6)
    return src, None, err


if __name__ == "__main__":
    sys.exit(main())
