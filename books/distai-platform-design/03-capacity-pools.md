---
title: "NodePool と ResourceFlavor を束ねる容量プール"
---

## はじめに

前の章では、容量を混ぜる悩みを 4 つの決定に分解し、Karpenter と Kueue を核にする構成を推奨とした。この章では、その推奨を実際に動く規約にまで落とし込む。核になるのは、NodePool と ResourceFlavor を 1 対 1 に結ぶ単純な規約である。この規約の上に、3 種類のプール、ワークロードごとの経路、高価な容量を複数の用途で分け合う仕組みが積み重なる。最後に、この構成が立っている最も危ない前提と、それを確かめるために計画されている実験を見る。

## 1. 核になる規約: 1 NodePool = 1 ResourceFlavor

この規約は、Karpenter の NodePool と Kueue の ResourceFlavor を 1 対 1 で機械的に対応させ、人が名前を手で合わせることから生じるずれを無くす。distributed-ai の実装では、Kueue のキュー設定は別のテンプレートにあり、プールの名前を人が手で結んでいた。名前を手で合わせる以上、どちらかを変更したときに結び付きが黙って崩れる可能性が常にある。

推奨する構成では、この結び付けを Karpenter がもともと全てのノードに付けているラベル `karpenter.sh/nodepool` を使って機械的に行う。ResourceFlavor の `nodeLabels` にこのラベルを書けば、NodePool と ResourceFlavor は名前で自動的に対応する。独自のラベルを新しく発行する必要はない。

```yaml
pools:
  - name: trn2-3xl-rsv
    accel: neuron
    instanceTypes: [trn2.3xlarge]
    capacityTypes: [reserved]
    reservationTags: {pool: trn2-3xl-rsv}
    quota: 1
  - name: trn2-3xl-flex
    accel: neuron
    instanceTypes: [trn2.3xlarge]
    capacityTypes: [spot, on-demand]
    quota: 4
```

この例は、Helm chart の values の形を示している。`name` が NodePool と ResourceFlavor 共通の名前になり、`accel` が拡張リソース名や taint、AMI の系統を決め、`quota` が Kueue の `nominalQuota` に対応する。ここで示した形は、まだ実機で確かめられた実装ではなく、Issue #17 の検討で提案された設計である。Issue #17 の設計メモの例では、chart の名前を `capacity-pool` とし、`reservationTags` が EC2NodeClass の `capacityReservationSelectorTerms` に、`quota` が `-flex` では「予算の上限 (容量の保証ではない)」になることがコメントで示されている。values を 1 プールあたり 10 行前後に収める簡潔さが目標であり、この values から NodePool と ResourceFlavor の対を生成する Helm chart と ApplicationSet を作ることが、この設計から導かれる最初の実装 Issue の 1 つになっている。ただし、規約の核である「`karpenter.sh/nodepool` で ResourceFlavor を NodePool に結ぶ」部分だけは、すでに実機で動いている。6 章の実験 E3 では、`g7.2xlarge` の spot (eu-south-2) で device plugin のプールと DRA のプールをこのラベルで別々の flavor に結び、Kueue がそれぞれを `nvidia.com/gpu: 1` として数えることを確かめた。一方、例に挙げた Trainium のプールを Kueue の枠と組み合わせた形は一度も動かしていない。7 章の実験 E4 (`trn2.3xlarge`) は Kueue を入れずに行ったためである。

<!-- FIG name="03-pools-pairing" kind="relation" shows="NodePool and ResourceFlavor joined by karpenter.sh/nodepool; three pools -rsv/-od/-flex; which carries a real quota" labels="NodePool, ResourceFlavor, karpenter.sh/nodepool, -rsv, -od, -flex, nominalQuota" -->
![NodePool と ResourceFlavor の対応を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/03-pools-pairing.png)

## 2. 3 種類のプール: -rsv、-od、-flex

プールは目的別に 3 種類に分ける。接尾辞がそのまま容量の性質を表す。

| プール | `capacity-type` | Kueue の枠が意味するもの | 入場できたら容量があるか |
| --- | --- | --- | --- |
| `-rsv` | reserved | 予約の実数 | ある (予約が有効な間) |
| `-od` | on-demand | 予算の上限 | 分からない |
| `-flex` | spot、on-demand | 予算の上限 | 分からない (spot が無ければ Karpenter が on-demand に切り替える) |

予約だけの `-rsv` を他の容量と混ぜずに独立したプールにする理由は、Karpenter の挙動そのものにある。Karpenter v1.15 の文書によれば、1 つの NodePool に reserved、spot、on-demand を混ぜて書くと、Karpenter は reserved、spot、on-demand の順に試す。ODCR と Capacity Block への対応は `ReservedCapacity` という feature gate の後ろにあり (`capacityReservationSelectorTerms` で予約を選ぶ)、対象を絞らない open な ODCR には対応していない。reserved を最初に試すこの順序は一見望ましい挙動だが、混ぜたプールのまま Kueue の枠を作ると、Kueue が数えている「予約の枠」と、実際に空いている予約容量の量がずれてしまう。混ぜたプールでは、別の枠で入場したジョブ (たとえば `-flex` 相当の予算枠で入ったジョブ) にも Karpenter が予約のインスタンスを先に割り当てうるので、予約が誰に使われたかを Kueue の枠では追えなくなる。その結果、高価な予約を複数の用途で順序立てて使い回す (設計メモはこれを「兼ねる」と呼ぶ) という、次の節で説明する仕組みや、中断されてはならないワークロードを確実に予約に乗せるという保証が崩れる。予約だけを別のプールに切り出しておけば、Kueue の `-rsv` の枠は「今有効な予約の台数 × デバイス数」を表す値になる。この値は人が PR で同期させるので、「入場できたら容量がある」が `-rsv` で成り立つのは、その PR が予約の実際と合っている間だけである。予約の終了時には枠を 0 に戻す手順を持つことが、設計メモの実装 Issue の 1 つに挙がっている。なお、個人の開発用セルであれば、1 つのプールに 3 種類の容量を書き flavor を 1 つにする最小形でも構わないと設計メモは書いている (chart は同じで、`capacityTypes` の中身が違うだけである)。

## 3. ワークロードの型と経路

ワークロードの性質によって、どの順でプールを使うか、中断にどう備えるかが変わる。

| ワークロードの型 | flavor の順 | 中断への備え |
| --- | --- | --- |
| 短い単一ノードの学習ジョブ | `-rsv` (空きを借用) → `-flex` | 中断されたらやり直せばよい |
| EFA を使う複数ノードの学習 | `-rsv` のみ | gang 入場と `waitForPodsReady` を使う。Capacity Block の終了前にチェックポイントを取る。spot は使わない |
| 常設の推論サービス | `-rsv` (ODCR) → `-od` | 常設分の容量には ODCR か on-demand を使う。distai-platform は Capacity Block を 24 時間単位で買う運用なので、Capacity Block は負荷試験にだけ使う |
| 中断されてよいバッチ | `-rsv` (借用のみ) → `-flex` | SkyPilot の managed jobs が探し直して再起動する。チェックポイントはジョブ側の責任である |

<!-- FIG name="03-workload-routes" kind="relation" shows="each workload type mapped to its flavor order and interruption handling" labels="short training, multi-node EFA training, long inference, interruptible batch, -rsv, -od, -flex, waitForPodsReady, reclaimWithinCohort" -->
![ワークロードの型と経路の対応を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/03-workload-routes.png)

高価な容量ほど、1 つのワークロードに固定せず複数の用途で分け合う価値が大きい。これを実現するのが、Kueue の Cohort、借用、preemption である。Capacity Block の開始時刻より前に、同じ Cohort に属する LocalQueue へジョブを積んでおく。開始時刻になったら Kueue の枠を追加する pull request を 1 本通すだけで、積んでおいたジョブが優先度順に流れ始める。予約が一時的に空いているときは、別のワークロードがその枠を借用でき、本来の持ち主のジョブが来た時点で `reclaimWithinCohort` によって取り返される。Karpenter の文書によれば、予約から起動したノードには `karpenter.k8s.aws/capacity-reservation-id`、`-type`、`-interruptible` のラベルが付き、Capacity Block の終了前にはノードが drain される。Capacity Block の終了前にチェックポイントを取るという上の表の備えは、この drain に間に合わせるためのものである。Kueue の `whenCanBorrow` の既定値は `MayStopSearch` であり、借用できる flavor があればそこで探索を止める挙動になる。この既定の挙動は、「払い済みの予約をまず使い切る」という方向に自然に働く。

## 4. 一番危ない前提と、確かめる実験

この構成全体が立っている前提のうち、最も危ういのは「Kueue が入場させた = その容量が実際に存在する」という前提である。この前提が成り立つのは `-rsv` だけである (2 節で見たとおり、それも枠の PR が予約の実際と合っている間に限る)。Kueue には `ProvisioningRequest` という、容量が本当に確保できるかを admission の前に確かめる仕組みがあるが、公式文書 (2026-11-17 に確認) を見てもこれは cluster-autoscaler 向けに作られており、Karpenter についての言及が無い。言及が無いことは対応していないことの証明ではないが、少なくとも Karpenter と組み合わせて使える根拠は文書に無い。そのため、`-od` や `-flex` に入場したジョブは、Karpenter が実際のノード起動に失敗しても、Kueue の側ではそれに気付かず、ジョブが枠を握ったまま待ち続けるはずだと推測されている。これはまだ実機で確認されていない推測であり、2 章で見た推奨構成が「数えられない容量は Karpenter に探させる」という形を取っている理由も、この失敗の仕方を最初から想定しているためである。`-od` と `-flex` の枠は予算の上限にすぎず、容量の有無を Kueue に判断させないので、どの型と AZ で起動するかは Karpenter が実際の起動の成否を見て選び直す。ただし、この回避が本当に効くかどうかも検証されておらず、次の表の実験 1 が最初に確かめる対象である。

この前提を含めた構成全体の健全性を確かめるための実験が、Issue #17 で計画されている。Issue #17 は、これらの実験より先に、デバイスプラグインと DRA (Dynamic Resource Allocation) に関する Issue #19 の実験 (#17 の文面では E1 から E4) を済ませることにした。デバイスを公開する方法としてどれを選ぶかによって、ここで説明したプールの規約そのものが変わりうるためである。#19 の実験は 2026-11-18 に実施済みである。#19 では机上の確認を E0 と呼び、実機の実験が E1 から E4 にあたる。結果は E0 から E3 (eu-south-2 の `g7.2xlarge`、6 章) と E4 (ap-southeast-4 の `trn2.3xlarge`、7 章) の 2 つにまとめて記録されており、E5 から E7 は計画のまま残っている。

その結果のうち、この章の規約に効くものは次のとおりである。NodePool ごとに、デバイスの公開方式を表すラベルをデバイスクラスごとに 1 つ付ける必要がある (5 章の決定 2)。Layer 2 (`ResourceClaimTemplate` でデバイスを要求する形。5 章で定義する) のジョブは、空いている device plugin の flavor に入場して動けなくなったので、DRA だけの flavor を持つキューに送るか、`karpenter.sh/nodepool` を DRA のプールに固定する必要がある (6 章の E3)。Neuron の DRA プールは動的には作れず、`StaticCapacity` gate を使う静的な NodePool にする必要がある (7 章の E4)。Karpenter は `trn2.3xlarge` の `neuroncore` を 8 と見積もるが、実際は 4 である (7 章の E4)。上の values の形にはまだ方式ラベルの欄が無く、#17 の受け入れ条件も、推奨構成を #19 の結果で更新することを求めている。

Issue #19 側の実験が終わったあとに計画されている、容量側の 5 つの実験は次のとおりである。

| # | ハードウェアと容量 | 費用の目安 | 分かれば次に進めること |
| --- | --- | --- | --- |
| 1 | `g7.2xlarge` の spot (spot placement score が高いリージョン)。失敗する役には `g6e.xlarge` の spot (どこも点が 1) か、上限 0 の NodePool を使う | 数ドル程度 (推測。同じ `g7.2xlarge` の E0 から E3 は約 1 時間 43 分で 2.5 から 3 ドルと見積もられた) | Kueue と Karpenter で起動に失敗したとき、`waitForPodsReady` のタイムアウト後にジョブが次の flavor へ進むか、同じ flavor に戻るか (戻るなら flex 方式を確定する)。flavor と NodePool の対が動くか。GPU と Neuron を 1 つの resourceGroup にまとめて書けるか |
| 2 | `trn2.3xlarge` の spot、`ap-southeast-4` | 数ドル程度 (推測。同じ型とリージョンの E4 は約 1 時間 15 分で 4 から 5 ドルと見積もられた) | Trainium が同じ chart で立ち、smoke test が通るか。SkyPilot の `accelerators:` で Trainium の Pod が作られ、Kueue のキューを通るか (作者の記事でも未検証だった点) |
| 3 | `g7.2xlarge` の targeted ODCR 2 台を 1 から 2 時間 | on-demand 2 台分 | タグで予約を拾い、`reserved` のラベルが付き、AZ が自動で合うか。予約の数を超えて入場させないか。借用と取り返しが動くか。`-flex` のジョブが予約に載らないか |
| 4 | `trn2.3xlarge` の 24 時間 Capacity Block (ap-southeast-4) と、安い GPU の spot のセル | 54 ドルと少額 | 開始前に積んだジョブが開始と同時に流れるか、終了前の drain がいつ起きるか (Capacity Block の一生)。セル A の枠を埋めたとき SkyPilot が B へ逃げるか A で待ち続けるか、同じ条件で MultiKueue はどうか (ここでクラスタを選ぶ役を 1 つに決める)。新しいセルが使えるまでの時間 |
| 5 | `trn2.48xlarge` の spot 2 台を東京 (ap-northeast-1、点 9) で。取れないか保てなければ us-east-2 の Capacity Block 2 台 | spot は未調査である。Capacity Block の場合は 858 ドル × 2 で 1,716 ドルになる | まとめての入場、同一 AZ への配置、EFA の集団通信が動くか、TAS (Topology Aware Scheduling) が要るか。Capacity Block を買う場合は、この 1 日に複数の検証を積む |

実験 5 には 2 つ注意がある。1 つは、3 節の表で EFA を使う複数ノードの学習は spot を使わないとしたのに、実験 5 は spot から試す点である。これは経路の規則ではなく、安く集団通信を確かめるための検証上の選択である。もう 1 つは、`trn2.48xlarge` は AWS の公開仕様で 1 台 192 vCPU なので、2 台で 384 vCPU になり、4 章で見るこのアカウントの Trn の上限 (spot、on-demand ともに 256 vCPU) を超える点である。この突き合わせは設計メモには無く本書の側で行った計算であり、実験の前に上限の引き上げが要ると考えられる。また 4 章の調査では、東京の `trn2.48xlarge` の Capacity Block は照会自体が対応していないという答えだった。spot の点は見込みであって保証ではなく、要求する台数によっても変わる。実験 3 と 5 は 2 台を求めるので、1 台で取った 4 章の点はそのまま当てはまらない。

起動の結果は、成功したか失敗したかを問わず、インスタンスの型、容量の種類、AZ ID ごとに記録として残していく方針になっている。この記録は、4 章で見る容量の地図を、実際の起動結果で裏打ちする役割も兼ねる。

<!-- FIG name="03-risky-assumption" kind="flow" shows="Kueue admits a job and the job assumes capacity exists; this holds for -rsv but for -od/-flex Karpenter still has to launch and can fail without Kueue noticing" labels="Kueue admit, Karpenter launch, -rsv guaranteed, -od/-flex not guaranteed, ProvisioningRequest (not for Karpenter)" -->
![一番危ない前提を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/03-risky-assumption.png)

## まとめ

容量プールの規約は、`karpenter.sh/nodepool` のラベルを使って NodePool と ResourceFlavor を名前で機械的に結び、容量の性質によって `-rsv`、`-od`、`-flex` の 3 種類に分けるという、単純な形にまとまっている。ワークロードの型ごとに、この 3 種類をどの順で使うかと中断への備えが決まり、高価な予約は Cohort と借用、`reclaimWithinCohort` によって複数の用途で分け合う。この構成全体は「Kueue が入場させたら容量がある」という前提の上に立っているが、その前提が成り立つのは `-rsv` だけであり、`-od` と `-flex` で起動に失敗したときにジョブが枠を握ったまま待つという失敗の仕方は推測の段階で、実機で確かめる実験が計画されている。#19 の実験の結果は、方式ラベル、Layer 2 のキュー、Neuron の静的な DRA プールという形で、この規約に反映する必要がある。次の 3b 章ではこのプールを複数のチームで分け合う設計を扱い、4 章では、実験の前提になる、実際にどのリージョンでどれだけの容量が取れるのかという調査を見ていく。
