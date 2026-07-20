---
title: "実ワークロードで動作確認 — GRPO on 2-node EFA"
---

## メインテーマ

Ch1-5 で構築した基盤（EKS + Karpenter + Capacity Block + EFA）の上で、実際の分散強化学習ワークロード（miles/GRPO）を動かし、マルチノード EFA 通信が本番相当の負荷で正しく機能することを確認する。

## これは何をするものか

ここまでのチャプターでは「基盤が正しく構成されているか」を個別のコマンドで確認してきた。このチャプターでは、その基盤の上で実際の GPU 分散ワークロードを端から端まで動かし、以下を実証する:

- 2 ノード 16 GPU（p5en x2）にまたがる NCCL all-reduce が EFA 経由で動く
- KubeRay を使った Ray クラスタが CB ノード上で正しく構成される
- SGLang（推論）と Megatron-LM（訓練）を統合した GRPO ループが完走する

使用するのは [miles](https://github.com/radixark/miles) — SLIME のフォークで、Megatron + SGLang + Ray による GRPO（Group Relative Policy Optimization）を実装したフレームワーク。

:::message
このチャプターの目的は「基盤が動く」ことの実証であり、GRPO の詳細な解説やハイパーパラメータチューニングは扱わない。ワークロード自体の詳細は [distributed-ai リポジトリの miles test case](https://github.com/littlemex/distributed-ai/tree/8d0d062/2026-07-19-miles-training-inference-mismatch/3.test_cases/pytorch/miles) を参照。
:::

## 全体の中での位置付け

![チャプターの位置付け](/images/books/eks-distributed-ai/ch6-run-workloads.png)

## 実際に挙動を確認する

### 前提

- Ch5 で Capacity Block の p5en x2 が稼働していること
- miles イメージが ECR に push 済みであること（イメージビルドは [miles test case の README](https://github.com/littlemex/distributed-ai/blob/8d0d062/2026-07-19-miles-training-inference-mismatch/3.test_cases/pytorch/miles/README.md) に記載）

### 1. EFA の 2 ノード通信を検証する

GRPO を動かす前に、NCCL が EFA を使えていることを先に確認しておく。miles イメージには `aws-ofi-nccl` プラグインが含まれているため、`torchrun` で直接確認できる:

```bash
# 2 つの Pod を p5en ノードに 1 つずつ配置し、torchrun で NCCL all-reduce を実行
# Pod 内から:
FI_PROVIDER=efa \
NCCL_DEBUG=INFO \
NCCL_DEBUG_SUBSYS=INIT,NET \
NCCL_SOCKET_IFNAME=eth0 \
torchrun --nnodes 2 --node_rank <0|1> --nproc_per_node 8 \
  --master_addr <rank0-pod-ip> --master_port 29500 bench.py
```

確認ポイント:
- ログに `NET/OFI Selected provider is efa, fabric is efa-direct` が出る
- `[send] via NET/Libfabric/*/GDRDMA` — GPU Direct RDMA が有効
- `NET/Socket` が一切出ない（TCP fallback なし）

実測値（2 ノード p5en.48xlarge、H200 x16、EFA 15 使用。Ch4 と同一環境での計測）:

| メッセージサイズ | algbw | busbw |
|---|---|---|
| 64 MB | 101.5 GB/s | 190.3 GB/s |
| 256 MB | 100.5 GB/s | 188.5 GB/s |
| 1024 MB | 127.2 GB/s | 238.4 GB/s |
| 4096 MB | 135.8 GB/s | 254.7 GB/s |
| 8192 MB | 137.1 GB/s | 257.1 GB/s |

busbw 190-257 GB/s は TCP（~10 GB/s）の 20-25 倍であり、EFA が正しく動作している直接的な証拠。

### 2. KubeRay クラスタをデプロイする

miles は Ray を使って SGLang（推論）と Megatron（訓練）を協調させる。RayCluster manifest をデプロイする:

```bash
kubectl apply -f kubernetes/raycluster.yaml
kubectl get raycluster
```

期待される出力:
```
NAME        DESIRED WORKERS   AVAILABLE WORKERS   CPUS   MEMORY   GPUS   STATUS   AGE
miles-ray   2                 2                   182    3608Gi   16     ready    5m
```

16 GPU（8 GPU x 2 ノード）が Ray クラスタに参加していれば OK。

Ray dashboard で状態を確認:
```bash
kubectl port-forward svc/miles-ray-head-svc 8265:8265
# http://localhost:8265 で 16 GPU が見える
```

### 3. GRPO を実行する

miles の GRPO ループを Ray Job として投入:

```bash
# 環境変数ファイルを読み込み（モデル名、ハイパーパラメータ等）
source env_vars.colocated.example

# Ray Job submit
ray job submit --address http://localhost:10001 -- \
  python -m miles.train \
    --model $MODEL \
    --dataset $DATASET \
    --num-rollouts $NUM_ROLLOUTS \
    --colocated
```

確認ポイント:
- `rollout SUCCEEDED` のログが出る（SGLang が推論を完了した）
- Megatron の backward pass でグラデーションが計算される
- `raw_reward` が表示される（報酬関数が動作している証拠）

実測参考（Qwen3-4B、colocated、2 ノード 16 GPU、3 cycles）:
```
[rank 0] rollout 0 SUCCEEDED, raw_reward=0.48
[rank 0] rollout 1 SUCCEEDED, raw_reward=0.52
[rank 0] rollout 2 SUCCEEDED, raw_reward=0.49
```

### 4. EFA 利用を確認する

GRPO 実行中の NCCL ログ（`NCCL_DEBUG=INFO` を設定した場合）で、inter-node の all-reduce が EFA 経由であることを確認:

```
NET/OFI Selected provider is efa, fabric is efa-direct (found 8 nics)
Channel 00/0 : 3[3] -> 14[6] [send] via NET/Libfabric/3/GDRDMA
```

`NET/Socket` や `Using internal network plugin` が出ていたら TCP fallback しており、Ch4 の注意点（EFA SG の egress self-ref、`NCCL_SOCKET_IFNAME`、`aws-ofi-nccl` の有無）を再確認する。

## 注意点

**miles イメージには `aws-ofi-nccl` が必須。** PyTorch の標準イメージには NCCL は入っているが `aws-ofi-nccl`（EFA 用の NCCL ネットワークプラグイン）は入っていない。これが無いと NCCL は `internal network plugin`（= TCP/Socket）しか使えず、EFA リソースをリクエストしていても帯域は 1/20 以下になる。miles の Dockerfile にはこのプラグインが含まれている。

**`hostNetwork: true` が EFA に必要。** EFA の SRD トラフィックは Pod のネットワーク名前空間を経由できない。Worker Pod は `hostNetwork: true` で起動し、ホストの EFA インターフェースを直接使う必要がある。ポート衝突を避けるため、sshd は 2222 で動かす。

**`MASTER_ADDR` は Worker-0 の Pod DNS 名を使う。** MPI Operator 経由の場合は `<job-name>-worker-0.<job-name>.<namespace>.svc` の形式。torchrun 方式の場合は Pod IP を直接使える。

**GRPO の詳細な手順はこの book のスコープ外。** miles の config 設定、モデル選択、データセット準備などの詳細は [distributed-ai リポジトリ](https://github.com/littlemex/distributed-ai/tree/8d0d062/2026-07-19-miles-training-inference-mismatch/3.test_cases/pytorch/miles) を参照。この chapter の目的はあくまで「基盤が正しく動いている」ことの実証である。
