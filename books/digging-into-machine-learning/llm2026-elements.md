---
title: "[TBD] 2026年LLMの要素技術を重要な順に — MTP・Attention・MoE・低ビット"
free: false
---

:::message
本稿は前章「なぜ2026年のLLMは同じ形に収束したのか」の続編である。前章で示した「1トークンの時間 ≈ (アクティブ重みバイト + KV バイト) ÷ 帯域 ÷ 期待受理長」という背骨の式を前提に、4つの要素技術を重要な順に掘り下げる。全変動距離、棄却サンプリング、低ランク近似、確率的丸めといった概念が登場する。統計検定準1級程度の確率と線形代数の素養があれば読み解ける。
:::

## はじめに

前章では、2026年前半の主要 LLM が MoE、Attention 疎化・圧縮、MTP・投機デコード、低ビット学習の4点に収束したこと、その背後にあるのが自己回帰デコードの帯域律速というハード制約であること、そして品質保証の型が非対称 (投機デコードだけが定理型、残りは学習時共設計型) であることを見た。

本章では、この4つを重要な順に一つずつ掘り下げる。順序の基準は「decode コスト削減のインパクト、定理保証の強さ、潮流の普遍性」の3つを掛け合わせたものである。まず唯一の定理型であり生成の環を閉じる MTP・投機デコードから始め、次に1M文脈で最も激しい主戦場となっている Attention の疎化・圧縮、続いて容量とコストの分離を担う MoE、最後に学習時に制約を焼き込む低ビットネイティブ化を扱う。

前章と同じく根拠レベルを明示する。[定理] は証明された保証、[報告] は一次ソースに原文がある事実、[仮説] は筆者の解釈である。

## 要素1: MTP・投機デコード — 唯一の定理型

### なぜ最初に置くか

4つのトレンドのうち、これだけが「速くしても出力の確率分布を変えない」ことを数学的に保証できる。背骨の式でいえば分母の $\tau$ (期待受理長) を大きくする因子であり、他の3因子が「読むバイト数」を削るのに対し、これは「読み出しの回数」そのものを割り算で減らす。出力分布を保つという保証が推論時の他の技術と衝突しないため、品質を担保したまま高速化を上乗せする「最後に環を閉じる」役割を担う。他の3つと違って品質保証が定理型である点を際立たせるため、あえて最初に解説する。ただし後述の通り、この「トレードオフのなさ」は出力分布に限った話で、スループットや学習側では別のトレードオフが残る。

### 投機デコードの仕組みと分布保存の定理

自己回帰デコードは1トークンずつ順番に生成するため、本質的に逐次的で、1トークンごとに巨大な重みを HBM から読み直す。この逐次性を隠すのが投機デコード (speculative decoding) である。小さく速いドラフトモデルが先に $\gamma$ トークンを一気に提案し、本体モデルがそれらをまとめて1回の順伝播で検証する。当たっていれば複数トークンを一度に確定でき、外れたらそこで打ち切る。

ここで決定的に重要なのが、修正版の棄却サンプリング (modified rejection sampling) である。本体モデルの次トークン分布を $p(x)$、ドラフトの分布を $q(x)$ とする。ドラフトが提案したトークン $x$ を、確率 $\min(1, p(x)/q(x))$ で受理する。棄却された場合は、調整された残差分布 $\mathrm{norm}(\max(0, p(x) - q(x)))$ から改めてサンプリングする。この手続きの結果として得られるトークンの分布が、本体モデル単独の $p(x)$ と厳密に一致することが証明されている ([定理]、arXiv:2211.17192 と arXiv:2302.01318 が同時期に独立に示した)。

これは強い保証である。投機デコードは、同じ本体モデルを前提にする限り「速くしても、出力の確率分布を変えない」。品質 (出力分布) に関するトレードオフが存在しない、稀有な高速化技術である。ただし主張の範囲は正確に区切っておく。厳密な等価は同一 prefix・厳密演算の下での話で、実装上は浮動小数点の非結合性やバッチ依存の数値揺らぎで完全に1ビット一致とはいかない。また保証されるのは「出力分布」であって「スループット」ではなく (この点は後述)、さらに MTP を学習に組み込むと本体の重み自体が変わるため、MTP 込みで学習したモデルは MTP なしのモデルとは別の分布を持つ。定理が守るのはあくまで「与えられた本体モデルを、推論時に速く走らせても分布を変えない」ことに限られる。

### 受理率と期待受理長

どれだけ速くなるかは受理率で決まる。1トークンが受理される確率 $\alpha$ は、本体分布とドラフト分布の全変動距離 (total variation distance) を使って

$$
\alpha = 1 - \mathrm{TV}(p, q), \qquad \mathrm{TV}(p, q) = \frac{1}{2} \sum_x |p(x) - q(x)|
$$

と書ける。ドラフトが本体をよく真似ているほど $\mathrm{TV}$ が小さく、受理率が高い。$\gamma$ トークン先読みしたときの1ステップあたり期待生成トークン数 (1回の検証で平均何トークン確定できるか) は、各トークンが独立に確率 $\alpha$ で受理されると仮定すると

$$
\mathbb{E}[\text{生成長}] = \frac{1 - \alpha^{\gamma+1}}{1 - \alpha}
$$

となる ([定理]、ただし $\alpha$ が位置・文脈によらず一定という近似の下での結果。原論文で定式化)。$\alpha = 0.7$、$\gamma = 4$ なら約2.7トークンである (この量は受理されたドラフト分に、棄却時の再サンプルまたは全受理時の追加1トークンを加えたもので、純粋な期待受理長より1大きい)。前章のウォーターフォールで $\tau \approx 2.7$ と置いたのはこの値に基づく。ここで区別すべきは3つある。分布が厳密に保たれることは無条件の定理、この閉形式は $\alpha$ 一定という近似込みの結果、そして受理率 $\alpha$ が高いこと自体はモデル依存の観測である。定理が保証するのは「受理・棄却の後で分布が保たれること」であって、「ドラフトがよく当たること」ではない。

なお、この $\tau$ で単純に割る前章の近似は、ドラフト (または MTP ヘッド) の追加読み出しコストを無視した上界である。厳密には Leviathan らの改善率 $\frac{1-\alpha^{\gamma+1}}{(1-\alpha)(\gamma c + 1)}$ のように、ドラフトのコスト比 $c$ が分母に入る。MTP ヘッド方式は $c$ が小さいので近似は良いが、独立したドラフトモデルを使う方式では無視できない。さらにこれは帯域律速の領域での話で、大バッチで演算律速に近づくと検証の余剰計算がスループットを食い、利得は縮むか負にもなりうる。

### MTP: 学習側にドラフトを内蔵する

初期の投機デコードは、本体とは別に小さなドラフトモデルを用意する必要があった (先読みして並列に検証する発想自体は、Leviathan らに先行する Stern らの Blockwise Parallel Decoding, arXiv:1811.03115 に遡る)。ここを進化させたのが Multi-Token Prediction (MTP) である。Meta の arXiv:2404.19737 が示したように、学習の段階で本体モデルに複数の予測ヘッドを持たせ、次の1トークンだけでなく数トークン先までを同時に予測させる。こうして訓練されたヘッドは、推論時にそのままドラフタとして使え、別モデルを用意する手間が消える。

ここで力点を誤らないよう補足する。MTP の第一義は投機デコードの部品ではなく、学習信号を密にしてサンプル効率と生成能力そのものを高めることにあった。Gloeckle らの主結果もこの品質向上であり、DeepSeek-V3 も MTP を第一には学習改善として導入している。投機デコードへの転用は強力な副次効果という位置づけである。「ドラフトを内蔵する技術」とだけ見ると系譜の本質を半分落とすことになる。

2026年のモデルはこの MTP を広く取り込んだ。DeepSeek-V4 は config に `num_nextn_predict_layers=1` を持ち MTP を継承し、GLM-5 は MTP を1層備え、5.2では受理長を最大20%改善したと報告する。Step-3.5 は MTP-3 で1回の順伝播から4トークンを並列に予測・検証し、毎秒100から300トークンを出す。Gemma 4 はドラフタ用ヘッドで投機デコードを行う ([報告])。Nemotron 3 の Super と Ultra は共有重みの MTP を2層持つ。

投機デコードから MTP への発展は、ドラフト品質の工夫競争として整理できる。Medusa (arXiv:2401.10774) が複数デコードヘッドとツリー Attention を導入し、EAGLE (arXiv:2401.15077) が特徴量レベルの自己回帰予測で受理率を上げ、その動的なドラフト木へと発展した。MTP はこの流れを事前学習側に内蔵し、実運用の標準装備にした位置づけになる。

### 反例: Kimi は MTP を避け続ける

前章でも触れたが、Moonshot の Kimi は K2.5 で `num_nextn_predict_layers=0` として MTP を明示的に無効化した。K3 は技術レポート・重みとも本稿執筆時点で未公開で、公表情報に言及がない (継承の有無は不明。[報告])。確定した反例は K2.5 だが、「MTP がデフォルト」という潮流への例外であることは明らかである。定理型で出力分布を守れる技術ですら、全社が採用するわけではない。理由は断定できないが、前節で述べた通り投機デコードのスループット利得は大バッチ・高稼働のサービングでは縮むか負にもなりうるため、そことのトレードオフであえて外す選択はありうる ([仮説])。

## 要素2: Attention の疎化・圧縮 — 1M文脈の主戦場

### なぜ2番目か、そして最も分岐しているか

背骨の式の $L_{\text{eff}} \cdot d_{\text{KV}} \cdot b_{\text{KV}}$、すなわち KV-Cache のバイト数を削る因子である。文脈長が1Mへ伸びた2026年、この項は重みの読み出しバイト数に匹敵しうるほど膨らむ。しかも KV はリクエストごとに固有でバッチ間で共有できないため、大バッチのサービングでは実質的なボトルネックが重みから KV へ移りうる。長文脈ほど効くため重要度が高い。同時に、ここが各社の設計が最も割れている主戦場でもある。前章の反例で列挙した通り、MLA、圧縮 Attention、DSA、SWA ハイブリッド、線形 Attention と、同じ問題に対する解法が百家争鳴の状態にある。

素朴な全結合 Attention の問題は2つある。計算量が系列長の2乗 $O(L^2)$ で効くこと、そして KV-Cache が系列長に比例して膨れ、その全てを毎トークン読み直すことである。この2つは別の問題であり、後で見る通り手法によって効く先が違う。前者の $O(L^2)$ 計算量は decode よりむしろ prefill (入力全体を一度に処理する局面) と学習時に効く。1M トークンの prefill は演算律速で数十秒から分オーダーかかり、長文脈での体感速度 (最初のトークンが返るまでの時間) をむしろ支配する。だから疎化・局所化・線形化の売りの半分は prefill 側にある。後者の KV バイトは decode の帯域に効く。以下では狙いで3系統に分けて整理する。KV のバイト数を削る低ランク圧縮、参照するトークン数を減らす疎選択・局所化、状態サイズを固定する線形化である。

### 系統1: 低ランク圧縮 (MLA とその後継)

まず系譜を押さえる。KV バイト削減の直系は MQA (Multi-Query Attention, arXiv:1911.02150) が全ヘッドで KV を1組に共有したのが出発点で、GQA (Grouped-Query Attention, arXiv:2305.13245) がヘッドをグループに分けて MHA と MQA の中間を取り、2023年以降ほぼ全ての dense モデルの標準装備になった。MLA はこの延長線上で「KV を低ランクの潜在表現に圧縮する」ところまで進めた手法である。

DeepSeek-V2 が導入した Multi-head Latent Attention (MLA) は、KV を低次元の潜在ベクトルに圧縮して保持する。各ヘッドが独立に大きな KV を持つ代わりに、共有された低ランク潜在表現から必要に応じて展開する。これにより KV cache を 93.3% 削減し、最大生成スループットを 5.76倍にしたと報告されている ([報告]、arXiv:2405.04434)。Kimi K2.5 はこの MLA を採用し、KV の低ランク射影の次元 (LoRA 次元) を512、Q の射影次元を1536に取る。

ここで重要な注意がある。MLA が削るのは KV のバイト数であって、Attention の計算量 $O(L^2)$ は潰さない。全トークンを参照する構造は保ったまま、参照時のメモリと帯域だけを圧縮する。したがってこの系統は「decode の KV 帯域」に効くが「prefill の $O(L^2)$ 計算」には効かない。DeepSeek-V4 はこれをさらに進め、CSA と HCA と呼ばれる圧縮 Attention のハイブリッドで、層ごとに128倍や4倍といった異なる圧縮率を使い分ける ([報告])。低ランク圧縮の背後には「Attention の実効的なランクが低い」という経験的観測がある (arXiv:2407.02490 MInference など)。

### 系統2: 疎選択・局所化 (DSA・SWA と全結合のハイブリッド)

系統1が「全トークンを参照するが KV を圧縮する」のに対し、この系統は「参照するトークン自体を減らす」。だから計算量 $O(L^2)$ にも効き、prefill の重さも和らげる。

疎選択の代表が GLM-5 の採る DeepSeek Sparse Attention (DSA) である。indexer と呼ぶ機構が各クエリに対して重要なトークンを top-k で選び、そこだけを参照する。MLA 構成を基盤に使うが、本質は低ランク圧縮ではなく疎選択である点で系統1と区別される。GLM-5.2 は indexer を疎注意4層ごとに共有する IndexShare を導入し、1M文脈時のトークンあたり FLOPs を約2.9分の1にした ([報告])。「学習の段階から疎パターンを焼き込む」この発想は NSA (Native Sparse Attention) 系の流れを汲む。

局所化の代表が Sliding Window Attention (SWA) である (原型は Sparse Transformer や Longformer・BigBird に遡り、実運用では Mistral 7B が広めた)。各トークンが直近の固定幅の窓だけを参照するので、KV-Cache が窓幅で頭打ちになり系列長に依存しなくなる。ただし局所窓だけでは長距離の依存を捉えられないため、一部の層は全結合 Attention を残すハイブリッドにする。Step-3.5 は SWA と全結合を 3対1 で交互に配置し、SWA 層の Q ヘッドを64から96に増やす ([報告])。Gemma 4 も local-to-global のハイブリッドで、モデルにより 4対1 や 5対1 の比率を採り、global 層では key を再利用して KV を 37.5% 削減する ([報告])。「何層に1層を全結合にするか」という比率は、後述するように理論から導かれるのではなくアブレーション実験の産物である。

### 系統3: 線形 Attention と状態空間モデル

より抜本的なのが線形 Attention である。全結合 Attention の $O(L^2)$ を、固定サイズの状態を持つ再帰へ書き換えることで $O(L)$ にする。arXiv:2006.16236 "Transformers are RNNs" が示したように、softmax を kernel で近似すると Attention は固定状態を持つ RNN と等価になる。その後、Schlag らの arXiv:2102.11174 "Linear Transformers Are Secretly Fast Weight Programmers" が delta 則 (状態を差分で更新する規則) の見方を与え、RWKV・RetNet・GLA といった世代を経て精緻化された。Mamba (arXiv:2312.00752) と Mamba-2 (arXiv:2405.21060) は選択的な状態空間モデル (SSM) でこれを実現し、arXiv:2412.06464 Gated Delta Networks は Mamba2 のゲートと delta 則を組み合わせた。Kimi Delta Attention や Gated DeltaNet の名前はこの delta 則の系譜に由来する。

ここで理解すべき本質は、固定状態は有限容量の連想記憶だということである。KV-Cache が全議事録の保管なら、固定状態は容量固定のホワイトボードで、書き続ければ古い記述は上書きされる。数理的に言えば、状態のサイズを $d_s$ 次元とすると、そこに厳密に復元可能な連想ペアの数はおおむね $d_s$ のオーダーで頭打ちになる。系列長 $L$ が $d_s$ を超えて伸びると、遠い過去の情報は必ず干渉・上書きを受ける。全結合 Attention が $L$ に比例して KV を積む (=どれだけ長くても厳密参照できる) のと対照的に、線形 Attention は状態が定数なので、長距離の厳密な参照が原理的に犠牲になる。RULER や needle-in-a-haystack のような長距離検索タスクで純粋な線形モデルが劣化しやすいのはこのためである。だから実務では、状態固定の安さと厳密参照の力を両取りするハイブリッドに落ち着く。Qwen3.5 は Gated DeltaNet (線形) と Grouped-Query Attention (GQA、複数の Q ヘッドで KV を共有する全結合系) と MoE を組み合わせ、Kimi K3 は Kimi Delta Attention (線形) と Gated MLA を 3対1 で混ぜる。Nemotron 3 は Mamba-2 を主体に一部の層だけ GQA を挟む Mamba-Transformer ハイブリッドである ([報告])。混合比 (3対1 など) をどこに置くかは、この状態容量と検索性能のトレードオフをアブレーションで探った結果であって、理論から一意に導かれるものではない。

### 品質保証は経験則が主

これら3系統に共通するのは、品質保証が定理ではなく経験則に大きく依存する点である。落とした重みの総和が $\varepsilon$ なら出力誤差が $\varepsilon \cdot \max\|v\|$ で抑えられるといった小さな補題はあるが、「Attention の質量が実際に少数のトークンに集中する」ことや「実効ランクが低い」ことは H2O や Quest による観測であって、事前に保証された性質ではない。圧縮率128倍や 3対1 の混合比といった具体値は、アブレーションで良かった設定を採っているにすぎない ([仮説] としての整理、観測自体は [報告])。だからこそ各社の解が分岐する。守るべき定理がないところでは、設計は経験と好みで割れるのである。

## 要素3: MoE — 容量とコストの分離

### なぜ3番目か

MoE は背骨の式の $P_{\text{active}}$ を下げる因子であり、2026年のフロンティア級モデルがほぼ全て採用する最も普遍的なトレンドである。削減インパクトでも普遍性でも最大で、その意味では本来「最重要」に近い。それでも後半に置くのは2つの理由による。第一に、定理保証の軸では最も弱く (品質を守る強い定理がほとんどない)、本連載が縦糸にする「定理型かどうか」の対比では端に位置する。第二に、容量とコストの分離という骨格は前章で説明済みで、本章では系譜と負荷分散という残る論点に絞れる。単なる高速化因子として見ると本質を見誤るため、思想の転換として最後の方でじっくり扱う。念のため付け加えると、MoE を推し進める最大の動機は歴史的には decode 帯域ではなく学習コスト (同じ学習 FLOPs でより高品質) であり、decode 帯域はそれと同方向に働く第二の圧力である。

### 条件付き計算の系譜

MoE の発想は新しくない。原点は Jacobs・Jordan らが1991年に示した "Adaptive Mixtures of Local Experts" に遡る。深層学習・大規模言語モデルの文脈で「条件付き計算」(入力ごとに一部のパラメータだけを使う) として復活させたのが arXiv:1701.06538 の Shazeer らで、これが現代の MoE の結節点になった。GShard (arXiv:2006.16668) と Switch Transformers (arXiv:2101.03961) が Transformer への大規模実装を確立し、後者は top-1 ルーティングで1兆パラメータに到達した。

スケール則の側でも精緻化が進んだ。arXiv:2202.01169 はアクティブと総パラメータを含む統合スケール則を与え、疎性の収穫逓減を定量化した。arXiv:2402.07871 は granularity (エキスパートの細かさ) を第3の変数として加えた。実運用の工夫としては、DeepSeekMoE (arXiv:2401.06066) の細粒度エキスパートと共有エキスパート、そして arXiv:2408.15664 の補助損失なし負荷分散 (bias の動的調整だけで負荷を分散する手法、V3が採用) が代表的である。

### 容量とコストの分離という思想

前章の散布図で見た通り、旧世代のアクティブ比が10%から28%だったのに対し、新世代は3%から7%へ移った。この移動を支えるのが「知識容量は総paramに比例し、演算コストはactive paramに比例する」という経験則である。arXiv:2404.05405 が合成的な事実想起タスクで観測した「約2ビット/パラメータ」の知識容量が、総paramを増やす動機を与える ([報告]、合成タスク観測。推論・合成能力が総paramに比例するかは別問題で未解明)。

したがって MoE の設計判断は、次の2つの制約の交点で決まる。総paramの上限は HBM 容量が縛り、アクティブ比の下限は「これ以上薄くすると品質が落ちる」という経験的な閾値と、all-to-all 通信の効率が縛る。前章で引いた DeepSeek-V3 の「1トークン最大4ノード」の制約 (NVLink と IB の帯域差3.2倍に由来) は、まさに後者のハード律速の一次証拠である ([報告])。アクティブ比が 3%から7% の帯に収束するのは、このコストモデル上の選択であって定理ではない ([仮説]。一次証拠が示すのは all-to-all がルーティングを縛ることまでで、具体的な帯を導く証言ではない)。

ここで decode の帯域という観点に一つ但し書きを付ける。前章の背骨の式で「MoE が $P_{\text{active}}$ を下げて帯域を減らす」と述べたのは batch=1 の話である。大バッチのサービングでは、バッチ内のトークンが異なるエキスパートに散るため、1ステップで読み出す重みは選ばれたエキスパートの和集合に広がり、バッチが十分大きいとほぼ全エキスパートに漸近する。つまり大バッチでは MoE の帯域削減効果は薄まる。それでも MoE が選ばれ続けるのは、帯域削減が主動機というより、前述の学習コストと、単一 GPU に載らない規模の知識容量を分散して持てるという容量・コスト分離の方が本質だからである。

### 負荷分散という現実の難所

MoE には理論的にきれいな定理が乏しい。負荷分散を最適輸送や割当問題として定式化することはできるが、品質を守る強い保証はほとんどない。特定のエキスパートにトークンが集中すると (ルーティングの偏り)、そのエキスパートがボトルネックになり並列効率が落ちる。これを補助損失で無理に均そうとすると品質が犠牲になるため、V3 が採った補助損失なしの bias 動的調整のような、経験的な工夫が実運用の要になる ([報告])。MoE は「容量とコストを分離できる」強力な枠組みだが、その代償として負荷分散という運用上の難所を抱える。

## 要素4: 低ビットネイティブ化 — 学習時に制約を焼き込む

### なぜ最後か

背骨の式の $b_{\text{weight}}$ と $b_{\text{KV}}$ を下げる因子である。最後に置くのは、これが単独で効く技術ではなく、他の3つと掛け算で効く「全体に薄くかかる係数」だからである。MoE でアクティブ比を下げた上で、その重みをさらに低ビットにする。KV を圧縮した上で、その KV をさらに低ビットにする。低ビット化は他因子の効果を底上げする位置にある。

### PTQ からネイティブ学習へ

量子化の系譜は、後付け (PTQ) から学習時前提 (QAT・ネイティブ) への移行として整理できる。GPTQ (arXiv:2210.17323) は二次近似の逐次 PTQ で3から4ビットを実現し、SmoothQuant (arXiv:2211.10438) は外れ値を重みへ移して W8A8 を、AWQ (arXiv:2306.00978) は活性化を意識した重み保護を達成した。FP8 は arXiv:2209.05433 で E4M3 と E5M2 が標準化され、FP8-LM (arXiv:2310.18313) が混合精度学習で GPT-175B のメモリを39%削減した。

2026年の潮流は、これらを「最初から低ビットで学習する」方向へ押し進めた。DeepSeek-V4 は FP4 と FP8 の混在 (エキスパートは FP4、他は FP8)、Gemma 4 は int2/int4 混在の QAT、Nemotron 3 の Super と Ultra は NVFP4 でのネイティブ事前学習を行う。NVFP4 のネイティブ学習は arXiv:2509.25149 が初めて公開実証したもので、Random Hadamard 変換と2次元ブロック量子化と確率的丸めを組み合わせ、12Bモデルを10兆トークンで学習した ([報告])。

### 理論の支柱と限界

量子化を支える理論的な柱は2つある。確率的丸めの不偏性 ($\mathbb{E}[Q(x)] = x$、arXiv:1502.02551 が基礎を与えた) と、PTQ の歪みに対する上界理論である。前者は「丸め誤差が平均的にはゼロになる」ことを保証し、低ビットでも期待値レベルでの正しさを支える。

しかし限界も明確である。QAT が実際に平坦な (量子化に頑健な) 解を見つけて int4 でも劣化が小さくなることは、理論というより経験的な観測である。QAT で使う Straight-Through Estimator (STE、arXiv:1308.3432) は、量子化の非微分な部分を勾配計算で恒等写像とみなす近似であり、その収束理論は限定的である。なぜこれほどうまくいくのかは十分に解明されていない、というのが筆者の見立てである ([仮説])。「どの層を FP4 に振り、どの層を FP8 に残すか」は、感度の高い層に多くのビットを割く配分問題 (通信理論の注水定理と同型) であり、定理ではなく設計者の選択である。

もう一つ、低ビット化はフリーランチではないという緊張も指摘しておく。前節で引いた知識容量の経験則は「1パラメータあたり約2ビット」だった。重みを FP4 (4ビット) にするということは、4ビットの器に2ビット相当の知識を詰めることであり、容量限界にかなり近い。実際 arXiv:2404.05405 は int4 量子化で知識容量が 2ビット/param を下回ることも報告している。低ビット化と知識容量の両立には原理的な天井があり、無制限に薄くできるわけではない。

フォーマットが未統一なことも、この領域が経験則主導であることを物語る。FP4 と FP8 の混在、MXFP4 と MXFP8、NVFP4、INT4 QAT と各社が別々の形式を採る。ハード (Blackwell の FP4 ネイティブ) が可能にしたものの、どの形式が標準になるかはまだ決着していない ([報告])。

なお KV-Cache 側の量子化については、本書の別チャプター「TurboQuant と PolarQuant」で情報理論の観点から詳しく扱っている。ランダム回転で分布を既知の形に押し込み、Shannon のレート歪み下界に定数倍で肉薄するという、量子化の理論的到達点を知りたい読者はそちらを参照してほしい。

## まとめ

第2章では4つの要素技術を重要な順に掘り下げた。

MTP・投機デコードは唯一の定理型である。修正版の棄却サンプリングが出力分布の厳密保存を保証し ($\alpha = 1 - \mathrm{TV}(p, q)$、1ステップあたり期待生成トークン数 $(1-\alpha^{\gamma+1})/(1-\alpha)$)、出力分布に関するトレードオフなしに高速化する。ただし守られるのは推論時の分布等価だけで、スループットや学習側には別のトレードオフが残る。Kimi のように採用しない選択があるのはそのためである。

Attention の疎化・圧縮は1M文脈の主戦場であり、最も分岐している。狙いは3つに分かれる。KV バイトを削る低ランク圧縮 (MLA 系、$O(L^2)$ は残す)、参照トークンを減らす疎選択・局所化 (DSA・SWA 系、$O(L^2)$ と prefill にも効く)、状態サイズを固定する線形化 (Mamba・DeltaNet 系、固定状態は有限容量の連想記憶) である。品質保証が経験則に依存するため、各社の解が割れる。

MoE は容量とコストの分離という思想の転換である。知識容量は総paramで、演算コストはactive paramで稼ぐ。アクティブ比が 3%から7% に収束するのは HBM 容量と all-to-all 帯域の交点というコストモデル上の選択であり、負荷分散という運用の難所を抱える。

低ビットネイティブ化は他の3因子に掛け算で効く係数であり、PTQ から学習時前提へ移った。確率的丸めの不偏性が支柱だが、QAT の有効性や層ごとのビット配分は経験則と運用判断であり、フォーマットもまだ統一されていない。

4つを貫く構図を最後にもう一度確認する。同じ「1トークンに読むバイト数を削る」という式を4因子で分担しながら、品質を守る型は非対称である。定理で守られるのは投機デコードだけで、残りは学習時の共設計と経験則に委ねられている。2026年のLLM設計は、この非対称性の上に成り立っている。収束の裏で最も豊かに発散しているのは、定理の後ろ盾がない領域 (とりわけ Attention と低ビットフォーマット) だという事実は、今後どこで競争が起きるかを予告している。

## 参考文献

- Robert A. Jacobs, Michael I. Jordan et al. "Adaptive Mixtures of Local Experts." Neural Computation, 1991.
- Noam Shazeer et al. "Outrageously Large Neural Networks: The Sparsely-Gated Mixture-of-Experts Layer." arXiv:1701.06538, 2017. [https://arxiv.org/abs/1701.06538](https://arxiv.org/abs/1701.06538)
- Dmitry Lepikhin et al. "GShard: Scaling Giant Models with Conditional Computation and Automatic Sharding." arXiv:2006.16668, 2020. [https://arxiv.org/abs/2006.16668](https://arxiv.org/abs/2006.16668)
- William Fedus, Barret Zoph, Noam Shazeer. "Switch Transformers." arXiv:2101.03961, 2021. [https://arxiv.org/abs/2101.03961](https://arxiv.org/abs/2101.03961)
- Aidan Clark et al. "Unified Scaling Laws for Routed Language Models." arXiv:2202.01169, 2022. [https://arxiv.org/abs/2202.01169](https://arxiv.org/abs/2202.01169)
- Jakub Krajewski et al. "Scaling Laws for Fine-Grained Mixture of Experts." arXiv:2402.07871, 2024. [https://arxiv.org/abs/2402.07871](https://arxiv.org/abs/2402.07871)
- Damai Dai et al. "DeepSeekMoE: Towards Ultimate Expert Specialization." arXiv:2401.06066, 2024. [https://arxiv.org/abs/2401.06066](https://arxiv.org/abs/2401.06066)
- Lean Wang et al. "Auxiliary-Loss-Free Load Balancing Strategy for Mixture-of-Experts." arXiv:2408.15664, 2024. [https://arxiv.org/abs/2408.15664](https://arxiv.org/abs/2408.15664)
- Zeyuan Allen-Zhu, Yuanzhi Li. "Physics of Language Models: Part 3.3, Knowledge Capacity Scaling Laws." arXiv:2404.05405, 2024. [https://arxiv.org/abs/2404.05405](https://arxiv.org/abs/2404.05405)
- Noam Shazeer. "Fast Transformer Decoding: One Write-Head is All You Need." arXiv:1911.02150, 2019. [https://arxiv.org/abs/1911.02150](https://arxiv.org/abs/1911.02150)
- Joshua Ainslie et al. "GQA: Training Generalized Multi-Query Transformer Models from Multi-Head Checkpoints." arXiv:2305.13245, 2023. [https://arxiv.org/abs/2305.13245](https://arxiv.org/abs/2305.13245)
- DeepSeek-AI. "DeepSeek-V2." arXiv:2405.04434, 2024. [https://arxiv.org/abs/2405.04434](https://arxiv.org/abs/2405.04434)
- Yaniv Leviathan, Matan Kalman, Yossi Matias. "Fast Inference from Transformers via Speculative Decoding." arXiv:2211.17192, 2022. [https://arxiv.org/abs/2211.17192](https://arxiv.org/abs/2211.17192)
- Mitchell Stern, Noam Shazeer, Jakob Uszkoreit. "Blockwise Parallel Decoding for Deep Autoregressive Models." arXiv:1811.03115, 2018. [https://arxiv.org/abs/1811.03115](https://arxiv.org/abs/1811.03115)
- Charlie Chen et al. "Accelerating Large Language Model Decoding with Speculative Sampling." arXiv:2302.01318, 2023. [https://arxiv.org/abs/2302.01318](https://arxiv.org/abs/2302.01318)
- Tianle Cai et al. "Medusa: Simple LLM Inference Acceleration Framework with Multiple Decoding Heads." arXiv:2401.10774, 2024. [https://arxiv.org/abs/2401.10774](https://arxiv.org/abs/2401.10774)
- Yuhui Li et al. "EAGLE: Speculative Sampling Requires Rethinking Feature Uncertainty." arXiv:2401.15077, 2024. [https://arxiv.org/abs/2401.15077](https://arxiv.org/abs/2401.15077)
- Fabian Gloeckle et al. "Better & Faster Large Language Models via Multi-token Prediction." arXiv:2404.19737, 2024. [https://arxiv.org/abs/2404.19737](https://arxiv.org/abs/2404.19737)
- Angelos Katharopoulos et al. "Transformers are RNNs: Fast Autoregressive Transformers with Linear Attention." arXiv:2006.16236, 2020. [https://arxiv.org/abs/2006.16236](https://arxiv.org/abs/2006.16236)
- Imanol Schlag, Kazuki Irie, Jürgen Schmidhuber. "Linear Transformers Are Secretly Fast Weight Programmers." arXiv:2102.11174, 2021. [https://arxiv.org/abs/2102.11174](https://arxiv.org/abs/2102.11174)
- Albert Gu, Tri Dao. "Mamba: Linear-Time Sequence Modeling with Selective State Spaces." arXiv:2312.00752, 2023. [https://arxiv.org/abs/2312.00752](https://arxiv.org/abs/2312.00752)
- Tri Dao, Albert Gu. "Transformers are SSMs: Generalized Models and Efficient Algorithms Through Structured State Space Duality." arXiv:2405.21060, 2024. [https://arxiv.org/abs/2405.21060](https://arxiv.org/abs/2405.21060)
- Songlin Yang et al. "Gated Delta Networks: Improving Mamba2 with Delta Rule." arXiv:2412.06464, 2024. [https://arxiv.org/abs/2412.06464](https://arxiv.org/abs/2412.06464)
- Zhenyu Zhang et al. "H2O: Heavy-Hitter Oracle for Efficient Generative Inference of Large Language Models." arXiv:2306.14048, 2023. [https://arxiv.org/abs/2306.14048](https://arxiv.org/abs/2306.14048)
- Jiaming Tang et al. "Quest: Query-Aware Sparsity for Efficient Long-Context LLM Inference." arXiv:2406.10774, 2024. [https://arxiv.org/abs/2406.10774](https://arxiv.org/abs/2406.10774)
- Elias Frantar et al. "GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers." arXiv:2210.17323, 2022. [https://arxiv.org/abs/2210.17323](https://arxiv.org/abs/2210.17323)
- Guangxuan Xiao et al. "SmoothQuant: Accurate and Efficient Post-Training Quantization for Large Language Models." arXiv:2211.10438, 2022. [https://arxiv.org/abs/2211.10438](https://arxiv.org/abs/2211.10438)
- Ji Lin et al. "AWQ: Activation-aware Weight Quantization for LLM Compression and Acceleration." arXiv:2306.00978, 2023. [https://arxiv.org/abs/2306.00978](https://arxiv.org/abs/2306.00978)
- Paulius Micikevicius et al. "FP8 Formats for Deep Learning." arXiv:2209.05433, 2022. [https://arxiv.org/abs/2209.05433](https://arxiv.org/abs/2209.05433)
- NVIDIA. "Pretraining Large Language Models with NVFP4." arXiv:2509.25149, 2025. [https://arxiv.org/abs/2509.25149](https://arxiv.org/abs/2509.25149)
- Shuming Ma et al. "The Era of 1-bit LLMs: All Large Language Models are in 1.58 Bits." arXiv:2402.17764, 2024. [https://arxiv.org/abs/2402.17764](https://arxiv.org/abs/2402.17764)
- Yoshua Bengio et al. "Estimating or Propagating Gradients Through Stochastic Neurons for Conditional Computation." arXiv:1308.3432, 2013. [https://arxiv.org/abs/1308.3432](https://arxiv.org/abs/1308.3432)
