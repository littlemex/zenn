---
title: "Chapter 7: DQN — 深層 Q-Learning"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter04_dqn/)

第 3 章のテーブル型 Q-learning を、ニューラルネットで関数近似する **DQN** に拡張。連続状態空間 (LunarLander) や生ピクセル入力 (Atari) を扱えるようになる。

## 学習目標

- DQN の中核 3 要素 (Experience Replay / Target Network / ε-greedy) の必要性を理解
- 標準 DQN の系譜 (Double / Dueling / PER / Rainbow) を整理
- 価値の過大評価バイアス、Bootstrap、replay buffer の warm-up といった実装上の罠を把握
- LunarLander (低次元連続) と Atari (ピクセル) の両方を動かす

## 前提

- venv: `chapter04_dqn`
- ハードウェア: CartPole は CPU で十分、Atari は GPU 強く推奨 (RTX PRO 6000 で快適)
- ROM: Atari は `gymnasium[atari,accept-rom-license]` で自動取得。Pokémon Red は別途用意

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter04_dqn/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter04_dqn
```

## なぜテーブルから NN へ

| 環境 | 状態 | テーブル化 |
|---|---|---|
| GridWorld 5×5 | 25 マス | 可能 |
| CartPole | 4 次元連続 | 不可能 (無限) |
| LunarLander | 8 次元連続 | 不可能 |
| Atari | 84×84×3 ピクセル | 不可能 |

ニューラルネット `Q(s, a; θ)` で全 (s, a) の価値を近似する。

## 訓練ターゲット

TD ターゲット:

$$
y = r + \gamma \max_{a'} Q(s', a'; \theta^-)
$$

損失:

$$
L(\theta) = \mathbb{E}\big[(Q(s, a; \theta) - y)^2\big]
$$

## 安定化に必須の 3 要素

### 1. Experience Replay

`(s, a, r, s', done)` を replay buffer に保存し、ランダムサンプリングで minibatch 学習。

- 連続する遷移の相関を破る (バイアス除去)
- データ再利用で効率向上
- 序盤の失敗と終盤の成功を混ぜて学習

### 2. Target Network

同じアーキテクチャの 2 つのネット:
- **Q-network (`θ`)**: 毎ステップ勾配降下で更新
- **Target network (`θ⁻`)**: C ステップごとに `θ` をコピー (固定)

「動くターゲット」問題を防ぐ。Target がなければ NN は自分の予測を追いかけ続けて発散する。

### 3. ε-Greedy 探索

- 確率 ε でランダム行動、(1-ε) で argmax Q
- ε は学習進行に伴って減衰 (例: 1.0 → 0.05)

## DQN ファミリー

| 改良 | 解決する問題 |
|---|---|
| **Double DQN** | max 操作による系統的な過大評価 (action 選択と value 評価を分離) |
| **Dueling DQN** | `Q(s,a) = V(s) + A(s,a)` 分解で状態価値と行動 advantage を独立学習 |
| **Prioritized Experience Replay (PER)** | TD 誤差の大きいサンプルを優先サンプリング (重要なものを多く見る) |
| **n-step Returns** | 1 ステップ TD と Monte Carlo の中間 (バイアス・分散の調整) |
| **Distributional RL** | 期待値ではなく **分布全体** をモデル化 |
| **Noisy Networks** | パラメータノイズで構造化探索 (ε-greedy 不要) |

**Rainbow** はこの 6 つを統合したフレームワーク。各改良が異なるエラー源を解決:
1. 過大評価 (Double)
2. 表現 (Dueling)
3. サンプル効率 (PER)
4. 報酬伝播 (n-step)
5. 不確実性 (Distributional)
6. 探索 (Noisy)

## 内的探索 (Intrinsic Motivation)

外的報酬がスパースな環境では、エージェントが学習信号を得られない。**好奇心** ベースの追加報酬 (ICM, RND) が新規・予測困難な状態への訪問を促す。

## 実行スクリプト

### 1. `dqn_cartpole.py` — Vanilla DQN

```bash
python dqn_cartpole.py
```

Replay buffer と Target network を明示的に書いた実装。中身を理解するのに最適。

### 2. `double_dqn_cartpole.py` — Double DQN

```bash
python double_dqn_cartpole.py
```

過大評価の補正効果を観察できる。

### 3. `dqn_gym_sb3.py` — SB3 で LunarLander

```bash
python dqn_gym_sb3.py
```

実用的なハイパラの SB3 ラッパー版。**初学者の推奨スタート**。

### 4. `dqn_atari_sb3.py` — Atari (ピクセル入力)

```bash
python dqn_atari_sb3.py
```

84×84 グレースケール 4 フレームスタック、CNN encoder。Breakout 等の Atari 環境。

GPU が真価を発揮する場面。RTX PRO 6000 なら数時間で人間レベル超え。

### 5. `dqn_pokemon_red_pyboy.py` — Pokémon Red (PyBoy 経由)

```bash
python dqn_pokemon_red_pyboy.py
```

Game Boy エミュレータ PyBoy で Pokémon Red を学習。ROM は自分で用意 (リポジトリには含まれない)。

### 6. 可視化

```bash
python render_lunarlander.py
python render_atari.py
```

学習済みチェックポイントを読み込み、エージェントの挙動を動画レンダリング。

## 重要な指標

| 指標 | 期待される挙動 |
|---|---|
| **Evaluation Reward Mean** | LunarLander 200 以上で着陸成功、Atari は環境ごと |
| **Q-value 推定値** | 過大評価していないか確認 (急上昇は危険) |
| **Buffer Utilization** | 訓練開始前に十分なサンプル多様性が必要 |
| **Epsilon Schedule** | 適切な減衰で探索が枯渇しないか |

### LunarLander 成功基準

- **Score > 200**: 着陸成功 (位置・速度・姿勢・ギア接地すべて良好)
- **Score 100-200**: 着陸はしたが効率悪
- **Score < 0**: まだ学習中 or 方策崩壊

## 訓練曲線の読み方

非単調な挙動は **正常**。replay buffer の多様性、ε の減衰、関数近似の相互作用で揺らぐ。

- 平坦な期間 → 突然の上昇: replay buffer が十分に育った瞬間に多い
- 突然 0 へ崩壊: 真のバグ (target sync 周期、学習率)

## よくある落とし穴

| 症状 | 原因 |
|---|---|
| 訓練開始直後に発散 | Replay buffer の warm-up 不足 |
| Q 値がインフレする | Target network の同期頻度が高すぎる |
| 探索が早期に枯渇 | ε 減衰が速すぎる |
| 勾配のばらつきが大きい | バッチサイズが小さすぎる |

## 次のステップ

- **[Chapter 8: 方策勾配](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/08-policy-gradient)**: 価値ベースから方策ベースへ
- **[Chapter 10: PPO](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/10-ppo)**: 連続制御で DQN の限界を超える
- **[Chapter 11: RLHF](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/11-rlhf)**: 言語モデルへの応用

## 補足: 第 4 章のメッセージ

DQN は **「テーブルが NN になっても、Bellman 方程式の本質は変わらない」** ことを示す代表例。

ただし関数近似を入れた瞬間に発散リスクが増える。Experience Replay と Target Network は **オプションではなく必須**。これを抜くと現実の問題ではほぼ確実に発散する。

Atari / Pokémon Red といった「子供のゲーム」を通じて、エージェントが視覚から学習する原理を体感できるのが本章の楽しいところ。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, gymnasium, stable_baselines3, ale_py, opencv) | OK |
| `dqn_cartpole.py` 完走 | OK |
| `double_dqn_cartpole.py` 完走 | OK |
| `dqn_gym_sb3.py` | 起動成功 (要 `swanlab[dashboard]` 追加) |
| `dqn_atari_sb3.py` | (Atari ROM が必要、別途準備) |
| `dqn_pokemon_red_pyboy.py` | (Pokémon Red ROM が必要、別途準備) |

### 追加で必要な pip パッケージ

```bash
pip install 'swanlab[dashboard]'  # dqn_gym_sb3.py の SwanLab local mode 用
pip install 'autorom[accept-rom-license]' && AutoROM --accept-license  # Atari ROM
```
