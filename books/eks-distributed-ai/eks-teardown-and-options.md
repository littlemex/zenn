---
title: "Basic09 - 安全な破棄とオプション機能"
free: true
---

本章では、これまで積み上げてきた EKS 基盤を安全に破棄する仕組みと、オプションで有効化できる外部公開エンドポイント（CloudFront → ALB → EKS）を扱います。`terraform destroy` がアクセラレータノードを取り残して課金が続く事故を防ぐ設計を理解し、実際に手を動かして破棄の過程を観察します。

# 解説

## 全体構成

![EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図で構築してきたリソース全体を安全に取り壊す仕組みと、図には含まれないオプションの外部公開経路（CloudFront・ALB Controller によるデモアプリ配信）です。基盤づくりの最終章として、構築の逆方向（破棄）と、任意で足す周辺機能の 2 つを押さえます。

## これは何をするものか

`kubectl_manifest` の削除は、Kubernetes API がリクエストを**受理した瞬間**に Terraform 上で「完了」として扱われます。しかし `NodePool`/`NodeClaim` の実際のノード drain・EC2 終了・ENI 解放は、Karpenter コントローラが行う非同期処理です。この非同期処理が終わる前に Karpenter や GPU Operator・EFA/Neuron device plugin・EFS/FSx CSI ドライバなど、ノードに紐づくリソースを持つコントローラを destroy してしまうと、アクセラレータノードの EC2 インスタンスが孤立し、誰も終了させないまま**課金だけが続く**事故になります。GPU/Neuron は時間単価が高く、このリスクは軽視できません。

これを防ぐのが `karpenter.tf` の `null_resource.wait_for_node_drain` です。destroy 時にのみ動く `provisioner "local-exec"`（`when = destroy`）で、`kubectl get nodeclaims.karpenter.sh` が空になるまで 10 秒間隔・最大 180 回（30 分）ポーリングします。ドレイン時間はノード数やインスタンスタイプで変動するため（単一ノードの実測で概ね 9 分）、固定 sleep ではなく実状態を見る設計にしています。この `null_resource` は Karpenter・GPU Operator・EFA plugin・Neuron・EFS/FSx CSI・EFA セキュリティグループすべてに `depends_on` しており、destroy 順序が `depends_on` の**逆順**になる性質を利用して「NodePool 削除 → ポーリング → 各コントローラ破棄」を強制しています。

もう一つ見つかった障害が NAT ゲートウェイの早期消失です。NAT と Karpenter の間に明示的な依存関係がないため、`module.vpc` の NAT ゲートウェイがポーリング中に先に消えることがあります。Karpenter コントローラは private subnet で動き、EC2/IAM/STS/SSM への API 呼び出しを NAT 経由のアクセスに依存しているため、NAT 消失と同時に API がすべてタイムアウトし、NodeClaim の finalizer が外れず 30 分のタイムアウトに達してしまいます。`wait_for_node_drain` を `module.vpc` に直接依存させたいところですが、`triggers` が参照する `module.eks` が既に `module.vpc` に依存しているため循環依存になり不可能です。

そこで `vpc-endpoints.tf` でネットワーク層側から対処します。EC2・STS・SSM の Interface VPC endpoint と S3 の Gateway endpoint（時間課金なし）を作成し、NAT の有無に関わらず Karpenter が AWS API を呼べるようにします。`wait_for_node_drain` はこれらの endpoint にも `depends_on` し、ポーリング中は消えないようにしています。ただし IAM はグローバルサービスで、リージョン単位の Interface endpoint を持ちません（`InvalidServiceName` で作成不可）。そのため Karpenter の `EC2NodeClass` 終了処理が呼ぶ `ListInstanceProfiles`（IAM API）は、NAT 消失後もタイムアウトしうります。この残差には 2 段階ポーリングで対処します。課金停止に直結する `NodeClaim` の消失を待つのは必須とし、続く `EC2NodeClass` finalizer の解除は最大 10 分の**ベストエフォート**として、タイムアウトしても destroy 全体は失敗させません。EC2 インスタンス自体は 1 段目の時点で終了済みであり、課金に影響しないオブジェクトのために destroy を止めるより先に進む方が実用的と判断しているためです。

もう 1 つ、オプション機能として外部公開エンドポイントのデモ（`alb-controller.tf` / `cloudfront.tf`）を用意しています。`var.enable_demo_app`（既定 `false`）でゲートされ、`Client → CloudFront (HTTPS) → ALB (HTTP/80) → EKS Pod` という経路をとります。主眼は多層防御で、Layer 1 は ALB のセキュリティグループを CloudFront のマネージド prefix list（`com.amazonaws.global.cloudfront.origin-facing`）のみに絞るネットワーク制限、Layer 2 は CloudFront が付与する `X-Origin-Verify` ヘッダーを Ingress の `conditions` アノテーションで検証するアプリケーション層の制限です。デプロイは 2 段階で、Phase 1（`enable_demo_app=true`）で ALB Controller と demo アプリの経路確認、Phase 2（`enable_cloudfront=true` を追加）で CloudFront・SG 制限・ヘッダー条件をまとめて適用します。

## 全体の中での位置付け

本章は基盤構築の最終章にあたります。Ch1 から積み上げてきた VPC・EKS コントロールプレーン・Karpenter・各アクセラレータプール・共有ストレージを、課金を取り残さずに安全に取り壊す方法を扱います。また、これまでの章では触れなかった「外部からアクセスする経路」をオプション機能として最後に足すことで、基盤の上にアプリケーションを公開する際の考え方も示します。破棄とオプション機能は独立した話題ですが、いずれも「基盤を運用し続ける中で、いつか必要になる」という共通点で本章にまとめています。

## 実際に挙動を確認する

`04-teardown.sh --destroy` を実行すると、`terraform destroy` の中で `wait_for_node_drain` のポーリングログが流れます。

```
wait_for_node_drain: waiting for Karpenter to terminate all accelerator NodeClaims...
wait_for_node_drain: 1 NodeClaim(s) still present (attempt 3/180)...
wait_for_node_drain: no NodeClaim(s) remain.
wait_for_node_drain: waiting for Karpenter to clear EC2NodeClass finalizers (best-effort)...
```

このログが出ている間、Karpenter コントローラや GPU Operator はまだ destroy されません。単一ノードの構成であれば概ね 9 分前後で `NodeClaim` が 0 件になります。具体的な実行手順は後述の「ワークショップ実施」で扱います。

## 注意

**destroy 実行環境の PATH に `bash`・`aws` CLI・`kubectl` が必要です。** いずれか欠けると provisioner はポーリングせずエラー終了します。確認できずに進んで課金を取り残すより destroy を止める意図的な安全側の設計であり、`aws ec2 describe-instances` で孤立インスタンスを確認してから再実行します。

**30 分でタイムアウトした場合は本当にノードが詰まっています。** `kubectl get nodeclaims` と `aws ec2 describe-instances` で Finalizer の残存や Karpenter コントローラの異常終了を確認してから再実行します。

**NAT 消失後に `EC2NodeClass` の finalizer が残ることがあります。** 対応する EC2 インスタンスは既に終了済みで課金への影響はありません。気になる場合は手動で外します。

```bash
kubectl patch ec2nodeclass <name> --type=merge -p '{"metadata":{"finalizers":[]}}'
```

**CloudFront デモは本番運用を想定していません。** ALB リスナーは HTTP/80 のまま、ACM 証明書も WAF も付けていません。本番要件では ALB への ACM 証明書追加による HTTPS 化と、CloudFront VPC Origins・WAFv2 の追加が必要になります。

**共有クラスタでは実行前に `kubectl config current-context` を必ず確認します。** この章の操作はクラスタ全体またはアクセラレータプール全体に影響する破壊的操作であり、意図しないコンテキストへの誤実行を避けます。

# ワークショップ実施

## 1. アクセラレータプールだけをドレインする

`04-teardown.sh` は既定で、指定した namespace の Deployment/StatefulSet/Job/MPIJob を削除し、GPU Pod の終了を確認したうえで Karpenter の NodePool を削除します。

```bash
cd infra/eks/scripts
./04-teardown.sh --namespace <namespace>
```

## 2. ドレインの過程を観察する

```bash
kubectl get nodeclaims -w
```

単一ノードの構成であれば概ね 9 分前後で 0 件になります。`NodePool` 削除の直後は NodeClaim がまだ `Terminating` で残り、Karpenter が EC2 インスタンスの終了をバックグラウンドで進めていることが分かります。

## 3. クラスタ全体を破棄する

```bash
./04-teardown.sh --namespace <namespace> --destroy
```

`terraform destroy` の中で、前節で示した `wait_for_node_drain` のポーリングログが流れます。ログが `no NodeClaim(s) remain.` に達してから、Karpenter コントローラや GPU Operator などノードに紐づくコントローラの破棄に進みます。

:::message
30 分のポーリングが完了するまで、ターミナルを閉じずに待ちましょう。途中で中断すると、アクセラレータノードが取り残されたまま課金が続く可能性があります。
:::

## 4. （オプション）CloudFront デモを試す

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

Phase 2 の適用後、ALB への直接アクセスがタイムアウトし、CloudFront 経由のアクセスのみ成功することを確認できれば、多層防御が機能しています。

# まとめ

本章では、`terraform destroy` がアクセラレータノードを取り残して課金が続く事故を防ぐ `wait_for_node_drain` の仕組みと、その周辺で見つかった NAT ゲートウェイ早期消失・IAM 残差への対処を扱いました。あわせて、オプション機能として CloudFront → ALB → EKS の多層防御デモも確認しました。破棄は「非同期処理が終わるまで待つ」「依存関係の逆順を利用する」という設計を理解しておけば、GPU/Neuron のような高額なリソースを安全に畳めます。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter NodeClaim/NodePool](https://karpenter.sh/docs/concepts/)
- [AWS Load Balancer Controller](https://kubernetes-sigs.github.io/aws-load-balancer-controller/)
- [Amazon CloudFront と Application Load Balancer の連携](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Introduction.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
