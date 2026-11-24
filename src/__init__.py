"""reddit-video-maker: turn public Reddit posts into narrated vertical videos."""

from pathlib import Path

# All paths resolve from the project root, never the caller's working directory.
ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
BACKGROUNDS = ROOT / "assets" / "backgrounds"
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
TEMP = OUTPUT / "temp"
USED_POSTS = DATA / "used_posts.json"
CREDENTIALS = ROOT / "credentials"
