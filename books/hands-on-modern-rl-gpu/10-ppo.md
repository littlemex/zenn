---
title: "Chapter 10: PPO — Proximal Policy Optimization"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter07_ppo/)

ゲーム RL から LLM アラインメントまで業界標準となった **PPO** を、原理から実装まで通しで学ぶ章。Actor-Critic の不安定性を「クリップ付き更新」で解消する。

## 学習目標

- PPO のクリップ目的関数の意味を理解 (なぜ更新が暴走しないか)
- GAE (Generalized Advantage Estimation) で λ を調整し、バイアス・分散をコントロール
- Importance sampling による K-epoch データ再利用の仕組み
- LLM の RLHF で必要となる 4 モデル構成 (Actor + Critic + Reference + Reward Model) のプレビュー

## 前提

- venv: `chapter07_ppo`
- ハードウェア: GPU 推奨。BipedalWalker は CPU でも数時間で学習する
- 追加: `gymnasium[box2d]`, `stable-baselines3`

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter07_ppo/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter07_ppo
```

## 解決したい問題: 方策崩壊

Actor-Critic は CartPole では動くが複雑環境で不安定。原因:

> 方策勾配の更新サイズに **制限がない**。1 つの不運なバッチで方策が劇的に変化し、それまでのデータが全部 off-policy になる

そして方策が崩壊すると、再収集とも長時間ロールバックが必要。

## 歴史的経緯: TRPO → PPO

- **TRPO (2015)**: KL ダイバージェンスを「信頼領域」として制約。理論的にエレガントだが Hessian 計算が重い
- **PPO (2017)**: KL 制約を **確率比のクリップ** に置き換え。同等の安定性で計算コスト激減

## PPO-Clip の目的関数

$$
L^{CLIP}(\theta) = \mathbb{E}_t \big[\min(r_t(\theta) \hat{A}_t, \text{clip}(r_t(\theta), 1-\epsilon, 1+\epsilon) \hat{A}_t)\big]
$$

| 記号 | 意味 |
|---|---|
| `r_t(θ)` | 確率比 `π_θ(a|s) / π_old(a|s)` |
| `Â_t` | Advantage 推定値 |
| `ε` | クリップ幅 (制御で 0.2、LLM で 0.05-0.1) |

`min` の意味: クリップした方とクリップしない方の **より保守的な方** を取る。確率比が `[1-ε, 1+ε]` を超えると勾配がゼロになり、過度な更新を防ぐ。

## Importance Sampling: 古いデータを再利用

確率比 `r_t(θ)` で重み付けすることで、`π_old` で集めたデータを **K 回の更新** に使い回せる。

```
collect rollouts with π_old
for k in range(K):  # 通常 K=10 程度
    update θ using clipped objective
update π_old ← π_θ
```

クリップが安全装置として機能し、データが少しずつ off-policy になっても破綻しない。

## GAE (Generalized Advantage Estimation)

Advantage の推定方法:

| 推定 | バイアス | 分散 |
|---|---|---|
| TD (1 ステップ): `δ = r + γV(s') - V(s)` | 有 | 小 |
| Monte Carlo: `G - V(s)` | 0 | 大 |
| **GAE**: 上記 2 つを `λ` で補間 | 調整可能 | 調整可能 |

$$
\hat{A}_t^{GAE} = \sum_{k=0}^{\infty} (\gamma\lambda)^k \delta_{t+k}
$$

| `λ` | 性質 |
|---|---|
| 0 | 純 TD (低分散・若干バイアス) |
| 1 | 純 MC (バイアス 0・高分散) |
| **0.95 (推奨)** | MC 寄りでバイアスを少し許容、分散を激減 |

指数減衰なので、近い未来の TD Error が遠い未来より重要視される。

## LLM アラインメント用の Reward Model

ゲームならシミュレータが報酬を返す。LLM には自然なスカラー報酬がない → **Reward Model (RM)** を学習。

Bradley-Terry モデル:

$$
P(y_w > y_l | x) = \sigma(r(x, y_w) - r(x, y_l))
$$

人間の preference 比較を集めて学習。

### 課題

- 人手アノテーションが高価
- **Reward Hacking**: 方策が RM の盲点を突き、人間が好まないパターンで高スコアを得る
- **Distribution Shift**: RM は SFT の出力分布で学習されたが、PPO 後の出力は別分布

### 4 モデル構成 (RLHF)

| モデル | 役割 |
|---|---|
| **Actor** | 訓練対象の方策 (LLM) |
| **Critic** | 価値推定 (V(s)) |
| **Reference** | 凍結 SFT モデル (KL ペナルティ用) |
| **Reward Model** | 応答のスコアリング |

Actor + Critic + Reference + RM = メモリ消費が大きい (詳細は第 8 章)。

## 完全な PPO 損失

$$
L = -L^{CLIP} + c_1 \cdot L^{VF} - c_2 \cdot S[\pi_\theta]
$$

| 項 | 意味 |
|---|---|
| `L^CLIP` | クリップ付き方策勾配 (最大化) |
| `L^VF` | 価値関数の MSE |
| `S[π_θ]` | エントロピーボーナス (探索促進) |

## 実行スクリプト

### 1. `ppo_from_scratch.py` — フル実装

```bash
python ppo_from_scratch.py
```

PPO の 6 コンポーネント (forward / sampling / rollout / advantage / clipped update / training loop) を全部書いた実装。中身を完全に理解できる。

### 2. `ppo_bipedal_walker.py` — BipedalWalker

```bash
python ppo_bipedal_walker.py
```

連続制御の代表ベンチマーク。第 6 章 Actor-Critic と比較すると、PPO の安定性が際立つ。

### 3. `ppo_lunar_lander.py` — DQN との比較

```bash
python ppo_lunar_lander.py
```

第 4 章で DQN を試した LunarLander を PPO で。サンプル効率と安定性の比較。

### 4. `gae_visualization.py` — GAE λ の効果可視化

```bash
python gae_visualization.py
```

λ を変えて Advantage 推定がどう変わるか視覚化。直感を掴むのに有効。

### 5. 可視化

```bash
python render_bipedal_walker.py
```

## 重要な指標

| 指標 | 健全な範囲 |
|---|---|
| **Episode Reward Mean** | BipedalWalker 300+ で歩行成功 |
| **Policy Entropy** | 緩やかに減少。急降下は早期収束 |
| **Clip Fraction** | 5-20% (健全)。30%+ なら learning rate / batch size 問題 |
| **KL Divergence** | 小さく保つ。0.02-0.05 を持続的に超えると不安定 |
| **Value Loss** | 減少。Advantage 推定品質に直結 |

## ハイパラ感度

| パラメータ | 制御タスク | LLM タスク |
|---|---|---|
| `ε` (clip range) | 0.2 | 0.05-0.1 (LLM は壊れやすい) |
| `K` (epochs/rollout) | 10 | 1-4 |
| `learning rate` | 3e-4 | 1e-6〜1e-5 |

## よくある落とし穴

| 症状 | 原因 |
|---|---|
| Clip Fraction が爆発 | K が大きすぎてデータが off-policy |
| 訓練が不安定 | Advantage を normalize していない |
| 早期に決定論化 | Entropy ボーナスが小さすぎる |
| KL が増える一方 | learning rate が大きすぎる |

## 次のステップ

- **[Chapter 11: RLHF](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/11-rlhf)**: PPO を LLM に適用、4 モデル構成と veRL
- **[Chapter 13: 連続制御比較](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/13-continuous-control)**: PPO/TD3/SAC の並列比較

## 補足: 第 7 章のメッセージ

PPO は「シンプルな数式 1 つ (clip) で複雑な問題 (方策崩壊) を解決した」名作。

LLM 時代になって、PPO はゲーム RL の枠を超えて **LLM アラインメントの基盤アルゴリズム** になった。OpenAI の InstructGPT、Anthropic の Claude、Google の Gemini もすべて PPO ベース (派生含む)。

この章を理解すると、第 8 章以降の LLM RL がほぼ全て「PPO の応用」として読める。逆に PPO が分からないと LLM 章は記号操作にしか見えない。**RL を学ぶ上でこの章だけは必ず通る**。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, gymnasium, stable_baselines3) | OK |
| `gae_visualization.py` 完走 | OK |
| `ppo_from_scratch.py` 完走 | OK |
| `ppo_bipedal_walker.py` | 要 `stable-baselines3[extra]` 追加 |
| `ppo_lunar_lander.py` | 起動するが upstream バグで訓練前に停止 |

### 既知の問題

`ppo_lunar_lander.py` 127 行目で `TypeError: unsupported operand type(s) for -: 'int' and 'FloatSchedule'`。SB3 の `clip_range` が schedule オブジェクトに変更されたため。`1 - model.clip_range` を `1 - model.clip_range(0)` に修正すれば動く。

### 追加で必要な pip パッケージ

```bash
pip install 'stable-baselines3[extra]'
pip install 'gymnasium[box2d]'  # ppo_bipedal_walker, ppo_lunar_lander 用
```
