"""
youtube_ingest_routes.py — bulk YouTube playlist transcript ingestion into RAG.

Single-URL ingestion already happens inline in the chat path; this route adds
the playlist-level bulk path so a whole playlist (e.g. a channel's uploads
playlist) can be fetched, cached, and vector-indexed in one request.

Politeness pacing (2–5s between fetches) and a per-video cache (data/youtube/
<video_id>.json) keep this from hammering YouTube and re-fetching on re-runs.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from src.auth_helpers import require_privilege, get_current_user

logger = logging.getLogger(__name__)


def setup_youtube_ingest_routes(upload_handler=None) -> APIRouter:
    router = APIRouter(tags=["youtube"])

    @router.post("/api/youtube/ingest-playlist")
    async def ingest_playlist(
        request: Request,
        playlist_url: str = Query(..., description="YouTube playlist URL"),
        max_videos: int = Query(100, ge=1, le=500),
        target_language: str = Query("es", max_length=8),
    ) -> Dict[str, Any]:
        """List a playlist, fetch + cache + RAG-index each video's transcript.

        Runs the whole pass in a worker thread so a large playlist does not
        hold the event loop; returns per-video results. Owner-scopes every
        indexed video to the requesting user.
        """
        user = require_privilege(request, "can_use_documents") or get_current_user(request)

        from src.youtube_handler import (
            list_youtube_playlist_video_ids,
            fetch_youtube_transcript,
            save_transcript_cache,
            index_transcript_to_rag,
        )
        from src import rag_index

        def _run() -> Dict[str, Any]:
            ids = list_youtube_playlist_video_ids(playlist_url, max_videos=max_videos)
            if not ids:
                raise HTTPException(400, "Could not list playlist (yt-dlp failed or playlist empty)")

            indexed = 0
            failed = 0
            results: List[Dict[str, Any]] = []

            for video_id in ids:
                url = f"https://www.youtube.com/watch?v={video_id}"
                try:
                    data = fetch_youtube_transcript(url, max_chars=0, target_language=target_language)
                except Exception as e:
                    results.append({"video_id": video_id, "status": "error", "error": str(e)})
                    failed += 1
                    continue

                if not data.get("success"):
                    results.append({"video_id": video_id, "status": "unavailable",
                                    "error": data.get("error", "no transcript")})
                    failed += 1
                else:
                    try:
                        save_transcript_cache(video_id, data, url=url)
                        ok = index_transcript_to_rag(video_id, data, owner=user, url=url)
                    except Exception as e:
                        ok = False
                        logger.warning(f"playlist index {video_id} failed: {e}")
                    status = "indexed" if ok else "cached"
                    if ok:
                        indexed += 1
                    else:
                        failed += 1
                    results.append({"video_id": video_id, "status": status})

                # Politeness pacing between YouTube calls (runs in a worker
                # thread, so a plain time.sleep does not block the event loop).
                time.sleep(2.5)
            return {"playlist_url": playlist_url, "total": len(ids),
                    "indexed": indexed, "failed": failed, "results": results}

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run), timeout=60 * max_videos
            )
        except asyncio.TimeoutError:
            raise HTTPException(504, "Playlist ingestion timed out")

    return router