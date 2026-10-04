---
title: "Awsome: EKS の GPU クラスタを CloudFormation で作る"
free: false
---

## はじめに

本章では、EKS の GPU クラスタを CloudFormation で作る経路を足した [PR #1269](https://github.com/awslabs/awsome-distributed-ai/pull/1269) をまとめる。先行する [#1259](https://github.com/awslabs/awsome-distributed-ai/pull/1259) の 1 つのテンプレートを 5 つに分け、GPU のノードグループだけを既存のクラスタに足せるようにし、ノードの AMI を作る経路も足した。

:::details EKS のノードグループと device plugin
EKS は Kubernetes のコントロールプレーンを AWS が運用するサービスで、ワーカーのノードはノードグループとしてまとめて作る。GPU や EFA を Pod から使うには、各ノードで device plugin が動き、`nvidia.com/gpu` や `vpc.amazonaws.com/efa` という資源としてノードに登録する必要がある。CloudFormation のネストしたスタックは、親のテンプレートが子のテンプレートを URL で呼び出してまとめて作る仕組みである。
:::

## Issue と PR の概要

![root のテンプレートが、ネットワーク、クラスタ、GPU のノードグループを S3 の URL から子のスタックとして作り、AMI をここで作る入力があればノードの AMI の子のスタックも作る。GPU のノードグループは単独でも、既存のクラスタに対して作れる。最後に、GPU のノードグループの各ノードが Ready で、期待する数の GPU と EFA を資源として出していなければスタックを失敗させる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/awsome-eks-gpu-cfn/templates.png)

| 項目 | 内容 |
| --- | --- |
| 解決したいこと | 既存の EKS クラスタに GPU を足したい人が使える経路が、テンプレートにも eksctl のマニフェストにも無い |
| やったこと | テンプレートを 5 つに分け、AMI の 3 つの入手の方法と、ノードの能力の確認を足した |
| テスト | レビューの後の版で、lint（11 の検査で失敗 0）、Kubernetes 1.34 から 1.36 の root の作成、既存のクラスタへの追加、Capacity Block、API の endpoint が private だけのクラスタ、AMI の更新、EFA の通信などを確かめた |
| マージ | [d432521](https://github.com/awslabs/awsome-distributed-ai/commit/d432521298a4c05afa2077ac1d12dfba69fb0050) |

## 何を解決しようとしたか

#1259 のテンプレートは、VPC からクラスタ、ノードまでを 1 つで作った。デプロイは毎回ゼロから始まるとは限らない。すでに EKS のクラスタと VPC を持っていて、GPU だけを足したい人には、全部を作るテンプレートは使えない。

## どうやったか

[architectures/amazon-eks/assets](https://github.com/awslabs/awsome-distributed-ai/tree/d432521298a4c05afa2077ac1d12dfba69fb0050/architectures/amazon-eks/assets) に、ネットワーク、クラスタ、GPU のノードグループ、ノードの AMI、それらを子のスタックとして作る root の 5 つのテンプレートを置いた。主な判断は次のとおりである。

| 判断 | 選んだもの | 比べた方向と、選んだ理由 |
| --- | --- | --- |
| テンプレートの分け方 | 5 つに分け、root が URL で子を作る | 1 つのテンプレートに条件を足す方法は、読むファイルが 1 つで済む。ただ、持ち込めるものが増えるたびに分岐が増え、一部の分岐でしか効かないパラメータが他と同じ一覧に並ぶ。分けた代わりに、変更を試すには子を S3 に置く手間が増える |
| クラスタを触る権限 | GPU のノードグループの側に access entry を置く | device plugin を入れる処理が Kubernetes の API を使う。ノードグループ側に置けば単独で作るときも権限を持ち込め、クラスタ側に置くと GPU を使わないクラスタにも権限が残る |
| 版の固定 | kubectl と Helm はパラメータ（既定は試した版）、device plugin のチャートは既定で固定。root もこれらを受け取って子に渡す | kubectl と Helm を Kubernetes の版の表にすると、EKS が新しい版を出すたびに表の更新が要る。device plugin はノードのドライバとの組み合わせで正しさが決まるので、既定では固定する |
| GPU のノードの taint | ノードグループの設定と kubelet のフラグの両方で付ける | フラグを外して登録の直後から観察すると、taint が付く前に `nvidia.com/gpu` を出す瞬間があった。フラグは登録の時点で効くので残した |
| AMI の入手 | 既存の AMI、ここで作る AMI に入れるパッケージ、外で管理するレシピのどれかを、埋めた入力から決める | 入手の方法を名前で選ぶパラメータは読みやすいが、「レシピ」を選んでパッケージも埋める矛盾を別の規則で止める必要がある。入力から決めれば、この組み合わせは書けない。2 つの入力を同時に埋める誤りは、別の規則が作成の前に拒む。代償は、画面で方法が明示されず、テンプレートの中の条件が増えること |

最後に、ノードの能力を確かめる処理を入れた。GPU のノードグループの各ノードが Ready になり、インスタンスタイプに合った数の `nvidia.com/gpu` と `vpc.amazonaws.com/efa` を出していなければ、スタックを失敗させる。ドライバがその GPU を認識できない AMI では、ノードは Ready になっても GPU を出さないので、この確認で止まる。この確認は Kubernetes の資源としての登録を見るもので、CUDA や学習が動くことまでは見ない。

## テストをどうしたか

レビューの後の版で、筆者は次を確かめた（[PR の返答](https://github.com/awslabs/awsome-distributed-ai/pull/1269)の Test results for this revision）。[tests/lint-templates.sh](https://github.com/awslabs/awsome-distributed-ai/blob/d432521298a4c05afa2077ac1d12dfba69fb0050/architectures/amazon-eks/tests/lint-templates.sh) は、認証情報を与えて 11 の検査が失敗 0 で終わった。`describe-instance-types` が答えない一部の項目は、lint が未検証と報告する。

| 確かめたこと | 結果 |
| --- | --- |
| Kubernetes 1.34、g4dn.8xlarge 2 台の root | `CREATE_COMPLETE`。GPU のノードで node-feature-discovery のワーカーが動き、`gpu: 1`、`efa: 1` を出した。root の `KubectlVersion` がノードグループに届いた |
| 1.35 と 1.36 の root | `CREATE_COMPLETE` |
| g7.12xlarge 2 台で EFA の試験の手順 | `fi_pingpong` が終了コード 0 で、EFA の計数が 2,720,004 バイト増えた |
| p5en.48xlarge の Capacity Block | `CapacityType` が `CAPACITY_BLOCK` で、ブロックの開始の後に `CREATE_COMPLETE`。`gpu: 8`、`efa: 16` |
| p5en.48xlarge、README のパッケージから作った AMI | スタックは `CREATE_COMPLETE` だが CUDA が `system not yet initialized` で失敗した。`nvidia-fabricmanager` を足すと NCCL の `all_reduce_perf` が終わった |
| ノードグループだけを、eksctl で作った 1.35 のクラスタに足す | `CREATE_COMPLETE` |
| API の endpoint が private だけのクラスタ | `BootstrapVpcId` が無いと拒まれ、あると `CREATE_COMPLETE` |
| すべてのスタックの削除 | 完了した |

g7e.12xlarge（既定）と、ネットワークカードが 4 枚以上の p5en.48xlarge 以外のインスタンスタイプは、確かめたリージョンで容量が無く試していない。

## レビューで直したこと

KeitaW さんは、レビューの前に README の手順で g7e.12xlarge の予約と Kubernetes 1.36、g4dn.8xlarge と 1.34 で作成し、6 回に分けて指摘した。マージの前に直すべきとされたのは 2 件だった。

- **Kubernetes 1.34 の device plugin**。NVIDIA の device plugin の DaemonSet は、`feature.node.kubernetes.io/pci-10de.present`、`cpu-model.vendor_id=NVIDIA`、`nvidia.com/gpu.present=true` のどれかのラベルを持つノードにだけ載る。1.35 以降は nodeadm が `nvidia.com/gpu.present=true` を付けるので 1.36 では動いたが、1.34 ではこのラベルが無い。1 つ目のラベルを書く node-feature-discovery のワーカーも、GPU のノードの taint を許容していなかった。チャートの `tolerations` を上書きしても、同梱のサブチャートのワーカーには届かない。[マージした版](https://github.com/awslabs/awsome-distributed-ai/blob/d432521298a4c05afa2077ac1d12dfba69fb0050/architectures/amazon-eks/assets/eks-add-gpu-nodegroup.yaml)では `nfd.worker.tolerations` にも同じ toleration を渡す。
- **Capacity Block の容量の種類**。Capacity Block のノードグループに `CapacityType: CAPACITY_BLOCK` が無かった。マージした版では、予約の種類に応じて `CAPACITY_BLOCK` か `ON_DEMAND` を入れる。

ほかに、CloudFormation への応答の送信に失敗しても送信済みとして記録していた処理（マージした版は送信が成功したときだけ記録する）、FSx for Lustre のクライアントが受けるべき通信の許可の不足、2 つのノードグループのスタックが 1 つの事前取得の DaemonSet を共有していたこと（マージした版はノードグループの名前を付けた DaemonSet にする）、などを直した。

## 技術的な要点

- **Ready と GPU の資源と計算**。GPU のノードは Ready になっても、GPU を資源として出しているとは限らない。資源として出していても、CUDA が動くとは限らない（p5en.48xlarge では `nvidia-fabricmanager` が要った）。資源の数を確かめたうえで、使う AMI とインスタンスタイプで計算も試す。スタックの成功を、ノードが期待した数の `nvidia.com/gpu` と `vpc.amazonaws.com/efa` を出したことで判定する。
- **登録の直後の taint**。taint をノードグループの設定だけで付けると、登録の直後に taint の無い瞬間がある。kubelet のフラグでも付ける。
- **Helm のサブチャートに届く値**。親のチャートの `tolerations` を上書きしても、同梱のサブチャートの DaemonSet には届かない。`helm template` で描き、DaemonSet ごとの toleration を確かめる。
- **テンプレートに書く版**。テンプレートに版を書くと、その版の保守を引き受けることになる。利用者のほうがよく知る値はパラメータにし、正しさに効く組み合わせだけを既定で固定する。
- **入手の方法を選ぶパラメータ**。方法の名前を選ばせず、埋めた入力から方法を決めると、名前と入力が食い違う組み合わせは書けなくなる。複数の入力を同時に埋める誤りは残るので、ちょうど 1 つを確かめる規則は別に置く。
- **試していない経路の一覧**。試していない経路は、PR に「試していない」と一覧で書く。レビューはそこから見るので、見落としが減る。

## まとめ

1 つのテンプレートを 5 つに分けたことで、既存のクラスタに GPU のノードグループだけを足す経路ができた。レビューの実機の作成で kubelet の taint のフラグと device plugin の版の確かめ方が裏づけられ、Kubernetes 1.34 の node-feature-discovery の toleration と Capacity Block の `CapacityType` の 2 件がマージの前に直された。
