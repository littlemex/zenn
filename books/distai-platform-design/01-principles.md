---
title: "設計原則: GPU と Trainium を等価に扱う"
---

## はじめに

この章は、本書の以降すべての章が従う設計原則を扱う。原則は 2 本の柱からなる。1 本目は、NVIDIA の GPU と AWS Trainium (Neuron) を等価に扱うという柱である。これは、すべての実装を 1 つに揃えることではなく、揃えられる部分は 1 つの仕組みにし、揃えられない部分は入口の形だけを揃えることを意味する。2 本目は、複数のチームが 1 つのクラスタを安全に共有できることを、後から足す機能ではなく最初から成り立たせるべき制約として扱う柱である。1 本目の柱は、リポジトリの規則の正本である `AGENTS.md` の 5 節 (Design Principles) に書かれ、PR のレビューでは毎回この原則に沿っているかが確認される。2 本目のマルチテナンシーの柱は、本書の執筆時点ではまだ `AGENTS.md` には反映されていない、より新しい方針である。

## 1. GPU と Trainium を等価に扱うとはどういうことか

distai-platform は、AWS の Trainium と Inferentia (Neuron) を、NVIDIA の GPU と等価な、第一級のアクセラレータとして扱うことを目標にしている。ここで「等価に扱う」は、GPU と Neuron のコードを 1 つに統合することを意味しない。意味するのは、利用者から見た基盤の形、つまりノードプールの作り方、容量の確保の仕方、スケジューリングの単位、ストレージと権限の扱い、来歴の記録、probe の枠組みを、アクセラレータの種類を値として受け取る 1 つの仕組みで揃えることである。デバイスプラグインやドライバのような、中身がどうしても別物になる部分は、無理に 1 本化せず、それぞれの実装を保ったまま入口だけを揃える。

この区別を「同じ形 (shape) であって、同じ方法 (method) ではない」と言い換えられる。同じ形とは、GPU 用の仕組みと Neuron 用の仕組みが並んで存在するのではなく、1 つの仕組みがアクセラレータの種類ごとに違う値を受け取るということである。同じ方法ではないとは、2 節の表の右側、つまりデバイスプラグインやドライバのように中身が別物になる部分では、GPU と Neuron が同じコードを実行するわけではないということである。表の左側 (ノードプールや Kueue など) は、文字どおり 1 つの実装であり、GPU と Neuron で同じコードが動く。`AGENTS.md` も左側について「GPU 用の写しと Neuron 用の写しではなく、仕組みは 1 つ」と書いている。なお「入口」という語は、本書では 2 節の右側の部品について、ラベル、ダッシュボードの置き場、probe の出力の形を揃えることを指す。

## 2. 共通にするものと分けるもの

どの部分を共通の仕組みに載せ、どの部分を分けるかは、`AGENTS.md` の 5 節が線を引いている。

| 共通の仕組みに載せる | それぞれの実装を持ち、入口だけ揃える |
| --- | --- |
| ノードプール | デバイスプラグイン (GPU / Neuron) |
| Capacity Block | ドライバとランタイム (CUDA / Neuron SDK) |
| スケジューリング (Kueue) | 性能の指標 (DCGM / neuron-monitor) |
| ストレージと権限 | 集団通信 (NCCL / Neuron の集団通信) |
| 来歴と probe の枠組み | プロファイラ (Nsight / Neuron Profiler) |

左側は、1 節で述べたとおり、アクセラレータの種類を値として受け取る 1 つの仕組みにする。右側は、それぞれが別の実装を持つことを認めたうえで、1 節で定義した入口 (ラベル、ダッシュボードの置き場、probe の出力の形) だけを揃える。ある部品を右側に分類するときは、分けて終わりにせず、その理由を該当する変更の pull request の中で毎回説明することが求められる。

この線引きには、退けられた 2 つの代案がある。1 つは、このリポジトリだけで通じるアクセラレータの抽象化の層を作る案である。入口を 1 つにできるという長所はあるが、そこで使う語彙が他のどこにも持ち出せず、アクセラレータごとの本当の指標や通信の違いを隠してしまう。もう 1 つは、GPU と Neuron でまったく別の仕組みを最初から 2 本持つ案である。互いに縛られないという長所はあるが、共通にできる部分まで 2 回書くことになり、時間が経つごとに少しずつずれていく。採用された案は、この 2 つの中間に位置する、共通化と分離を部分ごとに選ぶ線引きである。

## 3. GPU の世代差も同じやり方で扱う

アクセラレータの違いを「GPU か Neuron か」という二分法で捉えると、GPU の世代が違うだけで生じる差を見落とす。実際には、EFA のカードの数やドライバの世代といった違いは、2 つの GPU の世代の間でも、GPU と Trainium チップの間と同じくらい大きくなりうる。そこで distai-platform は、差をアクセラレータの種類と世代の組ごとに扱う。p5 と p6-b200 のような GPU の世代差も、GPU と Trainium の差と同じ扱いの対象になる。具体的な扱いの単位として、容量の設計 (2 章) は「クラス」を、拡張リソースの名前、1 台あたりのデバイス数、EFA の数が同じ型の集まりと定義している。GPU の世代違いも Trainium も、それぞれ別のクラスとして同じ扱いを受ける。`AGENTS.md` と設計の資料は世代差の大きさを具体的な数値では示しておらず、ここで言えるのは、EFA のカードの数とドライバの世代が違いうるという点までである。

## 4. 独自の抽象化を作らない原則

このリポジトリの中だけで通じる高度な抽象化の層は作らない。汎用に使える仕組みが必要になったときは、その仕組みを独立した OSS プロジェクトとして切り出し、distai-platform はそれを取り込む側に回る。判断の基準はひとつで、「`awslabs/awsome-distributed-ai` の利用者が、この部分だけを切り出して単独で使えるか」である。使えないなら、それはこのリポジトリに焼き込んでよい抽象化ではない。

この基準は、2 章と 3 章で見る容量管理の設計判断にも繰り返し登場する。独自の CRD を作らない判断も、この原則の具体的な適用である。

## 5. アップストリームへの貢献を前提にした設計

distai-platform は、汎用に使える部分をアップストリームの `awslabs/awsome-distributed-ai` の規約に沿う形で作り、貢献できる状態を保つ。当初は Neuron の部分もその対象だったが、のちに貢献の範囲は GPU と汎用の部分に絞られた。アップストリームの `awslabs/awsome-distributed-ai` は、Neuron をあまり中心的には扱っていない。たとえばアップストリームの `architectures/amazon-eks` にある EKS の例は、g4dn、g5、p4de、p5 といった GPU の型だけを対象にしており、Trainium の例を持たない。distai-platform はこの空白を自分たちだけで埋めるのではなく、Neuron 側で作った汎用的な部分をアップストリームの規約やテンプレート、ディレクトリ構成に沿う形で作り、アップストリームに貢献できる状態を保つ方針を取る。`AGENTS.md` 5 節の「Upstream-ready Neuron parts」がこれにあたる。pull request を出すたびに、「この変更はアップストリームに出せるか、出すなら何が要るか」を考えることが求められる。ただし、`AGENTS.md` 7 節が毎回のレビューで確かめると定めているのは、共通の仕組みに GPU 専用の名前や前提が無いこと、Neuron が同じ仕組みで扱われるか分離の理由が書かれていること、このリポジトリ限定の抽象化が無いこと、の 3 点であり、アップストリームに出せるかどうかはこの 3 点には入っていない。

この方針は、その後の判断で範囲が絞られた。アップストリームは Neuron を中心には扱わない方針に近いと判断され、Neuron を前提にした提案は awsome-distributed-ai には出さないことになった。Trainium の部分は distai-platform の中で育て、アップストリームへの貢献は GPU と汎用の部分に限る。`architectures/amazon-eks` に Karpenter、spot、Trainium、DRA を足す RFC も、メンテナの判断で出さないことになった (11 章)。Trainium の部分を distai-platform の中で育てることは、distai-platform という基盤自体の実験でもある。Neuron を公開リポジトリの中心的な対象として扱う設計が、実際にアップストリームの規約に沿う形で成立するかどうかを、distai-platform で確かめている。

## 6. マルチテナンシー: 複数のチームが 1 つのクラスタを共有する

distai-platform が前提とするもう 1 つの制約は、複数のチームが 1 つのクラスタを共有し、それぞれの権限と資源が互いにきれいに分離されていることである。これは後から足す機能ではなく、2 章以降で見る容量管理やノードプールの設計そのものに最初から効いてくる制約として扱う。ただし時系列としては、2 章の容量の設計 (Issue #17) はこの柱が方針として示される前に検討されており、Issue #17 の本文はテナンシーに触れていない。2 章の決定がこの柱と両立するかは、3b 章で改めて当てはめて確かめている。

役割の分け方は単純である。基盤の管理者がインフラそのもの、つまりセル (Terraform が作るクラスタの器)、ノードプール、デバイスの広告者 (device plugin または DRA)、共有ストレージを作る。各チームは、その上に自分のリソースを作り動かす。チームに許されるのは、自分の namespace の中でのセルフサービスであり、`karpenter.sh` のようなクラスタスコープの API に対する書き込み権限は渡さない。この役割分担は、2 章で Terraform が作るものと分けた、Argo CD か人が作るもの (NodePool、ResourceFlavor など) を、なぜチームへそのまま開放できないかを説明する。NodePool や EC2NodeClass はクラスタスコープのリソースであり、これに書き込める権限をチームに渡すと、あるチームが `capacityReservationSelectorTerms` で別のチームの予約を指定し、その予約済み容量でノードを起動できてしまう。EC2NodeClass に書き込めるということは、ノードの IAM ロールや AMI、メタデータの設定まで選べるということでもある。同様に、3 章で見る NodePool と ResourceFlavor の対も、各チームが自由に書くのではなく、管理者が信頼できる何らかの層が生成し、所有する必要がある。

共有そのものも、単に拒否すればよいわけではなく、最初から成立させるべき一級の要件として扱う。高価な資源ほど共有の必要性が高い。GPU では、デバイス全体の割り当てだけでなく、time-slicing、MIG、DRA による共有といった複数の共有方法が、テナントの境界と共存できなければならない。ここで共有の仕組みが特定の GPU だけを前提にしないことも、1 本目の柱からの要求である。なお、この要件は 3b 章で具体化するときに絞られる。time-slicing と MPS は NVIDIA 自身の文書がメモリの分離を持たないとしているので、チームの間で 1 つのデバイスを分け合う手段にはせず、1 つのチームの中だけで使う。MIG は対応するプールが無いので保留である。Neuron の側には MIG に相当するハードウェアの分割が無く、共有の単位はデバイスか NeuronCore になる。

Capacity Block のように 1 回の購入でまとまった量を確保した容量は、1 つのチームに固定されるのではなく、Kueue の Cohort を介した borrowing と reclaim によって複数チームのワークロードに回る必要がある。

共有ストレージも同様で、FSx for Lustre は 1 つの物理ファイルシステムでありながら、チームごとの専用ディレクトリと、複数チームが読み取り専用で使う共有データセットの両方を同時に提供する必要がある。これを実現するのが、この基盤のために自作した OSS プロジェクトの [sharedfs](https://github.com/littlemex/sharedfs) (commit `c19eb1f`) である。distai-platform への sharedfs の組み込み自体はまだ計画段階であり ([Issue #18](https://github.com/littlemex/distai-platform/issues/18))、sharedfs 自体も実装はしているが未検証の項目を残している。管理者は `FilesystemShare` というリソースで、既存の PersistentVolume とその上のパス、そのパスへのアクセスを許す namespace を指定するだけでよく、各チームは通常の PersistentVolumeClaim を使って、あたかも専用のストレージであるかのようにそのパスを取得する。チームがファイルシステム全体のパスを直接扱うことはない。

テナントの分離について毎回確かめる項目も、この柱に含まれる。チームの Pod がノードの資格情報やシステムノードに届かないこと (土台として IMDS の hop 数を 1 にし、CNI の権限は Pod Identity で渡し、NetworkPolicy を強制する)、チームの寿命とセルの寿命を同じ Terraform の state に入れないこと、共有の仕組みが特定のチームや GPU だけを前提にしていないこと、である。

この分離の具体的な実現方法には、すでに実績のある先例がある。ノードプールの側では、[karpenter-tenant-pools](https://github.com/littlemex/karpenter-tenant-pools) という、この基盤とは別の、ごく初期段階 (`v1alpha1`) の OSS プロジェクトが、`AcceleratorClass` と `AcceleratorPool` という 2 つの CRD にクラスタスコープとネームスペーススコープの責務を分ける operator を、以前の作業で実機で動かしている。管理者が所有する `AcceleratorClass` には IAM ロール、AMI セレクタ、サブネットとセキュリティグループのセレクタ、そしてどの namespace がプールを作ってよいかの許可リストと上限を書く。テナントが所有する `AcceleratorPool` には、欲しいアクセラレータの種類、インスタンスファミリ、購入オプション、上限、参照する Capacity Block の ID だけを書く。境界は 2 つの経路で同時に守られる。生成される NodePool には、CR の namespace から機械的に導いた taint が必ず付き、ユーザーの入力からは taint を作れない。それに加えて `ValidatingAdmissionPolicy` が、他のテナントの taint やワイルドカードの toleration を持つ Pod と、`spec.nodeName` を直接指定した Pod の作成を、admission の段階で拒否する。この仕組みには運用上の注意点が 1 つ付きまとう。デバイスプラグインのような system 系の DaemonSet はテナントを問わずすべてのノードで動く必要があるため、ワイルドカードの toleration を持たせたい namespace を除外リストに載せる必要がある。この除外を誰が付与できるかという権限は、境界そのものと同じ重さで管理しなければならない。除外を与える力は、境界を迂回する力でもあるからである。

distai-platform 自身にとって、このテナント分離の層はまだ設計中であり、実装はされていない。上で触れた `karpenter-tenant-pools` と sharedfs はいずれも既存の OSS であり、以前の作業で実機で動いた先例だが、どちらも初期段階で、sharedfs は自身が未検証と明記する領域を残している。distai-platform は sharedfs を取り込む一方で、`karpenter-tenant-pools` の `AcceleratorClass` / `AcceleratorPool` という CRD そのものは採用しない。3b 章が挙げる理由は 3 つある。第一に、2 章の容量の設計 (Issue #17) がすでに、容量やプールの管理のための独自 CRD を作らないと決めており、テナント向けに同じ目的の CRD を作るとその決定を理由なく蒸し返すことになる。第二に、プールは既定で共有されるもので、作るのは管理者であり、チームによるプールの自己申請はこの基盤が抱える問題を解決しない。第三に、`karpenter-tenant-pools` 自身が初期段階であり、汎用の仕組みを独立した OSS として外に置いておくという 4 節の原則のとおりの形になっている。sharedfs が取り込まれるのは、管理者が所有する共有ストレージという、この基盤に必要な役割をそのまま担うからである。採用するのは、namespace から導いた taint と、それを検査する admission policy という組み合わせのパターンだけであり、どう組み込むかは 3b 章で扱う。本章がここで確定させるのは原則そのものである。管理者とチームの役割分担と、高価な資源の共有とを両立させる余地を残さずに下した決定は、あとで作り直すことになる。これが、この柱を最初から置く理由である。

<!-- FIG name="01-tenancy-roles" kind="relation" shows="platform admin owns cell, node pools, device advertisers and shared storage; each team self-serves inside its own namespace without cluster-scoped write access to karpenter.sh or kueue objects" labels="Admin, Terraform shell, NodePool, EC2NodeClass, Device advertiser, Shared storage, Team namespace, AcceleratorPool, PersistentVolumeClaim, Capacity Block cohort" -->
![管理者とチームの役割分担を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/01-tenancy-roles.png)

<!-- FIG name="01-shared-vs-separate" kind="relation" shows="which subsystems become one mechanism parameterised by accelerator type versus which keep separate implementations with aligned entry points" labels="NodePool, Capacity Block, Kueue, Storage, Identity, Provenance, Probe framework, Device plugin, Driver/runtime, Metrics, Collective comm, Profiler, GPU, Neuron" -->
![共通化する部分と分離する部分の対応を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/01-shared-vs-separate.png)

<!-- FIG name="01-abstraction-test" kind="flow" shows="the decision test for a new general mechanism: can an awsome-distributed-ai user take this part and use it standalone; if not, extract it as independent OSS and consume it as a dependency" labels="New mechanism needed, Portable standalone?, Keep in this repository, Extract as independent OSS, Consume as dependency" -->
![独自抽象化を作らない判断の流れを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/01-abstraction-test.png)

## まとめ

distai-platform の設計は、2 本の原則の上に立つ。1 本目は、GPU と Trainium を同じ形で、しかし同じ方法ではなく扱うという原則であり、共通化する部分と分離する部分を部分ごとに選び、独自の抽象化を避け、可能な部分はアップストリームへの貢献を前提に作る。2 本目は、複数チームが 1 つのクラスタを共有しながら権限と資源を分離し、同時に高価な資源を共有できるという制約であり、distai-platform にとってはまだ設計中の層である。次の章では、この 2 本目の制約が強く関わる容量管理の問題を、時間の単位が異なる 4 つの決定として分解していく。
