---
title: "Chapter 17: 今後の動向 — Embodied / Multi-Agent / 推論時計算"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter12_future_trends/)

CartPole から VLM RL まで辿った旅の総括。2025-2026 にかけて RL の地形を再定義しつつある **3 大潮流と 6 つのフロンティア** を概観する章。

## 学習目標

- 第 1-11 章を縦串で振り返り、各手法がどの問題を解いたかを再確認
- Embodied / Inference-time scaling / Multi-Agent の 3 潮流を理解
- DPO → GRPO → DAPO の現代的進化ラインを把握
- Multi-Agent RL と Tree of Thought の基本的な仕組みを学ぶ

## 前提

- venv: `chapter12_future_trends`
- ハードウェア: スクリプトは軽量、CPU で動く

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter12_future_trends/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter12_future_trends
```

## 旅の総括

| 章 | 解決した問題 |
|---|---|
| 4 (DQN) | テーブルから NN へ、連続状態空間 |
| 5 (REINFORCE) | argmax 不可能な連続行動空間 |
| 6 (Actor-Critic) | REINFORCE の高分散 |
| 7 (PPO) | Actor-Critic の方策崩壊 |
| 8 (RLHF) | PPO の LLM 適用 |
| 9a (DPO) | RM 不要 (報酬モデルの除去) |
| 9b (GRPO/RLVR) | Critic 不要 + RM 不要 (検証可能報酬) |
| 10 (Agentic) | 単一ターンから多ターン・ツール使用へ |
| 11 (VLM) | テキストから視覚モダリティへ |

各ステップが **直前の手法の限界** を解決し、一貫したリネージを成す。

## 2025-2026 の 3 大潮流

### 1. Physical World Integration (Embodied Intelligence)

物理システム (ロボット) への RL 進出。シミュレーションと違い:
- 任意リセット不可
- 探索コスト高 (実機時間が必要)
- 失敗が許されない

主要課題:
- **Sim-to-real transfer**: シミュ訓練が実機で破綻
- **Sample efficiency**: 1 rollout が高価
- **Safety constraints**: ハード制約 (ソフト報酬では不十分)

### 2. Reasoning-Time Search (Inference-Time Scaling)

訓練ではなく **推論時** に計算を投資して良い答えを探す:

| 手法 | 内容 |
|---|---|
| **Best-of-N sampling** | N 応答生成 → 報酬関数で最良を選ぶ |
| **MCTS** (Monte Carlo Tree Search) | 推論パスを系統的に先読み |
| **Chain-of-thought scaling** | 推論連鎖を長くするだけで難問の精度向上 |

**トレードオフ**:
- オンラインサービス: 訓練効率優先 (推論安価)
- 品質クリティカル: 推論時計算優先

OpenAI o1 / DeepSeek-R1 で実証された方向性。

### 3. Multi-Agent Dynamics

単一エージェントから協調・競合ネットワークへ:
- **Self-play**: AlphaGo 系統 (自己対戦で進化)
- **LLM Multi-Agent**: 専門化エージェントの協働
- **Emergent coordination**: 通信プロトコルや専門化が emergent に出現

## 6 つのフロンティアトピック

### 1. Embodied Intelligence (具現化知能)

- Model-based RL: 世界モデルで sample efficient な学習
- 言語条件付け manipulation
- VLM 駆動の visual servoing

### 2. Model-Based RL

世界モデル (state, action → next_state, reward の予測器) を学習し、その中でプランニング:

- 利点: 物理実行なしに大量の rollout をシミュレート
- 課題: モデル誤差が長期予測で累積

### 3. Self-Play Evolution

自身 or 自身の variant と対戦して向上:
- AlphaGo / AlphaZero 系統
- LLM debating: 互いに反論し合う
- 競技ベンチでのコーディングエージェント
- Adversarial robustness テスト

### 4. LLM Multi-Agent Systems

オーケストレータが専門サブエージェントにタスク割り当て。RL で:
- オーケストレーション方策 (どう分解するか)
- 個別エージェントの行動

を同時最適化。

### 5. Offline RL とデータ効率

固定データセットからの学習 (環境相互作用なし):
- ドメイン: ヘルスケア、金融、物理システム (オンライン探索が危険)
- アルゴリズム: CQL、IQL、Decision Transformer

### 6. RL Scaling Outlook

3 つの軸:

| 軸 | 内容 | 飽和点 |
|---|---|---|
| **データスケール** | 自動生成プロンプトの多様性 | まだ伸びる |
| **サンプリングスケール** | GRPO の k を 4 → 64 へ | k=16 付近で diminishing returns |
| **訓練ステップ** | Pass@1 が継続的に改善 | 明確な飽和点なし |

### 訓練パラダイム比較

| | Offline (DPO) | Semi-online | Online (GRPO/DAPO) |
|---|---|---|---|
| データ | 固定 preference | 定期 refresh | リアルタイム生成 |
| 安定性 | 高 | 中 | 低 |
| 探索 | 限定 | 中 | 高 |

**推奨進化**: DPO (素早い検証) → GRPO (性能向上) → DAPO (十分な計算がある fine-tuning)

## RLMT (RL with Model-rewarded Thinking)

数学・コード以外の **一般会話** にも推論連鎖を適用するアプローチ。

- **RLMT-Zero**: SFT なしで base モデルから訓練
- 7K 会話プロンプトのみで supervised 版より良い結果
- 「答える前に考える」が一般対話品質も向上することを実証

## 実行スクリプト

### 1. Multi-Agent MARL

```bash
python multi_agent_marl.py
```

協調・競合シナリオでのマルチエージェント RL 訓練。

### 2. Tree of Thought

```bash
python tree_of_thought.py
```

RL 誘導された探索と価値推定による Tree-of-Thought 探索。

## Convergence Thesis (収斂仮説)

第 12 章の結論:

> 「2025-2026 のすべての主要進歩は、**RL が計算をどう効率的に配備するかを学習する** という共通構造を持つ。訓練時 (GRPO, DAPO) か推論時 (MCTS, Best-of-N) かの違いはあれど、**訓練と推論の境界がぼやけつつある**。」

## 学習継続のための推奨資源

| カテゴリ | 例 |
|---|---|
| 論文 | OpenAI o1, DeepSeek-R1, Qwen-Math, Anthropic Constitutional AI |
| OSS フレームワーク | veRL, TRL, Open-RLHF, EasyR1 |
| ベンチマーク | AIME, GSM8K, HumanEval, MMLU, BFCL |
| ブログ | Lilian Weng (OpenAI), Yannic Kilcher, Sebastian Raschka |

## 補足: 第 12 章のメッセージ

本書全体のメッセージを 1 行で要約すると:

> 「**RL の本質は計算の使い方の最適化** であり、対象がゲームでも LLM でも VLM でもエージェントでも、その基本構造は変わらない」

CartPole から VLM RL まで通読すると、確かに **同じ Bellman 方程式が形を変えて何度も現れる** ことに気づく。第 3 章 (MDP) の 5 要素フレームワークが最後まで通用するのも、それを実証している。

今後のフロンティアは **Infrastructure** と **計算配備の最適化** に移っていく。アルゴリズムの新規性よりも「どう効率的に計算を回すか」という engineering 競争。

第 1-11 章で学んだことは、これからも陳腐化しない基盤になる。新しい論文を読んだとき「どの章の手法をどう拡張したのか」が見える眼が、本書を通読した最大の収穫。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, stable_baselines3, numpy) | OK |
| `multi_agent_marl.py` 完走 | OK |
| `tree_of_thought.py` 完走 | OK |
