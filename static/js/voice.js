// voice.js — Voice conversation: mic capture, FFT-driven fluid shader,
// OpenRouter STT (server-side), and browser TTS (male British).
//
// The mic feeds a Web Audio AnalyserNode; its frequency spectrum drives a
// WebGL fragment shader: a circularly-clipped disc of layered Perlin "purling"
// noise that flows like a fluid. Stop to transcribe (server) and send.

let _micStream = null;
let _recorder = null;
let _audioCtx = null;
let _analyser = null;
let _chunks = [];
let _recording = false;

let _overlay = null;
let _canvas = null;
let _gl = null;
let _raf = 0;
let _fftLoc = null;
let _timeLoc = null;
let _resLoc = null;
let _freqData = null;
let _t0 = performance.now();

const BANDS = 64;

function _apiBase() {
  return window.location.origin;
}

// ── Shader sources ──────────────────────────────────────────────────────────

const VERT = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

// FFT-driven, circularly-clipped layered value-noise "fluid".
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
  if (r > 1.0) discard;                       // circular clip

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

  // layered bands
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

  // soft rim + inner glow
  col *= smoothstep(1.0, 0.82, r);
  col += vec3(0.02, 0.05, 0.08) * (0.4 + 0.6 * bass) * (1.0 - r * 0.6);

  gl_FragColor = vec4(col, 1.0);
}
`;

// ── WebGL overlay ───────────────────────────────────────────────────────────

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
  _analyser = null; _recorder = null; _chunks = [];
}

// ── STT + send ──────────────────────────────────────────────────────────────

async function _transcribe(blob) {
  const fd = new FormData();
  fd.append('file', blob, 'audio.webm');
  try {
    const r = await fetch(`${_apiBase()}/api/stt/transcribe`, { method: 'POST', body: fd });
    const data = await r.json().catch(() => ({}));
    return (data.text || '').trim();
  } catch (e) {
    console.error('STT failed', e);
    return '';
  }
}

function _insertAndSend(text) {
  const input = document.querySelector('#message-input, textarea#message-input');
  if (input) {
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
  const sendBtn = document.querySelector('.send-btn, #send-btn');
  if (sendBtn) sendBtn.click();
  else if (window.chatModule && window.chatModule.handleChatSubmit) window.chatModule.handleChatSubmit();
}

// ── TTS (browser, male British) ─────────────────────────────────────────────

export function speak(text, opts = {}) {
  if (!('speechSynthesis' in window)) return false;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const voices = window.speechSynthesis.getVoices();
  const prefer = /en[-_]GB/i;
  const male = /male|daniel|george|arthur|oliver/i;
  u.voice =
    voices.find(v => prefer.test(v.lang) && male.test(v.name)) ||
    voices.find(v => prefer.test(v.lang)) ||
    voices.find(v => /en[-_]GB/i.test(v.lang)) ||
    voices.find(v => /^en/i.test(v.lang)) ||
    null;
  u.lang = u.voice?.lang || 'en-GB';
  u.pitch = opts.pitch ?? 0.9;
  u.rate = opts.rate ?? 1.0;
  window.speechSynthesis.speak(u);
  return true;
}

// ── Public toggle ───────────────────────────────────────────────────────────

export function isRecording() { return _recording; }

export async function toggleVoice(canvas) {
  if (_recording) { await stopVoice(); return; }
  await startVoice(canvas);
}

async function startVoice(canvas) {
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

  _recorder = new MediaRecorder(_micStream);
  _chunks = [];
  _recorder.ondataavailable = e => { if (e.data && e.data.size) _chunks.push(e.data); };
  _recorder.start();
  _recording = true;

  _show(canvas);
  const status = document.getElementById('doc-voice-status');
  if (status) status.textContent = 'Listening… tap the mic to stop';
  if (window.uiModule) window.uiModule.showToast('Listening — tap the mic to stop');
}

async function stopVoice() {
  if (!_recording || !_recorder) return;
  _recording = false;
  _recorder.onstop = async () => {
    const type = _recorder.mimeType || 'audio/webm';
    const blob = new Blob(_chunks, { type });
    _hide();
    _cleanup();
    const status = document.getElementById('doc-voice-status');
    if (status) status.textContent = 'Click the mic below to talk';
    const text = await _transcribe(blob);
    if (text) _insertAndSend(text);
    else if (window.uiModule) window.uiModule.showError('No speech detected');
  };
  _recorder.stop();
}

export function initVoice() {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
  }
  // Delegate: the voice canvas lives in the document panel (created lazily),
  // so bind at the body level and resolve the canvas at click time.
  document.body.addEventListener('click', (e) => {
    if (e.target.closest('#voice-btn')) {
      // Open the dedicated "Voice agent" tab in the documents panel.
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
