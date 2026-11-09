---
title: "Attention の潮流 (2): 固定の状態で覚える系統と、ハイブリッドへの収束"
free: false
---

## はじめに

[前の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/llm2026-overview) の 2 つの流れでは、full Attention（過去の全トークンを読む普通の softmax Attention）の層を残す限り、その層の KV は文脈の長さに比例して伸び続ける。この章は、**過去を固定の大きさの状態に圧縮して覚える系統が、更新式を改良しながらも、最後は少数の full Attention を残すハイブリッドに収束した流れ**を扱う。線形 Attention、Mamba、DeltaNet、KDA は名前も出自も違うが、どれも同じ形の更新式の変奏として読める。この章では、この系統の層を線形層と呼ぶ（行列を 1 回掛けるだけの普通の線形変換の層とは別物である）。

この系統の流れは、次の 4 つの問いへの答えとしてまとめられる。

| 問い | 何が起きたか | 節 |
|---|---|---|
| 固定の状態に、どう賢く書き込むか | 足すだけ、忘れる、誤差で直す、状態を豊かにする、の 4 つの方向で更新式を改良した | [更新の 4 段](#状態の更新を賢くしてきた-4-段) |
| ばらばらに見える改良を、1 つの見方で並べられるか | 推論中に学び直す連想記憶として統一する見方が、2024〜2025 年に整った | [連想記憶という見方](#推論中に学び直す連想記憶という見方) |
| 式の上で速いものを、実際に速くできるか | 計算量が少ないのに短い系列で遅い問題に、行列積への書き換えとカーネルの融合で答えてきた | [効率と速さの違い](#式の上の効率と実際の速さの違い) |
| 線形層だけで足りるか | 情報を正確に引く課題で足りない場面があり、少数の full Attention を残すハイブリッドが主流になった | [ハイブリッドへの収束](#線形層だけで足りない場面とハイブリッドへの収束) |

最後に、2026 年後半の論点である「どこに full Attention を残すか」と「推論の基盤をどう追いつかせるか」を見る。読者には前の章の内容と、行列の積と勾配降下の基礎を想定する。

## 状態の更新を賢くしてきた 4 段

固定の大きさの状態に、古い記憶をどう残し、新しい組をどう書き込むかが、この系統の中心の問いだった。答えは、足すだけ、忘れる、誤差で直す、状態を豊かにする、の 4 段で進み、混線を減らしたうえで、縮めるだけだった記憶に反転や回転も持たせた。4 段は発表順ではなく、考え方の順に並べている。

例えるなら、full Attention は会議の議事録を全部保管して毎回読み返す係であり、この系統は大きさの決まったホワイトボードに要点を書き足していく係である。ホワイトボードの係の違いは、何を書き足すか、古い記述をどう消すか、どう書き直すかの選び方にある。

状態を $S_t$（Key の次元 $d_k$ × Value の次元 $d_v$ の行列）とすると、4 段の多くは次の基本形で書ける。

$$
S_t = A_t \, S_{t-1} + \beta_t \, k_t v_t^\top, \qquad o_t = S_t^\top q_t
$$

この式は、前の状態に $A_t$ を掛けて古い記憶を加工し、今のトークンの Key と Value の組を強さ $\beta_t$ で書き足し、Query で読み出すことを言っている。KV-Cache のように $L$ 個のエントリを持つ代わりに、状態は $L$ によらず一定の大きさなので、decode で読むバイトも一定になる。実際のモデルには、読み出しの正規化、入力から出力への直接の経路、複数の組を一度に書く形などが加わるが、何を変えてきたかの骨格は基本形で追える。

![状態の更新の改良を 4 段の階段で描いた図。足すだけ、忘れる、誤差で直す、状態を豊かにする、の順に、更新の行列 A_t の形と代表の手法を並べる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/llm2026-elements/update-ladder.png)

### 足すだけ: 線形 Attention

最初の形は、状態に Key と Value の組を足すだけの更新である。[Transformers are RNNs](https://arxiv.org/abs/2006.16236)（2020 年）は、softmax の代わりに、Query と Key を別の空間に写す関数（特徴写像）$\phi$ の内積を類似度に使った。softmax が無くなると、行列の積の順序を入れ替えられる。

$$
\sum_{s \le t} \left( \phi(q_t)^\top \phi(k_s) \right) v_s^\top = \phi(q_t)^\top \underbrace{\sum_{s \le t} \phi(k_s) v_s^\top}_{S_t}
$$

この式は、過去の全トークンとの類似度を毎回計算する代わりに、過去の組を足し合わせた $S_t$ を 1 つ持てば同じ出力が得られることを言っている。$S_t$ は 1 トークンごとに $\phi(k_t) v_t^\top$ を足すだけで更新できるので、Attention は $A_t = I$ の RNN になる。以下では $\phi(k)$ を単に $k$ と書く。論文の形では、正規化のための累積の和 $z_t = \sum_{s \le t} \phi(k_s)$ も一緒に持つが、後の DeltaNet 系はこの分母を落とし、別の正規化を使う形が主流になった。計算は $O(L)$、状態は一定の大きさになった。

足すだけの更新には容量の限界がある。Key を長さ 1 にそろえ、$N$ 個の組を足した状態からキー $k_j$ で読み出すと、次のようになる。

$$
S^\top k_j = v_j + \sum_{i \ne j} (k_i^\top k_j) \, v_i
$$

この式は、読み出しに欲しい $v_j$ のほかに、他の組からの混線が混ざることを言っている。混線を確実に消すには Key が互いに直交する必要があり、$d_k$ 次元の空間で直交できるのは高々 $d_k$ 本である。だから系列が $d_k$ を大きく超えて伸びると、一般には混線を避けにくくなる。全履歴の KV を持つ full Attention には、この種の圧縮による情報の損失が無い。

### 忘れる: ゲートと選択

混線を減らす 1 つ目の方向は、古い記憶を減衰させて忘れることである。[RetNet](https://arxiv.org/abs/2307.08621)（2023 年）は、全トークンに同じ固定の減衰 $A_t = \gamma I$ を掛けた。[GLA](https://arxiv.org/abs/2312.06635)（Gated Linear Attention、2023 年）は、減衰をトークンごとに入力から決めるゲートにした。

制御理論から来た状態空間モデル（SSM）は、別の入口から同じ所に着いた。SSM は、状態を減衰させながら入力を書き込む漸化式を、連続時間の式から離散化して作る。[S4](https://arxiv.org/abs/2111.00396)（2021 年）は、減衰と書き込みの係数が入力によらず一定の形（時不変）だったので、何を覚えて何を捨てるかを内容で変えられなかった。長い畳み込みの [Hyena](https://arxiv.org/abs/2302.10866)（2023 年）は入力に依存するゲートを足したが、畳み込みのフィルタ自体は入力によらず固定だった。[Mamba](https://arxiv.org/abs/2312.00752)（2023 年）は、減衰と書き込みをトークンごとに入力から決める「選択」を入れ、この限界を越えた。Mamba の書き込みの係数 $B_t$ は基本形の $k_t$ に、読み出しの係数 $C_t$ は $q_t$ に、入力 $x_t$ は $v_t$ にあたる。ただし Mamba の減衰は入力のチャネルごとにも違うので、正確には状態の全列に同じ $A_t$ を掛ける形ではなく、状態の要素ごとに違う減衰を掛ける形になる（図では簡略化して忘れる段に含めた）。

[Mamba-2](https://arxiv.org/abs/2405.21060)（2024 年）は、減衰をスカラー × 単位行列に絞った。Mamba-2 は、この形が、Attention の $L \times L$ の重みの行列に、トークンの間の減衰の積を要素ごとに掛けた線形 Attention と、数学的に同じであることを示した（State Space Duality、SSD）。

### 誤差で直す: delta 則

忘れるだけでは、似た Key が繰り返し来たときに、古い Value の上に新しい Value が足し込まれる。2 つ目の方向は「今の状態が出す予測と、新しい Value との差だけを書き込む」ことで、[Schlag ら](https://arxiv.org/abs/2102.11174)（2021 年）が持ち込んだ delta 則がそれにあたる。delta 則の発表は減衰の系統より早いが、大きなモデルに載ったのは後である。delta 則の更新は次の式で書ける。

$$
S_t = \left( I - \beta_t k_t k_t^\top \right) S_{t-1} + \beta_t k_t v_t^\top
$$

この式は、Key の方向にある古い記憶を消してから、新しい組を書き込むことを言っている。delta 則の更新は、二乗誤差 $\frac{1}{2} \| S^\top k_t - v_t \|^2$ を状態について 1 歩だけ勾配降下したものに一致する。

delta 則は並列に学習しにくかった。$A_t = I$ なら状態は単なる累積の和なので一度に計算できるが、delta 則では毎回違う行列が左から掛かり、行列の積が系列の長さだけ連なるからである。[Yang ら](https://arxiv.org/abs/2406.06484)（2024 年 6 月）は、系列を区切ったチャンクの中の計算を行列演算に展開して GPU で並列化し、DeltaNet を大きなモデルに載るようにした（チャンクの考え方は後の効率の節で見る）。[Gated DeltaNet](https://arxiv.org/abs/2412.06464)（2024 年 12 月）は、Mamba-2 流の忘却のゲート $g_t$ を掛けて「忘れる」と「誤差で直す」を合わせた。[RWKV-7](https://arxiv.org/abs/2503.14456)（2025 年 3 月）は、ベクトルのゲートを持つ一般化した delta 則を出した。Kimi の [KDA](https://arxiv.org/abs/2510.26692)（Kimi Delta Attention、2025 年 10 月）は、ゲートをスカラーからチャネルごとに独立な対角行列にして、記憶の成分ごとに忘れる速さを変えられるようにした。KDA は、対角のゲートを入れても、チャンク単位の行列演算に展開できる形（対角に低ランクを足した行列の特殊な形）を保っている。

### 状態を豊かにする: 負の固有値、行列の積、複素数

ここまでの更新は、記憶を縮めることはできても、反転させたり回したりはできない。そのため、偶数か奇数かの数え上げ（parity）のような、状態を追い続ける課題に限界があり、$A_t$ に負の固有値や回転を許す方向に進んだ。長さ 1 の Key では、$I - \beta_t k_t k_t^\top$ の Key の方向の固有値は $1 - \beta_t$ である。正の減衰を使う Mamba 系や、$\beta_t$ を 0〜1 に取る標準の DeltaNet では、固有値は 0 から 1 の間に収まる。parity は、1 が来るたびに符号を反転させれば表せるが、0〜1 の倍率では反転を表せない。

この限界には理論の裏付けがある。[Merrill ら](https://arxiv.org/abs/2404.08819)（2024 年 4 月）と [Sarrof ら](https://arxiv.org/abs/2405.17394)（2024 年 5 月）は、固有値が非負の SSM が parity のような状態追跡を解けないことを、形式言語の理論で示した。[Grazzi ら](https://arxiv.org/abs/2411.12537)（2024 年 11 月）は、delta 則の $\beta_t$ の範囲を 2 まで広げて Key の方向の固有値を負にできるようにするだけで、parity が解けるようになることを示した。[DeltaProduct](https://arxiv.org/abs/2502.10297)（2025 年 2 月）は、1 トークンあたり delta 則の更新を複数回重ね、$I - \beta k k^\top$ の形の行列の積で $A_t$ をより一般的な行列にした（$\beta = 2$ のとき、この形の行列は 1 つの方向の反射を表す Householder 行列になる）。負の固有値を許した線形層は、状態追跡では full Attention より強くなりうる。

[Mamba-3](https://arxiv.org/abs/2603.15569)（2026 年 3 月）は、SSM の側からこの流れに 3 つの改良を同時に入れた。1 つ目に、台形則による離散化で漸化式をより正確にした。2 つ目に、状態を複素数にして回転を表せるようにした。3 つ目に、入力と出力を複数にする MIMO（Multi-Input Multi-Output）の形で、1 トークンに複数の組を書き込むようにした。1.5B の規模で、Mamba-3 の下流の課題の平均の精度は Gated DeltaNet より 0.6 ポイント上がり、MIMO 版はさらに 1.2 ポイント上がった。Mamba-3 は、Mamba-2 の半分の状態の大きさで、同じ程度の perplexity（次のトークンの予測の難しさを表す指標で、低いほど良い）に届いた。Mamba-3 の改良は、状態を大きくする代わりに、更新の表現力で記憶の効率を上げた例である。

## 推論中に学び直す連想記憶という見方

4 段の改良は、推論中に Key から Value を返す対応表を学び直し、Query で引く連想記憶という 1 つの見方で並べられる。この見方は 2021 年の Schlag らに源流を持ち、2025 年に Test-time regression と Miras が、線形 Attention、SSM、softmax Attention を同じ枠組みで導く体系にまとめた。

例えるなら、線形層は、読んだそばから要点を自分のノートに書き写して覚え直す学生である。full Attention は、ノートを取らずに教科書を全部持ち歩き、聞かれるたびに該当ページを探す学生である。

Schlag らは、線形 Transformer を「推論中に重みを書き換えるネットワーク」とみなした。2024 年には、状態を推論中に学習するモデルそのものとみなす [TTT](https://arxiv.org/abs/2407.04620) や、記憶を小さなネットワークにした [Titans](https://arxiv.org/abs/2501.00663) が出た。[Test-time regression](https://arxiv.org/abs/2501.12352)（Wang・Shi・Fox、2025 年 1 月）は、線形 Attention、SSM、softmax Attention を、過去の組への回帰の解き方の違いとして導いた。Test-time regression の見方では、full Attention は、過去の組を全部持ち、Query に近い Key の Value を重み付きで平均する、いわば覚え直さない回帰である。線形層は、過去の組を固定の大きさの状態に回帰でまとめる。Google の [Miras](https://arxiv.org/abs/2504.13173)（It's All Connected、2025 年 4 月）は、同じ対象を、記憶の構造、何を目的に覚えるか、何を忘れるか、どう最適化するかの 4 つの選択で整理した。

| Miras の選択 | 4 段のどこにあたるか |
|---|---|
| 何を目的に覚えるか | 足すだけは内積を大きくする目的、誤差で直すは二乗誤差を小さくする目的 |
| 何を忘れるか | 忘れる（減衰とゲート） |
| どう最適化するか | 1 歩の勾配降下（delta 則）、複数歩（DeltaProduct） |
| 記憶の構造 | 行列の状態、複素数の状態（Mamba-3）、小さなネットワーク（Titans） |

Albert Gu は [ブログ](https://goombalab.github.io/blog/2025/tradeoffs/) で、SSM の成功の要素を、状態の大きさ、状態の更新の表現力、学習の効率（training efficiency）の 3 つにまとめた。ここまでの 4 段は 2 つ目の改良にあたる。次の節は、3 つ目の要素を、学習と推論の両方の実行の効率に広げて見る。

## 式の上の効率と実際の速さの違い

この系統は計算量が少ないのに、短い系列では Attention より遅いことが多い。原因は行列積の専用の演算器を使いにくい計算と小さなカーネルの多さにあり、各社は計算を行列積に書き換え、カーネルを融合し、演算器ごとに形を作り替えることで答えてきた。

例えるなら、短い距離の移動では、速い新幹線より、乗り換えの少ない普通の電車のほうが早く着く。計算の量が少なくても、乗り換えにあたる小さな処理の起動とメモリの往復が多ければ、時間はそちらで決まる。

FLOPs（浮動小数点演算の回数）の削減が実行時間の短縮に結びつかない理由は、大きく 3 つある。1 つ目に、短い系列では Attention の $L^2$ の項が全体に占める割合が小さく、削っても効果が限られる。2 つ目に、Mamba の selective scan（状態を入力ごとに更新していく計算）のような要素ごとの演算が中心の計算は、行列積の専用の演算器（NVIDIA の Tensor Core）を活かしにくい。3 つ目に、Mamba が入力に掛ける短い畳み込み、正規化、ゲートのような小さなカーネル（GPU で 1 回に起動する計算の単位。カーネル法のカーネルとは別）が多く、起動とメモリの往復の手間が積み上がる。

![左に Attention、右に状態の系統を置き、短い系列で時間が何に使われるかを比べた図。状態の系統では、Tensor Core を使える行列積は一部で、スキャン、畳み込み、正規化とゲートが残る。下に 4 つの答えを並べる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/llm2026-elements/flops-vs-time.png)

短い系列で遅いことは、実測でも確かめられている。[Mitra ら](https://arxiv.org/abs/2507.12442)（2025 年 7 月）は、家庭用と組み込み向けの GPU で推論を比べ、8K トークン未満では Transformer が最大 1.9 倍速く、約 57K トークンでは逆に SSM が最大 4 倍速くなることを示した。組み込み向けの GPU では、selective scan のカーネルが推論時間の 55% 以上を占め、Mitra らは原因を、処理が逐次的で要素ごとの演算であることに求めた。学習時の GPU での振る舞いは [Baruah ら](https://arxiv.org/abs/2508.17679)（2025 年 8 月）が分析している。

### 行列積に書き換える

最初の答えは、計算を Tensor Core が使える行列積に書き換えることだった。元の Mamba は、並列のスキャン（足し算のように順序をまとめ直せる演算を、木の形に分けて並列に計算する方法）を使った。Mamba はスキャンを GPU の内部の速いメモリの上で 1 つのカーネルにまとめ、状態を HBM に書き出さないことで速さを得た。ただし並列のスキャンも要素ごとの演算なので、Tensor Core には載らない。Mamba-2 は、系列をチャンクに区切り、チャンクの中は行列積で一度に計算し、チャンクの間だけ状態を渡す形に変えた。GLA や DeltaNet の並列化も同じ形をとる。行列積に書き換えても、累積の和や指数の計算のような要素ごとの演算は残る。

### 小さな演算を消し、まとめる

次の答えは、残った小さな演算そのものを減らし、カーネルを 1 つにまとめることだった。Mamba-3 では、台形則による離散化が漸化式の中で畳み込みに似た働きをするので、外付けの短い畳み込みに頼らない構成を取れる。Mamba-3 の著者は、prefill の経路を Triton で、decode の経路を CuTe DSL で書き、カーネルを融合した（Triton と CuTe DSL は、GPU のカーネルを Python に近い書き方で書くための言語である）。MIMO は読む 1 バイトあたりの計算（前の章の演算強度）を上げるので、decode の FLOPs は増えても時間はほとんど延びない。

部品を減らす研究もある。[2Mamba2Furious](https://arxiv.org/abs/2602.17363)（2026 年 2 月）は、Mamba-2 の部品を 1 つずつ外して精度への寄与を調べ、寄与の小さい部品を外した簡略版（Mamba-2S）を作った。外した部品には、SiLU と呼ぶ活性化関数や、入力をそのまま出力に足す D の項がある。2Mamba2Furious の目的は精度で、速さは測っていない。学習の逆伝播でも、[ReWY](https://arxiv.org/abs/2611.01940)（2026 年 11 月）が、delta 則の逆伝播に現れるチャンク内の行列積を 7 回から 3 回に減らした。ReWY は、対象の演算の逆伝播を H100 と H200 で 1.18〜1.70 倍速くした（学習の 1 ステップ全体の倍率ではない）。

### GPU 以外の演算器に合わせる

演算器の形が変わると、同じ書き換えでは足りない。GPU 向けに調整された Gated DeltaNet や KDA を TPU にそのまま移すと、256 × 256 の行列演算器が半分空いたままになり、遅いベクトル演算器が律速になる。[Systolic Linear Attention](https://arxiv.org/abs/2611.06828)（2026 年 11 月）は、モデルの側も変えた。Systolic Linear Attention は、Key の幅を 256 に広げ、Value のヘッドを共有し、減衰のゲートを 64 チャネルの塊にまとめ、チャンク内の計算をすべて行列積にした 1 つのカーネルにした。チャンク内の逆行列を近似で解く構成（SLA-R4）では、層全体で、公開の KDA のカーネルより TPU v7x で 1.6〜2.5 倍速く、逆行列を厳密に解く構成では 1.4〜2.4 倍速かった。主な効き目は、ヘッドの共有とカーネルの融合だった。

NPU（AI 専用のチップ）では、チャンク内に出てくる三角行列の逆行列が問題になる。三角行列の逆行列を前進代入で解く従来の実装は、逐次の依存が大きく、NPU の演算器を使い切れない。[Zhang ら](https://arxiv.org/abs/2606.06034)（2026 年 6 月）は、三角行列の逆行列の計算を行列積だけの近似に置き換え、カーネル単体で最大 5 倍速くした。Systolic Linear Attention と Zhang らは、どちらも計算を演算器の得意な形に作り替えるという同じ考え方で、前者はモデルの形まで変えている。

### decode では状態の読み書きが新しい壁になる

decode では別の壁が現れた。状態は一定の大きさだが、Key と Value の次元の積なので小さくはなく、毎ステップ読み書きする。[ReplaySSM](https://arxiv.org/abs/2610.13778)（2026 年 10 月）は、状態を毎回書く代わりに、ときどき取る保存点と直近の入力を持ち、状態の読み書きを約半分にした。推論サーバーの vLLM の上で、Mamba-2 と Gated DeltaNet を使うモデルは、バッチ 256 のとき 1.20〜1.47 倍速くなった。[SPARE](https://arxiv.org/abs/2611.07552) は状態を低い次元に圧縮したまま読み書きし、[PACE](https://arxiv.org/abs/2610.29545) は状態を 4 ビットで持つ。前の章で KV に対して起きた「持ち方を軽くする」流れが、状態に対しても始まっている。

## 線形層だけで足りない場面とハイブリッドへの収束

忘却や delta 則で混線は減ったが、固定の大きさに収まらない情報を正確に引く課題では、線形層だけのモデルは full Attention に負けやすい。そこで各社は、大部分の層を線形層にし、少数の層に full Attention を残すハイブリッドを、品質と効率を両立する有力な路線として選んだ。

例えるなら、ハイブリッドは、ふだんはホワイトボードの要点で仕事を進め、ときどき書庫の原本を引きに行く事務所である。要点だけでは引けない細部を、少数の原本係が受け持つ。

線形層の弱点には、観測と理論の両方がある。Hazy Research の [Zoology](https://hazyresearch.stanford.edu/blog/2023-12-11-zoology1-analysis)（2023 年）は、効率の良い系列モデルと Attention の品質の差の大部分が、文脈の中の連想の想起（associative recall）で説明できることを示した。[Repeat After Me](https://arxiv.org/abs/2402.01032)（2024 年 2 月）は、固定の大きさの状態では長い系列をそのまま写すことが原理的にできないことを示した。一方で前の節のとおり、負の固有値を許した線形層は状態追跡で full Attention より強くなりうる。情報を正確に引く役は full Attention が、状態を追う役は線形層が受け持つという分業が、ハイブリッドの根拠になる。

![実際のモデルの層の並びを、線形層と full Attention の層の色分けで、公開の順に並べた図。Jamba と MiniMax-01 は 7 対 1、Qwen3-Next と Kimi Linear と Qwen3.5 と Kimi K3 は 3 対 1、MiniMax M2 はすべて full Attention](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/llm2026-elements/hybrid-stacks.png)

| モデル | 時期 | 線形層 | Attention 側の方式 | 比率（線形 : Attention） |
|---|---|---|---|---|
| [Jamba](https://arxiv.org/abs/2403.19887) | 2024 年 3 月 | Mamba | full Attention | 7 対 1 |
| [MiniMax-01](https://arxiv.org/abs/2501.08313) | 2025 年 1 月 | Lightning Attention（MiniMax の線形 Attention） | full Attention | 7 対 1 |
| [Nemotron-H](https://arxiv.org/abs/2504.03624) | 2025 年 4 月 | Mamba-2 | full Attention（GQA） | 大部分を Mamba-2 に置き換え |
| [Qwen3-Next](https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct) | 2025 年 9 月 | Gated DeltaNet | Gated Attention | 3 対 1 |
| [Kimi Linear](https://arxiv.org/abs/2510.26692) | 2025 年 10 月 | KDA | MLA | 3 対 1 |
| [Qwen3.5](https://huggingface.co/Qwen/Qwen3.5-397B-A17B/blob/main/config.json) | 2026 年 2 月 | Gated DeltaNet | Gated Attention | 3 対 1 |
| Kimi K3 | 2026 年 7 月 | KDA | Gated MLA | 3 対 1、最後にもう 1 層 |

AI21 の Jamba は、Mamba の層を主体に full Attention の層を一定の間隔で挟んだ、早い時期の大きな規模の例である。MiniMax-01 は線形 Attention の側で 4560 億パラメータまで載せ、2025 年 6 月の [MiniMax-M1](https://arxiv.org/abs/2506.13585) も同じハイブリッドを続けた。Kimi Linear は、同じ学習の手順で比べて、全層を MLA にしたモデルをすべての課題で上回った。Kimi Linear は、KV-Cache を最大 75% 減らし、1M 文脈の decode のスループットを最大 6 倍にしたと報告した。比率の実験では、3 対 1 の検証の perplexity が最も低く、7 対 1 は学習の損失は同じでも検証で少し悪化し、15 対 1 ではさらに悪化した。Kimi Linear の論文は 3 対 1 を、品質とスループットの兼ね合いが最も良い比率と位置づけている。

Kimi Linear の MLA の層は RoPE を外している（位置の符号化を持たないので NoPE と呼ぶ）。位置の情報は KDA の減衰がすでに運んでいるので、full Attention の層は位置によらずに引く役に専念できる、という説明である。2.8T パラメータの MoE の Kimi K3 は、3 対 1 と RoPE を外す設計を Kimi Linear から引き継ぎ、Attention 側を出力にゲートを付けた Gated MLA にして、最後にもう 1 層 Gated MLA を足した（[筆者の解説](https://littlemex.github.io/explainers/paper-kimi-k3.html)）。

full Attention ではなく窓の Attention を残す系統もある。Google の [Griffin](https://arxiv.org/abs/2402.19427)（2024 年 2 月）は線形の再帰と局所の Attention を、Microsoft の [Samba](https://arxiv.org/abs/2406.07522)（2024 年 6 月）は Mamba と SWA を組み合わせた。Griffin と Samba の構成では、どの層の KV も文脈の長さに依存しない。代わりに、遠くの情報を正確に引く役を持つ層が無くなる。

ハイブリッドに至る経路も 2 つある。Qwen、Kimi、MiniMax は、ハイブリッドを最初から学習した。一方、学習済みの full Attention のモデルを蒸留で線形化する経路もある。[Mamba in the Llama](https://arxiv.org/abs/2408.15237)（2024 年 8 月）、[MOHAWK](https://arxiv.org/abs/2408.10189)（2024 年 8 月）、[LoLCATs](https://arxiv.org/abs/2410.10254)（2024 年 10 月）は、既存のモデルの Attention 層を線形層に置き換えて学び直させた。NVIDIA の [Jet-Nemotron](https://arxiv.org/abs/2508.15884)（2025 年 8 月）は、学習済みのモデルから出発し、どの層の Attention を残すかを探索で決めた。最初から学習する余力が無くても、変換の経路でハイブリッドを作れる。

### 戻った例: MiniMax M2

この収束には目立つ反例がある。MiniMax は 01 と M1 で線形のハイブリッドを続けたが、2025 年 10 月の M2 ですべての層を full Attention に戻した。MiniMax は理由を [ブログ](https://huggingface.co/blog/MiniMax-AI/why-did-m2-end-up-as-a-full-attention-model)（2025 年 10 月）で説明している。結論は「効率の良い Attention には取り組み続けているが、実際の大規模な運用で full Attention に確実に勝つにはまだ距離がある」である。1 つ目の理由として、MiniMax は、手元の評価では差が見えず、agent の用途（モデルが道具を呼びながら多段の作業を進める使い方）や長い思考の連鎖で、品質の問題が後から見つかることを挙げた。2 つ目の理由として、低精度の計算、キャッシュ、投機的デコードのような推論の基盤が追いついていないことを挙げた。式の上で効率が良いことと、運用で勝つことの間には、評価と基盤という 2 つの壁がある。

## 2026 年後半の論点

ハイブリッドが主流になると、問いは「線形層か full Attention か」から「どこに full Attention を残し、推論の基盤をどう追いつかせるか」に移った。

### どこに full Attention を残すか

どこに残すかという問いは、混ぜる粒度、位置の扱い、層の役割の 3 つの方向から研究されている。全体としては、比率を実験で選ぶ段階から、役割を測って配置する段階へ移りつつある。

混ぜる粒度は 3 つある。1 つ目は、Jamba 以来の、層の単位で混ぜる方式である。2 つ目は、[Hymba](https://arxiv.org/abs/2411.13676)（2024 年 11 月）や [Falcon-H1](https://arxiv.org/abs/2507.22448)（2025 年 7 月）の、1 つの層の中で Attention と SSM のヘッドを並べる方式である。3 つ目は、ヘッドの単位で選ぶ方式である。Meta の [Bae ら](https://arxiv.org/abs/2510.04800)（2025 年 10 月）は、層の単位で混ぜる方式と層の中で並べる方式を体系的に比べ、設計の指針を出した。[HydraHead](https://arxiv.org/abs/2606.20097)（2026 年 6 月）は、ヘッドの単位で混ぜ、取り出しに欠かせないヘッドだけを full Attention のまま残す。

位置の扱いも、同じ原則に収束しつつある。[Modern Transformers Are Implicit Hybrids](https://arxiv.org/abs/2609.02986)（2026 年 9 月）は、既存の Transformer のヘッドが、遠くの情報を引くヘッドと位置を扱うヘッドに分かれていることを見つけた。Implicit Hybrids の著者は、位置は近くだけで扱い、遠くの情報は位置によらない取り出しで扱う、という配置の原則を導いた。線形層を使わない Transformer の側では、Cohere の [Command A](https://arxiv.org/abs/2504.00698)（2025 年 3 月）が、RoPE を使う窓の層 3 層に、位置の符号化を持たない full Attention の層 1 層を混ぜた。[Llama 4](https://ai.meta.com/blog/llama-4-multimodal-intelligence/)（2025 年 4 月）も位置の符号化を持たない Attention の層を交互に挟み、[SmolLM3](https://huggingface.co/blog/smollm3)（2025 年 7 月）も一部の層で RoPE を外した。線形のハイブリッドの Kimi Linear と Kimi K3 が full Attention の層で RoPE を外したのも、同じ方向である。

役割の分担そのものも見直されている。[Who Asks, Who Answers](https://arxiv.org/abs/2611.01909)（2026 年 11 月）は Qwen3.5（Gated DeltaNet）と Granite 4.0（Mamba-2）を調べ、線形層が過去を圧縮するだけでなく、softmax の層が使う Query を組み立てていることを示した。線形層が「何を探すか」を決め、full Attention の層が「それを引く」という分担である。

### 推論の基盤の負債を返す

prefix caching や投機的デコードは、好きな位置で切り出せる KV-Cache を前提に作られていて、上書きされていく線形層の状態では、決まった保存点でしか再利用や巻き戻しができなかった。2026 年後半の研究は、直近の入力だけを流し直して任意の位置の状態を作り直す方法で、再利用と巻き戻しの問題に答え始めた。

prefix caching は、共通の先頭部分を持つリクエストの間で、その部分の計算結果を再利用する仕組みである。KV-Cache はトークンごとのエントリなので、一致した先頭部分の分だけ、ブロック単位で切り出して共有しやすい。一方、線形層の状態は、ある位置の状態を保存しておけばその位置からは正確に再開できるが、保存点の間の位置の状態は残っていない。[Marconi](https://arxiv.org/abs/2411.19379)（2024 年 11 月）以来、ハイブリッドの prefix caching は、どの位置で状態の保存点を取るかを工夫してきた。[Tail-Replay](https://arxiv.org/abs/2608.30310) と [SuffixReplay](https://arxiv.org/abs/2609.33477)（2026 年 8〜9 月）は、減衰があるので古い入力の影響は薄れる、という性質を使い、直近の入力だけを流し直して任意の位置の状態を近似する。

投機的デコードでも、検証のたびに、状態を受理した位置まで戻す手間が残る。[HybridSpec](https://arxiv.org/abs/2611.04055) は Qwen3.6-35B-A3B で、検証する長さを 4 から 16 に伸ばすと検証の時間が 71% 増え、増えた時間の大半が Gated DeltaNet と MoE の層であることを測った（MoE でない Qwen3-4B では 4〜6%）。効率の節で見た ReplaySSM は、保存点と直近の入力を持つので、受理した位置の状態を、保存点から受理した入力だけを流し直して作り直せる。どれも、推論の基盤が KV-Cache のために積み上げてきた仕組みを、状態に対して作り直す仕事である。

## まとめ

固定の状態で覚える系統は、足す、忘れる、誤差で直す、状態を豊かにする、と更新式を賢くしながら、計算を行列積に書き換えてハードウェアに合わせてきた。それでも情報を正確に引く課題では足りない場面があり、少数の full Attention を残すハイブリッドが主流になった。2026 年後半の問いは、full Attention をどこに残すかと、KV-Cache のために作られた推論の基盤を状態に合わせて作り直すことに移っている。

## 参考

| 資料 | 時期 | この章での役割 |
|---|---|---|
| [Transformers are RNNs](https://arxiv.org/abs/2006.16236) | 2020 年 6 月 | 線形 Attention |
| [RetNet](https://arxiv.org/abs/2307.08621) / [GLA](https://arxiv.org/abs/2312.06635) | 2023 年 7 月 / 12 月 | 減衰とゲート |
| [S4](https://arxiv.org/abs/2111.00396) / [Hyena](https://arxiv.org/abs/2302.10866) | 2021 年 10 月 / 2023 年 2 月 | 時不変の SSM と長い畳み込み |
| [Mamba](https://arxiv.org/abs/2312.00752) / [Mamba-2](https://arxiv.org/abs/2405.21060) | 2023 年 12 月 / 2024 年 5 月 | 選択的 SSM と SSD |
| [Schlag ら](https://arxiv.org/abs/2102.11174) / [DeltaNet の並列化](https://arxiv.org/abs/2406.06484) / [Gated DeltaNet](https://arxiv.org/abs/2412.06464) | 2021 年 2 月 / 2024 年 6 月 / 12 月 | delta 則とその並列化 |
| [RWKV-7](https://arxiv.org/abs/2503.14456) / [Kimi Linear (KDA)](https://arxiv.org/abs/2510.26692) | 2025 年 3 月 / 10 月 | ベクトルや対角のゲート |
| [Merrill ら](https://arxiv.org/abs/2404.08819) / [Sarrof ら](https://arxiv.org/abs/2405.17394) | 2024 年 4 月 / 5 月 | 非負の固有値と状態追跡の限界 |
| [Grazzi ら](https://arxiv.org/abs/2411.12537) / [DeltaProduct](https://arxiv.org/abs/2502.10297) | 2024 年 11 月 / 2025 年 2 月 | 負の固有値と行列の積による状態追跡 |
| [Mamba-3](https://arxiv.org/abs/2603.15569) | 2026 年 3 月 | 台形則、複素数の状態、MIMO、融合カーネル |
| [TTT](https://arxiv.org/abs/2407.04620) / [Titans](https://arxiv.org/abs/2501.00663) | 2024 年 7 月 / 12 月 | 推論中に学習する記憶 |
| [Test-time regression](https://arxiv.org/abs/2501.12352) / [Miras](https://arxiv.org/abs/2504.13173) | 2025 年 1 月 / 4 月 | 連想記憶としての統一の見方 |
| [On the Tradeoffs of SSMs and Transformers](https://goombalab.github.io/blog/2025/tradeoffs/) | 2025 年 | SSM の 3 つの要素 |
| [Mitra ら](https://arxiv.org/abs/2507.12442) / [Baruah ら](https://arxiv.org/abs/2508.17679) | 2025 年 7 月 / 8 月 | 短い系列で遅い理由の実測 |
| [2Mamba2Furious (Mamba-2S)](https://arxiv.org/abs/2602.17363) | 2026 年 2 月 | 部品の削減 |
| [ReWY](https://arxiv.org/abs/2611.01940) | 2026 年 11 月 | 逆伝播の行列積の削減 |
| [Systolic Linear Attention](https://arxiv.org/abs/2611.06828) / [Zhang ら](https://arxiv.org/abs/2606.06034) | 2026 年 11 月 / 6 月 | TPU と NPU への合わせ方 |
| [ReplaySSM](https://arxiv.org/abs/2610.13778) / [SPARE](https://arxiv.org/abs/2611.07552) / [PACE](https://arxiv.org/abs/2610.29545) | 2026 年 10〜11 月 | decode での状態の読み書き |
| [Zoology](https://hazyresearch.stanford.edu/blog/2023-12-11-zoology1-analysis) / [Repeat After Me](https://arxiv.org/abs/2402.01032) | 2023 年 12 月 / 2024 年 2 月 | 連想の想起と写す課題での限界 |
| [Jamba](https://arxiv.org/abs/2403.19887) / [MiniMax-01](https://arxiv.org/abs/2501.08313) / [MiniMax-M1](https://arxiv.org/abs/2506.13585) / [MiniMax M2 のブログ](https://huggingface.co/blog/MiniMax-AI/why-did-m2-end-up-as-a-full-attention-model) | 2024 年 3 月〜2025 年 10 月 | ハイブリッドの量産と、full Attention への回帰 |
| [Nemotron-H](https://arxiv.org/abs/2504.03624) | 2025 年 4 月 | Mamba-2 のハイブリッド |
| [Griffin](https://arxiv.org/abs/2402.19427) / [Samba](https://arxiv.org/abs/2406.07522) | 2024 年 2 月 / 6 月 | 線形層と窓の Attention のハイブリッド |
| [Mamba in the Llama](https://arxiv.org/abs/2408.15237) / [MOHAWK](https://arxiv.org/abs/2408.10189) / [LoLCATs](https://arxiv.org/abs/2410.10254) / [Jet-Nemotron](https://arxiv.org/abs/2508.15884) | 2024 年 8 月〜2025 年 8 月 | 学習済みのモデルの線形化 |
| [Hymba](https://arxiv.org/abs/2411.13676) / [Falcon-H1](https://arxiv.org/abs/2507.22448) / [Bae ら](https://arxiv.org/abs/2510.04800) / [HydraHead](https://arxiv.org/abs/2606.20097) | 2024 年 11 月〜2026 年 6 月 | 混ぜる粒度 |
| [Implicit Hybrids](https://arxiv.org/abs/2609.02986) / [Command A](https://arxiv.org/abs/2504.00698) / [Llama 4](https://ai.meta.com/blog/llama-4-multimodal-intelligence/) / [SmolLM3](https://huggingface.co/blog/smollm3) | 2025 年 3 月〜2026 年 9 月 | 位置の扱い |
| [Who Asks, Who Answers](https://arxiv.org/abs/2611.01909) | 2026 年 11 月 | 層の役割の分担 |
| [Marconi](https://arxiv.org/abs/2411.19379) / [Tail-Replay](https://arxiv.org/abs/2608.30310) / [SuffixReplay](https://arxiv.org/abs/2609.33477) / [HybridSpec](https://arxiv.org/abs/2611.04055) | 2024 年 11 月〜2026 年 11 月 | prefix caching と投機的デコードの負債 |
| [Kimi K3 の解説](https://littlemex.github.io/explainers/paper-kimi-k3.html) | 筆者の解説 | KDA と Gated MLA の 3 対 1 |
