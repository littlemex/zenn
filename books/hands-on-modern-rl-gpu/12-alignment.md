---
title: "Chapter 12: アラインメント手法 (DPO ファミリー)"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter09_alignment/)

DPO の数学的導出を深掘りし、KTO / SimPO / IPO といった派生手法の使い分けを学ぶ章。RLHF の **段階的簡素化** の流れを把握する。

## 学習目標

- KL 正則化付き RLHF 目的関数から DPO ロスを 3 ステップで導出できる
- DPO ファミリー (KTO / SimPO / IPO) のそれぞれの解決対象を理解する
- 業界の post-training パイプライン (SFT → DPO → GRPO/RLVR) を設計できる
- DPO のハイパラ (β, learning rate) を適切に調整できる

## 前提

- venv: `chapter09_alignment`
- ハードウェア: GPU 必須。0.5B モデルでも 8-12 GB VRAM 必要
- 第 2 章 (DPO 入門) を読み終えていることが望ましい

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter09_alignment/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter09_alignment
```

## RLHF 簡素化の系譜

各手法が異なるコンポーネントを除去:

| 手法 | 除去するもの | 残るもの |
|---|---|---|
| **PPO-RLHF** | (基準点) | Actor + Critic + Reference + RM の 4 モデル |
| **DPO** | Reward Model | 2 モデル (policy + reference) |
| **GRPO** | Critic | Actor + Reference (group 内 normalize で baseline 代用) |
| **RLVR** | RM の人手アノテーション | ルールベース検証で報酬 |
| **SimPO** | Reference まで除去 | 1 モデルのみ (長さ正規化) |

## DPO 数学的導出 (3 ステップ)

### Step 1: 最適方策の閉形式

KL 正則化付き RLHF 目的関数:

$$
\max_{\pi_\theta} \mathbb{E}_{x \sim D, y \sim \pi_\theta(\cdot|x)} [r(x, y)] - \beta \cdot KL(\pi_\theta || \pi_{ref})
$$

ラグランジュ未定乗数法で最適方策が得られる:

$$
\pi^*(y | x) \propto \pi_{ref}(y | x) \exp\big(r(x, y) / \beta\big)
$$

### Step 2: 暗黙の報酬

逆解きすると、**任意の方策が暗黙の報酬関数を表す**:

$$
r(x, y) = \beta \log \frac{\pi_\theta(y | x)}{\pi_{ref}(y | x)} + Z(x)
$$

分配関数 `Z(x)` は **pairwise 比較で消える** (差を取ると相殺)。

### Step 3: Bradley-Terry に代入

$$
P(y_w > y_l | x) = \sigma(r_w - r_l)
$$

に Step 2 の `r` を代入:

$$
L_{DPO} = -\mathbb{E}\big[\log \sigma(\beta(\log \frac{\pi_\theta(y_w|x)}{\pi_{ref}(y_w|x)} - \log \frac{\pi_\theta(y_l|x)}{\pi_{ref}(y_l|x)}))\big]
$$

報酬モデルを **訓練することなく**、嗜好データから直接方策を最適化できる。

## DPO vs PPO トレードオフ

| 項目 | DPO | PPO |
|---|---|---|
| モデル数 | 2 | 4 |
| メモリ | 〜2× SFT サイズ | 〜4× SFT サイズ |
| データ | オフライン preference pair | オンライン生成 |
| 安定性 | 高 (固定データ) | 低 (non-stationary) |
| 柔軟性 | データ分布に縛られる | 探索により分布外も改善可能 |

**DPO** はシンプルで安定、**PPO** はデータ分布を超えて改善できる。

## DPO ファミリー

| 手法 | 解決する課題 | 適用場面 |
|---|---|---|
| **DPO** (標準) | 標準の preference pair | 通常のアラインメント |
| **KTO** (Kahneman-Tversky) | pair 不要、👍/👎 単独信号 | ユーザーフィードバック (like/dislike) |
| **SimPO** | Reference モデルすら除去、長さ正規化 | メモリ制約環境 |
| **IPO** (Identity Pref. Opt.) | log-sigmoid を L2 正則化に置換 | 小データセット (DPO が overfit する場合) |

### 選択ガイド

```
標準の preference pair → DPO
👍/👎 のみ → KTO
メモリ厳しい → SimPO
データ少ない (< 10K) → IPO
```

## 業界の post-training パイプライン

実プロダクションは単一手法ではなく **直列に組み合わせる**:

```
Stage 1: SFT       (形式と基本タスク)
Stage 2: DPO/RLHF  (嗜好アラインメント)
Stage 3: GRPO/RLVR (推論能力強化)
Stage 4: Iterative (新モデルでデータ再収集)
```

各段階で異なる能力ギャップを解消:
- SFT → 形式の獲得
- DPO → 嗜好の判断
- GRPO/RLVR → 推論の正確性

## ハンズオン: DPO 訓練

### ステップ詳細 (第 2 章より深い)

1. preference データの生成 or 読み込み
2. ベースモデル + 凍結 reference を準備
3. chosen / rejected の log-prob を policy と reference の両方で計算
4. DPO ロス計算: `-log σ(β(log_ratio_chosen - log_ratio_rejected))`
5. **policy のみ** 勾配更新
6. 4 指標を **同時** モニタ: reward margin / accuracy / chosen/rejected reward 個別

### 重要ハイパラ

| パラメータ | 推奨範囲 | 効果 |
|---|---|---|
| `β` | 0.1-0.5 | KL ペナルティ強度。低いと reference から離れやすい |
| `learning rate` | 1e-6 〜 5e-6 | LLM ファインチューニング標準 |
| `max_seq_length` | chosen + rejected を収容できる長さ | OOM 注意 |

## 実行スクリプト

### 1. `dpo_hands_on.py`

```bash
python dpo_hands_on.py
```

TRL 統合の完全な DPO パイプライン: preference データ読み込み → 訓練 → 評価。

### 2. `dpo_math_reward.py`

```bash
python dpo_math_reward.py
```

数学推論タスクへの DPO 適用。reward margin の動きを比較できる。

## 重要な指標

| 指標 | 期待される挙動 |
|---|---|
| **Reward Margin** | chosen と rejected の暗黙報酬差。安定して増加 |
| **Reward Accuracy** | chosen > rejected の割合。0.8-0.95 を目標 |
| **Training Loss** | 滑らかに減少。erratic は data quality 問題 |
| **Chosen 増加 + Rejected 減少** | 両方正しい方向 (両方減少は確率圧縮で要注意) |

## 次のステップ

- **[Chapter 14: GRPO/RLVR](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/14-grpo-rlvr)**: Critic も RM も除去、検証可能報酬で推論強化
- **[Chapter 15: Agentic RL](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/15-agentic-rl)**: ツール使用エージェントへ応用

## 補足: 第 9a 章のメッセージ

DPO は「数学的な簡素化」(モデル数削減) と「実用上の簡素化」(オンライン rollout 不要) の両立がエレガント。

ただし **データ分布外への改善はできない**。preference データに含まれない応答パターンは学習されない。「DPO だけで十分」と「PPO じゃないとダメ」のどちらでもなく、**段階的に組み合わせる** のが業界のベストプラクティス、というのが本章の現実的なメッセージ。

DPO ファミリー (KTO / SimPO / IPO) は **何が手元にあるか** で選ぶ。アノテーション形式・データ量・メモリ制約で意思決定するための材料を提供する章。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, transformers, trl, datasets, accelerate) | OK |
| `dpo_hands_on.py` 立ち上がり | OK (180 秒で訓練継続中) |
| `dpo_math_reward.py` syntax | OK |

DPO 訓練は 5-10 分で完走の見込み (chapter02 と同じスケール)。
