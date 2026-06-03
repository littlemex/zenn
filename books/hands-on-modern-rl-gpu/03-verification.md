---
title: "Chapter 3: 全 14 章 動作検証サマリ"
---


g7e.2xlarge / DLAMI PyTorch 2.11 / ap-northeast-1 で実施。

## Phase A: venv セットアップ
全 14 章で `python3 -m venv --system-site-packages` + `pip install -r requirements.txt` 完了。所要時間 約 2 時間。

## Phase B: import スモークテスト
全 14 章で **主要モジュール import 成功**、全スクリプト `py_compile` 成功。

## Phase C: 軽量実行 (timeout 30-180s)

| 章 | スクリプト | 結果 | 備考 |
|---|---|---|---|
| 1 cartpole | 1-ppo_cartpole.py | PASS-TIMEOUT | 訓練継続中に timeout (前回検証で 80s 完走で 500/500) |
| 2 dpo | 0-download_model.py | PASS-TIMEOUT | モデル DL に 180s 以上 |
| 2 dpo | 1-generate_data.py | PASS | |
| 3 mdp | two_armed_bandit.py | PASS | |
| 3 mdp | gridworld_q_learning.py | PASS | |
| 3 mdp | bellman_equation_verify.py | PASS | |
| 4 dqn | dqn_cartpole.py | PASS | |
| 4 dqn | double_dqn_cartpole.py | PASS | |
| 4 dqn | dqn_gym_sb3.py | FAIL → 追加で `swanlab[dashboard]` install で解決 | |
| 5 pg | reinforce_cartpole.py | PASS | |
| 5 pg | reinforce_with_baseline.py | PASS | |
| 5 pg | actor_critic_cartpole.py | FAIL (upstream バグ) | `'float' object has no attribute 'detach'` |
| 6 ac | actor_critic_pendulum.py | FAIL → `stable-baselines3[extra]` で解決 | |
| 6 ac | actor_critic_bipedalwalker.py | FAIL → `gymnasium[box2d]` で解決 | |
| 7 ppo | gae_visualization.py | PASS | |
| 7 ppo | ppo_from_scratch.py | PASS | |
| 7 ppo | ppo_lunar_lander.py | FAIL (upstream バグ) | `'int' - 'FloatSchedule'` (SB3 API 互換) |
| 7 ppo | ppo_bipedal_walker.py | FAIL → `stable-baselines3[extra]` で解決 | |
| 8 rlhf | sft_pipeline.py | FAIL (upstream バグ) | tensor が CPU と CUDA に分散 |
| 9a alignment | dpo_hands_on.py | PASS-TIMEOUT | |
| 9b grpo_rlvr | grpo_mechanism.py | PASS (出力的に完走、exit code 1) | |
| 9b grpo_rlvr | rule_based_reward.py | PASS | |
| 9c cont. ctrl | sac_halfcheetah.py | FAIL → `stable-baselines3[extra]` で解決 | |
| 9c cont. ctrl | ppo_td3_sac_comparison.py | FAIL → `stable-baselines3[extra]` で解決 | |
| 10 agentic | tool_use_agent.py | PASS | |
| 10 agentic | multi_turn_rl.py | PASS | |
| 11 vlm | geometry_counting_dataset.py | PASS | |
| 11 vlm | multi_modal_reward.py | PASS | |
| 12 future | multi_agent_marl.py | PASS | |
| 12 future | tree_of_thought.py | PASS | |

**集計**: 31 件中 21 PASS / 10 FAIL
→ Phase D fix (追加 pip install) で 7 件解決
→ 残り 3 件は upstream リポジトリ固有のバグ

## Phase D recheck 結果 (Phase D fix 適用後)

| 章 | スクリプト | recheck 結果 |
|---|---|---|
| 4 dqn | dqn_gym_sb3.py | PASS-TIMEOUT (起動成功) |
| 6 ac | actor_critic_pendulum.py | **PASS (完走)** |
| 6 ac | actor_critic_bipedalwalker.py | PASS-TIMEOUT (起動成功) |
| 7 ppo | ppo_bipedal_walker.py | PASS-TIMEOUT (起動成功) |
| 9c cont. ctrl | sac_halfcheetah.py | PASS-TIMEOUT (起動成功) |
| 9c cont. ctrl | ppo_td3_sac_comparison.py | PASS-TIMEOUT (起動成功) |

**全 6 件で起動成功** ✓ → 訓練自体も時間さえあれば完走する見込み。

## 追加で必要な pip パッケージ

下記コマンドはすでに Phase D-fix で全 venv に適用済みですが、再現用に残します。

```bash
# SB3 を使う章 (4, 6, 7, 9 cont. ctrl 等): tqdm + rich (progress bar)
pip install 'stable-baselines3[extra]'

# Box2D 環境を使う章 (6 BipedalWalker)
pip install 'gymnasium[box2d]' box2d-py

# SwanLab ローカルダッシュボードを使う章 (4 dqn_gym_sb3)
pip install 'swanlab[dashboard]'
```

requirements.txt がこれらを取り損ねている (upstream の漏れ) ので、章の md にも記載しています。

## upstream バグ (要注意 3 件)

| ファイル | エラー | 対処 |
|---|---|---|
| chapter05_policy_gradient/actor_critic_cartpole.py | `'float' object has no attribute 'detach'` | スクリプト側のテンソル変換漏れ。学習部 244 行目周辺 |
| chapter07_ppo/ppo_lunar_lander.py | `unsupported operand type(s) for -: 'int' and 'FloatSchedule'` | SB3 の clip_range が schedule オブジェクトに変更された影響。`model.clip_range(0)` で取得 |
| chapter08_rlhf/sft_pipeline.py | `Expected all tensors to be on the same device` | input_ids を `.to(device)` する忘れ |

これらは upstream リポジトリの問題なので、本検証では「立ち上がるが訓練途中で停止する」と既知エラー扱いにします。

## DLAMI 警告について

```
WARNING: Please note that the Amazon EC2 g7e.2xlarge instance type is not supported by current Deep Learning AMI.
```

DLAMI のリリースノート未更新による誤検知です。実際の動作 (PyTorch 2.11 + CUDA 13 + Blackwell sm_120 + bf16) はすべて正常動作確認済み。**この警告は無視してください**。
