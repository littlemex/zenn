---
title: "GRPO 以降の RL 事後学習の目的関数: 方策勾配の推定量として読む基準、正規化、クリップ、KL"
free: false
---

## はじめに

[DeepSeek-R1](https://arxiv.org/abs/2501.12948) が GRPO で reasoning（長い思考で問題を解く能力）を伸ばしてから、公開モデルの技術報告の多くは GRPO を土台にして、その式に手を入れてきた。**GRPO 以後の変更の多くは、方策勾配の推定量の偏りとばらつきを変えるものと、上げる量そのものや問題の間の重みを変えるものに分けて読め、クリップのように 1 回の更新の幅を決める代理の目的関数の変更がそれに加わる**。この章では、GRPO の式の部品ごとに、どの論文が何を変え、それが推定量の上で何を意味するかを、数理統計の言葉と合わせて整理する。

| 問い | 答え | 節 |
|---|---|---|
| GRPO は何を推定しているのか | 報酬の期待値の勾配（スコア関数と報酬の積の標本平均）を出発点に、基準、標準偏差、長さ、クリップ、KL で更新の量を作り替えている。作り替えた量は、一般には元の期待値の勾配と一致しない | [GRPO は回答の比較から何を更新するか](#grpo-は回答の比較から何を更新するか) |
| 基準の報酬を引く、標準偏差で割る、は何を変えるか | 基準を引くのは勾配の期待値を保ったままばらつきを減らせる制御変量で、標準偏差で割ると問題の間の重みが変わる | [基準と正規化](#基準と正規化は勾配のばらつきと問題の重みを変える) |
| 長さでの割り方とクリップは何を変えるか | 長さでの割り方は回答の重みを、クリップと比の単位は 1 回の生成で方策をどこまで動かすかを決める | [回答の長さとクリップ](#回答の長さとクリップはどの-token-をどれだけ更新するかを決める) |
| KL の項はどう扱われているか | 外す報告が多い。残す場合、KL の推定値の値が正しくても、微分した勾配が正しいとは限らない | [KL](#kl-は何を制約し何を推定するか) |
| 回答の数はどの問題に配るべきか | 上げたい量によって最適な配り方の目安が変わる。回答の数を確かめた公開モデルの報告は、問題によらない数を使っている | [回答の数の配分](#回答の数をどの問題に配るか) |

読者には、方策勾配法と PPO のクリップの考え方の知識を想定する。数理統計の部分は、統計検定 1 級の程度の知識（スコア関数、Fisher 情報量、制御変量、デルタ法、層化抽出とネイマン配分）で追えるように書く。実験の数値は各論文の報告値で、筆者は再現していない。出典を書いていない導出と、計算図の値は、筆者による説明用の計算である。[次の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-mismatch-async-and-opd)では訓練と推論のずれと On-Policy Distillation を、[その次の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-open-models-and-open-problems)では 2026 年後半の公開モデルと未解決の課題を扱う。資料は 2026 年 10 月 30 日と 31 日に確かめた。

## GRPO は回答の比較から何を更新するか

GRPO（Group Relative Policy Optimization）は、1 つの問題に複数の回答を生成し、回答どうしの報酬を比べて、平均より良い回答の確率を上げ、悪い回答の確率を下げる。価値を予測する別のモデル（critic）を持たずに、同じ問題への回答の比較だけで更新の向きを決めるのが特徴である。

| 部品 | 受け持ち |
|---|---|
| 問題 $q$ と回答 $o_i$ | 標本の単位。1 つの問題に $G$ 個の回答を生成する |
| 方策 $\pi_\theta$ | 回答の確率分布。学習で動かす対象 |
| 報酬 $R_i$ | 回答の良し悪し。この章では主に正誤の 2 値 |
| advantage $\hat{A}_i$ | 報酬を同じ問題の回答どうしで比べた値。更新の向きと大きさを決める |
| スコア関数 $s(o) = \nabla_\theta \log \pi_\theta(o \mid q)$ | 回答の確率を上げる向き。勾配の推定量を作る |

[DeepSeekMath の論文](https://arxiv.org/abs/2402.03300) の目的関数は次の形である。$\pi_{\theta_{\text{old}}}$ は回答を生成した更新前の方策で、この章では生成したときの確率と訓練で計算し直した確率が一致するとみなす（一致しない場合は次の章で扱う）。$r_{i,t}(\theta) = \pi_\theta(o_{i,t} \mid q, o_{i,<t}) / \pi_{\theta_{\text{old}}}(o_{i,t} \mid q, o_{i,<t})$ は token ごとの確率の比である。

$$
\mathcal{J}_{\text{GRPO}}(\theta) = \mathbb{E}\left[ \frac{1}{G} \sum_{i=1}^{G} \frac{1}{|o_i|} \sum_{t=1}^{|o_i|} \Big( \min\big( r_{i,t} \hat{A}_{i},\ \operatorname{clip}(r_{i,t}, 1-\varepsilon, 1+\varepsilon) \hat{A}_{i} \big) - \beta\, \hat{d}^{\text{KL}}_{i,t} \Big) \right], \qquad \hat{A}_{i} = \frac{R_i - \operatorname{mean}(R)}{\operatorname{std}(R)}
$$

$\operatorname{clip}$ は比を $[1-\varepsilon, 1+\varepsilon]$ に収める関数で、$\varepsilon$ はクリップの幅である。$\hat{d}^{\text{KL}}_{i,t}$ は参照モデル $\pi_{\text{ref}}$（ふつうは RL を始める前のモデル）から離れすぎないための罰則の推定値で、$\beta$ はその係数である。回答の終わりに 1 つの報酬を与える場合（outcome の報酬）、advantage は同じ回答のすべての token に同じ値が配られる。全員の報酬が同じグループでは標準偏差が 0 になってこの式は使えず、以下ではそのグループの advantage を 0 とし、報酬から来る方策の項の勾配を 0 とする（KL の項を残す場合、その勾配は別に生じる）。後の論文が手を入れたのは、この式の 5 つの部品である。$\frac{1}{|o_i|}$ と $\frac{1}{G}$ による平均の取り方、advantage の基準と正規化、$\operatorname{clip}$ の形、$r_{i,t}$ を token ごとに取る比の単位、KL の項 $\hat{d}^{\text{KL}}_{i,t}$ である。5 つは筆者の整理の枠で、変更のすべてを含む分類ではない。

![GRPO の目的関数を 5 つの部品に分け、それぞれの部品を変えた手法を並べた。平均の取り方は DAPO と Dr. GRPO、報酬の基準は Dr. GRPO、RLOO、REINFORCE++ と、全員が同じ結果のグループを除く方法、クリップは Clip-Higher、CISPO、SAPO、比の単位は GSPO と GMPO、KL は除去と DeepSeek-V3.2 の推定量の補正](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-objectives-and-estimators/grpo-anatomy.png)

この式が何を推定しているかを、問題 $q$ の正解率 $p = \mathbb{E}_{o \sim \pi_\theta}[R(o)]$ で見る。問題の分布と報酬は $\theta$ によらず、回答は終了の token を含む分布から生成され、微分と和を入れ替えられ、スコア関数の 2 次の moment が有限だとすると、次の恒等式が成り立つ。

$$
\nabla_\theta\, p = \mathbb{E}_{o \sim \pi_\theta}\big[ R(o)\, s(o) \big], \qquad \mathbb{E}_{o \sim \pi_\theta}[s(o)] = 0
$$

左の式は、回答を 1 つ生成して $R(o)\, s(o)$ を計算すれば勾配の不偏推定量になることを言っている（REINFORCE）。右の式は最尤推定の教科書でスコア関数の期待値が 0 になるのと同じ式で、スコア関数の分散共分散行列は Fisher 情報量の行列である。回答の確率は token の条件付き確率の積なので、$s(o)$ は token ごとのスコアの和になる。以下の節は、この推定量の何を変えたかで読む。統計の計算では、現在の方策から独立に生成した回答を使い、断らない限り長さの重み、クリップ、KL を除いた回答単位の勾配を考える。

## 基準と正規化は勾配のばらつきと問題の重みを変える

回答によらない基準の報酬を引く操作は勾配の期待値を変えず、適切に選べばばらつきを減らす。グループの標準偏差で割る操作は問題の間の重みを変える。2 つは GRPO の advantage の式の分子と分母にあたり、統計の上の働きが違う。

### 基準を引くのは制御変量

スコア関数の期待値が 0 なので、回答によらない数 $\lambda$ について $(R - \lambda)\, s$ も不偏推定量になる。これは平均が分かっている確率変数を使う制御変量法そのもので、$\lambda$ が基準の報酬（baseline）である。勾配の成分 $j$ ごとに、$\mathbb{E}[s_j^2] > 0$ の成分で分散を最小にする $\lambda$ は $\lambda^*_j = \mathbb{E}[s_j^2 R] / \mathbb{E}[s_j^2]$ で、$s_j^2$ と $R$ が無相関に近いときに報酬の平均 $p$ に近くなる。[Greensmith らの論文](https://jmlr.org/papers/v5/greensmith04a.html) が、方策勾配の基準と制御変量による分散の削減を体系的に扱っている。

GRPO の基準のグループの平均は自分の報酬 $R_i$ を含むので、回答によらないという条件を満たさない。[RLOO](https://arxiv.org/abs/2402.14740)（REINFORCE Leave-One-Out）は自分を除いた $G-1$ 個の平均を基準にする。標本ごとに次の恒等式が成り立つ。

$$
R_i - \bar{R} = \frac{G-1}{G} \Big( R_i - \frac{1}{G-1} \sum_{j \ne i} R_j \Big)
$$

RLOO の形は勾配の不偏推定量なので、自分を含む平均を引く形の期待値はちょうど $\frac{G-1}{G} \nabla p$ になる。偏りは既知の定数倍だけで、$G = 16$ なら 16 分の 15 であり、学習率に吸収できる。標準偏差や長さの扱いをそろえれば、RLOO と平均を引くだけの形は勾配の向きで同じものである。[DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) と [Olmo 3](https://arxiv.org/abs/2512.13961) は、平均を引くだけで標準偏差で割らない形を使っている。

batch 全体の標準偏差で割る [REINFORCE++](https://arxiv.org/abs/2501.03262) や、グループの平均を引いたあと minibatch の平均と標準偏差で正規化する [Magistral](https://arxiv.org/abs/2506.10910) の形のうち、共通の標準偏差で割る操作は、観測した batch の中ではすべての問題に同じ倍率を掛ける。その倍率は batch の中身と相関するので、期待値の上で元の勾配の定数倍になるとは限らないが、batch が大きく標準偏差がほぼ一定なら、学習率を batch ごとに調整する働きとして近似できる。minibatch の平均も引く場合、その平均が 0 でなければ共通の倍率以外の変更も加わる。

### 標準偏差で割ると問題の重みが変わる

グループの標準偏差で割る形は、正誤の報酬で回答の数が多い極限では、正解率 $p$ ではなく $2\arcsin\sqrt{p}$ を上げる勾配になる。[Dr. GRPO](https://arxiv.org/abs/2503.20783) は、この割り算が易しすぎる問題と難しすぎる問題の重みを増やす「難しさの偏り」を生むと指摘して外した。統計の側から見ると、これは推定量の偏りではなく、どの量を上げるかという目的関数の選び方の違いである。

$G$ 個のうち $G_+$ 個が正解（$0 < G_+ < G$）のとき、[GRPO と Dr. GRPO と DAPO を 1 つの量で整理した論文](https://arxiv.org/abs/2607.00152) は、1 つの問題の GRPO の勾配が標本ごとに次の形に書けることを示した。$\bar{s}_+$ と $\bar{s}_-$ は正解と不正解の回答のスコア関数の平均、$\hat{\sigma}$ は自由度で割らない標本標準偏差である。

$$
g_{\text{GRPO}} = \frac{1}{G} \sum_{i} \frac{R_i - \bar{R}}{\hat{\sigma}}\, s(o_i) = \hat{\sigma}\, (\bar{s}_+ - \bar{s}_-), \qquad \hat{\sigma} = \frac{\sqrt{G_+(G-G_+)}}{G}
$$

標準偏差で割らない形の勾配は $\hat{\sigma}^2 (\bar{s}_+ - \bar{s}_-)$ になり、2 つの形の違いは、問題ごとに掛かる係数が $\hat{\sigma}$ か $\hat{\sigma}^2$ かだけである（長さの扱いとクリップを除く）。$\mathbb{E}[s \mid R=1] = \nabla p / p$ と $\mathbb{E}[s \mid R=0] = -\nabla p / (1-p)$ から $\mathbb{E}[\bar{s}_+ - \bar{s}_- \mid G_+] = \nabla p / (p(1-p))$ なので、期待値は次のようになる。

$$
\mathbb{E}[g_{\text{GRPO}}] = \frac{\mathbb{E}[\hat{\sigma}]}{p(1-p)}\, \nabla p \;\xrightarrow{\,G \to \infty\,}\; \frac{\nabla p}{\sqrt{p(1-p)}} = \nabla\big(2 \arcsin \sqrt{p}\big), \qquad \mathbb{E}\big[\hat{\sigma}^2 (\bar{s}_+ - \bar{s}_-)\big] = \frac{G-1}{G}\, \nabla p
$$

$\arcsin\sqrt{p}$ は 2 項分布の比率の分散安定化変換として知られる関数で、デルタ法で $\operatorname{Var}(\arcsin\sqrt{\hat{p}}) \approx \frac{1}{4n}$ と $p$ によらない分散になる。極限の係数 $1/\sqrt{p(1-p)}$ はベルヌーイ分布の Fisher 情報量の平方根で、GRPO は正解率の進み方を、確率の差ではなく、標本から見分けられる度合いの単位で測っていると読める。[Thrampoulidis らの論文](https://arxiv.org/abs/2510.23049) は、クリップを除いた GRPO を、回答の数の多い極限でこの変換を報酬にした RLOO として読む見方を示し、2607.00152 の論文が有限の $G$ での恒等式を与えた。Fisher 情報量としての読み方は、筆者が加えた解釈である。有限の $G$ では係数は極限と違い、筆者の計算では $G = 16$、正解率 0.05 で 3.27（極限は 4.59）、正解率 0.5 で 1.93（極限は 2）である。

![正誤の報酬で回答の数を増やす極限での、1 つの問題の正解率の勾配に掛かる重みを、正解率 0.5 での値を 1 として並べた計算図。Dr. GRPO はどの正解率でも 1、GRPO は正解率 0.05 と 0.95 で 2.29、正解率の log を目的にすると 0.05 で 10 になる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-objectives-and-estimators/objective-weights.png)

| 手法 | 回答の数の多い極限で上げる量 $f(p)$ | $\nabla p$ に掛かる重み $f'(p)$ | $p = 0.05$ と $p = 0.5$ の重みの比 |
|---|---|---|---|
| Dr. GRPO、平均を引くだけの形 | $p$ | $1$ | 1 |
| GRPO | $2\arcsin\sqrt{p}$ | $1/\sqrt{p(1-p)}$ | 約 2.3（$G = 16$ では約 1.7） |
| [Reinforce-Ada](https://arxiv.org/abs/2510.04996) が導出に使った目的 | $\log p$ | $1/p$ | 10 |

どの変換も単調増加なので、1 つの問題の中での最適な方策は同じだが、問題をまたいだ重みが変わる。正規化の働きには別の見方もあり、[Why GRPO Needs Normalization](https://arxiv.org/abs/2601.23135) は、標準偏差での正規化が適応的な勾配として働き、正規化しない REINFORCE より収束が速くなる条件を示した。GRPO は正解率が 0 と 1 に近い問題の両方を重く扱い、log の目的は難しい問題だけを重く扱う。

### 全員が同じ結果のグループ

正誤の報酬でグループの全員が正解か全員が不正解なら、平均を引いた分子は全員 0 で、標準偏差も 0 になる。このグループの勾配は 0 とみなされ、その問題から更新は生まれない。$G$ 個が全員同じ結果になる確率は $p^G + (1-p)^G$ で、$G = 16$ のとき正解率 0.05 で 0.44、0.5 で約 $3 \times 10^{-5}$ である。難しい問題では、作ったグループの半分近くが無駄になる。

[DAPO](https://arxiv.org/abs/2503.14476) の Dynamic Sampling は多めに生成して正解率が 0 と 1 のグループを除き、batch の問題の数を保つ。Magistral と Olmo 3 も同じ結果のグループを外している。捨てたグループの勾配はもともと 0 なので、問題を独立に選ぶ理想化のもとでは、残ったグループの平均の期待値は全体の倍率が変わるだけで、問題の間の相対的な重みは変わらない。変わるのは、追加の生成の費用と全体の倍率である。

## 回答の長さとクリップはどの token をどれだけ更新するかを決める

長さでの割り方は、どの回答と token にどれだけの重みを与えるかを決める。クリップと比の単位は、同じ生成で方策を何度も更新するときに、どこまで動かしてよいかを決める。前者は推定量の重みの設計、後者は 1 回の更新の幅の設計である。

### 長さで割る: 比の平均と比推定量

outcome の報酬では回答 $o_i$ の token の勾配の和は $\hat{A}_i\, s(o_i)$ になる。長さの扱いだけを比べるため、この節の 3 つの式では標準偏差で割らずに $\hat{A}_i = R_i - \bar{R}$ とし、回答の長さを $L_i = |o_i|$ とする。3 つの平均の取り方は標本調査の 3 つの推定量に対応する。

$$
\underbrace{\frac{1}{G} \sum_{i} \frac{\hat{A}_i\, s(o_i)}{L_i}}_{\text{GRPO}}, \qquad \underbrace{\frac{\sum_i \hat{A}_i\, s(o_i)}{\sum_i L_i}}_{\text{DAPO}}, \qquad \underbrace{\frac{1}{G \cdot L_{\max}} \sum_i \hat{A}_i\, s(o_i)}_{\text{Dr. GRPO}}
$$

GRPO の形は回答ごとの比の平均で、分母の $L_i$ が正誤と相関しうるので、期待値は $\nabla p$ の定数倍にならない。Dr. GRPO は、これが正解では短い回答を、不正解では長い回答を好む偏りを生むと指摘し、分母を設計で決めた定数 $L_{\max}$ にした。期待値は $\frac{G-1}{G L_{\max}} \nabla p$ と、$\nabla p$ の既知の定数倍になる。Qwen2.5-Math-7B を MATH の level 3 から 5 の問題で 8 枚の A100 で 27 時間学習し、AIME 2024 で 43.3% を得たと報告している。DAPO の形は分子の和と分母の和の比を取る比推定量で、長さが正で必要な moment が有限なら、グループを大きくすると $\nabla p / \mathbb{E}[L]$ に収束する一致推定量だが、有限のグループでは偏りがある。$\mathbb{E}[L]$ も方策で変わるので、これは $p / \mathbb{E}[L]$ の勾配ではない。Magistral はグループの token の総数で、Olmo 3 は batch 全体の token の総数で割っている。

[2026 年 7 月の論文](https://arxiv.org/abs/2607.23364) は、正誤だけを報酬にする設定で、回答の長さで決まる重みのどれを選んでも、勾配の推定に偏りが無いこと（Dr. GRPO の側）と、各回答の寄与が長さによらないこと（GRPO がおおよそ満たす側）を同時には満たせないと証明した。3 つの形の間の選択は釣り合いの問題である。

batch 全体の token の総数で割る形（多くの実装の既定）では、各問題のグループの中の平均に、その問題で生成した token の総数に比例する重みが付く。[Mercor と SkyRL の学習の手引き](https://www.mercor.com/blog/training-frontier-knowledge-work-agents-a-397b-rl-training-guide-with-skyrl/) は、Qwen3.6-35B-A3B で回答が 2k から 128k token に散らばる agent の課題を学習した比較で、batch 全体の token の平均から、問題ごとに等しく重みを付ける形に替えて、評価が 3.9 ポイント上がったと報告している。

### クリップ: 止めるか、重みを切るか、なめらかにするか

PPO と GRPO のクリップは、advantage の向きに動きすぎた token の項を平らにして、同じ生成で何度も更新するときの方策の動きを抑える。最初の更新では比 $r_{i,t}$ は 1 なのでクリップはかからず、更新を重ねて比が 1 から離れると効き始める。advantage が正の token は、比が $1+\varepsilon$ を超えるとクリップ付きの項がそれ以上増えず、確率を上げる勾配が止まる。逆の向きに動いた token には勾配が流れ続ける。推定量の補正ではなく、クリップしない代理の目的関数の下界、つまり悲観的な見積もりを取る操作である。2025 年以後の変更は、この止め方を 3 つの方向に変えた。

| 手法 | 止め方をどう変えたか |
|---|---|
| Clip-Higher（DAPO） | 上限だけを広げる。$\varepsilon = 0.2$ では、元の確率 0.01 の token は確率が 0.012 を超えるとクリップ付きの項から確率を上げる勾配が出なくなるが、元の確率 0.9 の token はほとんど制限を受けない。確率の低い token だけが強く抑えられてエントロピーが早く下がるので、上限を 0.28 にした |
| [CISPO](https://arxiv.org/abs/2506.13585)（MiniMax-M1） | 勾配を止めず、比のほうを上限で切って重みとして掛ける。振り返りの token（Wait、Recheck など）が確率の低さゆえに勾配から外れる問題を避ける |
| [SAPO](https://arxiv.org/abs/2511.20347)（Qwen） | 硬い境界をシグモイドに替え、比が 1 から離れるほどなめらかに重みを下げる。負の advantage の token に大きい温度を使う |

![advantage が正の token について、確率の比の軸の上で、クリップ付きの項から勾配が流れる範囲を並べた。GRPO と DAPO は上限を超えると勾配を止め、CISPO は上限で重みを固定したまま勾配を流し、SAPO は比が 1 から離れるほどなめらかに重みを下げる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-objectives-and-estimators/clip-shapes.png)

CISPO は、Qwen2.5-32B の base モデルの AIME 2024 の対照実験で、DAPO と同じ性能に半分の学習 step で達したと報告し、[MiniMax-M2](https://arxiv.org/abs/2605.26494) も CISPO を使っている。SAPO の著者たちは、SAPO で Qwen3-VL の系列を学習したと書いている。Magistral は上限を学習中に 0.26 から 0.28 の間で調整した。クリップと同じ場所でエントロピーを保つ工夫として、[The Entropy Mechanism の論文](https://arxiv.org/abs/2505.22617) は log 確率と advantage の共分散の大きい token の勾配を外す Clip-Cov を、[Beyond the 80/20 Rule](https://arxiv.org/abs/2506.01939) はエントロピーの上位 20% の token だけで勾配を計算する方法を提案した。

### token ごとの比か、回答ごとの比か

GRPO は確率の比を token ごとに取るが、報酬は回答全体に 1 つである。[GSPO](https://arxiv.org/abs/2507.18071) はこの食い違いを問題にし、回答全体で 1 つの比を、長さで正規化した幾何平均 $\bar{r}_i = \exp\big(\frac{1}{|o_i|}\sum_t \log r_{i,t}\big)$ として定義し、この比でクリップして回答単位で勾配に入れるかを決める。比が 1 の近くに集まるので、クリップの幅は下側 $3 \times 10^{-4}$、上側 $4 \times 10^{-4}$ と狭い。

![1 つの回答の 6 個の token のうち 1 個だけ比が大きく外れたとき、advantage が正なら、token ごとのクリップはその token だけを外し、回答ごとの幾何平均は外れを 1.08 まで薄めるが、GSPO の狭い上限を超えるので 6 個の token がまとめて外れる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-objectives-and-estimators/token-vs-sequence-ratio.png)

GSPO の論文が強調するのは MoE（Mixture of Experts）での効果である。Qwen3-30B-A3B-Base では、1 回の更新のあと、同じ回答で使われる expert の約 10% が古い方策と違い、token ごとの比が大きく揺れた。GRPO で MoE を収束させるには古い方策の expert を再現する Routing Replay が必要だったが、GSPO は回答全体の尤度だけを見るので、それなしで安定したと報告している。一方、SAPO の論文は同じ系統のモデルの数学で GSPO と Routing Replay を付けた GRPO が学習の初期に崩れたと報告し、[GMPO](https://arxiv.org/abs/2507.20673) は幾何平均を目的にしつつ、自分の目的関数では token ごとのクリップのほうが安定したと書いている。GMPO は DeepSeek-R1-Distill-Qwen-7B で 5 つの数学のベンチマークの平均 Pass@1 を GRPO の 59.3% から 63.4% に上げ、[LUSPO](https://arxiv.org/abs/2602.05261) は回答ごとの比にも長さの偏りが残ると指摘した。比の単位だけをそろえた比較は見当たらず、選び方は決着していない。この幾何平均は、log の比の平均を指数に戻した量で、回答全体の重要度の重み $\prod_t r_{i,t}$ ではなく、期待値も 1 にならない。その log を KL の推定値として読む意味は、[次の章の回答単位の判定の節](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-mismatch-async-and-opd#ずれた回答を補正するか学習から外すか)で扱う。

## KL は何を制約し何を推定するか

KL の項は方策を参照モデルから離れすぎないように抑えるが、reasoning の RL では外す報告が多い。[DeepSeek-R1](https://arxiv.org/abs/2501.12948) の論文の現在の版は、最初の RL の段で KL の係数を 0.001、クリップの幅を 10 とし、R1-Zero では 400 step ごとに参照モデルを最新の方策に置き換えた。残す場合に問題になるのは、KL の推定量の値が正しくても、それを損失として微分した勾配が正しい KL の勾配になるとは限らないことである。

外した例では、[DAPO](https://arxiv.org/abs/2503.14476) は長い思考の連鎖を学ぶ間に方策は大きく離れるのでこの制約は要らないとし、Magistral は参照モデルの複製を持つ費用に見合わないとし、[GLM-4.5](https://arxiv.org/abs/2508.06471) と Olmo 3 も外した。Olmo 3 は、それでも過剰な最適化や不安定さは起きなかったと書いている。

[Schulman のブログ](http://joschu.net/blog/kl-approx.html) は、KL を標本から推定する 3 つの量を $k_1$、$k_2$、$k_3$ と名付けた。その位置より前の問題と回答の token の列（prefix）を固定し、その位置の token $o_t$ を $\pi_\theta$ から生成し、比を $u = \pi_{\text{ref}}(o_t) / \pi_\theta(o_t)$ と書くと、$k_1 = -\log u$、$k_2 = \frac{1}{2}(\log u)^2$、$k_3 = (u - 1) - \log u$ で、DeepSeekMath の推定値は $k_3$ である。2 つの方策が共通の token の集合に正の確率を置くとき、$k_1$ と $k_3$ は $\mathrm{KL}(\pi_\theta \,\|\, \pi_{\text{ref}})$ の値の不偏推定量で、$k_2$ は値には偏りがあるが分布が近いときのよい近似である。$k_3 = k_1 + (u - 1)$ は平均 0 の $u - 1$ を制御変量として足した形である。$k_3$ は $\log u \le u - 1$ からつねに 0 以上になる。生成した token を固定して微分すると、$s_t = \nabla_\theta \log \pi_\theta(o_t)$ として、勾配の期待値は次のようになる。

| 損失に入れる量 | 生成した token を固定した勾配 | $o_t \sim \pi_\theta$ での期待値 |
|---|---|---|
| $k_1$ | $s_t$ | $0$ |
| $k_2$ | $(\log \pi_\theta - \log \pi_{\text{ref}})\, s_t$ | $\nabla_\theta \mathrm{KL}(\pi_\theta \,\|\, \pi_{\text{ref}})$ |
| $k_3$ | $(1 - u)\, s_t$ | $\nabla_\theta \mathrm{KL}(\pi_{\text{ref}} \,\|\, \pi_\theta)$ |

token を現在の方策から生成した場合、$k_3$ を損失にすると、値として推定している KL ではなく、逆向きの KL の勾配になる。古い方策から生成した token では、この等式もそのままでは成り立たない。2 つの KL は $\pi_\theta = \pi_{\text{ref}}$ の近くで 2 次まで一致するので、方策が参照モデルに近いうちは違いが小さい。DeepSeek-V3.2 は、回答を生成した方策 $\pi_{\text{old}}$（論文では推論のフレームワークが返した確率で、この章では訓練側と一致するとみなす）の token に比 $\pi_\theta / \pi_{\text{old}}$ を掛けて KL を残した。prefix を固定し、$\pi_\theta$、$\pi_{\text{ref}}$、$\pi_{\text{old}}$ が共通の token の集合に正の確率を置くとすると、期待値は $\theta$ の関数として厳密に $\mathrm{KL}(\pi_\theta \,\|\, \pi_{\text{ref}})$ になり、比も含めて微分すると、どの $\theta$ でもその勾配の不偏推定量になる。論文は、元の推定量が、更新中の方策での確率が参照モデルよりずっと低い token に上限の無い重みを与えて勾配を荒らしていたと書いていて、上の表の係数 $1 - u$ が $u$ の大きいところで際限なく大きくなることと合う。どちらの形も prefix の分布の変化による勾配は含まない。DeepSeek-V3.2 は KL の強さを分野ごとに変え、数学では弱めるか外すほうが良かったとも書いている。

KL が最適解をどう変えるかを、回答全体の分布を自由に選べる理想化した問題で見る。長さの重みとクリップを除き、回答全体の KL を使う $\mathbb{E}_\pi[R] - \beta\, \mathrm{KL}(\pi \,\|\, \pi_{\text{ref}})$ の最大化から $\pi^*(o) \propto \pi_{\text{ref}}(o)\, \exp(R(o)/\beta)$ と、参照モデルを報酬で指数的に傾けたものになる。$\beta > 0$ で参照モデルが正解と不正解の両方に確率を置く問題では、この最適な方策の正解率の log オッズは参照モデルよりちょうど $1/\beta$ 高く、参照モデルが確率を置かない回答には確率を置かない。token 単位の代理の目的関数の最適解や、KL を外した学習、有限の step の学習の結果を述べる式ではない。[Kimi k1.5](https://arxiv.org/abs/2501.12599) のように KL を直前の方策に向ける設計では、KL は参照モデルにとどめる罰則ではなく 1 回の反復で動かす幅を決める。

## 回答の数をどの問題に配るか

GRPO は問題ごとに同じ数 $G$ の回答を生成する。統計の言葉では問題を層とする層化抽出で、どの層にも同じ数を配る形である。層の重み $W_\ell$ と標準偏差 $\sigma_\ell$ で全体の平均を推定するなら、分散 $\sum_\ell W_\ell^2 \sigma_\ell^2 / n_\ell$ を総数のもとで最小にするネイマン配分 $n_\ell \propto W_\ell \sigma_\ell$ のほうが分散が小さい。ただし最適な配り方は、推定するのが正解率なのか勾配なのか、どの目的関数を上げるのかで変わる。

勾配の推定に合わせると、回答の数の多い極限での問題ごとの重みは目的関数の微分 $f'(p_q)$ になる。真の正解率 $p_q$ を基準に使え、問題の重みと 1 回の生成の費用が等しく、スコア関数の大きさ $\mathbb{E}\|s\|^2$ が問題と正誤によらないと仮定し、勾配の分散の trace の中の $-\|\nabla p_q\|^2$ の項を無視すると、配分の目安は次の形になる。$\sigma_q = \sqrt{p_q(1-p_q)}$ は問題 $q$ の報酬の標準偏差である。有限の $G$ で同じグループから基準と標準偏差を作る GRPO の最適な配分を、この式が直接与えるわけではない。

$$
n_q \propto |f'(p_q)|\, \sqrt{p_q (1 - p_q)}
$$

Dr. GRPO の $f(p) = p$ では $n_q \propto \sqrt{p_q(1-p_q)}$ で、正解率 0.5 の問題に最も多く配る。GRPO の $f(p) = 2\arcsin\sqrt{p}$ では $f'(p)\sqrt{p(1-p)} = 1$ なので、どの問題にも同じ数を配る形になる。log の目的では $n_q \propto \sqrt{(1-p_q)/p_q}$ で、難しい問題ほど多く配る。

![3 つの目的関数のもとで、スコア関数の大きさが問題と正誤によらず勾配の平均の 2 乗の項を無視できると仮定し、正解率 0.05 から 0.95 までの 10 個の値で回答の数の平均を 16 にそろえたネイマン配分の目安を並べた計算図。Dr. GRPO では正解率 0.5 の近くで 20、両端で 9、GRPO の逆正弦の目的ではどの問題も 16、log の目的では正解率 0.05 で 51、0.95 で 3 になる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-objectives-and-estimators/neyman-by-objective.png)

問題ごとに回答の数を変える研究は複数ある。[Knapsack RL](https://arxiv.org/abs/2509.25849) は総数の決まったナップサック問題として配分を解き、DeepSeek-R1-Distill-Qwen-1.5B で 6 つのベンチマークの平均を GRPO の 45.9 から 49.7 に上げた。[Reinforce-Ada](https://arxiv.org/abs/2510.04996) は、正解率の log の最大化から、推定した正解率 $\hat{p}_q$ と小さい定数 $c$ を使う配分 $N_q \propto 1/\sqrt{\hat{p}_q + c}$ を導き、回答の数を上限と下限の間に収めた。上の log の行の、正解率の小さいところの形に近い。[sGPO](https://arxiv.org/abs/2606.08854) は事前に測った正解率で回答の数を 2、4、8 と配り、Qwen2.5-Math-7B で DAPO と同程度の正答率を、事前の測定を含めて 8.9 EFLOPs（DAPO は 22.3 EFLOPs）で得た。[VSR](https://arxiv.org/abs/2610.15888) は、勾配の大きさが状態によらずそろうという仮定のもとで、回答の途中の状態についてネイマン配分の形を導き、Llama-3.2-3B-Instruct の数学で 6 つのベンチマークの平均を GRPO の 15.4 から 19.1 にした。

回答の数を確かめた公開モデルの技術報告では、MiMo-V2.6 と Nemotron 3.5 Super は 1 問に 16 個、Granite 4.2 は段ごとに 16 か 32 と、どれも問題によらない数を使っている。上の計算は近似の上の目安で、平均を引くだけの advantage を使う形では一様ではなく $\sigma_q$ に比例する配分が目安になるので、公開モデルが決まった数を使う理由をこの計算から決めることはできない。

## まとめ

GRPO 以後の目的関数の変更は、方策勾配の推定量の何を変えたかで次のように分けられる。

| 変更 | 推定量の上で変わるもの | 代価や条件 |
|---|---|---|
| 平均を引く、自分を除いて引く（RLOO） | 期待値を保ったままの勾配のばらつき。自分を含む平均は大きさが $(G-1)/G$ 倍になるだけ | 長さの重み、標準偏差、クリップ、KL を除いた回答単位の推定の場合 |
| グループの標準偏差で割る | 問題の間の重み。回答の数の多い極限で $2\arcsin\sqrt{p}$ を上げる | 有限の $G$ では係数が極限より両端で小さい |
| 全員同じ結果のグループを除く | 追加の生成の費用と全体の倍率 | KL と長さの重みを除き、問題を独立に引き直す場合、問題の間の相対的な重みは変えない |
| 長さで割る（GRPO、DAPO、Dr. GRPO） | 回答と token の重み | 正誤の報酬で長さで決まる重みの中では、不偏性と長さへの非依存は両立しない |
| クリップの形（Clip-Higher、CISPO、SAPO） | 1 回の生成で方策を動かす幅 | 推定量の補正ではない |
| 比の単位（GSPO、GMPO） | 外す単位が token か回答か | 比の単位だけをそろえた比較は見当たらない |
| KL の推定量（DeepSeek-V3.2） | 勾配が向かう KL の向き | 現在の方策から生成した場合の議論。prefix の分布の変化は補正しない |
| 回答の数の配分 | 勾配の推定の分散 | 上げたい量と、スコア関数の大きさの仮定による |

次の点は、まだ確かめられていない。

| 今後検証すること | 理由 |
|---|---|
| 比の単位だけをそろえた GSPO と token ごとの比の比較 | この章で扱った報告は、目的関数も条件も違う |
| 有限の $G$ での標準偏差の割り算の実際の効果 | 逆正弦の見方は極限の結果で、$G = 16$ では重みが違う |
| 問題ごとに回答の数を変える配分を、大きいモデルの事後学習で使った結果 | 公開モデルの報告に使用の記述が無い |
