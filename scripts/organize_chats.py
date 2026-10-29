#!/usr/bin/env python3
"""organize_chats.py — group every chat into nested, use-case folders.

Folders are recursive: a chat's folder is a path like
"OSINT Investigations/Costa Rica Politicians/Rodrigo Chaves", which the sidebar
renders as nested folders. The TOP-LEVEL folder is the chat's *project*: only
chats inside the same project share context with each other (search, @-mentions,
the findings board — see src/hub.py).

How a chat is placed (first match wins):
  1. RULES below — ordered regexes over the chat's title, its first user
     messages and its auto-tags (the taxonomy breadcrumbs).
  2. Fallback — the chat's own tags: "Topics/<tag 1>/<tag 2>".
  3. Chats with no messages go to "Odysseus System/Empty chats".

Usage
  python3 scripts/organize_chats.py            # dry run: print the tree it would build
  python3 scripts/organize_chats.py --apply    # write it (backs up current folders first)
  python3 scripts/organize_chats.py --apply --keep-existing   # only file unfiled chats
  python3 scripts/organize_chats.py --undo     # restore the folders from the last backup
  python3 scripts/organize_chats.py --apply --api   # write through the running server
  python3 scripts/organize_chats.py --db /path/to/app.db

Writes go straight to the sessions table (the sidebar reads folders from the
database on every refresh), so it is safe while Odysseus is running — just
reload the page afterwards. Run it again any time to file new chats.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DB = os.path.join(ROOT, "data", "app.db")
BACKUP_DIR = os.path.join(ROOT, "data", "hub")

# Chats that live in special system folders are never moved.
PROTECTED_FOLDERS = {"Assistant"}

# ── The rules: (folder path, patterns). Order matters — specific first. ──
# Patterns are case-insensitive regexes, matched against
#   "<title> || <first user messages> || <tags>".
RULES = [
    # Requests Odysseus refused / safety probes — kept apart from real work.
    ("Red-Team & Ethics Tests/Doxxing Requests",
     [r"\bdoxx?(ing)?\b", r"\bdox\b", r"privacy violation"]),
    ("Red-Team & Ethics Tests/Dangerous Information",
     [r"explosive", r"\bweapons?\b"]),

    # Politics, law and world affairs research.
    ("Politics & Law/Costa Rica/Trade Agreements",
     [r"trans-?pac[ií]fic", r"transatl[aá]ntic", r"acuerdos? comercial"]),
    ("Politics & Law/Costa Rica/Citizen Rights",
     [r"citizen", r"rights protection", r"ciudadan", r"mechanisms? (available|i can use)"]),
    ("Politics & Law/Costa Rica/Public Affairs",
     [r"presupuesto", r"universidades", r"necropol", r"costa rica.*(politic|corrup)"]),
    ("Politics & Law/Latin America",
     [r"sheinbaum", r"shane baum", r"m[eé]xico"]),
    ("Politics & Law/World Affairs",
     [r"sudan", r"congo", r"vatican", r"conspira"]),
    ("Politics & Law/Legal Plans",
     [r"relocation", r"\blegal offer", r"\blaw\b"]),

    # OSINT investigations — people, places, companies, cases.
    ("OSINT Investigations/Costa Rica Politicians/Rodrigo Chaves",
     [r"rodrigo ch[aá]v(es|ez)"]),
    ("OSINT Investigations/Costa Rica Politicians/Laura Fernández",
     [r"laura fern[aá]ndez"]),
    ("OSINT Investigations/Yocosur Coba Permits",
     [r"yocosur", r"cabo blanco", r"grupo-zen", r"yoko zen"]),
    ("OSINT Investigations/People/Self (Luis Armando)",
     [r"luis armando", r"armando oriens", r"murillo baltodano", r"my (own )?name", r"mi nombre",
      r"trusted source", r"self-inquiry.*identity", r"identity.*self-inquiry", r"say my name"]),
    ("OSINT Investigations/People/Carlos Saurez",
     [r"saurez", r"cdp skill"]),
    ("OSINT Investigations/Companies & Leads",
     [r"lexcore", r"decision makers?", r"aiexecutions", r"bl00d\.money", r"tamarindo fitness",
      r"scan this whole website"]),
    ("OSINT Investigations/Media Extraction",
     [r"youtube channel", r"canal de", r"transcri", r"extract contacts", r"wordcast"]),
    ("OSINT Investigations/Reports",
     [r"findings", r"informe", r"report of"]),
    ("OSINT Investigations/Honduras",
     [r"honduras"]),

    # Building things.
    ("Coding & Automation/Browser Automation",
     [r"open youtube", r"open it in my browser", r"run_javascript", r"window\.open", r"google ai search",
      r"plugin", r"abra una ventana"]),
    ("Coding & Automation/Python Scripts/Audio & Speech",
     [r"audio", r"whistle", r"silbido", r"text-to-speech", r"\btts\b"]),
    ("Coding & Automation/Python Scripts/Images",
     [r"black and white", r"image processing", r"\bimage\b"]),
    ("Coding & Automation/Python Scripts",
     [r"\bpython\b", r"script", r"followers", r"command-line tool", r"\bcli\b"]),
    ("Coding & Automation/Reverse Engineering",
     [r"reverse engineering", r"github project"]),
    ("Coding & Automation/UI Design",
     [r"\bui design", r"\bUIs?\b that", r"award-worthy ui", r"user interfaces?"]),

    # Odysseus itself — capability tests, scheduled tasks, smoke tests.
    ("Odysseus System/Scheduled Tasks",
     [r"^\[task\]"]),
    ("Odysseus System/Capability Tests",
     [r"odysseus", r"capacidades", r"de qu[eé] eres capaz", r"enviar? .*al chat", r"branch test",
      r"\bgo\b$", r"^go\b", r"try again", r"second attempt", r"strange one"]),
    ("Odysseus System/Greetings & Smoke Tests",
     [r"^(hi|hello|hola)\b", r"greeting", r"current time", r"what time"]),

    # Strategy and money.
    ("Business & Strategy/Email Strategy",
     [r"correos?", r"email tactics", r"jaque"]),
    ("Business & Strategy/Income Ideas",
     [r"recurring income", r"digital products?", r"sales analysis", r"analyze this plan"]),
    ("Business & Strategy/Community Research",
     [r"grassroots", r"neighbor"]),

    # Everyday life & learning.
    ("Local Life (Costa Rica)",
     [r"gimnasio", r"\bgyms?\b", r"boxeo", r"boxing", r"burger", r"hamburgues", r"restaurant"]),
    ("Tech Support",
     [r"\bbios\b", r"windows 1[01]", r"thinkpad", r"malware"]),
    ("Learning & Q&A/Science",
     [r"physics", r"rigid body", r"chemistry", r"\bmath", r"calcula", r"\d+(\.\d+)?\s*%"]),
    ("Learning & Q&A/Language",
     [r"what does .* mean", r"idiom", r"fighting fit"]),
    ("Learning & Q&A/AI & Tech",
     [r"openai", r"voices?", r"artificial intelligence"]),
    ("Learning & Q&A/Culture & Records",
     [r"seinfeld", r"guinness", r"r[eé]cords?", r"music", r"canta", r"[aá]ngel"]),
]

_COMPILED = [(path, [re.compile(p, re.I | re.M) for p in pats]) for path, pats in RULES]


def _text(content):
    if isinstance(content, str):
        if content[:1] == "[":
            try:
                v = json.loads(content)
                if isinstance(v, list):
                    return " ".join(str(p.get("text", "")) for p in v if isinstance(p, dict))
            except ValueError:
                pass
        return content
    return str(content or "")


def classify(name, first_msgs, tags):
    """Returns (folder path, reason). Three passes so the chat's own title
    decides first, then its opening request, and only then later messages
    and tags (conversations drift; their start says what they are for)."""
    title = (name or "").strip()
    # Auto-stamped titles ("gemini-web-ui 6:47:28 PM", "deepseek-v4-pro 12:03:41 AM")
    # are just the model + time — they say nothing about the use case.
    if re.match(r"^[\w.\-]+ \d{1,2}:\d{2}(:\d{2})? ?[AP]M$", title, re.I):
        title = ""
    first = _text(first_msgs[0])[:600] if first_msgs else ""
    rest = " \n".join(_text(m)[:600] for m in first_msgs[1:])
    tag_txt = " ".join(str(t) for t in tags)
    passes = (("title", title),
              ("opening request", f"{title}\n{first}"),
              ("later messages / tags", f"{title}\n{first}\n{rest}\n{tag_txt}"))
    for label, hay in passes:
        for path, pats in _COMPILED:
            for p in pats:
                m = p.search(hay)
                if m:
                    return path, f"{label}: /{p.pattern}/ “{m.group(0)[:30]}”"
    clean = []
    for t in tags:
        t = str(t).strip()
        if t and t not in clean and t.lower() not in ("initial", "technology", "chat sessions"):
            clean.append(t)
    if clean:
        return "Topics/" + "/".join(clean[:2]), "fallback: auto-tags"
    return "Topics/Miscellaneous", "fallback: nothing matched"


def load(db):
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        c.execute("SELECT 1 FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT id, name, tags, folder, archived, message_count FROM sessions").fetchall()
    out = []
    for r in rows:
        msgs = [m["content"] for m in c.execute(
            "SELECT content FROM chat_messages WHERE session_id=? AND role='user' ORDER BY timestamp LIMIT 3",
            (r["id"],)).fetchall()]
        try:
            tags = json.loads(r["tags"]) if r["tags"] else []
        except ValueError:
            tags = []
        out.append({"id": r["id"], "name": r["name"] or "", "folder": r["folder"], "archived": r["archived"],
                    "count": r["message_count"] or 0, "first": msgs, "tags": tags if isinstance(tags, list) else []})
    c.close()
    return out


def plan(chats, keep_existing):
    moves = []
    for ch in chats:
        if ch["folder"] in PROTECTED_FOLDERS:
            continue
        if keep_existing and ch["folder"]:
            continue
        if not ch["first"] and not ch["count"]:
            path, why = "Odysseus System/Empty chats", "no messages"
        else:
            path, why = classify(ch["name"], ch["first"], ch["tags"])
        moves.append({"id": ch["id"], "name": ch["name"], "from": ch["folder"], "to": path, "why": why})
    return moves


def print_tree(moves, verbose):
    tree = {}
    for m in moves:
        node = tree
        for part in m["to"].split("/"):
            node = node.setdefault(part, {})
        node.setdefault("\0chats", []).append(m)

    def count(node):
        return len(node.get("\0chats", [])) + sum(count(v) for k, v in node.items() if k != "\0chats")

    def walk(node, depth):
        for k in sorted(x for x in node if x != "\0chats"):
            print("  " * depth + f"📁 {k}  ({count(node[k])})")
            walk(node[k], depth + 1)
            for m in node[k].get("\0chats", []):
                line = "  " * (depth + 1) + f"· {m['name'][:60]}"
                if verbose:
                    line += f"   [{m['why']}]"
                print(line)
    walk(tree, 0)
    print(f"\n{len(moves)} chats → {len({m['to'].split('/')[0] for m in moves})} projects, "
          f"{len({m['to'] for m in moves})} folders")


def apply(db, moves):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(BACKUP_DIR, f"folders-backup-{stamp}.json")
    with open(backup, "w", encoding="utf-8") as f:
        json.dump([{"id": m["id"], "folder": m["from"]} for m in moves], f, ensure_ascii=False, indent=1)
    c = sqlite3.connect(db)
    with c:
        for m in moves:
            c.execute("UPDATE sessions SET folder=? WHERE id=?", (m["to"], m["id"]))
    c.close()
    print(f"Applied {len(moves)} moves. Backup: {backup}\nReload Odysseus and switch the sidebar to “By folder”.")


def apply_api(base, moves, token=""):
    """Apply through the running server (PATCH /api/session/<id>) — use this
    when the database file is locked by Odysseus or lives on another machine."""
    import urllib.request, uuid
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(BACKUP_DIR, f"folders-backup-{stamp}.json")
    with open(backup, "w", encoding="utf-8") as f:
        json.dump([{"id": m["id"], "folder": m["from"]} for m in moves], f, ensure_ascii=False, indent=1)
    ok = 0
    for m in moves:
        b = uuid.uuid4().hex
        body = (f'--{b}\r\nContent-Disposition: form-data; name="folder"\r\n\r\n{m["to"]}\r\n--{b}--\r\n').encode()
        req = urllib.request.Request(f"{base.rstrip('/')}/api/session/{m['id']}", data=body, method="PATCH",
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}",
                                              **({"Authorization": "Bearer " + token} if token else {})})
        try:
            urllib.request.urlopen(req, timeout=20).read()
            ok += 1
        except Exception as e:
            print(f"  ! {m['name'][:40]}: {e}")
    print(f"Applied {ok}/{len(moves)} moves via {base}. Backup: {backup}")


def undo(db):
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "folders-backup-*.json")))
    if not files:
        sys.exit("No backup found.")
    with open(files[-1], encoding="utf-8") as f:
        prev = json.load(f)
    c = sqlite3.connect(db)
    with c:
        for p in prev:
            c.execute("UPDATE sessions SET folder=? WHERE id=?", (p["folder"], p["id"]))
    c.close()
    os.rename(files[-1], files[-1] + ".restored")
    print(f"Restored {len(prev)} chats from {os.path.basename(files[-1])}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="write the folders (default: dry run)")
    ap.add_argument("--keep-existing", action="store_true", help="leave chats that already have a folder")
    ap.add_argument("--undo", action="store_true", help="restore folders from the last backup")
    ap.add_argument("-v", "--verbose", action="store_true", help="show why each chat was placed")
    ap.add_argument("--api", nargs="?", const="http://127.0.0.1:7860", default=None,
                    help="apply through the running server instead of the database file "
                         "(auth: ODYSSEUS_TOKEN env var)")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        sys.exit(f"Database not found: {a.db}")
    if a.undo:
        return undo(a.db)
    moves = plan(load(a.db), a.keep_existing)
    if a.json:
        print(json.dumps(moves, ensure_ascii=False))
        return
    print_tree(moves, a.verbose or not a.apply)
    if a.apply and a.api:
        apply_api(a.api, moves, os.environ.get("ODYSSEUS_TOKEN", ""))
    elif a.apply:
        try:
            apply(a.db, moves)
        except sqlite3.OperationalError as e:
            sys.exit(f"Could not write the database ({e}). It is probably locked by the running "
                     "server — re-run with --apply --api to go through Odysseus instead.")
    else:
        print("\nDry run — nothing changed. Re-run with --apply to write these folders.")


if __name__ == "__main__":
    main()
