"""画面の共通部品: 手順の表示、ページ離脱の確認、再生が終わったら次の候補へ進む仕組み。"""
import math

import streamlit as st

STEPS = ["動画を選ぶ", "解析", "確認", "書き出し"]

# st.html は SVG を取り除く (2026-09-23 に Chrome で確認。完了の印が空の丸になった) ので、文字で描く
_CHECK = '<span aria-hidden="true" style="font-size:13px;line-height:1;">✓</span>'
_HIDDEN = "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;"

_PAGE_CSS = """
<style>
[data-testid="stMainBlockContainer"] { padding-top: 1.25rem; padding-bottom: 2.5rem; max-width: 1440px; }
[data-testid="stMainBlockContainer"] h1 { font-size: 2rem; }
[data-testid="stMainBlockContainer"] h2 { font-size: 1.6rem; padding: .2rem 0 .1rem; }
[data-testid="stMainBlockContainer"] h3 { font-size: 1.25rem; padding: .2rem 0; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMetricValue"] { font-size: 1.35rem; }
@keyframes karuta-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .karuta-spin { animation: none !important; } }
</style>
"""


def apply_page_style() -> None:
    """Streamlit 既定の広い上の余白と大きな見出しを詰める。

    内部のクラス名はバージョンで変わるので、data-testid だけを使い、上書きは最小限にする。
    """
    st.html(_PAGE_CSS)


def stepper_html(current: int) -> str:
    """アプリ名と手順 (1 動画を選ぶ → 4 書き出し)。current は今の手順、5 はすべて完了。"""
    items = []
    for number, name in enumerate(STEPS, 1):
        if number < current:
            mark = (f'<span style="width:24px;height:24px;box-sizing:border-box;border-radius:50%;'
                    f'border:1.5px solid #213A2F;display:flex;align-items:center;justify-content:center;">{_CHECK}</span>')
            items.append(f'<li style="display:flex;align-items:center;gap:8px;color:#213A2F;">{mark}'
                         f'<span style="{_HIDDEN}">完了した手順：</span>{name}</li>')
        elif number == current:
            mark = (f'<span style="width:24px;height:24px;border-radius:50%;background:#213A2F;color:#FFFFFF;'
                    f'display:flex;align-items:center;justify-content:center;font-size:12px;">{number}</span>')
            items.append(f'<li aria-current="step" style="display:flex;align-items:center;gap:8px;color:#213A2F;'
                         f'font-weight:700;">{mark}{name}</li>')
        else:
            mark = (f'<span style="width:24px;height:24px;box-sizing:border-box;border-radius:50%;'
                    f'border:1.5px solid #9DA396;display:flex;align-items:center;justify-content:center;'
                    f'font-size:12px;">{number}</span>')
            items.append(f'<li style="display:flex;align-items:center;gap:8px;color:#666B62;">{mark}{name}</li>')
    return (
        '<header style="display:flex;flex-wrap:wrap;align-items:center;gap:12px 48px;padding:0 0 14px;'
        'border-bottom:1px solid #D5D8CC;">'
        '<div style="font-size:17px;font-weight:700;letter-spacing:.06em;color:#213A2F;">かるた動画の短縮</div>'
        '<ol aria-label="手順" style="position:relative;margin:0;padding:0;list-style:none;display:flex;'
        'flex-wrap:wrap;gap:8px 28px;font-size:14px;">' + "".join(items) + "</ol></header>"
    )


def needs_leave_guard(state: int, saved: bool, analyzing: bool = False) -> bool:
    """リロードやタブを閉じる前に確認するか。

    解析中と、確認中 (2)・書き出し中 (3) は、解析と確認の結果が消えて1〜数分かけて作り直しになる。
    完了 (4) は、保存するまで短縮版が消える。
    """
    return analyzing or state in (2, 3) or (state == 4 and not saved)


# 書き出しのように描き終える前に長く止まる画面では、前の画面の要素が薄く残る
# (Streamlit は、実行が終わるまで前の実行の要素を消さない)。確認画面の一番上の階層の
# 要素 (2026-09-23 時点で7個) より多い空の要素で上書きして消す。空の要素は場所を取らない
LEFTOVER_SLOTS = 12


def cover_leftovers() -> None:
    for _ in range(LEFTOVER_SLOTS):
        st.empty()


def duration_jp(sec: float) -> str:
    """動画の長さを「55分43秒」の形にする。"""
    sec = int(round(sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}時間{m}分{s}秒"
    return f"{m}分{s}秒" if m else f"{s}秒"


# cut_and_concat_mp4 は、場面の切り出しが終わるまでを 0〜0.97 で知らせる
_CUT_DONE = 0.97


def export_progress_text(fraction: float, elapsed: float) -> str:
    """書き出しの進み具合と、これまでの速さから見積もった残り時間。"""
    if fraction <= 0:
        return "準備しています"
    pct = int(fraction * 100)
    if fraction >= _CUT_DONE:
        return f"{pct}%　仕上げています"
    if fraction < 0.05:
        return f"{pct}%　残り時間を見積もっています"
    remaining = elapsed / fraction * (1 - fraction)
    if remaining >= 60:
        return f"{pct}%　残り約{math.ceil(remaining / 60)}分"
    return f"{pct}%　残り約{math.ceil(remaining)}秒"


def step_html(status: str, title: str, detail: str = "") -> str:
    """解析の段階1行。status は done (完了) / run (処理中) / todo (これから)。"""
    if status == "done":
        mark = ('<span style="width:24px;height:24px;flex-shrink:0;border-radius:50%;background:#213A2F;color:#FFFFFF;'
                'display:flex;align-items:center;justify-content:center;">' + _CHECK + "</span>")
    elif status == "run":
        mark = ('<span class="karuta-spin" style="width:24px;height:24px;flex-shrink:0;box-sizing:border-box;'
                'border-radius:50%;border:2px solid #213A2F;border-right-color:transparent;'
                'animation:karuta-spin 1s linear infinite;"></span>')
    else:
        mark = ('<span style="width:24px;height:24px;flex-shrink:0;box-sizing:border-box;border-radius:50%;'
                'border:1.5px solid #9DA396;"></span>')
    color = "#1F231E" if status != "todo" else "#666B62"
    sub = f'<div style="font-size:13px;color:#535A50;font-variant-numeric:tabular-nums;">{detail}</div>' if detail else ""
    return (f'<div style="display:flex;gap:14px;align-items:flex-start;padding:6px 0;">{mark}'
            f'<div><div style="font-size:15px;font-weight:700;color:{color};">{title}</div>{sub}</div></div>')


_MINI_SLOT = 'width:40px;height:56px;box-sizing:border-box;border-radius:2px;flex-shrink:0;'
FLOW_HTML = (
    '<div style="font-size:15px;font-weight:700;color:#3E443B;margin:8px 0 18px;">このあとの流れ</div>'
    '<ol style="margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:22px;">'
    '<li style="display:flex;gap:18px;align-items:center;">'
    f'<span style="{_MINI_SLOT}border:1.5px dashed #A8A383;"></span>'
    '<div><div style="font-size:15px;font-weight:700;">1　読みの候補を探す</div>'
    '<div style="font-size:13px;line-height:1.7;color:#535A50;">音声から、上の句の読み始めを見つけます。</div></div></li>'
    '<li style="display:flex;gap:18px;align-items:center;">'
    f'<span style="{_MINI_SLOT}border:1px solid #BDB795;background:#FFFFFF;display:flex;align-items:center;justify-content:center;">'
    '<span style="writing-mode:vertical-rl;font-family:\'Yuji Syuku\',\'Hiragino Mincho ProN\',serif;font-size:8px;'
    'line-height:1.3;"><span style="display:block;">からくれな</span><span style="display:block;">ゐにみづく</span>'
    '<span style="display:block;">くるとは</span></span></span>'
    '<div><div style="font-size:15px;font-weight:700;">2　読まれた歌を聞き分ける</div>'
    '<div style="font-size:13px;line-height:1.7;color:#535A50;">歌が分かった場面は、そのまま短縮版に入ります。</div></div></li>'
    '<li style="display:flex;gap:18px;align-items:center;">'
    f'<span style="{_MINI_SLOT}border:2px solid #B8321F;background:#2A4436;"></span>'
    '<div><div style="font-size:15px;font-weight:700;">3　分からなかった場面を確かめる</div>'
    '<div style="font-size:13px;line-height:1.7;color:#535A50;">残すか外すかを、あなたが決めます。</div></div></li>'
    '</ol>'
)


# この関数は描き直すたびに呼ばれるが、返した後片付けは部品が外されたときにしか呼ばれない
# (2026-09-23 に試作で確認)。描くたびに listener を足すと、保存後や最初の画面に戻っても
# 確認が出続けたので、listener は1つだけ付け、確認するかは最新の値で決める
_GUARD_JS = """
export default function (component) {
  const { data } = component;
  window.__karutaLeaveGuard = Boolean(data && data.active);
  if (!window.__karutaLeaveGuardInstalled) {
    window.__karutaLeaveGuardInstalled = true;
    window.addEventListener('beforeunload', (e) => {
      if (!window.__karutaLeaveGuard) return;
      e.preventDefault();
      e.returnValue = '';
    });
  }
  return () => { window.__karutaLeaveGuard = false; };
}
"""


def leave_guard(active: bool) -> None:
    """active の間、リロードやタブを閉じようとするとブラウザの確認を出す (文言はブラウザが決める)。"""
    # 部品の登録は描くたびに行う (card_strip.card_strip と同じ理由)
    component = st.components.v2.component("karuta_leave_guard", js=_GUARD_JS)
    component(key="leave_guard", data={"active": active})


_AUTO_ADVANCE_JS = """
export default function (component) {
  const { data } = component;
  const root = document.documentElement;
  root.dataset.karutaAutoAdvance = data.enabled ? 'on' : 'off';
  if (!data.enabled) return;
  let media = null;
  let timer = null;
  const advance = () => {
    if (root.dataset.karutaAutoAdvance !== 'on') return;
    const btn = [...document.querySelectorAll('button')].find((b) => b.innerText.trim().startsWith(data.nextLabel));
    if (btn) btn.click();
  };
  const bind = () => {
    const m = document.querySelector('video') || document.querySelector('audio');
    if (!m) return false;
    if (m.dataset.karutaAdvanceMarker === data.marker) return true;
    m.dataset.karutaAdvanceMarker = data.marker;
    m.addEventListener('ended', advance, { once: true });
    media = m;
    return true;
  };
  if (!bind()) {
    let tries = 0;
    timer = setInterval(() => { if (bind() || ++tries > 50) clearInterval(timer); }, 100);
  }
  return () => {
    if (timer) clearInterval(timer);
    if (media) media.removeEventListener('ended', advance);
  };
}
"""


def auto_advance(enabled: bool, marker: str, next_label: str) -> None:
    """再生が終わったら「次の件」を押す。

    Streamlit は再生終了を Python 側へ通知しないため、ページの <video>/<audio> の ended を購読する。
    区間の長さだけ time.sleep して rerun する方式は採らない。待っている間はボタンが押せず、
    その候補を残すか外すかを選べなくなるため。
    """
    component = st.components.v2.component("karuta_auto_advance", js=_AUTO_ADVANCE_JS)
    component(key="auto_advance_script", data={"enabled": enabled, "marker": marker, "nextLabel": next_label})
