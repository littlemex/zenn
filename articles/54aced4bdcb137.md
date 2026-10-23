---
title: "Engram の表を何台かに分けて引く"
emoji: "📬"
type: "tech"
topics: ["DeepSeek", "DeepEP", "NCCL", "GPU", "分散学習"]
published: false
---

## はじめに

「[Engram は何を覚えて、どう引くのか](https://zenn.dev/tosshi/articles/5853108f53bd7f)」で、Engram が読む行は、入力のトークンだけで計算の前に決まることを見ました。この記事では、その大きな表を何台かに分けて持ったとき、必要な行をどう集めるかに絞ります。GIN（GPU-Initiated Networking、GPU から通信を出す NCCL の仕組み）、get、Proxy の基本は「[MoE の all-to-all を支える層](https://zenn.dev/tosshi/articles/a0f5ff21eed405)」で説明しています。後半は、未公開の修正を含む手元の 2 台での確認です。

| 前の記事で見たこと | この記事で見る今の状態 | 実務での意味 |
|---|---|---|
| 読む行の番号は、計算の前に読み手だけで決まる | DeepEP は、読み手だけの操作（get）で行を集める | 表の持ち主の GPU を、行を返す計算に使わずに済む |
| V4.1-Flash の表は 2 か所で合わせて約 196 GB（FP8 の値だけ） | 基準の版（a56d6156）の DeepEP は、表をホストのメモリに置く | GPU のメモリを、モデルのほかの重みに回せる |
| 取り寄せは計算と重ねられる | 取り寄せを始めるカーネルと、完了を待つカーネルが分かれている | 間に別の計算を置ける |

## 分けた表から行を集める 3 つの方法

![表を 2 つのランクで分けて持ち、ランク 0 がランク 1 の持つ行を欲しいとき。all_reduce では両方のランクが同じ番号を同じ順番で引いて足し合わせ、All-to-All ではランク 0 が番号を送ってランク 1 が行を返し、GIN の get ではランク 0 がランク 1 の登録したメモリから行を読み、ランク 1 の GPU は行を返す計算をしない。処理の流れを並べたもので、速さを比べたものではない](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/54aced4bdcb137/zenn/articles/54aced4bdcb137/three-ways.png)

分散して動かすときは、参加する各プロセスにランクという番号を付けます。この記事では、各プロセスをランクと呼び、1 つの GPU に 1 つのランクを対応させます。表を行で分けると、自分の持たない行をほかのランクから集める必要があります。図は、別々の資料に出てくる 3 つの集め方を、処理の流れで並べたものです。同じ条件で速さを比べたものではありません。

1 つ目は all_reduce で、各ランクの値を決めた演算でまとめ、結果を全員に配る通信です。ここでは演算に和を使います。V4.1-Flash の[参照の推論コード](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/2cba9e42aa026125f3ed06c6d98c1db82f7ca027/inference/model.py#L296-L325)では、全ランクが同じ行番号を同じ順番で引きます。各ランクは自分が持つ行の値を置き、持たない行の位置を 0 にします。同じ位置どうしを足し合わせると、必要な行がそろいます。[README](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/2cba9e42aa026125f3ed06c6d98c1db82f7ca027/inference/README.md) はこのコードを、本番のサービングのエンジンではなく、読みやすい参照の実装だとしています。

2 つ目は All-to-All で、各ランクが相手ごとにデータを送り合う通信です。[論文](https://arxiv.org/abs/2601.07372)は学習で、表を GPU に分けて持ち、使う行を All-to-All で集め、逆伝播で勾配を配ると書いています（Section 2.5）。図は、番号を持ち主に送って行を返してもらう、典型的な論理の流れで描きました。持ち主の側も、毎回働きます。

3 つ目が GIN の get です。get は、相手が window（通信用に登録したメモリ）として登録した場所から、読み手の側だけの操作で読み出す操作で、[NCCL のドキュメント](https://github.com/NVIDIA/nccl/blob/12df1a11afad322be5a204a2db890161cbf8131d/docs/userguide/source/api/device_gin.rst#L59-L64)に定義があります。RDMA（相手が登録したメモリをネットワーク越しに直接読み書きする方式）の経路では、NIC（ネットワークの装置）が登録されたメモリを読むので、持ち主の GPU は行を返す計算をしません。後で扱う TCP の経路では、持ち主の側の CPU が通信を進めます。**読む行の番号を読み手だけで決められる Engram は、この形に合います**。

## DeepEP の Engram は get で表を引く

![3 つのランクの模式例で、ランク 0 が番号から持ち主のランクと行を計算し、ランク 1 とランク 2 がホストのメモリに持つ行を get で読む。持ち主の GPU は行を返す計算をしない。相手ごとに完了を確かめる request を作り、別のカーネルで完了を待ってから、読んだ行を返す](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/54aced4bdcb137/zenn/articles/54aced4bdcb137/engram-get.gif)

DeepEP の Engram は、3 つ目の get で表を引きます。[README](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/README.md#L23-L31) では「0 SM Engram (with RDMA)」という実験的な機能です。基準の版（a56d6156）では、表の本体はホストのメモリにあります。DeepEP は、GPU の領域の後ろに[ホストの NUMA ノードのメモリの領域を並べ](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/csrc/kernels/backend/symmetric.hpp#L33-L34)、GPU 上の表をその領域に[写します](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/csrc/elastic/buffer.hpp#L229-L235)。

GIF は、3 つのランクが 4 行ずつ持つ場合の模式例です。後半で実際に動かしたのは、2 台に 1 ランクずつの構成です。カーネルは、[行の番号を 1 ランクの行数で割って](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/deep_ep/include/deep_ep/impls/engram_fetch.cuh#L58-L68)、持ち主と、その中の行を出します。行 5 なら $5 \div 4$ の商が 1 なので持ち主はランク 1、余りが 1 なのでランク 1 の中の 1 行目（0 から数えた番号）です。

```cpp
const auto owner_rank_idx = global_idx / kNumEntriesPerRank;
const auto local_entry_idx = global_idx % kNumEntriesPerRank;
```

読み手は、出した get の完了を後で待てるように、request を作ります。ここでの request は、相手に送る注文ではなく、完了を待つための情報で、通信のキュー（QP）と相手の組ごとに作ります。[別の GPU カーネル](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/deep_ep/include/deep_ep/impls/engram_fetch_wait.cuh#L9-L29)が request の完了を待ち、読んだ行と FP8 のスケール係数を使える状態にします。Python の [engram_fetch](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/deep_ep/buffers/elastic.py#L584-L604) は、後で完了待ちのカーネルを始める関数（hook）を返すので、呼び出し側は hook を呼ぶ前に別の GPU の計算を置けます。Python の関数が戻る時点と、GPU の上で読み出しが終わる時点は別です。前の記事で見た、番号を先に出して取り寄せを計算と重ねる形は、この分け方で書けます。V4.1-Flash の推論系がこの実装を使っているかは、技術報告からは分かりません。

GIN の使い方は、DeepEP の中でも機能によって向きが違います。dispatch は、送り手が相手の領域に[書きます](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/deep_ep/include/deep_ep/impls/dispatch.cuh#L156-L176)。Engram の表引きは、読み手が get で取りに行きます。

:::details 読む位置と完了の単位（DeepEP a56d6156）
持ち主のランクは、get を出す相手の単位である RDMA の peer と、その peer の中のランクに分けます。`allow_hybrid_mode` を有効にすると、NVLink でつながる範囲の `num_scaleup_ranks` 個のランクを 1 つの peer に対応させ、peer の中のランクは読む位置のオフセットで選びます。このとき get の相手は、各ノードで同じ番号の GPU を集めた rail の team の中から選びます。無効にすると、1 つの peer が 1 つのランクで、team は全ランクの world です。

$$
\text{offset} = r_{\text{peer}} \cdot S + i_{\text{local}} \cdot B
$$

$r_{\text{peer}}$ は peer の中のランクの番号、$S$ は同じ peer の中で隣のランクの格納領域へ進む間隔（1 ランク分の CPU のバッファのバイト数）、$i_{\text{local}}$ はランクの中の行の番号、$B$ は 1 行のバイト数です。GIF は `allow_hybrid_mode` を無効にした場合で、$r_{\text{peer}} = 0$ です。

カーネルの 1 ブロックは 1 つの QP（Queue Pair）を受け持ち、QP ごとに GIN の context を選びます。peer ごとに出した get の最後に `flush_async` を呼び、完了を受け取る request を QP と peer の組ごとに 1 つ作ります。待つ側のカーネルは各 request を `wait` します。

| 決めること | DeepEP で使う部品 | 気をつける点 |
|---|---|---|
| 表をどう分けるか | 1 ランクが連続した行を持つ（行の番号 ÷ 1 ランクの行数で持ち主が決まる） | 表の行の並べ替えや負荷の偏りの調整はしない |
| 表の置き場と登録 | window。基準の版ではホストのメモリ | 各ランクは自分の持つ行を、他のランクから読める形で登録する |
| 行を読む | `ncclGin` の get | 読む位置は peer の中のランクと行の番号から計算する |
| 読み終わりを確かめる | peer ごとの `flush_async` と、別のカーネルの `wait` | 待つ単位は QP と RDMA の peer の組 |
| get の相手 | world か rail の team の中のランク（[team の選び方](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/deep_ep/include/deep_ep/common/handle.cuh#L127-L144)） | NVLink でつながるランクも、get の読む位置で選んで読む。NVLink の load/store で直接読む分岐は無い |
| FP8 のスケール | 全ランクに[複製](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/deep_ep/buffers/elastic.py#L569-L582) | 各ランクが表全体のスケールを持つので、ランクを増やすと全ランク合計の量が増える |

後のコミット（[93eb6eb2](https://github.com/deepseek-ai/DeepEP/tree/93eb6eb238127e96c6d7a4a625a6dad158348509)）では、Engram が `EngramBuffer` という独立したクラスになり、複数の層の表を 1 回の fetch で扱い、層ごとに hook を返します。表の置き場は `use_cpu_rdma_storage` で GPU かホストかを選び、FP8 のスケール係数は各ランクが[自分の分だけ持ちます](https://github.com/deepseek-ai/DeepEP/blob/93eb6eb238127e96c6d7a4a625a6dad158348509/deep_ep/buffers/engram.py#L118-L123)。QP の数と深さは、NIC が 1 秒に処理できる読み出しの数から計算する `get_theoretical_config` で決めます。
:::

## EFA GDA の経路と完了待ち

AWS の GPU のインスタンスどうしは EFA（AWS のネットワークの装置）でつながるので、実務ではこの経路で動くかが問題になります。EFA の上で動かすには、get に加えて、peer ごとの完了を待つ FlushAsync と Wait が要ります。2026 年 10 月 12 日に確かめた NCCL 2.32.3 の EFA GDA の経路には、get の実装があります。一方、[FlushAsync と Wait の中身は空](https://github.com/NVIDIA/nccl/blob/12df1a11afad322be5a204a2db890161cbf8131d/src/include/nccl_device/gin/efa_gda/gin_efa_gda.h#L926-L944)です（ソースで確かめた内容で、EFA の実機では動かしていません）。FlushAsync は request を初期化せず、Wait は完了を待たずに戻るので、前の節の待ち方はそのままでは使えません。このまま使うと、行がまだ届いていないうちに、読み出しの結果として使われるおそれがあります。DeepEP の 93eb6eb2 は、初期化で [`NCCL_GIN_TYPE` を GDAKI（3）に固定](https://github.com/deepseek-ai/DeepEP/blob/93eb6eb238127e96c6d7a4a625a6dad158348509/deep_ep/buffers/engram.py#L80-L82)しており、EFA GDA（5）は選びません。経緯は「[NCCL 2.32.3 でロードマップはどう実装されたか](https://zenn.dev/tosshi/articles/9ab10d37f33ced)」に書きました。

## NCCL の TCP の経路で動かす

EFA GDA では完了待ちが空なので、先に TCP の経路で、表を分けても読み出しの結果が変わらないかを確かめました。ここからは、未公開の DeepEP のパッチと NCCL の修正を含む、手元の 2 台での確認です。リンク先の公開コードだけで同じ実験を再現する手順ではありません。GIN の Proxy の方式では、GPU が出した get を CPU 側の NCCL が受け取り、RMA の通信を受け持つ部品に渡します。NCCL 2.32.3 にはその部品の 1 つとして、TCP で運ぶ RMA_SOCKET_PROXY が入っていて、[get も実装](https://github.com/NVIDIA/nccl/blob/12df1a11afad322be5a204a2db890161cbf8131d/src/transport/rma_socket.cc#L491-L528)されています。この経路では、持ち主の側も CPU が通信を進めます。RMA_SOCKET_PROXY は、GPU のメモリを CPU から読み書きするのに、GDRCopy（GPU のメモリを CPU のアドレスに対応づける仕組み）を使います。

RTX PRO 4500 Blackwell を 1 枚ずつ載せた 2 台の g7.2xlarge で、DeepEP の Engram の表引きをこの経路に通しました。DeepEP は、AWS のフォークである [amazon-contributing/DeepEP](https://github.com/amazon-contributing/DeepEP/tree/2a00bd88fcc69e6f9b669f5a338348e49217bf86) の 2a00bd8 に、別の調査で用意した 10 個のパッチを当てたものです。パッチはテストの補強や NVLink のない構成で起きる不具合の修正などで、下で使う 2 つのテストのファイルは変えていません。このパッチと、後で述べる NCCL の修正は、まだ公開していません。この節の結果は、手元の 2 台で確かめたものです。

NCCL 2.32.3 のままでは、表を登録する段階で `Window registration of addresses that span multiple physical segments requires DMABUF support with GIN.` と断られます。DeepEP は表をホストのメモリに置き、GPU のメモリと仮想アドレスが続くように並べて、1 つの window として登録します。NCCL はこの window を、[確保ごとの区画に分けて登録します](https://github.com/NVIDIA/nccl/blob/12df1a11afad322be5a204a2db890161cbf8131d/src/dev_runtime.cc#L639-L661)。ホストの区画にはホストのメモリという印を付けるので、RMA_SOCKET_PROXY はホストの区画をアドレスで、GPU の区画を GDRCopy で、そのまま扱えます。断っていたのは、その手前の[登録の検査](https://github.com/NVIDIA/nccl/blob/12df1a11afad322be5a204a2db890161cbf8131d/src/gin/gin_host.cc#L512-L521)です。この検査は、区画が複数ある window を、DMA-BUF（GPU のメモリをほかの装置と共有する Linux の仕組み）に対応した部品にしか許しません。この検査だけを外した NCCL で、下の表引きのテストはすべて通りました。

ただし、検査を外すだけでは、ホストの区画を持たず DMA-BUF も使えない window や、CPU から書けないホストのメモリでも、登録が通ってしまいます。そこで、ホストの区画を持つ window に限って TCP の部品の登録を通すように、NCCL を 3 か所直しました。

:::details 直した 3 か所（未公開）
次の修正は未公開で、ここに書いた結果は手元の 2 台でだけ確かめたものです。

1. 登録の検査: window を区画ごとに登録するとき、つまりどれかのランクの window にホストの区画があるときに限り、RMA の部品が RMA_SOCKET_PROXY であれば DMA-BUF を求めません。CUDA 12.2 以降でビルドし、次の確かめに使うドライバーの関数がそろっていることも条件です。この判断は全ランクで同じになります。ホストの区画を持たない window は、確保が複数あっても 1 つの範囲で登録され、GDRCopy はその範囲を断るので、今までどおり検査を残します。ほかの部品の条件は変えていません。
2. RMA_SOCKET_PROXY の登録: CPU 側の NCCL は、ホストのメモリに memcpy で書き込みます。cuMem で確保したホストのメモリが GPU からしか使えない設定だと、そこで落ちます。そこで、cuMem のホストのメモリを登録するときは、CPU が読み書きできるかを確かめ、できなければ断ります。malloc などで確保した普通のホストのメモリは、これまでどおり確かめずに登録します。
3. 受け取りの処理: ホストのメモリに書き込んだあとに、書き込みの順番を守らせる命令を置きます。signal や get の完了の知らせより先に、書き込みが見えるようにするためです。命令は NCCL の wc_store_fence() で、x86 では sfence、aarch64 では dsb st です。確かめたのは x86_64 だけです。
:::

直した NCCL を 2 台で動かした実行ではどれも、NCCL のログに `RMA/Plugin: Assigned plugin RMA_SOCKET_PROXY to comm` が出て、RMA_SOCKET_PROXY が使われました。Engram の表引きのテストでは、2 台の間で 1 台あたり約 3,000 万バイトから 6,500 万バイトの通信がありました。テストの間に、ネットワークの装置が送受信したバイト数の合計です。表引きの結果は、相手の機械の表から読んだ行を含んでいます。次はどれも、同じ 2 台の構成で別々に行ったテストです。

| 確かめたこと | 結果 |
|---|---|
| DeepEP の [test_engram.py](https://github.com/amazon-contributing/DeepEP/blob/2a00bd88fcc69e6f9b669f5a338348e49217bf86/tests/elastic/test_engram.py) を、BF16 では 1 ランクあたり 4,096 行と 65,536 行の表で、FP8 では 65,536 行の表で動かしました | 2 台とも通りました。get で読んだ行と FP8 のスケール係数が、全ランクから集めた正解と一致しました |
| 「[Engram を自分で作って学習させる](https://zenn.dev/tosshi/articles/35ef2467d84b63)」で学習したモデルの 1,049,422 行の表を 2 台に半分ずつ持たせ、表引きを DeepEP の engram_fetch に置き換えました | 同じ比較を 3 回実行し、表を丸ごと持って引いた場合と、読んだ行、出力の確率、続けて生成した 40 トークンが、3 回ともビット単位で一致しました。読んだ行のおよそ半分は、相手の機械の表から来ています |
| 対照として、学習したモデルの比較で、読む行の番号をすべて 1 ずつずらしました | 一致しなくなりました。比べ方が 1 行のずれを検出できることを確かめました |
| 対照として、直す前の NCCL 2.32.3 で動かしました | 表の登録で断られました |
| ホストの区画を持たない window を使う DeepEP の dispatch と combine（[test_ep.py](https://github.com/amazon-contributing/DeepEP/blob/2a00bd88fcc69e6f9b669f5a338348e49217bf86/tests/elastic/test_ep.py)）を動かしました | 直した NCCL でも、直す前と同じく通りました |
| RMA_SOCKET_PROXY の関数を直接呼ぶテストと、2 つのプロセスを TCP でつなぐテストで、区画ごとの登録、ホストの区画への put と get、断るべき登録（GPU からしか使えないホストのメモリ、確保のあとの隙間、ホストのメモリとして登録した GPU のメモリ）を確かめました | 通りました |
| 同じテストを、3 か所の修正に含まれる 5 つの仕組み（4 つの検査と、MR の番号を先に取ること）を 1 つずつ外した 5 通りの NCCL で動かしました | どの場合も、外した検査を受け持つ項目が失敗しました。テストが検査のあるなしを見分けられることを確かめました |

この一致は、未公開の修正を含む手元の構成で、表を分けても読み出しの結果が同じだ、という確かめです。モデルの性能や通信の速さを測ったものではありません。確かめたのは、2 台に 1 ランクずつの形だけです。test_ep.py には `--allow-hybrid-mode 1` を付けましたが、1 台に GPU が 1 枚なので、NVLink でつながる範囲を使う経路は通っていません。EFA でも動かしていません。TCP での速さからは EFA での速さが分からないので、ここでは速さを扱いません。

## まとめ

DeepEP の Engram は、読み手が行の番号から持ち主と位置を出し、get で読み、相手ごとの完了を待ってから使います。NCCL 2.32.3 の TCP の経路では表の登録が断られますが、登録の検査を区画ごとの登録に限って緩め、CPU から書けるかを確かめるように直しました。すると 2 台に 1 ランクずつの構成で、表を分けた読み出しが、丸ごと持つ場合とビット単位で一致しました。

## 参考

- [Conditional Memory via Scalable Lookup: A New Axis of Sparsity for Large Language Models (arXiv:2601.07372)](https://arxiv.org/abs/2601.07372)
- [DeepSeek-V4.1-Flash の inference/model.py](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/2cba9e42aa026125f3ed06c6d98c1db82f7ca027/inference/model.py#L296-L325)
- [DeepEP README](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/README.md)、[engram_fetch.cuh](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/deep_ep/include/deep_ep/impls/engram_fetch.cuh)、[engram_fetch_wait.cuh](https://github.com/deepseek-ai/DeepEP/blob/a56d6156febcd9976e55adc85b5155bfac9f28f8/deep_ep/include/deep_ep/impls/engram_fetch_wait.cuh)
- [NCCL Device API の GIN のドキュメント](https://github.com/NVIDIA/nccl/blob/12df1a11afad322be5a204a2db890161cbf8131d/docs/userguide/source/api/device_gin.rst)
- [Engram は何を覚えて、どう引くのか](https://zenn.dev/tosshi/articles/5853108f53bd7f)
- [Engram を自分で作って学習させる](https://zenn.dev/tosshi/articles/35ef2467d84b63)（2 台に分けて引いた表の学習元）
