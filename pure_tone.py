import numpy as np
from scipy.io.wavfile import write
import simpleaudio as sa

# Parameters
sample_rate = 44100  # Hz
duration = 1.0       # seconds
frequency = 2500    # Hz

# Number of samples
num_samples = int(sample_rate * duration)

# Create a complex spectrum array initialized to zeros
spectrum = np.zeros(num_samples, dtype=np.complex128)

# Set the bin corresponding to the target frequency
bin_index = int(frequency * duration)
spectrum[bin_index] = 1.0

# Apply inverse FFT to obtain the time-domain signal
signal = np.fft.ifft(spectrum)

# Normalize the signal to the range [-1, 1]
signal = np.real(signal) / np.max(np.abs(signal))

# Convert to 16-bit PCM integers
signal_int16 = np.int16(signal * 32767)

# Save as a WAV file
wav_file = "pure_tone.wav"
write(wav_file, sample_rate, signal_int16)

# Play the WAV file once
wave_obj = sa.WaveObject.from_wave_file(wav_file)
play_obj = wave_obj.play()
play_obj.wait_done()
