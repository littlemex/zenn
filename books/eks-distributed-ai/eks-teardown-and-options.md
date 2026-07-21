---
title: "Basic11 - 安全な破棄とオプション機能"
free: true
---

本章では、これまで積み上げてきた Amazon EKS 基盤を安全に破棄する仕組みと、オプションで有効化できる外部公開エンドポイント（Amazon CloudFront → Application Load Balancer → Amazon EKS）を扱います。`terraform destroy` がアクセラレータノードを取り残して課金が続く事故を防ぐ設計を理解し、実際に手を動かして破棄の過程を観察します。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図で構築してきたリソース全体を安全に取り壊す仕組みと、図には含まれないオプションの外部公開経路（Amazon CloudFront・AWS Load Balancer Controller によるデモアプリ配信）です。基盤づくりの最終章として、構築の逆方向（破棄）と、任意で足す周辺機能の 2 つを押さえます。

## これは何をするものか

`kubectl_manifest` の削除は、Kubernetes API がリクエストを**受理した瞬間**に Terraform 上で「完了」として扱われます。しかし `NodePool`/`NodeClaim` の実際のノード drain・Amazon EC2 終了・ENI 解放は、Karpenter コントローラが行う非同期処理です。この非同期処理が終わる前に Karpenter や GPU Operator・EFA/Neuron device plugin・Amazon EFS/Amazon FSx for Lustre CSI ドライバなど、ノードに紐づくリソースを持つコントローラを destroy してしまうと、アクセラレータノードの Amazon EC2 インスタンスが孤立し、誰も終了させないまま**課金だけが続く**事故になります。GPU/Neuron は時間単価が高く、このリスクは軽視できません。

これを防ぐのが [`karpenter.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/karpenter.tf) の `null_resource.wait_for_node_drain` です。この対処の過程で NAT ゲートウェイの早期消失や IAM 残差といった追加の障害が見つかり、[`vpc-endpoints.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/vpc-endpoints.tf) での対処も必要になりました。さらにオプション機能として、外部公開エンドポイントのデモ（[`alb-controller.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/alb-controller.tf) / [`cloudfront.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/cloudfront.tf)）も用意しています。以降で実際のコードを引用しながら、なぜその設計にしているのかを見ていきます。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/fix/eks-efa-verification-improvements/infra/eks) です。

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

読みどころは `stdout` と `stderr` を分けて捕捉している点です。`kubectl get` は結果が 0 件でも exit 0 のまま「No resources found」を**標準エラー**に書きます。これを `2>&1` でまとめて捕捉すると、そのメッセージが 1 行としてカウントされてしまい、実際には 0 件なのに「まだ 1 件残っている」と永久に判定してしまうバグを踏みました（別セッションで `kubectl get` すると 0 件なのに、このループだけ 15 分以上「1 件残存」と報告し続けていたことで発覚）。`err_file` に `stderr` だけを分離して捕捉することでこれを防いでいます。

呼び出し側は 2 段階です。

```bash
# karpenter.tf（抜粋）
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

1 段目の `NodeClaim` は 10 秒間隔で最大 180 回、つまり最大 30 分ポーリングし、タイムアウトすれば destroy を失敗させます（`exit 1`）。課金に直結するオブジェクトなので、ここは厳格に失敗させる設計です。2 段目の `EC2NodeClass` は同じ 10 秒間隔で最大 60 回、つまり最大 10 分ポーリングしますが、タイムアウトしても `exit 0` で destroy 全体は止めません。この非対称な扱いの理由は次のセクションで説明します。ドレイン時間はノード数やインスタンスタイプで変動するため（単一ノードの実測で概ね 9 分）、固定 sleep ではなく実状態を見る設計にしています。

この `null_resource` は次のリソース群すべてに `depends_on` しています。

```hcl
# karpenter.tf（抜粋）
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
```

Terraform の destroy は `depends_on` の**逆順**に進みます（A が B に `depends_on` していれば、destroy は A → B の順）。つまりこの一覧があることで、destroy 順序は必ず「`wait_for_node_drain`（ポーリングが走る）→ Karpenter/GPU Operator/EFA plugin/Neuron/Amazon EFS・Amazon FSx for Lustre CSI/EFA セキュリティグループ/placement group」の順に強制されます。もう一方の端、`NodePool`/`NodeClaim` の manifest 側は逆に `karpenter-resources.tf` でこの `null_resource` に `depends_on` しており、`NodePool` の削除が先に issue されてからポーリングが始まる形です。全体は「NodePool 削除 → この resource（待つ） → 各コントローラ破棄」という一方向の直線になり、循環は発生しません。

## Amazon VPC endpoints で NAT 依存を切る

もう一つ見つかった障害が NAT ゲートウェイの早期消失です。NAT と Karpenter の間に明示的な依存関係がないため、`module.vpc` の NAT ゲートウェイがポーリング中に先に消えることがあります。Karpenter コントローラは private subnet で動き、Amazon EC2/IAM/STS/SSM への API 呼び出しを NAT 経由のアクセスに依存しているため、NAT 消失と同時に API がすべてタイムアウトし、`NodeClaim` の finalizer が外れず 30 分のタイムアウトに達してしまいます。`wait_for_node_drain` を `module.vpc` に直接依存させたいところですが、`triggers` が参照する `module.eks` が既に `module.vpc` に依存しているため循環依存になり不可能です。

そこで `vpc-endpoints.tf` でネットワーク層側から対処します。

```hcl
# vpc-endpoints.tf（抜粋）
locals {
  vpc_endpoint_services = ["ec2", "sts", "ssm"]
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

Amazon EC2・STS・SSM の Interface VPC endpoint と Amazon S3 の Gateway endpoint を作成し、NAT の有無に関わらず Karpenter が AWS API を呼べるようにします。`wait_for_node_drain` はこれらの endpoint にも `depends_on` しているため（前節のリスト参照）、ポーリング中は消えません。

ただし IAM はここに含まれていません。IAM はグローバルサービスで、リージョン単位の Interface endpoint を持たないためです。実際に `aws ec2 describe-vpc-endpoint-services` で確認すると `ec2`/`ec2-fips`/`ssm*`/`sts`/`sts-fips` は列挙されますが `iam` は存在せず、`com.amazonaws.<region>.iam` への `aws_vpc_endpoint` 作成は `InvalidServiceName` で失敗します。そのため Karpenter の `EC2NodeClass` 終了処理が呼ぶ `ListInstanceProfiles`（IAM API）は、NAT 消失後もタイムアウトしうります。これが前節で `EC2NodeClass` のポーリングだけをベストエフォート（最大 10 分・タイムアウトしても `exit 0`）にしている理由です。課金停止に直結する `NodeClaim` の消失を待つのは必須としつつ、IAM という他手段のない経路に阻まれる可能性がある `EC2NodeClass` finalizer の解除まで destroy 全体を止めるのは実用的でないと判断しています。Amazon EC2 インスタンス自体は 1 段目の時点で終了済みであり、課金に影響しないオブジェクトのために destroy を止めるより先に進む方が合理的です。

## Amazon CloudFront デモ（オプション機能）の2段階 apply

もう 1 つ、オプション機能として外部公開エンドポイントのデモを用意しています。`var.enable_demo_app`（既定 `false`）でゲートされ、`Client → Amazon CloudFront (HTTPS) → Application Load Balancer (HTTP/80) → Amazon EKS Pod` という経路をとります。

AWS Load Balancer Controller 自体の権限付与は Ch1 の Karpenter・EBS CSI と同じ Pod Identity パターンです。

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

Layer 2 は Amazon CloudFront が付与する `X-Origin-Verify` ヘッダーを Ingress の `conditions` アノテーションで検証するアプリケーション層の制限です。

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

AWS Load Balancer Controller の SG デタッチが非同期であることに由来する `DependencyViolation` を避けるため、`time_sleep` ではなく delete タイムアウトの延長で対処しています。`time_sleep` を使わない理由は、この SG の `id` を Ingress 側が参照しているため、そちらで依存関係を組むと循環依存になってしまうからです。同種の非同期完了待ちの問題は `time_sleep.demo_ingress_finalizer`（AWS Load Balancer Controller の finalizer 解除を 20 秒待つ）にも見られ、この章で繰り返し出てくる「Kubernetes API の受理と実際の完了は別物」というテーマの一例になっています。

## 全体の中での位置付け

本章は基盤構築の最終章にあたります。Ch1 から積み上げてきた Amazon VPC・Amazon EKS コントロールプレーン・Karpenter・各アクセラレータプール・共有ストレージを、課金を取り残さずに安全に取り壊す方法を扱います。また、これまでの章では触れなかった「外部からアクセスする経路」をオプション機能として最後に足すことで、基盤の上にアプリケーションを公開する際の考え方も示します。破棄とオプション機能は独立した話題ですが、いずれも「基盤を運用し続ける中で、いつか必要になる」という共通点で本章にまとめています。

## 注意

**destroy 実行環境の PATH に `bash`・`aws` CLI・`kubectl` が必要です。** いずれか欠けると provisioner はポーリングせずエラー終了します。確認できずに進んで課金を取り残すより destroy を止める意図的な安全側の設計であり、`aws ec2 describe-instances` で孤立インスタンスを確認してから再実行します。

**30 分でタイムアウトした場合は本当にノードが詰まっています。** `kubectl get nodeclaims` と `aws ec2 describe-instances` で Finalizer の残存や Karpenter コントローラの異常終了を確認してから再実行します。

**NAT 消失後に `EC2NodeClass` の finalizer が残ることがあります。** 対応する Amazon EC2 インスタンスは既に終了済みで課金への影響はありません。気になる場合は手動で外します。

```bash
kubectl patch ec2nodeclass <name> --type=merge -p '{"metadata":{"finalizers":[]}}'
```

**Amazon CloudFront デモは本番運用を想定していません。** Application Load Balancer リスナーは HTTP/80 のまま、ACM 証明書も WAF も付けていません。本番要件では Application Load Balancer への ACM 証明書追加による HTTPS 化と、Amazon CloudFront VPC Origins・WAFv2 の追加が必要になります。

**共有クラスタでは実行前に `kubectl config current-context` を必ず確認します。** この章の操作はクラスタ全体またはアクセラレータプール全体に影響する破壊的操作であり、意図しないコンテキストへの誤実行を避けます。

# ワークショップ実施

## 1. アクセラレータプールだけをドレインする

`04-teardown.sh` は既定で、指定した namespace の Deployment/StatefulSet/Job/MPIJob を削除し、GPU Pod の終了を確認したうえで Karpenter の NodePool を削除します。ここで指定するのは、Basic01 以降のワークショップで使ってきた作業用 namespace（本 book では `distai`）です。

```bash
cd infra/eks/scripts
export NAMESPACE=distai
./04-teardown.sh --namespace "$NAMESPACE"
```

## 2. ドレインの過程を観察する

```bash
kubectl get nodeclaims -w
```

単一ノードの構成であれば概ね 9 分前後で 0 件になります。`NodePool` 削除の直後は NodeClaim がまだ `Terminating` で残り、Karpenter が Amazon EC2 インスタンスの終了をバックグラウンドで進めていることが分かります。

## 3. クラスタ全体を破棄する

```bash
./04-teardown.sh --namespace "$NAMESPACE" --destroy
```

`terraform destroy` の中で、前節で示した `wait_for_node_drain` のポーリングログが流れます。ログが `no NodeClaim(s) remain.` に達してから、Karpenter コントローラや GPU Operator などノードに紐づくコントローラの破棄に進みます。

:::message
30 分のポーリングが完了するまで、ターミナルを閉じずに待ちましょう。途中で中断すると、アクセラレータノードが取り残されたまま課金が続く可能性があります。
:::

## 4. （オプション）Amazon CloudFront デモを試す

```bash
# Phase 1: ALB Controller + demo app
terraform apply -var enable_demo_app=true
kubectl get ingress -n demo -w
curl -i http://$(kubectl get ingress -n demo echo -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')/
```

```bash
# Phase 2: CloudFront + SG制限 + ヘッダー検証を追加
terraform apply -var enable_demo_app=true -var enable_cloudfront=true
curl -i "$(terraform output -raw cloudfront_domain_name)"

# ALB への直接アクセスはタイムアウトする（SGが非CloudFront IPを遮断）
curl -i --max-time 5 http://$(terraform output -raw alb_dns_name)
```

Phase 2 の適用後、Application Load Balancer への直接アクセスがタイムアウトし、Amazon CloudFront 経由のアクセスのみ成功することを確認できれば、多層防御が機能しています。

# まとめ

本章では、`terraform destroy` がアクセラレータノードを取り残して課金が続く事故を防ぐ `wait_for_node_drain` の仕組みと、その周辺で見つかった NAT ゲートウェイ早期消失・IAM 残差への対処を扱いました。あわせて、オプション機能として Amazon CloudFront → Application Load Balancer → Amazon EKS の多層防御デモも確認しました。破棄は「非同期処理が終わるまで待つ」「依存関係の逆順を利用する」という設計を理解しておけば、GPU/Neuron のような高額なリソースを安全に畳めます。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter NodeClaim/NodePool](https://karpenter.sh/docs/concepts/)
- [AWS Load Balancer Controller](https://kubernetes-sigs.github.io/aws-load-balancer-controller/)
- [Amazon CloudFront と Application Load Balancer の連携](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Introduction.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
