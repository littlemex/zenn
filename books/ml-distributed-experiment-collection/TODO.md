
| 機能 | Kubernetes | Slurm | 説明 |
|------|-----------|-------|------|
| **Gang Scheduling** | △（拡張必要） | ◎（デフォルト） | Volcano/Kueue で実現可能 vs 標準機能 |
| **動的ノード追加** | ◎（Cluster Autoscaler） | △（手動/スクリプト） | クラウドネイティブ vs 静的前提 |
| **GPU Time-Slicing** | ◎（Device Plugin） | △（設定複雑） | 柔軟な共有 vs 基本はプロセス分離 |
| **MIG サポート** | ◎（DRA, GPU Operator） | △（手動設定） | K8s HyperPod は自動対応 |
| **推論サービング** | ◎（KServe, Triton） | ✕（不向き） | マイクロサービス vs バッチ |
| **ネットワークトポロジー最適化** | △（プラグイン依存） | ◎（標準機能） | ノード配置の最適化 |
| **ジョブ優先度管理** | ◎（Priority Class） | ◎（QoS, Fair Share） | 両方とも強力 |
| **リソースクォータ** | ◎（Namespace 単位） | ◎（Account/User 単位） | 管理粒度が異なる |
| **インタラクティブ開発** | ◎（Port Forward, Exec） | △（salloc, 非標準） | Jupyter など vs SSH ベース |
| **複数クラスター管理** | ◎（Multi-cluster Tools） | △（個別管理） | Federation vs 独立クラスター |
| **Spot インスタンス活用** | ◎（自動） | △（手動設定） | Karpenter など vs スクリプト |

**凡例**: ◎ 優秀、◯ 良好、△ 限定的、✕ 非対応・不向き

**Prolog/Epilog スクリプトでの自動診断**
- 
- MIG/TIme slice/vGPU違い
- Transformer のアーキテクチャの違い、SwiGLU、RoPE、RMSNorm、GQA、MoE
- グローバルバッチサイズ、学習率、計算精度（BF16など）の適切な設定とは？
- NCCL
- DP/PP, ZeRO あたり
- DDP をより深める
  - https://zenn.dev/tosshi/scraps/c28b1815d84434
  - コードリードする
- scaling inf on EKS
- Slurm
- Intelligent-Tiering ストレージクラス（2025年5月）
- EFA 対応の強化（2024年11月）、GPUDirect Storage (GDS) の完全サポート、ENA Express による約 30% のレイテンシ削減
- Unsloth
- SRD の話
- PC --rollback-on-failure false を Cluster 作成時につけると、作成に時間がかかりすぎた際に、再実行できるようになる。
- https://github.com/aws-samples/awsome-distributed-training/のサンプルリストの整理

- https://github.com/deepnote/deepnote deepnote 使って sagemaker studio notebook できる？


- 分散学習においては、Slurm は前章でモデル学習の並列処理手法を適用したジョブを効率的にスケジューリングし、GPU リソースを最大限に活用します。Slurm のジョブスクリプトでは、必要な GPU 数、ノード数、実行時間などを指定し、Slurm がリソースが利用可能になった時点でジョブを実行します。Slurm と並列処理手法の連携方法については別途並列処理手法の章で解説します。


https://huggingface.co/spaces/HuggingFaceTB/smol-training-playbook


Infos
- https://zenn.dev/tosshi/scraps/8839cb8949b692
- https://zenn.dev/tosshi/scraps/1772f3a0e0f3c1
- https://zenn.dev/turing_motors/articles/04eed10b0aafe9
- https://zenn.dev/turing_motors/articles/81cf3128b22c63
- https://zenn.dev/turing_motors/articles/ab14252a4da9da
- https://zenn.dev/turing_motors/articles/3a434d046bbf48
- https://zenn.dev/turing_motors/articles/fa687a8d30b373
- https://zenn.dev/turing_motors/articles/2fd279f6bb25a4
- https://zenn.dev/turing_motors/articles/4a7e80c4a47b65
- https://zenn.dev/turing_motors/articles/82505880d27d65
- https://zenn.dev/turing_motors/articles/257850090fc961
- https://zenn.dev/turing_motors/articles/f5f19f875bd8ba
- https://zenn.dev/turing_motors/articles/5b56edb7da1d30
- https://speakerdeck.com/odashi/llm-jp-3-and-beyond-training-large-language-models
- https://cafe-dc.com/network/aws-builds-separate-cabinet-to-bypass-nvidia-networking-hardware/
- https://jax-ml.github.io/scaling-book
- https://arxiv.org/pdf/2510.20171
- https://github.com/uccl-project/uccl


https://zenn.dev/tosshi/scraps/8862058842320e
https://zenn.dev/tosshi/scraps/b4101153b01fad
-

update

- https://aws.amazon.com/jp/blogs/machine-learning/accelerate-your-model-training-with-managed-tiered-checkpointing-on-amazon-sagemaker-hyperpod/
- https://aws.amazon.com/jp/blogs/machine-learning/checkpointless-training-on-amazon-sagemaker-hyperpod-production-scale-training-with-faster-fault-recovery/
- https://aws.amazon.com/jp/blogs/machine-learning/adaptive-infrastructure-for-foundation-model-training-with-elastic-training-on-sagemaker-hyperpod/
- 


- https://github.com/NVIDIA-NeMo/Megatron-Bridge
- Memory 効率テクニック: https://blog.dailydoseofds.com/p/a-memory-efficient-technique-to-train

# FT
- https://blog.dailydoseofds.com/p/step-by-step-guide-to-fine-tune-qwen3
- FT 手法: https://blog.dailydoseofds.com/p/5-llm-fine-tuning-techniques-250
- https://blog.dailydoseofds.com/p/top-4-llm-fine-tuning-frameworks

- precision: https://blog.dailydoseofds.com/p/train-neural-nets-4-6x-faster


# inference

- https://speakerdeck.com/aztecher/distributed-inference-serving-vllm-lmcache-nixl-and-llm-d
- MoE: https://blog.dailydoseofds.com/p/regular-ml-inference-vs-llm-inference

- ONNX: https://blog.dailydoseofds.com/p/model-development-and-optimization-f78