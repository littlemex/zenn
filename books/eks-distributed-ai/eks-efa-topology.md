---
title: "Basic05 - EFA でマルチノード通信を検証する"
free: true
---

本章では、Karpenter が起動する EFA 対応ノードで、マルチノード NCCL 通信が実際に EFA 経由で流れていることを検証します。EFA のインターフェース数とレイアウトをインスタンスタイプから自動導出する仕組み、schedulable な EFA 数がカード枚数より 1 つ少なくなる card 0 問題、そしてセキュリティグループの見落としがちな設定を押さえたうえで、実機のログと busbw 値で EFA が使われていることを確認します。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち Karpenter が起動するアクセラレータノード同士を結ぶ EFA ネットワークの部分です。ノードそのものの起動は前章までで完了しているため、ここでは「起動したノードの EFA が正しく使えているか」を確認します。

## これは何をするものか

### EFA とは

EFA（Elastic Fabric Adapter）は AWS の高帯域・低遅延ネットワークインターフェースで、OS-bypass による SRD（Scalable Reliable Datagram）プロトコルを使います。GPU/Neuron のマルチノード集合通信（NCCL）で必要な帯域を確保するために不可欠な存在です。

通常の ENI（Elastic Network Interface）と異なり、EFA はカーネルを経由せずにユーザ空間から直接データを送受信します。これにより低遅延・高スループットを実現しますが、ネットワーク上のトラフィック特性が通常の IP と異なるため、セキュリティグループの設定にも独自の要件があります（後述の「注意」を参照してください）。

![EFA によるマルチノード NCCL 通信](/images/books/eks-distributed-ai/arch-efa-detail.png)

### なぜ Karpenter は EFA を自動で付けないか

Karpenter（karpenter-provider-aws v1.11 以降）の EC2NodeClass は、`spec.networkInterfaces` を省略すると単一のデフォルト ENA（IP 通信用）だけを作ります。EFA を使うにはこのフィールドで以下を明示宣言する必要があります（詳細は [Karpenter NodeClasses ドキュメントの spec.networkInterfaces](https://karpenter.sh/docs/concepts/nodeclasses/#specnetworkinterfaces) を参照してください）。

- カード 0: `interfaceType: "interface"`（ノード IP 用、primary ENI）
- カード 1〜N: `interfaceType: "efa-only"`（RDMA 専用、IP を持たない）

この宣言はインスタンスタイプごとにカード枚数とレイアウトが異なるため、プールごとに手書きするとカード枚数を 1 つ間違えるだけで事故になります。以降では、この宣言を自動生成している実コードを引用しながら、設計意図を見ていきます。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) です。

## EFA トポロジを EC2 API から動的に取得する

EFA のカード枚数はインスタンスタイプごとに物理的に決まっていますが、命名規則からは導出できません。同じ g6e ファミリでも g6e.4xlarge 以下は EFA 非対応で g6e.8xlarge 以上は EFA 対応、同じ p5 系でも p5 は 32 カード・p5en は 16 カードというように、境界も枚数も型ごとに異なります。この境界はドキュメント改訂で変わり得るため、手元の型で確認したい場合は次のコマンドを実行してください。

```bash
aws ec2 describe-instance-types --instance-types <type> \
  --query 'InstanceTypes[0].NetworkInfo.{EFA:EfaSupported,MaxEfa:EfaInfo.MaximumEfaInterfaces}'
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

この値を使って [`locals.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/locals.tf) の `pool_efa` が EFA トポロジを解決します。pool 側で `efa_interface_count` を明示した場合はそれを優先し、未指定（既定値 `-1`）なら API の値を使います。

```hcl
# locals.tf
pool_efa = {
  for k, p in var.accelerator_pools : k => {
    count = (
      p.efa_interface_count >= 0
      ? p.efa_interface_count
      : (data.aws_ec2_instance_type.pool_rep[local.pool_rep_instance_type[k]].efa_supported
        ? coalesce(data.aws_ec2_instance_type.pool_rep[local.pool_rep_instance_type[k]].efa_maximum_interfaces, 0)
        : 0)
    )
    multi_card = (
      p.efa_multi_card != null
      ? p.efa_multi_card
      : (data.aws_ec2_instance_type.pool_rep[local.pool_rep_instance_type[k]].efa_supported &&
         coalesce(data.aws_ec2_instance_type.pool_rep[local.pool_rep_instance_type[k]].efa_maximum_interfaces, 0) > 1)
    )
  }
}

pool_efa_schedulable = {
  for k, e in local.pool_efa : k => (
    e.count <= 0 ? 0 : (e.multi_card ? e.count - 1 : e.count)
  )
}
```

`efa_supported = false` の g5/g6 は `efa_maximum_interfaces` が null を返すため `coalesce(..., 0)` で 0 に丸めます。この API 直結の導出により、新しいインスタンスタイプはコード変更なしで正しく扱えます。

:::message
`efa_maximum_interfaces`（EFA を張れる数）は `maximum_network_cards`（物理ネットワークカードの総数）とは別の属性です。多くの型では一致しますが、概念が違うため EFA 数には必ず `efa_maximum_interfaces` を使います。Terraform AWS provider にこの属性が用意されているため、AWS CLI を別途叩く必要はありません。
:::

`pool_efa_schedulable` が **card 0 問題**の実体です。multi-card レイアウトでは**カード 0 がノード IP を運ぶため EFA-only として広告されません**。つまり以下のようになります。

- p5en.48xlarge: 16 カード → schedulable EFA = **15**
- p5.48xlarge: 32 カード → schedulable EFA = **31**
- g6e.12xlarge: 1 カード（single-card）→ schedulable EFA = **1**

Pod が `vpc.amazonaws.com/efa: 16` をリクエストすると、15 しか広告されないため永久に Pending になります。このモジュールでは `terraform output accelerator_pool_efa_schedulable` でプールごとの正しい値を公開しています。

## networkInterfaces の自動生成

`pool_efa` で解決したトポロジを、Karpenter の EC2NodeClass が要求する `spec.networkInterfaces` の配列に変換するのが [`karpenter-resources.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/karpenter-resources.tf) の `pool_network_interfaces` です。

```hcl
# karpenter-resources.tf
pool_network_interfaces = {
  for k, p in var.accelerator_pools : k => (
    local.pool_efa[k].count <= 0 ? [] : concat(
      [{ networkCardIndex = 0, deviceIndex = 0, interfaceType = "interface" }],
      local.pool_efa[k].multi_card ? [
        for i in range(local.pool_efa[k].count - 1) : {
          networkCardIndex = i + 1
          deviceIndex      = 0
          interfaceType    = "efa-only"
        }
        ] : [
        for i in range(local.pool_efa[k].count) : {
          networkCardIndex = 0
          deviceIndex      = i + 1
          interfaceType    = "efa-only"
        }
      ]
    )
  )
}
```

読みどころは primary interface の後に続く 2 パターンの分岐です。multi-card（p5/p5en/trn2 系）では primary がカード 0 を占有しているため、`efa-only` は `range(count - 1)` 個をカード 1 以降（`networkCardIndex = i + 1`）に 1 枚ずつ割り当てます。これが前節の「schedulable = カード数 − 1」と一致する理由です。single-card（g6e）では逆に、primary と `efa-only` が同じカード 0 上に共存するため、`networkCardIndex` は常に 0 のまま `deviceIndex` だけを `range(count)` でインクリメントします。EFA が無効なプール（`count <= 0`）は空リストを返し、EC2NodeClass 側で `networkInterfaces` フィールド自体を省略してデフォルトの単一 ENA に委ねます。

## EFA セキュリティグループには egress self-ref が必須

これがこのモジュールで実測から得られた最も重要な知見です。[`sg.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/sg.tf) で定義しています。

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

egress self-ref が無い場合の症状として、NCCL は bootstrap（TCP）に成功し `Selected provider is efa` と表示するものの、実際のデータ転送で `NET/OFI ... Error 15 (Unreachable remote)` が出てハングします。「EFA を選んだはずなのにデータが流れない」という診断困難な障害になります。

## precondition ガード

`pool_network_interfaces` は自動生成される一方、pool の書き方を間違えると存在しないカードを参照した `networkInterfaces` を生成してしまいます。`karpenter-resources.tf` の `kubectl_manifest.accelerator_nodeclass` には、この事故を plan 時に止めるための precondition が並んでいます。EFA トポロジは前述の `data.aws_ec2_instance_type` から取得するため、いずれのガードも API の値（`efa_supported` / `efa_maximum_interfaces`）を参照します。

```hcl
# karpenter-resources.tf（抜粋）
# Guard 1: プール内の全 instance_types が同じ EFA トポロジを持つこと
precondition {
  condition = length(distinct([
    for t in each.value.instance_types :
    format("%d/%s",
      data.aws_ec2_instance_type.pool_rep[t].efa_supported ? coalesce(data.aws_ec2_instance_type.pool_rep[t].efa_maximum_interfaces, 0) : 0,
      data.aws_ec2_instance_type.pool_rep[t].efa_supported && coalesce(data.aws_ec2_instance_type.pool_rep[t].efa_maximum_interfaces, 0) > 1)
  ])) == 1
  error_message = "Pool ${each.key} mixes instance types with different EFA topologies ..."
}

# Guard 2: multi-card 対応のインスタンスが single-card レイアウトに解決されていないこと
precondition {
  condition = (
    local.pool_efa[each.key].count == 0 ||
    !(
      (data.aws_ec2_instance_type.pool_rep[local.pool_rep_instance_type[each.key]].efa_supported &&
       coalesce(data.aws_ec2_instance_type.pool_rep[local.pool_rep_instance_type[each.key]].efa_maximum_interfaces, 0) > 1) &&
      (local.pool_efa[each.key].count <= 1 || !local.pool_efa[each.key].multi_card)
    )
  )
  error_message = "Pool ${each.key} (...) is a multi-card EFA instance but resolved to a single-card layout."
}

# (Guard 3 は削除済み: EC2 API 直結にしたことで「未知の型」という状態自体がなくなったため不要になりました)

# Guard 4: 手動指定した efa_interface_count が EFA インターフェース数を超えないこと
precondition {
  condition = (
    each.value.efa_interface_count < 0 ||
    each.value.efa_interface_count <= coalesce(data.aws_ec2_instance_type.pool_rep[local.pool_rep_instance_type[each.key]].efa_maximum_interfaces, 0)
  )
  error_message = "Pool ${each.key} sets efa_interface_count = ..., but ... has only ... network card(s)."
}
```

:::message
静的テーブル時代には「テーブルに無い未知のインスタンスタイプが黙って EFA=0 にフォールバックしないこと」を確認するガードが Guard 3 として存在しましたが、EC2 API 直結にしたことで「未知の型」という状態自体がなくなったため、そのガードは不要になり削除されました。番号だけが欠番として残っているのはそのためです。pool が使う型は plan 時に必ず API で解決されます。
:::

各ガードはそれぞれ異なる事故を防ぎます。**Guard 1** は 1 つの NodePool に g6e と p5en のような異なる EFA トポロジのインスタンスタイプを混在させる設定を拒否します（`networkInterfaces` は pool 単位で 1 パターンしか生成できないため）。**Guard 2** は「multi-card のはずのインスタンスなのに解決結果が single-card 相当（count が 1 以下、または `multi_card = false` の上書き）になっている」という上書きミスを検出します。**Guard 4** は手動上書きが EFA インターフェース数を超えるケース（例: p5en の 16 に対して 32 を指定）を防ぎます。これを許すと Karpenter が存在しないカードを参照した `networkInterfaces` を生成し、`RunInstances` が失敗してリトライループに陥ります。

## EFA device plugin の supportedInstanceLabels 自動導出

EFA を Pod にリソースとして見せるのは `aws-efa-k8s-device-plugin` の DaemonSet です。この chart は `supportedInstanceLabels` に列挙されたインスタンスタイプにしか nodeAffinity でスケジュールされず、chart デフォルトの一覧には g6e.12xlarge のような一部の EFA 対応タイプが含まれていません。デフォルトのままだとそのタイプのノードにはプラグインが乗らず、`vpc.amazonaws.com/efa` が永久に広告されないという、静的テーブル時代に前述の削除済み Guard 3 が防いでいた「静かなフォールバック」と同種の問題が device plugin 側でも起こり得ます。[`gpu-addons.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/gpu-addons.tf) はこれをクラスタが実際に使う pool から動的に導出することで防いでいます。

```hcl
# gpu-addons.tf
efa_supported_instance_types = distinct(flatten([
  for k, p in var.accelerator_pools : p.instance_types if local.pool_efa[k].count > 0
]))

efa_device_plugin_values = merge(
  {
    tolerations = [
      { key = "capacity-reservation", operator = "Exists", effect = "NoSchedule" },
      { key = "nvidia.com/gpu", operator = "Exists", effect = "NoSchedule" },
      { key = "aws.amazon.com/neuron", operator = "Exists", effect = "NoSchedule" },
    ]
  },
  length(local.efa_supported_instance_types) > 0 ? {
    supportedInstanceLabels = {
      keys   = ["node.kubernetes.io/instance-type"]
      values = local.efa_supported_instance_types
    }
  } : {}
)
```

`efa_supported_instance_types` は `local.pool_efa[k].count > 0` の pool だけから `instance_types` を集めるため、クラスタが実際に EFA を使う構成でだけ chart のデフォルト一覧を上書きします。空リストになるのは `has_efa_pool = false`（EFA を使う pool が 1 つもない）場合で、そのときは `helm_release.aws_efa_k8s_device_plugin` 自体が `count = 0` でインストールされないため、この分岐に実際に到達することはありません。それでも空の `supportedInstanceLabels` を渡すとデフォルト一覧そのものが消えてしまうため、リリースが入らない場合に限られるとしても、あえて空上書きを避ける防御的な実装になっています。tolerations は `nvidia.com/gpu` と `aws.amazon.com/neuron` の両方の taint を許容しています。EFA は GPU プールと Neuron プールの両方から使われる共有アドオンなので、片方の toleration だけを付けると trn2 系のノードにプラグインが乗らず、そちらだけ `vpc.amazonaws.com/efa` が広告されないという非対称な障害になるためです。

## 全体の中での位置付け

本章は、前章までで Karpenter が起動したアクセラレータノードの上に成り立っています。ノードそのものは既に `Ready` になっていますが、EFA インターフェースが正しい枚数で広告され、セキュリティグループが SRD トラフィックを通し、実際の NCCL 通信が TCP にフォールバックせず EFA 経由で流れているかは、ノードが `Ready` であることとは別に確認が必要です。本章はこの「ノードは立っているが通信は本当に EFA を使っているか」を検証する層にあたります。

## 注意

**1. `vpc.amazonaws.com/efa` のリクエスト数を間違えると永久 Pending**

p5en で `efa: 16` をリクエストすると schedulable は 15 のため Pod がスケジュールされません。必ず `terraform output accelerator_pool_efa_schedulable` の値を参照してください。

**2. EFA SG に egress self-ref が無いと `Error 15 Unreachable remote`**

bootstrap（TCP）は成功するのにデータ転送がハングします。NCCL ログに `Selected provider is efa` と出ている時点で「EFA は選ばれている」ため、ネットワーク側を疑わないと迷宮入りします。原因は SG の egress に self-referencing ルールが無いことです。

**3. `NCCL_SOCKET_IFNAME` を positive 指定すると bootstrap に失敗する**

`NCCL_SOCKET_IFNAME` が制御するのは EFA データパスの選択ではなく、bootstrap（rendezvous 用のソケット通信）に使うインターフェースです。`NCCL_SOCKET_IFNAME=efa0` のように EFA-only インターフェースを名指しすると、efa-only インターフェースは RDMA 専用で IP アドレスを持たないため、NCCL がソケットを bind できずに失敗します。除外パターン `^lo,docker,veth` を使えば、IP を持つインターフェース（ENA）だけが bootstrap に使われるため安全です。

**4. EFA device plugin の chart version と app version の混同**

`aws-efa-k8s-device-plugin` の Helm chart version（`gpu-addons.tf` の `var.efa_device_plugin_chart_version`）とコンテナの app/image version は別系列です。chart version を指定する際に app version を代入すると、存在しないタグを参照して install が失敗します。

# ワークショップ実施

## 1. Schedulable EFA の値を確認する

```bash
terraform output accelerator_pool_efa_schedulable
```

期待される出力（定義したアクセラレータプールに応じて変わります。EFA を持たない cpu プールは含まれません）:

```text
{
  "gpu-dev"  = 1
  "gpu-p5en" = 15
  "trn2"     = 15
}
```

`gpu-dev`（g6e.12xlarge）は EFA を 1 枚だけ持つ single-card 構成なので、schedulable も 1 です。`gpu-p5en`（p5en.48xlarge、16 枚）と `trn2`（trn2.48xlarge、16 枚）は multi-card 構成で、card 0 を除いた 15 が schedulable になります。以降のマルチノード検証では 16 枚構成の p5en を使います。

## 2. ノード上の EFA リソースを確認する

```bash
kubectl describe node <p5en-node> | grep "vpc.amazonaws.com/efa"
```

実機出力（p5en.48xlarge）:

```text
  vpc.amazonaws.com/efa:  15
  vpc.amazonaws.com/efa:  15
```

Capacity（EFA device plugin が広告した数）と Allocatable（Pod にリクエスト可能な値）の両方が 15 であることが確認できます。物理的なカードは 16 枚ありますが、card 0 は node の IP を持つプライマリインターフェイスとして使われるため EFA リソースとして広告されず、Capacity も 15 になります。16 ではなく 15 になるのが card 0 問題の実証です。

## 3. EFA device plugin の稼働を確認する

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-efa-k8s-device-plugin
```

期待される出力:

```text
NAME                                 READY   STATUS    RESTARTS   AGE
aws-efa-k8s-device-plugin-xxxxx      1/1     Running   0          19h
aws-efa-k8s-device-plugin-yyyyy      1/1     Running   0          17h
```

EFA 対応ノード（p5en x2）それぞれに 1 Pod ずつ Running していれば問題ありません。

## 4. マルチノードで NCCL/EFA を検証する

:::message
マルチノード NCCL 検証には p5en x2 以上が必要です。p5en クラスのインスタンスは On-Demand ではまず取れないため、Basic06「Capacity Block を取得して組み込む」の手順で Capacity Block を購入してからここに戻ってきてください。p5en.48xlarge の CB は前払いで数十万円/日規模の支出になり得るため、購入前に必ず最小購入単位と予約期間を確認してください。手順 1〜3 は On-Demand の単一ノードでも確認できるので、まずそこまで進めても問題ありません。
:::

マルチノードで NCCL が EFA を使っていることを検証します。検証用の MPIJob を作る namespace として、Basic01 で用意した作業用 namespace を使います。

```bash
export NAMESPACE=distai
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
./scripts/03-verify-nccl.sh --nodes 2 --gpus-per-node 8 --namespace "$NAMESPACE"
```

このスクリプトは 2 ノードの NCCL `all_reduce_perf` を実行する MPIJob を内部で生成します。Worker Pod は `nvidia.com/gpu` と `vpc.amazonaws.com/efa` の両方の taint に対する toleration を持ち、`resources.limits` で `vpc.amazonaws.com/efa` を明示的にリクエストします。前節で見た通り、このリクエスト数が `terraform output accelerator_pool_efa_schedulable` の値を超えていると Pod は永久に Pending になるため、別のプールで実行する場合はスクリプトが要求する EFA 数を schedulable な値に合わせて調整してください。

確認ポイントは次の 2 つです。

- ログに `NET/OFI Selected provider is efa` が出ることを確認します（TCP fallback していない証拠になります）
- `busbw` が高い値を示すことを確認します

実機確認結果（2 ノード p5en.48xlarge、H200 x16、EFA 15 NIC）:

```text
ip-10-0-xx-xx [7] NCCL INFO NET/OFI Selected provider is efa, fabric is efa-direct (found 15 nics)
ip-10-0-yy-yy [6] NCCL INFO NET/OFI Selected provider is efa, fabric is efa-direct (found 15 nics)
```

両ノードで `efa-direct` プロバイダが選択され、15 NIC が認識されています。`found 15 nics` は `terraform output accelerator_pool_efa_schedulable` の値（= 16 − 1）と一致します。

参考 busbw 値（同構成の `torchrun` 直接実行での実測）:

| メッセージサイズ | algbw | busbw |
|---|---|---|
| 64 MB | 101.5 GB/s | 190.3 GB/s |
| 1024 MB | 127.2 GB/s | 238.4 GB/s |
| 8192 MB | 137.1 GB/s | 257.1 GB/s |

busbw 190-257 GB/s は TCP（~4-10 GB/s）の 20-60 倍であり、EFA が正しく動作している決定的な証拠です。

:::message
NCCL テストを実行するには、テスト対象の GPU が他の Pod（Ray ワーカーなど）に占有されていないことが前提です。既存のワークロードを停止してからテストを実行してください。
:::

## 5. NCCL_SOCKET_IFNAME を確認する

ワークロードの環境変数に以下が設定されていることを確認します。

```yaml
env:
  - name: NCCL_SOCKET_IFNAME
    value: "^lo,docker,veth"
  - name: FI_PROVIDER
    value: "efa"
```

`NCCL_SOCKET_IFNAME` は `^` で始まる**除外パターン**で書きます。`efa0,efa1,...` のような許可リスト方式で efa-only インターフェースを名指しすると、そのインターフェースは IP を持たないため NCCL が bootstrap 用のソケットを bind できず失敗します。除外パターンなら IP を持つインターフェース（ENA）だけが自動的に選ばれるため安全です。

# まとめ

本章では、Karpenter が起動した EFA 対応ノードで、マルチノード NCCL 通信が実際に EFA 経由で動作していることを検証しました。schedulable な EFA 数はカード枚数より 1 つ少ないこと、EFA のセキュリティグループには ingress/egress 両方に self-referencing ルールが必要なこと、`NCCL_SOCKET_IFNAME` は除外パターンで書くべきこと、という 3 点を実機のログと busbw 値で確認できれば、この基盤の上で分散学習・推論を安心して回せます。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [AWS の EFA（Elastic Fabric Adapter）](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/efa.html)
- [aws-efa-k8s-device-plugin](https://github.com/aws/eks-charts/tree/master/stable/aws-efa-k8s-device-plugin)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
