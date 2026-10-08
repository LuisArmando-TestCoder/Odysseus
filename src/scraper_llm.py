"""
src/scraper_llm.py
──────────────────────────────────────────────────────────────────────────────
Python port of scraperLLM.ts — Gemini web-UI scraper via Selenium.

Instead of calling a self-hosted LLM HTTP endpoint, this drives
https://gemini.google.com/app through Chrome so the web UI acts as the
inference backend.  All three Odysseus LLM call-sites (llm_call,
llm_call_async, stream_llm) delegate here when ODYSSEUS_LLM_SCRAPER=1.

Setup
─────
1.  pip install selenium          (Selenium ≥4.6 auto-manages chromedriver)
2.  Make sure Google Chrome is installed on this machine.
3.  Set ODYSSEUS_LLM_SCRAPER=1 in your .env (or environment).
4.  On first run a visible Chrome window opens — log into Google when
    prompted.  The session is saved in ~/.odysseus_chrome_profile so you
    only need to do this once.

Optional env vars
─────────────────
  SCRAPER_HEADLESS=1             Run Chrome without a visible window.
                                 Requires a valid persistent profile that
                                 already has an active Google session.
  SCRAPER_CHROME_PROFILE=<path>  Override profile directory.
                                 Default: ~/.odysseus_chrome_profile
"""

import os
import re
import json
import time
import logging
import threading
from typing import Dict, List, Optional, Union

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException

logger = logging.getLogger(__name__)

# ── Module-level globals ──────────────────────────────────────────────────────

_driver_instance: Optional[webdriver.Chrome] = None

# Selenium WebDriver is NOT thread-safe.  Every public entry-point must hold
# this lock for its entire duration so concurrent FastAPI route handlers never
# interleave their browser operations.
_driver_lock = threading.Lock()


# ── Browser lifecycle ─────────────────────────────────────────────────────────

def init_browser() -> webdriver.Chrome:
    """Initialize (or return) the global Chrome WebDriver instance."""
    global _driver_instance
    if _driver_instance is not None:
        return _driver_instance

    logger.info("🚀 [Scraper] Initializing Chrome WebDriver...")
    options = Options()
    options.add_argument("--window-size=1200,1000")
    options.add_argument("--disable-blink-features=AutomationControlled")
    # Suppress the "Chrome is being controlled by automated software" banner
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Persistent Chrome profile — keeps the Google login alive between restarts.
    profile_dir = os.getenv(
        "SCRAPER_CHROME_PROFILE",
        os.path.join(os.path.expanduser("~"), ".odysseus_chrome_profile"),
    )
    os.makedirs(profile_dir, exist_ok=True)
    options.add_argument(f"--user-data-dir={profile_dir}")

    # Headless by default — set SCRAPER_VISIBLE=1 to get a visible Chrome window
    # (useful the first time you need to log into Google, or for debugging).
    if os.getenv("SCRAPER_VISIBLE", "").lower() not in ("1", "true", "yes"):
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-extensions")
        # --no-sandbox and --disable-dev-shm-usage are Linux/Docker flags.
        # On macOS they can crash Chrome, so only add them on Linux.
        if os.name != "nt" and os.uname().sysname.lower() == "linux":
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

    _driver_instance = webdriver.Chrome(options=options)
    logger.info("🌐 [Scraper] Navigating to Gemini...")
    _driver_instance.get("https://gemini.google.com/app")

    logger.info("✋ [Scraper] Waiting for chat box — log in to Google if prompted...")
    _wait_for_element(_driver_instance, "div.ql-editor", timeout_ms=60_000)
    logger.info("🔓 [Scraper] Chat box found — scraper ready.\n")
    return _driver_instance


def close_browser() -> None:
    """Quit the global Chrome WebDriver and clear the module reference."""
    global _driver_instance
    if _driver_instance is not None:
        logger.info("🛑 [Scraper] Closing Chrome WebDriver...")
        try:
            _driver_instance.quit()
        except Exception:
            pass
        _driver_instance = None


def reset_chat() -> webdriver.Chrome:
    """Navigate back to the Gemini app root to start a fresh chat session.

    Mirrors TypeScript resetChat(): navigates to the app root which forces a
    new session in the UI and prevents context bleed between calls.
    """
    driver = init_browser()
    logger.info("🧹 [Scraper] Resetting chat context (new chat)...")
    driver.get("https://gemini.google.com/app")
    _wait_for_element(driver, "div.ql-editor", timeout_ms=60_000)
    return driver


# ── Private helpers ───────────────────────────────────────────────────────────

def _wait_for_element(
    driver: webdriver.Chrome,
    css_selector: str,
    timeout_ms: int = 60_000,
):
    """Wait for a CSS-matched element to appear and become visible.

    Mirrors TypeScript waitForElement().
    """
    timeout_s = timeout_ms / 1000.0
    wait = WebDriverWait(driver, timeout_s)
    element = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
    )
    wait.until(EC.visibility_of(element))
    return element


def _wait_and_extract_response(driver: webdriver.Chrome) -> str:
    """Poll the DOM until Gemini stops streaming its reply.

    Mirrors TypeScript waitForAndExtractGeminiResponse():
    - waits 3 s for the new response block to mount in the DOM
    - polls every 1.5 s
    - requires 3 consecutive identical reads (~4.5 s of silence) to declare done
    - hard-times-out after ~2 min (80 × 1.5 s)
    """
    logger.info("⏳ [Scraper] Waiting for Gemini to finish typing...")
    previous_text = ""
    stable_count = 0
    attempt = 0

    # Give the DOM time to add the new response block.
    time.sleep(3)

    while True:
        try:
            elements = driver.find_elements(
                By.CSS_SELECTOR,
                "message-content, .markdown, .model-response-text",
            )
            if elements:
                current_text = elements[-1].text
                if current_text:
                    if current_text == previous_text:
                        stable_count += 1
                    else:
                        stable_count = 0
                        previous_text = current_text

                    # Three identical consecutive readings → response is complete.
                    if stable_count >= 3:
                        logger.info("✅ [Scraper] Gemini response complete.")
                        return current_text
        except StaleElementReferenceException:
            pass  # DOM is still updating — ignore and retry
        except WebDriverException:
            pass

        attempt += 1
        if attempt > 80:  # ~2-minute hard timeout
            logger.warning(
                "⚠️  [Scraper] Timed out waiting for response — returning what we have."
            )
            return previous_text

        time.sleep(1.5)


def _extract_json_from_dash_block(text: str) -> Optional[str]:
    """Extract a JSON object from a ---{...}--- fence or fall back to bare JSON.

    Mirrors TypeScript extractJsonFromDashBlock().
    """
    # Primary: explicit --- fence used by the JSON-prompt format
    dash_match = re.search(r"---\s*(\{[\s\S]*?\})\s*---", text)
    if dash_match:
        return dash_match.group(1)
    # Fallback: first JSON object anywhere in the text
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    return match.group(0) if match else None


def _compile_messages(messages: List[Dict]) -> str:
    """Flatten an OpenAI-style messages list into a single Gemini prompt.

    Mirrors TypeScript compileMessages(): wraps the history in labelled blocks
    so that a stateless Gemini chat session can "replay" the conversation.
    """
    out = (
        "Please read the following conversation history and strictly adopt the "
        "persona defined in the SYSTEM instructions. "
        "Then, respond to the final USER message.\n\n"
        "=== CONVERSATION HISTORY ===\n"
    )
    for msg in messages:
        role = (msg.get("role") or "user").upper()
        content = msg.get("content") or ""
        # content may be a list of multimodal blocks — flatten to text only
        if isinstance(content, list):
            parts = [
                blk.get("text", "")
                for blk in content
                if isinstance(blk, dict) and blk.get("type") == "text"
            ]
            content = "\n".join(parts)
        out += f"[{role}]: {content}\n\n"
    out += "=== END HISTORY ===\n\nNow, generate the next response as the ASSISTANT."
    return out


# ── Public API ────────────────────────────────────────────────────────────────

def call_scraper_llm(
    messages: List[Dict],
    fields: Optional[List[str]] = None,
) -> Union[str, Dict]:
    """Send a conversation to Gemini via Selenium and return the response.

    This is the Python equivalent of GeminiWebAdapter.createCompletion() from
    scraperLLM.ts: it resets the chat to avoid context bleed, compiles the
    message history into one prompt, injects it, submits, and extracts the reply.

    Args:
        messages:  OpenAI-style list of {role, content} dicts.
        fields:    Optional list of JSON keys.  When provided, the prompt asks
                   Gemini to respond with a ---{...}--- JSON block and the
                   parsed dict is returned.

    Returns:
        str   — raw response text when fields is None / empty.
        dict  — parsed JSON object  when fields is provided.

    Thread safety:
        Holds _driver_lock for its entire execution so concurrent FastAPI route
        handlers never interleave Selenium operations on the shared driver.
    """
    requires_json = bool(fields)

    with _driver_lock:
        # Reset context — matches GeminiWebAdapter.createCompletion() which
        # calls resetChat() before each request to prevent context bleed.
        driver = reset_chat()

        # Build the full composite prompt.
        master_prompt = _compile_messages(messages)
        if requires_json:
            field_lines = ",\n".join(
                f'  "{f}": "Your {f} response value"' for f in fields
            )
            master_prompt += (
                "\n\nExpected response format should be only like this without prefix "
                "or suffix other than surrounded by ---...--- something like this:\n"
                f"---\n{{\n{field_lines}\n}}\n---"
            )

        if requires_json:
            logger.info("🤖 [Scraper] Sending prompt — expecting JSON fields: %s", fields)
        else:
            logger.info("🤖 [Scraper] Sending prompt — expecting raw text.")

        try:
            text_area = _wait_for_element(driver, "div.ql-editor", timeout_ms=60_000)

            # Inject prompt via JavaScript.
            # Selenium's execute_script passes extra positional args as
            # arguments[0], arguments[1], … inside the script string.
            # This mirrors the TypeScript executeScript block exactly.
            driver.execute_script(
                """
                var el   = arguments[0];
                var text = arguments[1];
                el.focus();
                el.innerText = text;
                el.dispatchEvent(new Event('input',  { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                text_area,
                master_prompt,
            )

            # Brief pause so the UI picks up the injected text before we click Send.
            time.sleep(2)

            # Find and click the Send button.
            send_btn = _wait_for_element(
                driver, "button[aria-label*='Send']", timeout_ms=15_000
            )
            send_btn.click()

            # Wait for the response to stabilize.
            raw_response = _wait_and_extract_response(driver)

            if not raw_response:
                raise RuntimeError("[Scraper] Failed to extract text from Gemini UI.")

            # ── Raw-text mode ──────────────────────────────────────────────
            if not requires_json:
                return raw_response

            # ── JSON mode ──────────────────────────────────────────────────
            json_string = _extract_json_from_dash_block(raw_response)
            if not json_string:
                logger.error("[Scraper] Raw response dump:\n%s", raw_response)
                raise RuntimeError(
                    "[Scraper] Could not find a valid JSON block "
                    "surrounded by --- in the response."
                )

            parsed: Dict = json.loads(json_string)
            missing = [f for f in fields if f not in parsed]
            if missing:
                logger.warning(
                    "⚠️  [Scraper] LLM omitted requested fields: %s", missing
                )
            return parsed

        except Exception:
            logger.exception("❌ [Scraper] Error in call_scraper_llm")
            raise
