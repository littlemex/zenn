---
title: "MoE のルーティングはなぜ成り立つのか: router の学習、偏り、専門化を論文から読む"
free: false
---

## はじめに

Mixture of Experts（MoE）の層では、router と呼ぶ小さな線形層が、各 token をどの expert（専門家と呼ぶ部分ネットワーク）に送るかを決める。router は本体と一緒に学習され、選ぶ操作は上位 $k$ 個を取る離散的な操作である。ここには素朴に考えると成り立ちにくい点が 3 つある。離散的な選択に勾配がどう流れるのか、一部の expert にだけ token が集まって崩れないのか、そして router が本当に意味のある振り分けを学んでいるのか、である。**この 3 点には、それぞれ別の論文が、別の設定から説明を与えている**。この章では、勾配の通り道、expert への負荷の偏りの抑え方、専門化が示された条件を分けて、論文から整理する。

3 つ目の点は、固定の振り分けとの比較、公開モデルでの観察、分けて得になる理由の理論の 3 つに分けて読む。合わせて、この章では問いを次の 5 つに分ける。表の右の列は、章の結論を先に並べたものである。

| 問い | 論文から読める答え |
|---|---|
| 離散的な選択に、どう勾配が流れるのか | 選んだ expert の出力に掛ける gate の値を通して流れる。選ばなかった expert のパラメータには流れない |
| expert への負荷の偏りを、どう抑えるのか | 放っておくと偏りは自分で強まる。ノイズ、補助損失、割り当ての制約、bias の調整を、方式によって単独か組み合わせで使って外から抑えている |
| 学習した router は、固定の振り分けより役立つのか | 役立つ度合いは条件で変わる。token の頻度で均等にした hash（balanced hash）の固定の振り分けが、学習した router を上回る比較もある |
| router の選択は何と対応しているのか | 調べたモデルでは、token ID、構文、分野、次に予測する token、expert の出力のノルムとの対応が報告されている。対応の出方はモデルと測り方で違う |
| なぜ分けると得になるのか | クラスタの構造を持つ特定の分類問題で 2 層の非線形の CNN を expert にすると、router がクラスタの中心の特徴を学ぶことが証明されている。ReLU を使う密な FFN でも活性の大部分が 0 で、FFN を分けて一部だけ使う余地がある |

読者には Transformer の FFN 層と softmax の知識を想定する。扱う論文は 2017 年の Sparsely-Gated MoE から 2026 年 9 月までに arXiv などに出たものである。数値はすべて各論文の abstract か本文の報告値で、筆者はどれも再現していない。

![router が 1 つの token に 8 個の expert の logits を作り、softmax のあと上位 2 個（expert 3 と expert 5）を選び、2 つの出力を gate の値 p3 と p5 で重み付けして足す。勾配は p3 と p5 を通って router に戻り、残り 6 個の expert には届かない](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/moe-routing-why-it-works/routing-layer.png)

## router の計算と、勾配の通り道

[Switch Transformer の論文](https://arxiv.org/abs/2101.03961) の式で、router の計算を確かめる。token の表現を $x$、router の重みを $W_r$ とすると、router は expert ごとの logits $h(x) = W_r x$ を作り、softmax で expert $i$ の gate の値 $p_i(x)$ にする。

$$
p_i(x) = \frac{e^{h(x)_i}}{\sum_{j=1}^{N} e^{h(x)_j}}
$$

この式は、$N$ 個の expert のそれぞれに、合計が 1 になる gate の値を配ることを言っている。上位 $k$ 個の expert の集合を $\mathcal{T}$ とすると、層の出力は選んだ expert の出力 $E_i(x)$ を gate の値で重み付けした和になる。

$$
y = \sum_{i \in \mathcal{T}} p_i(x) \, E_i(x)
$$

上位 $k$ 個を選ぶ操作そのものは微分できない。それでも router が学習できるのは、出力に $p_i(x)$ が掛かっているからである。損失を下げる向きに $E_i(x)$ を強めたいとき、勾配は $p_i(x)$ を大きくする向きに router の重みを動かす。逆に、選ばれなかった expert のパラメータには、その token からの勾配は届かない。Switch の論文も、MoE でも Switch でも gate の値が router を微分可能にしていると書いている。

計算の動きを 1 つの token で追うと次のようになる。

1. router が 8 個の expert のそれぞれに logits を出し、softmax で gate の値にする。
2. 上位 2 個、たとえば expert 3 と expert 5 を選び、この 2 つだけが token を計算する。
3. 出力は $p_3 E_3(x) + p_5 E_5(x)$ になる。
4. 逆伝播では、$p_3$ と $p_5$ を通して router に勾配が戻る。選ばれなかった expert 1 は $E_1(x)$ を計算しないので、expert 1 のパラメータには、この token からの勾配は届かない。ただし Switch の式では、$N$ 個すべての logits に softmax を取ってから上位 $k$ 個を選ぶので、expert 1 向けの logits $h(x)_1$ も softmax の分母に入り、router の側では $p_3$ と $p_5$ を通して $h(x)_1$ も動く。Sparsely-Gated MoE のように上位 $k$ 個を残してから softmax を取る式では、選ばれなかった expert の logits は gate の値に入らないので、この経路は無い。

[Sparsely-Gated MoE の論文](https://arxiv.org/abs/1701.06538) の著者たちは、router に意味のある勾配を与えるには、少なくとも 2 つの expert を比べる必要があると考えていた。Switch Transformer はこれに反して上位 1 個だけを選び、品質を保ったまま router の計算を減らし、性能も上がったと報告した。1 個しか選ばなくても、gate の値が出力に掛かっていれば、router は「この expert をもっと信じるべきか」を学べる。

この勾配は近似でもある。[SparseMixer](https://arxiv.org/abs/2310.00811) は、ふつうの MoE の学習が疎な計算のために一部の勾配の項を無視していると指摘し、その項を常微分方程式の数値解法の考え方で近似した。Switch Transformer に入れると、学習の収束が最大 2 倍速くなったと報告している。

## 偏りは自分で強まる

router を何も制約せずに学習すると、少数の expert にだけ大きな gate の値を出す状態に落ちやすい。[Sparsely-Gated MoE の論文](https://arxiv.org/abs/1701.06538) はこれを観察し、偏りは自分で強まると書いた。よく選ばれる expert はそれだけ多く学習され、学習が進んだ expert はさらに選ばれやすくなるからである。前の節で見たとおり、勾配は選ばれた expert にしか届かないので、選ばれない expert は育たないまま残る。

![よく選ばれる expert ほど多く学習され、さらに選ばれやすくなる。補助損失、割り当ての制約、bias の調整、ノイズは、この循環の別々の場所に入る](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/moe-routing-why-it-works/imbalance-loop.png)

この循環を止める方法は、論文によって入れる場所が違う。

| 方法 | 入れる場所 | 代表の論文 |
|---|---|---|
| ノイズを足してから上位 $k$ 個を選ぶ | router の logits | [Sparsely-Gated MoE](https://arxiv.org/abs/1701.06538) |
| 負荷を均す補助損失 | 学習の目的関数 | [Sparsely-Gated MoE](https://arxiv.org/abs/1701.06538)、[GShard](https://arxiv.org/abs/2006.16668)、[Switch Transformer](https://arxiv.org/abs/2101.03961) |
| 均等な割り当てを解く | 選ぶ操作そのもの | [BASE Layers](https://arxiv.org/abs/2103.16716)、[Expert Choice](https://arxiv.org/abs/2202.09368) |
| expert ごとの bias を負荷で調整する | 選ぶ操作の入力 | [Loss-Free Balancing](https://arxiv.org/abs/2408.15664)、[DeepSeek-V3](https://arxiv.org/abs/2412.19437) |

Switch Transformer の補助損失は、GShard などにならって、Sparsely-Gated MoE の負荷と importance の 2 つの補助損失を 1 つにまとめたものである。この損失は、batch の中で expert $i$ に送られた token の割合 $f_i$ と、expert $i$ の gate の値の平均 $P_i$ の内積を使う。

$$
\mathcal{L}_{\text{aux}} = \alpha \cdot N \cdot \sum_{i=1}^{N} f_i \, P_i
$$

この式は、多く送られている expert ほど、その gate の値を下げる向きに勾配を出すことを言っている。$f_i$ は数えた割合なので微分できず、勾配は $P_i$ を通してのみ流れる。論文は、この損失が一様な分布のときに最小になり、一様な振り分けを促すと説明している。$f_i$ と $P_i$ がどちらもすべての expert で $1/N$ のとき、$\sum_{i=1}^{N} f_i P_i = N \cdot \frac{1}{N} \cdot \frac{1}{N} = \frac{1}{N}$ になる。式に $N$ を掛けるのは、このときの損失を expert の数によらず $\alpha$ にそろえ、expert の数を変えても $\alpha$ の意味が変わらないようにするためである。論文は係数 $\alpha$ を $10^{-1}$ から $10^{-5}$ まで振り、$10^{-2}$ が負荷を早く均しつつ、主目的関数である cross-entropy 損失を妨げなかったと報告している。

学習の安定は、負荷を均すこととは別の課題である。[ST-MoE](https://arxiv.org/abs/2202.08906) の router z-loss は、偏りではなく学習の不安定さに向けた損失である。router の logits が大きくなりすぎると、指数関数を使う softmax の計算で丸めの誤差が効いてくる。z-loss は、token ごとに router の logits の log-sum-exp を取り、その 2 乗を batch の $B$ 個の token で平均する。

$$
\mathcal{L}_z = \frac{1}{B} \sum_{b=1}^{B} \left( \log \sum_{j=1}^{N} e^{h(x^{(b)})_j} \right)^2
$$

$x^{(b)}$ は batch の $b$ 番目の token の表現で、$h(x^{(b)})_j$ はその token の expert $j$ への logits である。ST-MoE の論文は logits を $x$ と書くが、この章では前の節の $h(x)$ にそろえた。$\log \sum_j e^{h(x)_j}$ は softmax の分母の対数で、logits 全体の大きさを 1 つの数にまとめたものである。この値が大きいほどペナルティが大きいので、logits は小さく保たれる。論文は、これで品質を落とさずに学習が安定したと報告している。

補助損失には代償がある。[Loss-Free Balancing の論文](https://arxiv.org/abs/2408.15664) は、大きな補助損失は学習を妨げる勾配を持ち込み、性能を下げると指摘した。そこで Loss-Free Balancing は、損失を足す代わりに、expert ごとの bias を router のスコアに足してから上位 $k$ 個を選び、最近の負荷に応じてその bias を上げ下げする。[DeepSeek-V3](https://arxiv.org/abs/2412.19437) はこの方式を使い、bias は選ぶときだけに使い、出力に掛ける gate の値は元のスコアから作ると書いている。DeepSeek-V3 は、負荷が多すぎる expert の bias を $\gamma$ だけ下げ、少なすぎる expert の bias を $\gamma$ だけ上げる。最初の 14.3T token では $\gamma = 0.001$ だった。ただし DeepSeek-V3 は bias の調整に加えて、1 つの系列の中の極端な偏りを防ぐ小さな補助損失も使っている。batch 全体の負荷を均す仕組みと、系列の中の偏りを抑える仕組みを組み合わせた形である。

割り当てそのものを制約する方法もある。[BASE Layers](https://arxiv.org/abs/2103.16716) は token と expert の対応を線形割り当て問題として解き、各 expert がちょうど同じ数の token を受け取るようにした。補助損失も新しいハイパーパラメータも要らない。[Expert Choice](https://arxiv.org/abs/2202.09368) は向きを逆にし、token が expert を選ぶのではなく、expert が上位 $k$ 個の token を選ぶ。各 expert の受け取る量は固定になり、token によって使う expert の数が変わる。論文は同じ計算資源で Switch の上位 1 個、GShard の上位 2 個と比べ、学習の収束までの時間が半分以下になったと報告している。

gate の関数を softmax から sigmoid に替える流れもある。softmax は合計を 1 にするので、ある expert の gate の値を上げると他の expert の gate の値が下がり、expert どうしが競う。[Sigmoid gating の論文](https://arxiv.org/abs/2405.13997) は、真の expert の数より多い expert で回帰を当てはめる設定を理論で解析した。この設定で expert が ReLU や GELU を使う FFN の場合、sigmoid の gate のほうが少ないサンプルで expert を推定できることを示した。DeepSeek-V3 は sigmoid で expert ごとのスコア（affinity）を作り、選んだ expert のスコアを正規化して、出力に掛ける gate の値にしている。

## 学習した router は固定の振り分けより役立つのか

前の節の方法は、expert への負荷の偏りを抑えるためのものである。では、学習した router は固定の振り分けに比べて何を足しているのか。この問いを正面から調べたのが、router を学習しない比較である。

[Hash Layers](https://arxiv.org/abs/2106.04426) は、token ID を hash して expert を決める。router のパラメータも負荷の損失も要らない。比較に主に使うのは、論文が balanced hash と呼ぶ方式である。balanced hash は、学習の前に学習データの token の頻度を数え、頻度の高い token から順に最も空いている expert に割り当てて対応表を作る。1 つの層だけを MoE にし、64 個の expert、総パラメータ 751M でそろえた比較では、balanced hash が Switch より perplexity で 0.4〜0.5 良かった。BASE Layers と同じコードで振り分けだけを替えた 4.5B パラメータの比較でも、学習のどの時点でも hash が上回った。学習の後半には BASE がやや不安定になった。論文は、学習の初めに hash が早く expert を専門化させられたこと、後半の不安定さは BASE の割り当てが動いたためであることを、どちらも推測として書いている。

hash に何を入れるかについて、論文は 2 つの実験を挙げている。1 つ目は、現在の token で hash する方が、1 つ前の token で hash するより明らかに良かったことである（perplexity 23.16 と 24.16）。論文はこれを、その位置の token が最も関係の深い手がかりであることの裏付けとしている。2 つ目は、同じ Wikitext-103 を 8k の語彙に分けると hash が Switch をわずかに上回り、267k の語彙に分けると Switch が hash を上回ったことである。論文はこれを、語彙が大きいと hash の値の種類が増えすぎて学びにくくなるという仮説の裏付けとしている。

[THOR](https://arxiv.org/abs/2110.04260) の著者たちは機械翻訳で、乱数での振り分けが、gate による振り分けと同じ程度に働くと分析した。そのうえで、学習でも推論でも expert を乱数で選び、expert どうしの予測をそろえる正則化を足した THOR を提案し、多言語翻訳で Switch Transformer を BLEU で 2 上回ったと報告している。

一方で、学習した router に意味があるという結果もある。[Dikkala らの論文](https://aclanthology.org/2023.emnlp-main.583/) は、入力がクラスタに分かれた人工のデータで、router がそのクラスタどおりに振り分けを学ぶことを理論と実験で示した。T5X での実データの実験でも、学習する router の方が学習しない router より明らかに良かったと報告している。[Unified Scaling Laws for Routed Language Models](https://arxiv.org/abs/2202.01169) は、強化学習で学ぶ router、均等な割り当てを解く S-BASE、hash の 3 つを、5 桁にわたる大きさで比べ、S-BASE が最も良かったと報告した。

同じ論文は、密なモデルと振り分けのあるモデルを同じ軸で比べるために、実効パラメータ数を定義した。基準になる密なモデルのパラメータ数 $N$ を増やしていくと、振り分けを足したモデルの実効パラメータ数が $N$ を超えなくなる点がある。これを $N_{\text{cutoff}}$ と呼び、振り分けが性能を上げる範囲の上限の目安にしている。当てはめたスケーリング則から得た値は、S-BASE で 937B、強化学習の router で 85B、hash で 83B と手法で大きく違う。これは MoE の総パラメータ数ではなく基準のモデルの大きさについての値で、学習 token 数に強く依存する。論文は、token を増やせば $N_{\text{cutoff}}$ も大きくなると見込んでいる。後の節で扱う Scaling Laws for Fine-Grained MoE は、学習 token 数と expert の細かさも変数に入れ、計算予算に対する構成を選ぶ研究である。2 つの研究の結論は、比べる条件を分けて読む必要がある。

これらの比較は、何を変えたかが違う。Hash Layers の BASE との比較は振り分けだけを替え、THOR は乱数の振り分けに正則化を組み合わせ、Unified Scaling Laws は 3 つの方式を同じスケーリング則で比べた。合わせて読むと、token ID による固定の振り分けは強い比較の相手であり、学習した router の利得は方式と学習の条件をそろえて測る必要がある。学習した router が token ID の上に何を足しているのかは、これらの論文からは特定できない。次の節で見るように、公開モデルの分析では、振り分けが token ID にかなり強くよるという報告もある。

## router の選択は何と対応しているのか

router の選択が何と対応しているかは、公開されたモデルで調べられている。結果はモデルによって違い、その違いにも手がかりがある。

![OpenMoE では token ID に、Mixtral では構文と隣の token に沿った偏りが見え、トピックごとの偏りは小さい。OLMoE では arXiv や GitHub のような特徴のある分野で偏りがはっきり出て、後ろの層ほど次に予測する token との対応が強い](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/moe-routing-why-it-works/what-routers-see.png)

図の値は、各論文が自分の測り方で報告したもので、同じ基準で比べた値ではない。

[Mixtral の論文](https://arxiv.org/abs/2401.04088) は、The Pile の分野ごとに選ばれた expert の分布を層 0、15、31 で比べ、トピックに沿った明らかな偏りは見られなかったと書いている。arXiv の論文、PubMed の要約、哲学の文書で、分布は 3 つの層のどれでもよく似ていた。合成データの DM Mathematics では、分布がわずかに違った。論文は、router がある程度は構文に沿って振る舞うと見ている。Python の `self` や英語の `Question` は複数の token にまたがっても同じ expert に送られることが多く、コードの字下げの token はいつも同じ expert に送られた。連続する token が同じ expert に送られる割合も、特に後ろの層で、一様に選んだ場合より明らかに高かった。

[OpenMoE](https://arxiv.org/abs/2402.01739) は、振り分けが主に token ID で決まり、文脈にはあまりよらないことを見つけ、これを Context-Independent Specialization と呼んだ。さらに、token と expert の対応は事前学習の早い時期に決まり、その後ほとんど変わらなかった（Early Routing Learning）。分野ごとの分布もほぼ一様だった。論文は、この振り分けの不完全さが性能を下げうる例として、マルチターンの対話を挙げている。各 expert が 1 回に受け取れる token の数（容量）は決まっているので、文の後ろにある token ほど、送り先の expert がすでに満杯で捨てられる危険が高い。論文はこれを Drop-towards-the-End と呼んだ。

[OLMoE](https://arxiv.org/abs/2409.02060) は違う結果を報告した。64 個の expert から 8 個を選ぶ OLMoE-1B-7B では、arXiv や GitHub のような特徴のある分野で、偶然よりはっきり高い割合で使われる expert が多くあった。層 0 には、arXiv の token のほぼすべてで上位 8 個に選ばれる expert があった。C4 のような一般の分野では、expert の使われ方がずっと均等だった。同じ方法で Mixtral 8x7B を測ると、どの層のどの分野でも一様な振り分けに近かった。OLMoE の著者たちは、Mixtral が密なモデルの Mistral から作られた（upcycling）ことが原因だと仮説を立てている。すべての expert が同じ局所解から始まるので、専門化の余地が狭まるという見方である。ただし、この比較だけでは差の要因を特定できない。

OLMoE は token の種類（語彙）ごとの専門化も測った。測り方は、ある token ID が現れたとき、その token が特定の expert に送られる割合である。後ろの層ほど、選ばれる expert と、モデルが予測した次の token ID との対応が、入力の token ID との対応より強かった。

OLMoE は router がいつ決まるかも測った。測り方は、各 token について途中の checkpoint と最終の checkpoint が選ぶ上位 8 個の expert の重なりを数え、その割合を平均するものである。事前学習の 1%（20B token）の時点で、この割合は層によって最大で約 60% に達し、40% の時点で最大で約 80% になった。一様に選んだ場合の値は 12.5% である。後ろの層ほど早く決まり、層 0 は他より遅かった。この観察は OpenMoE の Early Routing Learning とも合う。

[A Closer Look into MoE in LLMs](https://arxiv.org/abs/2406.18219) は別の角度から見て、Mixtral 8x7B でも DeepSeekMoE でも、選ばれた expert の出力のノルムが大きい傾向を観察した。選択と出力のノルムの結びつきは、Mixtral の方が DeepSeekMoE よりはっきり出たと論文は書いている。これは選択と出力の関連で、router が選ぶときに出力のノルムを手がかりにしたことを示す結果ではない。同じ論文は expert どうしのパラメータと出力の類似度も調べ、調べたモデルでは深い層ほど類似度が下がり、最後の層で急に上がる傾向を報告した。router の選び先の偏りと、expert 自体の計算の違いは、別々に測る量である。

## なぜ分けると得になるのか

分けて得になる理由には、2 種類の説明がある。1 つはクラスタの構造を持つ問題での学習の利点を示す理論で、もう 1 つは密な FFN の活性が疎であるという、分けて使う設計の動機である。この 2 つは種類の違う根拠なので、別々に見る。

![論文の設定では、クラスタの構造を持つ問題で、router がクラスタの中心を学び、非線形の expert がそれぞれ単純な部分問題を解く。ReLU を使う密な FFN でも活性は疎で、学習済みの FFN を分けて一部だけ使える](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/moe-routing-why-it-works/why-splitting-helps.png)

図は左右とも概念図である。左は、論文の patch を組み合わせた分類問題と 2 層の CNN の expert を 2 次元の点に単純化したもので、論文は 1 つの expert が少なくとも 1 つのクラスタを受け持つと言っている。右の 24 個の hidden unit と 4 つの block の数は、実際の FFN と対応しない。

データの側の説明は [Towards Understanding Mixture of Experts in Deep Learning](https://arxiv.org/abs/2208.02813) が代表である。対象は、特徴、クラスタ、ノイズの patch を組み合わせた特定の分類問題と、2 層の CNN を expert にした設定である。この問題は 1 つの expert では学びにくく、著者たちの実験では、非線形の expert の混合がクラスタの構造を取り戻し、線形の expert の混合を大きく上回った。線形の expert の混合も、1 つの expert は上回った。定理では、2 層の非線形の CNN を expert にした MoE が勾配降下でこの問題を学べること、そのとき router がクラスタの中心の特徴を学び、問題を各 expert が解ける線形の部分問題に分けることを証明した。各 expert がどのクラスタを受け持つかは、重みの初期値で決まる。

この論文は、学習の初めに何が起きるかも説明している。router の重みは 0 から始まるので、最初は $M$ 個の expert（この論文は expert の数を $M$ と書く）がどれもほぼ同じ確率 $1/M$ で選ばれる。著者たちはこの時期を探索の時期と呼び、router がほぼ一様に expert を選ぶあいだに各 expert が育つと分析した。論文の解析では、logits に足すノイズのおかげで、学習の反復 $t$ から $t'$ の間に router の出力が少し変わっても、expert を選ぶ確率は少ししか変わらない。これは、Sparsely-Gated MoE がノイズに期待した、負荷を均す効果とは別の役割である。

同じ方向の結果は他にもある。[Patch-level routing の論文](https://arxiv.org/abs/2306.04073) は、2 層の CNN を expert にした特定の教師あり分類問題で、各 expert に入力の一部の patch だけを送る pMoE を解析した。論文は、この設定の pMoE では、容量が同じか大きい 1 つの expert より、目標の汎化性能に達するのに要る学習サンプル数（sample complexity）が減ることを証明した。鍵は、router がラベルに関係のない patch を除き、クラスを見分けるのに役立つ似た patch を同じ expert に送ることである。[A Theoretical View on Sparsely Activated Networks](https://arxiv.org/abs/2208.04461) は、局所性鋭敏型ハッシュ（LSH）で振り分ける疎なネットワークを解析した。LSH は、近い入力が同じ値になりやすい hash である。論文は、この疎なネットワークが Lipschitz 連続な関数を密なネットワークと同じ精度で近似できることを示した。各 expert が入力空間の別々の領域で目標の関数を補間するからである。2026 年 9 月の [Towards a Statistical Understanding of MoE](https://arxiv.org/abs/2609.03501) は、MoE を入力に応じて局所的に予測を集める仕組みとして捉え、誤差を近似、expert の学習、router の推定の 3 つに分けて上界を出した。共有の expert が全体に共通の構造を受け持ち、振り分ける expert が局所の残りに集中できることも、この枠組みで説明している。

疎に計算できることと、expert を精度よく推定できることは別の問題である。[Statistical Perspective of Top-K Sparse Softmax Gating MoE](https://arxiv.org/abs/2309.13850) は Gaussian の MoE を解析し、真の expert の数より多い expert で当てはめる場合、密度の推定が収束するには、上位から選ぶ expert の数に条件が要ることを示した。この設定ではパラメータの推定がかなり遅くなり、論文はその原因を softmax の gate と expert の関数の間の相互作用に求めている。これは Transformer の学習を直接説明する定理ではない。

FFN の側の説明は、密な FFN がもともと疎に動いているという観察である。[Lazy Neuron の論文](https://arxiv.org/abs/2210.06313) は、ReLU の後の FFN の活性のうち 0 でないのは、T5-Base で平均 3.0%、ViT-B16 で 6.3% にすぎないと報告した。大きなモデルほど疎で、ラベルを乱数にしても、入力を乱数にしても疎さが現れた。[MoEfication](https://arxiv.org/abs/2110.01786) は、学習済みの FFN の重みを分けて expert とし、router を付けて MoE に変えた。FFN のパラメータの 10〜30% だけを使って、元の性能の 95% 以上を保てた。[Approximating Two-Layer Feedforward Networks](https://arxiv.org/abs/2310.10837) は、MoE を 2 層の FFN を近似する方法の 1 つとして整理し、パラメータの数をそろえた比較でも MoE が密な Transformer-XL と競えることを示した。ただし、学習した router が固定の振り分けより何を足すかまでは、この活性の疎さの観察だけでは説明できない。

## 専門化を強める設計

expert の専門化は、MoE の構造だけで保証されるものではない。[Improving Expert Specialization in MoE](https://arxiv.org/abs/2302.14703) は、元の MoE の構成と学習の方法では、MNIST や FashionMNIST のような単純なデータでも、直感的な問題の分け方と expert の十分な利用が得られない例を示した。これは Transformer の疎な MoE を直接調べた結果ではないが、専門化を設計の狙いと実際の観察に分けて読む理由になる。

以下では、expert を細かく分けたり共有したりして専門化を促す設計と、振り分けの揺れ、表現の崩壊、離散的な選択にそれぞれ対処する設計を見る。

[DeepSeekMoE](https://arxiv.org/abs/2401.06066) は、従来の MoE の問題を 2 つに分けた。1 つ目は、expert の数が 8 や 16 と少ないと、1 つの expert に多様な知識が混ざることである。2 つ目は、別々の expert が共通の知識をそれぞれ覚えて重複することである。DeepSeekMoE は、元の MoE が $N$ 個の expert から $K$ 個を選ぶとき、まず各 expert を $m$ 個に分け、$mN$ 個の小さい expert から $mK$ 個を使う形にした。各 token が使うパラメータの量は変えずに、組み合わせの数を増やすためである。次に、このうち $K_s$ 個をすべての token が使う共有の expert とし、計算量を保つために、残りの $mN-K_s$ 個から $mK-K_s$ 個を選ぶ形にした。論文は、DeepSeekMoE 2B が、expert のパラメータと計算が 1.5 倍の GShard 2.9B と同等の性能になったと報告している。

[Scaling Laws for Fine-Grained MoE](https://arxiv.org/abs/2402.07871) は、expert の細かさ（granularity）をハイパーパラメータに加えたスケーリング則を立てた。論文は、granularity を予算に合わせて選んだ設定が、ほとんどどの計算予算でも、expert の大きさを元の FFN と同じにする慣習を上回ったと報告している。MoE と密なモデルの効率の差は、モデルと学習予算を大きくするほど広がった。

振り分けの揺れを抑える設計もある。[StableMoE](https://arxiv.org/abs/2204.08396) は、学習の途中で同じ入力の行き先の expert が変わる現象を routing fluctuation と呼んだ。同じ入力が別々の expert を更新するのに、推論では 1 つしか使わないので、サンプルが無駄になる。StableMoE は学習を 2 つの段階に分ける。1 つ目の段階で振り分けを学んで軽い router に蒸留し、2 つ目の段階でその router を固定して学習する。[X-MoE](https://arxiv.org/abs/2204.09179) は、router が token を expert の中心のまわりに集めることで、表現が崩壊する傾向があると指摘し、低次元の超球面の上で router のスコアを計算した。

expert を選ぶ操作そのものをやめる方向もある。[Soft MoE](https://arxiv.org/abs/2308.00951) は、各 expert にすべての入力 token の重み付きの和を渡し、離散的な選択をなくした。完全に微分でき、token を捨てることもない。論文は、画像認識で Soft MoE が Tokens Choice と Experts Choice の MoE を上回ったと報告している。

## まとめ

router は、gate の値を通して流れる勾配で、どの expert の出力を強めるかを学ぶ。偏りが自分で強まる循環は、ノイズ、補助損失、割り当ての制約、bias の調整で外から抑える。router の学習は、この 2 つの上に成り立っている。学習した router が足す利得は、token ID による固定の振り分けと比べると条件で変わる。公開モデルの分析では、router の選択が token ID、構文、分野、次に予測する token と対応する例が報告されたが、対応の出方はモデルと測り方で違う。理論の論文は、クラスタの構造を持つ特定の分類問題で 2 層の非線形の CNN を expert にした場合に、router がそのまとまりを学べることを示した。ReLU を使う密な FFN の活性の疎さは、FFN を分けて一部だけ使う動機を与えている。

## 参考: この章で扱った研究

| 研究 | 公開 | この章での論点 |
|---|---|---|
| [Sparsely-Gated MoE](https://arxiv.org/abs/1701.06538) | 2017 年 1 月 | ノイズを足した上位 $k$ 個の選択、偏りが自分で強まる観察 |
| [GShard](https://arxiv.org/abs/2006.16668) | 2020 年 6 月 | 上位 2 個の選択と負荷分散の補助損失 |
| [Switch Transformer](https://arxiv.org/abs/2101.03961) | 2021 年 1 月 | 上位 1 個でも router が学べること、負荷分散の補助損失 |
| [BASE Layers](https://arxiv.org/abs/2103.16716) | 2021 年 3 月 | 線形割り当てによる均等な振り分け |
| [Hash Layers](https://arxiv.org/abs/2106.04426) | 2021 年 6 月 | 学習しない振り分けとの比較 |
| [MoEfication](https://arxiv.org/abs/2110.01786) | 2021 年 10 月 | 密な FFN を分けて MoE にする |
| [THOR](https://arxiv.org/abs/2110.04260) | 2021 年 10 月 | 乱数で expert を選ぶ方式 |
| [Unified Scaling Laws for Routed LMs](https://arxiv.org/abs/2202.01169) | 2022 年 2 月 | 振り分けの方式ごとのスケーリング則 |
| [ST-MoE](https://arxiv.org/abs/2202.08906) | 2022 年 2 月 | router z-loss による安定化 |
| [Expert Choice](https://arxiv.org/abs/2202.09368) | 2022 年 2 月 | expert が token を選ぶ向き |
| [StableMoE](https://arxiv.org/abs/2204.08396) | 2022 年 4 月 | 振り分けの揺れと router の固定 |
| [X-MoE](https://arxiv.org/abs/2204.09179) | 2022 年 4 月 | 振り分けによる表現の崩壊 |
| [Towards Understanding MoE](https://arxiv.org/abs/2208.02813) | 2022 年 8 月 | クラスタ構造と非線形の expert の理論 |
| [A Theoretical View on Sparsely Activated Networks](https://arxiv.org/abs/2208.04461) | 2022 年 8 月 | LSH による振り分けと近似の能力 |
| [Lazy Neuron](https://arxiv.org/abs/2210.06313) | 2022 年 10 月 | 密な FFN の活性の疎さ |
| [Improving Expert Specialization in MoE](https://arxiv.org/abs/2302.14703) | 2023 年 2 月 | 単純なデータでの専門化の失敗例 |
| [Patch-level routing](https://arxiv.org/abs/2306.04073) | 2023 年 6 月 | patch の振り分けとサンプルの効率 |
| [Soft MoE](https://arxiv.org/abs/2308.00951) | 2023 年 8 月 | 離散的な選択をなくした MoE |
| [Statistical Perspective of Top-K Sparse Softmax Gating MoE](https://arxiv.org/abs/2309.13850) | 2023 年 9 月 | 上位 $k$ 個の gate と expert の推定の条件 |
| [SparseMixer](https://arxiv.org/abs/2310.00811) | 2023 年 10 月 | router の勾配の近似 |
| [Approximating Two-Layer FFNs](https://arxiv.org/abs/2310.10837) | 2023 年 10 月 | MoE を FFN の近似として整理 |
| [Dikkala ら](https://aclanthology.org/2023.emnlp-main.583/) | 2023 年 12 月 | 学習する router の利得の理論と実験 |
| [Mixtral](https://arxiv.org/abs/2401.04088) | 2024 年 1 月 | トピックより構文に沿った振り分け |
| [DeepSeekMoE](https://arxiv.org/abs/2401.06066) | 2024 年 1 月 | 細かい expert と共有の expert |
| [OpenMoE](https://arxiv.org/abs/2402.01739) | 2024 年 1 月 | token ID による振り分けと早い確定 |
| [Scaling Laws for Fine-Grained MoE](https://arxiv.org/abs/2402.07871) | 2024 年 2 月 | expert の細かさのスケーリング則 |
| [Sigmoid gating](https://arxiv.org/abs/2405.13997) | 2024 年 5 月 | sigmoid の gate のサンプル効率 |
| [A Closer Look into MoE in LLMs](https://arxiv.org/abs/2406.18219) | 2024 年 6 月 | 出力のノルムと router の選択 |
| [Loss-Free Balancing](https://arxiv.org/abs/2408.15664) | 2024 年 8 月 | 補助損失を使わない負荷の調整 |
| [OLMoE](https://arxiv.org/abs/2409.02060) | 2024 年 9 月 | 分野と語彙への専門化、router の早い確定 |
| [DeepSeek-V3](https://arxiv.org/abs/2412.19437) | 2024 年 12 月 | bias による負荷の調整と sigmoid のスコア |
| [Towards a Statistical Understanding of MoE](https://arxiv.org/abs/2609.03501) | 2026 年 9 月 | 局所的な集約としての MoE と共有の expert |
