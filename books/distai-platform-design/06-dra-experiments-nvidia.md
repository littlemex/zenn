---
title: "NVIDIA の GPU で device plugin と DRA を実機で試す"
---

## はじめに

前章では、device plugin と DRA を両方扱うための設計を、文書の一次情報とこれまでの実測から組み立てた。しかし、その設計の骨格(ノードプールごとのラベルによる opt-in、Layer 1 を既定にする方針、広告する側の持ち主)と、GPU Operator を使わないという判断は、実機で確かめるまでは仮説にすぎない。この章は、[Issue #19](https://github.com/littlemex/distai-platform/issues/19) の実験計画のうち、小型 NVIDIA の GPU(`g7.2xlarge`)を使った E0 から E3 の結果を扱う。

結論から述べると、今までの書き方の Pod(`resources.limits` に `nvidia.com/gpu: 1` と書く Layer 1)は、device plugin のノードでも DRA のノードでも動いた。ただし、2 つの見逃せない落とし穴が見つかった。1 つは、同じノードで device plugin と DRA のドライバが両方動くと、1 枚の GPU が警告なしに 2 つの Pod へ同時に渡ることである。もう 1 つは、Layer 1 の Pod で Karpenter が DRA のノードを動的に起こすと、そのノードが「未初期化」のまま残り、空になっても片付かないことである。この 2 つの結果から、小型の GPU は device plugin を既定のままにし、DRA のプールは ResourceClaim を使う Layer 2 の Pod か、台数を固定したプールでだけ使うという判断に至った。

この章の結果はすべて、1 つのリージョン(eu-south-2)、1 つのインスタンスタイプ(`g7.2xlarge`、GPU 1 枚)、1 つの AMI バージョンと chart バージョンの組み合わせで、各条件をおおむね 1 回ずつ観測したものである。秒数は 1 回の観測値であり、ほかの型やバージョンに一般化した結論ではない。

<details>
<summary>実験環境</summary>

実験は、この目的のために作り、終了後に削除した専用の EKS クラスタ(リージョン eu-south-2)の上で行った。Amazon EKS 1.37(kubelet v1.37.1)、AMI `amazon-eks-node-al2023-x86_64-nvidia-1.37-v20261112`、NVIDIA ドライバ 595.91.07、Karpenter 1.15.2、Kueue 0.20.2、`nvidia-device-plugin` chart 0.20.2、`dra-driver-nvidia-gpu` chart 0.5.1、インスタンスは `g7.2xlarge` の spot を使った。起動は 6 回で、6 回とも spot で成功し、3 回は AZ `eus2-az1`、3 回は `eus2-az2` だった。かかった時間は約 1 時間 43 分、費用は推定で 2.5 から 3 ドル(上限は 40 ドル)である。実施日は 2026-11-18 で、起動を試みるたびに、インスタンスタイプ、容量の種類、Availability Zone ID を起動の記録(launch ledger)に残した。DRA のドライバの chart では GPU 1 枚単位の割り当て(既定で off)を values で明示的に有効にしている(`gpuResourcesEnabledOverride: true` と `resources.gpus.enabled: true`)。実験を流したのは、実装やレビューとは別の検証担当のセッションである。

</details>

## 1. 実験の見取り図

E0 から E3 の 4 つの実験について、確かめたかった問い、測り方、結果、その結果を詳しく扱う節を次の表にまとめる。

| # | なぜ | どう測ったか | 結果 | 詳しく扱う節 |
| --- | --- | --- | --- | --- |
| E0 | 2 つの chart がどのノードに乗るかを知る | chart の既定値を読み、実際のノードのラベルと突き合わせた | AL2023 の NVIDIA AMI がノードに `nvidia.com/gpu.present=true` を付け、両方の chart が既定でそれを選ぶため、何もしないと全 GPU ノードで両方が重なる | 2 節 |
| E1 | 同じ Pod が両方の方式で動くか | 同じ Pod を device plugin のノードと DRA のノードに置き、`nvidia-smi` を見た | 両方で動いた。わざと二重に入れたときは、同じ GPU の UUID が 2 つの Pod に渡った | 3 節 |
| E2 | Karpenter が 0 台から DRA のノードを起こせるか | プールを 0 台にして Pod を出し、NodeClaim の状態を見た | Layer 1 の Pod は 47 秒で Running になったが、ノードは未初期化のまま 5 分以上残った。Layer 2 の Pod は 48 秒で Running になってノードは初期化され、空にすると 145 秒で消えた(47 秒と 48 秒は Pod を出してから Running までの時間であり、どの秒数も 1 回の観測値である) | 4 節 |
| E3 | Kueue が方式によらず同じ名前で数えるか | `deviceClassMappings` と、プールごとの flavor で Layer 1 と Layer 2 のジョブを出した | どちらも `nvidia.com/gpu: 1` として正しく数えられたが、Layer 2 のジョブが device plugin の flavor に入って止まった | 5 節 |

## 2. 何もしないと device plugin と DRA のドライバが同じノードで重なる(E0)

NVIDIA の device plugin の chart も、DRA のドライバの chart も、既定では「GPU があるノード」を自分で選んで乗る。その目印になっているのが、AL2023 の NVIDIA AMI がノードに自分で付けているラベル `nvidia.com/gpu.present=true` である。このラベルは両方の chart の既定の affinity に一致するため、何も手を加えずに両方を入れると、すべての GPU ノードで device plugin と DRA のドライバが同時に動いてしまう。

検証に入る前は「ノードフィーチャーディスカバリのような、目印を付ける部品が入っていないので、どちらの chart もどこにも乗らないはずだ」という見立てだった。実際のノードのラベルを確認したところ、この見立ては誤りで、AMI 自身がラベルを付けていたと判明した。この訂正により、少なくともこの AMI バージョン(`v20261112`)と chart バージョンの組み合わせでは、既定のままだと必ず重なりが起きることが分かった。重なりを避けることは必須であり、前章の決定 2(方式をノードプールごとのラベルで分けること)は、できれば採用したい選択肢から、重なりを避けるために必要な対策へと位置づけが変わった。また、DRA のドライバの chart は `extendedResourceName: nvidia.com/gpu` を持つ DeviceClass `gpu.nvidia.com` を自分で作って配る。基盤側で同じ役割の DeviceClass を別に用意する必要は無い。Karpenter は、方式によらず `g7.2xlarge` を `nvidia.com/gpu=1` と見積もる。この事実は 4 節の E2 と第 7 章の比較で効いてくる。

## 3. 二重に入れると、同じ GPU が警告なしに 2 つの Pod に渡る(E1)

今までの書き方の Pod は、device plugin のノードでも DRA のノードでも動いた。device plugin のノードでは allocatable に `nvidia.com/gpu: 1` が見え、Pod の中で `nvidia-smi` が通ることを確認した。DRA のノードでは allocatable に GPU は出ず、代わりに `pcieRoot` を持つ `ResourceSlice` が見え、Pod の `limits` を満たす自動作成の `ResourceClaim`(`<pod>-extended-resources-*`)が `gpu-0` を割り当てた。ここまでは前章の決定 3(Layer 1 を既定にする)を裏付ける結果である。また、DRA の chart が作った DeviceClass がある状態でも、device plugin のノードの Layer 1 の Pod は自動の claim を作らず(`extendedResourceClaimStatus` が空)、device plugin 経由で動いた。

次に、わざと device plugin を DRA 専用のつもりだったノードでも動かした。すると、そのノードの allocatable に `nvidia.com/gpu: 1` が現れ、device plugin 経由の Pod と、`ResourceClaim` を使う Layer 2 の Pod が、**同じ UUID を持つ GPU を同時に受け取った**。どこにも警告やイベントは出なかった。一方で、この状態でもう 1 つの Layer 1 の Pod を出すと、こちらは正しく拒否された。今回試した組み合わせ(GPU 1 枚のノード、1 通りの順序)の中では、壊れたのは device plugin 経由と `ResourceClaim` 経由が交差するときだけだった。

<!-- FIG name="06-e1-double-allocation" kind="flow" shows="device plugin (should not run here) and the DRA driver both advertise the same node; kubelet hands the same GPU UUID to Pod A via the device plugin and Pod B via a ResourceClaim, with no warning" labels="device plugin, DRA driver, kubelet, Pod A (limits), Pod B (ResourceClaim), same GPU UUID, no warning" -->
![同じ GPU の UUID が device plugin 経由と DRA 経由の 2 つの Pod に渡る様子を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/06-e1-double-allocation.png)

今回の観測で外から見えた兆候は、DRA のドライバが動いているはずのノードの allocatable に、GPU の数が 0 より大きく出ていることだけだった。この壊れ方は警告にもイベントにも現れず、ノードの allocatable でしか気付けないため、前章で採用を決めた読むだけの検査ツール(決定 1(d))には、「DRA のドライバが動くノードで、対応する extended resource の allocatable が 0 より大きい」という検査を必ず入れることにした。一方で、個別の chart と AMI 由来のドライバだけでここまで動いたことから、GPU Operator は無くても足りると判断した。ただし確かめたのは、GPU 1 枚の `g7.2xlarge` で `nvidia-smi` が通り、割り当てが動くところまでである。ドライバの更新、監視、time-slicing、大型の `p5` 系の構成は比べていない。

## 4. Karpenter が起こしたノードが未初期化のまま残る(E2)

DRA のプールをノード 0 台の状態にしてから Pod を出すと、Layer 1 の Pod では Karpenter が起こしたノードが未初期化のまま残って片付かず、Layer 2 の Pod ではノードが初期化されて、空になると片付いた。

<!-- FIG name="06-e2-node-states" kind="relation" shows="a Layer 1 pod on a dynamically provisioned DRA pool launches a node that stays uninitialized and is never consolidated, while a Layer 2 pod on the same pool launches a node that initializes and is consolidated after it empties" labels="Layer 1 pod, Layer 2 pod, DRA pool, Initialized=Unknown, Initialized=True, 47s, 48s, not consolidated after 5 min+, consolidated after 145s" -->
![Layer 1 と Layer 2 の Pod で、Karpenter が起こした DRA ノードの初期化状態が分かれる様子を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/06-e2-node-states.png)

Layer 1 の Pod を出すと、Karpenter はノードを起こし、Pod は 47 秒で Running になって GPU を使えた。しかし、そのノードの NodeClaim は `Initialized=Unknown`、理由は `ResourceNotRegistered: "nvidia.com/gpu"` のままで、Pod を手で削除した後も 5 分以上(`consolidateAfter` の設定は 2 分)片付かなかった。Karpenter の DRA 機能(`settings.enableDRA`)を有効にしても無効にしても、この結果は変わらなかった。一方、Layer 2 の Pod を `settings.enableDRA=true` で出すと、48 秒で Pod が Running になり、NodeClaim には `requested-dra-drivers: gpu.nvidia.com` が記録されて初期化され、Pod を消してから 145 秒後に正しく片付いた。

この違いは、Karpenter が「Pod の `limits` にある extended resource が、実は DeviceClass 経由で満たされている」ことを認識していないために起きると推測している。Karpenter はノードを `nvidia.com/gpu=1` と見積もって起こすが、DRA のノードの allocatable には GPU が出ないので、そのリソースの登録を待ったまま初期化を終えない、という筋である。Layer 2 の Pod では NodeClaim に `requested-dra-drivers` が記録され、Karpenter が待つ対象が extended resource ではなく DRA のドライバになるので初期化される、と考えられる。Pod 自体は動くので気付きにくいが、Karpenter が未初期化のノードを片付けの対象にしないため、空になった後も課金が続く。観測したのは 5 分以上(`consolidateAfter` の約 2.5 倍)までであり、最終的に片付くかどうかは見ていない。未初期化が片付けを止める理由として Karpenter が `DisruptionBlocked: Node isn't initialized` を出すことは、第 7 章の Neuron で確かめている。この結果から、小型の GPU では DRA のプールを動的に増減させる用途は Layer 2 の Pod に限り、Layer 1 の Pod を DRA で動かしたい場合は台数を固定したプールを使うという判断に至った。ただし NVIDIA の台数を固定したプールはこの実験では試しておらず、固定プールで動くことの実機の裏付けは、第 7 章の Neuron(Karpenter の alpha ゲート `StaticCapacity` が必要)だけである。前章の決定 4 で「小型 NVIDIA は device plugin を既定にする」とした根拠は、この E2 の結果である。

## 5. Kueue は正しく数えるが、Layer 2 を違う flavor に入れる(E3)

Kueue は、device plugin で動くジョブも DRA で動くジョブも同じ `nvidia.com/gpu` という名前で数えられたが、Layer 2 のジョブを device plugin の flavor に admit して止めてしまった。Kueue の `deviceClassMappings`(`gpu.nvidia.com` を `nvidia.com/gpu` として数える設定)を入れると、device plugin のノードで動く Layer 1 のジョブ、DRA のノードで動く Layer 1 のジョブ、DRA のノードで動く Layer 2 のジョブは、`flavorsUsage` の上でどれも `nvidia.com/gpu: 1` として数えられた。自動で作られる `ResourceClaim` が二重に数えられることも無かった。前章の決定 3 で述べた「枠の名前を方式によらず 1 つにする」という方針は、この結果で裏付けられている。

しかし、Layer 2 のジョブを素朴に流すと、空いていた device plugin の flavor に admit されてしまい、そこでは `ResourceClaim` を割り当てられず `cannot allocate all claims` というイベントを出して止まった。

<!-- FIG name="06-e3-kueue-flavor" kind="flow" shows="a Layer 2 job is admitted to the free device-plugin flavor and gets stuck unable to allocate claims; pinning karpenter.sh/nodepool to the DRA flavor fixes it" labels="Layer 2 job, device-plugin flavor, DRA flavor, cannot allocate all claims, karpenter.sh/nodepool pin" -->
![Layer 2 のジョブが device plugin の flavor に入って止まる様子と、flavor を固定した場合の違いを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/06-e3-kueue-flavor.png)

ClusterQueue には device plugin の flavor を先に並べており、Layer 2 のジョブはその先頭の flavor に入った。Pod の nodeSelector に方式のラベル(`distai-platform/nvidia-gpu-method: dra`)を書いても、Kueue は device plugin の flavor のノードラベル(`karpenter.sh/nodepool: g7-device-plugin`)を足し、Pod は Pending のままだった。Kueue v0.20 で alpha の `KueueDRADeviceFeasibility` は有効にしておらず、試していない。flavor のキー(`karpenter.sh/nodepool`)で DRA のプールに固定したときだけ、ジョブは正しく動いた。この結果を踏まえ、Issue #19 には「Layer 2 のワークロードは、DRA の flavor だけを持つ ClusterQueue に紐づく LocalQueue に流すか、flavor のキー(`karpenter.sh/nodepool`)を固定する」という規則を追加した。

## 6. 決め直したこと

実験の前に仮説として置いていた項目のうち、次の 6 点を実験の結果を受けて確定させた。

| 項目 | 実験の前 | 実験の後 |
| --- | --- | --- |
| 方式のラベルによる選択 | 決定 2 として提案していた | 必須である(既定のままでは全ノードで重なる) |
| 小型の GPU(`g6e`、`g7`) | device plugin を既定にし、結果で反転してよい | device plugin に確定した。DRA のプールは Layer 2 か台数固定で使う |
| GPU Operator | 個別の chart で足りれば外す | 外す(E1 で足りることを確かめた) |
| Kueue | 1 つの名前で数える | 成り立つ。Layer 2 は DRA 専用のキューに流す |
| 検査ツール | 二重や欠けを報告する | 「DRA のノードで allocatable が 0 より大きい」を必ず見る規則を追加した |
| 移行のカナリア | Layer 1 の Pod | Layer 2 の Pod にする(Layer 1 では Karpenter がノードを初期化しないため) |

ここでの「確定」は、`g7.2xlarge` とこのバージョンの組み合わせで観測した結果にもとづく既定値という意味である。`g6e` は起動していない。

## 7. ついでに見つかったこと

実験用のクラスタを `terraform destroy` で削除する際、Karpenter が管理する `EC2NodeClass` の片付けが止まった。同じ `terraform destroy` が先に NAT ゲートウェイと経路を削除し、Karpenter が AWS の API に到達できなくなったためである。この問題は、新しい基盤では `terraform destroy` がネットワークを削除する前に Karpenter が管理するオブジェクトを片付ける順序にする必要がある、という要件として Issue #17 に記録されている。

また、この実験とは無関係に、スペインのリージョン(eu-south-2)で `gds-probe-g7.2xlarge` という名前のインスタンスが 2026-11-08 から動き続けていることに気付いた。この実験の作業で作ったものではないため、調査や削除は行っていない。

## まとめ

小型 NVIDIA の GPU では、今までの Pod の書き方(Layer 1)は device plugin のノードでも DRA のノードでもそのまま動く。ただし、device plugin と DRA のドライバは必ずノードプールで分け、小型の GPU は device plugin を既定のままにし、DRA は `ResourceClaim` を使う Layer 2 の Pod か台数を固定したプールでだけ使うという結論に至った。次の章では、同じ観点を Trainium(`trn2.3xlarge`)で確かめた結果を扱う。
