---
title: "Amazon SageMaker HyperPod -- Managed Tiered Checkpointing"
emoji: "💾"
type: "tech"
topics: ["AWS", "SageMaker", "HyperPod", "分散学習", "Checkpoint"]
published: false
---

## はじめに

本記事は SageMaker HyperPod 機能解説シリーズの一部です。以下の記事も公開済みですので、あわせて参照してください。

https://zenn.dev/tosshi/articles/45a746434b2090

https://zenn.dev/tosshi/articles/be0db364a7f8e2

大規模な分散学習では、チェックポイント保存が学習スループットの低下要因になります。大規模言語モデル（70B パラメータ以上）のチェックポイントは数百 GB に及ぶことがあり、チェックポイント書き込み中は GPU が待機状態となります。

Amazon SageMaker HyperPod の **Managed Tiered Checkpointing** は、保存先を CPU メモリと Amazon S3 の 2 階層に分けることで、保存の高速化とコスト削減を両立する機能です。本記事では、仕組み、実装方法、パフォーマンス特性、および FSx for Lustre + DRA との使い分けを解説します。

:::message alert
本記事は 2026 年 2 月時点の公式ドキュメント、オープンソースコードなどに基づく調査記事です。内容に誤りがある可能性もあるため、必ず最新の公式ドキュメントを正として確認してください。誤りを発見された場合はコメントでお知らせください。
:::

## 概要と FSx + DRA との比較

:::message
本機能は [EKS 環境でのセットアップ手順が公式ドキュメントに記載](https://docs.aws.amazon.com/sagemaker/latest/dg/managed-tier-checkpointing-setup.html)されています。Slurm 環境での利用可否については、公式ドキュメントに明示的な記載がないため、最新の情報を AWS に確認してください。

**FSx for Lustre との併用について**: Managed Tiered Checkpointing と FSx for Lustre + DRA は技術的に併用可能ですが、どちらも Amazon S3 にチェックポイントを永続化するため、チェックポイント保存の役割が重複します。FSx が構築済みの場合は、FSx をデータセット保存に、Managed Tiered Checkpointing をチェックポイント保存に使い分けることを推奨します。
:::

### FSx for Lustre + DRA（Data Repository Association）

HyperPod では **[Amazon FSx for Lustre](https://aws.amazon.com/fsx/lustre/) + [DRA](https://docs.aws.amazon.com/fsx/latest/LustreGuide/create-dra-linked-data-repo.html)** によるチェックポイント保存も広く利用されています。

```mermaid
graph LR
    TN["学習ノード"] -->|"torch.save<br/>学習の中断"| FSx["FSx for Lustre<br/>共有ストレージ"]
    FSx -->|"DRA<br/>変更検知"| DRA["Data Repository<br/>Association"]
    DRA -->|"バックグラウンド<br/>エクスポート"| S3["Amazon S3<br/>永続ストレージ"]

    style TN fill: #1a237e,color: #ffffff
    style FSx fill: #0d47a1,color: #ffffff
    style DRA fill: #1565c0,color: #ffffff
    style S3 fill: #1976d2,color: #ffffff
```

FSx + DRA の処理フローは、学習スクリプトが `torch.save()` で FSx に書き込み（書き込み完了まで学習を中断）、DRA が変更を検知して S3 へのエクスポートをスケジュールし、バックグラウンドで S3 に永続化するというものです。この方式は、POSIX 互換の共有ファイルシステムが必要な場合や、複数ノード間でのデータセット共有が必須な環境に適しています。

一方、Managed Tiered Checkpointing は、CPU メモリを活用した高速化とコスト最適化を重視した設計です。以下に両方式の特性を比較します。

| 比較項目 | FSx + DRA | Managed Tiered Checkpointing |
|---------|------------------|----------------------------|
| 学習の中断時間 | FSx 書き込み完了まで中断[^2] | メモリコピー完了まで中断[^4] |
| ノード障害時の復旧 | FSx または S3 から復元 | メモリレプリカから高速復旧 |
| POSIX 互換性 | あり（標準ファイルシステム） | なし（専用 API 使用） |
| データセット共有 | 得意（共有ストレージ） | S3 直接読込で対応 |
| S3 保存形式 | 単一の `.pt` ファイル | PyTorch DCP の sharded checkpoint（`.distcp`） |
| 推奨ケース | POSIX 互換性やデータセット共有が必須 | チェックポイント高速化とコスト最適化 |

以降では、Managed Tiered Checkpointing の仕組みと実装方法を詳しく解説します。

## 階層化戦略

Managed Tiered Checkpointing は **2 つの階層**でチェックポイントを管理します。高速アクセス用の主要層としてクラスターノードの **CPU メモリ（RAM）** を使用し、永続バックアップ用の副次層として **Amazon S3** を使用します。

```mermaid
graph TB
    subgraph "Tier 1: CPU メモリ"
        M1["クラスターノードの<br/>CPU メモリ (RAM)<br/>速度: GB/s"]
        M2["隣接ノードへの<br/>自動レプリケーション"]
    end

    subgraph "Tier 2: Amazon S3"
        S1["S3 バケット<br/>永続保存先<br/>耐久性: 99.999999999%"]
    end

    M1 -->|"自動レプリケート"| M2
    M1 -->|"定期的に<br/>非同期アップロード"| S1

    style M1 fill: #1b5e20,color: #ffffff
    style M2 fill: #2e7d32,color: #ffffff
    style S1 fill: #0d47a1,color: #ffffff
```

| 階層 | 保存先 | 速度 | 耐久性 | 用途 |
|------|--------|------|--------|------|
| Tier 1 | CPU メモリ（RAM） | 高速（GB/s） | ノード間レプリケーションで保護 | 高頻度保存・高速復旧 |
| Tier 2 | Amazon S3 | 低速（数百 MB/s） | 高耐久（99.999999999%） | 低頻度保存（永続バックアップ） |

### レプリケーション戦略

Tier 1（CPU メモリ）に保存されたチェックポイントは、**隣接する計算ノード間で自動的にレプリケート**されます。これにより、単一または複数のノード障害時にもデータを保護し、高速に復旧できます。

メモリ管理は、EKS 環境では Kubernetes DaemonSet としてデプロイされるメモリ管理デーモンが担当し、チェックポイント用の分散メモリ（disaggregated memory）を管理します。

:::message
`InstanceMemoryAllocationPercentage` パラメータで、チェックポイント用に割り当てる CPU メモリの割合を設定できます（20-100% の範囲で指定）。学習プロセスが使用するメモリとのバランスを考慮して設定してください。

以下は設定例です（`--tiered-storage-config` 内に含めます）。

```json
{
  "Mode": "Enable",
  "InstanceMemoryAllocationPercentage": 50
}
```
:::

## 非同期パイプライン

最大の特徴は、**学習を中断せずにチェックポイントを保存**できる点です。[PyTorch DCP（Distributed Checkpoint）](https://pytorch.org/docs/stable/distributed.checkpoint.html)の `async_save()` により非同期保存を実現します。

```mermaid
graph TB
    subgraph "従来方式 -- 同期保存"
        direction LR
        A1["学習"] --> A2["チェックポイント<br/>保存<br/>学習中断"] --> A3["学習"] --> A4["チェックポイント<br/>保存<br/>学習中断"] --> A5["学習"]
    end

    subgraph "Tiered Checkpointing -- 非同期保存"
        direction LR
        B1["学習 -- 中断なし"]
        B2["Tier1: RAM コピー<br/>高速"] -.->|"バックグラウンド"| B3["Tier2: S3<br/>アップロード"]
    end

    style A2 fill: #b71c1c,color: #ffffff
    style A4 fill: #b71c1c,color: #ffffff
    style B1 fill: #1b5e20,color: #ffffff
    style B2 fill: #f57f17,color: #000000
    style B3 fill: #0d47a1,color: #ffffff
```

従来方式ではチェックポイント保存中に学習が中断されます（図の赤色ノード）。Tiered Checkpointing では Tier 1（RAM）への高速なステージング後すぐに学習を再開でき、S3 へのアップロードはバックグラウンドで非同期に実行されます。

## 実装と API

### クラスターの構成

Managed Tiered Checkpointing を利用するには、クラスター作成時に `--tiered-storage-config` で有効化します（[セットアップドキュメント](https://docs.aws.amazon.com/sagemaker/latest/dg/managed-tier-checkpointing-setup.html)参照）。

**EKS 環境のクラスター構成例**:

```bash
aws sagemaker create-cluster \
    --cluster-name my-training-cluster \
    --orchestrator "Eks={ClusterArn=arn: aws: eks: us-west-2:123456789012: cluster/my-eks}" \
    --instance-groups '[{
        "InstanceGroupName": "training-group",
        "InstanceType": "ml.p5.48xlarge",
        "InstanceCount": 4,
        "LifeCycleConfig": {
            "SourceS3Uri": "s3://my-bucket/lifecycle-scripts",
            "OnCreate": "on_create.sh"
        },
        "ExecutionRole": "arn: aws: iam::123456789012: role/MyRole",
        "InstanceStorageConfigs": [
            { "EbsVolumeConfig": {"VolumeSizeInGB": 500} }
        ]
    }]' \
    --vpc-config '{
        "SecurityGroupIds": ["sg-xxxxxxxxxxxxxxxxx"],
        "Subnets": ["subnet-xxxxxxxxxxxxxxxxx"]
    }' \
    --tiered-storage-config '{"Mode": "Enable"}'
```

**無効化する場合**:

```bash
aws sagemaker update-cluster \
    --cluster-name my-training-cluster \
    --tiered-storage-config '{"Mode": "Disable"}'
```

### Python ライブラリ

専用ライブラリ [`amzn-sagemaker-checkpointing`](https://pypi.org/project/amzn-sagemaker-checkpointing/)（v1.1.2、Apache License 2.0、Python 3.10 以上）を使用します。`sagemaker` SDK とは別パッケージです。

```bash
pip install amzn-sagemaker-checkpointing s3torchconnector tenacity torch boto3
```

### チェックポイント設定

```python
import os
import time
import torch.distributed as dist
from amzn_sagemaker_checkpointing.config.sagemaker_checkpoint_config import (
    SageMakerCheckpointConfig,
)
from amzn_sagemaker_checkpointing.checkpointing.filesystem.filesystem import (
    SageMakerTieredStorageWriter,
    SageMakerTieredStorageReader,
)

# 分散学習の初期化
dist.init_process_group(backend="nccl")

# チェックポイント設定
checkpoint_config = SageMakerCheckpointConfig(
    namespace=os.environ.get("TRAINING_JOB_NAME", f"job-{int(time.time())}"),
    # namespace に使用可能な文字: 英数字、ハイフン、アンダースコアのみ
    world_size=dist.get_world_size(),
    s3_tier_base_path="s3://my-bucket/checkpoints",
)
```

`SageMakerCheckpointConfig` の主要パラメータは以下の通りです。

| パラメータ | 型 | 説明 |
|---|---|---|
| `namespace` | str | 学習ジョブの一意な識別子（英数字・ハイフン・アンダースコアのみ） |
| `world_size` | int | 分散プロセス数（`dist.get_world_size()` から取得） |
| `s3_tier_base_path` | str | S3 保存先パス |
| `save_to_s3` | bool | S3 への保存を有効化（保存ごとに動的に切替可能） |

### 学習スクリプトへの統合

[PyTorch DCP（Distributed Checkpoint）](https://pytorch.org/docs/stable/distributed.checkpoint.html)の `async_save()` / `load()` と組み合わせて使用します。以下は [AWS 公式ドキュメント](https://docs.aws.amazon.com/sagemaker/latest/dg/managed-tier-checkpointing-setup.html)に基づくコード例です。

```python
from torch.distributed.checkpoint import async_save, load

future = None
in_memory_ckpt_freq = 10   # 10 ステップごとにメモリ保存
s3_ckpt_freq = 50           # 50 ステップごとに S3 永続化

for step, batch in enumerate(dataloader):
    # 通常の学習ステップ
    loss = model(batch)
    loss.backward()
    optimizer.step()

    # Tiered Checkpointing: 非同期で保存
    if step % in_memory_ckpt_freq == 0 or step % s3_ckpt_freq == 0:
        state_dict = {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "step": step,
        }

        # S3 への保存はより低い頻度で実行
        checkpoint_config.save_to_s3 = (step % s3_ckpt_freq == 0)

        storage_writer = SageMakerTieredStorageWriter(
            checkpoint_config=checkpoint_config,
            step=step,
        )

        # 前回の非同期保存の完了を確認
        if future is not None:
            exc = future.exception()
            if exc is not None:
                print(f"Checkpoint save failed: {str(exc)}")

        future = async_save(state_dict=state_dict, storage_writer=storage_writer)
```

:::message
**FSDP との統合**: [FSDP（Fully Sharded Data Parallel）](https://pytorch.org/docs/stable/fsdp.html)を使用する場合、`model.state_dict()` は `SHARDED_STATE_DICT` 形式で返されます。PyTorch DCP はこの形式を直接扱えるため、追加の変換は不要です。FSDP の `StateDictType.SHARDED_STATE_DICT` を設定した上で上記のパターンをそのまま適用できます。
:::

### チェックポイントの読み込み

`SageMakerTieredStorageReader` は、メモリ層からの読み込みに失敗すると自動的に S3 層にフォールバックします。

```python
state_dict = {
    "model": model.state_dict(),
    "optimizer": optimizer.state_dict(),
    "step": 0,  # 初期値。load() により実際の保存値で上書きされる
}

# 最新のチェックポイントを自動検出して読み込み（step 省略時）
storage_reader = SageMakerTieredStorageReader(
    checkpoint_config=checkpoint_config,
)

try:
    load(state_dict, storage_reader=storage_reader)
    # load() は state_dict を in-place で更新する
    # 読み込み後、モデルとオプティマイザに反映
    model.load_state_dict(state_dict["model"])
    optimizer.load_state_dict(state_dict["optimizer"])
    start_step = state_dict["step"]
except BaseException as e:
    print(f"Checkpoint load failed: {str(e)}")

# 特定のステップを指定して読み込み
storage_reader = SageMakerTieredStorageReader(
    checkpoint_config=checkpoint_config,
    step=500,
)

try:
    load(state_dict, storage_reader=storage_reader)
    model.load_state_dict(state_dict["model"])
    optimizer.load_state_dict(state_dict["optimizer"])
except BaseException as e:
    print(f"Checkpoint load failed at step 500: {str(e)}")
```

## PyTorch DCP async_save の内部アーキテクチャ

Managed Tiered Checkpointing の高速化を実現する中核技術は、PyTorch Distributed Checkpoint（DCP）の **`async_save`** です。このセクションでは、OSS レイヤーでの実装アーキテクチャを解説します。

### 3 段階の最適化戦略

PyTorch DCP は、チェックポイント保存の並列化を段階的に実現します（[PyTorch 非同期チェックポイントチュートリアル](https://docs.pytorch.org/tutorials/recipes/distributed_async_checkpoint_recipe.html)、[async_save 実装](https://github.com/pytorch/pytorch/blob/main/torch/distributed/checkpoint/state_dict_saver.py)参照）。

```mermaid
graph TB
    subgraph Level1["Level 1: 基本的な async_save"]
        L1A["GPU 計算<br/>学習ステップ"] --> L1B["GPU → CPU コピー<br/>(メインスレッド、ブロッキング)"]
        L1B --> L1C["ディスク書き込み<br/>(非同期スレッド)"]
    end

    subgraph Level2["Level 2: Pinned Memory 最適化"]
        L2A["GPU 計算<br/>学習ステップ"] --> L2B["GPU → CPU コピー<br/>(Pinned Memory、高速化)"]
        L2B --> L2C["ディスク書き込み<br/>(非同期スレッド)"]
        L2C -.->|"バッファ再利用"| L2B
    end

    subgraph Level3["Level 3: DefaultStager (PyTorch 2.9+)"]
        L3A["GPU 計算<br/>学習ステップ"]
        L3B["GPU → CPU コピー<br/>(バックグラウンドスレッド)"]
        L3C["ディスク書き込み<br/>(非同期スレッド)"]

        L3A -.->|"並列実行"| L3B
        L3B --> L3C
    end

    style Level1 fill: #fff3e0,stroke: #e65100
    style Level2 fill: #e8f5e9,stroke: #2e7d32
    style Level3 fill: #e3f2fd,stroke: #1565c0
    style L3A fill: #bbdefb,stroke: #1976d2
    style L3B fill: #c8e6c9,stroke: #388e3c
    style L3C fill: #fff9c4,stroke: #f57f17
```

**Level 1** では、GPU→CPU コピー（ステージング）がメインスレッドで実行されるため、学習が一時停止します。ディスク書き込みのみが非同期化されます。

**Level 2** では、Pinned Memory（ページング不可能な CPU メモリ）を使用して GPU→CPU 転送を高速化します。バッファを学習全体で再利用することで、メモリアロケーションのオーバーヘッドも削減されます。

**Level 3**（PyTorch 2.9+ の **DefaultStager**）では、state dict の構築と GPU→CPU コピーをバックグラウンドスレッドに完全にオフロードします。これにより、学習計算とチェックポイント処理が真の並列実行となり、チェックポイント保存が学習スループットに与える影響を最小化します。

### Managed Tiered Checkpointing との統合

Managed Tiered Checkpointing は、この Level 3 のアーキテクチャを活用しています。

```mermaid
graph LR
    subgraph Training["学習プロセス（メインスレッド）"]
        GPU["GPU 計算<br/>Forward/Backward"]
    end

    subgraph Staging["ステージング（バックグラウンドスレッド）"]
        DICT["State Dict 構築"]
        COPY["GPU → CPU<br/>メモリ転送"]
    end

    subgraph Upload["アップロード（非同期スレッド）"]
        TIER1["Tier 1<br/>CPU メモリ保存"]
        TIER2["Tier 2<br/>S3 アップロード"]
    end

    GPU -.->|"並列実行"| DICT
    DICT --> COPY
    COPY --> TIER1
    TIER1 --> TIER2

    style Training fill: #e3f2fd,stroke: #1565c0
    style Staging fill: #e8f5e9,stroke: #2e7d32
    style Upload fill: #fff3e0,stroke: #e65100
    style GPU fill: #bbdefb,stroke: #1976d2
```

この設計により、**GPU 計算（学習）、ステージング（GPU→CPU 転送）、アップロード（S3 保存）が 3 段階のパイプラインとして並列実行**されます。学習の次のステップは、前ステップのチェックポイント保存完了を待たずに開始できるため、学習スループットの低下が大幅に抑制されます。

:::message
**メモリトレードオフ**: Level 3 の DefaultStager は、GPU メモリと CPU メモリの両方にモデルの state dict を一時的に保持するため、メモリ使用量が増加します。大規模モデルの場合、CPU メモリ容量の計画が重要です。Managed Tiered Checkpointing では、`InstanceMemoryAllocationPercentage`（20-100%）でこのメモリ割り当てを制御できます。
:::

## パフォーマンス特性

| メトリクス | 同期 S3 チェックポイント | Managed Tiered Checkpointing |
|-----------|----------------------|----------------------------|
| チェックポイント保存時間 | モデルサイズ・ストレージ性能に依存 | 高速なメモリ操作[^4] |
| 学習の中断時間 | 保存時間と同等 | ほぼゼロ（非同期） |
| 復旧元の選択 | S3 のみ | メモリ -> S3 の順にフォールバック |
| ストレージコスト | S3 のみ | メモリ + S3（段階的） |
| 学習スループット低下 | ストレージ性能に依存 | 大幅に削減[^3] |

:::message
**Checkpointless Training との関係**: Managed Tiered Checkpointing と [Checkpointless Training](https://zenn.dev/yunokiisshin/articles/45a746434b2090) は補完的な関係です。Checkpointless Training は GPU メモリ内の冗長レプリカによる高速 in-memory 復旧を担い、Tiered Checkpointing はカタストロフィックな障害（複数ノード同時障害やクラスター再構築）に対する永続バックアップを担います。併用することで、軽微なノード障害から大規模クラスター障害まで幅広い障害シナリオに対応できます。
:::

## 使い分けガイドライン

### 判断基準

以下の表を参考に、環境に適した方式を選択してください。

| 判断基準 | Managed Tiered Checkpointing | FSx + DRA |
|---------|-----|-----|
| FSx 未構築の新規プロジェクト | 適合 | 不要 |
| コスト最適化重視 | 適合（FSx 月額不要） | FSx コスト発生 |
| 学習の中断時間の最小化 | 高速なメモリステージングのみ[^4] | FSx 書き込み完了まで中断[^2] |
| 数千ノード規模 | 適合 | FSx クライアント数制限あり |
| PyTorch DCP 標準化 | 適合 | 別方式 |
| 既存 FSx インフラの活用 | - | 適合 |
| 大規模データセット共有（数十 TB） | S3 直接読込で対応可 | 適合 |
| POSIX 互換性必須 | 非対応 | 適合 |
| リージョン/インスタンス制約 | 制約あり（[詳細](https://docs.aws.amazon.com/sagemaker/latest/dg/managed-tier-checkpointing-setup.html)参照） | 広く利用可能 |

### ハイブリッド構成（FSx + Managed Tiered）

データセット保存用に FSx を継続使用し、チェックポイント保存のみ Managed Tiered Checkpointing に移行する構成も有効です。ただし、データセットも S3 から直接読み込む（[`s3torchconnector`](https://github.com/amazon-science/s3torchconnector) など）ことでさらにコスト削減できる場合が多いため、[FSx for Lustre](https://aws.amazon.com/fsx/lustre/) の継続利用が本当に必要かを検討してください。

## まとめ

Managed Tiered Checkpointing は、大規模分散学習におけるチェックポイント保存の課題に対する実用的な解決策です。

CPU メモリ（Tier 1）と Amazon S3（Tier 2）の 2 階層でチェックポイントを管理する階層化アーキテクチャにより、速度と耐久性を両立します。PyTorch DCP の `async_save()` による非同期保存で学習の中断時間を大幅に短縮し[^3]、FSx for Lustre が不要となることで月額ストレージコストも削減可能です。ノード間のメモリレプリケーションにより、ノード障害時に高速に復旧でき、各ノードのローカルメモリを使用するため数千ノード規模にも対応できます。

新規プロジェクトでは Managed Tiered Checkpointing の採用を推奨します。既存の FSx 環境がある場合は、FSx をデータセット共有に残しつつ、チェックポイント保存のみ Tiered Checkpointing に移行するハイブリッド構成も検討してください。

## 参考資料

- [Managed Tiered Checkpointing ドキュメント](https://docs.aws.amazon.com/sagemaker/latest/dg/managed-tier-checkpointing.html)
- [Managed Tiered Checkpointing セットアップガイド](https://docs.aws.amazon.com/sagemaker/latest/dg/managed-tier-checkpointing-setup.html)
- [amzn-sagemaker-checkpointing (PyPI)](https://pypi.org/project/amzn-sagemaker-checkpointing/) -- v1.1.2
- [aws-samples/awsome-distributed-training (GitHub)](https://github.com/aws-samples/awsome-distributed-training)
- [AI on SageMaker HyperPod](https://awslabs.github.io/ai-on-sagemaker-hyperpod/) -- HyperPod チュートリアル
- [PyTorch Distributed Checkpoint](https://pytorch.org/docs/stable/distributed.checkpoint.html)
- [PyTorch Distributed Checkpoint Tutorial](https://pytorch.org/tutorials/recipes/distributed_checkpoint_recipe.html)
- [PyTorch Asynchronous Checkpoint Tutorial](https://pytorch.org/tutorials/recipes/distributed_async_checkpoint_recipe.html) -- async_save の詳細実装解説
- [PyTorch DCP state_dict_saver.py](https://github.com/pytorch/pytorch/blob/main/torch/distributed/checkpoint/state_dict_saver.py) -- async_save のソースコード
- [PyTorch FSDP](https://pytorch.org/docs/stable/fsdp.html)
- [Amazon FSx for Lustre](https://aws.amazon.com/fsx/lustre/)
- [FSx for Lustre Data Repository Association](https://docs.aws.amazon.com/fsx/latest/LustreGuide/create-dra-linked-data-repo.html)
- [s3torchconnector](https://github.com/amazon-science/s3torchconnector) -- S3 からの直接データ読み込みライブラリ

[^1]: コスト数値は FSx for Lustre SSD ストレージのオンデマンド料金（約 $140/TB-month）に、スループット容量やプロビジョニング IOPS の追加料金を含めた概算です。S3 Standard の料金は一般的な料金に基づきます。実際の料金はリージョン、ストレージクラス、データ転送量等により異なります。最新の料金は [AWS 公式料金ページ](https://aws.amazon.com/pricing/)を参照してください。
[^2]: FSx for Lustre への書き込み時間は、クラスター構成、モデルサイズ、チェックポイント頻度、FSx のスループット設定（125-1000 MBps/TiB）により異なります。詳細は [Amazon FSx for Lustre パフォーマンス](https://docs.aws.amazon.com/fsx/latest/LustreGuide/performance.html)を参照してください。
[^3]: Managed Tiered Checkpointing の[公式ドキュメント](https://docs.aws.amazon.com/sagemaker/latest/dg/managed-tier-checkpointing.html)では "Improved training throughput" と定性的に記載されていますが、具体的な削減率は公開されていません。
[^4]: Tier 1 メモリコピーの所要時間はモデルサイズとノード間の帯域幅に依存します。公式ドキュメントでは具体的な所要時間は公開されていませんが、ディスク I/O よりも高速なメモリ操作であることから、学習の中断時間を最小化できます。

---

## 関連記事（HyperPod の他の耐障害性機能）

本記事で解説した Managed Tiered Checkpointing は、HyperPod の耐障害性機能の 1 つです。関連する他の機能も別記事で解説しています。

- **[Checkpointless Training 徹底解説](https://zenn.dev/yunokiisshin/articles/45a746434b2090)** - チェックポイント不要の高速障害復旧
- **[Elastic Training 徹底解説](https://zenn.dev/yunokiisshin/articles/be0db364a7f8e2)** - クラスター容量に応じた動的なノード数の増減
- **[Health Monitoring Agent 徹底解説](https://zenn.dev/yunokiisshin/articles/0742d879958d3a)** - リソースの常時監視と自動障害復旧
