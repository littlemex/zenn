---
title: "安全な破棄とオプション機能"
---

## メインテーマ

`terraform destroy` がアクセラレータノードを取り残して課金が続く事故を防ぐ仕組みと、オプションの公開エンドポイント（CloudFront → ALB → EKS）を理解する。

## これは何をするものか

`kubectl_manifest` の削除は、Kubernetes API がリクエストを**受理した瞬間**に Terraform 上で「完了」として扱われる。しかし `NodePool`/`NodeClaim` の実際のノード drain・EC2 終了・ENI 解放は、Karpenter コントローラが行う非同期処理である。この非同期処理が終わる前に Karpenter や GPU Operator・EFA/Neuron device plugin・EFS/FSx CSI ドライバなど、ノードに紐づくリソースを持つコントローラを destroy してしまうと、アクセラレータノードの EC2 インスタンスが孤立し、誰も終了させないまま**課金だけが続く**事故になる。GPU/Neuron は時間単価が高く、このリスクは軽視できない。

これを防ぐのが `karpenter.tf` の `null_resource.wait_for_node_drain` である。destroy 時にのみ動く `provisioner "local-exec"`（`when = destroy`）で、`kubectl get nodeclaims.karpenter.sh` が空になるまで 10 秒間隔・最大 180 回（30 分）ポーリングする。ドレイン時間はノード数やインスタンスタイプで変動するため（単一ノードの実測で概ね 9 分）、固定 sleep ではなく実状態を見る設計にしている。この `null_resource` は Karpenter・GPU Operator・EFA plugin・Neuron・EFS/FSx CSI・EFA セキュリティグループすべてに `depends_on` しており、destroy 順序が `depends_on` の**逆順**になる性質を利用して「NodePool 削除 → ポーリング → 各コントローラ破棄」を強制している。

もう一つ見つかった障害が NAT ゲートウェイの早期消失である。NAT と Karpenter の間に明示的な依存関係がないため、`module.vpc` の NAT ゲートウェイがポーリング中に先に消えることがある。Karpenter コントローラは private subnet で動き、EC2/IAM/STS/SSM への API 呼び出しを NAT 経由のアクセスに依存しているため、NAT 消失と同時に API がすべてタイムアウトし、NodeClaim の finalizer が外れず 30 分のタイムアウトに達してしまう。`wait_for_node_drain` を `module.vpc` に直接依存させたいところだが、`triggers` が参照する `module.eks` が既に `module.vpc` に依存しているため循環依存になり不可能である。

そこで `vpc-endpoints.tf` でネットワーク層側から対処する。EC2・STS・SSM の Interface VPC endpoint と S3 の Gateway endpoint（時間課金なし）を作成し、NAT の有無に関わらず Karpenter が AWS API を呼べるようにする。`wait_for_node_drain` はこれらの endpoint にも `depends_on` し、ポーリング中は消えないようにしている。ただし IAM はグローバルサービスで、リージョン単位の Interface endpoint を持たない（`InvalidServiceName` で作成不可）。そのため Karpenter の `EC2NodeClass` 終了処理が呼ぶ `ListInstanceProfiles`（IAM API）は、NAT 消失後もタイムアウトしうる。この残差には 2 段階ポーリングで対処する。課金停止に直結する `NodeClaim` の消失を待つのは必須とし、続く `EC2NodeClass` finalizer の解除は最大 10 分の**ベストエフォート**として、タイムアウトしても destroy 全体は失敗させない。EC2 インスタンス自体は 1 段目の時点で終了済みであり、課金に影響しないオブジェクトのために destroy を止めるより先に進む方が実用的と判断しているためである。

もう 1 つ、オプション機能として外部公開エンドポイントのデモ（`alb-controller.tf` / `cloudfront.tf`）を用意している。`var.enable_demo_app`（既定 `false`）でゲートされ、`Client → CloudFront (HTTPS) → ALB (HTTP/80) → EKS Pod` という経路をとる。主眼は多層防御で、Layer 1 は ALB のセキュリティグループを CloudFront のマネージド prefix list（`com.amazonaws.global.cloudfront.origin-facing`）のみに絞るネットワーク制限、Layer 2 は CloudFront が付与する `X-Origin-Verify` ヘッダーを Ingress の `conditions` アノテーションで検証するアプリケーション層の制限である。デプロイは 2 段階で、Phase 1（`enable_demo_app=true`）で ALB Controller と demo アプリの経路確認、Phase 2（`enable_cloudfront=true` を追加）で CloudFront・SG 制限・ヘッダー条件をまとめて適用する。

## 全体の中での位置付け

![チャプターの位置付け](/images/books/eks-distributed-ai/ch9-teardown.png)

## 実際に挙動を確認する

### 1. アクセラレータプールだけをドレインする

`04-teardown.sh` は既定で、指定した namespace の Deployment/StatefulSet/Job/MPIJob を削除し、GPU Pod の終了を確認したうえで Karpenter の NodePool を削除する。

```bash
cd infra/eks/scripts
./04-teardown.sh --namespace <namespace>
```

### 2. ドレインの過程を観察する

```bash
kubectl get nodeclaims -w
```

単一ノードの構成であれば概ね 9 分前後で 0 件になる。`NodePool` 削除の直後は NodeClaim がまだ `Terminating` で残り、Karpenter が EC2 インスタンスの終了をバックグラウンドで進めていることが分かる。

### 3. クラスタ全体を破棄する

```bash
./04-teardown.sh --namespace <namespace> --destroy
```

`terraform destroy` の中で `wait_for_node_drain` のポーリングログが流れる。

```
wait_for_node_drain: waiting for Karpenter to terminate all accelerator NodeClaims...
wait_for_node_drain: 1 NodeClaim(s) still present (attempt 3/180)...
wait_for_node_drain: no NodeClaim(s) remain.
wait_for_node_drain: waiting for Karpenter to clear EC2NodeClass finalizers (best-effort)...
```

このログが出ている間、Karpenter コントローラや GPU Operator はまだ destroy されない。

### 4. （オプション）CloudFront デモを試す

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

## 注意点

**destroy 実行環境の PATH に `bash`・`aws` CLI・`kubectl` が必要。** いずれか欠けると provisioner はポーリングせずエラー終了する。確認できずに進んで課金を取り残すより destroy を止める意図的な安全側の設計であり、`aws ec2 describe-instances` で孤立インスタンスを確認してから再実行する。

**30 分でタイムアウトした場合は本当にノードが詰まっている。** `kubectl get nodeclaims` と `aws ec2 describe-instances` で Finalizer の残存や Karpenter コントローラの異常終了を確認してから再実行する。

**NAT 消失後に `EC2NodeClass` の finalizer が残ることがある。** 対応する EC2 インスタンスは既に終了済みで課金への影響はない。気になる場合は手動で外す: `kubectl patch ec2nodeclass <name> --type=merge -p '{"metadata":{"finalizers":[]}}'`

**CloudFront デモは本番運用を想定していない。** ALB リスナーは HTTP/80 のまま、ACM 証明書も WAF も付けていない。本番要件では ALB への ACM 証明書追加による HTTPS 化と、CloudFront VPC Origins・WAFv2 の追加が必要になる。

**共有クラスタでは実行前に `kubectl config current-context` を必ず確認する。** この章の操作はクラスタ全体またはアクセラレータプール全体に影響する破壊的操作であり、意図しないコンテキストへの誤実行を避ける。
