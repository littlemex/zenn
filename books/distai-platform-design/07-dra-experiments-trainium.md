---
title: "Trainium で device plugin と DRA を実機で試す"
---

## はじめに

第 6 章では、NVIDIA の GPU(`g7.2xlarge`)で device plugin と DRA を実機で確かめ、小型の GPU は device plugin を既定にするという結論に至った。この章では、同じ観点を Trainium(`trn2.3xlarge`)で確かめた、[Issue #19](https://github.com/littlemex/distai-platform/issues/19) の実験 E4 の結果を扱う。

結論から述べると、`trn2.3xlarge` は、小型 GPU と同じく device plugin・Karpenter の動的プロビジョニング・今までの書き方の Pod(Layer 1)・スケジューラ拡張なしを既定にする。理由は、Layer 1 については小型 GPU と同じ(動的な DRA プールのノードが初期化されない)であり、Layer 2 については GPU より状況が悪い(ノードがそもそも起動しない)ことである。Neuron の DRA プールは台数を固定したプールでしか動かない。これは Karpenter の DRA 機能に Neuron 用の対応が無いためである。さらに、Layer 1 の Pod が DRA のノードに届くのは、この基盤側で追加した DeviceClass(`extendedResourceName` 付き)があるときだけであり、Neuron の chart 自身はこの DeviceClass を持っていない。

この章の結果はすべて、1 つのリージョン(ap-southeast-4)の 1 つの AZ、1 つのインスタンスタイプ(`trn2.3xlarge`、デバイス 1 枚)、Karpenter 1.15.2 と `neuron-helm-chart` 1.11.2 の組み合わせで、各条件をおおむね 1 回ずつ観測したものである。

<details>
<summary>実験環境</summary>

実験は、この目的のために作り、終了後に削除した専用の EKS クラスタ(リージョン ap-southeast-4、オーストラリア)の上で行った。Amazon EKS 1.37(kubelet v1.37.1)、AMI `amazon-eks-node-al2023-x86_64-neuron-1.37-v20261112`(Karpenter が `trn2` 向けに選んだもの)、Neuron カーネルドライバ 2.31.8.0、Karpenter 1.15.2、`neuron-helm-chart` 1.11.2(device plugin 2.33.1.0、DRA ドライバ 1.4.0)、インスタンスは `trn2.3xlarge` の spot を使った。起動は 3 回で、3 回とも spot で成功し、すべて AZ `apse4-az3`(`trn2.3xlarge` を提供する唯一の AZ)だった。かかった時間は約 1 時間 15 分、費用は推定で 4 から 5 ドルである。実施日は 2026-11-18 で、第 6 章と同じく起動を試みるたびに起動の記録(launch ledger)を残した。

</details>

## 1. 実験の見取り図

E4 は 4 つの実験(E4a から E4d)と、わざと二重に広告した場合を確かめる追加の確認(E4+)から成る。

| # | なぜ | どう測ったか | 結果 |
| --- | --- | --- | --- |
| E4a | 1 つの chart でノードプールごとに広告する側を分けられるか、Karpenter が Neuron をどう見積もるかを知る | chart の values を読み、方式のラベルで分けた 2 つの release を入れ、ノードの allocatable と Karpenter のログを見た | 1 回の release は 1 方式だけしか持てない。Karpenter の見積もり(neuroncore=8)が実測(neuroncore=4)の 2 倍だった |
| E4b | 今までの書き方の Pod(Layer 1)が DRA のノードに届くか | 同じ Pod を device plugin のノードと DRA のノードに出し、platform 側の DeviceClass を足す前後で比べた | 足す前は Pending、足すと約 3 秒で Scheduled された |
| E4c(動的) | Karpenter が 0 台から Neuron の DRA ノードを起こせるか | プールを 0 台にして Layer 1・Layer 2 それぞれの Pod を出した | Layer 1 はノードが起きるが未初期化のまま 8 分 50 秒たっても片付かない。Layer 2 はノードが 1 台も起きない |
| E4c(固定) | 台数を固定したプールなら動くか | `replicas: 1` の NodePool(Karpenter の `StaticCapacity` ゲート)を適用した | 38 秒で Ready になり初期化済みになった。Layer 1・Layer 2 とも、そのまま動いた |
| E4d | Neuron のスケジューラ拡張が無くても動くか | `default-scheduler` のまま、`aws.amazon.com/neuron` と `aws.amazon.com/neuroncore` の両方の要求で Pod を出した | 両方 Running した。`neuroncore:4` の Pod は `NEURON_RT_VISIBLE_CORES=3,0,1,2` を受け取った |
| E4+ | わざと二重に広告すると何が起きるか(E1 と同じ確認) | device plugin を固定プールのノードにも向け、device plugin 経由と Layer 2 の Pod を同時に出した | 同じ `/dev/neuron0` が 2 つの Pod に渡り、警告も出なかった。2 つ目の Layer 1 の Pod は正しく拒否された |

## 2. 1 つの chart は 1 回の release で 1 方式だけ(E4a)

Neuron の device plugin と DRA のドライバは、`neuron-helm-chart` という 1 つの chart の中に同居している。しかし、`devicePlugin.enabled` と `draDriver.enabled` を同じ release で両方 `true` にしても、テンプレートの条件分岐によって device plugin の DaemonSet が消える。公式のドキュメントのコマンドどおりに DRA を入れると、クラスタ全体の device plugin が無くなってしまう。また、どちらも既定では、インスタンスタイプが Neuron の一覧にあるすべてのノードを対象にする。第 6 章の E0 と同じく、何もしなければ全 Neuron ノードで重なる形である。

そこで、第 5 章の決定 2 で述べた方式のラベル(`distai-platform/neuron-method`)を使い、このラベルで対象を絞った affinity を持つ release を 2 回 install した(`neuron-dp` と `neuron-dra`)。`neuron-dp` は `devicePlugin.enabled=true`、`draDriver.enabled=false` とし、`neuron-dra` はその逆の値を持つ。`neuron-dp` が入れる `neuron-device-plugin` は `distai-platform/neuron-method=device-plugin` のノードへ、`neuron-dra` が入れる `neuron-dra-driver` は `distai-platform/neuron-method=dra` のノードへ、それぞれ向かう。これにより、1 つの chart から 2 つの広告する側を、ノードプールを分けて共存させられる。

## 3. Karpenter の見積もりと実測が 2 倍違う(E4a)

`trn2.3xlarge` には Trainium2 が 1 個搭載されており、物理コアは 8 個ある。Karpenter はこの物理コア数をそのまま `aws.amazon.com/neuroncore=8` と見積もる。しかし、device plugin が実際にノードの allocatable へ出す値は `aws.amazon.com/neuroncore=4` であり、見積もりの半分だった。

この差は、2 個の物理コアを 1 個の論理コアとして束ねる既定の設定(LNC=2)による。Karpenter の見積もりが間違っているわけではなく、見積もりは論理コアに束ねられる前の数を見ている。そのため、`neuroncore` を 5 から 8 の範囲で要求する Pod を書くと、Karpenter はその要求を満たせそうに見えるノードを起動するが、起動したノードの実際の allocatable(4)では満たせず、Pod は動かないと考えられる(この要求は実験では出しておらず、見積もりと allocatable の値から導いた推測である)。Pod を書くときは `aws.amazon.com/neuron`(デバイスの枚数)で要求するか、`neuroncore` の要求を実測の 4 以内にとどめる必要がある。

また、chart が持つ唯一の DeviceClass `neuron.aws.com` には `extendedResourceName` が無く、ResourceSlice が持つ属性は `deviceId`、`instanceType`、`neuronDriverVersion`、`draDriverVersion`、`resourceType` のみで、EFA と共有できるトポロジ属性は無かった。Neuron のデバイスと EFA を同じ PCIe のルートにそろえるような整合は、`trn2.3xlarge` の上では表現できない。この点は、より大きな `trn2.48xlarge` を使う実験(E6、まだ実行していない)で確かめる計画である。

## 4. Layer 1 が DRA のノードに届くのは、platform 側で足した DeviceClass のおかげ(E4b)

今までの書き方の Pod(`limits: {aws.amazon.com/neuron: 1}`)は、device plugin のノードでは普通に動いた(`neuron-ls` でデバイス 0、コア 4 個)。DRA のノードでは、chart が自分で作る DeviceClass `neuron.aws.com` に `extendedResourceName` が無いため、同じ Pod は `Insufficient aws.amazon.com/neuron` のまま Pending に留まった。

<!-- FIG name="07-e4b-deviceclass-before-after" kind="relation" shows="as-is: Layer 1 pod stays Pending on a DRA node because the chart's DeviceClass has no extendedResourceName; to-be: adding a platform DeviceClass with extendedResourceName lets the same unmodified pod schedule in about 3 seconds" labels="Layer 1 pod, chart DeviceClass (no extendedResourceName), platform DeviceClass (extendedResourceName), Pending, Scheduled in ~3s" -->
![platform 側の DeviceClass を足す前と後で、Layer 1 の Pod が DRA ノードに届くかどうかが変わる様子を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/07-e4b-deviceclass-before-after.png)

第 5 章の決定 3 で触れたとおり、`extendedResourceName: aws.amazon.com/neuron` を持ち `device.driver == "neuron.aws.com"` を選ぶ DeviceClass を、実験クラスタの上に 1 つ作った(この基盤のカタログ自体はまだ実装していない)。これを足したところ、同じ Pod は変更なしで約 3 秒で Scheduled になり、自動で作られた `ResourceClaim` が `neuron-device-0` を割り当てた。GPU 側(NVIDIA の DRA chart)は、この役割の DeviceClass を chart 自身が持っているため、同じ手当てが要らない。独自 CRD を避け、DeviceClass のカタログを小さく持つという第 5 章の決定 1(c)はこの非対称が分かる前にすでに決まっていたが、この非対称によって、そのカタログに Neuron 用の DeviceClass を足す必要があることが分かった。

## 5. Neuron の DRA プールは、台数を固定しないと使えない(E4c)

Neuron の DRA プールは、Karpenter が台数を動的に増減させるプールでは Layer 1 の Pod でも Layer 2 の Pod でも使えず、台数を固定したプールでだけ動いた。プールを 0 台にして Layer 1 の Pod を出すと、Karpenter はノードを起こし(`aws.amazon.com/neuron=1` と見積もる)、Pod 自体は動いた。しかし、そのノードの NodeClaim は `Initialized=Unknown`(理由は `ResourceNotRegistered "aws.amazon.com/neuron"`)のままで、Pod を手で削除してから 8 分 50 秒たっても片付かなかった(`consolidateAfter` の設定は 2 分)。Karpenter はこのノードに `DisruptionBlocked: Node isn't initialized` を出しており、未初期化であることが片付けを止めていた。同じ条件の device plugin のノードは、Pod を消してから約 3 分で片付いた。

Layer 2 の Pod(`ResourceClaimTemplate` で要求)はさらに悪く、Karpenter の DRA 機能(`settings.enableDRA`)を切っても入れても、ノードが 1 台も起動しなかった。`enableDRA` が off のときは「Dynamic Resource Allocation の要求を持つ Pod はまだ Karpenter でサポートされていない」という理由で、on のときは「割り当てを満たせるインスタンスタイプが無い」という理由で、どちらもノードを起動できなかった。Karpenter の DRA 機能に Neuron 用のゲートが無いことは、第 5 章で文書から確認しており、この章の実験で実機でも確かめられた。Karpenter の環境変数 `AWS_FEATURE_GATES` に出てくる DRA のゲートは `DRANVIDIAGPU` と `DRAEFA` だけだった。Neuron の DRA を Karpenter で扱うには、Karpenter 側に Neuron の対応が増えるのを待つか、台数を固定したプールで運用するかの 2 択になる。小型 GPU の実験(第 6 章の E2)では、Layer 2 の Pod は `enableDRA=true`(alpha)で 48 秒で Running になり、ノードは初期化までできたので、**Neuron は GPU よりも状況が悪い**ことになる。

<!-- FIG name="07-e4c-dynamic-vs-static" kind="relation" shows="a dynamic Neuron DRA pool: Layer 1 pod launches an uninitialized node, Layer 2 pod launches nothing; a static Neuron DRA pool (StaticCapacity gate): both Layer 1 and Layer 2 pods run on a node that becomes Ready and initialized in 38s" labels="dynamic pool, static pool, Layer 1 pod, Layer 2 pod, Initialized=Unknown, Ready in 38s, StaticCapacity gate" -->
![動的な Neuron の DRA プールでは起動が失敗し、台数を固定したプールでは両方の Pod が動く様子を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/07-e4c-dynamic-vs-static.png)

台数を固定した NodePool(`replicas: 1` を指定する。これに必要な Karpenter のゲート `StaticCapacity` は alpha で、既定では off である)を試すと、ノードは 38 秒で Ready になり初期化済みになった。この固定プールの上では、Layer 1 の Pod(platform の DeviceClass を足した状態)も Layer 2 の Pod も、そのまま動いた。

## 6. スケジューラの拡張は要らない(E4d)

Neuron のスケジューラ拡張(chart 側の `scheduler.enabled` で入れる)は「複数デバイスをまとめて連続に割り当てる」問題を解決するものであり、1 枚しかデバイスを持たない `trn2.3xlarge` ではこの問題がそもそも起きないため、拡張は不要だと判断した。この実験では拡張を入れていない。それでも、1 枚のデバイスを要求する Pod(`aws.amazon.com/neuron: 1`)と、4 個のコアを要求する Pod(`aws.amazon.com/neuroncore: 4`)は、どちらも `default-scheduler` のままで Running になった。後者は `NEURON_RT_VISIBLE_CORES=3,0,1,2` を受け取っており、コアの選び方自体は device plugin 側が行っている。

## 7. わざと二重に広告すると、Neuron でも同じ壊れ方をする(E4+)

device plugin を、本来は DRA 専用のつもりだった固定プールのノードでも動かすと、そのノードの allocatable に `aws.amazon.com/neuron: 1` が現れた。まず、同じ層(Layer 1)の Pod を 2 つ続けて出した場合は、2 つ目は正しく `Insufficient aws.amazon.com/neuron` で拒否され、二重割り当てにはならなかった。次に、device plugin 経由の Pod(Layer 1)を先に、Layer 2 の Pod(`ResourceClaim`)を後に出すと、両方が同時に Running になって同じ `/dev/neuron0`(同じ PCI アドレス `0000:33:00.0`)を受け取り、どこにも警告やイベントは出なかった。これは第 6 章の E1 で GPU について見た壊れ方とまったく同じである。

<!-- FIG name="07-e4plus-double-advertisement" kind="flow" shows="device plugin (should not run here) and the existing DRA driver both advertise the same node; a device-plugin pod and a Layer 2 pod receive the same /dev/neuron0 at the same PCI address, with no warning" labels="device plugin, DRA driver, /dev/neuron0, PCI 0000:33:00.0, device-plugin pod, Layer 2 pod, no warning" -->
![同じ /dev/neuron0 が device plugin 経由と Layer 2 の 2 つの Pod に渡る様子を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/07-e4plus-double-advertisement.png)

壊れるのは、Layer 1 の Pod が device plugin を、Layer 2 の Pod が DRA のドライバをそれぞれ経由して、同じデバイスを両方から受け取ってしまうときだけである。これにより、第 5 章の決定 1(d)で採用した読むだけの検査ツールの規則(「DRA のドライバが動くノードで、対応する extended resource の allocatable が 0 より大きい」)で、GPU と Neuron のどちらの二重広告も見つけられる見込みが立った。確かめたのは、二重の広告が起きたノードの allocatable に値が出ることまでで、検査ツールそのものはまだ作っていない。

## 8. 決め直したこと

実験の前に仮説として置いていた項目のうち、次の 6 点を実験の結果を受けて確定させた。

| 項目 | 実験の前 | 実験の後 |
| --- | --- | --- |
| `trn2.3xlarge` の既定 | 小型 GPU(E0 から E3)と同じ構図を想定していた | device plugin・動的プール・Layer 1・スケジューラ拡張なしで確定した(小型 GPU と同じ理由による) |
| Neuron の DRA プール | Karpenter の DRA 機能で動かせるか未確認だった | 台数を固定したプール(`StaticCapacity`、alpha)でしか動かない。動的には Layer 1 も Layer 2 も使えない |
| Layer 1 から DRA ノードへの到達 | chart が対応していると期待していた | Neuron の chart は `extendedResourceName` を持たない。platform の DeviceClass カタログに Neuron 用の DeviceClass を 1 つ追加する |
| Neuron の chart の install | 1 回で済むかもしれないと見込んでいた | 方式のラベルで分けた 2 回の release が必須である |
| Karpenter の `neuroncore` 見積もり | 未確認だった | 実測の 2 倍(8 対 4)だった。要求は `aws.amazon.com/neuron` を使うか、`neuroncore` は実測の範囲内にとどめる |
| 検査ツールの規則 | GPU 向けに設計していた | GPU も Neuron も同じ規則(extended resource の allocatable が 0 より大きく、かつ DRA ドライバが動くノード)でよい |

ここでの「確定」は、`trn2.3xlarge` とこのバージョンの組み合わせで観測した結果にもとづく既定値という意味である。Inferentia は起動していない。

## 9. GPU と Neuron の比較

小型 NVIDIA の GPU(第 6 章)と単体 Neuron(この章)は、最終的に同じ結論(device plugin・動的プール・Layer 1・スケジューラ拡張なし)に落ち着いたが、そこに至る経路には違いがある。

| 観点 | 小型 NVIDIA(`g7.2xlarge`) | 単体 Neuron(`trn2.3xlarge`) |
| --- | --- | --- |
| Layer 1 が DRA ノードに届くか | chart 自身が持つ DeviceClass で届く | platform が追加した DeviceClass が無いと届かない |
| Karpenter の見積もり | デバイスの数は方式によらず `nvidia.com/gpu=1` と見積もる。device plugin のノードの allocatable はこれと一致するが、DRA のノードは allocatable に GPU が出ないので、この見積もりとの一致は device plugin のノードでしか確認できない | デバイスの数は `aws.amazon.com/neuron=1` で allocatable と一致する。コアの数は `neuroncore=8` と見積もるが実測は 4 で、2 倍ずれている。NVIDIA にはコアに当たる比較の対象が無い |
| 動的プール + Layer 1 | ノードは起動するが未初期化のまま残る | 同じく未初期化のまま残る(8 分 50 秒まで確認した) |
| 動的プール + Layer 2 | `settings.enableDRA=true`(alpha)で 48 秒で Pod が Running になり、ノードは初期化される | ノードが 1 台も起動しない(Karpenter に Neuron の device データが無いため) |
| 二重広告の壊れ方 | 同じ GPU UUID が 2 つの Pod に渡る | 同じ `/dev/neuron0`(同じ PCI アドレス)が 2 つの Pod に渡る |
| スケジューラ拡張 | 該当なし | 不要(単体デバイスのため連続割り当ての問題が起きない) |

Neuron が GPU より一段階悪いのは、動的プールでの Layer 2 の挙動である。GPU は Layer 2 なら動的プールでも動くが(alpha の `settings.enableDRA` を有効にした場合)、Neuron は Layer 1 と Layer 2 のどちらも動的プールでは使えない。この差は、Karpenter の DRA 機能(`settings.enableDRA`)が NVIDIA GPU と EFA のドライバしか対象にしておらず、Neuron 用のゲートがそもそも無く、Karpenter が Neuron のデバイスの情報を持たないことに起因する。Layer 1 の未初期化は、ゲートのある NVIDIA でも起きたので、ゲートの有無では説明できず、第 6 章の 4 節で推測した別の原因(Karpenter が、Pod の `limits` にある extended resource が DeviceClass 経由で満たされていることを認識せず、ノードの allocatable に出ない登録を待ち続けること)によると考えられる。

## 10. awsome への提案

`awsome-distributed-ai` は Neuron(Trainium)を中心には扱わない方針に近いため(awsome 側が表明した方針ではなく、この基盤の側の判断である)、E4a から E4d で見つけた Neuron の chart の構造や Karpenter の見積もりのような、Trainium 固有の知見は `awsome-distributed-ai` へは提案せず、この基盤の中で育てる対象のままにする。GPU 一般に関わる、中立な点だけを次にまとめる。

| # | 提案 | 上流の今 | 取り込むと何が良いか |
| --- | --- | --- | --- |
| 1 | 二重広告を検出する読むだけの検査(第 5 章の決定 1(d)の裏付けを強化) | `plugin()` は Helm の release 名を 2 つ比べるだけで、DRA を広告する側は見ていない | E4+ は、第 6 章の E1 で見た「同じデバイスが警告なしで 2 つの Pod に渡る」壊れ方が、Neuron でも同じ形(device plugin と DRA のドライバが同じノードを広告する)で起きることを確かめた。E4+ では device plugin を DRA 用の固定プールのノードへ手で向けたので、既定の affinity(対象を絞らない限り全 Neuron ノードに乗る opt-out の指定)が原因で起きたわけではないが、既定のままでは同じ形が起きうる。release 名ではなく、ノードの allocatable と DRA の ResourceSlice を読む検査の方が、アクセラレータの種類を問わず効くという根拠が 1 件増えたことになる。出し方としては、決定 1(d)の検査ツールを先に作って GPU で動くことを確かめたうえで、Issue として提案する(まだ出していない) |

## まとめ

`trn2.3xlarge` は、Layer 1 については小型 GPU と同じ理由(動的な DRA プールのノードが初期化されない)、Layer 2 については GPU より悪い理由(ノードが起動しない)で、device plugin・動的プール・Layer 1・スケジューラ拡張なしという同じ結論に落ち着いた。これは Issue #17 の全体設計を進める根拠になる一方で、Neuron の DRA プールが台数を固定したプールでしか今は使えないという、GPU より厳しい制約も明らかになった。この結果は、第 5 章の決定 4 の表に反映されている。
