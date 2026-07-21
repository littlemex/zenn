---
title: "Basic06 - 実ワークロードで動作確認する (GRPO)"
free: true
---

本章では、Ch1-5 で構築した基盤（EKS + Karpenter + Capacity Block + EFA）の上で、実際の分散強化学習ワークロード（miles/GRPO）を動かします。これまでの章は個別のコマンドで基盤の各要素を確認してきましたが、本章ではその基盤の上で GPU 分散ワークロードを端から端まで動かし、マルチノード EFA 通信が本番相当の負荷で正しく機能することを実証します。

# 解説

## 全体構成

![EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章では、この図で組み上げてきた VPC・EKS コントロールプレーン・Karpenter・アクセラレータノード・EFA の全体を使い、その上に KubeRay 経由で実ワークロードを載せる部分を扱います。基盤そのものは変更せず、基盤が正しく機能しているかを実ワークロードで検証する章です。

## これは何をするものか

このチャプターでは、2 ノード 16 GPU（p5en x2）にまたがる分散強化学習ループを最初から最後まで動かし、以下を実証します。

- 2 ノード 16 GPU にまたがる NCCL all-reduce が EFA 経由で動く
- KubeRay を使った Ray クラスタが Capacity Block ノード上で正しく構成される
- SGLang（推論）と Megatron-LM（訓練）を統合した GRPO ループが完走する

使用するのは [miles](https://github.com/radixark/miles) です。SLIME のフォークで、Megatron + SGLang + Ray による GRPO（Group Relative Policy Optimization）を実装したフレームワークです。

![実ワークロード実行構成 KubeRay + miles GRPO](/images/books/eks-distributed-ai/arch-workload.png)

KubeRay が RayCluster を Capacity Block の p5en ノード上に展開し、Ray Job として投入された miles の GRPO ループの中で、SGLang（推論）と Megatron-LM（訓練）の重みが weight sync され、ノード間の勾配同期と重み転送が EFA 経由の NCCL で行われます。

:::message
このチャプターの目的は「基盤が動く」ことの実証であり、GRPO の詳細な解説やハイパーパラメータチューニングは扱いません。ワークロード自体の詳細は [distributed-ai リポジトリの miles test case](https://github.com/littlemex/distributed-ai/tree/8d0d062/2026-07-19-miles-training-inference-mismatch/3.test_cases/pytorch/miles) を参照してください。
:::

## 全体の中での位置付け

本章は基盤構築の最上層にあたります。Ch1 で作った EKS + System ノードの上に、Ch2 で Karpenter を載せ、Ch3-5 でアクセラレータノード・Capacity Block・EFA を積み上げてきました。本章ではその全部を使い切り、実ワークロードを流すことで基盤全体の妥当性を確認します。これ以降の章に進むための前提はここまでで揃っており、本章は「基盤側の作業」ではなく「基盤の検証」というスコープです。

## 注意

**miles イメージには `aws-ofi-nccl` が必須です。** PyTorch の標準イメージには NCCL は入っていますが `aws-ofi-nccl`（EFA 用の NCCL ネットワークプラグイン）は入っていません。これが無いと NCCL は `internal network plugin`（= TCP/Socket）しか使えず、EFA リソースをリクエストしていても帯域は 1/20 以下になります。miles の Dockerfile にはこのプラグインが含まれています。

**`hostNetwork: true` が EFA に必要です。** EFA の SRD トラフィックは Pod のネットワーク名前空間を経由できません。Worker Pod は `hostNetwork: true` で起動し、ホストの EFA インターフェースを直接使う必要があります。ポート衝突を避けるため、sshd は 2222 で動かします。

**`MASTER_ADDR` は Worker-0 の Pod DNS 名を使います。** MPI Operator 経由の場合は `<job-name>-worker-0.<job-name>.<namespace>.svc` の形式です。torchrun 方式の場合は Pod IP を直接使えます。

**GRPO の詳細な手順はこの book のスコープ外です。** miles の config 設定、モデル選択、データセット準備などの詳細は [distributed-ai リポジトリの miles test case](https://github.com/littlemex/distributed-ai/tree/8d0d062/2026-07-19-miles-training-inference-mismatch/3.test_cases/pytorch/miles) を参照してください。この chapter の目的はあくまで「基盤が正しく動いている」ことの実証です。

# ワークショップ実施

## 1. 前提を確認する

Ch5 で作成した Capacity Block の p5en x2 が `Ready` になっていることを確認します。

```bash
kubectl get nodes -l karpenter.sh/nodepool=<accelerator-nodepool-name>
```

2 台の p5en ノードが `Ready` であれば進めます。まだ揃っていない場合は Ch5 に戻って Capacity Block の予約状況を確認してください。

## 2. EFA 通信を検証する

GRPO を投入する前に、2 ノード間で `torchrun` による NCCL all-reduce を実行し、EFA 通信が成立していることを先に確認します（Ch4「EFA でマルチノード通信を検証する」と同じ検証です）。

```bash
FI_PROVIDER=efa \
NCCL_DEBUG=INFO \
NCCL_DEBUG_SUBSYS=INIT,NET \
NCCL_SOCKET_IFNAME=eth0 \
torchrun --nnodes 2 --node_rank <0|1> --nproc_per_node 8 \
  --master_addr <rank0-pod-ip> --master_port 29500 bench.py
```

ログに `NET/OFI Selected provider is efa` が出て `NET/Socket` が出ないことを確認します。

:::message alert
`NET/Socket` が出ている場合は EFA が使われず TCP fallback しています。ワークロードは動きますが帯域が 1/20 以下になるため、次のステップに進む前に Ch4 の EFA セキュリティグループ設定を見直してください。
:::

## 3. KubeRay クラスタをデプロイする

```bash
kubectl apply -f kubernetes/raycluster.yaml
kubectl get raycluster
```

`AVAILABLE WORKERS` が `DESIRED WORKERS` と一致し、`GPUS` が 16 になっていることを確認します。

## 4. GRPO を投入する

```bash
source env_vars.colocated.example
ray job submit --address http://localhost:10001 -- \
  python -m miles.train \
    --model $MODEL \
    --dataset $DATASET \
    --num-rollouts $NUM_ROLLOUTS \
    --colocated
```

`rollout SUCCEEDED` と `raw_reward` がログに出力されれば、SGLang による推論と Megatron による訓練が正しく協調して動いています。

## 5. EFA 利用を最終確認する

GRPO 実行中の NCCL ログで `via NET/Libfabric/*/GDRDMA` が出ていることを確認します。これが出ていれば、実ワークロードの重み転送・勾配同期がすべて EFA 経由で行われていることの確定的な証拠になります。

# まとめ

本章では、Ch1-5 で構築した EKS + Karpenter + Capacity Block + EFA の基盤の上で、miles/GRPO による実際の分散強化学習ワークロードを最初から最後まで動かしました。KubeRay で 2 ノード 16 GPU の Ray クラスタを構成し、SGLang と Megatron-LM を統合した GRPO ループが完走することを確認し、その通信がすべて EFA 経由（busbw 190-257 GB/s）であることを実測で確認しました。`aws-ofi-nccl` の同梱と `hostNetwork: true` の指定が、EFA を実ワークロードで使う上での必須条件です。

# 参考資料

- [miles (radixark/miles)](https://github.com/radixark/miles)
- [distributed-ai リポジトリの miles test case](https://github.com/littlemex/distributed-ai/tree/8d0d062/2026-07-19-miles-training-inference-mismatch/3.test_cases/pytorch/miles)
- [miles test case の README（イメージビルド手順）](https://github.com/littlemex/distributed-ai/blob/8d0d062/2026-07-19-miles-training-inference-mismatch/3.test_cases/pytorch/miles/README.md)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
- [AWS OFI NCCL Plugin](https://github.com/aws/aws-ofi-nccl)
