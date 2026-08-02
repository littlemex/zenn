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

本章の実機検証は p4d.24xlarge（NVIDIA A100 40GB x8、EFA x4）2 台の Capacity Block で実施しました。以降の出力はこの構成の実測値です。EFA の枚数はインスタンスファミリごとに違うので、読者の環境では数値が変わります。だからこそ枚数を決め打ちせず、次の手順のように必ず AWS 側の値を参照してください。

## 1. Schedulable EFA の値を確認する

```bash
terraform output accelerator_pool_efa_schedulable
```

実機出力（p4d プールと、EFA を持たない DDP 用プールを定義した状態）:

```text
{
  "gpu-ddp" = 0
  "gpu-p4d" = 3
}
```

`gpu-p4d`（p4d.24xlarge）は EFA を 4 枚持つ multi-card 構成なので、card 0 を除いた 3 が schedulable です。`gpu-ddp` は EFA を使わない小型 GPU プールなので 0 になります。

この値がインスタンスファミリごとにどう変わるかを、`describe-instance-types` が返すカード枚数から導出した実際の値で示します。

| インスタンスタイプ | EFA カード枚数 | schedulable | レイアウト |
|---|---|---|---|
| p4d.24xlarge | 4 | 3 | multi-card |
| p5en.48xlarge | 16 | 15 | multi-card |
| p5.48xlarge | 32 | 31 | multi-card |
| trn2.48xlarge | 16 | 15 | multi-card |
| g6e.12xlarge | 1 | 1 | single-card |

同じ p5 系でも p5 は 32 枚、p5en は 16 枚と倍違います。「multi-card なら 15」と覚えるのではなく、必ずこの `terraform output` かノードの allocatable を見る、というのがこの表の要点です。

## 2. ノード上の EFA リソースを確認する

```bash
kubectl describe node <p4d-node> | grep "vpc.amazonaws.com/efa"
```

実機出力（p4d.24xlarge）:

```text
  vpc.amazonaws.com/efa:  3
  vpc.amazonaws.com/efa:  3
```

Capacity（EFA device plugin が広告した数）と Allocatable（Pod にリクエスト可能な値）の両方が 3 であることが確認できます。物理的なカードは 4 枚ありますが、card 0 は node の IP を持つプライマリインターフェイスとして使われるため EFA リソースとして広告されず、Capacity も 3 になります。4 ではなく 3 になるのが card 0 問題の実証です。

スクリプトから参照する場合は `describe` の出力を grep するより、`.status.allocatable` を直接読むほうが確実です。

```bash
kubectl get nodes -l node-role=gpu-p4d \
  -o jsonpath="{range .items[*]}{.metadata.name}{'\t'}{.status.allocatable['vpc\.amazonaws\.com/efa']}{'\n'}{end}"
```

## 3. EFA device plugin の稼働を確認する

```bash
kubectl get pods -n kube-system -l name=aws-efa-k8s-device-plugin
```

実機出力（EFA 対応ノード 2 台）:

```text
NAME                              READY   STATUS    RESTARTS   AGE
aws-efa-k8s-device-plugin-d4vvd   1/1     Running   0          114m
aws-efa-k8s-device-plugin-pjb26   1/1     Running   0          124m
```

セレクタが `name=` であって `app.kubernetes.io/name=` ではない点に注意してください。この DaemonSet が付けているラベルは `name` の方だけなので、`app.kubernetes.io/name` で絞ると 1 件も返らず「導入されていない」と誤解します。ラベルを覚えるより、DaemonSet 自体を見るほうが確実です。

```bash
kubectl get ds -n kube-system aws-efa-k8s-device-plugin
```

EFA 対応ノード（p4d x2）それぞれに 1 Pod ずつ Running していれば問題ありません。

## 4. マルチノードで NCCL/EFA を検証する

:::message
マルチノード NCCL 検証には EFA 対応 GPU インスタンスが 2 台以上必要です。この規模のインスタンスは On-Demand ではまず取れないため、Basic06「Capacity Block を取得して組み込む」の手順で Capacity Block を購入してからここに戻ってきてください。本章の検証に使った p4d.24xlarge x2 の 24 時間ブロックは 566.40 USD（1 台 1 時間あたり 11.80 USD）でした。より新しい世代ではこれを大きく上回るため、購入前に `00-check-cb-offerings.sh` で必ず実際の価格・最小購入単位・予約期間を確認してください。手順 1〜3 は On-Demand の単一ノードでも確認できるので、まずそこまで進めても問題ありません。
:::

EFA の枚数も GPU の枚数もインスタンスタイプごとに違うため、コマンドに直接書かず、対象プールのノードの `.status.allocatable`（device plugin が実際に広告している値）から読み取って渡します。

```bash
export NAMESPACE=distai
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

POOL=gpu-p4d
GPU=$(kubectl get nodes -l node-role=$POOL \
  -o jsonpath="{.items[0].status.allocatable['nvidia\.com/gpu']}")
EFA=$(kubectl get nodes -l node-role=$POOL \
  -o jsonpath="{.items[0].status.allocatable['vpc\.amazonaws\.com/efa']}")
echo "gpu=$GPU efa=$EFA"   # p4d.24xlarge では gpu=8 efa=3

helm template exp charts/experiments -n "$NAMESPACE" \
  --set namespace="$NAMESPACE" \
  --set ncclSshd.enabled=true \
  --set ncclSshd.nodeRole=$POOL \
  --set ncclSshd.gpuCount=$GPU \
  --set ncclSshd.efaCount=$EFA \
  --set ncclSshd.image=public.ecr.aws/hpc-cloud/nccl-tests:cuda12.8.1-efa1.42.0-ofiv1.16.0-ncclv2.27.5-1-testsv2.16.4 \
  | kubectl apply -f -
```

`ncclSshd` は 2 つの Pod を別ノードに立て、それぞれに sshd を常駐させて、片方から `mpirun` で相手を叩く構成です。`nccl-tests` は MPI ベースなので rendezvous は `mpirun` が担います。

指定の要点は 3 つあります。

第一に、`ncclSshd.nodeRole` にはプール名を渡します。ノードの GPU SKU を表す `nvidia.com/gpu.product` で選びたくなりますが、このラベルは GPU Operator が起動済みのノードに後から付与するものなので、Karpenter が「どのインスタンスタイプを起動するか」を判断する材料になりません。これを nodeSelector に使うと Karpenter は次のように要求を拒否し、2 台目のノードが永久に起動しません。

```text
Failed to schedule pod, incompatible requirements,
label "nvidia.com/gpu.product" does not have known values
```

Karpenter が起動時に付ける `node-role=<プール名>` を使えば、ノードがまだ存在しない状態からプロビジョニングを誘発できます。

第二に、`gpuCount` と `efaCount` は上のようにノードから読んだ値を渡します。EFA の schedulable 数はファミリごとに違うため、固定値を書くと別のファミリでは必ず Pod が Pending になります。

第三に、SSH 鍵の配布は不要です。チャートがレンダリング時に鍵ペアを生成して Secret として両 Pod に配るため、Pod が `Running` になった時点で `mpirun` がそのまま通ります。

:::message
`helm template` は実行ごとに新しい鍵を生成します。上の例のようにパイプで一度に `kubectl apply` するか、いったんファイルに書き出してから適用してください。2 回に分けてレンダリングすると server と client が別々の鍵を持つことになり、SSH が通りません。
:::

2 つの Pod には hostname 単位の `podAntiAffinity` が入っており、同じノードに載ることはありません。同一ノードに載ると NCCL は NVLink だけで通信を完結させてしまい、EFA について何も検証できないテストになるためです。

:::message alert
`hugepages` を要求する Pod でノードの新規起動を誘発しないでください。Karpenter は hugepages を「どのインスタンスタイプなら足りるか」の判断に使わないため、`no instance type has enough resources` と判定して NodeClaim を作らず、Pod が永久に Pending になります。2 台目以降のノードは hugepages を要求しない Pod で先に起動させ、そのうえで hugepages を使うベンチマークを載せてください。この制約は Neuron 側のプローブでも同じです。
:::

両 Pod が `Running` になったら、server 側から `mpirun` でベンチマークを起動します。

```bash
SIP=$(kubectl -n "$NAMESPACE" get pod nccl-server -o jsonpath='{.status.podIP}')
CIP=$(kubectl -n "$NAMESPACE" get pod nccl-client -o jsonpath='{.status.podIP}')

kubectl -n "$NAMESPACE" exec nccl-server -- bash -lc "
/opt/amazon/openmpi/bin/mpirun --allow-run-as-root -np $((2 * GPU)) \
  -H $SIP:$GPU,$CIP:$GPU --mca plm_rsh_args '-p 2222' \
  -x FI_PROVIDER=efa -x FI_EFA_USE_DEVICE_RDMA=1 -x FI_EFA_FORK_SAFE=1 \
  -x NCCL_SOCKET_IFNAME='^lo,docker,veth' \
  -x NCCL_DEBUG=INFO -x NCCL_DEBUG_SUBSYS=INIT,NET \
  -x LD_LIBRARY_PATH -x PATH \
  /opt/nccl-tests/build/all_reduce_perf -b 512M -e 1G -f 2 -g 1"
```

`NCCL_DEBUG` は `INFO` にします。次に確認する `NET/OFI Selected provider is efa` の行は `INFO` レベルでしか出力されず、`WARN` では EFA が使われた証拠が得られません。`NCCL_DEBUG_SUBSYS=INIT,NET` で対象サブシステムを絞り、ログが溢れるのを防いでいます。両 Pod は `hostNetwork` なので Pod IP はノード IP と一致します。


確認ポイントは次の 2 つです。

- ログに `NET/OFI Selected provider is efa` が出ることを確認します（TCP fallback していない証拠になります）
- `busbw` が高い値を示すことを確認します

実機確認結果（2 ノード p4d.24xlarge、A100 x16、EFA 3 NIC/ノード、`all_reduce_perf` 16 ランク）:

```text
ip-10-0-115-100:318:365 [2] NCCL INFO NET/OFI Using transport protocol SENDRECV (platform set)
ip-10-0-115-100:318:365 [2] NCCL INFO NET/OFI Selected provider is efa, fabric is efa (found 3 nics)
ip-10-0-124-216:273:320 [0] NCCL INFO NET/OFI Selected provider is efa, fabric is efa (found 3 nics)
```

両ノードで `efa` プロバイダが選択され、3 NIC が認識されています。この `found 3 nics` が、手順 1 で見た `terraform output accelerator_pool_efa_schedulable` の `gpu-p4d = 3`（= 4 − 1）と一致していることが重要です。カード枚数から 1 引いた値が、そのまま NCCL が掴む NIC 数になります。

busbw 実測値:

| メッセージサイズ | algbw | busbw |
|---|---|---|
| 32 MB | 15.7 GB/s | 29.4 GB/s |
| 128 MB | 24.5 GB/s | 46.0 GB/s |
| 512 MB | 30.0 GB/s | 56.2 GB/s |
| 1024 MB | 30.9 GB/s | 57.9 GB/s |

平均 busbw は 57.0 GB/s でした。EFA が効いていることを確かめるには絶対値だけでなく比較対象が必要なので、同じコマンドを単一ノード 8 GPU（ノードをまたがないので NVLink のみ）で実行した値を並べます。

| 構成 | 通信経路 | busbw（1 GB） |
|---|---|---|
| 1 ノード 8 GPU | NVLink のみ | 227.1 GB/s |
| 2 ノード 16 GPU | ノード間は EFA | 57.9 GB/s |

ノードをまたぐと NVLink の約 4 分の 1 に落ちますが、これは想定どおりです。p4d.24xlarge の EFA は 4 カード構成で、そのうち通信に使えるのは 3 枚なので、NVLink の帯域には及びません。重要なのは 57.9 GB/s という値が TCP 経由（一般に数 GB/s 台）では到達できない水準にあることで、これが EFA/RDMA が実際に使われている証拠になります。EFA カードが 16 枚ある p5en や 32 枚ある p5 では、この数字はさらに大きくなります。

:::message
`fabric` の表示は `efa` と `efa-direct` の 2 種類があります。上の実測では `efa` が選択されており、同時に `Using transport protocol SENDRECV (platform set)` が出ています。どちらが選ばれるかはインスタンス世代・libfabric・aws-ofi-nccl のバージョンの組み合わせで決まるため、`efa-direct` でなくても異常ではありません。判定の要点は `Selected provider is efa` であること、つまり TCP へ落ちていないことです。
:::

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
