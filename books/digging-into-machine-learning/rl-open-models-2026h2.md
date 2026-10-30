---
title: "2026 年後半の公開モデルは RL の事後学習で何をしているか: 技術報告から読む"
free: false
---

## はじめに

この本の 2 つ前の章「GRPO のあとで RL の目的関数は何が変わったか」（以下、1 章）では GRPO の式の部品を、前の章「訓練と推論のずれと On-Policy Distillation」（以下、2 章）では訓練と推論の確率のずれと On-Policy Distillation（OPD）を、2025 年から 2026 年前半の論文を中心に整理した。この章では、2026 年 7 月から 10 月に出た公開モデルの技術報告とその周辺の資料に絞り、いまの事後学習が実際にどう組まれているかを読む。この章で読んだ報告では、目的関数の土台に GRPO を使うものがいちばん多く、**違いの多くは、学習に使う回答をどの確率で扱うか、どの教師から学ぶか、どの環境で回答を作るか、という周りの部分に出ている**。

合わせて、この章では問いを次の 7 つに分ける。表の右の列は、章の結論を先に並べたものである。

| 問い | 技術報告から読める答え |
|---|---|
| 事後学習はどんな段で組まれているか | [DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/DeepSeek_V41_Tech_Report.pdf)、[Kimi K3](https://arxiv.org/abs/2607.24653)、[Nemotron 3.5 Super](https://arxiv.org/abs/2610.38095)、[KAT-Coder-V2.5](https://arxiv.org/abs/2607.05471) は、SFT のあと分野ごとの専門家を RL で作り、OPD で 1 つのモデルにまとめる。専門家の作り方と OPD のあとの段は報告ごとに違う |
| 目的関数は何が変わったか | 土台は GRPO が多い。変わったのは比の扱いで、推論側の確率で直接割る、4 つの境界で外す、2 値で外す、といった違いがある。長い agent の課題では、critic を使う PPO の系統を選んだ報告もある |
| 訓練と推論のずれの扱いは何が変わったか | 比の帯で外す IcePop に対し、[Ling と Ring 2.6](https://arxiv.org/abs/2606.15079) は 2 択の KL で、[prime-rl](https://github.com/PrimeIntellect-ai/prime-rl/releases/tag/v0.10.0) は確率の差で外す方法に替えた。ビット単位の一致や expert の経路の再生で、ずれそのものを小さくする報告もある |
| OPD はどう使われているか | DeepSeek-V4.1-Flash は 40 を超える教師から語彙全体の分布で学ぶ。Nemotron 3.5 Super は 2 段に分け、2 段目で教師を教師と固定した生徒の混合に替えて、負の信号に下限を付ける |
| agent の RL はどれくらいの規模か | Kimi K3 は学習と評価の間に累計 5,000 万を超える sandbox を作り、DeepSeek-V4.1-Flash は同時に数百万の sandbox を動かす必要があったと書いている |
| RL の費用はどれくらいか | この章で読んだ資料で金額を書いたのは [MiMo-V2.6](https://arxiv.org/abs/2610.11959) だけで、RL に Pro で 260 万ドル、Flash で 90 万ドルを使い、Pro の費用の 43.8% が回答の生成だった |
| 問題ごとに回答の数を変える研究は使われているか | 報酬のばらつきに合わせて問題ごとの回答の数を配る研究は 2025 年から複数ある。この章で 1 問あたりの回答の数を確かめた報告は、どれも問題によらず同じ数を使っていて、こうした配分を使ったという記述は見つからなかった |

読者には、1 章と 2 章の内容を想定する。記号は 2 章に合わせ、推論エンジンが回答を生成したときの確率を $\mu_{\theta_{\text{old}}}$、同じ重みで訓練エンジンが計算し直した確率を $\pi_{\theta_{\text{old}}}$、更新中の方策の確率を $\pi_\theta$ と書く。扱う資料は arXiv の論文、公式のブログ、Hugging Face と GitHub に置かれた技術報告とリリースノートで、2026 年 10 月 30 日に確かめた。ブログとリリースノートは査読を経ていない資料で、報告のいくつかは 2026 年 1 月から 6 月のものだが、そのモデルの系列で目的関数や事後学習の方法を書いた最新の開示であるため含めた。本文と表の数値は各資料の報告値で、筆者はどれも再現していない。図 2 と図 3 の値と、Nemotron 3.5 Super の advantage の下限の −0.22 は、筆者が式から計算した説明用の値である。

## 全体像: 専門家を作ってからまとめる

この章で読んだ報告のいくつかは、事後学習を 3 つの段で組んでいる。最初に SFT で土台を作り、次に数学、コード、agent のような分野ごとに RL で専門家を育て、最後に OPD で専門家の能力を 1 つの生徒のモデルにまとめる。

![SFT の土台から分野ごとの専門家を RL で作り、OPD で 1 つのモデルにまとめる模式図。RL の段では非同期の生成、訓練と推論のずれの扱い、sandbox の環境が、OPD の段では教師の数と信号の粒度が報告ごとに違う](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-open-models-2026h2/pipeline-2026h2.png)

DeepSeek-V4.1-Flash の技術報告は、事後学習の最後の段として、すべての分野のデータで、40 を超える教師から語彙全体の分布で学ぶ OPD を行ったと書いている。Kimi K3 は、3 つの分野と 3 段階の推論の量（reasoning effort）を掛け合わせた 9 個の専門家を RL で作り、OPD でまとめた。Nemotron 3.5 Super は、RL の 120 step のチェックポイントから agent、reasoning、動画と画像、alignment などの教師を分岐させ、同じチェックポイントを生徒にして OPD を行う。OPD のあとには、投機的デコードの draft を強める段がある。KAT-Coder-V2.5 も、ソフトウェア工学、ツール利用、ターミナル、Web のコードなどの専門家を、生徒自身の回答の上での多教師の OPD でまとめたと書いている。

違う組み方もある。MiMo-V2.6 は、複数の課題を 1 回の RL で混ぜて学習し、そのあとに MOPD2 と呼ぶ OPD で、混ぜた RL で作った教師や、検証しにくい課題向けに SFT で作った教師の能力を足している。同じ Nemotron 3.5 の小さい方の Lightning には OPD の段が無く、RL のあとに draft を強める段だけがある。

## 非同期の生成と古いデータ

ここから扱うずれには 2 種類ある。1 つは同じ重みでも訓練と推論の実装が違うために生じる差で、もう 1 つは非同期の生成で古い重みの回答を使うために生じる差である。この節で生成と更新の流れを先に見て、次の節から比の扱い、学習から外す判定、計算と経路の一致を順に見る。

長い agent の回答は生成に時間がかかるので、この章で読んだ報告の多くは生成と学習を非同期に回している。DeepSeek-V4.1-Flash の報告は、非同期の生成が 2 つの副作用を生むと書いた。1 つ目は回答の長さの分布の偏りで、学習が始まるまでに終わった回答には短いものが多くなる。2 つ目は古い方策で作ったデータで、DeepSeek-V4.1-Flash は古さが大きすぎる token の寄与を損失から外す masking でこれを抑えている。

MiMo-V2.6 は古さの上限を 4 にして部分的な生成を使い、Nemotron 3.5 Super の OPD は 1 step 古い生徒の回答に重みを付け、重みが $[0.2, 5]$ の外にある token を外している。DeepSeek-V4.1-Flash は、group の回答がそろうのを待つと長い回答に引きずられて生成の並列度が保てないので、完了した回答がどの group のものかを問わず、新しく完了した回答の数が次の問題の group の大きさに達した時点で、その問題を送る方式を選んだと書いている。

## 目的関数: GRPO は残り、比の扱いが変わった

この章で読んだ報告のうち目的関数を書いたものの多くは、土台に GRPO を使っている。DeepSeek-V4.1-Flash の報告は、分野の専門家の RL を GRPO の group の単位で回すと書き、Nemotron 3.5 Super は agent と reasoning の教師を非同期の GRPO で学習した。[IBM Granite 4.2 のブログ](https://huggingface.co/blog/ibm-granite/granite-4-2) は、すべての段を非同期の GRPO で学習し、共通の設定としてクリップの幅を下側 0.2、上側 0.28 とし、30B のモデルの段ごとの設定の表で KL の係数を 0 から 0.05 の間で変えている。[Hunyuan-A13B](https://arxiv.org/abs/2609.27284) も reasoning の RL に GRPO を使った。[LongCat-Flash-Thinking-2601](https://arxiv.org/abs/2601.16725) は、MoE のモデルでの経験的な有効性と、長い agent の軌跡で回答単位の最適化がより安定することを理由に、GSPO を目的関数に選んだ。

変わったのは比の扱いである。2 章で見たように、GRPO の比 $r_{i,t}$ は更新中の方策 $\pi_\theta$ を訓練側の古い方策 $\pi_{\theta_{\text{old}}}$ で割った比で、訓練と推論のずれは別の比 $\rho_{i,t} = \pi_{\theta_{\text{old}}} / \mu_{\theta_{\text{old}}}$ で扱った。2026 年後半の報告には、$\pi_{\theta_{\text{old}}}$ を計算せずに、$\pi_\theta$ を推論側の確率 $\mu_{\theta_{\text{old}}}$ で直接割る例が出ている。この比は $r_{i,t}$ と $\rho_{i,t}$ の積にあたる。MiMo-V2.6 は GRPO を使い、この比を token ごとの重要度の重み $w_{i,t} = \operatorname{sg}[\pi_\theta(o_{i,t}) / \mu_{\theta_{\text{old}}}(o_{i,t})]$ とし、部分的に生成した回答でも推論側の確率は計算し直さないと書いている。$o_{i,t}$ は $i$ 番目の回答の $t$ 番目の token、$\hat{A}_{i,t}$ はその token の advantage、$\operatorname{sg}$ は勾配を流さない操作で、どれも 1 章と同じ記号である。MiMo-V2.6 は、advantage が正の token 用と負の token 用で別々の下限と上限、合わせて 4 つの境界を置き、範囲の外の token を外す。

$$
M_{i,t} = \mathbb{1}\left[ (\hat{A}_{i,t} \ge 0 \land c_{+}^{\text{low}} \le w_{i,t} \le c_{+}^{\text{high}}) \lor (\hat{A}_{i,t} < 0 \land c_{-}^{\text{low}} \le w_{i,t} \le c_{-}^{\text{high}}) \right]
$$

この式は、advantage の符号ごとに別の範囲で比を判定し、範囲に入る token だけを学習に使うことを言っている。正の側と負の側の範囲はどちらも $[0.2, 5.0]$ から始め、学習中に方策のエントロピーを見て別々に調整する。エントロピーが低すぎるときは正の側の範囲を広げて負の側を狭め、高すぎるときは逆にする。GLM のチームの [SAO の論文](https://arxiv.org/abs/2607.07508) も、非同期の RL で $\pi_{\theta_{\text{old}}}$ を持たずに推論側の確率を直接使い、token ごとに範囲を外れた比を外す形を提案した。論文は、IcePop と似ているが古い方策を取り除いたぶん単純だと書いている。

[Step 3.5 Flash](https://arxiv.org/abs/2602.10604) の MIS-PO は、比で重みを付けること自体をやめた。推論側の方策を提案分布、訓練側の方策を目標の分布と見て、範囲に入るかどうかの 2 値の判定を 2 つの粒度で行う。token の単位では 2 章の比 $\rho_{i,t} = \pi_{\theta_{\text{old}}} / \mu_{\theta_{\text{old}}}$ を $[0.5, 2]$ で判定して token を残し、回答の単位ではその比の幾何平均を $[0.996, 1.001]$ で判定して回答を残す。残ったものは実質的に on-policy として扱う。論文の MoE のモデルの比較では、GSPO が 200 iteration のあたりで伸びが止まり、訓練側と推論側の確率の比で測ったずれが大きくなっていったのに対し、MIS-PO はずれを小さく保ったと報告している。

長い agent の課題では、critic（価値を予測するモデル）を使う PPO の系統を選んだ報告もある。1 章で見たように、GRPO の系統は critic を持たないことで計算を減らした。KAT-Coder-V2.5 は、長い課題向けの非対称な actor と critic の PPO と、後から分かった情報で価値の推定を補う critic を使ったと書いている。Step 3.5 Flash も、文脈の長さで打ち切られた回答を報酬 0 として扱わず、価値の推定で補う方法を組み合わせている。回答が数百ターンに伸びると、1 つの回答に 1 つの報酬では信号が粗いので、critic で途中の状態を評価する設計を選んだと読める。

## 比から確率の差へ: IcePop のあとの判定

2 章の IcePop は、$\rho_{i,t}$ が決まった帯 $[c_{\text{low}}, c_{\text{high}}]$ の外にある token を学習から外した。IcePop を提案した Ant の Ling チームは、Ling と Ring 2.6 の技術報告で、帯は確率の高い token にも低い token にも同じ割合のずれを想定していて、確率の低い token を外しすぎると書いた。そこで KPop は、生成した token とそれ以外の 2 つに語彙を分け、2 択の分布として測った KL で判定する。訓練側の確率を $a = \pi_{\theta_{\text{old}}}(o_t)$、推論側の確率を $b = \mu_{\theta_{\text{old}}}(o_t)$ と書くと、次の形になる。

$$
D^{B}_{\text{KL}}(a \,\|\, b) = a \log \frac{a}{b} + (1-a) \log \frac{1-a}{1-b}, \quad \mathcal{M}_{\text{KPop}}(t) = \mathbb{1}\left[ D^{B}_{\text{KL}}(a \,\|\, b) \le \phi \right] \cdot \mathbb{1}\left[ D^{B}_{\text{KL}}(b \,\|\, a) \le \phi \right]
$$

この式は、2 つの向きの KL がどちらも閾値 $\phi$ 以下の token だけを残すことを言っている。IcePop の帯は下限と上限の 2 つの値を決めるが、KPop の閾値は $\phi$ の 1 つだけである。報告は $\phi$ の値を本文に書いていない。

prime-rl の v0.10.0 は、既定の損失を IPO（INTELLECT Policy Optimization）に替えた。IPO は確率そのものの差 $|a - b|$ が閾値を超えた token を外し、advantage の符号は見ない。リリースノートは、比で判断すると、推論側の確率 0.01 に対して訓練側が 0.06 の珍しい token は 6 倍として外され、推論側の確率 0.90 に対して訓練側が 0.20 の確率の高い token は 0.22 倍として残る場合があると、比が両端で判断を誤る例を挙げている。後者が残るのは帯の下限が 0.22 より小さい場合で、帯を $[0.5, 5]$ にすれば外れる。GLM-4.5-Air のソフトウェア工学の agent の RL（router replay なし、最大 64 step 遅れたデータ）では、IcePop は 1,200 step のあたりで訓練と推論の KL が 20 倍を超えて膨らんで崩れ、IPO は閾値が 0.2 でも 0.3 でもそこを越えて学習が続いたと報告している。閾値 0.3 で外れる token は約 0.25% だった。

![推論側と訓練側の確率を両軸の対数目盛りに取り、比の帯、確率の差、2 択の KL のそれぞれで学習に残る範囲を塗った計算図。閾値は帯が 0.5 から 5、差が 0.3、KL が仮に 0.05。比の帯は対角線に沿った一定の幅で、確率の差は両方が小さい領域をまとめて残し、2 択の KL は確率が小さいほど比の幅を広く許す](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-open-models-2026h2/mask-shapes.png)

IcePop の形を使う報告も続いている。[Tencent Hy3 の RL の手順書](https://huggingface.co/tencent/Hy3/raw/main/rl/README.md) は、Hy3 で DAPO の数学のデータを 128 枚の H20 で学習する例として、IcePop の帯を $[0.5, 4.0]$、クリップの幅を 0.2 と 0.28、KL の係数を 0 にした設定を載せ、推論側と訓練側の確率の差が小さいまま学習が安定したと書いている。これは手順書の実行例で、Hy3 本体の事後学習の設定かどうかは手順書からは読み取れない。[Intern-S1-Pro](https://arxiv.org/abs/2603.25040) は、IcePop に従って、訓練と推論のずれの比と minibatch の古さの比の 2 つを持つ REINFORCE の目的関数を使い、生成のときに選んだ expert を記録して学習で再現する rollout router replay も組み合わせた。

比の帯とは別の方法を取る報告もある。Kimi K3 は、長い軌跡が複数の反復にまたがって生じる古いデータに対し、token ごとの正則化で更新を局所に抑えると書いているが、式は示していない。

## ずれを小さくする: ビット単位の一致と経路の再生

ずれを比で扱う代わりに、2 つのエンジンの計算を一致させる報告もある。[Step-5-Preview のブログ](https://platform.stepfun.ai/blog/inside-step-5-preview) は、データの古さが 0 の場合に訓練と推論の演算をそろえ、log 確率の差をちょうど 0 にしたと書いている。tensor parallel の分け方が訓練で 1、推論で 8 と違ってもビット単位で一致し、追加の時間は 10% 未満だった。古さが 0 より大きい場合は、推論のときの計算の状態を訓練で再生する。router を止めて expert の番号と係数を再生し、top-p で残った候補の集合も再生して、その候補の上だけで log 確率を計算し直す。

[GLM-5.3 のブログ](https://z.ai/blog/glm-5.3) は、訓練と生成の一致を確かめる評価で、log 確率の差の平均が $10^{-7}$ の水準になり、以前の設定より 99.99% 以上小さかったと書いている。Nemotron 3.5 Super は、router replay と出力層の FP32 化の 2 つで、訓練と推論の KL を 1 桁以上小さくした。Snowflake の研究者による [FlashNext-RL](https://arxiv.org/abs/2610.28998) は、Qwen3.8-Flash-Next の長い agent の RL を自分たちの系で回した報告で、BF16 では 2 つの実装が同じ token に別の上位 10 個の expert を選ぶことが多く、本番とは別のオフラインの測定では、この経路の食い違いが訓練と推論のずれの約 96% を説明したと書いている。これは Qwen のチームの開示ではない。

経路の再生にも新しい形が出ている。DeepSeek-V4.1-Flash は非同期の生成で、1 つの回答が複数のチェックポイントにまたがるとき、新しいチェックポイントで経路を計算し直すのではなく、生成の区間ごとの expert の経路をつなげて再生する concatenated routing-replay を使う。これとは別に、MiMo-V2.6 は expert の負荷を安定させるために RL の間 router の重みを固定し、prime-rl も既定で router を固定している。router の固定は経路の再生とは別の操作で、重みの更新で経路が動くことを抑える。

## OPD: 何体の教師から、どの粒度で学ぶか

2 章では、OPD の信号を生成した token の log の比で作るか、語彙全体の分布で作るかが報告ごとに違うことを見た。2026 年後半の報告でも、この違いは続いている。

| 報告 | 教師 | 信号の粒度 | 書かれている工夫 |
|---|---|---|---|
| [DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/DeepSeek_V41_Tech_Report.pdf) | 40 を超える | 語彙全体 | 非同期の生成で OPD を回す |
| [Kimi K3](https://arxiv.org/abs/2607.24653) | 9 個（3 分野と 3 段階の推論の量） | 生成した token の log の比をクリップ | top-k の蒸留も試したが、はっきりした差は無かった |
| [Nemotron 3.5 Super](https://arxiv.org/abs/2610.38095) | agent、reasoning、動画と画像、alignment など | 生成した token の log の比 | 2 段に分け、2 段目で教師を教師と固定した生徒の混合に替える |
| [GLM-5.3](https://z.ai/blog/glm-5.3) | 複数の専門家 | GLM-5.3 で使った粒度は記載なし | 訓練の基盤の slime で top-p、top-k、語彙全体を選べるようにした |
| [MiMo-V2.6](https://arxiv.org/abs/2610.11959) | 混ぜた RL で作った教師と、検証しにくい課題向けに SFT で作った教師 | token ごと | 教師の回答や SFT のデータの途中までを prefix にして、そこから生徒に書かせる |
| [KAT-Coder-V2.5](https://arxiv.org/abs/2607.05471) | ソフトウェア工学、ツール利用など | 記載の範囲では不明 | off-policy の蒸留から始め、長い文脈でずれた回答を途中で切る |

Nemotron 3.5 Super の 2 段目の工夫は、式で見ると分かりやすい。報告の OPD の advantage は、教師の確率 $\pi_T$ と、更新の前に固定した生徒の確率 $\bar{\pi}_S$ を使った $\log \pi_T - \log \bar{\pi}_S$ で、教師がほとんど出さない token では、いくらでも大きな負の値になる。2 段目では教師を $\alpha \pi_T + (1-\alpha) \bar{\pi}_S$（$\alpha = 0.2$）に替えるので、advantage は $\log\big((\alpha \pi_T + (1-\alpha) \bar{\pi}_S) / \bar{\pi}_S\big) \ge \log(1-\alpha) \approx -0.22$ となり、この値より下がらない。報告は、生徒が 1 回目の教師の能力を取り込んだあとの学習がこれで安定したと書いている。1 段目は $\alpha = 1$、つまり教師そのものである。

Meta の [Muse Glimmer 30B](https://arxiv.org/abs/2610.24628) も、生徒が自分で回答を生成し、教師が 1 step ごとに生徒の分布を評価する OPD の段を持つ。報告は、この形が訓練と推論の分布のずれを解消し、生成がずれたときに立て直すことを教えると書いている。ここでの訓練と推論のずれは、2 章の前半の数値の食い違いではなく、2 章の後半で見た、教師が書いた prefix と生徒が実際に書く prefix の違いを指している。

## agent の RL: 環境と報酬の規模

この章で読んだ報告で目立つのは、agent の RL の環境の規模である。Kimi K3 は、学習と評価の間に、1,505,678 個の image から累計 51,219,741 個の sandbox を作ったと書いている。DeepSeek-V4.1-Flash は、V4.1 の学習で、同時に動かす sandbox の需要が数百万に増えたと書いている。KAT-Coder-V2.5 は、ソフトウェア工学の環境を作る成功率を 16.5% から 57.2% に上げ、12 の言語で 100,000 を超える検証できる環境を作った。3 つの数は、累計、同時の需要、環境の種類と、数えているものが違う。

環境が増えると、報酬の作り方と batch の組み方も変わる。[Qwen3.8-Max のブログ](https://qwen.ai/blog?id=qwen3.8) は、実行結果の検査と rubric による判定をまとめた Universal Reward System と、課題、難しさ、作業の場、harness（agent を動かす枠組み）の分布を batch ごとに均衡させる online data balancer を挙げ、後者が batch の間の勾配のばらつきを抑えると書いている。Kimi K3 は、検証できない課題では agent の形の生成的な報酬モデルを使い、そのモデルに、出力を読む、rubric を作る、各候補を採点する、点数を記録する、という手順を必ず踏ませる。

agent の RL には、学習の基盤の細部が効く。KAT-Coder-V2.5 は、約 200 ターンの agent の課題で、推論のサーバーが会話の template を当て直して token に分け直すことで、約 40% の回答に token のずれが生じたと報告した。報告は、template を通さずに推論の生成の口を直接呼ぶ Gateway Server でこれを避けている。[Apertus 1.5](https://arxiv.org/abs/2610.15542) は、外部のツールのサーバーの処理能力が足りなくなったので、ツールを使う問題を本番の RL から外し、公開したモデルのツール利用は SFT だけで学んだと書いている。

## 費用: RL に何が掛かるか

RL の費用を金額で書いた資料は、この章で読んだ中では MiMo-V2.6 だけだった。MiMo-V2.6 は、数千の GPU で 1 回の RL を回し、Pro に 260 万ドル、Flash に 90 万ドルを使った。1 step は 1,568 問に 16 個ずつ、合わせて約 25,000 の回答を生成する。Pro の費用の割合は、回答の生成が 43.8%、学習が 43.5%、採点が 12.7% だった。課題は agent と競技のコードが 68%、一般のツール利用が 12%、デザインが 13%、文脈の追従が 3%、セキュリティが 4% である。

## 回答の数をどう配るか: 研究と実務の隙間

GRPO は、問題ごとに同じ数 $G$ の回答を生成し、問題の中で報酬を比べる。統計の言葉で見ると、問題を層とする層化抽出で、どの層にも同じ数を配る形に似ている。層化抽出で全体の平均を推定するときは、層の大きさと標準偏差の積に比例して標本を配るネイマン配分のほうが分散が小さい。正誤の報酬で問題 $q$ の正解率を $p_q$ とすると報酬の標準偏差は $\sigma_q = \sqrt{p_q(1-p_q)}$ なので、各問題の重みと 1 回の生成の費用が同じという単純化した設定では、ネイマン配分は $G_q \propto \sigma_q$ になる。ただし GRPO が推定したいのは平均の報酬ではなく方策の勾配なので、この類推がそのまま最適な配分を与えるわけではない。

![正解率ごとに 1 問あたりの回答の数を比べた計算図。平均を 16 にそろえて、全問題に同じ数、標準偏差に比例するネイマン配分、正解率の平方根に反比例する配分（Reinforce-Ada の目安、上限と下限と定数 c は省略）を並べた。DAPO の Dynamic Sampling は数を変えず、全員が同じ結果の問題を外すので、残る回答の期待値を示した](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-open-models-2026h2/allocation-rules.png)

問題ごとに回答の数を変える研究は、2025 年から複数出ている。[Knapsack RL](https://arxiv.org/abs/2509.25849) は、各問題を、回答の数という費用と学習の見込みという価値を持つ品物と見て、総数の決まったナップサック問題として配分を解いた。DeepSeek-R1-Distill-Qwen-1.5B では、各問題を 16 回解いた平均の正答率で、6 つのベンチマークの平均を GRPO の 45.9 から 49.7 に上げたと報告している。[Reinforce-Ada](https://arxiv.org/abs/2510.04996) は、正解率の log を最大化する目的から、問題 $q$ について推定した正解率 $\hat{p}_q$ と小さい定数 $c$ を使って回答の数を $N_q \propto 1/\sqrt{\hat{p}_q + c}$ とし、上限と下限の間に収める配分を導いた。正解率が低い難しい問題ほど多く配る形で、正解率 0.5 で最大になるネイマン配分とは、目的も形も違う。[sGPO](https://arxiv.org/abs/2606.08854) は、事前に測った正解率で問題を分け、回答の数を 2、4、8 の段階で配り、Qwen2.5-Math-7B で DAPO と同程度の平均の正答率を得たと報告した。計算量は、sGPO が事前の正解率の測定の 1.6 EFLOPs を含めて 8.9 EFLOPs、DAPO が 22.3 EFLOPs だった。

ネイマン配分の形を方策勾配の問題として導いたのが、2026 年 10 月の [VSR](https://arxiv.org/abs/2610.15888) である。決まった数の回答で方策勾配の推定の分散を最小にする状態の分布を求めると、勾配の大きさが状態によらずそろうという論文の仮定のもとで、$\nu(s) \propto d^{\pi}(s)\, \sigma_{\pi}(s)$ というネイマン配分の形になると示した。この形は、その仮定と論文の推定量のもとでの結果で、どの方策勾配の推定にもそのまま当てはまる配分ではない。ここでは推論側の確率 $\mu$ と区別するために、論文の記号を $\nu$ に替えている。$d^{\pi}(s)$ はその状態に方策が実際に到達する度合い、$\sigma_\pi(s)$ はその状態から先の報酬の標準偏差である。VSR は、この配分を問題全体ではなく、回答の途中の状態（問題と途中までの回答）に当てはめ、その状態から回答を生成し直す。Llama-3.2-3B-Instruct の数学では、6 つのベンチマークの平均が GRPO の 15.4、Reinforce-Ada の 15.0 に対して 19.1 だった。

この章で確かめた公開モデルの技術報告には、こうした配分を使ったという記述は無かった。MiMo-V2.6 は 1 問に 16 個、Nemotron 3.5 Super も 1 問に 16 個と、決まった数を書いている。Granite 4.2 は、30B のモデルの段ごとの設定の表で 1 問あたりの数を 16 と 32 で変えているが、問題ごとには変えない。Qwen3.8-Max の online data balancer は、batch の中の課題と難しさの分布を均衡させる仕組みで、ブログには目標の割合も、問題ごとに回答の数を変えるかどうかも書かれていない。

## 開示の濃淡

報告によって、書かれている深さは大きく違う。Qwen3.8-Max のブログは、報酬の仕組みと batch の組み方を挙げているが、目的関数の名前は書いていない。MiniMax の [M3.1-Flash のブログ](https://www.minimax.io/blog/minimax-m31f) は、構造と評価の値を中心に書いており、RL の方法には触れていない。[LLM-jp-4.1 のブログ](https://llm-jp.nii.ac.jp/blog/llm-jp-4-1/) は、事後学習を SFT と DPO で組み、強化学習を取り入れたモデルの開発を今後進めると書いている。KAT-Coder-V2.5、MiMo-V2.6、Nemotron 3.5、Granite 4.2 は、目的関数、ハイパーパラメータ、基盤の細部まで書いている。

## 公開モデルでの組み合わせ

この章で読んだ資料に書かれている内容を並べる。「記載なし」は、その資料に書かれていないことを指し、使っていないことを意味しない。

| モデル | 資料の日付 | 目的関数 | 訓練と推論のずれ | OPD |
|---|---|---|---|---|
| [LongCat-Flash-Thinking-2601](https://arxiv.org/abs/2601.16725) | 2026 年 1 月 | GSPO | 記載なし | 記載なし |
| [Step 3.5 Flash](https://arxiv.org/abs/2602.10604) | 2026 年 2 月 | MIS-PO（2 値の判定）、価値の推定 | token と回答の 2 つの粒度の判定 | 記載なし |
| [Intern-S1-Pro](https://arxiv.org/abs/2603.25040) | 2026 年 3 月 | REINFORCE と 2 つの比 | IcePop に従う masking、rollout router replay | 記載なし |
| [Ling と Ring 2.6](https://arxiv.org/abs/2606.15079) | 2026 年 6 月 | 記載の範囲では不明 | KPop（2 択の KL による masking） | 記載なし |
| [KAT-Coder-V2.5](https://arxiv.org/abs/2607.05471) | 2026 年 7 月 | PPO と critic | template を通さない生成の口 | 多教師の OPD |
| [Kimi K3](https://arxiv.org/abs/2607.24653) | 2026 年 7 月 | 式は記載なし | 古いデータへの token ごとの正則化 | 9 個の専門家の多教師の OPD |
| [Qwen3.8-Max](https://qwen.ai/blog?id=qwen3.8) | 2026 年 8 月 | 記載なし | 記載なし | 記載なし |
| [Granite 4.2](https://huggingface.co/blog/ibm-granite/granite-4-2) | 2026 年 8 月 | 非同期の GRPO | 記載なし | 記載なし |
| [Hunyuan-A13B](https://arxiv.org/abs/2609.27284) | 2026 年 9 月 | GRPO | 記載なし | 記載なし |
| [DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/DeepSeek_V41_Tech_Report.pdf) | 2026 年 10 月 | GRPO | concatenated routing-replay、古い token の masking | 40 を超える教師、語彙全体 |
| [MiMo-V2.6](https://arxiv.org/abs/2610.11959) | 2026 年 10 月 | GRPO と 4 つの境界 | 推論側の確率で直接割る | MOPD2 |
| [Step-5-Preview](https://platform.stepfun.ai/blog/inside-step-5-preview) | 2026 年 10 月 | 記載なし | ビット単位の一致、経路と候補の再生 | 記載なし |
| [Nemotron 3.5 Super](https://arxiv.org/abs/2610.38095) | 2026 年 10 月 | 非同期の GRPO | router replay、出力層の FP32 | 2 段の多教師の OPD |
| [GLM-5.3](https://z.ai/blog/glm-5.3) | 日付の記載なし | 記載なし | 一致の評価で log 確率の差が $10^{-7}$ の水準 | 基盤で top-p、top-k、語彙全体を選べる |

DeepSeek-V4.1-Flash の日付は Hugging Face で重みが公開された月である。GLM-5.3 のブログには日付が無く、前の版の GLM-5.2 のブログは 2026 年 10 月 13 日に公開された。ほかは arXiv の v1 か、ブログとリリースノートの公開日である。

## まとめ

この章で読んだ 2026 年後半の公開モデルの事後学習は、目的関数の名前より、回答をどの確率で扱うか、どの教師から学ぶか、どの環境で作るかで違いが出ている。問題ごとに回答の数を変える研究はネイマン配分の形まで進んでいるが、この章で確かめた報告には、使ったという記述はまだ無い。

## 参考: この章で扱った資料

| 資料 | 公開 | この章での役割 |
|---|---|---|
| [Knapsack RL](https://arxiv.org/abs/2509.25849) | 2025 年 9 月 | ナップサック問題としての回答の配分 |
| [Reinforce-Ada](https://arxiv.org/abs/2510.04996) | 2025 年 10 月 | 正解率に応じた回答の配分 |
| [LongCat-Flash-Thinking-2601](https://arxiv.org/abs/2601.16725) | 2026 年 1 月 | GSPO の採用 |
| [Step 3.5 Flash](https://arxiv.org/abs/2602.10604) | 2026 年 2 月 | MIS-PO |
| [Intern-S1-Pro](https://arxiv.org/abs/2603.25040) | 2026 年 3 月 | IcePop に従う 2 つの比 |
| [Ling と Ring 2.6](https://arxiv.org/abs/2606.15079) | 2026 年 6 月 | KPop |
| [sGPO](https://arxiv.org/abs/2606.08854) | 2026 年 6 月 | 正解率の段階による回答の配分 |
| [Tencent Hy3 の RL の手順書](https://huggingface.co/tencent/Hy3/raw/main/rl/README.md) | 2026 年 7 月 | IcePop の帯の値の例 |
| [KAT-Coder-V2.5](https://arxiv.org/abs/2607.05471) | 2026 年 7 月 | PPO と critic、環境の規模、token のずれ |
| [SAO](https://arxiv.org/abs/2607.07508) | 2026 年 7 月 | 古い方策を持たない非同期の RL |
| [Kimi K3](https://arxiv.org/abs/2607.24653) | 2026 年 7 月 | 9 個の専門家、sandbox の規模 |
| [Qwen3.8-Max のブログ](https://qwen.ai/blog?id=qwen3.8) | 2026 年 8 月 | 報酬の仕組みと batch の組み方 |
| [Granite 4.2 のブログ](https://huggingface.co/blog/ibm-granite/granite-4-2) | 2026 年 8 月 | 段ごとの GRPO の設定 |
| [LLM-jp-4.1 のブログ](https://llm-jp.nii.ac.jp/blog/llm-jp-4-1/) | 2026 年 9 月 | SFT と DPO による事後学習 |
| [Hunyuan-A13B](https://arxiv.org/abs/2609.27284) | 2026 年 9 月 | GRPO の採用 |
| [MiMo-V2.6](https://arxiv.org/abs/2610.11959) | 2026 年 10 月 | 4 つの境界、費用、MOPD2 |
| [Apertus 1.5](https://arxiv.org/abs/2610.15542) | 2026 年 10 月 | ツールを使う問題を RL から外した例 |
| [VSR](https://arxiv.org/abs/2610.15888) | 2026 年 10 月 | ネイマン配分の形の配分 |
| [prime-rl v0.10.0](https://github.com/PrimeIntellect-ai/prime-rl/releases/tag/v0.10.0) | 2026 年 10 月 | IPO |
| [Step-5-Preview のブログ](https://platform.stepfun.ai/blog/inside-step-5-preview) | 2026 年 10 月 | ビット単位の一致と再生 |
| [DeepSeek-V4.1-Flash の技術報告](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/DeepSeek_V41_Tech_Report.pdf) | 2026 年 10 月 | 語彙全体の OPD、非同期の生成、sandbox の規模 |
| [MiniMax M3.1-Flash のブログ](https://www.minimax.io/blog/minimax-m31f) | 2026 年 10 月 | RL の方法に触れていない例 |
| [FlashNext-RL](https://arxiv.org/abs/2610.28998) | 2026 年 10 月 | 経路の食い違いがずれを説明する割合 |
| [Muse Glimmer 30B](https://arxiv.org/abs/2610.24628) | 2026 年 10 月 | OPD の採用の理由 |
| [Nemotron 3.5 Super と Lightning](https://arxiv.org/abs/2610.38095) | 2026 年 10 月 | 2 段の OPD、router replay、出力層の FP32 |
| [GLM-5.3 のブログ](https://z.ai/blog/glm-5.3) | 日付の記載なし | log 確率の差と OPD の粒度 |
