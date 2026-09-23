"""読手の声の検出と、それに基づく読み開始 (上の句) の検出。

競技かるたの読みは「下の句 (約7〜8秒) → 約1秒の間 → 上の句 (約6秒) → 取り →
(待ち) → 下の句 ...」を繰り返す。取りシーンは上の句の開始に始まるので、
上の句開始を見つければよい。

振幅だけを見る既存スコアは、空調などの定常雑音で「間」が埋まると、札の音の
切れ目を読みの間と誤認したり、雑音に埋もれた読み開始を見逃したりする。
そこで音声から「読手の声」を直接検出する。読手の声は

  * 持続する母音の倍音構造を持つ (250〜3000Hz に帯域制限した自己相関のピークが高い)
  * ノイズフロアより十分大きい (遠くの小さな周期音、空調のトーン成分を除く)
  * 音程が短時間で動かない (会話や物音と違い、読みは音程を伸ばす)

の3点で識別する。帯域制限で空調のゴロゴロ音 (250Hz以下) と札の打撃音 (広帯域の
単発) を落とすのが肝で、周期性だけでは空調のトーン成分を拾ってしまう。
"""
import numpy as np
import soundfile as sf

WINDOW_SEC = 0.064
HOP_SEC = 0.025            # 0.1秒の波形フレームあたり4サブフレーム
SUBFRAMES_PER_FRAME = 4
BAND_LO_HZ, BAND_HI_HZ = 250.0, 3000.0
PITCH_LO_HZ, PITCH_HI_HZ = 80.0, 500.0
PERIODICITY_THRESHOLD = 0.75
LEVEL_MARGIN_DB = 15.0     # ノイズフロア (帯域エネルギーの10パーセンタイル) からの余裕
PITCH_STABILITY = 0.15     # 0.1秒内の音程のぶれ (比率)
MIN_VOICED_SUBFRAMES = 2

# 読み開始の判定 (0.1秒フレーム単位)
SEGMENT_MERGE_GAP = 10     # 1.0秒以下の途切れは同じ読みとみなす (息継ぎ・札音による欠落)
SEGMENT_MIN_LEN = 5        # 0.5秒未満の有声区間は無視
ONSET_GAP_MIN, ONSET_GAP_MAX = 10, 30   # 下の句→上の句の間は規定1秒。検出上は1〜3秒に収まる
SHIMO_MIN_LEN = 65         # 下の句は約7〜8秒、上の句は約6秒。6.5秒で下の句だけを通す
ONSET_CHECK_LEN = 30       # 読み開始の直後3秒に
ONSET_MIN_VOICED_RATIO = 0.3   # この割合以上の声があれば上の句とみなす
# 上の句の冒頭は取りの札音 (大きな広帯域音) に重なって声の検出が途切れやすい。
# そのため「つながった有声区間」ではなく直後3秒の声の密度で読み開始を確かめる。


def voicing_features(samples, sample_rate):
    """サブフレームごとの (周期性, 帯域エネルギー dB, 音程 Hz) を返す。"""
    win = int(round(WINDOW_SEC * sample_rate))
    hop = int(round(HOP_SEC * sample_rate))
    n_fft = 1 << (2 * win - 1).bit_length()
    n = (len(samples) - win) // hop + 1
    if n <= 0:
        return np.empty(0), np.empty(0), np.empty(0)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = samples[idx] * np.hanning(win)[None, :]
    power = np.abs(np.fft.rfft(frames, n=n_fft, axis=1)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
    power[:, (freqs < BAND_LO_HZ) | (freqs > BAND_HI_HZ)] = 0.0
    autocorr = np.fft.irfft(power, axis=1)[:, :win]
    energy = autocorr[:, 0] + 1e-12
    autocorr /= energy[:, None]
    lag_lo = int(sample_rate / PITCH_HI_HZ)
    lag_hi = int(sample_rate / PITCH_LO_HZ)
    periodicity = autocorr[:, lag_lo:lag_hi + 1].max(axis=1)
    pitch = sample_rate / (autocorr[:, lag_lo:lag_hi + 1].argmax(axis=1) + lag_lo)
    band_db = 10 * np.log10(energy / win)
    return periodicity, band_db, pitch


def voiced_frames_from_features(periodicity, band_db, pitch, n_frames):
    """サブフレーム特徴量を0.1秒フレームの「読手の声あり」に集約する。"""
    floor = np.percentile(band_db, 10) if len(band_db) else 0.0
    voiced_sub = (periodicity > PERIODICITY_THRESHOLD) & (band_db > floor + LEVEL_MARGIN_DB)
    m = min(len(voiced_sub) // SUBFRAMES_PER_FRAME, n_frames)
    v = voiced_sub[:m * SUBFRAMES_PER_FRAME].reshape(m, SUBFRAMES_PER_FRAME)
    p = pitch[:m * SUBFRAMES_PER_FRAME].reshape(m, SUBFRAMES_PER_FRAME)
    stable = (p.max(axis=1) - p.min(axis=1)) < PITCH_STABILITY * p.mean(axis=1)
    voiced = np.zeros(n_frames, dtype=bool)
    voiced[:m] = (v.sum(axis=1) >= MIN_VOICED_SUBFRAMES) & stable
    return voiced


def compute_voiced_frames(wav_path, block_sec=20.0):
    """WAVから0.1秒フレームごとの「読手の声あり」bool配列を返す。

    長さは simplify_waveform の出力 (len(samples) // (sample_rate // 10)) と揃える。
    長時間の音声を丸ごと載せないよう、窓の重なりぶんを持ち越しつつブロック処理する。
    """
    with sf.SoundFile(wav_path) as f:
        sr = f.samplerate
        n_frames = f.frames // (sr // 10)
        win = int(round(WINDOW_SEC * sr))
        hop = int(round(HOP_SEC * sr))
        subframes_per_block = int(block_sec / HOP_SEC)
        block_samples = subframes_per_block * hop
        feats = []
        pos = 0
        while pos + win <= f.frames:
            f.seek(pos)
            block = f.read(block_samples + win - hop, dtype="float64", always_2d=True).mean(axis=1)
            per, db, pitch = voicing_features(block, sr)
            feats.append((per, db, pitch))
            pos += block_samples
    if not feats:
        return np.zeros(n_frames, dtype=bool)
    per, db, pitch = (np.concatenate(x) for x in zip(*feats))
    return voiced_frames_from_features(per, db, pitch, n_frames)


def voiced_segments(voiced):
    """有声フレーム列を (開始, 終了) の区間にまとめる。終了は含む。"""
    segments = []
    start = last = None
    for i in np.flatnonzero(voiced):
        if start is None:
            start = i
        elif i - last > SEGMENT_MERGE_GAP:
            segments.append((start, last))
            start = i
        last = i
    if start is not None:
        segments.append((start, last))
    return [(int(a), int(b)) for a, b in segments if b - a + 1 >= SEGMENT_MIN_LEN]


def find_reading_onsets(voiced):
    """上の句の開始フレームを返す。

    下の句 (十分長い有声区間) が終わって1〜3秒後に声が立ち上がり、その後3秒に
    一定以上の声があれば上の句の開始とみなす。
    """
    voiced = np.asarray(voiced, dtype=bool)
    onsets = []
    for start, end in voiced_segments(voiced):
        if end - start + 1 < SHIMO_MIN_LEN:
            continue
        # 区間の直後 SEGMENT_MERGE_GAP フレームは統合により無声なので、その先から探す
        search = voiced[end + 1 + ONSET_GAP_MIN:end + 1 + ONSET_GAP_MAX + 1]
        hits = np.flatnonzero(search)
        if len(hits) == 0:
            continue
        onset = end + 1 + ONSET_GAP_MIN + int(hits[0])
        if voiced[onset:onset + ONSET_CHECK_LEN].mean() >= ONSET_MIN_VOICED_RATIO:
            onsets.append(int(onset))
    return onsets
