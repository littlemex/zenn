---
title: "学習と推論の確率は本当に一致するか: TitanRL の batch-invariant モード"
---

# はじめに

大規模言語モデルの強化学習では、2 つのプログラムが同じモデルを動かす。1 つは推論エンジン（今回は vLLM）で、モデルに答えを書かせ、書いた各トークンの確率を報告する。もう 1 つは学習側のトレーナーで、同じ答えについて同じ確率を計算し直し、それをもとにモデルを更新する。この 2 つの確率は、同じ重みと同じ入力なら、本来は一致するはずである。

ところが実際には、少しずつずれる。行列の掛け算や合計を GPU で計算するとき、どの順番で足し合わせるかが、一度に処理する入力の数（バッチの大きさ）によって変わり、浮動小数点の丸め方が変わるからである。推論側と学習側ではバッチの組み方が違うので、同じ入力でも答えがわずかに違う。このずれがあると、学習がおかしくなったときに、プログラムの誤りなのか、計算の誤差なのかを見分けにくい。

[ML infra weekly](https://zenn.dev/tosshi/articles/95edc2cb88f57b) の 3 番目の項目は、[TorchTitan の更新を紹介した PyTorch のブログ](https://pytorch.org/blog/torchtitan-update-new-sota-models-performance-at-scale-titanrl/) が紹介した TitanRL（torchtitan の強化学習の部品）の batch-invariant モードで、このずれが本当になくなるかを確かめることだった。

# この章で使う言葉

| 言葉 | 意味 |
|---|---|
| トークン | モデルが文章を扱う単位。単語や単語の一部 |
| 対数確率 | トークンが選ばれる確率の対数。確率の掛け算を足し算で扱えるので、学習ではこちらを使う。差が 0.89 なら、確率がおよそ 2.4 倍違う |
| ビット単位で一致 | 計算結果のコンピューター上の表現が、最後の桁まで完全に同じこと |
| GRPO | 同じ問題に複数の答えを書かせ、答えどうしの良し悪しを比べてモデルを更新する強化学習の方法（Group Relative Policy Optimization） |
| テンソル並列 | 1 つの層の計算を複数の GPU に分けて受け持たせる方法 |
| Qwen3-0.6B | Alibaba の Qwen3 という言語モデルのうち、重みが約 6 億個の小さいもの |

# batch-invariant モードの仕組み

batch-invariant モードは、学習側と推論側の両方で、足し合わせる順番がバッチの大きさに左右されない計算に差し替える。torchtitan v0.4.0 の [BITWISE_PARITY.md](https://github.com/pytorch/torchtitan/blob/v0.4.0/torchtitan/rl/BITWISE_PARITY.md) によると、差し替えるのは主に次のものである。

![学習側と推論側の両方で、行列の掛け算、ソフトマックス、アテンション、GPU 間の合計を、順番が固定された計算に差し替える。推論側は確率の計算そのものも学習側と同じ関数を使う](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/titanrl-bi.png)

| 計算 | 普段の動き | batch-invariant モードでの動き |
|---|---|---|
| 行列の掛け算、log-softmax、平均 | 入力の形に合わせて、速い区切り方を毎回選ぶ | 区切り方を固定した Triton のカーネル（[batch_invariant_ops](https://github.com/thinking-machines-lab/batch_invariant_ops)）を使う |
| アテンション（FlashAttention 3） | 系列の長さに合わせて計算を分割する | 分割しない（`num_splits=1`） |
| GPU 間の合計（NCCL） | 状況に合わせて合計の順番を選ぶ | 1 本の木構造の順番に固定する |
| 低い精度での合計、TF32 | 速さのために使う | 使わない |
| 推論側の確率の計算 | vLLM の専用カーネル | 学習側と同じ関数を呼ぶ |

設定は、学習側と推論側の両方に `DebugConfig(batch_invariant=True, deterministic=True)` を書く。ただし、batch_invariant_ops を入れておくこと、学習側と推論側のテンソル並列の数をそろえること、系列並列を使わないこと、両側の計算を BF16 にそろえることなど、満たすべき条件がある。今回は、これらを満たした torchtitan の設定をそのまま使った。

# 何をどう測ったか

2026 年 11 月 10 日に、p5e.48xlarge（H200 が 8 枚）1 台で測った。torchtitan v0.4.0 に入っている `rl_grpo_qwen3_0_6b_varlen_batch_invariant` をそのまま使った。Qwen3-0.6B に、文字列を並べ替える課題（alphabet sort）を GRPO という強化学習の方法で解かせる。8 枚の GPU のうち 2 枚を学習側（テンソル並列 2）、残り 6 枚を推論側の 3 台（それぞれテンソル並列 2）に割り当て、推論した答えをすぐに学習に使う、完全なオンポリシーの設定（`target_offpolicy_steps=0`）である。

この設定をそのまま使う回（`bi_on`）と、学習側と推論側の `DebugConfig` だけを既定に戻した回（`bi_off`）を、それぞれ 30 ステップ流した。モデル、データ、構成、乱数の初期値の設定は同じである。比べたのは、トレーナーが毎ステップ記録する次の値である。

| 記録される値 | 意味 |
|---|---|
| `bit_wise/logprob_diff/max` | 推論側と学習側で、同じトークンの対数確率の差の最大 |
| `bit_wise/logprob_diff/mean` | 同じ差の平均 |
| `bit_wise/ratio_tokens_different/mean` | 対数確率の差の絶対値が 0.000001 を超えたトークンの割合 |

torchtitan v0.4.0 の強化学習の環境を入れるスクリプト（`scripts/install_rl_env.sh`）は、PyTorch と vLLM の開発版（nightly）を同じ日付でそろえて入れる作りになっていて、リリース版に固定できない。実際に入った版は、結果と一緒に記録した。

# 結果

| 実行 | 差の最大（30 ステップの最大） | 差の平均（30 ステップの平均） | 一致しなかったトークン | 1 ステップの時間（中央値） |
|---|---|---|---|---|
| `bi_on` | 0.0 | 0.0 | 0.0 % | 3.4 秒 |
| `bi_off` | 0.89 | -0.0003 | 42.6 % | 4.4 秒 |

batch-invariant モードを入れると、30 ステップのすべてで差の最大が 0 だった。つまり、損失の計算に使われた全トークンで、推論側と学習側の対数確率がビット単位まで一致した。モードを切ると、42.6 % のトークンで値が違い、1 つのトークンで最大 0.89 の差があった。差の平均は、学習側から推論側を引いた値を符号付きのまま平均したものなので（最大は絶対値をとる）、正と負のずれが打ち消し合い、ほぼ 0 になった。**平均だけを見ると一致しているように見えてしまうので、一致を確かめるときは最大と割合を見る。**

# 分かったこと、分からなかったこと

分かったことは、torchtitan v0.4.0 の batch-invariant モードは、少なくともこの設定では、うたわれているとおり推論と学習の確率をビット単位で一致させることである。強化学習で学習が崩れたとき、このモードで流し直して差が 0 になるかを見れば、推論と学習の計算のずれという原因の 1 つを外せる。報酬の設計や学習の手法そのものの問題は、別に調べる必要がある。

分からなかったことは速さへの影響である。torchtitan の文書は、Qwen3-8B を 8 枚の H100 で動かした測定で、決まった順番のカーネルにすると推論の 1 トークンと学習の計算がそれぞれ 2.4 倍と 2.9 倍遅くなった一方、1 ステップ全体の時間はほぼ変わらなかったと書いている。計算以外の待ち時間が大きい課題では、遅くなった分が表に出にくいということである。今回はむしろ `bi_on` のほうが 1 ステップ短かった。Qwen3-0.6B の小さな課題では、1 ステップの時間の大半が計算ではなく、推論と学習のやり取りの待ち時間である可能性があるが、内訳は測っていない。2 つの実行の順番（`bi_on` を先に流した）も違うので、この差を速さの比較として読んではいけない。

# 参考資料

- [ML infra weekly](https://zenn.dev/tosshi/articles/95edc2cb88f57b)
- [TorchTitan Update: New SOTA Models, Performance at Scale, TitanRL](https://pytorch.org/blog/torchtitan-update-new-sota-models-performance-at-scale-titanrl/)
- [torchtitan v0.4.0 の BITWISE_PARITY.md](https://github.com/pytorch/torchtitan/blob/v0.4.0/torchtitan/rl/BITWISE_PARITY.md)
- [実験のコードと結果](https://github.com/littlemex/distributed-ai/tree/b8c40f067d9a15db05d605f7393d3653e72c6e6c/investigations/2026-11-10-torchtitan-rl-batch-invariant)

# まとめ

TitanRL の batch-invariant モードを入れると、Qwen3-0.6B の GRPO 30 ステップで、推論側と学習側の対数確率が全トークンでビット単位まで一致した。モードを切ると 42.6 % のトークンがずれ、最大の差は 0.89 だった。差の平均はどちらもほぼ 0 なので、一致を確かめるときは平均ではなく、最大と一致しないトークンの割合を見る。
