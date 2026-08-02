---
title: "Basic06 - Capacity Block を取得して組み込む"
free: true
---
本章では、Basic04 で `accelerator_pools` に用意しておいた `capacity_type = "reserved"` という選択肢を実際に使い、H200/AWS Trainium の予約キャパシティ（Capacity Block）を Amazon EKS クラスタに組み込みます。予約の検索・購入から `terraform.tfvars` への反映、NCCL/EFA での動作確認、期限管理までの一連の運用フローを扱います。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち **Capacity Block による予約 GPU/AWS Trainium ノードの調達** と、その期限を監視する **Amazon EventBridge → Amazon SNS のアラート経路** です。アクセラレータプールの構造自体は Basic04 までに作った型をそのまま使います。

## これは何をするものか

Capacity Block（CB）とは、H100/H200/AWS Trainium のような希少なアクセラレータを、固定の開始時刻・終了時刻・インスタンス数で前払い予約する Amazon EC2 の仕組みです。Spot インスタンスと違い、予約期間中に中断されることはありません。しかし Spot とは逆の意味で扱いが難しい面があります。期間が終わればどれだけジョブが残っていても強制的に容量が回収される、という一方向の期限が必ず来るためです。

なぜ CB が必要になるのでしょうか。p5en.48xlarge や trn2.48xlarge のような大型インスタンスは、On-Demand で `RunInstances` を呼んでも `InsufficientInstanceCapacity` で弾かれることが多くあります。ODCR（On-Demand Capacity Reservation）や Amazon SageMaker の予約系チャネルなど他の入手経路もありますが、本書のようなセルフマネージドな Amazon EKS 構成では、AWS 側が容量をあらかじめ区切って予約販売する CB が現実的な選択肢になります。

CB を使う運用フローは次のようになります。

1. `describe-capacity-block-offerings` でオファリング（購入可能な予約の候補）を検索する
2. オファリングを選んで購入し、Capacity Reservation ID（`cr-...`）を受け取る
3. `cr-...` を `accelerator_pools` の該当プールに書き込み `terraform apply` する
4. NCCL/EFA でノード間通信を検証する
5. 終了時刻の 1 時間前に自動でアラートが飛ぶようにしておく
6. 終了前にワークロードを退避し、teardown する

この章に付属する helper script は `00-check-cb-offerings.sh` から `04-teardown.sh` までの 5 つで、上記の手順 1・2・3・4・6（検索・購入・反映・検証・teardown）にそれぞれ 1 対 1 で対応しています。番号の順に実行すれば迷わない構成にしています。手順 5 の期限アラートだけはスクリプト実行を挟まず、`cb_reservation_id` を書くだけで Terraform が予約の終了時刻を自動的に読み取り、アラートを組み立てます。

さらに、この構成には予約の終了時刻から自動的に期限アラートを組み立てる仕組みが入っています。プールに `cb_reservation_id` を書いておくと、Terraform がその予約の終了時刻を自動的に読み取り、Amazon EventBridge Scheduler の one-shot スケジュールを 1 プールにつき 1 つ作り、終了 1 時間前に Amazon SNS へ通知します。発火後はそのスケジュール自体が自己削除されるため、`terraform apply` を重ねても過去の時刻でスケジュールを再作成しようとして API に拒否される、という事故を避けられます。プールの `cb_end_date` は、この自動導出された終了時刻を緊急時に上書きするための任意項目であり、通常は書く必要がありません。

最後にコストの話をしておきます。CB は前払いで、購入後のキャンセルや返金はできません。p5en.48xlarge を 24 時間予約するだけで、千ドルから数千ドルのオーダーの upfront fee がかかります。したがって、CB を買う前に g6e のような On-Demand で十分に手が届く GPU インスタンスでコードとマニフェストの動作を検証しておき、CB は「動作確認済みのジョブを、確保した本番規模のアクセラレータに載せる」という最後のステップに使うのが安全な段階的アプローチになります。

対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) です。以降で実際の Terraform コードを引用しながら、予約メタデータの自動導出・期限アラートの組み立て方を見ていきます。

## 予約 ID（cr-...）の安全な取り扱い

CB は前払いで、購入した時点でその予約期間分の費用が確定します。途中で不要になっても取り消しや返金はできません。この前提があるからこそ、購入によって得られる Capacity Reservation ID（`cr-...`）を Terraform にどう受け渡すかという、一見小さな配線の部分にも慎重さが要求されます。ここでは `cr-...` の受け渡し方と、誤って設定した場合に何が起こるかを見ていきます。

`01-purchase-cb.sh` で CB を購入すると、標準出力に `cr-...` という Capacity Reservation ID が表示されます。これを `terraform.tfvars` の `accelerator_pools` 内、該当プールの `cb_reservation_id` に貼り付けるだけで、Terraform 側の配線は完了します。

```hcl
# terraform.tfvars（例、cr-... はプレースホルダ）
accelerator_pools = {
  gpu-p5en = {
    instance_types    = ["p5en.48xlarge"]
    device_plugin     = "nvidia"
    capacity_type     = "reserved"
    cb_reservation_id = "cr-0123456789abcdef0" # zone はこの予約から導出される
  }
}
```

`zone` を書いていない点に注目してください。`reserved` プールの AZ は Capacity Block の予約から自動導出される（`az.tf` が予約の AZ を読み取る）ため、通常は手書きしません。これにより、後日 CB が別の AZ に移っても、差し替えるのは新しい `cb_reservation_id` の 1 行だけで済みます。特定の AZ に固定したい特殊なケースだけ `zone` を明示指定でき、その値が予約の AZ と食い違うと後述の `check` ブロックが警告します。

ここで最も事故につながりやすいのは、予約を指定したのに `capacity_type` を `"reserved"` にし忘れる、あるいはその逆というミスです。[`variables.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/variables.tf) の `accelerator_pools` には、この 2 方向のミスをそれぞれ弾く `validation` ブロックが用意されています。

```hcl
# variables.tf（抜粋、accelerator_pools の validation）
validation {
  condition = alltrue([for k, p in var.accelerator_pools :
    !(p.capacity_type == "reserved" || (p.capacity_types != null ? contains(p.capacity_types, "reserved") : false)) ||
    (p.cb_reservation_id != null && p.cb_reservation_id != "") ||
    length(try(p.capacity_reservations.ids, [])) > 0 ||
    length(try(p.capacity_reservations.tags, {})) > 0
  ])
  error_message = "A pool that includes \"reserved\" must name a reservation: set capacity_reservations = { ids = [...] } (or tags), or the legacy cb_reservation_id (cr-...)."
}
```

```hcl
# variables.tf（抜粋、accelerator_pools の validation）
validation {
  condition = alltrue([for k, p in var.accelerator_pools :
    (p.capacity_type == "reserved" || (p.capacity_types != null ? contains(p.capacity_types, "reserved") : false)) ||
    ((p.cb_reservation_id == null || p.cb_reservation_id == "") && p.capacity_reservations == null)
  ])
  error_message = "A pool sets a reservation (capacity_reservations or cb_reservation_id) but does not include \"reserved\" in its capacity type(s); the reservation would be ignored. Add \"reserved\", or clear the reservation."
}
```

1 つ目は、`capacity_type`（または複数形の `capacity_types`）に `"reserved"` を含むプールが、`cb_reservation_id` にも `capacity_reservations` にも予約を指定していないケースを弾きます。Karpenter は開いている予約を自動探索しないため、これを書き忘れると CB ノードは永久に起動できません。

2 つ目はその逆で、予約を指定したのに `capacity_type`（または `capacity_types`）に `"reserved"` を含めていないケースを弾きます。この組み合わせが招く二重課金の実害は、後述の「注意 4」で詳しく扱います。この 2 つの `validation` は、予約の有無と `capacity_type` の組み合わせという構造的に検証できるミスを対象にしており、崩れた組み合わせを見つけた瞬間に plan 自体を失敗させて事故を未然に防ぎます。

`cr-...` は AWS アカウントと予約に固有の値です。本書では実際の値を書かず、上記のように `cr-0123456789abcdef0` のようなプレースホルダで統一しています。実際の運用でも `cr-...` はリポジトリにコミットせず、`terraform.tfvars`（`.gitignore` の対象とする）のような環境固有の設定ファイルにのみ書くことを徹底してください。

`cr-...` を正しく渡せば、あとは手で入力する項目はほとんど残りません。後述の [`capacity-block.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/capacity-block.tf) の `data "external" "capacity_reservations"` が `cb_reservation_id` だけから予約の `end_date`・`availability_zone`・`state` を自動的に読み取り、期限アラートや AZ 整合性チェックに使います。したがって `terraform.tfvars` に手で書く CB 関連の値は、原則 `cb_reservation_id` と `capacity_type = "reserved"` の 2 つだけで済みます。

なお、`variables.tf` では `capacity_type`・`cb_reservation_id`・`zone` は LEGACY（単数形）フィールドと明記されており、`capacity_types`・`capacity_reservations`・`zones`（複数形）という新形式も用意されています。本章は単純さを優先して LEGACY 形式で統一しますが、新形式で `reserved` を指定する場合は AZ の自動導出が効かず、`zones` を明示指定しないと validation で弾かれる点だけ覚えておいてください。

## 予約メタデータの自動導出（capacity-block.tf）

[`capacity-block.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/capacity-block.tf) は、`accelerator_pools` に書いた `cb_reservation_id`（`cr-...`）だけから、その予約の終了時刻・AZ・状態を自動的に読み取ります。

```hcl
# capacity-block.tf（抜粋）
data "external" "capacity_reservations" {
  count   = length(local.cb_reserved_pools) > 0 ? 1 : 0
  program = ["bash", "${path.module}/scripts/describe_capacity_reservations.sh"]
  query = {
    ids     = local.cb_reservation_ids_csv
    region  = var.region
    profile = var.aws_profile != null ? var.aws_profile : ""
  }
}

locals {
  cb_meta_flat = length(local.cb_reserved_pools) > 0 ? data.external.capacity_reservations[0].result : {}

  # end_date: 明示した tfvars の cb_end_date が優先（緊急時の上書き用）。
  # それが空なら予約の EndDate を使う。
  pool_cb_end_date = {
    for k, p in local.cb_reserved_pools : k => (
      p.cb_end_date != "" ? p.cb_end_date : lookup(local.cb_meta_flat, "${p.cb_reservation_id}.end_date", "")
    )
  }
  pool_cb_zone  = { for k, p in local.cb_reserved_pools : k => lookup(local.cb_meta_flat, "${p.cb_reservation_id}.availability_zone", "") }
  pool_cb_state = { for k, p in local.cb_reserved_pools : k => lookup(local.cb_meta_flat, "${p.cb_reservation_id}.state", "") }
}
```

読みどころは次の 3 点です。

**AWS provider に `aws_ec2_capacity_reservation` data source が存在しません。** 購入側の `aws_ec2_capacity_block_offering` はありますが、既存の予約を読み取る data source は用意されていません。そのため `data "external"` で [`describe_capacity_reservations.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/scripts/describe_capacity_reservations.sh) を呼び、`aws ec2 describe-capacity-reservations` の結果を `"<予約ID>.end_date"` のようなフラットな文字列マップに整形して Terraform に戻しています。external data source はネストした値を返せない制約があるための形です。

**`depends_on` を付けていません。** コメントにもある通り、この `data` ブロックの入力は `var` の値だけで決まるため、plan 時点で値が確定します。`depends_on` を付けてしまうと解決が apply まで遅延し、`end_date`/`zone` が plan 時点では unknown になってしまうため、意図的に付けていません。

**見つからない場合と AWS に到達できない場合を区別するスクリプト設計になっています。** `describe_capacity_reservations.sh` は `--capacity-reservation-ids` で個別に ID を指定するのではなく、リージョン内の予約を `aws ec2 describe-capacity-reservations` で一括取得し、リクエストされた ID をクライアント側（jq）でフィルタします。これは、typo や削除済みの ID を 1 つでも渡すと API がバッチ全体を `InvalidCapacityReservationId` で拒否してしまう問題を避けるための設計です。見つからなかった ID は `found=false` に加えて `end_date`・`availability_zone`・`state` が空文字のエントリとして返り、「無害な not-found」として処理されます（結果として state が空文字になり `check` ブロックが WARNING を出すだけで済みます）。一方、認証切れやネットワーク不通など describe 呼び出し自体が失敗した場合は、スクリプトが標準エラーにメッセージを出して即座に終了します。もしここで見つからない予約も一律で空マップを返すような実装だったら、plan が「予約が消えた」と誤認して稼働中の Amazon EventBridge スケジュールや Amazon SNS リソースの DELETE を計算してしまう恐れがあります。

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

1 つ目の assert は CB がまだ `scheduled`（開始前）や `expired`（終了後）のまま apply されようとしていないかを見ます。2 つ目は、プールに**明示指定した** `zone` と、予約が実際に確保している AZ が食い違っていないかを見ます。条件式の先頭が `p.zone == ""` で短絡している点が肝で、`zone` を書かない既定のプールでは `local.pool_zone` が予約の AZ をそのまま読み取るため食い違いようがなく、この assert は素通りします。警告が意味を持つのは、導出される AZ をあえて明示指定で上書きしようとして、その値が予約の AZ と矛盾した場合だけです（このとき Karpenter はゾーン要件と `capacityReservationSelectorTerms` が衝突してノードを起動できません）。

ここで重要なのは、**`check` ブロックは条件を満たさなくても plan/apply を WARNING で通す**という Terraform の仕様です。「アサーションだから当然 apply を止める」と考えると誤りで、この構成でもあえて「止めない」設計を選んでいます。理由はコメントにある通りで、もし CB の `state` を NodePool の `for_each` の条件に使ってハードゲート化すると、CB が後から `expired` に変わった瞬間に `for_each` の対象から外れて **NodePool ごと DESTROY される**という、警告よりもずっと悪い結果を招きます。したがって `check` ブロックはあくまで「気づくための仕掛け」であり、apply を止める防波堤ではありません。

一方、同じモジュールの [`variables.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/variables.tf) にある `validation` ブロック（`cb_end_date` の UTC 必須や、`capacity_type` と `cb_reservation_id` の組み合わせチェックなど）は、こちらは条件を満たさないと **plan 自体を失敗させる**、正真正銘のフェイルファストです。同じ「CB がらみのチェック」でも、構造的に検証できるもの（tfvars の書き方の誤り）は `validation` でハードに止め、外部の実行時状態（CB の `state` や実際の AZ）に依存するものは `check` でソフトに警告する、という役割分担になっています。

## 期限アラート（eventbridge-cb-alarm.tf）

[`eventbridge-cb-alarm.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/eventbridge-cb-alarm.tf) は、`capacity-block.tf` が導出した `local.pool_cb_end_date` から、プールごとに 1 つの one-shot な Amazon EventBridge Scheduler スケジュールを組み立てます。

```hcl
# eventbridge-cb-alarm.tf（抜粋）
locals {
  cb_pools_with_end_date = {
    for k, p in local.cb_reserved_pools : k => p
    if lookup(local.pool_cb_end_date, k, "") != ""
  }

  # cb_end_date - 1h がすでに過去のプールは対象から除外する
  cb_alert_pools = {
    for k, p in local.cb_pools_with_end_date : k => p
    if timecmp(timeadd(local.pool_cb_end_date[k], "-1h"), plantimestamp()) > 0
  }

  has_cb_alert = length(local.cb_alert_pools) > 0

  cb_alert_schedule_expr = {
    for k, p in local.cb_alert_pools :
    k => "at(${formatdate("YYYY-MM-DD'T'hh:mm:ss", timeadd(local.pool_cb_end_date[k], "-1h"))})"
  }
}

resource "aws_scheduler_schedule" "cb_expiry_alert" {
  for_each = local.cb_alert_pools

  schedule_expression          = local.cb_alert_schedule_expr[each.key]
  schedule_expression_timezone = "UTC"
  action_after_completion      = "DELETE"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sns_topic.cb_expiry_alert[0].arn
    role_arn = aws_iam_role.cb_expiry_scheduler[0].arn
    input = jsonencode({
      cb_end_date = local.pool_cb_end_date[each.key]
      message     = "... expires at ${local.pool_cb_end_date[each.key]}. Begin graceful drain now (1 hour remaining)."
    })
  }
}
```

読みどころは次の 4 点です。

**`formatdate` のトークンは `hh` であり `HH` ではありません。** Terraform の `formatdate` は `hh` が 24 時間制、`HH` が 12 時間制という、他言語の日時フォーマット関数とは逆の対応になっています。ここで `HH` と書いてしまうと、午後の時刻を含むアラートが軒並みずれる実害が出ます。実装のコメントにも「hh = 24-hour hour (HH would be 12-hour)」と明記されており、このトークンは写経時に取り違えやすい罠です。

**2 段階のフィルタで `timeadd("")` のエラーを避けています。** `cb_pools_with_end_date` と `cb_alert_pools` が 1 つの `&&` 条件ではなく 2 段に分かれているのは、HCL が `&&` の右辺を必ず短絡評価するとは限らないためです。`local.pool_cb_end_date[k]` が空文字のまま `timeadd()` に渡ると即座にエラーになるので、空文字を弾く段と `timeadd()` を呼ぶ段を明確に分離しています。

**`action_after_completion = "DELETE"` による自己削除に、`for_each` の除外ロジックが追従します。** スケジュールは発火すると AWS 側から自動的に消えます。そのため `cb_alert_pools` は、`cb_end_date - 1h`（アラート時刻）がすでに過去になったプールを `for_each` の対象からあらかじめ除外しています。これがないと、発火済みで消えたスケジュールに対して次の plan が「過去の時刻で作り直そう」としてしまい、Scheduler API の `at()` はいつも未来でなければならないため reject され続けます。なお、コード上のコメントにもある通り、plan 生成の直後にアラート時刻を過ぎ、その後 apply が走るという境界条件では、この仕組みでも構造的には救えず apply が失敗します。この場合は plan/apply をやり直せば解消します。

**通知本文が参照するのは `local.pool_cb_end_date` であり、`each.value.cb_end_date` ではありません。** `each.value.cb_end_date` は tfvars の生の値で、`capacity-block.tf` の自動導出が効いている今は多くの場合空文字です。スケジュール時刻の計算にも通知メッセージの組み立てにも `local.pool_cb_end_date`（導出済みの終了時刻）を使うことで、両者が食い違って「expires at 」のように日付が抜けたメッセージが届く事故を防いでいます。

## 全体の中での位置付け

Basic04 までに作った `accelerator_pools` という型に、本章では「予約 ID をどう埋めるか」という運用手順を積み重ねます。プール定義そのものの構造は変わりません。CB は容量調達の一手段であり、NodePool/NodeClass の設計自体には手を入れないという点が、この章を Basic04〜Basic05 の続きとして位置づける根拠になります。

## 注意

分散 AI 特有の落とし穴が 4 つあります。いずれも一度ハマると原因特定に時間がかかるため、最初に押さえておきます。

**注意 1: CB ノードが起動しない「filtered out all instance types」。** apply 後に Karpenter がノードを起動できない場合、直前に同じ予約を使っていた別インスタンスがまだ終了処理中で、予約スロットが実際には空いていないことが多くあります。少し待って Karpenter のリトライに任せれば解決するケースがほとんどです。

**注意 2: `capacity-reservation` taint も別に存在する。** CB ノードには、プールの `device_plugin` に応じた `nvidia.com/gpu` または `aws.amazon.com/neuron` の taint に加えて、予約ごとに値が変わる `capacity-reservation` taint も付きます。値が予約ごとに変わるため、ワークロード側は `operator: Exists` で値を問わずに tolerate する必要があります。`operator: Equal` で固定値を書いてしまうと、次に別の CB を買い直した瞬間にジョブがスケジュールされなくなります。

**注意 3: `cb_end_date` は UTC 必須（末尾 `Z`）。** `formatdate` は `cb_end_date` の年月日時分秒をそのまま文字列に整形するだけで、タイムゾーンオフセットの情報を持ち越しません。その結果できた `at()` 式にはタイムゾーン情報が一切含まれず、Amazon EventBridge Scheduler 側は `schedule_expression_timezone = "UTC"` で解釈します。したがって `cb_end_date` にタイムゾーンオフセット付き（例: `+09:00`）の値を書くと、その数字はオフセットを剥がされたうえで UTC の時刻として再解釈されます。結果としてアラートが実際の期限から最大で日付をまたぐほどずれます。この構成では変数の `validation` で `cb_end_date` が空でない限り末尾が `Z` であることを強制し、plan 時点で弾いています。

**注意 4: `cb_reservation_id` を設定しても `capacity_type` を `"reserved"` にし忘れると予約が無視される。** `cb_reservation_id` だけ書いて `capacity_type` を `"on-demand"` のままにすると、EC2NodeClass は `capacityReservationSelectorTerms` を持たずに生成され、予約はまるごと無視されて On-Demand で起動します。前払いした予約分の費用に加えて On-Demand の費用も別にかかる、という二重コストに直結するため、この構成では変数の `validation` でこの組み合わせを明示的に弾いています。

なお、Amazon EventBridge スケジュールは発火後に自己削除される点にも注意が必要です。`action_after_completion = "DELETE"` を設定しているため、アラートが一度発火するとその `aws_scheduler_schedule` リソースは AWS 側から消えます。Terraform 側もこれに追従できるよう、アラート時刻（`cb_end_date - 1h`）が過去になったプールは `for_each` の対象から自動的に除外される仕組みになっています。これがないと、発火済みで消えたスケジュールを次の plan が「過去の時刻で作り直そう」として Amazon EventBridge API に reject され続けることになります。

# ワークショップ実施

本章の実機検証は p4d.24xlarge（NVIDIA A100 40GB x8、EFA x4）2 台の Capacity Block で実施しました。以降のコマンド出力はすべてこの構成の実測値です。読者が別のインスタンスタイプで進める場合、台数や EFA の枚数は当然変わります。本章のスクリプトはそうした値を決め打ちせず AWS API から取得する作りにしてあるので、コマンドはそのまま使えます。

## 1. オファリングを検索する（読み取りのみ、課金なし）

```bash
cd infra/eks/scripts
./00-check-cb-offerings.sh \
  --region us-west-2 \
  --instance-types p4d.24xlarge \
  --instance-count 2 \
  --duration-hours 24
```

`describe-capacity-block-offerings` を叩き、インスタンスタイプ・AZ・開始/終了時刻・upfront fee の一覧を表示するだけの read-only スクリプトです。この時点ではまだ何も購入していないので、何度実行してもコストは発生しません。

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

## 3. terraform.tfvars に反映するブロックを生成する

```bash
./02-post-purchase.sh \
  --cr-id cr-0123456789abcdef0 \
  --end-date 2026-08-03T11:30:00Z \
  --instance-type p4d.24xlarge \
  --pool gpu-p4d
```

このスクリプトは AWS に対して何も呼びません。前のステップで得た `cr-...` と終了時刻を、`accelerator_pools` に貼り付けられる HCL ブロックとして標準出力に整形するだけです。

```hcl
gpu-p4d = {
  instance_types    = ["p4d.24xlarge"]
  device_plugin     = "nvidia"
  capacity_type     = "reserved"
  cb_reservation_id = "cr-0123456789abcdef0"   # zone はこの予約から導出
  cb_end_date       = "2026-08-03T11:30:00Z"   # 省略可、予約から自動導出される値の緊急上書き用
  volume_size       = "500Gi"
}
```

`device_plugin` はインスタンスタイプのファミリから決まります。`trn` または `inf` で始まれば `neuron`、それ以外は `nvidia` が入るので、Trainium/Inferentia の CB を買った場合も同じスクリプトがそのまま使えます（Neuron の場合は Neuron 用 AMI を指す `ami_ssm_parameter` の行も併せて出力されます）。

`zone` は含まれません。前述のとおり `reserved` プールの AZ は予約から導出されるため、スクリプトも既定では `zone` 行を出しません。特定の AZ に固定したい場合だけ `--zone <az>` を渡すと、その明示指定を含んだブロックが出力されます。`cb_end_date` も同様に、予約が返す実際の終了時刻を `capacity-block.tf` が自動導出するため、貼り付けなくても期限アラートは機能します。書いた場合はその値が予約側の `EndDate` より優先される緊急上書きとして働くので、予約を更新しても `cb_end_date` を書き換え忘れるとアラートが古い時刻のまま固定される点に注意してください。出力されたブロックを `terraform.tfvars` の `accelerator_pools` に貼り付けます。

## 4. apply して NodePool を確認する

```bash
cd infra/eks
terraform apply
kubectl get nodepool gpu-p4d
kubectl get ec2nodeclass gpu-p4d
```

apply が作るのは NodePool と EC2NodeClass の定義であって、この時点ではまだノードは立ちません。Karpenter は GPU を要求する Pod（Pending）が現れて初めてノードを起動します（Karpenter 自体のインストールと NodePool 生成の仕組みは Basic04 で構築済みという前提です）。実際にノードを立てるのは次の手順で、CB プールをターゲットにしたワークロードを投入したときです。ノードが立った後は次のコマンドで確認できます。

```bash
kubectl get nodeclaims -l karpenter.sh/nodepool=gpu-p4d
kubectl get nodes -l karpenter.sh/capacity-type=reserved
```

実機出力（p4d の Capacity Block は us-west-2d に確保したもの）:

```text
NAME            TYPE           CAPACITY   ZONE         NODE                                         READY
gpu-p4d-5zlm9   p4d.24xlarge   reserved   us-west-2d   ip-10-0-115-100.us-west-2.compute.internal   True
```

`CAPACITY = reserved` の NodeClaim が表示され、`ZONE` が予約の AZ に一致していれば、CB からのノード起動は成功です。ここで `zone` を `terraform.tfvars` に一切書いていないことを思い出してください。`us-west-2d` は予約から自動導出された値です。

## 5. マルチノードで NCCL/EFA を検証する

ここが Capacity Block を確保した目的の確認です。予約した複数ノードが実際に EFA/RDMA で通信できているかを測ります。EFA の仕組みそのもの（カード枚数の導出、card 0 問題、セキュリティグループの要件）は Basic05 で扱っているので、手順 3 までを終えていることを前提にします。

:::message
テストは 2 ノードで実行するので、手順 4 で `CAPACITY = reserved` のノードが 2 台起動していることを先に確認してください。1 台しか確保していない場合、この手順は実行できません（同一ノードに 2 つの Pod を載せると NCCL が NVLink だけで通信を完結させ、EFA について何も検証できないテストになるため、後述の podAntiAffinity で明示的に禁止しています）。
:::

EFA の枚数も GPU の枚数もインスタンスタイプごとに違うため、コマンドに直接書かず、対象プールのノードの `.status.allocatable`（device plugin が実際に広告している値）から読み取って渡します。

```bash
export NAMESPACE=distai
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

POOL=gpu-p4d
GPU=$(kubectl get nodes -l node-role=$POOL \
  -o jsonpath="{.items[0].status.allocatable['nvidia\.com/gpu']}")
EFA=$(kubectl get nodes -l node-role=$POOL \
  -o jsonpath="{.items[0].status.allocatable['vpc\.amazonaws\.com/efa']}")
echo "gpu=$GPU efa=$EFA"   # p4d.24xlarge では gpu=8 efa=3

helm template exp charts/experiments -n "$NAMESPACE" \
  --set namespace="$NAMESPACE" \
  --set ncclSshd.enabled=true \
  --set ncclSshd.nodeRole=$POOL \
  --set ncclSshd.gpuCount=$GPU \
  --set ncclSshd.efaCount=$EFA \
  --set ncclSshd.image=public.ecr.aws/hpc-cloud/nccl-tests:cuda12.8.1-efa1.42.0-ofiv1.16.0-ncclv2.27.5-1-testsv2.16.4 \
  | kubectl apply -f -
```

`ncclSshd` は 2 つの Pod を別ノードに立て、それぞれに sshd を常駐させて、片方から `mpirun` で相手を叩く構成です。`nccl-tests` は MPI ベースなので rendezvous は `mpirun` が担います。

指定の要点は 3 つあります。

第一に、`ncclSshd.nodeRole` にはプール名を渡します。ノードの GPU SKU を表す `nvidia.com/gpu.product` で選びたくなりますが、このラベルは GPU Operator が起動済みのノードに後から付与するものなので、Karpenter が「どのインスタンスタイプを起動するか」を判断する材料になりません。これを nodeSelector に使うと Karpenter は次のように要求を拒否し、2 台目のノードが永久に起動しません。

```text
Failed to schedule pod, incompatible requirements,
label "nvidia.com/gpu.product" does not have known values
```

Karpenter が起動時に付ける `node-role=<プール名>` を使えば、ノードがまだ存在しない状態からプロビジョニングを誘発できます。

第二に、`gpuCount` と `efaCount` は上のようにノードから読んだ値を渡します。EFA の schedulable 数はファミリごとに違うため、固定値を書くと別のファミリでは必ず Pod が Pending になります。

第三に、SSH 鍵の配布は不要です。チャートがレンダリング時に鍵ペアを生成して Secret として両 Pod に配るため、Pod が `Running` になった時点で `mpirun` がそのまま通ります。

:::message
`helm template` は実行ごとに新しい鍵を生成します。上の例のようにパイプで一度に `kubectl apply` するか、いったんファイルに書き出してから適用してください。2 回に分けてレンダリングすると server と client が別々の鍵を持つことになり、SSH が通りません。
:::

2 つの Pod には hostname 単位の `podAntiAffinity` が入っており、同じノードに載ることはありません。同一ノードに載ると NCCL は NVLink だけで通信を完結させてしまい、EFA について何も検証できないテストになるためです。

:::message alert
`hugepages` を要求する Pod でノードの新規起動を誘発しないでください。Karpenter は hugepages を「どのインスタンスタイプなら足りるか」の判断に使わないため、`no instance type has enough resources` と判定して NodeClaim を作らず、Pod が永久に Pending になります。2 台目以降のノードは hugepages を要求しない Pod で先に起動させ、そのうえで hugepages を使うベンチマークを載せてください。この制約は Neuron 側のプローブでも同じです。
:::

両 Pod が `Running` になったら、server 側から `mpirun` でベンチマークを起動します。

```bash
SIP=$(kubectl -n "$NAMESPACE" get pod nccl-server -o jsonpath='{.status.podIP}')
CIP=$(kubectl -n "$NAMESPACE" get pod nccl-client -o jsonpath='{.status.podIP}')

kubectl -n "$NAMESPACE" exec nccl-server -- bash -lc "
/opt/amazon/openmpi/bin/mpirun --allow-run-as-root -np $((2 * GPU)) \
  -H $SIP:$GPU,$CIP:$GPU --mca plm_rsh_args '-p 2222' \
  -x FI_PROVIDER=efa -x FI_EFA_USE_DEVICE_RDMA=1 -x FI_EFA_FORK_SAFE=1 \
  -x NCCL_SOCKET_IFNAME='^lo,docker,veth' \
  -x NCCL_DEBUG=INFO -x NCCL_DEBUG_SUBSYS=INIT,NET \
  -x LD_LIBRARY_PATH -x PATH \
  /opt/nccl-tests/build/all_reduce_perf -b 512M -e 1G -f 2 -g 1"
```

`NCCL_DEBUG` は `INFO` にします。次に確認する `NET/OFI Selected provider is efa` の行は `INFO` レベルでしか出力されず、`WARN` では EFA が使われた証拠が得られません。`NCCL_DEBUG_SUBSYS=INIT,NET` で対象サブシステムを絞り、ログが溢れるのを防いでいます。両 Pod は `hostNetwork` なので Pod IP はノード IP と一致します。


確認ポイントは次の 2 つです。

- ログに `NET/OFI Selected provider is efa` が出ることを確認します（TCP fallback していない証拠になります）
- `busbw` が高い値を示すことを確認します

実機確認結果（2 ノード p4d.24xlarge、A100 x16、EFA 3 NIC/ノード、`all_reduce_perf` 16 ランク）:

```text
ip-10-0-115-100:318:365 [2] NCCL INFO NET/OFI Using transport protocol SENDRECV (platform set)
ip-10-0-115-100:318:365 [2] NCCL INFO NET/OFI Selected provider is efa, fabric is efa (found 3 nics)
ip-10-0-124-216:273:320 [0] NCCL INFO NET/OFI Selected provider is efa, fabric is efa (found 3 nics)
```

両ノードで `efa` プロバイダが選択され、3 NIC が認識されています。この `found 3 nics` が、手順 1 で見た `terraform output accelerator_pool_efa_schedulable` の `gpu-p4d = 3`（= 4 − 1）と一致していることが重要です。カード枚数から 1 引いた値が、そのまま NCCL が掴む NIC 数になります。

busbw 実測値:

| メッセージサイズ | algbw | busbw |
|---|---|---|
| 32 MB | 15.7 GB/s | 29.4 GB/s |
| 128 MB | 24.5 GB/s | 46.0 GB/s |
| 512 MB | 30.0 GB/s | 56.2 GB/s |
| 1024 MB | 30.9 GB/s | 57.9 GB/s |

平均 busbw は 57.0 GB/s でした。EFA が効いていることを確かめるには絶対値だけでなく比較対象が必要なので、同じコマンドを単一ノード 8 GPU（ノードをまたがないので NVLink のみ）で実行した値を並べます。

| 構成 | 通信経路 | busbw（1 GB） |
|---|---|---|
| 1 ノード 8 GPU | NVLink のみ | 227.1 GB/s |
| 2 ノード 16 GPU | ノード間は EFA | 57.9 GB/s |

ノードをまたぐと NVLink の約 4 分の 1 に落ちますが、これは想定どおりです。p4d.24xlarge の EFA は 4 カード構成で、そのうち通信に使えるのは 3 枚なので、NVLink の帯域には及びません。重要なのは 57.9 GB/s という値が TCP 経由（一般に数 GB/s 台）では到達できない水準にあることで、これが EFA/RDMA が実際に使われている証拠になります。EFA カードが 16 枚ある p5en や 32 枚ある p5 では、この数字はさらに大きくなります。

この 2 つの値は同じ構成で日を変えて複数回測っても 57-58 GB/s と 227 GB/s に収まりました。読者の環境で桁が違う値（たとえばノード間が数 GB/s 台）になった場合は、EFA ではなく TCP にフォールバックしている可能性が高いので、先に `Selected provider is efa` の行が出ているかを確認してください。

:::message
`fabric` の表示は `efa` と `efa-direct` の 2 種類があります。上の実測では `efa` が選択されており、同時に `Using transport protocol SENDRECV (platform set)` が出ています。どちらが選ばれるかはインスタンス世代・libfabric・aws-ofi-nccl のバージョンの組み合わせで決まるため、`efa-direct` でなくても異常ではありません。判定の要点は `Selected provider is efa` であること、つまり TCP へ落ちていないことです。
:::

:::message
NCCL テストを実行するには、テスト対象の GPU が他の Pod（Ray ワーカーなど）に占有されていないことが前提です。既存のワークロードを停止してからテストを実行してください。
:::


## 6. 期限アラートを確認する

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

予約から自動導出された終了時刻（または `cb_end_date` で明示的に上書きした終了時刻）の 1 時間前に `at()` 式の Amazon EventBridge Scheduler スケジュールが 1 つ作られ、共有 Amazon SNS トピックにメール通知が届きます。通知が来たらワークロードの graceful drain を開始します。

メールを受け取るには、通知先アドレスを `terraform.tfvars` の `cb_alert_email_addresses` に設定して apply しておきます。設定すると Terraform が SNS トピックへの email サブスクリプションを作成しますが、AWS から確認メールが届くので、その中のリンクを一度クリックして承認するまで通知は届きません（SNS の仕様です）。

```hcl
# terraform.tfvars
cb_alert_email_addresses = ["you@example.com"]
```

## 7. teardown する

```bash
./04-teardown.sh --namespace "$NAMESPACE" --nodepool gpu-p4d
```

Deployment/StatefulSet/Job/MPIJob を削除し、GPU Pod が完全に終了したのを確認したうえで Karpenter の NodePool を削除します。CB のノード自体は予約期間の終了時に AWS 側で強制回収されるため、このスクリプトは「ワークロードを安全に退避させる」ところまでを担当します。クラスタ全体を壊す `terraform destroy` は `--destroy` を明示した場合のみ実行されます。

# まとめ

本章では、Basic04 で用意した `capacity_type = "reserved"` を使い、Capacity Block の検索・購入から `terraform.tfvars` への反映、確保したノードでのマルチノード NCCL/EFA 帯域測定、期限アラート、teardown までの一連の運用フローを構築しました。

手で書いたのは予約 ID と `capacity_type = "reserved"` の 2 つだけです。AZ は予約から自動導出され、期限アラートも予約の終了時刻から組み立てられます。実測では p4d.24xlarge 2 台で busbw 57.9 GB/s（単一ノード NVLink は 227.1 GB/s）が出て、NCCL のログが `Selected provider is efa (found 3 nics)` を示しました。この `3` が Basic05 で見た schedulable EFA 数と一致することが、EFA が意図どおり配線されている証拠になります。

CB は前払いで取り消しができないため、On-Demand で動作確認を済ませたジョブを最後に載せる、という段階的な使い方が安全です。価格は `00-check-cb-offerings.sh` が「ブロック全体の総額」と「1 台 1 時間あたり」の両方で示すので、購入前に On-Demand 単価と比較できます。`capacity_type` の指定漏れによる二重課金など、Terraform の `validation` で弾いている落とし穴も合わせて押さえておけば、期限管理まで含めた CB 運用を事故なく回せます。

# 参考資料

- [Amazon EC2 Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html)
- [Amazon EventBridge Scheduler](https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html)
- [awslabs/awsome-distributed-training](https://github.com/awslabs/awsome-distributed-training)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
