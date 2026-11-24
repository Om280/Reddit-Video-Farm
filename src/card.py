"""Render the title card as a transparent PNG with headless Chromium."""

from __future__ import annotations

import html
from pathlib import Path

from playwright.async_api import async_playwright

from . import TEMPLATES
from .fetcher import Post

WIDTH, HEIGHT = 1080, 1920


def render_html(post: Post) -> str:
    """Fill the template; every value is HTML-escaped."""
    values = {
        "subreddit": f"r/{post.subreddit}" if post.subreddit else "Reddit",
        "author": f"u/{post.author}" if post.author else "",
        "title": post.title,
    }
    page = (TEMPLATES / "card.html").read_text(encoding="utf-8")
    for key, value in values.items():
        page = page.replace("{{" + key + "}}", html.escape(value))
    return page


async def render_card(post: Post, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
            await page.set_content(render_html(post), wait_until="load")
            await page.screenshot(path=str(out_path), omit_background=True)
        finally:
            await browser.close()
    return out_path
