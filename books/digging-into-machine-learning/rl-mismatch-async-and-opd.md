---
title: "RL 事後学習の訓練と推論のずれ、非同期の RL、On-Policy Distillation: 誰の確率で学ぶか"
free: false
---

## はじめに

[前の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-objectives-and-estimators)では、同じ重みなら、回答を生成したときの推論エンジンの確率と、訓練エンジンが計算し直した確率が一致するとみなした（更新による方策の変化は比 $r_{i,t}$ で扱った）。実際の RL では、回答を作る推論エンジンと勾配を計算する訓練エンジンが別の実装で、同じ重みから違う確率を出し、非同期の学習では古い重みで作った回答も使う。生徒が自分の回答の上で教師から学ぶ On-Policy Distillation（OPD）も、どのモデルの回答の上で、どのモデルの確率から学ぶかの設計である。**この章の話題はどれも、学習に使う回答をどの方策がどの確率で生成したかをそろえるか、そろわない分をどう扱うかという 1 つの問いに答えている**。

| 問い | 答え | 節 |
|---|---|---|
| 訓練と推論で確率はなぜ食い違うのか | カーネルの計算順、数値の精度、MoE の expert の選び方、サンプリングの打ち切りが報告されている | [回答を作る側と学ぶ側はなぜ食い違うか](#回答を作る側と学ぶ側はなぜ食い違うか) |
| 残ったずれをどう扱うのか | 重みを上限で切る（winsorize）か、判定の外の token を捨てる（trimming）かに分かれる。回答単位の重みは、一定の条件のもとで長さとともに分散が指数的に増える | [ずれた回答を補正するか学習から外すか](#ずれた回答を補正するか学習から外すか) |
| ずれそのものを減らせるのか | 計算をビット単位で一致させる、数値の精度を上げる、expert の経路を再生する、サンプリングの候補の集合を再生する、の 4 つがある | [ずれそのものを小さくする](#ずれそのものを小さくする) |
| 古い方策で作った回答は使えるのか | 近い方策を基準にした目的関数や、比の 2 次の moment の制約で、報告された設定の古さまでは性能を保てた | [非同期の RL](#非同期の-rl-は待ち時間とデータの古さを交換する) |
| OPD は RL と何が違うのか | 回答に 1 つの報酬ではなく、生成した token ごとに教師との確率の差を信号にする | [OPD](#opd-は生徒の回答の上で教師から学ぶ) |
| 複数の専門家を 1 つのモデルにまとめるには | 分野ごとの RL で専門家を作り、多教師の OPD で 1 つの生徒にまとめる報告が増えた | [複数の専門家をまとめる](#複数の専門家を-1-つの生徒にまとめる) |

実験の数値は各資料の報告値で、筆者は再現していない。有効標本数の計算図、判定の範囲の図の閾値、advantage の下限の約 −0.22 は、筆者による説明用の計算である。数理統計の部分は、重点抽出法とラオ・ブラックウェルの定理の知識で追えるように書く。2025 年後半以後の主な公開モデルの組み合わせは、[次の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-open-models-and-open-problems)の比較表にまとめる。

## 回答を作る側と学ぶ側はなぜ食い違うか

RL の学習の系では、推論エンジン（vLLM、SGLang など）が回答とそのときの確率を作り、訓練エンジン（Megatron、FSDP など）がその回答の上で確率を計算し直して勾配を求める。2 つは同じ重みを持っていても計算の実装が違うので、同じ token に違う確率を付ける。

| 確率 | 誰が出すか | 記号 |
|---|---|---|
| 推論エンジンが生成のときに出した確率 | 推論エンジン | $\mu_{\theta_{\text{old}}}$ |
| 同じ重みで訓練エンジンが計算し直した確率 | 訓練エンジン | $\pi_{\theta_{\text{old}}}$ |
| 更新中の方策の確率 | 訓練エンジン | $\pi_\theta$ |

前の章の比 $r_{i,t} = \pi_\theta / \pi_{\theta_{\text{old}}}$ は、1 回の更新で方策を動かしすぎないための比だった。ずれを扱うのはもう 1 つの比 $\rho_{i,t} = \pi_{\theta_{\text{old}}}(o_{i,t}) / \mu_{\theta_{\text{old}}}(o_{i,t})$ で、2 つのエンジンが一致していればどの token でも 1 になる。古いデータを現在の方策に照らす比 $\pi_\theta / \mu_{\theta_{\text{old}}}$ は、$\rho_{i,t}$ と $r_{i,t}$ の積である。

![推論エンジンが回答と生成のときの確率を出し、訓練エンジンが同じ重みで確率を計算し直す。ずれの出どころは計算の順序、BF16 の丸め、出力層の精度、expert の選択、サンプリングの打ち切りで、それぞれにずれを小さくする方法が対応する。方策の古さは重みが違う別の種類のずれで、残ったずれは比で重みを付けるか学習から外す](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mismatch-async-and-opd/mismatch-sources.png)

[Yao らのブログ](https://fengyao.notion.site/off-policy-rl) は、同じ重みでも、ある token では推論側がほぼ 1、訓練側がほぼ 0 の確率を出すほど食い違うと報告した。建前では on-policy の RL が、実際には別の方策で集めたデータで学習する off-policy の RL になっている。原因として報告されているのは 5 つである。[Thinking Machines のブログ](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/) は、推論の非決定性の主な原因が batch の大きさで計算の順序が変わるカーネルにあると示した。[FP16 の論文](https://arxiv.org/abs/2510.26788) は BF16 の丸め誤差を、[MiniMax-M1](https://arxiv.org/abs/2506.13585) は出力層の精度を挙げた。MoE では、小さな数値の差で 2 つのエンジンが同じ token に別の expert を選ぶことがある。推論で top-p や top-k によって候補を狭めると、推論側の確率は残った候補の中で正規化し直されるので、同じ計算をしても $\rho_{i,t}$ は 1 にならない。

## ずれた回答を補正するか学習から外すか

ずれが残るときの扱いは、重点抽出法の 2 つの操作に分かれる。重みを上限で切って使う方法（winsorize）と、判定の範囲の外の token や回答を捨てる方法（trimming）で、それぞれ違う偏りと分散を引き受ける。

### 重みの分散は χ² ダイバージェンスで、回答が長いと爆発する

推論側の確率 $\mu$ から得た標本で訓練側の方策 $\pi$ の期待値を推定するには、$\pi$ が確率を置く token には $\mu$ も確率を置く（$\pi \ll \mu$）とき、重み $w = \pi / \mu$ を掛ければよい。top-p や top-k で推論側だけが候補を外すと、この条件は崩れる。重みの平均は 1、分散は $\operatorname{Var}_\mu(w) = \chi^2(\pi \,\|\, \mu)$ で、$n$ 個の標本の有効標本数の目安は $n / (1 + \chi^2)$ である。これは母集団の 2 次の moment から決まる目安で 1 を下回ることもあり、観測した重みから計算する標本の有効標本数（重みの和が正なら 1 以上 $n$ 以下）とは別のものである。

回答全体の重みは token ごとの比の積 $\prod_{t=1}^{L} \rho_t$ である。固定長 $L$ の回答で、どの prefix でも次の token の分布の $\chi^2$ が一様に $\kappa$ 以上なら、条件付き期待値を後ろから順に取ると、独立を仮定しなくても次の不等式が成り立つ。

$$
\mathbb{E}_\mu\Big[\prod_{t=1}^{L} \rho_t^2\Big] \ge (1+\kappa)^L
$$

![回答の長さと、各位置の条件付き χ² の一様な下限の組み合わせで、16 個の回答の母集団の有効標本数の目安の上限を計算した表。下限が 0.001 なら、100 token では 14.5、1,000 token では 5.9、10,000 token では 0.001 を下回る](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mismatch-async-and-opd/is-ess.png)

下限が $10^{-3}$ でも、長さ 10,000 token の回答では $\mathbb{E}[w^2] \ge (1.001)^{10000} \approx 2.19 \times 10^4$ となり、16 個の回答の有効標本数の目安は 0.001 を下回る。この分散の増大は、回答単位の重みをそのまま使いにくい理由の 1 つで、この章で扱う手法は token ごとの比を使い、上限で切るか、範囲の外を捨てる。token ごとの比だけでは prefix の分布の違いは補正されず、その偏りは切る、捨てるとは別に残る。

### 重みを切る TIS と、token を外す IcePop

| 操作 | 手法 | 推定量の偏りの源 |
|---|---|---|
| winsorize | TIS（ずれの比 $\rho$ を $\min(\rho, C)$ で切る） | 上限を超えた重みの切られた分 |
| trimming | IcePop、MiMo-V2.6 の 4 つの境界、KPop、IPO | 判定の外の寄与。外す token がまれでも、その token のスコア関数が大きければ偏りは小さくない |

Yao らのブログの Truncated Importance Sampling（TIS）は、GRPO の token ごとの損失に上限 $C$ で切った比 $\min(\rho_{i,t}, C)$ を掛けた。重みを上限で切る推定量は、重点抽出法では [Ionides（2008）](https://doi.org/10.1198/106186008X320456) の truncated importance sampling として調べられていて、重みの分散は $C^2$ で抑えられる。Olmo 3 が TIS を採用している。前の章の CISPO も重みを切る winsorize だが、切るのは更新の比 $r$ で、ずれの比 $\rho$ ではない。

[IcePop](https://arxiv.org/abs/2510.18855)（Ring-1T の報告）は、比が範囲 $[c_{\text{low}}, c_{\text{high}}]$ の外にある token の勾配を 0 にする。

$$
\mathcal{M}(\rho_{i,t}) = \begin{cases} \rho_{i,t}, & c_{\text{low}} \le \rho_{i,t} \le c_{\text{high}} \\ 0, & \text{otherwise} \end{cases}
$$

Ring-1T は $c_{\text{low}} = 0.5$、$c_{\text{high}} = 5$ とし、TIS はずれの大きい token にも係数を掛けて更新を続けるので小さな乱れが積み重なると批判し、Ring-mini-2.0 で AIME25 の正答率が TIS を上回ったと報告した。範囲の外の token を外す masking は広く使われていて、[GLM-5](https://arxiv.org/abs/2602.15763) は IcePop を、[INTELLECT-3](https://arxiv.org/abs/2512.16144) は更新中の方策の訓練側の確率と推論側の確率の比を、[Kimi K2.5](https://arxiv.org/abs/2602.02276) は advantage の符号によらず log の比の範囲を判定に使い、[Nemotron 3](https://arxiv.org/abs/2512.20856) は masked importance sampling と書いている。

### 判定の量を変える: KPop と IPO

KPop と IPO は、確率の比の帯の代わりに、生成した token の確率の差や 2 択の分布の距離で判定する。以下では、生成した token の訓練側の確率を $a = \pi_{\theta_{\text{old}}}(o_t)$、推論側の確率を $b = \mu_{\theta_{\text{old}}}(o_t)$ と書く。IcePop を提案した Ant の Ling チームは、[Ling と Ring 2.6 の報告](https://arxiv.org/abs/2606.15079) で、帯は確率の高い token にも低い token にも同じ割合のずれを想定していて、確率の低い token を外しすぎると書いた。

| 判定 | 判定する量 | 閾値 |
|---|---|---|
| IcePop | 比 $\rho = a / b$ の帯 | 下限と上限の 2 つ |
| KPop（Ling と Ring 2.6） | 生成した token とそれ以外の 2 択の分布の、2 つの向きの KL がどちらも $\phi$ 以下か | $\phi$ の 1 つ（報告に値は無い） |
| IPO（prime-rl の v0.10.0） | 確率の差 $\lvert a - b \rvert$ | 1 つ |

統計の言葉では、語彙を生成した token とそれ以外に分けると $a$ と $b$ は 2 つのベルヌーイ分布を定め、KPop の量はその KL、IPO の量はその全変動距離にあたる。Pinsker の不等式 $|a - b| \le \sqrt{\mathrm{KL}/2}$ から、KPop が閾値 $\phi$ で残す token は、IPO の閾値を $\sqrt{\phi/2}$ にしても残る。

![推論側と訓練側の確率を両軸の対数目盛りに取り、比の帯、確率の差、2 択の KL のそれぞれで学習に残る範囲を塗った計算図。閾値は帯が 0.5 から 5、差が 0.3、KL が仮に 0.05。比の帯は対角線に沿った一定の幅で、確率の差は両方が小さい領域をまとめて残し、2 択の KL は確率が小さいほど比の幅を広く許す](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mismatch-async-and-opd/mask-shapes.png)

[prime-rl の v0.10.0](https://github.com/PrimeIntellect-ai/prime-rl/releases/tag/v0.10.0) は、比で判断すると、推論側 0.01 に対して訓練側 0.06 の珍しい token は 6 倍として外され、推論側 0.90 に対して訓練側 0.20 の token は 0.22 倍として残る場合があると例を挙げた。後者が残るのは帯の下限が 0.22 より小さい場合で、帯を $[0.5, 5]$ にすれば外れる。GLM-4.5-Air のソフトウェア工学の agent の RL（router replay なし、最大 64 step 遅れたデータを使う設定）では、IcePop は 1,200 step のあたりで訓練と推論の KL が 20 倍を超えて膨らんで崩れ、IPO は閾値 0.2 でも 0.3 でも学習が続いたと報告している。比の帯そのものを工夫する報告もある。[MiMo-V2.6](https://arxiv.org/abs/2610.11959) は $\pi_{\theta_{\text{old}}}$ を計算せずに比 $\pi_\theta / \mu_{\theta_{\text{old}}}$ を使い、advantage が正の token と負の token で別々の帯（どちらも $[0.2, 5]$ から始める）で判定する。方策のエントロピーが低すぎれば正の側の帯を広げて負の側を狭め、高すぎれば逆にする。GLM のチームの [SAO](https://arxiv.org/abs/2607.07508) も、推論側の確率で直接割る形を提案した。

### 回答単位で外す: log の比の平均の閾値判定

[DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) は、advantage が負で、生成された token について平均した $\log(\mu_{\theta_{\text{old}}} / \pi_\theta)$ が閾値 $\delta$ を超える回答の、方策の項だけを損失から外し、KL の項は残す。本文には $\delta$ の値も効果を比べた表も見当たらない。[Step 3.5 Flash](https://arxiv.org/abs/2602.10604) の MIS-PO は、token の単位では $\rho_{i,t}$ を $[0.5, 2]$ で、回答の単位ではその幾何平均を $[0.996, 1.001]$ で判定し、残した token には比の重みを掛けずに実質的に on-policy として扱う。回答単位の判定には、どれも重要度の重みではなく log の比の回答に沿った平均が現れる。prefix $h$ を固定すると、推論側から token を生成したとき $\mathbb{E}_\mu[\log \rho_t \mid h] = -\mathrm{KL}(\mu(\cdot \mid h) \,\|\, \pi(\cdot \mid h))$ なので、log の比の平均は token ごとの KL の推定値を回答に沿って平均したものと読め、これらの masking はその閾値判定である。比べる方策と比の向きは手法で違い、MIS-PO は $\rho$、DeepSeek-V3.2 は $\log(\mu_{\theta_{\text{old}}} / \pi_\theta) = -\log(r \rho)$、前の章の GSPO は更新の比 $r$ を使う。

比が 1 に近いときは、KL、log の比の 2 乗の平均、$\chi^2$ は定数倍の違いになる（$\mathrm{KL} \approx \frac{1}{2}\mathbb{E}[\varepsilon^2]$、$\mathbb{E}[(\log \rho)^2] \approx \mathbb{E}[\varepsilon^2] = \chi^2$、$\rho = 1 + \varepsilon$）。非同期の節で見る M2PO が log の比の 2 乗の平均を使うのは、比が 1 の近くでそれが重みの分散に一致するからと読める。[Trust Region Masking の論文](https://arxiv.org/abs/2512.23075) は、目的関数の近似の誤差の上界が回答の中で最もずれの大きい token で決まるので、token ごとのクリップでは抑えられないと示し、回答全体を外す masking を提案している。

## ずれそのものを小さくする

重みで扱う前に、2 つのエンジンの計算の順序、数値の精度、expert の経路、サンプリングの候補の集合をそろえれば、ずれそのものを小さくできる。4 つは別の操作で、それぞれ違う原因を受け持つ。

| 操作 | 受け持つ原因 | 例 |
|---|---|---|
| ビット単位の一致 | カーネルの計算順 | Thinking Machines、vLLM と TorchTitan、DeepSeek-V4、Step-5-Preview |
| 精度を上げる | 丸め誤差、出力層の活性 | FP16 の論文、MiniMax-M1 の出力層の FP32 |
| expert の経路の再生 | MoE の expert の選び方 | R3（Rollout Routing Replay）、DeepSeek-V3.2 の Keep Routing |
| 候補の集合の再生 | top-p と top-k の打ち切り | DeepSeek-V3.2 の Keep Sampling Mask |

Thinking Machines のブログは、RMSNorm、行列積、attention を batch の大きさに依存しない形で実装した。同じブログの RL の実験では、重要度の重みで補正しないと報酬が崩れ、補正すると推論と訓練の KL は 0.001 付近にとどまり、2 つのエンジンをビット単位で一致させると KL は 0 のまま、補正なしで学習できた。費用もあり、Qwen3-8B で 1000 本の短い回答を生成する時間は 26 秒から 42 秒に伸び、[vLLM と TorchTitan のチーム](https://vllm.ai/blog/2025-11-10-bitwise-consistent-train-inference) は Qwen3 1.7B の RL を、一致させない場合の 2.4 倍の時間で動かした。[Step-5-Preview のブログ](https://platform.stepfun.ai/blog/inside-step-5-preview) は、データの古さが 0 の場合に、モデルを GPU に分ける数が訓練で 1、推論で 8 と違ってもビット単位で一致させ、追加の時間を 10% 未満に抑えたと書いている。古さが 0 より大きい場合は、expert の経路と候補の集合を再生する。測った構成が違うので、費用の数字は互いに比べられない。FP16 の論文は、学習と推論の両方を BF16 から FP16 にし、表せる値の範囲の狭さを loss scaling で補う設定で、検証した dense と MoE のモデルのずれが約 24 分の 1 になったと報告した。

[R3 の論文](https://arxiv.org/abs/2510.11370) は、推論エンジンが生成のときに選んだ expert を記録し、訓練エンジンでその選択を再現する。前の章の GSPO の Routing Replay が更新による expert の変化を扱うのに対し、R3 は訓練と推論の実装の違いによる変化を扱う。Qwen3-30B-A3B の訓練と推論の KL の推定値は約 $1.5 \times 10^{-3}$ から $7.5 \times 10^{-4}$ に下がり、dense の Qwen3-8B の $6.4 \times 10^{-4}$ に近づいた。Snowflake の研究者による [FlashNext-RL](https://arxiv.org/abs/2610.28998) は、BF16 でのオフラインの測定で、expert の経路の食い違いが訓練と推論のずれの約 96% を説明したと書いている。DeepSeek-V3.2 は推論で外した候補を訓練でも外す Keep Sampling Mask を加え、2 つの方策が同じ候補の集合を共有するようにした。学習基盤での実装と測定は、[次の章の SkyRL の節](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-open-models-and-open-problems#大規模に動かすと時間と費用と精度が問題になる)にまとめる。

## 非同期の RL は待ち時間とデータの古さを交換する

長い回答の生成では、最も長い 1 本を待つ間、他の GPU が止まる。非同期の RL は生成と学習を並行に回して待ち時間を減らし、代わりに古い方策で作った回答と、先に終わる短い回答の偏りを管理する。

![左は同期の RL で、生成、採点、学習、重みの同期を順に行い、最も長い回答を待つ間に GPU が止まる。右は非同期の RL で、1 生成、2 採点と学習、3 重みの同期が並行に進み、学習には古い方策で作った回答と、先に終わった短い回答が混ざる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mismatch-async-and-opd/sync-vs-async.png)

1. 推論エンジンが、手元の重みで回答を生成し続ける。
2. 採点が終わった回答から訓練エンジンが学習する。そこには古い重みで作った回答と、先に終わった短い回答が多く混ざる。
3. 更新した重みを推論エンジンに送る。送る間も生成を止めない方式もある。

[AReaL](https://arxiv.org/abs/2505.24298) は、許す古さの上限を決め、decoupled PPO を使った。回答を作った古い方策 $\pi_{\text{behav}}$（推論側の確率を使う場合はこの章の $\mu$ にあたる）と直近の方策 $\pi_{\text{prox}}$ の違いは重要度の重み $\pi_{\text{prox}} / \pi_{\text{behav}}$ で扱い、更新の幅は $\pi_\theta / \pi_{\text{prox}}$ のクリップで抑える。1.5B のモデルの数学で、この目的関数を使わないと古さの上限 4 で AIME24 が 23.3 に落ち、使うと 42.2 を保った（同期の学習は 42.0）。上限 16 では 38.7 に下がった。学習の全体の時間は、論文の評価の設定で、同じ GPU の数の同期の系に比べて最大 2.77 倍速くなった。[PipelineRL](https://arxiv.org/abs/2509.19128) は生成を止めずに重みを配る in-flight の更新で、128 枚の GPU で、安定して動いた最大の 32 回の更新を 1 回の生成に使う従来の RL と比べ、同じ報酬に約 2 倍速く達した。[M2PO](https://arxiv.org/abs/2510.01161) は log の比の 2 乗の平均を閾値以下に保つように外れの大きい token だけを捨て、少なくとも 256 回前の更新で作ったデータだけでも on-policy に匹敵する性能を保ったと報告した。

短い回答の偏りにも対策がある。[DeepSeek-V4](https://arxiv.org/abs/2606.19348) は、生成の途中で中断された回答を最初から作り直すと短い回答ほど中断を逃れやすくモデルが短い回答に偏るので、生成済みの token から再開する。[DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/DeepSeek_V41_Tech_Report.pdf) は、学習が始まるまでに終わる回答に短いものが多くなる偏りと、古すぎる token の寄与を外す masking を書いている。

## OPD は生徒の回答の上で教師から学ぶ

On-Policy Distillation（OPD）は、生徒のモデルが自分で回答を生成し、その回答の上で教師のモデルの確率から学ぶ。回答に 1 つの報酬を与える RL と違い、生成した token ごとに教師との確率の差を信号にする。

| 方法 | 回答を書くのは | 信号 |
|---|---|---|
| off-policy の蒸留（教師の回答で SFT） | 教師 | 教師の回答の token |
| 回答に 1 つの報酬の RL | 生徒 | 回答全体に 1 つの報酬 |
| OPD | 生徒 | 生成した token ごとの、教師と生徒の確率の差 |

![off-policy の蒸留は教師が書いた回答で生徒を学習し、回答に 1 つの報酬を与える RL は、その報酬から作る advantage を全 token に配り、生成した token を使う OPD は、生徒の回答の token ごとに教師の確率との差を信号にする](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mismatch-async-and-opd/sft-rl-opd.png)

off-policy の蒸留では、生徒は教師が書いた prefix の上で学ぶので、生徒が実際に生成する prefix とずれうる。[GKD](https://arxiv.org/abs/2306.13649) は生徒が自分で生成した回答の上で分布を近づける OPD を提案し、生徒が生成した回答の割合と使うダイバージェンスを選べる形にした。[MiniLLM](https://arxiv.org/abs/2306.08543) は生徒の分布で測った教師との KL（reverse KL）を方策勾配で最小化した。いまの OPD の多くは RL の枠組みをそのまま使い、教師 $\pi_T$ が生徒の回答の各 token の確率を 1 回の forward で計算して、token ごとの advantage を次の値にする。

$$
\hat{A}^{\text{OPD}}_t = \operatorname{sg}\left[ \log \pi_T(o_t \mid q, o_{<t}) - \log \pi_\theta(o_t \mid q, o_{<t}) \right]
$$

$\operatorname{sg}$ は括弧の中を微分のときに定数として扱う操作である。[Qwen3 の技術報告](https://arxiv.org/abs/2505.09388) は、数学とコードの問題に絞り、off-policy の蒸留を終えた同じ Qwen3-8B から出発して、AIME'24 を RL で 55.0 から 67.6 に、OPD で 74.4 に上げ、GPU 時間は RL の 17,920 時間に対して OPD は 1,800 時間だったと報告した。64 回のうち 1 回でも解ける割合の pass@64 は、RL では 90.0 のまま、OPD では 93.3 に上がった。[Thinking Machines の OPD のブログ](https://thinkingmachines.ai/blog/on-policy-distillation/) は別の実験で、Qwen3-8B-Base を約 40 万件の prompt で SFT して AIME'24 を 60% にしたあと、OPD を約 150 step 回して 70% に上げた。[RLVR が base model の能力を超えるかを調べた論文](https://arxiv.org/abs/2504.13837) も、蒸留は教師から新しい reasoning のパターンを持ち込めると報告している。

### 生成した token の信号と、語彙全体の信号

生成した token の log の比だけを使うか、語彙全体の分布で KL を計算するかで、信号のばらつきが変わる。prefix $h$ を固定した 1 つの位置で、生成した token の推定量 $X = \hat{A}^{\text{OPD}}_t\, \nabla_\theta \log \pi_\theta(o_t \mid h)$ の条件付き期待値は $-\nabla_\theta \mathrm{KL}(\pi_\theta(\cdot \mid h) \,\|\, \pi_T(\cdot \mid h))$ で、語彙全体の蒸留はこれを語彙の和で厳密に計算する。つまり語彙全体の信号は生成した token の信号の条件付き期待値で、全分散の公式から、その位置の項の分散は大きくならない（ラオ・ブラックウェルの定理の形）。[DeepSeek-V4](https://arxiv.org/abs/2606.19348) が、生成した token の方法を勾配の推定の分散が大きいと評価して語彙全体の蒸留を選んだことの、1 つの位置での裏付けにあたる。

この保証は 1 つの位置の項についてのもので、後の位置の項は前の位置で引いた token に依存するので、回答全体の和の分散が小さくなるとはこの定理だけでは言えない。教師の上位の token だけで和を取る top-k の蒸留は、外した token に生徒が大きな確率を置いていれば偏りを持つ近似である。Kimi K3 は top-k の蒸留も試したが、自分の設定では生成した token の方法とはっきりした差は無かったと書いている。どの形も、$\theta$ が変わると後の prefix の分布も変わることによる勾配を含まず、Thinking Machines は割引率を 0 とし、正にしても性能は上がらなかったと書いている。

### OPD の限界

[Revisiting On-Policy Distillation](https://arxiv.org/abs/2603.25562) は、生成した token だけを使う OPD の失敗として、token ごとの信号の大きさの偏り、生徒が作った prefix の上で教師の確率が当てにならないこと、tokenizer や特殊 token の不一致を挙げ、教師の上位 $K$ 個の token の集合で分布を比べる方法を提案した。数学と agent の課題を交互に学習する設定で、数学の平均は標準の OPD の 34.8 に対して 41.7 だった。標準的な OPD は教師と生徒が同じ tokenizer を持つことを前提にし、[Breaking the Tokenizer Barrier](https://arxiv.org/abs/2606.09456) は token の対応付けで系列の違うモデルの間に広げた。[A Distribution-Matching Perspective](https://arxiv.org/abs/2610.33740) は、論文の枠組みのもとで、OPD が有利なのは生徒の分布が目標に近いときだと論じている。外部の教師を持たず、正解などの追加の情報を与えた自分自身から学ぶ On-Policy Self-Distillation では、[Thinking Collapse の論文](https://arxiv.org/abs/2607.10805) が、数学で振り返りの token が抑えられて性能が下がる現象を報告している。

## 複数の専門家を 1 つの生徒にまとめる

OPD は小さいモデルを作るためだけでなく、分野ごとに RL で鍛えた専門家を 1 つのモデルにまとめるためにも使われている。分野ごとの RL が個別の能力を作り、多教師の OPD がそれを 1 つの生徒に移す、という分担である。この節は仕組みを扱い、モデルごとの組み方は次の章で比べる。

![SFT のあと、分野ごとに RL で専門家を作り、生徒が自分の回答を生成して、prompt の分野に対応する専門家の token ごとの確率から学ぶ模式図。MiMo-V2-Flash と Kimi K3 は生成した token の log の比を、DeepSeek-V4 は語彙全体の分布を使い、MiMo-V2-Flash は回答の正誤から作る advantage も足す。GLM-5 と Nemotron-Cascade 2 は前の段のチェックポイントを教師にする](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mismatch-async-and-opd/multi-teacher-opd.png)

[MiMo-V2-Flash](https://arxiv.org/abs/2601.02780) はこの形を Multi-Teacher On-Policy Distillation（MOPD）と呼び、prompt の分野に対応する教師の log の比を token ごとの advantage にし、回答の正誤から作る GRPO の advantage と組み合わせた。[MOPD の論文](https://arxiv.org/abs/2606.30406) は、Qwen3-30B-A3B で数学、指示追従、ソフトウェア工学の 3 分野をまとめ、生徒の SFT の時点を 0、教師を 1 とする正規化したスコアで 0.937 を得た。ただし AIME25 では RL で作った教師を 3.33 下回るなど、分野ごとの値ではすべてで最良ではない。前の段のチェックポイントを教師にする形もあり、[GLM-5](https://arxiv.org/abs/2602.15763) は順に学習する間に失われた能力を最後の段の OPD で取り戻し、[Nemotron-Cascade 2](https://arxiv.org/abs/2603.19220) は各段で最も強い中間のモデルを分野ごとの教師にし、数学だけの学習で、GRPO が 25 step で AIME25 を 89.9 から 91.0 に、MOPD は 30 step で 92.0 に上げたと報告した。step の数での比較で、教師の forward を含む GPU 時間の比較ではない。

教師の負の信号には下限を付ける工夫がある。[Nemotron 3.5 Super](https://arxiv.org/abs/2610.38095) の OPD の advantage は、教師の確率 $\pi_T$ と更新の前に固定した生徒の確率 $\bar{\pi}_S$ を使った $\log \pi_T - \log \bar{\pi}_S$ で、教師がほとんど出さない token ではいくらでも大きな負の値になる。2 段目では教師を $\alpha \pi_T + (1-\alpha) \bar{\pi}_S$（$\alpha = 0.2$）に替えるので、advantage は $\log(1-\alpha) \approx -0.22$ より下がらない。固定した生徒と混合の KL は [Lee（1999）](https://aclanthology.org/P99-1004/) の skew divergence と同じ形で、目標の分布を生徒の側に縮める操作と読める。報告は、生徒が 1 段目の教師の能力を取り込んだあとの学習がこれで安定したと書いている。2025 年後半以後の主な公開モデルの組み方は、[次の章の比較表](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-open-models-and-open-problems#公開モデルは事後学習をどう組んでいるか)にまとめる。

## まとめ

| 扱い | 受け持つ問題 | 代価や条件 |
|---|---|---|
| 重みを上限で切る（TIS） | 重みの分散 | 切った分の偏り |
| 判定の外を捨てる（IcePop、KPop、IPO、回答単位の masking） | 大きくずれた token や回答 | 捨てた部分の偏り。判定の量で残る範囲が違う |
| ビット単位の一致、経路と候補の再生 | ずれの原因そのもの | 生成と学習の時間 |
| 非同期の目的関数（AReaL、M2PO） | 古い方策の回答 | 許せる古さには限度がある |
| OPD（生成した token、語彙全体） | token ごとの密な信号 | 教師の forward の費用、同じ tokenizer |
| 多教師の OPD | 専門家の統合 | 分野ごとの値ではすべてで最良とは限らない |

| 今後検証すること | 理由 |
|---|---|
| KPop、IPO、IcePop を同じモデルと課題と学習の条件で比べた結果 | IPO と IcePop の比較はあるが、KPop を含む 3 つをそろえた比較は見当たらない |
| 語彙全体の OPD が回答全体の勾配の分散をどれだけ減らすか | 保証は 1 つの位置の項に限られる |
| OPD と RL を GPU 時間でそろえた比較の追試 | 報告の多くは step の数での比較である |
