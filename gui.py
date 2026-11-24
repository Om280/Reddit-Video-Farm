"""Desktop GUI for reddit-video-maker (CustomTkinter). Launch with launch_app.bat or `python gui.py`."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import main
from src import BACKGROUNDS, OUTPUT, USED_POSTS, fetcher, uploader, video

VOICES = {
    "Ava": "en-US-AvaMultilingualNeural",
    "Emma": "en-US-EmmaMultilingualNeural",
    "Andrew": "en-US-AndrewMultilingualNeural",
}
NO_BACKGROUND = "(no .mp4 in assets/backgrounds)"
REDDIT_TAB, PASTE_TAB = "Reddit", "Paste Text"


def latest_video() -> Path | None:
    videos = list(OUTPUT.glob("*.mp4"))
    return max(videos, key=lambda p: p.stat().st_mtime) if videos else None


async def generate_next(sub: str | None, url: str | None, file: Path | None, voice: str, background: Path,
                        used_path: Path | None = None, status=lambda text: None, render=None) -> str:
    """Render the next post not yet in used_posts.json and record it there; returns a status message.
    The same file is used for filtering and marking, so each click moves on to a new post."""
    used_path = used_path or USED_POSTS
    status("Fetching post…")
    try:
        posts = await main.load_posts(sub, url, file, limit=1, used_path=used_path)
    except fetcher.FetchError as exc:
        return (f"Couldn't fetch posts: Reddit (and the PullPush fallback) refused this network.\n"
                f"Use the {PASTE_TAB} tab, or Browse .xml File.\n\nDetails: {str(exc)[:300]}")
    if not posts:
        return ("Every fetched post has already been rendered.\n"
                "Click Reset History to start over, or try another subreddit.")
    status(f"Rendering: {posts[0].title}\n(narration, title card, FFmpeg — this can take a minute)")
    render = functools.partial(render or main.render_post, background=background)
    outputs, failures = await main.process_posts(posts, voice, used_path, render=render)
    if failures:
        return f"Error: {failures[0][1]}"
    return f"Done: {outputs[0]}"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.title("Reddit Video Maker")
        self.geometry("720x560")
        self.resizable(False, False)
        self.feed_file: Path | None = None
        self.busy = False

        pad = {"padx": 8, "pady": (6, 0)}
        ctk.CTkLabel(self, text="Reddit Video Maker", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(12, 0))

        self.tabs = ctk.CTkTabview(self, width=680, height=190)
        self.tabs.pack(padx=20, pady=(0, 0))
        reddit_tab, paste_tab = self.tabs.add(REDDIT_TAB), self.tabs.add(PASTE_TAB)

        ctk.CTkLabel(reddit_tab, text="Use one source: a subreddit, a post URL, or a saved .xml feed.",
                     text_color="gray70").pack()
        self.subreddit = ctk.CTkEntry(reddit_tab, placeholder_text="Subreddit (e.g. AmItheAsshole)")
        self.subreddit.pack(fill="x", **pad)
        self.url = ctk.CTkEntry(reddit_tab, placeholder_text="Single Reddit post URL")
        self.url.pack(fill="x", **pad)
        row = ctk.CTkFrame(reddit_tab, fg_color="transparent")
        row.pack(fill="x", **pad)
        ctk.CTkButton(row, text="Browse .xml File", width=160, command=self.browse).pack(side="left")
        self.file_label = ctk.CTkLabel(row, text="No file selected", text_color="gray70")
        self.file_label.pack(side="left", padx=12)
        ctk.CTkButton(row, text="Reset History", width=120, fg_color="gray30", hover_color="gray25",
                      command=self.reset_history).pack(side="right")

        self.paste_title = ctk.CTkEntry(paste_tab, placeholder_text="Title")
        self.paste_title.pack(fill="x", padx=8, pady=(0, 0))
        self.paste_body = ctk.CTkTextbox(paste_tab, height=96, wrap="word")
        self.paste_body.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.pack(fill="x", padx=20, pady=(16, 0))
        backgrounds = [p.name for p in video.list_backgrounds()] or [NO_BACKGROUND]
        default_bg = video.DEFAULT_BACKGROUND if video.DEFAULT_BACKGROUND in backgrounds else backgrounds[0]
        self.voice = self._dropdown(opts, 0, "Voice", list(VOICES), "Ava")
        self.background = self._dropdown(opts, 1, "Background", backgrounds, default_bg)
        self.privacy = self._dropdown(opts, 2, "Upload privacy", list(uploader.PRIVACY_OPTIONS), "private")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(24, 0))
        self.generate_btn = ctk.CTkButton(buttons, text="Generate Video", height=44, command=self.generate,
                                          fg_color="#ff4500", hover_color="#cc3700")
        self.check_btn = ctk.CTkButton(buttons, text="Check Video", height=44, command=self.check)
        self.upload_btn = ctk.CTkButton(buttons, text="Upload Video", height=44, command=self.upload)
        for i, b in enumerate((self.generate_btn, self.check_btn, self.upload_btn)):
            buttons.grid_columnconfigure(i, weight=1)
            b.grid(row=0, column=i, padx=6, sticky="ew")

        self.progress = ctk.CTkProgressBar(self, width=680, mode="determinate")
        self.progress.pack(padx=20, pady=(26, 0))
        self.progress.set(0)
        self.status = ctk.CTkLabel(self, text="Ready.", wraplength=680, justify="left")
        self.status.pack(padx=20, pady=(12, 0), fill="x")

    def _dropdown(self, parent, col: int, label: str, values: list[str], default: str) -> ctk.CTkOptionMenu:
        parent.grid_columnconfigure(col, weight=1)
        ctk.CTkLabel(parent, text=label).grid(row=0, column=col, sticky="w", padx=6)
        menu = ctk.CTkOptionMenu(parent, values=values)
        menu.set(default)
        menu.grid(row=1, column=col, sticky="ew", padx=6)
        return menu

    # ---------- helpers ----------

    def set_status(self, text: str) -> None:
        """Thread-safe status update."""
        self.after(0, lambda: self.status.configure(text=text))

    def run_task(self, label: str, work) -> None:
        """Run `work()` on a daemon thread with the progress bar animating; it returns a status string."""
        if self.busy:
            return
        self.busy = True
        for b in (self.generate_btn, self.upload_btn):
            b.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.status.configure(text=label)

        def target():
            try:
                message = work()
            except Exception as exc:  # surfaced in the UI, never swallowed
                message = f"Error: {exc}"
            self.after(0, lambda: self._done(message))

        threading.Thread(target=target, daemon=True).start()

    def _done(self, message: str) -> None:
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1 if message.startswith(("Done", "Uploaded")) else 0)
        for b in (self.generate_btn, self.upload_btn):
            b.configure(state="normal")
        self.status.configure(text=message)

    # ---------- actions ----------

    def browse(self) -> None:
        path = filedialog.askopenfilename(title="Choose a saved Reddit RSS feed",
                                          filetypes=[("RSS/Atom XML", "*.xml"), ("All files", "*.*")])
        if path:
            self.feed_file = Path(path)
            self.file_label.configure(text=self.feed_file.name)

    def generate(self) -> None:
        bg_name = self.background.get()
        if bg_name == NO_BACKGROUND:
            messagebox.showerror("No background", f"Add a .mp4 file to {BACKGROUNDS} and restart the app.")
            return
        voice, background = VOICES[self.voice.get()], BACKGROUNDS / bg_name
        if self.tabs.get() == PASTE_TAB:
            self.generate_pasted(voice, background)
            return

        sub, url, file = self.subreddit.get().strip(), self.url.get().strip(), self.feed_file
        if sum(bool(x) for x in (sub, url, file)) != 1:
            messagebox.showwarning("Choose one source", "Fill in exactly one: subreddit, post URL, or .xml file.")
            return
        try:
            sub = fetcher.normalize_subreddit(sub) if sub else None
            if url:
                fetcher.post_feed_url(url)
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc))
            return

        def work() -> str:
            return asyncio.run(generate_next(sub, url or None, file, voice, background, status=self.set_status))

        self.run_task("Starting…", work)

    def reset_history(self) -> None:
        if not messagebox.askyesno(
                "Reset history",
                f"Forget every rendered post in {USED_POSTS.name}?\n\n"
                "Posts you already made videos for can then be picked again. Existing videos are not deleted."):
            return
        USED_POSTS.unlink(missing_ok=True)
        self.status.configure(text="History cleared. Generate will start again from the top post.")

    def generate_pasted(self, voice: str, background: Path) -> None:
        """Render straight from pasted text: no network fetch, not added to used_posts.json."""
        try:
            post = fetcher.post_from_text(self.paste_title.get(), self.paste_body.get("1.0", "end"))
        except ValueError as exc:
            messagebox.showwarning("Missing text", str(exc))
            return

        def work() -> str:
            self.set_status(f"Rendering: {post.title}\n(narration, title card, FFmpeg — this can take a minute)")
            return f"Done: {asyncio.run(main.render_post(post, voice, background))}"

        self.run_task("Starting…", work)

    def check(self) -> None:
        path = latest_video()
        if not path:
            messagebox.showinfo("No video yet", "Generate a video first.")
            return
        os.startfile(path)  # Windows: opens in the default player

    def upload(self) -> None:
        path = latest_video()
        if not path:
            messagebox.showinfo("No video yet", "Generate a video first.")
            return
        if not uploader.CLIENT_SECRETS.exists() and not uploader.TOKEN.exists():
            messagebox.showinfo("YouTube setup needed", uploader.SETUP_HELP)
            return
        meta_file = path.with_suffix(".json")
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
        title = meta.get("title") or path.stem.rsplit("-", 1)[0].replace("-", " ")
        privacy = self.privacy.get()
        if not messagebox.askyesno("Upload to YouTube", f"Upload this video as a {privacy} Short?\n\n{path.name}"):
            return

        def work() -> str:
            vid = uploader.upload_short(path, title, meta.get("subreddit", ""), meta.get("author", ""), privacy)
            return f"Uploaded ({privacy}): https://youtube.com/shorts/{vid}"

        self.run_task("Uploading to YouTube (a browser window opens the first time to sign in)…", work)


if __name__ == "__main__":
    App().mainloop()
