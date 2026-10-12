import numpy as np
import wave

# Parameters
SAMPLE_RATE = 44100
DURATION = 10.0
TEMPO = 120
BEAT_DURATION = 60 / TEMPO

# Scales
MIXOLYDIAN = [261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25]
HIJAZ = [277.18, 311.13, 349.23, 415.30, 466.16, 523.25, 554.37, 622.25]
ARPEGGIO_PATTERN = [0, 2, 4, 6, 4, 2]

# Generate time array
t = np.linspace(0, DURATION, int(SAMPLE_RATE * DURATION), False)

# Generate arpeggio
output = np.zeros_like(t)
click_track = np.zeros_like(t)

for i, note_index in enumerate(ARPEGGIO_PATTERN * int(DURATION/2)):
    start = int(i * BEAT_DURATION * SAMPLE_RATE)
    end = int((i + 1) * BEAT_DURATION * SAMPLE_RATE)
    segment_length = end - start
    
    # Transition between scales
    if i < len(ARPEGGIO_PATTERN) * 5:
        note_freq = MIXOLYDIAN[note_index]
    else:
        note_freq = HIJAZ[note_index]
    
    # Sawtooth waveform
    wave = np.cumsum(np.ones(segment_length)) % 1
    wave = wave * 2 - 1
    
    # Envelope
    envelope = np.linspace(0, 1, int(0.1 * SAMPLE_RATE))
    wave[:len(envelope)] *= envelope
    wave[-len(envelope):] *= envelope[::-1]
    
    # Sidechain click
    click_t = np.linspace(0, BEAT_DURATION, segment_length)
    click = np.sin(2 * np.pi * 50 * click_t) * np.exp(-click_t * 10)
    click_track[start:end] += click
    
    # Apply sidechain
    wave *= (1 - click[:len(wave)])
    output[start:end] += wave

# Normalize
output /= np.max(np.abs(output))

# Simple EQ: boost lows, reduce highs
b, a = np.array([1, -0.9]), np.array([1, -0.5])
output = np.convolve(output, b)[:len(output)] / a[0]

# Convert to 16-bit PCM
output = np.int16(output * 32767)

# Save to WAV
with wave.open('arpeggio.wav', 'w') as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(SAMPLE_RATE)
    f.writeframes(output.tobytes())
