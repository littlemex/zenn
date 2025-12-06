---
title: "大規模基盤モデル学習のインフラストラクチャ"
emoji: "🚀"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["aws", "sagemaker", "hyperpod", "ai", "ml"]
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

**本章では大規模基盤モデル学習に求められるインフラストラクチャについて整理します。**

---

AWS Principle WW Solutions Architect,GenAI, Keita Watanabe さんの [Scalable Infrastructure for Large-Scale AI Training with AWS Sagemaker Hyperpod](https://speakerdeck.com/keitaw/scalable-infrastructure-for-large-scale-ai-training-with-aws-sagemaker-hyperpod-at-singapore-ai-hour) 資料の流れを参照しながら初学者向けに情報を整理します。

# インフラストラクチャ

:::message
***Point! 大規模基盤モデル学習に必要なインフラストラクチャの独自構築は非常に難易度が高い***
:::

以下に大規模基盤モデルの学習・推論で必要な技術スタックの概観を示します。本章では一番下のインフラストラクチャ層について整理します。規模が大きくなるにつれてオンプレ上で独自でこれらのインフラストラクチャを構築するハードルは非常に高いものとなります。故障時の交換などで全系が止まってしまう、それを正常に検知する、交換完了まで系を縮退して運用する、など考慮事項を考えるとなかなかやばそうですね。

```mermaid
graph TB
    subgraph Application["アプリケーション"]
        direction LR
        ML[**ML Frameworks**<br/>PyTorch, NxD, JAX]
    end
    
    subgraph Control["コントロール"]
        direction LR
        Slurm[**Slurm**<br/>ジョブスケジューラー]
        K8s[**Kubernetes**<br/>オーケストレーション]
    end
    
    subgraph Infrastructure["インフラストラクチャ"]
        direction LR
        Compute[**Compute**<br/>EC2 インスタンス]
        Network[**Network**<br/>EFA<br/>NVLink<br/>InfiniBand]
        Storage[**Storage**<br/>Amazon S3<br/>FSx for Lustre<br/>Amazon EBS]
    end
    
    ML --> Slurm
    ML --> K8s
    Slurm --> Compute
    Slurm --> Network
    Slurm --> Storage
    K8s --> Compute
    K8s --> Network
    K8s --> Storage
    
    style Application fill:#e1f5ff
    style Control fill:#fff4e1
    style Infrastructure fill:#f0fff4
```

*図: AWS で大規模基盤モデルを構築する際のビルディングブロック*

## Amazon EC2 UltraClusters

![](/images/books/distributed-experiment-collection/ec2-ultraclusters.png)

*図: [Amazon EC2 UltraClusters](https://aws.amazon.com/ec2/ultraclusters/)* の構成

Amazon EC2 UltraClusters は、大規模な AI/ML ワークロード向けに設計された物理的なハードウェアインフラストラクチャの集合体です。数千から数万の GPU やカスタムシリコンを高速ネットワークで相互接続し、単一の統合されたコンピューティング環境を提供します。

::::details UltraClusters の必要性

なぜこのようなインフラストラクチャが必要なのでしょうか。ウェブサービスのように Amazon EC2 を複数台使いたいなら、オートスケールして Multi AZ 構成にすればいいのではないか、と思う方がいるかもしれません。

## ウェブサービスと基盤モデル学習のインフラストラクチャの違い

:::message
**通常のウェブサービス（疎結合）**
- 各サーバーは独立して動作
- ノード間の通信は少ない
- レイテンシは数十～数百ミリ秒でも問題なし
- → Multi AZ 構成で高可用性を実現
:::

:::message
**基盤モデル学習（密結合）**
- 全 GPU が常に同期して計算
- 毎回の学習ステップで大量のデータを交換（例: Llama 3 70B で 245GB/iteration）
- **レイテンシはマイクロ秒単位が必須**
- → Single AZ の物理的近接性が必須
:::

## なぜ Single AZ なのか

**Multi AZ の問題点**
- AZ 間レイテンシ: 1-5ms
- 専用高速ネットワークなし
- 学習時間が数十倍～数百倍に増加し実用不可能

**Single AZ（UltraClusters）の利点**
- 物理的な近接性: ラック内/ラック間で配置が可能
- 専用ネットワーク: EFA、NVLink による超低レイテンシ（マイクロ秒単位）

## FSx for Lustre も Single AZ

チェックポイント保存時、数百の GPU が同時にストレージへアクセスします。仮に Multi AZ 構成が実現できたとしても帯域幅が不足し、保存に数十分かかる可能性があります。Single AZ では EFA 経由で数百 GB/s の帯域幅を実現し、保存時間を最小化します。

## まとめ

Amazon EC2 UltraClusters は、通常のウェブサービスの「分散」「高可用性」とは逆の「集中」「物理的近接性」を重視した設計です。これは大規模基盤モデルの学習という密結合ワークロードの要件（超低レイテンシ、超高帯域幅）を満たすために必要不可欠なアーキテクチャです。
::::


## UltraClusters の主要コンポーネント概観

以下に UltraClusters の主要コンポーネントを整理しました。古いインスタンスタイプなどは省略しています。

```mermaid
graph TB
    subgraph UltraCluster["EC2 UltraClusters"]
        direction TB
        
        subgraph Compute["コンピュートインスタンス"]
            P5[P5 インスタンス<br/>NVIDIA H100<br/>8x 80GB GPU]
            
            subgraph P6e["P6e UltraServer<br/>72 GPU"]
                GB200[GB200<br/>13.3TB メモリ]
                GB300[GB300<br/>約20TB メモリ]
            end
            
            Trn3[Trn3 UltraServer<br/>AWS Trainium3<br/>最大144チップ]
        end
        
        subgraph Network["ネットワーク"]
            subgraph EFAGroup["EFA: ノード間通信"]
                EFAv3[EFA v3<br/>3.2Tbps<br/>P5, Trn3]
                EFAv4[EFA v4<br/>28.8Tbps<br/>P6e]
            end
            
            subgraph NVLinkGroup["NVLink: GPU間通信"]
                NVLink4[NVLink 4.0<br/>900GB/s<br/>H100]
                subgraph NVLinkC2CGroup["NVLink-C2C"]
                    C2C[Grace-Blackwell<br/>GB200/GB300]
                end
            end
            
            subgraph NeuronGroup["Neuron: Trainium間通信"]
                NeuronLink[NeuronLink-v4<br/>2TB/s per chip]
                NeuronSwitch[NeuronSwitch-v1<br/>368TB/s total]
            end
        end
        
        subgraph Storage["ストレージ"]
            FSx[FSx for Lustre<br/>並列ファイルシステム]
            S3[Amazon S3<br/>オブジェクトストレージ]
        end
    end
    
    P5 --> EFAv3
    P5 --> NVLink4
    
    P6e --> EFAv4
    P6e --> C2C
    
    Trn3 --> EFAv3
    Trn3 --> NeuronLink
    Trn3 --> NeuronSwitch
    
    EFAv3 --> FSx
    EFAv4 --> FSx
    
    FSx -.S3統合.-> S3
    
    style UltraCluster fill:#e8f4f8
    style Compute fill:#d4e9f7
    style P6e fill:#c4d9e7
    style Network fill:#fff9d4
    style EFAGroup fill:#ffe9d4
    style NVLinkGroup fill:#e9d4ff
    style NVLinkC2CGroup fill:#d9c4ef
    style NeuronGroup fill:#d4ffe9
    style Storage fill:#f7e9d4
```

| 世代 | インスタンス | 最大スケール | ネットワーク | 特徴 |
|------|-----------|------------|------------|------|
| **1.0** | P4d (A100) | 数千GPU | 400Gbps EFA | 初代大規模クラスター |
| **2.0** | P5 (H100) | 20,000 GPU | 3,200Gbps EFA | NVLink 強化 |
| **3.0** | Trn3, P6e (GB200/GB300) | 100万チップ | 28.8Tbps EFA | 前世代の 10 倍スケール |

::::details 図の用語説明
## コンピュートインスタンス

### Accelerator

:::message
役割: AI/ML の計算を高速に実行する専用チップです。
:::

- **GPU**: NVIDIA 製のアクセラレータ（H100、H200、B200 など）
- **Trainium**: AWS が独自開発した ML 専用チップ、コスト効率に優れる

## ネットワーク

大規模モデル訓練では、GPU 間で頻繁にデータを交換する必要があります。ネットワークの性能が訓練速度に直結します。詳細は割愛しますが LSI チップ内（もしくは SoC のパッケージ内）、やボード内、サーバーラック 内、ラック間など複数の通信があります。

### EFA（Elastic Fabric Adapter）- ノード間通信

:::message
役割: 異なるサーバー（ノード）間を接続する高速ネットワーク
:::

AWS が開発した専用ネットワークインターフェースで、以下の特徴があります。

- **超低レイテンシ**: マイクロ秒単位（1/1,000,000 秒）の通信遅延
- **OS バイパス**: カーネルを介さずに直接通信（RDMA 技術）
- **高帯域幅**: 世代により 400Gbps～28.8Tbps

### NVLink - GPU 間通信（ノード内）

:::message
役割: 同じサーバー内の GPU 同士を接続する超高速インターコネクト
:::

NVIDIA 製の GPU 専用接続技術で、PCIe よりも遥かに高速です。

- **NVLink 4.0**: 900GB/s（H100）- 1つの GPU から見た帯域幅
- **NVLink-C2C**: Grace CPU と Blackwell GPU（GB200/GB300）を接続

### NeuronLink と NeuronSwitch - Trainium 間通信

:::message
役割: AWS Trainium チップ専用の高速インターコネクト
:::

- **NeuronLink-v4**: チップあたり2TB/s の帯域幅
- **NeuronSwitch-v1**: 144チップを All-to-all 接続するファブリック、総帯域幅 368TB/s

## ストレージ

### FSx for Lustre - 分散並列ファイルシステム

:::message
役割: 高速な共有ストレージ、チェックポイントと学習データの保存
:::

- **並列アクセス**: 数百～数千の GPU から同時に読み書き可能
- **高帯域幅**: 最大数百 GB/s
- **低レイテンシ**: サブミリ秒（1/1,000 秒未満）
- **EFA アクセス**: 専用高速ネットワークを使用するため、通常のストレージより高速

### Amazon S3 - オブジェクトストレージ

:::message
役割: 大容量データの長期保存
:::

- **FSx との関係**: FSx for Lustre のバックエンドとして統合可能。頻繁にアクセスするデータは FSx、長期保存は S3 という使い分けが一般的
::::

## コンピュートインスタンス

UltraClustes のコンピュートインスタンスには NVIDIA GPU、Trainium アクセラレータが主に分類として存在します。基本的には NVIDIA GPU を利用するケースが多いと思いますが、Trainium が利用可能なモデルアーキテクチャでは GPU と比べてコスト効率よく大規模基盤モデルの学習ができるため、コストとパフォーマンス観点では Trainium が自分たちのモデルで利用可能かどうかについては常に選択肢に入れておくべきでしょう。

### NVIDIA GPU インスタンス

::::details GPU インスタンスタイプ表
| インスタンスタイプ | Accelerator (数) | VRAM | Local SSD | Network (EFA) | 備考 |
|------------------|---------|-----------|----------|--------------|------|
| P4d.24xlarge | A100 (8) | 320GB | 8x1TB NVMe | 400Gbps | NVLink 3.0, 40GB/GPU |
| P4de.24xlarge | A100 (8) | 640GB | 8x1TB NVMe | 400Gbps | NVLink 3.0, 80GB/GPU |
| P5.48xlarge | H100 (8) | 640GB | 8x3.8TB NVMe | 3,200Gbps | NVLink 4.0, 80GB/GPU |
| P5e.48xlarge | H200 (8) | 1,128GB | 8x3.8TB NVMe | 3,200Gbps | NVLink 4.0, 141GB/GPU |
| P5en.48xlarge | H200 (8) | 1,128GB | 8x3.8TB NVMe | 3,200Gbps | Intel CPU, 141GB/GPU |
| P6-B200.48xlarge | B200 (8) | 1,432GB | - | 3,200Gbps | 179GB/GPU |
| P6-B300.48xlarge | B300 (8) | 2,148GB | - | 6,400Gbps | 268GB/GPU, EFA 2倍 |
| P6e-GB200 (UltraServer) | GB200 (72) | 13.3TB | - | 28.8Tbps | NVLink-C2C, 185GB/GPU |
| P6e-GB300 (UltraServer) | GB300 (72) | ~20TB | - | 28.8Tbps | NVLink-C2C, ~278GB/GPU |
::::

::::details インスタンスタイプ選定ガイド

## NVIDIA Compute Capability

NVIDIA GPU は世代ごとに **Compute Capability**（計算能力レベル）が定義されており、サポートされる機能や命令セットが異なります。詳細は [NVIDIA CUDA GPUs ページ](https://developer.nvidia.com/cuda-gpus) を参照してください。

| GPU | Compute Capability | 世代 | 主な特徴 |
|-----|-------------------|------|---------|
| **A100** | 8.0 | Ampere | FP64 Tensor Core、構造化スパース |
| **H100** | 9.0 | Hopper | Transformer Engine (FP8)、Thread Block Clusters |
| **H200** | 9.0 | Hopper | H100 と同じ、メモリ容量・帯域幅強化 |
| **B200/B300** | 10.0 | Blackwell | FP4 サポート、第5世代 Tensor Core |

新しい世代ほど、LLM や Transformer モデルに最適化された機能が追加されています。

https://www.nvidia.com/ja-jp/technologies/

## 選定時の主要考慮事項

### 1. アクセラレータメモリ容量

モデルサイズとバッチサイズに応じて必要なメモリが決まります。単一インスタンスで良いのか UltraServer が必要になるのかを考える必要があります。

**目安**:
- 小規模モデル（～13B）: 40-80GB（A100、H100）
- 中規模モデル（～70B）: 80-141GB（H100、H200）
- 大規模モデル（70B～）: 141GB+（H200、B200、B300）
- 超大規模モデル（405B～）: UltraServer（GB200/GB300, 72 GPU）

メモリ不足の場合、Model Parallel などの分割手法が必要になり、実装が複雑化します。

### 2. ネットワーク帯域幅とレイテンシ

複数 GPU での学習が必要な場合は GPU 間通信の性能が学習時間に直結します。

**EFA 帯域幅**:
- P4d: 400Gbps
- P5/P5e/P5en/P6-B200: 3,200Gbps（8倍）
- P6-B300: 6,400Gbps（16倍）
- P6e UltraServer: 28.8Tbps（72倍）

大規模クラスター（64ノード以上）では、高帯域幅が学習効率に大きく影響します。

### 3. コストと利用効率

**常に最新が良いとは限らない理由**

- **キャパシティ確保**: 新世代は入手困難な場合あり（サービスクォータ、物理的な供給制約）
- **コスト**: 新世代は高額（ただし性能あたりのコストは改善される傾向）
- **ソフトウェア成熟度**: 新世代は対応ライブラリやツールが限定的な場合あり

**実用的なアプローチ**:
1. **小規模実験**: 枯れた P5（H100）で検証、十分な実績とエコシステム
2. **中規模訓練**: P5e（H200）、メモリ容量と費用のバランス
3. **大規模本番**: 要件に応じて P6-B300 や P6e UltraServer を検討
::::

### Trainium インスタンス

::::details Trainium インスタンスタイプ表
| インスタンスタイプ | Accelerator (数) | HBM Memory | Local SSD | Network (EFA) | 備考 |
|------------------|-----------------|-----------|----------|--------------|------|
| Trn1.32xlarge | Trainium (16) | 512GB | - | 800Gbps (8x100Gbps) | 32GB/chip |
| Trn1n.32xlarge | Trainium (16) | 512GB | - | 1,600Gbps (16x100Gbps) | EFA 2倍、ネットワーク強化版 |
| Trn2.48xlarge | Trainium2 (16) | 8,192GB | - | 3,200Gbps (16x200Gbps) | 512GB/chip、Trn1 の16倍メモリ |
| Trn2u.48xlarge | Trainium2 (16) | 8,192GB | - | 3,200Gbps (16x200Gbps) | Trn2 の別バリアント |
| Trn3 UltraServer | Trainium3 (最大144) | - | - | EFA v3 (3.2Tbps推定) | NeuronLink-v4 (2TB/s per chip)NeuronSwitch-v1 (368TB/s total) |
::::

## ネットワーク

#### ネットワークトポロジー

```mermaid
graph TB
    subgraph Rack["ラック内通信"]
        GPU1[GPU 0] ---|NVLink 900GB/s| GPU2[GPU 1]
        GPU2 ---|NVLink| GPU3[GPU 2]
        GPU3 ---|NVLink| GPU4[GPU 7]
        
        Node1[ノード] ---|EFA 3,200Gbps| Node2[ノード]
    end
    
    subgraph CrossRack["ラック間通信"]
        Rack1[ラック A] ---|EFA Network<br/>Spine-Leaf| Rack2[ラック B]
        Rack2 ---|EFA Network| Rack3[ラック C]
    end
    
    style Rack fill:#e1f5ff
    style CrossRack fill:#fff4e1
```

- **ノード内通信**: NVLink による超高速 GPU 間通信
- **ノード間通信**: EFA による低レイテンシ・高帯域幅通信
- **ラック間通信**: Spine-Leaf トポロジーによるスケーラブルなネットワーク

### ストレージオプション

大規模モデル訓練では、チェックポイント保存、データセット読み込み、結果保存のために高性能なストレージが必要です。

#### Amazon S3

- **容量**: 事実上無制限
- **用途**: データセット保存、チェックポイントバックアップ
- **料金**: 低コスト（$0.023/GB/月、us-east-1）

#### FSx for Lustre

- **帯域幅**: 最大数百GB/s
- **レイテンシ**: サブミリ秒
- **用途**: 訓練中のチェックポイント、高速データ読み込み
- **S3 統合**: S3 バケットを自動マウント可能

#### Amazon EBS

- **種類**: gp3, io2, io2 Block Express
- **最大帯域幅**: 8,000MB/s（io2 Block Express）
- **用途**: OS、アプリケーション、一時データ

## AWS AI プラットフォームの選択肢

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

### 注意事項

- **ParallelCluster**: すべてのインスタンスタイプをサポートしますが、Spot や MIG は手動設定が必要
- **HyperPod (Slurm)**: 現在 Spot と MIG は未サポート（将来的にサポート予定の可能性）
- **HyperPod (EKS)**: Spot と MIG を完全サポートし、自動管理機能を提供
- **EKS**: すべてのインスタンスをサポートしますが、Spot や MIG の管理は自前で実装

## re:Invent 2025 主要アップデート

2025年11月～12月の AWS re:Invent で発表された、大規模 AI/ML インフラストラクチャに関する主要なアップデートを整理します。

### ハードウェア・インフラストラクチャ

#### 1. Trn3 UltraServer（一般提供開始）

**発表日**: 2025年12月2日

**主要改善**:
- Trainium2 比で計算性能4.4倍、エネルギー効率4倍
- メモリ帯域幅4倍、レイテンシ1/4
- EC2 UltraClusters 3.0 で最大100万チップ接続可能（前世代の10倍）

**実測パフォーマンス**（OpenAI GPT-OSS モデル）:
- チップあたりのスループット: 3倍向上
- 応答時間: 4倍高速化

**参考**: [AWS ブログ記事](https://www.aboutamazon.com/news/aws/trainium-3-ultraserver-faster-ai-training-lower-cost)

#### 2. P6e-GB300 UltraServer（一般提供開始）

**発表日**: 2025年12月2日

**主要スペック**:
- NVIDIA GB300 NVL72 搭載
- GPU メモリ: P6e-GB200 の1.5倍（約20TB）
- FP4 コンピュート: P6e-GB200 の1.5倍

**用途**: 高コンテキスト要求モデル、推論技術（Reasoning）、Agentic AI

**参考**: [AWS 発表](https://aws.amazon.com/jp/about-aws/whats-new/2025/12/amazon-ec2-p6e-gb300-ultraservers-nvidia-gb300-nvl72-generally-available/)

#### 3. AWS AI Factories

**コンセプト**: 顧客データセンター内への専用 AI インフラ直接展開

**提供内容**:
- NVIDIA GB300、AWS Trainium チップ
- Amazon Bedrock、SageMaker AI などのサービス
- AWS が完全管理（展開、スケーリング、パッチ、セキュリティ）

**メリット**:
- 既存のデータセンタースペース、ネットワーク、電力を活用
- データ主権と規制要件への対応
- 導入期間の大幅短縮

**実例**: サウジアラビアの HUMAIN と協力し、GB300 GPU を含む最大150,000個の AI チップを備えた「AI ゾーン」を構築中

**参考**: [AWS ニュース](https://www.aboutamazon.com/news/aws/aws-data-centers-ai-factories)

### SageMaker HyperPod の新機能

#### 4. Spot Instances サポート（HyperPod EKS）

**発表日**: 2025年11月24日

**主要機能**:
- On-Demand 比で最大90%のコスト削減
- Karpenter による自動スケーリング
- 中断処理の完全自動化（2分前通知、グレースフル eviction）
- 自動容量再プロビジョニング

**対応インスタンス**: ml.p4d.24xlarge、ml.p5.48xlarge、ml.p5e.48xlarge など

**具体例**（p4d.24xlarge × 10台、24時間×30日）:
- 従来（On-Demand 100%）: $236,000/月
- 新方式（Spot 80% + On-Demand 20%）: $73,400/月
- **削減額: $162,600/月（69%削減）**

**参考**: [AWS 発表](https://aws.amazon.com/jp/about-aws/whats-new/2025/11/amazon-sagemaker-hyperpod-spot-instances/)

#### 5. MIG（Multi-Instance GPU）サポート（HyperPod EKS）

**発表日**: 2025年11月24日

**主要機能**:
- 1つの GPU を最大7つの独立したパーティションに分割
- ハードウェアレベルでの分離と予測可能なパフォーマンス
- コンソールまたは Kubernetes での簡単設定

**対応 GPU と MIG プロファイル**:

**A100 GPU (ml.p4d.24xlarge)**:
- 1g.5gb（5GB）: 1 GPU → 7パーティション
- 2g.10gb（10GB）: 1 GPU → 3パーティション
- 3g.20gb（20GB）: 1 GPU → 2パーティション
- 7g.40gb（40GB）: GPU 全体使用

**H100 GPU (ml.p5.48xlarge)**:
- 1g.10gb（10GB）: 1 GPU → 7パーティション
- 2g.20gb（20GB）: 1 GPU → 3パーティション
- 3g.40gb（40GB）: 1 GPU → 2パーティション
- 7g.80gb（80GB）: GPU 全体使用

**H200 GPU (ml.p5e.48xlarge, ml.p5en.48xlarge)**:
- 1g.18gb（18GB）: 1 GPU → 7パーティション
- 2g.35gb（35GB）: 1 GPU → 3パーティション
- 3g.71gb（71GB）: 1 GPU → 2パーティション
- 7g.141gb（141GB）: GPU 全体使用

**効果**（ml.p4d.24xlarge、8 × A100 80GB の例）:
- 従来: 8タスク並行実行、GPU 利用率 ~10%
- MIG 有効化: 56タスク並行実行（8 GPU × 7パーティション）、GPU 利用率 ~70-80%
- **同じコストで7倍のスループット**

**参考**: [AWS 発表](https://aws.amazon.com/jp/about-aws/whats-new/2025/11/sagemaker-hyperpod-nvidia-multi-instance-gpu/)

#### 6. Checkpointless Training

**発表日**: 2025年12月

**主要機能**:
- チェックポイント-再起動サイクルを排除
- ピアツーピアの状態復旧
- 健全なピアを使用した即座のリカバリー
- 手動介入ゼロの自動検出・復旧

**効果**:
- リカバリー時間を**80%以上削減**（時間単位から分単位へ）
- 数千の AI アクセラレータにスケール可能
- トレーニングの勢いを維持

**実績**: Amazon Nova モデルは数万のアクセラレータでこの技術を使用してトレーニング

**参考**: [AWS ブログ](https://aws.amazon.com/jp/blogs/aws/introducing-checkpointless-and-elastic-training-on-amazon-sagemaker-hyperpod/)

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

### 推論の新機能

#### 11. EAGLE Speculative Decoding

**発表日**: 2025年11月

**主要機能**:
- モデル自身の隠れ層表現を利用した複数トークン並列予測
- 外部 draft モデル不要
- EAGLE 2 と EAGLE 3 の両方をサポート

**対応モデル**:
- EAGLE 3: LlamaForCausalLM、Qwen3ForCausalLM、GptOssForCausalLM など
- EAGLE 2: Qwen3NextForCausalLM

**効果**（Qwen3-32B、ml.p5.48xlarge）:
- スループット: **2.5倍向上**（No EAGLE 比）
- TTFT（Time to First Token）: 20-40%短縮
- TPOT（Time Per Output Token）: 56-63%短縮

**参考**: [AWS ブログ](https://aws.amazon.com/jp/blogs/machine-learning/amazon-sagemaker-ai-introduces-eagle-based-adaptive-speculative-decoding-to-accelerate-generative-ai-inference/)

#### 12. Bidirectional Streaming

**発表日**: 2025年11月

**主要機能**:
- HTTP/2 と WebSocket による双方向ストリーミング
- 単一の永続的接続でリアルタイムにデータが流れる
- 60秒ごとの ping/pong によるヘルスモニタリング

**用途**:
- 音声テキスト変換
- 音声エージェント
- リアルタイム翻訳

**効果**:
- ネットワークオーバーヘッド削減（単一接続）
- レイテンシー低減（リアルタイム処理）
- 音声認識の応答性向上

**参考**: [AWS ブログ](https://aws.amazon.com/jp/blogs/machine-learning/introducing-bidirectional-streaming-for-real-time-inference-on-amazon-sagemaker-ai/)

#### 13. Flexible Training Plans for Inference Endpoints

**発表日**: 2025年11月26日

**主要機能**:
- 推論エンドポイント用の GPU 容量事前予約
- 計画的な評価や本番ピーク時の容量保証
- 自動プロビジョニング

**効果**:
- モデル評価サイクルの予測可能性向上
- 本番ピーク時の安定したパフォーマンス
- インフラ管理の工数削減（数週間→自動）

**参考**: [AWS 発表](https://aws.amazon.com/jp/about-aws/whats-new/2025/11/sagemaker-ai-flexible-training-plans-inference/)

### その他の重要発表

#### 14. Amazon EKS Capabilities

**発表日**: 2025年12月

**提供内容**:
- **Argo CD**: GitOps による継続的デプロイメント
- **ACK（AWS Controllers for Kubernetes）**: K8s から AWS リソースを管理
- **KRO（Kube Resource Orchestrator）**: 複雑なリソースの抽象化

**特徴**:
- フルマネージド（EKS サービス所有アカウントで実行）
- 自動アップグレード
- IAM Identity Center との統合

**料金**: 使用分のみ課金、前払い・最低料金なし

**参考**: [AWS ブログ](https://aws.amazon.com/jp/blogs/aws/announcing-amazon-eks-capabilities-for-workload-orchestration-and-cloud-resource-management/)

#### 15. Amazon Nova Forge

**発表日**: 2025年11月

**主要機能**:
- Nova AI モデルを使用した独自フロンティアモデル構築
- プレトレーニング、ミッドトレーニング、ポストトレーニングからの開発
- データブレンディングによる壊滅的忘却の削減
- 強化学習（RL）サポート

**利用可能リージョン**: US East (N. Virginia)

**参考**: [AWS ブログ](https://aws.amazon.com/jp/blogs/aws/introducing-amazon-nova-forge-build-your-own-frontier-models-using-nova/)

#### 16. Serverless Customization in SageMaker AI

**発表日**: 2025年12月

**主要機能**:
- サーバーレスモデルカスタマイズ（インフラ管理不要）
- Supervised Fine-Tuning、DPO、RLVR、RLAIF をサポート
- モデルカスタマイズを数ヶ月から数日に短縮

**対応モデル**: Amazon Nova、DeepSeek、GPT-OSS、Llama、Qwen など

**参考**: [AWS ブログ](https://aws.amazon.com/jp/blogs/aws/new-serverless-customization-in-amazon-sagemaker-ai-accelerates-model-fine-tuning/)

## スケールと制約の理解

大規模 AI/ML ワークロードを計画する際、複数のレベルで制約が存在することを理解する必要があります。

### 制約の階層構造

```mermaid
graph TB
    subgraph Constraints["制約の階層"]
        direction TB
        
        Instance[インスタンスレベル<br/>━━━━━━━━━━<br/>P5: 8 GPU 固定<br/>P6e: 72 GPU 固定<br/>Trn3: 最大144チップ]
        
        UltraCluster[UltraClusters レベル<br/>━━━━━━━━━━<br/>物理的最大スケール<br/>3.0: 100万チップ]
        
        Platform[プラットフォームレベル<br/>━━━━━━━━━━<br/>HyperPod: 数千ノード<br/>ParallelCluster: 設定次第]
        
        Quota[AWS サービスクォータ<br/>━━━━━━━━━━<br/>実質的な制約<br/>デフォルト: 0〜数台<br/>要事前申請]
    end
    
    Instance --> UltraCluster
    UltraCluster --> Platform
    Platform --> Quota
    
    style Instance fill:#e1f5ff
    style UltraCluster fill:#fff4e1
    style Platform fill:#f0fff4
    style Quota fill:#ffe1e1
```

### 1. インスタンスレベルの制約

各インスタンスタイプには固定の GPU/Trainium 数があります。

| インスタンスタイプ | GPU/チップ数 | GPUメモリ | 総GPUメモリ | vCPU | システムメモリ |
|------------------|------------|----------|-----------|------|--------------|
| **P5 (ml.p5.48xlarge)** | 8x H100 | 80GB/GPU | 640GB | 192 | 2,048GB |
| **P6e-GB200 (ml.p6e.48xlarge)** | 72x GB200 | 185GB/GPU | 13.3TB | - | - |
| **P6e-GB300 (ml.p6e.48xlarge)** | 72x GB300 | ~278GB/GPU | ~20TB | - | - |
| **P4d (ml.p4d.24xlarge)** | 8x A100 | 40GB/GPU | 320GB | 96 | 1,152GB |
| **Trn1 (ml.trn1.32xlarge)** | 16x Trainium | - | 512GB HBM | 128 | 512GB |
| **Trn3 UltraServer** | 最大144x Trainium3 | - | - | - | - |

**重要なポイント**:
- インスタンスタイプごとに GPU/チップ数は固定
- ノード数を増やすことでスケール（例: P5 × 8ノード = 64 GPU）

### 2. EC2 UltraClusters レベルの制約

物理インフラストラクチャの限界です。

| 世代 | 最大スケール | 実例 |
|------|------------|------|
| **UltraClusters 2.0** | 20,000 GPU | P5 インスタンス |
| **UltraClusters 3.0** | 100万チップ | Trn3、P6e（前世代の10倍）|

**実績**:
- **Project Rainier**（Anthropic）: 50万個以上の Trainium2 チップを接続
- **Amazon Nova**: 数万のアクセラレータでトレーニング

### 3. プラットフォームレベルの制約

#### SageMaker HyperPod

**ノード数の制約**:
- 明示的な上限の公開情報は限定的
- 実質的に数百～数千ノード規模に対応
- 大規模クラスター（512ノード以上）は AWS との調整が必要

**インスタンスグループ**:
- 複数のインスタンスグループを作成可能
- 各グループで異なるインスタンスタイプを指定可能
- 例: GPUグループ + Trainiumグループの混在

#### AWS ParallelCluster

**MaxCount 設定**:
```yaml
Scheduling:
  Queues:
    - Name: compute
      ComputeResources:
        - Name: gpu-nodes
          InstanceType: p5.48xlarge
          MinCount: 0
          MaxCount: 128  # 最大ノード数
```

- 理論上は数千ノードまで対応可能
- 実際の制限はサービスクォータに依存

### 4. AWS サービスクォータ（最重要）

これが実質的な制約になります。

#### デフォルトクォータの例

| サービス | リソース | デフォルトクォータ |
|---------|---------|-----------------|
| SageMaker | ml.p5.48xlarge インスタンス | 0〜2台 |
| SageMaker | ml.p4d.24xlarge インスタンス | 0〜2台 |
| SageMaker | ml.trn1.32xlarge インスタンス | 0〜2台 |
| EC2 | P instances vCPUs | 128〜512 vCPUs |
| EC2 | Running On-Demand instances | アカウント・リージョン依存 |

**重要**: デフォルトは非常に小さく、大規模クラスターには**事前申請が必須**です。

#### サービスクォータの確認方法

```bash
# SageMaker のクォータを確認
aws service-quotas list-service-quotas \
  --service-code sagemaker \
  --region us-east-1

# EC2 のクォータを確認
aws service-quotas list-service-quotas \
  --service-code ec2 \
  --region us-east-1

# 特定のクォータを取得
aws service-quotas get-service-quota \
  --service-code sagemaker \
  --quota-code L-XXXXXX \
  --region us-east-1
```

#### クォータ引き上げ申請

```bash
# クォータ引き上げをリクエスト
aws service-quotas request-service-quota-increase \
  --service-code sagemaker \
  --quota-code L-XXXXXX \
  --desired-value 100 \
  --region us-east-1
```

**申請プロセス**:
1. AWS Service Quotas コンソールから申請
2. ビジネスケースを説明
3. 承認まで数日～数週間
4. 大規模（512ノード以上）の場合は TAM（Technical Account Manager）との調整を推奨

### 実用的なスケーリングガイドライン

#### スモールスタート（～8ノード）

**想定構成**: P5 × 8ノード = 64 GPU

```json
{
  "InstanceGroupName": "gpu-workers",
  "InstanceType": "ml.p5.48xlarge",
  "InstanceCount": 8
}
```

**サービスクォータ申請**:
- 小規模または不要の場合あり
- vCPUs: 8 × 192 = 1,536 vCPUs

**用途**: 概念実証、小規模モデル訓練（Llama 7B など）

#### ミディアムスケール（～64ノード）

**想定構成**: P5 × 64ノード = 512 GPU

```json
{
  "InstanceGroupName": "gpu-workers",
  "InstanceType": "ml.p5.48xlarge",
  "InstanceCount": 64
}
```

**サービスクォータ申請**:
- **必須**
- vCPUs: 64 × 192 = 12,288 vCPUs
- 申請理由と使用計画を明記

**用途**: 中規模モデル訓練（Llama 70B など）

#### ラージスケール（～512ノード）

**想定構成**: P5 × 512ノード = 4,096 GPU

```json
{
  "InstanceGroupName": "gpu-workers",
  "InstanceType": "ml.p5.48xlarge",
  "InstanceCount": 512
}
```

**サービスクォータ申請**:
- **必須**、TAM との調整推奨
- vCPUs: 512 × 192 = 98,304 vCPUs
- 段階的な増加計画を提示

**用途**: 大規模モデル訓練（Llama 405B など）

#### エクストララージ（512ノード～）

**想定構成**: P5 × 1,000+ ノード = 8,000+ GPU

**サービスクォータ申請**:
- AWS との密接な連携が**必須**
- 専任の TAM や Solutions Architect のサポート
- 段階的なロールアウト計画

**用途**: 最先端の基盤モデル訓練、研究プロジェクト

**実例**:
- Amazon Nova モデル: 数万のアクセラレータ
- Project Rainier: 50万個以上の Trainium2 チップ

### スケーリング時の考慮事項

#### 1. ネットワーク帯域幅

ノード数が増えると、ノード間通信が重要になります。

| ノード数 | 総GPU数（P5） | All-Reduce通信量（概算） | 必要帯域幅 |
|---------|-------------|----------------------|-----------|
| 8 | 64 | 中程度 | 標準 EFA |
| 64 | 512 | 高い | EFA 最適化必要 |
| 512 | 4,096 | 非常に高い | UltraClusters 推奨 |

#### 2. ストレージ帯域幅

大規模クラスターでは、チェックポイント保存やデータロードが課題になります。

**推奨構成**:
- 小規模（～64 GPU）: FSx for Lustre 1.2 TB/s
- 中規模（～512 GPU）: FSx for Lustre 2.4 TB/s
- 大規模（512+ GPU）: 複数の FSx for Lustre + S3

#### 3. コスト見積もり

| 構成 | インスタンス数 | 月間コスト（On-Demand、概算） |
|------|--------------|--------------------------|
| **P5 × 8** | 8 | ~$250,000 |
| **P5 × 64** | 64 | ~$2,000,000 |
| **P5 × 512** | 512 | ~$16,000,000 |

**コスト削減オプション**:
- **Spot Instances**（HyperPod EKS）: 最大90%削減
- **Reserved Instances**: 長期利用で30-50%削減
- **Savings Plans**: 柔軟な割引オプション

### ベストプラクティス

#### 1. 段階的なスケーリング

```
フェーズ1: 8ノードで概念実証（1-2週間）
  ↓
フェーズ2: 64ノードで中規模訓練（1ヶ月）
  ↓
フェーズ3: 512ノードで本番規模訓練（3ヶ月）
```

#### 2. サービスクォータの事前準備

- プロジェクト開始**6-8週間前**に申請
- 段階的な増加計画を提示
- ビジネスケースを明確に説明

#### 3. モニタリングとコスト管理

```bash
# Cost Explorer で使用状況を確認
aws ce get-cost-and-usage \
  --time-period Start=2025-12-01,End=2025-12-31 \
  --granularity DAILY \
  --metrics UnblendedCost \
  --filter file://filter.json
```

## まとめ

本章では、大規模基盤モデル学習に必要なインフラストラクチャについて、以下の観点から整理しました。

### インフラストラクチャの階層構造

1. **物理インフラ層（EC2 UltraClusters）**: GPU、カスタムシリコン、高速ネットワーク、ストレージ
2. **コントロール層**: Slurm、Kubernetes によるオーケストレーション
3. **アプリケーション層**: PyTorch、TensorFlow、JAX などの ML フレームワーク

### 主要なプラットフォーム

- **AWS ParallelCluster**: HPC 向けセルフマネージドクラスター
- **SageMaker HyperPod (Slurm)**: Slurm ベースのフルマネージド ML 訓練
- **SageMaker HyperPod (EKS)**: Kubernetes ベースのフルマネージド ML 訓練
- **Amazon EKS**: 汎用マネージド Kubernetes サービス

### re:Invent 2025 の主要アップデート

**ハードウェア**:
- Trn3 UltraServer（Trainium2 比4.4倍性能）
- P6e-GB300（GB200 比1.5倍メモリ）
- AWS AI Factories（顧客データセンター展開）

**HyperPod 新機能**:
- Spot Instances（最大90%コスト削減）
- MIG サポート（1 GPU を7分割）
- Checkpointless Training（リカバリー80%削減）
- Elastic Training（自動スケーリング）

**推論強化**:
- EAGLE Speculative Decoding（2.5倍スループット）
- Bidirectional Streaming（リアルタイム双方向通信）
- Managed Tiered KV Cache（40%レイテンシ削減）

これらの技術革新により、大規模基盤モデルの訓練・推論がより効率的、低コスト、高性能になりました。次章以降では、これらのインフラストラクチャ上で実際に分散訓練を実装する方法について解説します。
