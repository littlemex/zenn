---
title: "Chapter 11: RLHF — LLM の人間嗜好アラインメント"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter08_rlhf/)

InstructGPT スタイルの **3 段階 RLHF パイプライン (SFT → RM → PPO)** を、TRL (小規模) と veRL (産業規模) の両方で再現する章。第 7 章の PPO を LLM に適用。

## 学習目標

- 古典 RL の概念 (state, action, reward, policy) を LLM 文脈に対応付けできる
- SFT → 報酬モデル (Bradley-Terry) → PPO-RLHF の流れを実装できる
- 4 モデル構成 (Actor + Critic + Reference + RM) のメモリ制約を理解
- 報酬ハッキングの兆候を見抜き、評価フレームワークを設計できる
- veRL を使った GSM8K の RLVR (検証可能な報酬) 実験を回せる

## 前提

- venv: `chapter08_rlhf`
- ハードウェア: GPU 必須。Qwen2.5-0.5B でも 24 GB VRAM 推奨。RTX PRO 6000 96 GB は余裕
- ディスク: モデル + データセットで 10-20 GB
- 環境変数:
  ```bash
  export HF_HOME=/home/coder/.cache/huggingface
  export HF_DATASETS_CACHE=/mnt/local/hf-datasets-cache  # 高速
  ```

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter08_rlhf/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter08_rlhf
```

## RL 概念の LLM への対応

| RL 概念 | LLM での対応 |
|---|---|
| **State** | プロンプト + これまで生成したトークン列 |
| **Action** | 次のトークン予測 (語彙サイズが行動空間) |
| **Policy** | LLM のトークン分布 |
| **Reward** | 報酬モデル (RM) のスコア (応答完了後) |
| **Episode** | 1 つのプロンプト → 完全応答までの 1 サイクル |

## 3 段階パイプライン

### Stage 1: Supervised Fine-Tuning (SFT)

質の高い人手作成の (instruction, response) ペアで訓練。

- **獲得能力**: 形式 (どう答えるか)
- **獲得しない能力**: 判断 (どの答えがより良いか)
- **出力**: SFT モデル (=後の policy / reference 双方の出発点)

#### よくある失敗

- 応答が短すぎ
- 質問をそのまま echo
- 一般的すぎて中身がない

### Stage 2: 報酬モデル (RM) 訓練

Bradley-Terry の枠組み:

$$
P(y_w > y_l | x) = \sigma(r(x, y_w) - r(x, y_l))
$$

損失:

$$
L_{RM} = -\log \sigma(r_w - r_l)
$$

#### エンジニアリング上の重要点

- **データ分割は prompt 単位** (response pair 単位だと train/eval リーク)
- PPO 前に **スコアキャリブレーション** (報酬スケールの安定化)
- RM は「真実」ではなく「学習された判定者」。PPO はその盲点を必ず突く

### Stage 3: PPO-RLHF (4 モデル同時)

| モデル | 役割 | 状態 |
|---|---|---|
| **Actor** | 訓練対象の方策 | 学習可能 |
| **Critic** | 価値推定 → GAE | 学習可能 |
| **Reference** | KL ペナルティ用の凍結 SFT | 凍結 |
| **Reward Model** | 応答スコアリング | 凍結 |

報酬:

$$
\text{reward} = RM(y) - \beta \cdot KL(\pi_{actor} || \pi_{reference})
$$

KL ペナルティが Actor を SFT から離れすぎないよう抑える (=報酬ハッキング防止)。

## トークンレベル vs シーケンスレベル報酬

- **シーケンスレベル**: 全トークンに同じ報酬を割り当てる (シンプル)
- **トークンレベル (推奨)**: Critic + GAE で各トークンに個別の Advantage を割り当て

トークンレベルの利点: 「重要な答えのトークン」と「定型句」の差別化が可能。長文応答ほど効果大。

## 訓練の不安定性 3 種

| 種類 | 説明 |
|---|---|
| **Non-stationary data** | Actor が毎エポック異なる分布の応答を生成、データが常に off-policy |
| **OOD Reward Model errors** | 方策が RM の訓練データ外を探索し、信頼できないスコアを得る |
| **Reference drift** | Actor が SFT から徐々に離れ、KL ペナルティが弱くなる |

5 指標を **同時に** モニタ: reward / KL / response length / entropy / value loss。**raw reward だけ最適化は禁物**。

## TRL vs veRL

| | TRL | veRL |
|---|---|---|
| 規模 | 360M-7B | 7B-70B+ |
| 推論 | 標準 PyTorch (sequential) | vLLM/SGLang (10-20x 高速) |
| 分散 | DDP | Ray (Actor/Critic/Ref/RM 別ワーカー) |
| メモリ | DDP | FSDP / Megatron + 3D-HybridEngine |
| 用途 | 研究・学習 | 産業規模 |

## 実行スクリプト

### Stage 1: SFT

```bash
python sft_pipeline.py
```

Qwen2.5-0.5B 等を SFT。データセットは `databricks/databricks-dolly-15k` 等。

### Stage 2: 報酬モデル訓練

```bash
python reward_model_training.py
```

Bradley-Terry 損失で preference 学習。出力: スカラースコアを返すモデル。

### Stage 3: PPO-RLHF

```bash
python rlhf_ppo_train.py
```

4 モデル同時の PPO ループ。KL ペナルティと GAE 入り。Critic は別途訓練。

## veRL + GSM8K 実験 (RLVR の先駆け)

`verl_gsm8k/` サブディレクトリに veRL を使った実験スクリプト。

```bash
cd /home/coder/work/hands-on-modern-rl/code/chapter08_rlhf/verl_gsm8k

# RM ではなくルールベース報酬: 正解=1, 不正解=0
bash run_qwen2_5_0_5b_ppo_single_gpu.sh
```

特徴:
- **RM 不要** (GSM8K の正解は数値で検証可能)
- 開始精度 〜42% → 20 エポックで 〜55%
- ハイパラ: lr=1e-6 (Actor), lr=1e-5 (Critic), batch_size=128

これが第 9b 章の **RLVR (Reinforcement Learning with Verifiable Rewards)** の原型。

### 必要リソース

- 単一 GPU: 24 GB VRAM (Qwen2.5-0.5B)
- 8 GPU 版: `run_qwen2_5_0_5b_ppo_8gpu.sh`

## 報酬関数設計 (重要)

**RM 単独はアンチパターン**。混合報酬を推奨:

| 構成要素 | 内容 |
|---|---|
| RM スコア | 嗜好アラインメント |
| ルール報酬 | 長さ制約、引用形式、拒否パターン |
| 検証 | 数学正解、コード実行成否 |

## 評価フレームワーク (3 層)

| 層 | 内容 |
|---|---|
| **自動ベンチマーク** | MMLU, HumanEval, GSM8K — 能力保持の確認 (忘却チェック) |
| **Pairwise 比較** | reference モデルに対する勝率 (LLM-as-judge) |
| **手動スポットチェック** | 報酬ハッキング、事実性劣化、prompt injection |

## よくある落とし穴

| 症状 | 原因 |
|---|---|
| Reward は上昇するが応答品質が劣化 | 報酬ハッキング、KL 弱すぎ |
| 応答長が爆発 | RM の長さバイアス |
| Forgetting (能力の劣化) | KL 不足、訓練ステップ多すぎ |
| Critic が学習しない | learning rate が小さすぎ、value_clip_range が狭い |

## 次のステップ

- **[Chapter 12: アラインメント](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/12-alignment)**: DPO / IPO / KTO / ORPO といった RLHF の代替
- **[Chapter 14: GRPO/RLVR](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/14-grpo-rlvr)**: 報酬モデル不要、検証可能報酬での RL
- **[Chapter 15: Agentic RL](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/15-agentic-rl)**: ツール使用エージェントへの応用

## 補足: 第 8 章のメッセージ

LLM 時代の RL は「PPO + 4 モデル」が標準だが、その複雑さが **代替手法 (DPO / GRPO) の動機** になっている。

第 8 章は「正攻法でやるとこれだけ重い」という体感を提供することで、後続章の DPO / GRPO の意義を理解する土台になる。

実装上の最大の難所は **デバッグ**。reward が上がっても応答品質が落ちるケースがほぼ必ず起きる。「reward だけ見るな」「5 指標 + 質的評価」がこの章の最重要メッセージ。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, transformers 4.57.3, trl 0.24.0, datasets, accelerate) | OK |
| `sft_pipeline.py` | モデル forward 直前で upstream バグ |
| `reward_model_training.py` syntax | OK |
| `rlhf_ppo_train.py` syntax | OK |

### 既知の問題

`sft_pipeline.py` で `RuntimeError: Expected all tensors to be on the same device, but got index is on cpu, different from other tensors on cuda:0`。input_ids を `.to(device)` する処理が抜けている。修正例: モデル forward 前に `inputs = {k: v.to(device) for k, v in inputs.items()}`。
