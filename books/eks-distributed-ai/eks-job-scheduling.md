---
title: "Advanced03 - Kueue でジョブスケジューリングする"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.3.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.3.0)

本章では、Basic04 で立てた `gpu-ddp` プールに Kueue を足し、3 つの TrainJob を続けて投入して、GPU の枠に収まる分だけが先に動き、残りは Pod を作らずに順番を待つことを確かめます。Kueue を入れる前は、空きが無ければ Pod が `Pending` のまま並び、Karpenter はそのすべてを置けるだけのノードを起動しようとしていました。

Kueue の仕組みや、Volcano・KAI-Scheduler・Slurm との違いは、ブログ「[Kubernetes の分散ジョブスケジューラ比較](https://zenn.dev/tosshi/articles/e47ba6e81153c2)」で解説しています。本章は動かすことを主にし、そのために知っておくべき実装の要点に絞って説明します。

# 解説

## Kueue の要点

Kueue は、ジョブを動かしてよいかを決める層です。ジョブが要求する GPU などの量を、ClusterQueue に書いた枠と比べ、収まるジョブだけを通します。収まらないジョブは suspend したまま止めておくので、Pod が作られず、Karpenter もそのためのノードを起動しません。通したジョブの Pod をどのノードに置くかは、今まで通り kube-scheduler が決め、置けるノードが足りなければ Karpenter がノードを足します。

![Kueue on EKS: the webhook suspends the TrainJob, a reconciler creates a Workload that goes through LocalQueue to ClusterQueue, and on admission Kueue adds the flavor's nodeSelector and clears suspend, after which Trainer, kube-scheduler and Karpenter work as before](/images/books/eks-distributed-ai/job-scheduling-kueue-arch.png)

本章で使うのは次の 3 つのオブジェクトです。図の番号ごとの動きは、ブログで説明しています。

| オブジェクト | 置き場所 | 役割 |
|---|---|---|
| LocalQueue | ジョブと同じ namespace | 利用者がジョブを投入する窓口。ジョブは `kueue.x-k8s.io/queue-name` ラベルで、この名前を指定する |
| ClusterQueue | namespace に属さない、クラスタ全体のオブジェクト | GPU などの枠を持ち、通してよいかを判断する |
| ResourceFlavor | namespace に属さない、クラスタ全体のオブジェクト | 枠の中身がどのノードの資源かを、ノードのラベルで表す |

## 実装の要点

本章の実装は 3 か所に分かれています。Kueue 本体は [`infra/eks/kueue.tf`](https://github.com/littlemex/distributed-ai/blob/release/eks-distributed-ai/v0.3.0/infra/eks/kueue.tf) で入れ、`terraform apply` のたびに入った状態を保ちます。キューの定義は [`infra/eks/manifests/kueue-reference/queue.yaml.tmpl`](https://github.com/littlemex/distributed-ai/blob/release/eks-distributed-ai/v0.3.0/infra/eks/manifests/kueue-reference/queue.yaml.tmpl) を使う側が値を埋めて `kubectl apply` します。TrainJob をキューに乗せるのは、[`infra/eks/charts/experiments`](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.3.0/infra/eks/charts/experiments) の `trainjobTrain.queueName` です。キューの定義を Terraform に入れないのは、どのプールを、どれだけ、どの namespace に見せるかが使う側ごとに違うからで、Basic04 の `accelerator_pools` と同じ考え方です。

### Kueue 本体の設定

`kueue.tf` は、Kueue の Helm chart の設定ファイルを丸ごと渡し、chart の既定値に 2 つだけを足しています。chart 0.20.2 には設定ファイルの一部だけを上書きする仕組みが無いため、既定の内容を写したうえで足しています。

```yaml
waitForPodsReady:
  timeout: 30m
  blockAdmission: true
managedJobsNamespaceSelector:
  matchLabels:
    managed-by-kueue: "true"
```

1 つ目の `waitForPodsReady` は、通したジョブの Pod が全部 Ready になるのを待つ設定です。`blockAdmission: true` にすると、先に通したジョブの Pod が全部 Ready になるまで、次のジョブを通しません。GPU の枠を、実際に確保できる容量より大きくしたとします。2 ノードずつ要るジョブが 2 つ同時に通っても 2 ノードしか確保できないと、2 つのジョブが 1 ノードずつを握り、どちらも残りを待ち続けます。この設定は、その取り合いを防ぎます。代わりに、枠に余裕があっても、次のジョブは前のジョブの Pod が Ready になるまで始まりません。30 分以内に Ready にならなければ、Kueue はそのジョブを取り下げて待ち行列に戻します。この設定は ClusterQueue ごとではなく、Kueue 全体に効きます。

2 つ目の `managedJobsNamespaceSelector` は、Kueue が扱う namespace を、`managed-by-kueue=true` のラベルを付けたものだけに絞ります。chart はこの値を webhook の対象 namespace にも使うので、ラベルの無い namespace では、Job や TrainJob の作成が Kueue の webhook を通りません。chart の既定は `kube-system` と Kueue 自身の namespace 以外のすべてです。webhook の多くは、Kueue が応答しないと作成を拒否する設定なので、Kueue が落ちている間は、Job・JobSet・TrainJob など Kueue が扱う種類の作成がクラスタ全体で失敗します。他の用途と共有するクラスタでは、この既定を避けます。

もう 1 つ、Kueue の CRD はこの chart の中に入っています。そのため Kueue を外すと、クラスタ上のすべてのキューも一緒に消えます。外したあとに suspend 中のジョブが残ると、解除する部品がいないので止まったままになります。外すときの注意は、手順 6 に書きます。

### キューの定義

`queue.yaml.tmpl` は、`__NAMESPACE__`・`__NODEPOOL_NAME__`・`__GPU_QUOTA__` の 3 つを埋めて使います。ResourceFlavor は、Karpenter が自分の起動したノードに付ける `karpenter.sh/nodepool` ラベルで、NodePool のノードを指します。

```yaml
apiVersion: kueue.x-k8s.io/v1beta2
kind: ResourceFlavor
metadata:
  name: __NODEPOOL_NAME__
spec:
  nodeLabels:
    karpenter.sh/nodepool: __NODEPOOL_NAME__
```

ClusterQueue はこの ResourceFlavor の枠を持ち、LocalQueue は namespace の窓口として ClusterQueue を指します。

```yaml
kind: ClusterQueue
metadata:
  name: __NODEPOOL_NAME__-cq
spec:
  namespaceSelector: {}
  resourceGroups:
    - coveredResources: ["cpu", "memory", "nvidia.com/gpu"]
      flavors:
        - name: __NODEPOOL_NAME__
          resources:
            - name: "cpu"
              nominalQuota: "32"
            - name: "memory"
              nominalQuota: "128Gi"
            - name: "nvidia.com/gpu"
              nominalQuota: "__GPU_QUOTA__"
---
kind: LocalQueue
metadata:
  name: __NODEPOOL_NAME__
  namespace: __NAMESPACE__
spec:
  clusterQueue: __NODEPOOL_NAME__-cq
```

Kueue はジョブを通すときに、ResourceFlavor の `nodeLabels` を Pod の nodeSelector として付けます。値が実際の NodePool の名前と違うと、ジョブは通っても Pod はどのノードにも置けません。このリポジトリでは `accelerator_pools` の名前がそのまま NodePool の名前になるので、`gpu-ddp` を入れます。

ClusterQueue の `nvidia.com/gpu` の枠は、この ClusterQueue を使うすべての LocalQueue が同時に通せる GPU の合計です。`namespaceSelector: {}` なので、どの namespace の LocalQueue からも使えます。キューを通らないジョブが同じ NodePool で使う GPU は、この枠に数えられません。Kueue は数を見るだけで、spot の在庫などで本当にノードが立つかは見ないので、枠を NodePool が実際に出せる量より大きくすると、ジョブは通るのに Pod が `Pending` のまま残ります。cpu と memory の枠も GPU と同じくジョブを止めますが、値は `gpu-ddp` のインスタンスタイプに合わせて 32 と 128Gi に固定してあり、`__GPU_QUOTA__` には連動しません。本章の TrainJob は Pod 1 つあたり cpu 4 と memory 16Gi を要求し、2 Pod で cpu 8 と memory 32Gi になるので、GPU の枠を 8 より大きくするなら、cpu と memory の枠も上げます。

このテンプレートには Namespace を入れていません。`kubectl delete -f` で消すときに、namespace ごと共有 PVC まで消してしまわないためです。namespace のラベルは、手順 3 で別のコマンドで付けます。

### TrainJob をキューに乗せる

chart は、`trainjobTrain.queueName` が指定されたときだけ、TrainJob に `queue-name` のラベルを付けます。

```yaml
metadata:
  name: {{ $v.name | default "ddp-trainjob" | quote }}
  namespace: {{ include "experiments.namespace" . }}
  {{- if $v.queueName }}
  labels:
    kueue.x-k8s.io/queue-name: {{ $v.queueName | quote }}
  {{- end }}
```

TrainJob は既定で 2 ノードを使い、本章では各ノードに GPU を 1 枚要求するので、1 つのジョブが要る GPU は 2 枚です。手順 3 で GPU の枠を 2 にするのは、ちょうど 1 ジョブ分にして、残り 2 つが待つ様子を見るためです。`trainjobTrain.name` で TrainJob の名前を、`trainjobTrain.outputSubdir` で学習の途中経過を保存する場所を、ジョブごとに変えます。保存先が同じだと、ddp.py が別のジョブのスナップショットを拾って再開してしまうからです。

Kueue がジョブを扱うには、namespace の `managed-by-kueue=true` と、TrainJob の `queue-name` ラベルの両方が要ります。どちらかが欠けると、TrainJob はキューを通らずにそのまま Pod を作ります。

# ワークショップ実施

はじめにシェルを対象クラスタへ向けます。Basic01 手順 3 と同じ 5 行です。

```bash
cd ~/distributed-ai-v0.3.0
export AWS_PROFILE=default
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
source infra/scripts/distai-env.sh
```

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
ECR_URL=$(terraform -chdir="$(git rev-parse --show-toplevel)/infra/eks" output -raw ddp_sample_ecr_url)
aws ecr describe-images --region "$AWS_REGION" --repository-name "${ECR_URL#*/}" --image-ids imageTag=v1 >/dev/null 2>&1 && echo "OK ddp-sample:v1" || echo "NG ddp-sample:v1"
k get resourceflavor gpu-ddp >/dev/null 2>&1 && echo "NG resourceflavor gpu-ddp が既にあります" || echo "OK resourceflavor gpu-ddp は未使用"
k get clusterqueue gpu-ddp-cq >/dev/null 2>&1 && echo "NG clusterqueue gpu-ddp-cq が既にあります" || echo "OK clusterqueue gpu-ddp-cq は未使用"
k -n distai get localqueue gpu-ddp >/dev/null 2>&1 && echo "NG localqueue gpu-ddp が既にあります" || echo "OK localqueue gpu-ddp は未使用"
[ -z "$(k get namespace distai -o jsonpath='{.metadata.labels.managed-by-kueue}')" ] && echo "OK distai にラベルなし" || echo "NG distai に managed-by-kueue が既にあります"
```

`NG` が 1 つでも出たら、先へ進まないでください。後ろの 4 行は、本章が作るキューと namespace のラベルが、他の用途で使われていないかを確かめます。本章の手順 3 は同じ名前のものを上書きし、手順 6 はそれを削除するからです。Kueue がまだ入っていないクラスタでは、キューの 3 行は `OK` になります。

## 2. Kueue を入れる

`infra/eks` で apply すると、`kueue.tf` が Kueue 本体のコントローラと CRD を `kueue-system` namespace に入れます。既に入っていれば何も変わりません。下のコマンドは `terraform plan` の結果を保存し、変わるリソースが Kueue の追加か更新だけのときに限って、その計画を apply します。Kueue 以外のリソースが変わる計画や、Kueue を削除する計画なら、`NG` を表示して apply しません。`NG` が出たら、手順 3 以降に進まないでください。他の章で `-var` や `-var-file` を付けて apply してきたなら、その差分が出ているので、`terraform plan` に同じ引数を付けてやり直します。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
rm -f kueue.tfplan kueue.tfplan.txt
terraform plan -no-color -out=kueue.tfplan | tail -1
terraform show -no-color kueue.tfplan | grep -E '^  # .* (will be (created|updated|destroyed|replaced)|must be replaced)' | tee kueue.tfplan.txt
if [ -f kueue.tfplan ] && ! grep -qEv 'kueue.* will be (created|updated in-place)$' kueue.tfplan.txt; then terraform apply kueue.tfplan; else echo "NG Kueue の追加・更新以外の変更があるか plan に失敗したので apply しません"; fi
rm -f kueue.tfplan kueue.tfplan.txt
k -n kueue-system rollout status deploy/kueue-controller-manager --timeout=5m
k get pods -n kueue-system
```

実機出力 (最後の `k get pods`):

```text
NAME                                        READY   STATUS    RESTARTS   AGE
kueue-controller-manager-544dcdb599-q4xsg   1/1     Running   0          15s
```

まだどの namespace にも `managed-by-kueue=true` を付けていないので、Kueue はどのジョブも扱わず、クラスタ上の Job や TrainJob は今まで通りに動きます。

## 3. キューを定義する

`distai` に `managed-by-kueue=true` を付けて Kueue の対象にし、`queue.yaml.tmpl` に値を埋めて適用します。本章では namespace を `distai`、プールを `gpu-ddp`、GPU の枠を 1 ジョブ分の 2 にします。ClusterQueue は `gpu-ddp-cq`、LocalQueue と ResourceFlavor は `gpu-ddp` という名前になります。

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

## 4. 3 つの TrainJob を続けて投入する

Basic02 で push した `ddp-sample:v1` と、Basic04 で入れた `torch-distributed-eks` ランタイムを使います。`helm template` はランタイムも出力しますが、`k apply -l` でキューのラベルが付いた TrainJob だけを適用し、他の章と共有しているランタイムは書き換えません。

最初の `helm dependency build` は、chart が使う部品の chart を `charts/` に取り込みます。Basic02 で一度実行していれば、何も変わりません。

```bash
helm dependency build "$(git rev-parse --show-toplevel)"/infra/eks/charts/experiments
IMAGE=$(terraform -chdir="$(git rev-parse --show-toplevel)/infra/eks" output -raw ddp_sample_ecr_url):v1

for n in a b c; do
  helm template exp "$(git rev-parse --show-toplevel)"/infra/eks/charts/experiments \
    --set namespace=distai \
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
  | k apply -l kueue.x-k8s.io/queue-name=gpu-ddp -f -
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

3 つとも `Suspended` と出ています。TrainJob の `STATE` 列は、conditions 配列の末尾にある condition の種類を出すだけで、その condition が True か False かは見ていません。`a` は suspend が外れて Suspended の condition が False に変わっていますが、種類は Suspended のままなので、こう表示されます。どれが通ったかは、Workload の `ADMITTED` 列で見ます。

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

`a` だけが `ADMITTED: True` で、GPU の枠 2 を使い切っています。使用量は `k get clusterqueue gpu-ddp-cq -o jsonpath='{.status.flavorsUsage}'` で確かめられます。`b` と `c` はどちらも `RESERVED IN` が空で、まだ枠を割り当てられずに待っています。`ADMITTED` 列が `False` か空かは、Admitted の condition がもう付いたかどうかの違いで、順番の前後を表すものではありません。Pod があるのも `a` だけで、`b` と `c` には Pod がありません。

```bash
for n in a b c; do echo "=== ddp-trainjob-$n ==="; k -n distai get pods -l jobset.sigs.k8s.io/jobset-name=ddp-trainjob-$n; done
```

そのため、Karpenter が起動するのは `a` の分のノードだけです。次の実機出力は、`gpu-ddp` に空きノードも他の利用者もいない状態で取ったものです。

```bash
k get nodeclaims -l karpenter.sh/nodepool=gpu-ddp
```

実機出力:

```text
NAME            TYPE         CAPACITY   ZONE              NODE                                             READY   AGE
gpu-ddp-5qcqh   g6.2xlarge   spot       us-east-2a        ip-10-0-14-104.us-east-2.compute.internal        True    95s
gpu-ddp-gm2c8   g6.2xlarge   spot       us-east-2a        ip-10-0-24-166.us-east-2.compute.internal        True    95s
```

Kueue を入れていなければ、`b`・`c` の Pod も作られ、3 つで合わせて 6 枚の GPU を要求します。`gpu-ddp` プールの型はどれも GPU を 1 枚だけ積むので、既存の空きが無く在庫があれば、Karpenter は最大 6 ノードの確保を同時に試みます。

::::details NodeClaim が出てこないとき

`a` が `ADMITTED: True` なのに `k get nodeclaims` が `No resources found` のままなら、Karpenter がノードを起動できていません。Pod の Events と Karpenter のログで原因を確かめます。

```bash
k -n distai describe pod -l jobset.sigs.k8s.io/jobset-name=ddp-trainjob-a | grep -A3 -E 'FailedScheduling|Nominated'
k -n karpenter logs deploy/karpenter --since=15m | grep -E 'UnfulfillableCapacity|InsufficientInstanceCapacity' | tail -3
```

`UnfulfillableCapacity` や `InsufficientInstanceCapacity` が出ていれば、指定したゾーンにその型の在庫がありません。本章の TrainJob の Pod はメモリを 16 GiB 要求するので、`gpu-ddp` プールの型のうち実際に使えるのは `g6.2xlarge` と `g5.2xlarge` だけです。メモリが 16 GiB の `g6.xlarge` と `g5.xlarge` には、ノード側の取り分があるため載りません。Karpenter は在庫の無かった型をしばらく候補から外し、数分おきに起動をやり直すので、在庫が戻れば NodeClaim が現れます。Kueue から見るとジョブは通したままなので、`b`・`c` は待ち続けます。`waitForPodsReady` の 30 分が過ぎても `a` の Pod が Ready にならなければ、Kueue は `a` を取り下げて待ち行列に戻します。

::::

## 5. 順番に通っていく様子を見る

この実行例では、`a` が完走して GPU の枠が空くと、次に `b` が通りました。Karpenter が空いたノードを回収する前に次のジョブが通ったので、同じノードがそのまま使われました。各ジョブは 3 epoch で終わるので、確かめるタイミングによっては `b` も完走しています。

```bash
k -n distai get trainjob ddp-trainjob-a ddp-trainjob-b ddp-trainjob-c
```

実機出力 (`a`・`b` が完走した時点):

```text
NAME             STATE       AGE
ddp-trainjob-a   Complete    4m11s
ddp-trainjob-b   Complete    4m5s
ddp-trainjob-c   Suspended   3m59s
```

ノードが確保でき学習が正常に進めば、最終的に `c` も通って完走し、3 つとも `Complete` になります。最初の `k wait` は、3 つが完走するまで待ちます。

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

実機出力(`a` の分のみ抜粋。`b`・`c` も同じ流れで、保存先がそれぞれ `trainjob-gpu-ddp-b/`・`trainjob-gpu-ddp-c/` になります):

```text
=== ddp-trainjob-a ===
[W1023 07:56:04.547230108 socket.cpp:752] [c10d] The IPv6 network addresses of (ddp-trainjob-a-node-0-0.ddp-trainjob-a, 29500) cannot be retrieved (gai error: -2 - Name or service not known).
[rank 0/2] backend=nccl cuda_available=True device_count=1
[rank 0/2] downloading MNIST to /shared/mnist-data
[rank 0/2] starting training: 3 epochs, batch_size 32
[rank 0/2] mlflow disabled
[rank 0/2] epoch 0 | steps 938 | loss 0.1979
[rank 0/2] epoch 0 | snapshot saved to /shared/output/trainjob-gpu-ddp-a/snapshot.pt
[rank 0/2] epoch 1 | steps 938 | loss 0.0867
[rank 0/2] epoch 1 | snapshot saved to /shared/output/trainjob-gpu-ddp-a/snapshot.pt
[rank 0/2] epoch 2 | steps 938 | loss 0.0631
[rank 0/2] epoch 2 | snapshot saved to /shared/output/trainjob-gpu-ddp-a/snapshot.pt
[rank 0/2] done
```

先頭の `[W ...] socket.cpp` の行は、PyTorch が rank 0 の名前から IPv6 のアドレスを引こうとして見つからなかったという警告です。このクラスタは IPv4 だけで動いているので IPv6 のアドレスは無く、PyTorch は IPv4 でつなぎます。学習は最後の `done` まで進んでいるので、この行は無視して構いません。出ないこともあります。

3 本ともそれぞれ `trainjob-gpu-ddp-$n/` に書いており、互いのスナップショットを拾っていないことが確認できます。

::::details キューを通らずに動いてしまったとき

`queue-name` ラベルの無い TrainJob は、Kueue を通らずに直接 Pod を作り、GPU の枠に関係なく Karpenter がノードを確保します。本章の手順 4 は `k apply -l` でラベル付きの TrainJob だけを適用するので、`trainjobTrain.queueName` を省くと TrainJob 自体が作られません。キューに入れたつもりで全部がすぐに動き出したら、`k -n distai get trainjob ddp-trainjob-a -o jsonpath='{.metadata.labels}'` に `kueue.x-k8s.io/queue-name` があるかを確かめてください。

もう 1 つの原因は、namespace のラベルの付け忘れです。`distai` に `managed-by-kueue=true` が無いと、TrainJob に `queue-name` ラベルがあっても Kueue は扱わず、`k get workloads -n distai` に何も出ません。`k get namespace distai --show-labels` で確かめてください。

::::

## 6. 後片付け

本章で作った ClusterQueue・ResourceFlavor・LocalQueue と 3 つの TrainJob を削除し、`distai` のラベルを外します。手順 1 で、どれも本章より前には使われていなかったことを確かめてあります。

```bash
k -n distai delete trainjob ddp-trainjob-a ddp-trainjob-b ddp-trainjob-c --ignore-not-found
k -n distai delete localqueue gpu-ddp --ignore-not-found
k delete clusterqueue gpu-ddp-cq --ignore-not-found
k delete resourceflavor gpu-ddp --ignore-not-found
k label namespace distai managed-by-kueue-
until [ -z "$(k get nodeclaims -l karpenter.sh/nodepool=gpu-ddp -o name)" ]; do sleep 15; done; echo "OK gpu-ddp の NodeClaim なし"
```

`managed-by-kueue-` は、手順 3 で付けたラベルを外します。外し忘れると、他の章で `distai` に作る Job や TrainJob も Kueue の webhook を通ります。最後の行は、`gpu-ddp` の NodeClaim が無くなるまで待ちます。Pod が無くなると Karpenter が空いたノードを回収し、Basic04 と同じく数分かかります。`gpu-ddp` を他の用途でも使っている場合は、この行は終わらないので Ctrl-C で抜けてください。

TrainJob を消しても、共有 PVC 上の `/shared/output/trainjob-gpu-ddp-a/` などのスナップショットは残ります。本章をもう一度実行すると、ddp.py がそれを拾って学習を再開するので、やり直すときは手順 4 の `outputSubdir` を別の名前に変えてください。

:::message alert
Kueue 本体は消さずに残しておきます。`terraform apply -var kueue_enabled=false` や `helm uninstall` で外すと、CRD と一緒に、クラスタ上のすべての namespace の ClusterQueue・LocalQueue・ResourceFlavor・Workload が消えます。外す前に、キューを通るジョブを削除しておいてください。
:::

# まとめ

Kueue 本体は `kueue.tf` で常に入れておき、namespace のラベルで対象を絞りました。キューの定義はテンプレートに値を埋めて使い、TrainJob には chart の `queueName` でラベルを付けます。3 つの TrainJob を続けて投入すると、GPU の枠に収まる 1 つだけが通り、残りは Pod を作らずに待ち、枠が空くたびに 1 つずつ通ることを実機で確かめました。

# 今後の改善

| なぜ改善すべきか | 改善対象 | 改善案 |
|---|---|---|
| 本章は 1 つの ClusterQueue が 1 つの NodePool を見る最小構成で、複数チームが同じプールを分け合う場面は試していない | マルチテナントの公平性 | Kueue の [cohort](https://kueue.sigs.k8s.io/docs/concepts/cohort/) で複数の ClusterQueue の枠を共有し、fair sharing で公平に配分できるか確かめる |
| KAI-Scheduler は枠で待たせたジョブでも Pod を作るので、Karpenter がそのためのノードを立てることがあると、KAI-Scheduler の[設計提案](https://github.com/kai-scheduler/KAI-Scheduler/blob/96eb3764caad/docs/developer/designs/queue-admission-gating/README.md)に書かれている。配置まで受け持つスケジューラとの組み合わせは、本書ではまだ試していない | KAI-Scheduler と Karpenter の組み合わせ | KAI-Scheduler を Karpenter と組む構成を Advanced の章として追加し、Kueue を前に置いて Pod の作成前に止める構成で、余計なノードを防げるか確かめる。製品ごとの違いは[ブログ](https://zenn.dev/tosshi/articles/e47ba6e81153c2)で整理している |
| キューの定義は `queue.yaml.tmpl` の `__NAMESPACE__` などを sed で置き換える形で、他の章の主流である `charts/experiments` に `--set` で値を渡す形とそろっていない | キューの定義の渡し方 | キューの定義を `charts/experiments` の新しい部分にし、手順 3 も `helm template \| k apply` で namespace・プール・GPU の枠を渡す形にする。値の書き間違いを chart の検証で止められる |
| GPU の枠を人が `__GPU_QUOTA__` に書いており、Capacity Block のように時間を区切って使う容量には追従しない | 時間を区切った容量への追従 | Kueue v0.20.0 で alpha として入った [DynamicQuotaOrchestration](https://github.com/kubernetes-sigs/kueue/releases/tag/v0.20.0) で、枠を実際の容量に追従させられるかを試す |

# 参考資料

- [Kubernetes の分散ジョブスケジューラ比較](https://zenn.dev/tosshi/articles/e47ba6e81153c2) - Kueue の仕組みと、Volcano・KAI-Scheduler・Slurm との違い
- [Kueue](https://kueue.sigs.k8s.io/) - 公式ドキュメント
- [Opt-in Namespace Management](https://kueue.sigs.k8s.io/docs/tasks/manage/enforce_job_management/opt_in_namespace_management/) - webhook のスコープをラベルで絞る公式の手順
- [Wait for pods ready](https://kueue.sigs.k8s.io/docs/tasks/manage/setup_wait_for_pods_ready/) - 複数ノードジョブのデッドロックを防ぐ設定
- [kueuectl stop](https://kueue.sigs.k8s.io/docs/reference/kubectl-kueue/commands/kueuectl_stop/) - ClusterQueue/LocalQueue の一時停止コマンド
- [Kubeflow Trainer を Kueue で動かす](https://kueue.sigs.k8s.io/docs/tasks/run/trainjobs/) / [KubeRay を Kueue で動かす](https://kueue.sigs.k8s.io/docs/tasks/run/rayjobs/)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.3.0/infra/eks)
- [kueue-reference](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.3.0/infra/eks/manifests/kueue-reference) - 本章のリファレンス YAML
