"""読手の声の検出仕様。

読手の声は「持続する倍音構造 (母音)」「ノイズフロアより十分大きい」「音程が安定」の
3点で識別する。空調の低周波音、札の打撃音、遠くの小さな周期音は読手の声ではない。
"""
import numpy as np
import soundfile as sf
import pytest

from reader_voice import (
    compute_voiced_frames,
    find_reading_onsets,
)

SR = 16000


def _harmonic_tone(duration, f0=300.0, amp=0.1, n_harmonics=6):
    t = np.arange(int(duration * SR)) / SR
    tone = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, n_harmonics + 1))
    return amp * tone / np.abs(tone).max()


def _silence(duration):
    return np.zeros(int(duration * SR))


def _write(tmp_path, signal, name="a.wav"):
    rng = np.random.default_rng(0)
    floor = 0.001 * rng.standard_normal(len(signal))  # -60dBFS 程度のノイズフロア
    path = tmp_path / name
    sf.write(path, np.clip(signal + floor, -1, 1), SR)
    return str(path)


def _voiced_ratio(voiced, start_sec, end_sec):
    return voiced[int(start_sec * 10):int(end_sec * 10)].mean()


def test_sustained_harmonic_tone_is_reader_voice(tmp_path):
    signal = np.concatenate([_silence(2), _harmonic_tone(3), _silence(2)])
    voiced = compute_voiced_frames(_write(tmp_path, signal))
    assert _voiced_ratio(voiced, 2.2, 4.8) >= 0.9
    assert _voiced_ratio(voiced, 0, 1.8) <= 0.05
    assert _voiced_ratio(voiced, 5.2, 7) <= 0.05


def test_low_frequency_rumble_is_not_reader_voice(tmp_path):
    t = np.arange(7 * SR) / SR
    rumble = 0.2 * (np.sin(2 * np.pi * 60 * t) + 0.5 * np.sin(2 * np.pi * 120 * t))
    signal = rumble + np.concatenate([_silence(2), _harmonic_tone(3), _silence(2)])
    voiced = compute_voiced_frames(_write(tmp_path, signal))
    assert _voiced_ratio(voiced, 0, 1.8) <= 0.05
    assert _voiced_ratio(voiced, 2.2, 4.8) >= 0.9


def test_broadband_clicks_are_not_reader_voice(tmp_path):
    rng = np.random.default_rng(1)
    signal = np.concatenate([_silence(1), _harmonic_tone(3), _silence(4)])
    for sec in (5.0, 5.7, 6.3, 7.1):  # 札を払う音を模した短い広帯域バースト
        s = int(sec * SR)
        signal[s:s + 400] += 0.5 * rng.standard_normal(400)
    voiced = compute_voiced_frames(_write(tmp_path, signal))
    assert _voiced_ratio(voiced, 4.5, 8) <= 0.05


def test_faint_tone_near_noise_floor_is_not_reader_voice(tmp_path):
    signal = np.concatenate([_silence(1), _harmonic_tone(3), _silence(1), _harmonic_tone(3, amp=0.003), _silence(1)])
    voiced = compute_voiced_frames(_write(tmp_path, signal))
    assert _voiced_ratio(voiced, 1.2, 3.8) >= 0.9
    assert _voiced_ratio(voiced, 5.2, 7.8) <= 0.05


def test_voiced_frames_align_with_0_1s_waveform_frames(tmp_path):
    signal = _silence(3.27)
    voiced = compute_voiced_frames(_write(tmp_path, signal))
    assert len(voiced) == len(signal) // (SR // 10)
    assert voiced.dtype == bool


# --- 読み開始 (上の句) の検出 ------------------------------------------------

def _pattern(*spans):
    """(voiced, 秒) の並びから0.1秒フレームのbool配列を作る。"""
    return np.concatenate([np.full(int(sec * 10), bool(v)) for v, sec in spans])


SHIMO, GAP, KAMI, WAIT = (True, 7.5), (False, 1.5), (True, 6.0), (False, 5.0)


def test_onset_after_shimo_no_ku_and_one_second_pause_is_reading_start():
    voiced = _pattern(SHIMO, GAP, KAMI, WAIT, SHIMO, GAP, KAMI)
    assert find_reading_onsets(voiced) == [90, 290]


def test_onset_after_long_wait_is_not_reading_start():
    # 上の句 → 長い待ち → 下の句 の下の句開始は取りシーンではない
    voiced = _pattern(SHIMO, GAP, KAMI, WAIT, SHIMO)
    assert find_reading_onsets(voiced) == [90]


def test_onset_after_short_previous_segment_is_not_reading_start():
    # 直前の読みが下の句と呼べる長さ (約7秒以上) でなければ上の句開始とみなさない
    voiced = _pattern((True, 5.0), GAP, KAMI)
    assert find_reading_onsets(voiced) == []


def test_short_hole_inside_reading_does_not_create_onset():
    voiced = _pattern((True, 4.0), (False, 0.5), (True, 3.0), GAP, KAMI)
    assert find_reading_onsets(voiced) == [90]


def test_too_short_gap_is_breath_not_reading_start():
    voiced = _pattern(SHIMO, (False, 0.8), KAMI)
    assert find_reading_onsets(voiced) == []


def test_reading_start_interrupted_by_card_sounds_is_still_detected():
    # 上の句の直後に取りの札音が重なると声の検出が途切れるが、読み開始は失われない
    voiced = _pattern(SHIMO, GAP, (True, 0.4), (False, 1.3), (True, 3.0))
    assert find_reading_onsets(voiced) == [90]


def test_isolated_voice_blip_after_shimo_no_ku_is_not_reading_start():
    voiced = _pattern(SHIMO, GAP, (True, 0.2), (False, 6.0))
    assert find_reading_onsets(voiced) == []
