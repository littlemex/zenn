---
title: "Awsome: picotron のテストケースを固める"
free: false
---

## はじめに

[awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)（旧名 awsome-distributed-training）は、AWS の上で分散学習と分散推論を動かすためのサンプル集である。本章では、picotron のテストケースを再現できる形に直した [Issue #975](https://github.com/awslabs/awsome-distributed-ai/issues/975) と [PR #1019](https://github.com/awslabs/awsome-distributed-ai/pull/1019) をまとめる。

:::details picotron と、Slurm でジョブが GPU に届くまで
[picotron](https://github.com/huggingface/picotron) は、Hugging Face が公開している教育用の分散学習フレームワークで、テンソル並列（TP）、パイプライン並列（PP）、データ並列（DP）を短いコードで実装している。このテストケースは、SmolLM-1.7B を 2 ノードで TP=2 にして学習する。

Slurm の上でコンテナを使う分散学習では、`sbatch` のスクリプトがホストのシェルで動き、`srun` がノードにタスクを置き、pyxis と enroot がその中でコンテナを起動し、`torchrun` が `RANK` や `MASTER_ADDR` を設定してから学習のプロセスを起動する。ノードの間の通信は、NCCL が aws-ofi-nccl を通して EFA を使うことを狙う。設定が足りないと TCP に落ちることがあるので、実際に EFA が選ばれたかはログで確かめる。
:::

## Issue と PR の概要

![sbatch のスクリプトから srun、enroot のコンテナ、torchrun、train.py と NCCL を経て EFA に届く起動の流れ。PR #1019 の 4 か所の修正と、PR の対象外で検証の環境で踏んだ 2 つの落とし穴](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/awsome-picotron/launch-chain.png)

| 項目 | 内容 |
| --- | --- |
| 解決したいこと | ビルドが再現しない、手順書が実際の動きと合わない、複数ノードで EFA を使う設定が無い |
| やったこと | picotron の commit を固定し、設定ファイルの読み込み先を直し、EFA と NCCL の環境変数を足し、README の 2 か所を直した |
| テスト | ParallelCluster と Slurm の g5.8xlarge の 2 ノードで、all_reduce のログから EFA が選ばれることを、別の学習ジョブで 5000 ステップの完走を確かめた。主な対象の p4d と p5 では確かめていない |
| マージ | [6056782](https://github.com/awslabs/awsome-distributed-ai/commit/6056782ef127b7013484bc2aa5bc56e5a56bb8c4) |

## 何を解決しようとしたか

Issue #975 は 5 つの問題を挙げていた。

1. Dockerfile が picotron を commit を決めずに clone していたので、上流が変わるとイメージが黙って変わる。
2. Slurm の README が、設定の出力先を `conf/llama-1B-dp2-tp2-pp2/config.json` と書いていた。掲載のコマンドの `--exp_name llama-1B-tp2` と `train.sbatch` が使うのは `conf/llama-1B-tp2/config.json` である。
3. EC2 の README が、コンテナイメージを作る手順を準備の 1 番と 3 番に重ねて書いていた。
4. `train.sbatch` に EFA と NCCL の環境変数が無く、Issue は、p4d や p5 の複数ノードの実行が TCP に落ちたり、気づかないうちに遅くなったりするおそれを挙げていた。
5. `create_config.py` は、テンプレートの `template/base_config.json` をカレントディレクトリから開き、そのテンプレートはこのリポジトリに無く、コンテナの中に clone した picotron にあった。

## どうやったか

[Dockerfile](https://github.com/awslabs/awsome-distributed-ai/blob/6056782ef127b7013484bc2aa5bc56e5a56bb8c4/3.test_cases/pytorch/picotron/picotron.Dockerfile) は、clone のあとに `git checkout 59714b1bf255` を足した。[create_config.py](https://github.com/awslabs/awsome-distributed-ai/blob/6056782ef127b7013484bc2aa5bc56e5a56bb8c4/3.test_cases/pytorch/picotron/create_config.py) は、テンプレートの場所を `__file__` から求めるように変え、テンプレートの JSON を固定した commit から取ってリポジトリに置いた。[train.sbatch](https://github.com/awslabs/awsome-distributed-ai/blob/6056782ef127b7013484bc2aa5bc56e5a56bb8c4/3.test_cases/pytorch/picotron/SmolLM-1.7B/slurm/train.sbatch) には、`FI_EFA_USE_HUGE_PAGE=0`、`FI_PROVIDER=efa`、`NCCL_SOCKET_IFNAME=^docker,lo,veth` を足した。Slurm の README は出力先を `conf/llama-1B-tp2/config.json` に直し、EC2 の README は重なった手順を 1 つにした。

## なぜそうしたか

**torch の版**。`torch==2.1.0` は上げずに残した。固定した commit の `requirements.txt` が `torch==2.1.0` を指定していて、`pip install -e .` がそれで上書きするので、Dockerfile だけ上げても効かないからである。レビューの KeitaW さんは、この理由を書いたことを「直す必要の無いものを後の人が直してしまうのを防ぐ」と評価した。

**EFA の環境変数**。リポジトリの [EFA cheatsheet](https://github.com/awslabs/awsome-distributed-ai/blob/6056782ef127b7013484bc2aa5bc56e5a56bb8c4/1.architectures/efa-cheatsheet.md) に合わせた。`FI_PROVIDER=efa` は、libfabric が使うプロバイダとして EFA を明示する。PR は、スクリプトを動かすためにホストの特定の aws-ofi-nccl の版に頼らないことを理由にした。`FI_EFA_USE_HUGE_PAGE=0` は cheatsheet のプリセット 3.1（libfabric 1.18.0 以上、aws-ofi-nccl 1.7.0 以上）に従い、PyTorch の複数プロセスの dataloader が huge page を使い切って `Cannot allocate memory` になるのを防ぐ。`NCCL_DEBUG` は、cheatsheet が「NCCL を疑うとき以外は切る」と勧めているので入れていない。

**NCCL のソケットのインタフェース**。`NCCL_SOCKET_IFNAME` は、NCCL がソケットでの待ち合わせに使うインタフェースを決める変数で、EFA のプロバイダの選択とは別である。比べた方向は、`en` で始まるインタフェースだけを選ぶ `en` で、使う側を明示できるので意図が読みやすい。KeitaW さんはレビューで、名前が `en` で始まらないインタフェース（例として p4d や p5 の `efa0`、`efa1`）を持つインスタンスでは外れうると指摘し、FSDP、DeepSpeed、nanoVLM の sbatch と同じ除外の形 `^docker,lo,veth` を提案した。PR はこの提案を採り、マージした `train.sbatch` は `^docker,lo,veth` になっている。除外の形の代償は、想定外の不要なインタフェースが増えたときに一覧へ足す必要があることである。

## テストをどうしたか

ParallelCluster 3.13.1 と Slurm 24.05.8 で、g5.8xlarge（A10G、EFAv2）を 2 ノード使った。主な対象の p4d と p5 は、資源と費用の都合で確かめていない。以下の検証は、レビューの前の `NCCL_SOCKET_IFNAME=en` の版で行ったもので、`^docker,lo,veth` に変えた後の再検証は PR に記録が無い。

まず、Docker のビルドのログに `HEAD is now at 59714b1` が出ることで commit の固定を、`create_config.py` をコンテナの中で `--exp_name llama-1B-tp2` で実行し、`conf/llama-1B-tp2/config.json` ができることで README のパスを確かめた。コンテナの外で動くことは筆者が Issue のコメントで報告しているが、PR にはそのコマンドとログを載せていない。

次に、学習とは別に `torchrun` で 2 ノードの all_reduce を動かし、`NCCL_DEBUG=INFO` のログで両ノードが EFA を選んだことを確かめた。g5.8xlarge では GPU Direct RDMA は無効と出る。

```
NCCL INFO NET/OFI Initializing aws-ofi-nccl 1.13.2-aws
NCCL INFO NET/OFI Selected Provider is efa (found 1 nics)
NCCL INFO NET/Libfabric : GPU Direct RDMA Disabled for HCA 0 'rdmap0s29'
NCCL INFO Channel 00/0 : 0[0] -> 1[0] [send] via NET/Libfabric/0
[PASS] all_reduce complete: world_size=2, result=2.0
```

最後に学習ジョブを動かした。設定は SmolLM-1.7B を `num_hidden_layers=5`、DP=1、TP=2、PP=1、`grad_acc_steps=2`、`mbs=4`、`seq_len=128` に縮めた検証用のものである。検証の環境では、手元の `train.sbatch` に PR の対象外の 2 行（後述の `PATH` と `HF_TOKEN`）を足した。`sbatch train.sbatch` は 5000 ステップを NCCL のエラー無しで終了コード 0 で完走し、PR に載せた rank 0 の記録では Step 1 が 215.85 tokens/s、Step 100〜5000 が 7,230〜7,358 tokens/s だった。

## 技術的な要点

- **PyTorch の分散の環境変数を設定するもの**。`RANK`、`WORLD_SIZE`、`MASTER_ADDR` を設定するのは `torchrun` で、`srun` ではない。NCCL の確認も `srun ... torchrun ...` の形で起動する。
- **EFA が使われた証拠**。環境変数を足しただけでは証拠にならない。`Selected Provider is efa` のログを両ノードで確かめる。
- **ParallelCluster の sbatch での Slurm のパス**（検証の環境で踏んだもの、PR の対象外）。ParallelCluster は Slurm を `/opt/slurm/bin` に置き、`/etc/profile.d/` でパスを足す。`sbatch` のスクリプトは非ログインのシェルで動くので、このパスが入らず `srun: command not found` になることがある。そのときは `export PATH=/opt/slurm/bin:$PATH` を足す。コンテナの中から `srun` を呼ぶテストケースでは、別に `--container-mounts` で `/opt/slurm/bin` を見せる必要がある。picotron はコンテナの中から `srun` を呼ばないので、この対処は要らない。
- **pyxis を入れる段階**（検証の環境で踏んだもの）。ParallelCluster のカスタムアクションで pyxis を入れるときは、`OnNodeStart` ではなく `OnNodeConfigured` に置く。`OnNodeStart` はブートストラップの前に動くので、enroot がまだ入っていない。
- **公開モデルでも要る HF_TOKEN**（検証の環境で踏んだもの、PR の対象外）。固定した commit の picotron の `train.py` は、設定ファイルにも環境変数にも `HF_TOKEN` が無いと、公開モデルでも起動で止まる。検証では手元の sbatch に `export HF_TOKEN=""` を足した。

## まとめ

picotron の PR は、上流の commit の固定、`__file__` からのテンプレートの読み込み、EFA の環境変数、README の 2 か所の修正を入れたものである。別に動かした 2 ノードの all_reduce では NCCL のログで EFA が選ばれたことを、学習ジョブでは 5000 ステップの完走を確かめた。再現できるテストケースにするには、上流を固定し、スクリプトをカレントディレクトリに頼らせず、使われる経路をログで確かめる。
