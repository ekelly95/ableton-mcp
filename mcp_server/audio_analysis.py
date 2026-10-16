"""Measure an audio file the way a mix engineer reads meters.

Server-side only (numpy; Live is never touched): integrated loudness and
loudness range (ITU-R BS.1770-4 / EBU R128), sample and true peak, RMS, crest
factor, an octave-band spectrum, and stereo correlation/width. Reads WAV
(16/24/32-bit PCM, 32/64-bit float, WAVE_FORMAT_EXTENSIBLE) and AIFF/AIFC
(PCM, 'sowt', 'fl32'), the formats Live records and exports.
"""

import math
import struct
from pathlib import Path
from typing import Any

import numpy as np

_OCTAVE_CENTERS = (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
_MAX_SECONDS = 600


class AudioFileError(ValueError):
    pass


# --- Reading -------------------------------------------------------------------


def _pcm_to_float(raw: bytes, bits: int, big_endian: bool, is_float: bool) -> np.ndarray:
    order = ">" if big_endian else "<"
    if is_float:
        return np.frombuffer(raw, dtype=f"{order}f{bits // 8}").astype(np.float64)
    if bits == 8:  # WAV 8-bit is unsigned, AIFF 8-bit signed
        data = np.frombuffer(raw, dtype=np.int8 if big_endian else np.uint8).astype(np.float64)
        return data / 128.0 if big_endian else (data - 128.0) / 128.0
    if bits in (16, 32):
        return np.frombuffer(raw, dtype=f"{order}i{bits // 8}").astype(np.float64) / 2 ** (bits - 1)
    if bits == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        if big_endian:
            value = (b[:, 0] << 16) | (b[:, 1] << 8) | b[:, 2]
        else:
            value = (b[:, 2] << 16) | (b[:, 1] << 8) | b[:, 0]
        value = np.where(value >= 1 << 23, value - (1 << 24), value)
        return value.astype(np.float64) / 2**23
    raise AudioFileError(f"Unsupported bit depth: {bits}")


def _chunks(data: bytes, start: int, big_endian: bool):
    pos = start
    fmt = ">4sI" if big_endian else "<4sI"
    while pos + 8 <= len(data):
        cid, size = struct.unpack_from(fmt, data, pos)
        yield cid, data[pos + 8 : pos + 8 + size]
        pos += 8 + size + (size & 1)


def _read_wav(data: bytes) -> tuple[np.ndarray, int]:
    fmt = body = None
    for cid, chunk in _chunks(data, 12, big_endian=False):
        if cid == b"fmt ":
            fmt = chunk
        elif cid == b"data":
            body = chunk
    if fmt is None or body is None:
        raise AudioFileError("WAV file has no fmt/data chunk")
    tag, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag == 0xFFFE and len(fmt) >= 26:  # WAVE_FORMAT_EXTENSIBLE: real tag in the GUID
        tag = struct.unpack_from("<H", fmt, 24)[0]
    if tag not in (1, 3):
        raise AudioFileError(f"Unsupported WAV encoding (format tag {tag})")
    usable = len(body) - len(body) % (channels * bits // 8)
    samples = _pcm_to_float(body[:usable], bits, big_endian=False, is_float=tag == 3)
    return samples.reshape(-1, channels), rate


def _ieee_extended(b: bytes) -> float:
    exponent = ((b[0] & 0x7F) << 8) | b[1]
    mantissa = int.from_bytes(b[2:10], "big")
    return mantissa * 2.0 ** (exponent - 16383 - 63)


def _read_aiff(data: bytes) -> tuple[np.ndarray, int]:
    comm = ssnd = None
    for cid, chunk in _chunks(data, 12, big_endian=True):
        if cid == b"COMM":
            comm = chunk
        elif cid == b"SSND":
            ssnd = chunk
    if comm is None or ssnd is None:
        raise AudioFileError("AIFF file has no COMM/SSND chunk")
    channels, _, bits = struct.unpack_from(">hIh", comm)
    rate = int(round(_ieee_extended(comm[8:18])))
    compression = comm[18:22] if data[8:12] == b"AIFC" else b"NONE"
    offset = struct.unpack_from(">I", ssnd)[0]
    body = ssnd[8 + offset :]
    if compression in (b"NONE", b"twos"):
        big_endian, is_float = True, False
    elif compression == b"sowt":
        big_endian, is_float = False, False
    elif compression in (b"fl32", b"FL32"):
        big_endian, is_float, bits = True, True, 32
    else:
        raise AudioFileError(f"Unsupported AIFF-C compression {compression!r}")
    usable = len(body) - len(body) % (channels * bits // 8)
    samples = _pcm_to_float(body[:usable], bits, big_endian, is_float)
    return samples.reshape(-1, channels), rate


def read_audio(path: str) -> tuple[np.ndarray, int]:
    """(frames x channels float64 in -1..1, sample rate)."""
    file = Path(path)
    if not file.is_absolute():
        raise AudioFileError(f"file_path must be absolute, got {path!r}")
    if not file.is_file():
        raise AudioFileError(f"No such file: {path}")
    data = file.read_bytes()
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        samples, rate = _read_wav(data)
    elif data[:4] == b"FORM" and data[8:12] in (b"AIFF", b"AIFC"):
        samples, rate = _read_aiff(data)
    else:
        raise AudioFileError("Not a WAV or AIFF file")
    if samples.shape[0] == 0:
        raise AudioFileError("The file has no audio frames")
    if samples.shape[0] > rate * _MAX_SECONDS:
        raise AudioFileError(f"Longer than {_MAX_SECONDS} s; analyze a shorter excerpt")
    return samples, rate


# --- Loudness (ITU-R BS.1770-4) ---------------------------------------------------


def _biquad(x: np.ndarray, b: tuple, a: tuple) -> np.ndarray:
    """Direct-form II transposed. A per-sample loop, but over plain Python
    floats it stays at a few seconds for minutes of audio."""
    b0, b1, b2 = b
    _, a1, a2 = a
    out = []
    append = out.append
    z1 = z2 = 0.0
    for xi in x.tolist():
        yi = b0 * xi + z1
        z1 = b1 * xi - a1 * yi + z2
        z2 = b2 * xi - a2 * yi
        append(yi)
    return np.array(out)


def _k_weighting(rate: int) -> list[tuple[tuple, tuple]]:
    """The BS.1770 pre-filter (high shelf) and RLB high-pass, recomputed for
    any sample rate from their analog prototypes (the published 48 kHz
    coefficients fall out exactly)."""
    # Stage 1: high shelf, +4 dB above ~1.5 kHz.
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = 10 ** (gain_db / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = (
        ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0),
        (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0),
    )
    # Stage 2: RLB high-pass at ~38 Hz.
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    a0 = 1 + k / q + k * k
    highpass = ((1.0, -2.0, 1.0), (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0))
    return [shelf, highpass]


def _block_loudness(weighted: np.ndarray, rate: int, block_s: float, hop_s: float) -> np.ndarray:
    """Loudness of each gating block, LUFS (channel weights 1.0 for L/R)."""
    block, hop = int(round(block_s * rate)), int(round(hop_s * rate))
    if weighted.shape[0] < block:
        return np.array([])
    power = weighted**2
    csum = np.vstack([np.zeros((1, power.shape[1])), np.cumsum(power, axis=0)])
    starts = np.arange(0, weighted.shape[0] - block + 1, hop)
    mean_square = (csum[starts + block] - csum[starts]) / block  # per channel
    total = mean_square.sum(axis=1)
    with np.errstate(divide="ignore"):
        return -0.691 + 10 * np.log10(total)


def _gated_mean(loudness: np.ndarray, relative_lu: float) -> float | None:
    above_abs = loudness[loudness > -70.0]
    if above_abs.size == 0:
        return None
    gate = -0.691 + 10 * np.log10(np.mean(10 ** ((above_abs + 0.691) / 10))) + relative_lu
    gated = above_abs[above_abs > gate]
    if gated.size == 0:
        return None
    return float(-0.691 + 10 * np.log10(np.mean(10 ** ((gated + 0.691) / 10))))


def _loudness_range(short_term: np.ndarray) -> float | None:
    """EBU Tech 3342: spread (10th-95th percentile) of gated 3 s loudness."""
    above_abs = short_term[short_term > -70.0]
    if above_abs.size < 2:
        return None
    gate = -0.691 + 10 * np.log10(np.mean(10 ** ((above_abs + 0.691) / 10))) - 20.0
    gated = above_abs[above_abs > gate]
    if gated.size < 2:
        return None
    return float(np.percentile(gated, 95) - np.percentile(gated, 10))


# --- Other meters -----------------------------------------------------------------


def _db(value: float) -> float | None:
    return round(20 * math.log10(value), 2) if value > 0 else None


def _true_peak(samples: np.ndarray) -> float:
    """Peak after 4x band-limited oversampling (FFT zero-padding), per BS.1770
    Annex 2's intent: catches inter-sample overs a sample peak misses."""
    n = samples.shape[0]
    if n < 4:
        return float(np.max(np.abs(samples)))
    peak = 0.0
    chunk = 1 << 16
    for start in range(0, n, chunk):
        part = samples[max(0, start - 64) : start + chunk + 64]
        spectrum = np.fft.rfft(part, axis=0)
        up = np.fft.irfft(spectrum, n=part.shape[0] * 4, axis=0) * 4
        peak = max(peak, float(np.max(np.abs(up))))
    return peak


def _octave_bands(mono: np.ndarray, rate: int) -> dict[str, float]:
    """Energy per octave band, dB relative to the loudest band (0)."""
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(mono.size))) ** 2
    freqs = np.fft.rfftfreq(mono.size, 1 / rate)
    energy = {}
    for center in _OCTAVE_CENTERS:
        lo, hi = center / math.sqrt(2), center * math.sqrt(2)
        if lo >= rate / 2:
            break
        mask = (freqs >= lo) & (freqs < min(hi, rate / 2))
        energy[center] = float(spectrum[mask].sum())
    loudest = max(energy.values()) or 1.0
    bands = {}
    for center, value in energy.items():
        label = f"{center / 1000:g}k" if center >= 1000 else f"{center:g}"
        bands[label] = round(10 * math.log10(value / loudest), 1) if value > 0 else None
    return bands


def analyze(samples: np.ndarray, rate: int) -> dict[str, Any]:
    weighted = samples
    for b, a in _k_weighting(rate):
        weighted = np.column_stack(
            [_biquad(weighted[:, ch], b, a) for ch in range(weighted.shape[1])]
        )
    momentary = _block_loudness(weighted, rate, 0.4, 0.1)
    short_term = _block_loudness(weighted, rate, 3.0, 1.0)

    sample_peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples**2)))
    integrated = _gated_mean(momentary, -10.0)
    result: dict[str, Any] = {
        "duration_s": round(samples.shape[0] / rate, 2),
        "sample_rate": rate,
        "channels": samples.shape[1],
        "integrated_lufs": round(integrated, 1) if integrated is not None else None,
        "true_peak_dbtp": _db(_true_peak(samples)),
        "sample_peak_dbfs": _db(sample_peak),
        "rms_dbfs": _db(rms),
        "crest_db": round(20 * math.log10(sample_peak / rms), 1) if rms > 0 else None,
        "spectrum_db": _octave_bands(samples.mean(axis=1), rate),
    }
    if short_term.size:
        st = short_term[np.isfinite(short_term)]
        result["max_short_term_lufs"] = round(float(st.max()), 1) if st.size else None
        lra = _loudness_range(short_term)
        result["loudness_range_lu"] = round(lra, 1) if lra is not None else None
    if momentary.size:
        mom = momentary[np.isfinite(momentary)]
        result["max_momentary_lufs"] = round(float(mom.max()), 1) if mom.size else None
    if samples.shape[1] >= 2:
        left, right = samples[:, 0], samples[:, 1]
        denom = math.sqrt(float(np.sum(left**2) * np.sum(right**2)))
        if denom > 0:
            result["stereo_correlation"] = round(float(np.sum(left * right)) / denom, 2)
        mid, side = (left + right) / 2, (left - right) / 2
        mid_rms, side_rms = float(np.sqrt(np.mean(mid**2))), float(np.sqrt(np.mean(side**2)))
        if mid_rms > 0 and side_rms > 0:
            result["side_to_mid_db"] = round(20 * math.log10(side_rms / mid_rms), 1)
    if sample_peak == 0:
        result["note"] = "The file is digital silence."
    return result


def analyze_file(arguments: dict) -> dict[str, Any]:
    """Handler for the analyze_audio tool."""
    path = arguments.get("file_path") or ""
    samples, rate = read_audio(path)
    start = float(arguments.get("start_seconds") or 0)
    if arguments.get("seconds"):
        samples = samples[int(start * rate) : int((start + float(arguments["seconds"])) * rate)]
    elif start:
        samples = samples[int(start * rate) :]
    if samples.shape[0] == 0:
        raise AudioFileError("No audio in the requested range")
    result = analyze(samples, rate)
    result["file"] = Path(path).name
    return result
