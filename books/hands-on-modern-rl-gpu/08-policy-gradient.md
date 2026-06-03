---
title: "Chapter 8: 方策勾配法 — REINFORCE と Baseline"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter05_policy_gradient/)

DQN の限界 (連続行動・大規模語彙の argmax 不可) を超えるため、**方策を直接最適化** するアプローチへ。REINFORCE → REINFORCE with Baseline → Actor-Critic の流れの起点。

## 学習目標

- 方策勾配定理 ∇J(θ) = E[∇log π · G] を直感的に理解
- REINFORCE の高分散問題 (なぜ報酬曲線がガタつくのか) を体験する
- Baseline (V(s)) を導入することで分散を激減させる仕組みを学ぶ
- Advantage 関数 A(s,a) = G - V(s) の意味を把握

## 前提

- venv: `chapter05_policy_gradient`
- ハードウェア: CPU で十分

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter05_policy_gradient/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter05_policy_gradient
```

## なぜ方策勾配が必要か

DQN は本質的に `argmax_a Q(s, a)` を計算する設計。これが致命的に困るケース:

- **連続制御**: ロボットアームの関節トルクは実数値、argmax 不可
- **言語生成**: 50,000 語彙からの argmax は確率分布全体を捨てる
- **内蔵探索**: DQN は ε-greedy が別途必要、方策勾配は確率分布なので自然に探索

方策勾配は価値推定をスキップし、**直接 `π_θ(a|s)` を学習** する。

## 方策勾配定理

期待累積報酬の勾配:

$$
\nabla_\theta J(\theta) = \mathbb{E}_{\pi_\theta} \Big[\sum_t \nabla_\theta \log \pi_\theta(a_t | s_t) \cdot G_t \Big]
$$

直感: 高い累積報酬を生んだ行動の確率を上げ、低い報酬の行動の確率を下げる。サンプリングで勾配を推定可能 (環境モデル不要)。

## REINFORCE アルゴリズム

```
1. エピソードを 1 本完走、(s_t, a_t, r_t) を記録
2. 各 t で G_t = Σ_{k=t} γ^(k-t) r_k を計算
3. loss = -log π(a_t|s_t) · G_t (PyTorch は最小化なので符号反転)
4. 1 ステップ更新
5. 繰り返す
```

シンプルだが **致命的な弱点** がある。

## 高分散問題

`G_t` には、その後のすべての偶然性が累積する。同じ (state, action) でも、その後何が起きるかで gradient signal が大きく変わる。

例: 同じ「ポールが垂直に近い状態で右にプッシュ」という行動でも、
- 直後にバランス取れて 100 ステップ続く → G_t が大きい正の値
- たまたま外乱で倒れる → G_t が小さい

**これは行動の良し悪しではなく、その後の運**。にも関わらず gradient はそれを行動への評価として使ってしまう。

訓練曲線の症状:
- 報酬曲線が滑らかに上昇せず激しく振動
- DQN より多くのエピソードが必要
- 乱数シードに敏感

## Baseline で分散削減

状態のみに依存する関数 `b(s)` を引いても、勾配の期待値は変わらない:

$$
\nabla J = \mathbb{E}\Big[\sum_t \nabla \log \pi(a_t|s_t) \cdot (G_t - b(s_t))\Big]
$$

なぜなら `Σ_a π(a|s) = 1` なので `Σ_a ∇log π(a|s) = 0`。baseline は積分すると消える。

### 効果

`b(s) = V(s)` を使うと、Advantage `A(s, a) = G_t - V(s_t)` で評価:

| 行動の質 | Baseline なし | Baseline あり |
|---|---|---|
| 平均以下 | それでも強化される | 正しくペナルティ |
| 平均的 | 強化される | 更新なし |
| 平均以上 | 強く強化 | 適切に強化 |

## REINFORCE with Baseline の実装

```python
# Actor: π_θ(a|s)
# Critic: V_φ(s)

# 各エピソード後:
G = compute_returns(rewards, gamma)
V = critic(states)
A = G - V.detach()                    # Advantage (Critic を detach)

actor_loss = -(log_probs * A).mean()
critic_loss = F.mse_loss(V, G)

loss = actor_loss + 0.5 * critic_loss - 0.01 * entropy
```

純粋な Monte Carlo (エピソード完走必要) のままだが、**勾配分散は劇的に減る**。

## 実行スクリプト

### 1. `reinforce_cartpole.py` — Vanilla REINFORCE

```bash
python reinforce_cartpole.py
```

Baseline なし。報酬曲線が大きく振動するのを直接観察。

### 2. `reinforce_with_baseline.py` — Baseline 追加

```bash
python reinforce_with_baseline.py
```

V(s) baseline で分散がどれだけ減るか比較。同じ条件で滑らかな上昇を見れる。

### 3. `actor_critic_cartpole.py` — TD Actor-Critic

```bash
python actor_critic_cartpole.py
```

第 6 章のプレビュー。1 ステップ TD で更新するため、エピソード完走を待たず学習可能。

### 4. 可視化

```bash
python render_cartpole_baseline.py
```

## 重要な指標

| 指標 | 期待される挙動 |
|---|---|
| **Reward Mean** | CartPole 500 が目標、ただし DQN より分散大 |
| **Reward 分散** | 方策勾配特有の最重要指標。Baseline 有無で比較 |
| **Policy Entropy** | 方策の特化に伴って緩やかに減少 |
| **Value Loss** | Baseline ネットの当てはまり (悪いと信号が劣化) |

## よくある落とし穴

| 症状 | 原因 |
|---|---|
| エピソードごとに勾配スケールが暴れる | `G_t` を normalize していない |
| 訓練が遅い | Baseline なしで分散が大きすぎる |
| Critic を経由して Actor の勾配が誤伝播 | `V(s)` を `.detach()` していない |
| 早期に決定論的になり性能頭打ち | Entropy 正則化が弱い |

## 次のステップ

- **[Chapter 9: Actor-Critic](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/09-actor-critic)**: TD で 1 ステップごと更新、Monte Carlo 完走不要
- **[Chapter 10: PPO](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/10-ppo)**: クリップ付き方策勾配で更新の安定性をさらに改善

## 補足: 第 5 章のメッセージ

「方策勾配は理論的にエレガントだが、そのままでは実用に耐えない」。これが本章の中心メッセージ。

Baseline → Advantage → Actor-Critic → PPO と進化する流れの **起点** であり、後続章の動機付けでもある。

LLM の RLHF / DPO で「報酬から log π への gradient」を計算するのも、本質的にここで学んだ方策勾配定理の応用。第 5 章の理解は LLM 章 (8/9) の基盤になる。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, gymnasium, numpy) | OK |
| `reinforce_cartpole.py` 完走 | OK |
| `reinforce_with_baseline.py` 完走 | OK |
| `actor_critic_cartpole.py` | 起動成功するが訓練中にエラー (upstream バグ) |
| `render_cartpole_baseline.py` syntax | OK |

### 既知の問題

`actor_critic_cartpole.py` 244 行目付近で `AttributeError: 'float' object has no attribute 'detach'`。第 6 章の Actor-Critic を試したいなら、第 6 章の SB3 ベース実装 (`actor_critic_pendulum.py` 等) を推奨。
