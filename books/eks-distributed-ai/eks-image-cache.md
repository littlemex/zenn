---
title: "Advanced01 - イメージキャッシュ戦略を恒久基盤に組み込む"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、変化し続けるコンテナイメージをノードを跨いで賢くキャッシュし、かつ破綻しないためのイメージキャッシュ層を、この分散 AI 基盤に恒久的に組み込む考え方を扱います。特定の 1 イメージを速くする小手先の話ではなく、「キャッシュが無くて毎回コールド pull で待たされるのも、キャッシュがディスクを埋めてノードが起動不能になるのも、どちらも困る」という要求に対して、退屈だが壊れない設計を選ぶ判断を示します。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のノード層とレジストリ層のあいだ、つまり「イメージがどこから来て、どこに残り、次にどう再利用されるか」という部分です。Karpenter が起動・回収するノードの上で、コンテナイメージの取得と保持をどう設計すれば実験の反復が速くなるか、そしてその高速化が障害の種にならないかを見ていきます。

## これは何をするものか

この基盤は今後あらゆるワークロード（大型 GPU/Neuron 学習イメージ、vLLM、多様なフレームワーク）で使う恒久基盤です。そこで問題になるのが、コンテナイメージの取得コストです。数 GB のイメージを毎回レジストリからコールド pull すると、実験の反復ごとに数十秒から数分の待ち時間が積み重なります。

素朴な発想では「キャッシュを足せば速くなる」と考えますが、この基盤には無視できない支配的な事実が 2 つあります。

- **キャッシュの寿命はノードの寿命に等しくなります**。Karpenter のノード回収の挙動はプールごとに異なり（Basic03 で導入した Karpenter の disruption 設定です）、プールごとに明示指定していない場合の既定では、EFA や予約系のアクセラレータプール（Basic06 で作る `gpu-p4d` や Basic09 の `trn2` など）は `consolidateAfter: Never` でノードを維持し、非 EFA のアクセラレータプール（Basic04 の `gpu-ddp` など）と cpu プールは短い `consolidateAfter`（5m / 30s）でアイドルノードを回収します。プール側で値を書いた場合はそちらが優先されます。ノード内のキャッシュをいくら磨いても、対象ノードが消えればキャッシュも消えます。検証反復のコールド pull の主因はキャッシュ層の不在ではなく、回収対象プールでのノードの入れ替わり（churn）です
- **digest pin 運用が最強のキャッシュの味方になります**。イメージをタグではなく digest で参照すると、参照はイミュータブルになり「stale キャッシュ」という故障クラスがほぼ消滅します。`imagePullPolicy: IfNotPresent` を安全に使えるようになります

この 2 点を踏まえると、キャッシュ設計の目的は「速くすること」だけでは足りません。**速くする仕組みが失敗したときに、通常のコールド pull に気づかれずに戻るだけで済むこと**、つまり良性の故障モードに閉じることが同じくらい重要です。ノードが起動できなくなったり、実行中の学習ジョブが死んだりする故障を持ち込む高速化は、この基盤には入れません。

## キャッシュは物理的に 4 層しかない

![イメージキャッシュの層と被害が広がらない失敗の原則](/images/books/eks-distributed-ai/image-cache-layers.png)

キャッシュと呼べる実体は、この基盤では物理的に次の層にしか存在しません。層ごとに、どこまで整備すべきかを判定します。

### 層 0: ネットワーク基礎

ECR のレイヤ実体は、リージョンによって S3 の presigned URL 経由で配られます。レイヤが S3 経由になるリージョンでは、VPC に **S3 gateway endpoint** が無いと、イメージの大部分を占めるレイヤの取得が NAT 経由になり、帯域と課金の両面で律速します。ECR interface endpoint（`ecr.api` / `ecr.dkr`）も併せて用意しますが、こちらを通るのは認証トークンや manifest といった KB 単位のメタデータ往復であり、pull 速度そのものへの寄与は小さく、位置づけは「NAT 障害時に pull が死なない」という衛生面です。この基盤ではどちらも [`vpc-endpoints.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/vpc-endpoints.tf) で IaC 固定しています。

### 層 A: pull 経路

恒久ルールは **「ランタイムで参照するイメージは全て自アカウントの Amazon ECR 発とする」** です。BuildKit で焼く自前イメージは既にそうなっており、直接使う外部イメージ（vLLM 公式、NGC ベースなど）は `crane copy` などで自 ECR にミラーしてから使います。これは運用として決めていることで、ミラーを自動化する仕組みや、外部レジストリの直接参照を止める検査はこの実装には入っていません。後の手順で使う `registry.k8s.io/pause` のように、本書が説明のために外部レジストリを直に書いている箇所は、この恒久ルールから外れています。自分の環境で恒久的に置くものは自 ECR にミラーしてから参照してください。なお prewarm チャートもビルド Job も、イメージ参照が自 ECR かどうかは検査しません (prewarm が見るのは digest 指定かどうかだけです)。これは実装のガードではなく運用ルールです。

補助的に ECR pull-through cache（PTC）を Docker Hub や `ghcr` などに設定できますが、位置づけは開発時の利便性とミラー漏れの保険にとどめます。GPU 基盤で最も引きたい上流である nvcr.io（NVIDIA NGC）が PTC 非対応であること、そして「未キャッシュの新規 digest かつ上流障害」では PTC でも pull 不能になることから、ランタイム経路を PTC に依存させるのは避けます。

### 層 B: ノード内保持

accelerator プール（`terraform.tfvars` の `accelerator_pools` にコメントで例示されている `gpu-p5en` / `trn2` のような構成、この変数の既定値は空マップです）は概ね完成しています。nodeadm の `localStorage.strategy: Raid0` により、containerd の data-root が NVMe instance store に載ります。p5 や p5en、trn2 のように大容量の instance store を持つプールでは容量が数 TB 級になるので、imageGC の既定閾値（85/80）には届きにくくなります。どのインスタンスタイプを並べるかはプールの定義しだいなので、小容量のタイプではこの前提は成り立ちません。ここは本実装ではすでに IaC 固定済みで、kubelet の `imageMaximumGCAge` を `168h` に明示設定し、多世代 digest の無限堆積を防いでいます（[`karpenter-resources.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/karpenter-resources.tf) の `local.image_maximum_gc_age` を `accelerator_user_data` に注入）。この設定は**削除を追加する側**であることに注意してください。「未使用のまま 168 時間を超えたイメージを、ディスク閾値に達していなくても消す」という設定であり、閾値 GC（`imageGCHighThresholdPercent` の既定 85%）を置き換えたり抑止したりはしません。イメージを保護する設定ではないので、キャッシュを残したいイメージをこれで守ることはできません。

一方 cpu プールは NVMe を持たず、imagefs と nodefs が単一の gp3 に同居します。ここで見落とされがちな支配的ボトルネックは **gp3 のベースライン throughput 125MiB/s** です。イメージのダウンロードと展開で書き込みが二重に走り、ディスクだけで数分溶けます。ここもすでに IaC 固定済みで、CPU 用 EC2NodeClass の `blockDeviceMappings` に `throughput = 500` / `iops = 6000`（[`variables.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/variables.tf) の `cpu_node_volume_throughput` / `cpu_node_volume_iops` の既定値）を設定し、gp3 のベースラインより高いスループットを確保しています。

全プール共通で、kubelet の `serializeImagePulls: false` と `maxParallelImagePulls: 8`、containerd の `max_concurrent_downloads: 8` の引き上げも、`karpenter-resources.tf` の `accelerator_user_data` / `cpu_user_data` で nodeadm の NodeConfig にすでに注入済みです。宣言的でステートレスなので、失敗しても挙動が元に戻るだけです。

### 層 C: ノード跨ぎ再利用

ここがキャッシュ設計の主戦場です。ノードが 0 台から立ち上がる瞬間のコールド pull を潰すには、ノードを跨いだ再利用が要ります。

この基盤の恒久コアは、次の 2 つだけで構成します。

- **headroom floor (アイドル時もノードを 1 台残す仕組み)**: 低優先度の pause Deployment で、cpu プールにノードを常時 1 台維持します。ノードが生きていればノード provisioning の待ち時間がゼロになるので、prewarm と組み合わせたときに役に立ちます。ただし低優先度である以上、混雑時にはスケジューラに押し出されてノードごと入れ替わり得るため、これ単体ではキャッシュの永続性を保証しません（手順 2 で実測を示します）
- **prewarm DaemonSet（素朴実装）**: 温めたいイメージを、何もせず残り続けるだけのコンテナとして並べ、kubelet に pull させる DaemonSet です。ノードが新規参加すると自動で温まります。コントローラも CRD も不要で、認証も通常のワークロードと同じ経路なので追加の前提を 1 つも持ち込みません。prewarm を止めても壊しても、ワークロードは通常のコールド pull を使うだけで済みます (ただし digest の誤りや ECR の権限不足、レジストリ障害のように pull そのものが失敗する原因であれば、本命のワークロードも同じ理由で失敗します)

この 2 つに共通する状態管理の原則は、**キャッシュの状態はノードローカルの containerd にしか持たせない**ことです。共有キャッシュサービスを置かないので、「問題が起きたらノードを入れ替えれば直る」という一点に復旧手順を固定できます。

:::message
P2P registry mirror の Spegel は魅力的に見えますが、恒久コアからは外しています。Spegel は「既に温かいノードが複数居る」ことを前提とする技術で、この基盤の痛みである「ノードが 0 台から立ち上がる瞬間」とは前提が噛み合いません。しかも新規ノード起動直後に mirror 解決の遅延がレイヤごとに積み上がり、コールド pull を高速化するはずの部品がコールド pull を遅くする有害ケースがあります。大規模同時 scale-out で ECR 転送重複が支配的だと計測で示された場合に限り、再評価します。
:::

### 層 D: ガバナンス

キャッシュ戦略の最終防衛線は **「そもそも巨大イメージを作らせない」** ことです。モデル重みやデータセットをイメージレイヤに入れると、どの層のキャッシュ設計もいずれ破綻します。恒久ルールとして、イメージはコードと依存のみ（目安 15GB 上限）とし、重みは Amazon S3 に置いて Mountpoint for Amazon S3 CSI や推論フレームワークの S3 直接ロードで取得します。この使い分けは Basic10 の共有ストレージと地続きの判断です。ただし cpu プールで RL 学習のような ~18GB 級イメージを扱う既存の運用例もあり（`variables.tf` の `cpu_node_volume_size` のコメント）、15GB はあくまで新規イメージ設計時の目安であって、超える既存イメージを許さない絶対値ではありません。この上限を検査したり拒否したりする仕組みは実装に無く、運用上の取り決めです。

## なぜ SOCI や Spegel を恒久コアに入れないか

lazy pull の SOCI や P2P の Spegel は、技術記事では華やかに紹介されがちです。しかしこの基盤の要求は「速いこと」と同時に「行き行き詰まって壊れないこと」であり、両者はこの後半の要求と衝突します。

SOCI の lazy pull は、イメージの一部しか触らないワークロードで利得が最大化します。ところが学習イメージは起動直後に CUDA/Neuron ランタイムと Python パッケージ群の大半を実際に読むため、lazy はコストを「起動時」から「実行初期」へ移すだけで総転送量は減りません。さらに深刻なのは、**数日走る学習ジョブの途中にレイヤ span の Range GET 失敗が I/O エラーとしてコンテナ内へ噴出する**ことです。ネットワークの瞬断が「pod 起動リトライ」で済む世界から「学習ジョブ死亡」の世界に変わります。加えて soci-snapshotter は AMI に同梱されず、全ノードに自己管理の常駐デーモンを 1 個増やす決断になります。これが落ちれば pull も起動も不能になり、prewarm DaemonSet とは正反対の致命的な故障特性を持ちます。

この基盤が高速化に採る各層は、いずれも「失敗しても通常のコールド pull に戻るだけ」の被害が広がらない失敗の仕方に閉じています。ノード起動不能や学習中の死亡につながる常駐データパス（SOCI lazy、ステートフルミラー、snapshot 焼き込み）を恒久基盤から排除したことこそが、この設計の判断そのものです。

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

**5. 高速化の層は必ず被害が広がらない失敗に閉じます**

prewarm、並列化、zstd といった高速化は、全滅しても通常のコールド pull に戻るだけであるべきです。ノード起動不能や学習中死亡につながる仕組みを高速化のために持ち込まないことを、採否判断の第一原則にします。

# ワークショップ実施

本節では、恒久コアを投入する前にまず計測し、次に最小コアを入れ、効果を測ってから条件付き最適化に進む、という順序で進めます。この順序自体が本章の主張です。

以降のコマンドは `terraform output` と `charts/experiments` を相対パスで使うので、`infra/eks` から実行します。Basic01 の 4 行で `AWS_REGION` などを解決したうえで、namespace を置きます。以降の `aws` コマンドは `--region` を明示していないので、`AWS_REGION` が入っていないとリポジトリが見つからず落ちます (`--region` を毎回付けても構いません)。

```bash
cd "$(git rev-parse --show-toplevel)"
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
source infra/scripts/distai-env.sh
cd infra/eks
export NAMESPACE=distai
```

## 1. コールド pull の待ち時間を分解して計測する

最初にやるべきは高速化ではなく計測です。ユーザー体感の待ち時間を「ノード provisioning」「manifest 往復」「レイヤ取得」「展開」に分解して実測し、以降の全ての採否をこの数字で決めます。

計測対象として、Basic02 でビルドした学習イメージを digest 指定で 1 つ起動します。GPU プールを指定して、Karpenter に新規ノードを起こさせるところから測るのが要点です。既にそのプールのノードが立っている場合は、先に消してから始めます (`kubectl delete nodeclaim -l karpenter.sh/nodepool=gpu-ddp` で消すと、Karpenter が EC2 の終了まで処理します。ノードを直接 `delete node` すると NodeClaim が残って課金が続くので使いません)。

```bash
DIGEST=$(aws ecr describe-images \
  --repository-name "$(terraform output -raw ddp_sample_ecr_url | cut -d/ -f2-)" \
  --query 'sort_by(imageDetails[?imageTags],&imagePushedAt)[-1].imageDigest' --output text)

IMAGE="$(terraform output -raw ddp_sample_ecr_url)@${DIGEST}"

kubectl -n "$NAMESPACE" apply -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: coldpull
spec:
  restartPolicy: Never
  nodeSelector:
    node-role: gpu-ddp
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
  containers:
    - name: coldpull
      image: ${IMAGE}
      command: ["sleep", "600"]
YAML

kubectl -n "$NAMESPACE" wait --for=condition=Ready pod/coldpull --timeout=20m
```

`Ready` になったら、pod イベントのタイムスタンプで内訳を見ます。

```bash
kubectl -n "$NAMESPACE" get events --field-selector involvedObject.name=coldpull \
  --sort-by=.lastTimestamp -o wide
```

ここで読めるのは取得と展開の側です。`Pulling` から `Pulled` までがレイヤの取得と展開の合算で、`Pulled` から `Started` までがコンテナの作成と起動です。ノードの起動時間はここには出てきません。スケジューラが Pod をノードに割り当てるのはノードが Ready になったあとなので、Karpenter がノードを起動していた 1〜2 分は Pod の作成時刻から `Scheduled` までの区間に入ります。そこを見るには `kubectl get pod coldpull -o jsonpath='{.metadata.creationTimestamp}'` と `Scheduled` の時刻を突き合わせます。展開と取得の合算が大きな割合を占めているなら zstd や prewarm が有効で、Pod 作成から `Scheduled` までが大きな割合を占めているなら headroom floor が有効、という判断の材料になります。

本書の検証では、Basic02 でビルドした 3.3 GB の学習イメージを digest 指定で起動し、次の数字が出ました。

```text
Scheduled -> Pulling   1s
Pulling   -> Pulled    1m35.9s   (3,337,948,590 bytes = 実効 35 MB/s 程度)
Pulled    -> Started   1s 未満
```

`Pulled` のイベントは `in 1m35.906s (1m35.906s including waiting)` のように取得と展開を合算した値を出します。括弧内の `including waiting` が同じ値であれば、他イメージの pull を待たされていない（並列化が効いている）ことを意味します。この環境では待ち時間ゼロで 1 分半以上かかっており、大きな割合を占めているのは取得側でした。つまりこの基盤で有効なのは zstd よりも prewarm だという判断になります。

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

上の NodeConfig にある `discard_unpacked_layers = false` は、展開後も圧縮済みの blob を残す設定です。将来ノード間でレイヤを配り合う仕組みを載せたときに、そこから配れるようにしておくためのものです。名前から imageGC を弱める設定に見えますが、GC の判断は使用中かどうかで決まるので、この設定はそこに影響しません。ただし無害というわけではなく、展開後も圧縮済みの blob を残す分だけディスクを使います。本章で見るとおり CPU プールは gp3 のルートに imagefs と nodefs が同居するので、容量の見積もりではこの分を足して考えます。

## 2. 恒久コアを投入する

計測で痛みの所在を確認したら、恒久コアの 2 点を入れます。

headroom floor は、低優先度の pause Deployment で cpu プールにノードを常時 1 台維持します。cpu プールを狙い撃ちする `nodeSelector: node-role: cpu`（`karpenter-resources.tf` の `nodepool_cpu` が付与するラベル）と、`karpenter.sh/do-not-disrupt: "true"` アノテーションの両方が必須です。アノテーションが守っている相手は consolidation ではありません。CPU NodePool は `consolidationPolicy: WhenEmpty` なので、pause Pod が載っているノードはそもそも「空」ではなく consolidation の対象外です。守る対象は drift で、ノードの AMI に新しいリリースが出ると Karpenter は稼働中のノードでも置き換えます。これが起きると温めたキャッシュごとノードが入れ替わり、headroom floor の目的が消えます。ここでは常時 1 台維持のコストを許容する前提を置き、作業時間帯だけに絞る CronJob 制御は行いません。

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

上を `kubectl apply -f -` で投入します。PriorityClass はこの後の prewarm が `prewarmPriorityClassName=cache-headroom` で参照するので、prewarm より先に作ります。

```bash
kubectl apply -f - <<'YAML'
（上の YAML をそのまま貼る）
YAML

kubectl -n kube-system rollout status deploy/cache-headroom --timeout=10m
k get nodes -l node-role=cpu
```

`rollout status` が完了し、`node-role=cpu` のノードが 1 台 `Ready` で見えていれば、headroom floor は効いています。ワークロードを何も出していない状態でこのノードが残り続けることが、この仕組みで得られるものです。

この 2 つで得られるものは別です。headroom floor が消すのは**ノード起動の待ち時間**（Karpenter がノードを起動して Ready にするまでの 1〜2 分）で、prewarm が消すのは**イメージ取得の待ち時間**です。同じノードで両方を消したいなら、そのプールに両方を効かせる必要があります。本章の例では headroom を cpu プール、prewarm を GPU プールに置いていますが、これは説明のための分担です。実際にどちらの待ち時間も削りたいプールでは、そのプール名で両方を指定してください。

ここで 1 つ実測から分かった落とし穴があります。`do-not-disrupt` は Karpenter の consolidation を確かに止めます（`DisruptionBlocked ... Pod has "karpenter.sh/do-not-disrupt" annotation` というイベントで確認できます）が、**止めるのは Karpenter だけ**です。PriorityClass を `-10` にした headroom pod は、優先度既定値 0 の普通の pod がノードに入りきらないときスケジューラに preempt されます。実機では検証用の pod を 1 つ投げただけで headroom pod が追い出され、別ノードに再スケジュールされて、温めたノードが空になり consolidation で消えました。

つまり `do-not-disrupt` は「Karpenter が能動的にノードを畳むこと」への対策であって、キャッシュの永続性を保証しません。低優先度である以上、混雑時に押し出されるのは設計どおりの挙動です（そのために `-10` にしています）。押し出した相手は、まさにノードを待っていたワークロードなので、これは失敗ではなく overprovisioning が機能した形です。headroom floor は「空いているときに 1 台起きた状態を保つ」仕組みだと理解し、キャッシュそのものは次の prewarm で担保します。

ここで優先度を上げて preempt を防ごうとしてはいけません。優先度を 0 以上にすれば確かに preempt されなくなりますが、代わりにノードを待っていたワークロードが Karpenter の起動を 1〜2 分待つことになり、消したかった待ち時間をワークロード側に押し付けるだけです。また `preemptionPolicy: Never` も対策になりません。これは「その Pod が他を preempt するか」の設定であって、preempt される側の耐性は一切変わりません。

なお `do-not-disrupt` が止めるのは Karpenter が自発的に行う置き換え、つまり consolidation と drift です。ディスク逼迫による kubelet の eviction、Spot の中断、手動の削除はいずれも止まりません。満了 (`expireAfter`) も Karpenter v1 では[強制](https://karpenter.sh/docs/concepts/disruption/#forceful-disruption)なので止まらない側ですが、headroom を置く CPU プールは `expireAfter = "Never"` なので満了そのものが起きません (アクセラレータプールはプールごとに指定できます)。

prewarm DaemonSet は、温めたいイメージを列挙して各ノードで pull させる仕組みです。ここでの設計判断は、**pull を `ctr` ではなく kubelet にやらせる**ことです。温めたいイメージを、何もせず残り続けるだけのコンテナとして並べると、kubelet が通常のワークロードとまったく同じ経路で pull します。なぜ initContainer ではないのかは、この節の後半で扱います。

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

kubelet に pull させる形なら、これらは 1 つも不要です。ECR 認証はノードロールの権限がそのまま使われ、ServiceAccount も Pod Identity も IAM ロールも `containerd.sock` も専用イメージも不要です。実機でも、ServiceAccount を作らずに自アカウント ECR の digest pull が通ることを確認しています。本章の第一原則「高速化のためにノードを危険にしない」を満たすのはこちらだけなので、恒久コアに置けるのは kubelet 経由の実装だと判断しました。

ただしこの方式は「pull するだけ」ではなく、列挙したイメージのシェルを全ノードで実行します。イメージの既定 USER は多くの場合 root なので、`containerd.sock` を断った理屈と整合させるなら、ここも権限を落としておく必要があります。`sleep` を実行するだけなら何の権限も要らないので、上の例のように capability を全部落として root filesystem を読み取り専用にします。

温めたイメージを「何もしない initContainer」で pull させる書き方も見かけますが、これは採れません。initContainer は終了するので、そのイメージは kubelet から見て使用中ではなくなり、imageGC の回収候補に戻ります。しかも DaemonSet の Pod は Running のままなので、**キャッシュが消えた後も「温まっている」という顔で立ち続けます**。温めた側が気づけない壊れ方であり、`rollout status` が成功したことは何も保証しません。ノード寿命が長い Capacity Block のプールで最も静かに壊れます。

常駐コンテナにすれば、イメージは本物の「使用中」になり、どの GC 経路からも回収されません。代償として、ディスク逼迫時にノードはこのイメージを回収できず、まず prewarm Pod を追い出すことになります。本章のように `prewarmPriorityClassName=cache-headroom` を渡していれば prewarm が最も低い優先度になるので、これは正しい順序で、追い出された時点でイメージは回収可能に戻ります。この値を省くとチャートは `priorityClassName` を出さないため優先度は既定 (0) になり、この順序は成り立ちません。

この形が成立するのに必要なノード側の設定は、手順 1 で入れた `maxParallelImagePulls = 8` と `serializeImagePulls: false` だけです。これがあると、新規ノードで prewarm の pull と本命ワークロードの pull が同時に走り、ワークロードが prewarm の後ろに並びません。逆にこの設定が失われた場合の壊れ方は「遅くなる」であって、キャッシュが消えるわけではありません。

参照実装では Helm chart 側のテンプレートとして持っています。イメージの一覧はワークロードのライフサイクルに属する（vLLM のバージョンを上げれば温める対象も変わる）ので、`terraform apply` ではなく `helm` の値で更新できる場所に置いています。

温める対象はタグではなく digest で固定します。`imageTags` で絞るのは、失敗ビルドの残骸や中間イメージのような untagged な digest を拾わないためです。単に最後の push を取ると、それらや他人の push を選んでしまうことがあります。

```bash
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

`prewarm.<プール名>` のキーは、Karpenter がノードに付ける `node-role` ラベルの値です。通常は `accelerator_pools` のキーですが、`cpu` を指定して CPU プールを温めることもできます。チャートはこのキーが Kubernetes のリソース名として使える形 (小文字英数とハイフン) であることを検査します。もっとも `accelerator_pools` のキーは Terraform 側でも NodePool と EC2NodeClass の名前になるので、この制約は Helm だけの話ではありません。プール名は最初から小文字英数とハイフンで付けてください。プールごとに DaemonSet が分かれるので、どのプールに何を温めるかは自分で書き分けます。デバイスの種類が合っているかはチャートは見ないので、GPU プールに Neuron のイメージを並べれば、そのまま GPU ノードで pull されます。タグではなく digest で指定するのは、タグが可変だとノード上のキャッシュとレジストリの中身がずれても気づけないためです。

イメージの列挙は必要なものだけにしてください。1 エントリごとにそのプールの全ノードで pull とディスクを消費し、Capacity Block のプールでは予約が課金されている間にそれが走ります。ただし CB プールを除外すべきという話ではありません。CB では予約開始直後、ワークロードが着地する前に温めるので、どのみち課金されている時間を使うことになり、この仕組みの価値が最も高い場所です。

複数のアーキテクチャ向けに配布されているイメージでは、**マニフェストリストの digest** を指定してください。アーキ別の子 digest を指定するとノード側のプラットフォーム解決を飛ばすので、アーキが合わないノードでは `exec format error` になります。自 ECR に単一アーキで焼いたイメージを、同じアーキのプールだけで温める場合はその digest で問題ありません。チャートが検査するのは `@sha256:` の形をしているかどうかだけで、どちらの digest かは判別しません。

Capacity Block のプールを温める場合は toleration を 1 つ足す必要があります。prewarm DaemonSet が既定で許容するのは `nvidia.com/gpu` と `aws.amazon.com/neuron` の 2 つで、CB のノードはこれに加えて予約ごとの `capacity-reservation` taint を持ちます。足さないと DaemonSet の DESIRED が 0 のままになり、まさに温める価値が最も高いノードだけが黙って外れます。`--set 'prewarm.<プール名>.tolerations[0].key=capacity-reservation' --set 'prewarm.<プール名>.tolerations[0].operator=Exists'` のように渡します。確認は `kubectl -n "$NAMESPACE" get ds` で DESIRED が 1 以上になっていることです。

対象イメージには `/bin/sh` と `sleep` が必要で、既定 USER のまま権限を落とした状態でそれらが動く必要があります（本書で扱う CUDA / vLLM / Neuron 系のイメージはいずれも該当します）。distroless や scratch のイメージはシェルを持たないのでこの方法では温められません。正確には、pull そのものは成功するのでイメージはノード上に置かれます。しかしその後のコンテナ作成が `/bin/sh` 不在で失敗するため、イメージを「使用中」として保持するコンテナが無く、本章が扱ってきた枠組みのとおり imageGC の回収候補に残り続けます。DaemonSet も恒久的に不健全になります。

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

3 行目は「prewarm が新規ノードで走っている最中にワークロードが着地する」ケースで、Karpenter がノードを起動する経路では普通に起きます。prewarm が帯域を取ってワークロードを遅くするのではないか、という懸念がありますが、同じ digest を指している限りそうはなりません。kubelet は同一イメージの pull をまとめるので、両者は 1 回の pull を共有します。実測でも prewarm 側とワークロード側が同じ 1m9.3s を報告しており、単独のコールド pull より遅くなっていません。

これは prewarm とワークロードで**同じ digest を指していること**が前提です。digest がずれていると 2 本の別々の pull になり、そのときに `maxParallelImagePulls` が効いてワークロードが prewarm の後ろに並ばずに済みます。digest をずらさないこと自体がキャッシュ戦略の要点でもあるので、prewarm の digest はワークロードが参照するものと同じ値を渡してください。

この結果は取得が大きな割合を占めていたこの環境の数字です。手順 1 の計測で展開が支配的だと出た場合に限り、次の zstd 化に進んでください。

zstd 化は BuildKit の出力で行います。Basic02 のクラスタ内ビルドがそのまま使えるので、`imageBuild.zstd=true` を足すだけ (ただしビルド Job が clone する ref は既定で `main` です。本書のタグと同じソースから焼きたい場合は `--set imageBuild.gitRef=release/eks-distributed-ai/v0.2.0` も渡します)です。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
ECR_URL=$(terraform output -raw ddp_sample_ecr_url)

kubectl delete job build-ddp-sample-v2-zstd -n image-builder --ignore-not-found

helm template exp charts/experiments -n "$NAMESPACE" \
    --set imageBuild.enabled=true \
    --set imageBuild.repository="$ECR_URL" \
    --set imageBuild.tag=v2-zstd \
    --set imageBuild.zstd=true \
    -s templates/image-build-ddp-sample.yaml \
    | kubectl apply -f -

kubectl -n image-builder wait --for=condition=complete \
    job/build-ddp-sample-v2-zstd --timeout=30m
```

これは BuildKit の出力指定に `compression=zstd,oci-mediatypes=true` を足したものです。`oci-mediatypes` は省略できません。zstd のレイヤは OCI のメディアタイプでしか表現できず、Docker v2 のマニフェストでは記述できないためです。

ここで `force-compression=true` を付けてはいけません。外部のベースイメージまで再圧縮してしまい、ベースレイヤの digest が変わって共有とキャッシュヒットの両方を壊します。付けなければ、そのビルドが新しく積むレイヤだけが zstd になり、ベースは元のまま残ります。この混在した形が、圧縮の利点と共有の利点を同時に保つ唯一の形です。参照実装は `force-compression` を設定できないようにしてあります。

混在になっていることは、push 後のマニフェストで確かめられます。

```bash
aws ecr batch-get-image --repository-name "$(terraform output -raw ddp_sample_ecr_url | cut -d/ -f2-)" \
  --image-ids imageTag=v2-zstd --query 'images[0].imageManifest' --output text \
  | python3 -c "
import json, sys
for i, l in enumerate(json.load(sys.stdin)['layers']):
    print(f\"layer{i}: {l['mediaType']}  {l['size']:,} bytes\")
"
```

実機出力（gzip のままのベースと、zstd になった自ビルド層が混在している）:

```text
layer0: application/vnd.oci.image.layer.v1.tar+gzip  30,439,933 bytes
layer1: application/vnd.oci.image.layer.v1.tar+gzip  7,215,230 bytes
layer2: application/vnd.oci.image.layer.v1.tar+gzip  3,300,282,353 bytes
layer3: application/vnd.oci.image.layer.v1.tar+gzip  32 bytes
layer4: application/vnd.oci.image.layer.v1.tar+gzip  99 bytes
layer5: application/vnd.oci.image.layer.v1.tar+zstd  16 bytes
layer6: application/vnd.oci.image.layer.v1.tar+zstd  3,892 bytes
layer7: application/vnd.oci.image.layer.v1.tar+zstd  16 bytes
```

3.3 GB を占める layer2 は `tar+gzip` のままで、サイズも zstd 化しなかったビルドとバイト単位で一致しています。つまりベースレイヤの digest は変わっておらず、同じベースを使う他のイメージとの共有もノード上のキャッシュヒットも保たれています。マニフェスト全体のメディアタイプも `application/vnd.oci.image.manifest.v1+json` に変わっており、`oci-mediatypes` が効いていることが確認できます。

この例のように自ビルド層が数 KB しかない場合、zstd の効果はほぼありません。zstd が意味を持つのは、自分で積むレイヤが大きく、かつ手順 1 の計測で展開が支配的だと出たときだけです。

外部イメージをミラーする場合は `crane copy` で無変換のままコピーします。ミラーでの再圧縮は digest が変わって上流の署名と provenance を無効化するため禁止です。

## 4. 被害が広がらない失敗を検証する

恒久コアと最適化を入れたら、高速化の層を意図的に止めても pull が通ることを確認します。DaemonSet に `replicas` フィールドは存在しないため `kubectl scale` は使えません。代わりに、実在しないラベルを狙う `nodeSelector` を一時的に注入して Pod をどのノードにも乗せない状態にし、prewarm DaemonSet を止めた状態で新規ノードを起動し、pod が通常のコールド pull で正常に起動することを見ます。

```bash
kubectl -n "$NAMESPACE" patch daemonset image-prewarm-gpu-ddp \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"prewarm-disabled":"true"}}}}}'
kubectl -n "$NAMESPACE" get ds image-prewarm-gpu-ddp
```

`DESIRED` が 0 になり Pod が消えたことを確認します。そのうえで新規ノードを誘発します。手順 1 と同じやり方で、対象プールのノードを消してから `coldpull` Pod を作り直せば、prewarm の居ないノードで pull が走ります。prewarm を経由しない Pod が `Running` になることを確認します。検証が終わったら `nodeSelector` のパッチを外して元に戻します。

```bash
kubectl -n "$NAMESPACE" patch daemonset image-prewarm-gpu-ddp \
  --type json -p '[{"op":"remove","path":"/spec/template/spec/nodeSelector/prewarm-disabled"}]'
```

`kubectl scale` を試すと `Error from server (NotFound): the server could not find the requested resource` になります。DaemonSet に scale サブリソースが無いためで、パッチを使う理由がこれです。

高速化の層が全滅しても通常のコールド pull に戻るだけである、という被害が広がらない失敗の性質を実地で確認できれば、この層を安心して恒久基盤に組み込めます。

## 5. 後片付けをする

恒久基盤として置き続けるならこのままで構いませんが、試しただけならこの章で作ったものを消します。**特に headroom floor は消し忘れるとクラスタの破棄が止まります。** `do-not-disrupt` を付けた Pod は Karpenter が退去させないので、そのノードが空にならず、Basic11 の `terraform destroy` が NodeClaim の待ちで停滞します。しかも headroom は `kube-system` に置くので、Basic11 の片付けスクリプトが対象にする namespace の外にいて、掃除されません。実際にこれで destroy が 18 分止まり、手で消して初めて先に進みました。

```bash
kubectl -n "$NAMESPACE" delete daemonset -l app.kubernetes.io/name=image-prewarm --ignore-not-found
kubectl -n kube-system delete deployment cache-headroom --ignore-not-found
kubectl delete priorityclass cache-headroom --ignore-not-found
kubectl get nodes -l node-role=cpu
```

最後の確認で cpu ノードが `consolidateAfter` の経過後に消えていれば、headroom がノードを確保していない状態に戻っています。手順 1 で作った `coldpull` Pod も残っていれば消してください。

# まとめ

本章では、変化し続けるイメージを扱うキャッシュ層を、この分散 AI 基盤に恒久的に組み込む設計を示しました。キャッシュの寿命はノードの寿命に等しいこと、digest pin が stale 故障を消すこと、そして高速化の層は必ず「失敗しても通常のコールド pull に戻るだけ」の失敗しても被害が広がらない形にするべきこと、という原則を軸に据えました。まず計測し、headroom floor と単純な prewarm DaemonSet という地味な恒久コアを入れ、効果を測ってから zstd などの条件付き最適化に進む、という順序自体が本章の主張です。SOCI の lazy pull や Spegel を恒久コアに入れなかったのは、それらがノード起動不能や学習中死亡という受け入れ難い故障を持ち込むからであり、恒久基盤に要るのは派手さではなく退屈な堅牢さです。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter Disruption（consolidation / drift / expiration）](https://karpenter.sh/docs/concepts/disruption/)
- [Amazon ECR pull through cache](https://docs.aws.amazon.com/AmazonECR/latest/userguide/pull-through-cache.html)
- [Mountpoint for Amazon S3 CSI driver](https://github.com/awslabs/mountpoint-s3-csi-driver)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
