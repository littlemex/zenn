# LLM Fine-tuning フレームワーク詳細比較レポート

## 概要

本レポートは、[Daily Dose of Data Science](https://blog.dailydoseofds.com/p/top-4-llm-fine-tuning-frameworks)の記事で紹介されている4つのLLM fine-tuningフレームワークについて、Parallelism対応状況を中心に詳細調査した結果をまとめたものです。

調査対象:
1. **Unsloth** (42k⭐)
2. **Axolotl** (10k⭐)
3. **LlamaFactory** (54k⭐)
4. **DeepSpeed** (39k⭐)

---

## 1. Unsloth (42k⭐)

### 基本コンセプト
単一GPU向けに最適化された高速・省メモリfine-tuningフレームワーク。Tritonカーネルによる手書き最適化で、2倍の速度向上と最大80%のVRAM削減を実現。

### Parallelism対応状況

| 並列化手法 | 対応状況 | 詳細 |
|-----------|---------|------|
| **DDP (Distributed Data Parallel)** | ✅ 実験的 | 2024年末に実装。モデルがシングルGPUに収まることが前提。バッチサイズ=1の制限あり |
| **FSDP (Fully Sharded Data Parallel)** | ❌ 未対応 | - |
| **Tensor Parallelism** | ❌ 未対応 | - |
| **Pipeline Parallelism** | ❌ 未対応 | - |
| **Model Parallelism** | ❌ 未対応 | - |
| **Context/Sequence Parallelism** | ❌ 未対応 | - |
| **3D Parallelism** | ❌ 未対応 | - |

### MoE対応
✅ **基本対応**
- Mixtral 8x7B、Qwen-MoEなどのMoEアーキテクチャをサポート
- MoE専用の最適化は限定的

### 独自機能
- **Tritonカーネル最適化**: 手書きGPUカーネルによる高速化
- **RoPE & MLP Triton Kernels**: 3倍高速、30% VRAM削減
- **Padding Free + Packing**: メモリ効率向上
- **FP8 Reinforcement Learning**: コンシューマーGPUでのRL実現
- **500K Context対応**: 80GB GPUで20BモデルをQwen1.5の長文学習可能
- **TTS & Vision対応**: Text-to-SpeechとVisionモデルのサポート
- **動的4-bit量子化**: 精度を保ちながら<10% VRAM増でBnB 4-bitより高精度

### 制約事項
- `device_map="balanced"`とDDPの同時使用不可
- マルチGPU対応は最適化途上
- HybridSharding、3D Parallelismは未サポート

### 適用場面
- 12-24GB GPUでの個人研究者や小規模チーム
- LoRA/QLoRAでの高速実験
- シングルGPUで完結する作業
- TTS、Visionなど多様なモダリティの実験

---

## 2. Axolotl (10k⭐)

### 基本コンセプト
YAML設定ファイルで全パイプラインを管理するオールインワンツール。再現性と柔軟性を重視した設計で、研究から本番環境まで対応。

### Parallelism対応状況

| 並列化手法 | 対応状況 | 詳細 |
|-----------|---------|------|
| **DDP** | ✅ 完全対応 | - |
| **FSDP (ZeRO-2/ZeRO-3)** | ✅ 完全対応 | reshard_after_forwardの制御可能 |
| **HSDP (Hybrid Sharded Data Parallel)** | ✅ 完全対応 | ノード内FSDP + ノード間DDP |
| **Tensor Parallelism (TP)** | ✅ 完全対応 | Megatron-LMスタイル |
| **Context/Sequence Parallelism (CP)** | ✅ 完全対応 | Ring-Flash-Attention実装、最大10倍長いシーケンス処理可能 |
| **Pipeline Parallelism** | ❌ 未対応 | - |
| **3D Parallelism** | ✅ 完全対応 | FSDP + TP + CPの組み合わせ可能 |

### 対応マトリックス

| 構成 | dp_replicate | dp_shard | tp_size | cp_size | 対応 |
|------|--------------|----------|---------|---------|------|
| FSDP | 1 | >1 | 1 | 1 | ✅ |
| HSDP | >1 | >1 | 1 | 1 | ✅ |
| FSDP+TP | 1 | >1 | >1 | 1 | ✅ |
| HSDP+TP | >1 | >1 | >1 | 1 | ✅ |
| FSDP+CP | 1 | >1 | 1 | >1 | ✅ |
| FSDP+TP+CP | 1 | >1 | >1 | >1 | ✅ |
| DDP+TP/CP | >1 | 1 | >1 | >1 | ❌ |

### MoE対応
❌ **未対応**
- 公式ドキュメントに「No MoE support」と明記
- LlamaやMistralなど密なモデルのみ対応

### 独自機能
- **Curriculum Learning**: データサンプリング効率化、最大2倍のデータ・時間節約
- **Random Layerwise Token Dropping**: メモリ効率化
- **Multi-packing**: バッチ効率向上
- **Preference Tuning**: DPO, IPO, KTO, ORPO, RM対応
- **YAML設定ベース**: 再現性の高い実験管理
- **FlashAttention, XFormers統合**: 最新最適化技術
- **Docker/PyPI対応**: 環境構築の簡素化

### 適用場面
- 再現性を重視するチーム開発
- 複雑な並列化戦略が必要なプロジェクト（HSDP、3D Parallelism）
- 長文処理（Context Parallelism）が必要な場合
- マルチノードクラスターでの大規模学習

---

## 3. LlamaFactory (54k⭐)

### 基本コンセプト
GUI（LlamaBoard）を提供するno-code/low-codeプラットフォーム。初心者にも使いやすく、100以上のモデルをサポート。

### Parallelism対応状況

| 並列化手法 | 対応状況 | 詳細 |
|-----------|---------|------|
| **DDP (NativeDDP)** | ✅ 完全対応 | torchrun、accelerate、llamafactory-cliで起動可能 |
| **DeepSpeed** | ✅ 完全対応 | ZeRO-0, ZeRO-1, ZeRO-2, ZeRO-3対応。offload設定も可能 |
| **FSDP** | ✅ 完全対応 | accelerateを通じてサポート。CPU offloadも可能 |
| **Tensor Parallelism** | ❓ 不明 | DeepSpeed経由で可能性あり |
| **Pipeline Parallelism** | ❓ 不明 | DeepSpeed経由で可能性あり |
| **Multi-node/Multi-GPU** | ✅ 対応 | シングルマシン、マルチノード両方対応 |

### MoE対応
✅ **対応（推定）**
- Mixtralなどのモデルをサポート
- 明示的なMoE最適化の記述は限定的

### 独自機能
- **LlamaBoard GUI**: Webベースのノーコードインターフェース
- **ワンクリックデプロイ**: OpenAI-style APIやvLLM workerの即時起動
- **統合ダッシュボード**: W&B、MLflow組み込み
- **100+モデル対応**: 最も広範なモデルサポート
- **最新研究統合**: FlashAttention-2, LongLoRA, GaLore, DoRA組み込み
- **16-bit/Freeze-tune/LoRA/QLoRA対応**

### 適用場面
- GUI操作を好むユーザー
- 迅速なプロトタイピング
- ダッシュボード（W&B、MLflow）との統合が必要な場合
- 標準的なDDP/FSDP/DeepSpeedで十分な場合

---

## 4. DeepSpeed (39k⭐)

### 基本コンセプト
Microsoftが開発した分散学習エンジン。超大規模モデルの学習・推論に特化し、1兆パラメータ超のモデルに対応。

### Parallelism対応状況

| 並列化手法 | 対応状況 | 詳細 |
|-----------|---------|------|
| **Data Parallelism (DDP)** | ✅ 完全対応 | - |
| **ZeRO-1** | ✅ 完全対応 | Optimizer stateのみを分割 |
| **ZeRO-2** | ✅ 完全対応 | Optimizer + Gradientsを分割 |
| **ZeRO-3** | ✅ 完全対応 | Optimizer + Gradients + Parametersを分割 |
| **ZeRO-Offload** | ✅ 完全対応 | CPU/NVMeメモリへのoffload |
| **Tensor Parallelism** | ✅ 完全対応 | Megatron-LMスタイル |
| **Pipeline Parallelism** | ✅ 完全対応 | GPipeスタイル、マイクロバッチによるバブル最小化 |
| **3D Parallelism** | ✅ 完全対応 | Data + Tensor + Pipeline Parallelismの組み合わせ |
| **Mixture of Experts (MoE)** | ✅ 完全対応 | 専用のDeepSpeed-MoE |

### MoE対応
✅ **最高レベル**
- **DeepSpeed-MoE**: 専用のMoEトレーニング・推論システム
- **1.6兆パラメータ**: Switch Transformerのトレーニング実績
- **5つの並列化形態**:
  - E (Expert): 専門家の数でスケール
  - E + D: Expert + Data Parallel
  - E + Z: Expert + ZeRO
  - E + D + M: Expert + Data + Model Parallel
  - E + Z-Off + M: Expert + ZeRO-Offload + Model
- **Random Token Selection**: 収束改善技術
- **MoE専用圧縮**: 最大3.7倍のモデルサイズ削減
- **高速推論**: 既存MoE推論の7.3倍高速

### 独自機能
- **ZeRO-Infinity**: CPU/NVMeメモリの活用で無限スケール
- **1-bit Adam/1-bit LAMB**: 通信量を最大26倍削減
- **ZeroQuant & XTC圧縮**: モデル圧縮技術
- **Sparse Attention**: 長文処理の効率化（6倍高速）
- **Progressive Layer Dropping**: 2.5倍の収束加速
- **カスタム推論カーネル**: サブ秒レイテンシ実現
- **Data Efficiency Library**: Curriculum learning & Token dropping

### 適用場面
- 100億パラメータ以上のモデル学習
- 複数ノードにまたがるクラスター
- 最高のスケーラビリティが必要な場合
- MoEモデルの本格的なトレーニング・推論
- エンタープライズレベルの本番環境

---

## フレームワーク併用性

### Unsloth + Axolotl統合
✅ **公式サポートあり**

AxolotlがUnslothの統合を公式提供しています。

**設定例:**
```yaml
# Unslothの最適化を有効化
unsloth_lora_mlp: true
unsloth_lora_qkv: true
unsloth_lora_o: true
unsloth_cross_entropy_loss: true
unsloth_rms_norm: true
unsloth_rope: true
```

**制約事項:**
- シングルGPUのみ（マルチGPU不可）
- DeepSpeedやFSDP不可
- LoRA + QLoRAのみ（Full fine-tune不可）
- 対応モデル: Llama、Phi、Gemma、Mistralのみ
- MoE未対応

### その他のフレームワーク併用

基本的に各フレームワークは独立したツールですが、以下の組み合わせが可能です:

| 組み合わせ | 可能性 | 詳細 |
|----------|-------|------|
| **LlamaFactory + DeepSpeed** | ✅ | LlamaFactoryがDeepSpeedをバックエンドとしてサポート |
| **Axolotl + DeepSpeed** | ✅ | Axolotlも設定でDeepSpeed統合可能 |
| **LlamaFactory + Unsloth** | ❌ | 直接統合なし |
| **異なるステージでの使用** | ✅ | 例: UnslothでLoRA学習 → DeepSpeedで推論 |

---

## 総合比較表

### Parallelism対応

| 機能 | Unsloth | Axolotl | LlamaFactory | DeepSpeed |
|------|---------|---------|--------------|-----------|
| **DDP** | ✅ 実験的 | ✅ | ✅ | ✅ |
| **FSDP/ZeRO-2** | ❌ | ✅ | ✅ | ✅ |
| **FSDP/ZeRO-3** | ❌ | ✅ | ✅ | ✅ |
| **Tensor Parallelism** | ❌ | ✅ | ❓ | ✅ |
| **Pipeline Parallelism** | ❌ | ❌ | ❓ | ✅ |
| **Context Parallelism** | ❌ | ✅ | ❌ | ❌ |
| **3D Parallelism** | ❌ | ✅ | ❌ | ✅ |
| **HSDP** | ❌ | ✅ | ❌ | ✅ |
| **Multi-node** | ❌ | ✅ | ✅ | ✅ |

### MoE & その他機能

| 機能 | Unsloth | Axolotl | LlamaFactory | DeepSpeed |
|------|---------|---------|--------------|-----------|
| **MoE対応** | ✅ 基本 | ❌ | ✅ 推定 | ✅ 最高 |
| **GUI** | ❌ | ❌ | ✅ | ❌ |
| **量子化** | 4/8/16bit, FP8, 動的4bit | QAT | 4/8/16bit | ZeroQuant |
| **長文対応** | 500K | CP経由 | 標準 | Sparse Attention |
| **RL対応** | GRPO, FP8 | DPO, IPO等 | 標準 | 標準 |
| **Curriculum Learning** | ❌ | ✅ | ❌ | ✅ |
| **TTS/Vision** | ✅ | ❓ | ❓ | ❌ |

### 使いやすさ & 設定

| 項目 | Unsloth | Axolotl | LlamaFactory | DeepSpeed |
|------|---------|---------|--------------|-----------|
| **学習曲線** | 低 | 中 | 低 | 高 |
| **設定方法** | Python | YAML | GUI/CLI | JSON/Python |
| **ドキュメント** | 良好 | 充実 | 充実 | 非常に充実 |
| **コミュニティ** | 活発 | 活発 | 非常に活発 | 活発 |

### パフォーマンス特性

| 項目 | Unsloth | Axolotl | LlamaFactory | DeepSpeed |
|------|---------|---------|--------------|-----------|
| **速度** | 2x faster | 標準〜高速 | 標準 | 最高速 |
| **VRAM効率** | 最高（80%削減） | 高 | 標準 | 最高 |
| **スケーラビリティ** | 低（シングルGPU） | 高 | 高 | 最高 |
| **最大モデルサイズ** | 〜24GB | 数百億 | 数百億 | 1兆+ |

---

## 選択フローチャート

```
プロジェクトの性質を確認
│
├─ シングルGPU（12-24GB）で完結？
│  └─ YES → Unsloth
│     └─ 最速・最も省メモリ
│     └─ 特にLoRA/QLoRA実験に最適
│
├─ GUI操作を希望？
│  └─ YES → LlamaFactory
│     └─ ノーコード/ローコード
│     └─ 100+モデル対応
│
├─ 複雑な並列化が必要？
│  │
│  ├─ Context Parallelism（長文処理）が必要？
│  │  └─ YES → Axolotl
│  │     └─ Ring-Flash-Attention
│  │     └─ 10倍長いシーケンス処理
│  │
│  ├─ 3D Parallelism（FSDP+TP+CP）が必要？
│  │  └─ YES → Axolotl or DeepSpeed
│  │     └─ Axolotl: YAML設定で柔軟
│  │     └─ DeepSpeed: 最高のスケール
│  │
│  └─ Pipeline Parallelism が必要？
│     └─ YES → DeepSpeed のみ
│
├─ MoEモデル専門？
│  └─ YES → DeepSpeed
│     └─ DeepSpeed-MoE
│     └─ 1.6兆パラメータ実績
│
├─ 100億パラメータ以上？
│  └─ YES → DeepSpeed
│     └─ ZeRO-3 + 3D Parallelism
│
├─ 再現性重視のチーム開発？
│  └─ YES → Axolotl
│     └─ YAML設定で完全管理
│     └─ Docker/PyPI対応
│
└─ 標準的な分散学習で十分？
   └─ YES → LlamaFactory
      └─ DDP/FSDP/DeepSpeed対応
      └─ GUI + CLI両対応
```

---

## ユースケース別推奨

### 個人研究者・学生
- **第1選択**: Unsloth
- **理由**: 限られたGPUリソースで最大の効率、無料Colabノートブック
- **代替**: LlamaFactory（GUIが必要な場合）

### スタートアップ・小規模チーム
- **第1選択**: Axolotl
- **理由**: 再現性、柔軟性、スケーラビリティのバランス
- **代替**: LlamaFactory（迅速なプロトタイピング）

### 研究機関
- **第1選択**: Axolotl
- **理由**: YAML設定による再現性、複雑な並列化対応
- **代替**: DeepSpeed（超大規模実験の場合）

### エンタープライズ
- **第1選択**: DeepSpeed
- **理由**: 最高のスケーラビリティ、本番環境実績、MoE対応
- **代替**: Axolotl（柔軟な設定が必要な場合）

### 特殊用途
- **MoE専門**: DeepSpeed一択
- **長文処理**: Axolotl（Context Parallelism）
- **マルチモーダル**: Unsloth（TTS/Vision対応）
- **GUI必須**: LlamaFactory一択

---

## まとめ

### Unsloth
**強み**: 速度、メモリ効率、使いやすさ  
**弱み**: マルチGPU対応が限定的  
**最適**: シングルGPU環境での高速実験

### Axolotl
**強み**: 柔軟性、再現性、複雑な並列化対応  
**弱み**: MoE未対応  
**最適**: チーム開発、複雑な分散学習

### LlamaFactory
**強み**: GUI、広範なモデル対応、使いやすさ  
**弱み**: 先進的な並列化は限定的  
**最適**: 迅速なプロトタイピング、GUI重視

### DeepSpeed
**強み**: 最高のスケーラビリティ、MoE対応  
**弱み**: 学習曲線が急、複雑な設定  
**最適**: 超大規模モデル、本番環境

---

## 参考リンク

### 公式リポジトリ
- Unsloth: https://github.com/unslothai/unsloth
- Axolotl: https://github.com/axolotl-ai-cloud/axolotl
- LlamaFactory: https://github.com/hiyouga/LLaMA-Factory
- DeepSpeed: https://github.com/microsoft/DeepSpeed

### ドキュメント
- Unsloth Docs: https://docs.unsloth.ai/
- Axolotl Docs: https://docs.axolotl.ai/
- LlamaFactory Docs: https://www.aidoczh.com/llamafactory/
- DeepSpeed Docs: https://www.deepspeed.ai/

### 関連記事
- 元記事: https://blog.dailydoseofds.com/p/top-4-llm-fine-tuning-frameworks

---

**調査日**: 2025年12月19日  
**調査者**: Cline AI Assistant  
**バージョン**: 1.0

