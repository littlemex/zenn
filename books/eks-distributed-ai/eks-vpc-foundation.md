---
title: "Basic01 - Amazon EKS 基盤を立てる"
free: true
---

本章では、分散学習・推論の実験を回すための土台として Amazon EKS クラスタを構築します。Terraform で Amazon VPC・Amazon EKS コントロールプレーン・Karpenter を動かすための System ノードグループをデプロイし、`kubectl` でノードが見えるところまでを扱います。GPU/Neuron のアクセラレータノード自体は Basic04 以降で立てるため、ここでは「あとから何度でも実験を回せる足場」を一度だけ作ります。

:::message alert
本資料は `us-east-2` リージョンを例に説明します。実際には自身で選択したリージョンに読み替えて進めてください。コマンド中の `<region>` などのプレースホルダは自分の値に置き換えます。
:::

# 解説

## 全体構成

この book 全体で構築する分散 AI 基盤の全体像です。Amazon VPC は複数の AZ にまたがって張り、Amazon EKS コントロールプレーンの下で Karpenter が GPU/Neuron の各 NodePool を要求に応じて起動します。共有ストレージ（既定は単一 AZ の Amazon FSx for OpenZFS と FSx for Lustre）や Capacity Block の期限監視（Amazon EventBridge → Amazon SNS）といった周辺サービスも含めた構成です。各コンポーネントは以降の章で 1 つずつ扱います。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章の `terraform apply` は、この基盤のクラスタスコープの土台を一度に立ち上げます。図のうち中核となる **Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループ** に加え、その上で動く Karpenter コントローラ・各 CSI ドライバ・Kubeflow Training Operator・共有ストレージ（既定の FSx）まで、後続の章で使う基盤コンポーネントが同じ apply で揃います（後述の「基盤層が恒久管理するもの、しないもの」を参照）。本章で詳しく解説するのは中核の 3 つで、残りは各コンポーネントの章で 1 つずつ扱います。唯一まだ立たないのは GPU/Neuron のアクセラレータ「ノード」で、`accelerator_pools` が空の本章では起動せず、Basic04 以降で 1 行足して立てます。

## これは何をするものか

本章のゴールは、GPU/Neuron を使った分散学習・推論の実験を後からいくらでも回せる「土台」を一度だけ立てることです。この土台は 1 回の `terraform apply` でクラスタスコープの基盤コンポーネント（Karpenter・CSI ドライバ・Kubeflow Training Operator・共有ストレージ）まで含めて揃いますが、本章で中身に踏み込んで解説するのは、その最も中核となる次の 3 つです。いずれも「実験を始める前に一度作れば、あとは触らない」部分です。

- **Amazon EKS コントロールプレーン**: Kubernetes 1.35 と Pod Identity を使います
- **System managed node group**: m5 系を 2 台、kube-system と Karpenter コントローラ自身を載せる足場として起動します。Karpenter コントローラを Karpenter 管理下のノードに載せると、ゼロからの起動や自ノードの回収で自己参照の問題が起きるため公式に非推奨で、Karpenter が動き出すための最初の土台としてこの管理外のノードグループが要ります
- **Amazon VPC**: 上記を収めるネットワークです

アクセラレータ「ノード」自体はまだ立てません。Basic04 以降で `accelerator_pools` に 1 行足すだけで GPU/Neuron ノードが要求に応じて立つよう、それを動かす Karpenter の足場をこの apply で用意しておく、という位置づけです。この土台づくり自体は Amazon EKS の一般的な手順とほぼ同じですが、分散 AI 向けに効かせている設計判断がいくつかあります。以降で実際の Terraform コードを引用しながら、なぜその値・その書き方にしているのかを見ていきます。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) です。

## Amazon VPC の設計

Amazon VPC は [`vpc.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/vpc.tf) で `terraform-aws-modules/vpc/aws` モジュールを使って作ります。全体はこれだけです。

```hcl
# vpc.tf
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = var.cluster_name
  cidr = var.vpc_cidr        # 既定 10.0.0.0/16

  azs             = local.azs             # 既定はリージョンの全標準 AZ（az.tf で導出）
  private_subnets = local.private_subnets # vpc_cidr の下半分を AZ 数で等分（2 AZ→/18, 4 AZ→/19）
  public_subnets  = local.public_subnets  # vpc_cidr の上半分から AZ ごとに /24 を導出

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

ここで `azs` / `private_subnets` / `public_subnets` に渡している 3 つの `local.*` が、この構成の設計上の肝です。いずれも [`az.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/az.tf) で `var.region` と `var.vpc_cidr` から自動導出しており、通常のデプロイでは AZ もサブネット CIDR も一切手書きしません。tfvars に書くのは `region` とプールのインスタンスタイプだけで済みます。読みどころは AZ とサブネットの自動導出・`single_nat_gateway`・`private_subnet_tags` の 3 点です。

**AZ とサブネット CIDR の自動導出**: `local.azs` は `var.azs` が `null`（既定）ならそのリージョンの標準 AZ（Local Zone や Wavelength を除く、`opt-in-not-required` の AZ）を `sort` して全件返します。`us-west-2` や `us-east-2` なら 4 つの AZ すべてに VPC がまたがります。サブネット CIDR も `var.private_subnet_cidrs` / `var.public_subnet_cidrs` が `null`（既定）なら `var.vpc_cidr` から AZ ごとに 1 つずつ切り出します。プライベートは VPC の下半分（`/16` なら `10.0.0.0/17`）を AZ 数で等分し、パブリックは上半分から `/24` を AZ ごとに取ります。AZ が 2 つならプライベートは `/18`、4 つなら `/19`、というように AZ 数に追随してサイズが決まるため、サブネット一覧が AZ 一覧と食い違う余地がありません。この「プライベートは大きく、パブリックは小さく」という非対称な配分が分散 AI 向けの肝です。GPU ノードが載るプライベートサブネットは、Pod ごとに VPC の IP を消費する VPC CNI のために大きく確保し、NAT ゲートウェイやロードバランサーしか置かないパブリックサブネットは小さくて足ります（この IP 消費の仕組みと、EFA が IP を食わない理由は末尾の「注意」節で詳しく説明します）。特定の AZ 集合に固定したい、あるいは CIDR を明示指定したい場合だけ `var.azs` / `var.private_subnet_cidrs` / `var.public_subnet_cidrs` で上書きでき、その際は解決後の AZ ごとにちょうど 1 つずつ CIDR を与えます（過不足は `az.tf` の precondition が検出します）。

**`single_nat_gateway = true` の意味**: NAT ゲートウェイを AZ ごとに作らず 1 つだけにしています。本番の可用性設計では、AZ 障害の影響を切り離すためと、AZ をまたぐ転送料金を避けるために AZ ごとに NAT を置きます。ところがこの基盤では、EFA の集団通信も FSx for Lustre も Capacity Block も単一 AZ が前提で、性能を出したいワークロードは自然と 1 つの AZ に集まります。そのため計算そのものが単一 AZ に寄っており、その AZ に NAT を同居させる限り、AZ ごとに NAT を作る利点は薄いです。ただし NAT がどの AZ に作られるかは意識しておく必要があります。`terraform-aws-modules/vpc` の `single_nat_gateway = true` は先頭のパブリックサブネット、つまり `local.azs` の先頭 AZ に NAT を 1 つ作ります。オンデマンド/スポットのプールは `zone` を書かなければこの先頭 AZ に導出されるため、NAT と同じ AZ に載り外向き通信は AZ をまたぎません。一方 Capacity Block のプールは予約が確保された AZ に固定されるので、それが先頭 AZ と異なると外向き通信（イメージ pull など）が AZ をまたぎます。この基盤は「予約が動いても VPC がリージョン全 AZ をまたぐので必ず対応するサブネットがある」ことを優先して単一 NAT を許容しています。ここで一点はっきりさせておくと、NAT は CB の AZ に自動で追従しません。`local.azs` は既定でリージョンの AZ 名を単純にソートした並びで、その先頭（通常は `...a`）に NAT が固定されます。CB がどの AZ に確保されても、また確保先が次回の予約で変わっても、NAT の位置は変わりません。したがって CB のプールと NAT を同じ AZ にそろえたい場合は、クラスタを構築する際の `var.azs` に CB の AZ を先頭にして並べます（例: CB が `us-east-2c` なら `azs = ["us-east-2c", "us-east-2a", ...]`）。そうすれば `azs[0]` が CB の AZ になり、NAT もそこに作られます。これはあくまで最初の `terraform apply` 時に決める設定で、稼働後に CB が別 AZ へ動いても NAT が引っ越すわけではない点に注意してください。揃えなかった場合でも動作はし、CB プールの外向き通信（イメージ pull など）が先頭 AZ の NAT を経由して AZ をまたぐだけです。プライベートサブネットからの外向き通信はこの単一 NAT を経由し、Amazon S3 は Gateway エンドポイント、Amazon ECR などは Interface エンドポイントを併用して NAT 経由の通信量そのものを減らしています。

**`private_subnet_tags` の `karpenter.sh/discovery`**: このタグが後の章で効いてきます。Karpenter は「ノードを起動してよいサブネット」をこのタグで検出します。ここで**プライベートサブネットにだけ**タグを付け、`public_subnet_tags` には付けていない点が重要です。もし共通の `tags` に含めてしまうと全サブネットに伝搬してパブリックサブネットにも付き、Karpenter がそこにノードを立ててしまいます。この構成はパブリックサブネットにパブリック IP を自動付与しない設定なので、そこに立ったノードは外向きの到達経路を持たず、`nodeadm` によるクラスタ参加に失敗します。

## Amazon EKS クラスタと System ノードグループ

Amazon EKS 本体は [`eks.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/eks.tf) で `terraform-aws-modules/eks/aws` モジュールを使って作ります。アドオンと System ノードグループの定義が読みどころです。

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

**`before_compute = true` の 2 つのアドオン**: `vpc-cni` と `eks-pod-identity-agent` にこのフラグを付け、ワーカーノードが起動する前にこれらを導入します。特に Pod Identity Agent は、Pod Identity で AWS 権限を得るコントローラ（Karpenter など）より先に存在していないと、それらが起動時に認証情報を取得できずクラッシュします。そのため順序を保証するためのフラグです。

**System ノードグループの `karpenter.sh/controller` ラベル**: 規定では m5 系インスタンスを 2 台を固定起動します。このノードグループは Karpenter が管理するのではなく、Amazon EKS Managed Node Group として常時稼働させます。`karpenter.sh/controller: "true"` というラベルを付けているのは、本章の apply で導入される Karpenter コントローラ自身をこのノードに載せるためです。Karpenter コントローラを Karpenter 管理下のノードに載せるのは前述のとおり非推奨なので、Karpenter を動かす最初の足場として、Karpenter の管理外のノードグループが必要になります。

## Pod Identity による認証

このモジュールでは、コントローラが AWS API を呼ぶための権限を **Pod Identity** で付与します。従来の IRSA（IAM Roles for Service Accounts）ではなく Pod Identity を選んでいる点が特徴です。[`iam.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/iam.tf) で Karpenter 用の IAM ロールと Pod Identity Association を作ります。

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

IRSA は ServiceAccount にアノテーションで IAM ロールを結び付け、OIDC プロバイダ経由で認証する方式です。これに対し Pod Identity は、IAM 側の Pod Identity Association だけで ServiceAccount とロールを結び付けられ、Kubernetes マニフェスト側にアノテーションを書かずに済みます。設定が IAM 側で完結するぶんシンプルで、この構成では Karpenter・EBS/Amazon FSx（OpenZFS・Lustre）/Amazon EFS の各 CSI ドライバすべてを Pod Identity で統一しています。本章の `eks-pod-identity-agent` アドオンは、この Pod Identity を各 Pod で機能させるためのエージェントです。

`enable_inline_policy = true` にも実務上の理由があります。Karpenter v1 のコントローラポリシーは、AWS のマネージドポリシーのサイズ上限（空白を除いて 6,144 文字、変更不可）をわずかに超え、`LimitExceeded: PolicySize: 6144` で失敗します。ロールに直接付けるインラインポリシー（上限 10,240 文字）にすれば同じ権限のまま上限に収まるため、この構成ではインラインを選んでいます。

## インフラ層が恒久管理するもの、しないもの

本章では貫いている 1 つの原則があります。クラスタスコープで複数のワークロードが共有し、消えると学習/推論 Pod が動かなくなるもの、具体的には CSI ドライバ・Kubeflow Training Operator・Karpenter コントローラ・共有ストレージの静的 PV は、Pod と同じ寿命で作っては消すのではなく、クラスタの基盤として Terraform が恒久管理します。一方、namespace や PVC、実際に流す学習 Job そのもののように、特定のワークロードと運命を共にする namespace スコープの資材は、ワークショップ側で `kubectl` や Helm で作ります。

この原則からいくつかの設計判断をしました。

- **ストレージは CSI ドライバとファイルシステム本体を分離します**: ドライバは実行前提なので基盤層に属し、EBS・FSx（OpenZFS・Lustre）・EFS のいずれも無条件で常設します。ファイルシステム本体と静的 PV の作成はワークロード側の選択なので、`openzfs_enabled` などのフラグで制御します。既定では OpenZFS と Lustre を作り、EFS は本体を作らずドライバだけ常設します。この分離により、あとで EFS が要るときはアドオン導入からではなくファイルシステムを 1 つ足すだけで済みます。
- **共有ストレージの既定は単一 AZ の FSx です**: FSx for OpenZFS には Multi-AZ 構成も選べますがこの基盤では意図的に単一 AZ を既定にしています。理由は EFA・FSx for Lustre・Capacity Block を前提とする学習系ワークロードでは計算が 1 つの AZ に寄るため、ストレージだけをマルチ AZ にしても可用性は活きず、単価だけが上がります。AZ 障害に備えた成果物の長期保全は、ストレージのマルチ AZ 化ではなくチェックポイントの Amazon S3 退避で担うのが実務の定石です。逆に、可用性のためマルチ AZ 配置が定石になる推論サービングや、AZ をまたいでキャッシュを共有したい特定用途のためには、EFS を opt-in の選択肢として残しています。なお本章の Amazon VPC を複数 AZ で張るのは Amazon EKS コントロールプレーンが 2 AZ 以上のサブネットを要求するためと、Capacity Block がどの AZ に落ちても対応するサブネットが必ずあるようにするためで、計算を複数 AZ に分散させるためではありません。アクセラレータプールはそれぞれ単一 AZ に固定します。
- **Argo CD などの CD 機構は常設しません**: この基盤で流すワークロードは、適用しては消す使い捨ての実験カタログで、継続的に同期し続けるべき長命なデプロイ対象がありません。実行前提はすでに Terraform 側に揃っているため、残る作業は Helm でレンダリングして `kubectl apply` するだけで足り、release 履歴や drift 検出を担う CD コンポーネントは基盤層に要りません。CD 機構が無いのは欠落ではなく、この使い方から導かれる設計判断で、GitOps を継続運用したい場合はこの基盤の上に利用者が追加する層になります。

# ワークショップ実施

## 1. リポジトリを clone して tfvars を準備する

まず、この book が対象とするリポジトリを clone します。以降の章もこの作業ディレクトリを前提に進めます。

```bash
git clone https://github.com/littlemex/distributed-ai.git
cd distributed-ai/infra/eks
```

続いて `terraform.tfvars.example` を `terraform.tfvars` にコピーし、`region` と `cluster_name` を自分の環境に合わせて設定します。AZ もサブネット CIDR も `region` から自動導出されるので、この段階で書くのはこの 2 つだけです。`accelerator_pools` も空のままで構いません。名前付き profile（AWS SSO や assume-role）で認証している場合は、`aws_profile` にその profile 名も設定します。この値を設定しておくと、Terraform が aws/helm/kubectl の各 provider と CLI ヘルパーすべてに同じ profile を渡すため、以降の操作が同一プリンシパルで実行されます。あわせて `expected_account_id` にデプロイ先の 12 桁のアカウント ID を設定しておくことを強く推奨します。この値を設定すると、認証情報が別のアカウントを指したまま apply しようとしたときに plan の段階で停止するため、profile の取り違えでクラスタを別アカウントに作ってしまう事故を未然に防げます。自分のアカウント ID は `aws sts get-caller-identity --query Account --output text` で確認できます。

```bash
cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars を編集: region, cluster_name, expected_account_id
# （名前付き profile なら aws_profile も）
```

:::message
既定では AZ もサブネット CIDR も `region` と `vpc_cidr` から自動導出されるため、通常は tfvars で AZ やサブネットに手を加える必要はありません。AZ や CIDR を明示指定して上書きする場合だけ、解決後の AZ ごとにちょうど 1 つずつ CIDR を与えてください。長さが食い違うとサブネットを持たない AZ が生まれ、そこにアクセラレータプールを割り当てると Pod が永久に `Pending` になります。この不整合は `az.tf` の precondition（サブネット数と AZ 数の一致、各プールの解決後 AZ が VPC の AZ に含まれること、など）が plan 時に検出して apply を止めます。
:::

## 2. apply する

apply の前に、Terraform が実際にどのプリンシパルで認証するかを必ず確認します。ここを取り違えると、同じ `cluster_name` でも意図しないアカウントに二重にクラスタを作りかけたり、既存クラスタの一部リソースを作り直そうとして `EntityAlreadyExists` で apply が途中失敗したりします。tfvars に `aws_profile` を設定した場合、Terraform は環境変数 `AWS_PROFILE` より tfvars の値を優先するため、確認コマンドにも同じ profile を明示的に渡します。

```bash
# tfvars の aws_profile と同じ profile を明示して、Terraform が使うプリンシパルを確認します
aws sts get-caller-identity --profile <tfvars と同じ profile>   # Account と ARN が意図どおりか確認
terraform init
terraform apply
```

:::message alert
`terraform apply` は state に記録されたリソースだけを管理し、state に無いリソースが AWS 側に存在するかどうかは確認しません。このため profile の取り違え方によって 2 種類の事故が起きます。1 つ目は、state が空（またはそのリソースを未追跡）のまま、既にリソースが存在するアカウントに profile が向くケースです。IAM ロール・KMS エイリアス・CloudWatch ロググループのように名前に一意制約があるリソースは `EntityAlreadyExists` で失敗し、FSx ファイルシステムのように名前の一意制約が無いリソースはエラーにならず二重作成されて課金が始まります（apply が途中で失敗しても、並行して作成が始まった FSx はそのまま完成まで走り切ります）。2 つ目は、state にリソースが記録済みのまま別アカウントに profile が向くケースで、この場合は Terraform が「管理下のリソースがすべて消えた」と判断し、エラーも出さずに丸ごと作り直します。後者はエラーで止まらないぶん気づきにくく、より危険です。重複作成された FSx はコンソールで `fs-` から始まる ID を確認して手動で削除しない限り課金が続くため、apply の前に必ず `aws sts get-caller-identity` で Account と ARN を確認してください。step 1 で `expected_account_id` を設定しておけば、認証情報が別アカウントを指したまま apply しようとしても plan の段階で停止するので、この事故そのものを起こさせない歯止めになります。
:::

`terraform apply` は、Amazon VPC・NAT ゲートウェイ・Amazon EKS コントロールプレーン・System ノードグループに加えて、その上で動く Karpenter コントローラ・各 CSI ドライバ・Kubeflow Training Operator・共有ストレージ（既定の FSx）まで、クラスタスコープの基盤を一度に作ります。`accelerator_pools` が空なので GPU/Neuron ノードだけは立ちません。所要時間が大きいのはコントロールプレーンの起動と FSx ファイルシステム（特に FSx for Lustre）の作成で、いずれも単独で 10〜15 分級です。両者は VPC さえできれば並行して作られるため単純な足し算にはなりませんが、それでも全体では 20〜30 分程度を見ておくと安全です。

:::message
`terraform apply` は 20〜30 分ほどかかります（コントロールプレーンと FSx の作成が支配的です）。コントロールプレーンが `ACTIVE` になり、FSx ファイルシステムが `AVAILABLE` になるまで待ちましょう。
:::

## 3. kubeconfig を設定してノードを確認する

```bash
# terraform.tfvars に aws_profile を設定した場合は、同じ profile をここでも渡します
# （または事前に export AWS_PROFILE=<name>）。素の [default] で認証している場合は
# --profile を省略します。
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" \
  --region <region> --profile <tfvars と同じ profile>
kubectl get nodes
```

`kubectl get nodes` で m5 系のノードが 2 台 `Ready` 状態で表示されれば、System ノードグループの起動は成功です。

:::message alert
`kubectl` が `Unauthorized`（`error: You must be logged in to the server`）で弾かれる場合、原因はほぼ 2 つです。1 つ目は、`terraform apply` を実行したプリンシパルと `kubectl` を実行するプリンシパルが食い違っているケースです。`enable_cluster_creator_admin_permissions = true` はクラスタを作成したプリンシパルにだけ管理者権限を与えるため、`apply` を名前付き profile（AWS SSO や assume-role）で実行したのに `update-kubeconfig` を素の `[default]` で叩くと、両者が別プリンシパルになり弾かれます。`aws sts get-caller-identity` で両者のプリンシパルを確認します。assume-role の場合はセッション名部分が違っていても問題なく、`assumed-role/<ロール名>` までが一致していれば認証は通ります（アクセスエントリは基底の IAM ロール ARN 単位でマッチするためです）。一致していなければ `update-kubeconfig` に `apply` と同じ `--profile` を渡す（または `export AWS_PROFILE`）と解消します。自分のロールが登録済みかは `aws eks list-access-entries --cluster-name <name>` でも確認できます。2 つ目は、`apply` 直後にアクセスエントリがまだ認証レイヤに伝播していないケースで、この場合は 1〜2 分待って再実行すれば通ります。
:::

## 4. 作業用の namespace を作る

この book のワークショップ（Basic02 以降）では、学習 Job や推論サーバーなどのワークロードを `default` ではなく専用の namespace に作ります。あとで「この namespace ごと消せば実験の後片付けが済む」ようにするためです。本 book では作業用 namespace を `distai` に統一して進めます。

以降の各章のコマンドはこの `NAMESPACE` 変数を前提にしているので、ターミナルを開き直したら都度この 2 行を実行してください。`kubectl create namespace` を `--dry-run` 経由の `apply` にしているのは、すでに存在していてもエラーにならないようにするため（冪等）です。

```bash
export NAMESPACE=distai
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
```

`namespace/distai created`（初回）または `namespace/distai unchanged`（2 回目以降）と表示されれば準備完了です。本 book では最後まで同じ `distai` を使います。

## 5. context を確認する習慣をつける

```bash
kubectl config current-context
```

今の時点では地味に見えますが、操作対象のクラスタやリソースが増える後続の章で事故を防ぐための習慣として、ここで身につけておきます。マルチクラスタ環境では、別クラスタ用に context を切り替えたまま元のつもりで操作してしまう事故が起きやすいため、破壊的な操作の前には必ずこのコマンドで対象クラスタを確認します。

## 6. (任意) スモークテストで動作確認する

リポジトリの [`infra/eks/tests/`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/tests) にインフラ層のスモークテストが用意されています。実行は任意ですが、初回構築後やモジュール変更後に回すと「apply は通ったが何かが壊れている」を早期に検出できます。基盤テストは GPU ノードを起動せず約 1 分で完了します。

```bash
# ここまでの手順で infra/eks にいる前提です（別の場所にいる場合は cd <repo>/infra/eks）
cd tests

# 基盤テスト: control-plane / system-nodes / karpenter / training-operator /
#            csi-drivers / storage-mount (FSx read/write)
./run-tests.sh --profile <tfvars と同じ profile>

# GPU テストも含める場合 (Karpenter がノードを起動するため 5-10 分)
./run-tests.sh --with-gpu --profile <tfvars と同じ profile> --gpu-count 4
```

基盤テストが全 PASS であれば、Karpenter・CSI ドライバ・Training Operator・共有ストレージが正常に機能しており、Basic02 以降のワークショップに進む準備ができています。GPU テストの `--gpu-count` には NodePool のインスタンスタイプが持つ GPU 枚数を渡します（g6e.12xlarge なら 4、p5en.48xlarge なら 8）。GPU テストで ICE（InsufficientInstanceCapacity）により起動できない場合は AWS 側のキャパシティ問題であり、インフラの不具合ではありません。

# まとめ

本章では、分散 AI の実験を回すための土台として Amazon EKS クラスタを構築し、以降のワークショップで使う作業用 namespace `distai` を作成しました。中核として作ったのは Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループの 3 つで、同じ apply で載る Karpenter を Basic03 で掘り下げ、Basic04 以降でアクセラレータプールを積み上げていきます。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
