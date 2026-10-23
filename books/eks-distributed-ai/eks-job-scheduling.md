---
title: "Advanced03 - ジョブスケジューリングを Kueue で体験する"
free: true
---

本章が前提にするインフラの変更は、まだリリースタグにも `main` にも入っておらず、`feat/kueue-job-scheduling` ブランチにあります。対象は `infra/eks/kueue.tf`、`infra/eks/manifests/kueue-reference/`、`charts/experiments` の `trainjobTrain.name` と `trainjobTrain.queueName` です。このリポジトリのリリース v0.2.1 との違いのうち、Terraform の設定ファイルは `kueue.tf` の追加だけです。v0.2.1 のまま同じ変数で apply してきたクラスタなら、手順 2 の `terraform apply` で増えるのは Kueue だけです。本章はこのブランチのコミット `bc22f78` で検証しました。ワークショップ手順の最初で、このコミットに切り替える手順を示します。

本章は Basic04 で立てた `gpu-ddp` プールに、Kueue という admission(いつ Pod を作らせるか)の層を1枚足します。試すのは、同じプールに 3 つの TrainJob を一度に投げたとき何が起きるかです。Kueue を入れる前は、空きがなければ Pod が `Pending` のまま詰まり、Karpenter が在庫のある型を探し続けるだけでした。Kueue を入れると、容量に収まる分だけが先に通り、残りはキューで順番を待ち、空いた分だけ順に通っていきます。

:::message
Kueue は kube-scheduler を置き換えません。ノードも作りません。決めるのは「いつ Pod を作らせるか」だけで、できた Pod をどのノードに置くかは今まで通り kube-scheduler が決め、置けるノードが足りなければ Karpenter がノードを足します。
:::

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章が足すのは、既存の TrainJob/RayJob と Karpenter の間に挟まる 1 枚の admission 層です。

![Kueue によるジョブスケジューリングの構造](/images/books/eks-distributed-ai/job-scheduling-kueue.png)

容量内(①→②→③→④)なら今まで通り即座に Pod ができて Karpenter が動きますが、容量を超えた分は③の手前で止まり、Pod を作らないまま待ちます(③')。先行ジョブが完了して quota が空くと、保留していたジョブが②に戻って再判定されます。queue-name label の付いていない TrainJob/RayJob はこの図の①を素通りして今まで通り直接 Pod を作ります。本章の設定では、Kueue は明示的に名乗ったワークロードだけを見るので、何もしなければ何も変わりません。

## Kueue の内部構成

![Kueue on EKS: the webhook suspends the TrainJob, a reconciler creates a Workload that goes through LocalQueue to ClusterQueue, and on admission Kueue adds the flavor's nodeSelector and clears suspend, after which Trainer, kube-scheduler and Karpenter work as before](/images/books/eks-distributed-ai/job-scheduling-kueue-arch.png)

上の図は、TrainJob を 1 つ投入したときに EKS の中で何が起きるかを、部品ごとに並べたものです。先に、図に出てくる Kueue の 3 つのオブジェクトを説明します。

| オブジェクト | 置き場所 | 役割 |
|---|---|---|
| LocalQueue | ジョブと同じ namespace | 利用者がジョブを投入する窓口です。ジョブは `kueue.x-k8s.io/queue-name` ラベルで、投入先の LocalQueue の名前を指定します。LocalQueue 自身は枠を持たず、どの ClusterQueue に流すかだけを決めています。 |
| ClusterQueue | namespace に属さない、クラスタ全体のオブジェクト | GPU などの枠を持ち、通してよいかを判断します。複数の namespace の LocalQueue が、同じ ClusterQueue を共有できます。 |
| ResourceFlavor | namespace に属さない、クラスタ全体のオブジェクト | 枠の中身がどのノードの資源かを表します。本章の構成では、Karpenter の NodePool の名前をノードのラベルとして持たせています。 |

Kueue の実体は `kueue-system` namespace の `kueue-controller-manager` という 1 つの Deployment で、webhook とコントローラはこのプロセスの中にあります。CRD と webhook の設定はクラスタ全体に登録されますが、`kueue.tf` は webhook が働く namespace を `managed-by-kueue=true` のラベルを付けたものだけに絞っています。理由は下の「落とし穴」の 1 つ目で説明します。TrainJob を 1 つ投入すると、図の番号の順に次のことが起きます。主体は動く部品、相手はその部品が手を入れる対象です。

| 番号 | 主体 | 相手 | すること | その後の状態 |
|---|---|---|---|---|
| 1 | 利用者 | EKS の API server | `kueue.x-k8s.io/queue-name: gpu-ddp` のラベルを付けた TrainJob を `kubectl apply` で送る | 作成のリクエストが API server に届く |
| 2 | Kueue の webhook | API server が保存しようとしている TrainJob | 作成を止めずに `suspend: true` を書き加える | TrainJob は止まった状態で保存される。Trainer が作る JobSet も止まった状態のままで、Pod は作られない |
| 3 | kueue-controller-manager のジョブ用コントローラ | 保存された TrainJob | 必要な GPU や CPU の量をまとめた Workload を 1 つ作る | Kueue が待ち行列で扱う単位ができる |
| 4 | kueue-controller-manager のスケジューラ | LocalQueue を経て ClusterQueue に並んだ Workload | いま使われている量に要求量を足し、ClusterQueue の ResourceFlavor ごとの枠に収まるかを判断する。本章は `blockAdmission` を有効にしているので、先に通したジョブの Pod が全部 Ready になるまでは次を判断しない | 収まらなければ TrainJob は止まったままで、枠が空くのを待つ |
| 5 | kueue-controller-manager | 枠に収まった Workload の TrainJob | Workload を admitted にし、ResourceFlavor の nodeLabels を nodeSelector として、tolerations があればそれも付けて、`suspend` を外す | Workload は admitted、TrainJob は `suspend: false` になる |
| 6 | Trainer と JobSet・Job のコントローラ | `suspend` が外れた TrainJob | Trainer が、suspend の解除と 5 で付いた nodeSelector・tolerations を JobSet に伝え、JobSet と Job のコントローラが Pod を作る | Pod ができ、置き先が未定のまま並ぶ |
| 7 | kube-scheduler | 置き先が未定の Pod | nodeSelector に合い、空きのあるノードに bind する。置けない Pod には Unschedulable の印を付ける | 置けた Pod は動き出し、残りは Pending のまま |
| 8 | Karpenter | Unschedulable の印が付いた Pod | 条件に合う EC2 ノードを起動する | ノードが Ready になると 7 に戻り、kube-scheduler が残りを bind する |

Kueue が手を入れるのは 2 から 5 までで、6 から 8 の Trainer・kube-scheduler・Karpenter の動き方は変わりません。止まっている TrainJob には Pod が無いので、Karpenter はそのためのノードを起動しません。これが、ワークショップの手順 4 で容量を超えた分のノードが立たない仕組みです。

ただし、5 で通ったからといって、7 で全部の Pod が置けるとは限りません。quota は Kueue が通してよい量を数で表したもので、spot の在庫やノードごとの GPU の空き方までは見ていないからです。この取りこぼしへの備えが、下の「落とし穴」の 3 つ目です。

## なぜ Kueue が要るか

Basic04 で作った `gpu-ddp` プールに、4 種のインスタンスタイプを混在させる設計がありました(g6/g5 の世代混在、spot 優先)。これは「1 つのジョブが確保に失敗しないようにする」ための工夫で、「複数のジョブが同時に来たときどちらを先に通すか」は決めていません。3 つの TrainJob を同時に `kubectl apply` したら、Karpenter は 3 つ分の Pod を一斉に見て、全部を置けるだけのノードを起動しようとします。小さなプールでは想定より多くのノードが一瞬で立ち、意図しない課金が走ります。

Kueue はここに容量の概念を持ち込みます。ClusterQueue に「このプールは nvidia.com/gpu が同時に N までしか使わせない」と宣言すると、N を超える分の TrainJob は Pod を作る前に止まります。Pod が存在しないので Karpenter から見れば何も起きておらず、ノードは起動しません。先に通ったジョブが終わって容量が空くと、待っていた次のジョブの Pod が作られ、そこで初めて Karpenter が動きます。

## infra の責務をどこで切るか

`accelerator_pools`(Basic04)と同じ分け方をします。

| 層 | 何を置くか | どこに | 誰が変える |
|---|---|---|---|
| コントローラ | Kueue 本体(Helm release、CRD) | `infra/eks/kueue.tf`、常時インストール | インフラ担当、`terraform apply` |
| キュー定義 | ClusterQueue / ResourceFlavor / LocalQueue | `infra/eks/manifests/kueue-reference/`(リファレンスのみ) | 各ワークロードの利用者、`kubectl apply` |

コントローラ側を terraform で常時インストールできるのは、Kueue がラベルの付いた namespace 以外には何もしないからです。`kueue.tf` は `managedJobsNamespaceSelector` を明示的なオプトインラベル(`managed-by-kueue=true`)に絞っているので、ラベルの付いていない既存の namespace は Kueue が入ったことに気づきません。常時インストールしておいても、使わない限り何も変わらないなら、Karpenter や Trainer と同じ「常にある」コンポーネントにしてしまってよい、というのが Basic03 以来この本が取っている立場です。

一方キュー定義側は、どのプールを、どれだけ、どの namespace に見せるかという、ワークロードの形そのものです。`accelerator_pools` を terraform にインラインで書かなかった理由(1 つの map 変数に複数チームが書くと辞書順で後が勝つ、Basic04 の「今後の改善」で触れた話)がそのまま当てはまるので、ClusterQueue/LocalQueue もリファレンス YAML として置き、使う側が自分の値に差し替えて `kubectl apply` する形にしています。

## 導入前に知っておくべき3つの落とし穴

Kueue 自体は kubernetes-sigs の下で育っているプロジェクトで、本章では v0.20.2 を使います。それでも次の 3 点は、公式ドキュメントとソースを読まずに入れると実機で踏みます。

### 1. webhook の既定スコープはクラスタ全体

Kueue は `batch/v1` Job、JobSet、TrainJob、RayJob などの作成を webhook で横取りして、queue-name label があるかどうかを見ます。この webhook の `namespaceSelector` の既定値は「`kube-system` と Kueue 自身の namespace 以外すべて」です。つまり何も設定しないと、**このクラスタ上の他の用途(本章と無関係な namespace の Job/TrainJob すべて)が、Kueue の webhook を経由することになります**。しかも大半の webhook は `failurePolicy: Fail` です。Kueue の webhook Pod が落ちている間は、ラベルの有無に関わらずクラスタ全体で Job/TrainJob の作成が失敗します。

Kueue 自身がこれに対する公式のやり方を持っています([Opt-in Namespace Management](https://kueue.sigs.k8s.io/docs/tasks/manage/enforce_job_management/opt_in_namespace_management/))。`managedJobsNamespaceSelector` をラベル一致に変えるだけです。

```yaml
managedJobsNamespaceSelector:
  matchLabels:
    managed-by-kueue: "true"
```

`kueue.tf` はこれを既定値として入れています。ラベルを付けた namespace だけが Kueue の視界に入り、それ以外は存在しないのと同じです。この本のクラスタは Basic 各章や他の検証が同じ namespace を使い回すことがあるので、全 namespace を既定でスコープに入れる方の設定は選べません。

### 2. `kueuectl stop` は既定で実行中のジョブも退避させる

Kueue には ClusterQueue を一時停止する `kueuectl stop clusterqueue` がありますが、これは**新規受付を止めるだけでなく、既定では実行中の Workload も evict** します(ソース上は `StopPolicy: HoldAndDrain` が既定で、`Hold` に留めるには `--keep-already-running` を明示する必要があります)。「新規だけ止めたい」つもりで素のまま叩くと、今動いている学習ジョブが全部落ちます。本章では使いませんが、将来運用に組み込む場合はこの既定を知っておく必要があります。

### 3. `waitForPodsReady` を入れないと複数ノードジョブがデッドロックしうる

Kueue の admission は「ジョブ全体を1単位として通す/止める」までしか見ません。quota が本当にあれば問題は起きませんが、quota は Kueue が admit を許す量であって、Karpenter が実際に確保できる保証ではありません。たとえば 2 ノードずつ要る TrainJob を quota 上は 2 つとも admit できても、spot 在庫が足りず実際には 2 ノードしか確保できない場面では、2 つのジョブが 1 ノードずつを握り、どちらももう 1 ノードを待ったまま完成しない状況が起こりえます。`waitForPodsReady` を有効にして `blockAdmission: true` を設定すると、先に admit したジョブの Pod が全部 Ready になるまで次を admit しないので、この取り合いは起きません。ただし、先のジョブ自身に要るノードが確保できなければ、そのジョブは決めた時間が過ぎるまでノードを握り続け、時間が過ぎると取り下げられて待ち行列に戻ります。また、この設定は ClusterQueue ごとではなく Kueue 全体に効きます。`kueue.tf` では既定で有効にしています。

# ワークショップ実施

はじめにシェルを対象クラスタへ向けます。Basic01 手順 3 と同じ 5 行です。

```bash
cd ~/distributed-ai-v0.2.1
export AWS_PROFILE=default
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
source infra/scripts/distai-env.sh
```

本章のインフラの変更はまだ `feat/kueue-job-scheduling` ブランチにあるので、作業ディレクトリを本章で検証したコミットに切り替えます。作業ディレクトリは Basic01 のインストーラが `--depth 1` で clone したものなので、コミットも同じく浅く取得します。後片付けで元に戻せるよう、切り替える前のコミットを `ORIG_COMMIT` に控えます。`git status --porcelain` が何か表示した場合は、手元の変更を退避してから進めてください。

```bash
git status --porcelain
export ORIG_COMMIT=$(git rev-parse HEAD)
git fetch --depth 1 origin bc22f781e569dff703f5bdd63e768f799985fe00
git -c advice.detachedHead=false checkout --detach FETCH_HEAD
ls infra/eks/kueue.tf infra/eks/manifests/kueue-reference/queue.yaml.tmpl
source infra/scripts/distai-env.sh
```

最後の `source` は、切り替えたコミットの `distai-env.sh` を読み直すためのものです。

前提は次のとおりです。

| 前提 | どこで用意するか |
|---|---|
| `gpu-ddp` プール(Karpenter NodePool)が存在すること | [Basic04](https://zenn.dev/tosshi/books/eks-distributed-ai/viewer/eks-accelerator-pools) |
| ECR に `ddp-sample:v1` があること | [Basic02](https://zenn.dev/tosshi/books/eks-distributed-ai/viewer/eks-cpu-ddp) |
| 共有 PVC `shared-claim` が `Bound` であること | [Basic02](https://zenn.dev/tosshi/books/eks-distributed-ai/viewer/eks-cpu-ddp) |

## 1. 前提を確認する

```bash
k get nodepool gpu-ddp >/dev/null 2>&1 && echo "OK gpu-ddp プール" || echo "NG gpu-ddp プール"
k get pvc shared-claim -o jsonpath='{.status.phase}' 2>/dev/null | grep -qx Bound && echo "OK 共有 PVC shared-claim" || echo "NG 共有 PVC shared-claim"
```

## 2. Kueue を入れる

`infra/eks` で `terraform apply` すると `kueue.tf` が Kueue 本体(コントローラと CRD)を `kueue-system` namespace に入れます。これは常時インストールのコンポーネントなので、既に入っていれば何も変わりません。`yes` と答える前に、表示された計画に Kueue 以外の追加・変更・削除が無いことを確かめてください。他の章で `-var` や `-var-file` を付けて apply してきた場合は、同じ引数を付けます。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
terraform apply
k -n kueue-system rollout status deploy/kueue-controller-manager --timeout=5m
k get pods -n kueue-system
```

実機出力:

```text
NAME                                       READY   STATUS    RESTARTS   AGE
kueue-controller-manager-544dcdb599-q4xsg   1/1     Running   0          15s
```

まだ何のキューも定義していないので、クラスタ上のどの Job/TrainJob も今まで通りに動きます。

## 3. ClusterQueue/LocalQueue を定義する

リファレンス YAML に自分の値(namespace・対象プール・GPU 枠)を埋めて適用します。本章では `distai` namespace、`gpu-ddp` プール、GPU 枠 2(=1 ジョブ分、2 ノード x 1 GPU)で試します。

まず `distai` namespace を Kueue の対象にオプトインします。これを忘れると、次の手順で queue-name を付けても Kueue はそもそも `distai` を見ないため、TrainJob は普通に(キューを経由せず)即時に通ってしまいます。

```bash
k label namespace distai managed-by-kueue=true --overwrite
cd "$(git rev-parse --show-toplevel)"/infra/eks/manifests/kueue-reference
sed -e 's/__NAMESPACE__/distai/g' \
    -e 's/__NODEPOOL_NAME__/gpu-ddp/g' \
    -e 's/__GPU_QUOTA__/2/g' \
    queue.yaml.tmpl | k apply -f -
```

実機出力:

```text
namespace/distai labeled
resourceflavor.kueue.x-k8s.io/gpu-ddp created
clusterqueue.kueue.x-k8s.io/gpu-ddp-cq created
localqueue.kueue.x-k8s.io/gpu-ddp created
```

ラベル付けを `queue.yaml.tmpl` 側の `Namespace` リソースにせず別コマンドにしているのは、このテンプレートの自然な逆操作である `kubectl delete -f` が、同じ `Namespace` を含んでいたら namespace ごと(このテンプレートが作ったのではない共有 PVC まで)消してしまうためです。

## 4. 3 つの TrainJob を同時に投げる

Basic04 と同じ `ddp-sample` イメージ、同じ `torch-distributed-eks` ランタイムを使い、`trainjobTrain.name` で別々の TrainJob 名を付け、`trainjobTrain.queueName` でキューに乗せます。`outputSubdir` も別々にします(同じフォルダだと ddp.py が互いのスナップショットを拾って再開してしまうため)。

```bash
export NAMESPACE=distai
IMAGE=$(terraform -chdir="$(git rev-parse --show-toplevel)/infra/eks" output -raw ddp_sample_ecr_url):v1

for n in a b c; do
  helm template exp "$(git rev-parse --show-toplevel)"/infra/eks/charts/experiments \
    --set namespace=$NAMESPACE \
    --set trainjobTrain.enabled=true \
    --set trainjobTrain.image="$IMAGE" \
    --set trainjobTrain.nodeRole=gpu-ddp \
    --set trainjobTrain.gpu.enabled=true \
    --set trainjobTrain.gpu.count=1 \
    --set trainjobTrain.nprocPerNode=1 \
    --set trainjobTrain.totalEpochs=3 \
    --set trainjobTrain.name=ddp-trainjob-$n \
    --set trainjobTrain.queueName=gpu-ddp \
    --set trainjobTrain.outputSubdir=trainjob-gpu-ddp-$n \
    --set sharedStorage.existingClaimName=shared-claim \
  | k apply -f -
done
k -n distai get trainjob ddp-trainjob-a ddp-trainjob-b ddp-trainjob-c
```

実機出力(投入直後):

```text
NAME             STATE       AGE
ddp-trainjob-a   Suspended   18s
ddp-trainjob-b   Suspended   12s
ddp-trainjob-c   Suspended   6s
```

3 つとも `Suspended` と出ています。TrainJob の `STATE` 列は、最後に付いた condition の種類を出すだけで、その condition が True か False かは見ていません。そのため、どれが実際に Pod を作らせてよいかはこの列だけでは分かりません。どれが先に通るかは `Workload` オブジェクトの `ADMITTED` 列で見えます。

```bash
k get workloads -n distai
```

実機出力:

```text
NAME                            QUEUE     RESERVED IN   ADMITTED   FINISHED   AGE
trainjob-ddp-trainjob-a-c573a   gpu-ddp   gpu-ddp-cq    True                  45s
trainjob-ddp-trainjob-b-4df28   gpu-ddp                 False                 39s
trainjob-ddp-trainjob-c-515dd   gpu-ddp                                       33s
```

`a` だけが `ADMITTED: True` で GPU 枠 2 を使い切っています(`k get clusterqueue gpu-ddp-cq -o jsonpath='{.status.flavorsUsage}'` で `nvidia.com/gpu` の使用量が確認できます)。`b` と `c` はどちらも `RESERVED IN` が空で、まだ枠を割り当てられずに待っています。`ADMITTED` 列が `False` か空かは、Admitted の condition がもう付いたかどうかの違いで、順番の前後を表すものではありません。この時点で Karpenter が実際に起動するのは `a` の分の 2 ノードだけです。

```bash
k get nodeclaims -l karpenter.sh/nodepool=gpu-ddp
```

実機出力:

```text
NAME            TYPE         CAPACITY   ZONE              NODE                                             READY   AGE
gpu-ddp-5qcqh   g6.2xlarge   spot       us-east-2a   ip-10-0-14-104.us-east-2.compute.internal   True    95s
gpu-ddp-gm2c8   g6.2xlarge   spot       us-east-2a   ip-10-0-24-166.us-east-2.compute.internal   True    95s
```

`gpu-ddp` プールの型はどれも GPU を 1 枚だけ積むので、Kueue を入れていなければ、ここで `b`・`c` の分も合わせて最大 6 ノード分の確保が同時に走ります。

## 5. 順番に通っていく様子を見る

`a` が完走すると GPU 枠が空き、`b` が自動的に admit されます。ノードは消えずに再利用されます(Karpenter の consolidation が働く前に次のジョブが来たため)。各ジョブが短時間(3 epoch)で終わるため、確認のタイミングによっては `b` も既に完走していることがあります。

```bash
k -n distai get trainjob ddp-trainjob-a ddp-trainjob-b ddp-trainjob-c
```

実機出力(`a`・`b` が完走し、`c` がまだ順番待ちの瞬間):

```text
NAME             STATE       AGE
ddp-trainjob-a   Complete    4m11s
ddp-trainjob-b   Complete    4m5s
ddp-trainjob-c   Suspended   3m59s
```

最終的に `c` も admit されて完走し、3 つとも `Complete` になります。最初の `k wait` は、3 つが完走するまで待ちます。

```bash
k -n distai wait --for=condition=Complete trainjob/ddp-trainjob-a trainjob/ddp-trainjob-b trainjob/ddp-trainjob-c --timeout=30m
k -n distai get trainjob ddp-trainjob-a ddp-trainjob-b ddp-trainjob-c
k get clusterqueue gpu-ddp-cq
```

実機出力:

```text
NAME             STATE      AGE
ddp-trainjob-a   Complete   4m57s
ddp-trainjob-b   Complete   4m51s
ddp-trainjob-c   Complete   4m45s

NAME         COHORT   PENDING WORKLOADS
gpu-ddp-cq            0
```

ログを rank 0 で、3 本とも確認します。

```bash
for n in a b c; do
  echo "=== ddp-trainjob-$n ==="
  k -n distai logs -l jobset.sigs.k8s.io/jobset-name=ddp-trainjob-$n,batch.kubernetes.io/job-completion-index=0 --tail=-1
done
```

実機出力(`a` の分のみ抜粋。`b`・`c` も同じ形で、出力先のパスだけがそれぞれ `trainjob-gpu-ddp-b/`・`trainjob-gpu-ddp-c/` になります):

```text
=== ddp-trainjob-a ===
[rank 0/2] backend=nccl cuda_available=True device_count=1
[rank 0/2] downloading MNIST to /shared/mnist-data
[rank 0/2] starting training: 3 epochs, batch_size 32
[rank 0/2] epoch 0 | steps 938 | loss 0.2001
[rank 0/2] epoch 0 | snapshot saved to /shared/output/trainjob-gpu-ddp-a/snapshot.pt
[rank 0/2] epoch 1 | steps 938 | loss 0.0874
[rank 0/2] epoch 1 | snapshot saved to /shared/output/trainjob-gpu-ddp-a/snapshot.pt
[rank 0/2] epoch 2 | steps 938 | loss 0.0581
[rank 0/2] epoch 2 | snapshot saved to /shared/output/trainjob-gpu-ddp-a/snapshot.pt
[rank 0/2] done
```

3 本ともそれぞれ `trainjob-gpu-ddp-$n/` に書いており、互いのスナップショットを拾っていないことが確認できます。

::::details queue-name を付け忘れたらどうなるか

`trainjobTrain.queueName` を省くと、TrainJob は label なしで作られ、Kueue を経由せず直接 Pod を作ります。GPU 枠がいくつであっても無視され、Karpenter が即座に確保を試みます。これは機能として正しい挙動です(opt-in の設計なので、ラベルを付けないことはキューを使わない選択です)が、「キューに入れたつもりなのに即座に全部動いた」という形で気づくことが多い落とし穴です。`k -n distai get trainjob ddp-trainjob-a -o jsonpath='{.metadata.labels}'` に `kueue.x-k8s.io/queue-name` が無ければ、それが原因です。

もう一つの原因は namespace 側のラベル忘れです。`distai` に `managed-by-kueue=true` が付いていないと、Kueue の webhook がそもそも `distai` の TrainJob を見ません。TrainJob 側に queue-name label があっても、`k get workloads -n distai` に何も出ない(Workload オブジェクトが作られていない)状態になります。`k get namespace distai --show-labels` で確認してください。

::::

## 6. 後片付け

:::message
本章で作った ClusterQueue/ResourceFlavor/LocalQueue と 3 つの TrainJob は、他の章・他の検証と共有しているクラスタに影響しないよう必ず削除してください。Kueue 本体(`kueue.tf`)は Basic03 の Karpenter と同じ「常にあるコンポーネント」として残して構いません。
:::

```bash
k -n distai delete trainjob ddp-trainjob-a ddp-trainjob-b ddp-trainjob-c
k -n distai delete localqueue gpu-ddp
k delete clusterqueue gpu-ddp-cq
k delete resourceflavor gpu-ddp
k label namespace distai managed-by-kueue-
k get nodeclaims -l karpenter.sh/nodepool=gpu-ddp -w
```

TrainJob を消しても、共有 PVC 上の `/shared/output/trainjob-gpu-ddp-a/` などのスナップショットは残ります。本章をもう一度実行すると、ddp.py がそれを拾って学習を再開するので、やり直すときは手順 4 の `outputSubdir` を別の名前に変えてください。

最後の `-w` は終了しません。`NodeClaim` が消えたら Ctrl-C で抜けます。Pod が無くなると Karpenter が consolidation でノードを回収します。Basic04 と同じく非同期で数分かかります。`k label namespace distai managed-by-kueue-` は手順 3 で付けたオプトインラベルを外す操作で、これを忘れると `distai` は ClusterQueue/LocalQueue を消した後も Kueue の管理対象のままになり、他の章でこの namespace を使うときにも Kueue の webhook を経由し続けます。

:::message alert
Kueue 本体(`kueue.tf`)は Basic03 の Karpenter と同じ「常にあるコンポーネント」として残して構いませんが、`terraform apply -var kueue_enabled=false` や `helm uninstall` での丸ごと削除は、**このクラスタ上の全 namespace の** ClusterQueue/LocalQueue/ResourceFlavor/Workload を CRD もろとも削除します。Kueue の CRD はこの chart の `templates/` に入っているため(Karpenter の `karpenter-crd` のような別 chart ではない)、コントローラの削除とクラスタ全体のキュー状態の削除が 1 回の操作になります。本章の後片付け(上のコマンド)だけで十分なら、コントローラ自体は消さないでください。他の章・他チームが Kueue を使い始めている可能性がある限り、丸ごと削除は避けます。
:::

Kueue を残すなら、作業ディレクトリは本章のコミットのまま使い続けます。元のコミットに戻した状態で `infra/eks` の `terraform apply` を実行すると、`kueue.tf` が無いので Kueue が削除され、上の alert と同じことが起きるからです。Kueue を含むリリースタグが出るまでは、他の章の `terraform apply` も本章のコミットで実行してください。

Kueue を外すと決めて元のコミットに戻す場合は、リポジトリの最上位に移ってから戻します。

```bash
cd "$(git rev-parse --show-toplevel)"
git -c advice.detachedHead=false checkout --detach "$ORIG_COMMIT"
source infra/scripts/distai-env.sh
```

# まとめ

Kueue のコントローラは `kueue.tf` で常時インストールしておき、ClusterQueue/LocalQueue は `accelerator_pools` と同じ考え方でリファレンス YAML のまま使う側が値を埋める形にしました。`managedJobsNamespaceSelector` をラベルのオプトインに絞ったことで、他の章・他の検証と共有しているこのクラスタに影響を広げずに導入できます。3 つの TrainJob を同時に投げると、容量に収まる分だけが admit され、残りは Pod を作らずに待ち、空いた分だけ順に通っていくことを実機で確認しました。

# 今後の改善

| なぜ改善すべきか | 改善対象 | 改善案 |
|---|---|---|
| 本章は1つの ClusterQueue が1つの NodePool を見る最小構成。複数チームが複数プールを奪い合う状況は試していない | マルチテナントの公平性 | Kueue の [cohort](https://kueue.sigs.k8s.io/docs/concepts/cohort/) で複数 ClusterQueue 間の quota を共有する/fair sharing を試す |
| Kueue は admission だけで、ノード単位のタイトなビンパッキングや GPU の分割共有は見ない | 密な多テナント共有が要る場合の次の一手 | [Volcano](https://github.com/volcano-sh/volcano)(CNCF Incubating、pod-group の gang scheduling、Kubeflow Trainer v2/KubeRay 統合あり)か [NVIDIA KAI-Scheduler](https://github.com/kai-scheduler/KAI-Scheduler)(CNCF Sandbox、階層キュー、GPU の分割共有。Volcano と同じく、Pod の `schedulerName` で指定して kube-scheduler の代わりに配置まで判断する)への移行を検討する。どちらも本章の調査時点ではこのリポジトリに未導入 |
| KAI-Scheduler は配置を決めるスケジューラ自身が gang scheduling・階層キュー・GPU の分割共有を判断するので、GPU の分散学習で候補になるが、Karpenter と組めるかを確かめていない | KAI-Scheduler と Karpenter の組み合わせ | KAI-Scheduler を Karpenter と組む構成を Advanced の章として追加し、実機で確かめる。Karpenter は `PodScheduled=False` で Unschedulable の印が付いた Pod を見てノードを足すので、KAI が置けなかった Pod にこの印を付けるか、TrainJob を PodGroup に束ねられるかを見る。KAI-Scheduler の[設計提案](https://github.com/kai-scheduler/KAI-Scheduler/blob/96eb3764caad/docs/developer/designs/queue-admission-gating/README.md)によると、KAI-Scheduler は枠で待たせているジョブでも Pod の作成を止めないので、Karpenter がその Pod のために GPU ノードを立てることがある。この提案はまだ Proposed の段階なので、Kueue を前に置いてジョブを Pod の手前で止め、配置だけを KAI-Scheduler に任せる構成で、この無駄なノードを防げるか、両者の枠をそろえられるかも確かめる |
| GPU 枠を人手で `__GPU_QUOTA__` に書いているが、Capacity Block のように期限付きでまとまった容量を間欠的に使う運用(買う→ある時間だけ使う→畳む)には足りない | 時間窓付きの容量運用 | Kueue v0.20.0 で alpha として入った [DynamicQuotaOrchestration](https://github.com/kubernetes-sigs/kueue/releases/tag/v0.20.0) の feature gate で quota を実際の容量に追従させられるかを試す。足りなければ、quota を実際に使える容量へ同期する reconciler、ジョブの推定実行時間と残り時間を比較して admit を止めるカスタム AdmissionCheck、ノード撤去後もログと終了理由を追えるようにする外部台帳(S3/DynamoDB)を追加する |

# 参考資料

- [Kueue](https://kueue.sigs.k8s.io/) - 公式ドキュメント
- [Opt-in Namespace Management](https://kueue.sigs.k8s.io/docs/tasks/manage/enforce_job_management/opt_in_namespace_management/) - webhook のスコープをラベルで絞る公式の手順
- [Wait for pods ready](https://kueue.sigs.k8s.io/docs/tasks/manage/setup_wait_for_pods_ready/) - 複数ノードジョブのデッドロックを防ぐ設定
- [kueuectl stop](https://kueue.sigs.k8s.io/docs/reference/kubectl-kueue/commands/kueuectl_stop/) - ClusterQueue/LocalQueue の一時停止コマンド
- [Kubeflow Trainer を Kueue で動かす](https://kueue.sigs.k8s.io/docs/tasks/run/trainjobs/) / [KubeRay を Kueue で動かす](https://kueue.sigs.k8s.io/docs/tasks/run/rayjobs/)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/bc22f78/infra/eks)
- [kueue-reference](https://github.com/littlemex/distributed-ai/tree/bc22f78/infra/eks/manifests/kueue-reference) - 本章のリファレンス YAML
