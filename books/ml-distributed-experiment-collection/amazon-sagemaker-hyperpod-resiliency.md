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

**本章では Amazon SageMaker HyperPod の価値について整理します。**

---

AWS Principle WW Solutions Architect,GenAI, Keita Watanabe さんの [Scalable Infrastructure for Large-Scale AI Training with AWS Sagemaker Hyperpod](https://speakerdeck.com/keitaw/scalable-infrastructure-for-large-scale-ai-training-with-aws-sagemaker-hyperpod-at-singapore-ai-hour) 資料の流れを参照しながら初学者向けに情報を整理します。

# Amazon SageMaker HyperPod のレジリエンシー

:::message
***Point! HyperPod は Slurm と EKS でレジリエンシーや対応している機能が大幅に異なる。***
:::

前章では、大規模な GPU クラスターにおいて障害が避けられない現実を見てきました。例示したような高い障害率に対して、Meta は高度な自動化により [90% 以上の効果的な学習時間を維持](https://www.datacenterdynamics.com/en/news/meta-report-details-hundreds-of-gpu-and-hbm3-related-interruptions-to-llama-3-training-run/)できましたが、このようなシステムを独自に構築し運用することは、ML チームにとって大きな負担となります。

[Amazon SageMaker HyperPod](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod.html) は、レジリエントなクラスターをプロビジョニングし、大規模基盤モデル学習に閉じない機械学習ワークロード全般を実行するためのマネージドサービスです。基盤モデルの開発を加速するため、数千の GPU や AWS Trainium といったアクセラレータを用いた大規模な計算クラスターの構築と維持に関わる差別化されない作業を取り除きます。障害が発生すると HyperPod はクラスターインスタンスを自動的に監視し、障害のあるハードウェアを検出して即座に交換することができるため、研究者はワークロードの実行に集中できます。

## Amazon SageMaker HyperPod の概要

HyperPod は、Slurm と Amazon EKS の 2 つのオーケストレーションオプションをサポートしています。Slurm サポートでは、既に解説した通りオープンソースのワークロードマネージャーである Slurm との統合により、ヘッドノード、ログインノード、ワーカーノードのセットアップを通じたシームレスなクラスター管理を実現します。Amazon EKS サポートでは、コンテナ化されたワークロードによる基盤モデルの学習を可能にし、動的な容量管理、クラスターインスタンスへの直接アクセス、そして高度なレジリエンシー機能を提供します。

HyperPod のレジリエンシーは、前章で説明した DCGM による階層的な障害検出、統合テレメトリによる根本原因の特定、そして自動化の重要性という概念を実装しています。これらの機能により高い稼働率をより少ない運用負担で実現できます。

以下では、EKS と Slurm それぞれのオーケストレーションにおけるレジリエンシー機能を詳しく説明します。

# EKS クラスターでのレジリエンシー

Amazon EKS でオーケストレーションされた HyperPod クラスターでのレジリエンシー機能について説明します。

## Health Monitoring Agent による継続的な監視

[HyperPod は Health Monitoring Agent を通じて、クラスターインスタンスの健全性を継続的に監視](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency.html)します。このエージェントは、基本的な Health Check と Deep Health Check の両方を実行し、潜在的な問題を早期に検出します。前章で説明したように、障害の早期検出は失われる学習の進捗を最小化する上で極めて重要です。

基本的なヘルスチェックは、GPU の可用性、メモリエラー、温度異常などの即座に検出可能な問題を監視します。これらのチェックは継続的に実行され、システムの基本的な健全性を保証します。一方、Deep Health Check は、より包括的な診断を通じて、潜在的な問題や性能劣化を検出します。

## Deep Health Checks による包括的な診断

[HyperPod は、クラスターの作成と更新時に Deep Health Checks を実行](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-deep-health-checks.html)します。これらのチェックは、基盤となるハードウェアとインフラストラクチャコンポーネントを徹底的にテストし、クラスターが学習に使用される前に信頼性と安定性を確保します。このアプローチは、クラスターライフサイクルの早期段階で潜在的な問題を特定し、軽減するのに役立ちます。

::::details Instance レベルの Deep Health Checks

Instance レベルのチェックは、個々のインスタンスの健全性を検証します。

## GPU アクセラレータのチェック

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

## Trainium アクセラレータのチェック

**[Neuron sysfs](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/tools/neuron-sys-tools/neuron-sysfs-user-guide.html)**: Trainium を搭載したインスタンスでは、Neuron ドライバから直接伝播される Neuron sysfs からカウンターを読み取ることで、Neuron デバイスの健全性を判断します。
**Neuron hardware check**: 学習ワークロードを実行し、結果を検証することでハードウェアをテストします。
**NCCOM local test**: 単一の Trainium ノード上での collective communication 操作の性能を評価します。

## ネットワークのチェック

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

## NCCL Test (GPU 用)

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

::::details 自動ノード復旧の詳細メカニズム

### 障害検出の仕組み

[Health Monitoring Agent (HMA)](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-health-monitoring-agent.html) は、すべての HyperPod クラスターで継続的に実行され、以下を監視します。

**NVIDIA GPU の監視項目**:
- [DCGM policy violation notifications](https://docs.nvidia.com/datacenter/dcgm/3.0/user-guide/feature-overview.html#notifications)
- nvidia-smi の出力エラー
- Amazon EC2 プラットフォームログのエラー
- GPU カウント検証（期待数と実際の数の不一致を検出）

**AWS Trainium の監視項目**:
- [AWS Neuron monitor](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/tools/neuron-sys-tools/neuron-monitor-user-guide.html) の出力エラー
- [Neuron node problem detector](https://aws.amazon.com/blogs/machine-learning/node-problem-detection-and-recovery-for-aws-neuron-nodes-within-amazon-eks-clusters/) の出力
- Amazon EC2 プラットフォームログのエラー
- Neuron デバイスカウント検証

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

### リブートと交換の判断基準

**リブートが選択されるケース**:
- GPU/Neuron デバイスカウントの不一致
- 一時的なソフトウェア問題

**交換が選択されるケース**:
- ハードウェア障害（NVLink エラー、Xid エラーなど）
- 永続的な問題
- Deep Health Checks での重大な障害
- **重要**: [Slurm で auto-resume 使用時は常にノードを交換](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)（リブートなし）
::::

::::details エラー原因特定のための機能

### `None` オプションによるマーキングのみ

`None` オプションを選択すると、HyperPod は以下のように動作します。

1. [HMA が障害を検出](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-node-recovery.html)
2. インスタンスに [Kubernetes label を付加](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-node-labels.html)
3. **自動的な修復・復旧アクションは実行しない**
4. 管理者が手動で調査・対応

これにより、以下が可能になります。

**詳細な根本原因分析**:
- [SSM セッション](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-operate-access-through-terminal.html)でノードに直接ログイン
- `/var/log/aws/clusters/sagemaker-deep-health-check.log` を確認
- ハードウェア診断ツールの追加実行
- メモリダンプやコアダンプの取得

**手動での復旧操作**:
- **EKS**: [`BatchRebootClusterNodes`](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-manual.html) または [`BatchReplaceClusterNodes`](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-manual.html) API
- **EKS**: kubectl で label 付与（`sagemaker.amazonaws.com/node-health-status=UnschedulablePendingReboot` または `UnschedulablePendingReplacement`）
- **Slurm**: [Slurm コマンドまたは HyperPod API](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-replace-faulty-instance.html)

### 推奨される運用パターン

**本番環境**: `Automatic` で高可用性を維持し、CloudWatch ログで事後分析を実施

**開発/テスト環境**: `None` で詳細な根本原因分析を実施し、障害パターンを学習

**ハイブリッドアプローチ**: `Automatic` を有効化しつつ、[手動で quarantine](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-manual.html)（`kubectl cordon`）して調査を完了してから復旧
::::

::::details Slurm クラスターでの Auto-Resume との連携

[Slurm クラスターでは、Automatic Node Recovery と auto-resume 機能が連携](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)します。

### 動作フロー

1. HMA がハードウェア障害を検出
2. ノードがドレイン状態にマーク
3. すべてのジョブが終了後、ノードを自動交換
4. `--auto-resume=1` フラグ付きのジョブは最後のチェックポイントから自動再開
5. auto-resume なしのジョブは手動再送信が必要

### auto-resume の使用方法

```bash
# sbatch の例
srun --auto-resume=1 train_script.sh

# salloc の例
salloc -N 2 --exclusive
srun --auto-resume=1 train_script.sh
```

### 重要な注意点

**GRES (Generic Resources) の制約**: [GRES が付加されたノードでは、Slurm は通常ノード割り当ての変更を許可しない](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)ため、HyperPod auto-resume は障害ジョブを自動的に再キューし、最初から再起動します。

**auto-resume のスコープ**: auto-resume は [`--auto-resume=1` を指定した srun コマンドの現在のジョブステップ](https://slurm.schedmd.com/job_launch.html#step_allocation)でのみ動作します。リソース割り当て内の他の srun コマンドには適用されません。

**環境の一貫性**: ノード交換後、[環境を一貫して設定する必要](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)があります。依存関係のインストールや仮想環境のアクティベーションをスクリプト化する必要があります。

**$SLURM_JOB_NODELIST の注意**: [auto-resume 後は値が古くなる可能性](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-resiliency-slurm-auto-resume.html)があるため使用せず、`scontrol show jobid` で現在のノードリストを動的に取得します。
::::

::::details EKS クラスターでの手動操作

EKS クラスターでは、[2つの方法で手動のノード復旧を実行](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-manual.html)できます。

### API による操作（推奨）

**リブート**:
```bash
aws sagemaker-dev batch-reboot-cluster-nodes \
  --cluster-name arn:aws:sagemaker:region:account:cluster/cluster-name \
  --node-ids i-abc123 i-def456
```

**交換**:
```bash
aws sagemaker-dev batch-replace-cluster-nodes \
  --cluster-name arn:aws:sagemaker:region:account:cluster/cluster-name \
  --node-ids i-abc123 i-def456
```

### kubectl による操作

**ノードの quarantine**:
```bash
kubectl cordon <node-name>
```

**Pod の強制削除**（30分以上終了処理中の場合）:
```bash
kubectl delete pods <pod-name> --grace-period=0 --force
```

**リブートのトリガー**:
```bash
kubectl label nodes <node-name> \
  sagemaker.amazonaws.com/node-health-status=UnschedulablePendingReboot
```

**交換のトリガー**:
```bash
kubectl label nodes <node-name> \
  sagemaker.amazonaws.com/node-health-status=UnschedulablePendingReplacement
```

**重要**: 手動でラベルを使用する場合でも、クラスター作成/更新時に Automatic Node Recovery を有効化する必要があります。
::::

## Resilience-related Kubernetes Labels

[HyperPod は、ヘルスチェック、障害タイプ、計算障害に基づいてノードにラベルを付けます](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-node-labels.html)。これにより、ノードの健全性ステータスを監視し、Deep Health Checks を実行し、障害を特定し、それに応じてノードにラベルを付け、置換またはリブートのために異常なノードに taint を適用し、新しいノードをチェックします。

これらのラベルは、Kubernetes のスケジューリングと管理を通じて、障害のあるノードへのワークロードの配置を防ぎ、クラスター全体の信頼性を向上させます。

## インフラストラクチャへの直接アクセス

HyperPod の重要な機能の一つは、[AWS Systems Manager (SSM) Session Manager を使用してクラスターノードに直接アクセスできる](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-operate-access-through-terminal.html)ことです。これにより、コンテナレベルでのログ監視だけでなく、インフラストラクチャレベルでの詳細なトラブルシューティングと検証が可能になります。

SSM セッションを使用すると、以下のような作業が可能になります。

**ハードウェアエラーのシミュレーションと検証**: テスト環境でハードウェアエラーをシミュレートし、レジリエンシー機能の動作を検証できます。例えば、カーネルログにエラーメッセージを注入することで、Health Monitoring Agent がどのように反応し、Automatic Node Recovery がどのようにトリガーされるかを確認できます。

**リアルタイムの診断**: ノードが「not ready」ステータスになった場合、直接ログインして `/var/log/aws/clusters/sagemaker-deep-health-check.log` などのログファイルを確認し、問題の根本原因を特定できます。

**インスタンス置換の監視**: 障害が検出された後、新しいノードが起動し、コンテナが「creating」ステータスから「running」ステータスに移行する過程を、リアルタイムで観察できます。

この直接アクセス機能は、従来のコンテナオーケストレーションプラットフォームでは提供されないことが多く、HyperPod の運用性とデバッグ容易性を大きく向上させます。特に、大規模な本番環境でのトラブルシューティングにおいて、問題の迅速な特定と解決に不可欠です。
