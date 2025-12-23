---
title: "Appendix: GPU"
emoji: "🎶"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["aws", "sagemaker", "hyperpod", "distributed", "infrastructure"]
free: false
---

::::details GPU リソースの効率的な活用

分散学習環境において、物理 GPU を複数のワークロード間で効率的に共有することは、コスト削減とリソース利用率の向上に不可欠です。NVIDIA は GPU 共有のための複数の技術を提供しており、これらをオーケストレーションプラットフォームと統合することで、柔軟なリソース管理が可能になります。本セクションでは、Kubernetes と Slurm における GPU 共有技術の実装と、それぞれの特性を解説します。

#### GPU 共有技術の概要

NVIDIA が提供する主要な GPU 共有技術には、以下の 4 つがあります。

**Multi-Instance GPU (MIG)**: ハードウェアレベルで GPU を分割し、完全に分離された複数のインスタンスを作成します。Ampere および Hopper 世代の GPU（A100、H100 など）でサポートされ、予測可能なパフォーマンスと強固な分離を提供します。

**vGPU (Virtual GPU)**: 仮想化環境向けのエンタープライズソリューションで、QoS 保証とモニタリング機能を備えています。Maxwell 世代以降の幅広い GPU をサポートしますが、ライセンスが必要です。

**Time-Slicing**: 最もシンプルな GPU 共有方式で、複数のプロセスがラウンドロビン方式で GPU を使用します。セットアップが容易で幅広い GPU 世代に対応しますが、メモリ分離がなく、パフォーマンスの予測可能性が低いという欠点があります。

**Multi-Process Service (MPS)**: Hyper-Q 機能を活用して、複数のプロセスが単一 GPU 上で効率的に協調実行できるようにします。Time-Slicing よりも効率的ですが、メモリ分離は提供しません。

これらの技術の特性を比較したフローチャートを以下に示します。

```mermaid
flowchart TD
    Start([GPU 共有が必要])
    
    Q1{Ampere/Hopper<br/>世代 GPU?}
    Q2{ハードウェア分離<br/>が必要?}
    Q3{仮想化環境?}
    Q4{ライセンス<br/>取得可能?}
    Q5{協調的<br/>ワークロード?}
    
    MIG[MIG を使用<br/>ハードウェア分離<br/>予測可能なパフォーマンス]
    vGPU[vGPU を使用<br/>QoS 保証<br/>幅広い GPU 対応]
    MPS[MPS を使用<br/>効率的な共有<br/>低オーバーヘッド]
    TimeSlice[Time-Slicing を使用<br/>シンプルな実装<br/>柔軟な分割]
    
    style Start fill:#1e3a8a,stroke:#3b82f6,color:#fff
    style Q1 fill:#7c3aed,stroke:#a78bfa,color:#fff
    style Q2 fill:#7c3aed,stroke:#a78bfa,color:#fff
    style Q3 fill:#7c3aed,stroke:#a78bfa,color:#fff
    style Q4 fill:#7c3aed,stroke:#a78bfa,color:#fff
    style Q5 fill:#7c3aed,stroke:#a78bfa,color:#fff
    style MIG fill:#065f46,stroke:#10b981,color:#fff
    style vGPU fill:#065f46,stroke:#10b981,color:#fff
    style MPS fill:#065f46,stroke:#10b981,color:#fff
    style TimeSlice fill:#065f46,stroke:#10b981,color:#fff
    
    Start --> Q1
    Q1 -->|Yes| Q2
    Q1 -->|No| Q3
    Q2 -->|Yes| MIG
    Q2 -->|No| Q5
    Q3 -->|Yes| Q4
    Q3 -->|No| Q5
    Q4 -->|Yes| vGPU
    Q4 -->|No| Q5
    Q5 -->|Yes| MPS
    Q5 -->|No| TimeSlice
```

#### Kubernetes における GPU 共有の実装

##### NVIDIA GPU Operator

NVIDIA GPU Operator は、Kubernetes の Operator フレームワークを活用して、GPU ノードの管理に必要な全てのソフトウェアコンポーネントを自動化するツールです。従来の手動セットアップに代わり、GPU ノードを CPU ノードと同じように標準的な OS で管理できるようにします。

GPU Operator が管理する主要なコンポーネントには、NVIDIA ドライバー（コンテナとして実行）、Kubernetes Device Plugin（GPU リソースの公開）、NVIDIA Container Toolkit（実行環境）、GPU Feature Discovery（自動ラベル付与）、DCGM Exporter（メトリクス収集）、MIG Manager（MIG 設定の自動化）が含まれます。

以下の図は、GPU Operator のアーキテクチャを示しています。

```mermaid
graph TB
    subgraph "Kubernetes Cluster"
        subgraph "GPU Operator Namespace"
            GO[GPU Operator<br/>Controller]
            style GO fill:#1e3a8a,stroke:#3b82f6,color:#fff
        end
        
        subgraph "GPU Node"
            Driver[NVIDIA Driver<br/>DaemonSet]
            Toolkit[Container Toolkit<br/>DaemonSet]
            DevicePlugin[Device Plugin<br/>DaemonSet]
            GFD[GPU Feature Discovery<br/>DaemonSet]
            DCGM[DCGM Exporter<br/>DaemonSet]
            MIGMgr[MIG Manager<br/>DaemonSet]
            
            style Driver fill:#166534,stroke:#22c55e,color:#fff
            style Toolkit fill:#166534,stroke:#22c55e,color:#fff
            style DevicePlugin fill:#166534,stroke:#22c55e,color:#fff
            style GFD fill:#166534,stroke:#22c55e,color:#fff
            style DCGM fill:#166534,stroke:#22c55e,color:#fff
            style MIGMgr fill:#166534,stroke:#22c55e,color:#fff
        end
        
        Pod1[Pod 1<br/>GPU Workload]
        Pod2[Pod 2<br/>GPU Workload]
        
        style Pod1 fill:#7c3aed,stroke:#a78bfa,color:#fff
        style Pod2 fill:#7c3aed,stroke:#a78bfa,color:#fff
    end
    
    GO -->|デプロイ| Driver
    GO -->|デプロイ| Toolkit
    GO -->|デプロイ| DevicePlugin
    GO -->|デプロイ| GFD
    GO -->|デプロイ| DCGM
    GO -->|デプロイ| MIGMgr
    
    Driver -->|ドライバー提供| Pod1
    Driver -->|ドライバー提供| Pod2
    Toolkit -->|実行環境| Pod1
    Toolkit -->|実行環境| Pod2
    DevicePlugin -->|リソース公開| Pod1
    DevicePlugin -->|リソース公開| Pod2
    GFD -->|ノードラベル付与| Pod1
    GFD -->|ノードラベル付与| Pod2
    DCGM -->|メトリクス収集| Pod1
    DCGM -->|メトリクス収集| Pod2
    MIGMgr -->|MIG 設定管理| Driver
```

##### Kubernetes での MIG 設定

Kubernetes における MIG の設定は、GPU Operator を通じて宣言的に行われます。管理者は Helm チャートのパラメータで MIG 戦略（single または mixed）を指定し、ノードにラベルを付与することで動的に MIG プロファイルを変更できます。

MIG Manager が MIG の有効化、プロファイルの作成、GPU の再構成を自動的に処理します。設定変更時にはノードの GPU ポッドが一時的に終了されますが、設定完了後は新しい MIG 構成で再スケジュールされます。

以下の図は、A100 GPU における MIG パーティション構成の例を示しています。

```mermaid
graph LR
    subgraph "物理 GPU: A100 40GB"
        subgraph "コンピュートスライス（7個）"
            C1[1]
            C2[2]
            C3[3]
            C4[4]
            C5[5]
            C6[6]
            C7[7]
            
            style C1 fill:#1e40af,stroke:#3b82f6,color:#fff
            style C2 fill:#1e40af,stroke:#3b82f6,color:#fff
            style C3 fill:#1e40af,stroke:#3b82f6,color:#fff
            style C4 fill:#1e40af,stroke:#3b82f6,color:#fff
            style C5 fill:#1e40af,stroke:#3b82f6,color:#fff
            style C6 fill:#1e40af,stroke:#3b82f6,color:#fff
            style C7 fill:#1e40af,stroke:#3b82f6,color:#fff
        end
        
        subgraph "メモリスライス（8個 × 5GB）"
            M1[5GB]
            M2[5GB]
            M3[5GB]
            M4[5GB]
            M5[5GB]
            M6[5GB]
            M7[5GB]
            M8[5GB]
            
            style M1 fill:#065f46,stroke:#10b981,color:#fff
            style M2 fill:#065f46,stroke:#10b981,color:#fff
            style M3 fill:#065f46,stroke:#10b981,color:#fff
            style M4 fill:#065f46,stroke:#10b981,color:#fff
            style M5 fill:#065f46,stroke:#10b981,color:#fff
            style M6 fill:#065f46,stroke:#10b981,color:#fff
            style M7 fill:#065f46,stroke:#10b981,color:#fff
            style M8 fill:#065f46,stroke:#10b981,color:#fff
        end
    end
    
    subgraph "パーティション例: all-balanced"
        P1["3g.20gb<br/>コンピュート: 3<br/>メモリ: 20GB"]
        P2["2g.10gb<br/>コンピュート: 2<br/>メモリ: 10GB"]
        P3["1g.5gb<br/>コンピュート: 1<br/>メモリ: 5GB"]
        P4["1g.5gb<br/>コンピュート: 1<br/>メモリ: 5GB"]
        
        style P1 fill:#7c2d12,stroke:#f97316,color:#fff
        style P2 fill:#7c2d12,stroke:#f97316,color:#fff
        style P3 fill:#7c2d12,stroke:#f97316,color:#fff
        style P4 fill:#7c2d12,stroke:#f97316,color:#fff
    end
    
    C1 --> P1
    C2 --> P1
    C3 --> P1
    M1 --> P1
    M2 --> P1
    M3 --> P1
    M4 --> P1
    
    C4 --> P2
    C5 --> P2
    M5 --> P2
    M6 --> P2
    
    C6 --> P3
    M7 --> P3
    
    C7 --> P4
    M8 --> P4
```

ワークロードは、Pod の仕様で `resources.limits` に MIG リソースタイプ（例として `nvidia.com/mig-1g.5gb`）を指定することで、特定の MIG インスタンスを要求できます。Kubernetes スケジューラーは、ノードラベルと利用可能なリソースに基づいて、適切なノードに Pod を配置します。

##### Kubernetes での Time-Slicing と MPS

Time-Slicing は、GPU Operator の Device Plugin 設定を通じて有効化されます。ConfigMap で各 GPU のレプリカ数を定義し、クラスタポリシーでこの設定を参照することで、GPU が複数のリソースとして公開されます。

MPS のサポートは現在も進化中ですが、特定のユースケースでは Time-Slicing の代替として MPS を活用できます。ただし、Kubernetes における MPS の公式サポートは Time-Slicing ほど成熟していません。

#### Slurm における GPU 共有の実装

##### Slurm の GRES メカニズム

Slurm は、Generic Resource (GRES) スケジューリング機構を通じて GPU を管理します。GRES は任意のリソースタイプを定義・スケジュールできる柔軟な仕組みで、GPU、MIG、MPS などを統一的に扱えます。

設定は主に 3 つのファイルで行われます。`slurm.conf` で管理する GRES タイプを宣言し（例として `GresTypes=gpu,mps`）、各ノードで利用可能なリソースを定義します（例として `Gres=gpu:1,mps:100`）。`gres.conf` でノード上の物理リソースの詳細（デバイスファイル、コアアフィニティなど）を指定します。`cgroup.conf` でリソース分離のための cgroup 設定を定義します。

以下の図は、Slurm の GRES アーキテクチャを示しています。

```mermaid
graph TB
    subgraph "Slurm Cluster"
        subgraph "Controller Node"
            Slurmctld[slurmctld<br/>スケジューラー]
            SlurmConf[slurm.conf<br/>クラスタ設定]
            
            style Slurmctld fill:#1e3a8a,stroke:#3b82f6,color:#fff
            style SlurmConf fill:#1e3a8a,stroke:#3b82f6,color:#fff
        end
        
        subgraph "Compute Node"
            Slurmd[slurmd<br/>ノードデーモン]
            GresConf[gres.conf<br/>リソース定義]
            CgroupConf[cgroup.conf<br/>分離設定]
            GPU[GPU デバイス]
            
            style Slurmd fill:#166534,stroke:#22c55e,color:#fff
            style GresConf fill:#166534,stroke:#22c55e,color:#fff
            style CgroupConf fill:#166534,stroke:#22c55e,color:#fff
            style GPU fill:#166534,stroke:#22c55e,color:#fff
        end
        
        Job1[Job 1<br/>GPU ワークロード]
        Job2[Job 2<br/>GPU ワークロード]
        
        style Job1 fill:#7c3aed,stroke:#a78bfa,color:#fff
        style Job2 fill:#7c3aed,stroke:#a78bfa,color:#fff
    end
    
    SlurmConf -->|設定読み込み| Slurmctld
    Slurmctld -->|ジョブ割り当て| Slurmd
    GresConf -->|リソース情報| Slurmd
    CgroupConf -->|分離ルール| Slurmd
    
    Slurmd -->|リソース提供| Job1
    Slurmd -->|リソース提供| Job2
    GPU -->|デバイスアクセス| Job1
    GPU -->|デバイスアクセス| Job2
```

##### Slurm での MIG 設定

Slurm はバージョン 21.08 以降、MIG をネイティブにサポートしています。MIG 検出ツールを使用して、システム上の MIG デバイスを自動的に検出し、適切な `gres.conf` と `cgroup_allowed_devices_file.conf` を生成できます。

各 MIG デバイスは、その容量に応じた型（例として `1g.6gb`、`3g.20gb`、`4g.24gb` など）で分類されます。ジョブ投入時に `sbatch --gres=gpu:1g.6gb:2` のように要求することで、特定のサイズの MIG インスタンスを取得できます。

Slurm は cgroup と `CUDA_VISIBLE_DEVICES` 環境変数を使用して、各ジョブに割り当てられた MIG デバイスを適切に分離します。これにより、複数のユーザーやジョブが単一の物理 GPU を競合なく使用できます。

ただし、重要な制限として、CUDA の現在の実装では、複数の MIG デバイス間での直接通信がサポートされていません。`CUDA_VISIBLE_DEVICES` は最初の MIG デバイスのみを認識するため、複数の MIG デバイスにまたがる分散学習ジョブは実行できません。各 MIG デバイスは独立した CUDA プロセスとして実行する必要があります。

##### Slurm での MPS 設定

Slurm は MPS を GRES の一種として完全にサポートしています。`gres.conf` で各 GPU に対して MPS 共有数を定義します（例として `Name=mps Count=100 File=/dev/nvidia0`）。この設定により、GPU が複数の MPS シェアに分割されます。

ジョブは `sbatch --gres=mps:25` のように MPS シェアを要求できます。この例では、GPU の 25% を使用することを意味します。Slurm は自動的に `CUDA_MPS_ACTIVE_THREAD_PERCENTAGE` 環境変数を設定し、各ジョブの GPU 使用率を制御します。

ジョブスクリプト内で MPS 制御デーモンを起動し、ワークロード実行後にクリーンアップする必要があります。複数のジョブが同時に実行される場合、それぞれが独自の MPS パイプディレクトリとログディレクトリを持つように設定します。

複数 GPU 環境での MPS 使用には注意が必要です。Slurm の cgroup による GPU 分離と `CUDA_VISIBLE_DEVICES` の設定が、MPS が複数 GPU にアクセスすることを妨げる場合があります。単一 GPU あたり複数ジョブの実行は効率的ですが、複数 GPU にまたがる単一ジョブでの MPS 利用は制限されます。

#### Kubernetes と Slurm の比較

以下の表は、Kubernetes と Slurm における GPU 共有機能の比較をまとめたものです。

| 側面 | Kubernetes | Slurm |
|------|-----------|-------|
| **MIG サポート** | GPU Operator による自動管理、ノードラベルによる動的構成変更 | バージョン 21.08 以降でネイティブサポート、静的設定ファイルベース |
| **MPS サポート** | Time-Slicing を通じた部分的サポート、MPS 自体のサポートは発展途上 | GRES として完全サポート、ジョブごとの MPS デーモン管理 |
| **vGPU サポート** | 通常の GPU として透過的に使用可能、特別な設定不要 | 仮想化ノード上で通常の GPU として扱う、直接的なサポートなし |
| **Time-Slicing** | Device Plugin の拡張オプションで実装、ConfigMap ベースの設定 | GRES のオーバーサブスクリプション機能で実現可能 |
| **設定管理** | 宣言的設定、Git による管理、動的な変更が可能 | 静的設定ファイル、変更時はノードの再起動が必要な場合あり |
| **リソース分離** | Kubernetes の cgroup、Device Plugin による制御 | Slurm の cgroup、GRES による制御 |
| **スケジューリング** | Pod アフィニティ、taint/toleration、リソースクオータ | パーティション、QoS、アカウントポリシー、優先度 |
| **動的スケーリング** | Cluster Autoscaler との統合、クラウドネイティブ | 動的ノード管理機能あり、HPC 環境向けに最適化 |
| **モニタリング** | Prometheus、Grafana、DCGM Exporter | 従来の HPC モニタリングツール、sacct、sstat |
| **エコシステム** | Kubeflow、MLflow、Argo などの ML プラットフォーム | モジュールシステム、並列ファイルシステム、HPC ツールチェーン |

以下の図は、両プラットフォームの特性を視覚的に比較したものです。

```mermaid
graph TB
    subgraph "特性比較"
        subgraph "Kubernetes の強み"
            K1[宣言的な設定管理]
            K2[動的なリソース構成]
            K3[クラウドネイティブな<br/>自動スケーリング]
            K4[豊富な ML/DL<br/>エコシステム]
            K5[マイクロサービス統合]
            
            style K1 fill:#1e40af,stroke:#3b82f6,color:#fff
            style K2 fill:#1e40af,stroke:#3b82f6,color:#fff
            style K3 fill:#1e40af,stroke:#3b82f6,color:#fff
            style K4 fill:#1e40af,stroke:#3b82f6,color:#fff
            style K5 fill:#1e40af,stroke:#3b82f6,color:#fff
        end
        
        subgraph "Slurm の強み"
            S1[成熟した HPC<br/>ジョブ管理]
            S2[厳密なリソース<br/>割り当て]
            S3[バッチ処理の<br/>最適化]
            S4[HPC ツールチェーン<br/>との統合]
            S5[長時間ジョブの<br/>管理]
            
            style S1 fill:#065f46,stroke:#10b981,color:#fff
            style S2 fill:#065f46,stroke:#10b981,color:#fff
            style S3 fill:#065f46,stroke:#10b981,color:#fff
            style S4 fill:#065f46,stroke:#10b981,color:#fff
            style S5 fill:#065f46,stroke:#10b981,color:#fff
        end
        
        subgraph "共通の制限"
            L1[MIG デバイス間の<br/>CUDA 通信不可]
            L2[MPS でのメモリ<br/>分離なし]
            L3[複数 GPU での MPS<br/>制限]
            
            style L1 fill:#7c2d12,stroke:#f97316,color:#fff
            style L2 fill:#7c2d12,stroke:#f97316,color:#fff
            style L3 fill:#7c2d12,stroke:#f97316,color:#fff
        end
    end
```

##### 選択のガイドライン

**Kubernetes を選択すべき場合**、開発環境やプロトタイピングで柔軟性が必要な場合、短時間の実験を多数実行する場合（ハイパーパラメータチューニングなど）、ML 推論サービスや Web アプリケーションと統合する場合、クラウド環境で動的なスケーリングが必要な場合、マイクロサービスアーキテクチャの一部として GPU ワークロードを実行する場合が挙げられます。

**Slurm を選択すべき場合**、数日から数週間にわたる長期的な学習ジョブを実行する場合、厳密なリソース割り当てとアカウンティングが必要な場合、既存の HPC インフラストラクチャと統合する場合、複数のユーザーやプロジェクト間で公平なリソース共有が必要な場合、バッチ処理中心のワークロードを実行する場合が適しています。

分散学習の文脈では、両者とも MIG と MPS をサポートしていますが、それぞれの強みが異なります。Kubernetes は、細粒度のリソース割り当てによるマルチテナント環境の実現、GPU Operator による自動化されたインフラストラクチャ管理、開発と実験の迅速なイテレーションを提供します。Slurm は、大規模で長期的な学習ジョブの安定実行、厳密なリソース管理と公平性の保証、従来の HPC ワークフローとのシームレスな統合を提供します。

AWS SageMaker HyperPod は両方のバックエンドをサポートしており、ユーザーは自身のワークロードの特性に基づいて最適な環境を選択できます。HyperPod Slurm 版は長期的な学習ジョブと厳密なリソース管理に、HyperPod EKS 版は柔軟な開発環境と ML プラットフォームとの統合に適しています。
::::