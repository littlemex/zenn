---
title: "Chapter 16: VLM RL — 視覚言語モデルの強化学習"
---


[原文 (英語)](https://walkinglabs.github.io/hands-on-modern-rl/chapter11_vlm_rl/)

GRPO を画像入力を持つ VLM に適用し、**視覚理解と推論を同時に向上** させる章。テキスト RL からマルチモーダルへの拡張。

## 学習目標

- VLM RL 特有の 4 課題 (credit attribution / hallucination / encoder degradation / action grounding) を理解
- GRPO の VLM 適用 (入力に画像トークンが含まれるだけ) を体感
- 微分学習率 (vision encoder vs text decoder) の重要性を把握
- 多次元報酬 (correctness + reasoning + format) でハルシネーション抑制を実装

## 前提

- venv: `chapter11_vlm_rl`
- ハードウェア: GPU 必須。Qwen2-VL-2B でも 24-32 GB VRAM、7B なら 60+ GB。RTX PRO 6000 (96GB) 推奨
- 追加: `transformers==4.57.3`, `Pillow`、必要に応じて `trl`, `accelerate`

## 環境のアクティベート

```bash
source /home/coder/venvs/chapter11_vlm_rl/bin/activate
cd /home/coder/work/hands-on-modern-rl/code/chapter11_vlm_rl

# trl が requirements に無いので追加
pip install trl==0.24.0 datasets==4.4.1 accelerate==1.10.1
```

## 中核問題

| 観点 | テキスト RL | VLM RL |
|---|---|---|
| 最適化対象 | `P(answer | text_prompt)` | `P(answer | image + text_prompt)` |

これにより 4 つの新しい課題:

### 1. Credit Attribution

VLM が間違った答えを出したとき、原因はどこか?
- Vision encoder が画像を誤認識?
- Language decoder が正しい知覚から誤推論?

別々のコンポーネントなので **別々のデバッグ** が必要。

### 2. Visual Hallucination

VLM は画像にないものを記述する。RL 訓練で「正解報酬」だけを与えると、ハルシネートした内容から推論して偶然正解した応答も強化される → **当て推量を学習** してしまう。

### 3. Vision Encoder Degradation

RL 勾配が pre-trained 視覚特徴を上書きしてしまう。視覚 encoder の表現は大規模事前学習で苦労して獲得したもので、大きな勾配更新で簡単に壊れる。

### 4. Action Grounding (自動運転等)

視覚解釈と現実世界の意思決定 (車両制御) を結びつける必要。抽象推論を視覚観測に grounding して具体行動に翻訳する学習。

## VLM-GRPO の訓練アプローチ

第 9b 章の GRPO を直接適用、ただし入力 batch に **画像トークン + テキストトークン** が含まれる点が違う。

### アーキテクチャ

```
[Image] → Vision Encoder (ViT) → 視覚トークン
                                       ↓
                                Projection Layer (LLM 埋め込み空間に投影)
                                       ↓
[Text] → Tokenizer → テキストトークン → Language Decoder → 応答
```

### なぜ GRPO が機能するか

(image, question) ペアあたり k 個の応答を生成し、group 内 normalize。これにより:
- 画像の難易度が変動しても自然に適応 (難しい画像は group 全体の reward が低い)
- normalize で訓練信号は意味を保つ

## 微分学習率 (最重要実装ノウハウ)

**VLM RL の最重要実装詳細**:

| コンポーネント | 学習率 |
|---|---|
| **Text Decoder** | 1e-6 (基準) |
| **Projection Layer** | 中間 (1e-7 ぐらい) |
| **Vision Encoder** | **基準の 1/10** (1e-7) |

理由: 視覚特徴は事前学習で獲得済み・安定。aggressive な RL 更新で破壊しないよう、低 LR で慎重に扱う。Text decoder は fine-tuning でより plastic。

### 代替: Vision Encoder を完全凍結

視覚知覚が既に強い場合は freeze してしまう方が安全。

## ハンズオン: 図形カウント

### タスク

- 入力: 三角形・円・四角を含む合成画像
- 質問: 「この画像に円はいくつ?」
- データ生成: `geometry_counting_dataset.py`

### 3 次元報酬

| 次元 | 報酬 | 役割 |
|---|---|---|
| **Correctness** | +1.0 | 数値答えが gold と一致 |
| **Reasoning Quality** | +0.5 | 視覚内容の記述あり (grounding 検証) |
| **Format Compliance** | +0.2 | 「説明 → 推論 → 答え」構造を守る |

### 訓練で何が起きるか

- 訓練前: 画像を見ず当て推量 → 偶然正解
- 訓練後: **画像の内容を明示的に記述してから答えを導く** 構造化された推論

「視覚記述してから答える」という挙動が、報酬構造だけから **emergent に獲得** される (CoT 例を SFT で見せていない)。

### Hallucination 抑制の仕組み

`reasoning quality` 報酬 (+0.5) が「画像内容を実際に記述しろ」というプレッシャーを生む。当て推量で正解しても +1.0 だけ、画像記述すれば +1.5。**正直な視覚分析が profitable** になる。

## 業界フレームワーク

| フレームワーク | 特徴 |
|---|---|
| **EasyR1** | 教育向けミニマル VLM RL |
| **LLaVA-RLHF** | LLaVA の RLHF パイプライン |
| **InternVL-GRPO** | 産業規模、大パラメータ向け |

## 自動運転への応用

VLM-RL を自動運転に適用する際の課題は **探索と安全のトレードオフ**:

- Pure RL exploration (試行錯誤) は物理システムで危険
- 解決策:
  - シミュレーションでの事前訓練
  - Hard constraint 付き制約最適化
  - 多目的報酬: 安全 + 快適 + 効率

## 視覚生成 RL

理解だけでなく **生成** にも RL が使える (diffusion / autoregressive image generation):
- 報酬: 美的 preference、構造正しさ、意味整合性
- 課題: 生成の離散 vs 連続行動空間

## 実行スクリプト

### 1. データセット生成

```bash
python geometry_counting_dataset.py
```

合成画像 + 質問 + gold 答えを生成。

### 2. VLM-GRPO 訓練

```bash
python vlm_grpo_train.py
```

3 次元報酬での GRPO 訓練ループ。

### 3. 多次元報酬の実装

```bash
python multi_modal_reward.py
```

Correctness + reasoning quality + format compliance の各報酬関数。再利用可能。

## 重要な指標

| 指標 | 期待される挙動 |
|---|---|
| **Answer Accuracy** | 数値答えの正解率。主指標 |
| **Description Rate** | 画像内容を記述している応答の割合 (grounding proxy) |
| **Format Compliance** | 期待構造に従う割合 |
| **Hallucination Rate** | 画像にない物体を記述する割合。減少すべき |
| **Vision Encoder Gradient Norm** | 爆発・崩壊なし (LR 問題のサイン) |

## よくある落とし穴

| 症状 | 原因 |
|---|---|
| ハルシネーションが増える | Vision encoder の LR が高すぎ → 視覚特徴破壊 |
| テキスト先行で画像を見ない | Grounding 報酬がない (correctness only) |
| 視覚トークンがテキスト空間に整列しない | Projection layer 凍結 |
| 答え形式だけ最適化、視覚推論せず | 報酬が一様 (correctness のみ) |

## 次のステップ

- **[Chapter 17: 今後の動向](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/17-future-trends)**: マルチエージェント、推論時計算 scaling
- 視覚生成 RL は別領域 (Diffusion + RLHF / DDPO 等)

## 補足: 第 11 章のメッセージ

「**アルゴリズム (GRPO) は変わらない、入力が変わるだけ**」というシンプルさが本章の出発点。

ただし実装では **微分学習率** と **多次元報酬** という 2 つのノウハウが決定的に重要。これを抜くと「画像を見ずに当て推量する VLM」が出来上がる。

VLM RL は LLM RL の自然な拡張だが、debugging 難度は段違いに上がる。「答えが間違っている」ときに視覚なのか言語なのか判別する診断スキルが必要。**観察可能性 (visible reasoning chain)** を報酬に組み込むのが現実解。

## 動作検証メモ (2026-06-03 / g7e.2xlarge)

| 項目 | 結果 |
|---|---|
| 主要 import (torch, transformers, PIL) | OK |
| `geometry_counting_dataset.py` 完走 | OK (合成画像生成) |
| `multi_modal_reward.py` 完走 | OK |
| `vlm_grpo_train.py` syntax | OK |

requirements.txt に `trl` が含まれていないので、`vlm_grpo_train.py` 実行時は別途 install:

```bash
pip install trl==0.24.0 datasets==4.4.1 accelerate==1.10.1
```
