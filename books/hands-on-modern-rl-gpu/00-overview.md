---
title: "概要 — この book で何ができるか"
---

[walkinglabs/hands-on-modern-rl](https://github.com/walkinglabs/hands-on-modern-rl) は、CartPole の倒立振子から VLM (Vision-Language Model) の強化学習まで、現代 RL の主要トピックを 14 章で体系的に学べるオープン教材です。本 book はそれを **AWS の最新 GPU インスタンス (g7e.2xlarge / NVIDIA RTX PRO 6000 Blackwell 96GB)** 上で動かすための、**実機検証済みハンズオンガイド** です。

## 何を提供するか

- **環境構築**: CDK ベースで EC2 + EFS + Cognito + CloudFront を一発デプロイ
- **章ごとの venv セットアップ**: 14 章分のセットアップを Task Runner (SSM Run Command 経由) で自動化
- **動作検証**: 全 14 章で `import` / `py_compile` / 軽量実行を実施し、つまずきポイントをすべて記録
- **日本語チュートリアル**: 各章の理論サマリと実行コマンドを章別に提供

## なぜ AWS GPU で動かすか

walkinglabs/hands-on-modern-rl は CartPole こそ CPU でも動きますが、**LLM 系の章 (RLHF, DPO, GRPO, VLM RL)** は GPU 必須です。本書のセットアップは:

- **g7e.2xlarge**: NVIDIA RTX PRO 6000 Blackwell (96 GB VRAM, sm_120, bf16 対応)
- **DLAMI PyTorch 2.11 (Ubuntu 24.04)**: Driver 595, CUDA 13.2 同梱
- **EFS 永続化**: `/home/coder` と `/work` を EFS にマウントし、インスタンス再作成しても作業継続可能

### 96 GB VRAM で何ができるか

| クラス | 例 | 動作 |
|---|---|---|
| 〜0.5B | Qwen2.5-0.5B (RLHF / DPO / GRPO) | 余裕 |
| 〜2B | Qwen2-VL-2B (VLM RL) | 余裕 |
| 〜7B | LoRA + bf16 | 動作可 |
| ベース 7B 級 | フル fine-tuning | 厳しい (LoRA 推奨) |

## 本 book の構成

### Part 1: 環境構築 (Chapter 1-3)

| Chapter | 内容 |
|---|---|
| [Chapter 1: SETUP](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/01-setup) | CloudFront / Cognito ログイン、SSM ポート転送、code-server アクセスの 3 経路 |
| [Chapter 2: clone & venv](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/02-clone-venv) | hands-on-modern-rl の clone、章ごと venv セットアップ |
| [Chapter 3: 動作検証結果](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/03-verification) | 全 14 章 31 スクリプトの実機検証マトリックス |

### Part 2: 古典 RL (Chapter 4-10)

| Chapter | テーマ | 主要環境 |
|---|---|---|
| [Chapter 4: CartPole](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/04-cartpole) | RL の Hello World、PPO で 500/500 達成 | CartPole-v1 |
| [Chapter 5: DPO](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/05-dpo) | 嗜好データを使った LLM ファインチューニング | Qwen2.5-0.5B |
| [Chapter 6: MDP](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/06-mdp) | Bellman 方程式・価値関数・Q-learning | GridWorld / Bandit |
| [Chapter 7: DQN](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/07-dqn) | 深層 Q-Learning、Atari、Pokémon Red | Atari, PyBoy |
| [Chapter 8: 方策勾配](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/08-policy-gradient) | REINFORCE と Baseline | CartPole |
| [Chapter 9: Actor-Critic](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/09-actor-critic) | 連続制御 (Pendulum, BipedalWalker) | gymnasium[box2d] |
| [Chapter 10: PPO](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/10-ppo) | クリップ付き方策勾配の現代的標準 | LunarLander, BipedalWalker |

### Part 3: LLM 時代の RL (Chapter 11-15)

| Chapter | テーマ |
|---|---|
| [Chapter 11: RLHF](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/11-rlhf) | InstructGPT スタイルの 3 段階パイプライン (TRL + veRL) |
| [Chapter 12: アラインメント手法 (DPO ファミリー)](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/12-alignment) | DPO / KTO / SimPO / IPO の使い分け |
| [Chapter 13: 連続制御比較](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/13-continuous-control) | PPO / TD3 / SAC を HalfCheetah で並列比較 |
| [Chapter 14: GRPO / RLVR](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/14-grpo-rlvr) | DeepSeek-R1 系の推論強化 (Critic 不要 + RM 不要) |
| [Chapter 15: Agentic RL](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/15-agentic-rl) | ツール使用エージェント、軌跡合成、PRM |

### Part 4: フロンティア (Chapter 16-17)

| Chapter | テーマ |
|---|---|
| [Chapter 16: VLM RL](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/16-vlm-rl) | 視覚言語モデルの GRPO 訓練 (図形カウントタスク) |
| [Chapter 17: 今後の動向](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/17-future-trends) | Embodied / Multi-Agent / 推論時計算スケーリング |

## 推奨される読み進め方

### A. 古典 RL から入る

> Chapter 1-3 (環境構築) → Chapter 4 (CartPole) → Chapter 6 (MDP) → Chapter 7 (DQN) → Chapter 10 (PPO)

理論の系譜を追いたい方向け。各手法が前の手法の何を解決したか、を実装で体感できる。

### B. LLM 系から入る

> Chapter 1-3 (環境構築) → Chapter 5 (DPO 入門) → Chapter 11 (RLHF) → Chapter 14 (GRPO/RLVR)

DeepSeek-R1 の系譜が知りたい方向け。最短で「現代 LLM RL のレシピ」に到達できる。

### C. 各章のスクリプトをすべて動かす

Chapter 1-3 でセットアップを終えたら、興味のある章を `python <script>.py` で逐次実行。Chapter 3 (動作検証結果) でどのスクリプトが何分で動くかわかります。

## 動作検証サマリ (2026-06-03)

| 検証項目 | 結果 |
|---|---|
| venv セットアップ (14 章) | 全章 OK |
| `import` smoke (主要モジュール × 14 章) | 全 OK |
| `py_compile` (60+ スクリプト) | 全 OK |
| 軽量実行 (31 スクリプト) | 27 起動成功 / 3 upstream バグ (詳細は Chapter 3) |

詳細は [Chapter 3: 動作検証結果](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/03-verification) を参照。

## 関連リポジトリ

- 上流: [walkinglabs/hands-on-modern-rl](https://github.com/walkinglabs/hands-on-modern-rl) — 教材本体
- AWS CDK 基盤: [littlemex/aws-neuron-samples](https://github.com/littlemex/aws-neuron-samples) (`setup/single-node`) — Trainium 用だが GPU 化フォーク済み
- 検証済みデプロイ環境: [littlemex/data-science](https://github.com/littlemex/data-science) (`claudecode/investigations/hands-on-modern-rl-gpu`)

## 動作環境メモ

| 項目 | 値 |
|---|---|
| Region | ap-northeast-1 (Tokyo) |
| Instance | g7e.2xlarge (96 GB VRAM, 8 vCPU) |
| AMI | Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.11 (Ubuntu 24.04) |
| GPU | NVIDIA RTX PRO 6000 Blackwell Server Edition (sm_120, bf16 対応) |
| PyTorch | 2.11.0+cu130 (一部 venv で 2.12.0+cu130) |
| 永続化 | EFS (`/home/coder`, `/work`) |

それでは、[Chapter 1: 環境への接続手順](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/01-setup) から始めましょう。
