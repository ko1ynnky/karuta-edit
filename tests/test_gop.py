"""Mac版の短縮版のキーフレーム間隔 (GOP) の仕様。

キーフレームは各区間の先頭と、そこから 0.5 秒ごとに入る。細かすぎると同じ
ビットレートでも動きの部分に回るデータが減って画質が落ち、粗すぎるとシークや
コマ戻しのたびに前のキーフレームから復号し直す量が増える。フレームレートに
よらず秒で決まることを、59.94fps と 30fps の素材で確かめる。
"""
import math
import subprocess
import sys

import numpy as np
import pytest

from offline_app import cut_and_concat_mp4

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="VideoToolbox は macOS のみ")

INTERVAL_SEC = 0.5
# 区間の長さを 0.5 秒の倍数からずらし、区間の先頭で間隔が数え直されることも確かめる
SEGMENTS = [(1.0, 4.3), (6.0, 8.7)]


def _make_source(path, rate):
    subprocess.run(
        ["ffmpeg", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=s=320x180:r={rate}:d=10",
         "-f", "lavfi", "-i", "sine=f=440:d=10",
         "-c:v", "libx264", "-g", "1000", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-y", str(path)],
        check=True,
    )


def _video_packets(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    rows = sorted((float(t), "K" in flags) for t, flags in (line.split(",")[:2] for line in out))
    times = np.array([t for t, _ in rows])
    is_key = np.array([k for _, k in rows])
    return times, is_key


@pytest.mark.parametrize("rate", ["60000/1001", "30"], ids=["59.94fps", "30fps"])
def test_keyframes_are_placed_at_each_segment_start_and_every_half_second(tmp_path, rate):
    num, _, den = rate.partition("/")
    fps = int(num) / int(den or 1)
    source = tmp_path / "source.mp4"
    short = tmp_path / "short.mp4"
    _make_source(source, rate)
    cut_and_concat_mp4(
        input_video=str(source),
        segments=SEGMENTS,
        output_video=str(short),
        encoder_mode="videotoolbox_h264",
    )

    source_times, _ = _video_packets(source)
    times, is_key = _video_packets(short)
    expected = []
    segment_start = times[0]
    for start, end in SEGMENTS:
        duration = np.sum((source_times >= start) & (source_times < end)) / fps
        count = math.ceil(duration / INTERVAL_SEC - 1e-6)
        expected += [segment_start + j * INTERVAL_SEC for j in range(count)]
        segment_start += duration

    actual = times[is_key]
    assert len(actual) == len(expected), np.round(actual, 3).tolist()
    assert np.max(np.abs(actual - expected)) < 1 / fps, np.round(actual, 3).tolist()
