import streamlit as st
import streamlit.components.v1 as components
import numpy as np
import importlib.util
import shutil
import soundfile as sf
import subprocess
import sys
import tempfile
import os
from utils import return_top_scores
from offline_app import (
    extract_audio,
    simplify_waveform,
    cut_and_concat_mp4,
    extract_preview_clip,
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


_AUTO_ADVANCE_SCRIPT = """
<script>
(function () {
  const doc = window.parent.document;
  const enabled = __ENABLED__;
  const marker = "__MARKER__";
  const nextLabel = "__NEXT_LABEL__";

  // 停止指示は発火時に読み直す。iframe が作り直されても
  // 親要素に残ったリスナーが古い設定のまま動くのを防ぐため。
  doc.documentElement.dataset.karutaAutoAdvance = enabled ? "on" : "off";
  if (!enabled) return;

  function advance() {
    if (doc.documentElement.dataset.karutaAutoAdvance !== "on") return;
    const btn = Array.from(doc.querySelectorAll("button")).find(
      (b) => b.innerText.trim() === nextLabel
    );
    if (btn) btn.click();
  }

  function bind() {
    const media = doc.querySelector("video") || doc.querySelector("audio");
    if (!media) return false;
    if (media.dataset.karutaAdvanceMarker === marker) return true;
    media.dataset.karutaAdvanceMarker = marker;
    media.addEventListener("ended", advance, { once: true });
    return true;
  }

  // Streamlit はメディア要素を段階的に描画するため、現れるまで待つ
  if (!bind()) {
    let tries = 0;
    const timer = setInterval(function () {
      if (bind() || ++tries > 50) clearInterval(timer);
    }, 100);
  }
})();
</script>
"""

NEXT_BUTTON_LABEL = "スキップ →"


def render_auto_advance(enabled: bool, marker: str) -> None:
    """再生終了で次の候補へ進むスクリプトを親ドキュメントへ仕込む。

    Streamlit は再生終了を Python 側へ通知しないため、親ドキュメントの
    <video>/<audio> の ended を直接購読する。区間長ぶん time.sleep して
    rerun する方式は採らない。待機中はボタンが押せず、その候補に対する
    はい/いいえの判定ができなくなるため。
    """
    html = (
        _AUTO_ADVANCE_SCRIPT
        .replace("__ENABLED__", "true" if enabled else "false")
        .replace("__MARKER__", marker)
        .replace("__NEXT_LABEL__", NEXT_BUTTON_LABEL)
    )
    components.html(html, height=0)


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
# 共通 UI
# ---------------------------------------------------------------------------

url = "https://docs.google.com/presentation/d/1gG8EdmBDSkv82v8wLjVtbLoWbaBhAx5MWzBW1FoKmxg/edit?usp=sharing"
st.write(f'[使い方・仕組み]({url})')

slider_values = st.slider('区間を指定：', -10.0, 10.0, (-1.5, 3.0), step=0.5)
if slider_values[0] >= 0:
    st.warning('下限は負の値にしてください。')
if slider_values[1] <= 0:
    st.warning('上限は正の値にしてください。')
if slider_values[0] == slider_values[1]:
    st.snow()
if slider_values[0] == 0.0 and slider_values[1] == 0.0:
    st.balloons()
if slider_values[0] < 0 and slider_values[1] > 0:
    st.success(
        f'上の句の開始時点の{-slider_values[0]}秒前から、'
        f'下の句の終了時点の{slider_values[1]}秒後までを残します。'
    )

if 'state' not in st.session_state:
    st.session_state.state = 1
    for key in list(st.session_state.keys()):
        if key != "state":
            del st.session_state[key]

st.session_state.setdefault("uploader_gen", 0)

SUPPORTED_EXTS = [".mp4", ".mov", ".webm", ".mkv"]

# file uploader
# key を世代管理し、コピー完了後に世代を進めることで
# Streamlit がメモリ上に保持するアップロードデータを解放する
uploaded_file = st.file_uploader(
    "動画をアップロード：",
    type=[ext.lstrip(".") for ext in SUPPORTED_EXTS],
    key=f"file_uploader_{st.session_state.uploader_gen}",
)
# ダイアログで選択したパスをwidget keyへ事前反映
# (widget描画後のsession_state書き込みはエラーになるため)
if "_picked_path" in st.session_state:
    st.session_state["local_path_input"] = st.session_state.pop("_picked_path")

col_path, col_pick = st.columns([4, 1], vertical_alignment="bottom")
with col_path:
    local_path_input = st.text_input(
        "またはローカル動画ファイルのパスを指定：",
        key="local_path_input",
        placeholder="/path/to/video.mp4 （数GBの大きい動画はアップロードよりこちらが高速です）",
    )
with col_pick:
    if st.button("ファイルを選択..."):
        picked = pick_video_file()
        if picked:
            st.session_state._picked_path = picked
            st.rerun()


# ---------------------------------------------------------------------------
# State 1: 分析
# ---------------------------------------------------------------------------

if st.session_state.state == 1:
    input_video_path = None
    tmpdirname = None

    if uploaded_file is not None:
        suffix = os.path.splitext(uploaded_file.name)[1]
        if suffix.lower() not in SUPPORTED_EXTS:
            st.error("対応している動画形式はMP4、MOV、WebMまたはMKVのみです。")
            st.stop()
        st.status('動画を保存中...しばらくお待ちください。')
        tmpdirname = tempfile.mkdtemp()
        input_video_path = os.path.join(tmpdirname, f"input{suffix}")
        # getbuffer() は全量を一度にRAMへ複製するため、チャンクで書き出す
        uploaded_file.seek(0)
        with open(input_video_path, "wb") as f:
            shutil.copyfileobj(uploaded_file, f, length=16 * 1024 * 1024)
        st.session_state.uploader_gen += 1
    else:
        local_path = local_path_input.strip().strip("'\"")
        if local_path:
            local_path = os.path.expanduser(local_path)
            if not os.path.isfile(local_path):
                st.error("指定されたパスにファイルが見つかりません。")
            elif os.path.splitext(local_path)[1].lower() not in SUPPORTED_EXTS:
                st.error("対応している動画形式はMP4、MOV、WebMまたはMKVのみです。")
            else:
                # ローカルファイルはコピーせずそのまま使う (5GB級のコピーを回避)
                input_video_path = local_path

if st.session_state.state == 1 and input_video_path is not None:
    st.status('動画を分析中...しばらくお待ちください。')
    if tmpdirname is None:
        tmpdirname = tempfile.mkdtemp()

    audio_path = os.path.join(tmpdirname, "audio.wav")
    extract_audio(input_video_path, audio_path)

    simplified_waveform_path = os.path.join(tmpdirname, "simplified.npy")
    simplify_waveform(audio_path, simplified_waveform_path)

    waveform = np.load(simplified_waveform_path)
    _, score_dict = return_top_scores(waveform)
    sorted_scores = sorted(score_dict.items(), key=lambda x: x[0])

    st.session_state.update({
        "tmpdir": tmpdirname,
        "input_video": input_video_path,
        "audio_path": audio_path,
        "waveform": waveform,
        "sorted_scores": sorted_scores,
        "segment_enabled": {idx: True for idx, _ in sorted_scores},
        "preview_clips": {},
        "review_idx": 0,
        "state": 2,
    })
    st.rerun()


# ---------------------------------------------------------------------------
# State 2: 順次レビュー
# ---------------------------------------------------------------------------

if st.session_state.state == 2:
    sorted_scores = st.session_state.sorted_scores
    before = abs(slider_values[0])
    after = abs(slider_values[1])
    waveform = st.session_state.waveform
    total_count = len(sorted_scores)
    review_idx = st.session_state.review_idx

    # ボタン起因の変更をチェックボックスwidget keyに事前反映
    # (widget描画前でないとsession_stateへの書き込みがエラーになるため)
    if st.session_state.get("_sync_checkboxes"):
        for s_idx in st.session_state.segment_enabled:
            st.session_state[f"sb_cb_{s_idx}"] = st.session_state.segment_enabled[s_idx]
        del st.session_state._sync_checkboxes

    # 有効セットと推定時間
    enabled_set = {
        idx for idx, v in st.session_state.segment_enabled.items() if v
    }
    enabled_count = len(enabled_set)

    segments = compute_segments(
        sorted_scores, enabled_set, before, after, len(waveform)
    )
    est_sec = estimate_duration(segments)
    est_min = int(est_sec) // 60
    est_sec_remainder = est_sec - est_min * 60

    # --- サイドバー: メトリクス・一括操作・全シーン一覧 ---
    with st.sidebar:
        st.metric("選択区間", f"{enabled_count}/{total_count}")
        st.metric("推定出力", f"{est_min}分{est_sec_remainder:.0f}秒")

        col_all, col_none = st.columns(2)
        with col_all:
            if st.button("全選択"):
                for key in st.session_state.segment_enabled:
                    st.session_state.segment_enabled[key] = True
                st.session_state._sync_checkboxes = True
                st.rerun()
        with col_none:
            if st.button("全解除"):
                for key in st.session_state.segment_enabled:
                    st.session_state.segment_enabled[key] = False
                st.session_state._sync_checkboxes = True
                st.rerun()

        scene_container = st.container(height=600)
        with scene_container:
            for i, (s_idx, s_score) in enumerate(sorted_scores):
                s_center = s_idx / 10.0
                s_start = max(0.1, s_center - before)
                col_cb, col_btn = st.columns([1, 4])
                with col_cb:
                    new_val = st.checkbox(
                        f"{i}",
                        value=st.session_state.segment_enabled[s_idx],
                        key=f"sb_cb_{s_idx}",
                        label_visibility="collapsed",
                    )
                    if new_val != st.session_state.segment_enabled[s_idx]:
                        st.session_state.segment_enabled[s_idx] = new_val
                        st.rerun()
                with col_btn:
                    prefix = ">> " if i == review_idx else ""
                    label = f"{prefix}#{i + 1} {format_time(s_start)} {s_score / 100:.2f}"
                    if st.button(label, key=f"sb_jump_{s_idx}"):
                        st.session_state.review_idx = i
                        st.rerun()

    # --- メインエリア ---
    if review_idx < total_count:
        # 現在の区間
        idx, score = sorted_scores[review_idx]
        center = idx / 10.0
        seg_start = max(0.1, center - before)
        seg_end = min(center + after, len(waveform) / 10.0 - 0.1)

        # 区間情報
        st.markdown(
            f"**#{review_idx + 1}** &nbsp; "
            f"[{format_time(seg_start)} - {format_time(seg_end)}] &nbsp; "
            f"Score: {score / 100}"
        )

        auto_advance = st.checkbox(
            "自動で次の候補を再生する",
            value=True,
            key="auto_advance",
        )

        # 音声プレビュー: 抽出済みWAVをスライス再生する
        # (巨大な元動画からの再エンコードを避け、即座に確認できる)
        with sf.SoundFile(st.session_state.audio_path) as af:
            sr = af.samplerate
            start_frame = min(int(seg_start * sr), max(0, af.frames - 1))
            n_frames = max(1, int((seg_end - seg_start) * sr))
            af.seek(start_frame)
            audio_clip = af.read(
                min(n_frames, af.frames - start_frame), dtype="float32"
            )
        st.audio(audio_clip, sample_rate=sr, autoplay=True)

        # 映像はオンデマンド生成 (キャッシュあり)
        cache_key = (idx, before, after)
        if st.session_state.get("video_preview_key") == cache_key:
            if cache_key not in st.session_state.preview_clips:
                with st.spinner("プレビュー生成中..."):
                    clip_dir = os.path.join(st.session_state.tmpdir, "previews")
                    os.makedirs(clip_dir, exist_ok=True)
                    clip_path = os.path.join(clip_dir, f"preview_{idx}.mp4")
                    extract_preview_clip(
                        input_video=st.session_state.input_video,
                        center_sec=center,
                        before_sec=before,
                        after_sec=after,
                        output_path=clip_path,
                    )
                    st.session_state.preview_clips[cache_key] = clip_path

            clip_path = st.session_state.preview_clips[cache_key]
            if clip_path and os.path.exists(clip_path):
                st.video(clip_path, autoplay=True)
        else:
            if st.button("映像で確認"):
                st.session_state.video_preview_key = cache_key
                st.rerun()

        render_auto_advance(auto_advance, f"{review_idx}")

        # 操作ボタン
        col_back, col_yes, col_no, col_skip = st.columns(4)
        with col_back:
            if st.button("← 戻る", disabled=(review_idx == 0)):
                st.session_state.review_idx -= 1
                st.rerun()
        with col_yes:
            if st.button("はい"):
                st.session_state.segment_enabled[idx] = True
                st.session_state._sync_checkboxes = True
                st.session_state.review_idx += 1
                st.rerun()
        with col_no:
            if st.button("いいえ"):
                st.session_state.segment_enabled[idx] = False
                st.session_state._sync_checkboxes = True
                st.session_state.review_idx += 1
                st.rerun()
        with col_skip:
            if st.button(NEXT_BUTTON_LABEL):
                st.session_state.review_idx += 1
                st.rerun()
    else:
        st.info("全区間の確認が完了しました。")

    # 0件警告 & 編集遷移ボタン
    if enabled_count == 0:
        st.warning("有効な区間がありません。「← 戻る」で区間を選択し直してください。")

    if st.button("確認を終了して編集へ", disabled=(enabled_count == 0)):
        st.session_state.state = 3
        st.rerun()


# ---------------------------------------------------------------------------
# State 3: 動画編集
# ---------------------------------------------------------------------------

if st.session_state.state == 3:
    before = abs(slider_values[0])
    after = abs(slider_values[1])
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
        progress_callback=lambda p: progress.progress(p),
    )

    with open(output_video, "rb") as f:
        st.session_state.processed_video = f.read()

    st.session_state.state = 4
    st.rerun()


# ---------------------------------------------------------------------------
# State 4: ダウンロード
# ---------------------------------------------------------------------------

if st.session_state.state == 4:
    st.success('動画の編集が完了しました')
    st.download_button(
        "ダウンロード",
        data=st.session_state.processed_video,
        file_name="processed_video.mp4",
        mime="video/mp4",
        on_click=lambda: st.session_state.clear(),
    )
