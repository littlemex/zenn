---
title: "Basic06 - EFA でマルチノード通信を検証する"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、Karpenter が起動するノードで EFA が正しく構成され、実際にノード間で帯域が出ていることまでを確認します。Basic05 で確保した Capacity Block のノード 2 台で NCCL の帯域を測ります。

:::message
本章の帯域測定は、EFA を複数枚持つインスタンスが 2 台以上、しかも同一 AZ に必要です。Basic05 で Capacity Block を確保していることを前提にしています。まだ確保していない場合でも、解説部分と、ノードを必要としない手順 2（`terraform output` による schedulable EFA 数の確認）・手順 3（環境変数の書き方）までは先に読み進められます。手順 3 以降は Basic05 で確保した Capacity Block が必要です。
:::

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち Karpenter が起動するアクセラレータノード同士を結ぶ EFA ネットワークの部分です。ノード設定は前章までで完了しているため、ここでは「起動したノードの EFA が正しく使えているか」を確認します。

## これは何をするものか

### EFA とは

EFA（Elastic Fabric Adapter）は AWS の高帯域・低遅延ネットワークインターフェースで、OS-bypass による SRD（Scalable Reliable Datagram）プロトコルを使います。GPU/Neuron のマルチノード集合通信（NCCL）で必要な帯域を確保するために不可欠な存在です。

EFA は通常の ENI としての IP 通信も担いますが、それに加えて libfabric 経由の OS バイパス経路を持ち、そこではカーネルを経由せずにユーザ空間から直接データを送受信します。これにより低遅延・高スループットを実現しますが、ネットワーク上のトラフィック特性が通常の IP と異なるため、セキュリティグループの設定にも独自の要件があります。

![EFA によるマルチノード NCCL 通信](/images/books/eks-distributed-ai/arch-efa-detail.png)

### なぜ Karpenter は EFA を自動で付けないか

Karpenter（karpenter-provider-aws v1.11 以降）の EC2NodeClass は、`spec.networkInterfaces` を省略すると単一のデフォルト ENA（IP 通信用）だけを作ります。EFA を使うにはこのフィールドで以下を明示宣言する必要があります（詳細は [Karpenter NodeClasses ドキュメントの spec.networkInterfaces](https://karpenter.sh/docs/concepts/nodeclasses/#specnetworkinterfaces) を参照してください）。

- カード 0: `interfaceType: "interface"`（ノード IP 用、primary ENI）
- カード 1〜N: `interfaceType: "efa-only"`（RDMA 専用、IP を持たない）

この宣言はインスタンスタイプごとにカード枚数とレイアウトが異なるため、プールごとに手書きするとカード枚数を 1 つ間違えるだけで事故になります。以降では、この宣言を自動生成している実コードを引用しながら、設計意図を見ていきます。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) です。

## EFA トポロジを EC2 API から動的に取得する

EFA のカード枚数はインスタンスタイプごとに物理的に決まっていますが、命名規則からは導出できません。同じ g6e ファミリでも g6e.4xlarge 以下は EFA 非対応で g6e.8xlarge 以上は EFA 対応、同じ p5 系でも p5 は 32 カード・p5en は 16 カードというように、境界も枚数も型ごとに異なります。この境界はドキュメント改訂で変わり得るため、手元の型で確認したい場合は次のコマンドを実行してください。`--region` には、そのインスタンスタイプが提供されているリージョンを指定します。提供されていないリージョンを指定すると結果が空になるため、ここでは p5en を提供している `us-west-2` を例にしています。

```bash
export INSTANCE_TYPE=p5en.48xlarge
aws ec2 describe-instance-types --instance-types $INSTANCE_TYPE \
  --query 'InstanceTypes[0].NetworkInfo.{EFA:EfaSupported,MaxEfa:EfaInfo.MaximumEfaInterfaces}' --region us-west-2
```

```
{
    "EFA": true,
    "MaxEfa": 16
}
```

この知識を静的テーブルとしてコードに埋め込むと、新しい世代（g8e など）が出るたびに手で追記が必要になり、追記を忘れた型はビルドが落ちるか、あるいは黙って EFA が無効化されるという負債になります。

そこでこのモジュールでは、[`karpenter-resources.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/karpenter-resources.tf) で `data.aws_ec2_instance_type` を使い、pool の `instance_types` の EFA 情報を plan 時に EC2 の DescribeInstanceTypes API から取得します。

```hcl
# karpenter-resources.tf
data "aws_ec2_instance_type" "pool_rep" {
  for_each      = toset(flatten([for p in var.accelerator_pools : p.instance_types]))
  instance_type = each.value
}
```

取得できる主な属性は `efa_supported`（EFA 対応可否）と `efa_maximum_interfaces`（EFA を張れる最大インターフェース数）です。実データは次の通りで、新旧の型で一貫して取得できます。

| インスタンスタイプ | efa_supported | efa_maximum_interfaces |
|---|---|---|
| p5.48xlarge | true | 32 |
| p5en.48xlarge | true | 16 |
| g6e.48xlarge | true | 4 |
| g6e.12xlarge | true | 1 |
| g5.xlarge / g6.xlarge | false | (なし) |

同じ g6e ファミリでも、g6e.48xlarge は `efa_maximum_interfaces = 4` のため後述の `multi_card` 判定が true になり multi-card 構成として解決されます。一方、本章の以降の例で使う g6e.12xlarge は 1 枚だけの single-card 構成です。「g6e = single-card」という単純化は本章の検証で使う g6e.12xlarge を指しており、g6e ファミリ全体に当てはまるわけではない点に注意してください。

[`locals.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/locals.tf) の `pool_efa` が EFA トポロジを解決します。pool 側で `efa_interface_count` を明示した場合はそれを優先し、未指定（既定値 `-1`）なら API 値を使います。

multi-card レイアウトでは**カード 0 がノード IP を運ぶため EFA-only として広告されません**。つまり以下のようになります。

- p5en.48xlarge: 16 カード → schedulable EFA = **15**
- p5.48xlarge: 32 カード → schedulable EFA = **31**
- g6e.12xlarge: 1 カード（single-card）→ schedulable EFA = **1**

Pod が `vpc.amazonaws.com/efa: 16` をリクエストすると、15 しか広告されないため永久に Pending になります。このモジュールでは `terraform output accelerator_pool_efa_schedulable` でプールごとの正しい値を公開しています。

## networkInterfaces の自動生成

`pool_efa` で解決したトポロジを、Karpenter の EC2NodeClass が要求する `spec.networkInterfaces` の配列に変換するのが [`karpenter-resources.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/karpenter-resources.tf) の `pool_network_interfaces` です。

## EFA セキュリティグループには egress self-ref が必須

これがこのモジュールで最も重要です。[`sg.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/sg.tf) で定義しています。

EFA の SRD トラフィックは**通常の IP トラフィックではありません**。そのため、SG の egress ルールに `0.0.0.0/0` の CIDR を設定しても、SRD パケットは許可されません。必要なのは ingress と egress の**両方に self-referencing all-traffic ルール**を持つことです。

```hcl
# sg.tf（抜粋、description フィールドは省略）

# Ingress: peer EFA ノードからの全トラフィック（self-referencing）
resource "aws_security_group_rule" "efa_node_ingress_self" {
  security_group_id        = aws_security_group.efa_node.id
  type                     = "ingress"
  protocol                 = "-1"
  from_port                = 0
  to_port                  = 0
  source_security_group_id = aws_security_group.efa_node.id
}

# Egress: 通常の IP トラフィック（Amazon S3, Amazon ECR 等）
resource "aws_security_group_rule" "efa_node_egress_all" {
  security_group_id = aws_security_group.efa_node.id
  type               = "egress"
  protocol           = "-1"
  from_port          = 0
  to_port            = 0
  cidr_blocks        = ["0.0.0.0/0"]
}

# Egress: EFA SRD トラフィック（self-referencing、CIDR では通らない）
resource "aws_security_group_rule" "efa_node_egress_self" {
  security_group_id        = aws_security_group.efa_node.id
  type                     = "egress"
  protocol                 = "-1"
  from_port                = 0
  to_port                  = 0
  source_security_group_id = aws_security_group.efa_node.id
}
```

egress self-ref が無い場合の症状として、NCCL は bootstrap（TCP）に成功し `Selected provider is efa` と表示するものの、実際のデータ転送で `NET/OFI ... Error 15 (Unreachable remote)` が出てハングします。「EFA を選んだはずなのにデータが流れない」という診断困難な状況になります。

## EFA device plugin の supportedInstanceLabels 自動導出

EFA を Pod にリソースとして見せるのは `aws-efa-k8s-device-plugin` の DaemonSet です。この chart は `supportedInstanceLabels` に列挙されたインスタンスタイプにしか nodeAffinity でスケジュールされず、chart デフォルトの一覧には g6e.12xlarge のような一部の EFA 対応タイプが含まれていません。デフォルトのままだとそのタイプのノードにはプラグインが乗らず、`vpc.amazonaws.com/efa` が永久に広告されないという問題が起こり得ます。[`gpu-addons.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/gpu-addons.tf) はこれをクラスタが実際に使う pool から動的に導出することで防いでいます。

## EFA 関連の環境変数

その前に、この変数が何をするものかを押さえておきます。NCCL はマルチノード実行で 2 種類の通信を使い分けます。1 つはデータ本体の集合通信で、これは EFA/RDMA（`FI_PROVIDER=efa` で選ばれる経路）を通ります。もう 1 つは実行開始時に rank どうしが顔合わせをする bootstrap（ランデブー）で、こちらは通常の TCP/IP ソケットを使います。`NCCL_SOCKET_IFNAME` は後者の bootstrap にどのネットワークインターフェースを使うかを NCCL に指示する変数です。EFA を使う構成でも、この TCP 側のインターフェース選択を誤ると rank どうしが顔合わせできず、データ通信を始める前に止まってしまいます。だから「EFA を使うのに、なぜ TCP インターフェースの指定が要るのか」という話になります。

次の手順で投入する測定ワークロードは、環境変数に以下を渡します。自分でワークロードを書く場合も同じ形にします。

```yaml
env:
  - name: NCCL_SOCKET_IFNAME
    value: "^lo,docker,veth"
  - name: FI_PROVIDER
    value: "efa"
  - name: FI_EFA_USE_DEVICE_RDMA
    value: "1"
  - name: FI_EFA_FORK_SAFE
    value: "1"
  - name: NCCL_DEBUG
    value: "INFO"
  - name: NCCL_DEBUG_SUBSYS
    value: "INIT,NET"
```

後半の 4 つは測定のための設定です。`FI_EFA_USE_DEVICE_RDMA` は EFA デバイスの RDMA 経路を使わせ、`FI_EFA_FORK_SAFE` はプロセスが fork したときに libfabric が登録済みメモリを壊さないようにします。`NCCL_DEBUG` を `INFO` にしているのは、EFA が実際に選ばれた証拠となる `NET/OFI Selected provider is efa` の行がこのレベルでしか出ないためで、`NCCL_DEBUG_SUBSYS` でその判断に必要な範囲までログ量を絞っています。

要点は `NCCL_SOCKET_IFNAME` を `^` で始まる除外パターンで書くことです。`efa0,efa1,...` のような許可リスト方式で名指しすると bootstrap に失敗します。NCCL は起動時の rank 間ランデブーを TCP ソケットで行いますが、`efa-only` インターフェースは IP を持たないため、これらを名指しすると bootstrap 用の到達可能なインターフェースが見つからず接続できません。除外パターンで `lo` やコンテナ仮想 NIC（`docker`／`veth`）を外し、ノードの IP を持つインターフェースを NCCL に選ばせるのが正しい書き方です。

この値は本書のチャートでは 1 か所、[`values.yaml`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/values.yaml) の `ncclSocketIfname` にデフォルトとして持たせています。本章で使う TrainJob の [`nccl-trainjob.yaml`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/nccl-trainjob.yaml)、単ノードの sanity 用 [`nccl-probe.yaml`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/nccl-probe.yaml)、`mpirun` 方式の [`nccl-sshd.yaml`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/nccl-sshd.yaml) の 3 つが、いずれもそこから `NCCL_SOCKET_IFNAME` としてコンテナに渡します。特定のワークロードだけ別のパターンにしたい場合は `--set ncclProbe.socketIfname=...` のように個別に上書きできます。自分でワークロードを書く場合も、Pod の `env` にこの 1 行を同じ形で入れます。

なお、AWS の [awsome-distributed-ai リポジトリの EFA Cheatsheet](https://github.com/awslabs/awsome-distributed-ai/blob/main/1.architectures/efa-cheatsheet.md) に、`NCCL_SOCKET_IFNAME` を含む EFA/NCCL 環境変数の推奨値がまとまっています。バージョンごとの推奨が変わるので、あわせて参照すると良いでしょう。

# ワークショップ実施

本章の実機検証は p4d.24xlarge（NVIDIA A100 40GB x8、EFA x4）2 台の Capacity Block で実施しました。以降の出力はこの構成の実測値です。EFA の枚数はインスタンスファミリごとに違うので、読者の環境では数値が変わります。だからこそ枚数を決め打ちせず、次の手順のように必ず AWS 側の値を参照してください。

## 1. 前提を確認する

- Basic05 で EFA 対応インスタンスの Capacity Block を確保済み。
- リポジトリ同梱の NCCL 測定用 TrainJob チャート（`infra/eks/charts/experiments` の `ncclTrainjob`）
- Capacity Block のノードは予約の AZ に立つので、共有ストレージ (単一 AZ の FSx for OpenZFS) と別の AZ になることがあります。NFS は AZ を跨いでもマウントできるため本手順は動きますが、`/shared` への読み書きに AZ 間のデータ転送料金と余分なレイテンシがかかります。本章が `/shared` に置くのは数 KB の測定スクリプトだけなので測定結果には影響しません
- Basic02 で作った共有 PVC `shared-claim` が対象 namespace にあること (`ncclTrainjob` は `/shared` をマウントします。チャートが検査するのは PVC 名を渡したかどうかだけなので、PVC が実在しなくてもレンダリングと `kubectl apply` は通り、Pod が `Pending` のまま止まります。`k get pvc -n $NAMESPACE shared-claim` で `Bound` を先に確かめてください)
- `k` と `KUBECONFIG` は Basic01 step 2 の 4 行で設定済み

EFA 関連のアドオン（EC2NodeClass の `networkInterfaces` 自動生成、EFA 用セキュリティグループ、`aws-efa-k8s-device-plugin`）は、EFA 対応プールが 1 つ以上あることを条件に前章までの `terraform apply` で導入済みです。本章はそれらが正しく効いているかを確認する章なので、新しくインフラを足す操作はありません。

## 2. Schedulable EFA の値を確認する

この手順は **ノードを 1 台も起動せずに実行できます**。EFA のトポロジは EC2 の API から plan 時に取得しているので、プールを定義して `terraform apply` した時点で答えが出ています。`terraform output` は Terraform モジュールのディレクトリで実行します。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
terraform output accelerator_pool_efa_schedulable
```

Basic04 の `gpu-ddp` プールだけを定義した状態での出力:

```text
{
  "gpu-ddp" = 0
}
```

0 になるのは、`gpu-ddp` が並べている g6.2xlarge / g5.2xlarge が EFA 非対応だからです。これは推測ではなく EC2 API が返す事実で、次のコマンドで直接確認できます（`$AWS_REGION` は Basic01 step 2 の 4 行で解決済みのクラスタのリージョンです。インスタンスタイプの EFA 情報自体はリージョンによらずほぼ同じですが、クラスタと同じリージョンを指定しておくと以降の手順と揃います）。

```bash
aws ec2 describe-instance-types --instance-types g6.2xlarge g5.2xlarge g6e.12xlarge \
  --query 'InstanceTypes[].{Type:InstanceType,EFA:NetworkInfo.EfaSupported,MaxEfa:NetworkInfo.EfaInfo.MaximumEfaInterfaces}' \
  --output table --region "$AWS_REGION"
```

```text
------------------------------------------
|          DescribeInstanceTypes         |
+--------+----------+------------------+
|  EFA   |  MaxEfa  |      Type        |
+--------+----------+------------------+
|  False |  None    |  g6.2xlarge      |
|  False |  None    |  g5.2xlarge      |
|  True  |  1       |  g6e.12xlarge    |
+--------+----------+------------------+
```

## 3. 測定ワークロードを投入してノードを起動する

予約した複数ノードが実際に EFA/RDMA で通信できているかを測ります。

Basic05 の apply で作られたのは NodePool と EC2NodeClass の定義だけで、この時点ではまだノードは 1 台も立っていません。Karpenter は Pod の要求を見て初めてノードを起動します。

そこで測定ワークロード（`ncclTrainjob`）をそのまま投入します。Karpenter がこの Pod の要求を見て 2 台を起動し、TrainJob がそのノードで `all_reduce` を回します。ノードを別途 warmup Pod で起こす必要はありません。EFA の枚数はインスタンスタイプごとに違うため、コマンドに直接書かず手順 2 の `terraform output` から取ります。GPU 数も同様に AWS から読みます。以下のブロックはリポジトリのルートで実行します。

```bash
export NAMESPACE=distai
k create namespace "$NAMESPACE" --dry-run=client -o yaml | k apply -f -

POOL=gpu-p4d
ITYPE=p4d.24xlarge
GPU=$(aws ec2 describe-instance-types --instance-types "$ITYPE" \
  --query 'InstanceTypes[0].GpuInfo.Gpus[0].Count' --output text --region "$AWS_REGION")

cd "$(git rev-parse --show-toplevel)"/infra/eks
EFA=$(terraform output -json accelerator_pool_efa_schedulable | jq -r ".\"$POOL\"")
echo "gpu=$GPU efa=$EFA"

helm template exp charts/experiments -n "$NAMESPACE" \
  --set namespace="$NAMESPACE" \
  --set ncclTrainjob.enabled=true \
  --set ncclTrainjob.nodeRole=$POOL \
  --set ncclTrainjob.gpuCount=$GPU \
  --set ncclTrainjob.efaCount=$EFA \
  --set ncclTrainjob.image=763104351884.dkr.ecr.$AWS_REGION.amazonaws.com/pytorch-training:2.10.0-gpu-py313-cu130-ubuntu22.04-ec2-v1.11 \
  --set sharedStorage.existingClaimName=shared-claim \
  | k apply -f -
```

投入後は Karpenter が 2 台を起動し、DLC イメージ（十数 GB 級）の pull に初回 10 分前後かかるため、しばらく Pod は `ContainerCreating` に留まります。これはノード起動失敗ではないので、`k get pod` の理由が `ContainerCreating` のうちは pull の完了を待ちます。起動したノードは Basic05 の `consolidateAfter: Never`（予約プールは自動で `protect` に解決）で保たれるため、TrainJob が終わっても手順 4・5 で参照できます。

:::details hugepages を要求する方式（`ncclSshd` / Neuron）を使う場合の注意
`ncclTrainjob`（torchrun）は hugepages を要求しないので、上のようにそのまま投入すればノードが起動します。しかし `mpirun` 方式の `ncclSshd` や Neuron 側のプローブは hugepages を要求し、これは事情が異なります。

hugepages は Linux が通常の 4 KB より大きい単位（2 MB など）で確保するメモリページで、RDMA のようにメモリ領域を固定して DMA する用途で使われます。Pod は `hugepages-2Mi` リソースとして要求し、ノード側が起動時に予約しておく必要があります。ところが Karpenter は hugepages を「どのインスタンスタイプなら足りるか」の判断に使わないため、hugepages を要求する Pod で新規ノードの起動を誘発しようとすると、`no instance type has enough resources` と判定されて NodeClaim が作られず、Pod は永久に `Pending` になります。

これらの方式を使うときは、先に hugepages を要求しない GPU Pod（`sleep` するだけの使い捨て Pod で GPU を全数要求）で 2 台のノードを起こし、`Ready` を確認してからその Pod を削除し、そのうえで hugepages を要求する測定ワークロードを載せます。ノードは上記の `protect` preset で残るので、踏み台 Pod を消してもノードは回収されません。`ncclTrainjob` だけを使う本章の手順では、この段取りは不要です。
:::

`ncclTrainjob` は Basic02 と同じ Kubeflow Trainer v2 の TrainJob で、`torch.distributed` の `all_reduce` を 2 ノードにまたがって回します。ノードをまたぐ起動の段取り、つまり「どのプロセスが rank いくつで、どこに集合するか」は TrainJob が担います。Trainer が `PET_NNODES` / `PET_NPROC_PER_NODE` / `PET_NODE_RANK` / `PET_MASTER_ADDR` を各 Pod に注入し、`torchrun` がそれを既定値として読むという仕組みです。

指定の要点は 3 つあります。

第一に、`nodeRole` にはプール名を渡します。ノードの GPU SKU を表す `nvidia.com/gpu.product` で選びたくなりますが、このラベルは GPU Operator が起動済みのノードに後から付与するものなので、Karpenter が「どのインスタンスタイプを起動するか」を判断する材料になりません。これを nodeSelector に使うと Karpenter は次のように要求を拒否し、2 台目のノードが永久に起動しません。

```text
Failed to schedule pod, incompatible requirements,
label "nvidia.com/gpu.product" does not have known values
```

Karpenter が起動時に付ける `node-role=<プール名>` を使えば、ノードがまだ存在しない状態からプロビジョニングを誘発できます。

第二に、`gpuCount` と `efaCount` は上のように AWS 側から読んだ値を渡します。EFA の schedulable 数はファミリごとに違うため、固定値を書くと別のファミリでは必ず Pod が Pending になります。`gpuCount` は 1 ノードあたりのプロセス数にもなるので、8 GPU のノードなら 8 プロセスが 1 GPU ずつ担当します。

第三に、イメージは `torchrun` と EFA 用の `aws-ofi-nccl` プラグインの**両方**を持つものを選びます。ここが最も間違いやすい箇所です。

:::message alert
本書に出てくる他の 2 つのイメージは、どちらもこの用途には使えません。

`nccl-tests` のイメージは Open MPI 前提で `torchrun` を持たないため、Pod が即座に `exec: "torchrun": executable file not found in $PATH` で落ちます。これは失敗が明示されるので気づけます。

危険なのは Basic02 でビルドした `ddp-sample` のイメージです。`torchrun` は持ちますが素の PyTorch イメージなので EFA プラグインがありません。この場合 NCCL はエラーを出さず、自前の TCP ソケット通信に**黙って切り替えます**。ベンチマークは完走し、それらしい数値も出るので、EFA を測ったつもりで実際には TCP を測っていたという結果になります。帯域の数値だけでは区別できないため、手順 6 で説明するログ行の確認が必須です。

`torchrun` と EFA プラグインの両方を持つイメージとして、AWS Deep Learning Containers があります。利用可能なタグは次のように調べられます。

```bash
aws ecr describe-images --region "$AWS_REGION" --registry-id 763104351884 --repository-name pytorch-training \
  --query 'sort_by(imageDetails,&imagePushedAt)[-20:].imageTags[]' --output text | tr '\t' '\n'
```

:::

## 4. ノード上の EFA リソースを確認する

手順 3 でノードが起動したら、手順 2 の値が実際にノードへ反映されているかを確認します。

なお手順 3 のレンダリングは、測定用の TrainJob だけでなく `nccl-trainjob-stage` という Job も作ります。測定スクリプト `bench.py` は ConfigMap として渡されますが、TrainJob の Runtime がマウントするのは `/shared` だけで ConfigMap ではないため、この Job が CPU プール上で ConfigMap の内容を `/shared/nccl-bench/bench.py` にコピーします。TrainJob はそのパスを `torchrun` に渡すので、Job が `Completed` になっていることが測定の前提です。`k get pods -n $NAMESPACE` に busybox の Pod が現れるのはこのためで、測定 Pod が「スクリプトが無い」で落ちる場合はまずこの Job の状態を見てください。ノードが `Ready` になっていれば、測定 Pod の `Running` を待たずにこの allocatable 確認を実行できます（測定 Pod は DLC イメージの pull で `ContainerCreating` に留まっていることがありますが、ノードの EFA allocatable はその前から確認できます）。

`POOL` は Basic05 で `accelerator-pools.auto.tfvars` に貼り付けたプール名に置き換えます。

```bash
POOL=gpu-p4d
k get nodes -l node-role=$POOL \
  -o jsonpath="{range .items[*]}{.metadata.name}{'\t'}{.status.allocatable['vpc\.amazonaws\.com/efa']}{'\n'}{end}"
```

p4d.24xlarge 2 台での実機出力:

```text
ip-10-0-a-b.us-west-2.compute.internal	3
ip-10-0-c-d.us-west-2.compute.internal	3
```

`terraform output` が示した 3 と、ノードが実際に広告している 3 が一致しました。物理カード 4 枚に対して 3 になるのは、この構成が card 0 を IP を持つ通常の ENI に割り当て、残り 3 枚を EFA-only にしているためです。card 0 のハードウェアが EFA に使えないという意味ではなく、`networkInterfaces` の宣言の帰結です。

`k describe node` でも同じ数字が Capacity と Allocatable の両方に現れます。ただしスクリプトから読む場合は上の `.status.allocatable` を直接引く形のほうが確実です。

```bash
k describe node <node-name> | grep "vpc.amazonaws.com/efa"
```

```text
  vpc.amazonaws.com/efa:  3
  vpc.amazonaws.com/efa:  3
```

## 5. EFA device plugin の稼働を確認する

```bash
k get pods -n kube-system -l name=aws-efa-k8s-device-plugin
```

実機出力（EFA 対応ノード 2 台）:

```text
NAME                              READY   STATUS    RESTARTS   AGE
aws-efa-k8s-device-plugin-d4vvd   1/1     Running   0          114m
aws-efa-k8s-device-plugin-pjb26   1/1     Running   0          124m
```

セレクタが `name=` であって `app.kubernetes.io/name=` ではない点に注意してください。この DaemonSet が付けているラベルは `name` の方だけなので、`app.kubernetes.io/name` で絞ると 1 件も返らず「導入されていない」と誤解します。ラベルを覚えるより、DaemonSet 自体を見るほうが確実です。

```bash
k get ds -n kube-system aws-efa-k8s-device-plugin
```

EFA 対応ノード（p4d x2）それぞれに 1 Pod ずつ Running していれば問題ありません。

## 6. マルチノードで NCCL/EFA の帯域を測る

TrainJob は投入した時点で走り始めるので、あらためて起動する操作はありません。進行と結果を Pod のログで確認します。

```bash
k -n "$NAMESPACE" get trainjob nccl-trainjob
k -n "$NAMESPACE" logs -l jobset.sigs.k8s.io/jobset-name=nccl-trainjob --tail=-1 \
  | grep -E "Selected provider|Using network|\[bench\]"
```

確認ポイントは次の 2 つです。

- ログに `NET/OFI Selected provider is efa` と `Using network Libfabric` が出ることを確認します（TCP に落ちていない証拠になります）
- `busbw` が高い値を示すことを確認します

`NCCL_DEBUG` はチャートが `INFO` に設定しています。`Selected provider is efa` の行は `INFO` レベルでしか出力されず、`WARN` では EFA が使われた証拠が得られません。`NCCL_DEBUG_SUBSYS=INIT,NET` で対象サブシステムを絞り、ログが溢れるのを防いでいます。

実機確認結果（2 ノード p4d.24xlarge、A100 x16、EFA 3 NIC/ノード、16 プロセス）:

```text
nccl-trainjob-node-0-0:172:172 [0] NCCL INFO NET/Plugin: Loaded net plugin Libfabric (v11)
nccl-trainjob-node-0-0:172:172 [0] NCCL INFO Using network Libfabric
nccl-trainjob-node-0-0:172:172 [0] NCCL INFO NET/OFI Selected provider is efa, fabric is efa (found 3 nics)
nccl-trainjob-node-0-0:172:172 [0] NCCL INFO NET/OFI Using Libfabric version 2.4
nccl-trainjob-node-0-1:172:172 [0] NCCL INFO NET/OFI Selected provider is efa, fabric is efa (found 3 nics)
```

両ノードで `efa` プロバイダが選択され、3 NIC が認識されています。この `found 3 nics` が、手順 2 の `terraform output accelerator_pool_efa_schedulable` が CB プールについて返す値（p4d.24xlarge なら 3 = 4 − 1）と一致していることが重要です。手順 2 を実行した時点では `gpu-ddp` しか無いので 0 だけが出ますが、Basic05 の CB プールを apply したあとに同じコマンドを実行すると 3 が出ます。カード枚数から 1 引いた値が、そのまま NCCL が掴む NIC 数になります。

帯域の実測値:

```text
[bench] world_size=16 ranks/node=8
[bench]   512 MB     18.00 ms  algbw   29.82 GB/s  busbw   55.91 GB/s
[bench]  1024 MB     34.83 ms  algbw   30.82 GB/s  busbw   57.80 GB/s
[bench] done
```

| メッセージサイズ | algbw | busbw |
|---|---|---|
| 512 MB | 29.8 GB/s | 55.9 GB/s |
| 1024 MB | 30.8 GB/s | 57.8 GB/s |

測定するサイズは `ncclTrainjob.sizesMb` で変えられますが、EFA が効いているかの判定には大きいサイズの方が向きます。小さいメッセージでは通信の立ち上がりコストが支配的で、帯域が出るところまで到達しません。

EFA が効いていることを確かめるには絶対値だけでなく比較対象が必要です。同じ構成で単一ノードに閉じた場合（ノードをまたがないので NVLink のみ）の値と並べます。

| 構成 | 通信経路 | busbw（1 GB） |
|---|---|---|
| 1 ノード 8 GPU | NVLink のみ | 227.1 GB/s |
| 2 ノード 16 GPU | ノード間は EFA | 57.8 GB/s |

ノードをまたぐと NVLink の約 4 分の 1 に落ちますが、これは想定どおりです。p4d.24xlarge の EFA は 4 カード構成で、そのうち通信に使えるのは 3 枚なので、NVLink の帯域には及びません。重要なのは 57.8 GB/s という値が TCP 経由（一般に数 GB/s 台）では到達できない水準にあることで、これが EFA/RDMA が実際に使われている証拠になります。EFA カードが 16 枚ある p5en や 32 枚ある p5 では、この数字はさらに大きくなります。

この 2 つの値は同じ構成で日を変えて複数回測っても 57-58 GB/s と 227 GB/s に収まりました。`mpirun` 版で測った値（同一ハードウェアで 57.0 GB/s）ともほぼ一致します。読者の環境で桁が違う値（たとえばノード間が数 GB/s 台）になった場合は、EFA ではなく TCP にフォールバックしている可能性が高いので、先に `Selected provider is efa` の行が出ているかを確認してください。

:::message
`fabric` の表示は `efa` と `efa-direct` の 2 種類があります。上の実測では `efa` が選択されており、同時に `Using transport protocol SENDRECV (platform set)` が出ています。どちらが選ばれるかはインスタンス世代・libfabric・aws-ofi-nccl のバージョンの組み合わせで決まるため、`efa-direct` でなくても異常ではありません。判定の要点は `Selected provider is efa` であること、つまり TCP へ落ちていないことです。
:::

:::message
NCCL テストを実行するには、テスト対象の GPU が他の Pod（Ray ワーカーなど）に占有されていないことが前提です。既存のワークロードを停止してからテストを実行してください。
:::

## NCCL ログに出る GDRCopy 警告の正体

EFA でノード間通信を回すと、NCCL のログに次の一行が出ることがあります。

```text
NET/OFI Failed to initialize GDRCopy: Failed to open gdr handle
```

これはエラーではなく、GDRCopy という補助機構が使えなかったという通知です。EFA がノード間で GPU メモリのデータをやり取りするとき、NIC が GPU メモリへ直接データを読み書きする経路が二段構えになっています。大きなメッセージのバルク転送は GPUDirect RDMA が NIC から GPU メモリへ直接 DMA するので、この経路は GDRCopy とは無関係に動きます。一方で受信側の小さなメッセージのコピーには GDRCopy を使う道があり、これが無い場合は libfabric の EFA プロバイダが EFA デバイス経由のループバック read という代替経路でホストのバウンスバッファ越しにコピーします。つまり GDRCopy はマルチノード通信の小さなメッセージのレイテンシを詰めるための補助であって、EFA/NCCL がノード間で帯域を出すこと自体には必須ではありません。上の警告が出ていても、`Selected provider is efa` と高い `busbw` が出ていれば EFA は正しく効いています。

GDRCopy を実際に有効にするには、ノードのカーネルに `gdrdrv` というモジュールをロードして `/dev/gdrdrv` を用意する必要があります。AMI にこれが標準で載っていない場合、載せる仕組みを別途用意することになります。その仕組みと、GDRCopy を有効にしたときにマルチノード通信のレイテンシが実際にどうなるのかの実測は、本 book では扱いません。上の警告が出ていても EFA の帯域が出ていれば、本章の検証としては合格です。

## 7. teardown する

検証が終わったら、ワークロードを退避します。`04-teardown.sh` は Deployment/StatefulSet/Job/TrainJob/MPIJob を削除対象に含むため、本章で投入した `ncclTrainjob` もこのスクリプトで消えます。TrainJob は配下に JobSet が管理する Pod を持ちますが、スクリプトは TrainJob の削除がタイムアウトした場合に finalizer を外して確実に消すフォールバックまで備えているので、Pod が残って NodePool の drain が引っかかることはありません。単独で先に消しておきたい場合は次のコマンドを使いますが、必須ではありません。

```bash
k delete trainjob nccl-trainjob -n "$NAMESPACE" --ignore-not-found
```

helper script でノードプールを退避する場合、 `infra/eks/scripts` にスクリプトがあります。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/scripts
./04-teardown.sh --namespace "$NAMESPACE" --nodepool "$POOL"
```

`04-teardown.sh` は Deployment/StatefulSet/Job/TrainJob/MPIJob を削除し、GPU Pod が完全に終了したのを確認したうえで Karpenter の NodePool を削除します。CB のノード自体は予約期間の終了時に AWS 側で強制回収されるため、このスクリプトは「ワークロードを安全に退避させる」ところまでを担当します。クラスタ全体を壊す `terraform destroy` は `--destroy` を明示した場合のみ実行されます。

# まとめ

本章では、Karpenter が起動する EFA 対応ノードで EFA が正しく構成され、実際にノード間で帯域が出ていることを確認しました。カード枚数とレイアウトは EC2 の `DescribeInstanceTypes` から plan 時に導出されるため、インスタンスタイプを書けば `networkInterfaces` は自動生成されます。

加えて、実装を読まないと気づきにくい 2 点を押さえました。EFA のセキュリティグループには ingress と egress の**両方**に self-referencing ルールが必要で、egress を CIDR で書いても SRD トラフィックは通りません。`NCCL_SOCKET_IFNAME` は許可リストではなく `^` 始まりの除外パターンで書きます。どちらも設定を誤ると「EFA を選んだはずなのにデータが流れない」という診断困難な症状になります。

帯域は p4d.24xlarge 2 台で busbw 57.8 GB/s（対照の単一ノード NVLink は 227.1 GB/s）でした。NCCL のログが `Selected provider is efa (found 3 nics)` を示し、この `3` が手順 2 で見た schedulable EFA 数と一致することが、EFA が意図どおり配線されている証拠になります。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [AWS の EFA（Elastic Fabric Adapter）](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/efa.html)
- [aws-efa-k8s-device-plugin](https://github.com/aws/eks-charts/tree/master/stable/aws-efa-k8s-device-plugin)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
