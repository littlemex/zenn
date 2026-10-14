---
title: "Awsome: EKS の GPU クラスタを CloudFormation で作る (PR の整理と FAQ)"
free: false
---

## はじめに

本章は、前章「Awsome: EKS の GPU クラスタを CloudFormation で作る」の [PR #1269](https://github.com/awslabs/awsome-distributed-ai/pull/1269) について、設計の判断、不具合の原因、検証を問答の形で整理したものである。前章の本文が Zenn の 1 章あたりの文字数の上限に近いので、問答を別の章に分けた。

## PR の整理 (FAQ)

この節は、前章の PR を、設計の判断、不具合の原因、検証の 3 つの面から問答の形で整理したものである。問は、全体と設計、不具合の原因の深掘り、検証と振り返りの 3 つに分けた。各問は英語の質問 (見出し)、日本語の質問、噛み砕き (ひとことでの表とやさしい版の図)、英語の回答、日本語の回答の順に書いた。不具合の問には原因の連鎖の図を付け、回答は症状、原因、突き止め方、直し方と比べた案、検証、残ったこと、の順に書いた。回答は前章の本文と、PR、マージ前後のコードで確かめた事実だけを使う。

### 全体と設計

:::details Q1. What does this design do, in short?
**質問 (日本語)**

この設計は、要するに何をするものか。

**Answer (English)**

The goal was a CloudFormation path for EKS GPU clusters that also works when you already have a cluster and only want to add GPUs. I split #1259's single template into network, cluster, GPU node group and AMI templates, plus a root that creates them from S3 URLs, so a full build is one submission and partial adoption uses a child directly. The node group template can be deployed many times on one cluster: names, readiness counts and image pre-pull are per node group, while the cluster-scoped device plugin releases are shared under an install-if-absent, fail-on-mismatch policy. AMIs come from an existing ID, a package list or a recipe, decided by which input is set. Capacity can be On-Demand, a targeted ODCR or a Capacity Block. The stack succeeds only when every node is Ready and advertises exactly the expected GPUs and EFA, and the bootstrap reports failures to CloudFormation on every path I tested, with CloudFormation's wait capped at 45 minutes even if a report never arrives. It was tested on Kubernetes 1.34 to 1.36, a Capacity Block, an existing cluster and a private-only endpoint.

**回答 (日本語)**

狙いは、既にクラスタを持っていて GPU だけを足したい場合にも使える、EKS の GPU クラスタを作る CloudFormation の経路だった。#1259 の 1 つのテンプレートを、ネットワーク、クラスタ、GPU のノードグループ、AMI の 4 つと、それらを S3 の URL から作る root に分けた。全部を作るなら提出は 1 つで、一部だけ使うなら子を直接使う。ノードグループのテンプレートは 1 つのクラスタに何度でも出せる。名前、確認の数え方、イメージの事前取得はノードグループごとで、クラスタに 1 つの device plugin のリリースは、無ければ入れ、版が違えば失敗させる方針で共有する。AMI は既にある ID、パッケージの一覧、レシピのどれかを、埋めた入力から決める。容量は On-Demand、targeted の ODCR、Capacity Block から選べる。スタックが成功するのは、すべてのノードが Ready で、期待する数の GPU と EFA をちょうど出したときだけで、失敗は試したすべての経路で CloudFormation に知らせ、知らせが届かなくても CloudFormation が待つのは長くても 45 分である。Kubernetes 1.34 から 1.36、Capacity Block、既存のクラスタ、private だけの endpoint で試した。
:::

:::details Q2. What were the requirements and design goals?
**質問 (日本語)**

要件と設計の狙いは何だったか。

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 論点 | 既にクラスタと VPC (ネットワークの区画) を持っていて GPU だけ足したい人の経路が無かった中で、設計の目標をどう立てるか |
| 選んだこと | 自前の AMI (機械の起動用の雛形) を持ち込む、同じスタックの作成の中で AMI を作る、既存クラスタへ GPU を足す、1 つのクラスタに複数の GPU のグループを足す、容量の選び方を選べる、GPU と EFA の資源が登録されて初めて成功とする、固定の版をできるだけ減らす、の 7 つの目標を立てた |
| 良い点 | 既存クラスタを持つ人も使える経路ができ、分離推論 (役割を機械ごとに分ける構成) のような複数グループの構成にも対応できる |
| 悪い点・代償 | (元の回答に代償の記述は無い) 補足: 自前の AMI の目標は、PR 開始時に EKS の AL2023 の NVIDIA の AMI のドライバ (GPU を動かすためのソフト) が g7 の GPU を認識せず、ノードが Ready なのに GPU を 1 つも出さなかった実際の隙間から来た |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["既にクラスタがある人には GPU を足す方法が無かった"] --> B["設計の目標を 7 つ決めた"]
  B --> C["自分の AMI を持ち込める、または作れる"]
  B --> D["GPU のグループを複数足せる"]
  B --> E["容量の選び方を選べる"]
  B --> F["GPU と EFA が登録されて初めて成功とする"]
  G["GPU をドライバが認識しない問題が実際にあった"] --> C
```

**Answer (English)**

Someone who already has an EKS cluster and a VPC and only wants GPUs had no path, neither in the template nor in the eksctl manifests. I set seven goals. Users can bring their own AMI, or build one within the same stack deployment. They can add GPUs to an existing cluster, and add more than one GPU node group to the same cluster, for example g7 and g7e for prefill and decode in disaggregated inference. They can choose On-Demand, a targeted ODCR, or a Capacity Block. The stack succeeds only once GPU and EFA resources are registered. And the repository should maintain as few pinned versions as possible. The custom AMI goal came from a real gap: when the PR started, the EKS AL2023 NVIDIA AMI didn't recognize g7 GPUs, and nodes went Ready while advertising none.

**回答 (日本語)**

既に EKS のクラスタと VPC を持っていて GPU だけを足したい人が使える経路が、テンプレートにも eksctl のマニフェストにも無かった。設計の狙いは 7 つである。自前の AMI を持ち込むか、同じスタックの作成の中で作れる。既存のクラスタに GPU を足せ、1 つのクラスタに GPU のノードグループを複数足せる (例えば分離推論で prefill と decode を g7 と g7e に分ける)。On-Demand、targeted の ODCR、Capacity Block から選べる。GPU と EFA の資源が登録されて初めてスタックが成功する。リポジトリが保守する固定の版をできるだけ少なくする。自前の AMI の狙いは実際の隙間から来た。PR を始めた時点では EKS の AL2023 の NVIDIA の AMI が g7 の GPU を認識せず、ノードは Ready なのに GPU を 1 つも出さなかった。
:::

:::details Q3. Why four child templates and a root, instead of more conditions in one?
**質問 (日本語)**

なぜ 1 つのテンプレートに条件を足さず、4 つの子と root に分けたのか。

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 論点 | 1 つの大きな設計図にまとめるか、複数の部品に分けるか |
| 選んだこと | ネットワーク・クラスタ・ノードグループ・AMI の 4 つの部品と、それらをまとめて作る root (まとめ役) に分けた |
| 良い点 | 一部だけ使いたい人は部品を直接使え、全部作る人は root に 1 回出すだけで済む。AMI 作りとクラスタ作りが同時に進む |
| 悪い点・代償 | 変更を試すときは、4 つの部品と root の計 5 つのテンプレートを先に置き場所 (S3) にアップロードしておく必要がある (手順は README に書いた) |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["1 つの大きな設計図にすると分岐が増えて読みにくい"] --> B["4 つの部品に分けた<br/>ネットワーク・クラスタ・ノードグループ・AMI"]
  B --> C["まとめて作る root を追加"]
  C --> D["全部作る人は root を 1 回出すだけ"]
  B --> E["一部だけ使う人は部品を直接使える"]
  B --> F["代償: 試すには 4 つの部品と root の 5 つのテンプレートを先に S3 に置く"]
```

**Answer (English)**

A single template is easy to read and publish, but supporting existing clusters, VPCs and AMIs adds branches, and parameters that matter in only one branch end up next to the ones that always matter. Independent templates without a root separate the concerns, but a full deployment then needs four stacks in order with outputs passed by hand. I chose four children and a root that creates them from S3 URLs, the same shape as the aws-pcs and HyperPod EKS roots. The trade-off is that testing a change means uploading all five templates to S3 first, which the README documents. The AMI stack depends only on the network, so the AMI build and cluster creation run in parallel.

**回答 (日本語)**

1 つのテンプレートは読むのも公開するのも楽だが、既存のクラスタ、VPC、AMI に対応するたびに分岐が増え、1 つの分岐でしか意味の無いパラメータが、いつも意味のあるものと並ぶ。root を作らずに子を独立させると関心は分かれるが、全部を作るには 4 つのスタックを順に作り、出力を手で渡す必要がある。4 つの子と、それらを S3 の URL から作る root にした。aws-pcs と HyperPod EKS の root と同じ形である。代償は、変更を試すには先に 5 つのテンプレートを S3 に置く必要があることで、手順は README に書いた。AMI のスタックはネットワークだけに依存させたので、AMI のビルドとクラスタの作成は並んで進む。
:::

:::details Q4. How do you manage the shared, cluster-scoped device plugin releases?
**質問 (日本語)**

クラスタに 1 つの共有の device plugin のリリースをどう扱うか。

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 論点 | 1 つのクラスタにプラグインごとに 1 つしかない共有の device plugin (GPU や EFA を使えるようにする部品、NVIDIA 用と EFA 用) のリリースの版を、誰が変えてよいか |
| 選んだこと | 無ければ入れる、同じ版なら何もしない、違う版ならスタックを失敗させる方針にした |
| 良い点 | 2 つ目のグループを足したときに、知らないうちに共有の device plugin の版が変わって 1 つ目のグループが壊れることを防げる |
| 悪い点・代償 | 版を上げるときは専用の手順を踏む必要があり、別の方法で入れた場合は見えない |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["直す前: 版を指定せず毎回アップグレード"] --> B["2 つ目のグループが共有の device plugin の版を知らぬ間に変える"]
  C["直した後: 無ければ入れ、同じ版なら触らず、違えば失敗させる"] --> D["知らぬ間の変更を防げる"]
  D --> E["代償: 版を上げるには先に共有の device plugin だけ上げる手順が要る"]
```

**Answer (English)**

There's one release per plugin per cluster, one for NVIDIA and one for EFA, so the question is who may change its version. #1259 ran `helm upgrade --install` without a version every time, so a second stack could silently change the version of the shared plugin the first group was already using. A release per node group doesn't restrict where the DaemonSet lands, so two copies of the same plugin would sit on the same nodes and conflict. Install-only-if-absent silently accepts a mismatch. I chose: install if absent, leave it alone if the version matches, fail the stack if it differs. The cost is an explicit upgrade procedure: upgrade the release first with Helm `--reset-then-reuse-values`, then align each stack; my first README order couldn't work because the existing stack would fail its own check, which review caught. I tested 0.20.0 to 0.20.1 and both stacks reached `UPDATE_COMPLETE`. Limit: a plugin installed another way, such as by the GPU Operator, isn't detected.

**回答 (日本語)**

リリースはクラスタにプラグインごとに 1 つ (NVIDIA 用と EFA 用) なので、問題は誰が版を変えてよいかである。#1259 は毎回、版を指定せずに `helm upgrade --install` を実行していたので、2 つ目のスタックが、1 つ目のグループも使っている共有のプラグインの版を黙って変えうる。ノードグループごとにリリースを分けても DaemonSet の載る先は分かれず、同じプラグインが 2 つ同じノードに載って競合する。無ければ入れるだけの方法は食い違いを黙って通す。採ったのは、無ければ入れ、同じ版なら触らず、違う版ならスタックを失敗させる方針である。代償は版を上げる手順が要ることで、Helm の `--reset-then-reuse-values` でリリースを先に上げ、その後で各スタックをそろえる。最初の README の順では既存のスタックが自分の確認で止まるので上げられない、とレビューで見つかった。0.20.0 から 0.20.1 への移行を試し、2 つのスタックがどちらも `UPDATE_COMPLETE` になった。限界は、GPU Operator など別の方法で入れたプラグインが見えないことである。
:::

:::details Q5. How does the template decide where the node AMI comes from, and what changes with a custom AMI?
**質問 (日本語)**

ノードの AMI をどこから持ってくるかをどう決め、自前の AMI だと何が変わるか。

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 論点 | ノードの土台となる機械の雛形 (AMI) をどこから持ってくるかをどう決めるか |
| 選んだこと | 「既にある AMI を使う」「標準にパッケージを足して作る」「自分のレシピから作る」の 3 つの入力のどれを埋めたかで決め、自前のときは起動の設定を自分で全部渡す |
| 良い点 | 利用者の持っているものに合わせて 3 通りから選べる |
| 悪い点・代償 | 自前の AMI では、EKS (クラスタの管理の仕組み) が自動で付けてくれる設定を自分で渡す必要がある。方法を選ぶ専用のパラメータにすると選択と入力が食い違う恐れがあるため、入力を埋めたかどうかで決める作りにした |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["AMI をどこから持ってくるか"] --> B["既にある AMI を使う"]
  A --> C["標準の土台にパッケージを足して作る"]
  A --> D["自分のレシピから作る"]
  A --> E["何も埋めなければ EKS が自動で選ぶ"]
  F["自前の AMI を選ぶと<br/>EKS が自動で付ける設定が付かなくなる"] --> G["ノード側の起動の設定で自分で渡す"]
```

**Answer (English)**

By which input is filled: `NodeAmiId` uses an existing AMI, `NodeImagePackages` builds one with EC2 Image Builder from the standard EKS AMI plus packages, and `NodeImageRecipeArn` builds from your own recipe. More than one is rejected before any resource is created; all empty lets EKS pick from `AmiType`. A named selector would read better but lets selector and inputs disagree. With a custom AMI, EKS stops adding the bootstrap config, so user data must carry the cluster endpoint, certificate, Service CIDR, containerd's runtime and kubelet flags. EKS's chosen IPv4 Service CIDR isn't a CloudFormation attribute, so I made `ServiceIpv4Cidr` an input on the cluster template, defaulting to `172.20.0.0/16`, outside the VPC default. And I add the `eks.amazonaws.com/nodegroup` label in kubelet flags, because only EKS AMIs get it automatically, and the readiness check counts nodes by it.

**回答 (日本語)**

どの入力を埋めたかで決める。`NodeAmiId` は既にある AMI を使い、`NodeImagePackages` は EKS の標準の AMI にパッケージを入れて EC2 Image Builder で作り、`NodeImageRecipeArn` は自分のレシピから作る。2 つ以上を埋めると作成の前に拒み、すべて空なら EKS が `AmiType` から選ぶ。方法の名前を選ぶパラメータのほうが読みやすいが、名前と入力が食い違いうる。自前の AMI では EKS が起動の設定を足さなくなるので、ユーザーデータにクラスタの endpoint、証明書、Service CIDR、containerd のランタイム、kubelet のフラグを持たせる。EKS が選んだ IPv4 の Service CIDR は CloudFormation の属性に無いので、クラスタのテンプレートで `ServiceIpv4Cidr` を入力にし、既定を VPC の既定の範囲の外の `172.20.0.0/16` にした。また `eks.amazonaws.com/nodegroup` のラベルを kubelet のフラグで付ける。EKS の AMI でしか自動で付かず、確認はこのラベルでノードを数えるからである。
:::

:::details Q6. Why exact GPU and EFA counts as the success condition, and not just Ready?
**質問 (日本語)**

なぜ成功の条件を Ready ではなく、GPU と EFA の数のちょうどの一致にしたのか。

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 論点 | ノードが「準備できた (Ready)」というだけで成功と言ってよいか |
| 選んだこと | グループのノードがちょうど指定した台数で、各ノードが準備できて (Ready)、期待したちょうどの数の GPU と通信用の部品 (EFA) を出していることまで確認して初めて成功とした |
| 良い点 | ドライバが GPU を認識しない隙間があっても、準備できただけで GPU が無いまま成功と判定する誤りを防げる |
| 悪い点・代償 | この確認は Kubernetes (ノードを管理する仕組み) への登録を見るだけで、GPU を使った計算 (CUDA) が実際に動くことまでは確認しない |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["ノードが準備できた (Ready) だけを見る"] --> B["GPU を 1 つも出さないノードも成功と判定してしまう"]
  C["準備できた、かつ<br/>期待したちょうどの数の GPU と通信用の部品を出す"] --> D["GPU と EFA が期待した数だけ登録されたことまで確認できる"]
  D --> E["代償: 登録の確認であり、計算が動くことまでは見ない"]
```

**Answer (English)**

Because Ready isn't enough: with the g7 driver gap, nodes were Ready and advertised no GPUs. When `GpuNodeCount` is nonzero, the stack succeeds only if the group has exactly that many nodes, each Ready and advertising exactly the expected `nvidia.com/gpu` and `vpc.amazonaws.com/efa` counts for its type, from the `GpuCount` and `NicLayout` mappings. #1259 accepted "nodes with nonzero GPU and EFA, at least `GpuNodeCount`", which passes an 8-GPU node advertising 1, ignores Ready, and passes instantly at 0. The boundary matters: this checks Kubernetes registration, not that CUDA works, and testing showed exactly that gap with Fabric Manager.

**回答 (日本語)**

Ready では足りないからである。g7 のドライバの隙間では、ノードは Ready なのに GPU を出さなかった。`GpuNodeCount` が 0 でなければ、グループのノードがちょうどその数で、各ノードが Ready で、`GpuCount` と `NicLayout` の対応表から決まるタイプごとの数の `nvidia.com/gpu` と `vpc.amazonaws.com/efa` をちょうど出すときだけ成功にした。#1259 は「GPU と EFA が 0 でないノードが `GpuNodeCount` 以上」で通していたので、8 GPU のタイプで 1 つしか出ないノードも数え、Ready を見ず、0 台ならすぐ通った。境界が大事で、この確認は Kubernetes の登録を見るもので、CUDA が動くことまでは見ない。試験ではまさにその隙間が Fabric Manager で現れた。
:::

### 不具合の原因の深掘り

:::details Q7. [Bug] On Kubernetes 1.34 the NVIDIA device plugin landed on no node. Why?
**質問 (日本語)**

Kubernetes 1.34 で NVIDIA の device plugin がどのノードにも載らなかった。なぜか。

**原因の連鎖 (図)**

```mermaid
flowchart TD
  A["device plugin の DaemonSet は 3 つのラベルのどれかを持つノードにだけ載る"]
  B["nvidia.com/gpu.present は nodeadm が付ける<br/>1.35 以降だけ、1.34 には無い"] --> I
  K["cpu-model.vendor_id=NVIDIA<br/>1.34 の GPU のノードには付いていなかった"] --> I
  D["親のチャートに tolerations を渡す"] --> E["値は親の DaemonSet にしか届かない"]
  E --> F["サブチャートの NFD は既定の toleration<br/>nvidia.com/gpu=present のまま"]
  G["GPU のノードの taint は nvidia.com/gpu=true"] --> H["NFD のワーカーが GPU のノードに載れない"]
  F --> H
  H --> C["pci-10de.present も付かない"]
  C --> I["どのラベルも無く、device plugin が載らない"]
  A --> I
  J["修正: nfd.worker.tolerations にも同じ toleration を渡す"]
```

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 原因 | GPU の有無を調べる仕組みが、GPU の載ったノードに入れなかったため、GPU を見つける印 (ラベル) が付かなかった |
| やったこと | GPU を見つける仕組み (NFD のワーカー) にも、GPU のノードに入るための許可 (toleration) を同じように渡すよう直した |
| 良い点 | 直した後、GPU を見つける印が付き、GPU を使うための部品がちゃんと載るようになった |
| 悪い点・代償 | 元の試験では GPU を 0 台で動かしていたため、この問題に気づかずに通ってしまっていた |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["GPU の有無を調べる仕組みが<br/>GPU の載ったノードに入れなかった"] --> B["GPU を見つける印が付かない"]
  B --> C["GPU を使うための部品が<br/>どのノードにも載らない"]
  D["直した後: 調べる仕組みにも GPU ノードへ入る許可を渡す"] --> E["印が付き、部品が載るようになった"]
```

**Answer (English)**

The NVIDIA device plugin DaemonSet only schedules onto nodes that have one of three labels. From 1.35, nodeadm adds `nvidia.com/gpu.present=true`, so 1.36 worked. On 1.34 that label doesn't exist, and the fallback label, `feature.node.kubernetes.io/pci-10de.present`, comes from the node-feature-discovery worker. The reviewer found and diagnosed this before merge. That worker couldn't schedule on GPU nodes: the template passed `tolerations` to the parent chart, but that value only reaches the parent's DaemonSet, not the bundled NFD subchart, which kept its default toleration for `nvidia.com/gpu=present`, while our taint is `nvidia.com/gpu=true`. The reviewer confirmed it by rendering with `helm template`, and saw GPUs appear within 20 seconds after adding the value by hand. The fix passes the same toleration to `nfd.worker.tolerations`. I verified on 1.34 with two g4dn.8xlarge that the NFD worker ran and the label appeared. #1259's end-to-end run used `GpuNodeCount=0`, which is why it never hit this.

**回答 (日本語)**

NVIDIA の device plugin の DaemonSet は、3 つのラベルのどれかを持つノードにだけ載る。1.35 以降は nodeadm が `nvidia.com/gpu.present=true` を付けるので 1.36 では載った。1.34 にはそのラベルが無く、代わりの `feature.node.kubernetes.io/pci-10de.present` は node-feature-discovery のワーカーが付ける。レビューした人がマージの前に見つけて原因を突き止めた。そのワーカーが GPU のノードに載れなかった。テンプレートは親のチャートに `tolerations` を渡していたが、その値は親の DaemonSet にしか届かず、同梱の NFD のサブチャートには届かない。サブチャートは自分の既定の toleration `nvidia.com/gpu=present` のままで、こちらの taint は `nvidia.com/gpu=true` だった。レビューした人が `helm template` で描いて確かめ、手で値を足すと 20 秒以内に GPU が出ることも見た。修正は同じ toleration を `nfd.worker.tolerations` にも渡すもので、1.34 と g4dn.8xlarge 2 台で NFD のワーカーが動きラベルが付くことを確かめた。#1259 の全体の実行は `GpuNodeCount=0` だったので、この問題に当たらなかった。
:::

:::details Q8. [Bug] What went wrong with Capacity Blocks and placement groups?
**質問 (日本語)**

Capacity Block と placement group で何がまずかったか。

**原因の連鎖 (図)**

```mermaid
flowchart TD
  A["launch template: MarketType capacity-block だけ"] --> B["ノードグループの CapacityType を書かない"]
  B --> C["既定の ON_DEMAND で作られる"]
  C --> D["EKS の文書にある CAPACITY_BLOCK と食い違う"]
  E["修正: CapacityType CAPACITY_BLOCK"] --> F["EKS が終わる 40 分前に 0 台へ縮める予定を入れる<br/>p5en のブロックで確かめた"]
  G["予約と placement group の両方を指定"] --> H["推論: 予約に空きがあっても<br/>2 台目が InsufficientInstanceCapacity になりうる"]
  H --> I["予約があるときは placement group を作らない"]
```

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 原因 | 予約した容量 (Capacity Block) を使う設定を起動の雛形 (launch template) にだけ書き、ノードグループ側に書き漏らしていたため、既定の「予約なし (ON_DEMAND)」の扱いになっていた |
| やったこと | ノードグループの設定にも正しく「予約を使う」という扱いを書き、予約を使うときは専用の配置のグループを作らないようにした |
| 良い点 | 予約の終わりが近づくと自動で台数を減らす予定が入るようになり、文書に書かれた通りの動きになった |
| 悪い点・代償 | 推論 (再現はしていない。レビューでは両方を指定しても 2 台とも起動した): 予約した容量は専用の配置のグループの中に無いので、2 台目が容量不足で失敗しうる。それを避けるため、配置の場所を予約側に委ねる代償がある |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["予約を使う設定を 1 か所にしか書いていなかった"] --> B["既定の「予約なし」の扱いになる"]
  B --> C["予約の終わりが近づいても<br/>台数を減らす予定が入らない"]
  D["直した後: 正しい場所にも予約を使う設定を書く"] --> E["予約の終わりに合わせて自動で台数を減らす"]
  F["推論: 予約と専用の配置を同時に使うと<br/>2 台目が容量不足で失敗しうる (再現はしていない)"] --> G["予約を使うときは専用の配置を作らない"]
```

**Answer (English)**

A must-fix from review: #1259 and my first version only set `MarketType: capacity-block` in the launch template and never set `CapacityType` on the node group, so it defaulted to `ON_DEMAND`. I didn't observe the old version through the end of a block; the bug is a mismatch with the EKS documentation, which describes `CAPACITY_BLOCK` for these node groups, and with it EKS schedules a scale-to-zero 40 minutes before the block ends. The fix sets it from the reservation type. On a p5en.48xlarge block I confirmed the stack completed after the block started, with 8 GPUs and 16 EFA, and that EKS scaled to zero 40 minutes before the end. Before the block starts, launches fail with `Capacity Reservation ... is not yet active` and retry, so the README records that as observed behavior. Separately, I stopped creating a placement group whenever a reservation is used. The reviewer reported both nodes launched with both set, so this is my reasoning, not a failure I reproduced: reserved capacity isn't in the stack's placement group, so a second node could fail with `InsufficientInstanceCapacity`. The trade-off is that the reservation decides placement.

**回答 (日本語)**

レビューでマージの前に直すべきとされた 1 件である。#1259 と最初の版は launch template に `MarketType: capacity-block` を入れるだけで、ノードグループに `CapacityType` を書かず、既定の `ON_DEMAND` になっていた。古い版をブロックの終わりまで動かして見たわけではなく、不具合は EKS の文書との食い違いである。文書はこのノードグループに `CAPACITY_BLOCK` を設定することを記載していて、これがあると EKS はブロックの終わる 40 分前に 0 台へ縮める予定を入れる。修正は予約の種類から入れる。p5en.48xlarge のブロックで、開始の後にスタックが GPU 8、EFA 16 で完了し、終わる 40 分前に EKS が 0 台にすることを確かめた。開始の前は起動が `Capacity Reservation ... is not yet active` で失敗して再試行されるので、README には観察した挙動として書いた。別に、予約を使うときは placement group を作らないようにした。レビューした人は両方を指定しても 2 台とも起動したと報告したので、これは再現した失敗ではなく推論である。予約した容量はスタックの placement group の中に無いので、2 台目が `InsufficientInstanceCapacity` で失敗しうる。代償は、置き場所を予約が決めることである。
:::

:::details Q9. [Bug] What broke when a second GPU node group joined the same cluster?
**質問 (日本語)**

同じクラスタに 2 つ目の GPU のノードグループを足すと、何が壊れたか。

**原因の連鎖 (図)**

```mermaid
flowchart TD
  A["PR 1259: 1 つのスタックに 1 つのクラスタが前提"] --> B["名前が gpu で固定、2 つ目がぶつかる"]
  A --> C["確認: role=gpu のノードを以上で数える"]
  C --> D["新しいグループが 0 台でも<br/>先のグループのノードで通る"]
  A --> E["事前取得の DaemonSet が kube-system/prepull で 1 つ"]
  E --> F["2 つ目が上書きし、1 つ目のノードにも<br/>新しいイメージを取りに行かせる"]
  A --> G["helm upgrade --install を版の指定なしで毎回"]
  G --> H["先のグループも使う共有の device plugin の版が変わる"]
  I["修正: NodeGroupName を入力、eks.amazonaws.com/nodegroup で数をちょうど比べる<br/>prepull-名前、版の不一致で失敗"]
```

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 原因 | 1 つのクラスタに 1 つのグループしか無いことを前提にした作りが、名前・数え方・事前の準備・共有の設定の 4 か所に残っていた |
| やったこと | 名前を選べるようにし、数え方をグループ専用の印で数え、事前の準備もグループごとの名前にした |
| 良い点 | 2 つ目のグループを足しても、1 つ目のグループに影響を与えずに済むようになった |
| 悪い点・代償 | 事前の準備の名前は小文字にし `_` を `-` に変えて作るので、`ng_B` と `ng-b` のように同じ名前になる場合があり、そのときは 2 つ目のスタックの事前の準備が始まらず警告が出るだけになる |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["1 つのクラスタに 1 つのグループしか無い前提の作り"] --> B["名前が固定で 2 つ目がぶつかる"]
  A --> C["数え方が別のグループのノードまで数えてしまう"]
  A --> D["事前の準備の仕組みが 1 つ目を上書きする"]
  E["直した後: 名前を選べるようにし<br/>グループ専用の印で数え、名前を分ける"] --> F["2 つ目を足しても 1 つ目に影響しない"]
```

**Answer (English)**

Everything that assumed one stack per cluster. The node group name was hard-coded as `gpu`, so a second one collided; `NodeGroupName` became a parameter with `gpu` as the default. The readiness check counted nodes labeled `role=gpu` with "at least", and every GPU group carries that label, so a new group could pass with zero nodes of its own using the other group's; it now selects by `eks.amazonaws.com/nodegroup` and requires exact counts. The pre-pull DaemonSet was one fixed name, `kube-system/prepull`, so a second stack overwrote it and sent the first group's nodes after the new image; the reviewer caught this, and it's now named per node group, lowercased with underscores turned into hyphens because DaemonSet names don't allow them. The trade-off there is that `ng_B` and `ng-b` map to the same name, in which case the second stack's pre-pull doesn't start and it warns. And unversioned `helm upgrade --install` could change the shared plugin's version, which Q4 covers.

**回答 (日本語)**

1 つのクラスタに 1 つのスタックを前提にしていたものすべてである。ノードグループの名前が `gpu` と書き込まれていたので 2 つ目がぶつかり、`NodeGroupName` を入力にして既定を `gpu` にした。確認は `role=gpu` のラベルのノードを「以上」で数えていて、どの GPU のグループもこのラベルを持つので、新しいグループは自分のノードが 0 台でも別のグループのノードで通り得た。今は `eks.amazonaws.com/nodegroup` で選び、数のちょうどの一致を求める。事前取得の DaemonSet は `kube-system/prepull` という 1 つの名前で、2 つ目のスタックが上書きし、1 つ目のグループのノードにも新しいイメージを取りに行かせた。レビューで見つかり、今はノードグループごとの名前で、DaemonSet の名前が許さないので小文字にし `_` を `-` に変える。その代償として `ng_B` と `ng-b` は同じ名前になり、その場合 2 つ目のスタックの事前取得は始まらず警告を出す。版を指定しない `helm upgrade --install` が共有のプラグインの版を変えうる件は Q4 で扱った。
:::

:::details Q10. [Bug] How could CloudFormation end up waiting with GPUs running and no answer?
**質問 (日本語)**

どうして、GPU が動いたまま答えが来ずに CloudFormation が待ち続けることがありえたのか。

**原因の連鎖 (図)**

```mermaid
flowchart TD
  A["PR 1259: install の段階でダウンロード"] --> B["失敗を知らせる trap は build の段階で設定"]
  B --> C["ダウンロードの失敗を誰も知らせない"]
  D["最初の版の send: curl の成否によらず<br/>送った印のファイルを作る"] --> E["post_build は印を見て何もしない"]
  C --> F["CloudFormation が応答を待ち続ける<br/>その間も GPU のノードは課金"]
  E --> F
  G["修正: すべて build へ、STAGE を記録、post_build でも失敗を送る<br/>ServiceTimeout 45 分、CodeBuild 40 分、送信が成功したときだけ印"]
```

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 原因 | 失敗を知らせる仕組みを設定する場所と、実際に失敗しうる作業をする場所がずれていて、失敗しても誰も知らせない道があった |
| やったこと | すべての作業を同じ場所にまとめ、知らせが本当に届いたときだけ「知らせた」という印を付けるようにした |
| 良い点 | 試したすべての失敗の経路で知らせを送るようになり、届かなくても待つ時間の上限を 45 分に縮めた |
| 悪い点・代償 | 知らせの届け先そのものが届かない状態なら、結局は上限の時間まで待つことになる |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["失敗を知らせる仕組みの設定場所と<br/>実際の作業場所がずれていた"] --> B["失敗しても誰にも知らせない"]
  C["最初の版は、送信が失敗しても「知らせた」という印を作っていた"] --> B
  B --> D["GPU が動いたまま答えが来ず待ち続ける"]
  E["直した後: 作業をまとめ、本当に届いたときだけ印を付ける"] --> F["待つ時間の上限も縮めた"]
```

**Answer (English)**

Lambda starts CodeBuild with the response URL, and CodeBuild installs, checks, and sends the response to CloudFormation itself, which avoids Lambda's 15-minute limit. The failure to design against is silence: CloudFormation keeps waiting while GPU nodes run and cost money.

There were two gaps. In #1259, downloads ran in the `install` phase but the failure `trap` was set in `build`, so a failed download produced no answer and the default one-hour wait. And in my first version, `send()` created its "responded" marker even when `curl` failed, so `post_build` skipped its fallback. The reviewer reproduced that against an unreachable URL: `curl` failed with "Failed to connect", `send` returned 0, and the marker existed.

I moved all work into `build` and record the failing stage, kept `post_build` as a fallback that reports failure, and now write the marker only after `curl --retry 3` succeeds. `ServiceTimeout: 2700` caps CloudFormation's wait at 45 minutes, and CodeBuild's timeout went from 60 to 40 minutes so `post_build` has time to answer before that cap. I tested the response path with a URL returning 403 and a correct one. If the response URL stays unreachable, nothing can arrive, and the 45-minute cap is what ends the wait.

**回答 (日本語)**

Lambda が応答先の URL を渡して CodeBuild を起動し、CodeBuild が導入と確認をして自分で CloudFormation に応答を送る。これで Lambda の 15 分の上限を避ける。防ぐべき故障は沈黙で、CloudFormation が待ち続ける間も GPU のノードは動き、料金がかかる。

抜けは 2 つあった。#1259 は `install` の段階でダウンロードし、失敗を知らせる `trap` は `build` の段階で設定していたので、ダウンロードが失敗すると答えが無く、既定の 1 時間を待った。そして最初の版の `send()` は、`curl` が失敗しても「送った」印を作ったので、`post_build` が予備の知らせを飛ばした。レビューした人は届かない URL で再現し、`curl` が "Failed to connect" で失敗し、`send` が 0 を返し、印ができていることを示した。

すべての処理を `build` に入れて失敗した段階を記録し、`post_build` を失敗を知らせる予備として残し、印は `curl --retry 3` が成功したときだけ作るようにした。`ServiceTimeout: 2700` で CloudFormation の待ちを 45 分までにし、CodeBuild の上限を 60 分から 40 分に縮めて、その上限の前に `post_build` が答える時間を残した。応答の経路は 403 を返す URL と正しい URL で試した。応答先に届かない状態が続けば何も届かないので、45 分の上限が待ちを終わらせる。
:::

:::details Q11. [Bug] A /18 VPC passed validation and then failed. Why?
**質問 (日本語)**

/18 の VPC が検査を通った後で失敗した。なぜか。

**原因の連鎖 (図)**

```mermaid
flowchart TD
  A["サブネット: !Cidr VpcCidr, 8, 12<br/>VPC を 8 つの /20 に切る"] --> B["/16 か /17 でしか 8 つ取れない"]
  C["最初の版の検査: CIDR の形だけ、/ の後の長さを縛らない"] --> D["/18 も通る"]
  D --> E["VPC を作り始めてから<br/>invalid count (8) of subnets requested で巻き戻る"]
  F["修正: AllowedPattern で /16 と /17 だけ"]
```

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 原因 | ネットワークの切り分け方に合う広さの確認が、形だけを見ていて広さそのものを縛っていなかった |
| やったこと | 許される広さを、切り分け方に合う 2 つの広さだけに絞った |
| 良い点 | 作り始める前に、合わない広さを弾けるようになった |
| 悪い点・代償 | 切り分け方を将来変えたら、この許される広さの確認も一緒に変える必要がある |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["ネットワークを 8 つに切り分ける作り"] --> B["合う広さは 2 通りだけ"]
  C["最初の確認は広さの形だけを見ていた"] --> D["合わない広さも通ってしまう"]
  D --> E["作り始めた途中で<br/>サブネットを 8 つ取れず失敗する"]
  F["直した後: 許す広さを合う 2 通りだけに絞る"] --> G["作る前に合わない広さを弾ける"]
```

**Answer (English)**

Subnets are carved with `!Cidr [VpcCidr, 8, 12]`, eight /20 blocks, which only fits in a /16 or /17 VPC. #1259 accepted any string, and my first version checked only the CIDR shape. I actually asked in the PR whether that might be too strict; the reviewer created stacks and showed the opposite: a /18 passes the check, then fails mid-deployment with `invalid count (8) of subnets requested`, an error that names neither the parameter nor the CIDR. The merged pattern allows only /16 and /17. The cost is coupling: if the subnet layout changes, this pattern must change with it.

**回答 (日本語)**

サブネットは `!Cidr [VpcCidr, 8, 12]` で 8 つの /20 に切り出し、これは /16 か /17 の VPC にしか収まらない。#1259 はどんな文字列も受け付け、最初の版は CIDR の形だけを見た。筆者は PR で、厳しすぎるのではと意見を求めたほどだった。レビューした人はスタックを作って逆を示した。/18 は検査を通り、デプロイの途中でパラメータの名前も CIDR も出ない `invalid count (8) of subnets requested` で失敗する。マージした形は /16 と /17 だけを通す。代償は結びつきで、サブネットの切り方を変えたらこの検査も一緒に変える必要がある。
:::

:::details Q12. [Bug] Review showed your lint passing broken templates. How?
**質問 (日本語)**

レビューで、lint が壊れたテンプレートを通すことが示された。どうしてか。

**原因の連鎖 (図)**

```mermaid
flowchart TD
  A["lint: 条件の名前の数字だけを読む<br/>Cards4Plus の 4"] --> B["条件の中身から 17 を消しても通る"]
  B --> C["p6-b300 でカード 2 枚が落ち<br/>EFA が 16 ではなく 14"]
  D["lint: taint のキーを部分一致で比べる"] --> E["nvidia.com/gpus に変えても通る"]
  F["修正: 各条件をすべての枚数で評価<br/>キーを抜き出して完全一致で比べる"]
  G["確かめ方: 狙った誤りを入れた写しで lint が落ちること"]
```

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 原因 | 確認の仕組みが、設定の中身を実際には見ずに名前の一部や部分一致だけで判定していた |
| やったこと | 設定の中身をすべての場合で実際に評価し、名前も完全に一致するかで確認するようにした |
| 良い点 | 狙った誤りを混ぜた写しに対して、確認の仕組みがちゃんと失敗することを確かめられた |
| 悪い点・代償 | (元の回答に代償の記述は無い) 補足: レビューした人がこのやり方で試し、2 つの確認が狙った誤りを見逃すことが分かった |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["確認の仕組みが名前の一部や部分一致だけで判定"] --> B["中身を書き換えても確認が通ってしまう"]
  C["直した後: カードの条件をすべての枚数で実際に評価する"] --> E["狙って誤りを混ぜた写しで<br/>確認がちゃんと失敗することを確かめた"]
  D["直した後: taint のキーは完全一致を求める"] --> E
```

**Answer (English)**

The reviewer tested whether each check actually catches the mistake it's for, which is the right way to test a linter. Two failed. The network card check read the number in a condition's name, the 4 in `Cards4Plus`, without evaluating the condition, so removing `"17"` from it still passed. p6-b300.48xlarge has 17 cards, and `Cards4Plus` gates card indices 2 and 3, the third and fourth cards, so without 17 in that list it would lose those two cards and advertise 14 EFA instead of 16. And the taint check compared by substring, so changing the key to `nvidia.com/gpus` still passed. Now lint evaluates every `CardsNPlus` condition for every card count and requires it to be true exactly at N or more, and it extracts every `nvidia.com/...` key and requires an exact match. For every check I added or tightened, I ran it against a copy with the targeted mistake and confirmed it fails.

**回答 (日本語)**

レビューした人は、各検査が狙った誤りを本当に見つけるかを試した。lint の正しい試し方である。2 つが落第だった。ネットワークカードの検査は、条件の名前の数字 (`Cards4Plus` の 4) を読むだけで条件を評価しなかったので、そこから `"17"` を消しても通った。p6-b300.48xlarge のカードは 17 枚で、`Cards4Plus` はカード番号 2 と 3 (3 枚目と 4 枚目) を決めるので、一覧から 17 が消えるとその 2 枚が落ち、EFA が 16 ではなく 14 になるのにである。taint の検査は部分一致で比べていたので、キーを `nvidia.com/gpus` に変えても通った。今の lint はすべての `CardsNPlus` の条件をすべての枚数について評価し、ちょうど N 枚以上で真になることを求め、`nvidia.com/...` のキーをすべて抜き出して完全一致を求める。足したり厳しくしたりした検査はすべて、狙った誤りを入れた写しに対して走らせ、失敗することを確かめた。
:::

:::details Q13. [Bug] Diagnostics always printed "unknown" for the GPU count. Why?
**質問 (日本語)**

診断が GPU の数をいつも「不明」と出していた。なぜか。

**原因の連鎖 (図)**

```mermaid
flowchart TD
  A["GPU を出さないノード: ハードウェアが無い、ドライバ、device plugin のどれか"] --> B["PCI の NVIDIA の数を kubectl debug で数える"]
  B --> C["最初の版は --attach が無い"]
  C --> D["Pod を作るだけですぐ戻り、出力を受け取らない"]
  D --> E["いつも不明、手がかりにならない"]
  F["修正: --attach=true、専用の名前空間、ノードの名前で後片付け"] --> G["PCI reports 8 accelerators ... advertises 0"]
```

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 原因 | 診断の命令に、結果を受け取るための設定が抜けていて、結果を見る前にすぐ戻ってしまっていた |
| やったこと | 結果を受け取る設定を付け、専用の場所で動かし、後片付けも間違えないようにした |
| 良い点 | GPU のハードウェアがあるのかどうかを、別の原因と見分けられるようになった |
| 悪い点・代償 | クラウド側 (EC2 の API) に直接聞く方法は、すでに何かが失敗している経路に権限・通信・呼び出し制限の心配を足すので使わなかった |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["診断の命令に結果を受け取る設定が無い"] --> B["結果を見る前にすぐ戻ってしまう"]
  B --> C["いつも「不明」としか出ない"]
  D["直した後: 結果を受け取る設定を付ける"] --> E["ハードウェアはあるが使えない、など原因を絞れる"]
```

**Answer (English)**

A node that advertises no GPUs could be missing hardware, have a driver that doesn't recognize it, or lack the device plugin, and from outside they look the same. So the diagnostic counts NVIDIA devices on the node's PCI bus with `kubectl debug`: if PCI shows GPUs but the resource is 0, the hardware is there and you look at the driver or plugin. My first version omitted `--attach`, so the command created the debug Pod and returned immediately without its output, and the count was always "unknown"; the reviewer saw that on a T4 node in the 1.34 run. It now uses `--attach=true`, runs in the stack's `nvidia-device-plugin` namespace, and cleans up by a name that includes the node, so it can't delete someone else's debug Pod in `default`. I confirmed on g4dn.8xlarge and p5en.48xlarge that it prints lines like `PCI reports 8 accelerator(s) ... the node advertises 0`. I avoided asking the EC2 API instead, because that adds IAM, network and throttling to a path where something has already failed.

**回答 (日本語)**

GPU を出さないノードには、ハードウェアが無い、ドライバが認識しない、device plugin が無い、の場合があり、外からは同じに見える。そこで診断は `kubectl debug` でノードの PCI の NVIDIA のデバイスを数える。PCI に GPU が見えて資源が 0 なら、ハードウェアはあるのでドライバかプラグインを調べればよい。最初の版は `--attach` を付けていなかったので、コマンドは診断の Pod を作るだけですぐ戻り、出力を受け取らず、数はいつも「不明」だった。レビューした人は 1.34 の実行で T4 のノードでそれを見た。今は `--attach=true` を付け、スタックの `nvidia-device-plugin` の名前空間で動かし、ノードの名前を含む名前で後片付けするので、`default` にある誰かの診断の Pod を消すことはない。g4dn.8xlarge と p5en.48xlarge で `PCI reports 8 accelerator(s) ... the node advertises 0` のような行が出ることを確かめた。EC2 の API に聞かなかったのは、すでに何かが失敗している経路に IAM、通信、スロットリングを足すことになるからである。
:::

:::details Q14. [Bug] Why set the GPU taint both in the node group and in kubelet flags?
**質問 (日本語)**

なぜ GPU の taint を、ノードグループの設定と kubelet のフラグの両方で付けるのか。

**原因の連鎖 (図)**

```mermaid
flowchart TD
  A["taint をノードグループの設定だけで付ける"] --> B["ノードの登録と taint の付与の間に隙間"]
  B --> C["参加から 7 秒後の標本: taint が無いのに<br/>nvidia.com/gpu: 1 を出している"]
  C --> D["taint は次の標本で付いた"]
  D --> X["推論: その隙間に GPU を求めない Pod が載りうる"]
  E["--register-with-taints は登録の時点で効く"] --> F["自前の AMI の分岐に残す"]
```

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 原因 | ノードが仲間に加わった直後から、GPU を求めない作業を近づけない目印が付くまでの間に、短いが隙間があった |
| やったこと | ノードグループの設定で付けるのに加え、自前の AMI の分岐では、仲間に加わる瞬間から効く付け方 (`--register-with-taints`) でも付けた |
| 良い点 | 仲間に加わった直後の短い隙間も塞げる |
| 悪い点・代償 | 目印を 2 か所に書くことになり、2 か所をそろえ続ける手間が増える |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["仲間に加わった直後"] --> B["目印がまだ付いていない短い隙間がある"]
  B --> C["推論: その間に GPU を求めない作業が入り込める"]
  D["直した後: 自前の AMI の分岐では加わる瞬間から効く付け方も使う"] --> E["隙間を塞ぐ"]
  E --> F["代償: 2 か所の設定をそろえ続ける必要がある"]
```

**Answer (English)**

The taint `nvidia.com/gpu=true:NoSchedule` keeps non-GPU Pods off GPU nodes, and it's set in the node group config. On the custom-AMI branch I also set `--register-with-taints`, because the flag applies at registration. I removed the flag and watched from registration: a sample 7 seconds after joining showed an untainted node already advertising `nvidia.com/gpu: 1`, and the taint arrived in the next sample, so a window exists. The PR doesn't record which branch that sample came from, so I claim the window can exist, not where. The reviewer's observation that the taint was present one second after creation came from the EKS-AMI path, whose user data has no such flag, so it shows that path is in time, not the flag's effect. The cost is keeping two declarations in sync.

**回答 (日本語)**

`nvidia.com/gpu=true:NoSchedule` の taint は GPU を求めない Pod を GPU のノードから外すためのもので、ノードグループの設定で付けている。自前の AMI の分岐では、登録の時点で効く `--register-with-taints` でも付ける。筆者がフラグを外して登録の直後から観察すると、参加から 7 秒後の標本で taint の無いノードが `nvidia.com/gpu: 1` を出していて、taint は次の標本で付いた。隙間があるということである。この標本をどちらの分岐で取ったかは PR に書いていないので、隙間がありうるとは言うが、どこにあるかまでは言わない。作成の 1 秒後から taint が付いていたというレビューの観察は EKS の AMI の経路のもので、その経路のユーザーデータにフラグは無いので、フラグの効果ではなく、その経路で間に合っていることを示す。代償は 2 か所の宣言をそろえ続ける手間である。
:::

:::details Q15. [Bug] A p5en.48xlarge node with your AMI passed the check but CUDA failed. Why?
**質問 (日本語)**

自前の AMI の p5en.48xlarge のノードは確認を通ったのに、CUDA が失敗した。なぜか。

**原因の連鎖 (図)**

```mermaid
flowchart TD
  A["標準の親の AMI と README の例のパッケージで AMI を作る"] --> B["NVSwitch でつなぐタイプには<br/>ドライバと同じ版の nvidia-fabricmanager が要る"]
  B --> C["パッケージに入っていない"]
  C --> D["スタックは CREATE_COMPLETE<br/>GPU は資源として出る"]
  D --> E["CUDA: system not yet initialized"]
  F["nvidia-fabricmanager を足す"] --> G["all_reduce_perf が完了"]
```

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 原因 | 確認の仕組みは GPU が登録されたことしか見ておらず、計算に必要な別の部品 (ドライバと組み合わせる専用のソフト) が入っていなかった |
| やったこと | 必要な部品を足して、計算が動くことを確かめた |
| 良い点 | 「登録された」と「本当に計算できる」が別の話だと分かり、テンプレートに組み込む別案は選ばず、手順書に書くことにした |
| 悪い点・代償 | この部品の版は利用者ごとのドライバに合わせる必要があり、テンプレートに入れるとリポジトリがその組み合わせの責任を持つことになるので避けた |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["確認の仕組みは GPU が登録されたことしか見ない"] --> B["登録はできても計算できない場合がある"]
  C["ある種のノードには<br/>計算用の専用の追加ソフトが要る"] --> B
  D["足りないソフトを足す"] --> E["計算が実際に動くことを確かめた"]
  E --> F["代償: このソフトの版は利用者ごとに合わせる必要があり<br/>テンプレートには入れず手順書に書いた"]
```

**Answer (English)**

This is the exact boundary of my success check. The stack reached `CREATE_COMPLETE` and the GPUs were registered, but CUDA failed with `system not yet initialized`. p5en uses NVSwitch, which needs `nvidia-fabricmanager` at the same version as the driver, and the README's example package list didn't include it. After adding it, NCCL's `all_reduce_perf` completed. I deliberately documented it in the README instead of adding it to the template, because the Fabric Manager version has to match each user's driver, and putting it in the template would make the repository own that pairing. The general lesson: resource registration and compute readiness are different claims, so test both on the AMI and instance type you'll actually use.

**回答 (日本語)**

これはまさに成功の確認の境界である。スタックは `CREATE_COMPLETE` になり GPU も登録されたが、CUDA が `system not yet initialized` で失敗した。p5en は NVSwitch を使い、ドライバと同じ版の `nvidia-fabricmanager` が要るが、README の例のパッケージの一覧に入っていなかった。足すと NCCL の `all_reduce_perf` が完了した。テンプレートに足さず README に書いたのはわざとである。Fabric Manager の版は利用者ごとのドライバに合わせる必要があり、テンプレートに入れるとその組み合わせをリポジトリが持つことになる。一般的な教訓は、資源の登録と計算できることは別の主張なので、実際に使う AMI とインスタンスタイプで両方を試すことである。
:::

:::details Q16. How did you decide which versions to pin and which to expose, and what did review find?
**質問 (日本語)**

どの版を固定し、どれをパラメータにするかをどう決め、レビューで何が見つかったか。

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 論点 | どの版を固定し、どれを利用者が選べるようにするか |
| 選んだこと | クラスタの作業に使う道具は試した版を既定にしつつ選べるようにし、GPU 用の部品の版は既定で固定した |
| 良い点 | クラスタの版を変えるたびにテンプレートを直す必要が無くなり、版ごとの対応表のように誰かが行を足すまで新しい版を待つことも無い |
| 悪い点・代償 | 選べるようにした値が実際のクラスタと食い違っても止める仕組みは無い (lint は既定の版どうしの差しか見ない)。また、レビューで root に kubectl の版を渡す入力が無い抜けが見つかり、今は root から 4 つの版を渡すように直した |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["道具の版をコードに書き込む作り"] --> B["クラスタの版が変わるたびに書き換えが必要"]
  C["直した後: 道具の版を選べるパラメータにする"] --> D["新しい版が出てもすぐ使える"]
  E["GPU 用の部品の版は、選べるパラメータにしつつ既定で固定する"] --> F["正しさがドライバとの組み合わせで決まるため"]
  C --> G["途中で配線の抜けが見つかった:<br/>選んだ値がノードグループまで届いていなかった"]
```

**Answer (English)**

By who knows the right value and what correctness depends on. #1259 hard-coded kubectl and Helm in the buildspec, so every cluster version change needed a template edit; a per-version Mappings table would block new EKS versions until someone added a row. kubectl and Helm became parameters with tested defaults; lint only checks that the default kubectl is within one minor version of the default Kubernetes version; nothing compares `KubectlVersion` with the real cluster at deploy time. The device plugin charts are parameters but pinned by default, because unpinned means effectively `latest`, and correctness depends on the driver. Review found the wiring gap: the docs said the root controls the kubectl version, but the root had no `KubectlVersion`, so a 1.34 cluster ran kubectl 1.36.4, two minor versions ahead. The root now passes all four, `KubectlVersion`, `HelmVersion` and the two chart versions, and the 1.34 test confirmed the value reached the node group.

**回答 (日本語)**

正しい値を誰がよく知っているかと、正しさが何で決まるかで決めた。#1259 は kubectl と Helm を buildspec に書き込んでいたので、クラスタの版を変えるたびにテンプレートの修正が要った。版ごとの Mappings の表は、誰かが行を足すまで新しい EKS の版を止める。kubectl と Helm は試した版を既定にしたパラメータにし、lint は kubectl と Kubernetes の既定のマイナーバージョンの差が 1 以内であることだけを見て、デプロイの時に実際のクラスタと比べる仕組みは無い。device plugin のチャートもパラメータだが既定で固定した。固定しないと実質 `latest` で、正しさはドライバとの組み合わせで決まるからである。レビューでは配線の抜けが見つかった。文書は root で kubectl の版を決めると書いていたのに root に `KubectlVersion` が無く、1.34 のクラスタで、マイナーバージョンが 2 つ先の kubectl 1.36.4 が動いた。今は root が 4 つ (`KubectlVersion`、`HelmVersion`、2 つのチャートの版) を渡し、1.34 の試験で値がノードグループに届くことを確かめた。
:::

:::details Q17. CloudFormation has no loops. How did you handle up to 32 network cards, and why does lint guard the file size?
**質問 (日本語)**

CloudFormation には繰り返しが無い。最大 32 枚のネットワークカードをどう扱い、なぜ lint がファイルの大きさを見張るのか。

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 論点 | 繰り返しの仕組みが無い中で、枚数の違う通信用の部品をどう並べるか。また大きさが膨らんだときにどう気づくか |
| 選んだこと | 枚数ごとの一覧を生成する専用の仕組みを作り、貼った内容と生成した内容が違えば確認を失敗させる。大きさについても、上限に近づいたら確認を失敗させるようにした |
| 良い点 | 枚数の種類が少ないことを利用して、しきい値だけで必要な分を正しく並べられる。大きさの上限に近づいたことに早く気づける |
| 悪い点・代償 | 生成の仕組みと貼った内容を手で合わせ続ける必要があり、以前は大きさを表示するだけで、実際に上限を超えて壊れたことがあった |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["繰り返しの仕組みが無いので<br/>通信用の部品を枚数分すべて書く必要がある"] --> B["専用の生成する仕組みで作り、貼り付ける"]
  B --> C["貼った内容と生成した内容が違えば確認を失敗させる"]
  D["ファイルの大きさに上限がある"] --> E["以前は大きさを表示するだけで上限超えに気づけなかった"]
  E --> F["直した後: 上限に近づいたら確認自体を失敗させる"]
```

**Answer (English)**

EFA needs one interface per network card, from 1 to 32 depending on type. The launch template lists 32 entries and drops missing ones with `AWS::NoValue`. Only seven card counts exist, so I replaced #1259's 31 conditions with six "N or more" thresholds and generate the 32 entries with an 81-line script pasted between markers; `Fn::ForEach` would keep one source, but expanding into list elements was rejected with a duplicate-key error. Lint fails if the pasted block differs from the generator. On size: the README deploys the node group with `--template-body`, limited to 51,200 bytes, so people adding GPUs to an existing cluster don't need a bucket. Adding version parameters pushed the file to 51,540 bytes and broke that, and lint only printed the size. Now lint fails above 95%, parameter details moved to `docs/PARAMETERS.md`, and the merged file is 47,807 bytes.

**回答 (日本語)**

EFA はネットワークカード 1 枚にインターフェースを 1 つ要し、枚数はタイプで 1 枚から 32 枚まで変わる。launch template には 32 枚分を並べ、無い分は `AWS::NoValue` で消す。枚数は 7 通りしか無いので、#1259 の 31 個の条件を「N 枚以上」の 6 個のしきい値にまとめ、32 枚分は 81 行の生成器で作って印の間に貼った。`Fn::ForEach` なら元が 1 つで済むが、リストの要素として展開すると重複キーのエラーで拒まれた。貼った記述が生成器と違えば lint が失敗する。大きさについては、README は既存のクラスタに GPU を足す人がバケットを作らずに済むよう、ノードグループを `--template-body` で出し、これは 51,200 バイトまでである。版のパラメータを足したらファイルが 51,540 バイトになってこれが壊れ、lint は大きさを表示するだけだった。今は 95% を超えたら lint が失敗し、パラメータの詳細は `docs/PARAMETERS.md` に移し、マージした版は 47,807 バイトである。
:::

### 検証と振り返り

:::details Q18. What did you test, and what did you not test?
**質問 (日本語)**

何を試し、何を試していないか。

**Answer (English)**

After review, all 11 lint checks passed with credentials. I created the root on Kubernetes 1.34 with two g4dn.8xlarge, and on 1.35 and 1.36. On two g7.12xlarge the EFA procedure passed: `fi_pingpong` exited 0 and the EFA counters rose by 2,720,004 bytes. A p5en.48xlarge Capacity Block completed with 8 GPUs and 16 EFA. A node group alone was added to a 1.35 cluster made with eksctl. A private-endpoint-only cluster was rejected without `BootstrapVpcId` and completed with it. Deleting all stacks completed. Not tested: the default g7e.12xlarge, and types other than p5en with four or more network cards, because the regions I checked had no capacity; the reviewer did create g7e.12xlarge with a reservation. `SecondaryDeviceIndex` for p6-b300 is unverified, and lint says so. Some review fixes, like the FSx client ingress rule, were preventive: the failure wasn't reproduced with or without the rule.

**回答 (日本語)**

レビューの後の版で、lint は認証情報を与えて 11 の検査が失敗 0 だった。root は Kubernetes 1.34 と g4dn.8xlarge 2 台、1.35、1.36 で作成した。g7.12xlarge 2 台で EFA の手順が通り、`fi_pingpong` が終了コード 0 で、EFA の計数が 2,720,004 バイト増えた。p5en.48xlarge の Capacity Block は GPU 8、EFA 16 で完了した。eksctl で作った 1.35 のクラスタにノードグループだけを足せた。private だけの endpoint のクラスタは `BootstrapVpcId` が無いと拒まれ、あると完了した。すべてのスタックの削除も完了した。試していないのは、既定の g7e.12xlarge と、ネットワークカードが 4 枚以上の p5en 以外のタイプで、確かめたリージョンに容量が無かった。g7e.12xlarge はレビューした人が予約を使って作成した。p6-b300 の `SecondaryDeviceIndex` は未検証で、lint もそう言う。FSx のクライアントの受信の規則のように、レビューの修正の一部は予防のもので、規則があってもなくても失敗は再現していない。
:::

:::details Q19. What are the known limitations, and what would you change next?
**質問 (日本語)**

既知の限界は何で、次に何を変えるか。

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 論点 | 名前やチェックの仕組みを「1 つのテンプレートの中だけ」で考えた設計の名残が、どこに限界として残っているか |
| 選んだこと | 代表的な限界をそのまま認め、回避の手順を手順書に書く形で済ませ、直すのは次の課題にした |
| 良い点 | 利用者が困ったときに、回避の手順を手順書で追えるようにしておいた |
| 悪い点・代償 | ノードグループの名前を固定した作りのままだと AMI の種類を切り替えるだけで名前の衝突により失敗し、グループを消した後も共有の設定や後片付けされない部品が残る |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["ノードグループの名前を固定したまま"] --> B["AMI の種類を切り替えると名前がぶつかり失敗する"]
  C["この確認は自分のテンプレートの範囲しか見ない"] --> D["他の方法で入れた部品は見えない"]
  E["(別の限界) グループのスタックを消しても<br/>共有のプラグインのリリース、事前の準備の仕組み、access entry が残る"]
  B --> F["名前の衝突と access entry の消し方は README に回避の手順を書いた"]
  E --> F
  D --> D2["次の課題: ノードに載っているものでプラグインを見つける"]
```

**Answer (English)**

The root fixes the node group name to `gpu`, so switching between the EKS AMI and a custom AMI on a root-created stack changes `AmiType`, forces a replacement, and fails with `NodeGroup already exists` while the old group still holds the name; the README says to recreate the stack, or to change `NodeGroupName` in the same update on a standalone stack. The device plugin check only sees this template's release names, so a plugin installed by the GPU Operator isn't detected. After deleting a node group stack, the shared plugin releases, an orphaned pre-pull DaemonSet, and the access entry EKS created for the node role remain; the README shows how to remove the access entry. And Fabric Manager stays a README step. Next, I'd look at passing `NodeGroupName` through the root and detecting plugins by what's scheduled on the nodes rather than by release name.

**回答 (日本語)**

root はノードグループの名前を `gpu` に固定しているので、root で作ったスタックで EKS の AMI と自前の AMI を切り替えると `AmiType` が変わって置き換えが起き、古いグループが名前を持っている間は `NodeGroup already exists` で失敗する。README は、root ではスタックを作り直し、単独のスタックでは同じ更新で `NodeGroupName` を変えるよう書いている。device plugin の確認はこのテンプレートのリリース名しか見ないので、GPU Operator が入れたものは見えない。ノードグループのスタックを消した後は、共有のプラグインのリリース、どのノードにも合わない事前取得の DaemonSet、EKS がノードのロールに作った access entry が残り、README は access entry の消し方を示している。Fabric Manager は README の手順のままである。次は、root から `NodeGroupName` を渡すことと、リリース名ではなくノードに載っているものでプラグインを見つけることを考える。
:::

:::details Q20. A new GPU instance type launches. What do you change?
**質問 (日本語)**

新しい GPU のインスタンスタイプが出た。何を変えるか。

**噛み砕き (ひとことで)**

| 項目 | 内容 |
|---|---|
| 論点 | 新しい種類の GPU の機械が出たとき、設計のどこを変える仕組みにしておくか |
| 選んだこと | 許可するインスタンスタイプと 2 つの対応表に新しい種類を追加し、確認の仕組みで表どうしの一致と (認証情報があれば) クラウド側の実際の情報を比べる。ネットワークカードの枚数が新しければ、その枚数のしきい値を足して一覧を生成し直して貼る。ドライバが新しい GPU を認識しない間は自前の AMI の経路を使う |
| 良い点 | 新しい種類が出ても、決まった手順で対応表と生成の仕組みを更新するだけで対応できる |
| 悪い点・代償 | ドライバがまだ新しい GPU を認識しない期間は自前の AMI に頼ることになり、それでも GPU が出ないノードは成功と判定しない |

**図 (やさしい版)**

```mermaid
flowchart TD
  A["新しい種類の GPU の機械が出た"] --> B["許可する種類と 2 つの対応表に追加する"]
  B --> C["確認の仕組みが表の一致を見る"]
  B --> D["ネットワークカードの枚数が新しければ<br/>しきい値を足して一覧を生成し直す"]
  A --> E["ドライバがまだ認識しない間は<br/>自前の AMI の経路を使う"]
  E --> F["GPU が出ないノードは成功と判定しない"]
```

**Answer (English)**

Add the type to the allowed instance types and the two mappings, `NicLayout` for network cards and EFA and `GpuCount` for the exact GPU check; lint checks those lists agree and, with credentials, compares `NicLayout` with `ec2:DescribeInstanceTypes`. If the card count is new, add a `CardsNPlus` threshold for it, regenerate the block with `tests/render-nic-block.py`, and paste it between the markers; lint then evaluates every threshold for every card count, including the new one. And if the EKS AMI's driver doesn't recognize the new GPU yet, that's what the custom AMI paths are for: g7 was the case that motivated them, and the readiness check fails the stack instead of reporting nodes that advertise no GPUs.

**回答 (日本語)**

許可するインスタンスタイプと、2 つの対応表 (ネットワークカードと EFA の `NicLayout`、GPU の数をちょうど比べる `GpuCount`) に足す。lint はこれらの一致を確かめ、認証情報があれば `NicLayout` を `ec2:DescribeInstanceTypes` と比べる。カードの枚数が新しければ、その枚数の `CardsNPlus` の条件を足し、`tests/render-nic-block.py` で記述を生成し直して印の間に貼る。lint は新しい枚数も含めて、すべてのしきい値の条件を評価する。EKS の AMI のドライバがまだ新しい GPU を認識しないなら、自前の AMI の経路がそのためにある。g7 がそれを作るきっかけで、確認は GPU を出さないノードを成功と報告せず、スタックを失敗させる。
:::

## まとめ

前章の PR を、全体と設計、不具合の原因の深掘り、検証と振り返りの順に、20 の問で整理した。全体の要点は Q1 にまとめた。設計の中心は、1 つのクラスタに複数のノードグループを足す作り (Q4、Q9) と、CloudFormation への応答の確実さ (Q10) である。不具合の問は、症状、原因の連鎖、突き止め方、直し方と比べた案、検証、残ったこと、の順に整理した。
