---
title: "Advanced01 - イメージキャッシュ戦略を恒久基盤に組み込む"
free: true
---

本章では、変化し続けるコンテナイメージをノードを跨いで賢くキャッシュし、かつ破綻しないためのイメージキャッシュ層を、この分散 AI 基盤に恒久的に組み込む考え方を扱います。特定の 1 イメージを速くする小手先の話ではなく、「キャッシュが無くて毎回コールド pull で待たされるのも、キャッシュが詰まってノードが起動不能になるのも、どちらも困る」という要求に対して、退屈だが壊れない設計を選ぶ判断を示します。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のノード層とレジストリ層のあいだ、つまり「イメージがどこから来て、どこに残り、次にどう再利用されるか」という部分です。Karpenter が起動・回収するノードの上で、コンテナイメージの取得と保持をどう設計すれば実験の反復が速くなるか、そしてその高速化が障害の種にならないかを見ていきます。

## これは何をするものか

この基盤は今後あらゆるワークロード（大型 GPU/Neuron 学習イメージ、vLLM、多様なフレームワーク）で使う恒久基盤です。そこで問題になるのが、コンテナイメージの取得コストです。数 GB のイメージを毎回レジストリからコールド pull すると、実験の反復ごとに数十秒から数分の待ち時間が積み重なります。

素朴な発想では「キャッシュを足せば速くなる」と考えますが、この基盤には無視できない支配的な事実が 2 つあります。

- **キャッシュの寿命はノードの寿命に等しくなります**。Karpenter のノード回収の挙動はプールごとに異なり（Basic03 で導入した Karpenter の disruption 設定です）、EFA や予約系のアクセラレータプール（Basic06 で作る `gpu-p4d` や Basic09 の `trn2` など）は `consolidateAfter: Never` でノードを維持しますが、非 EFA のアクセラレータプール（Basic04 の `gpu-ddp` など）と cpu プールは短い `consolidateAfter`（5m / 30s）でアイドルノードを回収します。ノード内のキャッシュをいくら磨いても、対象ノードが消えればキャッシュも消えます。検証反復のコールド pull の主因はキャッシュ層の不在ではなく、回収対象プールでのノードの入れ替わり（churn）です
- **digest pin 運用が最強のキャッシュの味方になります**。イメージをタグではなく digest で参照すると、参照はイミュータブルになり「stale キャッシュ」という故障クラスがほぼ消滅します。`imagePullPolicy: IfNotPresent` を安全に使えるようになります

この 2 点を踏まえると、キャッシュ設計の目的は「速くすること」だけでは足りません。**速くする仕組みが失敗したときに、素のコールド pull に静かに戻るだけで済むこと**、つまり良性の故障モードに閉じることが同じくらい重要です。ノードが起動できなくなったり、実行中の学習ジョブが死んだりする故障を持ち込む高速化は、この基盤には入れません。

## キャッシュは物理的に 4 層しかない

![イメージキャッシュの層と良性故障の原則](/images/books/eks-distributed-ai/image-cache-layers.png)

キャッシュと呼べる実体は、この基盤では物理的に次の層にしか存在しません。層ごとに、どこまで整備すべきかを判定します。

### 層 0: ネットワーク基礎

ECR のレイヤ実体は S3 の presigned URL 経由で配られます。そのため VPC に **S3 gateway endpoint** が無いと、イメージの大部分を占めるレイヤが全て NAT を通り、帯域と課金の両面で律速します。ECR interface endpoint（`ecr.api` / `ecr.dkr`）も併せて用意しますが、こちらを通るのは認証トークンや manifest といった KB 単位のメタデータ往復であり、pull 速度そのものへの寄与は小さく、位置づけは「NAT 障害時に pull が死なない」という衛生面です。この基盤ではどちらも `vpc-endpoints.tf` で IaC 固定しています。

### 層 A: pull 経路

恒久ルールは **「ランタイムで参照するイメージは全て自アカウントの Amazon ECR 発とする」** です。BuildKit で焼く自前イメージは既にそうなっており、直接使う外部イメージ（vLLM 公式、NGC ベースなど）は CI で `crane copy` して自 ECR にミラーします。

補助的に ECR pull-through cache（PTC）を Docker Hub や `ghcr` などに設定できますが、位置づけは開発時の利便性とミラー漏れの保険にとどめます。GPU 基盤で最も引きたい上流である nvcr.io（NVIDIA NGC）が PTC 非対応であること、そして「未キャッシュの新規 digest かつ上流障害」では PTC でも pull 不能になることから、ランタイム経路を PTC に依存させるのは避けます。

### 層 B: ノード内保持

accelerator プール（`terraform.tfvars` の `accelerator_pools` にコメントで例示されている `gpu-p5en` / `trn2` のような構成、この変数の既定値は空マップです）は概ね完成しています。nodeadm の `localStorage.strategy: Raid0` により containerd の data-root が NVMe instance store に載り、instance store は数 TB 級なので imageGC の既定閾値（85/80）に実質到達しません。ここは本実装ではすでに IaC 固定済みで、kubelet の `imageMaximumGCAge` を `168h` に明示設定し、多世代 digest の無限堆積を防いでいます（`karpenter-resources.tf` の `local.image_maximum_gc_age` を `accelerator_user_data` に注入）。この設定は**削除を追加する側**であることに注意してください。「未使用のまま 168 時間を超えたイメージを、ディスク閾値に達していなくても消す」という設定であり、閾値 GC（`imageGCHighThresholdPercent` の既定 85%）を置き換えたり抑止したりはしません。イメージを保護する設定ではないので、キャッシュを残したいイメージをこれで守ることはできません。

一方 cpu プールは NVMe を持たず、imagefs と nodefs が単一の gp3 に同居します。ここで見落とされがちな支配的ボトルネックは **gp3 のベースライン throughput 125MiB/s** です。イメージのダウンロードと展開で書き込みが二重に走り、ディスクだけで数分溶けます。ここもすでに IaC 固定済みで、CPU 用 EC2NodeClass の `blockDeviceMappings` に `throughput = 500` / `iops = 6000`（`variables.tf` の `cpu_node_volume_throughput` / `cpu_node_volume_iops` の既定値）を設定し、gp3 のベースラインより高いスループットを確保しています。

全プール共通で、kubelet の `serializeImagePulls: false` と `maxParallelImagePulls: 8`、containerd の `max_concurrent_downloads: 8` の引き上げも、`karpenter-resources.tf` の `accelerator_user_data` / `cpu_user_data` で nodeadm の NodeConfig にすでに注入済みです。宣言的でステートレスなので、失敗しても挙動が元に戻るだけです。

### 層 C: ノード跨ぎ再利用

ここがキャッシュ設計の主戦場です。ノードが 0 台から立ち上がる瞬間のコールド pull を潰すには、ノードを跨いだ再利用が要ります。

この基盤の恒久コアは、次の 2 つだけで構成します。

- **headroom floor**: 低優先度の pause Deployment で、cpu プールにノードを常時 1 台維持します。ノードが生きていればノード provisioning の待ち時間がゼロになるので、prewarm と組み合わせたときに効きます。ただし低優先度である以上、混雑時にはスケジューラに押し出されてノードごと入れ替わり得るため、これ単体ではキャッシュの永続性を保証しません（手順 2 で実測を示します）
- **prewarm DaemonSet（素朴実装）**: 温めたいイメージを何もしない initContainer として並べ、kubelet に pull させるだけの DaemonSet です。ノードが新規参加すると自動で温まります。コントローラも CRD も不要で、認証も通常のワークロードと同じ経路なので追加の前提を 1 つも持ち込みません。pull が失敗してもワークロードは通常のコールド pull に落ちるだけという自明な故障挙動を持ちます

この 2 つに共通する状態管理の原則は、**キャッシュの状態はノードローカルの containerd にしか持たせない**ことです。共有キャッシュサービスを置かないので、「詰まったらノードを入れ替えれば直る」という一点に復旧手順を固定できます。

:::message
P2P registry mirror の Spegel は魅力的に見えますが、恒久コアからは外しています。Spegel は「既に温かいノードが複数居る」ことを前提とする技術で、この基盤の痛みである「ノードが 0 台から立ち上がる瞬間」とは前提が噛み合いません。しかも新規ノード起動直後に mirror 解決の遅延がレイヤごとに積み上がり、コールド pull を高速化するはずの部品がコールド pull を遅くする有害ケースがあります。大規模同時 scale-out で ECR 転送重複が支配的だと計測で示された場合に限り、再評価します。
:::

### 層 D: ガバナンス

キャッシュ戦略の最終防衛線は **「そもそも巨大イメージを作らせない」** ことです。モデル重みやデータセットをイメージレイヤに入れると、どの層のキャッシュ設計もいずれ破綻します。恒久ルールとして、イメージはコードと依存のみ（目安 15GB 上限）とし、重みは Amazon S3 に置いて Mountpoint for Amazon S3 CSI や推論フレームワークの S3 直接ロードで取得します。この使い分けは Basic10 の共有ストレージと地続きの判断です。ただし cpu プールで RL 学習のような ~18GB 級イメージを扱う既存の運用例もあり（`variables.tf` の `cpu_node_volume_size` のコメント）、15GB はあくまで新規イメージ設計時の目安であって、超える既存イメージを許さない絶対値ではありません。

## なぜ SOCI や Spegel を恒久コアに入れないか

lazy pull の SOCI や P2P の Spegel は、技術記事では華やかに紹介されがちです。しかしこの基盤の要求は「速いこと」と同時に「詰まって壊れないこと」であり、両者はこの後半の要求と衝突します。

SOCI の lazy pull は、イメージの一部しか触らないワークロードで利得が最大化します。ところが学習イメージは起動直後に CUDA/Neuron ランタイムと Python パッケージ群の大半を実際に読むため、lazy はコストを「起動時」から「実行初期」へ移すだけで総転送量は減りません。さらに深刻なのは、**数日走る学習ジョブの途中にレイヤ span の Range GET 失敗が I/O エラーとしてコンテナ内へ噴出する**ことです。ネットワークの瞬断が「pod 起動リトライ」で済む世界から「学習ジョブ死亡」の世界に変わります。加えて soci-snapshotter は AMI に同梱されず、全ノードに自己管理の常駐デーモンを 1 個増やす決断になります。これが落ちれば pull も起動も不能になり、prewarm DaemonSet とは正反対の致命的な故障特性を持ちます。

この基盤が高速化に採る各層は、いずれも「失敗しても素のコールド pull に退化するだけ」の良性故障モードに閉じています。ノード起動不能や学習中の死亡につながる常駐データパス（SOCI lazy、ステートフルミラー、snapshot 焼き込み）を恒久基盤から排除したことこそが、この設計の判断そのものです。

## AL2023 維持 vs Bottlerocket 移行

Bottlerocket の実利はスナップショット事前ロード（`aws-samples/bottlerocket-images-cache`）ですが、これはスナップショット焼き込みパイプラインの運用そのものであり、イメージが変わるたびの再生成・陳腐化・鮮度管理という負債を持ちます。本基盤は Neuron DKMS、EFA、GPU 専用 AL2023 AMI、nodeadm RAID0、rootless BuildKit といったホスト層の前提が厚く、Bottlerocket の不変・最小ホストモデルはこれらの自由度を大きく削ります。イメージキャッシュのための移行としては割に合わないため、**AL2023 維持で断定**します。

## 全体の中での位置付け

本章は Basic03 で導入した Karpenter のノード churn と、Basic10 の共有ストレージの判断の上に成り立っています。Karpenter が非 EFA/非予約プールのアイドルノードを回収するからこそキャッシュの寿命がノードの寿命に縛られ、だからこそ重みをイメージに入れず S3 に外出しするガバナンスが効いてきます。イメージキャッシュは単独の機能ではなく、ノードのライフサイクルとストレージ設計の交点にある運用最適化の層です。

## 注意

**1. S3 gateway endpoint の欠落は他のどの施策より優先して潰します**

ECR のレイヤは S3 presigned URL で配られるため、S3 gateway endpoint が無いと全レイヤが NAT を通ります。イメージが大きい基盤ほど、ここが帯域と課金の律速点になります。

**2. imageGC が次に使うイメージを消します**

prewarm 済みでまだ使っていないイメージは、kubelet から見ると未使用であり imageGC の削除候補です。ここで `imageMaximumGCAge` を長くすれば守れる、という誤解をしないでください。この設定は「未使用のまま指定期間を超えたら消す」という削除機構であって、ディスク閾値 GC を止めるものではありません。未使用イメージはディスクが閾値を超えれば期間に関係なく回収されます。**未使用のまま残そうとするのではなく、走っているコンテナに持たせて「使用中」にするのが唯一確実な手段です**。手順 2 の prewarm はこの理由から、pull 用の使い捨てコンテナではなく常駐コンテナの形を採ります。

**3. `imagePullPolicy: Always` はキャッシュ設計を裏切ります**

digest 参照でも `Always` は pod 起動ごとにレジストリへ問い合わせるため、レジストリや PTC の障害時に「キャッシュ済みなのに起動不能」という故障モードを作ります。digest pin と `IfNotPresent` を組みにします。

**4. cpu プールの gp3 throughput は明示的に引き上げます**

gp3 のベースライン 125MiB/s のままだと、NVMe を持たない cpu プールではダウンロードと展開でディスクが律速します。`blockDeviceMappings` で throughput と IOPS を上げます。gp3 は throughput 課金が安く、ノード寿命が短いので月額影響は軽微です。

**5. 高速化の層は必ず良性故障に閉じます**

prewarm、並列化、zstd といった高速化は、全滅しても素のコールド pull に戻るだけであるべきです。ノード起動不能や学習中死亡につながる仕組みを高速化のために持ち込まないことを、採否判断の第一原則にします。

# ワークショップ実施

本節では、恒久コアを投入する前にまず計測し、次に最小コアを入れ、効果を測ってから条件付き最適化に進む、という順序で進めます。この順序自体が本章の主張です。

## 1. コールド pull の待ち時間を分解して計測する

最初にやるべきは高速化ではなく計測です。ユーザー体感の待ち時間を「ノード provisioning」「manifest 往復」「レイヤ取得」「展開」に分解して実測し、以降の全ての採否をこの数字で決めます。

新規ノードを 1 台立ててから、pod イベントのタイムスタンプで内訳を見ます。

```bash
kubectl get events --field-selector involvedObject.name=<pod> \
  --sort-by=.lastTimestamp -o wide
```

`Scheduled` から `Pulling`、`Pulled`、`Started` までの各区間が、それぞれ provisioning・取得・展開のどこに時間を使っているかを示します。展開が支配的なら zstd が効き、取得が支配的なら prewarm や layer 経路が効く、という判断の土台になります。

本書の検証では、Basic02 でビルドした 3.3 GB の学習イメージを digest 指定で起動し、次の数字が出ました。

```text
Scheduled -> Pulling   1s
Pulling   -> Pulled    1m35.9s   (3,337,948,590 bytes = 実効 35 MB/s 程度)
Pulled    -> Started   1s 未満
```

`Pulled` のイベントは `in 1m35.906s (1m35.906s including waiting)` のように取得と展開を合算した値を出します。括弧内の `including waiting` が同じ値であれば、他イメージの pull を待たされていない（並列化が効いている）ことを意味します。この環境では待ち時間ゼロで 1 分半以上かかっており、支配的なのは取得側でした。つまりこの基盤で効くのは zstd よりも prewarm だという判断になります。

計測でもう 1 つ分かることがあります。同じイメージを 2 回目に起動したのに、また 1m18.7s かかりました。1 回目のノードが consolidation で片付けられ、別のノードに載ったためです。**キャッシュはノードに付くので、ノードが入れ替わればキャッシュもゼロに戻ります**。これが次の手順で headroom floor と prewarm を対で入れる理由です。

無リスクな containerd/kubelet の並列化は、本実装ではすでに `karpenter-resources.tf` の `accelerator_user_data` / `cpu_user_data` から EC2NodeClass の `userData` として全プール共通で注入済みです。nodeadm の NodeConfig はブート時の userData なので稼働中ノードに即時反映はできませんが、次に Karpenter が立てる新規ノードからはこの設定で起動します。

```yaml
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  containerd:
    config: |
      [plugins."io.containerd.cri.v1.images"]
        discard_unpacked_layers = false
        max_concurrent_downloads = 8
  kubelet:
    config:
      serializeImagePulls: false
      maxParallelImagePulls: 8
      imageMaximumGCAge: "168h"
```

## 2. 恒久コアを投入する

計測で痛みの所在を確認したら、恒久コアの 2 点を入れます。

headroom floor は、低優先度の pause Deployment で cpu プールにノードを常時 1 台維持します。cpu プールを狙い撃ちする `nodeSelector: node-role: cpu`（`karpenter-resources.tf` の `nodepool_cpu` が付与するラベル）と、CPU NodePool の `consolidationPolicy: WhenEmptyOrUnderutilized` による consolidation でノードを畳まれないようにする `karpenter.sh/do-not-disrupt: "true"` アノテーションの両方が必須です。ここでは常時 1 台維持のコストを許容する前提を置き、作業時間帯だけに絞る CronJob 制御は行いません。

```yaml
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: cache-headroom
value: -10
globalDefault: false
description: "Keeps a warm cache node alive; evicted first under pressure"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cache-headroom
  namespace: kube-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cache-headroom
  template:
    metadata:
      labels:
        app: cache-headroom
      annotations:
        karpenter.sh/do-not-disrupt: "true"
    spec:
      priorityClassName: cache-headroom
      nodeSelector:
        node-role: cpu
      containers:
        - name: pause
          image: registry.k8s.io/pause:3.10
          resources:
            requests:
              cpu: "1"
              memory: 1Gi
```

この 2 つが買っているものは別です。headroom floor が消すのは**ノード起動の待ち時間**（Karpenter がノードを起こして Ready にするまでの 1〜2 分）で、prewarm が消すのは**イメージ取得の待ち時間**です。同じノードで両方を消したいなら、そのプールに両方を効かせる必要があります。本章の例では headroom を cpu プール、prewarm を GPU プールに置いていますが、これは説明のための分担です。実際にどちらの待ち時間も削りたいプールでは、そのプール名で両方を指定してください。

ここで 1 つ実測から分かった落とし穴があります。`do-not-disrupt` は Karpenter の consolidation を確かに止めます（`DisruptionBlocked ... Pod has "karpenter.sh/do-not-disrupt" annotation` というイベントで確認できます）が、**止めるのは Karpenter だけ**です。PriorityClass を `-10` にした headroom pod は、優先度既定値 0 の普通の pod がノードに入りきらないときスケジューラに preempt されます。実機では検証用の pod を 1 つ投げただけで headroom pod が追い出され、別ノードに再スケジュールされて、温めたノードが空になり consolidation で消えました。

つまり `do-not-disrupt` は「Karpenter が能動的にノードを畳むこと」への対策であって、キャッシュの永続性を保証しません。低優先度である以上、混雑時に押し出されるのは設計どおりの挙動です（そのために `-10` にしています）。押し出した相手は、まさにノードを待っていたワークロードなので、これは失敗ではなく overprovisioning が機能した形です。headroom floor は「空いているときに 1 台起きた状態を保つ」仕組みだと理解し、キャッシュそのものは次の prewarm で担保します。

ここで優先度を上げて preempt を防ごうとしてはいけません。優先度を 0 以上にすれば確かに preempt されなくなりますが、代わりにノードを待っていたワークロードが Karpenter の起動を 1〜2 分待つことになり、消したかった待ち時間をワークロード側に押し付けるだけです。また `preemptionPolicy: Never` も対策になりません。これは「その Pod が他を preempt するか」の設定であって、preempt される側の耐性は一切変わりません。

なお `do-not-disrupt` が止めるのは Karpenter の consolidation だけです。ディスク逼迫による kubelet の eviction、Spot の中断、NodePool の `expireAfter` による満了、手動の削除はいずれも止まりません。

prewarm DaemonSet は、温めたいイメージを列挙して各ノードで pull させる仕組みです。ここでの設計判断は、**pull を `ctr` ではなく kubelet にやらせる**ことです。温めたいイメージを何もしない initContainer として並べると、kubelet が通常のワークロードとまったく同じ経路で pull します。

```yaml
containers:
  - name: warm-0
    image: <account>.dkr.ecr.<region>.amazonaws.com/ddp-sample@sha256:...
    imagePullPolicy: IfNotPresent
    # イメージは動かず居るだけでよい。sleep infinity は BusyBox と coreutils で
    # 挙動が分かれるので、両方が受け付ける最大値を使う
    command: ["/bin/sh", "-c", "exec sleep 2147483647"]
    resources:
      requests:
        cpu: 1m
        memory: 8Mi
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop: ["ALL"]
      seccompProfile:
        type: RuntimeDefault
```

`ctr -n k8s.io images pull` を使う書き方も広く見られますが、こちらを採りません。`ctr` は kubelet のクレデンシャルプロバイダを経由しないので、自アカウント ECR の digest を pull するには Pod Identity で IAM ロールを結んだ ServiceAccount と `aws ecr get-login-password` のトークン受け渡しが必要になり、さらに `containerd.sock` の hostPath マウント（ノードの root 相当の権限）と、`aws` CLI と `ctr` の両方を含む専用イメージまで要ります。キャッシュを温める道具自体がコールド pull される、という本末転倒も起きます。

kubelet に pull させる形なら、これらは 1 つも要りません。ECR 認証はノードロールの権限がそのまま効き、ServiceAccount も Pod Identity も IAM ロールも `containerd.sock` も専用イメージも不要です。実機でも、ServiceAccount を作らずに自アカウント ECR の digest pull が通ることを確認しています。本章の第一原則「高速化のためにノードを危険にしない」を満たすのはこちらだけなので、恒久コアに置けるのは kubelet 経由の実装だと判断しました。

ただしこの方式は「pull するだけ」ではなく、列挙したイメージのシェルを全ノードで実行します。イメージの既定 USER は多くの場合 root なので、`containerd.sock` を断った理屈と整合させるなら、ここも権限を落としておく必要があります。`sleep` を実行するだけなら何の権限も要らないので、上の例のように capability を全部落として root filesystem を読み取り専用にします。

温めたイメージを「何もしない initContainer」で pull させる書き方も見かけますが、これは採れません。initContainer は終了するので、そのイメージは kubelet から見て使用中ではなくなり、imageGC の回収候補に戻ります。しかも DaemonSet の Pod は Running のままなので、**キャッシュが消えた後も「温まっている」という顔で立ち続けます**。温めた側が気づけない壊れ方であり、`rollout status` が成功したことは何も保証しません。ノード寿命が長い Capacity Block のプールで最も静かに壊れます。

常駐コンテナにすれば、イメージは本物の「使用中」になり、どの GC 経路からも回収されません。代償として、ディスク逼迫時にノードはこのイメージを回収できず、まず prewarm Pod を追い出すことになります。prewarm が最も低い優先度である以上これは正しい順序で、追い出された時点でイメージは回収可能に戻ります。

この形が成立するのに必要なノード側の設定は、手順 1 で入れた `maxParallelImagePulls = 8` と `serializeImagePulls: false` だけです。これがあると、新規ノードで prewarm の pull と本命ワークロードの pull が同時に走り、ワークロードが prewarm の後ろに並びません。逆にこの設定が失われた場合の壊れ方は「遅くなる」であって、キャッシュが消えるわけではありません。

参照実装では Helm chart 側のテンプレートとして持っています。イメージの一覧はワークロードのライフサイクルに属する（vLLM のバージョンを上げれば温める対象も変わる）ので、`terraform apply` ではなく `helm` の値で更新できる場所に置いています。

```bash
# 温める digest を取得する（タグではなく digest で固定する）
# imageTags で絞るのは、失敗ビルドの残骸や中間イメージのような untagged な digest を
# 拾わないため。単に最後の push を取ると、それらや他人の push を掴むことがある
DIGEST=$(aws ecr describe-images \
  --repository-name "$(terraform output -raw ddp_sample_ecr_url | cut -d/ -f2-)" \
  --query 'sort_by(imageDetails[?imageTags],&imagePushedAt)[-1].imageDigest' --output text)

helm template exp charts/experiments -n "$NAMESPACE" \
  --set "prewarm.gpu-ddp.images[0]=$(terraform output -raw ddp_sample_ecr_url)@${DIGEST}" \
  --set prewarmPriorityClassName=cache-headroom \
  -s templates/image-prewarm.yaml \
  | kubectl apply -f -

kubectl -n "$NAMESPACE" rollout status ds/image-prewarm-gpu-ddp --timeout=10m
```

`helm template | kubectl apply` は Helm のリリース管理を通らないので、古いものが自動では消えません。プール名を変えたときや、あるプールの `images` を空にしたときは、対応する DaemonSet を明示的に削除してください。放っておくと「Neuron をやめたのに Neuron のイメージを全ノードで温め続ける」状態になります。

```bash
kubectl -n "$NAMESPACE" get ds -o name | grep image-prewarm
kubectl -n "$NAMESPACE" delete ds image-prewarm-<やめたプール>
```

`prewarm.<プール名>` のキーは `accelerator_pools` のキー、つまり Karpenter がノードに付ける `node-role` ラベルの値です。プールごとに DaemonSet が分かれるので、GPU プールに Neuron イメージを温めるような無駄が起きません。タグではなく digest で指定するのは、タグが可変だとノード上のキャッシュとレジストリの中身がずれても気づけないためです。

イメージの列挙は必要なものだけにしてください。1 エントリごとにそのプールの全ノードで pull とディスクを消費し、Capacity Block のプールでは予約が課金されている間にそれが走ります。ただし CB プールを除外すべきという話ではありません。CB では予約開始直後、ワークロードが着地する前に温めるので、どのみち課金されている時間を使うことになり、この仕組みの価値が最も高い場所です。

digest は**マルチアーキのマニフェストリストの digest**を指定してください。アーキ別の子 digest を指定するとノード側のプラットフォーム解決を飛ばすので、アーキが合わないプールでは全ノードで `exec format error` になります。

対象イメージには `/bin/sh` と `sleep` が必要で、既定 USER のまま権限を落とした状態でそれらが動く必要があります（本書で扱う CUDA / vLLM / Neuron 系のイメージはいずれも該当します）。distroless や scratch のイメージはシェルを持たないのでこの方法では温められません。この場合コンテナが crash loop に入り、DaemonSet が恒久的に不健全になるだけで、キャッシュも温まりません。

## 3. 効果を測って条件付き最適化に進む

恒久コア投入の前後で、開発反復シナリオの pod 起動時間（P50/P95）と、「push から次のデプロイまでに pull が発生したか」を比較します。ここで初めて、計測で展開時間が支配的だと出た場合に限り、自ビルドレイヤのみを zstd 化する条件付き最適化に進みます。

本書の検証では、同じ 3.3 GB のイメージで次の差が出ました。

| 条件 | ワークロードの pull 時間 |
| --- | --- |
| キャッシュなしの新規ノード | 1m35.9s |
| キャッシュを持つノードが consolidation で消え、別ノードに載った | 1m18.7s |
| 新規ノードで prewarm と同時に起動（同じ digest） | 1m9.3s |
| prewarm 済みのノード | 0s（`already present on machine`） |

prewarm 済みノードでは `Pulling` イベントすら出ず、`Container image ... already present on machine and can be accessed by the pod` になります。これが確認できれば効果測定は完了です。逆に `Pulling` が出る場合は、ノードが入れ替わって prewarm がまだ終わっていないか、digest がずれています。

3 行目は「prewarm が新規ノードで走っている最中にワークロードが着地する」ケースで、Karpenter がノードを起こす経路では普通に起きます。prewarm が帯域を取ってワークロードを遅くするのではないか、という懸念がありますが、同じ digest を指している限りそうはなりません。kubelet は同一イメージの pull をまとめるので、両者は 1 回の pull を共有します。実測でも prewarm 側とワークロード側が同じ 1m9.2s を報告しており、単独のコールド pull より遅くなっていません。

これは prewarm とワークロードで**同じ digest を指していること**が前提です。digest がずれていると 2 本の別々の pull になり、そのときに `maxParallelImagePulls` が効いてワークロードが prewarm の後ろに並ばずに済みます。digest をずらさないこと自体がキャッシュ戦略の要点でもあるので、prewarm の digest はワークロードが参照するものと同じ値を渡してください。

この結果は取得が支配的だったこの環境の数字です。手順 1 の計測で展開が支配的だと出た場合に限り、次の zstd 化に進んでください。

zstd 化は BuildKit の出力で行いますが、`force-compression=true` を付けて外部ベースイメージまで再圧縮してはいけません。ベースレイヤの digest が変わって共有・キャッシュヒットを壊すためです。自分が積む新規レイヤだけを zstd にする混在方式が正解です。

```bash
docker buildx build \
  --output type=image,name=<ecr-repo>:<tag>,push=true,compression=zstd,oci-mediatypes=true \
  .
```

外部イメージをミラーする場合は `crane copy` で無変換のままコピーします。ミラーでの再圧縮は digest が変わって上流の署名と provenance を無効化するため禁止です。

## 4. 良性故障を検証する

恒久コアと最適化を入れたら、高速化の層を意図的に止めても pull が通ることを確認します。DaemonSet に `replicas` フィールドは存在しないため `kubectl scale` は使えません。代わりに、実在しないラベルを狙う `nodeSelector` を一時的に注入して Pod をどのノードにも乗せない状態にし、prewarm DaemonSet を止めた状態で新規ノードを立て、pod が通常のコールド pull で正常に起動することを見ます。

```bash
kubectl -n "$NAMESPACE" patch daemonset image-prewarm-gpu-ddp \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"prewarm-disabled":"true"}}}}}'
# DESIRED が 0 になり Pod が消えたことを確認する
kubectl -n "$NAMESPACE" get ds image-prewarm-gpu-ddp

# 新規ノードを誘発し、prewarm を経由しない pod が Running になることを確認する

# 検証後は nodeSelector のパッチを外して元に戻す
kubectl -n "$NAMESPACE" patch daemonset image-prewarm-gpu-ddp \
  --type json -p '[{"op":"remove","path":"/spec/template/spec/nodeSelector/prewarm-disabled"}]'
```

`kubectl scale` を試すと `Error from server (NotFound): the server could not find the requested resource` になります。DaemonSet に scale サブリソースが無いためで、パッチを使う理由がこれです。

高速化の層が全滅しても素のコールド pull に退化するだけである、という良性故障の性質を実地で確認できれば、この層を安心して恒久基盤に組み込めます。

# まとめ

本章では、変化し続けるイメージを扱うキャッシュ層を、この分散 AI 基盤に恒久的に組み込む設計を示しました。キャッシュの寿命はノードの寿命に等しいこと、digest pin が stale 故障を消すこと、そして高速化の層は必ず「失敗しても素のコールド pull に退化するだけ」の良性故障に閉じるべきこと、という原則を軸に据えました。まず計測し、headroom floor と素の prewarm DaemonSet という退屈な恒久コアを入れ、効果を測ってから zstd などの条件付き最適化に進む、という順序自体が本章の主張です。SOCI の lazy pull や Spegel を恒久コアに入れなかったのは、それらがノード起動不能や学習中死亡という受け入れ難い故障を持ち込むからであり、恒久基盤に要るのは派手さではなく退屈な堅牢さです。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter Disruption（consolidation / drift / expiration）](https://karpenter.sh/docs/concepts/disruption/)
- [Amazon ECR pull through cache](https://docs.aws.amazon.com/AmazonECR/latest/userguide/pull-through-cache.html)
- [Mountpoint for Amazon S3 CSI driver](https://github.com/awslabs/mountpoint-s3-csi-driver)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
