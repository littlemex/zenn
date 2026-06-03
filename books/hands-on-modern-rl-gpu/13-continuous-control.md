---
title: "Chapter 13: 連続制御 — PPO / TD3 / SAC の比較"
---


> 注: 元リポジトリの `docs/chapter09_continuous_control/` ディレクトリは存在しないが、`code/chapter09_continuous_control/` にコードが置かれている。本章は code を読みながら実験する形になる。

連続行動空間の代表アルゴリズム 3 つを HalfCheetah で並列比較する章。第 6 章 (Actor-Critic)、第 7 章 (PPO) の延長線上。

## 学習目標

- PPO (on-policy) / TD3 (off-policy 決定論) / SAC (off-policy 最大エントロピー) の使い分けを理解
- 連続制御ベンチマーク HalfCheetah で性能・サンプル効率・安定性を比較
- MuJoCo の物理シミュレータの基本的な扱い方

## 前提

- venv: `chapter09_continuous_control`
- ハードウェア: GPU 推奨。MuJoCo は CPU でも動くが SAC は GPU の方が圧倒的に高速
- 追加パッケージ: `gymnasium[mujoco]`

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter09_continuous_control/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter09_continuous_control
```

## 3 アルゴリズムの位置付け

| | PPO | TD3 | SAC |
|---|---|---|---|
| **方式** | On-policy 方策勾配 | Off-policy 決定論 Actor-Critic | Off-policy 最大エントロピー |
| **行動方策** | ガウシアン (確率的) | 決定論 + 探索ノイズ | ガウシアン (確率的) |
| **データ再利用** | 数 epoch | Replay buffer 全活用 | Replay buffer 全活用 |
| **サンプル効率** | 低 | 高 | 高 |
| **安定性** | 高 (clip) | 中 (Q 過大評価対策あり) | 最高 (max-ent) |
| **Hyperparameter 感度** | 中 | 高 | 低 |

### TD3 の核心: Twin Critic + Target Smoothing

DDPG の改良:
1. **Twin Critic**: Q ネットを 2 つ持ち、min を取る → 過大評価バイアス対策
2. **Delayed Policy Update**: Critic を更新してから Actor 更新 (頻度差)
3. **Target Smoothing**: target action にノイズを足してロバスト化

### SAC の核心: 最大エントロピー RL

報酬目的に **エントロピーボーナス** を加える:

$$
J(\pi) = \sum_t \mathbb{E}\big[r(s, a) + \alpha H(\pi(\cdot|s))\big]
$$

`α` は entropy 係数 (自動調整可能)。

利点:
- 探索が built-in (ε-greedy 不要)
- 同等性能の方策が複数あれば多様性を保つ
- ハイパラに鈍感

**現在の連続制御 SOTA**。

## HalfCheetah-v4 (MuJoCo)

| 項目 | 値 |
|---|---|
| 状態空間 | 17 次元連続 (関節角度・速度) |
| 行動空間 | 6 次元連続 (関節トルク) |
| 目標 | 速く前進する |
| 報酬 | 前進速度 - エネルギー消費 |
| 成功目安 | リターン > 4000 |

## 実行スクリプト

### 1. `ppo_td3_sac_comparison.py` — 3 アルゴリズム比較

```bash
python ppo_td3_sac_comparison.py
```

同じ環境 (HalfCheetah)、同じステップ数で 3 アルゴリズムを並列訓練し、学習曲線を比較。

期待される結果 (大まかに):
- **SAC**: 最も速く高いリターンに到達。安定
- **TD3**: SAC とほぼ同等、わずかに不安定
- **PPO**: ステップ数では負けるが、分散環境ではスループットで勝つことも

### 2. `sac_halfcheetah.py` — SAC 単体

```bash
python sac_halfcheetah.py
```

SAC の中身 (twin critic、entropy 自動調整、target ネット) を完全に展開した実装。

## 重要な指標

| 指標 | 説明 |
|---|---|
| **Episode Return** | 1 エピソードの累積報酬。HalfCheetah は 4000+ を目標 |
| **Sample Efficiency** | リターン X に到達するまでに必要な環境ステップ数 |
| **Wall Time** | 同じ計算時間でどれだけ進むか (PPO は高並列で勝ちやすい) |
| **Q Value Estimate (TD3/SAC)** | 過大評価していないか |

## 選び方ガイド

| 状況 | 推奨 |
|---|---|
| シミュレータが速い、サンプル豊富 | **PPO** (並列環境で WallTime 勝負) |
| 実機ロボット (サンプル高価) | **SAC** (replay 再利用で効率) |
| ハイパラ探索する暇がない | **SAC** (鈍感) |
| 決定論的な制御が要件 | **TD3** (Actor が決定論) |

## よくある落とし穴

| 症状 | 原因 |
|---|---|
| MuJoCo 環境が立ち上がらない | `pip install mujoco` 後の OS 依存 (libosmesa6 必要) |
| SAC が学習しない | reward scale の調整不足 (auto entropy が効きにくい) |
| TD3 で Q 値が爆発 | target smoothing ノイズ不足、policy update delay 短すぎ |
| PPO が遅い | parallel envs を増やせばよい (`vec_env`) |

## 次のステップ

- **[Chapter 16: VLM RL](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/16-vlm-rl)**: GRPO ベースの実装は連続制御とは違う流派
- **[Chapter 17: 今後の動向](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/17-future-trends)**: マルチエージェント連続制御

## 補足: 第 9c 章のメッセージ

LLM 系章 (DPO/GRPO/RLVR) と並ぶ「連続制御の流派」を体系的に整理する章。

LLM RL ばかりに注目が集まるが、**ロボティクス・ゲーム AI・自動運転** などでは依然として PPO/TD3/SAC が現役。SAC は特に実機データの少ないドメインで重宝される。

3 アルゴリズムの設計判断 (on/off policy、決定論/確率的、エントロピー) を理解すると、新しい問題で **どのアルゴリズムを試すべきか** の判断軸が手に入る。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, gymnasium[mujoco], stable_baselines3) | OK |
| `sac_halfcheetah.py` | 要 `stable-baselines3[extra]` 追加 |
| `ppo_td3_sac_comparison.py` | 要 `stable-baselines3[extra]` 追加 |

`HalfCheetah-v4` は deprecated 警告が出るが動作には影響なし (`v5` が推奨)。

### 追加で必要な pip パッケージ

```bash
pip install 'stable-baselines3[extra]'
```
