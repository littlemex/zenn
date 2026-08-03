---
title: "Basic02 - 分散学習を体験する"
free: true
---

本章では、GPU を一切使わずに、Amazon EKS の CPU ノード上で PyTorch の分散学習（DDP）を動かします。まず 1 ノードの中で複数プロセスを協調させる `torchrun` から始め、続いて複数ノードにまたがる分散学習を Kubeflow Trainer v2 の TrainJob で動かします。高額な GPU/Capacity Block に進む前に、「複数プロセスが協調して 1 つのモデルを学習する」という分散学習の最小の成功体験を、GPU に比べればごくわずかなコストで得ることが目的です。GPU の手順も最後にオプションとして載せています。

:::message
本章は GPU も追加のインフラ手順も不要です。
:::

# 解説

## 全体構成

この book 全体で構築する分散 AI 基盤のうち、本章は最小の入口にあたります。GPU/Neuron ノードは使わず、Karpenter が要求に応じて立てる CPU ノード（`node-role=cpu` の NodePool）の上で分散学習を動かします。前章からの追加のリソースはありません。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

## DDP

分散学習の基本的な形が DDP（Distributed Data Parallel）です。DDP については参考情報がたくさんあるので調べてみてください。DDP の通信バックエンドにはいくつか種類がありますが、本章で押さえておきたいのは次の 2 つです。

- **gloo**: CPU 上で動きます。GPU は不要です。
- **nccl**: NVIDIA GPU 上で動き、GPU 間の高速な集合通信を担います。

本章では gloo backend を使い、CPU ノードの上で DDP を動かします。学習対象は、分散学習の教材として広く使われる MNIST を分類する小さな MLP です。モデルもデータも小さいので、GPU なしの CPU でも 1 周が数分で終わり、学習内容そのものより「複数プロセスが勾配を共有して 1 つのモデルを学習する」という DDP の挙動に集中できます。

本章は 2 段構えで進めます。同じ学習スクリプト [`ddp.py`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/manifests/ddp-sample/ddp.py) を使い回し、起動方法だけを差し替えます。

### 前半: 単一ノード（torchrun、オペレータ不要）

`torchrun --standalone --nproc_per_node=2` とすると、1 ノード内に 2 つのプロセス（rank 0, rank 1）を立て、それぞれに `RANK` / `WORLD_SIZE` / `LOCAL_RANK` などの環境変数を自動で設定してくれます。追加のコンポーネントは何も要らず、Kubernetes の素の `batch/v1` Job として実行できます。

### 後半: 複数ノード（TrainJob）

分散学習を複数ノードに広げると、「どのノードの誰が rank 0 で、情報連携の集合点（rendezvous）はどこか」を各ノードに教える仕組みが要ります。Kubernetes 上でこれを宣言的に扱う標準が、Kubeflow Trainer v2 が提供する TrainJob（`trainer.kubeflow.org/v1alpha1`）です。ノード数を宣言すれば、Trainer が内部で JobSet を展開して各ノードの Pod を並べ、集合点の情報を各 Pod に注入してくれます。

使うワークロードは Helm チャート [`charts/experiments`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments) です。前半が単一ノードの `torchrunTrain`、後半が複数ノードの `trainjobTrain` で、どちらも CPU と GPU の両対応です。適用は `helm template ... | kubectl apply -f -` で行い、`helm install` は使いません（このチャートは release 管理をせず、レンダリングして手で適用する実験カタログという位置づけです）。

## なぜ Kubeflow Trainer v2 なのか

複数ノードの PyTorch 学習を Kubernetes で動かす方法として、MPIJob（MPI Operator）に torchrun を載せる構成もあります。MPIJob 自体は Open MPI 以外に Intel MPI や MPICH も扱える汎用的なものです。ただし Launcher Pod が各 Worker へ SSH でログインして起動コマンドを撒く前提です。そのため PyTorch の DDP に流用すると、コンテナに sshd を仕込み SSH 鍵を配るという、PyTorch 本来は要らない足回りを抱え込みます。

Kubeflow の学習ジョブは世代ごとに改善がなされており v1 と v2 があり過渡期です。Kubeflow Training Operator v1 が提供する PyTorchJob（`kubeflow.org/v1`）で、[awslabs/awsome-distributed-ai の DDP サンプル](https://github.com/awslabs/awsome-distributed-ai/tree/main/3.test_cases/pytorch/ddp/kubernetes) もこれを使っています。ただし v1 は upstream でレガシー扱いになり（`release-1.9` ブランチで当面メンテされますが、公式は後継への移行を推奨）、本 book では後継の Kubeflow Trainer v2 の TrainJob（`trainer.kubeflow.org/v1alpha1`）を主線に採用してみました。

:::message
Kubeflow Trainer v2 の API はまだ `v1alpha1`（アルファ）です。将来のバージョンでフィールド名が変わる可能性があります。
:::

### v1（PyTorchJob）と v2（TrainJob）は何が違うのか

要点だけを対比します。細部は違っても、学習スクリプト `ddp.py` を `torchrun` で起動するという中身は v1 でも v2 でも同じです。

| 観点 | v1: PyTorchJob（レガシー） | v2: TrainJob（本 book の主線） |
|---|---|---|
| API | `kubeflow.org/v1` | `trainer.kubeflow.org/v1alpha1` |
| ジョブの型 | フレームワークごとに別 CRD（PyTorchJob / TFJob / MPIJob …） | 単一の TrainJob と Runtime の組（PyTorch も他フレームワークも同じ型） |
| 集合点（rendezvous） | 基本は operator が配線。elastic 構成では方式（etcd/c10d など）を利用者が選ぶ | Trainer が自動配線するため、利用者側の選択・設定が不要 |
| 実行の土台 | operator 単体 | JobSet の上に構築 |
| 成熟度 | 安定（ただしレガシー） | アルファ（API 変更あり得る） |
| 変わらないもの | `ddp.py` と `torchrun` の実行モデル | 同左 |

v2 の要点は「利用者は TrainJob で台数と中身だけを書き、集合点の配線は Trainer に任せる」ことです。具体的には、Trainer の torch プラグインが各 Pod に `torchrun`（TorchElastic）が読む `PET_*` 環境変数（`PET_NNODES` / `PET_NPROC_PER_NODE` / `PET_NODE_RANK` / `PET_MASTER_ADDR` / `PET_MASTER_PORT`）を注入します。`PET_NODE_RANK` は Pod のインデックスから固定で決まるため、`node-0-0` が常に node rank 0（= rank 0）になります。`PET_MASTER_ADDR` は先頭ノードの Pod（JobSet が払い出す `<ジョブ名>-node-0-0` の headless DNS）を指し、そこが集合点になります。`torchrun` はこれらを引数の既定値として読み（先頭ノード上の TCPStore を使う既定の rendezvous で動きます。参加順で rank が変わる動的方式ではありません）、各学習プロセスに `RANK` / `WORLD_SIZE` / `LOCAL_RANK` / `MASTER_ADDR` / `MASTER_PORT` を再エクスポートします。`ddp.py` はその値を、引数なしの `init_process_group()` による env:// rendezvous で読み取ります。

TrainJob 側は台数（`numNodes`）とノードあたりのプロセス数（`numProcPerNode`）、イメージ、起動コマンドだけを指定します。集合点の配線や 1 ノード 1 Pod の配置といった土台側の設定は、本 book がクラスタに用意した Runtime（[`torch-distributed-eks`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/clustertrainingruntime-eks.yaml)）が持っています。この Runtime を導入する Trainer v2 本体は Terraform の [`trainer.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/trainer.tf) が入れます。

## 学習結果の保存先と共有ストレージ

本章の 2 つのワークロードは、MNIST のデータと学習スナップショットを共有ストレージに保存します。既定の保存先は単一 AZ の **Amazon FSx for OpenZFS** です。ストレージの詳細は後続の章で扱いますが、`openzfs_enabled` が既定で有効なため、`terraform apply` の時点でファイルシステムと静的 PersistentVolume（`openzfs-shared`）はすでに作られています。

この PV を掴む PersistentVolumeClaim（`shared-claim`）は、Helm チャートは作りません。読者が `kubectl apply` で 1 回だけ作り、その名前を `--set sharedStorage.existingClaimName=shared-claim` で各ワークロードに渡します。なぜチャートに作らせないのかは、この後の「共有 PVC を用意する」ステップと、章末の「共有 PVC を消してみる」ステップで実際に手を動かしながら説明します。要点だけ先に言うと、静的 PV は同時に 1 つの PVC としか結びつきません。PVC の生成をワークロードのレンダリングに乗せると、PVC の寿命がその都度の `apply`/`delete` に引きずられてしまい、PV 側の寿命（Terraform が管理する、基盤が続く限り存在するもの）とズレてしまいます。PVC の作成を「基盤を用意する」タイミングに切り離し、以降は何度ワークロードを消して作り直しても同じ PVC を使い続けられるようにするのが、ここで一度だけ手動で作る理由です。

後半の複数ノード TrainJob でも、スナップショットの保存は rank 0 が担当します。そのため、どのノードから書かれても同じ場所に成果物が集まる共有ストレージ（ReadWriteMany）が要ります。単一ノードの torchrun でも同じ共有ストレージを使い、入口から同じ `/shared` 規約に揃えておくと 2 段目への流れが素直になります。共有ファイルシステム上での同時書き込みによる破損を避けるため、`ddp.py` はスナップショットの書き込みを rank 0 だけが行います。MNIST のダウンロードは全 rank が実行します（既にファイルが揃っていれば torchvision 側が再取得をスキップします）。

保存先は Helm の `sharedStorage.backend` で切り替えられます。既定の `openzfs` のほかに、`fsx`（FSx for Lustre）、リージョン規模のマルチ AZ 共有が要るときは `efs` を選べます。3 つのバックエンドはいずれも Terraform 側で静的 PV が用意される設計で、選んだバックエンドの `var.<x>_enabled` が有効になっている必要があります。既定で `openzfs` と `fsx` は有効、`efs` は無効（ドライバのみ常設）です。

## 学習用イメージをクラスタ内でビルドする

学習ワークロードには `ddp.py` を焼き込んだコンテナイメージが要ります。素直なやり方は手元の `docker build` で作って ECR に push することですが、これは「手元に Docker があり、しかも EKS ノードと同じ x86_64 向けにクロスビルドできる」という前提を各利用者に強いてしまいます。この基盤では、その前提を持ち込まずにイメージのビルドもクラスタ内で完結させます。

ビルドには [BuildKit](https://github.com/moby/buildkit) の rootless モードを使います。BuildKit は Docker デーモンや特権コンテナを必要とせず、通常の Pod の中で Dockerfile を解釈してイメージをビルドし、レジストリに push できるツールです。rootless イメージ（`moby/buildkit:rootless`）は、ビルド全体をユーザー namespace の中で非 root（uid 1000）として走らせるため、`CAP_SYS_ADMIN` などの特権を一切要求しません。本章ではこの BuildKit を、`buildctl-daemonless.sh` で daemon を常駐させずに 1 回限りの Kubernetes Job として起動します。特別なオペレータや常設のビルドサーバーは要らず、学習 Job と同じ「レンダリングして `kubectl apply` する」操作モデルにそのまま乗ります。

ビルドの土台は Basic01 の `terraform apply` の時点で用意されています（`image_builder_enabled` が既定で有効）。具体的には、ビルド先の Amazon ECR リポジトリ・ECR への push 権限を与える IAM ロール・その紐付けを担う Pod Identity・ビルド専用の namespace（`image-builder`）と ServiceAccount です。ここでも Basic01 と同じ設計原則が効いています。すなわち Terraform は「機構」だけを恒久管理し、実際のビルド Job という「実行」はワークショップ側でカタログから適用します。

認証の流れがクラスタ内ビルドの肝です。ECR への push には ECR のログイントークンが要りますが、この基盤では **Pod Identity** がそれを透過的に解決します。`image-builder` の ServiceAccount には Pod Identity Association で IAM ロールが結び付いており、この SA で動く Pod には認証情報を取得するためのエンドポイント情報が自動で注入されます。BuildKit の公式イメージは Amazon ECR 用の認証ヘルパーを同梱しません。そこでこのビルド Job は initContainer を 1 つ挟みます。initContainer は Pod Identity の認証情報で `aws ecr get-login-password` を実行してログイントークンを取り、それを Docker の `config.json` として emptyDir に書き出します。BuildKit コンテナは `DOCKER_CONFIG` でそのディレクトリを読み、push 時の認証に使います。結果として、`docker login` を手で打つことも認証情報ファイルをリポジトリに置くこともなく push まで通ります。Pod Identity の認証情報は initContainer にも注入されることを実機で確認しています。

ソースの取得も BuildKit に任せます。この book のリポジトリは公開されているので、BuildKit の Git コンテキスト機能（`context=https://<repo>#<ブランチ>:<サブディレクトリ>`）で clone からビルドまでを完結でき、ソースを取得するための事前の `git clone` は要りません。先ほどの initContainer は ECR 認証トークンを用意するためだけのもので、ソース取得には関与しません。

イメージのサイズには注意が要ります。BuildKit はベースイメージの展開とレイヤのスナップショットをノードのローカルディスク上で行うため、ビルド中に一時的に大きなディスクを消費します。この消費はノードの ephemeral-storage としてカウントされるので、ビルド Job には `ephemeral-storage` の requests/limits を設定して同居 Pod が eviction されるのを防いでいます。BuildKit の作業ディレクトリは Pod の emptyDir に載せており、これも同じ ephemeral-storage 予算に含まれます。ピーク時のディスク使用量は、展開後の非圧縮ファイルシステムと各レイヤのスナップショットの合計で、イメージの内容に依存しますが目安として push 後サイズの 4〜5 倍程度です。本章の `ddp-sample` は push 後で約 3GB で、実測でも既定の 30Gi 予算・CPU ノードの既定ルートディスク（`cpu_node_volume_size` の既定 150Gi）に十分収まりました。数十 GB 級の重いイメージを扱う場合は、ビルド専用の大容量ノードプールを opt-in で用意する仕組みも入れてあります。

この作業領域はノードのローカルディスクを使い、FSx や NFS のような共有ファイルシステムには移しません。ネットワークストレージ上でビルドすると、拡張属性（file capabilities）の非対応やタイムスタンプ精度の違いによるレイヤ差分検出の不整合から、壊れたイメージが生成される恐れがあります。ビルドの一時領域は常にローカルディスクを使うのが正解です。

## 実行時の注意点

**CPU ノードは Karpenter の consolidation で消えることがあります。** 本章の CPU ノードは Karpenter の CPU NodePool が起動するもので、`consolidationPolicy: WhenEmptyOrUnderutilized` はアイドルなノードを早めに回収します。学習 Pod が単独で載っているノードが「余剰」と判断されて学習中に evict される事故を避けるため、Pod には `karpenter.sh/do-not-disrupt: "true"` アノテーションを付けます。

# ワークショップ実施

## 1. 作業用 namespace を用意する

以降のコマンドは Basic01 で clone したリポジトリのルート（`infra/eks` の親）で実行する前提です。`kubectl` が Basic01 のクラスタを指していること、MNIST データセットを取得するためのアウトバウンド通信やノードの ECR pull 権限は、いずれも Basic01 の構築で用意済みです。

まず、Basic01 で作った作業用 namespace を使います。ターミナルを開き直した場合に備えて、ここで冪等に用意し直しておきます（すでに存在していてもエラーになりません）。

```bash
export NAMESPACE=distai
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
```

## 2. 学習用イメージを用意する

2 つのワークロードは、MNIST MLP を DDP で学習する `ddp.py` を焼き込んだ専用イメージ `ddp-sample` を共用します。`ddp.py` は [awslabs/awsome-distributed-ai の DDP サンプル](https://github.com/awslabs/awsome-distributed-ai/tree/main/3.test_cases/pytorch/ddp) をベースに、保存先を共有 PVC へ寄せて adapt したものです。Dockerfile はリポジトリの [`infra/eks/manifests/ddp-sample/`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/manifests/ddp-sample) に置いてあります。

このイメージのビルドは、上述した BuildKit で実施します。ビルド先の ECR URL は Terraform の出力から取得できます。イメージタグはワークショップ用に `v1` を使います（再ビルドするときは `v2` のようにタグを進めると、`latest` のキャッシュ問題を避けられます）。

```bash
cd infra/eks
ECR_URL=$(terraform output -raw ddp_sample_ecr_url)
IMAGE=${ECR_URL}:v1

# 同名のビルド Job が残っていると apply が "unchanged" でスキップされる（Job の spec は変更不可）。
# 同じタグで作り直すときは先に削除する。初回は存在しなくても --ignore-not-found で安全。
# （別解: タグを v2 に上げると Job 名も変わるので削除不要。latest 固定のキャッシュ事故も避けられる。）
kubectl delete job build-ddp-sample-v1 -n image-builder --ignore-not-found

# クラスタ内で BuildKit ビルド Job を起動
helm template exp charts/experiments -n "$NAMESPACE" \
    --set imageBuild.enabled=true \
    --set imageBuild.repository="$ECR_URL" \
    --set imageBuild.tag=v1 \
    -s templates/image-build-ddp-sample.yaml \
    | kubectl apply -f -

# ビルド完了を待つ（初回は CPU ノード起動とベースイメージ pull で 10 分ほどかかります）
kubectl -n image-builder wait --for=condition=complete \
    job/build-ddp-sample-v1 --timeout=30m
```

なお、この Job を `kubectl apply` すると `Warning: would violate PodSecurity "baseline" ... seccompProfile` という警告が表示されます。これは rootless BuildKit が `seccompProfile: Unconfined` を必要とするための意図した設計上の警告で、ビルドは正常に進みます。

進捗やエラーはビルド Job のログで確認できます。既定では BuildKit 本体（ビルドと push）のログが出ます。ECR 認証を用意する initContainer が失敗した場合は `-c ecr-login` でそちらのログを見ます。

```bash
kubectl -n image-builder logs -f job/build-ddp-sample-v1              # BuildKit 本体
kubectl -n image-builder logs job/build-ddp-sample-v1 -c ecr-login    # 認証 init が失敗したとき
```
::::details 補足
:::message
数十 GB 級の重いイメージは、前述のとおりピークディスクが push 後サイズの 4〜5 倍になり共有 CPU プールの 150Gi ルートに収まりません。その場合は `terraform apply` 時に `image_builder_dedicated_pool = true` を設定すると、NVMe インスタンスストアを束ねた大容量ローカルディスクのビルド専用ノードプール（taint で隔離、ビルドが終われば自動で 0 台に戻る）が用意されます。Helm 側では `--set imageBuild.dedicatedPool.enabled=true --set imageBuild.ephemeralStorage=150Gi` のように指定します。
:::

:::message
rootless BuildKit は非特権（`CAP_SYS_ADMIN` 不要、uid 1000）で動きますが、内部の rootlesskit が使う `clone`/`unshare` 系のシステムコールが `RuntimeDefault` の seccomp プロファイルでブロックされるため、ビルドコンテナは `seccompProfile: Unconfined` を指定する必要があります。これは Pod Security Admission の `baseline`/`restricted` に抵触するので、`image-builder` namespace だけは PSA の enforce を緩め（`warn`/`audit` は `baseline` のまま可視化）、単発のビルド Job 専用に隔離しています。この設定は `terraform apply`（`image_builder_enabled`）が行うので、利用者側の追加操作は不要です。
:::
::::

続いて、後半で使う Kubeflow Trainer v2 が入っていることを確認します。Basic01 の `terraform apply`（`trainer_enabled` が既定で有効）で導入済みのはずなので、TrainJob の CRD が見えることと、コントロールプレーン（`kubeflow-system` の manager と JobSet）が動いていることを確かめておきます。

```bash
kubectl get crd trainjobs.trainer.kubeflow.org
kubectl get pods -n kubeflow-system
```

## 3. 共有 PVC を用意する

本章の 2 つのワークロード（`torchrunTrain`/`trainjobTrain`）は共有ストレージへの書き込みが要りますが、そのための PVC はチャートが作りません。ここで 1 回だけ、自分で `kubectl apply` して作ります。

```bash
# infra/eks にいる前提です
POOL_PV=$(terraform output -json shared_storage | jq -r '.fsx_openzfs.persistent_volume')
sed "s/__VOLUME_NAME__/${POOL_PV}/" manifests/shared-pvc.yaml | kubectl apply -n "$NAMESPACE" -f -
kubectl get pvc shared-claim -n "$NAMESPACE"
```

`STATUS` が `Bound` になれば準備完了です。このマニフェスト（`manifests/shared-pvc.yaml`）は名前空間ごとに 1 回だけ適用するもので、以降の各ステップで `--set sharedStorage.existingClaimName=shared-claim` としてこの PVC の名前を渡します。

:::message
なぜチャートが PVC を自動生成しないのか、実際に手を動かして確かめてみましょう。ワークロードの Job/TrainJob を作り直すたびに PVC も一緒に作り直す設計だったらどうなるか、というのを本章の最後の「共有 PVC を消してみる」ステップで体験します。先に結論だけ言うと、PV は PVC を「名前」ではなく「オブジェクトの実体（UID）」で覚えるため、同じ名前で PVC を作り直しても新しい実体とみなされ、二度と bind できなくなります（`Released` という状態で止まります）。PVC の生成をワークロードの `apply`/`delete` から切り離し、基盤を用意するこのステップで 1 回だけ作ることで、この事故を避けています。
:::

## 4. 単一ノードで torchrun を動かす（前半）

まず 1 ノードの中で 2 プロセスの DDP を動かします。この章の Job は `parallelism` を指定しない素の `batch/v1` Job なので **Pod は 1 つだけ**で、その 1 コンテナの中で `torchrun --standalone --nproc_per_node=2` 自身が 2 つの学習プロセス（rank 0, rank 1）を fork します。複数の Pod が立つわけではありません（後半の TrainJob は Pod ごとに 1 rank で、そちらは Pod が複数に分かれます。今の段階では違いだけ覚えておいてください。詳しくは次の step 5 で説明します）。`gpu.enabled` を付けないのでこのまま CPU（gloo）で動きます。`nprocPerNode=2` は 1 コンテナ内に fork する rank 数です。前のステップでビルドしたイメージの URI を `torchrunTrain.image` に、直前のステップで作った PVC の名前を `sharedStorage.existingClaimName` に渡します。

`ddp.py` の既定は 3 エポックで、MNIST + MLP という小さい組み合わせでは 1 分足らずで完走してしまい、ログを `follow` しても途中経過を眺める前に終わります。そこで最初から `torchrunTrain.totalEpochs=100` を渡して、学習が進む様子を実際に見られる長さにしておきます。

```bash
# step 2 から続けて infra/eks にいる前提です。ターミナルを変えた場合は cd infra/eks した上で
# 変数を再取得します: ECR_URL=$(terraform output -raw ddp_sample_ecr_url); IMAGE=${ECR_URL}:v1

# 同名の Job が残っていると apply が "unchanged" で無言でスキップされる（Job の spec は変更不可）。
# 作り直すときは必ず先に削除する。初回は存在しなくても --ignore-not-found で安全。
kubectl delete job ddp-torchrun -n "$NAMESPACE" --ignore-not-found

helm template exp charts/experiments -n "$NAMESPACE" \
    --set torchrunTrain.enabled=true \
    --set torchrunTrain.image="$IMAGE" \
    --set torchrunTrain.backend=gloo \
    --set torchrunTrain.nodeRole=cpu \
    --set torchrunTrain.nprocPerNode=2 \
    --set torchrunTrain.totalEpochs=100 \
    --set sharedStorage.existingClaimName=shared-claim \
    | kubectl apply -f -
```

:::message
`sharedStorage.existingClaimName` を渡し忘れると、`apply` は次のエラーでレンダリング自体に失敗します（Pod が Pending になるのではなく、その場で分かります）。この章で `torchrunTrain`/`trainjobTrain` を有効にするすべての `helm template` 呼び出しにこの `--set` が必要です。

```text
Error: execution error at (experiments/templates/torchrun-train.yaml:101:65):
sharedStorage.existingClaimName is required — apply manifests/shared-pvc.yaml once
(per namespace) and pass its name here.
```
:::

:::message
同名の `batch/v1` Job（`ddp-torchrun`）がすでに存在すると、`kubectl apply` は `job.batch/ddp-torchrun unchanged` と表示するだけで新しい定義を適用しません（Job の Pod テンプレートは作成後に変更できないためです）。前回の実行が残ったまま気づかず、古い完了済み Job を「動いた」と誤認しやすいので、投入の前に必ず削除します。
:::

Pod が `Running` になるまでログは出ません。初回は CPU ノードの起動とイメージ pull で数分かかるので、`kubectl wait` で Pod が Ready になるのを待ってから、続けてログを追います。

```bash
kubectl wait --for=condition=ready pod -l job-name=ddp-torchrun -n "$NAMESPACE" --timeout=10m
kubectl logs -f -l job-name=ddp-torchrun -n "$NAMESPACE"
```

:::message alert
`kubectl logs -f`（`-f` は follow）は、走行中の Pod にストリーム接続してログを追うコマンドです。Job が完了した後に `-f` を付けて実行すると、追従先の走行中 Pod が無いため `error: timed out waiting for the condition` になります（ビルドや学習の失敗ではありません）。さらに Job の Pod は完了後に短時間で回収されるため、`-f` を外しても `pods ... not found` でログが取れないことがあります。ログはあくまで Pod が `Running` の間に追うものと考え、上記のように `kubectl wait` で Ready を待ってすぐ follow するのが確実です。完了後に成功を確かめる方法は後述します。
:::

2 つの rank は同じ 1 コンテナのプロセスなので、標準出力も 1 つのログストリームに合流し、両者の行が入り混じって流れます。実機では次のように出ました（`totalEpochs=100` を渡しているので `starting training: 100 epochs` になります。loss の値は初期重みの乱数に依存するため実行ごとに変わります）。

```text
[rank 1/2] backend=gloo cuda_available=False device_count=0
[rank 0/2] backend=gloo cuda_available=False device_count=0
[rank 1/2] downloading MNIST to /shared/mnist-data
[rank 0/2] downloading MNIST to /shared/mnist-data
[rank 1/2] starting training: 100 epochs, batch_size 32
[rank 0/2] starting training: 100 epochs, batch_size 32
[rank 0/2] mlflow disabled
[rank 1/2] mlflow disabled
[rank 1/2] epoch 0 | steps 938 | loss 0.1981
[rank 0/2] epoch 0 | steps 938 | loss 0.1993
[rank 0/2] epoch 0 | snapshot saved to /shared/output/torchrun-gloo/snapshot.pt
```

MNIST のダウンロードは全 rank が実行し、初回のこの step で共有ストレージ上の `/shared/mnist-data` に落ちます（`download=True` は冪等で、既にファイルが揃っていれば torchvision 側が再取得をスキップします。rank 0 だけが取得して他 rank を barrier で待たせる形にすると、取得が collective の初期化タイムアウトを超えた場合にデッドロックするため、こちらを選んでいます）。各 rank は `DistributedSampler` によってデータセットの異なる部分を担当し、勾配を all-reduce で共有しながら同じモデルを更新します。エポックが進むと loss が減少します。`ddp.py` は各エポックの終わりに（`SAVE_EVERY` の既定は 1）rank 0 だけがスナップショットを共有ストレージ上の `/shared/output/torchrun-gloo/snapshot.pt` へ上書き保存します。上の出力で `snapshot saved` の行が rank 0 からしか出ていないのがそれです。「保存は rank 0 のみが行う」というのは DDP の定石で、全 rank が同じモデルを持っているため保存は 1 つで足ります（共有ファイルシステムでは全 rank が同じファイルへ同時書き込みすると破損しかねない、という実務上の理由もあります）。

100 エポックの完走を待つ必要はありません。`epoch N | loss ...` の行がいくつか流れて学習が進んでいることを確認できたら、`Ctrl-C` でログの追従を止めて次に進みます。

ここで、学習を完走前に止めてみます。`kubectl delete job` は実行中の Pod も一緒に削除するので、100 エポックの途中でも学習は即座に終わります。

```bash
kubectl delete job ddp-torchrun -n "$NAMESPACE"
```

止めた時点までの学習成果が共有ストレージに残っていることを確認します。同じ PVC をマウントした使い捨ての Pod から覗きます。

```bash
kubectl run peek --rm -it --restart=Never --image=busybox:1.36 -n "$NAMESPACE" \
  --overrides='{"spec":{"containers":[{"name":"peek","image":"busybox:1.36","command":["ls","-lh","/shared/output/torchrun-gloo"],"volumeMounts":[{"name":"s","mountPath":"/shared"}]}],"volumes":[{"name":"s","persistentVolumeClaim":{"claimName":"shared-claim"}}]}}'
```

`snapshot.pt` が表示されれば、途中まで進んだモデルが保存されています。実機では次のように出ました。

```text
total 3M
-rw-r--r--    1 root     root        2.6M Aug  3 12:01 snapshot.pt
```

スナップショットは各エポックの終わりに保存されるので、削除した時点で最後に完了したエポックまでの状態が残ります（進行中だったエポックの分は保存されないので、失われるのはそのエポックだけです）。同じコマンドで Job を再投入すると `resuming from snapshot at epoch <N>` と出て、消したところから学習が続きます。この resume の仕組みは手で止めたときのためだけでなく、ノードの入れ替えや Karpenter の consolidation で Pod が意図せず落ちた場合の耐性にもなっています。

ジョブが最後まで走り切るのを待って完了状態を確認する流れは、次の TrainJob で試します（そちらはエポック数を既定のままにするので数十秒で終わります）。

## 5. 複数ノードで TrainJob を動かす（後半）

次に、同じ学習を 2 ノードにまたがる TrainJob で動かします。前半との最大の違いは Pod の数です。前半は 1 Pod（1 コンテナ）の中で `torchrun` が 2 プロセスを fork していましたが、ここからは **rank ごとに別々の Pod、別々のノード**に分かれます。rank 0 と rank 1 は同じコンテナのプロセスではなく、ネットワーク越しに通信する別々の Pod です。

`numNodes=2` がノード数、`nprocPerNode=1` が各ノード内のプロセス数です（Helm の `nprocPerNode` は TrainJob の `numProcPerNode` に対応します）。本 book がクラスタに用意した Runtime（[`torch-distributed-eks`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/clustertrainingruntime-eks.yaml)）に `topologyKey: kubernetes.io/hostname` の podAntiAffinity が入っているので、2 つの Pod は必ず別ノードに分かれて配置されます。PVC は前半と同じ `shared-claim` を使い回します。

```bash
# torchrun の Job と同じ理由で、同名の TrainJob が残っていると apply がスキップされる。
# 作り直すときは先に削除する（初回は存在しなくても --ignore-not-found で安全）。
kubectl delete trainjob ddp-trainjob -n "$NAMESPACE" --ignore-not-found

helm template exp charts/experiments -n "$NAMESPACE" \
    --set trainjobTrain.enabled=true \
    --set trainjobTrain.image="$IMAGE" \
    --set trainjobTrain.nodeRole=cpu \
    --set trainjobTrain.numNodes=2 \
    --set trainjobTrain.nprocPerNode=1 \
    --set sharedStorage.existingClaimName=shared-claim \
    | kubectl apply -f -
```

TrainJob が展開する Pod は JobSet の規則で名付けられ、`<ジョブ名>-node-0-<index>-<ランダム>` になります。本章のジョブ名は `ddp-trainjob` なので、rank 0 の Pod は `ddp-trainjob-node-0-0-xxxxx`、rank 1 は `ddp-trainjob-node-0-1-xxxxx` という形です（末尾のランダムな 5 文字は実行のたびに変わるので、Pod 名を決め打ちせずラベルで選ぶのが確実です）。node index と rank は一致し、`node-0-0` が常に rank 0 です。まず 2 つの Pod がそれぞれ別ノードに載っていることを確認します（`-o wide` の `NODE` 列が 2 つとも違えば OK です）。ジョブ名のラベルで全ノードの Pod をまとめて選べます。

```bash
kubectl get pods -n "$NAMESPACE" -o wide -l jobset.sigs.k8s.io/jobset-name=ddp-trainjob
```

次に、TrainJob の状態を確認します。`-w` を付けると状態が変わるたびに更新されるので、`Complete` になったら `Ctrl-C` で抜けます。

```bash
kubectl get trainjob ddp-trainjob -n "$NAMESPACE" -w
```

rank 0 が載る Pod のログを追います。Pod 名は末尾のランダム文字が変わるので、決め打ちせずラベルで選びます。rank 0 は JobSet の completion index 0 なので、`batch.kubernetes.io/job-completion-index=0` と jobset 名の 2 つのラベルで一意に選べます。前半と同じく、ログは Pod が `Running` の間に追います（完了後は Pod が回収されて `logs -f` が `timed out` になります。前半の step 4 の注意書きを参照してください）。

```bash
SEL="jobset.sigs.k8s.io/jobset-name=ddp-trainjob,batch.kubernetes.io/job-completion-index=0"
kubectl wait --for=condition=ready pod -l "$SEL" -n "$NAMESPACE" --timeout=15m
kubectl logs -f -l "$SEL" -n "$NAMESPACE"
```

前半の単一ノード（1 Pod 内の fork）とはログの出方がここで初めて変わります。`node-0-0` と `node-0-1` は別々の Pod・別々のノードで動く独立したプロセスなので、`kubectl logs` も Pod ごとに別々に取る必要があります（前半のように 1 つの `logs` 呼び出しで両 rank が混在することはありません）。`node-0-0` では rank 0 が gloo backend で起動します。スナップショットの保存は rank 0 が担当するため、その行は `node-0-0` 側にのみ現れます。

`downloading MNIST to /shared/mnist-data` の行は、step 4 で同じ `/shared` に取得済みでも毎回出力され、しかも rank 0 と rank 1 の両方に出ます。`ddp.py` が全 rank から無条件に `download=True` を渡す実装になっているためです。torchvision 側は既にファイルが揃っていれば実際の再取得をスキップするので、2 回目以降のこの行は「確認しただけ」を意味します。

```
[rank 0/2] backend=gloo cuda_available=False device_count=0
[rank 0/2] downloading MNIST to /shared/mnist-data
[rank 0/2] starting training: 3 epochs, batch_size 32
[rank 0/2] epoch 0 | steps 938 | loss 0.5312
[rank 0/2] epoch 0 | snapshot saved to /shared/output/trainjob/snapshot.pt
[rank 0/2] epoch 1 | steps 938 | loss 0.2287
[rank 0/2] epoch 1 | snapshot saved to /shared/output/trainjob/snapshot.pt
[rank 0/2] epoch 2 | steps 938 | loss 0.1614
[rank 0/2] epoch 2 | snapshot saved to /shared/output/trainjob/snapshot.pt
[rank 0/2] done
```

もう一方の rank 1（completion index 1）のログも見てみます。

```bash
kubectl logs -l "jobset.sigs.k8s.io/jobset-name=ddp-trainjob,batch.kubernetes.io/job-completion-index=1" -n "$NAMESPACE"
```

`node-0-1` では rank 1 が同じく gloo backend で起動し、各エポックの loss を出して最後に `done` で終わります。ダウンロードの行は rank 1 側にも出ますが、スナップショット保存の行は rank 0 側にしか出ません。

```
[rank 1/2] backend=gloo cuda_available=False device_count=0
[rank 1/2] downloading MNIST to /shared/mnist-data
[rank 1/2] starting training: 3 epochs, batch_size 32
[rank 1/2] epoch 0 | steps 938 | loss 0.5289
[rank 1/2] epoch 1 | steps 938 | loss 0.2301
[rank 1/2] epoch 2 | steps 938 | loss 0.1627
[rank 1/2] done
```

`WORLD_SIZE=2` の 2 プロセスが別々のノードで起動し、両 rank の loss がエポックを追って単調に下がっていることから、2 つのノードが勾配を all-reduce しながら 1 つのモデルを学習できていることが分かります（各 rank はデータセットの異なる分割を担当するので、loss は完全に同一ではなく近い値で推移します）。最後に TrainJob が `Complete` になり、rank 0 がスナップショットを共有ストレージ上の `/shared/output/trainjob/snapshot.pt` に保存します。

ログを追い損ねても、TrainJob が `Complete` になったことと、共有ストレージ上のスナップショットで完了を確認できます。`kubectl wait` の `--for=condition=Complete` が完了を待つ確実な方法です。

```bash
kubectl wait --for=condition=Complete trainjob/ddp-trainjob -n "$NAMESPACE" --timeout=30m
kubectl run peek --rm -it --restart=Never --image=busybox:1.36 -n "$NAMESPACE" \
  --overrides='{"spec":{"containers":[{"name":"peek","image":"busybox:1.36","command":["ls","-lh","/shared/output/trainjob"],"volumeMounts":[{"name":"s","mountPath":"/shared"}]}],"volumes":[{"name":"s","persistentVolumeClaim":{"claimName":"shared-claim"}}]}}'
```

`wait` が返り、`snapshot.pt` があれば、2 ノードの分散学習は完走しています。確認できたら削除します。

```bash
kubectl delete trainjob ddp-trainjob -n "$NAMESPACE"
```

## 6. 共有 PVC を消してみる

最後に、step 3 で触れた「なぜチャートが PVC を自動生成しないのか」を実際に確かめます。ワークロード（Job/TrainJob）を消すのと同じ感覚で、共有 PVC 自体を消してみましょう。

```bash
kubectl delete pvc shared-claim -n "$NAMESPACE"
kubectl get pv openzfs-shared
```

`STATUS` が `Released` になっているはずです。`Available`（誰にも bound されていない、次の PVC を待てる状態）ではなく `Released`（前の持ち主の後始末を待っている状態）である点に注目してください。この状態で、もう一度 `shared-claim` を作り直してみます。

```bash
POOL_PV=$(terraform output -json shared_storage | jq -r '.fsx_openzfs.persistent_volume')
sed "s/__VOLUME_NAME__/${POOL_PV}/" manifests/shared-pvc.yaml | kubectl apply -n "$NAMESPACE" -f -
kubectl get pvc shared-claim -n "$NAMESPACE"
```

`STATUS` はいつまでも `Pending` のままで、`Bound` になりません。名前を `shared-claim` のまま揃えても、PV 側は新しい PVC を古い PVC と同一視してくれないのです。

:::message alert
PV は PVC を「名前」ではなく、bind が成立した瞬間に書き込まれる PVC の **UID**（オブジェクトとしての実体）で覚えています。PVC を削除して同じ名前で作り直すと、Kubernetes 内部では UID が異なる別オブジェクトになるため、PV の `claimRef` はどの新しい PVC にも一致しません。しかも Kubernetes には `Released → Available` の自動遷移がなく、これは仕様です。データが残っている可能性のあるボリュームを、審査なしに次の PVC へ渡さないための安全機構です。もしこの PV の `reclaimPolicy` が `Delete` だったら、`kubectl delete pvc` の瞬間に基盤側のボリュームまで削除されて学習成果物が消えていたはずです。`Retain`（このマニフェストの既定）はその代わりに「宙に浮いた」状態で止まり、人間の判断を待ちます。
:::

復旧するには、PV の `claimRef` のうち `uid` と `resourceVersion` だけを取り除きます。`claimRef` 全体を消すのではないことに注意してください。`name`/`namespace` を残すことで、PV は「その名前の PVC が来たら bind する」という pre-bind 状態に戻り、無関係な別の PVC に横取りされるレースを防げます。

```bash
kubectl patch pv openzfs-shared --type json \
  -p '[{"op":"remove","path":"/spec/claimRef/uid"},{"op":"remove","path":"/spec/claimRef/resourceVersion"}]'
kubectl get pvc shared-claim -n "$NAMESPACE"
```

数秒待つと `shared-claim` が `Bound` に変わります。他の静的 PV（`fsx-training`、`efs-neuron-workspace`）も同じ `Retain` なので、それらを使っている場合も同じ症状・同じ復旧手順になります。

これで step 3 で 1 回だけ手動作成した理由が実感できたはずです。PVC の生成をワークロードの `apply`/`delete` に乗せていたら、ワークロードを作り直すたびにこの `Released` を踏むことになります。基盤を用意するタイミングで 1 回だけ作り、以降のワークロードはその PVC の**名前を渡すだけ**にする、というこの章の設計はこの事故を避けるためのものです。

# まとめ

本章では、GPU を使わずに Amazon EKS の CPU ノード上で MNIST MLP の DDP 学習を 2 段構えで動かしました。前半はオペレータ不要の `torchrun`（`batch/v1` Job）で 1 ノード内の 2 プロセスを走らせ、後半は Kubeflow Trainer v2 の TrainJob で 2 ノードにまたがる分散学習を走らせました。いずれも gloo backend で動かし、loss が減少すること、そして rank 0 のみがスナップショットを共有ストレージに保存するという DDP の基本動作を確認しました。さらに、共有ストレージの PVC を意図的に削除して `Released` 状態を再現し、`claimRef` の `uid`/`resourceVersion` だけを取り除く復旧手順まで体験しました。静的プロビジョニングされた PV は PVC を名前ではなく実体（UID）で覚えるという、この基盤の共有ストレージ全体を貫く重要な性質です。

# 参考資料

- [PyTorch DistributedDataParallel](https://pytorch.org/docs/stable/notes/ddp.html)
- [torchrun (Elastic Launch)](https://pytorch.org/docs/stable/elastic/run.html)
- [Kubeflow Trainer v2 (TrainJob)](https://trainer.kubeflow.org/en/latest/)
- [Kubeflow Training Operator v1 (PyTorchJob、レガシー)](https://trainer.kubeflow.org/en/latest/legacy-v1/user-guides/pytorch.html)
- [awslabs/awsome-distributed-ai の DDP テストケース (Kubernetes)](https://github.com/awslabs/awsome-distributed-ai/tree/main/3.test_cases/pytorch/ddp/kubernetes)
- [対象ワークロード torchrunTrain（charts/experiments）](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/torchrun-train.yaml)
- [対象ワークロード trainjobTrain（charts/experiments）](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/trainjob-train.yaml)
