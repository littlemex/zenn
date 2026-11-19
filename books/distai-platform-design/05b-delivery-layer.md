---
title: "配送層: 空のセルに Argo CD が何をどう届けるか"
---

## はじめに

[2 章](./02-four-decisions)は、Terraform がセルの外枠だけを作り、中身は全部 Argo CD が届けるという形を決めた([Issue #17](https://github.com/littlemex/distai-platform/issues/17))。[5 章](./05-device-plugin-and-dra)は、device plugin と DRA のどちらを使うかを、NodePool とデバイスクラスの組に貼るラベルとして扱うと決め、そのラベルも同じ Argo CD の経路で届くことを前提にした([Issue #19](https://github.com/littlemex/distai-platform/issues/19))。[3b 章](./03b-multi-tenancy)は、チームごとの `Application` を、管理者が持つ「枠」とチーム自身が持つワークロードの経路に分けると決めた([Issue #22](https://github.com/littlemex/distai-platform/issues/22))。これらの章はいずれも、Argo CD が「Karpenter 本体、Kueue、1 つの NodePool、1 つのデバイス広告、1 つのチームの `Application`」を実際にどう届けるかを、前提として素通りしている。この章([Issue #25](https://github.com/littlemex/distai-platform/issues/25))が扱うのは、まさにこの前提そのもの、つまり空の状態で `apply` されたセルから、Karpenter や Kueue が動くセルへと、何がどの順で届くのかという配送の機構である。

この章では、節番号をそのまま決定番号として使う(「2 節」と「決定 2」は同じものを指す)。決定を比較の表で示さない節(5 節、7 節など)でも、他の節からその節の内容を指すときは同じ番号で呼ぶ。

この章は、基盤層([8 章](./08-foundation))から具体的で未解決のギャップを引き継いでいる。`infra/foundation` は `aws_eks_capability` という EKS のマネージドな Argo CD capability と、そのための IAM ロールを作るが、デプロイ先のクラスタを 1 つも登録しない。AWS 自身のドキュメントは、このマネージドな capability が「ローカルのクラスタも含めて、どのクラスタも自動では登録しない」と明記しており、capability のロール用のアクセスエントリには既定で Kubernetes の RBAC が何も付かないとも書いている。PR #21 からそのまま作ったセルは、動いてはいるが空の Argo CD コントロールプレーンを持ち、登録済みのクラスタはゼロ、自分自身への権限もゼロという状態になる。Karpenter も Kueue も 1 つの NodePool も、何かがこのギャップを閉じるまで届かない。この章は、[Issue #17](https://github.com/littlemex/distai-platform/issues/17)の「独自 CRD を作らない」という決定と、[Issue #19](https://github.com/littlemex/distai-platform/issues/19)の「Terraform はセルの外枠だけ」という決定を、どちらも再び開かずにこのギャップを閉じる、最小の機構を組み立てる。

## 1. 鶏と卵を閉じる: セル自身の登録

セルを `apply` した直後、Argo CD capability は動いているが、登録済みのクラスタはゼロで、自分自身のアクセスエントリにも権限が無い。何かがこの RBAC を与え、登録用の `Secret` を作ってからでなければ、Argo CD は何も届けられない。

| 選択肢 | 何か | なぜ | 決定 |
| --- | --- | --- | --- |
| (a) Terraform の Kubernetes provider がすべてのオブジェクト(登録用の `Secret` を含む)を作る | 1 回の `terraform apply` だけで配送まで完了する | Kubernetes provider と、その認証情報、apply 時にクラスタへ到達できることが新たに要る。[Issue #17](https://github.com/littlemex/distai-platform/issues/17)と[Issue #19](https://github.com/littlemex/distai-platform/issues/19)がどちらも AWS API だけに絞っていた Terraform の state に、初めてクラスタ内オブジェクトが現れてしまう | 不採用 |
| (b) メンテナが `argocd cluster add` や `kubectl apply` を手で実行し、README に書くだけにする | 新しい道具を一切増やさない | 実行したかどうか、どのクラスタに対して実行したかの記録が残らない | 不採用 |
| (c) 小さく冪等なスクリプト(`gitops/bootstrap/bootstrap.sh`)を、最初の bootstrap のときと、決定 6 のチャートレジストリ追加のときに再実行する。この 1 点だけ必要な AWS API 呼び出しは Terraform 側に移す | Terraform は AWS API だけ、Argo CD は Git だけという、[Issue #17](https://github.com/littlemex/distai-platform/issues/17)と[Issue #19](https://github.com/littlemex/distai-platform/issues/19)が引いた境界線をそのまま保てる | アクセスポリシーの関連付け(`aws_eks_access_policy_association`)自体は AWS API 呼び出しであって Kubernetes オブジェクトではなく、[3b 章](./03b-multi-tenancy)がすでに Terraform 管理にしている身元の資源と同じ種類である。Terraform はこれを capability と同時に 1 回作ればよく、クラスタの内側に書き込む 3 つのオブジェクトだけをスクリプトに残す | **採用** |
| (d) ルートの `Application` 自身が `platform` の `AppProject` を管理し、レジストリ追加を PR だけで済ませる | スクリプトの再実行というケースを消せる | `kubectl apply` は、存在しない `AppProject` を指す `Application` を受理してしまい、同期されないまま残るので、誰かが最初の `AppProject` を書く必要は消えない。これは決定 6 の「後からの再実行」だけを無くす話であり、`Application` が自分自身のスコープを決める `AppProject` 自体を広げてしまう点が、スクリプトによる書き込みという管理者だけの操作よりも弱い | 不採用 |

Terraform 側で `infra/foundation/argocd.tf` に足すのは、capability のロールを `AmazonEKSClusterAdminPolicy` に結びつける `aws_eks_access_policy_association` を 1 つだけである。`AmazonEKSEditPolicy` と `AmazonEKSAdminPolicy` はどちらも `CustomResourceDefinition`・`ClusterRole`・`ClusterRoleBinding`・`Namespace` の作成権限を持たず、このロールは Karpenter・Kueue・各 add-on を横断してこれらを作る必要があるため、クラスタ全体に対するこの広い権限を選んだ。実際のテナント境界は、この権限の範囲ではなく、[3b 章](./03b-multi-tenancy)のチームごとの `AppProject` と admission バンドルの役目である。

<!-- FIG name="05b-bootstrap-flow" kind="flow" shows="AWS APIの側(terraform applyが作るaccess-policy association)とクラスタ内側の境界、bootstrap.shの2つの事前チェック(kubeconfigの一致、既存Secretのcluster_arnの一致)、bootstrap.shが書く3つのオブジェクト(Secret, AppProject, root Application)の順序、その先にplatform ApplicationSetとcells ApplicationSetの2つがあること" labels="terraform apply, aws_eks_access_policy_association, bootstrap.sh, 2 checks before write, cell-registration Secret, platform AppProject, root Application, platform ApplicationSet, cells ApplicationSet" -->
![Terraform と bootstrap.sh が順に作るものを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/05b-bootstrap-flow.png)

`bootstrap.sh` が必須で取る入力は `--tf-outputs <(terraform output -json)` の 1 つだけであり、個別のフラグをいくつも渡す形は採らない(値が古くなったまま使われる事故を避けるため)。任意の `--revision`(既定は決定 6 で導入する `stable`)は、`stable` タグがまだ存在しない期間だけ上書きする。書き込むオブジェクトは常に 3 つで、すべて capability の namespace に置く。1 つ目は登録用 `Secret` で、名前は固定で `cell-registration`、`argocd.argoproj.io/secret-type: cluster` のラベル、`server: <cluster_arn>`、Cluster generator が `{{name}}` として公開する `stringData.name: <cluster_name>`、`distai.dev.annotations/revision` などの注釈を持つ。2 つ目は `platform` の `AppProject`(決定 7 で述べる)である。3 つ目はルートの `Application` 1 つで、`spec.project: platform`、ソースは `gitops/appsets/` にする。書き込みの前に、`--tf-outputs` から取った 2 つの確認(`kubeconfig` のサーバーが `cluster_endpoint` と一致すること、既存の `cell-registration` Secret があればその `cluster_arn` と一致すること)を、最初の bootstrap でも再実行でも必ず行い、一致しなければ何も書かずに中断する。3 つの書き込みはすべて `kubectl apply` による冪等な操作であり、決定 6 のレジストリ追加や `--revision` だけの再実行は、この同じ仕組みで安全に繰り返せる。

ここで、他の章の記述との間に、まだ解消していない食い違いを 1 つ明示しておく。[Issue #25](https://github.com/littlemex/distai-platform/issues/25)自身は「この基盤の常設の構成(standing topology)は、セルごとに 1 つの Argo CD capability を持ち、各セルが自分自身だけを登録することであり、この Issue はセルをまたぐハブ・アンド・スポーク型の登録を作らない」と明記している(鉤括弧内は原文からの訳)。これは、[8 章](./08-foundation)と[3b 章](./03b-multi-tenancy)の既存の記述が使う「ハブのセル」(複数のチームのセルを束ねる管理用のセル)という言い方と、文字どおりには両立しない。8 章・3b 章の記述の元になった Issue 自身の本文がこの語をどう使っているかは、この章では確認していない。帰結を 1 つ指摘しておく: `argocd` 変数を設定しないセルには、この章が組み立てる機構は何も届かない。この最初の PR と e2e が実際に裏付けたのは、セルが自分自身を登録する形だけであり、どちらの位置づけを正とするかは、この章では決めない。

## 2. セルの事実をチャートの値に渡し、1 つの `ApplicationSet` で複数のセルを捌く

| 選択肢 | 何か | なぜ | 決定 |
| --- | --- | --- | --- |
| (a) add-on ごとに `Application` を手書きする | generator が要らず、明示的 | 新しいセルを作るたびに既存セルの YAML をコピーすることになり、独自 CRD を避けるために[Issue #17](https://github.com/littlemex/distai-platform/issues/17)が退けた「独自の仕組み」が別の形で復活する | 不採用 |
| (b) add-on ごとに 1 サブチャートを持つ、1 枚の umbrella Helm チャート | `Application` の数が 1 つに減る | add-on ごとの同期・健全性の状態と、個別の rollback が 1 つに潰れ、[Issue #19](https://github.com/littlemex/distai-platform/issues/19)が決めた NodePool 単位の blue/green 移行が成立しなくなる | 不採用 |
| (c) **Cluster generator** と **Git file generator**(add-on ごとの小さなメタデータファイル。裸のディレクトリではない)を組み合わせた Matrix generator を持つ `ApplicationSet` | 登録用 `Secret` の注釈を読み、add-on ごとの `addon.yaml` と掛け合わせる | Git の *ディレクトリ* generator が公開するのはパス(`path`、`path.basename`)だけで、チャート名・バージョン・wave は公開しない。Git の *ファイル* generator は小さな YAML/JSON ファイルの中身そのものをテンプレートのパラメータとして公開でき、add-on ごとの `chart`・`version`・`wave` を渡すのにちょうど合う | **採用** |

テンプレート化の仕組みは、両方の `ApplicationSet` が ApplicationSet 自身の `templatePatch` によるテンプレート化を使い、1 つの add-on だけが必要とする値(`karpenter` の `settings.clusterName` など)を他の全テンプレートに強制しない形にする(`templatePatch` は Go テンプレートが有効なときに使う機能であり、この章で `{{name}}-{{addon}}` のように示す記法は、どの値がどこから来るかを示す模式的な表記であって、実際の YAML の記法をそのまま書き起こしたものではない)。各 `addons/<name>/addon.yaml` が Git file generator 自身のパラメータファイルであり、`addon:`(Cluster generator がすでに持つ `name` とは意図的に別の名前)、`chart`/`repoURL`/`version`、`wave`、`namespace`、セル固有ではない静的な `values:` を持つ。`skipCrds` と `syncOptions` は `values:` の中ではなく兄弟のキーとして置き、`Application` 自身の `spec.source.helm.skipCrds` / `spec.syncPolicy.syncOptions` に直接効かせる。`valuesFrom:` の一覧は、1 つの Helm の値のパスを、`Secret` の注釈キーか Matrix generator のパラメータのどちらかに結びつける。生成される `Application` の `metadata.name` は `{{name}}-{{addon}}` という形にし、将来 1 つの capability が複数のクラスタを登録する場合に備えるが、現時点のこの構成ではまだ衝突を起こさない。

両方の Cluster generator は `argocd.argoproj.io/secret-type: cluster` への `selector.matchLabels` を持つ。これは任意の最適化ではない。セレクタが無いと、Cluster generator は組み込みの local/in-cluster エントリ(登録用 `Secret` も注釈も無く、この capability が対応しない `kubernetes.default.svc` という宛先を持つ)も対象にしてしまうため、このラベルで除外する。

<!-- FIG name="05b-generator-matrix" kind="relation" shows="登録用SecretからCluster generatorへ、add-onごとのaddon.yaml/セルごとのパラメータファイルからGit file generatorへ、2つがMatrix generatorで掛け合わされてApplicationが生成される経路" labels="cell-registration Secret, Cluster generator, addon.yaml, Git file generator, Matrix generator, Application" -->
![Cluster generator と Git file generator を組み合わせる経路を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/05b-generator-matrix.png)

登録用 `Secret` は、セル自身の事実を注釈として運ぶ。

| キー | 出典 | 使い先 |
| --- | --- | --- |
| `distai.dev.annotations/revision` | `bootstrap.sh` の `--revision` の値 | 両方の `ApplicationSet` 自身の Git file generator の `revision:` フィールド、各 `cells` 生成 `Application` 自身の `targetRevision`(6 節で述べるバージョン管理) |
| `distai.dev.annotations/region` | Terraform の `region` 出力 | `karpenter` の `controller.env`(`AWS_REGION`。IMDS のホップ制限 1 のため、暗黙のままにはできない) |
| `distai.dev.annotations/karpenter-interruption-queue` | Terraform の `karpenter_interruption_queue_name` 出力 | `karpenter` の `settings.interruptionQueue` |
| `distai.dev.annotations/karpenter-node-role` | Terraform の `karpenter_node_role_name` 出力 | `cells` 側の `capacity-pool` リリースの `EC2NodeClass.spec.role` |

`targetRevision` 自体は、`platform.ApplicationSet.yaml` / `cells.ApplicationSet.yaml` のどのコミットを適用するかしか選ばず、どちらのファイルの Git file generator 自身の `revision:` フィールド(すでにコミットされた別の設定)は選ばない。そのため、そのフィールドは `{{metadata.annotations.'distai.dev.annotations/revision'}}` を読む形にし、Matrix の中で後に来る Git file generator が、前にある Cluster generator の値を読む(生成者が先、消費者が後という順序の規則)。

アクセラレータクラスを示すラベルを `Secret` に持たせる案は検討したが見送った。あるセルがそのアクセラレータを持たなければ、対応する `capacity-pool` の NodePool はそもそも存在せず、広告用の `DaemonSet` 自身も、対応するノードが無ければ何もスケジュールされず費用もかからない。そのため、すべてのアクセラレータ広告(決定 5)は無条件にすべてのセルへ届ける。セル固有のオブジェクト(AZ や Capacity Block/ODCR の ID を持つ `capacity-pool` リリース、チームの枠組み)はこの形では表せず、`gitops/cells/<cluster-name>/capacity-pool/*.yaml` と `.../smoke/*.yaml` という明示的なグロブの下に置く。

## 3. リポジトリの構成

| 選択肢 | 何か | なぜ | 決定 |
| --- | --- | --- | --- |
| (a) 1 枚の平らな `gitops/` ディレクトリ | 最も単純な木 | 管理者専用の不変条件と、add-on ごとの値、チームのパスをディレクトリだけでは区別できない。これは[3b 章](./03b-multi-tenancy)の決定 1 がチームのオブジェクトについて既に退けた、持ち主の弱さと同じ形である | 不採用 |
| (b) sync wave の番号で名付けたディレクトリ(`00-crds/`、`10-controllers/` など) | 順序がパスに見える | 1 つの wave の番号を変えるだけで、ディレクトリ名と、それを指すすべての参照を変える必要が出る | 不採用 |
| (c) 持ち主と種類でディレクトリを分ける: `bootstrap/`、`appsets/`、`addons/`、`cells/`。wave や手順の名前は各 add-on 自身の `addon.yaml` の中の値としてのみ持つ | 「何であるか」と「いつ適用するか」を分離できる | wave の番号振り直しは値の 1 行変更で済み、ファイル移動を伴わない | **採用** |

```text
gitops/
  bootstrap/
    bootstrap.sh
    root-application.yaml
  appsets/
    platform.ApplicationSet.yaml   # Matrix(Cluster, Git FILE over addons/*/addon.yaml)
    cells.ApplicationSet.yaml      # Matrix(Cluster, Git FILE over cells/{{name}}/{capacity-pool,smoke}/*.yaml)
  addons/
    karpenter-crd/addon.yaml   karpenter/addon.yaml   kueue/addon.yaml
    accel-nvidia-device-plugin/addon.yaml   accel-nvidia-dra/addon.yaml
    accel-neuron-device-plugin/addon.yaml   accel-neuron-dra/addon.yaml
    admission-bundle/addon.yaml   sharedfs/addon.yaml   notifications/addon.yaml
  charts/
    capacity-pool/   # 3章の共有チャートテンプレート
    smoke/           # この章自身の小さなチャート(Namespace+ClusterQueue+LocalQueue)
  cells/
    <cluster-name>/
      capacity-pool/<pool>.yaml
      smoke/queue.yaml
      teams/<team-name>.yaml   # 3b章の入口(wave: teams)
```

この章の generator が読むファイル(`addons/<name>/addon.yaml`、`cells/*/capacity-pool/*.yaml`、`cells/*/smoke/*.yaml`)は、どれも Git file generator 用のパラメータファイルであり、生の manifest ではない。`appsets/`、`bootstrap/`、`addons/`、`charts/`、`cells/*/teams/*`([3b 章](./03b-multi-tenancy)が実装された後)は `CODEOWNERS` で管理者保護とし、`cells/*/capacity-pool/` と `cells/*/smoke/` も同様に管理者保護とする(プールは管理者が買う容量であり、smoke フィクスチャもこの章自身が持つもので、どちらもチームのものではない)。

## 4. 順序

Argo CD 自身の kind 順序(CRD を CR より先に適用する)は、1 つの `Application` の*内側*での順序はすでに扱う。これは Argo CD 自身のドキュメントが明記している振る舞いである。残るのは、1 つの Matrix generator が生成する多数の `Application` の*間*の順序であり、Argo CD の ApplicationSet のドキュメントによれば、これに使える仕組みは Progressive Syncs の `RollingSync` 戦略だけである。このドキュメントは、`RollingSync` が 1 つの `ApplicationSet` 自身の `spec` に属し、その同じ `ApplicationSet` が生成した `Application` しか順序付けないとも明記しており、2 つの別々の `ApplicationSet` をまたいで順序付けることはできない。マネージドな capability がこの `RollingSync`(Argo CD v3.3.0 以降の Beta 機能)自体を受け付けるかどうかは、この章を書いている時点では未確認である。

| 選択肢 | 何か | なぜ | 決定 |
| --- | --- | --- | --- |
| (a) `ApplicationSet` が生成した `Application` に `sync-wave` の注釈だけを付ける | 単純な注釈 | 独立した `Application` どうしをまたいで協調しないことが確認済みである | 単独の仕組みとしては不採用 |
| (b) すべての add-on を、手書きの親 1 つの子 `Application` として app-of-apps にする | 子の sync-wave は親の同期で尊重される | 決定 2 が既に退けた、セルごとのコピー&ペーストの問題が復活する | 不採用 |
| (c) generator の形ごとに 1 つの `ApplicationSet`(`platform`、`cells`)を持ち、それぞれが自分自身の生成した `Application` だけを順序付ける `RollingSync` を持つ。2 つの `ApplicationSet` をまたぐ順序は、保証されたものではなく再試行による収束とする | `platform.ApplicationSet.yaml` は `crds` → `controllers` → `accelerators` → `extras`、`cells.ApplicationSet.yaml` は `capacity` → `teams` という手順を踏む | add-on ごとに 1 つの `ApplicationSet` を作るよりは少なく、(d) よりも文書化された保証(generator の形ごとに実際の順序保証)を持つ | **採用** |
| (d) 1 つの `ApplicationSet` の中に、両方の Matrix generator をトップレベルの別エントリとして置き、6 手順すべてを 1 つの `RollingSync` で順序付ける | オブジェクトの数が最も少ない | `RollingSync` が使える場合でしか勝たず、使えない場合は (c) と同じ再試行に落ちる。1 つの generator のエラーがもう一方の generator の `Application` を止めるかどうかは文書化されておらず、(c) が持つ 2 つの独立したオブジェクトという保証を、未確認の挙動に賭ける形で手放す | 不採用 |

| 手順 | `ApplicationSet` | 内容 | この順序が閉じる依存 |
| --- | --- | --- | --- |
| `crds` | `platform` | `karpenter-crd` | `controllers` の `karpenter` が `NodePool`/`EC2NodeClass` を監視する前、`cells` の `capacity` がこれらの種類を作る前に必要。`karpenter` 自身のチャートも同じ CRD を `crds/` フォルダで持つため、`karpenter` の `Application` は `helm.skipCrds: true` を設定し、両者が同じ CRD を二重に持たないようにする |
| `controllers` | `platform` | `karpenter`、`kueue` | どちらも `CriticalAddonsOnly` を tolerate し、システムノードグループを対象にする。Kueue の `Application` は `syncOptions: [ServerSideApply=true]` を設定する |
| `accelerators` | `platform` | `accel-nvidia-device-plugin`、`accel-nvidia-dra`、`accel-neuron-device-plugin`、`accel-neuron-dra`、`admission-bundle` | 各アクセラレータ広告は NodePool とデバイスクラスの組へのラベル([5 章](./05-device-plugin-and-dra))ごとに opt-in する。[3b 章](./03b-multi-tenancy)自身の admission バンドルは、同じ `platform.ApplicationSet` の後ろの手順である `extras` より先に置けば十分である。`cells` の `teams` 手順との間は別の `ApplicationSet` をまたぐので、順序ではなく再試行ベースの依存のままにする。きれいに適用できるチームの枠組みは再試行を一度も必要としないので、この手順が `teams` より先であることまでは求めない。その依存を閉じる必要が実際にあるかどうかは[3b 章](./03b-multi-tenancy)自身の課題として残る |
| `extras` | `platform` | `sharedfs`、`notifications` | クラスタ全体向けで、スケジューリングをブロックしない |
| `capacity` | `cells` | `cells/{{name}}/capacity-pool/*`、`cells/{{name}}/smoke/*` | `NodePool`/`EC2NodeClass` は `crds` の `karpenter-crd` に、`ResourceFlavor`/`ClusterQueue`/`LocalQueue` は `controllers` の `kueue` に依存する。どちらも `platform` への再試行ベースの依存であり、`crds` だけでは満たされない |
| `teams` | `cells` | `cells/{{name}}/teams/*` | 同じ `cells.ApplicationSet` の中の `capacity` より後に置くことで `ClusterQueue` への参照だけを閉じる |

<!-- FIG name="05b-ordering-steps" kind="flow" shows="platform.ApplicationSetのRollingSync手順(crds→controllers→accelerators→extras)とcells.ApplicationSetの手順(capacity→teams)、2つのApplicationSetをまたぐ依存は再試行ベースであること" labels="platform.ApplicationSet, crds, controllers, accelerators, extras, cells.ApplicationSet, capacity, teams, RollingSync, retry-based convergence" -->
![2 つの ApplicationSet の手順と、その間の再試行ベースの依存を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/05b-ordering-steps.png)

JobSet、Trainer、LWS、可観測性のスタックは、この章ではどの手順にも割り当てない。まだこれらを届けるどの PR も存在せず、値を持つ issue が無い段階で先回りして割り当てることは、この章がすべきでない種類の内容だからである。

## 5. GPU と Neuron を同じ配送の形にする

[Issue #19](https://github.com/littlemex/distai-platform/issues/19)が決めたのはロジック側の形(メソッドラベル、2 層、メソッドごとに 1 つのチャートリリース)であり、この章が足すのは、配送側の形(ディレクトリ、generator の一致、手順、作られるオブジェクトの種類)もベンダーにかかわらず同じ単位であるということである。

| 単位の部分 | NVIDIA、device plugin | NVIDIA、DRA | Neuron、device plugin | Neuron、DRA(静的) |
| --- | --- | --- | --- | --- |
| `addons/` のディレクトリ | `accel-nvidia-device-plugin/` | `accel-nvidia-dra/` | `accel-neuron-device-plugin/` | `accel-neuron-dra/` |
| チャート | `nvidia-device-plugin` v0.20.2 | `dra-driver-nvidia-gpu` v0.5.1 | `neuron-helm-charts` v1.11.2 | 同じチャート |
| opt-in する `capacity-pool` の NodePool ラベル | `distai.dev/nvidia-gpu-method: device-plugin` | `distai.dev/nvidia-gpu-method: dra` | `distai.dev/neuron-method: device-plugin` | `distai.dev/neuron-method: dra` |
| 手順 | `accelerators` | `accelerators` | `accelerators` | `accelerators` |

この表がそのまま残す非対称が 2 つある。Neuron の 2 つのリリースは 1 つの上流チャートに対する 2 つの `values.yaml` だが、NVIDIA の 2 つは別々のチャートである。また、NVIDIA の DRA チャートは device plugin 形の要求からもそのまま使える `DeviceClass` を最初から持つが、Neuron の DRA チャートは持たず、これが[5 章](./05-device-plugin-and-dra)の `DeviceClass` カタログが存在する唯一の理由である。この非対称をどちらかの「アクセラレータチャート」で覆い隠すことは、この基盤の設計原則が禁じるリポジトリ限定の抽象化そのものになるので行わない。同じチャートを 2 回動かすときに両方のリリースが崩してはならない規則が 1 つある。device plugin リリース(既定のメソッド)はチャートのスケジューラ・node-problem-detector・`observer` の各サブコンポーネントを有効化し、DRA リリースはこれら 3 つを無効化して自分自身の DRA ドライバだけを有効化する。

## 6. バージョンのピン留めとアップグレード経路

| 選択肢 | 何か | なぜ | 決定 |
| --- | --- | --- | --- |
| (a) 各チャートの上流ブランチを、ピン留めせずに追跡する | 常に最新 | レビューされていない上流の変更が、次のポーリングで全セルに入ってしまう | 不採用 |
| (b) 各チャートを `addon.yaml` の中で正確なバージョンにピン留めし、プラットフォームのリポジトリ自身の Git ref は、PR ごとにコミット SHA を `root-application.yaml` に書き込む | チャートのバージョンは PR の diff に文字列として現れ、レビュー可能になる | 1 つのコミットは、自分自身の SHA を、その同じコミットの中のファイルに書き込めない。自己参照になり、記述どおりには不可能である | 不採用 |
| (c) 各チャートのバージョンは (b) と同じく PR ごとにピン留めし、プラットフォームのリポジトリ自身の Git ref は、マージ直後に CI だけが動かす 1 つの移動タグ(`stable`)にピン留めする。GitHub のタグ保護ルールセットで、その CI ジョブ自身のアクター以外の書き込みを禁じる | `root-application.yaml` の `targetRevision` はタグの静的な名前になり、SHA ではない | (b) の自己参照の問題を完全に避けられる。タグ保護ルールセットが設定されれば「CI だけが動かす」が実際のものになる(この章を書いている時点で、`stable` タグ自身もこのルールセットも、[10 章](./10-development-process) 6 節が述べる `main` のブランチルールセットと同じく、まだ設定されていない設計上の予定であり、実装済みの事実ではない) | **採用** |
| (d) プラットフォームのリポジトリ自身の Git ref を、直接 `main` にピン留めする | 別のタグも CI ジョブも要らない | `main` への merge は Argo CD の次のポーリングで即座に効き、減速の地点が無くなる。`stable` の再設定も現時点では無条件のまま CI だけが動かすが、将来ここに判定を足せる唯一の場所であり、その再設定の CI ジョブ自体が失敗するか動かなかった場合でも、`stable` は前回の良いコミットを指したまま変わらない(直接 `main` を使う場合は、このように留まる先が無い) | 不採用 |

2 つの追加規則がある。1 つは、`addons/karpenter/addon.yaml` のバージョンを上げる PR は `addons/karpenter-crd/addon.yaml` も合わせて上げる必要があり、これは機械的に検査する。もう 1 つは、通常のチャートのバージョンアップには `bootstrap.sh` の再実行は要らず、CI が `stable` を動かし Argo CD が気づけば反映される一方、新しいチャートレジストリを足す PR([Issue #19](https://github.com/littlemex/distai-platform/issues/19)のアクセラレータ広告、[Issue #18](https://github.com/littlemex/distai-platform/issues/18)の `sharedfs`、[Issue #23](https://github.com/littlemex/distai-platform/issues/23)の通知が、いずれもこれに当たる)は、`platform` の `AppProject` 自身の `sourceRepos` を更新する必要があるため、すでに bootstrap 済みのすべてのセルで再実行が要る。バージョンアップ PR は、[Issue #24](https://github.com/littlemex/distai-platform/issues/24)が定めるアップグレードモード e2e を、1 段上のこの層で、1 つの使い捨てテストセルに対して走らせる設計である。`stable` のコミットから構築し、カナリアを走らせ、そのセルだけのルート `Application.spec.source.targetRevision` と登録用 `Secret` の `revision` 注釈を PR のブランチ先端へ再設定し(共有の `stable` タグ自身は動かさない)、どの `Application` も `Degraded` にならないこと、NodePool 起動済みのノードが置き換わらないこと、カナリアの Pod が `Running` のままであることを確認する設計だが、9 節で見るとおり、この PR 自身の検証では `terraform apply` の時点で止まり、この 3 点そのものは実機ではまだ確認できていない。

## 7. `platform` の `AppProject` と、3b 章への継ぎ目

この章が持つ `AppProject` は `platform` の 1 つだけであり、管理者専用、`spec.sourceNamespaces` は capability の namespace に設定し、`spec.destinations` は capability に登録されたクラスタ、すべての種類のリソースを許可する。`spec.sourceRepos` はこのリポジトリ自身には絞らず、この PR が届けるそれぞれの add-on(`karpenter-crd`、`karpenter`、`kueue`)が、自分自身の上流レジストリから単一ソースで届くため、そのレジストリも列挙する。この一覧は `bootstrap.sh` が自分自身のチェックアウトの HEAD(`--revision` が指すコミットでなければならない)から毎回計算し、手で保守しない。両方の `ApplicationSet` は `platform` を対象とする。[3b 章](./03b-multi-tenancy)のチームごとの `AppProject` は、別の、後から `teams` の手順が届ける対象であり、この章はその中身までは定義しない。

## 8. 削除

| 選択肢 | 何か | なぜ | 決定 |
| --- | --- | --- | --- |
| (a) Argo CD の既定のカスケード削除をどこでも変えない | 追加の設定が要らない | `addons/` のディレクトリを削除するだけの PR が、生きている CRD や、それに依存するすべての `NodePool`/`ClusterQueue` を警告なしに削除してしまう | 不採用 |
| (b) `platform.ApplicationSet` と `cells.ApplicationSet` の両方の `spec.syncPolicy` に `preserveResourcesOnDeletion: true` を設定する | Argo CD 自身のドキュメントがこのフィールドを置く場所そのもの(`AppProject` ではなく `ApplicationSet` 自身) | 生成された `Application` にカスケード削除のファイナライザが付かなくなり、1 つの generator エントリを失っても、そのリソースは削除されずに孤立するだけになる | **採用** |

bootstrap スクリプトが書いた 3 つのオブジェクトを削除しても、NodePool や EC2 インスタンスにはカスケードしない。ルートの `Application` は `resources-finalizer.argocd.argoproj.io` を明示的に宣言しているため、これを削除すると 2 つの `ApplicationSet` にカスケードし、各 `ApplicationSet` の `preserveResourcesOnDeletion: true` が、その先の CRD や NodePool へのカスケードだけを止める。EC2 側には別の仕組みは要らない。基盤層([8 章](./08-foundation))の削除ガードが、Karpenter の IAM ポリシーを切り離してから、タグ付きのインスタンスと起動テンプレートを直接終了させる(Kubernetes 側の `NodePool` が孤立していても構わない)ためである。この章がティアダウンの手順に足すのは 1 行だけで、Terraform の destroy の前に、ルートの `Application`、`AppProject`、`Secret` をこの順で削除する。

## 9. 最初の PR と、実機で見つかったこと

| 選択肢 | 何か | なぜ | 決定 |
| --- | --- | --- | --- |
| (a) この Issue が名指しするすべての add-on を 1 本の PR で届ける | 一度で動く構成になる | 1 本の diff としてはレビューできず、e2e が落ちたときにどの機構のせいかも曖昧になる | 不採用 |
| (b) 機構そのものだけを証明する最小の PR(bootstrap、`crds`/`controllers` の手順、CPU 専用の `capacity-pool` の骨格、アクセラレータ固有やチーム固有の内容は含めない) | 失敗の原因を機構とそれ以外に切り分けられる | [Issue #17](https://github.com/littlemex/distai-platform/issues/17)・[Issue #19](https://github.com/littlemex/distai-platform/issues/19)・[Issue #22](https://github.com/littlemex/distai-platform/issues/22)自身の内容の失敗と、この配送機構自身の失敗が、1 本の PR では区別できなくなる(a)の弱点を避けられる | **採用** |

最初の PR(ブランチ `feat/25-delivery-bootstrap`)は、2 つの Terraform 出力と新しいアクセスポリシーの関連付け、`gitops/bootstrap/`、マージごとに `stable` を動かす CI ジョブ、`crds`/`controllers` だけを持つ `platform.ApplicationSet.yaml`、`capacity` だけを持つ `cells.ApplicationSet.yaml`、`karpenter-crd`/`karpenter`(`skipCrds` 付き)/`kueue`、CPU 専用の最小 `capacity-pool` テンプレート、管理者所有の smoke `ClusterQueue`/`LocalQueue`、そして決定 6 のアップグレード e2e スクリプトを届ける。

実機の検証は、`gitops/tests/0.static.sh`(静的チェック)と `gitops/tests/1.e2e.sh`(セル自体は `us-east-1`。Argo CD capability の RBAC 確認に IAM Identity Center のインスタンスが要るため、08 章の run 8 と同じくこのリージョンを使う。Terraform の state バケットだけは `ap-northeast-1` に置く。`light` の階層、CPU のみ、on-demand のみ)で行った。静的チェックは、`helm lint`/`helm template`、`kubeconform`、そして新しく足した `check_pinned_crds.py`(ピン留めしたチャート自身の CRD と比べて検証する)まで通った。この検査は、修正前のコミット(`kueue.x-k8s.io/v1beta1` をハードコードしていた時点のコード)に対して実行すると、`ResourceFlavor`・`ClusterQueue`・`LocalQueue` のいずれも `v1beta2` しか提供されないという失敗を正しく出すことも別に確かめている。

e2e(`gitops/tests/1.e2e.sh`)がここで言う最後まで通った 2 回に達する前には、下で述べる成果物側 4 件とテストスクリプト側 18 件の大半の欠陥を、それぞれ見つけて直すための、PASS に至らない途中の実行を重ねている。4 件の成果物側の欠陥(`templatePatch` の YAML、`bootstrap.sh` の `sed`、Kueue の toleration、Kueue の API バージョン)は、いずれもこの途中の実行の中で見つかった。この途中の実行のうち 1 回は、この e2e がまず組み立てる基盤層([8 章](./08-foundation))自身の `terraform apply` が、EventBridge のルールを読む呼び出しの一時的なエラーで途中失敗し、[Issue #26](https://github.com/littlemex/distai-platform/issues/26)として基盤層側に起票した削除ガードの欠陥をここで最初に見つけている。最後まで通した 2 回はどちらもスクリプトの判定が PASS で、1 回目は、使い捨てブランチの後片付けに漏れがある欠陥(下で述べる)を見つけて直し、2 回目はその欠陥が直ったことを別に確かめる再実行である。確認した主な項目を表にまとめる。

| 確認した項目 | 結果 |
| --- | --- |
| 基盤層のコード(`feat/20-foundation`)だけを先に `apply` した状態での、capability のロールのアクセスエントリの RBAC | 無い |
| この章自身の変更を加えて再度 `apply` した後の同じ RBAC | `AmazonEKSClusterAdminPolicy` が付く |
| `bootstrap.sh` の 1 回目の実行、同一入力での再実行 | 真の no-op(`Secret`/`AppProject` の `resourceVersion` もルートの `Application` の `spec` も変化しない) |
| `--revision` だけを変えた再実行 | `Secret` の注釈とルートの `targetRevision` が一緒に更新される |
| 別のクラスタの `cluster_arn` を指す `--tf-outputs` を渡した場合 | 何も書かずに中断する |
| 生成された `Application`(`<CELL>-karpenter-crd`、`<CELL>-karpenter`、`<CELL>-kueue`、`<CELL>-capacity-pool-cpu`、`<CELL>-smoke`) | それぞれ `Synced`/`Healthy` になる |
| `karpenter` の配送元 | OCI チャート(`oci://public.ecr.aws/karpenter/karpenter`)から届く |
| `karpenter-crd` と `karpenter` の CRD の所有 | 同じ CRD を二重に所有しない |
| NodePool/EC2NodeClass/ResourceFlavor | `cpu` という名前で 1 つずつ作られ、`nodeLabels['karpenter.sh/nodepool']` で正しく結び付く |
| カナリア `Job` | Kueue が smoke キューを通じて admission し、実際に NodePool `cpu` からノードが起動する |
| `crds` の手順を意図的に壊した場合 | `capacity` 側の新しいオブジェクト作成が止まり、復旧すると再試行の範囲内で回復する(`kueue` を壊した場合も同様で、既に存在する `NodePool` 自体は影響を受けない) |
| bootstrap の 3 つのオブジェクトを決定 8 の順で削除した場合 | NodePool・EC2NodeClass・Karpenter のインスタンスが孤立したまま残り、`bootstrap.sh` を再実行すると同じオブジェクトが(作成時刻を変えずに)再び取り込まれる |
| `terraform destroy` | 手動の手順なしに完了し、インスタンス・ENI・NAT ゲートウェイ・ボリューム・起動テンプレートのいずれも残らない |

`platform.ApplicationSet` の内側の順序(`crds` が `controllers` より先に `Synced` になること)自体を単独で確かめた記録は、この e2e には無い。確かめているのは、`crds` を意図的に壊した場合に `capacity` 側が止まることだけであり、通常時に 2 つの手順がどの順で `Synced` になるかは、この検証の範囲外である。

アップグレード e2e(`gitops/tests/2.upgrade.sh`)は、`check_gitops_contract.py` のバージョン対の検査が `karpenter`/`karpenter-crd` の不整合なバージョン組み合わせを正しく検出することまでは確認できたが、その先の `terraform apply` が、Argo CD capability 自身のアクセスエントリに対する `AssociateAccessPolicy` の呼び出しで `ResourceNotFoundException` を返して失敗した。この e2e は使い捨てのテストセルを 1 から構築する手順で、capability とこの関連付けを同じ `terraform apply` の中で一緒に作る。これに対し、`1.e2e.sh` の本体 2 回の run の中で同じ関連付けの呼び出しが成功しているのは、capability をあらかじめ作った状態に対して、関連付けだけを別の 2 回目の `apply` として足す RBAC の前後比較の手順であり、条件が異なる。したがって、capability と関連付けを同じ 1 回の `apply` で作る経路については、記録されている唯一の試行がこの失敗であり、「同じ関連付けの呼び出しは確実に成功する」とは言えない。この失敗が、AWS 側の結果整合性によるものか、capability とこの関連付けの間に何らかの依存関係を明示しないことによるものかは、この run だけでは判断できていない(時間の枠が尽きたため、同じ run の中での再試行はしていない)。この run 自身の後片付けも、VPC CNI の未使用ネットワークインターフェースと EKS が自動作成したクラスタセキュリティグループに阻まれて完了せず、手動で特定・削除してから `terraform destroy` を再実行した。これは、`apply` そのものが途中で失敗したときに基盤層([8 章](./08-foundation))の削除ガードが一度も作られないという欠陥(別の API の失敗から見つかり、[Issue #26](https://github.com/littlemex/distai-platform/issues/26)として基盤層側に起票済みの欠陥)に、ここでも別の API の失敗を通じて当たったものである。

検証の過程では、届ける成果物側 4 件、テストスクリプト側 18 件の修正が入っている。届ける成果物側は、`templatePatch` の `valuesObject` ブロックの YAML が無効だった点(ブロックスカラーの内側に必要な行頭のインデントを、生成する側の `sed` の置換が付けていなかった)、`bootstrap.sh` の `sed` が区切り文字に `/` を使っていたため `/` を含む `REVISION`(実際のブランチ名はほぼすべて該当する)で壊れていた点、Kueue の controllerManager に `CriticalAddonsOnly` の toleration が無く `Pending` のままだった点(4 節の表はこの toleration を持つ前提で書いているが、最初に届けたコードにはこれが欠けていた)、`capacity-pool`/`smoke` チャートが `kueue.x-k8s.io/v1beta1` をハードコードしていた点(Kueue 0.20.6 は `v1beta2` のみを提供する)の 4 件である。テストスクリプト側は、新しく足した `check_pinned_crds.py` がピン留めしたチャートの CRD を正しく検証できるようにする一連の修正、Kueue の `QuotaReserved`/`Admitted` イベントが `Job` ではなく `Workload` オブジェクトに乗ることへの対応、使い捨てブランチの後片付け漏れ(サブシェル内の変数更新が親シェルに伝わらず、2 つのブランチが削除対象から漏れていた)、ルートの `Application` の実際のオブジェクト名がファイル名と違う点への対応など、18 件である。使い捨ての e2e ブランチ(`refs/heads/e2e/<run-id>/*`)自体は、SSM 経由で発行する一回限りのトークンを `GIT_ASKPASS` で渡し、ディスクにも `git config` にも残さない形にしており、この仕組みを足した後に、ローカルのファイルパスを `origin` として使っていた(push が実際には GitHub に届いていなかった)という別の欠陥も見つかり、修正している。

## まとめ

この章は、[Issue #17](https://github.com/littlemex/distai-platform/issues/17)・[Issue #19](https://github.com/littlemex/distai-platform/issues/19)・[Issue #22](https://github.com/littlemex/distai-platform/issues/22)・[Issue #23](https://github.com/littlemex/distai-platform/issues/23)がそれぞれ前提にしていた「Argo CD がどう届けるか」という共通の機構を定義した。Terraform は、capability のためのアクセスポリシーの関連付けを 1 つだけ追加で作り、残りは `bootstrap.sh` という小さく冪等なスクリプトが、登録用 `Secret`・`platform` の `AppProject`・ルートの `Application` という 3 つのオブジェクトだけをクラスタの内側に書き込む。そこから先は、`platform`(手順: `crds` → `controllers` → `accelerators` → `extras`)と `cells`(手順: `capacity` → `teams`)という 2 つの `ApplicationSet` が、Cluster generator と Git file generator を組み合わせた Matrix generator で、add-on とセル固有のオブジェクトをそれぞれ届ける。GPU と Neuron の配送は同じ単位(ディレクトリ・generator・手順・作られる種類)で扱い、バージョンは各チャートごとにピン留めしつつ、プラットフォーム自身のリポジトリは CI だけが動かす `stable` タグで追いかける。最初の PR の実機検証では、Kueue の API バージョンの不整合、`CriticalAddonsOnly` の toleration 不足、使い捨てブランチの後片付け漏れといった欠陥を見つけて直した一方で、基盤層の削除ガードが `apply` の途中失敗後には動かないという、この章の範囲を超えた欠陥([Issue #26](https://github.com/littlemex/distai-platform/issues/26)、この最初の PR の e2e で、EventBridge のルールを読む呼び出しの一時的なエラーをきっかけに最初に見つかったもの)に、6 節のアップグレード e2e でも別の API(`AssociateAccessPolicy`)の失敗を通じて再び当たっている。6 節が定めるアップグレード e2e 自身の 3 つの確認(`Application` が `Degraded` にならないこと、NodePool 起動済みのノードが置き換わらないこと、カナリアの Pod が `Running` のままであること)は、この `terraform apply` の失敗のため実機ではまだ確認できておらず、`stable` タグとそれを保護するタグ保護ルールセットも、[10 章](./10-development-process) 6 節が述べる `main` のブランチルールセットと同じく、まだ設定されていない設計上の予定にとどまる。4 節が頼る `RollingSync` 自体も、マネージドな capability が受け付けるかどうかは未確認である。また、capability とそのアクセスポリシーの関連付けを同じ 1 回の `apply` で作る経路(新しいセルを 1 から構築する通常の経路そのもの)には、成功した記録が無く、今のところ失敗した記録しか無い。アクセラレータ固有の add-on、チームの枠組み、`sharedfs`、通知の各手順は、いずれもこの最初の PR には含まれておらず、依存する各 issue が自分自身の PR でそれぞれの手順に追加する。
