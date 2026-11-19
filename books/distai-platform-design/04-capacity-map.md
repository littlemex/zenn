---
title: "検証に使うアクセラレータ容量の地図を作る"
---

## はじめに

DRA や device plugin の実機実験(第 6 章、第 7 章)は、GPU や Trainium を実際に spot か Capacity Block で確保できなければ始まらない。この章は、その実験に入る前に作った「容量の地図」を扱う。地図が答えるのは、どのリージョンに、どの型が、どれだけ取りやすく存在するかという問いであり、実験そのものの結果ではない。

本質から言うと、アクセラレータの提供があることと、実際に取れることは別物である。ここで使う「spot の取りやすさの点」は、EC2 の Spot placement score (1 から 10 の点で、その時点で spot の要求がどれだけ通りやすいかの見込み) のことである。たとえば trn2.3xlarge は提供されているリージョンが 3 つしかないが、そのうちの ap-southeast-4 では点が 9 で取りやすい。一方 trn2.48xlarge は東京(ap-northeast-1)でも点が 9 だった (ただし東京ではまだ起動していない)。g6e.xlarge は、点が返った 10 リージョンのすべてで点が 1 だった。点は見込みであって保証ではないので、点が低いことは spot が取れないことの証明ではないが、提供の有無だけを見て実験のリージョンを決めると、起動できずに時間を失うおそれがある、というのがこの地図を作った動機である。

この章では、AWS の API を使ってこのアカウントで有効な 20 リージョンすべてから、型の提供、spot の取りやすさ、Capacity Block の空き、vCPU の上限を集めた結果を示す。データの取得元は AWS の EC2 API と Service Quotas の API で、2026-11-17 13:46 UTC にこのアカウントで取った値である。spot の点と上限はアカウントごとに異なり、別のアカウントでは同じ値にならない。アカウント ID はこの章のどこにも載せていない。

## 1. 何を、なぜ、どう調べたか

容量の地図は、4 つの問いに答えるために作った。網羅的な統計を取るためのものではなく、どのリージョンで何を実験するかを決めるための一次資料という位置づけである。下の表の「関連する問い」の列は、その問いの答えを読むときに合わせて見る必要がある、ほかの問いの番号である。

| # | なぜ | どう測ったか | 他の選択肢 | 結果 | 関連する問い |
| --- | --- | --- | --- | --- | --- |
| 1 | 型がどこにあるかを知る | `describe-instance-type-offerings` を AZ ID ごとに全リージョンで呼ぶ | 文書やまとめの対応表を読む(更新が遅れることがある。3 節の食い違いがその例) | 調べた 28 型 × 20 リージョン = 560 組のうち、286 組の(型, リージョン)に提供があった | 2, 3 |
| 2 | spot が取れそうかを知る | `get-spot-placement-scores`(1 台、リージョン単位。11 型だけを照会) | 実際に起動して試す(費用がかかる) | 下の表にまとめた。点が 7 以上を取りやすいとみなした | 1 |
| 3 | 24 時間の Capacity Block がいつ取れるかを知る | `describe-capacity-block-offerings`(1 台、24 時間、8 日先まで) | AWS コンソールの購入画面で手で探す | 12 件の空きが見つかった。照会の回数の上限で分からなかった組が 4 件あった | 1 |
| 4 | vCPU の上限で起動が止まらないかを知る | Service Quotas で G、P、Trn、Inf の on-demand と spot の上限を調べる | 実際に起動して上限エラーを見る | 下の表にまとめた(このアカウントの値) | — |

`describe-instance-type-offerings` は型の存在だけを教え、`get-spot-placement-scores` は「はじめに」で定義した spot の取りやすさの点を返す。点は高いほど通りやすく、時間とともに変わり、求める台数によっても変わる。今回はすべて 1 台 (`TargetCapacity=1`) で照会したので、2 台以上を求める計画 (第 3 章の実験 3 と 5) にはそのまま当てはまらない。提供は AZ ID ごとに取ったが、点はリージョン単位で取ったので、同じリージョンの中のどの AZ が取りやすいかは分からない。AZ 名ではなく AZ ID を使うのは、AZ 名とその実体の対応がアカウントごとに違うためである (スクリプトは AZ 名を AZ ID に読み替えて記録している)。提供の有無と spot の点を組み合わせることで、「提供はあるが取りにくい」型と「提供が少ないが取りやすい」型を区別できる。7 以上を「取りやすい」とする線は、地図を読むための目安として置いたもので、起動の実績から決めたものではない。これまでに起動したのは点が 9 の 2 リージョンだけである。

この章がこの後の節で載せている spot の点そのものの値について、1 つ注記しておく必要がある。AWS のサービス利用条件には EC2 Capacity Manager のデータの公開を制限する条項があり([Issue #29](https://github.com/littlemex/distai-platform/issues/29)、本章 8 節)、そのレビューの中で、Spot placement score がその条項とは別の機能であり、その条項そのものには当たらないと読めるものの、Spot placement score 自身の利用条件そのものはまだ個別に確認していないという論点が見つかった。この点検はまだ終わっていない。この章に載っている点の値は、その点検の結果を待ってから消すか残すかを決めるべきものであり、今の時点では、データそのものは消さずに、この注記を添えて残している。公開してよいかどうかの最終的な判断はメンテナが行う。

## 2. すぐ使える小型・中型の候補

次の表は、spot か on-demand でその場で取ることを想定する型 (Capacity Block を前提にしない、第 9 章で言う軽い検証の候補) を、リージョンごとにまとめたものである。その場で起動できることを保証する表ではない。各セルには、提供のある AZ の数、spot の取りやすさの点(1 台を求めた場合)、直近 6 時間の spot の価格(1 時間あたり、AZ ごとに API が返した最新の 1 件のうち、一番安い AZ のもの)を入れた。AZ によって価格が大きく違うことがある点に注意が要る (たとえば sa-east-1 の trn2.3xlarge は 1 つの AZ が $1.27/h、残る 2 つの AZ が $7.40/h で、Capacity Block の空きは高い側の AZ に出ている)。ハイフンは、そのリージョンにその型の提供が無いことを表す。spot の点が書かれていないセルには 2 種類ある。1 つは、点を照会しなかった型 (g5.12xlarge、g7.12xlarge、g7e.12xlarge など) で、もう 1 つは、照会したが点が返らなかったセルである。どの型も点が返ったのは最多で 10 リージョンで、提供があるのに点が無いセルが 18 あった。返らなかった原因 (API が返す件数の上限かどうか) は確かめていないので、点の無いセルを低いと読んではいけない。価格の無いセルは、直近 6 時間の価格の履歴が返らなかったところである。Capacity Block は Trainium と P 系の型だけを照会したので、G 系と inf2 の行に CB が無いのは照会していないためである。trn2.3xlarge の行だけ、24 時間の Capacity Block(CB、表記は 3 節で説明する)の最速の空きも併記した。

| 型 | ap-northeast-1 | ap-northeast-2 | ap-northeast-3 | ap-south-1 | ap-south-2 | ap-southeast-1 | ap-southeast-2 | ap-southeast-4 | ca-central-1 | eu-central-1 | eu-north-1 | eu-south-2 | eu-west-1 | eu-west-2 | eu-west-3 | sa-east-1 | us-east-1 | us-east-2 | us-west-2 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| g5.xlarge | 2 AZ <br> spot 点 1 <br> $0.70/h | 3 AZ <br> spot 点 6 <br> $0.61/h | - | 2 AZ <br> $0.66/h | - | - | 2 AZ <br> spot 点 1 <br> $0.49/h | - | 2 AZ <br> spot 点 1 <br> $0.31/h | 3 AZ <br> spot 点 2 <br> $0.62/h | 2 AZ <br> spot 点 1 <br> $0.42/h | 3 AZ <br> $0.82/h | 3 AZ <br> $0.66/h | 2 AZ <br> spot 点 1 <br> $0.42/h | 3 AZ <br> spot 点 1 <br> $0.51/h | 2 AZ <br> $0.38/h | 5 AZ <br> spot 点 3 <br> $0.55/h | 3 AZ <br> $0.54/h | 3 AZ <br> spot 点 1 <br> $0.62/h |
| g5.12xlarge | 2 AZ <br> $3.70/h | 3 AZ <br> $3.34/h | - | 2 AZ <br> $2.34/h | - | - | 2 AZ <br> $7.37/h | - | 2 AZ <br> $2.40/h | 3 AZ <br> $4.62/h | 2 AZ <br> $1.06/h | 3 AZ <br> $2.56/h | 3 AZ <br> $2.40/h | 2 AZ <br> $3.55/h | 3 AZ <br> $2.93/h | 2 AZ <br> $7.91/h | 5 AZ <br> $2.17/h | 3 AZ <br> $2.55/h | 3 AZ <br> $1.53/h |
| g6e.xlarge | 2 AZ <br> spot 点 1 <br> $1.42/h | 2 AZ <br> $1.31/h | 2 AZ <br> spot 点 1 <br> $1.61/h | 2 AZ <br> spot 点 1 <br> $1.30/h | 2 AZ <br> spot 点 1 <br> $0.65/h | - | - | - | - | 3 AZ <br> spot 点 1 <br> $2.23/h | 2 AZ <br> spot 点 1 <br> $0.56/h | 2 AZ <br> spot 点 1 <br> $0.94/h | - | - | - | - | 4 AZ <br> spot 点 1 <br> $1.62/h | 3 AZ <br> spot 点 1 <br> $1.67/h | 4 AZ <br> spot 点 1 <br> $1.79/h |
| g6e.12xlarge | 2 AZ <br> spot 点 1 <br> $8.03/h | 2 AZ <br> spot 点 1 <br> $5.94/h | 2 AZ <br> $2.99/h | 2 AZ <br> spot 点 1 <br> $8.37/h | 2 AZ <br> spot 点 1 <br> $3.31/h | - | - | - | - | 3 AZ <br> spot 点 1 <br> $13.12/h | 2 AZ <br> spot 点 1 <br> $8.34/h | 2 AZ <br> spot 点 1 <br> $11.06/h | - | - | - | - | 4 AZ <br> spot 点 2 <br> $6.14/h | 3 AZ <br> spot 点 9 <br> $5.28/h | 4 AZ <br> spot 点 1 <br> $9.33/h |
| g7.2xlarge | - | - | - | 2 AZ <br> spot 点 9 <br> $1.03/h | - | - | 3 AZ <br> spot 点 9 <br> $1.11/h | - | - | 3 AZ <br> spot 点 9 <br> $0.73/h | 2 AZ <br> spot 点 1 <br> $0.31/h | 2 AZ <br> spot 点 9 <br> $1.02/h | - | 2 AZ <br> spot 点 8 <br> $1.09/h | - | 3 AZ <br> spot 点 9 <br> $0.78/h | 4 AZ <br> spot 点 9 <br> $0.46/h | 3 AZ <br> spot 点 9 <br> $1.16/h | 4 AZ <br> spot 点 9 <br> $0.70/h |
| g7.12xlarge | - | - | - | 2 AZ <br> $3.00/h | - | - | 3 AZ <br> $3.22/h | - | - | 3 AZ <br> $1.59/h | 2 AZ <br> $0.76/h | 2 AZ <br> $0.94/h | - | 2 AZ <br> $3.00/h | - | 3 AZ <br> $2.27/h | 4 AZ <br> $0.82/h | 3 AZ <br> $3.22/h | 4 AZ <br> $0.79/h |
| g7e.2xlarge | 2 AZ <br> spot 点 1 <br> $2.62/h | 2 AZ <br> spot 点 1 <br> $3.31/h | - | 2 AZ <br> spot 点 9 <br> $1.89/h | - | - | - | - | 1 AZ | 2 AZ <br> spot 点 2 <br> $3.23/h | 2 AZ <br> spot 点 1 <br> $1.62/h | 2 AZ <br> spot 点 1 <br> $0.58/h | - | 2 AZ <br> spot 点 1 <br> $1.45/h | - | - | 3 AZ <br> spot 点 1 <br> $1.76/h | 3 AZ <br> spot 点 1 <br> $1.81/h | 4 AZ <br> spot 点 2 <br> $0.66/h |
| g7e.12xlarge | 2 AZ <br> $5.41/h | 2 AZ <br> $9.17/h | - | 2 AZ <br> $13.53/h | - | - | - | - | 1 AZ | 2 AZ <br> $7.76/h | 2 AZ <br> $1.78/h | 2 AZ <br> $2.70/h | - | 2 AZ <br> $2.81/h | - | - | 3 AZ <br> $4.60/h | 3 AZ <br> $3.73/h | 4 AZ <br> $4.90/h |
| trn2.3xlarge | - | - | - | - | 1 AZ <br> spot 点 1 | - | - | 1 AZ <br> spot 点 9 <br> $2.56/h <br> CB 11-18 $54 | - | - | - | - | - | - | - | 3 AZ <br> spot 点 1 <br> $1.27/h <br> CB 11-18 $54 | - | - | - |
| inf2.xlarge | 2 AZ <br> spot 点 1 <br> $0.90/h | 3 AZ <br> $0.15/h | - | 2 AZ <br> spot 点 9 <br> $0.31/h | - | 2 AZ <br> spot 点 9 <br> $0.17/h | 2 AZ <br> spot 点 1 <br> $0.33/h | - | - | 2 AZ <br> $0.32/h | 2 AZ <br> $0.08/h | - | 3 AZ <br> spot 点 9 <br> $0.23/h | 1 AZ <br> spot 点 2 <br> $0.16/h | 2 AZ <br> spot 点 9 <br> $0.13/h | 1 AZ <br> spot 点 1 <br> $0.13/h | 4 AZ <br> $0.11/h | 3 AZ <br> spot 点 1 <br> $0.08/h | 4 AZ <br> spot 点 9 <br> $0.13/h |

この表から言えるのは、第 6 章と第 7 章で実際に使ったリージョンが、点の高いリージョンから選んだことと整合する、というところまでである。g7.2xlarge は提供のある 10 リージョンのうち、eu-north-1 (点 1) を除く 9 リージョンで点が 8 か 9 と高い。ただし東京、ソウル、大阪には提供が無い。第 6 章の実験 (E0 から E3) は、このうち eu-south-2 を使っている。eu-south-2 の価格 ($1.02/h) は us-east-1 ($0.46/h) などより高く、なぜ eu-south-2 を選んだかはこの表だけでは説明できない。一方 trn2.3xlarge は提供自体が ap-south-2、ap-southeast-4、sa-east-1 の 3 リージョンに限られ、そのうち点が 9 と高いのは ap-southeast-4 だけである。設計メモも、Trainium の実験に ap-southeast-4 を選んだ根拠としてこの点 9 を挙げている。第 7 章の実験(E4)はオーストラリアのメルボルン(ap-southeast-4)の trn2.3xlarge を使っている。

## 3. Capacity Block が主の大きな型

p4d、p5 系、p6 系、trn1.32xlarge、trn2.48xlarge のような大きな構成では、この基盤は spot に加えて 24 時間単位の Capacity Block(CB)を主な取得手段として想定している。ただし今回の照会で空きが見つかったのは 12 件だけで、p4d、p5e、p5en には 8 日先まで 1 件も無かった。一方で spot の点が 9 のリージョンはいくつもあり、第 3 章の実験 5 も trn2.48xlarge を spot から試す計画である。セルの「CB」は、8 日先までの照会で見つかった最も早い 24 時間枠の開始日(月-日、UTC)と前払いの金額を表す。「CB 不明」は、型の提供はあるが照会の回数の上限で Capacity Block の有無を確認できなかったことを意味する。スクリプトは照会の結果を、空きあり (offered)、空きなし (none)、その型では照会できない (unsupported)、そのリージョンでは照会自体ができない (unsupported-region)、分からない (unknown) の 5 つに分けて記録しており、内訳は 12、42、14、8、4 件だった。下の表では、空きなし、その型では照会できない、そのリージョンでは照会できない、の 3 つをどれも空欄にしているので、空欄は「空きが無い」とは限らない。たとえば東京の trn2.48xlarge は「その型では照会できない」であり、東京ではこの型の Capacity Block に逃げられない。ca-central-1、eu-central-1、eu-west-1 は、どの型でも「そのリージョンでは照会できない」だった。これらの分類は、API が返したエラーの種類 (`InvalidParameterValue` と `InvalidAction`) から機械的に決めたものである。

| 型 | ap-northeast-1 | ap-northeast-2 | ap-south-1 | ap-south-2 | ap-southeast-2 | ap-southeast-4 | ca-central-1 | eu-central-1 | eu-north-1 | eu-south-2 | eu-west-1 | eu-west-2 | eu-west-3 | sa-east-1 | us-east-1 | us-east-2 | us-west-1 | us-west-2 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| p4d.24xlarge | 2 AZ <br> spot 点 5 <br> $17.28/h | 2 AZ <br> spot 点 1 <br> $8.86/h | 3 AZ <br> $26.34/h | - | 2 AZ <br> $27.73/h | - | 2 AZ <br> spot 点 1 <br> $6.60/h | 3 AZ <br> $11.75/h | 1 AZ <br> spot 点 1 <br> $4.30/h | - | 1 AZ <br> spot 点 1 <br> $17.46/h | 2 AZ <br> spot 点 1 <br> $8.02/h | - | 2 AZ <br> spot 点 1 <br> $29.16/h | 4 AZ <br> spot 点 9 <br> $13.93/h | 3 AZ <br> spot 点 8 <br> $4.84/h | - | 4 AZ <br> spot 点 9 <br> $10.43/h |
| p5.48xlarge | 2 AZ <br> spot 点 2 <br> $29.13/h | 1 AZ <br> spot 点 3 <br> $48.48/h | 3 AZ <br> spot 点 1 <br> $22.50/h | - | 3 AZ <br> spot 点 9 <br> $26.33/h | - | 1 AZ <br> $42.44/h | 2 AZ <br> spot 点 2 <br> $20.27/h | 3 AZ <br> $7.88/h <br> CB 11-19 $1146 | - | - | 3 AZ <br> spot 点 3 <br> $18.40/h | - | 1 AZ <br> spot 点 1 <br> $18.39/h | 6 AZ <br> spot 点 3 <br> $23.93/h | 3 AZ <br> spot 点 9 <br> $32.52/h | 2 AZ <br> $17.36/h | 4 AZ <br> spot 点 9 <br> $28.49/h |
| p5e.48xlarge | - | - | - | - | 1 AZ <br> $82.28/h | - | - | - | 1 AZ <br> $14.30/h | - | - | 1 AZ <br> $26.95/h | - | 2 AZ <br> $29.41/h | - | 3 AZ <br> $34.44/h | - | 1 AZ <br> $35.99/h |
| p5en.48xlarge | 1 AZ <br> spot 点 9 <br> $33.46/h | 1 AZ <br> spot 点 1 <br> $26.56/h | 2 AZ <br> spot 点 1 <br> $24.96/h | - | - | - | 1 AZ <br> spot 点 1 <br> $21.26/h | - | 2 AZ <br> spot 点 2 <br> $6.77/h | 2 AZ <br> spot 点 2 <br> $20.83/h | - | - | - | - | 2 AZ <br> spot 点 9 <br> $40.76/h | 3 AZ <br> spot 点 9 <br> $32.97/h | 1 AZ <br> spot 点 1 <br> $32.59/h | 3 AZ <br> spot 点 9 <br> $38.41/h |
| p6-b200.48xlarge | - | - | 2 AZ <br> $52.16/h <br> CB 11-19 $2728 | 1 AZ <br> $16.07/h <br> CB 不明 | - | - | - | - | 1 AZ <br> $16.07/h | - | - | - | 1 AZ <br> $23.98/h <br> CB 11-20 $2728 | - | 1 AZ <br> $66.65/h | 3 AZ <br> $44.66/h | - | 3 AZ <br> $41.50/h <br> CB 11-19 $2728 |
| p6-b300.48xlarge | - | 1 AZ <br> $46.27/h | - | 2 AZ <br> $20.08/h <br> CB 不明 | - | - | - | - | - | - | - | 1 AZ <br> $55.98/h | - | 2 AZ <br> $94.22/h | 2 AZ <br> $73.27/h | - | - | 2 AZ <br> $64.09/h <br> CB 11-19 $3100 |
| trn1.32xlarge | - | - | 1 AZ <br> CB 11-18 $229 | - | 2 AZ <br> CB 11-18 $229 | 1 AZ <br> CB 不明 | - | - | - | - | - | - | - | - | 3 AZ <br> $2.15/h <br> CB 11-19 $229 | 1 AZ <br> $2.15/h | - | 2 AZ <br> $2.94/h <br> CB 11-19 $229 |
| trn2.48xlarge | 3 AZ <br> spot 点 9 <br> $21.90/h | - | 3 AZ <br> spot 点 7 <br> $28.34/h | 1 AZ <br> spot 点 9 <br> $10.32/h <br> CB 不明 | - | - | 3 AZ <br> spot 点 1 <br> $24.35/h | - | - | - | - | - | - | - | - | 3 AZ <br> spot 点 9 <br> $8.60/h <br> CB 11-18 $858 | - | 4 AZ <br> spot 点 5 <br> $20.04/h |

東京(ap-northeast-1)の trn2.48xlarge は spot の点が 9 で、3 つの AZ に提供がある。これは、参考にした既存のまとめ資料([Zenn のスクラップ記事](https://zenn.dev/tosshi/scraps/bde219d4dce3f3))が「東京に trn2 は無い」としていたのと食い違う。この章では、情報源が食い違う場合は後で測った値を使うという方針に従い、2026-11-17 13:46 UTC の API の応答を正とした。食い違いの理由は確かめていない。資料が古くなっているか、調べた時点でまだ提供が始まっていなかった可能性のほか、資料が trn2.3xlarge (東京には実際に提供が無い) を指していた可能性もある。

## 4. 24 時間 Capacity Block の空き状況

8 日先までの照会で見つかった、24 時間の Capacity Block の空きは次の 12 件である。

| リージョン | 型 | AZ ID | 開始 | 前払い(24 時間、1 台) |
| --- | --- | --- | --- | --- |
| ap-south-1 | p6-b200.48xlarge | aps1-az2 | 2026-11-19 11:30 UTC | $2,728 |
| ap-south-1 | trn1.32xlarge | aps1-az2 | 2026-11-18 11:30 UTC | $229 |
| ap-southeast-2 | trn1.32xlarge | apse2-az2 | 2026-11-18 11:30 UTC | $229 |
| ap-southeast-4 | trn2.3xlarge | apse4-az3 | 2026-11-18 11:30 UTC | $54 |
| eu-north-1 | p5.48xlarge | eun1-az3 | 2026-11-19 11:30 UTC | $1,146 |
| eu-west-3 | p6-b200.48xlarge | euw3-az1 | 2026-11-20 11:30 UTC | $2,728 |
| sa-east-1 | trn2.3xlarge | sae1-az2 | 2026-11-18 11:30 UTC | $54 |
| us-east-1 | trn1.32xlarge | use1-az4 | 2026-11-19 11:30 UTC | $229 |
| us-east-2 | trn2.48xlarge | use2-az1 | 2026-11-18 11:30 UTC | $858 |
| us-west-2 | p6-b200.48xlarge | usw2-az2 | 2026-11-19 11:30 UTC | $2,728 |
| us-west-2 | p6-b300.48xlarge | usw2-az2 | 2026-11-19 11:30 UTC | $3,100 |
| us-west-2 | trn1.32xlarge | usw2-az4 | 2026-11-19 11:30 UTC | $229 |

この照会には 2 つの注意が要る。1 つは、`describe-capacity-block-offerings` が 1 回の呼び出しにつき最も条件に合う 1 件しか返さないことが多く、表に無い日にも実際には空きがある場合があるという点である。もう 1 つは、照会そのものに回数の上限があり (エラーは `CapacityBlockDescribeLimitExceeded`。スクリプトは間隔を広げながら 5 回まで再試行し、4 リージョンずつ並列に照会した)、上限に達して答えが返らなかった組(ap-south-2 の p6-b200.48xlarge、ap-south-2 の p6-b300.48xlarge、ap-south-2 の trn2.48xlarge、ap-southeast-4 の trn1.32xlarge)が 4 件あったという点である。これらは「空きが無い」のではなく「分からない」であり、2 つを区別しないと、実際には取れる型を候補から誤って外すことになる。

## 5. vCPU の上限

この節は、1 節の 4 つ目の問い、つまり vCPU の上限で起動が止まらないかに答える。Service Quotas が返す上限の単位は vCPU である。G の列は、G 系と VT 系で共有される上限である。たとえば g6e.12xlarge は 48 vCPU を使うので、G 系の on-demand の上限が 64 vCPU なら、同時に起動できるのは 1 台までになる。上限はアカウントごとに異なり、申請で引き上げられる。

| リージョン | G on-demand | G spot | P on-demand | P spot | Trn on-demand | Trn spot | Inf on-demand | Inf spot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ap-northeast-1 | 64 | 64 | 384 | 768 | 256 | 256 | 128 | 128 |
| ap-northeast-2 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| ap-northeast-3 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| ap-south-1 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| ap-south-2 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| ap-southeast-1 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| ap-southeast-2 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| ap-southeast-4 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| ca-central-1 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| eu-central-1 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| eu-north-1 | 64 | 64 | 384 | 768 | 256 | 256 | 128 | 128 |
| eu-south-2 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| eu-west-1 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| eu-west-2 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| eu-west-3 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| sa-east-1 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| us-east-1 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| us-east-2 | 64 | 64 | 384 | 768 | 256 | 256 | 128 | 128 |
| us-west-1 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |
| us-west-2 | 768 | 64 | 768 | 768 | 256 | 256 | 128 | 128 |

ほとんどのリージョンは G on-demand が 768 vCPU だが、ap-northeast-1、eu-north-1、us-east-2 の 3 リージョンだけは 64 vCPU(g6e.12xlarge なら 1 台分)にとどまり、P on-demand も 384 vCPU と他のリージョンの半分である。ただし G spot の上限は 20 リージョンすべてで 64 vCPU、Trn の上限は on-demand・spot ともに全リージョンで 256 vCPU であり、第 6 章・第 7 章の実験はいずれも spot を使っているため、この表だけでは実験のリージョン選定を説明できない。第 6 章の実験は `g7.2xlarge` を 2 台、第 7 章の実験は `trn2.3xlarge` の起動を 3 回という小さな規模だったので、この上限の内に収まっていた。一方、第 3 章の実験 5 が計画する `trn2.48xlarge` 2 台は、AWS の公開仕様 (1 台 192 vCPU) で計算すると 384 vCPU になり、Trn の上限 256 vCPU を超える。この上限は申請で引き上げる必要があると考えられる。

## 6. 分かっていないこと

この地図には、API だけでは埋まらない空白と、今回の照会で埋めきれなかった空白が残っている。

on-demand がいま実際に取れるかどうかを確かめる API は無い。提供と vCPU の上限から「取れる可能性がある」までは言えるが、確定させるには実際に起動するしかない。

spot の取りやすさの点、価格、Capacity Block の空きは時間とともに変わる値である。実際の検証に入る前には、この地図を作り直すスクリプトをもう一度流すべきだと考えている。なお、提供と Capacity Block の価格はアカウントによらないが、spot の点と上限はアカウントごとに違う。この地図は 2026-11-17 に作り、第 6 章と第 7 章の実験は翌日の 2026-11-18 に行った。その間に地図を作り直した記録は無い。

今回の照会で埋めきれなかったところもある。Capacity Block の照会が回数の上限で分からなかった 4 件、spot の点が返らなかった 18 セル、点や Capacity Block を照会しなかった型、そして 2 台以上を同時に求めたときの取りやすさである。

このほか、既存の資料との食い違いが 1 件見つかっている。3 節で触れたとおり、東京の trn2 に関する記述である。これは API で答えが出たが、食い違いの理由は分かっていない。

<!-- FIG name="04-score-vs-offering" kind="relation" shows="type offered in a region vs. actually easy to get on spot; trn2.3xlarge has few offerings but a high score in one region, g6e.xlarge is offered widely but scores low everywhere" labels="offer count, spot placement score, trn2.3xlarge, g6e.xlarge, g7.2xlarge, score 1-10" -->
![型の提供数と spot の取りやすさの点は別の軸であることを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/04-score-vs-offering.png)

## 7. 起動の記録(launch ledger)という発想

地図はある瞬間の静的な写真である。これに対して、第 6 章と第 7 章で用意した NodePool を使って Karpenter に実際にインスタンスを起動させた結果は、地図とは別の動的な記録として残している。これを launch ledger と呼んでいる。いまの launch ledger は実験の側で使った小さなスクリプトと 1 つの記録ファイルであり、基盤の機能としての記録の仕組みは、3 章の設計から導かれる実装 Issue の 1 つ (予約の手順と起動実績の台帳) として計画されている段階である。

launch ledger は、クラスタ上の NodeClaim の状態を読み、起動に成功したか失敗したかを、起動時刻、リージョン、AZ ID、型、capacity type、結果、理由とともに 1 行の JSON で追記する仕組みである。いまのスクリプトは capacity type をインスタンスのライフサイクルから spot か on-demand として記録するので、reserved (ODCR や Capacity Block) から起動したノードを区別するには、基盤側の実装で項目を足す必要がある。ここで言う成功は、NodeClaim が `Launched` になった、つまり容量が取れたことであり、ノードが使える状態になったことではない。たとえば第 6 章の E2 では、起動には成功したが NodeClaim が初期化されないままのノードがあった。第 6 章の実験 (2026-11-18) では、g7.2xlarge の spot が eu-south-2 の eus2-az1 と eus2-az2 で 3 回ずつ、合計 6 回すべて成功したことが、この記録として残っている (約 1 時間 43 分の実験)。第 7 章の実験 (同日) では、trn2.3xlarge の spot が ap-southeast-4 の apse4-az3(このリージョンでこの型を提供する唯一の AZ)で 3 回すべて成功したことが同じ形式で残っている (約 1 時間 15 分の実験)。つまり、いまの実績は 1 日のうちの約 3 時間、点が 9 の 2 リージョンでの 9 回だけであり、失敗の記録はまだ 1 件も無い。

地図と launch ledger の受け持ちは異なる。地図は、描いた瞬間の提供状況と取りやすさの点を広く横断的に示し、次にどこで何を試すかを選ぶための事前の資料になる。launch ledger は、実際に起動を試みた結果だけを、同じ型・同じ AZ・同じ capacity type で積み重ね、地図の点がどれだけ当たっていたかを後から検証するための実績の記録になる。ただし、いまのように点の高いところでだけ起動していては点の当たり外れは測れないし、点はリージョン単位、記録は AZ 単位で、粒度も揃っていない。地図は、20 リージョンに対して複数の AWS API を呼べば作れる(ただし Capacity Block の照会には回数の上限があり、本章 4 節で見たとおり、上限のエラーを空の答えとして扱うと「空きなし」に見えてしまう)のに対し、launch ledger は実験を重ねるたびに少しずつ増えていく。どちらか片方だけでは、次にどこで実験するかを決めるための材料として不十分である。

## 8. EC2 Capacity Manager は使わない

Amazon EC2 Capacity Manager は、on-demand・spot・Capacity Reservation にまたがる容量の利用状況を 1 つの場所にまとめて監視・分析するための AWS の機能である。ダッシュボード、タグでまとめた利用状況の表示、S3 へのデータ書き出しは追加のプラン無しで使えるが、実時間の調達しやすさの判定(supportability check)と、調達の成否をまとめた指標(fulfillment metrics)の 2 つは、AWS Support の Unified Operations プランを契約した顧客専用の機能として公開されている。この基盤([Issue #29](https://github.com/littlemex/distai-platform/issues/29))は、この機能を一切使わないという決定をしている。

理由は、この基盤の設計判断ではなく、AWS のサービス利用条件そのものにある。EC2 の利用条件 5.9 項は、EC2 Capacity Manager を通じて得られるデータ(「EC2 Capacity Manager Data」)の使い方を、利用者自身の社内の容量計画と作業管理の目的に限っており、第三者への開示や、同種・競合する製品・サービスの開発・改善への転用を禁じている。この「データ」の定義は、調達しやすさの点や確信度だけでなく、見通し、余力の指標、しきい値、そして「EC2 Capacity Manager を通じて利用者に提供されるその他の容量に関する情報」を広く含む書き方になっている。`littlemex/distai-platform` は公開リポジトリであり、Issue や PR、コミットされたファイル、CI のログに書いたものは誰でも読める。これを保守的に読めば、そこに書くことは第三者への開示に当たり、この基盤がその値を使う機能を作ること自体が、条項が禁じる「類似・競合する製品・サービスの開発」に近づいてしまう。

| 選択肢 | 何か | なぜ | 決定 |
| --- | --- | --- | --- |
| EC2 Capacity Manager を使う機能をこの基盤に作る(ダッシュボードの値を読む、調達しやすさの判定を自動で使う、など) | この章の地図や launch ledger を補う新しいデータ源になる | 利用条件 5.9 項の対象になり、公開リポジトリで扱うこと自体がリスクになる | 不採用 |
| この基盤のコードパスには一切組み込まず、メンテナ個人が AWS コンソールや CLI を自分のアカウントで直接見ることは妨げない、という注記だけを置く | この基盤のツール・リポジトリ・公開物を一切経由しない、個人の社内の容量計画そのもの | 利用条件が許す「利用者自身の社内の容量計画」の使い方に最も近く、公開・自動化・第三者への中継のいずれも発生しない | **採用** |

採用した案のもとでは、この基盤はこの機能を呼ばず、保存せず、公開もせず、自動化もしない。メンテナが自分の判断で AWS コンソールや素の AWS CLI コマンドを使い、自分のアカウントの容量計画のために一度だけ確認することまでは妨げないが、それはこの基盤のツール・スクリプト・パイプラインが行うことではなく、チャットツールや AI アシスタントを介した中継でもない。この章の地図と launch ledger(7 節)は、このデータには依存しない独立した情報源のままである。

関連して、既存の公開リポジトリ `littlemex/distributed-ai` の調査用ディレクトリ(`investigations/2026-11-05-ec2-capacity-supportability-report/`)についても、[Issue #29](https://github.com/littlemex/distai-platform/issues/29)自身が、ある時点のコミットを対象に確認した内容を記録している。それによれば、実際に測った点は 1 件も記録されておらず、README に書かれた例もすべて AWS の文書の例をそのまま転記したものであり、テストも実際の呼び出しを一切しない作りのクライアントで動いている。このディレクトリをどう扱うか(残す、消す、非公開にする)は、この利用条件との整合性を踏まえたメンテナ自身の判断であり、この基盤の設計はそのディレクトリの有無に依存しない。

## まとめ

容量の地図は、型の提供、spot の取りやすさの点、Capacity Block の空き、vCPU の上限という 4 つの問いに、AWS の API を使って 2026-11-17 の時点で答えたものであり、第 6 章と第 7 章が点の高いリージョンから実験の場所を選んだことと整合する一次資料になっている。点が返らなかったセルや、照会できなかった Capacity Block の組のように、空欄が「無い」ではなく「分からない」を意味するところも残る。on-demand の実際の取得可能性のように API だけでは分からない空白は残るが、そこは実際に起動して確かめるしかなく、その結果は launch ledger という別の記録に積み重ねていく。Spot placement score 自身の利用条件の点検はまだ終わっておらず、この章が載せている点の値をどうするかはメンテナの判断を待っている。もう一つ、容量の状況をまとめて見るための AWS の機能である EC2 Capacity Manager は、利用条件 5.9 項を保守的に読んだ結果、この基盤のどのコードパスにも組み込んでいない。
