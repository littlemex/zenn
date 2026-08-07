---
title: "Basic03 - Karpenter を導入する"
free: true
---

本章では、Pod のリソース要求に応じてアクセラレーターノードを動的に起動する Karpenter を扱います。Karpenter 本体は Basic01 の `terraform apply` で導入済みなので、本章はその構成（CRD 管理・認証方式の Pod Identity・Spot 中断通知の SQS）を読み解き、動作を確認します。アクセラレーター用の NodePool は次章以降で定義します。

# 解説

## 全体構成

この book 全体の構成のうち、本章で扱うのは Amazon EKS コントロールプレーンと System ノードの上で動く **Karpenter コントローラ** と、その CRD・SQS interruption queue です。Karpenter は NodePool を読み取り、要求に応じてノードを起動します（CPU 用の NodePool は Basic01 で作成済みで、アクセラレーター用は次章以降で定義します）。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

## これは何をするものか

Karpenter は、スケジュールできずに `Pending` のままになっている Pod のリソース要求を監視し、それを満たす Amazon EC2 インスタンスを自動的に起動・終了させる Kubernetes コントローラです。ノードを事前にまとめて用意しておく Amazon EKS Managed Node Group とは発想が逆で、Pod が要求してから初めてノードが立つ「demand-driven」なプロビジョニングを行います。

この構成で Managed Node Group ではなく Karpenter を選ぶ理由は 2 つあります。1 つは、この基盤で使うアクセラレータの型が `g6e` 系 GPU、`p5en` 系 GPU、`trn2` 系 Neuron など多様で、ワークロードごとに必要な型が変わることです。Managed Node Group でも 1 つのグループに複数のインスタンスタイプを指定できますが、GPU 数やアーキテクチャの異なる型を混ぜるとスケジューリングやスケール判断が破綻するため、実務では型ファミリーごとにグループを分けるのが定石で、型の種類が増えるほど管理コストが跳ね上がります。もう 1 つは、アクセラレーターインスタンスは時間単価が高く、常時起動しておくコストが大きいことです。Karpenter は Pod が要求したときだけノードを起動し、不要になれば consolidation で終了させられるので、オンデマンドや Spot のノードを使った分だけの課金に近づけられます。特にバッチ的な学習・推論ジョブでは、ジョブのある間だけノードが立ち、終われば自動で回収される運用に向いています。ただしこれは購入オプションによって変わります。Capacity Block for ML は予約した時間ぶんを固定で確保する（=その間は起動していなくても課金される）ため、consolidation で「使った分だけ」には縮みません。Karpenter はこうした Capacity Block・オンデマンド・Spot を同じ NodePool で併記でき、予約枠を優先しつつ足りない分を Spot やオンデマンドで補うといったリソース増強の使い分けもできます。

:::message
実際に柔軟にリソース確保ができるかどうかはさておき、Spot/On Demand/Capacity Block などの購入オプション、様々なインスタンスサイズやタイプ、を柔軟に扱えることは重要な要件としています。
:::

導入の実装上のポイントは 3 つあります。

1 つ目は CRD の管理方法です。Karpenter が使う `EC2NodeClass` / `NodePool` / `NodeClaim` の CRD は、コントローラ本体の Helm chart（`karpenter`）とは別の chart（`karpenter-crd`）として提供されています。これは Helm の仕様上、chart の `crds/` ディレクトリに含まれる CRD は `helm upgrade` の対象外で、初回インストール時のスキーマのまま更新されないためです。`karpenter-crd` を同じバージョンで別チャートとして管理すれば、バージョンアップ時に CRD のスキーマも一緒に更新できます。

2 つ目は認証方式です。Karpenter コントローラが Amazon EC2 の起動・終了などの AWS API を呼ぶために必要な権限は、Amazon EKS Pod Identity を使って付与します。Pod Identity は IAM ロールとの結び付けを ServiceAccount のアノテーションではなく EKS 側のリソース（Pod Identity Association）で完結させられるため、設定がシンプルになります。これが機能するには `eks-pod-identity-agent` アドオンが必要ですがこれは導入済みです。

3 つ目は Spot 中断への対応です。Karpenter は SQS の interruption queue を経由して、Spot インスタンスの中断通知や AWS のヘルスイベント（スケジュールされた変更）などを受け、対象ノード上の Pod を強制終了ではなく graceful に drain してから終了させます。なお rebalance recommendation もこの queue に届きますが、Karpenter はこれを能動的なノード置換のトリガーにはしません（drain の対象は Spot 中断警告・スケジュール変更ヘルスイベント・インスタンス停止/終了です）。この queue と、通知を queue に流す Amazon EventBridge ルールの作成も、Karpenter 導入の一部として行います。

drain で退去した Pod は削除され、それを管理する上位コントローラ（Deployment や Job／TrainJob など）が代替の Pod を作成します。その代替 Pod が `Pending` になり、これを Karpenter のプロビジョニングが検知して、要求を満たす新しいノードを自動で起動します（上位コントローラを持たない素の Pod は再作成されず、Job 系も restart 設定しだいでは代替が作られない点に注意します）。中断ハンドリング（消す側）とプロビジョニング（立てる側）は別々に動くため、明示的な再取得の指示は要りません。次にどの購入オプションで取り直すかは NodePool の `karpenter.sh/capacity-type` に許可した値しだいで、`spot` だけなら在庫があれば再び Spot を、`spot` と `on-demand` を併記していれば Spot が取れないときは on-demand にフォールバックして台数を満たします。ただし立ち上がるのは元と同じインスタンスではなく別ノードなので、途中結果は共有ストレージ上のスナップショットから resume する前提で組みます（Basic02 の resume がこれにあたります）。中断そのものを避けたい長時間ジョブには、Spot のような突発的中断が起きない Capacity Block を選ぶ、という使い分けになります（Capacity Block も予約終了の 30 分前からインスタンスの回収が始まるため、終了処理やチェックポイントはそれを見込んで設計します）。

以降で実際の Terraform コードを引用しながら、なぜその値・その書き方にしているのかを見ていきます。対象ファイルは [`karpenter.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/karpenter.tf) と [`iam.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/iam.tf) です。

## CRD を別チャートで管理する

CRD のインストールは [`karpenter.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/karpenter.tf) で、コントローラ本体とは別の `helm_release` として行います。

```hcl
# karpenter.tf（抜粋）
# ECR public auth token must always come from us-east-1 (AWS API restriction)
data "aws_ecrpublic_authorization_token" "karpenter" {
  provider = aws.us_east_1
}

# Karpenter CRDs, installed as a SEPARATE chart. Helm never upgrades CRDs bundled in a
# chart's crds/ directory, so bumping var.karpenter_chart_version would otherwise leave the
# EC2NodeClass/NodePool/NodeClaim CRDs at their first-installed schema. Installing the
# dedicated karpenter-crd chart (same version) lets `helm upgrade` roll the CRD schema too.
resource "helm_release" "karpenter_crd" {
  namespace        = local.karpenter_namespace
  create_namespace = true
  name             = "karpenter-crd"
  repository       = "oci://public.ecr.aws/karpenter"
  chart            = "karpenter-crd"
  version          = var.karpenter_chart_version

  repository_username = data.aws_ecrpublic_authorization_token.karpenter.user_name
  repository_password = data.aws_ecrpublic_authorization_token.karpenter.password

  # The karpenter-crd chart exposes webhook.enabled to wire a CRD conversion webhook. Karpenter
  # removed the v1beta1->v1 conversion webhooks in v1.1, so on the version pinned here it is not
  # needed; disable it explicitly to keep the CRD schema plain.
  set {
    name  = "webhook.enabled"
    value = "false"
  }

  depends_on = [module.eks]
}
```

**`aws.us_east_1` プロバイダエイリアスでトークンを取得する。** `oci://public.ecr.aws/karpenter` から chart を pull するには Amazon ECR Public の認証トークンが必要ですが、このトークンは AWS API の制約でリージョンを `us-east-1` に固定して取得しなければなりません。

## Karpenter コントローラの Helm リリース

コントローラ本体は同じ `karpenter.tf` の `helm_release.karpenter` で入れます。

```hcl
# karpenter.tf（抜粋）
resource "helm_release" "karpenter" {
  namespace        = local.karpenter_namespace
  create_namespace = true
  name             = "karpenter"
  repository       = "oci://public.ecr.aws/karpenter"
  chart            = "karpenter"
  version          = var.karpenter_chart_version
  # CRDs are managed by helm_release.karpenter_crd above, so the controller chart must not
  # also ship them.
  skip_crds = true
  wait = false

  repository_username = data.aws_ecrpublic_authorization_token.karpenter.user_name
  repository_password = data.aws_ecrpublic_authorization_token.karpenter.password

  values = [
    <<-EOT
    # Run Karpenter on the stable system node group, not on nodes it manages
    nodeSelector:
      karpenter.sh/controller: "true"

    # Required when running on a VPC with custom DNS / non-cluster-aware resolvers
    dnsPolicy: Default

    # Pod Identity is configured in iam.tf via module.karpenter.
    # No serviceAccount.annotations (IRSA) needed.

    settings:
      clusterName: ${module.eks.cluster_name}
      clusterEndpoint: ${module.eks.cluster_endpoint}
      # SQS queue for spot interruption, rebalance, and AWS health events
      interruptionQueue: ${module.karpenter.queue_name}
    EOT
  ]

  depends_on = [
    module.eks,
    module.karpenter,
    helm_release.karpenter_crd,
  ]
}
```

**`skip_crds = true` を付ける。** `karpenter-crd` chart で CRD を管理している以上、コントローラ chart 側からは CRD をインストールさせません。Helm 3 は `crds/` ディレクトリの CRD が既に存在すればスキップするだけなので衝突エラーにはなりませんが、`crds/` 経由で入れた CRD にはリリースの所有権情報が付かず、しかも Helm は `crds/` を初回 install 時にしか処理せず `helm upgrade` では触りません。そのため、どちらの chart が同梱 CRD で入れたかによって以降のスキーマ更新の追従可否が変わってしまいます。CRD の管理主体を `karpenter-crd` chart 一本に限定するために、コントローラ chart 側では明示的にスキップします。

**`nodeSelector` で `karpenter.sh/controller: "true"` を要求する。** Basic01 で System ノードグループに付けたラベルと同じキーです。Karpenter は自分が管理するノードにこのラベルを付けないため、このラベルを持つノードは常に Karpenter 管理外の System ノードだけになり、コントローラが自己参照で詰まることがありません。

**`settings.interruptionQueue` に `module.karpenter.queue_name` を渡す。** 作成される SQS queue の名前をそのまま Helm values に埋め込んでいます。この 1 行だけで Spot 中断通知の受信先が決まります。

## Pod Identity と interruption queue（iam.tf）

Karpenter コントローラ用の IAM ロール・Pod Identity association・SQS queue はすべて [`iam.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/iam.tf) の `module "karpenter"` に集約されています。

```hcl
# iam.tf（抜粋）
module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "21.24.0"

  cluster_name = module.eks.cluster_name
  region       = var.region

  enable_inline_policy = true

  create_pod_identity_association = true
  namespace                       = local.karpenter_namespace
  service_account                 = local.karpenter_service_account

  node_iam_role_use_name_prefix = false
  node_iam_role_name            = local.karpenter_node_role_name
  create_instance_profile       = true

  enable_spot_termination = true

  node_iam_role_additional_policies = {
    AmazonSSMManagedInstanceCore = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
    NodeS3ReadWrite              = aws_iam_policy.karpenter_node_s3.arn
  }

  tags = var.tags

  depends_on = [module.eks]
}
```

**`enable_spot_termination = true` が SQS queue と Amazon EventBridge ルールを両方作る。** この 1 行が、Spot 中断通知を受け取る interruption queue と、その queue に Spot 中断イベント・AWS ヘルスイベント・リバランス推奨を流す Amazon EventBridge ルールをまとめて作成します。上の Helm values で参照した `module.karpenter.queue_name` は、この行がなければ存在しません。

**`node_iam_role_use_name_prefix = false` でノードロール名を固定する。** ノード用 IAM ロール名を `${var.cluster_name}-karpenter-node`（`locals.tf` の `karpenter_node_role_name`）に固定しています。デフォルトではモジュールがランダムな suffix を付けた名前を生成します（この suffix はリソース作成時に一度だけ決まり、以後の apply では変わりませんが、名前が予測不能になります）。ロール名を固定するとインスタンスプロファイル名も確定的になり、以降の章で紹介する `EC2NodeClass` から決め打ちの名前で参照できます。

**`node_iam_role_additional_policies` で 2 つのポリシーを足す。** Session Manager 経由でノードにログインする、アカウント ID で始まる名前の Amazon S3 バケット（実験データ用の命名規則）への読み書きをノードに許可します。

```hcl
# iam.tf（抜粋、S3 ポリシーの definition）
data "aws_iam_policy_document" "karpenter_node_s3" {
  statement {
    sid    = "S3ReadWrite"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:s3:::${data.aws_caller_identity.current.account_id}-*",
      "arn:${data.aws_partition.current.partition}:s3:::${data.aws_caller_identity.current.account_id}-*/*",
    ]
  }
}
```

バケット ARN のアカウント部分は `data.aws_caller_identity.current` から動的に取得しており、変数として手入力する必要はありません。これにより、別アカウントにコピーしたときに誤って他アカウントのバケットを指す ARN になるリスクを排除しています。

## Terraform destroy 時のノード drain 待ち

`karpenter.tf` にはもう 1 つ、Helm リリースそのものとは別に `null_resource.wait_for_node_drain` があります。これは Karpenter 自体の機能ではなく、リソース削除を安全に行うための Terraform 側の工夫です。

```hcl
# karpenter.tf（抜粋）
resource "null_resource" "wait_for_node_drain" {
  triggers = {
    cluster_name = module.eks.cluster_name
    region       = var.region
    aws_profile  = var.aws_profile != null ? var.aws_profile : ""
  }

  depends_on = [
    helm_release.karpenter,
    helm_release.gpu_operator,
    helm_release.aws_efa_k8s_device_plugin,
    helm_release.neuron,
    helm_release.trainer,
    aws_eks_addon.efs_csi_driver,
    aws_eks_addon.fsx_csi_driver,
    helm_release.openzfs_csi_driver,
    aws_security_group.efa_node,
    aws_placement_group.accelerator,
    aws_vpc_endpoint.interface,
    aws_vpc_endpoint.s3,
  ]

  provisioner "local-exec" {
    when = destroy
    # ... kubectl get nodeclaims.karpenter.sh が 0 件になるまでポーリングする
  }
}
```

**なぜこれが要るのか。** `kubectl_manifest` は `NodePool` / `NodeClaim` の削除を Kubernetes API が受理した瞬間に「完了」として報告しますが、実際のノード drain・Amazon EC2 インスタンス終了・ENI 解放は Karpenter コントローラが非同期に行う後処理です。GPU ノードが起動中に `terraform destroy` で Karpenter やその関連コントローラ（EFA デバイスプラグイン、Amazon EFS/Amazon FSx for Lustre CSI ドライバなど）を先に消してしまうと、その Amazon EC2 インスタンスは誰にも終了されずに課金され続ける「孤児」インスタンスになります。

## Dynamic Resource Allocation（DRA）とは何か

ここまで扱ってきた `nvidia.com/gpu` や `vpc.amazonaws.com/efa` のような拡張リソースは、GPU/Neuron や EFA インターフェースをノード上の device plugin が数量として Kubernetes API サーバーに登録し、Pod 側は `resources.limits` に個数を書いて要求する、という枠組みです。device plugin はノードごとに動く DaemonSet で、デバイスを単純な整数カウントとして表現するため、Pod 側はリソース要求の枠組みの中では「何個欲しいか」しか指定できず、GPU の世代やメモリ容量、トポロジといった属性はリソース要求そのものでは選べません（実務ではノードラベルに対する `nodeSelector`/`nodeAffinity` で機種を選び分けます）。

Dynamic Resource Allocation（DRA）は、この device plugin 方式に代わる新しいデバイス割り当ての仕組みです。DRA では `DeviceClass` / `ResourceClaim` / `ResourceClaimTemplate` という新しい API リソースを使い、Pod は「このクラスのデバイスを 1 つ要求する」という `ResourceClaim` を経由してデバイスにアクセスします。デバイスドライバはノード上のデバイスを `ResourceSlice` として公開し、そこにはモデル名やメモリ容量、トポロジといった豊富な属性が載るため、スケジューラは CEL（Common Expression Language）式でその属性を条件にデバイスを選べます。複数コンテナが同じ `ResourceClaim` を共有してデバイスを使い分けられる点も、単純なカウント方式の device plugin には無い特徴です（マルチノード GPU 通信を `ComputeDomain` という単位で管理する仕組みは DRA コア API ではなく NVIDIA の DRA ドライバ固有の機能です）。永続ボリュームを動的にプロビジョニングする仕組みに近い体験を、アクセラレーターにも持ち込むことが DRA の狙いです。DRA のコア API は Kubernetes 1.34 で GA（Stable）となり、同じ 1.34 の時点でデフォルトで有効です（機能ゲートは GA 昇格と同時に既定有効かつロックされます）。

## Karpenter は DRA にまだ対応していない

DRA が GA になったからといって、この book の構成にそのまま持ち込めるわけではありません。[Amazon EKS の GPU デバイス管理に関する AWS 公式ドキュメント](https://docs.aws.amazon.com/eks/latest/userguide/device-management-nvidia.html) は、Kubernetes 1.34 以降で EKS マネージド型ノードグループやセルフマネージド型ノードグループを使う新規デプロイに NVIDIA DRA driver を推奨しつつも、次の制約を明示しています: NVIDIA DRA driver は Karpenter および EKS Auto Mode では現状サポートされておらず、Karpenter と EKS Auto Mode では引き続き NVIDIA device plugin を使う必要があるという制約です。同ドキュメントはこの制約の追跡先として upstream の [KEP-5004](https://github.com/kubernetes/enhancements/issues/5004) を挙げています。

KEP-5004 は正式には「DRA: Handle extended resource requests via DRA Driver」という提案で、DRA ドライバが公開するデバイスを、device plugin を介さずに `nvidia.com/gpu` のような従来の拡張リソース API 経由でも要求できるようにすることを目指しています。この仕組みが実現すると、同じクラスタの一部のノードが device plugin を使い、別の一部のノードが DRA ドライバを使うという混在運用や、既存の Pod マニフェストを書き換えずに DRA へ段階的に移行することが可能になる、という位置づけです。この仕組みに伴い、cluster-autoscaler 側が新設される `NodeInfo` の `DynamicResources` フィールドなどを認識してスケール判断に反映できるようにするための連携も、KEP 本文で考慮事項として触れられています（ただし Karpenter については言及がなく、この KEP 自体がオートスケーラーに実装を加えるものでもありません）。KEP のマイルストーンは次のとおりです: Alpha が Kubernetes 1.34、Beta が 1.35 から 1.36 に後ろ倒しされ、Stable（GA）の目標は 1.37 とされています。ただしこれは KEP が置いている目標であり、他の多くの KEP と同様に確定したスケジュールではないため、実際のリリースタイミングは前後する可能性がある点は留保しておきます。

したがって、Karpenter でノードプロビジョニングを行うこの構成では、DRA ドライバは現時点で選択肢になりません。device plugin 方式（NVIDIA GPU Operator、aws-efa-k8s-device-plugin、Neuron device plugin）を使うことが、legacy な妥協ではなく現状で唯一実用的な選択です。Karpenter からも DRA の `ResourceClaim` が扱えるようになれば、この判断は再検討の対象になります。

# ワークショップ実施

Karpenter は Basic01 の `terraform apply` に含めて導入済みの構成です。ここでは導入結果を確認します。以降のコマンドは Basic01 で current-context をこのクラスタに切り替え、`alias k=kubectl` を設定済みの前提です（開き直した場合は Basic01 step 3 の `use-context` / `set-context` / `alias` を実行し直してください）。

## 1. Karpenter コントローラの起動を確認する

```bash
k -n karpenter get pods
```

`karpenter` namespace で controller Pod が `Running` になっていることを確認します。Basic01 で作った System ノード（ラベル `karpenter.sh/controller=true` を持つノード）の上にスケジュールされているはずです。

## 2. CRD が入っていることを確認する

```bash
k get crd -o custom-columns=NAME:.metadata.name | grep karpenter
```

実機出力（Karpenter 1.13.0）:

```text
ec2nodeclasses.karpenter.k8s.aws
nodeclaims.karpenter.sh
nodeoverlays.karpenter.sh
nodepools.karpenter.sh
```

中核となる `ec2nodeclasses.karpenter.k8s.aws` / `nodeclaims.karpenter.sh` / `nodepools.karpenter.sh` の 3 つが表示されれば、`karpenter-crd` chart による CRD 登録が完了しています。`nodeoverlays.karpenter.sh` は本書では使いませんが、同じ chart が登録するため一覧に現れます（表示される CRD の数は Karpenter のバージョンで変わり得ます）。

## 3. 2 つのチャートが別々に入っていることを確認する

```bash
helm list -n karpenter
```

`karpenter-crd` と `karpenter` が見えます。この 2 つは `var.karpenter_chart_version` という 1 つの変数から同じバージョンを受け取る設計なので、バージョンを上げるときはこの変数を変えて `terraform apply` すれば両者が揃って更新されます（Helm リリースは Terraform が管理しているので、手で `helm upgrade` はしません）。

## 4. NodePool と、まだノードが増えていないことを確認する

```bash
k get nodepool
k get nodes
```

`k get nodepool` には `cpu` が 1 つ表示されます。これは `cpu_nodepool_enabled`（既定 `true`）によって Basic01 の apply で作られたもので、Basic02 の CPU DDP がこのプールにノードを起こしていました。アクセラレータ用の NodePool は `accelerator_pools` が空のままなのでまだ存在せず、次章で定義します。

```text
NAME   NODECLASS   NODES   READY   AGE
cpu    cpu         0       True    2d
```

一方 `kubectl get nodes` に見えるのは Basic01 の System ノードだけです（Basic02 で起きた cpu ノードは、ワークロードが終わったあと `consolidateAfter` で回収されています）。NodePool が存在しても、それを要求する Pod がなければノードは立ちません。これが demand-driven なプロビジョニングの動作確認になります。

`NODES` 列が 0 であることと、`READY` が `True`（= Karpenter がこの NodePool を受理して起動待機している）ことの両方を確認してください。

# まとめ

本章では、ノードを要求に応じて起動する Karpenter の構成を確認しました。CRD を別チャート（`karpenter-crd`）で管理してバージョンアップに追従できるようにし、認証は Pod Identity、Spot 中断は SQS interruption queue で graceful に処理する構成です。

Basic02 の CPU DDP がノードを得られていたのは、`cpu` NodePool が Basic01 の apply で先に作られていたからでした。本章でその仕組みを確認したので、次章では `accelerator_pools` を定義して、同じ Karpenter に GPU ノードを起動させます。

# 参考資料

- [Karpenter 公式ドキュメント](https://karpenter.sh/)
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [Karpenter on Amazon EKS (AWS ドキュメント)](https://docs.aws.amazon.com/eks/latest/userguide/karpenter.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
