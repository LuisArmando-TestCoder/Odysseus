#!/usr/bin/env python3
"""
Silbido simple generado con una transformada de Fourier (IFFT) y reproducido una sola vez.
Onda senoidal pura (una única línea espectral), sin efectos ni envolventes adicionales.
"""
import argparse, math, os, struct, subprocess, sys, tempfile, wave

SAMPLE_RATE = 44100
BITS = 16

def build_whistle(sample_rate, duration, freq, amplitude):
    n = int(sample_rate * duration)
    k = max(0, min(int(round(freq * n / sample_rate)), n - 1))
    try:
        import numpy as np
        spectrum = np.zeros(n, dtype=complex)
        spectrum[k] = -1j * amplitude * n
        return np.fft.ifft(spectrum).real
    except ImportError:
        return [amplitude * math.sin(2.0 * math.pi * k * i / n) for i in range(n)]

def samples_to_pcm16(samples):
    peak = max(1e-9, max(abs(s) for s in samples))
    scale = (2 ** (BITS - 1) - 1) / peak
    return [int(s * scale) for s in samples]

def write_wav(path, sample_rate, pcm_samples):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(BITS // 8); w.setframerate(sample_rate)
        w.writeframes(struct.pack("<%dh" % len(pcm_samples), *pcm_samples))

def play_once(path):
    if sys.platform == "darwin":
        return subprocess.run(["afplay", path]).returncode == 0
    if sys.platform.startswith("win"):
        import winsound; winsound.PlaySound(path, winsound.SND_FILENAME); return True
    for player in (["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        try:
            if subprocess.run(player + [path]).returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False

def main():
    p = argparse.ArgumentParser(description="Silbido simple por IFFT")
    p.add_argument("--freq", type=float, default=2500.0)
    p.add_argument("--duration", type=float, default=1.0)
    p.add_argument("--amplitude", type=float, default=0.5)
    p.add_argument("--out", type=str, default=None)
    p.add_argument("--no-play", action="store_true")
    a = p.parse_args()
    samples = build_whistle(SAMPLE_RATE, a.duration, a.freq, a.amplitude)
    if a.out:
        wav_path = a.out
    else:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="silbido_"); os.close(fd)
    pcm = samples_to_pcm16(samples)
    write_wav(wav_path, SAMPLE_RATE, pcm)
    print(f"Silbido generado: {a.freq:.0f} Hz, {len(samples)/SAMPLE_RATE:.2f} s, {len(samples)} muestras -> {wav_path}")
    if a.no_play:
        return
    if not play_once(wav_path):
        print("Sin reproductor; WAV en: " + wav_path, file=sys.stderr); sys.exit(1)
    if a.out is None:
        try: os.remove(wav_path)
        except OSError: pass

if __name__ == "__main__":
    main()
