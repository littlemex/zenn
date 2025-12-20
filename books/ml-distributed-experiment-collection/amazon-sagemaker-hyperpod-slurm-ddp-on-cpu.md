---
title: "Blueprints by Slurm: PyTorch DDP on CPU"
emoji: "🚀"
type: "tech"
topics: ["aws", "sagemaker", "hyperpod", "slurm", "distributed-training"]
free: true
---

::::details 前提
:::message
**対象読者**: 大規模基盤モデルの分散学習に興味があり、PyTorch DDP の仕組みを深く理解したい方。分散学習の基礎知識があると理解しやすい内容です。
:::
:::message
**ライセンス**: © 2025 littlemex.
本文および自作図表: CC BY 4.0
※公式ドキュメントからの引用や翻訳部分は原典の著作権に従います。
引用画像: 各画像の出典に記載されたライセンスに従います。
:::
:::message
一部 AI を用いて文章を作成します。レビューは実施しますが、見逃せない重大な間違いなどがあれば[こちらの Issue](https://github.com/littlemex/samples/issues) から連絡をお願いします。
:::
::::

本章では Slurm 環境で PyTorch Distributed Data Parallel（DDP）を使用した分散学習を試します。EKS で DDP を使用した分散学習を試したい場合は[こちら](https://awslabs.github.io/ai-on-sagemaker-hyperpod/docs/eks-blueprints/training/ddp/distributed-data-parallel)を確認してください。

:::message
実装が変更される可能性があるため必要に応じて[ドキュメント](https://awslabs.github.io/ai-on-sagemaker-hyperpod/docs/slurm-blueprints/training/ddp/distributed-data-parallel)を確認してください。
:::

---

# PyTorch DDP の理解

本章では、まず DDP とはそもそも何か、そして PyTorch DDP の仕組みを詳しく整理し、その後 Amazon SageMaker HyperPod の Slurm 環境で実際に CPU インスタンスでトレーニングを実行します。

まず理解すべき重要な点は、PyTorch でビルドされたモデルは、デフォルトでは単一の GPU でのみトレーニングされるということです。複数の GPU が利用可能であっても、PyTorch は自動的にはそれらを活用しません。

大規模基盤モデルの学習には、分散学習が不可欠です。その理由は大きく 2 つあります。メモリ制約の対策のためにマルチ GPU にモデルを分割する必要性についてはすでに解説しました。もう一つの理由は学習速度です。Llama 3 70B を 1.4 兆トークンで学習するには、H100 GPU 1 台で約 19 年かかる計算量（588 エクサ FLOP）が必要です。これを現実的な期間（数週間から数ヶ月）で完了させるには、数千台の GPU を並列動作させる必要があります。これらの複合的な要求によってマルチ GPU での学習が必須です。

## DDP の位置づけ

DDP は、主に**学習速度の向上**を目的とした並列化手法です。各 GPU がモデル全体のコピーを保持するため、モデルが単一 GPU のメモリに収まる規模（BF16 精度で 7B から 13B パラメータ程度）で有効です。複数 GPU で異なるデータバッチを並列処理することで、実効的なバッチサイズを増やし、学習を高速化します。

## DDP の仕組み

DDP では、利用可能なデータを小さなバッチに分割し、各バッチを個別の GPU で処理します。各 GPU からの更新結果は集約され、モデルパラメータの更新に使用されます。

同様に Data Parallelism では、各 GPU が「ミニサーバー」として機能し、データの一部を処理してローカルでモデルパラメータを更新します。これらのローカル更新が結合されてグローバルモデルが更新されます。このデータの並列処理により、トレーニングプロセスが高速化されるだけでなく、分散環境でのリソースの効率的な使用が可能になります。

主な違いは、Federated Learning ではローカルデータセットに直接アクセスできないのに対し、Data Parallelism ではデータに直接アクセス可能な点です。このアプローチにより、モデルトレーニングの効率が向上し、単一の GPU やマシンでは不可能な規模の大規模データセットや複雑なモデルの処理が可能になります。

```mermaid
graph TB
    subgraph "DDP の基本構造"
        subgraph GPU0["GPU 0"]
            M0[Model Copy<br/>全パラメータ]
            D0[Data Batch 0]
            M0 --> F0[Forward Pass]
            D0 --> F0
            F0 --> G0[Gradients 0]
        end
        
        subgraph GPU1["GPU 1"]
            M1[Model Copy<br/>全パラメータ]
            D1[Data Batch 1]
            M1 --> F1[Forward Pass]
            D1 --> F1
            F1 --> G1[Gradients 1]
        end
        
        subgraph GPU2["GPU 2"]
            M2[Model Copy<br/>全パラメータ]
            D2[Data Batch 2]
            M2 --> F2[Forward Pass]
            D2 --> F2
            F2 --> G2[Gradients 2]
        end
        
        G0 --> AR[All-Reduce<br/>勾配の平均化]
        G1 --> AR
        G2 --> AR
        
        AR --> U0[Parameter Update]
        AR --> U1[Parameter Update]
        AR --> U2[Parameter Update]
    end
    
    style M0 fill:#2d5986,color:#fff
    style M1 fill:#2d5986,color:#fff
    style M2 fill:#2d5986,color:#fff
    style AR fill:#8b4513,color:#fff
    style U0 fill:#2d6b35,color:#fff
    style U1 fill:#2d6b35,color:#fff
    style U2 fill:#2d6b35,color:#fff
```

各 GPU は独立して Forward Pass と Backward Pass を実行しますが、勾配を計算した後に All-Reduce という通信操作を使って全 GPU の勾配を平均化します。この平均化された勾配を使って各 GPU が同じパラメータ更新を行うため、全てのモデルが同期された状態を保ちます。

## PyTorch DDP とは

PyTorch の分散学習において、Distributed Data Parallel は最も基本的で重要な技術です。PyTorch DDP は PyTorch 標準の分散学習機能で、PyTorch に標準搭載されているため追加の依存関係が不要です。DDP で学ぶ勾配同期、通信最適化、バケット化などの基礎概念は、他の発展手法にも共通する重要な知識です。

単一 GPU のメモリに収まる規模のモデル（BF16 精度で 7B から 13B パラメータ程度）では、DDP が効率的な選択肢です。既存の PyTorch コードを数行変更するだけで分散化でき、デバッグも容易です。エラーメッセージが明確で問題の特定がしやすい点も利点です。[Hugging Face の Trainer API](https://huggingface.co/blog/pytorch-ddp-accelerate-transformers) では DDP が標準的な分散学習オプションとして利用可能であり、PyTorch Lightning でも DDP が推奨バックエンドとして採用されています。

Small Language Models (SLM) の市場は複数の調査会社により高成長が予測されています。[MarketsandMarkets](https://www.marketsandmarkets.com/Market-Reports/small-language-model-market-4008452.html) は 2025 年 0.93 億ドルから 2032 年 54.5 億ドルへの成長（CAGR 28.7%）を、[Polaris Market Research](https://www.polarismarketresearch.com/industry-analysis/small-language-model-market) は 2024 年 69.8 億ドルから 2034 年 580.5 億ドルへの成長（CAGR 23.6%）を、[DataInsights Market](https://www.datainsightsmarket.com/reports/small-language-model-149827) は 2025 年 64.3 億ドルから 2033 年 377.8 億ドルへの成長（CAGR 17.8%）を報告しています。エッジデバイスでの AI 処理需要の拡大により、5B パラメータ以下の軽量モデルの重要性が高まっています。このような小規模モデルのトレーニングでは、Tensor Parallelism などの高度な並列化手法を必要としないケースが中心となります。

## DDP の動作フロー

DDP のトレーニングは以下の 5 つのフェーズで構成されます。

```mermaid
sequenceDiagram
    participant R0 as Rank 0
    participant R1 as Rank 1
    participant R2 as Rank 2
    
    Note over R0,R2: Phase 1: 初期化
    R0->>R1: Broadcast Parameters
    R0->>R2: Broadcast Parameters
    Note over R0,R2: 全 GPU が同じパラメータを持つ
    
    Note over R0,R2: Phase 2: Forward Pass
    R0->>R0: Forward(Batch 0)
    R1->>R1: Forward(Batch 1)
    R2->>R2: Forward(Batch 2)
    Note over R0,R2: 各 GPU が独立に計算
    
    Note over R0,R2: Phase 3: Backward Pass
    R0->>R0: Compute Gradients
    R1->>R1: Compute Gradients
    R2->>R2: Compute Gradients
    
    Note over R0,R2: Phase 4: All-Reduce
    R0->>R1: Exchange Gradients
    R1->>R2: Exchange Gradients
    R2->>R0: Exchange Gradients
    Note over R0,R2: 勾配を平均化
    
    Note over R0,R2: Phase 5: Update
    R0->>R0: Update Parameters
    R1->>R1: Update Parameters
    R2->>R2: Update Parameters
    Note over R0,R2: 全 GPU が同じ更新を適用
```

### Phase 1: 初期化とパラメータブロードキャスト

トレーニング開始時、Rank 0（通常は最初の GPU）が持つモデルパラメータを全ての GPU にブロードキャストします。これにより、全ての GPU が完全に同じ初期パラメータを持つことが保証されます。このステップは一度だけ実行され、以降は各 GPU が独立して動作します。

### Phase 2: Forward Pass（順伝播）

各 GPU は異なるデータバッチを受け取り、独立に Forward Pass を実行します。この時点では GPU 間の通信は発生せず、完全に並列に計算が進みます。GPU 0 は Batch 0 を、GPU 1 は Batch 1 を、というように異なるデータを処理するため、実効的なバッチサイズは単一 GPU の場合の GPU 数倍になります。

### Phase 3: Backward Pass（逆伝播）

Loss を計算した後、各 GPU は独立に Backward Pass を実行して勾配を計算します。この段階でも GPU 間の通信は発生しません。各 GPU は自分が担当したデータバッチに対する勾配のみを計算します。

### Phase 4: All-Reduce（勾配同期）

ここが DDP の最も重要なステップです。各 GPU が計算した勾配を All-Reduce 操作によって平均化します。All-Reduce は全ての GPU が勾配を送受信し、最終的に全 GPU が同じ平均勾配を持つようにする通信操作です。この操作により、実効的に大きなバッチサイズでトレーニングしたのと同等の勾配が得られます。

### Phase 5: パラメータ更新

平均化された勾配を使って、各 GPU が独立にパラメータを更新します。全ての GPU が同じ初期パラメータを持ち、同じ勾配で更新するため、更新後も全ての GPU が同じパラメータを保持します。この性質により、次のイテレーションでも同期が保たれます。

## 素朴な実装の課題

DDP の基本的な動作は理解しやすいものの、素朴に実装すると重大な性能問題が発生します。

::::details パラメータごとの All-Reduce の問題

最も単純な実装では、各パラメータの勾配が計算されるたびに All-Reduce を実行することになります。

```python
# 素朴な実装（非効率）
for param in model.parameters():
    if param.grad is not None:
        # パラメータごとに All-Reduce を実行
        dist.all_reduce(param.grad)
        param.grad /= world_size
```

大規模なモデルでは数千から数万のパラメータが存在します。例えば Llama 2 7B モデルには約 32,000 個のパラメータテンソルがあります。各 All-Reduce 呼び出しにはレイテンシーが存在するため、32,000 回の通信を行うと膨大なオーバーヘッドが発生します。

通信のレイテンシーは転送するデータサイズではなく、通信回数に大きく依存します。小さなテンソルを何度も送るよりも、大きなテンソルを一度に送る方が遥かに効率的です。この問題を解決するために、PyTorch DDP は勾配バケット化という最適化を実装しています。
::::

::::details 通信と計算の分離による非効率性

素朴な実装では、Backward Pass が完全に終了してから All-Reduce を開始します。

```mermaid
gantt
    title 素朴な実装のタイムライン
    dateFormat X
    axisFormat %s
    
    section GPU 0
    Backward Pass :b0, 0, 10
    Wait :w0, 10, 15
    All-Reduce :ar0, 15, 18
    
    section GPU 1
    Backward Pass :b1, 0, 10
    Wait :w1, 10, 15
    All-Reduce :ar1, 15, 18
    
    section GPU 2
    Backward Pass :b2, 0, 10
    Wait :w2, 10, 15
    All-Reduce :ar2, 15, 18
```

この方式では、Backward Pass 中に GPU の計算リソースが利用可能であるにもかかわらず、通信は待機状態になります。逆に All-Reduce 中は通信リソースを使っていますが、計算リソースは遊んでいます。計算と通信を重ねることができれば、全体の実行時間を短縮できます。
::::

## 勾配バケット化による最適化

PyTorch DDP は勾配バケット化（Gradient Bucketing）という手法で通信回数を削減します。

```mermaid
graph TB
    subgraph "バケット化の概念"
        subgraph "パラメータテンソル"
            P1[Layer 80 weight<br/>4096×4096]
            P2[Layer 80 bias<br/>4096]
            P3[Layer 79 weight<br/>4096×4096]
            P4[Layer 79 bias<br/>4096]
            P5[Layer 78 weight<br/>4096×4096]
        end
        
        subgraph "Bucket 1 (25MB)"
            P1 --> B1[Flatten & Concatenate]
            P2 --> B1
            P3 --> B1
        end
        
        subgraph "Bucket 2 (25MB)"
            P4 --> B2[Flatten & Concatenate]
            P5 --> B2
        end
        
        B1 --> AR1[All-Reduce<br/>Bucket 1]
        B2 --> AR2[All-Reduce<br/>Bucket 2]
    end
    
    style P1 fill:#2d5986,color:#fff
    style P2 fill:#2d5986,color:#fff
    style P3 fill:#2d5986,color:#fff
    style P4 fill:#4a5986,color:#fff
    style P5 fill:#4a5986,color:#fff
    style B1 fill:#8b4513,color:#fff
    style B2 fill:#8b4513,color:#fff
    style AR1 fill:#2d6b35,color:#fff
    style AR2 fill:#2d6b35,color:#fff
```

バケット化では、複数のパラメータの勾配を一つの大きなテンソルにまとめてから All-Reduce を実行します。PyTorch のデフォルトではバケットサイズは 25MB に設定されており、この設定は多くの場合で良好なパフォーマンスを示します。

### バケット化のメリット

通信回数の削減により、レイテンシーによるオーバーヘッドが大幅に減少します。例えば 32,000 個のパラメータを 500 個のバケットにまとめれば、通信回数は 1/64 になります。大きなテンソルでは GPU 間の帯域幅を効率的に活用でき、通信と計算のオーバーラップも実装しやすくなります。

### バケットの構成戦略

DDP はモデルのパラメータを逆順（最後の層から最初の層へ）にバケットに割り当てます。これは Backward Pass が最後の層から開始されるため、最初に勾配が計算される層を最初のバケットに入れることで、早期に通信を開始できるようにするためです。

## 通信と計算のオーバーラップ

バケット化により通信回数を削減できましたが、さらに重要な最適化が通信と計算のオーバーラップです。

```mermaid
gantt
    title オーバーラップ最適化のタイムライン
    dateFormat X
    axisFormat %s
    
    section GPU Computation
    Layer 80 Backward :l80, 0, 2
    Layer 79 Backward :l79, 2, 4
    Layer 78 Backward :l78, 4, 6
    Layer 77 Backward :l77, 6, 8
    
    section Communication
    Wait :w1, 0, 2
    Bucket 1 All-Reduce :ar1, 2, 6
    Bucket 2 All-Reduce :ar2, 6, 10
```

### オーバーラップの仕組み

Backward Pass は最後の層から最初の層へと進行します。各層の勾配計算が完了すると、その層が属するバケットの準備が整ったことをチェックします。バケット内の全ての勾配が揃った時点で、即座に非同期 All-Reduce を開始します。

非同期 All-Reduce は別のストリームで実行されるため、GPU は次の層の Backward 計算を続けることができます。これにより、通信と計算が並行して実行され、全体の実行時間が短縮されます。

### Autograd フックによる実装

PyTorch DDP は autograd のフックメカニズムを使ってこの最適化を実装しています。

```python
# DDP の内部実装の概念（簡略化）
class DistributedDataParallel:
    def __init__(self, module):
        self.module = module
        self.buckets = self._create_buckets()
        
        # 各パラメータにフックを登録
        for param in self.module.parameters():
            if param.requires_grad:
                param.register_post_accumulate_grad_hook(
                    self._make_hook(param)
                )
    
    def _make_hook(self, param):
        def hook(*unused):
            # このパラメータが属するバケットを取得
            bucket = self._find_bucket(param)
            
            # バケット内の全勾配が揃ったかチェック
            if bucket.all_gradients_ready():
                # 非同期 All-Reduce を開始
                bucket.all_reduce_async()
        
        return hook
```

各パラメータの勾配が計算される度にフックが呼ばれ、属するバケットの状態をチェックします。バケットが完成したら即座に通信を開始し、GPU は引き続き次の層の計算を進めます。

## Ring All-Reduce アルゴリズム

DDP が使用する All-Reduce 操作の内部では、Ring All-Reduce というアルゴリズムが使われています。これは NCCL（NVIDIA Collective Communications Library）が実装する高効率な通信パターンです。

### 素朴な All-Reduce の問題

最も単純な All-Reduce 実装では、全ての GPU が Rank 0 に勾配を送り、Rank 0 が合計を計算して全 GPU にブロードキャストします。

```mermaid
graph TB
    subgraph "素朴な All-Reduce"
        GPU0[GPU 0<br/>Gradients: A0]
        GPU1[GPU 1<br/>Gradients: A1]
        GPU2[GPU 2<br/>Gradients: A2]
        GPU3[GPU 3<br/>Gradients: A3]
        
        Master[Rank 0<br/>集約と計算]
        
        GPU1 -->|Send A1| Master
        GPU2 -->|Send A2| Master
        GPU3 -->|Send A3| Master
        GPU0 -->|A0| Master
        
        Master -->|Broadcast Sum| GPU0
        Master -->|Broadcast Sum| GPU1
        Master -->|Broadcast Sum| GPU2
        Master -->|Broadcast Sum| GPU3
    end
    
    style Master fill:#8b4513,color:#fff
    style GPU0 fill:#2d5986,color:#fff
    style GPU1 fill:#2d5986,color:#fff
    style GPU2 fill:#2d5986,color:#fff
    style GPU3 fill:#2d5986,color:#fff
```

この方式では、Rank 0 がボトルネックになります。N 個の GPU がある場合、Rank 0 は N 倍のデータを受信し、N 倍のデータを送信する必要があります。GPU 数が増えるほど、Rank 0 の通信量が線形に増加してしまいます。

### Ring All-Reduce の効率性

Ring All-Reduce では、GPU をリング状に配置し、隣接する GPU とのみ通信します。

```mermaid
graph LR
    subgraph "Ring 構造"
        GPU0[GPU 0]
        GPU1[GPU 1]
        GPU2[GPU 2]
        GPU3[GPU 3]
        
        GPU0 -->|Send/Receive| GPU1
        GPU1 -->|Send/Receive| GPU2
        GPU2 -->|Send/Receive| GPU3
        GPU3 -->|Send/Receive| GPU0
    end
    
    style GPU0 fill:#2d5986,color:#fff
    style GPU1 fill:#4a5986,color:#fff
    style GPU2 fill:#2d6b35,color:#fff
    style GPU3 fill:#8b4513,color:#fff
```

アルゴリズムは以下の 2 つのフェーズで構成されます。

**Reduce-Scatter フェーズ**

データを chunk に分割し、リング上で回しながら累積加算します。N 個の GPU と N 個の chunk がある場合、N-1 ステップで各 GPU が異なる chunk の合計を持つ状態になります。

**All-Gather フェーズ**

各 GPU が持つ合計済み chunk をリング上で共有します。さらに N-1 ステップで、全ての GPU が全ての chunk を持つようになります。

### 通信量の比較

Ring All-Reduce の重要な特性は、通信量が GPU 数に依存しないことです。

| 方式 | 各 GPU の通信量 | Rank 0 の通信量 |
|------|----------------|----------------|
| 素朴な実装 | O(M) | O(N×M) |
| Ring All-Reduce | O(M) | O(M) |

ここで M はデータサイズ、N は GPU 数です。素朴な実装では Rank 0 が O(N×M) の通信を行う必要がありますが、Ring All-Reduce では全ての GPU が O(M) の通信のみで済みます。これにより、数百から数千の GPU にスケールしても通信効率が維持されます。

## DDP の利点と制約

### 利点

**実装の容易性**

PyTorch に標準搭載されており、追加の依存関係が不要です。既存のコードを数行変更するだけで分散化でき、学習曲線が緩やかです。エラーメッセージが明確で、デバッグが容易です。

**優れたパフォーマンス**

勾配バケット化により通信回数を最小化します。通信と計算のオーバーラップにより GPU の稼働率を最大化します。Ring All-Reduce により数百から数千 GPU へのスケーラビリティを実現します。

**ハードウェアの柔軟性**

PyTorch DDP は標準的な API として、複数のハードウェアバックエンドで動作します。NVIDIA GPU では NCCL バックエンド、CPU では gloo バックエンドを使用するだけでなく、[AWS Trainium](https://aws.amazon.com/ai/machine-learning/neuron/) でも PyTorch の DDP API がそのまま利用可能です。[AWS Neuron SDK のドキュメント](https://awsdocs-neuron.readthedocs-hosted.com/en/v2.9.1/frameworks/torch/torch-neuronx/tutorials/training/distributed_data_parallel.html)によると、既存の PyTorch DDP コードを minimal な変更で Trainium に移行でき、GPU から AI アクセラレータへの柔軟な展開が可能になります。

### 制約

**メモリ効率**

各 GPU がモデル全体のコピーを保持する必要があります。モデルが大きすぎると単一 GPU のメモリに収まりません。70B パラメータ以上の大規模モデルでは単独では不十分です。

**適用範囲**

Data Parallelism のみをサポートし、Model Parallelism は含まれません。超大規模モデルには FSDP や Megatron-LM などの追加技術が必要です。

**勾配同期のオーバーヘッド**

All-Reduce 通信がボトルネックになる可能性があります。特にノード間通信では、ノード内の NVLink に比べて遅い InfiniBand や Ethernet を使用するため、通信コストが増大します。

## DDP と他の並列化手法の関係

DDP は分散学習の基礎となる技術であり、他の高度な手法の土台となっています。

```mermaid
graph TB
    subgraph "PyTorch 分散学習エコシステム"
        DDP[DDP<br/>Data Parallelism<br/>基礎技術]
        
        FSDP[FSDP<br/>Parameter Sharding<br/>メモリ効率化]
        
        Megatron[Megatron-LM<br/>Tensor Parallel<br/>Model Parallelism]
        
        DeepSpeed[DeepSpeed<br/>ZeRO<br/>統合最適化]
        
        DDP --> FSDP
        DDP --> Megatron
        DDP --> DeepSpeed
    end
    
    style DDP fill:#2d5986,color:#fff
    style FSDP fill:#4a5986,color:#fff
    style Megatron fill:#2d6b35,color:#fff
    style DeepSpeed fill:#8b4513,color:#fff
```

FSDP は DDP の勾配同期メカニズムを拡張し、パラメータと optimizer state も分散します。Megatron-LM は DDP の Data Parallelism に Tensor Parallelism を組み合わせます。DeepSpeed は DDP の通信パターンを基礎として ZeRO 最適化を実装しています。

これらの高度な手法を理解するためには、DDP で学ぶ勾配同期、通信パターン、バケット化といった概念が不可欠です。

::::details マルチ GPU 処理手法の詳細

DDP を含む様々な並列化手法の詳細については、[マルチ GPU 処理手法の整理](./multi-gpu-processing-approaches.md)を参照してください。この章では以下のトピックを詳しく解説しています。

- Data Parallelism、Pipeline Parallelism、Tensor Parallelism の比較
- ZeRO（Zero Redundancy Optimizer）の詳細
- FSDP、DeepSpeed、Megatron-LM の特徴
- フレームワーク選択のガイドライン
- 学習手法（事前学習、SFT、DPO）とフレームワークの対応

DDP の理解を深めた後、より高度な手法を学ぶ際の参考にしてください。
::::

---

# Amazon SageMaker HyperPod Slurm での実装

ここからは、Amazon SageMaker HyperPod の Slurm 環境で実際に DDP を使用したトレーニングを実行します。

## 前提条件

::::details インフラストラクチャ要件

:::message
**Slurm クラスターの構築**

本章の実践には、事前に Amazon SageMaker HyperPod Slurm クラスターが構築されている必要があります。クラスターの構築手順については [Amazon SageMaker HyperPod Getting Started by SLURM](./amazon-sagemaker-hyperpod-slurm-tutorial.md) を参照してください。
:::

以下のリソースが準備されていることを確認してください。

**クラスター構成**

Amazon SageMaker HyperPod Slurm クラスターがデプロイされ、InService 状態であること。Controller ノード、Login ノード、Worker ノードが正常に稼働していること。FSx for Lustre ファイルシステムが `/fsx` にマウントされていること。

**開発環境**

AWS CLI v2 がインストールされ、適切な権限で設定されていること。SSM Session Manager プラグインがインストールされていること。Docker がインストールされていること（コンテナイメージのビルド用）。
::::

::::details 動作確認用の推奨インスタンス構成

本章では CPU インスタンスを使用して DDP の動作を確認します。GPU は不要ですが、DDP の基本的な動作原理は GPU でも CPU でも同じです。

| グループ | インスタンスタイプ | 数 | 用途 |
|---------|-----------------|---|------|
| **Controller** | `ml.c5.xlarge` | 1 | Slurm コントローラー |
| **Login** | `ml.c5.xlarge` | 1 | SSH ログイン用 |
| **Worker** | `ml.c5.4xlarge` | 2 | 計算ワークロード |

GPU を使用したい場合は、Worker を `ml.g5.xlarge` などに変更してください。
::::

## リポジトリのクローンと準備

:::message
なんのための作業か: DDP トレーニングコードと Slurm ジョブスクリプトを取得します。AWS の分散トレーニングサンプルリポジトリには、Slurm 向けに最適化された PyTorch DDP サンプルが含まれています。
:::

:::message
次のステップに進む条件: `awsome-distributed-training/3.test_cases/pytorch/cpu-ddp` ディレクトリに移動でき、必要なファイルが存在すること。
:::

クラスターに SSH 接続した後、リポジトリをクローンします。

```bash
cd ~
git clone https://github.com/aws-samples/awsome-distributed-training/
cd awsome-distributed-training/3.test_cases/pytorch/cpu-ddp
```

ディレクトリの内容を確認します。

```bash
ls -la
```

以下のようなファイルが含まれています。

```bash
drwxr-xr-x  Dockerfile
drwxr-xr-x  README.md
drwxr-xr-x  ddp.py          # トレーニングスクリプト
drwxr-xr-x  kubernetes/     # Kubernetes 用の設定
drwxr-xr-x  slurm/          # Slurm 用のジョブスクリプト
```

slurm ディレクトリの内容を確認します。

```bash
ls -la slurm/
```

以下の 4 つのファイルがあります。

```bash
0.create-conda-env.sh       # Conda 環境セットアップスクリプト
1.conda-train.sbatch        # Conda 環境での実行用 sbatch
2.create-enroot-image.sh    # Enroot イメージ作成スクリプト
3.container-train.sbatch    # Enroot コンテナでの実行用 sbatch
```

## トレーニングスクリプトとSlurmジョブの実行

リポジトリには 2 つの異なる実行方法が用意されています。それぞれの方法にはメリットとデメリットがあり、用途に応じて選択できます。

::::details トレーニングスクリプトの確認

:::message
なんのための作業か: DDP トレーニングを実行する Python スクリプトの内容を理解します。
:::

`ddp.py` ファイルを確認します。このスクリプトは、PyTorch DDP を使用した torchrun ベースのトレーニングを実装しています。

```python
import torch
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torch.utils.data.distributed import DistributedSampler
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.distributed import init_process_group, destroy_process_group
import os

# シンプルなダミーデータセット
class MyTrainDataset(Dataset):
    def __init__(self, size):
        self.size = size
        self.data = [(torch.rand(20), torch.rand(1)) for _ in range(size)]

    def __len__(self):
        return self.size
    
    def __getitem__(self, index):
        return self.data[index]

# プロセスグループの初期化
def ddp_setup():
    init_process_group(backend="gloo")  # CPU では gloo を使用

# Trainer クラス（スナップショットからの再開をサポート）
class Trainer:
    def __init__(self, model, train_data, optimizer, save_every, snapshot_path):
        self.model = model
        self.rank = os.environ["RANK"]
        self.train_data = train_data
        self.optimizer = optimizer
        self.save_every = save_every
        self.epochs_run = 0
        self.snapshot_path = snapshot_path
        
        # スナップショットが存在する場合はロード
        if os.path.exists(snapshot_path):
            self._load_snapshot(snapshot_path)
        
        # DDP でモデルをラップ
        self.model = DDP(self.model)
```

このスクリプトは torchrun による実行を前提としており、以下の特徴があります。

**環境変数の使用**: RANK、WORLD_SIZE、LOCAL_RANK を環境変数から取得し、torchrun が自動的に設定します。

**スナップショットからの再開**: トレーニングの中断と再開をサポートし、障害発生時にも学習を継続できます。

**DistributedSampler**: データを各プロセスに自動的に分配し、各 GPU が異なるデータバッチを処理します。

**argparse による引数処理**: コマンドライン引数でエポック数、チェックポイント間隔、バッチサイズを指定できます。
::::

::::details 方法 1: Conda 環境での実行

:::message
なんのための作業か: Conda 仮想環境を使用して、コンテナを介さずに直接トレーニングを実行します。この方法はシンプルで、デバッグが容易です。
:::

:::message
次のステップに進む条件: Conda 環境が作成され、ジョブが正常に投入できること。
:::

:::message alert
**Anaconda Terms of Service について**

2024 年以降、Anaconda は非対話モードでの環境作成時に利用規約（ToS）への明示的な同意を要求するようになりました。`0.create-conda-env.sh` を実行すると ToS エラーで失敗するため、以下の手順で対応する必要があります。
:::

**ステップ 1: Miniconda のインストールと ToS への同意**

```bash
cd ~/awsome-distributed-training/3.test_cases/pytorch/cpu-ddp/slurm

# Miniconda をダウンロードしてインストール
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
chmod +x Miniconda3-latest-Linux-x86_64.sh
./Miniconda3-latest-Linux-x86_64.sh -b -f -p ./miniconda3

# Miniconda の conda を有効化
source ./miniconda3/bin/activate

# ToS に同意（必須）
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
```

**ステップ 2: Conda 環境の作成**

ToS に同意した後、PyTorch 環境を作成します。

```bash
# オリジナルの方法（ToS 同意後なら動作）
conda create -y -p ./pt_cpu python=3.10 pytorch=2.0.1 -c pytorch -c nvidia -c conda-forge

# または conda-forge のみを使用（推奨）
# conda create -y -p ./pt_cpu python=3.10 pytorch cpuonly -c pytorch -c conda-forge

# 環境をアクティベート
source activate ./pt_cpu/

# 追加パッケージのインストール（必要に応じて）
# conda install -y torchvision -c pytorch -c conda-forge

# インストーラーをクリーンアップ
rm Miniconda3-latest-Linux-x86_64.sh*
```

**ステップ 3: 環境の確認**

torchrun が正しくインストールされたことを確認します。

```bash
ls -la ./pt_cpu/bin/torchrun
which torchrun
```

**ステップ 4: トレーニングジョブの投入**

```bash
sbatch 1.conda-train.sbatch
```

`1.conda-train.sbatch` は、作成した Conda 環境の `torchrun` を使用して `ddp.py` を実行します。このスクリプトは以下のように動作します。

各ノードで `srun` を通じて `./pt_cpu/bin/torchrun` を起動します。torchrun は環境変数（MASTER_ADDR、MASTER_PORT など）を自動設定し、各ノードで指定された数のプロセスを起動します。各プロセスは親ディレクトリの `ddp.py` を実行し、環境変数から RANK や LOCAL_RANK を取得して DDP トレーニングを開始します。

**メリット**
- コンテナイメージのビルドが不要
- 設定がシンプル（ToS 同意後）
- デバッグが容易
- 環境のカスタマイズが柔軟

**デメリット**
- 初回セットアップ時に ToS への同意が必要
- 環境の再現性がコンテナより劣る
- 依存関係の管理が必要
::::

::::details 方法 2: Enroot コンテナでの実行

:::message
なんのための作業か: Enroot コンテナを使用してトレーニングを実行します。Enroot は NVIDIA が開発した HPC 向けの軽量コンテナランタイムで、Slurm との統合に最適化されています。この方法は環境の完全な再現性を提供します。
:::

:::message
次のステップに進む条件: Enroot イメージが作成され、コンテナ経由でジョブが正常に実行できること。
:::

**Enroot とは**

Enroot は NVIDIA が開発した軽量コンテナランタイムです。Docker のような従来のコンテナとは異なり、HPC 環境に特化して設計されています。デーモンプロセスが不要で、非特権ユーザーでも実行でき、Slurm の Pyxis プラグインを通じてシームレスに統合されます。過度な隔離を排除しながらファイルシステム分離を維持するため、パフォーマンスオーバーヘッドが最小限です。

**ステップ 1: Enroot イメージの作成**

```bash
cd ~/awsome-distributed-training/3.test_cases/pytorch/cpu-ddp/slurm
bash 2.create-enroot-image.sh
```

このスクリプトは、Docker イメージを Enroot 形式（.sqsh ファイル）に変換します。Enroot イメージは圧縮された SquashFS フォーマットで保存され、高速な読み込みと効率的なストレージ使用を実現します。

**ステップ 2: コンテナトレーニングジョブの投入**

```bash
sbatch 3.container-train.sbatch
```

`3.container-train.sbatch` は、Pyxis プラグインを通じて Enroot コンテナ内でトレーニングを実行します。このスクリプトは以下のように動作します。

Slurm の `--container-image` オプションで Enroot イメージを指定し、必要な環境変数とボリュームマウントを設定します。Pyxis プラグインが Enroot を呼び出し、各ノードでコンテナを起動します。コンテナ内で `torchrun` が実行され、DDP トレーニングが開始されます。

**メリット**
- 環境の完全な再現性
- 依存関係がコンテナに含まれる
- HyperPod での推奨方法
- Slurm との統合が最適化されている

**デメリット**
- イメージの作成とビルドに時間がかかる
- Enroot と Pyxis の理解が必要
::::

## トレーニングジョブの実行

選択した実行方法（Conda または Enroot）に応じてジョブを投入します。

::::details ジョブの投入

:::message
なんのための作業か: 選択した方法で Slurm ジョブスクリプトを投入し、DDP トレーニングを開始します。
:::

:::message
次のステップに進む条件: `sbatch` コマンドが正常に実行され、ジョブ ID が返されること。
:::

**Conda 環境を使用する場合**

```bash
cd ~/awsome-distributed-training/3.test_cases/pytorch/cpu-ddp
sbatch slurm/1.conda-train.sbatch
```

**Enroot コンテナを使用する場合**

```bash
cd ~/awsome-distributed-training/3.test_cases/pytorch/cpu-ddp
sbatch slurm/3.container-train.sbatch
```

出力例は以下のようになります。

```
Submitted batch job 123
```

このジョブ ID を記録しておきます。
::::

::::details ジョブの監視

:::message
なんのための作業か: ジョブの状態を確認し、トレーニングの進行状況を監視します。
:::

:::message
次のステップに進む条件: ジョブが実行中であることが確認でき、ログからトレーニングの進行状況が見えること。
:::

ジョブのステータスを確認します。

```bash
squeue
```

出力例は以下のようになります。

```
JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
    4       dev cpu-ddp-   ubuntu  R       2:12      2 ip-10-4-33-25,ip-10-4-198-29
```

この出力から、ジョブ ID 4 が 2 ノードで実行中であることがわかります。`1.conda-train.sbatch` は各ノードで 4 プロセスを起動するため、合計 8 プロセス（RANK 0-7）が DDP トレーニングを実行します。

ジョブのログを確認します。

```bash
cd ~/awsome-distributed-training/3.test_cases/pytorch/cpu-ddp/slurm
head -n 200 logs/cpu-ddp-conda_4.out
```

出力例は以下のようになります。

```
Detected Hyperpod cluster.. enabling --auto-resume=1
[Auto Resume] Info: JobID: 4 StepID: 0 TaskID: 0 process_task_init
[Auto Resume] Info: JobID: 4 StepID: 0 TaskID: 1 process_task_init

INFO:torch.distributed.launcher.api:Starting elastic_operator with launch configs:
  entrypoint       : /fsx/ubuntu/awsome-distributed-training/3.test_cases/pytorch/cpu-ddp/ddp.py
  min_nodes        : 2
  max_nodes        : 2
  nproc_per_node   : 4
  run_id           : 4
  rdzv_backend     : c10d
  rdzv_endpoint    : ip-10-4-33-25
  max_restarts     : 0
  monitor_interval : 5

INFO:torch.distributed.elastic.agent.server.api:[default] Rendezvous complete for workers. Result:
  restart_count=0
  master_addr=ip-10-4-198-29.ec2.internal
  master_port=58353
  group_rank=0
  group_world_size=2
  local_ranks=[0, 1, 2, 3]
  role_ranks=[0, 1, 2, 3]
  global_ranks=[0, 1, 2, 3]
  role_world_sizes=[8, 8, 8, 8]
  global_world_sizes=[8, 8, 8, 8]

[RANK 0] Epoch 0 | Batchsize: 32 | Steps: 8
[RANK 1] Epoch 0 | Batchsize: 32 | Steps: 8
[RANK 2] Epoch 0 | Batchsize: 32 | Steps: 8
[RANK 3] Epoch 0 | Batchsize: 32 | Steps: 8
[RANK 4] Epoch 0 | Batchsize: 32 | Steps: 8
[RANK 5] Epoch 0 | Batchsize: 32 | Steps: 8
[RANK 6] Epoch 0 | Batchsize: 32 | Steps: 8
[RANK 7] Epoch 0 | Batchsize: 32 | Steps: 8

Epoch 0 | Training snapshot saved at ./snapshot.pt
[RANK 0] Epoch 1 | Batchsize: 32 | Steps: 8
...
```

### ログの解説

このログから、DDP トレーニングが正常に動作していることが確認できます。

**HyperPod Auto Resume の検出**

ログの最初に `Detected Hyperpod cluster.. enabling --auto-resume=1` と表示されています。これは HyperPod 環境が検出され、自動再開機能が有効になったことを示します。この機能により、ノード障害が発生してもトレーニングが自動的に再開されます。

**torchrun の起動設定**

torchrun が elastic operator として起動され、以下の設定が表示されます。

- `min_nodes: 2` / `max_nodes: 2`: 2 ノードで実行
- `nproc_per_node: 4`: 各ノードで 4 プロセスを起動
- `rdzv_backend: c10d`: PyTorch の分散通信バックエンドを使用
- `rdzv_endpoint: ip-10-4-33-25`: マスターノードのアドレス

**Rendezvous の完了**

各ノードの torchrun プロセスが rendezvous（ランデブー、集合地点）で合流し、プロセスグループを形成します。ログには 2 つのノードからの rendezvous 情報が表示されます。

ノード 0（group_rank=0）は `global_ranks=[0, 1, 2, 3]` を持ち、ノード 1（group_rank=1）は `global_ranks=[4, 5, 6, 7]` を持ちます。`global_world_sizes=[8, 8, 8, 8]` は、全プロセスが world size 8（2 ノード × 4 プロセス）を認識していることを示します。

**NumPy 警告（無害）**

```
UserWarning: Failed to initialize NumPy: No module named 'numpy'
```

この警告は、NumPy がインストールされていないことを示しますが、このトレーニングでは NumPy を使用していないため、トレーニングの実行には影響しません。警告を消したい場合は、`conda install -y numpy` で NumPy をインストールできます。

**トレーニングの進行**

全 8 つのプロセス（RANK 0-7）が同期してトレーニングを実行しています。各エポックで全ての RANK がログを出力し、同じバッチサイズ（32）とステップ数（8）でトレーニングしています。これは、DistributedSampler が正しくデータを分配し、DDP が勾配を同期していることを示します。

**チェックポイントの保存**

エポック 0、10、20... のように 10 エポックごとに `Epoch X | Training snapshot saved at ./snapshot.pt` というメッセージが表示されます。複数の RANK から同じメッセージが出力されていますが、実際には各 RANK が独立してスナップショットを保存しています。本来は RANK 0 のみから保存すべきですが、このサンプルコードでは全 RANK が保存する実装になっています。

**実効バッチサイズ**

各プロセスがバッチサイズ 32 でトレーニングし、8 プロセスが並列実行するため、実効バッチサイズは `32 × 8 = 256` になります。これは単一プロセスでバッチサイズ 256 を使用するのと同等の効果があり、学習の高速化につながります。

別のターミナルで特定のランクのログを確認することもできます。

```bash
# RANK 0 のみのログを表示
grep "RANK 0" logs/cpu-ddp-conda_4.out

# RANK 4-7 のログを表示（ノード 1）
grep -E "RANK [4-7]" logs/cpu-ddp-conda_4.out
```
::::

::::details 結果の確認

:::message
なんのための作業か: トレーニングが正常に完了したことを確認し、結果を検証します。
:::

:::message
次のステップに進む条件: ジョブが完了し、エラーがないこと。両方のランクが同期してトレーニングを実行したことが確認できること。
:::

ジョブの完了を確認します。

```bash
squeue
# 何も表示されなければジョブは完了
```

ログファイル全体を確認します。

```bash
cat /fsx/logs/pytorch-ddp_123.out
```

正常に完了した場合、以下のような情報が表示されます。

- 両方のランク（0 と 1）が初期化されたこと
- 各エポックで両方のランクがトレーニングを実行したこと
- Loss が減少していること
- エラーなく完了したこと

チェックポイントが保存されている場合は、それも確認します。

```bash
ls -la /fsx/checkpoints/
```
::::

## トラブルシューティング

::::details よくある問題と解決方法

**問題 1: "CondaToSNonInteractiveError: Terms of Service have not been accepted"**

Conda 環境作成時に最も一般的なエラーです。

```
CondaToSNonInteractiveError: Terms of Service have not been accepted for the following channels
```

原因は Anaconda の利用規約に同意していないためです。`0.create-conda-env.sh` はこのエラーで失敗し、`pt_cpu` 環境が作成されません。結果として、ジョブ実行時に torchrun が見つからないエラーが発生します。

解決方法は、ToS に同意してから環境を作成します。

```bash
cd ~/awsome-distributed-training/3.test_cases/pytorch/cpu-ddp/slurm

# Miniconda をインストール
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
chmod +x Miniconda3-latest-Linux-x86_64.sh
./Miniconda3-latest-Linux-x86_64.sh -b -f -p ./miniconda3
source ./miniconda3/bin/activate

# ToS に同意
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r

# 環境を作成
conda create -y -p ./pt_cpu python=3.10 pytorch=2.0.1 -c pytorch -c nvidia -c conda-forge
source activate ./pt_cpu/

# 確認
ls -la ./pt_cpu/bin/torchrun
```

**問題 2: "No such file or directory: torchrun"**

原因は Conda 環境が正しく作成されていない（問題 1 が解決していない）ことです。

解決方法は、問題 1 の手順で Conda 環境を作成してから、再度ジョブを投入します。

**問題 3: "RuntimeError: Address already in use"**

原因は MASTER_PORT が既に使用されている可能性があります。

解決方法は、ジョブスクリプトで異なるポート番号を指定します。

```bash
export MASTER_PORT=29501  # 別のポートに変更
```

**問題 4: "NCCL error" または "gloo error"**

原因はネットワーク設定の問題、またはバックエンドの選択ミスです。

解決方法として、CPU では `gloo` バックエンドを使用することを確認します。

```python
dist.init_process_group(backend='gloo')  # CPU の場合
# dist.init_process_group(backend='nccl')  # GPU の場合
```

**問題 5: "RuntimeError: Default process group has not been initialized"**

原因は `dist.init_process_group()` が呼ばれる前に DDP を使用している可能性があります。

解決方法は、必ず最初に分散環境を初期化します。

```python
# 最初に初期化
dist.init_process_group(backend='gloo')

# その後でモデルを DDP でラップ
model = DDP(model)
```
::::

## パフォーマンスの確認

::::details スケーラビリティの検証

DDP の効果を確認するために、異なるノード数でトレーニング時間を比較します。

**1 ノード（1 GPU/CPU）の場合**

```bash
# ジョブスクリプトで --nodes=1 に変更
sbatch ddp_train.sbatch
```

**2 ノード（2 GPU/CPU）の場合**

```bash
# ジョブスクリプトで --nodes=2 に変更
sbatch ddp_train.sbatch
```

理想的には、2 ノードで約 2 倍の高速化が期待できます。実際には通信オーバーヘッドにより 1.7 から 1.9 倍程度になることが多いです。

**スループットの計算**

ログから各エポックの時間を測定し、以下の式でスループットを計算します。

```
スループット = (バッチサイズ × ステップ数) / 経過時間
```

ノード数を増やした際にスループットが線形に増加すれば、DDP が効率的に動作していることを示します。
::::

---

# まとめ

本章では、PyTorch Distributed Data Parallel（DDP）の内部動作を詳しく学び、Amazon SageMaker HyperPod の Slurm 環境で実際に実装しました。

## 学んだこと

**DDP の基本原理**

各 GPU がモデル全体のコピーを保持し、異なるデータで並列にトレーニングを実行します。All-Reduce 操作により勾配を同期し、全 GPU が同じパラメータ更新を行います。

**最適化技術**

勾配バケット化により通信回数を削減し、レイテンシーのオーバーヘッドを最小化します。通信と計算のオーバーラップにより、GPU の稼働率を最大化します。Ring All-Reduce により、数百から数千 GPU へのスケーラビリティを実現します。

**実装スキル**

PyTorch の標準 API を使用した DDP の実装方法を習得しました。Slurm を使用したマルチノードトレーニングの実行方法を学びました。トラブルシューティングとパフォーマンス検証の手法を理解しました。

## 次のステップ

DDP の基礎を理解したら、以下のトピックに進むことをお勧めします。

**より高度な並列化手法**

[マルチ GPU 処理手法の整理](./multi-gpu-processing-approaches.md)で、FSDP、Megatron-LM、DeepSpeed などの advanced な手法を学習できます。大規模モデル（70B+ パラメータ）のトレーニングには、これらの手法が必要になります。

**HyperPod の高度な機能**

レジリエンシーとチェックポイント管理については [HyperPod Part 2](./hp-part2-checkpoint.md) を参照してください。動的キャパシティ管理については [HyperPod Part 3](./hp-part3-dynamic.md) を参照してください。オブザーバビリティについては [HyperPod Part 4](./hp-part4-observability.md) を参照してください。

**GPU を使用した実践**

本章では CPU を使用しましたが、実際の大規模トレーニングでは GPU が必要です。`ml.g5.xlarge` や `ml.p5.48xlarge` などの GPU インスタンスを使用し、NCCL バックエンドで同様のトレーニングを実行してください。

DDP は分散学習の基礎技術であり、これをマスターすることで、より高度な手法への道が開けます。

## 参考資料

### PyTorch 公式 DDP チュートリアルシリーズ

PyTorch は DDP の学習のための包括的なビデオチュートリアルシリーズを提供しています。本書では [PyTorch DDP 公式チュートリアル](./pytorch-ddp.md)で日本語による詳細な解説を行っています。

**公式チュートリアルリンク**
- [Introduction](https://docs.pytorch.org/tutorials/beginner/ddp_series_intro.html): シリーズの概要
- [What is DDP](https://docs.pytorch.org/tutorials/beginner/ddp_series_theory.html): DDP の理論的背景、DataParallel との比較
- [Single-Node Multi-GPU Training](https://docs.pytorch.org/tutorials/beginner/ddp_series_multigpu.html): 単一ノードでのマルチ GPU トレーニング
- [Fault Tolerance](https://docs.pytorch.org/tutorials/beginner/ddp_series_fault_tolerance.html): torchrun による耐障害性トレーニング
- [Multi-Node training](https://docs.pytorch.org/tutorials/intermediate/ddp_series_multinode.html): マルチノードトレーニング
- [minGPT Training](https://docs.pytorch.org/tutorials/intermediate/ddp_series_minGPT.html): 実践的な GPT モデルのトレーニング例
- [GitHub: DDP Tutorial Series](https://github.com/pytorch/examples/tree/main/distributed/ddp-tutorial-series): コード例

### torchrun について

本章では Slurm の `srun` を使用しましたが、PyTorch には `torchrun` というユーティリティも用意されています。torchrun は分散トレーニングの設定を自動化し、以下の機能を提供します。

**自動化される設定**
- 環境変数（RANK、WORLD_SIZE、LOCAL_RANK など）の自動設定
- プロセスの起動と管理
- 複数マシンでの実行時の調整

**耐障害性**
- 障害発生時に全プロセスを自動的に終了し再起動
- 最後に保存されたスナップショットから学習を再開
- モデル状態、エポック数、optimizer state などを保存して継続性を確保

**エラスティックトレーニング**
- ノードの動的な追加・削除をサポート
- メンバーシップ変更時に自動的にプロセスを再起動

**使用例**
```bash
# 従来の方法（mp.spawn）
python script.py

# torchrun を使用
torchrun --standalone --nproc_per_node=4 script.py
```

詳細は [Fault Tolerance チュートリアル](https://docs.pytorch.org/tutorials/beginner/ddp_series_fault_tolerance.html)を参照してください。
