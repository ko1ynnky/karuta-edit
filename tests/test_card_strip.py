"""札の列の仕様: 候補を時刻順の取り札として並べ、確かめる札と確かめた結果が分かる。"""
from card_strip import neighbor, queue_steps, review_queue, strip_items
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


def test_queue_is_the_unidentified_candidates_in_time_order():
    readings = {1010: _reading(1010), 1230: _reading(1230, poem=17), 1530: _reading(1530), 4000: _reading(4000, poem=3)}
    assert review_queue(SCORES, readings) == [0, 2]


def test_queue_is_every_candidate_without_identification():
    assert review_queue(SCORES, {}) == [0, 1, 2, 3]


def test_next_and_previous_move_among_cards_to_check():
    queue = [0, 2, 3]
    assert neighbor(queue, 0, +1) == 2
    assert neighbor(queue, 2, -1) == 0
    assert neighbor(queue, 3, +1) is None
    assert neighbor(queue, 0, -1) is None


def test_next_from_an_identified_card_goes_to_the_following_card_to_check():
    # 札を押して、確かめなくてよい札に移っていても、そこから次の確かめる札へ進む
    assert neighbor([0, 2, 3], 1, +1) == 2
    assert neighbor([0, 2, 3], 1, -1) == 0


def test_queue_steps_show_what_was_decided_and_where_you_are():
    enabled = {1010: False, 1230: True, 1530: True, 4000: True}
    steps = queue_steps([0, 1, 2, 3], SCORES, enabled, reviewed={1010, 1230}, current=2)
    assert [(s["order"], s["n"], s["state"]) for s in steps] == [
        (1, 1, "off"), (2, 2, "kept"), (3, 3, "now"), (4, 4, "todo"),
    ]
