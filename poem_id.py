"""読手の読みを書き起こし、読まれた歌 (百人一首の上の句・下の句) を特定する。

音声認識は各人の PC で動かす (NVIDIA Parakeet TDT-CTC 0.6B ja を sherpa-onnx で実行)。
書き起こしは古語を現代語の漢字に取り違えることが多いので、文字列をそのまま比べず、
かなに直してから 百人一首の 202 句 (序歌を含む) の発音と照合する。候補を句に限るので、
自由に文を生成させる方式 (Whisper など) と違い、無関係な文が「特定結果」になることはない。
"""
import os
import sys
import tarfile
import tempfile
import unicodedata
import urllib.request
from dataclasses import dataclass, replace

import numpy as np
import soundfile as sf

from hyakunin_isshu import POEMS

MODEL_NAME = "sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_NAME}.tar.bz2"
MODEL_DOWNLOAD_MB = 630

# 候補 (上の句の読み始め) からの秒数。読手テキストの標準は「下の句 5秒程度 → 余韻 3秒 →
# 間合い 1秒 → 上の句 6秒程度」。上の句は伸ばして読むと6秒を超えるので8秒まで取る。
# 候補の位置は0.1秒単位の音量から決めていて前後にずれるため、境目を0.3秒ずらしている。
AFTER_WINDOW = (-0.3, 8.0)
BEFORE_WINDOW = (-10.0, -0.3)

# 句の発音のうち、書き起こしと食い違ってよい割合の上限。これを超えたら「特定できない」とする。
# 2026-09-20 第4試合 (試合記録の98首と照合) では、正しく特定できた読みは最大 0.667、
# 読み以外の発話 (挨拶・相づち) は 0.71 以上だった。1試合だけで決めた値なので、試合を増やして見直す。
MAX_COST = 0.7

# 直後の上の句と次の読みの前の下の句が食い違ったとき、上の句の cost がこれ以下なら上の句を採る。
# 4試合の食い違い6件では、正しい上の句は 0.294 以下、誤った上の句 (雑談などを句に当てはめたもの) は
# 0.647 だった。上の句が正しいのは、間の読みが候補になっていないとき。
CLEAR_KAMI_COST = 0.5

_FOLD = str.maketrans({"は": "わ", "へ": "え", "を": "お", "ぢ": "じ", "づ": "ず", "ゐ": "い", "ゑ": "え"})


@dataclass(frozen=True)
class Match:
    poem: int      # 0 は序歌
    part: str      # "kami" (上の句) / "shimo" (下の句)
    cost: float    # 0 = 句の発音がそのまま書き起こしに含まれる、1 = まったく含まれない
    margin: float  # 次に近い別の歌との cost の差。小さいほど取り違えやすい


@dataclass(frozen=True)
class Reading:
    onset_sec: float
    after: Match | None      # 候補の直後に読まれた句 (取りの場面なら上の句)
    before: Match | None     # 候補の直前に読まれた句 (前の歌の下の句のはず)
    after_text: str = ""
    before_text: str = ""
    # 以下は resolve_readings が前後の候補と合わせて決める
    poem: int | None = None        # この候補で上の句が読まれた歌
    source: str | None = None      # poem の根拠。"kami" (直後の上の句) / "shimo" (次の読みの前の下の句)
    confirmed: bool | None = None  # 直後の上の句と次の読みの前の下の句が同じ歌か。確かめようがなければ None
    next_shimo: int | None = None  # 次の読みの前に読まれた下の句の歌


_kakasi = None


def _fold(kana: str) -> str:
    """表記だけが違う文字と、濁点・半濁点の有無を同じ文字にそろえる。

    書き起こしの助詞「は」を「わ」と読むか、漢字の読み (竜田川 → たつたかわ) が連濁するかは
    判断できないので、句の側も同じようにそろえて比べる。
    """
    kana = unicodedata.normalize("NFD", kana.translate(_FOLD))
    return unicodedata.normalize("NFC", kana.replace("\u3099", "").replace("\u309a", "")).translate(_FOLD)


def to_kana(text: str) -> str:
    """書き起こしを、照合用のひらがなに直す。"""
    global _kakasi
    if _kakasi is None:
        import pykakasi
        _kakasi = pykakasi.kakasi()
    kana = "".join(item["hira"] for item in _kakasi.convert(text))
    return _fold("".join(ch for ch in kana if "ぁ" <= ch <= "ゖ"))


def _substring_distance(pattern: str, text: str) -> int:
    """pattern を text のどこかに当てはめたときの最小編集距離 (text の前後の余りは数えない)。"""
    prev = [0] * (len(text) + 1)
    for i, pc in enumerate(pattern, 1):
        cur = [i] + [0] * len(text)
        for j, tc in enumerate(text, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (pc != tc))
        prev = cur
    return min(prev)


_PHRASES = [
    (no, part, _fold("".join(pron.split())))
    for no, _, _, kami, shimo in POEMS
    for part, pron in (("kami", kami), ("shimo", shimo))
]


def match_phrase(transcript: str) -> Match | None:
    """書き起こしに最もよく含まれている句を返す。どの句とも言えなければ None。"""
    kana = to_kana(transcript)
    if not kana:
        return None
    costs = sorted(
        (_substring_distance(phrase, kana) / len(phrase), no, part)
        for no, part, phrase in _PHRASES
    )
    best_cost, best_no, best_part = costs[0]
    if best_cost > MAX_COST:
        return None
    # 同じ歌の上の句と下の句は取り違えても歌は合っているので、次点は別の歌から選ぶ
    runner_up = next(cost for cost, no, _ in costs[1:] if no != best_no)
    return Match(best_no, best_part, round(best_cost, 3), round(runner_up - best_cost, 3))


def poem_label(poem: int) -> str:
    """表示用の名前。例: "17 ちはやぶる"、"序歌 なにはづに"。"""
    no, kami, *_ = POEMS[poem]
    first = kami.split()[0]
    return f"序歌 {first}" if no == 0 else f"{no} {first}"


def _starts_kami(reading: Reading) -> bool:
    """この候補で上の句が読み始められたか。

    直後が上の句と聞こえたときのほか、直前に下の句が読まれていれば上の句の読み始めとみなす。
    読みは「前の歌の下の句 → 間 → 次の歌の上の句」の順なので、下の句の直後は上の句になる。
    直後の上の句は取りの札音に重なって聞き取れないことが多いので、直前の下の句を手がかりにする。
    ただし前後とも同じ歌の下の句なら、下の句の途中 (読み直しなど) に付いた候補とみなす。
    """
    after, before = reading.after, reading.before
    if after is not None and after.part == "kami":
        return True
    if before is None or before.part != "shimo":
        return False
    return not (after is not None and after.part == "shimo" and after.poem == before.poem)


def _next_shimo(readings: list[Reading], i: int) -> Match | None:
    """i 番目の読みのあとに読まれた下の句 (次の読みの前、または下の句の読み始めに付いた候補)。"""
    for later in readings[i + 1:]:
        if later.before is not None and later.before.part == "shimo":
            return later.before
        if _starts_kami(later):
            return None  # 次の読みの前の下の句が聞き取れなかった。その先は別の歌なので見ない
        if later.after is not None and later.after.part == "shimo":
            return later.after
    return None


def resolve_readings(readings: list[Reading]) -> list[Reading]:
    """前後の候補と合わせて、各候補で読まれた歌を決める。

    競技かるたでは、上の句のあと (取りと札の整理を待って) 同じ歌の下の句を読み、続けて次の歌の
    上の句を読む。そこで、直後の上の句の特定を、次の読みの前の下の句で確かめる。下の句は札が
    落ち着いてから読むので、取りの音に重なる上の句より聞き取りやすい。
    両者が食い違ったら、上の句がはっきり聞き取れていれば (CLEAR_KAMI_COST 以下) 上の句を採り、
    間の読みが候補になっていないとみなす (2026-09-20 第4試合の 33 → 64 → 20)。そうでなければ
    書き起こしとの食い違いが小さいほうを採る。
    """
    result = []
    for i, reading in enumerate(readings):
        poem = source = confirmed = next_shimo = None
        if _starts_kami(reading):
            kami = reading.after if reading.after is not None and reading.after.part == "kami" else None
            shimo = _next_shimo(readings, i)
            next_shimo = shimo.poem if shimo is not None else None
            if kami is not None and shimo is not None:
                confirmed = kami.poem == shimo.poem
            if kami is not None and (
                shimo is None or confirmed or kami.cost <= CLEAR_KAMI_COST or kami.cost <= shimo.cost
            ):
                poem, source = kami.poem, "kami"
            elif shimo is not None:
                poem, source = shimo.poem, "shimo"
        result.append(replace(reading, poem=poem, source=source, confirmed=confirmed, next_shimo=next_shimo))
    return result


def review_text(reading: Reading) -> dict:
    """確認画面に出す、候補の見出し・説明と、おすすめの判断 ("keep" / "remove" / None)。"""
    if reading.source == "kami":
        title = f"{poem_label(reading.poem)} の上の句です"
        if reading.confirmed:
            detail = "次の読みの前の下の句でも同じ歌でした。"
        elif reading.confirmed is False:
            detail = (f"次の読みの前の下の句は {poem_label(reading.next_shimo)} でした。"
                      "間の読みが候補になっていないか、どちらかの特定の誤りです。")
        else:
            detail = ""
        return {"title": title, "detail": detail, "recommend": "keep"}
    if reading.source == "shimo":
        return {
            "title": f"{poem_label(reading.poem)} の上の句と推定しました",
            "detail": "上の句は聞き取れませんでしたが、次の読みの前にこの歌の下の句が読まれています。",
            "recommend": "keep",
        }
    if _starts_kami(reading):
        return {
            "title": "読みの場面のようです",
            "detail": "直前に下の句が読まれていますが、歌は特定できませんでした。",
            "recommend": None,
        }
    if reading.after is not None and reading.after.part == "shimo":
        return {
            "title": "下の句の読み始めです",
            "detail": (f"{poem_label(reading.after.poem)} の下の句が読まれています。"
                       "取りの場面ではないので、外すのがおすすめです。"),
            "recommend": "remove",
        }
    return {
        "title": "歌を特定できませんでした",
        "detail": "読みの声が聞き取れませんでした。取りの場面かどうか、映像で確かめてください。",
        "recommend": None,
    }


def identify_readings(wav_path: str, onsets_sec: list[float], recognizer, progress=None) -> list[Reading]:
    """候補ごとに、直後と直前の読みを書き起こして歌を特定する。"""
    readings = []
    with sf.SoundFile(wav_path) as f:
        sr = f.samplerate

        def clip(start_sec, end_sec):
            start = max(0, int(round(start_sec * sr)))
            end = min(f.frames, int(round(end_sec * sr)))
            f.seek(start)
            samples = f.read(max(0, end - start), dtype="float32", always_2d=True)
            return samples.mean(axis=1)

        for k, onset in enumerate(onsets_sec):
            texts = []
            for lo, hi in (BEFORE_WINDOW, AFTER_WINDOW):
                samples = clip(onset + lo, onset + hi)
                texts.append(recognizer.transcribe(samples, sr) if len(samples) else "")
            before_text, after_text = texts
            readings.append(Reading(
                onset_sec=onset,
                after=match_phrase(after_text),
                before=match_phrase(before_text),
                after_text=after_text,
                before_text=before_text,
            ))
            if progress is not None:
                progress(k + 1, len(onsets_sec))
    return resolve_readings(readings)


def model_cache_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "karuta-edit", "models")


def ensure_model(progress=None) -> str:
    """音声認識モデルの場所を返す。なければダウンロードして展開する (初回のみ、約630MB)。"""
    cache = model_cache_dir()
    model_dir = os.path.join(cache, MODEL_NAME)
    if os.path.isfile(os.path.join(model_dir, "model.int8.onnx")):
        return model_dir
    os.makedirs(cache, exist_ok=True)

    def hook(blocks, block_size, total):
        if progress is not None and total > 0:
            progress(min(blocks * block_size, total), total)

    # 途中で止まっても壊れたモデルが残らないよう、一時フォルダに展開してから移す
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        archive = os.path.join(tmp, "model.tar.bz2")
        urllib.request.urlretrieve(MODEL_URL, archive, reporthook=hook)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(tmp, filter="data")
        os.replace(os.path.join(tmp, MODEL_NAME), model_dir)
    return model_dir


class Recognizer:
    """sherpa-onnx で Parakeet (ja) を動かす。"""

    SAMPLE_RATE = 16000

    def __init__(self, model_dir: str, num_threads: int | None = None):
        import sherpa_onnx
        self._rec = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=os.path.join(model_dir, "model.int8.onnx"),
            tokens=os.path.join(model_dir, "tokens.txt"),
            num_threads=num_threads or max(1, min(4, os.cpu_count() or 1)),
        )

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str:
        # sherpa-onnx に48kHzなどを渡すと、呼ぶたびに再標本化のログを標準エラーへ出すので、
        # 読み込み側で16kHzのWAVを用意する (resample_for_recognition)。
        stream = self._rec.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self._rec.decode_stream(stream)
        return stream.result.text


def resample_for_recognition(input_audio: str, output_wav: str) -> str:
    """抽出済みの音声を、音声認識に渡す16kHz・モノラルのWAVに変換する。"""
    import ffmpeg
    (
        ffmpeg
        .input(input_audio)
        .output(output_wav, ac=1, ar=Recognizer.SAMPLE_RATE)
        .overwrite_output()
        .run(quiet=True)
    )
    return output_wav
