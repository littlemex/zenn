---
title: "Awsome: DDP のコンテナ学習を直す"
free: false
---

## はじめに

本章では、PyTorch DDP のテストケースのコンテナ学習を直した 2 組の Issue と PR をまとめる。[Issue #1038](https://github.com/awslabs/awsome-distributed-ai/issues/1038) と [PR #1039](https://github.com/awslabs/awsome-distributed-ai/pull/1039) はイメージと引数の誤りを、[Issue #1047](https://github.com/awslabs/awsome-distributed-ai/issues/1047) と [PR #1048](https://github.com/awslabs/awsome-distributed-ai/pull/1048) は CPU のノードでときどき失敗する問題を扱う。

:::details DDP と GLOO
DDP（DistributedDataParallel）は、同じモデルを各プロセスに置き、勾配を全プロセスで足し合わせて学習する PyTorch の仕組みである。GPU のときは NCCL、CPU のときは GLOO が、プロセスの間の通信を受け持つ。このテストケースは、CPU だけのクラスタでも動かせるように GLOO を使う。
:::

## Issue と PR の概要

![1 つ目はイメージの作り方と引数の名前の 2 段の誤り。2 つ目は nproc_per_node=4 のとき、一部の rank が MNIST のファイルの破損を出し、他の rank が GLOO の接続切れを出す失敗。PR は PyTorch の版を固定し、1 ノードのプロセス数を変えられるようにした。共有のストレージへの同時のダウンロードは仮説として後続に残った](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/awsome-ddp-container/failure-chain.png)

| PR | 解決したいこと | やったこと | マージ |
| --- | --- | --- | --- |
| #1039 | コンテナ学習が `No module named 'mlflow'` で止まり、直すと次に引数の名前で止まる | Dockerfile からイメージを作り、引数の名前を合わせた | [7334940](https://github.com/awslabs/awsome-distributed-ai/commit/7334940bbf8b359ae18d07af436f62249372a619) |
| #1048 | CPU のノードで、ときどき MNIST の破損と GLOO の接続切れで失敗する | PyTorch を 2.10.0 に固定し、`NPROC_PER_NODE` で 1 ノードのプロセス数を変えられるようにした（回避策で、`nproc_per_node=4` の失敗は残る） | [cbdc4ee](https://github.com/awslabs/awsome-distributed-ai/commit/cbdc4eeaed2d3639ee9882e0166c46a47b7bc418) |

## PR #1039: イメージと引数

**何を解決しようとしたか**。`ddp.py` はモジュールの先頭で `mlflow` を無条件に import するので、MLflow を使わない実行でも `mlflow` が要る。venv の手順（`0.create-venv.sh`）は `mlflow==2.13.2` と `sagemaker-mlflow==0.1.0` を入れるが、コンテナの手順の `2.create-enroot-image.sh` は `docker://pytorch/pytorch` を Docker Hub から直接取り込み、これらを入れる1 つ上のディレクトリの `ddp/Dockerfile` を使っていなかった。そのため、コンテナの学習だけが `ModuleNotFoundError` で止まった。

**どうやったか**。[2.create-enroot-image.sh](https://github.com/awslabs/awsome-distributed-ai/blob/7334940bbf8b359ae18d07af436f62249372a619/3.test_cases/pytorch/ddp/slurm/2.create-enroot-image.sh) を、`docker build` で Dockerfile からイメージを作って `dockerd://` で取り込む形に変えた。これを直すと 2 つ目の誤りが現れた。sbatch は `--use-mlflow` を渡していたが、`ddp.py` の argparse は `--use_mlflow` を定義していた。[3.container-train.sbatch](https://github.com/awslabs/awsome-distributed-ai/blob/7334940bbf8b359ae18d07af436f62249372a619/3.test_cases/pytorch/ddp/slurm/3.container-train.sbatch) の引数を合わせた。

**なぜそうしたか**。依存を足す方法として、他のテストケース（deepspeed、trl、mosaicml-composer）も Dockerfile からイメージを作って `dockerd://` で取り込んでいる。同じ形にそろえた。

**テスト**。c5.4xlarge の 2 ノード、1 ノードに 4 プロセスの計 8 プロセスで、GLOO で学習が進み、各 rank がエポックの損失を出すことを確かめた。

## PR #1048: CPU のノードでときどき失敗する

**何を解決しようとしたか**。Issue #1047 は、2026 年 4 月 9 日の ParallelCluster のワークショップで使う DDP のコンテナ学習が、ときどき失敗することを報告した。ParallelCluster 3.14.2、c5n.9xlarge の 2 ノード、共有の FSx for Lustre で、`nproc_per_node=4`（計 8 プロセス）の失敗の例では、一部の rank が MNIST の読み込みで `File not found or corrupted` を出し、他の rank が GLOO の `Connection closed by peer` を出した。筆者の計測では、`nproc_per_node=4` の成功率は PyTorch 2.2.1（`pytorch:latest` が指していた版）で 0%、2.10.0 で約 70% だった（試行の回数は Issue に書いていない）。PyTorch 2.10.0 で `nproc_per_node=1`（計 2 プロセス）にすると、10 回中 10 回成功した。

筆者は原因を確定させず、仮説として 3 つを挙げた。PyTorch 2.2.1 の GLOO の不安定さ、プロセスの数が多いことによる同時のダウンロード、FSx for Lustre のメタデータの反映の遅れである。

**どうやったか**。[Dockerfile](https://github.com/awslabs/awsome-distributed-ai/blob/cbdc4eeaed2d3639ee9882e0166c46a47b7bc418/3.test_cases/pytorch/ddp/Dockerfile) の `FROM` を `pytorch/pytorch:2.10.0-cuda12.6-cudnn9-runtime` に固定し、venv の手順（`torch==2.10.0`）とそろえた。この版は Python 3.12 で PEP 668 に従うので、`pip install` に `--break-system-packages` を付けた。コンテナは使い捨てで、ホストから切り離されているので、この指定の害は小さい。sbatch には `NPROC_PER_NODE=${NPROC_PER_NODE:-4}` を足し、既定の 4 を保ったまま、ワークショップでは 1 を選べるようにした。

**なぜそうしたか**。直近のワークショップで安定して動くことを優先し、PR の範囲を版の固定と実行時の切り替えに絞った。`ddp.py` は変えていない。筆者は Issue のコメントで、共有データ向けの `--container-mounts` の設定、データセットによらない barrier での同期、ストレージの見直し、の 3 つを後続で調べる項目に挙げた。

**レビュー**。KeitaW さんから、既定値を代入する行が、それを使う配列の宣言より後にあると指摘があった。bash の配列は宣言の時点で展開されるので、既定値が効かない。代入を配列の前に移し、変数を引用符で囲んだ。

**テスト**。筆者は ParallelCluster の c5n.9xlarge の 2 ノードで、既定の `nproc_per_node=4` と `NPROC_PER_NODE=1` の 2 通りを試した。どちらも毎回の前に `rm -rf ../data` で MNIST を消し、初めてのダウンロードから再現させた。イメージの中の版が `2.10.0+cu126` であることも確かめた。`nproc_per_node=4` のログには、rank の 1 つが MNIST のダウンロードで例外を出す場面が残った。KeitaW さんは別の環境の [SageMaker HyperPod の Slurm](https://github.com/awslabs/awsome-distributed-ai/pull/1048)（ml.c5.2xlarge の 2 ノード、FSx for Lustre）でも、`NPROC_PER_NODE` を使う venv の sbatch で確かめ（NVIDIA のドライバの無い CPU のインスタンスではコンテナの経路が使えないため）、`NPROC_PER_NODE=1` は 5 回中 5 回、`4` は 5 回中 4 回成功した。失敗した 1 回は、同じ MNIST の破損と GLOO の接続切れだった。KeitaW さんは、すべての rank が共有のストレージに対して `datasets.MNIST(download=True)` を呼ぶことによる競合は PyTorch の版と別の問題で、`ddp.py` に `torch.distributed.barrier()` の守りを入れれば直せるとし、後続の PR に回すとした。barrier の修正は、この PR では実装も検証もしていない。

## 技術的な要点

- **GLOO の接続切れが出たときの最初の確認**。この章の失敗では、ある rank が MNIST の `File not found or corrupted` で先に落ち、残りの rank が GLOO の `Connection closed by peer` を出した。接続切れのエラーだけを追わず、最初に落ちた rank のトレースバックを探す。
- **1 つずつ現れる誤り**。1 つの誤りを直すと、次の誤りが現れることがある。直した後も、最後まで動かして確かめる。
- **配列を組み立てる前の既定値の代入**。`NPROC_PER_NODE=${NPROC_PER_NODE:-4}` のような代入は、その変数を使う配列の宣言より前に置く。配列の要素は宣言の時点で展開されるので、後から代入しても配列の中の値は変わらない。
- **latest のタグ**。`latest` は、いつ何を指すかが変わる。テストケースは版を固定する。

## まとめ

PR #1039 は、依存のパッケージの不足と引数の名前の不一致を直した。PR #1048 は、版の固定とプロセスの数の切り替えを足した。共有のストレージへの同時のダウンロードを防ぐ同期は、後続の課題として残った。
