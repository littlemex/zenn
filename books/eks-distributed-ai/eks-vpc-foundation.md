---
title: "Basic01 - Amazon EKS 基盤を立てる"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章のゴールは、GPU や Trainium のノードを「必要になったときだけ起動する」土台をひととおり立て、`kubectl` でノードが見える状態にすることです。

そのために作るものは 3 つです。ノードを置く場所として **Amazon VPC**、Kubernetes の頭脳として **Amazon EKS コントロールプレーン**、そしてノードを起動する Karpenter コントローラ自身を載せる **System ノードグループ** です。3 つ目が要るのは、Karpenter が「Pod が要求したときにノードを作る」仕組みだからです。理由は後の節で見ます。

以降では、この 3 つを Terraform でどう作っているか、その中で押さえておくべき判断だけを見ていきます。

:::message alert
本資料は `us-east-2` リージョンを例に説明します。実際には自身で選択したリージョンに読み替えて進めてください。コマンド中の `<region>` などのプレースホルダは自分の値に置き換えます。
:::

# 解説

## 全体構成

まず本書全体で作るものの位置関係を見ます。Amazon VPC は複数の AZ にまたがり、Amazon EKS コントロールプレーンの下で Karpenter が GPU や Neuron の NodePool を要求に応じて起動します。共有ストレージや Capacity Block の期限監視も、同じクラスタの上に載ります。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章の `terraform apply` は、このうちクラスタ全体で共有する部分をまとめて作ります。中核の 3 つに加えて、Karpenter コントローラ・各 CSI ドライバ・Kubeflow Trainer・共有ストレージまでが同じ apply で揃います。まとめて作るのは、どれも「特定のワークロードのものではなく、クラスタに 1 つあればよいもの」だからです。個々のコンポーネントの中身は、それを使う章で扱います。

## Amazon VPC の設計

VPC に求めるものは 2 つです。1 つは Capacity Block がどの AZ に落ちても、その AZ にサブネットがあって受け止められることです。もう 1 つは、Karpenter が起動したノードが VPC の外の宛先に到達できることです。ノードは起動直後にコンテナイメージを Amazon ECR から pull し、kubelet や各コントローラが EC2・STS・SSM といった AWS の API を呼び、`nvcr.io` のような ECR 以外のレジストリからも pull します。これらはすべて VPC の外にあるので、経路が無いとノードはクラスタに参加すらできません。

この 2 つを満たすために、AZ とサブネットは全 AZ 分を自動で用意し、外向きの経路は NAT と VPC エンドポイントで作ります。実装は [`vpc.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/vpc.tf) で [`terraform-aws-modules/vpc/aws`](https://registry.terraform.io/modules/terraform-aws-modules/vpc/aws/latest) モジュールを呼ぶだけで、全体はこれだけです。

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

押さえるべきは 3 点です。

**AZ とサブネットは手書きしない。** `azs` / `private_subnets` / `public_subnets` に渡している `local.*` は、[`az.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/az.tf) が `var.region` と `var.vpc_cidr` から導出します。既定ではそのリージョンの標準 AZ を全件使い、CIDR も AZ ごとに切り出すので、tfvars に AZ も CIDR も書きません。Capacity Block は購入するまでどの AZ に落ちるか決まらないので、先に全 AZ 分のサブネットを用意しておけば、あとで「その AZ にサブネットが無い」で詰まりません。

**NAT は AZ ごとに置く。** `one_nat_gateway_per_az = true` により、各 AZ のプライベートサブネットは自分の AZ の NAT を向きます。単一 NAT はその AZ が落ちたときに全プライベートノードの外向き通信が止まり、イメージの pull がすべて失敗する単一障害点になるためです。NAT の料金は GPU の費用に比べれば小さいので、AZ 障害の隔離を取っています。代償は AZ の数だけ NAT と Elastic IP を持つことで、[Elastic IP の既定のクォータ](https://docs.aws.amazon.com/vpc/latest/userguide/amazon-vpc-limits.html)はリージョンあたり 5 つなので、AZ が 6 つある us-east-1 のようなリージョンではクォータの引き上げか AZ の絞り込みが要ります。

**`karpenter.sh/discovery` はプライベートサブネットにだけ付ける。** Karpenter は「ノードを起動してよいサブネット」をこのタグで探します。共通の `tags` に入れると全サブネットに伝搬してパブリック側にも付き、Karpenter がそこにノードを立ててしまいます。この構成はパブリックサブネットにパブリック IP を振らないので、そこに立ったノードは外に出られず、`nodeadm` によるクラスタ参加に失敗します。

::::details 全 AZ を使う既定で apply が失敗する場合

EKS コントロールプレーンや新しいインスタンスタイプに対応していない制約付きの AZ (us-east-1e など) を含むリージョンでは、全 AZ を使う既定のままでは apply が失敗します。その場合は `terraform.tfvars` に `azs = ["...", "..."]` で使う AZ を明示します。

::::

::::details 外向き通信の経路と、その課金

外向き通信は NAT だけに依存していません。ECR・Amazon EC2・STS・SSM・CloudWatch Logs・EKS Auth は [`vpc-endpoints.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/vpc-endpoints.tf) の Interface endpoint 経由、Amazon S3 は Gateway endpoint 経由で、いずれも NAT を通りません。ECR のイメージ pull はリージョンによってレイヤの実体を S3 から取るので、S3 の Gateway endpoint も合わせて置いています。NAT が担うのは `nvcr.io` や `quay.io` のような ECR 以外のレジストリと、Interface endpoint を持たない IAM です。

エンドポイントを置く目的はコストではなく、NAT の有無や作られる順序から AWS API の呼び出しを切り離すことです。`terraform destroy` の途中で NAT が先に消えると、プライベートサブネットにいる Karpenter コントローラは EC2 API を呼べなくなり、ノードを終了できずにドレイン待ちが時間切れになります。初回の apply でも、NAT ができる前にコントローラが認証情報を取りに行く場面があります。エンドポイント自体はサービスごとに 1 つ (7 個) ですが、それぞれが全 AZ のプライベートサブネットに ENI を持つので、us-east-2 (3 AZ) なら 7 × 3 = 21 AZ 分の時間課金とデータ処理課金がかかります。無料の仕組みではありません。

::::

::::details nodeadm とは

[`nodeadm`](https://awslabs.github.io/amazon-eks-ami/nodeadm/) は、EKS のノードになる EC2 インスタンスをクラスタへ参加させるブートストラップ CLI です。[Amazon Linux 2023 ベースの EKS 最適化 AMI](https://docs.aws.amazon.com/eks/latest/userguide/eks-optimized-ami.html) に同梱されており、ブート時に kubelet と containerd を構成してコントロールプレーンへ join させます。Amazon Linux 2 の時代に user data から呼んでいた `/etc/eks/bootstrap.sh` の置き換えで、設定は `NodeConfig` という YAML で宣言的に渡します。

**NodeConfig の書き方**

nodeadm は user data から `NodeConfig` を読み取ります。次は書式の例です。標準 AMI のマネージドノードグループでは EKS 側がクラスタ接続情報を注入するので、利用者が書くのはカスタマイズしたい差分だけです。

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

`spec.cluster` にクラスタ接続情報を、`spec.kubelet` に kubelet の設定やフラグを書きます。`spec.cluster` をフルに書くのは、セルフマネージドノードやカスタム AMI を使う場合だけです。

ノード側では `nodeadm init` が join を担います。user data 内のスクリプトとして動くのではなく、AMI に組み込まれた systemd ユニット (`nodeadm-config.service` / `nodeadm-run.service`) としてブート時に起動し、IMDS 経由で user data の `NodeConfig` を読み取って実行します。join に失敗したときの診断は `nodeadm debug` です。

**Karpenter との関係**

Karpenter で AL2023 AMI を使う場合も、ノードの user data は nodeadm の `NodeConfig` 形式になります。`EC2NodeClass` の `spec.userData` に書いた内容は、Karpenter が生成する `NodeConfig` パートの前段に置かれ、起動時に nodeadm が複数の `NodeConfig` を結合します。後から読み込まれた設定が優先されるため、クラスタ接続情報や Karpenter が付与するラベル（`karpenter.sh/nodepool` など）は利用者側では上書きできません。「userData に書けば何でも反映される」わけではない点に注意してください。そのため、ノードラベルは userData ではなく NodePool の `spec.template.metadata.labels` で付与します。

::::

## Amazon EKS クラスタと System ノードグループ

次に、Karpenter を動かす場所を用意します。Karpenter は Pod の要求を見てノードを作るコントローラなので、それ自身が載るノードは Karpenter の管理外に常設しておく必要があります。ここが System ノードグループを固定台数で持つ理由です。実装は [`eks.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/eks.tf) で `terraform-aws-modules/eks/aws` モジュールを呼びます。見るべきはアドオンの順序と、System ノードグループに付けたラベルの 2 か所です。

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

**アドオンの順序**: `vpc-cni` と `eks-pod-identity-agent` に `before_compute = true` を付け、ワーカーノードより先に入れます。Pod Identity Agent が後になると、Pod Identity で AWS の権限を得るコントローラ (Karpenter など) が起動時に認証情報を取れずクラッシュするためです。

**2 つのラベル**: `karpenter.sh/controller: "true"` は、Karpenter コントローラをこのノードに載せるための宛先です。Karpenter 管理下のノードに載せると、コントローラが自分の載っているノードを消しかねません。`node-role: system` は、後続の章で各プールが付ける `node-role=<プール名>` と同じキーで、ワークロードの載せ先を毎回明示できるようにするためのものです。「GPU でないノード」のような消極的な条件で system ノードに紛れ込むのを防ぎます。既定の台数は m5 系 2 台で、Karpenter ではなく Managed Node Group として常時稼働します。

## Pod Identity による認証

最後に、Karpenter が EC2 を起動できるよう AWS の権限を渡します。方式は 2 つあり、この構成は **Pod Identity** を選んでいます。実装は [`iam.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/iam.tf) で、Karpenter 用の IAM ロールと Pod Identity Association を作ります。

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

従来の IRSA は ServiceAccount にアノテーションを書き、OIDC プロバイダ経由で認証します。Pod Identity は EKS の API リソースである Pod Identity Association だけで ServiceAccount とロールを結び付けるので、Kubernetes のマニフェストに何も書き足しません (IAM 側で要るのは、信頼ポリシーで `pods.eks.amazonaws.com` を許可することだけです)。設定が AWS 側で完結するので、この構成では Karpenter と各 CSI ドライバ (EBS・FSx for OpenZFS・FSx for Lustre・EFS) を Pod Identity で統一しています。先ほどの `eks-pod-identity-agent` アドオンは、これを各 Pod で機能させるためのエージェントです。

`enable_inline_policy = true` は上限に対する回避です。Karpenter v1 のコントローラポリシーは AWS の[マネージドポリシーのサイズ上限](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_iam-quotas.html) (空白を除いて 6,144 文字、変更不可) をわずかに超え、`LimitExceeded: PolicySize: 6144` で失敗します。ロールに直接付ける[インラインポリシー](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_iam-quotas.html)は上限が 10,240 文字なので、権限を変えずに収まります。

## インフラ層が常設管理するもの、しないもの

本章の apply が何を含み、何を含まないかは 1 つの線引きで決めています。**クラスタに 1 つあればよく、消えると学習や推論の Pod が動かなくなるもの**は Terraform が常設管理します。CSI ドライバ・Kubeflow Trainer・Karpenter コントローラ・共有ストレージの静的 PV がこれにあたります。一方、namespace や PVC、実際に流す学習 Job のように**特定のワークロードと寿命が同じもの**は、各章で `kubectl` や Helm から作ります。この線引きがあるので、実験を何度やり直しても基盤側を作り直す必要がありません。

この線引きから 2 つの判断が出ています。

- **ストレージは CSI ドライバとファイルシステム本体を分ける**: ドライバはワークロードを動かす前提なので、EBS・FSx (OpenZFS・Lustre)・EFS のすべてを無条件で常設します。ファイルシステム本体と静的 PV を作るかはワークロード側の選択なので `openzfs_enabled` などのフラグで切り替えます。既定は OpenZFS と Lustre を作り、EFS はドライバだけです。分けておくと、あとで EFS が要るときにファイルシステムを 1 つ足すだけで済みます。
- **共有ストレージの既定は単一 AZ にする**: EFA や Capacity Block を前提とする学習では計算ノードが 1 つの AZ に集まるので、ストレージだけをマルチ AZ にしても可用性は上がらず単価だけ上がります。AZ 障害に備えた保全は、ストレージの多重化ではなくチェックポイントの Amazon S3 退避で担います。マルチ AZ 配置が前提になる推論サービングや、AZ をまたいでキャッシュを共有したい用途には EFS を選択肢として残しています。なお VPC を複数 AZ に張るのは [Amazon EKS コントロールプレーンが 2 AZ 以上のサブネットを要求する](https://docs.aws.amazon.com/eks/latest/userguide/network-reqs.html)ためと、Capacity Block がどの AZ に落ちてもサブネットがあるようにするためで、計算を分散させるためではありません。

# ワークショップ実施

## 1. 2 段階で導入する

導入は「リポジトリを取ってくる」と「クラスタを作る」の 2 つのコマンドに分かれています。前半は AWS のリソースを 1 つも作らず、後半で初めて課金が始まります。分けているのは、どのコマンドで課金が始まったかを後から追えるようにするためと、クラスタを作る側では実行前に対話で確認を取りたいためです。`curl` の出力をシェルに流す形は標準入力をスクリプトが使ってしまうので、対話の確認を置けません。

前半はリポジトリをリリース固定で取得するだけです。ただし前提の確認として `aws sts get-caller-identity` を実行するので、この時点で認証は通っている必要があります。

名前付きプロファイル (AWS SSO や assume-role) を使う場合は、**この後に開くシェルでは毎回 `export AWS_PROFILE=<自分のプロファイル名>` を置く**、と決めておいてください。以降の手順もスクリプトも、プロファイルの指定はこの環境変数だけを見ます。必要なら `aws sso login --profile <名前>` も先に済ませます。存在しない名前を設定すると `no usable AWS credentials. Sign in, or set AWS_PROFILE, before running this.` で停止します。この停止では AWS CLI 側の詳細メッセージが出ないので、原因は `aws sts get-caller-identity` を単独で実行して確かめます。

```bash
curl -fsSL https://raw.githubusercontent.com/littlemex/distributed-ai/refs/tags/release/eks-distributed-ai/v0.2.0/infra/scripts/distai-install.sh | bash
```

後半に渡すのはクラスタ名とリージョンだけです。この 2 つが対象クラスタを決めるので、自分の値に読み替えてください。

```bash
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
```

クラスタを作るコマンドは引数なしで実行しますが (使い方を出す `-h` は受け付けます)、打つ前に**課金の大きい 2 つについて決めておくこと**があります。どちらもこの apply に含まれ、放っておくと常時課金されるものです。

1 つ目は共有ストレージ (FSx for Lustre と FSx for OpenZFS) です。アイドル時の課金として最も大きいので、基盤だけを先に見たいなら次の 2 つ目のコマンドを使います。ただし Basic02 の学習サンプルが FSx for OpenZFS を使うので、Basic02 に進むならいずれ有効にすることになります。

2 つ目は監視スタック (`enable_observability`) です。既定で有効なので、この apply で監視専用の NodePool にノードが 1 台常駐し、Prometheus と Grafana の EBS も作られます。Basic08 まで監視を見ないなら止めておけますが、これを切るには変数ファイル `infra/eks/terraform.tfvars` に `enable_observability = false` を書く必要があり、そのファイルはこのコマンドの中で生成されます。つまり 1 回目の実行では間に入れません。止めたい場合は、plan の適用確認でクラスタ名以外を入力して `The plan above was discarded.` で終わらせ、生成された変数ファイルに 1 行足してからもう一度実行します。2 回目は変数ファイルが既にあるのでそのまま使われます。

共有ストレージも含めて作る場合は、次を実行します。

```bash
cd ~/distributed-ai-v0.2.0
./infra/scripts/distai-up.sh
```

共有ストレージを作らない場合は、代わりに次を実行します。`DISTAI_SHARED_STORAGE` が効くのは変数ファイルを初めて生成するときだけで、後から付けても既存のファイルは書き換わりません。

```bash
DISTAI_SHARED_STORAGE=off ./infra/scripts/distai-up.sh
```

[`distai-up.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/scripts/distai-up.sh) は 5 つのフェーズを順に実行します。前提確認、実行前の確認、state の作成とレジストリへの記録、変数ファイルの生成、そして plan の表示と apply です。

確認は 2 回あり、どちらも y の 1 文字ではなく**クラスタ名の入力**を求めます。1 回目は前提確認で表示したアカウント・呼び出し元・リージョン・クラスタ名に対する同意で、2 回目は plan を見たあとの適用の同意です。y だけで進めると、上に何が表示されていても押せてしまうためこの形にしています。

apply には 20〜30 分程度かかります。`Cluster <クラスタ名> is applied and registered.` と、次の step で使う 4 行が表示されれば成功です。時間がかかるのはコントロールプレーンの起動と FSx ファイルシステムの作成で、どちらも単独で 10〜15 分かかります。VPC さえできれば並行して作られるので、2 つの合計にはなりません。

:::message alert
`terraform apply` は state に記録されたリソースだけを管理し、state に無いリソースが AWS 側に存在するかどうかは確認しません。このため profile を取り違えると、名前に一意制約があるリソース (IAM ロール、KMS エイリアス、CloudWatch ロググループ) は作成時のエラーで失敗し、FSx ファイルシステムのように一意制約が無いものはエラーにならず二重作成されて課金が始まります。より危険なのは state にリソースが記録済みのまま別アカウントに profile が向くケースで、Terraform は「管理下のリソースがすべて消えた」と判断してエラーも出さずに丸ごと作り直します。`distai-up.sh` は実行前にアカウントと呼び出し元 ARN を表示し、生成する tfvars に `expected_account_id` を書き込むので、この事故は plan の段階で止まります。それでも表示されたアカウントが意図どおりかは自分の目で確かめてください。
:::

生成された変数ファイルを見ておきます。中身はリージョン、クラスタ名、`expected_account_id`、そして `AWS_PROFILE` を設定している場合だけ `aws_profile` です。`expected_account_id` が上のアラートで触れた安全策で、アカウント ID はこの時点で判っているので自動で埋まります。

```bash
cat infra/eks/terraform.tfvars
```

このファイルは生成後に自由に編集してよいので、AZ や CIDR を明示したいときはここに書き足します。変数ファイルには GPU や Capacity Block のプールは書かれません。利用料金が高いので、必要になった章で [`accelerator-pools.tfvars.example`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/accelerator-pools.tfvars.example) をコピーして明示的に有効化します。

::::details plan の表示範囲と、あとから変数を変える場合

plan に表示されるのは変更の件数と、変更のあるリソース名の先頭 40 件までです。作成だけでなく更新・置換・削除も同じ形で並び、40 件を超えた分は `... and N more` にまとめられます。属性ごとの差分や置き換えの詳細は出ないので、そこまで見たい場合は step 2 の 4 行を実行したうえで `infra/eks` で `terraform plan` を直に実行します。

すでに `terraform.tfvars` がある状態で `distai-up.sh` を再実行すると、`exists; leaving it alone` と表示してファイルには触りません。共有ストレージをあとから有効にするなら、`fsx_enabled` と `openzfs_enabled` を `true` に直してから再実行します。

plan の確認で中止した場合も、state のバケットとロックテーブル、レジストリのパラメータは前のフェーズで作成済みなので残ります。クラスタを作らずにやめるなら自分で消すか、次に同じ名前で作るときにそのまま再利用してください。

::::

## 2. 以降の章の前提はこの 4 行

以降の章はすべて、クラスタを名前とリージョンで指すだけで始まります。バケット名も state のキーも章に書きません。それを可能にしているのが、apply の途中でレジストリ (AWS Systems Manager のパラメータストア) に書いた記録です。

```bash
cd ~/distributed-ai-v0.2.0
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
source infra/scripts/distai-env.sh
```

この 4 行がすることは 2 つです。1 つはレジストリから state の場所 (バケット・キー・ロックテーブル・暗号化キー) とリリースタグを引き、`infra/eks/backend.hcl` が無ければ書き出すことです。すでにある場合は触りません。Terraform が実際に読むのはそのファイルなので既存を優先し、レジストリの記録と食い違うときは警告だけを出します。もう 1 つは `kubectl` をこのクラスタに向けることで、`aws eks update-kubeconfig` の実行、context の選択、既定 namespace の設定、`kubectl` を `k` と打つための定義までが含まれます。章ごとにこれらを打ち直す必要はありません。

1 行目でチェックアウトに移動しているのは、この後の章が `terraform output` を使うためです。リポジトリの外で実行すると別のリポジトリを参照しかねません。別の場所に clone した場合はそのディレクトリに読み替えてください。名前付きプロファイルで認証している場合は、`export AWS_PROFILE=<自分のプロファイル名>` もこの 4 行の前に置きます。

レジストリを経由するのは、state の保存場所を state 自身には書けず、`backend.hcl` は環境固有なのでリポジトリに置けないからです。この 2 つが「クラスタ名だけでは始められない」原因なので、この 2 つの情報だけをレジストリに置いています。クラスタのエンドポイントやサブネット ID はレジストリに入れていません。`terraform output` で引けるものを二重に持つと、どちらが正しいかという問いが生まれるためです。

リージョンを 4 行に含めているのは、クラスタが (アカウント, リージョン, 名前) の 3 つ組で初めて一意になるからです。`AWS_REGION` を省くと AWS CLI の設定を使い、それも無ければ停止します。CLI の既定リージョンがクラスタのリージョンと違う環境では「そのリージョンにそのクラスタは無い」で止まるので、書いておく方が確実です。

この 4 行はレジストリの読み取り、呼び出し元アカウントの確認、クラスタの参照を行うので、`ssm:GetParametersByPath`、`sts:GetCallerIdentity`、`eks:DescribeCluster` の権限が必要です。レジストリが読めないときは、この権限を疑ってください。

:::message
実行すると、対象のクラスタ・リージョン・アカウント・リリースタグ・データ層 (紐づいていなければ `none`) が 1 行で表示されます。データ層はプロファイリング基盤を導入したときに初めて紐づくので、Basic01 では `none` です。認証情報のアカウントがレジストリの記録と食い違う場合はその場で停止します。名前だけで別のクラスタを操作しないための確認です。
:::

::::details 別のマシンで clone し直した場合と、apply が途中で失敗した場合

この 4 行は `backend.hcl` を書き出し、`backend.tf` が無ければ `infra/eks/backend.tf.example` からそれも用意します (どちらもリポジトリには含まれないので、この生成が無いと `terraform init` が S3 の state を見ません)。ただし `terraform init` までは行いません。clone し直した直後は `.terraform` が無いので `terraform output` は `Backend initialization required` で失敗します。その場合は一度 `terraform -chdir=infra/eks init -reconfigure -backend-config=backend.hcl` を実行してください。なお `backend.tf` を作るのは `backend.hcl` を生成するときだけなので、`backend.hcl` だけが残っている作業ディレクトリでは `backend.tf.example` を自分でコピーします。

apply が途中で失敗した場合、レジストリには state の場所までが記録され、リリースタグがまだ無い状態になります。この状態でも解決は通り、リリースタグの位置に `unrecorded` と表示されます。レジストリが作成時と最後の適用のリリースタグを別に持っているのは、古いチェックアウトで新しいクラスタを触ろうとしている状況を検出するためです。

::::

## 3. ノードを確認する

ここで確かめるのは、System ノードグループが立ち上がっていることと、`kubectl` が正しいクラスタを向いていることです。向き先の設定は step 2 で済んでいて、`source` したときに表示された次の 2 行がその結果です。

```text
distai-env: distai-eks in us-east-2 (account <アカウント ID>, release release/eks-distributed-ai/v0.2.0, data layer none)
distai-env: kubectl: context distai-eks, namespace distai at https://XXXXXXXX.gr7.us-east-2.eks.amazonaws.com (the namespace does not exist yet)
distai-env: k is kubectl --context distai-eks; KUBECONFIG is /home/ubuntu/.kube/distai/distai-eks.distai.yaml
```

1 行目で見るのは末尾です。`(unreachable: ...)` が付いていなければ、endpoint への到達と認証まで確認できたことになります。`(the namespace does not exist yet)` は step 4 で作る `distai` namespace がまだ無いという意味なので、この時点では正常です。

kubeconfig は既定の `~/.kube/config` ではなく、クラスタと namespace ごとの専用ファイルに書きます。既定の current-context を書き換えると、別のターミナルで他のクラスタを触っている作業まで巻き込むためです。設定が効くのは `source` したシェルの中だけなので、ターミナルを開き直したら step 2 の 4 行をもう一度実行します。

::::details `k` はどこまで向き先を固定するか

`k` は、クラスタの解決に成功している間は `--context` を付けて `kubectl` を呼ぶ関数です。後から current-context が変わっても向き先はずれません。一度解決に成功したシェルで再度の解決に失敗した場合は `--context` を付けない `kubectl` として動くので、そのときは向き先が固定されません。初回から解決に失敗した場合は `k` そのものが定義されません。

::::

では実際にノードを見ます。

```bash
k get nodes
```

インスタンスタイプまで見たい場合は列を指定します。

```bash
k get nodes -o custom-columns='NAME:.metadata.name,TYPE:.metadata.labels.node\.kubernetes\.io/instance-type,ROLE:.metadata.labels.node-role,CAP:.metadata.labels.karpenter\.sh/capacity-type'
```

実機出力です。system の 2 台は Managed Node Group なので `CAP` を持たず、monitoring の 1 台は Karpenter がオンデマンドで起動したものです。

```text
NAME                                        TYPE        ROLE         CAP
ip-10-0-1-12.us-east-2.compute.internal     c6a.large   monitoring   on-demand
ip-10-0-17-26.us-east-2.compute.internal    m5.xlarge   system       <none>
ip-10-0-54-140.us-east-2.compute.internal   m5.xlarge   system       <none>
```

`k get nodes` で m5 系のノードが 2 台 `Ready` 状態で表示されれば、System ノードグループの起動は成功です。監視スタックを既定のまま有効にしている場合は、これに加えて監視専用 NodePool のノードが 1 台見えるので、合計 3 台になります。この 3 台目は Karpenter が監視 Pod の `Pending` を見てから起動するので、apply 完了の数分後に現れます。2 台しか見えない時間があるのは失敗ではありません。10 分待っても増えない場合は `k get nodeclaims` と `k -n karpenter logs deploy/karpenter --tail=50` を見ます。

:::message alert
`kubectl` が `Unauthorized`（`error: You must be logged in to the server`）で拒否される場合、原因はほぼ 2 つです。1 つ目は、クラスタを作ったプリンシパルと、いま `kubectl` を実行しているプリンシパルが違うケースです。`enable_cluster_creator_admin_permissions = true` は作成したプリンシパルにだけ管理者権限を与えるので、別のプリンシパルからは拒否されます。よくあるのは、`distai-up.sh` を名前付きプロファイルで実行したのに、`source` するシェルで `AWS_PROFILE` を設定し忘れ、プロファイル指定なしの `[default]` で認証している場合です。`source` は `AWS_PROFILE` をそのまま kubeconfig に書き込むので、`export AWS_PROFILE=<name>` を 4 行の前に置き、`aws sts get-caller-identity` で両者のプリンシパルを確認してください。assume-role の場合はセッション名部分が違っていても問題なく、`assumed-role/<ロール名>` までが一致していれば認証は通ります（アクセスエントリは基底の IAM ロール ARN 単位でマッチするためです）。自分のロールが登録済みかは `aws eks list-access-entries --cluster-name <name>` でも確認できます。2 つ目は、`apply` 直後にアクセスエントリがまだ認証レイヤに伝播していないケースで、この場合は 1〜2 分待って再実行すれば通ります。
:::

## 4. 作業用の namespace を作る

本書のワークショップでは、学習 Job や推論サーバーなどのワークロードを `default` ではなく専用の namespace に作ります。あとで「この namespace ごと消せば実験の後片付けが済む」ようにするためです。本書では作業用 namespace を `distai` に統一して進めます。

以降の章はこの名前を `NAMESPACE` で受け取る形に揃えているので、ここでも環境変数に入れてから作ります。別の名前で進めたい場合は、この 1 行だけを変えれば以降のコマンドはそのまま使えます。作成は `--dry-run` 経由の `apply` にしていて、すでに存在していてもエラーになりません。

```bash
export NAMESPACE=distai
k create namespace "$NAMESPACE" --dry-run=client -o yaml | k apply -f -
```

`namespace/distai created`（初回）または `namespace/distai unchanged`（2 回目以降）と表示されれば準備完了です。本書は既定としてこの `distai` を使います (章によって別の namespace を使いたい場合の切り替え方は次に触れます)。作成後に step 2 の 4 行をもう一度実行すると、先ほどの `(the namespace does not exist yet)` が消えます。

## 5. 向き先を確認する習慣をつける

操作対象のクラスタやリソースが増える後続の章では、別のクラスタを向いたまま元のつもりで操作してしまう事故が起きやすいため、破壊的な操作の前には現在の向き先を確認する習慣をつけておきます。`source` したときに表示される 2 行と同じ内容を、いつでも自分で確認できます。

```bash
k config current-context

k config view --minify -o 'jsonpath={.contexts[0].context.namespace} @ {.clusters[0].cluster.server}{"\n"}'
```

別のクラスタに切り替えたいときは、`CLUSTER_NAME` を変えて step 2 の 4 行をもう一度実行します。クラスタごとに kubeconfig ファイルが分かれているので、`k config use-context` で切り替えようとしても、いま使っている kubeconfig の中に別のクラスタの context は居ません。`distai` 以外の namespace を既定にしたい章では、`source` の前に `export DISTAI_NAMESPACE=<name>` を置くか、コマンド側で `-n <name>` を明示します。

## 6. (任意) スモークテストで動作確認する

`apply` が通っても、その上のコンポーネントが動いているとは限りません。それを早めに切り分けるために、リポジトリの [`infra/eks/tests/`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/tests) にスモークテストを置いています。

対象クラスタは `kubectl` の向き先と `terraform output` から解決するので、step 2 の 4 行を同じシェルで実行済みにしてから走らせてください。

テストは自分専用の namespace (`distai-test`) を作って最後に消します。手順 4 で `NAMESPACE` を export しているので、その名前を渡さないよう `--namespace` で明示します。渡さないと、テストが作業 namespace を自分のものと解釈して停止します。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/tests
./run-tests.sh --namespace distai-test
```

引数なしで走るのは `baseline` という一群で、3 つの層に分かれています。

- **static**: AWS に触らず、チャートのレンダリング結果や `terraform validate` を見ます
- **live-ro**: クラスタを読むだけです (control-plane、system-nodes、karpenter、csi-drivers、device-plugins、trainer)
- **live-mut**: 実際に作って消します (`storage-mount` が FSx と OpenZFS に読み書きします)

GPU ノードは起動しません。全部で 38 項目、3 分前後で終わります。

出力の末尾に集計が出ます。

```text
--------------------------------------------------------------
PASS: 37  FAIL: 0  SKIP: 1  TOTAL: 38
```

`SKIP` の 1 件は `registry-default-layer-attached` で、Advanced02 のプロファイリング基盤を導入するまでデータ層が紐づかないため「該当なし」になります。この時点では正常です。`device-plugins` は GPU/EFA/Neuron の device plugin を見る項目ですが、該当プールが無い段階では対象が存在しないので PASS します。手順 1 で `DISTAI_SHARED_STORAGE=off` を選んだ場合は、複製元の PV が無いので `storage-mount` が SKIP になり、`PASS: 36 SKIP: 2` になります。

FAIL があった場合は、その項目名で `infra/eks/tests/cases/` を探すと、何を assert しているかがそのまま読めます。

GPU ノードを起動して行う GPU スモークテストは、アクセラレータプールを定義する Basic04 で扱います。

# まとめ

本章では、分散 AI の実験を行うための基盤として Amazon EKS クラスタを構築し、以降のワークショップで使う作業用 namespace `distai` を作成しました。中核として作ったのは Amazon VPC・Amazon EKS コントロールプレーン・System ノードグループの 3 つで、同じ apply で載る Karpenter を Basic03 で掘り下げ、Basic04 以降でアクセラレータプールを積み上げていきます。

ここで一度中断する場合は、この時点で EKS コントロールプレーン・System ノード・NAT Gateway・Interface endpoint・共有ストレージ・監視スタックが動いたままになります。先に進まないなら Basic11 の破棄手順で片付けてください。state を置く S3 バケットとレジストリ (AWS Systems Manager Parameter Store) は破棄後も残る設計です。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
