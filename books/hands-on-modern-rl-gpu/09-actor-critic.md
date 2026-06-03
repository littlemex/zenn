---
title: "Chapter 9: Actor-Critic — 価値ベースと方策ベースの統合"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter06_actor_critic/)

REINFORCE の高分散問題を、**Critic がオンライン推定する V(s)** で解決。Monte Carlo 完走を待たず、1 ステップごとに TD 更新できる現代 RL の基本構造。

## 学習目標

- Actor (方策) と Critic (価値) が TD Error を介してどう連携するか理解
- Advantage 関数 ≈ TD Error の近似関係を把握
- Pendulum / BipedalWalker の連続制御をガウシアン方策で実装
- DQN では不可能だった連続行動空間の問題を解く

## 前提

- venv: `chapter06_actor_critic`
- ハードウェア: GPU 推奨 (連続制御は CPU でも動くが時間がかかる)
- 追加パッケージ: `stable-baselines3` (連続行動の SAC 比較用)

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter06_actor_critic/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter06_actor_critic
```

## 中核アイデア: Critic で Monte Carlo を置き換える

REINFORCE はエピソード完走しないと `G_t` が計算できない。Actor-Critic の洞察:

> 「未来の累積報酬」を `V(s')` で近似すれば、1 ステップごとに更新可能

2 つのネットの役割:

| ネット | 役割 | 更新方法 |
|---|---|---|
| **Actor** (`π_θ`) | 行動を選ぶ | 方策勾配 |
| **Critic** (`V_φ`) | 状態の価値を評価 | TD 学習 |

両者は **TD Error** で連携:

$$
\delta = r + \gamma V(s') - V(s)
$$

この 1 つのスカラーが:
- Actor に「この行動は期待を上回ったか?」を伝える
- Critic に「自分の予測はどれだけズレていたか」を伝える

## Advantage 関数との関係

Advantage 関数: `A(s, a) = Q(s, a) - V(s)` (この行動はこの状態の平均と比べてどれだけ良いか)

Actor-Critic では 1 ステップ TD Error が Advantage を近似する:

$$
A(s, a) \approx \delta = r + \gamma V(s') - V(s)
$$

| TD Error の符号 | 解釈 |
|---|---|
| `δ > 0` | 期待を上回った → 確率を上げる |
| `δ < 0` | 期待を下回った → 確率を下げる |
| `δ ≈ 0` | 期待通り → ほぼ更新なし |

## REINFORCE との数値比較

REINFORCE (エピソード完走時): `G_t = 4.9` (累積)
Actor-Critic (1 ステップ): `δ = 1.97`

**信号が小さい → 勾配の分散が激減**。代わりに bootstrap によるバイアスを少し導入。バイアス・分散トレードオフ。

## 訓練ループ

```python
for each step:
    action = Actor(state)
    next_state, reward, done = env.step(action)

    delta = reward + gamma * Critic(next_state) * (1-done) - Critic(state)

    actor_loss = -log_prob(action) * delta.detach()  # detach 重要!
    critic_loss = delta ** 2

    update(actor_loss + critic_loss)
```

CartPole なら 200-300 エピソードで 500/500 達成 (REINFORCE の 1000+ より高速)。

## Critic の訓練方法

第 3 章で見た 3 通りどれでも使える:

| 方法 | 特徴 |
|---|---|
| **TD(0)** | 1 ステップ bootstrap。低分散・若干バイアス。**実用上の主流** |
| **Monte Carlo** | エピソード完走の累積報酬。バイアス 0 だが分散大 |
| **TD(λ) / n-step** | 上記 2 つの中間 |

## 連続行動空間: ガウシアン方策

CartPole は離散 (2 値) だが、Pendulum / BipedalWalker は連続トルク。

ガウシアン方策の場合、Actor は `(μ, σ)` を出力:

```python
mu, log_std = actor(state)
std = log_std.exp()
dist = Normal(mu, std)
action = dist.sample()                    # 連続値
log_prob = dist.log_prob(action).sum(-1)
```

DQN の argmax では絶対扱えない。

## 実行スクリプト

### 1. `actor_critic_pendulum.py`

```bash
python actor_critic_pendulum.py
```

Pendulum-v1: 倒立した振り子のスイングアップ。連続トルク 1 次元。

### 2. `actor_critic_bipedalwalker.py`

```bash
python actor_critic_bipedalwalker.py
```

BipedalWalker-v3: 4 次元連続行動 (両脚の股と膝)。歩行学習。**第 6 章のハイライト**。

### 3. 可視化

```bash
python render_pendulum.py
python render_bipedalwalker.py
```

## 重要な指標

| 指標 | 期待される挙動 |
|---|---|
| **Episode Reward** | BipedalWalker 300 以上で歩行成功 |
| **Value Loss** | 減少が必須。減らないと Actor の信号が無意味 |
| **Policy Entropy** | 緩やかに減少。BipedalWalker は CartPole より長く探索維持が必要 |
| **TD Error 大きさ** | 持続的に大きいと Critic が当てはまっていない |

## よくある落とし穴

| 症状 | 原因 |
|---|---|
| Actor の勾配が変な方向に流れる | TD Error を `.detach()` していない |
| 学習が壊れる | Actor と Critic で feature 層を共有 (互いの干渉) |
| 不安定 | Actor と Critic の learning rate が合っていない |
| 早期に決定論化して停滞 | 複雑タスクで entropy 正則化が弱い |

## 業界応用

- **AlphaStar (DeepMind)**: StarCraft II AI。複数の時間スケールの報酬、リーグ訓練、抽象度別ネット
- **Soft Actor-Critic (SAC)**: 最大エントロピー正則化 + Q-net 2 つで過大評価を抑制。**連続制御の SOTA**

## 次のステップ

- **[Chapter 10: PPO](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/10-ppo)**: Actor-Critic にクリップ付き更新を加えて更に安定化
- **[Chapter 13: 連続制御比較](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/13-continuous-control)**: PPO/TD3/SAC の比較実験

## 補足: 第 6 章のメッセージ

REINFORCE の純粋さと PPO の実用性の **橋渡し** が Actor-Critic。

「Monte Carlo を bootstrap で置き換える」というアイデアは TD 学習の核心であり、これを理解すると LLM RLHF の **GAE (Generalized Advantage Estimation)** や **TD(λ)** がスムーズに腹に落ちる。

連続制御を初めて扱う章でもあり、ここで「ガウシアン方策で確率分布を出力する」概念を獲得する。これが LLM 系章で「トークン分布をサンプリングする」設計と地続きであることに気づくのが本章の隠れた発見。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, gymnasium, stable_baselines3, imageio) | OK |
| `actor_critic_pendulum.py` | 要 `stable-baselines3[extra]` 追加 |
| `actor_critic_bipedalwalker.py` | 要 `gymnasium[box2d]` 追加 |
| render スクリプト syntax | OK |

### 追加で必要な pip パッケージ

```bash
pip install 'stable-baselines3[extra]'  # progress bar (tqdm + rich)
pip install 'gymnasium[box2d]' box2d-py  # BipedalWalker-v3 用
```
