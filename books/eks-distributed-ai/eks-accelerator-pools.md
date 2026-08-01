---
title: "Basic04 - GPU DDP をヘテロジニアスなプールで回す"
free: true
---

本章では、Basic02 で CPU だけだった分散学習(DDP)を GPU に載せ替えます。`accelerator_pools` の新しい capacity-mix スキーマを使い、g5 と g6 を混ぜた spot+on-demand フォールバック構成で 2 ノード GPU DDP を実行します。

# はじめに

## このシナリオが解決する課題

分散学習の実機検証では「とにかく GPU ノードを 2 台確保して DDP を回したい」という要求が頻繁に発生します。しかし単一のインスタンスタイプに絞ると、そのサイズがキャパシティ不足のとき InsufficientInstanceCapacity で詰まります。

本章のアプローチは次の通りです。

- g5(A10G)と g6(L4)を**ヘテロジニアスに混ぜます**: 片方が取れなくても他方でノードが立ちます
- spot を第一優先、on-demand をフォールバックにします: コストを抑えつつ確実に 2 台確保します(Intent F)
- 単一 AZ に固定します: gloo/TCP バックエンドの DDP でも cross-AZ レイテンシを避けます

単一 AZ への固定は、本章の目的である「確実に 2 台確保する」こととは緊張関係にあります。AZ を 1 つに絞るとその AZ の spot 在庫が尽きていれば取得できず、複数 AZ に広げれば可能性は上がります。ここでは 4 インスタンスタイプ × 2 capacity-type の組み合わせで確保しやすさを確保しつつ、AZ は 1 つに固定するトレードオフを選んでいます。これは EFA や FSx など AZ をまたげないコンポーネントとの将来的な整合を優先したためです。

:::message
本章の accelerator_pools capacity-mix 実装は、基本的なユースケースをカバーする初期バージョンです。今後、実運用のフィードバックに基づき EFA 対応の大規模訓練シナリオや Capacity Block との連携パターンを追加していきます。
:::

# accelerator_pools の設定

## tfvars に 1 エントリ追加するだけ

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

`zones` は書いていません。Basic01 の `terraform.tfvars` で設定した `region` の最初の AZ(`local.azs[0]`)に自動導出されるため、on-demand/spot プールでは基本的に手書き不要です。特定の AZ に固定したい場合だけ、その region で解決済みの AZ(例: `region = "us-east-2"` なら `zones = ["us-east-2a"]`)を明示してください。

この 1 エントリから、Karpenter の NodePool と EC2NodeClass が自動生成されます。

## 設定の意味

| フィールド | 値 | 効果 |
|---|---|---|
| instance_types | g6.2xlarge, g5.2xlarge, g6.xlarge, g5.xlarge | 4 種のうちキャパシティがあるものを Karpenter が選択 |
| capacity_types | ["spot", "on-demand"] | spot 優先、取れなければ on-demand にフォールバック(Intent F) |
| zones | 未指定(`local.azs[0]` に自動導出) | 将来 Basic06 で追加する Capacity Block プールと同じ AZ に配置し、共有ストレージ接続に備える |
| efa_interface_count | 0 | 小型 GPU に EFA は不要(TCP gloo で十分) |
| labels | workload=ddp-basic04 | 追加のラベル。Pod からプールを選ぶ第一級の手段は、プール名(map のキー)がそのまま Karpenter のノードラベル `node-role=<プール名>` になる仕組みで、`labels` はさらに細かくルーティングしたいときの補助です |

## プール確保ロジックの全体像

設定がどう処理されるかを図にまとめました。

![accelerator_pools ロジックフロー](/images/books/eks-distributed-ai/accelerator-pools-logic.png)

入力(tfvars)から NodePool/EC2NodeClass のレンダリングまでは、正規化(pool_effective)と EFA 導出(EC2 API)が互いに独立して並行評価され、その両者を disruption preset が参照し、最後にすべてが Kubernetes リソース生成に流れ込むという依存関係で処理されます。

## インタラクティブシミュレータ

設定を変えるとアロケーション結果がどう変わるかを、以下のシミュレータで試せます。

@[codepen](https://codepen.io/larcpwpp-the-styleful/pen/019fbdc6-b9c2-7eb1-809e-3c95429b1d9e?default-tab=result)

インスタンスタイプ、capacity_types、zones を変えると、NodePool の requirement values、EFA topology、disruption 設定がリアルタイムで更新されます。このシミュレータは挙動のイメージをつかむための別実装であり、正は常に Terraform の実装です。両者がドリフトする可能性がある点にご注意ください。

# GPU DDP を実行する

## 前提

- Basic03 で Karpenter + GPU Operator が導入済み
- `terraform apply` で gpu-ddp プールが作成済み(NodePool + EC2NodeClass)
- Basic02 で作った `ddp-sample` イメージ(ECR に push 済み)

## TrainJob で 2 ノード DDP

Kubeflow Trainer v2 の TrainJob(`trainer.kubeflow.org/v1alpha1`)には `nodeSelector` や `tolerations` を直接書くフィールドがありません。Pod スペックの土台は Basic02 で見た `ClusterTrainingRuntime`(`torch-distributed-eks`)が持っており、そこに焼き込まれた `nodeSelector: { node-role: <値> }` がどのプールに載せるかを決めます。したがって GPU プールへ切り替えるには、TrainJob の中身を書き換えるのではなく、Helm の `trainjobTrain.nodeRole` を `gpu-ddp` に切り替えて `torch-distributed-eks` を再レンダリングします。

Basic02 と同じ `ddp.py`(CUDA が見えれば nccl backend を自動選択するコード)を、同じ `ddp-sample` イメージ(CUDA ベース)で動かすので、学習コード・イメージのどちらも変更不要です。変わるのは Helm に渡す値(`nodeRole`、`gpu.enabled`、`gpu.count`)だけです。

```bash
# 既存の TrainJob が残っていると apply がスキップされるため、作り直す前に削除する
kubectl delete trainjob ddp-trainjob -n "$NAMESPACE" --ignore-not-found

# Basic02 で push した ddp-sample イメージ(ECR_URL は Basic02 と同じ導出)
ECR_URL=$(terraform output -raw ddp_sample_ecr_url)
IMAGE=${ECR_URL}:v1

helm template exp charts/experiments -n "$NAMESPACE" \
    --set sharedStorage.existingClaimName=openzfs-claim \
    --set trainjobTrain.enabled=true \
    --set trainjobTrain.image="$IMAGE" \
    --set trainjobTrain.nodeRole=gpu-ddp \
    --set trainjobTrain.numNodes=2 \
    --set trainjobTrain.nprocPerNode=1 \
    --set trainjobTrain.gpu.enabled=true \
    --set trainjobTrain.gpu.count=1 \
    | kubectl apply -f -
```

`trainjobTrain.nodeRole=gpu-ddp` が `torch-distributed-eks` の `nodeSelector.node-role` を `gpu-ddp` に、`trainjobTrain.gpu.enabled=true` が `resourcesPerNode.limits.nvidia.com/gpu` と `nvidia.com/gpu` taint への toleration を有効にします。TrainJob 自体の名前は常に `ddp-trainjob` です。

:::message alert
`ClusterTrainingRuntime` はクラスタスコープの単一オブジェクトです。このコマンドを実行すると、Basic02 で作った `nodeRole=cpu` の `torch-distributed-eks` が `nodeRole=gpu-ddp` に上書きされます。クラスタを他の用途と共有している場合は、同名のランタイムを奪い合う点に注意してください。
:::

## 実行と確認

TrainJob が展開する Pod は JobSet の規則で名付けられるため、Pod 名を決め打ちせずラベルで選びます。

```bash
kubectl get pods -n "$NAMESPACE" -o wide -l jobset.sigs.k8s.io/jobset-name=ddp-trainjob
kubectl get trainjob ddp-trainjob -n "$NAMESPACE" -w
```

Karpenter が spot で 2 台の g5/g6 ノードを起動し、それぞれに 1 GPU ずつ割り当てて DDP が走ります。spot が取れない場合は on-demand にフォールバックします。rank 0(completion index 0)のログを追います。

```bash
SEL="jobset.sigs.k8s.io/jobset-name=ddp-trainjob,batch.kubernetes.io/job-completion-index=0"
kubectl wait --for=condition=ready pod -l "$SEL" -n "$NAMESPACE" --timeout=15m
kubectl logs -f -l "$SEL" -n "$NAMESPACE"
```

`ddp.py` は CUDA が見えるとログに `backend=nccl cuda_available=True device_count=1` と出し、各 rank が `done` で終われば成功です。

:::message alert
本章の構成は spot 前提の 2 ノード DDP です。spot ノードが中断されると、その rank のプロセスが失われて collective 全体(all-reduce)が止まり、学習は失敗します。`torch-distributed-eks` は Pod に `karpenter.sh/do-not-disrupt: "true"` を付けていますが、これは Karpenter 自身による自発的な退去(consolidation など)を止めるだけで、AWS 側のスポット中断そのものは防げません。短時間の検証用途に留め、長時間の学習では `ddp.py` のチェックポイント間隔を詰めるか、Basic06 で扱う Capacity Block(reserved)の利用を検討してください。
:::

# Intent F と Intent M の違い

本章の構成は **Intent F(フォールバック)** です。

- **Intent F**: 1 つのプールに複数の capacity_types を並べます。Karpenter は reserved を最優先し、それ以外は価格の低いものから選ぶため、結果として spot が on-demand より先に選ばれ、取れなければ次にフォールバックして台数充足を目指します。全ノードが同じプール・同じ NodePool に属します。
- **Intent M**: reserved+spot を 1 プールに入れ、reserved ノードで長期訓練を走らせつつ spot ノードで推論やデータ前処理を同時に動かします。訓練 rank は nodeSelector `karpenter.sh/capacity-type: reserved` で reserved にピン留めし、spot に置きたい推論等は同じキーで `spot` にピン留めします。どちらの capacity-type に留まりたいかを Pod 側が明示的に指定する仕組みです。

両方とも `capacity_types` リストで表現しますが、使い方が異なります。詳しくは Basic06(Capacity Block)で扱います。

# まとめ

- `accelerator_pools` に 1 エントリ書くだけで、ヘテロジニアスな GPU DDP 環境が立ち上がります
- spot+on-demand フォールバック(Intent F)でコストと可用性を両立できます
- EFA 不要な小規模 DDP なら TCP gloo で十分に動きます
- 大規模・EFA 必須のシナリオは Basic05(EFA topology)と Basic06(Capacity Block)で扱います
