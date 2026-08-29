---
title: "Basic01 - Amazon EKS 基盤を立てる"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、分散学習・推論の実験を行うための基盤として Amazon EKS クラスタを構築します。

Terraform で Amazon VPC・Amazon EKS コントロールプレーン・Karpenter を動かすための System ノードグループをデプロイし、`kubectl` でノードが見えるところまでを扱います。

:::message alert
本資料は `us-east-2` リージョンを例に説明します。実際には自身で選択したリージョンに読み替えて進めてください。コマンド中の `<region>` などのプレースホルダは自分の値に置き換えます。
:::

# 解説

## 全体構成

本書全体で構築する分散 AI 基盤の全体像です。Amazon VPC は複数の AZ にまたがって作り、Amazon EKS コントロールプレーンの下で Karpenter が GPU/Neuron の各 NodePool を要求に応じて起動します。共有ストレージ（既定は単一 AZ の Amazon FSx for OpenZFS と FSx for Lustre）や Capacity Block の期限監視といった周辺サービスも含めた構成です。各コンポーネントは以降の章で 1 つずつ扱います。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章の `terraform apply` は、この基盤のクラスタ全体で共有する部分を一度に構築します。図のうち中核となる **Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループ** に加え、その上で動く Karpenter コントローラ・各 CSI ドライバ・Kubeflow Trainer・共有ストレージまで、後続の章で使う基盤コンポーネントが同じ apply で揃います。本章で詳しく解説するのは中核の 3 つで、残りは各コンポーネントの章で 1 つずつ扱います。

## Amazon VPC の設計

Amazon VPC は [`vpc.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/vpc.tf) で [`terraform-aws-modules/vpc/aws`](https://registry.terraform.io/modules/terraform-aws-modules/vpc/aws/latest) モジュールを使って作ります。全体はこれだけです。

```hcl
# vpc.tf
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = var.cluster_name
  cidr = var.vpc_cidr        # 既定 10.0.0.0/16

  azs             = local.azs             # 既定はリージョンの全標準 AZ（az.tf で導出）
  private_subnets = local.private_subnets # vpc_cidr の下半分を AZ 数が収まる 2 の冪個に切り先頭から使う（2 AZ→/18, 3-4 AZ→/19）
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

ここで `azs` / `private_subnets` / `public_subnets` に渡している 3 つの `local.*` が、この構成の設計上の要点です。いずれも [`az.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/az.tf) で `var.region` と `var.vpc_cidr` から自動導出しており、通常のデプロイでは AZ もサブネット CIDR も一切手書きしません。AZ や CIDR を tfvars に書く必要はありません (生成される tfvars には、後述のとおりリージョンとクラスタ名とアカウント ID が入ります)。

**AZ とサブネット CIDR の自動導出**: `local.azs` は `var.azs` が `null`（既定）ならそのリージョンの標準 AZ を `sort` して全件返します。サブネット CIDR も `var.private_subnet_cidrs` / `var.public_subnet_cidrs` が `null`（既定）なら `var.vpc_cidr` から AZ ごとに 1 つずつ切り出します。既定ではうまく AZ を適切な CIDR で切ってくれていると思っていただければ大丈夫です。ただし全 AZ を返すという性質上、EKS のコントロールプレーンや新しいインスタンスタイプに対応していない制約付きの AZ (us-east-1e など) を含むリージョンでは、そのままだと apply が失敗することがあります。その場合は `terraform.tfvars` に `azs = ["...", "..."]` で使いたい AZ を明示します。

**`one_nat_gateway_per_az = true` の意味**: NAT ゲートウェイを AZ ごとに 1 つ置き、各 AZ のプライベートルートテーブルはその AZ 自身の NAT を向きます。VPC は `local.azs` の全 AZ にまたがるので、2 AZ なら NAT も 2 つ、3 AZ なら 3 つ作られます。既定では `local.azs` がリージョンの全標準 AZ を返すため、AZ 数が多いリージョン（us-east-1 は標準 AZ が 6 つ）では NAT ゲートウェイと Elastic IP がその数だけ作られます。[Elastic IP の既定のクォータ](https://docs.aws.amazon.com/vpc/latest/userguide/amazon-vpc-limits.html)はリージョンあたり 5 つなので、AZ 数の多いリージョンに読み替える場合は、クォータの引き上げか `var.azs` での AZ 数の絞り込みが必要になることがあります。

当初この基盤は `single_nat_gateway = true` で単一 NAT にしていましたが、Capacity Block for ML がどの AZ でも使いうることを考えると、全ての AZ に NAT を置く方が良いと判断しました。単一 NAT だと他 AZ のノードからの外向き通信がすべて AZ をまたいでその NAT に集まり、クロス AZ のデータ転送料金がかかります。AZ ごとに NAT を置けばこの転送料金を避けられ、かつ 1 つの AZ の NAT が落ちても他 AZ の外向き通信が生き残る耐障害性の利点もあります。ただしこれはどちらでも良いと思います。

なお、外向き通信は NAT だけに依存しているわけではありません。ECR・Amazon EC2・STS・SSM・CloudWatch Logs・EKS Auth は [`vpc-endpoints.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/vpc-endpoints.tf) の Interface endpoint 経由で、Amazon S3 は Gateway endpoint 経由で、いずれも NAT を通らずに到達します。ECR のイメージ pull は、リージョンによってレイヤの実体を S3 から取得するため、この S3 Gateway endpoint も合わせて用意しています。NAT が主に担うのは `nvcr.io` や `quay.io`、`registry.k8s.io` といった ECR 以外のレジストリと、Interface endpoint を持たない IAM です。なお Interface endpoint はサービスごと・AZ ごとに 1 つ作られます。AZ はリージョンの標準 AZ をすべて使うので、us-east-2 (3 AZ) なら 7 サービス × 3 で 21 個、AZ の多いリージョンではさらに増え、それぞれに時間課金とデータ処理課金がかかります。NAT を通す構成より安く済ませるための選択ですが、無料ではありません。

**`private_subnet_tags` の `karpenter.sh/discovery`**: このタグが後の章で有効に働きます。Karpenter は「ノードを起動してよいサブネット」をこのタグで検出します。ここで**プライベートサブネットにだけ**タグを付け、`public_subnet_tags` には付けていない点が重要です。もし共通の `tags` に含めてしまうと全サブネットに伝搬してパブリックサブネットにも付き、Karpenter がそこにノードを起動してしまいます。この構成はパブリックサブネットにパブリック IP を自動付与しない設定なので、そこに立ったノードは外向きの到達経路を持たず、`nodeadm` によるクラスタ参加に失敗します。

::::details nodeadm とは

[`nodeadm`](https://awslabs.github.io/amazon-eks-ami/nodeadm/) は、EKS のノードとなる EC2 インスタンスを Kubernetes クラスタへ参加させるためのブートストラップ CLI です。[Amazon Linux 2023 ベースの EKS 最適化 AMI](https://docs.aws.amazon.com/eks/latest/userguide/eks-optimized-ami.html) に標準で同梱されており、ブート時に kubelet と containerd を構成してコントロールプレーンへ join させる役割を担います。

## 旧方式からの変化

Amazon Linux 2 の時代は `/etc/eks/bootstrap.sh` というシェルスクリプトを user data から呼び出し、クラスタ名やフラグを引数として渡していました。AL2023 ではこれが `nodeadm` に置き換わり、YAML による宣言的な設定へと移行しています。設定オブジェクトは `NodeConfig` と呼びます。

## NodeConfig の書き方

nodeadm は user data から `NodeConfig` を読み取ります。YAML ドキュメント単体で渡すこともできますし、cloud-init と併用する場合は MIME マルチパートの 1 パート（`Content-Type: application/node.eks.aws`）として渡します。

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

Karpenter で AL2023 AMI を使う場合も、ノードの user data は nodeadm の `NodeConfig` 形式になります。`EC2NodeClass` の `spec.userData` に書いた内容は、Karpenter が生成する `NodeConfig` パートの前段に置かれ、起動時に nodeadm が複数の `NodeConfig` を結合します。後から読み込まれた設定が優先されるため、クラスタ接続情報や Karpenter が付与するラベル（`karpenter.sh/nodepool` など）は利用者側では上書きできません。「userData に書けば何でも反映される」わけではない点に注意してください。そのため、ノードラベルは userData ではなく NodePool の `spec.template.metadata.labels` で付与します。

::::

## Amazon EKS クラスタと System ノードグループ

Amazon EKS 本体は [`eks.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/eks.tf) で `terraform-aws-modules/eks/aws` モジュールを使って作ります。アドオンと System ノードグループの定義が注目すべき点です。

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

**System ノードグループの `karpenter.sh/controller` ラベル**: 既定では m5 系インスタンスを 2 台、固定で起動します。このノードグループは Karpenter が管理するのではなく、Amazon EKS Managed Node Group として常時稼働させます。`karpenter.sh/controller: "true"` というラベルを付けているのは、本章の apply で導入される Karpenter コントローラ自身をこのノードに載せるためです。Karpenter コントローラを Karpenter 管理下のノードに載せると、コントローラが自分の載っているノードを消しかねないため、推奨されません。そのため Karpenter 自身を動かす場所として、Karpenter の管理外のノードグループが必要になります。もう 1 つの `node-role: system` は、後続の章で Karpenter の各プールが付ける `node-role=<プール名>` と同じキーです。ワークロードが「GPU でないノード」という消極的な条件で誤って system ノードに載るのを防ぎ、載せたい層を明示的に指定できるようにしています。

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

`enable_inline_policy = true` にも実務上の理由があります。Karpenter v1 のコントローラポリシーは、AWS の[マネージドポリシーのサイズ上限](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_iam-quotas.html)（空白を除いて 6,144 文字、変更不可）をわずかに超え、`LimitExceeded: PolicySize: 6144` で失敗します。ロールに直接付ける[インラインポリシー](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_iam-quotas.html)（上限 10,240 文字）にすれば同じ権限のまま上限に収まるため、この構成ではインラインを選んでいます。

## インフラ層が常設管理するもの、しないもの

本章では貫いている 1 つの原則があります。クラスタスコープで複数のワークロードが共有し、消えると学習/推論 Pod が動かなくなるもの、具体的には CSI ドライバ・Kubeflow Trainer・Karpenter コントローラ・共有ストレージの静的 PV は、Pod と同じ寿命で作っては消すのではなく、クラスタの基盤として Terraform が常設管理します。一方、namespace や PVC、実際に流す学習 Job そのもののように、特定のワークロードと同じ寿命を持つ namespace スコープのリソースは、ワークショップ側で `kubectl` や Helm で作ります。

この原則からいくつかの設計判断をしました。

- **ストレージは CSI ドライバとファイルシステム本体を分離**: ドライバはワークロードを動かす前提になるので基盤層に属し、EBS・FSx（OpenZFS・Lustre）・EFS のいずれも無条件で常設します。ファイルシステム本体と静的 PV の作成はワークロード側の選択なので、`openzfs_enabled` などのフラグで制御します。既定では OpenZFS と Lustre を作り、EFS は本体を作らずドライバだけ常設します。この分離により、あとで EFS が要るときはアドオン導入からではなくファイルシステムを 1 つ足すだけで済みます。
- **共有ストレージの既定は単一 AZ の FSx**: FSx for OpenZFS には Multi-AZ 構成も選べますがこの基盤では意図的に単一 AZ を既定にしています。理由は EFA・FSx for Lustre・Capacity Block を前提とする学習系ワークロードでは計算ノードが 1 つの AZ に集まるため、ストレージだけをマルチ AZ にしても可用性は上がらず、単価だけが上がります。AZ 障害に備えた成果物の長期保全は、ストレージのマルチ AZ 化ではなくチェックポイントの Amazon S3 退避で担うのが実務で一般的な構成です。逆に、可用性のためマルチ AZ 配置が一般的な構成になる推論サービングや、AZ をまたいでキャッシュを共有したい特定用途のためには、EFS を 任意で有効にできる選択肢として残しています。なお本章の Amazon VPC を複数 AZ で張るのは [Amazon EKS コントロールプレーンが 2 AZ 以上のサブネットを要求する](https://docs.aws.amazon.com/eks/latest/userguide/network-reqs.html)ためと、Capacity Block がどの AZ に落ちても対応するサブネットが必ずあるようにするためで、計算を複数 AZ に分散させるためではありません。

# ワークショップ実施

## 1. 2 段階で導入する

導入は 2 段階です。前半はリポジトリをリリース固定で取得するだけで、AWS のリソースは作りません。ただし前提の確認として `aws sts get-caller-identity` を実行するので、この時点で認証は通っている必要があります。名前付きプロファイルを使う場合は、この前に `export AWS_PROFILE=<自分のプロファイル名>` を置き、必要なら `aws sso login --profile <名前>` を済ませてください。

```bash
curl -fsSL https://raw.githubusercontent.com/littlemex/distributed-ai/refs/tags/release/eks-distributed-ai/v0.2.0/infra/scripts/distai-install.sh | bash
```

後半がクラスタを作るコマンドです。渡すのはクラスタ名とリージョンだけで、どちらも環境変数で置きます。名前付きプロファイル (AWS SSO や assume-role) で認証している場合は `AWS_PROFILE` も置いてください。スクリプトはこれを読み取り、生成する変数ファイルにも書き込みます。ただし書き込む先は Terraform が読む値で、AWS CLI と `kubectl` は環境変数の `AWS_PROFILE` しか見ません。新しいシェルで export し忘れると、tfvars に profile が残っていても CLI 側はプロファイル指定なしの `[default]` で動くので、両方を揃えてください。

```bash
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
```

名前付きプロファイルを使っている場合は、あわせて `export AWS_PROFILE=<自分のプロファイル名>` も置きます。プロファイル指定なしの `[default]` で認証している場合は不要です。存在しない名前を設定すると、スクリプトが `no usable AWS credentials. Sign in, or set AWS_PROFILE, before running this.` で停止します。AWS CLI 側の詳細メッセージは表示されないので、`aws sts get-caller-identity` を単独で実行して原因を見てください。

コマンド自体は引数なしで実行します (使い方を出す `-h` は受け付けます)。ここで作られるもののうち、共有ストレージ (FSx for Lustre と OpenZFS) はアイドル時の課金が最も大きいので、まず基盤だけ見たい場合は下の 2 行目のように `DISTAI_SHARED_STORAGE=off` を付けます。この変数が効くのは `terraform.tfvars` を初めて生成するときだけで、後から付けても既存のファイルは書き換わりません。

```bash
cd ~/distributed-ai-v0.2.0
./infra/scripts/distai-up.sh

DISTAI_SHARED_STORAGE=off ./infra/scripts/distai-up.sh
```

分けてあるのは、`curl` をシェルに流す形の中で課金リソースを作らせないためです。理由は 3 つあります。取得と課金を別のコマンドにしておけば、何を取得して何に課金したかを後から追えます。パイプの中では stdin をスクリプト本体が使っているので、確認を求めても読者は答えられません。そして apply の前には `terraform plan` の内容を表示して明示的に確認を取りたいからです。ここで表示されるのは変更の件数と、変更のあるリソース名の先頭 40 件までです (作成だけでなく更新・置換・削除も同じ形で並び、40 件を超えた分は `... and N more` にまとめられます)。属性ごとの差分や置き換えの詳細は表示されないので、そこまで見たい場合は後述の 4 行を実行したうえで `infra/eks` で `terraform plan` を直に実行してください。

[`distai-up.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/scripts/distai-up.sh) は 5 つのフェーズを順に実行します。前提確認、実行前の確認、state の作成とレジストリへの記録、変数ファイルの生成、そして plan の表示と apply です。実行前の確認では、その前の前提確認で表示されたアカウント・呼び出し元・リージョン・クラスタ名を確認したうえで**クラスタ名の入力**を求めます。y の 1 文字では、上に何が表示されていても押せてしまうからです。クラスタ名の入力は 2 回あります。1 回目がこの実行前の確認で、2 回目は plan を表示したあとの適用確認です。

変数ファイル (`infra/eks/terraform.tfvars`) はここで生成されます。中身はリージョン、クラスタ名、`expected_account_id`、そして `AWS_PROFILE` を設定している場合だけ `aws_profile` です (設定していなければこの行は書かれません)。`expected_account_id` は、認証情報が別のアカウントを指したまま apply しようとしたときに plan の段階で停止させるための安全策で、アカウント ID はこの時点で判っているので自動で埋まります。生成後のファイルは自由に編集してよいので、AZ や CIDR を明示指定したいときはここに書き足します。

```bash
cat infra/eks/terraform.tfvars
```

アクセラレータノードのプールは生成されません。GPU や Capacity Block は利用料金が高いので、必要になった章で [`accelerator-pools.tfvars.example`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/accelerator-pools.tfvars.example) をコピーして明示的に有効化します。一方で監視スタック (`enable_observability`) は既定で有効なので、この apply の時点で監視専用の NodePool にノードが 1 台常駐し、Prometheus と Grafana の EBS も作られます。Basic08 まで監視を見ないのであれば、この 1 台分を後回しにできます。ただし変数ファイルの生成と plan は同じ実行の中で連続して行われるので、生成された `terraform.tfvars` を編集する隙はありません。plan の適用確認でクラスタ名以外を入力すると `The plan above was discarded.` で終わるので、そこで `infra/eks/terraform.tfvars` に `enable_observability = false` を書き足し、`distai-up.sh` をもう一度実行します。2 回目は変数ファイルが既にあるのでそのまま使われます。共有ストレージ (FSx for Lustre と OpenZFS) は既定で有効です。学習サンプルが `/shared` をマウントするためですが、アイドル時の課金として最も大きいので、上に書いたとおり最初の実行で `DISTAI_SHARED_STORAGE=off` を付けるかどうかを決めます。この環境変数が反映されるのは `terraform.tfvars` を初めて生成するときだけです。すでにファイルがある場合はスクリプトが `exists; leaving it alone` と表示して触らないので、そのときは次に述べる方法でファイルを直接編集します。あとから有効にするときは、生成された `infra/eks/terraform.tfvars` の `fsx_enabled` と `openzfs_enabled` を `true` に直して `distai-up.sh` を再実行します。

:::message alert
`terraform apply` は state に記録されたリソースだけを管理し、state に無いリソースが AWS 側に存在するかどうかは確認しません。このため profile を取り違えると、名前に一意制約があるリソース (IAM ロール、KMS エイリアス、CloudWatch ロググループ) は作成時のエラーで失敗し、FSx ファイルシステムのように一意制約が無いものはエラーにならず二重作成されて課金が始まります。より危険なのは state にリソースが記録済みのまま別アカウントに profile が向くケースで、Terraform は「管理下のリソースがすべて消えた」と判断してエラーも出さずに丸ごと作り直します。`distai-up.sh` は実行前にアカウントと呼び出し元 ARN を表示し、生成する tfvars に `expected_account_id` を書き込むので、この事故は plan の段階で止まります。それでも表示されたアカウントが意図どおりかは自分の目で確かめてください。
:::

ここで中止しても、state のバケットとロックテーブル、レジストリのパラメータは前のフェーズで作成済みなので残ります。クラスタ本体を作らずにやめる場合は、これらを自分で消すか、次に同じ名前で作るときにそのまま再利用してください。

plan が表示されたあと、apply に入る前にもう一度クラスタ名の入力を求められます。apply には 20〜30 分程度かかります。`Cluster <クラスタ名> is applied and registered.` と、次の step で使う 4 行が表示されれば成功です。時間がかかるのはコントロールプレーンの起動と FSx ファイルシステムの作成で、いずれも単独で 10〜15 分かかります。両者は VPC さえできれば並行して作られるので、2 つの合計にはなりません。

## 2. 以降の章の前提はこの 4 行

レジストリ (AWS Systems Manager のパラメータストア) への記録は 2 段階です。state がどこにあるかは apply の前に記録され、どのリリースで作ったかは apply が成功したあとに記録されます。以降の章は、どのクラスタかを名前とリージョンで指すだけで、残りをそこから解決できます。

```bash
cd ~/distributed-ai-v0.2.0
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
source infra/scripts/distai-env.sh
```

1 行目でチェックアウトに移動しているのは、この後の章が `terraform output` を使うためと、リポジトリの外で実行すると別のリポジトリを参照してしまう可能性があるからです。別の場所に clone した場合はそのディレクトリに読み替えてください。名前付きプロファイル で認証している場合は、`export AWS_PROFILE=my-profile` もこの 4 行の前に置きます。

これが解決するのは、リージョン、アカウント ID、state のバケットとキーとロックテーブルと暗号化キー、クラスタを作ったときのリリースタグと最後に適用したリリースタグ、そして紐づいているデータ層の一覧と既定です。ただしデータ層はプロファイリング基盤を導入したときに初めて紐づくので、Basic01 の時点ではこの項目は空です。バケット名や state のキーを章に書く必要がなくなり、別のマシンで clone し直した場合でも `backend.hcl` と、`backend.tf` が無ければそれも合わせて用意されます (どちらもリポジトリには含まれないので、この生成が無いと `terraform init` が S3 の state を見ません)。ただしこの 4 行は `terraform init` までは行いません。別のマシンで clone し直した直後は `.terraform` が無いので、`terraform output` は `Backend initialization required` で失敗します。その場合は一度 `terraform -chdir=infra/eks init -reconfigure -backend-config=backend.hcl` を実行してください。`backend.tf` を作るのは `backend.hcl` を生成するときだけなので、`backend.hcl` だけが残っていて `backend.tf` が無い作業ディレクトリでは、`infra/eks/backend.tf.example` を自分で `backend.tf` にコピーします。

この 4 行はレジストリの読み取り、呼び出し元アカウントの確認、クラスタの参照を行うので、`ssm:GetParametersByPath`、`sts:GetCallerIdentity`、`eks:DescribeCluster` の権限が必要です。レジストリが読めないときは、この権限を疑ってください。

あわせて `kubectl` もこのクラスタに向けます。`aws eks update-kubeconfig` の実行、context の選択、既定 namespace の設定、`kubectl` を `k` と打つための定義が、この `source` に含まれています。章ごとにこれらを打ち直す必要はありません。実行内容は step 3 で確認します。

リージョンを 4 行に含めているのは、クラスタが (アカウント, リージョン, 名前) の 3 つ組で初めて一意になるからです。`AWS_REGION` を省いた場合は AWS CLI の設定を使い、それも無ければ停止します。CLI の既定リージョンがクラスタのリージョンと違う環境では、省くと「そのリージョンにそのクラスタは無い」という停止に当たるので、書いておく方が確実です。作成時と最後の適用のリリースタグを別に持っているのは、古いチェックアウトで新しいクラスタを触ろうとしている状況を検出するためです。

レジストリに置いているのは、state を開く前に必要な情報と、クラスタの外側にある関連付けだけです。state ファイル自身には保存場所を書けませんし、`backend.hcl` は環境固有なのでリポジトリに含まれません。この 2 つの事情が「クラスタ名から始められない」原因だったので、そこを外に出しています。エンドポイントもサブネット ID も MLflow の ARN も入れていません。これらは `terraform output` で引けるので、二重に持つと「どちらが正しいか」という問いが生まれるからです。

:::message
実行すると、対象のクラスタ・リージョン・アカウント・リリースタグ・データ層 (紐づいていなければ `none`) が 1 行で表示されます。apply が途中で失敗した場合、レジストリには state の保存場所までが記録されていてリリースタグがまだ無い状態になります。この状態でも解決自体は通り、リリースタグの位置に `unrecorded` と表示されます。認証情報のアカウントがレジストリの記録と食い違う場合は、その場で停止します。クラスタ名は (アカウント, リージョン, 名前) の 3 つ組で初めて一意になるので、名前だけで別のクラスタを操作しないための確認です。
:::

## 3. ノードを確認する

`kubectl` の設定は step 2 で済んでいます。4 行を `source` したときに `aws eks update-kubeconfig` が実行され、context がこのクラスタに、その context の既定 namespace が `distai` に設定され、`kubectl` を `k` と打てるようになっています。表示された次の 2 行がその結果です。

```text
distai-env: kubectl: context distai-eks, namespace distai at https://XXXXXXXX.gr7.us-east-2.eks.amazonaws.com (the namespace does not exist yet)
distai-env: k is kubectl --context distai-eks; KUBECONFIG is /home/ubuntu/.kube/distai/distai-eks.distai.yaml
```

この 1 行目は、kubeconfig から読んだ endpoint に加えて、実際に API サーバーへ 1 回問い合わせた結果を添えたものです。endpoint 自体は kubeconfig にあるので、問い合わせが失敗しても endpoint は表示されます。見るのは末尾で、`(unreachable: ...)` が付いていなければ到達性と認証まで確認できたことになります。末尾の `(the namespace does not exist yet)` は step 4 で作る `distai` namespace がまだ無いという意味なので、この時点では正常です。

kubeconfig は既定の `~/.kube/config` ではなく、クラスタと namespace ごとの専用ファイルに書きます。既定の kubeconfig の current-context を書き換えると、別のターミナルで他のクラスタを触っている作業まで巻き込むためです。設定が有効なのは `source` したシェルの中だけなので、ターミナルを開き直したら step 2 の 4 行をもう一度実行します。`k` は、クラスタの解決に成功している間は `--context` を付けて `kubectl` を呼ぶ関数なので、後から current-context が変わっても向き先はずれません (一度解決に成功したシェルで再度の解決に失敗した場合は、`--context` を付けない `kubectl` として動くので向き先が固定されません。初回から解決に失敗した場合は `k` そのものが定義されません)。

```bash
k get nodes
```

インスタンスタイプまで見たい場合は列を指定します。

```bash
k get nodes -o custom-columns='NAME:.metadata.name,TYPE:.metadata.labels.node\.kubernetes\.io/instance-type,ROLE:.metadata.labels.node-role,CAP:.metadata.labels.karpenter\.sh/capacity-type'
```

`k get nodes` で m5 系のノードが 2 台 `Ready` 状態で表示されれば、System ノードグループの起動は成功です。監視スタックを既定のまま有効にしている場合は、これに加えて監視専用 NodePool のノードが 1 台見えるので、合計 3 台になります。この 3 台目は Karpenter が監視 Pod の `Pending` を見てから起動するので、apply 完了の数分後に現れます。2 台しか見えない時間があるのは失敗ではありません。10 分待っても増えない場合は `k get nodeclaims` と `k -n karpenter logs deploy/karpenter --tail=50` を見ます。

:::message alert
`kubectl` が `Unauthorized`（`error: You must be logged in to the server`）で拒否される場合、原因はほぼ 2 つです。1 つ目は、`terraform apply` を実行したプリンシパルと `kubectl` を実行するプリンシパルが食い違っているケースです。`enable_cluster_creator_admin_permissions = true` はクラスタを作成したプリンシパルにだけ管理者権限を与えるため、`distai-up.sh` を名前付きプロファイル（AWS SSO や assume-role）で実行したのに、`source` するシェルで `AWS_PROFILE` を設定し忘れてプロファイル指定なしの `[default]` で認証していると、両者が別プリンシパルになり拒否されます。`source` は `AWS_PROFILE` をそのまま kubeconfig に書き込むので、`export AWS_PROFILE=<name>` を 4 行の前に置き、`aws sts get-caller-identity` で両者のプリンシパルを確認してください。assume-role の場合はセッション名部分が違っていても問題なく、`assumed-role/<ロール名>` までが一致していれば認証は通ります（アクセスエントリは基底の IAM ロール ARN 単位でマッチするためです）。自分のロールが登録済みかは `aws eks list-access-entries --cluster-name <name>` でも確認できます。2 つ目は、`apply` 直後にアクセスエントリがまだ認証レイヤに伝播していないケースで、この場合は 1〜2 分待って再実行すれば通ります。
:::

## 4. 作業用の namespace を作る

本書のワークショップでは、学習 Job や推論サーバーなどのワークロードを `default` ではなく専用の namespace に作ります。あとで「この namespace ごと消せば実験の後片付けが済む」ようにするためです。本書では作業用 namespace を `distai` に統一して進めます。

namespace の作成は `--dry-run` 経由の `apply` にしています。すでに存在していてもエラーにならないようにするためです。step 2 で既定 namespace が `distai` になっているので、ここで作る namespace 名も `distai` で揃えます。

```bash
k create namespace distai --dry-run=client -o yaml | k apply -f -
```

`namespace/distai created`（初回）または `namespace/distai unchanged`（2 回目以降）と表示されれば準備完了です。本書は既定としてこの `distai` を使います (章によって別の namespace を使いたい場合の切り替え方は次に触れます)。作成後に step 2 の 4 行をもう一度実行すると、先ほどの `(the namespace does not exist yet)` が消えます。

## 5. 向き先を確認する習慣をつける

操作対象のクラスタやリソースが増える後続の章では、別のクラスタを向いたまま元のつもりで操作してしまう事故が起きやすいため、破壊的な操作の前には現在の向き先を確認する習慣をつけておきます。`source` したときに表示される 2 行と同じ内容を、いつでも自分で確認できます。

```bash
k config current-context

k config view --minify -o 'jsonpath={.contexts[0].context.namespace} @ {.clusters[0].cluster.server}{"\n"}'
```

別のクラスタに切り替えたいときは、`CLUSTER_NAME` を変えて step 2 の 4 行をもう一度実行します。クラスタごとに kubeconfig が分かれているので、`k config use-context` で切り替える相手はこのファイルの中には居ません。`distai` 以外の namespace を既定にしたい章では、`source` の前に `export DISTAI_NAMESPACE=<name>` を置くか、コマンド側で `-n <name>` を明示します。

## 6. (任意) スモークテストで動作確認する

リポジトリの [`infra/eks/tests/`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/tests) にインフラ層のスモークテストが用意されています。テストは `kubectl` の向き先と `terraform output` から対象クラスタを解決するので、step 2 の 4 行を同じシェルで実行済みにしてから走らせてください。実行は任意ですが、初回構築後やモジュール変更後に回すと「apply は通ったが何かが壊れている」を早期に検出できます。基盤テストは GPU ノードを起動しません。下の実機出力の DETAIL 列を足すと 2 分弱で、長いのは共有ストレージのマウント確認 (storage-mount) と CSI ドライバの確認です。

基盤テストが見るのは control-plane、system-nodes、karpenter、trainer、csi-drivers、device-plugins、storage-mount (FSx の読み書き) の 7 項目です。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/tests
./run-tests.sh
```

クラスタ名とリージョンは kubectl の向き先と `terraform output` から自動で解決され、認証は `AWS_PROFILE` をそのまま使うので、引数は不要です（`--cluster-name` / `--region` / `--profile` で明示的に上書きすることもできます）。実機出力は次のようになります。

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

全 PASS であれば、Karpenter・CSI ドライバ・Kubeflow Trainer・共有ストレージが正常に機能しており、Basic02 以降のワークショップに進む準備ができています。手順 1 で `DISTAI_SHARED_STORAGE=off` を選んだ場合は、複製元の PV が無いので `storage-mount` は FAIL ではなく SKIP になり、`PASS: 6 SKIP: 1` になります。`device-plugins` は GPU/EFA/Neuron の device plugin の DaemonSet を見る項目で、該当プールが無い段階では対象が存在しないため「該当なし」として PASS します。

GPU ノードを起動して行う GPU スモークテストは、アクセラレータプールを定義する Basic04 で扱います。

# まとめ

本章では、分散 AI の実験を行うための基盤として Amazon EKS クラスタを構築し、以降のワークショップで使う作業用 namespace `distai` を作成しました。中核として作ったのは Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループの 3 つで、同じ apply で載る Karpenter を Basic03 で掘り下げ、Basic04 以降でアクセラレータプールを積み上げていきます。

ここで一度中断する場合は、この時点で EKS コントロールプレーン・System ノード・NAT Gateway・Interface endpoint・共有ストレージ・監視スタックが動いたままになります。先に進まないなら Basic11 の破棄手順で片付けてください。state を置く S3 バケットとレジストリ (AWS Systems Manager Parameter Store) は破棄後も残る設計です。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
