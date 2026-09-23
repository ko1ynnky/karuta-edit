"""読まれた歌の特定の仕様: 読手の読みの書き起こしから、百人一首のどの句かを選ぶ。

書き起こしの例は、2026-09-20 第4試合の元動画を Parakeet (ja) で実際に書き起こしたもの。
"""
import numpy as np
import pytest

from hyakunin_isshu import POEMS
from poem_id import (
    Match,
    Reading,
    describe_reading,
    identify_readings,
    match_phrase,
    poem_label,
    resolve_readings,
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


def _reading(onset, kami=None, shimo=None, after_shimo=None, kami_cost=0.1, shimo_cost=0.1):
    if kami is not None:
        after = Match(kami, "kami", kami_cost, 0.5)
    elif after_shimo is not None:
        after = Match(after_shimo, "shimo", 0.3, 0.5)
    else:
        after = None
    before = Match(shimo, "shimo", shimo_cost, 0.5) if shimo is not None else None
    return Reading(onset_sec=onset, after=after, before=before)


def test_kami_is_confirmed_when_next_reading_starts_after_its_shimo():
    readings = resolve_readings([
        _reading(100, kami=3),
        _reading(120, kami=37, shimo=3),
    ])
    assert (readings[0].poem, readings[0].source, readings[0].confirmed) == (3, "kami", True)
    assert readings[1].confirmed is None  # 次の読みがないので確かめられない


def test_kami_is_kept_when_next_shimo_differs_but_fits_worse():
    # 2026-09-20 第4試合: 33 の次の 64 が候補にならず、次の候補の前で 64 の下の句が読まれていた
    readings = resolve_readings([
        _reading(486, kami=33, kami_cost=0.176),
        _reading(558, kami=20, shimo=64, shimo_cost=0.214),
    ])
    assert (readings[0].poem, readings[0].source) == (33, "kami")
    assert readings[0].confirmed is False
    assert readings[0].next_shimo == 64


def test_clear_kami_is_kept_even_if_next_shimo_fits_slightly_better():
    # 2026-09-20 第5試合 #56: 上の句ははっきり 90。次の下の句 95 との食い違いは間の読みの見逃しと考えられる
    readings = resolve_readings([
        _reading(1700, kami=90, kami_cost=0.294),
        _reading(1744, kami=75, shimo=95, shimo_cost=0.286),
    ])
    assert (readings[0].poem, readings[0].source, readings[0].confirmed) == (90, "kami", False)


def test_next_shimo_wins_when_it_fits_better_than_kami():
    # IMG_0091 #54: 上の句「心にとまければ」は 43 に近かったが、次の下の句は 68 で、こちらが正しかった
    readings = resolve_readings([
        _reading(2049, kami=43, shimo=44, kami_cost=0.647),
        _reading(2076, shimo=68, shimo_cost=0.5),
    ])
    assert (readings[0].poem, readings[0].source) == (68, "shimo")


def test_poem_is_inferred_from_next_shimo_when_kami_is_not_heard():
    # IMG_0091 #4: 上の句は取りの音に埋もれて「大きな」だけ。次の読みの前の下の句で 95 と分かる
    readings = resolve_readings([
        _reading(151, shimo=38),
        _reading(188, shimo=95),
    ])
    assert (readings[0].poem, readings[0].source) == (95, "shimo")


def test_candidate_right_after_shimo_is_a_kami_start_even_if_it_sounds_like_shimo():
    # IMG_0091 #18: 読みは「下の句 → 間 → 上の句」の順なので、下の句の直後が下の句であることはない
    readings = resolve_readings([
        _reading(679, shimo=9, after_shimo=34),
        _reading(699, kami=53, shimo=99),
    ])
    assert (readings[0].poem, readings[0].source) == (99, "shimo")


def test_candidate_inside_a_repeated_shimo_stays_a_shimo_start():
    # 第4試合 2465.9 秒: 前後とも同じ歌の下の句。取りの場面ではない
    readings = resolve_readings([
        _reading(2440, kami=56),
        _reading(2465, shimo=56, after_shimo=56),
        _reading(2490, kami=76, shimo=56),
    ])
    assert readings[1].poem is None
    assert readings[0].confirmed is True


def test_shimo_start_candidate_confirms_the_previous_kami():
    readings = resolve_readings([
        _reading(560, kami=20),
        _reading(574, after_shimo=20),
        _reading(583, kami=63),
    ])
    assert readings[0].confirmed is True
    assert readings[1].poem is None


def test_confirmation_skips_candidates_without_shimo_before_them():
    # 途中の候補 (雑音など) の前に下の句が聞き取れなくても、その次の読みで確かめる
    readings = resolve_readings([
        _reading(100, kami=93),
        _reading(120),
        _reading(154, kami=68, shimo=93),
    ])
    assert readings[0].confirmed is True
    assert readings[1].poem is None


def test_confirmation_is_unknown_when_next_reading_has_no_shimo_before_it():
    # 次の読み (上の句が特定できた候補) の前の下の句が聞き取れなければ、その先の読みでは確かめない
    readings = resolve_readings([
        _reading(100, kami=81),
        _reading(130, kami=51),
        _reading(150, kami=72, shimo=51),
    ])
    assert readings[0].confirmed is None
    assert readings[1].confirmed is True


def test_description_of_kami_confirmed_by_next_shimo():
    [reading, _] = resolve_readings([_reading(100, kami=17), _reading(120, kami=3, shimo=17)])
    assert describe_reading(reading) == "17 ちはやぶる（上の句、次の下の句でも一致）"


def test_description_of_kami_that_could_not_be_checked():
    [reading] = resolve_readings([_reading(100, kami=17)])
    assert describe_reading(reading) == "17 ちはやぶる（上の句）"


def test_description_suggests_missed_reading_when_next_shimo_differs():
    [reading, _] = resolve_readings([
        _reading(486, kami=33, kami_cost=0.176),
        _reading(558, kami=20, shimo=64, shimo_cost=0.214),
    ])
    text = describe_reading(reading)
    assert text.startswith("33 ひさかたの（上の句）")
    assert "64 あさぼらけ" in text
    assert "候補" in text


def test_description_of_poem_inferred_from_next_shimo():
    [reading, _] = resolve_readings([_reading(151, shimo=38), _reading(188, shimo=95)])
    assert describe_reading(reading) == "95 おほけなく（次の読みの前の下の句から推定）"


def test_description_of_shimo_warns_it_may_not_be_a_take():
    [reading] = resolve_readings([_reading(574, after_shimo=20)])
    text = describe_reading(reading)
    assert "20 わびぬれば" in text
    assert "下の句" in text
    assert "取り" in text


def test_description_of_reading_whose_poem_is_unknown():
    # 直前に下の句があるので読みの場面だが、次の下の句もない (試合の最後など)
    [reading] = resolve_readings([_reading(4118, shimo=40)])
    text = describe_reading(reading)
    assert text.startswith("特定できませんでした")
    assert "読み" in text


def test_description_when_nothing_is_identified():
    [reading] = resolve_readings([_reading(100)])
    assert describe_reading(reading) == "特定できませんでした"


def test_short_label_for_scene_list():
    kami, shimo_start, inferred, _, nothing = resolve_readings([
        _reading(100, kami=17),
        _reading(110, after_shimo=17),
        _reading(150, shimo=17),
        _reading(170, shimo=95),
        _reading(200),
    ])
    assert short_label(kami) == "ちはやぶる"
    assert short_label(shimo_start) == "(下の句)"
    assert short_label(inferred) == "おほけなく"
    assert short_label(nothing) == ""


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
