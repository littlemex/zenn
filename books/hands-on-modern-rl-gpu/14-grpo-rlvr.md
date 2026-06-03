---
title: "Chapter 14: GRPO と RLVR — 推論強化の現代的標準"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter09_grpo_rlvr/)

DeepSeek-R1 が示した「**SFT 不要、純 RL で推論能力が emergent**」を支える 2 つの技術: GRPO (Critic 除去) と RLVR (RM 除去) を学ぶ章。

## 学習目標

- GRPO の group-relative normalization が Critic の代わりになる仕組みを理解
- RLVR で人手アノテーションを排除する条件と適用領域を把握
- DAPO の 4 改良 (asymmetric clip / dynamic sampling / token loss / smooth length penalty) を整理
- 数学推論やツール呼び出しに RLVR + GRPO を適用できる

## 前提

- venv: `chapter09_grpo_rlvr`
- ハードウェア: GPU 必須。Qwen2.5-0.5B + GRPO で 16-24 GB VRAM
- 第 7 章 (PPO)、第 8 章 (RLHF)、第 9a 章 (DPO ファミリー) の前提知識

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter09_grpo_rlvr/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter09_grpo_rlvr
```

## GRPO: Critic 除去

標準 PPO は Critic ネット (V(s) 推定) が必要 → LLM スケールではメインモデルと同サイズの第 2 モデルを別途持つ → メモリが倍増。

GRPO の解決: Critic を **group-relative normalization** で置き換える。

### 動作

```
1. 同じプロンプトに対して k 個の応答を現方策で生成
2. 各応答を報酬関数 (ルール or RM) でスコアリング
3. group 内統計を計算: 平均 μ, 標準偏差 σ
4. 各応答の Advantage:  Â_i = (r_i - μ) / σ
5. PPO 風のクリップ付き更新を Advantage に適用
6. policy のみ更新 (Critic 更新なし)
```

### 核心アイデア

「同じ group 内の応答同士で比較すれば、自動的に **相対的な baseline** が得られる」

- 難問で全応答が低スコア → group 内 normalize で訓練信号は維持
- 易しい問題で全応答が高スコア → 同上

### メモリ削減

PPO-RLHF 比で **〜30-40% の VRAM 節約**。LLM スケールでは決定的な差。

## RLVR: 報酬モデル除去

RLHF の RM は: 人手アノテーション必要、主観的、distribution shift が起きる。

RLVR は **正解が客観的に判定可能なドメイン** で RM を **ルールベース検証** に置き換える:

| ドメイン | 検証方法 |
|---|---|
| **数学** | `\boxed{...}` から答え抽出、正解と比較 |
| **コード** | テストケースで実行、pass/fail |
| **論理証明** | 形式検証ソルバ |
| **構造化タスク** | JSON validity、API スキーマ |

### 利点

- アノテーション 0 円
- 決定論的 (主観なし)
- 分布シフトなし (検証ルールは固定)
- 任意のデータ量にスケール可能

### 1-shot RLVR

研究結果: たった **1 例** の訓練でも RLVR が有効に機能する。

解釈: 「RL は新しい能力を教えるのではなく、**事前学習で既に持っている能力をアンロック**する」。推論能力は潜在しており、RLVR がそれを信頼できる戦略へと組織化する。

## DeepSeek-R1-Zero: SFT 不要の RL

最も驚いた結果: **base モデルから直接** GRPO + RLVR で訓練 (SFT なし)。

### 監督なしで創発したもの

- 長い chain-of-thought (CoT) 推論が **自発的に** 出現
- 自己訂正、エラーチェックの行動
- 問題タイプによる戦略切り替え
- 訓練ステップ 500-1000 付近で **breakthrough moment** (高度な推論が突然現れる)

### 解釈

事前学習 = 推論の素地、RL = 潜在能力の信頼できる戦略への組織化。

## DAPO: 4 つの工学的改良

DeepSeek-V3 で導入された GRPO の改善版:

| 改良 | 内容 |
|---|---|
| **Asymmetric clipping** | 強化方向 (保守的) と抑制方向 (積極的) で異なるクリップ。低確率高報酬の探索を促進 |
| **Dynamic sampling** | 既習得問題 (k 応答全部正解) をフィルタ、本当に難しい例に集中。**訓練効率 2-3×** |
| **Token-level loss** | 全応答平均ではなくトークン位置で重み付け。長文応答での credit assignment 改善 |
| **Smooth length penalty** | hard truncation の代わりに滑らかな長さペナルティ。長さ崩壊防止と勾配連続性の両立 |

結果: AIME 2024 で 50 点を、ベースライン DeepSeek-R1 の **半分の訓練ステップ** で達成。

## ハンズオン: 数学推論への GRPO

### 訓練ループ

```
1. k=8 応答を 1 問ごとサンプル
2. 各応答から答え抽出 (boxed 記法のパース)
3. gold 答えと照合 (報酬 0 or 1)
4. group 内で Advantage normalize
5. クリップ付き PPO ロスで更新
```

### 創発的挙動

binary 報酬 (正解/不正解) のみで訓練しても、step-by-step 推論パターンが **CoT を見せていないのに** 自発的に発達する。これが本章のクライマックス。

## ハンズオン: 金融ツール呼び出し GRPO

### タスク

3 つの金融 API: 株価、収益、通貨換算。

### 4 次元の検証報酬

| 次元 | 内容 |
|---|---|
| **JSON validity** | 整形正しいか |
| **正しい関数選択** | 適切なツールを呼んでいるか |
| **パラメータスキーマ** | 引数名・型が正しいか |
| **実行成否** | 実際に call が成功するか |

### SFT との違い

SFT は形式模倣を教えるだけ。GRPO は **k 個の候補出力を比較** することで、エージェントに「実行可能 vs 失敗」を区別させる。

## 実行スクリプト

### 1. `grpo_math_reasoning.py`

```bash
python grpo_math_reasoning.py
```

GRPO + RLVR で数学推論を訓練。完全なループ。

### 2. `grpo_mechanism.py`

```bash
python grpo_mechanism.py
```

GRPO の核 (group normalization) を最小限の実装で見せる。中身を理解するのに最適。

### 3. `rule_based_reward.py`

```bash
python rule_based_reward.py
```

数学・コード・JSON 検証用の再利用可能な報酬関数。

## 重要な指標

| 指標 | 期待される挙動 |
|---|---|
| **Pass@1** | 1 番目の応答が正解する確率。推論タスクの主指標 |
| **Group Mean Reward** | 訓練進行で増加 |
| **Reward Variance Within Groups** | 全部同じスコア → DAPO の dynamic sampling でフィルタ対象 |
| **Response Length 分布** | 長さ崩壊 (token-level 報酬を稼ぐため超長応答) を監視 |
| **Clip Fraction** | PPO と同じ。5-20% が健全 |

## よくある落とし穴

| 症状 | 原因 |
|---|---|
| group normalization が不安定 | k が小さすぎ (k < 4) |
| 全応答が報酬 0 | 検証が厳しすぎ |
| 形式だけ最適化されて中身が伴わない | 検証が緩すぎ |
| 簡単な例ばかり訓練に出てくる | dynamic sampling 不使用 |

## 次のステップ

- **[Chapter 15: Agentic RL](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/15-agentic-rl)**: 多ターン・ツール使用への拡張
- **[Chapter 16: VLM RL](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/16-vlm-rl)**: 視覚言語モデルへの GRPO 適用

## 補足: 第 9b 章のメッセージ

DeepSeek-R1 で「LLM RL のレシピ」が決定的に変わった章。

GRPO + RLVR の組み合わせは **「人手アノテーション 0、Critic 0、SFT もスキップ可能」** という極端な簡素化を実現した。これが業界全体に波及し、**推論強化のデファクト** になっている (Qwen-Math, MetaMath, OpenR1 なども同じ系統)。

ただし RLVR が効くのは **「正解が機械検証可能なドメイン」** だけ。創作、感情の機微、価値観のアラインメントなど主観的タスクには使えない。**RLVR (検証可能) + DPO/RLHF (主観的) を併用** が業界の進化の方向性。

「シンプルな改善 1 つで業界が動く」典型例として、エンジニアリング感覚を磨くのに最適な章。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, transformers, trl, datasets, accelerate) | OK |
| `grpo_mechanism.py` 完走 (報酬関数デモ) | OK (出力は正常) |
| `rule_based_reward.py` 完走 | OK |
| `grpo_math_reasoning.py` syntax | OK |

`grpo_math_reasoning.py` はモデルダウンロード + GRPO 訓練で時間がかかる (30 分以上)。
