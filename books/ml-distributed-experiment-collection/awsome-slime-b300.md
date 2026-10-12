---
title: "Awsome: SLIME の GRPO を B300 と H200 で動かす"
free: false
---

## はじめに

本章では、SLIME の GRPO のテストケースを、NVIDIA H200（p5en）と B300（p6-b300）の上で学習まで通した [Issue #1163](https://github.com/awslabs/awsome-distributed-ai/issues/1163) と [PR #1164](https://github.com/awslabs/awsome-distributed-ai/pull/1164) をまとめる。この PR は 1 つの大きな変更ではなく、原因ごとに分けた小さな修正を 1 本のブランチに積んだもの（epic）である。

:::details SLIME と GRPO
GRPO（Group Relative Policy Optimization）は、同じ問題に対して複数の答えを生成し、そのグループの中での報酬の相対的な良さで方策を更新する強化学習の手法である。[SLIME](https://github.com/THUDM/slime) は、生成（rollout）を SGLang、学習を Megatron-LM に任せ、Ray でそれらを動かす強化学習のフレームワークである。1 回のループは、生成、報酬の計算、学習の 1 ステップ、学習した重みを SGLang に戻す同期、の順に進む。
:::

## Issue と PR の概要

| 項目 | 内容 |
| --- | --- |
| 解決したいこと | テストケースの README どおりに動かしても、学習が始まらない、または途中で止まる |
| やったこと | 止まる原因を 1 つずつ特定し、原因ごとに 1 つの commit で直した。そのうち 4 つは GPU の世代によらない一般の問題だった |
| テスト | EKS の p5en.48xlarge の 2 ノード（H200 16 枚）で、4B の構成で GRPO のループを 3 回、30B MoE の構成で 1 回、最後まで回した。一般の修正は p6-b300.48xlarge の 2 ノードでも確かめた（Issue #1163） |
| マージ | [d8243f1](https://github.com/awslabs/awsome-distributed-ai/commit/d8243f1f5269982d155129144d18090d49a9cb65) |

## 何を解決しようとしたか

ベースのイメージ（NGC の PyTorch 26.02、CUDA 13、SGLang 0.5.12）はすでに Blackwell に対応していたので、B300 を足すのに要るのは設定の値だけのはずだった。ところが動かすと、B300 に固有ではない問題が順に現れた。H100 の p5 で README どおりに動かしても同じように止まるものが多く、それを原因ごとに分けて直した。

## どうやったか

![レシピが学習の起動を 1 つの文字列にまとめて ray job submit -- bash -c に渡すと、Ray が外側の sh -c で先に展開してしまい MODEL_ARGS が空になる。SLIME と同じく、モデルのスクリプトを読み込む起動用のスクリプトを用意して argv で渡す形にした。ほかに 4 つの修正がある](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/awsome-slime-b300/model-args.png)

最初に止まったのは、学習の起動の直後だった。`train.py` が `hidden_size None` で止まる。レシピは `train.py` の呼び出しを 1 つの文字列にまとめ、`ray job submit ... -- bash -c "${TRAIN_CMD}"` で渡し、モデルの設定を `\${MODEL_ARGS[@]}` として後で展開させようとしていた。Ray 2.55.1 の `ray job submit` は、`--` より後ろを `subprocess.list2cmdline` でつなぎ直し、`Popen(shell=True)` で外側の `/bin/sh -c` から動かす。そのため、内側の `bash -c` がモデルのスクリプトを読み込む前に、外側のシェルが `${MODEL_ARGS[@]}` を展開し、まだ定義されていないので空になる。

直し方は、さらにエスケープを重ねることではなく、シェルの境界をまたがないことである。SLIME 自身の起動スクリプトは、モデルのスクリプトを読み込んだのと同じシェルで `${MODEL_ARGS[@]}` を展開し、argv として渡している。それに合わせて、[grpo_launch.sh](https://github.com/awslabs/awsome-distributed-ai/blob/d8243f1f5269982d155129144d18090d49a9cb65/3.test_cases/pytorch/slime/recipe/launcher/grpo_launch.sh) を足した。このスクリプトはモデルのスクリプトを読み込み、`MODEL_ARGS` が空なら止まり、`exec python3 train.py "${MODEL_ARGS[@]}" "$@"` で起動する。

ほかの修正は次のとおりである。

| 症状 | 原因 | 修正 |
| --- | --- | --- |
| rollout の HTTP サーバーが立たず、学習が待ち続ける | `--sglang-log-level WARN` を uvicorn が受け付けない（小文字だけ） | `warning` に変えた |
| NGC 26.02 と CUDA 13 の構成で、offload が有効（既定）なとき学習のワーカーが起動の直後に死ぬ | SLIME が `LD_PRELOAD` する `torch_memory_saver` の `.so` が CUDA 12 向け | ビルドの時に当てるパッチで、`torch_memory_saver` 自身の CUDA に合わせた選び方に `.so` を任せる。上流にも修正を出した |
| Megatron の初期化で止まる | `sglang[all]` が numpy 2 系を連れてくる | `numpy<2` を固定した（上流の SLIME の Dockerfile と同じ） |
| 30B MoE の構成だけが止まる | `torch_dist` への変換に要る `mbridge` が無い、GPU の無い Ray のドライバで Megatron の `validate_args` が GPU を調べる、TP や CP が 2 以上のときに Megatron が求める `CUDA_DEVICE_MAX_CONNECTIONS=1` が無い | `mbridge` を入れ、GPU が無いときだけ GPU の問い合わせを飛ばすパッチを当て、環境変数を足した。あわせて、SGLang 0.5.12 では黙って無視される `--sglang-enable-ep-moe` を外した |

## なぜそうしたか

**起動用のスクリプトにした理由**。比べた方向は、レシピの側でクォートを重ねる方法で、起動用のスクリプトを増やさずに済む。筆者は、`ray job submit` が `--` の後ろを `list2cmdline` でつなぎ直し、外側の `/bin/sh` がレシピのクォートによらず `${MODEL_ARGS[@]}` を展開するので、エスケープでは直らないと判断した。SLIME を使わない小さな再現でも、`bash -c` を 1 段で動かすと引数は 25 個届き、`ray job submit -- bash -c` を通すと 0 個になった。起動用のスクリプトを 1 本保守する代わりに、配列が Ray の境界をまたがなくなる。

**世代ごとのディレクトリを作らない理由**。`b300/` のように世代ごとのディレクトリを作ると、違いが 1 か所に見える。ただ、B300 と H200 の違いはほぼ環境変数とフラグの値だけで、レシピを複製すると世代ごとに中身がずれていく。そこで筆者は、1 つのレシピに値の違いだけを持たせる形を提案し、どちらが良いかをメンテナに尋ねた。この PR には B300 向けの値は入っておらず、B300 の対応はレビューした allela-roy さんが引き取った。

**SGLang を下げない理由**。MoE の問題を避けるために SGLang を下げる方向もあった。SGLang 0.5.9 は torch 2.9.1 を求め、ベースのイメージが頼る torch 2.11 と CUDA 13 の組み合わせを壊すので採らなかった。

**1 本の大きな PR にしない理由**。一般の修正と 30B MoE だけの修正を混ぜるとレビューが難しくなるので、原因ごとの小さな commit を 1 本の epic の PR に積んだ。

**パッチの形**。ビルドの時に当てるパッチは、対象の上流のコードがまだ問題を持っているときだけ変更を加え、上流が直した後は何もしない形（self-neutralizing）にした（[apply_slime_patches.py](https://github.com/awslabs/awsome-distributed-ai/blob/d8243f1f5269982d155129144d18090d49a9cb65/3.test_cases/pytorch/slime/patches/apply_slime_patches.py)）。SLIME のフォークを参照せずに一時的な修正を当てられる。

**取り下げた見立て**。H200 で遅くなるのを防ぐつもりだった CUDA graph のフラグは、遅くなることを再現できなかったので外した。「30B MoE の重みの同期が 400 を返す」という見立ても取り下げた。SGLang の既定の設定では 400 は起きず、30B の起動を妨げていた要因の 1 つは、GPU を割り当てない Ray のドライバの上で Megatron の `validate_args` が GPU を調べることだった。確かめられなかった設定は、予防のためでも入れないことにした。

## テストをどうしたか

以下の数値は、すべて EKS の p5en.48xlarge の 2 ノード（H200 16 枚）での結果で、このブランチの Dockerfile から作ったイメージを使った。一般の修正は p6-b300.48xlarge の 2 ノードでも確かめたと Issue #1163 に書いた。B300 については、H200 と同じループの回数と 30B の構成の結果はここでは示さない。組み込みの報酬を使う Qwen3-4B の colocated の構成は、生成、参照と方策の log 確率、Megatron の学習の 1 ステップ、SGLang への重みの同期までの GRPO のループを 3 回最後まで回した。ノードの間の NCCL は EFA を使った（`Selected provider is efa, fabric is efa-direct (found 16 nics)`）。別の実行では、`RM_TYPE=remote_rm` に変えて 4B のレシピを動かし直し、CPU のノードの HTTP の報酬サービスへの 250 回以上の `POST /score` がすべて 200 で返ることを確かめた。Qwen3-30B-A3B の disaggregated の構成（学習 12 枚、生成 4 枚）は、GRPO のループを 1 回最後まで回した。レビューした allela-roy さんも、自分のクラスタで最後まで動くことを確かめた。

## 技術的な要点

- **モデルの引数を定義して展開するシェル**。今回の Ray 2.55.1 とレシピの組み合わせでは、モデルのスクリプトを読み込む前に外側のシェルが `${MODEL_ARGS[@]}` を展開していた。配列を定義するシェルと展開するシェルを同じにする。`ray job submit -- bash -c "..."` のような二重のシェルを避け、配列を展開するシェルと定義するシェルを同じにする。
- **エラーの文面と原因の距離**。エラーの文面（`hidden_size None`）と原因（シェルの展開の順）は遠いことがある。上流のソースを読み、フレームワークを使わない小さな再現で仕組みを確かめる。
- **GPU の世代と、ライブラリと設定の条件の切り分け**。uvicorn のログの指定、Megatron と numpy 2 系の組み合わせ、CUDA 13 の offload で読み込む CUDA 12 向けの `.so` の問題は、GPU の世代によらない。B300 で問題を見つけたときも、GPU の世代だけでなく、ライブラリの版と有効な設定を確かめる。
- **上流の修正と一時的な修正を分ける**。上流で直すべき問題は上流に出し、テストケースの側の一時的なパッチは、対象の上流の修正が入った後には変更を加えない形にする。

## 技術面接の準備 (FAQ)

この節は、本章の内容を技術面接で聞かれたときに答えるための想定問答である。システム設計面接の流れ (要件の確認、全体の設計、深掘り、トレードオフ、故障と検証) に沿って並べ、各問は英語の質問、日本語の質問、英語の回答、日本語の回答の順に書いた。回答は本章に書いた事実だけを使う。

:::details Q1. You set out to add B300 support. What actually happened?
**質問 (日本語)**

B300 の対応を足すつもりで始めた。実際には何が起きたか。

**Answer (English)**

The base image, NGC PyTorch 26.02 with CUDA 13 and SGLang 0.5.12, already supported Blackwell, so I expected B300 to need only configuration values. When I ran it, problems that weren't B300-specific appeared one after another, and many of them reproduce on H100 p5 by following the README. So the work became a series of generic fixes, one commit per root cause, stacked on one epic branch. Four of the fixes were general problems independent of GPU generation, and the rest were specific to the 30B MoE configuration.

**回答 (日本語)**

ベースのイメージ (NGC の PyTorch 26.02、CUDA 13、SGLang 0.5.12) はすでに Blackwell に対応していたので、B300 に要るのは設定の値だけのはずだった。動かすと、B300 に固有ではない問題が順に現れ、その多くは H100 の p5 で README どおりに動かしても同じように止まるものだった。そのため作業は、原因ごとに 1 つの commit に分けた一般の修正を 1 本の epic のブランチに積むものになった。そのうち 4 つは GPU の世代によらない一般の問題で、残りは 30B MoE の構成に固有のものだった。
:::

:::details Q2. Training died with `hidden_size None`. How did you find the root cause?
**質問 (日本語)**

学習が `hidden_size None` で止まった。原因をどう突き止めたか。

**Answer (English)**

The error message was far from the cause. The recipe packed the `train.py` call into one string, passed it through `ray job submit ... -- bash -c "${TRAIN_CMD}"`, and escaped `${MODEL_ARGS[@]}` to be expanded later. I read Ray's source: Ray 2.55.1 rejoins everything after `--` with `subprocess.list2cmdline` and runs it with `Popen(shell=True)` under an outer `/bin/sh -c`. So the outer shell expanded `${MODEL_ARGS[@]}` before the inner bash had sourced the model script, and it came out empty. I confirmed the mechanism with a minimal reproduction without SLIME: one level of `bash -c` delivered 25 arguments, and going through `ray job submit -- bash -c` delivered 0.

**回答 (日本語)**

エラーの文面と原因は遠かった。レシピは `train.py` の呼び出しを 1 つの文字列にまとめ、`ray job submit ... -- bash -c "${TRAIN_CMD}"` で渡し、`${MODEL_ARGS[@]}` をエスケープして後で展開させようとしていた。Ray のソースを読むと、Ray 2.55.1 は `--` より後ろを `subprocess.list2cmdline` でつなぎ直し、外側の `/bin/sh -c` から `Popen(shell=True)` で動かす。そのため、内側の bash がモデルのスクリプトを読み込む前に外側のシェルが `${MODEL_ARGS[@]}` を展開し、空になった。SLIME を使わない小さな再現で仕組みを確かめ、`bash -c` を 1 段で動かすと引数は 25 個届き、`ray job submit -- bash -c` を通すと 0 個になった。
:::

:::details Q3. Why add a launcher script instead of fixing the quoting?
**質問 (日本語)**

なぜクォートを直さずに起動用のスクリプトを足したのか。

**Answer (English)**

The alternative, more escaping in the recipe, avoids adding a file. But it would depend on how Ray 2.55.1 rejoins arguments with `list2cmdline` and how the outer `/bin/sh` expands them, which is Ray's internal behavior. I judged that escaping couldn't fix it reliably. The real fix is not to cross the shell boundary at all. SLIME's own launcher expands `${MODEL_ARGS[@]}` in the same shell that sourced the model script and passes it as argv, so I followed that: `grpo_launch.sh` sources the model script, fails if `MODEL_ARGS` is empty, and runs `exec python3 train.py "${MODEL_ARGS[@]}" "$@"`. The cost is one more script to maintain; the benefit is that the array never crosses Ray's boundary.

**回答 (日本語)**

比べた方向はレシピの側でクォートを重ねる方法で、ファイルを増やさずに済む。ただ、それは Ray 2.55.1 が `list2cmdline` で引数をつなぎ直し、外側の `/bin/sh` が展開するという Ray の内部の挙動に頼ることになる。エスケープでは確実には直らないと判断した。本当の直し方は、シェルの境界をまたがないことである。SLIME 自身の起動スクリプトは、モデルのスクリプトを読み込んだのと同じシェルで `${MODEL_ARGS[@]}` を展開して argv で渡しているので、それに合わせた。`grpo_launch.sh` はモデルのスクリプトを読み込み、`MODEL_ARGS` が空なら止まり、`exec python3 train.py "${MODEL_ARGS[@]}" "$@"` で起動する。代償は保守するスクリプトが 1 本増えることで、得るものは配列が Ray の境界をまたがなくなることである。
:::

:::details Q4. What were the other failures, and how did you classify them?
**質問 (日本語)**

ほかの失敗は何で、どう分類したか。

**Answer (English)**

I separated GPU generation from library versions and enabled features. uvicorn only accepts a lowercase log level, so `WARN` became `warning`. With CUDA 13 and offload enabled, SLIME preloaded a CUDA 12 build of `torch_memory_saver`, so a build-time patch lets it pick its own `.so`, and I sent that upstream. `sglang[all]` pulled in numpy 2, which broke Megatron's init, so I pinned `numpy<2`. Those are generic. The 30B MoE also needed `mbridge`, a patch to skip a GPU query on the GPU-less Ray driver, and `CUDA_DEVICE_MAX_CONNECTIONS=1`. None of these are B300-specific.

**回答 (日本語)**

GPU の世代と、ライブラリの版、有効な設定を切り分けた。uvicorn はログの指定を小文字しか受け付けないので、`WARN` を `warning` にした。CUDA 13 で offload を有効にすると、SLIME が CUDA 12 向けの `torch_memory_saver` を `LD_PRELOAD` していたので、ビルドの時のパッチで `.so` の選び方を `torch_memory_saver` 自身に任せ、上流にも修正を出した。`sglang[all]` が numpy 2 系を連れてきて Megatron の初期化が止まったので、`numpy<2` を固定した。ここまでが一般の修正である。30B MoE では、ほかに `mbridge`、GPU の無い Ray のドライバで GPU の問い合わせを飛ばすパッチ、`CUDA_DEVICE_MAX_CONNECTIONS=1` が要った。どれも B300 に固有ではない。
:::

:::details Q5. Why not create a `b300/` directory per GPU generation?
**質問 (日本語)**

なぜ GPU の世代ごとに `b300/` のようなディレクトリを作らなかったのか。

**Answer (English)**

A per-generation directory makes the differences visible in one place. But the differences between B300 and H200 were almost entirely environment variables and flag values, and duplicated recipes drift apart over time. So I proposed one recipe that carries only the value differences, and asked the maintainers which they preferred. This PR doesn't contain B300 values; the B300 work was picked up by the reviewer.

**回答 (日本語)**

世代ごとのディレクトリにすると、違いが 1 か所に見える。ただ、B300 と H200 の違いはほぼ環境変数とフラグの値だけで、レシピを複製すると世代ごとに中身がずれていく。そこで、1 つのレシピに値の違いだけを持たせる形を提案し、どちらが良いかをメンテナに尋ねた。この PR に B300 向けの値は入っておらず、B300 の対応はレビューした人が引き取った。
:::

:::details Q6. Why not downgrade SGLang, and how did you structure the patches?
**質問 (日本語)**

なぜ SGLang を下げず、パッチはどういう形にしたのか。

**Answer (English)**

Downgrading SGLang to avoid the MoE issue was an option, but SGLang 0.5.9 requires torch 2.9.1, which would break the torch 2.11 and CUDA 13 combination the base image relies on. For temporary fixes I used build-time patches that change the upstream code only while it still has the problem, and do nothing once upstream is fixed. That way the test case doesn't depend on a SLIME fork. Fixes that belong upstream go upstream, and the test case keeps only the temporary patches.

**回答 (日本語)**

MoE の問題を避けるために SGLang を下げる方向もあったが、SGLang 0.5.9 は torch 2.9.1 を求め、ベースのイメージが頼る torch 2.11 と CUDA 13 の組み合わせを壊すので採らなかった。一時的な修正は、ビルドの時に当てるパッチを、対象の上流のコードがまだ問題を持っているときだけ変更を加え、上流が直した後は何もしない形 (self-neutralizing) にした。こうすれば SLIME のフォークに頼らずに済む。上流で直すべきものは上流に出し、テストケースには一時的なパッチだけを置く。
:::

:::details Q7. Did you get anything wrong along the way?
**質問 (日本語)**

途中で間違えた見立てはあったか。

**Answer (English)**

Two. I had added a CUDA graph flag to prevent a slowdown on H200, but I couldn't reproduce the slowdown, so I removed it. I also suspected that the 30B MoE weight sync returned HTTP 400, but with SGLang's default settings the 400 didn't occur; one of the real blockers was Megatron's `validate_args` querying GPUs on a Ray driver that has none. My rule since then is not to ship a setting I couldn't verify, even as a precaution.

**回答 (日本語)**

2 つある。H200 で遅くなるのを防ぐつもりで CUDA graph のフラグを入れたが、遅くなることを再現できなかったので外した。30B MoE の重みの同期が 400 を返すという見立ても立てたが、SGLang の既定の設定では 400 は起きず、30B を妨げていた要因の 1 つは、GPU を持たない Ray のドライバの上で Megatron の `validate_args` が GPU を調べることだった。それ以来、確かめられなかった設定は予防のためでも入れないことにしている。
:::

:::details Q8. What exactly did you test, and on which hardware?
**質問 (日本語)**

何を、どのハードウェアで試したのか。

**Answer (English)**

All the numbers are from two EKS p5en.48xlarge nodes, 16 H200 GPUs, using an image built from this branch's Dockerfile. Qwen3-4B colocated with the built-in reward ran three complete GRPO loops: generation, reference and policy log-probs, one Megatron training step, and weight sync to SGLang. Inter-node NCCL used EFA. In a separate run with `RM_TYPE=remote_rm`, more than 250 `POST /score` calls to an HTTP reward service on a CPU node all returned 200. Qwen3-30B-A3B disaggregated, 12 GPUs for training and 4 for generation, completed one loop. I verified the generic fixes on two p6-b300.48xlarge nodes too, but I don't claim the same loop counts or the 30B result for B300.

**回答 (日本語)**

数値はすべて EKS の p5en.48xlarge の 2 ノード (H200 16 枚) で、このブランチの Dockerfile から作ったイメージを使ったものである。組み込みの報酬を使う Qwen3-4B の colocated は、生成、参照と方策の log 確率、Megatron の学習の 1 ステップ、SGLang への重みの同期までの GRPO のループを 3 回回した。ノードの間の NCCL は EFA を使った。別の実行で `RM_TYPE=remote_rm` にし、CPU のノードの HTTP の報酬サービスへの 250 回以上の `POST /score` がすべて 200 で返ることを確かめた。Qwen3-30B-A3B の disaggregated (学習 12 枚、生成 4 枚) はループを 1 回回した。一般の修正は p6-b300.48xlarge の 2 ノードでも確かめたが、B300 で同じループの回数や 30B の結果を得たとは言わない。
:::

## まとめ

B300 の対応のつもりで始めた作業は、ほとんどが GPU の世代によらない一般の問題の修正になった。具体的なループの回数と構成の結果は H200 のもので、B300 では一般の修正を確かめた。原因ごとに 1 つの commit に分け、上流の修正と、上流が直すまでの一時的な修正を分けて入れた。
