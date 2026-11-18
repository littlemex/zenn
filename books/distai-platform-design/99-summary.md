---
title: "設計判断のまとめと残る問い"
---

## はじめに

ここまでの章で、distai-platform の設計判断を、立ち上げの経緯から、容量の混ぜ方、複数チームでのセル共有、device plugin と DRA の扱い、基盤層の実装、実機検証の運用、開発プロセス、アップストリームへの貢献まで、個別に見てきた。この章では、それらの決定を 1 つの表に並べ直し、どの代替案を比べてどちらを選んだか、理由は何か、根拠はどの章にあるかを一覧にする。続けて、まだ決まっておらず、今後の実験で決めるとされている問いを並べる。

この章の決定の表は、各章の執筆者の文章を経由せず、explainer のページと Issue 本文(アップストリームの `awsome-distributed-ai` の該当箇所を含む)を直接の根拠にしている。数値や識別子は、該当するソースにある表記のまま載せている。

## 1. 決定事項の一覧

<!-- FIG name="99-timeline" kind="relation" shows="Issue番号と章番号の対応、PR#14の中止からPR#21・Issue#16/#19までの決定の連鎖" labels="Issue #2, PR #14, Issue #17, Issue #19, Issue #20, PR #21, Issue #16, Issue #1, PR #15" -->
![決定の連鎖を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/99-timeline.png)

これまでの章の決定を、選んだ案、比較した代替案、理由、根拠章とともに 1 つの表に並べる。図は、Issue と PR の番号でたどった決定の連鎖である。

| # | 決定 | 選んだ案 | 比較した代替案 | 理由 | 根拠章 |
| --- | --- | --- | --- | --- | --- |
| 1 | 実験基盤 `littlemex/distributed-ai` の `infra/` の扱い | いったん無変更で写す PR([#14](https://github.com/littlemex/distai-platform/pull/14))を作ったあと、取り込まず閉じ、`distributed-ai@e092c3f` を参照にして一から作り直す([#2](https://github.com/littlemex/distai-platform/issues/2) は not planned、設計は [#17](https://github.com/littlemex/distai-platform/issues/17)) | 実験基盤をそのまま取り込んで直す案 | 長く保守する基盤と、日付付きの 1 回限りの実験が同居したリポジトリを基準にすると、作り直しの最初の決定(容量の 4 分割、state の分離)を後付けで入れることになる | 00 |
| 2 | PR #14 自体の作り方(取り込みを検討していた段階の決定) | 無変更で写し、直す作業は別の Issue(#5〜#13)と PR に分ける。ファイルだけ写し、履歴(`git filter-repo`)は持ってこない。実験は元のリポジトリに残す | 写すのと直すのを 1 つの PR にする/履歴ごと移す/実験も一緒に移す | 移したせいの差と直したせいの差を見分けられなくする。公開した履歴は書き換えられない。寿命の違うものを同居させる元の問題が残る | 00 |
| 3 | Neuron(Trainium)を GPU とどう並べるか | 共通の部分(ノードプール、Capacity Block、スケジューリング、ストレージ、identity、来歴、probe の枠)は 1 つの仕組みに、違う部分(device plugin、ドライバとランタイム、性能指標、集団通信、プロファイラ)は別実装で入口だけそろえる。GPU の世代差も同じ扱いにする。このリポジトリだけで通じる抽象化は作らない | このリポジトリだけのアクセラレータ抽象化層/GPU と Neuron で完全に別々の仕組み | 独自の抽象化は持ち出せずアップストリームにも出せない。完全な分離は共通の仕組みを 2 回書いて少しずつずれる | 01 |
| 4 | 容量を混ぜる悩みの切り方 | 買う、セルを選ぶ、ジョブを入れる、実際に起動する、の 4 つの決定に分け、それぞれ向いている道具に任せる(「セルを選ぶ」と「ジョブを入れる」は時間の単位としては同じ「ジョブを出すとき」で、2 つを分けるのは範囲であり、クラスタの間かクラスタの中かという違いである)。Terraform はクラスタの入れ物だけ、予約は Kueue の枠、spot と on-demand は Karpenter の探索、配布は Argo CD | (a) 今の Terraform 変数を延ばす/(e) kro で容量 API を合成/(f) 独自 CRD とコントローラ | (a) は複雑さが 1 変数に溜まり続ける。(e) は複雑さが CEL に移るだけで独自抽象化になる。(f) は保守と権限の負担が大きく、独自抽象化の禁止に反する | 02 |
| 5 | 容量を決める層の核(推奨構成) | (b) Karpenter を素直に使い Argo CD で配る、と (c) Kueue を中心に枠・順番・借用・preemption を扱う、を核にし、(d) SkyPilot を薄い外側に置く | (b)(c)(d) をそれぞれ単独で採用する案、kro、独自 CRD | (b) は予約へのネイティブ対応があるが入場順の制御が無い。(c) は枠と preemption を表現できるが spot/on-demand は入場できても容量が無いことがある。3 つを組むことで単独の弱点を補う | 02 |
| 6 | NodePool と ResourceFlavor の結び方 | 1 NodePool = 1 ResourceFlavor を規約にする。Karpenter が全ノードに付ける `karpenter.sh/nodepool` ラベルを ResourceFlavor に書いて名前で 1 対 1 に結ぶ。プールは `-rsv` / `-od` / `-flex` の 3 種類に切る | この結び付けを人が手作業で管理する(distributed-ai の今の形) | `-rsv` の Kueue の枠は予約の実数なので入場したら容量がある。`-od` と `-flex` の枠は予算の上限で容量の保証ではなく、混ぜると数え方がずれる | 03 |
| 7 | 容量の抽象化に独自 CRD を作るか | 作らない。既存の API(NodePool、EC2NodeClass、ResourceFlavor、ClusterQueue)をそのまま使う。予約を買ったら Kueue の枠を書き換える PR を 1 本出す(手動の同期) | 容量の状態を読んで Kueue の枠を自動で書くコントローラ | Capacity Block は 1 回の購入が $54〜$3,100 と高額なので、購入ごとに PR 1 本という手続きの重さは妥当。自動化が要るなら独立した OSS として切り出す | 03 |
| 8 | 容量判断の一番危ない前提 | 「Kueue が入場させた = 容量がある」は予約プールでしか成り立たないと認め、5 つの安い実験(数ドル〜$1,716)で確かめることにする(未実施) | 検証せず推測のまま採用する | Kueue の容量を先に確かめる仕組み(ProvisioningRequest)は Karpenter に対応していない。spot/on-demand で起動に失敗してもジョブが枠を握ったまま待つ可能性がある | 03、99 |
| 9 | 容量の実際の取りやすさをどう調べるか | AWS API(`describe-instance-type-offerings`、`get-spot-placement-scores`、`describe-capacity-block-offerings`、Service Quotas)を全リージョンで集め、情報源が食い違う場合は後で測った値を使う | 公開されている地域別まとめの記事を信じる | 公開の参考資料(Zenn のスクラップ)は東京に `trn2` が無いとしていたが、2026 年 11 月 17 日 13:46 UTC の API では東京に `trn2.48xlarge` の提供があり spot の点も 9 だった。食い違いの理由は確かめておらず、資料が `trn2.3xlarge`(東京には実際に提供が無い)を指していた可能性も残る | 04 |
| 10 | device plugin と DRA を抽象化する独自 CRD を作るか | 作らない。Kubernetes 本体の KEP-5004(DRA の extended resource、v1.37 で stable、EKS で使える)が Pod の書き方の差をすでに吸収している | ベンダー横断の方式宣言 CRD とコントローラ | 各ベンダーの chart を追い続けるメタオペレータになる。GPU Operator 自体が 2 設定を排他にしており上にかぶせてもノード単位の混在を解決できない。一次情報に需要の根拠が無い | 05 |
| 11 | 広告する方式(device plugin か DRA か)をどこで宣言するか | 「ノードプール × デバイスのクラス」の属性とし、広告側はそのラベルで自分のノードを選ぶ opt-in にする。E0 の実機確認で、これは任意ではなく必須と判明した | 広告側が既定で全 GPU/Neuron ノードに乗る opt-out のまま運用する | 両方の chart が既定で同じ「GPU があるノード」の目印(`nvidia.com/gpu.present=true` など)を選ぶため、何もしないと全ノードで重なる | 05、06 |
| 12 | Pod の書き方を何層にするか | 2 層にする。Layer 1(今までどおりの拡張リソースの要求)を既定にし、DRA でしか表せない性質が要るときだけ Layer 2(ResourceClaimTemplate)を使う。Kueue の枠の名前は方式によらず 1 つ、ResourceFlavor には方式ラベルを持たせない | 全部を Layer 2(DRA)に統一する | 既存の Pod の書き方を変えずに済ませる方が移行の継ぎ目になる。ResourceFlavor に方式ラベルを入れると移行中に新旧どちらのプールにも乗れなくなる | 05 |
| 13 | クラスごとの既定の方式 | NVIDIA 小型は device plugin + 動的プール + Layer 1 で確定した(E1〜E3。確かめたのは `g7.2xlarge` だけである。`g6e` は起動しておらず、同じ既定値を当てはめる推定にとどまる)。NVIDIA+EFA 大型は DRA + static または予約 + Layer 2 にする。Neuron 多デバイスは DRA を第一候補にする。Neuron 小型(`trn2.3xlarge`)は device plugin + 動的プール + Layer 1 で確定した(E4) | Neuron 小型も GPU 大型と同じく DRA を既定にする | E4 で、Neuron の DRA は Karpenter が 0 台から動的に起こす形では Layer 1 も Layer 2 も使えないと確認された(Karpenter に Neuron 向けの DRA gate が無い) | 05、06、07 |
| 14 | 動いているクラスタでの DRA への移行単位 | ノードプール単位の blue/green で移行する。同じノードの中では切り替えない | 同じノードプールの中身を差し替える | 既存の Pod が自然に終わるのを待つ必要があり、同じノードで方式を切り替える手段がそもそも無い | 05 |
| 15 | GPU Operator を使うか | 使わない。個別の chart(NVIDIA の device plugin chart、DRA ドライバ chart)と AMI のドライバだけで、GPU 1 枚の `g7.2xlarge` で割り当てが動くところまでは足りることを E1 で確認した(ドライバの更新、監視、time-slicing、大型の `p5` 系の構成は比べていない) | GPU Operator で一括管理する | GPU Operator は `ClusterPolicy` と `GPUCluster` を同時に持てず、5 章 1 節の問題 6(排他)を抱える | 05、06 |
| 16 | 二重広告(同じデバイスが 2 つの Pod に渡る)をどう検出するか | 読むだけの検査ツールを作る方針とし、規則を「DRA のドライバが動くノードで、対応する拡張リソースの allocatable が 0 より大きい」に統一する。GPU も Neuron も同じ規則で検出できることを E1 と E4+ の両方で確認した | ノードの GPU 数(allocatable)だけで判定する/GPU Operator の設定の有無で判定する | 二重に動いていても allocatable の数だけでは見分けがつかない場合がある。GPU Operator の設定確認は eksctl や別名の Helm には効かない | 05、06、07、11 |
| 17 | Kueue での方式をまたいだ数え方 | `deviceClassMappings` で Layer 1 と Layer 2 を同じリソース名で数える。ただし Layer 2 のジョブは DRA 専用の flavor を持つキューに流す規則を追加する | 層ごとに別のリソース名で数える | E3 で、`deviceClassMappings` 自体は正しく数えるが、Layer 2 のジョブが空いていた device plugin の flavor に入って claim を割り当てられず止まることが分かった | 05、06 |
| 18 | 基盤層(Terraform)がどこまでを作るか | 1 セル 1 state の `infra/foundation` を作り、VPC・EKS・システムノード・Karpenter の前提・削除ガード・任意の Argo CD capability だけに絞る。NodePool、Kueue、device plugin、容量の予約は作らない | Terraform に NodePool や Kueue の設定も持たせる 1 本の state | 予約を 1 つ買うたびに巨大な state の plan を見ることになり、Argo CD も扱うオブジェクトを state に重複して持つことになる | 08 |
| 19 | 公開 API エンドポイントの絞り方 | `endpoint_public_access_cidrs` を必須の変数にし、空リスト・`0.0.0.0/0`・同じ広さを作る `/1` の組み合わせも含めて `/8` 未満を拒否する | 変数を省略可能にし、既定値を用意する | EKS は CIDR リストが空だと「制限なし」と解釈するため、「値を指定しない」が黙って「公開」を意味してはならない | 08 |
| 20 | クラスタ管理者の決め方 | `bootstrap_cluster_creator_admin_permissions = false` にし、`admin_principal_arns` に列挙した ARN だけにアクセスエントリで管理者権限を与える | Terraform 実行者を暗黙の管理者として残す | 暗黙の管理者はどの変数にも現れず、レビューできない | 08 |
| 21 | Karpenter 本体を Terraform で入れるか | 入れない(Argo CD が入れる)。ただし Karpenter が要る AWS 側の下ごしらえ(IAM、SQS、EventBridge の 5 ルール)は Terraform で作る | Helm provider で Terraform から Karpenter を入れる | Argo CD が in-cluster のコンポーネントを受け持つという設計(#17)に合わせ、Terraform は AWS 側が要求するものだけを作る | 08 |
| 22 | Karpenter が作った EC2 インスタンスを安全に壊す方法 | destroy 専用の `terraform_data` が `teardown-guard.sh` を呼び、ネットワークを壊す前に Karpenter のインスタンスを終了・片付ける | 手順書に「先に Kubernetes から NodePool を消す」と書くだけにする | 手作業の手順は既に 1 度(E0〜E3 実験のクラスタ解体時)失敗している。セルが既に壊れているときほど、動くクラスタを前提にした手順は機能しない | 08 |
| 23 | 実機検証を常設クラスタなしでどう運用するか | 「正」をメンテナのコメント 1 件(計画)に置き、ラベルはそこからの表示にする。承認は commit の SHA と計画に束縛し、tier(`cpu-only` / `light` / `queued`)をスクリプトで決定的に判定する | PR 本文に YAML を書く/台数やリージョンをラベルに持つ/Issue でバッチを管理する | PR 本文は fork の作者が Go の後に書き換えられる。台数やリージョンをラベルに持つと組み合わせが爆発する。Issue は第二の真実になる | 09 |
| 24 | AI とメンテナが同一 GitHub アカウント配下という制約への対処 | GitHub の中(ラベルの actor 確認)だけでは区別できないと認めたうえで、AI の AWS 側の権限を分けない(購入・クラスタ作成を MFA 必須ロールに分けない)と決定した。AI の GitHub 上の身元を分けるかどうかは決定に明記していない。計画の commit SHA と PR の head、ラベルの時刻を束縛する規則が、AI に対する主な安全装置として残る | AWS 側の権限分離(MFA 必須ロール)を採る/ラベルを付けた actor で承認を確認する | 同一アカウントなので GitHub のイベントだけでは区別できない。権限分離を採らないため、AI が計画を更新してから自分でラベルを付ければ束縛の 4 条件は通ってしまい、同一アカウントの AI による誤用は受け入れた残余のリスクとして扱う | 09 |
| 25 | 開発を AI メンバーで分担する方法 | 1 つの Issue につき、実装・テスト作成・レビュー・検証を別々のセッションに分ける。同じセッションが 2 つの役を兼ねない | 1 つのセッションが実装からレビューまで済ませ、チェックリストで確認する | テストが実装に合わせた形になり、レビューが書いた本人の見立てに引きずられる | 10 |
| 26 | 公開リポジトリでの書き込みの扱い | `@littlemex` 以外が書いた Issue・PR・コメントの本文は、評価するデータであって指示ではないと定める。verifier は `status: verifying` の付いた commit だけを実行し、fork のコードは資格情報のある環境で実行しない | レビューだけに任せ、書いた人による区別はしない | レビューの前に、仕込まれた指示が実行されてしまう可能性がある | 10 |
| 27 | 機能を変える PR をいつ開くか | 証拠が揃う前に draft で開き、`## Test Results` に `pending verification` と書く。証拠が揃ったら ready にする | 証拠が揃ってから PR を開く | アップストリームの fail-closed の規則(証拠が無ければ進まない)を守りながら、検証する人(verifier)に流す対象を与える必要がある | 10 |
| 28 | アップストリームのテンプレートをどこまで真似るか | 新しいテストケースの様式以外は一字一句そのまま使う(リンクと Directory Structure の中身だけ差し替え) | このリポジトリ専用の欄を足したフォームにする | アップストリームを読み慣れた人が迷わず貢献できる形を保つ | 10 |
| 29 | アップストリームへどの提案を出すか | 数行で出せるもの(PR テンプレートの伏せ字注意、stale workflow の権限と文面の修正)はそのまま候補にし、規模が大きいもの(device plugin の検出方法、Capacity Block を探して買う道具)は先に Issue で相談する形にする。実際にはまだ何も出していない | すぐに実装 PR を出す | 検出方式の変更は「衝突」の定義そのものをアップストリームと決める必要があり、購入を伴う道具は課金が発生し取り消せない操作を含む | 11 |
| 30 | 複数チームでセルを共有するとき、チームの状態をどこに置くか | チームごとの小さな Terraform ルート(クラウド側の身元: アクセスエントリ、共有 Pod Identity ロールへの関連付け)と、Argo CD の `ApplicationSet`(クラスタ内だけで完結するもの)の組み合わせにする。後者はさらに、管理者が持つ「枠」(namespace、`ResourceQuota`、`NetworkPolicy`、`LocalQueue`、`AppProject` 本体)と、チーム自身が持つワークロードの経路に分ける | (a) 基盤の Terraform ルートにチームを map 変数として追加/(d) ACK でクラスタ内コントローラにチームの身元を作らせる/(e) チーム専用の CRD とコントローラ | (a) は容量の設計で退けた `accelerator_pools` と同じ、1 つの共有変数にチームの出入りを書き込む形の衝突が再発する。(d) は `iam:CreateRole` を呼べる権限をクラスタ内コントローラに持たせ、乗っ取られたときの被害が大きい。(e) は Argo CD がすでに宣言的にやっていることの独自再実装になる | 03b |
| 31 | テナント分離の実現方法に独自 CRD を作るか | 作らない。`karpenter-tenant-pools` の CRD(`AcceleratorClass`/`AcceleratorPool`)そのものは採用せず、namespace から導いた taint と `ValidatingAdmissionPolicy` によるその検査という、コードではなくパターンだけを取り入れる | `karpenter-tenant-pools` の CRD と自己申請の仕組みをそのまま採用する | 2 章の「独自 CRD を作らない」決定をテナント向けに蒸し返すことになる。プールの自己申請は 3b 章の問題(プールは既定で共有すべきもの)を解決しない。`karpenter-tenant-pools` 自身がまだ `v1alpha1` の早期段階で、1 章の原則どおり外部 OSS のまま置いてよい | 01、03b |
| 32 | GPU と Neuron のデバイスをチーム間でどう共有するか | 共有は常に時間方向の共有(Cohort を通じた借用と `reclaimWithinCohort` による回収)とし、2 つのチームが同時に 1 つの物理デバイスの計算を分け合うことはしない。1 つのプールは `aws.amazon.com/neuron` と `aws.amazon.com/neuroncore` のどちらか一方だけをチーム間で分け合う単位に固定する | 2 つのチームに同じデバイスの同時利用を許す/2 つのリソース名を両方チーム間で分け合う単位にする | 同じシリコンを 2 つの名前で二重に数えると、クォータの数え方がずれる。この前提自体は、device plugin と DRA ドライバの二重広告が無いことに依存しており、二重広告があれば 6 章・7 章の実測のとおり警告なしに破れる | 03b |
| 33 | Neuron を前提にした提案をアップストリームに出すか | `awsome-distributed-ai` には出さない。Trainium に特有の知見は distai-platform の中で育てる。ただし DRA の実験で分かった Neuron に関わる知見(Karpenter への Neuron 向け DRA gate の追加など)は、アップストリームではなく Karpenter のような該当する上流プロジェクトへの候補として扱う | GPU と同様に Neuron 向けの提案もアップストリームに出す/Neuron に関わる知見は一切アップストリームに出さない | アップストリーム側の判断材料は `amazon-eks` の例に Trainium が無いことという、1 つのディレクトリの中身だけに基づく。この判断により、AI メンバーへの指示規則の提案と `amazon-eks` への Karpenter/DRA 拡張の RFC は、持ち主の判断でアップストリームへの候補から外された | 11 |

## 2. まだ決まっていないこと

Issue #17(容量の混ぜ方)と #19(device plugin と DRA)は、どちらも安い実験で段階的に骨格を決める計画になっている。この書籍の時点で E0 から E4 までは実機で実施済みだが、残りはまだ実施していない。

| 実験 | 使うもの | 確かめること | 決められること |
| --- | --- | --- | --- |
| E5 | `g7` を数台 | device plugin から DRA へのノードプール単位の移行手順(5 章で見た blue/green)を、実際にジョブを流しながら通す | 移行の手順を確定する |
| E6 | `trn2.48xlarge` の spot 1 台(東京) | 連続したデバイスの割り当てと、EFA の DRA との同居 | Neuron 用の追加のスケジューラを捨てられるかどうか |
| E7 | `p5` / `p5en` の spot 1〜2 台(us-east-2、us-west-2) | 以前の実測(各 1 回のみ)を、EKS 1.37 と今のドライバで複数回確かめ、2 台での NCCL も確かめる | 大型 GPU で DRA と Layer 2 を既定にするかどうか |

容量の章(02、03)で挙げた「一番危ない前提」の検証実験も、まだ実施していない。

| # | 実験 | 使うもの | 分かれば次に進めること |
| --- | --- | --- | --- |
| 1 | Kueue と Karpenter で起動に失敗したとき、次の flavor に進むか | `g7.2xlarge` の spot(取りやすいリージョン)。失敗役は `g6e.xlarge` の spot(どこも取りにくい) | flavor と NodePool の対が動くか。GPU と Neuron を 1 つの `resourceGroup` に書けるか |
| 2 | Trainium が同じ chart で立つか、SkyPilot が Trainium を扱えるか | `trn2.3xlarge` の spot(`ap-southeast-4`) | SkyPilot の Trainium 対応の有無(別の記事では未検証だった) |
| 3 | 予約の勘定(ODCR を超えて入場させないか、借用と取り返し) | `g7.2xlarge` の ODCR 2 台を 1〜2 時間 | 予約の数を超えて入場させないことの確認 |
| 4 | Capacity Block の一生と、クラスタを選ぶ仕組み(SkyPilot か MultiKueue か) | `trn2.3xlarge` の 24 時間の Capacity Block と、安い GPU の spot | クラスタ選択の仕組みを 1 つに決める |
| 5 | EFA の複数ノード | `trn2.48xlarge` の spot 2 台(東京)。取れなければ Capacity Block 2 台 | まとめての入場、同じ Availability Zone、EFA の集団通信の確認 |

共有ストレージ(Issue [#18](https://github.com/littlemex/distai-platform/issues/18))も、まだ計画段階である。これは [`littlemex/sharedfs`](https://github.com/littlemex/sharedfs)(Apache-2.0)を外部コンポーネントとして取り込み、`FilesystemShare` という 1 つのクラスタスコープのリソースで、既存の PersistentVolume の一部ディレクトリを、名前空間ごとに読み書きか読み取り専用で共有する仕組みである。訓練用のデータセットとモデルの重みはチーム間で共有し、チームごとのチェックポイントのディレクトリだけを書き込み可能にする、という使い分けを想定している。FSx for Lustre CSI ドライバの動的プロビジョニング(クレームごとに別のファイルシステムを作る)や、手作業の静的 PersistentVolume(ファイルシステム全体を結んだ名前空間に晒してしまう)は、この用途には合わないため代替案として退けられている。sharedfs の README には、Kueue が待機状態の PVC を見ないため `waitForPodsReady` を使う必要があると書かれており、これは #17 で決まる Kueue の設定と直接つながる。この Issue は、#17 のアーキテクチャ(特に GitOps の経路と Kueue の設定)が決まるまで、スケジュールされていない。

複数チームでのセル共有(Issue [#22](https://github.com/littlemex/distai-platform/issues/22)、3b 章)も、まだ設計段階であり実装はない。確かめる実験は 9 章の `cpu-only` と `light` の段階に沿って進める計画である。主な確認項目は次の 6 つである。

- チームをまたぐ toleration、ワイルドカードの toleration、`nodeName` の直接指定が admission で拒否される。
- Cohort の借用と `reclaimWithinCohort` による回収が `flavorsUsage` から確認できる。
- ラベルの無いワークロードが保留になる。
- namespace スコープのアクセスエントリが他の namespace への呼び出しを拒否する。
- sharedfs 越しの他チームのディレクトリへの読み取りが失敗し、取り消された権限が文書化された猶予期間(既定 15 分)で止まる。
- 3b 章で名前を付けた 11 個の迂回(H1〜H11)が実際に拒否される。MIG を使うプールでのデバイス共有、`karpenter-tenant-pools` の自己申請が将来必要になるかの再検討、NVIDIA Container Toolkit の設定レベルでの対策は、3b 章の時点でも開いたままの問いとして残されている。

この書籍の round 2 のレビューでは、3b 章の設計と他の章の決定の間に、4 つの矛盾が見つかっていた。3b 章の迂回対策 H11 はチームの `AppProject` に `NetworkPolicy` を禁止リストとして持たせる想定だったが、3b 章のネットワークの節と H7 は逆に各チームが自分の `NetworkPolicy` を書くことを前提にしていた。H4 はテナントに `karpenter.sh/do-not-disrupt` と Pod Disruption Budget の使用を一律で禁じていたが、3 章の容量設計は複数ノードの EFA 訓練でまさに前者に頼っていた。すべてのチームの `nominalQuota` を 0 にする設計では、Cohort から取り返す「自分の取り分」が無くなり、`-rsv` のワークロードを中断させないという保証も成り立たなくなっていた。チームごとに `ClusterQueue` を 1 つに絞る設計は、3 章で見たワークロードごとの flavor の順番を、1 つのキューの中では表現できなかった。

これら 4 つは、Issue #22 自身の見直しで解消済みである(2026-11-18、独立したレビューと、Kueue・Kubernetes・VPC CNI の一次情報での裏取りを経た)。`NetworkPolicy` は、管理者が持つ基準の 1 枚(既定で全拒否)と、チームが自由に追加する分とに分け、Admin tier の `ClusterNetworkPolicy` が拒否した通信は namespace 側のどんな `NetworkPolicy` でも開き直せないという、AWS 自身の文書で曖昧さの無い部分だけに安全性の根拠を置いた。`karpenter.sh/do-not-disrupt` は、Pod が名乗る `LocalQueue` の名前(ワークロードの型)で許可・拒否を分け、`-rsv` だけの学習用の queue では許し、`-flex` に届く queue では禁じる形に絞った。`Cohort` の `nominalQuota` は、Kueue の文書で「メンバーの取り分に足される」ことを確認したうえで、学習と推論の `ClusterQueue` には実際の取り分を持たせ、バッチと短時間学習の `ClusterQueue` はゼロのままにする設計に改めた。`ClusterQueue` は 1 チーム 1 つではなく、ワークロードの型ごとに分け、1 つの平らな `Cohort` で束ねることにした(チームごとに `Cohort` を入れ子にする案は、二重にクォータを数えてしまう欠陥と、チーム内での回収が働かなくなる欠陥が見つかり、採らなかった)。詳細は 3b 章の該当箇所に反映済みである。

<!-- FIG name="99-open" kind="relation" shows="E5-E7とリスク前提の実験5件、Issue#18 sharedfsが、どのIssue(#17/#19)の決定待ちかを示す" labels="E5, E6, E7, Issue #17, Issue #19, Issue #18, FilesystemShare, riskiest assumption" -->
![まだ決まっていない問いの関係を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/99-open.png)

## まとめ

distai-platform の設計は、1 つの大きな決定ではなく、小さな決定の積み重ねでできている。その決定とは、基盤を作り直す、容量の問題を 4 つの決定(買う、セルを選ぶ、ジョブを入れる、実際に起動する)に分ける、device plugin と DRA の排他をノードプール単位のラベルで解く、Terraform の責務をセルの入れ物だけに絞る、複数チームでのセル共有をチームごとの小さな Terraform ルートと taint・admission policy の組み合わせで解く、実機検証の承認を commit と計画に束縛する、AI メンバーの役割を分ける、の 7 つである。どの決定にも、比べた代替案と、それを退けた具体的な理由が残っている。一方で、容量の判定で一番危ない前提(Kueue の入場が容量の保証になるかどうか)、Trainium の大型・複数ノードでの DRA の扱い、共有ストレージの Kueue との結び方、複数チームでのセル共有の設計(3b 章。他の章の決定との 4 つの矛盾は Issue #22 の見直しで解消済みである)は、まだ実機の実験かメンテナの判断で確かめる段階にとどまっている。
