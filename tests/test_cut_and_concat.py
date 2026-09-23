"""短縮版の音声と映像の同期と、元動画との時刻の対応の仕様。

競技かるたでは読みの音と動き出しの時間差が反応の速さそのものなので、
短縮版の各区間で音と映像が 1 フレーム未満の精度でそろっている必要がある。
再生アプリには音声をタイムスタンプではなくデコード順に続けて鳴らすもの
(QuickTime など) があるため、音声を先頭から続けてデコードしたときにそろうことを確かめる。

短縮版で見つけた場面を元動画 (10bit などの元の画質) で確かめられるよう、短縮版には
区間ごとのチャプターがあり、名前に区間の先頭の元動画での時刻が入っている。
短縮版の再生位置 t の元動画での時刻は t - チャプターの開始 + チャプター名の時刻。

テスト素材は 59.94fps の映像で、97 フレームごとに 1 フレームだけ白く光り、
同じ瞬間にビープ音が鳴り始める。
"""
import json
import subprocess
import sys

import numpy as np
import pytest

from offline_app import cut_and_concat_mp4

FPS = 60000 / 1001
FLASH_EVERY = 97
FLASH_PERIOD = FLASH_EVERY / FPS
SR = 48000
W, H = 32, 18

# 区間の開始・終了はフレーム境界からずらしておく (実際の候補位置も 0.1 秒単位で、フレームとはそろわない)
SEGMENTS = [
    (1.234, 3.9),
    (5.01, 7.77),
    (9.333, 11.2),
    (13.1, 16.05),
    (18.47, 21.3),
]
# 1 フレームの 1/4。1 フレームずれるとテストが落ちる精度
TOLERANCE_SEC = 0.25 / FPS

ENCODER_MODES = ["libx264"] + (["videotoolbox_h264"] if sys.platform == "darwin" else [])


H264 = ["-c:v", "libx264", "-g", "60", "-pix_fmt", "yuv420p"]
# iPhone の撮影と同じ HEVC Main10
HEVC_10BIT = ["-c:v", "libx265", "-x265-params", "log-level=error:keyint=60",
              "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1"]


def _make_source(path, video_codec=H264):
    video = (
        f"color=c=black:s=320x180:r=60000/1001:d=24,"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:"
        f"enable='eq(mod(n\\,{FLASH_EVERY})\\,0)'"
    )
    beep = f"0.5*sin(2*PI*1000*t)*lt(mod(t\\,{FLASH_PERIOD:.10f})\\,0.05)"
    audio = f"aevalsrc=exprs='{beep}|{beep}':s={SR}:d=24"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio,
         *video_codec, "-c:a", "aac", "-b:a", "192k", "-y", str(path)],
        check=True,
    )


def _frame_times(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return np.sort([float(line.split(",")[0]) for line in out.split()])


def _flash_times(path):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
         "-vf", f"scale={W}:{H},format=gray", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-"],
        check=True, capture_output=True,
    ).stdout
    brightness = np.frombuffer(raw, dtype=np.uint8).reshape(-1, H * W).mean(axis=1)
    times = _frame_times(path)
    assert len(times) == len(brightness)
    return times[brightness > 128], times


def _decoded_audio(path):
    """先頭から続けてデコードした音声 (タイムスタンプの隙間や重なりは無視される)。"""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-ac", "1", "-f", "f32le", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _beep_onsets(audio):
    loud = np.abs(audio) > 0.25
    quiet_before = int(0.005 * SR)
    onsets = []
    last = -quiet_before
    for i in np.flatnonzero(loud):
        if i - last >= quiet_before:
            onsets.append(i / SR)
        last = i
    return np.array(onsets)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("src") / "source.mp4"
    _make_source(path)
    return path


@pytest.fixture(scope="module", params=ENCODER_MODES)
def shortened(request, source, tmp_path_factory):
    out = tmp_path_factory.mktemp(request.param) / "short.mp4"
    cut_and_concat_mp4(
        input_video=str(source),
        segments=SEGMENTS,
        output_video=str(out),
        encoder_mode=request.param,
    )
    return out


def test_every_flash_is_heard_at_the_same_moment_it_is_seen(shortened):
    flashes, _ = _flash_times(shortened)
    onsets = _beep_onsets(_decoded_audio(shortened))
    assert len(flashes) >= len(SEGMENTS)
    errors = [onsets[np.argmin(np.abs(onsets - t))] - t for t in flashes]
    assert np.max(np.abs(errors)) < TOLERANCE_SEC, [round(e * 1000, 1) for e in errors]


def test_audio_does_not_outlast_video_when_decoded_continuously(shortened):
    _, frame_times = _flash_times(shortened)
    video_end = frame_times[-1] + 1 / FPS
    audio_end = len(_decoded_audio(shortened)) / SR
    # AAC は 1024 サンプル単位なので、最後の 1 フレーム分 (約 21ms) の端数だけ許す
    assert abs(audio_end - video_end) < 1024 / SR


def test_each_segment_keeps_the_frames_inside_its_range(source, shortened):
    source_frames = _frame_times(source)
    expected = sum(
        int(np.sum((source_frames >= start) & (source_frames < end)))
        for start, end in SEGMENTS
    )
    _, frame_times = _flash_times(shortened)
    assert len(frame_times) == expected


def _format_info(path):
    return json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout)


def _chapters(path):
    """(開始, 終了, チャプター名が示す元動画の時刻) の一覧。時刻は秒。"""
    chapters = []
    for c in _format_info(path)["chapters"]:
        hh, mm, ss = c["tags"]["title"].split()[-1].split(":")
        source_sec = int(hh) * 3600 + int(mm) * 60 + float(ss)
        chapters.append((float(c["start_time"]), float(c["end_time"]), source_sec))
    return chapters


def test_chapter_titles_tell_the_source_time_of_every_flash(source, shortened):
    source_start = float(_format_info(source)["format"].get("start_time", 0))
    source_flashes = _flash_times(source)[0] - source_start
    chapters = _chapters(shortened)
    flashes, _ = _flash_times(shortened)
    assert len(flashes) >= len(SEGMENTS)
    for t in flashes:
        start, _, source_sec = next(c for c in chapters if c[0] <= t < c[1])
        estimated = t - start + source_sec
        assert np.min(np.abs(source_flashes - estimated)) < TOLERANCE_SEC, round(t, 3)


def test_chapters_follow_the_segments_from_start_to_end_without_gaps(shortened):
    chapters = _chapters(shortened)
    _, frame_times = _flash_times(shortened)
    assert len(chapters) == len(SEGMENTS)
    assert chapters[0][0] == 0
    assert all(prev[1] == nxt[0] for prev, nxt in zip(chapters, chapters[1:]))
    assert abs(chapters[-1][1] - (frame_times[-1] + 1 / FPS)) < 0.002
    source_starts = [c[2] for c in chapters]
    assert source_starts == sorted(source_starts)
    for (start, _), source_sec in zip(SEGMENTS, source_starts):
        assert start <= source_sec < start + 1 / FPS


def test_source_file_name_is_recorded_in_the_shortened_video(source, tmp_path):
    # アップロードされた動画は一時ファイル名で処理されるので、元の名前を別に渡せる
    out = tmp_path / "short.mp4"
    cut_and_concat_mp4(
        input_video=str(source),
        segments=SEGMENTS[:1],
        output_video=str(out),
        encoder_mode="libx264",
        source_name="2026-09-20_5_第5試合.MOV",
    )
    assert "2026-09-20_5_第5試合.MOV" in _format_info(out)["format"]["tags"]["comment"]


@pytest.mark.skipif(sys.platform != "darwin", reason="VideoToolbox は macOS のみ")
@pytest.mark.parametrize("video_codec", [H264, HEVC_10BIT], ids=["h264", "hevc_10bit"])
def test_hardware_decoding_is_used_without_falling_back_to_software(tmp_path, video_codec):
    source = tmp_path / "source.mov"
    _make_source(source, video_codec)
    stages = []
    cut_and_concat_mp4(
        input_video=str(source),
        segments=SEGMENTS,
        output_video=str(tmp_path / "short.mp4"),
        encoder_mode="videotoolbox_h264",
        prefer_hw_decode=True,
        timing_callback=lambda stage, elapsed, meta: stages.append((stage, meta)),
    )
    attempts = [meta for stage, meta in stages if stage == "prepare_segments"]
    assert [m["use_hw_decode"] for m in attempts] == [True]
    assert not any(stage.startswith("fallback") for stage, _ in stages)
