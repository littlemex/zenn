---
title: "Basic04 - EFA でマルチノード通信を検証する"
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

Karpenter の EC2NodeClass は、`spec.networkInterfaces` を省略すると単一のデフォルト ENA（IP 通信用）だけを作ります。EFA を使うにはこのフィールドで以下を明示宣言する必要があります。

- カード 0: `interfaceType: "interface"`（ノード IP 用、primary ENI）
- カード 1〜N: `interfaceType: "efa-only"`（RDMA 専用、IP を持たない）

この宣言はインスタンスタイプごとにカード枚数とレイアウトが異なるため、手書きは事故の温床になります。

この宣言をプールごとに手書きすると、カード枚数を 1 つ間違えるだけで事故になります。以降では、この宣言を自動生成している実コードを引用しながら、設計意図を見ていきます。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/fix/eks-efa-verification-improvements/infra/eks) です。

## EFA トポロジのルックアップテーブル

[`locals.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/locals.tf) にインスタンスタイプ別のカード枚数テーブルを置き、pool の `instance_types` から EFA トポロジを自動導出します。

```hcl
# locals.tf
efa_capability = {
  "p5.48xlarge"    = { cards = 32, multi_card = true }
  "p5e.48xlarge"   = { cards = 32, multi_card = true }
  "p5en.48xlarge"  = { cards = 16, multi_card = true }
  "trn2.48xlarge"  = { cards = 16, multi_card = true }
  "trn1.32xlarge"  = { cards = 8, multi_card = true }
  "trn1n.32xlarge" = { cards = 16, multi_card = true }
  "g6e.12xlarge"   = { cards = 1, multi_card = false }
  "g6e.24xlarge"   = { cards = 1, multi_card = false }
  "g6e.48xlarge"   = { cards = 1, multi_card = false }
  # EFA 非対応の GPU タイプも cards = 0 で明示する。表に載せないと「未知のインスタンス
  # タイプ」として後述の Guard 3 に弾かれるが、cards = 0 と明示すれば「EFA なしは既知の
  # 選択」として通る。
  "g5.12xlarge"    = { cards = 0, multi_card = false }
  "g5.24xlarge"    = { cards = 0, multi_card = false }
  "g5.48xlarge"    = { cards = 0, multi_card = false }
}
```

`multi_card = true`（p5/p5e/p5en/trn1n/trn2）はカード 0 が primary（ノード IP）、残りのカードが `efa-only` になるレイアウトです。`multi_card = false`（g6e、および EFA 非対応の g5）はカード 0 上に primary と EFA（またはなにも）が共存します。g5 を `cards = 0` で明示的に列挙しているのは「EFA 非対応です」という既知の事実を表に残すためで、単に書き忘れているのと Terraform 上は区別がつかないため、意図的にエントリを作っています。

このテーブルは pool 側で上書きできます。`pool_efa` がその解決ロジックです。

```hcl
# locals.tf
pool_efa = {
  for k, p in var.accelerator_pools : k => {
    count = (
      p.efa_interface_count >= 0
      ? p.efa_interface_count
      : try(local.efa_capability[local.pool_rep_instance_type[k]].cards, 0)
    )
    multi_card = (
      p.efa_multi_card != null
      ? p.efa_multi_card
      : try(local.efa_capability[local.pool_rep_instance_type[k]].multi_card, false)
    )
  }
}

pool_efa_schedulable = {
  for k, e in local.pool_efa : k => (
    e.count <= 0 ? 0 : (e.multi_card ? e.count - 1 : e.count)
  )
}
```

`p.efa_interface_count >= 0` のときだけ pool の明示値を優先し、そうでなければテーブルから導出します（既定値は `-1` で「未指定」を表します）。この 2 段構えにより、通常は何も書かなくてもテーブルから自動導出され、特殊なプールだけ明示的に上書きできます。

`pool_efa_schedulable` が **card 0 問題**の実体です。multi-card レイアウトでは**カード 0 がノード IP を運ぶため EFA-only として広告されません**。つまり以下のようになります。

- p5en.48xlarge: 16 カード → schedulable EFA = **15**
- p5.48xlarge: 32 カード → schedulable EFA = **31**
- g6e.12xlarge: 1 カード（single-card）→ schedulable EFA = **1**

Pod が `vpc.amazonaws.com/efa: 16` をリクエストすると、15 しか広告されないため永久に Pending になります。このモジュールでは `terraform output accelerator_pool_efa_schedulable` でプールごとの正しい値を公開しています。

## networkInterfaces の自動生成

`pool_efa` で解決したトポロジを、Karpenter の EC2NodeClass が要求する `spec.networkInterfaces` の配列に変換するのが [`karpenter-resources.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/karpenter-resources.tf) の `pool_network_interfaces` です。

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

## EFA セキュリティグループ — egress self-ref が必須

これがこのモジュールで実測から得られた最も重要な知見です。[`sg.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/sg.tf) で定義しています。

EFA の SRD トラフィックは**通常の IP トラフィックではありません**。そのため、SG の egress ルールに `0.0.0.0/0` の CIDR を設定しても、SRD パケットは許可されません。必要なのは ingress と egress の**両方に self-referencing all-traffic ルール**を持つことです。

```hcl
# sg.tf

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

## 4 つの precondition ガード

`pool_network_interfaces` はテーブル駆動で自動生成される一方、pool の書き方を間違えると存在しないカードを参照した `networkInterfaces` を生成してしまいます。`karpenter-resources.tf` の `kubectl_manifest.accelerator_nodeclass` には、この事故を plan 時に止めるための precondition が 4 つ並んでいます。

```hcl
# karpenter-resources.tf（抜粋）
# Guard 1: プール内の全 instance_types が同じ EFA トポロジを持つこと
precondition {
  condition = length(distinct([
    for t in each.value.instance_types :
    format("%d/%t", try(local.efa_capability[t].cards, 0), try(local.efa_capability[t].multi_card, false))
  ])) == 1
  error_message = "Pool ${each.key} mixes instance types with different EFA topologies ..."
}

# Guard 2: multi-card 対応のインスタンスが single-card レイアウトに解決されていないこと
precondition {
  condition = (
    local.pool_efa[each.key].count == 0 ||
    !(
      try(local.efa_capability[local.pool_rep_instance_type[each.key]].multi_card, false) &&
      (local.pool_efa[each.key].count <= 1 || !local.pool_efa[each.key].multi_card)
    )
  )
  error_message = "Pool ${each.key} (...) is a multi-card EFA instance but resolved to a single-card layout."
}

# Guard 3: 未知のインスタンスタイプが黙って EFA=0 にフォールバックしないこと
precondition {
  condition = (
    contains(keys(local.efa_capability), local.pool_rep_instance_type[each.key]) ||
    each.value.efa_interface_count >= 0
  )
  error_message = "Pool ${each.key} uses instance type ... which is not in the EFA capability table ..."
}

# Guard 4: 手動指定した efa_interface_count が物理カード数を超えないこと
precondition {
  condition = (
    each.value.efa_interface_count < 0 ||
    each.value.efa_interface_count <= try(local.efa_capability[local.pool_rep_instance_type[each.key]].cards, 999999)
  )
  error_message = "Pool ${each.key} sets efa_interface_count = ..., but ... has only ... EFA card(s)."
}
```

4 つのガードはそれぞれ異なる事故を防ぎます。**Guard 1** は 1 つの NodePool に g6e と p5en のような異なる EFA トポロジのインスタンスタイプを混在させる設定を拒否します（`networkInterfaces` は pool 単位で 1 パターンしか生成できないため）。**Guard 2** は「multi-card のはずのインスタンスなのに解決結果が single-card 相当（count が 1 以下、または `multi_card = false` の上書き）になっている」という上書きミスを検出します。**Guard 3** が最も診断困難な障害を防ぐガードで、テーブルに存在しない未知のインスタンスタイプかつ `efa_interface_count` も未指定の場合、黒魔術的に `try(..., 0)` で EFA=0 に静かにフォールバックさせず、明示的に reject します。これが無いと「ノードは起動するが NCCL が TCP にフォールバックする」という、ログを読まないと分からない障害になります。**Guard 4** は逆に手動上書きが物理カード数を超えるケース（例: p5en の 16 カードに対して 32 を指定）を防ぎます。これを許すと Karpenter が存在しないカードを参照した `networkInterfaces` を生成し、`RunInstances` が失敗してリトライループに陥ります。

## EFA device plugin の supportedInstanceLabels 自動導出

EFA を Pod にリソースとして見せるのは `aws-efa-k8s-device-plugin` の DaemonSet です。この chart は `supportedInstanceLabels` に列挙されたインスタンスタイプにしか nodeAffinity でスケジュールされず、chart デフォルトの一覧には g6e.12xlarge のような一部の EFA 対応タイプが含まれていません。デフォルトのままだとそのタイプのノードにはプラグインが乗らず、`vpc.amazonaws.com/efa` が永久に広告されないという、Guard 3 と同種の「静かなフォールバック」が device plugin 側でも起こり得ます。[`gpu-addons.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/gpu-addons.tf) はこれをクラスタが実際に使う pool から動的に導出することで防いでいます。

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

`efa_supported_instance_types` は `local.pool_efa[k].count > 0` の pool だけから `instance_types` を集めるため、クラスタが実際に EFA を使う構成でだけ chart のデフォルト一覧を上書きします。空リストのときはあえて上書きせず chart デフォルトに委ねているのは、空の `supportedInstanceLabels` を渡すとデフォルト一覧そのものが消え、EFA 非対応クラスタでプラグインがどこにもスケジュールされなくなるためです。tolerations は `nvidia.com/gpu` と `aws.amazon.com/neuron` の両方の taint を許容しています。EFA は GPU プールと Neuron プールの両方から使われる共有アドオンなので、片方の toleration だけを付けると trn2 系のノードにプラグインが乗らず、そちらだけ `vpc.amazonaws.com/efa` が広告されないという非対称な障害になるためです。

## 全体の中での位置付け

本章は、前章までで Karpenter が起動したアクセラレータノードの上に成り立っています。ノードそのものは既に `Ready` になっていますが、EFA インターフェースが正しい枚数で広告され、セキュリティグループが SRD トラフィックを通し、実際の NCCL 通信が TCP にフォールバックせず EFA 経由で流れているかは、ノードが `Ready` であることとは別に確認が必要です。本章はこの「ノードは立っているが通信は本当に EFA を使っているか」を検証する層にあたります。

## 注意

**1. `vpc.amazonaws.com/efa` のリクエスト数を間違えると永久 Pending**

p5en で `efa: 16` をリクエストすると schedulable は 15 のため Pod がスケジュールされません。必ず `terraform output accelerator_pool_efa_schedulable` の値を参照してください。

**2. EFA SG に egress self-ref が無いと `Error 15 Unreachable remote`**

bootstrap（TCP）は成功するのにデータ転送がハングします。NCCL ログに `Selected provider is efa` と出ている時点で「EFA は選ばれている」ため、ネットワーク側を疑わないと迷宮入りします。原因は SG の egress に self-referencing ルールが無いことです。

**3. `NCCL_SOCKET_IFNAME` を positive 指定すると TCP fallback**

`NCCL_SOCKET_IFNAME=efa0` のような書き方は失敗する場合があります。除外パターン `^lo,docker,veth` の方が安全です。

**4. EFA device plugin の chart version と app version の混同**

`aws-efa-k8s-device-plugin` の Helm chart version とコンテナの app/image version は別系列です。chart version を指定する際に app version を代入すると、存在しないタグを参照して install が失敗します。

**5. 未知のインスタンスタイプは黙って EFA=0 にフォールバックしない**

ルックアップテーブルに存在しないインスタンスタイプで `efa_interface_count` も未指定の場合、`karpenter-resources.tf` の precondition が明示的に reject します。黙って EFA なしにフォールバックすることはありません。これは「ノードは起動するが NCCL が TCP になる」という最も診断しづらい障害モードを防ぐための設計です。

# ワークショップ実施

## 1. Schedulable EFA の値を確認する

```bash
terraform output accelerator_pool_efa_schedulable
```

期待される出力:

```
{
  "gpu-p5en" = 15
}
```

## 2. ノード上の EFA リソースを確認する

```bash
kubectl describe node <p5en-node> | grep "vpc.amazonaws.com/efa"
```

実機出力（p5en.48xlarge）:

```
  vpc.amazonaws.com/efa:  15
  vpc.amazonaws.com/efa:  15
```

Capacity（物理上限）と Allocatable（Pod にリクエスト可能な値）の両方が 15 であることが確認できます。16 ではなく 15 — これが card 0 問題の実証です。

## 3. EFA device plugin の稼働を確認する

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-efa-k8s-device-plugin
```

期待される出力:

```
NAME                                 READY   STATUS    RESTARTS   AGE
aws-efa-k8s-device-plugin-xxxxx      1/1     Running   0          19h
aws-efa-k8s-device-plugin-yyyyy      1/1     Running   0          17h
```

EFA 対応ノード（p5en x2）それぞれに 1 Pod ずつ Running していれば問題ありません。

## 4. マルチノードで NCCL/EFA を検証する

:::message
マルチノード NCCL 検証には p5en x2 以上が必要です。p5en クラスのインスタンスは On-Demand ではまず取れないため、Capacity Block を購入してからここに戻ってきてください。手順 1〜3 は On-Demand の単一ノードでも確認できるので、まずそこまで進めても問題ありません。
:::

マルチノードで NCCL が EFA を使っていることを検証します。

```bash
./scripts/03-verify-nccl.sh --nodes 2 --gpus-per-node 8
```

確認ポイントは次の 2 つです。

- ログに `NET/OFI Selected provider is efa` が出ること（TCP fallback していない証拠）
- `busbw` が高い値を示すこと

実機確認結果（2 ノード p5en.48xlarge、H200 x16、EFA 15 NIC）:

```
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

`NCCL_SOCKET_IFNAME` は**除外パターン**（`^` で始まる）で書きます。`efa0,efa1,...` のような許可リスト方式だと、NCCL が EFA インターフェースを見つけられず TCP fallback する場合があります。

# まとめ

本章では、Karpenter が起動した EFA 対応ノードで、マルチノード NCCL 通信が実際に EFA 経由で動作していることを検証しました。schedulable な EFA 数はカード枚数より 1 つ少ないこと、EFA のセキュリティグループには ingress/egress 両方に self-referencing ルールが必要なこと、`NCCL_SOCKET_IFNAME` は除外パターンで書くべきこと、という 3 点を実機のログと busbw 値で確認できれば、この基盤の上で分散学習・推論を安心して回せます。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [AWS の EFA（Elastic Fabric Adapter）](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/efa.html)
- [aws-efa-k8s-device-plugin](https://github.com/aws/eks-charts/tree/master/stable/aws-efa-k8s-device-plugin)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
