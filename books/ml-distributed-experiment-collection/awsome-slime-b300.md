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

## まとめ

B300 の対応のつもりで始めた作業は、ほとんどが GPU の世代によらない一般の問題の修正になった。具体的なループの回数と構成の結果は H200 のもので、B300 では一般の修正を確かめた。原因ごとに 1 つの commit に分け、上流の修正と、上流が直すまでの一時的な修正を分けて入れた。
