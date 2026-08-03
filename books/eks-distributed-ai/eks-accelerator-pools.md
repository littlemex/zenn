---
title: "Basic04 - GPU で分散学習を体験する"
free: true
---

本章では、Basic02 で CPU だけだった分散学習(DDP)を GPU に載せ替えます。`accelerator_pools` の新しい capacity-mix スキーマを使い、g5 と g6 を混ぜた spot+on-demand フォールバック構成で 2 ノード GPU DDP を実行します。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち **Karpenter が起動するアクセラレータノードのプール定義**です。Basic03 で入れた Karpenter が実際に GPU ノードを立てられるようにするのがゴールで、EFA を使う大規模構成は Basic06 で扱い、そのために必要な容量の確保は Basic05 で扱います。

## このシナリオが解決する課題

分散学習の実機検証では「とにかく GPU ノードを 2 台確保して DDP を回したい」という要求が頻繁に発生します。しかし単一のインスタンスタイプに絞ると、そのサイズがキャパシティ不足のとき InsufficientInstanceCapacity で詰まります。

本章のアプローチは次の通りです。

- g5(A10G)と g6(L4)を**ヘテロジニアスに混ぜます**: 片方が取れなくても他方でノードが立ちます
- spot を第一優先、on-demand をフォールバックにします: コストを抑えつつ確実に 2 台確保します(Intent F)
- 単一 AZ に固定します: gloo/TCP バックエンドの DDP でも cross-AZ レイテンシを避けます

単一 AZ への固定は、本章の目的である「確実に 2 台確保する」こととは緊張関係にあります。AZ を 1 つに絞るとその AZ の spot 在庫が尽きていれば取得できず、複数 AZ に広げれば可能性は上がります。ここでは 4 インスタンスタイプ × 2 capacity-type の組み合わせで確保しやすさを確保しつつ、AZ は 1 つに固定するトレードオフを選んでいます。これは EFA や FSx など AZ をまたげないコンポーネントとの将来的な整合を優先したためです。

:::message
本章の accelerator_pools capacity-mix 実装は、基本的なユースケースをカバーする初期バージョンです。今後、実運用に基づき EFA 対応の大規模訓練シナリオや Capacity Block との連携パターンを追加していきます。
:::

## accelerator_pools の設定

### tfvars に 1 エントリ追加するだけ

```hcl
accelerator_pools = {
  gpu-ddp = {
    instance_types  = ["g6.2xlarge", "g5.2xlarge", "g6.xlarge", "g5.xlarge"]
    device_plugin   = "nvidia"
    capacity_types  = ["spot", "on-demand"]
    efa_interface_count = 0
    labels          = { workload = "ddp-basic04" }
  }
}
```

この 1 エントリから、Karpenter の NodePool と EC2NodeClass が自動生成されます。

### 設定の意味

| フィールド | 値 | 効果 |
|---|---|---|
| instance_types | g6.2xlarge, g5.2xlarge, g6.xlarge, g5.xlarge | 4 種のうちキャパシティがあるものを Karpenter が選択 |
| capacity_types | ["spot", "on-demand"] | spot 優先、取れなければ on-demand にフォールバック |
| zones | 未指定(`local.azs[0]` に自動導出) | 将来 Basic05 で追加する Capacity Block プールと同じ AZ に配置し、共有ストレージ接続に備える |
| efa_interface_count | 0 | 小型 GPU に EFA なし(TCP gloo) |
| labels | workload=ddp-basic04 | 追加のラベル。Pod からプールを選ぶ第一級の手段は、プール名(map のキー)がそのまま Karpenter のノードラベル `node-role=<プール名>` になる仕組みで、`labels` はさらに細かくルーティングしたいときの補助です |

### プール確保ロジックの全体像

設定がどう処理されるかを図にまとめました。

![accelerator_pools ロジックフロー](/images/books/eks-distributed-ai/accelerator-pools-logic.png)

入力(tfvars)から NodePool/EC2NodeClass のレンダリングまでは、正規化(pool_effective)と EFA 導出(EC2 API)が互いに独立して並行評価され、その両者を disruption preset が参照し、最後にすべてが Kubernetes リソース生成に流れ込むという依存関係で処理されます。

### インタラクティブシミュレータ

設定を変えるとアロケーション結果がどう変わるかを、以下のシミュレータで試せます。

@[codepen](https://codepen.io/larcpwpp-the-styleful/pen/019fbdc6-b9c2-7eb1-809e-3c95429b1d9e?default-tab=result)

インスタンスタイプ、capacity_types、zones を変えると、NodePool の requirement values、EFA topology、disruption 設定がリアルタイムで更新されます。このシミュレータは挙動のイメージをつかむための別実装であり、正は常に Terraform の実装です。両者がドリフトする可能性がある点にご注意ください。

# ワークショップ実施

## 1. 前提を確認する

- Karpenter が導入済み
- Basic02 で作った `ddp-sample` イメージ(ECR に push 済み)

NVIDIA GPU Operator は Basic03 の時点では入っていません。`accelerator_pools` に `device_plugin = "nvidia"` のプールが 1 つ以上あることを条件(`local.has_gpu_pool`)に導入されるため、本章で `gpu-ddp` プールを足して `terraform apply` した時点で初めてインストールされます。したがって上記 2 つ目の apply は、プールの NodePool/EC2NodeClass と GPU Operator を同時に作ることになり、初回はその分だけ時間がかかります。

```hcl
accelerator_pools = {
  gpu-ddp = {
    instance_types  = ["g6.2xlarge", "g5.2xlarge", "g6.xlarge", "g5.xlarge"]
    device_plugin   = "nvidia"
    capacity_types  = ["spot", "on-demand"]
    efa_interface_count = 0
    labels          = { workload = "ddp-basic04" }
  }
}
```

```bash
terraform apply
```

## 2. TrainJob で 2 ノード DDP を投入する

Kubeflow Trainer v2 の TrainJob には `nodeSelector` や `tolerations` を直接書くフィールドがありません。Pod スペックの土台は Basic02 で確認した `ClusterTrainingRuntime`(`torch-distributed-eks`)が持っており、そこに焼き込まれた `nodeSelector: { node-role: <値> }` がどのプールに載せるかを決めます。したがって GPU プールへ切り替えるには、TrainJob の中身を書き換えるのではなく、Helm の `trainjobTrain.nodeRole` を `gpu-ddp` に切り替えて `torch-distributed-eks` を再レンダリングします。

Basic02 と同じ `ddp.py`(CUDA が見えれば nccl backend を自動選択するコード)を、同じ `ddp-sample` イメージ(CUDA ベース)で動かすので、学習コード・イメージのどちらも変更不要です。変わるのは Helm に渡す値(`nodeRole`、`gpu.enabled`、`gpu.count`)だけです。

```bash
# 既存の TrainJob が残っていると apply がスキップされるため、作り直す前に削除する
kubectl delete trainjob ddp-trainjob -n "$NAMESPACE" --ignore-not-found

# Basic02 で push した ddp-sample イメージ(ECR_URL は Basic02 と同じ導出)
ECR_URL=$(terraform output -raw ddp_sample_ecr_url)
IMAGE=${ECR_URL}:v1

helm template exp charts/experiments -n "$NAMESPACE" \
    --set trainjobTrain.enabled=true \
    --set trainjobTrain.image="$IMAGE" \
    --set trainjobTrain.nodeRole=gpu-ddp \
    --set trainjobTrain.numNodes=2 \
    --set trainjobTrain.nprocPerNode=1 \
    --set trainjobTrain.gpu.enabled=true \
    --set trainjobTrain.gpu.count=1 \
    --set torchrunTrain.totalEpochs=100 \
    --set sharedStorage.existingClaimName=shared-claim \
    | kubectl apply -f -
```

`trainjobTrain.nodeRole=gpu-ddp` が `torch-distributed-eks` の `nodeSelector.node-role` を `gpu-ddp` に、`trainjobTrain.gpu.enabled=true` が `resourcesPerNode.limits.nvidia.com/gpu` と `nvidia.com/gpu` taint への toleration を有効にします。TrainJob 自体の名前は常に `ddp-trainjob` です。

:::message alert
`ClusterTrainingRuntime` はクラスタスコープの単一オブジェクトです。このコマンドを実行すると、Basic02 で作った `nodeRole=cpu` の `torch-distributed-eks` が `nodeRole=gpu-ddp` に上書きされます。クラスタを他の用途と共有している場合は、同名のランタイムを奪い合う点に注意してください。
:::

## 3. 実行と確認

TrainJob が展開する Pod は JobSet の規則で名付けられるため、Pod 名を決め打ちせずラベルで選びます。

```bash
kubectl get pods -n "$NAMESPACE" -o wide -l jobset.sigs.k8s.io/jobset-name=ddp-trainjob
kubectl get trainjob ddp-trainjob -n "$NAMESPACE" -w
```

Karpenter が 2 台の g5/g6 ノードを起動し、それぞれに 1 GPU ずつ割り当てて DDP が走ります。確保の様子は NodeClaim で見えます。

```bash
kubectl get nodeclaims -l karpenter.sh/nodepool=gpu-ddp
```

実機出力:

```text
NAME            TYPE         CAPACITY    ZONE         NODE                                        READY
gpu-ddp-9clls   g6.2xlarge   spot        us-west-2a   ip-10-0-0-254.us-west-2.compute.internal    True
gpu-ddp-qhczq   g6.2xlarge   on-demand   us-west-2a   ip-10-0-27-109.us-west-2.compute.internal   True
```

1 台目は spot で取れましたが、2 台目は同じ条件の spot 在庫が無く on-demand にフォールバックして台数を充足しています。`CAPACITY` 列が 2 台で異なるのは失敗ではなく、単一の capacity type に固定していたら 2 台目が取れずに `Pending` のままだった状況を、フォールバックが救っている状態です。ここは状況によって spot x2 で取れるケースもあります。

rank 0(completion index 0)のログを追います。

```bash
SEL="jobset.sigs.k8s.io/jobset-name=ddp-trainjob,batch.kubernetes.io/job-completion-index=0"
kubectl wait --for=condition=ready pod -l "$SEL" -n "$NAMESPACE" --timeout=15m
kubectl logs -f -l "$SEL" -n "$NAMESPACE"
```

`ddp.py` は CUDA が見えるとログに `backend=nccl cuda_available=True device_count=1` と出し、各 rank が `done` で終われば成功です。実機出力は次のようになります。

```text
[rank 0/2] backend=nccl cuda_available=True device_count=1
[rank 0/2] downloading MNIST to /shared/mnist-data
[rank 0/2] resuming from snapshot at epoch 2
[rank 0/2] starting training: 3 epochs, batch_size 32
[rank 0/2] epoch 2 | steps 938 | loss 0.0523
[rank 0/2] epoch 2 | snapshot saved to /shared/output/trainjob/snapshot.pt
[rank 0/2] done
```

:::message alert
本章の構成は spot 前提の 2 ノード DDP です。spot ノードが中断されると、その rank のプロセスが失われて collective 全体(all-reduce)が止まり、学習は失敗します。`torch-distributed-eks` は Pod に `karpenter.sh/do-not-disrupt: "true"` を付けていますが、これは Karpenter 自身による自発的な退去(consolidation など)を止めるだけで、AWS 側のスポット中断そのものは防げません。
:::

# Intent F と Intent M の違い

今回作成したプール確保の方式の二つの構成について解説します。

- **Intent F**: 1 つのプールに複数の capacity_types を並べます。Karpenter は reserved を最優先し、それ以外は価格の低いものから選ぶため、結果として spot が on-demand より先に選ばれ、取れなければ次にフォールバックして台数充足を目指します。全ノードが同じプール・同じ NodePool に属します。
- **Intent M**: reserved+spot を 1 プールに入れ、reserved ノードで長期訓練を走らせつつ spot ノードで推論やデータ前処理を同時に動かします。訓練 rank は nodeSelector `karpenter.sh/capacity-type: reserved` で reserved にピン留めし、spot に置きたい推論等は同じキーで `spot` にピン留めします。どちらの capacity-type に留まりたいかを Pod 側が明示的に指定する仕組みです。

両方とも `capacity_types` リストで表現しますが、使い方が異なります。詳しくは Basic05(Capacity Block)で扱います。

# まとめ

`accelerator_pools` に 1 エントリ書くだけで、ヘテロジニアスな GPU DDP 環境が立ち上がりました。

# 参考資料

- [Karpenter NodePools](https://karpenter.sh/docs/concepts/nodepools/)
- [Amazon EC2 スポットインスタンス](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html)
- [Kubeflow Trainer](https://www.kubeflow.org/docs/components/trainer/)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
- [実験ワークロード chart（charts/experiments）](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments)
