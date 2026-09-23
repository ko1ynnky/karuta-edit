"""札の列の仕様: 候補を時刻順の取り札として並べ、確かめる札と確かめた結果が分かる。"""
from card_strip import strip_items
from poem_id import Reading

SCORES = [(1010, 8126), (1230, 7983), (1530, 8546), (4000, 8000)]


def _reading(idx, poem=None):
    return Reading(idx / 10, None, None, poem=poem, source="kami" if poem is not None else None)


def _items(readings, enabled=None, reviewed=()):
    enabled = enabled or {idx: True for idx, _ in SCORES}
    return strip_items(SCORES, readings, enabled, set(reviewed))


def test_cards_follow_candidate_order_with_number_and_time():
    items = _items({})
    assert [(it["n"], it["time"]) for it in items] == [(1, "01:41"), (2, "02:03"), (3, "02:33"), (4, "06:40")]


def test_identified_card_shows_shimo_no_ku_in_three_columns_like_a_torifuda():
    items = _items({1230: _reading(1230, poem=17)})
    assert items[1]["state"] == "known"
    assert items[1]["kana"] == ["からくれな", "ゐにみづく", "くるとは"]
    assert items[1]["label"] == "17 ちはやぶる"


def test_unidentified_card_is_to_be_checked_until_reviewed():
    items = _items({1010: _reading(1010), 1230: _reading(1230, poem=17)})
    assert items[0]["state"] == "check"
    assert items[0]["kana"] == []


def test_reviewed_card_shows_whether_it_was_kept_or_removed():
    readings = {idx: _reading(idx) for idx, _ in SCORES}
    items = _items(readings, enabled={1010: True, 1230: False, 1530: True, 4000: True}, reviewed=(1010, 1230))
    assert [it["state"] for it in items] == ["kept", "off", "check", "check"]


def test_identified_card_removed_by_hand_is_shown_as_removed():
    items = _items({1230: _reading(1230, poem=17)}, enabled={1010: True, 1230: False, 1530: True, 4000: True})
    assert items[1]["state"] == "known_off"


def test_cards_are_plain_when_poems_were_not_identified():
    assert {it["state"] for it in _items({})} == {"plain"}
