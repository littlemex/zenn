---
title: "常設クラスタなしで実機検証を計画し承認する"
---

## はじめに

distai-platform は GPU も Trainium も実機で動かして確かめることを前提にしているが、このリポジトリには常設の GPU や Trainium のクラスタが無い。クラスタ、リージョン、Availability Zone、AWS アカウントは実行のたびに変わり、容量は高価で、毎日手に入るとは限らない。この章で扱うのは、Issue [#16](https://github.com/littlemex/distai-platform/issues/16) で設計した「実機検証をどう計画し、承認し、まとめて流すか」という仕組みである。

AI のメンバーとメンテナが同じ GitHub アカウント配下で動くという特殊な制約のもとで、ラベル 1 個に押し込んでいた「検証に合意する」という行為を、計画・承認・購入の各段に分け(承認はさらに、PR ごとの Go とバッチごとの購入承認の 2 段に分かれる)、どこまでを GitHub の中で、どこからを AWS 側の権限で守るかを決めていく。

先に、この章で使う語をまとめておく。Go は、メンテナが「この commit とこの計画で実機を動かしてよい」と出す許可で、`status: verifying` ラベルで表す。tier は、許可と確保の重さによる PR の区分(`cpu-only`、`light`、`queued`、4 節)で、gate は実行の直前に Go が有効かを確かめる判定(3 節)、preflight は課金の前に提供状況やクォータを確かめる手順(6 節)である。policy ファイルは tier の判定規則を書いたファイル、orchestrator と Verifier は 10 章の役割表にある、Issue と PR を回すメインのセッションと実機で確かめるセッションである。ODCR は On-Demand Capacity Reservation、Capacity Block は GPU などを決まった期間だけ前払いで予約する買い方で(11 章 5 節)、offering はその照会で返る空き枠である。spot の点は spot placement score(1 から 10)を指す(4 章)。

なお、東京には destroy と再作成をしてよいテスト用のクラスタが 1 つあり、本章で「既存クラスタ」と呼ぶのはこれである。常設の GPU や Trainium の容量は持たない。

## 1. 1 つのラベルでは足りない理由

最初の提案は単純だった。AI が PR ごとに「何で検証するか」を事前にラベルで示し、メンテナが `status: verifying` を付けたら、そのリソースの確保に合意したことになる、というものである。高価な確保(24 時間の Capacity Block など)は複数の PR で兼ねて使い、PR が溜まったらまとめて流す。CPU だけのものは承認なしで検証まで進めてよく、spot や on-demand の `g6e`、`g5`、`g7`、`trn2.3xlarge` は承認されたらキューに回さずすぐ確保してよい、という想定だった。この単純な形を設計の段階で検討すると、壊れる点や抜けている点が 15 件挙がった。いずれも机上の検討で挙がったもので、実際に起きた事故ではない。アップストリームの `awslabs/awsome-distributed-ai` は、`p5` / `p5en` / `p6` の常設クラスタに紐づく self-hosted runner で検証し、`max-parallel: 1` と `concurrency` で直列化しているが、distai-platform にはこの常設の容量が無いため、同じ形はそのまま使えない。

| # | 問題 | 何が起きるか | 対処 |
| --- | --- | --- | --- |
| 1 | AI とメンテナが同一アカウント | `labeled` イベントの actor が同じで、AI が自分で Go を出しても GitHub の中では検出できない。コメントや承認の文言も同じく偽造できる | AGENTS.md で「AI は `status: verifying` を付けない」と定める。AI の常用ロールは軽い実行しかできない権限にし、購入や ODCR、クラスタ作成は MFA 必須のロールに分ける。根治には AI を GitHub App にするか、別のアカウントに分ける方法もある |
| 2 | Go が commit に束縛されない | 承認後に push すると、未レビューのコードがメンテナの資格情報で apply される。fork PR では Terraform の provider や local-exec による任意コード実行にもつながる | 承認を commit の SHA に束縛する。checkout はブランチ名でなく SHA で行う |
| 3 | 計画が承認後に変わる | 台数や容量の種類が Go の後で書き換わる | 計画を更新したら Go を失効させる |
| 4 | 「verifying = 確保に合意」がキュー対象では成り立たない | ラベルを付けた時点では、まとめて流すバッチの構成・価格・Availability Zone が未定。Capacity Block は購入後に取り消しも返金もできない | 承認を 2 段にする。PR ごとの Go と、バッチごとの購入承認(MFA 付き)に分ける |
| 5 | 「ライト」(承認後すぐ確保してよい範囲)がインスタンスファミリ名だけで定義されている | `g6e.48xlarge` や `g5.48xlarge` のような大型は 192 vCPU あり、このアカウントの東京の G 系の上限(64 vCPU)では `12xlarge`(48 vCPU)1 台や `8xlarge` 2 台までしか収まらない。共用アカウントなので他の実験の使用分も引かれる。「`g7`」というファミリ名が `g7e` を含むかどうかも曖昧である | ファミリ名だけでなく、合計 vCPU、費用、時間で判定する。クォータの残りは実行時に取得する |
| 6 | `trn2.3xlarge` は東京に無い | 2026 年 11 月 17 日の調査では、`trn2.3xlarge` を提供するのは `ap-south-2`、`sa-east-1`、`ap-southeast-4` だけで(東京にあるのは `trn2.48xlarge` だけ)、しかも `ap-south-2` と `ap-southeast-4` は opt-in リージョンである。「ライト」のつもりが、別リージョンにクラスタを新設する案件になる | 提供リージョンに既存クラスタがある場合だけライトとし、無ければキューに回す |
| 7 | リージョン・アカウント・クラスタが変わる | ラベルに書くと古くなる。AMI、クォータ、提供 Availability Zone が変わる。Availability Zone の名前(`-1a` など)とその実体の対応はアカウントごとに違う | 計画には要件だけ書き、環境は実行時の preflight で解決する。証拠には Availability Zone ID で記録する |
| 8 | spot の中断 | 中断が検証失敗として記録されるか、黙って再試行して費用が増える | 結果を pass / fail / inconclusive の 3 値にする。中断は inconclusive、再試行は 1 回までとする |
| 9 | 容量が取れない | 待ち続けて滞留するか、勝手に高い種別へ切り替わる | fallback は計画に列挙した順番だけ(承認済みの範囲)にし、試行時間に上限を設ける |
| 10 | コストの暴走 | Verifier は Mac 上のセッションなので、スリープやセッション切れで後片付けが走らない。ODCR の解放漏れや、専用クラスタの放置につながる | 期限をクラウド側に持たせる(NodePool の `expireAfter` と `limits`、ODCR の `EndDate`)。加えてタグによる残留掃除を行う |
| 11 | 共用クラスタは壊せない | VPC・EKS・Karpenter 本体を変える PR は既存の共用クラスタに apply できない(設計メモはこの東京のクラスタを共用として扱うが、`AGENTS.md` 4 節はテスト用クラスタを壊して作り直してよいものとしており、どちらで扱うかはメンテナが決める) | 計画に `scope` を持たせる。workload は既存クラスタへの追加のみ、cluster は専用クラスタを作る |
| 12 | CPU のみの免除範囲 | fork PR にも免除を適用すると、未レビューのコードを資格情報付きで実行することになる。「CPU」という括り自体にも上限が無いため、免除範囲が無制限に広がりうる | 免除は同一リポジトリのブランチだけに限る。fork PR は tier によらず Go が必須で、vCPU と時間にも上限を設ける |
| 13 | キューの飢餓 | 件数の閾値だけだと、1 件しかない種類が永久に待つ | 最古の待ち日数も条件にする。自動購入はせず提案までにする |
| 14 | 集計ページが第二の真実になる | PR 側と食い違う可能性がある | 集計ページは毎回全件を再計算する派生物とし、アカウント ID や予約 ID は載せない |
| 15 | 判定規則を PR 側から読む | PR が規則を書き換えて自分の tier を軽くできる | 判定の policy ファイルは必ず main から読む |

<!-- FIG name="09-problems" kind="relation" shows="単一のstatus:verifyingラベルが抱える問題群と、計画・承認・購入の3段への分割" labels="status: verifying, head_sha, plan comment, purchase approval, MFA" -->
![単一のラベルが抱える問題を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/09-problems.png)

## 2. 計画コメントとラベルの役割分担

2 節から 6 節は Issue #16 の設計であり、まだ実装されていない。現在形で書くのは仕組みの説明のためである。

設計の骨格は、「正」をメンテナのアカウントの固定コメント 1 件に置き、ラベルはそこから導く表示にすることである。計画コメントはマーカー `<!-- verify-plan:v1 -->` 付きで、更新は上書きする。

```yaml
head_sha: <40桁>
scope: workload            # workload | cluster
accelerator: gpu           # none | gpu | trainium
nodes:
  - instance_type: g6e.4xlarge
    count: 2
capacity: [spot, on-demand]   # 試す順 = 承認済みの fallback
timebox_minutes: 90
max_cost_usd: 20
tier: light                   # 導出値。gate で再計算して照合
```

PR 本文の `Environment:` の 3 行(アップストリームのテンプレートは変更しない)には同じ値を記入し、食い違いはスクリプトが検出する。ただし 3 行は `AWS Service`、`Instance type`、`Number of nodes` なので、照合できるのは型と台数だけで、scope、容量の種類、timebox、費用は計画コメントにしか無い。計画は fork PR のものも含めて全て orchestrator が書き、コメントの author がリポジトリの持ち主であることを確認する。つまり「メンテナのアカウントの固定コメント」は、同じアカウントで AI が書いたものである。

ラベルは手で編集しない。計画から毎回同期する。サイズ、台数、リージョン、Availability Zone、アカウント、クラスタ名はラベルに持たない。前の 2 つ(サイズと台数)は計画に書き、残り(リージョンなど)は実行時に解決する。結果のラベルも作らない。ラベルは SHA に束縛できないので、結果は証拠コメントに置く。

| ラベル | 意味 | 付ける主体 |
| --- | --- | --- |
| `verify: cpu-only` / `verify: light` / `verify: queued` | tier(排他) | スクリプトが計画から導出する |
| `capacity: on-demand` / `capacity: spot` / `capacity: odcr` / `capacity: capacity-block` | 計画の第 1 候補 | 同上 |
| `family: g5` / `family: g6e` / `family: g7` / `family: p5` / `family: trn2` など | 絞り込み用 | 同上 |
| `needs: dedicated-cluster` | scope が cluster であること | 同上 |
| `status: verifying`(既存のラベル) | この SHA と計画への Go | 人(メンテナ)だけ |

`family` ラベルは最初から全てを列挙するのではなく、必要になった型が出てきた時点で追加する。

この設計が受け入れられたと言える条件(受け入れ基準)は 4 つある。1 つ目は、spot か on-demand の GPU ノードでの light な実行が gate を通り、実行され、証拠を残し、タグ付きのリソースを残さないことである。2 つ目は、Go の後に commit を push すると、gate がその実行を拒否することである。3 つ目は、計画が Capacity Block を必要とする PR には `verify: queued` が付き、実行されないことである。4 つ目は、このリポジトリのブランチからの cpu-only の PR が、Go なしで検証されることである。

この仕組みの規則は `AGENTS.md` に、外部の contributor 向けの説明は `CONTRIBUTING.md` に、許可リストや上限値などの policy ファイルとラベル定義は `.github/` に置く。計画の生成とラベル同期、gate の判定、preflight、実行、残留掃除、キューの集計といったメンテナ側の道具一式は、このリポジトリ自体には含まれない。

## 3. 承認を commit と計画に束縛する

Go が有効なのは、次の 4 条件を全て満たすときだけである。

1. `status: verifying` が付いている。
2. 計画の `head_sha`、PR の現在の head、手元で checkout した SHA が一致している。
3. 最後の `labeled(status: verifying)` の時刻が、計画コメントの `updated_at` より後である。
4. main の policy ファイルで再計算した tier が、計画の tier と一致している。

この検出は timeline API、コメント API、pulls API の 3 つを読むだけで済み、GitHub Actions は不要である。push された場合は条件 2 が崩れるので、orchestrator が計画を更新してラベルを外し、メンテナの付け直しが必要になる。証拠には `head_sha`、`base_sha`、計画のハッシュ、`labeled` イベントの ID を残す。

この束縛は、陳腐化やミスを防ぐものであって、同一アカウントの AI に対する境界にはならない。1 節の問題 1 では、AI の常用ロールを軽い権限に絞ることや、AI の GitHub 上の身元を分けることも手当てとして挙げたが、メンテナは「AI の権限は分けない、Go の規則が安全装置として残る」と決めた(8 節。GitHub の身元を分けるかどうかは、決定には明記されていない)。したがって、この束縛(計画の `head_sha` が PR の head と一致すること、ラベルの時刻が計画の更新より後であること)が、AI に対する主な安全装置になる。ただし AI が計画を更新してから自分でラベルを付ければ 4 条件は全て通り、gate を回すのも AI のセッションなので、同一アカウントの AI による誤用は防げない。問題 1 は、この決定によって受け入れた残余のリスクとして扱うことになる。費用の面では、6 節で張る NodePool の `limits` と `expireAfter`、main から読む policy ファイルも歯止めとして残る。なお main から policy を読む対処(問題 15)は main が信頼できることが前提だが、10 章 5 節のとおり main のルールセットはまだ無く、AI のトークンは main に書き込める。キュー対象の購入は、メンテナ本人が MFA 付きで購入コマンドを実行する。権限を分けないという決定のもとでは、この MFA を IAM で強制するロールは無いので、購入を人が MFA 付きで行うことは運用上の手順にとどまる(推論である。強制するなら、それは採らないとした MFA 必須ロールの分離そのものになる)。コマンドは offering(型と台数、Availability Zone、開始と終了、前払い額)と、対象 PR の SHA および計画ハッシュを表示する。

<!-- FIG name="09-gate" kind="flow" shows="計画コメント・PRのhead・checkoutしたSHAの一致と、main policyでのtier再計算によるgate通過の流れ" labels="plan comment, head_sha, PR head, checked out SHA, status: verifying, main policy, tier" -->
![承認のgateの流れを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/09-gate.png)

## 4. tier の判定規則

tier はスクリプトが決定的に判定し、AI の裁量には任せない。ただし `light` の条件のうち「クォータの残り」と「その型を提供するリージョンに既存クラスタがある」は、計画にも policy にも無い実行時の状態に依存する。同じ計画でも、他の実験の使用量によって `light` と `queued` が入れ替わりうる。gate の条件 4 がどの時点の値で照合するのかは、設計にまだ書かれていない。既存クラスタは東京にしか無いので、リージョンを問わないという 8 節の決定のもとでも、`trn2.3xlarge` は今は常に `queued` になる。

| tier | 条件(全て満たす) | Go |
| --- | --- | --- |
| `cpu-only` | アクセラレータが none、scope が workload、同一リポジトリのブランチ、vCPU と時間が上限内 | 不要(fork PR では必要) |
| `light` | 容量が spot と on-demand のみ。型が許可リスト内(`g5`、`g6e`、`g7`、`g7e`、`trn2.3xlarge`)。合計 vCPU がクォータの残り以下。timebox が 180 分以下(仮)で、見積もり費用が 50 ドル以下(仮)。scope が workload で、その型を提供するリージョンに既存クラスタがある | `status: verifying` が付けば即実行 |
| `queued` | 上記以外(Capacity Block、ODCR、`p` 系、`trn2.48xlarge`、クォータ超過、クラスタ新設) | `status: verifying` に加えてバッチごとの購入承認(MFA)が必要 |

`queued` には `p` 系の spot やクラスタ新設のように購入を伴わないものも入るが、それらで「購入承認」が何を承認するのかは設計に書かれていない。

この表は `AGENTS.md` の現行の規則と 2 点で食い違う。`AGENTS.md` 6 節は、Verifier が実行するのはメンテナが `status: verifying` を付けた commit だけで、fork のコードは AWS の資格情報がある環境では実行しないと定める。一方この設計は、`cpu-only` を Go なしで実行し、fork PR も Go があれば実行する。設計を入れるには `AGENTS.md` 6 節の改訂が要り、どちらに寄せるかはメンテナが決める。

## 5. キューのまとめ方とバッチ購入

キューとバッチ購入は、返金できない高価な確保(24 時間の Capacity Block など)を、複数の `queued` の PR で兼ねて使うための仕組みである。`queued` の PR はインスタンスタイプをキーにまとめる。台数はバッチ内の最大値で確保し、少ない要求の PR はその一部を使う。リージョンとアカウントはキーに入れず、バッチを組むときに解決する。まとめるきっかけは、同じキーで 3 件、または最古の待ちが 7 日に達したときで、どちらも既定値である。あるキーがこの条件に達すると、メンテナにバッチの提案を出す(自動では買わない)。バッチは、timebox の合計が「ブロックの長さから準備時間と余裕 1 時間を引いた時間」に収まる範囲で組み、購入そのものは自動化しない。

バッチの中では scope が workload のものを先に、cluster のものを後に流す。それぞれ timebox の短い順である。1 件終わるごとに main の基準状態へ戻し、健全性を確認してから次に進む。戻せなければ、残りは未実行としてキューに残す。購入後に SHA が変わった PR はバッチから外れ、再度の Go が間に合えば戻す。

クラスタを作る場合の手順は次のとおりである。

1. offering を照会して Availability Zone を確定する。Capacity Block は単一の Availability Zone なので、これが先になる。
2. メンテナが MFA 付きで購入する。
3. ブロック開始前に、main を専用の作業ディレクトリへ clone する。Terraform の state はこのバッチ専用の S3 のキーに置く。
4. その Availability Zone のサブネットを含む CPU だけの基盤を apply し、基準の動作を確認する。
5. 開始後に PR を順に流す。
6. インスタンスは終了時刻の 30 分前から落とされるので、それより前に destroy と残留確認を終える。

## 6. 1 回の実行の手順

設計上、実行は毎回次の同じ順を踏む想定である。

1. **gate**: 3 節の 4 条件を確認し、SHA で checkout する。
2. **課金なしの preflight**: 呼び出し元のアカウントと環境プロファイル、Availability Zone ごとの提供状況とクォータの残り、AMI(SSM パラメータで解決)、Karpenter のバージョンがその容量の種類に対応しているか、見積もりが `max_cost_usd` 以下かを確認する。失敗は `inconclusive: preflight` になる。
3. **期限を先に張る**: 実行専用の NodePool に `limits` と `expireAfter`(timebox)を設定する。ODCR には `EndDate` を付ける。全リソースに `run-id` と `expires-at` のタグを付ける。ただし専用クラスタそのもの(コントロールプレーンや NAT)には期限が無く、`expires-at` タグを見て消す仕組みもクラウド側には無い(残留掃除はセッション開始時に手元から走る)。問題 10 の「専用クラスタの放置」は、この点でまだ埋まっていない。
4. **確保**: 計画に書いた容量の種類の順に試す。1 候補あたり Ready を 15 分待ち、合計 30 分(いずれも、メンテナが確定させる前の仮の値)で打ち切り、`inconclusive: no-capacity` とする。実際に使った種類を記録する。
5. **apply と実トラフィック**: `terraform plan` を保存してから apply し、実際のジョブを流す。
6. **time box**: 超過したら打ち切り、`inconclusive: timeout` とする。
7. **spot 中断**: `inconclusive: spot-interrupted` とする。再試行は 1 回までで、その後は計画にある場合だけ on-demand に移る。
8. **必ず実行する後片付け**: ワークロードの削除、NodePool の削除、タグで EC2 が 0 台であることの確認、ODCR の取消、基準状態への復帰(専用クラスタなら destroy)、タグで残留が 0 であることの確認、の順に行う。セッション開始時にも毎回、残留掃除を実行する。
9. **費用**: 実行時の見積もり(台数 × 時間 × 単価)を記録する。Capacity Block の前払いは、バッチ内の使用時間に応じて按分する。Cost Explorer への反映は遅いので、後日の照合用にとどめる。
10. **証拠**: PR に 1 コメントで残す。機械可読な部分には run-id、SHA、計画の sha256、結果と理由、リージョン、Availability Zone ID、型と台数、実際に使った容量の種類、AMI ID、各バージョン、所要時間、見積もり費用を入れる。人が読む部分にはジョブのログの抜粋、ノードの型のラベル、`nvidia-smi` または Neuron 系ツールの出力を入れる。アカウント ID、ARN、予約 ID、エンドポイントは伏せる。完全なログは非公開の場所に置く。終了時に `status: verifying` を外す。証拠の置き場はこのように PR へのコメントだが、10 章で見る `AGENTS.md` は Verifier の証拠を PR 本文の `## Test Results` に貼るとしており、両者の関係(コメントを正として本文に要約を写すのか)は設計にまだ書かれていない。

6 章と 7 章の DRA の実験は、それぞれ専用のクラスタを作って消したもので、この章の規則では「クラスタ新設」として `queued` に当たる。6 章で見た destroy の停滞は、5 節の手順 6 と、この節の手順 8 の後片付けに直接関わる。

<!-- FIG name="09-run" kind="flow" shows="gate→preflight→期限設定→容量確保→apply/traffic→timebox/spot対応→後片付け→証拠の1回の実行の流れ" labels="gate, preflight, expireAfter, capacity order, apply, pass, fail, inconclusive, evidence comment" -->
![1回の実行の手順を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/09-run.png)

## 7. 検討して退けた案

| 案 | 退けた理由 |
| --- | --- |
| GitHub Actions + 一時 runner(OIDC) | fork から資格情報に届かないように、Actions には AWS の資格情報を渡さないという前提に反する。使っている PAT には Workflows への権限も無いはずである(未確認) |
| アップストリームと同じ常設の self-hosted runner | 常設の容量が前提になるが、このリポジトリには無い。公開リポジトリの self-hosted runner は fork PR からの危険もある |
| ラベルの actor で承認を確認する | 同一アカウントなので区別できない |
| 台数やリージョンをラベルに持つ | 組み合わせが爆発し、環境が変わるたびに古くなる |
| PR 本文に YAML を書く | アップストリームのテンプレート構造から外れる。fork の作者が Go の後に本文を書き換えられる |
| PR 内に計画ファイルを置く | PR どうしが衝突し、main を汚す。PR の作者が計画を支配する |
| Issue でバッチを管理する | 第二の真実になり、ラベルや計画とずれる。アップストリームの Issue form の形にも合わない |
| 閾値に達したら自動購入する | 返金できない支出を人の確認なしで行うことになる |
| commit status で SHA に束縛する | 現在のトークンに権限が無い。権限を足せる段階になれば採用の候補になる |

## 8. メンテナが決めたこと

Issue #16 へのコメントで、次のように決まっている(orchestrator を介してメンテナから伝えられたもの)。リージョンも Availability Zone も問わず、実行時に毎回取り直す容量調査(Availability Zone ID ごとの提供状況、spot の点と価格、24 時間 Capacity Block の空き、クォータ)に基づいて、取れるところで取る。Capacity Block は原則 1 日(24 時間)で買う。`light` は `g5`、`g6e`、`g7`、`g7e`、`trn2.3xlarge` の spot と on-demand を含む。実行時間はあまり気にしないが、1 日以内に必ず落とす(on-demand を持ち続けるかどうかは後で検討する)。AI の権限は分けない(3 節で挙げた Go の束縛ルールが、そのまま安全装置として残る)。バッチにまとめるきっかけは既定値(同じ型で 3 本、または最古が 7 日)のままにする。

これらの決定とは別に、設計メモが提案する最小で動く形は、東京の既存クラスタで、`cpu-only` と `light` だけ、scope は `workload` だけに絞ったものである。ラベルは tier の 3 つと容量の 4 つだけ、計画コメント、gate の 4 条件、preflight(提供状況、クォータの残り、AMI)、実行専用 NodePool、証拠コメント、残留掃除を備える。`queued` の PR はラベルを付けるだけにし、一覧は `gh pr list --label "verify: queued"` で代用する。この提案はリージョンを東京に絞っている点で、上の「リージョンも Availability Zone も問わない」という決定より狭い。この提案が次に足すものとして挙げる MFA 必須ロールの分離または AI の GitHub App 化も、メンテナが「AI の権限は分けない」と決めたことで優先度が下がっている。残る、キューの集計ページ、専用クラスタと `EndDate` 付きの ODCR、Capacity Block のバッチ、commit status への結果記録は、順に足す対象のままである。

2026 年 11 月 17 日時点の実測では、`trn2.48xlarge` は東京で spot の点が 9、`trn2.3xlarge` は `ap-southeast-4` で 9、`g7.2xlarge` は多くのリージョンで 9、`g6e.xlarge` はどこでも 1 だった。spot の点とクォータの上限はアカウントごとに異なるため、この数値自体は参考値である。Capacity Block の照会には回数の上限があり(`CapacityBlockDescribeLimitExceeded` というエラーになる)、このエラーを「空きなし」として扱うと、実際には空きがあるのに「無い」と見えてしまう。照会結果は offered(空きあり)、none(空きなし)、unsupported(そのインスタンスタイプを扱わない)、unsupported-region(そのリージョンでは扱わない)、unknown(照会できなかった)を分けて持つ必要がある。照会は 1 回に 1 件しか返らないことが多く、8 日先までの空きを見るには日をずらして何度も聞く必要があるため、表に出ていない日にも空きがある可能性がある、という点は preflight の実装で意識しておく必要がある。

この設計のうち、リージョンごとの提供状況・クォータ・AMI を確かめる preflight の手順、ODCR や Capacity Block 向けの Karpenter NodePool と EC2NodeClass の例、「常設クラスタなしで検証する」ための手順書は、汎用性が高くアップストリームに出せる候補になりうる。commit SHA への束縛という考え方自体も候補だが、アップストリームの fork PR の扱いが未確認なため、確認してから提案する。これらは設計メモが挙げた段階のもので、11 章の候補 A から D には入っていない。設計メモは NodePool の例を GPU と Trainium の両方で書くとしているが、11 章の方針(Neuron を前提にした提案は出さない、Karpenter を足す相談は却下された)に従うなら、出すとしても形を改める必要がある。

## まとめ

この章で見た設計は、1 個のラベルに押し込んでいた「検証に合意する」を、計画コメント(正)・ラベル(表示)・承認(commit と計画への束縛)・購入(MFA 付きの人の操作)の 4 つの役割に分けたものである。GitHub の中だけでは同一アカウントの AI とメンテナを区別できないという限界を正面から認めている。AI の AWS 側の権限を分けることはメンテナの決定により採らないことになったため(GitHub 上の身元を分けるかは決定に明記されていない)、3 節で見た束縛ルール(計画と commit への紐付け)が AI に対する主な安全装置として残り、同一アカウントの AI による誤用は受け入れた残余のリスクになる。この章の時点では、リージョンの選び方、Capacity Block の期間、`light` に含める型、AI の権限を分けないこと、バッチにまとめる閾値についてはメンテナが既に決めているが、timebox や費用といった具体的な上限値は確定しておらず、実装にもまだ着手していない。
