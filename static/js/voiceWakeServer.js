// voiceWakeServer.js — server-backed recogniser for the wake phrase.
//
// Chrome's Web Speech API streams audio to Google; when that service can't be
// reached it fails with error "network" on every attempt. This class speaks
// the same interface (start/stop/abort, onresult/onerror/onend) but records
// the mic locally, cuts it into utterances with an energy VAD, and sends ONLY
// the spoken clips to Odysseus's own /api/stt/transcribe (whatever STT
// provider the server is configured with). Silence never leaves the browser.
//
// Bounded: one request in flight + at most one waiting; a clip is capped at
// MAX_UTTER_MS; idle recorder segments are recycled so blobs stay small.

const FRAME = 2048;               // ScriptProcessor buffer (runs on the audio thread, not throttled in background tabs)
const MIN_SPEECH_MS = 350;        // shorter blips are noise
const END_SILENCE_MS = 700;       // this much quiet ends an utterance
const MAX_UTTER_MS = 8000;        // hard cap per clip
const IDLE_RECYCLE_MS = 6000;     // restart the recorder while idle so clips start near speech
const MIN_RMS = 0.012;

export function makeServerRecognition({
  endpoint = '/api/stt/transcribe',
  fetchImpl = (...a) => fetch(...a),
  log = (...a) => { try { console.info('[wake/server]', ...a); } catch (e) { /* ignore */ } },
} = {}) {
  return class ServerRecognition {
    constructor() {
      this.continuous = true;
      this.interimResults = false;
      this.maxAlternatives = 1;
      this.lang = 'en-US';
      this.onresult = this.onerror = this.onend = null;
      this._on = false;
    }

    _emitError(error, message = '') { try { this.onerror?.({ error, message }); } catch (e) { /* ignore */ } }
    _emitEnd() { try { this.onend?.(); } catch (e) { /* ignore */ } }

    start() {
      if (this._on) return;
      this._on = true;
      this._boot().catch((e) => {
        const name = e?.name || '';
        this._emitError(name === 'NotAllowedError' || name === 'SecurityError' ? 'not-allowed' : 'audio-capture', e?.message || name);
        this._shutdown(true);
      });
    }

    async _boot() {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (!this._on) { stream.getTracks().forEach(t => t.stop()); return; }
      this._stream = stream;
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this._ctx = new Ctx();
      try { await this._ctx.resume(); } catch (e) { /* ignore */ }
      const src = this._ctx.createMediaStreamSource(stream);
      const proc = this._ctx.createScriptProcessor(FRAME, 1, 1);
      const mute = this._ctx.createGain(); mute.gain.value = 0;
      src.connect(proc); proc.connect(mute); mute.connect(this._ctx.destination);
      this._proc = proc;

      this._mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']
        .find(m => window.MediaRecorder?.isTypeSupported?.(m)) || '';
      this._floor = 0.008;
      this._speaking = false;
      this._speechMs = 0;
      this._quietMs = 0;
      this._inflight = 0;
      this._waiting = null;
      this._newRecorder();

      const frameMs = (FRAME / this._ctx.sampleRate) * 1000;
      proc.onaudioprocess = (ev) => {
        if (!this._on) return;
        const d = ev.inputBuffer.getChannelData(0);
        let sum = 0;
        for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
        this._vad(Math.sqrt(sum / d.length), frameMs);
      };
      log('server STT wake engine on (' + (this._mime || 'default codec') + ')');
    }

    _newRecorder() {
      this._recStart = performance.now();
      try {
        const rec = new MediaRecorder(this._stream, this._mime ? { mimeType: this._mime } : undefined);
        // Each recorder owns its chunks: data arrives AFTER stop(), when the
        // next recorder is already running.
        const chunks = [];
        rec._chunks = chunks;
        rec.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
        rec.start();
        this._rec = rec;
      } catch (e) {
        this._emitError('audio-capture', e?.message || 'MediaRecorder failed');
        this._shutdown(true);
      }
    }

    // Stop the current recorder; if `send`, ship what it captured.
    _cut(send) {
      const rec = this._rec; if (!rec) return;
      const chunks = rec._chunks;
      this._rec = null;
      rec.onstop = () => {
        if (!send || !chunks.length) return;
        this._submit(new Blob(chunks, { type: rec.mimeType || this._mime || 'audio/webm' }));
      };
      try { rec.stop(); } catch (e) { /* ignore */ }
      if (this._on) this._newRecorder();
    }

    _vad(rms, frameMs) {
      const thresh = Math.max(MIN_RMS, this._floor * 3);
      const loud = rms > thresh;
      if (!this._speaking) {
        // Track the room's noise floor only while quiet.
        this._floor = this._floor * 0.95 + Math.min(rms, 0.05) * 0.05;
        if (loud) { this._speaking = true; this._speechMs = frameMs; this._quietMs = 0; }
        else if (performance.now() - this._recStart > IDLE_RECYCLE_MS) this._cut(false);
        return;
      }
      if (loud) { this._speechMs += frameMs; this._quietMs = 0; }
      else this._quietMs += frameMs;
      const elapsed = performance.now() - this._recStart;
      if (this._quietMs >= END_SILENCE_MS || elapsed >= MAX_UTTER_MS) {
        const real = this._speechMs >= MIN_SPEECH_MS;
        this._speaking = false; this._speechMs = 0; this._quietMs = 0;
        this._cut(real);
      }
    }

    _submit(blob) {
      if (this._inflight) { this._waiting = blob; return; }   // keep only the newest
      this._inflight = 1;
      const fd = new FormData();
      fd.append('file', blob, blob.type.includes('mp4') ? 'audio.mp4' : blob.type.includes('ogg') ? 'audio.ogg' : 'audio.webm');
      fetchImpl(endpoint, { method: 'POST', body: fd, credentials: 'same-origin' })
        .then(async (r) => {
          if (!r.ok) {
            const body = await r.text().catch(() => '');
            this._emitError(r.status === 503 ? 'service-not-allowed' : 'network', `server STT HTTP ${r.status} ${body.slice(0, 120)}`);
            return;
          }
          const text = ((await r.json().catch(() => ({})))?.text || '').trim();
          if (!text || !this._on) return;
          const res = [{ transcript: text, confidence: 1 }]; res.isFinal = true;
          try { this.onresult?.({ resultIndex: 0, results: [res] }); } catch (e) { /* ignore */ }
        })
        .catch((e) => this._emitError('network', e?.message || 'fetch failed'))
        .finally(() => {
          this._inflight = 0;
          const next = this._waiting; this._waiting = null;
          if (next && this._on) this._submit(next);
        });
    }

    _shutdown(emitEnd) {
      const was = this._on;
      this._on = false;
      try { if (this._proc) this._proc.onaudioprocess = null; } catch (e) { /* ignore */ }
      try { this._rec?.stop(); } catch (e) { /* ignore */ }
      this._rec = null; this._waiting = null;
      this._stream?.getTracks().forEach(t => t.stop());
      this._ctx?.close().catch(() => {});
      this._stream = this._ctx = this._proc = null;
      if (emitEnd && was) this._emitEnd();
    }

    stop() { this._shutdown(true); }
    abort() { this._shutdown(true); }
  };
}

export default { makeServerRecognition };
