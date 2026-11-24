"""Upload rendered videos to YouTube as Shorts (YouTube Data API v3, OAuth installed-app flow)."""

from __future__ import annotations

from pathlib import Path

from . import CREDENTIALS

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS = CREDENTIALS / "client_secrets.json"
TOKEN = CREDENTIALS / "token.json"
PRIVACY_OPTIONS = ("private", "unlisted", "public")
TITLE_LIMIT = 100  # YouTube's hard limit
CATEGORY_ENTERTAINMENT = "24"

SETUP_HELP = (
    "YouTube upload needs OAuth credentials.\n\n"
    "1. In Google Cloud Console, create a project and enable 'YouTube Data API v3'.\n"
    "2. Configure the OAuth consent screen and add your Google account as a test user.\n"
    "3. Create an OAuth client ID of type 'Desktop app' and download its JSON.\n"
    f"4. Save it as:\n   {CLIENT_SECRETS}\n\n"
    "The first upload opens a browser to sign in; the token is cached in credentials/token.json."
)


class UploadError(RuntimeError):
    pass


def shorts_title(title: str) -> str:
    """Title capped at 100 chars, always ending with #Shorts. YouTube rejects < and >."""
    suffix = " #Shorts"
    clean = " ".join(title.replace("<", "").replace(">", "").split())
    room = TITLE_LIMIT - len(suffix)
    if len(clean) > room:
        clean = clean[: room - 1].rstrip() + "…"
    return clean + suffix


def shorts_metadata(title: str, subreddit: str, author: str, privacy: str = "private") -> dict:
    if privacy not in PRIVACY_OPTIONS:
        raise ValueError(f"privacy must be one of {PRIVACY_OPTIONS}, got {privacy!r}")
    credit = " ".join(filter(None, ["Story from", subreddit and f"r/{subreddit}", author and f"by u/{author}"]))
    description = (
        f"{title}\n\n"
        + (f"{credit}.\n\n" if subreddit or author else "")  # pasted stories have no Reddit source
        + "#Shorts #RedditStories"
    ).replace("<", "").replace(">", "")
    return {
        "snippet": {
            "title": shorts_title(title),
            "description": description[:5000],
            "tags": ["Shorts", "Reddit", "RedditStories", subreddit],
            "categoryId": CATEGORY_ENTERTAINMENT,
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }


def get_credentials(secrets: Path = CLIENT_SECRETS, token: Path = TOKEN):
    """Load the cached token, refresh it, or run the browser sign-in flow once."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not secrets.exists():
            raise UploadError(SETUP_HELP)
        creds = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES).run_local_server(port=0)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def upload_short(video_path: Path, title: str, subreddit: str, author: str, privacy: str = "private") -> str:
    """Upload a video as a Short; returns the YouTube video ID."""
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    video_path = Path(video_path)
    if not video_path.is_file():
        raise UploadError(f"Video not found: {video_path}")
    body = shorts_metadata(title, subreddit, author, privacy)
    youtube = build("youtube", "v3", credentials=get_credentials(), cache_discovery=False)
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    try:
        response = None
        while response is None:
            _, response = request.next_chunk()
    except HttpError as exc:
        raise UploadError(f"YouTube upload failed: {exc}") from exc
    return response["id"]
