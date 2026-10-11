# services/code/pipeline.py
"""Code-execution neural pipeline (decide → forge → execute).

Mirrors the voice pipeline's multi-agent shape, but for code:

    user request
         │
         ▼
    ① DECIDE   — ONE utility call: does this need code at all? If so, what
                 language and execution target (system | browser)?
         │
         ▼
    ② FORGE    — ONE utility call: write the actual script (self-contained,
                 no placeholders), in the chosen language.
         │
         ▼
    ③ EXECUTE  — run it. System target → subprocess (bash/python); browser
                 target → the guarded run_javascript bridge. The real output
                 is streamed back as the final stage.

There is deliberately NO compressor here: the decision is a single yes/no
("does this need code?"), not a memory hierarchy. The three stages stream as
Server-Sent Events so the UI can render the pipeline live.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Dict, Optional

from src.decisions import DecisionsUnavailable, jev_available, jev_decide
from src.endpoint_resolver import resolve_endpoint
from src.llm_core import llm_call_async

logger = logging.getLogger(__name__)

# Execution targets
TARGET_SYSTEM = "system"    # run on the server (bash / python)
TARGET_BROWSER = "browser"  # run in the user's browser (guarded JS)

# Languages the forger may emit that must run in the user's browser.
LANG_BROWSER = {"javascript", "js", "html"}

DECIDER_PROMPT = """You are the decision maker of a code-execution pipeline. A user has made a request.
Decide whether the request needs CODE to be written and executed, and if so, what kind.

Answer ONLY with JSON:
{"needs_code": 0..1, "language": "python|bash|javascript|html|none",
 "target": "system|browser|none", "reason": "<short phrase>"}

- needs_code: probability the request is best fulfilled by writing and running a script
  (convert an image, rename files, parse data, fetch/transform something, compute a result,
  automate a browser action, etc.). Pure conversation, explanation, or a question that needs
  no computation is 0.
- language: the best language for the job. python for data/image/file/HTTP work; bash for
  shell plumbing; javascript for anything that must run in the user's browser (read the page,
  control the UI, open a link, manipulate the DOM); html for a self-contained page.
- target: "system" if the code should run on the server (python/bash), "browser" if it must
  run in the user's own browser (javascript/html). Prefer "system" for pure computation and
  file work; use "browser" only when the task genuinely needs the browser.
- reason: one short phrase.
"""

FORGER_PROMPT = """You are the code forger of a code-execution pipeline. A separate executor will run
your script and return its real output. Write ONE complete, self-contained script in the requested
language that fulfills the user's request.

RULES:
- Output ONLY the code. No markdown fences, no explanation, no preamble, no "here is the script".
- The code must be complete and runnable as-is — no placeholders, no "TODO", no ellipses.
- Prefer the standard library; only use a third-party library if it is essential, and note it in a
  comment at the top (the executor will install it if needed).
- For python: write a script that prints its result to stdout. Use sys.argv for any input paths.
- For bash: a plain shell script.
- For javascript (browser target): write a function body that uses `return <expr>;` to return a
  value, or performs the DOM/browser action directly.
- For html: a complete, self-contained HTML document.
- Do NOT include anything destructive (no rm -rf, no sudo, no credential exfiltration).
"""


def _ev(**kw) -> Dict[str, Any]:
    kw.setdefault("ts", time.time())
    return kw


def _parse_json(raw: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _strip_fence(text: str) -> str:
    """Strip a wrapping markdown code fence if the model added one anyway."""
    t = (text or "").strip()
    m = re.fullmatch(r"```[a-zA-Z0-9]*\s*\n?([\s\S]*?)\n?```", t)
    if m:
        return m.group(1).strip()
    return t


async def _utility(owner: Optional[str], system: str, user: str, max_tokens: int = 400) -> str:
    url, model, headers = resolve_endpoint("utility", owner=owner or None)
    if not url or not model:
        raise RuntimeError("No utility/default model configured")
    return await llm_call_async(
        url, model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.1, max_tokens=max_tokens, headers=headers, timeout=60,
        reasoning_effort="low",
    )


async def _decide(owner: str, request: str, use_jev: bool) -> Dict[str, Any]:
    """One decision call: does this need code, and what kind?"""
    if use_jev:
        try:
            ans = await jev_decide({"request": request}, {
                "needs_code": {
                    "type": "noul",
                    "instructions": (
                        "Does this request need a script to be written and executed to be fulfilled "
                        "(convert an image, rename files, parse data, fetch/transform something, "
                        "compute a result, automate a browser action)? Pure conversation, explanation, "
                        "or a question needing no computation is false."
                    ),
                    "criteria": {
                        "true": "Needs code written and executed",
                        "false": "No code needed — answer/explain directly",
                    },
                },
                "language": {
                    "type": "choice",
                    "instructions": "Best language for the job.",
                    "criteria": {
                        "python": "Data, image, file, HTTP, computation",
                        "bash": "Shell plumbing, file ops",
                        "javascript": "Must run in the user's browser",
                        "html": "A self-contained page",
                        "none": "No code needed",
                    },
                },
                "target": {
                    "type": "choice",
                    "instructions": "Where should the code run?",
                    "criteria": {
                        "system": "On the server (python/bash)",
                        "browser": "In the user's browser (javascript/html)",
                        "none": "No code needed",
                    },
                },
            })
            return {
                "engine": "jev",
                "needs_code": _f(ans["needs_code"]["value"]),
                "language": ans["language"]["value"] or "none",
                "target": ans["target"]["value"] or "none",
                "reason": "",
            }
        except (DecisionsUnavailable, Exception) as e:
            logger.info(f"code decider: JEV unavailable → LLM fallback ({e})")

    data = _parse_json(await _utility(owner, DECIDER_PROMPT, request[:6000], 120))
    return {
        "engine": "llm",
        "needs_code": _f(data.get("needs_code")),
        "language": data.get("language") or "none",
        "target": data.get("target") or "none",
        "reason": str(data.get("reason") or "").strip(),
    }


async def _forge(owner: str, request: str, language: str, target: str) -> str:
    """Write the actual script."""
    prompt = FORGER_PROMPT + f"\n\nRequested language: {language}\nExecution target: {target}\n"
    raw = await _utility(owner, prompt, request[:8000], 2000)
    return _strip_fence(raw)


async def _execute_system(code: str, language: str) -> Dict[str, Any]:
    """Run code on the server via subprocess."""
    import subprocess
    import tempfile
    import os

    lang = (language or "").lower()
    tmp_path = None
    if lang in ("bash", "sh", "shell"):
        argv = ["bash", "-c", code]
    else:  # python — write to a temp file so multi-line code survives quoting
        fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="odysseus-code-")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(code)
        except Exception as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return {"output": f"Failed to write script: {e}", "exit_code": 1}
        argv = ["python3", tmp_path]

    try:
        result = await asyncio.to_thread(
            subprocess.run, argv, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"output": "Execution timed out (120s)", "exit_code": 1}
    except Exception as e:
        return {"output": str(e), "exit_code": 1}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    output = (result.stdout or "").strip()
    if result.returncode != 0 and result.stderr:
        output += "\nSTDERR: " + result.stderr.strip()
    return {"output": output or "(no output)", "exit_code": result.returncode}


async def _execute_browser(code: str, session_id: Optional[str], owner: Optional[str],
                            progress_cb) -> Dict[str, Any]:
    """Run code in the user's browser via the guarded run_javascript bridge."""
    from src.browser_eval import do_run_javascript
    return await do_run_javascript(code, session_id, owner, progress_cb=progress_cb)


async def run_pipeline(
    owner: str,
    request: str,
    session_id: Optional[str] = None,
    progress_cb=None,
) -> AsyncIterator[Dict[str, Any]]:
    """Run decide → forge → execute, streaming each stage as SSE events.

    `progress_cb` is an optional async callable(dict) used to push the
    browser eval_request to the frontend (same contract as do_run_javascript).
    """
    from src.cost_ledger import set_cost_scope
    set_cost_scope(session_id or None, "code-pipeline", owner)

    request = (request or "").strip()
    if not request:
        yield _ev(type="done", executed=False, reason="empty-request")
        return

    use_jev = jev_available()
    yield _ev(type="hello", engine="jev" if use_jev else "llm")

    # ① DECIDE ────────────────────────────────────────────────────────────────
    t0 = time.time()
    yield _ev(type="stage", stage="decide", status="running", engine="jev" if use_jev else "llm")
    try:
        from src.cost_ledger import cost_scope
        with cost_scope(source="code-decide"):
            decision = await _decide(owner, request, use_jev)
    except Exception as e:
        logger.warning(f"code decider failed: {e}")
        yield _ev(type="stage", stage="decide", status="error", error=str(e)[:200])
        yield _ev(type="done", executed=False, reason="decider-error")
        return

    ms = int((time.time() - t0) * 1000)
    decision["ms"] = ms
    yield _ev(type="decision", decision=decision, ms=ms)
    yield _ev(type="stage", stage="decide", status="done" if decision.get("needs_code", 0) >= 0.5 else "idle",
              ms=ms, engine=decision.get("engine"))

    if decision.get("needs_code", 0) < 0.5 or decision.get("language") in (None, "none"):
        yield _ev(type="done", executed=False, reason="no-code-needed")
        return

    language = decision["language"]
    target = decision.get("target") or (
        TARGET_BROWSER if language in LANG_BROWSER else TARGET_SYSTEM
    )

    # ② FORGE ─────────────────────────────────────────────────────────────────
    t0 = time.time()
    yield _ev(type="stage", stage="forge", status="running")
    try:
        from src.cost_ledger import cost_scope
        with cost_scope(source="code-forge"):
            code = await _forge(owner, request, language, target)
    except Exception as e:
        logger.warning(f"code forger failed: {e}")
        yield _ev(type="stage", stage="forge", status="error", error=str(e)[:200])
        yield _ev(type="done", executed=False, reason="forger-error")
        return

    if not code:
        yield _ev(type="stage", stage="forge", status="idle", ms=int((time.time() - t0) * 1000))
        yield _ev(type="done", executed=False, reason="forger-declined")
        return

    ms = int((time.time() - t0) * 1000)
    yield _ev(type="stage", stage="forge", status="done", ms=ms)
    yield _ev(type="forged", language=language, target=target, code=code)

    # ③ EXECUTE ───────────────────────────────────────────────────────────────
    t0 = time.time()
    yield _ev(type="stage", stage="execute", status="running", target=target)
    try:
        from src.cost_ledger import cost_scope
        with cost_scope(source="code-execute"):
            if target == TARGET_BROWSER:
                result = await _execute_browser(code, session_id, owner, progress_cb)
            else:
                result = await _execute_system(code, language)
    except Exception as e:
        logger.warning(f"code executor failed: {e}")
        yield _ev(type="stage", stage="execute", status="error", error=str(e)[:200])
        yield _ev(type="done", executed=False, reason="executor-error")
        return

    ms = int((time.time() - t0) * 1000)
    ok = result.get("exit_code", 1) == 0
    yield _ev(type="stage", stage="execute", status="done" if ok else "error", ms=ms,
              exit_code=result.get("exit_code"), target=target)
    yield _ev(type="executed", output=result.get("output", ""), exit_code=result.get("exit_code"),
              blocked=result.get("blocked", False), target=target)
    yield _ev(type="done", executed=True, exit_code=result.get("exit_code"))