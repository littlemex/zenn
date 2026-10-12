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

## 技術面接の準備 (FAQ)

この節は、本章の内容を技術面接で聞かれたときに答えるための想定問答である。システム設計面接の流れ (要件の確認、全体の設計、深掘り、トレードオフ、故障と検証) に沿って並べ、各問は英語の質問、日本語の質問、英語の回答、日本語の回答の順に書いた。回答は本章に書いた事実だけを使う。

:::details Q1. What problem did this PR solve, and what did you change?
**質問 (日本語)**

この PR は何を解決し、何を変えたか。

**Answer (English)**

The picotron test case wasn't reproducible. The Dockerfile cloned picotron without pinning a commit, so the image changed silently whenever upstream moved. The README pointed at the wrong config path, the EC2 README duplicated a setup step, `create_config.py` opened its template from the current directory, and `train.sbatch` had no EFA settings for multi-node runs. I pinned the commit, resolved the template path from `__file__` and vendored the template, added the EFA and NCCL environment variables from the repository's EFA cheatsheet, and fixed the two README issues.

**回答 (日本語)**

picotron のテストケースが再現できない状態だった。Dockerfile が commit を固定せずに clone していたので、上流が変わるとイメージが黙って変わる。README は設定の出力先を誤って書き、EC2 の README は準備の手順を重ねて書き、`create_config.py` はテンプレートをカレントディレクトリから開き、`train.sbatch` には複数ノードで EFA を使う設定が無かった。commit を固定し、テンプレートの場所を `__file__` から求めてリポジトリに置き、リポジトリの EFA cheatsheet に合わせて EFA と NCCL の環境変数を足し、README の 2 か所を直した。
:::

:::details Q2. Why did you pin a commit but leave torch at 2.1.0?
**質問 (日本語)**

commit は固定したのに、なぜ torch は 2.1.0 のまま残したのか。

**Answer (English)**

Because bumping it in the Dockerfile wouldn't have taken effect. The pinned picotron commit specifies `torch==2.1.0` in its `requirements.txt`, and `pip install -e .` reinstalls that version over whatever the Dockerfile installed. So I left it and wrote the reason down. The reviewer valued that note because it stops the next person from "fixing" something that doesn't need fixing.

**回答 (日本語)**

Dockerfile だけで上げても効かないからである。固定した picotron の commit は `requirements.txt` で `torch==2.1.0` を指定していて、`pip install -e .` がそれで上書きする。そのため上げずに残し、理由を書いた。レビューでは、直す必要の無いものを後の人が直してしまうのを防ぐ、と評価された。
:::

:::details Q3. How did you prove EFA was actually used?
**質問 (日本語)**

EFA が実際に使われたことをどう証明したか。

**Answer (English)**

Setting environment variables is not evidence, so I checked the path NCCL actually chose. Separately from training, I ran a two-node all_reduce with `torchrun` and `NCCL_DEBUG=INFO`, and confirmed on both nodes that aws-ofi-nccl initialized and logged `Selected Provider is efa`. On g5.8xlarge the log also shows GPU Direct RDMA disabled. Then I ran a cut-down training job for 5,000 steps with exit code 0 and no NCCL errors. To be precise, the provider evidence comes from that separate all_reduce run, not from the training job's own logs, and both runs used the earlier `NCCL_SOCKET_IFNAME=en` version.

**回答 (日本語)**

環境変数を足しただけでは証拠にならないので、NCCL が実際に選んだ経路を見た。学習とは別に `torchrun` で 2 ノードの all_reduce を `NCCL_DEBUG=INFO` で動かし、両ノードで aws-ofi-nccl が初期化され `Selected Provider is efa` が出ることを確かめた。g5.8xlarge では GPU Direct RDMA が無効と出る。そのうえで、縮めた設定の学習ジョブが 5000 ステップを NCCL のエラー無しで終了コード 0 で完走することを確かめた。正確に言うと、プロバイダの証拠は別に動かした all_reduce のものであり、学習ジョブ自身のログではない。どちらもレビュー前の `NCCL_SOCKET_IFNAME=en` の版で動かした。
:::

:::details Q4. Why `NCCL_SOCKET_IFNAME=^docker,lo,veth` instead of `en`?
**質問 (日本語)**

`NCCL_SOCKET_IFNAME` を `en` ではなく `^docker,lo,veth` にしたのはなぜか。

**Answer (English)**

I first used `en`, an allowlist, because it states the intended interface and is easy to read. The reviewer pointed out that it can miss instances whose interfaces don't start with `en`, and suggested the exclusion form already used by the FSDP, DeepSpeed and nanoVLM test cases. I adopted the suggestion, and the merged `train.sbatch` uses `^docker,lo,veth`. The trade-off is that if an unexpected, unwanted interface appears, someone has to add it to the exclusion list. Note that this variable only controls NCCL's socket bootstrap, not the EFA provider selection.

**回答 (日本語)**

最初は、使う側を明示できて意図が読みやすい `en` (許可の形) にした。レビューで、名前が `en` で始まらないインタフェースを持つインスタンスでは外れうると指摘され、FSDP、DeepSpeed、nanoVLM の sbatch と同じ除外の形を提案された。この提案を採り、マージした `train.sbatch` は `^docker,lo,veth` になっている。代償は、想定外の不要なインタフェースが増えたときに除外の一覧へ足す必要があることである。なお、この変数は NCCL のソケットでの待ち合わせを決めるもので、EFA のプロバイダの選択とは別である。
:::

:::details Q5. Why `FI_EFA_USE_HUGE_PAGE=0`, and why not set `NCCL_DEBUG`?
**質問 (日本語)**

なぜ `FI_EFA_USE_HUGE_PAGE=0` を入れ、`NCCL_DEBUG` は入れなかったのか。

**Answer (English)**

Both follow the repository's EFA cheatsheet. `FI_EFA_USE_HUGE_PAGE=0` matches its preset for libfabric 1.18.0+ and aws-ofi-nccl 1.7.0+, and prevents PyTorch's multi-process dataloader from exhausting huge pages and failing with `Cannot allocate memory`. `FI_PROVIDER=efa` makes the provider explicit so the script doesn't depend on a particular aws-ofi-nccl version on the host. The cheatsheet recommends leaving `NCCL_DEBUG` off unless you're investigating NCCL, so I only used it in the separate verification run.

**回答 (日本語)**

どちらもリポジトリの EFA cheatsheet に合わせた。`FI_EFA_USE_HUGE_PAGE=0` は libfabric 1.18.0 以上、aws-ofi-nccl 1.7.0 以上のプリセットに従い、PyTorch の複数プロセスの dataloader が huge page を使い切って `Cannot allocate memory` になるのを防ぐ。`FI_PROVIDER=efa` はプロバイダを明示し、スクリプトがホストの特定の aws-ofi-nccl の版に頼らないようにする。`NCCL_DEBUG` は、NCCL を疑うとき以外は切ることを cheatsheet が勧めているので、別に動かした検証でだけ使った。
:::

:::details Q6. Walk me through how a job reaches the GPU on Slurm with containers.
**質問 (日本語)**

コンテナを使う Slurm で、ジョブが GPU に届くまでの流れを説明してほしい。

**Answer (English)**

The `sbatch` script runs in a host shell, `srun` places tasks on nodes, pyxis and enroot start the container, and `torchrun` sets `RANK`, `WORLD_SIZE` and `MASTER_ADDR` before launching the training process. So the distributed environment variables come from `torchrun`, not `srun`. Inter-node traffic is meant to go through NCCL, aws-ofi-nccl and EFA. Two pitfalls I hit in my test environment, outside the PR's scope: on ParallelCluster a non-login `sbatch` shell may not have `/opt/slurm/bin` on `PATH`, and pyxis must be installed in `OnNodeConfigured`, not `OnNodeStart`, because enroot isn't installed yet at that stage.

**回答 (日本語)**

`sbatch` のスクリプトがホストのシェルで動き、`srun` がノードにタスクを置き、pyxis と enroot がコンテナを起動し、`torchrun` が `RANK`、`WORLD_SIZE`、`MASTER_ADDR` を設定してから学習のプロセスを起動する。分散の環境変数を設定するのは `srun` ではなく `torchrun` である。ノードの間の通信は NCCL、aws-ofi-nccl、EFA を通ることを狙う。PR の対象外だが検証の環境で 2 つ踏んだ。ParallelCluster では非ログインの `sbatch` のシェルに `/opt/slurm/bin` のパスが入らないことがあり、pyxis は enroot がまだ入っていない `OnNodeStart` ではなく `OnNodeConfigured` に置く必要がある。
:::

:::details Q7. What did you not test, and what is the risk?
**質問 (日本語)**

何を試しておらず、どんなリスクが残るか。

**Answer (English)**

I tested on two g5.8xlarge nodes with ParallelCluster 3.13.1 and Slurm 24.05.8, not on p4d or p5, which are the main targets, because of resources and cost. Also, my verification ran on the earlier `NCCL_SOCKET_IFNAME=en` version; the PR has no record of re-testing after the switch to `^docker,lo,veth`. Running outside the container was reported in an Issue comment but the command and logs aren't in the PR. I'd state these gaps explicitly rather than imply coverage.

**回答 (日本語)**

ParallelCluster 3.13.1、Slurm 24.05.8 の g5.8xlarge の 2 ノードで試し、主な対象の p4d と p5 は資源と費用の都合で試していない。また、検証はレビュー前の `NCCL_SOCKET_IFNAME=en` の版で行い、`^docker,lo,veth` に変えた後の再検証は PR に記録が無い。コンテナの外で動くことは Issue のコメントで報告したが、コマンドとログは PR に載せていない。試した範囲を広く見せず、こうした抜けははっきり言う。
:::

## まとめ

picotron の PR は、上流の commit の固定、`__file__` からのテンプレートの読み込み、EFA の環境変数、README の 2 か所の修正を入れたものである。別に動かした 2 ノードの all_reduce では NCCL のログで EFA が選ばれたことを、学習ジョブでは 5000 ステップの完走を確かめた。再現できるテストケースにするには、上流を固定し、スクリプトをカレントディレクトリに頼らせず、使われる経路をログで確かめる。
