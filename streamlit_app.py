import streamlit as st
import numpy as np
import importlib.util
import shutil
import soundfile as sf
import subprocess
import sys
import tempfile
import os
import time
from card_strip import card_strip, neighbor, queue_steps, review_queue, strip_items
from page_parts import (
    FLOW_HTML,
    apply_page_style,
    auto_advance,
    leave_guard,
    needs_leave_guard,
    step_html,
    stepper_html,
)
from poem_id import (
    MODEL_DOWNLOAD_MB,
    Recognizer,
    ensure_model,
    identify_readings,
    resample_for_recognition,
    review_text,
)
from reader_voice import compute_voiced_frames
from utils import return_candidates
from offline_app import (
    get_media_duration_sec,
    extract_audio,
    simplify_waveform,
    cut_and_concat_mp4,
    extract_preview_clip,
    shortened_file_name,
)

st.set_page_config(page_title="かるた動画自動編集アプリ", layout="wide")


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------

def format_time(seconds: float) -> str:
    """秒数を MM:SS.s 形式に変換する。"""
    seconds = max(0.0, seconds)
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m:02d}:{s:04.1f}"


def compute_segments(
    sorted_scores: list[tuple[int, float]],
    enabled_set: set[int],
    before: float,
    after: float,
    waveform_len: int,
) -> list[tuple[float, float]]:
    """有効なスコアのみからマージ済みセグメントを構築する。"""
    segments: list[tuple[float, float]] = []
    for idx, _ in sorted_scores:
        if idx not in enabled_set:
            continue
        center = idx / 10.0
        start = max(0.1, center - before)
        end = min(center + after, waveform_len / 10.0 - 0.1)
        if segments and segments[-1][1] >= start:
            segments[-1] = (segments[-1][0], end)
        else:
            segments.append((start, end))
    return segments


def estimate_duration(segments: list[tuple[float, float]]) -> float:
    """セグメント合計秒数を計算する。"""
    return sum(end - start for start, end in segments)


NEXT_BUTTON_LABEL = "次の件"


_TK_DIALOG_CODE = """
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askopenfilename(
    title="動画ファイルを選択",
    filetypes=[("動画", "*.mp4 *.mov *.webm *.mkv"), ("すべて", "*.*")],
)
print(path)
"""


def identify_candidate_poems(
    audio_path: str, tmpdir: str, sorted_scores: list[tuple[int, float]]
) -> dict:
    """候補ごとに読まれた歌を特定する。失敗しても空の結果を返し、編集は続けられるようにする。

    歌の表示はレビューの補助なので、モデルを取得できない (オフラインなど) ときに
    解析全体を止めない。
    """
    bar = st.progress(0.0, text="音声認識のモデルを準備中...")
    shown = {"pct": -1}

    def on_download(done: int, total: int) -> None:
        pct = done * 100 // total
        if pct != shown["pct"]:  # ブロックごとに呼ばれるので、1%ごとにだけ描き直す
            shown["pct"] = pct
            bar.progress(pct / 100, text=f"音声認識のモデルをダウンロード中... {done >> 20}/{total >> 20} MB")

    started = time.monotonic()

    def on_identify(done: int, total: int) -> None:
        remaining = (time.monotonic() - started) / done * (total - done)
        bar.progress(done / total, text=f"{done} / {total}　残り約{int(remaining) + 1}秒")

    try:
        model_dir = ensure_model(progress=on_download)
        wav16k = resample_for_recognition(audio_path, os.path.join(tmpdir, "audio16k.wav"))
        readings = identify_readings(
            wav16k, [idx / 10.0 for idx, _ in sorted_scores], Recognizer(model_dir), progress=on_identify
        )
    except (OSError, RuntimeError, ImportError) as e:
        st.warning(f"読まれた歌を特定できませんでした（{e}）。歌の表示なしで続けます。")
        return {}
    return {idx: reading for (idx, _), reading in zip(sorted_scores, readings)}


def pick_video_file() -> str | None:
    """サーバ側でOSネイティブのファイル選択ダイアログを開き、選択パスを返す。

    ブラウザのセキュリティ制約上、ページ内のファイル選択UIからは
    ローカルパスを取得できないため、同一マシンで動くこのプロセス側から開く。
    Streamlitのスクリプトスレッドからtkinterを直接使うとmacOSで
    クラッシュするため、いずれの方式もサブプロセスで実行する。
    キャンセル時・ダイアログを開けない環境では None を返す。
    """
    if sys.platform == "darwin":
        # macOSはtkinterが未導入のPython環境が多いため、標準のosascriptを使う
        cmd = [
            "osascript", "-e",
            'POSIX path of (choose file with prompt "動画ファイルを選択")',
        ]
    elif importlib.util.find_spec("tkinter") is not None:
        cmd = [sys.executable, "-c", _TK_DIALOG_CODE]
    elif sys.platform == "win32":
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.OpenFileDialog; "
            "$d.Filter = '動画|*.mp4;*.mov;*.webm;*.mkv|すべて|*.*'; "
            "if ($d.ShowDialog() -eq 'OK') { $d.FileName }",
        ]
    else:
        cmd = [
            "zenity", "--file-selection", "--title=動画ファイルを選択",
            "--file-filter=動画 | *.mp4 *.mov *.webm *.mkv",
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


# ---------------------------------------------------------------------------
# 状態の初期化
# ---------------------------------------------------------------------------

if 'state' not in st.session_state:
    st.session_state.state = 1
    for key in list(st.session_state.keys()):
        if key != "state":
            del st.session_state[key]

st.session_state.setdefault("uploader_gen", 0)

apply_page_style()
leave_guard(needs_leave_guard(
    st.session_state.state, st.session_state.get("saved", False), analyzing="pending" in st.session_state,
))

SUPPORTED_EXTS = [".mp4", ".mov", ".webm", ".mkv"]


# ---------------------------------------------------------------------------
# State 1: 動画を選ぶ・解析
# ---------------------------------------------------------------------------

def _duration_text(sec: float | None) -> str:
    if not sec:
        return "不明"
    sec = int(round(sec))
    h, rest = divmod(sec, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60:02d}:{rest % 60:02d}"


def _size_text(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


def _describe_video(path: str, name: str, tmpdir: str | None = None) -> dict:
    try:
        duration = get_media_duration_sec(path)
    except Exception:  # 壊れたファイルなど。長さは表示の補助なので、分からなくても先へ進める
        duration = None
    return {"path": path, "name": name, "tmpdir": tmpdir, "duration": duration, "size": os.path.getsize(path)}


def _choose_path() -> None:
    ss = st.session_state
    ss.pop("choose_error", None)
    raw = ss.get("local_path_input", "").strip().strip("'\"")
    if not raw:
        return
    path = os.path.expanduser(raw)
    if not os.path.isfile(path):
        ss.choose_error = "指定されたパスにファイルが見つかりません。"
    elif os.path.splitext(path)[1].lower() not in SUPPORTED_EXTS:
        ss.choose_error = "対応している動画形式は MP4・MOV・WebM・MKV です。"
    else:
        # ローカルファイルはコピーせずそのまま使う (5GB級のコピーを回避)
        ss.chosen = _describe_video(path, os.path.basename(path))


def _pick_file() -> None:
    picked = pick_video_file()
    if picked:
        st.session_state.local_path_input = picked
        _choose_path()


def _choose_another() -> None:
    st.session_state.pop("chosen", None)
    st.session_state.local_path_input = ""


def _start_analysis() -> None:
    ss = st.session_state
    lo, hi = ss.clip_slider
    ss.pending = {
        **ss.chosen,
        # 区間の指定欄は確認・書き出しの画面に出さないので、値をここで残しておく
        "clip_range": (abs(lo), abs(hi)),
        "use_reader_voice": ss.use_reader_voice,
        "identify_poems": ss.identify_poems,
    }


if st.session_state.state == 1 and "pending" not in st.session_state:
    ss = st.session_state
    st.html(stepper_html(1))
    col_main, col_flow = st.columns([1.7, 1], gap="large")
    with col_main:
        st.markdown("## 試合の動画を選んでください")
        st.write(
            "読みと取りの場面だけを残した短縮版を作ります。読まれた歌を自動で聞き分けるので、"
            "確かめるのは聞き分けられなかった場面だけです。"
        )
        chosen = ss.get("chosen")
        if chosen:
            with st.container(border=True):
                col_file, col_change = st.columns([4, 1.3], vertical_alignment="center")
                col_file.markdown(f"**{chosen['name']}**")
                col_file.markdown(f"長さ {_duration_text(chosen['duration'])}　大きさ {_size_text(chosen['size'])}")
                col_change.button("別の動画を選ぶ", on_click=_choose_another, width="stretch")
        else:
            col_path, col_pick = st.columns([4, 1.3], vertical_alignment="bottom")
            col_path.text_input(
                "動画ファイルの場所",
                key="local_path_input",
                placeholder="/Users/…/試合.MOV（パスを貼り付けて Enter）",
                on_change=_choose_path,
            )
            col_pick.button("ファイルを選択...", on_click=_pick_file, width="stretch")
            if ss.get("choose_error"):
                st.error(ss.choose_error)
            # key を世代管理し、コピー完了後に世代を進めることで
            # Streamlit がメモリ上に保持するアップロードデータを解放する
            uploaded_file = st.file_uploader(
                "またはアップロード（ほかの端末から開いているとき）",
                type=[ext.lstrip(".") for ext in SUPPORTED_EXTS],
                key=f"file_uploader_{ss.uploader_gen}",
            )
            if uploaded_file is not None:
                with st.spinner("動画を保存しています..."):
                    tmpdirname = tempfile.mkdtemp()
                    suffix = os.path.splitext(uploaded_file.name)[1]
                    copy_path = os.path.join(tmpdirname, f"input{suffix}")
                    # getbuffer() は全量を一度にRAMへ複製するため、チャンクで書き出す
                    uploaded_file.seek(0)
                    with open(copy_path, "wb") as f:
                        shutil.copyfileobj(uploaded_file, f, length=16 * 1024 * 1024)
                ss.chosen = _describe_video(copy_path, uploaded_file.name, tmpdirname)
                ss.uploader_gen += 1
                st.rerun()

        lo, hi = ss.get("clip_slider", (-1.5, 3.0))
        identify_on = ss.get("identify_poems", True)
        st.caption(
            f"上の句の{-lo}秒前から下の句の{hi}秒後までを残します。"
            f"読まれた歌の聞き分けは{'オン' if identify_on else 'オフ'}です。"
        )
        with st.expander("詳細設定"):
            slider_values = st.slider(
                "残す範囲（上の句の開始から何秒前、下の句の終了から何秒後）",
                -10.0, 10.0, (-1.5, 3.0), step=0.5, key="clip_slider",
            )
            if slider_values[0] >= 0:
                st.warning('下限は負の値にしてください。')
            if slider_values[1] <= 0:
                st.warning('上限は正の値にしてください。')
            if slider_values[0] == slider_values[1]:
                st.snow()
            if slider_values[0] == 0.0 and slider_values[1] == 0.0:
                st.balloons()

            # 既定オン: 歌が分かれば、特定できなかった候補だけを確かめれば済む (レビューの手間が大きく減る)
            st.checkbox(
                "読まれた歌を特定する",
                value=True,
                key="identify_poems",
                help=(
                    "候補ごとに、読手の読みを音声認識で書き起こし、百人一首のどの歌の上の句・下の句かを"
                    "確認画面に表示します。下の句の読み始めに付いた候補（取りの場面ではない候補）も分かります。\n\n"
                    f"初回だけ、音声認識のモデル（約{MODEL_DOWNLOAD_MB}MB）をダウンロードします。"
                    "音声はこのPCの中だけで処理し、外部には送りません。"
                    "1試合で1〜2分ほどかかります（PCの性能によってはそれ以上）。"
                ),
            )
            # 既定オフ: 読手の声の検出は空調などの雑音に強いが、読手が遠い・声が混ざるといった
            # 録音では声が取れず候補を落とし得る。静かな会場では従来の音量パターンで十分なので、
            # 雑音で誤候補が多いときだけ使う選択式にしている。
            # (session_state の初期化より後に置かないと、初回実行でウィジェットのキーが消される)
            st.checkbox(
                "読手の声で候補を絞る（雑音の多い動画向け）",
                value=False,
                key="use_reader_voice",
                help=(
                    "空調などの雑音が大きく、取りではない場面（札を払う音・札を並べる音だけの区間）が"
                    "候補に多く混ざるときにオンにしてください。\n\n"
                    "オンにすると、音声から読手の声を検出し、下の句の後に上の句が読み始められた位置だけを"
                    "候補にします。雑音に埋もれて見逃していた読み始めも拾えるようになります。\n\n"
                    "読手の声が小さく録れている動画では、かえって本物の取りを落とすことがあります。"
                    "静かな会場で撮った動画ではオフ（音量パターンのみで検出）のままで十分です。"
                ),
            )

        # 以前はパスを入れて Enter を押した時点で解析が始まり、始め方が分かりにくかった
        # (2026-09-23 の通しの操作で確認)。動画を選んでから、このボタンで始める。
        st.button("解析を始める", type="primary", disabled=not chosen, on_click=_start_analysis)
        url = "https://docs.google.com/presentation/d/1gG8EdmBDSkv82v8wLjVtbLoWbaBhAx5MWzBW1FoKmxg/edit?usp=sharing"
        st.markdown(f"[使い方・仕組み]({url})")
    with col_flow:
        st.html(FLOW_HTML)


if st.session_state.state == 1 and "pending" in st.session_state:
    ss = st.session_state
    job = ss.pending
    st.html(stepper_html(2))
    st.markdown("## 解析しています")
    st.caption(job["name"])
    rows = [st.empty() for _ in range(3 if job["identify_poems"] else 2)]
    rows[0].html(step_html("run", "音声を取り出す"))
    rows[1].html(step_html("todo", "読みの候補を探す"))
    if job["identify_poems"]:
        rows[2].html(step_html("todo", "読まれた歌を聞き分ける"))

    tmpdirname = job["tmpdir"] or tempfile.mkdtemp()
    audio_path = os.path.join(tmpdirname, "audio.wav")
    extract_audio(job["path"], audio_path)
    simplified_waveform_path = os.path.join(tmpdirname, "simplified.npy")
    simplify_waveform(audio_path, simplified_waveform_path)
    rows[0].html(step_html("done", "音声を取り出す", "完了"))

    rows[1].html(step_html("run", "読みの候補を探す"))
    waveform = np.load(simplified_waveform_path)
    voiced = compute_voiced_frames(audio_path) if job["use_reader_voice"] else None
    score_dict = return_candidates(waveform, voiced)
    sorted_scores = sorted(score_dict.items(), key=lambda x: x[0])
    rows[1].html(step_html("done", "読みの候補を探す", f"{len(sorted_scores)}件見つかりました"))

    readings = {}
    if job["identify_poems"]:
        with rows[2].container():
            st.html(step_html("run", "読まれた歌を聞き分ける"))
            readings = identify_candidate_poems(audio_path, tmpdirname, sorted_scores)

    del ss.pending
    ss.update({
        "tmpdir": tmpdirname,
        "input_video": job["path"],
        "source_name": job["name"],
        "source_duration": job["duration"],
        "audio_path": audio_path,
        "waveform": waveform,
        "sorted_scores": sorted_scores,
        "segment_enabled": {idx: True for idx, _ in sorted_scores},
        "preview_clips": {},
        "readings": readings,
        "reviewed": set(),
        "clip_range": job["clip_range"],
        "state": 2,
    })
    st.rerun()


# ---------------------------------------------------------------------------
# State 2: 確認
# ---------------------------------------------------------------------------

_QUEUE_STYLE = {
    "off": ("border: 1.5px dashed #8E8A6C; background: #F2F3EE; color: #4E4C3B;", "外した", "#595F55"),
    "kept": ("border: 2px solid #213A2F; background: #2A4436; color: #F2F3EE;", "残した", "#595F55"),
    "now": ("border: 2px solid #B8321F; outline: 2px solid #1F231E; outline-offset: 2px;"
            " background: #2A4436; color: #F2F3EE;", "確認中", "#1F231E"),
    "todo": ("border: 2px solid #B8321F; background: #2A4436; color: #F2F3EE;", "", "#595F55"),
}


def queue_html(steps: list[dict]) -> str:
    """「確かめる順番」を、確認の順番の札 (1〜N) と状態で並べた HTML。"""
    cells = []
    for step in steps:
        box, note, color = _QUEUE_STYLE[step["state"]]
        weight = "700" if step["state"] == "now" else "400"
        cells.append(
            f'<li style="display:flex;flex-direction:column;align-items:center;gap:4px;">'
            f'<span style="width:30px;height:42px;box-sizing:border-box;border-radius:2px;{box}'
            f'display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;">'
            f'{step["order"]}</span>'
            f'<span style="font-size:11px;color:{color};font-weight:{weight};">#{step["n"]} {note}</span></li>'
        )
    return (
        '<div style="font-size:13px;font-weight:700;color:#3E443B;margin-bottom:8px;">確かめる順番</div>'
        '<ol aria-label="確かめる順番" style="margin:0;padding:0;list-style:none;display:flex;'
        'flex-wrap:wrap;gap:10px 14px;font-variant-numeric:tabular-nums;">' + "".join(cells) + "</ol>"
    )


if st.session_state.state == 2:
    ss = st.session_state
    sorted_scores = ss.sorted_scores
    before, after = ss.clip_range
    waveform = ss.waveform
    readings = ss.readings
    total_count = len(sorted_scores)
    queue = review_queue(sorted_scores, readings)
    ss.setdefault("review_idx", queue[0] if queue else total_count)
    review_idx = ss.review_idx
    st.html(stepper_html(3))

    def move(step: int) -> None:
        target = neighbor(queue, ss.review_idx, step)
        if target is not None:
            ss.review_idx = target
        elif step > 0:
            ss.review_idx = total_count  # 最後の札より先へ進んだら、確かめ終わりの表示にする

    def decide(keep: bool) -> None:
        idx = sorted_scores[ss.review_idx][0]
        ss.segment_enabled[idx] = keep
        ss.reviewed.add(idx)
        move(+1)

    def jump() -> None:
        position = ss.card_strip.jump
        if position is not None:
            ss.review_idx = position

    enabled_set = {idx for idx, v in ss.segment_enabled.items() if v}
    enabled_count = len(enabled_set)
    segments = compute_segments(sorted_scores, enabled_set, before, after, len(waveform))
    est_sec = estimate_duration(segments)
    est_min = int(est_sec) // 60
    est_sec_remainder = est_sec - est_min * 60

    if not readings:
        # 全候補を通しで確かめる前提の言い方にしない。実際には、誤検知が少し混ざるのを承知で
        # そのまま短縮版を作ることが多い (ユーザーの指摘)
        st.markdown(f"## 読みの候補が{total_count}件見つかりました")
        st.caption("確かめなくても、そのまま短縮版を作れます。気になる場面があれば、札を押して再生し、外してください。")
    elif queue:
        st.markdown(f"## {len(queue)}件を確かめてください")
        st.caption(
            f"{total_count}件のうち{total_count - len(queue)}件は、読まれた歌が分かりました。"
            "裏向きの札を、左から順に確かめます。"
        )
    else:
        st.markdown("## すべての候補の歌が分かりました")

    card_strip(
        strip_items(sorted_scores, readings, ss.segment_enabled, ss.reviewed),
        current=review_idx if review_idx < total_count else -1,
        key="card_strip",
        on_jump=jump,
    )

    if review_idx < total_count:
        idx, score = sorted_scores[review_idx]
        reading = readings.get(idx)
        center = idx / 10.0
        seg_start = max(0.1, center - before)
        seg_end = min(center + after, len(waveform) / 10.0 - 0.1)

        col_main, col_side = st.columns([1.7, 1], gap="large")
        with col_main:
            # 映像はオンデマンド生成 (キャッシュあり)。巨大な元動画からの切り出しに数秒かかるので、
            # まずは抽出済みの音声をすぐ再生し、映像は求められたときに作る
            cache_key = (idx, before, after)
            if ss.get("video_preview_key") == cache_key and cache_key not in ss.preview_clips:
                with st.spinner("映像を用意しています..."):
                    clip_dir = os.path.join(ss.tmpdir, "previews")
                    os.makedirs(clip_dir, exist_ok=True)
                    clip_path = os.path.join(clip_dir, f"preview_{idx}.mp4")
                    extract_preview_clip(
                        input_video=ss.input_video,
                        center_sec=center,
                        before_sec=before,
                        after_sec=after,
                        output_path=clip_path,
                    )
                    ss.preview_clips[cache_key] = clip_path
            clip_path = ss.preview_clips.get(cache_key)
            if clip_path and os.path.exists(clip_path):
                st.video(clip_path, autoplay=True)
            else:
                with sf.SoundFile(ss.audio_path) as af:
                    sr = af.samplerate
                    start_frame = min(int(seg_start * sr), max(0, af.frames - 1))
                    n_frames = max(1, int((seg_end - seg_start) * sr))
                    af.seek(start_frame)
                    audio_clip = af.read(min(n_frames, af.frames - start_frame), dtype="float32")
                st.audio(audio_clip, sample_rate=sr, autoplay=True)
                if st.button("映像も見る"):
                    ss.video_preview_key = cache_key
                    st.rerun()
            if reading is not None and (reading.before_text or reading.after_text):
                st.caption(
                    f"聞き取った言葉　直前「{reading.before_text}」　直後「{reading.after_text}」"
                )
            auto_next = st.checkbox("自動で次の候補を再生する", value=False, key="auto_advance")
            auto_advance(auto_next, f"{review_idx}", NEXT_BUTTON_LABEL)

        with col_side:
            if readings and len(queue) <= 12:
                st.html(queue_html(queue_steps(queue, sorted_scores, ss.segment_enabled, ss.reviewed, review_idx)))
            elif review_idx in queue:
                st.caption(f"{queue.index(review_idx) + 1} / {len(queue)} 件目")
            st.markdown(f"**#{review_idx + 1}**　元動画 {format_time(center)}")
            text = (
                review_text(reading) if reading is not None
                else {"title": "読みの候補です", "detail": "", "recommend": None}
            )
            st.markdown(f"### {text['title']}")
            if text["detail"]:
                st.write(text["detail"])
            st.markdown("**この場面を短縮版に残しますか？**")
            col_remove, col_keep = st.columns(2)
            col_remove.button(
                "外す", shortcut="N", on_click=decide, args=(False,), width="stretch",
                type="primary" if text["recommend"] == "remove" else "secondary",
            )
            col_keep.button(
                "残す", shortcut="Y", on_click=decide, args=(True,), width="stretch",
                type="primary" if text["recommend"] == "keep" else "secondary",
            )
            col_prev, col_next = st.columns(2)
            col_prev.button(
                "前の件", shortcut="Left", on_click=move, args=(-1,), width="stretch",
                disabled=neighbor(queue, review_idx, -1) is None,
            )
            col_next.button(NEXT_BUTTON_LABEL, shortcut="Right", on_click=move, args=(+1,), width="stretch")
    elif not queue:
        st.info("確かめる札はありません。このまま短縮版を作れます。")
    elif all(sorted_scores[i][0] in ss.reviewed for i in queue):
        st.info("確かめ終わりました。短縮版を作れます。")
    else:
        left = sum(1 for i in queue if sorted_scores[i][0] not in ss.reviewed)
        st.info(f"まだ確かめていない札が{left}件あります。札を押すと、その候補に戻れます。")

    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        st.metric("残す場面", enabled_count)
        st.metric("短縮版の長さ", f"約{est_min}分{est_sec_remainder:.0f}秒")
        if st.button("短縮版を作る", type="primary", disabled=enabled_count == 0):
            ss.state = 3
            st.rerun()
    if enabled_count == 0:
        st.warning("残す場面がありません。札を押して、残す場面を選び直してください。")


# ---------------------------------------------------------------------------
# State 3: 動画編集
# ---------------------------------------------------------------------------

if st.session_state.state == 3:
    st.html(stepper_html(4))
    before, after = st.session_state.clip_range
    waveform = st.session_state.waveform
    sorted_scores = st.session_state.sorted_scores

    enabled_set = {
        idx for idx, v in st.session_state.segment_enabled.items() if v
    }
    segments = compute_segments(
        sorted_scores, enabled_set, before, after, len(waveform)
    )
    est_sec = estimate_duration(segments)
    est_min = int(est_sec) // 60
    est_sec_remainder = est_sec - est_min * 60
    output_video = os.path.join(st.session_state.tmpdir, "processed.mp4")

    # State 2 と同数の要素を描画してから処理開始することで、
    # ブロッキング中に旧 State 2 の UI が残るのを防ぐ
    st.info('動画を編集中...しばらくお待ちください。')
    col_m1, col_m2 = st.columns([1, 1])
    with col_m1:
        st.metric("対象区間", f"{len(segments)}")
    with col_m2:
        st.metric("推定出力", f"{est_min}分{est_sec_remainder:.0f}秒")
    progress = st.progress(0)

    cut_and_concat_mp4(
        input_video=st.session_state.input_video,
        segments=segments,
        output_video=output_video,
        source_name=st.session_state.source_name,
        progress_callback=lambda p: progress.progress(p),
    )

    with open(output_video, "rb") as f:
        st.session_state.processed_video = f.read()

    st.session_state.state = 4
    st.rerun()


# ---------------------------------------------------------------------------
# State 4: ダウンロード
# ---------------------------------------------------------------------------

def _mark_saved() -> None:
    st.session_state.saved = True


def _start_over() -> None:
    st.session_state.clear()


if st.session_state.state == 4:
    st.html(stepper_html(5))
    st.success('動画の編集が完了しました')
    # 保存しても画面は消さない。以前は押した時点でセッションを消していたので、
    # 保存に失敗したり保存先を間違えたりすると、作り直すしかなかった。
    st.download_button(
        "保存する",
        data=st.session_state.processed_video,
        file_name=shortened_file_name(st.session_state.source_name),
        mime="video/mp4",
        type="primary",
        on_click=_mark_saved,
    )
    st.button("別の動画を短縮する", on_click=_start_over)
