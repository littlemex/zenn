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

## 技術面接の準備 (FAQ)

この節は、本章の内容を技術面接で聞かれたときに答えるための想定問答である。システム設計面接の流れ (要件の確認、全体の設計、深掘り、トレードオフ、故障と検証) に沿って並べ、各問は英語の質問、日本語の質問、英語の回答、日本語の回答の順に書いた。回答は本章に書いた事実だけを使う。

:::details Q1. Why did only the container path fail with `No module named 'mlflow'`?
**質問 (日本語)**

なぜコンテナの経路だけが `No module named 'mlflow'` で止まったのか。

**Answer (English)**

`ddp.py` imports `mlflow` unconditionally at module load, so it's needed even when MLflow isn't used. The venv path installs `mlflow` and `sagemaker-mlflow`, but the container path imported `docker://pytorch/pytorch` straight from Docker Hub and never used the `ddp/Dockerfile` that adds them. I changed `2.create-enroot-image.sh` to build from the Dockerfile and import it with `dockerd://`, the same pattern the deepspeed, trl and mosaicml-composer test cases use.

**回答 (日本語)**

`ddp.py` がモジュールの先頭で `mlflow` を無条件に import するので、MLflow を使わない実行でも要る。venv の経路は `mlflow` と `sagemaker-mlflow` を入れるが、コンテナの経路は Docker Hub の `docker://pytorch/pytorch` を直接取り込み、それらを入れる `ddp/Dockerfile` を使っていなかった。`2.create-enroot-image.sh` を、Dockerfile からイメージを作って `dockerd://` で取り込む形に変えた。deepspeed、trl、mosaicml-composer のテストケースと同じ形である。
:::

:::details Q2. After that fix, a second error appeared. How do you deal with errors that surface one at a time?
**質問 (日本語)**

その修正の後に 2 つ目の誤りが出た。1 つずつ現れる誤りにどう向き合うか。

**Answer (English)**

Fixing the import exposed an argument mismatch: the sbatch passed `--use-mlflow`, but `ddp.py`'s argparse defined `--use_mlflow`. The lesson is that a fix isn't done when the first error disappears; you run the whole path to the end. I fixed the argument in `3.container-train.sbatch` and confirmed training on two c5.4xlarge nodes with four processes each, eight in total, with every rank reporting its epoch loss over GLOO.

**回答 (日本語)**

import を直すと、引数の名前の不一致が現れた。sbatch は `--use-mlflow` を渡していたが、`ddp.py` の argparse は `--use_mlflow` を定義していた。最初の誤りが消えたところで終わりにせず、最後まで動かして確かめる、というのが教訓である。`3.container-train.sbatch` の引数を合わせ、c5.4xlarge の 2 ノード、1 ノードに 4 プロセスの計 8 プロセスで、GLOO で学習が進み各 rank がエポックの損失を出すことを確かめた。
:::

:::details Q3. How did you investigate the intermittent failure, and what was the root cause?
**質問 (日本語)**

ときどき起きる失敗をどう調べ、原因は何だったか。

**Answer (English)**

On two c5n.9xlarge nodes with shared FSx for Lustre and `nproc_per_node=4`, some ranks failed reading MNIST with `File not found or corrupted`, and the others then reported GLOO `Connection closed by peer`. The key was to find the rank that failed first instead of chasing the connection errors. I measured success rates: 0% on PyTorch 2.2.1, which `latest` pointed to, about 70% on 2.10.0, and 10 out of 10 with one process per node. I didn't claim a single root cause; I listed three hypotheses: GLOO instability in 2.2.1, concurrent downloads from many processes, and FSx metadata propagation delay. In review, the maintainer saw the same failure once on a different cluster, and said that every rank calling `datasets.MNIST(download=True)` on shared storage is a race separate from the PyTorch version, fixable with a `torch.distributed.barrier()` guard in `ddp.py`. I didn't isolate why 2.10.0 succeeded more often than 2.2.1.

**回答 (日本語)**

共有の FSx for Lustre を使う c5n.9xlarge の 2 ノード、`nproc_per_node=4` で、一部の rank が MNIST の読み込みで `File not found or corrupted` を出し、残りの rank が GLOO の `Connection closed by peer` を出した。要点は、接続切れを追わずに最初に落ちた rank を探すことだった。成功率は、`latest` が指していた PyTorch 2.2.1 で 0%、2.10.0 で約 70%、1 ノード 1 プロセスなら 10 回中 10 回だった。原因を 1 つに確定させず、2.2.1 の GLOO の不安定さ、多数のプロセスによる同時のダウンロード、FSx のメタデータの反映の遅れの 3 つを仮説として挙げた。レビューではメンテナも別の環境で同じ失敗を 1 回観測し、すべての rank が共有のストレージで `datasets.MNIST(download=True)` を呼ぶ競合は PyTorch の版と別の問題で、`ddp.py` に `torch.distributed.barrier()` の守りを入れれば直せるとした。2.10.0 が 2.2.1 より成功しやすい理由は切り分けていない。
:::

:::details Q4. Why ship a workaround instead of fixing the race?
**質問 (日本語)**

なぜ競合を直さずに回避策を出したのか。

**Answer (English)**

There was a ParallelCluster workshop on April 9, 2026 that depended on this test case, so I prioritized stability for that date and kept the scope to pinning the version and adding a runtime switch. I pinned the base image to `pytorch/pytorch:2.10.0-cuda12.6-cudnn9-runtime` to match the venv path, and added `NPROC_PER_NODE=${NPROC_PER_NODE:-4}`, keeping the default of 4 while letting the workshop choose 1. I didn't touch `ddp.py`. The trade-off is explicit: the `nproc_per_node=4` failure remains. The barrier guard the reviewer proposed was left for a follow-up PR, and it's neither implemented nor tested here.

**回答 (日本語)**

このテストケースを使う ParallelCluster のワークショップが 2026 年 4 月 9 日にあったので、その日の安定を優先し、範囲を版の固定と実行時の切り替えに絞った。ベースのイメージを venv の経路とそろえて `pytorch/pytorch:2.10.0-cuda12.6-cudnn9-runtime` に固定し、`NPROC_PER_NODE=${NPROC_PER_NODE:-4}` を足して、既定の 4 を保ったままワークショップでは 1 を選べるようにした。`ddp.py` は変えていない。代償ははっきりしていて、`nproc_per_node=4` の失敗は残る。レビュアーが示した barrier の守りは後続の PR に回し、この PR では実装も検証もしていない。
:::

:::details Q5. Isn't `--break-system-packages` dangerous?
**質問 (日本語)**

`--break-system-packages` は危なくないか。

**Answer (English)**

The 2.10.0 image ships Python 3.12, which follows PEP 668 and refuses system-wide `pip install` by default. The flag overrides that. The risk is that pip overwrites packages the image's own Python depends on. Here the container is disposable and isolated from the host, and the image is rebuilt from the Dockerfile, so the harm is small. I'd not use the same flag on a long-lived host.

**回答 (日本語)**

2.10.0 のイメージは Python 3.12 で、PEP 668 に従い、既定ではシステムへの `pip install` を拒む。このフラグはそれを上書きする。危険は、pip がイメージ自身の Python が頼るパッケージを上書きすることである。ここではコンテナが使い捨てで、ホストから切り離されていて、イメージは Dockerfile から作り直せるので、害は小さい。長く使うホストでは同じフラグは使わない。
:::

:::details Q6. What did the review catch in the shell script?
**質問 (日本語)**

レビューはシェルのスクリプトで何を見つけたか。

**Answer (English)**

The line that assigned the default for `NPROC_PER_NODE` came after the bash array that used it. Bash expands array elements when the array is declared, so the default never reached the array. I moved the assignment above the array and quoted the variable. It's a small bug, but it would have made the new switch silently ineffective.

**回答 (日本語)**

`NPROC_PER_NODE` の既定値を代入する行が、それを使う bash の配列の宣言より後にあった。配列の要素は宣言の時点で展開されるので、既定値が配列に届かない。代入を配列の前に移し、変数を引用符で囲んだ。小さな誤りだが、足した切り替えが黙って効かなくなるところだった。
:::

:::details Q7. How was the fix verified, and by whom?
**質問 (日本語)**

修正はどう検証され、誰が確かめたか。

**Answer (English)**

I tested the changed container path myself on two c5n.9xlarge nodes, with one and four processes per node. Before each run I deleted MNIST with `rm -rf ../data`, so every run repeated the first download, and I confirmed the image reported `2.10.0+cu126`. With four processes per node, a rank still raised an exception during the download. The maintainer separately tested the venv sbatch, not the container path, on SageMaker HyperPod Slurm with two ml.c5.2xlarge nodes, because on those CPU instances without NVIDIA drivers the container path wasn't usable. With one process per node it passed 5 of 5, with four it passed 4 of 5, and the failed run showed the same MNIST and GLOO errors.

**回答 (日本語)**

変えたコンテナの経路は、自分で c5n.9xlarge の 2 ノードで、1 ノードに 1 プロセスと 4 プロセスの両方を試した。毎回の前に `rm -rf ../data` で MNIST を消して初めてのダウンロードから再現させ、イメージの中の版が `2.10.0+cu126` であることも確かめた。1 ノードに 4 プロセスでは、ダウンロードで例外を出す rank がまだ現れた。メンテナは別に、SageMaker HyperPod の Slurm の ml.c5.2xlarge の 2 ノードで、コンテナの経路ではなく venv の sbatch を確かめた。NVIDIA のドライバの無いその CPU のインスタンスでは、コンテナの経路が使えないからである。1 ノードに 1 プロセスで 5 回中 5 回、4 プロセスで 5 回中 4 回成功し、失敗した 1 回は同じ MNIST と GLOO の誤りだった。
:::

## まとめ

PR #1039 は、依存のパッケージの不足と引数の名前の不一致を直した。PR #1048 は、版の固定とプロセスの数の切り替えを足した。共有のストレージへの同時のダウンロードを防ぐ同期は、後続の課題として残った。
