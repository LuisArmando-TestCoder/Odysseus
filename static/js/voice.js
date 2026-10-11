// voice.js — Odysseus Advanced Voice Mode.
//
// Full-screen, Jarvis-style voice HUD on top of GPT-Live (OpenAI realtime over
// WebRTC; the API key stays server-side). The chat keeps using whatever model
// the user selected — voice never replaces it, it *drives* it:
//
//   mic ─► GPT-Live (talks back, barge-in via server VAD)
//            │ transcripts
//            ▼
//   /api/realtime/pipeline  (SSE)
//     ① Observer   → is anything here worth acting on?
//     ② Compressor → hierarchical voice memory (highest layer, newest of every
//                    level, current raw batch) — quasi-infinite & reliable
//     ③ Decider    → JEV calibrated decisions (act / ready / duplicate / intent / urgency)
//     ④ Forger     → one self-contained chat message
//            │
//            ▼
//   queue ─► dispatched to the chat only when nothing is executing
//            └─► when the chat answers, GPT-Live speaks a short summary back.

const BANDS = 64;
const STATE_COLORS = {
  idle:       [0.36, 0.62, 0.78],
  connecting: [0.45, 0.70, 1.00],
  listening:  [0.30, 0.86, 1.00],
  user:       [0.35, 1.00, 0.82],
  speaking:   [1.00, 0.72, 0.32],
  muted:      [0.52, 0.56, 0.62],
  error:      [1.00, 0.36, 0.40],
};
const THINK_COLOR = [0.68, 0.48, 1.00];
const STATE_LABELS = {
  idle:       ['STANDBY', 'Tap the core or press Space to connect'],
  connecting: ['LINKING', 'Establishing neural link…'],
  listening:  ['LISTENING', 'Speak naturally — interrupt any time'],
  user:       ['HEARING YOU', 'Go on…'],
  speaking:   ['RESPONDING', 'Talk over me to interrupt'],
  muted:      ['MUTED', 'Press M to unmute'],
  error:      ['LINK FAILURE', 'Tap the core to retry'],
};
const STAGES = ['observe', 'compress', 'decide', 'forge'];
const INTENT_LABELS = {
  task: 'Task', question: 'Question', research: 'Research',
  remember: 'Remember', followup: 'Follow-up', none: 'None',
};

// ── Connection state ────────────────────────────────────────────────────────
let _micStream = null, _audioCtx = null, _micAnalyser = null, _outAnalyser = null;
let _pc = null, _dc = null, _audioEl = null;
let _active = false, _connecting = false, _muted = false;
let _state = 'idle';
let _callStart = 0, _timerInt = 0;

// ── Pipeline / queue state ──────────────────────────────────────────────────
// Never a shared key: until _bindVoiceToSession() runs, use an ephemeral
// per-page-load key so no chat can read another chat's voice memory.
let _voiceId = `pending-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
let _pending = [];            // turns not yet sent to the pipeline
let _pipeTimer = 0, _pipeBusy = false, _pipeRerun = false, _pipeAbort = null;
let _queue = [];              // {id, title, message, intent, urgency, status}
let _awaiting = null;         // {id, sid, sawBusy, since}
let _opts = { review: false, speak: true };
let _errors = [];             // {at, text} — every error surfaced in the HUD

// ── Chat mirror state ───────────────────────────────────────────────────────
let _chatObs = null, _chatTickInt = 0, _chatRenderT = 0;
let _costTick = 0, _costSid = undefined;
let _chatSid = null, _chatWasBusy = false;
let _injectOK = true, _lastInjectAt = 0;
let _respActive = false, _pendingBriefs = [];   // GPT-Live is mid-response → briefings wait
let _respSince = 0;          // when _respActive was set (guards against a missed response.done)
let _lastAudioAt = 0;        // last frame GPT-Live audio was audible (speech-queue pacing)

// ── Incremental narration ───────────────────────────────────────────────────
// While the chat is generating, read the growing AI message in batches and
// speak each new chunk aloud (commentary) so the voice never waits for the
// whole run to finish. Each batch tells GPT-Live whether we're still mid-run
// or finished, so it can keep narrating discoveries or give the final summary.
let _narratedLen = 0;        // watermark: chars of the streaming AI message already narrated
let _lastNarrateAt = 0;      // last commentary timestamp (throttle)
let _narrateActive = false;  // we are narrating a run in progress
let _narrateStallAt = 0;     // when the text last grew (stall detection)
let _narratedFinal = false;  // final summary already spoken by narration
let _narrateEl = null;       // bubble currently being narrated (agent runs span several)
let _stallSpoken = false;    // "let me research more" already said for the current pause

// ── Event diagnostics ───────────────────────────────────────────────────────
const _eventTypes = new Map();   // type → count
let _transcriptSeen = false, _diagT = 0;

// ── Transcript streaming ────────────────────────────────────────────────────
let _aiLine = null, _aiText = '', _aiFinalizeT = 0;
let _userLine = null, _userText = '', _userFinalizeT = 0;
let _userBase = 0;           // chars of the current user turn already shown in earlier (split) lines
let _userTurnOpen = false;   // a user turn is accumulating (survives line splits)
let _memPreview = '';        // top layer of the compressed voice memory (seeded into GPT-Live)
let _seedTries = 0;          // retries while waiting for the link to be fully active
let _notes = [];             // text the user typed/pasted as context (re-seeded on reconnect)
let _lastAiSaid = '';        // ALFRED's last real line — a reconnect resumes in that state
let _lastAiSaidAt = 0;
let _missed = [];            // chat answers that finished while the voice link was off
const _knownEls = new WeakSet();   // chat bubbles already accounted for
let _everWatched = false;

// ── Proactivity: ALFRED's promises become real chat requests ──────────────
let _lastForgeAt = 0;        // last time the pipeline forged a request (a promise right after is just an ack)
let _pendingCommit = null;   // a promise waiting for the pipeline to be free
let _lastCommitAt = 0;
let _lastUserFinalAt = 0;    // last finished user turn (its forge may still be on the way)

// ── Render (WebGL) ──────────────────────────────────────────────────────────
let _canvas = null, _gl = null, _raf = 0, _prog = null, _tex = null;
let _u = {};
let _fftBytes = new Uint8Array(BANDS);
let _tmpBands = new Uint8Array(BANDS);   // reused every frame — no per-frame allocation
let _t0 = performance.now();
let _col = STATE_COLORS.idle.slice();
let _lvl = 0, _out = 0, _think = 0, _thinkTarget = 0;
let _scale = 1, _scaleAt = 0, _lastFrameAt = 0;
let _bgCanvas = null, _bgGl = null, _bgProg = null, _bgTex = null, _bgU = {};
let _orbRect = null, _orbResizeBound = false;
const _reducedMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

let _root = null;
let _assistantName = 'ALFRED';
let _keyHandler = null;

const $ = (sel) => (_root ? _root.querySelector(sel) : null);
const _api = (p) => `${window.location.origin}${p}`;

// ═══════════════════════════════════════════════════════════════════════════
//  SHADERS — arc-reactor core
// ═══════════════════════════════════════════════════════════════════════════

const VERT = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

const FRAG = `
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform sampler2D u_fft;
uniform float u_level;
uniform float u_out;
uniform vec3 u_color;
uniform float u_think;
#define TAU 6.28318530718

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.03 + 7.1; a *= 0.5; }
  return v;
}
float fft(float x) { return texture2D(u_fft, vec2(clamp(x, 0.0, 1.0), 0.5)).r; }
float ring(float r, float rad, float w) { return smoothstep(w, 0.0, abs(r - rad)); }

void main() {
  vec2 uv = (2.0 * gl_FragCoord.xy - u_res) / min(u_res.x, u_res.y);
  float r = length(uv);
  float a = atan(uv.y, uv.x);
  float an = a / TAU + 0.5;
  float t = u_time;
  float e = clamp(max(u_level, u_out), 0.0, 1.0);
  vec3 c = u_color;
  vec3 col = vec3(0.0);

  // ── plasma core ──
  float coreR = 0.19 + 0.07 * e + 0.012 * sin(t * 2.1);
  vec2 warp = vec2(fbm(uv * 3.0 + t * 0.25), fbm(uv * 3.0 - t * 0.21));
  float n = fbm(uv * 5.0 + warp * 1.6 + vec2(t * 0.4, -t * 0.3));
  float core = smoothstep(coreR, 0.0, r);
  col += c * core * (0.55 + 0.9 * n);
  col += vec3(1.0) * pow(core, 3.0) * (0.45 + 0.9 * e);
  col += c * exp(-r * r * 13.0) * (0.22 + 0.7 * e);
  col += c * ring(r, coreR + 0.035, 0.006) * (0.7 + 0.6 * e);

  // ── radial spectrum (mirrored, rotated 90° counter-clockwise) ──
  float anr = fract(an - 0.25);    // 0.25 of a turn = 90° CCW (screen space)
  float fx = abs(anr * 2.0 - 1.0);
  float s = fft(fx * 0.85);
  float seg = fract(anr * 120.0);
  float bar = smoothstep(0.12, 0.24, seg) * smoothstep(0.88, 0.76, seg);
  float r0 = 0.39;
  float r1 = r0 + 0.035 + s * 0.27;
  float inBar = step(r0, r) * smoothstep(r1, r1 - 0.012, r);
  col += mix(c, vec3(1.0), s * 0.55) * bar * inBar * (0.3 + 1.3 * s);
  col += c * ring(r, 0.375, 0.004) * 0.75;

  // ── instrument rings ──
  float tk = fract(an * 72.0);
  col += c * ring(r, 0.745, 0.012) * step(tk, 0.1) * 0.5;
  col += c * ring(r, 0.705, 0.0025) * 0.35;
  float arc1 = step(fract((an + t * 0.035) * 6.0), 0.62);
  col += c * ring(r, 0.805, 0.0065) * arc1 * 0.9;
  float arc2 = step(fract((an - t * 0.022) * 3.0), 0.33);
  col += c * ring(r, 0.875, 0.004) * arc2 * 0.65;
  float dash = step(fract((an + t * 0.07) * 48.0), 0.45);
  col += c * ring(r, 0.935, 0.003) * dash * 0.4;

  // ── thinking: radar sweep + orbiting satellites ──
  float sweep = fract(an - t * 0.33);
  float band = step(0.40, r) * step(r, 0.90);
  col += c * u_think * exp(-sweep * 9.0) * band * 0.55;
  for (int k = 0; k < 3; k++) {
    float fk = float(k);
    float ang = t * (1.2 + fk * 0.35) + fk * 2.094;
    vec2 p = (0.80 + fk * 0.035) * vec2(cos(ang), sin(ang));
    col += mix(c, vec3(1.0), 0.6) * u_think * smoothstep(0.03, 0.0, length(uv - p));
  }

  col += c * exp(-abs(r - 0.8) * 11.0) * 0.06 * (1.0 + e);
  col *= smoothstep(1.02, 0.95, r);
  col = 1.0 - exp(-col * 1.25);
  float alpha = clamp(max(col.r, max(col.g, col.b)) * 1.1, 0.0, 1.0);
  gl_FragColor = vec4(col, alpha);
}
`;

// Full-screen background shader — replaces the old CSS layers (grid + aurora +
// scan + vignette + halo) with a single GPU pass. Perlin noise (fbm) with
// time-based domain warp, impulsed by the FFT amplitude (texture), tinted to
// the accent colour, plus halo glow at the orb centre. Renders opaque over
// `.vx`'s radial-gradient fallback.
const BG_FRAG = `
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform sampler2D u_fft;
uniform float u_level;
uniform float u_out;
uniform vec3 u_color;
uniform vec2 u_orb;
uniform float u_orbR;
#define TAU 6.28318530718

float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }
float noise(vec2 p){
  vec2 i=floor(p), f=fract(p);
  f=f*f*(3.0-2.0*f);
  return mix(mix(hash(i), hash(i+vec2(1.0,0.0)), f.x),
             mix(hash(i+vec2(0.0,1.0)), hash(i+vec2(1.0,1.0)), f.x), f.y);
}
float fbm(vec2 p){
  float v=0.0, a=0.5;
  for(int i=0;i<5;i++){ v+=a*noise(p); p=p*2.03+7.1; a*=0.5; }
  return v;
}
float fft(float x){ return texture2D(u_fft, vec2(clamp(x,0.0,1.0),0.5)).r; }

vec3 palette(float t){
  vec3 a=vec3(0.5), b=vec3(0.5), c=vec3(1.0), d=vec3(0.263,0.416,0.557);
  return a + b*cos(TAU*(c*t+d));
}

void main(){
  vec2 frag = gl_FragCoord.xy;
  vec2 uv = (2.0*frag - u_res) / min(u_res.x, u_res.y);
  float t = u_time;
  float e = clamp(max(u_level, u_out), 0.0, 1.0);

  // Aurora: time-based fluid perlin noise (domain warp) + FFT impulse (addition)
  vec2 q = vec2(fbm(uv*1.7 + t*0.12), fbm(uv*1.7 - t*0.10));
  vec2 r = vec2(fbm(uv*1.7 + q*1.3 + vec2(1.7,9.2) + t*0.14),
                fbm(uv*1.7 + q*1.3 + vec2(8.3,2.8) - t*0.12));
  float n = fbm(uv*1.7 + r*1.5);

  float ang = atan(uv.y, uv.x)/TAU + 0.5;
  float radl = length(uv);
  float s = fft(abs(ang*2.0 - 1.0) * 0.85);
  float impulse = s * exp(-radl*2.4) * (0.35 + 0.85*e);

  float m = n + impulse;
  vec3 aur = palette(m*0.5 + t*0.04 + e*0.18);
  aur = mix(aur, u_color, 0.30 + 0.30*e);

  vec3 col = vec3(0.012, 0.026, 0.05) + aur * (0.16 + 0.30*e);

  // Grid + drift
  float gs = 46.0;
  vec2 g = frag/gs + vec2(t*0.03, t*0.03);
  vec2 gv = abs(fract(g - 0.5) - 0.5);
  float line = max(step(gv.x, 0.018), step(gv.y, 0.018));
  float gridMask = smoothstep(1.1, 0.15, radl);
  col += u_color * line * 0.04 * gridMask;

  // Scan lines
  float scan = 0.5 + 0.5*sin(frag.y * 1.5);
  col += vec3(0.03) * step(0.98, scan);

  // Sweep band
  float sw = fract(t*0.14);
  float band = smoothstep(0.025, 0.0, abs(frag.y/u_res.y - (1.0 - sw)));
  col += u_color * band * 0.05;

  // Halo glow at the orb (energy-scaled)
  float d = length(frag - u_orb);
  float halo = exp(-(d*d) / max(0.0001, 1.5*u_orbR*u_orbR));
  col += u_color * halo * (0.09 + 0.22*e);

  // Vignette
  col *= smoothstep(1.65, 0.25, radl);

  gl_FragColor = vec4(col, 1.0);
}
`;

function _compile(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    console.error('voice shader error', gl.getShaderInfoLog(s));
    return null;
  }
  return s;
}

function _initGL(canvas) {
  _canvas = canvas;
  _gl = canvas.getContext('webgl', { antialias: true, alpha: true, premultipliedAlpha: true });
  if (!_gl) return false;
  const vs = _compile(_gl, _gl.VERTEX_SHADER, VERT);
  const fs = _compile(_gl, _gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) { _gl = null; return false; }
  _prog = _gl.createProgram();
  _gl.attachShader(_prog, vs);
  _gl.attachShader(_prog, fs);
  _gl.linkProgram(_prog);
  _gl.useProgram(_prog);

  const buf = _gl.createBuffer();
  _gl.bindBuffer(_gl.ARRAY_BUFFER, buf);
  _gl.bufferData(_gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), _gl.STATIC_DRAW);
  const loc = _gl.getAttribLocation(_prog, 'a_pos');
  _gl.enableVertexAttribArray(loc);
  _gl.vertexAttribPointer(loc, 2, _gl.FLOAT, false, 0, 0);

  _tex = _gl.createTexture();
  _gl.bindTexture(_gl.TEXTURE_2D, _tex);
  _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_MIN_FILTER, _gl.LINEAR);
  _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_MAG_FILTER, _gl.LINEAR);
  _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_WRAP_S, _gl.CLAMP_TO_EDGE);
  _gl.texParameteri(_gl.TEXTURE_2D, _gl.TEXTURE_WRAP_T, _gl.CLAMP_TO_EDGE);
  _gl.texImage2D(_gl.TEXTURE_2D, 0, _gl.LUMINANCE, BANDS, 1, 0, _gl.LUMINANCE, _gl.UNSIGNED_BYTE, _fftBytes);

  for (const n of ['u_res', 'u_time', 'u_fft', 'u_level', 'u_out', 'u_color', 'u_think']) {
    _u[n] = _gl.getUniformLocation(_prog, n);
  }
  _gl.uniform1i(_u.u_fft, 0);
  _gl.enable(_gl.BLEND);
  _gl.blendFunc(_gl.ONE, _gl.ONE_MINUS_SRC_ALPHA);
  return true;
}

function _measureOrb() {
  const wrap = $('.vx-orb-wrap');
  if (!wrap) { _orbRect = null; return; }
  const r = wrap.getBoundingClientRect();
  if (!r.width || !r.height) { _orbRect = null; return; }
  _orbRect = { cx: r.left + r.width / 2, cy: r.top + r.height / 2, radius: Math.min(r.width, r.height) / 2 };
}

function _initBG(canvas) {
  if (!canvas) return false;
  _bgCanvas = canvas;
  _bgGl = canvas.getContext('webgl', { antialias: false, alpha: false, depth: false, stencil: false });
  if (!_bgGl) return false;
  const vs = _compile(_bgGl, _bgGl.VERTEX_SHADER, VERT);
  const fs = _compile(_bgGl, _bgGl.FRAGMENT_SHADER, BG_FRAG);
  if (!vs || !fs) { _bgGl = null; return false; }
  _bgProg = _bgGl.createProgram();
  _bgGl.attachShader(_bgProg, vs);
  _bgGl.attachShader(_bgProg, fs);
  _bgGl.linkProgram(_bgProg);
  _bgGl.useProgram(_bgProg);

  const buf = _bgGl.createBuffer();
  _bgGl.bindBuffer(_bgGl.ARRAY_BUFFER, buf);
  _bgGl.bufferData(_bgGl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), _bgGl.STATIC_DRAW);
  const loc = _bgGl.getAttribLocation(_bgProg, 'a_pos');
  _bgGl.enableVertexAttribArray(loc);
  _bgGl.vertexAttribPointer(loc, 2, _bgGl.FLOAT, false, 0, 0);

  _bgTex = _bgGl.createTexture();
  _bgGl.bindTexture(_bgGl.TEXTURE_2D, _bgTex);
  _bgGl.texParameteri(_bgGl.TEXTURE_2D, _bgGl.TEXTURE_MIN_FILTER, _bgGl.LINEAR);
  _bgGl.texParameteri(_bgGl.TEXTURE_2D, _bgGl.TEXTURE_MAG_FILTER, _bgGl.LINEAR);
  _bgGl.texParameteri(_bgGl.TEXTURE_2D, _bgGl.TEXTURE_WRAP_S, _bgGl.CLAMP_TO_EDGE);
  _bgGl.texParameteri(_bgGl.TEXTURE_2D, _bgGl.TEXTURE_WRAP_T, _bgGl.CLAMP_TO_EDGE);
  _bgGl.texImage2D(_bgGl.TEXTURE_2D, 0, _bgGl.LUMINANCE, BANDS, 1, 0, _bgGl.LUMINANCE, _bgGl.UNSIGNED_BYTE, _fftBytes);

  for (const n of ['u_res', 'u_time', 'u_fft', 'u_level', 'u_out', 'u_color', 'u_orb', 'u_orbR']) {
    _bgU[n] = _bgGl.getUniformLocation(_bgProg, n);
  }
  _bgGl.uniform1i(_bgU.u_fft, 0);
  return true;
}

function _drawBG(now) {
  if (!_bgGl || !_bgCanvas) return;
  const dprB = Math.min(window.devicePixelRatio || 1, 1.25) * _scale;
  const cw = _bgCanvas.clientWidth, ch = _bgCanvas.clientHeight;
  const w = Math.max(2, Math.round(cw * dprB));
  const h = Math.max(2, Math.round(ch * dprB));
  if (_bgCanvas.width !== w || _bgCanvas.height !== h) {
    _bgCanvas.width = w; _bgCanvas.height = h;
    _bgGl.viewport(0, 0, w, h);
  }
  const t = _reducedMotion ? 0.0 : (now - _t0) / 1000;
  _bgGl.clearColor(0.01, 0.02, 0.05, 1.0);
  _bgGl.clear(_bgGl.COLOR_BUFFER_BIT);
  _bgGl.activeTexture(_bgGl.TEXTURE0);
  _bgGl.bindTexture(_bgGl.TEXTURE_2D, _bgTex);
  _bgGl.texSubImage2D(_bgGl.TEXTURE_2D, 0, 0, 0, BANDS, 1, _bgGl.LUMINANCE, _bgGl.UNSIGNED_BYTE, _fftBytes);
  _bgGl.uniform2f(_bgU.u_res, w, h);
  _bgGl.uniform1f(_bgU.u_time, t);
  _bgGl.uniform1f(_bgU.u_level, _lvl);
  _bgGl.uniform1f(_bgU.u_out, _out);
  _bgGl.uniform3f(_bgU.u_color, _col[0], _col[1], _col[2]);
  let ox = w * 0.5, oy = h * 0.5, orr = Math.min(w, h) * 0.16;
  if (_orbRect && cw > 0 && ch > 0) {
    const sx = w / cw, sy = h / ch;
    ox = _orbRect.cx * sx;
    oy = _orbRect.cy * sy;
    orr = _orbRect.radius * ((sx + sy) * 0.5);
  }
  _bgGl.uniform2f(_bgU.u_orb, ox, oy);
  _bgGl.uniform1f(_bgU.u_orbR, orr);
  _bgGl.drawArrays(_bgGl.TRIANGLES, 0, 3);
}

function _readBands(analyser, into, mix) {
  if (!analyser) return 0;
  const data = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(data);
  // Voice energy lives low — use the bottom ~60% of bins.
  const usable = Math.floor(data.length * 0.6);
  const bin = Math.max(1, Math.floor(usable / BANDS));
  let sum = 0;
  for (let i = 0; i < BANDS; i++) {
    let acc = 0;
    for (let j = 0; j < bin; j++) acc += data[i * bin + j] || 0;
    const v = acc / bin;
    into[i] = mix ? Math.max(into[i], v) : v;
    sum += v;
  }
  return Math.min(1, (sum / BANDS) / 110);
}

function _loop() {
  _raf = requestAnimationFrame(_loop);
  const now = performance.now();

  // Adaptive frame rate: if the last frame took too long, drop the render
  // scale (and thus the pixel count) until it recovers.
  const dt = now - _lastFrameAt;
  _lastFrameAt = now;
  if (now - _scaleAt > 2000) {
    if (dt > 26) _scale = Math.max(0.5, _scale - 0.1);
    else if (dt < 14 && _scale < 1) _scale = Math.min(1, _scale + 0.05);
    _scaleAt = now;
  }

  // Audio analysis (also drives the state when events are sparse).
  // Clear the reused scratch buffer every frame — the output pass merges with
  // Math.max, so without this a muted/missing mic left the previous peak in
  // place and the bars never fell back.
  _tmpBands.fill(0);
  const micLvl = _muted ? 0 : _readBands(_micAnalyser, _tmpBands, false);
  const outLvl = _readBands(_outAnalyser, _tmpBands, true);
  for (let i = 0; i < BANDS; i++) _fftBytes[i] = Math.round(_fftBytes[i] * 0.55 + _tmpBands[i] * 0.45);
  _lvl += (micLvl - _lvl) * 0.25;
  _out += (outLvl - _out) * 0.25;
  if (_out > 0.07) _lastAudioAt = Date.now();   // wall clock — compared with Date.now() in _drainSay
  _think += (_thinkTarget - _think) * 0.06;

  if (_active && !_muted) {
    const next = _out > 0.07 ? 'speaking' : (_lvl > 0.11 ? 'user' : 'listening');
    if (next !== _state) _setState(next);
  }

  const target = STATE_COLORS[_state] || STATE_COLORS.idle;
  for (let i = 0; i < 3; i++) {
    const want = target[i] * (1 - _think * 0.6) + THINK_COLOR[i] * _think * 0.6;
    _col[i] += (want - _col[i]) * 0.07;
  }

  // Background + accent colour live on the GPU now — no per-frame CSS custom
  // property writes (which forced a style recalc + repaint of the whole HUD).
  // Accent colour is set only on state changes in _setState.
  _drawBG(now);

  if (!_gl || !_canvas) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2) * _scale;
  const w = Math.round(_canvas.clientWidth * dpr);
  const h = Math.round(_canvas.clientHeight * dpr);
  if (!w || !h) return;
  if (_canvas.width !== w || _canvas.height !== h) {
    _canvas.width = w; _canvas.height = h;
    _gl.viewport(0, 0, w, h);
  }
  _gl.clearColor(0, 0, 0, 0);
  _gl.clear(_gl.COLOR_BUFFER_BIT);
  _gl.activeTexture(_gl.TEXTURE0);
  _gl.bindTexture(_gl.TEXTURE_2D, _tex);
  _gl.texSubImage2D(_gl.TEXTURE_2D, 0, 0, 0, BANDS, 1, _gl.LUMINANCE, _gl.UNSIGNED_BYTE, _fftBytes);
  _gl.uniform2f(_u.u_res, w, h);
  _gl.uniform1f(_u.u_time, _reducedMotion ? 0.0 : (now - _t0) / 1000);
  _gl.uniform1f(_u.u_level, _lvl);
  _gl.uniform1f(_u.u_out, _out);
  _gl.uniform3f(_u.u_color, _col[0], _col[1], _col[2]);
  _gl.uniform1f(_u.u_think, _think);
  _gl.drawArrays(_gl.TRIANGLES, 0, 3);
}

// ═══════════════════════════════════════════════════════════════════════════
//  HUD
// ═══════════════════════════════════════════════════════════════════════════

const ICON = {
  close: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  mic: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><line x1="12" y1="19" x2="12" y2="22"/></svg>',
  micOff: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="2" x2="22" y2="22"/><path d="M18.9 12.9A7 7 0 0 0 19 10"/><path d="M15 9.3V5a3 3 0 0 0-5.7-1.3"/><path d="M9 9v3a3 3 0 0 0 5.1 2.1"/><path d="M5 10a7 7 0 0 0 11.3 5.5"/><line x1="12" y1="19" x2="12" y2="22"/></svg>',
  power: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>',
  send: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  edit: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
  eye: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
  speak: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a10 10 0 0 1 0 14"/></svg>',
};

const STAGE_META = {
  observe:  ['Observe',  'Attention gate'],
  compress: ['Compress', 'Memory spine'],
  decide:   ['Decide',   'JEV decisions'],
  forge:    ['Forge',    'Chat message'],
};

function _gauge(key, label) {
  return `<div class="vx-gauge" data-g="${key}">
    <svg viewBox="0 0 44 44"><circle class="vx-g-track" cx="22" cy="22" r="18" pathLength="100"/><circle class="vx-g-fill" cx="22" cy="22" r="18" pathLength="100"/></svg>
    <span class="vx-g-val">—</span><label>${label}</label></div>`;
}

function _buildHUD() {
  const el = document.createElement('div');
  el.id = 'vx-root';
  el.className = 'vx';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-label', 'Advanced voice mode');
  el.dataset.state = 'idle';
  el.innerHTML = `
  <div class="vx-bg" aria-hidden="true"><canvas id="vx-bg-canvas"></canvas></div>
  <header class="vx-top">
    <div class="vx-brand">
      <span class="vx-logo"><i></i></span>
      <div><div class="vx-title">ALFRED</div><div class="vx-sub">Odysseus · Advanced Voice</div></div>
    </div>
    <div class="vx-badges">
      <span class="vx-badge" data-k="live" title="Realtime voice"><i></i><span>GPT-Live</span></span>
      <span class="vx-badge" data-k="jev" title="Decision maker"><i></i><span>JEV</span></span>
      <span class="vx-badge" data-k="util" title="Compressor / forger model"><i></i><span>Utility</span></span>
      <span class="vx-badge vx-code-badge" data-k="code" title="Code-execution pipeline (decide → forge → execute)"><i></i><span>Code</span></span>
    </div>
    <div class="vx-code-strip" id="vx-code-strip" hidden>
      <span data-cs="decide">Decide</span><i></i><span data-cs="forge">Forge</span><i></i><span data-cs="execute">Execute</span>
    </div>
    <div class="vx-top-right">
      <span class="vx-cost" title="API cost of this chat so far (voice + pipeline + chat)" aria-label="API cost">$0</span>
      <span class="vx-timer" aria-label="Call duration">00:00</span>
      <button class="vx-icon-btn vx-err-btn" data-act="errors" title="Errors" aria-label="Show errors" aria-haspopup="true" aria-expanded="false">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <span class="vx-err-count" hidden>0</span>
      </button>
      <button class="vx-icon-btn" data-act="close" title="Close (Esc)" aria-label="Close voice mode">${ICON.close}</button>
      <div class="vx-err-pane" id="vx-err-pane" hidden>
        <div class="vx-err-pane-h"><span>Errors</span><button class="vx-err-clear" data-act="clear-errors" title="Clear all">clear</button></div>
        <div class="vx-err-list" id="vx-err-list"></div>
      </div>
    </div>
  </header>

  <main class="vx-main">
    <section class="vx-panel vx-left" aria-label="Live transcript and chat feed">
      <div class="vx-pane vx-pane-voice">
        <div class="vx-panel-h"><span>Live Transcript</span><span class="vx-live-dot"></span></div>
        <div class="vx-transcript" id="vx-transcript">
          <div class="vx-empty">Conversation appears here in real time.<br><kbd>Space</kbd> connect · <kbd>M</kbd> mute · <kbd>⏎</kbd> send now · <kbd>Esc</kbd> close</div>
        </div>
        <div class="vx-inject">
          <textarea id="vx-inject-ta" rows="2" placeholder="Type or paste — an article, a quote, a note…" aria-label="Type or paste text for the voice assistant"></textarea>
          <div class="vx-inject-acts">
            <small>⌘⏎ ask · ⌥⏎ context</small>
            <button type="button" data-inject="context" title="Give it to ALFRED as context (not a request)">Add context</button>
            <button type="button" data-inject="ask" title="Treat it as something you asked">Ask</button>
          </div>
        </div>
      </div>
      <div class="vx-pane vx-pane-chat">
        <div class="vx-panel-h"><span>Chat Feed</span><span class="vx-chat-state" data-s="idle">Idle</span></div>
        <div class="vx-chatlog" id="vx-chatlog" aria-live="polite">
          <div class="vx-empty">What the chat agent receives and answers appears here live.</div>
        </div>
      </div>
    </section>

    <section class="vx-center">
      <div class="vx-orb-wrap">
        <canvas id="vx-canvas" aria-hidden="true"></canvas>
        <button class="vx-orb-hit" data-act="core" aria-label="Connect or disconnect"></button>
        <div class="vx-orb-label"><div class="vx-state">STANDBY</div><div class="vx-state-sub">Tap the core or press Space to connect</div></div>
      </div>
      <div class="vx-caption" id="vx-caption" aria-live="polite"></div>
    </section>

    <section class="vx-panel vx-right" aria-label="Neural pipeline">
      <div class="vx-panel-h"><span>Neural Pipeline</span><span class="vx-engine" title="Decision engine">—</span></div>
      <div class="vx-pipe">
        ${STAGES.map((s, i) => `
          <div class="vx-node" data-stage="${s}">
            <div class="vx-node-ic"><span>${i + 1}</span></div>
            <div class="vx-node-tx"><b>${STAGE_META[s][0]}</b><small>${STAGE_META[s][1]}</small></div>
            <div class="vx-node-ms"></div>
          </div>`).join('<div class="vx-link"><i></i></div>')}
      </div>
      <div class="vx-decision">
        ${_gauge('send', 'Act')}${_gauge('ready', 'Ready')}${_gauge('novel', 'Novel')}
        <div class="vx-intent">
          <span class="vx-chip" data-intent="none">Awaiting</span>
          <div class="vx-urgency" title="Urgency"><i></i><i></i><i></i></div>
        </div>
      </div>
      <div class="vx-panel-h vx-sm"><span>Memory Strata</span><button class="vx-link-btn" data-act="reset-mem" title="Forget this voice memory">reset</button></div>
      <div class="vx-strata" id="vx-strata"></div>
      <div class="vx-mem-meta"><span data-m="turns">0 turns</span><span data-m="depth">depth 0</span><span data-m="comp">0 compactions</span></div>
    </section>
  </main>

  <footer class="vx-bottom">
    <div class="vx-queue" id="vx-queue" aria-label="Forged messages"></div>
    <div class="vx-dock">
      <label class="vx-toggle" title="Review forged messages before they reach the chat"><input type="checkbox" data-opt="review"><span class="vx-sw"></span>${ICON.eye}<em>Review</em></label>
      <button class="vx-dock-btn" data-act="mute" title="Mute (M)" aria-label="Mute microphone">${ICON.mic}</button>
      <button class="vx-dock-btn vx-primary" data-act="core" title="Connect / end (Space)" aria-label="Connect or end call">${ICON.power}</button>
      <button class="vx-dock-btn" data-act="force" title="Forge & send now (Enter)" aria-label="Send now">${ICON.bolt}</button>
      <label class="vx-toggle" title="Speak chat results back to you"><input type="checkbox" data-opt="speak" checked><span class="vx-sw"></span>${ICON.speak}<em>Speak results</em></label>
    </div>
  </footer>`;
  return el;
}

function _ensureCSS() {
  if (document.getElementById('vx-css')) return;
  const l = document.createElement('link');
  l.id = 'vx-css';
  l.rel = 'stylesheet';
  l.href = '/static/css/voice-mode.css';
  document.head.appendChild(l);
}

function _setState(s) {
  _state = s;
  if (!_root) return;
  _root.dataset.state = s;
  const cc = STATE_COLORS[s] || STATE_COLORS.idle;
  _root.style.setProperty('--vx-r', Math.round(cc[0] * 255));
  _root.style.setProperty('--vx-g', Math.round(cc[1] * 255));
  _root.style.setProperty('--vx-b', Math.round(cc[2] * 255));
  const [a, b] = STATE_LABELS[s] || STATE_LABELS.idle;
  const st = $('.vx-state'); if (st) st.textContent = a;
  const sub = $('.vx-state-sub'); if (sub) sub.textContent = b;
  const mute = $('[data-act="mute"]');
  if (mute) {
    mute.innerHTML = _muted ? ICON.micOff : ICON.mic;
    mute.classList.toggle('on', _muted);
    mute.disabled = !_active;
  }
  const force = $('[data-act="force"]'); if (force) force.disabled = !_active;
  const core = $('.vx-primary'); if (core) core.classList.toggle('live', _active || _connecting);
}

function _setSub(text) {
  const sub = $('.vx-state-sub');
  if (sub) sub.textContent = text;
}

function _caption(text, who) {
  const el = $('#vx-caption');
  if (!el) return;
  const t = (text || '').trim();
  el.dataset.who = who || '';
  el.textContent = t.length > 160 ? '…' + t.slice(-160) : t;
  el.classList.toggle('show', !!t);
}

function _fmtTime(ms) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

// ── Transcript ──────────────────────────────────────────────────────────────

function _newLine(who) {
  const box = $('#vx-transcript');
  if (!box) return null;
  const empty = box.querySelector('.vx-empty');
  if (empty) empty.remove();
  const line = document.createElement('div');
  line.className = `vx-line vx-${who}`;
  const tag = document.createElement('div');
  tag.className = 'vx-who';
  tag.textContent = who === 'user' ? 'YOU' : _assistantName;
  const ts = document.createElement('time');
  ts.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  tag.appendChild(ts);
  const body = document.createElement('div');
  body.className = 'vx-say';
  line.append(tag, body);
  box.appendChild(line);
  while (box.children.length > 120) box.removeChild(box.firstChild);
  return line;
}

function _scrollTranscript() {
  const box = $('#vx-transcript');
  if (box) box.scrollTop = box.scrollHeight;
}

function _systemLine(text, kind = 'info') {
  const box = $('#vx-transcript');
  if (!box) return;
  const empty = box.querySelector('.vx-empty');
  if (empty) empty.remove();
  const d = document.createElement('div');
  d.className = `vx-sys vx-sys-${kind}`;
  d.textContent = text;
  box.appendChild(d);
  _scrollTranscript();
  // Every warning/error is also recorded in the error pane so nothing is lost
  // in the fast-scrolling transcript.
  if (kind === 'warn' || kind === 'error') _recordError(text);
}

// ── Error pane ──────────────────────────────────────────────────────────────
// Every error/warning surfaced in the HUD is also collected here, with a
// notification badge on the top bar and a dropdown pane listing them all.
function _recordError(text) {
  _errors.push({ at: Date.now(), text: String(text || '') });
  if (_errors.length > 100) _errors.shift();
  _renderErrors();
}

function _renderErrors() {
  const btn = $('.vx-err-btn');
  const count = $('.vx-err-count');
  const list = $('#vx-err-list');
  if (btn) btn.classList.toggle('has', _errors.length > 0);
  if (count) {
    count.hidden = _errors.length === 0;
    count.textContent = _errors.length > 99 ? '99+' : String(_errors.length);
  }
  if (!list) return;
  if (!_errors.length) {
    list.innerHTML = '<div class="vx-err-empty">No errors.</div>';
    return;
  }
  const frag = document.createDocumentFragment();
  for (let i = _errors.length - 1; i >= 0; i--) {
    const e = _errors[i];
    const row = document.createElement('div');
    row.className = 'vx-err-item';
    const t = document.createElement('time');
    t.textContent = new Date(e.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const p = document.createElement('p');
    p.textContent = e.text;
    row.append(t, p);
    frag.appendChild(row);
  }
  list.replaceChildren(frag);
}

function _toggleErrors(force) {
  const pane = $('#vx-err-pane');
  const btn = $('.vx-err-btn');
  if (!pane) return;
  const show = force !== undefined ? force : pane.hidden;
  pane.hidden = !show;
  if (btn) btn.setAttribute('aria-expanded', String(show));
  if (show) _renderErrors();
}

function _clearErrors() {
  _errors = [];
  _renderErrors();
  _toggleErrors(false);
}

function _join(prev, chunk) {
  if (!prev) return chunk;
  if (/^[\s,.!?;:)\]'’]/.test(chunk) || /[\s(\[-]$/.test(prev)) return prev + chunk;
  return prev + chunk;
}

// Some transports stream true deltas, others resend the cumulative text so
// far. Treat a chunk that extends what we already have as a replacement so
// cumulative captions never duplicate.
function _merge(prev, chunk) {
  if (prev && chunk.length >= prev.length && chunk.startsWith(prev)) return chunk;
  return _join(prev, chunk);
}

// Short reactions GPT-Live drops in while the user is still talking.
// Covers "Mm-hmm", truncated "Mm-h", "Mmh", "Hmm. Quite.", "Ajá", "I see"…
const _BACKCHANNEL = /^(?:(?:m+(?:[- ]?h+m*)?|hm+|uh[- ]?huh|aj[aá]|s[ií]|yes|yeah|right|okay|ok|i see|quite|claro|vale|got it|understood|entendido)[\s.,!?…-]*)+$/i;
function _isBackchannel(text) {
  const t = String(text || '').trim();
  return !!t && t.length <= 32 && _BACKCHANNEL.test(t);
}

// A user turn keeps streaming while GPT-Live backchannels over it. Freeze the
// user line where the AI line starts so the transcript reads in true order
// (you · Mm-hmm · you …) instead of one block followed by all the reactions.
function _splitUserLine() {
  if (!_userLine || !_userTurnOpen) return;
  _userLine.classList.remove('streaming');
  _userBase = _userText.length;
  _userLine = null;
}

function _newAiLine() {
  _splitUserLine();
  _aiLine = _newLine('ai');
}

function _aiReplace(full) {
  if (!full) return;
  _transcriptSeen = true;
  if (!_aiLine) _newAiLine();
  _aiText = full;
  if (_aiLine) _aiLine.querySelector('.vx-say').textContent = _aiText;
  _caption(_aiText, 'ai');
}

function _aiDelta(chunk) {
  if (!chunk) return;
  _transcriptSeen = true;
  if (!_aiLine) { _newAiLine(); _aiText = ''; }
  _aiText = _merge(_aiText, chunk);
  if (_aiLine) {
    _aiLine.querySelector('.vx-say').textContent = _aiText;
    _aiLine.classList.add('streaming');
  }
  _caption(_aiText, 'ai');
  _scrollTranscript();
  clearTimeout(_aiFinalizeT);
  _aiFinalizeT = setTimeout(_aiFinalize, 2600);
}

function _aiFinalize() {
  clearTimeout(_aiFinalizeT);
  const text = _aiText.trim();
  if (_aiLine) {
    _aiLine.classList.remove('streaming');
    // Backchannels stay visible (dimmed) but are not real turns — keep them
    // out of the pipeline so they don't pollute memory or decisions.
    if (_isBackchannel(text)) _aiLine.classList.add('vx-bc');
  }
  if (text && !_isBackchannel(text)) {
    _pending.push({ speaker: 'AI', text });
    _lastAiSaid = text; _lastAiSaidAt = Date.now();
    try { localStorage.setItem('odysseus-voice-last-' + _voiceId, JSON.stringify({ t: text.slice(0, 400), at: _lastAiSaidAt })); } catch (e) { /* ignore */ }
    _maybeCommit(text);
  }
  _aiLine = null; _aiText = '';
}

// ALFRED promising to do real work ("I'll run a quick check on Stroessner").
// Before, that promise went nowhere — only the user's own speech could reach
// the chat. Now a concrete promise is forged into a chat request.
const _COMMIT_RE = /\b(?:i'?ll|i will|let me|i'?m going to|i'?m gonna|i shall|voy a|d[eé]jame|lo voy a|te lo voy a)\s+(?:\w+\s+){0,3}?(?:run|do|check|look(?:\s+up|\s+into)?|search|research|find|pull(?:\s+up)?|dig|verify|investigate|fetch|confirm|try|review|compile|put together|gather|track down|buscar|revisar|investigar|verificar|consultar|averiguar|confirmar)\b/i;
// "I'll check" with no subject, or the generic "I'm on it" acks — not a task.
const _COMMIT_VAGUE = /^(?:[^.!?]*\b(?:on it|onto it|in hand|right away|one moment|a moment|just a moment|un momento)\b[^.!?]*[.!?]?\s*)+$/i;

function _maybeCommit(text) {
  if (!_active || !_COMMIT_RE.test(text) || _COMMIT_VAGUE.test(text)) return;
  const now = Date.now();
  // A promise right after a forge/dispatch is just ALFRED acknowledging the
  // request that is already on its way — not a new one.
  if (now - _lastForgeAt < 25000 || now - _lastCommitAt < 15000) return;
  if (_awaiting || _queue.some(q => ['queued', 'sending', 'sent', 'running'].includes(q.status))) return;
  _lastCommitAt = now;
  // The user just finished a turn: its own forge is likely seconds away and
  // the promise is probably ALFRED acknowledging it. Let the user's pipeline
  // run first; the promise only goes out if nothing was forged meanwhile.
  if (_pipeBusy || now - _lastUserFinalAt < 8000 || _pending.some(t => t.speaker === 'You')) {
    _pendingCommit = text;
    setTimeout(() => {
      if (_pendingCommit !== text || _pipeBusy) return;   // handled by the pipeline's finally
      _pendingCommit = null;
      if (_active && Date.now() - _lastForgeAt > 25000) _runPipeline(false, false, text);
    }, 9000);
    return;
  }
  _runPipeline(false, false, text);
}

function _userDelta(chunk) {
  if (!chunk) return;
  _transcriptSeen = true;
  if (!_userTurnOpen) { _userTurnOpen = true; _userText = ''; _userBase = 0; }
  _userText = _merge(_userText, chunk);
  // Continuation segments start mid-sentence — drop leading space/punctuation.
  const seg = _userText.slice(_userBase).replace(/^[\s.,;:!?…-]+/, '');
  if (!_userLine && seg) _userLine = _newLine('user');
  if (_userLine) {
    _userLine.querySelector('.vx-say').textContent = seg;
    _userLine.classList.add('streaming');
  }
  _caption(_userText, 'user');
  _scrollTranscript();
  clearTimeout(_userFinalizeT);
  _userFinalizeT = setTimeout(() => _userFinal(null), 3000);
}

function _userFinal(full) {
  clearTimeout(_userFinalizeT);
  if (full) _transcriptSeen = true;
  const text = ((full != null ? full : _userText) || '').trim();
  const shown = _userText.slice(0, _userBase);
  const base = _userBase;
  _userTurnOpen = false; _userBase = 0; _userText = '';
  if (!text) { _userLine = null; return; }
  // Only the part after the last split belongs in the current line. If the
  // final transcript was re-worded and no longer extends what is shown, put
  // it all in the last line rather than dropping words.
  let seg = text;
  if (base > 0) {
    seg = text.startsWith(shown.trim())
      ? text.slice(shown.trim().length).replace(/^[\s.,;:!?…-]+/, '').trim()
      : text;
  }
  if (seg) {
    if (!_userLine) _userLine = _newLine('user');
    _userLine.querySelector('.vx-say').textContent = seg;
  }
  if (_userLine) _userLine.classList.remove('streaming');
  _caption(text, 'user');
  _scrollTranscript();
  _pending.push({ speaker: 'You', text });   // the pipeline always gets the whole turn
  _userLine = null;
  _lastUserFinalAt = Date.now();
  _schedulePipeline(1100);
}

// ═══════════════════════════════════════════════════════════════════════════
//  PIPELINE (SSE)
// ═══════════════════════════════════════════════════════════════════════════

let _settleTimer = 0;
function _schedulePipeline(delay = 1100, force = false) {
  clearTimeout(_pipeTimer);
  clearTimeout(_settleTimer);        // new speech supersedes a pending settle
  _pipeTimer = setTimeout(() => _runPipeline(force), delay);
}

function _resetStages() {
  if (!_root) return;
  _root.querySelectorAll('.vx-node').forEach(n => {
    n.dataset.status = '';
    n.querySelector('.vx-node-ms').textContent = '';
  });
  _root.querySelectorAll('.vx-link').forEach(l => l.classList.remove('flow'));
}

function _stage(stage, status, ev) {
  const node = $(`.vx-node[data-stage="${stage}"]`);
  if (!node) return;
  node.dataset.status = status;
  const ms = node.querySelector('.vx-node-ms');
  if (status === 'running') ms.textContent = '···';
  else if (ev && typeof ev.ms === 'number') ms.textContent = ev.ms < 1000 ? `${ev.ms}ms` : `${(ev.ms / 1000).toFixed(1)}s`;
  else ms.textContent = '';
  if (stage === 'observe' && ev && typeof ev.score === 'number') {
    node.querySelector('small').textContent = `Attention ${Math.round(ev.score * 100)}%`;
  }
  if (stage === 'compress' && ev && status === 'done') {
    node.querySelector('small').textContent = `${ev.depth || 0} layers · ${ev.live || 0} raw`;
  }
  if (stage === 'decide' && ev && ev.engine) {
    node.querySelector('small').textContent = ev.engine === 'jev' ? 'JEV decisions' : 'LLM fallback';
  }
  const idx = STAGES.indexOf(stage);
  const links = _root.querySelectorAll('.vx-link');
  if (status === 'done' && links[idx]) links[idx].classList.add('flow');
}

function _setGauge(key, v) {
  const g = $(`.vx-gauge[data-g="${key}"]`);
  if (!g) return;
  const pct = Math.max(0, Math.min(1, Number(v) || 0));
  g.querySelector('.vx-g-fill').style.strokeDasharray = `${(pct * 100).toFixed(1)} 100`;
  g.querySelector('.vx-g-val').textContent = `${Math.round(pct * 100)}`;
  g.dataset.level = pct >= 0.66 ? 'hi' : pct >= 0.4 ? 'mid' : 'lo';
}

function _renderDecision(d) {
  _setGauge('send', d.should_send);
  _setGauge('ready', d.ready);
  _setGauge('novel', 1 - (Number(d.already_sent) || 0));
  const chip = $('.vx-chip');
  if (chip) {
    const intent = d.intent || 'none';
    chip.dataset.intent = intent;
    const conf = typeof d.intent_confidence === 'number' ? ` · ${Math.round(d.intent_confidence * 100)}%` : '';
    chip.textContent = (INTENT_LABELS[intent] || intent) + conf;
  }
  const urg = $('.vx-urgency');
  if (urg) {
    let u = d.urgency;
    if (typeof u === 'string') u = ['Whenever', 'Soon', 'Right now'].indexOf(u);
    u = Math.max(0, Math.min(2, Number(u) || 0));
    urg.dataset.u = String(u);
  }
  const dec = $('.vx-decision');
  if (dec) {
    dec.classList.toggle('go', !!d.send);
    dec.classList.remove('pulse'); void dec.offsetWidth; dec.classList.add('pulse');
  }
}

function _renderMemory(m) {
  if (!m) return;
  // Keep the top compressed layer so a new GPT-Live session can be seeded with it.
  const top = (m.layers || [])[0];
  if (top && top.preview) _memPreview = String(top.preview);
  if (!_root) return;
  const strata = $('#vx-strata');
  if (strata) {
    strata.innerHTML = '';
    const layers = (m.layers || []).slice();
    layers.forEach((l, i) => {
      const row = document.createElement('div');
      row.className = 'vx-stratum' + (i === 0 ? ' top' : '');
      row.style.setProperty('--fill', Math.min(1, l.count / Math.max(1, m.merge_size)).toFixed(2));
      const lv = document.createElement('b'); lv.textContent = `L${l.level}`;
      const bar = document.createElement('div'); bar.className = 'vx-st-bar'; bar.appendChild(document.createElement('i'));
      const pv = document.createElement('span'); pv.className = 'vx-st-pv';
      pv.textContent = (l.preview || '').replace(/^[-*•\s]+/, '').replace(/\s+/g, ' ');
      const ct = document.createElement('em'); ct.textContent = `${l.count}/${m.merge_size}`;
      row.title = l.preview || '';
      row.append(lv, bar, pv, ct);
      strata.appendChild(row);
    });
    const raw = document.createElement('div');
    raw.className = 'vx-stratum raw' + (m.compressing ? ' busy' : '');
    raw.style.setProperty('--fill', Math.min(1, m.live_turns / Math.max(1, m.batch_size)).toFixed(2));
    const lv = document.createElement('b'); lv.textContent = 'RAW';
    const bar = document.createElement('div'); bar.className = 'vx-st-bar'; bar.appendChild(document.createElement('i'));
    const pv = document.createElement('span'); pv.className = 'vx-st-pv';
    pv.textContent = m.compressing ? 'Compressing batch…' : 'Current batch — uncompressed';
    const ct = document.createElement('em'); ct.textContent = `${m.live_turns}/${m.batch_size}`;
    raw.append(lv, bar, pv, ct);
    strata.appendChild(raw);
  }
  const set = (k, v) => { const e = $(`[data-m="${k}"]`); if (e) e.textContent = v; };
  set('turns', `${m.turns_total || 0} turns`);
  set('depth', `depth ${m.depth || 0}`);
  set('comp', `${m.compactions || 0} compactions`);
}

async function _runPipeline(force = false, settle = false, commitment = null) {
  if (_pipeBusy) {
    if (commitment) _pendingCommit = commitment; else _pipeRerun = true;
    return;
  }
  const turns = _pending.splice(0, _pending.length);
  if (!turns.length && !force && !settle && !commitment) return;
  _pipeBusy = true;
  _thinkTarget = 1;
  _resetStages();
  const ctrl = new AbortController();
  _pipeAbort = ctrl;
  const sid = window.sessionModule?.getCurrentSessionId?.() || null;
  try {
    const resp = await fetch(_api('/api/realtime/pipeline'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      signal: ctrl.signal,
      body: JSON.stringify({ voice_id: _voiceId, turns, chat_session_id: sid, force, settle,
                             commitment: commitment || undefined }),
    });
    if (!resp.ok || !resp.body) throw new Error(`pipeline HTTP ${resp.status}`);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          try { _onPipeEvent(JSON.parse(line.slice(5).trim())); } catch (e) { /* partial */ }
        }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      console.warn('voice pipeline failed', e);
      _systemLine('Pipeline unavailable — ' + (e.message || e), 'warn');
      _pending.unshift(...turns); // don't lose context
    }
  } finally {
    _pipeBusy = false;
    _pipeAbort = null;
    _thinkTarget = 0;
    if (_pipeRerun) { _pipeRerun = false; _schedulePipeline(400); }
    else if (_pendingCommit) {
      const c = _pendingCommit; _pendingCommit = null;
      // Only if nothing was forged meanwhile (then the promise was an ack).
      if (Date.now() - _lastForgeAt > 25000) setTimeout(() => _runPipeline(false, false, c), 300);
    }
  }
}

function _onPipeEvent(ev) {
  switch (ev.type) {
    case 'hello': {
      const eng = $('.vx-engine');
      if (eng) { eng.textContent = ev.engine === 'jev' ? 'JEV' : 'LLM'; eng.dataset.engine = ev.engine; }
      _renderMemory(ev.memory);
      break;
    }
    case 'stage': _stage(ev.stage, ev.status, ev); break;
    case 'decision': _renderDecision(ev.decision || {}); break;
    case 'memory': _renderMemory(ev.memory); break;
    case 'forged': _enqueue(ev); break;
    case 'answer_from_context':
      // The answer was already in the chat/memory — speak it directly instead
      // of re-asking the chat agent.
      if (ev.answer) {
        _systemLine('Answered from context — no chat round-trip.', 'done');
        _queueSpeak(ev.answer, true);
      }
      break;
    case 'done':
      // Speech judged "still forming": once the user has been quiet for a
      // moment, decide again treating it as finished (rambling ≠ incomplete).
      if (ev.reason === 'not-ready') {
        clearTimeout(_settleTimer);
        _settleTimer = setTimeout(() => _runPipeline(false, true), 2500);
      }
      if (ev.reason === 'error' && ev.error) _systemLine('Pipeline error — ' + ev.error, 'warn');
      else if (ev.reason === 'duplicate' && ev.duplicate_of) _systemLine(`Already sent a moment ago — “${ev.duplicate_of.slice(0, 80)}”`, 'info');
      else if (ev.reason === 'decider-error' || ev.reason === 'forger-error') _systemLine('Pipeline could not decide — say it again or press Enter to force-send.', 'warn');
      break;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
//  QUEUE → CHAT
// ═══════════════════════════════════════════════════════════════════════════

const _STOP = new Set(['the', 'a', 'an', 'to', 'of', 'and', 'or', 'please', 'me', 'my', 'i', 'for',
  'in', 'on', 'it', 'that', 'this', 'is', 'be', 'can', 'you', 'with', 'at']);
function _tokens(t) {
  return new Set((String(t || '').toLowerCase().match(/[a-z0-9]+/g) || []).filter(w => w.length > 1 && !_STOP.has(w)));
}
function _similar(a, b) {
  const x = _tokens(a), y = _tokens(b);
  if (!x.size || !y.size) return 0;
  let inter = 0;
  for (const w of x) if (y.has(w)) inter++;
  return inter / (x.size + y.size - inter);
}

function _enqueue(ev) {
  // A forger "decline" ({"title":"","message":""}) must never become a chat request.
  const msg = String(ev.message || '').trim();
  if (!msg || /^\s*(?:```\w*)?\s*\{[\s\S]*\}\s*(?:```)?\s*$/.test(msg)) return;
  // Never queue the same instruction twice (live items or anything sent in the last 30 min).
  const now = Date.now();
  const LIVE = ['review', 'queued', 'sending', 'sent', 'running', 'background'];
  const dup = _queue.find(q => LIVE.includes(q.status) && _similar(q.message, ev.message) >= 0.6);
  if (dup) {
    _systemLine(`Already queued — “${dup.title}”`, 'info');
    return;
  }
  _lastForgeAt = now;
  const item = {
    created: now,
    id: 'q' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    title: (ev.source === 'commitment' ? '↻ ' : '') + (ev.title || 'Request'),
    message: ev.message,
    intent: ev.intent || 'task',
    urgency: ev.urgency,
    status: _opts.review ? 'review' : 'queued',
  };
  _queue.push(item);
  _renderQueue();
  _systemLine(ev.source === 'commitment'
    ? `Following through on ${_assistantName}'s promise → “${ev.title || 'request'}”`
    : `Forged → “${item.title}”`, 'forge');
  _ensureDispatcher();
}

function _statusLabel(s) {
  return { review: 'Review', queued: 'Queued', waiting: 'Waiting for chat', sending: 'Sending',
           running: 'Executing', background: 'Running in bg', answered: 'Done', sent: 'Sent',
           interrupted: 'Interrupted', failed: 'Not sent' }[s] || s;
}

function _renderQueue() {
  const box = $('#vx-queue');
  if (!box) return;
  const visible = _queue.slice(-6);
  box.classList.toggle('has', visible.length > 0);
  // A card being edited keeps its exact DOM node (and so focus + cursor):
  // other cards re-render around it as their status changes during dispatch.
  const editing = visible.find(q => q.editing);
  const keep = editing ? box.querySelector(`.vx-card[data-id="${editing.id}"]`) : null;
  const keepOk = !!(keep && keep.querySelector('.vx-card-edit'));
  for (const child of Array.from(box.children)) {
    if (!(keepOk && child === keep)) child.remove();
  }
  let before = true;   // inserting cards that come before the kept card
  visible.forEach(item => {
    if (keepOk && item === editing) {
      before = false;
      const pill = keep.querySelector('.vx-pill');
      if (pill) pill.textContent = 'Editing';
      return;
    }
    const card = document.createElement('article');
    card.className = 'vx-card';
    card.dataset.status = item.status;
    card.dataset.id = item.id;
    const head = document.createElement('header');
    const chip = document.createElement('span');
    chip.className = 'vx-chip'; chip.dataset.intent = item.intent;
    chip.textContent = INTENT_LABELS[item.intent] || item.intent;
    const title = document.createElement('b'); title.textContent = item.title;
    const st = document.createElement('span'); st.className = 'vx-pill'; st.textContent = item.editing ? 'Editing' : _statusLabel(item.status);
    head.append(chip, title, st);
    let body;
    if (item.editing) {
      body = document.createElement('textarea');
      body.className = 'vx-card-edit';
      body.value = item.message;
      body.addEventListener('input', () => { item.message = body.value; });
      body.addEventListener('blur', () => _finishEdit(item, body), { once: true });
      body.addEventListener('keydown', (ke) => {
        if (ke.key === 'Enter' && (ke.metaKey || ke.ctrlKey)) { ke.preventDefault(); _finishEdit(item, body); }
        ke.stopPropagation();
      });
    } else {
      body = document.createElement('p');
      body.className = 'vx-card-msg';
      body.textContent = item.message;
    }
    const acts = document.createElement('div');
    acts.className = 'vx-card-acts';
    if (item.editing) {
      // No buttons while editing — a click on them would race the blur that
      // ends the edit. Click away or ⌘/Ctrl+⏎ to finish.
    } else if (['review', 'queued', 'failed'].includes(item.status)) {
      const label = item.status === 'failed' ? 'Retry' : (item.status === 'queued' ? 'Send next' : 'Send');
      acts.innerHTML = `
        <button data-q="send" title="Send to chat">${ICON.send}<span>${label}</span></button>
        <button data-q="edit" title="Edit">${ICON.edit}</button>
        <button data-q="drop" title="Discard">${ICON.trash}</button>`;
    } else if (['sent', 'running', 'background', 'sending'].includes(item.status)) {
      acts.innerHTML = `<button data-q="release" title="Stop tracking this item (the chat keeps running)">${ICON.close}<span>Release</span></button>`;
    }
    if (item.note) {
      const note = document.createElement('div');
      note.className = 'vx-card-note';
      note.textContent = item.note;
      card.append(head, body, note, acts);
    } else {
      card.append(head, body, acts);
    }
    if (keepOk && before) box.insertBefore(card, keep);
    else box.appendChild(card);
  });
  if (!keepOk) box.scrollLeft = box.scrollWidth;   // don't yank the view while editing
}

// Finish editing a queued task: commit the text and unhalt the queue so it
// auto-sends again. Called on blur (click away) or Ctrl/Cmd+Enter.
function _finishEdit(item, ta) {
  if (!item.editing) return;   // already finished (e.g. blur after Ctrl+Enter)
  item.message = ta.value.trim() || item.message;
  item.editing = false;
  item.note = '';
  _renderQueue();
  _ensureDispatcher();         // unhalt: resume auto-send
}

function _onQueueClick(e) {
  const btn = e.target.closest('[data-q]');
  if (!btn) return;
  const card = btn.closest('.vx-card');
  const item = _queue.find(q => q.id === card?.dataset.id);
  if (!item) return;
  const act = btn.dataset.q;
  if (act === 'send') {
    // Manual send jumps the line and is attempted right away.
    item.status = 'queued';
    item.note = '';
    _queue = [item, ..._queue.filter(q => q !== item)];
    _renderQueue();
    _ensureDispatcher();
    _dispatchTick(true);
  }
  else if (act === 'release') {
    if (_awaiting && (_awaiting.ids || [_awaiting.id]).includes(item.id)) {
      _queue.filter(q => _awaiting.ids?.includes(q.id) && q !== item).forEach(q => _setItem(q, 'answered'));
      _awaiting = null;
    }
    _setItem(item, 'answered');
    _dispatchTick(true);
  }
  else if (act === 'drop') { _queue = _queue.filter(q => q !== item); _renderQueue(); }
  else if (act === 'edit') {
    // Halt the queue at this exact task while it's being edited. The textarea
    // is rendered by _renderQueue (so it survives re-renders); finishing the
    // edit (blur or Ctrl/Cmd+Enter) unhalts and auto-sends.
    item.editing = true;
    item.note = 'Editing — the queue is paused here. Click away or ⌘/Ctrl+⏎ to resume.';
    _renderQueue();
    const ta = $('#vx-queue')?.querySelector(`.vx-card[data-id="${item.id}"] .vx-card-edit`);
    if (ta) ta.focus();
  }
}

function _currentSid() { return window.sessionModule?.getCurrentSessionId?.() || null; }

function _streamActive(sid) {
  return !!(sid && window.chatModule?.hasActiveStream?.(sid));
}

// Busy = something is streaming in the visible chat. Submitting while a stream
// runs would hit handleChatSubmit's "stop streaming" branch and CANCEL it, so
// this guard is strict.
function _chatBusy() { return _streamActive(_currentSid()); }

// The chat watcher's 700ms tick drives dispatch too (one loop, ordered).
function _ensureDispatcher() { _startChatWatch(); }

const _LINGER = { answered: 3500, interrupted: 9000 };
function _setItem(item, status, note) {
  if (!item) return;
  if (note !== undefined) item.note = note;
  if (item.status === status && note === undefined) return;
  item.status = status;
  _renderQueue();
  if (_LINGER[status]) {
    setTimeout(() => {
      if (item.status !== status) return;
      const card = $(`.vx-card[data-id="${item.id}"]`);
      if (card) card.classList.add('leaving');
      setTimeout(() => { _queue = _queue.filter(q => q !== item); _renderQueue(); }, 380);
    }, _LINGER[status]);
  }
}

let _busyNoteShown = 0;

function _dispatchTick(manual = false) {
  // ① Track the in-flight chat turn (one at a time).
  if (_awaiting) {
    const item = _queue.find(q => q.id === _awaiting.id);
    if (!item) { _awaiting = null; }
    else {
      if (!_awaiting.sid) _awaiting.sid = _currentSid();
      const busy = _streamActive(_awaiting.sid);
      const elapsed = Date.now() - _awaiting.since;
      const group = _queue.filter(q => (_awaiting.ids || [_awaiting.id]).includes(q.id));
      if (busy) {
        _awaiting.sawBusy = true;
        group.forEach(q => _setItem(q, _awaiting.sid === _currentSid() ? 'running' : 'background'));
      } else if (!_awaiting.sawBusy) {
        // Did the chat actually accept it? If our text is still sitting in
        // the composer, the submit was swallowed — say so instead of hanging.
        if (_chatShowsUserMsg(_awaiting.text)) _awaiting.accepted = true;
        if (!_awaiting.accepted) {
          const input = document.getElementById('message');
          const stuck = input && input.value.trim() === _awaiting.text.trim();
          if (stuck && elapsed > 10000) {
            group.forEach(q => _setItem(q, 'failed', 'The chat did not accept the message — check the chat for a prompt, then Retry.'));
            _awaiting = null;
            return;
          }
        }
        if (elapsed < 8000) return;          // give the stream time to start (agent runs warm up)
      }
      const finished = (_awaiting.sawBusy && !busy) || (!_awaiting.sawBusy && elapsed >= 8000) || elapsed > 600000;
      if (!finished) return;
      const visible = _awaiting.sid === _currentSid();
      const lastAi = visible ? _lastAIEl() : null;
      if (lastAi && lastAi.querySelector('.stopped-indicator')) {
        group.forEach(q => _setItem(q, 'interrupted'));
        _systemLine(`Chat run “${item.title}” was interrupted.`, 'warn');
        _setChatState('interrupted', 'Interrupted');
      } else if (!visible) {
        group.forEach(q => _setItem(q, 'answered'));
        _systemLine(`“${item.title}” finished in another chat.`, 'info');
      } else {
        group.forEach(q => _setItem(q, 'answered'));
        if (_awaiting.sawBusy) _speakResult(group.length > 1 ? { title: group.map(q => q.title).join(' + ') } : item, lastAi);
      }
      _awaiting = null;
    }
  }
  // ② Dispatch the next queued message only when the chat is idle.
  const next = _queue.find(q => q.status === 'queued');
  if (!next) return;
  // A task being edited halts the queue right here: it (and everything after
  // it) waits until the user finishes editing, then auto-sends.
  if (next.editing) return;
  if (_chatBusy()) {
    const ahead = '';
    if (next.note !== 'Waiting — the chat is still answering. Sends as soon as it finishes.') {
      _setItem(next, 'queued', 'Waiting — the chat is still answering. Sends as soon as it finishes.');
    }
    if (manual && Date.now() - _busyNoteShown > 4000) {
      _busyNoteShown = Date.now();
      _systemLine('Chat is busy — your message goes next.', 'info');
    }
    return;
  }
  // Batch: everything waiting goes out together, so a long chat run never
  // leaves a pile of cards behind it — but stop at the first task being
  // edited so it (and anything after) waits for the edit to finish.
  const batch = [];
  for (const q of _queue) {
    if (q.status !== 'queued') continue;
    if (q.editing) break;
    batch.push(q);
  }
  const text = batch.length === 1 ? batch[0].message
    : 'Please handle all of these:\n' + batch.map((q, i) => `${i + 1}. ${q.message}`).join('\n');
  batch.forEach(q => { q.note = ''; _setItem(q, 'sending'); });
  if (_sendToChat(text)) {
    batch.forEach(q => _setItem(q, 'sent'));
    _awaiting = { id: batch[0].id, ids: batch.map(q => q.id), sid: _currentSid(), sawBusy: false, since: Date.now(), text };
    _systemLine(batch.length === 1 ? `Sent to chat → “${batch[0].title}”` : `Sent ${batch.length} requests to chat in one message`, 'forge');
    // Tell GPT-Live (quietly) that real work just started, so it can reassure
    // the user with accurate status instead of guessing while the chat runs.
    _brief('The chat agent just received a request to work on: ' + text.slice(0, 260) +
      '. It is running now; its result will follow shortly. You may tell the user that the work is underway.', false);
  } else {
    batch.forEach(q => _setItem(q, 'failed', 'The chat composer was not available — Retry when the chat is ready.'));
  }
}

// The composer's real submit path is chatModule.handleChatSubmit. #chat-form is
// an empty placeholder whose submit listener is disabled (app.js), so the old
// requestSubmit() on it reported success while sending nothing.
function _sendToChat(text) {
  const input = document.getElementById('message');
  const submit = window.chatModule?.handleChatSubmit;
  if (!input || typeof submit !== 'function') return false;
  if (_chatBusy()) return false;
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  try {
    const p = submit({ preventDefault() {}, stopPropagation() {}, target: input });
    if (p && typeof p.catch === 'function') {
      p.catch((e) => {
        console.warn('voice→chat submit failed', e);
        _systemLine('Chat rejected the message — ' + (e?.message || e), 'warn');
      });
    }
    return true;
  } catch (e) {
    console.warn('voice→chat submit failed', e);
    return false;
  }
}

// ── Chat DOM helpers ────────────────────────────────────────────────────────

function _chatMsgs() {
  return Array.from(document.querySelectorAll('#chat-history .msg-user, #chat-history .msg-ai'));
}

function _chatShowsUserMsg(text) {
  const head = String(text || '').trim().slice(0, 40);
  if (!head) return false;
  const users = document.querySelectorAll('#chat-history .msg-user');
  for (let i = users.length - 1; i >= Math.max(0, users.length - 3); i--) {
    if ((users[i].textContent || '').includes(head)) return true;
  }
  return false;
}

function _lastAIEl() {
  const a = document.querySelectorAll('#chat-history .msg-ai');
  return a[a.length - 1] || null;
}

// _msgText is called several times per 700ms tick (feed, ingest, narration)
// and each call deep-clones the bubble — tables included. Cache the extracted
// text per bubble and only re-extract when the bubble actually changed.
const _msgCache = new WeakMap();
function _msgText(el, max = 4000) {
  if (!el) return '';
  const body = el.querySelector('.body') || el;
  const sig = (body.textContent || '').length + ':' + body.childElementCount + ':' + el.classList.contains('streaming');
  const hit = _msgCache.get(el);
  if (hit && hit.sig === sig) return hit.text.slice(0, max);
  const text = _extractText(body);
  _msgCache.set(el, { sig, text });
  return text.slice(0, max);
}

function _extractText(body) {
  const clone = body.cloneNode(true);
  clone.querySelectorAll('details, .think, .thinking, .thinking-section, .live-think-inner, .sources-box, ' +
    '.msg-actions, .msg-metrics, .msg-footer, .agent-thread, .spinner, .research-timer, .stopped-indicator, ' +
    'button, svg, style, script')
    .forEach(n => n.remove());
  // Detached nodes have no layout, so add breaks after blocks by hand —
  // and separators between table cells, or rows read as "ArgentinaVidela1976".
  clone.querySelectorAll('td, th').forEach(n => n.append(' · '));
  clone.querySelectorAll('p, li, h1, h2, h3, h4, h5, h6, pre, tr, br, blockquote').forEach(n => n.append('\n'));
  return (clone.textContent || '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

// A fresh GPT-Live session knows nothing — after a reload or server restart it
// would claim "no record of our chat". Seed it (silently) with the compressed
// voice memory plus the most recent turns of the chat thread it is attached to.
function _seedChatContext() {
  if (!_dc || _dc.readyState !== 'open') return;
  if (!_active) { if (++_seedTries < 20) setTimeout(_seedChatContext, 300); return; }
  _seedTries = 0;
  const HEAD = '[Context — the conversation so far in the chat thread you are attached to. You DO know this; ' +
    'use it when the user refers to earlier topics. Do NOT read it out, summarise it or resume it now — ' +
    'it is background, already told to the user.]';
  if (_memPreview) _brief(HEAD + '\nVoice memory summary:\n' + _memPreview.slice(0, 1200), false);
  // Newest turns, within a budget, back in chronological order.
  const lines = [];
  let budget = 3200;
  for (const el of _chatMsgs().slice(-12).reverse()) {
    if (el.querySelector('.stopped-indicator')) continue;
    const who = el.classList.contains('msg-ai') ? 'Chat agent' : 'User';
    const t = _speakable(_msgText(el, 4000)).replace(/\s+/g, ' ').trim();
    if (!t) continue;
    const line = `${who}: ${t.length > 500 ? t.slice(0, 500) + '…' : t}`;
    if (line.length > budget) break;
    budget -= line.length;
    lines.unshift(line);
  }
  if (lines.length) {
    const parts = _chunks(lines.join('\n'));
    parts.forEach((p, i) => _brief(`${HEAD}${parts.length > 1 ? ` (part ${i + 1} of ${parts.length})` : ''}\n${p}`, false));
  }
  // Re-seed anything the user typed/pasted earlier (survives reconnects).
  for (const n of _notes.slice(-3)) _briefNote(n);
  // Results that finished while the link was off are new — deliver them now.
  if (_missed.length) {
    const missed = _missed.splice(0, _missed.length).slice(-2);
    if (_opts.speak) {
      for (const t of missed) _queueSpeak('(Finished while the voice link was off.)\n' + t, true);
    } else {
      for (const t of missed) _injectContext(t);
    }
  }
  // Resume in the state the previous session ended in — a reconnect is not a
  // new conversation. If ALFRED last said "standing by", it stays silent.
  if (!_lastAiSaid) {
    try {
      const s = JSON.parse(localStorage.getItem('odysseus-voice-last-' + _voiceId) || 'null');
      if (s && s.t) { _lastAiSaid = s.t; _lastAiSaidAt = s.at || 0; }
    } catch (e) { /* ignore */ }
  }
  if (_lastAiSaid && Date.now() - _lastAiSaidAt < 6 * 3600 * 1000) {
    _brief('[Resume state — the voice link was just re-established mid-conversation] Your last words before ' +
      `the reconnect were: "${_lastAiSaid.slice(0, 300)}". Continue from exactly that state. Do not greet, ` +
      'do not recap, do not resume reading any result. If you were standing by, keep standing by in silence ' +
      'until the user speaks to you.', false);
  } else {
    _brief('[Session start] Do not start talking or summarise anything — wait for the user to speak first.', false);
  }
}

// ── Typed / pasted context (E) ──────────────────────────────────────────────
function _briefNote(text) {
  const parts = _chunks(text).slice(0, 8);
  parts.forEach((p, i) => _brief(
    `[Typed context from the user${parts.length > 1 ? `, part ${i + 1} of ${parts.length}` : ''}] ` +
    'Treat this as true context the user wants you to know. Do not read it out or reply to it now.\n' + p, false));
}

function _noteLine(text, label) {
  const line = _newLine('user');
  if (!line) return;
  line.classList.add('vx-note');
  const who = line.querySelector('.vx-who');
  if (who && who.firstChild) who.firstChild.textContent = label;
  line.querySelector('.vx-say').textContent = text.length > 600 ? text.slice(0, 600) + '…' : text;
  _scrollTranscript();
}

async function _addContext(text) {
  _notes.push(text);
  if (_notes.length > 10) _notes.shift();
  _noteLine(text, 'YOU · TYPED CONTEXT');
  if (_active) _briefNote(text);
  // Into the voice memory too (as context, never evaluated as a request).
  const pieces = [];
  for (let i = 0; i < text.length && pieces.length < 6; i += 5000) {
    pieces.push({ speaker: 'user_note', text: text.slice(i, i + 5000) });
  }
  try {
    const r = await fetch(_api('/api/realtime/memory/ingest'), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_id: _voiceId, turns: pieces, chat_session_id: _currentSid() }),
    });
    if (r.ok) _renderMemory((await r.json()).stats);
  } catch (e) { /* best-effort */ }
  _systemLine(`Context added (${text.length.toLocaleString()} chars) — ${_assistantName} now knows it.`, 'done');
}

function _askTyped(text) {
  _noteLine(text, 'YOU · TYPED');
  // Let the voice know what was asked (silently), then run it through the
  // pipeline exactly like spoken user speech.
  if (_active) _brief('[Typed by the user] ' + text.slice(0, 1400), false);
  _pending.push({ speaker: 'You', text });
  _schedulePipeline(50, true);
}

// ── API cost: GPT-Live usage → sidecar ledger; live readout in the HUD ─────
function _fmtUsd(v) {
  if (!(v > 0)) return '$0';
  if (v < 0.01) return '$' + v.toFixed(4);
  if (v < 1) return '$' + v.toFixed(3);
  return '$' + v.toFixed(2);
}

async function _reportRealtimeUsage(usage) {
  try {
    const r = await fetch(_api('/api/costs/realtime'), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ usage, chat_session_id: _currentSid() }),
    });
    if (r.ok) {
      const d = await r.json();
      if (typeof d.chat_total_usd === 'number') _paintHudCost(d.chat_total_usd);
    }
  } catch (e) { /* cost tracking is best-effort */ }
  window.refreshSessionCosts?.();
}

function _paintHudCost(usd) {
  const el = $('.vx-cost');
  if (el) el.textContent = _fmtUsd(usd);
}

async function _refreshHudCost() {
  const sid = _currentSid();
  if (!sid) { _paintHudCost(0); return; }
  try {
    const r = await fetch(_api(`/api/costs/${encodeURIComponent(sid)}?limit=1`), { credentials: 'same-origin' });
    if (r.ok) _paintHudCost((await r.json()).total_usd || 0);
  } catch (e) { /* best-effort */ }
}

function _lastAIMessage() { return _msgText(_lastAIEl(), 1800); }

function _dcSend(obj) {
  if (!_dc || _dc.readyState !== 'open') return false;
  try { _dc.send(JSON.stringify(obj)); return true; } catch (e) { return false; }
}

// ── Chat mirror: live feed, interruption/switch detection, chat → memory ────

const _seenKeys = new Set();     // role|text already folded into voice memory
const _spokenKeys = new Set();   // chat answers already briefed aloud

function _key(role, text) { return role + '|' + text; }
function _remember(set, k) {
  set.add(k);
  if (set.size > 400) set.delete(set.values().next().value);
}

function _setChatState(s, label) {
  const e = $('.vx-chat-state');
  if (e) { e.dataset.s = s; e.textContent = label; }
}

function _markAllSeen() {
  for (const el of _chatMsgs().slice(-30)) {
    _ingestState.set(el, { text: '', stable: 0, done: true });
    _knownEls.add(el);
  }
}

function _startChatWatch() {
  if (_chatTickInt) return;
  _chatSid = _currentSid();
  _chatWasBusy = _chatBusy();
  // HUD was closed (watcher stopped) while a chat run finished: those answers
  // were never heard — keep them for the next voice link. Not on first open,
  // where everything on screen is plain history.
  if (_everWatched && _chatSid === _lastWatchedSid) {
    for (const el of _chatMsgs().slice(-6)) {
      if (_knownEls.has(el) || _spokenEls.has(el) || !el.classList.contains('msg-ai')) continue;
      if (el.classList.contains('streaming') || el.querySelector('.stopped-indicator')) continue;
      const t = _msgText(el, 12000);
      if (t) { _missed.push(t); _spokenEls.add(el); }
    }
  }
  _everWatched = true;
  _markAllSeen();  // existing history is already in the chat's own summary spine
  const hist = document.getElementById('chat-history');
  if (hist && !_chatObs) {
    _chatObs = new MutationObserver(() => {
      clearTimeout(_chatRenderT);
      _chatRenderT = setTimeout(_renderChatFeed, 120);
    });
    _chatObs.observe(hist, { childList: true, subtree: true, characterData: true });
  }
  _chatTickInt = setInterval(_chatTick, 700);
  _setChatState(_chatWasBusy ? 'streaming' : 'idle', _chatWasBusy ? 'Responding' : 'Idle');
  _renderChatFeed();
}

let _lastWatchedSid = null;
function _stopChatWatch() {
  _lastWatchedSid = _chatSid;
  clearInterval(_chatTickInt); _chatTickInt = 0;
  clearTimeout(_chatRenderT);
  _chatObs?.disconnect(); _chatObs = null;
}

function _chatTick() {
  // Session switch: the stream (if any) keeps running in the background.
  const sid = _currentSid();
  if (sid !== _chatSid) {
    const prev = _chatSid;
    _chatSid = sid;
    _bindVoiceToSession(sid);
    _resetNarration();
    _sayQueue = [];
    _markAllSeen();
    _chatWasBusy = _chatBusy();
    if (prev) {
      const running = !!(_awaiting && _awaiting.sid === prev && _streamActive(prev));
      _systemLine(running
        ? 'Chat switched — the voice request keeps running in the background.'
        : 'Chat switched — now mirroring the new thread.', running ? 'warn' : 'info');
      _setChatState('switched', running ? 'Switched · running in bg' : 'Switched');
      // The live GPT-Live session still holds the OLD chat's conversation
      // context ("my name is Alfred"). Restart it so the new chat starts with
      // a clean transcript trail — otherwise the old chat's context poisons
      // the new one. Fire-and-forget: the restart re-seeds GPT-Live with the
      // new chat's (empty) trail via _seedChatContext.
      if (_active || _connecting) {
        _restartVoiceForSessionSwitch();
      }
    }
  }

  _dispatchTick();

  const busy = _chatBusy();
  if (busy && !_chatWasBusy) { _resetNarration(); _setChatState('streaming', 'Responding'); }
  if (!busy && _chatWasBusy) {
    const last = _lastAIEl();
    if (last && last.querySelector('.stopped-indicator')) {
      _setChatState('interrupted', 'Interrupted');
      if (!_awaiting) _systemLine('The chat response was interrupted.', 'warn');
    } else {
      _setChatState('done', 'Answered');
    }
  }
  _chatWasBusy = busy;
  _costTick = (_costTick + 1) % 10;
  if (_costTick === 0 || sid !== _costSid) { _costSid = sid; _refreshHudCost(); }
  _ingestChat(busy);
  _narrateStream(busy);
  _drainSay();
  _renderChatFeed();          // no-op unless something changed (signature check)
}

const _ingestState = new WeakMap();   // chat element → {text, stable, done}
async function _ingestChat(busy) {
  // Never snapshot a message that is still growing: an agent run streams one
  // answer across many tool rounds, and partial snapshots polluted memory and
  // re-briefed the voice model each time. Wait for the chat to be idle and the
  // text to be unchanged for ~2s, then fold each message in exactly once.
  if (busy) return;
  const msgs = _chatMsgs().slice(-12);
  const turns = [];
  for (const el of msgs) {
    let st = _ingestState.get(el);
    if (!st) { st = { text: '', stable: 0, done: false }; _ingestState.set(el, st); }
    _knownEls.add(el);
    if (st.done) continue;
    if (el.querySelector('.stopped-indicator')) { st.done = true; continue; }   // cancelled turn
    const isAi = el.classList.contains('msg-ai');
    const text = _msgText(el);
    if (!text) continue;
    if (text !== st.text) { st.text = text; st.stable = 0; continue; }
    if (++st.stable < 3) continue;          // 3 × 700ms unchanged
    st.done = true;
    const role = isAi ? 'chat_ai' : 'chat_user';
    const k = _key(role, text);
    if (_seenKeys.has(k)) continue;
    _remember(_seenKeys, k);
    turns.push({ speaker: role, text, el });
  }
  if (!turns.length) return;
  // Every chat answer reaches the voice: spoken aloud (unless narration or the
  // dispatcher already spoke it), or as silent context when "Speak results" is off.
  for (const t of turns) {
    if (t.speaker !== 'chat_ai' || _spokenEls.has(t.el) || _spokenKeys.has(_key('chat_ai', t.text))) continue;
    _spokenEls.add(t.el);
    _remember(_spokenKeys, _key('chat_ai', t.text));
    if (!_active) _missed.push(_msgText(t.el, 12000));   // voice is off — deliver on reconnect
    else if (_opts.speak) _queueSpeak(_msgText(t.el, 12000), true);
    else _injectContext(t.text);
  }
  for (const t of turns) delete t.el;
  try {
    const r = await fetch(_api('/api/realtime/memory/ingest'), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_id: _voiceId, turns, chat_session_id: _currentSid() }),
    });
    if (r.ok) _renderMemory((await r.json()).stats);
  } catch (e) { /* memory ingest is best-effort */ }
}

function _injectContext(text) {
  _brief('Chat agent answer (context for you; do not speak it now):\n' + text.slice(0, 1200), false);
}

let _chatFeedSig = '';
function _renderChatFeed() {
  const box = $('#vx-chatlog');
  if (!box) return;
  const msgs = _chatMsgs().slice(-8);
  if (!msgs.length) {
    if (_chatFeedSig !== '') { _chatFeedSig = ''; box.innerHTML = '<div class="vx-empty">What the chat agent receives and answers appears here live.</div>'; }
    return;
  }
  const busy = _chatBusy();
  // Cheap signature: role, stopped flag, text length (grows while streaming)
  // and the chat's busy state (clears the "live" tag). Only rebuild the DOM
  // when it changes — avoids cloning every message every tick.
  let sig = busy ? 'B' : 'I';
  for (const el of msgs) {
    const isAi = el.classList.contains('msg-ai');
    const stopped = !!el.querySelector('.stopped-indicator');
    sig += '|' + (isAi ? 'A' : 'U') + (stopped ? '!' : '') + (el.textContent || '').length;
  }
  if (sig === _chatFeedSig) return;
  _chatFeedSig = sig;
  const frag = document.createDocumentFragment();
  msgs.forEach((el, i) => {
    const isAi = el.classList.contains('msg-ai');
    const streaming = isAi && busy && i === msgs.length - 1;
    const stopped = !!el.querySelector('.stopped-indicator');
    const row = document.createElement('div');
    row.className = 'vx-cmsg ' + (isAi ? 'vx-cmsg-ai' : 'vx-cmsg-user');
    if (streaming) row.classList.add('streaming');
    if (stopped) row.classList.add('interrupted');
    const who = document.createElement('div');
    who.className = 'vx-who';
    who.textContent = isAi ? 'CHAT AGENT' : 'SENT TO CHAT';
    if (stopped) { const tag = document.createElement('em'); tag.textContent = 'interrupted'; who.appendChild(tag); }
    else if (streaming) { const tag = document.createElement('em'); tag.textContent = 'live'; who.appendChild(tag); }
    const say = document.createElement('div');
    say.className = 'vx-say';
    let txt = stopped ? 'Cancelled.' : _msgText(el, 4000);
    if (!txt && streaming) txt = 'Thinking…';
    say.textContent = txt.length > 420 ? (streaming ? '…' + txt.slice(-420) : txt.slice(0, 420) + '…') : txt;
    row.append(who, say);
    frag.appendChild(row);
  });
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  box.replaceChildren(frag);
  if (atBottom || busy) box.scrollTop = box.scrollHeight;
}

function _resetNarration() {
  _narratedLen = 0;
  _lastNarrateAt = 0;
  _narrateActive = false;
  _narrateStallAt = 0;
  _narratedFinal = false;
  _narrateEl = null;
  _stallSpoken = false;
}

// Said at most once per pause in new findings; re-armed when text grows again.
function _maybeSayStall(now) {
  if (_stallSpoken || _sayQueue.length || now - _narrateStallAt < 15000 || now - _lastNarrateAt < 12000) return;
  _stallSpoken = true;
  _lastNarrateAt = now;
  _sayQueue.push('[Status — the chat agent is still researching; nothing new yet] Say one short line such as ' +
    '"Let me research a bit more." Do not ask the user anything and do not guess at results.');
}

// Text meant to be spoken. Drops two kinds of noise the voice would otherwise
// read aloud:
//  - the chat agent's boilerplate about untrusted context blocks;
//  - the agent's running self-talk between tool calls ("Let me extract the
//    full body text…", "Now let me check…", "I now have the key content…").
const _BOILERPLATE_RE = /untrusted (block|context|content)|embedded instructions|instructions (inside|in) (it|that|the) block|won'?t follow|not follow(ing)? any|not act on any instructions|no tool calls|reference (material )?only/i;
const _SELF_TALK_RE = /^(?:(?:ok(?:ay)?|alright|great|good|now|next|first|then|finally|perfect)[,.!—-]?\s+)*(?:let me|let's|i'?ll now|i will now|i'?m going to|i am going to|i need to|i now have|i have now|i'?ve now|now i have|i can now|i'?ll (?:extract|read|fetch|check|search|look|pull|open|parse|scan|grab|get|verify|confirm|try|use|run|call|query))\b/i;
function _speakable(text) {
  return String(text || '').split('\n')
    .map(l => {
      const s = l.trim();
      if (!s) return l;
      if (_BOILERPLATE_RE.test(s)) return null;
      // Drop self-talk per SENTENCE, so "I now have the key content. The
      // article confirms the pardon." keeps the finding. Only short sentences
      // count — a long one that starts with "Let me…" is real content.
      const kept = s.split(/(?<=[.!?])\s+/)
        .filter(sent => !(sent.length < 200 && _SELF_TALK_RE.test(sent)));
      return kept.length ? kept.join(' ') : null;
    })
    .filter(l => l !== null)
    .join('\n').replace(/\s*·\s*\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

// GPT-Live is mid-response. Expires after 20s so a missed response.done can
// never block speech for the rest of the call.
function _gptSpeaking() {
  if (_respActive && Date.now() - _respSince > 20000) _respActive = false;
  return _respActive || _out > 0.07;
}

// ── Speech queue ────────────────────────────────────────────────────────────
// Everything the chat produces is passed up to the voice and spoken. Long
// content is split into parts that fit GPT-Live's 500-token append limit and
// delivered one at a time — never over GPT-Live itself or over the user.
const _SAY_CHUNK = 1100;      // chars of content per part (+ instructions < _BRIEF_MAX)
const _SAY_MAX_PARTS = 8;     // ~9k chars spoken; beyond that, point to the chat
let _sayQueue = [];
let _lastSayAt = 0;
const _spokenEls = new WeakSet();   // chat bubbles already spoken (no repeats)

function _chunks(text) {
  const out = [];
  let cur = '';
  for (let line of text.split('\n')) {
    while (line.length > _SAY_CHUNK) {
      if (cur) { out.push(cur); cur = ''; }
      let cut = line.lastIndexOf(' ', _SAY_CHUNK);
      if (cut < _SAY_CHUNK / 2) cut = _SAY_CHUNK;
      out.push(line.slice(0, cut));
      line = line.slice(cut).trimStart();
    }
    if (cur && cur.length + line.length + 1 > _SAY_CHUNK) { out.push(cur); cur = line; }
    else cur = cur ? cur + '\n' + line : line;
  }
  if (cur.trim()) out.push(cur);
  return out.filter(c => c.trim());
}

function _queueSpeak(text, final) {
  text = _speakable(text);
  if (!text) return;
  let parts = _chunks(text);
  const more = parts.length > _SAY_MAX_PARTS;
  if (more) parts = parts.slice(0, _SAY_MAX_PARTS);
  const n = parts.length;
  parts.forEach((p, i) => {
    const tag = final
      ? `[Chat agent — FINISHED${n > 1 ? `, part ${i + 1} of ${n}` : ''}]`
      : '[Chat agent — still working; more will follow]';
    const lead = i > 0 ? ' Continue straight on from the previous part; do not repeat it or re-introduce it.' : '';
    const tail = final && i === n - 1 && more ? ' Then mention briefly that the remainder is in the chat.' : '';
    _sayQueue.push(`${tag}${lead}\n${p}\n\nSay this to the user now, in their language — all of it, naturally ` +
      '(names, numbers, dates; not a robotic table read). ' +
      (final ? 'Do NOT just say it is in the chat.' : 'Do not ask questions and do not claim the work is finished.') + tail);
  });
}

function _drainSay() {
  if (!_sayQueue.length || !_active || !_injectOK) return;
  const now = Date.now();
  if (_gptSpeaking() || _lvl > 0.11) return;        // never talk over GPT-Live or the user
  if (now - _lastAudioAt < 900) return;             // let the previous part finish cleanly
  if (now - _lastSayAt < 3500) return;              // give the last part time to start playing
  _lastSayAt = now;
  _brief(_sayQueue.shift(), true);
}

function _narrateStream(busy) {
  if (!_active || !_injectOK || !_opts.speak) return;
  const el = _lastAIEl();
  if (!el) return;
  const now = Date.now();

  if (busy) {
    // Only narrate the bubble that is actually streaming — at run start the
    // last .msg-ai can still be the previous, already-answered message.
    if (!el.classList.contains('streaming')) {
      if (_narrateActive) _maybeSayStall(now);
      return;
    }
    if (!_narrateActive) {
      _narrateActive = true;
      _narrateStallAt = now;
    }
    // Agent runs open a new bubble per tool round: flush whatever of the
    // previous round was not spoken yet, then restart the watermark.
    if (el !== _narrateEl) {
      if (_narrateEl) {
        const rest = _speakable(_msgText(_narrateEl, 12000)).slice(_narratedLen);
        if (rest.trim()) _queueSpeak(rest, false);
        _spokenEls.add(_narrateEl);
      }
      _narrateEl = el;
      _narratedLen = 0;
    }
    const text = _speakable(_msgText(el, 12000));
    const delta = text.slice(_narratedLen);
    if (delta.length > 0) { _narrateStallAt = now; _stallSpoken = false; }
    // Batch: a substantial chunk, or a smaller one once it has settled a while.
    const enough = delta.length >= 350;
    const settled = delta.length >= 80 && now - _lastNarrateAt >= 8000;
    if (enough || settled) {
      // Cut on a line / sentence boundary — the last line may still be streaming.
      let cut = delta.lastIndexOf('\n');
      if (cut < 40) {
        const s = delta.lastIndexOf('. ');
        cut = s >= 40 ? s + 1 : (settled ? delta.length : -1);
      }
      if (cut > 0) {
        _narratedLen += cut;
        _lastNarrateAt = now;
        _queueSpeak(delta.slice(0, cut), false);
      }
    } else {
      _maybeSayStall(now);
    }
  } else if (_narrateActive) {
    // Run finished: speak everything not yet said, marked as final.
    const text = _speakable(_msgText(el, 12000));
    if (!text) return;
    if (el.querySelector('.stopped-indicator')) { _resetNarration(); return; }  // cancelled run
    _narrateActive = false;
    _narratedFinal = true;
    _lastNarrateAt = now;
    _spokenEls.add(el);
    _remember(_spokenKeys, _key('chat_ai', _msgText(el)));
    const rest = el === _narrateEl ? text.slice(_narratedLen) : text;
    if (rest.trim()) _queueSpeak(rest, true);
    else _sayQueue.push('[Chat agent — FINISHED] Everything was already told. In one short line, tell the user it is done.');
  }
}

function _speakResult(item, el) {
  if (!_opts.speak || !_active || !_injectOK) return;
  // Incremental narration owns the final summary for any run it narrated —
  // it fires right after this (same tick), so skip here to avoid speaking twice.
  if (_narrateActive || _narratedFinal) { _narratedFinal = false; return; }
  el = el || _lastAIEl();
  if (!el || _spokenEls.has(el)) return;
  const text = _msgText(el, 12000);
  if (!_speakable(text)) return;
  _spokenEls.add(el);
  _remember(_spokenKeys, _key('chat_ai', _msgText(el)));
  _systemLine(`Chat finished “${item?.title || 'request'}” — telling you.`, 'done');
  _queueSpeak(text, true);
}

function _nowNote() {
  const d = new Date();
  return `Current local time: ${d.toLocaleString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })} (${Intl.DateTimeFormat().resolvedOptions().timeZone}).`;
}

// GPT-Live injects context over the data channel with session.*.append events —
// NOT the Realtime API's conversation.item.create (which GPT-Live rejects).
//   - session.thinking.append  → quiet factual context the model can use later
//   - session.commentary.append → results GPT-Live should paraphrase and say aloud
// Both take a plain-string `content` (≤500 tokens), a unique `event_id`, and a
// `delegation_id` (null for session-wide context). A commentary append makes the
// model speak on its own, so there is no response.create to synchronise.
let _briefSeq = 0;
// GPT-Live rejects appends over 500 tokens. ~1600 chars stays safely under
// that even for token-dense text; keep the tail, where instructions live.
const _BRIEF_MAX = 1600;
function _brief(text, speak) {
  if (!_active || !_injectOK || !text) return;
  if (text.length > _BRIEF_MAX) text = '…' + text.slice(-(_BRIEF_MAX - 1));
  _lastInjectAt = Date.now();
  const eventId = 'ody_' + (++_briefSeq) + '_' + Date.now().toString(36);
  const type = speak ? 'session.commentary.append' : 'session.thinking.append';
  _dcSend({ type, event_id: eventId, delegation_id: null, content: text });
}

function _flushBriefs() {
  if (!_pendingBriefs.length || _respActive) return;
  const all = _pendingBriefs.splice(0, _pendingBriefs.length);
  _brief(all.join('\n\n'), true);
}

// ═══════════════════════════════════════════════════════════════════════════
//  REALTIME (WebRTC)
// ═══════════════════════════════════════════════════════════════════════════

// Pull transcript text out of whatever shape the event uses.
function _evText(evt) {
  for (const k of ['delta', 'text', 'transcript']) {
    if (typeof evt[k] === 'string' && evt[k]) return evt[k];
  }
  const parts = evt.item?.content || evt.content;
  if (Array.isArray(parts)) {
    for (const p of parts) {
      if (typeof p?.transcript === 'string' && p.transcript) return p.transcript;
      if (typeof p?.text === 'string' && p.text) return p.text;
    }
  }
  if (typeof evt.part?.transcript === 'string') return evt.part.transcript;
  if (typeof evt.transcript?.text === 'string') return evt.transcript.text;
  return '';
}

// GPT-Live's exact transcript event names aren't pinned down in our docs, so
// classify by pattern instead of a fixed list: direction (user vs. assistant)
// from the type/role, delta vs. final from the suffix. Every new type is
// logged once to the console (window.__odyVoiceEvents) for diagnosis.
function _handleEvent(evt) {
  const t = String(evt?.type || '');
  const n = (_eventTypes.get(t) || 0) + 1;
  _eventTypes.set(t, n);
  if (n === 1) console.info('[voice] GPT-Live event:', t, evt);
  window.__odyVoiceEvents = _eventTypes;
  // GPT-Live bills per response; its usage block arrives on response.done.
  if (t === 'response.done' && evt.response && evt.response.usage) _reportRealtimeUsage(evt.response.usage);
  if (t === 'response.created') { _respActive = true; _respSince = Date.now(); }
  if (t === 'response.done' || t === 'response.cancelled') { _respActive = false; setTimeout(_flushBriefs, 300); }

  if (t === 'session.started' || t === 'session.created') {
    _setSub('Linked — speak naturally');
    _injectOK = true; _respActive = false; _pendingBriefs = [];
    _userTurnOpen = false; _userBase = 0;
    _brief('[Context — not said by the user; do not reply to it] ' + _nowNote() +
      ' Use this when asked the time or date; time passes after it.', false);
    _seedTries = 0;
    setTimeout(_seedChatContext, 300);
    return;
  }
  if (t === 'session.closed') {
    if (_active) { _systemLine('Session closed by the server.', 'warn'); stopVoice(); }
    return;
  }
  if (t === 'error' || t.endsWith('.error') || t.endsWith('.failed')) {
    console.warn('realtime error', evt);
    // An error right after we injected chat context means GPT-Live doesn't
    // accept that command — stop injecting instead of spamming the HUD.
    const emsg = String(evt.error?.message || evt.message || evt.error?.code || '');
    // "Already has an active response" → just retry the briefing once it's done.
    if (/active[_ ]response|already.*response|in progress/i.test(emsg)) { _respActive = true; _respSince = Date.now(); return; }
    // Only a genuine "this command isn't supported" disables briefings.
    if (_injectOK && Date.now() - _lastInjectAt < 4000 &&
        /conversation\.item|unknown.*(event|type)|invalid.*(event|type)|not supported|unsupported/i.test(emsg)) {
      _injectOK = false;
      _systemLine('GPT-Live rejected injected chat context — chat answers stay in the HUD and memory.', 'info');
      return;
    }
    _systemLine('Voice: ' + (evt.error?.message || evt.message || 'unknown error'), 'warn');
    return;
  }
  // Barge-in: lock in what the assistant said so far.
  if (/speech_started|speech\.started|input[^.]*\.started|user[^.]*\.(started|speaking)/.test(t)) {
    if (_aiLine) _aiFinalize();
    return;
  }
  // Delegated backend work (Responses events nested in the session).
  if (t.startsWith('response.event') || t.includes('delegat')) return;

  const isFinal = /[._](done|completed|final|finished|end)$/.test(t) || evt.is_final === true || evt.final === true;
  if (!/transcri|caption|text|speech/.test(t)) {
    if (/^response\.(done|completed)$/.test(t)) _aiFinalize();
    return;
  }
  const text = _evText(evt);
  const role = evt.role || evt.item?.role || evt.speaker;
  const isUser = role === 'assistant' ? false : (
    role === 'user' ||
    /input|user|caller|human/.test(t) ||
    (/transcription/.test(t) && !/output|assistant|response|caption|agent/.test(t))
  );
  const isDelta = /delta/.test(t);
  if (isUser) {
    if (isFinal) _userFinal(text || null);
    else if (text) _userDelta(text);
  } else if (isFinal) {
    if (text && !isDelta) _aiReplace(text);
    _aiFinalize();
  } else if (text) {
    _aiDelta(text);
  }
}

// Per-chat transcript trail. Each chat's live transcript is saved here so it
// can be restored when the user returns to that chat — and a NEW chat starts
// empty instead of inheriting the previous chat's conversation.
const _transcriptStore = {};   // voiceId -> innerHTML of #vx-transcript

function _saveTranscript(voiceId) {
  const box = $('#vx-transcript');
  if (box) _transcriptStore[voiceId] = box.innerHTML;
}

function _restoreTranscript(voiceId) {
  const box = $('#vx-transcript');
  if (!box) return;
  const saved = _transcriptStore[voiceId];
  if (saved) {
    box.innerHTML = saved;
  } else {
    box.innerHTML = '<div class="vx-empty">Conversation appears here in real time.<br><kbd>Space</kbd> connect · <kbd>M</kbd> mute · <kbd>⏎</kbd> send now · <kbd>Esc</kbd> close</div>';
  }
}

// Clear the in-progress live transcript state so a half-finished turn from the
// OLD chat is never finalized into the NEW chat's context (or re-seeded into
// GPT-Live after a session-switch restart).
function _clearLiveTranscriptState() {
  _aiLine = null; _aiText = ''; clearTimeout(_aiFinalizeT);
  _userLine = null; _userText = ''; _userTurnOpen = false; _userBase = 0;
  clearTimeout(_userFinalizeT);
}

// Per-session state that must NOT leak between chats. When the user opens a
// new chat, the voice mode starts clean — no memory summary, pasted notes,
// resume line, missed answers, or unsent speech from the previous session.
let _pendingVoiceId = null;   // ephemeral key while the chat is not yet materialized
function _bindVoiceToSession(sid) {
  let vid;
  if (sid) {
    vid = `chat-${sid}`;
    _pendingVoiceId = null;
  } else {
    // New chat, not yet materialized: use a fresh ephemeral key so it can
    // NEVER load a previous chat's voice memory. The old 'global' fallback
    // was a shared key that leaked a prior chat's context into a new chat.
    if (!_pendingVoiceId) {
      _pendingVoiceId = `pending-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    }
    vid = _pendingVoiceId;
  }
  if (vid === _voiceId) return;
  // Save the OLD chat's transcript trail before switching, so it can be
  // restored when the user returns to that chat.
  _saveTranscript(_voiceId);
  _clearLiveTranscriptState();
  _voiceId = vid;
  _memPreview = '';
  _notes = [];
  _lastAiSaid = '';
  _lastAiSaidAt = 0;
  _missed = [];
  _pending = [];
  _pendingCommit = null;
  _sayQueue = [];
  _seenKeys.clear();
  _spokenKeys.clear();
  _resetNarration();
  // Restore the NEW chat's transcript trail (empty for a brand-new chat).
  _restoreTranscript(vid);
}

// Restart the live GPT-Live session so its accumulated conversation context is
// cleared. Called when the user switches chats while voice is connected —
// otherwise the old chat's transcript ("my name is Alfred") leaks into the new
// chat and the voice assistant answers from the wrong context. After the
// restart, _seedChatContext re-seeds GPT-Live with the NEW chat's (empty) trail.
async function _restartVoiceForSessionSwitch() {
  if (!_active && !_connecting) return;
  await stopVoice();
  // startVoice() re-binds to the current session (no-op — already bound) and
  // re-seeds GPT-Live with the new chat's context via _seedChatContext.
  await startVoice();
}

async function startVoice() {
  if (_active || _connecting) return;
  _connecting = true;
  _setState('connecting');
  const sid = window.sessionModule?.getCurrentSessionId?.();
  _bindVoiceToSession(sid);

  try {
    _micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    _connecting = false;
    _setState('error');
    _setSub('Microphone access denied');
    return;
  }

  _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  try { await _audioCtx.resume(); } catch (e) { /* ignore */ }
  _micAnalyser = _audioCtx.createAnalyser();
  _micAnalyser.fftSize = 1024;
  _micAnalyser.smoothingTimeConstant = 0.72;
  _audioCtx.createMediaStreamSource(_micStream).connect(_micAnalyser);

  try {
    // Use a LOCAL pc for the whole handshake. `_pc` is a module global that
    // _teardown() nulls out (and a quick disconnect/reconnect can replace)
    // while this async handshake awaits getUserMedia/fetch. Referencing the
    // global after an await could then touch a different/closed pc, which is
    // what makes setRemoteDescription throw "Called in wrong state: stable".
    const pc = new RTCPeerConnection();
    _pc = pc;   // registered for teardown, but handshake below uses the local ref
    pc.addTrack(_micStream.getAudioTracks()[0], _micStream);
    pc.ontrack = (e) => {
      const stream = e.streams[0] || new MediaStream([e.track]);
      _audioEl = new Audio();
      _audioEl.autoplay = true;
      _audioEl.srcObject = stream;
      _audioEl.play().catch(() => _setSub('Click anywhere to enable audio'));
      try {
        _outAnalyser = _audioCtx.createAnalyser();
        _outAnalyser.fftSize = 1024;
        _outAnalyser.smoothingTimeConstant = 0.7;
        _audioCtx.createMediaStreamSource(stream).connect(_outAnalyser);
      } catch (err) { _outAnalyser = null; }
    };
    pc.onconnectionstatechange = () => {
      if (['failed', 'disconnected'].includes(pc?.connectionState) && _active) {
        _systemLine('Connection lost.', 'warn');
        stopVoice(true);
      }
    };

    _dc = pc.createDataChannel('oai-events');
    _dc.onmessage = (e) => { try { _handleEvent(JSON.parse(e.data)); } catch (err) { /* ignore */ } };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    if (pc.iceGatheringState !== 'complete') {
      await new Promise((resolve) => {
        const timeout = setTimeout(resolve, 3000);
        const onState = () => { if (pc.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); } };
        pc.addEventListener('icegatheringstatechange', onState);
        onState();
      });
    }
    const sdp = pc.localDescription?.sdp || offer.sdp;
    const resp = await fetch(_api('/api/realtime/session'), {
      method: 'POST',
      body: JSON.stringify({ sdp, chat_session_id: _currentSid() }),
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
    });
    if (!resp.ok) {
      let txt = await resp.text().catch(() => '');
      try { txt = JSON.parse(txt).detail || txt; } catch (e) { /* raw */ }
      throw new Error(`HTTP ${resp.status} ${String(txt).slice(0, 160)}`);
    }
    const result = await resp.json();
    const answerSdp = result?.transport?.sdp;
    if (!answerSdp) throw new Error('No SDP answer from GPT-Live');
    // If the connection was reset/reconnected while we awaited the SDP answer,
    // the pending offer is gone and applying an answer would throw
    // "Called in wrong state". Fail with a clean, retryable message instead.
    if (pc.signalingState !== 'have-local-offer') {
      throw new Error('Connection was reset before the voice answer arrived — please reconnect.');
    }
    await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
  } catch (e) {
    console.error('realtime connect failed', e);
    _teardown();
    _connecting = false;
    _setState('error');
    _setSub(e.message || 'Connection failed');
    _systemLine('Link failed — ' + (e.message || e), 'warn');
    return;
  }

  _connecting = false;
  _active = true;
  _muted = false;
  _callStart = Date.now();
  clearInterval(_timerInt);
  _timerInt = setInterval(() => {
    const t = $('.vx-timer'); if (t) t.textContent = _fmtTime(Date.now() - _callStart);
  }, 500);
  _root?.classList.add('connected');
  _setState('listening');
  _systemLine('Neural link established.', 'done');
  _refreshMemory();
  // If nothing we recognise as a transcript arrives, say so in the HUD and
  // list the event types GPT-Live did send.
  _transcriptSeen = false;
  clearTimeout(_diagT);
  _diagT = setTimeout(() => {
    if (!_active || _transcriptSeen) return;
    const types = [..._eventTypes.keys()].filter(Boolean);
    console.info('[voice] event types seen so far', Object.fromEntries(_eventTypes));
    _systemLine(types.length
      ? 'No transcript events recognised yet. GPT-Live sent: ' + types.slice(0, 14).join(', ')
      : 'GPT-Live has not sent any data-channel events yet.', 'warn');
  }, 20000);
}

function _teardown() {
  try { _dc?.close(); } catch (e) { /* ignore */ }
  try { _pc?.close(); } catch (e) { /* ignore */ }
  _micStream?.getTracks().forEach(t => t.stop());
  if (_audioEl) { _audioEl.pause(); _audioEl.srcObject = null; }
  _audioCtx?.close().catch(() => {});
  _micStream = _audioCtx = _micAnalyser = _outAnalyser = _audioEl = _pc = _dc = null;
}

async function stopVoice(keepError = false) {
  if (!_active && !_connecting) return;
  if (_aiLine) _aiFinalize();
  if (_userText) _userFinal(null);
  _dcSend({ type: 'session.close' });
  _sayQueue = [];
  _pendingCommit = null;
  clearTimeout(_diagT);
  _active = false;
  _connecting = false;
  _teardown();
  clearInterval(_timerInt);
  _root?.classList.remove('connected');
  _setState(keepError ? 'error' : 'idle');
  _caption('', '');
  // Flush whatever was said last through the pipeline.
  if (_pending.some(t => t.speaker === 'You')) _schedulePipeline(50);
}

function _toggleMute() {
  if (!_active || !_micStream) return;
  _muted = !_muted;
  _micStream.getAudioTracks().forEach(t => { t.enabled = !_muted; });
  _setState(_muted ? 'muted' : 'listening');
}

function _forceSend() {
  if (_userText) _userFinal(null);
  if (_aiLine) _aiFinalize();
  clearTimeout(_pipeTimer);
  _runPipeline(true);
}

async function _refreshMemory() {
  try {
    const r = await fetch(_api(`/api/realtime/memory?voice_id=${encodeURIComponent(_voiceId)}`), { credentials: 'same-origin' });
    if (r.ok) _renderMemory((await r.json()).stats);
  } catch (e) { /* ignore */ }
}

async function _refreshStatus() {
  try {
    const r = await fetch(_api('/api/realtime/status'), { credentials: 'same-origin' });
    if (!r.ok) return;
    const s = await r.json();
    if (s.assistant_name) {
      _assistantName = String(s.assistant_name).toUpperCase();
      const t = $('.vx-title'); if (t) t.textContent = _assistantName;
    }
    const set = (k, ok, title) => {
      const b = $(`.vx-badge[data-k="${k}"]`);
      if (!b) return;
      b.dataset.ok = ok ? '1' : '0';
      if (title) b.title = title;
    };
    set('live', s.realtime, s.realtime ? `Realtime: ${s.realtime_model}` : 'OpenAI API key not configured');
    set('jev', s.jev, s.jev ? `Decider: ${s.jev_model}` : 'No OpenRouter key — falling back to the utility LLM');
    set('util', s.utility, s.utility ? `Compressor/forger: ${s.utility_model}` : 'No utility/default model configured');
    const u = $('.vx-badge[data-k="util"] span');
    if (u && s.utility_model) u.textContent = s.utility_model.split('/').pop().slice(0, 22);
    if (!s.realtime && _state === 'idle') _setSub('Add an OpenAI API key in settings to enable GPT-Live');
  } catch (e) { /* ignore */ }
}

// ═══════════════════════════════════════════════════════════════════════════
//  OPEN / CLOSE
// ═══════════════════════════════════════════════════════════════════════════

function _bind() {
  _root.addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'close') closeVoiceMode();
    else if (act === 'core') toggleVoice();
    else if (act === 'mute') _toggleMute();
    else if (act === 'force') _forceSend();
    else if (act === 'reset-mem') _resetMemory();
    else if (act === 'errors') _toggleErrors();
    else if (act === 'clear-errors') _clearErrors();
    if (_audioEl && _audioEl.paused) _audioEl.play().catch(() => {});
  });
  $('#vx-queue').addEventListener('click', _onQueueClick);
  // Type / paste box: "Add context" (⌥⏎) or "Ask" (⌘⏎ / Ctrl⏎).
  const ta = $('#vx-inject-ta');
  const submitInject = (mode) => {
    const text = (ta.value || '').trim();
    if (!text) return;
    ta.value = '';
    if (mode === 'ask') _askTyped(text); else _addContext(text);
  };
  _root.querySelectorAll('[data-inject]').forEach(b => b.addEventListener('click', () => submitInject(b.dataset.inject)));
  ta.addEventListener('keydown', (ke) => {
    if (ke.key === 'Enter' && (ke.metaKey || ke.ctrlKey)) { ke.preventDefault(); submitInject('ask'); }
    else if (ke.key === 'Enter' && ke.altKey) { ke.preventDefault(); submitInject('context'); }
  });
  _root.querySelectorAll('[data-opt]').forEach(inp => {
    inp.checked = !!_opts[inp.dataset.opt];
    inp.addEventListener('change', () => {
      _opts[inp.dataset.opt] = inp.checked;
      try { localStorage.setItem('odysseus-voice-opts', JSON.stringify(_opts)); } catch (e) { /* ignore */ }
      if (inp.dataset.opt === 'review' && !inp.checked) {
        _queue.forEach(q => { if (q.status === 'review') q.status = 'queued'; });
        _renderQueue(); _ensureDispatcher();
      }
    });
  });
  _keyHandler = (e) => {
    if (!_root || _root.hidden) return;
    const typing = e.target.closest?.('textarea, input[type="text"], [contenteditable="true"]');
    if (e.key === 'Escape') { e.preventDefault(); closeVoiceMode(); return; }
    if (typing) return;
    if (e.code === 'Space') { e.preventDefault(); toggleVoice(); }
    else if (e.key === 'm' || e.key === 'M') { e.preventDefault(); _toggleMute(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (_active) _forceSend(); }
  };
  document.addEventListener('keydown', _keyHandler, true);
  _bindCodePipelineEvents();
}

// ── Code-execution pipeline (decide → forge → execute) header mirror ────────
// The normal chat runs the code pipeline (static/js/codePipeline.js) and emits
// window CustomEvents. The voice HUD mirrors them into a header badge + a
// 3-stage strip so the same neural pipeline is visible in both surfaces.
function _bindCodePipelineEvents() {
  const strip = () => $('#vx-code-strip');
  const badge = () => $('.vx-code-badge');
  const setStage = (stage, status) => {
    const s = strip();
    if (!s) return;
    s.hidden = false;
    s.querySelectorAll('[data-cs]').forEach(el => {
      el.dataset.status = el.dataset.cs === stage ? status : (el.dataset.status || '');
    });
  };
  const reset = () => {
    const s = strip();
    if (s) { s.hidden = true; s.querySelectorAll('[data-cs]').forEach(el => { delete el.dataset.status; }); }
    const b = badge();
    if (b) b.dataset.ok = '';
  };
  window.addEventListener('code-pipeline-start', () => {
    const b = badge(); if (b) b.dataset.ok = '1';
    reset();
    const s = strip(); if (s) s.hidden = false;
  });
  window.addEventListener('code-pipeline-stage', (e) => {
    const ev = e.detail || {};
    setStage(ev.stage, ev.status);
  });
  window.addEventListener('code-pipeline-executed', (e) => {
    const ev = e.detail || {};
    setStage('execute', ev.exit_code === 0 ? 'done' : 'error');
  });
  window.addEventListener('code-pipeline-done', (e) => {
    const ev = e.detail || {};
    const b = badge();
    if (b) b.dataset.ok = ev.executed ? '1' : '0';
    if (!ev.executed) setStage('execute', 'idle');
  });
  window.addEventListener('code-pipeline-end', () => {
    // Leave the strip visible briefly, then clear it.
    setTimeout(() => {
      const b = badge(); if (b) b.dataset.ok = '';
      reset();
    }, 4000);
  });
}

async function _resetMemory() {
  const ok = window.uiModule?.styledConfirm
    ? await window.uiModule.styledConfirm('Forget this voice memory? The chat itself is not affected.', { confirmText: 'Forget', danger: true })
    : window.confirm('Forget this voice memory?');
  if (!ok) return;
  try {
    const r = await fetch(_api('/api/realtime/memory/reset'), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_id: _voiceId }),
    });
    if (r.ok) { _renderMemory((await r.json()).stats); _systemLine('Voice memory cleared.', 'info'); }
  } catch (e) { /* ignore */ }
}

export function openVoiceMode({ autostart = true } = {}) {
  _ensureCSS();
  try { Object.assign(_opts, JSON.parse(localStorage.getItem('odysseus-voice-opts') || '{}')); } catch (e) { /* ignore */ }
  if (!_root) {
    _root = _buildHUD();
    document.body.appendChild(_root);
    _bind();
    _initGL($('#vx-canvas'));
    _initBG($('#vx-bg-canvas'));
    _measureOrb();
    if (!_orbResizeBound) {
      _orbResizeBound = true;
      window.addEventListener('resize', _measureOrb);
      window.addEventListener('orientationchange', _measureOrb);
    }
  }
  _root.hidden = false;
  document.documentElement.classList.add('vx-open');
  requestAnimationFrame(() => _root.classList.add('open'));
  if (!_raf) { _t0 = performance.now(); _raf = requestAnimationFrame(_loop); }
  else { _lastFrameAt = performance.now(); }
  const sid = window.sessionModule?.getCurrentSessionId?.();
  if (!_active) _bindVoiceToSession(sid);
  _setState(_active ? 'listening' : 'idle');
  _refreshStatus();
  _refreshMemory();
  _renderQueue();
  _startChatWatch();
  if (autostart && !_active) startVoice();
}

export async function closeVoiceMode() {
  await stopVoice();
  if (!_root) return;
  _root.classList.remove('open');
  document.documentElement.classList.remove('vx-open');
  setTimeout(() => {
    if (_root && !_root.classList.contains('open')) {
      _root.hidden = true;
      if (_raf) { cancelAnimationFrame(_raf); _raf = 0; }
      _stopChatWatch();
    }
  }, 420);
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

export async function toggleVoice() {
  if (_active || _connecting) { await stopVoice(); return; }
  if (!_root || _root.hidden) { openVoiceMode({ autostart: true }); return; }
  await startVoice();
}

export function initVoice() {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
  }
  document.body.addEventListener('click', (e) => {
    // Composer mic → full-screen voice mode.
    if (e.target.closest('#voice-btn')) {
      e.preventDefault();
      openVoiceMode({ autostart: true });
    }
  });
}

export default { speak, isRecording, toggleVoice, initVoice, openVoiceMode, closeVoiceMode };