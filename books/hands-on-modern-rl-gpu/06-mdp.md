---
title: "Chapter 6: MDP と価値関数"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter03_mdp/)

DQN・REINFORCE・Actor-Critic・PPO といった一見バラバラに見える RL 手法が **すべて同じ MDP 問題を異なる近似で解いている** ことを理解する数学基盤の章。

## 学習目標

- MDP の 5 要素 (S, A, P, R, γ) で任意の RL 問題を記述できる
- Bellman 方程式が「無限和」を「再帰計算」に変換する仕組みを理解する
- DP / MC / TD の 3 つの価値推定手法の違い (モデル有無、バイアス・分散トレードオフ) を把握する
- 報酬設計の落とし穴 (Goodhart's Law、ontology error 等) を意識できる

## 前提

- venv: `chapter03_mdp`
- ハードウェア: CPU で十分。Tabular Q-learning なので軽量

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter03_mdp/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter03_mdp
```

## バンディットから MDP へ

- **Multi-armed bandit**: 単一状態、複数行動、即時報酬。「次の状態」がない単純化された問題
- **MDP**: 行動が将来の状態を変える。順次意思決定が本質

MDP の **5 要素**:

| 記号 | 意味 | 例 |
|---|---|---|
| **S** (State Space) | 状態空間 | CartPole の 4D 連続状態、LLM のトークン列 |
| **A** (Action Space) | 行動空間 | CartPole の 2 値、ロボットアームの連続トルク |
| **P** (Transition Prob) | 状態遷移確率 | しばしば未知 — 学習する or 回避する |
| **R** (Reward Function) | 報酬関数 | 設計者の選択。タスク固有 |
| **γ** (Discount Factor) | 割引率 | 将来報酬の重み、無限和の収束を保証 |

## 累積割引報酬

$$
G_t = r_t + \gamma r_{t+1} + \gamma^2 r_{t+2} + \cdots
$$

再帰構造:

$$
G_t = r_t + \gamma G_{t+1}
$$

これが Bellman 方程式の出発点。

## 価値関数

| 記号 | 定義 | 直感 |
|---|---|---|
| `V^π(s)` | 状態 s から方策 π に従ったときの期待累積報酬 | 「この状況の良さ」 |
| `Q^π(s,a)` | 状態 s で行動 a を取り、以後 π に従ったときの期待累積報酬 | 「ここでこの行動を取る良さ」 |

関係式: `V(s) = Σ_a π(a|s) · Q(s,a)`

## Bellman 方程式

無限和を **再帰** に変換する魔法:

$$
V^\pi(s) = \mathbb{E}_a \mathbb{E}_{s'} [r + \gamma V^\pi(s')]
$$

これにより「全軌跡を列挙する」のではなく「1 ステップ先の値を更新する」反復計算が可能になる。

## 価値推定の 3 手法

| 手法 | 仮定 | 更新タイミング | バイアス | 分散 |
|---|---|---|---|---|
| **DP** (Dynamic Programming) | 環境モデル既知 | 全状態を一括更新 | 0 | 0 |
| **MC** (Monte Carlo) | モデル不要 | エピソード終了後 | 0 | 高 |
| **TD** (Temporal Difference) | モデル不要 | 1 ステップごと | 有 | 低 |

**Q-Learning** は TD の代表格。テーブル `Q(s,a)` を持ち、毎ステップ:

$$
Q(s,a) \leftarrow Q(s,a) + \alpha [r + \gamma \max_{a'} Q(s', a') - Q(s,a)]
$$

連続状態空間ではテーブルが無限大になる → **DQN (第 4 章)** でニューラルネットに置き換える。

## 実行スクリプト

### 1. `two_armed_bandit.py` — 探索と活用のトレードオフ

```bash
python two_armed_bandit.py
```

ε-greedy で 2 つの腕の期待報酬を学習。最も基本的な探索-活用問題。

### 2. `gridworld_q_learning.py` — テーブル型 Q-learning

```bash
python gridworld_q_learning.py
```

5×5 のグリッドワールドで Q テーブルが収束していく様子を可視化。Bellman 更新が実際にどう動くか目で見える。

### 3. `bellman_equation_verify.py` — DP/MC/TD の数値検証

```bash
python bellman_equation_verify.py
```

DP・MC・TD すべてが同じ価値関数に収束することを数値的に確認。

## 重要な指標

- **Value Estimation Error**: `|V_推定(s) - V_真(s)|` がゼロに収束
- **Convergence Rate**: 何反復で安定するか。モデル既知なら DP が圧倒的に速い
- **Q-table の進化**: 重要な状態の Q 値の変化を追うと、エージェントが何を学習中か分かる

## 報酬設計の落とし穴 (重要)

**Goodhart's Law** が RL でも牙を剥く: 「測定指標が目的になると、その指標は目的を測れなくなる」

3 つのミスアラインメント:

| 種類 | 例 |
|---|---|
| **Weight Errors** | 複数目的の相対重みが間違っている (速度を重視しすぎて安全を犠牲) |
| **Ontology Errors** | そもそも測る次元が違う (歩行ロボに「移動距離」だけ報酬 → 倒れて転がる) |
| **Range Errors** | 想定外の状況をエージェントが exploit (バグを利用してスコア稼ぎ) |

### Potential-Based Reward Shaping (PBRS)

報酬を密にしたいが最適方策を変えたくない場合の **理論的に安全な方法**:

$$
F(s, s') = \gamma \Phi(s') - \Phi(s)
$$

任意の状態関数 Φ から導出される shaping 報酬を加えても、最適方策は変わらないことが証明されている。

## On-policy vs Off-policy

| 方式 | 学習データの出所 | 例 |
|---|---|---|
| **On-policy** | 現在の方策で生成したデータ | REINFORCE, PPO |
| **Off-policy** | 別の方策 (古い方策、専門家の行動) | DQN (replay buffer), SAC |

データ効率と安定性のトレードオフ。

## 決定論的 vs 確率的方策

- **決定論的**: `a = π(s)`。シンプルだが探索を別途必要 (DQN は ε-greedy)
- **確率的**: `π(a|s)` が確率分布。自然に探索する (PPO はこちら)

これが「なぜ PPO は確率的方策で、DQN は決定論的方策 + ε-greedy なのか」の答え。

## 5 つの質問フレームワーク

任意の RL 問題は次の 5 つで記述できる:

1. 状態は何か?
2. 行動は何か?
3. 環境はどう変化するか?
4. どんなフィードバックがあるか?
5. 割引率はいくつか?

CartPole もロボット制御も LLM のアラインメントも、この 5 つで統一される。

## 次のステップ

- **[Chapter 7: DQN](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/07-dqn)**: Q-table をニューラルネットで置き換え、Atari に適用
- **[Chapter 8: 方策勾配](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/08-policy-gradient)**: 価値ベースから方策ベースへ
- **[Chapter 9: Actor-Critic](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/09-actor-critic)**: 価値関数と方策の合体
- **[Chapter 10: PPO](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/10-ppo)**: クリップ付き方策勾配の現代的な実装

## 補足: 第 3 章のメッセージ

「RL の手法はバラバラに見えて、実は **同じ Bellman 方程式を異なる近似で解いている** だけ」というメタ理解を植え付ける章。

ここを読むと、後続章の手法が「なぜそういう設計なのか」が腑に落ちる。

報酬設計の節は実務的に超重要。LLM 時代の RLHF / DPO / GRPO もすべて「どう報酬を設計するか」が肝で、それは数学ではなく **設計判断** という章のメッセージは現代でも色褪せない。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, gymnasium, numpy, matplotlib) | OK |
| `two_armed_bandit.py` 完走 | OK |
| `gridworld_q_learning.py` 完走 | OK |
| `bellman_equation_verify.py` 完走 | OK |

CPU だけで全スクリプト完走。GPU は使用せず。
