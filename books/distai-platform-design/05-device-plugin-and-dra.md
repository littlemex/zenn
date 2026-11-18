---
title: "device plugin と DRA をどちらも扱うための設計"
---

## はじめに

GPU や Trainium を Kubernetes の Pod から使うには、ノードが持つデバイスを誰かが Kubernetes に広告しなければならない。その広告の仕組みには、長く使われてきた device plugin と、新しい Dynamic Resource Allocation(以下 DRA)という 2 つの方式がある。この章は、この 2 つを 1 つの基盤の中でどちらも扱えるようにするために、[Issue #19](https://github.com/littlemex/distai-platform/issues/19) で検討した設計を扱う。検討した時点(2026-11-18)の一次情報と、独立したレビューとの比較検討がもとになっている。方式の比較と推奨は、検討の時点では推測であり、8 節で計画した実験(E0 から E7。番号は 8 節の表で定義する。本文に出てくる E4a から E4d と E4+ は E4 の枝番で、第 7 章で定義する)で確かめる前提で書かれた。

本質から言うと、2 つの方式を抽象化する独自の CRD(Custom Resource Definition)はありうるが、作らない。Pod の書き方の違いは、Kubernetes 本体の KEP-5004(DRA の extended resource、v1.37 で stable、Amazon EKS では 1.37 から使える)がすでに吸収している。「device plugin を後から入れると問題になる」の正体は抽象化の不足ではなく、1 つのノードで 1 つのデバイスのクラスを広告できる主体は 1 つだけという排他と、その広告する主体を誰が受け持つかが決まっていないことにある。そこで、方式(device plugin か DRA か)を「ノードプール × デバイスのクラス」が持つ属性として扱ってノードプールのラベルに書き、広告する側はそのラベルで自分が広告するノードを選ぶ形にする。

性能の面でも方式の違いは無視できない。1 台の `p5e.48xlarge`(1 台、各 1 回の実測)で GPU と EFA デバイスを同じ PCIe のルートにそろえられたのは、device plugin では 32 回中 2 回だったのに対し、`matchAttribute: pcieRoot` を使った DRA では 32 回中 32 回だった。NCCL の帯域は、GPU と同じルートにある EFA デバイスを通すと 45.8 GB/s、別の NUMA ノードにある EFA デバイスを通すと 3.6 から 4.7 GB/s だった。この実測は kubelet に手を入れていない条件のもので、本書の実験より前に行われたものである。

## 1. 「後から入れると問題になる」の正体

デバイスを Kubernetes に見せる仕組みには 3 つの層がある。カーネルドライバ、広告する層(device plugin か DRA のドライバ)、そしてそれを入れる側(installer)の層である。広告する層が問題の核心で、入れる側の入り口が 5 つあるのに、どれも同じ広告する層に書き込む。

5 つの入り口とは、Helm の release、GPU Operator、eksctl、ノードイメージへの同梱(Bottlerocket の NVIDIA variant)、EKS Auto Mode の内蔵である。広告する層では、1 ノードにつき 1 つの名前は 1 つの広告主体しか持てない。広告する層はカーネルドライバ(多くは AMI に同梱)と結びつき、GPU Operator はさらに `ClusterPolicy` か `GPUCluster` のどちらか一方しか持てないという設定の層も抱えている。この 5 対 1 の構造が、後から device plugin や DRA のドライバを入れたときに起きる問題の根である。

AWS が公開している reference アーキテクチャ集 [awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai) の CloudFormation テンプレートには、この問題を検出する [`plugin()`](https://github.com/awslabs/awsome-distributed-ai/blob/d29964fcf8273fbc14131f9e252a25b08865ebd3/architectures/amazon-eks/assets/eks-add-gpu-nodegroup.yaml#L867-L880) という関数がある。この関数は、Helm の release 名を 2 つ比較するだけである。バージョンが違う重なりを見つけるとデプロイを失敗させて「先にそろえよ」と案内し、別名の release が残っている重なりについては README が別途「先に他の install を消してから使え」と案内する。これらは実際に起きている重なりのうち一部しか見つけられない対症療法であり、重なりそのものの構造を直すものではない。

実際に起きる重なりは、次の 8 種類に分けられる。このうち本書の実験で実際に再現したのは #5 だけであり(第 6 章の E1、第 7 章の E4+)、#1 の症状は推測のまま残っている。

| # | 重なり | 症状 | 今の対処 | 評価 |
| --- | --- | --- | --- | --- |
| 1 | 同じ名前の device plugin が二重に入る(別名の Helm release、GPU Operator、eksctl、ノードイメージへの同梱) | 数や割り当てが揺れる(推測) | `plugin()` は Helm の release 名を 2 つだけ見る。README は「先に消してから使え」と案内する | 検出が入れる側の一部しか見えていない対症療法 |
| 2 | ノードグループのスタックどうしでバージョンが違う | 後から作るスタックが失敗する | `plugin()` が失敗し、先にそろえるよう案内する | クラスタ全体に 1 つしか持てない対象を、ノードグループ単位のスタックが入れている |
| 3 | スタックを削除した後に Helm release が残る | リリースが残り続ける | README に注意書きがある | 持ち主が決まっていない |
| 4 | ドライバの読み込み元が二重になる | ドライバの読み込みに失敗する | 旧基盤である distributed-ai は `driver.enabled=false` を強制し、plan の時点で組み合わせを拒否していた(この基盤ではまだ実装していない) | 宣言の時点で拒否しており、根本の対処に近い |
| 5 | device plugin と DRA のドライバが同じノードで同じ名前を広告する | KEP-5004 ではそもそも許されていない。ただし規約上の禁止であって、実機ではどこにも止められず、警告なしに同じデバイスが 2 つの Pod に渡った(第 6 章の E1) | 旧基盤である distributed-ai の [ADR D9](https://github.com/littlemex/distributed-ai/blob/e092c3f54e2ebcee665389cbdfc17348f612c2d3/infra/eks/docs/adr-0001-accelerator-pools-mixing.md) は DRA 自体を避けた | 避けただけで、解いてはいない |
| 6 | GPU Operator の設定が排他的 | クラスタに `ClusterPolicy` と `GPUCluster` の片方しか持てず、その場での移行手順も無い | 対処は無い | ノードプール単位の移行の妨げになる |
| 7 | 入っているのに広告されない | EFA の chart が既定で対象とするインスタンスラベルの一覧に `g6e.12xlarge` が無く、EFA が 0 枚のまま | 旧基盤である distributed-ai は values で回避していた(この基盤ではまだ実装していない) | 上流で直すべき不具合 |
| 8 | taint を誰も付けない | 無関係な Pod がアクセラレータのノードに乗る | NodePool が taint を付ける | 妥当 |

根本の原因は 3 つに絞れる。どの広告主体がどの方式で広告するつもりかを宣言する場所がどこにも無いこと、広告する側の DaemonSet が「入れると対象ノードすべてに自動的に広がる」opt-out の形をしていること、そしてクラスタに 1 つしか持てないはずの対象を、寿命の短いノードグループのスタックが所有していることである。

表の #5 で触れた旧基盤の ADR D9 は、この検討で書き直しが必要と判断された。「Karpenter と Auto Mode が DRA に未対応」という前提は古くなっており、「同じノードで device plugin と DRA は共存できない」という書き方も、正確には「同じリソース名を同じノードで両方が広告できない」である。実際、Auto Mode は NVIDIA と EFA の DRA ドライバと Neuron の device plugin を同じノードで併用している。

## 2. 周辺技術の現状(2026-11-18 に確認)

決定を検討する前提として、Kubernetes、Amazon EKS、NVIDIA、Neuron、Karpenter、Kueue の一次情報を確かめた。

| 対象 | 分かったこと | 出典 |
| --- | --- | --- |
| DRA 本体 | `DynamicResourceAllocation` は v1.34 で GA、v1.35 で確定。API は `resource.k8s.io/v1` | [KEP-4381](https://github.com/kubernetes/enhancements/blob/master/keps/sig-node/4381-dra-structured-parameters/kep.yaml) |
| DRA 経由の extended resource | KEP-5004。alpha は v1.34、beta は v1.36、**stable は v1.37**。`DeviceClass.spec.extendedResourceName` を使うと、`nvidia.com/gpu: 1` のように要求する Pod を DRA 側で処理できる。方式はノードごとに違ってよいが、同じノードで同じ名前を device plugin と DRA の両方が広告することはできない | [KEP-5004 の kep.yaml](https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/5004-dra-extended-resource/kep.yaml)、[KEP-5004 の README](https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/5004-dra-extended-resource/README.md) |
| Amazon EKS | v1.37 は標準サポート(2026-10-01 リリース)。AWS は 1.34 以降の新規デプロイに DRA ドライバを推奨している。AL2023 の NVIDIA 向け・Neuron 向けの AMI は device plugin も DRA ドライバも同梱しない。Bottlerocket の NVIDIA variant は device plugin を同梱する。Auto Mode(1.36 以降)は NVIDIA GPU と EFA の DRA ドライバを内蔵し、Neuron は device plugin を使う | [EKS device management](https://docs.aws.amazon.com/eks/latest/userguide/device-management.html)、[Neuron on EKS](https://docs.aws.amazon.com/eks/latest/userguide/device-management-neuron.html) |
| NVIDIA | `dra-driver-nvidia-gpu` は kubernetes-sigs 配下に移った(v0.5.1)。GPU 1 枚単位の割り当てはまだ公式サポートではなく、既定で off。ComputeDomain(IMEX)はサポートされている。GPU Operator は `ClusterPolicy` と `GPUCluster` を同時に持てない | [GPU Operator DRA](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-intro-install.html) |
| EFA | DRANET(`aws-dranet`)経由の DRA がある。DeviceClass は `efa.networking.k8s.aws`、ドライバ名は `dra.net`。1 Pod につき 1 デバイスまで | [EKS EFA](https://docs.aws.amazon.com/eks/latest/userguide/device-management-efa.html) |
| Neuron | Neuron SDK 2.28.0 から DRA ドライバがある。`matchAttribute: resource.aws.com/devicegroup4_id` のような CEL の制約で、連続したデバイスを Neuron のスケジューラ拡張なしに割り当てられる | [Neuron DRA](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/deploy/eks/dra.html) |
| Karpenter v1.15 | 動的な DRA のプロビジョニングは alpha で、NVIDIA GPU と EFA のドライバだけが対象(`settings.enableDRA` が `DRANVIDIAGPU` と `DRAEFA` を有効にする)。**Neuron 用のゲートは無い**。static な NodePool ならどのドライバでも動く | [Karpenter DRA](https://karpenter.sh/docs/tasks/dra/) |
| Kueue | DRA との統合は v0.18 から。extended resource と partitionable device は v0.19 で既定で有効。`KueueDRADeviceFeasibility` は v0.20 で alpha | [Kueue DRA](https://kueue.sigs.k8s.io/docs/concepts/dynamic_resource_allocation/) |
| その他の DRA 機能 | prioritized list と admin access は v1.36 で stable、device taints は v1.37 で stable、partitionable devices、consumable capacity、binding conditions は v1.36 で beta | Kubernetes API reference v1.37 |
| 抽象化レイヤー | KEP-5004 自体を除けば、device plugin と DRA を一般化する一次情報は見つからなかった | — |

ノードを起こす側の他の選択肢として cluster-autoscaler もある(1.35 で DRA が production-ready とされる)。この基盤は容量の起こし方を Karpenter に寄せている(第 2 章)ため、比較の対象としては挙げるにとどめた。

## 3. 決定 1: 独自の CRD は作らない

2 つの方式を抽象化する独自の CRD は、作ろうとすれば作れる。しかし、1 節で見た「後から入れると問題になる」の正体は、抽象化が足りないことではなく、誰が広告するかという所有権が決まっていないことだった。KEP-5004 がすでに Pod の書き方を吸収している以上、この基盤の上にもう 1 段の抽象化を重ねる理由は乏しい。

| 案 | 内容 | 設計の原則との整合性 | 弱点 | 判定 |
| --- | --- | --- | --- | --- |
| (a) 作らない | KEP-5004 と、ノードプールごとの方式のラベルだけで済ませる | この基盤だけの抽象化を増やさない。GPU と Trainium が同じ形を保てる | 排他は規約(ラベルを守ること)に頼る | **採用** |
| (b) ベンダーを横断する方式宣言用の CRD とコントローラ | ベンダーを横断する GPU Operator のようなもので、導入・衝突の検出・持ち主の引き継ぎを受け持つ | 独立した OSS として作るなら筋が通る | 下の 4 つの理由 | 今は作らない |
| (c) DeviceClass と ResourceClaimTemplate のカタログ | 上流の API オブジェクトの実体を chart で配るだけ | 抽象化ではなく、単体でも使える部品である | ベンダーの chart が作る DeviceClass と重複しうる(第 6 章の E0 で NVIDIA の chart が実際に作ることを確認) | **採用** (小さく) |
| (d) 読むだけの検査ツール | ノードごとに「デバイスのクラス × 広告する側」を突き合わせ、二重・欠け・ラベルとの食い違いを報告する。CRD は持たない | 独立した OSS として公開しやすく、`plugin()` の置き換えになりうる | 報告するだけで直さない | 実験 E1 の後に**採用** |

(b)を今は作らない理由は 4 つある。各ベンダーの chart の値を追い続けるメタオペレータになってしまうこと、GPU Operator 自体が 2 つの設定を排他にしている以上その上に重ねてもノード単位の混在を解決できないこと、EKS Auto Mode では広告する側が内蔵されているため意味を持たないこと、そして上流の KEP-5004 自体が「ノードごとに方式を移れる」ことを目的にしており、独自コントローラを求める一次情報が見当たらないことである。作る条件は、同じ排他の仕組みを 3 つのベンダーの分だけ手で書くことになり、かつ他の利用者からの需要が確かめられたときであり、そのときは独立した OSS として作る。

CRD 以外にも、Issue #19 は次の 4 つの代替案を退けている。容量の混ぜ方の全体設計(Issue #17)を先に決めて DRA を後から足す案は、DRA が Pod の要求の書き方、Kueue の数え方、Karpenter が起こすノードをすべて変えるので退けた。旧基盤の ADR D9 のように device plugin に留まる案は、その前提(Karpenter が DRA に未対応)が古くなり、AWS が新しいデプロイに DRA を推奨しているので退けた。すべてのクラスを今すぐ DRA に移す案は、NVIDIA の GPU 1 枚単位の DRA が公式サポートでなく、Karpenter の動的な DRA が alpha で、Neuron には Karpenter のゲートが無いので退けた。ベンダー横断のオペレータは上の (b) である。

## 4. 決定 2: 方式はノードプールとデバイスクラスの属性にする

この決定は、方式(device plugin か DRA か)をノードプールとデバイスのクラスの組が持つ属性として宣言させ、同じ名前を 2 つの広告主体が同じノードで広告する重なりを構造的に防ぐものである。そのために、各 NodePool には、デバイスのクラスごとに 1 つのラベルキー(たとえば `<domain>/nvidia-gpu-method: device-plugin | dra`)を持たせる。広告する側はこのラベルで、自分が広告するノードを選ぶ(opt-in)。1 つのラベルキーは 1 つの値しか持てないので、すべての広告主体がこのセレクタを守る限り、同じ名前を 2 つの広告主体が同時に広告することは構造的に起きない。守らない主体が 1 つでもあると黙って二重に割り当てられることは第 6 章の E1 と第 7 章の E4+ で実際に見ており、セレクタを持つことを強制する手段として、admission policy か検査ツール (d) を使う。

<!-- FIG name="05-opt-in-labels" kind="relation" shows="as-is: device plugin DaemonSet with no selector spreads to every node pool (opt-out); to-be: advertisers select node pools by a method label (opt-in), one label key per device class" labels="node pool 1, node pool 2, node pool 3, method=device-plugin, method=dra, DaemonSet, opt-out, opt-in" -->
![device plugin が全ノードプールに広がる今の形と、ラベルで選ぶ提案の形を並べた図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/05-opt-in-labels.png)

Argo CD がすべての広告する側の唯一の持ち主になり、Terraform もノードグループもこれらを一切インストールしない。重なりの検出は、Helm の release 名ではなく、ノードの allocatable、ResourceSlice、ノード上の Pod という実際の結果から行う。

この決定には、Neuron 固有の制約が重なっている。Neuron の chart は device plugin と DRA のドライバを 1 つの chart に持つが、`draDriver.enabled=true` にすると device plugin が消える(テンプレートの条件分岐による。chart 1.11.2 で確認)。そのため、Neuron では同じ chart を、方式のラベルで対象を絞った 2 回の release として install する必要がある。この点は第 7 章の実験(E4a)で確かめている。

## 5. 決定 3: Pod の要求を 2 つの層にする

Pod がデバイスを要求する書き方は、既定の層と、必要なときだけ書く層の 2 つに分ける。

<!-- FIG name="05-pod-request-layers" kind="flow" shows="Layer 1 (resources.limits) reaches both a device-plugin node and a DRA node with extendedResourceName via KEP-5004; Layer 2 (ResourceClaimTemplate) reaches only a DRA node" labels="Layer 1, Layer 2, resources.limits, ResourceClaimTemplate, device plugin node, DRA node, extendedResourceName, KEP-5004" -->
![Layer 1 と Layer 2 がそれぞれどのノードに届くかを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/05-pod-request-layers.png)

Layer 1(既定)は今までどおり `resources.limits` に `nvidia.com/gpu: 1` や `aws.amazon.com/neuron: 1` のように書く。これは device plugin のノードでも、`extendedResourceName` を持つ DeviceClass がある DRA のノードでも、KEP-5004 のおかげでそのまま届く。この層が、device plugin から DRA へ方式を移すときの継ぎ目になる。Layer 2(opt-in)は `ResourceClaimTemplate` を使い、DRA でしか表せない性質(GPU と EFA を同じ PCIe のルートにそろえる、Neuron のデバイスを連続して取る)が必要なときだけ書く。

Layer 1 が DRA のノードに届くには、`extendedResourceName` を持つ DeviceClass が存在することが前提になる。NVIDIA の DRA chart はこの DeviceClass を自分で作って配るが、Neuron の chart は作らない。そのため、この基盤の DeviceClass のカタログ(決定 1(c))には、`extendedResourceName: aws.amazon.com/neuron` を持ち `device.driver == "neuron.aws.com"` を選ぶ DeviceClass を 1 つ追加する必要がある。この基盤自体にはまだ実装していないが、追加すれば実際に効くことは、第 7 章の実験クラスタ上(E4b)で確かめている。

Kueue の枠の名前は、方式によらず 1 つにする。Layer 1 はそのまま数えられるが(Kueue v0.19 で既定で有効)、Layer 2 の数え方は第 6 章の実験(E3)で確かめている。ResourceFlavor には方式のラベルを入れない。入れてしまうと、移行の途中で新旧どちらのノードプールにも乗れる Pod が作れなくなるからである。flavor は方式ではなくノードプール(`karpenter.sh/nodepool`)をキーにし、Kueue には `deviceClassMappings` で DeviceClass を枠の名前に対応づける。E3 の結果を受けて、Layer 2 のワークロードは DRA の flavor だけを持つ ClusterQueue に紐づく LocalQueue に流すか、`karpenter.sh/nodepool` を固定する、という規則を足した。そうしないと Kueue が Layer 2 のジョブを device plugin の flavor に入れ、claim を割り当てられずに止まるからである。

## 6. 決定 4: アクセラレータのクラスごとの方式

この決定は、アクセラレータのクラスごとに、既定の方式(device plugin か DRA か)、ノードの起こし方、Pod の要求の層を定めるものである。次の表のうち、実験で確定したのは小型 NVIDIA の行(第 6 章の E2)、単体 Neuron の行(第 7 章の E4)、多デバイス Neuron の行のうち「Karpenter が動的には起こせない」という部分(第 7 章の E4c)である。確定といっても、起動したのは `g7.2xlarge` と `trn2.3xlarge` の spot だけであり、`g6e` と Inferentia は 1 台も起動していない。残りの行(大型 NVIDIA、Multi-Node NVLink、多デバイス Neuron を DRA にすること、EFA)は実験前の推奨のままで、大型 NVIDIA は E7、多デバイス Neuron は E6 で確かめる計画である。Multi-Node NVLink の行に対応する実験はまだ計画していない。

| クラス | 方式 | ノードの起こし方 | Pod の要求 | 理由 |
| --- | --- | --- | --- | --- |
| 小型の単体 NVIDIA(`g6e`、`g7`) | **device plugin** (第 6 章の E2 で確定) | Karpenter の動的プロビジョニング | Layer 1 | Layer 1 の Pod を動的な DRA プールに乗せると、ノードが初期化されないまま 5 分以上残った(第 6 章の E2)。横に置く DRA プールは Layer 2 の Pod か、台数を固定したプールで使う |
| EFA を持つ大型 NVIDIA(`p5` 系) | GPU と EFA の両方を **DRA** | static な NodePool か予約 | Layer 2(`pcieRoot`) | 揃う回数が device plugin の 32 回中 2 回に対し DRA では 32 回中 32 回で、NCCL の帯域は GPU と同じルートの EFA を通すと 45.8 GB/s、別の NUMA ノードの EFA では 3.6 から 4.7 GB/s だった(1 台、各 1 回の実測)。static なら Karpenter 側の DRA 対応が要らないが、static な NodePool には Karpenter の alpha ゲート `StaticCapacity` が要る |
| Multi-Node NVLink の世代 | DRA(ComputeDomain) | static | ResourceClaim | ComputeDomain は公式にサポートされている |
| 多デバイスの Neuron(`trn2.48xlarge` など) | **DRA を第一候補** | static な NodePool(`replicas` 指定、Karpenter の alpha ゲート `StaticCapacity` が必要) | Layer 2(device group) | スケジューラ拡張が要らなくなる。その根拠の `devicegroup4_id` の制約は文書の記述で、`trn2.3xlarge` の ResourceSlice にはこの属性が出ていない(第 7 章の E4a)。E6 で確かめる。Karpenter は Neuron の DRA ノードを動的には起こせない(第 7 章の E4c) |
| 単体の Neuron(`trn2.3xlarge`、Inferentia) | **device plugin** (第 7 章の E4 で確定) | Karpenter の動的プロビジョニング | Layer 1 | 小型 NVIDIA と同じ理由。既定のスケジューラで足り、拡張は不要(第 7 章の E4d) |
| EFA | 同じノードの GPU と方式を合わせる | GPU に従う | GPU に従う | そろえる効果が出るのは両方が DRA のときだけ(推測)。Neuron の device plugin との同居は可能 |

GPU と Trainium が等価であることは、「同じ方式を使う」ことではなく「同じ形を持つ」ことで保つ。どのクラスも、ノードプール(ラベルと taint)、広告する側の chart、DeviceClass、ResourceFlavor という 4 つの部品を持つ。この観点では GPU Operator を使わない方が筋が通る。GPU Operator を使うと NVIDIA だけが別の形になり、1 節の重なり #6 もそのまま持ち込んでしまうからである。個別の chart だけで足りることは、第 6 章の実験(E1)で `g7.2xlarge` について確かめている。検討の時点では、残る非対称は Karpenter の動的な DRA 機能に Neuron 用のゲートが無いことの 1 つだけだと考えていた。実験の後は、それに加えて、Neuron の chart が `extendedResourceName` を持つ DeviceClass を配らないこと、1 回の release で 1 方式しか動かないこと、Karpenter の `neuroncore` の見積もりが実測の 2 倍であること、EFA と共有できるトポロジ属性が ResourceSlice に無いことが分かった(第 7 章の 9 節)。どれも部品の数(4 つ)は変えないので「同じ形」は保てるが、Neuron 側で手当てする部品が多い。

小型 NVIDIA を device plugin のままにする理由には、実験の結果のほかに、実験の前からの理由もある。AWS は 1.34 以降の新規デプロイに DRA を推奨しているが、上流の NVIDIA は GPU 1 枚単位の DRA をまだ公式サポートしておらず、ここに緊張がある。形が同じであれば、既定を反転させるときの変更は NodePool 1 つで済む。また、DRA のプールを Layer 2 で動的に使う道は、alpha の `settings.enableDRA` と、公式サポート外の GPU 1 枚単位の DRA の両方に依存している。

## 7. 決定 5: 移行はノードプール単位で、途中の置き換えはしない

稼働中のクラスタで device plugin から DRA へ移るときは、同じノードの中で方式を切り替えるのではなく、新しいノードプールを横に作って入れ替える、いわゆる blue/green の形を取る。

<!-- FIG name="05-migration-steps" kind="flow" shows="six-step blue/green migration from a device-plugin node pool to a DRA node pool, with rollback restoring weights and limits only" labels="1 narrow, 2 install DRA driver, 3 new node pool, 4 canary, 5 swap weights, 6 clean up, rollback" -->
![device plugin から DRA へノードプール単位で移る 6 段階の手順を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/05-migration-steps.png)

手順は、既存の device plugin の対象を `method != dra` に絞ることから始め、DRA のドライバを 0 台の状態で install し、低い weight を持つ新しい NodePool を `method=dra` で作り、Layer 2 の Pod 1 つをカナリアとして流し、問題が無ければ weight を入れ替えて旧プールの上限を 0 にし、旧プールが空になってから片付ける、という 6 段階になる。カナリアに Layer 2 の Pod を使うのは、決定 4 の根拠にもなっている第 6 章の E2 の結果どおり、Layer 1 の Pod では Karpenter が動的な DRA ノードを初期化しないためである。E2 より前の、検討の時点の手順では、カナリアは Layer 1 の Pod を DRA プールに固定して流し、デバイスが見えることと Kueue の使用量が同じ名前で増えることを確かめる、としていた。

この手順には前提がある。Kubernetes 1.37、Kueue v0.19 以降で、Layer 2 の Pod で DRA のノードを動的に起こすなら Karpenter の `settings.enableDRA` も要る。DRA のドライバを入れる段階で、`extendedResourceName` を持つ DeviceClass も用意する(NVIDIA は chart が作り、Neuron はこの基盤のカタログが足す)。weight を入れ替えた後も既存の Pod は自然に終わるのを待ち、そのあいだ `karpenter.sh/do-not-disrupt` で止められないようにする。旧プールが空になったら device plugin を消し、Neuron ではスケジューラ拡張も消す。

この手順には、まだ解けていない穴が 1 つある。手順 5 で weight を入れ替えると、既定である Layer 1 のワークロードも動的な DRA プールに流れ、E2 で見た「初期化されず片付かないノード」を作ることになると考えられる(推測)。そのため今の時点でこの手順をそのまま使えるのは、移す先が台数を固定した DRA プールの場合か、移すワークロードが Layer 2 の場合に限られる。小型 NVIDIA は device plugin のまま、大型 NVIDIA と多デバイス Neuron は最初から static という決定 4 の範囲では、この穴にはまだ当たらない。移行の手順そのものは E5 で確かめる。

ロールバックは weight と limits を戻すだけで、ノード自体には触れない。予備の容量が無い場合(予約が 1 台しか無いなど)は、横に新しいノードプールを作る余裕が無いので、ジョブの切れ目で入れ替える。DRA のノードプールは AL2023 を使う。Bottlerocket の NVIDIA variant は device plugin を同梱しているため、DRA 専用のノードには向かない。Neuron のスケジューラ拡張のために `schedulerName` を指定していた Pod は、その指定を取り除く必要があると考えられる(推測)。time-slicing の設定や、監視の Pod とデバイスの対応づけも、DRA では表し方が変わるので移し替えが要ると考えられる(推測)。

## 8. 決定する前に行う実験

Issue #19 はデバイスの公開方式を決める Issue であり、容量の混ぜ方の全体設計([2 章](./02-four-decisions)、[3 章](./03-capacity-pools)、Issue #17)をブロックしている。デバイスの公開方式の選び方によって、プールの規約そのものが変わりうる。そのため、#17 の骨格を決める前に、この設計のうち方式の骨格(ラベルによる opt-in、Layer 1 の既定化、広告主体の持ち主)を決めるための実験を、安いものから順に次の 8 つ計画した。

| # | ハードウェアと容量 | 確かめること | 決まること |
| --- | --- | --- | --- |
| E0 | 机上 | 各 chart の既定のノードセレクタ、Karpenter が知っている Neuron のリソース名 | opt-in に必要な値 |
| E1 | `g7.2xlarge` の spot ×2 | 同じ Layer 1 の Pod が両方のノードプールで動くか、AL2023 で NVIDIA の DRA ドライバが動くか、わざと二重に入れたときの壊れ方 | Layer 1 を既定にするか、GPU Operator を外せるか、検査ツール(d)の仕様 |
| E2 | 同上、Karpenter v1.15 | DRA のゲート無しで、Layer 1 の Pod がノードを起こして使えるようになるか | DRA のノードプールを動的にするか static にするか |
| E3 | 同上、Kueue | device plugin、Layer 1、Layer 2 が同じ名前で数えられるか | 枠の名前を方式によらず 1 つにできるか |
| E4 | `trn2.3xlarge` の spot(ap-southeast-4) | Neuron の DRA と Layer 1 で、ゲートの無い Karpenter が動的に起こせるか | Trainium の動的プロビジョニングの可否 |
| E5 | `g7` を数台 | 7 節の移行手順を、Job を流しながら通す | 移行手順の確定 |
| E6 | `trn2.48xlarge` の spot(東京)×1 | 連続したデバイスの割り当て、EFA の DRA との同居 | Neuron のスケジューラ拡張を捨てられるか |
| E7 | `p5` / `p5en` の spot(us-east-2、us-west-2)×1〜2 | 以前の 1 台・各 1 回の実測を、v1.37 と現行のドライバで何度か確かめる。2 台での NCCL | 大型 GPU で DRA と Layer 2 を既定にするか |

E1 から E3 は同じクラスタで続けて流せる。E1 から E4 の結果は第 6 章と第 7 章で詳しく扱っており、方式の骨格のうち、ラベルによる opt-in と Layer 1 の既定化はこの 4 つの実験で決まった。広告主体の持ち主を Argo CD 1 つにすることは、実験で確かめる性質のものではなく、設計として決めている。E5、E6、E7 は、クラスごとの既定値を仕上げるための実験として、まだ実行していない。

## 9. awsome へ提案できること

実験で確かめた内容のうち、GPU 一般に関わる部分は、1 節で触れた AWS の reference リポジトリ `awsome-distributed-ai` に次の 4 件を提案できると考えている。

| # | 提案 | 上流の今 | 出し方 |
| --- | --- | --- | --- |
| 1 | device plugin の検出を、release 名ではなく結果(ノードの allocatable、ResourceSlice、ノード上の Pod)で行う | `plugin()` は Helm の release 名を 2 つだけ見ている | 決定 1(d) の検査ツールを単体の OSS として先に作り、その後にテンプレートから呼び出す |
| 2 | 広告する側を opt-in にし、クラスタに 1 つしか持てない対象をノードグループのスタックの外に出す | ノードグループのスタックが device plugin をインストールし、削除後も残る | テンプレートの分け方を変える RFC として提案する |
| 3 | DRA(GPU と EFA の `pcieRoot` によるそろえ)の例と測定を加える | DRA の扱いがまだ無い | E7 の実験の後に提案する |
| 4 | 広告する層と入れる側の対応表を README に加える | 注意が散文で 1 段落にまとまっているだけ | ドキュメントの PR として提案する |

ほかにも、EFA の device plugin が対象とするインスタンスラベルの一覧に `g6e.12xlarge` を加える提案、Karpenter への Neuron 用 DRA ゲートの提案と KEP-5004 の Pod の挙動の報告、Kueue への「方式をまたいで同じ名前で数える」設定例の提案、GPU Operator への「2 つの設定をノード単位で共存させる」issue が出せると考えている。Neuron 固有の知見(第 7 章で扱う `neuron-helm-chart` の構造や Karpenter の見積もりのずれなど)は、awsome-distributed-ai が Neuron を中心には扱わない方針に近いため、awsome には出さず、この基盤の中で育てる対象のままにしている。awsome 以外の宛先としては、Neuron の chart と DRA ドライバ(`extendedResourceName` を持つ DeviceClass の例、device plugin と並べて置くための values、EFA と共通のトポロジ属性)、`dra-driver-nvidia-gpu` と `aws-dranet`(AL2023 での検証の報告と、`pcieRoot` による整列の実測)、Karpenter(ドライバが無いノードを空振りで起こし続けないためのガード)も検討の時点で挙げている。

## 10. まだ分かっていないこと

Layer 1 の Pod で、Karpenter が DRA のノードを動的に起こせるかどうかは、E2(NVIDIA)と E4(Neuron)、つまり第 6 章と第 7 章で確かめた範囲にとどまる。大型 NVIDIA と多デバイス Neuron は、この問いの対象ではなく、最初から static なノードプールを前提に設計している。E5〜E7 は、この前提のもとで既定値を詰める実験(移行手順、連続した割り当てと EFA の同居、p5 系での複数回・2 台構成の再測定)であり、動的起動の可否そのものを確かめる実験ではない。Kueue で Layer 2 を Layer 1 と同じ名前で数える設定の詳細は第 6 章の E3 で確かめた範囲にとどまる。DeviceClass を追加したときに device plugin のノードのスケジューリングが変わらないかは、第 6 章の E1 で、chart の DeviceClass がある状態で device plugin のノードの Layer 1 の Pod が自動の claim を作らずに動いたことを 1 回見た範囲にとどまる。以前の `p5e` の実測は 1 台・各 1 回で、kubelet には手を入れていない条件のものであり、複数回の実測と 2 台構成での実測は E7 で行う計画である。

実験によって新しく生じた未知もある。Layer 1 の Pod で起こした未初期化のノードが最終的に片付くのかどうかは、NVIDIA で 5 分以上、Neuron で 8 分 50 秒まで見ただけで分かっていない。NVIDIA の台数を固定した DRA プールは試していない。AMI の自動ラベル `nvidia.com/gpu.present=true` は 1 つの AMI バージョンで見ただけである。横に置いた DRA プールに Layer 1 の Pod が乗らないようにする仕組み(taint や weight)はまだ決めていない。EFA の DRA は 1 Pod につき 1 デバイスまでとされており、複数の GPU を持つ Pod で GPU ごとに EFA をそろえる形をどう書くかも、E7 で確かめる必要がある(推測)。

## まとめ

device plugin と DRA を両方扱うために、独自の抽象化 CRD は作らず、KEP-5004 で Pod の書き方を固定し、方式はノードプールごとのラベルで分ける設計にした。大型の GPU と多デバイスの Trainium は DRA を第一候補にし、小型のアクセラレータは device plugin のままにすることを実験で確定させた。この設計の骨格は、推定で 2.5 から 3 ドル(E0 から E3)と 4 から 5 ドル(E4)で済んだ実験によって決められており、その結果は次の 2 つの章で詳しく扱う。
