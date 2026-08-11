"""Deterministic test-tone WAV generation shared by the checkpoint scripts."""

import math
import struct
import wave
from pathlib import Path


def write_sine_wav(
    path: Path,
    freq_hz: float,
    amplitude: float,
    seconds: float,
    antiphase: bool = False,
) -> None:
    """16-bit 44.1 kHz WAV: mono, or stereo with R = -L when antiphase (the
    v1 tap's mono-sum killer — a mono sum of R = -L reads as near-silence)."""
    rate = 44100
    channels = 2 if antiphase else 1
    frames = bytearray()
    for i in range(int(rate * seconds)):
        sample = int(amplitude * 32767 * math.sin(2 * math.pi * freq_hz * i / rate))
        frames += struct.pack("<h", sample)
        if antiphase:
            frames += struct.pack("<h", -sample)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
