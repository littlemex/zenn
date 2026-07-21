---
title: "Basic02 - Karpenter を導入する"
free: true
---

本章では、Pod のリソース要求に応じて GPU/Neuron ノードを動的に起動する Karpenter を導入します。CRD 管理・認証方式（Pod Identity）・Spot 中断通知（SQS）まで含めて、安定して運用できる形で入れます。この章の時点ではまだアクセラレータノードは 1 台も立ちませんが、次章以降で `accelerator_pools` を定義したときにノードを自動起動する「エンジン」をここで用意します。

# 解説

## 全体構成

この book 全体の構成のうち、本章で扱うのは EKS コントロールプレーンと System ノードの上で動く **Karpenter コントローラ**（および CRD・SQS interruption queue）です。Karpenter は次章以降で定義する NodePool を読み取り、要求に応じて GPU/Neuron ノードを起動します。

![EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

## これは何をするものか

Karpenter は、スケジュールできずに `Pending` のままになっている Pod のリソース要求を監視し、それを満たす EC2 インスタンスを自動的に起動・終了させる Kubernetes コントローラです。ノードを事前にまとめて用意しておく EKS Managed Node Group とは発想が逆で、Pod が要求してから初めてノードが立つ「demand-driven」なプロビジョニングを行います。

この構成で Managed Node Group ではなく Karpenter を選ぶ理由は 2 つあります。1 つは、この基盤で使うアクセラレータの型が `g6e` 系 GPU、`p5en` 系 GPU、`trn2` 系 Neuron など多様で、ワークロードごとに必要な型が変わることです。Managed Node Group はインスタンスタイプの組み合わせごとにグループを作る必要があり、型の種類が増えるほど管理コストが跳ね上がります。もう 1 つは、GPU/Neuron インスタンスは時間単価が高く、常時起動しておくコストが大きいことです。Karpenter は Pod が要求したときだけノードを起動し、不要になれば consolidation で終了させるため、使った分だけ課金する運用に向いています。

導入の実装上のポイントは 3 つあります。

1 つ目は CRD の管理方法です。Karpenter が使う `EC2NodeClass` / `NodePool` / `NodeClaim` の CRD は、コントローラ本体の Helm chart（`karpenter`）とは別の chart（`karpenter-crd`）として提供されています。これは Helm の仕様上、chart の `crds/` ディレクトリに含まれる CRD は `helm upgrade` の対象外で、初回インストール時のスキーマのまま更新されないためです。`karpenter-crd` を同じバージョンで別チャートとして管理すれば、バージョンアップ時に CRD のスキーマも一緒に更新できます。

2 つ目は認証方式です。Karpenter コントローラが EC2 の起動・終了などの AWS API を呼ぶために必要な権限は、ServiceAccount に IAM ロールをアノテーションで結び付ける IRSA 方式ではなく、EKS Pod Identity を使って付与します。Pod Identity は IAM ロールとの結び付けを ServiceAccount 定義ではなく Kubernetes クラスタの外（IAM 側の Pod Identity Association）で完結させられるため、設定がシンプルになります。

3 つ目は Spot 中断への対応です。Karpenter は SQS の interruption queue を経由して、Spot インスタンスの中断通知や AWS のヘルスイベント、リバランス推奨を受け取り、対象ノード上の Pod を強制終了ではなく graceful に drain してから終了させます。この queue と、通知を queue に流す EventBridge ルールの作成も、Karpenter 導入の一部として行います。

## 全体の中での位置付け

前章（Ch1）で用意した System ノードの上に、本章で Karpenter コントローラを載せます。Karpenter 自身はまだ何の NodePool も持たないため、この時点ではアクセラレータノードは 1 台も起動しません。次章（Ch3）で `accelerator_pools` を定義すると、その Karpenter が定義に従って GPU/Neuron ノードを起動する、という流れになります。本章は「エンジンだけ載せて、燃料はまだ入れていない」状態を作る章です。

## 注意

**注意 1: `featureGates.reservedCapacity` を Helm values に書かない。** このフラグは Karpenter v1.13.0 のコンパイル時点で `true` がデフォルトになっています。Helm values に明示的にキーを書くと、chart 側のスキーマに存在しないキーとして扱われてエラーになる場合があります。バージョンごとのデフォルト値は chart の `values.yaml` で確認し、デフォルトと同じ値をわざわざ明示しないようにします。

**注意 2: `expireAfter` は ISO-8601 ではなく Go の duration 文字列で書く。** NodePool で使う `expireAfter` フィールドは `P1D` のような ISO-8601 形式ではなく、`"24h"` のような Go 標準の duration 文字列を期待します。ISO-8601 の書式で書いても apply 自体は通ってしまう場合があり、実際の期限が意図した値と違うことに気づきにくいので注意します。

**注意 3: CRD が `Established` になる前の NodePool apply は失敗しうる。** `karpenter-crd` chart のインストール直後、まだ Kubernetes API サーバーに CRD が完全に登録されていない状態で `NodePool` リソースを apply すると失敗することがあります。これは一時的な状態なので、再実行すれば通ることが多い既知の flake として扱ってよいです。

**注意 4: ECR Public の認証トークンは `us-east-1` から取る。** Karpenter の Helm chart は `oci://public.ecr.aws/karpenter` から取得しますが、ECR Public の認証トークンは AWS API の制約でリージョンを `us-east-1` に固定して取得する必要があります。他のリージョンからトークンを取ろうとすると認証エラーになります。

# ワークショップ実施

Karpenter は Ch1 の `terraform apply` に含めて導入済みの構成です。ここでは導入結果を確認します。

## 1. Karpenter コントローラの起動を確認する

```bash
kubectl -n karpenter get pods
```

`karpenter` namespace で controller Pod が `Running` になっていることを確認します。Ch1 で作った System ノード（`nodeSelector: karpenter.sh/controller: "true"`）の上にスケジュールされているはずです。

## 2. CRD が入っていることを確認する

```bash
kubectl get crd | grep karpenter
```

`ec2nodeclasses.karpenter.k8s.aws` / `nodeclaims.karpenter.sh` / `nodepools.karpenter.sh` の 3 つが表示されれば、`karpenter-crd` chart による CRD 登録が完了しています。

## 3. 2 つのチャートが別々に入っていることを確認する

```bash
helm list -n karpenter
```

`karpenter-crd` と `karpenter` が別リリースとして並びます。バージョンを上げるときはこの 2 つを同じバージョンで揃えて `helm upgrade` します。

## 4. まだノードが増えていないことを確認する

```bash
kubectl get nodes
```

この時点では `NodePool` を 1 つも定義していないため、Karpenter はまだ起動先の情報を持ちません。表示されるノードは Ch1 の System ノードのみで、GPU/Neuron ノードは増えていません。これが demand-driven なプロビジョニングの動作確認になります。

# まとめ

本章では、GPU/Neuron ノードを要求に応じて起動する Karpenter を導入しました。CRD を別チャート（`karpenter-crd`）で管理してバージョンアップに追従できるようにし、認証は Pod Identity、Spot 中断は SQS interruption queue で graceful に処理する構成です。この時点ではまだノードは増えませんが、次章で `accelerator_pools` を定義すると、この Karpenter が実際に GPU/Neuron ノードを起動し始めます。

# 参考資料

- [Karpenter 公式ドキュメント](https://karpenter.sh/)
- [EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [Karpenter on EKS (AWS ドキュメント)](https://docs.aws.amazon.com/eks/latest/userguide/karpenter.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
