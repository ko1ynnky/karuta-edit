"""短縮版のファイル名の仕様。

名前順に並べたとき元動画のすぐ後ろに来るよう、元動画の名前に _short を付ける。
言語によらず読めるよう英語にし、中身は常に MP4 なので拡張子は .mp4 にそろえる。
"""
from offline_app import shortened_file_name


def test_short_suffix_is_added_to_the_source_name_and_the_extension_becomes_mp4():
    assert (
        shortened_file_name("2026-09-20_5_和田竜太朗(信州B)-奥田光市(東大A).MOV")
        == "2026-09-20_5_和田竜太朗(信州B)-奥田光市(東大A)_short.mp4"
    )
    assert shortened_file_name("match.mkv") == "match_short.mp4"


def test_it_sorts_right_after_the_source_video():
    names = ["match_1.MOV", "match_2.MOV"]
    names += [shortened_file_name(n) for n in names]
    assert sorted(names) == ["match_1.MOV", "match_1_short.mp4", "match_2.MOV", "match_2_short.mp4"]
