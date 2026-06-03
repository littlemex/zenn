---
title: "Chapter 4: CartPole — 強化学習の Hello World"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter01_cartpole/)

CartPole（倒立振子のバランス取り）を題材に、理論ゼロから RL を体感する章。CPU だけで 30 秒以内に学習が完走するので、まず動かしてから理論に入るスタンス。

## 学習目標

- 強化学習の 4 つの基本要素 (state / action / reward / policy) を CartPole の文脈で理解する
- 訓練ループ「観測 → 行動 → 報酬 → 方策更新」を体験する
- Stable Baselines3 (SB3) のラッパーと PyTorch 実装の 2 通りで PPO を動かす
- 訓練曲線の読み方 (Episode Reward Mean、Policy Entropy、Value Loss、KL Divergence) を学ぶ

## 前提

- venv: `chapter01_cartpole` ([Chapter 2: clone と章ごとの venv セットアップ](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/02-clone-venv) でセットアップ済み)
- ハードウェア: CPU で十分。GPU は使わない (CartPole は小さすぎて GPU の恩恵なし)

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter01_cartpole/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter01_cartpole
```

## 状態・行動・報酬

| 要素 | 内容 |
|---|---|
| **state** | 4 次元ベクトル: カート位置、カート速度、ポール角度、角速度 |
| **action** | 2 値: 左に押す / 右に押す |
| **reward** | バランス維持 1 ステップごとに +1 (1 エピソード最大 500 ステップ) |
| **policy** | 状態 → 行動確率を返すニューラルネット (2 層 × 64 ニューロン) |

訓練ループ:

```
state → policy(state) → action → env.step(action) → (next_state, reward, done)
                                          ↓
                          policy update with collected rollouts
```

## 実行スクリプト

### 1. `1-ppo_cartpole.py` — SB3 ラッパー版

```bash
python 1-ppo_cartpole.py
```

中身は基本的に以下だけ:

```python
from stable_baselines3 import PPO
import gymnasium as gym

env = gym.make("CartPole-v1")
model = PPO("MlpPolicy", env, verbose=1)
model.learn(total_timesteps=100_000)
```

`model.learn()` がカプセル化しているもの:
1. **Actor-Critic アーキテクチャ**: 行動選択ネットと状態価値推定ネット
2. **Rollout 収集**: エピソード自然終了 (`done`) と時間切れ打ち切り (`truncated`) を区別
3. **PPO 更新**: クリップ付き方策勾配で安定化

CPU で 30 秒程度。

### 2. `2-pytorch_ppo.py` — 素の PyTorch 実装

```bash
python 2-pytorch_ppo.py
```

PPO の中身を全部展開した版。以下が見える:

- Rollout buffer の生データ収集
- Generalized Advantage Estimation (GAE) の計算
- クリップ付き方策勾配の更新ステップ
- 価値関数の MSE 損失

学習速度は SB3 とほぼ同じ。「中で何をやっているか」を知りたい時に読む。

### 3. 学習曲線の可視化

```bash
python plot_curves.py
```

または SwanLab のローカルダッシュボードを使う場合:

```bash
swanlab login --host http://127.0.0.1:5092
# ローカル PC 側でポート転送
# bash gpu-cdk/scripts/deploy.sh --port-forward --stack-name rl-gpu-ws -p 5092:5092 -r ap-northeast-1
```

## 重要な指標

| 指標 | 意味 | 健全な挙動 |
|---|---|---|
| **Episode Reward Mean** | 1 エピソードあたりの平均報酬 | 500 (最大) に向かって上昇 |
| **Policy Entropy** | 方策のランダムさ | 高 (ランダム) → 低 (自信あり) と緩やかに減少 |
| **Value Loss** | Critic の状態価値推定誤差 | 単調に減少 |
| **KL Divergence** | 更新前後の方策の差 | 小さい値で推移 (大きいと不安定) |
| **Clip Fraction** | クリップが効いた割合 | 0.1〜0.3 程度なら正常 |

## 訓練曲線の読み方

### 正常な挙動

- 初期は報酬が 20-50 で低迷
- ある時点から急上昇 (典型的な S 字カーブ)
- 500 付近で安定収束
- **小さな上下動は正常** (サンプリングのばらつき)

### 異常な挙動と原因

| 症状 | 原因 | 対処 |
|---|---|---|
| 報酬が低いまま頭打ち | 探索不足 / 学習率が小さすぎる | `ent_coef` を上げる、`learning_rate` を上げる |
| 一度上がってから崩壊 | 方策更新ステップが大きすぎる | `clip_range` を下げる、`n_epochs` を減らす |
| Entropy が急速にゼロへ | 早期収束 | `ent_coef` で探索を残す |
| 報酬が突然 0 に落ちる | 真のバグ (env / reward 関数) | env を疑う |

## 次のステップ

- [Chapter 6: MDP](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/06-mdp) で「なぜ報酬の累積を最大化するのか」を数学的に理解
- [Chapter 8: 方策勾配](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/08-policy-gradient) で REINFORCE → Actor-Critic の流れを追う
- [Chapter 10: PPO](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/10-ppo) で本格的な PPO チューニングへ

## 補足: 第 1 章のメッセージ

「現代的な RL はノートパソコンでも体験できる」を実証する章。100-200 MB RAM、2 層 64 ニューロンの小さなネット、CPU だけで完走。

ここで学んだ 4 要素 (state / action / reward / policy) は、ロボット制御も LLM のアラインメントも同じ枠組みで捉えられる、という後続章への伏線でもある。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, gymnasium, stable_baselines3, swanlab) | OK |
| `1-ppo_cartpole.py` 完走 | OK (前回 80 秒で 500.0±0.0 達成) |
| `2-pytorch_ppo.py` 立ち上がり | OK |
| `plot_curves.py` syntax | OK |

警告 `Please note that the Amazon EC2 g7e.2xlarge instance type is not supported by current Deep Learning AMI` は DLAMI リリースノート未更新による誤検知で無害。
