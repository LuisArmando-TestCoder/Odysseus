// voice.js — Real-time voice conversation via OpenAI Realtime API (WebRTC),
// with a FFT-driven fluid shader. The mic streams continuously; OpenAI's
// server-side VAD handles turn-taking and barge-in, so it feels like a phone
// call — no record-then-send.

let _micStream = null;
let _audioCtx = null;
let _analyser = null;
let _pc = null;
let _dc = null;
let _audioEl = null;
let _active = false;

// ── Reasoning → chat queue ──────────────────────────────────────────────────
let _transcriptBuffer = [];   // {speaker, text} not yet sent to the reasoner
let _reasonDebounce = null;
let _reasoning = false;
let _queue = [];              // chat messages awaiting dispatch
let _dispatcherTimer = null;
let _lastReasonAt = 0;

let _canvas = null;
let _gl = null;
let _raf = 0;
let _fftLoc = null;
let _timeLoc = null;
let _resLoc = null;
let _freqData = null;
let _t0 = 0;

const BANDS = 64;

function _apiBase() {
  return window.location.origin;
}

// ── Shader sources ──────────────────────────────────────────────────────────

const VERT = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

const FRAG = `
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform float u_fft[BANDS];

float hash(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float noise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i + vec3(0,0,0)), hash(i + vec3(1,0,0)), f.x),
                 mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x),
                 mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
}
float fbm(vec3 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 5; i++) {
    v += a * noise(p);
    p = p * 2.02 + vec3(11.0);
    a *= 0.5;
  }
  return v;
}
void main() {
  vec2 uv = (2.0 * gl_FragCoord.xy - u_res) / min(u_res.x, u_res.y);
  float r = length(uv);
  if (r > 1.0) discard;

  float bass = 0.0, mid = 0.0, hi = 0.0;
  for (int i = 0; i < BANDS; i++) {
    float v = u_fft[i];
    if (i < 4) bass += v;
    else if (i < 24) mid += v;
    else hi += v;
  }
  bass = clamp(bass * 0.25, 0.0, 1.0);
  mid = clamp(mid * 0.05, 0.0, 1.0);
  hi = clamp(hi * 0.02, 0.0, 1.0);

  float t = u_time * 0.35;
  vec3 p = vec3(uv * (1.6 + 1.8 * bass), t);
  float n = fbm(p);
  float n2 = fbm(vec3(uv * 3.4, t * 1.3) + vec3(7.0));
  float n3 = fbm(vec3(uv * 6.2, -t * 0.9) + vec3(13.0));

  float m = 0.55 * n + 0.3 * n2 + 0.15 * n3;
  float flow = sin(uv.y * 8.0 + u_time * (1.0 + bass * 3.0)) * 0.5 + 0.5;

  vec3 deep = vec3(0.04, 0.08, 0.16);
  vec3 teal = vec3(0.08, 0.45, 0.5);
  vec3 violet = vec3(0.45, 0.2, 0.6);
  vec3 cyan = vec3(0.3, 0.9, 0.95);

  vec3 col = mix(deep, teal, smoothstep(0.1, 0.9, m));
  col = mix(col, violet, mid * smoothstep(0.2, 0.8, n2));
  col = mix(col, cyan, hi * smoothstep(0.3, 0.9, n3));
  col += flow * vec3(0.02, 0.04, 0.06) * (0.5 + bass);
  col *= smoothstep(1.0, 0.82, r);
  col += vec3(0.02, 0.05, 0.08) * (0.4 + 0.6 * bass) * (1.0 - r * 0.6);

  gl_FragColor = vec4(col, 1.0);
}
`;

// ── WebGL ───────────────────────────────────────────────────────────────────

function _compile(type, src) {
  const s = _gl.createShader(type);
  _gl.shaderSource(s, src);
  _gl.compileShader(s);
  if (!_gl.getShaderParameter(s, _gl.COMPILE_STATUS)) {
    console.error('shader error', _gl.getShaderInfoLog(s));
    return null;
  }
  return s;
}

function _show(canvas) {
  _canvas = canvas;
  _gl = _canvas.getContext('webgl', { antialias: true, alpha: true });
  const vs = _compile(_gl.VERTEX_SHADER, VERT);
  const fs = _compile(_gl.FRAGMENT_SHADER, FRAG);
  const prog = _gl.createProgram();
  _gl.attachShader(prog, vs);
  _gl.attachShader(prog, fs);
  _gl.linkProgram(prog);
  _gl.useProgram(prog);

  const buf = _gl.createBuffer();
  _gl.bindBuffer(_gl.ARRAY_BUFFER, buf);
  _gl.bufferData(_gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), _gl.STATIC_DRAW);
  const loc = _gl.getAttribLocation(prog, 'a_pos');
  _gl.enableVertexAttribArray(loc);
  _gl.vertexAttribPointer(loc, 2, _gl.FLOAT, false, 0, 0);

  _fftLoc = _gl.getUniformLocation(prog, 'u_fft');
  _timeLoc = _gl.getUniformLocation(prog, 'u_time');
  _resLoc = _gl.getUniformLocation(prog, 'u_res');
  _freqData = new Uint8Array(BANDS);
  _t0 = performance.now();

  _raf = requestAnimationFrame(_loop);
}

function _loop() {
  if (!_gl) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(_canvas.clientWidth * dpr);
  const h = Math.round(_canvas.clientHeight * dpr);
  if (_canvas.width !== w || _canvas.height !== h) {
    _canvas.width = w; _canvas.height = h;
    _gl.viewport(0, 0, w, h);
  }

  if (_analyser) {
    const data = new Uint8Array(_analyser.frequencyBinCount);
    _analyser.getByteFrequencyData(data);
    const bin = Math.max(1, Math.floor(data.length / BANDS));
    for (let i = 0; i < BANDS; i++) {
      let acc = 0, n = 0;
      for (let j = 0; j < bin; j++) {
        const idx = i * bin + j;
        if (idx < data.length) { acc += data[idx]; n++; }
      }
      _freqData[i] = (acc / Math.max(1, n)) / 255;
    }
  }

  _gl.uniform2f(_resLoc, w, h);
  _gl.uniform1f(_timeLoc, (performance.now() - _t0) / 1000);
  _gl.uniform1fv(_fftLoc, _freqData);
  _gl.drawArrays(_gl.TRIANGLES, 0, 3);

  _raf = requestAnimationFrame(_loop);
}

function _hide() {
  if (_raf) { cancelAnimationFrame(_raf); _raf = 0; }
  _canvas = null; _gl = null;
}

function _cleanup() {
  if (_micStream) { _micStream.getTracks().forEach(t => t.stop()); _micStream = null; }
  if (_audioCtx) { _audioCtx.close().catch(() => {}); _audioCtx = null; }
  if (_audioEl) { _audioEl.pause(); _audioEl.srcObject = null; _audioEl = null; }
  _analyser = null; _pc = null; _dc = null;
}

// ── Status / transcript UI ──────────────────────────────────────────────────

function _setStatus(text) {
  const el = document.getElementById('doc-voice-status');
  if (el) el.textContent = text;
}

function _appendTranscript(speaker, text) {
  if (!text || !text.trim()) return;
  const el = document.getElementById('doc-voice-transcript');
  if (el) {
    const line = document.createElement('div');
    line.className = 'voice-line';
    line.innerHTML = `<b>${speaker}:</b> <span></span>`;
    line.querySelector('span').textContent = text.trim();
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
    while (el.children.length > 40) el.removeChild(el.firstChild);
  }
  // Accumulate for the reasoning agent.
  _transcriptBuffer.push({ speaker, text: text.trim() });
  if (_transcriptBuffer.length > 60) _transcriptBuffer.splice(0, _transcriptBuffer.length - 60);
  _scheduleReason();
}

// ── Reasoning agent: decide what to send to the chat ────────────────────────

function _scheduleReason() {
  clearTimeout(_reasonDebounce);
  _reasonDebounce = setTimeout(_maybeReason, 4000); // wait for a quiet gap
}

async function _maybeReason() {
  if (_reasoning || !_transcriptBuffer.length) return;
  _reasoning = true;
  const lines = _transcriptBuffer
    .map(l => (l.speaker === 'You' ? 'User' : 'Assistant') + ': ' + l.text)
    .join('\n');
  _transcriptBuffer = []; // process new turns next time
  try {
    const r = await fetch(`${_apiBase()}/api/realtime/reason`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript: lines }),
      credentials: 'same-origin',
    });
    const data = await r.json().catch(() => ({}));
    if (data && data.send && data.message) {
      _queue.push(data.message);
      _setStatus(`${_queue.length} message(s) queued for the chat`);
      _dispatch();
    }
  } catch (e) {
    console.error('reason-to-chat failed', e);
  } finally {
    _reasoning = false;
    _lastReasonAt = Date.now();
  }
}

// ── Dispatcher: send queued messages one-at-a-time when the chat is idle ────

function _dispatch() {
  if (_dispatcherTimer) return;
  _dispatcherTimer = setInterval(() => {
    if (!_queue.length) { clearInterval(_dispatcherTimer); _dispatcherTimer = null; return; }
    const sid = window.sessionModule && window.sessionModule.getCurrentSessionId && window.sessionModule.getCurrentSessionId();
    const busy = window.chatModule && window.chatModule.hasActiveStream && window.chatModule.hasActiveStream(sid);
    if (busy) return;
    const msg = _queue.shift();
    _sendToChat(msg);
    _setStatus(_queue.length ? `${_queue.length} message(s) still queued` : 'Sent to chat');
  }, 1200);
}

function _sendToChat(text) {
  const input = document.querySelector('#message, textarea#message');
  if (input) {
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
  const sendBtn = document.querySelector('.send-btn, #send-btn');
  if (sendBtn) sendBtn.click();
  else if (window.chatModule && window.chatModule.handleChatSubmit) window.chatModule.handleChatSubmit();
}

// ── Realtime event handling ─────────────────────────────────────────────────

function _handleEvent(evt) {
  switch (evt.type) {
    case 'session.started':
      _setStatus('Connected — listening… (talk to interrupt)');
      break;
    case 'session.closed':
      _setStatus('Conversation ended — click the mic to restart');
      break;
    case 'user_transcription_text':
      _appendTranscript('You', evt.text || evt.transcript || '');
      break;
    case 'live_captioning_text':
      _appendTranscript('AI', evt.text || evt.delta || '');
      break;
    case 'conversation.item.input_audio_transcription.completed':
      _appendTranscript('You', evt.transcript || (evt.item && evt.item.content && evt.item.content[0] && evt.item.content[0].transcript) || '');
      break;
    case 'response.output_audio_transcript.delta':
    case 'response.audio_transcript.delta':
      _appendTranscript('AI', evt.delta || '');
      break;
    case 'response.output_text.delta':
    case 'response.text.delta':
      _appendTranscript('AI', evt.delta || '');
      break;
    case 'response.done':
      _setStatus('Listening… (talk to interrupt)');
      break;
    case 'error':
      console.error('realtime error', evt);
      if (window.uiModule) window.uiModule.showError('Voice error: ' + (evt.error && evt.error.message || 'unknown'));
      break;
  }
}

// ── Start / stop ────────────────────────────────────────────────────────────

async function startVoice(canvas) {
  if (_active) return;

  try {
    _micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    console.error('mic denied', e);
    if (window.uiModule) window.uiModule.showError('Microphone access denied');
    return;
  }

  _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const src = _audioCtx.createMediaStreamSource(_micStream);
  _analyser = _audioCtx.createAnalyser();
  _analyser.fftSize = 512;
  src.connect(_analyser);

  try {
    _pc = new RTCPeerConnection();
    _pc.addTrack(_micStream.getAudioTracks()[0], _micStream);

    _pc.ontrack = (e) => {
      _audioEl = new Audio();
      _audioEl.autoplay = true;
      _audioEl.srcObject = e.streams[0];
      _audioEl.play().catch(() => {});
    };

    _dc = _pc.createDataChannel('oai-events');
    _dc.onopen = () => {
      _setStatus('Connected — listening… (talk to interrupt)');
    };
    _dc.onmessage = (e) => { try { _handleEvent(JSON.parse(e.data)); } catch (err) {} };

    const offer = await _pc.createOffer();
    await _pc.setLocalDescription(offer);

    // Wait for ICE candidate gathering so the SDP offer is complete.
    if (_pc.iceGatheringState !== 'complete') {
      await new Promise((resolve) => {
        const timeout = setTimeout(resolve, 3000);
        const onState = () => {
          if (_pc.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); }
        };
        _pc.addEventListener('icegatheringstatechange', onState);
        onState();
      });
    }
    const sdp = (_pc.localDescription && _pc.localDescription.sdp) || offer.sdp;

    // Proxy the SDP offer through our backend (CSP-friendly; key stays server-side).
    const resp = await fetch(
      `${_apiBase()}/api/realtime/session`,
      {
        method: 'POST',
        body: JSON.stringify({ sdp }),
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
      }
    );
    if (!resp.ok) {
      const txt = await resp.text().catch(() => '');
      throw new Error('GPT-Live SDP: HTTP ' + resp.status + ' ' + txt.slice(0, 200));
    }
    const result = await resp.json();
    const answerSdp = result && result.transport && result.transport.sdp;
    if (!answerSdp) throw new Error('No SDP answer from GPT-Live');
    await _pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
  } catch (e) {
    console.error('realtime connect failed', e);
    _cleanup();
    if (window.uiModule) window.uiModule.showError('Voice connect failed: ' + e.message);
    return;
  }

  _active = true;
  _show(canvas);
  _setStatus('Listening… (talk over it to interrupt)');
}

async function stopVoice() {
  if (!_active) return;
  _active = false;
  try { if (_dc) _dc.close(); } catch (e) {}
  try { if (_pc) _pc.close(); } catch (e) {}
  _hide();
  _cleanup();
  _setStatus('Click the mic to talk');
}

// ── TTS (browser, male British) — retained for plain read-aloud ────────────

export function speak(text, opts = {}) {
  if (!('speechSynthesis' in window)) return false;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const voices = window.speechSynthesis.getVoices();
  const male = /male|daniel|george|arthur|oliver|ryan|james/i;
  u.voice =
    voices.find(v => /^en[-_]GB/i.test(v.lang) && male.test(v.name)) ||
    voices.find(v => /^en[-_]GB/i.test(v.lang)) ||
    voices.find(v => /^en/i.test(v.lang) && male.test(v.name)) ||
    voices.find(v => /^en/i.test(v.lang)) || null;
  u.lang = u.voice?.lang || 'en-GB';
  u.pitch = opts.pitch ?? 0.9;
  u.rate = opts.rate ?? 1.0;
  window.speechSynthesis.speak(u);
  return true;
}

// ── Public API ──────────────────────────────────────────────────────────────

export function isRecording() { return _active; }

export async function toggleVoice(canvas) {
  if (_active) { await stopVoice(); return; }
  await startVoice(canvas);
}

export function initVoice() {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
  }
  document.body.addEventListener('click', (e) => {
    if (e.target.closest('#voice-btn')) {
      import('./document.js').then(m => {
        if (m.openPanel) m.openPanel();
        if (m.switchToDoc) m.switchToDoc('__voice__');
      }).catch(() => {});
      return;
    }
    if (e.target.closest('#doc-voice-mic-btn')) {
      toggleVoice(document.getElementById('voice-canvas'));
    }
  });
}

export default { speak, isRecording, toggleVoice, initVoice };
