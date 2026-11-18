---
title: "アップストリームへの貢献をどう選ぶか"
---

## はじめに

distai-platform は [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)(以下、アップストリームと呼ぶ)の規則と構成を手本にしているが、一方通行の関係ではない。基盤を作る過程で、アップストリームのテンプレートやワークフローの具体的な欠落や欠陥も見つかっている。この章では、2026 年 11 月 18 日時点でアップストリームに返せると判断した候補を、実際にまだ出していない前提で並べる。候補 A から D はコード上の commit(`25d5723`)を引用した事実に基づいており、1 節の却下された提案は PR #15 の内容に基づき、6 節の DRA の候補は 6 章と 7 章で扱う実験(E0 から E4)に基づいている。どれを・いつ出すかはリポジトリの持ち主が決める。「同じ提案がすでにあるか」の確認には `gh` コマンドが使えず、認証無しの GitHub の REST / Search API を使っている。そのため、10 章が定める `gh` での重複確認を、この調査はそのまま踏めていない。この API は語の一致がゆるく、フレーズ検索でも関係ない結果が混ざり、一部のクエリは回数の上限(403)で失敗したため、「見つからなかった」はこの条件下での弱い確認でしかない。候補 A から C は、この検索で一致するものが見つからなかったという意味で「既存の提案は無い」としている。

## 1. Neuron を前提にした提案は出さない

アップストリームの `architectures/amazon-eks` の eksctl の例は `g4dn`、`g5`、`p4de`、`p5` のような GPU だけで、Trainium の例を持たない。このことと、`amazon-eks` の README と `PARAMETERS.md` を `karpenter|spot|trainium|neuron|DRA` で検索して 0 件だったことから、アップストリームは Neuron(Trainium)を中心には扱わない方針に近いと判断している。根拠はこの 1 つのディレクトリの中身であり、リポジトリ全体の方針を確かめたものではない。したがって、Neuron を前提にした提案はアップストリームには出さない。Trainium に特有の部分(Neuron の chart の構造、Karpenter の `neuroncore` 見積もりの挙動など)は、distai-platform の中で実機で動かしてから育てる対象にとどめる。GPU と Trainium の両方に当てはまる、中立な点だけを候補にする。なお `AGENTS.md` 5 節は、汎用な Neuron の部分をアップストリームに出せる形で書くことを今も定めている。アップストリームへの貢献を GPU と汎用の部分に限るという方針は、その後にメンテナが加えた限定であり、`AGENTS.md` にはまだ反映されていない。DRA、device plugin、KEP-5004 は 5 章で扱う。RFC は、アップストリームの「大きな作業は先に Issue で相談する」ための Issue の様式である。

いったん候補に挙がったが、持ち主の判断で却下されたものが 2 つある。

- **AI メンバーへの指示は持ち主の書き込みだけに限る、という規則をアップストリームの `AGENTS.md` に足す提案**である。distai-platform 自身がこの規則を持っている(前章で扱った)ため、同じ考え方をアップストリームにも勧める形だったが、却下された。
- **`architectures/amazon-eks` に Karpenter・spot・Trainium・DRA の扱いを足す RFC の相談**である。Kubernetes の DRA の拡張リソース互換 API(KEP-5004)は v1.37 で stable になり、Karpenter v1.15 は NVIDIA GPU と EFA の DRA による動的プロビジョニングを alpha でサポートし、Neuron 用の DRA ドライバは Neuron SDK 2.28.0 から提供されている。これらは今の固定ノードグループと 2 本の device plugin という設計の前提そのものに関わる変更であり、単純な追記では済まない規模の提案になるため、アップストリームの「大きな作業は先に Issue で相談する」という方針そのものに沿った内容ではあったが、Neuron を前提にした相談である点を含めて却下された。

## 2. 候補 A: PR テンプレートの伏せ字注意

候補 A は、アップストリームの PR テンプレートの `## Test Results` に、ログを貼る前に秘匿の値を伏せるよう促す 1 行の注意を足し、アカウント ID や ARN が公開の PR に残ることを防ぐものである。アップストリームの PR テンプレートには `## Test Results` という欄があり、機能を変える PR では実際の AWS 環境で最後まで動かした証拠(ジョブの出力、ベンチマークの数字、ログ)をここに貼ることが必須になっている。実機のログには、`aws sts get-caller-identity` の出力に出るアカウント ID やロールの ARN、`kubectl get nodes -o wide` に出る内部の IP、`terraform apply` の出力に出るバケット名やクラスタのエンドポイントのような、利用者の AWS 環境の値がそのまま入りやすい。公開の PR に一度貼ると、消しても通知のメールや写しのサイトに残ってしまう。

[PR テンプレート](https://github.com/awslabs/awsome-distributed-ai/blob/25d5723cb62ab1f83b0f4307c28c8f3496bfaa53/.github/PULL_REQUEST_TEMPLATE.md)の `## Test Results` は、ログを貼れと言うだけで伏せ字の注意が無い。

```text
## Test Results

<!--
REQUIRED for functional changes: paste evidence of an end-to-end run on a
real AWS environment (job output, benchmark numbers, relevant logs).

For changes with no functional impact, replace this section's content with
the single word: docs-only
-->
```

一方で、同じリポジトリの[バグ報告の Issue テンプレート](https://github.com/awslabs/awsome-distributed-ai/blob/25d5723cb62ab1f83b0f4307c28c8f3496bfaa53/.github/ISSUE_TEMPLATE/200-bug-report.yml)には、同じ趣旨の注意が 2 か所ある。

```text
**IMPORTANT:** Please redact any access keys, secret keys, session tokens,
or other sensitive information from your bug report and log output.
...
**Remember to redact sensitive information.**
```

アップストリームは既に「ログには秘匿の値が混ざる」と分かっていて、Issue では注意している。機能を変える PR では実機のログを必ず貼るのに、その PR のテンプレートにだけ、その注意が無い。候補の内容は、`## Test Results` の HTML コメント内に、バグ報告のテンプレートと同じ趣旨の 1 行を足すことである。

```text
REQUIRED for functional changes: paste evidence of an end-to-end run on a
real AWS environment (job output, benchmark numbers, relevant logs).
Redact access keys, session tokens, account IDs, ARNs and other sensitive
information before pasting.
```

この文面は、動機に挙げた内部の IP、バケット名、クラスタのエンドポイントを名指ししていない。バグ報告のテンプレートより対象を広げているので「同じ趣旨」ではあっても同じ文ではなく、どこまで名指しするかはアップストリームとの相談になる。

これは言い回しの好みの書き換えではなく、テンプレートどうしの食い違い(Issue にはある注意が PR に無い)という具体的な抜けを埋めるものである。PR の本文を自動で調べて伏せ字を強制する workflow を作る案も考えられるが、1 行の注意書きを足すだけで済む話には重すぎる変更になるため見送る。文書だけの変更なので、確かめ方は `docs-only` で足りる。出し方は PR(1 行の差分)であり、実害(秘匿情報の貼付)の予防効果に対してコストが極めて小さいため、優先度は高いと判断している。

## 3. 候補 B: stale workflow の修正

候補 B は、アップストリームの stale の workflow から使っていない権限を外し、`RFC` ラベル付きの Issue を自動クローズの対象から除き、Issue に書き込む文面の日数を設定に合わせるものである。動きの無い Issue に印を付け、さらに動きが無ければ閉じる stale の workflow は、アップストリームでは毎日 1 回動く。[`closing-soon.yml`](https://github.com/awslabs/awsome-distributed-ai/blob/25d5723cb62ab1f83b0f4307c28c8f3496bfaa53/.github/workflows/closing-soon.yml) の全文は次のようになっている。

```yaml
jobs:
  close-issues:
    runs-on: ubuntu-latest
    permissions:
      issues: write
      pull-requests: write
    steps:
      - uses: actions/stale@v5
        with:
          days-before-issue-stale: 90
          days-before-issue-close: 60
          stale-issue-label: "stale"
          stale-issue-message: "This issue is stale because it has been open for 30 days with no activity."
          close-issue-message: "This issue was closed because it has been inactive for 14 days since being marked as stale."
          days-before-pr-stale: -1
          days-before-pr-close: -1
```

ここには 3 つの問題がある。1 つ目は、`days-before-pr-stale: -1` と `days-before-pr-close: -1` で PR を対象外にしているにもかかわらず、PR を書き換える権限 `pull-requests: write` を渡していることである。同じ `.github/workflows/` の中の [`blog-pr-gate.yml`](https://github.com/awslabs/awsome-distributed-ai/blob/25d5723cb62ab1f83b0f4307c28c8f3496bfaa53/.github/workflows/blog-pr-gate.yml) は `permissions: pull-requests: write` だけに絞り、コメントで「`permissions` stays minimal」と明記している。`closing-soon.yml` はこの自己規律と矛盾する。2 つ目は、除外のラベル(`exempt-issue-labels`)が設定されていないため、[`600-RFC.yml`](https://github.com/awslabs/awsome-distributed-ai/blob/25d5723cb62ab1f83b0f4307c28c8f3496bfaa53/.github/ISSUE_TEMPLATE/600-RFC.yml) で作られた `RFC` ラベル付きの Issue も、90 日動きが無ければ stale 化し、さらに 60 日(合計 150 日)で閉じられることである。議論に時間がかかる提案ほど、黙って消えることになる(これは設定から導いた見立てである。閉じた RFC の履歴は浅い clone では追えず、実際に自動クローズで消えた実例をこの調査では確認できていない)。3 つ目は、Issue に書き込む文面が「30 日動きが無いので stale」「stale になってから 14 日で閉じた」と言っているのに対し、設定は 90 日と 60 日であり、利用者が間違った日数を読むことになる点である。

候補の内容は次のとおりである。

```yaml
    permissions:
      issues: write
    steps:
      - uses: actions/stale@v5
        with:
          days-before-issue-stale: 90
          days-before-issue-close: 60
          stale-issue-label: "stale"
          stale-issue-message: "This issue is stale because it has had no activity for 90 days. Comment to keep it open."
          close-issue-message: "This issue was closed because it has been inactive for 60 days since being marked as stale."
          exempt-issue-labels: "RFC"
          days-before-pr-stale: -1
          days-before-pr-close: -1
```

action をタグ(`@v5`)ではなく commit で固定することや、runner を `ubuntu-latest` から固定のバージョンにすることは、あえて候補に入れない。この章の候補をまとめた調査の文書(以下、提案書)の調べでは、アップストリームの他の 10 本の workflow は全て `ubuntu-latest` かタグ参照のどちらかで書かれていて、1 本だけ変えるとアップストリームの慣習に合わず、別の議論になるためである。この workflow は毎日の定期実行でしか動かないので、確かめ方は `actions/stale` の文書にある `exempt-issue-labels` の動きを引用し、マージ後の最初の実行の記録で確認することになる。出し方は PR(数行の差分)である。提案書の評価では優先度は中である。

## 4. 候補 C: device plugin の検出をリリース名ではなく結果で行う

候補 C は、アップストリームのテンプレートが既存の device plugin を、Helm のリリース名ではなく device plugin が必ずマウントするディレクトリを手がかりに探すようにし、別の経路で入った device plugin との二重導入を deploy の時点で止めるものである。GPU と EFA を Kubernetes の Pod から使うには、ノードごとに device plugin という DaemonSet が要る。1 つのノードで同じ種類のデバイス(たとえば `nvidia.com/gpu`)を広告できるのは 1 つだけで、2 つが同時に動くと、数や割り当てが当てにならなくなると考えられる(Issue #19 の表でも、device plugin どうしの二重導入の症状は推論として書かれており、実機では測っていない)。device plugin は、アップストリームの `amazon-eks` のテンプレート自身が Helm で入れるほか、eksctl でクラスタを作ると eksctl が入れ、NVIDIA の GPU Operator を使っていれば GPU Operator が入れる、というように複数の経路から入る。

[`eks-add-gpu-nodegroup.yaml` の `plugin()`](https://github.com/awslabs/awsome-distributed-ai/blob/25d5723cb62ab1f83b0f4307c28c8f3496bfaa53/architectures/amazon-eks/assets/eks-add-gpu-nodegroup.yaml#L867-L884) は、Helm の決まった 2 つのリリース名を探し、無ければ入れ、同じバージョンなら触らず、違えば止まる。

この関数は引数を release、namespace、version、chart、残りの Helm の引数の順に受け取る。

```bash
plugin() {
  local rel=$1 ns=$2 ver=$3 chart=$4 have; shift 4
  have=$(helm list -n "$ns" -f "^$rel\$" -o json | ...)
  if [ -z "$have" ]; then
    helm upgrade --install "$rel" "$chart" --version "$ver" ...
  elif [ "${have#v}" = "${ver#v}" ]; then
    echo "$rel $ver is already installed; leaving it alone"
  else
    fail "the cluster runs $rel $have and this stack pins $ver. ..."
  fi
}
```

リリース名で探すこの方式は、別の経路で入った device plugin を見逃す。この弱点は、[`architectures/amazon-eks` の README](https://github.com/awslabs/awsome-distributed-ai/blob/25d5723cb62ab1f83b0f4307c28c8f3496bfaa53/architectures/amazon-eks/README.md) の 296 行目に既に書かれている。

```text
The stack recognises the device plugins by the two release names it installs
them under, so a device plugin installed another way, by the GPU Operator or
under another release name, goes unseen and the stack installs a second one
beside it. On a cluster created with eksctl, GPU and EFA node groups come with
device plugins eksctl installs: delete those DaemonSets from kube-system, or
create the eksctl node groups with --install-nvidia-plugin=false, before deploying.
```

つまり、アップストリームは既に二重に入ることがあると文書で知らせ、利用者に手で削除してもらう形にしている。候補の内容は、Helm のリリース名ではなく、device plugin が必ず使うものを手がかりに探すことである。device plugin は kubelet と話すために、ノードの `/var/lib/kubelet/device-plugins` というディレクトリを必ずマウントする。そこで `plugin()` の前段に、全 namespace の DaemonSet のうちこのディレクトリをマウントしているものを調べ、このスタックが入れたもの以外があれば、その名前と namespace を挙げて `fail` する処理を足す。

```bash
kubectl get daemonset -A -o json | python3 -c '
import json, sys
for d in json.load(sys.stdin)["items"]:
    vols = d["spec"]["template"]["spec"].get("volumes", [])
    if any(v.get("hostPath", {}).get("path", "").rstrip("/") == "/var/lib/kubelet/device-plugins" for v in vols):
        print(d["metadata"]["namespace"], d["metadata"]["name"])'
```

<!-- FIG name="11-plugin-detect" kind="relation" shows="リリース名で探す今の方式(左、GPU OperatorやeksctlのDaemonSetを見逃す)と、kubelet device-pluginsディレクトリのマウントで探す提案(右、入れ方によらず見つかる)" labels="helm list, nvdp/nvidia-device-plugin, aws-efa-k8s-device-plugin, GPU Operator, eksctl, /var/lib/kubelet/device-plugins, DaemonSet" -->
![device plugin の検出方法の比較を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/11-plugin-detect.png)

この方法なら、GPU Operator が入れたものも eksctl が入れたものも、DaemonSet として入る経路であれば見つかる見込みである(まだ実行して確かめてはいない)。Issue #19 が挙げる 5 つの入れ方のうち、ノードイメージへの同梱(Bottlerocket の NVIDIA バージョン)と EKS Auto Mode の内蔵は DaemonSet ではないので、この方法では見つからない。また、このスタック自身が入れた DaemonSet を除くには結局リリース名などで見分ける必要があり、分かるのは DaemonSet がクラスタにあることだけで、そのノードで動くか、同じリソース名を広告するかまでは分からない。黙って削除するのではなく、止まって知らせるだけにするので、利用者のクラスタを勝手には壊さない。他に 4 つの方法も検討したが、いずれもディレクトリを手がかりにする方法に劣る。見つけたら自動で削除する方法は、速いが、利用者が意図して入れたものを削除する危険がある。GPU Operator の `ClusterPolicy` の有無も見る方法は、GPU Operator には効くが、eksctl や別名の Helm には効かない。ノードの GPU の数(allocatable)で見る方法は、二重に動いていても数は同じに見えることがあり、決め手にならない。提案書の初案だった、既知のラベル(`app.kubernetes.io/name=nvidia-device-plugin` や GPU Operator の `app=nvidia-device-plugin-daemonset` など)を持つ DaemonSet を探す方法は、既知のラベルを付けない入れ方を見逃す。

この候補はテンプレートの動き(止まる条件)自体を変え、他の device plugin(別のベンダーのものなど)も見つけて止まるようになるため、どこまでを「衝突」とみなすかをアップストリームと決める必要がある。出し方は、まず Issue(RFC)で相談し、賛同を得てから PR にする。回避策(手での削除)が README に既に書かれていて緊急ではなく、実装も小さくないため、提案書の評価では優先度は低から中である。確かめ方は、eksctl で device plugin つきのノードグループを先に作り、そこに本スタックを deploy して、既存の device plugin を検出して `fail` することを実機のログで示すことである。

distai-platform 自身の実験(6 章と 7 章で扱う E0 から E4)では、これと近いが別の重なり方、つまり device plugin と DRA のドライバが同じノードで両方動く場合を、NVIDIA と Neuron の両方で確かめている。意図的に両方を載せた条件で、デバイスが 1 つの型(`g7.2xlarge` と `trn2.3xlarge`)を使い、それぞれ 1 回ずつ試した結果、1 枚の GPU や 1 個の Neuron デバイスが警告なしに 2 つの Pod に同時に渡った(NVIDIA の E1 ではイベントも出なかった。Neuron の E4+ の記録は警告が無かったことだけを書いている)。この重なりは、アクセラレータの種類を問わず「DRA のドライバが動くノードで、対応する拡張リソースの allocatable が 0 より大きい」という 1 つの規則で見つけられる見込みである。この規則を調べる、読むだけの検査ツールは、まだ独立 OSS の候補の段階である。

## 5. 候補 D: Capacity Block を探して買う道具

候補 D は、Capacity Block の空き枠を探し、額を確かめて買い、予約の ID を得るまでを AWS CLI だけで行う小さなスクリプトを `architectures/common/` に置き、テンプレートが予約の ID の受け取りしか持たない欠落を埋めるものである。EC2 Capacity Blocks for ML は、GPU のインスタンスを決まった期間(たとえば 1 日)だけ予約して使う買い方である。使うには、まず空いている枠を探し(型、台数、期間、リージョン)、開始日時と Availability Zone と前払い額を見て、買う。買うと予約の ID が発行され、それをノードの起動に渡す。

アップストリームの `amazon-eks` のテンプレートは、既に取得済みの予約の ID を受け取るパラメータを持つ。

```text
CapacityReservationId:
  Type: String
  Default: ""
  Description: >-
    Targeted On-Demand Capacity Reservation or Capacity Block for ML. ...
CapacityReservationType:
  Type: String
  Default: targeted-odcr
  AllowedValues: [targeted-odcr, capacity-block]
```

<!-- FIG name="11-capacity-block-flow" kind="flow" shows="探す→見る→買う→IDを渡すの4段のうち、上流は4(ID受け取り)だけを持ち1〜3の道具が無いこと" labels="search, view offering, purchase, CapacityReservationId, describe-capacity-block-offerings, purchase-capacity-block" -->
![Capacity Block を使うまでの流れを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/11-capacity-block-flow.png)

同じ形のパラメータは `aws-pcs` や `aws-parallelcluster` のテンプレートにもある。一方で、共通の部品を置く [`architectures/common`](https://github.com/awslabs/awsome-distributed-ai/tree/25d5723cb62ab1f83b0f4307c28c8f3496bfaa53/architectures/common) には 3 つのファイル(非公開のバケット、HyperPod の通知など)しか無く、Capacity Block を探して買う手順や道具はどこにも無い。利用者は `aws ec2 describe-capacity-block-offerings` と `purchase-capacity-block` の呼び方を自分で調べることになる。

提案書の調べでは、`describe-capacity-block-offerings` 自体にも制約があり、`start-date-range` は今から 48 時間後まで、`end-date-range` は今から 9 日後までしか指定できない(この値は提案書だけにあり、AWS の文書で確かめ直していない。下の「8 日先まで」は distai-platform の調査スクリプトの照会範囲である)。distai-platform 自身が 2026 年 11 月 17 日に全リージョンを調べたときにも、つまずく点があった。照会には回数の上限があり、エラーを「空きが無い」と扱うと空きがゼロに見えてしまう。また照会は 1 回に 1 件しか返さないことが多く、8 日先までの空きを見るには日をずらして何度も聞く必要があった(4 章を参照)。

候補 C と D はいずれも、distai-platform の持ち主が以前から行っている `amazon-eks` テンプレートへの貢献の続きとして、足りない部分を埋める形になる。

候補の内容は、`architectures/common/` に、AWS CLI だけで動く小さなスクリプトを置き、各構成の README から「`CapacityReservationId` を手に入れる方法」として案内することである。

| サブコマンド | やること | お金 |
| --- | --- | --- |
| `search` | 型、台数、期間(1 日など)、リージョンの一覧を受け取り、空いている枠を開始日時、Availability Zone、前払い額付きで並べる。照会の上限に当たったら待って聞き直し、「分からない」と「空きなし」を分けて表示する | かからない |
| `purchase` | 選んだ枠を買う。明示の指定(たとえば `--yes`)が無ければ買わない。買う前に額を表示する | 前払い(取り消し不可) |
| `list` / `describe` | 持っている予約と、その状態、開始と終了を見る | かからない |

distai-platform の持ち主が別のリポジトリで書いた `watch-and-buy-capacity-block.sh` と `manage-capacity-block.sh` が、空き枠のポーリング、条件に合う枠の自動購入、予約 ID の SSM Parameter Store への反映、予約の一覧と詳細と取り消しを既に持っており、GPU の型を前提に一般化して渡すことができる。照会の上限での聞き直しと、「分からない」と「空きなし」を分ける部分は、容量の調査スクリプト(`capacity_survey.py`、4 章)の機能である。自動購入は 9 章で退けた形なので、渡すときは購入を明示の指定がある場合だけに絞る。アップストリームの方針に合わせ、Trainium の型を前提にした書き方はしない(型は引数なので、使うかどうかは利用者が選べる)。類似の Issue として「`[aws-pcs]` の GPU CNG テンプレートが `CapacityReservationId` 経由の targeted ODCR を使えない」という open の Issue が見つかったが、これは既存の ODCR を CloudFormation から使えないという不具合であり、Capacity Block を探して買う機能が無いこととは重複しない。置き場所(`common` か、各構成の下か)と、購入まで道具に入れるか探すだけにするかは、アップストリームの判断が要る。購入は課金が発生し取り消せない操作なので、Issue(機能要望のテンプレート)で先に合意する。提案書の評価では優先度は中である。確かめ方は、`search` を実際のリージョンで流して枠の一覧が返ることを示すことである。`purchase` まで入れるなら、小さな枠を実際に 1 つ購入してノードが起きることも示す。

## 6. DRA に関する上流への候補(実験のあと)

device plugin と DRA の実験(E0 から E4、6 章と 7 章)からは、候補 C と重なるものを含めて、いくつかの候補が挙がっている。この節の宛先はアップストリームに限らず、Karpenter、Kueue、GPU Operator、EFA の chart のような上流のプロジェクトも含む。Layer 1 と Layer 2、広告する側、opt-in と opt-out、ResourceSlice、`pcieRoot` は 5 章、E4+ は 7 章、E7(大型 GPU での位置合わせの測定の繰り返し)は 5 章の実験計画で扱う。

| # | 候補 | 根拠 | 出し方 |
| --- | --- | --- | --- |
| 1 | device plugin の検出を、リリース名ではなく結果(ノードの allocatable、DRA の ResourceSlice、ノード上の Pod)で行う | 候補 C の動機に近い事実に基づく。実験 E4+ は、GPU(E1)で見た「同じデバイスが警告なしで 2 つの Pod に渡る」壊れ方が、Neuron でも device plugin と DRA ドライバを同じノードに載せると起きることを確かめた。Neuron の chart は 1 つの release で片方の方式しか動かさないので、二重に広告されるのは方式ごとに 2 つの release を入れる構成(5 章の決定 2)をとったときである。検査は allocatable を使うが、これは DRA ドライバが動くノードという条件と組み合わせた場合で、4 節で決め手にならないとしたのは allocatable 単独で見る場合である | 読むだけの検査ツールを独立させてから、テンプレートで呼ぶ形にする |
| 2 | 広告する側を opt-in にし、クラスタに 1 つしか要らない物をノードグループのスタックから外す | ノードグループのスタックが device plugin を入れ、削除後に残ることがある | テンプレートの分け方の提案(RFC) |
| 3 | DRA(GPU と EFA の `pcieRoot`)の例と測定 | アップストリームには DRA の扱いが無い | 実験 E7(大型 GPU の DRA 検証)の後に出す。1 節で却下された RFC と範囲が重なるので、出すかどうかは改めて持ち主が判断する |
| 4 | 層と入り口の表を README に足す | 今の注意は散文 1 段落だけ | 文書の PR |

ほかにも、アップストリームに出せそうな候補として、EFA の device plugin の対象インスタンスタイプの追加(`g6e.12xlarge` が既定の対象に無く、EFA が 0 になる挙動が見つかっている)、Karpenter への Neuron の DRA の gate 追加の提案、KEP-5004 に基づく Pod の挙動の報告、Kueue への「device plugin と DRA を同じ名前で数える」設定例の提案、GPU Operator への「2 つの設定(`ClusterPolicy` と `GPUCluster`)のノード単位での共存」の Issue がある。このうち Karpenter、KEP-5004、Kueue に関するものは distai-platform 内の実験(E0 から E4)で見つかった挙動に基づく。EFA の chart の件は以前の distributed-ai での経験に基づき(E0 から E4 は EFA を使っていない)、GPU Operator の排他は NVIDIA の文書に基づく(実験では GPU Operator を入れていない)。どれも E0 から E4 が終わった段階でまだ出していない。

調査では、候補として検討したが最終的に落とした点も 2 つある。ネットワークカードの階層を CloudFormation の `Conditions` の羅列で表している書き方は、読みにくく見えるが、専用のスクリプトで生成し CI で生成結果と一致するかを検証する意図的な設計であり、提案すると見かけだけの書き換えになるため見送った。ファイルを移動する PR で中身とファイルの実行権限が一致しているかを確かめる規則が無い点も検討したが、手元の clone が浅い(shallow clone)ために過去の事故の有無を履歴で確かめられず、根拠が弱いため見送った。候補をまとめる作業自体も、実装やレビューとは別の担当のセッションが行っている(10 章の役割分担と同じ考え方である)。

## まとめ

アップストリームへ返せる候補は、数行で出せるもの(候補 A: PR テンプレートの伏せ字注意、候補 B: stale workflow の権限と文面の修正)と、先に Issue での相談が要るもの(候補 C: device plugin の検出方法、候補 D: Capacity Block を探して買う道具)に分かれる。Neuron を前提にした提案はアップストリームの方針に合わないためアップストリームには出さない。DRA に関する知見のうち Neuron に関わるもの(Karpenter への Neuron の gate など)は、アップストリームではなく、Karpenter のようなアップストリーム以外の上流プロジェクトへの候補として扱う。どれも、まだ Issue も PR も出していない段階の候補であり、出す・出さない・いつ出すかはリポジトリの持ち主が判断する。
