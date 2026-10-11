// ═══════════════════════════════════════════════════════════════════════════
//  Odysseus · Chat background — the Advanced Voice Mode GLSL shader, reused
//  as the chat area's ambient backdrop. No audio: the FFT texture is driven by
//  a gentle idle sine so the aurora still breathes. Accent is theme-aware
//  (--accent-primary / --red), re-read live when the theme changes.
//  Self-contained: creates its own fixed canvas behind the UI.
// ═══════════════════════════════════════════════════════════════════════════

(function () {
  'use strict';

  const BANDS = 64;
  const _reducedMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  const VERT = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

  // Mirrors voice.js BG_FRAG (aurora + grid + scan + sweep + halo + vignette).
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

  float gs = 46.0;
  vec2 g = frag/gs + vec2(t*0.03, t*0.03);
  vec2 gv = abs(fract(g - 0.5) - 0.5);
  float line = max(step(gv.x, 0.018), step(gv.y, 0.018));
  float gridMask = smoothstep(1.1, 0.15, radl);
  col += u_color * line * 0.04 * gridMask;

  float scan = 0.5 + 0.5*sin(frag.y * 1.5);
  col += vec3(0.03) * step(0.98, scan);

  float sw = fract(t*0.14);
  float band = smoothstep(0.025, 0.0, abs(frag.y/u_res.y - (1.0 - sw)));
  col += u_color * band * 0.05;

  float d = length(frag - u_orb);
  float halo = exp(-(d*d) / max(0.0001, 1.5*u_orbR*u_orbR));
  col += u_color * halo * (0.09 + 0.22*e);

  col *= smoothstep(1.65, 0.25, radl);

  gl_FragColor = vec4(col, 1.0);
}
`;

  let _canvas = null, _gl = null, _prog = null, _tex = null, _u = {};
  let _fftBytes = new Uint8Array(BANDS);
  let _t0 = performance.now();
  let _raf = null;
  let _col = [0.36, 0.62, 0.78]; // fallback: voice idle blue
  let _scale = 1, _scaleAt = 0, _lastFrameAt = 0;

  function _compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      console.error('chat-bg shader error', gl.getShaderInfoLog(s));
      return null;
    }
    return s;
  }

  function _hexToRgb01(hex) {
    hex = (hex || '').trim().replace('#', '');
    if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
    if (!/^[0-9a-fA-F]{6}$/.test(hex)) return null;
    const n = parseInt(hex, 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }

  function _readAccent() {
    const cs = getComputedStyle(document.documentElement);
    const raw = cs.getPropertyValue('--accent-primary').trim() ||
                cs.getPropertyValue('--red').trim();
    const rgb = _hexToRgb01(raw);
    if (rgb) _col = rgb;
  }

  function _init() {
    _canvas = document.createElement('canvas');
    _canvas.id = 'chat-bg-canvas';
    _canvas.setAttribute('aria-hidden', 'true');
    _canvas.style.cssText =
      'position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none;';
    document.body.insertBefore(_canvas, document.body.firstChild);

    _gl = _canvas.getContext('webgl', { antialias: false, alpha: false, depth: false, stencil: false });
    if (!_gl) { _canvas.remove(); _canvas = null; return; }

    const vs = _compile(_gl, _gl.VERTEX_SHADER, VERT);
    const fs = _compile(_gl, _gl.FRAGMENT_SHADER, BG_FRAG);
    if (!vs || !fs) { _gl = null; return; }

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

    for (const n of ['u_res', 'u_time', 'u_fft', 'u_level', 'u_out', 'u_color', 'u_orb', 'u_orbR']) {
      _u[n] = _gl.getUniformLocation(_prog, n);
    }
    _gl.uniform1i(_u.u_fft, 0);

    _readAccent();
    _raf = requestAnimationFrame(_loop);

    // Re-read accent when the theme changes (theme.js sets CSS vars on <html>).
    if (window.MutationObserver) {
      const mo = new MutationObserver(_readAccent);
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
    }
    window.addEventListener('resize', _onResize);
  }

  function _onResize() {
    // Canvas size is handled per-frame in _loop; nothing else needed.
  }

  function _loop(now) {
    _raf = requestAnimationFrame(_loop);
    if (!_gl || !_canvas) return;

    // Adaptive render scale (mirrors voice.js) to stay cheap on slow devices.
    const dt = now - _lastFrameAt;
    _lastFrameAt = now;
    if (now - _scaleAt > 2000) {
      if (dt > 26) _scale = Math.max(0.5, _scale - 0.1);
      else if (dt < 14 && _scale < 1) _scale = Math.min(1, _scale + 0.05);
      _scaleAt = now;
    }

    const dpr = Math.min(window.devicePixelRatio || 1, 1.25) * _scale;
    const cw = _canvas.clientWidth, ch = _canvas.clientHeight;
    const w = Math.max(2, Math.round(cw * dpr));
    const h = Math.max(2, Math.round(ch * dpr));
    if (_canvas.width !== w || _canvas.height !== h) {
      _canvas.width = w; _canvas.height = h;
      _gl.viewport(0, 0, w, h);
    }

    const t = _reducedMotion ? 0.0 : (now - _t0) / 1000;

    // Idle FFT: a slow breathing sine so the radial spectrum has gentle life.
    for (let i = 0; i < BANDS; i++) {
      const x = i / (BANDS - 1);
      const wave = 0.5 + 0.5 * Math.sin(x * 6.0 - t * 0.6);
      const breathe = 0.5 + 0.5 * Math.sin(t * 0.5);
      _fftBytes[i] = Math.round(18 + 26 * wave * breathe);
    }

    _gl.clearColor(0.01, 0.02, 0.05, 1.0);
    _gl.clear(_gl.COLOR_BUFFER_BIT);
    _gl.activeTexture(_gl.TEXTURE0);
    _gl.bindTexture(_gl.TEXTURE_2D, _tex);
    _gl.texSubImage2D(_gl.TEXTURE_2D, 0, 0, 0, BANDS, 1, _gl.LUMINANCE, _gl.UNSIGNED_BYTE, _fftBytes);

    _gl.uniform2f(_u.u_res, w, h);
    _gl.uniform1f(_u.u_time, t);
    _gl.uniform1f(_u.u_level, 0.12);
    _gl.uniform1f(_u.u_out, 0.0);
    _gl.uniform3f(_u.u_color, _col[0], _col[1], _col[2]);
    _gl.uniform2f(_u.u_orb, w * 0.5, h * 0.5);
    _gl.uniform1f(_u.u_orbR, Math.min(w, h) * 0.16);
    _gl.drawArrays(_gl.TRIANGLES, 0, 3);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init, { once: true });
  } else {
    _init();
  }
})();