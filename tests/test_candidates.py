"""候補統合の仕様: 読手の声の立ち上がり (上の句開始) を候補とし、振幅スコアは位置と表示スコアに使う。"""
import numpy as np

from utils import return_candidates, return_top_scores


def _waveform_with_readings(n_cards=12, shimo=7.5, gap=1.5, kami=6.0, wait=5.0):
    """下の句→間→上の句→待ち を繰り返す0.1秒ピーク波形と、上の句開始フレーム一覧。"""
    frames, onsets = [np.full(30, 0.01)], []
    for _ in range(n_cards):
        frames.append(np.full(int(shimo * 10), 0.5))
        frames.append(np.full(int(gap * 10), 0.01))
        onsets.append(sum(len(f) for f in frames))
        frames.append(np.full(int(kami * 10), 0.5))
        frames.append(np.full(int(wait * 10), 0.01))
    frames.append(np.full(60, 0.01))
    return np.concatenate(frames), onsets


def _voiced_from_waveform(waveform):
    return waveform > 0.1


def _near(candidates, frame, tol=15):
    return [i for i in candidates if abs(i - frame) <= tol]


def test_without_voice_info_candidates_equal_legacy_top_scores():
    waveform, _ = _waveform_with_readings()
    _, legacy = return_top_scores(waveform)
    assert return_candidates(waveform, None) == legacy


def test_every_reading_start_becomes_exactly_one_candidate():
    waveform, onsets = _waveform_with_readings()
    candidates = return_candidates(waveform, _voiced_from_waveform(waveform))
    assert len(candidates) == len(onsets)
    for onset in onsets:
        assert len(_near(candidates, onset)) == 1


def test_legacy_candidate_without_reader_voice_is_removed():
    waveform, onsets = _waveform_with_readings()
    voiced = _voiced_from_waveform(waveform)
    voiced[onsets[3] - 20:onsets[3] + 80] = False  # 4枚目だけ声なし (札の音だけの区間を模す)
    candidates = return_candidates(waveform, voiced)
    assert not _near(candidates, onsets[3])
    for onset in onsets[:3] + onsets[4:]:
        assert _near(candidates, onset)


def test_legacy_candidate_at_shimo_no_ku_start_is_removed():
    # 上の句→待ち→下の句 の下の句開始に振幅候補が付いても、取りシーンではないので採用しない
    waveform, onsets = _waveform_with_readings(wait=2.0)
    voiced = _voiced_from_waveform(waveform)
    _, legacy = return_top_scores(waveform)
    shimo_start = onsets[2] + 60 + 20
    assert _near(legacy, shimo_start)
    assert not _near(return_candidates(waveform, voiced), shimo_start)


def test_reading_start_missed_by_legacy_scoring_is_added_with_positive_score():
    waveform, onsets = _waveform_with_readings()
    voiced = _voiced_from_waveform(waveform)
    waveform = waveform.copy()
    waveform[onsets[4] - 15:onsets[4] + 60] = 0.03  # 5枚目の上の句を波形上は目立たなくする
    _, legacy = return_top_scores(waveform)
    assert not _near(legacy, onsets[4])
    candidates = return_candidates(waveform, voiced)
    added = _near(candidates, onsets[4])
    assert added and all(candidates[i] > 0 for i in added)


def test_reading_start_near_legacy_candidate_keeps_legacy_position_and_score():
    waveform, onsets = _waveform_with_readings()
    _, legacy = return_top_scores(waveform)
    candidates = return_candidates(waveform, _voiced_from_waveform(waveform))
    for onset in onsets:
        (idx,) = _near(candidates, onset)
        assert idx in legacy and candidates[idx] == legacy[idx]


def test_falls_back_to_legacy_when_no_reading_start_is_detected():
    waveform, _ = _waveform_with_readings()
    _, legacy = return_top_scores(waveform)
    assert return_candidates(waveform, np.zeros(len(waveform), dtype=bool)) == legacy
