---
title: "訓練と推論のずれと On-Policy Distillation: GRPO のあとの RL の土台を論文から読む"
free: false
---

## はじめに

前の章「GRPO のあとで RL の目的関数は何が変わったか」では、GRPO の式の 5 つの部品がどう差し替えられたかを見た。2025 年の後半からの公開モデルの技術報告には、式の外にある 2 つの話題が増えている。1 つ目は、回答を生成する推論エンジンと、勾配を計算する訓練エンジンが、同じ重みから違う確率を出す問題である。2 つ目は、分野ごとに RL で鍛えた複数のモデルを 1 つにまとめる方法で、ここに On-Policy Distillation（OPD）が使われるようになった。OPD では、生徒のモデルが自分で回答を生成し、その回答の上で教師のモデルの確率から学ぶ。**2 つの話題を読むときの共通の視点は、学習に使う回答をどの方策がどの確率で生成したか**である。この章では、この 2 つを論文と技術報告から整理する。

合わせて、この章では問いを次の 6 つに分ける。表の右の列は、章の結論を先に並べたものである。

| 問い | 論文から読める答え |
|---|---|
| 訓練と推論で確率はなぜ食い違うのか | カーネルの計算順が batch の大きさで変わること、BF16 の丸め誤差、出力層の精度、MoE の expert の選び方の違い、サンプリングの打ち切りが報告されている |
| 残ったずれをどう扱うのか | 確率の比で重みを付ける方法（TIS）と、比が範囲を外れた token や回答を学習から外す方法（IcePop、DeepSeek-V3.2 の回答単位の masking など）に分けて整理できる |
| ずれそのものを減らせるのか | 推論と訓練をビット単位で一致させた [Thinking Machines の実験](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/) では、推論側と訓練側の確率の KL が 0 のまま学習できた。FP16 と、推論のときの expert の記録と再現は、報告された設定でずれを小さくした |
| 古い方策で作ったデータは使えるのか | 非同期の RL では、近い方策を基準にした目的関数や、比の 2 次の moment の制約で、報告された設定の古さまでは性能を保てた |
| OPD は RL と何が違うのか | 回答に 1 つの報酬を与える RL と違い、生成した token ごとに教師との確率の差を信号にする。[Qwen3 の数学とコードの比較](https://arxiv.org/abs/2505.09388) では、同じ蒸留済みの 8B のモデルから、OPD が RL の約 10 分の 1 の GPU 時間で、RL より高い評価値を得た |
| 公開モデルは OPD をどう使っているか | MiMo-V2-Flash、Kimi K3、DeepSeek-V4、Nemotron 3 Ultra は、分野ごとの教師から 1 つの生徒への多教師の OPD を報告している。GLM-5 は前の段のチェックポイントを、Nemotron-Cascade 2 は学習の各段の中間のモデルを教師にする |

読者には、前の章の GRPO の式と、KL ダイバージェンスの知識を想定する。扱う資料は 2023 年 6 月の GKD から 2026 年 10 月までに arXiv などに出た論文、技術報告、ブログである。数値はすべて各資料の報告値で、筆者はどれも再現していない。前の章と同じく、reasoning はそのまま書き、学習済みのモデルで回答を生成することを「推論」と書く。

## 同じ重みで確率が食い違う

いまの RL の学習システムでは、回答の生成を vLLM や SGLang のような推論エンジンが受け持ち、勾配の計算を Megatron-LM や FSDP のような訓練エンジンが受け持つ。2 つは同じ重みを持つが、計算の実装が違う。[Yao らのブログ](https://fengyao.notion.site/off-policy-rl) は、同じ重みでも 2 つのエンジンの token の確率が大きく違い、ある token では推論側がほぼ 1、訓練側がほぼ 0 の確率を出すほど食い違うと報告した。このとき、建前では on-policy の RL が、実際には別の方策で集めたデータで学習する off-policy の RL になっている。

![推論エンジンが回答と生成のときの確率を出し、訓練エンジンが同じ重みで確率を計算し直す。ずれの出どころは計算の順序、BF16 の丸め、出力層の精度、expert の選択、サンプリングの打ち切りで、それぞれにずれを小さくする方法が対応する。方策の古さは重みが違う別の種類のずれで、残ったずれは比で重みを付けるか学習から外す](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mismatch-and-on-policy-distillation/mismatch-sources.png)

ずれの出どころとして、次のものが報告されている。最後の行の方策の古さは、同じ重みのずれではなく、重みそのものが違うことによるずれだが、同じ仕組みで扱われることが多いので並べる。

| 出どころ | 何が起きるか | 報告 |
|---|---|---|
| 計算の順序 | 推論のカーネルは batch の大きさによって足し算の順序を変え、浮動小数点の足し算は順序で結果が変わる | [Thinking Machines のブログ](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/) で、Qwen3-235B-A22B-Instruct-2507 に 1 つの prompt を温度 0 で 1000 回与えると 80 通りの回答が出た |
| 数値の精度 | BF16 の仮数部は 7 bit で、丸め誤差が自己回帰の生成で積み重なる | [FP16 の論文](https://arxiv.org/abs/2510.26788) |
| 出力層の精度 | 出力層の大きな活性で、訓練と推論の確率がずれる | [MiniMax-M1](https://arxiv.org/abs/2506.13585) が出力層を FP32 にして揃えた |
| expert の選択 | MoE では、小さな数値の差で選ばれる expert が変わり、確率が大きくずれる | [R3 の論文](https://arxiv.org/abs/2510.11370) で、訓練と推論の確率の KL の推定値が Qwen3-30B-A3B で $1.535 \times 10^{-3}$、dense の Qwen3-8B で $6.4 \times 10^{-4}$ |
| サンプリングの打ち切り | 推論で top-p や top-k で外した token を、訓練の確率の計算では外さない | [DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) |
| 方策の古さ | 1 回の生成を複数の minibatch の更新に使う、または生成と学習を非同期に回す | DeepSeek-V3.2、[AReaL](https://arxiv.org/abs/2505.24298) |

## 3 つの確率と 2 つの比

ずれを扱う論文の式を読むために、確率を 3 つに分ける。前の章では、回答を生成した方策と、勾配の計算で基準にする古い方策を区別せず、どちらも $\pi_{\theta_{\text{old}}}$ と書いた。この章では両者を分け、推論エンジンが生成のときに出した確率を $\mu_{\theta_{\text{old}}}$、同じ重みで訓練エンジンが計算し直した確率を $\pi_{\theta_{\text{old}}}$、更新中の方策の確率を $\pi_\theta$ と書く。前の章の GRPO の比 $r_{i,t}(\theta)$ は、$\pi_\theta$ を $\pi_{\theta_{\text{old}}}$ で割った比で、1 回の更新で方策を動かしすぎないためのものだった。ずれを扱うのは、もう 1 つの比である。

$$
\rho_{i,t} = \frac{\pi_{\theta_{\text{old}}}(o_{i,t} \mid q, o_{i,<t})}{\mu_{\theta_{\text{old}}}(o_{i,t} \mid q, o_{i,<t})}
$$

この比は、実際に生成された token について、同じ重みで、訓練エンジンが推論エンジンと比べてその token をどれだけ出しやすいと見ているかを表す。2 つのエンジンが一致していれば $\rho_{i,t}$ はどの token でも 1 で、$r_{i,t}$ だけを考えればよい。推論で top-p や top-k によって候補を狭めると、推論側の確率は残った候補の中で正規化し直され、訓練側の方策は外された token にも確率を置く。そのため、2 つのエンジンが同じ計算をしても $\rho_{i,t}$ は 1 にならず、打ち切りの範囲も揃える必要がある。古いデータを現在の方策に照らすときの比 $\frac{\pi_\theta}{\mu_{\theta_{\text{old}}}}$ は、$\rho_{i,t}$ と $r_{i,t}$ の積で、2 つのずれを両方含む。

## 比で重みを付けるか、学習から外すか

Yao らのブログは Truncated Importance Sampling（TIS）として、GRPO の token ごとの損失に、上限 $C$ で切った比 $\min(\rho_{i,t}, C)$ を掛けた。重要度の重みでずれの影響を和らげ、上限で重みの分散を抑える近似的な方法で、上限で切るぶん厳密な補正からは外れる。前の章の CISPO も重みを切るが、CISPO が制限するのは更新中の方策と古い方策の比 $r_{i,t}$ で、TIS が制限するのは同じ重みでの訓練と推論の比 $\rho_{i,t}$ である。[Olmo 3](https://arxiv.org/abs/2512.13961) はこの TIS を採用し、推論エンジンと訓練エンジンの log 確率の差を補正するために損失に掛けると書いている。

その後、token ごとの補正では足りないという指摘が続いた。FP16 の論文は先行研究をまとめ、token ごとの TIS は学習を長持ちさせるが、勾配に偏りが残るので学習を安定させきれないと書いている。[Trust Region Masking の論文](https://arxiv.org/abs/2512.23075) は、方策の更新で目的関数を近似するときの誤差を調べた。従来の誤差の上界は回答の長さの 2 乗で大きくなる。論文はより狭い上界を導いたが、どの上界も回答の中で最もずれの大きい token で決まるので、token ごとの PPO のクリップでは抑えられないと示し、信頼域を外れた回答全体を学習から外す masking を提案した。

学習から外す方向の代表が [IcePop](https://arxiv.org/abs/2510.18855) である。Ring-1T の技術報告は、訓練エンジンと推論エンジンの確率の比が範囲 $[c_{\text{low}}, c_{\text{high}}]$ の外にある token の勾配を 0 にする関数を GRPO の損失に掛けた。

$$
\mathcal{M}(\rho_{i,t}) = \begin{cases} \rho_{i,t}, & c_{\text{low}} \le \rho_{i,t} \le c_{\text{high}} \\ 0, & \text{otherwise} \end{cases}
$$

この式は、ずれが小さい token は比で重みを付け、ずれが大きい token はその更新から外すことを言っている。Ring-1T の reasoning の RL では $c_{\text{low}} = 0.5$、$c_{\text{high}} = 5$ とし、KL の係数を 0 にした。論文は、TIS がずれの大きい token にも係数を掛けて更新を続けるので、小さな乱れが積み重なって性能が頭を打つと批判し、小規模な MoE の Ring-mini-2.0 で、AIME25 の正答率が学習を通して TIS を上回ったと報告している。[GLM-5](https://arxiv.org/abs/2602.15763) は GRPO に IcePop を入れ、KL の項を外して使っている。[INTELLECT-3](https://arxiv.org/abs/2512.16144) も、更新中の方策の訓練側の確率と推論側の確率の比で、同じ形の masking を使っている。[Kimi K2.5](https://arxiv.org/abs/2602.02276) は、訓練と推論の違いで大きくなる off-policy のずれを抑えるために、log の比が一定の範囲の外にある token の勾配を、advantage の符号によらず 0 にしている。[Nemotron 3](https://arxiv.org/abs/2512.20856) も、GRPO に masked importance sampling（比が範囲を外れた token を外す重要度の重み付け）を組み合わせている。

DeepSeek-V3.2 は回答単位で外す。論文が $\pi_{\text{old}}$ と書く確率は推論フレームワークが返したサンプリングの確率で、この章の $\mu_{\theta_{\text{old}}}$ にあたる。この記号で書くと、advantage が負で、生成された回答の token について平均した $\log \frac{\mu_{\theta_{\text{old}}}}{\pi_\theta}$ が閾値 $\delta$ を超える回答の、方策の項を損失から外す。KL の項は外さない。そのため、この量は、1 回の生成を複数の minibatch の更新に使うことによる古さと、推論と訓練のフレームワークの違いの両方を含む。前の章の GSPO の比と比べると、分子と分母が逆で、基準の方策も推論側の確率である。論文は、モデルは自分の誤りから最もよく学ぶが、方策から大きく外れた負の回答は最適化を乱しうると理由を述べ、不安定になる設定でこの masking が安定性を上げたと書いている。本文には、閾値 $\delta$ の値も、効果を比べた表も見当たらない。

## ずれそのものを減らす

重みで扱うのではなく、2 つのエンジンの計算を一致させる方向もある。Thinking Machines のブログは、推論の非決定性の主な原因が、batch の大きさによって計算の順序が変わるカーネルにあると示し、RMSNorm、行列積、attention を batch の大きさに依存しない形で実装した。その実装では、先の 1000 回の生成がすべて同じになった。Qwen3-8B で 1000 本の短い回答を生成する時間は、通常の vLLM の 26 秒に対して 42 秒だった。同じブログの RL の実験では、重要度の重みで補正しないと報酬が崩れた。補正すると、推論と訓練の KL は 0.001 付近にとどまった。2 つのエンジンをビット単位で一致させると、KL は 0 のままで、補正なしで学習できた。[vLLM と TorchTitan のチーム](https://vllm.ai/blog/2025-11-10-bitwise-consistent-train-inference) は、この方式を訓練の側まで通し、Qwen3 1.7B の RL を、ビット単位で一致させない場合の 2.4 倍の時間で動かした。

[DeepSeek-V4](https://arxiv.org/abs/2606.19348) は、事前学習、事後学習、推論のすべてでビット単位の一致を目標に、batch の大きさに依存しない決定的なカーネルを用意したと書いている。attention では、1 つの回答を 1 つの SM（GPU の演算ユニット）で計算するカーネルと、SM の数で割り切れずに最後に残る仕事だけを複数の SM で同じ足し算の順序で計算するカーネルを組み合わせた。行列積では cuBLAS を DeepGEMM に置き換え、内側の次元を分けて並列に足す split-k を、ほとんどの場面で使わない。

精度の側では、FP16 の論文が、検証した dense と MoE のモデルで、学習と推論の両方を BF16 から FP16 にするだけで、ずれが約 24 分の 1 になったと報告した。FP16 の仮数部は 10 bit で、BF16 の 7 bit より細かい。代わりに表せる値の範囲が狭いので、論文は loss scaling（損失を拡大して小さい勾配の消失を防ぐ方法）と合わせて使っている。この論文は、FP16 では TIS や MIS（回答単位の masked importance sampling）のようなアルゴリズムの違いによる性能の差がほとんど見分けられなくなったとも書いている。

expert の選択を揃える方法は、扱うずれの範囲で 2 つに分かれる。GSPO の論文の Routing Replay は、古い方策で使った expert を記録し、更新中の方策の比の計算で再現する。R3 の論文はこれを Recompute Routing Replay と呼び、この方法はモデルの更新による expert の変化は扱うが、訓練と推論のフレームワークの違いによる変化は扱わないと指摘した。R3（Rollout Routing Replay）は、推論エンジンが生成のときに選んだ expert を記録し、訓練エンジンでその選択を再現する。gate の重みは訓練エンジンの logits から計算する。R3 を入れると、Qwen3-30B-A3B の訓練と推論の KL の推定値は約 $1.5 \times 10^{-3}$ から $7.5 \times 10^{-4}$ に下がり、dense の Qwen3-8B の $6.4 \times 10^{-4}$ に近づいた。[MiMo-V2-Flash](https://arxiv.org/abs/2601.02780) は R3 を採用している。DeepSeek-V3.2 も Keep Routing として、推論で使った expert の経路を訓練で強制し、DeepSeek-V3-0324 から使っていると書いている。

サンプリングの打ち切りによるずれには、DeepSeek-V3.2 の Keep Sampling Mask がある。推論で top-p や top-k によって外した token を訓練の方策でも外し、2 つの方策が同じ token の集合を共有するようにする。

## 非同期の RL と古いデータ

長い回答の生成では、最も長い 1 本を待つ間、他の GPU が止まる。非同期の RL は生成と学習を分けて並行に回すが、学習に使う回答の一部は古い方策で作られる。[AReaL](https://arxiv.org/abs/2505.24298) は、許す古さの上限 $\eta$ を決め、回答を作った方策 $\pi_{\text{behav}}$ ではなく、直近の方策 $\pi_{\text{prox}}$ を基準にクリップする decoupled PPO の目的関数を使った。ここでは前の章の記号に合わせ、$\pi_{\text{behav}}$ から生成された回答 $o_i$ の token $o_{i,t}$ で書く。推論側の確率を使う場合、$\pi_{\text{behav}}$ はこの章の $\mu$ にあたる。

$$
\mathcal{J}(\theta) = \mathbb{E}\left[ \sum_t \frac{\pi_{\text{prox}}(o_{i,t} \mid q, o_{i,<t})}{\pi_{\text{behav}}(o_{i,t} \mid q, o_{i,<t})} \min\big( u_{i,t}(\theta) \hat{A}_{i,t},\ \operatorname{clip}(u_{i,t}(\theta), 1-\varepsilon, 1+\varepsilon) \hat{A}_{i,t} \big) \right], \quad u_{i,t}(\theta) = \frac{\pi_\theta(o_{i,t} \mid q, o_{i,<t})}{\pi_{\text{prox}}(o_{i,t} \mid q, o_{i,<t})}
$$

この式は、古い方策との違いを重要度の重みで扱い、更新の幅は新しい方策のまわりで抑えることを言っている。AReaL の 1.5B のモデルの数学の実験では、この目的関数を使わないと古さの上限 4 で AIME24 が 23.3 に落ち、使うと 42.2 を保った。同期の学習の 42.0 とほぼ同じである。この目的関数を使っても、上限 16 では 38.7、上限なしでは 36.9 に下がった。学習の全体の時間は、論文の評価の設定で、同じ GPU の数の同期の系に比べて最大で 2.77 倍速くなった。[PipelineRL](https://arxiv.org/abs/2509.19128) は、生成を止めずに更新した重みを推論サーバーに配る in-flight の重みの更新を使った。128 枚の GPU で、安定して動いた最大の 32 回の更新を 1 回の生成に使う従来の RL と比べ、同じ報酬に約 2 倍速く達したと報告した。

[M2PO](https://arxiv.org/abs/2510.01161) は、論文が重要度の重みの 2 次の moment と呼ぶ、比の log の 2 乗の平均を閾値以下に保つように、外れの大きい token だけを捨てる。少なくとも 256 回前のモデルの更新で作ったデータだけを使う実験でも、on-policy の学習に匹敵する性能を保ったと報告した。

DeepSeek-V4 は、生成の途中で中断（preempt）された回答の扱いに注意を促している。中断された回答を最初から生成し直すと、短い回答ほど中断を逃れやすいので、モデルが短い回答に偏る。DeepSeek-V4 は生成済みの token を記録しておき、そこから再開する。

## On-Policy Distillation: 生徒の回答に教師の確率をかける

蒸留にも、データを誰が生成するかの区別がある。教師が生成した回答で生徒を SFT するのは off-policy の蒸留で、DeepSeek-R1 の出力で Qwen や Llama の小さいモデルを学習した DeepSeek-R1-Distill がその例である。off-policy の蒸留は生徒の回答を生成する費用がかからないが、生徒は教師が書いた prefix の上で学ぶので、生徒が実際に生成する prefix とはずれうる。[GKD](https://arxiv.org/abs/2306.13649) は、生徒が自分で生成した回答の上で、教師と生徒の token の分布を近づける On-Policy Distillation を提案した。GKD は、学習に使う回答のうち生徒が生成した割合（student data fraction）と、使うダイバージェンスを選べる形にしている。[MiniLLM](https://arxiv.org/abs/2306.08543) は、生徒の分布で測った教師との KL、つまり reverse KL を、方策勾配で最小化した。reverse KL は、教師が確率を置かない token に生徒が確率を置くことを強く罰するので、教師の主な出力に生徒を寄せる性質を持つ。

![off-policy の蒸留は教師が書いた回答で生徒を学習し、回答に 1 つの報酬を与える RL は、その報酬から作る advantage を全 token に配り、生成した token を使う OPD は、生徒の回答の token ごとに教師の確率との差を信号にする](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mismatch-and-on-policy-distillation/sft-rl-opd.png)

いまの OPD の報告の多くは、RL の枠組みをそのまま使う。ここでは前の章の記号に合わせ、問題を $q$、生徒の回答の $t$ 番目の token を $o_t$ と書く。生徒 $\pi_\theta$ が回答を生成し、教師 $\pi_T$ がその回答の各 token の確率を 1 回の forward で計算し、token ごとの advantage を次の値にする。前の章の GRPO の $\hat{A}_{i,t}$ と区別して $\hat{A}^{\text{OPD}}_t$ と書く。

$$
\hat{A}^{\text{OPD}}_t = \operatorname{sg}\left[ \log \pi_T(o_t \mid q, o_{<t}) - \log \pi_\theta(o_t \mid q, o_{<t}) \right]
$$

この式は、教師が生徒より出しやすいと見る token の確率を上げ、生徒だけが出しやすい token の確率を下げることを言っている。prefix を固定し、token を生徒からサンプルすると、この値の期待値は、その位置での生徒と教師の reverse KL の符号を変えたものになる。推論エンジンで生成する実装では、前の節までのずれも別に扱う必要がある。[Thinking Machines の OPD のブログ](https://thinkingmachines.ai/blog/on-policy-distillation/) は、回答に 1 つの報酬を与える RL が 1 回の生成で得る情報は報酬の 1 つの値なのに対し、蒸留は token ごとに信号を得ると対比した。

このブログの例では、Qwen3-8B-Base を 40 万件の prompt で SFT すると AIME'24 が 60% になり、そこから OPD を約 150 step 回すと 70% に達した。同じ 70% に SFT だけで届くには約 200 万件の prompt が要ると外挿し、その SFT と FLOPs で比べると、OPD の追加の費用は 9 分の 1 から 30 分の 1 になると見積もった。9 分の 1 は SFT のデータがすでにある場合、30 分の 1 は教師に回答を生成させる費用も含めた場合である。実際の教師には Qwen3-8B を使い、費用の比較には 32B のモデルの FLOPs を使っている。

[Qwen3 の技術報告](https://arxiv.org/abs/2505.09388) は、大きいモデルには SFT と RL を含む 4 段の学習を行い、小さいモデルは off-policy の蒸留のあと on-policy の蒸留で作った。数学とコードの問題に絞った比較では、off-policy の蒸留を終えた同じ Qwen3-8B から出発し、AIME'24 は RL で 55.0 から 67.6 に、OPD で 74.4 に上がった。GPU 時間は RL の 17,920 時間に対して OPD は 1,800 時間だった。64 回のうち 1 回でも解ける割合の pass@64 は、RL では 90.0 のまま変わらず、OPD では 93.3 に上がった。[RLVR が base model の能力を超えるかを調べた論文](https://arxiv.org/abs/2504.13837) も、調べた手法とモデルでは、RLVR（正誤を判定できる報酬での RL）で学習したモデルは試行の回数が多いと base model の pass@k を下回ることがある一方、蒸留は教師から新しい推論のパターンを持ち込めると報告している。

## 複数の専門家を 1 つにまとめる

2026 年の公開モデルの報告では、OPD は小さいモデルを作るためだけでなく、分野ごとに RL で鍛えた専門家を 1 つのモデルにまとめるためにも使われている。以下では、報告ごとの呼び名によらず、分野ごとの複数の教師から生徒の回答の上で蒸留する形を、多教師の OPD と呼ぶ。

![SFT のあと、分野ごとに RL で専門家を作り、生徒が自分の回答を生成して、prompt の分野に対応する専門家の token ごとの確率から学ぶ模式図。MiMo-V2-Flash と Kimi K3 は生成した token の log の比を、DeepSeek-V4 は語彙全体の分布を使い、MiMo-V2-Flash は回答の正誤から作る advantage も足す。GLM-5 と Nemotron-Cascade 2 は前の段のチェックポイントを教師にする](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-mismatch-and-on-policy-distillation/multi-teacher-opd.png)

MiMo-V2-Flash は、この形を Multi-Teacher On-Policy Distillation（MOPD）と呼んだ。prompt の分野に対応する教師の log の比を token ごとの advantage にし、訓練と推論の比が範囲を外れた token を捨て、既定では回答の正誤から作る GRPO の advantage と組み合わせる。MiMo-V2-Flash の事後学習に MOPD を使ったと書く [MOPD の論文](https://arxiv.org/abs/2606.30406) は、Qwen3-30B-A3B で数学、指示追従、ソフトウェア工学の 3 分野をまとめた。生徒の SFT の時点を 0、分野ごとの教師を 1 とする正規化したスコアで 0.937 を得て、次に良い方法の 0.882 を上回った。ただし AIME25 では RL で作った教師を 3.33 下回るなど、分野ごとの値ではすべての項目で最良ではない。論文は、同じ元のモデルから作った教師を使うことが、蒸留を安定させたとも書いている。

DeepSeek の報告には、off-policy から on-policy への移行が見える。DeepSeek-V3.2 は、専門家が生成したデータで最終のモデルを学習し、残った差をその後の RL で埋めた。DeepSeek-V4 は、10 以上の教師を使う多教師の OPD を、専門家をまとめる主な方法にした。従来の簡略化では、生成した token の log の比を advantage にする。DeepSeek-V4 はこの方法を、計算資源には優しいが勾配の推定の分散が大きく学習が不安定になりやすいと評価し、語彙全体の分布で reverse KL を計算する full-vocabulary の蒸留を選んだ。

[Kimi K3](https://arxiv.org/abs/2607.24653) は、3 つの分野と 3 段階の推論の量（reasoning effort）の組み合わせで 9 個の専門家を作り、生成した token の log の比を $[-R_{\max}, R_{\max}]$ でクリップした値を報酬にした。Kimi K3 は、より細かい top-k の蒸留も試したが、自分の設定では収束の速さでも最終の性能でもはっきりした差は無かったと書いている。top-k と語彙全体は同じ方法ではなく、2 つの報告はモデルも条件も違うので、直接の比較ではない。

GLM-5 は、reasoning の RL、agent の RL、一般の RL を順に行い、最後の段で on-policy cross-stage distillation を行う。前の段の最終のチェックポイントを教師にし、順に学習する間に失われた能力を取り戻す。[Nemotron-Cascade 2](https://arxiv.org/abs/2603.19220) は、Cascade RL の各段で最も強い中間のモデルを分野ごとの教師にした。数学だけの学習では、GRPO が 25 step で AIME25 を 89.9 から 91.0 に上げ、MOPD は 30 step で 92.0 に達した。ArenaHard v2 の Hard Prompt では、MOPD が 52 step で 71.5 から 85.5 に上げ、RLHF は 160 step で 80.7 だった。Creative Writing では MOPD の 71.0 と RLHF の 71.2 がほぼ同じである。どれも step の数での比較で、教師の forward を含む GPU 時間の比較ではない。[Nemotron 3 Ultra](https://arxiv.org/abs/2606.15007) も、SFT と RL のあとに MOPD を使っている。

## OPD の限界

生成した token の確率をそのまま教師と生徒で比べる標準的な OPD は、教師と生徒が同じ tokenizer を持つことを前提にする。[Breaking the Tokenizer Barrier](https://arxiv.org/abs/2606.09456) は、tokenizer の違うモデルの間では教師の回答で SFT するのがふつうだと述べ、token の対応付けで OPD を別の系列のモデルの間に広げた。

[Revisiting On-Policy Distillation](https://arxiv.org/abs/2603.25562) は、生成した token だけを使う OPD の失敗を 3 つ挙げた。token ごとの信号の大きさの偏り、生徒が作った prefix の上では教師の確率が当てにならないこと、tokenizer や特殊 token の不一致である。著者たちは、教師の上位 $K$ 個の token の集合の上で分布を比べる方法を提案した。数学と agent の課題を交互に学習する設定で、数学の平均を標準の OPD の 34.8 から 41.7 に上げたと報告している。

[A Distribution-Matching Perspective](https://arxiv.org/abs/2610.33740) は、論文が定める枠組みのもとで、OPD が従来の蒸留より有利なのは生徒の分布が目標の分布に近いときで、遠いときは不利になると論じた。Qwen3 が off-policy の蒸留で生徒を教師に近づけてから on-policy の蒸留に移る順序を考える手がかりになるが、その順序の効果を直接確かめた結果ではない。外部の教師を持たず、正解の解答などの追加の情報を与えた自分自身の出力から学ぶ On-Policy Self-Distillation では、[Thinking Collapse の論文](https://arxiv.org/abs/2607.10805) が、数学の reasoning で途中の振り返りの token が抑えられて性能が下がる現象を報告している。

## 公開モデルでの組み合わせ

公開モデルの技術報告に書かれた、ずれの扱いと蒸留の方法を並べる。どれも報告に書かれた段階と分野での設定である。「記載なし」は、その報告に書かれていないことを指し、使っていないことを意味しない。

| モデル | 報告の日付 | ずれの扱い | 蒸留 |
|---|---|---|---|
| [Qwen3](https://arxiv.org/abs/2505.09388) | 2025 年 5 月 | 記載なし | 小さいモデルを off-policy から on-policy の蒸留で作る |
| [MiniMax-M1](https://arxiv.org/abs/2506.13585) | 2025 年 6 月 | 出力層を FP32 にする | 記載なし |
| [Ring-1T](https://arxiv.org/abs/2510.18855) | 2025 年 10 月 | reasoning の RL で IcePop | 記載なし |
| [DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) | 2025 年 12 月 | 回答単位の masking、Keep Routing、Keep Sampling Mask | 専門家が生成したデータで学習する off-policy |
| [Olmo 3](https://arxiv.org/abs/2512.13961) | 2025 年 12 月 | TIS | 記載なし |
| [INTELLECT-3](https://arxiv.org/abs/2512.16144) | 2025 年 12 月 | IcePop の形の masking | 記載なし |
| [Nemotron 3](https://arxiv.org/abs/2512.20856) | 2025 年 12 月 | masked importance sampling | 記載なし |
| [MiMo-V2-Flash](https://arxiv.org/abs/2601.02780) | 2026 年 1 月 | R3、範囲外の token を捨てる | 多教師の OPD（MOPD） |
| [Kimi K2.5](https://arxiv.org/abs/2602.02276) | 2026 年 2 月 | log の比による token の masking | 記載なし |
| [GLM-5](https://arxiv.org/abs/2602.15763) | 2026 年 2 月 | IcePop | 前の段を教師にする on-policy の蒸留 |
| [Nemotron-Cascade 2](https://arxiv.org/abs/2603.19220) | 2026 年 3 月 | 記載なし | 中間のモデルを教師にする多教師の OPD |
| [DeepSeek-V4](https://arxiv.org/abs/2606.19348) | 2026 年 4 月 | batch の大きさに依存しない決定的なカーネル | 語彙全体の多教師の OPD |
| [Kimi K3](https://arxiv.org/abs/2607.24653) | 2026 年 7 月 | 記載なし | 多教師の OPD |

表と参考の日付は arXiv の v1 の日付である。DeepSeek-V4 は、arXiv の記録で v1 の日付が 2026 年 4 月 26 日と表示され、識別子の年月 2606 と一致しない。

## まとめ

訓練と推論のずれの扱いと OPD は、どちらも学習に使う回答をどの方策がどの確率で生成したかを扱う仕組みとして読める。この章で扱った 2025 年の後半から 2026 年の技術報告では、ずれを比で重みを付けるか学習から外す仕組みと、分野ごとの教師から生徒自身の回答の上で学ぶ OPD が、GRPO の目的関数の選択と並んで書かれている。

## 参考: この章で扱った研究

| 資料 | 公開 | この章での役割 |
|---|---|---|
| [GKD](https://arxiv.org/abs/2306.13649) | 2023 年 6 月 | On-Policy Distillation の提案 |
| [MiniLLM](https://arxiv.org/abs/2306.08543) | 2023 年 6 月 | reverse KL を方策勾配で最小化する蒸留 |
| [RLVR は base model を超えるか](https://arxiv.org/abs/2504.13837) | 2025 年 4 月 | RLVR と蒸留の能力の範囲の違い |
| [Qwen3](https://arxiv.org/abs/2505.09388) | 2025 年 5 月 | OPD と RL の比較 |
| [AReaL](https://arxiv.org/abs/2505.24298) | 2025 年 5 月 | 非同期の RL と decoupled PPO |
| [MiniMax-M1](https://arxiv.org/abs/2506.13585) | 2025 年 6 月 | 出力層の精度によるずれ |
| [Yao らのブログ](https://fengyao.notion.site/off-policy-rl) | 2025 年 8 月 | 訓練と推論のずれの指摘と TIS |
| [Defeating Nondeterminism in LLM Inference](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/) | 2025 年 9 月 | batch の大きさに依存しないカーネル |
| [PipelineRL](https://arxiv.org/abs/2509.19128) | 2025 年 9 月 | in-flight の重みの更新 |
| [M2PO](https://arxiv.org/abs/2510.01161) | 2025 年 10 月 | 古いデータでの off-policy の学習 |
| [R3](https://arxiv.org/abs/2510.11370) | 2025 年 10 月 | 推論のときの expert の記録と再現 |
| [Ring-1T（IcePop）](https://arxiv.org/abs/2510.18855) | 2025 年 10 月 | 比が範囲を外れた token の masking |
| [On-Policy Distillation（Thinking Machines）](https://thinkingmachines.ai/blog/on-policy-distillation/) | 2025 年 10 月 | OPD の費用と効果 |
| [FP16 の論文](https://arxiv.org/abs/2510.26788) | 2025 年 10 月 | 精度によるずれ |
| [Bitwise-consistent train and inference](https://vllm.ai/blog/2025-11-10-bitwise-consistent-train-inference) | 2025 年 11 月 | 訓練まで通したビット単位の一致 |
| [DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) | 2025 年 12 月 | 回答単位の masking、Keep Routing、Keep Sampling Mask |
| [Olmo 3](https://arxiv.org/abs/2512.13961) | 2025 年 12 月 | TIS の採用 |
| [INTELLECT-3](https://arxiv.org/abs/2512.16144) | 2025 年 12 月 | masking の採用 |
| [Nemotron 3](https://arxiv.org/abs/2512.20856) | 2025 年 12 月 | masked importance sampling の採用 |
| [Trust Region Masking](https://arxiv.org/abs/2512.23075) | 2025 年 12 月 | 長い回答での誤差と回答単位の masking |
| [MiMo-V2-Flash](https://arxiv.org/abs/2601.02780) | 2026 年 1 月 | MOPD と R3 |
| [Kimi K2.5](https://arxiv.org/abs/2602.02276) | 2026 年 2 月 | log の比による masking |
| [GLM-5](https://arxiv.org/abs/2602.15763) | 2026 年 2 月 | IcePop と cross-stage の蒸留 |
| [Nemotron-Cascade 2](https://arxiv.org/abs/2603.19220) | 2026 年 3 月 | MOPD と GRPO、RLHF の比較 |
| [Revisiting On-Policy Distillation](https://arxiv.org/abs/2603.25562) | 2026 年 3 月 | OPD の失敗の型 |
| [DeepSeek-V4](https://arxiv.org/abs/2606.19348) | 2026 年 4 月 | 決定的なカーネルと語彙全体の OPD |
| [Breaking the Tokenizer Barrier](https://arxiv.org/abs/2606.09456) | 2026 年 6 月 | tokenizer の違うモデルの間の OPD |
| [Nemotron 3 Ultra](https://arxiv.org/abs/2606.15007) | 2026 年 6 月 | MOPD の採用 |
| [MOPD](https://arxiv.org/abs/2606.30406) | 2026 年 6 月 | 多教師の OPD の比較 |
| [Thinking Collapse](https://arxiv.org/abs/2607.10805) | 2026 年 7 月 | 自己蒸留での reasoning の崩れ |
| [Kimi K3](https://arxiv.org/abs/2607.24653) | 2026 年 7 月 | log の比のクリップと top-k の蒸留の比較 |
| [A Distribution-Matching Perspective](https://arxiv.org/abs/2610.33740) | 2026 年 10 月 | OPD が有利になる条件 |
