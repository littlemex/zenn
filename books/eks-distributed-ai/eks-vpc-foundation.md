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

この book 全体で構築する分散 AI 基盤の全体像です。Amazon VPC は複数の AZ にまたがって張り、Amazon EKS コントロールプレーンの下で Karpenter が GPU/Neuron の各 NodePool を要求に応じて起動します。共有ストレージ（既定は単一 AZ の Amazon FSx for OpenZFS と FSx for Lustre）や Capacity Block の期限監視といった周辺サービスも含めた構成です。各コンポーネントは以降の章で 1 つずつ扱います。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章の `terraform apply` は、この基盤のクラスタスコープの土台を一度に立ち上げます。図のうち中核となる **Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループ** に加え、その上で動く Karpenter コントローラ・各 CSI ドライバ・Kubeflow Trainer・共有ストレージまで、後続の章で使う基盤コンポーネントが同じ apply で揃います。本章で詳しく解説するのは中核の 3 つで、残りは各コンポーネントの章で 1 つずつ扱います。

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

**AZ とサブネット CIDR の自動導出**: `local.azs` は `var.azs` が `null`（既定）ならそのリージョンの標準 AZ を `sort` して全件返します。サブネット CIDR も `var.private_subnet_cidrs` / `var.public_subnet_cidrs` が `null`（既定）なら `var.vpc_cidr` から AZ ごとに 1 つずつ切り出します。デフォルトではうまく AZ を適切な CIDR で切ってくれていると思っていただければ大丈夫です。

**`one_nat_gateway_per_az = true` の意味**: NAT ゲートウェイを AZ ごとに 1 つ置き、各 AZ のプライベートルートテーブルはその AZ 自身の NAT を向きます。VPC は `local.azs` の全 AZ にまたがるので、2 AZ なら NAT も 2 つ、3 AZ なら 3 つ作られます。既定では `local.azs` がリージョンの全標準 AZ を返すため、AZ 数が多いリージョン（us-east-1 は標準 AZ が 6 つ）では NAT ゲートウェイと Elastic IP がその数だけ作られます。Elastic IP のデフォルトのクォータはリージョンあたり 5 つなので、AZ 数の多いリージョンに読み替える場合は、クォータの引き上げか `var.azs` での AZ 数の絞り込みが必要になることがあります。

当初この基盤は `single_nat_gateway = true` で単一 NAT にしていましたが、Capacity Block for ML がどの AZ でも使いうることを考えると、全ての AZ に NAT を置く方が良いと判断しました。単一 NAT だと他 AZ のノードからの外向き通信がすべて AZ をまたいでその NAT に集まり、クロス AZ のデータ転送料金がかかります。AZ ごとに NAT を置けばこの転送料金を避けられ、かつ 1 つの AZ の NAT が落ちても他 AZ の外向き通信が生き残る耐障害性の利点もあります。ただしこれはどちらでも良いと思います。

なお、外向き通信は NAT だけに依存しているわけではありません。ECR・Amazon EC2・STS・SSM・CloudWatch Logs・EKS Auth は [`vpc-endpoints.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/vpc-endpoints.tf) の Interface endpoint 経由で、Amazon S3 は Gateway endpoint 経由で、いずれも NAT を通らずに到達します。ECR のイメージ pull はレイヤの実体を S3 から取得するため、この S3 Gateway endpoint も合わせて必要です。NAT が主に担うのは `nvcr.io` や `quay.io`、`registry.k8s.io` といった ECR 以外のレジストリと、Interface endpoint を持たない IAM です。

**`private_subnet_tags` の `karpenter.sh/discovery`**: このタグが後の章で効いてきます。Karpenter は「ノードを起動してよいサブネット」をこのタグで検出します。ここで**プライベートサブネットにだけ**タグを付け、`public_subnet_tags` には付けていない点が重要です。もし共通の `tags` に含めてしまうと全サブネットに伝搬してパブリックサブネットにも付き、Karpenter がそこにノードを立ててしまいます。この構成はパブリックサブネットにパブリック IP を自動付与しない設定なので、そこに立ったノードは外向きの到達経路を持たず、`nodeadm` によるクラスタ参加に失敗します。

::::details nodeadm とは

`nodeadm` は、EKS のノードとなる EC2 インスタンスを Kubernetes クラスタへ参加させるためのブートストラップ CLI です。Amazon Linux 2023 ベースの EKS 最適化 AMI に標準で同梱されており、ブート時に kubelet と containerd を構成してコントロールプレーンへ join させる役割を担います。

## 旧方式からの変化

Amazon Linux 2 の時代は `/etc/eks/bootstrap.sh` というシェルスクリプトを user data から呼び出し、クラスタ名やフラグを引数として渡していました。AL2023 ではこれが `nodeadm` に置き換わり、YAML による宣言的な設定へと移行しています。設定オブジェクトは `NodeConfig` と呼びます。

## NodeConfig の書き方

nodeadm は user data から `NodeConfig` を読み取ります。素の YAML ドキュメント単体で渡すこともできますし、cloud-init と併用する場合は MIME マルチパートの 1 パート（`Content-Type: application/node.eks.aws`）として渡します。

```yaml
---
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  cluster:
    name: distai-eks
    apiServerEndpoint: https://XXXXXXXX.gr7.us-east-2.eks.amazonaws.com
    certificateAuthority: <base64-encoded-ca>
    # Service CIDR（VPC の CIDR ではない点に注意）
    cidr: 172.20.0.0/16
  kubelet:
    config:
      maxPods: 110
    flags:
      - "--node-labels=role=gpu"
```

`spec.cluster` にクラスタ接続情報を、`spec.kubelet` に kubelet の設定やフラグを記述します。ただし `spec.cluster` をフルに書くのは、セルフマネージドノードやカスタム AMI を使う場合です。標準 AMI のマネージドノードグループでは、EKS 側がクラスタ接続情報を含む `NodeConfig` を自動的に注入するため、利用者が書くのはカスタマイズしたい差分（kubelet 設定など）だけで済みます。

## 主なサブコマンド

- `nodeadm init` はノードを初期化してクラスタへ join します。user data 内のスクリプトとして動くのではなく、AMI に組み込まれた systemd ユニット（`nodeadm-config.service` / `nodeadm-run.service`）としてブート時に起動し、IMDS 経由で user data の `NodeConfig` を読み取って実行します。
- `nodeadm config check` は `NodeConfig` の内容を検証します。
- `nodeadm debug` は join に失敗した際の診断を行います。

## Karpenter との関係

Karpenter で AL2023 AMI を使う場合も、ノードの user data は nodeadm の `NodeConfig` 形式になります。`EC2NodeClass` の `spec.userData` に書いた内容は、Karpenter が生成する `NodeConfig` パートの前段に置かれ、起動時に nodeadm が複数の `NodeConfig` を結合します。結合は後勝ちのため、クラスタ接続情報や Karpenter が付与するラベル（`karpenter.sh/nodepool` など）は利用者側では上書きできません。「userData に書けば何でも効く」わけではない点に注意してください。そのため、ノードラベルは userData ではなく NodePool の `spec.template.metadata.labels` で付与します。

::::

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
      instance_types = var.system_node_instance_types   # 既定 m5.xlarge
      disk_size      = var.system_node_volume_size      # 既定 50 (GiB)
      min_size       = var.system_node_desired_size     # 既定 2
      max_size       = var.system_node_desired_size
      desired_size   = var.system_node_desired_size
      labels = {
        "karpenter.sh/controller" = "true"
        "node-role"               = "system"
      }
    }
  }
}
```

**`before_compute = true` の 2 つのアドオン**: `vpc-cni` と `eks-pod-identity-agent` にこのフラグを付け、ワーカーノードが起動する前にアドオンを導入します。特に Pod Identity Agent は、Pod Identity で AWS 権限を得るコントローラ（Karpenter など）より先に存在していないと、それらが起動時に認証情報を取得できずクラッシュします。そのため順序を保証するためのフラグです。

**System ノードグループの `karpenter.sh/controller` ラベル**: 規定では m5 系インスタンスを 2 台を固定起動します。このノードグループは Karpenter が管理するのではなく、Amazon EKS Managed Node Group として常時稼働させます。`karpenter.sh/controller: "true"` というラベルを付けているのは、本章の apply で導入される Karpenter コントローラ自身をこのノードに載せるためです。Karpenter コントローラを Karpenter 管理下のノードに載せると、コントローラが自分の載るノードを消してしまう自己依存に陥りかねず推奨されません。そのため Karpenter を動かす最初の足場として、Karpenter の管理外のノードグループが必要になります。もう 1 つの `node-role: system` は、後続の章で Karpenter の各プールが付ける `node-role=<プール名>` と同じキーです。ワークロードが「GPU でないノード」という消極的な条件で誤って system ノードに載るのを防ぎ、載せたい層を積極的に名指しできるようにしています。

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

IRSA は ServiceAccount にアノテーションで IAM ロールを結び付け、OIDC プロバイダ経由で認証する方式です。これに対し Pod Identity は、EKS の API リソースである Pod Identity Association だけで ServiceAccount とロールを結び付けられ、Kubernetes マニフェスト側にアノテーションを書かずに済みます（IAM 側で要るのはロールの信頼ポリシーで `pods.eks.amazonaws.com` を許可することだけです）。設定が AWS 側で完結するぶんシンプルで、この構成では Karpenter・EBS/Amazon FSx（OpenZFS・Lustre）/Amazon EFS の各 CSI ドライバすべてを Pod Identity で統一しています。本章の `eks-pod-identity-agent` アドオンは、この Pod Identity を各 Pod で機能させるためのエージェントです。

`enable_inline_policy = true` にも実務上の理由があります。Karpenter v1 のコントローラポリシーは、AWS のマネージドポリシーのサイズ上限（空白を除いて 6,144 文字、変更不可）をわずかに超え、`LimitExceeded: PolicySize: 6144` で失敗します。ロールに直接付けるインラインポリシー（上限 10,240 文字）にすれば同じ権限のまま上限に収まるため、この構成ではインラインを選んでいます。

## インフラ層が恒久管理するもの、しないもの

本章では貫いている 1 つの原則があります。クラスタスコープで複数のワークロードが共有し、消えると学習/推論 Pod が動かなくなるもの、具体的には CSI ドライバ・Kubeflow Trainer・Karpenter コントローラ・共有ストレージの静的 PV は、Pod と同じ寿命で作っては消すのではなく、クラスタの基盤として Terraform が恒久管理します。一方、namespace や PVC、実際に流す学習 Job そのもののように、特定のワークロードと運命を共にする namespace スコープの資材は、ワークショップ側で `kubectl` や Helm で作ります。

この原則からいくつかの設計判断をしました。

- **ストレージは CSI ドライバとファイルシステム本体を分離**: ドライバは実行前提なので基盤層に属し、EBS・FSx（OpenZFS・Lustre）・EFS のいずれも無条件で常設します。ファイルシステム本体と静的 PV の作成はワークロード側の選択なので、`openzfs_enabled` などのフラグで制御します。既定では OpenZFS と Lustre を作り、EFS は本体を作らずドライバだけ常設します。この分離により、あとで EFS が要るときはアドオン導入からではなくファイルシステムを 1 つ足すだけで済みます。
- **共有ストレージの既定は単一 AZ の FSx**: FSx for OpenZFS には Multi-AZ 構成も選べますがこの基盤では意図的に単一 AZ を既定にしています。理由は EFA・FSx for Lustre・Capacity Block を前提とする学習系ワークロードでは計算が 1 つの AZ に寄るため、ストレージだけをマルチ AZ にしてもジョブ継続の観点では可用性が活きにくく、コスト増に見合いにくいからです。どのストレージをどの用途に使い分けるか（学習と推論の違い、Multi-AZ 共有に EFS や Amazon S3 を選ぶ判断）は Advanced01「共有ストレージをマルチテナントで扱う」で扱います。なお本章の Amazon VPC を複数 AZ で張るのは Amazon EKS コントロールプレーンが 2 AZ 以上のサブネットを要求するためと、Capacity Block がどの AZ に落ちても対応するサブネットが必ずあるようにするためで、計算を複数 AZ に分散させるためではありません。

# ワークショップ実施

## 1. リポジトリを clone して tfvars を準備する

まず、この book が対象とするリポジトリを clone します。以降の章もこの作業ディレクトリを前提に進めます。

```bash
git clone --depth 1 --branch release/eks-distributed-ai/v0.0.1 \
  --filter=blob:none --sparse \
  https://github.com/littlemex/distributed-ai.git
cd distributed-ai
git sparse-checkout set infra
cd infra/eks
```

続いて `terraform.tfvars.example` を `terraform.tfvars` にコピーし、`region` と `cluster_name` を自分の環境に合わせて設定します。AZ もサブネット CIDR も `region` から自動導出されるので、この段階で書くのはこの 2 つだけです。`accelerator_pools` も空のままで構いません。名前付き profile（AWS SSO や assume-role）で認証している場合は、****`aws_profile` にその profile 名も設定**します。この値を設定しておくと、Terraform が aws/helm/kubectl の各 provider と CLI ヘルパーすべてに同じ profile を渡すため、以降の操作が同一プリンシパルで実行されます。あわせて `expected_account_id` にデプロイ先の 12 桁のアカウント ID を設定しておくことを強く推奨します。この値を設定すると、認証情報が別のアカウントを指したまま apply しようとしたときに plan の段階で停止するため、profile の取り違えでクラスタを別アカウントに作ってしまう事故を未然に防げます。自分のアカウント ID は `aws sts get-caller-identity --query Account --output text` で確認できます。

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
`terraform apply` は state に記録されたリソースだけを管理し、state に無いリソースが AWS 側に存在するかどうかは確認しません。このため profile の取り違え方によって 2 種類の事故が起きます。1 つ目は、state が空（またはそのリソースを未追跡）のまま、既にリソースが存在するアカウントに profile が向くケースです。IAM ロール・KMS エイリアス・CloudWatch ロググループのように名前に一意制約があるリソースは作成時のエラー（IAM なら `EntityAlreadyExists`、KMS エイリアスなら `AlreadyExistsException`、CloudWatch Logs なら `ResourceAlreadyExistsException`）で失敗し、FSx ファイルシステムのように名前の一意制約が無いリソースはエラーにならず二重作成されて課金が始まります（apply が途中で失敗しても、並行して作成が始まった FSx はそのまま完成まで走り切ります）。2 つ目は、state にリソースが記録済みのまま別アカウントに profile が向くケースで、この場合は Terraform が「管理下のリソースがすべて消えた」と判断し、エラーも出さずに丸ごと作り直します。後者はエラーで止まらないぶん気づきにくく、より危険です。重複作成された FSx はコンソールで `fs-` から始まる ID を確認して手動で削除しない限り課金が続くため、apply の前に必ず `aws sts get-caller-identity` で Account と ARN を確認してください。step 1 で `expected_account_id` を設定しておけば、認証情報が別アカウントを指したまま apply しようとしても plan の段階で停止するので、この事故そのものを起こさせない歯止めになります。
:::

`terraform apply` は、Amazon VPC・AZ ごとの NAT ゲートウェイ・Amazon EKS コントロールプレーン・System ノードグループに加えて、その上で動く Karpenter コントローラ・各 CSI ドライバ・Kubeflow Trainer・共有ストレージ（既定の FSx）まで、クラスタスコープの基盤を一度に作ります。アクセラレーターノードだけはまだ立ちません。所要時間が大きいのはコントロールプレーンの起動と FSx ファイルシステム（特に FSx for Lustre）の作成で、いずれも単独で 10〜15 分級です。両者は VPC さえできれば並行して作られるため単純な足し算にはなりませんが、それでも全体では 20〜30 分程度を見ておくと安全です。

:::message
`terraform apply` は 20〜30 分ほどかかります。コントロールプレーンが `ACTIVE` になり、FSx ファイルシステムが `AVAILABLE` になるまで待ちましょう。
:::

## 3. kubeconfig を設定してノードを確認する

```bash
# 自分の環境に合わせて設定してください
export REGION=xxx
export PROFILE=xxx

# --alias で kubeconfig の context 名を短い固定名（ここでは ws）にします。
# これを付けないと context 名がクラスタ ARN 全体になり、毎回打つのが長くなります。
# terraform.tfvars に aws_profile を設定した場合は、同じ profile をここでも渡します
# （または事前に export AWS_PROFILE=<name>）。素の [default] で認証している場合は
# --profile を省略します。
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" \
  --region $REGION --profile $PROFILE --alias ws
```

続いて、以降のコマンドが常にこのクラスタと作業用 namespace に向くよう、current-context をこのクラスタに切り替え、その context の既定 namespace を `distai` にします。こうしておくと、`--context` も `-n` も付けずに、常にこのクラスタの `distai` namespace を対象にできます。あわせて、以降は打鍵を減らすため `kubectl` を `k` と打てるようにエイリアスを張っておきます（context も namespace も kubeconfig 側に設定済みなので、エイリアスには何も埋め込みません）。

```bash
kubectl config use-context ws
kubectl config set-context --current --namespace=distai
alias k=kubectl
```

これで `k get po` は「ws クラスタの distai namespace の Pod」を対象にします（別の namespace を見たいときだけ `-n <name>` を明示します）。

```bash
k get nodes

# インスタンスタイプまで見たい場合
k get nodes -o custom-columns='NAME:.metadata.name,TYPE:.metadata.labels.node\.kubernetes\.io/instance-type,ROLE:.metadata.labels.node-role,CAP:.metadata.labels.karpenter\.sh/capacity-type'
```

`k get nodes` で m5 系のノードが 2 台 `Ready` 状態で表示されれば、System ノードグループの起動は成功です。

:::message alert
`kubectl` が `Unauthorized`（`error: You must be logged in to the server`）で弾かれる場合、原因はほぼ 2 つです。1 つ目は、`terraform apply` を実行したプリンシパルと `kubectl` を実行するプリンシパルが食い違っているケースです。`enable_cluster_creator_admin_permissions = true` はクラスタを作成したプリンシパルにだけ管理者権限を与えるため、`apply` を名前付き profile（AWS SSO や assume-role）で実行したのに `update-kubeconfig` を素の `[default]` で叩くと、両者が別プリンシパルになり弾かれます。`aws sts get-caller-identity` で両者のプリンシパルを確認します。assume-role の場合はセッション名部分が違っていても問題なく、`assumed-role/<ロール名>` までが一致していれば認証は通ります（アクセスエントリは基底の IAM ロール ARN 単位でマッチするためです）。一致していなければ `update-kubeconfig` に `apply` と同じ `--profile` を渡す（または `export AWS_PROFILE`）と解消します。自分のロールが登録済みかは `aws eks list-access-entries --cluster-name <name>` でも確認できます。2 つ目は、`apply` 直後にアクセスエントリがまだ認証レイヤに伝播していないケースで、この場合は 1〜2 分待って再実行すれば通ります。
:::

## 4. 作業用の namespace を作る

この book のワークショップでは、学習 Job や推論サーバーなどのワークロードを `default` ではなく専用の namespace に作ります。あとで「この namespace ごと消せば実験の後片付けが済む」ようにするためです。本 book では作業用 namespace を `distai` に統一して進めます。

namespace の作成は `--dry-run` 経由の `apply` にしています。すでに存在していてもエラーにならないようにするためです。step 3 で既定 namespace を `distai` にしたので、ここで作る namespace 名も `distai` で揃えます。

```bash
k create namespace distai --dry-run=client -o yaml | k apply -f -
```

`namespace/distai created`（初回）または `namespace/distai unchanged`（2 回目以降）と表示されれば準備完了です。本 book では最後まで同じ `distai` を使います。

## 5. context を確認する習慣をつける

step 3 で current-context を切り替えたので、以降の `k`（`kubectl`）はすべてこのクラスタの `distai` namespace を対象にします。操作対象のクラスタやリソースが増える後続の章では、current-context を切り替えたまま元のつもりで操作してしまう事故が起きやすいため、破壊的な操作の前には現在の向き先を確認する習慣をつけておきます。

```bash
# 現在の current-context（=どのクラスタを向いているか）
k config current-context

# その context の既定 namespace と、叩きに行く API サーバー
k config view --minify -o 'jsonpath={.contexts[0].context.namespace} @ {.clusters[0].cluster.server}{"\n"}'
```

別のクラスタに切り替えたくなったら `k config use-context <名前>` で戻せます。

## 6. (任意) スモークテストで動作確認する

リポジトリの [`infra/eks/tests/`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/tests) にインフラ層のスモークテストが用意されています。実行は任意ですが、初回構築後やモジュール変更後に回すと「apply は通ったが何かが壊れている」を早期に検出できます。基盤テストは GPU ノードを起動せず約 1 分で完了します。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/tests

# 基盤テスト: control-plane / system-nodes / karpenter / trainer /
#            csi-drivers / device-plugins / storage-mount (FSx read/write)
./run-tests.sh --profile $PROFILE
```

クラスタ名とリージョンは `terraform output` から自動で解決されるので指定は不要です（`--cluster-name` / `--region` で明示的に上書きすることもできます）。実機出力は次のようになります。

```text
==============================
 Test Summary
==============================
STATUS   TEST                                DETAIL
--------------------------------------------------------------
PASS     control-plane                       4s
PASS     system-nodes                        3s
PASS     karpenter                           8s
PASS     trainer                             3s
PASS     csi-drivers                         29s
PASS     device-plugins                      11s
PASS     storage-mount                       42s
--------------------------------------------------------------
PASS: 7  FAIL: 0  SKIP: 0  TOTAL: 7
```

全 PASS であれば、Karpenter・CSI ドライバ・Kubeflow Trainer・共有ストレージが正常に機能しており、Basic02 以降のワークショップに進む準備ができています。`device-plugins` は GPU/EFA/Neuron の device plugin の DaemonSet を見る項目で、該当プールが無い段階では対象が存在しないため「該当なし」として PASS します。

GPU ノードを起動して行う GPU スモークテストは、アクセラレータプールを定義する Basic04 で扱います。

# まとめ

本章では、分散 AI の実験を回すための土台として Amazon EKS クラスタを構築し、以降のワークショップで使う作業用 namespace `distai` を作成しました。中核として作ったのは Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループの 3 つで、同じ apply で載る Karpenter を Basic03 で掘り下げ、Basic04 以降でアクセラレータプールを積み上げていきます。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
