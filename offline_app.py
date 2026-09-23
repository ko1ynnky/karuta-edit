import soundfile as sf
import matplotlib.pyplot as plt
import numpy as np
import os
import shutil
import subprocess
import sys
import tempfile
import time
import json
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import ffmpeg
import tqdm



from reader_voice import compute_voiced_frames
from utils import return_candidates


def get_audio_sample_rate(input_video: str) -> int | None:
    probe = ffmpeg.probe(input_video)
    audio_streams = [
        s for s in probe["streams"] if s["codec_type"] == "audio"
    ]
    if not audio_streams:
        return None
    return int(audio_streams[0]["sample_rate"])



def get_media_duration_sec(input_media: str) -> float:
    probe = ffmpeg.probe(input_media)
    format_info = probe.get("format", {})
    try:
        duration = float(format_info.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0.0
    return max(0.0, duration)


def _run_output_stream(
    output_stream,
    progress_callback=None,
    total_duration_sec: float | None = None,
):
    if progress_callback is None:
        output_stream.overwrite_output().run(quiet=True)
        return

    cmd = ffmpeg.compile(output_stream.overwrite_output())
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    total = max(0.0, float(total_duration_sec or 0.0))
    last_progress = -1.0

    try:
        if process.stdout is not None:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)

                if key == "out_time_ms" and total > 0:
                    try:
                        out_time_sec = int(value) / 1_000_000.0
                    except ValueError:
                        continue
                    progress = min(0.999, max(0.0, out_time_sec / total))
                    if progress - last_progress >= 0.005:
                        progress_callback(progress)
                        last_progress = progress
                elif key == "progress" and value == "end":
                    progress_callback(1.0)

        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"ffmpeg exited with status {return_code}")
    finally:
        if process.stdout:
            process.stdout.close()
        process.wait()


def _merge_close_segments(
    segments: list[tuple[float, float]],
    merge_gap_sec: float,
) -> list[tuple[float, float]]:
    if not segments:
        return []

    sorted_segments = sorted(segments, key=lambda x: x[0])
    merged = [sorted_segments[0]]

    for start, end in sorted_segments[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + merge_gap_sec:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _build_decode_input_options(prefer_hw_decode: bool) -> dict[str, object]:
    if not prefer_hw_decode:
        return {}
    # デコーダ (-c:v) は指定しない。hevc_videotoolbox / h264_videotoolbox は
    # エンコーダ名でデコーダとしては存在せず、指定すると必ず失敗して
    # ソフトウェアデコードへのフォールバックになる。-hwaccel だけで標準の
    # デコーダが VideoToolbox を使い、使えないコーデックならソフトウェアで復号する。
    return {"hwaccel": "videotoolbox"}


def _probe_frame_times(media: str) -> np.ndarray:
    """映像の各フレームの表示時刻 (秒) を昇順で返す。

    ffmpeg は入力のタイムスタンプからファイルの start_time を引いて扱うので、
    trim に渡す時刻と同じ基準になるよう start_time を引いておく。
    """
    start_time = float(ffmpeg.probe(media)["format"].get("start_time") or 0.0)
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "packet=pts_time", "-of", "csv=p=0", media,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    values = (line.split(",")[0] for line in out.split())
    times = [float(v) for v in values if v not in ("", "N/A")]
    return np.sort(np.array(times)) - start_time


def _frame_ranges_in_segments(
    segments: list[tuple[float, float]],
    frame_times: np.ndarray,
) -> list[tuple[int, int]]:
    """各区間を、表示時刻が区間内にあるフレームの番号範囲 [first, stop) に置き換える。"""
    ranges = []
    for start, end in segments:
        first = int(np.searchsorted(frame_times, start, side="left"))
        stop = int(np.searchsorted(frame_times, end, side="left"))
        if stop > first:
            ranges.append((first, stop))
    return ranges


def _trim_window(
    frame_times: np.ndarray,
    first: int,
    stop: int,
) -> tuple[float, float]:
    """trim がちょうど first..stop-1 のフレームを選ぶ時刻範囲。

    境界をフレームの表示時刻ちょうどに置くと、シーク後の時刻の丸めで
    前後のフレームが入ったり抜けたりするので、隣のフレームとの中間に置く。
    """
    if first > 0:
        start = (frame_times[first - 1] + frame_times[first]) / 2
    else:
        start = max(0.0, frame_times[0] - 0.001)
    if stop < len(frame_times):
        end = (frame_times[stop - 1] + frame_times[stop]) / 2
    else:
        end = frame_times[-1] + 1.0
    return float(start), float(end)


def _encode_single_segment(
    input_video: str,
    segment: tuple[float, float],
    output_video: str,
    video_options: dict[str, object],
    decode_input_options: dict[str, object],
    seek_preroll_sec: float,
    progress_callback=None,
):
    # 音声はここでは扱わない。区間ごとに AAC にすると先頭のプライミングと末尾の埋め草が
    # つなぎ目ごとに残り、音声をデコード順に鳴らすプレーヤーで音が遅れていくため
    # (_write_audio_following_video で一括して作る)。
    start, end = segment
    seek_preroll_sec = max(0.0, float(seek_preroll_sec))
    seek_start = max(0.0, start - seek_preroll_sec)
    local_start = max(0.0, start - seek_start)
    local_end = max(local_start + 0.001, end - seek_start)

    input_stream = ffmpeg.input(
        input_video,
        ss=seek_start,
        **decode_input_options,
    )
    video_output = (
        input_stream.video
        .filter("trim", start=local_start, end=local_end)
        .filter("setpts", "PTS-STARTPTS")
    )
    output_stream = ffmpeg.output(
        video_output,
        output_video,
        **video_options,
    )
    total_duration_sec = max(0.001, end - start)
    _run_output_stream(
        output_stream=output_stream,
        progress_callback=progress_callback,
        total_duration_sec=total_duration_sec,
    )


def _concat_batch_outputs_copy(
    batch_paths: list[str],
    output_video: str,
):
    concat_file = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        encoding="utf-8",
    )
    try:
        with concat_file as f:
            for path in batch_paths:
                f.write(f"file '{path}'\n")

        (
            ffmpeg
            .input(concat_file.name, format="concat", safe=0)
            .output(output_video, c="copy")
            .overwrite_output()
            .run(quiet=True)
        )
    finally:
        if os.path.exists(concat_file.name):
            os.remove(concat_file.name)


def _extract_audio_pcm(input_video: str, output_wav: str) -> float:
    """音声をチャンネル数・サンプルレートはそのままに WAV へ書き出す。

    戻り値は WAV の先頭サンプルの時刻 (_probe_frame_times と同じ基準)。
    """
    probe = ffmpeg.probe(input_video)
    audio_stream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    format_start = float(probe["format"].get("start_time") or 0.0)
    audio_start = float(audio_stream.get("start_time") or format_start)
    (
        ffmpeg
        .input(input_video)
        .audio
        .output(output_wav, acodec="pcm_s16le")
        .overwrite_output()
        .run(quiet=True)
    )
    return audio_start - format_start


def _read_zero_padded(src: sf.SoundFile, first: int, count: int) -> np.ndarray:
    block = np.zeros((count, src.channels), dtype=np.int16)
    lo = max(first, 0)
    hi = min(first + count, src.frames)
    if hi > lo:
        src.seek(lo)
        block[lo - first:hi - first] = src.read(hi - lo, dtype="int16", always_2d=True)
    return block


def _write_audio_following_video(
    source_wav: str,
    source_start_sec: float,
    placements: list[tuple[float, float]],
    total_duration_sec: float,
    output_wav: str,
):
    """映像の各区間の配置どおりに元の音声を切り貼りした WAV を作る。

    placements は区間ごとの (出力での開始時刻, 元動画での開始時刻)。各区間の音声は
    次の区間の開始 (最後の区間は total_duration_sec) まで続く。区間の長さを
    出力上の境界の差から決めるので、サンプル単位の丸めが区間を重ねても積み上がらない。
    """
    with sf.SoundFile(source_wav) as src:
        sr = src.samplerate
        with sf.SoundFile(
            output_wav, "w", samplerate=sr, channels=src.channels, subtype="PCM_16"
        ) as dst:
            bounds = [round(out_sec * sr) for out_sec, _ in placements]
            bounds.append(round(total_duration_sec * sr))
            firsts = [round((src_sec - source_start_sec) * sr) for _, src_sec in placements]
            # 映像の先頭フレームより前の分も元の音声で埋め、音声の先頭を映像の先頭にそろえる
            firsts[0] -= bounds[0]
            bounds[0] = 0
            for k, first in enumerate(firsts):
                dst.write(_read_zero_padded(src, first, bounds[k + 1] - bounds[k]))


def _format_timestamp(sec: float) -> str:
    ms = round(sec * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def _write_source_time_chapters(
    placements: list[tuple[float, float]],
    total_duration_sec: float,
    output_path: str,
):
    """区間ごとのチャプターを FFMETADATA 形式で書き出す。

    チャプター名は区間の先頭が元動画のどの時刻かを示す (例: "03 元動画 00:12:51.617")。
    短縮版の再生位置 t の元動画での時刻は t - チャプターの開始 + チャプター名の時刻。
    """
    width = max(2, len(str(len(placements))))
    starts_ms = [round(out_sec * 1000) for out_sec, _ in placements]
    ends_ms = starts_ms[1:] + [round(total_duration_sec * 1000)]
    lines = [";FFMETADATA1"]
    for k, ((_, source_sec), start_ms, end_ms) in enumerate(zip(placements, starts_ms, ends_ms)):
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            f"END={end_ms}",
            f"title={k + 1:0{width}d} 元動画 {_format_timestamp(source_sec)}",
        ]
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _source_creation_time(input_video: str) -> str | None:
    return ffmpeg.probe(input_video)["format"].get("tags", {}).get("creation_time")


def _mux_video_and_audio(
    video_path: str,
    audio_path: str,
    chapters_path: str,
    source_name: str,
    creation_time: str | None,
    output_video: str,
    audio_options: dict[str, object],
):
    # ffmpeg-python は映像・音声のストリームを持つ入力しかコマンドに含めないため、
    # チャプターだけの FFMETADATA を入力に加えられず、ここは直接 ffmpeg を呼ぶ
    audio_args = [arg for key, value in audio_options.items() for arg in (f"-{key}", str(value))]
    metadata_args = ["-metadata", f"comment=元動画: {source_name}"]
    # 元動画のメタデータは撮影日時だけを引き継ぐ。iPhone の撮影場所や機種は moov 直下の
    # meta にあり、ffmpeg は MP4 では udta の中にしか書けず写真アプリが読めないうえ、
    # 撮影場所を共有先に渡さずに済む
    if creation_time:
        metadata_args += ["-metadata", f"creation_time={creation_time}"]
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-i", video_path, "-i", audio_path, "-i", chapters_path,
            "-map", "0:v:0", "-map", "1:a:0", "-map_chapters", "2",
            "-c:v", "copy", *audio_args,
            *metadata_args,
            "-movflags", "+faststart",
            output_video,
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        # 呼び出し元のフォールバックは ffmpeg-python の例外で判定しているので、それにそろえる
        raise ffmpeg.Error("ffmpeg", result.stdout, result.stderr)


def _first_positive_int(values: list[object]) -> int | None:
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def get_video_bitrate_bps(input_video: str) -> int | None:
    probe = ffmpeg.probe(input_video)
    video_stream = next(
        (s for s in probe["streams"] if s["codec_type"] == "video"),
        None,
    )
    if video_stream is None:
        return None

    format_info = probe.get("format", {})
    bitrate_bps = _first_positive_int([
        video_stream.get("bit_rate"),
        format_info.get("bit_rate"),
    ])
    if bitrate_bps is not None:
        return bitrate_bps

    size_bytes = _first_positive_int([format_info.get("size")])
    try:
        duration_sec = float(format_info.get("duration", 0))
    except (TypeError, ValueError):
        duration_sec = 0.0

    if size_bytes is None or duration_sec <= 0:
        return None
    return int((size_bytes * 8) / duration_sec)


_KEYFRAME_INTERVAL_SEC = 0.5


def _build_encode_options(
    input_video: str,
    encoder_mode: str,
    crf: int,
    preset: str,
    video_bitrate_scale: float,
    frame_rate: float,
) -> tuple[dict[str, object], dict[str, object]]:
    """(区間の映像エンコード用, 最後に一括で行う音声エンコード用) のオプションを返す。"""
    if encoder_mode == "libx264":
        return (
            {
                "vcodec": "libx264",
                "preset": preset,
                "crf": crf,
                "fps_mode": "vfr",
            },
            {"acodec": "aac", "b:a": "192k"},
        )

    if encoder_mode == "videotoolbox_h264":
        source_bitrate_bps = get_video_bitrate_bps(input_video)
        if source_bitrate_bps is None:
            source_bitrate_bps = 30_000_000

        target_bitrate_bps = int(
            max(2_000_000, source_bitrate_bps * video_bitrate_scale)
        )
        maxrate_bps = int(target_bitrate_bps * 1.3)
        bufsize_bps = int(target_bitrate_bps * 2.0)
        # -g を省くと ffmpeg の既定の 12 フレームになり、59.94fps の対局動画では
        # データ量の約半分をキーフレームが占めて画質が落ちていた。0.5 秒より延ばしても
        # 画質の伸びは小さく (1 秒で PSNR +0.3dB)、シークやコマ戻しで復号し直す量が増える。
        # libx264 は x264 の既定 (250 フレーム) が使われ、CRF で画質を決めているので指定しない。
        keyframe_interval = max(1, round(frame_rate * _KEYFRAME_INTERVAL_SEC))

        return (
            {
                "vcodec": "h264_videotoolbox",
                "profile:v": "high",
                "fps_mode": "vfr",
                "g": str(keyframe_interval),
                "b:v": str(target_bitrate_bps),
                "maxrate:v": str(maxrate_bps),
                "bufsize:v": str(bufsize_bps),
                "allow_sw": "0",
                "prio_speed": "1",
                "spatial_aq": "1",
            },
            {"acodec": "aac_at", "b:a": "256k"},
        )

    raise ValueError(f"Unsupported encoder_mode: {encoder_mode}")


_DEFAULT_ENCODER = "videotoolbox_h264" if sys.platform == "darwin" else "libx264"


def shortened_file_name(source_name: str) -> str:
    return f"{os.path.splitext(source_name)[0]}_short.mp4"


def extract_audio(input_video: str, output_audio: str):
    """MP4などの動画から音声を抽出してWAVに変換"""
    probe = ffmpeg.probe(input_video)
    audio_streams = [
        s for s in probe["streams"] if s["codec_type"] == "audio"
    ]

    if not audio_streams:
        raise RuntimeError("No audio stream found in video")

    sample_rate = int(audio_streams[0]["sample_rate"])

    (
        ffmpeg
        .input(input_video)
        .output(
            output_audio,
            ac=1,              # mono
            ar=sample_rate     # original sample rate
        )
        .overwrite_output()
        .run(quiet=True)
    )

    return output_audio



def simplify_waveform(input_path: str, output_path: str):
    # 長時間動画のWAVを丸ごとメモリに載せないよう、ブロック単位で処理する。
    # blocksizeをframe_sizeの倍数にすることでブロック境界が0.1秒フレームに
    # 揃い、一括読み込み版と同一の結果になる (末尾の端数フレーム切り捨ても同じ)。
    with sf.SoundFile(input_path) as f:
        sample_rate = f.samplerate
        frame_size = sample_rate // 10
        block_size = frame_size * 600  # 60秒ぶん
        frame_maxima = []
        for block in f.blocks(
            blocksize=block_size, dtype="float64", always_2d=True
        ):
            # モノラル化
            block = np.abs(block.mean(axis=1))
            num_frames = len(block) // frame_size
            if num_frames == 0:
                break
            frame_maxima.append(
                block[:num_frames * frame_size]
                .reshape(num_frames, frame_size)
                .max(axis=1)
            )

    waveform = (
        np.concatenate(frame_maxima) if frame_maxima else np.empty(0)
    )
    np.save(output_path, waveform)
    print(f"Saved simplified waveform to {output_path}")




def cut_and_concat_mp4(
    input_video: str,
    segments: list[tuple[float, float]],
    output_video: str,
    crf: int = 20,
    preset: str = "veryfast",
    encoder_mode: str = _DEFAULT_ENCODER,
    video_bitrate_scale: float = 1.1,
    merge_gap_sec: float = 0.5,
    batch_size: int = 24,
    parallel_workers: int = 2,
    seek_preroll_sec: float = 1.5,
    prefer_hw_decode: bool = True,
    source_name: str | None = None,
    progress_callback=None,
    timing_callback=None,
):
    """segments の区間を切り出してつなぎ、output_video に書き出す。

    source_name は短縮版に記録する元動画のファイル名 (省略時は input_video のファイル名)。
    アップロードされた動画のように一時ファイル名で処理するときに、元の名前を渡す。
    """
    def emit_timing(stage: str, elapsed_sec: float, **meta):
        if timing_callback is None:
            return
        timing_callback(stage, elapsed_sec, meta)

    total_started_at = time.perf_counter()
    normalized_segments = []
    normalize_started_at = time.perf_counter()
    for start, end in segments:
        start = max(0.0, float(start))
        end = float(end)
        if end > start:
            normalized_segments.append((start, end))
    emit_timing(
        "normalize_segments",
        time.perf_counter() - normalize_started_at,
        input_count=len(segments),
        valid_count=len(normalized_segments),
    )

    if not normalized_segments:
        raise ValueError("No valid segments to process.")

    merge_started_at = time.perf_counter()
    merged_segments = _merge_close_segments(
        segments=normalized_segments,
        merge_gap_sec=max(0.0, float(merge_gap_sec)),
    )
    emit_timing(
        "merge_segments",
        time.perf_counter() - merge_started_at,
        merge_gap_sec=float(merge_gap_sec),
        merged_count=len(merged_segments),
    )
    probe_started_at = time.perf_counter()
    frame_times = _probe_frame_times(input_video)
    frame_ranges = _frame_ranges_in_segments(merged_segments, frame_times)
    if not frame_ranges:
        raise ValueError("No valid segments to process.")
    windows = [_trim_window(frame_times, first, stop) for first, stop in frame_ranges]
    last_stop = frame_ranges[-1][1]
    if last_stop < len(frame_times):
        last_frame_duration = frame_times[last_stop] - frame_times[last_stop - 1]
    elif len(frame_times) >= 2:
        last_frame_duration = frame_times[-1] - frame_times[-2]
    else:
        last_frame_duration = 1 / 30
    # iPhone の動画はフレーム間隔が一定でない (16.7ms の中に 18.3ms が混ざる) ので、
    # 平均ではなく中央値で求める
    frame_rate = (
        1 / float(np.median(np.diff(frame_times))) if len(frame_times) >= 2 else 30.0
    )
    emit_timing(
        "probe_frame_times",
        time.perf_counter() - probe_started_at,
        frame_count=len(frame_times),
        segment_count=len(frame_ranges),
    )
    batch_size = max(1, int(batch_size))
    parallel_workers = max(1, int(parallel_workers))
    if encoder_mode != "videotoolbox_h264":
        parallel_workers = min(parallel_workers, 2)

    def run_with_options(
        encode_options: tuple[dict[str, object], dict[str, object]],
        decode_input_options: dict[str, object],
        selected_encoder_mode: str,
    ):
        video_options, audio_options = encode_options
        tmp_dir = tempfile.mkdtemp(prefix="cut_segments_")
        try:
            segment_paths = [
                os.path.join(tmp_dir, f"segment_{idx:04d}.mp4")
                for idx in range(len(windows))
            ]
            segment_frame_counts = [0] * len(windows)
            segment_durations = [
                max(0.001, end - start) for start, end in windows
            ]
            total_duration = max(0.001, sum(segment_durations))
            progress_by_segment = [0.0] * len(windows)
            progress_done_duration = 0.0
            progress_target = 0.0
            progress_last_emitted = -1.0
            progress_lock = threading.Lock()

            emit_timing(
                "prepare_segments",
                0.0,
                segment_count=len(windows),
                batch_size=batch_size,
                parallel_workers=parallel_workers,
                seek_preroll_sec=seek_preroll_sec,
                encoder_mode=selected_encoder_mode,
                use_hw_decode=bool(decode_input_options),
            )

            def on_segment_progress(segment_index: int, local_progress: float):
                nonlocal progress_done_duration, progress_target
                if progress_callback is None:
                    return
                clamped = min(1.0, max(0.0, float(local_progress)))
                with progress_lock:
                    previous = progress_by_segment[segment_index]
                    if clamped <= previous:
                        return
                    progress_by_segment[segment_index] = clamped
                    progress_done_duration += (
                        clamped - previous
                    ) * segment_durations[segment_index]
                    mapped = min(
                        0.97,
                        max(0.0, (progress_done_duration / total_duration) * 0.97),
                    )
                    if mapped > progress_target:
                        progress_target = mapped

            def emit_progress(force: bool = False):
                nonlocal progress_last_emitted
                if progress_callback is None:
                    return
                with progress_lock:
                    target = progress_target
                if target <= progress_last_emitted and not force:
                    return
                if not force and target - progress_last_emitted < 0.002 and target < 0.97:
                    return
                progress_last_emitted = target
                progress_callback(target)

            def encode_segment(index: int):
                segment = windows[index]
                started_at = time.perf_counter()

                def local_progress_cb(progress: float, idx=index):
                    on_segment_progress(idx, progress)

                _encode_single_segment(
                    input_video=input_video,
                    segment=segment,
                    output_video=segment_paths[index],
                    video_options=video_options,
                    decode_input_options=decode_input_options,
                    seek_preroll_sec=seek_preroll_sec,
                    progress_callback=local_progress_cb,
                )
                # 結合後のどのフレームがどの区間かを知るため、実際に書き出されたフレーム数を数える
                segment_frame_counts[index] = len(_probe_frame_times(segment_paths[index]))
                elapsed = time.perf_counter() - started_at
                return index, elapsed, max(0.001, segment[1] - segment[0])

            encode_started_at = time.perf_counter()
            if parallel_workers == 1 or len(windows) == 1:
                for idx in range(len(windows)):
                    seg_idx, elapsed, duration = encode_segment(idx)
                    emit_progress(force=True)
                    emit_timing(
                        "encode_segment",
                        elapsed,
                        segment_index=seg_idx + 1,
                        total_segments=len(windows),
                        segment_duration_sec=duration,
                    )
            else:
                with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                    pending = {
                        executor.submit(encode_segment, idx)
                        for idx in range(len(windows))
                    }
                    while pending:
                        done, pending = wait(
                            pending,
                            timeout=0.2,
                            return_when=FIRST_COMPLETED,
                        )
                        emit_progress(force=False)
                        for future in done:
                            seg_idx, elapsed, duration = future.result()
                            emit_timing(
                                "encode_segment",
                                elapsed,
                                segment_index=seg_idx + 1,
                                total_segments=len(windows),
                                segment_duration_sec=duration,
                            )
                    emit_progress(force=True)
            emit_timing(
                "encode_segments_total",
                time.perf_counter() - encode_started_at,
                segment_count=len(windows),
                parallel_workers=parallel_workers,
            )

            concat_started_at = time.perf_counter()
            video_only_path = os.path.join(tmp_dir, "video_only.mp4")
            _concat_batch_outputs_copy(
                batch_paths=segment_paths,
                output_video=video_only_path,
            )
            emit_timing(
                "concat_segments_copy",
                time.perf_counter() - concat_started_at,
                segment_count=len(segment_paths),
            )
            if progress_callback is not None:
                progress_callback(0.98)

            audio_started_at = time.perf_counter()
            output_frame_times = _probe_frame_times(video_only_path)
            if len(output_frame_times) != sum(segment_frame_counts):
                raise RuntimeError(
                    "Concatenated frame count does not match the segments: "
                    f"{len(output_frame_times)} != {sum(segment_frame_counts)}"
                )
            segment_first_frames = np.cumsum([0] + segment_frame_counts[:-1])
            placements = [
                (float(output_frame_times[out_idx]), float(frame_times[first]))
                for out_idx, (first, _) in zip(segment_first_frames, frame_ranges)
            ]
            source_wav = os.path.join(tmp_dir, "source_audio.wav")
            aligned_wav = os.path.join(tmp_dir, "aligned_audio.wav")
            source_start_sec = _extract_audio_pcm(input_video, source_wav)
            total_duration_sec = float(output_frame_times[-1] + last_frame_duration)
            _write_audio_following_video(
                source_wav=source_wav,
                source_start_sec=source_start_sec,
                placements=placements,
                total_duration_sec=total_duration_sec,
                output_wav=aligned_wav,
            )
            emit_timing("build_audio", time.perf_counter() - audio_started_at)
            if progress_callback is not None:
                progress_callback(0.99)

            mux_started_at = time.perf_counter()
            # 元動画との対応は別ファイルにせず動画に埋め込む。Web版のダウンロードは
            # 1 回で画面の状態を消すので 2 つ目のファイルを渡しにくく、別ファイルだと
            # 動画だけが共有されて対応が失われやすい
            chapters_path = os.path.join(tmp_dir, "chapters.txt")
            _write_source_time_chapters(placements, total_duration_sec, chapters_path)
            _mux_video_and_audio(
                video_path=video_only_path,
                audio_path=aligned_wav,
                chapters_path=chapters_path,
                source_name=source_name or os.path.basename(input_video),
                creation_time=_source_creation_time(input_video),
                output_video=output_video,
                audio_options=audio_options,
            )
            emit_timing("mux_audio", time.perf_counter() - mux_started_at)
            if progress_callback is not None:
                progress_callback(1.0)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def build_encode_options(
        selected_encoder_mode: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        return _build_encode_options(
            input_video=input_video,
            encoder_mode=selected_encoder_mode,
            crf=crf,
            preset=preset,
            video_bitrate_scale=video_bitrate_scale,
            frame_rate=frame_rate,
        )

    primary_decode_options = _build_decode_input_options(
        prefer_hw_decode=bool(prefer_hw_decode and encoder_mode == "videotoolbox_h264"),
    )

    try:
        run_with_options(
            encode_options=build_encode_options(encoder_mode),
            decode_input_options=primary_decode_options,
            selected_encoder_mode=encoder_mode,
        )
    except (ffmpeg.Error, RuntimeError):
        if primary_decode_options:
            emit_timing("fallback_to_software_decode", 0.0)
            try:
                run_with_options(
                    encode_options=build_encode_options(encoder_mode),
                    decode_input_options={},
                    selected_encoder_mode=encoder_mode,
                )
                return
            except (ffmpeg.Error, RuntimeError):
                if encoder_mode != "videotoolbox_h264":
                    raise
        elif encoder_mode != "videotoolbox_h264":
            raise

        emit_timing("fallback_to_libx264", 0.0)
        run_with_options(
            encode_options=build_encode_options("libx264"),
            decode_input_options={},
            selected_encoder_mode="libx264",
        )
    finally:
        emit_timing(
            "cut_and_concat_total",
            time.perf_counter() - total_started_at,
            output_video=output_video,
        )










def extract_preview_clip(
    input_video: str,
    center_sec: float,
    before_sec: float,
    after_sec: float,
    output_path: str,
) -> str:
    """プレビュー用の短いクリップを高速生成する。

    center_sec を中心に before_sec 前 ~ after_sec 後の区間を
    低品質・低解像度でエンコードし output_path に書き出す。
    """
    start = max(0, center_sec - before_sec)
    duration = (center_sec + after_sec) - start

    inp = ffmpeg.input(input_video, ss=start, t=duration)
    (
        ffmpeg
        .output(
            inp,
            output_path,
            vcodec="libx264",
            preset="ultrafast",
            crf=30,
            vf="scale=min(640\\,iw):-2",
            acodec="aac",
            movflags="+faststart",
            **{"b:a": "128k"},
        )
        .overwrite_output()
        .run(quiet=True)
    )
    return output_path


def main():
    before = 0.5
    after = 2.5
    # Web版の「読手の声で候補を絞る」と同じく既定オフ (雑音の多い動画でのみ True にする)
    use_reader_voice = False

    file_names = os.listdir("offline_app")


    start_time = time.time()

    for file_name_ in file_names:
        if not file_name_.endswith(".mp4"):
            continue
        file_name = file_name_.replace(".mp4","")

        input_file = f"offline_app/{file_name}.mp4"
        extract_audio(input_file, output_audio=f"offline_app/{file_name}.wav")
        simplify_waveform(f"offline_app/{file_name}.wav", output_path=f"offline_app/simplified_{file_name}.npy")
        waveform = np.load(f"offline_app/simplified_{file_name}.npy")

        print("loaded simplified waveform. Time: ", time.time() - start_time)

        voiced = compute_voiced_frames(f"offline_app/{file_name}.wav") if use_reader_voice else None
        score_dict = return_candidates(waveform, voiced)

        print("calculated scores. Time: ", time.time() - start_time)

        sorted_scores = sorted(score_dict.items(), key=lambda x: x[0])

        for idx, score in sorted_scores:
            print(f"Time: {idx/10} s, Score: {score/100} pts")

        segments = []
        for idx, score in sorted_scores:
            center_time = idx / 10.0
            start = max(0.1, center_time - before)
            end = min(center_time + after, len(waveform) / 10.0 - 0.1)
            segments.append((start, end))

        with tqdm.tqdm(total=100) as bar:

            def progress_callback(p):
                bar.n = int(p * 100)
                bar.refresh()

            cut_and_concat_mp4(
                input_video=f"offline_app/{file_name}.mp4",
                segments=segments,
                output_video=f"offline_app/{file_name}短縮版.mp4",
                progress_callback=progress_callback,
            )

        print("Processed video. Time: ", time.time() - start_time)

        os.remove(f"offline_app/{file_name}.wav")
        os.remove(f"offline_app/simplified_{file_name}.npy")





if __name__ == "__main__":
    main()
    