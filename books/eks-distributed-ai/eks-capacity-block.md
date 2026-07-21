---
title: "Basic05 - Capacity Block を取得して組み込む"
free: true
---
本章では、Ch3 で `accelerator_pools` に用意しておいた `capacity_type = "reserved"` という選択肢を実際に使い、H200/Trainium の予約キャパシティ（Capacity Block）を EKS クラスタに組み込みます。予約の検索・購入から `terraform.tfvars` への反映、NCCL/EFA での動作確認、期限管理までの一連の運用フローを扱います。

# 解説

## 全体構成

![EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち **Capacity Block による予約 GPU/Trainium ノードの調達** と、その期限を監視する **EventBridge → SNS のアラート経路** です。アクセラレータプールの構造自体は Ch3 までに作った型をそのまま使います。

## これは何をするものか

Capacity Block（CB）とは、H100/H200/Trainium のような希少なアクセラレータを、固定の開始時刻・終了時刻・インスタンス数で前払い予約する EC2 の仕組みです。Spot インスタンスと違い、予約期間中に中断されることはありません。しかし Spot とは逆の意味で扱いが難しい面があります。期間が終わればどれだけジョブが残っていても強制的に容量が回収される、という一方向の期限が必ず来るためです。

なぜ CB が必要になるのでしょうか。p5en.48xlarge や trn2.48xlarge のような大型インスタンスは、On-Demand で `RunInstances` を呼んでも `InsufficientInstanceCapacity` で弾かれることが多くあります。需要が旺盛な GPU/Trainium は、AWS 側が容量をあらかじめ区切って予約販売する CB というチャネルでなければ実質手に入らない、というのが実務上の前提になります。

CB を使う運用フローは次のようになります。

1. `describe-capacity-block-offerings` でオファリング（購入可能な予約の候補）を検索する
2. オファリングを選んで購入し、Capacity Reservation ID（`cr-...`）を受け取る
3. `cr-...` を `accelerator_pools` の該当プールに書き込み `terraform apply` する
4. NCCL/EFA でノード間通信を検証する
5. 終了時刻の 1 時間前に自動でアラートが飛ぶようにしておく
6. 終了前にワークロードを退避し、teardown する

この章に付属する helper script は `00-check-cb-offerings.sh` から `04-teardown.sh` までの 5 つで、上記の手順 1・2・3・4・6（検索・購入・反映・検証・teardown）にそれぞれ 1 対 1 で対応しています。番号の順に実行すれば迷わない構成にしています。手順 5 の期限アラートだけはスクリプト実行を挟まず、`cb_end_date` を設定するだけで Terraform が自動的に組み立てます。

さらに、この構成には `cb_end_date` から自動的に期限アラートを組み立てる仕組みが入っています。プールに `cb_end_date` を書いておくと、Terraform が EventBridge Scheduler の one-shot ルールを 1 プールにつき 1 つ作り、終了 1 時間前に SNS へ通知します。発火後はそのスケジュール自体が自己削除されるため、`terraform apply` を重ねても過去の時刻でスケジュールを再作成しようとして API に拒否される、という事故を避けられます。

最後にコストの話をしておきます。CB は前払いで、購入後のキャンセルや返金はできません。p5en.48xlarge を 24 時間予約するだけで数百ドルから千ドル台のオーダーの upfront fee がかかります。したがって、CB を買う前に g6e のような On-Demand で十分に手が届く GPU インスタンスでコードとマニフェストの動作を検証しておき、CB は「動作確認済みのジョブを、確保した本番規模のアクセラレータに載せる」という最後のステップに使うのが安全な段階的アプローチになります。

## 全体の中での位置付け

Ch3 までに作った `accelerator_pools` という型に、本章では「予約 ID をどう埋めるか」という運用手順を積み重ねます。プール定義そのものの構造は変わりません。CB は容量調達の一手段であり、NodePool/NodeClass の設計自体には手を入れないという点が、この章を Ch3〜Ch4 の続きとして位置づける根拠になります。

## 注意

分散 AI 特有の落とし穴が 4 つあります。いずれも一度ハマると原因特定に時間がかかるため、最初に押さえておきます。

**注意 1: CB ノードが起動しない「filtered out all instance types」。** apply 後に Karpenter がノードを起動できない場合、直前に同じ予約を使っていた別インスタンスがまだ終了処理中で、予約スロットが実際には空いていないことが多くあります。少し待って Karpenter のリトライに任せれば解決するケースがほとんどです。

**注意 2: `capacity-reservation` taint も別に存在する。** CB ノードには、プールの `device_plugin` に応じた `nvidia.com/gpu` または `aws.amazon.com/neuron` の taint に加えて、予約ごとに値が変わる `capacity-reservation` taint も付きます。値が予約ごとに変わるため、ワークロード側は `operator: Exists` で値を問わずに tolerate する必要があります。`operator: Equal` で固定値を書いてしまうと、次に別の CB を買い直した瞬間にジョブがスケジュールされなくなります。

**注意 3: `cb_end_date` は UTC 必須（末尾 `Z`）。** EventBridge Scheduler は `schedule_expression_timezone = "UTC"` で動いているため、`cb_end_date` にタイムゾーンオフセット付き（例: `+09:00`）の値を書くと、そのオフセットは無視されて UTC として再解釈されます。結果としてアラートが実際の期限から最大で日付をまたぐほどずれます。この構成では変数の `validation` で `cb_end_date` が空でない限り末尾が `Z` であることを強制し、plan 時点で弾いています。

**注意 4: `cb_reservation_id` を設定しても `capacity_type` を `"reserved"` にし忘れると予約が無視される。** `cb_reservation_id` だけ書いて `capacity_type` を `"on-demand"` のままにすると、EC2NodeClass は `capacityReservationSelectorTerms` を持たずに生成され、予約はまるごと無視されて On-Demand で起動します。前払いした予約分の費用に加えて On-Demand の費用も別にかかる、という二重コストに直結するため、この構成では変数の `validation` でこの組み合わせを明示的に弾いています。

なお、EventBridge スケジュールは発火後に自己削除される点にも注意が必要です。`action_after_completion = "DELETE"` を設定しているため、アラートが一度発火するとその `aws_scheduler_schedule` リソースは AWS 側から消えます。Terraform 側もこれに追従できるよう、アラート時刻（`cb_end_date - 1h`）が過去になったプールは `for_each` の対象から自動的に除外される仕組みになっています。これがないと、発火済みで消えたスケジュールを次の plan が「過去の時刻で作り直そう」として EventBridge API に reject され続けることになります。

# ワークショップ実施

## 1. オファリングを検索する（読み取りのみ、課金なし）

```bash
cd infra/eks/scripts
./00-check-cb-offerings.sh --region us-east-2 --duration-hours 24
```

`describe-capacity-block-offerings` を叩き、インスタンスタイプ・AZ・開始/終了時刻・upfront fee の一覧を表示するだけの read-only スクリプトです。この時点ではまだ何も購入していないので、何度実行してもコストは発生しません。欲しいインスタンスタイプ・期間・台数で候補が出るまでオプションを変えて試します。

## 2. CB を購入する（ここで課金が発生する）

```bash
./01-purchase-cb.sh \
  --offering-id <offering-id> \
  --instance-type p5en.48xlarge \
  --instance-count 1
```

このスクリプトは実際に `purchase-capacity-block` を呼びます。実行前に upfront fee を含む価格サマリーを表示し、`y` の入力を求める確認プロンプトを挟みます。ここでの購入は取り消しできないため、必ず予算の承認を得てから `y` を入力してください。購入が成功すると Capacity Reservation ID（`cr-...`）と `EndDate` が出力されます。

:::message alert
CB の購入は前払いで、キャンセルや返金はできません。`00-check-cb-offerings.sh` で候補を確認し、必要台数・期間を十分に見積もってから購入してください。
:::

## 3. terraform.tfvars に反映するブロックを生成する

```bash
./02-post-purchase.sh \
  --cr-id cr-0123456789abcdef0 \
  --end-date 2026-07-21T12:00:00Z \
  --instance-type p5en.48xlarge \
  --zone us-east-2a \
  --pool gpu-p5en
```

このスクリプトは AWS に対して何も呼びません。前のステップで得た `cr-...` と終了時刻を、`accelerator_pools` に貼り付けられる HCL ブロックとして標準出力に整形するだけです。

```hcl
gpu-p5en = {
  instance_types    = ["p5en.48xlarge"]
  device_plugin     = "nvidia"
  capacity_type     = "reserved"
  zone              = "us-east-2a"
  cb_reservation_id = "cr-0123456789abcdef0"
  cb_end_date       = "2026-07-21T12:00:00Z"
  volume_size       = "500Gi"
}
```

出力されたブロックを `terraform.tfvars` の `accelerator_pools` に貼り付けます。

## 4. apply してノードを確認する

```bash
cd infra/eks
terraform apply
kubectl get nodes -l karpenter.sh/capacity-type=reserved
kubectl get nodeclaims -l karpenter.sh/nodepool=gpu-p5en
```

`CAPACITY = reserved` の NodeClaim が表示され、`ZONE` が指定した単一 AZ に一致していれば、CB からのノード起動は成功です。

## 5. マルチノードなら NCCL/EFA を検証する

```bash
./03-verify-nccl.sh --nodes 2 --gpus-per-node 8 \
  --namespace <ns> --image <nccl-tests-image>
```

MPIJob を使って 2 ノード間の `all_reduce_perf` を実行し、busbw（EFA 経由の実効帯域）を確認します。単体ノードの NVLink 内帯域だけでなく、CB で確保した複数ノードが実際に EFA/RDMA で正しく通信できているかをここで確かめます。

## 6. 期限アラートを確認する

```bash
terraform output cb_expiry_alert_schedule_exprs
terraform output cb_expiry_sns_topic_arn
```

実機出力:
```
cb_expiry_alert_schedule_exprs = {
  "gpu-p5en" = "at(2026-07-21T10:30:00)"
}
cb_expiry_sns_topic_arn = "arn:aws:sns:<region>:<account>:distai-eks-<name>-cb-expiry-alert"
```

`cb_end_date`（または予約から自動導出された終了時刻）の 1 時間前に `at()` 式の EventBridge Scheduler ルールが 1 つ作られ、共有 SNS トピックにメール通知が届きます。通知が来たらワークロードの graceful drain を開始します。

## 7. teardown する

```bash
./04-teardown.sh --namespace <ns> --nodepool gpu-p5en
```

Deployment/StatefulSet/Job/MPIJob を削除し、GPU Pod が完全に終了したのを確認したうえで Karpenter の NodePool を削除します。CB のノード自体は予約期間の終了時に AWS 側で強制回収されるため、このスクリプトは「ワークロードを安全に退避させる」ところまでを担当します。クラスタ全体を壊す `terraform destroy` は `--destroy` を明示した場合のみ実行されます。

# まとめ

本章では、Ch3 で用意した `capacity_type = "reserved"` を使い、Capacity Block の検索・購入から `terraform.tfvars` への反映、NCCL/EFA での動作確認、期限アラート、teardown までの一連の運用フローを構築しました。CB は前払いで取り消しができないため、On-Demand で動作確認を済ませたジョブを最後に載せる、という段階的な使い方が安全です。`cb_end_date` の UTC 指定や `capacity_type` との組み合わせなど、Terraform の `validation` で弾いている落とし穴も合わせて押さえておけば、期限管理まで含めた CB 運用を事故なく回せます。

# 参考資料

- [Amazon EC2 Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html)
- [Amazon EventBridge Scheduler](https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-training)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
</content>
