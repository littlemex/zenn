---
title: "Awsome: miles の GRPO のテストケースを足す"
free: false
---

## はじめに

本章では、[miles](https://github.com/radixark/miles) の GRPO のテストケースを足した [Issue #1224](https://github.com/awslabs/awsome-distributed-ai/issues/1224) と [PR #1225](https://github.com/awslabs/awsome-distributed-ai/pull/1225) をまとめる。miles は SLIME を CUDA 13 と NVIDIA Blackwell 向けにフォークしたもので、このテストケースは前章の SLIME のテストケースと同じ形にそろえ、2 つを同じ手順で使って比べられるようにした。

:::details colocated と disaggregated
強化学習の後学習では、生成（rollout）と学習を同じ GPU で交互に行う colocated と、別々の GPU に分ける disaggregated の 2 つの構成がある。colocated は GPU を無駄なく使えるが、生成と学習の切り替えのたびにメモリを入れ替える。disaggregated は切り替えが要らないが、学習した重みを生成の側へネットワーク越しに送る。
:::

## Issue と PR の概要

![RayCluster の head は調整役で GPU を持たず、GPU のワーカーが SGLang の生成と Megatron の学習を受け持つ。FSx for Lustre と、クラスタの中でイメージを作る BuildKit の Job がそれを支える。CPU の報酬サービスは、報酬を別に計算する remote_rm の経路のための任意の部品で、学習の報酬の経路としては動かしていない。レビューの 18 件は、名前空間と head の取り違え、報酬の誤りを 0.0 として返すこと、イメージとドキュメントのずれ、検証の表の再現性などだった](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/awsome-miles-grpo/layout.png)

| 項目 | 内容 |
| --- | --- |
| 解決したいこと | CUDA 13 と Blackwell を対象にした SLIME のフォークの miles を、このリポジトリで SLIME と並べて使い、GPU に合うスタックを選べるようにする |
| やったこと | Dockerfile、RayCluster などの Kubernetes のマニフェスト、dense と MoE のレシピ、報酬サービス、変換と評価のスクリプト、README を足した |
| テスト | レビューの後、別のリージョンに一からクラスタを作り、README の手順どおりに dense と MoE の 4 つの構成、チェックポイントの往復の変換、報酬サービスを確かめた |
| マージ | [f342873](https://github.com/awslabs/awsome-distributed-ai/commit/f342873c8d170678a53231549295e1e5454eb279) |

## どうやったか

テストケースは [examples/training/miles](https://github.com/awslabs/awsome-distributed-ai/tree/f342873c8d170678a53231549295e1e5454eb279/examples/training/miles) にある。ベースのイメージは `radixark/miles` を digest で固定し、その上に AWS の EFA の層を足した。Kubernetes のマニフェストは `envsubst` で値を埋める形で、RayCluster、クラスタの中でイメージを作る BuildKit の Job、データを用意する Pod、報酬を別に計算する `remote_rm` の経路のための CPU の報酬サービスからなる。同梱のレシピは、既定ではルールに基づく報酬を学習の中で計算する。30B MoE は、最初のレシピでは rollout の後の最初の optimizer の step で GPU のメモリが足りなかった。そこでレシピに `--optimizer-cpu-offload` などを足し、optimizer をホストのメモリに逃がした。`run-on-cluster.sh` は、head の Pod の中からレシピを Ray の job として投げる。

## テストをどうしたか

最初の PR の説明には 9 行の結果の表を載せたが、レビューで、同梱した設定だけでは再現できない行があると指摘された。筆者は再現できない行を消し、レビューの後に別のリージョンに一からクラスタを作り、KubeRay を入れ、クラスタの中の BuildKit の Job でイメージを作り、README の Quick Start を step 0 から書いてあるとおりに辿った。以下は、筆者が p5en.48xlarge（1 ノードに H200 8 枚）で、構成ごとに別々に動かした結果で、[PR の最終の返答](https://github.com/awslabs/awsome-distributed-ai/pull/1225)に載せた。値は TensorBoard のイベントファイルから読み戻したもので、実行ごとに記録を分けてある。ループが最後まで回ったことを示す値で、収束を示すものではない。

| 構成 | rollout の回数 | reward | repetition | 時間 |
| --- | --- | --- | --- | --- |
| Qwen3-4B dense、colocated、1 ノード | 100 | 0.508 | 0.023 | 3 時間 15 分 |
| Qwen3-4B dense、disaggregated、2 ノード | 60 | 0.461 | 0.008 | 1 時間 50 分 |
| GLM-Z1-9B dense、colocated、TP=2、2 ノード | 100 | 0.672 | 0.008 | 2 時間 35 分 |
| Qwen3-30B-A3B MoE、colocated、EP のみ、2 ノード | 100 | 0.719 | 0.008 | 5 時間 5 分 |

どの構成も Ray の job は `succeeded` で終わった。チェックポイントは、Hugging Face から Megatron への変換と、4B の実行のチェックポイントからの逆の変換を、README のコマンドで確かめた。報酬サービスは、CPU のノードで 4 つのレプリカが Ready になることと、閾値を下げて readiness の動きを直接確かめた。学習の報酬の経路としては動かしていないので、miles の `remote_rm` が 503 を再試行するかは確かめていない。

4B の 100 回の rollout では、rollout の reward が平らなまま、AIME の評価が 0.596 から 0.333 に下がった。筆者は、応答の平均の長さが伸び、上限の 16384 トークンに届く応答の割合が 0.38 から 0.60 に増えたので、切り詰めと連動していると書いたが、原因は切り分けていない。TP と EP を組み合わせた MoE の構成で FlashInfer の融合を有効にすると出力が崩れること（レシピはこの構成で融合を切る）と、Qwen2.5-72B が試した構成では H200 16 枚に収まらないことは、結果ではなく制約として README の Known Issues に書いた。

## レビューで直したこと

KeitaW さんは、レビューの前に p6-b300.48xlarge の 2 ノードのクラスタで、Qwen3-4B の dense、colocated の構成を GPU 8 枚で最後まで動かしたと報告した。GRPO の生成、報酬、Megatron の学習、colocated の CUDA IPC での重みの同期は Blackwell でも動き、reward は 3 回の rollout で 0.4922、0.5156、0.5313 と動き、AIME の評価は 0.6125 だった。そのうえで、4 回に分けて 18 件の指摘があり、すべて対応した。主なものは次のとおりである。

| 指摘 | 対応 |
| --- | --- |
| `run-on-cluster.sh` が env のファイルの名前空間を読まず、`ray.io/node-type=head` の最初の Pod を選ぶので、別のクラスタの head に投げうる | [マージした版](https://github.com/awslabs/awsome-distributed-ai/blob/f342873c8d170678a53231549295e1e5454eb279/examples/training/miles/run-on-cluster.sh)は、名前空間をフラグ、シェルの変数、env のファイルから決め、`ray.io/cluster` のラベルで head を選び、ワーカーの状態も確かめる |
| 報酬サービスが、採点の例外をすべて reward 0.0 の 200 で返す。壊れたまま 0 の信号で学習が最後まで進む | [マージした版](https://github.com/awslabs/awsome-distributed-ai/blob/f342873c8d170678a53231549295e1e5454eb279/examples/training/miles/reward_service/app.py)は、採点に失敗したら 0.0 ではなく 503 を返して数え、既定で 20 回続けて失敗したら readiness を 503 にする。採点器は別のプロセスで動かし、時間の上限を付ける |
| `evaluate.sh` が文字列の一致で採点していて、数学的に同じ答えを誤りと数える | 報酬と同じ採点器（miles の `grade_answer_verl`）を使う |
| README が引用する miles の commit と、固定したイメージの中の commit が違う | イメージを固定しているものを正として表を直した |
| 検証の表の行のうち、同梱した設定から再現できないものがある | 再現できない行を消し、足りなかった GLM-Z1-9B の設定と実行の環境変数を足して測り直した |

**head の Pod の設定**。head のコンテナは GPU を持たず、`libcuda.so.1` も無い。一方で head は CPU を出しているので、Ray は CPU だけを求める制御用の actor（RolloutManager など）を head に置ける。RolloutManager は Megatron 経由で transformer_engine を import するので、KeitaW さんの実行では投入から 46 秒で `OSError: libcuda.so.1` で止まった。KeitaW さんは、head の `num-cpus` を `'0'` にして制御用の actor を head に置かせない回避策を示し、あわせて head にドライバを持たせる `NVIDIA_VISIBLE_DEVICES=none` を提案した。筆者が p5en.48xlarge で `none` を試すと、GPU Operator が CDI を有効にした環境では空のデバイス名に解決され、Pod が起動しなかった。そこで `num-cpus: '0'` を採り、その限界を設定の横に書いた。この設定は head にドライバを与えるものではなく、CPU を求める actor を head から外すだけである。RolloutManager を head に固定するオプションを使うと、CPU を 1 つ求める RolloutManager を CPU の無い head に置けず、進まなくなる。CPU を求めずに head に固定される actor（`MultiLoRAController`）には、この回避策は効かない。同梱のレシピはこの actor を作らないが、レシピを広げるときは、`libcuda.so.1` を使う actor の置き場所を確かめる。

再現できなかった GLM-Z1-9B の行は、実機で原因を探した。4B のレシピはモデルによらないはずだったが、Ray の実行環境の変数に `CUDA_DEVICE_MAX_CONNECTIONS` が無く、テンソル並列を使う 9B だけが Megatron の検査で止まっていた。4B は TP=1 なのでこの検査に届かず、気づかなかった。

## 技術的な要点

- **報酬サービスの誤りの返し方**。誤りを正常な値（0.0）として返すと、学習は止まらずに無意味な信号で進む。誤りはエラーの状態で返して数え、連続したら readiness で外す。liveness につなぐと、1 つの壊れた入力で Pod が再起動を繰り返す。呼び出す側がエラーを再試行しない場合は、1 件の失敗で rollout のバッチ全体が落ちうるので、呼び出す側の動きも確かめる。
- **Pod の選び方**。同じラベルで選ぶ Pod は、同じ名前空間の別のクラスタのものかもしれない。KubeRay のクラスタのラベルまで含めて選ぶ。
- **digest で固定したイメージの版**。ドキュメントの版や commit は、そのイメージの中を実際に見て書く。
- **envsubst の未定義の変数**。未定義の変数を空の文字列に置き換えて成功する。埋める前に、必要な変数がそろっているかを確かめる。
- **検証の表の範囲**。同梱した設定だけで再現できる行に限る。TP=1 の構成で通っても、TP>1 で初めて通る検査がある。

## 技術面接の準備 (FAQ)

この節は、本章の内容を技術面接で聞かれたときに答えるための想定問答である。システム設計面接の流れ (要件の確認、全体の設計、深掘り、トレードオフ、故障と検証) に沿って並べ、各問は英語の質問、日本語の質問、英語の回答、日本語の回答の順に書いた。回答は本章に書いた事実だけを使う。

:::details Q1. Why add miles when the repo already had SLIME?
**質問 (日本語)**

すでに SLIME があるのに、なぜ miles を足したのか。

**Answer (English)**

miles is a fork of SLIME targeting CUDA 13 and NVIDIA Blackwell. I added it so users could choose the stack that fits their GPU. I kept the test case's structure and procedure the same as SLIME's, so the two are easy to compare and users can reuse what they already know.

**回答 (日本語)**

miles は SLIME を CUDA 13 と NVIDIA Blackwell 向けにフォークしたものである。目的は利用者が自分の GPU に合うスタックを選べるようにすることで、そのために miles のテストケースを SLIME と同じ形、同じ手順にし、2 つを直接比べられるようにした。ここではそろっていること自体が価値で、片方を知っていればもう片方も動かせる。
:::

:::details Q2. Walk me through the architecture.
**質問 (日本語)**

全体の構成を説明してほしい。

**Answer (English)**

The base image is `radixark/miles` pinned by digest, with an AWS EFA layer on top. Kubernetes manifests are filled with `envsubst` and consist of a RayCluster, a BuildKit Job that builds the image inside the cluster, a Pod that prepares data, and an optional CPU reward service for the `remote_rm` path. The Ray head is a coordinator without GPUs; GPU workers run SGLang rollout and Megatron training, backed by FSx for Lustre. By default the bundled recipes compute a rule-based reward inside training. `run-on-cluster.sh` submits a recipe as a Ray job from inside the head Pod.

**回答 (日本語)**

ベースのイメージは `radixark/miles` を digest で固定し、その上に AWS の EFA の層を足した。Kubernetes のマニフェストは `envsubst` で値を埋める形で、RayCluster、クラスタの中でイメージを作る BuildKit の Job、データを用意する Pod、`remote_rm` の経路のための任意の CPU の報酬サービスからなる。Ray の head は GPU を持たない調整役で、GPU のワーカーが SGLang の生成と Megatron の学習を受け持ち、FSx for Lustre が支える。同梱のレシピは既定では、ルールに基づく報酬を学習の中で計算する。`run-on-cluster.sh` は head の Pod の中からレシピを Ray の job として投げる。
:::

:::details Q3. How should a reward service report errors?
**質問 (日本語)**

報酬サービスは誤りをどう返すべきか。

**Answer (English)**

Not as a normal value. The first version returned reward 0.0 with HTTP 200 on any grading exception, so a broken grader would let training run to completion on a meaningless zero signal. The merged version returns 503 and counts failures; after 20 consecutive failures by default, readiness turns 503 so the replica is taken out of rotation. The grader runs in a separate process with a time limit. I tied failures to readiness, not liveness, because with liveness one bad input would put the Pod into a restart loop. The caller matters too: if it doesn't retry errors, one failure can drop a whole rollout batch. I didn't verify whether miles' `remote_rm` retries 503, because I didn't run the service as the training reward path.

**回答 (日本語)**

正常な値として返してはいけない。最初の版は、採点の例外をすべて reward 0.0 の 200 で返していたので、採点器が壊れても学習は無意味な 0 の信号で最後まで進む。マージした版は 503 を返して数え、既定で 20 回続けて失敗したら readiness を 503 にしてレプリカを外す。採点器は別のプロセスで動かし、時間の上限を付けた。liveness ではなく readiness につないだのは、liveness だと 1 つの壊れた入力で Pod が再起動を繰り返すからである。呼び出す側も重要で、エラーを再試行しないなら 1 件の失敗で rollout のバッチ全体が落ちうる。miles の `remote_rm` が 503 を再試行するかは、報酬サービスを学習の報酬の経路として動かしていないので確かめていない。
:::

:::details Q4. The head Pod crashed with `libcuda.so.1`. How did you fix it, and what are the limits?
**質問 (日本語)**

head の Pod が `libcuda.so.1` で落ちた。どう直し、限界は何か。

**Answer (English)**

The head container has no GPU and no `libcuda.so.1`, but it advertises CPUs, so Ray can place CPU-only control actors like RolloutManager there. RolloutManager imports transformer_engine through Megatron, so the reviewer's run failed 46 seconds in. The reviewer suggested two options: `num-cpus: '0'` on the head, and `NVIDIA_VISIBLE_DEVICES=none` to give the head a driver. When I tried `none` on p5en.48xlarge with the GPU Operator's CDI enabled, it resolved to an empty device name and the Pod didn't start, so I took `num-cpus: '0'` and documented its limits next to the setting. It doesn't give the head a driver; it only keeps CPU-requesting actors off it. If you pin RolloutManager to the head, it can't be placed and stalls, and actors pinned to the head without requesting CPU, such as `MultiLoRAController`, aren't covered.

**回答 (日本語)**

head のコンテナは GPU も `libcuda.so.1` も持たないが CPU は出しているので、Ray は CPU だけを求める制御用の actor (RolloutManager など) を head に置ける。RolloutManager は Megatron 経由で transformer_engine を import するので、レビューした人の実行は投入から 46 秒で落ちた。提案された選択肢は 2 つで、head の `num-cpus` を `'0'` にすることと、head にドライバを持たせる `NVIDIA_VISIBLE_DEVICES=none` である。p5en.48xlarge で `none` を試すと、GPU Operator が CDI を有効にした環境では空のデバイス名に解決され Pod が起動しなかったので、`num-cpus: '0'` を採り、その限界を設定の横に書いた。この設定は head にドライバを与えず、CPU を求める actor を head から外すだけである。RolloutManager を head に固定すると置けずに進まなくなり、CPU を求めずに head に固定される actor (`MultiLoRAController`) には効かない。
:::

:::details Q5. The 30B MoE ran out of GPU memory. What did you do?
**質問 (日本語)**

30B MoE で GPU のメモリが足りなかった。何をしたか。

**Answer (English)**

With the first recipe, the first optimizer step after rollout ran out of GPU memory. I added `--optimizer-cpu-offload` and related flags to move the optimizer to host memory. The run then completed 100 rollouts in about 5 hours on two nodes with EP only.

**回答 (日本語)**

最初のレシピでは、rollout の後の最初の optimizer の step で GPU のメモリが足りなかった。レシピに `--optimizer-cpu-offload` などを足し、optimizer をホストのメモリに逃がした。その後、EP のみの 2 ノードで 100 回の rollout を約 5 時間で回し切った。
:::

:::details Q6. The review said parts of your results table weren't reproducible. How did you respond?
**質問 (日本語)**

レビューで、結果の表の一部が再現できないと言われた。どう対応したか。

**Answer (English)**

The reviewer was right: some of the nine rows couldn't be reproduced from the bundled config alone. I removed those rows, then built a cluster from scratch in a different region, installed KubeRay, built the image with the in-cluster BuildKit Job, and followed the README's Quick Start from step 0 exactly as written. The final table has four configurations run separately on p5en.48xlarge, with values read back from TensorBoard event files. I state that these show the loop completed, not that it converged. Chasing the missing GLM-Z1-9B row also found a real bug: `CUDA_DEVICE_MAX_CONNECTIONS` was missing from the Ray runtime env, so only the TP=2 model failed Megatron's check. The 4B recipe is TP=1 and never reached that check.

**回答 (日本語)**

指摘は正しく、9 行のうち一部は同梱の設定だけでは再現できなかった。その行を消し、別のリージョンに一からクラスタを作り、KubeRay を入れ、クラスタの中の BuildKit の Job でイメージを作り、README の Quick Start を step 0 から書いてあるとおりに辿った。最後の表は p5en.48xlarge で構成ごとに別々に動かした 4 つで、値は TensorBoard のイベントファイルから読み戻した。この値はループが最後まで回ったことを示すもので、収束を示すものではないと書いた。足りなかった GLM-Z1-9B の行を追ったことで本物の不具合も見つかった。Ray の実行環境の変数に `CUDA_DEVICE_MAX_CONNECTIONS` が無く、TP=2 のモデルだけが Megatron の検査で止まっていた。4B のレシピは TP=1 なのでこの検査に届かなかった。
:::

:::details Q7. The 4B run's AIME score dropped from 0.596 to 0.333. How do you interpret that?
**質問 (日本語)**

4B の実行で AIME の評価が 0.596 から 0.333 に下がった。どう解釈するか。

**Answer (English)**

Over 100 rollouts, the rollout reward stayed roughly constant while the AIME score fell. Mean response length grew, and the share of responses reaching the 16,384-token cap rose from 0.38 to 0.60. That points to a correlation with truncation, but I didn't isolate the cause, and I say so explicitly. I'd treat it as a correlation worth investigating, not a conclusion. Similarly, FlashInfer fusion breaking output with TP plus EP on MoE, and Qwen2.5-72B not fitting in 16 H200s, are documented as constraints in Known Issues, not as results.

**回答 (日本語)**

100 回の rollout で、rollout の reward はほぼ横ばいのまま、AIME の評価が下がった。応答の平均の長さが伸び、上限の 16,384 トークンに届く応答の割合が 0.38 から 0.60 に増えた。切り詰めとの相関は示唆されるが、原因は切り分けておらず、そのことははっきり書いた。結論ではなく、調べる価値のある相関として扱う。同じく、TP と EP を組み合わせた MoE で FlashInfer の融合を有効にすると出力が崩れることと、Qwen2.5-72B が H200 16 枚に収まらないことは、結果ではなく制約として Known Issues に書いた。
:::

:::details Q8. What were the most important review findings beyond correctness?
**質問 (日本語)**

正しく動くかどうか以外で、最も重要だったレビューの指摘は何か。

**Answer (English)**

Most of the 18 findings weren't about whether it ran, but about whether the docs matched reality. `run-on-cluster.sh` picked the first Pod labeled `ray.io/node-type=head` and ignored the namespace, so it could submit to another cluster's head; it now resolves the namespace and selects by `ray.io/cluster`. `evaluate.sh` graded by string match and counted mathematically equal answers as wrong; it now uses the same grader as the reward. The README cited a miles commit different from the one in the pinned image; I treated the image as the source of truth. And `envsubst` replaces undefined variables with empty strings and still succeeds; checking required variables before rendering is one of the lessons I wrote down.

**回答 (日本語)**

18 件の多くは、動くかどうかではなく、書いてあることと実際がずれていないかの指摘だった。`run-on-cluster.sh` は名前空間を読まずに `ray.io/node-type=head` の最初の Pod を選ぶので、別のクラスタの head に投げうる。今は名前空間を決め、`ray.io/cluster` のラベルで選ぶ。`evaluate.sh` は文字列の一致で採点していて、数学的に同じ答えを誤りと数えていた。今は報酬と同じ採点器を使う。README が引用する miles の commit が、固定したイメージの中の commit と違った。イメージを正とした。また `envsubst` は未定義の変数を空の文字列に置き換えて成功する。埋める前に必要な変数がそろっているかを確かめることを、教訓として書いた。
:::

## まとめ

テストケースの価値は、読者が書いてあることを信じて動かせることにある。レビューの 18 件の多くは、動くかどうかではなく、書いてあることと実際がずれていないかの指摘で、それを実機で確かめて直した。
