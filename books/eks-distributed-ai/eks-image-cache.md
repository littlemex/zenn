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

- **キャッシュの寿命はノードの寿命に等しい**。Karpenter は `WhenEmpty` と短い `consolidateAfter` でアイドルノードを即座に回収します（Basic04 で見た挙動です）。ノード内のキャッシュをいくら磨いても、ノードが消えればキャッシュも消えます。検証反復のコールド pull の主因はキャッシュ層の不在ではなく、ノードの入れ替わり（churn）です
- **digest pin 運用が最強のキャッシュの味方になる**。イメージをタグではなく digest で参照すると、参照はイミュータブルになり「stale キャッシュ」という故障クラスがほぼ消滅します。`imagePullPolicy: IfNotPresent` を安全に使えるようになります

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

accelerator プール（gpu-p5en / gpu-dev / trn2）は概ね完成しています。nodeadm の `localStorage.strategy: Raid0` により containerd の data-root が NVMe instance store に載り、instance store は数 TB 級なので imageGC の既定閾値（85/80）に実質到達しません。ここで追加すべきは kubelet の `imageMaximumGCAge` を明示設定し、多世代 digest の無限堆積だけを防ぐことです。

一方 cpu プールは NVMe を持たず、imagefs と nodefs が単一の gp3 に同居します。ここで見落とされがちな支配的ボトルネックは **gp3 のベースライン throughput 125MiB/s** です。イメージのダウンロードと展開で書き込みが二重に走り、ディスクだけで数分溶けます。EC2NodeClass の `blockDeviceMappings` で throughput と IOPS を引き上げるのが、今日できて絶対に壊れない改善です。

全プール共通で、kubelet の `serializeImagePulls: false` と `maxParallelImagePulls`、containerd の `max_concurrent_downloads` の引き上げを nodeadm の NodeConfig で注入します。宣言的でステートレスなので、失敗しても挙動が元に戻るだけです。

### 層 C: ノード跨ぎ再利用

ここがキャッシュ設計の主戦場です。ノードが 0 台から立ち上がる瞬間のコールド pull を潰すには、ノードを跨いだ再利用が要ります。

この基盤の恒久コアは、次の 2 つだけで構成します。

- **headroom floor**: 低優先度の pause Deployment で、作業時間帯の cpu プールに温かいキャッシュを持つノードを最小 1 台維持します。ノードが生き残る、すなわちキャッシュが生き残ることが、ノード provisioning 待ちとコールド pull の両方を同時に殺す最も効く一手です
- **prewarm DaemonSet（素朴実装）**: ConfigMap に列挙した pinned digest のリストを読んで `ctr` で pull するだけの DaemonSet です。ノードが新規参加すると自動で温まります。コントローラも CRD も不要で、pull が失敗してもワークロードは通常のコールド pull に落ちるだけという自明な故障挙動を持ちます

この 2 つに共通する状態管理の原則は、**キャッシュの状態はノードローカルの containerd にしか持たせない**ことです。共有キャッシュサービスを置かないので、「詰まったらノードを入れ替えれば直る」という一点に復旧手順を固定できます。

:::message
P2P registry mirror の Spegel は魅力的に見えますが、恒久コアからは外しています。Spegel は「既に温かいノードが複数居る」ことを前提とする技術で、この基盤の痛みである「ノードが 0 台から立ち上がる瞬間」とは前提が噛み合いません。しかも新規ノード起動直後に mirror 解決の遅延がレイヤごとに積み上がり、コールド pull を高速化するはずの部品がコールド pull を遅くする有害ケースがあります。大規模同時 scale-out で ECR 転送重複が支配的だと計測で示された場合に限り、再評価します。
:::

### 層 D: ガバナンス

キャッシュ戦略の最終防衛線は **「そもそも巨大イメージを作らせない」** ことです。モデル重みやデータセットをイメージレイヤに入れると、どの層のキャッシュ設計もいずれ破綻します。恒久ルールとして、イメージはコードと依存のみ（目安 15GB 上限）とし、重みは Amazon S3 に置いて Mountpoint for Amazon S3 CSI や推論フレームワークの S3 直接ロードで取得します。この使い分けは Basic10 の共有ストレージと地続きの判断です。

## なぜ SOCI や Spegel を恒久コアに入れないか

lazy pull の SOCI や P2P の Spegel は、技術記事では華やかに紹介されがちです。しかしこの基盤の要求は「速いこと」と同時に「詰まって壊れないこと」であり、両者はこの後半の要求と衝突します。

SOCI の lazy pull は、イメージの一部しか触らないワークロードで利得が最大化します。ところが学習イメージは起動直後に CUDA/Neuron ランタイムと Python パッケージ群の大半を実際に読むため、lazy はコストを「起動時」から「実行初期」へ移すだけで総転送量は減りません。さらに深刻なのは、**数日走る学習ジョブの途中にレイヤ span の Range GET 失敗が I/O エラーとしてコンテナ内へ噴出する**ことです。ネットワークの瞬断が「pod 起動リトライ」で済む世界から「学習ジョブ死亡」の世界に変わります。加えて soci-snapshotter は AMI に同梱されず、全ノードに自己管理の常駐デーモンを 1 個増やす決断になります。これが落ちれば pull も起動も不能になり、prewarm DaemonSet とは正反対の致命的な故障特性を持ちます。

この基盤が高速化に採る各層は、いずれも「失敗しても素のコールド pull に退化するだけ」の良性故障モードに閉じています。ノード起動不能や学習中の死亡につながる常駐データパス（SOCI lazy、ステートフルミラー、snapshot 焼き込み）を恒久基盤から排除したことこそが、この設計の判断そのものです。

## AL2023 維持 vs Bottlerocket 移行

Bottlerocket の実利はスナップショット事前ロード（`aws-samples/bottlerocket-images-cache`）ですが、これはスナップショット焼き込みパイプラインの運用そのものであり、イメージが変わるたびの再生成・陳腐化・鮮度管理という負債を持ちます。本基盤は Neuron DKMS、EFA、GPU 専用 AL2023 AMI、nodeadm RAID0、rootless BuildKit といったホスト層の前提が厚く、Bottlerocket の不変・最小ホストモデルはこれらの自由度を大きく削ります。イメージキャッシュのための移行としては割に合わないため、**AL2023 維持で断定**します。

## 全体の中での位置付け

本章は Basic03 で導入した Karpenter のノード churn と、Basic10 の共有ストレージの判断の上に成り立っています。Karpenter がノードを積極的に回収するからこそキャッシュの寿命がノードの寿命に縛られ、だからこそ重みをイメージに入れず S3 に外出しするガバナンスが効いてきます。イメージキャッシュは単独の機能ではなく、ノードのライフサイクルとストレージ設計の交点にある運用最適化の層です。

## 注意

**1. S3 gateway endpoint の欠落は他のどの施策より優先して潰す**

ECR のレイヤは S3 presigned URL で配られるため、S3 gateway endpoint が無いと全レイヤが NAT を通ります。イメージが大きい基盤ほど、ここが帯域と課金の律速点になります。

**2. imageGC が次に使うイメージを消す**

prewarm 済みでまだ使っていないイメージは、kubelet から見ると未使用であり imageGC の削除候補です。`imageMaximumGCAge` と閾値を prewarm 運用を前提に設計しないと、温めたそばから消される事故になります。

**3. `imagePullPolicy: Always` はキャッシュ設計を裏切る**

digest 参照でも `Always` は pod 起動ごとにレジストリへ問い合わせるため、レジストリや PTC の障害時に「キャッシュ済みなのに起動不能」という故障モードを作ります。digest pin と `IfNotPresent` を組みにします。

**4. cpu プールの gp3 throughput は明示的に引き上げる**

gp3 のベースライン 125MiB/s のままだと、NVMe を持たない cpu プールではダウンロードと展開でディスクが律速します。`blockDeviceMappings` で throughput と IOPS を上げます。gp3 は throughput 課金が安く、ノード寿命が短いので月額影響は軽微です。

**5. 高速化の層は必ず良性故障に閉じる**

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

同時に、無リスクな containerd 並列化だけは即適用します。nodeadm の NodeConfig で次を注入します。

```yaml
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  kubelet:
    config:
      serializeImagePulls: false
      maxParallelImagePulls: 8
```

## 2. 恒久コアを投入する

計測で痛みの所在を確認したら、恒久コアの 2 点を入れます。

headroom floor は、低優先度の pause Deployment で作業時間帯にノードを 1 台維持します。

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
    spec:
      priorityClassName: cache-headroom
      containers:
        - name: pause
          image: registry.k8s.io/pause:3.10
          resources:
            requests:
              cpu: "1"
              memory: 1Gi
```

prewarm DaemonSet は、ConfigMap に列挙した digest を各ノードで pull するだけの実装です。

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: prewarm-images
  namespace: kube-system
data:
  images: |
    <account>.dkr.ecr.<region>.amazonaws.com/ddp-sample@sha256:...
    <account>.dkr.ecr.<region>.amazonaws.com/vllm@sha256:...
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: image-prewarm
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: image-prewarm
  template:
    metadata:
      labels:
        app: image-prewarm
    spec:
      tolerations:
        - operator: Exists
      containers:
        - name: prewarm
          image: <account>.dkr.ecr.<region>.amazonaws.com/prewarm:latest
          command: ["/bin/sh", "-c"]
          args:
            - |
              for img in $(cat /config/images); do
                ctr -n k8s.io images pull "$img" || true
              done
              sleep infinity
          volumeMounts:
            - name: config
              mountPath: /config
            - name: containerd-sock
              mountPath: /run/containerd/containerd.sock
      volumes:
        - name: config
          configMap:
            name: prewarm-images
        - name: containerd-sock
          hostPath:
            path: /run/containerd/containerd.sock
```

## 3. 効果を測って条件付き最適化に進む

恒久コア投入の前後で、開発反復シナリオの pod 起動時間（P50/P95）と、「push から次のデプロイまでに pull が発生したか」を比較します。ここで初めて、計測で展開時間が支配的だと出た場合に限り、自ビルドレイヤのみを zstd 化する条件付き最適化に進みます。

zstd 化は BuildKit の出力で行いますが、`force-compression=true` を付けて外部ベースイメージまで再圧縮してはいけません。ベースレイヤの digest が変わって共有・キャッシュヒットを壊すためです。自分が積む新規レイヤだけを zstd にする混在方式が正解です。

```bash
docker buildx build \
  --output type=image,name=<ecr-repo>:<tag>,push=true,compression=zstd \
  .
```

外部イメージをミラーする場合は `crane copy` で無変換のままコピーします。ミラーでの再圧縮は digest が変わって上流の署名と provenance を無効化するため禁止です。

## 4. 良性故障を検証する

恒久コアと最適化を入れたら、高速化の層を意図的に止めても pull が通ることを確認します。prewarm DaemonSet を止めた状態で新規ノードを立て、pod が通常のコールド pull で正常に起動することを見ます。

```bash
kubectl -n kube-system scale daemonset image-prewarm --replicas=0
# 新規ノードを誘発し、pod が Running になることを確認する
kubectl -n kube-system rollout restart daemonset image-prewarm
```

高速化の層が全滅しても素のコールド pull に退化するだけである、という良性故障の性質を実地で確認できれば、この層を安心して恒久基盤に組み込めます。

# まとめ

本章では、変化し続けるイメージを扱うキャッシュ層を、この分散 AI 基盤に恒久的に組み込む設計を示しました。キャッシュの寿命はノードの寿命に等しいこと、digest pin が stale 故障を消すこと、そして高速化の層は必ず「失敗しても素のコールド pull に退化するだけ」の良性故障に閉じるべきこと、という原則を軸に据えました。まず計測し、headroom floor と素の prewarm DaemonSet という退屈な恒久コアを入れ、効果を測ってから zstd などの条件付き最適化に進む、という順序自体が本章の主張です。SOCI の lazy pull や Spegel を恒久コアに入れなかったのは、それらがノード起動不能や学習中死亡という受け入れ難い故障を持ち込むからであり、恒久基盤に要るのは派手さではなく退屈な堅牢さです。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter Disruption（consolidation / drift / expiration）](https://karpenter.sh/docs/concepts/disruption/)
- [Amazon ECR pull through cache](https://docs.aws.amazon.com/AmazonECR/latest/userguide/pull-through-cache.html)
- [Mountpoint for Amazon S3 CSI driver](https://github.com/awslabs/mountpoint-s3-csi-driver)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
