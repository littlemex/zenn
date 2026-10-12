---
title: "Awsome: EKS の GPU クラスタを CloudFormation で作る (技術面接の FAQ)"
free: false
---

## はじめに

本章は、前章「Awsome: EKS の GPU クラスタを CloudFormation で作る」の [PR #1269](https://github.com/awslabs/awsome-distributed-ai/pull/1269) について、技術面接で聞かれたときに答えるための想定問答をまとめたものである。前章の本文が Zenn の 1 章あたりの文字数の上限に近いので、想定問答を別の章に分けた。

## 技術面接の準備 (FAQ)

この節は、前章の内容を技術面接で聞かれたときに答えるための想定問答である。システム設計面接の流れ (要件の確認、全体の設計、深掘り、トレードオフ、故障と検証) に沿って並べ、各問は英語の質問、日本語の質問、英語の回答、日本語の回答の順に書いた。回答は前章に書いた事実だけを使う。

:::details Q1. What were the requirements, and what design goals did you set?
**質問 (日本語)**

要件は何で、どんな設計の狙いを置いたか。

**Answer (English)**

The gap was that someone who already has an EKS cluster and a VPC and only wants to add GPUs had no path, neither in the template nor in the eksctl manifests. #1259's single template built everything from the VPC up. I set seven design goals. Users can bring their own AMI, or build one within the same stack deployment. They can add GPUs to an existing cluster, and add more than one GPU node group to the same cluster, for example g7 and g7e for prefill and decode in disaggregated inference. They can choose On-Demand, a targeted ODCR, or a Capacity Block. The stack succeeds only once GPU and EFA resources are registered. And the repository should maintain as few pinned versions as possible.

**回答 (日本語)**

既に EKS のクラスタと VPC を持っていて GPU だけを足したい人が使える経路が、テンプレートにも eksctl のマニフェストにも無かった。#1259 の 1 つのテンプレートは VPC から全部を作った。設計の狙いは次の 7 つである。自前の AMI を持ち込める。1 回のスタックの作成で AMI まで作れる。1 つのクラスタに GPU のノードグループを複数足せる (例えば分離推論で prefill と decode を g7 と g7e に分ける)。容量の取り方を On-Demand、targeted の ODCR、Capacity Block から選べる。既存のクラスタに足せる。GPU と EFA の資源の登録をスタックの成功の条件にする。リポジトリが保守する版を少なくする。
:::

:::details Q2. Why split one template into four child templates and a root, instead of adding conditions to one?
**質問 (日本語)**

なぜ 1 つのテンプレートに条件を足さず、4 つの子と root の計 5 つに分けたのか。

**Answer (English)**

I compared three options. A single template is easy to read and publish, but supporting existing clusters, VPCs and AMIs adds branches, and parameters that matter in only one branch end up in the same list as the ones that always matter. Independent templates without a root separate the concerns, but a full deployment needs four stacks created in order, with outputs passed by hand. I chose four child templates and a root that creates them from S3 URLs. Users deploy everything through the root, or use a child directly. This follows the aws-pcs and HyperPod EKS roots. The trade-off is that testing a change means uploading all five templates to S3 first, which the README documents.

**回答 (日本語)**

3 つを比べた。1 つのテンプレートに条件を足す方法は、読むファイルも公開するファイルも 1 つで済むが、持ち込めるもの (既存のクラスタ、VPC、AMI) が増えるたびに分岐が増え、1 つの分岐でしか意味の無いパラメータが、いつも意味のあるパラメータと同じ一覧に並ぶ。root を作らずに子を独立させる方法は、分かれてはいるが、全部を作りたい人は 4 つのスタックを順に作り、出力を手で渡す必要がある。採ったのは、子に分けて root が S3 の URL から作る形である。全部を作る人は root を 1 つ出すだけで、一部を持ち込む人は子を直接使える。aws-pcs と HyperPod EKS の root と同じ形にそろえた。代償は、変更を試すには先に 5 つを S3 に置く必要があることで、手順は README に書いた。
:::

:::details Q3. What broke when you allowed multiple GPU node groups on one cluster?
**質問 (日本語)**

1 つのクラスタに GPU のノードグループを複数足せるようにすると、何がぶつかったか。

**Answer (English)**

Five things, each of which assumed one stack per cluster. The node group name was hard-coded as `gpu`, so I made `NodeGroupName` a parameter with `gpu` as the default. The readiness check counted nodes labeled `role=gpu` with "at least", so a new group could pass using another group's nodes; it now selects by `eks.amazonaws.com/nodegroup` and requires an exact count. The device plugin Helm releases, one for NVIDIA and one for EFA, are cluster-scoped and shared, so a second stack could silently upgrade them under the first group. The image pre-pull DaemonSet had one fixed name, so a second stack overwrote it; it's now named per node group. And the access entry that lets CodeBuild act on the cluster moved from the cluster stack to the node group stack, so a standalone node group brings its own permission and a cluster without GPUs doesn't keep admin access.

**回答 (日本語)**

5 か所で、どれも 1 つのクラスタに 1 つのスタックを前提にしていた。ノードグループの名前が `gpu` と書き込まれていたので、`NodeGroupName` を入力にし、既定を `gpu` にした。確認は `role=gpu` のラベルのノードを「以上」で数えていたので、新しいグループが別のグループのノードで通り得た。今は `eks.amazonaws.com/nodegroup` で自分のグループだけを選び、数のちょうどの一致を求める。device plugin の Helm のリリースは NVIDIA 用と EFA 用がクラスタに 1 つずつで共有なので、2 つ目のスタックが 1 つ目のグループの下で黙って版を上げ得た。イメージの事前取得の DaemonSet は名前が 1 つに固定で、2 つ目のスタックが上書きしたので、ノードグループごとの名前にした。CodeBuild がクラスタを操作するための access entry はクラスタの側からノードグループの側へ移し、単独で作るノードグループが自分の権限を持ち込み、GPU を使わないクラスタに管理者の権限が残らないようにした。
:::

:::details Q4. How do you manage a shared, cluster-scoped Helm release across many stacks?
**質問 (日本語)**

多数のスタックの間で、クラスタに 1 つの共有の Helm のリリースをどう扱うか。

**Answer (English)**

The question is who may change its version. I compared four policies. Reinstalling every time lets a later stack change the plugin under existing groups. A release per node group doesn't restrict where the DaemonSet lands, so two copies of the same plugin end up on the same nodes and conflict. Installing only if absent silently accepts a version mismatch. I chose: install if absent, leave it alone if the version matches, and fail the stack if it differs. The cost is an explicit upgrade procedure: upgrade the release first with Helm `--reset-then-reuse-values`, then align each stack. I tested 0.20.0 to 0.20.1 that way, and both stacks reached `UPDATE_COMPLETE`. The limit: a plugin installed another way, such as by the GPU Operator, isn't detected.

**回答 (日本語)**

問題は、誰が版を変えてよいかである。4 つの方針を比べた。毎回入れ直すと、後から足したスタックが既存のグループの下でプラグインを変える。ノードグループごとに別のリリースにしても、DaemonSet が載る先は分かれないので、同じプラグインが 2 つ同じノードに載って競合する。入っていれば何もしない方法は、版の食い違いを黙って通す。採ったのは、無ければ入れ、同じ版なら触らず、違う版ならスタックを失敗させる方針である。代償は版を上げる手順が要ることで、Helm の `--reset-then-reuse-values` でリリースを先に上げ、その後で各スタックの指定をそろえる。0.20.0 から 0.20.1 への移行をこの手順で試し、2 つのスタックがどちらも `UPDATE_COMPLETE` になった。限界は、GPU Operator など別の方法で入れたプラグインが見えないことである。

追撃されたら: 最初の README の手順 (既存のスタックの版を先に上げる) は、そのスタック自身が確認で止まるので上げられない、とレビューで指摘されて直した。
:::

:::details Q5. Why is "GPU and EFA registered" the success condition, and not just node Ready?
**質問 (日本語)**

なぜ成功の条件を、ノードの Ready ではなく GPU と EFA の登録にしたのか。

**Answer (English)**

Because Ready isn't enough. When this PR started, the EKS AL2023 NVIDIA AMI's driver didn't recognize g7 GPUs; nodes went Ready but advertised no GPUs. So when `GpuNodeCount` is nonzero, the stack succeeds only if the group has exactly that many nodes, each Ready and advertising exactly the expected `nvidia.com/gpu` and `vpc.amazonaws.com/efa` counts for its instance type. #1259 accepted "nodes with nonzero GPU and EFA, at least `GpuNodeCount`", which passes an 8-GPU node advertising 1, ignores Ready, and passes instantly at 0. I'm explicit about the boundary: this checks Kubernetes resource registration, not that CUDA or training works. That boundary showed up in testing: an AMI built from packages on p5en.48xlarge reached `CREATE_COMPLETE`, but CUDA failed with `system not yet initialized` until `nvidia-fabricmanager` was added.

**回答 (日本語)**

Ready では足りないからである。この PR を始めた時点では、EKS の AL2023 の NVIDIA の AMI のドライバが g7 の GPU を認識せず、ノードは Ready でも GPU を出さなかった。そこで `GpuNodeCount` が 0 でなければ、グループのノードがちょうどその数で、各ノードが Ready で、インスタンスタイプに合った数の `nvidia.com/gpu` と `vpc.amazonaws.com/efa` をちょうど出すときだけ成功にした。#1259 は「GPU と EFA が 0 でないノードが `GpuNodeCount` 以上」で通していたので、8 GPU のタイプで 1 つしか出ないノードも数え、Ready を見ず、0 台ならすぐに通った。境界ははっきり言う。この確認は Kubernetes の資源の登録を見るもので、CUDA や学習が動くことまでは見ない。この境界は試験でも現れた。p5en.48xlarge でパッケージから作った AMI はスタックが `CREATE_COMPLETE` になったが、`nvidia-fabricmanager` を足すまで CUDA が `system not yet initialized` で失敗した。
:::

:::details Q6. How did you make sure CloudFormation always gets an answer from the custom resource?
**質問 (日本語)**

custom resource から CloudFormation に必ず答えが届くように、どうしたか。

**Answer (English)**

Lambda starts CodeBuild and passes it the response URL. CodeBuild installs and checks, then sends the response to CloudFormation itself, which avoids Lambda's 15-minute limit. The failure to design against is silence: CloudFormation keeps waiting while GPU nodes run and cost money. In #1259, downloads ran before the failure trap was set, so a failed download left CloudFormation waiting the default hour. I moved all work into the `build` phase and record the failing stage. `post_build` reports failure if the trap missed it. `ServiceTimeout: 2700` caps CloudFormation's wait for the response at 45 minutes, and the CodeBuild timeout went from 60 to 40 minutes to leave margin under that cap. Review also caught that `send()` marked the response as sent even when `curl` failed; it now retries three times and marks only on success. What I tested is the response path, with a URL returning 403 and with a correct one, not the CodeBuild timeout path itself; there the 45-minute cap is the backstop.

**回答 (日本語)**

Lambda が CodeBuild を起動して応答先の URL を渡し、CodeBuild が導入と確認をして自分で CloudFormation に応答を送る。これで Lambda の 15 分の上限に縛られない。設計で防ぐべき故障は沈黙で、CloudFormation が待ち続ける間も GPU のノードは動き、料金がかかる。#1259 は失敗を知らせる `trap` を設定する前にダウンロードしていたので、ダウンロードが失敗すると CloudFormation は既定の 1 時間を待った。すべての処理を `build` の段階に入れ、失敗した段階を記録するようにした。`trap` が拾えなかった失敗は `post_build` が失敗として答える。`ServiceTimeout: 2700` で CloudFormation が応答を待つのを 45 分までにし、CodeBuild の上限を 60 分から 40 分に縮めて、その上限の内側に余裕を残した。レビューでは、`send()` が `curl` の失敗に関係なく「送った」印を作っていたことも見つかり、今は 3 回まで再試行し、成功したときだけ印を作る。試したのは応答の経路 (403 を返す URL と正しい URL) で、CodeBuild の時間切れの経路そのものは試していない。そこでは 45 分の上限が最後の止め役になる。
:::

:::details Q7. How does the template decide where the node AMI comes from?
**質問 (日本語)**

ノードの AMI をどこから持ってくるかを、テンプレートはどう決めるのか。

**Answer (English)**

By which input is filled. `NodeAmiId` uses an existing AMI, `NodeImagePackages` builds one with EC2 Image Builder by adding packages to the standard EKS AMI, and `NodeImageRecipeArn` builds from your own recipe. Filling more than one is rejected before any resource is created; leaving all empty lets EKS pick the AMI from `AmiType`. I compared this with a named selector like `NodeImageSource=recipe`, which reads better in the console but lets you write contradictions, such as choosing recipe while also filling packages, that need another rule to stop. Deciding from the inputs avoids any mismatch between a separate selector and the inputs. The cost is that the console doesn't name the method and the template has more conditions. For the same reason the comma-separated input is a `String`, not a `CommaDelimitedList`: the rules test emptiness, and a list can't be tested for being empty.

**回答 (日本語)**

どの入力を埋めたかで決める。`NodeAmiId` は既にある AMI を使い、`NodeImagePackages` は EKS の標準の AMI にパッケージを入れて EC2 Image Builder で作り、`NodeImageRecipeArn` は自分のレシピから作る。2 つ以上を埋めると作成の前に拒み、すべて空なら EKS が `AmiType` から AMI を選ぶ。比べたのは `NodeImageSource=recipe` のように方法の名前を選ぶパラメータで、画面で読みやすいが、「レシピ」を選んでパッケージも埋めるような矛盾が書けてしまい、それを止める規則が別に要る。入力から決めれば、選ぶパラメータと入力の食い違いは起こらない。代償は、画面で方法が明示されず、テンプレートの中の条件が増えることである。同じ理由で、カンマ区切りの入力は `CommaDelimitedList` ではなく `String` にした。規則は空かどうかで書いてあり、リストは空かどうかを調べられないからである。
:::

:::details Q8. What changes when a node uses a custom AMI?
**質問 (日本語)**

ノードが自前の AMI を使うと何が変わるか。

**Answer (English)**

When the launch template specifies an AMI, EKS stops adding the bootstrap config to user data, so the node has to receive everything itself: cluster endpoint, certificate, Service CIDR, containerd's default runtime, and kubelet flags. containerd config goes in user data rather than the image because nodeadm rewrites it on every boot. The Service CIDR is why I made `ServiceIpv4Cidr` an input on the cluster template: if EKS chooses it, the IPv4 value isn't a CloudFormation attribute, so the root can't pass it to a custom-AMI node. The trade-off is that users must keep it from overlapping the VPC CIDR; the default `172.20.0.0/16` sits outside the VPC default. Also, the `eks.amazonaws.com/nodegroup` label that EKS adds on its own AMI isn't added on a custom AMI, so I add it in kubelet flags, otherwise the readiness check couldn't count the nodes.

**回答 (日本語)**

launch template が AMI を指定すると、EKS はユーザーデータに起動の設定を足さなくなるので、ノードはクラスタの endpoint、証明書、Service CIDR、containerd の既定のランタイム、kubelet のフラグをすべて自分で受け取る必要がある。containerd の設定をイメージではなくユーザーデータに書くのは、nodeadm が起動のたびに書き直すからである。Service CIDR のために、クラスタのテンプレートで `ServiceIpv4Cidr` を入力にした。EKS に選ばせると IPv4 の値が CloudFormation の属性に無く、root から自前の AMI のノードへ渡せないからである。トレードオフは、利用者が VPC の CIDR と重ならないよう気を付ける必要があることで、既定値の `172.20.0.0/16` は VPC の既定の範囲の外に置いた。また、EKS の AMI なら EKS が付ける `eks.amazonaws.com/nodegroup` のラベルが自前の AMI では付かないので kubelet のフラグで付ける。付けないと確認がノードを数えられない。
:::

:::details Q9. Why set the GPU taint in two places?
**質問 (日本語)**

なぜ GPU の taint を 2 か所で付けるのか。

**Answer (English)**

The taint `nvidia.com/gpu=true:NoSchedule` keeps non-GPU Pods off GPU nodes, and it's set in the node group config. On the custom-AMI branch I also set `--register-with-taints`, because the flag applies at registration. With the flag removed, a sample 7 seconds after joining showed an untainted node already advertising `nvidia.com/gpu: 1`, and the taint arrived in the next sample. The PR doesn't record which branch that sample came from, so it shows the window can exist, not which path has it. The cost is keeping two declarations in sync.

**回答 (日本語)**

`nvidia.com/gpu=true:NoSchedule` の taint は GPU を求めない Pod を GPU のノードから外すためのもので、ノードグループの設定で付けている。自前の AMI の分岐では、登録の時点で効く kubelet のフラグ `--register-with-taints` でも付ける。フラグを外して観察すると、参加から 7 秒後の標本で taint の無いノードが `nvidia.com/gpu: 1` を出していて、taint は次の標本で付いた。この標本をどちらの分岐で取ったかは PR に書いていないので、その隙間がありうることは示すが、どの経路にあるかまでは示さない。代償は 2 か所の宣言をそろえ続ける手間である。

追撃されたら: レビューの観察 (作成の 1 秒後から taint が付いていた) は EKS の AMI の経路のもので、フラグの効果ではなく、その経路で taint が間に合っていることを示す。
:::

:::details Q10. How does the template handle Capacity Blocks and reservations?
**質問 (日本語)**

Capacity Block と予約を、テンプレートはどう扱うか。

**Answer (English)**

`CapacityReservationId` and `CapacityReservationType` select the mode. The reviewer caught a must-fix: #1259 and my first version only set `MarketType: capacity-block` in the launch template, so the node group defaulted to `ON_DEMAND`. EKS documents `CAPACITY_BLOCK` for these node groups, and with it EKS schedules a scale-to-zero 40 minutes before the block ends. The merged version sets it from the reservation type, and I confirmed on a p5en.48xlarge block that the stack completed after the block started and EKS scaled to zero 40 minutes before the end. Before the block starts, launches fail with `Capacity Reservation ... is not yet active` and retry, so the README says to create the stack after the start or a few minutes before, as observed behavior, not a rule. Separately, I stopped creating a placement group when a capacity reservation is used. The reviewer had been unsure whether the two could be combined, but reported that both nodes did launch. I still judged that specifying both could leave a second node failing with `InsufficientInstanceCapacity` while the reservation had room. That's my reasoning, not a failure I reproduced. The trade-off is that the reservation, not a placement group, decides placement.

**回答 (日本語)**

`CapacityReservationId` と `CapacityReservationType` で方式を選ぶ。マージの前に直すべき 1 件をレビューで指摘された。#1259 と最初の版は launch template に `MarketType: capacity-block` を入れるだけで、ノードグループは既定の `ON_DEMAND` で作られていた。EKS の文書はこのノードグループに `CAPACITY_BLOCK` を求め、これがあると EKS はブロックの終わる 40 分前に 0 台へ縮める予定を入れる。マージした版は予約の種類から入れ、p5en.48xlarge のブロックで、開始の後にスタックが完了し、終わる 40 分前に EKS が 0 台にすることを確かめた。開始の前は起動が `Capacity Reservation ... is not yet active` で失敗して再試行されるので、README には決まりではなく観察した挙動として、開始の後か数分前に作ればよいと書いた。別に、予約を使うときは placement group を作らないようにした。レビューした人は、両方を指定してよいか不安だったが実際には 2 台とも起動した、と報告した。それでも筆者は、両方を指定すると予約に空きがあっても 2 台目が `InsufficientInstanceCapacity` で失敗しうると考えた。これは推論で、失敗を再現したわけではない。代償は、置き場所を placement group ではなく予約が決めることである。
:::

:::details Q11. CloudFormation has no loops. How did you handle up to 32 network cards per instance?
**質問 (日本語)**

CloudFormation には繰り返しが無い。インスタンスあたり最大 32 枚のネットワークカードをどう扱ったか。

**Answer (English)**

EFA needs one interface per network card, from 1 to 32 depending on instance type. So the launch template lists 32 entries and drops missing ones with `AWS::NoValue`. Only seven card counts exist, so I replaced #1259's 31 conditions with six "N or more" thresholds, and generate the 32 entries with an 81-line script pasted between markers. `Fn::ForEach` would have kept one source, but expanding into list elements was rejected with a duplicate-key error. Lint fails if the pasted block differs from the generator output, and it now evaluates each condition for every card count.

**回答 (日本語)**

EFA はネットワークカード 1 枚にインターフェースを 1 つ要し、枚数はインスタンスタイプで 1 枚から 32 枚まで変わる。そこで launch template には 32 枚分を並べ、無いカードの分は `AWS::NoValue` で消す。枚数は 7 通りしか無いので、#1259 の 31 個の条件を「N 枚以上」の 6 個のしきい値にまとめ、32 枚分の記述は 81 行の生成器で作って印の間に貼った。`Fn::ForEach` なら元の記述が 1 つで済むが、リストの要素として展開すると重複キーのエラーで拒まれた。貼った記述と生成器の出力が違えば lint が失敗し、今の lint は各条件をすべての枚数について評価する。

追撃されたら: 最初の lint は条件の名前の数しか読んでおらず、`Cards4Plus` から `"17"` を消して p6-b300 でカードが 2 枚落ちても通る、とレビューで示されて直した。
:::

:::details Q12. How did you decide which versions to pin and which to expose?
**質問 (日本語)**

どの版を固定し、どの版をパラメータにするかを、どう決めたか。

**Answer (English)**

By who knows the right value and what correctness depends on. #1259 hard-coded kubectl and Helm in the buildspec, so every cluster version change needed a template edit and review. A Mappings table per Kubernetes version needs a new row before anyone can deploy a new EKS version. I made kubectl and Helm parameters with the tested versions as defaults, so a new EKS release needs no template change; lint only checks that the default kubectl version is within one minor version of the default Kubernetes version; nothing compares `KubectlVersion` with the real cluster at deploy time. The device plugin charts are parameters too, but stay pinned by default, because an unpinned chart is effectively `latest` and correctness depends on the node's driver. The reviewer found the root didn't pass `KubectlVersion`, so a 1.34 cluster ran kubectl 1.36.4, two versions ahead; the root now passes all four, `KubectlVersion`, `HelmVersion` and the two chart versions, so root-created stacks can follow the upgrade procedure too.

**回答 (日本語)**

正しい値を誰がよく知っているかと、正しさが何で決まるかで決めた。#1259 は kubectl と Helm を buildspec に書き込んでいたので、クラスタの版を変えるたびにテンプレートの修正とレビューが要った。Kubernetes の版ごとの対応表 (Mappings) は、誰かが行を足すまで新しい EKS の版にデプロイできない。kubectl と Helm はパラメータにして試した版を既定にし、EKS が新しい版を出してもテンプレートを変えずに済むようにした。lint が確かめるのは、kubectl と Kubernetes の既定の版の差が 1 つ以内であることだけで、デプロイの時に `KubectlVersion` を実際のクラスタと比べる仕組みは無い。device plugin のチャートもパラメータだが、既定では固定した。版を書かないと実質 `latest` になり、正しく動くかはノードのドライバとの組み合わせで決まるからである。レビューで、root が `KubectlVersion` を渡しておらず、1.34 のクラスタで kubectl が 2 つ新しい 1.36.4 で動いていたことが見つかった。今は root が 4 つ (`KubectlVersion`、`HelmVersion`、2 つのチャートの版) とも渡し、root で作ったスタックも版を上げる手順に乗れる。
:::

:::details Q13. On Kubernetes 1.34 the device plugin landed on no node. Why?
**質問 (日本語)**

Kubernetes 1.34 で device plugin がどのノードにも載らなかった。なぜか。

**Answer (English)**

The NVIDIA device plugin DaemonSet only schedules onto nodes with one of three labels. On 1.35 and later, nodeadm adds `nvidia.com/gpu.present=true`, so 1.36 worked. On 1.34 that label was missing. The node-feature-discovery worker should have added `pci-10de.present`, but it couldn't be scheduled on our GPU nodes. We had overridden `tolerations` in the parent chart, but that setting didn't reach the bundled NFD subchart. NFD still tolerated `nvidia.com/gpu=present`, while our taint was `nvidia.com/gpu=true`. The reviewer confirmed it with `helm template`. The fix passes the same toleration to `nfd.worker.tolerations`, and I verified on 1.34 with g4dn.8xlarge that the NFD worker ran and the label appeared. #1259's own end-to-end run used `GpuNodeCount=0`, which is why it never hit this. Lesson: render with `helm template` and check tolerations per DaemonSet.

**回答 (日本語)**

NVIDIA の device plugin の DaemonSet は、3 つのラベルのどれかを持つノードにだけ載る。1.35 以降は nodeadm が `nvidia.com/gpu.present=true` を付けるので 1.36 では載った。1.34 にはそのラベルが無く、代わりに `pci-10de.present` を付けるはずの node-feature-discovery のワーカーも GPU のノードに載れなかった。親のチャートの `tolerations` を上書きしていたが、その値は同梱の NFD のサブチャートに届かず、サブチャートは自分の既定の toleration `nvidia.com/gpu=present` のままで、こちらの taint は `nvidia.com/gpu=true` だったからである。レビューした人が `helm template` で確かめた。修正は同じ toleration を `nfd.worker.tolerations` にも渡すもので、1.34 と g4dn.8xlarge で NFD のワーカーが動きラベルが付くことを確かめた。#1259 の全体の実行は `GpuNodeCount=0` だったので、この問題に当たらなかった。教訓は、`helm template` で描いて DaemonSet ごとの toleration を確かめることである。
:::

:::details Q14. Why does lint fail at 95% of a template size limit?
**質問 (日本語)**

なぜ lint はテンプレートの大きさの上限の 95% で失敗するのか。

**Answer (English)**

The CLI's `--template-body` accepts at most 51,200 bytes. The README lets people add GPUs to an existing cluster with `--template-body`, so they don't need an S3 bucket. Adding version parameters pushed the file to 51,540 bytes and broke that path, and the old lint only printed the size. The alternative was to let it grow and switch the README to `--template-url`, which allows up to 1 MB, but that forces a bucket on the simplest use case. I kept the bucket-free path, moved detailed parameter explanations to `docs/PARAMETERS.md`, and made lint fail above 95% so the next person learns from lint rather than from a failed deploy. The merged file is 47,807 bytes.

**回答 (日本語)**

CLI の `--template-body` は 51,200 バイトまでしか受け付けない。README は、既存のクラスタに GPU を足す人が S3 のバケットを作らずに済むよう `--template-body` で出す。版のパラメータを足したときにファイルが 51,540 バイトになってこの経路が壊れたが、それまでの lint は大きさを表示するだけだった。比べた方向は、大きくなるに任せて README を 1 MB まで置ける `--template-url` にすることだが、最も単純な使い方にバケットを強いることになる。バケット無しの経路を残し、パラメータの細かい説明を `docs/PARAMETERS.md` に移し、95% を超えたら lint を失敗させて、次の人がデプロイの失敗ではなく lint で気付けるようにした。マージした版のファイルは 47,807 バイトである。
:::

:::details Q15. What did you test, and what did you not test?
**質問 (日本語)**

何を試し、何を試していないか。

**Answer (English)**

After review, all 11 lint checks passed with credentials. I created the root on Kubernetes 1.34 with two g4dn.8xlarge, and on 1.35 and 1.36. On two g7.12xlarge the EFA procedure passed: `fi_pingpong` exited 0 and the EFA counters rose by 2,720,004 bytes. A p5en.48xlarge Capacity Block completed with 8 GPUs and 16 EFA. A node group alone was added to a 1.35 cluster made with eksctl. A private-endpoint-only cluster was rejected without `BootstrapVpcId` and completed with it. Deleting all stacks completed. I didn't test the default g7e.12xlarge, or types other than p5en with four or more network cards, because the regions I checked had no capacity; the reviewer did create g7e.12xlarge with a reservation. `SecondaryDeviceIndex` for p6-b300 is unverified, and lint reports it as unverified. I list untested paths in the PR, because reviewers start there.

**回答 (日本語)**

レビューの後の版で、lint は認証情報を与えて 11 の検査が失敗 0 だった。root は Kubernetes 1.34 と g4dn.8xlarge 2 台、1.35、1.36 で作成した。g7.12xlarge 2 台で EFA の手順が通り、`fi_pingpong` が終了コード 0 で、EFA の計数が 2,720,004 バイト増えた。p5en.48xlarge の Capacity Block は GPU 8、EFA 16 で完了した。eksctl で作った 1.35 のクラスタにノードグループだけを足せた。API の endpoint が private だけのクラスタは `BootstrapVpcId` が無いと拒まれ、あると完了した。すべてのスタックの削除も完了した。試していないのは、既定の g7e.12xlarge と、ネットワークカードが 4 枚以上の p5en 以外のタイプで、確かめたリージョンに容量が無かった。g7e.12xlarge はレビューした人が予約を使って作成した。p6-b300 の `SecondaryDeviceIndex` は未検証で、lint も未検証と報告する。試していない経路は PR に一覧で書く。レビューはそこから見るからである。
:::

:::details Q16. What are the known limitations, and what would you change next?
**質問 (日本語)**

既知の限界は何で、次に何を変えるか。

**Answer (English)**

Three that the chapter records. The root fixes the node group name to `gpu`, so switching between the EKS AMI and a custom AMI on a root-created stack collides with `NodeGroup already exists`; the README says to recreate the stack, or to change `NodeGroupName` in the same update on a standalone stack. The device plugin check only sees this template's release names, so a plugin installed by the GPU Operator isn't detected and a second one could be installed. And after deleting a node group stack, the shared device plugin releases, an orphaned pre-pull DaemonSet, and the access entry EKS created for the node role remain; the README shows how to remove the access entry. Beyond that, Fabric Manager is documented rather than added, because its version must match the user's driver. Those are the places I'd look at first.

**回答 (日本語)**

本章が記録している 3 つがある。root はノードグループの名前を `gpu` に固定しているので、root で作ったスタックで EKS の AMI と自前の AMI を切り替えると `NodeGroup already exists` でぶつかる。README は、root ではスタックを作り直し、単独のスタックでは同じ更新で `NodeGroupName` を変えるよう書いている。device plugin の確認はこのテンプレートのリリース名しか見ないので、GPU Operator が入れたものは見えず、2 つ目を入れうる。ノードグループのスタックを消した後は、共有の device plugin のリリース、どのノードにも合わない事前取得の DaemonSet、EKS がノードのロールに作った access entry が残り、README は access entry の消し方を示している。ほかに、Fabric Manager は版を利用者のドライバに合わせる必要があるので、テンプレートに足さず README に書いた。次に見るならこれらの箇所からである。
:::

:::details Q17. Give me a two-minute overview of this design.
**質問 (日本語)**

この設計を 2 分で説明してほしい。

**Answer (English)**

The goal was a CloudFormation path for EKS GPU clusters that also works when you already have a cluster and only want to add GPUs. I split one template into network, cluster, GPU node group and AMI templates, plus a root that creates them from S3 URLs, so a full build is one submission and partial adoption uses a child directly. The GPU node group template can be deployed many times on one cluster: names, readiness counts and pre-pull are per node group, while the cluster-scoped device plugin release is shared under an install-if-absent, fail-on-mismatch policy. AMIs come from an existing ID, a package list or a recipe, decided by which input is set. Capacity can be On-Demand, a targeted ODCR or a Capacity Block. The stack only succeeds when every node is Ready and advertises exactly the expected GPUs and EFA, and the custom resource is built so a failure is reported, with CloudFormation waiting at most 45 minutes for the bootstrap's response. It was tested on Kubernetes 1.34 to 1.36, a Capacity Block, an existing cluster and a private-only endpoint.

**回答 (日本語)**

狙いは、既にクラスタを持っていて GPU だけを足したい場合にも使える、EKS の GPU クラスタを作る CloudFormation の経路だった。1 つのテンプレートを、ネットワーク、クラスタ、GPU のノードグループ、AMI の 4 つと、それらを S3 の URL から作る root に分けた。全部を作るなら提出は 1 つで、一部だけ使うなら子を直接使う。GPU のノードグループのテンプレートは 1 つのクラスタに何度でも出せる。名前、確認の数え方、事前取得はノードグループごとで、クラスタに 1 つの device plugin のリリースは、無ければ入れ、版が違えば失敗させる方針で共有する。AMI は既にある ID、パッケージの一覧、レシピのどれかを、埋めた入力から決める。容量は On-Demand、targeted の ODCR、Capacity Block から選べる。スタックが成功するのは、すべてのノードが Ready で、期待する数の GPU と EFA をちょうど出したときだけで、custom resource は失敗も CloudFormation に知らせ、CloudFormation が bootstrap の応答を待つのは長くても 45 分になる作りにした。Kubernetes 1.34 から 1.36、Capacity Block、既存のクラスタ、private だけの endpoint で試した。
:::

:::details Q18. A new GPU instance type launches. What do you change?
**質問 (日本語)**

新しい GPU のインスタンスタイプが出た。何を変えるか。

**Answer (English)**

Three places. Add the type to the allowed instance types and to the two mappings, `NicLayout` for network cards and EFA, and `GpuCount` for the exact GPU check; lint checks that those lists agree and, with credentials, compares `NicLayout` with `ec2:DescribeInstanceTypes`. If the card count is new, regenerate the network card block and paste it; lint fails if the pasted block and the generator output differ. And if the EKS AMI's driver doesn't recognize the new GPU yet, that's exactly what the custom AMI paths are for: g7 was the case that motivated them, and the readiness check would fail the stack rather than report a node that advertises no GPUs.

**回答 (日本語)**

3 か所である。許可するインスタンスタイプと、2 つの対応表 (ネットワークカードと EFA の `NicLayout`、GPU の数をちょうど比べるための `GpuCount`) に足す。lint はこれらの一致を確かめ、認証情報があれば `NicLayout` を `ec2:DescribeInstanceTypes` と比べる。カードの枚数が新しければ、ネットワークカードの記述を生成し直して貼る。貼った記述と生成器の出力が違えば lint が失敗する。EKS の AMI のドライバがまだ新しい GPU を認識しないなら、自前の AMI の経路がまさにそのためにある。g7 がその経路を作るきっかけで、確認は GPU を出さないノードを成功と報告せず、スタックを失敗させる。
:::

## まとめ

前章の設計を、要件、全体の設計、深掘り、トレードオフ、故障と検証の順に、18 の問で答えられるようにした。最初に聞かれやすいのは Q17 の 2 分の概要説明で、掘り下げは Q3 から Q6 の、1 つのクラスタに複数のノードグループを足す設計と、CloudFormation への応答の確実さに集まりやすい。
