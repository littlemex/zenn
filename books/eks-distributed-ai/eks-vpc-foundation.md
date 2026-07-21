---
title: "Basic01 - Amazon EKS 基盤を立てる"
free: true
---

本章では、分散学習・推論の実験を回すための土台として Amazon EKS クラスタを構築します。Terraform で Amazon VPC・Amazon EKS コントロールプレーン・Karpenter を動かすための System ノードグループをデプロイし、`kubectl` でノードが見えるところまでを扱います。GPU/Neuron のアクセラレータノード自体は Ch4 以降で立てるため、ここでは「あとから何度でも実験を回せる足場」を一度だけ作ります。

:::message alert
本資料は `us-east-2` リージョンを例に説明します。実際には自身で選択したリージョンに読み替えて進めてください。コマンド中の `<region>` などのプレースホルダは自分の値に置き換えます。
:::

# 解説

## 全体構成

この book 全体で構築する分散 AI 基盤の全体像です。Amazon VPC の中に 2 つの AZ を張り、Amazon EKS コントロールプレーンの下で Karpenter が GPU/Neuron の各 NodePool を要求に応じて起動します。共有ストレージ（Amazon EFS / Amazon FSx for Lustre）や Capacity Block の期限監視（Amazon EventBridge → Amazon SNS）といった周辺サービスも含めた構成です。各コンポーネントは以降の章で 1 つずつ扱います。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で作るのは、この図のうち **Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループ** の 3 つだけです。アクセラレータノードや各種アドオンは後続の章で積み上げていきます。

## これは何をするものか

本章のゴールは、GPU/Neuron を使った分散学習・推論の実験を後からいくらでも回せる「土台」を一度だけ立てることです。土台とは具体的に、Amazon EKS コントロールプレーンと、その上で Karpenter コントローラを動かすための最小構成（Amazon VPC + System ノードグループ）を指します。アクセラレータノード自体はまだ立てません。Ch4 以降で `accelerator_pools` に 1 行足すだけで GPU/Neuron ノードが要求に応じて立つよう、Karpenter の足場をここで用意しておく、という位置づけです。

構成要素は次の 3 つだけで、いずれも「実験を始める前に一度作れば、あとは触らない」部分です。

- **Amazon EKS コントロールプレーン**（Kubernetes 1.35 / Pod Identity）
- **System managed node group**（m5 系 x2）: kube-system と Karpenter コントローラ自身を載せる足場です。Karpenter はここに自分のノードグループを作らない（ラベルで自己参照を避ける）ため、Karpenter が動き出すための最初の土台としてこのノードグループが要ります
- **Amazon VPC**: 上記を収めるネットワーク

この土台づくり自体は Amazon EKS の一般的な手順とほぼ同じですが、分散 AI 向けに効かせている設計判断がいくつかあります。以降で実際の Terraform コードを引用しながら、なぜその値・その書き方にしているのかを見ていきます。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/fix/eks-efa-verification-improvements/infra/eks) です。

## Amazon VPC の設計

Amazon VPC は [`vpc.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/vpc.tf) で `terraform-aws-modules/vpc/aws` モジュールを使って作ります。全体はこれだけです。

```hcl
# vpc.tf
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = var.cluster_name
  cidr = var.vpc_cidr                 # 既定 10.0.0.0/16

  azs             = var.azs                  # 既定 ["us-east-2a", "us-east-2b"]
  private_subnets = var.private_subnet_cidrs # 既定 ["10.0.0.0/18", "10.0.64.0/18"]
  public_subnets  = var.public_subnet_cidrs  # 既定 ["10.0.254.0/24", "10.0.255.0/24"]

  enable_nat_gateway     = true
  single_nat_gateway     = true
  one_nat_gateway_per_az = false

  enable_dns_hostnames = true
  enable_dns_support   = true

  private_subnet_tags = {
    "karpenter.sh/discovery"                    = var.cluster_name
    "kubernetes.io/role/internal-elb"           = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "owned"
  }
  public_subnet_tags = {
    "kubernetes.io/role/elb"                    = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "owned"
  }

  tags = local.cluster_tags
}
```

読みどころは次の 3 点です。

**CIDR のサイジング（`/16` + `/18` x2 + `/24` x2）。** これらの既定値は [`variables.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/variables.tf) で定義しています。Amazon VPC 全体を `/16`（65,536 アドレス）、プライベートサブネットを AZ ごとに `/18`（16,384 アドレス）と大きく取り、パブリックは `/24`（256 アドレス）と小さくしています。この非対称な配分が分散 AI 向けの肝です。アクセラレータノードは VPC CNI が配る secondary IP に加え、EFA 対応インスタンスが EFA 専用 ENI を多数持ちます（`p5en.48xlarge` は 16 枚、`p5.48xlarge` は 32 枚）。1 ENI ごとに IP を消費するため、ワークロードが載るプライベートサブネットは大きく、NAT/ロードバランサーしか置かないパブリックサブネットは小さく、という配分になります。

**`single_nat_gateway = true`。** NAT ゲートウェイを AZ ごとに作らず 1 つだけにしています。本番の可用性設計では AZ ごとに NAT を置きますが、実験基盤では NAT ゲートウェイの時間課金を抑えることを優先しています。プライベートサブネットからの外向き通信（イメージ pull など）はこの単一 NAT を経由します。

**`private_subnet_tags` の `karpenter.sh/discovery`。** このタグが後の章で効いてきます。Karpenter は「ノードを起動してよいサブネット」をこのタグで検出します。ここで**プライベートサブネットにだけ**タグを付け、`public_subnet_tags` には付けていない点が重要です。もし共通の `tags` に含めてしまうと全サブネットに伝搬してパブリックサブネットにも付き、Karpenter がそこにノードを立ててしまいます。パブリックサブネットのノードは Amazon EC2 API への到達経路がなく `nodeadm` によるクラスタ参加に失敗するため、この付け分けは意図的です（詳細は末尾の「注意」節）。

## Amazon EKS クラスタと System ノードグループ

Amazon EKS 本体は [`eks.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/eks.tf) で `terraform-aws-modules/eks/aws` モジュールを使って作ります。アドオンと System ノードグループの定義が読みどころです。

```hcl
# eks.tf（抜粋）
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.24.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version   # 既定 "1.35"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  enable_cluster_creator_admin_permissions = true

  addons = {
    vpc-cni                = { before_compute = true }
    kube-proxy             = {}
    coredns                = {}
    eks-pod-identity-agent = { before_compute = true }
    aws-ebs-csi-driver = {
      pod_identity_association = [{
        role_arn        = aws_iam_role.ebs_csi.arn
        service_account = "ebs-csi-controller-sa"
      }]
    }
  }

  eks_managed_node_groups = {
    system = {
      ami_type       = var.system_node_ami_type
      instance_types = var.system_node_instance_types   # 既定 m5 系
      min_size       = var.system_node_desired_size     # 既定 2
      max_size       = var.system_node_desired_size
      desired_size   = var.system_node_desired_size
      labels = {
        "karpenter.sh/controller" = "true"
      }
    }
  }
}
```

:::message
このモジュールは `terraform-aws-eks` v21 系を使っており、引数名が `name` / `kubernetes_version` です（v20 以前の `cluster_name` / `cluster_version` ではありません）。バージョンを変える際は引数名の互換性に注意してください。
:::

**`before_compute = true` の 2 つのアドオン。** `vpc-cni` と `eks-pod-identity-agent` に `before_compute = true` を付け、ワーカーノードが起動する前にこれらを導入します。特に Pod Identity Agent は、Pod Identity で AWS 権限を得るコントローラ（Ch3 の Karpenter や、上の `aws-ebs-csi-driver`）より先に存在していないと、それらが起動時に認証情報を取得できずクラッシュします。実際 `aws-ebs-csi-driver` には `pod_identity_association` で IAM ロールを渡しており、これが機能するには Pod Identity Agent が先にいる必要があります。順序を保証するためのフラグです。

**System ノードグループの `karpenter.sh/controller` ラベル。** m5 系インスタンス（`var.system_node_ami_type` / `var.system_node_instance_types`）を `var.system_node_desired_size`（既定 2）台、`min_size = max_size = desired_size` で固定起動します。このノードグループは Karpenter が管理するのではなく、Amazon EKS Managed Node Group として常時稼働させます。`karpenter.sh/controller: "true"` というラベルを付けているのは、Ch3 で導入する Karpenter コントローラ自身をこのノードに載せるためです。Karpenter は自分自身が動くノードは作れない（自己参照になる）ので、Karpenter を動かす最初の足場として、Karpenter の管理外のノードグループが必要になります。

## Pod Identity による認証

このモジュールでは、コントローラが AWS API を呼ぶための権限を **Pod Identity** で付与します。従来の IRSA（IAM Roles for Service Accounts）ではなく Pod Identity を選んでいる点が特徴です。[`iam.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/iam.tf) で Karpenter 用の IAM ロールと Pod Identity Association を作ります。

```hcl
# iam.tf（抜粋）
module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "21.24.0"

  cluster_name = module.eks.cluster_name
  region       = var.region

  # コントローラポリシーをマネージドではなくインライン role policy として付与
  enable_inline_policy = true

  # Pod Identity association: kube-system/karpenter SA → コントローラ role
  create_pod_identity_association = true   # IRSA ではなく Pod Identity
  namespace                       = local.karpenter_namespace
  service_account                 = local.karpenter_service_account

  # EC2NodeClass.spec.instanceProfile から参照する決定的なノード role 名
  node_iam_role_use_name_prefix = false
  node_iam_role_name            = local.karpenter_node_role_name
  create_instance_profile       = true
}
```

IRSA は ServiceAccount にアノテーションで IAM ロールを結び付け、OIDC プロバイダ経由で認証する方式です。これに対し Pod Identity は、IAM 側の Pod Identity Association だけで ServiceAccount とロールを結び付けられ、Kubernetes マニフェスト側にアノテーションを書かずに済みます。設定が IAM 側で完結するぶんシンプルで、この構成では Karpenter・EBS/Amazon EFS/Amazon FSx for Lustre の各 CSI ドライバすべてを Pod Identity で統一しています。本章の `eks-pod-identity-agent` アドオンは、この Pod Identity を各 Pod で機能させるためのエージェントです。

`enable_inline_policy = true` にも実務上の理由があります。Karpenter v1 のコントローラポリシーは約 6,172 バイトあり、AWS のマネージドポリシーのサイズ上限（6,144 バイト、変更不可）をわずかに超えて `LimitExceeded: PolicySize: 6144` で失敗します。インラインポリシー（上限 10,240 バイト）にすれば同じ権限のまま上限に収まるため、この構成ではインラインを選んでいます。

## 全体の中での位置付け

本章は基盤構築の最下層にあたります。ここで作った Amazon EKS + System ノードの上に、Ch3 で Karpenter コントローラを載せ、その Karpenter が `accelerator_pools` の定義に従って GPU/Neuron ノードを起動する（Ch4 以降）、という順で積み上がっていきます。つまり本章は「まだ何も GPU が動かない」状態を作る章ですが、これ以降のすべての章がこの土台の上に成り立ちます。

## 注意

分散 AI 特有の落とし穴が 2 つあります。いずれも一度ハマると原因特定に時間がかかるため、最初に押さえておきます。

**注意 1: Amazon VPC の IP アドレスは大きめに確保する。** アクセラレータノードは通常の CPU ノードより桁違いに IP を消費します。VPC CNI がノードごとに secondary IP を配るのに加え、EFA（Elastic Fabric Adapter）対応インスタンスは EFA 専用 ENI を多数持ちます（`p5en.48xlarge` は 16 枚、`p5.48xlarge` は 32 枚）。ENI 1 枚ごとに IP を消費するため、小さな CIDR だとノード数台で枯渇し、最悪 Amazon EKS コントロールプレーンが管理 ENI を置けず `IMPAIRED`（`InsufficientFreeAddresses`）に陥ります。後から広げにくい失敗なので、この構成では Amazon VPC を `/16`、プライベートサブネットを AZ ごとに `/18` と大きめに取り、パブリックは NAT/LB 用途のみなので `/24` にしています。「パブリックは小さく、プライベートは大きく」は `awslabs/awsome-distributed-ai` の HyperPod-EKS リファレンスにも見られる原則です。

**注意 2: `karpenter.sh/discovery` タグをパブリックサブネットに漏らさない。** このタグは Karpenter が「ノードを起動してよいサブネット」を検出するための目印です。Amazon VPC モジュールの共通タグに含めてしまうと全サブネットに伝搬し、パブリックサブネットにも付いてしまいます。すると Karpenter がパブリックサブネットにノードを立て、そのノードは IGW 経由のルートしか持たず Amazon EC2 API に到達できないため `nodeadm` によるクラスタ参加に失敗して詰みます。この構成ではこのタグをプライベートサブネットとノードセキュリティグループにだけ明示的に付け、Amazon VPC 共通タグからは意図的に外しています。

なお、必須アドオン `vpc-cni` と `eks-pod-identity-agent` は `before_compute = true` でワーカーノード起動前に導入します。特に Pod Identity Agent は、Pod Identity で権限を得るコントローラ（EBS CSI ドライバなど）より先に存在していないと、そのコントローラが起動時に Amazon EC2 IMDS ロールを見つけられずクラッシュし、アドオンが `CREATING` のまま止まります。

# ワークショップ実施

## 1. tfvars を準備する

`terraform.tfvars.example` を `terraform.tfvars` にコピーし、`region` / `azs` / `cluster_name` を自分の環境に合わせて設定します。この段階では `accelerator_pools` は空のままで構いません（アクセラレータプールは Ch4 以降で扱います）。

```bash
cd infra/eks
cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars を編集: region, azs, cluster_name
```

## 2. apply する

```bash
terraform init
terraform apply   # 完了まで概ね 15 分程度
```

`terraform apply` の内訳は、Amazon VPC・NAT ゲートウェイ・Amazon EKS コントロールプレーン・System ノードグループの作成です。コントロールプレーンの起動が最も時間を要します。

:::message
`terraform apply` は 15 分ほどかかります。コントロールプレーンが `ACTIVE` になるまで待ちましょう。
:::

## 3. kubeconfig を設定してノードを確認する

```bash
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region <region>
kubectl get nodes
```

`kubectl get nodes` で m5 系のノードが 2 台 `Ready` 状態で表示されれば、System ノードグループの起動は成功です。

## 4. context を確認する習慣をつける

```bash
kubectl config current-context
```

今の時点では地味に見えますが、操作対象のクラスタやリソースが増える後続の章で事故を防ぐための習慣として、ここで身につけておきます。マルチクラスタ環境では、別クラスタ用に context を切り替えたまま元のつもりで操作してしまう事故が起きやすいため、破壊的な操作の前には必ずこのコマンドで対象クラスタを確認します。

:::message alert
`azs` / `private_subnet_cidrs` / `public_subnet_cidrs` の 3 つの配列は長さを揃えてください。長さが食い違うとサブネットを持たない AZ が生まれ、そこにアクセラレータプールを割り当てると Pod が永久に `Pending` になります。この構成では plan 時のバリデーションで配列長の不一致を検出します。
:::

# まとめ

本章では、分散 AI の実験を回すための土台として Amazon EKS クラスタを構築しました。作ったのは Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループの 3 つで、この上に Ch3 から Karpenter とアクセラレータプールを積み上げていきます。Amazon VPC は大きめの CIDR を確保し、`karpenter.sh/discovery` タグをパブリックサブネットに漏らさない、という 2 点だけ押さえておけば、あとは一般的な Amazon EKS 構築とほぼ同じです。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-training)（Amazon VPC サイジングの参考にした HyperPod-EKS リファレンス）
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
