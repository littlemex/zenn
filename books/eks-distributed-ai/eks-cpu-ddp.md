---
title: "Basic02 - CPU で分散学習を体験する (torchrun と PyTorchJob)"
free: true
---

本章では、GPU を一切使わずに、Amazon EKS の CPU ノード上で PyTorch の分散学習（DDP）を動かします。まず 1 ノードの中で複数プロセスを協調させる `torchrun` から始め、続いて複数ノードにまたがる分散学習を Kubeflow Training Operator の PyTorchJob で動かします。高額な GPU/Capacity Block に進む前に、「複数プロセスが協調して 1 つのモデルを学習する」という分散学習の最小の成功体験を、GPU に比べればごくわずかなコストで得ることが目的です。

:::message
本章は GPU も追加のインフラ手順も不要です。Basic01 の `terraform apply` の時点で、Karpenter コントローラ・CPU 用の NodePool（`node-role=cpu`、`cpu_nodepool_enabled` が既定で有効）・共有ストレージ Amazon FSx for OpenZFS（`openzfs_enabled` が既定で有効）・Kubeflow Training Operator（PyTorchJob 用、`training_operator_enabled` が既定で有効）がすべて揃っています。本章の学習 Pod は `node-role=cpu` を要求するので、Karpenter がその CPU NodePool に CPU ノードを 1〜2 台オンデマンドで立ち上げて実行します（Karpenter そのものの解説は Basic03 で行いますが、動かすのに前倒しの作業は要りません）。まずここで「分散学習が EKS 上で動く」ことを自分の手で確認しておくと、以降の章の GPU 版が理解しやすくなります。
:::

# 解説

## 全体構成

この book 全体で構築する分散 AI 基盤のうち、本章は最小の入口にあたります。GPU/Neuron ノードは使わず、Karpenter が要求に応じて立てる CPU ノード（`node-role=cpu` の NodePool）の上で分散学習を動かします。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

図の下段、Amazon EKS コントロールプレーンと CPU ノードだけを使う構成です。アクセラレータプールや EFA には触れません。ここで DDP の仕組みと、単一ノード（torchrun）から複数ノード（PyTorchJob）へ広げる流れを CPU で体験しておくと、後続の章の nccl backend やマルチノード通信（Basic05 の EFA、Basic09 の Neuron 2 ノード DDP など）が「gloo を GPU + EFA に置き換えたもの」として素直に理解できます。

## DDP と本章の 2 段構成

分散学習の最も基本的な形が DDP（DistributedDataParallel）です。DDP では、同じモデルの複製を複数のプロセス（rank）が持ち、各 rank が異なるデータのミニバッチで勾配を計算し、その勾配を全 rank で平均（all-reduce）してからモデルを更新します。これにより、実質的なバッチサイズを rank 数倍に増やして学習を高速化します。

DDP の通信バックエンドにはいくつか種類がありますが、本章で押さえておきたいのは次の 2 つです。

- **gloo**: CPU 上で動きます。GPU は不要です。
- **nccl**: NVIDIA GPU 上で動き、GPU 間の高速な集合通信を担います（後続の章で EFA と組み合わせます）。

本章では gloo backend を使い、CPU ノードの上で DDP を動かします。学習対象は、分散学習の教材として広く使われる MNIST（手書き数字画像）を分類する小さな MLP（多層パーセプトロン）です。モデルもデータも小さいので、GPU なしの CPU でも 1 周が数分で終わり、学習内容そのものより「複数プロセスが勾配を共有して 1 つのモデルを学習する」という DDP の挙動に集中できます。

本章は 2 段構えで進めます。同じ学習スクリプト `ddp.py` を使い回し、起動方法だけを差し替えます。

### 前半: 単一ノード（torchrun、オペレータ不要）

`torchrun --standalone --nproc_per_node=2` とすると、1 ノード内に 2 つのプロセス（rank 0, rank 1）を立て、それぞれに `RANK` / `WORLD_SIZE` / `LOCAL_RANK` などの環境変数を自動で設定してくれます。追加のコンポーネントは何も要らず、Kubernetes の素の `batch/v1` Job として実行できます。これが「オペレータ無しで何ができるか」の最小形です。

### 後半: 複数ノード（PyTorchJob）

分散学習を複数ノードに広げると、「どのノードの誰が rank 0 で、集合点（rendezvous）はどこか」を各ノードに教える仕組みが要ります。Kubernetes 上でこれを宣言的に扱う標準が、Kubeflow Training Operator が提供する PyTorchJob（`kubeflow.org/v1`）です。Worker の台数を宣言すれば operator が各 Worker Pod を並べてくれます。ただし後述のとおり、Worker だけの構成では集合点の配線に `spec.elasticPolicy` の指定が要ります。

使うワークロードは Helm チャート [`charts/experiments`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments) の 2 つです。前半が単一ノードの `torchrunTrain`、後半が複数ノードの `pytorchjobTrain` で、どちらも CPU と GPU の両対応です。適用は `helm template ... | kubectl apply -f -` で行い、`helm install` は使いません（このチャートは release 管理をせず、レンダリングして手で適用する実験カタログという位置づけです）。

なお、values の `backend`（`gloo` / `nccl`）は出力ディレクトリ名（`/shared/output/*-<backend>`）を分けるための**ラベルに過ぎません**。実際にどの通信バックエンドで動くかは、GPU が見えるかどうか（`gpu.enabled=true` と Pod の `nvidia.com/gpu` リクエスト）で `ddp.py` が自動判定します。つまり CPU で動かすか GPU で動かすかを決めるのは `gpu.enabled` であって、`backend` の文字列ではありません。この点は後半の GPU 節でもう一度触れます。

## なぜ MPIJob ではなく PyTorchJob なのか

複数ノードの PyTorch 学習を Kubernetes で動かす方法として、かつては MPIJob（MPI Operator）に torchrun を載せる構成もよく使われました。MPIJob 自体は Open MPI 以外に Intel MPI や MPICH も扱える汎用的なものです。ただし Launcher Pod が各 Worker へ SSH でログインして起動コマンドを撒く前提です。そのため PyTorch の DDP に流用すると、コンテナに sshd を仕込み SSH 鍵を配るという、PyTorch 本来は要らない足回りを抱え込みます。

一方 PyTorchJob は PyTorch の分散学習そのもののために作られた operator です。SSH も Open MPI も不要で、torchrun の集合点は operator が配線します。[awslabs/awsome-distributed-ai の DDP サンプル](https://github.com/awslabs/awsome-distributed-ai/tree/main/3.test_cases/pytorch/ddp/kubernetes) も、この PyTorchJob を使っています。本 book でも、独自の足回りを持たない PyTorchJob に一本化しています。この AWS サンプルと同じく、本 book でも集合点の管理に etcd を使います（後述）。etcd は Worker のライフサイクルから独立した集合点ストアで、任意の Worker が再起動しても残りが再 rendezvous できます。固定サイズの学習なら c10d（Worker-0 自身が TCP ストアを持つ方式）でも十分ですが、リファレンスとの対応関係を保つためと elastic 化への布石として etcd を採用しています。

PyTorchJob を使ううえで 1 つだけ知っておきたい癖があります。少し込み入るので 3 つに分けて説明します。

**1. env が注入される条件について**: PyTorchJob は Master と Worker の 2 種類のレプリカを持てますが、Training Operator が `MASTER_ADDR` / `RANK` / `WORLD_SIZE` を Pod の環境変数として注入するのは、Master レプリカを定義したときだけです。本 book のように Worker だけで構成すると、これらは注入されません。

**2. etcd rendezvous による集合点の配線**: そこで `spec.elasticPolicy` を付けると、operator が torchrun の集合点情報（`PET_RDZV_BACKEND=etcd` と `PET_RDZV_ENDPOINT=etcd:2379`）を各 Worker に注入します。`PET_*` は torchrun（TorchElastic）が読む環境変数の接頭辞です。etcd は Helm チャートが同じ namespace に立てる軽量な KVS で、学習 Pod とは独立に rendezvous 状態を保持します。torchrun はこの情報を読んで etcd 経由で各ノードの rank を動的に割り当て、そのうえで `RANK` / `WORLD_SIZE` / `LOCAL_RANK` / `MASTER_ADDR` / `MASTER_PORT` を各学習プロセスへ再エクスポートします。`ddp.py` はこの再エクスポートされた値を、引数なしの `init_process_group()` による env:// rendezvous で読み取ります。集合点の在り処は env（`PET_RDZV_*`）経由で受け取りますが、ノード数は command に静的に書かれた `--nnodes`（= `workers`）で決まります。

**3. `elasticPolicy` という名前について**: 名前に反して、本章ではオートスケールが目的ではありません。`minReplicas == maxReplicas == workers` で台数を固定し、etcd への集合点配線と Worker restart tolerance（`maxRestarts`）のために使います。

## 学習結果の保存先と共有ストレージ

本章の 2 つのワークロードは、MNIST のデータと学習スナップショットを共有ストレージに保存します。既定の保存先は **Amazon FSx for OpenZFS**（単一 AZ の NFS 共有）です。ストレージの詳細は Basic10 で扱いますが、`openzfs_enabled` が既定で有効なため、Basic01 の `terraform apply` の時点でファイルシステムと静的 PersistentVolume（`openzfs-shared`）はすでに作られています。本章では、Basic01 で作成済みの PVC（`openzfs-claim`）をそのまま再利用し、コンテナの `/shared` にマウントして使います（Helm の `sharedStorage.existingClaimName` で指定します）。

後半の複数ノード PyTorchJob では、スナップショットの保存を担う rank 0 がどの Worker になるかが etcd rendezvous で動的に決まります。そのため、どのノードから書かれても同じ場所に成果物が集まる共有ストレージ（ReadWriteMany）が要ります。単一ノードの torchrun でも同じ共有ストレージを使い、入口から同じ `/shared` 規約に揃えておくと 2 段目への流れが素直になります。共有ファイルシステム上での同時書き込みによる破損を避けるため、`ddp.py` は MNIST のダウンロードを rank 0 だけが行い（他 rank は barrier で待って同じ実体を読む）、スナップショットの書き込みも rank 0 に限定しています。

保存先は Helm の `sharedStorage.backend` で切り替えられます。既定の `openzfs`（FSx for OpenZFS、汎用の共有ホーム）のほかに、高スループットのスクラッチ領域が要るときは `fsx`（FSx for Lustre）、リージョン規模のマルチ AZ 共有が要るときは `efs`（Amazon EFS）を選べます。3 つのバックエンドはいずれも Terraform 側で静的 PV が用意される設計で、選んだバックエンドの `var.<x>_enabled` が有効になっている必要があります。既定で `openzfs` と `fsx` は有効、`efs` は無効（ドライバのみ常設）です。

:::message
既定が単一 AZ の FSx なのは、EFA・FSx for Lustre・Capacity Block を前提とする学習ワークロードでは計算が 1 つの AZ に寄るため、ストレージだけをマルチ AZ にしても可用性が活きず単価だけが上がるからです。マルチ AZ 配置が定石になる推論サービングや、AZ をまたいでキャッシュを共有したい用途のためには EFS を選択肢として残しています。この設計判断の詳細は Basic01 の「基盤層が恒久管理するもの、しないもの」で、ストレージ自体の詳細は Basic10 で扱います。なお本章の CPU NodePool は軽量なため AZ 固定しておらず、CPU ノードが OpenZFS とは別の AZ に立つと NFS アクセスが AZ をまたぐことがありますが、MNIST 規模では体感できる差はなく、ここでは単純さを優先しています（性能を要求するアクセラレータプールは単一 AZ に固定します）。
:::

なお静的 PV は 1 つの PVC としか結び付けられません。namespace やチャートを消して作り直すと、古い PVC への参照（claimRef）が PV 側に残って `Released` のまま再バインドできず Pod が Pending で止まることがあります。その場合の復旧やストレージの詳細は Basic10 で扱います。

## 学習用イメージをクラスタ内でビルドする

学習ワークロードには `ddp.py` を焼き込んだコンテナイメージが要ります。素直なやり方は手元の `docker build` で作って ECR に push することですが、これは「手元に Docker があり、しかも EKS ノードと同じ x86_64 向けにクロスビルドできる」という前提を各利用者に強いてしまいます。Apple Silicon の Mac ではプラットフォーム指定を忘れて arm64 イメージを作り、ノード上で `exec format error` になる、という事故も定番です。この基盤では、その前提を持ち込まずにイメージのビルドもクラスタ内で完結させます。

ビルドには [Kaniko](https://github.com/GoogleContainerTools/kaniko) を使います。Kaniko は Docker デーモンや特権コンテナを必要とせず、通常の Pod の中で Dockerfile を解釈してイメージをビルドし、レジストリに push できるツールです。Docker デーモンが要らないのは、Kaniko がベースイメージを自分のコンテナのファイルシステムに展開し、Dockerfile の各命令をユーザー空間で実行したうえで、命令ごとにファイルシステムのスナップショットを取って差分をレイヤ化するためです。本章ではこの Kaniko を 1 回限りの Kubernetes Job として起動します。特別なオペレータや常設のビルドサーバーは要らず、学習 Job と同じ「レンダリングして `kubectl apply` する」操作モデルにそのまま乗ります。

:::message
Kaniko 本家（`GoogleContainerTools/kaniko`）はメンテナンスが終了し、リポジトリはアーカイブされています。この基盤ではイメージをバージョン（ダイジェスト）で固定して動作を検証しているため引き続き利用できますが、Kaniko はビルドツールとして差し替え可能な部品と位置づけています。将来的には BuildKit（rootless モード）や buildah、Chainguard によるフォークなどへ移行する選択肢があり、その場合も Job としての起動方法や以降の手順は変わりません。
:::

ビルドの土台は Basic01 の `terraform apply` の時点で用意されています（`image_builder_enabled` が既定で有効）。具体的には、ビルド先の Amazon ECR リポジトリ・Kaniko に ECR への push 権限を与える IAM ロール・その紐付けを担う Pod Identity・ビルド専用の namespace（`image-builder`）と ServiceAccount です。ここでも Basic01 と同じ設計原則が効いています。すなわち Terraform は「機構」だけを恒久管理し、実際のビルド Job という「実行」はワークショップ側でカタログから適用します。これは Kubeflow Training Operator は Terraform が入れるが学習 Job 自体は作らない、という切り分けと同じ構図です。

認証の流れが Kaniko とクラスタ内ビルドの肝です。ECR への push には ECR のログイントークンが要りますが、この基盤では **Pod Identity** がそれを透過的に解決します。`image-builder` の ServiceAccount には Pod Identity Association で IAM ロールが結び付いており、この SA で動く Pod には認証情報を取得するためのエンドポイント情報が自動で注入されます。Kaniko の公式イメージには Amazon ECR 用の認証ヘルパー（`amazon-ecr-credential-helper`）が同梱されていて、push 先が ECR の URI であればこのヘルパーが呼ばれます。ヘルパーはまず Pod Identity 経由で AWS の一時認証情報を取得し、その認証情報で `ecr:GetAuthorizationToken` を呼んで ECR のログイントークンに交換し、レジストリに push します。結果として、`docker login` も認証情報ファイルの受け渡しも一切書かずに、push まで通ります。この一時認証情報のトークンファイル方式に対応するには比較的新しいバージョンのヘルパーが要るため、この基盤では検証済みの Kaniko イメージをダイジェストで固定しています。

ソースの取得も Kaniko に任せます。この book のリポジトリは公開されているので、Kaniko の Git コンテキスト機能で clone からビルドまでを 1 コンテナで完結でき、ソースを取得するための init コンテナや事前の `git clone` は要りません。

イメージのサイズには注意が要ります。Kaniko はベースイメージをノードのローカルディスクに展開してビルドするため、ビルド中に一時的に大きなディスクを消費します。この消費はノードの ephemeral-storage としてカウントされるので、ビルド Job には `ephemeral-storage` の requests/limits を設定して同居 Pod が eviction されるのを防いでいます（テンプレートに含まれています）。ピーク時のディスク使用量は、展開後の非圧縮ファイルシステムとスナップショットの中間 tar と push 用の圧縮レイヤの合計で、イメージの内容に依存しますが目安として push 後サイズの 4〜5 倍程度です。本章の `ddp-sample` は push 後で約 3GB なので、CPU ノードの既定のルートディスク（50GiB）に十分収まります。数十 GB 級の重いイメージを扱う場合は、ビルド専用の大容量ノードプールを opt-in で用意する仕組みも入れてあります（詳細はワークショップ手順の該当箇所で触れます）。

この展開先はノードのローカルディスクに固定されており、FSx や NFS のような共有ファイルシステムには移せません。Kaniko はベースイメージをコンテナのルートファイルシステム直下に展開しますが、この `/` はコンテナランタイムがノードローカルのストレージに用意するもので差し替えられないためです。仮にネットワークストレージ上でビルドできたとしても、拡張属性（file capabilities）の非対応やタイムスタンプ精度の違いによるスナップショット差分検出の不整合から、壊れたイメージが生成される恐れがあります。ビルドの一時領域は常にローカルディスクを使うのが正解です。

## 実行時の注意点

**CPU ノードは Karpenter の consolidation で消えることがあります。** 本章の CPU ノードは Karpenter の CPU NodePool が起動するもので（Karpenter そのものは Basic03 で解説します）、`consolidationPolicy: WhenEmptyOrUnderutilized` はアイドルなノードを早めに回収します。学習 Pod が単独で載っているノードが「余剰」と判断されて学習中に evict される事故を避けるため、Pod には `karpenter.sh/do-not-disrupt: "true"` アノテーションを付けます（テンプレートに含まれています）。

**CPU で動かします。** 本章は性能を測るのではなく「動く」ことを確認する章です。MNIST MLP は非常に軽いので、CPU でも数分で数エポックが終わります。GPU に載せれば当然さらに速くなりますが、この規模のモデルでは差を実感するほどではありません。GPU での高速化を体感するのは、もっと大きなモデルを扱う後続の章になります。

# ワークショップ実施

## 1. 作業用 namespace を用意する

以降のコマンドは Basic01 で clone したリポジトリのルート（`infra/eks` の親）で実行する前提です。`kubectl` が Basic01 のクラスタを指していること、MNIST データセットを取得するためのアウトバウンド通信やノードの ECR pull 権限は、いずれも Basic01 の構築で用意済みです。

まず、Basic01 で作った作業用 namespace を使います。ターミナルを開き直した場合に備えて、ここで冪等に用意し直しておきます（すでに存在していてもエラーになりません）。

```bash
export NAMESPACE=distai
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
```

:::message
本章のワークショップ全体を一発で実行するスクリプトが [`infra/eks/scripts/run-basic02.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/scripts/run-basic02.sh) に用意されています。ECR リポ作成・イメージ build/push・torchrun・PyTorchJob のすべてを自動で行い、完了後にクリーンアップします。以下の手順を個別に実行する代わりに、`./scripts/run-basic02.sh` を使うこともできます。手動で 1 ステップずつ確認したい場合は以下の手順に従ってください。
:::

## 2. 学習用イメージを用意する

2 つのワークロードは、MNIST MLP を DDP で学習する `ddp.py` を焼き込んだ専用イメージ `ddp-sample` を共用します。`ddp.py` は [awslabs/awsome-distributed-ai の DDP サンプル](https://github.com/awslabs/awsome-distributed-ai/tree/main/3.test_cases/pytorch/ddp) をベースに、保存先を共有 PVC へ寄せて adapt したものです。rendezvous は awsome と同じ etcd 方式を採用しています。Dockerfile はリポジトリの [`infra/eks/manifests/ddp-sample/`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/manifests/ddp-sample) に置いてあります。

このイメージのビルドは、手元の Docker に頼らずクラスタ内で完結させます。Basic01 の `terraform apply`（`image_builder_enabled` が既定で有効）で、ビルド用の Amazon ECR リポジトリ・IAM ロール・Pod Identity・専用 namespace（`image-builder`）がすでに用意されています。ビルド自体は [Kaniko](https://github.com/GoogleContainerTools/kaniko) の Job で行い、公開リポジトリから直接ソースを取得して Dockerfile をビルドし、ECR に push します。ECR 認証は Pod Identity 経由で解決されるため、`docker login` も認証情報の受け渡しも要りません。ビルドの起動は学習 Job と同じく Helm チャートのレンダリングで行います。

ビルド先の ECR URL は Terraform の出力から取得できます。イメージタグはワークショップ用に `v1` を使います（再ビルドするときは `v2` のようにタグを進めると、`latest` のキャッシュ問題を避けられます）。

```bash
cd infra/eks
ECR_URL=$(terraform output -raw ddp_sample_ecr_url)
IMAGE=${ECR_URL}:v1

# クラスタ内で Kaniko ビルド Job を起動
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

進捗やエラーはビルド Job のログで確認できます。

```bash
kubectl -n image-builder logs -f job/build-ddp-sample-v1
```

:::message
このイメージには sshd も Open MPI も etcd サーバーも入りません。`torch` と `torchvision` は PyTorch のベースイメージに同梱されており、唯一の追加は etcd rendezvous 用のクライアントライブラリ（`python-etcd`）だけです。etcd サーバー自体は別の Pod として Helm チャートが namespace 内に立てます。
:::

:::message
どうしても手元でビルドしたい場合は、[`infra/eks/manifests/ddp-sample/README.md`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/manifests/ddp-sample) に `docker`（または `finch`）でのビルド・push 手順があります。その場合は `terraform apply` 時に `image_builder_enabled = false` にしてクラスタ内ビルドの機構を作らないようにもできますが、既定のクラスタ内ビルドで完結するのでこの節では使いません。
:::

:::message
Kaniko はベースイメージをノードのローカルディスク上に展開してビルドします。ビルド中のピークディスクはおおよそ push 後イメージサイズの 4〜5 倍で、`ddp-sample`（push 後約 3GB）は共有 CPU ノードプールの既定 50Gi ルートに収まります。数十 GB 級の重いイメージ（独自 CUDA 拡張入りの学習イメージなど）を扱う場合は、`terraform apply` 時に `image_builder_dedicated_pool = true` を設定すると、NVMe インスタンスストアを束ねた大容量ローカルディスクのビルド専用ノードプール（taint で隔離、ビルドが終われば自動で 0 台に戻る）が用意されます。その際は Helm 側で `--set imageBuild.dedicatedPool.enabled=true --set imageBuild.ephemeralStorage=150Gi` のように専用プールを指定します。共有ファイルシステム（FSx や NFS）をビルド作業領域に使うことはできません。Kaniko の展開先はノードのルートに固定されており、ネットワークストレージ上ではファイル属性の扱いで壊れたイメージが生成される恐れがあるためです。
:::

続いて、後半で使う Kubeflow Training Operator が入っていることを確認します。Basic01 の `terraform apply`（`training_operator_enabled` が既定で有効）で v1.9.0 が導入済みのはずなので、PyTorchJob の CRD が見えることだけ確かめておきます。

```bash
kubectl get crd pytorchjobs.kubeflow.org
```

## 3. 単一ノードで torchrun を動かす（前半）

まず 1 ノードの中で 2 プロセスの DDP を動かします。`gpu.enabled` を付けないのでこのまま CPU（gloo）で動きます。`nprocPerNode=2` は 1 ノード内に立てる rank 数です。前のステップでビルドしたイメージの URI を `torchrunTrain.image` に渡します。共有ストレージは Basic01 で作成済みの `openzfs-claim` PVC を再利用するため、`sharedStorage.existingClaimName` に渡します。

```bash
cd infra/eks
helm template exp charts/experiments -n "$NAMESPACE" \
    --set sharedStorage.existingClaimName=openzfs-claim \
    --set torchrunTrain.enabled=true \
    --set torchrunTrain.image="$IMAGE" \
    --set torchrunTrain.backend=gloo \
    --set torchrunTrain.nodeRole=cpu \
    --set torchrunTrain.nprocPerNode=2 \
    | kubectl apply -f -
```

:::message
`sharedStorage.existingClaimName` を渡すのは、共有ストレージの静的 PersistentVolume（`openzfs-shared`）が 1 つしかなく、1 つの PVC にしかバインドできないためです。Basic01 で `openzfs-claim` がこの PV を掴んでいるので、チャートに新しい PVC を作らせるとバインド先が無く永久に `Pending` になります。既存の PVC を再利用するようこの値を渡すと、その競合を避けられます。
:::

Pod が `Running` になるまでログは出ません。まずスケジュールされたことを確認してからログを追います（初回は CPU ノード起動とイメージ pull で数分かかります）。

```bash
kubectl get pods -n "$NAMESPACE" -l job-name=ddp-torchrun -w
```

学習ログを確認します。

```bash
kubectl logs -f job/ddp-torchrun -n "$NAMESPACE"
```

2 つの rank は同じ 1 つのログストリームに出力するため、両者の行が入り混じって流れます。各 rank が backend の選択・データのロード・各エポックの loss を出すので、同じ形式の行が rank 0 と rank 1 の両方から出ます。

```
[rank 0/2] backend=gloo cuda_available=False device_count=0
[rank 1/2] backend=gloo cuda_available=False device_count=0
[rank 0/2] starting training: 3 epochs, batch_size 32
[rank 1/2] starting training: 3 epochs, batch_size 32
[rank 0/2] epoch 0 | steps 938 | loss 0.4123
[rank 1/2] epoch 0 | steps 938 | loss 0.4098
```

各 rank は `DistributedSampler` によってデータセットの異なる部分を担当し、勾配を all-reduce で共有しながら同じモデルを更新します。エポックが進むと loss が減少します。`ddp.py` は各エポックの終わりに（`SAVE_EVERY` の既定は 1）rank 0 だけがスナップショットを共有ストレージ上の `/shared/output/torchrun-gloo/snapshot.pt` へ上書き保存し、3 エポックを終えて両 rank が正常終了します。「保存は rank 0 のみが行う」というのは DDP の定石で、全 rank が同じモデルを持っているため保存は 1 つで足ります（共有ファイルシステムでは全 rank が同じファイルへ同時書き込みすると破損しかねない、という実務上の理由もあります）。

完了を待ちます。初回は Karpenter が CPU ノードを立ち上げ、学習イメージを pull するため、学習開始までに数分の追加時間がかかります。学習本体と合わせて余裕を見て 30 分のタイムアウトにしています。

```bash
kubectl wait --for=condition=complete job/ddp-torchrun -n "$NAMESPACE" --timeout=30m
kubectl delete job ddp-torchrun -n "$NAMESPACE"
```

## 4. 複数ノードで PyTorchJob を動かす（後半）

次に、同じ学習を 2 ノードにまたがる PyTorchJob で動かします。`workers=2` が Worker Pod の台数、`nprocPerNode=1` が各 Worker 内のプロセス数です。テンプレートには `topologyKey: kubernetes.io/hostname` の podAntiAffinity が入っているので、2 つの Worker Pod は必ず別ノードに分かれて配置されます（結果として Worker Pod 数 = ノード数になります）。前半と違い、rank の割り当てと集合点の配線は Training Operator と torchrun の etcd rendezvous に任せます。Helm チャートが同じ namespace に etcd Deployment + Service を自動で立てるため、追加の手作業は不要です。

```bash
helm template exp charts/experiments -n "$NAMESPACE" \
    --set sharedStorage.existingClaimName=openzfs-claim \
    --set pytorchjobTrain.enabled=true \
    --set pytorchjobTrain.image="$IMAGE" \
    --set pytorchjobTrain.backend=gloo \
    --set pytorchjobTrain.nodeRole=cpu \
    --set pytorchjobTrain.workers=2 \
    --set pytorchjobTrain.nprocPerNode=1 \
    | kubectl apply -f -
```

まず 2 つの Worker Pod がそれぞれ別ノードに載っていることを確認します（`-o wide` の `NODE` 列が 2 つとも違えば OK です）。

```bash
kubectl get pods -n "$NAMESPACE" -o wide -l training.kubeflow.org/job-name=ddp-pytorchjob
```

次に、PyTorchJob の状態を確認します。`-w` を付けると `Succeeded` になるまでターミナルが張り付くので、状態を見たら `Ctrl-C` で抜けます。

```bash
kubectl get pytorchjob ddp-pytorchjob -n "$NAMESPACE" -w
```

rank 0 が載る Worker-0 のログを追います。

```bash
kubectl logs -f ddp-pytorchjob-worker-0 -n "$NAMESPACE"
```

前半の単一ノードと違い、rank 0 と rank 1 は別々の Pod なのでログも分かれます。Worker-0 では rank 0 が gloo backend で起動します。MNIST のダウンロードとスナップショットの保存は rank 0 が担当するため、そのログ行は Worker-0 側にのみ現れます。

```
[rank 0/2] backend=gloo cuda_available=False device_count=0
[rank 0/2] downloading MNIST to /shared/mnist-data
[rank 0/2] starting training: 3 epochs, batch_size 32
[rank 0/2] epoch 0 | steps 938 | loss 0.5312
[rank 0/2] epoch 0 | snapshot saved to /shared/output/pytorchjob-gloo/snapshot.pt
[rank 0/2] epoch 1 | steps 938 | loss 0.2287
[rank 0/2] epoch 1 | snapshot saved to /shared/output/pytorchjob-gloo/snapshot.pt
[rank 0/2] epoch 2 | steps 938 | loss 0.1614
[rank 0/2] epoch 2 | snapshot saved to /shared/output/pytorchjob-gloo/snapshot.pt
[rank 0/2] done
```

もう一方の Worker-1（rank 1）のログも見てみます。

```bash
kubectl logs ddp-pytorchjob-worker-1 -n "$NAMESPACE"
```

Worker-1 では rank 1 が同じく gloo backend で起動し、rank 0 の MNIST ダウンロード完了を barrier で待ってから学習に加わり、各エポックの loss を出して最後に `done` で終わります。ダウンロードとスナップショット保存の行は rank 0 側にしか出ません。

```
[rank 1/2] backend=gloo cuda_available=False device_count=0
[rank 1/2] starting training: 3 epochs, batch_size 32
[rank 1/2] epoch 0 | steps 938 | loss 0.5289
[rank 1/2] epoch 1 | steps 938 | loss 0.2301
[rank 1/2] epoch 2 | steps 938 | loss 0.1627
[rank 1/2] done
```

`WORLD_SIZE=2` の 2 プロセスが別々のノードで起動し、両 rank の loss がエポックを追って単調に下がっていることから、2 つのノードが勾配を all-reduce しながら 1 つのモデルを学習できていることが分かります（各 rank はデータセットの異なる分割を担当するので、loss は完全に同一ではなく近い値で推移します）。最後に PyTorchJob が `Succeeded` になり、rank 0 がスナップショットを共有ストレージ上の `/shared/output/pytorchjob-gloo/snapshot.pt` に保存します。

:::message
上のログは形式を示すための例で、loss の数値そのものは seed やデータ分割で変わります。単調に減少していれば正常です。DDP の 2 ノード動作自体は distai-eks-smoke クラスタ（us-east-2、CPU ノード r5a.large ×2）で確認済みです。
:::

:::message
Worker の 1 つが 1 回だけ再起動（RESTARTS が 1）することがありますが、これは異常ではありません。etcd が起動する前やイメージ pull が遅れている間に Worker が rendezvous に到達できないと一度終了し、`restartPolicy: OnFailure` によって再試行して合流します。etcd は Worker とは独立に動いているため、再起動した Worker は etcd に再接続するだけで rendezvous をやり直せます。テンプレートは意図的に `Never` ではなく `OnFailure` を使っています。ただし再試行は `elasticPolicy.maxRestarts: 100` で打ち切られます。
:::

確認できたら削除します。

```bash
kubectl delete pytorchjob ddp-pytorchjob -n "$NAMESPACE"
```

## 5. GPU（nccl）で動かす場合

同じワークロードを GPU に載せ替えるときの形だけ示します。GPU 実行を実際に決めるのは `gpu.enabled=true` と Pod の `nvidia.com/gpu` リクエストで、これが揃うと `ddp.py` が nccl backend を選びます。`backend=nccl` はあくまで出力パスのラベルなので、`gpu.enabled=true` を付け忘れると GPU ノード上でも gloo で動いてしまい、出力先だけ `*-nccl` になるという分かりにくい不整合が起きます。GPU プールを選ぶ `nodeRole` と合わせて、この 3 つをセットで指定するのがポイントです。`nprocPerNode` は 1 Worker あたりの GPU 数（`gpu.count`）と一致させます（1 プロセスが 1 GPU を掴むため、ずれると同じ GPU を奪い合います）。

```bash
# 単一ノード torchrun を 1 GPU で
helm template exp charts/experiments -n "$NAMESPACE" \
    --set sharedStorage.existingClaimName=openzfs-claim \
    --set torchrunTrain.enabled=true --set torchrunTrain.image="$IMAGE" \
    --set torchrunTrain.backend=nccl --set torchrunTrain.nodeRole=<GPU プール名> \
    --set torchrunTrain.gpu.enabled=true --set torchrunTrain.gpu.count=1 \
    --set torchrunTrain.nprocPerNode=1 | kubectl apply -f -
```

:::message
この nccl のコマンドは形を示すためのもので、本章では実機検証していません（本章で実測したのは CPU の gloo 経路だけです）。Karpenter の仕組みは Basic03 で掘り下げ、GPU プールの用意とその上での実際の学習は Basic04 以降で扱います。本章の時点では CPU（gloo）で「動く」ことを確認できていれば十分です。
:::

# まとめ

本章では、GPU を使わずに Amazon EKS の CPU ノード上で MNIST MLP の DDP 学習を 2 段構えで動かしました。前半はオペレータ不要の `torchrun`（`batch/v1` Job）で 1 ノード内の 2 プロセスを走らせ、後半は Kubeflow の PyTorchJob で 2 ノードにまたがる分散学習を走らせました。いずれも gloo backend で動かし、loss が減少すること、そして rank 0 のみがスナップショットを共有ストレージに保存するという DDP の基本動作を確認しました。複数ノードの PyTorch 学習には、SSH の足回りが要る MPIJob 構成ではなく、PyTorch のために作られた PyTorchJob を使い、Worker だけの構成では `spec.elasticPolicy` で etcd rendezvous を配線する、という勘所も押さえました。共有ストレージは既定で単一 AZ の FSx for OpenZFS を使い、その選定理由（計算が単一 AZ に寄る基盤ではマルチ AZ 共有は活きない）も見ました。次章 Basic03 で Karpenter を掘り下げ、Basic04 以降でこの DDP を GPU（nccl backend）に載せ替え、さらに EFA でマルチノードに広げていきます。

# 参考資料

- [PyTorch DistributedDataParallel](https://pytorch.org/docs/stable/notes/ddp.html)
- [torchrun (Elastic Launch)](https://pytorch.org/docs/stable/elastic/run.html)
- [Kubeflow Training Operator v1 (PyTorchJob)](https://trainer.kubeflow.org/en/latest/legacy-v1/user-guides/pytorch.html)
- [awslabs/awsome-distributed-ai の DDP テストケース (Kubernetes)](https://github.com/awslabs/awsome-distributed-ai/tree/main/3.test_cases/pytorch/ddp/kubernetes)
- [対象ワークロード torchrunTrain（charts/experiments）](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/torchrun-train.yaml)
- [対象ワークロード pytorchjobTrain（charts/experiments）](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/pytorchjob-train.yaml)
