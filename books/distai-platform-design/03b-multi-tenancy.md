---
title: "マルチテナンシー層の設計 - 複数チームで 1 つのセルを分け合う"
---

## はじめに

この章が扱うのは、1 つのセル(クラスタ)を複数のチームで共有するための、権限とリソースの分離の設計である。管理者がセルを構築し、各チームがその上で自分のワークロードを作って動かす、という役割分担を崩さずに、GPU や Trainium のノードプールと、共有の FSx for Lustre ファイルシステムは複数チームが同時に使えるようにする。チームの間を「きれいに分離する」ことと、「アクセラレータとストレージを共有する」ことは、一見矛盾する要求だが、この章の設計はこの両方を同時に満たすことを目標にする。

この章が設計するのは、1 クラスタの中で namespace を単位に分離する「soft multi-tenancy」である。チームごとに別クラスタを立てる「hard multi-tenancy」は、分離は強くなるが、クラスタ数だけ基盤の運用コストが増え、高価なアクセラレータやストレージを複数チームで分け合うという、この章がそもそも解きたい問題にも向かない。EKS 自身のベストプラクティスガイドも、namespace を soft multi-tenancy の基本とし、taint/toleration を namespace 単位の分離の上に重ねる層として示したうえで、Pod 側の申告を信じず admission の層を置くことを勧めている (ガイドが例に挙げるのは OPA/Gatekeeper と Kyverno である)。この章はその型に沿うが、admission の層に `ValidatingAdmissionPolicy` を使うのはこの基盤の選択であり、上流にマルチテナンシーの手順として文書化されたものではない。また、このガイドにはストレージの分離の節が無く、FSx への言及も無い (いずれも 2026-11-18 に Issue #22 の検討で確認した)。

断っておくと、この章の内容は提案 (Issue #22) であり、まだ実装されておらず、実機で確認した事実ではない。上流のドキュメントから確認した事実 (Issue #22 が 2026-11-18 に出典ごとに確認したもので、章末の参考資料に挙げる)、本書の他の章の実験で測った事実 (その実験の型とリージョンの範囲に限る)、そしてまだ検証していない設計上の期待は、本文の中でそのつど区別して書く。断りなく「〜である」と書いた振る舞いのうち、出典も他章の実測も示していないものは、この設計がそうなるよう組む、という意味である。

本題に入る前に、この章がどの決定の続きにあるかを整理しておく。基盤の設計([2 章](./02-four-decisions))は、Terraform がセルの外枠だけを作り、Argo CD がクラスタの中身をすべて届ける形を決めた。ノードプールの規約([3 章](./03-capacity-pools))は、1 つの `NodePool` が 1 つの `ResourceFlavor` に対応し、`-rsv` / `-od` / `-flex` という購入形態ごとのプールに分かれることを決めた。デバイスの公開方式([5 章](./05-device-plugin-and-dra))は、device plugin と DRA のどちらを使うかをノードプールとデバイスクラスの組に貼るラベルとして扱うことを決めた。基盤そのもの([8 章](./08-foundation))の実装は、システムノードの IMDSv2 の強制とホップ制限、CNI の権限を Pod Identity に任せる仕組み、`NetworkPolicy` を強制できる状態そのものはすでに用意している一方で、テナント用の Pod をシステムノードから遠ざけること、実際の `NetworkPolicy` オブジェクトを書くこと、Argo CD のロール割り当てをチーム単位に分けることの 3 つは、この章の元になる Issue #22 へ明示的に持ち越している。この章は、これらの意図的に残された「チームをどう分けるか」の部分を埋める。

もう 1 つ、この章が明示的に答えを出す問いがある。`karpenter-tenant-pools` という、この基盤とは別の、ごく初期段階の OSS プロジェクトは、チームが自分の namespace の中だけで Karpenter の `NodePool` を自己申請できる仕組みを持っている。これは、基盤の設計がすでに決めた「独自の CRD を作らない」という方針と正面から衝突する。この章では、その衝突をどう解くか(採用しない部分と、パターンだけ取り入れる部分)を 7 番目の決定として明示する。この章で「n 番目の決定」と呼ぶのは Issue #22 の Decision 1 から 10 のことで、順に、チームの状態の置き場所(1)、身元(2)、スケジューリングの分離(3)、クォータとアクセラレータの共有(4)、デバイスの共有(5)、ネットワークの分離(6)、`karpenter-tenant-pools` との関係(7)、ストレージの共有(8)、可観測性とコスト帰属(9)、想定される迂回への対策(10)であり、この章の節もおおむねこの順に並ぶ。

<!-- FIG name="03b-tenancy-overview" kind="relation" shows="誰がどの層を持つか: 管理者が持つセルとGitOpsの枠組み、チームが持つ namespace の中身、両者の境界線" labels="admin, team, cell, namespace, GitOps shell, Terraform" -->
![マルチテナンシー層が足す部分と、既存の決定が持つ部分の関係](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/03b-tenancy-overview.png)

## 誰が何を持つか

### 本質と、チームの寿命がセルの寿命と違うという問題

この章の最初の決定は、「チームの状態をどこに置くか」である。チームは、セルより短い寿命で増減する。あるチームが加わり、別のチームが抜ける、という変化がセルの生きている間に何度も起きる。ここで本質的な問題は、チームの追加や削除を扱う仕組みが、セル全体の 1 つの Terraform の状態ファイルに書き込む形になっていると、チームの出入りのたびに全チームに影響する apply が必要になり、2 つのチームが同時に変更を加えると衝突する、という点である。これは、[2 章](./02-four-decisions)で見た、容量の設計が解決した問題(`accelerator_pools` という 1 つの共有変数に全プールを書き込む形)が、チームについて再発したものである。

選択肢は次のように整理できる。

| 選択肢 | 内容 | 強み / 弱み | 判断 |
| --- | --- | --- | --- |
| (a) 基盤の Terraform ルートにチームを map 変数として追加 | 旧 `accelerator_pools` と同じ形 | チームの寿命をセルの寿命と結びつけ、他チームの変更とも結びつける | 採らない |
| (b) チームごとに小さな Terraform ルートを 1 つ(`infra/teams/<name>`、状態キーも個別)、管理者が apply | クラウド側の身元(アクセスエントリの関連付け、共有ロールへの Pod Identity の関連付け)だけを作る | チームごとに完全に独立。他チームの状態にも基盤の状態にも触れない | **採用** |
| (c) Argo CD の `Application` をチームごとに 1 つ(`ApplicationSet` の Git ディレクトリジェネレータが `gitops/teams/<name>/` を走査する) | namespace、`ResourceQuota`、`NetworkPolicy`、`LocalQueue` など、クラスタ内だけで完結するものを作る | Issue #17 がこの基盤の型として決めた `ApplicationSet` に合う (ただし GitOps 側はまだ実装されておらず、#17 が挙げたのはクラスタごとの generator で、ここでは Git ディレクトリの generator を使う)。ただし、チーム自身のリポジトリがこの経路に書き込めると、チームが自分自身の境界を書き換えられてしまう | **採用するが、さらに分割する** (後述) |
| (d) ACK (AWS Controllers for Kubernetes) の IAM/EKS コントローラがクラスタ内でチームの身元を作る | GitOps の経路が 1 本にまとまる | IAM を操作できる権限をクラスタ内のコントローラに持たせることになり、そのコントローラが乗っ取られたときの被害が大きい。後述する共有ロールの設計により、定常運用では `iam:CreateRole` の呼び出しがそもそも要らなくなるため、この選択肢を採る理由はむしろ弱まる | 採らない (チームごとの 2 つのリソースさえ実際の負担になったときに見直す) |
| (e) チーム専用の CRD とコントローラ(`karpenter-tenant-pools` の `AcceleratorPool` と同じ発想) | namespace スコープの CR から、クラスタ内のオブジェクトを作る | クラウドの認証情報が要らない以上、これは (c) の Argo CD がすでに宣言的にやっていることを、わざわざ独自実装し直すだけになる。ノードプールのために退けた独自 CRD([2 章](./02-four-decisions))と、同じ形の抽象化をチーム向けに繰り返すことになる | 採らない |

採用するのは (b) と (c) の組み合わせである。なお基盤の Argo CD は、ハブのセルにだけ作る任意の capability である ([8 章](./08-foundation))。(c) の経路はこの Argo CD を前提にするので、Argo CD を持たないセルにどのハブから届けるかは、Issue #22 には書かれていない。クラウド側の、権限を持つ部分(アクセスエントリの関連付け、Pod Identity の関連付け)は小さな Terraform ルートとして管理者が apply し、クラスタの中だけで完結する部分は Argo CD の `ApplicationSet` で届ける。

### (c) をさらに分割する理由

(c) の中身、つまりチームのクラスタ内オブジェクト一式を、さらに 2 つに分ける必要がある。理由は単純で、自分自身の境界を定義しているオブジェクト(namespace のラベル、`ResourceQuota`、`NetworkPolicy`、自分の `AppProject` オブジェクトそのもの)を編集できるチームは、自分自身の境界を編集できてしまうからである。

| 部分 | 内容 | 誰が書き込めるか |
| --- | --- | --- |
| 管理者が持つ「枠」 | `Namespace`(`distai.dev/team` ラベルを持つ。このラベルは namespace の名前と常に一致させ、独立した正本にはしない)、`ResourceQuota`、`LimitRange`、既定の `NetworkPolicy`、`LocalQueue`、チームの `AppProject` オブジェクトそのもの | チームのコミットが届かない Git のパス(たとえば `CODEOWNERS` で保護する) |
| チームが持つワークロードの経路 | チーム自身の `AppProject` にスコープされた 1 つ以上の `Application`。チーム自身のリポジトリを元に、ワークロードのマニフェスト(`TrainJob`、`JobSet`、`RayJob`、Pod、Service など、自分の namespace の中だけ)と、基準となる `NetworkPolicy` に追加する、チーム自身の `NetworkPolicy`(後述のネットワークの節)を届ける | チーム自身 |

他の namespace に触れないことの根拠は、`AppProject` の `destinations` をそのチームの namespace 1 つだけに限ることにある。チームの `Application` がどの namespace を宛先に書いても、`AppProject` 自身が許す宛先の外は Argo CD が同期を拒否するので、チームは自分のワークロードを完全に自己申請できるが、自分自身の境界や他の namespace には触れられない。この区別は、後で 10 番目の決定(想定される迂回への対策)でさらに具体的に強化する。ただし `AppProject` が効くのは Argo CD を通る経路だけである。チームのメンバーは、次の節のアクセスエントリによって自分の namespace に kubectl でも書き込めるので、そちらの経路で自分の境界を変えられないことは、namespace に絞った `AccessScope` と、このあと述べる admission のバンドルで担保する設計である。組み込みの `AmazonEKSEditPolicy` で `TrainJob` のようなカスタムリソースを扱えるかどうかは、まだ確かめていない。

## 身元をどう分けるか

### 受け持ちの整理

人がクラスタに触る経路、Pod が AWS に触る経路、Argo CD にチームが触る経路は、それぞれ別の仕組みで分ける。3 つとも、1 つのチームが 1 つの IAM Identity Center のグループに対応する、という前提を共有する。

| 仕組み | 誰のためのものか | スコープの絞り方 |
| --- | --- | --- |
| EKS のアクセスエントリ | 人間やグループによる Kubernetes API へのアクセス | チームごとに 1 つの IAM Identity Center グループを、組み込みの `AccessPolicy`(たとえば `AmazonEKSEditPolicy`)に、`AccessScope { type = "namespace", namespaces = [チームの namespace] }` で結びつける。これは EKS 自身が強制する。基盤のレイヤー([8 章](./08-foundation))が管理者用の `admin_principal_arns` や Argo CD の `role_mappings` にすでに IAM Identity Center のグループを使っているのと同じ考え方である |
| EKS Pod Identity | ワークロードから AWS へのアクセス(たとえばチーム自身の S3 の prefix) | `(クラスタ, チームの namespace, サービスアカウント)` の組 1 つにつき 1 つの関連付けを作るが、その先は **1 つの共有 IAM ロール**であり、チームごとに新しいロールを作らない。ロール側のリソースポリシーは、Pod Identity が自動で付与するセッションタグ(`kubernetes-namespace`、`eks-cluster-name`、`kubernetes-service-account` など、追加の設定なしで付く)を条件式に使う。たとえば S3 バケットポリシーで `aws:PrincipalTag/kubernetes-namespace` がリクエストする prefix と一致する場合だけ `s3:GetObject` / `s3:PutObject` を許す、という形である。1 クラスタあたりの関連付けの上限は 5,000 件である |
| Argo CD | チーム自身が自分の `Application` を管理する経路 | チームごとに 1 つの `AppProject`(Argo CD 自身が文書化しているマルチテナンシーの型)。各 `AppProject` は自分自身の `roles[].groups` を持ち、そのチームの IAM Identity Center グループを、その `AppProject` だけに効くロールへ結びつける。共有の設定ファイルを編集する必要はない |

人間のアクセスエントリを個人単位の IAM プリンシパルではなく、チームごとの IAM Identity Center グループに結びつけるのは、小さいが重要な判断である。個人を直接結びつけると、1 人が 2 つのチームに所属するとき、同じアクセスエントリに異なる `AccessScope` の関連付けが積み重なり、どちらのチームの Terraform ルートがそのエントリの正本かが曖昧になる。グループを単位にすれば、1 つの IAM Identity Center グループは常に 1 つの IAM プリンシパルであり、1 つのアクセスエントリしか持たないので、この曖昧さは最初から発生しない。

Pod Identity について、チームごとに専用の IAM ロールを作る設計ではなく、1 つの共有ロールと、セッションタグを条件に使う設計(属性ベースのアクセス制御、ABAC)を採った理由は、チームが持つべき裁量を管理者の Terraform の状態に入り込ませないことにある。チームごとに専用ロールを作る設計では、サービスアカウントの名前や権限の変更が、すべて管理者の Terraform apply を経由することになり、チームが持つべき裁量(どんなサービスアカウントを使うか)まで管理者の状態に入り込んでしまう。1 つの共有ロールに、Pod 自身の namespace の値を条件とする ABAC を使えば、チームごとの Terraform ルートに必要なリソースは、関連付け(アクセスエントリへの関連付け、Pod Identity の関連付け)の 2 種類だけに縮む。ただし Pod Identity の関連付けはサービスアカウントごとに 1 つ要るので、AWS に触れるサービスアカウントを増やすときは、管理者の apply が 1 回要ることに変わりはない。縮むのは新しい IAM ロールを作る作業であり、関連付けそのものではない。

<!-- FIG name="03b-identity-per-team" kind="relation" shows="チームごとの身元: IdCグループ→アクセスエントリ、Pod→Pod Identity→共有ロール+ABAC、チームのAppProject" labels="IdC group, access entry, Pod Identity, shared role, ABAC tag, AppProject" -->
![チームごとの身元の3つの経路](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/03b-identity-per-team.png)

AWS 自身のドキュメントは、Pod Identity について「コンテナはセキュリティ境界ではない」と明記している。Pod Identity が分離するのは、どの Pod がどの AWS の認証情報を取得できるかであって、同じノード上の Pod どうしの計算の分離ではない。計算の分離は、次のスケジューリングの節と、ネットワークの節の役目である。

## スケジューリングの分離

### 2 種類のノードプールと、その違い

ノードプールは、これまでの決定([3 章](./03-capacity-pools))と同じく、常に管理者が所有する。この章が足すのは、ノードプールを 2 つの形に分けることである。どちらの形になるかは、プールを作るときに決まり、途中で両方の性質を持つことはない。

| 種類 | taint | ResourceFlavor の参照先 | 典型的な使い方 |
| --- | --- | --- | --- |
| 貸し出し可能なプール(既定で、よくある形) | チームを示す taint を付けない | 複数チームの `ClusterQueue` から参照される | 共有される Capacity Block など、要求に応じて複数チームが時間を分けて使う容量 |
| ノード専有のプール | `distai.dev/team=<name>:NoSchedule` を、管理者が `NodePool` のマニフェストに直接書く(テナント側の入力から導出することはない) | その 1 チームの `ClusterQueue` だけから参照される | チームが買った容量を、コンプライアンスかレイテンシの理由で貸し出したくない場合 (容量の購入そのものは管理者が行う。後述) |

ここで重要な制約が 1 つある。taint が付いたプールを、別チームが Kueue の Cohort を通じて借りられるようにしてはならない。admission された Pod が、その先のノードの taint を tolerate できなければ、クォータだけを抱え込んだまま動けない Pod になるだけである。言い換えると、「貸し出し可能」であることと「taint が付いている」ことは、同じプールの上で同時には成立させない。

### taint だけでは境界にならない

taint は Pod 側が自由に toleration を書けてしまうので、それだけでは「他チームの値を勝手に tolerate する Pod」を止められない。これは [1 章](./01-principles)で触れた一般論の、この章での具体化である。そこで、管理者の GitOps の経路から配る、クラスタスコープの `ValidatingAdmissionPolicy` の束(バンドル)が、実際の境界を強制する。このバンドルは、保護するキーや、除外する namespace の名前などをすべて値として持ち、特定の実装にハードコードしない形で配る。これは、`karpenter-tenant-pools` が示した型(namespace から導いた taint の値と、それを検査する admission policy の組み合わせ)を、そのコードごと取り込むのではなく、パターンだけ一般化して取り込んだものである(7 番目の決定で詳しく述べる)。`ValidatingAdmissionPolicy` は Kubernetes 1.30 で GA になった機能であり、この基盤が使う EKS 1.37 ではそのまま使える。

バンドルが強制する規則は次のとおりである。

- `distai.dev/team` という保護されたキーへの toleration は、その Pod 自身の namespace のラベルと一致する値のときだけ許し、別チームの値も、値を指定しない `operator: Exists` も拒否する。
- `CriticalAddonsOnly` は、明示的に除外した namespace(`kubernetes.io/metadata.name` という、テナントが変更できない属性で選ぶ)の外では、どんな値であっても tolerate を許さない。これが、基盤のレイヤー([8 章](./08-foundation))がすでに指摘していた穴、つまり taint だけではテナントの Pod をシステムノードから遠ざけられない、という問題を閉じる。
- `spec.nodeName` を直接指定する Pod の作成は拒否する。これは作成時だけを検査する。`nodeName` は作成後に不変な値なので、更新も検査すると、通常の Pod へのラベル付けのような無害な更新までまとめて拒否してしまう(`karpenter-tenant-pools` の運用で実際に起きた問題)。

アクセラレータの種類を示す taint(`nvidia.com/gpu` や `aws.amazon.com/neuron` など)は、意図的に保護されたキーに含めない。どのデバイスクラスのノードに乗るかは、どのチームでも開いているべきであり、同時に共有してはならないのは、ノードではなくデバイスそのものである(5 番目の決定)。

これは、明言しておくべき結果を生む。貸し出し可能なプールの上では、異なる 2 つのチームの Pod が、同じ物理ノードの上に同時に乗ることがある。これは隠れた抜け穴ではなく、意図した結果である。通常の namespace の分離や、CPU・メモリの requests/limits、そして後述のネットワークの分離の節が、ノードの上でのチーム間の分離を受け持つ。

## クォータとアクセラレータの共有

### ワークロードの型ごとに `ClusterQueue` を分け、1 つの平らな `Cohort` で束ねる

この節が決めるのは、チームごとの保証された取り分と、空いている容量の貸し借りを、Kueue の `ClusterQueue` と `Cohort` でどう表すかである。結論は、ワークロードの型ごとに `ClusterQueue` を分け、1 つの平らな `Cohort` で束ねる構成である。チームごとに `ClusterQueue` を 1 つだけ作る、という最初の設計は、見直しが入った。理由は単純で、[3 章](./03-capacity-pools)がすでに決めているとおり、ワークロードの型ごとにフレーバーの並び順が違う(学習は `-rsv` だけ、バッチは `-rsv` の借用のみから `-flex`、推論は `-rsv` から `-od`)。ところが `ClusterQueue` は 1 つにつきフレーバーの並び順を 1 本しか持てないので、1 チーム 1 `ClusterQueue` では 3 つの並び順を同時に表せない。そこで、チームが使うワークロードの型ごとに `ClusterQueue` を 1 つずつ作る(たとえば `team-a-training-trn2`、`team-a-batch-trn2`、`team-a-inference-trn2`)。1 つの namespace に複数の `LocalQueue` を置けることと、`LocalQueue` は常にちょうど 1 つの `ClusterQueue` を指すことは、Kueue 自身のドキュメントで確認した事実である。各 `LocalQueue` は、対応する `ClusterQueue` と同じ名前で揃え、チームはジョブをどの `LocalQueue` に出すかで、ワークロードの型も同時に宣言する形になる。

これらの `ClusterQueue` は、あるアクセラレータのクラスを使う可能性のあるチーム全員で、1 つの平らな `Cohort` を共有する。チームごとに `Cohort` を入れ子にする(クラス全体の `Cohort` の下に、チームごとの子 `Cohort` を置く)案も検討したが、採らなかった。独立したレビュー(2026-11-18、敵対的な観点での査読)で、入れ子にすると 2 つの欠陥が出ると分かったためである。1 つは、Kueue 自身のドキュメントで確認したとおり、`Cohort` 自身の `nominalQuota` はメンバーの `nominalQuota` を置き換えるのではなく、その上に**足される**ものなので、クラス全体の `Cohort` がプールの全容量を持ちながら、その下の子 `Cohort` もチームの取り分を持つと、物理的に存在する以上の容量が宣言されてしまう。もう 1 つは、入れ子にするとチームの中での回収が働かなくなる点である。子 `Cohort` の下にあるワークロードの型ごとの `ClusterQueue` がすべてゼロ値の `nominalQuota` を持つ設計のままだと、`reclaimWithinCohort` が「自分の取り分の範囲内」と認める `ClusterQueue` がチームの中に 1 つも無くなり、同じチームのバッチジョブが同じチームの学習ジョブの枠を奪っても、取り戻す仕組みが無くなってしまう。

そこで採用したのは、入れ子を作らず、実際の取り分(フロア)を、必要な `ClusterQueue` に直接持たせる、平らな構成である。どの `ClusterQueue` が実際の取り分を持つかは、[3 章](./03-capacity-pools)がすでに書いている言葉をそのまま読み取って決める。短時間の単一ノード学習とバッチは、`-rsv` を「借用」としか書かれておらず、これは取り分を持たないという意味にしか読めない。一方、複数ノードの EFA 学習は「`-rsv` だけ」とだけ書かれ「借用」という限定が付いておらず、推論の `-rsv` の側は ODCR という、それ自体が予約である仕組みに支えられている。したがって、学習と推論の `ClusterQueue` には管理者が割り当てた実際の `nominalQuota`(そのチームの保証された取り分)を持たせ、バッチと短時間の単一ノード学習の `ClusterQueue` は `nominalQuota` をゼロのままにして、空いている分を他のどのチームからでも機会的に借りる。クラス全体の `Cohort` 自身の `nominalQuota` も、特定のチームに割り当てていない余剰分があるとき以外はゼロのままにする。

<!-- FIG name="03b-cohort-quota" kind="relation" shows="Cohortが実容量を持ち、各チームのClusterQueueは重みと上限だけを持つ、LocalQueueからの経路" labels="Cohort, nominalQuota, ClusterQueue, LocalQueue, borrowingLimit, lendingLimit" -->
![Cohort に実容量を置き、チームの ClusterQueue は重みだけを持つ構成](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/03b-cohort-quota.png)

数字で確認しておく。8 デバイスの Capacity Block で支えられた `-rsv` プールを、チーム A とチーム B が使うとする。チーム A の学習用 `ClusterQueue`(フレーバーは `-rsv` のみ)には `nominalQuota: 5`、`lendingLimit: 5`、`borrowingLimit: 3` を設定する(`borrowingLimit` は、B が貸し出せる分を超えて借りられないようにするための値である)。チーム A のバッチ用 `ClusterQueue` は `nominalQuota: 0` とする。チーム B の学習用 `ClusterQueue` には `nominalQuota: 3`、`lendingLimit: 3`、`borrowingLimit: 5` を設定する。クラス全体の `Cohort` 自身の `nominalQuota` はゼロである。8 台のうち 5 + 3 = 8 がすでに両チームの学習用の取り分として割り振られているからである。チーム B の学習用の枠が空いている間、チーム A の学習用の枠は最大 3 台まで借りられる(合計で 8 台使える)。チーム A のバッチ用の枠も、空いていればどちらのチームの取り分からでも同じように借りられる。チーム B が自分の取り分(3 台)の範囲に収まる学習ジョブを出した瞬間、`reclaimWithinCohort` を `Any`(優先度が低いものしか取り戻せない `LowerPriority` ではなく)に設定しているので、借りている側が同じ優先度か高い優先度であっても関係なく、チーム B は自分の 3 台を取り戻せる。借りていたのがチーム A の学習用の枠であっても、チーム A 自身のバッチ用の枠であっても、取り戻す仕組みは同じである。

ここで新しいチーム C をこの同じ 8 台のプールに加えるときの代償も、正直に書いておく。1 番目の決定が目指した「チームの追加が他チームの状態に触れない」という性質は、`-od` / `-flex` のような予算のプール(保証を最初から持たない)では今もそのまま成り立つが、`-rsv` の保証された取り分については成り立たない。物理的に固定された予約容量から、新しいチームに取り分を与える方法は、既存のどこかから取るしかないからである。これは `-rsv` の保証という性質そのものに付随する、避けられない代償として受け入れる。

DRA([5 章](./05-device-plugin-and-dra)で言う Layer 2、`ResourceClaimTemplate` を使うワークロード)についても、`deviceClassMappings` の `coveredResources` によって、各チームの、ワークロードの型ごとの `ClusterQueue` が、通常の `resources.limits`(Layer 1)と同じ論理的なリソース名のもとで、同じ `DeviceClass` を独立にクォータ管理できる(5 章の仕組みを、複数チームの複数のキューに広げたものである)。ただし、Kueue の DRA の文書は、同じ `DeviceClass` を 1 つのワークロードの中で Layer 1 と Layer 2 の両方の経路に設定しないよう求めている (勘定がずれる恐れがあるため)。実機で確かめたのは逆側の事実で、[6 章](./06-dra-experiments-nvidia)の実験 E3 (`g7.2xlarge`、eu-south-2、Kueue 0.20.2、テナント 1 つ) では、Layer 1 の Pod のために自動で作られた ResourceClaim は二重に数えられなかった。複数チームの `ClusterQueue` がそれぞれ独立にクォータ管理できるかどうかは、まだ試しておらず、後の実験の計画 (Issue #22 の実験 4) で確かめる。

もう 1 つ、E3 で実測した詰まり方がこの章の設計に効く。Layer 2 のジョブが空いている device plugin の flavor に入場し、`cannot allocate all claims` で動けなくなった (6 章)。デバイスの公開方式を決めた Issue #19([5 章](./05-device-plugin-and-dra))の決定 3 は、Layer 2 のワークロードを DRA だけの flavor を持つ `ClusterQueue` に向けることにしている。ワークロードの型ごとに `ClusterQueue` を分ける上の設計は、この要請にそのまま乗る。Layer 2 を使うワークロードの型には、DRA だけの flavor を持つ `ClusterQueue` を割り当てればよく、1 チーム 1 `ClusterQueue` だった旧設計のように別立ての仕組みを足す必要は無い。

### Kueue をすり抜ける経路を閉じる

ここで、閉じておかなければならない具体的な抜け道がある。`kueue.x-k8s.io/queue-name` というラベルを一切付けない Pod や Deployment は、既定では Kueue に一切気づかれない。貸し出し可能なプールはアクセラレータの taint をどのチームにも開いているので、ラベルを付けずにアクセラレータを要求する Pod を作れば、Karpenter はノードを起動し、Kueue のクォータ管理をまるごと迂回できてしまう。

これを閉じるのが、Kueue の `manageJobsWithoutQueueName` という設定と、それをチームの namespace に絞る `managedJobsNamespaceSelector` である。これらは Kueue 自身が文書化している実在の設定項目である。Pod 統合(通常の Job や JobSet だけでなく、素の Pod も対象にする設定)を有効にしたうえでこれを設定すると、ラベルの無い Pod は、ラベルを付けて `LocalQueue` に向けて出し直すまで、保留(suspend)される。ただし、1 つ注意がある。この設計は、ラベルの無いワークロードを自動的にどこかの既定の `LocalQueue` に向ける動作には頼らない。Issue #22 の検討では、そのような自動の振り分けは無く、保留されたままになると確認しており、チームは自分の `LocalQueue` を宛先に指定しないと、そのワークロードはそもそも動かない。この振る舞いは文書での確認にとどまり、実機ではまだ試していない。

もう 1 つの迂回は、`DaemonSet` である。Kueue の Pod 統合は、`DaemonSet` が作る Pod を対象にしない設計になっている(`DaemonSet` を保留にすると、本来の「全ノードに 1 つ」という意味が壊れるためである)。そのため、テナントの namespace には `DaemonSet` という種類のオブジェクトを作る権限そのものを与えない。これは 3 番目の決定である admission バンドル(`ValidatingAdmissionPolicy` による強制)で実現する(関係する決定は 3 番目と 4 番目)。

ワークロードの型ごとに `LocalQueue` を分けたことには、もう 1 つの効き目がある。10 番目の決定(想定される迂回への対策)で述べる `karpenter.sh/do-not-disrupt` の扱いは、Pod がどの `kueue.x-k8s.io/queue-name` を名乗っているかで判断する。`ValidatingAdmissionPolicy` の CEL 式は、その場にある Pod 1 つの情報しか見られず、`LocalQueue` から `ClusterQueue` へ、そこからフレーバーの一覧へ、という横断的な参照はできない。そこで、管理者が `-rsv` だけの `LocalQueue` を作るたびに、同じテンプレートから「この名前の queue は `do-not-disrupt` を許す」という小さな一覧を生成し、admission バンドルはその一覧に対する静的な照合だけを行う。一覧と `LocalQueue` の対応が食い違う心配は無い。両方が同じテンプレートから一度に作られるからである。

最後に、これまでの決定で変わらず残る前提を確認しておく。[3 章](./03-capacity-pools)が指摘していたとおり、admission が容量の確保を意味するのは `-rsv` のプールだけであり、`-od` や `-flex` では、チームが admission されてクォータを保持したまま、Karpenter がノードの起動に失敗しうる (3 章のとおり、これは実機で確認されていない推測である)。マルチテナントではこの危うさに新しい側面が加わる。この推測が当たるなら、詰まった admission は、そのワークロード自身の時間制限かキューの方針が解消するまで、同じ Cohort の他チームの reclaim まで塞ぐと考えられる。そのため、[9 章](./09-verification-operations)で触れる時間制限(time box)の仕組みが、単一テナントのときよりも重要になる。

## GPU と Neuron のデバイス共有

### 本質: ノードは共有できるが、1 つのデバイスは共有できない

この基盤における「共有」は、異なるチームの間では常に時間方向の共有、つまり複数チームのキューが Cohort を通じて同じプールを時間を分けて使うこと(前節)であり、2 つのチームの Pod が同時に 1 つの物理デバイスの計算を分け合うことではない。

NVIDIA の GPU には、デバイス 1 つを複数のプロセスで分け合う仕組みが 3 つある。time-slicing は、device plugin や GPU Operator の設定(`sharing.timeSlicing.resources[].replicas`)で有効にするもので、NVIDIA 自身のドキュメントが、分けた先の間にメモリやフォルトの分離は無いと明記している。MPS(Multi-Process Service)は、NVIDIA 自身が「協調的な」複数プロセスのための仕組みと説明しており、互いを信頼し合うプロセスを前提にしている。MIG(Multi-Instance GPU)だけが、ハードウェアレベルのメモリとフォルトの分離を実際に持つが、対応するのは Ampere 世代以降のデータセンター向けチップ(A100、H100、H200、Blackwell 世代など)に限られ、この基盤が今使っている `g5` / `g6e` / `g7` の系列(A10G、L4、L40S 系のチップ)には無い。

Neuron 側では、device plugin が `aws.amazon.com/neuron`(デバイス 1 個)と `aws.amazon.com/neuroncore`(1 つのデバイスの中の、複数ある NeuronCore の 1 個)の両方を公開する。これは [7 章](./07-dra-experiments-trainium)の実験 E4 で `trn2.3xlarge` (ap-southeast-4) を 3 回起動して観測した事実を持ち越したもので、Issue #22 では測り直していない。1 つのデバイスの中のコアどうしの間にメモリの分離があるという文書は無く、MIG に相当するハードウェアの分割機能も Neuron については文書化されていない。

| プール | 割り当ての単位 | 異なるチームが同じプールを同時に使って安全か (デバイスは 1 つずつ丸ごと 1 つの Pod に渡す) |
| --- | --- | --- |
| `g5` / `g6e` / `g7`(MIG 無しの NVIDIA) | GPU 1 個 | 安全(1 度に 1 チームのみ。デバイス自体はチーム間で分けない) |
| 単一デバイスの Neuron(`trn2.3xlarge`) | Neuron デバイス 1 個 | 安全 |
| 複数デバイスの Neuron(`trn2.48xlarge`、静的な DRA プール) | Neuron デバイス 1 個 | 安全 |
| 将来の MIG 対応プール(`p4d` / `p5` 系) | MIG インスタンス 1 個 | 候補だが、ここでは決めない。NVIDIA の DRA ドライバは現時点では GPU 丸ごとの割り当てさえ公式サポートとして文書化しておらず([5 章](./05-device-plugin-and-dra))、MIG の割り当てはさらにその先なので、対応するプールが実在し文書化されるまで対象外 |

この表の「安全」は、割り当ての単位が丸ごとのデバイスであることから導いた設計上の判断であり、チーム間で試した結果ではない。本書で実際に起動した NVIDIA の型は `g7.2xlarge` だけで、`g5` と `g6e` はこの基盤ではまだ起動していない。`trn2.48xlarge` の静的な DRA プールも、#19 の実験 E6 として計画されたまま実施されておらず、静的な DRA プールが動いたのは 7 章の `trn2.3xlarge` 1 台だけである。チップの名前と MIG の可否は、Issue #22 が NVIDIA の文書から引いたものである。

1 つのプールは、常にちょうど 1 つのリソース名(`aws.amazon.com/neuron` と `aws.amazon.com/neuroncore` を同時にではなく、どちらか一方)を割り当ての単位として使う設計にし、同じシリコンが 2 つの名前で二重に数えられることを防ぐ。device plugin 自体は上で見たとおり両方の名前を公開するので、どちらで数えるかはプールごとに Kueue のクォータと要求の書き方で固定する、という意味である。この設計では、チーム間で分け合う単位は `aws.amazon.com/neuron` (デバイス 1 個) とし、`aws.amazon.com/neuroncore` はチーム間で分け合う単位としては提供しない。また、同じデバイスを device plugin と DRA ドライバが二重に広告すると、2 つの Pod に同じデバイスが警告なしに渡ることが実測されている (6 章の E1 で同じ GPU UUID、7 章で同じ `/dev/neuron0`)。この章の「1 つのデバイスを 2 つのチームが同時に持たない」という前提は、二重広告が無いことを前提にしており、それを見つける検査 (5 章の決定 1 の検査) はまだ実装されていない。

time-slicing や MPS は、使うとしても、1 つのチーム自身の `ClusterQueue` と namespace の中だけで、別の名前のリソースと別名の `ResourceFlavor` として設定する。リソース名まで分けるのは、丸ごとの GPU と分割した GPU のクォータを Kueue が混同しないためである。time-slicing と MPS は、そのチーム自身の、互いを信頼し合うワークロードのための選択肢であり、信頼し合っていない 2 つのチームが 1 つのデバイスを分け合う手段として提供することはない。どちらの仕組みも、この章が要求する「きれいな分離」が必要とするメモリの分離を与えないからである。

## ネットワークの分離

クラスタ全体で守るべき不変条件、つまり「チーム間の通信を全部拒否する」「どのチームの namespace もシステムの namespace に到達できないようにする」は、管理者が持つ 1 つの `ClusterNetworkPolicy` に置き、どのチーム自身の `NetworkPolicy` よりも先に評価され、チームの `NetworkPolicy` では上書きできないようにする。この `ClusterNetworkPolicy` は `networking.k8s.aws/v1alpha1` の Admin tier であり、VPC CNI 1.21.0 以降が必要である(2026-11-18 時点の最新は 1.23.3)。`NetworkPolicy` と `ClusterNetworkPolicy` のどちらも VPC CNI 自身が強制するので別の admission 層は要らないことを、AWS のドキュメントで確かめた。

ここで、`NetworkPolicy` オブジェクトのうち、どれを管理者が持ち、どれをチームが持つかを、1 か所で揃えて決めておく。これは一度、章をまたいで食い違った書き方をしてしまった箇所で、独立したレビュー(2026-11-18)で見つかった。

- **管理者が持つもの** (1 番目の決定の「枠」の中、チームのコミットが届かない Git のパス): チームの namespace ごとに、既定で全拒否(`podSelector: {}` と両方の `policyTypes` から組む)の基準となる `NetworkPolicy` を 1 つだけ置く。これは、チームが何も書かなくても namespace が全開放のままにならないための土台である。
- **チームが持つもの** (チーム自身のワークロードの経路): この基準の上に積み重ねる、追加の `NetworkPolicy` をいくつでも書ける。DNS、Kubernetes の API サーバー、個別に審査したチーム固有の外向き通信などである。`NetworkPolicy` のルールは加算的(許可だけを積み重ねる形で、拒否を書けない)なので、チームが誤ってすべて許可するポリシーを書いてしまっても、それが広げられるのはチーム自身の、もともと開いていた通信の範囲だけである。AWS のドキュメントは「namespace 側のポリシーは Admin tier をより厳しくすることしかできず、Admin tier の拒否を上書きできない」と明記しており、この一文は曖昧さが無い。この章の安全性の主張は、この「拒否は常に勝つ」という部分だけに乗せている。同じ文書は「Admin tier の許可に一致した通信は、それ以降の評価が行われない」とも書いており、これは「namespace 側のポリシーが許可済みの通信をさらに絞れる」という記述と字面のうえで両立しづらい。この食い違いは AWS の文書そのものに残っている以上、この章では無理に解消せず、拒否に関する部分だけを根拠として使う。

チームが追加の `NetworkPolicy` を書けるようにしたことで、1 つだけ塞いでおく穴がある。Argo CD の `AppProject` の許可・拒否の一覧は種類(kind)単位で働き、オブジェクトの名前では区別しない。つまり、チームが管理者の基準ポリシーと同じ名前の `NetworkPolicy` を自分の `Application` から出せば、Argo CD はどちらが持ち主かを決められず、最後に同期した側が実体を書き換えてしまいかねない。これを防ぐため、基準ポリシーの名前(たとえば `baseline-deny`)そのものを admission バンドルで予約し、その名前への作成・更新・削除は、管理者自身の GitOps のサービスアカウントからのものだけを許す。

正直に書いておくべき残る論点もある。Admin tier が確実に守るのは「チーム間を拒否する」「システムへの到達を拒否する」の 2 点だけである。それ以外の、たとえばチームの既定の外向き通信を内側に絞っておくといった、クロスチームとは関係ない衛生上の制約は、管理者の基準ポリシーの中にしか無く、チーム自身の追加ポリシーの書き方次第でその制約は失われる。これは今回の整理で新しく生まれた弱点ではなく、この章がもともと「チーム自身の通信のための道具」と呼んでいた設計にそのまま付随する性質である。チームをまたいで必ず守られてほしい制約は、基準ポリシーだけに頼らず、Admin tier の側に書く必要がある。

もう 1 つ、`NetworkPolicy` にできないことを 2 つ、明示しておく。1 つは、IMDS(インスタンスメタデータサービス)へのアクセス制限である。Pod が動いているノード自身への通信(そのノード自身が持つ `169.254.169.254` のリスナーを含む)は、Kubernetes の `NetworkPolicy` の仕様上、IP ブロックによる判定の対象から除外されている。この問題は、別の仕組み(IMDSv2 の強制とホップ制限 1)で閉じる。ただし基盤のレイヤーがすでに持つこの保証はシステムノードグループに限られ、Karpenter が起動するテナントのノードについては、後の H10 のとおり、すべての `EC2NodeClass` に明示して初めて閉じる。もう 1 つは、sharedfs の Lustre のマウントである。これはノードレベルの CSI バインドマウントであり、Pod のネットワーク名前空間を通る通信ではないため、チームの `NetworkPolicy` は何の影響も与えない。ディレクトリの境界は、sharedfs 自身のマウント時の強制(別の issue、[Issue #18](https://github.com/littlemex/distai-platform/issues/18))という、別の仕組みの役目である。

## `karpenter-tenant-pools` との緊張関係をどう解くか

`karpenter-tenant-pools` の中心的な発想、つまり namespace スコープの CRD からの `NodePool` の自己申請は、この基盤には**採用しない**。理由は 3 つある。第一に、[2 章](./02-four-decisions)がすでに、容量やプールの管理のための独自 CRD を作らないと決めている。テナント向けに同じ目的の CRD を別に作ることは、理由なくその決定を蒸し返すことになる。第二に、自己申請によるプール作成は、この章が抱えている問題をそもそも解決しない。プールは既定で共有されるべきものであり、プールの作成自体は([3 章](./03-capacity-pools))も容量の購入自体も([9 章](./09-verification-operations))も、すでに管理者が一貫して持っている。新しい形の容量が必要なチームは、Capacity Block を頼むのと同じように、管理者にプールの追加を頼めばよい。第三に、`karpenter-tenant-pools` 自身がまだ早期段階(`v1alpha1`)のプロジェクトであり、この基盤自身の原則([1 章](./01-principles))がそのまま当てはまる好例になっている。「このリポジトリの中だけで通じる独自の層を作らない」という原則は、裏を返せば、汎用の仕組みは独立した OSS として外に置いたままでよい、ということでもある。自己申請によるプール作成が本当に必要になったときに、改めて検討すればよい。

採用するのは、コードそのものではなく、パターンである。namespace から導いた、テナントが自分で書き換えられない値に taint を結びつけ、それを `ValidatingAdmissionPolicy` で、境界をまたぐ toleration と直接の `nodeName` 指定の両方について検査する、という組み合わせである。これはコントローラがクラスタの状態を所有するものではなく、ポリシーオブジェクトにすぎないので、これを取り入れても独自 CRD の決定を蒸し返すことにはならない。この基盤自身の判断基準(「`awsome-distributed-ai` の利用者が、この部分だけを持ち出して使えるか」)にも当てはまる形、つまり値だけを渡せば単体で使えるポリシーのパッケージとして、この基盤自身の GitOps の経路に書く。

`karpenter-tenant-pools` の運用の記録から、そのまま持ち越すべき注意も 1 つある。ワイルドカードな toleration を持つ `DaemonSet`(device plugin、CNI、メトリクスのエージェント、CSI の node plugin など)が動く namespace は、admission の対象から除外する必要があるが、その除外を与える権限そのものが、境界全体を無効化できる力を持つ。だからこの除外の対象は、変更できない namespace の名前で選び、短く保ち (設計の初稿では、`kube-system` とデバイスを広告するコンポーネントの namespace だけとしていた)、チーム側の Git のパスからは絶対に広げられない、管理者が持つ「枠」の一部として扱う。

この除外とは別に、ノード専有プールの taint そのものへの対応も要る。`karpenter-tenant-pools` の検証では、実機のクラスタで NVIDIA の device plugin(GPU Operator)の DaemonSet の toleration を読み、既定で `nvidia.com/gpu`、`vpc.amazonaws.com/efa`、`capacity-reservation` しか tolerate せず、チームの taint は tolerate しないことが確かめられている。その検証ではノードを起動しておらず、そこから導かれる帰結として、この状態でノード専有プールを起動すると、device plugin の Pod がそのノードに乗れず、GPU がノードの利用可能リソースとして公開されないため、GPU を要求するワークロードは起動されたノードの上で永久に `Pending` のままになると見込まれる。この基盤は GPU Operator を使わず個別の chart を入れる (#19 の決定 4) が、DaemonSet の既定の toleration がチームの taint を含まないという問題は同じ形で残ると考えられ、Neuron の device plugin と DRA ドライバ、EFA の device plugin、CSI の node plugin、メトリクスのエージェントも同じ確認の対象になる。ワイルドカードの `operator: Exists` を device plugin の toleration に加えても、`ValidatingAdmissionPolicy` 自身がテナントキーへのワイルドカード toleration を拒否するので解決しない。各チームの値を `Equal` で device plugin の DaemonSet に列挙しても通らない。バンドルが求めるのは「値が Pod 自身の namespace と一致すること」なので、device plugin の namespace で動く Pod がチームの値を tolerate すると、値が namespace と一致せず拒否される (`karpenter-tenant-pools` の文書がこの挙動を説明している)。残る道は、device plugin が動く namespace を admission の除外の対象に含めることであり、除外された namespace ではワイルドカードの toleration も通る。上で述べた「ワイルドカードは拒否される」は、この除外をしていない状態での話である。

## ストレージの共有

ここは sharedfs(別の issue、[Issue #18](https://github.com/littlemex/distai-platform/issues/18))が提供する仕組みをそのまま使い、この章では再決定しない。この章が追加するのは、その使い方の約束である。

- チームごとのチェックポイント用ディレクトリは、1 つの `FilesystemShare` に `path: "checkpoints/{namespace}"` と `provision`(作成する uid/gid/mode)を設定し、チームのラベルに一致する namespace へ `ReadWrite` を許可することで、各チームのディレクトリが最初の claim のときに自動的に作られる。親ディレクトリ(`checkpoints`)自体は root が所有し、グループやその他に書き込み権限を与えない形で、あらかじめ用意しておく必要がある。管理者が手でチームごとに `PersistentVolume` を書くことはない。
- 読み取り専用で共有するデータセットやモデルの重みは、データセットのツリーごとに 1 つの `FilesystemShare` を用意し、`ReadOnly` を広く許可する。これにより、どのチームも同じバイト列をコピー無しで読む。
- quota(容量制限)は、Lustre 2.15 でのみ使える Lustre project quota で、quota を使う namespace は Pod Security `restricted` で動く必要がある。この基盤の既定の Pod Security レベルは `baseline` である(`hostPath`、`hostNetwork`、特権コンテナを閉じるという、この章の脅威モデルが実際に依存している部分はこれで閉じる)。`restricted` はさらに厳しいが、EFA や MPI を使う学習イメージの多くが必要とする capability を禁止してしまう。ただし quota は共有の `FilesystemShare` ごとの設定であり、チームだけに閉じた選択ではない。同じファイルシステムのどこか 1 つの share に quota を設定した時点で、そのファイルシステムの全 share の読み書き claim が `restricted` の namespace でなければ `UnsupportedClaim` になる(`--quota-requires-restricted=false` で外せるが、その場合は root squash が前提になる)。`baseline` のままでは root プロセスがファイルの project ID を書き換えて quota を迂回できる、という事実も踏まえ、quota を 1 つのチームが使い始める判断は、同じファイルシステムを使う他チームにも影響することを前提に、管理者側で調整する。
- quota にはこのほかにも実務上の注意がある。50 MiB の上限を設定しても、Lustre は各ストレージターゲットへまとまった単位ごとに quota を割り当てるため、sharedfs 自身の実測 (README と設計文書の M22) では 53〜76 MiB まで書き込みが進んでから止まった。sharedfs が作成した後のディレクトリしか自動では project ID が付かず、既存ディレクトリは管理者が `lfs project` で手動タグ付けするまで新規 claim が待たされる。
- sharedfs 自身がまだ早い段階の OSS であることも前提に入れる。共有ストレージの Issue 自体([#18](https://github.com/littlemex/distai-platform/issues/18))がまだスケジュールされておらず、CRD は `v1alpha1` でバージョン間の変換を持たない。Karpenter のコールドスタート時の挙動、コントローラのアップグレードとロールバック、1,000 件を超える claim、`PERSISTENT_2` 型の Lustre 2.15 は、sharedfs 自身がまだ検証していないと明記している領域である。quota の実測も、`SCRATCH_2` 型の Lustre 2.15 での結果にとどまる。
- チームごとの S3 アクセスは、共有された Pod Identity のロール(2 番目の決定)のリソース側のポリシーが、ABAC の条件でそのチーム自身の prefix だけに絞る。「1 つの共有リソースを、チームごとにスコープされたアクセスで使う」という、FSx のパターンと同じ形である。
- チーム側の使い方はごく薄い。share ごとに `<share>-rw` と `<share>-ro` という 2 つの `StorageClass` が自動的にできるので、チームは通常の `PersistentVolumeClaim` でこれを指定するだけでよい。Pod はそのディレクトリのグループの一員として書き込む必要があり、`runAsGroup` か `supplementalGroups` でグループを指定する(`fsGroup` はこの種のボリュームには効果が無い)。grant がどの namespace を許可するかを選ぶラベルは、管理者の「枠」側が持ち、チーム自身が書き換えられる位置には置かない。
- Kueue との組み合わせには 2 つの注意がある。1 つは、claim が `NotGranted` のままの Job でも Kueue にはすでに admit されてクォータを抱えてしまう、という問題で、sharedfs 自身が文書化している対処(`waitForPodsReady` を設定する。これは Kueue 全体で 1 つの設定であり、管理者が持つセル全体のタイムアウトで、チームごとに調整するものではない)をそのまま使う。なお、後の「実験の計画と受け入れ基準」に出てくる 15 分という値は、sharedfs の権限取り消しの猶予 (`--revocation-grace-period`) の既定値であり、`waitForPodsReady` の値ではない。もう 1 つは、Kueue が素の Pod まで管理する設定にした場合、sharedfs のコントローラ自身が動く `sharedfs-system` と `sharedfs-quota` の 2 つの namespace を `managedJobsNamespaceSelector` の対象から外す必要がある。

## 可観測性とコスト帰属

メトリクスのエクスポータ(DCGM や `neuron-monitor` など)に、チームという概念を教える必要は無い。エクスポータは、これまでと同じように namespace と Pod を出力し続け、チームへの帰属は、問い合わせの時点で namespace を手がかりに結びつける。なお、これらのエクスポータは、`karpenter-tenant-pools` の節で、admission の除外が要る DaemonSet の「メトリクスのエージェント」として触れたものである。これは、AWS の EKS 向け Split Cost Allocation Data が、まさに同じ結びつけ方(`aws:eks:namespace` などのタグ)でコストを帰属させている方法と揃っている。この機能のデータの元は Container Insights ではなく、Cost and Usage Report のエクスポートである。Pod 単位で計測し、`aws:eks:cluster-name`、`aws:eks:namespace`、`aws:eks:workload-name` などのコスト配分用タグを自動で生成する(アクセラレータ搭載インスタンスでは、Pod の稼働時間ごとにアクセラレータ分の利用量レコードも別に作られる)。すべてのチームがすでに 1 つの namespace を持つ(1 番目の決定)ので、チームごとのコストは、このタグに対する問い合わせであり、別のラベル付けの仕組みを並行して維持する必要は無い。

## 基盤がすでに保証していること、ここで足すこと

次の表は、基盤([8 章](./08-foundation))がすでに保証していることと、この章がその上に足すことを、行ごとに対応づけたものである。

| 基盤([8 章](./08-foundation))がすでに保証していること | この章が足すこと |
| --- | --- |
| システムノードの IMDSv2 の強制とホップ制限 1 | `CriticalAddonsOnly` とチームの taint を、実際に効く境界にする admission のバンドル |
| ノードの IAM ロールに CNI の権限を持たせず、`aws-node` は Pod Identity で認証情報を得る | チームのワークロード用の、共有かつ ABAC でスコープされた Pod Identity ロールと、チームごとの関連付け |
| `NetworkPolicy` の強制だけは有効にし、ポリシー自体はまだ無い状態 | 実際の `NetworkPolicy` と `ClusterNetworkPolicy` |
| Argo CD の、クラスタ全体に効くロール割り当て(ADMIN / EDITOR / VIEWER) | チームごとの `AppProject` と、その `roles[].groups` |
| (まだ扱っていない) | チームごとの namespace、quota、`LocalQueue`(1 番目の決定)、チームごとのアクセスエントリ(2 番目の決定)、`Cohort` / `ClusterQueue` の構成(4 番目の決定)、デバイス共有の規則(5 番目の決定)、ストレージとコスト帰属の約束(8、9 番目の決定) |

## 想定される迂回への対策

ここまでの決定を、1 人のチームメンバー(自分の namespace の中だけの、ごく普通の権限しか持たない立場)の視点から、意図的に敵対的に検討し直すと、具体的に成立してしまう迂回が見つかる。この検討は Issue #22 の机上のレビューであり、実機で試したものではないが、理論上の弱点ではなく、書いたとおりの設計のままなら通ってしまうと判断した経路なので、1 つずつ名前を付けて塞ぐ。

| # | 迂回 | なぜ通るか | 対策 | 関係する決定 |
| --- | --- | --- | --- | --- |
| H1 | ラベルを付けない Pod や Deployment が、アクセラレータの taint だけを tolerate してクォータを使う | Kueue の Job 管理の仕組みは、既定ではすべての種類のオブジェクトを捕まえない | Kueue の Pod 統合を有効にし、`manageJobsWithoutQueueName` と `managedJobsNamespaceSelector` でチームの namespace を対象にする。`DaemonSet` という種類そのものをテナントの namespace に作らせない | 3、4 |
| H2 | `priorityClassName: system-cluster-critical` を名乗る Pod が、Kueue を経由せずに他チームの Pod を preempt する | どの `PriorityClass` を使えるかを制限していなかった | チームの `ResourceQuota` に `PriorityClass` の `scopeSelector` を加え、システム用と、管理者が定義したその他の高優先度クラスをまとめて禁止する | 1、3 |
| H3 | 他チームの namespace を指す `podAntiAffinity` を、ゾーン全体を対象にして書き、そのゾーンに他チームが一切スケジュールできなくする | admission のバンドルが toleration と `nodeName` しか検査していなかった | affinity / anti-affinity の `namespaceSelector` または `namespaces` が自分の namespace の外を指す場合を拒否し、テナントの Pod の anti-affinity の `topologyKey` はホスト名の単位に限定する | 3 |
| H4 | アクセラレータの taint を tolerate するだけで、実際にはアクセラレータを要求しない Pod で、ノードを占有し続ける | taint の tolerate と、実際の資源要求が結びついていなかった。加えて、見直し前の対策は `karpenter.sh/do-not-disrupt` をテナントに一律で禁止しており、これは [3 章](./03-capacity-pools) が複数ノードの EFA 学習に要求する内容と正面から衝突していた。独立したレビュー(2026-11-18、Karpenter 自身のドキュメントで裏取り)で、さらに 2 点が分かった。1 つは、Issue #17 が推論に求めているのは「`PodDisruptionBudget`」であって `maxUnavailable: 0` のものではなく、こちらは最初から衝突していなかったことである。もう 1 つは、`karpenter.sh/do-not-disrupt` は Karpenter 自身の自発的な破壊(consolidation や drift)しか止めず、Kueue 自身の reclaim(preemption)には何の効果も無いことである(Karpenter の文書に「この挙動は Karpenter の判断ロジックに限る」と明記されている) | アクセラレータの taint を tolerate する Pod は、対応する資源を実際に要求していないと拒否する (Layer 2 の Pod は `resources.limits` ではなく ResourceClaim で要求するので、この検査は claim による要求も要求として数える必要がある)。この検査は見直し前の対策から変えていない。`karpenter.sh/do-not-disrupt` は、Pod が名乗る `kueue.x-k8s.io/queue-name` が、管理者が `-rsv` だけの `LocalQueue` を作るたびに生成する一覧に載っているときだけ許す(「Kueue をすり抜ける経路を閉じる」の節で述べた仕組み)。`-flex` に届く queue を名乗る Pod にはどれも許さない。チームが名乗りを偽っても、Kueue 自身のフレーバーと `nodeSelector` を結びつける仕組み([3 章](./03-capacity-pools))により、実際に乗る物理プールは `ClusterQueue` が列挙するものに固定されるので、被害はそのチーム自身の `-rsv` の取り分を無駄にするだけに留まる。`maxUnavailable: 0` の `PodDisruptionBudget` は、どのテナントの namespace でも例外なく禁止する(#17 のどのワークロードの型も、永久に破壊を許さない PDB は必要としていない)。一方、ふつうの(ゼロではない)PDB は、推論が本来必要としているものなので、どのチームにも常に許す。あわせて、セレクタが空(`{}`)で、意図しない別のワークロードの型の Pod まで保護してしまう PDB も拒否する | 3、4 |
| H5 | `activeDeadlineSeconds` を設定しない、または `SIGTERM` を無視し続けるワークロードが、借用したクォータを長時間抱え込む | Cohort の `reclaimWithinCohort` の既定値は `Never` であり、名前を出すだけでは効かない | `reclaimWithinCohort` と `borrowWithinCohort` を明示的に設定し、`LimitRange` で `terminationGracePeriodSeconds` に上限を設け、バッチ的なワークロードには `activeDeadlineSeconds` を要求する | 4 |
| H6 | 絶対に動かない `nodeSelector` を持つ Job を大量に出し続け、`waitForPodsReady` のタイムアウトのたびにクォータを使い続ける | Fair Sharing は既定で無効、`borrowingLimit` を設定しなければ無制限、優先度は自己申告 | Cohort 全体で Fair Sharing を有効にし、各チームの `ClusterQueue` に有限の `borrowingLimit` を設定し、使える `WorkloadPriorityClass` を制限し、再試行の回数に上限を設ける | 4 |
| H7 | 自分の namespace で全許可の `NetworkPolicy` を書き、ポリシーの無い他の namespace(メトリクス収集用など)にまで到達する。`externalIPs` を他チームの Pod の IP に設定し、通信を奪う | `NetworkPolicy` は加算的で拒否を表現できず、ポリシーの無い namespace はどこからでも届く | 管理者の `ClusterNetworkPolicy`(Admin tier)で、除外していないすべての namespace(メトリクス収集用を含む)への、テナントからの通信を拒否する。テナントが作る `Service` の `spec.externalIPs` は admission で拒否する | 6 |
| H8 | sharedfs の元になる `PersistentVolume` の名前が公開リポジトリから見えるので、`claimRef` による予約が外れていれば直接 bind して全体をマウントする | sharedfs 自身がすでに文書化している `SourceUnreserved` の検査と同じ話だが、この章で繰り返さないと見落とされる | 元になる `PersistentVolume` は、管理者専用の namespace の claim に `claimRef` で予約してから `FilesystemShare` に参照させる。テナントの Pod にはインラインの一時 `csi` ボリュームも許可しない | 8 |
| H9 | ターゲットを絞っていない(open な)オンデマンドキャパシティ予約は、同じインスタンスタイプ・同じアベイラビリティーゾーンの、どのオンデマンド起動にも消費されてしまう | AWS 側のマッチングの既定が open であり、Kueue も Kubernetes も関与しない | ノード専有のプールを支える予約は、必ず対象を絞った(targeted)形で作る。open な予約は `-rsv` の「枠は予約の実数」という前提も崩し、Karpenter v1.15 はそもそも open な ODCR を拾わない (3 章) ので、貸し出し可能なプールの予約も同じく targeted にすべきだと考えられる (Issue #22 は専有プールについてだけ書いている) | 3 |
| H10 | Karpenter が起動するテナントのノードが、システムノードグループの起動テンプレートを自動的に継承すると誤解していた | `EC2NodeClass` はシステムノードグループの起動テンプレートとは別に定義される | 管理者の GitOps の経路から配る、すべての `EC2NodeClass` に、`httpTokens: required` と `httpPutResponseHopLimit: 1` を明示する。これを新しいプールごとの受け入れ基準とする | 3 |
| H11 | チーム自身の `AppProject` が `applications, *` を持つせいで、自分自身の `ResourceQuota` や `Namespace` のラベルを定義している `Application` 自体を編集・削除できてしまう。種類の許可が緩いままだと、他チームの `ClusterQueue` を指す `LocalQueue` や `RoleBinding` をチームが自分で作ることもできてしまう | 管理者の「枠」とチームのワークロードが、同じ Argo CD のプロジェクトに混在していた。見直し前の対策は `NetworkPolicy` も拒否リストに含めていたが、これはネットワークの節と H7 が前提にしている「チーム自身が `NetworkPolicy` を書く」という設計と正面から食い違っていた | 「枠」の `Application` は管理者スコープの `AppProject` が所有し、チームの `AppProject` は `CreateNamespace=false` とし、`ResourceQuota`、`LimitRange`、`LocalQueue`、`ClusterQueue`、`RoleBinding`、`AppProject` を `namespaceResourceBlacklist`(拒否リスト)に含める。ネットワークの節で述べたとおり、**`NetworkPolicy` はこの拒否リストに意図的に含めない**。チームが自分の `NetworkPolicy` を追加で書いても、境界をまたぐ通信を開き直せないのは Admin tier の `ClusterNetworkPolicy` の役目であって、Argo CD の種類の許可とは別の仕組みだからである。管理者が持つ基準の `NetworkPolicy` だけは、同じ名前での書き換えから admission バンドルで別途守る(ネットワークの節)。チームの `AppProject` はワークロードの種類と、自分の追加の `NetworkPolicy` だけを作れる | 1、2、6 |

同じ机上の検討で、設計どおりの admission のバンドルに照らして、すでに保たれていると判断したことも記録しておく。`DaemonSet` を専有プールに向けても、カスタムの taint への自動的な toleration は付かないので通らない。`nodeSelector` や affinity だけでは、対応する toleration が無ければノードに乗らない。`spec.nodeName` の直接指定は作成時に拒否される。Pod Identity の認証情報がチームをまたいで漏れる経路は見つからなかった。taint が付いたノード専有のプールは、他チームの `ClusterQueue` から最初から参照されないので、Cohort を通じた借用の対象にもならない(3 番目と 4 番目の決定の間に見えた一見の矛盾は、この「貸し出し可能」と「専有」を同時に成立させない、という規則で解消する)。

admission のバンドル自身の実装についても、具体的な注意が 2 つ要る。`ValidatingAdmissionPolicyBinding` の `validationActions` は常に `Deny`、`failurePolicy` は常に `Fail` にする(`Warn` や `Audit`、`Ignore` は使わない)。toleration の `value` のような、存在しないことがある項目を CEL で読むときは、必ず `has()` で存在を確認してから読む(存在しない項目を読んでエラーになり、それが許可側に落ちてしまう事故を避ける)。

### まだ解けていないものを、正直に書く

すべての指摘にきれいな対策があるわけではない。ここは解けていないリスクとして、そのまま書いておく。

- 新しく作られた Pod は、CNI のポリシーエージェントが `NetworkPolicy` を反映するまでの短い間、到達可能なままになりうる。これは強制の仕組み自体が持つ性質であり、この設計では取り除けない。
- sharedfs の quota は Lustre 2.15 でのみ、かつ Pod Security `restricted` の namespace でのみ有効である。スループットそのものを制限する仕組みは無いので、あるチームの持続的な大量の入出力が、他チームの体感速度を落とすことはありうる。これは sharedfs([Issue #18](https://github.com/littlemex/distai-platform/issues/18))の時点での範囲であり、この章で解決するものではない。
- 正しく 1 個の GPU を要求して受け取った Pod でも、`NVIDIA_VISIBLE_DEVICES=all` を自分で設定すれば、NVIDIA Container Toolkit の設定によっては、そのノードの他の GPU まで見えてしまう場合がある。これを閉じるには、Pod が自分で設定した環境変数を信用しない動作モード(CDI によるデバイスの注入など)で Toolkit 自体を設定する必要があり、これは [5 章](./05-device-plugin-and-dra) の、ノードのイメージとデバイス広告側の設定の話であって、この章の Kubernetes API レベルの対策では閉じられない。
- 1 つの IAM Identity Center の permission set を 2 つのチームのグループに結びつけてしまえば、両チームが同じロールと同じアクセス権を持つことになる。これは Identity Center を運用する側の規律の問題であり、クラスタ内のどのオブジェクトでも強制できない。
- 同じ名前の namespace を再利用しても、sharedfs 側の記録(`FilesystemShareDirectory`)が残っている間は `NamespaceRecreated` として拒否され、前のチームのチェックポイント用ディレクトリをそのまま引き継ぐことはない。引き継ぎが起きるのは、管理者がこの記録を消した後である。そのため、チームを抜けさせる手順には、namespace を消すだけでなく、この記録をいつ・どう消すか(ディレクトリ内のデータをどう扱うか)の判断も含める。
- チームが、実際には複数ノードの EFA 学習ではないワークロードを、`-rsv` だけの学習用 `LocalQueue` に向けて出せば、`karpenter.sh/do-not-disrupt` の許可を手に入れられてしまう(H4)。Kueue 自身のフレーバーと `nodeSelector` を結びつける仕組みにより、被害はそのチーム自身の `-rsv` の取り分を無駄にするだけに留まり、他チームや共有の `-flex` プールには及ばない。ただし、ワークロードの「見た目」(ガングスケジュールされているか、EFA を使っているか、複数ノードか)が、名乗っている `LocalQueue` と実際に一致しているかどうかまでは、オブジェクト 1 つずつしか見られない `ValidatingAdmissionPolicy` では確かめられない。これは解けていないリスクとして残す。
- Admin tier の `ClusterNetworkPolicy` が確実に守るのは「チーム間を拒否する」「システムへの到達を拒否する」の 2 点だけであり、それ以外の衛生上の制約は、チーム自身が追加する `NetworkPolicy` の書き方によっては、そのチーム自身の namespace の中で失われうる(理由と位置づけはネットワークの分離の節で述べたとおりである)。

## 実験の計画と受け入れ基準

これまでの決定を実機で確かめる実験は、この基盤がすでに持つ検証の段階([9 章](./09-verification-operations)の `cpu-only` と `light`)に沿って進める。`cpu-only` は承認なしで進められる段階 (Issue #22 は、どちらの段階でも新しいクラスタは要らないとしている)、`light` は `g5` / `g6e` / `g7` / `g7e` / `trn2.3xlarge` の spot または on-demand を使う段階である。

主な確認項目は次のとおりである。

- チームをまたぐ toleration、ワイルドカードの toleration、直接の `nodeName` 指定が、貸し出し可能なプールとノード専有のプールの両方で admission により拒否されることを確かめる。
- 少なくとも CPU と、1 つのアクセラレータ(Neuron を含む)の `ResourceFlavor` で、1 つの平らな `Cohort` の中での借用と、実際の取り分(ゼロではない `nominalQuota`)を持つ `ClusterQueue` による `reclaimWithinCohort: Any` の回収が、借りている側の優先度やチームに関係なく働くことを、Kueue 自身の admission のイベントと `flavorsUsage` から確かめる。あわせて、`Cohort` とその配下の `ClusterQueue` のどちらにも同じ容量が二重に数えられていないことを確かめる。
- 1 つのチームが、学習用とバッチ用で別の `ClusterQueue`/`LocalQueue` を持ち、それぞれに [3 章](./03-capacity-pools) が定めたフレーバー順が実際に効くことを確かめる。
- ラベルの無いワークロードが、スケジュールされるのではなく保留になることを確かめる。
- `karpenter.sh/do-not-disrupt` が学習用の queue では admission され、バッチ用の queue では拒否されることを確かめる。`maxUnavailable: 0` の `PodDisruptionBudget` がどの namespace でも拒否され、セレクタを絞った通常の PDB は admission されることも確かめる。
- namespace スコープのアクセスエントリが、1 つの namespace だけを許可し、他の namespace への呼び出しを拒否することを確かめる。
- sharedfs 越しの他チームのディレクトリへの読み取りが失敗し、取り消された権限では新しいマウントが文書化された猶予期間(既定 15 分)のうちに止まり、すでに動いている Pod のマウント自体はその Pod が止まるまで残ることを確かめる。
- チーム自身の許可的な `NetworkPolicy` が、チーム間の通信を再び開かないことを確かめる。
- 前節の H1 から H11 までの迂回のすべてが、実際に試して拒否されることを確かめる。まだ解けていないリスクについては「解けた」と主張しない。

上流の対応状況に関するすべての主張には、出典(一次情報源、実測、または「未確認・推測」という明示)が付いていることも、受け入れ基準の 1 つである。

## まとめ

この章は、1 つのセルを複数のチームで分け合うための、権限とリソースの分離を設計した。管理者がセルと GitOps の枠組みを持ち、チームは自分の namespace の中だけで自分のワークロードを持つ、という役割分担は保ったまま、GPU や Neuron のノードプールと、共有の FSx for Lustre ファイルシステムは複数チームでの利用を前提にした。チームの状態は、クラウド側の小さな Terraform ルートと、クラスタ内の Argo CD の経路に分けて置き、さらにクラスタ内の経路を、管理者が持つ「枠」とチーム自身が持つワークロードの経路に分けた。身元は、IAM Identity Center のグループ単位のアクセスエントリ、1 つの共有 Pod Identity ロールと ABAC、チームごとの `AppProject` の 3 本で持つ。スケジューリングの境界は、taint と `ValidatingAdmissionPolicy` の組み合わせで作るが、`karpenter-tenant-pools` の自己申請の仕組みそのものは採らず、そのパターンだけを一般化した形で取り入れた。アクセラレータの共有は、Cohort を通じた時間方向の共有であり、1 つの物理デバイスを複数チームが同時に使わない設計にした (ただしこれは、device plugin と DRA の二重広告が無いことを前提にしており、二重広告があれば 6 章と 7 章の実測のとおり警告なしに破れる)。そして、1 人のチームメンバーの立場からの敵対的な検討によって見つかった、具体的に通ってしまう迂回を 11 個、名前を付けて対策を定めた。この章自体はまだ提案であり、実機でこれらの対策を確かめる実験は、前の節で計画した段階にとどまっている。見直しの中で、`NetworkPolicy` をどちらが持つかの食い違い(ネットワークの節と H11)、`karpenter.sh/do-not-disrupt` の一律禁止と [3 章](./03-capacity-pools) の要求の衝突(H4)、`Cohort` の `nominalQuota` をゼロにする設計が `-rsv` の保証を崩していた点、1 チーム 1 `ClusterQueue` ではワークロードの型ごとのフレーバー順を表せなかった点の、4 つの矛盾を見つけて解消した。いずれも Kueue・Kubernetes・VPC CNI の一次情報で裏取りしたうえで書き直している。

上流のドキュメントの確認で裏付けた部分、他の章の実験 (主に `g7.2xlarge` と `trn2.3xlarge` の 1 リージョンずつ) から持ち越した部分、まだ検証していない部分は、この章の中でそのつど区別して書いた。残っている開かれた問い(MIG 対応プールでのデバイス共有、`karpenter-tenant-pools` の自己申請が将来必要になるかどうかの再検討、NVIDIA Container Toolkit の設定レベルでの対策など)は、次の実験と、必要になれば新しい issue に委ねる。

## 参考資料

- [Kueue ClusterQueue](https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/)
- [Kueue LocalQueue](https://kueue.sigs.k8s.io/docs/concepts/local_queue/)
- [Kueue Cohort](https://kueue.sigs.k8s.io/docs/concepts/cohort/)
- [Kueue Preemption](https://kueue.sigs.k8s.io/docs/concepts/preemption/)
- [Kueue Dynamic Resource Allocation](https://kueue.sigs.k8s.io/docs/concepts/dynamic_resource_allocation/)
- [Kueue: Manage jobs without a queue name](https://kueue.sigs.k8s.io/docs/tasks/manage/enforce_job_management/manage_jobs_without_queue_name/)
- [Amazon EKS access policies](https://docs.aws.amazon.com/eks/latest/userguide/access-policies.html)
- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [Amazon EKS Pod Identity ABAC](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-abac.html)
- [Amazon EKS CNI NetworkPolicy](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy.html)
- [AWS Split Cost Allocation Data](https://docs.aws.amazon.com/cur/latest/userguide/split-cost-allocation-data.html)
- [Amazon EKS Best Practices Guide, multi-tenancy](https://aws.github.io/aws-eks-best-practices/security/docs/multitenancy/)
- [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [Kubernetes ValidatingAdmissionPolicy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)
- [Argo CD AppProject](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/)
- [Argo CD RBAC](https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/)
- [NVIDIA GPU sharing](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html)
- [NVIDIA MPS](https://docs.nvidia.com/deploy/mps/index.html)
- [NVIDIA MIG supported GPUs](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-gpus.html)
