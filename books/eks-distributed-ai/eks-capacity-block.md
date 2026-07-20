---
title: "Capacity Block ライフサイクル — 予約 GPU/Trainium を使う"
---

## メインテーマ

Capacity Block（CB）で確保した H200/Trainium の予約キャパシティを EKS クラスタに組み込み、期限管理まで含めた運用フローを構築する。

## これは何をするものか

Ch3 で `accelerator_pools` に `capacity_type = "reserved"` という選択肢があることに触れた。このチャプターでは、その「予約」を実際に手に入れて使い切るまでの一連の流れを扱う。

Capacity Block とは、H100/H200/Trainium のような希少なアクセラレータを、固定の開始時刻・終了時刻・インスタンス数で前払い予約する EC2 の仕組みである。Spot インスタンスと違い、予約期間中に中断されることはない。しかし Spot とは逆の意味で扱いが難しい面がある。期間が終わればどれだけジョブが残っていても強制的に容量が回収される、という一方向の期限が必ず来る。

なぜ CB が必要になるのか。p5en.48xlarge や trn2.48xlarge のような大型インスタンスは、On-Demand で `RunInstances` を呼んでも `InsufficientInstanceCapacity` で弾かれることが多い。需要が旺盛な GPU/Trainium は、AWS 側が容量をあらかじめ区切って予約販売する CB というチャネルでなければ実質手に入らない、というのが実務上の前提になる。

CB を使う運用フローは次のようになる。

1. `describe-capacity-block-offerings` でオファリング（購入可能な予約の候補）を検索する
2. オファリングを選んで購入し、Capacity Reservation ID（`cr-...`）を受け取る
3. `cr-...` と終了日時を `accelerator_pools` の該当プールに書き込み `terraform apply` する
4. NCCL/EFA でノード間通信を検証する
5. 終了時刻の 1 時間前に自動でアラートが飛ぶようにしておく
6. 終了前にワークロードを退避し、teardown する

この章に付属する helper script は `00-check-cb-offerings.sh` から `04-teardown.sh` までの 5 つで、上記の手順 1〜4・6（検索・購入・反映・検証・teardown）にそれぞれ 1 対 1 で対応する。番号の順に実行すれば迷わない構成にしている。手順 5 の期限アラートだけはスクリプト実行を挟まず、`cb_end_date` を設定するだけで Terraform が自動的に組み立てる。

さらに、この構成には `cb_end_date` から自動的に期限アラートを組み立てる仕組みが入っている。プールに `cb_end_date` を書いておくと、Terraform が EventBridge Scheduler の one-shot ルールを 1 プールにつき 1 つ作り、終了 1 時間前に SNS へ通知する。発火後はそのスケジュール自体が自己削除されるため、`terraform apply` を重ねても past の時刻でスケジュールを再作成しようとして API に拒否される、という事故を避けられる。

最後にコストの話をしておく。CB は前払いで、購入後のキャンセルや返金はできない。p5en.48xlarge を 24 時間予約するだけで数百ドルから千ドル台のオーダーの upfront fee がかかる。したがって、CB を買う前に g6e のような On-Demand で十分に手が届く GPU インスタンスでコードとマニフェストの動作を検証しておき、CB は「動作確認済みのジョブを、確保した本番規模のアクセラレータに載せる」という最後のステップに使うのが安全な段階的アプローチになる。

## 全体の中での位置付け

![チャプターの位置付け](/images/books/eks-distributed-ai/ch5-capacity-block.png)

Ch3 までに作った `accelerator_pools` という型に、このチャプターでは「予約 ID をどう埋めるか」という運用手順を積み重ねる。プール定義そのものの構造は変わらない。

## 実際に挙動を確認する

### 1. オファリングを検索する（読み取りのみ、課金なし）

```bash
cd infra/eks/scripts
./00-check-cb-offerings.sh --region us-east-2 --duration-hours 24
```

`describe-capacity-block-offerings` を叩き、インスタンスタイプ・AZ・開始/終了時刻・upfront fee の一覧を表示するだけの read-only スクリプトである。この時点ではまだ何も購入していないので、何度実行してもコストは発生しない。欲しいインスタンスタイプ・期間・台数で候補が出るまでオプションを変えて試す。

### 2. CB を購入する（ここで課金が発生する）

```bash
./01-purchase-cb.sh \
  --offering-id <offering-id> \
  --instance-type p5en.48xlarge \
  --instance-count 1
```

このスクリプトは実際に `purchase-capacity-block` を呼ぶ。実行前に upfront fee を含む価格サマリーを表示し、`y` の入力を求める確認プロンプトを挟む。ここでの購入は取り消しできないため、必ず予算の承認を得てから `y` を入力すること。購入が成功すると Capacity Reservation ID（`cr-...`）と `EndDate` が出力される。

### 3. terraform.tfvars に反映するブロックを生成する

```bash
./02-post-purchase.sh \
  --cr-id cr-0123456789abcdef0 \
  --end-date 2026-07-21T12:00:00Z \
  --instance-type p5en.48xlarge \
  --zone us-east-2a \
  --pool gpu-p5en
```

このスクリプトは AWS に対して何も呼ばない。前のステップで得た `cr-...` と終了時刻を、`accelerator_pools` に貼り付けられる HCL ブロックとして標準出力に整形するだけである。

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

出力されたブロックを `terraform.tfvars` の `accelerator_pools` に貼り付ける。

### 4. apply してノードを確認する

```bash
cd infra/eks
terraform apply
kubectl get nodes -l karpenter.sh/capacity-type=reserved
```

実機出力（p5en x2 の CB 環境）:
```
NAME                            STATUS   ROLES    AGE   VERSION
ip-10-0-xx-xx.us-east-2...     Ready    <none>   21h   v1.35.6-eks-...
ip-10-0-yy-yy.us-east-2...     Ready    <none>   18h   v1.35.6-eks-...
```

NodeClaim でも確認:
```bash
kubectl get nodeclaims -l karpenter.sh/nodepool=gpu-p5en
```

```
NAME             TYPE            CAPACITY    ZONE         READY   AGE
gpu-p5en-aaaaa   p5en.48xlarge   reserved    us-east-2a   True    18h
gpu-p5en-bbbbb   p5en.48xlarge   reserved    us-east-2a   True    21h
```

`CAPACITY = reserved` が Capacity Block 由来であることを示し、`ZONE` が単一 AZ に固定されていることも確認できる。

apply の内部では、`capacity-block.tf` が `cb_reservation_id` から実際の予約情報を自動解決する。`data "external"` で `aws ec2 describe-capacity-reservations` を呼び、以下の3値を予約から取得する:

- **end_date**: EventBridge アラームの発火時刻に使う。`cb_end_date` を tfvars に明記していない場合、予約の `EndDate` から自動導出される（明記すれば上書きも可能）
- **availability_zone**: プールの `zone` と一致しているか検証
- **state**: `active` でなければ警告（`scheduled` や `expired` ではノードが起動しない）

この自動導出により、`02-post-purchase.sh` で得た `cr-...` さえ書けば、終了日時や AZ を手コピーする必要がない。

Terraform の `check` ブロックは state/zone の不整合を **WARNING として** 表示するが、**apply を止めはしない**。これは意図的な設計で、もし state が `expired` に変わったタイミングで `check` が NodePool の `for_each` をゲートしてしまうと、NodePool リソース自体が破壊される（= 稼働中の Pod が突然消える）ことになるため。`check` はあくまで「確認してから proceed せよ」という視覚的なシグナルである。

### 5. マルチノードなら NCCL/EFA を検証する

```bash
./03-verify-nccl.sh --nodes 2 --gpus-per-node 8 \
  --namespace <ns> --image <nccl-tests-image>
```

MPIJob を使って 2 ノード間の `all_reduce_perf` を実行し、busbw（EFA 経由の実効帯域）を確認する。単体ノードの NVLink 内帯域だけでなく、CB で確保した複数ノードが実際に EFA/RDMA で正しく通信できているかをここで確かめる。

### 6. 期限アラートを確認する

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

`cb_end_date`（または予約から自動導出された終了時刻）の 1 時間前に `at()` 式の EventBridge Scheduler ルールが 1 つ作られ、共有 SNS トピックにメール通知が届く。通知が来たらワークロードの graceful drain を開始する。

### 7. teardown

```bash
./04-teardown.sh --namespace <ns> --nodepool gpu-p5en
```

Deployment/StatefulSet/Job/MPIJob を削除し、GPU Pod が完全に終了したのを確認したうえで Karpenter の NodePool を削除する。CB のノード自体は予約期間の終了時に AWS 側で強制回収されるため、このスクリプトは「ワークロードを安全に退避させる」ところまでを担当する。クラスタ全体を壊す `terraform destroy` は `--destroy` を明示した場合のみ実行される。

## 注意点

**CB ノードが起動しない「filtered out all instance types」。** apply 後に Karpenter がノードを起動できない場合、直前に同じ予約を使っていた別インスタンスがまだ終了処理中で、予約スロットが実際には空いていないことが多い。少し待って Karpenter のリトライに任せれば解決するケースがほとんどである。

**`capacity-reservation` taint も別に存在する。** CB ノードには、プールの `device_plugin` に応じた `nvidia.com/gpu` または `aws.amazon.com/neuron` の taint に加えて、予約ごとに値が変わる `capacity-reservation` taint も付く。値が予約ごとに変わるため、ワークロード側は `operator: Exists` で値を問わずに tolerate する必要がある。`operator: Equal` で固定値を書いてしまうと、次に別の CB を買い直した瞬間にジョブがスケジュールされなくなる。

**`cb_end_date` は UTC 必須（末尾 `Z`）。** EventBridge Scheduler は `schedule_expression_timezone = "UTC"` で動いているため、`cb_end_date` にタイムゾーンオフセット付き（例: `+09:00`）の値を書くと、そのオフセットは無視されて UTC として再解釈される。結果としてアラートが実際の期限から最大で日付をまたぐほどずれる。この構成では変数の `validation` で `cb_end_date` が空でない限り末尾が `Z` であることを強制し、plan 時点で弾いている。

**EventBridge スケジュールは発火後に自己削除される。** `action_after_completion = "DELETE"` を設定しているため、アラートが一度発火するとその `aws_scheduler_schedule` リソースは AWS 側から消える。Terraform 側もこれに追従できるよう、アラート時刻（`cb_end_date - 1h`）が過去になったプールは `for_each` の対象から自動的に除外される仕組みになっている。これがないと、発火済みで消えたスケジュールを次の plan が「過去の時刻で作り直そう」として EventBridge API に reject され続けることになる。

**`cb_reservation_id` を設定しても `capacity_type` を `"reserved"` にし忘れると予約が無視される。** `cb_reservation_id` だけ書いて `capacity_type` を `"on-demand"` のままにすると、EC2NodeClass は `capacityReservationSelectorTerms` を持たずに生成され、予約はまるごと無視されて On-Demand で起動する。前払いした予約分の費用に加えて On-Demand の費用も別にかかる、という二重コストに直結するため、この構成では変数の `validation` でこの組み合わせを明示的に弾いている。
