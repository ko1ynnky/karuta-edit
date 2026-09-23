"""確認画面の札の列: 候補を時刻順の取り札として1列に並べ、確認中の札を中央に置く。

札を押すと、その候補へ移る (trigger 値 "jump" に候補の位置を返す)。
格子に並べると一覧に見えて「1件目から順に見る」流れが伝わらなかったため、1列にしている。
"""
import streamlit as st

from hyakunin_isshu import POEMS
from poem_id import poem_label

_CSS = """
.ks-root { font-family: 'Hiragino Sans', 'Yu Gothic', sans-serif; color: #1F231E; }
.ks-map { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 14px; align-items: center;
  margin-bottom: 8px; font-size: 12px; color: #535A50; font-variant-numeric: tabular-nums; }
.ks-map-bar { position: relative; height: 12px; border-radius: 6px; background: #E1E3DA; }
.ks-map-window { position: absolute; top: -3px; bottom: -3px; box-sizing: border-box;
  border: 1.5px solid #1F231E; border-radius: 4px; transition: left .35s ease, width .35s ease; }
.ks-map-mark { position: absolute; top: 1px; width: 4px; height: 10px; margin-left: -2px;
  border-radius: 1px; background: #B8321F; }
.ks-map-mark.done { box-sizing: border-box; border: 1px solid #B8321F; background: #FFFFFF; }
.ks-map-mark.now { top: -1px; width: 5px; height: 14px; background: #1F231E; }
.ks-viewport { position: relative; overflow: hidden; padding: 14px 20px 10px;
  border-top: 10px solid #213A2F; border-bottom: 10px solid #213A2F;
  background: repeating-linear-gradient(90deg, rgba(92,86,38,.08) 0 1px, transparent 1px 5px), #D6D3A8; }
.ks-strip { margin: 0; padding: 0; list-style: none; display: flex; gap: 8px; width: max-content;
  transition: transform .35s cubic-bezier(.2,.7,.2,1); }
.ks-item { width: 52px; flex-shrink: 0; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.ks-card { width: 52px; height: 72px; box-sizing: border-box; padding: 0; border-radius: 3px; font: inherit;
  cursor: pointer; display: flex; align-items: center; justify-content: center; }
.ks-card:focus-visible { outline: 3px solid #B8321F; outline-offset: 2px; }
.ks-up { border: 1px solid #BDB795; background: #FFFFFF; }
.ks-dim { opacity: .4; }
.ks-kana { writing-mode: vertical-rl; font-family: 'Yuji Syuku', 'Hiragino Mincho ProN', 'Yu Mincho', serif;
  font-size: 10px; line-height: 1.35; letter-spacing: .02em; color: #1F231E; }
.ks-kana span { display: block; }
.ks-down, .ks-plain { flex-direction: column; justify-content: flex-end; gap: 2px; padding-bottom: 6px;
  color: #F2F3EE; font-size: 11px; font-weight: 700;
  background: repeating-linear-gradient(45deg, rgba(255,255,255,.07) 0 2px, transparent 2px 6px), #2A4436; }
.ks-down { border: 2px solid #B8321F; }
.ks-plain { border: 1px solid #6F7A6C; }
.ks-kept { border: 2px solid #213A2F; }
.ks-off { flex-direction: column; gap: 4px; border: 1.5px dashed #8E8A6C; background: transparent;
  color: #4E4C3B; font-size: 11px; font-weight: 700; }
.ks-note { font-size: 10px; font-weight: 400; }
.ks-now { outline: 3px solid #1F231E; outline-offset: 3px; }
.ks-time { font-size: 11px; color: #3F3D2E; font-variant-numeric: tabular-nums; }
.ks-rev { color: #213A2F; font-weight: 700; }
.ks-map-mark.rev { top: 8px; width: 3px; height: 4px; margin-left: -1.5px; background: #213A2F; }
@media (prefers-reduced-motion: reduce) { .ks-strip, .ks-map-window { transition: none; } }
"""

_JS = """
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function card(it, i, now) {
  const cur = now ? ' ks-now' : '';
  const attrs = `type="button" data-i="${i}"${now ? ' aria-current="true"' : ''}`;
  const rev = it.reviewed ? ' 確認済み' : '';
  let face;
  if (it.state === 'known' || it.state === 'known_off') {
    const dim = it.state === 'known_off' ? ' ks-dim' : '';
    const kana = it.kana.map((k) => `<span>${esc(k)}</span>`).join('');
    face = `<button ${attrs} class="ks-card ks-up${dim}${cur}" aria-label="#${it.n} ${it.time} ${esc(it.label)}${rev}"><span class="ks-kana">${kana}</span></button>`;
  } else if (it.state === 'off') {
    face = `<button ${attrs} class="ks-card ks-off${cur}" aria-label="#${it.n} ${it.time} 外した${rev}">${it.n}<span class="ks-note">外した</span></button>`;
  } else if (it.state === 'kept') {
    face = `<button ${attrs} class="ks-card ks-down ks-kept${cur}" aria-label="#${it.n} ${it.time} 残した${rev}">${it.n}<span class="ks-note">残す</span></button>`;
  } else {
    const cls = it.state === 'check' ? 'ks-down' : 'ks-plain';
    const what = it.state === 'check' ? ' 確かめる' : '';
    face = `<button ${attrs} class="ks-card ${cls}${cur}" aria-label="#${it.n} ${it.time}${what}${rev}">${it.n}</button>`;
  }
  const time = it.reviewed ? `<span class="ks-time ks-rev">✓${it.time}</span>` : `<span class="ks-time">${it.time}</span>`;
  return `<li class="ks-item">${face}${time}</li>`;
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let root = parentElement.querySelector('.ks-root');
  if (!root) {
    root = document.createElement('div');
    root.className = 'ks-root';
    root.innerHTML = '<div class="ks-map"><span class="ks-first"></span><div class="ks-map-bar"><div class="ks-map-window"></div></div><span class="ks-last"></span></div>'
      + '<div class="ks-viewport"><ol class="ks-strip" aria-label="すべての候補（時刻順）"></ol></div>';
    parentElement.appendChild(root);
  }
  const items = data.items || [];
  const current = data.current;
  const strip = root.querySelector('.ks-strip');
  const bar = root.querySelector('.ks-map-bar');
  root.querySelector('.ks-first').textContent = items.length ? `1件目　${items[0].time}` : '';
  root.querySelector('.ks-last').textContent = items.length ? `${items.length}件目　${items[items.length - 1].time}` : '';
  strip.innerHTML = items.map((it, i) => card(it, i, i === current)).join('');
  strip.onclick = (e) => {
    const b = e.target.closest('button[data-i]');
    if (b) setTriggerValue('jump', Number(b.dataset.i));
  };

  bar.querySelectorAll('.ks-map-mark').forEach((m) => m.remove());
  const lastIndex = Math.max(1, items.length - 1);
  items.forEach((it, i) => {
    const flagged = it.state === 'check' || it.state === 'kept' || it.state === 'off';
    if (!flagged && !it.reviewed && i !== current) return;
    const m = document.createElement('span');
    const kind = i === current ? ' now' : it.state === 'check' ? '' : flagged ? ' done' : ' rev';
    m.className = 'ks-map-mark' + kind;
    m.style.left = `${(i / lastIndex) * 100}%`;
    bar.appendChild(m);
  });

  const place = () => {
    const viewport = root.querySelector('.ks-viewport');
    const el = strip.children[current];
    const visible = viewport.clientWidth - 40;
    const full = strip.scrollWidth;
    const max = Math.max(0, full - visible);
    // 列に transform があるので、札の offsetLeft は列の左端からの位置になる
    const center = el ? el.offsetLeft + el.offsetWidth / 2 : 0;
    const x = Math.max(0, Math.min(center - visible / 2, max));
    strip.style.transform = `translateX(${-x}px)`;
    const win = root.querySelector('.ks-map-window');
    win.style.left = `${full ? (x / full) * 100 : 0}%`;
    win.style.width = `${full ? Math.min(100, (visible / full) * 100) : 100}%`;
  };
  // 描き替えた直後にその場で位置を決める。requestAnimationFrame だけに任せると、
  // 前面にないタブでは描画が止まり、列が送られないままになる
  place();
  // 後片付けは部品が外されたときにしか呼ばれないので、描くたびに観測を足さず、1つを使い回す
  root._place = place;
  if (!root._observer) {
    root._observer = new ResizeObserver(() => root._place());
    root._observer.observe(root);
  }
  return () => {
    root._observer.disconnect();
    root._observer = null;
  };
}
"""



def _mmss(sec: float) -> str:
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def _torifuda_columns(poem: int) -> list[str]:
    """取り札と同じく、下の句を5・5・残りの3行に分ける。"""
    shimo = POEMS[poem][2].replace(" ", "")
    return [shimo[:5], shimo[5:10], shimo[10:]]


def strip_items(sorted_scores, readings: dict, enabled: dict, reviewed: set) -> list[dict]:
    """候補を札の列の中身にする。

    state: known (歌が分かった) / known_off (歌は分かったが外した) / check (確かめる) /
    kept・off (確かめて残した・外した) / plain (歌の特定を使わず、まだ確かめていない)。
    reviewed: 外す・残すを1回以上押したか。途中まで確かめたとき、どこまで見たかが分かるように
    すべての札で持つ (ユーザーの要望)。
    """
    items = []
    for n, (idx, _) in enumerate(sorted_scores, 1):
        reading = readings.get(idx)
        poem = reading.poem if reading is not None else None
        if poem is not None:
            state = "known" if enabled[idx] else "known_off"
        elif idx in reviewed:
            state = "kept" if enabled[idx] else "off"
        else:
            state = "check" if readings else "plain"
        items.append({
            "n": n,
            "time": _mmss(idx / 10),
            "state": state,
            "kana": _torifuda_columns(poem) if poem is not None else [],
            "label": poem_label(poem) if poem is not None else "",
            "reviewed": idx in reviewed,
        })
    return items


def card_strip(items: list[dict], current: int, key: str, on_jump):
    """札の列を描く。札が押されると on_jump が呼ばれ、st.session_state[key] に位置が入る。"""
    # 登録は描くたびに行う。モジュールの読み込み時に1回だけ登録すると、Streamlit の実行環境が
    # 作り直されたとき (AppTest のテストごとなど) に登録が消え、"is not registered" で落ちる。
    # 同じ定義での登録し直しは警告も出ない。
    component = st.components.v2.component("karuta_card_strip", css=_CSS, js=_JS)
    return component(key=key, data={"items": items, "current": current}, on_jump_change=on_jump)


def review_queue(sorted_scores, readings: dict) -> list[int]:
    """確かめる札の位置 (時刻順)。歌の特定を使ったときは歌が分からなかった候補、使わなければ全候補。"""
    if not readings:
        return list(range(len(sorted_scores)))
    return [i for i, (idx, _) in enumerate(sorted_scores) if readings[idx].poem is None]


def neighbor(queue: list[int], current: int, step: int) -> int | None:
    """current から step の向き (+1: 次、-1: 前) にある、いちばん近い確かめる札。"""
    ahead = [i for i in queue if (i - current) * step > 0]
    if not ahead:
        return None
    return ahead[0] if step > 0 else ahead[-1]


def queue_steps(queue: list[int], sorted_scores, enabled: dict, reviewed: set, current: int) -> list[dict]:
    """「確かめる順番」の表示: 何番目か、候補番号、状態 (off: 外した / kept: 残した / now / todo)。"""
    steps = []
    for order, i in enumerate(queue, 1):
        idx = sorted_scores[i][0]
        if i == current:
            state = "now"
        elif idx in reviewed:
            state = "kept" if enabled[idx] else "off"
        else:
            state = "todo"
        steps.append({"order": order, "n": i + 1, "state": state})
    return steps
