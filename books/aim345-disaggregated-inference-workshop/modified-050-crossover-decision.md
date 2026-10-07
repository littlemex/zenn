---
title: "Modified: 050 どちらのスタックを残すか決める"
---

## はじめに

CSV には、このセッションの SLO のもとでどの到着率が条件を満たすかが示されている。この結果から暫定的な選択を記録し、それぞれの構成がどこで条件を満たさなくなるか、そしてなぜそうなるかを突き止め、同じ呼び出しの記録を別の上限で採点し直す。

目的は、暫定的な判断を記録し、分割することのコストでそれを説明し、レイテンシの上限を変えるとどう変わるかを見ることである。**約束** とは TTFT の上限と TPOT の上限の組を指し、スタックの **壁** とは、そのスタックが初めて条件を満たさなくなる、測定済みの到着率を指す。やることは、このセッションの約束のもとで暫定的な判断を記録すること、それぞれのスタックの壁と、それが表している分割のコストを見つけること、同じ呼び出しの記録を複数の約束の組み合わせで採点し直すことの 3 つである。この章には 10 分が割り当てられている。

## 手順

1. `results/paired-comparison/summary.csv` から **あなたの測定** の列を埋める。

「負荷を上げる」の章の手順 7 で見つけた、条件を満たす `B-long-context` の到着率と、その行の有効な calls/s/GPU を使う。例は 2026-09-10 の測定である。

| 判断の項目 | あなたの測定 | g7.48xlarge の測定例 | g7.24xlarge 相当の割り当ての測定例 |
|---|---|---|---|
| 構成あたりの GPU 枚数 | _____ 枚 | 8 枚 | 4 枚 |
| TTFT の上限 | _____ ms | 2000 ms | 2000 ms |
| TPOT の上限 | _____ ms/トークン | 100 ms/トークン | 100 ms/トークン |
| 必要な達成率 | _____ パーセント | 90 パーセント | 90 パーセント |
| 統合構成が満たす最も高い到着率 | _____ tasks/s | 4 tasks/s | 2 tasks/s |
| その到着率での統合構成の実効スループット | _____ calls/s/GPU | 0.3638653388375643 calls/s/GPU | 0.35860889234303406 calls/s/GPU |
| その到着率での統合構成の達成率 | _____ パーセント | 100 パーセント | 100 パーセント |
| 分離構成が満たす最も高い到着率 | _____ tasks/s | 1 task/s | 0.25 tasks/s |
| その到着率での分離構成の実効スループット | _____ calls/s/GPU | 0.1026702465300911 calls/s/GPU | 0.025 calls/s/GPU |
| その到着率での分離構成の達成率 | _____ パーセント | 100 パーセント | 100 パーセント |
| 長文脈トラフィックのために暫定的に残すスタック (1 到着率あたり 1 回の実行) | _____ | 統合 | 統合 |

2. 次の文を完成させる。「TTFT 上限 2000 ms、TPOT 上限 100 ms/トークンのもとで 1 到着率あたり 1 回の実行から、_____ tasks/s で約束を満たし _____ 有効な calls/s/GPU を出せるので、暫定的に _____ を残す。」どちらも条件を満たさなかった場合は「どちらも約束を満たさなかった」と書く。

3. それぞれのスタックの壁を見つけて説明する。

それぞれのスタックについて、最初に条件を満たさなくなった `B-long-context` の行を取り、どちらの p90 が上限を超えているかを記録する。その行が `client_valid=False` の場合は「クライアント側の経路」と書き、どちらの p90 も超えていない場合は「達成率」と書き、ライブで測った到着率のどれも失敗していない場合は「4 tasks/s より上」と書く。

| スタック | 最初に条件を満たさなかった到着率 | そのときの p90 TTFT | そのときの p90 TPOT | 手がかり | 2026-10-03 の測定例 |
|---|---|---|---|---|---|
| 統合 | _____ tasks/s | _____ ms | _____ ms/トークン | _____ | 4 tasks/s: 21673 ms と 125.7 ms/トークン、両方とも上限超え |
| 分離 | _____ tasks/s | _____ ms | _____ ms/トークン | _____ | 1 task/s: 4064 ms と 16.8 ms/トークン、TTFT が上限超え |

測定例の列は、同じ割り当てを 2026-10-03 に測り直した結果であり、次の図はその結果を描いたものである。

![g7.24xlarge 相当の割り当てについての対数軸の 2 枚組。2026-10-03 取得。統合構成の p90 TPOT は 0.25 から 4 tasks/s の間で 7 から 126 ms/トークンまで上がり、分離構成の p90 TTFT は 0.25 tasks/s の 1721 ms から 1 tasks/s までに 2000 ms を超える。帯は乱数の種を 3 通り変えたときの範囲を示す](/images/books/aim345-disaggregated-inference-workshop/modified-050-two-walls-24-equivalent.png)

![統合エンジンは GPU を共有するので prefill が decode を止め、負荷が増えると TPOT が上がり、TPOT の上限が壁になる。分離構成は KV ハンドオフを追加して GPU を分け合うので、TTFT が最初から高く負荷とともに増え、TTFT の上限が壁になる。TPOT の約束が厳しいと統合構成の壁に先に当たり、TTFT の約束が厳しいと分離構成の壁に先に当たる](/images/books/aim345-disaggregated-inference-workshop/modified-050-split-tradeoff.png)

4. 同じ呼び出しの記録を、複数の約束の組み合わせで採点し直す。

```bash
python3 rescore.py --runs results/paired-unified-b results/paired-disaggregated-b --ttft-ms 1000 2000 3000 5000 --tpot-ms 10 15 20 30 100 --output results/promise-map
```

行は TPOT の上限、列は TTFT の上限である。`U` は統合、`D` は分離、`=` は同点、`-` はどちらも満たさないことを表し、このセッションの SLO は `100` の行の `2000` の列にある。次のような形の地図が得られる (2026-10-03 に 6 つの到着率で記録。自分の地図は 5 つの到着率で、測定済みの行数は 10 になる)。

```
B-long-context: these runs predate the traffic hash; compared on the shape definition only

B-long-context: disaggregated, unified on 4 GPUs per stack, planned rates 0.25, 0.5, 1, 2, 4, 8 tasks/s, attainment target 0.9, runs per rate disaggregated 1, unified 1
a rate was run once: a cell near the attainment threshold can change on a repeat; rerun the ramp on both stacks with another --seed
cell = architecture qualifying at the higher rate, and that rate; = tie, - neither
TPOT \ TTFT       1000      2000      3000      5000
         10      U0.25      D0.5      D0.5      D0.5
         15       U0.5      =0.5      =0.5      =0.5
         20       U0.5      =0.5      =0.5        D1
         30         U1        U1        U1        =1
        100         U1        U2        U2        U2

Recorded objective reproduced for all 12 measured rows. Wrote results/recorded-single/promise-map.csv
A rate qualifies only if every ramp that planned it meets the objective there. The grid is an exploratory screen of finite windows, not a confidence bound; confirm a chosen promise with new, equally repeated runs.
```

`D` のマスは TPOT の上限が厳しく TTFT の上限がゆるいところに現れるが、このペアを測り直したときには 1 つも残らなかった。1 回だけの実行で埋まったマスは、確定していないものとして扱う。

5. 答えを変えうる試験を 1 つ書く。

次の文を完成させる。「_____ を変えると、_____ 構成の _____ の壁が動くはずだ。」例えば入力を 16384 トークンにすると統合構成の壁は下がるが、分離構成の prefill とハンドオフは長くなる。新しい約束を試すだけなら、採点し直すだけでよい。

:::details rescore.py が地図の代わりにメッセージで止まる場合
このページの間はスイープをやり直さないこと。`No measured files` はディレクトリ名が間違っていることを意味するので、`results/` を一覧表示し、2 つの `paired-*-b` ディレクトリを渡す。他のメッセージはファイル名と、それを拒否した検査名を示すので、上の記録済みの地図を読み、「記録済みの例」と明記する。それぞれのメッセージの意味は、Optional の「約束の地図を深く読む」の章にまとめてある。
:::

:::details なぜこうなるか
それぞれの壁は 1 つのコストを反映している。統合構成の TPOT の壁は分離が取り除く干渉だが、decode のバッチサイズが大きくなることも TPOT を上げる要因であり、バッチサイズは記録していない。分離構成の TTFT の壁はハンドオフと、1 つしかない prefill エンジンの待ち行列だが、エンジン内部の段階別タイマーは転送の段階が負荷とともに伸びることを示す一方、その平均には死活確認も含まれていてクライアント側の p90 を分解する道具ではない。エージェント的な shape A の行は別の話をしており、g7.24xlarge 相当の例では分離構成の p90 TTFT の方が低い (462 ms 対 546 ms) のに、実効スループットは統合構成の方が高い。つまり 1 つのレイテンシ指標だけでは選択は決まらない。これらの壁は、1 台のノードに prefill 1 台と decode 1 台を同じ大きさで置き、コンパクトな KV キャッシュを持つ 160 億パラメータの mixture-of-experts モデルを配信する、この構成に固有のものである。
:::

## まとめ

自分の暫定的な判断に、ワークロード、GPU 割り当て、約束、条件を満たした到着率、実効 calls/s/GPU が含まれていることを確認する。このセッションの約束のもとで、それぞれのスタックの壁と、それが反映する分割のコストを言えることを確認する。両方のスタックについて予測を書いた試験を 1 つ記録したことを確認する。次の「まとめ」の章に進む。Optional の「約束の地図を深く読む」の章では、この地図を 1 マスずつ読み、測り直しが何を変えたかを示し、測り直しのコマンドを載せている。
