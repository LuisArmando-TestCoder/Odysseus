// voice-delta.js — say each sentence once.
//
// The chat agent's answer reaches GPT-Live in pieces while it streams. Those
// pieces used to be cut by CHARACTER OFFSET into the growing text. That breaks
// in three common cases, and each one made ALFRED re-say what it had already
// said, from the beginning:
//   1. the bubble re-renders (markdown, a table, self-talk filtered out), so
//      the old offset no longer points at the end of what was spoken;
//   2. an agent run opens a NEW bubble per round and the offset resets to 0,
//      while the new round restates the previous one;
//   3. the same answer arrives again through another path (narration, live
//      context, the finished-answer ingest, the reconnect seed).
//
// This ledger works on SENTENCES instead. Every sentence that was delivered is
// remembered (normalised), so a later batch only ever contributes sentences
// that were never delivered — each batch is ADDITIONAL context, not the whole
// thing again. Two channels:
//   - 'speak': said aloud to the user (implies known)
//   - 'know' : given to GPT-Live as silent context
// Something already spoken is never re-sent as context; something only known
// silently can still be spoken once.

export function normSentence(s) {
  return String(s || '')
    .normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[*_`#>|~\[\]()"“”'‘’«»·•\-–—:;,.!?¿¡…]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// Split into sentences. `complete` is false only for the trailing fragment
// that has no sentence end yet (it may still be streaming).
export function splitSentences(text) {
  const out = [];
  const src = String(text || '');
  const re = /[^\n.!?…]*(?:[.!?…]+["”’)\]]*(?=\s|$)|\n|$)/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    if (m[0] === '') { if (re.lastIndex >= src.length) break; re.lastIndex++; continue; }
    const raw = m[0];
    const t = raw.trim();
    if (!t) continue;
    const ended = /[.!?…]["”’)\]]*$/.test(t) || /\n$/.test(raw) || re.lastIndex < src.length;
    out.push({ text: t, complete: ended });
  }
  return out;
}

export function createLedger({ max = 5000 } = {}) {
  const spoken = new Map();   // norm → true (insertion-ordered for trimming)
  const known = new Map();
  const partials = [];        // raw text of trailing fragments delivered with final=true

  function _trim(map) { while (map.size > max) map.delete(map.keys().next().value); }
  function _seen(n, channel) { return channel === 'speak' ? spoken.has(n) : (known.has(n) || spoken.has(n)); }
  function _mark(n, channel) {
    if (channel === 'speak') { spoken.set(n, true); _trim(spoken); }
    known.set(n, true); _trim(known);
  }

  // The sentences of `text` not yet delivered on `channel`, joined back with
  // the text's own line structure. final=false holds back a trailing fragment.
  function _collect(text, channel, final) {
    const lines = [];
    const fresh = [];
    for (const s of splitSentences(text)) {
      if (!s.complete && !final) break;
      let piece = s.text;
      let n = normSentence(piece);
      if (!n) continue;
      // A fragment delivered earlier that this sentence completes: send only the rest.
      const p = partials.find(x => piece.startsWith(x) && piece.length > x.length);
      if (p) { piece = piece.slice(p.length).trim(); n = normSentence(piece); if (!n) continue; }
      if (_seen(n, channel) || fresh.some(f => f.n === n)) continue;
      fresh.push({ n, piece, complete: s.complete });
      lines.push(piece);
    }
    return { fresh, out: lines.join(' ').replace(/\s+/g, ' ').trim() };
  }

  return {
    peek(text, channel = 'speak', final = false) { return _collect(text, channel, final).out; },
    take(text, channel = 'speak', final = false) {
      const { fresh, out } = _collect(text, channel, final);
      for (const f of fresh) {
        _mark(f.n, channel);
        if (!f.complete) partials.push(f.piece);
      }
      if (partials.length > 50) partials.splice(0, partials.length - 50);
      return out;
    },
    markKnown(text) { for (const s of splitSentences(text)) { const n = normSentence(s.text); if (n) _mark(n, 'know'); } },
    markSpoken(text) { for (const s of splitSentences(text)) { const n = normSentence(s.text); if (n) _mark(n, 'speak'); } },
    // A new GPT-Live session forgets silent context but the user still heard
    // what was spoken: forget 'know', keep 'speak'.
    resetKnown() { known.clear(); for (const k of spoken.keys()) known.set(k, true); },
    reset() { spoken.clear(); known.clear(); partials.length = 0; },
    size() { return { spoken: spoken.size, known: known.size }; },
  };
}

export default createLedger;
