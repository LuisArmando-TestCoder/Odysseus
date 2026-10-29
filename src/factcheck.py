# src/factcheck.py
"""Fact-checking & anomaly-detection superpower.

Built on the working method of Doble Check (Universidad de Costa Rica, IFCN
signatory) as analysed in the user's 2026-10-29 study: claims are dismantled
by comparing each sentence against a dated, verifiable PRIMARY document — not
by intuition. The tool turns that method into something the agent runs:

  claim      analyse a claim: verifiable atoms (figures, dates, absolutes,
             comparisons, attributions), red-flag SIGNALS, candidate VICES,
             the six QUESTION types, the primary repositories that would hold
             the proof, a 10-step plan; optionally checks whether the claim's
             own figures appear in the source it cites, and searches the
             primary repositories (run=true)
  anomalies  cross-read several texts/sources: the same thing reported with
             different numbers ("moving figures"), outliers vs the median,
             dates out of order, absolutes contradicted elsewhere — leads, not
             verdicts
  recalc     recompute a figure: gap, ratio, % difference, currency conversion
  method     the full method (mechanics, questions, vices, signals, verdict
             scale, blind spots) for the agent to follow

The agent supplies the judgement; this module supplies structure, detectors
and retrieval so the judgement is anchored in documents.
"""

import asyncio
import json
import re
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple

VERDICTS = ["Verdadero", "Verdad a medias", "Engañoso", "Falso", "No verificable (aún)"]
OUT_OF_SCOPE = "Not fact-checkable as such: opinions, promises/forecasts, religious belief, sports/entertainment taste, matters sub judice."

# ── The method (Doble Check, generalised) ───────────────────────────────────

MECHANICS = [
    ("Fix the exact quote", "Exact words + link to the video/minute or document page. No paraphrase."),
    ("Ask the source for its source", "Request the backing from whoever said it; the source they cite becomes evidence for or against."),
    ("Go to the primary document", "Acta, law, financial statement, audit, technical report — never the press note about it."),
    ("Build the timeline", "Award → contract → endorsement (refrendo) → start order → delivery → inauguration. Who inaugurates ≠ who built."),
    ("Recalculate the figure", "Convert currency at the date's rate, subtract claimed vs recorded, compute the % gap."),
    ("Compare equivalent groups", "Top 25% vs top 25%, not top 25% vs the average. The fair comparison exposes the favourable framing."),
    ("Separate blended concepts", "exists vs available · cap vs increase · build vs rent · inaugurate vs build · operating spend vs investment."),
    ("Consult the person/institution alluded to", "The subject's own version is often the most direct refutation (or confirmation)."),
    ("Acknowledge what is true first", "Validate the true part, then measure the exaggeration — it shows exactly where the distortion starts."),
]

QUESTIONS = {
    "Origin": "Where does that figure come from? Can the document be shown?",
    "Existence": "Did the event said not to happen actually happen? Is there an acta, video, law?",
    "Time": "What happened before? Who signed, endorsed, gave the start order? Does it recur every year?",
    "Scale": "Is the comparison between equivalent things? What does the official technical parameter say?",
    "Definition": "What exactly does the term mean? Available or merely existing? Increase or cap?",
    "Attribution": "Who is the source? Was the person alluded to consulted? Who exactly are 'the groups'?",
    "Context (closing)": "What context does the public need to grasp the real size of the issue?",
}

VICES = {
    "inflated_moving_figure": ("Cifra inflada y móvil", "The number changes during the same statement and is never broken down."),
    "apples_oranges": ("Suma de peras con manzanas", "Items of different nature (third-party funds, guarantees) added up for an impressive total."),
    "forced_equivalence": ("Equivalencia forzada", "A figure is 'rounded' to a technical benchmark it does not reach."),
    "best_subgroup": ("Generalización desde lo mejor", "The most favourable subgroup is extended to the whole."),
    "cropped_timeline": ("Cronología recortada", "The start of a process is omitted to claim its result."),
    "correlation_as_intent": ("Correlación como intención", "Two events close in time presented as cause and effect, often with irony."),
    "denying_documented": ("Negar lo documentado", "Asserting something doesn't happen, betting the public won't check the record."),
    "ghost_source": ("Fuente fantasma", "Attributing to 'groups' or anonymous calls without naming or contrasting them."),
    "category_confusion": ("Confusión de categorías", "Operating spend vs investment; cap vs increase; exists vs available."),
    "attack_the_checker": ("Ataque al verificador", "Answering a check by attacking the checker instead of producing the missing source."),
}

SIGNALS = {
    1: "Huge round number with no breakdown",
    2: "The number changes while they speak",
    3: "Absolute superlatives ('any country', 'never', 'not once') — one documented exception refutes them",
    4: "Credit claimed through an inauguration — look for the FIRST document of the process",
    5: "Insinuation instead of assertion ('casually', 'let's not be suspicious')",
    6: "Unnamed collective subject ('leftist groups', 'people who work there')",
    7: "The figure isn't in the source the speaker cites",
    8: "The topic shifts (asked about staff/rent, answered with construction)",
    9: "What the statement doesn't mention (audits, official letters reveal the omission)",
}

BLIND_SPOTS = [
    "Conflict of interest — declare it (e.g. a checker funded by the institution it defends).",
    "Target imbalance — apply the same intensity to government, opposition, media and the checkers themselves.",
    "Scope creep — context must serve the checked claim, not expand into a different disputed topic.",
    "Non-response is a fact to record, never proof; the verdict must stand on documents alone.",
    "Make the verdict label explicit (Verdadero / Verdad a medias / Engañoso / Falso).",
]

# Primary repositories by topic (Costa Rica first, then international).
REPOSITORIES = [
    (r"\b(ley|leyes|art[ií]culo|decreto|reglamento|rinde cuentas|rendici[oó]n de cuentas|obligaci[oó]n legal|law|statute)\b", "SINALEVI / Procuraduría (laws, decrees, vigencia)", "site:pgrweb.go.cr"),
    (r"\b(corte|poder judicial|magistrad|sala constitucional|oij|fiscal[ií]a|juez)", "Nexus PJ (Corte Plena actas, rulings) · Dirección Ejecutiva PJ (estados financieros, fideicomiso)", "site:poder-judicial.go.cr"),
    (r"\b(ccss|caja costarricense|hospital|ebais|[aá]rea de salud|quir[oó]fano|lista de espera|enfermer)", "CCSS Junta Directiva actas · Auditoría Interna CCSS · BINASSS (memorias)", "site:ccss.sa.cr OR site:binasss.sa.cr"),
    (r"\b(presupuesto|hacienda|d[eé]ficit|deuda|impuesto|fideicomiso|recorte|budget|tax)", "Ministerio de Hacienda (proyecto de presupuesto, oficios) · Contraloría (CGR)", "site:hacienda.go.cr OR site:cgr.go.cr"),
    (r"\b(contrat|licitaci[oó]n|adjudic|refrendo|orden de inicio|sicop|procurement)", "SICOP (public procurement) · CGR refrendos", "site:sicop.go.cr OR site:cgr.go.cr"),
    (r"\b(diputad|asamblea|plenario|expediente|comisi[oó]n legislativa|congress|parliament)", "Asamblea Legislativa (SIL expedientes, actas, YouTube plenario)", "site:asamblea.go.cr"),
    (r"\b(presidenta|presidente de la rep[uú]blica|casa presidencial|ministr[oa]|conferencia de prensa|cadena nacional)", "Casa Presidencial (comunicados, YouTube conferencias) — get the exact minute", "site:presidencia.go.cr"),
    (r"\b(mep|educaci[oó]n|pisa|ocde|oecd|escuela|colegio|estudiantes)", "OECD PISA reports & technical notes · MEP", "site:oecd.org OR site:mep.go.cr"),
    (r"\b(ucr|universidad|becas|fees|conare|uned|tecnol[oó]gico de costa rica|universidad nacional)", "Universities (Oficina de Becas, actas Consejo Universitario) · CONARE (FEES)", "site:ucr.ac.cr OR site:conare.ac.cr"),
    (r"\b(inflaci[oó]n|tipo de cambio|colones|d[oó]lar|pib|gdp|desempleo|empleo|pobreza|inec|bccr)", "BCCR (exchange rates, macro series) · INEC (employment, poverty)", "site:bccr.fi.cr OR site:inec.cr"),
    (r"\b(elecci[oó]n|tse|votos|padr[oó]n|partido|campa[ñn]a)", "TSE (results, party finance)", "site:tse.go.cr"),
    (r"\b(seguridad|homicidi|crimen|narco|delito)", "OIJ statistics · Ministerio de Seguridad", "site:poder-judicial.go.cr OR site:msp.go.cr"),
    (r"\b(estado de la naci[oó]n|estado de la justicia|estado de la educaci[oó]n)", "Programa Estado de la Nación (structural diagnosis)", "site:estadonacion.or.cr"),
    (r"\b(cine|medio digital|publicaci[oó]n|redes sociales|facebook|instagram|tiktok|youtube|canal|outlet|post)\b", "The original post + the alluded party's own channels (ask them directly) · distributor / programme chain", ""),
    (r"\b(onu|united nations|who|oms|world bank|banco mundial|fmi|imf|cepal)", "International bodies' primary reports (UN, WHO, World Bank, IMF, CEPAL)", "site:un.org OR site:worldbank.org OR site:imf.org OR site:cepal.org"),
]

# ── detectors ───────────────────────────────────────────────────────────────

_MULT = {"mil": 1e3, "miles": 1e3, "thousand": 1e3, "k": 1e3, "millón": 1e6, "millon": 1e6, "millones": 1e6,
         "million": 1e6, "millions": 1e6, "m": 1e6, "mil millones": 1e9, "billion": 1e9, "billones": 1e12,
         "billón": 1e12, "bn": 1e9}
_NUM_RE = re.compile(
    r"(?P<cur>₡|US\$|\$|€|USD|CRC)?\s?(?P<num>\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s?"
    r"(?P<mult>mil millones|millones|millón|millon|mil|billones|billón|million|millions|billion|thousand|bn|k|m)?\b\s?"
    r"(?P<unit>%|por ciento|percent|puntos|points|colones|d[oó]lares|dollars|plazas|camas|quir[oó]fanos|personas|people|años|years)?",
    re.I)


def _to_float(s: str) -> Optional[float]:
    s = s.strip().replace(" ", "")
    if re.match(r"^\d{1,3}(\.\d{3})+(,\d+)?$", s):            # 1.234.567,8 (es)
        s = s.replace(".", "").replace(",", ".")
    elif re.match(r"^\d{1,3}(,\d{3})+(\.\d+)?$", s):          # 1,234,567.8 (en)
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def extract_figures(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in _NUM_RE.finditer(text or ""):
        v = _to_float(m.group("num"))
        if v is None:
            continue
        cur = (m.group("cur") or "").upper()
        mult = (m.group("mult") or "").lower()
        unit = (m.group("unit") or "").lower()
        if not cur and not mult and not unit and (1900 <= v <= 2100 or v < 10):
            continue                                            # years / tiny counts: not figures
        if "colon" in unit or cur in ("₡", "CRC"):
            kind = "CRC"
        elif "lar" in unit or cur in ("$", "US$", "USD"):
            kind = "USD"
        elif cur == "€":
            kind = "EUR"
        elif unit in ("%", "por ciento", "percent"):
            kind = "%"
        elif unit:
            kind = unit
        else:
            kind = "count"
        val = v * _MULT.get(mult, 1)
        a = max(0, m.start() - 60)
        out.append({"raw": m.group(0).strip(), "value": val, "kind": kind,
                    "context": (text[a:m.start()].split("\n")[-1])[-60:].strip(), "pos": m.start()})
    return out


_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|setiembre|septiembre|octubre|noviembre|diciembre)(?:\s+(?:de|del)\s+(\d{4}))?"
    r"|\b(\d{4})-(\d{2})-(\d{2})\b"
    r"|\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})", re.I)
_MONTHS = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                       "septiembre", "octubre", "noviembre", "diciembre"], 1)}
_MONTHS["setiembre"] = 9
_MONTHS.update({m.lower(): i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                                      "August", "September", "October", "November", "December"], 1)})


def extract_dates(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in _DATE_RE.finditer(text or ""):
        try:
            if m.group(1):
                d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3)) if m.group(3) else None
            elif m.group(4):
                y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
            else:
                mo, d, y = _MONTHS[m.group(7).lower()], int(m.group(8)), int(m.group(9))
            out.append({"raw": m.group(0), "y": y, "m": mo, "d": d, "pos": m.start(),
                        "context": text[max(0, m.start() - 50):m.start()].strip()[-50:]})
        except (KeyError, ValueError, TypeError):
            continue
    return out


_PATTERNS = [
    # (signal, vice, regex, note)
    (3, "best_subgroup", r"\b(cualquier pa[ií]s|igual o mejor|el mejor|la mejor|los mejores|el peor|r[eé]cord|hist[oó]ric[oa]|sin precedentes|el [uú]nico|la [uú]nica|any country|best in|the only|unprecedented|record[- ]breaking)\b", "absolute / superlative"),
    (3, "denying_documented", r"\b(nunca|jam[aá]s|ni una vez|ninguno|ninguna|nadie|no existe|no rinde cuentas|never|not once|nobody|no one|does not exist)\b", "absolute negative — one documented exception refutes it"),
    (3, None, r"\b(siempre|todos|todas|todo el mundo|always|everyone|all of them)\b", "absolute positive"),
    (4, "cropped_timeline", r"\b(inaugur\w*|logros? de(l)? (este |nuestro )?gobierno|gracias a (este|nuestro) gobierno|construimos|hicimos realidad|gobierno de la continuidad|we built|our government delivered|thanks to (this|our) government)\b", "credit attribution"),
    (5, "correlation_as_intent", r"\b(casualmente|curiosamente|qu[eé] coincidencia|no seamos mal pensados|justo cuando|da la casualidad|coincidentally|funny how|just when)\b", "insinuation of intent"),
    (6, "ghost_source", r"\b(grupos de \w+|ciertos sectores|algunos sectores|personas que trabajan|fuentes (an[oó]nimas|cercanas)|llamadas an[oó]nimas|se dice que|dicen que|seg[uú]n (fuentes|dicen)|some groups|sources say|people are saying|they say)\b", "unnamed collective / anonymous source"),
    (None, "forced_equivalence", r"(≈|\bequivale(n)? a\b|\bcasi (un|una|lo mismo)\b|\bpr[aá]cticamente\b|\blo mismo que\b|\bcomo (un|una) a[ñn]o\b|\bequivalent to\b|\bbasically the same\b|\balmost a\b)", "equivalence to a benchmark"),
    (None, "category_confusion", r"\b(disponible|existe(n)?|contante y sonante|l[ií]quido|en efectivo|tope|aumento|alquiler|alquilar|construir|inversi[oó]n|gasto|available|exists|cash|cap\b|increase|rent|build|investment|spending)\b", "terms that discourse often blends — define each"),
    (None, "apples_oranges", r"\b(en total|sumando|suman|incluye(ndo)?|entre todos|in total|adding up|combined)\b", "aggregate — check what is being added"),
    (None, None, r"\b(mejor que|peor que|m[aá]s que|menos que|comparad[oa] con|frente a|vs\.?|better than|worse than|compared (to|with))\b", "comparison — are the groups equivalent?"),
    (8, None, r"\b(lo importante es|la verdadera pregunta|en lugar de eso|what matters is|the real question)\b", "possible topic shift"),
]


def _round_big(fig) -> bool:
    v = fig["value"]
    return v >= 1e6 and (v % 1e8 == 0 or v % 5e7 == 0 or (v >= 1e9 and v % 1e9 == 0))


def analyse_claim(claim: str, speaker: str = "", date: str = "", context: str = "") -> Dict[str, Any]:
    text = claim or ""
    figs = extract_figures(text)
    dates = extract_dates(text)
    hits = []
    for sig, vice, rx, note in _PATTERNS:
        for m in re.finditer(rx, text, re.I):
            hits.append({"signal": sig, "vice": vice, "match": m.group(0), "note": note})
    signals, vices = set(), set()
    for h in hits:
        if h["signal"]:
            signals.add(h["signal"])
        if h["vice"] and h["vice"] != "category_confusion":
            vices.add(h["vice"])
    # Signal 1 — huge round number without breakdown
    if any(_round_big(f) for f in figs) and not re.search(r"(desglose|de los cuales|distribuid|breakdown|of which)", text, re.I):
        signals.add(1)
        vices.add("inflated_moving_figure")
    # Signal 2 — moving figure: same kind, different values within one statement
    by_kind: Dict[str, List[float]] = {}
    for f in figs:
        by_kind.setdefault(f["kind"], []).append(f["value"])
    # Values of the same currency within ~3× of each other = one quantity that
    # keeps changing (600 → 700 → 771); far-apart values are different things.
    moving = {}
    for k, v in by_kind.items():
        vs = sorted(set(v))
        if k in ("USD", "CRC", "EUR") and len(vs) > 1 and vs[-1] / (vs[0] or 1) <= 3:
            moving[k] = vs
    if re.search(r"(perd[oó]n|en realidad|digo|o sea|mejor dicho|I mean|actually)", text, re.I) and any(len(set(v)) > 1 for v in by_kind.values()):
        moving = moving or {k: sorted(set(v)) for k, v in by_kind.items() if len(set(v)) > 1}
    if moving:
        signals.add(2)
        vices.add("inflated_moving_figure")
    if re.search(r"(seg[uú]n|de acuerdo con|according to|cit[oó]|nota de|art[ií]culo de|informe de)", text, re.I):
        signals.add(7)                                       # cited source → check the figure is in it
    blended = sorted({h["match"].lower() for h in hits if h["vice"] == "category_confusion"})
    if len(blended) >= 1:
        vices.add("category_confusion")
    hay = f"{text} {context} {speaker}".lower()
    repos = [(name, site) for rx, name, site in REPOSITORIES if re.search(rx, hay, re.I)]
    if not repos:
        repos = [("Official primary source for the topic (law, acta, statistics office, audit)", "")]
    verifiable = bool(figs or dates or signals or vices)
    opinion = bool(re.search(r"\b(creo que|pienso que|deber[ií]a|ojal[aá]|vamos a|we will|i think|i believe|should)\b", text, re.I)) and not figs
    questions = {k: v for k, v in QUESTIONS.items()
                 if k in ("Origin",) and figs or k == "Existence" and 3 in signals or k == "Time" and (4 in signals or dates)
                 or k == "Scale" and (moving or any(h["note"].startswith("comparison") for h in hits) or "forced_equivalence" in vices)
                 or k == "Definition" and blended or k == "Attribution" and (6 in signals or 7 in signals)
                 or k == "Context (closing)"}
    return {"claim": text, "speaker": speaker, "date": date, "figures": figs, "dates": dates, "moving_figures": moving,
            "signals": sorted(signals), "vices": sorted(vices), "blended_terms": blended, "pattern_hits": hits[:30],
            "repositories": repos, "questions": questions, "verifiable": verifiable, "looks_like_opinion": opinion}


def _fmt_val(f):
    v = f["value"]
    s = f"{v / 1e9:,.2f} mil millones" if v >= 1e9 else f"{v / 1e6:,.1f} millones" if v >= 1e6 else f"{v:,.0f}" if v >= 100 else f"{v:g}"
    return f"{s} {f['kind']}" if f["kind"] not in ("count",) else s


def format_analysis(a: Dict[str, Any]) -> str:
    L = [f"## Fact-check workup", f"> {a['claim'][:600]}" + (f"\n> — {a['speaker']}" if a["speaker"] else "") + (f", {a['date']}" if a["date"] else "")]
    if a["looks_like_opinion"]:
        L.append(f"⚠ Reads like opinion/promise. {OUT_OF_SCOPE} Check only the factual premises inside it.")
    if a["figures"]:
        L.append("**Verifiable figures:** " + "; ".join(f"`{f['raw']}` ({_fmt_val(f)}; after “…{f['context'][-35:]}”)" for f in a["figures"][:12]))
    if a["moving_figures"]:
        L.append("**Moving figure (Signal 2):** " + "; ".join(f"{k}: " + " → ".join(f"{x:,.0f}" for x in v) for k, v in a["moving_figures"].items())
                 + " — a number that changes isn't anchored to a document.")
    if a["dates"]:
        L.append("**Dates to anchor the timeline:** " + ", ".join(d["raw"] for d in a["dates"][:8]))
    if a["signals"]:
        L.append("**Red-flag signals:** " + "; ".join(f"S{s} {SIGNALS[s]}" for s in a["signals"]))
    if a["vices"]:
        L.append("**Candidate vices to test (not yet proven):** " + "; ".join(f"{VICES[v][0]} — {VICES[v][1]}" for v in a["vices"]))
    if a["blended_terms"]:
        L.append("**Define precisely before judging:** " + ", ".join(a["blended_terms"][:10]))
    L.append("**Questions:**\n" + "\n".join(f"- {k}: {v}" for k, v in a["questions"].items()))
    L.append("**Where the proof lives:**\n" + "\n".join(f"- {n}" + (f" (`{s}`)" if s else "") for n, s in a["repositories"]))
    L.append("**Plan:** 1) fix the exact quote+minute · 2) isolate the verifiable part · 3) test every absolute for one documented exception · "
             "4) read the cited source in full — is the figure there? · 5) fetch the primary document · 6) build the timeline from the FIRST document · "
             "7) recalculate (currency at the date, gap %, equivalent groups) · 8) separate blended concepts · 9) get the alluded party's version · "
             "10) acknowledge what's true, measure the distortion, find what's omitted (audits, CGR, Estado de la Nación).")
    L.append("**Verdict scale:** " + " / ".join(VERDICTS) + ". Record who didn't respond — non-response is a fact, never proof.")
    return "\n\n".join(L)


# ── cited-source check (Signal 7) ───────────────────────────────────────────

def _norms(f) -> List[str]:
    v = f["value"]
    outs = {f["raw"].strip()}
    for div, words in ((1e9, ["mil millones", "billion", "bn"]), (1e6, ["millones", "million", "M"]), (1e3, ["mil", "thousand"])):
        if v >= div and v % (div / 10) == 0:
            n = v / div
            ns = f"{n:g}"
            outs |= {f"{ns} {w}" for w in words} | {ns.replace('.', ',') + " " + w for w in words}
    outs.add(f"{v:,.0f}")
    outs.add(f"{v:,.0f}".replace(",", "."))
    outs.add(f"{v:.0f}")
    return [o for o in outs if len(o) >= 2]


async def check_in_source(url: str, figures: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "Odysseus-FactCheck/1.0"}) as c:
            r = await c.get(url)
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)
    except Exception as e:
        return {"error": f"could not fetch the cited source: {type(e).__name__}: {str(e)[:120]}"}
    found, missing = [], []
    low = text.lower()
    for f in figures:
        if any(n.lower() in low for n in _norms(f)):
            found.append(f["raw"])
        else:
            missing.append(f["raw"])
    return {"url": url, "found": found, "missing": missing, "source_figures": [g["raw"] for g in extract_figures(text)][:40]}


async def search_primary(claim: str, repos: List[Tuple[str, str]], extra: str = "") -> Dict[str, Any]:
    """Run searches restricted to the primary repositories + look for prior checks."""
    try:
        from src.search import comprehensive_web_search
    except Exception as e:
        return {"error": str(e)}
    key = " ".join(re.findall(r"[\wáéíóúñ]{4,}", claim)[:9])
    queries = [f"{key} {site}".strip() for _, site in repos[:4] if site]
    queries.append(f"{key} (\"Doble Check\" OR verificación OR fact-check OR \"falso\" OR \"engañoso\")")
    if extra:
        queries.append(extra)

    async def one(q):
        try:
            ctx, src = await asyncio.wait_for(asyncio.to_thread(comprehensive_web_search, q, return_sources=True), 30)
            return q, [s for s in (src or []) if isinstance(s, dict)][:5]
        except Exception:
            return q, []
    res = await asyncio.gather(*[one(q) for q in queries])
    return {"queries": [q for q, _ in res], "results": {q: s for q, s in res}}


# ── anomalies across sources ────────────────────────────────────────────────

def _anchor(ctx: str) -> str:
    words = re.findall(r"[a-záéíóúñ]{4,}", ctx.lower())
    stop = {"para", "como", "pero", "este", "esta", "esos", "unos", "unas", "with", "that", "from", "this", "they",
            "sobre", "entre", "desde", "hasta", "según", "cuando", "donde", "también", "about", "were", "have"}
    words = [w for w in words if w not in stop]
    return " ".join(words[-2:])


def anomalies(items: List[Dict[str, str]]) -> Dict[str, Any]:
    """items: [{"source": name/url, "text": ...}]. Finds figures about the same
    anchor that disagree across (or within) sources, outliers vs the median,
    absolutes contradicted by another source, and impossible date orders."""
    rows = []
    for it in items:
        for f in extract_figures(it.get("text", "")):
            rows.append({**f, "source": it.get("source") or "?", "anchor": _anchor(f["context"])})
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        if r["anchor"]:
            groups.setdefault((r["anchor"], r["kind"]), []).append(r)
    conflicts = []
    for (anc, kind), g in groups.items():
        vals = [x["value"] for x in g]
        if len(set(vals)) < 2:
            continue
        med = statistics.median(vals)
        spread = (max(vals) - min(vals)) / (abs(med) or 1)
        if spread < 0.05:
            continue
        conflicts.append({"anchor": anc, "kind": kind, "spread_pct": round(spread * 100, 1),
                          "values": [{"value": x["value"], "raw": x["raw"], "source": x["source"],
                                      "outlier": abs(x["value"] - med) / (abs(med) or 1) > 0.3} for x in g]})
    conflicts.sort(key=lambda c: -c["spread_pct"])
    absolutes = []
    for it in items:
        for m in re.finditer(r"\b(nunca|jam[aá]s|ni una vez|ninguno|no existe|never|not once|no one|does not exist)\b[^.]{0,80}", it.get("text", ""), re.I):
            absolutes.append({"source": it.get("source"), "text": m.group(0)[:120]})
    order = []
    for it in items:
        ds = extract_dates(it.get("text", ""))
        seq = [d for d in ds if d["y"]]
        for a, b in zip(seq, seq[1:]):
            if re.search(r"\b(luego|despu[eé]s|posteriormente|then|after|later)\b", it["text"][a["pos"]:b["pos"]], re.I) and \
                    (b["y"], b["m"], b["d"]) < (a["y"], a["m"], a["d"]):
                order.append({"source": it.get("source"), "first": a["raw"], "then": b["raw"]})
    return {"conflicts": conflicts[:20], "absolutes_to_test": absolutes[:15], "date_order_issues": order[:10],
            "figures_read": len(rows)}


def format_anomalies(r: Dict[str, Any]) -> str:
    L = [f"## Anomaly scan — {r['figures_read']} figures read"]
    if not (r["conflicts"] or r["absolutes_to_test"] or r["date_order_issues"]):
        L.append("No numeric disagreements, untested absolutes or date-order problems detected. (Absence of anomalies ≠ verified.)")
    for c in r["conflicts"]:
        L.append(f"**{c['anchor']}** ({c['kind']}) — values disagree by {c['spread_pct']}%:")
        for v in c["values"]:
            L.append(f"- `{v['raw']}` · {v['source']}" + ("  ← outlier vs median" if v["outlier"] else ""))
    if r["absolutes_to_test"]:
        L.append("**Absolutes to test (one documented exception refutes them):**")
        L += [f"- {a['source']}: “{a['text']}”" for a in r["absolutes_to_test"]]
    if r["date_order_issues"]:
        L.append("**Timeline problems:**")
        L += [f"- {d['source']}: “{d['first']}” then “{d['then']}”" for d in r["date_order_issues"]]
    L.append("Treat each item as a LEAD: find the primary document that settles it, and name competing explanations "
             "(different date, different scope, different definition, error, or deliberate distortion).")
    return "\n".join(L)


# ── recalculation ───────────────────────────────────────────────────────────

def recalc(claimed: float, documented: float, rate: Optional[float] = None, unit: str = "") -> Dict[str, Any]:
    out = {"claimed": claimed, "documented": documented}
    if rate:
        out["claimed_converted"] = claimed / rate
        out["documented_converted"] = documented / rate
    diff = claimed - documented
    out["difference"] = diff
    out["ratio"] = (claimed / documented) if documented else None
    out["gap_pct_vs_documented"] = (diff / documented * 100) if documented else None
    out["documented_as_pct_of_claimed"] = (documented / claimed * 100) if claimed else None
    txt = (f"Claimed {claimed:,.2f}{unit} vs documented {documented:,.2f}{unit}: difference {diff:,.2f}{unit}"
           + (f" · claimed is {out['ratio']:.2f}× the record ({out['gap_pct_vs_documented']:+.1f}%)" if out["ratio"] else "")
           + (f" · the record is {out['documented_as_pct_of_claimed']:.1f}% of the claim" if out["documented_as_pct_of_claimed"] else ""))
    if rate:
        txt += f" · at rate {rate}: {out['claimed_converted']:,.2f} vs {out['documented_converted']:,.2f}"
    return {"output": txt, "data": out}


def method_text() -> str:
    L = ["## Fact-checking method (Doble Check, generalised)",
         "**Mechanics:**"] + [f"{i}. **{n}** — {d}" for i, (n, d) in enumerate(MECHANICS, 1)]
    L += ["**Six question types:**"] + [f"- {k}: {v}" for k, v in QUESTIONS.items()]
    L += ["**Vices (most distort a TRUE datum rather than invent):**"] + [f"- {n}: {d}" for n, d in VICES.values()]
    L += ["**Red-flag signals:**"] + [f"- S{k}: {v}" for k, v in SIGNALS.items()]
    L += ["**Transversal strategy:** verify a SERIES of statements over time, not an isolated sentence — patterns appear.",
          "**Verdict scale:** " + " / ".join(VERDICTS) + f". {OUT_OF_SCOPE}",
          "**Apply the same lens to the checkers (blind spots):**"] + [f"- {b}" for b in BLIND_SPOTS]
    return "\n".join(L)


# ── entry point for the tool ────────────────────────────────────────────────

async def run(args: Dict[str, Any]) -> Dict[str, Any]:
    action = (args.get("action") or "claim").lower()
    t0 = time.time()
    try:
        if action == "method":
            return {"output": method_text(), "exit_code": 0}
        if action == "recalc":
            r = recalc(float(args["claimed"]), float(args["documented"]),
                       float(args["rate"]) if args.get("rate") else None, str(args.get("unit") or ""))
            return {"output": r["output"], "exit_code": 0, "data": r["data"]}
        if action == "anomalies":
            items = args.get("items") or []
            if isinstance(items, str):
                items = [{"source": "text", "text": items}]
            if args.get("text"):
                items.append({"source": "text", "text": args["text"]})
            r = anomalies(items)
            return {"output": format_anomalies(r), "exit_code": 0, "data": r}
        claim = str(args.get("claim") or args.get("text") or "").strip()
        if not claim:
            return {"error": "factcheck needs a claim (exact words)", "exit_code": 1}
        a = analyse_claim(claim, str(args.get("speaker") or ""), str(args.get("date") or ""), str(args.get("context") or ""))
        out = format_analysis(a)
        data: Dict[str, Any] = {"analysis": {k: v for k, v in a.items() if k != "pattern_hits"}}
        if args.get("source_url") and a["figures"]:
            chk = await check_in_source(str(args["source_url"]), a["figures"])
            data["cited_source"] = chk
            if chk.get("error"):
                out += f"\n\n**Cited source:** {chk['error']}"
            else:
                out += (f"\n\n**Cited source check (S7)** — {chk['url']}\n- Figures found there: {', '.join(chk['found']) or 'none'}"
                        f"\n- NOT in the cited source: {', '.join(chk['missing']) or 'none'}"
                        + ("\n- ⚠ The speaker's figure is not in the source they cite — this is evidence against the claim." if chk["missing"] else ""))
        if args.get("run"):
            sr = await search_primary(claim, a["repositories"], str(args.get("query") or ""))
            data["primary_search"] = sr
            if sr.get("results"):
                out += "\n\n**Primary-source & prior-check search:**"
                for q, hits in sr["results"].items():
                    for h in hits[:3]:
                        out += f"\n- [{(h.get('title') or h.get('url') or '')[:90]}]({h.get('url')})  ← `{q[:70]}`"
        out += f"\n\n_factcheck {action} · {time.time() - t0:.1f}s_"
        return {"output": out, "exit_code": 0, "data": data}
    except (KeyError, ValueError, TypeError) as e:
        return {"error": f"factcheck {action}: bad arguments ({e})", "exit_code": 1}


INVESTIGATION_PROTOCOL = (
    "## Investigation & fact-check protocol (always)\n"
    "- **Anomaly radar:** while investigating, keep a running baseline of what the context, the regional brief and general knowledge "
    "say should be true. Flag anything that is OFF (a figure far from documented values, a figure that changes, dates out of order, an "
    "actor in an unexpected role, money moving through unusual entities, a silence where a record should exist). An anomaly is a LEAD, "
    "not a verdict: say why it is off, list competing explanations (different date / scope / definition, error, deliberate distortion) "
    "and what document would settle it. Your memory predates today — when news contradicts your memory, trust dated sources.\n"
    "- **Inference & induction:** reason explicitly from the current global and regional context (budget disputes, institutional "
    "conflicts, electoral cycles, economic pressures, who benefits) to hypotheses; label each as inference with a confidence, and keep "
    "facts, inferences and speculation visibly separate.\n"
    "- **Fact-check claims** with the `factcheck` tool: exact quote → verifiable atoms → red-flag signals → candidate vices → primary "
    "documents (actas, laws, financial statements, audits, technical reports) → timeline from the FIRST document → recalculation → "
    "equivalent comparisons → the alluded party's version → acknowledge what's true, measure the distortion, find what's omitted. "
    "Give an explicit verdict (Verdadero / Verdad a medias / Engañoso / Falso / No verificable aún) with the documents that support it.\n"
    "- **Same lens for everyone:** government, opposition, media and fact-checkers alike; declare conflicts of interest; "
    "non-response is recorded, never treated as proof; keep context in scope.\n\n"
)


# ── regional context briefs ─────────────────────────────────────────────────
# knowledge/regional/*.md — each starts with a front-matter block:
#   ---
#   region: Costa Rica
#   as_of: 2026-10-29
#   triggers: costa rica|ccss|oij|...        (regex, case-insensitive)
#   ---
# The brief is injected into a turn when the conversation matches its
# triggers, so inferences rest on current regional context, not stale memory.

_BRIEFS = None


def _load_briefs():
    global _BRIEFS
    if _BRIEFS is not None:
        return _BRIEFS
    import os, glob
    try:
        from src.constants import BASE_DIR
    except Exception:
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = []
    for p in sorted(glob.glob(os.path.join(BASE_DIR, "knowledge", "regional", "*.md"))):
        try:
            txt = open(p, encoding="utf-8").read()
        except OSError:
            continue
        meta, body = {}, txt
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", txt, re.S)
        if m:
            body = m.group(2)
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
        try:
            rx = re.compile(meta.get("triggers") or r"$^", re.I)
        except re.error:
            continue
        out.append({"path": p, "meta": meta, "body": body.strip(), "rx": rx})
    _BRIEFS = out
    return out


def regional_context(text: str, max_chars: int = 7000) -> Optional[str]:
    hits = [b for b in _load_briefs() if b["rx"].search(text or "")]
    if not hits:
        return None
    parts = []
    for b in hits[:2]:
        parts.append(f"=== REGIONAL CONTEXT BRIEF — {b['meta'].get('region', '?')} (as of {b['meta'].get('as_of', '?')}) ===\n"
                     "Use it as background for inferences and to notice what is OFF; it is a compilation of dated "
                     "sources, not ground truth — re-verify anything you cite.\n" + b["body"][:max_chars])
    return "\n\n".join(parts)
