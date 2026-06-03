---
title: "Chapter 5: DPO — 嗜好データを使った LLM ファインチューニング"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter02_dpo/)

500M パラメータの LLM を Direct Preference Optimization (DPO) でアラインメントし、過度な迎合 (sycophancy) を抑える。報酬モデル不要で実装可能な現代 RL の代表例。

## 学習目標

- DPO が「報酬モデル不要」になる数学的トリックを理解する
- LLM の 3 段階訓練 (pre-train → SFT → alignment) のうち、各段階で何が獲得されるかを把握する
- TRL ライブラリで DPO 訓練ループを実装する
- 4 つの指標 (loss / reward margin / reward accuracy / chosen vs rejected) を同時に読む

## 前提

- venv: `chapter02_dpo`
- ハードウェア: GPU 必須。500M モデルなので 96 GB は余裕 (実質 12 GB あれば動く)
- ディスク: モデルダウンロード約 2 GB、データ生成と DPO 出力で +1 GB

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter02_dpo/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter02_dpo

# Hugging Face cache を EFS に固定
export HF_HOME=/home/coder/.cache/huggingface
mkdir -p $HF_HOME
```

## DPO の核心 (なぜ報酬モデルが要らないか)

従来の RLHF は次の 4 段階:
1. SFT で会話形式を学習
2. 嗜好データから別の **報酬モデル** を学習
3. 報酬モデルを使って PPO で方策を更新
4. KL 正則化で reference 方策から離れすぎないよう抑制

DPO の数学的洞察: ある応答の **暗黙の報酬は、現方策の対数確率と reference モデルの対数確率の比例関係** で表現できる。つまり報酬モデルを別途訓練せず、**方策ロス関数に直接埋め込める**。

メモリ要件: RLHF は 4 モデル (policy + reference + reward + value) → DPO は 2 モデル (policy + reference) だけ。**実装も訓練も半分以下のコスト**。

## LLM 訓練の 3 段階

| 段階 | 入力データ | 獲得能力 |
|---|---|---|
| **Pre-training** | ラベルなし大規模テキスト (next-token 予測) | 知識、ただし会話できない |
| **SFT** | 質問-回答ペア | 会話形式、ただし「悪い回答」を避ける感覚なし |
| **Alignment (DPO/RLHF)** | 嗜好データ (preferred vs rejected) | 質に対するセンス、好ましくない回答を避ける |

SFT は「正解例だけ」で訓練するので、何を避けるべきか教えられない。DPO の **明示的な negative 信号** (rejected 応答) が善悪の境界を学習可能にする。

## デモ: 過度な迎合を抑える

ベースモデルは「数学なんて役に立たない」と言うとそのまま同意する傾向がある (sycophancy)。DPO で「敬意を持って反論する」応答を preferred、「無批判に同意する」応答を rejected として学習させる。

### 4 ステップ

#### Step 0. ベースモデルのダウンロード

```bash
python 0-download_model.py
```

500M パラメータモデル (Qwen2.5-0.5B など) を `$HF_HOME` にダウンロード。

#### Step 1. 嗜好データの生成

```bash
python 1-generate_data.py
```

100 件の (prompt, chosen, rejected) トリプルを生成。例:

```
prompt:   "数学なんて社会に出たら役に立たないよね"
chosen:   "確かに直接使う場面は少ないですが、論理的思考力という形で..."
rejected: "そうですね、数学は実用性が低いですよね"
```

#### Step 2. ベースラインの評価

```bash
python 2-test_before.py
```

DPO 前のモデルが迎合的な応答をしていることを確認。

#### Step 3. DPO 訓練

```bash
python 3-train_dpo.py
```

TRL の `DPOTrainer` で約 5-10 分。

#### Step 4. 訓練後の評価

```bash
python 4-test_after.py
```

訓練後のモデルが反論する応答を返すことを確認。

## 重要な指標 (4 つを同時に読む)

| 指標 | 期待される挙動 |
|---|---|
| **Training Loss** | `ln(2) ≈ 0.69` から開始、緩やかに減少。極端な急降下は過学習 |
| **Reward Margin** | 0 から増加し、安定する。preferred と rejected の信頼度ギャップ |
| **Reward Accuracy** | 0.5 (ランダム) から 0.8-0.95 へ。preferred に高い暗黙報酬を与えた割合 |
| **Chosen / Rejected Rewards** | chosen 増加、rejected 減少。両方一緒に減るのは要注意 |

### よくある誤読

- **Accuracy 高いが Margin 低い**: 信頼度が弱い (差はわずか)。学習不足
- **Chosen も Rejected も減少**: モデルが全体的に出力確率を絞っているだけで、嗜好を学習していない
- **Loss だけ見る**: 4 指標を同時に見ないと、表面的な収束で実は失敗していることがある

## 訓練曲線の読み方

正常な学習では 4 指標が **同期して改善** する:
- Loss ↓
- Reward Margin ↑
- Reward Accuracy ↑
- Chosen reward ↑、Rejected reward ↓

異常パターン:
- Loss は減っているのに Margin が伸びない → 確率を絞っているだけ
- 訓練の最初から Accuracy が高い → データが簡単すぎる
- Margin が頭打ち → β が大きすぎて KL 制約が強い

## β ハイパーパラメータ

`β` は KL 正則化の強さ。reference モデルからどれだけ離れていいかを制御する。

- 小さい β (例 0.01): 自由に逸脱、リスク高
- 大きい β (例 0.5): reference に近いまま、変化が小さい
- 推奨初期値: 0.1〜0.3

## 落とし穴: garbage in, garbage out

データ品質がアラインメント品質を決める。指標が健全に見えても、データが偏っていれば浅いパターンしか学習しない。

チェックすべき点:
- 嗜好の **一貫性**: 同じ基準で chosen / rejected が選ばれているか
- **難易度**: chosen と rejected の差が大きすぎても小さすぎてもダメ
- **エッジケース**: 訓練データが網羅的か

## 次のステップ

- **[Chapter 11: RLHF](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/11-rlhf)**: 本格的な RLHF パイプライン (SFT + 報酬モデル + PPO) で DPO との比較
- **[Chapter 12: アラインメント](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/12-alignment)**: DPO のバリエーション (IPO, KTO, ORPO 等)
- **[Chapter 14: GRPO/RLVR](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/14-grpo-rlvr)**: 報酬モデル不要のもう一つの流派、検証可能な報酬

## 補足: 第 2 章のメッセージ

DPO は「強化学習を直接適用するのが難しい LLM ドメインで、嗜好データから直接学習できる」現代的な代替手段。

ただしロスが綺麗に下がっても、データ品質が悪ければ **アラインメントも浅い**。「指標が健全に見える ≠ モデルが意図通り学習している」が本章のもう一つのメッセージ。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, transformers 4.57.3, trl 0.24.0, datasets 4.4.1, accelerate 1.10.1) | OK |
| `0-download_model.py` 立ち上がり | OK (180 秒以上のモデルダウンロードあり) |
| `1-generate_data.py` 完走 | OK |
| `2-test_before.py` / `3-train_dpo.py` / `4-test_after.py` syntax | OK |

初回はモデルダウンロードで 5-10 分。`HF_HOME=/home/coder/.cache/huggingface` を設定して EFS にキャッシュさせると 2 回目以降速い。
