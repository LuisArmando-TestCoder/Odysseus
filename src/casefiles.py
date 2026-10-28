"""
casefiles.py — Case Files: the organized intelligence store ("the Batcomputer").

Every investigation / research thread the user (or the agent) works on gets a
*case*. A case keeps everything about that thread in ONE structured place
instead of scattered across chats, notes and memory:

  - entities   people, orgs, usernames, emails, phones, domains, urls, ips,
               locations, crypto wallets … — normalized and DEDUPED, so the
               same email found three times is one entity with three sources.
  - findings   atomic facts with a source URL, a confidence (0..1) and the
               entities they mention (auto-extracted from the text).
  - links      typed relations between entities ("owns", "alias_of", …).
  - next steps an open-loop task list for the case. ``suggest_pivots`` derives
               concrete next moves from what is known so far, so the agent
               always knows what to chase next — relentless by construction.
  - timeline   append-only audit trail of everything that happened.

Storage: one JSON file per case under ``data/cases/<owner>/<case_id>.json``,
written atomically. Stdlib only, so it is cheap to import on every path and
trivially testable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

ENTITY_TYPES = (
    "person", "org", "username", "email", "phone", "domain", "url", "ip",
    "location", "crypto", "vehicle", "document", "other",
)
CASE_STATUSES = ("open", "paused", "closed")
MAX_TIMELINE = 500
MAX_FINDINGS = 5000

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_PHONE_RE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{7,}\d(?![\w/])")
_HANDLE_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9_])?)\b")
_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,24})\b", re.IGNORECASE)
_BTC_RE = re.compile(r"\b(?:bc1[a-z0-9]{25,59}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
_ETH_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

# Common file-ish suffixes that the domain regex would otherwise grab.
_NOT_TLDS = {"png", "jpg", "jpeg", "gif", "pdf", "txt", "md", "py", "js", "json",
             "html", "htm", "css", "csv", "zip", "exe", "doc", "docx", "xls",
             "xlsx", "svg", "webp", "mp4", "mp3", "log", "yaml", "yml", "ts"}


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _slug(value: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "_anon")).strip("._")
    return s[:80] or "_anon"


# ---------------------------------------------------------------------------
# Normalization + extraction
# ---------------------------------------------------------------------------

def normalize_entity(etype: str, value: str) -> str:
    """Canonical dedupe key for an entity value of a given type."""
    v = (value or "").strip()
    t = (etype or "other").lower()
    if t == "email":
        return v.lower()
    if t == "domain":
        v = v.lower().rstrip(".")
        return v[4:] if v.startswith("www.") else v
    if t == "username":
        return v.lstrip("@").lower()
    if t == "phone":
        digits = re.sub(r"\D", "", v)
        return ("+" + digits) if v.startswith("+") else digits
    if t == "url":
        m = re.match(r"^(https?)://([^/]+)(.*)$", v, re.IGNORECASE)
        if m:
            host = m.group(2).lower()
            if host.startswith("www."):
                host = host[4:]
            rest = m.group(3).rstrip("/")
            return f"{m.group(1).lower()}://{host}{rest}"
        return v.rstrip("/").lower()
    if t == "crypto":
        return v if not v.lower().startswith("0x") else v.lower()
    return re.sub(r"\s+", " ", v).lower()


def guess_entity_type(value: str) -> str:
    """Best-effort type detection for a bare value."""
    v = (value or "").strip()
    if not v:
        return "other"
    if _EMAIL_RE.fullmatch(v):
        return "email"
    if _URL_RE.fullmatch(v):
        return "url"
    if _IPV4_RE.fullmatch(v):
        return "ip"
    if _ETH_RE.fullmatch(v) or _BTC_RE.fullmatch(v):
        return "crypto"
    if v.startswith("@") and _HANDLE_RE.fullmatch(v):
        return "username"
    if _PHONE_RE.fullmatch(v) and len(re.sub(r"\D", "", v)) >= 8:
        return "phone"
    if _DOMAIN_RE.fullmatch(v) and v.rsplit(".", 1)[-1].lower() not in _NOT_TLDS:
        return "domain"
    if re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", v) and not v.isdigit() and " " not in v:
        return "username"
    return "other"


def extract_entities(text: str) -> List[Tuple[str, str]]:
    """Pull obvious selectors (emails, urls, ips, phones, handles, wallets,
    domains) out of free text. Returns de-duplicated (type, value) pairs in
    first-seen order."""
    text = text or ""
    found: List[Tuple[str, str]] = []
    seen = set()

    def _add(t: str, v: str):
        k = (t, normalize_entity(t, v))
        if k[1] and k not in seen:
            seen.add(k)
            found.append((t, v))

    emails = _EMAIL_RE.findall(text)
    urls = [u.rstrip(".,;:") for u in _URL_RE.findall(text)]
    for e in emails:
        _add("email", e)
    for u in urls:
        _add("url", u)
    for ip in _IPV4_RE.findall(text):
        _add("ip", ip)
    for w in _ETH_RE.findall(text):
        _add("crypto", w)
    for w in _BTC_RE.findall(text):
        _add("crypto", w)
    for h in _HANDLE_RE.findall(text):
        _add("username", h)
    # Phones: skip things that are actually IPs or dates.
    for p in _PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", p)
        if 8 <= len(digits) <= 15 and not _IPV4_RE.search(p) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.strip()):
            _add("phone", p.strip())
    # Domains: exclude ones already covered by email/url hosts and file names.
    covered = {e.split("@", 1)[1].lower() for e in emails}
    for u in urls:
        m = re.match(r"^https?://([^/:]+)", u, re.IGNORECASE)
        if m:
            covered.add(normalize_entity("domain", m.group(1)))
    scrubbed = _URL_RE.sub(" ", _EMAIL_RE.sub(" ", text))
    for d in _DOMAIN_RE.findall(scrubbed):
        nd = normalize_entity("domain", d)
        if nd.rsplit(".", 1)[-1] in _NOT_TLDS or nd in covered or _IPV4_RE.fullmatch(nd):
            continue
        if re.fullmatch(r"[\d.]+", nd):
            continue
        _add("domain", d)
    return found


# ---------------------------------------------------------------------------
# Pivot playbook — what to do next for each selector type
# ---------------------------------------------------------------------------

PIVOTS: Dict[str, List[str]] = {
    "username": [
        "Enumerate `{v}` across platforms (sherlock_osint)",
        "Search the web for \"{v}\" in quotes plus likely real-name pairings",
    ],
    "email": [
        "Check breach exposure for {v} (e.g. haveibeenpwned) and note dates",
        "Search the web for \"{v}\" and its local-part as a username",
        "Look up the mail domain of {v} (MX / org ownership)",
    ],
    "domain": [
        "Pull WHOIS / RDAP for {v} (registrar, creation date, registrant org)",
        "List subdomains for {v} via certificate transparency (crt.sh)",
        "Check DNS (A/MX/TXT) and hosting for {v}",
        "Look for archived versions of {v} (Wayback Machine)",
    ],
    "phone": [
        "Identify carrier / country / line type for {v}",
        "Search the web and messaging apps for {v}",
    ],
    "url": [
        "Fetch and summarize {v}; extract any new selectors",
        "Check archived snapshots of {v}",
        "Add {v} to the watchlist if it may change",
    ],
    "ip": [
        "Geolocate {v} and identify its ASN / hosting provider",
        "Reverse-DNS {v} and check for co-hosted domains",
    ],
    "person": [
        "Search the web for \"{v}\" with location/employer qualifiers",
        "Check official registries (company / property / court) for {v}",
    ],
    "org": [
        "Look up corporate registry records and officers for {v}",
        "Find the official domain(s) and social accounts of {v}",
    ],
    "crypto": [
        "Trace transactions for wallet {v} on a block explorer",
    ],
    "location": [
        "Map {v} and check satellite / street-level imagery",
    ],
}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class CaseStore:
    """File-backed store of cases, one JSON file per case per owner."""

    def __init__(self, data_dir: str):
        self.root = os.path.join(data_dir, "cases")
        self._lock = threading.RLock()

    # -- paths / io --------------------------------------------------------
    def _owner_dir(self, owner: Optional[str]) -> str:
        d = os.path.join(self.root, _slug(owner or "_anon"))
        os.makedirs(d, exist_ok=True)
        return d

    def _case_path(self, owner: Optional[str], case_id: str) -> str:
        if not re.fullmatch(r"case_[0-9a-f]{10}", case_id or ""):
            raise KeyError(f"invalid case id: {case_id!r}")
        return os.path.join(self._owner_dir(owner), f"{case_id}.json")

    def _read(self, owner, case_id) -> Dict[str, Any]:
        path = self._case_path(owner, case_id)
        if not os.path.exists(path):
            raise KeyError(f"case not found: {case_id}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, owner, case: Dict[str, Any]) -> None:
        path = self._case_path(owner, case["id"])
        case["updated_at"] = _now()
        d = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".case-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(case, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _log(case: Dict[str, Any], event: str) -> None:
        tl = case.setdefault("timeline", [])
        tl.append({"ts": _now(), "event": event[:500]})
        if len(tl) > MAX_TIMELINE:
            del tl[: len(tl) - MAX_TIMELINE]

    # -- resolution --------------------------------------------------------
    def resolve_case_id(self, owner, ref: str) -> str:
        """Accept a full id, an id prefix, or a (case-insensitive) title."""
        ref = (ref or "").strip()
        if not ref:
            raise KeyError("case reference is required")
        cases = self.list_cases(owner, status=None)
        for c in cases:
            if c["id"] == ref:
                return c["id"]
        pref = [c for c in cases if c["id"].startswith(ref) or c["id"][5:].startswith(ref)]
        if len(pref) == 1:
            return pref[0]["id"]
        low = ref.lower()
        exact = [c for c in cases if c["title"].lower() == low]
        if len(exact) == 1:
            return exact[0]["id"]
        part = [c for c in cases if low in c["title"].lower()]
        if len(part) == 1:
            return part[0]["id"]
        if len(pref) + len(exact) + len(part) > 1:
            raise KeyError(f"ambiguous case reference: {ref!r}")
        raise KeyError(f"case not found: {ref!r}")

    # -- cases -------------------------------------------------------------
    def create_case(self, owner, title: str, objective: str = "",
                    tags: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("title is required")
        with self._lock:
            case = {
                "id": "case_" + uuid.uuid4().hex[:10],
                "title": title[:200],
                "objective": (objective or "").strip()[:2000],
                "status": "open",
                "tags": sorted({str(t).strip().lower() for t in (tags or []) if str(t).strip()}),
                "created_at": _now(),
                "updated_at": _now(),
                "entities": {},
                "findings": [],
                "links": [],
                "next_steps": [],
                "timeline": [],
            }
            self._log(case, f"Case opened: {case['title']}")
            self._write(owner, case)
            return case

    def get_case(self, owner, case_ref: str) -> Dict[str, Any]:
        with self._lock:
            return self._read(owner, self.resolve_case_id(owner, case_ref))

    def list_cases(self, owner, status: Optional[str] = "open") -> List[Dict[str, Any]]:
        d = self._owner_dir(owner)
        out = []
        for name in os.listdir(d):
            if not name.endswith(".json") or name.startswith("."):
                continue
            try:
                with open(os.path.join(d, name), "r", encoding="utf-8") as f:
                    c = json.load(f)
            except (OSError, ValueError):
                continue
            if status and c.get("status") != status:
                continue
            out.append({
                "id": c["id"], "title": c.get("title", ""), "status": c.get("status"),
                "objective": c.get("objective", ""), "tags": c.get("tags", []),
                "entities": len(c.get("entities") or {}), "findings": len(c.get("findings") or []),
                "open_steps": sum(1 for s in c.get("next_steps") or [] if not s.get("done")),
                "updated_at": c.get("updated_at", 0),
            })
        out.sort(key=lambda c: c["updated_at"], reverse=True)
        return out

    def update_case(self, owner, case_ref: str, **fields) -> Dict[str, Any]:
        with self._lock:
            case = self._read(owner, self.resolve_case_id(owner, case_ref))
            changes = []
            if fields.get("title"):
                case["title"] = str(fields["title"]).strip()[:200]
                changes.append("title")
            if fields.get("objective") is not None:
                case["objective"] = str(fields["objective"]).strip()[:2000]
                changes.append("objective")
            if fields.get("status"):
                st = str(fields["status"]).lower()
                if st not in CASE_STATUSES:
                    raise ValueError(f"status must be one of {CASE_STATUSES}")
                case["status"] = st
                changes.append(f"status→{st}")
            if fields.get("tags") is not None:
                case["tags"] = sorted({str(t).strip().lower() for t in fields["tags"] if str(t).strip()})
                changes.append("tags")
            if changes:
                self._log(case, "Updated " + ", ".join(changes))
                self._write(owner, case)
            return case

    def delete_case(self, owner, case_ref: str) -> bool:
        with self._lock:
            cid = self.resolve_case_id(owner, case_ref)
            os.remove(self._case_path(owner, cid))
            return True

    # -- entities ----------------------------------------------------------
    @staticmethod
    def _find_entity(case, etype: str, key: str) -> Optional[Dict[str, Any]]:
        for e in case["entities"].values():
            if e["type"] == etype and e["key"] == key:
                return e
            if key in (e.get("alias_keys") or []) and e["type"] == etype:
                return e
        return None

    def _upsert_entity(self, case, value: str, etype: Optional[str] = None, *,
                       notes: str = "", source: str = "", confidence: Optional[float] = None,
                       label: str = "") -> Tuple[Dict[str, Any], bool]:
        value = (value or "").strip()
        if not value:
            raise ValueError("entity value is required")
        etype = (etype or guess_entity_type(value)).lower()
        if etype not in ENTITY_TYPES:
            etype = "other"
        key = normalize_entity(etype, value)
        ent = self._find_entity(case, etype, key)
        created = False
        if ent is None:
            ent = {
                "id": _new_id("ent"), "type": etype, "value": value, "key": key,
                "label": label[:200], "notes": "", "aliases": [], "alias_keys": [],
                "sources": [], "confidence": 0.5, "first_seen": _now(), "last_seen": _now(),
                "mentions": 0,
            }
            case["entities"][ent["id"]] = ent
            created = True
        ent["last_seen"] = _now()
        ent["mentions"] = int(ent.get("mentions", 0)) + 1
        if label and not ent.get("label"):
            ent["label"] = label[:200]
        if notes:
            ent["notes"] = (ent.get("notes", "") + ("\n" if ent.get("notes") else "") + notes.strip())[-4000:]
        if source and source not in ent["sources"]:
            ent["sources"].append(source[:1000])
        if confidence is not None:
            c = max(0.0, min(1.0, float(confidence)))
            # Corroboration only ever raises confidence.
            ent["confidence"] = max(ent.get("confidence", 0.0), c)
        elif not created and len(ent["sources"]) > 1:
            ent["confidence"] = min(1.0, ent.get("confidence", 0.5) + 0.1)
        return ent, created

    def add_entity(self, owner, case_ref: str, value: str, etype: Optional[str] = None,
                   **kw) -> Dict[str, Any]:
        with self._lock:
            case = self._read(owner, self.resolve_case_id(owner, case_ref))
            ent, created = self._upsert_entity(case, value, etype, **kw)
            self._log(case, f"{'Added' if created else 'Corroborated'} {ent['type']} {ent['value']}")
            self._write(owner, case)
            return {**ent, "created": created}

    def add_alias(self, owner, case_ref: str, entity_ref: str, alias: str) -> Dict[str, Any]:
        with self._lock:
            case = self._read(owner, self.resolve_case_id(owner, case_ref))
            ent = self._resolve_entity(case, entity_ref)
            k = normalize_entity(ent["type"], alias)
            if k and k != ent["key"] and k not in ent["alias_keys"]:
                ent["aliases"].append(alias.strip())
                ent["alias_keys"].append(k)
                self._log(case, f"Alias {alias} → {ent['value']}")
                self._write(owner, case)
            return ent

    @staticmethod
    def _resolve_entity(case, ref: str) -> Dict[str, Any]:
        ref = (ref or "").strip()
        if ref in case["entities"]:
            return case["entities"][ref]
        low = ref.lower()
        for e in case["entities"].values():
            if e["key"] == normalize_entity(e["type"], ref) or e["value"].lower() == low:
                return e
        raise KeyError(f"entity not found in case: {ref!r}")

    # -- findings ----------------------------------------------------------
    def add_finding(self, owner, case_ref: str, text: str, *, source_url: str = "",
                    confidence: float = 0.6, kind: str = "fact",
                    entities: Optional[Iterable[Any]] = None,
                    auto_extract: bool = True) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("finding text is required")
        with self._lock:
            case = self._read(owner, self.resolve_case_id(owner, case_ref))
            confidence = max(0.0, min(1.0, float(confidence)))
            ent_ids: List[str] = []
            new_ents = []
            pairs: List[Tuple[Optional[str], str]] = []
            for e in entities or []:
                if isinstance(e, dict):
                    pairs.append((e.get("type"), str(e.get("value", ""))))
                else:
                    pairs.append((None, str(e)))
            if auto_extract:
                pairs.extend(extract_entities(text))
            for etype, val in pairs:
                if not val.strip():
                    continue
                ent, created = self._upsert_entity(case, val, etype, source=source_url,
                                                   confidence=None)
                if ent["id"] not in ent_ids:
                    ent_ids.append(ent["id"])
                if created:
                    new_ents.append(ent)
            # De-dupe identical findings (same text + source).
            for f in case["findings"]:
                if f["text"] == text and f.get("source_url", "") == source_url:
                    f["confidence"] = max(f.get("confidence", 0), confidence)
                    self._write(owner, case)
                    return {**f, "duplicate": True, "new_entities": []}
            finding = {
                "id": _new_id("fnd"), "text": text[:8000], "source_url": source_url[:2000],
                "confidence": confidence, "kind": (kind or "fact")[:40],
                "entity_ids": ent_ids, "ts": _now(),
            }
            case["findings"].append(finding)
            if len(case["findings"]) > MAX_FINDINGS:
                del case["findings"][: len(case["findings"]) - MAX_FINDINGS]
            self._log(case, f"Finding: {text[:120]}")
            self._write(owner, case)
            return {**finding, "new_entities": [
                {"id": e["id"], "type": e["type"], "value": e["value"]} for e in new_ents]}

    # -- links -------------------------------------------------------------
    def link(self, owner, case_ref: str, a: str, b: str, relation: str,
             evidence: str = "") -> Dict[str, Any]:
        relation = (relation or "related_to").strip().lower().replace(" ", "_")[:60]
        with self._lock:
            case = self._read(owner, self.resolve_case_id(owner, case_ref))
            ea = self._resolve_or_create(case, a)
            eb = self._resolve_or_create(case, b)
            for l in case["links"]:
                if {l["a"], l["b"]} == {ea["id"], eb["id"]} and l["relation"] == relation:
                    if evidence and evidence not in l.get("evidence", []):
                        l.setdefault("evidence", []).append(evidence[:1000])
                        self._write(owner, case)
                    return l
            link = {"id": _new_id("lnk"), "a": ea["id"], "b": eb["id"], "relation": relation,
                    "evidence": [evidence[:1000]] if evidence else [], "ts": _now()}
            case["links"].append(link)
            self._log(case, f"Linked {ea['value']} —{relation}→ {eb['value']}")
            self._write(owner, case)
            return link

    def _resolve_or_create(self, case, ref: str) -> Dict[str, Any]:
        try:
            return self._resolve_entity(case, ref)
        except KeyError:
            ent, _ = self._upsert_entity(case, ref)
            return ent

    # -- next steps --------------------------------------------------------
    def add_step(self, owner, case_ref: str, text: str) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("step text is required")
        with self._lock:
            case = self._read(owner, self.resolve_case_id(owner, case_ref))
            for s in case["next_steps"]:
                if s["text"].lower() == text.lower():
                    return s
            step = {"id": _new_id("stp"), "text": text[:500], "done": False,
                    "created_at": _now(), "done_at": None, "result": ""}
            case["next_steps"].append(step)
            self._log(case, f"Next step: {text[:120]}")
            self._write(owner, case)
            return step

    def complete_step(self, owner, case_ref: str, step_ref: str, result: str = "") -> Dict[str, Any]:
        with self._lock:
            case = self._read(owner, self.resolve_case_id(owner, case_ref))
            for s in case["next_steps"]:
                if s["id"] == step_ref or s["id"].startswith(step_ref) or s["text"].lower() == step_ref.lower():
                    s["done"] = True
                    s["done_at"] = _now()
                    s["result"] = (result or "")[:2000]
                    self._log(case, f"Done: {s['text'][:120]}")
                    self._write(owner, case)
                    return s
            raise KeyError(f"step not found: {step_ref!r}")

    def suggest_pivots(self, owner, case_ref: str, limit: int = 12) -> List[str]:
        """Concrete next moves derived from the case's entities that have not
        already been queued or done. Weakest-sourced entities come first —
        they are where corroboration is most valuable."""
        case = self.get_case(owner, case_ref)
        known = {s["text"].lower() for s in case["next_steps"]}
        ents = sorted(case["entities"].values(),
                      key=lambda e: (len(e.get("sources") or []), e.get("confidence", 0)))
        out: List[str] = []
        for e in ents:
            for tpl in PIVOTS.get(e["type"], []):
                s = tpl.format(v=e["value"])
                if s.lower() not in known and s not in out:
                    out.append(s)
                if len(out) >= limit:
                    return out
        if not case["entities"] and case.get("objective"):
            out.append(f"Identify the initial selectors (names, handles, emails, domains) for: {case['objective'][:200]}")
        return out

    # -- search / export ---------------------------------------------------
    def search(self, owner, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        hits: List[Dict[str, Any]] = []
        for meta in self.list_cases(owner, status=None):
            try:
                case = self._read(owner, meta["id"])
            except (KeyError, OSError, ValueError):
                continue
            if q in case["title"].lower() or q in case.get("objective", "").lower():
                hits.append({"case_id": case["id"], "case": case["title"], "kind": "case",
                             "text": case.get("objective") or case["title"]})
            for e in case["entities"].values():
                hay = " ".join([e["value"], e.get("label", ""), e.get("notes", "")] + e.get("aliases", [])).lower()
                if q in hay or normalize_entity(e["type"], query) == e["key"]:
                    hits.append({"case_id": case["id"], "case": case["title"], "kind": "entity",
                                 "text": f"[{e['type']}] {e['value']}", "id": e["id"]})
            for f in case["findings"]:
                if q in f["text"].lower():
                    hits.append({"case_id": case["id"], "case": case["title"], "kind": "finding",
                                 "text": f["text"][:300], "id": f["id"]})
            if len(hits) >= limit:
                break
        return hits[:limit]

    def export_markdown(self, owner, case_ref: str) -> str:
        case = self.get_case(owner, case_ref)
        ents = case["entities"]

        def _ts(t):
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(t or 0))

        lines = [f"# Case: {case['title']}", "",
                 f"- **ID:** `{case['id']}`  ·  **Status:** {case['status']}  ·  "
                 f"**Updated:** {_ts(case.get('updated_at'))}"]
        if case.get("tags"):
            lines.append(f"- **Tags:** {', '.join(case['tags'])}")
        if case.get("objective"):
            lines += ["", "## Objective", "", case["objective"]]
        lines += ["", f"## Entities ({len(ents)})", ""]
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for e in ents.values():
            by_type.setdefault(e["type"], []).append(e)
        for t in ENTITY_TYPES:
            group = by_type.get(t)
            if not group:
                continue
            lines.append(f"### {t.title()}")
            for e in sorted(group, key=lambda e: -e.get("confidence", 0)):
                extra = f" — {e['label']}" if e.get("label") else ""
                alias = f" (aka {', '.join(e['aliases'])})" if e.get("aliases") else ""
                lines.append(f"- `{e['value']}`{alias}{extra} · conf {e.get('confidence', 0):.2f} · "
                             f"{len(e.get('sources') or [])} source(s)")
            lines.append("")
        if case["links"]:
            lines += [f"## Relationships ({len(case['links'])})", ""]
            for l in case["links"]:
                a, b = ents.get(l["a"], {}), ents.get(l["b"], {})
                lines.append(f"- `{a.get('value', l['a'])}` —**{l['relation']}**→ `{b.get('value', l['b'])}`")
            lines.append("")
        lines += [f"## Findings ({len(case['findings'])})", ""]
        for f in sorted(case["findings"], key=lambda f: -f.get("confidence", 0)):
            src = f" ([source]({f['source_url']}))" if f.get("source_url") else ""
            lines.append(f"- {f['text']}{src} · conf {f.get('confidence', 0):.2f} · {_ts(f.get('ts'))}")
        open_steps = [s for s in case["next_steps"] if not s["done"]]
        done_steps = [s for s in case["next_steps"] if s["done"]]
        lines += ["", "## Next steps", ""]
        lines += [f"- [ ] {s['text']}" for s in open_steps] or ["- (none queued)"]
        if done_steps:
            lines += ["", "### Completed", ""]
            lines += [f"- [x] {s['text']}" + (f" — {s['result']}" if s.get("result") else "") for s in done_steps]
        lines += ["", "## Timeline", ""]
        lines += [f"- {_ts(t['ts'])} — {t['event']}" for t in case.get("timeline", [])[-50:]]
        return "\n".join(lines) + "\n"

    def brief(self, owner, limit: int = 5) -> str:
        """One-paragraph-per-case status for the daily brief / proactive check-ins."""
        cases = self.list_cases(owner, status="open")[:limit]
        if not cases:
            return ""
        out = ["Open cases:"]
        for c in cases:
            out.append(f"- {c['title']} ({c['entities']} entities, {c['findings']} findings, "
                       f"{c['open_steps']} open steps)")
        return "\n".join(out)


_stores: Dict[str, CaseStore] = {}
_stores_lock = threading.Lock()


def get_case_store(data_dir: Optional[str] = None) -> CaseStore:
    if data_dir is None:
        from src.constants import DATA_DIR
        data_dir = DATA_DIR
    with _stores_lock:
        st = _stores.get(data_dir)
        if st is None:
            st = _stores[data_dir] = CaseStore(data_dir)
        return st
