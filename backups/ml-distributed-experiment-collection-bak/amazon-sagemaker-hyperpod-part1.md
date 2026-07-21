---
title: "Amazon SageMaker HyperPod -- レジリエンシー実現: 大規模学習を支えるインフラストラクチャ (Part 1)"
emoji: "🏗️"
type: "tech"
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

前章では、大規模な GPU クラスターにおいて障害が避けられない現実を見てきました。[100,000 GPU のクラスターでは平均 30 分ごとに GPU 障害が発生](https://epoch.ai/blog/hardware-failures-wont-limit-ai-scaling)し、[Meta の Llama 3 405B モデルの学習では 54 日間で 419 回の予期しない中断](https://www.datacenterdynamics.com/en/news/meta-report-details-hundreds-of-gpu-and-hbm3-related-interruptions-to-llama-3-training-run/)が記録されました。この高い障害率に対して、Meta は高度な自動化により [90% 以上の効果的な学習時間を維持](https://www.datacenterdynamics.com/en/news/meta-report-details-hundreds-of-gpu-and-hbm3-related-interruptions-to-llama-3-training-run/)できましたが、このようなシステムを独自に構築し運用することは、多くの組織にとって大きな負担となります。

[Amazon SageMaker HyperPod](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod.html) は、レジリエントなクラスターをプロビジョニングし、機械学習ワークロードを実行するためのマネージドサービスです。基盤モデルの開発を加速するため、数千の GPU や AWS Trainium といったアクセラレータを用いた大規模な計算クラスターの構築と維持に関わる差別化されない作業を取り除きます。[アクセラレータに障害が発生すると、HyperPod のレジリエンシー機能がクラスターインスタンスを自動的に監視し、障害のあるハードウェアを検出して即座に交換](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod.html)するため、研究者は機械学習ワークロードの実行に集中できます。

## Amazon SageMaker HyperPod の概要

[HyperPod は、Slurm と Amazon EKS という 2 つのオーケストレーションオプションをサポート](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod.html)しています。Slurm サポートでは、既に解説した通りオープンソースのワークロードマネージャーである Slurm との統合により、ヘッドノード、ログインノード、ワーカーノードのセットアップを通じたシームレスなクラスター管理を実現します。Amazon EKS サポートでは、コンテナ化されたワークロードによる基盤モデルの大規模学習を可能にし、動的な容量管理、クラスターインスタンスへの直接アクセス、そしてレジリエンシー機能を提供します。

[HyperPod は UltraServers との統合により、NVIDIA Blackwell GPU を搭載した高性能インフラストラクチャを提供](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod.html)します。各 NVL72 UltraServer は、NVLink で相互接続された 72 個の NVIDIA Blackwell GPU を搭載した 18 のインスタンスを組み合わせており、前世代のインスタンスと比較してより高速な推論と学習性能を実現します。このアーキテクチャは、兆パラメータの基盤モデルを扱う組織にとって特に価値があります。統一された GPU メモリにより、モデル全体を単一の NVLink ドメイン内に保持でき、ノード間のネットワークボトルネックを排除できます。

## 簡単なクラスター作成体験

HyperPod は、[SageMaker コンソール上でのクラスター作成体験を大幅に強化](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-operate-slurm-console-ui.html)しています。従来、クラスターの設定は非常に複雑でした。クラスター自体だけでなく、VPC、サブネット、セキュリティグループ、IAM ロールなどの前提条件となるリソースも適切に設定する必要があったためです。

HyperPod のブラウザベースの管理コンソールを使用すれば、いくつかのパラメータを入力して設定するだけで、簡単にクラスターをセットアップできます。コンソールは、必要な前提条件リソースの作成を支援し、設定の妥当性を検証します。[CloudFormation テンプレート](https://docs.aws.amazon.com/sagemaker/latest/dg/smcluster-getting-started-slurm-console-create-cluster-cfn.html)も提供されており、Infrastructure as Code (IaC) アプローチでのクラスター作成も可能です。

[SageMaker Studio との統合](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-studio.html)により、HyperPod クラスターを Studio IDE から直接管理できます。クラスターの状態監視、ジョブの送信、リソース使用状況の確認など、すべてを統一されたインターフェイスから実行できるため、開発者の生産性が向上します。

# HyperPod のレジリエンシー機能

HyperPod のレジリエンシー機能は、前章で説明した DCGM による階層的な障害検出、統合テレメトリによる根本原因の特定、そして自動化の重要性という概念を実装しています。これらの機能により、Meta が Llama 3 学習で達成したような高い稼働率を、より少ない運用負担で実現できます。

## Health Monitoring Agent による継続的な監視

[HyperPod は Health Monitoring Agent を通じて、クラスターインスタンスの健全性を継続的に監視](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency.html)します。このエージェントは、基本的なヘルスチェックと深層ヘルスチェックの両方を実行し、潜在的な問題を早期に検出します。前章で説明したように、障害の早期検出は失われる学習の進捗を最小化する上で極めて重要です。

基本的なヘルスチェックは、GPU の可用性、メモリエラー、温度異常などの即座に検出可能な問題を監視します。これらのチェックは継続的に実行され、システムの基本的な健全性を保証します。一方、深層ヘルスチェックは、より包括的な診断を通じて、潜在的な問題や性能劣化を検出します。

## Deep Health Checks による包括的な診断

[HyperPod は、クラスターの作成と更新時に Deep Health Checks を実行](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-deep-health-checks.html)します。これらのチェックは、基盤となるハードウェアとインフラストラクチャコンポーネントを徹底的にテストし、クラスターが機械学習モデルの学習に使用される前に信頼性と安定性を確保します。このプロアクティブなアプローチは、クラスターライフサイクルの早期段階で潜在的な問題を特定し、軽減するのに役立ちます。

::::details Instance レベルの Deep Health Checks

Instance レベルのチェックは、個々のインスタンスの健全性を検証します。

### GPU アクセラレータのチェック

**GPU/NVLink カウント**: GPU と NVLink の数を検証し、予期される構成と一致することを確認します。

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

**NCCM local test**: 単一の Trainium ノード上での collective communication 操作の性能を評価します。

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

### NCCM Cluster Test (Trainium 用)

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

[クラスターの作成または更新時に、クラスター管理者はノード（インスタンス）の復旧オプションとして `Automatic`（推奨）または `None` を選択できます](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks-resiliency-node-recovery.html)。`Automatic` に設定すると、HyperPod は障害のあるノードを自動的にリブートまたは交換します。

自動ノード復旧は、Health Monitoring Agent、基本ヘルスチェック、Deep Health Checks から問題が検出されると実行されます。`None` に設定した場合、Health Monitoring Agent は障害が検出されたときにインスタンスにラベルを付けますが、影響を受けたノードに対する修復や復旧アクションを自動的には開始しません。この設定は推奨されません。

前章で説明した Meta の事例では、[419 回の中断のうち 416 回が自動化システムにより処理](https://www.datacenterdynamics.com/en/news/meta-report-details-hundreds-of-gpu-and-hbm3-related-interruptions-to-llama-3-training-run/)されました。HyperPod の Automatic Node Recovery は、この自動化の概念を実装し、手動介入の必要性を最小限に抑えます。

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

## マルチ AZ サポートによるインフラレベルのレジリエンシー

[HyperPod は、複数のアベイラビリティーゾーン（AZ）にわたるクラスターの設定をサポート](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-prerequisites.html#sagemaker-hyperpod-prerequisites-multiple-availability-zones)しています。この機能は、単一 AZ の障害からクラスター全体を保護し、インフラストラクチャレベルでのレジリエンシーを大幅に向上させます。

前章で説明した障害は、主に GPU やネットワークコンポーネントなど、個々のハードウェアレベルでの問題でした。しかし、データセンター全体に影響を与える可能性のある AZ レベルの障害（電源障害、ネットワーク分断、自然災害など）も考慮する必要があります。マルチ AZ 構成により、一つの AZ が完全にダウンした場合でも、他の AZ で稼働しているノードで学習を継続できます。

[2025 年 2 月の機能強化](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-release-notes.html)により、クラスター内の個々のインスタンスグループに対して、異なる AZ にまたがって異なるサブネットとセキュリティグループを指定できるようになりました。これにより、より柔軟なネットワーク設計が可能となり、例えばヘッドノードとワーカーノードを異なる AZ に配置しながら、それぞれに最適化されたネットワーク設定を適用できます。

マルチ AZ 構成は、特に長期間稼働する大規模学習において重要です。Meta の Llama 3 学習のように 54 日間にわたる学習では、その期間中に AZ レベルの障害が発生する確率も無視できません。マルチ AZ サポートにより、このようなインフラレベルの障害に対しても自動的に復旧し、学習を継続できます。

# Elastic Training - 革新的な動的スケーリング

[Elastic Training は、計算リソースの可用性とワークロードの優先度に基づいて学習ジョブを自動的にスケーリングする HyperPod の新機能](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-eks-elastic-training.html)です。この機能は、大規模学習におけるリソース利用の効率化と、優先度の高いワークロードへの迅速な対応を実現します。

## Elastic Training の動作原理

Elastic Training ジョブは、モデル学習に必要な最小限の計算リソースで開始し、異なるノード構成（ワールドサイズ）にわたる自動的なチェックポイントと再開を通じて、動的にスケールアップまたはダウンできます。スケーリングは、データ並列レプリカの数を自動的に調整することで実現されます。

クラスターの利用率が高い期間中、Elastic Training ジョブは、より優先度の高いジョブからのリソース要求に応じて自動的にスケールダウンするように設定できます。これにより、重要なワークロードのための計算リソースが解放されます。オフピーク時にリソースが解放されると、Elastic Training ジョブは自動的にスケールアップして学習を加速し、より優先度の高いワークロードが再びリソースを必要とするときに再びスケールダウンします。

## 主要コンポーネントとの統合

Elastic Training は、以下のコンポーネントと統合されています。

**Amazon EKS**: Kubernetes オーケストレーションの基盤として機能します。

**HyperPod Task Governance**: ジョブのキューイング、優先順位付け、スケジューリングを提供します。[Kueue](https://kueue.sigs.k8s.io/) との統合により、Gang Scheduling（すべての必要な Pod が一緒に起動することを保証）、Gentle Preemption（低優先度の Elastic ジョブがリソースを優雅に譲渡）、Fair-share ポリシーなどの高度なワークロード管理を実現します。

**[PyTorch Distributed Checkpoint (DCP)](https://pytorch.org/docs/stable/distributed.checkpoint.html)**: スケーラブルな状態とチェックポイント管理を提供します。これにより、異なるワールドサイズ間でのチェックポイントの保存と復元が可能になります。

## サポートされるフレームワーク

Elastic Training は、以下のフレームワークをサポートしています。

- PyTorch with Distributed Data Parallel (DDP) and Fully Sharded Data Parallel (FSDP)
- PyTorch Distributed Checkpoint (DCP)

## Elastic Training の利点

前章で説明した Meta の事例では、研究者は 3 時間ごとに発生する障害に対応する必要がありました。Elastic Training を使用すると、障害が発生した場合でも、ジョブは自動的に利用可能なリソースで再開され、研究者の介入を最小限に抑えます。

さらに、優先度ベースのプリエンプションにより、組織は限られた計算リソースを効率的に活用できます。緊急の実験や本番ワークロードが発生した場合、低優先度の Elastic Training ジョブは自動的にスケールダウンし、リソースを譲渡します。優先度の高いジョブが完了すると、Elastic Training ジョブは自動的にスケールアップして学習を再開します。

::::details Elastic Training のセットアップと使用

### 前提条件

**HyperPod EKS クラスター**: HyperPod クラスターと Amazon EKS オーケストレーションが実行されている必要があります。

**HyperPod Training Operator**: Elastic Training は Training Operator v1.2 以降でサポートされます。

**Task Governance と Kueue の設定**: ワークロードの優先度を指定するために、Task Governance 経由で Kueue をインストールおよび設定することを推奨します。Kueue は、キューイング、優先順位付け、Gang Scheduling、リソース追跡、優雅なプリエンプションなどの強力なワークロード管理を提供し、マルチテナント学習環境で動作する上で不可欠です。

### トレーニングコンテナの構築

HyperPod Training Operator は、[HyperPod Elastic Agent Python パッケージ](https://www.piwheels.org/project/hyperpod-elastic-agent/)を通じて提供されるカスタム PyTorch ランチャーと連携します。Elastic Agent をインストールし、`torchrun` コマンドを `hyperpodrun` に置き換えて学習を起動する必要があります。

```dockerfile
FROM ...

...

RUN pip install hyperpod-elastic-agent
ENTRYPOINT ["entrypoint.sh"]

# entrypoint.sh
hyperpodrun --nnodes=node_count --nproc-per-node=proc_count \
  --rdzv-backend hyperpod \
  --pre-train-script pre.sh --pre-train-args "pre_1 pre_2 pre_3" \
  --post-train-script post.sh --post-train-args "post_1 post_2 post_3" \
  training.py --script-args
```

### トレー
