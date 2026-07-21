---
title: "Basic01 - EKS 基盤を立てる"
free: true
---

本章では、分散学習・推論の実験を回すための土台として EKS クラスタを構築します。Terraform で VPC・EKS コントロールプレーン・Karpenter を動かすための System ノードグループをデプロイし、`kubectl` でノードが見えるところまでを扱います。GPU/Neuron のアクセラレータノード自体は次章以降で立てるため、ここでは「あとから何度でも実験を回せる足場」を一度だけ作ります。

:::message alert
本資料は `us-east-2` リージョンを例に説明します。実際には自身で選択したリージョンに読み替えて進めてください。コマンド中の `<region>` などのプレースホルダは自分の値に置き換えます。
:::

# 解説

## 全体構成

この book 全体で構築する分散 AI 基盤の全体像です。VPC の中に 2 つの AZ を張り、EKS コントロールプレーンの下で Karpenter が GPU/Neuron の各 NodePool を要求に応じて起動します。共有ストレージ（EFS / FSx）や Capacity Block の期限監視（EventBridge → SNS）といった周辺サービスも含めた構成です。各コンポーネントは以降の章で 1 つずつ扱います。

![EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で作るのは、この図のうち **VPC・EKS コントロールプレーン・System ノードグループ** の 3 つだけです。アクセラレータノードや各種アドオンは後続の章で積み上げていきます。

## これは何をするものか

本章のゴールは、GPU/Neuron を使った分散学習・推論の実験を後からいくらでも回せる「土台」を一度だけ立てることです。土台とは具体的に、EKS コントロールプレーンと、その上で Karpenter コントローラを動かすための最小構成（VPC + System ノードグループ）を指します。アクセラレータノード自体はまだ立てません。Ch3 以降で `accelerator_pools` に 1 行足すだけで GPU/Neuron ノードが要求に応じて立つよう、Karpenter の足場をここで用意しておく、という位置づけです。

構成要素は次の 3 つだけで、いずれも「実験を始める前に一度作れば、あとは触らない」部分です。

- **EKS コントロールプレーン**（Kubernetes 1.35 / Pod Identity）
- **System managed node group**（m5 系 x2）: kube-system と Karpenter コントローラ自身を載せる足場です。Karpenter はここに自分のノードグループを作らない（ラベルで自己参照を避ける）ため、Karpenter が動き出すための最初の土台としてこのノードグループが要ります
- **VPC**: 上記を収めるネットワーク

この土台づくり自体は EKS の一般的な手順とほぼ同じで、特別なことはほとんどありません。ただし分散 AI 特有の注意点が 2 つあるので、そこだけ押さえておきます（詳しくは末尾の「注意」節を参照してください）。

## 全体の中での位置付け

本章は基盤構築の最下層にあたります。ここで作った EKS + System ノードの上に、次章で Karpenter コントローラを載せ（Ch2）、その Karpenter が `accelerator_pools` の定義に従って GPU/Neuron ノードを起動する（Ch3 以降）、という順で積み上がっていきます。つまり本章は「まだ何も GPU が動かない」状態を作る章ですが、これ以降のすべての章がこの土台の上に成り立ちます。

## 実際に挙動を確認する

`terraform apply` が完了すると、System ノードグループの m5 系ノードが 2 台 `Ready` になります。この時点ではまだ Karpenter も GPU/Neuron ノードも存在しないため、`kubectl get nodes` で見えるのはこの 2 台だけです。

```
NAME                            STATUS   ROLES    AGE   VERSION
ip-10-0-xx-xx.<region>...      Ready    <none>   5m    v1.35.x-eks-...
ip-10-0-yy-yy.<region>...      Ready    <none>   5m    v1.35.x-eks-...
```

この 2 台が Karpenter コントローラと kube-system の載る足場になります。具体的な確認コマンドは後述の「ワークショップ実施」で扱います。

## 注意

分散 AI 特有の落とし穴が 2 つあります。いずれも一度ハマると原因特定に時間がかかるため、最初に押さえておきます。

**注意 1: VPC の IP アドレスは大きめに確保する。** アクセラレータノードは通常の CPU ノードより桁違いに IP を消費します。VPC CNI がノードごとに secondary IP を配るのに加え、EFA（Elastic Fabric Adapter）対応インスタンスは EFA 専用 ENI を多数持ちます（`p5en.48xlarge` は 16 枚、`p5.48xlarge` は 32 枚）。ENI 1 枚ごとに IP を消費するため、小さな CIDR だとノード数台で枯渇し、最悪 EKS コントロールプレーンが管理 ENI を置けず `IMPAIRED`（`InsufficientFreeAddresses`）に陥ります。後から広げにくい失敗なので、この構成では VPC を `/16`、プライベートサブネットを AZ ごとに `/18` と大きめに取り、パブリックは NAT/LB 用途のみなので `/24` にしています。「パブリックは小さく、プライベートは大きく」は `awslabs/awsome-distributed-ai` の HyperPod-EKS リファレンスにも見られる原則です。

**注意 2: `karpenter.sh/discovery` タグをパブリックサブネットに漏らさない。** このタグは Karpenter が「ノードを起動してよいサブネット」を検出するための目印です。VPC モジュールの共通タグに含めてしまうと全サブネットに伝搬し、パブリックサブネットにも付いてしまいます。すると Karpenter がパブリックサブネットにノードを立て、そのノードは IGW 経由のルートしか持たず EC2 API に到達できないため `nodeadm` によるクラスタ参加に失敗して詰みます。この構成ではこのタグをプライベートサブネットとノードセキュリティグループにだけ明示的に付け、VPC 共通タグからは意図的に外しています。

なお、必須アドオン `vpc-cni` と `eks-pod-identity-agent` は `before_compute = true` でワーカーノード起動前に導入します。特に Pod Identity Agent は、Pod Identity で権限を得るコントローラ（EBS CSI ドライバなど）より先に存在していないと、そのコントローラが起動時に EC2 IMDS ロールを見つけられずクラッシュし、アドオンが `CREATING` のまま止まります。

# ワークショップ実施

## 1. tfvars を準備する

`terraform.tfvars.example` を `terraform.tfvars` にコピーし、`region` / `azs` / `cluster_name` を自分の環境に合わせて設定します。この段階では `accelerator_pools` は空のままで構いません（アクセラレータプールは Ch3 以降で扱います）。

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

`terraform apply` の内訳は、VPC・NAT ゲートウェイ・EKS コントロールプレーン・System ノードグループの作成です。コントロールプレーンの起動が最も時間を要します。

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

本章では、分散 AI の実験を回すための土台として EKS クラスタを構築しました。作ったのは VPC・EKS コントロールプレーン・System ノードグループの 3 つで、この上に次章から Karpenter とアクセラレータプールを積み上げていきます。VPC は大きめの CIDR を確保し、`karpenter.sh/discovery` タグをパブリックサブネットに漏らさない、という 2 点だけ押さえておけば、あとは一般的な EKS 構築とほぼ同じです。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-training)（VPC サイジングの参考にした HyperPod-EKS リファレンス）
- [EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
