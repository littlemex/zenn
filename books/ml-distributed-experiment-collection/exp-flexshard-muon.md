---
title: "Muon の更新を速くする FlexShard: 行列ごとに 1 回だけ計算する"
---

# はじめに

Muon は、AdamW に代わる最適化手法として注目されている。AdamW が重みの 1 つ 1 つの値を、その値の勾配の履歴だけから更新するのに対し、Muon は重みの行列ごとに、更新の向きを Newton-Schulz 反復という計算で整えてから更新する。この計算には、行列の全体が要る。

一方、大きなモデルを複数の GPU で学習するときに使う FSDP は、重みの行列を行の方向に切り分けて、各 GPU に少しずつ持たせる。だから Muon を素直に使うと、たとえば各 GPU が自分の担当していない部分まで集めて行列を組み立て、全 GPU がすべての行列について同じ計算を繰り返すことになる。今回の比較では、この素直な方法を比べる相手にした。

[ML infra weekly](https://zenn.dev/tosshi/articles/95edc2cb88f57b) の 2 番目の項目は、[TorchTitan の更新を紹介した PyTorch のブログ](https://pytorch.org/blog/torchtitan-update-new-sota-models-performance-at-scale-titanrl/) が紹介した FlexShard で、この無駄がどれくらい減るかを測ることだった。

# この章で使う言葉

| 言葉 | 意味 |
|---|---|
| 最適化手法 | 勾配（重みをどちらに動かすと損失が増えるか）の逆向きに重みを動かして更新する、計算の決まり。AdamW や Muon がある |
| Newton-Schulz 反復 | 行列の掛け算を数回繰り返して、更新の行列を「どの向きにも同じ大きさで動く」形に近づける計算 |
| FSDP | Fully Sharded Data Parallel。重み、勾配、最適化手法の状態を GPU の間で切り分けて持ち、使うときだけ集める学習の方法 |
| DTensor | PyTorch で、1 つの大きなテンソルを複数の GPU に切り分けて持つための型 |
| FP32、BF16 | 数の表し方。FP32 は 32 ビットで精度が高く、BF16 は 16 ビットで速いが粗い |

# FlexShard の仕組み

FlexShard は、torchtitan v0.4.0 の [`torchtitan/distributed/flex_shard`](https://github.com/pytorch/torchtitan/tree/v0.4.0/torchtitan/distributed/flex_shard) にある部品で、最適化手法が計算する間だけ、重みの持ち方を FSDP の持ち方から別の持ち方に組み替える。その最初の利用者が、分散版の Muon である DistMuon である。

![普通に Muon を使うと、8 枚の GPU すべてが全部の行列を集めて同じ計算をする。DistMuon は行列ごとに担当の GPU を 1 枚決め、その GPU だけが行列を集めて計算し、結果を持ち主に返す](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/flexshard-muon.png)

今回の DistMuon の設定では、行列ごとに担当の GPU を 1 枚決める（`Owned` という持ち方）。FlexShard には、行列を区切って複数の GPU で分担する `BlockShard` という持ち方もあるが、今回は使っていない。担当の GPU だけが、その行列の切れ端を集めて Newton-Schulz 反復を BF16 で計算し、結果を元の持ち主に返す。**8 枚の GPU で行列を分担するので、1 つの行列の計算は 1 回で済む。**さらに、層ごとに束ねて、行列を集める通信と計算を重ねて進める。

# 何をどう測ったか

2026 年 11 月 11 日に、p5e.48xlarge（H200 が 8 枚）1 台で、torchtitan v0.4.0 と PyTorch 2.15.0 を使って測った。Llama 3 8B の各層が持つ 2 次元の重み行列を用意した。32 層のそれぞれに、アテンションの 4 つ（wq、wk、wv、wo）と MLP の 3 つ（w1、w2、w3）があり、合計 224 個、約 70 億個の値である。埋め込みや正規化の重みは、Muon を使わないので除いた。これを FP32 の DTensor として 8 枚の GPU に行方向で切り分け、FSDP と同じ持ち方にした。勾配は乱数で作った。

この上で、3 つの最適化手法の 1 回の更新（`optimizer.step()`）の時間を測った。

| 最適化手法 | 何をするか |
|---|---|
| AdamW | 切り分けたまま、値ごとに更新する。FSDP の普段の使い方 |
| Muon、全部集める | 各 GPU がすべての勾配を集めて行列を組み立て、PyTorch の `torch.optim.Muon` で全部の行列を計算する |
| DistMuon | FlexShard で、行列ごとに担当の GPU を決めて計算する |

各手法を 3 回空回ししてから 10 回測った。1 回ごとに全 GPU の足並みをそろえ、一番遅い GPU の時間を記録した。測る前に、torchtitan 自身の DistMuon の GPU テストが通ることも確かめた（7 件すべて合格）。

# 結果

| 最適化手法 | 1 回の更新（中央値） | GPU 1 枚あたりの最大メモリ |
|---|---|---|
| AdamW | 18.6 ms | 16.3 GiB |
| Muon、全部集める | 1,117.6 ms | 85.0 GiB |
| DistMuon | 237.8 ms | 10.7 GiB |

比べる相手は、自分で書いた素直な実装（全部集める Muon）であり、FlexShard を使わない場合の最善の実装ではない点に注意してほしい。DistMuon は、この相手の約 21 % の時間で 1 回の更新を終え、メモリは約 8 分の 1 で済んだ。全部集める方法では、8 枚の GPU がそれぞれ全部の行列を持ち、同じ計算を 8 回繰り返している。DistMuon では、それぞれの行列の計算が 1 回になり、各 GPU は担当の行列だけを組み立てればよい。差の主な理由はこの 2 点だと考えられる。ただし、DistMuon は Newton-Schulz 反復を BF16 で計算し、全部集める Muon は FP32 のまま計算しているので、数の表し方の違いも速さに効いている。通信と計算の内訳までは測っていないので、計算の回数が 8 分の 1 なのに速さが約 4.7 倍にとどまった理由も、この測定からは分けられない。

それでも、DistMuon の 1 回の更新は、AdamW の約 13 倍の時間がかかった。AdamW は値ごとの計算で済むのに対し、Muon は Newton-Schulz 反復で大きな行列の掛け算を何度も繰り返すためと考えられる。

# 分かったこと、分からなかったこと

分かったことは、この 8 枚の H200 と Llama 3 8B の形では、行列を全部集める素直な方法は時間でもメモリでも重く、FlexShard の DistMuon にすると 1 回の更新が約 4.7 倍速くなり、メモリも大きく減ったことである。

分からなかったことは 2 つある。1 つは、2 つの Muon が同じ更新を出しているかである。torchtitan 自身の GPU テストは、DistMuon と普通の Muon の結果が一致することを確かめているが、今回のベンチマークでは結果を突き合わせていない。もう 1 つは、学習全体の速さへの影響である。今回は最適化手法の更新だけを、乱数の勾配で切り出して測った。1 回の学習ステップのうち更新が何割を占めるかは、モデルの大きさとバッチの大きさで決まり、この測定からは分からない。PyTorch のブログが示した最適化の時間の最大 79 % の短縮は、DeepSeek-V3 を 256 枚の H100 で学習したとき、FSDP だけを使う同等の実装と比べた数字で、1 台 8 枚の今回とは比べられない。torchtitan v0.4.0 の README では、DistMuon を学習に組み込んだ例として Kimi の試験用の設定が挙げられている。Llama で学習を最後まで流す設定を作ることは、次の課題である。

# 参考資料

- [ML infra weekly](https://zenn.dev/tosshi/articles/95edc2cb88f57b)
- [TorchTitan Update: New SOTA Models, Performance at Scale, TitanRL](https://pytorch.org/blog/torchtitan-update-new-sota-models-performance-at-scale-titanrl/)
- [torchtitan v0.4.0 の FlexShard](https://github.com/pytorch/torchtitan/tree/v0.4.0/torchtitan/distributed/flex_shard)
- [実験のコードと結果](https://github.com/littlemex/distributed-ai/tree/b8c40f067d9a15db05d605f7393d3653e72c6e6c/investigations/2026-11-10-torchtitan-flexshard-muon)

# まとめ

Llama 3 8B と同じ形の 224 個の行列を 8 枚の GPU に切り分けて持つと、行列を全部集めて Muon を計算する方法は 1 回の更新に 1,117.6 ms と 85.0 GiB を使った。FlexShard の DistMuon は、行列ごとに担当の GPU を決めて 1 回だけ計算し、237.8 ms と 10.7 GiB で済んだ。それでも AdamW の 18.6 ms の約 13 倍で、学習全体での効果はモデルとバッチの大きさしだいである。
