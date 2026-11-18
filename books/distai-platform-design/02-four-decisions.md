---
title: "容量を混ぜる悩みは四つの決定である"
---

## はじめに

distai-platform が扱う容量は一枚岩ではない。GPU と Trainium、複数のインスタンス世代、spot、on-demand、On-Demand Capacity Reservation (ODCR)、EC2 Capacity Block for ML が、その日に空いているリージョンとアカウントのどこかに散らばっている。この章は、Issue #17 で検討されたこの悩みの正体を分解し、既存の 2 つの実装の限界を確認したうえで、6 つの構成案を比較し、最終的にどの道具にどの決定を任せるかをまとめる。

## 1. 容量を混ぜる悩みの正体: 時間の単位が違う 4 つの決定

「どの容量を使うか」は一度に決まる 1 つの問いではない。実際には、時間の単位がまったく異なる 4 つの決定が積み重なっている。

| 決定 | 中身 | 時間の単位 | 担当 |
| --- | --- | --- | --- |
| D1 買う | どのリージョン、AZ、アカウントで何を予約するか | 日 | 人とスクリプト |
| D2 セルを選ぶ | どのクラスタにジョブを投げるか | ジョブを出すとき | SkyPilot |
| D3 入場させる | そのジョブがどの容量を、どの順で、何台まで使えるか | ジョブを出すとき | Kueue |
| D4 起こす | 実際にどの型、どの AZ、spot か on-demand かでノードを起動するか | 秒から分 | Karpenter |

D2 と D3 はどちらも「ジョブを出すとき」に決まるので、時間の単位としては 3 種類である。D2 と D3 を分けているのは時間ではなく範囲で、D2 はクラスタの間でどこに投げるか、D3 は 1 つのクラスタの中でどの容量を使わせるかを決める。D2 の担当を SkyPilot としたのは推奨構成での割り当てであり、クラスタの選択を SkyPilot と MultiKueue のどちらに任せるかは、後の実験 (Issue #17 の実験 4) で決めることになっている (4 節)。

<!-- FIG name="02-four-decisions" kind="flow" shows="D1 buy (days) to D2 choose a cell (at submission) to D3 admit (at submission) to D4 launch (seconds to minutes), each owned by a different layer" labels="D1 Buy, D2 Choose a cell, D3 Admit, D4 Launch, days, at submission, seconds to minutes, human+script, SkyPilot, Kueue, Karpenter" -->
![4 つの決定とその時間軸を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/02-four-decisions.png)

この 4 つを 1 つの Terraform 変数に押し込むと、何が起きるか。Capacity Block を 1 日単位で買うたびに Terraform を流し直すことになり、Kueue の設定とプールの名前が人手で結ばれている以上、両者は黙ってずれていくと見込まれる。このずれは実際に起きた記録ではなく、構造から見込まれる壊れ方である。一方、distributed-ai の実装では、`accelerator_pools` という 1 つの変数だけで約 380 行、19 個の validation を抱え、意味が同じ新旧のフィールドが 4 組並存していた。これは感覚的な印象ではなく、実際のコードと ADR を読んで数えた事実である。ただし数えた値が示すのは変数の重さであり、新旧のフィールドの並存が時間の単位の混在から生じたという因果までは示していない。

## 2. 今の 2 つの実装の限界

既存の 2 つの実装に共通する限界は、「ジョブが欲しい量」と「今ある容量」を結び付ける層が基盤の外にあることである。Issue #17 の検討では、[`littlemex/distributed-ai` の Terraform + Karpenter](https://github.com/littlemex/distributed-ai/tree/e092c3f54e2ebcee665389cbdfc17348f612c2d3/infra/eks) と、[`awslabs/awsome-distributed-ai` の `architectures/amazon-eks`](https://github.com/awslabs/awsome-distributed-ai/tree/d29964fcf8273fbc14131f9e252a25b08865ebd3/architectures/amazon-eks) (CloudFormation) という、既存の 2 つの実装を読んで比較している。

| 観点 | distributed-ai (Terraform + Karpenter) | awsome の amazon-eks (CloudFormation) |
| --- | --- | --- |
| 容量の種類 | spot、on-demand、予約を 1 つのリストで混ぜ、Karpenter が reserved、spot、on-demand の順に試す | on-demand、ODCR、Capacity Block のうちどれか 1 つを 1 ノードグループに固定する。spot は無い |
| 型の混在 | 1 プールに複数の型を混ぜられる (EFA の構成が同じものに限る) | 1 テンプレートに 1 型である。複数の型を使うには複数回デプロイする |
| Trainium | `device_plugin = "neuron"` を GPU と同じループで扱う | 対応していない |
| スケジューラ | Kueue は入れるが、キューの設定は別のテンプレートにあり、プール名を人が手で結ぶ | 無い |
| 本質的でない複雑さ | 同じ意味を持つ新旧のフィールドが 4 組並存する | ネットワークカードの枚数を `Conditions` の羅列で表現している |

## 3. 構成案の比較: 6 つの選択肢

Issue #17 では、容量の決め方について 6 つの構成案を比較した。比較すると、案の違いは 2 本の軸に分解できることが分かる。1 本目は「実際に決める層はどこか」で、Karpenter、Kueue、SkyPilot のいずれかが該当する。2 本目は「その宣言をどう包むか」で、Terraform の変数、素のマニフェスト、kro、独自の CRD のいずれかが該当する。(a)、(e)、(f) は包み方の違いであり、(b)、(c)、(d) は決める層の違いである。表の「アップストリームに出せるか」の列は Issue #17 の時点の見立てであり、その後、`architectures/amazon-eks` に Karpenter、spot、Trainium、DRA を足す RFC はメンテナの判断で出さないことになった (11 章)。

| 案 | 長所 | 短所 | Trainium | アップストリームに出せるか |
| --- | --- | --- | --- | --- |
| (a) 今の Terraform 変数を延ばす | 1 回の apply で全体が揃う | 複雑さが変数に溜まり続ける。予約を買うたびに apply が要る | 対応可 | 難しい |
| (b) Karpenter を素直に使い、Argo CD で配る | 予約にネイティブ対応する。誰でも読める素の NodePool になる | 誰が予約を先に使うかの順番や枠が存在しない | 対応可 | 高い |
| (c) Kueue を中心にする | 枠、順番、借用、preemption で「高価な容量を兼ねる」が表現できる | spot と on-demand では、入場できても容量があるとは限らない | 公式文書に言及なし | 高い |
| (d) SkyPilot を外側に置く | 利用者の入口が 1 つになる。中断されたら探し直して再起動する | クラスタ間の切り替えは既定で無い | 公式文書に言及なし (メンテナ自身の記事では v0.14.0 で対応するが多くは未検証) | 設定例として出せる程度 |
| (e) kro で「容量」の API を合成する | コントローラのコードを書かずに API を作れる | その API 自体がこのリポジトリだけの抽象化になり、複雑さが CEL に移るだけになる | 対応可 | 低い |
| (f) 独自の CRD とコントローラを書く | 何でも実現できる | 保守と権限の負担が大きく、1 章の「独自の抽象化を作らない」原則に正面から反する | 対応可 | ほぼ不可能 |

比較表の各案には、表に書ききれなかった事実がある。Kueue の `ProvisioningRequest` という容量を先に確かめる仕組みは、公式文書を見ても cluster-autoscaler 向けに作られており、Karpenter への言及が無い。これは 3 章で扱う一番危ない前提につながる。また (d) の SkyPilot は、既存のクラスタの上で動くことが前提で、Kubernetes コンテキストをまたぐ failover は既定では無効であり、`kubernetes.allowed_contexts` はジョブをどのセルに送るかを選ぶための設定である。ODCR と Capacity Block を優先して使う機能は SkyPilot の VM のバックエンドに対するものであり、Kubernetes の上で動かすときには効かない。(b) の Karpenter は、v1.15 の時点で `ReservedCapacity` という feature gate の背後に ODCR と Capacity Block へのネイティブ対応を持ち、予約で起動したノードには `karpenter.k8s.aws/capacity-reservation-id` などのラベルが付く。ただし対象を絞らない open な ODCR への対応は無く、Capacity Block の終了前にはノードが drain される。(e) の kro は、Argo CD と並んで EKS Capabilities というマネージドな形で提供されている。(e) の kro 案を支える ACK の EC2 コントローラは、`CapacityReservation` (ODCR) は管理できるが、Capacity Block を購入する機能は持たない。

Trainium の列の「対応可」も、確度に差がある。実績があるのは (a) だけで、distributed-ai で `device_plugin = "neuron"` を GPU と同じループで動かしていた。(b)、(e)、(f) の「対応可」は、同じ仕組みで動くはずだという見立てである。設計の時点で未解決だった点として、Karpenter の AMI の指定で Neuron 用の AMI が自動で選ばれるか、Neuron にスケジューラの拡張が要るか、GPU と Neuron を Kueue の 1 つの resourceGroup に並べて書けるか、が残されていた。Issue #17 は、デバイスの扱い方がプールの規約を変えうるとして Issue #19 に依存すると明記しており、Trainium の扱いの実測は 7 章で扱う。

比較表の外にも、名前が挙がった選択肢がある。調査の段階では、Kueue の `ProvisioningRequest` を使うために cluster-autoscaler を使う選択肢も挙がっていたが、6 つの構成案には入れていない。また Volcano と KAI は、Kueue と機能が重なるため入れないとした。

## 4. 推奨する構成: (b) と (c) を核に、(d) を薄い外側に置く

Issue #17 の検討がまとめた推奨は、(b) Karpenter と (c) Kueue を核にし、(d) SkyPilot をその外側に薄く置く構成である。独自の CRD である (f) は作らず、(e) kro は保留にする。

(b) と (c) を組む理由は、互いの短所を補い合うことにある。(b) には誰が予約を先に使うかの順番や枠が無く、(c) では spot と on-demand に入場しても容量があるとは限らない。そこで、数えられる容量 (ODCR と Capacity Block) は Kueue の枠で数え、数えられない容量 (spot と on-demand) は Karpenter の探索に任せる。この分担が推奨の核である。2 本目の軸である包み方については、素の Helm chart 1 つを選んだ。chart はこのリポジトリ固有の values を持つが、NodePool と ResourceFlavor の対を生成するだけで判断を持たず、実験を通った後にリポジトリの外へ独立させて取り込む形にする想定である。この 2 点が、(e) の kro で合成する API と違って「このリポジトリだけの抽象化」に当たらないとする理由である。

この推奨が成り立つかどうかは、3 章で扱う一番危ない前提、つまり「Kueue が入場させた = 容量がある」が `-rsv` (予約だけのプール。3 章で定義する) でしか成り立たないという点にかかっている。これはまだ実機で確かめていない推測であり、数ドルで済む最初の実験で確かめる予定である。

kro を保留にする理由は、「容量」という API を合成しても、それ自体がこのリポジトリだけの抽象化になり、既存の NodePool / ResourceFlavor の複雑さが CEL の側に移るだけだと見込まれるからである。この見込みは kro の ResourceGraphDefinition を試作して確かめたものではなく、設計上の推測である。合成する宣言の対の数が増え、同期用の小さなコントローラと status の集約が実際に欲しくなった段階で再評価すればよく、今の段階で先取りして複雑さを足す必要は無い。独自の CRD である (f) を作らない理由は、1 章で見た「このリポジトリの中だけで通じる独自の層を作らない」という原則に正面から反するためである。既存の API である NodePool、EC2NodeClass、ResourceFlavor、ClusterQueue をそのまま組み合わせれば表現できることを、わざわざコントローラを書いて再実装する必要は無い。

比較表の短所を、壊れたときに何が起きるかで言い直すと次のようになる。(a) では Capacity Block を 1 日買うたびに plan と apply が要り、Kueue の側との名前のずれが黙って起きる。(b) では中断できないジョブが spot に載り、予約を先着で食い合う。(c) では入場済みのジョブが枠を握ったまま Pending になる。(d) では Kueue の待ちと SkyPilot の failover が噛み合わず、最初のセルで待ち続けると見込まれる。(e) ではスキーマを変えるときの移行が要り、status は「作った」は言えても「取れた」は言えない。(f) ではコントローラが止まると容量の管理が止まり、Argo CD との二重管理になる。いずれも設計の段階での見立てである。

推奨構成でも、予約を買うたびの人手は残る。(a) で apply だったものが、推奨構成では Kueue の枠を書き換える PR 1 本に替わる。Capacity Block は 1 日あたり 54 ドルから 3,100 ドルの買い物なので、購入ごとに PR を 1 本出すのは妥当な手続きだと判断した。この同期を自動にしたくなったら、予約の状態を読んで Kueue の枠を書くだけの、新しい API を持たない小さなコントローラを独立した OSS として切り出す。

推奨する構成では、Terraform が作るものと、Argo CD や人の手続きが作るものを明確に分ける。

| Terraform が作るもの | Terraform が作らないもの (Argo CD か人が担う) |
| --- | --- |
| VPC、全 AZ のサブネット、EFA 用セキュリティグループ | NodePool、EC2NodeClass |
| EKS クラスタ、システム用の小さなノードグループ | ResourceFlavor、ClusterQueue、LocalQueue |
| IAM (Karpenter、ノード、Pod Identity)、中断通知用の SQS | Karpenter、Kueue、JobSet、Trainer、LWS、デバイスの広告者 (device plugin または DRA)、指標の収集本体 |
| Argo CD の capability 登録とセルのラベル付け | ODCR と Capacity Block の購入 |
| チェックポイント用のストレージ、コンテナレジストリ | ワークロードそのもの |

この表は Issue #17 で提案した境界である。8 章で見る土台の層 (Issue #20、PR #21) が実際に作るのは、このうち VPC、EKS クラスタ、システム用のノードグループ、Karpenter の IAM と中断通知のキュー、任意の Argo CD の capability であり、EFA 用のセキュリティグループ、チェックポイント用のストレージ、コンテナレジストリはまだ含まれていない。また、1 章で管理者の持ち物とした共有ストレージ (FSx for Lustre と sharedfs) は、この表には現れず、別の Issue (#18) で扱う。

この境界線が、1 章で述べたマルチテナンシーの制約と重なっていることに注意したい。ただし Issue #17 の時点ではマルチテナンシーは前提に入っておらず、これは後から見た整理である。NodePool や ResourceFlavor、ClusterQueue はクラスタスコープのリソースであり、Terraform が作らない代わりにチームへそのまま渡すわけでもない。これらは Argo CD が配る Helm chart か、管理者が担う手続きが生成し、所有する。各チームが受け取るのは、その上に自分のワークロードを置くための入口だけである。

各層の責任は次のように割り振られる。人の手順とスクリプトが D1 (予約の購入、タグ付け、Git への記録) を担い、Terraform はセルの器だけを作る。Argo CD が ApplicationSet を介してセルのラベルを見てアドオンとプールとキューを配り、Karpenter が D4 (型、AZ、spot か on-demand かの探索と起動) を担う。Kueue が D3 (枠、順番、借用、preemption、gang 入場) を担い、SkyPilot がハブ (Argo CD と SkyPilot を置く、ほかのセルを束ねる側のクラスタ) として D2 (どのクラスタに投げるか) と利用者の入口を担う。kro と ACK は当面使わない。

D2 の割り当ては、まだ確定ではない。SkyPilot 経由のジョブは `allowed_contexts` でセルを選ぶが、Kubernetes ネイティブのジョブは人がセルを名指しする。MultiKueue は実験 4 の結果が出るまで入れず、同じジョブに SkyPilot と MultiKueue の両方を噛ませることはしない。クラスタの選択をどちらに任せるかは、実験 4 で 1 つに決める。

<!-- FIG name="02-options-axes" kind="relation" shows="the six options plotted on two axes: which layer actually decides (Karpenter, Kueue, SkyPilot) versus how the declaration is wrapped (Terraform variable, plain manifest, kro, custom CRD)" labels="(a) TF variable, (b) Karpenter+ArgoCD, (c) Kueue-centric, (d) SkyPilot outside, (e) kro, (f) custom CRD, deciding layer, wrapping" -->
![6 つの構成案を 2 軸で整理した図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/02-options-axes.png)

<!-- FIG name="02-recommended-layers" kind="position" shows="the recommended layered architecture from entry point down to the Terraform shell: SkyPilot hub, Kueue admission, Karpenter launch, Argo CD delivery, Terraform shell, human purchase" labels="Entry (SkyPilot hub / direct), D2 Choose a cell, D3 Admit (Kueue), D4 Launch (Karpenter), Deliver (Argo CD), Shell (Terraform), Buy (human procedure)" -->
![推奨構成の層を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/02-recommended-layers.png)

## まとめ

容量をどう混ぜるかという悩みは、1 つの問いではなく、時間の単位が違う 4 つの決定である。distributed-ai はこの決定群を 1 つの Terraform 変数に押し込み、awsome の amazon-eks は 1 ノードグループに 1 種類の容量を固定しており、どちらも「欲しい量」と「今ある容量」を結ぶ層を基盤の外に置いていた。6 つの構成案を比較した結果、Karpenter と Kueue を核にし、SkyPilot を薄い外側に置き、独自の CRD を作らず kro を保留にする構成を推奨とした。この推奨そのものはまだ実機で確かめた結論ではなく、Issue #17 での机上の比較から導いた推測であり、3 章で挙げる実験で確かめる。次の章では、この構成の核となる具体的な規約、NodePool と ResourceFlavor をどう対応させるかを見ていく。
