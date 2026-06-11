"""
audio_transcribe — download a YouTube clip's audio (yt-dlp) and transcribe it
with Groq Whisper (whisper-large-v3-turbo), reusing the existing GROQ_API_KEY.

Used by the A+ youtube report to get the REAL spoken words of each channel's
last-24h uploaded clips (not just titles/descriptions).

Honest constraints:
  - Skips LIVE streams (no fixed audio) and clips longer than `max_seconds`
    (bounds file size + transcription cost).
  - YouTube may block downloads from datacenter IPs ("confirm you're not a
    bot"); on failure this returns "" and the caller falls back to the
    description. transcribe_stats lets the report show how many actually worked.
"""

from __future__ import annotations

import os
import tempfile

import httpx

from services.logger import log

_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_WHISPER_MODEL = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
_MAX_BYTES = 24 * 1024 * 1024  # Groq audio limit ~25MB


def _download_audio(video_id: str, outdir: str, max_seconds: int):
    """Download the smallest audio stream for a non-live, short-enough video.
    Returns (path, duration) or (None, reason)."""
    try:
        import yt_dlp
    except Exception as e:
        return None, f"yt-dlp missing: {e}"
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        "format": "worstaudio/bestaudio",  # smallest audio → cheap + small file
        "outtmpl": os.path.join(outdir, "%(id)s.%(ext)s"),
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "retries": 2, "socket_timeout": 30,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("is_live"):
                return None, "live"
            dur = info.get("duration") or 0
            if dur and dur > max_seconds:
                return None, f"too long ({dur}s)"
            info = ydl.extract_info(url, download=True)
        path = os.path.join(outdir, f"{info['id']}.{info.get('ext', 'm4a')}")
        if not os.path.exists(path):
            # find whatever file landed
            for f in os.listdir(outdir):
                if f.startswith(info["id"]):
                    path = os.path.join(outdir, f)
                    break
        if not os.path.exists(path):
            return None, "download produced no file"
        if os.path.getsize(path) > _MAX_BYTES:
            return None, "audio too large"
        return path, info.get("duration") or 0
    except Exception as e:
        return None, f"download failed: {str(e)[:120]}"


def _groq_whisper(path: str) -> str:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return ""
    try:
        with open(path, "rb") as f:
            r = httpx.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (os.path.basename(path), f, "application/octet-stream")},
                data={"model": _WHISPER_MODEL, "response_format": "text"},
                timeout=180,
            )
        if r.status_code == 200:
            return (r.text or "").strip()
        log.warning(f"groq whisper {r.status_code}: {r.text[:160]}")
    except Exception as e:
        log.warning(f"groq whisper error: {str(e)[:120]}")
    return ""


def transcribe_youtube(video_id: str, max_seconds: int = 1500, max_chars: int = 6000) -> str:
    """Real transcript of a YouTube clip via yt-dlp + Groq Whisper. '' on any
    failure (live, too long, IP-blocked, etc.) so the caller can fall back."""
    if not video_id:
        return ""
    with tempfile.TemporaryDirectory() as td:
        path, info = _download_audio(video_id, td, max_seconds)
        if not path:
            log.info(f"audio_transcribe {video_id}: skipped ({info})")
            return ""
        txt = _groq_whisper(path)
        return (txt or "")[:max_chars]
