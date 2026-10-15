"""
YouTube handling — transcript extraction, comment fetching (yt-dlp),
and context formatting for LLM injection. Used by chat_handler.py.
"""

import asyncio
import json
import logging
import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

YOUTUBE_INSTRUCTION_PROMPT = """When the user shares a YouTube video, respond with a structured breakdown:

1. **Summary** — Concise overview of the video's content and main thesis (2-4 sentences)
2. **Key Points** — Bullet list of the most important topics, arguments, or moments
3. **Notable Timestamps** — If timestamps are available from the transcript, highlight 3-5 interesting moments with their approximate timestamps (e.g. "03:45 — discusses X")
4. **Audience Reception** — If comments are available, summarize what viewers think: general sentiment, top reactions, any debate or controversy

Keep it conversational and concise. Do NOT web search for this video — use only the transcript and comments provided."""

# ---------------------------------------------------------------------------
# Init / helpers
# ---------------------------------------------------------------------------

# Will be set at startup by init_youtube()
YouTubeTranscriptApi = None
YOUTUBE_AVAILABLE = False


def _find_ytdlp() -> str:
    """Find the yt-dlp binary: venv bin first, then system PATH."""
    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if venv_bin.exists():
        return str(venv_bin)
    found = shutil.which("yt-dlp")
    return found or "yt-dlp"


def init_youtube():
    """Import and cache the YouTube transcript API."""
    global YouTubeTranscriptApi, YOUTUBE_AVAILABLE
    try:
        from youtube_transcript_api import YouTubeTranscriptApi as _Api
        YouTubeTranscriptApi = _Api
        YOUTUBE_AVAILABLE = True
        logger.info("YouTube transcript API available")
    except ImportError as e:
        logger.warning(f"youtube-transcript-api not installed: {e}")
        YOUTUBE_AVAILABLE = False


def is_youtube_url(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def extract_youtube_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            params = urllib.parse.parse_qs(parsed.query)
            if "v" in params:
                return params["v"][0]
        elif parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[-1]
        elif parsed.path.startswith("/live/"):
            return parsed.path.split("/")[-1]
        elif parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[-1]
    elif parsed.hostname == "youtu.be":
        return parsed.path[1:]
    return None


def fetch_youtube_transcript(url: str, max_chars: int = 8000, target_language: str = "en") -> Dict[str, Any]:
    """Synchronously fetch a YouTube video's transcript from a URL.

    A self-contained wrapper for the ``youtube_transcript`` agent tool: it
    extracts the video id from the URL, prefers a manual-caption track already
    in ``target_language``, falls back to ASR auto-generated, and as a last
    resort translates the best available track (e.g. a Hindi- or Spanish-only
    video the agent needs in English). Returns both the stitched full text and
    the timestamped segments. Degrades to a clear
    ``{"success": False, "error": ...}`` instead of raising, so the agent can
    reason around a missing API/library / translation.
    """
    if not YOUTUBE_AVAILABLE or YouTubeTranscriptApi is None:
        # Lazy-init: the library may not be imported at import time.
        init_youtube()
    if not YOUTUBE_AVAILABLE or YouTubeTranscriptApi is None:
        return {
            "success": False,
            "error": "youtube-transcript-api is not installed.",
            "transcript": None,
        }

    url = (url or "").strip()
    video_id = extract_youtube_id(url)
    if not video_id:
        return {"success": False, "error": f"Could not extract a YouTube video id from: {url[:120]}", "transcript": None}

    try:
        api = YouTubeTranscriptApi()
        transcript = None
        is_generated = False
        language = None
        translated_from = None
        try:
            tl = api.list(video_id)
            # Version-stable selection: the `youtube-transcript-api` library's
            # `find_*` helpers changed signatures across releases (some take
            # `language_codes`, others don't), so we iterate the TranscriptList
            # directly and inspect each Transcript's public attributes
            # (`language_code`, `is_generated`, `is_translatable`), which are
            # stable. Priority: manual in-target > any in-target > manual
            # translate-able > generated translate-able > first available.
            tracks = [t for t in tl]

            def _attrs(t):
                lc = getattr(t, "language_code", "") or ""
                gen = bool(getattr(t, "is_generated", False))
                translatable = bool(getattr(t, "is_translatable", False))
                return lc, gen, translatable

            # 1. Manual track already in the target language.
            chosen = None
            for t in tracks:
                lc, gen, _ = _attrs(t)
                if not gen and lc == target_language:
                    chosen = t
                    break
            # 2. Any track already in the target language (generated ASR ok).
            if chosen is None:
                for t in tracks:
                    lc, _, _ = _attrs(t)
                    if lc == target_language:
                        chosen = t
                        break
            # 3. Manual track in another language (translate-able preferred).
            if chosen is None:
                for t in tracks:
                    lc, gen, translatable = _attrs(t)
                    if not gen and translatable:
                        chosen = t
                        break
            # 4. Generated track in another language (translate-able preferred).
            if chosen is None:
                for t in tracks:
                    lc, gen, translatable = _attrs(t)
                    if translatable:
                        chosen = t
                        break
            # 5. First available track regardless of language/translatability.
            if chosen is None and tracks:
                chosen = tracks[0]

            if chosen is not None:
                original_lang, is_generated, translatable = _attrs(chosen)
                if original_lang and original_lang != target_language and translatable:
                    # Translate the chosen track to the target language.
                    try:
                        translated = chosen.translate(target_language)
                        translated_from = original_lang
                        transcript = list(translated.fetch())
                        language = target_language
                    except Exception:
                        # Translation failed — keep the original so we still
                        # return something rather than nothing.
                        transcript = list(chosen.fetch())
                        language = original_lang
                else:
                    transcript = list(chosen.fetch())
                    language = original_lang or target_language
        except Exception:
            # Older API / fallback: fetch() returns the default track.
            transcript = list(api.fetch(video_id))

        if not transcript:
            return {
                "success": False,
                "error": "This video has no available transcript (captions disabled).",
                "transcript": None,
            }

        segments = []
        for snippet in transcript:
            text = getattr(snippet, "text", "")
            if isinstance(text, str):
                text = text.strip()
            if not text:
                continue
            start = float(getattr(snippet, "start", 0.0) or 0.0)
            duration = float(getattr(snippet, "duration", 0.0) or 0.0)
            segments.append({
                "text": text,
                "start": start,
                "duration": duration,
                "timestamp": f"{int(start // 60):02d}:{int(start % 60):02d}",
            })

        full_text = " ".join(s["text"] for s in segments)
        truncated = len(full_text) > max_chars
        if truncated:
            full_text = full_text[:max_chars] + "... [transcript truncated]"

        return {
            "success": True,
            "transcript": full_text,
            "video_id": video_id,
            "language": language or target_language,
            "translated_from": translated_from,
            "is_generated": is_generated,
            "segments": segments,
            "truncated": truncated,
        }
    except Exception as e:
        # Distinguish transient YouTube anti-bot blocks from genuine failures
        # so the agent can decide to wait/retry or fall back to another source
        # rather than just dumping a multi-line error into the chat.
        etype = type(e).__name__
        if etype in ("IpBlocked", "RequestBlocked", "TooManyRequests"):
            logger.warning(f"youtube_transcript: {etype} for {video_id}")
            return {
                "success": False,
                "error": (
                    f"YouTube is temporarily blocking transcript requests from this IP "
                    f"({etype}). Wait a bit and retry, or use a different video."
                ),
                "transcript": None,
                "_blocked": True,
            }
        logger.warning(f"fetch_youtube_transcript failed for {url}: {e}")
        return {
            "success": False,
            "error": f"Transcript fetch failed: {e}",
            "transcript": None,
        }


async def extract_transcript_async(
    url: str, video_id: str, max_retries: int = 3
) -> Dict[str, Any]:
    """
    Async YouTube transcript extraction with retries.

    Args:
        url: Full YouTube URL
        video_id: Extracted video ID
        max_retries: Number of attempts

    Returns:
        Dict with success/error/transcript keys
    """
    if not YOUTUBE_AVAILABLE or YouTubeTranscriptApi is None:
        return {"success": False, "error": "YouTube transcript API not available", "transcript": None}

    for attempt in range(max_retries):
        try:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id)
            transcript_list = list(transcript)

            formatted = []
            for snippet in transcript_list:
                text = snippet.text.strip()
                if not text:
                    continue
                start = snippet.start
                formatted.append({
                    "text": text,
                    "start": start,
                    "duration": snippet.duration,
                    "timestamp": f"{int(start // 60):02d}:{int(start % 60):02d}",
                })

            full_text = " ".join(e["text"] for e in formatted)
            max_len = 8000
            if len(full_text) > max_len:
                full_text = full_text[:max_len] + "... [transcript truncated]"

            return {
                "success": True,
                "transcript": full_text,
                "video_id": video_id,
                "language": "en",
                "is_generated": False,
                "segments": formatted,
            }
        except Exception as e:
            logger.warning(f"Transcript attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))

    return {"success": False, "error": f"Failed after {max_retries} attempts", "transcript": None}


# ---------------------------------------------------------------------------
# Transcript persistence + RAG indexing
# ---------------------------------------------------------------------------

def _transcript_cache_dir() -> Path:
    """Directory for cached YouTube transcripts, keyed by video id.

    Lives under data/youtube/ so transcripts survive restarts and can be
    re-indexed on startup without re-fetching from YouTube.
    """
    base = Path(__file__).resolve().parent.parent
    d = base / "data" / "youtube"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _transcript_cache_path(video_id: str) -> Path:
    return _transcript_cache_dir() / f"{video_id}.json"


def save_transcript_cache(video_id: str, transcript_data: Dict[str, Any],
                          title: str = "", channel: str = "", url: str = "") -> bool:
    """Persist a fetched transcript to data/youtube/<video_id>.json."""
    try:
        import time as _time
        payload = {
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "url": url,
            "transcript": transcript_data.get("transcript", ""),
            "segments": transcript_data.get("segments", []),
            "language": transcript_data.get("language", ""),
            "is_generated": transcript_data.get("is_generated", False),
            "translated_from": transcript_data.get("translated_from"),
            "fetched_at": _time.time(),
        }
        with open(_transcript_cache_path(video_id), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.warning(f"save_transcript_cache({video_id}) failed: {e}")
        return False


def load_transcript_cache(video_id: str) -> Optional[Dict[str, Any]]:
    """Return the cached transcript for a video, or None."""
    try:
        p = _transcript_cache_path(video_id)
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"load_transcript_cache({video_id}) failed: {e}")
        return None


def transcript_index_text(transcript_data: Dict[str, Any]) -> str:
    """Serialize a transcript into the text form we vector-index.

    Timestamped segments are preferred (one line each, preserving the
    ``[mm:ss]`` prefix so citations can reference the exact moment); falls
    back to the stitched transcript when no segments are present.
    """
    segs = transcript_data.get("segments") or []
    if segs:
        return "\n".join(f"[{s.get('timestamp', '00:00')}] {s.get('text', '')}" for s in segs if s.get("text"))
    return transcript_data.get("transcript", "") or ""


def index_transcript_to_rag(video_id: str, transcript_data: Dict[str, Any],
                            owner: Optional[str] = None, title: str = "",
                            channel: str = "", url: str = "") -> bool:
    """Best-effort: vectorize a transcript into the shared RAG store."""
    try:
        from src import rag_index
        text = transcript_index_text(transcript_data)
        if not text.strip():
            return False
        return rag_index.index_youtube(
            video_id, text, owner=owner, title=title, channel=channel, url=url,
        )
    except Exception as e:
        logger.warning(f"index_transcript_to_rag({video_id}) failed: {e}")
        return False


def list_youtube_playlist_video_ids(
    playlist_url: str, max_videos: int = 200, timeout: int = 120
) -> List[str]:
    """Return the video IDs of a YouTube playlist URL via yt-dlp.

    Uses ``--flat-playlist`` so only metadata is fetched (cheap). Returns an
    empty list on failure so callers can degrade gracefully.
    """
    cmd = [
        _find_ytdlp(),
        "--flat-playlist",
        "--print", "%(id)s",
        "--no-warnings",
        playlist_url,
    ]
    try:
        import subprocess
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            logger.warning(f"yt-dlp playlist listing failed for {playlist_url}: {proc.stderr[:200]}")
            return []
        ids = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        return ids[:max_videos]
    except Exception as e:
        logger.warning(f"list_youtube_playlist_video_ids failed: {e}")
        return []


def format_transcript_for_context(
    transcript_data: Dict[str, Any], url: str,
    title: str = "", channel: str = ""
) -> str:
    """Format transcript data for inclusion in LLM context."""
    if not transcript_data.get("success"):
        header = ""
        if title:
            header = f" \"{title}\""
            if channel:
                header += f" by {channel}"
        return f"\n[YouTube Video{header}: Transcript unavailable ({transcript_data.get('error', 'Unknown error')}). Use the comments below if available, do NOT web search for this video.]"

    transcript = transcript_data.get("transcript", "")
    video_id = transcript_data.get("video_id", "")
    language = transcript_data.get("language", "unknown")
    is_generated = transcript_data.get("is_generated", False)
    segments = transcript_data.get("segments", [])

    ctx = "\n[YOUTUBE VIDEO TRANSCRIPT]\n"
    if title:
        ctx += f"Title: {title}\n"
    if channel:
        ctx += f"Channel: {channel}\n"
    ctx += f"Video ID: {video_id}\n"
    ctx += f"Language: {language}\n"
    ctx += f"Source: {'Auto-generated' if is_generated else 'Manual'}\n"
    ctx += f"URL: {url}\n\n"
    # Include timestamped segments for the LLM to reference
    if segments:
        ctx += "Timestamped Transcript:\n"
        for seg in segments:
            ctx += f"[{seg['timestamp']}] {seg['text']}\n"
        # Check length — fall back to plain text if too long
        if len(ctx) > 12000:
            ctx = ctx[:ctx.index("Timestamped Transcript:\n")]
            ctx += "Transcript:\n"
            ctx += transcript
    else:
        ctx += "Transcript:\n"
        ctx += transcript
    ctx += "\n[END TRANSCRIPT]\n"
    return ctx


async def fetch_youtube_comments(
    video_id: str, max_comments: int = 25, timeout: int = 30
) -> Dict[str, Any]:
    """Fetch top comments for a YouTube video using yt-dlp.

    Returns dict with 'success', 'comments' list, 'error'.
    """
    try:
        cmd = [
            _find_ytdlp(),
            "--skip-download",
            "--write-comments",
            "--extractor-args", f"youtube:max_comments={max_comments},all,100,0",
            "--dump-json",
            "--js-runtimes", "node",
            "--remote-components", "ejs:github",
            f"https://www.youtube.com/watch?v={video_id}",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Bound the wait on the process actually finishing, not on spawning it.
        # create_subprocess_exec returns as soon as the child starts, so wrapping
        # it in wait_for never enforces the timeout — proc.communicate() is the
        # blocking step. Kill and reap the child if it overruns so it does not
        # linger after we return.
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            return {"success": False, "error": f"yt-dlp failed: {stderr.decode()[:200]}", "comments": []}

        data = json.loads(stdout.decode())
        title = data.get("title", "")
        channel = data.get("channel", "") or data.get("uploader", "")
        raw_comments = data.get("comments", [])

        comments = []
        for c in raw_comments[:max_comments]:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            comments.append({
                "author": c.get("author", "Unknown"),
                "text": text,
                "likes": c.get("like_count", 0),
            })

        # Sort by likes descending — most popular comments first
        comments.sort(key=lambda x: x.get("likes", 0), reverse=True)

        return {"success": True, "comments": comments, "count": len(comments),
                "title": title, "channel": channel}

    except asyncio.TimeoutError:
        logger.warning(f"Comment fetch timed out for {video_id}")
        return {"success": False, "error": "Comment fetch timed out", "comments": []}
    except FileNotFoundError:
        logger.warning("yt-dlp not installed — cannot fetch comments")
        return {"success": False, "error": "yt-dlp not installed", "comments": []}
    except Exception as e:
        logger.warning(f"Failed to fetch comments for {video_id}: {e}")
        return {"success": False, "error": str(e), "comments": []}


def format_comments_for_context(comments_data: Dict[str, Any], url: str) -> str:
    """Format YouTube comments for inclusion in LLM context."""
    if not comments_data.get("success") or not comments_data.get("comments"):
        return ""

    comments = comments_data["comments"]
    ctx = f"\n[YOUTUBE VIDEO COMMENTS — Top {len(comments)} by popularity]\n"
    ctx += f"URL: {url}\n\n"

    for i, c in enumerate(comments, 1):
        likes = c.get("likes", 0)
        likes_str = f" [{likes} likes]" if likes else ""
        ctx += f"{i}. @{c['author']}{likes_str}: {c['text']}\n\n"

    if len(ctx) > 4000:
        ctx = ctx[:4000] + "\n[Comments truncated]\n"

    ctx += "[END COMMENTS]\n"
    return ctx
