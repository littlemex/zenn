---
title: "この本の読み方と実験環境"
---

# はじめに

この本は、分散学習と分散推論の新しい手法を、実機で動かして確かめた記録である。新しい手法は、発表された記事やリリースノートを読むだけでは、手元の環境で同じように動くか、どこで詰まるか、数字がどれくらい出るかが分からない。そこで、気になった手法を 1 つずつ動かし、仕組み、測り方、結果、分かったことの順にまとめていく。

各章のきっかけは、分散学習と推論の基盤に関する記事や論文を毎週まとめている [ML infra weekly](https://zenn.dev/tosshi/articles/95edc2cb88f57b) である。その中の「次に確かめること」の表から、実機で確かめる価値があるものを選んだ。

| 章 | ML infra weekly の項目 | 何を確かめたか |
|---|---|---|
| [チェックポイントの保存で学習はどれだけ止まるか](https://zenn.dev/tosshi/books/ml-distributed-experiment-collection/viewer/exp-torchtitan-checkpoint) | 4 | torchtitan の 4 種類の保存方法で、学習が止まる時間と、保存が本当に終わるまでの時間 |
| [学習と推論の確率は本当に一致するか](https://zenn.dev/tosshi/books/ml-distributed-experiment-collection/viewer/exp-titanrl-batch-invariant) | 3 | TitanRL の batch-invariant モードで、学習側と推論側が同じ確率を出すか |
| [Muon の更新を速くする FlexShard](https://zenn.dev/tosshi/books/ml-distributed-experiment-collection/viewer/exp-flexshard-muon) | 2 | 行列ごとに計算する最適化手法 Muon を、FSDP の上で速く回す仕組みの効果 |

同じ表の 6 番目の項目（KvikIO で FSx for Lustre から GPU のメモリへ直接読む）は、GPUDirect Storage の仕組みを扱う[別のブログ記事](https://zenn.dev/tosshi/articles/3335e5d9e6ee14)で扱う。

# 実験環境

3 つの章の実験は、どれも同じ 1 台のインスタンスで行った。

| 項目 | 値 |
|---|---|
| インスタンス | p5e.48xlarge（NVIDIA H200 が 8 枚、EFA が 32 個）を Amazon EC2 Capacity Blocks for ML で 24 時間確保 |
| リージョン | 南米（サンパウロ）、sa-east-1 |
| AMI | Deep Learning Base OSS Nvidia Driver GPU AMI（Ubuntu 24.04）、2026 年 11 月 6 日版 |
| 共有ストレージ | Amazon FSx for Lustre（Persistent 2、4.8 TiB、EFA 有効）とインスタンスストア |
| 実施日 | 2026 年 11 月 10 日から 11 日 |

Capacity Blocks for ML は、GPU インスタンスを日時を指定して前払いで確保する仕組みである。確保した時刻にインスタンスが自動で起動し、決めた順番で実験を流し、結果を 1 分ごとに Amazon S3 へ保存するように組んだ。インスタンスが消えても結果が残るようにするためである。

実験のコードと結果は、[littlemex/distributed-ai の investigations](https://github.com/littlemex/distributed-ai/tree/b8c40f067d9a15db05d605f7393d3653e72c6e6c/investigations) に、実験ごとのディレクトリで置いてある。各章の最後に、そのディレクトリへのリンクを付ける。

# 各章の読み方

各章は、同じ順番で書く。最初に、その手法が何を解決するのかを説明する。次に、仕組みを図で示す。そのあと、何をどう測ったかと、その結果を表にまとめる。最後に、結果から分かったことと、分からなかったことを分けて書く。数字は 1 回の測定である場合が多いので、どの条件で測ったかを必ず添える。

# まとめ

この本は、分散学習と分散推論の新しい手法を実機で確かめる記録である。最初の 3 章は、ML infra weekly の項目から、チェックポイントの保存、強化学習での確率の一致、最適化手法 Muon の高速化を、p5e.48xlarge 1 台で確かめた。コードと結果はすべてリポジトリに残してあり、同じ手順で再現できる。
