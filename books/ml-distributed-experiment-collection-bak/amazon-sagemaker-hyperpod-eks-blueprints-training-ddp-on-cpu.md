---
title: "Amazon SageMaker HyperPod EKS トレーニング Blueprint"
emoji: "🚀"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["aws", "sagemaker", "hyperpod", "eks", "distributed-training"]
free: true
---

::::details 前提
:::message
**対象読者**: 大規模基盤モデルがどういうものかを理解している方、これからモデル学習を行う方
:::
:::message
**ライセンス**: © 2025 littlemex.
本文および自作図表: CC BY 4.0
※公式ドキュメントからの引用や翻訳部分は原典の著作権に従います。
引用画像: 各画像の出典に記載されたライセンスに従います。
:::
:::message
一部 AI を用いて文章を作成します。レビューは実施しますが、見逃せない重大な間違いなどがあれば[こちらのIssue](https://github.com/littlemex/samples/issues)から連絡をお願いします。
:::
::::

**本章では Amazon SageMaker HyperPod EKS 上での分散学習とファインチューニングの実践的な手法を解説します。以下の公式ドキュメントをマスターとして説明に日本語の補足を加えて実施します。**

:::message
実装が変更される可能性があるため必要に応じて公式ドキュメントを確認ください。
:::

公式ドキュメント

https://awslabs.github.io/ai-on-sagemaker-hyperpod/docs/category/eks-blueprints

---

# Amazon SageMaker HyperPod EKS でのトレーニング手法

本章では、Amazon SageMaker HyperPod の EKS オーケストレーションモードで利用可能な分散学習手法とファインチューニング手法を解説します。各手法の特徴、適用場面、実装手順を詳しく説明します。

## 概要

Amazon SageMaker HyperPod EKS では、以下の分散学習およびファインチューニング手法が利用可能です。

### トレーニング手法

1. **Distributed Data Parallel (DDP)** - PyTorch 標準のデータ並列化手法
2. **Fully Sharded Data Parallel (FSDP)** - メモリ効率的な大規模モデルトレーニング
3. **NVIDIA Megatron-LM** - テンソル並列とパイプライン並列による超大規模モデルトレーニング
4. **AWS Trainium** - AWS 独自のアクセラレータによる高効率トレーニング

### ファインチューニング手法

1. **LoRA (Low-Rank Adaptation)** - パラメータ効率的なファインチューニング
2. **DPO (Direct Preference Optimization)** - （開発中）

## 手法の比較

以下の表は各手法の特徴をまとめたものです。

| 手法 | 適用場面 | メモリ効率 | 通信オーバーヘッド | 実装難易度 | ハードウェア要件 |
|------|----------|-----------|------------------|-----------|----------------|
| DDP | 小〜中規模モデル | 低 | 中 | 低 | GPU/CPU |
| FSDP | 大規模モデル | 高 | 中〜高 | 中 | GPU（推奨） |
| Megatron-LM | 超大規模モデル | 最高 | 高 | 高 | 高性能 GPU（P5、P4d など）|
| Trainium | 大規模モデル | 高 | 低 | 中 | AWS Trainium |
| LoRA | ファインチューニング | 最高 | 低 | 低 | GPU/Trainium |

---

# 1. Distributed Data Parallel (DDP)

## 1.1 概要

Distributed Data Parallel（DDP）は PyTorch が提供する標準的なデータ並列化手法です。各プロセスがモデルの完全なレプリカを保持し、異なるデータバッチで並列にトレーニングを実行します。

### 特徴

- **シンプルな実装**: PyTorch の標準機能として提供されており、学習曲線が緩やか
- **柔軟性**: CPU と GPU の両方で動作可能
- **効率的な勾配同期**: All-Reduce 通信パターンによる効率的な勾配同期
- **スケーラビリティ**: 小〜中規模モデルで優れたスケーラビリティ

### 適用場面

- 小〜中規模モデル（数十億パラメータ以下）のトレーニング
- マルチ GPU 環境での基本的な並列化
- CPU のみの環境でのトレーニング（開発・テスト用途）

## 1.2 前提条件

::::details インフラストラクチャ要件

- ✅ Amazon SageMaker HyperPod EKS クラスターがデプロイされ稼働中であること
- ✅ 適切なインスタンスタイプの EKS ノードグループ（例: ml.m5.2xlarge）
- ✅ FSx for Lustre ファイルシステムがクラスターにマウントされていること
- ✅ Kubeflow Training Operator がクラスターにインストールされていること
::::

::::details 開発環境

- ✅ AWS CLI v2 がインストールされ、適切な権限で設定されていること
- ✅ kubectl がインストールされ、EKS クラスターにアクセスできること
- ✅ Docker がインストールされていること
- ✅ envsubst ユーティリティ（テンプレート処理用）
- ✅ Git（リポジトリのクローン用）
::::

::::details AWS 権限

AWS 認証情報には以下の権限が必要です。

- ✅ Amazon ECR - コンテナイメージの push/pull
- ✅ Amazon EKS - クラスターリソースへのアクセス
- ✅ Amazon FSx - 共有ファイルシステムへのアクセス
- ✅ Amazon EC2 - インスタンスとアベイラビリティーゾーンの記述
- ✅ AWS STS - 認証情報の取得
::::

::::details クラスターの検証

クラスターが準備完了であることを確認します。

```bash
# クラスターの状態を確認
kubectl get nodes

# Kubeflow Training Operator が実行中であることを確認
kubectl get pods -n kubeflow

# FSx ストレージが利用可能であることを確認
kubectl get pvc

# リソースを作成できることを確認
kubectl auth can-i create pytorchjobs
```
::::

## 1.3 Docker イメージのセットアップ

:::message
- [ ] 1.3.1 リポジトリのクローン
- [ ] 1.3.2 Docker イメージのビルド
- [ ] 1.3.3 イメージの Amazon ECR へのプッシュ
:::

::::details 1.3.1 リポジトリのクローン

:::message
なんのための作業か: トレーニングコードと Docker 設定を取得します。AWS 分散トレーニングサンプルリポジトリには、Kubernetes 向けに最適化された PyTorch DDP サンプルが含まれています。
:::

:::message
次のステップに進む条件: `awsome-distributed-training/3.test_cases/pytorch/cpu-ddp/kubernetes` ディレクトリに移動でき、必要なファイルが存在すること。
:::

リポジトリをクローンして作業ディレクトリに移動します。

```bash
cd ~
git clone https://github.com/aws-samples/awsome-distributed-training/
cd awsome-distributed-training/3.test_cases/pytorch/cpu-ddp/kubernetes
```

ディレクトリの内容を確認します。

```bash
ls -la
```
::::

::::details 1.3.2 Docker イメージのビルド

:::message
なんのための作業か: PyTorch、DDP トレーニングコード、全ての必要な依存関係を含むコンテナイメージをビルドします。
:::

:::message alert
**$DOCKER_NETWORK 変数について**

環境変数 `$DOCKER_NETWORK` は、SageMaker Studio Code Editor CloudFormation スタックをデプロイした場合にのみ `--network=sagemaker` に設定されます。これは SageMaker Studio がコンテナに特定のネットワーク設定を使用するために必要です。それ以外の場合は未設定のままです。
:::

:::message
次のステップに進む条件: `docker build` コマンドが正常に完了し、"Successfully built" メッセージが表示されること。通常 3 から 5 分かかります。
:::

環境変数を設定します。

```bash
export AWS_REGION=$(aws ec2 describe-availability-zones --output text --query 'AvailabilityZones[0].[RegionName]')
export ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export REGISTRY=${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/
```

Docker イメージをビルドします。

```bash
docker build $DOCKER_NETWORK -t ${REGISTRY}fsdp:pytorch2.2-cpu ..
```

ビルドが成功すると、以下のようなメッセージが表示されます。

```
Successfully built 123ab12345cd
Successfully tagged 123456789012.dkr.ecr.us-east-2.amazonaws.com/fsdp:pytorch2.2-cpu
```
::::

::::details 1.3.3 イメージの Amazon ECR へのプッシュ

:::message
なんのための作業か: コンテナレジストリが存在しない場合は作成し、コンテナイメージをプッシュします。これにより、EKS クラスターノードからイメージが利用可能になります。
:::

:::message
次のステップに進む条件: `docker push` コマンドが正常に完了し、イメージが ECR にプッシュされること。EC2/CloudShell を使用する場合、通常 6 から 8 分かかります。
:::

レジストリを作成します（存在しない場合）。

```bash
REGISTRY_COUNT=$(aws ecr describe-repositories | grep \"fsdp\" | wc -l)
if [ "$REGISTRY_COUNT" == "0" ]; then
    aws ecr create-repository --repository-name fsdp
fi
```

レジストリにログインします。

```bash
echo "Logging in to $REGISTRY ..."
aws ecr get-login-password | docker login --username AWS --password-stdin $REGISTRY
```

イメージをレジストリにプッシュします。

```bash
docker image push ${REGISTRY}fsdp:pytorch2.2-cpu
```
::::

## 1.4 トレーニングジョブの準備

:::message
- [ ] 1.4.1 envsubst のインストール
- [ ] 1.4.2 マニフェストファイルの生成
:::

::::details 1.4.1 envsubst のインストール

:::message
なんのための作業か: テンプレートファイルとパラメータから Kubernetes マニフェストファイルを生成するツールをインストールします。
:::

:::message
次のステップに進む条件: `envsubst --version` コマンドが正常に実行できること。
:::

envsubst がインストールされているか確認します。

```bash
which envsubst
```

インストールされていない場合は、以下のインストール手順に従ってください。

https://github.com/a8m/envsubst?tab=readme-ov-file#installation
::::

::::details 1.4.2 マニフェストファイルの生成

:::message
なんのための作業か: インスタンスタイプ、ノード数、CPU 数に基づいてクラスター仕様に合わせた Kubernetes マニフェストを生成します。
:::

:::message
次のステップに進む条件: `fsdp.yaml` ファイルが生成され、クラスターの仕様に合わせた設定が含まれていること。
:::

クラスターの仕様を確認します。

```bash
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,INSTANCETYPE:.metadata.labels.node\.kubernetes\.io/instance-type,CPU:.status.capacity.cpu"
```

出力例は以下のようになります。

```
NAME                           INSTANCETYPE    CPU
hyperpod-i-0427a1830f8e4a49e   ml.m5.2xlarge   4
hyperpod-i-052768f9f54856cd6   ml.m5.2xlarge   4
```

環境変数を設定して envsubst を実行し、`fsdp.yaml` を生成します。

**ml.m5.2xlarge × 2 の場合:**

```bash
export IMAGE_URI=${REGISTRY}fsdp:pytorch2.2-cpu
export INSTANCE_TYPE=ml.m5.2xlarge
export NUM_NODES=2
export CPU_PER_NODE=4

cat fsdp.yaml-template | envsubst > fsdp.yaml
```

### FSx PVC 名の確認と更新

テンプレートファイルは FSx Lustre ボリュームが `fsx-pvc` としてクレームされていることを前提としています。

クラスター内の FSx Lustre ファイルシステムのクレーム名を確認します。

```bash
kubectl get pvc
```

出力例は以下のようになります。

```
NAME        STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
fsx-claim   Bound    pvc-ed0fd2cb-33da-498d-bab4-bf08cb4d555c   1200Gi     RWX            fsx-sc         <unset>                 6d22h
```

FSx Lustre ボリュームが `fsx-pvc` 以外の名前（例: `fsx-claim`）でクレームされている場合は、以下のコマンドで更新します。

```bash
sed 's/fsx-pv/fsx-claim/g' -i ./fsdp.yaml
```

### エポック数の調整（オプション）

レジリエンシー機能をテストするためにトレーニングジョブを長時間実行したい場合は、エポック数を増やします。

```bash
sed 's/5000/50000/g' -i ./fsdp.yaml
```

この例では、エポック数（107 行目）が `5000` から `50000` に増加します。
::::

## 1.5 トレーニングワークロードのデプロイ

:::message
- [ ] 1.5.1 トレーニングジョブのデプロイ
- [ ] 1.5.2 ジョブの監視
- [ ] 1.5.3 トレーニングの停止
:::

::::details 1.5.1 トレーニングジョブのデプロイ

:::message
なんのための作業か: 生成されたマニフェストファイル `fsdp.yaml` を使用して、トレーニングワークロードをデプロイします。
:::

:::message
次のステップに進む条件: kubectl コマンドが正常に完了し、Service、Deployment、PyTorchJob が作成されたことを示すメッセージが表示されること。
:::

トレーニングワークロードをデプロイします。

```bash
kubectl apply -f ./fsdp.yaml
```

以下のメッセージが表示されます。

```
service/etcd created
deployment.apps/etcd created
pytorchjob.kubeflow.org/fsdp created
```
::::

::::details 1.5.2 ジョブの監視

:::message
なんのための作業か: ジョブの状態を確認し、トレーニングの進行状況を監視します。
:::

:::message
次のステップに進む条件: Pod のステータスが "Running" になり、ログからトレーニングの進行状況が確認できること。初回実行時は Pod のステータスが "ContainerCreating" から "Running" に変わるまで 2 から 3 分かかります。
:::

ジョブの状態を確認します。

```bash
kubectl get pytorchjob
kubectl get pods
```

出力例は以下のようになります。

```
NAME   STATE     AGE
fsdp   Running   40s

NAME                    READY   STATUS    RESTARTS   AGE
etcd-7787559c74-msgpq   1/1     Running   0          49s
fsdp-worker-0           1/1     Running   0          49s
fsdp-worker-1           1/1     Running   0          49s
```

各 Pod はジョブログを生成します。以下のコマンドでログを監視できます。

```bash
kubectl logs -f fsdp-worker-0
```

出力例は以下のようになります。

```
[2024-07-19 04:39:07,890] torch.distributed.run: [WARNING] *****************************************
INFO 2024-07-19 04:39:07,958 Etcd machines: ['http://0.0.0.0:2379']
INFO 2024-07-19 04:39:07,964 Attempting to join next rendezvous
INFO 2024-07-19 04:39:07,965 Observed existing rendezvous state: {'status': 'joinable', 'version': '1', 'participants': [0]}
INFO 2024-07-19 04:39:08,062 Joined rendezvous version 1 as rank 1. Full state: {'status': 'frozen', 'version': '1', 'participants': [0, 1], 'keep_alives': []}
INFO 2024-07-19 04:39:08,062 Waiting for remaining peers.
INFO 2024-07-19 04:39:08,063 All peers arrived. Confirming membership.
INFO 2024-07-19 04:39:08,149 Waiting for confirmations from all peers.
INFO 2024-07-19 04:39:08,161 Rendezvous version 1 is complete. Final state: {'status': 'final', 'version': '1', 'participants': [0, 1], 'keep_alives': ['/torchelastic/p2p/run_none/rdzv/v_1/rank_1', '/torchelastic/p2p/run_none/rdzv/v_1/rank_0'], 'num_workers_waiting': 0}
INFO 2024-07-19 04:39:08,161 Creating EtcdStore as the c10d::Store implementation...
[RANK 1] Epoch 4991 | Batchsize: 32 | Steps: 8
Epoch 4990 | Training snapshot saved at /fsx/snapshot.pt
[RANK 0] Epoch 4991 | Batchsize: 32 | Steps: 8
[RANK 1] Epoch 4992 | Batchsize: 32 | Steps: 8
[RANK 0] Epoch 4992 | Batchsize: 32 | Steps: 8
```
::::

::::details 1.5.3 トレーニングの停止

:::message
なんのための作業か: 現在のトレーニングジョブを停止します。
:::

:::message
次のステップに進む条件: kubectl コマンドが正常に完了し、リソースが削除されること。
:::

以下のコマンドを使用してトレーニングジョブを停止します。

```bash
kubectl delete -f ./fsdp.yaml
```
::::

## 1.6 シンプルな方法でのトレーニング実行（代替手段）

カスタムコンテナイメージのビルドなしで、より簡単な方法を使用したい場合は、事前ビルドされたサンプルを使用できます。

::::details シンプルな方法の手順

### リポジトリのクローン

```bash
cd ~
git clone https://github.com/aws-samples/awsome-distributed-training/
cd awsome-distributed-training/3.test_cases/pytorch/cpu-ddp/kubernetes
```

レジリエンシー機能をテストしたい場合は、以下のコマンドでエポック数を増やします。

```bash
sed 's/5000/50000/g' -i ./fsdp-simple.yaml
```

### トレーニングワークロードのデプロイ

```bash
kubectl apply -f ./fsdp-simple.yaml
```

### 監視

```bash
kubectl get pytorchjob
kubectl get pods
kubectl logs -f fsdp-worker-0
```

### 停止

```bash
kubectl delete -f ./fsdp-simple.yaml
```
::::

---

# 2. Fully Sharded Data Parallel (FSDP)

## 2.1 概要

Fully Sharded Data Parallel（FSDP）は PyTorch が提供するメモリ効率的な大規模モデルトレーニング手法です。モデルパラメータ、勾配、オプティマイザの状態を全てのデバイスに分散（シャード）することで、単一のデバイスに収まらない大規模モデルのトレーニングを可能にします。

### 特徴

- **メモリ効率**: モデルの状態を全デバイスに分散することで、メモリ使用量を大幅に削減
- **スケーラビリティ**: 数十億から数千億パラメータのモデルに対応
- **柔軟性**: トレーニング中の混合精度、勾配チェックポイントなどをサポート
- **HuggingFace 統合**: HuggingFace Transformers ライブラリとのシームレスな統合

### 適用場面

- 大規模言語モデル（LLM）のトレーニング
- 数十億パラメータ以上のモデル
- 限られた GPU メモリでの大規模モデルトレーニング

## 2.2 前提条件

::::details インフラストラクチャ要件

- ✅ Amazon SageMaker HyperPod EKS クラスターがデプロイされ稼働中であること
- ✅ 適切なインスタンスタイプの GPU ノードグループ（例: ml.g5.8xlarge、ml.p5en.48xlarge）
- ✅ GPU device plugin がクラスターにインストールされていること
- ✅ EFA device plugin が高性能ネットワーキングのためにインストールされていること
- ✅ Kubeflow Training Operator がクラスターにインストールされていること
::::

::::details 開発環境

- ✅ AWS CLI v2 がインストールされ、適切な権限で設定されていること
- ✅ kubectl がインストールされ、EKS クラスターにアクセスできること
- ✅ Docker がインストールされていること（x86-64 ベース）
- ✅ envsubst ユーティリティ（テンプレート処理用）
- ✅ Git（リポジトリのクローン用）
- ✅ HuggingFace アカウントとトークン（データセットアクセス用）
::::

::::details クラスターの検証

クラスターが準備完了であることを確認します。

```bash
# クラスターの状態と GPU の可用性を確認
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,INSTANCETYPE:.metadata.labels.node\.kubernetes\.io/instance-type,GPU:.status.allocatable.nvidia\.com/gpu,EFA:.status.allocatable.vpc\.amazonaws\.com/efa"

# Kubeflow Training Operator が実行中であることを確認
kubectl get pods -n kubeflow

# GPU device plugin を確認
kubectl get daemonset -n kube-system | grep nvidia

# EFA device plugin を確認
kubectl get daemonset -n kube-system | grep aws-efa

# リソースを作成できることを確認
kubectl auth can-i create pytorchjobs
```
::::

::::details 検証済みインスタンスタイプ

このサンプルは以下のインスタンスタイプで検証されています。

- **ml.p5en.48xlarge × 2** - 高性能トレーニングセットアップ

モデルサイズを調整することで、他のインスタンスタイプにも対応できます。
::::

## 2.3 モデルサイズの設定

::::details Llama モデルサイズ設定表

以下の表は、[Llama 2](https://arxiv.org/abs/2307.09288) および [Llama 3](https://arxiv.org/abs/2407.21783) 論文に基づく、異なる Llama モデルサイズのパラメータを示しています。

| パラメータ | Llama 2 7B | Llama 2 13B | Llama 2 70B | Llama 3.1 8B | Llama 3.1 70B | Llama 3.2 1B | Llama 3.2 3B |
|-----------|-----------|------------|------------|-------------|--------------|-------------|-------------|
| **intermediate_size** | 11008 | 13824 | 28672 | 14336 | 28672 | 8192 | 11008 |
| **num_key_value_heads** | 32 | 40 | 8 | 8 | 8 | 8 | 8 |
| **hidden_width** | 4096 | 5120 | 8192 | 4096 | 8192 | 2048 | 3072 |
| **num_layers** | 32 | 40 | 80 | 32 | 80 | 16 | 28 |
| **num_heads** | 32 | 40 | 64 | 32 | 64 | 32 | 24 |
| **max_context_length** | 4096 | 4096 | 4096 | 8192 | 8192 | 8192 | 8192 |

これらの設定は、計算要件と利用可能なインスタンスタイプに基づいて、トレーニングスクリプトでモデルパラメータを調整するために使用できます。
::::

## 2.4 Docker イメージのセットアップ

:::message
- [ ] 2.4.1 リポジトリのクローン
- [ ] 2.4.2 Docker イメージのビルド
- [ ] 2.4.3 イメージの Amazon ECR へのプッシュ
:::

::::details 2.4.1 リポジトリのクローン

:::message
なんのための作業か: FSDP トレーニングコードと Docker 設定を取得します。
:::

:::message
次のステップに進む条件: `awsome-distributed-training/3.test_cases/pytorch/FSDP` ディレクトリに移動でき、必要なファイルが存在すること。
:::

```bash
cd ~
git clone https://github.com/aws-samples/awsome-distributed-training/
cd awsome-distributed-training/3.test_cases/pytorch/FSDP
```
::::

::::details 2.4.2 Docker イメージのビルド

:::message
なんのための作業か: PyTorch、FSDP トレーニングコード、全ての必要な依存関係を含むコンテナイメージをビルドします。
:::

:::message
次のステップに進む条件: `docker build` コマンドが正常に完了し、"Successfully built" メッセージが表示されること。通常 5 から 7 分かかります。
:::

まず、パブリック ECR レジストリで認証してベースイメージにアクセスします。

```bash
aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws/hpc-cloud
```

環境変数を設定します。

```bash
export REGION=$(aws ec2 describe-availability-zones --output text --query 'AvailabilityZones[0].[RegionName]')
export ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export REGISTRY=${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/
```

コンテナイメージをビルドします。

**Mac を使用している場合は、`buildx` を使用して `linux/amd64` アーキテクチャをターゲットにします:**

```bash
docker buildx build --platform linux/amd64 -t ${REGISTRY}fsdp:pytorch2.5.1 .
```

**SageMaker Studio 環境で実行している場合:**

```bash
docker build $DOCKER_NETWORK -t ${REGISTRY}fsdp:pytorch2.5.1 .
```

ビルドが成功すると、以下のようなメッセージが表示されます。

```
Successfully built 123ab12345cd
Successfully tagged 123456789012.dkr.ecr.us-west-2.amazonaws.com/fsdp:pytorch2.5.1
```
::::

::::details 2.4.3 イメージの Amazon ECR へのプッシュ

:::message
なんのための作業か: コンテナレジストリを作成し、コンテナイメージをプッシュします。
:::

:::message
次のステップに進む条件: `docker push` コマンドが正常に完了すること。EC2/CloudShell を使用する場合、通常 6 から 8 分かかります。
:::

```bash
# レジストリを作成（必要な場合）
REGISTRY_COUNT=$(aws ecr describe-repositories | grep "fsdp" | wc -l)
if [ "$REGISTRY_COUNT" -eq 0 ]; then
    aws ecr create-repository --repository-name fsdp
fi

# レジストリにログイン
echo "Logging in to $REGISTRY ..."
aws ecr get-login-password | docker login --username AWS --password-stdin $REGISTRY

# イメージをレジストリにプッシュ
docker image push ${REGISTRY}fsdp:pytorch2.5.1
```
::::

## 2.5 データと HuggingFace のセットアップ

::::details データセットの理解

:::message
なんのための作業か: トレーニングに使用するデータセットを理解します。
:::

このサンプルでは [allenai/c4](https://huggingface.co/datasets/allenai/c4) データセットを使用します。データセット全体をダウンロードする代わりに、`create_streaming_dataloaders` 関数が [HuggingFace](https://huggingface.co/datasets) からデータセットをストリーミングするため、トレーニング実行のためのデータ準備は不要です。

独自のデータセットを使用したい場合は、[HuggingFace データセットとしてフォーマット](https://huggingface.co/docs/datasets/create_dataset)し、その場所を `--dataset_path` 引数に渡すことができます。
::::

::::details HuggingFace トークンの作成

:::message
なんのための作業か: HuggingFace からデータセットにアクセスするためのアクセストークンを作成します。
:::

:::message
次のステップに進む条件: HuggingFace アクセストークンを取得し、環境変数として設定できること。
:::

このデータセットには HuggingFace アクセストークンが必要です。

1. [HuggingFace アカウント](https://huggingface.co/welcome)を作成します
2. [読み取り権限を持つアクセストークンを生成](https://huggingface.co/docs/hub/en/security-tokens)します

次のステップで、このトークンを環境変数として参照します。
::::

## 2.6 トレーニングの実行

:::message
- [ ] 2.6.1 envsubst のインストール
- [ ] 2.6.2 マニフェストファイルの生成
- [ ] 2.6.3 トレーニングジョブのデプロイ
- [ ] 2.6.4 トレーニングジョブの監視
:::

::::details 2.6.1 envsubst のインストール

envsubst がインストールされているか確認し、必要に応じてインストールします。

インストール手順: https://github.com/a8m/envsubst?tab=readme-ov-file#installation
::::

::::details 2.6.2 マニフェストファイルの生成

:::message
なんのための作業か: クラスター仕様に基づいて Kubernetes マニフェストを生成します。
:::

:::message
次のステップに進む条件: `fsdp.yaml` ファイルが生成され、適切な設定が含まれていること。
:::

クラスターの仕様を確認します。

```bash
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,INSTANCETYPE:.metadata.labels.node\.kubernetes\.io/instance-type,GPU:.status.allocatable.nvidia\.com/gpu,EFA:.status.allocatable.vpc\.amazonaws\.com/efa"
```

出力例は以下のようになります。

```
NAME                           INSTANCETYPE    GPU   EFA
hyperpod-i-055aeff9546187dee   ml.g5.8xlarge   1     1
hyperpod-i-09662f64f615c96f5   ml.g5.8xlarge   1     1
hyperpod-i-099e2a84aba621d52   ml.g5.8xlarge   1     1
hyperpod-i-0a6fea3329235be91   ml.g5.8xlarge   1     1
hyperpod-i-0ac3feb733dc0f00e   ml.g5.8xlarge   1     1
hyperpod-i-0bf7dce836e063fa6   ml.g5.8xlarge   1     1
hyperpod-i-0ddf28f3ff2870f1b   ml.g5.8xlarge   1     1
hyperpod-i-0fe48912b03d2c22e   ml.g5.8xlarge   1     1
```

`kubernetes/` ディレクトリに移動します。

```bash
cd kubernetes/
```

環境変数を設定して envsubst を実行します。

**ml.g5.8xlarge × 8 の場合:**

```bash
export IMAGE_URI=${REGISTRY}fsdp:pytorch2.5.1
export INSTANCE_TYPE=ml.g5.8xlarge
export NUM_NODES=8
export GPU_PER_NODE=1
export EFA_PER_NODE=1
export FI_PROVIDER=efa
export HF_TOKEN=<Your HuggingFace Token>

cat fsdp.yaml-template | envsubst > fsdp.yaml
```
::::

::::details 2.6.3 トレーニングジョブのデプロイ

:::message
なんのための作業か: 生成されたマニフェストファイルを使用してトレーニングワークロードをデプロイします。
:::

:::message
次のステップに進む条件: kubectl コマンドが正常に完了し、PyTorchJob が作成されること。
:::

```bash
kubectl apply -f ./fsdp.yaml
```

以下のメッセージが表示されます。

```
pytorchjob.kubeflow.org/fsdp created
```
::::

::::details 2.6.4 トレーニングジョブの監視

:::message
なんのための作業か: ジョブの状態を確認し、トレーニングの進行状況を監視します。
:::

:::message
次のステップに進む条件: Pod のステータスが "Running" になり、ログからトレーニングの進行状況が確認できること。初回実行時は 3 から 4 分かかります。
:::

ジョブの状態を確認します。

```bash
kubectl get pytorchjob
kubectl get pods
```

初回実行時の出力例は以下のようになります。

```
NAME   STATE     AGE
fsdp   Running   5m

NAME                    READY   STATUS              RESTARTS   AGE
etcd-7787559c74-pw4jp   1/1     Running             0          74s
fsdp-worker-0           0/1     ContainerCreating   0          74s
fsdp-worker-1           0/1     ContainerCreating   0          74s
fsdp-worker-2           0/1     ContainerCreating   0          74s
fsdp-worker-3           0/1     ContainerCreating   0          74s
fsdp-worker-4           0/1     ContainerCreating   0          74s
fsdp-worker-5           0/1     ContainerCreating   0          74s
fsdp-worker-6           0/1     ContainerCreating   0          74s
fsdp-worker-7           0/1     ContainerCreating   0          74s
```

Pod が Running 状態になったら（3 から 4 分後）:

```
NAME                    READY   STATUS    RESTARTS   AGE
etcd-7787559c74-pw4jp   1/1     Running   0          3m43s
fsdp-worker-0           1/1     Running   0          3m43s
fsdp-worker-1           1/1     Running   0          3m43s
fsdp-worker-2           1/1     Running   0          3m43s
fsdp-worker-3           1/1     Running   0          3m43s
fsdp-worker-4           1/1     Running   0          3m43s
fsdp-worker-5           1/1     Running   0          3m43s
fsdp-worker-6           1/1     Running   0          3m43s
fsdp-worker-7           1/1     Running   0          3m43s
```

### マスター Pod の特定

各 Pod はジョブログを生成します。ジョブ初期化中に 1 つの Pod がマスターとして選出され、このマスター Pod のみがトレーニングジョブの進行状況をログに表示します。

現在のマスター Pod を特定するには、以下のコマンドを実行します。

```bash
kubectl logs fsdp-worker-0 | grep master_addr=
```

出力例は以下のようになります。

```
[2024-06-25 22:20:17,556] torch.distributed.elastic.agent.server.api: [INFO]   master_addr=fsdp-worker-1
```

この例では、`fsdp-worker-1` が現在のマスターです。現在のジョブログを確認するには、以下のコマンドを使用します。

```bash
kubectl logs -f fsdp-worker-1
```

出力例は以下のようになります。

```
    :
2024-06-25 22:22:36 I [train.py:102] Batch 0 Loss: 11.63946, Speed: 0.27 samples/sec, lr: 0.000006
2024-06-25 22:22:57 I [train.py:102] Batch 1 Loss: 11.66096, Speed: 0.39 samples/sec, lr: 0.000013
2024-06-25 22:23:17 I [train.py:102] Batch 2 Loss: 11.56659, Speed: 0.40 samples/sec, lr: 0.000019
2024-06-25 22:23:37 I [train.py:102] Batch 3 Loss: 11.14039, Speed: 0.40 samples/sec, lr: 0.000025
    :
```

### GPU 使用率の確認

実行中のコンテナ内で `nvtop` コマンドを実行して GPU 使用率を確認できます。

```bash
kubectl exec -it fsdp-worker-4 -- nvtop
```
::::

::::details 2.6.5 トレーニングの停止

以下のコマンドを使用してトレーニングジョブを停止します。

```bash
kubectl delete -f ./fsdp.yaml
```
::::

## 2.7 HyperPod CLI を使用したトレーニング（代替手段）

`kubectl` の代わりに HyperPod CLI を使用してトレーニングを実行することもできます。

::::details HyperPod CLI による実行手順

:::message
HyperPod CLI がインストールされていない場合は、[HyperPod CLI のインストール](https://awslabs.github.io/ai-on-sagemaker-hyperpod/docs/add-ons/installing-the-hyperpod-cli)を参照してください。
:::

### 環境変数の設定

クラスター仕様を確認します。

```bash
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,INSTANCETYPE:.metadata.labels.node\.kubernetes\.io/instance-type,GPU:.status.allocatable.nvidia\.com/gpu,EFA:.status.allocatable.vpc\.amazonaws\.com/efa"
```

環境変数を設定します。

```bash
export IMAGE_URI=${REGISTRY}fsdp:pytorch2.5.1
export INSTANCE_TYPE=ml.g5.8xlarge
export NUM_NODES=8
export GPU_PER_NODE=1
```

### ジョブ設定ファイルの生成

HyperPod CLI 用のジョブ設定ファイル（`hpcli-fsdp.yaml`）を生成します。

```bash
cat > hpcli-fsdp.yaml << EOL
defaults:
  - override hydra/job_logging: stdout

hydra:
  run:
    dir: .
  output_subdir: null

training_cfg:
  entry_script: /fsdp/train.py
  script_args:
    - --max_context_width: 4096
    - --num_key_value_heads: 32
    - --intermediate_size: 11008
    - --hidden_width: 4096
    - --num_layers: 32
    - --num_heads: 32
    - --model_type: llama_v2
    - --tokenizer: hf-internal-testing/llama-tokenizer
    - --checkpoint_freq: 5000
    - --validation_freq: 500
    - --max_steps: 5000
    - --checkpoint_dir: /checkpoints
    - --dataset: allenai/c4
    - --dataset_config_name: en
    - --resume_from_checkpoint: /checkpoints
    - --train_batch_size: 1
    - --val_batch_size: 1
    - --sharding_strategy: full
    - --offload_activation: 1
  run:
    name: fsdp
    nodes: ${NUM_NODES}
    ntasks_per_node: ${GPU_PER_NODE}

cluster:
  cluster_type: k8s
  instance_type: ${INSTANCE_TYPE}
  cluster_config:
    service_account_name: null
    volumes:
      - volumeName: local
        hostPath: "/mnt/k8s-disks/0"
        mountPath: "/local"
    namespace: kubeflow
    label_selector:
      required:
        sagemaker.amazonaws.com/node-health-status:
          - Schedulable
      preferred:
        sagemaker.amazonaws.com/deep-health-check-status:
          - Passed
      weights:
        - 100
    pullPolicy: Always
    restartPolicy: OnFailure
    annotations:
      sagemaker.amazonaws.com/enable-job-auto-resume: True
      sagemaker.amazonaws.com/job-max-retry-count: 10

base_results_dir: ./result
container: ${IMAGE_URI}

env_vars:
  LOGLEVEL: DEBUG
  TORCH_DISTRIBUTED_DEBUG: DETAIL
  TORCH_NCCL_ENABLE_MONITORING: 1
  TORCH_NCCL_TRACE_BUFFER_SIZE: 20000
  TORCH_NCCL_DUMP_ON_TIMEOUT: 1
  TORCH_NCCL_DEBUG_INFO_TEMP_FILE: /local/nccl_trace_rank_
  PYTORCH_CUDA_ALLOC_CONF: "expandable_segments:True"
  NCCL_DEBUG: INFO
  NCCL_SOCKET_IFNAME: ^lo
  TORCH_NCCL_ASYNC_ERROR_HANDLING: 1
EOL
```

### トレーニングジョブの開始

クラスターに接続します。

```bash
hyperpod connect-cluster --cluster-name ml-cluster
```

トレーニングジョブを開始します。

```bash
hyperpod start-job --config-file ./hpcli-fsdp.yaml
```

出力例は以下のようになります。

```json
{
  "Console URL": "https://us-west-2.console.aws.amazon.com/sagemaker/home?region=us-west-2#/cluster-management/ml-cluster"
}
```

### ジョブの監視

```bash
hyperpod get-job --job-name fsdp -n kubeflow
```

詳細情報が必要な場合は `--verbose` オプションを使用します。

```bash
hyperpod get-job --job-name fsdp -n kubeflow --verbose
```

Pod のリストを表示します。

```bash
hyperpod list-pods --job-name fsdp -n kubeflow
```

Pod のログを表示します。

```bash
hyperpod get-log --job-name fsdp --pod fsdp-worker-0 -n kubeflow
```

### トラブルシューティング

Pod からログが表示されない場合は、`kubectl` を使用して Kubernetes リソースの状態を確認します。

```bash
# PyTorchJob のリスト
kubectl get pytorchjobs -n kubeflow

# PyTorchJob の詳細
kubectl describe pytorchjob fsdp -n kubeflow

# Pod のリスト
kubectl get pods -n kubeflow

# Pod の詳細
kubectl describe pod fsdp-worker-0 -n kubeflow
```

### トレーニングの停止

```bash
hyperpod cancel-job --job-name fsdp -n kubeflow
```

ジョブリストが空であることを確認します。

```bash
hyperpod list-jobs -n kubeflow
```
::::

---

# 3. NVIDIA Megatron-LM

## 3.1 概要

[MegatronLM](https://github.com/NVIDIA/Megatron-LM) は、NVIDIA が開発した大規模言語モデル（LLM）のトレーニング用フレームワークです。テンソル並列化とパイプライン並列化を組み合わせることで、数千億パラメータ規模のモデルを効率的にトレーニングできます。

### 特徴

- **テンソル並列化**: モデルの各層を複数の GPU に分割
- **パイプライン並列化**: モデルの異なる層を異なる GPU に配置
- **3D 並列化**: データ並列、テンソル並列、パイプライン並列の組み合わせ
- **メモリ最適化**: Activation Checkpointing、Sequence Parallelism などの高度な最適化

### 推奨論文

Megatron-LM の理解を深めるために、以下の論文を読むことを推奨します。

- [Megatron-LM: Training Multi-Billion Parameter Language Models Using Model Parallelism](https://arxiv.org/abs/1909.08053)
- [Efficient Large-Scale Language Model Training on GPU Clusters Using Megatron-LM](https://arxiv.org/abs/2104.04473)
- [Reducing Activation Recomputation in Large Transformer Models](https://arxiv.org/pdf/2205.05198)

### 適用場面

- 超大規模言語モデル（数百億〜数千億パラメータ）のトレーニング
- 高性能 GPU クラスターでの効率的なリソース利用
- GPT、BERT、T5 などのトランスフォーマーベースのモデル

## 3.2 前提条件

::::details インフラストラクチャ要件

- ✅ Amazon SageMaker HyperPod EKS クラスターがデプロイされ稼働中であること
- ✅ EFA device plugin と NVIDIA device plugin がデプロイされていること（自動インストール）
- ✅ Docker がインストールされていること（コンテナイメージのビルド用）
- ✅ FSx for Lustre ファイルシステムが `/fsx` に PVC 経由でマウントされていること
- ✅ Kubeflow Training Operator がインストールされ設定されていること

FSx for Lustre のセットアップ例は以下を参照してください。

https://docs.aws.amazon.com/eks/latest/userguide/fsx-csi-create.html
::::

## 3.3 環境の準備

:::message
- [ ] 3.3.1 リポジトリのクローン
- [ ] 3.3.2 コンテナのビルド
- [ ] 3.3.3 計算リソースの決定
:::

::::details 3.3.1 リポジトリのクローン

:::message
なんのための作業か: Dockerfile と Kubernetes マニフェストにアクセスするために、awsome-distributed-training リポジトリをクローンします。
:::

:::message
次のステップに進む条件: `awsome-distributed-training/3.test_cases/megatron/megatron-lm` ディレクトリに移動でき、`aws-megatron-lm.Dockerfile` が存在すること。
:::

```bash
git clone https://github.com/aws-samples/awsome-distributed-training.git
cd awsome-distributed-training/3.test_cases/megatron/megatron-lm
```
::::

::::details 3.3.2 コンテナのビルド

:::message
なんのための作業か: Megatron-LM を含むコンテナイメージをビルドし、ECR にプッシュします。
:::

:::message
次のステップに進む条件: Docker イメージが正常にビルドされ、ECR にプッシュされること。
:::

コンテナイメージをビルドします。

```bash
docker build -t aws-megatron-lm -f aws-megatron-lm.Dockerfile .
```

イメージをタグ付けして ECR にプッシュします。

```bash
export AWS_REGION=us-east-1  # EKS クラスターと ECR リポジトリが存在するリージョンに設定
export ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export REGISTRY=${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com
export ECR_REPOSITORY_NAME=aws-megatron-lm
export REPO_URI=${REGISTRY}/${ECR_REPOSITORY_NAME}:latest

# ECR リポジトリを作成（存在しない場合）
aws ecr describe-repositories --repository-names ${ECR_REPOSITORY_NAME} --region ${AWS_REGION} 2>/dev/null || \
aws ecr create-repository --repository-name ${ECR_REPOSITORY_NAME} --region ${AWS_REGION}

# ECR にログイン
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${REGISTRY}

docker tag ${ECR_REPOSITORY_NAME}:latest ${REGISTRY}/${ECR_REPOSITORY_NAME}:latest
docker push ${REGISTRY}/${ECR_REPOSITORY_NAME}:latest
```
::::

::::details 3.3.3 計算リソースの決定

:::message
なんのための作業か: トレーニング実行前に、EKS クラスターノードで利用可能な計算リソースを決定します。これにより、GPU と EFA（Elastic Fabric Adapter）ネットワークインターフェースの正しいリソース制限を設定できます。
:::

:::message
次のステップに進む条件: インスタンスタイプに応じた環境変数が設定されること。
:::

インスタンスタイプに基づいて環境変数をエクスポートします。

```bash
# ml.p5.48xlarge の例
export INSTANCE_TYPE=ml.p5.48xlarge
export GPU_PER_NODE=8
export EFA_PER_NODE=32
export NUM_NODES=2
```

インスタンスタイプごとの正しい値は以下の表を参照してください。

| インスタンスタイプ | GPU 数 | EFA インターフェース数 |
|------------------|--------|---------------------|
| ml.p5.48xlarge | 8 | 32 |
| ml.p5e.48xlarge | 8 | 32 |
| ml.p5en.48xlarge | 8 | 16 |
| ml.p6-b200.48xlarge | 8 | 8 |
::::

## 3.4 データの前処理

:::message
- [ ] 3.4.1 データのダウンロード
- [ ] 3.4.2 データの前処理
:::

::::details 3.4.1 データのダウンロード

:::message
なんのための作業か: トレーニングに必要なデータセットとボキャブラリーファイルをダウンロードします。
:::

:::message
次のステップに進む条件: ジョブが "Completed" ステータスになり、データが FSx にダウンロードされること。
:::

GPT3 マニフェストディレクトリに移動します。

```bash
cd kubernetes/gpt3
```

データダウンロードジョブのマニフェストを生成して適用します。

```bash
envsubst < manifests/getdata-job.yaml-template > manifests/getdata-job.yaml
kubectl apply -f manifests/getdata-job.yaml
```

ジョブの作成を確認します。

```bash
kubectl get jobs
```

ジョブが管理する Pod を確認します。

```bash
kubectl get pods -l job-name=getdata-job
```

ジョブの詳細を確認します。

```bash
kubectl describe job getdata-job
```

ログをストリーミングしてダウンロードの進行状況を監視します。

```bash
kubectl logs -f job/getdata-job
```

ダウンロードが正常に完了すると、以下のような出力が表示されます。

```
...
Saving to: 'gpt2-merges.txt'

 0K .......... .......... .......... .......... .......... 11% 19.2M 0s
50K .......... .......... .......... .......... .......... 22% 55.9M 0s
100K .......... .......... .......... .......... .......... 33% 57.3M 0s
...
total 940M
drwxr-xr-x 2 root root   33K Jun 20 09:00 .
drwxr-xr-x 5 root root   33K Jun 20 08:59 ..
-rw-r--r-- 1 root root  446K Feb 18  2019 gpt2-merges.txt
-rw-r--r-- 1 root root 1018K Feb 18  2019 gpt2-vocab.json
-rw-r--r-- 1 root root  1.1G Jul 24  2021 oscar-1GB.jsonl
Download completed.
```

ジョブが完了したら、ジョブと Pod を削除します。

```bash
kubectl delete -f manifests/getdata-job.yaml
```
::::

::::details 3.4.2 データの前処理

:::message
なんのための作業か: ダウンロードしたデータをトレーニング用に変換する前処理ジョブを実行します。
:::

:::message
次のステップに進む条件: ジョブが "Completed" ステータスになり、前処理が完了すること。
:::

前処理ジョブを起動します。

```bash
cat manifests/prepdata-job.yaml-template | envsubst > manifests/prepdata-job.yaml
kubectl apply -f ./manifests/prepdata-job.yaml
```

`prepdata-job` の Pod を確認します。

```bash
kubectl get pods -l job-name=prepdata-job
```

ログをストリーミングしてジョブの進行状況を監視します。

```bash
kubectl logs -f job/prepdata-job
```

前処理が正常に完了すると、以下のような出力が表示されます。

```
...
-rw-r--r--  1 root root 3.4K Jun 14 02:55 pretrain_vision_classify.py
-rw-r--r--  1 root root 3.5K Jun 14 02:55 pretrain_vision_dino.py
-rw-r--r--  1 root root 4.8K Jun 14 02:55 pretrain_vision_inpaint.py
-rw-r--r--  1 root root 8.2K Jun 14 02:55 pretrain_vlm.py
-rw-r--r--  1 root root  824 Jun 14 02:55 pyproject.toml
-rw-r--r--  1 root root 4.0K Jun 14 02:55 setup.py
drwxr-xr-x  8 root root  200 Jun 14 02:55 tasks
drwxr-xr-x  4 root root   67 Jun 14 02:55 tests
drwxr-xr-x  6 root root 4.0K Jun 14 02:55 tools
Data preprocessing completed.
```

ジョブが完了したら、ジョブと Pod を削除します。

```bash
kubectl delete -f manifests/prepdata-job.yaml
```
::::

## 3.5 分散トレーニング

:::message
- [ ] 3.5.1 トレーニングジョブの起動
- [ ] 3.5.2 トレーニングの監視
:::

::::details 3.5.1 トレーニングジョブの起動

:::message
なんのための作業か: データの前処理が完了したので、Megatron-LM を使用して GPT3 モデルの事前トレーニングを実行します。
:::

:::message
次のステップに進む条件: PyTorchJob が正常に作成され、Pod が起動すること。
:::

環境変数を設定して PyTorchJob を起動します。

```bash
export TENSOR_PARALLEL=8
export PIPELINE_PARALLEL=1
export NUM_LAYERS=36
export HIDDEN_SIZE=4096
export NUM_ATTENTION_HEADS=32
export SEQ_LENGTH=2048
export MAX_POSITION_EMBEDDINGS=2048
export MICRO_BATCH_SIZE=1
export GLOBAL_BATCH_SIZE=288

cat manifests/pytorchjob.yaml-template | envsubst > manifests/pytorchjob.yaml
kubectl apply -f ./manifests/pytorchjob.yaml
```

トレーニングが開始されます。

```bash
kubectl get pods
```

出力例は以下のようになります。

```
NAME                    READY   STATUS      RESTARTS   AGE
etcd-7787559c74-wpcb9   1/1     Running     0          3m10s
megatron-worker-0       1/1     Running     0          3m10s
```
::::

::::details 3.5.2 トレーニングの監視

:::message
なんのための作業か: トレーニングの進行状況を確認し、正常に動作していることを検証します。
:::

:::message
次のステップに進む条件: ログにイテレーション情報が表示され、トレーニングが進行していることが確認できること。
:::

ログを確認します。

```bash
kubectl logs -f megatron-worker-0
```

出力例は以下のようになります（抜粋）。

```
...
using torch.float16 for parameters ...
------------------------ arguments ------------------------
accumulate_allreduce_grads_in_fp32 .............. False
adam_beta1 ...................................... 0.9
adam_beta2 ...................................... 0.95
...
-------------------- end of arguments ---------------------
setting number of micro-batches to constant 288
> building GPT2BPETokenizer tokenizer ...
> padded vocab (size: 50257) with 943 dummy tokens (new size: 51200)
> initializing torch distributed ...
> initialized tensor model parallel with size 8
> initialized pipeline model parallel with size 1
> setting random seeds to 1234 ...
> compiling dataset index builder ...
...
time to initialize megatron (seconds): 15.424
[after megatron is initialized] datetime: 2024-07-16 22:14:01
building GPT model ...
> number of parameters on (tensor, pipeline) model parallel rank (4, 0): 941594624
...
> building train, validation, and test datasets ...
> datasets target sizes (minimum size):
   train:      146484375
   validation: 5863680
   test:       11520
...
iteration        1/  508626 | consumed samples:          288 | elapsed time per iteration (ms): 255940.5 | learning rate: 0.000E+00 | global batch size:   288 | loss scale: 4294967296.0 | number of skipped iterations:   1 | number of nan iterations:   0 |
iteration        2/  508626 | consumed samples:          576 | elapsed time per iteration (ms): 243438.3 | learning rate: 0.000E+00 | global batch size:   288 | loss scale: 2147483648.0 | number of skipped iterations:   1 | number of nan iterations:   0 |
iteration        3/  508626 | consumed samples:          864 | elapsed time per iteration (ms): 243344.4 | learning rate: 0.000E+00 | global batch size:   288 | loss scale: 1073741824.0 | number of skipped iterations:   1 | number of nan iterations:   0 |
...
```

トレーニングジョブを停止するには、以下のコマンドを実行します。

```bash
kubectl delete -f ./manifests/pytorchjob.yaml
```
::::

## 3.6 モデルサイズの変更

::::details GPT モデルサイズ設定表

:::message
なんのための作業か: 異なるサイズの GPT モデルをトレーニングするための設定を理解します。
:::

このサンプルは、MegatronLM の[リポジトリ](https://github.com/NVIDIA/Megatron-LM/blob/main/examples/pretrain_gpt.sh)の GPT3 サンプルに基づいています。[Efficient Large-Scale Language Model Training on GPU Clusters Using Megatron-LM](https://arxiv.org/abs/2104.04473) の文書（8 ページ、表 1）に基づいて、`NUM_ATTENTION_HEADS`、`NUM_LAYERS`、`HIDDEN_SIZE` を変更することでモデルサイズを変更できます。

PyTorchJob を適用する前に環境変数を設定することで、異なるモデルサイズのトレーニングを起動できます。

| モデルサイズ | パラメータ設定 |
|------------|--------------|
| 1.7B | `NUM_ATTENTION_HEADS=24 HIDDEN_SIZE=2304 NUM_LAYERS=24` |
| 3.6B | `NUM_ATTENTION_HEADS=32 HIDDEN_SIZE=3072 NUM_LAYERS=30` |
| 7.5B | `NUM_ATTENTION_HEADS=32 HIDDEN_SIZE=4096 NUM_LAYERS=36` |
| 18.4B | `NUM_ATTENTION_HEADS=48 HIDDEN_SIZE=6144 NUM_LAYERS=40` |
| 39.1B | `NUM_ATTENTION_HEADS=64 HIDDEN_SIZE=8192 NUM_LAYERS=48` |
| 76.1B | `NUM_ATTENTION_HEADS=80 HIDDEN_SIZE=10240 NUM_LAYERS=60` |
| 145.6B | `NUM_ATTENTION_HEADS=96 HIDDEN_SIZE=12288 NUM_LAYERS=80` |
| 310.1B | `NUM_ATTENTION_HEADS=128 HIDDEN_SIZE=16384 NUM_LAYERS=96` |

例: 64 層、8192 の隠れサイズ、48 のアテンションヘッドでトレーニングを起動する場合

```bash
export NUM_LAYERS=64
export HIDDEN_SIZE=8192
export NUM_ATTENTION_HEADS=48

cat manifests/pytorchjob.yaml-template | envsubst > manifests/pytorchjob.yaml
kubectl apply -f ./manifests/pytorchjob.yaml
```
::::

::::details ベンチマークモードとトレーニングステップの調整

### ベンチマークモードの有効化

ベンチマークモード（トレーニングのみ、検証とテストなし）で実行するには、`pytorchjob.yaml-template` ファイルの PyTorchJob 引数を変更します。

```diff
-        --eval-iters 40 \
-        --eval-interval 1000 \
-        --split 98,2,0 \
+        --eval-iters 0 \
+        --split 100,0,0 \
```

### トレーニングステップの直接指定

デフォルトでは、PyTorchJob はサンプル数を指定し、トレーニングステップ数は `--train_samples` / `--global-batch-size` に等しくなります。ステップ数を直接指定するには、`pytorchjob.yaml-template` ファイルの引数を変更します。`samples` と `iters` は相互に排他的であることに注意してください。

```diff
-        --train-samples 146484375 \
-        --lr-decay-samples 126953125 \
-        --lr-warmup-samples 183105 \
+        --train-iters 50 \
+        --lr-decay-iters 45 \
+        --lr-warmup-iters 2 \
```

同じパターンで、他のモデルもトレーニングできます。Bert、ICT、T5 などのモデルの事前トレーニングスクリプトは、既に `/workspace/Megatron-LM` の Megatron-LM コンテナに含まれています。
::::

---

# 4. LoRA (Low-Rank Adaptation) ファインチューニング

## 4.1 概要

Low-Rank Adaptation（LoRA）は、大規模言語モデルを効率的にファインチューニングするためのパラメータ効率的な手法です。モデルの全パラメータを更新する代わりに、低ランク行列を学習することで、少ない計算リソースとメモリでファインチューニングを実現します。

### 特徴

- **パラメータ効率**: 学習するパラメータ数を 0.1% 程度に削減
- **メモリ効率**: フルファインチューニングに比べて大幅にメモリ使用量を削減
- **柔軟性**: 複数のタスクに対して異なる LoRA アダプターを作成可能
- **高速**: トレーニング時間とコストを大幅に削減

### 適用場面

- 限られた計算リソースでのファインチューニング
- 複数のタスクや顧客向けのカスタマイズモデル作成
- 頻繁なモデル更新が必要な場合

## 4.2 前提条件

::::details インフラストラクチャ要件

- ✅ Amazon SageMaker HyperPod EKS クラスターに少なくとも 1 つの Trainium インスタンスグループ（ml.trn1.32xlarge / ml.trn1n.32xlarge）があること
- ✅ Neuron device plugin がクラスターにデプロイされていること
- ✅ EFA device plugin がデプロイされていること
- ✅ Kubeflow Training Operator がデプロイされていること
- ✅ FSx for Lustre ファイルシステムが PVC 経由でセットアップされていること

詳細は以下を参照してください。

https://awslabs.github.io/ai-on-sagemaker-hyperpod/docs/getting-started/install-pre-requisites
::::

::::details 開発環境

- ✅ x86-64 ベースの開発環境に Docker がインストールされていること
- ✅ HuggingFace アカウントと HF_ACCESS_TOKEN（[llama 3.1](https://huggingface.co/meta-llama/Meta-Llama-3.1-8B) はゲートモデルのため）

:::message alert
**Apple Silicon Mac について**

Apple Silicon（M1、M2 など）を搭載した Mac は ARM ベースであり、x86-64 ベースではありません。この場合、SageMaker Code Editor を使用できます。
:::
::::

::::details 検証済みインスタンスタイプ

- ml.trn1.32xlarge × (1, 2)
- ml.trn1n.32xlarge × (1, 2)
::::

## 4.3 環境のセットアップ

:::message
- [ ] 4.3.1 FSx for Lustre のセットアップ
- [ ] 4.3.2 Docker イメージのビルドとプッシュ
:::

::::details 4.3.1 FSx for Lustre のセットアップ

:::message
なんのための作業か: トークナイズされたデータとトレーニングチェックポイントを保存するための FSx for Lustre PVC をセットアップします。
:::

:::message
次のステップに進む条件: PVC のステータスが "Bound" になり、テスト Pod からマウントできること。
:::

FSx for Lustre CSI ドライバーをインストールします（インストール手順は前章を参照）。

動的プロビジョニングを使用して、`kubeflow` 名前空間に `fsx-claim` ストレージクレームを使用する PVC を作成します。

```bash
cat <<EOF> pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: fsx-claim
  namespace: kubeflow
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: fsx-sc
  resources:
    requests:
      storage: 1200Gi
EOF

kubectl apply -f pvc.yaml
```

PVC の状態を確認します。

```bash
kubectl describe pvc fsx-claim -n kubeflow
```

PVC のステータスが `Bound` になるまで待機します（通常 10 分程度）。

ボリュームをコンテナにマウントします。

```bash
cat <<EOF> pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: fsx-app
  namespace: kubeflow
spec:
  containers:
  - name: app
    image: ubuntu
    command: ["/bin/sh"]
    args: ["-c", "while true; do echo $(date -u) >> /data/out.txt; sleep 5; done"]
    volumeMounts:
    - name: persistent-storage
      mountPath: /data
  volumes:
  - name: persistent-storage
    persistentVolumeClaim:
      claimName: fsx-claim
EOF

kubectl apply -f pod.yaml
```
::::

::::details 4.3.2 Docker イメージのビルドとプッシュ

:::message
なんのための作業か: Optimum Neuron を含む Docker イメージをビルドし、ECR にプッシュします。
:::

:::message
次のステップに進む条件: Docker イメージが正常にビルドされ、ECR にプッシュされること。
:::

ECR にログインして、`huggingface-pytorch-training-neuronx` イメージをプルします。

```bash
region=us-east-1
dlc_account_id=763104351884
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $dlc_account_id.dkr.ecr.$region.amazonaws.com
docker pull ${dlc_account_id}.dkr.ecr.${region}.amazonaws.com/huggingface-pytorch-training-neuronx:2.1.2-transformers4.43.2-neuronx-py310-sdk2.20.0-ubuntu20.04-v1.0
```

x86-64 ベースの開発環境で、リポジトリをクローンします。

```bash
cd ~
git clone https://github.com/Captainia/awsome-distributed-training.git
git checkout optimum-neuron-eks
cd 3.test_cases/pytorch/optimum-neuron/llama3/kubernetes/fine-tuning
```

Docker イメージをビルドしてプッシュします。

```bash
export AWS_REGION=$(aws ec2 describe-availability-zones --output text --query 'AvailabilityZones[0].[RegionName]')
export ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export REGISTRY=${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/
export IMAGE=peft-optimum-neuron
export TAG=:latest

docker build --network=sagemaker -t ${REGISTRY}${IMAGE}${TAG} .

# レジストリを作成（必要な場合）
export REGISTRY_COUNT=$(aws ecr describe-repositories | grep \"${IMAGE}\" | wc -l)
if [ "${REGISTRY_COUNT//[!0-9]/}" == "0" ]; then
    echo "Creating repository ${REGISTRY}${IMAGE} ..."
    aws ecr create-repository --repository-name ${IMAGE}
else
    echo "Repository ${REGISTRY}${IMAGE} already exists"
fi

# レジストリにログイン
echo "Logging in to $REGISTRY ..."
aws ecr get-login-password | docker login --username AWS --password-stdin $REGISTRY

# イメージをプッシュ
docker image push ${REGISTRY}${IMAGE}${TAG}
```
::::

## 4.4 トレーニングの実行

:::message
- [ ] 4.4.1 ジョブ仕様ファイルの生成
- [ ] 4.4.2 データのトークナイズ
- [ ] 4.4.3 モデルのコンパイル
- [ ] 4.4.4 ファインチューニングの実行
- [ ] 4.4.5 ウェイトの統合とマージ
:::

::::details 4.4.1 ジョブ仕様ファイルの生成

:::message
なんのための作業か: トークナイズとトレーニング用のジョブ仕様ファイルを生成します。
:::

:::message
次のステップに進む条件: `tokenize_data.yaml` と `llama3_train.yaml` ファイルが生成されること。
:::

`./generate-jobspec.sh` スクリプトを編集して、環境設定を更新します。特に `HF_ACCESS_TOKEN` を設定する必要があります。

```bash
./generate-jobspec.sh
```

このスクリプトは、デフォルトで 8B Llama 3.1 モデル用の設定で 2 つの YAML ファイル（`tokenize_data.yaml` と `llama3_train.yaml`）を作成します。
::::

::::details 4.4.2 データのトークナイズ

:::message
なんのための作業か: HuggingFace Hub から [wikicorpus](https://huggingface.co/datasets/gboleda/wikicorpus) データセットをダウンロードし、トークナイズして FSx Lustre に保存します。
:::

:::message
次のステップに進む条件: トークナイズジョブが完了し、データが FSx に保存されること。
:::

```bash
kubectl apply -f ./tokenize_data.yaml
```

ジョブの進行状況を監視します。

```bash
kubectl get jobs
kubectl logs -f job/tokenize-data
```
::::

::::details 4.4.3 モデルのコンパイル

:::message
なんのための作業か: Trainium でのトレーニングには、`neuron_parallel_compile` ユーティリティを使用したモデルコンパイルが必要です。
:::

:::message
次のステップに進む条件: コンパイルジョブが完了すること。
:::

コンパイルプロセスは以下を実行します。

- 試行実行（約 10 トレーニングステップ）から計算グラフを抽出
- これらのグラフの並列事前コンパイルを実行
- 実際のトレーニングと同一のスクリプトを使用するが、max_steps を削減
- Trainium ハードウェア上での効率的な実行のためにモデルを準備

```bash
kubectl apply -f ./compile_peft.yaml
```

コンパイルの進行状況を監視します。

```bash
kubectl get jobs
kubectl logs -f job/compile-peft
```
::::

::::details 4.4.4 ファインチューニングの実行

:::message
なんのための作業か: 前のステップでトークナイズしたデータを使用して、Llama 3.1 8B モデルをファインチューニングします。
:::

:::message
次のステップに進む条件: トレーニングジョブが起動し、ログからトレーニングの進行状況が確認できること。
:::

`launch_peft_train.yaml` ジョブ仕様ファイルは、前のステップのトークナイズされたデータを使用して Llama 3.1 8B モデルをファインチューニングします。デフォルトでは 1 つの ml.trn1.32xlarge を使用しますが、任意のノード数に変更できます。

```bash
kubectl apply -f ./launch_peft_train.yaml
```

トレーニングプロセスの特徴は以下の通りです。

- 次数 8 のテンソル並列化を使用し、ml.trn1.32xlarge インスタンスの全 32 NeuronCores を活用
- データ並列度 4
- BFloat16 精度（XLA_USE_BF16=1）によるメモリフットプリントの削減
- より大きい有効バッチサイズのための勾配累積ステップ 3
- LoRA 設定
  - r=16（ランク）
  - lora_alpha=16
  - lora_dropout=0.05
  - ターゲットモジュール: q_proj と v_proj

トレーニングの進行状況を監視します。

```bash
kubectl get pytorchjobs -n kubeflow
kubectl get pods -n kubeflow
kubectl logs -f <pod-name> -n kubeflow
```
::::

::::details 4.4.5 ウェイトの統合とマージ

:::message
なんのための作業か: 分散トレーニングのチェックポイントを統合し、LoRA アダプターをベースモデルにマージします。
:::

:::message
次のステップに進む条件: 統合とマージジョブが完了し、最終的なモデルが safetensor 形式で保存されること。
:::

### ウェイトの統合

分散トレーニング中、モデルチェックポイントは複数のデバイスに分割されます。統合プロセスは以下を実行します。

- 分散チェックポイントを統一されたモデルに結合
- メモリ効率的なチャンクでテンソルを処理
- インデックスファイル付きのシャード出力を作成
- safetensor 形式で統合されたウェイトを保存

```bash
kubectl apply -f ./consolidation.yaml
```

### LoRA ウェイトのマージ

最終ステップは、LoRA アダプターをベースモデルにマージします。

```bash
kubectl apply -f ./merge_lora.yaml
```

このプロセスは以下を実行します。

- ベースモデルと LoRA 設定を読み込み
- LoRA ウェイト名をベースモデル構造に合わせて変換
- アダプターを元のモデルウェイトにマージ
- 最終モデルをシャード形式で保存

結果としてマージされたモデルは、ベースモデルの知識とファインチューニング中に学習されたタスク固有の適応を組み合わせ、LoRA トレーニングの効率性の利点を維持します。
::::

---

# まとめ

本章では、Amazon SageMaker HyperPod EKS で利用可能な主要な分散学習およびファインチューニング手法を解説しました。

## 各手法の特徴の再確認

**DDP (Distributed Data Parallel)**
- PyTorch 標準のデータ並列化手法として、実装が容易で幅広い環境で動作します。小〜中規模モデルのトレーニングに最適です。

**FSDP (Fully Sharded Data Parallel)**
- メモリ効率的な大規模モデルトレーニングを実現します。モデルの状態を全デバイスに分散することで、数十億パラメータのモデルもトレーニング可能です。

**NVIDIA Megatron-LM**
- テンソル並列とパイプライン並列を組み合わせた 3D 並列化により、数千億パラメータの超大規模モデルをトレーニングできます。高度な最適化技術により、最高レベルのメモリ効率を実現します。

**LoRA (Low-Rank Adaptation)**
- パラメータ効率的なファインチューニング手法として、学習するパラメータ数を大幅に削減します。限られたリソースで効率的にモデルをカスタマイズできます。

## 次のステップ

実際のトレーニングワークロードを実行する際は、以下の点に注意してください。

1. **モデルサイズとハードウェアの適切なマッチング**: モデルのパラメータ数に応じて適切なインスタンスタイプとノード数を選択します
2. **ストレージの最適化**: FSx for Lustre の適切な設定により、データ I/O のボトルネックを回避します
3. **モニタリングとログ**: トレーニング中の GPU 使用率、メモリ使用量、ネットワーク帯域幅を監視します
4. **チェックポイント戦略**: 適切な頻度でチェックポイントを保存し、障害からの復旧を可能にします

次章では、HyperPod のアドオン機能（オブザーバビリティ、タスクガバナンスなど）について解説します。
