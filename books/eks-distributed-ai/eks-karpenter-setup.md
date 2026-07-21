---
title: "Basic02 - Karpenter を導入する"
free: true
---

本章では、Pod のリソース要求に応じて GPU/Neuron ノードを動的に起動する Karpenter を導入します。CRD 管理・認証方式（Pod Identity）・Spot 中断通知（SQS）まで含めて、安定して運用できる形で入れます。この章の時点ではまだアクセラレータノードは 1 台も立ちませんが、次章以降で `accelerator_pools` を定義したときにノードを自動起動する「エンジン」をここで用意します。

# 解説

## 全体構成

この book 全体の構成のうち、本章で扱うのは Amazon EKS コントロールプレーンと System ノードの上で動く **Karpenter コントローラ**（および CRD・SQS interruption queue）です。Karpenter は次章以降で定義する NodePool を読み取り、要求に応じて GPU/Neuron ノードを起動します。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

## これは何をするものか

Karpenter は、スケジュールできずに `Pending` のままになっている Pod のリソース要求を監視し、それを満たす Amazon EC2 インスタンスを自動的に起動・終了させる Kubernetes コントローラです。ノードを事前にまとめて用意しておく Amazon EKS Managed Node Group とは発想が逆で、Pod が要求してから初めてノードが立つ「demand-driven」なプロビジョニングを行います。

この構成で Managed Node Group ではなく Karpenter を選ぶ理由は 2 つあります。1 つは、この基盤で使うアクセラレータの型が `g6e` 系 GPU、`p5en` 系 GPU、`trn2` 系 Neuron など多様で、ワークロードごとに必要な型が変わることです。Managed Node Group はインスタンスタイプの組み合わせごとにグループを作る必要があり、型の種類が増えるほど管理コストが跳ね上がります。もう 1 つは、GPU/Neuron インスタンスは時間単価が高く、常時起動しておくコストが大きいことです。Karpenter は Pod が要求したときだけノードを起動し、不要になれば consolidation で終了させるため、使った分だけ課金する運用に向いています。

導入の実装上のポイントは 3 つあります。

1 つ目は CRD の管理方法です。Karpenter が使う `EC2NodeClass` / `NodePool` / `NodeClaim` の CRD は、コントローラ本体の Helm chart（`karpenter`）とは別の chart（`karpenter-crd`）として提供されています。これは Helm の仕様上、chart の `crds/` ディレクトリに含まれる CRD は `helm upgrade` の対象外で、初回インストール時のスキーマのまま更新されないためです。`karpenter-crd` を同じバージョンで別チャートとして管理すれば、バージョンアップ時に CRD のスキーマも一緒に更新できます。

2 つ目は認証方式です。Karpenter コントローラが Amazon EC2 の起動・終了などの AWS API を呼ぶために必要な権限は、ServiceAccount に IAM ロールをアノテーションで結び付ける IRSA 方式ではなく、Amazon EKS Pod Identity を使って付与します。Pod Identity は IAM ロールとの結び付けを ServiceAccount 定義ではなく Kubernetes クラスタの外（IAM 側の Pod Identity Association）で完結させられるため、設定がシンプルになります。

3 つ目は Spot 中断への対応です。Karpenter は SQS の interruption queue を経由して、Spot インスタンスの中断通知や AWS のヘルスイベント、リバランス推奨を受け取り、対象ノード上の Pod を強制終了ではなく graceful に drain してから終了させます。この queue と、通知を queue に流す Amazon EventBridge ルールの作成も、Karpenter 導入の一部として行います。

以降で実際の Terraform コードを引用しながら、なぜその値・その書き方にしているのかを見ていきます。対象ファイルは [`karpenter.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/karpenter.tf) と [`iam.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/iam.tf) です。

## CRD を別チャートで管理する

CRD のインストールは [`karpenter.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/karpenter.tf) で、コントローラ本体とは別の `helm_release` として行います。

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

  # The karpenter-crd chart can optionally run a conversion webhook; it is not needed here
  # (the controller chart runs its own), so disable it to avoid a second webhook Deployment.
  set {
    name  = "webhook.enabled"
    value = "false"
  }

  depends_on = [module.eks]
}
```

読みどころは 3 点です。

**`aws.us_east_1` プロバイダエイリアスでトークンを取得する。** `oci://public.ecr.aws/karpenter` から chart を pull するには Amazon ECR Public の認証トークンが必要ですが、このトークンは AWS API の制約でリージョンを `us-east-1` に固定して取得しなければなりません。`providers.tf` に別途定義した `aws.us_east_1` エイリアスをこの `data` ソースにだけ指定し、クラスタ自体のリージョン（`var.region`）とは分離しています。

**`karpenter_chart_version` を `karpenter` と `karpenter-crd` の両方で共有する。** バージョンは `variables.tf` で `1.13.0` を既定値としています。

```hcl
# variables.tf（抜粋）
variable "karpenter_chart_version" {
  description = "Helm chart version for Karpenter (oci://public.ecr.aws/karpenter/karpenter)."
  type        = string
  default     = "1.13.0"
}
```

この 1 つの変数を `helm_release.karpenter_crd` と `helm_release.karpenter` の両方の `version` に渡すことで、バージョンアップ時に 2 か所を揃えて書き換える必要がなくなり、片方だけ上げ忘れる事故を防いでいます。

**`webhook.enabled = false` で `karpenter-crd` chart 側の webhook を無効化する。** `karpenter-crd` chart は CRD の conversion webhook をオプションで持てますが、その webhook はコントローラ本体（`helm_release.karpenter`）側のチャートも同じものを起動します。両方で起動すると Deployment が重複するため、CRD 専用チャート側では明示的に無効化しています。

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

**`skip_crds = true`。** `karpenter-crd` chart で CRD を管理している以上、コントローラ chart 側が同梱する CRD は絶対にインストールさせてはいけません。これを付けないと `helm upgrade` のたびにどちらの chart が CRD の最終形を決めるかが不定になり、`karpenter-crd` 側での更新が無意味になります。

**`wait = false` と、その代わりの `depends_on`。** このリリースはコントローラ Pod が `Ready` になるまで Terraform 側で待ちません。その代わり、後続の `NodePool` / `EC2NodeClass` を表す `kubectl_manifest` リソース（`karpenter-resources.tf`、次章で扱う）が `helm_release.karpenter_crd` に明示的に依存する形で、CRD の登録だけを保証しています。コントローラ Pod 自体の起動完了までは待たないため、「CRD が `Established` になる前に NodePool を apply すると失敗しうる」という後述の注意が残っています。

**`nodeSelector.karpenter.sh/controller: "true"`。** Ch1 で System ノードグループに付けたラベルと同じキーです。Karpenter は自分が管理するノードにこのラベルを付けないため、このラベルを持つノードは常に Karpenter 管理外の System ノードだけになり、コントローラが自己参照で詰まることがありません。

**`settings.interruptionQueue` に `module.karpenter.queue_name` を渡す。** `iam.tf` の `module "karpenter"` が作成した SQS queue の名前をそのまま Helm values に埋め込んでいます。この 1 行だけで Spot 中断通知の受信先が決まります。

**featureGates は書かない。** ソース中のコメントにある通り、v1.13.0 の chart 既定値では `reservedCapacity=true`（BETA）、`nodeRepair` / `nodeOverlay` / `spotToSpotConsolidation` / `staticCapacity` はいずれも `false`（ALPHA）です。この構成で必要な挙動はすべて既定値と一致するため、`values` に `featureGates` ブロックを一切書いていません。

## Pod Identity と interruption queue（iam.tf）

Karpenter コントローラ用の IAM ロール・Pod Identity association・SQS queue はすべて [`iam.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/iam.tf) の `module "karpenter"` に集約されています。

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

**`node_iam_role_use_name_prefix = false` と `node_iam_role_name`。** ノード用 IAM ロール名を `${var.cluster_name}-karpenter-node`（`locals.tf` の `karpenter_node_role_name`）に固定しています。デフォルトではモジュールがランダムな suffix を付けたロール名を生成しますが、それだと次章で書く `EC2NodeClass.spec.instanceProfile` から参照するロール名が Terraform apply ごとに変わってしまいます。名前を固定することで、EC2NodeClass 側から確定的な名前で参照できるようにしています。

**`node_iam_role_additional_policies` の 2 つ。** `AmazonSSMManagedInstanceCore` は Session Manager 経由でノードにログインするために付与しています。`NodeS3ReadWrite` は同じ `iam.tf` 内で定義したカスタムポリシー（`aws_iam_policy.karpenter_node_s3`）で、アカウント ID を名前に含む Amazon S3 バケット（実験データ用の命名規則）への読み書きをノードに許可します。

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

`karpenter.tf` にはもう 1 つ、Helm リリースそのものとは別に `null_resource.wait_for_node_drain` があります。これは Karpenter 自体の機能ではなく、`terraform destroy` を安全に行うための Terraform 側の工夫です。

```hcl
# karpenter.tf（抜粋）
resource "null_resource" "wait_for_node_drain" {
  triggers = {
    cluster_name = module.eks.cluster_name
    region       = var.region
    aws_profile  = coalesce(var.aws_profile, "")
  }

  depends_on = [
    helm_release.karpenter,
    helm_release.gpu_operator,
    helm_release.aws_efa_k8s_device_plugin,
    helm_release.neuron,
    aws_eks_addon.efs_csi_driver,
    aws_eks_addon.fsx_csi_driver,
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

**なぜこれが要るのか。** `kubectl_manifest` は `NodePool` / `NodeClaim` の削除を Kubernetes API が受理した瞬間に「完了」として報告しますが、実際のノード drain・Amazon EC2 インスタンス終了・ENI 解放は Karpenter コントローラが非同期に行う後処理です。GPU/Neuron ノードが起動中に `terraform destroy` で Karpenter やその関連コントローラ（EFA デバイスプラグイン、Amazon EFS/Amazon FSx for Lustre CSI ドライバなど）を先に消してしまうと、その Amazon EC2 インスタンスは誰にも終了されずに課金され続ける「孤児」インスタンスになります。

**`depends_on` の設計。** Terraform は destroy を `depends_on` の逆順で実行するため、この `null_resource` が `depends_on` に列挙している Karpenter・GPU Operator・EFA デバイスプラグイン・Amazon EFS/Amazon FSx for Lustre CSI・placement group・Amazon VPC エンドポイントは、すべてこの drain 待ちが完了した**後**に破棄されます。Amazon VPC エンドポイントが含まれているのは、destroy 中に NAT ゲートウェイが先に消えても、Karpenter コントローラが Amazon VPC エンドポイント経由で Amazon EC2/IAM/STS/SSM の API 呼び出しを続けられるようにするためです（詳細はソースコード中のコメントを参照）。

## 全体の中での位置付け

前章（Ch1）で用意した System ノードの上に、本章で Karpenter コントローラを載せます。Karpenter 自身はまだ何の NodePool も持たないため、この時点ではアクセラレータノードは 1 台も起動しません。次章（Ch3）で `accelerator_pools` を定義すると、その Karpenter が定義に従って GPU/Neuron ノードを起動する、という流れになります。本章は「エンジンだけ載せて、燃料はまだ入れていない」状態を作る章です。

## 注意

**注意 1: `featureGates.reservedCapacity` を Helm values に書かない。** このフラグは Karpenter v1.13.0 のコンパイル時点で `true` がデフォルトになっています。Helm values に明示的にキーを書くと、chart 側のスキーマに存在しないキーとして扱われてエラーになる場合があります。バージョンごとのデフォルト値は chart の `values.yaml` で確認し、デフォルトと同じ値をわざわざ明示しないようにします。

**注意 2: `expireAfter` は ISO-8601 ではなく Go の duration 文字列で書く。** NodePool で使う `expireAfter` フィールドは `P1D` のような ISO-8601 形式ではなく、`"24h"` のような Go 標準の duration 文字列を期待します。ISO-8601 の書式で書いても apply 自体は通ってしまう場合があり、実際の期限が意図した値と違うことに気づきにくいので注意します。

**注意 3: CRD が `Established` になる前の NodePool apply は失敗しうる。** `karpenter-crd` chart のインストール直後、まだ Kubernetes API サーバーに CRD が完全に登録されていない状態で `NodePool` リソースを apply すると失敗することがあります。これは一時的な状態なので、再実行すれば通ることが多い既知の flake として扱ってよいです。

**注意 4: Amazon ECR Public の認証トークンは `us-east-1` から取る。** Karpenter の Helm chart は `oci://public.ecr.aws/karpenter` から取得しますが、Amazon ECR Public の認証トークンは AWS API の制約でリージョンを `us-east-1` に固定して取得する必要があります。他のリージョンからトークンを取ろうとすると認証エラーになります。

# ワークショップ実施

Karpenter は Ch1 の `terraform apply` に含めて導入済みの構成です。ここでは導入結果を確認します。

## 1. Karpenter コントローラの起動を確認する

```bash
kubectl -n karpenter get pods
```

`karpenter` namespace で controller Pod が `Running` になっていることを確認します。Ch1 で作った System ノード（`nodeSelector: karpenter.sh/controller: "true"`）の上にスケジュールされているはずです。

## 2. CRD が入っていることを確認する

```bash
kubectl get crd | grep karpenter
```

`ec2nodeclasses.karpenter.k8s.aws` / `nodeclaims.karpenter.sh` / `nodepools.karpenter.sh` の 3 つが表示されれば、`karpenter-crd` chart による CRD 登録が完了しています。

## 3. 2 つのチャートが別々に入っていることを確認する

```bash
helm list -n karpenter
```

`karpenter-crd` と `karpenter` が別リリースとして並びます。バージョンを上げるときはこの 2 つを同じバージョンで揃えて `helm upgrade` します。

## 4. まだノードが増えていないことを確認する

```bash
kubectl get nodes
```

この時点では `NodePool` を 1 つも定義していないため、Karpenter はまだ起動先の情報を持ちません。表示されるノードは Ch1 の System ノードのみで、GPU/Neuron ノードは増えていません。これが demand-driven なプロビジョニングの動作確認になります。

# まとめ

本章では、GPU/Neuron ノードを要求に応じて起動する Karpenter を導入しました。CRD を別チャート（`karpenter-crd`）で管理してバージョンアップに追従できるようにし、認証は Pod Identity、Spot 中断は SQS interruption queue で graceful に処理する構成です。この時点ではまだノードは増えませんが、次章で `accelerator_pools` を定義すると、この Karpenter が実際に GPU/Neuron ノードを起動し始めます。

# 参考資料

- [Karpenter 公式ドキュメント](https://karpenter.sh/)
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [Karpenter on Amazon EKS (AWS ドキュメント)](https://docs.aws.amazon.com/eks/latest/userguide/karpenter.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
