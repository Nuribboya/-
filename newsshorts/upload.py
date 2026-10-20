from __future__ import annotations

from pathlib import Path

from newsshorts.config import Config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _credentials(config: Config):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token = Path(config.youtube_token)
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(config.youtube_client_secret, SCOPES)
        creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def upload_short(config: Config, video: Path, title: str, description: str, tags: list[str]) -> str:
    """Upload a video and return its YouTube id.

    Note: videos uploaded through an API project Google hasn't audited are
    locked to private regardless of `youtube_privacy`.
    """
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=_credentials(config))
    hashtags = " ".join(f"#{t.lstrip('#')}" for t in tags)
    body = {
        "snippet": {
            "title": title[:100],
            "description": f"{description}\n\n{hashtags} #shorts".strip(),
            "tags": [t.lstrip("#") for t in tags],
            "categoryId": config.youtube_category_id,
            "defaultLanguage": "ko",
        },
        "status": {
            "privacyStatus": config.youtube_privacy,
            "selfDeclaredMadeForKids": False,
            # Narration is synthetic; YouTube asks creators to disclose altered/synthetic media.
            "containsSyntheticMedia": True,
        },
    }
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=MediaFileUpload(str(video), resumable=True)
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]
