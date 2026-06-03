---
title: "Chapter 15: Agentic RL — 多ターン・ツール使用エージェント"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter10_agentic_rl/)

単発のテキスト生成から、**多ステップで環境と相互作用** するエージェント (コーディング、リサーチ、ツール呼び出し) への RL 拡張。Credit Assignment、軌跡合成、infrastructure が新しい挑戦。

## 学習目標

- LLM RL から POMDP への拡張 (state / action / reward の定義変更) を理解
- ORM (結果報酬) と PRM (プロセス報酬) の credit assignment トレードオフを把握
- 軌跡合成の 6 手法と、いつどれを使うかを把握
- ツール呼び出しエージェントを GRPO で訓練するパイプラインを実装できる

## 前提

- venv: `chapter10_agentic_rl`
- ハードウェア: GPU 必須。多ターン rollout で context が長くなり VRAM 厳しい
- 第 7 章 (PPO)、第 8 章 (RLHF)、第 9b 章 (GRPO/RLVR) の前提知識
- 追加: `pip install json5` (合成データ生成で使用)

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter10_agentic_rl/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter10_agentic_rl
```

## MDP から POMDP へ

| 観点 | LLM RL (第 9 章まで) | Agentic RL |
|---|---|---|
| **State 空間** | 固定プロンプト | 動的な環境状態 |
| **Action 空間** | テキスト生成のみ | テキスト + 構造化ツール呼び出し |
| **Reward** | 1 エピソード 1 スカラー | ステップレベル可能 |
| **目的** | 出力品質 | 多ステップ相互作用戦略 |
| **エピソード長** | 1 ターン | 多ターン (50-200+) |

## 中核概念

### Rollout

完全なエージェント-環境相互作用のシーケンス: 推論 → ツール呼び出し → 観測 → ... → 最終結果。テキスト生成と違い **実行が必要**。

### Agent Loop

```
Perception → Reasoning → Action → Observation → (繰り返し)
```

ツール呼び出しは単なるテキスト出力ではなく **明示的な API invocation**。

### RL によるツール呼び出し学習

検索戦略やツール選択ルールを **ハードコード** せず、報酬信号から trial-and-error で学習する。

## 中心的課題: Credit Assignment

7 ステップの軌跡が失敗したとき、どのステップに失敗を帰属させるか? 序盤のミスが後続全部に伝播する問題。

### ORM (Outcome Reward Model)

最終結果のみ評価。中間ステップは品質に関わらず報酬 0。

| 利点 | 欠点 |
|---|---|
| シンプル、アノテーション安価 | スパース信号、エージェントが自分でどのステップが原因か発見必要 |

### PRM (Process Reward Model)

各ステップ個別評価。

| 利点 | 欠点 |
|---|---|
| Dense フィードバック、推論ミスを正確に特定 | ステップごとアノテーション必要 (高コスト) |

### 比較結果

実験では PRM が ORM より **70% 成功率到達まで 20 epoch 速い**。信号区別力は **19 倍**。

## 軌跡合成 (Trajectory Synthesis)

人手で軌跡を作るのは **単応答アノテーションの 10 倍コスト**。6 つの合成手法:

| 手法 | 内容 |
|---|---|
| **1. Rejection Sampling** | 大量試行 → 成功例だけ採用。シンプル、多様性低 |
| **2. Director-Actor Mode** | 高レベル計画と step-by-step 実行を分離。一貫性向上 |
| **3. Graph-Based (Magnet)** | 関数シグネチャグラフで論理的に妥当なツール呼び出し列を保証 |
| **4. Closed-Loop (LoopTool)** | 訓練とデータ生成を交互、現モデルの弱点を狙う |
| **5. HardGen** | 難しい例を優先 |
| **6. ECHO (Hindsight Rewriting)** | 失敗軌跡を「別目的での成功」として再フレーム |

## Infrastructure 課題

LLM RL になかった engineering 要件:

| 要件 | 内容 |
|---|---|
| **ヘテロ計算** | GPU (推論) + CPU (ツール実行) + ネット (API call) の混在 |
| **安全 sandbox** | コード実行環境を分離して有害な副作用を防ぐ |
| **非同期並行** | ツール call の所要時間がばらつき、同期 batch が非効率 |
| **長 context** | 多ターン履歴が大きく成長、attention 計算が重い |

## 業界フレームワーク

| フレームワーク | 提供元 |
|---|---|
| **Agent-R1** | OSS, agentic RL 基盤 |
| **AReaL** | 非同期 RL (long-horizon 向け) |
| **NeMo Gym** | NVIDIA の産業向け |
| **rLLM** | LLM API call をフックして軌跡を集める |

## ハンズオン

### 1. ORM vs PRM 比較 (`multi_turn_rl.py`)

```bash
python multi_turn_rl.py
```

地球の周長を計算する多ステップタスクで、ORM と PRM の収束速度を比較。

### 2. ツール呼び出しエージェント (`tool_use_agent.py`)

```bash
python tool_use_agent.py
```

基本的なツール呼び出し RL ループ。報酬は実行成功 / 失敗。

### 3. 軌跡合成パイプライン (`generate_synthetic_data.py`)

```bash
python generate_synthetic_data.py
```

6 手法のうち主要なものを実装。

### 4. リサーチエージェント (`mini_deep_research_grpo.py`)

```bash
python mini_deep_research_grpo.py
```

GRPO で訓練する深層リサーチエージェント。検索クエリ生成 → 情報統合 → 要約。事実精度に紐付いた報酬。

## 業界実装パターン

LinkedIn / Bespoke Labs / Moonshot / Alibaba / Salesforce / Amazon の事例から:

```
ルールベース報酬 (形式 validity, 実行成功)
    ↓
セマンティック品質スコア
    ↓
RLER (環境応答からの報酬学習)
    ↓
多次元 rubric (正確性 + 効率 + 安全 + ユーザー嗜好)
```

## 重要な指標

| 指標 | 説明 |
|---|---|
| **Task Success Rate** | タスク完了率。主指標 |
| **Tool Call Accuracy** | ツール選択 + パラメータ正しさ |
| **Step-Level Reward (PRM)** | 各ステップ品質。推論劣化箇所が見える |
| **Trajectory Length 分布** | 平均ステップ数 (短いほど良い、成功率同じなら) |
| **Rollout Efficiency** | 訓練に使える rollout の割合 (完全失敗を除く) |

## 重要な実装上の注意

### サンドボックス

コード実行は **必ず分離環境**:
- Docker container
- Firejail
- gVisor

ツール呼び出しエージェントは「rm -rf /」みたいな破壊的コマンドを学習する可能性がある。

### 非同期 rollout

ツール call の所要時間に大きなばらつきがあるので、同期で待つと GPU 利用率が下がる。AReaL のような非同期 RL framework が有用。

## 次のステップ

- **[Chapter 16: VLM RL](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/16-vlm-rl)**: 視覚言語モデルへの GRPO 適用
- **[Chapter 17: 今後の動向](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/17-future-trends)**: マルチエージェント、Tree of Thought

## 補足: 第 10 章のメッセージ

「LLM RL の最終形」と言える章。Agentic RL は **コーディングエージェント、リサーチエージェント、ツール使用 LLM** のすべての基盤。

特筆すべきは Infrastructure 複雑度。RL アルゴリズムだけでは解決できない、システム設計レベルの問題 (heterogeneous compute、サンドボックス、async) が前面に出る。

PRM の Dense 信号は強力だが、アノテーションコストとのトレードオフ。**ルールベース PRM (検証ステップで自動採点)** が現実解として有望。

「LLM RL の難所は数学ではなくシステム」というのが本章の最重要メッセージ。RLHF/GRPO まではアルゴリズム勝負、Agentic からはエンジニアリング勝負。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, transformers, trl, datasets, accelerate, json5) | OK |
| `multi_turn_rl.py` 完走 | OK |
| `tool_use_agent.py` 完走 | OK |
| `mini_deep_research_grpo.py` syntax | OK |
| `generate_synthetic_data.py` syntax | OK |
