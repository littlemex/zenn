---
title: "Basic11 - 安全な破棄とオプション機能"
free: true
---

本章では、これまで積み上げてきた Amazon EKS 基盤を安全に破棄する仕組みと、オプションで有効化できる外部公開エンドポイント（Amazon CloudFront → Application Load Balancer → Amazon EKS）を扱います。`terraform destroy` がアクセラレータノードを取り残して課金が続く事故を防ぐ設計を理解し、実際に手を動かして破棄の過程を観察します。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図で構築してきたリソース全体を安全に取り壊す仕組みと、図には含まれないオプションの外部公開経路（Amazon CloudFront・AWS Load Balancer Controller によるデモアプリ配信）です。本章は Basic01 から積み上げてきた Amazon VPC・Amazon EKS コントロールプレーン・Karpenter・各アクセラレータプール・共有ストレージを、課金を取り残さずに安全に取り壊す方法を扱う、基盤構築の最終章にあたります。構築の逆方向（破棄）と、任意で足す周辺機能の 2 つを押さえます。破棄とオプション機能は独立した話題ですが、いずれも「基盤を運用し続ける中で、いつか必要になる」という共通点で本章にまとめています。

## これは何をするものか

`kubectl_manifest` の削除は、Kubernetes API がリクエストを**受理した瞬間**に Terraform 上で「完了」として扱われます。しかし `NodePool`/`NodeClaim` の実際のノード drain・Amazon EC2 終了・ENI 解放は、Karpenter コントローラが行う非同期処理です。この非同期処理が終わる前に Karpenter や GPU Operator・EFA/Neuron device plugin・Amazon EFS/Amazon FSx for Lustre CSI ドライバなど、ノードに紐づくリソースを持つコントローラを destroy してしまうと、アクセラレータノードの Amazon EC2 インスタンスが孤立し、誰も終了させないまま**課金だけが続く**事故になります。GPU/Neuron は時間単価が高く、このリスクは軽視できません。

これを防ぐのが [`karpenter.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/karpenter.tf) の `null_resource.wait_for_node_drain` です。この対処の過程で NAT ゲートウェイの早期消失や IAM 残渣といった追加の障害が見つかり、[`vpc-endpoints.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/vpc-endpoints.tf) での対処も必要になりました。以降で実際のコードを引用しながら、なぜその設計にしているのかを見ていきます。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) です。

## wait_for_node_drain のポーリング

`null_resource.wait_for_node_drain` は destroy 時にのみ動く `provisioner "local-exec"`（`when = destroy`）です。中核はポーリング用の関数で、`kubectl get <resource_type> --no-headers` の結果が空になるまで繰り返します。

```bash
# karpenter.tf（抜粋）
wait_for_empty() {
  local resource_type="$1" label="$2" max_attempts="$3"
  for i in $(seq 1 "$max_attempts"); do
    local err_file
    err_file=$(mktemp)
    local out
    if ! out=$(kubectl get "$resource_type" --no-headers 2>"$err_file"); then
      local err
      err=$(cat "$err_file"); rm -f "$err_file"
      if printf '%s\n' "$err" | grep -q "doesn't have a resource type"; then
        echo "wait_for_node_drain: the $label CRD no longer exists, nothing left to wait for."
        return 0
      fi
      echo "wait_for_node_drain: kubectl error listing $label (transient?): $err — retrying"
    else
      rm -f "$err_file"
      local count
      count=$(printf '%s\n' "$out" | grep -c . || true)
      if [ "$count" = "0" ]; then
        echo "wait_for_node_drain: no $label remain."
        return 0
      fi
      echo "wait_for_node_drain: $count $label still present (attempt $i/$max_attempts)..."
    fi
    sleep 10
  done
  return 1
}
```

読みどころは `stdout` と `stderr` を分けて捕捉している点です。`kubectl get` は結果が 0 件でも exit 0 のまま「No resources found」を**標準エラー**に書きます。これを `2>&1` でまとめて捕捉すると、そのメッセージが 1 行としてカウントされてしまい、実際には 0 件なのに「まだ 1 件残っている」と永久に判定してしまうバグを踏みました（別セッションで `kubectl get` すると 0 件なのに、このループだけ 15 分以上「1 件残存」と報告し続けていたことで発覚しました）。`err_file` に `stderr` だけを分離して捕捉することでこれを防いでいます。

provisioner の実行フローは、事前分岐 2 つとポーリング 3 段階に分かれます。

- クラスタ存在確認: `aws eks describe-cluster` が失敗する（クラスタが既に存在しない）場合、ポーリング不要として即 `exit 0` します。
- kubeconfig 取得: クラスタは存在するのに `aws eks update-kubeconfig` が失敗する場合、状態を確認できないため安全側で `exit 1` します。
- TrainJob 事前削除（best-effort）: NodeClaim を待つ前にクラスタ全体の TrainJob を削除します。
- NodeClaim 待ち（必須）: 最大 30 分ポーリングし、タイムアウトすれば destroy を失敗させます。
- EC2NodeClass 待ち（best-effort）: 最大 10 分ポーリングし、タイムアウトしても destroy を続行します。

呼び出し側のコードは次のとおりです。

```bash
# karpenter.tf（抜粋）
if ! aws eks describe-cluster --name "${self.triggers.cluster_name}" --region "${self.triggers.region}" ... >/dev/null 2>&1; then
  echo "wait_for_node_drain: cluster ${self.triggers.cluster_name} no longer exists, skipping drain wait"
  exit 0
fi
aws eks update-kubeconfig --name "${self.triggers.cluster_name}" --region "${self.triggers.region}" ... --kubeconfig "$KCONF" >/dev/null 2>&1 \
  || { echo "wait_for_node_drain: cluster exists but update-kubeconfig failed..." >&2; exit 1; }

# Kubeflow Trainer v2 の TrainJob をクラスタ全体から先に削除する(best-effort)。
# 素の terraform destroy では 04-teardown.sh の namespace 限定削除を経由しないため、
# ここで削除しておかないと TrainJob の Pod がノードを占有し続け NodeClaim 待ちが
# 30 分タイムアウトしてしまう。
echo "wait_for_node_drain: deleting any TrainJobs before draining (best-effort)..."
kubectl delete trainjob --all --all-namespaces --ignore-not-found=true --timeout=120s 2>/dev/null || {
  # finalizer 強制解除フォールバック
  for tj in $(kubectl get trainjob --all-namespaces -o jsonpath='...' 2>/dev/null); do
    kubectl -n "$ns" patch trainjob "$name" --type=merge -p '{"metadata":{"finalizers":[]}}' 2>/dev/null || true
  done
}

echo "wait_for_node_drain: waiting for Karpenter to terminate all accelerator NodeClaims..."
if ! wait_for_empty "nodeclaims.karpenter.sh" "NodeClaim(s)" 180; then
  echo "wait_for_node_drain: NodeClaims still present after 30 minutes. Refusing to proceed..." >&2
  exit 1
fi

echo "wait_for_node_drain: waiting for Karpenter to clear EC2NodeClass finalizers (best-effort)..."
if ! wait_for_empty "ec2nodeclasses.karpenter.k8s.aws" "EC2NodeClass(es)" 60; then
  echo "wait_for_node_drain: EC2NodeClasses still present after 10 minutes..." >&2
fi
exit 0
```

TrainJob の削除は `--timeout=120s` を付けた best-effort で、失敗してもフォールバックで finalizer を強制的に外し、先に進みます。これは `04-teardown.sh` が行う namespace 限定の TrainJob 削除とは別に、`terraform destroy` を直接叩いた場合でもクラスタ全体から TrainJob を確実に片付けるための保険です。

`NodeClaim`/`EC2NodeClass` はいずれも 10 秒間隔でポーリングしますが、最大回数（180 回=30 分／60 回=10 分）とタイムアウト時の扱い（`exit 1`／`exit 0`）が異なります。この非対称な扱いの理由は次のセクションで説明します。ドレイン時間はノード数やインスタンスタイプで変動するため（単一ノードの実測で概ね 9 分）、固定 sleep ではなく実状態を見る設計にしています。

この `null_resource` は次のリソース群すべてに `depends_on` しています。

```hcl
# karpenter.tf（抜粋）
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
```

`helm_release.trainer`（Kubeflow Trainer v2 のコントローラ）が含まれるのは、TrainJob の worker Pod がアクセラレータノード上で動くためです。`helm_release.openzfs_csi_driver` が含まれるのも同じ理由で、Amazon FSx for OpenZFS の PV がアクセラレータノードの Pod にマウントされているうちにドライバを消してしまうと解放処理が失敗するリスクがあるためです。

Terraform の destroy は `depends_on` の**逆順**に進みます（A が B に `depends_on` していれば、destroy は A → B の順）。つまりこの一覧があることで、destroy 順序は必ず「`wait_for_node_drain`（ポーリングが走る）→ Karpenter/GPU Operator/EFA plugin/Neuron/Kubeflow Trainer v2/Amazon EFS・Amazon FSx for Lustre・Amazon FSx for OpenZFS CSI/EFA セキュリティグループ/placement group」の順に強制されます。もう一方の端、`NodePool`/`NodeClaim` の manifest 側は逆に `karpenter-resources.tf` でこの `null_resource` に `depends_on` しており、`NodePool` の削除が先に issue されてからポーリングが始まる形です。全体は「NodePool 削除 → この resource（待つ） → 各コントローラ破棄」という一方向の直線になり、循環は発生しません。

## Amazon VPC endpoints で NAT 依存を切る

もう一つ見つかった障害が NAT ゲートウェイの早期消失です。NAT と Karpenter の間に明示的な依存関係がないため、`module.vpc` の NAT ゲートウェイがポーリング中に先に消えることがあります。Karpenter コントローラは private subnet で動き、Amazon EC2/IAM/STS/SSM への API 呼び出しを NAT 経由のアクセスに依存しているため、NAT 消失と同時に API がすべてタイムアウトし、`NodeClaim` の finalizer が外れず 30 分のタイムアウトに達してしまいます。`wait_for_node_drain` を `module.vpc` に直接 `depends_on` させる案でも destroy の順序自体は守れます（`wait_for_node_drain → module.vpc` は既存の `wait_for_node_drain → module.eks → module.vpc` と同方向で、循環になるとしたらその逆方向の `module.vpc → wait_for_node_drain` のはずです）。しかしこれは module 全体を粗い単位で待たせるだけで、初回 apply 時の安定性（後述の `eks-auth` の問題）は別に解決する必要があります。そこで、destroy 時の順序と初回 apply 時の安定性を同時に解決できる、ネットワーク層そのものを NAT 非依存にする `vpc-endpoints.tf` の VPC endpoint 案を採用しました。

```hcl
# vpc-endpoints.tf（抜粋）
locals {
  vpc_endpoint_services = ["ec2", "sts", "ssm", "ecr.api", "ecr.dkr", "logs", "eks-auth"]
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(local.vpc_endpoint_services)

  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
}

# Gateway endpoint（時間課金なし、ENI も持たない）
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids
}
```

Amazon EC2・STS・SSM に加え、`ecr.api`・`ecr.dkr`・`logs`・`eks-auth` の Interface VPC endpoint と Amazon S3 の Gateway endpoint を作成し、NAT の有無に関わらずクラスタ内の AWS API 呼び出しが動くようにします。`ecr.api`/`ecr.dkr` は private subnet のノードが EKS 管理下のイメージ（VPC CNI・kube-proxy・EFA/GPU device plugin など）を NAT に頼らず pull できるようにするためで、`ecr.dkr` が取得するレイヤー自体は後述の Amazon S3 Gateway endpoint 経由になります。`logs` は Amazon CloudWatch Logs へのノード/Pod ログ送信用です。

`eks-auth` は Pod Identity の認証そのものに不可欠です。EKS Pod Identity は IRSA と異なり、STS の `AssumeRoleWithWebIdentity` ではなく、Pod Identity Agent が EKS Auth API（`AssumeRoleForPodIdentity`、STS とも EKS 本体とも別のサービスプリンシパル）を呼んで認証情報を取得します。`eks-auth` の endpoint が無いと、private subnet の Pod Identity 利用者は NAT 経由でしかこの API に到達できません。しかも NAT ゲートウェイは他リソースと並行して作られるため、初回 apply では NAT が 1 つも無い時点で `aws-ebs-csi-driver` などの addon が起動を試みることがあります。実際に一から構築した際、この状態で addon が 17 分間 `CREATING` に留まり、コントローラ Pod が「認証情報の更新に失敗した」というエラーで `CrashLoopBackOff` した事例がありました。IAM ロール・信頼ポリシー・Pod Identity association・agent はすべて正しく設定されていたにもかかわらず起きた障害で、`eks-auth` endpoint を追加することで解消しています。

`wait_for_node_drain` はこれらの endpoint にも `depends_on` しているため（前節のリスト参照）、ポーリング中は消えません。Gateway endpoint（Amazon S3）は時間課金なし・ENI も持ちませんが、Interface VPC endpoint（Amazon EC2・STS・SSM・ECR API/DKR・CloudWatch Logs・EKS Auth）は AZ ごとに ENI を持ち常時の時間課金が発生します。破棄時の安全性と初回構築時の安定性を、恒久的な少額コストと引き換えに買っている設計です。

ただし IAM はここに含まれていません。IAM はグローバルサービスで、`us-east-1` を除きリージョン単位の Interface endpoint を持たないためです。本書のワークショップが使う `us-west-2`/`us-east-2` では、`aws ec2 describe-vpc-endpoint-services` で確認すると `ec2`/`ec2-fips`/`ssm*`/`sts`/`sts-fips` は列挙されますが `iam` は存在せず、`com.amazonaws.<region>.iam` への `aws_vpc_endpoint` 作成は `InvalidServiceName` で失敗します。そのため Karpenter の `EC2NodeClass` 終了処理が呼ぶ `ListInstanceProfiles`（IAM API）は、NAT 消失後もタイムアウトし得ます。これが前節で `EC2NodeClass` のポーリングだけをベストエフォート（最大 10 分・タイムアウトしても `exit 0`）にしている理由です。課金停止に直結する `NodeClaim` の消失を待つのは必須としつつ、IAM という他手段のない経路に阻まれる可能性がある `EC2NodeClass` finalizer の解除まで destroy 全体を止めるのは実用的でないと判断しています。Amazon EC2 インスタンス自体は 1 段目の時点で終了済みであり、課金に影響しないオブジェクトのために destroy を止めるより先に進む方が合理的です。

## Amazon CloudFront デモ（オプション機能）の2段階 apply

これまでの破棄の話とは別に、オプション機能として外部公開エンドポイントのデモを用意しています（[`alb-controller.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/alb-controller.tf) / [`cloudfront.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/cloudfront.tf)）。`var.enable_demo_app`（既定 `false`）でゲートされ、`Client → Amazon CloudFront (HTTPS) → Application Load Balancer (HTTP/80) → Amazon EKS Pod` という経路をとります。

AWS Load Balancer Controller 自体の権限付与は、Basic03 の Karpenter や Basic01 の EBS CSI ドライバと同じ Pod Identity パターンです。

```hcl
# alb-controller.tf（抜粋）
data "http" "alb_iam_policy" {
  count = local.demo_app_enabled
  url   = "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v${var.alb_controller_app_version}/docs/install/iam_policy.json"

  lifecycle {
    postcondition {
      condition     = self.status_code == 200
      error_message = "Failed to fetch ALB controller IAM policy (HTTP ${self.status_code})..."
    }
  }
}

resource "aws_eks_pod_identity_association" "alb_controller" {
  count           = local.demo_app_enabled
  cluster_name    = module.eks.cluster_name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.alb_controller[0].arn
}
```

IAM ポリシーを固定 JSON でハードコードせず、アップストリームの GitHub タグから `data.http` で都度取得している点が特徴です。`lifecycle.postcondition` で HTTP ステータス 200 を検証しているのは、`var.alb_controller_app_version`（chart バージョンとは別変数）のタグ typo や GitHub 障害を plan 時点で検出するためです。

主眼は多層防御で、Layer 1 は Application Load Balancer のセキュリティグループを Amazon CloudFront のマネージド prefix list のみに絞るネットワーク制限です。

```hcl
# cloudfront.tf（抜粋）
data "aws_ec2_managed_prefix_list" "cloudfront_origin" {
  count = local.cf_enabled
  name  = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  count             = local.cf_enabled
  security_group_id = aws_security_group.alb_cloudfront_only[0].id
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin[0].id
}
```

Layer 2 は Amazon CloudFront が付与する `X-Origin-Verify` ヘッダーをアプリケーション層で検証する制限です。実際に検証を行うのは Application Load Balancer のリスナールールで、Ingress の `conditions` アノテーションはそのルールへヘッダー条件を設定する手段にあたります。

```hcl
# cloudfront.tf（抜粋、Phase 2 のみ付与）
"alb.ingress.kubernetes.io/security-groups" = aws_security_group.alb_cloudfront_only[0].id
"alb.ingress.kubernetes.io/conditions.echo" = jsonencode([{
  field = "http-header"
  httpHeaderConfig = {
    httpHeaderName = "X-Origin-Verify"
    values         = [random_password.origin_verify[0].result]
  }
}])
```

Layer 1（SG）を通過しない直接アクセスはそもそも Application Load Balancer に到達せずタイムアウトし、Layer 1 を通過してもヘッダーを持たないリクエストは Application Load Balancer のデフォルトルールで 404 になります。ヘッダーの値は `random_password` でランダム生成し、Terraform state にのみ保持される（Git にはコミットしない）ため、Amazon CloudFront 経由以外からの偽装は困難です。

デプロイは 2 段階です。Phase 1（`enable_demo_app=true`）で AWS Load Balancer Controller と demo アプリを作り、Application Load Balancer への直接アクセスで経路そのものを確認します。Phase 2（`enable_cloudfront=true` を追加）で Amazon CloudFront・SG 制限・ヘッダー条件をまとめて適用し、Application Load Balancer への直接アクセスがタイムアウトすることを確認します。もう一点、Phase 2 → Phase 1 のロールバック（`enable_cloudfront` を `true` → `false`）に備えたコードもあります。

```hcl
# cloudfront.tf（抜粋）
resource "aws_security_group" "alb_cloudfront_only" {
  # ...
  # ALB Controller の実際の SG デタッチは非同期で、Ingress の
  # security-groups アノテーション更新が受理された後も数秒間 ENI に
  # 付いたままになりうる。time_sleep だとこの SG の id を Ingress が
  # 参照する関係で循環依存になるため、delete タイムアウトを延ばして
  # AWS provider 側の DependencyViolation リトライに任せている。
  timeouts {
    delete = "5m"
  }
}
```

AWS Load Balancer Controller の SG デタッチが非同期であることに由来する `DependencyViolation` を避けるため、`time_sleep` ではなく delete タイムアウトの延長で対処しています。`time_sleep` を使わない理由は、この SG の `id` を Ingress 側が参照しているため、そちらで依存関係を組むと循環依存になってしまうからです。

同種の非同期完了待ちの問題は `null_resource.demo_ingress_finalizer` にも見られます。`kubectl_manifest.demo_ingress` の削除は Kubernetes API がリクエストを受理した瞬間に Terraform 上で「完了」扱いになりますが、実際に ALB を消すのは AWS Load Balancer Controller の finalizer 処理で、これは非同期に続きます。この resource は `helm_release.alb_controller` に `depends_on` することで、destroy の順序を「Ingress 削除 → この resource（待つ） → ALB Controller 破棄」に固定します。

```bash
# cloudfront.tf（抜粋、null_resource.demo_ingress_finalizer の local-exec）
for i in $(seq 1 60); do
  if ! OUT=$(aws elbv2 describe-load-balancers --region "$REGION" \
        --query "length(LoadBalancers[?VpcId=='$VPC_ID'])" --output text 2>/dev/null); then
    echo "wait_for_alb_deletion: describe-load-balancers failed (transient?) — retrying"
  elif [ "$OUT" = "0" ]; then
    echo "wait_for_alb_deletion: no load balancers remain in $VPC_ID."
    exit 0
  else
    echo "wait_for_alb_deletion: $OUT load balancer(s) still present (attempt $i/60)..."
  fi
  sleep 10
done
```

これはもともと `time_sleep`（`destroy_duration = "20s"`）でした。しかし実機の teardown で ALB の削除自体が数分かかることが判明し、20 秒では全く足りませんでした。固定 20 秒が過ぎた時点でまだ ALB が存在するのに ALB Controller を先に破棄してしまうため、finalizer を外す担当がいなくなり、ALB は ENI を public subnet に付けたまま `active` で取り残されます。これが `module.vpc.aws_subnet.public` の削除を 18 分間ブロックし、`DependencyViolation` で destroy 全体を失敗させ、課金の残る ALB を残す結果になりました。現在は対象 VPC 内の ALB が `aws elbv2 describe-load-balancers` で 0 件になるまで 10 秒間隔・最大 60 回（最大 10 分）ポーリングするベストエフォート方式に置き換えており、この章で繰り返し出てくる「Kubernetes API の受理と実際の完了は別物」というテーマの一例になっています。

# ワークショップ実施

破棄はいちばん最後の操作なので、先にオプション機能のデモ（手順 2）を試してから、ドレインと全体破棄（手順 3 以降）に進みます。デモが不要であれば手順 3 から始めて構いません。

## 1. 前提を確認する

- 共有クラスタでは実行前に `k config current-context` を必ず確認します。この章の操作はクラスタ全体またはアクセラレータプール全体に影響する破壊的操作であり、意図しないコンテキストへの誤実行を避けます。
- 作業用 namespace は Basic01 以降のワークショップで使ってきた `distai` を使います。
- 作業ディレクトリは本章のコマンドの基準になる `infra/eks` に固定します。
- `enable_demo_app`/`enable_cloudfront` は既定でどちらも `false` です。手順 2 のデモを試さずに破棄だけ行う場合、この確認は不要です。

```bash
k config current-context
cd infra/eks
export NAMESPACE=distai
grep -E "enable_demo_app|enable_cloudfront" terraform.tfvars* 2>/dev/null \
  || echo "(enable_demo_app/enable_cloudfront は未設定 = 既定の false)"
```

## 2. （オプション）Amazon CloudFront デモを試す

破棄する前に、外部公開エンドポイントのオプション機能を試します。手順 1 で移動した `infra/eks` のまま 2 段階で apply します。

```bash
# Phase 1: ALB Controller + demo app
# demo app の namespace は var.demo_namespace（既定 "demo"）で決まります。
terraform apply -var enable_demo_app=true
k get ingress -n demo -w
```

`ingress` に `ADDRESS` が入っても、Application Load Balancer 自体のプロビジョニングとターゲット登録はまだ続いています。この間の `curl` はタイムアウトするので、ターゲットが `healthy` になるのを待ってから叩きます。

```bash
ALB=$(k get ingress -n demo echo -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

# ターゲットが healthy になるまで待つ（初回は数分かかります）
TG=$(aws elbv2 describe-target-groups \
  --query "TargetGroups[?contains(TargetGroupName,'echo')].TargetGroupArn" --output text)
aws elbv2 describe-target-health --target-group-arn "$TG" \
  --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State,TargetHealth.Reason]' --output text

curl -i "http://${ALB}/"
```

:::message alert
ターゲットが `unhealthy` のまま `Target.Timeout` で止まり、`curl` が 504 を返す場合はセキュリティグループを疑ってください。本書の実装で実際に踏んだ 2 つの原因があります（どちらも修正済みですが、自分で構成を変えたときに再発し得ます）。詳細は下記の details を参照してください。
:::

::::details ターゲットが unhealthy になる 2 つの既知原因
1 つ目は、Karpenter が選ぶセキュリティグループに `kubernetes.io/cluster/<クラスタ名>` タグを持つものが 2 つ以上あるケースです。AWS Load Balancer Controller は Pod の ENI からセキュリティグループを 1 つに決められないと、バックエンド側の許可ルールを作るのを諦めて 15 秒ごとに再試行し続けます。コントローラのログにこう出ます。

```text
expected exactly one securityGroup tagged with kubernetes.io/cluster/<クラスタ名>
for eni eni-..., got: [sg-..., sg-...]
```

Application Load Balancer は `active` になり、ノードも `Ready` なので、症状はここを指しません。次で確認できます。

```bash
k logs -n kube-system -l app.kubernetes.io/name=aws-load-balancer-controller --tail=20 \
  | grep -i "exactly one securityGroup"
```

本書の実装は、`karpenter.sh/discovery` タグをノード用のセキュリティグループだけに付けることでこれを避けています。EKS が作るクラスタ用セキュリティグループにこのタグを足すと、Karpenter が両方をノードに付けてこの状態になります。`terraform plan` が同じ検査を持っているので、自分でタグを増やした場合は plan の警告として出ます。

2 つ目は、ノード用セキュリティグループの自己参照ルールがポート 80 を含まないケースです。`terraform-aws-eks` の既定は TCP 1025-65535 だけを許可するので、Pod が 80 番のような低いポートで listen していると、**別ノードの Pod からの通信だけが落ちます**。同じノードで動く kubelet の readiness probe は途中にセキュリティグループを挟まないので成功し続け、Pod は `Ready` に見えます。本書の実装はノード間で全ポートを許可して解消しています。
::::

```bash
# Phase 2: CloudFront + SG制限 + ヘッダー検証を追加
terraform apply -var enable_demo_app=true -var enable_cloudfront=true
curl -s -o /dev/null -w 'HTTP %{http_code}\n' "$(terraform output -raw cloudfront_domain_name)"

# ALB への直接アクセスはタイムアウトする（SGが非CloudFront IPを遮断）
curl -s -o /dev/null -w 'HTTP %{http_code}\n' --max-time 5 "$(terraform output -raw alb_dns_name)"
```

Phase 2 の適用後、Application Load Balancer への直接アクセスがタイムアウトし、Amazon CloudFront 経由のアクセスのみ成功することを確認できれば、多層防御が機能しています。実機出力:

```text
--- CloudFront 経由 ---
HTTP 200
--- ALB 直接 ---
HTTP 000
```

`000` は curl が応答を得られずタイムアウトしたことを示します。接続が拒否されるのではなく無応答になるのは、セキュリティグループが破棄したパケットに何も返さないためです。確認できたら、次の破棄手順に進みます（`enable_demo_app`/`enable_cloudfront` で作ったリソースも `--destroy` の `terraform destroy` でまとめて消えます）。

:::message
Amazon CloudFront デモは本番運用を想定していません。Application Load Balancer リスナーは HTTP/80 のまま、ACM 証明書も WAF も付けていません。本番要件では Application Load Balancer への ACM 証明書追加による HTTPS 化と、Amazon CloudFront VPC Origins・WAFv2 の追加が必要になります。
:::

:::message
Phase 2 では Amazon CloudFront 用の ACM 証明書を扱うため、`us-east-1` 向けのプロバイダが追加で使われます。ここで `terraform plan` が次のように失敗する場合があります。

```text
Error: No valid credential sources found
Error: failed to refresh cached credentials, process provider error:
credential process timed out: signal: killed
```

認証情報そのものの問題ではなく、`credential_process` を使う構成で 2 つ目のリージョンの認証が同時に走ってタイムアウトしただけです。先に `aws sts get-caller-identity --region us-east-1` を一度実行して認証情報をキャッシュさせておけば通ります。
:::

`-var` はそのコマンド実行時だけの指定です。この後 `-var` を付けずに `terraform apply` すると、`enable_demo_app`/`enable_cloudfront` は既定の `false` に戻り、demo アプリと Amazon CloudFront のリソースが黙って削除されます。恒久的に有効にしたい場合は `terraform.tfvars` に `enable_demo_app = true`（Phase 2 まで進めるなら `enable_cloudfront = true` も）を書いておきます。

なお Phase 1 の状態は、ヘッダー検証も SG 制限もないまま Application Load Balancer が internet-facing で公開されます。echo サーバーとはいえ、確認が済んだら速やかに Phase 2 へ進むか、次の破棄手順で destroy することを勧めます。

## 3. アクセラレータプールだけをドレインする

`04-teardown.sh` は既定で、指定した namespace の Deployment/StatefulSet/Job/TrainJob/MPIJob を削除し、GPU/Neuron Pod の終了を確認したうえで Karpenter の NodePool を削除します。本 book の主力ワークロードである Kubeflow Trainer v2 の TrainJob（Basic02/Basic09 の DDP）もここで確実に消えます。ここで指定するのは、Basic01 以降のワークショップで使ってきた作業用 namespace（本 book では `distai`）です。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/scripts
export NAMESPACE=distai
./04-teardown.sh --namespace "$NAMESPACE"
```

対話実行なので `--yes` は付けていません。`04-teardown.sh` は各ステップの削除前に `y/N` の確認を挟むため、手元のターミナルで様子を見ながら進められます。`--yes` の正確な挙動と、CI などの非対話実行で必須になる理由は手順 5 で扱います。

削除対象の NodePool はクラスタに問い合わせて決まります。`accelerator_pools` は読者が自分で定義するマップなので、スクリプトが決め打ちできる名前は存在しません。代わりに、Terraform モジュールがアクセラレータプールに付ける device taint（`nvidia.com/gpu` / `aws.amazon.com/neuron`）を持つ NodePool をすべて対象にします。実機出力です（プール名は `accelerator_pools` で自分が付けた名前がそのまま出るため、検証時期や読者の環境によってここは変わります。以下は本書の検証時点の一例です）。

```text
Discovered accelerator NodePool(s): gpu-ddp gpu-p4d
=== Teardown Plan ===
  Namespace  : distai
  NodePool(s): gpu-ddp gpu-p4d
  Destroy    : false
```

taint を持たない cpu プールは対象外です。drain 待ちの間、Karpenter コントローラ・CSI コントローラ・CoreDNS などクラスタ内で動くコントローラの実行先ノードを残しておく必要があるためで、次の手順 5 でまとめて消えます。

削除のあと、device リソースを持つノードが残っていないかを必ず出します。ここに GPU や Neuron のノードが並んでいるのに「Teardown complete」と出た場合、削除が効いていないので放置しないでください。

```text
Accelerator nodes still registered:
  ip-10-0-a-b...  p4d.24xlarge  nvidia.com/gpu=8
  (still billing — they drain asynchronously; watch: kubectl get nodeclaims -w)
```

対象を明示したい場合は `--nodepool` を繰り返し指定できます（`--nodepool gpu-ddp --nodepool gpu-p4d`）。存在しない名前を渡した場合は警告を出して止まらずに進むので、`k get nodepool` で実際の名前を確かめてください。

:::message alert
プールを 1 つ取り残すと、そのノードは課金され続けます。GPU や Neuron は時間単価が高いので、上の「Accelerator nodes still registered」が `none` になるまで確認してから次へ進んでください。
:::

## 4. ドレインの過程を観察する

```bash
k get nodeclaims -w
```

単一ノードの構成であれば概ね 9 分前後で 0 件になります。`NodePool` 削除の直後は NodeClaim がまだ `Terminating` で残り、Karpenter が Amazon EC2 インスタンスの終了をバックグラウンドで進めていることが分かります。

この時間はインスタンスタイプで大きく変わります。EFA を複数枚持つノードは ENI の解放に時間がかかり、本書の検証では EFA 4 枚の p4d.24xlarge 1 台が `shutting-down` から `terminated` になるまで**約 20 分**を要しました。Pod の退避（ドレイン）自体は 1 分ほどで終わっており、残りはすべて EC2 側の非同期処理です。次のコマンドで、Kubernetes 側が終わったあとに EC2 側がまだ動いていることを確かめられます。

```bash
k get nodeclaim <name> \
  -o jsonpath='{range .status.conditions[?(@.type=="Drained")]}Drained={.status}{"\n"}{end}'
aws ec2 describe-instances \
  --filters "Name=private-dns-name,Values=<node-name>" \
  --query 'Reservations[0].Instances[0].[State.Name,NetworkInterfaces[].InterfaceType]' --output text
```

`Drained=True` が出たあとも `shutting-down` が続き、`efa-only` の ENI が一覧から順に消えていきます。`04-teardown.sh` の `NodeClaim` 待ちが最大 30 分なのは、この幅を吸収するためです。EFA を多く積んだノードを複数台まとめて畳む場合、9 分では終わらない前提で待ってください。

## 5. クラスタ全体を破棄する

```bash
./04-teardown.sh --namespace "$NAMESPACE" --destroy
```

対話実行なので `--yes` は付けていません。`--yes` を付けない場合、`04-teardown.sh` の各ステップの y/N 確認（`confirm` 関数）は手元のターミナルからの入力を待ちます。ここで注意が必要なのは、CI やバックグラウンド実行のように標準入力が繋がっていない非対話実行で `--yes` を付け忘れた場合の挙動です。`read` が即座に EOF を受け取り、`confirm` は「N」と解釈してすべてのステップを黙って `Skipped` にします。つまりワークロードの削除も `terraform destroy` の実行そのものも一切起きないまま `=== Teardown complete ===` が表示され、何も壊れていないのに完了したように見えてしまいます。

逆に `--yes` を付けると、スクリプト自身の確認がすべて自動で通るだけでなく、`terraform destroy` にも `-auto-approve` が渡されます。これは `terraform destroy` 自身が持つ「本当に破棄しますか」という確認プロンプトを避けるためで、もし `-auto-approve` を渡さなければ、スクリプトの確認は自動で通って Kubernetes 側の削除だけが先に完了したあと、`terraform destroy` 自身の確認プロンプトが標準入力の EOF で失敗し、NodePool は消えたのにクラスタは残る中途半端な状態で止まってしまいます。CI やバックグラウンド実行のように非対話で流す場合は、この事故を避けるために `--yes` が必須です。

`terraform destroy` の中で、前節で示した `wait_for_node_drain` のポーリングログが流れます。ログが `no NodeClaim(s) remain.` に達してから、Karpenter コントローラや GPU Operator などノードに紐づくコントローラの破棄に進みます。

:::message
destroy 実行環境の PATH に `bash`・`aws` CLI・`kubectl` が必要です。いずれか欠けると provisioner はポーリングせずエラー終了します。確認できずに進んで課金を取り残すより destroy を止める意図的な安全側の設計であり、`aws ec2 describe-instances` で孤立インスタンスを確認してから再実行します。

Capacity Block の期限が近い状態で teardown する場合は、この 30 分ポーリング分の時間も見積もりに入れてください。期限を過ぎてから teardown を始めても意味がなく、容量は AWS 側で強制回収されます。
:::

:::message alert
30 分のポーリングが完了するまで、ターミナルを閉じずに待ちましょう。途中で中断すると、アクセラレータノードが取り残されたまま課金が続く可能性があります。30 分でタイムアウトした場合は本当にノードが詰まっています。`k get nodeclaims` と `aws ec2 describe-instances` で Finalizer の残存や Karpenter コントローラの異常終了を確認してから再実行してください。
:::

NAT 消失後に `EC2NodeClass` の finalizer が残ることがあります。対応する Amazon EC2 インスタンスは既に終了済みで課金への影響はありません。気になる場合は手動で外します。

```bash
k patch ec2nodeclass <name> --type=merge -p '{"metadata":{"finalizers":[]}}'
```

# まとめ

本章では、`terraform destroy` がアクセラレータノードを取り残して課金が続く事故を防ぐ `wait_for_node_drain` の仕組みと、その周辺で見つかった NAT ゲートウェイ早期消失・IAM 残渣への対処を扱いました。あわせて、オプション機能として Amazon CloudFront → Application Load Balancer → Amazon EKS の多層防御デモも確認しました。破棄は「非同期処理が終わるまで待つ」「依存関係の逆順を利用する」という設計を理解しておけば、GPU/Neuron のような高額なリソースを安全に畳めます。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter NodeClaim/NodePool](https://karpenter.sh/docs/concepts/)
- [AWS Load Balancer Controller](https://kubernetes-sigs.github.io/aws-load-balancer-controller/)
- [Amazon CloudFront のカスタムオリジンヘッダー](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/add-origin-custom-headers.html)
- [Terraform: Resource dependencies (depends_on)](https://developer.hashicorp.com/terraform/language/resources/behavior#resource-dependencies)
- [Terraform: Destroy-Time Provisioners](https://developer.hashicorp.com/terraform/language/resources/provisioners/syntax#destroy-time-provisioners)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
