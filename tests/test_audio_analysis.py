"""analyze_audio: loudness against the EBU reference, peaks, spectrum, stereo,
and every file format Live records or exports."""

import math
import struct

import numpy as np
import pytest

from mcp_server.audio_analysis import (
    AudioFileError,
    _k_weighting,
    analyze,
    analyze_file,
    read_audio,
)


def sine(freq, amplitude_db, seconds=10.0, rate=48000, channels=2, phase=0.0):
    t = np.arange(int(seconds * rate)) / rate
    mono = 10 ** (amplitude_db / 20) * np.sin(2 * math.pi * freq * t + phase)
    return np.column_stack([mono] * channels)


def write_wav(path, samples, rate, bits=16, is_float=False):
    frames, channels = samples.shape
    if is_float:
        body = samples.astype("<f4").tobytes()
        tag = 3
    elif bits == 24:
        ints = np.clip(np.round(samples * 2**23), -(2**23), 2**23 - 1).astype("<i4")
        body = b"".join(int(v).to_bytes(3, "little", signed=True) for v in ints.ravel())
        tag = 1
    else:
        body = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2").tobytes()
        tag = 1
    width = bits // 8
    fmt = struct.pack(
        "<HHIIHH", tag, channels, rate, rate * channels * width, channels * width, bits
    )
    chunks = (
        b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(body)) + body
    )
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    return str(path)


def write_aiff(path, samples, rate):
    frames, channels = samples.shape
    body = np.clip(np.round(samples * 32767), -32768, 32767).astype(">i2").tobytes()
    mantissa, exponent = math.frexp(rate)  # 80-bit IEEE extended
    rate80 = struct.pack(">H", exponent + 16382) + int(mantissa * 2**64).to_bytes(8, "big")
    comm = struct.pack(">hIh", channels, frames, 16) + rate80
    ssnd = struct.pack(">II", 0, 0) + body
    chunks = (
        b"COMM"
        + struct.pack(">I", len(comm))
        + comm
        + b"SSND"
        + struct.pack(">I", len(ssnd))
        + ssnd
    )
    path.write_bytes(b"FORM" + struct.pack(">I", 4 + len(chunks)) + b"AIFF" + chunks)
    return str(path)


def test_k_weighting_matches_the_published_48k_coefficients():
    (shelf_b, shelf_a), (hp_b, hp_a) = _k_weighting(48000)
    assert shelf_b == pytest.approx((1.53512485958697, -2.69169618940638, 1.19839281085285))
    assert shelf_a == pytest.approx((1.0, -1.69065929318241, 0.73248077421585))
    assert hp_a == pytest.approx((1.0, -1.99004745483398, 0.99007225036621))


def test_ebu_reference_tone_reads_minus_23_lufs():
    # EBU Tech 3341: 1 kHz sine at -23 dBFS on both channels = -23.0 LUFS.
    result = analyze(sine(1000, -23.0), 48000)
    assert result["integrated_lufs"] == pytest.approx(-23.0, abs=0.1)
    assert result["max_momentary_lufs"] == pytest.approx(-23.0, abs=0.1)
    assert result["loudness_range_lu"] == pytest.approx(0.0, abs=0.1)


def test_loudness_at_44k1_too():
    result = analyze(sine(1000, -23.0, rate=44100), 44100)
    assert result["integrated_lufs"] == pytest.approx(-23.0, abs=0.1)


def test_true_peak_catches_inter_sample_overs():
    # A sine at fs/4 sampled 45 degrees off its crest: every sample sits at
    # 0.707 of the real peak, so sample peak under-reads by ~3 dB.
    result = analyze(sine(12000, -1.0, seconds=1.0, phase=math.pi / 4), 48000)
    assert result["sample_peak_dbfs"] == pytest.approx(-4.0, abs=0.1)
    assert result["true_peak_dbtp"] == pytest.approx(-1.0, abs=0.2)


def test_rms_crest_and_spectrum_of_a_sine():
    result = analyze(sine(1000, -6.0, seconds=2.0), 48000)
    assert result["rms_dbfs"] == pytest.approx(-9.0, abs=0.1)  # sine RMS is peak - 3 dB
    assert result["crest_db"] == pytest.approx(3.0, abs=0.1)
    bands = result["spectrum_db"]
    assert bands["1k"] == 0.0
    assert bands["63"] < -60 and bands["8k"] < -40


def test_stereo_correlation_and_width():
    same = analyze(sine(440, -12.0, seconds=1.0), 48000)
    assert same["stereo_correlation"] == pytest.approx(1.0)
    flipped = sine(440, -12.0, seconds=1.0)
    flipped[:, 1] *= -1
    anti = analyze(flipped, 48000)
    assert anti["stereo_correlation"] == pytest.approx(-1.0)
    assert "side_to_mid_db" not in same  # no side signal at all
    assert "side_to_mid_db" not in anti  # no mid signal at all
    narrow = sine(440, -12.0, seconds=1.0)
    narrow[:, 1] *= 0.5  # mid = 0.75 L, side = 0.25 L -> side 9.5 dB under mid
    assert analyze(narrow, 48000)["side_to_mid_db"] == pytest.approx(-9.5, abs=0.1)


def test_silence_is_reported_not_crashed():
    result = analyze(np.zeros((48000, 2)), 48000)
    assert result["integrated_lufs"] is None
    assert result["sample_peak_dbfs"] is None
    assert result["note"] == "The file is digital silence."


@pytest.mark.parametrize("bits, is_float", [(16, False), (24, False), (32, True)])
def test_wav_formats_round_trip(tmp_path, bits, is_float):
    source = sine(1000, -6.0, seconds=0.5)
    path = write_wav(tmp_path / f"t{bits}.wav", source, 48000, bits=bits, is_float=is_float)
    samples, rate = read_audio(path)
    assert rate == 48000 and samples.shape == source.shape
    assert np.max(np.abs(samples - source)) < 1e-4


def test_aiff_round_trip(tmp_path):
    source = sine(1000, -6.0, seconds=0.5, rate=44100)
    samples, rate = read_audio(write_aiff(tmp_path / "t.aif", source, 44100))
    assert rate == 44100
    assert np.max(np.abs(samples - source)) < 1e-4


def test_analyze_file_with_an_excerpt(tmp_path):
    loud = sine(1000, -10.0, seconds=4.0)
    quiet = sine(1000, -30.0, seconds=4.0)
    path = write_wav(tmp_path / "two_parts.wav", np.vstack([loud, quiet]), 48000)
    whole = analyze_file({"file_path": path})
    tail = analyze_file({"file_path": path, "start_seconds": 4, "seconds": 4})
    assert tail["integrated_lufs"] == pytest.approx(-30.0, abs=0.2)
    assert whole["integrated_lufs"] > tail["integrated_lufs"] + 15
    assert whole["file"] == "two_parts.wav"


def test_bad_inputs(tmp_path):
    with pytest.raises(AudioFileError, match="absolute"):
        read_audio("mix.wav")
    with pytest.raises(AudioFileError, match="No such file"):
        read_audio(str(tmp_path / "missing.wav"))
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not audio at all")
    with pytest.raises(AudioFileError, match="Not a WAV or AIFF"):
        read_audio(str(junk))


@pytest.mark.anyio
async def test_analyze_audio_over_protocol(tmp_path):
    from mcp import Client

    from mcp_server.server import build_server
    from tests.helpers import FakeAbletonClient

    fake = FakeAbletonClient()
    path = write_wav(tmp_path / "ref.wav", sine(1000, -23.0, seconds=5.0), 48000)
    async with Client(build_server(fake)) as session:
        ok = await session.call_tool("analyze_audio", {"file_path": path})
        assert ok.is_error is False
        assert ok.structured_content["integrated_lufs"] == pytest.approx(-23.0, abs=0.1)
        missing = await session.call_tool("analyze_audio", {"file_path": str(tmp_path / "x.wav")})
        assert missing.is_error is True
        assert "No such file" in missing.content[0].text
    assert fake.sent == []  # never touches Live
