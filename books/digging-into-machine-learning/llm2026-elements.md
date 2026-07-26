---
title: "[TBD] 2026年のLLM技術潮流 - 要素技術を重要な順に"
free: false
---

:::message
本稿は前章「ハード制約から読む設計収束」の続編である。前章で示した「1トークンの時間 ≈ (アクティブ重みバイト + KV バイト) ÷ 帯域 ÷ 期待受理長」という背骨の式を前提に、4つの要素技術を重要な順に掘り下げる。全変動距離、棄却サンプリング、低ランク近似、確率的丸めといった概念が登場する。統計検定準1級程度の確率と線形代数の素養があれば読み解ける。
:::

## はじめに

前章では2026年前半の主要 LLM が MoE、Attention 疎化・圧縮、MTP・投機デコード、低ビット学習の4点に収束した事実と、その背後にある帯域律速というハード制約を整理した。本章はそこから一歩踏み込み、4つの要素技術を数理まで下りて一つずつ解剖する。順序は「decode コスト削減のインパクト、定理保証の強さ、潮流の普遍性」を掛け合わせた重要度に従い、MTP・投機デコード、Attention 疎化・圧縮、MoE、低ビットネイティブ化の順で扱う。前章の背骨の式がどの因子を叩く技術なのかを各節の入口に置き、そこから技術固有の数理へ入る。

根拠レベルは前章と同じく明示する。[定理] は証明された保証、[報告] は一次ソースに原文がある事実、[仮説] は筆者の解釈である。加えて本章では、証明はあるが特定のモデル化仮定に依存する主張を [定理(仮定付き)] と表記して区別する (期待受理長やエキスパート和集合の閉形式がこれに当たる)。なお 2026年モデルの個別数値 (圧縮率・削減率・生成速度など) は、arXiv 番号のあるものを除き、各社の公式 blog・技術レポート・公開 config を一次ソースとする [報告] である。本稿執筆時点で技術レポートや重みが未公開のモデル (Kimi K3 など) については、その旨を都度明記する。

本章で繰り返し使う記号を先にまとめる。

| 記号 | 意味 |
|---|---|
| $p(x), q(x)$ | 本体モデル・ドラフトモデルの次トークン分布 |
| $\mathrm{TV}(p,q)$ | 全変動距離 $\frac{1}{2}\sum_x \lvert p(x)-q(x) \rvert$ |
| $\alpha$ | 1トークンの受理率 $1-\mathrm{TV}(p,q)$ |
| $\gamma$ | 1回の検証で先読みするドラフトトークン数 |
| $\tau$ | 1検証あたりの期待生成トークン数 (期待受理長) |
| $c$ | 本体1トークンに対するドラフト1トークンのコスト比 |
| $B$ | サービング時の実効バッチサイズ |
| $E, k$ | 総エキスパート数、1トークンが選ぶエキスパート数 |
| $u(B)$ | バッチ内で選ばれるエキスパートの和集合の割合 |
| $L, L_{\text{eff}}$ | 文脈長、実効的に参照する系列長 |
| $d_{\text{KV}}$ | 前章由来の KV バイト略記における1トークンあたり KV 次元 (本章では $n_{kv} d_h$ に分解) |
| $d_h, n_{kv}, b_{\text{KV}}$ | ヘッド次元、KV ヘッド数、KV の1要素バイト数 |
| $d_c, d_r$ | MLA の潜在次元、RoPE 用の分離次元 |
| $d_k, d_v$ | 線形 Attention の状態の key 側・value 側次元 |
| $S, \phi$ | 線形 Attention の状態行列 ($d_k \times d_v$)、特徴写像 |
| $\beta_t, g_t$ | delta 則の書き込み強度、忘却ゲート |
| $\Delta, b_l$ | 量子化の刻み幅、層 $l$ に割り当てるビット数 |

## 要素1: MTP・投機デコード — 唯一の定理型

投機デコードは背骨の式の分母にある $\tau$ (期待受理長) を大きくする唯一の因子で、他の3つが「1トークンに読むバイト数」を削るのに対し、これは「読み出しの回数」を割り算で減らす。しかも「速くしても出力の確率分布を変えない」ことを厳密な定理で保証できる点が際立っており、だから最初に置く。

### 分布保存の定理: 棄却と残差の恒等式

自己回帰デコードは1トークンずつ逐次に生成するため、毎トークン巨大な重みを HBM から読み直す。この逐次性を隠すのが投機デコード (speculative decoding) である。小さく速いドラフトモデルが先に $\gamma$ トークンを提案し、本体モデルがそれらをまとめて1回の順伝播で検証する。当たっていれば複数トークンを一度に確定し、外れたらそこで打ち切る。

![投機デコードのタイムライン: ドラフトが先読みした複数トークンを本体が1パスで並列検証し、受理長ぶんをまとめて確定する](/images/books/digging-into-machine-learning/llm2026-e1-timeline.png)

核心は修正版の棄却サンプリング (modified rejection sampling) にある。本体の次トークン分布を $p(x)$、ドラフトを $q(x)$ とする。ドラフトが出したトークン $x$ を確率 $\min(1, p(x)/q(x))$ で受理し、棄却したら残差分布 $\mathrm{norm}(\max(0, p(x)-q(x)))$ から引き直す。この手続きが出す分布が本体単独の $p(x)$ に厳密一致することは、各点で成り立つ次の恒等式から2行で出る ([定理]、arXiv:2211.17192 と arXiv:2302.01318 が独立に証明)。

$$
\min(p, q) + (p-q)_+ = p, \qquad (x)_+ := \max(0, x)
$$

実際、最終的に $x$ が出力される確率は、受理経路と棄却後の再サンプル経路の和で

$$
P(X=x) = \underbrace{q(x)\min\!\left(1, \tfrac{p(x)}{q(x)}\right)}_{=\,\min(p(x),\,q(x))} + \underbrace{\Big(\textstyle\sum_y (p(y)-q(y))_+\Big)}_{=\,P(\text{棄却})} \cdot \frac{(p(x)-q(x))_+}{\sum_y (p(y)-q(y))_+} = \min(p(x), q(x)) + (p(x)-q(x))_+ = p(x)
$$

となり、ドラフトが何であれ出力は $p$ に戻る。同じ恒等式の全語彙和を取ると受理率も即座に出る。トークンが受理される確率 $\alpha$ は

$$
\alpha = \sum_x \min(p(x), q(x)) = \sum_x \big[p(x) - (p(x)-q(x))_+\big] = 1 - \sum_x (p(x)-q(x))_+ = 1 - \mathrm{TV}(p, q)
$$

最後の等号は、$\sum_x (p(x)-q(x)) = 0$ より正部分の和と負部分の和が等しく、$\sum_x (p-q)_+ = \frac{1}{2}\sum_x |p-q| = \mathrm{TV}(p,q)$ となることによる。すなわち $\alpha = 1 - \mathrm{TV}(p,q)$ は天下りの定義ではなく、分布保存の証明と同じ恒等式の副産物である。ドラフトが本体をよく真似るほど $\mathrm{TV}$ が小さく受理率が上がる。

![min(p,q) の重なり面積が受理率 α、はみ出した残差 (p−q)+ から再サンプルして分布が p に戻る](/images/books/digging-into-machine-learning/llm2026-e2-accept.png)

保証の範囲は正確に区切る。厳密な等価は同一 prefix・厳密演算の下の話で、実装では浮動小数点の非結合性やバッチ依存の数値揺らぎで完全な1ビット一致にはならない。守られるのは「出力分布」であって「スループット」ではない (後述)。さらに MTP を学習に組み込むと本体の重み自体が変わるため、MTP 込みで学習したモデルは MTP なしのモデルとは別の分布を持つ。定理が守るのは「与えられた本体モデルを推論時に速く走らせても分布を変えない」ことに限られる。

### 期待受理長: 打ち切り幾何級数

どれだけ速くなるかは、1回の検証で平均何トークン確定できるかで決まる。生成長を $N = 1 + (\text{受理数})$ と定義する (棄却時の再サンプル1個、または全受理時のボーナス1個が末尾に必ず付くので $+1$ する)。各トークンが独立に確率 $\alpha$ で受理されると仮定すると、$N$ は打ち切り幾何分布に従い

$$
\tau := \mathbb{E}[N] = \sum_{j=0}^{\gamma-1}(j+1)\,\alpha^j(1-\alpha) + (\gamma+1)\,\alpha^\gamma = \sum_{j=0}^{\gamma}\alpha^j = \frac{1-\alpha^{\gamma+1}}{1-\alpha}
$$

となる ([定理(仮定付き)]、$\alpha$ が位置・文脈によらず一定という近似の下での閉形式)。中央の等号は、非負整数値の期待値が生存関数の和で書ける恒等式 $\mathbb{E}[N] = \sum_{j\ge 0} P(N > j)$ による。ここでは「$j$ 個目まで受理される」確率が $P(N>j) = \alpha^j$ ($j \le \gamma$) なので、和を直接計算する手間なく等比級数の部分和 $\sum_{j=0}^{\gamma}\alpha^j$ に落ちる。$\alpha=0.7$、$\gamma=4$ なら $\tau = (1-0.7^5)/0.3 \approx 2.77$ で、前章のウォーターフォールで置いた値はこれである。区別すべきは、分布が厳密に保たれることは無条件の [定理]、この閉形式は $\alpha$ 一定という近似込みの [定理(仮定付き)]、$\alpha$ が高いこと自体はモデル依存の [報告] という3点である。

### 加速の天井とスループットの落とし穴

前章で $\tau$ で単純に割った近似は、ドラフトの追加コストを無視した上界だった。ドラフト1トークンのコスト比を $c$ とし、検証1パスを本体1トークン分の時間とみなすコストモデル (Leviathan らの仮定) を置くと、実効改善率は ([定理(仮定付き)]、このコストモデルの下での閉形式)

$$
\text{改善率} = \frac{1-\alpha^{\gamma+1}}{(1-\alpha)(\gamma c + 1)}, \qquad c=0 \text{ のもとで } \gamma\to\infty \text{ とすると } \frac{1}{1-\alpha} \text{ が天井}
$$

となる。$c>0$ を固定して $\gamma$ だけ伸ばすと分母の $\gamma c$ が効いて改善率はむしろ 0 へ潰れるので、天井 $1/(1-\alpha)$ はドラフトが十分軽い ($\gamma c \to 0$) 極限の話である。受理率 $\alpha$ が加速の上限を決め、MTP ヘッド方式は $c$ が小さいので近似が良いが独立ドラフトでは無視できない。さらに重要なのは、この利得が帯域律速の領域に限られることである。1検証は $\gamma+1$ トークン分の計算をまとめて行うので、検証パスの所要時間は

$$
T_{\text{verify}} \approx \max\!\left(\frac{W_{\text{bytes}} + B \cdot \text{Bytes}_{\text{KV}}}{BW}, \; \frac{(\gamma+1)\, B \cdot 2P_{\text{active}}}{\text{FLOPS}}\right)
$$

で書ける (帯域項は重みと KV の読み出しの和、$B$ に比例する KV 側は大バッチで無視できない。演算項の $2P_{\text{active}}$ は1トークンあたり順伝播の FLOPs)。右項 (演算律速) が勝つ大バッチ・高稼働のサービングでは、検証の余剰計算がスループットを食い、利得は縮むか負にもなる。分布保存という定理の裏で、スループット上のトレードオフは確かに存在する。

### MTP: 学習側にドラフトを内蔵する

初期の投機デコードは、本体とは別に小さなドラフトモデルを用意する必要があった (先読みして並列に検証する発想自体は、Leviathan らに先行する Stern らの Blockwise Parallel Decoding, arXiv:1811.03115 に遡る)。ここを進化させたのが Multi-Token Prediction (MTP) である。Meta の arXiv:2404.19737 が示したように、学習の段階で本体モデルに複数の予測ヘッドを持たせ、次の1トークンだけでなく数トークン先までを同時に予測させる。こうして訓練されたヘッドは、推論時にそのままドラフタとして使え、別モデルを用意する手間が消える。

力点を誤らないよう補足する。MTP の第一義は投機デコードの部品ではなく、学習信号を密にしてサンプル効率と生成能力そのものを高めることにあった。学習時には次の1トークンだけでなく $K$ トークン先までの予測を損失に足す。

$$
\mathcal{L}_{\text{MTP}} = \mathcal{L}_1 + \sum_{m=2}^{K}\lambda_m\, \mathbb{E}\big[-\log p_\theta(x_{t+m}\mid x_{\le t})\big]
$$

第1項が通常の言語モデル損失、第2項以降が先読みヘッドの損失で、$\lambda_m$ が重みである。上式は各ヘッドが $x_{\le t}$ だけを条件に $m$ トークン先を予測する Meta の並列ヘッド方式に対応する。DeepSeek の逐次モジュール方式は $m$ 番目の予測に $m-1$ 番目のモジュールの隠れ状態を渡し、学習時は teacher forcing で途中のトークンも条件に使う点で、添字の依存構造が違う。いずれも1トークンあたりの教師信号を増やす狙いで、Gloeckle らの主結果もこの品質向上であり、DeepSeek-V3 も MTP を第一には学習改善として入れている。投機デコードへの転用は強力な副次効果で、「ドラフトを内蔵する技術」とだけ見ると系譜の本質を半分落とす。

2026年のモデルはこの MTP を広く取り込んだ。DeepSeek-V4 は config に `num_nextn_predict_layers=1` を持ち MTP を継承し、GLM-5 は MTP を1層備え、5.2では受理長を最大20%改善したと報告する。Step-3.5 は MTP-3 で1回の順伝播から4トークンを並列に予測・検証し、毎秒100から300トークンを出す。Nemotron 3 の Super と Ultra は共有重みの MTP を2層持つ ([報告])。

投機デコードから MTP への発展は、ドラフト品質の工夫競争として整理できる。Medusa (arXiv:2401.10774) が複数デコードヘッドとツリー Attention を導入し、EAGLE (arXiv:2401.15077) が特徴量レベルの自己回帰予測で受理率を上げ、続く EAGLE-2 が文脈に応じてドラフト木の形を変える動的な木探索へと発展させた。MTP はこの流れを事前学習側に内蔵し、実運用の標準装備にした位置づけになる。

### 反例: Kimi は K2.5 で MTP を外した (K3 も言及なし)

Moonshot の Kimi は K2.5 で `num_nextn_predict_layers=0` として MTP を明示的に無効化した。後継の K3 は公式 blog で Stable LatentMoE (総パラメータ 2.8T、896エキスパート中16活性 + 共有エキスパート)、KDA (線形) と Gated MLA を 3対1 で混ぜたハイブリッド Attention、MXFP4 重み + MXFP8 活性の QAT といったアーキテクチャを詳細に述べているが、そこに MTP への言及はない (技術レポート・重みは本稿執筆時点で未公開のため継承の有無は不明。[報告])。定理型で出力分布を守れる技術ですら全社が採用するわけではない、という反例である。理由はまさに前節の $T_{\text{verify}}$ の式で読める。分布保存の定理が守るのはレイテンシ最適時の分布であって、演算律速側 (右項が勝つ大バッチ) では検証の余剰計算がスループットを削る。高稼働のサービングを主戦場に置くなら、レイテンシ利得よりスループット損の方が効く領域があり、そこであえて MTP を外すのは式の上で筋が通る ([仮説])。ただし MTP を学習に入れること (品質向上) と推論で投機に使うこと (レイテンシ短縮) は本来分離でき、演算律速が理由なら推論時にヘッドを無効化すれば済むので、学習からヘッドを持たない選択には学習コストや実装の複雑性を避ける別の動機も併存しうる ([仮説])。第1章では「例外の報告」として挙げたこの反例は、ここでは $T_{\text{verify}}$ の式が予言する動作領域の一例として説明される。

## 要素2: Attention の疎化・圧縮 — 1M文脈の主戦場

この技術群は背骨の式のうち KV-Cache のバイト数を削る因子を叩き (前章では KV バイトを $L_{\text{eff}} \cdot d_{\text{KV}} \cdot b_{\text{KV}}$ と略記した。本節ではこれを下で全因子に分解する)、文脈が1Mへ伸びた2026年にこの項は重み読み出しに匹敵するほど膨らむ。同じ問題に MLA・圧縮 Attention・DSA・SWA ハイブリッド・線形 Attention と各社の解が最も割れる主戦場であり、その分岐を数式のどの因子を叩くかで整理するのが本節の狙いである。

まず削減対象の KV バイトを分解する。1リクエストの KV-Cache 全体のバイト数は、key と value の2つ、層数 $n_{\text{layer}}$、KV ヘッド数 $n_{kv}$、ヘッド次元 $d_h$、1要素のバイト数 $b_{\text{KV}}$、系列長 $L$ の積で

$$
\text{Bytes}_{\text{KV}} = \underbrace{2}_{K,V} \cdot\, n_{\text{layer}} \cdot n_{kv} \cdot d_h \cdot b_{\text{KV}} \cdot L
$$

と書ける。後述の3系統は、この式のどの因子を叩くかで綺麗に分かれる。低ランク圧縮 (MLA) はヘッドあたりの $2\, n_{kv} d_h$ を潜在次元へ潰し、疎選択・局所化 (DSA・SWA) は $L$ を参照トークン数 $k_{\text{top}}$ や窓幅 $w$ へ縮め、線形化はこの式ごと固定サイズの状態に置き換える (前段の GQA は KV ヘッド数 $n_{kv}$ をグループ数まで減らす古典的な一手である)。$b_{\text{KV}}$ を下げる KV 量子化は要素4で扱う。

![KV バイトの式を中央に置き、各因子を叩く手法を対応づけた解剖図。GQA は n_kv、MLA は 2 n_kv d_h、SWA/DSA は L、線形化は式全体を固定状態へ](/images/books/digging-into-machine-learning/llm2026-e3-kvbytes.png)

### 設計を分ける軸: コスト・性能・精度

個別手法に入る前に、なぜ全結合 Attention が置き換えられるのかをコスト・性能・精度の3面から整理する。前章はハード制約 (帯域と HBM 容量) を主役に据えたが、Attention の設計軸はそれだけでは閉じない。全結合 Attention は次の3つの負担を抱え、その対価に1つの強みを持つ。

- decode の KV 帯域: 1トークン生成ごとに全 KV を読み直す。系列長 $L$ に比例し、上の $\text{Bytes}_{\text{KV}}$ がそのまま効く。decode の帯域はアクティブ重みと KV の読み出しの和で決まり (背骨の式の分子)、短文脈では重み側が、長文脈では KV 側が支配的になる。ここが TPOT を左右する。
- KV の HBM 占有: KV-Cache が GPU メモリを食い、同時に載せられる系列数 (バッチ) の上限を決める。1M 文脈では1系列で数十 GB に達し、バッチが組めずスループットとトークン単価が悪化する。
- prefill の $O(L^2)$ 計算: 入力全体を一度に処理する prefill では Attention の演算量が $L^2$ で効く。これは演算律速で、最初のトークンが返るまでの時間 (TTFT) を支配する。1M トークンの prefill は数十秒から分オーダーになりうる (なお $O(L^2)$ の中間行列を実体化するメモリ問題は FlashAttention で解消済みで、ここで問題なのは演算量の方である)。
- 強み: 全トークンを厳密に参照できる。どれだけ離れたトークンでも正確に引ける。長距離検索 (needle-in-a-haystack) の精度はこの性質に支えられる。

ここで性能が一枚岩でないことが要点である。同じ「速さ」でも、次の3つの指標は別々の因子に律速される。

- TTFT (最初のトークンが返るまでの時間): prefill の $O(L^2)$ 計算に律速される。
- TPOT (以降1トークンあたりの時間): decode の帯域に律速される。アクティブ重みと KV の読み出しの和で、長文脈ほど KV 側が支配的になる。
- スループット (とトークン単価): まず KV 容量が同時実行数の上限を決め、その上限までバッチを積めた先では演算律速へ移る。

効かせたい指標が違えば叩くべき因子も違う。対話は TTFT と TPOT の両方が、長文入力タスクは TTFT (prefill 計算) が、高同時実行のサービングはスループットが主戦場になる。前章のルーフラインで言えば、decode 側の Attention は GEMV 的で演算強度が低く帯域側に張り付く一方、prefill 側は GEMM 的で演算律速に寄る。だから decode の KV 帯域を削るには KV を共有・圧縮して1バイトあたりの演算を増やす方向 (系統1) が、prefill の $O(L^2)$ まで削るには参照そのものを間引く方向 (系統2・3) が効く。

精度の側では、全トークン厳密参照を捨てられるかどうかが分岐点になる。多くのトークンでは注意質量が少数のトークンに集中する (後述の経験的観測) ため近似が許されるが、長距離の厳密な検索を要するタスクでは厳密参照が要る。だから設計は「全部を近似に振る」ではなく「一部の層だけ全結合を残す」ハイブリッドへ繰り返し収束する。

この軸で見ると、以下の3系統 (系統2は疎選択と局所化にさらに分かれる) は「どの負担を削り、何を犠牲にするか」で綺麗に分かれる。

| 系統 | 主に削る負担 | 犠牲にする精度 | 効くフェーズ |
|---|---|---|---|
| 系統1 低ランク圧縮 (MLA) | KV 帯域・KV 容量 | 小 (低ランク近似だが経験的に近ロスレス) | decode |
| 系統2a 疎選択 (DSA) | KV 帯域・$O(L^2)$ 計算 | 全トークン参照を近似 | prefill・decode |
| 系統2b 局所化 (SWA) | KV 帯域・容量・$O(L^2)$ 計算 | 全トークン参照を近似 | prefill・decode |
| 系統3 線形化・SSM | KV も $O(L^2)$ も (状態を固定) | 厳密参照を最も手放す (容量 $N \le d_k$) | prefill・decode |

低ランク圧縮は全結合の構造を保ったまま KV だけを潰すので精度をほとんど犠牲にせず decode に効き、疎選択・局所化は参照トークンを間引いて $O(L^2)$ にも効かせる代わりに全トークン参照を近似し、線形化は状態を固定して両コストを消す代わりに厳密参照を最も大きく手放す。削る量と精度の犠牲がこの順で大きくなる。ただし疎選択 (DSA) と局所化 (SWA) は KV 容量への効き方が違う。窓外を捨てる SWA は KV-Cache そのものを窓幅に頭打ちにできるが、クエリごとに top-k で参照先を選ぶ DSA は「どのトークンが後で選ばれるか事前に分からない」ため全 KV を HBM に保持し続ける必要があり、削るのは帯域と計算であって容量ではない。以下、順に見ていく。

### 系統1: 低ランク圧縮 (MLA とその後継)

まず系譜を押さえる。KV バイト削減の直系は MQA (Multi-Query Attention, arXiv:1911.02150) が全ヘッドで KV を1組に共有したのが出発点で、GQA (Grouped-Query Attention, arXiv:2305.13245) がヘッドをグループに分けて MHA と MQA の中間を取り、2023年以降ほぼ全ての dense モデルの標準装備になった。MLA はこの延長線上で「KV を低ランクの潜在表現に圧縮する」ところまで進めた手法である。

DeepSeek-V2 が導入した Multi-head Latent Attention (MLA) は、各トークンにつき低次元の潜在ベクトル $c_s$ だけをキャッシュし、必要に応じて $k_s = W_{UK} c_s$ のように展開する。これにより KV cache を 93.3% 削減し、最大生成スループットを 5.76倍にしたと報告されている ([報告]、arXiv:2405.04434)。Kimi K2.5 はこの MLA を採用し、KV の潜在次元 $d_c$ を512、Q の射影次元を1536に取る ([報告])。

MLA が GQA と本質的に違うのは、単に KV を小さくするだけでなく、展開行列を Q 側へ吸収できる点にある。Attention スコアを潜在ベクトルのまま書くと

$$
q_t^\top k_s = (W_{UQ}\, q'_t)^\top (W_{UK}\, c_s) = q'^{\,\top}_t \underbrace{(W_{UQ}^\top W_{UK})}_{\text{事前に1つの行列へ}} c_s
$$

ここで $q'_t$ はクエリ側の低次元潜在ベクトル ($c_s$ が KV 側の潜在ベクトルであるのと対になる。この $c_s$ は前節のコスト比 $c$ とは無関係な別記号である)、$W_{UQ}, W_{UK}$ はそれぞれを Attention 空間へ展開する行列である。$W_{UQ}^\top W_{UK}$ を1つの行列に畳んでおけば、decode 時にキャッシュから読むのは低次元の $c_s$ だけで済む (展開した大きな $k_s$ を持つ必要がない)。これが KV バイトを潜在次元まで潰せる仕掛けである。ただし RoPE を入れると位置 $t, s$ の回転行列 $R_t, R_s$ がクエリとキーの間に挟まり、その積が相対位置 $R_{s-t}$ に依存するため、位置に依らない1つの行列へ事前に畳めなくなる (吸収が壊れる)。そこで MLA は位置情報用に小さな $d_r$ 次元を分離して別枠で持つ (DeepSeek で $d_c + d_r = 512 + 64$)。なお MLA が削るのは KV バイトであって計算量 $O(L^2)$ は潰さない。全トークンを参照する構造は保つので、この系統は「decode の KV 帯域」に効くが「prefill の $O(L^2)$ 計算」には効かない。DeepSeek-V4 はこれを進め、CSA と HCA と呼ぶ圧縮 Attention のハイブリッドで層ごとに128倍や4倍の圧縮率を使い分ける ([報告])。背後には「KV を低ランクに圧縮しても品質がほぼ落ちない」という経験的観測があり、これは DeepSeek-V2 自身が MHA 同等以上の性能を報告したアブレーションに支えられている ([報告]、arXiv:2405.04434)。

### 系統2: 疎選択・局所化 (DSA・SWA と全結合のハイブリッド)

系統1が「全トークンを参照するが KV を圧縮する」のに対し、この系統は「参照するトークン自体を減らす」。KV バイトの式の $L$ を、選んだ集合 $\mathcal{S}$ のサイズ $k_{\text{top}}$ (疎選択) や窓幅 $w$ (局所化) へ縮める。だから計算量 $O(L^2)$ にも効き、prefill の重さも和らげる。

参照集合を $\mathcal{S}$ に絞って残りを捨てると、出力 (value の重み付き和) の誤差は捨てた注意重みの総和 $\varepsilon = \sum_{i \notin \mathcal{S}} a_i$ で

$$
\|o - \hat o\| = \Big\|\sum_{i \notin \mathcal{S}} a_i v_i\Big\| \le \varepsilon \max_i \|v_i\|
$$

と抑えられる (ここで $\hat o$ は $\mathcal{S}$ 外の項を落とした非正規化の打ち切り。実装のように $\mathcal{S}$ 上で softmax を再正規化した場合も誤差は同オーダー $O(\varepsilon)$ で抑えられる)。この不等式自体は無条件に成り立つ [定理] だが、肝心の「$\varepsilon$ が実際に小さい」こと (注意質量が少数のトークンに集中すること) は保証ではなく観測である。つまり疎化の品質は、補題ではなく後述の経験則が支える。

疎選択の代表が GLM-5 の採る DeepSeek Sparse Attention (DSA) である。indexer と呼ぶ機構が各クエリに対して重要なトークンを上位 $k_{\text{top}}$ 個だけ選び、そこだけを参照する。MLA 構成を基盤に使うが、本質は低ランク圧縮ではなく疎選択である点で系統1と区別される。GLM-5.2 は indexer を疎注意4層ごとに共有する IndexShare を導入し、1M文脈時のトークンあたり FLOPs を約2.9分の1にした ([報告])。「学習の段階から疎パターンを焼き込む」この発想は NSA (Native Sparse Attention) 系の流れを汲む。

局所化の代表が Sliding Window Attention (SWA) である (原型は Sparse Transformer や Longformer・BigBird に遡り、実運用では Mistral 7B が広めた)。各トークンが直近の固定幅の窓だけを参照するので、KV-Cache が窓幅で頭打ちになり系列長に依存しなくなる。ただし局所窓だけでは長距離の依存を捉えられないため、一部の層は全結合 Attention を残すハイブリッドにする。Step-3.5 は SWA と全結合を 3対1 で交互に配置し、SWA 層の Q ヘッドを64から96に増やす ([報告])。Gemma 4 も local-to-global のハイブリッドで、モデルにより 4対1 や 5対1 の比率を採り、global 層では key を再利用して KV を 37.5% 削減する ([報告])。「何層に1層を全結合にするか」という比率は、後述するように理論から導かれるのではなくアブレーション実験の産物である。

### 系統3: 線形化 (線形 Attention・DeltaNet・SSM)

より抜本的なのが線形 Attention である。全結合 Attention の $O(L^2)$ を、固定サイズの状態を持つ再帰へ書き換えることで $O(L)$ にする。arXiv:2006.16236 "Transformers are RNNs" が示したのは、softmax の指数カーネルを特徴写像 $\phi$ の内積で近似すると、Attention が固定状態を持つ RNN と等価になるという事実である。$\exp(q_t^\top k_s) \to \phi(q_t)^\top \phi(k_s)$ と置くと、出力は状態行列 $S_t = \sum_{s \le t} \phi(k_s) v_s^\top$ と正規化項 $z_t = \phi(q_t)^\top \sum_{s \le t}\phi(k_s)$ を使って $o_t = \phi(q_t)^\top S_t / z_t$ と書け、状態は

$$
S_t = S_{t-1} + \phi(k_t)\, v_t^\top
$$

という単純な累積更新に従う ([定理])。$S_t$ は $d_k \times d_v$ の固定サイズで、系列長 $L$ に依存しない。これが $O(L)$ かつ定数メモリの正体である (以降の DeltaNet 系は分母 $z_t$ を落とし、正規化を別途行う形が主流である)。

#### DeltaNet: 予測誤差で記憶を上書きする

特徴写像を恒等とみなした累積更新 $S_t = S_{t-1} + k_t v_t^\top$ には弱点がある (実際の DeltaNet は SiLU 特徴写像と L2 正規化したキーを使うが、ここでは骨格を見るため恒等写像で書く)。似たキーが繰り返し来ると値が無条件に足し込まれ、状態が飽和して古い連想が壊れる。Schlag らの arXiv:2102.11174 "Linear Transformers Are Secretly Fast Weight Programmers" は、Widrow–Hoff の古典的な delta 則 (least mean squares) をこの線形 Transformer の fast weight 更新として持ち込み、状態を連想記憶としてオンライン学習する更新に置き換えた。

状態 $S$ を「キーを入れると値を返す」線形連想記憶とみなすと、キー $k_t$ に対する現在の予測値は $\hat v_t = S_{t-1}^\top k_t$ である (前節の読み出し $o_t = \phi(q_t)^\top S_t$ は $S$ を左から掛ける行ベクトル向き、こちらは値を復元するため $S^\top$ を掛ける列ベクトル向きで、転置1つの違いだが同じ状態を指す)。新しい値 $v_t$ との予測誤差 (delta) $v_t - \hat v_t$ だけを、書き込み強度 $\beta_t$ でキー方向に足す:

$$
S_t = S_{t-1} + \beta_t\, k_t\,(v_t - S_{t-1}^\top k_t)^\top = \big(I - \beta_t\, k_t k_t^\top\big)\,S_{t-1} + \beta_t\, k_t v_t^\top
$$

DeltaNet の名前はこの「予測誤差 (delta) で状態を補正する」構造に由来する。この更新は、二乗誤差 $\mathcal{L}_t = \frac{1}{2}\|S^\top k_t - v_t\|^2$ を状態について1ステップ勾配降下したもの ($S_t = S_{t-1} - \beta_t \nabla_S \mathcal{L}_t$、$\nabla_S \mathcal{L}_t = k_t(S_{t-1}^\top k_t - v_t)^\top$) にちょうど一致する ([定理])。書き込み強度 $\beta_t$ は入力からトークンごとに学習される学習率で、実装では sigmoid を通して $(0,1)$ に収めることが多い (勾配降下の安定性だけなら $(0,2)$ まで許される)。大きく取れば $k_t$ 方向の既存成分を強く消してから上書きし、小さく取れば緩やかに混ぜる。単なる加算が「無条件に足す」のに対し、delta 則は「必要な差分だけ足す」ので、有限容量の状態を効率よく使える。

もっとも、この $(I - \beta_t k_t k_t^\top)$ という行列が各ステップ左から挟まる形は、加算更新のような単純な累積和には畳めず、系列方向の並列学習が難しい。これを chunk 単位の行列演算に展開して GPU で並列化する道を開いたのが arXiv:2406.06484 "Parallelizing Linear Transformers with the Delta Rule over Sequence Length" で、DeltaNet が実モデルのスケールに乗る転機になった。arXiv:2412.06464 Gated Delta Networks はさらに、状態を一律に減衰させる忘却ゲート $g_t \in (0,1)$ を掛けた $S_t = g_t\big(I - \beta_t k_t k_t^\top\big)S_{t-1} + \beta_t k_t v_t^\top$ を与え、Mamba-2 流の選択的忘却と delta 則を統合した。Kimi Delta Attention (KDA) はこれを土台に (arXiv:2510.26692 "Kimi Linear")、忘却ゲートをスカラーからチャネルごとに独立な対角ゲートへ細粒度化したものである。

系譜としては RWKV・RetNet・GLA を経て精緻化され、Mamba (arXiv:2312.00752) と Mamba-2 (arXiv:2405.21060) が選択的な状態空間モデル (SSM) で同型の再帰を実現した。線形 Attention・SSM・DeltaNet は、いずれも「固定サイズの状態を再帰で更新する」という同じ骨格の変奏である。

#### 固定状態の容量限界

ここで理解すべき本質は、固定状態は有限容量の連想記憶だということである。KV-Cache が全議事録の保管なら、固定状態は容量固定のホワイトボードで、書き続ければ古い記述は上書きされる。この限界はランクの議論で厳密に書ける。$N$ 個の連想ペア $(k_i, v_i)$ を加算で書き込んだ状態 $S = \sum_{i=1}^{N} k_i v_i^\top$ から、キー $k_j$ で読み出す ($\hat v = S^\top k$) ことを考える。キーは $\lVert k_i \rVert = 1$ に正規化されているとすると (DeltaNet 系の実装でもキーは L2 正規化するのが標準である)、

$$
S^\top k_j = v_j + \underbrace{\sum_{i \ne j} (k_i^\top k_j)\, v_i}_{\text{干渉項}}
$$

となる ($k_j^\top k_j = 1$ を使った。書き込みも読み出しも系統3の状態規約 $S = \sum \phi(k) v^\top$ に揃えた)。干渉項が消えて $v_j$ を厳密復元できる十分条件は $\{k_i\}$ が正規直交なこと ($k_i^\top k_j = 0\ (i \ne j)$) だが、$d_k$ 次元空間で正規直交にできるベクトルは高々 $d_k$ 本しかない。よって任意の値ベクトルを厳密復元できるペア数は $N \le d_k$ で頭打ちになる (状態の表現力自体も $\mathrm{rank}(S) \le \min(d_k, d_v)$ で抑えられる。[定理])。系列長 $L$ が $d_k$ を超えて伸びると、干渉項が無視できなくなり、遠い過去の情報は必ず上書き・混線を受ける。全結合 Attention が $L$ に比例して KV を積む (=どれだけ長くても厳密参照できる) のと対照的に、線形 Attention は状態が定数なので、長距離の厳密な参照が原理的に犠牲になる。RULER や needle-in-a-haystack のような長距離検索タスクで純粋な線形モデルが劣化しやすいのは、この干渉項の顕在化として説明できる。

![左は追記型台帳の KV-Cache (L に比例して伸びる)、右は d_k×d_v 固定のホワイトボード。書き込むペア数 N が d_k を超えると干渉項が漏れ出す](/images/books/digging-into-machine-learning/llm2026-e4-memory.png)

だから実務では、状態固定の安さと厳密参照の力を両取りするハイブリッドに落ち着く。Qwen3.5 は Gated DeltaNet (線形) と Grouped-Query Attention (GQA、複数の Q ヘッドで KV を共有する全結合系) と MoE を組み合わせ、Nemotron 3 は Mamba-2 を主体に一部の層だけ GQA を挟む ([報告])。2026年後半で最も踏み込んだ例が Kimi K3 で、線形の KDA と全結合系の Gated MLA を 3対1 で交互に積み、線形層の一定周期ごとに全結合層への残差経路 (AttnRes と呼ぶ機構。K3 公式 blog の記述による [報告]) を挿して、線形層で失われがちな長距離情報を全結合層へ迂回させる。線形層で $L$ に依らず状態を定数に保ちつつ、混線した遠い過去を全結合層の厳密参照で救う設計で、上で見た干渉項のトレードオフに真正面から答えた構成になっている。混合比 (3対1 など) をどこに置くかは、この状態容量と検索性能のトレードオフをアブレーションで探った結果であって、理論から一意に導かれるものではない。

### 品質保証は経験則が主

これら3系統に共通するのは、品質保証が定理ではなく経験則に大きく依存する点である。落とした重みの総和が $\varepsilon$ なら出力誤差が $\varepsilon \cdot \max\|v\|$ で抑えられるといった小さな補題はあるが、「Attention の質量が実際に少数のトークンに集中する」ことや「実効ランクが低い」ことは H2O や Quest による観測であって、事前に保証された性質ではない。圧縮率128倍や 3対1 の混合比といった具体値は、アブレーションで良かった設定を採っているにすぎない ([仮説] としての整理、観測自体は [報告])。だからこそ各社の解が分岐する。守るべき定理がないところでは、設計は経験と好みで割れるのである。

## 要素3: MoE — 容量とコストの分離

MoE は背骨の式の $P_{\text{active}}$ を下げる因子で、削減インパクトでも普遍性でも4要素中で最大だが、品質を守る強い定理がほとんどないという意味では最も経験則寄りにある。最大の動機は歴史的には decode 帯域ではなく学習コスト (同じ学習 FLOPs でより高品質) であり、本節では大バッチで帯域削減がどう希釈されるか、負荷分散をどう定式化するかという数理に絞る。

### 条件付き計算の系譜

MoE の発想は新しくない。原点は Jacobs・Jordan らが1991年に示した "Adaptive Mixtures of Local Experts" に遡る。深層学習・大規模言語モデルの文脈で「条件付き計算」(入力ごとに一部のパラメータだけを使う) として復活させたのが arXiv:1701.06538 の Shazeer らで、これが現代の MoE の結節点になった。GShard (arXiv:2006.16668) と Switch Transformers (arXiv:2101.03961) が Transformer への大規模実装を確立し、後者は top-1 ルーティングで1兆パラメータに到達した。

スケール則の側でも精緻化が進んだ。arXiv:2202.01169 はアクティブと総パラメータを含む統合スケール則を与え、疎性の収穫逓減を定量化した。arXiv:2402.07871 は granularity (エキスパートの細かさ) を第3の変数として加えた。実運用の工夫としては、DeepSeekMoE (arXiv:2401.06066) の細粒度エキスパートと共有エキスパート、そして arXiv:2408.15664 の補助損失なし負荷分散 (bias の動的調整だけで負荷を分散する手法、V3が採用) が代表的である。

### 容量とコストの分離という思想

前章の散布図で見たアクティブ比の低下 (旧世代の10%から28%が新世代で3%から7%へ) を支えるのが、「知識容量は総paramに、演算コストはactive paramに比例する」という経験則で、arXiv:2404.05405 が合成的な事実想起タスクで観測した「約2ビット/パラメータ」の知識容量が総paramを増やす動機を与える ([報告]、合成タスク観測。推論・合成能力が総paramに比例するかは別問題で未解明)。2026年後半の Kimi K3 の Stable LatentMoE はこの分離を極端まで推し進めた例で、総パラメータ 2.8T を持ちながらルーティングは 896エキスパート中16活性 + 共有エキスパートに絞る (エキスパートの活性割合 $k/E$ は 2% 弱。active パラメータの絶対値は非公開。[報告])。アクティブ比が 3%から7% の帯に収束するのは、HBM 容量と all-to-all 帯域の交点というコストモデル上の選択であって定理ではない ([仮説]。前章で引いた DeepSeek-V3 の「1トークン最大4ノード」制約が示すのは all-to-all がルーティングを縛ることまでで、具体的な帯を導く証言ではない)。

### 大バッチで帯域削減が希釈される数理

前章の背骨の式で「MoE が $P_{\text{active}}$ を下げて帯域を減らす」と述べたのは batch$=1$ の話である。大バッチのサービングでは、バッチ内のトークンが異なるエキスパートに散るため、1ステップで読み出す重みはバッチの選ぶエキスパートの和集合に広がる。トークンが独立に一様ルーティングされると仮定すると、総エキスパート $E$ 個のうち少なくとも1トークンに選ばれるものの割合の期待値は

$$
u(B) := \frac{\mathbb{E}[\lvert\text{選ばれたエキスパートの和集合}\rvert]}{E} = 1 - \left(1 - \frac{k}{E}\right)^{B}
$$

で与えられる ([定理(仮定付き)]、一様独立ルーティングの下での閉形式。実ルーティングは偏るので $u$ はこれより下)。導出は素直で、1トークンが特定のエキスパート $i$ を選ばない確率が $1 - k/E$、$B$ トークンが独立ならその $B$ 乗が「$i$ が誰にも選ばれない確率」、その余事象を全エキスパートで平均すれば上式になる。$k=8, E=256$ なら $B=1$ で 3.1%、$B=32$ で約63%、$B=128$ で約98% と、バッチとともに急速に全エキスパートへ漸近する。

![u(B) 曲線: 横軸バッチサイズ B (対数)、縦軸が和集合割合。k/E から 1 へ立ち上がり、下段に MoE と dense のトークンあたり読み出しバイトの比較 (k=8, E=256)](/images/books/digging-into-machine-learning/llm2026-e5-union.png)

ここで注意したいのは、これを「大バッチではトークンあたり読み出しバイトが増える」と読むと誤りだという点である。共有エキスパートのパラメータを $P_{\text{sh}}$、ルーティング対象の総パラメータを $P_{\text{exp}}$、1パラメータのバイト数を $b_{\text{weight}}$ とすると、$B$ トークンで読む重みを頭割りしたトークンあたり読み出しバイトは

$$
\text{Bytes/token} = \frac{\big(P_{\text{sh}} + u(B)\, P_{\text{exp}}\big)\, b_{\text{weight}}}{B}
$$

で、分母に $B$ があるので dense と同じくバッチとともに減り続ける。正しい主張は「同じ総パラメータの dense に対する MoE の帯域優位が、その総パラメータ比 $(P_{\text{sh}} + u(B)P_{\text{exp}})/(P_{\text{sh}} + P_{\text{exp}})$ で表され (共有部を無視すればおよそ $u(B)$)、これが $k/E$ から 1 へ縮む」である。大バッチで薄まるのは絶対量ではなく dense 比の優位であって、この区別を曖昧にすると誤読を生む。それでも MoE が選ばれ続けるのは、帯域削減が主動機というより、前述の学習コストと、単一 GPU に載らない規模の知識容量を分散して持てるという容量・コスト分離の方が本質だからである。

### 負荷分散という現実の難所

特定のエキスパートにトークンが集中すると (ルーティングの偏り)、そのエキスパートがボトルネックになり並列効率が落ちる。この負荷分散は割当問題として書ける。トークン $t$ をエキスパート $i$ に割り当てる指標を $x_{ti} \in \{0,1\}$、ゲートのスコアを $s_{ti}$、1エキスパートの容量を $C$ とすると

$$
\max_{x} \sum_{t,i} x_{ti}\, s_{ti} \quad \text{s.t.} \quad \sum_i x_{ti} = k,\ \ \sum_t x_{ti} \le C
$$

という容量制約付きの割当になる ($B$ トークンを各 $k$ 個ずつ配るので、実行可能なのは総容量が足りる $Bk \le EC$ のときである)。素朴な貪欲 top-$k$ は容量制約 $\sum_t x_{ti} \le C$ を無視するので偏りが出る。従来はこれを補助損失 $\mathcal{L}_{\text{aux}} = \lambda_{\text{aux}} E \sum_i f_i P_i$ (エキスパート $i$ の割当頻度 $f_i$ と平均ゲート確率 $P_i$ の積、$\lambda_{\text{aux}}$ は損失係数) で均してきたが、勾配に余計な力を混ぜるぶん品質を損なう。V3 が採った補助損失なしの手法は、各エキスパートのスコアに bias $b_i$ を足して選び、混みすぎたら下げ空いていたら上げる

$$
b_i \leftarrow b_i - \eta\, \mathrm{sign}(\text{load}_i - \overline{\text{load}})
$$

だけで分散させる ($\eta$ は更新幅、$\overline{\text{load}}$ は全エキスパートの平均負荷。[報告])。肝は、この bias $b_i$ を使うのはどのエキスパートを選ぶかという選択の段だけで、選んだ後にゲート重みを計算するときは bias を含まない元のスコアに戻す点にある。だから負荷を均す力が損失の勾配に混ざらず、品質を損なわずに分散だけを効かせられる。この $b_i$ は容量制約の双対変数 (エキスパートの利用料金) の更新と読める。混雑したエキスパートの価格を上げて需要を他へ散らす市場機構と同型で、損失勾配を汚さずに制約だけを満たす点が効いている ([仮説])。MoE は「容量とコストを分離できる」強力な枠組みだが、その代償としてこの負荷分散を運用上の難所として抱える。

## 要素4: 低ビットネイティブ化 — 学習時に制約を焼き込む

低ビット化は背骨の式の $b_{\text{weight}}$ と $b_{\text{KV}}$ を下げる因子で、単独で効くというより他の3つと掛け算で効く「全体に薄くかかる係数」である。MoE でアクティブ比を下げた重みをさらに低ビットに、圧縮した KV をさらに低ビットにと、他因子の効果を底上げする位置にある。本節では確率的丸めの不偏性と分散、層別ビット配分の注水定理、そして知識容量との緊張を数理で押さえる。

### PTQ からネイティブ学習へ

量子化の系譜は、後付け (PTQ) から学習時前提 (QAT・ネイティブ) への移行として整理できる。GPTQ (arXiv:2210.17323) は二次近似の逐次 PTQ で3から4ビットを実現し、SmoothQuant (arXiv:2211.10438) は外れ値を重みへ移して W8A8 を、AWQ (arXiv:2306.00978) は活性化を意識した重み保護を達成した。FP8 は arXiv:2209.05433 で E4M3 と E5M2 が標準化され、FP8-LM (arXiv:2310.18313) が混合精度学習で GPT-175B のメモリを39%削減した。極端な例として、重みを三値 (約1.58ビット) に制約して最初から学習する BitNet b1.58 (arXiv:2402.17764) は、事後量子化では届かない低ビット域をネイティブ学習で開拓してみせた。

2026年の潮流は、これらを「最初から低ビットで学習する」方向へ押し進めた。DeepSeek-V4 は FP4 と FP8 の混在 (エキスパートは FP4、他は FP8)、Gemma 4 は int2/int4 混在の QAT、Nemotron 3 の Super と Ultra は NVFP4 でのネイティブ事前学習を行う。NVFP4 のネイティブ学習は arXiv:2509.25149 が初めて公開実証したもので、Random Hadamard 変換と2次元ブロック量子化と確率的丸めを組み合わせ、12Bモデルを10兆トークンで学習した ([報告])。

### 確率的丸めの不偏性と、その代償

量子化を支える第一の柱が確率的丸め (stochastic rounding) の不偏性である。刻み幅 $\Delta$ の格子に丸めるとき、下の格子点に切り捨てるか上に切り上げるかを、端数に比例した確率で決める。

$$
Q(x) = \Delta\left(\left\lfloor \frac{x}{\Delta} \right\rfloor + \mathrm{Bern}(\theta)\right), \qquad \theta = \frac{x}{\Delta} - \left\lfloor \frac{x}{\Delta} \right\rfloor
$$

このとき $\mathbb{E}[Q(x)] = x$ で丸め誤差は平均ゼロ、分散は $\mathrm{Var}[Q(x)] = \Delta^2\, \theta(1-\theta) \le \Delta^2/4$ で抑えられる ([定理]、arXiv:1502.02551)。

ここで見落とされがちな緊張がある。一様入力で平均を取ると、確率的丸めの平均二乗誤差 (MSE) は $\Delta^2/6$、最近接丸めは $\Delta^2/12$ で、不偏性の代償として MSE は2倍になる。それでも学習で確率的丸めを使うのは、$n$ 回の勾配累積で不偏な誤差は $O(\sqrt{n}\,\Delta)$ にしか育たないのに対し、最近接丸めのバイアスは同符号に積もって $O(n\Delta)$ で線形に効きうるからである。1回の精度では損だが、累積で効くバイアスを消せる。「不偏だから正しい」だけでは動機の半分しか説明できない。

### 層別ビット配分は逆注水になる

第二の柱が、限られた総ビット数をどの層に厚く振るかの配分問題である。層 $l$ のパラメータ数を $n_l$、量子化感度を $w_l$、パラメータ分散を $\sigma_l^2$、割り当てビットを $b_l$ とし、パラメータあたり量子化歪みが $\propto 2^{-2b_l}$ で減ると近似すると、層の総歪みを足し合わせた

$$
\min_{\{b_l\}} \sum_l n_l\, w_l\, \sigma_l^2\, 2^{-2b_l} \quad \text{s.t.} \quad \sum_l n_l b_l = B_{\text{total}}
$$

の最小化になる。Lagrange の停留条件を解くと ([定理(仮定付き)]、歪みが $2^{-2b_l}$ で減るという近似の下での配分)、$n_l$ が目的関数と制約の両方に同じ形で入るため相殺し、各層の限界歪みを揃える等歪み条件 $w_l \sigma_l^2\, 2^{-2b_l} = \text{const}$、すなわち

$$
b_l^* = \text{const} + \frac{1}{2}\log_2\!\left(w_l\, \sigma_l^2\right)
$$

で、感度・分散の高い層ほど多くのビットを配る。ただしこの停留条件は $b_l$ が自由に動ける前提のもので、感度 $w_l \sigma_l^2$ が水位を下回る層には形式上マイナスのビット数が割り当たってしまう。実際にはビット数は非負なので $b_l = \max(0,\ \cdot)$ とクリップする (KKT 条件の非負制約)。感度の低い層から順に $b_l = 0$ に張り付き、残った予算を感度の高い層で分け合う。この「水位を下回る帯には水を張らない」クリッピングまで込みで、通信理論の注水 (water-filling) と同型になる。符号側 (レート歪み) の配分なので厳密には逆注水 (inverse water-filling) にあたる。「どの層を FP4 に振り、どの層を FP8 に残すか」という設計判断は、この配分の離散近似として読める ([仮説])。

![逆注水によるビット配分: 横軸に層を感度 w_l σ_l^2 の降順で並べ、等歪み定数の水位線までの深さが各層のビット数 b_l。インセットに確率的丸めの数直線 (2格子点と確率 θ)](/images/books/digging-into-machine-learning/llm2026-e6-waterfill.png)

### 容量の天井と STE

低ビット化はフリーランチではない。格納できる情報量は自明に「$b$ bit/param 以下」で上から抑えられる (情報理論的な上界。[定理]) のに対し、観測される知識容量は約2ビット/param [報告] だった。重みを FP4 (4ビット) にするのは4ビットの器に2ビット相当を詰めることで、余裕は2ビットしかない。int2 は器そのものが2ビットで容量の上限に張り付き余裕が皆無になり、実際 arXiv:2404.05405 は int4 でも知識容量が 2ビット/param を下回りうると報告する (ただし FP4 の E2M1 は指数を持つ非一様格子なので、「4ビットの器」という比喩は動的範囲まで含めては正確でない)。

QAT が実際に量子化に頑健な平坦解を見つけて int4 でも劣化が小さくなることは、理論というより経験的な観測である。QAT の勾配は、量子化の非微分な段差を恒等写像で近似する Straight-Through Estimator (STE、arXiv:1308.3432)

$$
\frac{\partial Q}{\partial x} \approx \mathbb{1}\big[\lvert x \rvert \le \text{clip}\big]
$$

に頼るが、その収束理論は限定的で、なぜこれほどうまくいくのかは十分に解明されていない ([仮説])。

フォーマットが未統一なことも、この領域が経験則主導であることを物語る。FP4 と FP8 の混在、MXFP4 と MXFP8、NVFP4、INT4 QAT と各社が別々の形式を採る。たとえば Kimi K3 は重みを MXFP4、活性を MXFP8 に置く QAT を SFT 段以降に適用し、感度の高い活性側にだけ広いフォーマットを割く非対称な配分を採っている ([報告])。これは上で見た逆注水が「感度の高いところに多くのビットを割く」と予言する配分そのものである。ハード (Blackwell の FP4 ネイティブ) が可能にしたものの、どの形式が標準になるかはまだ決着していない ([報告])。

なお KV-Cache 側の量子化については、本書の別チャプター「TurboQuant と PolarQuant」で情報理論の観点から詳しく扱っている。ランダム回転で分布を既知の形に押し込み、Shannon のレート歪み下界に定数倍で肉薄するという、量子化の理論的到達点を知りたい読者はそちらを参照してほしい。

## まとめ

第2章では4つの要素技術を数理まで下りて解剖した。各要素の核心を1式ずつ振り返る。

- MTP・投機デコード: $\min(p,q)+(p-q)_+ = p$ の恒等式が出力分布の厳密保存を、その全語彙和が受理率 $\alpha = 1 - \mathrm{TV}(p,q)$ を与え、期待受理長 $\tau = (1-\alpha^{\gamma+1})/(1-\alpha)$ が加速の天井 $1/(1-\alpha)$ を決める。
- Attention: $\text{Bytes}_{\text{KV}} = 2\, n_{\text{layer}} n_{kv} d_h b_{\text{KV}} L$ のどの因子を叩くかで3系統が分かれ、線形化の固定状態は $N \le d_k$ で厳密復元が頭打ちになる有限容量の連想記憶である。
- MoE: 大バッチの帯域優位は和集合 $u(B) = 1 - (1-k/E)^B$ で決まり、dense 比の優位が $k/E$ から 1 へ縮む (絶対バイトが増えるのではない)。
- 低ビット: 確率的丸めが $\mathbb{E}[Q(x)] = x$ で不偏な代わりに MSE は最近接の2倍 (一様入力平均で $\Delta^2/6$ 対 $\Delta^2/12$)、層別配分は $b_l^* = \text{const} + \frac{1}{2}\log_2(w_l\sigma_l^2)$ の逆注水になる。

この4因子は別々のモデルの話ではなく、1つのフラッグシップに同居する。本稿執筆時点で最新の Kimi K3 は、Stable LatentMoE で $P_{\text{active}}$ を、KDA と Gated MLA の 3対1 ハイブリッドで KV バイトを、MXFP4 重み + MXFP8 活性の QAT で $b_{\text{weight}}$ を同時に叩く。ただし MTP だけは、少なくとも公式 blog に言及がなく (前身 K2.5 では明示的に無効化していた)、投機デコードの定理保証を前面には出していない。DeepSeek-V4 や GLM-5 も同様に複数因子を組み合わせており、背骨の式の複数因子への同時攻撃が2026年の設計の標準になったことを示唆する。

これらを貫くのは、前章で見た品質保証の非対称性である。定理で分布まで守れるのは投機デコードだけで、残る3つは経験則と学習時の共設計に委ねられており、その定理の後ろ盾がない領域 (とりわけ Attention と低ビットフォーマット) こそ、各社の解が最も豊かに発散する競争の場になっている。

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
- DeepSeek-AI. "DeepSeek-V3 Technical Report." arXiv:2412.19437, 2024. [https://arxiv.org/abs/2412.19437](https://arxiv.org/abs/2412.19437)
- Jingyang Yuan et al. "Native Sparse Attention: Hardware-Aligned and Natively Trainable Sparse Attention." arXiv:2502.11089, 2025. [https://arxiv.org/abs/2502.11089](https://arxiv.org/abs/2502.11089)
- Rewon Child et al. "Generating Long Sequences with Sparse Transformers." arXiv:1904.10509, 2019. [https://arxiv.org/abs/1904.10509](https://arxiv.org/abs/1904.10509)
- Iz Beltagy, Matthew E. Peters, Arman Cohan. "Longformer: The Long-Document Transformer." arXiv:2004.05150, 2020. [https://arxiv.org/abs/2004.05150](https://arxiv.org/abs/2004.05150)
- Manzil Zaheer et al. "Big Bird: Transformers for Longer Sequences." arXiv:2007.14062, 2020. [https://arxiv.org/abs/2007.14062](https://arxiv.org/abs/2007.14062)
- Albert Q. Jiang et al. "Mistral 7B." arXiv:2310.06825, 2023. [https://arxiv.org/abs/2310.06825](https://arxiv.org/abs/2310.06825)
- Tri Dao et al. "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness." arXiv:2205.14135, 2022. [https://arxiv.org/abs/2205.14135](https://arxiv.org/abs/2205.14135)
- Yaniv Leviathan, Matan Kalman, Yossi Matias. "Fast Inference from Transformers via Speculative Decoding." arXiv:2211.17192, 2022. [https://arxiv.org/abs/2211.17192](https://arxiv.org/abs/2211.17192)
- Mitchell Stern, Noam Shazeer, Jakob Uszkoreit. "Blockwise Parallel Decoding for Deep Autoregressive Models." arXiv:1811.03115, 2018. [https://arxiv.org/abs/1811.03115](https://arxiv.org/abs/1811.03115)
- Charlie Chen et al. "Accelerating Large Language Model Decoding with Speculative Sampling." arXiv:2302.01318, 2023. [https://arxiv.org/abs/2302.01318](https://arxiv.org/abs/2302.01318)
- Tianle Cai et al. "Medusa: Simple LLM Inference Acceleration Framework with Multiple Decoding Heads." arXiv:2401.10774, 2024. [https://arxiv.org/abs/2401.10774](https://arxiv.org/abs/2401.10774)
- Yuhui Li et al. "EAGLE: Speculative Sampling Requires Rethinking Feature Uncertainty." arXiv:2401.15077, 2024. [https://arxiv.org/abs/2401.15077](https://arxiv.org/abs/2401.15077)
- Yuhui Li et al. "EAGLE-2: Faster Inference of Language Models with Dynamic Draft Trees." arXiv:2406.16858, 2024. [https://arxiv.org/abs/2406.16858](https://arxiv.org/abs/2406.16858)
- Fabian Gloeckle et al. "Better & Faster Large Language Models via Multi-token Prediction." arXiv:2404.19737, 2024. [https://arxiv.org/abs/2404.19737](https://arxiv.org/abs/2404.19737)
- Angelos Katharopoulos et al. "Transformers are RNNs: Fast Autoregressive Transformers with Linear Attention." arXiv:2006.16236, 2020. [https://arxiv.org/abs/2006.16236](https://arxiv.org/abs/2006.16236)
- Imanol Schlag, Kazuki Irie, Jürgen Schmidhuber. "Linear Transformers Are Secretly Fast Weight Programmers." arXiv:2102.11174, 2021. [https://arxiv.org/abs/2102.11174](https://arxiv.org/abs/2102.11174)
- Albert Gu, Tri Dao. "Mamba: Linear-Time Sequence Modeling with Selective State Spaces." arXiv:2312.00752, 2023. [https://arxiv.org/abs/2312.00752](https://arxiv.org/abs/2312.00752)
- Tri Dao, Albert Gu. "Transformers are SSMs: Generalized Models and Efficient Algorithms Through Structured State Space Duality." arXiv:2405.21060, 2024. [https://arxiv.org/abs/2405.21060](https://arxiv.org/abs/2405.21060)
- Songlin Yang et al. "Parallelizing Linear Transformers with the Delta Rule over Sequence Length." arXiv:2406.06484, 2024. [https://arxiv.org/abs/2406.06484](https://arxiv.org/abs/2406.06484)
- Songlin Yang et al. "Gated Delta Networks: Improving Mamba2 with Delta Rule." arXiv:2412.06464, 2024. [https://arxiv.org/abs/2412.06464](https://arxiv.org/abs/2412.06464)
- Bo Peng et al. "RWKV: Reinventing RNNs for the Transformer Era." arXiv:2305.13048, 2023. [https://arxiv.org/abs/2305.13048](https://arxiv.org/abs/2305.13048)
- Yutao Sun et al. "Retentive Network: A Successor to Transformer for Large Language Models." arXiv:2307.08621, 2023. [https://arxiv.org/abs/2307.08621](https://arxiv.org/abs/2307.08621)
- Songlin Yang et al. "Gated Linear Attention Transformers with Hardware-Efficient Training." arXiv:2312.06635, 2023. [https://arxiv.org/abs/2312.06635](https://arxiv.org/abs/2312.06635)
- Kimi Team. "Kimi Linear: An Expressive, Efficient Attention Architecture." arXiv:2510.26692, 2025. [https://arxiv.org/abs/2510.26692](https://arxiv.org/abs/2510.26692)
- Kimi Team. "Kimi K3" 公式 blog (2026年7月16日公開)。本稿の AttnRes に関する記述はこの blog を一次ソースとする (blog 中で arXiv:2603.15031 として言及されるが、本稿執筆時点で arXiv 本体は未確認)。
- Zhenyu Zhang et al. "H2O: Heavy-Hitter Oracle for Efficient Generative Inference of Large Language Models." arXiv:2306.14048, 2023. [https://arxiv.org/abs/2306.14048](https://arxiv.org/abs/2306.14048)
- Jiaming Tang et al. "Quest: Query-Aware Sparsity for Efficient Long-Context LLM Inference." arXiv:2406.10774, 2024. [https://arxiv.org/abs/2406.10774](https://arxiv.org/abs/2406.10774)
- Elias Frantar et al. "GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers." arXiv:2210.17323, 2022. [https://arxiv.org/abs/2210.17323](https://arxiv.org/abs/2210.17323)
- Guangxuan Xiao et al. "SmoothQuant: Accurate and Efficient Post-Training Quantization for Large Language Models." arXiv:2211.10438, 2022. [https://arxiv.org/abs/2211.10438](https://arxiv.org/abs/2211.10438)
- Ji Lin et al. "AWQ: Activation-aware Weight Quantization for LLM Compression and Acceleration." arXiv:2306.00978, 2023. [https://arxiv.org/abs/2306.00978](https://arxiv.org/abs/2306.00978)
- Paulius Micikevicius et al. "FP8 Formats for Deep Learning." arXiv:2209.05433, 2022. [https://arxiv.org/abs/2209.05433](https://arxiv.org/abs/2209.05433)
- Houwen Peng et al. "FP8-LM: Training FP8 Large Language Models." arXiv:2310.18313, 2023. [https://arxiv.org/abs/2310.18313](https://arxiv.org/abs/2310.18313)
- Suyog Gupta et al. "Deep Learning with Limited Numerical Precision." arXiv:1502.02551, 2015. [https://arxiv.org/abs/1502.02551](https://arxiv.org/abs/1502.02551)
- NVIDIA. "Pretraining Large Language Models with NVFP4." arXiv:2509.25149, 2025. [https://arxiv.org/abs/2509.25149](https://arxiv.org/abs/2509.25149)
- Shuming Ma et al. "The Era of 1-bit LLMs: All Large Language Models are in 1.58 Bits." arXiv:2402.17764, 2024. [https://arxiv.org/abs/2402.17764](https://arxiv.org/abs/2402.17764)
- Yoshua Bengio et al. "Estimating or Propagating Gradients Through Stochastic Neurons for Conditional Computation." arXiv:1308.3432, 2013. [https://arxiv.org/abs/1308.3432](https://arxiv.org/abs/1308.3432)
