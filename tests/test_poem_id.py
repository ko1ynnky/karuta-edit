"""読まれた歌の特定の仕様: 読手の読みの書き起こしから、百人一首のどの句かを選ぶ。

書き起こしの例は、2026-09-20 第4試合の元動画を Parakeet (ja) で実際に書き起こしたもの。
"""
import numpy as np
import pytest

from hyakunin_isshu import POEMS
from poem_id import (
    Match,
    Reading,
    confirm_by_next_shimo,
    describe_reading,
    identify_readings,
    match_phrase,
    poem_label,
    short_label,
    to_kana,
)


def test_poems_cover_joka_and_all_hundred():
    assert sorted(no for no, *_ in POEMS) == list(range(101))


def test_pronunciations_are_modern_hiragana():
    for _, _, _, kami, shimo in POEMS:
        for text in (kami, shimo):
            assert all("ぁ" <= ch <= "ゖ" or ch == " " for ch in text), text
            assert not set(text) & set("ゐゑ"), text


def test_kanji_in_transcript_is_read_as_kana():
    assert to_kana("竜田川") == to_kana("たつたがわ")


def test_particle_spelling_does_not_matter():
    assert to_kana("花の色は") == to_kana("はなのいろわ")
    assert to_kana("世を") == to_kana("よお")


def test_katakana_digits_and_punctuation_are_ignored_or_folded():
    assert to_kana("キリギリス、泣く?") == to_kana("きりぎりすなく")
    assert to_kana("4らに") == to_kana("らに")


@pytest.mark.parametrize("transcript, poem, part", [
    ("足引きの山鳥の尾シナリオの", 3, "kami"),
    ("由良野渡る船人風事をたえ", 46, "kami"),
    ("キリギリス泣くやしの世の寒しろにはい", 91, "kami"),
    ("行方も知らぬ恋の道かな", 46, "shimo"),
    ("衣町天か山", 2, "shimo"),
    ("乱れてけさはものこそ思え", 80, "shimo"),
    ("なわずに咲くやこのの花冬ごもり", 0, "kami"),
    ("を春べと咲くやこの花", 0, "shimo"),
])
def test_transcript_is_matched_to_the_read_phrase(transcript, poem, part):
    match = match_phrase(transcript)
    assert (match.poem, match.part) == (poem, part)


def test_missing_start_and_trailing_speech_are_tolerated():
    match = match_phrase("4らに風の吹きしく秋きのはすいません")
    assert (match.poem, match.part) == (37, "kami")


@pytest.mark.parametrize("transcript", ["よろしくお願いします", "うん", "ごいた", ""])
def test_speech_other_than_reading_is_not_matched(transcript):
    assert match_phrase(transcript) is None


def test_clear_reading_has_margin_over_runner_up():
    match = match_phrase("足引きの山鳥の尾シナリオの")
    assert 0 <= match.cost < match.cost + match.margin <= 1
    assert match.margin > 0.3


def test_label_shows_number_and_first_phrase_in_traditional_kana():
    assert poem_label(17) == "17 ちはやぶる"
    assert poem_label(0) == "序歌 なにはづに"


def _reading(onset, kami=None, shimo=None):
    return Reading(
        onset_sec=onset,
        after=Match(kami, "kami", 0.1, 0.5) if kami is not None else None,
        before=Match(shimo, "shimo", 0.1, 0.5) if shimo is not None else None,
    )


def test_kami_is_confirmed_when_next_reading_starts_after_its_shimo():
    readings = confirm_by_next_shimo([
        _reading(100, kami=3),
        _reading(120, kami=37, shimo=3),
    ])
    assert readings[0].confirmed is True
    assert readings[1].confirmed is None  # 次の読みがないので確かめられない


def test_kami_is_not_confirmed_when_next_shimo_is_another_poem():
    readings = confirm_by_next_shimo([
        _reading(100, kami=3),
        _reading(120, kami=37, shimo=80),
    ])
    assert readings[0].confirmed is False
    assert readings[0].next_shimo == 80


def test_confirmation_skips_candidates_without_shimo_before_them():
    # 途中の候補 (雑音など) の前に下の句が聞き取れなくても、その次の読みで確かめる
    readings = confirm_by_next_shimo([
        _reading(100, kami=93),
        _reading(120),
        _reading(154, kami=68, shimo=93),
    ])
    assert readings[0].confirmed is True


def test_confirmation_is_unknown_when_next_reading_has_no_shimo_before_it():
    # 次の読み (上の句が特定できた候補) の前の下の句が聞き取れなければ、その先の読みでは確かめない
    readings = confirm_by_next_shimo([
        _reading(100, kami=81),
        _reading(130, kami=51),
        _reading(150, kami=72, shimo=51),
    ])
    assert readings[0].confirmed is None
    assert readings[1].confirmed is True


def test_description_of_kami_confirmed_by_next_shimo():
    [reading, _] = confirm_by_next_shimo([_reading(100, kami=17), _reading(120, kami=3, shimo=17)])
    assert describe_reading(reading) == "17 ちはやぶる（上の句、次の下の句でも一致）"


def test_description_of_kami_that_could_not_be_checked():
    assert describe_reading(_reading(100, kami=17)) == "17 ちはやぶる（上の句）"


def test_description_suggests_missed_reading_when_next_shimo_differs():
    # 2026-09-20 第4試合: 33 の次の 64 が候補にならず、次の候補の前で 64 の下の句が読まれていた
    [reading, _] = confirm_by_next_shimo([_reading(100, kami=33), _reading(170, kami=20, shimo=64)])
    text = describe_reading(reading)
    assert text.startswith("33 ひさかたの（上の句）")
    assert "64 あさぼらけ" in text
    assert "候補" in text


def test_description_of_shimo_warns_it_may_not_be_a_take():
    text = describe_reading(Reading(100, after=Match(20, "shimo", 0.2, 0.3), before=None))
    assert "20 わびぬれば" in text
    assert "下の句" in text
    assert "取り" in text


def test_description_when_nothing_is_identified():
    assert describe_reading(Reading(100, after=None, before=None)) == "特定できませんでした"


def test_short_label_for_scene_list():
    assert short_label(_reading(100, kami=17)) == "ちはやぶる"
    assert short_label(Reading(100, after=Match(20, "shimo", 0.2, 0.3), before=None)) == "(下の句)"
    assert short_label(Reading(100, after=None, before=None)) == ""


class _FakeRecognizer:
    """窓の開始時刻に置かれた書き起こしを返す。"""

    def __init__(self, texts_by_start):
        self.texts_by_start = texts_by_start
        self.windows = []

    def transcribe(self, samples, sample_rate):
        start = samples[0] / sample_rate  # 波形の値に時刻を埋め込んでいる
        self.windows.append((round(start, 1), round(start + len(samples) / sample_rate, 1)))
        return self.texts_by_start.get(round(start, 1), "")


def test_readings_use_speech_after_and_before_each_candidate(tmp_path):
    import soundfile as sf
    sr = 100
    audio = np.arange(60 * sr, dtype=np.float64)  # 各サンプルの値 = サンプル番号
    wav = tmp_path / "a.wav"
    sf.write(wav, audio / audio.max(), sr, subtype="DOUBLE")
    scale = audio.max()

    class Rec(_FakeRecognizer):
        def transcribe(self, samples, sample_rate):
            return super().transcribe(samples * scale, sample_rate)

    rec = Rec({29.7: "足引きの山鳥の尾シナリオの", 20.0: "行方も知らぬ恋の道かな"})
    readings = identify_readings(str(wav), [30.0], rec)
    assert rec.windows == [(20.0, 29.7), (29.7, 38.0)]
    assert (readings[0].after.poem, readings[0].after.part) == (3, "kami")
    assert (readings[0].before.poem, readings[0].before.part) == (46, "shimo")
