---
title: "GRPO のあとで RL の目的関数は何が変わったか: 平均の取り方、基準、クリップ、比の単位、KL を論文から読む"
free: false
---

## はじめに

[DeepSeek-R1](https://arxiv.org/abs/2501.12948) が GRPO という強化学習の手法で reasoning（長い思考で問題を解く能力）を伸ばしてから、公開モデルの技術報告の多くは GRPO を土台にして、その式に手を入れてきた。GRPO（Group Relative Policy Optimization）は、1 つの問題に複数の回答を生成し、回答どうしの報酬を比べて、平均より良い回答の確率を上げ、悪い回答の確率を下げる手法である。この章で扱う変更の多くは、**GRPO の式の 5 つの部品のどれかを差し替えたものとして読める**ものである。この章では、GRPO の式を 5 つの部品に分け、どの論文がどの部品をどう変えたか、どの公開モデルがそれを採用したかを、論文と技術報告から整理する。5 つの部品は筆者の整理の枠で、変更のすべてを含む分類ではない。

合わせて、この章では問いを次の 6 つに分ける。表の右の列は、章の結論を先に並べたものである。

| 問い | 論文から読める答え |
|---|---|
| GRPO の式は何でできているか | 損失の平均の取り方、報酬の基準と正規化、クリップ、確率の比の単位、KL の 5 つの部品でできている |
| 損失の平均の取り方はなぜ変わったか | 回答ごとに長さで割る平均は、長い回答の token の重みを下げる。[Magistral](https://arxiv.org/abs/2506.10910) や [Olmo 3](https://arxiv.org/abs/2512.13961) は、グループや batch の token の総数で割る形にした。ただし正誤だけを報酬にする設定で、回答の長さで token の重みを決める方法の中では、勾配の推定に偏りが無いことと、各回答の寄与が長さに依存しないことは両立しないと[証明した論文](https://arxiv.org/abs/2607.23364)がある |
| 報酬の基準はどう変わったか | 標準偏差で割る正規化を外す、自分を除いた平均を基準にする、batch 全体で正規化する、全員の結果が同じグループを除く、の 4 つの変更がある |
| クリップはどう変わったか | 上側だけ広げる、勾配を止めずに重みだけを抑える、硬い境界をなめらかな関数に替える、の 3 つの方向に分かれた |
| 確率の比を token ごとに取るか、回答ごとに取るか | [GSPO](https://arxiv.org/abs/2507.18071) は、回答ごとに幾何平均で 1 つの比を取ると、調べた MoE の学習が Routing Replay なしで安定したと報告した。[GMPO](https://arxiv.org/abs/2507.20673) は自分の目的関数では token ごとのクリップのほうが安定したと報告している。この章で扱った資料には、比の単位だけをそろえた比較は見当たらない |
| KL はどう扱われているか | この章で扱う公開モデルでは、Magistral、GLM-4.5、Olmo 3 が KL の項を外した。[DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) は KL を残して推定量を補正し、数学のような分野では弱めるか外すほうが良かったと書いている |

読者には、方策勾配法と PPO のクリップの考え方の知識を想定する。扱う資料は 2024 年 2 月の [DeepSeekMath](https://arxiv.org/abs/2402.03300) から 2026 年 10 月までに arXiv などに出た論文と技術報告である。数値はすべて各論文の abstract か本文の報告値で、筆者はどれも再現していない。この章と次の章では、reasoning を「思考」とは訳さずにそのまま書き、学習済みのモデルで回答を生成することを「推論」と書く。訓練と推論で確率が食い違う問題と、On-Policy Distillation は次の章で扱う。この章の部品を、方策勾配の推定量の偏りと分散として読み直す話は、この本の 4 つ後の章「[数理統計から読む RL の事後学習](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-mathematical-statistics)」で扱う。

![GRPO の目的関数を 5 つの部品に分け、それぞれの部品を変えた手法を並べた。平均の取り方は DAPO と Dr. GRPO、報酬の基準は Dr. GRPO、RLOO、REINFORCE++ と、全員が同じ結果のグループを除く方法、クリップは Clip-Higher、CISPO、SAPO、比の単位は GSPO と GMPO、KL は除去と DeepSeek-V3.2 の推定量の補正](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-objectives-after-grpo/grpo-anatomy.png)

## GRPO の式と 5 つの部品

[DeepSeekMath の論文](https://arxiv.org/abs/2402.03300) の式で、GRPO の目的関数を確かめる。問題 $q$ に対して、この更新の前の方策 $\pi_{\theta_{\text{old}}}$ から $G$ 個の回答 $o_1, \dots, o_G$ を生成する。$\pi_{\theta_{\text{old}}}$ は回答を生成した方策で、学習の最初のモデルではない。この章では、回答を生成したときの確率と、訓練で計算し直した確率が一致するとみなす。一致しない場合は次の章で扱う。回答 $o_i$ の $t$ 番目の token の確率の比を $r_{i,t}(\theta)$ と書く。

$$
r_{i,t}(\theta) = \frac{\pi_\theta(o_{i,t} \mid q, o_{i,<t})}{\pi_{\theta_{\text{old}}}(o_{i,t} \mid q, o_{i,<t})}
$$

この比は、更新中の方策 $\pi_\theta$ が、回答を生成した方策と比べてその token をどれだけ出しやすくなったかを表す。GRPO の目的関数は次の形である。

$$
\mathcal{J}_{\text{GRPO}}(\theta) = \mathbb{E}\left[ \frac{1}{G} \sum_{i=1}^{G} \frac{1}{|o_i|} \sum_{t=1}^{|o_i|} \Big( \min\big( r_{i,t}(\theta) \hat{A}_{i,t},\ \operatorname{clip}(r_{i,t}(\theta), 1-\varepsilon, 1+\varepsilon) \hat{A}_{i,t} \big) - \beta\, \hat{d}^{\text{KL}}_{i,t} \Big) \right]
$$

回答の終わりに 1 つの報酬を与える場合（outcome supervision）、advantage $\hat{A}_{i,t}$（その回答が平均よりどれだけ良いかを表す値）は、グループの報酬 $R_1, \dots, R_G$ の平均を引いて標準偏差で割った値になり、同じ回答のすべての token に同じ値を配る。

$$
\hat{A}_{i,t} = \frac{R_i - \operatorname{mean}(R_1, \dots, R_G)}{\operatorname{std}(R_1, \dots, R_G)}
$$

この式は、価値を予測する別のモデル（critic、value model とも呼ぶ）を持たずに、同じ問題への回答どうしの比較で advantage を作ることを言っている。全員の報酬が同じグループでは標準偏差が 0 になり、この式はそのままでは使えない。$\hat{d}^{\text{KL}}_{i,t}$ は参照モデル $\pi_{\text{ref}}$ から離れすぎないための罰則で、DeepSeekMath は生成された token ごとに次の推定値を計算し、係数 $\beta$ を掛けて損失に足していた。

$$
\hat{d}^{\text{KL}}_{i,t} = \frac{\pi_{\text{ref}}(o_{i,t} \mid q, o_{i,<t})}{\pi_\theta(o_{i,t} \mid q, o_{i,<t})} - \log \frac{\pi_{\text{ref}}(o_{i,t} \mid q, o_{i,<t})}{\pi_\theta(o_{i,t} \mid q, o_{i,<t})} - 1
$$

この式は KL ダイバージェンスそのものではなく、prefix を固定し、その位置の token を $\pi_\theta$ からサンプルしたときに、期待値がその位置の KL になる推定値である。GRPO の式には、後の論文が手を入れた部品が 5 つある。$\frac{1}{|o_i|}$ と $\frac{1}{G}$ による平均の取り方、advantage の基準と正規化、$\operatorname{clip}$ の形、$r_{i,t}$ を token ごとに取るという比の単位、KL の項である。この章はこの順に節を立てる。

DeepSeekMath は KL の係数を $\beta = 0.04$ とした。DeepSeek-R1 の論文の現在の版（v2）は、最初の RL の段で KL の係数を 0.001、クリップの幅 $\varepsilon$ を 10 とした。論文は、クリップの幅を小さくすると多くの token の勾配が切られて性能が落ち、大きくすると学習が不安定になると書いている。同じ論文の DeepSeek-R1-Zero の学習では、400 step ごとに参照モデルを最新の方策に置き換えた。

## 平均の取り方と長さの偏り

GRPO は、回答ごとに token の損失を足して長さ $|o_i|$ で割り、それを $G$ 個の回答で平均する。この形では、どの回答も合計で同じ重みを持つので、長い回答の 1 つ 1 つの token の重みは小さくなる。

[Dr. GRPO](https://arxiv.org/abs/2503.20783) は、この割り算が回答の長さの偏りを生むと指摘した。正誤だけを報酬にする場合、advantage が正の回答は正解で、負の回答は不正解である。正解では、短い回答ほど token あたりの勾配が大きくなり、短い正解が好まれる。不正解では、長い回答ほど token あたりの罰則が弱まるので、不正解の回答が長くなる方向に学習が進む。Dr. GRPO は $\frac{1}{|o_i|}$ を定数に替え、次の節で見る標準偏差での割り算も外した。Qwen2.5-Math-7B を MATH の level 3 から 5 の問題で 8 枚の A100 で 27 時間学習し、AIME 2024 で 43.3% を得たと報告している。

[DAPO](https://arxiv.org/abs/2503.14476)（Decoupled Clip and Dynamic sAmpling Policy Optimization）は、グループのすべての回答の token の損失を足し、グループの token の総数で割る token-level の損失を使った。

$$
\mathcal{J}_{\text{DAPO}}(\theta) = \mathbb{E}\left[ \frac{1}{\sum_{i=1}^{G} |o_i|} \sum_{i=1}^{G} \sum_{t=1}^{|o_i|} \min\big( r_{i,t}(\theta) \hat{A}_{i,t},\ \operatorname{clip}(r_{i,t}(\theta), 1-\varepsilon_{\text{low}}, 1+\varepsilon_{\text{high}}) \hat{A}_{i,t} \big) \right]
$$

この式では、同じグループの中のどの token も同じ重みを持つ。長い回答は token の数が多いぶん、全体への影響も大きい。クリップの幅を上下で別の値 $\varepsilon_{\text{low}}$ と $\varepsilon_{\text{high}}$ にしている点は、クリップの節で見る。公開モデルでは、Magistral がグループの中のすべての token の損失を足して生成の長さの合計で割り、Olmo 3 が batch 全体の token の総数で割っている。[GLM-4.5 の技術報告](https://arxiv.org/abs/2508.06471) は、小さい実験用のモデルでコードの RL を比べ、すべての token を同じ重みで平均する損失が、回答ごとに平均する損失より速く収束し、長さの偏りも和らげたと書いている。この比較の曲線は GLM-4.5 そのものではない。

ただし、この方向に正解は 1 つではない。[2026 年 7 月の論文](https://arxiv.org/abs/2607.23364) は、正誤だけを報酬にする GRPO の設定で、回答の長さで決める重みのどれを選んでも、2 つの性質を同時には満たせないと証明した。1 つ目は勾配の推定に偏りが無いこと（P1）、2 つ目は各回答の勾配への寄与が長さに依存しないこと（P2）である。回答の長さを $L = |o_i|$ とし、その回答の token 1 つに掛ける重みを $f_\alpha(L) = L^{\alpha - 1}$ の族で書くと、$\alpha = 0$（重み $\frac{1}{L}$）が GRPO、$\alpha = 1$（重み 1）が Dr. GRPO にあたる。GRPO は P2 をおおよそ満たすが P1 を満たさず、Dr. GRPO は P1 を満たすが、長い回答が長さの比に比例して勾配を支配しうる。著者たちは、Dr. GRPO の「偏りが無い」という主張は勾配の推定の性質としては正しいが、長さへの依存は残ると指摘している。

長さに関わる変更には、生成の上限で打ち切られた回答の扱いもある。DAPO は Overlong Reward Shaping として、打ち切られた回答に不適切な報酬を与えると報酬のノイズになり学習が乱れると指摘し、長すぎる回答の扱いを報酬の側で整えた。これは token の重みではなく報酬の設計の変更である。

標本調査の言葉では、回答ごとに長さで割ってから平均する GRPO の形は比の平均、グループの token の総数で割る DAPO の形は比推定量、分母を定数にする Dr. GRPO の形は線形な推定量にあたる。GRPO と DAPO の形は、どちらも勾配に長さに依存する重みを掛け、DAPO の比推定量はグループを大きくした極限で正解率の勾配を長さの平均で割った量に近づく。

## 報酬の基準: 何と比べ、何で割るか

GRPO は、グループの報酬の標準偏差で advantage を割る。Dr. GRPO は、この割り算が問題の難しさによる偏りを生むと指摘した。ほとんど全員が正解する問題や、ほとんど全員が不正解の問題では標準偏差が小さく、割った後の advantage が大きくなるので、易しすぎる問題と難しすぎる問題の重みが増える。Olmo 3 はこの指摘に従い、標準偏差で割らない形を採用している。DeepSeek-V3.2 の論文も、advantage をグループの平均を引くだけの $R_i - \operatorname{mean}(R_1, \dots, R_G)$ と書いている。

基準の取り方を変える手法もある。[RLOO](https://arxiv.org/abs/2402.14740)（REINFORCE Leave-One-Out）は、同じ問題への $G$ 個の回答のうち、自分を除いた $G-1$ 個の平均報酬を基準にする。論文では回答の数を $k$ と書いている。[REINFORCE++](https://arxiv.org/abs/2501.03262) は、advantage を batch 全体の平均と標準偏差で正規化する。1 つの問題に複数の回答を生成する変種では、グループの平均を引いたあと、batch 全体の標準偏差で割る。Magistral は、グループの平均を引いた advantage を、さらに minibatch の中の平均と標準偏差で正規化している。

報酬の基準と合わせて、学習に使う問題の選び方も変わった。正誤だけを報酬にすると、グループの全員が正解、または全員が不正解の問題では、報酬から作る方策の勾配が生まれない。DAPO は Dynamic Sampling として、多めに生成して正解率が 0 と 1 の問題を除き、batch の問題の数を保つ。Magistral は全員が同じ結果のグループを batch から外し、Olmo 3 も advantage が 0 のグループを除いて、足りない分を追加の生成で埋めている。

数理統計から見ると、基準の報酬は平均 0 のスコア関数を使う制御変量で、標準偏差や長さの扱いをそろえれば、自分を含むグループの平均を引く形は、標本ごとに RLOO のちょうど $(G-1)/G$ 倍になる。偏るのは勾配の大きさだけで、既知の定数倍である。[GRPO の勾配の恒等式の論文](https://arxiv.org/abs/2607.00152) の式から、標準偏差で割る形は、正誤の報酬で正解率 $p$ を固定して回答の数を増やす極限では、$p$ の代わりに 2 項分布の分散安定化変換 $2\arcsin\sqrt{p}$ を上げる勾配になる。Dr. GRPO が指摘した難しさの偏りは、推定量の偏りではなく、どの量を上げるかという目的関数の選び方の違いと読める。導出は[数理統計の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-mathematical-statistics)にある。

## クリップ: 何を止め、何を通すか

PPO と GRPO のクリップは、同じ生成で方策を複数回更新するときに効く。この章の前提のとおり生成と訓練の確率が一致していれば、最初の更新では比 $r_{i,t}$ は 1 なので、クリップはかからない。更新を重ねて比が 1 から離れると、advantage の符号によって効き方が変わる。advantage が正の token は、比が $1+\varepsilon$ を超えると、クリップ付きの項がそれ以上増えなくなり、その項から確率を上げる勾配が出なくなる。advantage が負の token は、比が $1-\varepsilon$ を下回ると同じことが起きる。逆の向きに動いた token には、勾配が流れ続ける。クリップは確率そのものに上限を課すのではなく、advantage の向きに動きすぎた token の項を平らにする仕組みである。

![よく使われる幅 0.2 の GRPO を含め、advantage が正の token について、確率の比の軸の上で、クリップ付きの項から勾配が流れる範囲を並べた。GRPO と DAPO は上限を超えると勾配を止め、CISPO は上限で重みを固定したまま勾配を流し、SAPO は比が 1 から離れるほどなめらかに重みを下げる。温度 1 では比が 0 と 2 でも重みは約 0.79 残る](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-objectives-after-grpo/clip-shapes.png)

DAPO は、この上限が探索を妨げると考えた。論文の例では、$\varepsilon = 0.2$ のとき、元の確率が 0.01 の token は、クリップ付きの項が確率を上げる勾配を出すのが 0.012 までだが、元の確率が 0.9 の token はほとんど制限を受けない。上限が確率の低い token にだけ強く効くので、方策のエントロピー（出力の散らばり）が早く下がる。DAPO は Clip-Higher として、下限を $\varepsilon_{\text{low}} = 0.2$ のまま、上限を $\varepsilon_{\text{high}} = 0.28$ に広げた。Magistral はこの上限を学習中に 0.26 から 0.28 の間で調整し、グループのエントロピーを安定させたと書いている。GSPO の論文も、比較に使った GRPO の幅を、下側 0.2、上側 0.27 に調整している。

[CISPO](https://arxiv.org/abs/2506.13585)（Clipped Importance Sampling Policy Optimization）は MiniMax-M1 の論文で提案された手法で、クリップで勾配を止めること自体を問題にした。MiniMax-M1 の著者たちは、hybrid attention のモデルを SFT を挟まずに RL で学習する実験で、However、Recheck、Wait、Aha のような振り返りの token が、推論の分岐点になるのに確率が低く、更新で比が大きくなりやすいので、最初の更新のあとにクリップで勾配から外されると報告した。CISPO は比 $r_{i,t}$（重要度の重み）のほうをクリップし、勾配を止める演算 $\operatorname{sg}$ を掛けて、すべての token の勾配を残す。

$$
\mathcal{J}_{\text{CISPO}}(\theta) = \mathbb{E}\left[ \frac{1}{\sum_{i=1}^{G} |o_i|} \sum_{i=1}^{G} \sum_{t=1}^{|o_i|} \operatorname{sg}\big(\hat{r}_{i,t}(\theta)\big) \hat{A}_{i,t} \log \pi_\theta(o_{i,t} \mid q, o_{i,<t}) \right], \quad \hat{r}_{i,t}(\theta) = \operatorname{clip}\big(r_{i,t}(\theta), 1-\varepsilon^{\text{IS}}_{\text{low}}, 1+\varepsilon^{\text{IS}}_{\text{high}}\big)
$$

この式では、比が上限を超えた token も、重み $1+\varepsilon^{\text{IS}}_{\text{high}}$ で勾配に寄与する。著者たちは下限を実質的に設けず、上限だけを調整した。Qwen2.5-32B の base モデルの AIME 2024 の対照実験では、CISPO が DAPO と同じ性能に半分の学習 step で達し、2 倍の速さと報告している。[MiniMax-M2 の技術報告](https://arxiv.org/abs/2605.26494) も、M2 の系列の RL に CISPO を使っている。

[SAPO](https://arxiv.org/abs/2511.20347)（Soft Adaptive Policy Optimization）は、硬い境界をなめらかな関数に替えた。目的関数の $r_{i,t}$ の代わりに、比を温度 $\tau$ のシグモイドに通した $g_\tau(r_{i,t}) = \sigma(\tau(r_{i,t}-1)) \cdot \frac{4}{\tau}$ を使う。$\sigma$ はシグモイド関数である。論文が勾配の重みと呼ぶ $4\sigma(\tau(r_{i,t}-1))\big(1-\sigma(\tau(r_{i,t}-1))\big)$ は、比が 1 のときに最大の 1 を取り、1 から離れるほどなめらかに小さくなる。Qwen3-30B-A3B-Base から作ったモデルでの数学の比較では、advantage が負の token に大きい温度（$\tau_{\text{neg}} = 1.05$、正の token は $\tau_{\text{pos}} = 1.0$）を使い、負の側の分散の大きい勾配を抑えることが、学習の初期の崩壊を防ぐのに効いたと報告した。SAPO の著者たち（Qwen のチーム）は、SAPO で Qwen3-VL の系列を学習したと書いている。

クリップと同じ場所で、エントロピーを保つ工夫もある。[The Entropy Mechanism の論文](https://arxiv.org/abs/2505.22617) は、softmax の方策を方策勾配で更新すると、エントロピーの変化が token の log 確率と advantage の共分散と強く結びつくことを導いた。共分散の大きい token がエントロピーを下げると分析し、その token の勾配を外す Clip-Cov と、その token にだけ KL の罰則を掛ける KL-Cov を提案している。[Beyond the 80/20 Rule](https://arxiv.org/abs/2506.01939) は、エントロピーが上位 20% の token だけで勾配を計算した。Qwen3-8B の base モデルでは全 token の更新と同等で、Qwen3-32B の base モデルでは全 token の更新を AIME'25 で 11.04 ポイント、AIME'24 で 7.71 ポイント上回ったと報告している。

## 比の単位: token ごとか、回答ごとか

GRPO は確率の比を token ごとに取る。しかし報酬は回答全体に 1 つしか与えられない。[GSPO](https://arxiv.org/abs/2507.18071)（Group Sequence Policy Optimization）はこの食い違いを問題にし、回答全体で 1 つの比を、長さで正規化した幾何平均として定義した。

$$
s_i(\theta) = \left( \frac{\pi_\theta(o_i \mid q)}{\pi_{\theta_{\text{old}}}(o_i \mid q)} \right)^{\frac{1}{|o_i|}} = \exp\left( \frac{1}{|o_i|} \sum_{t=1}^{|o_i|} \log r_{i,t}(\theta) \right)
$$

この式は、token ごとの log の比の平均を取り、回答を 1 つの比で代表させることを言っている。GSPO はこの $s_i$ でクリップし、回答単位で勾配に入れるかどうかを決める。比が 1 の近くに集まるので、クリップの幅は下側 $3 \times 10^{-4}$、上側 $4 \times 10^{-4}$ という狭い値になる。

![1 つの回答の 6 個の token のうち 1 個だけ比が大きく外れたとき、advantage が正なら、token ごとのクリップはその token だけを外し、回答ごとの幾何平均は外れを 1.08 まで薄めるが、GSPO の狭い上限を超えるので 6 個の token がまとめて外れる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-objectives-after-grpo/token-vs-sequence-ratio.png)

GSPO の論文が強調するのは MoE（Mixture of Experts）での効果である。48 層の Qwen3-30B-A3B-Base では、1 回の勾配の更新のあと、同じ回答に対して新しい方策で使われる expert の約 10% が古い方策と違っていた。expert が変わると token ごとの比が大きく揺れるので、GRPO で MoE を収束させるには、古い方策で使った expert を記録して比の計算で再現する Routing Replay が必要だった。Qwen3-30B-A3B-Base から作ったモデルの実験で、GSPO は回答全体の尤度だけを見るので Routing Replay なしで安定に学習できたと報告している。クリップで外れた token の割合は GSPO が GRPO より 2 桁大きかったのに、学習の効率は高かった。論文は、これらの利点が最新の Qwen3 モデルの改善に寄与したと書いているが、どの版に使ったかは明記していない。

一方で、別の設定では違う結果も出ている。SAPO の論文は、同じ系統のモデルでの数学の比較で、GSPO と、Routing Replay を付けた GRPO が学習の初期に崩壊したと報告した。[GMPO](https://arxiv.org/abs/2507.20673)（Geometric-Mean Policy Optimization）は token ごとの比の幾何平均を最大化するが、クリップは token ごとに $(e^{-0.4}, e^{0.4})$ の幅で掛ける。著者たちは自分の目的関数で回答単位のクリップと比べ、回答単位のクリップは一度掛かると回答のすべての token の勾配を 0 にするので強すぎ、token 単位のほうが安定したと書いた。GMPO は DeepSeek-R1-Distill-Qwen-7B で、5 つの数学のベンチマークの平均 Pass@1 を GRPO の 59.3% から 63.4% に上げたと報告している。回答ごとの比にも長さの偏りが残るという指摘が [LUSPO](https://arxiv.org/abs/2602.05261)（Length-Unbiased Sequence Policy Optimization）から出ている。これらは比の単位だけをそろえて比べた実験ではなく、この章で扱った報告の範囲では、比の単位の選び方は決着していない。MoE の expert の食い違いには、次の章で見る、推論のときの expert を記録して訓練で再現する方法もある。

## KL: 外すか、直して残すか

この章で扱う公開モデルのうち、Magistral、GLM-4.5、Olmo 3 は reasoning の RL で KL の項を外した。DAPO は、長い思考の連鎖を学ぶ間に方策は最初のモデルから大きく離れるので、この制約は必要ないとして KL を外した。Magistral は、GRPO では方策がどのみち大きく離れ、参照モデルの複製を持って計算するコストに見合わないとして、KL の罰則を取り除いた。GLM-4.5 は GRPO から KL の損失を除いた形を使っている。Olmo 3 も、広く使われている方法として KL を外し、それでも過剰な最適化や不安定さは起きなかったと書いている。

DeepSeek-V3.2 は、GRPO と KL を残しつつ、推定量を直した。DeepSeekMath の推定値は、token が更新中の方策 $\pi_\theta$ から出たときに期待値が KL になる。しかし実際の token は、回答を生成した方策 $\pi_{\text{old}}$ から出ている。DeepSeek-V3.2 は、比 $\frac{\pi_\theta}{\pi_{\text{old}}}$ を掛けてこの違いを補正した。補正した推定値を、DeepSeekMath の $\hat{d}^{\text{KL}}_{i,t}$ と区別して $\hat{d}^{\text{KL,IS}}_{i,t}$ と書く。

$$
\hat{d}^{\text{KL,IS}}_{i,t} = \frac{\pi_\theta(o_{i,t} \mid q, o_{i,<t})}{\pi_{\text{old}}(o_{i,t} \mid q, o_{i,<t})} \left( \frac{\pi_{\text{ref}}(o_{i,t} \mid q, o_{i,<t})}{\pi_\theta(o_{i,t} \mid q, o_{i,<t})} - \log \frac{\pi_{\text{ref}}(o_{i,t} \mid q, o_{i,<t})}{\pi_\theta(o_{i,t} \mid q, o_{i,<t})} - 1 \right)
$$

DeepSeek-V3.2 の論文は、この $\pi_{\text{old}}$ を推論のフレームワークが返した確率と定義している。この章では推論と訓練の違いを無視して $\pi_{\theta_{\text{old}}}$ と同じものとして扱い、区別は次の章で行う。この補正が直すのは、固定した prefix での次の token の分布の違いで、prefix の分布の違いまでは直さない。論文によれば、元の推定量は、更新中の方策での確率が参照モデルよりずっと低い token に、上限の無い大きな重みを与え、勾配を荒らしていた。論文は、この補正でその系統的な誤差が消えると書いている。数理統計から見ると、DeepSeekMath の推定値は、更新中の方策から生成した token では KL の値の不偏推定量だが、prefix を固定した 1 つの位置で、生成した token を固定して損失として微分すると、勾配の期待値は逆向きの KL $\mathrm{KL}(\pi_{\text{ref}} \,\|\, \pi_\theta)$ の勾配になる。比 $\frac{\pi_\theta}{\pi_{\text{old}}}$ を掛けて比も含めて微分すると、足りなかったスコア関数の項が加わり、$\mathrm{KL}(\pi_\theta \,\|\, \pi_{\text{ref}})$ の勾配の不偏推定量になる。計算は[数理統計の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-mathematical-statistics)にある。DeepSeek-V3.2 は KL の強さを分野ごとに変え、数学のような分野では KL を弱くするか外すほうが性能が上がったとも書いている。

KL を参照モデルではなく直前の方策に向ける設計もある。[Kimi k1.5](https://arxiv.org/abs/2501.12599) は、online policy mirror descent の一種として、反復ごとに、直前の反復の方策 $\pi_{\theta_{\text{prev}}}$ から離れすぎない範囲で報酬の期待値を上げる目的関数を解く。目的関数は、報酬の期待値から $\pi_{\theta_{\text{prev}}}$ への KL に係数を掛けて引いた形である。この KL は、参照モデルにとどめる罰則ではなく、1 回の反復で方策を動かす幅を決める役割を持つ。

## value model を使う系統

GRPO の系統は critic を持たないが、critic を持つ PPO の系統を reasoning の RL に合わせて直す研究も続いている。[VAPO](https://arxiv.org/abs/2504.05118) は 7 つの技術を組み合わせた。たとえば、方策の学習の前に value model を事前に学習しておく、回答の長さに応じて GAE（advantage の推定法）の係数を変える、DAPO の Clip-Higher と token-level の損失を取り入れる、などである。Qwen 32B の事前学習モデルから学習し、AIME 2024 で 60.4 を報告した。論文は DeepSeek-R1-Zero-Qwen-32B の 47、DAPO の 50 と比べ、3 回の繰り返しの実験で最高値が 60 から 61 にそろったと書いている。この結果は 7 つの技術を合わせたもので、critic の有無だけを比べたものではない。

## 公開モデルでの採用

公開モデルの技術報告に書かれた RL の目的関数の変更を、モデルごとに並べる。どれも報告に書かれた段階と分野での設定で、そのモデルの事後学習のすべてに共通するとは限らない。「記載なし」は、その報告に書かれていないことを指し、使っていないことを意味しない。

| モデル | 報告の日付 | 土台の手法 | 書かれている変更 |
|---|---|---|---|
| [DeepSeek-R1](https://arxiv.org/abs/2501.12948) | 2025 年 1 月 | GRPO | 最初の RL の段で KL の係数 0.001、クリップの幅 10。R1-Zero では参照モデルを 400 step ごとに更新 |
| [Kimi k1.5](https://arxiv.org/abs/2501.12599) | 2025 年 1 月 | online policy mirror descent | KL を直前の方策に向ける |
| [Qwen3](https://arxiv.org/abs/2505.09388) | 2025 年 5 月 | GRPO | reasoning の RL で GRPO を使ったと記載。細部は記載なし |
| [Magistral](https://arxiv.org/abs/2506.10910) | 2025 年 6 月 | GRPO | KL を除去、グループの token の総数で割る、minibatch で advantage を正規化、Clip-Higher、全員同じ結果のグループを除外 |
| [MiniMax-M1](https://arxiv.org/abs/2506.13585)、[M2](https://arxiv.org/abs/2605.26494) | 2025 年 6 月、2026 年 5 月 | CISPO | 重みをクリップして全 token の勾配を残す |
| GSPO を使った Qwen3 の版 | 2025 年 7 月（GSPO の論文） | [GSPO](https://arxiv.org/abs/2507.18071) | 比を回答ごとの幾何平均にする。どの版かは記載なし |
| [GLM-4.5](https://arxiv.org/abs/2508.06471) | 2025 年 8 月 | GRPO | KL を除去。コードの RL の token-level の損失は、小さい実験用のモデルでの比較として記載 |
| Qwen3-VL | 2025 年 11 月（SAPO の論文） | [SAPO](https://arxiv.org/abs/2511.20347) | クリップをなめらかな関数に替える |
| [DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) | 2025 年 12 月 | GRPO | 標準偏差で割らない、KL の推定量を補正、KL の強さを分野ごとに変える |
| [Olmo 3](https://arxiv.org/abs/2512.13961) | 2025 年 12 月 | GRPO | KL を除去、batch の token の総数で割る、標準偏差で割らない、Clip-Higher、advantage が 0 のグループを除外 |

DeepSeek-V3.2、Olmo 3 と、次の章で扱う GLM-5、Kimi K2.5、MiMo-V2-Flash などは、この表の変更に加えて、訓練と推論の確率の食い違いを扱う仕組みを、目的関数か学習の仕組みに入れている。

## まとめ

この章で扱った GRPO のあとの手法の多くは、平均の取り方、報酬の基準、クリップ、比の単位、KL の 5 つの部品のどれかを変えたものとして読める。表に挙げた技術報告では、KL の除去、token の総数で割る平均、Clip-Higher の採用例がある一方、比の単位とクリップの形は GSPO、CISPO、SAPO のように報告ごとに選択が分かれ、同じ Qwen のチームからも GSPO と SAPO の 2 つが出ている。

## 参考: この章で扱った研究

| 論文 | 公開 | この章での役割 |
|---|---|---|
| [DeepSeekMath](https://arxiv.org/abs/2402.03300) | 2024 年 2 月 | GRPO の定義 |
| [RLOO（Back to Basics）](https://arxiv.org/abs/2402.14740) | 2024 年 2 月 | 自分を除いた平均を基準にする |
| [REINFORCE++](https://arxiv.org/abs/2501.03262) | 2025 年 1 月 | batch 全体での advantage の正規化 |
| [DeepSeek-R1](https://arxiv.org/abs/2501.12948) | 2025 年 1 月 | GRPO を使った reasoning の RL |
| [Kimi k1.5](https://arxiv.org/abs/2501.12599) | 2025 年 1 月 | 直前の方策への KL |
| [DAPO](https://arxiv.org/abs/2503.14476) | 2025 年 3 月 | token-level の損失、Clip-Higher、Dynamic Sampling、Overlong Reward Shaping、KL の除去 |
| [Dr. GRPO](https://arxiv.org/abs/2503.20783) | 2025 年 3 月 | 長さと標準偏差による偏りの指摘 |
| [VAPO](https://arxiv.org/abs/2504.05118) | 2025 年 4 月 | value model を使う系統 |
| [Qwen3](https://arxiv.org/abs/2505.09388) | 2025 年 5 月 | 公開モデルの RL |
| [The Entropy Mechanism](https://arxiv.org/abs/2505.22617) | 2025 年 5 月 | Clip-Cov と KL-Cov |
| [Beyond the 80/20 Rule](https://arxiv.org/abs/2506.01939) | 2025 年 6 月 | 高エントロピーの token だけの更新 |
| [Magistral](https://arxiv.org/abs/2506.10910) | 2025 年 6 月 | 公開モデルの GRPO の変更点 |
| [MiniMax-M1](https://arxiv.org/abs/2506.13585) | 2025 年 6 月 | CISPO |
| [GSPO](https://arxiv.org/abs/2507.18071) | 2025 年 7 月 | 回答ごとの比と MoE の安定化 |
| [GMPO](https://arxiv.org/abs/2507.20673) | 2025 年 7 月 | token の比の幾何平均と token ごとのクリップ |
| [GLM-4.5](https://arxiv.org/abs/2508.06471) | 2025 年 8 月 | 公開モデルの KL の除去と token-level の損失の比較 |
| [SAPO](https://arxiv.org/abs/2511.20347) | 2025 年 11 月 | なめらかなクリップ |
| [DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) | 2025 年 12 月 | KL の推定量の補正 |
| [Olmo 3](https://arxiv.org/abs/2512.13961) | 2025 年 12 月 | 公開モデルの GRPO の変更点 |
| [LUSPO](https://arxiv.org/abs/2602.05261) | 2026 年 2 月 | 回答ごとの比に残る長さの偏り |
| [MiniMax-M2](https://arxiv.org/abs/2605.26494) | 2026 年 5 月 | CISPO の継続 |
| [On the Impossibility of Unbiased and Length-Invariant Policy Optimization](https://arxiv.org/abs/2607.23364) | 2026 年 7 月 | 長さで決める重みの中で、勾配の偏りの無さと長さの不変性が両立しないことの証明 |
