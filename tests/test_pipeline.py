"""Offline tests: no network, Edge TTS, Chromium, or FFmpeg required."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main
from src import fetcher, tts, video
from src.fetcher import Post


@pytest.fixture(autouse=True)
def _no_real_reddit_cookie(monkeypatch):
    """fetcher loads the developer's .env at import; tests must not see (or send) a real cookie."""
    monkeypatch.delenv("REDDIT_COOKIE", raising=False)

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <category term="AmItheAsshole" label="r/AmItheAsshole"/>
  <title>top scoring links : AmItheAsshole</title>
  <entry>
    <author><name>/u/throwaway_42</name><uri>https://www.reddit.com/user/throwaway_42</uri></author>
    <category term="AmItheAsshole" label="r/AmItheAsshole"/>
    <content type="html">&lt;!-- SC_OFF --&gt;&lt;div class=&quot;md&quot;&gt;&lt;p&gt;AITA for skipping my sister&amp;#39;s wedding?&lt;/p&gt;
&lt;p&gt;So   my sister &amp;amp; I    had a fight. See https://example.com/pic.jpg for proof.&lt;/p&gt;
&lt;p&gt;TL;DR: I stayed home. IMO it was fair.&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt; &amp;#32; submitted by &amp;#32; &lt;a href=&quot;https://www.reddit.com/user/throwaway_42&quot;&gt; /u/throwaway_42 &lt;/a&gt; &lt;br/&gt; &lt;span&gt;&lt;a href=&quot;https://www.reddit.com/r/AmItheAsshole/comments/1abc2de/aita/&quot;&gt;[link]&lt;/a&gt;&lt;/span&gt;</content>
    <id>t3_1abc2de</id>
    <link href="https://www.reddit.com/r/AmItheAsshole/comments/1abc2de/aita_for_skipping/"/>
    <updated>2026-11-20T10:00:00+00:00</updated>
    <title>AITA for skipping my sister&amp;#39;s wedding?</title>
  </entry>
  <entry>
    <author><name>/u/linkposter</name></author>
    <category term="AmItheAsshole" label="r/AmItheAsshole"/>
    <content type="html">&amp;#32; submitted by &amp;#32; &lt;a href=&quot;https://www.reddit.com/user/linkposter&quot;&gt; /u/linkposter &lt;/a&gt;</content>
    <id>t3_9zz9zz</id>
    <link href="https://www.reddit.com/r/AmItheAsshole/comments/9zz9zz/link/"/>
    <title>A link post</title>
  </entry>
  <entry>
    <author><name>/u/commenter</name></author>
    <content type="html">&lt;div class=&quot;md&quot;&gt;&lt;p&gt;NTA&lt;/p&gt;&lt;/div&gt;</content>
    <id>t1_comment1</id>
    <title>comment</title>
  </entry>
</feed>
"""

# Shape of https://www.reddit.com/r/<sub>/top.json (Reddit HTML-escapes title/selftext in JSON).
LISTING = {
    "kind": "Listing",
    "data": {"children": [
        {"kind": "t3", "data": {
            "id": "sticky1", "stickied": True, "subreddit": "AmItheAsshole", "author": "AutoModerator",
            "title": "Monthly rules thread", "selftext": "Read the rules.", "permalink": "/r/AmItheAsshole/comments/sticky1/x/"}},
        {"kind": "t3", "data": {
            "id": "1abc2de", "stickied": False, "subreddit": "AmItheAsshole", "author": "throwaway_42",
            "title": "AITA for skipping my sister&amp;#39;s wedding?",
            "selftext": ("AITA for skipping my sister's wedding?\n\nSo **my sister** &amp; I had a "
                         "[huge](https://example.com/pic) fight.\n\n&amp;#x200B;\n\n# TL;DR\n\n- I stayed home... IMO it was fair."),
            "permalink": "/r/AmItheAsshole/comments/1abc2de/aita_for_skipping/", "score": 4321}},
        {"kind": "t3", "data": {
            "id": "link1", "stickied": False, "subreddit": "AmItheAsshole", "author": "linkposter",
            "title": "A link post", "selftext": "", "url": "https://example.com", "permalink": "/r/AmItheAsshole/comments/link1/x/"}},
    ]},
}
LISTING_BYTES = __import__("json").dumps(LISTING).encode()
# Shape of https://www.reddit.com/r/<sub>/comments/<id>/.json: [post listing, comments listing]
SINGLE_LINK_POST = [
    {"kind": "Listing", "data": {"children": [LISTING["data"]["children"][2]]}},
    {"kind": "Listing", "data": {"children": [{"kind": "t1", "data": {"id": "c1", "body": "NTA"}}]}},
]


# ---------- JSON parsing (live fetching) ----------

def test_parse_listing_extracts_and_cleans():
    posts = fetcher.parse_listing(LISTING_BYTES)
    assert [p.id for p in posts] == ["1abc2de"]  # stickied and link-only posts skipped
    p = posts[0]
    assert (p.subreddit, p.author) == ("AmItheAsshole", "throwaway_42")
    assert p.title == "AITA for skipping my sister's wedding?"
    assert p.permalink == "https://www.reddit.com/r/AmItheAsshole/comments/1abc2de/aita_for_skipping/"
    # title not repeated; markdown, URL, zero-width space gone; & spoken; acronyms expanded
    assert p.body == "So my sister and I had a huge fight. Too long, didn't read I stayed home, in my opinion it was fair."


def test_parse_listing_single_post_keeps_link_post():
    posts = fetcher.parse_listing(SINGLE_LINK_POST, single=True)
    assert [(p.id, p.body) for p in posts] == [("link1", "")]
    assert fetcher.parse_listing(SINGLE_LINK_POST) == []  # same post skipped in a subreddit listing


@pytest.mark.parametrize("raw, match", [
    (b"<!DOCTYPE html><html>blocked</html>", "HTML page instead of JSON"),
    (b"{not json", "Malformed Reddit JSON"),
    (b'{"error": 403}', "no data.children"),
    (b"[]", "no data.children"),
])
def test_parse_listing_errors(raw, match):
    with pytest.raises(fetcher.FetchError, match=match):
        fetcher.parse_listing(raw)


@pytest.mark.parametrize("md, expected", [
    ("[click here](https://x.com) now", "click here now"),
    ("![img](https://x.com/a.png)", "img"),
    ("**bold** and *it* and ~~old~~ and `code` and __u__", "bold and it and old and code and u"),
    ("# Heading\n> quoted\n- item\n2. second", "Heading\nquoted\nitem\nsecond"),
    ("snake_case stays and 5 - 3", "snake_case stays and 5 - 3"),
])
def test_strip_markdown(md, expected):
    assert fetcher.strip_markdown(md) == expected


# ---------- RSS parsing (--file) ----------

def test_parse_feed_extracts_fields():
    posts = fetcher.parse_feed(FEED)
    assert [p.id for p in posts] == ["1abc2de", "9zz9zz"]  # t1_ comment skipped
    p = posts[0]
    assert p.subreddit == "AmItheAsshole"
    assert p.author == "throwaway_42"
    assert p.title == "AITA for skipping my sister's wedding?"
    assert p.permalink == "https://www.reddit.com/r/AmItheAsshole/comments/1abc2de/aita_for_skipping/"
    # duplicated title removed, HTML/URL/footer gone, acronyms expanded
    assert p.body == "So my sister and I had a fight. See for proof. Too long, didn't read: I stayed home. in my opinion it was fair."
    assert "submitted by" not in p.body and "<" not in p.body


def test_link_post_has_empty_body():
    assert fetcher.parse_feed(FEED)[1].body == ""


def test_malformed_feed_raises():
    with pytest.raises(fetcher.FetchError):
        fetcher.parse_feed(b"<feed><entry>broken")


# ---------- Text cleaning ----------

def test_clean_text_html_entities_whitespace():
    assert fetcher.clean_text("<p>Hello&nbsp;&amp;\n\n  <b>world</b>&#39;s</p>") == "Hello and world 's"


@pytest.mark.parametrize("raw, expected", [
    ("AITA?", "Am I the asshole?"),
    ("aita for this", "Am I the asshole for this"),
    ("WIBTA if", "Would I be the asshole if"),
    ("TIFU by", "Today I messed up by"),
    ("tl;dr: no", "Too long, didn't read: no"),
    ("Clearly NTA, not YTA. ESH.", "Clearly Not the asshole, not You're the asshole. Everyone sucks here."),
    ("OP said IMHO and imo", "original poster said in my humble opinion and in my opinion"),
])
def test_acronym_expansion(raw, expected):
    assert fetcher.clean_text(raw) == expected


def test_acronyms_whole_words_only():
    text = "STOP the opera, Mesh and Tiara. IMOGEN, NTAS"
    assert fetcher.clean_text(text) == text


def test_urls_removed():
    assert fetcher.clean_text("see https://x.com/a?b=1 and www.foo.org now") == "see and now"


# ---------- URLs ----------

@pytest.mark.parametrize("name", ["tifu", "r/tifu", "/r/AmItheAsshole/", "R/ask_science"])
def test_valid_subreddits(name):
    assert fetcher.normalize_subreddit(name) == name.strip("/").split("/")[-1]


@pytest.mark.parametrize("name", ["", "a", "bad name", "x" * 30, "../etc", "tifu?t=all", "_lead"])
def test_invalid_subreddits(name):
    with pytest.raises(ValueError):
        fetcher.normalize_subreddit(name)


def test_subreddit_feed_url():
    assert fetcher.subreddit_feed_url("r/tifu") == "https://www.reddit.com/r/tifu/top.json?t=week&limit=15"


@pytest.mark.parametrize("url", [
    "https://www.reddit.com/r/AmItheAsshole/comments/abc123/example/",
    "https://reddit.com/r/AmItheAsshole/comments/abc123/example",
    "http://old.reddit.com/r/AmItheAsshole/comments/ABC123/",
    "https://www.reddit.com/r/AmItheAsshole/comments/abc123/example/?utm_source=share",
])
def test_post_feed_url(url):
    assert fetcher.post_feed_url(url) == "https://www.reddit.com/r/AmItheAsshole/comments/abc123/.json"


@pytest.mark.parametrize("url", [
    "https://evil.com/r/tifu/comments/abc123/x/",
    "https://reddit.com.evil.com/r/tifu/comments/abc123/x/",
    "https://www.reddit.com/r/tifu/",
    "https://www.reddit.com/user/someone/",
    "ftp://reddit.com/r/tifu/comments/abc123/",
    "https://www.reddit.com/r/tifu/comments/abc123/x/extra/",
    "not a url",
])
def test_post_feed_url_rejects(url):
    with pytest.raises(ValueError):
        fetcher.post_feed_url(url)


# ---------- Processed-post tracking ----------

def test_load_used_missing_and_empty(tmp_path):
    assert fetcher.load_used(tmp_path / "nope.json") == set()
    (tmp_path / "empty.json").write_text("")
    assert fetcher.load_used(tmp_path / "empty.json") == set()


def test_load_used_malformed_recovers(tmp_path):
    path = tmp_path / "used.json"
    path.write_text("{not json")
    assert fetcher.load_used(path) == set()
    fetcher.mark_used("abc", path)
    assert json.loads(path.read_text()) == ["abc"]


def test_mark_used_creates_dir_and_appends(tmp_path):
    path = tmp_path / "sub" / "used.json"
    fetcher.mark_used("b", path)
    fetcher.mark_used("a", path)
    fetcher.mark_used("a", path)
    assert fetcher.load_used(path) == {"a", "b"}
    assert not list(path.parent.glob("*.tmp"))


def _post(pid):
    return Post(id=pid, subreddit="tifu", author="u", title=f"title {pid}", body="body", permalink="")


async def test_only_successful_posts_marked(tmp_path):
    used = tmp_path / "used.json"
    fetcher.mark_used("old", used)

    async def fake_render(post, voice):
        if post.id == "bad":
            raise RuntimeError("ffmpeg exploded")
        return tmp_path / f"{post.id}.mp4"

    outputs, failures = await main.process_posts([_post("good"), _post("bad"), _post("good2")], "v", used, fake_render)
    assert [o.name for o in outputs] == ["good.mp4", "good2.mp4"]
    assert failures == [("bad", "ffmpeg exploded")]
    assert fetcher.load_used(used) == {"old", "good", "good2"}


async def test_run_skips_used_posts(tmp_path, monkeypatch):
    used = tmp_path / "used.json"
    fetcher.mark_used("1abc2de", used)
    monkeypatch.setattr(main, "USED_POSTS", used)

    async def fake_fetch(**kw):
        return fetcher.parse_feed(FEED)

    seen = []

    async def fake_process(posts, voice, used_path, render=None, privacy=None):
        assert used_path == used
        seen.extend(p.id for p in posts)
        return [], []

    monkeypatch.setattr(fetcher, "fetch_posts", fake_fetch)
    monkeypatch.setattr(main, "process_posts", fake_process)
    assert await main.run(main.parse_args(["--subreddit", "AmItheAsshole", "--limit", "5"])) == 0
    assert seen == ["9zz9zz"]


# ---------- CLI ----------

@pytest.mark.parametrize("argv", [
    [],
    ["--subreddit", "tifu", "--url", "https://www.reddit.com/r/tifu/comments/abc123/x/"],
    ["--subreddit", "tifu", "--limit", "0"],
    ["--subreddit", "bad name"],
    ["--url", "https://example.com/r/tifu/comments/abc123/"],
])
def test_cli_rejects(argv):
    with pytest.raises(SystemExit) as exc:
        main.parse_args(argv)
    assert exc.value.code == 2


def test_cli_accepts():
    args = main.parse_args(["--subreddit", "r/tifu", "--limit", "3"])
    assert (args.subreddit, args.limit, args.voice) == ("tifu", 3, "en-US-AvaMultilingualNeural")


def test_cli_rejects_missing_feed_file(tmp_path):
    with pytest.raises(SystemExit):
        main.parse_args(["--file", str(tmp_path / "missing.xml")])


async def test_run_from_feed_file(tmp_path, monkeypatch):
    feed = tmp_path / "feed.xml"
    feed.write_text(FEED, encoding="utf-8")
    monkeypatch.setattr(main, "USED_POSTS", tmp_path / "used.json")
    seen = []

    async def fake_process(posts, voice, used_path, render=None, privacy=None):
        seen.extend(p.id for p in posts)
        return [], []

    monkeypatch.setattr(main, "process_posts", fake_process)
    assert await main.run(main.parse_args(["--file", str(feed), "--limit", "2"])) == 0
    assert seen == ["1abc2de", "9zz9zz"]


async def test_fetch_falls_back_to_old_reddit(monkeypatch):
    tried = []

    async def fake_fetch_url(url):
        tried.append(url)
        if "www." in url:
            raise fetcher.FetchError("Reddit returned HTTP 404")
        return LISTING_BYTES

    monkeypatch.setattr(fetcher, "fetch_url", fake_fetch_url)
    posts = await fetcher.fetch_posts(subreddit="AmItheAsshole")
    assert [p.id for p in posts] == ["1abc2de"]  # JSON listing: sticky and link-only posts skipped
    assert tried == ["https://www.reddit.com/r/AmItheAsshole/top.json?t=week&limit=15",
                     "https://old.reddit.com/r/AmItheAsshole/top.json?t=week&limit=15"]


async def test_fetch_reports_both_failures(monkeypatch):
    async def fake_fetch_url(url):
        raise fetcher.FetchError(f"HTTP 404 for {url}")

    monkeypatch.setattr(fetcher, "fetch_url", fake_fetch_url)
    with pytest.raises(fetcher.FetchError, match="www.reddit.com.*old.reddit.com"):
        await fetcher.fetch_posts(url="https://www.reddit.com/r/tifu/comments/abc123/x/")


def test_card_has_no_score():
    from src import card
    page = card.render_html(fetcher.parse_feed(FEED)[0])
    assert "Score" not in page and "upvote" not in page.lower() and "{{" not in page
    assert "AITA for skipping my sister&#x27;s wedding?" in page


# ---------- ASS captions ----------

@pytest.mark.parametrize("seconds, expected", [
    (0, "0:00:00.00"),
    (1.234, "0:00:01.23"),
    (1.235001, "0:00:01.24"),
    (59.999, "0:01:00.00"),
    (61.5, "0:01:01.50"),
    (3599.995, "1:00:00.00"),
    (3725.07, "1:02:05.07"),
    (-1, "0:00:00.00"),
])
def test_ass_timestamp(seconds, expected):
    assert tts.ass_timestamp(seconds) == expected


def _words(n):
    return [{"text": f"w{i}", "start": i * 0.5, "duration": 0.4} for i in range(n)]


def _dialogues(ass):
    return [line.split(",", 9) for line in ass.splitlines() if line.startswith("Dialogue:")]


def _text(event):
    return event[9].removeprefix(tts.POP_IN)


def test_build_ass_one_word_per_cue_from_word_boundaries():
    events = _dialogues(tts.build_ass(_words(3), offset=0))
    assert [(e[1], e[2], _text(e)) for e in events] == [
        ("0:00:00.00", "0:00:00.50", "w0"),  # held until the next word starts
        ("0:00:00.50", "0:00:01.00", "w1"),
        ("0:00:01.00", "0:00:01.40", "w2"),  # last word ends with its own duration
    ]
    assert all(e[9].startswith(tts.POP_IN) for e in events)


def test_build_ass_no_overlaps_and_skips_zero_length():
    words = [{"text": "a", "start": 0.0, "duration": 0.3}, {"text": "b", "start": 0.001, "duration": 0.3},
             {"text": "c", "start": 0.7, "duration": 0.2}]
    events = _dialogues(tts.build_ass(words, offset=0))
    assert [_text(e) for e in events] == ["b", "c"]  # "a" would be 0 cs long
    for prev, nxt in zip(events, events[1:]):
        assert prev[2] <= nxt[1]


def test_build_ass_offset_title_plus_gap():
    title_dur = 2.5
    events = _dialogues(tts.build_ass(_words(2), offset=title_dur + video.GAP_SECONDS))
    assert (events[0][1], events[0][2]) == ("0:00:02.80", "0:00:03.30")
    assert (events[1][1], events[1][2]) == ("0:00:03.30", "0:00:03.70")


def test_build_ass_header_and_escaping():
    ass = tts.build_ass([{"text": "{\\b0}hi", "start": 0, "duration": 0.2}], 0)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert _text(_dialogues(ass)[0]) == "(\\\\b0)hi"


def test_trim_window_and_shift():
    words = [{"text": "a", "start": 0.12, "duration": 0.2}, {"text": "b", "start": 0.5, "duration": 0.3}]
    start, end = tts.trim_window(words)
    assert start == pytest.approx(0.07) and end == pytest.approx(0.95)
    assert [w["start"] for w in tts.shift_words(words, start)] == [0.05, 0.43]
    assert tts.trim_window([]) == (0.0, None)
    assert tts.trim_window([{"text": "x", "start": 0.01, "duration": 0.1}])[0] == 0.0


def test_tts_defaults():
    assert (tts.DEFAULT_VOICE, tts.DEFAULT_RATE, tts.DEFAULT_PITCH) == ("en-US-AvaMultilingualNeural", "+22%", "+2Hz")


# ---------- Video helpers ----------

def test_sanitize_and_output_path(tmp_path):
    assert video.sanitize_filename("AITA: for ../../etc/passwd?!") == "AITA-for-etc-passwd"
    assert video.sanitize_filename("???") == "post"
    assert video.output_path("Hi there", "abc123", tmp_path) == tmp_path / "Hi-there-abc123.mp4"
    with pytest.raises(ValueError):
        video.output_path("x", "../evil", tmp_path)


def test_escape_filter_path_windows():
    escaped = video.escape_filter_path(Path("C:/Users/me/it's [x].ass"))
    # level 1: C\: and it\'s ; level 2 doubles those backslashes and escapes [ ]
    assert escaped.endswith(r"C\\:/Users/me/it\\\'s \[x\].ass")


def test_pick_background(tmp_path):
    with pytest.raises(video.VideoError, match="Add at least one .mp4"):
        video.pick_background(tmp_path)
    (tmp_path / "bg.MP4").write_bytes(b"x")
    assert video.pick_background(tmp_path).name == "bg.MP4"
    (tmp_path / "minecraft_parkour.mp4").write_bytes(b"x")
    assert video.pick_background(tmp_path).name == "minecraft_parkour.mp4"


def test_background_start():
    assert video.background_start(5.0, 10.0) is None  # too short -> loop
    assert video.background_start(11.0, 10.0) == 0.0  # fits, but no room beyond the 2 s margin
    for _ in range(50):
        assert 0.0 <= video.background_start(1852.0, 40.0) <= 1810.0


def test_build_command_seek_vs_loop(tmp_path):
    paths = [tmp_path / n for n in ["bg.mp4", "c.png", "t.mp3", "b.mp3", "s.ass"]]
    seek = video.build_command(*paths, title_dur=2.0, total=10.3, out=tmp_path / "o.mp4", bg_start=123.4567)
    bg = seek.index(str(paths[0]))
    assert seek[bg - 5:bg] == ["-ss", "123.457", "-t", "10.300", "-i"] and "-stream_loop" not in seek
    loop = video.build_command(*paths, title_dur=2.0, total=10.3, out=tmp_path / "o.mp4")
    bg = loop.index(str(paths[0]))
    assert loop[bg - 3:bg] == ["-stream_loop", "-1", "-i"]


def test_build_command_single_filter_graph(tmp_path):
    cmd = video.build_command(*(tmp_path / n for n in ["bg.mp4", "c.png", "t.mp3", "b.mp3", "s.ass"]),
                              title_dur=2.0, total=10.3, out=tmp_path / "o.mp4")
    assert cmd.count("-filter_complex") == 1
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "enable='lt(t,2.000)'" in graph and "atrim=end=2.300" in graph
    assert "concat=n=2:v=0:a=1" in graph


# ---------- YouTube upload (mocked) ----------

from src import uploader


def test_shorts_title_caps_and_suffix():
    assert uploader.shorts_title("AITA for <this>?") == "AITA for this? #Shorts"
    long = uploader.shorts_title("x" * 300)
    assert len(long) == 100 and long.endswith("… #Shorts")


def test_shorts_metadata():
    body = uploader.shorts_metadata("My title", "tifu", "someone", "unlisted")
    snip = body["snippet"]
    assert snip["title"] == "My title #Shorts" and snip["categoryId"] == "24"
    assert "r/tifu" in snip["description"] and "u/someone" in snip["description"]
    assert snip["description"].endswith("#Shorts #RedditStories")
    assert body["status"]["privacyStatus"] == "unlisted"
    assert uploader.shorts_metadata("t", "s", "a")["status"]["privacyStatus"] == "private"
    with pytest.raises(ValueError):
        uploader.shorts_metadata("t", "s", "a", "friends-only")


def test_upload_short_mocked(tmp_path, monkeypatch):
    from unittest import mock
    import googleapiclient.discovery, googleapiclient.http

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"fake mp4")
    youtube = mock.MagicMock()
    request = youtube.videos.return_value.insert.return_value
    request.next_chunk.side_effect = [(mock.Mock(), None), (None, {"id": "abc123XYZ"})]
    monkeypatch.setattr(uploader, "get_credentials", lambda: "creds")
    monkeypatch.setattr(googleapiclient.discovery, "build", mock.Mock(return_value=youtube))
    monkeypatch.setattr(googleapiclient.http, "MediaFileUpload", mock.Mock())

    assert uploader.upload_short(vid, "Title", "tifu", "me", "public") == "abc123XYZ"
    googleapiclient.discovery.build.assert_called_once_with("youtube", "v3", credentials="creds", cache_discovery=False)
    kwargs = youtube.videos.return_value.insert.call_args.kwargs
    assert kwargs["part"] == "snippet,status"
    assert kwargs["body"]["status"]["privacyStatus"] == "public"
    assert kwargs["body"]["snippet"]["title"] == "Title #Shorts"


def test_upload_short_errors(tmp_path):
    with pytest.raises(uploader.UploadError, match="Video not found"):
        uploader.upload_short(tmp_path / "missing.mp4", "t", "s", "a")
    with pytest.raises(uploader.UploadError, match="client_secrets.json"):
        uploader.get_credentials(secrets=tmp_path / "client_secrets.json", token=tmp_path / "token.json")


async def test_process_posts_uploads_and_keeps_render_on_upload_failure(tmp_path, monkeypatch):
    used = tmp_path / "used.json"
    calls = []

    def fake_upload(path, title, subreddit, author, privacy):
        calls.append((path.name, title, subreddit, privacy))
        if "bad" in title:
            raise uploader.UploadError("quota exceeded")
        return "yt1"

    async def fake_render(post, voice):
        return tmp_path / f"{post.id}.mp4"

    monkeypatch.setattr(uploader, "upload_short", fake_upload)
    outputs, failures = await main.process_posts([_post("ok"), _post("bad")], "v", used, fake_render, privacy="unlisted")
    assert len(outputs) == 2
    assert calls == [("ok.mp4", "title ok", "tifu", "unlisted"), ("bad.mp4", "title bad", "tifu", "unlisted")]
    assert failures == [("bad", "upload: quota exceeded")]
    assert fetcher.load_used(used) == {"ok", "bad"}  # rendered successfully, so not re-rendered later


def test_cli_upload_flags():
    args = main.parse_args(["--subreddit", "tifu", "--upload", "--privacy", "public"])
    assert args.upload and args.privacy == "public"
    assert main.parse_args(["--subreddit", "tifu"]).privacy == "private"
    with pytest.raises(SystemExit):
        main.parse_args(["--subreddit", "tifu", "--privacy", "secret"])


# ---------- Narration smoothing & block-page detection ----------

@pytest.mark.parametrize("raw, expected", [
    ("me & my sister", "me and my sister"),
    ("Tom &amp; Jerry", "Tom and Jerry"),
    ("Tom &amp;amp; Jerry", "Tom and Jerry"),  # double-escaped in RSS
    ("R&D at AT&T & co", "R&D at AT&T and co"),  # only standalone & is spoken
    ("&", "and"),
    ("I waited... and nothing", "I waited, and nothing"),
    ("I waited…nothing", "I waited, nothing"),
    ("She left -- forever", "She left, forever"),
    ("She left—forever", "She left, forever"),
    ("pages 3–4", "pages 3, 4"),
    ("Really?!?! No!!!", "Really? No!"),
    ("...so anyway", "so anyway"),
    ("Wait, ... what", "Wait, what"),
    ("well-known re-do", "well-known re-do"),  # single hyphens untouched
])
def test_spoken_smoothing(raw, expected):
    assert fetcher.clean_text(raw) == expected


def test_card_title_keeps_ampersand():
    # display text keeps "&"; only narration says "and"
    assert fetcher.strip_html("Me &amp; you") == "Me & you"


@pytest.mark.parametrize("page", [
    b"<!DOCTYPE html><html><body>blocked</body></html>",
    b"<html><head><title>Reddit - Prove your humanity</title></head></html>",
    b"",
])
def test_parse_feed_rejects_html_pages(page):
    with pytest.raises(fetcher.FetchError, match="HTML page instead of an RSS feed"):
        fetcher.parse_feed(page)


def test_parse_feed_accepts_bom_and_whitespace():
    assert len(fetcher.parse_feed(b"\xef\xbb\xbf\n  " + FEED.encode())) == 2


async def test_fetch_url_mocked_http(monkeypatch):
    """Real fetch_url/fetch_posts code paths over a mocked HTTP transport."""
    import functools, httpx
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["Accept"]))
        if request.url.host == "www.reddit.com":
            return httpx.Response(200, html="<!DOCTYPE html><html>challenge</html>")
        return httpx.Response(200, content=LISTING_BYTES)

    monkeypatch.setattr(fetcher.httpx, "AsyncClient",
                        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))
    posts = await fetcher.fetch_posts(subreddit="AmItheAsshole")
    assert [p.id for p in posts] == ["1abc2de"]
    assert [h for _, h in seen] == [fetcher.HEADERS["Accept"]] * 2
    assert [u.split("/r/")[0] for u, _ in seen] == ["https://www.reddit.com", "https://old.reddit.com"]


async def test_fetch_url_mocked_http_429(monkeypatch):
    import functools, httpx
    handler = lambda request: httpx.Response(429, headers={"Retry-After": "60"})
    monkeypatch.setattr(fetcher.httpx, "AsyncClient",
                        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))
    with pytest.raises(fetcher.FetchError, match="rate-limited.*Retry-After: 60"):
        await fetcher.fetch_posts(subreddit="tifu")


def test_user_agent_is_descriptive_not_a_browser():
    ua = fetcher.HEADERS["User-Agent"]
    if "REDDIT_VIDEO_MAKER_UA" not in __import__("os").environ:
        assert ua == "windows:reddit-video-maker:v1.0 (personal RSS reader)"
    assert "Mozilla" not in ua and "Chrome" not in ua


# ---------- Optional REDDIT_COOKIE session ----------

def _mock_reddit(monkeypatch, handler):
    import functools, httpx
    monkeypatch.setattr(fetcher.httpx, "AsyncClient",
                        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))


def _recording_handler(seen, www_status=404):
    import httpx

    def handler(request):
        seen.append((request.url.host, request.headers.get("cookie")))
        if request.url.host == "www.reddit.com":
            return httpx.Response(www_status)
        return httpx.Response(200, content=LISTING_BYTES)
    return handler


async def test_no_cookie_header_without_env(monkeypatch):
    monkeypatch.delenv("REDDIT_COOKIE", raising=False)
    seen = []
    _mock_reddit(monkeypatch, _recording_handler(seen))
    await fetcher.fetch_posts(subreddit="tifu")
    assert seen == [("www.reddit.com", None), ("old.reddit.com", None)]


async def test_cookie_header_sent_when_env_set(monkeypatch):
    monkeypatch.setenv("REDDIT_COOKIE", "reddit_session=abc$123; token_v2=xyz")
    seen = []
    _mock_reddit(monkeypatch, _recording_handler(seen))
    await fetcher.fetch_posts(subreddit="tifu")
    for host, cookie in seen:
        assert host.endswith("reddit.com")
        assert sorted(cookie.split("; ")) == ["reddit_session=abc$123", "token_v2=xyz"]
    assert len(seen) == 2


async def test_cookie_kept_on_reddit_redirect_but_never_sent_elsewhere(monkeypatch):
    import httpx
    monkeypatch.setenv("REDDIT_COOKIE", "reddit_session=abc")
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers.get("cookie")))
        if request.url.path.startswith("/r/tifu/"):
            return httpx.Response(302, headers={"Location": "https://www.reddit.com/r/TIFU/top.json?t=week"})
        if request.url.host == "www.reddit.com":
            return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
        return httpx.Response(200, content=LISTING_BYTES)

    _mock_reddit(monkeypatch, handler)
    await fetcher.fetch_url("https://www.reddit.com/r/tifu/top.json?t=week")
    assert len(seen) == 3
    assert seen[0] == ("www.reddit.com", "reddit_session=abc")
    assert seen[1] == ("www.reddit.com", "reddit_session=abc")  # same-site redirect keeps the session
    assert seen[2] == ("evil.example", None)  # cross-site redirect never gets it


def test_cookie_must_be_single_line(monkeypatch):
    monkeypatch.setenv("REDDIT_COOKIE", "a=1\nb=2")
    with pytest.raises(fetcher.FetchError, match="single line"):
        fetcher.reddit_cookies()


def test_cookie_accepts_pasted_header_prefix(monkeypatch):
    monkeypatch.setenv("REDDIT_COOKIE", "Cookie: a=1; b=2")
    jar = fetcher.reddit_cookies()
    assert (jar.get("a", domain=".reddit.com"), jar.get("b", domain=".reddit.com")) == ("1", "2")


def test_load_env_file_and_real_env_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("REDDIT_COOKIE=from_file$x\nRVM_TEST_ONLY=1\n", encoding="utf-8")
    monkeypatch.delenv("REDDIT_COOKIE", raising=False)
    monkeypatch.delenv("RVM_TEST_ONLY", raising=False)
    fetcher.load_env(env)
    assert __import__("os").environ["REDDIT_COOKIE"] == "from_file$x"  # no $ interpolation
    monkeypatch.setenv("REDDIT_COOKIE", "from_env")
    fetcher.load_env(env)
    assert __import__("os").environ["REDDIT_COOKIE"] == "from_env"
    monkeypatch.delenv("RVM_TEST_ONLY", raising=False)


# ---------- PullPush fallback (mocked) ----------

PULLPUSH = {"data": [
    {"id": "pp1", "subreddit": "tifu", "author": "alice", "title": "TIFU by microwaving foil &amp; sparks",
     "selftext": "So **this** happened... [pic](https://x.com/a.png) it was bad", "score": 900,
     "permalink": "/r/tifu/comments/pp1/tifu_by/"},
    {"id": "pp2", "subreddit": "tifu", "author": "bob", "title": "Removed one", "selftext": "[removed]"},
    {"id": "pp3", "subreddit": "tifu", "author": "[deleted]", "title": "Deleted one", "selftext": "[deleted]"},
    {"id": "pp4", "subreddit": "tifu", "author": "carol", "title": "Link post", "selftext": ""},
    {"id": "pp5", "subreddit": "tifu", "author": "dan", "title": "Second real one", "selftext": "Body &amp; more"},
]}


def test_parse_pullpush_filters_and_cleans():
    posts = fetcher.parse_pullpush(__import__("json").dumps(PULLPUSH))
    assert [p.id for p in posts] == ["pp1", "pp5"]
    p = posts[0]
    assert (p.subreddit, p.author) == ("tifu", "alice")
    assert p.title == "TIFU by microwaving foil & sparks"  # display keeps "&"
    assert p.body == "So this happened, pic it was bad"  # same markdown + clean_text pipeline
    assert p.permalink == "https://www.reddit.com/r/tifu/comments/pp1/tifu_by/"
    assert posts[1].body == "Body and more"


@pytest.mark.parametrize("raw", [b'{"error": "x"}', b'[]', b"<html>down</html>"])
def test_parse_pullpush_errors(raw):
    with pytest.raises(fetcher.FetchError):
        fetcher.parse_pullpush(raw)


def test_pullpush_url():
    assert fetcher.pullpush_url("r/tifu") == (
        "https://api.pullpush.io/reddit/search/submission/?subreddit=tifu&sort=desc&sort_type=score&size=15")


async def test_blocked_reddit_falls_back_to_pullpush(monkeypatch):
    import httpx
    monkeypatch.setenv("REDDIT_COOKIE", "reddit_session=abc")
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers.get("cookie")))
        if request.url.host == "www.reddit.com":
            return httpx.Response(403, html="<!DOCTYPE html><html>blocked</html>")
        if request.url.host == "old.reddit.com":
            return httpx.Response(200, html="<!DOCTYPE html><html>challenge</html>")
        return httpx.Response(200, json=PULLPUSH)

    _mock_reddit(monkeypatch, handler)
    posts = await fetcher.fetch_posts(subreddit="tifu")
    assert [p.id for p in posts] == ["pp1", "pp5"]
    assert [h for h, _ in seen] == ["www.reddit.com", "old.reddit.com", "api.pullpush.io"]
    assert seen[2][1] is None  # Reddit session cookie never sent to PullPush


async def test_no_pullpush_when_reddit_works_or_for_single_urls(monkeypatch):
    import httpx
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if "/comments/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, content=LISTING_BYTES)

    _mock_reddit(monkeypatch, handler)
    await fetcher.fetch_posts(subreddit="AmItheAsshole")
    assert hosts == ["www.reddit.com"]
    hosts.clear()
    with pytest.raises(fetcher.FetchError):
        await fetcher.fetch_posts(url="https://www.reddit.com/r/tifu/comments/abc123/x/")
    assert "api.pullpush.io" not in hosts


async def test_all_sources_failing_reports_each(monkeypatch):
    import httpx
    _mock_reddit(monkeypatch, lambda request: httpx.Response(503))
    with pytest.raises(fetcher.FetchError) as exc:
        await fetcher.fetch_posts(subreddit="tifu")
    msg = str(exc.value)
    assert "www.reddit.com returned HTTP 503" in msg and "old.reddit.com returned HTTP 503" in msg
    assert "PullPush fallback: api.pullpush.io returned HTTP 503" in msg


# ---------- Paste Text mode ----------

def test_post_from_text():
    post = fetcher.post_from_text("  My   story ", "**Me** & my cat...\n\nThe end -- really")
    assert (post.title, post.subreddit, post.author, post.permalink) == ("My story", "", "", "")
    assert post.body == "Me and my cat, The end, really"
    assert post.id.startswith("p") and post.id.isalnum() and len(post.id) == 11
    assert fetcher.post_from_text("My story", "**Me** & my cat...\n\nThe end -- really").id == post.id
    assert video.output_path(post.title, post.id).name == f"My-story-{post.id}.mp4"
    for title, body in [("", "body"), ("title", "   \n")]:
        with pytest.raises(ValueError, match="title and a body"):
            fetcher.post_from_text(title, body)


def test_card_and_upload_metadata_without_reddit_source():
    from src import card
    post = fetcher.post_from_text("Pasted", "Body")
    page = card.render_html(post)
    assert "u/" not in page and "Reddit" in page  # no empty u/ line; generic label instead of r/
    desc = uploader.shorts_metadata("Pasted", "", "")["snippet"]["description"]
    assert "r/" not in desc and "u/" not in desc and desc.endswith("#Shorts #RedditStories")
    assert "Story from r/tifu by u/me." in uploader.shorts_metadata("t", "tifu", "me")["snippet"]["description"]


def test_cookie_value_without_name_is_an_error(monkeypatch):
    monkeypatch.setenv("REDDIT_COOKIE", "just-a-bare-value-with-no-name")
    with pytest.raises(fetcher.FetchError, match="no name=value pairs") as exc:
        fetcher.reddit_cookies()
    assert "bare-value" not in str(exc.value)  # never echo the secret


# ---------- GPU (NVENC) encoding with CPU fallback ----------

def test_build_command_encoder_args(tmp_path):
    paths = [tmp_path / n for n in ["bg.mp4", "c.png", "t.mp3", "b.mp3", "s.ass"]]
    gpu = video.build_command(*paths, title_dur=2.0, total=10.3, out=tmp_path / "o.mp4", encoder=video.NVENC)
    i = gpu.index("-c:v")
    assert gpu[i:i + 8] == ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22", "-pix_fmt", "yuv420p"]
    cpu = video.build_command(*paths, title_dur=2.0, total=10.3, out=tmp_path / "o.mp4")
    i = cpu.index("-c:v")
    assert cpu[i:i + 8] == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]
    assert cpu.count("-c:v") == 1 and cpu[-1] == str(tmp_path / "o.mp4")


def _fake_render_env(monkeypatch, listed, nvenc_fails):
    encoders = []

    def fake_run(cmd):
        enc = cmd[cmd.index("-c:v") + 1]
        encoders.append(enc)
        if enc == "h264_nvenc" and nvenc_fails:
            raise video.VideoError("ffmpeg failed (exit 1):\nCannot load nvcuda.dll")
        return ""

    monkeypatch.setattr(video, "nvenc_listed", lambda: listed)
    monkeypatch.setattr(video, "_run", fake_run)
    monkeypatch.setattr(video, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(video, "duration", lambda p: 1800.0)
    monkeypatch.setattr(video, "verify", lambda p, d: None)
    return encoders


@pytest.mark.parametrize("listed, fails, expected", [
    (True, False, ["h264_nvenc"]),  # GPU works
    (True, True, ["h264_nvenc", "libx264"]),  # listed but no usable GPU -> CPU retry
    (False, False, ["libx264"]),  # FFmpeg build without NVENC
])
def test_render_encoder_selection(tmp_path, monkeypatch, listed, fails, expected):
    encoders = _fake_render_env(monkeypatch, listed, fails)
    p = [tmp_path / n for n in ["c.png", "t.mp3", "b.mp3", "s.ass"]]
    video.render(*p, title_dur=2.0, body_dur=5.0, out=tmp_path / "o.mp4", background=tmp_path / "bg.mp4")
    assert encoders == expected


def test_nvenc_listed_parses_encoder_list(monkeypatch):
    video.nvenc_listed.cache_clear()
    monkeypatch.setattr(video, "_run", lambda cmd: " V....D h264_nvenc  NVIDIA NVENC H.264 encoder\n")
    assert video.nvenc_listed() is True
    video.nvenc_listed.cache_clear()
    monkeypatch.setattr(video, "_run", lambda cmd: " V....D libx264  libx264 H.264\n")
    assert video.nvenc_listed() is False
    video.nvenc_listed.cache_clear()


# ---------- GUI: consecutive Generate clicks ----------

async def test_gui_consecutive_generations_skip_used_posts(tmp_path, monkeypatch):
    import gui
    used = tmp_path / "used_posts.json"
    rendered = []

    async def fake_fetch(subreddit=None, url=None):
        return [_post("first"), _post("second"), _post("third")]  # Reddit always returns the same top list

    async def fake_render(post, voice, background=None):
        if post.id == "second" and "second" not in [r for r, _ in rendered]:
            rendered.append(("second", "failed"))
            raise RuntimeError("ffmpeg crashed")
        rendered.append((post.id, background.name))
        return tmp_path / f"{post.id}.mp4"

    monkeypatch.setattr(fetcher, "fetch_posts", fake_fetch)
    click = lambda: gui.generate_next("tifu", None, None, "voice", tmp_path / "bg.mp4", used_path=used, render=fake_render)

    assert (await click()).startswith("Done") and fetcher.load_used(used) == {"first"}
    assert (await click()) == "Error: ffmpeg crashed"  # failed render is not recorded...
    assert fetcher.load_used(used) == {"first"}
    assert (await click()).startswith("Done")  # ...so the next click retries it
    assert (await click()).startswith("Done")
    assert [r for r in rendered if r[1] != "failed"] == [("first", "bg.mp4"), ("second", "bg.mp4"), ("third", "bg.mp4")]
    assert "Reset History" in await click()  # all fetched posts used
    used.unlink()  # what the Reset History button does
    assert (await click()).startswith("Done") and rendered[-1][0] == "first"
