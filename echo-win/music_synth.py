# music_synth.py — pure-stdlib offline melody synthesizer (no numpy, no deps).
# Local writes a compact melody; this turns it into a real 16-bit WAV.
# Drafted by the local qwen-coder, then fixed by Claude (the original had
# float samples that crashed struct.pack, crashing rests, and mistuned
# sharps/flats — all corrected here).
import wave
import math
import struct
import re

# Semitone offset from C within an octave, per letter.
_LETTER = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def note_to_frequency(note_name):
    """Note like C4, D#4, Eb4, A5 -> frequency in Hz (12-TET, A4 = 440)."""
    m = re.fullmatch(r'([A-Ga-g])([#b]?)(-?\d+)', note_name.strip())
    if not m:
        raise ValueError(f"bad note '{note_name}' (expected like C4, D#4, Eb4)")
    letter, accidental, octave = m.group(1).upper(), m.group(2), int(m.group(3))
    semitone = _LETTER[letter] + (1 if accidental == '#' else -1 if accidental == 'b' else 0)
    midi = (octave + 1) * 12 + semitone   # MIDI number; C4 = 60, A4 = 69
    return 440.0 * 2 ** ((midi - 69) / 12.0)


def _tone(frequency, duration, sample_rate):
    """One note: sine + two quieter harmonics, with a short attack/release
    envelope so notes don't click. Returns a list of int16 samples."""
    n = int(duration * sample_rate)
    attack = min(int(0.005 * sample_rate), n // 2)   # ~5 ms, but never > half
    release = min(int(0.005 * sample_rate), n // 2)
    out = []
    for i in range(n):
        t = i / sample_rate
        if attack and i < attack:
            env = i / attack
        elif release and i >= n - release:
            env = (n - i) / release
        else:
            env = 1.0
        s = (math.sin(2 * math.pi * frequency * t)
             + 0.3 * math.sin(2 * math.pi * 2 * frequency * t)
             + 0.15 * math.sin(2 * math.pi * 3 * frequency * t))
        val = max(-1.0, min(1.0, env * s * 0.6))     # master gain + clamp
        out.append(int(val * 32767))                 # int16 (must be int!)
    return out


def _silence(duration, sample_rate):
    return [0] * int(duration * sample_rate)


def synth_melody(notes, out_path, tempo=120, sample_rate=44100):
    """notes: space-separated 'NOTE:BEATS' tokens, e.g. 'C4:1 E4:1 G4:2 R:0.5 C5:2'
    (R = rest). One beat = 60/tempo seconds. Writes a mono 16-bit WAV."""
    beat_seconds = 60.0 / tempo
    all_samples = []
    for token in notes.split():
        name, _, beats = token.partition(':')
        duration = (float(beats) if beats else 1.0) * beat_seconds
        if name.upper() == 'R':
            all_samples.extend(_silence(duration, sample_rate))
        else:
            all_samples.extend(_tone(note_to_frequency(name), duration, sample_rate))

    with wave.open(out_path, 'w') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(struct.pack('<%dh' % len(all_samples), *all_samples))
    return out_path


if __name__ == '__main__':
    # A C-major scale, then a little arpeggio — sanity check.
    synth_melody('C4:0.5 D4:0.5 E4:0.5 F4:0.5 G4:0.5 A4:0.5 B4:0.5 C5:1 '
                 'R:0.5 C4:0.5 E4:0.5 G4:0.5 C5:1', 'scale_test.wav')
    print('wrote scale_test.wav')
