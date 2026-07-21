---
title: "Basic02 - CPU で分散学習を体験する (torchrun DDP)"
free: true
---

本章では、GPU を一切使わずに、Amazon EKS の CPU ノード上で PyTorch の分散学習（DDP: DistributedDataParallel）を動かします。GPU/Neuron や Karpenter がまだ無い Basic01 直後の状態でも実行でき、「複数プロセスが協調して 1 つのモデルを学習する」という分散学習の最小の成功体験を、追加コストほぼゼロで得ることが目的です。

:::message
本章は GPU 不要です。Basic01 で作った Amazon EKS クラスタと System ノード（m5 系）があれば実行できます。高額な GPU/Capacity Block に進む前に、まずここで「分散学習が EKS 上で動く」ことを自分の手で確認しておくと、以降の章の GPU 版が理解しやすくなります。
:::

# 解説

## 全体構成

この book 全体で構築する分散 AI 基盤のうち、本章は最小の入口です。GPU/Neuron ノードも Karpenter も使わず、Basic01 で立てた System ノード（CPU）の上で分散学習を動かします。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

図の下段、Amazon EKS コントロールプレーンと System ノードだけを使う構成です。アクセラレータプールや EFA には触れません。

## これは何をするものか

分散学習の最も基本的な形が DDP（DistributedDataParallel）です。DDP では、同じモデルの複製を複数のプロセス（rank）が持ち、各 rank が異なるデータのミニバッチで勾配を計算し、その勾配を全 rank で平均（all-reduce）してからモデルを更新します。これにより、実質的なバッチサイズを rank 数だけ増やして学習を高速化します。

DDP の通信バックエンドには 2 種類あります。

- **gloo**: CPU 上で動く。GPU 不要
- **nccl**: NVIDIA GPU 上で動く。GPU 間の高速な集合通信（後続の章で EFA と組み合わせる）

本章では **gloo backend** を使い、CPU ノード 1 台の中で複数プロセスを立てて DDP を動かします。学習対象は軽量な言語モデル `HuggingFaceTB/SmolLM2-135M` で、`databricks/databricks-dolly-15k` データセットの一部を使ってファインチューニングします。

起動には `torchrun` を使います。`torchrun --standalone --nproc_per_node=2` とすると、1 ノード内に 2 つのプロセス（rank 0, rank 1）を立て、それぞれに `RANK` / `WORLD_SIZE` / `LOCAL_RANK` などの環境変数を自動で設定してくれます。MPI Operator のような追加コンポーネントは不要で、Kubernetes の素の `batch/v1` Job として実行できるのが本章の手軽さのポイントです。

使うマニフェストはモジュール内蔵の [`cpu-gpu-torchrun-train.yaml.tpl`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/manifests/cpu-gpu-torchrun-train.yaml.tpl) です。このテンプレートは CPU（gloo）と GPU（nccl）の両対応で、同じ `train_smollm.py` を使い回します。CPU 版では GPU リソースをリクエストせず、`node-role: cpu` の nodeSelector で System ノード相当の CPU プールに Pod を載せます。

## 全体の中での位置付け

本章は基盤の一番低いところにある「動作確認の入口」です。Basic01 で Amazon EKS の土台を立てた直後、まだ Karpenter も GPU ノードも無い状態で実行できます。ここで DDP の仕組み（複数 rank・勾配の all-reduce・rank 0 だけがモデルを保存する挙動）を CPU で体験しておくと、Basic05（EFA）や Basic07（GPU での本格ワークロード）で登場する nccl backend・マルチノード通信が「gloo を GPU + EFA に置き換えたもの」として素直に理解できます。

## 注意

**CPU ノードは Karpenter の consolidation で消えることがあります。** CPU NodePool（後の章で Karpenter を入れた場合）は `consolidationPolicy: WhenEmptyOrUnderutilized` で、アイドルなノードを早めに回収します。学習 Job の Pod が単独で載っているノードが「余剰」と判断されて学習中に evict される事故を避けるため、Job の Pod には `karpenter.sh/do-not-disrupt: "true"` アノテーションを付けます（テンプレートに含まれています）。本章の時点では Karpenter 未導入で System ノード上で動くため影響しませんが、後続の章と組み合わせる際に効いてきます。

**CPU なので遅いです。** これは性能を測る章ではなく「動く」ことを確認する章です。SmolLM2-135M の 25 ステップで、CPU では十数分かかります（実測で train_runtime 約 937 秒）。GPU に載せれば桁違いに速くなりますが、それは後続の章で確認します。

# ワークショップ実施

## 1. 作業用 namespace を用意する

Basic01 で作った作業用 namespace を使います。ターミナルを開き直した場合に備えて、ここで冪等に用意し直しておきます（すでに存在していてもエラーになりません）。

```bash
export NAMESPACE=distai
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
```

## 2. CPU DDP の Job を投入する

テンプレートを CPU（gloo）設定でレンダリングして適用します。`__NPROC__=2` は 1 ノード内に立てる rank 数です。

```bash
IMAGE=<account>.dkr.ecr.<region>.amazonaws.com/mpijob-hf-sample:v1
sed -e "s/__NAMESPACE__/${NAMESPACE}/g" -e "s#__MPIJOB_IMAGE__#${IMAGE}#g" \
    -e "s/__NODE_ROLE__/cpu/g" -e "s/__NPROC__/2/g" -e "s/__BACKEND__/gloo/g" \
    -e '/__GPU_RESOURCES_IF_NEEDED__/d' -e '/__GPU_TOLERATIONS_IF_NEEDED__/d' \
    cpu-gpu-torchrun-train.yaml.tpl | kubectl apply -f -
```

## 3. 学習ログを確認する

```bash
kubectl logs -f job/smollm-torchrun -n "$NAMESPACE"
```

まず 2 つの rank が gloo backend で起動し、CPU で動いていることが分かります。

```
[rank 0/2] backend=gloo cuda_available=False device_count=0
[rank 1/2] backend=gloo cuda_available=False device_count=0
[rank 0/2] loading model HuggingFaceTB/SmolLM2-135M
[rank 1/2] loading model HuggingFaceTB/SmolLM2-135M
[rank 0/2] loading dataset databricks/databricks-dolly-15k (first 200 rows)
```

学習が始まると、loss が減少していきます（実測値）。

```
{'loss': 3.8219, 'grad_norm': 79.97, 'learning_rate': 4e-05, 'epoch': 0.2}
{'loss': 1.5746, 'grad_norm': 7.80,  'learning_rate': 3e-05, 'epoch': 0.4}
{'loss': 1.3407, 'grad_norm': 2.74,  'learning_rate': 2e-05, 'epoch': 0.6}
{'loss': 1.1859, 'grad_norm': 2.51,  'learning_rate': 1e-05, 'epoch': 0.8}
{'loss': 1.1408, 'grad_norm': 2.48,  'learning_rate': 0.0,   'epoch': 1.0}
```

loss が 3.82 → 1.14 に下がっており、2 rank が勾配を all-reduce しながら学習が進んでいることが確認できます。

## 4. 完了とモデル保存を確認する

```
[rank 0/2] saving final model to /tmp/output/final
[rank 1/2] done
[rank 0/2] done
```

最後に **rank 0 だけ**がモデルを保存し、両 rank が正常終了します。「モデルの保存は rank 0 のみが行う」というのは DDP の定石で、全 rank が同じモデルを持っているため保存は 1 つで足りるからです。この挙動を CPU で確認できれば、DDP の基本動作の理解は完了です。

```bash
kubectl wait --for=condition=complete job/smollm-torchrun -n "$NAMESPACE" --timeout=20m
```

## 5. 後片付け

```bash
kubectl delete job smollm-torchrun -n "$NAMESPACE"
```

# まとめ

本章では、GPU を使わずに Amazon EKS の CPU ノード上で torchrun による DDP 学習を動かしました。gloo backend で 2 rank が協調し、loss が 3.82 → 1.14 に減少、rank 0 のみがモデルを保存する、という DDP の基本動作を実測で確認しました。追加オペレータ不要の `batch/v1` Job として動くため、Basic01 直後の最小構成で試せます。次章以降で Karpenter を導入し、この DDP を GPU（nccl backend）に載せ替え、さらに EFA でマルチノードに広げていきます。

# 参考資料

- [PyTorch DistributedDataParallel](https://pytorch.org/docs/stable/notes/ddp.html)
- [torchrun (Elastic Launch)](https://pytorch.org/docs/stable/elastic/run.html)
- [awslabs/awsome-distributed-training の DDP テストケース](https://github.com/awslabs/awsome-distributed-training/tree/main/3.test_cases/pytorch/ddp)
- [対象マニフェスト cpu-gpu-torchrun-train.yaml.tpl](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/manifests/cpu-gpu-torchrun-train.yaml.tpl)
