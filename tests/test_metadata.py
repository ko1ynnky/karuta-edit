"""短縮版が元動画の撮影日時を引き継ぐ仕様。

写真アプリなどで元動画と同じ日時に並ぶよう、元動画の撮影日時 (creation_time) を
短縮版にも記録する。撮影場所や機種などは引き継がない。
"""
import json
import subprocess

from offline_app import cut_and_concat_mp4

SHOT_AT = "2026-09-20T06:16:15.000000Z"


def _tags(path):
    return json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout)["format"].get("tags", {})


def _make_source(path, metadata=()):
    subprocess.run(
        ["ffmpeg", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=4",
         "-f", "lavfi", "-i", "sine=f=440:d=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         *metadata, "-y", str(path)],
        check=True,
    )


def _shorten(source, out):
    cut_and_concat_mp4(
        input_video=str(source),
        segments=[(0.5, 2.0), (2.8, 3.5)],
        output_video=str(out),
        encoder_mode="libx264",
    )


def test_shooting_date_is_carried_over(tmp_path):
    source = tmp_path / "source.mov"
    _make_source(source, ["-metadata", f"creation_time={SHOT_AT}",
                          "-metadata", "location=+35.0566+136.6784/"])
    out = tmp_path / "short.mp4"
    _shorten(source, out)
    tags = _tags(out)
    assert tags.get("creation_time") == SHOT_AT
    assert "location" not in tags


def test_video_without_shooting_date_can_still_be_shortened(tmp_path):
    source = tmp_path / "source.mp4"
    _make_source(source)
    assert "creation_time" not in _tags(source)
    out = tmp_path / "short.mp4"
    _shorten(source, out)
    assert "creation_time" not in _tags(out)
