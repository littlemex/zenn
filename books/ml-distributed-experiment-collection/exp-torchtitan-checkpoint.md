---
title: "チェックポイントの保存で学習はどれだけ止まるか"
---

# はじめに

大きなモデルの学習では、途中の状態をファイルに保存するチェックポイントが欠かせない。GPU が 1 枚でも壊れると学習が止まるので、数十分から数時間おきに保存しておき、止まったらそこから再開する。問題は、保存している間に学習が止まることである。Llama 3 8B を FP32 の重みと AdamW で学習するときの状態は、今回の設定で約 96 GB あり、これを書き終えるまで GPU を待たせると、そのぶん学習が遅れる。

[ML infra weekly](https://zenn.dev/tosshi/articles/95edc2cb88f57b) の 4 番目の項目は、[TorchTitan の更新を紹介した PyTorch のブログ](https://pytorch.org/blog/torchtitan-update-new-sota-models-performance-at-scale-titanrl/) をもとに、この保存にかかる時間を AWS の共有ストレージで測ることだった。本章では、torchtitan v0.4.0 が持つ 4 種類の保存方法で、学習が止まる時間と、保存が本当に終わるまでの時間を、Amazon FSx for Lustre とインスタンスストアの 2 か所で測った。

# この章で使う言葉

| 言葉 | 意味 |
|---|---|
| torchtitan | PyTorch のチームが作っている、大きなモデルを多くの GPU で学習するためのプログラム一式 |
| ステップ | 学習で、データの塊を 1 つ読んで重みを 1 回更新する単位 |
| 同期、非同期 | 同期は、保存が終わるまで学習を待たせるやり方。非同期は、保存を裏で進めて学習を先に再開するやり方 |
| ピン留めメモリ | OS が場所を動かさないように固定した CPU のメモリ。GPU から直接写せるので、普通のメモリより写しが速い |
| Amazon FSx for Lustre | 多くのサーバーから同時に読み書きできる、AWS の高速な共有ファイルシステム |
| インスタンスストア | インスタンスに直接つながった SSD。速いが、インスタンスを止めると中身が消える |

# 学習が止まる時間と、保存が終わる時間は別物である

保存には 2 つの時間がある。1 つは、学習が止まっている時間である。もう 1 つは、保存を始めてから、ファイルがストレージに全部書き終わるまでの時間である。書き終わるまでは、そのチェックポイントから再開できない。

![同期の保存は、書き終わるまで学習を止める。非同期の保存は、保存の呼び出しをすぐに返し、ストレージへの書き込みを学習と並行して進めるが、次の保存はこの書き込みが終わるまで待つ](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/ckpt-timeline.png)

同期の保存では、2 つの時間は同じになる。非同期の保存では、保存の呼び出しはすぐに返り、ストレージへの書き込みは裏で進む。学習が止まる時間は短くなるが、書き込みが学習と並行して進むので、書き終わるまでの時間はかえって長くなることがある。

torchtitan v0.4.0 は、次の保存方法を持つ。

| 保存方法 | torchtitan の設定 | 何をするか |
|---|---|---|
| DCP、同期 | `CheckpointManager.Config(async_mode="disabled")` | PyTorch の Distributed Checkpoint（DCP）で、書き終わるまで待つ |
| DCP、非同期 | `async_mode="async"` | 別のスレッドが書き込み、学習は先に再開する |
| DCP、ピン留めメモリへ写してから非同期 | `async_mode="async_with_pinned_mem"` | まず OS が動かさない CPU のメモリ（ピン留めメモリ）へ写し、そこから書き込む |
| torch_checkpointing | `TorchCheckpointingManager.Config()` | 新しいチェックポイントのライブラリ。別のプロセスが書き込む |

# 何をどう測ったか

2026 年 11 月 10 日から 11 日に、p5e.48xlarge（H200 が 8 枚）1 台で、torchtitan v0.4.0 と PyTorch 2.15.0 を使って測った。8 枚の H200 で、Llama 3 8B を 21 ステップ学習し、5 ステップごとに学習状態をすべて（モデル、最適化手法の状態、学習率の予定）保存した。1 回の保存は 96.4 GB である。重みは乱数で初期化し、データとトークナイザーは torchtitan のリポジトリに入っている試験用のものを使った。Hugging Face で利用申請が要るモデルを使わずに再現できるようにするためである。

1 回の保存で学習が止まる時間は、torchtitan 自身のログから取った。これは保存の呼び出しにかかった時間で、前の保存を待つ時間は含まない。同期の保存では `Finished saving the checkpoint in X seconds`、非同期の保存では `Finished staging the checkpoint in X seconds` という行である。保存が本当に終わった時刻は、学習とは別のプロセスで保存先のディレクトリを 0.2 秒ごとに見張り、そのステップのメタデータのファイル（`.metadata`）が現れた時刻とした。DCP は全部を書き終えたあとにこのファイルを書くので、トレーナーの報告とは独立に、ストレージの側から完了を確かめられる。torch_checkpointing は別の行（`Initiating checkpoint staging` と `Staging copy done.`）を出し、一時的なディレクトリに書いてから名前を付け替えるので、この 2 行の間を学習が止まる時間、ステップのディレクトリが現れた時刻を保存が終わった時刻とした。測り方が少し違うので、DCP の行と並べるときは目安として見てほしい。

保存先は 2 か所である。FSx for Lustre は、Persistent 2、4.8 TiB、1 TiB あたり 1000 MB/s、EFA 有効で、すべてのストレージサーバーに 16 MiB 単位で分散して書く設定にした。インスタンスストアは、8 台の NVMe SSD をまとめた RAID0 である。

# 結果

5、10、15、20 ステップ目の 4 回の保存の中央値である。保存のないステップは、どの実行でも 1 ステップ 0.88 秒から 0.90 秒だった。

| 保存方法 | 保存先 | 保存の呼び出しにかかった時間 | 保存が終わるまでの時間 |
|---|---|---|---|
| DCP、同期 | FSx for Lustre | 24.1 秒 | 24.2 秒 |
| DCP、非同期 | FSx for Lustre | 5.5 秒 | 34.2 秒 |
| DCP、ピン留めメモリ、非同期 | FSx for Lustre | 0.37 秒 | 29.5 秒 |
| torch_checkpointing（下の注意を参照） | FSx for Lustre | 4.0 秒 | 51.8 秒 |
| DCP、同期 | インスタンスストア | 10.2 秒 | 10.2 秒 |
| DCP、非同期 | インスタンスストア | 5.5 秒 | 18.1 秒 |
| DCP、ピン留めメモリ、非同期 | インスタンスストア | 0.18 秒 | 15.3 秒 |
| torch_checkpointing（下の注意を参照） | インスタンスストア | 3.9 秒 | 22.2 秒 |

ピン留めメモリを使う非同期の保存は、FSx for Lustre で保存の呼び出しにかかる時間を 24.1 秒から 0.37 秒に縮めた。ところが、学習にかかる時間は縮まらなかった。次の表は、6 ステップ目から 20 ステップ目までの区間にかかった時間で、この区間に 10、15、20 ステップ目の 3 回の保存が始まる。最後の保存の書き込みが終わるまでの時間は含まない。

| 保存方法 | FSx for Lustre | インスタンスストア |
|---|---|---|
| DCP、同期 | 60.7 秒 | 32.9 秒 |
| DCP、非同期 | 73.7 秒 | 39.5 秒 |
| DCP、ピン留めメモリ、非同期 | 64.9 秒 | 36.2 秒 |
| torch_checkpointing（下の注意を参照） | 55.6 秒 | 25.8 秒 |

**非同期の保存は、次の保存までに前の書き込みが終わらないと、結局そこで学習を待たせる。** torchtitan は、新しい保存を始める前に、前の保存が書き終わるのを待つ（[`maybe_wait_for_saving`](https://github.com/pytorch/torchtitan/blob/v0.4.0/torchtitan/components/checkpointer/dcp.py)）。今回は 5 ステップ（約 4.4 秒の計算）ごとに保存したので、30 秒近くかかる書き込みを隠しきれず、10、15、20 ステップ目の保存を始めるときに前の書き込みを待った。保存のないステップは 0.88 秒なのに、保存をはさむステップの間隔は 25 秒から 34 秒になった。非同期の保存で学習の時間を縮めるには、保存の間隔の計算時間を、書き込みの時間より長くとる必要がある。実際の学習では保存の間隔を数十分以上にすることが多く、その場合はこの条件を満たしやすい。

単なる非同期（スレッドで書く方法）は、保存の呼び出しに 5.5 秒かかった。ピン留めメモリを使う方法は、GPU のメモリから CPU のメモリへの写しそのものも学習の計算と並行して進める作りなので（[dcp.py の `_maybe_wait_for_staging`](https://github.com/pytorch/torchtitan/blob/v0.4.0/torchtitan/components/checkpointer/dcp.py)）、呼び出しが短い。2 つの方法の差の内訳までは、今回は測っていない。

torch_checkpointing は、標準の設定のままでは、どちらの保存先でも失敗した。このライブラリは、OS のページキャッシュを通さずに書く O_DIRECT でファイルを開く。O_DIRECT では、書き込むデータの位置と大きさを 4 KiB などの区切りにそろえる必要がある。今回の保存では、そろっていない書き込みが `EINVAL`（不正な引数）で断られ、エラーには `unexpected pos 64 vs 0` と出た。ライブラリは、ファイルを開くときに O_DIRECT が断られれば普通の書き込みに切り替えるが、書き込みの途中で断られた場合は切り替えない。表の torch_checkpointing の行は、O_DIRECT を使わない設定（`use_direct_io=False`）にして測った値である。

# 分かったこと、分からなかったこと

分かったことは 3 つある。1 つ目に、ピン留めメモリへ写してから非同期に書く方法は、保存の呼び出しを一番短くするが、学習の時間を縮めるには、保存の間隔の計算時間を書き込みの時間より長くとる必要がある。今回のように間隔が短いと、前の書き込みを待つ時間が出て、6 ステップ目から 20 ステップ目の区間は同期の保存より長くかかった。2 つ目に、非同期にすると、保存が本当に終わるまでの時間はむしろ延びる。障害が起きたとき、最後に保存を始めたチェックポイントはまだ書き終わっていないことがあり、それは再開に使えない、と考えておく必要がある。3 つ目に、torch_checkpointing 0.1.1 は、標準の設定のままでは ext4 にも Lustre にも書けなかった。O_DIRECT を使わない設定にすると、6 ステップ目から 20 ステップ目の区間は 4 つの中で一番短かった。

分からなかったことも書いておく。PyTorch のブログは、512 枚の H100 で DeepSeek-V3 671B を学習したとき、書き込みの時間が DCP の平均 384.4 秒から torch_checkpointing の 13.1 秒に、学習が止まる時間が 841.2 ms から 34.1 ms に縮んだと報告している。1 台 8 GPU の今回の測定は、これと比べられるものではない。台数が増えると、ストレージの合計の帯域と、全員がそろうのを待つ時間が効いてくる。また、使った FSx for Lustre は、EFA を有効にできる最小の 4.8 TiB で、合計の帯域はおよそ 4.8 GB/s が上限である。容量の大きなファイルシステムでは帯域も大きくなるので、同期の保存の時間は変わりうるが、今回は測っていない。

# 参考資料

- [ML infra weekly](https://zenn.dev/tosshi/articles/95edc2cb88f57b)
- [TorchTitan Update: New SOTA Models, Performance at Scale, TitanRL](https://pytorch.org/blog/torchtitan-update-new-sota-models-performance-at-scale-titanrl/)
- [torchtitan v0.4.0 の DCP の保存処理](https://github.com/pytorch/torchtitan/blob/v0.4.0/torchtitan/components/checkpointer/dcp.py)
- [実験のコードと結果](https://github.com/littlemex/distributed-ai/tree/b8c40f067d9a15db05d605f7393d3653e72c6e6c/investigations/2026-11-10-torchtitan-checkpoint-fsx-lustre)

# まとめ

Llama 3 8B の 96 GB のチェックポイントでは、ピン留めメモリへ写してから非同期に書くと、FSx for Lustre で 保存の呼び出しにかかる時間は 24.1 秒から 0.37 秒になった。ただし、5 ステップごとに保存した今回の設定では、前の書き込みが終わるのを待つ時間が出て、学習にかかる時間は同期の保存より長かった。非同期の保存を生かすには、保存の間隔の計算時間を書き込みの時間より長くとる。torch_checkpointing は標準の O_DIRECT の書き込みで失敗し、O_DIRECT を使わない設定にすると、学習にかかる時間が一番短かった。
