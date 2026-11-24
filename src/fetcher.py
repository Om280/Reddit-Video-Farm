"""Fetch and normalize Reddit posts (live JSON listings, or saved RSS files), and track processed posts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import quote, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from dotenv import load_dotenv

from . import ROOT, USED_POSTS


def load_env(path: Path = ROOT / ".env") -> None:
    """Load optional settings (e.g. REDDIT_COOKIE) from .env; real environment variables win."""
    load_dotenv(path, override=False, interpolate=False)  # cookies may contain '$'


load_env()

USER_AGENT = os.environ.get(
    "REDDIT_VIDEO_MAKER_UA",
    # Reddit's recommended format: <platform>:<app ID>:<version> (<description>)
    "windows:reddit-video-maker:v1.0 (personal RSS reader)",
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json, application/rss+xml;q=0.9, */*;q=0.8"}


def reddit_cookies() -> httpx.Cookies:
    """Optional REDDIT_COOKIE ("name=value; name2=value2") scoped to .reddit.com.

    A cookie jar (not a raw header) so httpx sends it to www/old.reddit.com, keeps it across
    Reddit's own redirects, and never forwards it to any other host."""
    jar = httpx.Cookies()
    raw = os.environ.get("REDDIT_COOKIE", "").strip()
    if not raw:
        return jar
    if "\n" in raw or "\r" in raw:
        raise FetchError("REDDIT_COOKIE must be a single line (copy only the Cookie header value).")
    for part in raw.removeprefix("Cookie:").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            jar.set(name, value, domain=".reddit.com")
    if not list(jar.jar):  # e.g. only one cookie's value was pasted, without its name
        raise FetchError(
            "REDDIT_COOKIE has no name=value pairs, so no cookie can be sent. Paste the whole Cookie request "
            "header from your browser, e.g. REDDIT_COOKIE=reddit_session=...; token_v2=...; loid=..."
        )
    return jar
TIMEOUT = httpx.Timeout(20.0)
REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com"}
SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{1,20}$")
POST_PATH_RE = re.compile(r"^/r/([A-Za-z0-9_]{2,21})/comments/([a-z0-9]+)(?:/[^/]*)?/?$", re.I)
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
# Narration smoothing: each pattern is something Edge TTS pauses on or reads awkwardly.
SPOKEN_FIXES = [
    (re.compile(r"(?<!\S)&(?!\S)"), "and"),  # standalone "&" only; "R&D" and "AT&T" stay
    (re.compile(r"\s*(?:\.{2,}|…)\s*"), ", "),  # ellipses
    (re.compile(r"\s*(?:-{2,}|[—–])\s*"), ", "),  # double hyphen, em/en dash
    (re.compile(r"([!?])[!?]+"), r"\1"),  # "?!?!" -> "?"
    (re.compile(r"\s+([,.!?;:])"), r"\1"),  # no space before punctuation
    (re.compile(r"([,;:])(?:\s*[,;:])+"), r"\1"),  # ", ," left by the fixes above
    (re.compile(r"^[\s,;:]+|[\s,;:]+$"), ""),  # no dangling punctuation at the ends
]
FEED_START = (b"<?xml", b"<feed", b"<rss")
LISTING_LIMIT = 15
MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")  # [text](url) -> text
MD_LINE_MARKER_RE = re.compile(r"(?m)^[ \t]{0,3}(?:#{1,6}|>+|[-*+]|\d+[.)])[ \t]+")  # headings, quotes, bullets
MD_EMPHASIS_RE = re.compile(r"\*{1,3}|_{2,3}|~~|`+")

ACRONYMS = {
    "AITA": "Am I the asshole",
    "WIBTA": "Would I be the asshole",
    "TIFU": "Today I messed up",
    "TL;DR": "Too long, didn't read",
    "TLDR": "Too long, didn't read",
    "NTA": "Not the asshole",
    "YTA": "You're the asshole",
    "ESH": "Everyone sucks here",
    "IMHO": "in my humble opinion",
    "IMO": "in my opinion",
    "OP": "original poster",
}
# Lookarounds instead of \b because "TL;DR" contains a non-word character.
ACRONYM_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(k) for k in sorted(ACRONYMS, key=len, reverse=True)) + r")(?!\w)",
    re.I,
)


class FetchError(RuntimeError):
    """Raised when a feed cannot be fetched or parsed."""


@dataclass
class Post:
    id: str
    subreddit: str
    author: str
    title: str  # display text (HTML stripped, not acronym-expanded)
    body: str  # narration-ready text
    permalink: str


# ---------- URLs ----------

def normalize_subreddit(name: str) -> str:
    """Strip an optional r/ prefix and validate the subreddit name."""
    name = re.sub(r"^/?r/", "", name.strip(), flags=re.I).strip("/")
    if not SUBREDDIT_RE.match(name):
        raise ValueError(f"Invalid subreddit name: {name!r}")
    return name


def subreddit_feed_url(name: str) -> str:
    """Top-of-the-week JSON listing URL for a subreddit."""
    return f"https://www.reddit.com/r/{quote(normalize_subreddit(name))}/top.json?t=week&limit={LISTING_LIMIT}"


def post_feed_url(url: str) -> str:
    """Convert a Reddit post URL into its JSON URL. Rejects non-Reddit or non-post URLs."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in REDDIT_HOSTS:
        raise ValueError(f"Not a reddit.com URL: {url!r}")
    m = POST_PATH_RE.match(parsed.path)
    if not m:
        raise ValueError(f"Not a Reddit post URL: {url!r}")
    sub, post_id = m.groups()
    return f"https://www.reddit.com/r/{sub}/comments/{post_id.lower()}/.json"


# ---------- Text ----------

def strip_html(html: str) -> str:
    """Remove tags, decode entities (including double-escaped ones like &amp;#39;), collapse whitespace."""
    text = unescape(BeautifulSoup(html or "", "html.parser").get_text(" "))
    return " ".join(text.replace("​", "").split())  # Reddit pads paragraphs with &#x200B;


def expand_acronyms(text: str) -> str:
    return ACRONYM_RE.sub(lambda m: ACRONYMS[m.group(1).upper()], text)


def clean_text(html: str) -> str:
    """Narration text: HTML/entities removed, URLs dropped, acronyms expanded,
    standalone & spoken as "and", pause-inducing punctuation smoothed, whitespace normalized."""
    text = " ".join(expand_acronyms(URL_RE.sub(" ", strip_html(html))).split())
    for pattern, repl in SPOKEN_FIXES:
        text = pattern.sub(repl, text)
    return text


def strip_markdown(text: str) -> str:
    """Reddit JSON selftext is Markdown: keep link text, drop formatting markers."""
    text = MD_LINK_RE.sub(r"\1", text)
    return MD_EMPHASIS_RE.sub("", MD_LINE_MARKER_RE.sub("", text))


def _make_post(post_id: str, subreddit: str, author: str, title: str, body: str, permalink: str) -> Post:
    """Build a Post, dropping a body that just repeats the title."""
    spoken_title = clean_text(title)
    if spoken_title and body.lower().startswith(spoken_title.lower()):
        body = body[len(spoken_title):].lstrip(" .:-")
    return Post(id=post_id, subreddit=subreddit, author=author,
                title=title, body=body, permalink=permalink)


def _check_payload(data: bytes, starts: tuple[bytes, ...], kind: str) -> bytes:
    """Reject block/challenge pages before parsing, with a clear message."""
    data = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not data.startswith(starts):
        snippet = data[:80].decode("utf-8", "replace").strip()
        raise FetchError(
            f"Got an HTML page instead of {kind} (likely a block or challenge page). "
            f"Starts with: {snippet!r}. Try another network, set REDDIT_COOKIE, or save the feed and use --file."
        )
    return data


def parse_listing(raw: bytes | str | dict | list, single: bool = False) -> list[Post]:
    """Parse a Reddit JSON listing (subreddit top.json, or a post's .json, whose first element is the post).

    Stickied and link-only (empty selftext) posts are skipped, except for a single requested post."""
    raw = _load_json(raw, "Reddit")
    listing = raw[0] if isinstance(raw, list) and raw else raw
    try:
        children = listing["data"]["children"]
    except (KeyError, TypeError):
        raise FetchError("Unexpected Reddit JSON: no data.children listing") from None
    posts = []
    for child in children:
        d = child.get("data") or {}
        if child.get("kind") != "t3" or not d.get("id"):
            continue
        if not single and (d.get("stickied") or not _has_text(d)):
            continue
        posts.append(_post_from_json(d))
    return posts


def parse_pullpush(raw: bytes | str | dict) -> list[Post]:
    """Parse a PullPush submission search ({"data": [submission, ...]}); text posts only."""
    raw = _load_json(raw, "PullPush")
    items = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise FetchError("Unexpected PullPush JSON: no data list")
    return [_post_from_json(d) for d in items
            if isinstance(d, dict) and d.get("id") and not d.get("stickied") and _has_text(d)]


def _load_json(raw: bytes | str | dict | list, source: str) -> dict | list:
    if not isinstance(raw, (bytes, str)):
        return raw
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    try:
        return json.loads(_check_payload(data, (b"{", b"["), "JSON"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FetchError(f"Malformed {source} JSON: {exc}") from exc


def _has_text(d: dict) -> bool:
    """A real self-post body: not empty, not removed by mods, not deleted by the author."""
    return unescape(d.get("selftext") or "").strip() not in ("", "[removed]", "[deleted]")


def _post_from_json(d: dict) -> Post:
    """One submission object (Reddit or PullPush; same field names) -> Post."""
    permalink = d.get("permalink", "")
    return _make_post(
        post_id=str(d["id"]),
        subreddit=d.get("subreddit", ""),
        author=d.get("author", ""),
        title=strip_html(d.get("title", "")),
        body=clean_text(strip_markdown(unescape(d.get("selftext") or ""))),
        permalink=f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink,
    )


def _body_html(content_html: str) -> str:
    """Reddit wraps self-text in <div class="md">; everything else is the 'submitted by' footer."""
    md = BeautifulSoup(content_html or "", "html.parser").find("div", class_="md")
    return str(md) if md else ""


def post_from_text(title: str, body: str) -> Post:
    """A Post from pasted text (GUI Paste Text tab). The body may be plain text or Reddit Markdown.
    The id is a content hash, so the same paste always maps to the same output file."""
    title = " ".join(title.split())
    if not title or not body.strip():
        raise ValueError("Enter both a title and a body.")
    post_id = "p" + hashlib.sha1(f"{title}\n{body.strip()}".encode("utf-8")).hexdigest()[:10]
    return _make_post(post_id, "", "", title, clean_text(strip_markdown(body)), "")


def parse_feed(xml: str | bytes) -> list[Post]:
    """Parse a saved Reddit Atom/RSS feed (--file) into posts. Comments (t1_ entries) are ignored."""
    data = _check_payload(xml.encode("utf-8") if isinstance(xml, str) else xml, FEED_START, "an RSS feed")
    feed = feedparser.parse(data)
    posts = []
    for e in feed.entries:
        raw_id = e.get("id", "")
        if not raw_id.startswith("t3_"):
            continue
        tags = e.get("tags") or []
        posts.append(_make_post(
            post_id=raw_id[3:],
            subreddit=tags[0]["term"] if tags else "",
            author=re.sub(r"^/?u/", "", e.get("author", "") or ""),
            title=strip_html(e.get("title", "")),
            body=clean_text(_body_html(e.content[0].value if e.get("content") else e.get("summary", ""))),
            permalink=e.get("link", ""),
        ))
    if feed.bozo and not posts:
        raise FetchError(f"Malformed feed XML: {feed.get('bozo_exception')}")
    return posts


# ---------- Network ----------

async def fetch_url(url: str) -> bytes:
    """GET a URL; turn failures into FetchError. The Reddit cookie jar only ever matches reddit.com."""
    host = urlparse(url).hostname or url
    try:
        async with httpx.AsyncClient(headers=HEADERS, cookies=reddit_cookies(), follow_redirects=True,
                                     timeout=TIMEOUT) as client:
            resp = await client.get(url)
    except httpx.RequestError as exc:
        raise FetchError(f"Network error fetching {url}: {exc}") from exc
    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After", "unknown")
        raise FetchError(f"{host} rate-limited this request (HTTP 429, Retry-After: {retry}). Wait and try again.")
    if resp.status_code >= 400:
        raise FetchError(f"{host} returned HTTP {resp.status_code} for {url}")
    return resp.content


def pullpush_url(subreddit: str) -> str:
    """PullPush archive search: highest-scored text posts of a subreddit (no time window)."""
    return ("https://api.pullpush.io/reddit/search/submission/"
            f"?subreddit={quote(normalize_subreddit(subreddit))}&sort=desc&sort_type=score&size={LISTING_LIMIT}")


async def fetch_posts(*, subreddit: str | None = None, url: str | None = None) -> list[Post]:
    """Fetch posts from a subreddit's weekly top JSON listing or a single post's JSON.
    If both reddit.com hosts fail for a subreddit, fall back to the PullPush archive."""
    feed_url = subreddit_feed_url(subreddit) if subreddit else post_feed_url(url or "")
    reddit_cookies()  # a malformed REDDIT_COOKIE fails once, up front, instead of per source
    errors = []
    for candidate in (feed_url, feed_url.replace("://www.reddit.com/", "://old.reddit.com/", 1)):
        try:
            posts = parse_listing(await fetch_url(candidate), single=bool(url))
        except FetchError as exc:
            errors.append(str(exc))
            continue
        if posts:
            return posts[:1] if url else posts
        errors.append(f"No text posts found at {candidate}")
    if subreddit and all("No text posts found" not in e for e in errors):  # blocked, not empty
        try:
            posts = parse_pullpush(await fetch_url(pullpush_url(subreddit)))
        except FetchError as exc:
            errors.append(f"PullPush fallback: {exc}")
        else:
            if posts:
                print(f"Reddit is unreachable from this network; using {len(posts)} posts from the PullPush archive.")
                return posts
            errors.append("PullPush fallback: no text posts found")
    raise FetchError("; ".join(errors))


# ---------- Processed-post tracking ----------

def load_used(path: Path = USED_POSTS) -> set[str]:
    """Return processed IDs; a missing, empty, or malformed file counts as empty."""
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except FileNotFoundError:
        return set()
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        print(f"Warning: {path} is unreadable or malformed; treating as empty.")
        return set()
    return {str(x) for x in data} if isinstance(data, list) else set()


def mark_used(post_id: str, path: Path = USED_POSTS) -> None:
    """Atomically add a post ID to the processed list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = sorted(load_used(path) | {post_id})
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ids, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
