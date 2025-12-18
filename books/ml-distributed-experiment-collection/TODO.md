

- Transformer のアーキテクチャの違い、SwiGLU、RoPE、RMSNorm、GQA、MoE
- グローバルバッチサイズ、学習率、計算精度（BF16など）の適切な設定とは？
- NCCL
- DP/PP, ZeRO あたり
- scaling inf on EKS
- Slurm
- Intelligent-Tiering ストレージクラス（2025年5月）
- EFA 対応の強化（2024年11月）、GPUDirect Storage (GDS) の完全サポート、ENA Express による約 30% のレイテンシ削減
- Unsloth
- SRD の話
- PC --rollback-on-failure false を Cluster 作成時につけると、作成に時間がかかりすぎた際に、再実行できるようになる。

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


inference

- https://speakerdeck.com/aztecher/distributed-inference-serving-vllm-lmcache-nixl-and-llm-d