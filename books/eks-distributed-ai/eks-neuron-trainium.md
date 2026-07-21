---
title: "Basic07 - Neuron/Trainium 対応 (設計と現状)"
free: true
---

本章では、これまで GPU 向けに組んできた Karpenter の `accelerator_pools` の仕組みに、Trainium/Inferentia（AWS Neuron）を組み込む方法を扱います。`device_plugin = "neuron"` と書くだけで同じ枠組みに乗る一方、マルチノードでの Neuron over EFA 検証はまだ完了していません。できていることとできていないことを分けて、正直に見ていきます。

# 解説

## 全体構成

![EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち Karpenter が管理する NodePool 群のうち **Neuron（Trainium/Inferentia）向けのアクセラレータプール** の部分です。GPU プールとは taint の key が違うだけで、同じ Karpenter の仕組みに乗ります。

## これは何をするものか

Ch3 で見た `accelerator_pools` は、GPU と Neuron を「taint の key が違うだけの同じ形のノード」として 1 つの型で表現していました。本章では、その Neuron 側の実装を具体的に見ていきます。

Neuron 対応の骨格は 3 つの部品からできています。

1. **NodePool / EC2NodeClass** — Ch3 と同じ `for_each` レンダリングを、`device_plugin = "neuron"` のプールにもそのまま適用します。GPU プール専用の分岐は存在しません。
2. **Neuron AL2023 AMI** — Trainium/Inferentia のドライバ（`aws-neuronx-dkms`）は EKS Optimized AL2023 Neuron AMI にすでに同梱されています。GPU Operator が担っていた「ドライバをどうするか」という判断は Neuron 側には存在せず、AMI を正しく選ぶだけで済みます。
3. **Neuron device plugin（Helm add-on）** — ドライバの上に乗る、Kubernetes に対して `aws.amazon.com/neuron` という resource を advertise する DaemonSet です。これだけを Helm で追加します。

`neuron-addons.tf` では、`neuron-helm-chart`（`oci://public.ecr.aws/neuron/neuron-helm-chart`）を `local.has_neuron_pool`（`accelerator_pools` の中に `device_plugin = "neuron"` のエントリが 1 つでもあるかどうか）で条件付き導入しています。GPU のみのクラスタではこのリソースは 0 個になり、何も入りません。

AMI の選び方には `ami_ssm_parameter` というフィールドを使います。`ami_alias`（`"al2023@latest"`）に任せると alias 解決の挙動に依存してしまうため、Neuron 用途では EKS Optimized AL2023 Neuron AMI の SSM パラメータパスを明示的に指定してピン留めするのが確実です。

もう 1 点、GPU 側の知識をそのまま持ち込むと誤解しやすいのが taint の管理主体です。Neuron device plugin もアクセラレータの taint を自分では付けません。GPU Operator が taint を付けないのと同じ理屈で、これは NodePool 側が明示的に `aws.amazon.com/neuron: NoSchedule` を宣言しています。

テンソル並列で複数の Neuron デバイスを跨ぐ推論・訓練を行う場合は、`neuron_enable_scheduler = true` で Neuron Scheduler Extension を有効にします。これは複数デバイスへの割り当てが contiguous な device ID になることを保証する拡張で、単一デバイスの推論のように 1 プロセスが 1 デバイスしか使わない場合は不要です。デフォルトは `false` です。

マルチノードで Neuron over EFA（trn2 同士を EFA で接続して collective 通信を行う構成）についても、配線自体はすでにテーブルに組み込まれています。EFA device plugin のトレランス一覧には `aws.amazon.com/neuron` が含まれており、`locals.tf` の EFA トポロジテーブルには `trn2.48xlarge`（16 カード）や `trn1.32xlarge` などの Neuron インスタンスタイプも GPU インスタンスタイプと同じ形式で登録されています。つまり Terraform の設計上は「Neuron プールでも EFA が自動的に紐づく」ところまで作られています。しかし、この構成でマルチノードの Neuron ワークロードを実機で流し、EFA 経由の collective 通信が実際に機能することを確認する検証はまだ行っていません。単一ノードの Neuron プローブと単一ノードの vLLM サービングまでは動作を確認済みですが、その先は将来課題として正直に残しています。

## 全体の中での位置付け

Ch3 で作った `accelerator_pools` の型は GPU/Neuron 共通です。本章はその型に `device_plugin = "neuron"` のエントリを 1 つ追加し、`neuron-addons.tf` が条件付きで Neuron device plugin を導入するところまでを扱います。EFA を使ったマルチノード構成（Ch5 相当）とも配線上はつながっていますが、Neuron での実機検証はまだ単一ノードにとどまっており、その先は本 book の範囲外として切り出しています。

## 注意

**Karpenter + Neuron の NPD/DRA は非サポートです。** `neuron-helm-chart` の Node Problem Detector（NPD）と Dynamic Resource Allocation（DRA、Kubernetes 1.30+ のデバイス割り当て API）はこの構成では明示的に無効化しています。Karpenter がノードのライフサイクルを管理する構成では、Neuron の NPD/DRA は組み合わせとして未サポートのため、有効化しません。

**EFA device plugin の toleration に `aws.amazon.com/neuron` を含めないと詰みます。** EFA device plugin は GPU と Neuron の両方のプールで共有される 1 つの DaemonSet であり、その toleration に Neuron taint が入っていなければ trn2 ノードには乗れません。乗れなければ `vpc.amazonaws.com/efa` がそのノードで一切 advertise されず、EFA を要求する Pod は理由の分かりにくい Pending のまま止まります。この構成ではすでに toleration に含めてありますが、自分で device plugin の設定を変更する場合はここを崩さないよう注意してください。

**マルチノード Neuron over EFA は「設計上組み込まれている」と「検証済み」は別です。** NodePool・EC2NodeClass・EFA トポロジテーブルまではコード上 GPU と同じ扱いで実装されていますが、実機の多ノード collective 通信で性能・安定性を確認したわけではありません。過度な期待は禁物で、本番投入前には自分の環境で検証するステップを挟むべきです。

**Neuron Scheduler Extension を入れ忘れると多デバイス割り当てが崩れる可能性があります。** 複数の Neuron デバイスを跨ぐテンソル並列のワークロードを流すのに `neuron_enable_scheduler` を `false`（デフォルト）のままにすると、contiguous な device ID 割り当てが保証されません。単一デバイスの推論だけなら気にする必要はありませんが、複数デバイスをまとめて使う構成に切り替えるときは、このフラグを忘れずに `true` にしてください。

# ワークショップ実施

## 1. terraform.tfvars に Neuron プールを追加する

`accelerator_pools` に、Capacity Block を使う trn2 のサービング用プールを追加します。

```hcl
trn2-serving = {
  instance_types    = ["trn2.48xlarge"]
  device_plugin     = "neuron"
  capacity_type     = "reserved"
  zone              = "<az>"
  cb_reservation_id = "<capacity-block-reservation-id>"
  ami_ssm_parameter = "/aws/service/eks/optimized-ami/1.35/amazon-linux-2023/x86_64/neuron/recommended/image_id"
  volume_size       = "500Gi"
}
```

`device_plugin = "neuron"` に切り替えている以外、フィールドの構造は Ch3 で書いた GPU プールと同じです。`cb_reservation_id` は事前に確保した Capacity Block の予約 ID に置き換えます。

## 2. apply する

```bash
cd infra/eks
terraform apply
```

`has_neuron_pool` が `true` になったことを検知して、Neuron device plugin（と `neuron_enable_scheduler = true` にしていれば Scheduler Extension）が導入されます。EFA device plugin もすでに `aws.amazon.com/neuron` taint を tolerate する設定で導入済みのため、追加の変更は不要です。

## 3. Neuron probe pod で device plugin の advertise を確認する

```bash
NAMESPACE=<namespace>
sed "s/__NAMESPACE__/${NAMESPACE}/g" manifests/neuron-probe-pod.yaml.tpl | kubectl apply -f -
```

このプローブ Pod は `trn2-serving` の NodePool に `nodeSelector` で固定され、`aws.amazon.com/neuron` taint と `vpc.amazonaws.com/efa` taint への toleration を持ちます。Pod が `Running` になったら、内部で `neuron-ls` を実行してデバイスが正しく列挙されるか確認します。

```bash
kubectl -n $NAMESPACE exec neuron-probe -- neuron-ls
```

trn2.48xlarge であれば 16 個の Trainium2 デバイスが表示されるはずです。これが確認できれば、「AMI にドライバが載っている」「device plugin がリソースを advertise している」「Pod がそのリソースを要求してスケジュールされる」という一連の流れが単一ノードでは通っていることになります。

## 4. マルチノードは未検証であることを確認する

複数の trn2 ノードを EFA で束ねた collective 通信（多ノードのテンソル並列や分散訓練）を試す場合は、この構成の配線（EFA device plugin の toleration・EFA トポロジテーブル）だけを前提にせず、実機での動作確認を自分で積む必要があります。この book の範囲では単一ノードまでの確認に留めています。

:::message alert
マルチノード Neuron over EFA は設計上の配線は組み込まれていますが、実機での collective 通信の動作確認はまだ行っていません。本番投入前には必ず自分の環境で検証してください。
:::

# まとめ

本章では、Karpenter の `accelerator_pools` に Neuron（Trainium/Inferentia）を組み込む方法を扱いました。骨格は NodePool/EC2NodeClass・Neuron AL2023 AMI・Neuron device plugin の 3 つで、`device_plugin = "neuron"` と書くだけで GPU と同じ枠組みに乗ります。単一ノードでのドライバ・device plugin・スケジューリングの動作は確認済みですが、マルチノードでの Neuron over EFA collective 通信は配線上組み込まれているだけで実機検証は未完了です。NPD/DRA は Karpenter との組み合わせで非サポートである点、EFA device plugin の toleration を崩さない点、複数デバイス利用時は `neuron_enable_scheduler` を忘れない点の 3 つが実運用上の注意点です。

# 参考資料

- [AWS Neuron ドキュメント](https://awsdocs-neuron.readthedocs-hosted.com/)
- [Neuron device plugin (neuron-helm-chart)](https://github.com/aws-neuron/neuron-helm-charts)
- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
