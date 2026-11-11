---
title: "この本の読み方と実験環境"
---

# はじめに

この本は、毎週の ML infra weekly で「次に確かめる」と書いた項目を、実機で実際に確かめた記録である。ML infra weekly は、その週に読んだ分散学習と分散推論の基盤に関するブログや論文をまとめ、最後に「次に確かめること」を表にしている。記事やリリースノートを読むだけでは、手元の環境で同じように動くか、どこで詰まるか、数字がどれくらい出るかが分からない。そこで、表に挙げた項目のうち実機で確かめる価値があるものを、その号の次の週以降に動かし、仕組み、測り方、結果、分かったことの順にまとめる。

号が出るたびに章を足していく。各章の題には、どの号の何番目の項目かを付ける。

| 号 | 項目 | 章 | 何を確かめたか |
|---|---|---|---|
| [~26/10/29](https://zenn.dev/tosshi/articles/95edc2cb88f57b) | 4 | [チェックポイントの保存で学習はどれだけ止まるか](https://zenn.dev/tosshi/books/ml-distributed-experiment-collection/viewer/exp-torchtitan-checkpoint) | torchtitan の 4 種類の保存方法で、保存の呼び出しにかかる時間、保存が本当に終わるまでの時間、学習にかかる時間 |
| [~26/10/29](https://zenn.dev/tosshi/articles/95edc2cb88f57b) | 3 | [学習と推論の確率は本当に一致するか](https://zenn.dev/tosshi/books/ml-distributed-experiment-collection/viewer/exp-titanrl-batch-invariant) | TitanRL の batch-invariant モードで、学習側と推論側が同じ確率を出すか |
| [~26/10/29](https://zenn.dev/tosshi/articles/95edc2cb88f57b) | 2 | [Muon の更新を速くする FlexShard](https://zenn.dev/tosshi/books/ml-distributed-experiment-collection/viewer/exp-flexshard-muon) | 行列ごとに計算する最適化手法 Muon を、FSDP の上で速く回す仕組みの効果 |

[~26/10/29 の号](https://zenn.dev/tosshi/articles/95edc2cb88f57b)の 6 番目の項目（KvikIO で FSx for Lustre から GPU のメモリへ直接読む）は、GPUDirect Storage の仕組みを扱う[別のブログ記事](https://zenn.dev/tosshi/articles/3335e5d9e6ee14)で扱う。同じ号のほかの項目のうち、Kubernetes での GPU の分け合い（8、9 番目）は EKS のクラスターが要り、CUDA 13.4 のメモリの上限（10 番目）は対応するドライバが要るので、環境がそろったときに章を足す。拡散言語モデルの推論（11 番目）は測ったが、今のところ本にするほどの結論がないので、記録をリポジトリに残すにとどめた。

# 実験環境

~26/10/29 の号の 3 つの章は、どれも同じ 1 台のインスタンスで確かめた。号ごとに、使った環境をこの節に足していく。

| 項目 | 値 |
|---|---|
| インスタンス | p5e.48xlarge（NVIDIA H200 が 8 枚、EFA が 32 個）を Amazon EC2 Capacity Blocks for ML で 24 時間確保 |
| リージョン | 南米（サンパウロ）、sa-east-1 |
| AMI | Deep Learning Base OSS Nvidia Driver GPU AMI（Ubuntu 24.04）、2026 年 11 月 6 日版 |
| ストレージ | Amazon FSx for Lustre（Persistent 2、4.8 TiB、EFA 有効）と、インスタンスに付いている SSD（インスタンスストア） |
| 実施日 | 2026 年 11 月 10 日から 11 日 |

Capacity Blocks for ML は、GPU インスタンスを日時を指定して前払いで確保する仕組みである。確保した時間に Amazon EventBridge Scheduler がインスタンスを起動し、インスタンスが決めた順番で実験を流し、結果を 1 分ごとに Amazon S3 へ保存するように組んだ。インスタンスが消えても結果が残るようにするためである。一部の実験は、最初の実行で見つかった設定の誤りを直して流し直した。どの実験も、ほかの実験と GPU を同時には使わないように、1 つずつ流している。

実験のコードと結果は、[littlemex/distributed-ai の investigations](https://github.com/littlemex/distributed-ai/tree/e092c3f54e2ebcee665389cbdfc17348f612c2d3/investigations) に、実験ごとのディレクトリで置いてある。各章の最後に、そのディレクトリへのリンクを付ける。

# 各章の読み方

各章は、同じ順番で書く。最初に、その手法が何を解決するのかを説明する。次に、仕組みを図で示す。そのあと、何をどう測ったかと、その結果を表にまとめる。最後に、結果から分かったことと、分からなかったことを分けて書く。数字は 1 回の測定である場合が多いので、どの条件で測ったかを必ず添える。

# まとめ

この本は、毎週の ML infra weekly で「次に確かめる」と書いた項目を、実機で確かめる記録である。最初の ~26/10/29 の号からは、チェックポイントの保存、強化学習での確率の一致、最適化手法 Muon の高速化の 3 項目を、p5e.48xlarge 1 台で確かめた。コードと結果はリポジトリに残してある。以降も号ごとに章を足していく。
