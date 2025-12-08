---
title: "大規模基盤モデル学習のためのインフラオーケストレーション"
emoji: "🎶"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["aws", "sagemaker", "hyperpod", "distributed", "infrastructure"]
free: true
---

::::details 前提
:::message
**対象読者**: 大規模基盤モデルがどういうものかを理解している方、これからモデル学習を行う方
:::
:::message
**ライセンス**: © 2025 littlemex.
本文および自作図表: CC BY 4.0
※公式ドキュメントからの引用や翻訳部分は原典の著作権に従います。
引用画像: 各画像の出典に記載されたライセンスに従います。
:::
:::message
一部 AI を用いて文章を作成します。レビューは実施しますが、見逃せない重大な間違いなどがあれば[こちらのIssue](https://github.com/littlemex/samples/issues)から連絡をお願いします。
:::
::::

**本章では大規模基盤モデル学習に求められるオーケストレーションについて整理します。**

---

AWS Principle WW Solutions Architect,GenAI, Keita Watanabe さんの [Scalable Infrastructure for Large-Scale AI Training with AWS Sagemaker Hyperpod](https://speakerdeck.com/keitaw/scalable-infrastructure-for-large-scale-ai-training-with-aws-sagemaker-hyperpod-at-singapore-ai-hour) 資料の流れを参照しながら初学者向けに情報を整理します。

# オーケストレーション

:::message
***Point! 分散学習のためのインフラストラクチャをオーケストレーションする主要なツールは Slurm と kubernetes***
:::

## Slurm

[Slurm](https://slurm.schedmd.com/overview.html) は、オープンソースのジョブスケジューラー兼リソースマネージャーです。[Wikipedia](https://ja.wikipedia.org/wiki/Slurm_Workload_Manager) によると Slurmは、TOP500 の約 60% のスーパーコンピューターでワークロードマネージャーとして使用されています。

### Slurm の 3 つの主要機能

Slurm は以下の 3 つの主要機能を提供します。

**1. リソース割り当て**: 計算ノードに対する排他的または非排他的なアクセスを一定期間ユーザーに割り当て、ジョブの実行を可能にします。複数のジョブが同一リソースを要求した場合、Slurm が調停し、適切なタイミングでリソースを割り当てます。

**2. ジョブ実行フレームワーク**: 割り当てられたノード上でジョブを開始、実行、監視するためのフレームワークを提供します。並列ジョブの起動、プロセス間通信の設定、ジョブのステータス監視などを一元的に管理します。

**3. キュー管理**: 保留中のジョブをキューで管理し、リソースの競合を調停します。ジョブの優先度、ユーザーのクォータ、リソースの制約などに基づいて、実行順序を決定します。

これらの機能により、Slurm は数百から数万の GPU を含む大規模クラスタでも効率的にリソースを管理できます。

::::details Slurm の価値を具体例で確認！
## モデル学習における Slurm の実例

Slurm のメリットを理解するために、具体的にモデル学習のシナリオを見てみましょう。

**シナリオ**: 3 人の研究者が同じ GPU クラスタ（4 基の GPU）を使って、異なる機械学習モデルを学習したいと考えています。

- 研究者 A: BERT モデル（2 GPU 必要、所要時間 3 時間、優先度: 中）
- 研究者 B: GPT モデル（2 GPU 必要、所要時間 5 時間、優先度: 高）
- 研究者 C: ResNet モデル（1 GPU 必要、所要時間 1 時間、優先度: 低）

### Slurm なしの場合

```mermaid
sequenceDiagram
    participant A as 研究者 A<br/>(BERT)
    participant B as 研究者 B<br/>(GPT)
    participant C as 研究者 C<br/>(ResNet)
    participant GPU as GPUクラスタ<br/>(4基のGPU)
    
    Note over A,GPU: 手動でのリソース管理
    
    A->>GPU: GPU使える？
    GPU-->>A: 空き状況が不明
    
    B->>GPU: GPU使える？
    GPU-->>B: 誰が使っているか不明
    
    C->>GPU: SSH でログインして確認...
    GPU-->>C: nvidia-smi で確認中...
    
    Note over A,C: 問題点
    Note over A: GPU の空き状況が<br/>リアルタイムで分からない
    Note over B: 他の研究者との<br/>リソース競合が発生
    Note over C: 手動での調整が必要で<br/>時間の無駄が多い
    
    rect rgb(255, 220, 220)
    Note over A,GPU: 結果: 非効率な利用
    end
```

この方式では、研究者が各自で GPU の空き状況を確認し、手動でジョブを開始する必要があります。これにより以下の問題が発生します。

- リソースの可視性が低く、空き GPU を見つけるのに時間がかかる
- 複数の研究者が同時に同じ GPU を使おうとして競合が発生する可能性がある
- 優先度の概念がなく、重要なジョブが待たされることがある
- GPU の利用率が低下し、アイドル時間が増える

### Slurm ありの場合

```mermaid
sequenceDiagram
    participant A as 研究者 A<br/>(BERT)
    participant B as 研究者 B<br/>(GPT)
    participant C as 研究者 C<br/>(ResNet)
    participant Slurm as Slurm<br/>ジョブスケジューラー
    participant GPU as GPUクラスタ<br/>(4基のGPU)
    
    Note over A,GPU: Slurm による自動管理
    
    rect rgb(220, 240, 255)
    Note over A,Slurm: 1. リソース割り当て
    A->>Slurm: sbatch train_bert.sh<br/>(2 GPU, 優先度: 中)
    B->>Slurm: sbatch train_gpt.sh<br/>(2 GPU, 優先度: 高)
    C->>Slurm: sbatch train_resnet.sh<br/>(1 GPU, 優先度: 低)
    end
    
    rect rgb(220, 255, 220)
    Note over Slurm: 2. キュー管理<br/>優先度とリソースを考慮
    Slurm->>Slurm: 優先度順にソート<br/>B (高) → A (中) → C (低)
    end
    
    rect rgb(255, 255, 220)
    Note over Slurm,GPU: 3. ジョブ実行フレームワーク
    Slurm->>GPU: B のジョブを GPU-1,2 に割り当て
    activate GPU
    Note over GPU: GPT訓練 実行中<br/>(GPU-1, GPU-2)
    
    Slurm->>GPU: A のジョブを GPU-3,4 に割り当て
    Note over GPU: BERT訓練 実行中<br/>(GPU-3, GPU-4)
    
    GPU-->>Slurm: B のジョブ完了 (5時間後)
    deactivate GPU
    
    Slurm->>GPU: C のジョブを GPU-1 に割り当て
    activate GPU
    Note over GPU: ResNet訓練 実行中<br/>(GPU-1)
    
    GPU-->>Slurm: A のジョブ完了 (3時間後)
    GPU-->>Slurm: C のジョブ完了 (1時間後)
    deactivate GPU
    end
    
    rect rgb(220, 255, 220)
    Note over A,GPU: 結果: 効率的な利用
    Note over A: 自動でリソースが割り当てられる
    Note over B: 優先度に基づいて<br/>適切に実行される
    Note over C: 待ち時間が最小化される
    end
```

Slurm を使用することで、以下のメリットが得られます。

**リソース割り当ての自動化**: 研究者は `sbatch` コマンドでジョブを投入するだけで、Slurm が自動的に利用可能な GPU を割り当てます。手動での空き確認は不要です。

**優先度ベースのスケジューリング**: ジョブの優先度に基づいて実行順序が決定されます。この例では、優先度が高い研究者 B の GPT 訓練が最初に実行され、次に研究者 A の BERT 訓練が並行して実行されます。研究者 C の ResNet 訓練は優先度が低いため、リソースが空くまで待機します。

**並列実行の最適化**: Slurm は利用可能なリソースを最大限に活用します。この例では、4 基の GPU すべてを同時に使用し（研究者 B が 2 GPU、研究者 A が 2 GPU）、リソースのアイドル時間を最小化します。

**ジョブの自動管理**: ジョブの開始、実行、完了を Slurm が自動的に管理します。研究者 B のジョブが完了すると、待機中の研究者 C のジョブが自動的に開始されます。

**公平性の保証**: キュー管理により、すべての研究者が適切にリソースにアクセスできます。優先度、ユーザーのクォータ、過去の利用履歴などを考慮した公平な割り当てが可能です。

このように、
:::message
Slurm は大規模な GPU クラスタでも複数のユーザーが効率的にリソースを共有できる環境を提供します。手動でのリソース管理と比較して、管理の手間を大幅に削減し、GPU の利用率を向上させることができます。
:::
::::

### Slurm のアーキテクチャ


![arch](/images/books/ml-distributed-experiment-collection/arch.gif)

> https://slurm.schedmd.com/overview.html より引用

Slurm は中央集権型のアーキテクチャを採用しており、上図のコンポーネントで構成されます。

::::details 各コンポーネントについて

**slurmctld (中央マネージャー)**: クラスタ全体のリソースとジョブを監視する中央デーモンです。ジョブのスケジューリング、ノードの状態管理、リソース割り当ての決定などを担当します。高可用性のために、バックアップマネージャーを配置することも可能です。

**slurmd (計算ノードデーモン)**: 各計算ノードで実行されるデーモンで、リモートシェルのように動作します。ジョブの受信、実行、ステータス報告、次のジョブの待機というサイクルを繰り返します。slurmd 間は耐障害性のある階層的な通信を行います。

**slurmdbd (データベースデーモン)**: オプションのコンポーネントで、複数の Slurm クラスタのアカウンティング情報を単一のデータベースに記録します。ジョブの実行履歴、リソース使用量、課金情報などを一元管理できます。

**slurmrestd (REST API デーモン)**: オプションのコンポーネントで、REST API を通じて Slurm と対話できます。外部システムとの統合や、カスタムダッシュボードの構築に利用されます。

::::

::::details Slurm のユーザーツールとプラグインシステム

### ユーザーツール

Slurm は豊富なコマンドラインツールを提供しています。

**srun**: ジョブを投入し、実行します。並列ジョブの起動、MPI プログラムの実行、インタラクティブなシェルセッションの開始などに使用されます。

**scancel**: キューイング中または実行中のジョブを終了します。ジョブ ID や ユーザー名を指定して、特定のジョブをキャンセルできます。

**sinfo**: クラスタ全体のシステム状態を報告します。ノードの可用性、パーティションの状態、リソースの使用状況などを確認できます。

**squeue**: ジョブの状態を報告します。実行中のジョブ、キューイング中のジョブ、優先度などの情報を表示します。

**sacct**: 実行中または完了したジョブのアカウンティング情報を取得します。ジョブの実行時間、使用リソース、終了ステータスなどを確認できます。

**scontrol**: 管理者向けのツールで、クラスタの設定や状態を監視・変更します。ノードのドレイン、ジョブの優先度変更、パーティションの設定などが可能です。

**sview**: グラフィカルにシステムとジョブの状態を表示します。ネットワークトポロジーの可視化にも対応しています。

### プラグインシステム

Slurm は汎用的なプラグインメカニズムを提供しており、さまざまなインフラストラクチャに対応できます。主要なプラグインには以下があります。

**Accounting Storage**: ジョブの履歴データを保存します。SlurmDBD と組み合わせることで、リミットベースのシステムや履歴的なシステムステータスを提供できます。

**Authentication**: Slurm の各コンポーネント間の認証メカニズムを提供します。

**Generic Resources**: GPU などの汎用リソースを制御するインターフェースを提供します。

**Job Submit**: ジョブ投入時にサイト固有の要件を適用するカスタムプラグインです。

**MPI**: さまざまな MPI 実装に対応するフックを提供します。MPI 固有の環境変数の設定などが可能です。

**Priority**: ジョブの優先度を決定します。エージング、フェアシェア、QoS などの要素を組み合わせた多要素優先度アルゴリズムをサポートします。

**Scheduler**: ジョブのスケジューリング方法を決定します。バックフィルスケジューリング、ギャングスケジューリングなどが利用可能です。

**Network Topology**: ネットワークトポロジーに基づいてリソース選択を最適化します。ジョブ割り当てと高度な予約の両方に使用されます。

### Slurm の設定例

以下は Slurm の設定ファイル (`/etc/slurm.conf`) の抜粋例です。

```bash
# 中央マネージャーの設定
SlurmctldHost=linux0001  # プライマリサーバー
SlurmctldHost=linux0002  # バックアップサーバー

# 認証とプラグイン
AuthType=auth/munge
PluginDir=/usr/local/slurm/lib

# ノード設定
NodeName=DEFAULT CPUs=4 TmpDisk=16384 State=IDLE
NodeName=lx[0001-0002] State=DRAINED
NodeName=lx[0003-8000] RealMemory=2048 Weight=2
NodeName=lx[8001-9999] RealMemory=4096 Weight=6 Feature=video

# パーティション設定
PartitionName=DEFAULT MaxTime=30 MaxNodes=2
PartitionName=debug Nodes=lx[0003-0030] State=UP Default=YES
PartitionName=batch Nodes=lx[0041-9999] MaxTime=UNLIMITED MaxNodes=4096
```

この設定では、ノードをグループ化してパーティション (ジョブキュー) を定義しています。各パーティションには、ジョブの最大実行時間、最大ノード数、アクセス権限などの制約を設定できます。
::::

分散学習においては、Slurm は前章でモデル学習の並列処理手法を適用したジョブを効率的にスケジューリングし、GPU リソースを最大限に活用します。Slurm のジョブスクリプトでは、必要な GPU 数、ノード数、実行時間などを指定し、Slurm がリソースが利用可能になった時点でジョブを実行します。Slurm と並列処理手法の連携方法については別途並列処理手法の章で解説します。

## Kubernetes

Kubernetes は、コンテナ化されたアプリケーションのデプロイ、スケーリング、管理を自動化するオープンソースのコンテナオーケストレーションプラットフォームです。Google が開発し、現在は Cloud Native Computing Foundation (CNCF) が管理しています。Kubernetes は、コンテナベースのワークロードに対するデファクトスタンダードとして、多様な業界で広く採用されています。

分散学習の文脈において、Kubernetes は特に **レジリエンス（回復力）と耐障害性** で優れています。Kubernetes のセルフヒーリング機能により、ハードウェア障害やソフトウェアエラーが発生した場合でも、自動的にコンテナを再起動し、障害ノードから健全なノードへワークロードを移行します。この特性は、数日から数週間にわたる長期的な学習ジョブにおいて、ダウンタイムを最小化する上で重要です。

:::message alert
Slurm にレジリエンスと耐障害性がないというわけではありません。
:::

### Kubernetes の主要な特徴

**セルフヒーリング**: コンテナの障害を自動的に検知し、再起動や再スケジューリングを実行します。ヘルスチェック (liveness probe, readiness probe) により、アプリケーションレベルの問題も検出できます。

**宣言的な設定**: ユーザーは望ましいシステムの状態を YAML または JSON で定義し、Kubernetes がその状態を維持します。設定の変更や更新も宣言的に行えるため、インフラストラクチャのバージョン管理が容易です。

**サービスディスカバリーとロードバランシング**: Pod (Kubernetes の最小デプロイ単位) に対する安定した IP アドレスと DNS 名を提供し、複数の Pod 間でトラフィックを自動的に分散します。分散学習では、トレーニングジョブの各ワーカーが互いを発見し、通信する仕組みとして利用されます。

**水平スケーリング**: CPU 使用率やメモリ使用量に基づいて、Pod の数を自動的に増減します (Horizontal Pod Autoscaler)。Cluster Autoscaler と組み合わせることで、ノード自体の追加・削除も自動化できます。

**ローリングアップデートとロールバック**: ダウンタイムなしでアプリケーションを更新し、問題が発生した場合は以前のバージョンにロールバックできます。

### 分散学習における Kubernetes の利点

分散学習において、Kubernetes は以下の利点を提供します。

**コンテナによる環境の再現性**: Docker コンテナに学習環境をパッケージ化することで、開発環境と本番環境の差異を排除し、依存関係の管理を簡素化します。

**マルチテナント対応**: Namespace による論理的な分離と RBAC (Role-Based Access Control) により、複数のチームやプロジェクトが同一クラスタを安全に共有できます。

**エコシステムとの統合**: Kubernetes エコシステムには、モニタリング (Prometheus, Grafana)、ロギング (Fluentd, Elasticsearch)、CI/CD (Argo CD, Flux) など、豊富なツールが揃っています。これらを組み合わせることで、包括的な ML プラットフォームを構築できます。

**スケジューリングの柔軟性**: Node Selector, Node Affinity, Taints and Tolerations などの機能により、GPU の種類やノードの特性に基づいて Pod を適切なノードに配置できます。

**ジョブリソースの管理**: Kubernetes の Job および CronJob リソースにより、バッチ処理やスケジュールされたタスクを管理できます。分散学習では、Kubeflow Training Operator や PyTorch Elastic などのオペレーターが、Kubernetes のネイティブリソースとして学習ジョブを管理します。

Kubernetes 自体は汎用的なコンテナオーケストレーターであり、ML 固有の機能は限定的です。しかし、Kubeflow, Ray, MLflow などの ML プラットフォームと組み合わせることで、モデル学習からデプロイまでの一貫したワークフローを構築できます。AWS では、Amazon EKS (Elastic Kubernetes Service) および Amazon SageMaker HyperPod (EKS 版) が、Kubernetes ベースの分散学習環境を提供します。








## Kubernetes

説明書いてもらえるかな。kubernetes の強みも書いて欲しいな。おそらくレジリエンシーとして障害復旧が強いと思う。

kubernetes 自体は言わずもがなでよく使われてもいるので細かい話は割愛。

## AWS での Slurm/kubernetes を用いた分散学習環境の概要

概要を紹介

### [Amazon Sagemaker HyperPod](https://docs.aws.amazon.com/ja_jp/sagemaker/latest/dg/sagemaker-hyperpod.html)

Slurm/kubernetes どちらにも対応

便利機能多数(slurm/k8s どちらの機能かは明確に書いて)

以下を中身を見てhyperpod に関連するアップデート機能をまとめて
- https://aws.amazon.com/jp/blogs/machine-learning/accelerate-your-model-training-with-managed-tiered-checkpointing-on-amazon-sagemaker-hyperpod/
- https://aws.amazon.com/jp/blogs/machine-learning/amazon-sagemaker-hyperpod-launches-model-deployments-to-accelerate-the-generative-ai-model-development-lifecycle/
- https://zenn.dev/tosshi/scraps/f9b72d35baa5bd

### AWS Parallel Cluster

### Amazon EKS

軽く触れるだけ、基本自分で頑張ることになる、どうしてもオンプレとAWSでGPUリソースを併用したい、などがあればこの選択肢

## オーケストレーションソリューションに求められること

- slurm/k8s をマネージドで提供
- 障害の検知と復旧
  - https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html#run-levels-and-tests この内容の対応、::::details で説明して
- 前章の infrastructures.md で紹介したインフラストラクチャとの簡易な統合

## 比較

それぞれの能力について比較、できることできないこと、DCGM のレベルなど

## 手を動かす

https://github.com/aws-samples/awsome-distributed-training 全般でこれを利用するのが良い

https://github.com/aws-samples/awsome-distributed-training/tree/main/1.architectures/2.aws-parallelcluster PCluster

https://github.com/aws/sagemaker-hyperpod-recipes 上の awsome との違いを知りたい

https://catalog.workshops.aws/ml-on-aws-parallelcluster/en-US Parallel Cluster はこれ

https://catalog.workshops.aws/sagemaker-hyperpod/en-US Workshop としてはこれもある hyperpod 
https://catalog.us-east-1.prod.workshops.aws/workshops/eef64d11-5673-4fb1-b047-4cebdde81eb9/en-US これも

## エラー発生率

https://developer.nvidia.com/blog/ensuring-reliable-model-training-on-nvidia-dgx-cloud/ この内容からめっちゃ壊れるので検知と自動リペアの仕組みは必須ということを説明したい




---- 以降は参考情報なので参考にした後は消して良いです


EC2 UltraClusters 上で大規模 AI/ML ワークロードを実行するには、適切なオーケストレーション・管理プラットフォームを選択する必要があります。AWS は以下の4つの主要な選択肢を提供しています。

```mermaid
graph TB
    subgraph Platform["AWS AI プラットフォーム"]
        direction TB
        
        ParallelCluster[AWS ParallelCluster<br/>━━━━━━━━━━━━<br/>HPC向けクラスター管理<br/>Slurm, SGE, Torque<br/>CloudFormation]
        
        HyperPodSlurm[SageMaker HyperPod<br/>Slurm版<br/>━━━━━━━━━━━━<br/>マネージドML訓練<br/>自動リカバリー<br/>ヘルスモニタリング]
        
        HyperPodEKS[SageMaker HyperPod<br/>EKS版<br/>━━━━━━━━━━━━<br/>Kubernetes管理<br/>Karpenter, MIG対応<br/>Spot対応]
        
        EKS[Amazon EKS<br/>━━━━━━━━━━━━<br/>マネージドKubernetes<br/>EKS Capabilities<br/>汎用オーケストレーション]
    end
    
    subgraph Infrastructure["EC2 UltraClusters"]
        GPU[GPU: P5, P6e]
        Trainium[Trainium: Trn3]
        Network[Network: EFA, NVLink]
        Storage[Storage: S3, FSx, EBS]
    end
    
    ParallelCluster --> Infrastructure
    HyperPodSlurm --> Infrastructure
    HyperPodEKS --> Infrastructure
    EKS --> Infrastructure
    
    style Platform fill:#e1f5ff
    style Infrastructure fill:#f0fff4
    style ParallelCluster fill:#ffd4d4
    style HyperPodSlurm fill:#d4ffd4
    style HyperPodEKS fill:#d4d4ff
    style EKS fill:#ffffd4
```

### 比較表

| プラットフォーム | オーケストレーター | 管理レベル | 自動リカバリー | 主な用途 | Spot対応 |
|----------------|------------------|-----------|--------------|---------|---------|
| **ParallelCluster** | Slurm, SGE, Torque | セルフマネージド | 手動 | HPC、バッチ処理 | 手動設定 |
| **HyperPod (Slurm)** | Slurm | フルマネージド | 自動 | 長期間ML訓練 | ❌ |
| **HyperPod (EKS)** | Kubernetes | フルマネージド | 自動 | コンテナベースML | ✅ 自動 |
| **EKS** | Kubernetes | マネージドK8s | 手動 | 汎用コンテナ | 手動設定 |

### 1. AWS ParallelCluster

**概要**: HPC（High Performance Computing）向けのクラスター管理ツールで、従来の HPC ワークロードや研究機関での利用に最適です。

**特徴**:
- CloudFormation による自動デプロイ
- Slurm, SGE, Torque などの HPC ジョブスケジューラーをサポート
- カスタム AMI、スクリプトによる柔軟な環境構築
- コスト最適化のための Auto Scaling

**適用ケース**:
- 従来の HPC ワークロード（分子動力学、気象シミュレーションなど）
- 研究機関での大規模計算
- オンプレミス HPC からのリフト&シフト

**制約**:
- ノード障害時のリカバリーは手動
- ヘルスチェックは基本的な死活監視のみ
- ML 特化の最適化は限定的

### 2. Amazon SageMaker HyperPod（Slurm版）

**概要**: Slurm をジョブスケジューラーとして使用する、フルマネージドの ML 訓練プラットフォームです。

**特徴**:
- **自動ノードリカバリー**: ハードウェア障害を検出し、自動的にノードを交換
- **ヘルスモニタリング**: GPU、ネットワーク、ストレージの包括的な監視
- **自動ジョブ再開**: チェックポイントから自動的に訓練を再開
- **Slurm 統合**: 既存の Slurm スクリプトをそのまま使用可能

**適用ケース**:
- 数週間から数ヶ月にわたる大規模基盤モデル訓練
- Slurm に慣れたチームでの利用
- 高い信頼性が求められる本番環境

**新機能（re:Invent 2025）**:
- **Checkpointless Training**: リカバリー時間を80%以上削減
- **Elastic Training**: リソース可用性に基づく自動スケーリング
- **Programmatic Node Operations**: API による再起動・交換

### 3. Amazon SageMaker HyperPod（EKS版）

**概要**: Kubernetes（EKS）をオーケストレーターとして使用する、最も柔軟で現代的なマネージド ML プラットフォームです。

**特徴**:
- **Kubernetes ネイティブ**: 標準的な K8s API、kubectl、Helm を使用
- **Karpenter 統合**: ワークロードに応じた自動スケーリング
- **コンテナベース**: Docker コンテナによる環境の再現性
- **マルチテナント**: Namespace による分離、RBAC

**適用ケース**:
- コンテナベースの ML ワークフロー
- CI/CD パイプラインとの統合
- マイクロサービス的なアプローチ
- 複数チームでのリソース共有

**新機能（re:Invent 2025）**:
- **MIG（Multi-Instance GPU）サポート**: 1 GPU を最大7パーティションに分割
- **Spot Instances サポート**: 最大90%のコスト削減
- **Custom Kubernetes Labels & Taints**: 柔軟な Pod スケジューリング
- **Managed Tiered KV Cache**: 推論レイテンシ40%削減、スループット25%向上

### 4. Amazon EKS（スタンドアロン）

**概要**: AWS のマネージド Kubernetes サービスで、汎用的なコンテナオーケストレーションに使用されます。

**特徴**:
- **標準 Kubernetes**: アップストリーム K8s との完全な互換性
- **EKS Capabilities**（2025年12月発表）: Argo CD, ACK, KRO の統合
- **柔軟性**: あらゆるコンテナワークロードに対応
- **エコシステム**: Kubernetes エコシステムのツールを活用

**適用ケース**:
- ML 以外のワークロードも含む統合プラットフォーム
- 既存の Kubernetes 環境からの移行
- カスタマイズ性を最大限に活用したい場合

**EKS Capabilities**（2025年12月発表）:
- **Argo CD**: GitOps による継続的デプロイメント
- **ACK（AWS Controllers for Kubernetes）**: K8s から AWS リソースを管理
- **KRO（Kube Resource Orchestrator）**: 複雑なリソースの抽象化

### プラットフォーム選択のガイドライン

```mermaid
graph TD
    Start[大規模AI/MLワークロード] --> Q1{既存のSlurm<br/>スクリプトあり?}
    
    Q1 -->|Yes| Q2{自動リカバリー<br/>必要?}
    Q1 -->|No| Q3{Kubernetes<br/>使用したい?}
    
    Q2 -->|Yes| HyperPodSlurm[HyperPod Slurm版]
    Q2 -->|No| ParallelCluster[ParallelCluster]
    
    Q3 -->|Yes| Q4{ML特化機能<br/>必要?}
    Q3 -->|No| ParallelCluster
    
    Q4 -->|Yes| HyperPodEKS[HyperPod EKS版]
    Q4 -->|No| EKS[Amazon EKS]
    
    style Start fill:#e1f5ff
    style HyperPodSlurm fill:#d4ffd4
    style ParallelCluster fill:#ffd4d4
    style HyperPodEKS fill:#d4d4ff
    style EKS fill:#ffffd4
```

**選択基準**:

1. **既存のワークフロー**: Slurm スクリプトがあれば HyperPod Slurm、Kubernetes 経験があれば HyperPod EKS
2. **管理レベル**: フルマネージドを求めるなら HyperPod、柔軟性を求めるなら ParallelCluster や EKS
3. **コスト**: Spot インスタンスを活用したいなら HyperPod EKS
4. **スケール**: 数万 GPU のスケールには HyperPod
5. **統合**: 既存の AWS サービスとの統合には ACK を含む EKS Capabilities

## プラットフォーム別インスタンス対応表

各プラットフォームで利用可能なインスタンスタイプを整理します。

| インスタンスタイプ | ParallelCluster | HyperPod (Slurm) | HyperPod (EKS) | EKS |
|------------------|----------------|------------------|----------------|-----|
| **P5 (H100)** | ✅ | ✅ | ✅ | ✅ |
| **P6e-GB200** | ✅ | ✅ | ✅ | ✅ |
| **P6e-GB300** | ✅ | ✅ | ✅ | ✅ |
| **P4d (A100)** | ✅ | ✅ | ✅ | ✅ |
| **Trn1 (Trainium)** | ✅ | ✅ | ✅ | ✅ |
| **Trn2 (Trainium2)** | ✅ | ✅ | ✅ | ✅ |
| **Trn3 (Trainium3)** | ✅ | ✅ | ✅ | ✅ |
| **Inf2 (Inferentia2)** | ✅ | ✅ | ✅ | ✅ |
| **Spot インスタンス** | 手動設定 | ❌ | ✅ 自動 | 手動設定 |
| **MIG 分割** | 手動設定 | ❌ | ✅ 自動 | 手動設定 |

- **ParallelCluster**: すべてのインスタンスタイプをサポートしますが、Spot や MIG は手動設定が必要
- **HyperPod (Slurm)**: 現在 Spot と MIG は未サポート（将来的にサポート予定の可能性）
- **HyperPod (EKS)**: Spot と MIG を完全サポートし、自動管理機能を提供
- **EKS**: すべてのインスタンスをサポートしますが、Spot や MIG の管理は自前で実装

#### 7. Elastic Training

**発表日**: 2025年12月

**主要機能**:
- リソース可用性に基づく自動スケーリング
- アイドル状態の容量を自動的に活用
- 高優先度ワークロード（推論など）のピーク時は自動縮小
- トレーニング品質を維持しながらスケーリング

**仕組み**:
- HyperPod トレーニングオペレーターが Kubernetes と統合
- Pod ライフサイクル、ノード可用性、リソーススケジューラーを監視
- データ並列レプリカの追加・削除によるスケーリング
- グローバルバッチサイズを保持、学習率を適応

**効果**:
- クラスタ使用率の最大化
- 週あたり数時間のエンジニアリング時間を節約

**参考**: [AWS ブログ](https://aws.amazon.com/jp/blogs/aws/introducing-checkpointless-and-elastic-training-on-amazon-sagemaker-hyperpod/)


#### 8. Custom Kubernetes Labels & Taints

**発表日**: 2025年11月26日

**主要機能**:
- インスタンスグループレベルでラベルとテイントを設定
- ノードライフサイクル全体で自動維持
- 最大50ラベル、50テイントまで指定可能

**効果**:
- GPU リソースの保護（NoSchedule テイントで明示的な Toleration を持つジョブのみ実行）
- デバイスプラグイン統合の簡素化（EFA、NVIDIA GPU オペレーターなど）
- 手動再適用作業の完全排除

**参考**: [AWS 発表](https://aws.amazon.com/jp/about-aws/whats-new/2025/11/amazon-sagemaker-hyperpod-kubernetes/)

#### 9. Programmatic Node Operations

**発表日**: 2025年11月26日

**新 API**:
- **BatchRebootClusterNodes**: 最大25ノードを一度に再起動
- **BatchReplaceClusterNodes**: 最大25ノードを新ハードウェアに交換

**主要機能**:
- オーケストレータ非依存（Slurm、EKS 両対応）
- 既存のオーケストレータ固有方法と併用可能
- 進捗状況の監視が可能

**効果**:
- 大規模復旧シナリオの効率的な管理
- ダウンタイムの削減
- 一貫した復旧オペレーション

**参考**: [AWS 発表](https://aws.amazon.com/jp/about-aws/whats-new/2025/11/amazon-sagemaker-hyperpod-programmatic-node-reboot-replacement/)

#### 10. Managed Tiered KV Cache & Intelligent Routing

**発表日**: 2025年11月26日

**主要機能**:
- 2層アーキテクチャ（L1: ローカル CPU メモリ、L2: 分散ストレージ）
- AWS-native 分散階層ストレージ（テラバイト規模）
- 3つのルーティング戦略: Prefix-aware、KV-aware、Round-robin

**効果**:
- レイテンシ: 最大**40%削減**
- スループット: **25%向上**
- コスト: **25%削減**

**参考**: [AWS 発表](https://aws.amazon.com/jp/about-aws/whats-new/2025/11/sagemaker-hyperpod-managed-tiered-kv-cache/)
