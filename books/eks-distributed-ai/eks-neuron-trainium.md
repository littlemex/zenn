---
title: "Basic09 - Neuron/Trainium 対応 (2 ノード DDP まで実証)"
free: true
---

本章では、これまで GPU 向けに組んできた Karpenter の `accelerator_pools` の仕組みに、AWS Trainium/AWS Inferentia（AWS Neuron）を組み込む方法を扱います。`device_plugin = "neuron"` と書くだけで同じ枠組みに乗り、trn2.48xlarge を 2 台束ねた EFA 越しのマルチノード DDP（分散データ並列）まで実機で確認できています。設計の骨格から実機での検証結果、そして途中で踏んだドライバ起因のハマりどころまで順に見ていきます。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち Karpenter が管理する NodePool 群のうち **Neuron（AWS Trainium/AWS Inferentia）向けのアクセラレータプール** の部分です。GPU プールとは taint の key が違うだけで、同じ Karpenter の仕組みに乗ります。

## これは何をするものか

Basic04 で見た `accelerator_pools` は、GPU と Neuron を「taint の key が違うだけの同じ形のノード」として 1 つの型で表現していました。本章では、その Neuron 側の実装を具体的に見ていきます。

Neuron 対応の骨格は 3 つの部品からできています。

1. **NodePool / EC2NodeClass** — Basic04 と同じ `for_each` レンダリングを、`device_plugin = "neuron"` のプールにもそのまま適用します。GPU プール専用の分岐は存在しません。
2. **Neuron AL2023 AMI** — AWS Trainium/AWS Inferentia のドライバ（`aws-neuronx-dkms`）は Amazon EKS Optimized AL2023 Neuron AMI にすでに同梱されています。GPU Operator が担っていた「ドライバをどうするか」という判断は Neuron 側には存在せず、AMI を正しく選ぶだけで済みます。
3. **Neuron device plugin** — ドライバの上に乗る Helm add-on で、Kubernetes に対して `aws.amazon.com/neuron` という resource を advertise する DaemonSet です。これだけを Helm で追加します。

`neuron-addons.tf` では、`neuron-helm-chart`（`oci://public.ecr.aws/neuron/neuron-helm-chart`）を `local.has_neuron_pool`（`accelerator_pools` の中に `device_plugin = "neuron"` のエントリが 1 つでもあるかどうか）で条件付き導入しています。GPU のみのクラスタではこのリソースは 0 個になり、何も入りません。

AMI の選び方には `ami_ssm_parameter` というフィールドを使います。`ami_alias`（`"al2023@latest"`）に任せると alias 解決の挙動に依存してしまうため、Neuron 用途では Amazon EKS Optimized AL2023 Neuron AMI の SSM パラメータパスを明示的に指定してピン留めするのが確実です。

もう 1 点、GPU 側の知識をそのまま持ち込むと誤解しやすいのが taint の管理主体です。Neuron device plugin もアクセラレータの taint を自分では付けません。GPU Operator が taint を付けないのと同じ理屈で、これは NodePool 側が明示的に `{ key = "aws.amazon.com/neuron", effect = "NoSchedule" }` という taint を各 Neuron ノードに宣言しています（`karpenter-resources.tf`）。

テンソル並列で複数の Neuron デバイスを跨ぐ推論・訓練を行う場合は、`neuron_enable_scheduler = true` で Neuron Scheduler Extension を有効にします。デフォルトは `false` です。何を保証する拡張かは後述の「Neuron Scheduler Extension とテンソル並列」で扱います。

マルチノードで Neuron over EFA（trn2 同士を EFA で接続して collective 通信を行う構成）についても、配線自体はすでに用意されています。EFA device plugin のトレランス一覧には `aws.amazon.com/neuron` が含まれており、`locals.tf` は各プールの代表インスタンスタイプ（`pool_rep_instance_type`）を使って EC2 の `DescribeInstanceTypes` API を plan 時に呼び、EFA のインターフェース数やマルチカード構成を動的に解決します（`pool_efa`）。trn2.48xlarge や trn1.32xlarge のような Neuron インスタンスタイプも GPU インスタンスタイプと同じこのロジックで自動的に解決され、インスタンスタイプ別の静的なテーブルを保守する必要はありません。プール側で `efa_interface_count` / `efa_multi_card` を明示すれば、この自動解決を上書きすることもできます。本章の後半では、この配線を実際に trn2.48xlarge 2 台で動かし、EFA 越しの collective 通信が機能することを実機で確認します。

## Neuron device plugin の条件付き導入

Neuron 側の add-on は [`neuron-addons.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/neuron-addons.tf) にまとまっています。GPU 側の `gpu-addons.tf` の対になるファイルで、構造も似ています。

```hcl
# neuron-addons.tf
locals {
  neuron_helm_values = {
    npd = { enabled = false }

    scheduler = { enabled = var.neuron_enable_scheduler }

    devicePlugin = {
      tolerations = [
        { key = "aws.amazon.com/neuron", operator = "Exists", effect = "NoSchedule" },
        { key = "capacity-reservation", operator = "Exists", effect = "NoSchedule" },
      ]
    }
  }
}

resource "helm_release" "neuron" {
  count = local.has_neuron_pool ? 1 : 0

  name             = "neuron-helm-chart"
  repository       = "oci://public.ecr.aws/neuron"
  chart            = "neuron-helm-chart"
  version          = var.neuron_helm_chart_version
  namespace        = "kube-system"
  create_namespace = false

  values = [yamlencode(local.neuron_helm_values)]

  depends_on = [helm_release.karpenter]
}
```

`local.has_neuron_pool` は [`locals.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/locals.tf) で次のように定義されています。

```hcl
# locals.tf
has_gpu_pool    = length([for k, p in var.accelerator_pools : k if p.device_plugin == "nvidia"]) > 0
has_neuron_pool = length([for k, p in var.accelerator_pools : k if p.device_plugin == "neuron"]) > 0
```

`accelerator_pools` の全エントリを走査して `device_plugin == "neuron"` のものが 1 つでもあれば `true` になる、というだけの単純な集計です。この値を `helm_release.neuron` の `count` にそのまま使っているのが読みどころで、GPU プールしか定義していないクラスタでは `count = 0` となり、`neuron-helm-chart` は一切 apply されません。逆に Neuron プールを 1 つ追加すると、次の `terraform apply` でこの Helm リリースが追加されます。GPU/Neuron の両方を使う混在クラスタでは、`has_gpu_pool` と `has_neuron_pool` がともに `true` になり両方の add-on が入ります。

`neuron_helm_values` の中身も 3 点だけです。

- `npd.enabled = false` — Node Problem Detector を明示的に無効化しています。Karpenter と Neuron の NPD/DRA（Dynamic Resource Allocation）の組み合わせは未サポートのため、この構成では入れません。
- `scheduler.enabled = var.neuron_enable_scheduler` — 変数 1 つでそのまま Helm values に流し込んでいます。デフォルト `false` なので、明示的に `true` にしない限り Scheduler Extension は入りません。
- `devicePlugin.tolerations` — device plugin の DaemonSet 自身が Neuron taint（`aws.amazon.com/neuron`）と Capacity Block taint（`capacity-reservation`）を tolerate するよう明示しています。plugin 自身がこれらの taint を tolerate できなければ、advertise する対象の Neuron ノードにそもそも乗れません。

`depends_on = [helm_release.karpenter]` も見落としやすい点です。Neuron device plugin が意味を持つのは Karpenter が Neuron ノードを実際に起動した後なので、Karpenter コントローラより先に入れる理由はありません。この依存関係の主目的は apply 時の順序ではなく destroy 時の順序で、`karpenter.tf` の `null_resource.wait_for_node_drain` がこの Helm リリースに同じ形で `depends_on` することで、ノードのドレインが完了する前に device plugin が消えてしまう事故を防いでいます。

## Neuron Scheduler Extension とテンソル並列

`neuron_enable_scheduler` は [`variables.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/variables.tf) でこう定義されています。

```hcl
# variables.tf
variable "neuron_enable_scheduler" {
  description = <<-EOT
    Enable the Neuron Scheduler Extension. Required for pods that request more than one
    Neuron device (e.g. tensor-parallel serving across many chips on trn2.48xlarge), so
    that contiguous device IDs are guaranteed. Off by default (single-device workloads
    do not need it). The Node Problem Detector (npd) is always disabled here because
    Karpenter + Neuron DRA/NPD is unsupported.
  EOT
  type        = bool
  default     = false
}
```

デフォルトが `false` なのは「1 プロセスが 1 デバイスしか使わない単一デバイス推論では不要」という判断です。これに対して、複数の Neuron デバイスを跨ぐテンソル並列を行う場合は事情が変わります。Kubernetes のデフォルトスケジューラは、Pod が要求する `aws.amazon.com/neuron: "<n>"` の数だけデバイスを割り当てはしますが、割り当てられる device ID が連続（contiguous）である保証はしません。トレーニング・推論ランタイム側がテンソル並列で複数デバイスを 1 プロセス内から束ねて使う際、device ID が飛び飛びだと初期化に失敗したり、意図しないトポロジで通信が組まれたりする可能性があります。Neuron Scheduler Extension は、Kubernetes のスケジューリング拡張ポイント（extender）としてこの割り当てに介入し、1 つのノードの中で contiguous な device ID の集合を選んで Pod に割り当てることを保証します。

この変数は `neuron-addons.tf` の `neuron_helm_values.scheduler.enabled` にそのまま渡されるだけなので、有効化の手順は `terraform.tfvars` に `neuron_enable_scheduler = true` と 1 行書いて `apply` するだけです。単一デバイス推論から複数デバイスのテンソル並列サービングに構成を切り替えるときに、このフラグを忘れずに立てる必要があります。忘れた場合の失敗モードは「動くこともあれば動かないこともある」という不安定さで、単純な起動失敗より原因特定が難しい点に注意してください。

## 全体の中での位置付け

Basic04 で作った `accelerator_pools` の型は GPU/Neuron 共通です。本章はその型に `device_plugin = "neuron"` のエントリを 1 つ追加し、`neuron-addons.tf` が条件付きで Neuron device plugin を導入するところから始めて、Basic06 で見た EFA を使ったマルチノード構成を Neuron 側でも実際に動かすところまでを扱います。GPU 側では NCCL がその役割を担っていましたが、Neuron では torch-neuronx（PyTorch/XLA バックエンド）と Neuron 用の collective 通信ライブラリがその役割を担います。

## 設計上の注意

**Karpenter + Neuron の NPD/DRA は非サポートです。** この構成では `neuron-helm-chart` の Node Problem Detector（NPD）を `npd.enabled = false` で明示的に無効化しています。DRA（Dynamic Resource Allocation、デバイス割り当ての新 API）はチャートのデフォルトで無効なので、こちらは有効化しないことで結果的に使いません。いずれも Karpenter がノードのライフサイクルを管理する構成とは組み合わせとして未サポートのため、オフのまま運用します。

**EFA device plugin の toleration には必ず `aws.amazon.com/neuron` を含めてください。** EFA device plugin は GPU と Neuron の両方のプールで共有される 1 つの DaemonSet であり、その toleration に Neuron taint が入っていなければ trn2 ノードには乗れません。乗れなければ `vpc.amazonaws.com/efa` がそのノードで一切 advertise されず、EFA を要求する Pod は理由の分かりにくい Pending のまま止まります。この構成ではすでに toleration に含めてありますが、自分で device plugin の設定を変更する場合はここを崩さないよう注意してください。

**Neuron Scheduler Extension を入れ忘れると多デバイス割り当てが崩れる可能性があります。** 詳細は前掲の「Neuron Scheduler Extension とテンソル並列」を参照してください。複数デバイスをまとめて使う構成に切り替えるときは `neuron_enable_scheduler` を `true` にするのを忘れないでください。

# ワークショップ実施

ワークロードは Helm チャート `charts/experiments` で管理しており、`helm template ... | k apply -f -` でレンダリングして適用します（`helm install` は使いません）。

trn2 には大きく 2 つのシェイプがあり、必要なデバイス数で選びます。どちらも同じ `accelerator_pools` の書き方で扱えます。

| インスタンスタイプ | Trainium2 デバイス | NeuronCore（LNC=2） | EFA schedulable | 本章での用途 |
|---|---|---|---|---|
| trn2.3xlarge | 1 | 4 | 1 | 手順 2〜4（プール投入・デバイス認識・単一デバイス実行） |
| trn2.48xlarge | 16 | 64 | 15 | 手順 5〜6（2 ノード EFA 越し DDP、world_size 64） |

手順 5 のマルチノード DDP は trn2.48xlarge を 2 台使って検証したものです。手順 2〜4 は最小構成の trn2.3xlarge 1 台でも同じように確認でき、本書ではその構成でも再現しています。デバイス数と EFA 枚数以外は手順が変わらないため、まず 3xlarge で配線を確かめてから 48xlarge の Capacity Block に進む、という段取りが取れます。

## 1. 前提を確認する

- Basic01 でクラスタを構築済み・`k` エイリアスと current-context が設定済みであること（開き直した場合は Basic01 step 3 の `use-context` / `set-context` / `alias k=kubectl` を実行し直してください）
- Basic04 で `accelerator_pools` の書き方を確認済みであること。本章はそこに `device_plugin = "neuron"` のプールを追加するだけで、型自体は変わりません
- **使いたい Trainium が Basic01 で選んだリージョンに存在するか**を確認していること。Trainium は提供リージョンが限られており、しかもシェイプごとに異なります。存在しないタイプを書くと、EC2 の `DescribeInstanceTypes` がそのタイプを返さないため plan の段階で止まります

```text
Error: reading EC2 Instance Type: api error InvalidInstanceType:
The following supplied instance types do not exist: [trn2.48xlarge]
```

本書の執筆時点で実測した可用性は次のとおりです。

| インスタンスタイプ | 存在を確認したリージョン |
|---|---|
| trn2.48xlarge | us-east-2 |
| trn2.3xlarge | ap-southeast-4 |
| trn1.32xlarge | us-east-1 / us-east-2 / us-west-2 / ap-southeast-4 |
| inf2.48xlarge | us-east-1 / us-east-2 / us-west-2 / ap-northeast-1 |

trn2 の 2 つのシェイプが同じリージョンに無いことに注意してください。手元のリージョンで何が使えるかは次のコマンドで確認できます（返ってこないタイプはそのリージョンに存在しません）。

```bash
aws ec2 describe-instance-types --region <region> \
  --instance-types trn2.48xlarge trn2.3xlarge trn1.32xlarge inf2.48xlarge \
  --query 'InstanceTypes[].InstanceType' --output text
```

使いたいタイプが Basic01 のリージョンに無い場合は、そのリージョン用に別の作業ディレクトリと state を用意してクラスタを立てるのが安全です（同じ state で `region` だけ書き換えると、既存クラスタを作り直そうとします）。trn2.3xlarge と trn2.48xlarge が別リージョンにしか無いケースの具体的な切り替え手順は、手順 5 の冒頭で扱います。

## 2. accelerator-pools.auto.tfvars に Neuron プールを追加する

リージョンを確認したら、`accelerator_pools` に Capacity Block を使う trn2 のプールを追加します。プール定義は Basic04 と同じく `accelerator-pools.auto.tfvars` に書きます。`trn2 = { ... }` は既存の `accelerator_pools = { ... }` マップの中に 1 エントリとして追記するもので、この行だけを単独のファイルとして置くわけではありません。

```hcl
# accelerator-pools.auto.tfvars（既存の accelerator_pools マップに、gpu-ddp 等と並べて追記）
accelerator_pools = {
  # ...既存の gpu-ddp 等はそのまま...
  trn2 = {
    instance_types    = ["trn2.3xlarge"]
    device_plugin     = "neuron"
    capacity_type     = "reserved"
    cb_reservation_id = "<capacity-block-reservation-id>" # zone はこの予約から導出
    ami_ssm_parameter = "/aws/service/eks/optimized-ami/1.35/amazon-linux-2023/x86_64/neuron/recommended/image_id"
  }
}
```

`device_plugin = "neuron"` に切り替えている以外、フィールドの構造は Basic04 で書いた GPU プールと同じです。`cb_reservation_id` は事前に確保した Capacity Block の予約 ID に置き換えます。`zone` は書いていません。`reserved` プールの AZ は予約から自動導出される（`az.tf` が予約の AZ を読み取る）ので、trn2 プールでも Basic05 の GPU プールと同じく手書き不要です。プール名（map のキー）が Karpenter のノードラベル `node-role=<プール名>` になる点は後で使うので覚えておいてください。ここでは `trn2` としています。

trn2.3xlarge が Capacity Block の対象であることは、実際に CB を購入して起動する形で確認済みです。Capacity Block の在庫が無い、または短時間の検証で予約を確保したくない場合は、`capacity_type = "on-demand"` に変え `cb_reservation_id` の行を削除するだけで on-demand のプールとして同じ手順が通ります（`zone` は on-demand では最初のクラスタ AZ に自動で解決されます）。

Scheduler Extension（後述）を有効にする `neuron_enable_scheduler` は、この `trn2` プールのフィールドではありません。`accelerator_pools` マップの中に書くと invalid な設定になるので注意してください。これは `terraform.tfvars` 側に書くトップレベルの変数で、詳細は次節「Neuron Scheduler Extension とテンソル並列」で扱います。今回の trn2.3xlarge はデバイスが 1 個なのでこの検証では有効化していません。

## 3. apply する

```bash
cd infra/eks
terraform apply
k get nodepool trn2
k get ec2nodeclass trn2
```

apply が作るのは NodePool と EC2NodeClass の定義であって、この時点ではまだノードは立ちません。Karpenter は Neuron を要求する Pod（Pending）が現れて初めてノードを起動します。この時点での出力例（`NODES` はまだ 0 台）:

```text
NAME   NODECLASS   NODES   READY   AGE
trn2   trn2        0       True    2m

NAME   AGE
trn2   2m
```

`has_neuron_pool` が `true` になったことを検知して、Neuron device plugin が導入されます（Scheduler Extension は `neuron_enable_scheduler` を有効にした場合のみ）。EFA device plugin もすでに `aws.amazon.com/neuron` taint を tolerate する設定で導入済みのため、追加の変更は不要です。

実機では apply 直後に両方の DaemonSet が入り、対象ノードがまだ無いので `DESIRED` は 0 のままです。

```bash
k get ds -n kube-system
```

```text
NAME                        DESIRED   CURRENT   READY   AGE
aws-efa-k8s-device-plugin   0         0         0       3m
neuron-device-plugin        0         0         0       3m
```

Capacity Block が `active` になっていれば、この後 Pod を投入した時点で Karpenter が trn2 を起動し、両 DaemonSet がそのノードに配られます。

## 4. 単一ノードで device plugin の advertise を確認する

まず 1 台の trn2 でデバイスが正しく見えるかを確認します。`charts/experiments` の `neuronProbe` ワークロードを使います。

```bash
export NAMESPACE=distai
k create namespace "$NAMESPACE" --dry-run=client -o yaml | k apply -f -
helm template exp charts/experiments \
  --set namespace="$NAMESPACE" --set neuronProbe.enabled=true --set neuronProbe.nodeRole=trn2 \
  | k apply -f -
```

このプローブ Pod は `node-role=trn2` の nodeSelector で Neuron プールに固定され、`aws.amazon.com/neuron` taint への toleration を持ちます。最初の 1 回は Karpenter が trn2.3xlarge を起動するため数分かかります。Pod が `Running` になったら、内部で `neuron-ls` を実行してデバイスが列挙されるか確認します。

```bash
k -n "$NAMESPACE" exec neuron-probe -- neuron-ls
```

実機出力（trn2.3xlarge）:

```text
instance-type: trn2.3xlarge
instance-id: i-0abcdef1234567890
logical-neuroncore-config: 2
+--------+--------+----------+--------+--------------+----------+------+
| NEURON | NEURON |  NEURON  | NEURON |     PCI      |   CPU    | NUMA |
| DEVICE | CORES  | CORE IDS | MEMORY |     BDF      | AFFINITY | NODE |
+--------+--------+----------+--------+--------------+----------+------+
| 0      | 4      | 0-3      | 96 GB  | 0000:33:00.0 | 0-11     | 0    |
+--------+--------+----------+--------+--------------+----------+------+
```

Trainium2 デバイスが 1 個、その上に論理 NeuronCore が 4 つ（`logical-neuroncore-config: 2` = LNC=2 のもとでの数）、デバイスメモリ 96 GB という構成です。ノード側の allocatable も同じ数字を示します。

:::message
`neuron-ls` が示す 96 GB は Trainium2 デバイス 1 個の HBM 容量です。`aws ec2 describe-instance-types --instance-types trn2.3xlarge` の `NeuronInfo.NeuronDevices[].MemoryInfo.SizeInMiB` は `524288`（512 GiB）を返しますが、これは集計の単位が異なる値なので、`neuron-ls` の表示と食い違って見えても誤りではありません。実機で確認したいときは `neuron-ls` の出力を正とします。
:::

```bash
k get nodes -l node-role=trn2 -o custom-columns=\
'NAME:.metadata.name,NEURON:.status.allocatable.aws\.amazon\.com/neuron,CORE:.status.allocatable.aws\.amazon\.com/neuroncore,EFA:.status.allocatable.vpc\.amazonaws\.com/efa'
```

実機出力:

```text
NAME                                             NEURON   CORE   EFA
ip-10-0-a-b.ap-southeast-4.compute.internal      1        4      1
```

`aws.amazon.com/neuron: 1` と `aws.amazon.com/neuroncore: 4` は Neuron device plugin が、`vpc.amazonaws.com/efa: 1` は別の DaemonSet である EFA device plugin が、それぞれ advertise しています。trn2.3xlarge は EFA カードが 1 枚だけの single-card 構成なので、schedulable も 1 です。

対して trn2.48xlarge では `neuron: 16` / `neuroncore: 64` / `efa: 15` になります。EFA が 16 枚のカードに対して 15 なのは、ネットワークカード 0 をノードの IP を担う ENA として構成し、残る 15 枚を EFA 専用インターフェースとして立てるためです（EC2 の制約上、カード 0 のインターフェースは ENA を含む必要があります）。要求できる枚数はインスタンスタイプごとに違うので、決め打ちせず `terraform output accelerator_pool_efa_schedulable` の値か、ノードの allocatable を直接参照してください。

:::message
Karpenter は hugepages を「新しいノードをどのインスタンスタイプで立てるか」の判断材料として扱いません。そのため、まだ対象ノードが存在せずプロビジョニングを誘発するプローブ Pod で `hugepages-2Mi` を要求すると、Karpenter が「条件を満たすインスタンスタイプがない」と誤判定して NodeClaim を作らず、Pod が永久に Pending になります。プローブでは hugepages を要求しないのはこのためです。一方、次の 2 ノード DDP は起動済みのノードに対して適用するので hugepages を要求できます。
:::

### 単一デバイスで計算まで通す

`neuron-ls` はデバイスの列挙しかしないため、Neuron ランタイムの初期化までは検証できません。実際に計算を流して初めて、ドライバとランタイムの整合が確認できます。`neuronProbe` は前述のとおり hugepages を要求しない設計なので、ノードが起動したあとに hugepages を持つ Pod を別途立てて実行します。

この Pod は `neuronProbe` と同じ DLC イメージを使い、`hugepages-2Mi` を足しただけのものです。チャートには含まれないので、次のように直接作ります。ノードは既に起動しているので、この Pod で hugepages を要求しても問題ありません。

:::message
DLC のレジストリアカウント ID `763104351884` は主要リージョン向けのものです。ap-southeast-4 のような比較的新しいリージョンでは、DLC の提供対象になっていない、または別のアカウント ID が割り当たっている場合があります。`<region>` を置き換えるだけで pull できるとは限らないため、事前に DLC 公式の available images 一覧でリージョンとアカウント ID を確認してください。また、ノードの IAM ロール（Karpenter node role）に ECR pull 権限（`AmazonEC2ContainerRegistryReadOnly` 相当）が付与されている必要があります。この構成ではすでに付与済みです。
:::

```bash
# neuronProbe がデバイスを掴んでいるので先に退かす（trn2.3xlarge はデバイス 1 個）
k -n "$NAMESPACE" delete pod neuron-probe

# hugepages 付きの Pod を立てる。image は neuronProbe と同じ DLC を指定する
# アカウント ID・リージョン・タグは事前に確認した DLC の値に読み替える
DLC=763104351884.dkr.ecr.<region>.amazonaws.com/pytorch-inference-neuronx:2.9.0-neuronx-py312-sdk2.29.1-ubuntu24.04

cat <<EOF | k apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: neuron-compute
  namespace: $NAMESPACE
spec:
  restartPolicy: Never
  nodeSelector: { node-role: trn2 }
  tolerations:
    - { key: aws.amazon.com/neuron, operator: Exists, effect: NoSchedule }
    - { key: capacity-reservation, operator: Exists, effect: NoSchedule }
  containers:
    - name: compute
      image: $DLC
      command: ["sleep", "3600"]
      resources:
        limits:
          aws.amazon.com/neuron: "1"
          vpc.amazonaws.com/efa: "1"
          hugepages-2Mi: 2Gi
          memory: 16Gi
        requests:
          cpu: "4"
          memory: 16Gi
      volumeMounts:
        - { name: shm, mountPath: /dev/shm }
  volumes:
    - name: shm
      emptyDir: { medium: Memory, sizeLimit: 8Gi }
EOF

k -n "$NAMESPACE" wait --for=condition=ready pod/neuron-compute --timeout=10m

# ランタイムを初期化して計算を流す
k -n "$NAMESPACE" exec neuron-compute -- python -c "
import torch, torch_xla.core.xla_model as xm
d = xm.xla_device()
a = torch.ones(512, 512, device=d) * 3
b = torch.ones(512, 512, device=d) * 4
c = torch.matmul(a, b)
xm.mark_step()
print('device:', d)
print('matmul[0,0]:', float(c[0,0].cpu()))
"
```

実機出力（trn2.3xlarge、デバイス 1 個）:

```text
Compiler status PASS
device: xla:0
matmul[0,0]: 6144.0
```

`512 * 3 * 4 = 6144` が返り、NEFF のコンパイルも `PASS` しています。ここまで通れば、ドライバ・ランタイム・torch-neuronx の 3 層が噛み合っていることが確認できます。

:::message alert
この計算を実行する Pod では `hugepages-2Mi` を要求してください。Neuron ランタイムは hugepages を必要とし、要求していない Pod では `neuron-ls` が成功しても `nrt_init` が `NRT_FAILURE` で失敗します。「ノードを起動させる Pod は hugepages なし、計算を流す Pod は hugepages あり」と役割を分けるのが安全です。
:::

:::message alert
コンテナの Neuron SDK は、ノード AMI のドライバより新しくしてはいけません。ドライバのバージョンは次のコマンドで確認できます。

```bash
k -n "$NAMESPACE" exec <pod> -- bash -lc 'NEURON_RT_LOG_LEVEL=INFO python -c "import torch_xla.core.xla_model as xm; xm.xla_device()" 2>&1 | grep -i "neuron driver"'
```

ここで確認している trn2.3xlarge（ap-southeast-4）の実機ドライバは 2.27.4.0 で、上の計算はこのドライバと `sdk2.29.1` の DLC の組み合わせで問題なく通っています。一方、後述の手順 5 で使う trn2.48xlarge（us-east-2）側の別環境では、ランタイム 2.32.31（SDK 2.30 の DLC）に対してドライバが 2.29 という、版数の食い違う組み合わせを観測しました。

```text
INFO NRT:nrt_init  Neuron Runtime 2.32.31.0 built on May 16 2026
INFO NRT:nrt_init  Found neuron driver: 2.29
```

この組み合わせでは HBM の初期化までは成功するのに、直後の `dmem_buf_copyin`（ホストからデバイスへの DMA コピー）で失敗します。原因と暫定回避策は後述の「検証時の注意」で扱いますが、最も素直な解決は DLC のタグをドライバに合わせることです。48xlarge 側の環境ではイメージを `sdk2.29.1` に下げるだけで、最適化を無効化せずに計算が通りました。使用中の AMI のドライバが属する SDK リリースを Neuron の互換表で確認し、その SDK に対応する DLC タグを選んでください（ドライバパッケージの版数と DLC タグの SDK 版数は別の体系なので、数字をそのまま合わせるのではなく互換表を経由します）。
:::

## 5. 2 ノードの EFA 越し DDP を動かす

ここが本章の主目的です。trn2.48xlarge 2 台に torch-neuronx の分散データ並列（DDP）を流し、EFA 越しに collective 通信（all-reduce）が成立することを確認します。

前掲のとおり trn2.48xlarge は trn2.3xlarge とは別リージョン（本書の実測では us-east-2）にしかありません。ここまで手順 2〜4 を進めてきたのが trn2.3xlarge のリージョン（ap-southeast-4 など）であれば、この手順からは 48xlarge 用の別クラスタに切り替えます。

1. 48xlarge が存在するリージョン用に、別の作業ディレクトリと state を用意します（手順 1 で触れたとおり、同じ state で `region` だけ書き換えると既存クラスタを作り直そうとしてしまうため）。そのディレクトリで Basic01 の手順に沿ってクラスタを構築し直すか、すでに 48xlarge リージョンにクラスタがある場合は `aws eks update-kubeconfig` でそちらの context に切り替えます。
2. その `terraform.tfvars` / `accelerator-pools.auto.tfvars` に 48xlarge 用のプールを定義します。`instance_types = ["trn2.48xlarge"]`、`capacity_type = "reserved"`、`cb_reservation_id` はこのリージョンで確保した Capacity Block の予約 ID、`ami_ssm_parameter` は手順 2 と同じ Neuron 用 AMI のパスに `1.35` などのバージョン部分を合わせたものにします。
3. 複数の Trainium2 デバイスを 1 プロセスから束ねて使う構成に進む場合は、この `terraform.tfvars` に `neuron_enable_scheduler = true` を追記します（詳細は前掲「Neuron Scheduler Extension とテンソル並列」）。今回の DDP は 1 プロセスが 1 デバイスだけを使うので、無くても動作します。
4. `terraform apply` して NodePool/EC2NodeClass を作ります。手順 3 と同じ手順（`k get nodepool` などでの確認）をこのリージョンでも一度実行しておくと安全です。

`charts/experiments` の `neuronDdp` ワークロードは、ConfigMap（all-reduce テストスクリプト）と 2 台のノードにそれぞれ載る Pod（`neuron-server` / `neuron-client`）をレンダリングします。2 台は podAntiAffinity で必ず別ノードに配置されるので、通信は確実にノード間の EFA を通ります。

```bash
helm template exp charts/experiments \
  --set namespace="$NAMESPACE" --set neuronDdp.enabled=true --set neuronDdp.nodeRole=trn2 \
  | k apply -f -
```

チャートは `experiments.namespace` という共通ヘルパーで namespace を決めます。既定の `namespace: ""` のままなら `helm template` の `-n` がそのまま使われるので、通常は `-n "$NAMESPACE"` だけで足ります。`--set namespace=<名前>` はそれを明示的に上書きしたいときの手段です。

2 台目の trn2 が起動して両 Pod が `Running` になったら、あとは両ノードで `torchrun` を起動するだけです。ここでの rendezvous は `torchrun` の `--master-addr` / `--master-port` に master ノードの IP を直接渡す方式なので、torchrun 自体に Pod 間の SSH は必要ありません。ただし、このテンプレートには両 Pod が `Running` になった後に SSH 鍵を配布する手順も用意されています（NCCL 版ワークロードと同じ作りで各 Pod に sshd を持っているため）。今回の all-reduce の疎通確認だけであれば鍵配布は不要ですが、後述の MNIST MLP のように別のマルチプロセス実行方式を試す場合に備えて用意されている手順、と理解しておいてください。両 Pod は hostNetwork なので Pod IP はノード IP と一致します。

Pod の IP を確認し、両ノードで `torchrun` を起動します。1 ノードあたりの並列数（`--nproc-per-node`）は 32 で、2 ノードで world_size は 64 になります。

ここで並列数の数え方を整理しておきます。trn2.48xlarge は 16 個の Trainium2 デバイスを積んでおり、Trainium2 は 1 デバイスあたり 8 個の物理 NeuronCore を持つため、物理 NeuronCore は合計 128 個です。この構成は LNC（logical NeuronCore config）=2 で動いており、2 つの物理コアが 1 論理コアに束ねられます。その結果、1 ノードあたりの論理 NeuronCore は 64 個になり、これは手順 4 で device plugin が advertise していた `aws.amazon.com/neuroncore: 64` と一致します。

この検証では 1 ノードあたり 32 プロセス（`--nproc-per-node=32`）で起動しており、論理コアをすべて使い切る構成（`--nproc-per-node=64`）ではなく、疎通と DDP の成立を確認するためのスモーク構成である点に注意してください。プロセス数を変える場合は、この後のワークロードの `nprocPerNode` の値も合わせて調整します。

```bash
k -n "$NAMESPACE" get pods -o wide   # server / client の IP を確認
SERVER_IP=<neuron-server のノード IP>

# client（node-rank=1）を先に起動
k -n "$NAMESPACE" exec neuron-client -- bash -lc "cd /opt/test && \
  torchrun --nnodes=2 --node-rank=1 --nproc-per-node=32 \
    --master-addr=$SERVER_IP --master-port=29500 allreduce_test.py" &

# server（node-rank=0）
k -n "$NAMESPACE" exec neuron-server -- bash -lc "cd /opt/test && \
  torchrun --nnodes=2 --node-rank=0 --nproc-per-node=32 \
    --master-addr=$SERVER_IP --master-port=29500 allreduce_test.py"
```

`FI_PROVIDER=efa` などの EFA 関連の環境変数は、Pod の `env` にすでに設定されているため（`experiments.neuronDdpEnv`）、`exec` コマンド側で明示する必要はありません。

初回は NEFF（Neuron 実行ファイル）のコンパイルが走るため時間がかかります。完了すると rank 0 が次のように出力します。

```text
[rank 0] step 0: result=64.0 (expected 64)
...
[rank 0] ALL 20 STEPS OK. world_size=64 elapsed=20.09s
[rank 0] DONE - SUCCESS
```

各ステップの all-reduce 結果が world_size（=64）と一致しており、2 台のノードにまたがる 64 プロセス全員が collective に参加できたことを意味します。より大きなテンソル（1 ステップあたり約 67MB）で 30 ステップ流しても全ステップで結果が一致し続けました。

## 6. EFA が実際に使われていることを確認する

「2 ノードで通信が成立した」だけでは、その通信が EFA を通ったのか TCP にフォールバックしたのか分かりません。EFA が使われている証拠を 2 つの角度から確認します。

1 つ目は libfabric のログです。`neuronDdp` ワークロードは `fiLogInfo` という values フラグでこのログ出力を切り替えられるので、`--set neuronDdp.fiLogInfo=true` を付けて手順 5 のレンダリングをやり直し、Pod を再作成してから同じ `torchrun` を実行します。すると、コンテナの標準出力に次のようなログが現れます。

```text
libfabric:...:core:core:ofi_register_provider():526<info> registering provider: efa (204.0)
libfabric:...:core:core:fi_fabric_():1588<info> Opened fabric: efa-direct
```

ここで注意したいのは、`registering provider: efa` の行だけでは EFA が使われた証拠にならないことです。この `registering provider:` 行は libfabric がビルドに含む全プロバイダについて初期化時に出力するもので、実際のログには `efa` に加えて `lnx` や `off_coll` などの行も並びます。TCP にフォールバックした場合でも `registering provider: efa` 自体は出力されます。実際に EFA のファブリックが開かれた証拠になるのは `Opened fabric: efa-direct` の行の方です。全 64 プロセスがこの行を出力しており、`efa-direct`（libfabric がデバイス機能を直接公開するファブリック）越しに通信が確立したことを裏付けています。

2 つ目はホスト側の EFA NIC の RDMA 書き込みカウンタです。`torchrun` の実行前後で、両ノードに対してそれぞれ次のコマンドを実行し、カウンタの差分を見ます。

```bash
for f in /sys/class/infiniband/rdmap*/ports/1/hw_counters/rdma_write_bytes; do echo "$f: $(cat "$f")"; done
```

このコマンドをノード上で直接（SSM や `kubectl node-shell` などで）実行すると、両ノードでほぼ対称に 0 から 2.5 億バイト超まで増加します。片方向でなく双方向に RDMA write が成立していること、そして複数の EFA NIC で同時に増えていて単一 NIC がボトルネックになっていないことも確認できます。

さらに、公式サンプル（aws-neuron-samples）の [`torch-neuronx/training/mnist_mlp/train_torchrun.py`](https://github.com/aws-neuron/aws-neuron-samples) を DDP で流す実践的なワークロードも 2 ノード（各 16 ワーカー、計 32 ワーカー）で完走し、両ノードの全ワーカーが最終 loss を算出しました。`neuronDdp` の ConfigMap は `files/allreduce_test.py` をそのまま埋め込んでいるだけの仕組みなので、この `files/` の内容を `train_torchrun.py` に差し替えてチャートをレンダリングし直せば、同じ `torchrun` コマンドで実行できます。ConfigMap の差し替え自体の具体的な手順はチャート側の改修を伴うため、本書では詳細を割愛し、この構成で実際にモデル訓練が 2 ノードで回ることを確認できた、という結果のみ共有します。

## 検証時の注意

**ドライバとランタイムの `zerocopy` ABI 不一致で `nrt_init` が失敗することがあります。** 今回の検証では、上の起動コマンドをそのまま実行すると、Neuron ランタイムの初期化が次のエラーで失敗するハマりどころを踏みました。

```text
NRT:nrt_init  Failed to initialize devices, error:1
TDRV:ucode_ll_create  ucode_lib_ll_create failed, error: 6
TDRV:dmem_buf_copyin  Copy from buffer to memory failed
```

一見 hugepages 不足に見えますが、真因は別でした。`strace` で追うと、`NEURON_IOCTL_MEM_BUF_ZEROCOPY64`(ioctl 番号 0x7c)が `EINVAL` で失敗しており、これが上のエラー連鎖の起点でした。原因は、ホストのカーネルドライバ(`aws-neuronx-dkms` 2.29.0.0)と、コンテナ内の Neuron ランタイム(2.32.31.0、SDK 2.30 の DLC)との間で、この zerocopy ioctl が期待する構造体サイズが食い違っていたことです。ドライバのソース(`aws-neuron-driver` の `neuron_cdev.c`)には `_IOC_SIZE(cmd) != sizeof(arg)` なら `EINVAL` を返す厳格な ABI チェックがあり、バージョン差でこれに引っかかっていました。

恒久的な対処は、ノードの AMI が持つドライバが属する Neuron SDK のリリースを互換表で確認し、その SDK に対応する DLC タグへ揃えることです。ドライバパッケージの版数（`aws-neuronx-dkms` の `2.29.0.0` のような数字）と DLC タグに書かれた SDK 版数（`sdk2.30` のような数字）は別の体系なので、単純に同じ数字に揃えれば良いわけではありません。今回のように手元の DLC タグとノード AMI のドライバがずれている場合の回避策として、`neuronDdp` ワークロードには `disableZerocopy` という values フラグが用意されています。`nrt_init` が上のエラーで失敗したときだけ、`--set neuronDdp.disableZerocopy=true` を付けて手順 5 のレンダリングをやり直し、Pod を再作成してから同じ `torchrun` を実行してください。これは Pod の env に `NEURON_RT_DBG_ZEROCOPY=0` を注入して zerocopy 経路をバイパスする仕組みで、最適化を 1 つ無効化する設定です。デフォルトは `false` になっており、バージョン差を解消するまでの暫定策としてのみ使ってください。

**Capacity Block の期限に注意してください。** trn2 のような高性能インスタンスは Capacity Block で確保することが多く、期限が来るとインスタンスは自動的に回収されます（実際、今回の検証でも期限の少し前にインスタンスが `shutting-down` に入りました）。長時間の実験は期限に余裕を持って始め、`cb_end_date` を設定して期限前アラート（Basic05 参照）を受け取れるようにしておくと安全です。

## 検証後のクリーンアップ

trn2.48xlarge は Capacity Block を使っていても高額な構成なので、検証が終わったら速やかに片付けてください。Basic07 と同じく、レンダリングした Pod だけを対象にするなら `helm template` の出力をそのまま `k delete -f -` に渡すのが基本です。投入時に指定した `--set` の値をそのまま再現すれば、チャートが出力した全リソースを取りこぼしなく削除できます。

```bash
helm template exp charts/experiments \
  --set namespace="$NAMESPACE" --set neuronDdp.enabled=true --set neuronDdp.nodeRole=trn2 \
  | k delete -f -
```

手順 4 の `neuronProbe` が残っている場合は同様に `--set neuronProbe.enabled=true --set neuronProbe.nodeRole=trn2` を付けて削除するか、直接 `k -n "$NAMESPACE" delete pod neuron-probe neuron-compute` で消します。本章の Namespace を丸ごとやり直す場合だけ、次のように Namespace 自体を削除する選択肢もあります（配下の Pod・ConfigMap もまとめて消えますが、他章でこの Namespace を使い続けている場合は巻き込むので注意してください）。

```bash
k delete ns "$NAMESPACE"
```

削除後、Karpenter がノード側のスケールインをどう判断するかは各プールの `disruption` 設定に依存します。ワークロードが無くなった trn2 ノードが実際に `terminating` に遷移したかどうかは、`k get nodes` やコンソールの EC2 インスタンス一覧で確認してください。ノードが残り続けている場合は、Basic05 で扱った Capacity Block の期限アラートに加えて、手動でノードの状態を確認する運用を挟むと安全です。プール自体を使い終えた場合は、`accelerator-pools.auto.tfvars` から `trn2` プールのエントリを削除して `terraform apply` すれば、NodePool/EC2NodeClass も含めて片付きます。

# まとめ

本章では、Karpenter の `accelerator_pools` に Neuron（AWS Trainium/AWS Inferentia）を組み込み、trn2.48xlarge 2 台での EFA 越しマルチノード DDP まで実機で確認しました。骨格は NodePool/EC2NodeClass・Neuron AL2023 AMI・Neuron device plugin の 3 つで、`device_plugin = "neuron"` と書くだけで GPU と同じ枠組みに乗ります。単一ノードでのデバイス認識に加え、2 ノード・world_size=64 の all-reduce と公式 MNIST MLP の DDP がともに完走し、EFA が使われていることを libfabric が `efa-direct` ファブリックを開いたログとホストの RDMA 書き込みカウンタの両面から確認できました。

実運用上の注意点は次の 5 つです。

- ドライバとランタイムの zerocopy ABI 不一致（`nrt_init` が失敗したときのみ `neuronDdp.disableZerocopy=true` で暫定回避する）
- NPD/DRA は Karpenter との組み合わせで非サポートである
- EFA device plugin の toleration を崩さない
- 複数デバイス利用時は `neuron_enable_scheduler` を忘れない
- 検証後はチャート単位で Pod を削除し、プールも片付ける

# 参考資料

- [AWS Neuron ドキュメント](https://awsdocs-neuron.readthedocs-hosted.com/)
- [AWS Neuron Setup Guide（ランタイム/ドライバの互換性）](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/setup/index.html)
- [Neuron device plugin (neuron-helm-chart)](https://github.com/aws-neuron/neuron-helm-charts)
- [aws-neuron-samples（MNIST MLP DDP など）](https://github.com/aws-neuron/aws-neuron-samples)
- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
- [実験ワークロード chart（charts/experiments）](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments)
