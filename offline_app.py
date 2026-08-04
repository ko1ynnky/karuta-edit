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



from utils import return_top_scores


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


def _get_primary_video_codec(input_video: str) -> str | None:
    probe = ffmpeg.probe(input_video)
    video_stream = next(
        (s for s in probe["streams"] if s["codec_type"] == "video"),
        None,
    )
    if video_stream is None:
        return None
    codec = video_stream.get("codec_name")
    if not codec:
        return None
    return str(codec)


def _build_decode_input_options(
    input_video: str,
    prefer_hw_decode: bool,
) -> dict[str, object]:
    if not prefer_hw_decode:
        return {}

    codec = _get_primary_video_codec(input_video)
    if codec == "hevc":
        return {"hwaccel": "videotoolbox", "c:v": "hevc_videotoolbox"}
    if codec == "h264":
        return {"hwaccel": "videotoolbox", "c:v": "h264_videotoolbox"}
    return {"hwaccel": "videotoolbox"}


def _encode_single_segment(
    input_video: str,
    segment: tuple[float, float],
    output_video: str,
    encode_options: dict[str, object],
    decode_input_options: dict[str, object],
    seek_preroll_sec: float,
    progress_callback=None,
):
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
    audio_output = (
        input_stream.audio
        .filter("atrim", start=local_start, end=local_end)
        .filter("asetpts", "PTS-STARTPTS")
    )
    output_stream = ffmpeg.output(
        video_output,
        audio_output,
        output_video,
        **encode_options,
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
            .output(
                output_video,
                c="copy",
                movflags="+faststart",
            )
            .overwrite_output()
            .run(quiet=True)
        )
    finally:
        if os.path.exists(concat_file.name):
            os.remove(concat_file.name)


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


def _build_encode_options(
    input_video: str,
    encoder_mode: str,
    crf: int,
    preset: str,
    video_bitrate_scale: float,
) -> dict[str, object]:
    if encoder_mode == "libx264":
        return {
            "vcodec": "libx264",
            "preset": preset,
            "crf": crf,
            "acodec": "aac",
            "fps_mode": "vfr",
            "movflags": "+faststart",
            "b:a": "192k",
        }

    if encoder_mode == "videotoolbox_h264":
        source_bitrate_bps = get_video_bitrate_bps(input_video)
        if source_bitrate_bps is None:
            source_bitrate_bps = 30_000_000

        target_bitrate_bps = int(
            max(2_000_000, source_bitrate_bps * video_bitrate_scale)
        )
        maxrate_bps = int(target_bitrate_bps * 1.3)
        bufsize_bps = int(target_bitrate_bps * 2.0)

        return {
            "vcodec": "h264_videotoolbox",
            "profile:v": "high",
            "acodec": "aac_at",
            "fps_mode": "vfr",
            "movflags": "+faststart",
            "b:v": str(target_bitrate_bps),
            "maxrate:v": str(maxrate_bps),
            "bufsize:v": str(bufsize_bps),
            "b:a": "256k",
            "allow_sw": "0",
            "prio_speed": "1",
            "spatial_aq": "1",
        }

    raise ValueError(f"Unsupported encoder_mode: {encoder_mode}")


_DEFAULT_ENCODER = "videotoolbox_h264" if sys.platform == "darwin" else "libx264"


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
    progress_callback=None,
    timing_callback=None,
):
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
    batch_size = max(1, int(batch_size))
    parallel_workers = max(1, int(parallel_workers))
    if encoder_mode != "videotoolbox_h264":
        parallel_workers = min(parallel_workers, 2)

    def run_with_options(
        encode_options: dict[str, object],
        decode_input_options: dict[str, object],
        selected_encoder_mode: str,
    ):
        tmp_dir = tempfile.mkdtemp(prefix="cut_segments_")
        try:
            segment_paths = [
                os.path.join(tmp_dir, f"segment_{idx:04d}.mp4")
                for idx in range(len(merged_segments))
            ]
            segment_durations = [
                max(0.001, end - start) for start, end in merged_segments
            ]
            total_duration = max(0.001, sum(segment_durations))
            progress_by_segment = [0.0] * len(merged_segments)
            progress_done_duration = 0.0
            progress_target = 0.0
            progress_last_emitted = -1.0
            progress_lock = threading.Lock()

            emit_timing(
                "prepare_segments",
                0.0,
                segment_count=len(merged_segments),
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
                segment = merged_segments[index]
                started_at = time.perf_counter()

                def local_progress_cb(progress: float, idx=index):
                    on_segment_progress(idx, progress)

                _encode_single_segment(
                    input_video=input_video,
                    segment=segment,
                    output_video=segment_paths[index],
                    encode_options=encode_options,
                    decode_input_options=decode_input_options,
                    seek_preroll_sec=seek_preroll_sec,
                    progress_callback=local_progress_cb,
                )
                elapsed = time.perf_counter() - started_at
                return index, elapsed, max(0.001, segment[1] - segment[0])

            encode_started_at = time.perf_counter()
            if parallel_workers == 1 or len(merged_segments) == 1:
                for idx in range(len(merged_segments)):
                    seg_idx, elapsed, duration = encode_segment(idx)
                    emit_progress(force=True)
                    emit_timing(
                        "encode_segment",
                        elapsed,
                        segment_index=seg_idx + 1,
                        total_segments=len(merged_segments),
                        segment_duration_sec=duration,
                    )
            else:
                with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                    pending = {
                        executor.submit(encode_segment, idx)
                        for idx in range(len(merged_segments))
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
                                total_segments=len(merged_segments),
                                segment_duration_sec=duration,
                            )
                    emit_progress(force=True)
            emit_timing(
                "encode_segments_total",
                time.perf_counter() - encode_started_at,
                segment_count=len(merged_segments),
                parallel_workers=parallel_workers,
            )

            if progress_callback is not None:
                progress_callback(0.98)
            concat_started_at = time.perf_counter()
            _concat_batch_outputs_copy(
                batch_paths=segment_paths,
                output_video=output_video,
            )
            emit_timing(
                "concat_segments_copy",
                time.perf_counter() - concat_started_at,
                segment_count=len(segment_paths),
            )
            if progress_callback is not None:
                progress_callback(1.0)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def build_encode_options(selected_encoder_mode: str) -> dict[str, object]:
        return _build_encode_options(
            input_video=input_video,
            encoder_mode=selected_encoder_mode,
            crf=crf,
            preset=preset,
            video_bitrate_scale=video_bitrate_scale,
        )

    primary_decode_options = _build_decode_input_options(
        input_video=input_video,
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

        _, score_dict = return_top_scores(waveform)

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
    