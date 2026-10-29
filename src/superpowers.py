# src/superpowers.py
"""Odysseus superpowers — first-class OSINT reconnaissance, modelled on the
OSINT Framework (osintframework.com, bundled as arf.json).

One tool, ``recon``, with an ``action`` per capability. Every module uses
free, keyless, public sources, runs with short timeouts and returns both a
readable markdown summary (``output``) and structured ``data``:

  auto        detect what the target is and run every relevant module at once
  dns         A/AAAA/MX/NS/TXT/CNAME/SOA/CAA + SPF/DMARC (DNS-over-HTTPS)
  whois       RDAP registration data for a domain or IP
  subdomains  certificate-transparency subdomain discovery (crt.sh)
  http        live fetch: redirect chain, headers, security headers, tech
              fingerprint, title, public contact emails, social links,
              robots.txt / security.txt / sitemap   (ACTIVE: touches target)
  ip          geolocation, ASN/ISP, reverse DNS
  wayback     Internet Archive: first/last capture, capture count, snapshots
  email       syntax, MX/SPF/DMARC, disposable-provider check, Gravatar
  username    presence on ~15 platforms with reliable public APIs
  github      public GitHub profile, organisations, top repositories
  reddit      public Reddit profile + where the account is active
  hackernews  public HN profile + recent activity
  phone       E.164 normalisation, country, number-type hints, search pivots
  geocode     OpenStreetMap place search / reverse geocoding (lat,lon)
  exif        image metadata incl. GPS from a URL or a workspace file
  reverse_image  reverse-image-search links (Lens, Bing, Yandex, TinEye)
  dorks       advanced search-engine queries for a target (optionally run)
  crypto      Bitcoin / Ethereum address activity (Blockstream, Blockscout)
  framework   best matching tools from the OSINT Framework tree

Ethics are built in: recon is for organisations, infrastructure, public
figures' public conduct, the user's own footprint and authorised
investigations. It never compiles a private person's home address, family or
whereabouts (see POLICY, enforced in ``_policy_block``).
"""

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

UA = "Odysseus-OSINT/1.0 (+local research assistant)"
TIMEOUT = 12.0

POLICY = (
    "Recon is for organisations, infrastructure, public figures' PUBLIC conduct, the "
    "user's own footprint and authorised investigations. Never locate or compile a "
    "private person's home address, family members, daily whereabouts or contact "
    "details for the purpose of finding them (doxxing) — decline that and offer a "
    "lawful alternative (official channels, the person's public professional profile)."
)

_DOXX_RE = re.compile(
    r"\b(home address|where (does|do|did) .{0,40} live|lives at|dónde vive|donde vive|"
    r"direcci[oó]n de (su )?casa|domicilio|residen(ce|cia) (of|de)|doxx?|family members?|"
    r"daily routine|whereabouts)\b", re.I)


def _policy_block(*texts) -> Optional[str]:
    hay = " ".join(str(t or "") for t in texts)
    if _DOXX_RE.search(hay):
        return ("Declined by recon policy: locating a person's home, family or whereabouts is "
                "doxxing. " + POLICY)
    return None


# ── HTTP helper ─────────────────────────────────────────────────────────────

async def _get(client, url, *, accept=None, timeout=TIMEOUT, as_json=True, method="GET"):
    try:
        h = {"User-Agent": UA}
        if accept:
            h["Accept"] = accept
        r = await client.request(method, url, headers=h, timeout=timeout, follow_redirects=True)
        if not as_json:
            return r
        if r.status_code == 404:
            return {"_status": 404}
        if r.status_code >= 400:
            return {"_status": r.status_code, "_error": r.text[:200]}
        try:
            return r.json()
        except ValueError:
            return {"_status": r.status_code, "_error": "non-JSON response"}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"[:200]}


def _client():
    import httpx
    return httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True)


# ── target detection ────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$", re.I)
_BTC_RE = re.compile(r"^(bc1[a-z0-9]{25,90}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$")
_ETH_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,20}$")
_COORD_RE = re.compile(r"^\s*(-?\d{1,2}(\.\d+)?)\s*,\s*(-?\d{1,3}(\.\d+)?)\s*$")


def detect(target: str) -> str:
    t = (target or "").strip()
    if not t:
        return "empty"
    if t.lower().startswith(("http://", "https://")):
        return "url"
    try:
        ipaddress.ip_address(t)
        return "ip"
    except ValueError:
        pass
    if _EMAIL_RE.match(t):
        return "email"
    if _ETH_RE.match(t):
        return "eth"
    if _BTC_RE.match(t):
        return "btc"
    if _COORD_RE.match(t):
        return "coords"
    if _DOMAIN_RE.match(t):
        return "domain"
    if _PHONE_RE.match(t) and sum(c.isdigit() for c in t) >= 7:
        return "phone"
    if t.startswith("@") or re.match(r"^[A-Za-z0-9_.-]{2,40}$", t):
        return "username"
    return "text"


def _domain_of(t: str) -> str:
    t = t.strip()
    if "://" in t:
        t = urlparse(t).hostname or t
    return t.lower().rstrip(".")


# ── modules ─────────────────────────────────────────────────────────────────

_DNS_TYPES = ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA", "CAA"]


async def dns(client, domain: str) -> Dict[str, Any]:
    domain = _domain_of(domain)

    async def q(name, typ):
        d = await _get(client, f"https://cloudflare-dns.com/dns-query?name={quote(name)}&type={typ}",
                       accept="application/dns-json")
        return [a.get("data") for a in (d.get("Answer") or []) if isinstance(a, dict)] if isinstance(d, dict) else []

    res = await asyncio.gather(*[q(domain, t) for t in _DNS_TYPES], q("_dmarc." + domain, "TXT"))
    recs = {t: r for t, r in zip(_DNS_TYPES, res[:-1]) if r}
    spf = [x for x in recs.get("TXT", []) if "v=spf1" in x.lower()]
    dmarc = [x for x in res[-1] if "v=dmarc1" in x.lower()]
    verif = [x for x in recs.get("TXT", []) if re.search(r"(verification|verify|site-verification|=)", x, re.I) and "spf" not in x.lower()]
    providers = _mail_provider(recs.get("MX", []))
    lines = [f"**DNS — {domain}**"]
    for t in _DNS_TYPES:
        if recs.get(t):
            lines.append(f"- {t}: " + "; ".join(str(x)[:120] for x in recs[t][:8]))
    lines.append(f"- SPF: {'present' if spf else 'missing'} · DMARC: {(dmarc[0][:80] if dmarc else 'missing')}")
    if providers:
        lines.append(f"- Mail provider: {', '.join(providers)}")
    if verif:
        lines.append(f"- Service verification TXT (reveals SaaS in use): {len(verif)} record(s)")
    return {"output": "\n".join(lines), "data": {"records": recs, "spf": spf, "dmarc": dmarc,
                                                  "mail_providers": providers, "verification_txt": verif}}


def _mail_provider(mx: List[str]) -> List[str]:
    m = " ".join(mx).lower()
    out = []
    for key, name in (("google", "Google Workspace"), ("outlook", "Microsoft 365"), ("protection.outlook", "Microsoft 365"),
                      ("zoho", "Zoho Mail"), ("protonmail", "Proton Mail"), ("mimecast", "Mimecast"),
                      ("pphosted", "Proofpoint"), ("secureserver", "GoDaddy"), ("mailgun", "Mailgun"),
                      ("amazonaws", "Amazon SES"), ("yandex", "Yandex"), ("icloud", "iCloud")):
        if key in m and name not in out:
            out.append(name)
    return out


async def whois(client, target: str) -> Dict[str, Any]:
    t = _domain_of(target)
    is_ip = detect(t) == "ip"
    d = await _get(client, f"https://rdap.org/{'ip' if is_ip else 'domain'}/{quote(t)}",
                   accept="application/rdap+json")
    if not isinstance(d, dict) or d.get("_error") or d.get("_status"):
        return {"output": f"**RDAP — {t}**: no record ({(d or {}).get('_status') or (d or {}).get('_error')})", "data": d}
    events = {e.get("eventAction"): e.get("eventDate") for e in d.get("events") or [] if isinstance(e, dict)}
    ents = []
    for e in d.get("entities") or []:
        roles = ",".join(e.get("roles") or [])
        name = None
        for item in ((e.get("vcardArray") or [None, []])[1] or []):
            if isinstance(item, list) and item and item[0] in ("fn", "org"):
                name = item[3] if len(item) > 3 else None
        ents.append({"roles": roles, "name": name, "handle": e.get("handle")})
    lines = [f"**RDAP — {t}**"]
    if is_ip:
        lines += [f"- Network: {d.get('name')} ({d.get('handle')}) · {d.get('startAddress')} – {d.get('endAddress')}",
                  f"- Country: {d.get('country')}"]
    else:
        lines += [f"- Status: {', '.join(d.get('status') or [])}",
                  f"- Registered: {events.get('registration', '?')} · Expires: {events.get('expiration', '?')} · Changed: {events.get('last changed', '?')}",
                  f"- Nameservers: {', '.join((n.get('ldhName') or '') for n in d.get('nameservers') or [])}"]
    for e in ents[:6]:
        if e["name"] and "redact" not in str(e["name"]).lower():
            lines.append(f"- {e['roles'] or 'entity'}: {e['name']}")
    return {"output": "\n".join(lines), "data": {"events": events, "entities": ents, "status": d.get("status"),
                                                  "raw_handle": d.get("handle")}}


async def subdomains(client, domain: str) -> Dict[str, Any]:
    domain = _domain_of(domain)
    d = await _get(client, f"https://crt.sh/?q=%25.{quote(domain)}&output=json", timeout=30)
    names = {}
    if isinstance(d, list):
        for row in d:
            for n in str(row.get("name_value", "")).split("\n"):
                n = n.strip().lower().lstrip("*.")
                if n.endswith(domain):
                    first = row.get("not_before", "")[:10]
                    names[n] = min(names.get(n, first) or first, first) if names.get(n) else first
    else:
        d2 = await _get(client, f"https://api.certspotter.com/v1/issuances?domain={quote(domain)}"
                                f"&include_subdomains=true&expand=dns_names", timeout=20)
        if isinstance(d2, list):
            for row in d2:
                for n in row.get("dns_names") or []:
                    n = n.lower().lstrip("*.")
                    if n.endswith(domain):
                        names.setdefault(n, str(row.get("not_before", ""))[:10])
    subs = sorted(names)
    interesting = [s for s in subs if re.search(r"(dev|stag|test|admin|vpn|internal|api|jira|git|jenkins|old|beta|mail|portal|sso|grafana|kibana)", s)]
    lines = [f"**Subdomains (certificate transparency) — {domain}: {len(subs)} found**"]
    if interesting:
        lines.append("- Notable: " + ", ".join(interesting[:25]))
    lines.append("- " + ", ".join(subs[:60]) + (" …" if len(subs) > 60 else ""))
    return {"output": "\n".join(lines), "data": {"subdomains": subs, "first_seen": names, "notable": interesting}}


_SOCIAL_RE = re.compile(r"https?://(?:www\.)?(twitter\.com|x\.com|linkedin\.com/(?:company|in)|facebook\.com|instagram\.com|"
                        r"github\.com|youtube\.com/(?:@|c/|channel/|user/)|tiktok\.com/@|t\.me|mastodon\.[a-z]+|bsky\.app/profile)"
                        r"/?[A-Za-z0-9_./@-]{0,80}", re.I)
_MAIL_IN_PAGE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SEC_HEADERS = ["strict-transport-security", "content-security-policy", "x-frame-options",
                "x-content-type-options", "referrer-policy", "permissions-policy"]
_TECH = [("WordPress", r"wp-content|wp-includes"), ("Shopify", r"cdn\.shopify|shopify"), ("Wix", r"wixstatic|wix\.com"),
         ("Squarespace", r"squarespace"), ("Next.js", r"/_next/|__NEXT_DATA__"), ("Nuxt", r"__NUXT__|/_nuxt/"),
         ("React", r"react(-dom)?(\.production)?\.min\.js|data-reactroot"), ("Vue", r"vue(\.runtime)?\.min\.js|data-v-"),
         ("Angular", r"ng-version"), ("Drupal", r"drupal"), ("Joomla", r"joomla"), ("Ghost", r"ghost-"),
         ("Webflow", r"webflow"), ("Google Analytics", r"googletagmanager|google-analytics"), ("Meta Pixel", r"connect\.facebook\.net"),
         ("Hotjar", r"hotjar"), ("HubSpot", r"hs-scripts|hubspot"), ("Cloudflare", r"cloudflare"), ("jQuery", r"jquery"),
         ("Bootstrap", r"bootstrap(\.min)?\.(css|js)"), ("Tailwind", r"tailwind"), ("Stripe", r"js\.stripe\.com"),
         ("Intercom", r"intercom"), ("Zendesk", r"zendesk"), ("reCAPTCHA", r"recaptcha")]


async def http(client, url: str) -> Dict[str, Any]:
    if "://" not in url:
        url = "https://" + url
    try:
        r = await client.get(url, headers={"User-Agent": UA}, timeout=15)
    except Exception as e:
        return {"output": f"**HTTP — {url}**: unreachable ({type(e).__name__}: {str(e)[:120]})", "data": {"error": str(e)}}
    chain = [f"{h.status_code} {h.url}" for h in r.history] + [f"{r.status_code} {r.url}"]
    hdr = {k.lower(): v for k, v in r.headers.items()}
    body = r.text[:400000] if "html" in hdr.get("content-type", "") or not hdr.get("content-type") else ""
    title = (re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S) or [None, ""])[1].strip()[:160]
    desc = (re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', body, re.I) or [None, ""])[1][:200]
    gen = (re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']*)', body, re.I) or [None, ""])[1][:80]
    hay = body + " " + " ".join(f"{k}:{v}" for k, v in hdr.items())
    tech = [n for n, p in _TECH if re.search(p, hay, re.I)]
    if gen:
        tech = [gen] + [x for x in tech if not gen.lower().startswith(x.lower())]
    socials = sorted({m.group(0).rstrip("/.\"'") for m in _SOCIAL_RE.finditer(body)})[:25]
    emails = sorted({e for e in _MAIL_IN_PAGE.findall(body) if not re.search(r"\.(png|jpg|gif|svg|webp)$", e, re.I)})[:15]
    missing = [h for h in _SEC_HEADERS if h not in hdr]
    base = f"{urlparse(str(r.url)).scheme}://{urlparse(str(r.url)).netloc}"
    extras = {}
    for path in ("/robots.txt", "/.well-known/security.txt", "/sitemap.xml"):
        x = await _get(client, base + path, as_json=False, timeout=8)
        if hasattr(x, "status_code") and x.status_code == 200 and len(x.text) < 200000 and "<html" not in x.text[:200].lower():
            extras[path] = x.text[:1500]
    disallow = re.findall(r"(?im)^disallow:\s*(\S+)", extras.get("/robots.txt", ""))[:20]
    lines = [f"**HTTP — {url}** (active: this request reached the site)",
             f"- Chain: {' → '.join(chain)}",
             f"- Server: {hdr.get('server', '?')}{' · powered by ' + hdr['x-powered-by'] if hdr.get('x-powered-by') else ''}",
             f"- Title: {title or '—'}" + (f" · {desc}" if desc else ""),
             f"- Tech: {', '.join(dict.fromkeys(tech)) or 'not identified'}",
             f"- Missing security headers: {', '.join(missing) or 'none'}"]
    if socials:
        lines.append("- Linked profiles: " + ", ".join(socials))
    if emails:
        lines.append("- Public contact emails on the page: " + ", ".join(emails))
    if extras:
        lines.append("- Found: " + ", ".join(extras))
    if disallow:
        lines.append("- robots.txt hides: " + ", ".join(disallow))
    return {"output": "\n".join(lines), "data": {"chain": chain, "headers": hdr, "title": title, "tech": tech,
                                                  "socials": socials, "emails": emails, "missing_security_headers": missing,
                                                  "robots_disallow": disallow, "files": list(extras)}}


async def ip(client, addr: str) -> Dict[str, Any]:
    d = await _get(client, f"http://ip-api.com/json/{quote(addr)}?fields=status,message,country,regionName,city,"
                           f"isp,org,as,asname,reverse,proxy,hosting,mobile,query")
    ptr = ""
    try:
        ipobj = ipaddress.ip_address(addr)
        rname = ipobj.reverse_pointer
        r = await _get(client, f"https://cloudflare-dns.com/dns-query?name={rname}&type=PTR", accept="application/dns-json")
        ptr = ", ".join(a.get("data", "") for a in (r.get("Answer") or [])) if isinstance(r, dict) else ""
    except ValueError:
        pass
    if not isinstance(d, dict) or d.get("status") != "success":
        return {"output": f"**IP — {addr}**: lookup failed ({(d or {}).get('message') or (d or {}).get('_error')})", "data": d}
    flags = [n for n in ("proxy", "hosting", "mobile") if d.get(n)]
    lines = [f"**IP — {addr}**",
             f"- Location (approximate, network-level): {d.get('city')}, {d.get('regionName')}, {d.get('country')}",
             f"- Network: {d.get('isp')} · {d.get('org')} · {d.get('as')}",
             f"- Reverse DNS: {ptr or d.get('reverse') or '—'}" + (f" · flags: {', '.join(flags)}" if flags else "")]
    return {"output": "\n".join(lines), "data": {**d, "ptr": ptr}}


async def wayback(client, target: str) -> Dict[str, Any]:
    t = target.strip()
    rows = await _get(client, f"https://web.archive.org/cdx/search/cdx?url={quote(t)}&output=json"
                              f"&fl=timestamp,original,statuscode&collapse=timestamp:6&limit=500", timeout=25)
    caps = rows[1:] if isinstance(rows, list) and rows and isinstance(rows[0], list) else []
    if not caps:
        return {"output": f"**Wayback — {t}**: no captures found", "data": {"captures": 0}}
    fmt = lambda ts: f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
    first, last = caps[0][0], caps[-1][0]
    years = {}
    for c in caps:
        years[c[0][:4]] = years.get(c[0][:4], 0) + 1
    lines = [f"**Wayback Machine — {t}**",
             f"- Monthly captures: {len(caps)} · first {fmt(first)} · last {fmt(last)}",
             f"- By year: " + ", ".join(f"{y}: {n}" for y, n in sorted(years.items())),
             f"- Oldest snapshot: https://web.archive.org/web/{first}/{caps[0][1]}",
             f"- Latest snapshot: https://web.archive.org/web/{last}/{caps[-1][1]}"]
    return {"output": "\n".join(lines), "data": {"captures": len(caps), "first": first, "last": last, "by_year": years}}


_DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org",
               "yopmail.com", "trashmail.com", "getnada.com", "sharklasers.com", "dispostable.com", "maildrop.cc",
               "fakeinbox.com", "throwawaymail.com", "mintemail.com", "mohmal.com", "emailondeck.com"}
_FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com",
             "me.com", "proton.me", "protonmail.com", "aol.com", "gmx.com", "zoho.com", "yandex.com", "mail.com"}


async def email(client, addr: str) -> Dict[str, Any]:
    addr = addr.strip().lower()
    if not _EMAIL_RE.match(addr):
        return {"output": f"**Email — {addr}**: not a valid address", "data": {}}
    local, dom = addr.split("@", 1)
    dn = await dns(client, dom)
    md5 = hashlib.md5(addr.encode()).hexdigest()
    av = await _get(client, f"https://www.gravatar.com/avatar/{md5}?d=404&s=80", as_json=False, timeout=8)
    has_grav = hasattr(av, "status_code") and av.status_code == 200
    prof = await _get(client, f"https://en.gravatar.com/{md5}.json", timeout=8) if has_grav else {}
    entry = ((prof or {}).get("entry") or [{}])[0] if isinstance(prof, dict) else {}
    mx = dn["data"]["records"].get("MX", [])
    kind = "disposable" if dom in _DISPOSABLE else "free webmail" if dom in _FREEMAIL else "organisation / custom domain"
    lines = [f"**Email — {addr}**",
             f"- Domain {dom}: {kind}; mail {'accepted (MX present)' if mx else 'NOT configured (no MX)'}"
             + (f" via {', '.join(dn['data']['mail_providers'])}" if dn["data"]["mail_providers"] else ""),
             f"- Gravatar: {'yes — ' + (entry.get('displayName') or entry.get('preferredUsername') or 'profile exists') if has_grav else 'none'}"]
    for acc in (entry.get("accounts") or [])[:8]:
        lines.append(f"  - linked: {acc.get('shortname')} {acc.get('url')}")
    lines.append(f"- Pivots: username “{local}” (recon username), domain {dom} (recon auto), "
                 f"breach exposure via haveibeenpwned.com (manual / API key)")
    return {"output": "\n".join(lines), "data": {"domain": dom, "kind": kind, "mx": mx, "gravatar": has_grav,
                                                  "gravatar_profile": entry, "local_part": local}}


# Platforms with stable, public, keyless lookups. (url to show, probe url, how to read it)
_USER_SITES = [
    ("GitHub", "https://github.com/{u}", "https://api.github.com/users/{u}", "json"),
    ("GitLab", "https://gitlab.com/{u}", "https://gitlab.com/api/v4/users?username={u}", "list"),
    ("Reddit", "https://reddit.com/user/{u}", "https://www.reddit.com/user/{u}/about.json", "json"),
    ("Hacker News", "https://news.ycombinator.com/user?id={u}", "https://hn.algolia.com/api/v1/users/{u}", "json"),
    ("Keybase", "https://keybase.io/{u}", "https://keybase.io/_/api/1.0/user/lookup.json?usernames={u}", "keybase"),
    ("Dev.to", "https://dev.to/{u}", "https://dev.to/api/users/by_username?url={u}", "json"),
    ("Docker Hub", "https://hub.docker.com/u/{u}", "https://hub.docker.com/v2/users/{u}/", "json"),
    ("PyPI", "https://pypi.org/user/{u}/", "https://pypi.org/user/{u}/", "status"),
    ("npm", "https://www.npmjs.com/~{u}", "https://www.npmjs.com/~{u}", "status"),
    ("Codeberg", "https://codeberg.org/{u}", "https://codeberg.org/api/v1/users/{u}", "json"),
    ("Chess.com", "https://www.chess.com/member/{u}", "https://api.chess.com/pub/player/{u}", "json"),
    ("Lichess", "https://lichess.org/@/{u}", "https://lichess.org/api/user/{u}", "json"),
    ("Bluesky", "https://bsky.app/profile/{u}.bsky.social", "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={u}.bsky.social", "json"),
    ("Gravatar", "https://gravatar.com/{u}", "https://en.gravatar.com/{u}.json", "json"),
    ("Wikipedia", "https://en.wikipedia.org/wiki/User:{u}", "https://en.wikipedia.org/w/api.php?action=query&list=users&ususers={u}&format=json", "wiki"),
]


async def username(client, name: str) -> Dict[str, Any]:
    u = name.strip().lstrip("@")
    if not re.match(r"^[A-Za-z0-9_.-]{1,40}$", u):
        return {"output": f"**Username — {name}**: invalid handle", "data": {}}

    async def probe(site, show, api, kind):
        url = api.format(u=quote(u))
        if kind == "status":
            r = await _get(client, url, as_json=False, timeout=10)
            ok = hasattr(r, "status_code") and r.status_code == 200
            return site, show.format(u=u), ok, None
        d = await _get(client, url, timeout=10)
        if kind == "list":
            ok = isinstance(d, list) and len(d) > 0
        elif kind == "keybase":
            ok = isinstance(d, dict) and any(d.get("them") or [])
        elif kind == "wiki":
            users = (((d or {}).get("query") or {}).get("users") or [{}]) if isinstance(d, dict) else [{}]
            ok = bool(users) and "missing" not in users[0] and "invalid" not in users[0]
        else:
            ok = isinstance(d, dict) and not d.get("_status") and not d.get("_error") and not d.get("error")
            if site == "Reddit" and ok:
                ok = bool((d.get("data") or {}).get("name"))
        err = d.get("_error") if isinstance(d, dict) else None
        return site, show.format(u=u), ok, err

    res = await asyncio.gather(*[probe(*s) for s in _USER_SITES])
    found = [(s, url) for s, url, ok, _ in res if ok]
    unsure = [s for s, _, ok, err in res if not ok and err]
    lines = [f"**Username “{u}” — found on {len(found)}/{len(_USER_SITES)} checked platforms**"]
    lines += [f"- {s}: {url}" for s, url in found]
    if unsure:
        lines.append(f"- Could not check: {', '.join(unsure)}")
    lines.append("- Same handle ≠ same person: confirm with matching name, avatar, bio, links or writing style. "
                 "For 400+ more sites run sherlock_osint.")
    return {"output": "\n".join(lines), "data": {"found": dict(found), "unchecked": unsure}}


async def github(client, name: str) -> Dict[str, Any]:
    u = name.strip().lstrip("@").split("/")[-1]
    p = await _get(client, f"https://api.github.com/users/{quote(u)}", accept="application/vnd.github+json")
    if not isinstance(p, dict) or p.get("_status") or p.get("_error"):
        return {"output": f"**GitHub — {u}**: not found ({(p or {}).get('_status') or (p or {}).get('_error')})", "data": p}
    repos, orgs = await asyncio.gather(
        _get(client, f"https://api.github.com/users/{quote(u)}/repos?sort=updated&per_page=100"),
        _get(client, f"https://api.github.com/users/{quote(u)}/orgs"))
    repos = repos if isinstance(repos, list) else []
    top = sorted(repos, key=lambda r: -(r.get("stargazers_count") or 0))[:8]
    langs = {}
    for r in repos:
        if r.get("language"):
            langs[r["language"]] = langs.get(r["language"], 0) + 1
    lines = [f"**GitHub — {p.get('login')}** ({p.get('type')}) · {p.get('html_url')}",
             f"- Name: {p.get('name') or '—'} · Company: {p.get('company') or '—'} · Location: {p.get('location') or '—'}",
             f"- Bio: {(p.get('bio') or '—')[:200]}",
             f"- Blog: {p.get('blog') or '—'} · X/Twitter: {p.get('twitter_username') or '—'}",
             f"- Since {str(p.get('created_at'))[:10]} · {p.get('public_repos')} repos · {p.get('followers')} followers",
             f"- Languages: " + ", ".join(f"{k} ({v})" for k, v in sorted(langs.items(), key=lambda kv: -kv[1])[:8])]
    if isinstance(orgs, list) and orgs:
        lines.append("- Orgs: " + ", ".join(o.get("login", "") for o in orgs[:15]))
    for r in top:
        lines.append(f"  - ★{r.get('stargazers_count')} {r.get('full_name')} — {(r.get('description') or '')[:90]}")
    return {"output": "\n".join(lines), "data": {"profile": p, "top_repos": [r.get("full_name") for r in top],
                                                  "languages": langs, "orgs": [o.get("login") for o in orgs] if isinstance(orgs, list) else []}}


async def reddit(client, name: str) -> Dict[str, Any]:
    u = name.strip().lstrip("@").replace("u/", "").replace("/", "")
    about, posts, comments = await asyncio.gather(
        _get(client, f"https://www.reddit.com/user/{quote(u)}/about.json"),
        _get(client, f"https://www.reddit.com/user/{quote(u)}/submitted.json?limit=50"),
        _get(client, f"https://www.reddit.com/user/{quote(u)}/comments.json?limit=100"))
    d = (about or {}).get("data") if isinstance(about, dict) else None
    if not d:
        return {"output": f"**Reddit — u/{u}**: not found or private", "data": about}
    subs = {}
    for src in (posts, comments):
        for ch in ((src or {}).get("data") or {}).get("children") or [] if isinstance(src, dict) else []:
            s = (ch.get("data") or {}).get("subreddit")
            if s:
                subs[s] = subs.get(s, 0) + 1
    created = time.strftime("%Y-%m-%d", time.gmtime(d.get("created_utc") or 0))
    lines = [f"**Reddit — u/{u}** · https://reddit.com/user/{u}",
             f"- Since {created} · karma {d.get('link_karma', 0)} post / {d.get('comment_karma', 0)} comment",
             "- Most active in: " + ", ".join(f"r/{k} ({v})" for k, v in sorted(subs.items(), key=lambda kv: -kv[1])[:12])]
    return {"output": "\n".join(lines), "data": {"about": {k: d.get(k) for k in ("name", "created_utc", "link_karma", "comment_karma")},
                                                  "subreddits": subs}}


async def hackernews(client, name: str) -> Dict[str, Any]:
    u = name.strip().lstrip("@")
    d, items = await asyncio.gather(
        _get(client, f"https://hn.algolia.com/api/v1/users/{quote(u)}"),
        _get(client, f"https://hn.algolia.com/api/v1/search_by_date?tags=author_{quote(u)}&hitsPerPage=20"))
    if not isinstance(d, dict) or d.get("_status") or d.get("_error"):
        return {"output": f"**Hacker News — {u}**: not found", "data": d}
    hits = (items or {}).get("hits") or [] if isinstance(items, dict) else []
    lines = [f"**Hacker News — {u}** · https://news.ycombinator.com/user?id={u}",
             f"- Karma {d.get('karma')} · since {str(d.get('created_at'))[:10]}",
             f"- About: {re.sub('<[^>]+>', ' ', d.get('about') or '—')[:300]}"]
    for h in hits[:8]:
        lines.append(f"  - {str(h.get('created_at'))[:10]} {(h.get('title') or h.get('comment_text') or '')[:100]}")
    return {"output": "\n".join(lines), "data": {"karma": d.get("karma"), "about": d.get("about"), "recent": len(hits)}}


_CC = {"1": "US/Canada", "7": "Russia/Kazakhstan", "20": "Egypt", "27": "South Africa", "30": "Greece", "31": "Netherlands",
       "32": "Belgium", "33": "France", "34": "Spain", "36": "Hungary", "39": "Italy", "40": "Romania", "41": "Switzerland",
       "43": "Austria", "44": "United Kingdom", "45": "Denmark", "46": "Sweden", "47": "Norway", "48": "Poland",
       "49": "Germany", "51": "Peru", "52": "Mexico", "53": "Cuba", "54": "Argentina", "55": "Brazil", "56": "Chile",
       "57": "Colombia", "58": "Venezuela", "60": "Malaysia", "61": "Australia", "62": "Indonesia", "63": "Philippines",
       "64": "New Zealand", "65": "Singapore", "66": "Thailand", "81": "Japan", "82": "South Korea", "84": "Vietnam",
       "86": "China", "90": "Turkey", "91": "India", "92": "Pakistan", "98": "Iran", "234": "Nigeria", "254": "Kenya",
       "351": "Portugal", "353": "Ireland", "358": "Finland", "380": "Ukraine", "502": "Guatemala", "503": "El Salvador",
       "504": "Honduras", "505": "Nicaragua", "506": "Costa Rica", "507": "Panama", "591": "Bolivia", "593": "Ecuador",
       "595": "Paraguay", "598": "Uruguay", "852": "Hong Kong", "886": "Taiwan", "961": "Lebanon", "966": "Saudi Arabia",
       "971": "UAE", "972": "Israel"}


async def phone(client, number: str) -> Dict[str, Any]:
    digits = re.sub(r"\D", "", number)
    intl = number.strip().startswith("+") or number.strip().startswith("00")
    if number.strip().startswith("00"):
        digits = digits[2:]
    cc, country = None, None
    if intl or len(digits) > 10:
        for n in (3, 2, 1):
            if digits[:n] in _CC:
                cc, country = digits[:n], _CC[digits[:n]]
                break
    elif len(digits) == 8:
        cc, country = "506", "Costa Rica (assumed from 8-digit format)"
        digits = "506" + digits
    nat = digits[len(cc):] if cc else digits
    kind = "unknown"
    if cc == "506" and len(nat) == 8:
        kind = {"2": "landline", "4": "landline / VoIP", "5": "mobile (5xxx)", "6": "mobile", "7": "mobile", "8": "mobile",
                "9": "special / toll"}.get(nat[0], "unknown")
    e164 = "+" + digits if cc else number
    pretty = f"+{cc} {nat[:4]}-{nat[4:]}" if cc == "506" and len(nat) == 8 else e164
    lines = [f"**Phone — {number}**",
             f"- E.164: {e164} · Country: {country or 'unknown (add the + country code)'} · Type: {kind}",
             f"- Search pivots: \"{pretty}\" OR \"{nat}\" OR \"{e164}\" (web, classifieds, business listings)",
             "- Business listing check: search the number on Google Maps / OpenStreetMap for registered businesses.",
             "- Messaging presence (WhatsApp/Telegram) can only be checked from a phone — do it only with consent."]
    return {"output": "\n".join(lines), "data": {"e164": e164, "country": country, "type": kind, "national": nat}}


async def geocode(client, query: str) -> Dict[str, Any]:
    m = _COORD_RE.match(query or "")
    if m:
        lat, lon = m.group(1), m.group(3)
        d = await _get(client, f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lon}&zoom=16")
        if not isinstance(d, dict) or d.get("_error"):
            return {"output": f"**Reverse geocode {lat},{lon}**: failed", "data": d}
        return {"output": f"**Reverse geocode {lat},{lon}**\n- {d.get('display_name')}\n- Map: https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}",
                "data": {"display_name": d.get("display_name"), "address": d.get("address")}}
    d = await _get(client, f"https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q={quote(query)}")
    rows = d if isinstance(d, list) else []
    lines = [f"**Place search — {query}: {len(rows)} result(s)**"]
    for r in rows:
        lines.append(f"- {r.get('display_name')} ({r.get('type')}) · {r.get('lat')},{r.get('lon')} · "
                     f"https://www.openstreetmap.org/?mlat={r.get('lat')}&mlon={r.get('lon')}#map=17/{r.get('lat')}/{r.get('lon')}")
    return {"output": "\n".join(lines), "data": {"results": [{k: r.get(k) for k in ("display_name", "lat", "lon", "type")} for r in rows]}}


def _exif_from_bytes(b: bytes) -> Dict[str, Any]:
    from io import BytesIO
    from PIL import Image, ExifTags
    img = Image.open(BytesIO(b))
    info = {"format": img.format, "size": f"{img.width}x{img.height}", "mode": img.mode}
    ex = img.getexif()
    tags = {}
    for k, v in ex.items():
        name = ExifTags.TAGS.get(k, str(k))
        if isinstance(v, bytes):
            continue
        tags[name] = str(v)[:120]
    gps = {}
    try:
        g = ex.get_ifd(0x8825)
        for k, v in g.items():
            gps[ExifTags.GPSTAGS.get(k, str(k))] = v
    except Exception:
        pass
    try:
        sub = ex.get_ifd(0x8769)
        for k, v in sub.items():
            name = ExifTags.TAGS.get(k, str(k))
            if name in ("DateTimeOriginal", "LensModel", "FocalLength", "ExposureTime", "ISOSpeedRatings", "BodySerialNumber"):
                tags[name] = str(v)[:80]
    except Exception:
        pass

    def deg(vals, ref):
        try:
            d, m, s = [float(x) for x in vals]
            out = d + m / 60 + s / 3600
            return -out if ref in ("S", "W") else out
        except Exception:
            return None
    lat = deg(gps.get("GPSLatitude", ()), gps.get("GPSLatitudeRef")) if gps.get("GPSLatitude") else None
    lon = deg(gps.get("GPSLongitude", ()), gps.get("GPSLongitudeRef")) if gps.get("GPSLongitude") else None
    return {"image": info, "exif": tags, "gps": {"lat": lat, "lon": lon} if lat is not None and lon is not None else None}


async def exif(client, source: str, owner=None, session_id=None) -> Dict[str, Any]:
    src = source.strip()
    try:
        if src.lower().startswith(("http://", "https://")):
            r = await client.get(src, timeout=20)
            if r.status_code != 200:
                return {"output": f"**EXIF**: download failed ({r.status_code})", "data": {}}
            b = r.content[:20_000_000]
        else:
            from src.tool_execution import _resolve_tool_path
            p = _resolve_tool_path(src, owner, session_id)
            with open(p, "rb") as f:
                b = f.read(20_000_000)
        d = await asyncio.to_thread(_exif_from_bytes, b)
    except Exception as e:
        return {"output": f"**EXIF — {src}**: could not read ({type(e).__name__}: {str(e)[:120]})", "data": {}}
    ex = d["exif"]
    lines = [f"**Image metadata — {src[:80]}**", f"- {d['image']['format']} {d['image']['size']}"]
    for k in ("Make", "Model", "LensModel", "Software", "DateTime", "DateTimeOriginal", "Artist", "Copyright", "BodySerialNumber"):
        if ex.get(k):
            lines.append(f"- {k}: {ex[k]}")
    if d["gps"]:
        la, lo = d["gps"]["lat"], d["gps"]["lon"]
        lines.append(f"- GPS: {la:.6f}, {lo:.6f} · https://www.openstreetmap.org/?mlat={la}&mlon={lo}#map=17/{la}/{lo}")
    if len(lines) == 2:
        lines.append("- No EXIF metadata (stripped — typical for images re-uploaded to social networks).")
    return {"output": "\n".join(lines), "data": d}


def reverse_image(url: str) -> Dict[str, Any]:
    u = quote(url.strip(), safe="")
    links = {
        "Google Lens": f"https://lens.google.com/uploadbyurl?url={u}",
        "Bing Visual Search": f"https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{u}",
        "Yandex Images": f"https://yandex.com/images/search?rpt=imageview&url={u}",
        "TinEye": f"https://tineye.com/search?url={u}",
    }
    return {"output": "**Reverse image search** (open each — engines differ a lot; Yandex is strongest for faces/places, "
                      "TinEye for oldest copy)\n" + "\n".join(f"- {k}: {v}" for k, v in links.items()),
            "data": links}


def dork_queries(target: str, kind: Optional[str] = None) -> List[str]:
    t = target.strip()
    kind = kind or detect(t)
    if kind == "username" and not t.startswith("@"):
        kind = "text"            # a bare word is as likely an organisation or a name
    if kind == "ip":
        return [f"\"{t}\"", f"\"{t}\" (abuse OR spam OR malware OR botnet)", f"site:github.com \"{t}\"",
                f"site:pastebin.com \"{t}\""]
    if kind in ("domain", "url"):
        d = _domain_of(t)
        return [f"site:{d}", f"site:{d} filetype:pdf", f"site:{d} (filetype:xls OR filetype:xlsx OR filetype:csv)",
                f"site:{d} (filetype:doc OR filetype:docx OR filetype:ppt)", f"site:{d} inurl:admin OR inurl:login",
                f"\"{d}\" -site:{d}", f"site:github.com \"{d}\"", f"site:pastebin.com \"{d}\"",
                f"site:linkedin.com/company \"{d.split('.')[0]}\"", f"site:{d} intitle:\"index of\""]
    if kind == "email":
        return [f"\"{t}\"", f"\"{t}\" filetype:pdf", f"site:github.com \"{t}\"", f"site:pastebin.com \"{t}\"",
                f"\"{t.split('@')[0]}\" \"{t.split('@')[1]}\""]
    if kind == "username":
        u = t.lstrip("@")
        return [f"\"{u}\"", f"\"@{u}\"", f"inurl:{u} (site:twitter.com OR site:x.com OR site:instagram.com OR site:tiktok.com)",
                f"site:github.com \"{u}\"", f"\"{u}\" (forum OR profile OR member)"]
    if kind == "phone":
        digits = re.sub(r"\D", "", t)
        return [f"\"{t}\"", f"\"{digits}\"", f"\"{digits[-8:]}\"" if len(digits) >= 8 else f"\"{digits}\""]
    # people / organisations / free text
    return [f"\"{t}\"", f"\"{t}\" filetype:pdf", f"\"{t}\" site:linkedin.com", f"\"{t}\" (CEO OR director OR fundador OR gerente)",
            f"\"{t}\" (site:gob.go.cr OR site:go.cr OR site:gov)", f"\"{t}\" (news OR noticias)",
            f"\"{t}\" (lawsuit OR demanda OR sentencia OR expediente)"]


async def dorks(client, target: str, run: bool = False, kind: Optional[str] = None) -> Dict[str, Any]:
    qs = dork_queries(target, kind)
    lines = [f"**Search dorks — {target}**"] + [f"- `{q}`" for q in qs]
    data = {"queries": qs}
    if run:
        try:
            from src.search import comprehensive_web_search
            async def one(q):
                try:
                    ctx, src = await asyncio.wait_for(asyncio.to_thread(comprehensive_web_search, q, return_sources=True), 25)
                    return q, src or []
                except Exception:
                    return q, []
            res = await asyncio.gather(*[one(q) for q in qs[:5]])
            data["results"] = {}
            lines.append("\n**Top hits**")
            for q, src in res:
                data["results"][q] = src[:5]
                for s in (src or [])[:3]:
                    if isinstance(s, dict):
                        lines.append(f"- [{(s.get('title') or s.get('url') or '')[:80]}]({s.get('url')}) ← `{q}`")
        except Exception as e:
            lines.append(f"(running the dorks failed: {e})")
    return {"output": "\n".join(lines), "data": data}


async def crypto(client, addr: str) -> Dict[str, Any]:
    a = addr.strip()
    if _ETH_RE.match(a):
        d = await _get(client, f"https://eth.blockscout.com/api/v2/addresses/{a}")
        c = await _get(client, f"https://eth.blockscout.com/api/v2/addresses/{a}/counters")
        if not isinstance(d, dict) or d.get("_error") or d.get("_status"):
            return {"output": f"**Ethereum — {a}**: lookup failed", "data": d}
        bal = int(d.get("coin_balance") or 0) / 1e18
        lines = [f"**Ethereum — {a}**", f"- Balance: {bal:.6f} ETH · contract: {bool(d.get('is_contract'))}"
                 + (f" · name: {d.get('name')}" if d.get("name") else ""),
                 f"- Transactions: {(c or {}).get('transactions_count', '?')} · token transfers: {(c or {}).get('token_transfers_count', '?')}",
                 f"- Explorer: https://eth.blockscout.com/address/{a}"]
        if d.get("ens_domain_name"):
            lines.append(f"- ENS: {d['ens_domain_name']}")
        return {"output": "\n".join(lines), "data": {"balance_eth": bal, "counters": c, "ens": d.get("ens_domain_name")}}
    d = await _get(client, f"https://blockstream.info/api/address/{a}")
    if not isinstance(d, dict) or d.get("_error") or d.get("_status"):
        return {"output": f"**Bitcoin — {a}**: lookup failed", "data": d}
    cs = d.get("chain_stats") or {}
    recv = cs.get("funded_txo_sum", 0) / 1e8
    sent = cs.get("spent_txo_sum", 0) / 1e8
    lines = [f"**Bitcoin — {a}**", f"- Received {recv:.8f} BTC · sent {sent:.8f} · balance {recv - sent:.8f}",
             f"- Transactions: {cs.get('tx_count', 0)} · Explorer: https://blockstream.info/address/{a}"]
    return {"output": "\n".join(lines), "data": {"received_btc": recv, "sent_btc": sent, "tx_count": cs.get("tx_count")}}


_FW_FOR = {"domain": ["Domain Name"], "url": ["Domain Name", "Archives"], "ip": ["IP & MAC Address"],
           "email": ["Email Address"], "username": ["Username", "Social Networks"], "phone": ["Telephone Numbers"],
           "btc": ["Blockchain & Cryptocurrency"], "eth": ["Blockchain & Cryptocurrency"],
           "coords": ["Geolocation Tools / Maps"], "image": ["Images / Videos / Docs"], "company": ["Business Records"],
           "text": ["Search Engines", "Public Records"]}


def framework(topic: str, limit: int = 12) -> Dict[str, Any]:
    """Best live, free, passive tools from the bundled OSINT Framework tree for
    a target type ("domain", "email", …) or a category / keyword."""
    try:
        from src.osint_framework import load_tree
        tree = load_tree()
    except Exception as e:
        return {"output": f"OSINT Framework unavailable: {e}", "data": {}}
    cats = _FW_FOR.get((topic or "").lower())
    roots = []
    for c in tree.get("children", []):
        if cats and c.get("name") in cats:
            roots.append(c)
    leaves = []

    def walk(n, path):
        if n.get("type") == "url":
            leaves.append((path, n))
            return
        for ch in n.get("children") or []:
            walk(ch, path + [n.get("name")])
    if roots:
        for r in roots:
            walk(r, [])
    else:
        kw = (topic or "").lower()
        walk(tree, [])
        leaves = [(p, n) for p, n in leaves if kw in (" ".join(p) + " " + n.get("name", "") + " " + str(n.get("description", ""))).lower()]

    def score(n):
        s = 0
        s += 3 if n.get("status") == "live" else -5
        s += 2 if n.get("pricing") == "free" else 0
        s += 1 if n.get("opsec") == "passive" else 0
        s -= 2 if n.get("registration") else 0
        s -= 9 if n.get("deprecated") else 0
        return s
    leaves.sort(key=lambda pn: -score(pn[1]))
    top = leaves[:limit]
    lines = [f"**OSINT Framework — best tools for “{topic}”** ({len(leaves)} in the catalogue)"]
    for p, n in top:
        lines.append(f"- [{n.get('name')}]({n.get('url')}) — {(n.get('bestFor') or n.get('description') or '')[:110]}"
                     f" · {n.get('pricing', '?')}, {n.get('opsec', '?')}{' · registration' if n.get('registration') else ''}"
                     f"  ({' › '.join(x for x in p[1:] if x)})")
    return {"output": "\n".join(lines), "data": {"tools": [{"name": n.get("name"), "url": n.get("url"), "path": p} for p, n in top]}}


# ── auto: run everything that fits the target ───────────────────────────────

async def auto(client, target: str, owner=None, session_id=None) -> Dict[str, Any]:
    kind = detect(target)
    t = target.strip()
    jobs = {}
    if kind in ("domain", "url"):
        d = _domain_of(t)
        jobs = {"dns": dns(client, d), "whois": whois(client, d), "subdomains": subdomains(client, d),
                "http": http(client, t if kind == "url" else d), "wayback": wayback(client, d)}
    elif kind == "ip":
        jobs = {"ip": ip(client, t), "whois": whois(client, t)}
    elif kind == "email":
        jobs = {"email": email(client, t), "username": username(client, t.split("@")[0])}
    elif kind == "username":
        u = t.lstrip("@")
        jobs = {"username": username(client, u), "github": github(client, u), "reddit": reddit(client, u),
                "hackernews": hackernews(client, u)}
    elif kind == "phone":
        jobs = {"phone": phone(client, t)}
    elif kind in ("btc", "eth"):
        jobs = {"crypto": crypto(client, t)}
    elif kind == "coords":
        jobs = {"geocode": geocode(client, t)}
    else:
        # Names, organisations, topics: run the search dorks for real.
        jobs = {"dorks": dorks(client, t, run=True)}
    names = list(jobs)
    results = await asyncio.gather(*jobs.values(), return_exceptions=True) if jobs else []
    parts, data = [f"## Recon · {t}  _(detected: {kind})_"], {"kind": kind}
    for n, r in zip(names, results):
        if isinstance(r, Exception):
            parts.append(f"**{n}**: failed ({type(r).__name__}: {str(r)[:120]})")
            continue
        # Only show modules that found something for usernames (keeps it scannable).
        parts.append(r["output"])
        data[n] = r.get("data")
    dk = dork_queries(t, kind)
    parts.append("**Next pivots (search dorks):** " + " · ".join(f"`{q}`" for q in dk[:6]))
    fw = framework(kind if kind in _FW_FOR else "text", limit=6)
    parts.append(fw["output"])
    data["framework"] = fw["data"]
    return {"output": "\n\n".join(parts), "data": data}


ACTIONS = ["auto", "dns", "whois", "subdomains", "http", "ip", "wayback", "email", "username", "github", "reddit",
           "hackernews", "phone", "geocode", "exif", "reverse_image", "dorks", "crypto", "framework"]


async def run(action: str, target: str, *, owner=None, session_id=None, run_dorks: bool = False,
              purpose: str = "") -> Dict[str, Any]:
    action = (action or "auto").strip().lower().replace("-", "_")
    target = (target or "").strip()
    if action not in ACTIONS:
        return {"error": f"Unknown recon action {action!r}. Use one of: {', '.join(ACTIONS)}", "exit_code": 1}
    if not target and action != "framework":
        return {"error": "recon needs a target (domain, URL, IP, email, @username, phone, wallet, image URL, place…)",
                "exit_code": 1}
    blocked = _policy_block(target, purpose)
    if blocked:
        return {"error": blocked, "exit_code": 1, "policy": "doxxing"}
    t0 = time.time()
    try:
        if action == "framework":
            res = framework(target or "domain")
        elif action == "reverse_image":
            res = reverse_image(target)
        else:
            async with _client() as client:
                fn = {"auto": auto, "dns": dns, "whois": whois, "subdomains": subdomains, "http": http, "ip": ip,
                      "wayback": wayback, "email": email, "username": username, "github": github, "reddit": reddit,
                      "hackernews": hackernews, "phone": phone, "geocode": geocode, "exif": exif, "dorks": dorks,
                      "crypto": crypto}[action]
                if action in ("auto", "exif"):
                    res = await fn(client, target, owner=owner, session_id=session_id)
                elif action == "dorks":
                    res = await dorks(client, target, run=run_dorks)
                else:
                    res = await fn(client, target)
    except Exception as e:
        logger.warning("[recon] %s %s failed: %s", action, target, e)
        return {"error": f"recon {action} failed: {type(e).__name__}: {e}", "exit_code": 1}
    out = res.get("output", "")
    if len(out) > 9000:
        out = out[:9000] + "\n… (truncated)"
    return {"output": out + f"\n\n_recon {action} · {time.time() - t0:.1f}s · sources: public, keyless_",
            "exit_code": 0, "recon": {"action": action, "target": target, "data": res.get("data")}}


# ── the manifest the agent always sees ──────────────────────────────────────

CAPABILITIES = [
    ("Instant recon", "recon auto <anything> — detects domain / URL / IP / email / @username / phone / wallet / coordinates and runs every relevant module in parallel"),
    ("Domains & infrastructure", "DNS + SPF/DMARC + mail provider, RDAP whois, certificate-transparency subdomains, live HTTP fingerprint (tech stack, security headers, contact emails, social links, robots/security.txt)"),
    ("Networks", "IP geolocation (network-level), ASN/ISP, reverse DNS, RDAP network owner"),
    ("History", "Wayback Machine capture timeline + oldest/latest snapshots"),
    ("People & handles (public footprint)", "username presence on 15 platforms via official APIs (+ Sherlock for 400+), GitHub/Reddit/Hacker News profiles, email → Gravatar/MX/provider"),
    ("Phones & places", "phone normalisation/country/type, OpenStreetMap place search & reverse geocoding"),
    ("Media forensics", "image EXIF incl. camera + GPS, reverse-image search across Lens/Bing/Yandex/TinEye"),
    ("Money trails", "Bitcoin & Ethereum address activity (balances, tx counts, ENS)"),
    ("Search tradecraft", "auto-generated search dorks per target (files, leaks, mentions, LinkedIn), optionally executed"),
    ("Fact-checking & anomaly radar", "factcheck — Doble Check method: verifiable atoms, red-flag signals, the 10 vices (moving figures, forced equivalence, cropped timelines, ghost sources…), primary repositories, cited-source check, cross-source anomaly scan, recalculation, explicit verdicts"),
    ("1,166-tool OSINT Framework", "best live/free/passive tools for any category, ranked"),
    ("Organised intelligence", "case files (entities, findings, links, next steps), watchlists that alert on change, Costa Rica registries, document grep (docfind)"),
]


def manifest() -> str:
    try:
        from src.factcheck import INVESTIGATION_PROTOCOL as _proto
    except Exception:
        _proto = ""
    return _proto + ("## Superpowers (OSINT, always on)\n"
            "You have a `recon` tool (actions: " + ", ".join(ACTIONS) + "). Use it proactively whenever a request "
            "involves a domain, website, IP, email, handle, phone, wallet, image or place — start with "
            "`recon auto`, then pivot. Record durable findings in a case file.\n"
            + "\n".join(f"- **{n}**: {d}" for n, d in CAPABILITIES)
            + "\nWhen the user asks what you can do, show these superpowers concisely.\n"
            "Policy: " + POLICY + "\n\n")
