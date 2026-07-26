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
3. **Neuron device plugin**（Helm add-on） — ドライバの上に乗る、Kubernetes に対して `aws.amazon.com/neuron` という resource を advertise する DaemonSet です。これだけを Helm で追加します。

`neuron-addons.tf` では、`neuron-helm-chart`（`oci://public.ecr.aws/neuron/neuron-helm-chart`）を `local.has_neuron_pool`（`accelerator_pools` の中に `device_plugin = "neuron"` のエントリが 1 つでもあるかどうか）で条件付き導入しています。GPU のみのクラスタではこのリソースは 0 個になり、何も入りません。

AMI の選び方には `ami_ssm_parameter` というフィールドを使います。`ami_alias`（`"al2023@latest"`）に任せると alias 解決の挙動に依存してしまうため、Neuron 用途では Amazon EKS Optimized AL2023 Neuron AMI の SSM パラメータパスを明示的に指定してピン留めするのが確実です。

もう 1 点、GPU 側の知識をそのまま持ち込むと誤解しやすいのが taint の管理主体です。Neuron device plugin もアクセラレータの taint を自分では付けません。GPU Operator が taint を付けないのと同じ理屈で、これは NodePool 側が明示的に `aws.amazon.com/neuron: NoSchedule` を宣言しています。

テンソル並列で複数の Neuron デバイスを跨ぐ推論・訓練を行う場合は、`neuron_enable_scheduler = true` で Neuron Scheduler Extension を有効にします。これは複数デバイスへの割り当てが contiguous な device ID になることを保証する拡張で、単一デバイスの推論のように 1 プロセスが 1 デバイスしか使わない場合は不要です。デフォルトは `false` です。

マルチノードで Neuron over EFA（trn2 同士を EFA で接続して collective 通信を行う構成）についても、配線自体はテーブルに組み込まれています。EFA device plugin のトレランス一覧には `aws.amazon.com/neuron` が含まれており、`locals.tf` の EFA トポロジテーブルには `trn2.48xlarge`（16 カード）や `trn1.32xlarge` などの Neuron インスタンスタイプも GPU インスタンスタイプと同じ形式で登録されています。本章の後半では、この配線を実際に trn2.48xlarge 2 台で動かし、EFA 越しの collective 通信が機能することを実機で確認します。

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

- `npd.enabled = false` — Node Problem Detector を明示的に無効化しています。Karpenter がノードのライフサイクルを完全に管理する構成では、NPD が想定するノード状態管理と競合するため、この構成では入れません。
- `scheduler.enabled = var.neuron_enable_scheduler` — 変数 1 つでそのまま Helm values に流し込んでいます。デフォルト `false` なので、明示的に `true` にしない限り Scheduler Extension は入りません。
- `devicePlugin.tolerations` — device plugin の DaemonSet 自身が Neuron taint（`aws.amazon.com/neuron`）と Capacity Block taint（`capacity-reservation`）を tolerate するよう明示しています。plugin 自身がこれらの taint を tolerate できなければ、advertise する対象の Neuron ノードにそもそも乗れません。

`depends_on = [helm_release.karpenter]` も見落としやすい点です。Neuron device plugin が意味を持つのは Karpenter が Neuron ノードを実際に起動した後なので、Karpenter コントローラより先に入れる理由はありません。依存関係を明示することで、apply 順序の事故（plugin が先に入り、対象ノードがまだ存在せず Pending の DaemonSet Pod が残る、といった混乱）を避けています。

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

Basic04 で作った `accelerator_pools` の型は GPU/Neuron 共通です。本章はその型に `device_plugin = "neuron"` のエントリを 1 つ追加し、`neuron-addons.tf` が条件付きで Neuron device plugin を導入するところから始めて、Basic05 で見た EFA を使ったマルチノード構成を Neuron 側でも実際に動かすところまでを扱います。GPU 側では NCCL がその役割を担っていましたが、Neuron では torch-neuronx（PyTorch/XLA バックエンド）と Neuron 用の collective 通信ライブラリがその役割を担います。

## 注意

**Karpenter + Neuron の NPD/DRA は非サポートです。** `neuron-helm-chart` の Node Problem Detector（NPD）と Dynamic Resource Allocation（DRA、Kubernetes 1.30+ のデバイス割り当て API）はこの構成では明示的に無効化しています。Karpenter がノードのライフサイクルを管理する構成では、Neuron の NPD/DRA は組み合わせとして未サポートのため、有効化しません。

**EFA device plugin の toleration には必ず `aws.amazon.com/neuron` を含めてください。** EFA device plugin は GPU と Neuron の両方のプールで共有される 1 つの DaemonSet であり、その toleration に Neuron taint が入っていなければ trn2 ノードには乗れません。乗れなければ `vpc.amazonaws.com/efa` がそのノードで一切 advertise されず、EFA を要求する Pod は理由の分かりにくい Pending のまま止まります。この構成ではすでに toleration に含めてありますが、自分で device plugin の設定を変更する場合はここを崩さないよう注意してください。

**Neuron Scheduler Extension を入れ忘れると多デバイス割り当てが崩れる可能性があります。** 複数の Neuron デバイスを跨ぐテンソル並列のワークロードを流すのに `neuron_enable_scheduler` を `false`（デフォルト）のままにすると、contiguous な device ID 割り当てが保証されません。単一デバイスの推論だけなら気にする必要はありませんが、複数デバイスをまとめて使う構成に切り替えるときは、このフラグを忘れずに `true` にしてください。

# ワークショップ実施

trn2.48xlarge を 2 台使い、単一ノードでのデバイス認識から 2 ノードの EFA 越し DDP までを順に確認します。ワークロードは Helm チャート `charts/experiments` で管理しており、`helm template ... | kubectl apply -f -` でレンダリングして適用します（`helm install` は使いません）。

## 1. terraform.tfvars に Neuron プールを追加する

`accelerator_pools` に、Capacity Block を使う trn2 のプールを追加します。

```hcl
trn2 = {
  instance_types    = ["trn2.48xlarge"]
  device_plugin     = "neuron"
  capacity_type     = "reserved"
  zone              = "<az>"
  cb_reservation_id = "<capacity-block-reservation-id>"
  cb_end_date       = "<RFC3339 UTC の期限>"
}

# 複数の Neuron デバイスを 1 プロセスから跨いで使うので Scheduler Extension を有効化
neuron_enable_scheduler = true
```

`device_plugin = "neuron"` に切り替えている以外、フィールドの構造は Basic04 で書いた GPU プールと同じです。`cb_reservation_id` は事前に確保した Capacity Block の予約 ID に置き換えます。プール名（map のキー）が Karpenter のノードラベル `node-role=<プール名>` になる点は後で使うので覚えておいてください。ここでは `trn2` としています。

## 2. apply する

```bash
cd infra/eks
terraform apply
```

`has_neuron_pool` が `true` になったことを検知して、Neuron device plugin と Scheduler Extension が導入されます。EFA device plugin もすでに `aws.amazon.com/neuron` taint を tolerate する設定で導入済みのため、追加の変更は不要です。Capacity Block が `active` になっていれば、この後 Pod を投入した時点で Karpenter が trn2.48xlarge を起動します。

## 3. 単一ノードで device plugin の advertise を確認する

まず 1 台の trn2 でデバイスが正しく見えるかを確認します。`charts/experiments` の `neuronProbe` ワークロードを使います。

```bash
export NAMESPACE=trn2-verify
kubectl create ns "$NAMESPACE"
helm template exp charts/experiments -n "$NAMESPACE" \
  --set neuronProbe.enabled=true --set neuronProbe.nodeRole=trn2 \
  | kubectl apply -f -
```

このプローブ Pod は `node-role=trn2` の nodeSelector で Neuron プールに固定され、`aws.amazon.com/neuron` taint への toleration を持ちます。最初の 1 回は Karpenter が trn2.48xlarge を起動するため数分かかります。Pod が `Running` になったら、内部で `neuron-ls` を実行してデバイスが列挙されるか確認します。

```bash
kubectl -n "$NAMESPACE" exec neuron-probe -- neuron-ls
```

trn2.48xlarge では 16 個の Trainium2 デバイスが表示されます。ノードの allocatable を見ると、device plugin が `aws.amazon.com/neuron: 16`、`aws.amazon.com/neuroncore: 64`、`vpc.amazonaws.com/efa: 15` を advertise していることが確認できます。この `neuroncore: 64` は、後述する LNC=2 のもとでの論理 NeuronCore 数です。EFA が 16 枚のカードに対して 15 なのは、この構成ではネットワークカード 0 をノードの IP を担う ENA として構成し、残る 15 枚を EFA 専用インターフェースとして立てているためです（EC2 の制約上、カード 0 のインターフェースは ENA を含む必要があります）。したがって Pod がスケジュール要求に使える EFA は 15 枚になります。実際に要求できる枚数はインスタンスタイプとノードの起動時構成で決まるので、`terraform output accelerator_pool_efa_schedulable` の値か、ノードの allocatable を直接確認するのが確実です。

:::message
Karpenter は hugepages を「新しいノードをどのインスタンスタイプで立てるか」の判断材料として扱いません。そのため、まだ対象ノードが存在せずプロビジョニングを誘発するプローブ Pod で `hugepages-2Mi` を要求すると、Karpenter が「条件を満たすインスタンスタイプがない」と誤判定して NodeClaim を作らず、Pod が永久に Pending になります。プローブでは hugepages を要求しないのはこのためです。一方、次の 2 ノード DDP は起動済みのノードに対して適用するので hugepages を要求できます。
:::

## 4. 2 ノードの EFA 越し DDP を動かす

ここが本章の主目的です。trn2.48xlarge 2 台に torch-neuronx の分散データ並列（DDP）を流し、EFA 越しに collective 通信（all-reduce）が成立することを確認します。`charts/experiments` の `neuronDdp` ワークロードは、ConfigMap（all-reduce テストスクリプト）と 2 台のノードにそれぞれ載る Pod（`neuron-server` / `neuron-client`）をレンダリングします。2 台は podAntiAffinity で必ず別ノードに配置されるので、通信は確実にノード間の EFA を通ります。

```bash
helm template exp charts/experiments -n "$NAMESPACE" \
  --set neuronDdp.enabled=true --set neuronDdp.nodeRole=trn2 \
  | kubectl apply -f -
```

2 台目の trn2 が起動して両 Pod が `Running` になったら、Pod 間で SSH 鍵を配ります（両 Pod は hostNetwork なので Pod IP はノード IP と一致します）。

```bash
NS=trn2-verify
kubectl -n $NS exec neuron-server -- bash -lc \
  '[ -f /root/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519 -q; \
   cp /root/.ssh/id_ed25519.pub /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys'
PRIV=$(kubectl -n $NS exec neuron-server -- bash -lc 'base64 -w0 < /root/.ssh/id_ed25519')
PUB=$(kubectl -n $NS exec neuron-server -- bash -lc 'cat /root/.ssh/id_ed25519.pub')
kubectl -n $NS exec neuron-client -- bash -lc \
  "echo '$PRIV' | base64 -d > /root/.ssh/id_ed25519 && chmod 600 /root/.ssh/id_ed25519; \
   echo '$PUB' > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys; \
   chown -R root:root /root/.ssh"
```

`chown -R root:root /root/.ssh` を忘れると、sshd の StrictModes が所有者不一致を理由に鍵を拒否するので必ず入れてください。

Pod の IP を確認し、両ノードで `torchrun` を起動します。1 ノードあたりの並列数（`--nproc-per-node`）は 32 で、2 ノードで world_size は 64 になります。

ここで並列数の数え方を整理しておきます。trn2.48xlarge は 16 個の Trainium2 デバイスを積んでおり、Trainium2 は 1 デバイスあたり 8 個の物理 NeuronCore を持つため、物理 NeuronCore は合計 128 個です。この構成は LNC（logical NeuronCore config）=2 で動いており、2 つの物理コアが 1 論理コアに束ねられます。その結果、1 ノードあたりの論理 NeuronCore は 64 個になります。これは手順 3 で device plugin が advertise していた `aws.amazon.com/neuroncore: 64` と一致します。この検証では 1 ノードあたり 32 プロセス（`--nproc-per-node=32`）で起動しており、論理コアをすべて使い切る構成（`--nproc-per-node=64`）ではなく、疎通と DDP の成立を確認するためのスモーク構成である点に注意してください。プロセス数を変える場合は、この後のワークロードの `nprocPerNode` の値も合わせて調整します。

```bash
kubectl -n $NS get pods -o wide   # server / client の IP を確認
SERVER_IP=<neuron-server のノード IP>

# client（node-rank=1）を先に起動
kubectl -n $NS exec neuron-client -- bash -lc "cd /opt/test && \
  NEURON_RT_DBG_ZEROCOPY=0 FI_PROVIDER=efa FI_EFA_USE_DEVICE_RDMA=1 FI_EFA_FORK_SAFE=1 \
  torchrun --nnodes=2 --node-rank=1 --nproc-per-node=32 \
    --master-addr=$SERVER_IP --master-port=29500 allreduce_test.py" &

# server（node-rank=0）
kubectl -n $NS exec neuron-server -- bash -lc "cd /opt/test && \
  NEURON_RT_DBG_ZEROCOPY=0 FI_PROVIDER=efa FI_EFA_USE_DEVICE_RDMA=1 FI_EFA_FORK_SAFE=1 \
  torchrun --nnodes=2 --node-rank=0 --nproc-per-node=32 \
    --master-addr=$SERVER_IP --master-port=29500 allreduce_test.py"
```

初回は NEFF（Neuron 実行ファイル）のコンパイルが走るため時間がかかります。完了すると rank 0 が次のように出力します。

```
[rank 0] step 0: result=64.0 (expected 64)
...
[rank 0] ALL 20 STEPS OK. world_size=64 elapsed=20.09s
[rank 0] DONE - SUCCESS
```

各ステップの all-reduce 結果が world_size（=64）と一致しており、2 台のノードにまたがる 64 プロセス全員が collective に参加できたことを意味します。より大きなテンソル（1 ステップあたり約 67MB）で 30 ステップ流しても全ステップで結果が一致し続けました。

## 5. EFA が実際に使われていることを確認する

「2 ノードで通信が成立した」だけでは、その通信が EFA を通ったのか TCP にフォールバックしたのか分かりません。EFA が使われている証拠を 2 つの角度から確認します。

1 つ目は libfabric のログです。`FI_LOG_LEVEL=info` を付けて実行すると、次のようなログが現れます。

```
libfabric:...:core:core:ofi_register_provider():526<info> registering provider: efa (204.0)
libfabric:...:core:core:fi_fabric_():1588<info> Opened fabric: efa-direct
```

ここで注意したいのは、`registering provider: efa` の行だけでは EFA が使われた証拠にならないことです。この `registering provider:` 行は libfabric がビルドに含む全プロバイダについて初期化時に出力するもので、実際のログには `efa` に加えて `lnx` や `off_coll` などの行も並びます。TCP にフォールバックした場合でも `registering provider: efa` 自体は出力されます。実際に EFA のファブリックが開かれた証拠になるのは `Opened fabric: efa-direct` の行の方です。全 64 プロセスがこの行を出力しており、`efa-direct`（libfabric がデバイス機能を直接公開するファブリック）越しに通信が確立したことを裏付けています。

2 つ目はホスト側の EFA NIC の RDMA 書き込みカウンタです。実行の前後で `/sys/class/infiniband/rdmap*/ports/1/hw_counters/rdma_write_bytes` を読むと、両ノードでほぼ対称に 0 から 2.5 億バイト超まで増加します。片方向でなく双方向に RDMA write が成立していること、そして複数の EFA NIC で同時に増えていて単一 NIC がボトルネックになっていないことも確認できます。

さらに、公式サンプル（aws-neuron-samples）の MNIST MLP を DDP で流す実践的なワークロードも 2 ノード（各 16 ワーカー、計 32 ワーカー）で完走し、両ノードの全ワーカーが最終 loss を算出しました。単なる collective 疎通だけでなく、実際のモデル訓練が 2 ノードで回ることまで確認できています。

## 注意

**ドライバとランタイムの `zerocopy` ABI 不一致で `nrt_init` が失敗することがあります。** 上の起動コマンドに付けた `NEURON_RT_DBG_ZEROCOPY=0` は、今回の検証で踏んだハマりどころへの対処です。何も付けずに実行すると、Neuron ランタイムの初期化が次のエラーで失敗しました。

```
NRT:nrt_init  Failed to initialize devices, error:1
TDRV:ucode_ll_create  ucode_lib_ll_create failed, error: 6
TDRV:dmem_buf_copyin  Copy from buffer to memory failed
```

一見 hugepages 不足に見えますが、真因は別でした。`strace` で追うと、`NEURON_IOCTL_MEM_BUF_ZEROCOPY64`（ioctl 番号 0x7c）が `EINVAL` で失敗しており、これが上のエラー連鎖の起点でした。原因は、ホストのカーネルドライバ（`aws-neuronx-dkms` 2.29.0.0）と、コンテナ内の Neuron ランタイム（2.32.31.0、SDK 2.30 の DLC）との間で、この zerocopy ioctl が期待する構造体サイズが食い違っていたことです。ドライバのソース（`aws-neuron-driver` の `neuron_cdev.c`）には `_IOC_SIZE(cmd) != sizeof(arg)` なら `EINVAL` を返す厳格な ABI チェックがあり、バージョン差でこれに引っかかっていました。

恒久的な対処は、ノードの AMI が持つドライバのバージョンを、使用する DLC の Neuron SDK に揃えることです。今回のように手元の DLC タグとノード AMI のドライバがずれている場合の回避策として、`NEURON_RT_DBG_ZEROCOPY=0` で zerocopy 経路をバイパスすると `nrt_init` が通ります。これは最適化を 1 つ無効化する設定なので、あくまでバージョン差を解消するまでの暫定策として使ってください。

**Karpenter + Neuron の NPD/DRA は非サポートです。** 前掲のとおり、`neuron-helm-chart` の NPD/DRA はこの構成では無効化しています。

**Capacity Block の期限に注意してください。** trn2 のような高性能インスタンスは Capacity Block で確保することが多く、期限が来るとインスタンスは自動的に回収されます（実際、今回の検証でも期限の少し前にインスタンスが `shutting-down` に入りました）。長時間の実験は期限に余裕を持って始め、`cb_end_date` を設定して期限前アラート（Basic06 参照）を受け取れるようにしておくと安全です。

# まとめ

本章では、Karpenter の `accelerator_pools` に Neuron（AWS Trainium/AWS Inferentia）を組み込み、trn2.48xlarge 2 台での EFA 越しマルチノード DDP まで実機で確認しました。骨格は NodePool/EC2NodeClass・Neuron AL2023 AMI・Neuron device plugin の 3 つで、`device_plugin = "neuron"` と書くだけで GPU と同じ枠組みに乗ります。単一ノードでのデバイス認識に加え、2 ノード・world_size=64 の all-reduce と公式 MNIST MLP の DDP がともに完走し、EFA が使われていることを libfabric が `efa-direct` ファブリックを開いたログとホストの RDMA 書き込みカウンタの両面から確認できました。実運用上の注意点は、ドライバとランタイムの zerocopy ABI 不一致（`NEURON_RT_DBG_ZEROCOPY=0` で暫定回避）、NPD/DRA が Karpenter と非サポートである点、EFA device plugin の toleration を崩さない点、複数デバイス利用時は `neuron_enable_scheduler` を忘れない点の 4 つです。

# 参考資料

- [AWS Neuron ドキュメント](https://awsdocs-neuron.readthedocs-hosted.com/)
- [Neuron device plugin (neuron-helm-chart)](https://github.com/aws-neuron/neuron-helm-charts)
- [aws-neuron-samples（MNIST MLP DDP など）](https://github.com/aws-neuron/aws-neuron-samples)
- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
- [実験ワークロード chart（charts/experiments）](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments)
