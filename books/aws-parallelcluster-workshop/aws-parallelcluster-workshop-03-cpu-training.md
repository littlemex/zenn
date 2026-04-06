---
title: "Basic01: PyTorch DDP on CPU"
free: true
---

本章では、作成したクラスターで実際にトレーニングジョブを実行してみましょう。このワークショップは CPU で動作するように構築されています。

PyTorch DDP で実際に分散学習の仕組みを理解していきましょう。以下に DDP (Distributed Data Parallel) について解説してありますので参考にしてください。

https://zenn.dev/tosshi/books/ml-distributed-experiment-collection/viewer/transformer-distributed-parallelism#1.-data-parallel-(dp)

CPU インスタンスでの分散学習は本番の GPU 学習と同じ分散処理の概念を学ぶのに最適です。

# 解説

実行するバッチジョブのコードについて解説しておきましょう。

::::details sbatch スクリプト
```bash
#!/bin/bash
#SBATCH --job-name=ddp-venv
#SBATCH --exclusive
#SBATCH --wait-all-nodes=1
#SBATCH --nodes 2
#SBATCH --output=logs/%x_%j.out # logfile for stdout/stderr

export LOGLEVEL=INFO

declare -a TORCHRUN_ARGS=(
    --nproc_per_node=1     # For GPU: Set this to number of GPUs per node
    --nnodes=$SLURM_JOB_NUM_NODES
    --rdzv_id=$SLURM_JOB_ID
    --rdzv_backend=c10d
    --rdzv_endpoint=$(hostname)
)

declare -a TRAIN_ARGS=(
    --total_epochs 500
    --save_every 1
    --batch_size 32
    --checkpoint_path ./snapshot.pt
    --use_mlflow
)

AUTO_RESUME=""
if [ -d "/opt/sagemaker_cluster" ]; then
    echo "Detected Hyperpod cluster.. enabling --auto-resume=1"
    AUTO_RESUME="--auto-resume=1"
fi

srun ${AUTO_RESUME} ./pt/bin/torchrun \
    "${TORCHRUN_ARGS[@]}" \
    $(dirname "$PWD")/ddp.py ${TRAIN_ARGS[@]}
```
::::

## 分散学習のための Slurm バッチスクリプト

このスクリプトは、PyTorch の分散学習を Slurm クラスター上で実行するための実践的な例です。前回説明した `srun` と `torchrun` の組み合わせが、実際にどのように使われるかを示しています。

### リソース要求の宣言

```bash
#SBATCH --job-name=ddp-venv
#SBATCH --exclusive
#SBATCH --wait-all-nodes=1
#SBATCH --nodes 2
#SBATCH --output=logs/%x_%j.out
```

スクリプトの冒頭にある `#SBATCH` ディレクティブは、Slurm に対するリソース要求です。通常のコメント記号 `#` で始まっていますが、`sbatch` コマンドはこれらを特別に解釈します。

`--exclusive` オプションは、ノード全体を占有することを意味します。他のジョブとノードを共有せず、すべての CPU コアとメモリをこのジョブ専用にします。分散学習では、ノード間の通信がパフォーマンスの鍵を握るため、他のジョブによる干渉を避けるためにこのオプションが推奨されます。

`--wait-all-nodes=1` は重要な設定です。これは、すべてのノードが完全に起動し準備完了になるまで、ジョブの開始を待つことを意味します。分散学習では、すべてのプロセスが同時に通信を開始する必要があります。一部のノードだけが先に実行を開始すると、他のノードを待つタイムアウトエラーが発生する可能性があります。

`--output=logs/%x_%j.out` は、標準出力と標準エラーをファイルに保存する設定です。

### torchrun の引数設定

```bash
declare -a TORCHRUN_ARGS=(
    --nproc_per_node=1
    --nnodes=$SLURM_JOB_NUM_NODES
    --rdzv_id=$SLURM_JOB_ID
    --rdzv_backend=c10d
    --rdzv_endpoint=$(hostname)
)
```

bash の配列として `torchrun` の引数を定義しています。これにより `"${TORCHRUN_ARGS[@]}"` として展開でき、可読性が向上します。

`--nproc_per_node=1` は、各ノードで起動する GPU プロセスの数です。コメントに「For GPU: Set this to number of GPUs per node」とあるように、GPU を使う場合はノードあたりの GPU 数に合わせます。このスクリプトは CPU 版の例なので 1 に設定されています。

`--nnodes=$SLURM_JOB_NUM_NODES` は、Slurm が割り当てたノード数を自動的に取得します。`#SBATCH --nodes 2` で要求したノード数がこの変数に格納されています。

`--rdzv_id` と `--rdzv_backend`、`--rdzv_endpoint` は、PyTorch の Rendezvous（ランデブー）メカニズムの設定です。Rendezvous は分散学習の開始時に、すべてのプロセスが互いを発見し、通信グループを形成するための仕組みです。

`--rdzv_backend=c10d` は、PyTorch の C10D バックエンドを使用することを意味します。C10D は PyTorch の分散通信ライブラリで、内部で TCP ソケットを使って Rendezvous を実装します。

`--rdzv_endpoint=$(hostname)` は、Rendezvous サーバーのアドレスです。`hostname` コマンドは現在のノード（Slurm が最初のタスクを実行するノード）のホスト名を返します。このノードが Rendezvous サーバーとして機能し、他のノードがここに接続して情報を交換します。

https://zenn.dev/tosshi/articles/be0db364a7f8e2#rendezvous-%E3%83%A1%E3%82%AB%E3%83%8B%E3%82%BA%E3%83%A0

Rendezvous については上記に軽くまとめてあります。

### 学習の引数設定

```bash
declare -a TRAIN_ARGS=(
    --total_epochs 500
    --save_every 1
    --batch_size 32
    --checkpoint_path ./snapshot.pt
    --use_mlflow
)
```

これらは学習スクリプト（`ddp.py`）に渡される引数です。Slurm や torchrun の設定ではなく、アプリケーション固有のパラメータです。

`--total_epochs 500` は学習のエポック数、`--batch_size 32` はミニバッチサイズです。`--checkpoint_path ./snapshot.pt` はチェックポイントの保存先を指定しています。

`--use_mlflow` は、MLflow を使って学習のメトリクスを記録することを示しています。MLflow は機械学習の実験管理ツールで、損失、精度、ハイパーパラメータなどを自動的に記録・可視化できます。

### Hyperpod の自動検出

```bash
AUTO_RESUME=""
if [ -d "/opt/sagemaker_cluster" ]; then
    echo "Detected Hyperpod cluster.. enabling --auto-resume=1"
    AUTO_RESUME="--auto-resume=1"
fi
```

この部分は、AWS SageMaker Hyperpod クラスター上で実行されているかを検出します。

Hyperpod は AWS が提供するマネージド型の分散学習基盤で、数週間から数ヶ月にわたる長時間学習をサポートします。`--auto-resume=1` は、ノード障害が発生した際に自動的にジョブを再開する機能です。

通常の AWS ParallelCluster では、この条件は満たされないため、`AUTO_RESUME` 変数は空文字列のままです。

### 実行コマンドの構築

```bash
srun ${AUTO_RESUME} ./pt/bin/torchrun \
    "${TORCHRUN_ARGS[@]}" \
    $(dirname "$PWD")/ddp.py ${TRAIN_ARGS[@]}
```

最後の行が実際の実行コマンドです。`srun` が各ノードで `torchrun` を起動し、`torchrun` が学習スクリプト `ddp.py` を実行します。

`${AUTO_RESUME}` は Hyperpod 上でのみ `--auto-resume=1` に展開され、それ以外では空文字列なので無視されます。bash の変数展開の便利な使い方です。

`./pt/bin/torchrun` は、Python 仮想環境内の `torchrun` を明示的に指定しています。

`"${TORCHRUN_ARGS[@]}"` は、先ほど定義した配列を展開します。ダブルクォートで囲むことで、スペースを含む引数も正しく扱われます。

`$(dirname "$PWD")/ddp.py` は、現在のディレクトリの親ディレクトリにある `ddp.py` を指定しています。スクリプトがサブディレクトリから実行されることを想定した設定です。

### 実行時の動作

このスクリプトを `sbatch` で投入すると、以下のように動作します。

1. **Slurm がノードを割り当て**: `--nodes 2` で要求した 2 台のノードを確保
2. **すべてのノードが起動完了を待機**: `--wait-all-nodes=1` により同期
3. **srun が各ノードで torchrun を起動**: 各ノードで 1 回ずつ実行
4. **torchrun が Rendezvous で同期**: 全プロセスが揃うまで待機
5. **各プロセスに Rank を割り当て**: Rank 0, 1 を各ノードに割り当て
6. **学習スクリプト ddp.py を実行**: 各プロセスが並列に学習を開始
7. **チェックポイントを定期的に保存**: `--save_every 1` で毎エポック保存
8. **ログファイルに結果を出力**: `logs/ddp-venv_42.out`

この一連の流れにより、複数ノードにまたがる分散学習が自動的にオーケストレーションされます。研究者はスクリプトを投入するだけで、残りは Slurm と PyTorch が連携して処理します。

# ワークショップ実施

:::message
実はここまでであらかた基礎的な内容は説明してしまったのであとは実践あるのみです。
:::

https://catalog.workshops.aws/ml-on-aws-parallelcluster/en-US/04-train-cpu/07-pytorch-ddp-on-cpu

:::message alert
公式ワークショップ手順 [[こちら](https://catalog.workshops.aws/ml-on-aws-parallelcluster/en-US/04-train-cpu/07-pytorch-ddp-on-cpu)] を正として進めてください。
:::

ヘッドノードで以下を実行します。

```bash
cd ~
git clone https://github.com/aws-samples/awsome-distributed-training.git && cd awsome-distributed-training/3.test_cases/pytorch/ddp/slurm/
```

## Python venv Environment

コンテナを使わずに PyTorch DDP のジョブを投入します。まずは以下のスクリプトで Python venv と PyTorch 環境を作成します。

```bash
bash 0.create-venv.sh
```

このスクリプトは `./pt/` ディレクトリに venv 環境を作成し、PyTorch と依存ライブラリをインストールします。セットアップには数分かかります。

```bash
JOB_ID=$(sbatch -p cpu 1.venv-train.sbatch | awk '{print $4}')
echo "Submitted job: $JOB_ID"

squeue
```

```bash
tail -f logs/ddp-venv_${JOB_ID}.out
```

学習が正常に進むと、各ランクからのエポック情報が表示されます。

```text
[RANK 3] Epoch 49 | Batchsize: 32 | Steps: 8
[RANK 5] Epoch 49 | Batchsize: 32 | Steps: 8
[RANK 4] Epoch 49 | Batchsize: 32 | Steps: 8
[RANK 0] Epoch 49 | Batchsize: 32 | Steps: 8
[RANK 7] Epoch 49 | Batchsize: 32 | Steps: 8
[RANK 1] Epoch 49 | Batchsize: 32 | Steps: 8
[RANK 6] Epoch 49 | Batchsize: 32 | Steps: 8
[RANK 2] Epoch 49 | Batchsize: 32 | Steps: 8
```

## Container

**Enroot コンテナイメージの作成のコマンド例**

```bash
enroot import --output pytorch.sqsh docker://pytorch/pytorch:2.4.1-cuda11.8-cudnn9-runtime
```

このコマンドは `pytorch/pytorch` コンテナイメージを Squashfs 形式に変換して `pytorch.sqsh` として保存します。

:::message
Squashfs（`.sqsh`）ファイルは読み取り専用の圧縮ファイルシステムイメージです。多数のコンピュートノードで同じイメージを使用する際、FSx for Lustre に一度置けば全ノードから高速に参照できます。
:::

以下のコマンドを実行しましょう。

```bash
bash 2.create-enroot-image.sh
```

**コンテナを使ったジョブの投入**

```bash
export NPROC_PER_NODE=1
JOB_ID=$(sbatch -p cpu 3.container-train.sbatch | awk '{print $4}')
echo "Submitted job: $JOB_ID"
```

ログの確認:

```bash
tail -f logs/cpu-ddp-container_*.out
```

Conda 環境と同じ形式の出力が得られます。

```text
[RANK 3] Epoch 49 | Batchsize: 32 | Steps: 8
[RANK 5] Epoch 49 | Batchsize: 32 | Steps: 8
[RANK 4] Epoch 49 | Batchsize: 32 | Steps: 8
```

## ジョブのキャンセル

```bash
scancel $JOB_ID
```

:::message
学習の動作を確認したら `scancel` でジョブを終了させてください。コンピュートノードはジョブ終了後 10 分（デフォルトの ScaledownIdletime）で自動停止しますが、早めに終了させることでコストを抑えられます。
:::

# まとめ

本章では以下を実施しました。

- `awsome-distributed-training` リポジトリをクローンし、PyTorch DDP テストケースのディレクトリに移動
- venv 環境を使って PyTorch DDP 学習ジョブを CPU クラスターで実行
- Enroot コンテナを使った別の実行方法も確認
- `squeue`・`sacct`・`scancel` によるジョブ管理の基本操作を習得

# 参考資料

- [awsome-distributed-training: pytorch ddp](https://github.com/aws-samples/awsome-distributed-training/tree/main/3.test_cases/pytorch/ddp)
- [PyTorch Distributed Data Parallel](https://pytorch.org/docs/stable/notes/ddp.html)
- [Enroot（NVIDIA GitHub）](https://github.com/NVIDIA/enroot)
- [Slurm sbatch ドキュメント](https://slurm.schedmd.com/sbatch.html)