---
title: "Attention の潮流 (1): 帯域の壁と、KV を軽く持ち、選んで読む流れ"
free: false
---

## はじめに

2017 年の Transformer から 2026 年の DeepSeek-V4.1-Flash まで、Attention の変種は数十個ある。名前だけを並べると類型の多さに埋もれるが、**長い文脈の推論という観点から見ると、ほとんどの変種は、KV-Cache（過去のトークンの Key と Value を保存しておく場所）が重すぎるという 1 つの問題への別々の答えとして読める**。元の論文の目的には学習時の計算を減らすことも含まれるが、この 2 章ではこの観点に揃えて並べる。変種を 1 つずつ解説する代わりに、各社が何を問題と考え、どの方向に手を打ち、何が残ったかを、流れとして整理する。

答えの方向は大きく 3 つに分かれる。この章は前の 2 つを扱い、次の章は 3 つ目を扱う。表の手法の名前は、本文で順に説明する。

| 方向 | 何を減らすか | 代表 | 扱う章 |
|---|---|---|---|
| 持ち方を軽くする | 1 トークンぶんの KV のバイト数 | GQA、MLA、KV の量子化 | この章 |
| 読む範囲を選ぶ | 1 回の生成で読むトークンの数 | SWA、DSA | この章 |
| 覚え方を変える | KV そのもの。過去を固定の大きさの状態に圧縮する | Mamba、DeltaNet と、それらのハイブリッド | [次の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/llm2026-elements) |

用語を 1 つ決めておく。この 2 章では、過去の全トークンを読む普通の softmax Attention を full Attention と呼ぶ。読者には、Attention の Query、Key、Value の計算と、GPU のメモリ階層の基礎を想定する。Softmax Attention の動きは筆者の解説「[Softmax Attention をビジュアルに理解する](https://littlemex.github.io/explainers/softmax-attention-explainer.html)」で、個々の変種の式は「[Attention の系統と進化](https://littlemex.github.io/explainers/attention-series-index.html)」の各章で扱っている。

## 何が設計を縛っているか

LLM の推論は、入力をまとめて処理する prefill と、1 トークンずつ出力する decode の 2 つの局面に分かれる。変種を生んだ制約は 3 つある。decode は計算ではなくメモリからの読み出しで詰まること、文脈が長いほど読み出しと保存の大半が KV になること、prefill の計算が文脈の長さの 2 乗で増えることである。

### decode はメモリ帯域で詰まる

小さなバッチの decode の速さを決めるのは、計算ではなく HBM（GPU の主メモリで、DRAM の一種）からの読み出しである。decode は 1 トークンを出すたびに重みと KV-Cache を読み直すのに、1 バイトあたりの計算は数回しかないからである。例えるなら、組み立ての作業員はとても速いのに、倉庫から部品を運ぶ通路が細い工場である。作業員を増やしても、通路の太さで生産量が決まる。

この数回を見積もる。全パラメータを使う普通のモデル（MoE でないモデル）がバッチ 1 で 1 トークンを生成するとき、重みの部分だけを取り出すと、パラメータ数 $N$ に対して計算はおよそ $2N$ 回（掛け算と足し算を 1 回ずつ数える）である。読み出しは $N \times (\text{1 パラメータのバイト数})$ である。1 バイト読むごとの計算回数を演算強度と呼ぶ。演算強度は、BF16（2 バイト）で 1、FP8（1 バイト）で 2、FP4（0.5 バイト）でも 4 にしかならない。

![横軸に演算強度をとった図。decode は左端の帯域律速の領域にあり、GPU の世代が進むほど、計算で詰まり始める折れ点が右へ動く](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/llm2026-overview/roofline-wall.png)

GPU が計算で詰まり始める演算強度を、ルーフラインの折れ点（ridge point）と呼ぶ。折れ点は、ピークの計算性能を帯域で割った値である。各世代で主に使える最も低い精度について、NVIDIA のデータシートの疎性による倍増を含まない値で計算すると、次のようになる。

| GPU | 精度 | 折れ点（FLOP / バイト） |
|---|---|---|
| A100（80GB） | BF16 | 153 |
| H100 | FP8 | 591 |
| B200 | FP4 | 約 1100〜1250（データシートの版で異なる） |

decode の 1〜4 は、どの世代でも 2 桁以上左にある。しかも世代ごとに低い精度の計算が足され、計算性能の伸びが帯域の伸びを上回るので、使える最も低い精度で比べた折れ点は右へ動き続ける。[AI and Memory Wall](https://arxiv.org/abs/2403.14123) は、ピークの計算性能（1 秒あたりの浮動小数点演算の回数）が 2 年で約 3.0 倍になるのに対し、DRAM の帯域は 1.6 倍しか伸びないと指摘した。読むバイトを減らす工夫の価値は、世代を追うごとに上がる。

### 長い文脈では読むバイトの大半が KV になる

文脈が長くバッチが大きいほど、decode で読むバイトの大半は重みではなく KV になる。重みはバッチの全リクエストで共有して 1 回読めば済むが、KV は入力ごとに別物で共有できないからである。共通の先頭部分を持つリクエストの間では、その部分の KV を使い回す仕組み（prefix caching）があるが、分かれた後の KV は共有できない。標準的な full Attention では、1 リクエストの KV のバイト数は次の式で書ける。

$$
\text{Bytes}_{\text{KV}} = 2 \cdot n_{\text{layer}} \cdot n_{kv} \cdot d_h \cdot b_{\text{KV}} \cdot L
$$

この式は、Key と Value の 2 つ、層の数 $n_{\text{layer}}$、KV のヘッドの数 $n_{kv}$、ヘッドの次元 $d_h$、1 要素のバイト数 $b_{\text{KV}}$、系列の長さ $L$ をすべて掛けると KV の大きさになることを言っている。

この式の量には、2 つの別の顔がある。1 つは HBM に保存しておくバイトで、同時に載せられるリクエストの数を決める。もう 1 つは 1 トークンの生成ごとに読むバイトで、decode の時間を決める。標準の full Attention は毎ステップ全 KV を参照するので、論理的には 2 つは同程度の量になる（実際の HBM の読み出しの回数は、カーネルの作りで変わる）。一方、後で見る疎な選択では、読むバイトだけが減り、保存するバイトは減らない。prefill には別の問題があり、全トークンの組を計算するので計算量が $L^2$ で増え、最初のトークンが返るまでの時間（TTFT）を押し上げる。

この章と次の章の工夫は、おおまかには、この式のどの因子を削るかで分類できる。1 トークンが KV-Cache に持つ 1 件のデータをエントリと呼ぶと、$n_{kv} \cdot d_h$ はエントリの大きさにあたる。

![KV のバイト数の式を、層、エントリの次元、精度、系列の 4 つの軸に分け、それぞれを削る手法を並べた図。系列の軸の疎な選択は読むバイトだけを減らす](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/llm2026-overview/kv-four-axes.png)

| 軸 | 式の因子 | 削り方 | 代表 | 減るもの |
|---|---|---|---|---|
| 層 | $n_{\text{layer}}$ | 層をまたいで同じ KV や選択の結果を使う | CLA、YOCO、CSA2 | 保存（読み出しが減るかは実装次第） |
| エントリの次元 | $n_{kv} \cdot d_h$ | ヘッドの間で共有する、低ランクに圧縮する | MQA、GQA、MLA | 保存と読み出し |
| 精度 | $b_{\text{KV}}$ | 低ビットで持つ | KIVI、MXFP4 の KV | 保存と読み出し |
| 系列 | $L$ | 窓で切る、まとめて 1 つにする | SWA、CSA の圧縮 | 保存と読み出し |
| 系列 | $L$ | 重要でないものを捨てる | H2O、SnapKV | 保存と読み出し（捨てた KV は取り戻せない） |
| 系列 | $L$ | 全部持ったまま重要なものだけ選んで読む | Quest、NSA、DSA | 読み出しと計算だけ |

軸の効き方はおおむね掛け算になるが、実際のモデルでは方式ごとにキャッシュの形が違うので、容量はキャッシュの種類ごとの和で見積もる必要がある。後で見るように、2026 年の DeepSeek-V4.1-Flash は、4 つの軸を 1 つの設計で同時に削った。

### Attention 以外の答えは紹介にとどめる

同じ帯域の問題には、Attention の外にも答えがある。この本では別の章が受け持つので、ここでは位置づけだけを書く。

- MoE（Mixture of Experts）は、複数の専門家の FFN から一部だけを使う構造で、1 トークンで読む重みのバイトを減らす。2026 年 11 月時点で重みが公開されている最上位のモデルは、ほぼすべて MoE である。DeepSeek-V4-Pro は 1.6 兆パラメータのうち 490 億だけを使い、使う割合は約 3% である。どの専門家を使うかを決める router の仕組みは「[MoE のルーティングはなぜ成り立つのか](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/moe-routing-why-it-works)」で扱う。
- 低ビット化は、重みと KV の 1 要素のバイト数を下げる。FP8 での学習が広がり、FP4 を学習に取り入れるモデルも現れた。KV の量子化の理論は「[TurboQuant と PolarQuant](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/turboquant-polarquant)」で扱う。
- 投機的デコード（speculative decoding）は、下書きが出した数トークンを本体が 1 回でまとめて検証し、読み出しの回数を減らす。下書きは小さな別モデルのことも、本体に付けた MTP（Multi-Token Prediction）と呼ぶ先読みの予測層のこともある。決まった受理と補正の規則で検証すれば、出力の分布は本体だけで生成したときと一致する（[Leviathan ら](https://arxiv.org/abs/2211.17192)、[Chen ら](https://arxiv.org/abs/2302.01318)）。2025 年から 2026 年前半には多くのモデルが MTP の層を載せた。ただし大きなバッチでは、捨てられる下書きの検証の計算がスループットを下げることがある。採否も分かれていて、Kimi K2.5 は公開の設定で MTP の層を持たない。投機的デコードそのものの仕組みはこの本では扱わないが、固定の状態を持つモデルとの相性の悪さは次の章で見る。

## 持ち方を軽くする流れ

この流れの問いは「1 トークンぶんの KV を、どこまで小さく持てるか」である。各社は、ヘッドの間で共有する、低ランクに圧縮する、層の間で共有する、ビット数を下げる、とそれぞれ別の軸を削っていった（図の並びは考え方の順で、発表順とは少し違う）。出発点の MHA（Multi-Head Attention）は、ヘッドごとに別々の Key と Value を持っていた。

![持ち方を軽くする流れと、読む範囲を選ぶ流れの系統図。2017 年の MHA から始まり、2026 年の CSA2 で 2 つの流れが合流する](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/llm2026-overview/softmax-lineage.png)

### ヘッドの間で共有する

最初の一手は、Query のヘッドの数はそのままに、Key と Value のヘッドを減らすことだった。[MQA](https://arxiv.org/abs/1911.02150)（Multi-Query Attention、2019 年）は、全ヘッドで KV を 1 組だけ共有した。MQA で KV は大きく減るが、品質も落ちやすい。[GQA](https://arxiv.org/abs/2305.13245)（Grouped-Query Attention、2023 年）は、ヘッドをグループに分けてグループごとに KV を共有し、MHA と MQA の間を取った。Llama 2 の 70B や Mistral 7B が GQA を採用し、MoE でないモデルでは GQA がほぼ標準になった。GQA が削ったのは式の $n_{kv}$ である。

### 低ランクに圧縮する

次の問いは「共有する代わりに、KV を小さなベクトルに畳めないか」だった。[DeepSeek-V2](https://arxiv.org/abs/2405.04434)（2024 年）の MLA（Multi-head Latent Attention）は、トークンごとに、全ヘッドに共通の低次元の潜在ベクトル $c_s$ を 1 本だけキャッシュする。各ヘッドは、ヘッドごとの行列 $W_{UK}$ で $c_s$ から Key を $k_s = W_{UK} c_s$ と展開する。行列の次元を下げて情報を畳むことを、低ランクの圧縮と呼ぶ。

MLA のスコアは、位置を扱う部分と扱わない部分に分けて計算する。位置を回転で教える RoPE は、回転の角度が Query と Key の位置ごとに変わるので、Query と Key の間に位置ごとに違う行列が挟まる。そのため位置を扱う部分は、次に述べる行列のまとめ方ができない。位置を扱わない部分は、Query の側も潜在ベクトル $q_t'$ から $q_t = W_{UQ} q_t'$ と作ると、次のように書ける。

$$
q_t^\top k_s = q_t'^{\top} \left( W_{UQ}^\top W_{UK} \right) c_s
$$

この式は、括弧の中の行列を前もって 1 つにまとめておけば（行列の吸収）、decode のたびに Key を展開せず、小さな $c_s$ を読むだけで済むことを言っている。位置を扱う部分のために、MLA は位置用の小さな Key を別に持ち、潜在ベクトルと一緒にキャッシュする（DeepSeek では潜在 512 次元と位置用 64 次元）。読んだ $c_s$ は全ヘッドの Query が使い回すので、読むバイトは 1 組分なのに計算はヘッドの数だけある。MLA は 1 バイトあたりの計算を増やし、帯域の壁から少し離れる。

DeepSeek-V2 は、前の世代の DeepSeek 67B と比べて KV-Cache を 93.3% 減らしたと報告した。同じ報告で、モデル全体の生成のスループットは最大 5.76 倍になったが、この比較は MoE を含むモデル全体の比較で、MLA だけの効果ではない。Kimi K2 系も MLA を採用した。MLA が削るのは KV のバイトで、全トークンを参照する構造は変えないので、prefill の $L^2$ の計算は残る。

### 層をまたいで共有する、ビット数を下げる

KV を層の数だけ持つ必要はあるか、1 要素を何ビットまで落とせるか、という残る 2 つの問いにも、2024 年に答えが出始めた。層の軸では、[CLA](https://arxiv.org/abs/2405.12981)（Cross-Layer Attention）が隣り合う層で KV を共有した。[YOCO](https://arxiv.org/abs/2405.05254)（You Only Cache Once）は、前半の self-decoder の出力から global な KV を 1 組だけ作る。後半の cross-decoder の全層は、その 1 組の KV を参照する。YOCO の構成は、層の数だけ KV を持つ無駄をなくした。精度の軸では、[KIVI](https://arxiv.org/abs/2402.02750) が、追加の学習なしで、古いトークンの KV を 2 ビットに量子化しても品質を保てることを示した。KIVI は直近のトークンの KV を高い精度のまま残す。その後、精度の軸は学習時の量子化と組み合わせて、FP8 や FP4 の KV として実際のモデルに入った。

## 読む範囲を選ぶ流れ

この流れの問いは、KV を小さくしても $L$ のまま残る読むトークンの数を減らせるか、である。選び方は、直近の窓だけを読む位置の選択から、内容を見て重要なトークンを選ぶ選択へ移った。持ち方の流れが KV を小さくしても、読むトークンの数と prefill の $L^2$ は残るので、この流れが並行して進んだ。

### 位置で選ぶ

最も単純な規則は「直近の $w$ トークンだけを読む」SWA（Sliding Window Attention）で、窓の層の KV は窓幅で頭打ちになり、$L$ に依存しなくなる。長文処理向けの先行例は、局所の窓と少数の global なトークンを組み合わせた [Longformer](https://arxiv.org/abs/2004.05150)（2020 年）である。生成の LLM では、[Mistral 7B](https://arxiv.org/abs/2310.06825) が窓幅 4096 の SWA を広めた。窓の層は窓の外を直接読めないので、遠くを読む手段として full Attention の層を一部に混ぜるモデルが多い。Gemma 3 は窓の層 5 層に full Attention の層 1 層、Step-3.5 は 3 対 1 で混ぜる。

後から窓に切り替えるときの落とし穴も見つかった。full Attention で学習したモデルを推論時だけ窓にすると、先頭付近のトークンを追い出した瞬間に品質が崩れる。多くのモデルでは、内容の上では重要でない先頭のトークンに注意の重みが集まる現象（attention sink）が観測されている。softmax の重みの合計が 1 になる制約が、attention sink の背景にあると考えられている。[StreamingLLM](https://arxiv.org/abs/2309.17453) は、先頭の数トークンを常に残すことで、窓に切り替えたときの崩壊を大きく和らげた。ただし先頭を残しても、窓の外の内容を読めるようになるわけではない。

attention sink には、sink をあらかじめ設計に入れる答えと、sink を抑える答えも出た。OpenAI の [gpt-oss](https://arxiv.org/abs/2508.10925)（2025 年 8 月）は、窓幅 128 の SWA の層と full Attention の層を交互に積み、ヘッドごとに学習した sink の項を softmax の分母に足した。Qwen チームの著者を含む [Gated Attention](https://arxiv.org/abs/2505.06708)（2025 年 5 月）は、ヘッドの出力に sigmoid のゲートを掛け、sink の形成そのものを抑えた。Gated Attention は KV を変えないが、学習の安定や長い文脈での振る舞いに効くと報告された。この出力のゲートは、次の章で見る Qwen3-Next、Qwen3.5、Kimi K3 の Attention の層に入っている。

### 内容で選ぶ

位置で切ると、文書の冒頭で定義されて最後に参照される語のような、遠くの重要なトークンを取りこぼす。そこで「今の Query にとって重要なトークンを、内容を見て選ぶ」方向に進んだ。2023〜2024 年には、学習を変えずに推論時だけ選ぶ方式が先に広がった。[H2O](https://arxiv.org/abs/2306.14048) や [SnapKV](https://arxiv.org/abs/2404.14469) は、注意の重みが小さいトークンの KV を捨て、保存する量も減らす。捨てた KV は後で必要になっても取り戻せない。[Quest](https://arxiv.org/abs/2406.10774) は、KV を全部持ったまま、Query ごとに重要なページだけを読む。どの方式も、多くの Query で注意の重みが少数のトークンに集中するという観測に頼っている。

集中が強ければ、選んだトークンだけで出力をよく近似できる。選んだ集合 $\mathcal{S}$ の外の重みの合計を $\varepsilon$ とし、外の項を落とすだけで重みを正規化し直さない出力を $\hat{o}$ とすると、誤差は次で抑えられる。

$$
\| o - \hat{o} \| \le \varepsilon \max_i \| v_i \|
$$

この式は、捨てた重みが小さければ出力もほとんど変わらないことを言っている。選んだトークンだけで softmax を取り直す実装では、右辺の係数が 2 になる。どちらの場合も、$\varepsilon$ が実際に小さいことは保証ではなく観測である。

2025 年 2 月には、学習の段階から疎な選択を組み込む 2 本が出た。DeepSeek の [NSA](https://arxiv.org/abs/2502.11089)（Native Sparse Attention）は、トークンをまとめた粗い要約、選んだブロック、直近の窓の 3 つの経路を束ね、GPU で速く動くブロック単位で選ぶ。Kimi の [MoBA](https://arxiv.org/abs/2502.13189)（Mixture of Block Attention）は、KV をブロックに分け、MoE の router のように関係するブロックだけを選ぶ。NSA と MoBA は、選び方をハードウェアに合わせることと、学習の段階で疎さを焼き込むことを同時に狙った。

[DSA](https://arxiv.org/abs/2512.02556)（DeepSeek Sparse Attention）は、2025 年 9 月の DeepSeek-V3.2-Exp で導入され、12 月に公開した DeepSeek-V3.2 にも採用された。DSA は、MLA の上に indexer を載せる。indexer は、各トークンの重要度を小さな Key で安く見積もる部品で、Query ごとに上位 $k$ 個のトークンを選ぶ。本体の Attention は、選ばれた $k$ 個の KV だけを読む。NSA と MoBA が最初から疎に学習するのに対し、DSA は既存のモデルに継続学習で indexer を足した。indexer 自身は全トークンの小さな Key を毎回走査するので、indexer の読み出しと計算は $L$ に比例したまま残る。GLM-5 は DSA を採用し、GLM-5.2 は indexer を疎な層 4 層ごとに共有して、1M 文脈での 1 トークンあたりの計算の回数を約 2.9 分の 1 にした。DSA には代償がもう 1 つある。どのトークンが後で選ばれるかは事前に分からないので、DSA は全履歴の KV を取り出せる形で持ち続ける。H2O のように捨てれば保存は減るが、捨てた KV は取り戻せない。DSA は保存を残す側を選び、読む量と計算だけを減らした。

## 2026 年に 2 つの流れが合流した

2026 年、DeepSeek は 2 つの流れを 1 つの設計にまとめた。V4 で系列を「まとめる」と「選ぶ」を組み合わせ、V4.1-Flash では 4 つの軸すべてを同時に削った。

[DeepSeek-V4](https://arxiv.org/abs/2606.19348)（2026 年 6 月）には、大きな V4-Pro と小さな V4-Flash の 2 つの構成がある。V4 は、複数のトークンを 1 つのエントリに圧縮してから疎に選ぶ CSA（Compressed Sparse Attention）と、さらに強く圧縮する HCA（Heavily Compressed Attention）を層ごとに使い分ける。圧縮率は、軽い圧縮の 4 倍と、強い圧縮の 128 倍を使い分ける。トークンをまとめる操作は NSA の粗い要約の経路にすでにあったが、V4 はトークンをまとめる操作をキャッシュの持ち方そのものにした。

[DeepSeek-V4.1-Flash](https://www.alphaxiv.org/abs/2609.deepseek-v4-1-flash)（2026 年 9 月）の CSA2 は、全履歴を参照する global な層と、直近だけを読む窓の層からなる。CSA2 の global な層は、4 つの軸を次のように削る。

| 軸 | CSA2 の削り方 |
|---|---|
| エントリの次元 | MLA 系の圧縮 |
| 系列 | 複数トークンの圧縮と、粗く選んでから細かく選ぶ階層的な indexer |
| 層 | KV だけでなく、上位 $k$ 個を選んだ結果まで層をまたいで使い回す（YOCO を着想元として明示している） |
| 精度 | 4 ビットの値に 32 要素ごとの共有のスケールを付ける MXFP4 |

その結果、HBM に常に置く global な KV は 1 トークンあたり 890 バイトになり、V4-Flash の約 4 分の 1 になった。

### 置き場所も設計の対象になった

DSA のように選んで読む方式は、保存する KV を減らせない。2026 年にはこの課題に対し、KV を HBM の外に出す、あるいは捨てて作り直すという、置き場所の工夫が現れた。V4.1-Flash は、後で再利用するために SSD やホストのメモリへ書き出す KV（永続的な KV）から、窓の層の KV を外した。V4.1-Flash は、再利用するときに直近の窓だけを計算し直し、永続的な KV を V4-Flash の約 8 分の 1 にした。[GLM-5.3 の Hybrid HiSparse](https://littlemex.github.io/explainers/vllm-blog-02-glm53-hybrid-hisparse.html) は、疎な層が上位 $k$ 個のトークンしか読まない性質を使い、メモリが足りないときにはページ単位で KV を HBM から外へ出す。HBM の外に出した KV が選ばれれば転送を待つので、Hybrid HiSparse が増やすのは載せられるリクエストの数で、decode が速くなるかは転送の隠し方による。V4.1-Flash と Hybrid HiSparse の工夫は、DSA が残した容量の課題に、置き場所の側から答えた。

選び方そのものを推論時に作り直す研究も続いている。sink に重みを吸われて答えのブロックを取りこぼす問題を直す [CRISP](https://littlemex.github.io/explainers/paper-crisp.html) や、思考の途中でどこを読むかをモデル自身に宣言させる [Declarative Attention](https://littlemex.github.io/explainers/paper-declarative-attention.html) がその例である。

## 品質は何で守られているか

持ち方と読む範囲のどちらの流れでも、品質を守っているのは定理ではなく観測と実験である。「KV を低ランクに圧縮しても品質がほぼ落ちない」は DeepSeek-V2 の実験に、「注意の重みは少数のトークンに集中する」は H2O や Quest の観測に支えられている。窓の層と full Attention の層の比率（3 対 1、5 対 1）や、V4 の圧縮率（4 倍と 128 倍）も、実験で良かった設定である。逆に、何ができないかには理論がある。[Haris と Onak](https://arxiv.org/abs/2502.15955)（2025 年 2 月）は、Attention の出力を正確に保ったまま、KV を系列の長さより小さい空間に圧縮できないことを示した。KV を大きく縮める方式は、どれも出力の近似を受け入れている。品質を保証する定理が無いので、各社の答えは分かれる。同じ問題に対して、DeepSeek は圧縮と疎な選択を、Step と Gemma は窓のハイブリッドを、Qwen と Kimi は次の章の線形のハイブリッドを選んだ。

この 2 つの流れでは消えないものが 2 つある。full Attention の層が残る限り、その層の KV はトークン数に比例して伸びる。選んで読んでも、DSA のように全履歴の KV を持ち続ける方式は容量の問題を残す。そこで「過去をすべて覚えておく」前提そのものを外し、固定の大きさの状態に圧縮して覚える方向が並行して進んだ。次の章で見るように、固定の状態の方向も最後は少数の full Attention の層を残すハイブリッドに落ち着くが、KV を持つ層の数を数分の 1 にできる。

## まとめ

Attention の変種の多くは、KV-Cache が重すぎるという 1 つの問題に、持ち方を軽くする方向と、読む範囲を選ぶ方向から答えてきた。前者は層、エントリの次元、精度の軸を、後者は系列の軸を削り、2026 年の CSA2 で 4 つの軸を同時に削る設計として合流した。

## 参考

| 資料 | 時期 | この章での役割 |
|---|---|---|
| [AI and Memory Wall](https://arxiv.org/abs/2403.14123) | 2024 年 3 月 | 計算と帯域の伸びの差 |
| [MQA](https://arxiv.org/abs/1911.02150) | 2019 年 11 月 | KV を全ヘッドで共有 |
| [GQA](https://arxiv.org/abs/2305.13245) | 2023 年 5 月 | KV をグループで共有 |
| [DeepSeek-V2 (MLA)](https://arxiv.org/abs/2405.04434) | 2024 年 5 月 | KV の低ランク圧縮 |
| [CLA](https://arxiv.org/abs/2405.12981) / [YOCO](https://arxiv.org/abs/2405.05254) | 2024 年 5 月 | 層をまたいだ KV の共有 |
| [KIVI](https://arxiv.org/abs/2402.02750) | 2024 年 2 月 | KV の 2 ビット量子化 |
| [Longformer](https://arxiv.org/abs/2004.05150) / [Mistral 7B](https://arxiv.org/abs/2310.06825) | 2020 年 4 月 / 2023 年 10 月 | 窓で読む SWA |
| [StreamingLLM](https://arxiv.org/abs/2309.17453) | 2023 年 9 月 | attention sink と窓の後付け |
| [gpt-oss](https://arxiv.org/abs/2508.10925) / [Gated Attention](https://arxiv.org/abs/2505.06708) | 2025 年 8 月 / 5 月 | sink を設計に入れる、sink を抑える |
| [H2O](https://arxiv.org/abs/2306.14048) / [SnapKV](https://arxiv.org/abs/2404.14469) / [Quest](https://arxiv.org/abs/2406.10774) | 2023 年 6 月〜2024 年 6 月 | 推論時に捨てる、選んで読む |
| [NSA](https://arxiv.org/abs/2502.11089) / [MoBA](https://arxiv.org/abs/2502.13189) | 2025 年 2 月 | 学習時からの疎な選択 |
| [DSA (DeepSeek-V3.2)](https://arxiv.org/abs/2512.02556) | 2025 年 9 月（V3.2-Exp）/ 12 月（V3.2） | indexer による上位 $k$ の選択 |
| [DeepSeek-V4](https://arxiv.org/abs/2606.19348) | 2026 年 6 月 | CSA と HCA |
| [DeepSeek-V4.1-Flash](https://www.alphaxiv.org/abs/2609.deepseek-v4-1-flash) | 2026 年 9 月 | CSA2、4 軸の同時の削減 |
| [Compression Barriers](https://arxiv.org/abs/2502.15955) | 2025 年 2 月 | KV の圧縮の下界 |
| [Leviathan ら](https://arxiv.org/abs/2211.17192) / [Chen ら](https://arxiv.org/abs/2302.01318) | 2022 年 11 月 / 2023 年 2 月 | 投機的デコードの分布の保存 |
| [Attention の系統と進化](https://littlemex.github.io/explainers/attention-series-index.html) | 筆者の解説 | 各変種の式と動き |
