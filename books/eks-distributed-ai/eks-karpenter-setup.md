---
title: "Karpenter 導入 — ノードプロビジョニングの仕組み"
---

## メインテーマ

Pod のリソース要求に応じてアクセラレータノードを動的にプロビジョニングする Karpenter を、CRD 管理・認証方式・中断通知まで含めて安定運用できる形で導入する。

## これは何をするものか

Karpenter は、スケジュールできずに `Pending` のままになっている Pod のリソース要求を監視し、それを満たす EC2 インスタンスを自動的に起動・終了させる Kubernetes コントローラである。ノードを事前にまとめて用意しておく EKS Managed Node Group とは発想が逆で、Pod が要求してから初めてノードが立つ「demand-driven」なプロビジョニングを行う。

この構成で Managed Node Group ではなく Karpenter を選ぶ理由は 2 つある。1 つは、この基盤で使うアクセラレータの型が `g6e` 系 GPU、`p5en` 系 GPU、`trn2` 系 Neuron など多様で、ワークロードごとに必要な型が変わることである。Managed Node Group はインスタンスタイプの組み合わせごとにグループを作る必要があり、型の種類が増えるほど管理コストが跳ね上がる。もう 1 つは、GPU/Neuron インスタンスは時間単価が高く、常時起動しておくコストが大きいことである。Karpenter は Pod が要求したときだけノードを起動し、不要になれば consolidation で終了させるため、使った分だけ課金する運用に向いている。

導入の実装上のポイントは 3 つある。1 つ目は CRD の管理方法である。Karpenter が使う `EC2NodeClass` / `NodePool` / `NodeClaim` の CRD は、コントローラ本体の Helm chart（`karpenter`）とは別の chart（`karpenter-crd`）として提供されている。これは Helm の仕様上、chart の `crds/` ディレクトリに含まれる CRD は `helm upgrade` の対象外で、初回インストール時のスキーマのまま更新されないためである。`karpenter-crd` を同じバージョンで別チャートとして管理すれば、バージョンアップ時に CRD のスキーマも一緒に更新できる。

2 つ目は認証方式である。Karpenter コントローラが EC2 の起動・終了などの AWS API を呼ぶために必要な権限は、ServiceAccount に IAM ロールをアノテーションで結び付ける IRSA 方式ではなく、EKS Pod Identity を使って付与する。Pod Identity は IAM ロールとの結び付けを `kubectl` 側の ServiceAccount 定義ではなく Kubernetes クラスタの外（IAM 側の Pod Identity Association）で完結させられるため、設定がシンプルになる。

3 つ目は Spot 中断への対応である。Karpenter は SQS の interruption queue を経由して、Spot インスタンスの中断通知や AWS のヘルスイベント、リバランス推奨を受け取り、対象ノード上の Pod を強制終了ではなく graceful に drain してから終了させる。この queue と、通知を queue に流す EventBridge ルールの作成も、Karpenter 導入の一部として行う。

## 全体の中での位置付け

前チャプターで用意した System ノードの上に Karpenter コントローラを載せる。Karpenter 自身はまだ何のノードプールも持たないため、この時点ではアクセラレータノードは 1 台も起動しない。

![チャプターの位置付け](/images/books/eks-distributed-ai/ch2-karpenter.png)

## 実際に挙動を確認する

### 1. Karpenter コントローラの起動確認

```bash
kubectl -n karpenter get pods
```

`karpenter` namespace で controller Pod が `Running` になっていることを確認する。前チャプターで作った System ノード（`nodeSelector: karpenter.sh/controller: "true"`）の上にスケジュールされているはずである。

### 2. CRD が入っていることを確認

```bash
kubectl get crd | grep karpenter
```

`ec2nodeclasses.karpenter.k8s.aws` / `nodeclaims.karpenter.sh` / `nodepools.karpenter.sh` の 3 つが表示されれば、`karpenter-crd` chart による CRD 登録が完了している。

### 3. 2 つのチャートが別々に入っていることを確認

```bash
helm list -n karpenter
```

`karpenter-crd` と `karpenter` が別リリースとして並んでいる。バージョンを上げるときはこの 2 つを同じバージョンで揃えて `helm upgrade` する。

### 4. まだノードは増えていないことを確認

```bash
kubectl get nodes
```

この時点では `NodePool` を 1 つも定義していないため、Karpenter はまだ起動先の情報を持たない。表示されるノードは前チャプターの System ノードのみで、GPU/Neuron ノードは増えていない。これが demand-driven なプロビジョニングの動作確認になる。

## 注意点

**`featureGates.reservedCapacity` を Helm values に書かない。** このフラグは v1.13.0 のコンパイル時点で `true` がデフォルトになっている。Helm values に明示的にキーを書くと、chart 側のスキーマに存在しないキーとして扱われてエラーになる場合がある。バージョンごとのデフォルト値は chart の `values.yaml` で確認し、デフォルトと同じ値をわざわざ明示しないようにする。

**`expireAfter` は ISO-8601 ではなく Go の duration 文字列で書く。** NodePool で使う `expireAfter` フィールドは `P1D` のような ISO-8601 形式ではなく、`"24h"` のような Go 標準の duration 文字列を期待する。ISO-8601 の書式で書いても apply 自体は通ってしまう場合があり、実際の期限が意図した値と違うことに気づきにくい。

**CRD が `Established` になる前の NodePool apply は失敗しうる。** `karpenter-crd` chart のインストール直後、まだ Kubernetes API サーバーに CRD が完全に登録されていない状態で `NodePool` リソースを apply すると失敗することがある。これは一時的な状態なので、再実行すれば通ることが多い既知の flake として扱ってよい。

**ECR Public の認証トークンは `us-east-1` から取る。** Karpenter の Helm chart は `oci://public.ecr.aws/karpenter` から取得するが、ECR Public の認証トークンは AWS API の制約でリージョンを `us-east-1` に固定して取得する必要がある。他のリージョンからトークンを取ろうとすると認証エラーになる。
