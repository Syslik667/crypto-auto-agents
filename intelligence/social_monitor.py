"""Only publisher metadata leaves the collector. Social message text is transient."""
import base64
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlsplit

from .net import canonical, digest, failure, fetch, read_json
from .telegram_client import monitor

CATALOG = "AirdropAlert (каталог, НЕ подтверждение)"
SOURCES = {
    CATALOG: "https://airdropalert.com/feed/rssfeed",
    "Kraken (официальный блог)": "https://blog.kraken.com/feed/",
    "Coinbase (официальный блог)": "https://www.coinbase.com/blog/rss.xml",
    "Ethereum Foundation": "https://blog.ethereum.org/feed.xml",
    "Bitfinex (официальный блог)": "https://blog.bitfinex.com/feed/",
    "GitHub · go-ethereum": "https://github.com/ethereum/go-ethereum/releases.atom",
}
PUBLISHERS = {
    "airdropalert.com": "/airdrops/", "blog.kraken.com": "/",
    "www.coinbase.com": "/blog/", "coinbase.com": "/blog/",
    "blog.ethereum.org": "/", "blog.bitfinex.com": "/", "github.com": "/ethereum/go-ethereum/releases/tag/",
}
TRIGGERS = re.compile(r"\b(airdrops?|giveaway|learn.?and.?earn|learn\s*&\s*earn|quiz|rewards?|bonuses?|bonus|campaign|quest|promotions?|free token|earn btc|claim|incentive|distribution|launchpool|waitlist)\b", re.I)
RISK = re.compile(r"\b(deposit|staking|stake|purchase|buy|trading volume|trading competition|trade to|leverage|futures|referral|pay gas|gas fee|swap|mint)\b", re.I)
URL = re.compile(r"""https://[^\s<>"']+""")
PERSONAL = re.compile(r"[\w.+-]+@[\w.-]+|(?<!\w)@\w+|\b(?:\+?\d[\s()-]*){10,15}\b")


def links(text):
    return {u for raw in URL.findall(html.unescape(text)) if (u := canonical(raw.rstrip(".,);")))}


def publisher_link(url):
    p = urlsplit(url)
    prefix = PUBLISHERS.get(p.hostname)
    return bool(prefix and p.path.startswith(prefix) and len(p.path) > len(prefix))


def parse_feed(raw, source, *, news=False):
    root = ET.fromstring(raw)
    if root.tag.split("}")[-1] not in {"rss", "feed", "RDF"}:
        raise ValueError("Unexpected feed")
    out = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in {"item", "entry"}:
            continue
        fields = {c.tag.split("}")[-1]: "".join(c.itertext()).strip() for c in node}
        original = fields.get("link") or next((c.get("href", "") for c in node
                    if c.tag.split("}")[-1] == "link" and c.get("rel", "alternate") == "alternate"), "")
        url = canonical(original)
        title = " ".join(fields.get("title", "").split())[:240]
        if not url or not title or PERSONAL.search(title):
            continue
        if source in SOURCES and not publisher_link(url):
            continue
        raw_body = fields.get("description") or fields.get("summary") or fields.get("content", "")
        body = " ".join(re.sub(r"<[^>]*>", " ", html.unescape(raw_body)).split())[:8000]
        content = title + " " + body
        if not news and not TRIGGERS.search(content):
            continue
        host = urlsplit(url).hostname
        feed_host = urlsplit(SOURCES.get(source, "")).hostname
        official = source != CATALOG and host and host.removeprefix("www.") == (feed_host or "").removeprefix("www.")
        out.append(dict(id=digest(source + "\n" + original), title=title, url=url, source=source,
                        published=(fields.get("pubDate") or fields.get("published") or fields.get("updated", ""))[:70],
                        summary=body, refs=links(raw_body), official=bool(official), mentions=set(),
                        status="Возможны платные условия" if RISK.search(content) else "Условия не проверены"))
        if len(out) >= 50:
            break
    return out


def telegram(health=None):
    return monitor(links, health if health is not None else {}, read_json)


def local_telegram():
    filename = os.environ.get("TELEGRAM_SCAN_PATH")
    if not filename:
        return None
    path = Path(filename).resolve()
    if os.environ.get("GITHUB_ACTIONS") == "true" or path.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("Local snapshot must stay outside the repository and Actions")
    with path.open("rb") as handle:
        raw = handle.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Snapshot too large")
    data = json.loads(raw)
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(data["generated_utc"])
    if not dt.timedelta(0) <= age <= dt.timedelta(days=1):
        raise ValueError("Snapshot expired")
    return {u for u in data["links"] if isinstance(u, str) and canonical(u) == u and publisher_link(u)}


def reddit():
    client = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    ua = os.environ.get("REDDIT_USER_AGENT")
    if not all((client, secret, ua)):
        return None
    basic = base64.b64encode(f"{client}:{secret}".encode()).decode()
    token = read_json("https://www.reddit.com/api/v1/access_token",
                      headers={"Authorization": "Basic " + basic, "User-Agent": ua},
                      data=b"grant_type=client_credentials")["access_token"]
    listing = read_json("https://oauth.reddit.com/r/ethereum/new?limit=25",
                        headers={"Authorization": "Bearer " + token, "User-Agent": ua})
    result = set()
    for item in listing["data"]["children"]:
        post = item["data"]
        if post.get("subreddit_type") == "public" and post.get("subreddit", "").lower() == "ethereum":
            result.update(links(post.get("url", "") + " " + post.get("selftext", "")))
    return result


def discord():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel = os.environ.get("DISCORD_PUBLIC_ANNOUNCEMENT_ID")
    if not token or not channel:
        return None
    if not re.fullmatch(r"\d{15,20}", channel):
        raise ValueError("Invalid channel")
    endpoint = f"https://discord.com/api/v10/channels/{channel}"
    auth = {"Authorization": "Bot " + token}
    info = read_json(endpoint, headers=auth)
    if info.get("type") != 5 or not info.get("guild_id"):
        raise ValueError("Announcement channel required")
    guild = read_json("https://discord.com/api/v10/guilds/" + str(info["guild_id"]), headers=auth)
    if "DISCOVERABLE" not in guild.get("features", []) or guild.get("id") != info["guild_id"]:
        raise ValueError("Public guild required")
    role = next(r for r in guild["roles"] if r["id"] == guild["id"])
    perms = int(role["permissions"])
    for overwrite in info["permission_overwrites"]:
        if overwrite["id"] == guild["id"] and overwrite["type"] == 0:
            perms = (perms & ~int(overwrite["deny"])) | int(overwrite["allow"])
    if not perms & (1 << 10):
        raise ValueError("Channel hidden from everyone")
    result = set()
    for post in read_json(endpoint + "/messages?limit=25", headers=auth):
        # Only announcements explicitly published to following servers.
        if post.get("channel_id") == channel and post.get("flags", 0) & 1:
            result.update(links(post.get("content", "")))
            for embed in post.get("embeds", []):
                result.update(links(embed.get("url", "")))
    return result


def x_public(posts):
    # Explicit organizational post URLs or links discovered in official feeds.
    candidates = set(os.environ.get("X_PUBLIC_POSTS", "").split()) | set(posts)
    urls = sorted(u for u in candidates if re.fullmatch(
        r"https://(?:x|twitter)\.com/(?:ethereum|krakenfx|coinbase)/status/\d{1,22}", u, re.I))
    if not urls:
        return None
    result = set()
    for url in urls[:3]:
        data = read_json("https://publish.twitter.com/oembed?" + urlencode({"url": url, "omit_script": 1}))
        if data.get("type") == "rich" and data.get("html"):
            result.add(url)
            result.update(links(data["html"]))
    return result


def resolve_article(url):
    """Look up the slug through the publisher's public posts API. Require an exact permalink match."""
    p = urlsplit(url)
    publisher = {"blog.kraken.com": "Kraken (официальный блог)",
                 "blog.bitfinex.com": "Bitfinex (официальный блог)"}.get(p.hostname)
    slug = p.path.rsplit("/", 1)[-1]
    if not publisher or not re.fullmatch(r"[a-z0-9-]{3,180}", slug):
        return None
    query = urlencode({"slug": slug, "status": "publish", "per_page": 1,
                       "_fields": "link,date_gmt,title,content,status"})
    rows = read_json(f"https://{p.hostname}/wp-json/wp/v2/posts?{query}")
    for row in rows:
        if (row.get("status") != "publish" or canonical(row.get("link", "")) != url
                or row["content"].get("protected")):
            continue
        title = " ".join(html.unescape(re.sub("<[^>]*>", " ", row["title"]["rendered"])).split())[:240]
        if not title or PERSONAL.search(title):
            continue
        body = row["content"]["rendered"]
        return dict(id=digest(url), source=publisher, url=url, official=True, title=title,
                    published=row["date_gmt"][:10], refs=links(body), mentions=set(),
                    summary=" ".join(html.unescape(re.sub("<[^>]*>", " ", body)).split())[:8000],
                    status="Условия не проверены")
    return None


def collect(health):
    news = []
    for name, url in SOURCES.items():
        try:
            items = parse_feed(fetch(url), name, news=True)
            news.extend(items)
            health[name] = f"OK · публикаций {len(items)}"
        except Exception as exc:
            health[name] = failure(exc)
    public_posts = {u for item in news if item["official"] for u in item["refs"] if "/status/" in u}
    social_links = {}
    for name, reader in [("Telegram · закрепления", lambda: telegram(health)), ("Telegram · локальный сканер", local_telegram), ("Reddit", reddit),
                         ("Discord", discord), ("X · oEmbed", lambda: x_public(public_posts))]:
        try:
            found = reader()
            if found is None:
                health[name] = "Нет настроенного доступа или публичных публикаций"
                continue
            social_links[name] = found
        except Exception as exc:
            health[name] = failure(exc)
    known = {p["url"] for p in news}
    social_urls = set().union(*social_links.values())
    directory_urls = {u for p in news if p["source"] == CATALOG for u in p["refs"]}
    lookups = [u for u in sorted((social_urls | directory_urls) - known, key=lambda u: (u not in social_urls, u))
               if urlsplit(u).hostname in {"blog.kraken.com", "blog.bitfinex.com"}]
    for url in lookups[:3]:
        try:
            article = resolve_article(url)
            if article:
                news.append(article)
        except Exception as exc:
            health["Проверка ссылок · " + urlsplit(url).hostname] = failure(exc)
    for name, found in social_links.items():
        hits = 0
        for item in news:
            if item["official"] and (item["url"] in found or (name.startswith("X") and item["refs"] & found & public_posts)):
                item["mentions"].add(name)
                hits += 1
        health[name] = f"OK · совпадений с официальными публикациями {hits}"
    return news
