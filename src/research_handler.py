# src/research_handler.py
"""Handler for research service integration with expandable UI support.

Uses the IterResearch-style DeepResearcher (LLM-in-the-loop) as the primary
engine, falling back to the legacy ResearchOrchestrator or basic web search
if needed.

Includes a task registry so research survives page refreshes and can be cancelled.
"""
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Dict

from src.research_utils import strip_thinking, is_low_quality

logger = logging.getLogger(__name__)

RESEARCH_DATA_DIR = Path("data/deep_research")


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, n))


class _ResearchLimit(Exception):
    """Raised by the run supervisor when a research run must be stopped
    (optional wall-clock cap reached, or no progress for too long)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _setting_seconds(key: str, default: int, minimum: int, maximum: int) -> int:
    """Read a seconds setting. 0 (or negative) means disabled and returns 0;
    any other value is bounded to [minimum, maximum]."""
    from src.settings import get_setting
    try:
        raw = int(get_setting(key, default))
    except (TypeError, ValueError):
        raw = default
    if raw <= 0:
        return 0
    return _bounded_int(raw, default=default, minimum=minimum, maximum=maximum)


def _format_probe_failure(model: str, exc: Exception) -> str:
    """Turn a failed research model probe into a user-facing message."""
    detail = getattr(exc, "detail", None)
    status = getattr(exc, "status_code", None)
    err = str(detail if detail is not None else exc).strip()

    if status in {401, 403} or "401" in err or "API key" in err or "Unauthorized" in err:
        return f"Model '{model}' requires an API key. Check your endpoint configuration."

    if status and err:
        return f"Model '{model}' probe failed: {err}"

    if err:
        return f"Cannot reach model '{model}' — {err}"

    return f"Cannot reach model '{model}' — check that the endpoint is running and accessible."


class ResearchHandler:
    """Handles research service operations with iterative deep research."""

    def __init__(self):
        self._legacy_engine = None
        self._active_tasks: Dict[str, dict] = {}
        self._initialize_legacy_engine()
        RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _initialize_legacy_engine(self):
        """Initialize the legacy research engine as a fallback."""
        try:
            from research_engine import ResearchOrchestrator, Config
            config = Config(max_searches=12, max_content_per_page=15000)
            self._legacy_engine = ResearchOrchestrator(config)
            logger.info("Legacy ResearchOrchestrator initialized (fallback)")
        except ImportError:
            logger.info("Legacy research_engine.py not found — DeepResearcher only")
            self._legacy_engine = None
        except Exception as e:
            logger.warning(f"Legacy research engine init failed: {e}")
            self._legacy_engine = None

    # ------------------------------------------------------------------
    # Query synthesis & planning
    # ------------------------------------------------------------------

    async def synthesize_query(
        self, sess, latest_message: str,
        llm_endpoint: str, llm_model: str, llm_headers: dict = None,
    ) -> str:
        """Synthesize the conversation into a single focused research query.

        Reads the session history and latest message to produce a clear,
        specific research question that captures the user's full intent.
        Falls back to the latest message if synthesis fails.
        """
        # Build conversation context from history
        history = getattr(sess, 'history', [])

        # A bare affirmation ("yes", "ok", "go ahead") is the user accepting the
        # clarifying-question round, NOT a research topic — researching the word
        # "yes" is the classic failure here. When synthesis can't run or fails,
        # fall back to the earliest substantive user message (the original ask)
        # rather than the literal follow-up.
        #
        # Match on an explicit affirmation/continuation phrase only (plus the
        # empty/punctuation-only case). We deliberately do NOT use a length
        # heuristic: a short answer like "UK", "C++", or "Rust" is a real topic
        # in a clarification flow and must be left untouched.
        _AFFIRMATIONS = {
            "yes", "y", "yeah", "yep", "yup", "sure", "sure thing", "ok", "okay",
            "k", "kk", "go", "go ahead", "go for it", "do it", "please",
            "yes please", "sounds good", "continue", "proceed", "lets go",
            "let's go", "yes go ahead",
        }

        def _normalize(text: str) -> str:
            return (text or "").strip().lower().strip("!.? ")

        def _fallback() -> str:
            normalized = _normalize(latest_message)
            if normalized and normalized not in _AFFIRMATIONS:
                return latest_message  # short or long, it's a real topic
            # Affirmation, or empty/punctuation-only: use the original ask.
            for m in history:
                c = (m.content or "").strip()
                if m.role == "user" and c and _normalize(c) not in _AFFIRMATIONS:
                    return c
            return latest_message

        if len(history) <= 1:
            return _fallback()  # No conversation to synthesize

        # Take last 6 messages max for context
        recent = history[-6:]
        convo = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content[:500]}"
            for m in recent if m.content
        )
        convo += f"\nUser: {latest_message}"

        try:
            from src.llm_core import llm_call_async

            response = await llm_call_async(
                url=llm_endpoint,
                model=llm_model,
                messages=[{"role": "user", "content":
                    "Read this conversation and write a single, specific research query that captures "
                    "what the user wants to know. Include all relevant context, constraints, and preferences "
                    "they mentioned. Output ONLY the research query — nothing else.\n\n"
                    f"Conversation:\n{convo}"
                }],
                temperature=0.1,
                max_tokens=200,
                headers=llm_headers,
                timeout=15,
                max_retries=1,
            )
            query = strip_thinking(response).strip().strip('"\'')
            if query and len(query) > 5:
                return query
        except Exception as e:
            logger.warning(f"Query synthesis failed: {e}")

        return _fallback()

    async def generate_plan(
        self, query: str, llm_endpoint: str, llm_model: str, llm_headers: dict = None,
    ) -> Optional[dict]:
        """Generate a research plan for user review before starting research."""
        try:
            from src.deep_research import RESEARCH_PLAN_PROMPT
            from src.llm_core import llm_call_async

            prompt = RESEARCH_PLAN_PROMPT.format(question=query)
            response = await llm_call_async(
                url=llm_endpoint,
                model=llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
                headers=llm_headers,
                timeout=30,
                max_retries=1,
            )
            response = strip_thinking(response)

            # Try to parse structured plan
            import json as _json
            parsed = None
            try:
                # Try to extract JSON from response
                _clean = response.strip()
                if _clean.startswith("```"):
                    _clean = re.sub(r'^```(?:json)?\s*', '', _clean)
                    _clean = re.sub(r'\s*```$', '', _clean)
                import re as _re
                _match = _re.search(r'\{[\s\S]*\}', _clean)
                if _match:
                    parsed = _json.loads(_match.group())
            except Exception:
                pass

            return {
                "sub_questions": parsed.get("sub_questions", []) if parsed else [],
                "key_topics": parsed.get("key_topics", []) if parsed else [],
                "success_criteria": parsed.get("success_criteria", "") if parsed else "",
                "raw": response,
            }
        except Exception as e:
            logger.warning(f"Research plan generation failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Task registry — background research with persistence
    # ------------------------------------------------------------------

    def start_research(
        self,
        session_id: str,
        query: str,
        llm_endpoint: str,
        llm_model: str,
        max_time: int = 300,
        hard_timeout: int = None,
        llm_headers: dict = None,
        on_complete: callable = None,
        prior_report: str = "",
        prior_findings: list = None,
        prior_urls: set = None,
        max_rounds: int = 20,
        search_provider: str = None,
        category: str = None,
        extraction_timeout: int = None,
        extraction_concurrency: int = None,
        owner: str = "",
    ) -> dict:
        """Start research as a background task. Returns task info dict.

        max_rounds is the safety cap; the AI's _should_stop decision (after
        min_rounds) terminates the loop earlier in normal operation.
        """
        # Long-horizon policy: research is NOT bounded by wall-clock time by
        # default. A run keeps going for as long as it is making progress
        # (it still ends when the AI decides it is done, or at max_rounds).
        # What stops a run is a STALL: no progress event for
        # research_stall_timeout_seconds (a hung LLM/search call). While it
        # runs, a check-in digest is recorded every research_checkin_seconds
        # and pushed through the reminder channel, so the user (on the web,
        # on the phone, or through any bridge polling /status) can keep an
        # eye on it. research_run_timeout_seconds > 0 restores an optional
        # wall-clock cap (bounded to [60, 7 days]).
        from src.settings import get_setting
        if hard_timeout is None:
            hard_timeout = _setting_seconds(
                "research_run_timeout_seconds", 0, 60, 7 * 86400) or None
        stall_timeout = _setting_seconds(
            "research_stall_timeout_seconds", 900, 120, 86400)
        checkin_every = _setting_seconds(
            "research_checkin_seconds", 600, 60, 86400)

        # Research's own per-run time budget (checked between rounds).
        # 0 = unlimited. Previously a setting of 0 silently fell back to the
        # caller's 300s default.
        try:
            _mt = int(get_setting("research_max_time_seconds", 0) or 0)
        except (TypeError, ValueError):
            _mt = 0
        max_time = _mt if _mt > 0 else 0

        # Cancel any existing research for this session
        if session_id in self._active_tasks:
            existing = self._active_tasks[session_id]
            if existing.get("status") == "running":
                self.cancel_research(session_id)

        entry = {
            "task": None,
            "researcher": None,
            "query": query,
            "status": "running",
            "progress": {},
            "result": None,
            "started_at": time.time(),
            "last_progress_at": time.time(),
            "checkins": [],
            "limits": {
                "wall_clock_seconds": hard_timeout or 0,
                "stall_seconds": stall_timeout,
                "checkin_seconds": checkin_every,
            },
            "category": category,
            # SECURITY: track ownership so all reads / saves can filter by user.
            "owner": owner or "",
            "session_id": session_id,
        }
        self._active_tasks[session_id] = entry

        def on_progress(event):
            entry["progress"] = event
            entry["last_progress_at"] = time.time()

        _completed = False

        def _guarded_complete(*args, **kwargs):
            nonlocal _completed
            if _completed:
                return
            _completed = True
            if on_complete:
                on_complete(*args, **kwargs)

        async def _supervise(coro):
            """Await the research coroutine while watching it. Raises
            _ResearchLimit on a stall (or on the optional wall-clock cap),
            and records/pushes a check-in every `checkin_every` seconds."""
            inner = asyncio.ensure_future(coro)
            started = entry["started_at"]
            next_checkin = (started + checkin_every) if checkin_every else None
            try:
                while True:
                    done, _ = await asyncio.wait({inner}, timeout=5)
                    if done:
                        return inner.result()
                    now = time.time()
                    if hard_timeout and now - started > hard_timeout:
                        raise _ResearchLimit(f"hit the {hard_timeout}s time limit")
                    idle = now - entry.get("last_progress_at", started)
                    if stall_timeout and idle > stall_timeout:
                        raise _ResearchLimit(
                            f"stalled (no progress for {int(idle // 60)} min)")
                    if next_checkin and now >= next_checkin:
                        next_checkin = now + checkin_every
                        self._checkin(session_id, entry)
            finally:
                if not inner.done():
                    _r = entry.get("researcher")
                    if _r:
                        try:
                            _r.cancel()
                        except Exception:
                            pass
                    inner.cancel()

        async def _run():
            # Supervised run — saves partial results if it stalls or hits an
            # optional wall-clock cap.
            try:
                result = await _supervise(
                    self.call_research_service(
                        query, llm_endpoint, llm_model,
                        max_time=max_time,
                        progress_callback=on_progress,
                        _task_entry=entry,
                        llm_headers=llm_headers,
                        prior_report=prior_report,
                        prior_findings=prior_findings,
                        prior_urls=prior_urls,
                        max_rounds=max_rounds,
                        search_provider=search_provider,
                        category=category,
                        extraction_timeout=extraction_timeout,
                        extraction_concurrency=extraction_concurrency,
                    ),
                )
                entry["result"] = result
                entry["status"] = "done"
                self._save_result(session_id, entry)
                self._notify_finished(session_id, entry, "finished")
                # Persist to DB via callback (ensures result survives even if SSE disconnected)
                try:
                    sources = entry.get("sources", [])
                    researcher = entry.get("researcher")
                    findings = self._extract_raw_findings(researcher.findings) if researcher and researcher.findings else []
                    _guarded_complete(session_id, result, sources, findings)
                except Exception as cb_err:
                    logger.error(f"on_complete callback failed: {cb_err}")
            except _ResearchLimit as limit:
                _elapsed = time.time() - entry["started_at"]
                logger.error(f"Research stopped ({limit.reason}) after {_elapsed:.0f}s for session {session_id}")
                researcher = entry.get("researcher")
                partial = getattr(researcher, "evolving_report", "") if researcher else ""
                if not partial and researcher and researcher.findings:
                    # Salvage: a hang shouldn't erase what was already found.
                    partial = "\n\n".join(
                        f"- **{f.get('title') or f.get('url', 'source')}**\n"
                        f"  {f.get('summary') or f.get('evidence', '')} ({f.get('url', '')})"
                        for f in researcher.findings[:30]
                    )
                if partial:
                    entry["result"] = (
                        self._format_research_report(query, partial, researcher.get_stats(), _elapsed)
                        + f"\n\n> ⚠️ Research {limit.reason} — showing the partial "
                          "results gathered so far (saved to this chat). Ask to continue and it "
                          "picks up from these sources."
                    )
                    entry["status"] = "done"
                    self._save_result(session_id, entry)
                    try:
                        sources = self._extract_sources(researcher.findings) if researcher.findings else []
                        findings = self._extract_raw_findings(researcher.findings) if researcher.findings else []
                        _guarded_complete(session_id, entry["result"], sources, findings)
                    except Exception as e:
                        logger.warning(f"on_complete callback failed in timeout branch: {e}")
                else:
                    entry["result"] = (f"Research {limit.reason} before any sources "
                                       "could be gathered. You can retry, or ask a more specific question.")
                    entry["status"] = "error"
                    _guarded_complete(session_id, entry["result"], [], [])
                on_progress({"phase": "error", "message": f"Research {limit.reason} — partial results saved"})
                self._notify_finished(session_id, entry, limit.reason)
            except asyncio.CancelledError:
                entry["status"] = "cancelled"
                raise
            except Exception as e:
                logger.error(f"Background research failed: {e}", exc_info=True)
                entry["result"] = str(e)
                entry["status"] = "error"

        task = asyncio.create_task(_run())
        entry["task"] = task
        return {"session_id": session_id, "status": "running", "query": query}

    def get_status(self, session_id: str) -> Optional[dict]:
        """Get current research status for a session."""
        avg = self.get_avg_duration()
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            result = {
                "status": entry["status"],
                "progress": entry["progress"],
                "query": entry["query"],
                "started_at": entry["started_at"],
                "elapsed": round(time.time() - entry["started_at"], 1),
                "last_progress_age": round(time.time() - entry.get("last_progress_at", entry["started_at"]), 1),
                "limits": entry.get("limits", {}),
            }
            if entry.get("checkins"):
                result["last_checkin"] = entry["checkins"][-1]
            if avg is not None:
                result["avg_duration"] = round(avg, 1)
            return result
        # Check disk for completed research (skip consumed results)
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("consumed"):
                    return None
                status = data.get("status", "done")
                # Stale "running" snapshot guard: this branch only runs when
                # the task is NOT in memory, so a "running" snapshot whose
                # last checkpoint is older than ~2 minutes means the task no
                # longer exists (server restart / crash). Report it as an
                # error so the SSE loop doesn't wait forever on a dead task.
                # (No started_at cap: long runs are legitimate.)
                if status == "running":
                    _last = data.get("checkpoint_at") or data.get("started_at") or 0
                    _stale = bool(_last) and (time.time() - _last) > 120
                    if _stale:
                        status = "error"
                return {
                    "status": status,
                    "progress": {},
                    "query": data.get("query", ""),
                    "started_at": data.get("started_at", 0),
                }
            except Exception:
                pass
        return None

    # ------------------------------------------------------------------
    # Check-ins — "constant checking" for long-horizon runs
    # ------------------------------------------------------------------

    def _checkin(self, session_id: str, entry: dict) -> dict:
        """Record a progress digest for a running research and push it
        through the user's reminder channel. No LLM call — cheap and safe
        to run every few minutes for hours."""
        r = entry.get("researcher")
        findings = list(getattr(r, "findings", None) or [])
        elapsed = int(time.time() - entry["started_at"])
        latest = []
        for f in findings[-3:]:
            t = (f.get("title") or f.get("url") or "").strip() if isinstance(f, dict) else ""
            if t:
                latest.append(t[:90])
        digest = {
            "at": time.time(),
            "n": len(entry.get("checkins") or []) + 1,
            "elapsed": elapsed,
            "round": getattr(r, "round_count", 0) if r else 0,
            "sources": len(getattr(r, "urls_fetched", None) or ()) if r else 0,
            "findings": len(findings),
            "phase": (entry.get("progress") or {}).get("phase", ""),
            "idle": int(time.time() - entry.get("last_progress_at", entry["started_at"])),
            "latest": latest,
        }
        mins = max(1, elapsed // 60)
        text = (f"Still researching ({mins} min): round {digest['round']}, "
                f"{digest['sources']} sources read, {digest['findings']} findings")
        if digest["phase"]:
            text += f", now {digest['phase']}"
        text += "."
        if latest:
            text += " Latest: " + "; ".join(latest)
        digest["text"] = text
        entry.setdefault("checkins", []).append(digest)
        entry["checkins"] = entry["checkins"][-50:]
        logger.info(f"Research check-in {session_id}: {text}")
        self._push(entry, f"Research check-in #{digest['n']}",
                   text, f"research-{session_id}-checkin-{digest['n']}")
        return digest

    def _notify_finished(self, session_id: str, entry: dict, how: str):
        """Tell the user a long run ended (only if it ran past one check-in
        interval or 3 minutes — short runs are watched live anyway)."""
        elapsed = time.time() - entry["started_at"]
        if elapsed < 180:
            return
        mins = int(elapsed // 60)
        q = (entry.get("query") or "")[:120]
        self._push(entry, f"Research {how} after {mins} min",
                   f"{q}\nOpen the chat to read the report.",
                   f"research-{session_id}-done")

    def _push(self, entry: dict, title: str, body: str, note_id: str):
        """Fire-and-forget push via the configured reminder channel
        (browser / ntfy). Email is skipped for check-ins to avoid inbox spam
        unless research_checkin_push is set to "all"."""
        from src.settings import get_setting
        mode = str(get_setting("research_checkin_push", "auto") or "auto").lower()
        if mode in ("off", "false", "0", "none"):
            return
        channel = str(get_setting("reminder_channel", "browser") or "browser").lower()
        if channel == "email" and mode != "all" and "checkin" in note_id:
            return
        try:
            from routes.note_routes import dispatch_reminder
            loop = asyncio.get_running_loop()
            loop.create_task(dispatch_reminder(
                title=title, note_body=body, note_id=note_id,
                owner=entry.get("owner") or "",
            ))
        except Exception as e:
            logger.debug(f"research push failed: {e}")

    def get_checkins(self, session_id: str) -> list:
        entry = self._active_tasks.get(session_id)
        return list(entry.get("checkins") or []) if entry else []

    def cancel_research(self, session_id: str) -> bool:
        """Cancel running research for a session."""
        if session_id not in self._active_tasks:
            return False
        entry = self._active_tasks[session_id]
        if entry["status"] != "running":
            return False
        researcher = entry.get("researcher")
        if researcher:
            researcher.cancel()
        task = entry.get("task")
        if task and not task.done():
            task.cancel()
        entry["status"] = "cancelled"
        return True

    def get_result(self, session_id: str) -> Optional[str]:
        """Get the completed research result."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            if entry["status"] in ("done", "error", "cancelled"):
                return entry.get("result")
        # Check disk (skip consumed results)
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("consumed"):
                    return None
                return data.get("result")
            except Exception:
                pass
        return None

    def get_sources(self, session_id: str) -> Optional[list]:
        """Get deduplicated source list from research findings."""
        # Check in-memory first
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            if entry.get("sources"):
                return entry["sources"]
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                return self._extract_sources(researcher.findings)
        # Check disk
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("sources")
            except Exception:
                pass
        return None

    def get_raw_findings(self, session_id: str) -> Optional[list]:
        """Get raw per-source findings for display."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                return self._extract_raw_findings(researcher.findings)
        # Check disk
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("raw_findings")
            except Exception as e:
                logger.warning(f"Failed to read raw findings for {session_id}: {e}")
        return None

    @staticmethod
    def _extract_sources(findings: list) -> list:
        """Extract deduplicated [{url, title}] from findings, filtering low-quality ones."""
        seen = set()
        sources = []
        for f in findings:
            url = f.get("url", "")
            title = f.get("title", "") or url
            summary = f.get("summary", "") or f.get("evidence", "")
            if url and url not in seen and not is_low_quality(summary):
                seen.add(url)
                entry = {"url": url, "title": title}
                og_img = f.get("og_image", "")
                if og_img:
                    entry["image"] = og_img
                sources.append(entry)
        return sources

    @staticmethod
    def _extract_raw_findings(findings: list) -> list:
        """Extract [{url, title, summary}] for per-source findings display, filtering junk."""
        try:
            items = []
            for f in findings:
                url = f.get("url", "")
                title = f.get("title", "") or "Untitled"
                summary = f.get("summary", "")
                evidence = f.get("evidence", "")
                content = summary if summary else (evidence[:2000] if evidence else "")
                if url and content and not is_low_quality(content):
                    items.append({"url": url, "title": title, "summary": content})
            return items
        except Exception as e:
            logger.warning(f"Failed to extract raw findings: {e}")
            return []

    def get_avg_duration(self) -> Optional[float]:
        """Compute average research duration from completed results on disk."""
        durations = []
        try:
            for p in RESEARCH_DATA_DIR.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if data.get("status") == "done":
                        started = data.get("started_at", 0)
                        completed = data.get("completed_at", 0)
                        if started and completed and completed > started:
                            durations.append(completed - started)
                except Exception:
                    continue
        except Exception:
            pass
        if durations:
            return sum(durations) / len(durations)
        return None

    def clear_result(self, session_id: str):
        """Mark result as consumed so it won't be re-rendered on refresh.

        Keeps the JSON on disk so visual reports can be generated later.
        """
        self._active_tasks.pop(session_id, None)
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["consumed"] = True
                path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass

    def _save_result(self, session_id: str, entry: dict):
        """Persist completed research result to disk."""
        try:
            # Extract and cache sources + raw findings
            sources = []
            raw_findings = []
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                sources = self._extract_sources(researcher.findings)
                raw_findings = self._extract_raw_findings(researcher.findings)
            entry["sources"] = sources

            path = RESEARCH_DATA_DIR / f"{session_id}.json"
            data = {
                "query": entry["query"],
                "status": entry["status"],
                "result": entry["result"],
                "raw_report": entry.get("raw_report", ""),
                "sources": sources,
                "raw_findings": raw_findings,
                "stats": entry.get("stats"),
                "category": entry.get("category"),
                "started_at": entry["started_at"],
                "completed_at": time.time(),
                # SECURITY: stamp owner so route handlers can filter by user.
                "owner": entry.get("owner", ""),
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Research result saved to {path}")
            try:
                from src.event_bus import fire_event
                fire_event("research_completed", entry.get("owner") or None)
            except Exception:
                logger.debug("research_completed event dispatch failed", exc_info=True)
        except Exception as e:
            logger.error(f"Failed to save research result: {e}")

    def _get_session_json(self, session_id: str) -> Optional[dict]:
        """Load the saved research JSON for a session, if it exists."""
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def get_report_html(self, session_id: str) -> Optional[str]:
        """Generate the visual HTML report for a session (always fresh from JSON)."""
        json_path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if not json_path.exists():
            logger.warning(f"No JSON found for visual report: {json_path}")
            return None

        try:
            from src.visual_report import generate_visual_report

            data = json.loads(json_path.read_text(encoding="utf-8"))
            report_md = data.get("raw_report") or data.get("result", "")
            html_content = generate_visual_report(
                question=data.get("query", ""),
                report_markdown=report_md,
                sources=data.get("sources"),
                stats=data.get("stats"),
                category=data.get("category"),
                session_id=session_id,
                hidden_images=data.get("hidden_images") or [],
            )
            logger.info(f"Visual report generated for {session_id}")
            return html_content
        except Exception as e:
            logger.error(f"Failed to generate visual report: {e}")
            return None

    def hide_image(self, session_id: str, image_url: str) -> bool:
        """Add image_url to the persisted hidden_images list for a research."""
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hidden = data.get("hidden_images") or []
            if image_url not in hidden:
                hidden.append(image_url)
                data["hidden_images"] = hidden
                path.write_text(json.dumps(data), encoding="utf-8")
                logger.info(f"Hid image {image_url[:80]} for research {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to hide image: {e}")
            return False

    def unhide_all_images(self, session_id: str) -> bool:
        """Clear the hidden_images list for a research."""
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["hidden_images"] = []
            path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Cleared hidden_images for research {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to unhide images: {e}")
            return False

    @staticmethod
    async def _probe_endpoint(endpoint: str, model: str, headers: dict = None):
        """Quick probe to verify the LLM endpoint/model responds before research."""
        from src.llm_core import llm_call_async
        try:
            logger.info(f"Probing {model} at {endpoint} (has_auth={bool(headers and 'Authorization' in (headers or {}))})")
            await llm_call_async(
                url=endpoint,
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                temperature=0,
                max_tokens=5,
                headers=headers,
                timeout=15,
                max_retries=1,
            )
            logger.info(f"Endpoint probe OK: {model}")
        except Exception as e:
            logger.error(f"Probe failed for {model}: {e}")
            raise RuntimeError(_format_probe_failure(model, e)) from e

    async def call_research_service(
        self,
        query: str,
        llm_endpoint: str,
        llm_model: str,
        max_time: int = 300,
        progress_callback=None,
        _task_entry: dict = None,
        llm_headers: dict = None,
        prior_report: str = "",
        prior_findings: list = None,
        prior_urls: set = None,
        max_rounds: int = 20,
        search_provider: str = None,
        category: str = None,
        extraction_timeout: int = None,
        extraction_concurrency: int = None,
    ) -> str:
        """
        Run iterative deep research using the LLM-in-the-loop DeepResearcher.

        Args:
            query: Research question
            llm_endpoint: LLM endpoint URL for chat completions
            llm_model: Model name/ID
            max_time: Maximum research time in seconds (default 5 minutes)
            _task_entry: Internal - registry entry to store researcher ref
            prior_report: Previous report to continue from.
            prior_findings: Previous findings to build on.
            prior_urls: URLs already visited (won't re-fetch).

        Returns:
            Formatted research report with expandable section and summary
        """
        is_continuation = bool(prior_report)
        logger.info(f"{'Continuing' if is_continuation else 'Starting'} IterResearch Deep Research")
        logger.info(f"Query: {query}")
        logger.info(f"LLM: {llm_endpoint} / {llm_model}")
        logger.info(f"Max time: {max_time}s")
        if is_continuation:
            logger.info(f"Prior: {len(prior_findings or [])} findings, {len(prior_urls or set())} URLs")

        # Probe the endpoint before committing to a long research run
        if progress_callback:
            progress_callback({"phase": "probing", "model": llm_model})
        await self._probe_endpoint(llm_endpoint, llm_model, llm_headers)

        try:
            from src.deep_research import DeepResearcher

            from src.settings import get_setting
            _max_report_tokens = int(get_setting("research_max_tokens", 16384))
            _extraction_timeout = _bounded_int(
                extraction_timeout if extraction_timeout is not None else get_setting("research_extraction_timeout_seconds", 90),
                default=90,
                minimum=15,
                maximum=3600,
            )
            _extraction_concurrency = _bounded_int(
                extraction_concurrency if extraction_concurrency is not None else get_setting("research_extraction_concurrency", 3),
                default=3,
                minimum=1,
                maximum=12,
            )

            # Throttled disk checkpoints: wrap the progress callback so that,
            # at most once per ~10s, we snapshot the evolving report + findings
            # to data/deep_research/<session>.json. A server crash mid-hang then
            # leaves "what it already researched" on disk, keyed to the chat ID.
            _session_id = (_task_entry or {}).get("session_id")
            _last_checkpoint = [0.0]

            def _checkpointing_progress(event):
                if progress_callback:
                    progress_callback(event)
                if not _session_id:
                    return
                now = time.time()
                if now - _last_checkpoint[0] < 10:
                    return
                _last_checkpoint[0] = now
                try:
                    _res = (_task_entry or {}).get("researcher")
                    _snap = {
                        "query": query,
                        "status": "running",
                        "checkpoint_at": now,
                        "evolving_report": getattr(_res, "evolving_report", "") if _res else "",
                        "sources": self._extract_sources(_res.findings) if _res and _res.findings else [],
                        "raw_findings": self._extract_raw_findings(_res.findings) if _res and _res.findings else [],
                        "started_at": (_task_entry or {}).get("started_at", now),
                        "owner": (_task_entry or {}).get("owner", ""),
                    }
                    _path = RESEARCH_DATA_DIR / f"{_session_id}.json"
                    _path.write_text(json.dumps(_snap), encoding="utf-8")
                except Exception:
                    pass

            researcher = DeepResearcher(
                llm_endpoint=llm_endpoint,
                llm_model=llm_model,
                llm_headers=llm_headers,
                max_rounds=max_rounds,
                min_rounds=min(3, max_rounds),
                max_time=max_time,
                max_report_tokens=_max_report_tokens,
                extraction_timeout=_extraction_timeout,
                extraction_concurrency=_extraction_concurrency,
                progress_callback=_checkpointing_progress,
                search_provider=search_provider,
                category=category,
            )
            if _task_entry is not None:
                _task_entry["researcher"] = researcher

            start_time = time.time()
            report = await researcher.research(
                query,
                prior_report=prior_report,
                prior_findings=prior_findings,
                prior_urls=prior_urls,
            )
            elapsed = time.time() - start_time

            stats = researcher.get_stats()
            logger.info("IterResearch completed successfully")
            for key, value in stats.items():
                logger.info(f"  {key}: {value}")

            # Store raw report and stats for visual report generation
            if _task_entry is not None:
                _task_entry["raw_report"] = strip_thinking(report)
                _task_entry["stats"] = stats

            return self._format_research_report(query, report, stats, elapsed)

        except Exception as e:
            logger.error(f"DeepResearcher failed: {e}", exc_info=True)
            return await self._fallback_research(query, llm_endpoint, llm_model, max_time, str(e))

    async def _fallback_research(
        self, query: str, llm_endpoint: str, llm_model: str,
        max_time: int, primary_error: str,
    ) -> str:
        """Fall back to legacy engine, then to basic web search."""
        # Try legacy orchestrator
        if self._legacy_engine:
            try:
                import asyncio
                logger.info("Falling back to legacy ResearchOrchestrator...")
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, self._legacy_engine.start_research, query, max_time
                )
                stats = self._get_legacy_stats()
                elapsed = float(stats.get("Duration", "0").rstrip("s") or 0)
                return self._format_research_report(query, result, stats, elapsed)
            except Exception as e:
                logger.error(f"Legacy engine also failed: {e}")

        # Fall back to basic web search
        return self._handle_research_failure(query, primary_error)

    def _get_legacy_stats(self) -> dict:
        """Get statistics from the legacy research engine."""
        if not self._legacy_engine:
            return {}
        try:
            tracker = self._legacy_engine.progress_tracker
            return {
                "Findings": len(self._legacy_engine.findings),
                "Sources": len(self._legacy_engine.source_reports),
                "Searches": tracker.counters['searches_executed'],
                "URLs": tracker.counters['urls_processed'],
            }
        except Exception:
            return {}

    def _format_research_report(
        self, query: str, full_report: str, stats: dict, elapsed: float,
    ) -> str:
        """Format research report (markdown only — sources/findings handled by frontend)."""
        full_report = strip_thinking(full_report)
        summary_lines = [
            f"**Duration:** {elapsed:.1f}s",
            f"**Rounds:** {stats.get('Rounds', stats.get('Findings', '?'))}",
            f"**Queries:** {stats.get('Queries', stats.get('Searches', '?'))}",
            f"**URLs Analyzed:** {stats.get('URLs', '?')}",
        ]
        summary_text = " | ".join(summary_lines)

        formatted = f"""---

## Research Summary

{summary_text}

---

{full_report}
"""
        return formatted

    def _format_error_response(self, error_msg: str, query: str) -> str:
        """Format error response in a user-friendly way."""
        return f"""## Research Engine Unavailable

**Query:** {query}

**Error:** {error_msg}

**Please check:**
1. LLM endpoint is reachable
2. SearXNG is running at the configured instance
3. Application logs for detailed error information

**Troubleshooting:**
- Test basic search: Try the web search toggle first
- Check search config: `/api/search/config`
- Review logs for initialization errors
"""

    def _handle_research_failure(self, query: str, error: str) -> str:
        """Handle research failure with fallback to basic search."""
        try:
            logger.info("Attempting fallback to basic web search...")
            from src.search import comprehensive_web_search

            search_result = comprehensive_web_search(query)

            return f"""## Research Failed - Basic Search Fallback

**Query:** {query}

**Error:** {error}

**Note:** The deep research engine encountered an error. Here are basic search results instead:

---

### Basic Web Search Results

{search_result}

---

**To fix deep research:**
1. Check that your LLM endpoint and search provider are properly configured
2. Verify network connectivity
3. Review application logs for detailed error information

Try the web search toggle for simpler queries, or fix the research engine for comprehensive analysis.
"""

        except Exception as e2:
            logger.error(f"Fallback search also failed: {e2}", exc_info=True)
            return f"""## Complete Research Failure

**Primary Error:** {error}
**Fallback Error:** {str(e2)}

**Please check:**
1. Search provider configuration in Settings -> Search Settings
2. Network connectivity to search APIs
3. Application logs for detailed error information
4. That SearXNG is running (if using SearXNG)

**Debug Info:**
- Search config endpoint: `/api/search/config`
- Test basic search toggle with a simple query first
"""
