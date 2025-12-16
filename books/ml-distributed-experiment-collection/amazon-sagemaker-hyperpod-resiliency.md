---
title: "Amazon SageMaker HyperPod のレジリエンシー"
emoji: "🏗️"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["aws", "sagemaker", "hyperpod", "distributed", "infrastructure"]
free: true
---

::::details 前提
:::message
**対象読者**: 大規模基盤モデルの学習に携わる方、レジリエントなインフラストラクチャに興味がある方
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

**本章では Amazon SageMaker HyperPod のレジリエンシーについて整理します。**

---

AWS Principle WW Solutions Architect,GenAI, Keita Watanabe さんの [Scalable Infrastructure for Large-Scale AI Training with AWS Sagemaker Hyperpod](https://speakerdeck.com/keitaw/scalable-infrastructure-for-large-scale-ai-training-with-aws-sagemaker-hyperpod-at-singapore-ai-hour) 資料の流れを参照しながら初学者向けに情報を整理します。

# Amazon SageMaker HyperPod のレジリエンシー

:::message alert
***Point! EKS と Slurm ではレジリエンシーや Observability の機能が大幅に異なる。***
:::

前章では、大規模な GPU クラスターにおいて障害が避けられない現実を見てきました。例示したような高い障害率に対して、Meta は高度な自動化により [90% 以上の効果的な学習時間を維持](https://www.datacenterdynamics.com/en/news/meta-report-details-hundreds-of-gpu-and-hbm3-related-interruptions-to-llama-3-training-run/)できましたが、このようなシステムを独自に構築し運用することは、ML チームにとって大きな負担となります。

[Amazon SageMaker HyperPod](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod.html) は、レジリエントなクラスターをプロビジョニングし、大規模基盤モデル学習に閉じない機械学習ワークロード全般を実行するためのマネージドサービスです。基盤モデルの開発を加速するため、数千の GPU や AWS Trainium といったアクセラレータを用いた大規模な計算クラスターの構築と維持に関わる差別化されない作業を取り除きます。障害が発生すると HyperPod はクラスターインスタンスを自動的に監視し、障害のあるハードウェアを検出して即座に交換することができるため、研究者はワークロードの実行に集中できます。

HyperPod は、Slurm と Amazon EKS の 2 つのオーケストレーションオプションをサポートしています。Slurm サポートでは、既に解説した通りオープンソースのワークロードマネージャーである Slurm との統合により、ヘッドノード、ログインノード、ワーカーノードのセットアップを通じたシームレスなクラスター管理を実現します。Amazon EKS サポートでは、コンテナ化されたワークロードによる基盤モデルの学習を可能にし、動的な容量管理、クラスターインスタンスへの直接アクセス、そして高度なレジリエンシー機能を提供します。

# オーケストレーター別の機能比較

EKS と Slurm は、どちらも大規模機械学習に必要な主要機能を提供していますが、実装方法や運用スタイルに違いがあります。以下では、大分類で機能を整理し、両者の特徴を明確にします。以降の文章を読み進める上でどちらのオーケストレーターのことなのかを確認しながら読むことをお勧めします。多くの機能を紹介しますがまずはこのような機能があるということを認識するだけで良く、以降の章で実験によって挙動を確認する予定です。

| 大分類 | 機能 | EKS | Slurm | 備考 |
|--------|------|-----|-------|------|
| **障害からの自動復旧** | - | ✅ | ✅ | 両方サポート、実装が異なる |
| | Automatic Node Recovery | ✅ | ✅ | クラスター作成/更新時に設定 |
| | Checkpointless Training | ✅ | ❌ | メモリベースの高速復旧機能 |
| | Auto-Resume | ❌ | ✅ | チェックポイントベース、`--auto-resume=1` で有効化 |
| | マネージド階層化チェックポイント | ✅ | ❌ | CPU メモリ活用、高速チェックポイント |
| **ヘルスモニタリングと診断** | - | ✅ | ✅ | 基本機能は共通、実装が異なる |
| | Health Monitoring Agent | ✅ | ✅ | GPU/Trainium の継続的監視 |
| | Basic Health Checks | ✅ | ✅ | Orchestrator-agnostic |
| | Deep Health Checks（統合自動実行） | ✅ | ❌ | EKS 固有の統合機能 |
| | 個別診断ツール実行 | ✅ | ✅ | DCGM/NCCL/EFA 等を手動実行可能 |
| **Observability** | - | ✅ | ✅ | セットアップ方法が異なる |
| | Amazon Managed Prometheus | ✅ | ✅ | メトリクス収集基盤 |
| | Amazon Managed Grafana | ✅ | ✅ | 可視化ダッシュボード |
| | SageMaker Managed MLflow | ✅ | ✅ | 実験トラッキング |
| | セットアップ | ✅ ワンクリック | ⚠️ 手動 | EKS は自動統合、Slurm は手動設定 |
| | CloudWatch Container Insights | ✅ | ❌ | コンテナ固有メトリクス |
| | Task Governance Reports | ✅ | ❌ | リソース使用量とコスト配分 |
| | Slurm 固有メトリクス | ❌ | ✅ | ジョブキュー、パーティション情報など |
| **運用管理** | - | ✅ | ✅ | 操作方法が異なる |
| | 手動操作 | API/kubectl | Slurm コマンド | それぞれのエコシステム |
| | SSM 直接アクセス | ✅ | ✅ | ノードへの直接ログイン |
| | マルチ AZ サポート | ✅ | ✅ | インフラレベルのレジリエンシー |

**凡例**: ✅ = サポート、❌ = 非サポート、⚠️ = 手動またはカスタム実装で対応可能

# EKS クラスターでのレジリエンシー

Amazon EKS でオーケストレーションされた HyperPod クラスターでのレジリエンシー機能について説明します。

## Health Monitoring Agent による継続的な監視

[HyperPod は Health Monitoring Agent を通じて、クラスターインスタンスの健全性を継続的に監視](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency.html)します。このエージェントは、基本的な Health Check と Deep Health Check の両方を実行し、潜在的な問題を早期に検出します。前章で説明したように、障害の早期検出は失われる学習の進捗を最小化する上で極めて重要です。

基本的なヘルスチェックは、GPU の可用性、メモリエラー、温度異常などの即座に検出可能な問題を監視します。これらのチェックは継続的に実行され、システムの基本的な健全性を保証します。一方、Deep Health Check は、より包括的な診断を通じて、潜在的な問題や性能劣化を検出します。

**Health Monitoring Agent による監視**

[Health Monitoring Agent (HMA)](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-health-monitoring-agent.html) は、GPU と Trainium アクセラレータの健全性を継続的に監視します。NVIDIA GPU では、[DCGM policy violation notifications](https://docs.nvidia.com/datacenter/dcgm/3.0/user-guide/feature-overview.html#notifications)、nvidia-smi の出力エラー、Amazon EC2 プラットフォームログのエラー、GPU デバイスのカウント検証、を監視します。AWS Trainium では、[AWS Neuron monitor](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/tools/neuron-sys-tools/neuron-monitor-user-guide.html) の出力エラー、[Neuron node problem detector](https://aws.amazon.com/blogs/machine-learning/node-problem-detection-and-recovery-for-aws-neuron-nodes-within-amazon-eks-clusters/) の出力、Amazon EC2 プラットフォームログのエラー、Neuron デバイスカウント検証を監視します。

DCGM policy violation notifications は、GPU の温度、電力消費、PCIe エラーなどの特定の条件が閾値を超えた場合にポリシー違反として通知する機能です。コールバック関数を登録することで、違反発生時に自動的に通知を受け取り、問題を早期に検出して対応できます。これにより、GPU の健全性を維持し、学習ワークロードの信頼性を向上させます。

## Deep Health Checks による包括的な診断

[HyperPod は、クラスターの作成と更新時に Deep Health Checks を実行](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-deep-health-checks.html)します。これらのチェックは、基盤となるハードウェアとインフラストラクチャコンポーネントを徹底的にテストし、クラスターが学習に使用される前に信頼性と安定性を確保します。このアプローチは、クラスターライフサイクルの早期段階で潜在的な問題を特定し、軽減するのに役立ちます。

::::details Instance レベルの Deep Health Checks

Instance レベルのチェックは、個々のインスタンスの健全性を検証します。

### GPU アクセラレータのチェック

**GPU/NVLink カウント**: GPU と NVLink の数を検証し、期待の構成との一致を確認します。

**[DCGM diagnostics Level 4](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html)**: 前章で説明した DCGM の階層的な診断レベルのうち、最も包括的な Level 4 を実行します。これには追加のメモリテストが含まれ、NVIDIA GPU の健全性と機能性を評価します。Level 4 は通常 30 分から 120 分を要し、RMA（返品承認）前の最終検証や原因不明の障害の徹底調査に使用されます。

HyperPod が実行する DCGM Level 4 診断の出力例を以下に示します。

```
2024-08-20T22:25:02Z    info    DCGM diagnostic health summary: 
  dcgmCheckLevel: 0 
  dcgmVersion: 3.3.7 
  gpuDriverVersion: 535.183.01
  gpuDeviceIds: [2237] 
  replacementRequired: false 
  rebootRequired: false
```

この出力から、GPU ドライバのバージョン、デバイス ID、そして置換やリブートが必要かどうかを確認できます。

### Trainium アクセラレータのチェック

**[Neuron sysfs](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/tools/neuron-sys-tools/neuron-sysfs-user-guide.html)**: Trainium を搭載したインスタンスでは、Neuron ドライバから直接伝播される Neuron sysfs からカウンターを読み取ることで、Neuron デバイスの健全性を判断します。

**Neuron hardware check**: 学習ワークロードを実行し、結果を検証することでハードウェアをテストします。

**NCCOM local test**: 単一の Trainium ノード上での collective communication 操作の性能を評価します。

### ネットワークのチェック

**EFA (Elastic Fabric Adapter)**: GPU と Trainium の両方のインスタンスタイプで、接続された EFA デバイス上でレイテンシと帯域幅のベンチマークを実行します。以下は EFA ループバックテストの出力例です。

```
2024-08-20T22:26:28Z    info    EFA Loopback check passed for device: rdmap0s29
  MaxBw: 58.590000
  AvgBw: 32.420000
  MaxTypicalLat: 30.870000
  MinTypicalLat: 20.080000
  AvgLat: 21.630000
```

この出力から、EFA デバイスの帯域幅とレイテンシの特性を確認できます。
::::

::::details Cluster レベルの Deep Health Checks

Cluster レベルのチェックは、複数のノードにわたる通信と協調動作を検証します。

### NCCL Test (GPU 用)

[NCCL test](https://github.com/NVIDIA/nccl-tests) は、複数の NVIDIA GPU 上での collective communication 操作の性能を検証します。前章で説明したように、大規模な分散学習では All-Reduce などの Collective Communication が頻繁に実行されるため、この通信性能がボトルネックとなります。

以下は NCCL テストの出力例の一部です。

```
#       size         count      type   redop    root     time   algbw   busbw
#        (B)    (elements)                               (us)  (GB/s)  (GB/s)
           8             2     float     sum      -1    353.9    0.00    0.00
          16             4     float     sum      -1    352.8    0.00    0.00
   ...
  2147483648     536870912     float     sum      -1  1804971    1.19    1.19

# Out of bounds values : 0 OK
# Avg bus bandwidth    : 0.488398
```

この出力から、様々なメッセージサイズにおける通信帯域幅とレイテンシを確認できます。`Out of bounds values: 0 OK` は、データ破損が発生していないことを示し、`Avg bus bandwidth` は平均的なバス帯域幅を示します。

### NCCOM Cluster Test (Trainium 用)

Trainium ノード上での複数ノードにわたる collective communication 操作の性能を検証します。

これらの Cluster レベルのチェックにより、単一ノードでは問題がなくても、複数ノード間の通信で問題が発生するケースを検出できます。これは、前章で説明したクラスターテレメトリの重要性を実装したものです。
::::

::::details Deep Health Checks のログと分析

HyperPod は、Deep Health Checks の結果を複数の場所に保存し、問題の診断を容易にします。

### Cluster レベルのログ

Cluster レベルの Deep Health Check ログは、Amazon CloudWatch のログストリーム `/aws/sagemaker/Clusters/<cluster_name>/<cluster_id>` の下の `DeepHealthCheckResults/<log_stream_id>` に保存されます。

以下は、障害が検出された場合のログ例です。

```json
{
    "level": "error",
    "ts": "2024-06-18T21:15:22Z",
    "msg": "Encountered FaultyInstance. Replace the Instance. Region: us-west-2, InstanceType: p4d.24xlarge. ERROR:Bandwidth has less than threshold: Expected minimum threshold :80,NCCL Test output Bw: 30"
}
```

このログから、どのインスタンスが障害を起こし、その原因が何であるか（この場合は NCCL テストの帯域幅が閾値を下回った）を明確に把握できます。

### Instance レベルのログ

Instance レベルの Deep Health Check ログは、各ノードの `/var/log/aws/clusters/sagemaker-deep-health-check.log` に保存されます。SSH でノードにアクセスし、以下のコマンドでログファイルを開くことができます。

```bash
cat /var/log/aws/clusters/sagemaker-deep-health-check.log
```

これらのログには、ハードウェアストレステスト、DCGM ストレステスト、EFA 接続テストの詳細な結果が含まれます。前章で説明した統合テレメトリの概念に沿って、これらのログを相関分析することで、問題の根本原因を迅速に特定できます。
::::

## Automatic Node Recovery による自動復旧

[クラスターの作成または更新時に、クラスター管理者はノード（インスタンス）の復旧オプションとして `Automatic`（推奨、デフォルト）または `None` を選択できます](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-node-recovery.html)。`Automatic` に設定すると、HyperPod は障害のあるノードを自動的にリブートまたは交換します。

自動ノード復旧は、Health Monitoring Agent、基本ヘルスチェック、Deep Health Checks から問題が検出されると実行されます。`None` に設定した場合、Health Monitoring Agent は障害が検出されたときにインスタンスにラベルを付けますが、影響を受けたノードに対する修復や復旧アクションを自動的には開始しません。

前章で説明した Meta の事例では、[419 回の中断のうち 416 回が自動化システムにより処理](https://www.datacenterdynamics.com/en/news/meta-report-details-hundreds-of-gpu-and-hbm3-related-interruptions-to-llama-3-training-run/)されました。HyperPod の Automatic Node Recovery は、この自動化の概念を実装し、手動介入の必要性を最小限に抑えます。

::::details リブートと交換の判断基準

**リブートが選択されるケース**
- GPU/Neuron デバイスカウントの不一致
- 一時的なソフトウェア問題

**交換が選択されるケース**
- ハードウェア障害（NVLink エラー、Xid エラーなど）
- 永続的な問題
- Deep Health Checks での重大な障害

HMA が検出した健全性イベントは、[CloudWatch のログストリーム `/aws/sagemaker/Clusters/` の `SagemakerHealthMonitoringAgent`](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-health-monitoring-agent.html) に公開されます。以下は NVLink エラー検出の例です。

```json
{
  "level":"info",
  "ts":"2024-08-21T18:35:35Z",
  "msg":"NPD caught event",
  "details": {
    "severity":"warn",
    "reason":"XidHardwareFailure",
    "message":"NVRM: Xid (PCI:0000:b9:00): 71, NVLink: fatal error detected on link 6"
  },
  "HealthMonitoringAgentDetectionEvent":"HealthEvent"
}
```
::::

## Kubernetes Labels によるノード状態管理

[HyperPod は、ヘルスチェック、障害タイプ、計算障害に基づいてノードにラベルを付けます](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-node-labels.html)。これにより、ノードの健全性ステータスを監視し、Deep Health Checks を実行し、障害を特定し、それに応じてノードにラベルを付け、置換またはリブートのために異常なノードに taint を適用し、新しいノードをチェックします。

これらのラベルは、Kubernetes のスケジューリングと管理を通じて、障害のあるノードへのワークロードの配置を防ぎ、クラスター全体の信頼性を向上させます。

## 手動操作

EKS クラスターでは、[2つの方法で手動のノード復旧を実行](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-manual.html)できます。

::::details API による操作（推奨）

**注意**: AWS 公式の HyperPod ドキュメントでは `aws sagemaker-dev` を使用していますが、一般的な AWS CLI 利用では `aws sagemaker` コマンドを使用します。実際の環境に応じて適切なコマンドを選択してください。

**リブート**

```bash
aws sagemaker-dev batch-reboot-cluster-nodes \
  --cluster-name arn:aws:sagemaker:ap-northeast-1:123456789:cluster/test-cluster \
  --node-ids i-abc123 i-def456 
```

**交換**

```bash
aws sagemaker-dev batch-replace-cluster-nodes \
  --cluster-name arn:aws:sagemaker:ap-northeast-1:123456789:cluster/test-cluster \
  --node-ids i-abc123 i-def456 
```
::::

::::details kubectl による操作

**ノードの quarantine**

```bash
kubectl cordon <node-name>
```

**Pod の強制削除**（30分以上終了処理中の場合）

```bash
kubectl delete pods <pod-name> --grace-period=0 --force
```

**リブートのトリガー**

```bash
kubectl label nodes <node-name> \
  sagemaker.amazonaws.com/node-health-status=UnschedulablePendingReboot
```

**交換のトリガー**

```bash
kubectl label nodes <node-name> \
  sagemaker.amazonaws.com/node-health-status=UnschedulablePendingReplacement
```

**重要**: 手動でラベルを使用する場合でも、クラスター作成/更新時に Automatic Node Recovery を有効化する必要があります。
::::

## Observability

EKS クラスターでは、[Amazon CloudWatch Container Insights、Amazon Managed Service for Prometheus、Amazon Managed Grafana と統合](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-cluster-observability.html)してクラスターリソースとソフトウェアコンポーネントの包括的な Observability を実現します。

### One-Click Observability

EKS 固有の機能として、[SageMaker HyperPod observability add-on](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-cluster-observability-cluster.html) が提供されます。これは、基盤モデル開発タスクとクラスターリソースに関する洞察を提供する、すぐに使える包括的なダッシュボードです。この統合された Observability ソリューションは、主要なメトリクスを自動的に Amazon Managed Service for Prometheus に公開し、Amazon Managed Grafana ダッシュボードに表示します。

[Amazon CloudWatch Container Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/ContainerInsights.html) は、EKS クラスター固有の機能として、コンテナ化されたアプリケーションとマイクロサービスのメトリクスとログを収集、集約、要約します。Container Insights は、CPU、メモリ、ディスク、ネットワークなどのメトリクスを自動的に収集し、コンテナの再起動失敗などの診断情報も提供します。

### Task Governance と Usage Reports

[Task Governance](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-operate-console-ui-governance.html) と連携して、HyperPod Usage Reports は財務とリソースの説明責任に関する洞察を提供します。これらのレポートは、namespace/team 単位での計算利用（GPU/CPU/Neuron Core 時間）、割り当てられたリソースと借用されたリソースのコスト配分、監査と最適化のための履歴トレンド（最大 180 日）を追跡します。

# Slurm クラスターでのレジリエンシー

Slurm でオーケストレーションされた HyperPod クラスターでのレジリエンシー機能について説明します。

## Health Monitoring Agent

[Slurm クラスターでも、EKS と同様に Health Monitoring Agent が動作](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-cluster-health-check.html)します。HMA は、GPU と Trainium アクセラレータの健全性を継続的に監視し、障害を早期に検出します。監視項目は EKS と同じく、DCGM、nvidia-smi、Neuron monitor、EC2 プラットフォームログなどです。

::::details 診断テストの実行

Slurm クラスターでは、EKS の Deep Health Checks のような統合された自動診断機能は提供されていません。しかし、必要に応じて個別の診断ツールを手動で実行することで、同等の検証を行えます。

**実行可能な診断テスト**
- **DCGM diagnostics**: `dcgmi diag -r 4` で Level 4 診断を実行
- **NCCL test**: [NCCL-tests](https://github.com/NVIDIA/nccl-tests) を使用してクラスター通信性能を検証
- **EFA test**: `fi_pingpong` や `efa_test` で EFA の性能を測定
- **Neuron test**: Neuron SDK の診断ツールで Trainium デバイスを検証

これらのテストは、クラスターのセットアップ時や定期的なメンテナンス時に実行することで、ハードウェアの健全性を確認できます。
::::

## Automatic Node Recovery と Auto-Resume

[Slurm クラスターでは、Automatic Node Recovery と auto-resume 機能が連携](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)します。

### 動作フロー

1. HMA がハードウェア障害を検出
2. ノードがドレイン状態にマーク
3. すべてのジョブが終了後、ノードを自動交換
4. `--auto-resume=1` フラグ付きのジョブは最後のチェックポイントから自動再開
5. auto-resume なしのジョブは手動再送信が必要

**重要**: [Slurm で auto-resume 使用時は常にノードを交換](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)し、リブートは実行しません。

### Auto-Resume の使用方法

```bash
# srun の例
srun --auto-resume=1 train_script.sh

# salloc の例
salloc -N 2 --exclusive
srun --auto-resume=1 train_script.sh
```

### 制約事項と注意点

**GRES (Generic Resources) の制約**: [GRES が付加されたノードでは、Slurm は通常ノード割り当ての変更を許可しない](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)ため、HyperPod auto-resume は障害ジョブを自動的に再キューし、最初から再起動します。

**Auto-Resume のスコープ**: auto-resume は [`--auto-resume=1` を指定した srun コマンドの現在のジョブステップ](https://slurm.schedmd.com/job_launch.html#step_allocation)でのみ動作します。リソース割り当て内の他の srun コマンドには適用されません。

**環境の一貫性**: ノード交換後、[環境を一貫して設定する必要](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)があります。依存関係のインストールや仮想環境のアクティベーションをスクリプト化する必要があります。

**$SLURM_JOB_NODELIST の注意**: [auto-resume 後は値が古くなる可能性](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)があるため使用せず、`scontrol show jobid` で現在のノードリストを動的に取得します。

## 手動操作

[Slurm クラスターでは、Slurm コマンドまたは HyperPod API を使用して手動でノードを復旧](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-replace-faulty-instance.html)できます。

::::details Slurm コマンドによる操作

**ノードの状態確認**

```bash
sinfo
```

**ノードの手動ドレイン**

```bash
scontrol update NodeName=<node-name> State=DRAIN Reason="maintenance"
```

**ノードの復帰**

```bash
scontrol update NodeName=<node-name> State=RESUME
```
::::

::::details リブートと交換の判断

**リブートを検討する場合**
- ノードが応答しないが、ハードウェア障害の兆候がない
- ソフトウェアの問題が疑われる
- 一時的なネットワークの問題

**交換を検討する場合**
- ハードウェア障害が検出された
- 複数回のリブートでも問題が解決しない
- Deep Health Checks（EKS のみ）で重大な問題が報告された
::::

## Observability

[Slurm クラスターでは、Amazon Managed Service for Prometheus と Amazon Managed Grafana を手動で統合](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-cluster-observability-slurm.html)してクラスターリソースの Observability を実現します。

### 手動セットアップ

EKS の One-Click Observability とは異なり、Slurm クラスターでは以下の手動セットアップが必要です。

1. **メトリクスエクスポーターパッケージのインストール**: [クラスター上にメトリクスエクスポーターをインストール](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-cluster-observability-slurm-install-exporters.html)
2. **Prometheus の設定**: Amazon Managed Service for Prometheus workspace との接続を設定
3. **Grafana workspace の設定**: [Amazon Managed Grafana workspace を作成し、Prometheus をデータソースとして追加](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-cluster-observability-slurm-managed-grafana-ws.html)

### Slurm Metrics

[Slurm 固有のメトリクス](https://docs.aws.amazon.com/sagemaker/latest/dg/smcluster-slurm-metrics.html)が利用可能で、ジョブキューの状態、ノードの利用率、パーティション情報などを追跡できます。

## 共通機能

### SageMaker Managed MLflow

EKS と Slurm の両方で、SageMaker Managed MLflow を使用して実験のトラッキング、モデルの管理、デプロイメントワークフローを実行できます。

### SSM による直接アクセス

EKS と同様に、Slurm クラスターでも SSM Session Manager を使用してノードに直接アクセスできます。これにより、詳細なトラブルシューティングと検証が可能になります。

### マルチ AZ サポート

Slurm クラスターでも、複数の AZ にわたるクラスター構成をサポートし、インフラレベルのレジリエンシーを提供します。

# まとめ

Amazon SageMaker HyperPod は、Slurm と Amazon EKS の 2 つのオーケストレーションオプションを通じて、大規模な機械学習ワークロードのためのレジリエントなクラスター管理を提供します。

**EKS の特徴**は、One-Click Observability、Deep Health Checks、CloudWatch Container Insights などの高度な統合機能により、運用開始までの時間を短縮し、包括的な監視とトラブルシューティングを実現することです。Kubernetes Labels と Taints による柔軟なノード管理、Task Governance による詳細なリソース追跡も提供します。

**Slurm の特徴**は、auto-resume 機能により、チェックポイントからのジョブの自動再開を実現し、障害からの復旧を効率化することです。HPC 分野で広く使用されている Slurm の豊富な機能とエコシステムを活用できます。

両オーケストレーションとも、Health Monitoring Agent による継続的な監視、Automatic Node Recovery による自動復旧、SSM による直接アクセス、マルチ AZ サポートなどの重要なレジリエンシー機能を共有しています。オーケストレーションの選択は、チームの専門知識、ワークロードの特性、運用要件に基づいて行うべきです。
