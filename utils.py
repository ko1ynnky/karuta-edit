import soundfile as sf
import matplotlib.pyplot as plt
import numpy as np
import os
from tqdm import tqdm
import time

from reader_voice import find_reading_onsets






# 直近６秒くらいにノイズがどれだけ少ないか？
def return_noise_scores(waveform):
    noise_scores4 = np.zeros_like(waveform)
    for i in range(40,len(waveform) - 1):
        noise_scores4[i] = 1 - waveform[i-40:i+1].max()
    noise_scores6 = np.zeros_like(waveform)
    for i in range(60, len(waveform) - 1):
        noise_scores6[i] = 1 - waveform[i-60:i+1].max()
    return np.mean([noise_scores4, noise_scores6], axis=0)


# 直近0.8秒がどれだけ静かか？
def return_interval_scores(waveform):
    interval_scores = np.zeros_like(waveform)
    for i in range(8, len(waveform) - 1):
        interval_scores[i] = (1 - waveform[i-8:i+1].max())**5
    return interval_scores


# 前数秒の音量は大きいか？
def return_before_scores(waveform):
    before_scores = np.zeros_like(waveform)
    for i in range(70):
        before_scores[i] = 0.5
    for i in range(70, len(waveform) - 1):
        before_scores[i] = (np.sum(waveform[i-70:i-20] > waveform[i-8:i+1].max())*1.0/50.0)**5
    return before_scores


# 後数秒の音量は大きいか？
def return_after_scores(waveform):
    after_scores = np.zeros_like(waveform)
    for i in range(8, len(waveform) - 35):
        after_scores[i] = (np.sum(waveform[i+5:i+35] > waveform[i-8:i+1].max())*1.0/30.0)**5
    for i in range(len(waveform) - 35, len(waveform)):
        after_scores[i] = 0.5
    return after_scores


# 全部の要素を統合
def return_raw_scores(waveform):
    noise_scores = return_noise_scores(waveform)
    interval_scores = return_interval_scores(waveform)
    before_scores = return_before_scores(waveform)
    after_scores = return_after_scores(waveform)
    raw_scores = interval_scores*before_scores*after_scores*noise_scores
    return raw_scores


# どの３秒間にもピークが一つだけあるように調整
def return_processed_scores(waveform):
    raw_scores = return_raw_scores(waveform)
    scores = np.zeros(len(raw_scores), dtype=int)
    score_dict = {}
    for i in range(len(raw_scores)):
        if raw_scores[i] == raw_scores[30*(i//30):30*(i//30 + 1)].max() and raw_scores[i] > 0.0001:
            score_dict[i] = int(raw_scores[i]*10000)
            scores[i] = int(raw_scores[i]*10000)

    sorted_scores_list = sorted(score_dict.items(), key=lambda x: x[0])

    for idx, val in sorted_scores_list:
        if scores[idx] == scores[idx + 1]:
            scores[idx] = 0
            del score_dict[idx]
        elif val < scores[(max(0, idx - 30)):min(len(scores), idx + 31)].max():
            scores[idx] = 0
            del score_dict[idx]

    return scores, score_dict
    

# 上位n個を抽出した上で、medianとmaxを考慮して低いスコアについてチェック
def return_top_scores(waveform, top_n=125):
    scores, score_dict = return_processed_scores(waveform)
    noise_scores = return_noise_scores(waveform)
    score_dict = dict(sorted(score_dict.items(), key=lambda x: x[1], reverse=True)[:top_n])
    good_noise_score = np.mean([noise_scores[idx] for idx in list(score_dict.keys())[:25]])
    median_val = np.median(list(score_dict.values()))
    max_val = np.max(list(score_dict.values()))
    for i in range(len(scores)):
        if i not in score_dict:
            scores[i] = 0
        elif score_dict[i] < 2*median_val - max_val:
            if score_dict[i] < 11*median_val - 10*max_val:
                scores[i] = 0
                del score_dict[i]
            elif noise_scores[i] < good_noise_score * 0.75:
                scores[i] = 0
                del score_dict[i]
            else:
                for j in range(max(0, i - 150), min(len(scores), i + 151)):
                    if j != i and j in score_dict and score_dict[j] > score_dict[i]:
                        scores[i] = 0
                        del score_dict[i]
                        break
            
    return scores, score_dict


# 読手の声から見つけた上の句開始を候補とし、振幅スコアは位置合わせと表示用スコアに使う
# 振幅スコアの候補は雑音の多い録音では読みの途中や下の句の開始にも付くため、
# 声の立ち上がりに一致しないものは採用しない。声の立ち上がりが一つも取れない
# 録音 (読手が遠いなど) では従来どおり振幅スコアの候補をそのまま返す。
def return_candidates(waveform, voiced, top_n=125, merge_frames=15):
    _, legacy = return_top_scores(waveform, top_n=top_n)
    if voiced is None:
        return legacy
    onsets = find_reading_onsets(voiced)
    if not onsets:
        return legacy
    raw_scores = return_raw_scores(waveform)
    candidates = {}
    for onset in onsets:
        near = [idx for idx in legacy if abs(idx - onset) <= merge_frames]
        if near:
            idx = min(near, key=lambda i: abs(i - onset))
            candidates[idx] = legacy[idx]
        else:
            nearby = raw_scores[max(0, onset - 5):onset + 6]
            candidates[onset] = max(1, int(nearby.max() * 10000)) if len(nearby) else 1
    return dict(sorted(candidates.items()))


















def prepare_waveform(path, index):
    files = f"simplified_audio{index + 1}.npy"
    print("processing file:",files)

    wav_path = os.path.join(path, files)
    waveform = np.load(wav_path)

    return waveform











def plot_score_distribution(path, output_path="score_distribution.png"):
    start_time = time.time()
    fig, ax = plt.subplots(4,3, figsize = (40, 30), sharex=True, sharey=True)

    for i in range(12):
        waveform = prepare_waveform(path, i)
        #_, score_dict = return_processed_scores(waveform)
        _, score_dict = return_top_scores(waveform, top_n=125)
        print(len(score_dict))

        score_vals = np.array(list(score_dict.values()))

        print("mean score:", np.mean(score_vals))
        print("std score:", np.std(score_vals))

        print(f"Score Calculated. Time: {time.time() - start_time} seconds.")

        ax[i//3, i%3].hist(score_vals[score_vals > 0], bins=50, cumulative = True, color='blue', alpha=0.7)
        ax[i//3, i%3].set_title(f'File {i+5} Score Distribution')
        ax[i//3, i%3].set_xlabel('Score')
        ax[i//3, i%3].set_ylabel('Frequency')

        print()

    fig.savefig(output_path)
    print(f"Saved score distribution figure to {output_path}")
    print(f"Plotting Over. Time: {time.time() - start_time} seconds.")

    








def plot_waves(path, output_path="waveform.png"):
    start_time = time.time()
    left = 0
    right = 6000
    if left == 0 and right == -1:
        figure_width = 800
    else:
        figure_width = (right - left) // 50

    fig, ax = plt.subplots(12,1, figsize = (figure_width, 30), sharex=True, sharey=True)

    for i in range(12):
        waveform = prepare_waveform(path, i)
        
        noise_scores = return_noise_scores(waveform)
        interval_scores = return_interval_scores(waveform)
        before_scores = return_before_scores(waveform)
        after_scores = return_after_scores(waveform)
        raw_scores = return_raw_scores(waveform)
        #scores, _ = return_processed_scores(waveform)
        scores, _ = return_top_scores(waveform)

        print(f"Score Calculated. Time: {time.time() - start_time} seconds.")

        waveform = waveform[left:right]
        noise_scores = noise_scores[left:right]
        interval_scores = interval_scores[left:right]
        before_scores = before_scores[left:right]
        after_scores = after_scores[left:right]
        raw_scores = raw_scores[left:right]
        scores = scores[left:right]

       
        # 時間軸
        duration = len(waveform) / 10
        time_x = np.linspace(0, duration, len(waveform))
        
        ax[i].plot(time_x, noise_scores, color='tab:green', linewidth=2) 
        ax[i].plot(time_x, interval_scores, color='tab:red', linewidth=2)
        ax[i].plot(time_x, before_scores, color='tab:orange', linewidth=2)
        ax[i].plot(time_x, after_scores, color='tab:pink', linewidth=2)
        ax[i].plot(time_x, scores/10000, color='black', linewidth=8)
        ax[i].plot(time_x, waveform, color='blue', linewidth=4)
        ax[i].set_xticks(np.arange(0, duration + 1, 5.0))
        ax[i].grid(True)
        print()

    print(f"Plotting Over. Time: {time.time() - start_time} seconds.")

    # 保存
    fig.savefig(output_path)
    print(f"Saved waveform figure to {output_path}")


if __name__ == "__main__":
    plot_score_distribution("simplified_waveforms")
    plot_waves("simplified_waveforms")
