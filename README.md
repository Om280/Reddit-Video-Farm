# reddit-video-maker

A Python CLI that turns public Reddit posts into narrated, captioned **9:16 vertical videos** (1080×1920, H.264, 30 fps, AAC). It needs no Reddit API credentials, no paid APIs and no API keys.

```
Reddit JSON / saved RSS ─► clean text ─► Edge TTS (title + body, word timings) ─► ASS captions
                                     │                                   │
                Playwright title card (PNG) ─────────► FFmpeg composite ◄─┘ ─► output/*.mp4
```

## Features

- Reads posts from **Reddit's public `.json` endpoints**, either a subreddit's top 15 posts of the week or one post URL, and falls back to `old.reddit.com` if `www.reddit.com` fails. Stickied and link-only posts are skipped. If Reddit blocks your network, subreddit fetches fall back to the public PullPush archive. It can also load a saved RSS `.xml` feed offline, and the desktop app can render pasted text with no fetch at all. No OAuth is involved.
- Cleans the text: strips HTML, decodes entities, normalizes whitespace, removes URLs, avoids reading the title twice, and expands Reddit acronyms (AITA, TIFU, TL;DR, NTA, …) as whole words regardless of case.
- Narrates with **Edge TTS**: the default voice is `en-US-AvaMultilingualNeural` at `+22%` rate and `+2Hz` pitch, for a fast Shorts-style delivery. The whole body is synthesized in one streaming pass, which also collects the `WordBoundary` timings. Leading and trailing silence is then trimmed.
- Burns in **one-word captions**, one per `WordBoundary` cue. Each word is bold white with a thick black outline, centered, and pops in with a slight zoom. Each word stays on screen until the next one starts, so captions never overlap.
- Renders a dark-mode, Reddit-style **title card** with headless Chromium and keeps it transparent around the card.
- Composes the video with **FFmpeg** in one filter graph: the title narration plays over the card, then 0.3 s of silence, then the body narration with captions. The background is a center-cropped clip that starts at a **random point** each run, so the same footage isn't reused.
- **Optional NVIDIA GPU encoding:** if FFmpeg lists `h264_nvenc`, videos are encoded on the GPU (`-preset p4 -cq 22`), which needs an NVIDIA GPU (GTX 900 series or newer) and a current driver. If NVENC is missing or fails at runtime (no NVIDIA GPU, old driver, encoder busy), the render is retried automatically on the CPU with `libx264 -preset veryfast -crf 21`. The background is opened with `-ss <offset> -t <length>` before `-i`, so FFmpeg jumps straight to the chosen point in a long clip without decoding the footage before it.
- Checks every output with `ffprobe`: resolution, codec, pixel format, frame rate, audio stream and duration.
- Optionally **uploads to YouTube as a Short** (YouTube Data API v3, OAuth), with a title ending in `#Shorts`, credits in the description, and a privacy setting.
- Includes a **Windows desktop app** (`gui.py`, CustomTkinter) that you open with a double-click on `launch_app.bat`.
- Tracks processed posts so the same post is never rendered twice. A post is marked done only after its video is generated.

## Architecture

| Module | Responsibility |
|---|---|
| `main.py` | `argparse` CLI, post loading (`load_posts`), the per-post pipeline (`render_post`), and orchestration (`process_posts`), which marks a post used only on success and optionally uploads it. |
| `gui.py` | CustomTkinter desktop app. The work runs on a daemon thread, so the window never freezes, and it reuses the same pipeline functions as the CLI. |
| `src/__init__.py` | Project paths, resolved from the project root, so the CLI works from any working directory. |
| `src/fetcher.py` | URL building and validation, async HTTP (`httpx`), JSON listing parsing (live) and Atom/RSS parsing (`feedparser`, for `--file`), Markdown/HTML cleanup (`beautifulsoup4`), acronym expansion, and the `data/used_posts.json` tracking with atomic writes. |
| `src/tts.py` | Edge TTS synthesis in one pass, collecting `WordBoundary` events (100 ns ticks → seconds). Also trims silence, shifting the word timings to match, and writes the one-word ASS captions. |
| `src/uploader.py` | YouTube Shorts upload: OAuth with a cached token, metadata formatting, and a resumable upload. |
| `src/card.py` | Fills `templates/card.html` (every value HTML-escaped) and screenshots it with Playwright into a transparent PNG. |
| `src/video.py` | Background selection and the random seek point, the FFmpeg/ffprobe calls (`subprocess.run`, argument lists, no shell), filter-path escaping, safe output filenames and output verification. |

### Timeline

```
0 ──── title audio (title card visible) ──── T ── 0.3 s silence ── T+0.3 ──── body audio + captions ──── end
```

The body captions are shifted by `T + 0.3`, where `T` is the title audio duration measured with `ffprobe`. The title audio is padded and trimmed to exactly `T + 0.3`, so the audio and the captions use the same offset.

## Project tree

```
reddit-video-maker/
├── main.py               # CLI
├── gui.py                # desktop app
├── launch_app.bat        # double-click launcher for gui.py
├── pyproject.toml
├── requirements.txt
├── README.md
├── .gitignore
├── .env.example          # template for the optional REDDIT_COOKIE (copy to .env)
├── src/
│   ├── __init__.py
│   ├── fetcher.py
│   ├── tts.py
│   ├── card.py
│   ├── uploader.py
│   └── video.py
├── templates/
│   └── card.html
├── assets/backgrounds/   # put your .mp4 background clips here
├── credentials/          # YouTube OAuth files (gitignored, you create it)
├── data/                 # used_posts.json (created automatically)
├── output/               # rendered videos; output/temp/ holds intermediates
└── tests/
    └── test_pipeline.py
```

## Prerequisites

- **Python 3.11+**. On Windows, use a 64-bit Python: Playwright's `greenlet` dependency has no 32-bit wheels.
- **FFmpeg and ffprobe** on your `PATH`. They are installed separately (see below).
- **Internet access**, which Reddit and Edge TTS both need.
- At least one **background `.mp4`** in `assets/backgrounds/`.

## Setup

Create and activate a virtual environment.

Windows (PowerShell):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install the dependencies and the `reddit-video-maker` command. `requirements.txt` is the single source of dependency versions, and `pyproject.toml` reads it.

```bash
python -m pip install --upgrade pip
pip install -e .
```

To install only the libraries without the command, run `pip install -r requirements.txt` instead.

Install Chromium for Playwright:

```bash
playwright install chromium
```

### Installing FFmpeg / ffprobe

| OS | Command |
|---|---|
| Windows | `winget install --id Gyan.FFmpeg -e`, then open a **new** terminal so `PATH` updates |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt update && sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` (requires RPM Fusion) |

Check that both are available:

```bash
ffmpeg -version
ffprobe -version
```

## Usage

```bash
python main.py --subreddit AmItheAsshole
python main.py --subreddit tifu --limit 5
python main.py --url "https://www.reddit.com/r/AmItheAsshole/comments/abc123/example/"
python main.py --subreddit tifu --voice en-US-AvaMultilingualNeural --limit 3
python main.py --file saved_feed.xml --limit 2   # offline fallback
python main.py --subreddit tifu --upload --privacy unlisted   # render + upload as a Short

# after `pip install -e .`
reddit-video-maker --subreddit AmItheAsshole
```

| Option | Description |
|---|---|
| `--subreddit` | Subreddit name, with or without `r/`. Uses `https://www.reddit.com/r/<name>/top.json?t=week&limit=15`, skipping stickied and link-only posts. |
| `--url` | One post URL on `reddit.com`, `www.reddit.com` or `old.reddit.com`, fetched as `<post>/.json`. A link-only post is still rendered, with a short fallback narration. |
| `--file` | A saved Reddit RSS/Atom `.xml` file. Use it when Reddit blocks live requests: open `https://www.reddit.com/r/<name>/top/.rss?t=week` in your browser, save the page, and pass it here. |
| `--voice` | Edge TTS voice. Run `edge-tts --list-voices` to see them all. Default: `en-US-AvaMultilingualNeural`. Rate `+22%` and pitch `+2Hz` are fixed (`DEFAULT_RATE` / `DEFAULT_PITCH` in `src/tts.py`). |
| `--limit` | Maximum number of new posts to render. Must be at least 1. Default: 1. |
| `--upload` | Upload each rendered video to YouTube as a Short. See [YouTube upload setup](#youtube-upload-setup). |
| `--privacy` | `private` (default), `unlisted` or `public`. Applies only with `--upload`. |

Exactly one of `--subreddit`, `--url` or `--file` is required. Videos are saved as `output/<sanitized-title>-<post_id>.mp4`.

Each video also gets a small `.json` sidecar with the post's title, subreddit and author, which the desktop app uses for uploads.

Exit codes: `0` means every selected post rendered (and uploaded, with `--upload`). `1` means a fetch error, no unprocessed posts, or a failed render or upload. `2` means invalid arguments. If only the upload fails, the post still counts as rendered and won't be rendered again; upload that video from the desktop app instead.

## When Reddit blocks your network

Some ISPs, VPNs and cloud IP addresses get an HTTP 403/404 or a block page from Reddit's public endpoints.

**Automatic fallback: PullPush.** For `--subreddit` (and a subreddit typed into the desktop app), if both `www.reddit.com` and `old.reddit.com` fail, the fetcher tries the public [PullPush](https://pullpush.io) Reddit archive: `https://api.pullpush.io/reddit/search/submission/?subreddit=<name>&sort=desc&sort_type=score&size=15`.
- Posts with an empty, `[removed]` or `[deleted]` body are skipped, and the rest go through the same Markdown and narration cleanup.
- The archive can lag behind Reddit. This query has no time window, so it returns the subreddit's top-scored posts overall, not just this week's; already-rendered posts are still skipped.
- PullPush is a third-party service with its own rate limits, and your `REDDIT_COOKIE` is never sent to it.
- Single post URLs don't use this fallback.

If PullPush is unavailable too, use one of these:

0. **Paste Text (desktop app, no network).** Copy a post's title and body from your browser into the **Paste Text** tab and click **Generate Video**. See [Desktop app](#desktop-app-windows).
1. **`--file` (no setup).** Open `https://www.reddit.com/r/<subreddit>/top/.rss?t=week` in your browser and save the page as `.xml`. Then run `python main.py --file feed.xml`, or use **Browse .xml File** in the desktop app.
2. **Your own session cookie (`REDDIT_COOKIE`, optional).** The fetcher can send your logged-in Reddit session with its requests, so subreddit feeds load automatically. This works in the CLI and in the desktop app.
   1. Copy the template: `Copy-Item .env.example .env` (PowerShell) or `cp .env.example .env`.
   2. In your browser, while logged in to reddit.com, open DevTools (F12) and go to **Network**. Reload the page, select any request to `www.reddit.com`, and copy the value of the **`Cookie`** request header.
   3. Paste it on one line: `REDDIT_COOKIE=reddit_session=...; token_v2=...`. A real `REDDIT_COOKIE` environment variable takes precedence over `.env`.

   The cookie is sent only to `reddit.com` hosts, including Reddit's own redirects, and is never forwarded to other sites or printed in errors. **Treat it like a password:** anyone with it can act as your Reddit account. `.env` is gitignored, so never commit it or share it. The session expires eventually (copy a fresh one when fetching starts failing again), and logging out of Reddit invalidates it. It's meant for personal use with your own account; follow Reddit's terms.
3. **The official Reddit API (OAuth).** This is the most reliable long-term route for automation. Register a free personal "script" app at <https://www.reddit.com/prefs/apps>. It isn't built into this tool yet.

## Desktop app (Windows)

Double-click **`launch_app.bat`** in the project folder. It starts `venv\Scripts\pythonw.exe gui.py` without a console window. You can also run `python gui.py` from an activated venv.

- **Reddit tab:** type a subreddit, *or* paste one post URL, *or* click **Browse .xml File** to choose a saved feed. Use exactly one of the three.
- **Paste Text tab:** enter a **Title** and paste a **Body** (plain text or Reddit Markdown) to render without any network fetch. The text gets the same narration cleanup. The card shows no subreddit or author, and pasted stories aren't recorded in `data/used_posts.json`, so you can render the same text again.
- **Voice:** Ava, Emma or Andrew, the US English multilingual neural voices.
- **Background:** any `.mp4` in `assets/backgrounds/`. Defaults to `minecraft_parkour.mp4`.
- **Upload privacy:** `private`, `unlisted` or `public`.
- **Generate Video** renders one new post. A progress bar and a status line show what's happening, and the window stays responsive.
- **Check Video** opens the most recent video in `output/` in your default player.
- **Upload Video** uploads the most recent video to YouTube after you confirm. Without credentials set up, it shows the setup steps below instead.

FFmpeg must be on your `PATH` for the app too. If you installed it while a terminal or Explorer was already open, sign out and back in, or restart Explorer.

## YouTube upload setup

You only need to do this once.

1. Open [Google Cloud Console](https://console.cloud.google.com/), create a project, and enable **YouTube Data API v3** under *APIs & Services → Library*.
2. Under *APIs & Services → OAuth consent screen*, choose **External** and fill in the app name and your email. Under **Test users**, add the Google account that owns your YouTube channel.
3. Under *APIs & Services → Credentials*, choose **Create credentials → OAuth client ID** and pick **Desktop app** as the application type. Download the JSON file.
4. Save that file as `credentials/client_secrets.json` in the project folder. Create the folder if it doesn't exist.
5. Run an upload (`--upload`, or **Upload Video** in the app). The first time, a browser window opens so you can sign in and allow access. The token is then cached in `credentials/token.json`, so later uploads run without asking.

Uploads are titled `<post title> #Shorts` (100 characters at most), use category 24 (Entertainment), and have a description crediting `r/<subreddit>` and `u/<author>` with `#Shorts #RedditStories`. Privacy defaults to **private**, so you can review a video in YouTube Studio before publishing it.

`credentials/` is gitignored. Never commit `client_secrets.json` or `token.json`. If they leak, delete the OAuth client in Google Cloud Console and create a new one.

> Google limits uploads with a daily API quota, and each upload costs about 1,600 units of the default 10,000. Until Google verifies your app, unverified apps' uploads may be locked to private. See Google's [audit and quota extension](https://support.google.com/youtube/contact/yt_api_form) process.

Requests identify the app honestly with the User-Agent format Reddit recommends, `windows:reddit-video-maker:v1.0 (personal RSS reader)`, and send an `Accept: application/json, application/rss+xml;q=0.9, */*;q=0.8` header. To use a different User-Agent, set `REDDIT_VIDEO_MAKER_UA`, for example to add your Reddit username: `windows:reddit-video-maker:v1.0 (by /u/yourname)`. Each listing is requested from `www.reddit.com` first and then from `old.reddit.com`.

## Background videos

Copy one or more `.mp4` files into `assets/backgrounds/`. Minecraft parkour gameplay is the classic choice for Reddit Shorts:

```powershell
Copy-Item "$HOME\Downloads\minecraft-parkour.mp4" assets\backgrounds\
```

```bash
cp ~/Downloads/minecraft-parkour.mp4 assets/backgrounds/
```

The file can have any name and any resolution or aspect ratio. The CLI uses `minecraft_parkour.mp4` if it exists and otherwise picks a clip at random; the desktop app lets you choose. Each run seeks to a random start point between 0 and `clip length − video length − 2 s`, so long clips give fresh footage every time. A clip shorter than the narration is looped instead. The clip is scaled to cover 1080×1920 and center-cropped without distortion, and its audio is ignored. Use only footage you have the right to use. These files are gitignored and never committed.

## Processed-post tracking

The IDs of successfully rendered posts are stored in `data/used_posts.json`. Posts already listed there are skipped. A post is added only **after** its video has been rendered and verified, so a failed run can be retried. A missing, empty or corrupt file is treated as empty, and updates are written atomically through a temporary file and `os.replace`. Delete the file to start over.

## Tests

```bash
pytest -v
```

The tests run fully offline: no Reddit, Edge TTS, Chromium, FFmpeg or YouTube is needed. They cover:

- feed parsing with an HTML-bearing Atom fixture, text cleaning, acronym boundaries and URL validation
- tracking, including "only successful posts are marked", and CLI validation
- ASS timestamps, one-word cues with no overlaps, offsets and silence trimming
- the FFmpeg filter graph, the random background seek and path escaping
- YouTube metadata and a mocked upload

## Troubleshooting

| Problem | Fix |
|---|---|
| `ffmpeg and ffprobe not found on PATH` | Install FFmpeg (see above) and open a new terminal. |
| `No background video found` | Add an `.mp4` to `assets/backgrounds/`. |
| `HTTP 429` | Reddit is rate-limiting you. Wait a few minutes and lower `--limit`. |
| `HTTP 403` / `HTTP 404` on both `www` and `old` | Reddit is blocking anonymous requests from your network. See [When Reddit blocks your network](#when-reddit-blocks-your-network): use `--file`, or set `REDDIT_COOKIE`. If it was already working with `REDDIT_COOKIE`, the session has probably expired, so copy a fresh one. |
| `Reddit returned an HTML page instead of JSON` (or `of an RSS feed` for `--file`) | Reddit sent a block or "prove your humanity" page instead of the feed. Same fixes as above. The fetcher checks for this before parsing, so you get this message instead of a confusing JSON or XML error. |
| `Executable doesn't exist ... chromium` | Run `playwright install chromium` inside the venv. |
| `Failed building wheel for greenlet` (Windows) | You are on 32-bit Python. Install 64-bit Python and recreate the venv. |
| Edge TTS errors | Check your internet connection and the voice name (`edge-tts --list-voices`). |
| `No unprocessed posts found` | Every fetched post is already in `data/used_posts.json`. |

## Known limitations

- **Public `.json` endpoints instead of OAuth.** Reddit may rate-limit, block or change unauthenticated access at any time. Each fetch looks at the top 15 posts of the week.
- **No upvote counts on the card.** The title card shows only the subreddit, the author and the title.
- **Edge TTS needs internet** and relies on an unofficial Microsoft endpoint that could change.
- **FFmpeg/ffprobe are installed separately**, and you supply your own background videos.
- Link and image posts have no body text, so a short fallback line is narrated instead.
- Captions use the words exactly as Edge TTS reports them, without punctuation.
- Silence is trimmed only at the start and end. The voice still pauses briefly at commas and full stops; that comes from the Edge TTS voice itself.
- The desktop app uses `os.startfile`, so **Check Video** works on Windows only.
- `output/temp/title_card.png` is shared between posts. Posts are rendered one at a time, so this is safe, but do not run two instances at once.
- Paths resolve from the project root, so use an editable install (`pip install -e .`) rather than a regular one.
