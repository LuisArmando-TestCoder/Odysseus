"""Minimal, hallucination-free voice ↔ chat flow — regressions replayed from the
2026-10-30 peritaje session (Toyota Prado A340F, licenciado Murillo).

What went wrong in that session, and the test that pins each fix:

  1. "¿Quieres que le pida al chat…?" asked ~10 times before anything was sent
     → ambiguous direction sends at once; permission questions are flagged.
  2. "Guard rejected the message" ×4 and then "Voice→chat paused"
     → a doubted forge sends the user's own words; there is no pause state.
  3. "dame la conclusión" was forged into "Necesito un análisis técnico
     detallado… el informe debe incluir 1) 2) 3) 4)"; the chat wrote a document
     → a forge that adds an unrequested deliverable falls back to the user's words.
  4. The voice turned "damaged mount" + "sludge" (both maintenance context) into
     a vibration → slipping causal chain the expert had ruled out
     → self-assessment notes are independent, carry the user's quote, and the
       prompt forbids chaining them causally.
  5. ALFRED re-asked the same question over and over
     → question ledger flags repeats with the user's answer since.
  6. "¿De qué estamos hablando?" was forwarded to the chat as new work
     → answered from the whole chat digest, never sent.
  7. The chat answered every relay with "su mensaje se recortó"
     → the chat's voice stratum says relays are condensed, nothing was cut.
  8. Streamed batches were re-spoken from the beginning
     → sentence ledger (voice-delta.js) only ever adds new sentences.
  9. The Middleman rewrote answers into "obedient" ones → removed.
 (A second session, 2026-10-31, is replayed at the bottom of this file.)
 10. A user turn that grew by merging a later fragment came back WHOLE, so
     speech already sent was forged into the next message again
     → only the never-evaluated part of a turn is new speech.

Offline: the decider, guard and utility LLM are monkeypatched.
"""
import asyncio
import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

USER_CONCLUSION = ("Esa pregunta me la has hecho varias veces y varias veces te he pedido que me indiques "
                   "cuál es tu conclusión basado en el contexto que ya tenés")
FORGED_WITH_DOC = ("Necesito un análisis técnico detallado que sustente la conclusión. El informe debe incluir: "
                   "1) mecanismo de falla, 2) evidencias, 3) relación causal y dejarlo guardado como documento.")
USER_TEACHES = ("Como parte de tu aprendizaje: el soporte de la caja no está vinculado con la falla interna, "
                "es un asunto contextual, no un nexo causal")


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sa(monkeypatch, tmp_path):
    import src.self_assessment as S
    monkeypatch.setattr(S, "_root", lambda: str(tmp_path / "sa"))
    (tmp_path / "sa").mkdir()
    return S


@pytest.fixture
def pipe(monkeypatch, tmp_path, sa):
    import services.voice.memory as M
    import services.voice.pipeline as P

    monkeypatch.setattr(M, "_memory_dir", lambda: str(tmp_path))
    monkeypatch.setattr(M, "_registry", {})
    monkeypatch.setattr(P, "chat_summary_spine", lambda *a, **k: "CHAT_AI: last turns only")
    monkeypatch.setattr(P, "chat_context_digest", lambda *a, **k: "FULL-DIGEST: caso Prado A340F, silicón RTV, lodos")
    monkeypatch.setattr(P, "session_documents_spine", lambda *a, **k: "")
    monkeypatch.setattr(P, "jev_available", lambda: False)
    st = {"decision": {}, "guard": {"coherent": 1, "duplicate": 0, "faithful": 1, "directed": 1},
          "forge": "Dame tu conclusión con el contexto que ya tienes.", "utility_inputs": [],
          "lesson": None}

    async def _decide(owner, state, use_jev):
        d = dict(engine="llm", should_send=0, ready=1, already_sent=0, intent="none", answerable=0,
                 commitment=0, stop_talking=0, interrupt=0, negative_directive=0, directed_to_chat=0,
                 confirms_pending=0, about_conversation=0, teaches=0, urgency=0)
        d.update(st["decision"])
        return d

    async def _guard(owner, message, ctx, use_jev):
        st["guarded"] = message
        return dict(st["guard"])

    async def _utility(owner, system, user, max_tokens=400):
        st["utility_inputs"].append((system, user))
        if "self-assessment note" in system:
            return st["lesson"] or "{}"
        if "extract" in system.lower():
            return st.get("extract") or ('{"found": true, "answer": "Hablamos del peritaje de la caja A340F.", '
                                         '"quote": "caso Prado A340F, silicón RTV, lodos"}')
        return st["forge"]

    monkeypatch.setattr(P, "_decide", _decide)
    monkeypatch.setattr(P, "_guard", _guard)
    monkeypatch.setattr(P, "_utility", _utility)

    def run(text, speaker="You", voice="v", **kw):
        async def go():
            return [e async for e in P.run_pipeline("owner", voice, [{"speaker": speaker, "text": text}],
                                                    "sess", **kw)]
        return asyncio.run(go())

    st["run"] = run
    st["mem"] = lambda voice="v": M.get_voice_memory("owner", voice)
    return st


def _types(evs, t):
    return [e for e in evs if e["type"] == t]


def _reasons(evs):
    return [e.get("reason") for e in evs if e["type"] == "done"]


# ── 1 / 2. one hop: no permission round-trip, no guard pause ───────────────

def test_ambiguous_direction_is_sent_without_asking(pipe):
    pipe["decision"] = {"should_send": 0.9, "intent": "question"}
    pipe["guard"]["directed"] = 0.5
    evs = pipe["run"](USER_CONCLUSION, voice="hop1")
    assert _types(evs, "forged"), _reasons(evs)
    assert not _types(evs, "needs_clarification")
    assert pipe["mem"]("hop1").pending_confirm is None


def test_unfaithful_forge_sends_the_users_own_words_and_never_pauses(pipe):
    pipe["decision"] = {"should_send": 0.9, "intent": "question"}
    pipe["guard"]["faithful"] = 0.1
    sent = []
    for i, text in enumerate([
        ". Okay, pregúntele al chat por el tema que yo te pregunté con la respuesta que te di",
        "Basado en el contexto completo, determina si la falla fue por mantenimiento reciente o prolongado",
        "Detecté residuos lodosos y magnéticos dentro de las carcasas y obstrucción de las líneas",
    ]):
        evs = pipe["run"](text, voice="hop2")
        assert "guard-paused" not in _reasons(evs)
        f = _types(evs, "forged")
        assert f, _reasons(evs)
        sent.append(f[0]["message"])
        assert _types(evs, "verbatim")
    assert sent[0].startswith("Okay, pregúntele al chat")       # stray ". " from the transcript removed
    assert sent[1].startswith("Basado en el contexto completo")


def test_merged_turn_never_resends_speech_already_sent(pipe):
    pipe["decision"] = {"should_send": 0.9, "intent": "question"}
    pipe["forge"] = ""                                    # forge returns nothing → the user's words
    first = _types(pipe["run"]("Analiza si la falla fue por mantenimiento reciente o prolongado", voice="mrg"), "forged")
    second = _types(pipe["run"]("y considera que el taller niega haber aplicado el silicón", voice="mrg"), "forged")
    assert first and second
    assert second[0]["message"] == "y considera que el taller niega haber aplicado el silicón"


def test_doubted_assistant_promise_is_dropped_not_rewritten(pipe):
    pipe["decision"] = {"commitment": 0.95}
    pipe["guard"]["faithful"] = 0.1
    evs = pipe["run"]("Le consulto al chat la especificación exacta del ATF para el 5VZ-FE.",
                      speaker="AI", voice="hop3")
    assert not _types(evs, "forged") and "guard-rejected" in _reasons(evs)


# ── 3. no invented deliverables ─────────────────────────────────────────────

def test_forge_that_adds_a_document_falls_back_to_the_users_words(pipe):
    pipe["decision"] = {"should_send": 0.9, "intent": "question"}
    pipe["forge"] = FORGED_WITH_DOC
    evs = pipe["run"](USER_CONCLUSION, voice="del1")
    f = _types(evs, "forged")
    assert f and f[0]["message"] == USER_CONCLUSION
    assert "documento" not in f[0]["message"].lower() and "informe" not in f[0]["message"].lower()
    v = _types(evs, "verbatim")
    assert v and v[0]["reason"].startswith("added-")


def test_a_requested_document_is_kept(pipe):
    pipe["decision"] = {"should_send": 0.9, "intent": "task"}
    pipe["forge"] = "Redacta el informe pericial y guárdalo como documento."
    evs = pipe["run"]("Ahora sí, prepárame el informe pericial completo en un documento", voice="del2")
    f = _types(evs, "forged")
    assert f and f[0]["message"] == "Redacta el informe pericial y guárdalo como documento."
    assert not _types(evs, "verbatim")


def test_assistant_promise_of_an_unrequested_document_is_not_forwarded(pipe):
    pipe["decision"] = {"commitment": 0.95}
    pipe["forge"] = "Prepara el análisis forense completo y guárdalo como documento anexo."
    evs = pipe["run"]("Le preparo el análisis completo y lo dejo guardado como documento.",
                      speaker="AI", voice="del3")
    assert not _types(evs, "forged")
    assert "commitment-unrequested" in _reasons(evs)


def test_unrequested_deliverables_helper():
    from services.voice.pipeline import unrequested_deliverables
    assert unrequested_deliverables(FORGED_WITH_DOC, USER_CONCLUSION) == ["document", "report"]
    assert unrequested_deliverables("Dame la conclusión en dos frases.", USER_CONCLUSION) == []
    assert unrequested_deliverables("Make a comparison table", "build me a table of the options") == []


# ── 6. "what are we talking about" never goes to the chat ─────────────────

def test_about_the_conversation_is_answered_from_the_whole_chat(pipe):
    pipe["decision"] = {"should_send": 0.9, "about_conversation": 0.95, "intent": "question"}
    evs = pipe["run"]("Bueno, entonces, ¿de qué estamos hablando?", voice="conv1")
    assert not _types(evs, "forged")
    assert _types(evs, "answer_from_context")
    extractor_input = [u for s, u in pipe["utility_inputs"] if "extract" in s.lower()][-1]
    assert "FULL-DIGEST" in extractor_input           # the whole chat, not just its last turns


# ── 4. self-assessment: independent notes, real quotes, no causal chaining ─

def test_correction_becomes_an_independent_note_with_the_users_quote(pipe, sa):
    pipe["decision"] = {"teaches": 0.95}
    pipe["lesson"] = json.dumps({
        "note": "Tratar el soporte dañado como contexto de falta de mantenimiento, no como causa de la falla interna.",
        "kind": "correction",
        "evidence": "es un asunto contextual, no un nexo causal",
        "relation": "context-only"})
    evs = pipe["run"](USER_TEACHES, voice="sa1")
    les = _types(evs, "lesson")
    assert les and les[0]["note"]["relation"] == "context-only"
    block = sa.prompt_block("owner", "sess")
    assert sa.EPISTEMIC_RULE in block
    assert "[correction] [context-only]" in block
    assert 'the user said: "es un asunto contextual, no un nexo causal"' in block


def test_note_with_invented_evidence_is_refused(pipe, sa):
    pipe["decision"] = {"teaches": 0.95}
    pipe["lesson"] = json.dumps({
        "note": "La vibración del soporte aceleró el patinaje de los discos.",
        "kind": "lesson", "evidence": "la vibración del soporte causa patinaje"})
    evs = pipe["run"](USER_TEACHES, voice="sa2")
    assert not _types(evs, "lesson")
    assert sa.entries("owner", "sess") == []


def test_self_assessment_store(sa):
    a = sa.append("u1", "s1", "No asumir causalidad entre hallazgos del mismo contexto.", kind="lesson")
    again = sa.append("u1", "s1", "no asumir causalidad entre hallazgos del mismo contexto")
    assert a and again["id"] == a["id"]                                         # dedupe
    sa.append("u1", "s1", "Dar la conclusión en tres frases cuando la pidan.", kind="preference")
    sa.append("u1", None, "Responder en español.", kind="preference", scope="owner")
    sa.append("u2", "s1", "Nota de otro usuario que nunca debe verse.")
    texts = [e["text"] for e in sa.entries("u1", "s1")]
    assert texts[0] == "Responder en español." and len(texts) == 3
    assert all("otro usuario" not in t for t in texts)                       # owner isolation
    block = sa.prompt_block("u1", "s1")
    assert block.count("\n1. ") == 1 and "\n3. " in block                    # one numbered line per note
    assert sa.remove("u1", "s1", a["id"]) and len(sa.entries("u1", "s1")) == 2
    assert sa.append("u1", "s1", "short") is None


def test_chat_context_gets_the_notes(sa, monkeypatch):
    sa.append("owner", "sess", "Separar contexto de causa en el dictamen.", kind="correction")
    src = (_REPO / "routes" / "chat_helpers.py").read_text()
    assert "self_assessment" in src and "_sa.prompt_block(user, session_id" in src


# ── 5. questions are not re-asked ───────────────────────────────────────────

def test_repeated_question_is_flagged_with_the_users_answer(pipe):
    pipe["run"]("¿El ATF presentó olor a vinagre y el daño apareció justo después de la intervención?",
                speaker="AI", voice="q1")
    pipe["run"]("No sé el olor, pero el taller dice que el silicón ya estaba cuando abrieron la caja",
                voice="q1")
    evs = pipe["run"]("Entiendo. ¿El ATF presentó olor a vinagre, y el daño apareció después de esa intervención?",
                      speaker="AI", voice="q1")
    rq = _types(evs, "repeated_question")
    assert rq and rq[0]["times"] == 2 and not rq[0]["permission"]
    assert "el silicón ya estaba" in rq[0]["answers"]
    assert "olor a vinagre" in pipe["mem"]("q1").questions_text()


def test_permission_question_is_flagged(pipe):
    evs = pipe["run"]("¿Quieres que le pida al chat un criterio rápido sobre si eso apunta a un proceso prolongado?",
                      speaker="AI", voice="q2")
    rq = _types(evs, "repeated_question")
    assert rq and rq[0]["permission"]


# ── 7. the chat is told relays are condensed, not cut ──────────────────────

def test_chat_stratum_says_relays_are_not_truncated(monkeypatch, tmp_path):
    import services.voice.memory as M
    monkeypatch.setattr(M, "_memory_dir", lambda: str(tmp_path))
    monkeypatch.setattr(M, "_registry", {})
    M.get_voice_memory("owner", "chat-s7").add_turns([{"speaker": "You", "text": USER_TEACHES}])
    try:
        from routes.chat_helpers import voice_memory_stratum
    except Exception as e:  # pragma: no cover - env without fastapi
        pytest.skip(f"chat_helpers unavailable: {e}")
    out = voice_memory_stratum("owner", "s7")
    assert "Nothing was cut or truncated" in out and USER_TEACHES in out


def test_voice_contract_rules():
    try:
        from routes.realtime_routes import VOICE_CONTRACT
    except Exception as e:  # pragma: no cover
        pytest.skip(f"realtime_routes unavailable: {e}")
    for rule in ("ONE HOP", "NEVER ask permission", "PROACTIVE QUESTIONS", "Never ask the same question twice",
                 "CONCLUSIONS", "never turn co-occurrence into causation", "RELAY PARTS"):
        assert rule in VOICE_CONTRACT


# ── 9. the Middleman is gone ────────────────────────────────────────────────

def test_middleman_is_removed(monkeypatch):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.llm_middleman")
    import src.llm_core as C
    monkeypatch.setattr(C, "_llm_call_raw", lambda *a, **k: "No lo sé; habría que medir la presión de línea.")
    assert C.llm_call("u", "m", []) == "No lo sé; habría que medir la presión de línea."
    from src import hooks
    hooks._STREAM_REGISTRY.clear(); hooks._builtins_done = False
    hooks.register_builtins()
    assert not hooks._STREAM_REGISTRY.get(hooks.HookPoint.POST_RESPONSE)
    hooks._REGISTRY.clear(); hooks._builtins_done = False
    from src.settings import DEFAULT_SETTINGS
    assert not [k for k in DEFAULT_SETTINGS if k.startswith("llm_middleman")]


# ── 8. streaming: each batch adds, nothing is re-said ──────────────────────

_NODE = shutil.which("node") is not None


def _node(script: str):
    res = subprocess.run(["node", "--input-type=module", "-e", script], cwd=_REPO,
                         capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not _NODE, reason="node not on PATH")
def test_sentence_ledger_only_adds_new_sentences():
    out = _node(r"""
import { createLedger } from './static/js/voice-delta.js';
const L = createLedger();
const r = [];
const a = 'Entiendo, licenciado. El quid forense es separar procesos.';
const b = a + ' El ATF viejo deja lodos y limadura acumulados. En cambio, el RTV deja grumos';
r.push(L.take(a, 'speak'));                       // batch 1
r.push(L.take(b, 'speak'));                       // batch 2 = cumulative: only the new sentence
r.push(L.take(b + ' gelatinosos.', 'speak', true)); // the fragment completes: only its rest
r.push(L.take('**Entiendo, licenciado.**\n\nEl quid forense es separar procesos.', 'speak', true)); // re-render
r.push(L.take(a + ' Lo más habitual es un fondo crónico.', 'speak', true));  // new bubble restating round 1
r.push(L.take(a, 'know', true));                  // spoken ⇒ already known
const K = createLedger();
r.push(K.take('Los lodos son crónicos.', 'know', true));
r.push(K.take('Los lodos son crónicos.', 'speak', true));   // known silently can still be said once
r.push(K.take('Los lodos son crónicos.', 'speak', true));
K.resetKnown();
r.push(K.take('Los lodos son crónicos.', 'know', true));   // a new session still knows what was said
console.log(JSON.stringify(r));
""")
    assert out == [
        "Entiendo, licenciado. El quid forense es separar procesos.",
        "El ATF viejo deja lodos y limadura acumulados.",
        "En cambio, el RTV deja grumos gelatinosos.",
        "",
        "Lo más habitual es un fondo crónico.",
        "",
        "Los lodos son crónicos.",
        "Los lodos son crónicos.",
        "",
        "",
    ]


def test_voice_js_uses_the_ledger_and_no_longer_asks_or_replays():
    js = (_REPO / "static" / "js" / "voice.js").read_text()
    assert "import { createLedger } from './voice-delta.js'" in js
    assert "Send this to the chat? Say yes" not in js
    assert "Voice→chat paused" not in js
    seed = js[js.index("function _seedChatContext()"):js.index("function _briefNote(")]
    assert "`${who}: " not in seed and "_deliver.markKnown" in seed      # no replay of chat turns
    narr = js[js.index("function _narrateStream(busy)"):js.index("// ── Live context streaming", js.index("function _narrateStream(busy)"))]
    assert ".slice(_narratedLen)" not in narr and "_deliver.take" in narr


# ════════════════════════════════════════════════════════════════════════════
#  Second session (2026-10-31, English, "Nogui Acosta" research). What broke:
#   a. "dig deeper on the award" was "answered from context" with the
#      restaurant from three turns earlier; "his name likely comes from…"
#      was an inference the chat never made
#   b. ALFRED kept talking over "shut up", and narrated search-result titles
#      ("an article about Schäuble…") and "still checking" every minute
#   c. the user found the answer (The Banker, FT Group) mid-run; it waited in
#      the queue ~15 min and the run ended saying no magazine could be found
#   d. "Want me to look deeper into…?" offers
#   e. event log / errors could only be copied by hand
# ════════════════════════════════════════════════════════════════════════════

RESTAURANT_CTX = ("CHAT_AI: The restaurant's founder is named Nogui Acosta and it was opened in 1974 in Tamarindo. "
                  "It is the family bar-restaurant flagged for debts in the 2019 controversy.")


def test_new_request_is_not_answered_with_an_earlier_topic(pipe, monkeypatch):
    import services.voice.pipeline as P
    monkeypatch.setattr(P, "chat_context_digest", lambda *a, **k: RESTAURANT_CTX)
    pipe["decision"] = {"should_send": 0.9, "answerable": 0.9, "intent": "research"}
    pipe["extract"] = json.dumps({"found": True, "answer": "On the restaurant itself, it was founded in 1974.",
                                  "quote": "it was opened in 1974 in Tamarindo"})
    evs = pipe["run"]("I want to dig deeper on the recognition a British organism gave him as a great minister",
                      voice="nb1")
    assert not _types(evs, "answer_from_context")
    assert _types(evs, "forged")                                  # the new request goes to the chat


def test_answer_grounding_rules():
    from services.voice.pipeline import answer_is_grounded
    q = "Which British magazine named him finance minister of the year?"
    ctx = "CHAT_AI: The Banker, an FT Group magazine, named Nogui Acosta Finance Minister of the Year 2024."
    good = "The Banker, an FT Group magazine, named Nogui Acosta Finance Minister of the Year 2024"
    assert answer_is_grounded(q, "The Banker, from the FT Group.", good, ctx)
    assert not answer_is_grounded(q, "The Banker.", "a quote that is not in the context at all", ctx)
    assert not answer_is_grounded(q, "Probably The Banker.", good, ctx)                 # added hedge
    assert not answer_is_grounded("Who founded the restaurant in Tamarindo?", "The Banker.", good, ctx)  # off-topic
    assert answer_is_grounded("", "The chat has the award.", good, ctx)                 # "what do we have?"


def test_ungrounded_extraction_is_not_spoken(pipe):
    pipe["decision"] = {"about_conversation": 0.95}
    pipe["extract"] = json.dumps({"found": True, "answer": "His name likely comes from the family restaurant.",
                                  "quote": "The restaurant's founder is named Nogui Acosta"})
    evs = pipe["run"]("What do you find so far about what we are discussing", voice="nb2")
    assert not _types(evs, "answer_from_context")
    assert "about-conversation" in _reasons(evs)


def test_offer_of_more_work_is_flagged(pipe):
    evs = pipe["run"]("That's everything for now, sir. Want me to look deeper into the 2019 case?",
                      speaker="AI", voice="nb3")
    rq = _types(evs, "repeated_question")
    assert rq and rq[0]["permission"]


def test_live_notes_reach_the_running_turn():
    from src import live_notes as L
    L.clear("s-live")
    L.add("s-live", "I found it: The Banker (FT Group) named him Finance Minister of the Year 2024.", owner="o")
    L.add("s-live", "   ", owner="o")
    assert L.pending("s-live") == 1
    msg = L.as_message(L.drain("s-live"))
    assert msg["role"] == "user" and "The Banker" in msg["content"]
    assert "do not report them as unfound" in msg["content"]
    assert L.drain("s-live") == [] and L.as_message([]) is None
    loop = (_REPO / "src" / "agent_loop.py").read_text()
    assert "_ln.drain(session_id)" in loop and "messages.append(_note_msg)" in loop


def test_live_note_route_exists():
    try:
        from routes.realtime_routes import setup_realtime_routes
    except Exception as e:  # pragma: no cover
        pytest.skip(f"realtime_routes unavailable: {e}")
    paths = {r.path for r in setup_realtime_routes().routes}
    assert "/api/realtime/live-note" in paths and "/api/realtime/lessons" in paths


def test_voice_js_floor_control_and_quiet_hold():
    js = (_REPO / "static" / "js" / "voice.js").read_text()
    drain = js[js.index("function _drainSay()"):js.index("function _narrateStream(")]
    assert "_lastUserVoiceAt < _FLOOR_GAP_MS" in drain             # never over the user
    assert "if (_quiet)" in drain and "FINISHED" in drain           # quiet: results only
    stall = js[js.index("function _maybeSayStall("):js.index("function _maybeSayStall(") + 400]
    assert "_quiet" in stall and "45000" in stall
    assert "_STOP_RE.test(chunk)" in js and "_audioEl.muted = true" in js   # instant local hush
    assert "if (!_opts.narrate || _quiet)" in js                    # no interim narration by default
    assert "/api/realtime/live-note" in js                          # mid-run info goes into the run
    assert "reconnecting…" in js


def test_copy_buttons_for_event_log_and_errors():
    js = (_REPO / "static" / "js" / "voice.js").read_text()
    assert 'data-act="copy-log"' in js and 'data-act="copy-errors"' in js
    assert "_copyText(vectors.logText()" in js and "_copyText(_errorsText()" in js
    vv = (_REPO / "static" / "js" / "voice_vectors.js").read_text()
    assert "export function logText()" in vv and "v.full || v.label" in vv


@pytest.mark.skipif(not _NODE, reason="node not on PATH")
def test_log_text_exports():
    out = _node(r"""
import vectors, { logText } from './static/js/voice_vectors.js';
console.log(JSON.stringify([logText(), typeof vectors.logText]));
""")
    assert out == ["No conversation events yet.", "function"]
