"""
tool_schemas.py

OpenAI-compatible function tool schemas and the converter that turns
native function calls back into ToolBlocks for the execution pipeline.

Extracted from agent_tools.py to keep schema definitions separate from
tool parsing / execution logic.
"""

import json
import logging
from typing import Optional

from src.agent_tools import ToolBlock, TOOL_TAGS
from src.tool_parsing import _TOOL_NAME_MAP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI-compatible function tool schemas
# ---------------------------------------------------------------------------
FUNCTION_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command (full access)",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Execute Python code to compute a result or test something",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Quick single web lookup for a fact or current event mid-task. NOT for 'research X' / 'do research on X' — those are deep-research jobs; use trigger_research instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "time_filter": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "Optional freshness filter for news/latest/today queries"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch and read the text content of a specific URL the user names (e.g. 'check example.com', 'what's on this page <url>'). Use when you already have a concrete URL/domain. NOT for open-ended searches (use web_search) or 'research X' jobs (use trigger_research).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL or domain to fetch (http/https; a bare domain like example.com is fine)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "universal_search",
            "description": "Run the Universal Search Framework. Deconstructs the prompt into Static Anchors and Variable Claims, fires three parallel query tiers (proximity / broad / adversarial), scores retrieved evidence via a validation matrix (source consensus, temporal recency, semantic distance), and returns a grounded synthesis or an explicit ignorance default. Use for factual-verification questions, checking a claim, or validating any assertion — especially where hallucination must be avoided.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The claim or question to verify"},
                    "time_filter": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "Optional freshness filter"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_transcript",
            "description": "Fetch the transcript (captions) of a YouTube video from its URL. Returns the stitched full text plus timestamped segments; prioritizes manual captions over auto-generated (ASR) ones, and translates to English when the only track is in another language. Use when the user shares a YouTube link or asks what a video says.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The YouTube video URL (youtube.com/watch?v=..., youtu.be/..., shorts, or embed)"},
                    "language": {"type": "string", "description": "Target language code to translate the transcript to when no track matches (default 'en')"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reddit_search",
            "description": "Search Reddit (zero-login) by keyword and/or within a specific subreddit, returning normalized posts (title, author, score, url, selftext). Use when the user asks what Reddit says about a topic, to search a subreddit, or to read a discussion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords to find on Reddit"},
                    "subreddit": {"type": "string", "description": "Optional subreddit to restrict the search to (with or without 'r/')"},
                    "limit": {"type": "integer", "description": "Max posts to return (default 5, max 25)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from disk",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write/save a file to disk",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write to"},
                    "content": {"type": "string", "description": "File content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "checkpoint",
            "description": "Persistent, append-only execution checkpoint so you NEVER re-do or re-derive work across turns. action='append' adds a timestamped note (confirmed paths, endpoints, findings index, next action) to your per-session checkpoint; action='read' returns everything you already did. Use append after every discovery and read at the start of a new turn — the harness also auto-injects your checkpoint each round.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["append", "read"], "description": "'append' to add a note, 'read' to view your full checkpoint"},
                    "text": {"type": "string", "description": "The note to append (for action=append)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finish_goal",
            "description": "Signal the end of the current continuous goal. Call this ONLY when you are working under an ACTIVE GOAL (the harness injected '=== ACTIVE GOAL ===' into your context). status='done' when the objective is truly achieved (with evidence); status='blocked' when a concrete condition prevents progress (name it in `reason`). The harness owns the state transition — you just report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["done", "blocked"], "description": "'done' = objective achieved; 'blocked' = a concrete condition prevents progress"},
                    "reason": {"type": "string", "description": "The concrete blocking condition (required for status='blocked')"}
                },
                "required": ["status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_goal",
            "description": "Create a persistent, harness-owned continuous goal for this chat (Layer 0 / F0.4). Use when the user asks for a long-running objective — even if they don't say 'goal'. The harness tracks phase (active/paused/blocked/complete) and round count; you work one round at a time and read state back via get_goal. One active goal per user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string", "description": "The concrete completion objective, one or two sentences."},
                    "max_goal_rounds": {"type": "integer", "description": "Optional cap on automatic continuation rounds (default 256)."}
                },
                "required": ["objective"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_goal",
            "description": "Read the current goal for THIS chat (owner-keyed, session-scoped): objective, phase, roundsStarted, revision, blockedReason. Use at the start of a turn to check whether you are still under an active goal.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_goal",
            "description": "Transition the current goal from the agent side. Model authority (DSH rule): ONLY 'complete' (objective truly achieved, with evidence) or 'blocked' (concrete blocking condition in blocked_reason). edit/pause/resume are human-only via the UI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["complete", "blocked"], "description": "'complete' when achieved; 'blocked' when a concrete condition prevents progress."},
                    "blocked_reason": {"type": "string", "description": "The concrete blocking condition (for action='blocked')."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user_question",
            "description": "Ask the user a concise question when you need confirmation, a choice, or missing information before proceeding. Send one or more questions, each with a stable id that is echoed in the answer. Only available in the live root chat turn; the turn pauses until the user answers (other chats keep running).",
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": "Questions to ask the user before continuing.",
                        "items": {
                            "type": "object",
                            "additionalProperties": True,
                            "properties": {
                                "id": {"type": "string", "description": "Stable id for this question; echoed in the answer."},
                                "question": {"type": "string", "description": "The specific question to ask the user."},
                                "header": {"type": "string", "description": "Optional short heading, such as 'Confirm' or 'Choose Mode'."},
                                "options": {
                                    "type": "array",
                                    "description": "Optional choices. If you recommend one, put it first and append '(Recommended)' to that label.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string", "description": "Short user-facing option label."},
                                            "description": {"type": "string", "description": "One sentence explaining the tradeoff or impact."}
                                        }
                                    }
                                },
                                "multi_select": {"type": "boolean", "description": "Whether the user may select more than one option. Defaults to false."}
                            }
                        }
                    }
                },
                "required": ["questions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sinalevi_search",
            "description": "Search Costa Rican legislation (SINALEVI) — laws, decrees, norms. Targeted queries only: by theme (e.g. 'ambiente' lists all environmental norms), free term, or law number. Returns titles + numbers + links + PDF links. Always cite the returned source; never fabricate a norm.",
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "Free-text term to search in the legislation."},
                    "theme": {"type": "string", "description": "Thematic search (SINALEVI temática), e.g. 'ambiente'."},
                    "number": {"type": "string", "description": "Law/decreto number, e.g. '7554'."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "corteidh_search",
            "description": "Search Inter-American Court of Human Rights (Corte IDH) jurisprudence for cases matching a term. Returns case ids, titles, and PDF links.",
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "Search term (country, topic, person)."}
                },
                "required": ["term"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "corteidh_case",
            "description": "Fetch one Corte IDH case's metadata + direct PDF links by case id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "The case id from corteidh_search."}
                },
                "required": ["case_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "legal_fetch_document",
            "description": "Download a legal document PDF (SINALEVI law or Corte IDH case) on demand, extract its text into the local legal corpus, and return the path + a snippet. Use docfind/read_file on the returned path for precise article grep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": ["sinalevi", "corteidh"], "description": "Which corpus the document belongs to."},
                    "pdf_url": {"type": "string", "description": "The pdf_url from a sinalevi_search/corteidh_case hit."},
                    "title": {"type": "string", "description": "Optional document title for the corpus index."}
                },
                "required": ["source", "pdf_url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "document_append",
            "description": "Append findings to a document WITHOUT rewriting what's already there. Append-only: existing content is preserved and the new `content` is added at the end with a separator. Accumulate findings, evidence, and results here as you discover them — never rely on create_document/update_document for accumulation because those replace/rewrite. Resolution: pass `doc_id` (most precise), or omit to target the active document / most recent document, or pass `title` to match by title. If no document exists yet, it creates one titled `title` (or 'Findings').",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Optional document ID to append to (from manage_documents list or a previous document_append result). Omit to use the active document / most recent document."},
                    "title": {"type": "string", "description": "Optional title — used to match an existing document by title, or to name a new document if none exists."},
                    "content": {"type": "string", "description": "The findings to append (appended verbatim, never truncated or rewritten)."}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "subagent",
            "description": "Delegate a self-contained task to a child agent running in its own chat session. mode='spawn' = fresh child with just the task; mode='fork' = child seeded with a copy of THIS conversation (branch to a new chat). Set run_in_background=true to keep working while the child runs (a continuable child you steer with send_message/list_agents/interrupt_agent); default waits for the child's final answer as this tool's result. Max delegation depth is 3.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["spawn", "fork"], "description": "'spawn' = fresh child session; 'fork' = child inherits this conversation's history."},
                    "task": {"type": "string", "description": "Complete self-contained instructions for the child agent."},
                    "run_in_background": {"type": "boolean", "description": "True = start and return a subagent_id immediately; the child runs independently."}
                },
                "required": ["mode", "task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send more work to a background/continuable subagent (id from subagent or list_agents). Appends a user message to the child's chat and starts its next turn if idle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subagent_id": {"type": "string", "description": "The subagent id returned by subagent(..., run_in_background=true)."},
                    "message": {"type": "string", "description": "The message/task to send."}
                },
                "required": ["subagent_id", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "List your subagents (id, session, parent, depth, status, task) — children started with run_in_background=true.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "interrupt_agent",
            "description": "Stop a running subagent's current turn (its partial output is saved).",
            "parameters": {
                "type": "object",
                "properties": {
                    "subagent_id": {"type": "string", "description": "The subagent id to interrupt."}
                },
                "required": ["subagent_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "job_output",
            "description": "Check the status/result of a background subagent (alias over the subagent registry).",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The subagent id (job id) to inspect."}
                },
                "required": ["job_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "job_kill",
            "description": "Stop a background subagent run (alias for interrupt_agent).",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The subagent id (job id) to stop."}
                },
                "required": ["job_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Your visible plan: the checklist the user watches live in the chat's goal/plan panel. Call it at the START of any multi-step task (3+ steps) with every step, then call it AGAIN whenever a step finishes or the plan changes — send the FULL list each time with statuses, so the user sees steps tick off.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "description": "Every step in order. Each item: {\"text\": \"...\", \"status\": \"pending|in_progress|done\"}.",
                              "items": {"type": "object", "properties": {"text": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}}, "required": ["text"]}}
                },
                "required": ["items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docfind",
            "description": "Fetch a primary document (government PDF, site REST API, sitemap) and grep it locally for exact identifiers (cédula, case number, finca, slug). Use when you need a precise, authoritative hit that a name search can't give. Args (JSON): {\"url\": \"...\", \"terms\": [\"...\"], \"context\": 2, \"json\": false} to fetch+search, or {\"file\": \"/abs/path\", \"terms\": [...]} for an already-downloaded file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch (PDF/HTML/JSON). Omit if using `file`."},
                    "file": {"type": "string", "description": "Absolute path to a local file. Omit if using `url`."},
                    "terms": {"type": "array", "items": {"type": "string"}, "description": "Exact identifiers to grep (case-insensitive). E.g. cédula, case number, finca, slug."},
                    "context": {"type": "integer", "description": "Lines of context around each hit (default 2)."},
                    "json": {"type": "boolean", "description": "Treat the fetched body as JSON (e.g. wp-json REST API)."}
                },
                "required": ["terms"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "registros_cr",
            "description": "Costa Rica public-records source catalog: returns how to search each authoritative source and the unique key (cédula, finca, sumaria, cédula jurídica) to avoid homonyms. Args (JSON): {\"source\": \"tse\"} for one source, or {} for all.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Optional source id: tse, gaceta, registro_nacional, poder_judicial, surcos, municipal, sicop, cgr. Omit for all."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": "Create a new document in the editor panel. Use this when the user asks to write, create, build, or generate code, scripts, programs, games, apps, or any substantial content (>15 lines) AND there is no already-open document/email draft that the request refers to. If an email compose draft is open, edit that draft instead of creating another document. NEVER put large code blocks directly in chat — use this tool instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Document title"},
                    "language": {"type": "string", "description": "Programming language or format (e.g. python, javascript, markdown, text)"},
                    "content": {"type": "string", "description": "The document content"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_document",
            "description": "PREFERRED way to change an existing document. Targeted find-and-replace with multiple FIND/REPLACE pairs per call. Use this for any edit smaller than a full rewrite: adding a function, fixing a bug, tweaking a section, renaming things. Do NOT send the whole file back via update_document for small edits — it wastes tokens and is hard to review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "description": "List of find/replace edits (first match only per edit)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {"type": "string", "description": "Exact text to find in the document"},
                                "replace": {"type": "string", "description": "Text to replace it with"}
                            },
                            "required": ["find", "replace"]
                        }
                    }
                },
                "required": ["edits"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_document",
            "description": "Suggest improvements to the active document WITHOUT editing it. Creates inline comment bubbles the user can accept or reject. Use when the user asks for suggestions, review, improvements, or feedback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "suggestions": {
                        "type": "array",
                        "description": "List of suggested changes with reasons",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {"type": "string", "description": "Exact text in the document to suggest changing"},
                                "replace": {"type": "string", "description": "Suggested replacement text"},
                                "reason": {"type": "string", "description": "Brief explanation of why this change helps"}
                            },
                            "required": ["find", "replace", "reason"]
                        }
                    }
                },
                "required": ["suggestions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_document",
            "description": "Replace the ENTIRE active document. ONLY use for genuine full rewrites (>50% of lines changed). For any smaller change, use edit_document — echoing back the whole file for small edits is wasteful.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Complete new document content"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_chats",
            "description": "Search ALL of the user's chats (full transcripts) and get matching passages with chat ids. Syntax: words, \"exact phrase\", -exclude, in:<chat>, role:user|ai, tool:<tool>, after:YYYY-MM-DD, before:YYYY-MM-DD. Paste a URL (e.g. a pull-request link) to find the chats that touched it. Use whenever the user refers to earlier work, a past decision or another chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query in the syntax above"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "factcheck",
            "description": "Fact-check claims and spot what is off. action=claim (default): analyse the exact quote — figures, dates, absolutes, red-flag signals, candidate vices, the primary documents that would prove it, the questions to ask and a plan; give source_url to test whether the claim's figures are actually in the source it cites; run=true also searches primary repositories and prior fact-checks. action=anomalies: give items=[{source,text}] to find figures/dates that disagree across sources (moving numbers, outliers, impossible timelines). action=recalc: claimed, documented, optional rate → gap/ratio/%. action=method: the full method.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["claim", "anomalies", "recalc", "method"]},
                    "claim": {"type": "string", "description": "Exact words of the claim"},
                    "speaker": {"type": "string"}, "date": {"type": "string"},
                    "context": {"type": "string", "description": "Where/when it was said, topic"},
                    "source_url": {"type": "string", "description": "The source the speaker cites (checked for the claim's figures)"},
                    "run": {"type": "boolean", "description": "Also search primary repositories and prior checks"},
                    "items": {"type": "array", "items": {"type": "object", "properties": {"source": {"type": "string"}, "text": {"type": "string"}}}, "description": "anomalies: texts to cross-read"},
                    "claimed": {"type": "number"}, "documented": {"type": "number"}, "rate": {"type": "number"}, "unit": {"type": "string"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recon",
            "description": "OSINT superpowers. Instant reconnaissance using free public sources. action=auto (default) detects the target type and runs every relevant module in parallel. Individual actions: dns, whois, subdomains, http (fingerprint a live site), ip, wayback, email, username (15 platforms), github, reddit, hackernews, phone, geocode (place or 'lat,lon'), exif (image URL or workspace path), reverse_image, dorks (set run=true to execute them), crypto (BTC/ETH address), framework (best tools from the OSINT Framework for a type/category/keyword). Never use it to locate a private person's home, family or whereabouts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["auto", "dns", "whois", "subdomains", "http", "ip", "wayback", "email", "username", "github", "reddit", "hackernews", "phone", "geocode", "exif", "reverse_image", "dorks", "crypto", "framework"]},
                    "target": {"type": "string", "description": "Domain, URL, IP, email, username, phone, wallet, place, coordinates, image URL/path, or a topic for framework"},
                    "run": {"type": "boolean", "description": "dorks only: also execute the top queries"},
                    "purpose": {"type": "string", "description": "One line: why this lookup (kept with the finding)"}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "session_read",
            "description": "Read a dense recap of another chat (its summary + latest exchanges, or only passages matching `query`). Use to pull context from another chat instead of asking the user to re-explain.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Chat id or id prefix (from search_chats / list_sessions)"},
                    "query": {"type": "string", "description": "Optional: only passages mentioning this"}
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "share_finding",
            "description": "Share a key finding, decision or result with the user's OTHER chats via the cross-session board (they receive it automatically in their context). Optionally address it to one chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The finding / decision, self-contained (1-6 sentences)"},
                    "title": {"type": "string", "description": "Short title"},
                    "kind": {"type": "string", "enum": ["finding", "decision", "result", "question"]},
                    "to_session": {"type": "string", "description": "Optional chat id to deliver it to directly"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "publish_live",
            "description": "Publish a document as a live, shareable web page that updates in real time as the document changes. Markdown documents may contain ```mcp-live blocks ({\"tool\": \"server.tool\", \"args\": {...}, \"refresh\": 60}) that fetch live data from MCP servers when the page is opened (needs allow_mcp).",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Document id (defaults to the active / most recent document)"},
                    "allow_mcp": {"type": "boolean", "description": "Let mcp-live blocks fetch data for readers"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chat_with_model",
            "description": "Send a message to another AI model and get its response. Use for getting a second opinion, delegating subtasks, or AI-to-AI communication.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Model name (e.g. 'qwen3-32b') or model@endpoint_name"},
                    "message": {"type": "string", "description": "The message to send to the model"}
                },
                "required": ["model", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_session",
            "description": "Create a new chat for ongoing conversations with a specific model. (The UI calls these 'chats'; 'session' is the internal term.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the new chat"},
                    "model": {"type": "string", "description": "Model name or model@endpoint_name"}
                },
                "required": ["name", "model"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_sessions",
            "description": "List the user's chats (the UI calls them 'chats') as clickable markdown links. Use this to enumerate chats before opening, renaming, archiving, or deleting them. When replying to the user, preserve the returned [title](#session-id) links; do not strip them into plain text. Optionally filter by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "Optional keyword to filter chats by name"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_session",
            "description": "Send a message to an existing chat and get the model's response. The chat keeps its conversation history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The id of the chat to send the message to"},
                    "message": {"type": "string", "description": "The message to send"}
                },
                "required": ["session_id", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pipeline",
            "description": "Run a multi-step AI pipeline where each model's output feeds the next. Example: Draft -> Critique -> Revise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "Pipeline steps in order",
                        "items": {
                            "type": "object",
                            "properties": {
                                "model": {"type": "string", "description": "Model name for this step"},
                                "instruction": {"type": "string", "description": "What this step should do"}
                            },
                            "required": ["model", "instruction"]
                        }
                    }
                },
                "required": ["steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_session",
            "description": "Manage a chat: rename, archive, unarchive, delete, mark important, truncate history, or fork it. (The UI calls these 'chats'; 'session' is the internal term.) For destructive actions like delete, call list_sessions first and pass the exact id returned there; never invent ids.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["rename", "archive", "unarchive", "delete", "important", "unimportant", "truncate", "fork"],
                               "description": "The action to perform"},
                    "session_id": {"type": "string", "description": "Exact target chat id from list_sessions, or 'current' for the active chat where supported"},
                    "value": {"type": "string", "description": "Action parameter: new name (rename), keep_count (truncate/fork)"}
                },
                "required": ["action", "session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_memory",
            "description": "Manage the user's memory system: list, add, edit, delete, or search memories. Memories persist across sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "edit", "delete", "search"],
                               "description": "The action to perform"},
                    "text": {"type": "string", "description": "Memory text (for add/edit) or search query (for search)"},
                    "memory_id": {"type": "string", "description": "Memory ID (for edit/delete)"},
                    "category": {"type": "string", "enum": ["fact", "event", "contact", "preference"],
                                 "description": "Memory category (for add/list filter)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_models",
            "description": "List all available AI models across configured endpoints. Optionally filter by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "Optional keyword to filter models"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui_control",
            "description": "Control the user interface. Actions: toggle (turn tools on/off), open_panel (open a modal: documents/library, gallery, email, sessions, notes, memories/brain, skills, settings, cookbook), open_email_reply (open an email reply draft document; does NOT send), set_mode, switch_model, set_theme (presets: dark, light, midnight, paper, nord, monokai, gruvbox, dracula, cyberpunk, retrowave, forest, ocean, ume, copper, terminal, vaporwave, lavender, gpt, coffee, claude), create_theme (CREATE any custom theme with a name + colors object — pick distinctive, evocative hex colors that match the requested aesthetic, NOT generic defaults. The theme auto-applies after creation). When a user asks for ANY theme not in the preset list, ALWAYS use create_theme.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["toggle", "open_panel", "open_email_reply", "set_mode", "switch_model", "set_theme", "create_theme", "get_toggles"],
                               "description": "The UI action. Use set_theme for presets, create_theme to build a custom theme with any hex colors"},
                    "name": {"type": "string", "description": "For toggle: web, bash, research, incognito, document_editor (aliases: shell, search, deepresearch, documents). For open_panel: documents, gallery, email, sessions, notes, brain/memories, skills, settings, cookbook. For open_email_reply: email UID. For set_theme: a preset theme name. For create_theme: the custom theme name."},
                    "value": {"type": "string", "description": "Value: on/off for toggle, agent/chat for set_mode, model name for switch_model, theme name for set_theme, or folder for open_email_reply"},
                    "uid": {"type": "string", "description": "Email UID for open_email_reply"},
                    "folder": {"type": "string", "description": "Email folder for open_email_reply (default INBOX)"},
                    "mode": {"type": "string", "description": "Reply draft mode for open_email_reply: reply, reply-all, or ai-reply"},
                    "colors": {"type": "object", "description": "For create_theme: the theme colors",
                               "properties": {
                                   "bg": {"type": "string", "description": "Background color (hex, e.g. #1a1a2e)"},
                                   "fg": {"type": "string", "description": "Foreground/text color (hex)"},
                                   "panel": {"type": "string", "description": "Panel/sidebar background color (hex)"},
                                   "border": {"type": "string", "description": "Border/divider color (hex)"},
                                   "accent": {"type": "string", "description": "Accent color for buttons, brand, highlights (hex)"},
                                   "userBubbleBg": {"type": "string", "description": "User chat bubble background (hex, optional)"},
                                   "aiBubbleBg": {"type": "string", "description": "AI chat bubble background (hex, optional)"},
                                   "bubbleBorder": {"type": "string", "description": "Chat bubble border color (hex, optional)"},
                                   "sidebarBg": {"type": "string", "description": "Sidebar background override (hex, optional)"},
                                   "sectionAccent": {"type": "string", "description": "Section header accent color (hex, optional)"},
                                   "brandColor": {"type": "string", "description": "Brand/logo color (hex, optional)"},
                                   "inputBg": {"type": "string", "description": "Chat input background (hex, optional)"},
                                   "inputBorder": {"type": "string", "description": "Chat input border (hex, optional)"},
                                   "sendBtnBg": {"type": "string", "description": "Send button background (hex, optional)"},
                                   "sendBtnHover": {"type": "string", "description": "Send button hover color (hex, optional)"},
                                   "codeBg": {"type": "string", "description": "Code block background (hex, optional)"},
                                   "codeFg": {"type": "string", "description": "Code block text color (hex, optional)"},
                                   "toggleBg": {"type": "string", "description": "Toggle switch off background (hex, optional)"},
                                   "toggleActive": {"type": "string", "description": "Toggle switch on color (hex, optional)"},
                                   "accentPrimary": {"type": "string", "description": "Primary accent override (hex, optional)"},
                                   "accentError": {"type": "string", "description": "Error/danger color (hex, optional)"}
                               },
                               "required": ["bg", "fg", "panel", "border", "accent"]}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_tasks",
            "description": "Manage scheduled/automated tasks: list, create, edit, delete, pause, resume, or run tasks. Use this for ANY recurring/scheduled request ('every morning…', 'each day at 7:30', 'daily summarize…') — create a task rather than doing it once. Task types: llm (AI runs a prompt), research (runs the deep-research pipeline on a question), or action (built-in automation). Triggers can be time-based or event-based.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "edit", "delete", "pause", "resume", "run"],
                               "description": "The action to perform"},
                    "task_id": {"type": "string", "description": "Task ID (for edit/delete/pause/resume/run)"},
                    "name": {"type": "string", "description": "Task name"},
                    "prompt": {"type": "string", "description": "The instruction (for task_type=llm) or the research question (for task_type=research). Required for both."},
                    "task_type": {"type": "string", "enum": ["llm", "research", "action"],
                                  "description": "llm = AI runs your prompt; research = runs the deep-research pipeline on the prompt as a question; action = direct built-in function"},
                    "action_name": {"type": "string", "enum": [
                        "tidy_sessions", "tidy_documents", "consolidate_memory", "tidy_research",
                        "summarize_emails", "draft_email_replies", "extract_email_events",
                        "classify_events", "mark_email_boundaries", "learn_sender_signatures",
                        "test_skills", "audit_skills", "check_email_urgency"
                    ],
                                    "description": "Built-in action (for task_type=action)"},
                    "trigger_type": {"type": "string", "enum": ["schedule", "event"],
                                     "description": "schedule = time-based, event = count-based"},
                    "schedule": {"type": "string", "enum": ["once", "daily", "weekly", "monthly"],
                                 "description": "Schedule frequency (for trigger_type=schedule)"},
                    "scheduled_time": {"type": "string", "description": "HH:MM in UTC (for schedule triggers). Convert the user's stated local time using the UTC offset given in the 'Current date and time' context."},
                    "scheduled_day": {"type": "integer", "description": "Day of week 0=Mon (weekly) or day of month (monthly)"},
                    "trigger_event": {"type": "string", "enum": ["session_created", "message_sent", "document_created", "memory_added", "research_completed", "email_received", "skill_added"],
                                      "description": "Event name (for trigger_type=event)"},
                    "trigger_count": {"type": "integer", "description": "Fire every N events (for trigger_type=event)"},
                    "output_target": {"type": "string", "description": "Where results go. Defaults to 'session' (results land in a dedicated chat session the user reads) — this is the right choice for 'summarize for me' / 'send to me'. Do NOT go hunting for the user's email address; only use an email MCP tool name here if the user explicitly asked to be emailed AND an address is already known."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_calendar",
            "description": "Manage calendar events: list events in a date range, create, update, delete. Each event can carry a tag/category (event_type) and importance level. Use ISO 8601 datetimes; for all-day events set all_day=true and pass YYYY-MM-DD. For event reminders/alarms, pass reminder_minutes; the tool creates the Odysseus note reminder, so do not also call manage_notes for the same reminder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list_events", "create_event", "update_event", "delete_event", "list_calendars"],
                               "description": "Action to perform"},
                    "summary": {"type": "string", "description": "Event title (for create/update)"},
                    "dtstart": {"type": "string", "description": "Start ISO datetime, or YYYY-MM-DD if all_day"},
                    "dtend": {"type": "string", "description": "End ISO datetime; defaults to +1h (or +1 day for all_day)"},
                    "all_day": {"type": "boolean", "description": "Whether this is an all-day event"},
                    "description": {"type": "string", "description": "Event description / notes"},
                    "location": {"type": "string", "description": "Event location"},
                    "uid": {"type": "string", "description": "Event UID (for update/delete)"},
                    "calendar_href": {"type": "string", "description": "Specific calendar URL (optional; defaults to first calendar)"},
                    "calendar": {"type": "string", "description": "Filter list_events by calendar name or href"},
                    "start": {"type": "string", "description": "list_events range start (ISO datetime); defaults to today"},
                    "end": {"type": "string", "description": "list_events range end (ISO datetime); defaults to +14 days"},
                    "event_type": {"type": "string", "description": "Tag / category for the event. Common values: work, personal, health, travel, meal, social, admin, other. Aliases accepted: tag, category, type."},
                    "importance": {"type": "string", "enum": ["low", "normal", "high", "critical"], "description": "Priority level (defaults to 'normal')"},
                    "reminder_minutes": {"type": "integer", "description": "For create_event: create an Odysseus reminder this many minutes before the event, e.g. 5 for 'reminder 5 min before'."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_notes",
            "description": "Manage notes and checklists (Google Keep-style): list, add, update, delete, toggle_item. IMPORTANT: For to-do lists / checklists, set note_type='checklist' and pass the items as the `checklist_items` array — do NOT serialize them into `content` as plain text. For freeform notes, use note_type='note' and put the body in `content`. `due_date` accepts natural language like 'tomorrow at 9am' (parsed in the user's timezone) and fires a notification — do not also create a calendar event for the same reminder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list", "add", "update", "delete", "toggle_item"],
                               "description": "The action to perform"},
                    "id": {"type": "string", "description": "Note id (for update/delete/toggle_item); 8-char prefix is fine"},
                    "title": {"type": "string", "description": "Note title (for add/update)"},
                    "content": {"type": "string", "description": "Freeform body text. Use this for note_type='note'. Do NOT use this for checklists — pass `checklist_items` instead."},
                    "note_type": {"type": "string", "enum": ["note", "checklist"],
                                  "description": "'note' = freeform text in `content`. 'checklist' = structured to-do items in `checklist_items`. Defaults to 'checklist' if checklist_items is supplied, else 'note'."},
                    "checklist_items": {"type": "array",
                                        "items": {"type": "object",
                                                  "properties": {
                                                      "text": {"type": "string", "description": "The to-do item text"},
                                                      "done": {"type": "boolean", "description": "Whether the item is checked off"}
                                                  },
                                                  "required": ["text"]},
                                        "description": "Checklist items for note_type='checklist'. Each item is {text, done}. REQUIRED for checklists — leaving this empty produces a blank note."},
                    "color": {"type": "string", "description": "Optional color label (e.g. 'yellow', 'blue', 'green')"},
                    "label": {"type": "string", "description": "Optional category label (also used as a list filter)"},
                    "pinned": {"type": "boolean", "description": "Pin the note to the top"},
                    "archived": {"type": "boolean", "description": "For update: archive/unarchive. For list: show archived notes when true."},
                    "due_date": {"type": "string", "description": "Reminder time. Accepts natural language ('tomorrow at 9am', '11pm today') or ISO 8601. Fires a notification at that time."},
                    "index": {"type": "integer", "description": "Checklist item index (for toggle_item, 0-based)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "api_call",
            "description": "Call a registered API integration (RSS reader, git forge, bookmark manager, smart home, etc.). Check the system context for available integrations and their endpoints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "integration": {"type": "string", "description": "Integration name or ID (e.g. 'Miniflux', 'Gitea')"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method"},
                    "path": {"type": "string", "description": "API endpoint path (e.g. '/v1/entries?status=unread&limit=20')"},
                    "body": {"type": "object", "description": "JSON request body (for POST/PUT/PATCH)"}
                },
                "required": ["integration", "method", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_teacher",
            "description": "Ask a more capable AI model for help when stuck on a difficult problem. The teacher provides guidance that can be saved as a learned skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Teacher model name (e.g. 'claude-sonnet-4') or 'auto' for configured default"},
                    "problem": {"type": "string", "description": "Describe the problem or question you need help with"}
                },
                "required": ["problem"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_skills",
            "description": (
                "Read or modify the user's skill library. Skills are SKILL.md files "
                "(YAML frontmatter + structured body: When to Use / Procedure / "
                "Pitfalls / Verification) and follow a draft → published lifecycle. "
                "Use progressive disclosure: 'list' to see what exists, 'view' to "
                "load full content for a single skill, 'view_ref' for sub-files. "
                "Use 'patch' for surgical text edits and 'edit' for full rewrites. "
                "'publish' once you've verified the procedure works. For add, "
                "always provide an explicit name slug and only tell the user the "
                "exact name returned by the tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "view", "view_ref", "add", "edit", "patch", "publish", "delete", "search"], "description": "list = name+description summary; view = full SKILL.md; view_ref = sub-file under the skill dir; add = create; edit = full rewrite (content); patch = old_string→new_string; publish = flip status; delete; search = relevance match on published skills."},
                    "name": {"type": "string", "description": "Slug/name of the skill. Required for add/view/view_ref/edit/patch/publish/delete. For add, choose the exact kebab-case name the user should see and report only the returned name."},
                    "path": {"type": "string", "description": "Sub-path under the skill directory for view_ref (e.g. 'references/example.md')."},
                    "description": {"type": "string", "description": "One-line summary surfaced in the skills index (for add)."},
                    "category": {"type": "string", "description": "Organizational grouping like 'dev', 'email', 'system' (for add)."},
                    "when_to_use": {"type": "string", "description": "Trigger conditions in plain English (for add)."},
                    "procedure": {"type": "array", "items": {"type": "string"}, "description": "Numbered steps (for add)."},
                    "pitfalls": {"type": "array", "items": {"type": "string"}, "description": "Known failure modes + recovery (for add)."},
                    "verification": {"type": "array", "items": {"type": "string"}, "description": "How to confirm the procedure succeeded (for add)."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Keyword tags (for add)."},
                    "platforms": {"type": "array", "items": {"type": "string"}, "description": "Restrict to OSes (for add)."},
                    "requires_toolsets": {"type": "array", "items": {"type": "string"}, "description": "Hide unless these toolsets are active (for add)."},
                    "fallback_for_toolsets": {"type": "array", "items": {"type": "string"}, "description": "Hide when these toolsets are active (for add)."},
                    "status": {"type": "string", "enum": ["draft", "published"], "description": "Defaults to 'draft' on add."},
                    "version": {"type": "string", "description": "Semver-ish, e.g. '1.0.0' (for add)."},
                    "confidence": {"type": "number", "description": "0-1 (for add/publish)."},
                    "content": {"type": "string", "description": "Full SKILL.md text (for edit)."},
                    "old_string": {"type": "string", "description": "Exact substring to replace (for patch). Must appear exactly once."},
                    "new_string": {"type": "string", "description": "Replacement text (for patch)."},
                    "query": {"type": "string", "description": "Search query (for search)."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_endpoints",
            "description": "Manage model API endpoints: list configured endpoints, add new ones, delete, enable or disable them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable"]},
                    "endpoint_id": {"type": "string", "description": "Endpoint ID (for delete/enable/disable)"},
                    "name": {"type": "string", "description": "Display name (for add)"},
                    "base_url": {"type": "string", "description": "API base URL e.g. https://api.openai.com/v1 (for add)"},
                    "api_key": {"type": "string", "description": "API key (for add)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_mcp",
            "description": "Manage MCP (Model Context Protocol) tool servers: list servers and their tools, add new servers, delete, enable/disable, reconnect, or list all available tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable", "reconnect", "list_tools"]},
                    "server_id": {"type": "string", "description": "Server ID (for delete/enable/disable/reconnect)"},
                    "name": {"type": "string", "description": "Server name (for add)"},
                    "command": {"type": "string", "description": "Command to run e.g. npx (for add)"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Command arguments (for add)"},
                    "env": {"type": "object", "description": "Environment variables (for add)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_webhooks",
            "description": "Manage webhooks: list, add, delete, enable or disable webhook endpoints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable"]},
                    "webhook_id": {"type": "string", "description": "Webhook ID (for delete/enable/disable)"},
                    "name": {"type": "string", "description": "Webhook name (for add)"},
                    "url": {"type": "string", "description": "Webhook URL (for add)"},
                    "events": {"type": "string", "description": "Comma-separated event names (for add)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_tokens",
            "description": "Manage API access tokens: list existing tokens, create new ones, or delete them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "delete"]},
                    "token_id": {"type": "string", "description": "Token ID (for delete)"},
                    "name": {"type": "string", "description": "Token name (for create)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_documents",
            "description": "Manage documents: list all documents (with optional search/language filter), delete documents, or run tidy cleanup.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "delete", "tidy"]},
                    "document_id": {"type": "string", "description": "Document ID (for delete)"},
                    "search": {"type": "string", "description": "Search query (for list)"},
                    "language": {"type": "string", "description": "Filter by language (for list)"},
                    "limit": {"type": "integer", "description": "Max results (for list, default 50)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_settings",
            "description": "Manage user preferences and settings. Use `disable_tool`/`enable_tool`/`list_tools` to turn individual tools on or off globally (e.g. shell, search, browser, documents, memory, skills, images, tasks, notes, calendar, email). Use list/get/set/delete for free-form preferences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "get", "set", "delete", "disable_tool", "enable_tool", "list_tools"]},
                    "key": {"type": "string", "description": "Setting key (for get/set/delete)"},
                    "value": {"description": "Setting value (for set) — can be string, number, boolean, or object"},
                    "tool": {"type": "string", "description": "Tool name to disable/enable (for disable_tool/enable_tool). Accepts aliases: shell, search, browser, documents, memory, skills, images, tasks, notes, calendar, email — or a raw tool name like 'bash' or 'web_search'."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_model",
            "description": "Download a HuggingFace model to a server. If `host` is omitted, defaults to the cookbook's currently-selected server (NOT localhost) — call list_cookbook_servers first if you're unsure where it should go.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "HuggingFace repo (e.g. 'Qwen/Qwen3-8B')"},
                    "host": {"type": "string", "description": "Target server — use the friendly NAME from list_cookbook_servers (e.g. 'gpu-box', 'workstation') or a raw user@host. Omit to use the cookbook's selected default server."},
                    "local": {"type": "boolean", "description": "Force download to THIS machine (localhost) instead of the default remote server."},
                    "include": {"type": "string", "description": "Glob filter for specific files (e.g. '*Q4_K_M*')"},
                },
                "required": ["repo_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "serve_model",
            "description": "Start serving a model with vLLM, SGLang, llama.cpp, Ollama, or Diffusers. If `host` is omitted, defaults to the cookbook's selected server (not localhost). For image/inpainting/diffusion models use the built-in command `python3 scripts/diffusion_server.py --model <repo> --port 8100` rather than inventing a custom diffusers API server. After launching, call list_served_models to check readiness/errors; if it reports a diagnosis with retry suggestions, retry via serve_model using the suggested adjusted cmd.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Model repo (e.g. 'Qwen/Qwen3-8B')"},
                    "cmd": {"type": "string", "description": "Full serve command (e.g. 'vllm serve Qwen/Qwen3-8B --port 8000 --tp 2', 'python3 -m sglang.launch_server --model-path Qwen/Qwen3-8B --port 30000', or for inpainting/image models: 'python3 scripts/diffusion_server.py --model diffusers/stable-diffusion-xl-1.0-inpainting-0.1 --port 8100')"},
                    "host": {"type": "string", "description": "Target server — friendly NAME from list_cookbook_servers (e.g. 'gpu-box', 'workstation') or raw user@host. Omit to use the cookbook's selected default."},
                    "local": {"type": "boolean", "description": "Force serve on THIS machine instead of the default remote server."},
                },
                "required": ["repo_id", "cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_served_models",
            "description": "List currently running model servers with status, model name, port, throughput, and structured Cookbook diagnoses. If a serve failed, this includes recent logs plus retry suggestions/adjusted commands the agent can use with serve_model.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_served_model",
            "description": "Stop a running model server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session ID of the server to stop"},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_downloads",
            "description": "List in-progress model downloads in the Cookbook. Shows each download's model name, phase, percent (if available), session ID, and remote host.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_download",
            "description": "Cancel an in-progress model download by killing its tmux session. Use list_downloads first to get the session_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session ID from list_downloads (e.g. 'cookbook-a1b2c3d4')"},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_hf_models",
            "description": "Search HuggingFace for models matching a query. Returns a ranked list of repo IDs, sizes (when available), and download counts. Use this when the user wants to find a model to download.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms (e.g. 'Qwen 8B', 'flux', 'llama-3 instruct')"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_cookbook_servers",
            "description": "List the cookbook's configured servers (remote GPU boxes + local) and the current default host. Call this before download_model/serve_model when the user didn't specify a host, so models go to the right machine (where the GPUs and model cache are) instead of localhost. If multiple servers and intent is ambiguous, show them and ask the user which.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_serve_presets",
            "description": "List saved Cookbook serve presets. Each preset is a launch template (name, model, host, port, tmux cmd) the user previously saved from the UI. Call this BEFORE serve_model when the user asks to launch a model by name — there's almost always a working preset for it.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adopt_served_model",
            "description": "Register an existing tmux model server (started manually or outside the cookbook flow) into Cookbook tracking, AND add it as a chat endpoint. Use when the user (or you) launched something via ssh+tmux and now want it visible in the UI / stoppable via stop_served_model / usable in the model picker. Verifies the tmux session + port respond before adding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Remote host in user@host form (e.g. 'user@192.0.2.10'). Omit for localhost."},
                    "tmux_session": {"type": "string", "description": "Existing tmux session name (e.g. 'minimax-m27')"},
                    "model": {"type": "string", "description": "Model repo_id or display name (e.g. 'cyankiwi/MiniMax-M2.7-AWQ-4bit')"},
                    "port": {"type": "integer", "description": "Port the server is listening on (default 8000)"},
                    "name": {"type": "string", "description": "Optional display name (defaults to model basename)"},
                    "add_endpoint": {"type": "boolean", "description": "Also register as a chat endpoint (default true)"}
                },
                "required": ["tmux_session", "model"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "serve_preset",
            "description": "Launch a saved Cookbook serve preset by name. Reuses the exact tmux command + host the user saved before. This is the preferred way to start a known model (SD3.5, vLLM presets, etc.) — don't fabricate launch commands when a preset exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Preset name (exact or case-insensitive substring of one returned by list_serve_presets)"},
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_cached_models",
            "description": "List models already cached on disk locally or on a remote server. `host` accepts friendly Cookbook server names from list_cookbook_servers (for example ajax) or raw user@host. Also reports completed Cookbook download tasks when the filesystem cache scan cannot locate the HF cache path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Friendly Cookbook server name (e.g. 'ajax', 'gpu-box') or raw remote host (e.g. 'user@gpu-box'). Omit for local."},
                    "model_dir": {"type": "string", "description": "Comma-separated additional model directories to scan beyond ~/.cache/huggingface/hub"},
                    "ssh_port": {"type": "string", "description": "SSH port for remote host (default 22)"},
                    "platform": {"type": "string", "enum": ["linux", "windows"], "description": "Remote platform"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "app_api",
            "description": "Generic loopback to ANY internal Odysseus endpoint. Use this when there's no named tool for what the user wants. Hits the same routes the UI buttons hit (cookbook, gallery, library/documents, memory, notes, calendar, tasks, settings, themes, research, compare, etc.). action='endpoints' returns the OpenAPI surface (use `filter` to narrow). action='call' (default) takes method+path+body. Auth/user/admin paths are blocked for safety. Do not use for email account discovery; use list_email_accounts instead because /api/email/accounts is owner-filtered in tool context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["call", "endpoints"], "description": "'call' to hit an endpoint, 'endpoints' to list what's available"},
                    "path": {"type": "string", "description": "Endpoint path starting with /api/ (e.g. '/api/cookbook/gpus', '/api/gallery/list', '/api/calendar/events')"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method (default GET)"},
                    "body": {"type": "object", "description": "JSON request body for POST/PUT/PATCH"},
                    "query": {"type": "object", "description": "Querystring params as a key-value object"},
                    "filter": {"type": "string", "description": "For action=endpoints: substring to filter paths/summaries (e.g. 'cookbook', 'gallery')"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_image",
            "description": "Edit a gallery image: upscale, remove background, inpaint, or harmonize.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "string", "description": "Gallery image ID"},
                    "action": {"type": "string", "enum": ["upscale", "rembg", "inpaint", "harmonize"], "description": "Edit action"},
                    "prompt": {"type": "string", "description": "For inpaint: what to fill the masked area with"},
                    "scale": {"type": "number", "description": "For upscale: scale factor (default 2)"},
                },
                "required": ["image_id", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_research",
            "description": "Start a deep research task on a topic. Returns a task ID for tracking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Research question or topic"},
                },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_contact",
            "description": "Look up a contact's email address by name. Searches CardDAV address book and sent email history. Use when the user says 'message [name]' or 'email [name]' without an email address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Person's name to search for"},
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sherlock_osint",
            "description": "Enumerate a username/handle across hundreds of sites using Sherlock to find where it exists (OSINT). Use when the user asks to find someone's accounts or aliases by username. Requires the sherlock CLI (pip install sherlock).",
            "parameters": {
                "type": "object",
                "properties": {
                    "usernames": {"type": "array", "items": {"type": "string"}, "description": "Usernames/handles to search, e.g. ['alice', 'alice_1987']"},
                },
                "required": ["usernames"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "osint_framework",
            "description": "Navigate the OSINT Framework tree (a large catalog of OSINT tools organized as folders) ONE LEVEL at a time. Pass a `path` (list of folder names from the root) to see the options at that level; descend recursively by appending a chosen option's name to the path until you reach a url leaf, which returns the full tool details (url, description, input, output, opsec). Use when the user wants to browse or find OSINT tools for a specific purpose (e.g. 'what tools exist for username search'). Start with an empty path [] to see the top level.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "array", "items": {"type": "string"}, "description": "List of folder names from the root to the current level, e.g. ['Username', 'Username Search Engines']. Empty [] = root level."},
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "case_file",
            "description": "Case Files: the organized intel store for investigations, OSINT and research threads. Open one case per thread, then record entities (auto-typed and deduped: person, org, username, email, phone, domain, url, ip, location, crypto…), sourced findings (selectors in the text are auto-extracted into entities), relationships, aliases and next steps. op=pivots returns concrete next moves for what is known so far (queue=true adds them as steps). op=export returns a markdown dossier. op=search finds a selector across ALL cases. Record facts as you discover them — do not keep them only in chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["open", "list", "get", "update", "add_entity", "add_finding", "link", "alias", "add_step", "done_step", "pivots", "search", "export", "delete"]},
                    "case": {"type": "string", "description": "Case id, id prefix, or title (required for all ops except open/list/search)."},
                    "title": {"type": "string", "description": "open/update: case title."},
                    "objective": {"type": "string", "description": "open/update: what this case is trying to establish."},
                    "status": {"type": "string", "enum": ["open", "paused", "closed"], "description": "update: case status; list: filter (default open)."},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "value": {"type": "string", "description": "add_entity: the selector/name, e.g. 'jdoe@example.com'."},
                    "type": {"type": "string", "description": "add_entity: entity type (auto-detected when omitted)."},
                    "label": {"type": "string", "description": "add_entity: human label, e.g. 'personal Gmail'."},
                    "text": {"type": "string", "description": "add_finding/add_step: the fact or step text."},
                    "source_url": {"type": "string", "description": "add_finding: where the fact came from."},
                    "source": {"type": "string", "description": "add_entity: where the entity was seen."},
                    "confidence": {"type": "number", "description": "0..1 confidence (findings default 0.6)."},
                    "entities": {"type": "array", "items": {"type": "string"}, "description": "add_finding: extra entity values the finding is about."},
                    "a": {"type": "string", "description": "link: entity id or value."},
                    "b": {"type": "string", "description": "link: entity id or value."},
                    "relation": {"type": "string", "description": "link: e.g. owns, alias_of, works_at, registered_by, located_in."},
                    "evidence": {"type": "string", "description": "link: supporting evidence / URL."},
                    "entity": {"type": "string", "description": "alias: entity id or value."},
                    "alias": {"type": "string", "description": "alias: alternative value for the entity."},
                    "step": {"type": "string", "description": "done_step: step id or text."},
                    "result": {"type": "string", "description": "done_step: what the step produced."},
                    "queue": {"type": "boolean", "description": "pivots: also add the suggestions as open steps."},
                    "query": {"type": "string", "description": "search: text or selector to find across cases."}
                },
                "required": ["op"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "watch_target",
            "description": "Watchlist: proactively monitor a web page or RSS/Atom/JSON feed for changes. op=add starts watching a URL on an interval (min 15 min, default 360); feeds are auto-detected and alert on new items (title + link); keywords make it alert only when one appears in newly added text; case links changes into a Case File. A scheduled task re-checks due watches and notifies the user. op=check checks one immediately; list/pause/resume/remove manage watches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "list", "check", "pause", "resume", "remove"]},
                    "url": {"type": "string", "description": "add: page to watch; other ops: may identify the watch."},
                    "id": {"type": "string", "description": "Watch id (from list)."},
                    "label": {"type": "string"},
                    "interval_minutes": {"type": "integer"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "kind": {"type": "string", "enum": ["auto", "page", "feed"], "description": "auto (default) detects feeds; feed requires one; page forces text diff."},
                    "case": {"type": "string", "description": "Case id/title to file changes into."}
                },
                "required": ["op"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_contact",
            "description": "Create, update, delete, or list the user's CardDAV contacts. Use to save a new contact ('save Jonathan's email jon@x.com'), update an existing one ('change Maria's number'), or remove one. For update/delete you need the contact's uid — call action='list' first to find it. Writes go through the same dedupe + validation as the Contacts UI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "update", "delete"],
                               "description": "list = show all contacts (with uids); add = create; update = edit by uid; delete = remove by uid."},
                    "uid": {"type": "string", "description": "Contact UID (required for update/delete; get it from action=list)."},
                    "name": {"type": "string", "description": "Contact's display name (for add/update)."},
                    "email": {"type": "string", "description": "Single email address (convenience for add, or the primary email for update)."},
                    "emails": {"type": "array", "items": {"type": "string"}, "description": "Full list of email addresses (for update; first is primary)."},
                    "phones": {"type": "array", "items": {"type": "string"}, "description": "Full list of phone numbers (for update)."},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_email_accounts",
            "description": "List configured email accounts. Use this before checking mail when the user names a mailbox/account such as Gmail, work, or a custom domain, then pass the returned account name/email/id to the other email tools.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send a new email. Use resolve_contact first if you only have a name and need to find the email address. If multiple accounts exist, pass account from list_email_accounts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Email body text"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_emails",
            "description": "List emails from an account/folder, newest first. Returns subject, sender, date, UID, and account for each email. Use list_email_accounts first when the user mentions Gmail/work/a custom mailbox. For last/latest/newest email requests, use max_results=1 and unread_only=false.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "max_results": {"type": "integer", "description": "Max emails to return (default: 20)"},
                    "limit": {"type": "integer", "description": "Backward-compatible alias for max_results"},
                    "unread_only": {"type": "boolean", "description": "Only show unread emails. Default false; set true only when the user asks for unread emails."},
                    "unresponded_only": {"type": "boolean", "description": "Only show unanswered emails. Default false."},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": "Read the full content of a specific email by UID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID to read"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_email",
            "description": "SEND a reply email immediately by UID. Do not use this when the user asks to open/start a reply window or draft; use ui_control action=open_email_reply instead. For follow-up 'reply ...' requests where the user clearly wants to send now, use the exact UID from the latest read_email/list_emails result; never invent UID 1. Automatically threads with In-Reply-To/References headers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact UID of the email to reply to from list_emails/read_email; never invent UID 1"},
                    "body": {"type": "string", "description": "Reply body text"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bulk_email",
            "description": "Perform one action on many emails at once. Use this for 'delete all those', 'archive these', 'mark all read', or any bulk operation after list_emails. Always pass account when the listed emails came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["mark_read", "mark_unread", "archive", "delete", "junk"], "description": "Bulk action to perform"},
                    "uids": {"type": "array", "items": {"type": "string"}, "description": "UIDs from the latest list_emails result"},
                    "all_unread": {"type": "boolean", "description": "Operate on all unread messages in folder instead of explicit UIDs"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "permanent": {"type": "boolean", "description": "For delete: hard-delete instead of moving to Trash"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_email",
            "description": "Delete one email by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "permanent": {"type": "boolean", "description": "Hard-delete instead of moving to Trash"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "archive_email",
            "description": "Archive one email by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_email_read",
            "description": "Mark one email as read or unread by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "read": {"type": "boolean", "description": "True marks read; false marks unread"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_javascript",
            "description": "Run JavaScript in the user's own browser (the open Odysseus page) and get the stringified result back. Use for anything only the browser can do: read the current page / URL / time, control the UI, open a link. The code runs as a function body; use `return <expr>;` to return a value. To ask Google AI mode or YouTube 'Ask' (when the Odysseus Browser Bridge extension is installed), prefix the code with the marker comment `/*@external*/` and call `return await window.__odyBridge.askAI(\"your question\");` — the extension runs it in the active Google/YouTube tab and returns the answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "JavaScript to execute in the page (as a function body; optional `return` expression)"}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "google_ai_search",
            "description": "Ask Google AI Mode (the user's own logged-in browser session, via the Odysseus Browser Bridge extension) a research question and get its answer back. This is a FIRST-CLASS search source — prefer it for open-ended research, 'look into X', 'find PDFs about Y', and any question where Google's AI overview is more useful than a keyword search. The extension opens (or reuses) a Google tab, submits the question, extracts the answer, and returns it. The prompt is automatically made contextually heavy: it requires concrete PDF titles + URLs in the answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The research question to ask Google AI Mode"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_pdf",
            "description": "Download a PDF from a URL into the current chat's session folder (data/documents/<session_id>/). Use this to save PDFs that Google AI Mode (or any search) referenced, so they become part of the research corpus. Returns the absolute path of the saved file. Accepts a URL or a JSON object {\"url\": \"...\", \"title\": \"optional filename\"}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The PDF URL to download (http/https)"},
                    "title": {"type": "string", "description": "Optional filename stem (defaults to the URL basename)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scrapebadger",
            "description": "Scrape any website or pull structured JSON data from a platform via the ScrapeBadger API (anti-bot bypass, JS rendering, AI extraction, plus dedicated scrapers). This is the FIRST tool to reach for on any 'scrape X', 'search <platform> for Y', 'get <platform> profile Z', or structured-data request. Args (JSON): {\"source\": \"<platform>\", \"endpoint\": \"<action>\", \"params\": {...}}. Sources and their endpoints: web (scrape, detect), google (search, ai_mode, images, videos, autocomplete, lens, maps, maps_place, maps_reviews, maps_photos, maps_posts, shopping, shopping_product, shopping_click, news, news_topics, news_trending, scholar, patents, patents_detail, hotels, hotels_details, trends, trends_trending, trends_autocomplete, jobs, finance, products), reddit (search_posts, search_subreddits, search_users, domain_posts, trending, post, post_comments, post_duplicates, subreddit, subreddit_posts, subreddit_rules, subreddit_wiki, subreddit_wiki_page, subreddits_popular, subreddits_new, user, user_posts, user_comments, user_moderated, user_trophies), youtube (search, autocomplete, trending, trending_shorts, hashtag, home, music_search, video, videos_batch, related, video_comments, comment_replies, transcript, captions, streams, live_chat, short, shorts_by_sound, channel, channel_videos, channel_shorts, channel_streams, channel_playlists, channel_community, channel_about, channel_subscribers, channel_search, channel_resolve, playlist, playlist_items, mix, post, post_comments, oembed, categories, languages, regions, markets), tiktok (user, user_videos, user_reposts, video, video_comments, related, transcript, comment_replies, oembed, hashtag, hashtag_videos, music, music_videos, search, search_users, search_videos, search_hashtags, trending, trending_hashtags, trending_songs, shop_bestsellers, shop_categories, shop_category, shop_search, shop_mall, shop_store, shop_product, shop_reviews, ads_search, ads_advertisers, ad, regions), linkedin (jobs_search, job, company, company_jobs, school, profile, post, article, course, geo_suggest), instagram (health, user, user_posts, user_reels, user_tagged, user_followers, user_following, user_stories, user_highlights, media, media_comments, search_hashtags), facebook (marketplace, marketplace_category, marketplace_item, marketplace_locations, marketplace_categories, search, search_tab, page, page_posts, profile, profile_posts, group, group_posts, post, post_comments, ads, ad, ads_pages_search, ads_page_info), twitter (tweet, tweet_replies, tweet_retweets, tweet_likes, tweet_edit_history, tweet_community_notes, search, advanced_search, user, user_tweets, user_replies, user_media, user_likes, user_followers, user_following, user_mentions, user_articles, users_batch, list, list_tweets, list_search, community, community_members, community_tweets, trends, trends_place, geo_search, geo_place, space, space_search), airbnb (search, listing, listing_calendar, listing_reviews, experiences, experience, health). Path params (e.g. subreddit, video_id, username, room_id, post_id) go inside `params` and are interpolated into the URL; everything else is sent as query/body. If you pass an unknown source/endpoint, the tool returns the full list of available endpoints for that source.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Platform key: web, google, reddit, youtube, tiktok, linkedin, instagram, facebook, twitter, or airbnb"},
                    "endpoint": {"type": "string", "description": "Action within the source (e.g. search, user, video, ai_mode, transcript, marketplace)"},
                    "params": {"type": "object", "description": "Query/body/path params as a JSON object (e.g. {\"q\": \"...\", \"subreddit\": \"python\", \"video_id\": \"...\"})"}
                },
                "required": ["source", "endpoint"]
            }
        }
    },
]


# ---------------------------------------------------------------------------
# Converter: native function call -> ToolBlock
# ---------------------------------------------------------------------------

def function_call_to_tool_block(name: str, arguments: str) -> Optional[ToolBlock]:
    """Convert a native function call into a ToolBlock for the existing execution pipeline."""
    try:
        if not arguments or (isinstance(arguments, str) and not arguments.strip()):
            args = {}
        else:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (json.JSONDecodeError, TypeError):
        logger.error(f"Failed to parse function call arguments for {name}: {arguments}")
        return None

    tool_type = _TOOL_NAME_MAP.get(name, name)

    # Allow MCP tools through (namespaced as mcp__serverid__toolname)
    if tool_type.startswith("mcp__"):
        content = json.dumps(args) if args else "{}"
        return ToolBlock(tool_type, content)
    # Email tools are implemented as MCP — route them to email
    _BUILTIN_EMAIL_TOOLS = {"list_email_accounts", "send_email", "list_emails", "read_email", "reply_to_email",
                            "archive_email", "delete_email", "mark_email_read", "bulk_email", "download_attachment"}
    if name in _BUILTIN_EMAIL_TOOLS:
        return ToolBlock(f"mcp__email__{name}", json.dumps(args) if args else "{}")
    if tool_type not in TOOL_TAGS:
        logger.warning(f"Unknown function call: {name}")
        return None

    # Convert structured args back to the text format each tool expects
    if tool_type == "bash":
        content = args.get("command", "")
    elif tool_type == "python":
        content = args.get("code", "")
    elif tool_type == "web_search":
        queries = args.get("queries")
        if isinstance(queries, list) and queries:
            content = str(queries[0])
        elif queries:
            content = str(queries)
        else:
            content = args.get("query", "")
    elif tool_type == "universal_search":
        content = args.get("query", "")
    elif tool_type == "read_file":
        content = args.get("path", "")
    elif tool_type == "write_file":
        content = args.get("path", "") + "\n" + args.get("content", "")
    elif tool_type == "create_document":
        parts = [args.get("title", "Untitled")]
        if args.get("language"):
            parts.append(args["language"])
        parts.append(args.get("content", ""))
        content = "\n".join(parts)
    elif tool_type == "edit_document":
        blocks = []
        for edit in args.get("edits", []):
            blocks.append(
                f'<<<FIND>>>\n{edit.get("find", "")}\n<<<REPLACE>>>\n{edit.get("replace", "")}\n<<<END>>>'
            )
        content = "\n".join(blocks)
    elif tool_type == "suggest_document":
        blocks = []
        for s in args.get("suggestions", []):
            blocks.append(
                f'<<<FIND>>>\n{s.get("find", "")}\n<<<SUGGEST>>>\n{s.get("replace", "")}\n<<<REASON>>>\n{s.get("reason", "")}\n<<<END>>>'
            )
        content = "\n".join(blocks)
    elif tool_type == "update_document":
        content = args.get("content", "")
    elif tool_type == "search_chats":
        content = args.get("query", "")
    elif tool_type == "chat_with_model":
        content = args.get("model", "") + "\n" + args.get("message", "")
    elif tool_type == "create_session":
        content = args.get("name", "Untitled") + "\n" + args.get("model", "")
    elif tool_type == "list_sessions":
        content = args.get("filter", "")
    elif tool_type == "send_to_session":
        content = args.get("session_id", "") + "\n" + args.get("message", "")
    elif tool_type == "pipeline":
        # Pass as JSON for the pipeline parser
        content = json.dumps({"steps": args.get("steps", [])})
    elif tool_type == "manage_session":
        action = args.get("action", "")
        value = args.get("value", "")
        # `list` is the only action that takes an OPTIONAL keyword
        # filter — never a session_id. Don't leak the "current" default
        # into the filter slot (was producing "No sessions found
        # matching 'current'" when the agent omitted session_id).
        if action == "list":
            keyword = args.get("session_id", "") or args.get("keyword", "") or value
            content = "list" + (("\n" + keyword) if keyword and keyword.lower() != "current" else "")
        else:
            sid = args.get("session_id", "current")
            content = action + "\n" + sid
            if value:
                content += "\n" + value
    elif tool_type == "manage_memory":
        action = args.get("action", "")
        if action == "add":
            content = "add\n" + args.get("text", "")
            if args.get("category"):
                content += "\n" + args["category"]
        elif action == "edit":
            content = "edit\n" + args.get("memory_id", "") + "\n" + args.get("text", "")
        elif action == "delete":
            content = "delete\n" + args.get("memory_id", "")
        elif action == "search":
            content = "search\n" + args.get("text", "")
        elif action == "list":
            content = "list"
            if args.get("category"):
                content += "\n" + args["category"]
        else:
            content = action
    elif tool_type == "list_models":
        content = args.get("filter", "")
    elif tool_type == "ui_control":
        action = args.get("action", "")
        name = args.get("name", "")
        value = args.get("value", "")
        if action == "toggle":
            content = f"toggle {name} {value}"
        elif action == "open_panel":
            content = f"open_panel {name or value}"
        elif action == "open_email_reply":
            uid = args.get("uid") or name
            folder = args.get("folder") or value or "INBOX"
            mode = args.get("mode") or "reply"
            content = f"open_email_reply {uid} {folder} {mode}"
        elif action == "set_mode":
            content = f"set_mode {value or name}"
        elif action == "switch_model":
            content = f"switch_model {value or name}"
        elif action == "set_theme":
            content = f"set_theme {value or name}"
        elif action == "create_theme":
            colors = args.get("colors", {})
            theme_name = name or value or "custom"
            bg = colors.get("bg", "#282c34")
            fg = colors.get("fg", "#9cdef2")
            panel = colors.get("panel", "#111111")
            border = colors.get("border", "#355a66")
            accent = colors.get("accent", "#e06c75")
            content = f"create_theme {theme_name} {bg} {fg} {panel} {border} {accent}"
            # Append advanced overrides as key=value
            adv_keys = [
                "userBubbleBg", "aiBubbleBg", "bubbleBorder", "sidebarBg",
                "sectionAccent", "brandColor", "inputBg", "inputBorder",
                "sendBtnBg", "sendBtnHover", "codeBg", "codeFg",
                "toggleBg", "toggleActive", "accentPrimary", "accentError",
            ]
            for ak in adv_keys:
                if colors.get(ak):
                    content += f" {ak}={colors[ak]}"
        else:
            content = action
    elif tool_type in ("manage_tasks", "manage_skills", "api_call",
                        "manage_endpoints", "manage_mcp", "manage_webhooks",
                        "manage_tokens", "manage_documents", "manage_settings"):
        content = json.dumps(args)
    elif tool_type == "ask_teacher":
        content = args.get("model", "auto") + "\n" + args.get("problem", "")
    elif tool_type == "run_javascript":
        content = args.get("code", "")
    elif tool_type == "google_ai_search":
        content = args.get("query", "")
    elif tool_type == "download_pdf":
        content = json.dumps(args)
    elif tool_type == "scrapebadger":
        content = json.dumps(args)
    elif tool_type == "youtube_transcript":
        lang = args.get("language")
        if lang:
            content = f"{args.get('url', '')}\n@@LANG@@{lang}"
        else:
            content = args.get("url", "")
    elif tool_type == "reddit_search":
        content = json.dumps(args)
    else:
        content = json.dumps(args)

    return ToolBlock(tool_type, content)
