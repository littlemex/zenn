---
title: "Basic01 - Amazon EKS 基盤を立てる"
free: true
---

本章では、分散学習・推論の実験を回すための土台として Amazon EKS クラスタを構築します。

Terraform で Amazon VPC・Amazon EKS コントロールプレーン・Karpenter を動かすための System ノードグループをデプロイし、`kubectl` でノードが見えるところまでを扱います。

:::message alert
本資料は `us-east-2` リージョンを例に説明します。実際には自身で選択したリージョンに読み替えて進めてください。コマンド中の `<region>` などのプレースホルダは自分の値に置き換えます。
:::

# 解説

## 全体構成

この book 全体で構築する分散 AI 基盤の全体像です。Amazon VPC は複数の AZ にまたがって張り、Amazon EKS コントロールプレーンの下で Karpenter が GPU/Neuron の各 NodePool を要求に応じて起動します。共有ストレージ（既定は単一 AZ の Amazon FSx for OpenZFS と FSx for Lustre）や Capacity Block の期限監視（Amazon EventBridge → Amazon SNS）といった周辺サービスも含めた構成です。各コンポーネントは以降の章で 1 つずつ扱います。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章の `terraform apply` は、この基盤のクラスタスコープの土台を一度に立ち上げます。図のうち中核となる **Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループ** に加え、その上で動く Karpenter コントローラ・各 CSI ドライバ・Kubeflow Training Operator・共有ストレージ（既定の FSx）まで、後続の章で使う基盤コンポーネントが同じ apply で揃います。本章で詳しく解説するのは中核の 3 つで、残りは各コンポーネントの章で 1 つずつ扱います。

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
  single_nat_gateway     = false
  one_nat_gateway_per_az = true      # NAT を AZ ごとに 1 つ置きます

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

ここで `azs` / `private_subnets` / `public_subnets` に渡している 3 つの `local.*` が、この構成の設計上の肝です。いずれも [`az.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/az.tf) で `var.region` と `var.vpc_cidr` から自動導出しており、通常のデプロイでは AZ もサブネット CIDR も一切手書きしません。tfvars に書くのは `region` とプールのインスタンスタイプだけで済みます。

**AZ とサブネット CIDR の自動導出**: `local.azs` は `var.azs` が `null`（既定）ならそのリージョンの標準 AZ を `sort` して全件返します。`us-east-2` なら 3 つ、`us-west-2` なら 4 つの AZ すべてに VPC がまたがります（AZ 数はリージョンによって違うので、`aws ec2 describe-availability-zones --region <region> --filters Name=opt-in-status,Values=opt-in-not-required` で確認できます）。サブネット CIDR も `var.private_subnet_cidrs` / `var.public_subnet_cidrs` が `null`（既定）なら `var.vpc_cidr` から AZ ごとに 1 つずつ切り出します。デフォルトではうまく AZ を適切な CIDR で切ってくれていると思っていただければ大丈夫です。

**`one_nat_gateway_per_az = true` の意味**: NAT ゲートウェイを AZ ごとに 1 つ置き、各 AZ のプライベートルートテーブルはその AZ 自身の NAT を向きます。VPC は `local.azs` の全 AZ にまたがるので、2 AZ なら NAT も 2 つ、3 AZ なら 3 つ作られます。

当初この基盤は `single_nat_gateway = true` で単一 NAT にしていました。計算が単一 AZ に寄る（EFA の集団通信も FSx for Lustre も Capacity Block も単一 AZ 前提）ので AZ ごとに NAT を作る利点は薄い、という判断です。これを AZ ごとに変えたのは、単一 NAT がその AZ 単位の単一障害点になり、影響範囲が「その AZ のワークロード」ではなく**クラスタ全体のイメージ pull** に及ぶためです。プライベートサブネットのノードは外向き通信を NAT に依存しており、NAT を置いた AZ が劣化すると、他の AZ のノードもレジストリに到達できなくなります。NAT の時間課金は 1 つあたり `$0.045/h`（us-east-2）で、同リージョンの `p4d.24xlarge` オンデマンド `$21.96/h` に対して桁が 3 つ違います。AZ 障害の切り離しを買う対価としては無視できる差です。

もう一つの理由は、単一 NAT だと**別 AZ のノードの外向き通信が AZ をまたぐ**点です。NAT がある AZ 以外で起動したノードのトラフィックは AZ 間転送を経由するため、レイテンシと AZ 間転送料金が乗ります。Capacity Block はどの AZ に落ちるか事前に決められず、この基盤の VPC がリージョンの全 AZ にまたがるのはまさにそのためなので、「計算がどの AZ に来ても、その AZ に NAT がある」状態を既定にしておく方が構成として素直です。

なお、外向き通信は NAT だけに依存しているわけではありません。ECR・Amazon EC2・STS・SSM・CloudWatch Logs・EKS Auth は [`vpc-endpoints.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/vpc-endpoints.tf) の VPC endpoint 経由で NAT を通らずに到達します。NAT が担うのは `nvcr.io` や `quay.io`、`registry.k8s.io` といった ECR 以外のレジストリと、Interface endpoint を持たない IAM です。endpoint で NAT 依存を切る設計は Basic11、イメージ pull の経路そのものは Advanced01 で扱います。

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

**`before_compute = true` の 2 つのアドオン**: `vpc-cni` と `eks-pod-identity-agent` にこのフラグを付け、ワーカーノードが起動する前にアドオンを導入します。特に Pod Identity Agent は、Pod Identity で AWS 権限を得るコントローラ（Karpenter など）より先に存在していないと、それらが起動時に認証情報を取得できずクラッシュします。そのため順序を保証するためのフラグです。

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

`terraform apply` は、Amazon VPC・AZ ごとの NAT ゲートウェイ・Amazon EKS コントロールプレーン・System ノードグループに加えて、その上で動く Karpenter コントローラ・各 CSI ドライバ・Kubeflow Training Operator・共有ストレージ（既定の FSx）まで、クラスタスコープの基盤を一度に作ります。`accelerator_pools` が空なので GPU/Neuron ノードだけは立ちません。所要時間が大きいのはコントロールプレーンの起動と FSx ファイルシステム（特に FSx for Lustre）の作成で、いずれも単独で 10〜15 分級です。両者は VPC さえできれば並行して作られるため単純な足し算にはなりませんが、それでも全体では 20〜30 分程度を見ておくと安全です。

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

あわせて NAT が AZ ごとに分かれていることも確認しておきます。プライベートルートテーブルの `0.0.0.0/0` が、それぞれ別の NAT を向いているのが期待する状態です。

```bash
aws ec2 describe-route-tables --region <region> \
  --filters "Name=vpc-id,Values=$(terraform output -raw vpc_id)" "Name=tag:Name,Values=*private*" \
  --query 'RouteTables[].[Tags[?Key==`Name`]|[0].Value,Routes[?DestinationCidrBlock==`0.0.0.0/0`]|[0].NatGatewayId]' \
  --output text
```

実機出力は次のようになります（2 AZ 構成の例で、AZ ごとにルートテーブルと NAT が 1 対 1 に対応しています）。

```text
<cluster>-private-<region>a	nat-0ea8dxxxxxxxxxxxx
<cluster>-private-<region>b	nat-09c50xxxxxxxxxxxx
```

`<cluster>-private` という AZ 名の付かない行が 1 つだけ返り、NAT も 1 つしか出ない場合は単一 NAT の構成です。`single_nat_gateway` / `one_nat_gateway_per_az` の値を確認してください。

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

# 基盤テスト: control-plane / system-nodes / karpenter / trainer /
#            csi-drivers / device-plugins / storage-mount (FSx read/write)
./run-tests.sh --profile <tfvars と同じ profile>
```

クラスタ名とリージョンは `terraform output` から自動で解決されるので指定は不要です（`--cluster-name` / `--region` で明示的に上書きすることもできます）。実機出力は次のようになります。

```text
STATUS   TEST                                DETAIL
--------------------------------------------------------------
PASS     control-plane                       6s
PASS     system-nodes                        6s
PASS     karpenter                           10s
PASS     trainer                             6s
PASS     csi-drivers                         44s
PASS     device-plugins                      8s
PASS     storage-mount                       54s
--------------------------------------------------------------
PASS: 7  FAIL: 0  SKIP: 0  TOTAL: 7
```

全 PASS であれば、Karpenter・CSI ドライバ・Kubeflow Trainer・共有ストレージが正常に機能しており、Basic02 以降のワークショップに進む準備ができています。`device-plugins` は GPU/EFA/Neuron の device plugin の DaemonSet を見る項目で、該当プールが無い段階では対象が存在しないため「該当なし」として PASS します。

GPU テストは Basic04 でアクセラレータプールを定義した後に実行できます。

```bash
# Karpenter が GPU ノードを起動するため 5-10 分かかります
./run-tests.sh --with-gpu --profile <tfvars と同じ profile> --gpu-count 1
```

対象の NodePool は cpu 以外の NodePool から自動選択されます（`--gpu-nodepool` で明示指定も可能）。`--gpu-count` には検証したい GPU 枚数を渡します（g6.2xlarge なら 1、g6e.12xlarge なら 4、p4d.24xlarge なら 8）。GPU テストで ICE（InsufficientInstanceCapacity）により起動できない場合は AWS 側のキャパシティ問題であり、インフラの不具合ではありません。

# まとめ

本章では、分散 AI の実験を回すための土台として Amazon EKS クラスタを構築し、以降のワークショップで使う作業用 namespace `distai` を作成しました。中核として作ったのは Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループの 3 つで、同じ apply で載る Karpenter を Basic03 で掘り下げ、Basic04 以降でアクセラレータプールを積み上げていきます。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
