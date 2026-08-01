---
title: "Basic04 - GPU DDP をヘテロジニアスなプールで回す"
free: true
---

本章では、Basic02 で CPU だけだった分散学習(DDP)を GPU に載せ替えます。`accelerator_pools` の新しい capacity-mix スキーマを使い、g5 と g6 を混ぜた spot+on-demand フォールバック構成で 2 ノード GPU DDP を実行します。

# はじめに

## このシナリオが解決する課題

分散学習の実機検証では「とにかく GPU ノードを 2 台確保して DDP を回したい」という要求が頻繁に発生します。しかし単一のインスタンスタイプに絞ると、そのサイズがキャパシティ不足のとき InsufficientInstanceCapacity で詰まります。

本章のアプローチは次の通りです。

- g5(A10G)と g6(L4)を**ヘテロジニアスに混ぜる**: 片方が取れなくても他方でノードが立つ
- spot を第一優先、on-demand をフォールバックに: コストを抑えつつ確実に 2 台確保する(Intent F)
- 単一 AZ に固定: gloo/TCP バックエンドの DDP でも cross-AZ レイテンシを避ける

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
    zones           = ["us-west-2c"]
    efa_interface_count = 0
    labels          = { workload = "ddp-basic04" }
  }
}
```

この 1 エントリから、Karpenter の NodePool と EC2NodeClass が自動生成されます。

## 設定の意味

| フィールド | 値 | 効果 |
|---|---|---|
| instance_types | g6.2xlarge, g5.2xlarge, g6.xlarge, g5.xlarge | 4 種のうちキャパシティがあるものを Karpenter が選択 |
| capacity_types | ["spot", "on-demand"] | spot 優先、取れなければ on-demand にフォールバック(Intent F) |
| zones | ["us-west-2c"] | p5en CB と同じ AZ に配置し、将来の共有ストレージ接続に備える |
| efa_interface_count | 0 | 小型 GPU に EFA は不要(TCP gloo で十分) |
| labels | workload=ddp-basic04 | Pod の nodeSelector で狙い撃ち |

## プール確保ロジックの全体像

設定がどう処理されるかを図にまとめました。

![accelerator_pools ロジックフロー](/images/books/eks-distributed-ai/accelerator-pools-logic.png)

入力(tfvars)から NodePool/EC2NodeClass のレンダリングまで、正規化(pool_effective)→ EFA 導出(EC2 API)→ disruption preset → Kubernetes リソース生成、という 4 段パイプラインで処理されます。

## インタラクティブシミュレータ

設定を変えるとアロケーション結果がどう変わるかを、以下のシミュレータで試せます。

@[codepen](https://codepen.io/littlemex/pen/PLACEHOLDER?default-tab=result)

インスタンスタイプ、capacity_types、zones を変えると、NodePool の requirement values、EFA topology、disruption 設定がリアルタイムで更新されます。

# GPU DDP を実行する

## 前提

- Basic03 で Karpenter + GPU Operator が導入済み
- `terraform apply` で gpu-ddp プールが作成済み(NodePool + EC2NodeClass)

## TrainJob で 2 ノード DDP

Basic02 と同じ PyTorch DDP コードを使います。違いは nodeSelector で GPU プールを指定する点だけです。

```yaml
apiVersion: trainer.kubeflow.org/v1alpha1
kind: TrainJob
metadata:
  name: gpu-ddp-basic04
spec:
  runtimeRef:
    name: torch-distributed
  trainer:
    image: <ECR_URI>/basic02-train:latest
    numNodes: 2
    resourcesPerNode:
      requests:
        nvidia.com/gpu: "1"
    env:
      - name: MASTER_PORT
        value: "29500"
    nodeSelector:
      workload: ddp-basic04
    tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
```

## 実行と確認

```bash
kubectl apply -f gpu-ddp-trainjob.yaml
kubectl get pods -w -l job-name=gpu-ddp-basic04
```

Karpenter が spot で 2 台の g5/g6 ノードを起動し、それぞれに 1 GPU ずつ割り当てて DDP が走ります。spot が取れない場合は on-demand にフォールバックします。

```bash
kubectl logs gpu-ddp-basic04-node-0-0 --tail=20
```

`[rank0] training complete` と `[rank1] training complete` が両方出れば成功です。

# Intent F と Intent M の違い

本章の構成は **Intent F(フォールバック)** です。

- **Intent F**: 1 つのプールに複数の capacity_types を並べ、Karpenter の native priority(reserved > spot > on-demand)で台数充足を目指す。全ノードが同じプール・同じ NodePool に属する。
- **Intent M**: reserved+spot を 1 プールに入れ、reserved ノードで長期訓練を走らせつつ spot ノードで推論やデータ前処理を同時に動かす。Pod が nodeSelector `karpenter.sh/capacity-type: reserved` で明示的にピン留めする。

両方とも `capacity_types` リストで表現しますが、使い方が異なります。詳しくは Basic06(Capacity Block)で扱います。

# まとめ

- `accelerator_pools` に 1 エントリ書くだけで、ヘテロジニアスな GPU DDP 環境が立ち上がる
- spot+on-demand フォールバック(Intent F)でコストと可用性を両立
- EFA 不要な小規模 DDP なら TCP gloo で十分に動く
- 大規模・EFA 必須のシナリオは Basic05(EFA topology)と Basic06(Capacity Block)で扱う
