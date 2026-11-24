"""CLI: python main.py --subreddit tifu --limit 3"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from src import TEMP, USED_POSTS
from src import card, fetcher, tts, uploader, video

EMPTY_BODY_NARRATION = "This post has no text. Check the comments to see what people are saying."


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reddit-video-maker", description="Turn public Reddit posts into narrated 9:16 videos.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--subreddit", help="subreddit name, with or without r/ (uses this week's top posts)")
    source.add_argument("--url", help="a single Reddit post URL")
    source.add_argument("--file", type=Path, help="a saved Reddit RSS/Atom .xml file (offline fallback)")
    parser.add_argument("--voice", default=tts.DEFAULT_VOICE, help=f"Edge TTS voice (default: {tts.DEFAULT_VOICE})")
    parser.add_argument("--limit", type=int, default=1, help="maximum number of posts to render (default: 1)")
    parser.add_argument("--upload", action="store_true", help="upload each rendered video to YouTube as a Short")
    parser.add_argument("--privacy", choices=uploader.PRIVACY_OPTIONS, default="private",
                        help="YouTube privacy status for --upload (default: private)")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    try:
        if args.subreddit:
            args.subreddit = fetcher.normalize_subreddit(args.subreddit)
        elif args.url:
            fetcher.post_feed_url(args.url)
        elif not args.file.is_file():
            raise ValueError(f"Feed file not found: {args.file}")
    except ValueError as exc:
        parser.error(str(exc))
    return args


async def load_posts(subreddit: str | None = None, url: str | None = None, file: Path | None = None,
                     limit: int = 1, used_path: Path | None = None) -> list[fetcher.Post]:
    """Fetch (or read) posts and drop ones already rendered. Raises FetchError/ValueError/OSError."""
    if file:
        posts = fetcher.parse_feed(Path(file).read_bytes())
        if not posts:
            raise fetcher.FetchError(f"No posts found in {file}")
    else:
        posts = await fetcher.fetch_posts(subreddit=subreddit, url=url)
    used = fetcher.load_used(used_path or USED_POSTS)
    return [p for p in posts if p.id not in used][:limit]


async def render_post(post: fetcher.Post, voice: str, background: Path | None = None) -> Path:
    """Full pipeline for one post. Raises on any failure."""
    title_mp3 = TEMP / f"{post.id}_title.mp3"
    body_mp3 = TEMP / f"{post.id}_body.mp3"
    await tts.synthesize(fetcher.clean_text(post.title) or post.title, voice, title_mp3)
    words = await tts.synthesize(post.body or EMPTY_BODY_NARRATION, voice, body_mp3)
    title_dur, body_dur = video.duration(title_mp3), video.duration(body_mp3)
    subs = tts.write_ass(words, title_dur + video.GAP_SECONDS, TEMP / f"{post.id}.ass")
    png = await card.render_card(post, TEMP / "title_card.png")
    out = video.output_path(post.title, post.id)
    video.render(png, title_mp3, body_mp3, subs, title_dur, body_dur, out, background)
    # metadata sidecar so the GUI can upload the latest video after a restart
    out.with_suffix(".json").write_text(
        json.dumps({"id": post.id, "title": post.title, "subreddit": post.subreddit, "author": post.author}),
        encoding="utf-8",
    )
    return out


async def process_posts(
    posts: list[fetcher.Post], voice: str, used_path: Path = USED_POSTS,
    render: Callable[[fetcher.Post, str], Awaitable[Path]] = render_post,
    privacy: str | None = None,
) -> tuple[list[Path], list[tuple[str, str]]]:
    """Render each post; mark it used only after success. If privacy is set, upload each
    rendered video too (an upload failure is reported but the render still counts).
    Returns (outputs, failures)."""
    outputs, failures = [], []
    for i, post in enumerate(posts, 1):
        print(f"[{i}/{len(posts)}] {post.id} r/{post.subreddit}: {post.title}")
        try:
            out = await render(post, voice)
        except Exception as exc:  # report and continue with the next post
            print(f"  FAILED: {exc}", file=sys.stderr)
            failures.append((post.id, str(exc)))
            continue
        fetcher.mark_used(post.id, used_path)
        print(f"  done -> {out}")
        outputs.append(out)
        if privacy:
            try:
                vid = await asyncio.to_thread(uploader.upload_short, out, post.title, post.subreddit, post.author, privacy)
                print(f"  uploaded ({privacy}) -> https://youtube.com/shorts/{vid}")
            except Exception as exc:
                print(f"  UPLOAD FAILED: {exc}", file=sys.stderr)
                failures.append((post.id, f"upload: {exc}"))
    return outputs, failures


async def run(args: argparse.Namespace) -> int:
    try:
        fresh = await load_posts(args.subreddit, args.url, args.file, args.limit, USED_POSTS)
    except (fetcher.FetchError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not fresh:
        print("No unprocessed posts found (all were already rendered).", file=sys.stderr)
        return 1
    outputs, failures = await process_posts(fresh, args.voice, USED_POSTS,
                                            privacy=args.privacy if args.upload else None)
    print(f"\n{len(outputs)} rendered, {len(failures)} failed.")
    for out in outputs:
        print(f"  {out}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
