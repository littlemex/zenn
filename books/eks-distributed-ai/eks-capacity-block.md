---
title: "Basic05 - Capacity Block を利用する"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、Basic04 で導入した `accelerator_pools` の仕組みに、ここで初めて `capacity_type = "reserved"` のプールを足し、Capacity Block(CB) で確保したリソースを Amazon EKS クラスタに組み込みます。予約の検索・購入から `accelerator-pools.auto.tfvars` への反映、NodePool と EC2NodeClass が正しく作られるところまでの確認、期限管理までを扱います (実際にノードが起動するのは次章 Basic06 です)。確保したノードで実際にマルチノード通信が出ているかの検証は、次章の Basic06 で行います。

本章がこの位置にあるのは、次章で EFA のマルチノード通信を検証するために、EFA を複数枚持つインスタンスが 2 台以上必要になるためです。この規模のインスタンスは On-Demand ではなかなか確保できず、しかも EFA の OS バイパス通信は同一サブネットに限られるので、2 台を同じ AZ に揃える必要があります (クラスタプレイスメントグループはレイテンシを詰めるための推奨で、EFA の必須条件ではありません)。Capacity Block はこれらを満たす現実的な手段です。

:::message alert
Capacity Block は開始時刻と終了時刻が固定で、期間が過ぎれば容量は強制的に回収されます。リソース利用が開始されたら、間を置かずに Basic06 の検証まで進めてください。本章と次章を別の日に分けると、確保した時間を待機に費やすことになります。
:::

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち **Capacity Block による予約** です。アクセラレータプールの構造自体は Basic04 までに作った型をそのまま使います。

## これは何をするものか

Capacity Block（CB）は、H100/H200/AWS Trainium のような一部の対応アクセラレータを、固定の開始時刻・終了時刻・インスタンス数で前払い予約する購入オプションです。Spot インスタンスと違い、予約期間中に中断されることはありません。しかし期間が終わればどれだけジョブが残っていても強制的に容量が回収される、という一方向の期限が必ず来ます。

CB を使う最低限の運用フローは次のようになります。

1. `describe-capacity-block-offerings` でオファリング（購入可能な予約の候補）を検索する
2. オファリングを選んで購入し、Capacity Reservation ID（`cr-...`）を受け取る
3. `cr-...` を `accelerator_pools` の該当プールに書き込み `terraform apply` する
4. 確保したノードでワークロードを動かす

この章に付属する CB 関連の 補助スクリプト は [`00-check-cb-offerings.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/scripts/00-check-cb-offerings.sh)、[`01-purchase-cb.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/scripts/01-purchase-cb.sh)、[`02-post-purchase.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/scripts/02-post-purchase.sh)、[`04-teardown.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/scripts/04-teardown.sh) の 4 つです（`03-` は欠番で、そういうファイルはありません）。

この構成には予約の終了時刻から自動的に期限アラートを組み立てる仕組みが入っています。プールに `cb_reservation_id` を書いておくと、Terraform がその予約の終了時刻を自動的に読み取り、Amazon EventBridge Scheduler の one-shot スケジュールを 1 プールにつき 1 つ作り、終了 1 時間前に Amazon SNS へ通知します。発火後はそのスケジュール自体が AWS 側から消えます。ここで素朴に作ると、次の `terraform apply` が消えたスケジュールを過去の時刻で作り直そうとして API に拒否され、以降 apply が通らなくなります。これを避けているのは自己削除ではなく Terraform 側の時刻フィルタで、通知時刻 (終了 1 時間前) がすでに過ぎたプールをスケジュールの対象から外しています。同じ仕組みを自分で組む場合は、この 2 つを対で用意しないと apply が毎回失敗します。例外は、通知時刻の直前に `plan` を作って直後に `apply` する場合です。このときは `plan` の時点では未来だった時刻が `apply` の時点で過去になっているため、その 1 回の apply が失敗します。`plan` を作り直せば解消します。この時刻フィルタには読者に見える帰結が 1 つあります。終了 1 時間前を過ぎたプールは、スケジュールの作成対象から外れます。SNS トピックはクラスタで 1 つを共有するので、通知時刻がまだ先の予約プールが 1 つでも残っていれば作られます。予約プールがこの 1 つだけ、あるいは全予約プールの通知時刻がすでに過ぎている場合に、後述の手順 5 で `cb_expiry_alert_schedule_exprs` が空の map、`cb_expiry_sns_topic_arn` が空文字になります。これは設定の失敗ではありません。

プールの `cb_end_date` は、この自動導出された終了時刻を緊急時に上書きするための任意項目であり、通常は書く必要がありません。

## 予約 ID（cr-...）の安全な取り扱い

CB は前払いで、購入した時点でその予約期間分の費用が確定します。途中で不要になっても取り消しや返金はできません。`infra/eks/scripts` の中にある [`00-check-cb-offerings.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/scripts/00-check-cb-offerings.sh) で CB 予約のオファリングを検索できます。

`01-purchase-cb.sh` で CB を購入すると、標準出力に `cr-...` という Capacity Reservation ID が表示されます。これを `accelerator-pools.auto.tfvars`(Basic04 で作ったプール定義ファイル)の `accelerator_pools` 内、該当プールの `cb_reservation_id` に貼り付けるだけで、Terraform 側の設定は完了します。

```hcl
# accelerator-pools.auto.tfvars（例、cr-... はプレースホルダ）
accelerator_pools = {
  gpu-p4d = {
    instance_types    = ["p4d.24xlarge"]
    device_plugin     = "nvidia"
    capacity_type     = "reserved"
    cb_reservation_id = "cr-0123456789abcdef0" # zone はこの予約から導出される
  }
}
```

ここで最も障害につながりやすいのは、予約を指定したのに `capacity_type` を `"reserved"` にし忘れる、あるいはその逆というミスです。[`variables.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/variables.tf) の `accelerator_pools` には、この 2 方向のミスをそれぞれ弾く `validation` ブロックが用意されています。

`cr-...` を正しく渡せば、あとは手で入力する項目はほとんど残りません。後述の [`capacity-block.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/capacity-block.tf) の `data "external" "capacity_reservations"` が `cb_reservation_id` だけから予約の `end_date`・`availability_zone`・`state` を自動的に読み取り、期限アラートや AZ 整合性チェックに使います。したがって `accelerator-pools.auto.tfvars` に手で書く CB 関連の値は、原則 `cb_reservation_id` と `capacity_type = "reserved"` の 2 つだけで済みます。

## check ブロックはなぜ apply を止めないか

`capacity-block.tf` の末尾には `check` ブロックがあります。

```hcl
# capacity-block.tf（抜粋）
check "capacity_block_ready" {
  assert {
    condition = alltrue([
      for k, s in local.pool_cb_state : s == "active"
    ])
    error_message = "A Capacity Block for a reserved accelerator pool is not active: ..."
  }
  assert {
    condition = alltrue([
      for k, p in local.cb_reserved_pools :
      p.zone == "" || local.pool_cb_zone[k] == "" || local.pool_cb_zone[k] == p.zone
    ])
    error_message = "A reserved pool sets an explicit zone that does not match its Capacity Block's AZ. ... Clear the pool's zone (leave it \"\") to inherit the CB's AZ automatically, or fix it to match."
  }
}
```

1 つ目の assert は CB がまだ `scheduled`（開始前）や `expired`（終了後）のまま apply されようとしていないかを見ます。2 つ目は、プールに**明示指定した** `zone` と、予約が実際に確保している AZ が食い違っていないかを見ます。条件式の先頭が `p.zone == ""` で短絡している点が肝で、`zone` を書かない既定のプールでは `local.pool_zone` が予約の AZ をそのまま読み取るため食い違いようがなく、この assert は素通りします。警告が意味を持つのは、導出される AZ をあえて明示指定で上書きしようとして、その値が予約の AZ と矛盾した場合だけです。

ここで重要なのは、**`check` ブロックは条件を満たさなくても plan/apply を WARNING で通す**という [Terraform の仕様](https://developer.hashicorp.com/terraform/language/checks)です。「アサーションだから当然 apply を止める」と考えると誤りで、この構成でもあえて「止めない」設計を選んでいます。理由はコメントにある通りで、もし CB の `state` を NodePool の `for_each` の条件に使ってハードゲート化すると、CB が後から `expired` に変わった瞬間に `for_each` の対象から外れて **NodePool ごと DESTROY される**という、警告よりもずっと悪い結果を招きます。したがって `check` ブロックはあくまで「気づくための仕掛け」であり、apply を止める防波堤ではありません。

一方、同じモジュールの [`variables.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/variables.tf) にある `validation` ブロック（`cb_end_date` の UTC 必須や、`capacity_type` と `cb_reservation_id` の組み合わせチェックなど）は、こちらは条件を満たさないと **plan 自体を失敗させる**、正真正銘のフェイルファストです。同じ「CB がらみのチェック」でも、構造的に検証できるもの（tfvars の書き方の誤り）は `validation` でハードに止め、外部の実行時状態（CB の `state` や実際の AZ）に依存するものは `check` でソフトに警告する、という役割分担になっています。

## 期限アラート（eventbridge-cb-alarm.tf）

[`eventbridge-cb-alarm.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/eventbridge-cb-alarm.tf) は、`capacity-block.tf` が導出した `local.pool_cb_end_date` から、プールごとに 1 つの one-shot な Amazon EventBridge Scheduler スケジュールを組み立てます。

# ワークショップ実施

本章の実機検証は p4d.24xlarge（NVIDIA A100 40GB x8、EFA x4）2 台の Capacity Block で実施しました。以降のコマンド出力はすべてこの構成の実測値です。読者が別のインスタンスタイプで進める場合、台数や EFA の枚数は当然変わります。本章のスクリプトはそうした値を固定値で指定せず AWS API から取得する作りにしてあるので、コマンドはそのまま使えます。

## 1. オファリングを検索する（読み取りのみ、課金なし）

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/scripts
./00-check-cb-offerings.sh \
  --region "$AWS_REGION" \
  --instance-types p4d.24xlarge \
  --instance-count 2 \
  --duration-hours 24
```

`describe-capacity-block-offerings` を叩き、インスタンスタイプ・AZ・開始/終了時刻・upfront fee の一覧を表示するだけの read-only スクリプトです。この時点ではまだ何も購入していないので、何度実行してもコストは発生しません。

検索は必ずクラスタと同じリージョン (`$AWS_REGION`) で行います。オファリング ID はリージョンごとに固有なので、別のリージョンで検索した ID は購入時に見つかりません。以降の実機出力は `us-west-2` で採取したものなので、AZ 名やインスタンス ID は自分のリージョンの値に読み替えてください。

実機出力（p4d.24xlarge x2、24 時間）:

```
--- p4d.24xlarge ---
  OfferingId             AZ               Cnt   Hrs   UpfrontUSD  USD/inst-hour
  cb-0785c7267b6908e72   us-west-2d         2    24       566.40          11.80
    start 2026-08-02T11:30:00+00:00  end 2026-08-03T11:30:00+00:00
```

`UpfrontUSD` は「そのブロック全体（台数 x 期間すべて）」の前払い総額です。1 台 1 時間あたりに割り戻した `USD/inst-hour` も併記されるので、同じインスタンスタイプの On-Demand 単価と直接比較できます。この例では 2 台 24 時間で 566.40 USD、1 台 1 時間あたり 11.80 USD でした。

`--instance-types` を省略すると、`describe-instance-types` に `supported-usage-class=capacity-block` を問い合わせてそのリージョンで CB が買えるインスタンスタイプを列挙し、それぞれの価格を順に表示します。新しい世代が出てもスクリプトを書き換える必要はありません。

:::message
そのリージョン・期間・台数の組み合わせで在庫が無い場合は `(no offerings available — sold out for this window/size)` と表示されます。アカウントの CB 上限に達している場合は AWS API のエラー文がそのまま表示されるため、「売り切れ」と「上限不足」を区別できます。台数を減らす、期間を変える、別の AZ やリージョンを試す、といった調整で候補が出ることがあります。
:::

## 2. CB を購入する（ここで課金が発生する）

`--offering-id` には手順 1 の検索結果に出た自分の OfferingId を渡します。下の値は例です。オファリングは在庫に連動するので、検索から時間が経つと無効になります。その場合は手順 1 の検索からやり直します。

```bash
./01-purchase-cb.sh \
  --offering-id cb-0785c7267b6908e72 \
  --instance-type p4d.24xlarge \
  --instance-count 2
```

このスクリプトは実際に `purchase-capacity-block` を呼びます。実行前に upfront fee を含む価格サマリーを表示し、`y` の入力を求める確認プロンプトを挟みます。ここでの購入は取り消しできないため、必ず予算の承認を得てから `y` を入力してください。購入が成功すると Capacity Reservation ID（`cr-...`）と `EndDate` が出力されます。

:::message alert
CB の購入は前払いで、キャンセルや返金はできません。`00-check-cb-offerings.sh` で候補を確認し、必要台数・期間を十分に見積もってから購入してください。
:::

## 3. accelerator-pools.auto.tfvars に反映するブロックを生成する

購入した CB の CR ID を確認するコマンドです。

```bash
aws ec2 describe-capacity-reservations \
  --region "$AWS_REGION" \
  --query 'CapacityReservations[].{ID:CapacityReservationId,Type:InstanceType,AZ:AvailabilityZone,Total:TotalInstanceCount,Avail:AvailableInstanceCount,Ends:EndDate,State:State}' \
  --output table
```

CR ID を指定して tfvars に貼り付ける設定を出力します。

```bash
export POOL=gpu-p4d
export CR_ID=cr-023f18e20d3829f4e
./02-post-purchase.sh \
  --cr-id "$CR_ID" \
  --pool "$POOL"
```

`CR_ID` は 1 つ前のコマンドで表示された自分の予約 ID に読み替えます。必須の引数は `--cr-id` だけです。インスタンスタイプ・AZ・終了時刻は、スクリプトが `describe-capacity-reservations` で予約 ID から自動で解決するため、手で渡す必要はありません（`--pool` は貼り付け先のプール名で、省略すると `gpu-cb` になります）。

```hcl
gpu-p4d = {
  instance_types    = ["p4d.24xlarge"]
  device_plugin     = "nvidia"
  capacity_type     = "reserved"
  cb_reservation_id = "cr-023f18e20d3829f4e"          # zone と終了時刻はこの予約から導出
  cb_end_date       = "2026-08-03T11:30:00Z"        # 省略可、予約から自動導出される値の緊急上書き用
  volume_size       = "500Gi"
}
```

`device_plugin` はインスタンスタイプのファミリから決まります。`trn` または `inf` で始まれば `neuron`、それ以外は `nvidia` が入るので、Trainium/Inferentia の CB を買った場合も同じスクリプトがそのまま使えます（Neuron の場合は Neuron 用 AMI を指す `ami_ssm_parameter` の行も併せて出力されます）。

`zone` は含まれません。前述のとおり `reserved` プールの AZ は予約から導出されるため、スクリプトは `zone` 行を出しません。特定の AZ に固定したい場合だけ、貼り付け後に自分で `zone = "<az>"` を足します。`cb_end_date` は末尾が `Z` の UTC 表記でなければ `variables.tf` の validation が plan を落とします (`+00:00` のようなオフセット表記は EventBridge のスケジュールで UTC と誤解されるため)。スクリプトは予約が返す時刻をこの形式に正規化して出力するので、貼り付けたままなら問題ありません。この値は予約が返す実際の終了時刻を自動で埋めていますが、`capacity-block.tf` は tfvars に `cb_end_date` が無くても予約 ID から終了時刻を導出するため、この行を消してもアラートは機能します。書いておくとその値が予約側の `EndDate` より優先される緊急上書きとして働くので、予約を更新したら `cb_end_date` も併せて更新してください（更新し忘れるとアラートが古い時刻のまま固定されます）。Basic04 では `capacity_types = ["spot", "on-demand"]` とリストで書きましたが、CB のプールは `capacity_type = "reserved"` と単数形で書きます。綴りの誤りではなく、実装が両方の書き方を受け取って内部で同じ形に正規化しているためです。予約を使うプールは単数形と `cb_reservation_id` の組で書く、と覚えておけば足ります。

出力されたブロックを `accelerator-pools.auto.tfvars` の `accelerator_pools` に貼り付けます。同じファイルを `cat >` で完全形に上書きするのが冪等で確実ですが、ここでいう完全形とは **Basic04 で定義した `gpu-ddp` も含めた全プール** です。CB のプールだけを書いて上書きすると、次の `apply` で `gpu-ddp` の NodePool が destroy され、それを前提にしている Basic07 と Basic08 が進められなくなります。Basic04 の例のまま進めている場合、完全形は次のようになります。予約 ID は上で設定した `CR_ID` から入るので、同じシェルで実行してください。

```bash
cat > accelerator-pools.auto.tfvars <<EOF
accelerator_pools = {
  gpu-ddp = {
    instance_types      = ["g6.2xlarge", "g5.2xlarge", "g6.xlarge", "g5.xlarge"]
    device_plugin       = "nvidia"
    capacity_types      = ["spot", "on-demand"]
    efa_interface_count = 0
    labels              = { workload = "ddp-basic04" }
  }
  gpu-p4d = {
    instance_types    = ["p4d.24xlarge"]
    device_plugin     = "nvidia"
    capacity_type     = "reserved"
    cb_reservation_id = "${CR_ID}"
    volume_size       = "500Gi"
  }
}
EOF
```

## 4. apply して NodePool を確認する

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
terraform apply
k get nodepool $POOL
k get ec2nodeclass $POOL
```

シェルを開き直した場合は、Basic01 手順 2 の 4 行と `export POOL=<プール名>` を先に実行し直します。CB の開始時刻より前に apply すると、予約が `scheduled` のままなので `capacity_block_ready` の WARNING が出ます。定義の作成自体は成功しているので、これは異常ではありません。ワークロードの投入 (Basic06 の手順 3) は予約の開始時刻を過ぎてから行います。

apply が作るのは NodePool と EC2NodeClass の定義であって、この時点ではまだノードは起動しません。Karpenter は GPU を要求する Pod（Pending）が現れて初めてノードを起動します（Karpenter 自体のインストールと NodePool 生成の仕組みは Basic04 で構築済みという前提です）。実際にノードが起動するのは、次章 Basic06 で CB プールをターゲットにした検証ワークロードを投入したときなので、ここで確認するのは定義が正しく作られたことまでです。

ノードが立ったあと、それが予約から起動したことを確かめるコマンドを先に示しておきます。実行するのは Basic06 の手順 3 でワークロードを投入したあとです。

```bash
k get nodeclaims -l karpenter.sh/nodepool=$POOL
k get nodes -l karpenter.sh/capacity-type=reserved
```

実機出力（p4d の Capacity Block は us-west-2d に確保したもの。2 台のうち 1 台分を抜粋）:

```text
NAME            TYPE           CAPACITY   ZONE         NODE                                         READY
gpu-p4d-5zlm9   p4d.24xlarge   reserved   us-west-2d   ip-10-0-115-100.us-west-2.compute.internal   True
```

`CAPACITY = reserved` の NodeClaim が表示され、`ZONE` が予約の AZ に一致していれば、CB からのノード起動は成功です。ここで `zone` を `accelerator-pools.auto.tfvars` に一切書いていないことを思い出してください。`us-west-2d` は予約から自動導出された値です。

## 5. 期限アラートを確認する

```bash
terraform output cb_expiry_alert_schedule_exprs
terraform output cb_expiry_sns_topic_arn
```

実機出力:
```text
cb_expiry_alert_schedule_exprs = {
  "gpu-p4d" = "at(2026-08-03T10:30:00)"
}
cb_expiry_sns_topic_arn = "arn:aws:sns:<region>:<account>:<cluster_name>-cb-expiry-alert"
```

予約から自動導出された終了時刻（または `cb_end_date` で明示的に上書きした終了時刻）の 1 時間前に `at()` 式の Amazon EventBridge Scheduler スケジュールが 1 つ作られ、共有 Amazon SNS トピックにメール通知が届きます。通知が来たら、ワークロードを安全に退避させる作業を始めます。

メールを受け取るには、通知先アドレスを `terraform.tfvars` の [`cb_alert_email_addresses`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/variables.tf) に設定して apply しておきます。設定すると Terraform が SNS トピックへの email サブスクリプションを作成しますが、AWS から確認メールが届くので、その中のリンクを一度クリックして承認するまで通知は届きません（SNS の仕様です）。

```hcl
# terraform.tfvars
cb_alert_email_addresses = ["you@example.com"]
```

# まとめ

本章では、Basic04 で用意した `capacity_type = "reserved"` を使い、Capacity Block の検索・購入から `accelerator-pools.auto.tfvars` への反映、NodePool の確認、期限アラートまでを構築しました。

手で書いたのは予約 ID と `capacity_type = "reserved"` の 2 つだけです。AZ は予約から自動導出され、期限アラートも予約の終了時刻から組み立てられます。ここまでで確保できたのは容量であって、その容量が期待どおりの帯域を出すかはまだ分かっていません。確保したノードで EFA が正しく構成され、ノード間で実際に帯域が出ているかの検証と、終わったあとの後片付け は次章の Basic06 で扱います。

CB は前払いで取り消しができないため、On-Demand で動作確認を済ませたジョブを最後に載せる、という段階的な使い方が安全です。価格は `00-check-cb-offerings.sh` が「ブロック全体の総額」と「1 台 1 時間あたり」の両方で示すので、購入前に On-Demand 単価と比較できます。`capacity_type` の指定漏れによる二重課金など、Terraform の `validation` で弾いている落とし穴も合わせて理解しておけば、期限管理まで含めた CB 運用を問題なく運用できます。

# 参考資料

- [Amazon EC2 Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html)
- [Amazon EventBridge Scheduler](https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
