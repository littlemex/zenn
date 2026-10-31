---
title: "数理統計から読む RL の事後学習: 推定量、重要度の重み、標本の配分、評価"
free: false
---

## はじめに

この本の直前の 4 つの章「GRPO のあとで RL の目的関数は何が変わったか」「訓練と推論のずれと On-Policy Distillation」「2026 年後半の公開モデルは RL の事後学習で何をしているか」「RL の事後学習のこれから」（以下、1 章から 4 章）では、GRPO の式の部品と、その周りの工夫を、論文と技術報告から整理した。この章では、同じ工夫を数理統計の言葉で読み直す。**GRPO とその後の手法の部品は、方策勾配の推定量の偏りと分散を変えるものと、推定する目的関数そのものや問題の間の重みを変えるものに分けて読める**。前者の例は、基準の報酬（制御変量）、重要度の重みのクリップ（winsorize と trimming）、語彙全体の蒸留（各位置のラオ・ブラックウェル化）である。後者の例は、標準偏差での割り算（回答の数の多い極限での分散安定化変換）と、長さでの割り算（長さに依存する重みで、固定した目的関数の勾配になるとは限らない）である。問題ごとの回答の数の配り方（ネイマン配分）と評価の数字の読み方（一様最小分散不偏推定量、UMVUE）も、同じ言葉で扱える。

合わせて、この章では問いを次の 9 つに分ける。表の右の列は、章の結論を先に並べたものである。

| 問い | 数理統計から読める答え |
|---|---|
| 方策勾配は何を推定しているのか | 報酬の期待値の勾配を、スコア関数（対数尤度の勾配）と報酬の積の標本平均で推定している。基準の報酬を引くのは、平均 0 の制御変量を足すことにあたる |
| 自分を含むグループの平均を引くと偏るのか | 標準偏差や長さの処理を同じにそろえれば、偏るのは大きさだけで、期待値は不偏な推定量の $(G-1)/G$ 倍になる。自分を除いた平均を引く RLOO は、この形を $G/(G-1)$ 倍したものである |
| 標準偏差で割ると何が変わるのか | 推定量の分散だけでなく、勾配の期待値が変わる。長さの扱いとクリップを除き、正誤の報酬で、正解率 $p$ を固定して回答の数を増やす極限では、$p$ ではなく 2 項分布の分散安定化変換 $2\arcsin\sqrt{p}$ を上げる勾配になる。有限の回答の数では、問題ごとの重みはこの極限より両端で小さい |
| 長さで割る平均の違いは何か | 回答ごとに長さで割ってから平均する GRPO は比の平均、グループの token の総数で割る DAPO は比推定量にあたり、どちらも勾配に長さに依存する重みを掛ける |
| 回答単位の重要度の重みはなぜ扱いにくいのか | 重みの分散は $\chi^2$ ダイバージェンスで、固定長 $L$ の回答の各位置で条件付きの $\chi^2$ が一様に $\kappa$ 以上なら、重みの 2 乗の期待値は $(1+\kappa)^L$ 以上になる。母集団の 2 次の moment から決まる有効標本数の目安は、長い回答では 1 を下回りうる |
| クリップと masking は統計では何にあたるか | 重みを上限で切る TIS と CISPO は winsorize、判定の範囲の外の token を外す IcePop などは trimming にあたる。回答単位の masking の多くは、token ごとの KL の推定値の回答に沿った平均の閾値判定である |
| KL の推定量は何が正しく、何が正しくないのか | DeepSeekMath の推定値は、更新中の方策から生成した token では KL の値の不偏推定量だが、損失として微分すると、逆向きの KL の勾配の不偏推定量になる。DeepSeek-V3.2 が掛けた重要度の比を微分すると、足りなかった項が補われる |
| 語彙全体の蒸留はなぜ分散が小さいのか | 1 つの位置の項では、生成した token だけを使う OPD の推定量の、prefix を条件とした条件付き期待値になり、分散は大きくならない。位置をまたいだ和の分散まではこの定理だけでは保証されない |
| 評価の数字はどう読むべきか | pass@k の標準的な推定量は UMVUE である。ベンチマークの平均の誤差には、問題の間のばらつきと、問題の中の試行のばらつきの 2 つの成分がある。ラベル無しの RL の最多票は、答えの分布の最頻値が 1 つに決まるなら、回答を増やすほど、正しいかどうかにかかわらずその最頻値にほとんど確実に一致する |

読者には、統計検定 1 級の程度の数理統計の知識を想定する。具体的には、尤度とスコア関数、Fisher 情報量、十分統計量と完備性、ラオ・ブラックウェルの定理とレーマン・シェフェの定理、デルタ法、層化抽出とネイマン配分、重点抽出法である。強化学習の用語は 1 章から 4 章で導入したものを使う。この章の式の導出のうち、出典を書いていないものは筆者が教科書的な計算で示したもので、どれも筆者が学習の実験で確かめたものではない。引用する論文の数値は各論文の報告値で、2026 年 10 月 30 日に確かめた。

![統計の 10 個の考え方と、それが RL の事後学習のどの部品に現れるかを対応させた模式図。制御変量はグループの平均の基準、分散安定化変換は標準偏差での割り算、比推定量と比の平均は token と回答の平均の取り方、χ² ダイバージェンスと有効標本数は回答単位の重み、winsorize と trimming はクリップと masking、KL の推定量は k3、ラオ・ブラックウェルは各位置の語彙全体の OPD、ネイマン配分は問題ごとの回答の数、UMVUE と同時信頼帯と FDR は評価、陪審定理は多数決のラベルに対応する](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mathematical-statistics/stats-map.png)

## 記号と前提

1 つの問題 $q$ に対し、方策 $\pi_\theta$ から回答 $o$ を生成し、正誤の報酬 $R(o) \in \{0, 1\}$ を得るとする。問題 $q$ の正解率を $p_\theta(q) = \mathbb{E}_{o \sim \pi_\theta(\cdot \mid q)}[R(o)]$ と書き、問題を固定した文脈では $p$ と略す。学習の目的を問題の分布についての平均 $J(\theta) = \mathbb{E}_q[p_\theta(q)]$ とする。回答のスコア関数を $s(o) = \nabla_\theta \log \pi_\theta(o \mid q)$ と書く。回答の確率は token の条件付き確率の積なので、スコア関数は token ごとのスコアの和 $s(o) = \sum_t \nabla_\theta \log \pi_\theta(o_t \mid q, o_{<t})$ になる。

以下では、問題の分布と報酬は $\theta$ によらず、終了の token を含む回答の分布について微分と和を交換でき、スコア関数の 2 次の moment が有限であると仮定する。1 章と同じく、この章の前半では、回答を生成した方策と勾配を計算する方策が一致するとみなし、両者がずれる場合は重要度の重みの節で扱う。報酬は正誤の 2 値とするが、2 値に限らない結果はその都度そう書く。

## 方策勾配はスコア関数を使った不偏推定量

統計の言葉では、方策勾配は、パラメータを持つ分布 $\pi_\theta$ のもとでの報酬の期待値を、パラメータで微分した量である。上の仮定のもとで、次の恒等式が成り立つ。

$$
\nabla_\theta\, p_\theta(q) = \sum_o R(o)\, \nabla_\theta \pi_\theta(o \mid q) = \mathbb{E}_{o \sim \pi_\theta}\big[ R(o)\, s(o) \big]
$$

この式は、回答を 1 つ生成して $R(o)\, s(o)$ を計算すれば、勾配の不偏推定量になることを言っている。REINFORCE はこの推定量である。スコア関数の期待値は 0 になる（$\mathbb{E}[s(o)] = \nabla_\theta \sum_o \pi_\theta(o \mid q) = 0$）。これは最尤推定の教科書でスコア関数の期待値が 0 になるのと同じ式で、スコア関数の分散共分散行列 $\mathbb{E}[s s^\top]$ は Fisher 情報量の行列である。

スコア関数の期待値が 0 なので、問題 $q$ には依存してよいが回答 $o$ にはよらない数 $\lambda$ について、$(R(o) - \lambda)\, s(o)$ も不偏推定量になる。これは平均が分かっている確率変数 $s$ を使う制御変量法そのもので、$\lambda$ を基準の報酬（baseline）と呼ぶ。勾配の成分 $j$ ごとに $\lambda$ を選べるとすると、$\mathbb{E}[s_j^2] > 0$ の成分で分散を最小にする $\lambda$ は次の値になる。

$$
\operatorname{Var}\big((R - \lambda)\, s_j\big) = \mathbb{E}\big[(R - \lambda)^2 s_j^2\big] - (\nabla_j p)^2 \quad \Longrightarrow \quad \lambda^*_j = \frac{\mathbb{E}[s_j^2 R]}{\mathbb{E}[s_j^2]}
$$

この最適な基準は $s_j^2$ で重みを付けた報酬の平均で、報酬の平均 $p$ とは一般には一致しない。$s_j^2$ と $R$ が無相関に近いときに $\lambda^*_j \approx p$ となり、報酬の平均を基準にする実務の形が近似として正当化される。制御変量の言葉で言えば、最適な係数は回帰係数にあたり、分散は、もとの推定量の分散に、1 から推定量と制御変量の相関係数の 2 乗を引いた値を掛けたものまで下がる。方策勾配の基準と制御変量による分散の削減は、[Greensmith らの 2004 年の論文](https://jmlr.org/papers/v5/greensmith04a.html) が体系的に扱っている。

## グループの平均を引く: RLOO と GRPO の違いは定数倍

GRPO は、同じ問題への $G$ 個（$G \ge 2$）の回答 $o_1, \dots, o_G$ を独立に生成し、グループの報酬の平均を基準にする。この節では、標準偏差や長さで割る前の、回答単位の形 $\frac{1}{G}\sum_i (R_i - \bar{R})\, s(o_i)$ を考える。$\bar{R}$ は自分の報酬 $R_i$ を含むので、基準が回答 $o_i$ によらないという前の節の条件を満たさない。[RLOO](https://arxiv.org/abs/2402.14740) は、自分を除いた $G-1$ 個の平均を基準にする。

$$
\hat{A}^{\text{RLOO}}_i = R_i - \frac{1}{G-1} \sum_{j \ne i} R_j, \qquad R_i - \bar{R} = \frac{G-1}{G}\, \hat{A}^{\text{RLOO}}_i
$$

RLOO の基準は $o_i$ と独立な回答だけで作られるので、$\frac{1}{G} \sum_i \hat{A}^{\text{RLOO}}_i\, s(o_i)$ は勾配の不偏推定量である。右の恒等式は標本ごとに成り立つので、自分を含む平均を引く形の期待値は、ちょうど $\frac{G-1}{G} \nabla p$ になる。偏りは既知の定数倍だけで、$G = 16$ なら 16 分の 15 で、学習率に吸収できる。1 章で「自分を除いた平均を基準にする」変更として並べた RLOO と、平均を引くだけの形は、標準偏差や長さの扱いをそろえれば、標本ごとに比例する。ただし、Dr. GRPO や DAPO のように長さの扱いも変える手法の全体の更新が比例するわけではない。

グループの全員の報酬が同じとき、$R_i - \bar{R}$ はすべての回答で 0 になる。問題の正解率を $p$ とすると、$G$ 個の回答が全員同じ結果になる確率は $p^G + (1-p)^G$ で、$G = 16$ のとき $p = 0.05$ で 0.44、$p = 0.1$ で 0.19、$p = 0.5$ で約 $3 \times 10^{-5}$ である。正解率 0.05 の問題では、その問題で作るグループの約 44% が勾配を生まない。生成済みの batch からこのグループを除いても、勾配の和の分子は変わらない。一方、DAPO の Dynamic Sampling のように、除いた分を追加の生成で埋めて batch の大きさを保つと、問題 $q$ が batch に残る確率は $1 - p_q^G - (1-p_q)^G$ に比例して変わる。ただし、捨てたグループの勾配は 0 なので、残ったグループで平均した勾配の期待値は、問題を独立に選ぶ理想化のもとで $\mathbb{E}_q[\mathbb{E}[g_q]] / \mathbb{E}_q[a_q]$（$a_q$ はこの採用の確率）となる。採用の確率は分母で全体の倍率を変えるだけで、問題の間の相対的な重みは変えない。変わるのは全体の倍率と、追加の生成の費用である。1 回でも正解を観測できる確率 $1 - (1-p)^G$ は、$G$ を標本の大きさとする検出の確率と読める。

## 標準偏差で割る: 回答の数の多い極限では逆正弦の目的になる

GRPO は、平均を引いた報酬をグループの標準偏差で割る。正誤の報酬で、$G$ 個のうち $G_+$ 個が正解（$0 < G_+ < G$）のとき、[2026 年 6 月の論文](https://arxiv.org/abs/2607.00152) は、1 つの問題の GRPO の勾配が標本ごとに次の形に書けることを示した。$\bar{s}_+$ と $\bar{s}_-$ は、正解の回答と不正解の回答のスコア関数の平均である。

$$
g_{\text{GRPO}} = \frac{1}{G} \sum_{i=1}^{G} \frac{R_i - \bar{R}}{\hat{\sigma}}\, s(o_i) = \hat{\sigma}\, (\bar{s}_+ - \bar{s}_-), \qquad \hat{\sigma} = \frac{\sqrt{G_+(G-G_+)}}{G}
$$

ここで $\hat{\sigma}$ は自由度で割らない標本標準偏差で、全員が同じ結果のグループでは $\hat{\sigma} = 0$、勾配も 0 とする。標準偏差で割らない形の勾配は、同じ計算で $\hat{\sigma}^2 (\bar{s}_+ - \bar{s}_-)$ になる。長さの扱いとクリップを除けば、2 つの形の違いは問題ごとに掛かる係数が $\hat{\sigma}$ か $\hat{\sigma}^2$ かの違いで、DAPO の Dynamic Sampling は $\hat{\sigma} = 0$ のグループを除く操作である。

次に、この係数が期待値の上で何を意味するかを見る。スコア関数の期待値が 0 で $\mathbb{E}[R s] = \nabla p$ なので、正解と不正解のそれぞれを条件とするスコア関数の期待値は $\mathbb{E}[s \mid R=1] = \nabla p / p$ と $\mathbb{E}[s \mid R=0] = -\nabla p / (1-p)$ になる（$0 < p < 1$）。$G_+$ を条件とすると、正解の回答と不正解の回答はそれぞれの条件付き分布から独立に出るので、$\mathbb{E}[\bar{s}_+ - \bar{s}_- \mid G_+] = \nabla p / (p(1-p))$ である。$G_+$ についても期待値を取ると、有限の $G$ で次の式が成り立つ。

$$
\mathbb{E}[g_{\text{GRPO}}] = \frac{\mathbb{E}[\hat{\sigma}]}{p(1-p)}\, \nabla p, \qquad \mathbb{E}\big[\hat{\sigma}^2 (\bar{s}_+ - \bar{s}_-)\big] = \frac{G-1}{G}\, \nabla p
$$

右の式は前の節の $(G-1)/G$ と同じものである。正解率 $p$ を固定して $G \to \infty$ とすると $\mathbb{E}[\hat{\sigma}] \to \sqrt{p(1-p)}$ なので、GRPO の勾配の期待値は次の形に近づく。

$$
\mathbb{E}[g_{\text{GRPO}}] \to \frac{\nabla p}{\sqrt{p(1-p)}} = \nabla\big(2 \arcsin \sqrt{p}\big)
$$

この式は、回答の数が多い極限で、平均を引くだけの形が正解率 $p$ そのものを上げ、GRPO が $2\arcsin\sqrt{p}$ を上げることを言っている。$\arcsin\sqrt{p}$ は 2 項分布の比率の分散安定化変換として知られる関数で、デルタ法で $\operatorname{Var}(\arcsin\sqrt{\hat{p}}) \approx \frac{1}{4n}$ と $p$ によらない分散になる。[Thrampoulidis らの論文](https://arxiv.org/abs/2510.23049) は、GRPO を（クリップを除いて）この変換を報酬にした RLOO として読む見方を示し、2607.00152 の論文は、有限の $G$ での 1 回の更新の勾配を上の恒等式で厳密に書いた。有限の $G$ では係数は極限と違い、筆者の計算では $G = 16$ のとき、正解率 0.05 での係数 $\mathbb{E}[\hat{\sigma}]/(p(1-p))$ は 3.27 で、極限の 4.59 より小さい。正解率 0.5 では 1.93 で、極限の 2 に近い。$G = 2$ では $\mathbb{E}[\hat{\sigma}] = p(1-p)$ なので、GRPO の勾配の期待値は $\nabla p$ そのものになる。

統計の言葉では、極限の係数 $1/\sqrt{p(1-p)}$ はベルヌーイ分布の Fisher 情報量 $I(p) = 1/(p(1-p))$ の平方根である。$2\arcsin\sqrt{p}$ は、ベルヌーイ分布の族の上で Fisher 情報量を計量とする距離（Fisher-Rao 距離）で $p = 0$ から測った長さにあたる。回答の数の多い極限の GRPO は、正解率の進み方を、確率の差ではなく、標本から見分けられる度合いの単位で測っていると読める。

![正誤の報酬で、正解率を固定して回答の数を増やす極限での、1 つの問題の正解率の勾配に掛かる重みを、正解率 0.5 での値を 1 として並べた計算図。Dr. GRPO はどの正解率でも 1、GRPO は正解率 0.05 と 0.95 で 2.29、正解率の log を目的にすると 0.05 で 10 になる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mathematical-statistics/objective-weights.png)

この見方では、1 章で見た Dr. GRPO の「難しさの偏り」は推定量の偏りではなく、目的関数の選び方の違いになる。どの変換も単調増加なので、1 つの問題の中での最適な方策は同じだが、問題をまたいだ重みが変わる。

| 手法 | 回答の数の多い極限で上げる量 $f(p)$ | $\nabla p$ に掛かる重み $f'(p)$ | $p = 0.05$ と $p = 0.5$ の重みの比 |
|---|---|---|---|
| Dr. GRPO、平均を引くだけの形 | $p$ | $1$ | 1 |
| GRPO | $2\arcsin\sqrt{p}$ | $1/\sqrt{p(1-p)}$ | 約 2.3（$G = 16$ では約 1.7） |
| [Reinforce-Ada](https://arxiv.org/abs/2510.04996) が導出に使った目的 | $\log p$ | $1/p$ | 10 |

この表の中で、GRPO は正解率が 0 に近い問題と 1 に近い問題の両方を重く扱い、log の目的は難しい問題だけを重く扱う。2607.00152 の論文は、正解率 5% と 95% の問題の重みが約 4.6 で、半々の問題の 2 倍を超えると書き、難しさの偏りを分散安定化変換を使うことの代価と述べている。

REINFORCE++ や Magistral のように batch 全体の標準偏差で割る形は、観測した batch の中ではすべての問題に同じ 1 つの数を掛けるので、その batch の中の重みを変えない。その数は batch の中身と相関するので、期待値の上で元の勾配の定数倍になるとは限らないが、batch が大きく標準偏差がほぼ一定なら、学習率を batch ごとに調整する働きとして近似できる。1 章の分類で「報酬の基準」の変更として並べたもののうち、問題の間の重みを変えるのは、グループの標準偏差で割るかどうかである。

## 長さで割る: 比の平均と比推定量

1 章の平均の取り方の違いも、標本調査の言葉で整理できる。outcome の報酬では、回答 $o_i$ のすべての token に同じ advantage $\hat{A}_i$ を配るので、回答の token の勾配の和は $\hat{A}_i\, s(o_i)$ になる。回答の長さを $L_i = |o_i|$ と書き、$\hat{A}_i = R_i - \bar{R}$ とすると、3 つの平均の取り方は次の形である。

$$
\underbrace{\frac{1}{G} \sum_{i} \frac{\hat{A}_i\, s(o_i)}{L_i}}_{\text{GRPO}}, \qquad \underbrace{\frac{\sum_i \hat{A}_i\, s(o_i)}{\sum_i L_i}}_{\text{DAPO}}, \qquad \underbrace{\frac{1}{G \cdot L_{\max}} \sum_i \hat{A}_i\, s(o_i)}_{\text{Dr. GRPO}}
$$

GRPO の形は、回答ごとの比 $\hat{A}_i s(o_i) / L_i$ の平均（比の平均）である。分母の $L_i$ が回答ごとに違い、正解かどうかとも相関しうるので、期待値は $\nabla p$ の定数倍にならず、長さに依存する重みが勾配に入る。DAPO の形は、分子の和と分母の和の比を取る比推定量である。長さが正で必要な moment が有限なら、$p$ を固定して $G \to \infty$ とすると $\nabla p / \mathbb{E}[L]$ に収束する。有限の $G$ では、同じグループから作る基準と、確率的な分母の両方が期待値をずらす。Dr. GRPO は分母を設計で決めた定数 $L_{\max}$ にするので線形な推定量になり、期待値は $\frac{G-1}{G L_{\max}} \nabla p$ と、$\nabla p$ の既知の定数倍になる。上の DAPO の形は、1 問のグループの中で損失の和を token の総数で割るものである。多くの実装の既定のように、複数の問題をまとめた batch 全体で token の平均を取ると、各問題のグループの中の平均に、その問題で生成した token の総数に比例する重みが付く。グループの中の平均を、さらに問題ごとに等しい重みで平均すれば、この長さによる問題の間の重みを除ける。この違いに対応する実装の比較として、4 章で見た [Mercor と SkyRL の報告](https://www.mercor.com/blog/training-frontier-knowledge-work-agents-a-397b-rl-training-guide-with-skyrl/) は、回答が 2k から 128k token に散らばる agent の課題で、batch 全体の token の平均から問題ごとに等しく重みを付ける形に替えて、3.9 ポイント上がったと書いている。

1 章で見た [2026 年 7 月の論文](https://arxiv.org/abs/2607.23364) の不可能性の定理は、回答の長さで決まる重みの範囲では、勾配の推定に偏りが無いこと（Dr. GRPO の側）と、各回答の寄与が長さによらないこと（GRPO がおおよそ満たす側）を同時には満たせないと示した。3 つの形の間の選択が釣り合いの問題であることを、定理として述べたものと読める。

## 重要度の重みの分散: χ² ダイバージェンスと有効標本数

2 章と 3 章のずれの扱いは、重点抽出法の推定量の性質で読める。推論側の確率 $\mu$ から得た標本で、訓練側の方策 $\pi$ のもとでの期待値 $\mathbb{E}_\pi[\psi]$（$\psi$ は推定したい量）を推定するには、重み $w = \pi / \mu$ を掛ければよい。$\pi$ が確率を置く回答には $\mu$ も確率を置く（$\pi \ll \mu$）とすると、$\mathbb{E}_\mu[w \psi] = \mathbb{E}_\pi[\psi]$ で、重みの平均は 1、分散は次の値になる。

$$
\operatorname{Var}_\mu(w) = \mathbb{E}_\mu[w^2] - 1 = \chi^2(\pi \,\|\, \mu)
$$

この分散が重点抽出法の効率の目安になる。Kong（1992）の技術報告以来、$n$ 個の標本の有効標本数の目安として $n / \mathbb{E}_\mu[w^2] = n / (1 + \chi^2)$ が使われる。これは母集団の 2 次の moment から決まる量で、1 を下回ることもある。観測した重みから計算する標本の有効標本数 $(\sum_i w_i)^2 / \sum_i w_i^2$ は、重みの和が正なら 1 以上 $n$ 以下である。母集団の目安が小さいことは、重みの大きいまれな回答を 1 本も引かないまま、標本の上ではそろって見えることも含めて、推定がその回答に左右されやすいことを意味する。推定の精度は $\psi$ にもよるので、重みの分散だけで決まるわけではない。

回答全体の重みは、token ごとの比の積 $w(o) = \prod_{t=1}^{L} \rho_t$ である。ここで $\rho_t$ は 2 章と同じ訓練側と推論側の確率の比で、回答の長さは固定の $L$ とする。各位置で、$\mu$ のもとで到達しうるどの prefix でも、次の token の分布の $\chi^2$ が共通の正の定数 $\kappa$ 以上なら、条件付き期待値を後ろの位置から順に取ることで、独立を仮定しなくても次の不等式が成り立つ。

$$
\mathbb{E}_\mu\Big[\prod_{t=1}^{L} \rho_t^2\Big] = \mathbb{E}_\mu\Big[\prod_{t=1}^{L-1} \rho_t^2 \cdot \mathbb{E}_\mu[\rho_L^2 \mid o_{<L}]\Big] \ge (1+\kappa)\, \mathbb{E}_\mu\Big[\prod_{t=1}^{L-1} \rho_t^2\Big] \ge \dots \ge (1+\kappa)^L
$$

この式は、1 つの token のずれが小さくても、回答が長いと重みの 2 乗の期待値が指数的に大きくなることを言っている。条件は prefix ごとの一様な下限で、位置ごとの平均の $\chi^2$ が分かるだけではこの証明は使えない。長さが回答ごとに違う場合も、そのままでは当てはまらない。

![回答の長さと、各位置の条件付き χ² の一様な下限の組み合わせで、16 個の回答の母集団の有効標本数の目安の上限を計算した表。下限が 0.001 なら、100 token では 14.5、1,000 token では 5.9、10,000 token では 0.001 を下回る](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mathematical-statistics/is-ess.png)

下限 $\kappa$ が $10^{-3}$ でも、長さ 10,000 token の回答では $\mathbb{E}_\mu[w^2] \ge (1.001)^{10000} \approx 2.19 \times 10^4$ となり、16 個の回答の有効標本数の目安は 0.001 を下回る。$L$ が $\ln n / \ln(1+\kappa)$ を超えれば、$n$ 個の回答の目安は必ず 1 を下回る。$n = 16$、$\kappa = 10^{-3}$ なら約 2,770 token で、これは十分条件なので、実際の 2 次の moment が下限より大きければ、もっと短い回答でも下回りうる。回答単位の重みをそのまま掛けにくい理由の 1 つはここにある。1 章の token ごとの比は、その位置の比だけを使い、prefix の分布の違いを補正しない。分散を抑える代わりに偏りを受け入れた推定量である。

回答単位の比を使う手法は、重要度の重みではなく、log の比の平均を使っている。1 章で $s_i$ と書いた GSPO の回答の比を、この章ではスコア関数と区別して $\bar{r}_i = \exp\big(\frac{1}{|o_i|}\sum_t \log r_{i,t}\big)$ と書く。$r_{i,t}$ は 1 章の更新の比である。これは幾何平均で、期待値は 1 にならない。推論側の確率から token を生成したとき、各位置で $\mathbb{E}_\mu[\log \rho_t \mid o_{<t}] = -\mathrm{KL}(\mu \,\|\, \pi)$ なので、log の比の平均は、回答に沿った token ごとの KL の推定値の平均の符号を変えたものになる。2 章の DeepSeek-V3.2 の回答単位の masking（advantage が負の回答に限って外す）も、3 章の MIS-PO の幾何平均の判定も、この推定値による閾値判定として読める。ただし、誤判定の確率を決めた統計的な検定ではない。

比が 1 に近いときは、これらの量の違いは定数倍だけになる。$\rho = 1 + \varepsilon$ と書くと、$\mathbb{E}_\mu[\varepsilon] = 0$ なので、2 次までの展開で次の関係が成り立つ。

$$
\mathrm{KL}(\mu \,\|\, \pi) = -\mathbb{E}_\mu[\log \rho] \approx \tfrac{1}{2}\, \mathbb{E}_\mu[\varepsilon^2], \qquad \mathbb{E}_\mu[(\log \rho)^2] \approx \mathbb{E}_\mu[\varepsilon^2] = \chi^2(\pi \,\|\, \mu)
$$

[M2PO](https://arxiv.org/abs/2510.01161) は、log の比の 2 乗の平均 $M_2$ が分散に敏感で、token ごとの KL の推定値のように正と負が打ち消し合わないと書いている。上の展開は、比が 1 の近くでは $M_2$ が $\chi^2$、つまり重みの分散に一致することを示し、論文のこの説明と同じ方向を指す。

## winsorize と trimming: クリップと masking の違い

重みが大きすぎる標本の扱いは、頑健統計の 2 つの操作に分かれる。値を上限で切って残すのが winsorize、判定の範囲の外の標本を捨てるのが trimming である。下の表の $\mathcal{S}$ は、各手法が token を残す判定の集合で、IcePop では比 $w$ の帯、KPop と IPO では訓練側と推論側の確率の組で決まる集合、MiMo-V2.6 では advantage の符号ごとの比の帯である。

| 操作 | 手法 | 推定量 | 偏りの源 |
|---|---|---|---|
| winsorize | TIS（$\min(\rho, C)$）、CISPO（比のクリップ、勾配は残す） | $\mathbb{E}_\mu[\min(w, C)\, \psi]$ | 上限を超えた重みの切られた分 $\mathbb{E}_\mu[(w - C)_+ \psi]$ |
| trimming | IcePop、MiMo-V2.6 の 4 つの境界、KPop、IPO | $\mathbb{E}_\mu[w \psi\, \mathbb{1}\{\mathcal{S}\}] = \mathbb{E}_\pi[\psi\, \mathbb{1}\{\mathcal{S}\}]$ | 判定の外の寄与 $\mathbb{E}_\pi[\psi\, \mathbb{1}\{\mathcal{S}^c\}]$ |

重みを上限で切る推定量は、重点抽出法の文献では [Ionides（2008）](https://doi.org/10.1198/106186008X320456) の truncated importance sampling として調べられていて、重みの分散は $C^2$ で抑えられる。trimming の推定量は、右の等式のとおり、訓練側の方策のもとで判定に入る部分だけを推定する。偏りは判定の外の寄与で決まり、$\|\psi\| \le B$ なら $B$ と判定の外の確率の積で抑えられる。外す token がまれでも、その token の $\psi$（勾配ではスコア関数）が大きければ偏りは小さくない。PPO のクリップはどちらとも違い、推定量の補正ではなく、クリップしない代理の目的関数の下界、つまり悲観的な見積もりを取る操作である。

3 章の KPop と IPO は、判定の量を 2 つの確率分布の距離として読める。生成した token とそれ以外の 2 つに語彙を分けると、3 章の記号で、生成した token の訓練側の確率 $a$ と推論側の確率 $b$ は、2 つのベルヌーイ分布を定める。KPop の量 $D^{B}_{\text{KL}}$ はこの 2 つのベルヌーイ分布の KL で、IPO の $|a - b|$ はその全変動距離である。Pinsker の不等式 $|a - b| \le \sqrt{D^{B}_{\text{KL}}/2}$ から、KPop が閾値 $\phi$ で残す token は、IPO の閾値を $\sqrt{\phi/2}$ にしたときにも残る。語彙を 2 つにまとめる操作はデータ処理不等式により KL を大きくしないので、KPop の量は、その位置の語彙全体の KL の下界でもある。

## KL の推定量: 値の不偏性と勾配の不偏性は別

[Schulman のブログ](http://joschu.net/blog/kl-approx.html) は、2 つの分布の KL を標本から推定する 3 つの量を $k_1$、$k_2$、$k_3$ と名付けた。GRPO の KL の項に当てはめ、prefix を固定した 1 つの位置で、token $o_t$ を更新中の方策 $\pi_\theta$ から生成し、比を $u = \pi_{\text{ref}}(o_t) / \pi_\theta(o_t)$ と書くと、3 つは $k_1 = -\log u$、$k_2 = \frac{1}{2}(\log u)^2$、$k_3 = (u - 1) - \log u$ で、どれも $\mathrm{KL}(\pi_\theta \,\|\, \pi_{\text{ref}})$ の推定に使う。$k_1$ と $k_3$ は値の不偏推定量で、$k_2$ は値には偏りがあるが、2 つの分布が近いときによい近似になる。1 章の DeepSeekMath の推定値は $k_3$ である。固定した prefix で $\pi_\theta$、$\pi_{\text{ref}}$ と、後で使う $\pi_{\text{old}}$ が共通の token の集合に正の確率を置くとすると、$\mathbb{E}_{\pi_\theta}[u] = 1$ なので、$k_3$ は $\pi_\theta$ から生成した token では KL の値の不偏推定量で、$\log u \le u - 1$ からつねに 0 以上になる。$k_3 = k_1 + (u - 1)$ は、平均 0 の $u - 1$ を制御変量として係数 1 で足した形で、基準の報酬と同じ考え方である。

一方で、これらを損失に入れ、生成した token を固定して微分すると、勾配の期待値は次のようになる。$s_t = \nabla_\theta \log \pi_\theta(o_t)$ と書く。

| 損失に入れる量 | 生成した token を固定した勾配 | $o_t \sim \pi_\theta$ での期待値 |
|---|---|---|
| $k_1$ | $s_t$ | $0$ |
| $k_2$ | $(\log \pi_\theta - \log \pi_{\text{ref}})\, s_t$ | $\nabla_\theta \mathrm{KL}(\pi_\theta \,\|\, \pi_{\text{ref}})$ |
| $k_3$ | $(1 - u)\, s_t$ | $\nabla_\theta \mathrm{KL}(\pi_{\text{ref}} \,\|\, \pi_\theta)$ |

この表は、値を正しく推定する量と、勾配を正しく推定する量が違うことを言っている。$k_1$ を損失にすると勾配の期待値は 0 で、$k_3$ を損失にすると、値として推定している $\mathrm{KL}(\pi_\theta \,\|\, \pi_{\text{ref}})$ ではなく、逆向きの $\mathrm{KL}(\pi_{\text{ref}} \,\|\, \pi_\theta)$ の勾配になる。2 つの KL は $\pi_\theta = \pi_{\text{ref}}$ の近くで 2 次まで一致するので、方策が参照モデルに近いうちは違いが小さい。token を古い方策 $\pi_{\text{old}}$ から生成した場合は、$k_3$ の値も、表の勾配の等式も、そのままでは成り立たない。

[DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) の補正は、この食い違いを直す形になっている。古い方策 $\pi_{\text{old}}$ から生成した token に比 $\pi_\theta / \pi_{\text{old}}$ を掛けると、prefix を固定した位置で $\mathbb{E}_{\pi_{\text{old}}}[(\pi_\theta/\pi_{\text{old}})\, k_3] = \mathrm{KL}(\pi_\theta \,\|\, \pi_{\text{ref}})$ と、$\theta$ の関数として厳密に KL になる。標本の分布は $\theta$ によらないので、微分と期待値を入れ替えられ、比も含めて微分すると、どの $\theta$ でも $\mathrm{KL}(\pi_\theta \,\|\, \pi_{\text{ref}})$ の勾配の不偏推定量になる。比の勾配を止めるとこの計算にはならない。論文は、この補正で KL の推定量の勾配が不偏になると書き、元の $k_3$ の勾配は、更新中の方策の確率が参照モデルよりずっと低い token に上限の無い大きな重みを与えると指摘している。上の表の $k_3$ の係数 $1 - u$ は、比 $u = \pi_{\text{ref}} / \pi_\theta$ が大きいほど絶対値がいくらでも大きくなり、その指摘と合う。どちらの形も、prefix の分布が $\theta$ で変わることによる勾配の項は含まない。[KL の正則化の勾配を整理した論文](https://arxiv.org/abs/2505.17508) も、GRPO の KL の項の off-policy の重みの食い違いを指摘している。

KL の項を残す場合の最適な方策も、統計の言葉で書ける。$\mathbb{E}_\pi[R] - \beta\, \mathrm{KL}(\pi \,\|\, \pi_{\text{ref}})$ を最大にする方策は、Gibbs の変分原理から $\pi^*(o) \propto \pi_{\text{ref}}(o)\, \exp(R(o)/\beta)$ で、参照モデルを報酬で指数的に傾けたもの（Esscher 変換）である。正誤の報酬では、問題ごとの正解率の log オッズがちょうど $1/\beta$ だけ上がる。参照モデルが確率を置かない回答には $\pi^*$ も確率を置かない。4 章で見た [There Is No Free Lunch in RLVR](https://arxiv.org/abs/2610.39295) も、RLVR を解き方の分布の指数的な傾きとして模型化している。ただし、この性質は KL の項を持つ目的関数の最適解の性質で、KL を外した学習や、有限の step の学習にそのまま当てはまるものではない。

## OPD: 語彙全体の蒸留は各位置のラオ・ブラックウェル化

2 章の生成した token を使う OPD を、prefix $h = (q, o_{<t})$ を固定した 1 つの位置で見る。生徒 $\pi_\theta$ から token $o_t$ を生成し、次の量を勾配の推定に使う。$\operatorname{sg}$ は、括弧の中を微分のときに定数として扱う操作（stop-gradient）である。

$$
X = \operatorname{sg}\big[\log \pi_T(o_t \mid h) - \log \pi_\theta(o_t \mid h)\big]\, \nabla_\theta \log \pi_\theta(o_t \mid h), \qquad \mathbb{E}[X \mid h] = -\nabla_\theta \mathrm{KL}\big(\pi_\theta(\cdot \mid h) \,\|\, \pi_T(\cdot \mid h)\big)
$$

右の等式は、前の節の $k_2$ の行と同じ計算で得られる。語彙全体の蒸留は、この右辺を語彙の和で厳密に計算する。つまりその位置の語彙全体の蒸留の勾配は、生成した token の推定量 $X$ の、prefix を条件とした条件付き期待値 $\mathbb{E}[X \mid h]$ である。全分散の公式 $\operatorname{Cov}(X) = \mathbb{E}[\operatorname{Cov}(X \mid h)] + \operatorname{Cov}(\mathbb{E}[X \mid h])$ から、条件付き期待値の分散共分散行列は元の推定量のものを超えない。これはラオ・ブラックウェルの定理の形で、消えるのは、その位置で次の token を 1 つ引くことによる分散 $\mathbb{E}[\operatorname{Cov}(X \mid h)]$ である。

この保証は 1 つの位置の項についてのものである。回答の勾配は位置についての和で、後の位置の条件付き期待値は前の位置で引いた token に依存するので、各位置を条件付き期待値に置き換えても、和の分散が小さくなるとはこの定理だけでは言えない。[DeepSeek-V4](https://arxiv.org/abs/2606.19348) は、生成した token の log の比を使う方法を勾配の推定の分散が大きいと評価して語彙全体の蒸留を選んだと書いていて、上の計算はその評価の 1 つの位置での裏付けにあたる。prefix は生徒から生成したままなので、prefix の分散も残る。教師の上位の token だけで和を取る top-k の蒸留は、外した token について、生徒の確率で重みを付けた勾配の項の和が小さいときだけ条件付き期待値に近く、一般には偏りを持つ近似である。教師の残りの確率が小さくても、生徒がその token に大きな確率を置いていれば、外した項は小さくない。さらに、どの形も、$\theta$ が変わると後の prefix の分布も変わることによる勾配の項を含まない。[Thinking Machines の OPD のブログ](https://thinkingmachines.ai/blog/on-policy-distillation/) は、割引率を 0 とし、直後の token だけを最適化すると書き、割引率を正にするほうが数学的には正しいが、実際には性能が上がらなかったと述べている。

3 章の [Nemotron 3.5 Super](https://arxiv.org/abs/2610.38095) の 2 段目は、教師を、教師と、更新の前に固定した生徒 $\bar{\pi}_S$ の混合 $\tilde{\pi}_T = \alpha \pi_T + (1-\alpha) \bar{\pi}_S$ に替えた。固定した生徒と混合の KL $\mathrm{KL}(\bar{\pi}_S \,\|\, \tilde{\pi}_T)$ は、[Lee（1999）](https://aclanthology.org/P99-1004/) の skew divergence と同じ形で、比 $\bar{\pi}_S / \tilde{\pi}_T \le 1/(1-\alpha)$ から、$0 \le \alpha < 1$ でつねに $-\log(1-\alpha)$ 以下に収まる。3 章で見た advantage の下限 $\log(\tilde{\pi}_T / \bar{\pi}_S) \ge \log(1-\alpha) \approx -0.22$ は、同じ比の不等式の token ごとの形である。この上界は、更新中の生徒を第 1 引数とする KL にはそのまま当てはまらない。目標の分布を生徒の側に縮める操作で、重点抽出法で提案分布に目標の分布を混ぜて重みを抑える [Hesterberg（1995）](https://doi.org/10.1080/00401706.1995.10484303) の defensive mixtureと同じ考え方を、目標の側に使ったものと読める。

## 回答の数の配分: ネイマン配分は目的関数で変わる

層化抽出で、層 $\ell$ の重み $W_\ell$ と標準偏差 $\sigma_\ell$ を使って全体の平均を推定するとき、層ごとの標本の数 $n_\ell$ で推定量の分散は $\sum_\ell W_\ell^2 \sigma_\ell^2 / n_\ell$ になる。総数 $\sum_\ell n_\ell = n$ のもとでこれを最小にすると、ラグランジュの未定乗数法から $n_\ell \propto W_\ell \sigma_\ell$ が得られる。これがネイマン配分で、最小の分散は $(\sum_\ell W_\ell \sigma_\ell)^2 / n$ である。

GRPO の問題を層と見ると、推定したいのは平均の報酬ではなく勾配で、回答の数の多い極限での各問題の重みは目的関数 $f$ の微分 $f'(p_q)$ である。勾配はベクトルなので、分散共分散行列の trace（成分の分散の和）を最小にする。基準を真の正解率 $p_q$ とし、$\|s\|^2$ が報酬と独立で、その期待値 $\mathbb{E}\|s\|^2$ が問題によらないと仮定すると、1 回の生成による trace は $p_q(1-p_q)\, \mathbb{E}\|s\|^2 - \|\nabla p_q\|^2$ である。第 2 項を無視でき、問題の重みと 1 回の生成の費用が等しいとさらに近似すると、同じ計算から次の目安になる。

$$
n_q \propto |f'(p_q)|\, \sqrt{p_q (1 - p_q)}
$$

この式は、どの量を上げたいかで、回答の数の配り方の目安が変わることを言っている。Dr. GRPO の $f(p) = p$ では $n_q \propto \sqrt{p_q(1-p_q)}$ で、3 章で触れたネイマン配分 $G_q \propto \sigma_q$ になる。GRPO の $f(p) = 2\arcsin\sqrt{p}$ では $f'(p)\sqrt{p(1-p)} = 1$ なので、どの問題にも同じ数を配る形になる。log の目的では $n_q \propto \sqrt{(1-p_q)/p_q}$ で、正解率が小さいところでは Reinforce-Ada の $1/\sqrt{\hat{p}_q}$ に小さい定数を足した形に近い。

![3 つの目的関数のもとで、スコア関数の大きさが問題と正誤によらず勾配の平均の 2 乗の項を無視できると仮定し、正解率 0.05 から 0.95 までの 10 個の値で回答の数の平均を 16 にそろえたネイマン配分の目安を並べた計算図。Dr. GRPO では正解率 0.5 の近くで 20、両端で 9、GRPO の逆正弦の目的ではどの問題も 16、log の目的では正解率 0.05 で 51、0.95 で 3 になる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mathematical-statistics/neyman-by-objective.png)

この計算は、スコア関数の大きさが問題と正誤によらないという強い仮定、勾配の平均の 2 乗の項を無視する近似、正解率が分かっているという仮定に依っている。省いた項は小さいとは限らず、ロジットを 1 つ持つベルヌーイの方策では $p = 0.5$ で推定量が定数になり、分散は 0 になる。実際には正解率は前の生成から推定するので、配分は逐次的な標本設計になり、正解を 1 回も観測していない問題を正解率 0 と見なす誤りも入る。有限の $G$ で同じグループから基準と標準偏差を作る GRPO の最適な配分を、この計算が直接与えるわけでもない。3 章の [VSR](https://arxiv.org/abs/2610.15888) は、勾配の大きさが状態によらずそろうという仮定のもとで、回答の途中の状態についてネイマン配分の形 $\nu(x) \propto d^\pi(x)\, \sigma_\pi(x)$ を導いた。ここではスコア関数の $s$ と区別するために、3 章で $s$ と書いた状態を $x$ と書いている。上の式の Dr. GRPO の行と同じ組み合わせの仮定にあたる。3 章で回答の数を確かめた公開モデルの報告は問題によらない数を使っていたが、その理由はこの計算からは決まらない。平均を引くだけの advantage を使う DeepSeek-V3.2 の形では、同じ近似のもとでの目安は一様ではなく $\sigma_q$ に比例する配分である。

## 評価の統計: pass@k の UMVUE と 2 つの分散

問題 $q$ の pass@k は $1 - (1 - p_q)^k$ である。同じ方策と生成の設定から独立に $n$ 回解き（$1 \le k \le n$）、$c$ 回が正解のとき、[Chen らの論文](https://arxiv.org/abs/2107.03374) の推定量は次の形である。

$$
\widehat{\text{pass@}k} = 1 - \binom{n - c}{k} \Big/ \binom{n}{k}
$$

これは $n$ 回の試行から $k$ 回を選ぶすべての組み合わせについて「1 回でも正解があるか」の平均を取った U 統計量で、不偏である。$c$ は 2 項分布の完備十分統計量で、推定量は $c$ だけの関数なので、レーマン・シェフェの定理からこれは UMVUE になる。素朴に $1 - (1 - c/n)^k$ とすると、$(1-x)^k$ が凸関数なのでジェンセンの不等式から、pass@k を小さく見積もる。

ベンチマークの平均の誤差には 2 つの成分がある。$Q$ 個の問題を問題の母集団からの標本と見て、各問題を $n$ 回解くと、正解率の平均の分散は次の形になる。

$$
\operatorname{Var}(\bar{Y}) = \frac{1}{Q}\left( \operatorname{Var}_q(p_q) + \frac{\mathbb{E}_q[p_q (1 - p_q)]}{n} \right)
$$

第 1 項は問題の間のばらつき、第 2 項は問題の中の試行のばらつきで、$n$ を増やして減るのは第 2 項だけである。筆者の計算の例として、正解率 0.9 と 0.1 の問題が半数ずつの母集団から 30 問を独立に選ぶ模型では、$n = 16$ で第 2 項だけの標準誤差は 1.4 ポイント、両方の項を含むと 7.4 ポイントで、$n = 64$ にしても 7.3 ポイントまでしか下がらない。「この 30 問での成績」を問うなら第 2 項だけ、「この種類の問題での成績」を問うなら両方が誤差になる。2 つのモデル A と B を同じ問題で比べ、問題ごとの差を取ると、差に残る問題の間の分散は $\operatorname{Var}_q(p^A_q) + \operatorname{Var}_q(p^B_q) - 2\operatorname{Cov}_q(p^A_q, p^B_q)$ で、2 つのモデルの問題ごとの正解率の相関が高いほど小さくなる。

4 章の評価の論文は、この考え方を使っている。[RLVR is a Kernel, Not a Function](https://arxiv.org/abs/2609.22547) は、同じ問題で比べた pass@k の差の曲線に、各点の信頼区間ではなく、すべての $k$ で同時に成り立つ同時信頼帯（sup-t）を付け、逆転を主張できるかを判定した。[Phantom Gains](https://arxiv.org/abs/2608.20290) は、閾値で数える統計量を、未学習のモデルの独立な評価をまとめた基準に対する問題ごとの正確検定に替え、偽発見率（FDR）を制御した。[What Will Post-Training Fix?](https://arxiv.org/abs/2610.04978) は、互いに重ならない実行の部分集合の間の一致から、どの問題が直るかについて、測定の雑音のもとで到達できる一致の上限（noise ceiling）を約 0.9 と見積もり、2 つの base model の間の一致は相関 0.25 にとどまると報告した。測定に雑音があると、真の相関が 1 でも観測される相関は 1 を下回る。0.25 という値は、この上限と比べて読む必要がある。

pass@1 と pass@k の逆転も、問題ごとの正解率の分布の別の関数を見ていると読める。pass@1 は $\mathbb{E}_q[p_q]$、pass@k は $\mathbb{E}_q[1 - (1-p_q)^k]$ で、$k$ を大きくすると正解率が 0 より大きい問題の割合 $P(p_q > 0)$ に近づく。RL が正解率の平均を上げながら、一部の問題の小さい正解率を下げると、有限の $k$ で 2 つが逆に動く場合がある。逆転するかどうかは問題ごとの変化の全体で決まり、正解率が正のまま小さくなるだけなら、$k \to \infty$ の極限は変わらない。また、$n$ 回の試行で正解が 0 回だったことは正解率が 0 だったことを意味せず、95% の信頼上限はおよそ $3/n$ である（3 の法則）。4 章の [ProRL](https://arxiv.org/abs/2505.24864) の例のように、元のモデルの評価値が 0 だった課題を読むときは、何回試したかを合わせて見る必要がある。

## 多数決と陪審定理: ラベル無しの RL が誤った合意に向かう理由

コンドルセの陪審定理は、2 択では、各投票者が独立に、共通の $1/2$ より大きい確率で正しい答えを選ぶなら、投票者を増やすと多数決が正しい確率は 1 に近づくと述べる。答えが 3 つ以上のときに最多票を採る場合は、正しい答えが最も選ばれやすいことが同じ役割を果たすことを、[List と Goodin（2001）](https://doi.org/10.1111/1467-9760.00128) が示している。4 章の [TTRL](https://arxiv.org/abs/2504.16084) は、同じモデルの $m$ 個の回答の最多票を疑似的な正解にする。1 つの問題の中では、固定したモデルから回答が独立に生成されるので、答えの分布の最頻値が 1 つに決まるなら、$m$ を増やすと最多票はその最頻値にほとんど確実に一致する。

この収束は、最頻値が正しいかどうかによらない。最頻値が誤っている問題では、回答を増やすほど、誤った答えが確実に疑似的な正解になる。$m \to \infty$ での疑似的な正解の正しさは、最頻値が正しい問題の割合で決まり、標本を増やしても上がらない。その疑似的な正解で RL を回すと、誤った最頻値への集中を自分で強める可能性がある。ただし、学習のあとの方策がどこへ向かうかは、更新の式、正則化、問題の間でのパラメータの共有によって決まり、最多票の収束だけからは導けない。4 章で見た consensus trap は、この可能性が学習の中で現れたものと読める。選択肢の少ない問題で誤った推論が同じ選択肢に集まりやすいという[医療の QA の報告](https://arxiv.org/abs/2609.16660) は、最頻値が誤る問題の割合が大きい場合にあたる。正しい答えが最も選ばれやすいかどうかは正解率だけでは決まらず、誤った答えが 1 つの答えに集まるかどうかにもよる。

## まとめ

GRPO とその後の手法の部品は、方策勾配の推定量の偏りと分散を変えるものと、目的関数や問題の間の重みを変えるものに分けて読める。基準の報酬は制御変量で、自分を含む平均は勾配の大きさだけを既知の定数倍で変える。標準偏差での割り算は、正誤の報酬で回答の数の多い極限では、目的関数を逆正弦に変える選択にあたり、問題の間の重みを変える。強い仮定のもとでは、回答の数の配り方の目安も変える。重要度の重みは、固定長の回答で各位置のずれに一様な下限があれば、長いほど 2 乗の期待値が指数的に大きくなるので、実際の手法は winsorize か trimming で偏りを受け入れ、回答単位の判定は KL の推定値の閾値判定になっている。KL の推定量は値と勾配で性質が違い、語彙全体の蒸留は、各位置の項について、生成した token の蒸留のラオ・ブラックウェル化である。評価の数字は、問題の間のばらつき、同時信頼帯、多重比較の補正を通して読む必要がある。

## 参考: この章で扱った資料

| 資料 | 公開 | この章での役割 |
|---|---|---|
| Kong, "A Note on Importance Sampling using Standardized Weights", シカゴ大学の技術報告 | 1992 年 | 有効標本数の目安 |
| [Hesterberg, Weighted Average Importance Sampling and Defensive Mixture Distributions](https://doi.org/10.1080/00401706.1995.10484303) | 1995 年 | defensive mixture |
| [Lee, Measures of Distributional Similarity](https://aclanthology.org/P99-1004/) | 1999 年 | skew divergence |
| [List and Goodin, Epistemic Democracy: Generalizing the Condorcet Jury Theorem](https://doi.org/10.1111/1467-9760.00128) | 2001 年 | 多択の陪審定理 |
| [Greensmith, Bartlett, Baxter, Variance Reduction Techniques for Gradient Estimates in Reinforcement Learning](https://jmlr.org/papers/v5/greensmith04a.html) | 2004 年 | 方策勾配の基準と制御変量 |
| [Ionides, Truncated Importance Sampling](https://doi.org/10.1198/106186008X320456) | 2008 年 | 重みを上限で切る推定量 |
| [Schulman, Approximating KL Divergence](http://joschu.net/blog/kl-approx.html) | 2020 年 | KL の推定量 $k_1$、$k_2$、$k_3$ |
| [Chen ら, Evaluating Large Language Models Trained on Code](https://arxiv.org/abs/2107.03374) | 2021 年 7 月 | pass@k の不偏推定量 |
| [RLOO](https://arxiv.org/abs/2402.14740) | 2024 年 2 月 | 自分を除いた平均の基準 |
| [TTRL](https://arxiv.org/abs/2504.16084) | 2025 年 4 月 | 多数決の疑似的な正解 |
| [On the Design of KL-Regularized Policy Gradient Algorithms](https://arxiv.org/abs/2505.17508) | 2025 年 5 月 | KL の項の off-policy の重み |
| [ProRL](https://arxiv.org/abs/2505.24864) | 2025 年 5 月 | 評価値 0 の課題 |
| [M2PO](https://arxiv.org/abs/2510.01161) | 2025 年 10 月 | log の比の 2 次の moment |
| [Reinforce-Ada](https://arxiv.org/abs/2510.04996) | 2025 年 10 月 | log の目的と回答の数の配分 |
| [Advantage Shaping as Surrogate Reward Maximization](https://arxiv.org/abs/2510.23049) | 2025 年 10 月 | GRPO と逆正弦の代理の報酬 |
| [On-Policy Distillation のブログ](https://thinkingmachines.ai/blog/on-policy-distillation/) | 2025 年 10 月 | 割引率 0 の OPD |
| [DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) | 2025 年 12 月 | 重要度の比で補正した KL の推定量 |
| [DeepSeek-V4](https://arxiv.org/abs/2606.19348) | 2026 年 4 月（arXiv の v1 の表示） | 語彙全体の蒸留の選択 |
| [GRPO, Dr. GRPO, and DAPO Are Three Operations on One Number](https://arxiv.org/abs/2607.00152) | 2026 年 6 月 | 有限の $G$ での GRPO の勾配の恒等式 |
| [長さの重みの不可能性の論文](https://arxiv.org/abs/2607.23364) | 2026 年 7 月 | 不偏性と長さへの非依存の両立の不可能性 |
| [Mercor と SkyRL の 397B の学習の手引き](https://www.mercor.com/blog/training-frontier-knowledge-work-agents-a-397b-rl-training-guide-with-skyrl/) | 2026 年 9 月 | batch 全体と問題ごとの平均の比較 |
| [Phantom Gains](https://arxiv.org/abs/2608.20290) | 2026 年 8 月 | 問題ごとの正確検定と FDR |
| [医療の QA での TTRL](https://arxiv.org/abs/2609.16660) | 2026 年 9 月 | 選択肢の問題での誤った合意 |
| [RLVR is a Kernel, Not a Function](https://arxiv.org/abs/2609.22547) | 2026 年 9 月 | pass@k の差の同時信頼帯 |
| [What Will Post-Training Fix?](https://arxiv.org/abs/2610.04978) | 2026 年 10 月 | 雑音のもとでの一致の上限 |
| [VSR](https://arxiv.org/abs/2610.15888) | 2026 年 10 月 | 状態のネイマン配分 |
| [Nemotron 3.5](https://arxiv.org/abs/2610.38095) | 2026 年 10 月 | 教師と生徒の混合 |
| [There Is No Free Lunch in RLVR](https://arxiv.org/abs/2610.39295) | 2026 年 10 月 | 指数的な傾きとしての RLVR |
