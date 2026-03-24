---
title: "Machine Learning on AWS ParallelCluster"
type: "tech"
free: true
---

本ワークショップは [AWS 公式ワークショップ "Machine Learning on AWS ParallelCluster"](https://catalog.workshops.aws/ml-on-aws-parallelcluster/en-US) をベースに、日本語でまとめて補足を入れたものです。

# ワークショップの概要

AWS ParallelCluster は AWS が提供する HPC クラスター管理ツールです。Slurm スケジューラーを使って Amazon EC2 インスタンスを柔軟にスケールアウト・スケールインでき、機械学習の分散トレーニング環境を素早く構築できます。

# ワークショップで学べること

- AWS ParallelCluster 3.x を使った HPC クラスターの構築
- CloudFormation による VPC・FSx for Lustre のインフラ自動構築
- Slurm によるジョブスケジューリングの基本操作（`sbatch`、`squeue`、`sinfo`）
- CPU インスタンス（c5.4xlarge）を使った PyTorch DDP 分散学習
- GPU インスタンス（g5.8xlarge / p4d.24xlarge）を使った Megatron-LM による GPT 事前学習
- Prometheus と Grafana によるクラスターの可観測性（Observability）の実装
- On-Demand Capacity Reservation（ODCR）や GPU Health Check などの運用 Tips

# 全体構成

```mermaid
flowchart TD
    A[概要] --> B[Getting Started<br>VPC・FSx・pcluster CLI]
    B --> C[クラスターの作成と基本操作]
    C --> D[CPU 分散学習<br>PyTorch DDP]
    C --> E[GPU 分散学習<br>Megatron-LM]
    C --> F[NCCL Tests<br>通信性能確認]
    D --> G[可観測性<br>Grafana ダッシュボード]
    E --> G
    F --> G
    G --> H[クリーンアップ]
    C -.-> I[Tips<br>ODCR・Health Check]

    style A fill:#4a90d9,color:#fff
    style B fill:#7b68ee,color:#fff
    style C fill:#7b68ee,color:#fff
    style D fill:#50c878,color:#fff
    style E fill:#ff6b6b,color:#fff
    style F fill:#ff6b6b,color:#fff
    style G fill:#ffa500,color:#fff
    style H fill:#808080,color:#fff
    style I fill:#9e9e9e,color:#fff
```

# 事前準備

- AdministratorAccess 相当の IAM 権限（または以下のサービスへのアクセス権）
  - Amazon EC2、AWS CloudFormation、Amazon FSx、Amazon VPC、AWS Systems Manager
- 必要に応じて GPU インスタンス（g5.8xlarge など）のサービスクォータ引き上げ申請

# アーキテクチャ概要

ワークショップで構築するインフラの全体像を示します。

```mermaid
graph TB
    subgraph VPC["VPC"]
        subgraph Public["パブリックサブネット"]
            HN[ヘッドノード<br>m5.8xlarge]
        end
        subgraph Private["プライベートサブネット"]
            CN1[コンピュートノード 1]
            CN2[コンピュートノード 2]
            CN3[コンピュートノード N]
        end
        FSxL[(FSx for Lustre<br>/fsx<br>1.2 TB)]
        FSxZ[(FSx for OpenZFS<br>/home)]
    end
    User[ユーザー / CloudShell] -->|SSM Session Manager| HN
    HN -->|Slurm ジョブ投入| CN1
    HN -->|Slurm ジョブ投入| CN2
    HN -->|Slurm ジョブ投入| CN3
    HN --- FSxL
    CN1 --- FSxL
    CN2 --- FSxL
    CN3 --- FSxL
    HN --- FSxZ

    style HN fill:#4a90d9,color:#fff
    style CN1 fill:#7b68ee,color:#fff
    style CN2 fill:#7b68ee,color:#fff
    style CN3 fill:#7b68ee,color:#fff
    style FSxL fill:#ff9800,color:#fff
    style FSxZ fill:#ff9800,color:#fff
```

### 主要コンポーネントの説明

| コンポーネント | 説明 |
|------------|------|
| ヘッドノード | Slurm マスターノード。ジョブ受付・スケジューリングを担当 |
| コンピュートノード | Slurm ワーカーノード。ジョブ投入時にオンデマンドで起動し、アイドル後に自動削除 |
| FSx for Lustre | 高性能並列ファイルシステム。`/fsx` にマウントされ全ノードで共有。トレーニングデータやチェックポイントの置き場として使用 |
| FSx for OpenZFS | `/home` ディレクトリ用の共有ストレージ。全ノードで同一のホームディレクトリを参照できる |
| Slurm | ジョブスケジューラー。`sbatch`・`squeue`・`sinfo` などのコマンドでジョブを管理 |