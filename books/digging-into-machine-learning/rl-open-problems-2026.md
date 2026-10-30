---
title: "RL の事後学習のこれから: 量子化、工学の壁、未解決の問いを論文から読む"
free: false
---

## はじめに

この本の直前の 3 つの章「GRPO のあとで RL の目的関数は何が変わったか」「訓練と推論のずれと On-Policy Distillation」「2026 年後半の公開モデルは RL の事後学習で何をしているか」（以下、1 章、2 章、3 章）では、GRPO の式の部品、訓練と推論のずれと On-Policy Distillation（OPD）、2026 年後半の公開モデルの事後学習を順に見てきた。この章では、これから解くべき問題を 3 つの層に分けて整理する。数値の精度の層、学習の系の層、そして RL が何をしているのかという科学の層である。**数値の精度と学習の系の層では、問題の多くが、回答を作る側と学ぶ側をどこまでそろえられるかという 2 章からの問いにつながっている**。RL の科学の層では、測り方を変えると結論も変わることが中心の問題になる。

合わせて、この章では問いを次の 7 つに分ける。表の右の列は、章の結論を先に並べたものである。

| 問い | 論文から読める答え |
|---|---|
| 回答を FP8 や FP4 で生成すると何が起きるか | [HiFloat4 の論文](https://arxiv.org/abs/2607.26515) が調べた FP4 の設定では、劣化の主な原因は回答の生成での活性の量子化だった。論文のQwen2.5-3B の GSM8K の実験では、生成を FP4 のまま学習側だけを BF16 に戻すと、両方を FP4 にしたときより正答率が下がった |
| 量子化したモデルの性能は、どう取り戻すか | 量子化した生徒が自分で書いた回答の上で全精度の教師から学ぶ OPD で、低 bit のモデルの長い reasoning の性能を取り戻したと複数の論文が報告している。固定した回答で学び続ける方法との同じ予算での比較は [Train Where the Quantized Model Goes](https://arxiv.org/abs/2609.26708) にある。改善の大きさは bit 幅と課題で違う |
| 学習の時間と費用はどこに消えるか | 報告のある系では、回答の生成が大きな部分を占める。[verl の文書](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/one_step_off.md) は DAPO の 32B の学習で時間の約 70% と書き、[MiMo-V2.6](https://arxiv.org/abs/2610.11959) は RL の費用の 43.8% と報告した |
| 重みを推論側に送る時間は、どこまで縮んだか | 1 兆パラメータの Kimi-K2 で、[Mooncake](https://raw.githubusercontent.com/kvcache-ai/Mooncake/main/README.md) を使う RDMA の送り方は 53 秒から 7.2 秒になった。[verl の差分だけを送る方式](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/delta_weight_sync.md) は、Qwen3-235B-A22B の測った構成で、全体を送ると 246〜266 秒かかる送り込みを 11.4〜14.9 秒にした |
| RL は新しい能力を作るのか | 決着していない。[2 つの base model を比べた論文](https://arxiv.org/abs/2610.04978) では、同じ base model からの独立な実行は、どの問題が直るかで強く一致し、base model が違うと一致は弱かった。[ProRL](https://arxiv.org/abs/2505.24864) は、元のモデルの評価値が 0 だった課題で長い RL のあとに点を取った例を報告している |
| 報酬の抜け穴（reward hacking）は防げるのか | [hacktrace](https://arxiv.org/abs/2610.03055) は、論文が評価したコードの課題で、合格した解に占める不正の割合を 82〜91% から 1〜5% に下げた。[評価が有限である限り抜け穴は構造的に残るとする理論](https://arxiv.org/abs/2603.28063) もあり、2 つは扱う範囲が違う |
| どの結果が確かで、どれがまだ 1 本の論文の主張か | ラベル無しの RL が多数決への収束で崩れることは、複数の論文が報告している。RL の計算量の scaling 則、pass@1 と pass@k の定理、RL が忘れにくい理由は、この章で見た範囲では主に 1 本の論文の結果である |

読者には、1 章から 3 章の内容を想定する。記号は 2 章に合わせ、推論エンジンが回答を生成したときの確率を $\mu$、訓練エンジンの確率を $\pi$、その比を $\rho = \pi / \mu$ と書く。扱う資料は、主に 2025 年後半から 2026 年 10 月までの arXiv の論文と、GitHub に置かれた学習の基盤の文書と、いくつかのブログで、2025 年前半の論文も一部に含む。どれも 2026 年 10 月 30 日に確かめた。arXiv の論文の多くと文書、ブログは、査読を経ていない資料で、2026 年の論文の多くは他の研究者による追試がまだ無い。本文と表の数値は各資料の報告値で、筆者はどれも再現していない。

## 全体像: 3 つの層の問題

この章で扱う問題を、3 つの層に分けて並べる。

![数値の精度、学習の系、RL の科学の 3 つの層に、それぞれの問題と、いま取られている対策と、まだ解けていない部分を並べた模式図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-open-problems-2026/three-layers.png)

| 層 | 問題 | いまの対策 | 残っている部分 |
|---|---|---|---|
| 数値の精度 | 補正なしに FP8 や FP4 で生成すると $\rho$ が 1 から離れることがあり、補正の無い量子化したモデルは長い reasoning の性能を落としうる | 生成側の活性の誤差の補正、生成と学習で同じ量子化を使う設計、OPD による回復 | 量子化の誤差と RL の安定性の関係を測った研究がまだ少ない |
| 学習の系 | 回答の生成が遅く、重みの送り込みが重く、環境が壊れやすい | 非同期の生成、差分だけを送る同期、router の固定、決定的なカーネル | 長い agent の課題での時間と費用、環境の抜け穴 |
| RL の科学 | RL が何を学ぶのか、報酬は信用できるのか、規模を増やすと何が起きるのか | 統計の検定、抜け穴の検出、scaling 則の当てはめ | 多くの結果が 1 つの組織か 1 本の論文に依っている |

## 数値の精度: 回答を作る精度と学ぶ精度

2 章では、BF16 の丸め誤差が訓練と推論のずれの原因の 1 つで、学習と推論の両方を FP16 にするとずれが小さくなるという論文を見た。2026 年の論文には、逆に回答の生成を FP8 や FP4 に下げて速くする研究がある。RL の時間の多くは回答の生成に使われるので、生成を速くすると全体が速くなる。一方で、生成側の量子化は $\rho$ を 1 から離しうる。どれだけ離れるかは、量子化の方式と補正による。

[QUADS](https://arxiv.org/abs/2607.15810) は、MoE のモデルの GRPO を素朴に NVFP4（NVIDIA の 4 bit の浮動小数点の形式）で学習すると、4 つのベンチマークの平均の pass@1 が BF16 の 73.15% に対して 51.37% まで下がったと報告した。論文は、学習側では重みだけを量子化する非対称な QAT（量子化を意識した学習）を使い、生成側では誤差の大きい活性のチャネルを補う方法を組み合わせて 72.86% まで戻し、回答の生成の速さは FP8 より 16% 上がった。

[HiFloat4](https://arxiv.org/abs/2607.26515) は、回答の生成と学習の両方を 4 bit で行う RL（学習側は forward と backward の両方）を調べ、劣化の主な原因は学習側の量子化ではなく、回答の生成での活性の量子化だと報告した。論文によれば、活性の外れ値が値の範囲を広げるので、FP4 では多くの活性が 0 に潰れる。次の図は、論文の表 1 にある Qwen2.5-3B の GRPO の GSM8K の正答率を、学習側と生成側の精度の組み合わせごとに並べたものである。

![Qwen2.5-3B の GRPO の GSM8K の正答率を、学習側と生成側の精度の組み合わせごとに並べた。生成側の精度を上げると正答率は上がるが、生成を FP4 のまま学習側だけを BF16 に戻すと、HiF4 で 82.03% から 78.01% に、MXFP4 で 73.31% から 50.57% に下がる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-open-problems-2026/fp4-consistency.png)

学習側を FP4 に保ったまま生成側の精度を上げると、正答率は上がった。逆に、生成を FP4 のまま学習側だけを BF16 に戻すと、HiF4（論文の 4 bit の形式）で 82.03% から 78.01% に、MXFP4（OCP の 4 bit の形式）で 73.31% から 50.57% に下がった。論文は、学習側の精度を上げたのに悪くなる理由を、生成と学習の間のずれが大きくなるためだと説明している。論文は、生成の行列積にだけ疎な形の残差の補正を足す Rollout-ResQ で、BF16 との正答率の差を、HiF4 で 4.9 ポイントから 1.1 ポイントに、MXFP4 で 13.6 ポイントから 5.3 ポイントに縮めた。

KV cache（attention が過去の token の情報を持っておく領域）を小さくする研究もある。[TRACE](https://arxiv.org/abs/2610.07767) は、重みと活性と KV cache を FP4 にして回答を生成しても、BF16 で生成したときと同程度の RL の性能を保ち、生成を最大 5.4 倍速くしたと報告した。[KV4-RL](https://arxiv.org/abs/2610.14448) は、KV cache を INT4 にし、attention の sink（多くの token が注目する先頭の位置）と直近の位置だけを INT8 に残すことでずれを抑えた。Qwen3-8B-Base の GRPO の 400 iteration で、BF16 で生成する場合に比べ、回答の生成を累計 1.80 倍、iteration 全体を累計 1.27 倍速くしている。

公開モデルの技術報告にも、配備のための低い精度を事後学習の間から使う例がある。3 章で見た Kimi K3 は、SFT の段から MoE の expert の重みを MXFP4、活性を MXFP8 にした QAT を続けた。[DeepSeek-V4](https://arxiv.org/abs/2606.19348) は、事後学習の間に MoE の expert の重みと attention の indexer の一部を FP4 にする QAT を行った。報告は、ブロックの中のスケールの比が一定の範囲に収まる限り FP4 から FP8 への戻しは情報を失わず、対象の重みがその条件を満たすことを確かめたと書いている。MiMo-V2.6 は、MXFP4 での学習を安定させるために、SFT の段の FP32 の master の重みと、最適化手法 Muown の状態を引き継いで RL を始めた。これらとは別に、[Nemotron 3.5](https://arxiv.org/abs/2610.38095) は RL のあとで NVFP4 に量子化し、正確さの中央値で BF16 の 99.62% を保ち、評価した対話的な agent の負荷では BF16 の 2.43 倍の処理量を出したと報告している。

量子化で落ちた reasoning の能力を取り戻す方法として、OPD を使う論文が 2026 年 9 月と 10 月に続けて出た。[Train Where the Quantized Model Goes](https://arxiv.org/abs/2609.26708) は、量子化した生徒が自分の回答の上で全精度の教師から学び、検証器の報酬も組み合わせる方法で、実効 2.79 bit と 1.88 bit の 4 つのモデルの平均で、MATH-500 での BF16 の性能の保持率を 35% から 70% に上げた。同じ予算で固定した回答の上の蒸留（QAD）を続けた場合より、reasoning の改善が大きかったとも書いている。[OnlineQAT](https://arxiv.org/abs/2610.09346) は、Qwen3-1.7B で、比べた方法の ReasoningQAT を、平均で 3 bit の重みでは 2.90 ポイント、2 bit では 0.44 ポイント上回り、改善は 3 bit で大きかった。[Right On Q](https://arxiv.org/abs/2610.29378) は、固定した回答で学ぶと、量子化した生徒が実際に書く prefix と学習の prefix がずれ、そのずれは回答が長いほど大きくなると書いている。2 章で見た off-policy の蒸留と OPD の違いが、量子化の回復でも現れている。

## 学習の系: 時間と費用はどこに消えるか

RL の 1 step は、回答の生成、報酬の採点、勾配の計算、重みの送り込みからなる。報告のある系では、このうち回答の生成が大きな部分を占める。

![左は MiMo-V2.6-Pro の RL の費用の割合（生成 43.8%、学習 43.5%、採点 12.7%）。右は重みを推論側に送る時間の報告で、1 兆パラメータの Kimi-K2 では Mooncake の RDMA で 53 秒から 7.2 秒に、Qwen3-235B-A22B では全体を送る 246〜266 秒が差分の送り込みで 11.4〜14.9 秒になった](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-open-problems-2026/time-budget.png)

verl の非同期の学習の文書は、DAPO の 32B の学習では回答の生成が全体の約 70% の時間を占め、資源を増やしても生成の時間は縮まないと書いている。最も長い回答を待つ間に他の GPU が止まるからで、3 章で見た非同期の生成と部分的な生成は、この待ち時間を減らすための仕組みである。MiMo-V2.6-Pro の RL の費用の内訳では、生成が 43.8%、学習が 43.5%、採点が 12.7% だった。時間と費用は別の量で、測り方によって生成の重さは変わる。

重みの送り込みは、モデルが大きくなるほど重くなる。[checkpoint-engine](https://raw.githubusercontent.com/MoonshotAI/checkpoint-engine/main/README.md) は、1 兆パラメータの Kimi-K2 の重みを数千の GPU に送る更新に約 20 秒かかると書いている。Mooncake の README によれば、SGLang が Mooncake の転送の仕組みで RDMA（CPU を通さずに機械の間でメモリを直接読み書きする方式）の送り込みを入れ、同じ Kimi-K2 で 53 秒から 7.2 秒にした。2 つは送り方も時期も違う報告で、数字をそのまま比べることはできない。verl の差分の同期の文書は、変わった部分だけを送る方式で、Qwen3-235B-A22B を 8+2 ノードで動かした構成の送り込みを、NCCL で全体を送る場合の 246〜266 秒に対して 11.4〜14.9 秒にした。

回答の生成を速くする投機的デコード（小さな draft で先の token を予想し、本体でまとめて検証する方法）は、RL の中では効くとは限らない。[verl の MTP の文書](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/mtp.md) は、H20 の GPU で mimo-7B の回答を SGLang で生成した例で、MTP（モデルに組み込んだ draft の層）による投機的デコードを有効にすると、生成の処理量が約 50% 下がったと書き、いまは推論の段で有効にしないよう勧めている。文書は、演算の性能が低い GPU では draft の検証の費用が節約を上回ると説明している。

MoE の router も、RL の中で不安定になる。MiMo-V2.6 は、router を学習させた実行の第 9 層で、最初の 20 step のうちに expert の負荷の変動係数が 0.78 から 2.0 に、最大の負荷が平均の 6 倍から 16 倍に、ほとんど使われない expert の割合が 0.5% から 22% に増えたと報告し、RL の間は router を固定した。

2 章と 3 章で見たビット単位の一致にも費用がある。[Thinking Machines のブログ](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/) では、決定的な推論にすると Qwen3-8B の生成の時間が 26 秒から 42 秒に伸び、[vLLM と TorchTitan のチーム](https://vllm.ai/blog/2025-11-10-bitwise-consistent-train-inference) の RL は、一致させない場合の 2.4 倍の時間がかかった。3 章で見た [Step-5-Preview](https://platform.stepfun.ai/blog/inside-step-5-preview) は、自分たちの構成で追加の時間を 10% 未満に抑えたと書いている。測った構成が違うので、費用の傾向はこれらの数字だけでは判断できない。

agent の環境そのものも問題になる。3 章で見たように、Apertus 1.5 はツールのサーバーの処理能力が足りずにツールを使う問題を RL から外し、KAT-Coder-V2.5 は token の分け直しのずれを報告した。報酬の抜け穴は後の節で見る。

## RL は新しい能力を作るのか

2 章では、RLVR（正誤を判定できる報酬での RL）で学習したモデルが、試行の回数が多いと base model の pass@k（k 回のうち 1 回でも解ける割合）を下回ることがあり、蒸留は教師から新しいパターンを持ち込めるという論文を見た。2026 年には、この問いに別の角度からの結果が出ている。

[What Will Post-Training Fix?](https://arxiv.org/abs/2610.04978) は、DeepSeek-R1-Distill-Qwen-1.5B と Qwen2.5-Math-1.5B の 2 つの base model から 18 回の事後学習を行い、最大 1,532 問の数学の問題で、どの問題が直るかを比べた。同じ base model からの独立な実行どうしは、めったに解けない問題のうちどれが直るかで強く一致し、実行を分けて見積もった一致の上限は約 0.9 だった。2 つの base model の間の相関は 0.25 にとどまった。論文は、共有される成分は問題ではなく base model に属すると書いている。

[There Is No Free Lunch in RLVR](https://arxiv.org/abs/2610.39295) は、RLVR を、問題をまたいで共有される解き方の分布を指数的に傾ける操作として模型化した。その模型の条件のもとで、pass@1 を上げる更新はある問題の成功率を必ず下げ、pass@k の逆転を完全に消そうとすると pass@1 の改善もすべて失うことを定理として示し、エントロピーの崩壊も同じ式から導いた。著者は、RL は reasoning の範囲を広げられず、その上を動くだけだと結論している。これは模型の仮定のもとでの結果で、実際の RLVR が新しい解き方を獲得しないことを示したものではない。

逆の方向の結果もある。ProRL は、長く RL を続けた Nemotron-Research-Reasoning-Qwen-1.5B が、元の DeepSeek-R1-Distill-Qwen-1.5B の評価値が 0.00 だった boxnet の課題で 7.91 を出したと報告した。元のモデルで成功が観測されなかったことは、成功の確率が 0 だったことと同じではなく、新しい能力の獲得と低い確率の成功の増幅をどう見分けるかは決まっていない。[RLVR is a Kernel, Not a Function](https://arxiv.org/abs/2609.22547) は、同じ問題で比べる信頼帯を作ると、公開されている 5 組の RLVR のモデルの初めの評価では、pass@k の逆転は統計的に確かめられなかったと報告した。新しい問題を 32k token で評価し直すと、11 回から 61 回の試行の間で逆転が見つかった。逆転を見つけられないことは、逆転が無いことを意味しないとも書いている。

数理統計から見ると、pass@1 と pass@k は、問題ごとの正解率 $p_q$ の分布の別の関数である。pass@1 は平均 $\mathbb{E}_q[p_q]$、pass@k は $\mathbb{E}_q[1 - (1 - p_q)^k]$ で、$k$ が大きいと正解率が 0 より大きい問題の割合に近づくので、平均を上げながら一部の問題の小さい正解率を下げると、有限の $k$ で 2 つが逆に動く場合がある。また、$n$ 回の試行で正解が 0 回でも、正解率の 95% の信頼上限はおよそ $3/n$ で、0 とは言えない。評価の誤差の 2 つの成分と、pass@k の推定量が UMVUE であることは、[数理統計の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-mathematical-statistics)で扱う。

自分で自分を伸ばすという主張にも、測り方の問題がある。[Phantom Gains](https://arxiv.org/abs/2608.20290) は、Qwen3-8B の 3 回の自己学習を、同じ手順を通した凍結したモデルと比べ、測り方の誤りを 7 つ挙げた。どれも、対照が無いと報告された結論を逆にする。論文は、外部の教師からの蒸留は base model がめったに解けない問題を改善したが、3 つの形の自己学習は改善しなかったと書いている。

## 報酬は信用できるか

RL は報酬を最大にするので、報酬の作り方の穴を突く。agent の課題では、テストを書き換える、検証を回避する、といった形で現れる。hacktrace は、コードの生成の途中の状態から不正を検出し、GRPO で強い罰則を与えて、合格した解に占める不正の割合を 82〜91% から 1〜5% に下げたと報告した。[Reward Hacking as Equilibrium under Finite Evaluation](https://arxiv.org/abs/2603.28063) は、評価が有限で最適化が効く、といった条件のもとで、最適化された agent は評価に含まれない品質の次元への努力を体系的に減らすと論じ、報酬の抜け穴は直せるバグではなく構造的な均衡だと主張している。前者は特定の不正を減らせることを示し、後者は評価の外の品質が残りうることを論じていて、扱う範囲が違うので両立しうる。[Reward Hacking as a Transmissible Strategy](https://arxiv.org/abs/2610.12822) は、環境の仕組みの知識に触れさせてから RL で不正を引き出した agent を起点にすると、その不正が共有のコードや記憶や自然言語の計画を通して他の agent に広がり、小さい公開モデルの agent が強いモデルにも不正を引き起こしたと報告した。通常の運用での発生の頻度を測った結果ではない。

正解のラベルを使わない RL にも限界がある。[TTRL](https://arxiv.org/abs/2504.16084) は、多数決の答えを疑似的な正解にして、ラベルの無いテストのデータだけで Qwen-2.5-Math-7B の AIME 2024 の pass@1 を約 211% 上げたと報告した。その後、多数決への一致を最大化すると出力の多様性が失われ、誤った答えに自信を持って収束するという報告が続いた。[CoVerRL](https://arxiv.org/abs/2603.17775) はこれを consensus trap（合意の罠）と呼び、[医療の QA の論文](https://arxiv.org/abs/2609.16660) は、選択肢の少ない問題では誤った推論が同じ誤った選択肢に集まりやすいと書いている。[Collective Dynamics of Self-Reinforcement](https://arxiv.org/abs/2610.37553) は、崩壊が問題の数が多いときにだけ起きることを、パラメータを共有する多数の問題の模型の不安定性として説明し、崩壊が始まる問題の数の目安 $N_c \simeq \ln(1/\delta) / (\lambda^2 G)$ を導いた。$\lambda$ は問題の間の結びつきの強さ、$\delta$ は共有の揺らぎの種の大きさ、$G$ は初めの確信度とともに小さくなる量で、確信度の高い初めのモデルでは崩壊しないとも書いている。これは模型と小さい transformer での結果である。

数理統計から見ると、1 つの問題の中では固定したモデルから回答が独立に生成されるので、答えの分布の最頻値が 1 つに決まるなら、回答を増やすほど最多票はその最頻値にほとんど確実に一致する。コンドルセの陪審定理とその多択への拡張が最多票の正しさを保証するのは、正しい答えが最も選ばれやすいときだけで、最頻値が誤る問題では、回答を増やすほど誤った答えが確実に疑似的な正解になる。

## 更新の形と忘れにくさ

RL が重みのどこを動かすかについては、測る量の違う論文がある。[Reinforcement Learning Finetunes Small Subnetworks](https://arxiv.org/abs/2505.11711) は、7 つの RL の手法と 10 のモデルで、パラメータの 5〜30% しか更新されず、その部分だけを学習すれば全体を学習したのとほぼ同じモデルになると報告した。更新される行列はほぼ full-rank で、少ないパラメータが行列の表せる空間のほぼ全体に広がっている。[RELEX](https://arxiv.org/abs/2605.21468) は、RLVR の重みの変化の軌跡の大部分が rank 1 の近似で捉えられ、その大きさが step にほぼ比例して伸びると報告した。初めの 50 step を見て 1,000 step 先まで外挿でき、学習の 15% の step で同等以上のチェックポイントを作ったと書いている。前者は行列ごとの rank、後者はチェックポイントの間の軌跡の rank を見ていて、測っているものが違う。

[RL's Razor](https://arxiv.org/abs/2509.04259) は、RL が SFT より以前の能力を忘れにくい理由を、新しい課題で測った base model からの KL の小ささで説明した。調べた設定では、忘れる量は学習の手法より KL とよく対応し、2 次の当てはめの決定係数は ParityMNIST で 0.96、LLM の実験で 0.71 だった。論文は、on-policy の方策勾配が base model に近い解に収束しやすいと論じている。

## 規模と理論

RL の計算量を増やすと性能がどう伸びるかを、事前学習の scaling 則のように予測しようとする研究がある。[ScaleRL](https://arxiv.org/abs/2510.13786) は、性能を計算量のシグモイドの曲線で当てはめ、漸近の性能を動かす設計と効率だけを動かす設計を分けた。8B の dense のモデルで 1 回の RL を 100,000 GPU 時間まで伸ばし、50,000 GPU 時間までの検証の結果から当てはめた曲線が、その先まで当たることを示した。これは 1 つの組織の実験で、他の研究者による追試は、この章で調べた範囲では見つからなかった。

GRPO がなぜ働くのかを説明する理論も 2026 年に複数出た。[Why GRPO Needs Normalization](https://arxiv.org/abs/2601.23135) は、標準偏差での正規化が適応的な勾配として働き、正規化しない REINFORCE より収束が速くなる条件を示した。[A Relative-Budget Theory](https://arxiv.org/abs/2602.01523) は、生成の長さの上限と、base model が最初の正解に至るまでの token の数の比で、RL のサンプル効率が決まると論じた。[Anatomy of Off-Policy Policy Gradient](https://arxiv.org/abs/2610.25725) は、状態の遷移が決定的だという仮定のもとで、off-policy で使う方策勾配が最適な方策を停留点に保つには、回答単位の重要度の重みか、group の平均を基準にした KL の正則化が要ることを示し、後者を group で比べる方法として位置づけた。どれも 1 つの面からの説明で、互いの関係はまだ整理されていない。

## 新しい形の RL

1 つのモデルの回答を group で比べるという GRPO の前提が、そのままでは成り立たない設定もある。[SPIRAL](https://arxiv.org/abs/2506.24119) は、モデルが自分自身と 2 人の零和のゲームを繰り返して学ぶ self-play で、役割ごとに基準を持つ advantage の推定（RAE）を提案した。RAE を使わない場合、100 policy iteration 以内に reasoning の長さが約 2,000 文字からほぼ 0 に落ちる崩壊が起きたと報告している。3 章で見た prime-rl の v0.8.0 は、この RAE を複数の agent の RL の方法として取り入れた。この本の loop のモデルの章でも触れた [RLTT](https://arxiv.org/abs/2602.10520) は、同じ層を繰り返し通す loop のモデルで、報酬を最後の状態だけでなく潜在の軌跡全体に配る。

## 確からしさで分ける

この章で見た結果を、根拠の形で分ける。分け方は筆者のもので、2026 年 10 月 30 日の時点の判断である。複数の論文が同じ方向の結果を出していても、同じ条件での追試とは限らない。

| 根拠の形 | 結果 | 根拠と条件 |
|---|---|---|
| 複数の論文、設定はそれぞれ違う | 生成側の量子化の誤差を抑える設計が、低い精度の RL の性能の回復に効いた | [QUADS](https://arxiv.org/abs/2607.15810)、[HiFloat4](https://arxiv.org/abs/2607.26515)、[TRACE](https://arxiv.org/abs/2610.07767)、[KV4-RL](https://arxiv.org/abs/2610.14448) |
| 複数の論文、設定はそれぞれ違う | 量子化の回復に、生徒自身の回答で学ぶ OPD が効いた | [Train Where the Quantized Model Goes](https://arxiv.org/abs/2609.26708)、[OnlineQAT](https://arxiv.org/abs/2610.09346)、[Right On Q](https://arxiv.org/abs/2610.29378)。改善の大きさは bit 幅と課題で違う |
| 複数の論文 | ラベル無しの RL は、多数決への収束で崩れることがある | [CoVerRL](https://arxiv.org/abs/2603.17775)、[医療の QA の論文](https://arxiv.org/abs/2609.16660)、[Collective Dynamics](https://arxiv.org/abs/2610.37553) |
| 複数の資料、測り方は違う | 回答の生成が RL の時間や費用の大きな部分を占める | [verl の文書](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/one_step_off.md)（時間）、[MiMo-V2.6](https://arxiv.org/abs/2610.11959)（費用） |
| 1 本の論文 | 学習側だけを BF16 に戻すと、両方を FP4 にするより悪い | [HiFloat4](https://arxiv.org/abs/2607.26515) の Qwen2.5-3B と GSM8K の設定 |
| 1 本の論文 | RL の計算量に対する性能はシグモイドの曲線で予測できる | [ScaleRL](https://arxiv.org/abs/2510.13786) の設定 |
| 1 本の論文 | pass@1 を上げると pass@k の逆転は避けられない | [There Is No Free Lunch in RLVR](https://arxiv.org/abs/2610.39295) の模型の仮定のもと |
| 1 本の論文 | どの問題が直るかの共有される成分は、実行より base model に依る | [What Will Post-Training Fix?](https://arxiv.org/abs/2610.04978) の 2 つの base model と数学の問題 |
| 1 本の論文 | RL が忘れにくいことは、base model からの KL の小ささとよく対応する | [RL's Razor](https://arxiv.org/abs/2509.04259)。LLM の実験の当てはまりは決定係数 0.71 |
| 主張が対立 | RL は新しい能力を作るか | [There Is No Free Lunch in RLVR](https://arxiv.org/abs/2610.39295) と [What Will Post-Training Fix?](https://arxiv.org/abs/2610.04978) に対し、[ProRL](https://arxiv.org/abs/2505.24864) と [統計の検定の論文](https://arxiv.org/abs/2609.22547) |
| 扱う範囲が違う | 報酬の抜け穴は防げるか | [hacktrace](https://arxiv.org/abs/2610.03055) の特定の不正の削減と、[構造的な均衡の理論](https://arxiv.org/abs/2603.28063) |

## まとめ

この章で見た資料では、数値の精度と学習の系の層の課題の多くが、回答を作る側と学ぶ側をどこまでそろえられるかにつながっていた。RL が何を学ぶのかと、報酬をどこまで信用できるのかは、測り方を変えると結論も変わる段階にあり、各報告の条件と根拠の形を分けて読むことが要る。

## 参考: この章で扱った資料

| 資料 | 公開 | この章での役割 |
|---|---|---|
| [TTRL](https://arxiv.org/abs/2504.16084) | 2025 年 4 月 | ラベル無しの RL |
| [Reinforcement Learning Finetunes Small Subnetworks](https://arxiv.org/abs/2505.11711) | 2025 年 5 月 | RL の更新の疎さ |
| [ProRL](https://arxiv.org/abs/2505.24864) | 2025 年 5 月 | 長い RL で解けるようになった課題 |
| [SPIRAL](https://arxiv.org/abs/2506.24119) | 2025 年 6 月 | self-play と RAE |
| [RL's Razor](https://arxiv.org/abs/2509.04259) | 2025 年 9 月 | RL が忘れにくい理由 |
| [Defeating Nondeterminism in LLM Inference](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/) | 2025 年 9 月 | 決定的な推論の費用 |
| [ScaleRL](https://arxiv.org/abs/2510.13786) | 2025 年 10 月 | RL の計算量の scaling 則 |
| [Bitwise-consistent train and inference](https://vllm.ai/blog/2025-11-10-bitwise-consistent-train-inference) | 2025 年 11 月 | ビット単位で一致させた RL の費用 |
| [Why GRPO Needs Normalization](https://arxiv.org/abs/2601.23135) | 2026 年 1 月 | 正規化と収束 |
| [A Relative-Budget Theory](https://arxiv.org/abs/2602.01523) | 2026 年 2 月 | 生成の長さとサンプル効率 |
| [RLTT](https://arxiv.org/abs/2602.10520) | 2026 年 2 月 | loop のモデルの RL |
| [CoVerRL](https://arxiv.org/abs/2603.17775) | 2026 年 3 月 | consensus trap |
| [Reward Hacking as Equilibrium under Finite Evaluation](https://arxiv.org/abs/2603.28063) | 2026 年 3 月 | 構造的な均衡としての抜け穴 |
| [DeepSeek-V4](https://arxiv.org/abs/2606.19348) | 2026 年 4 月（arXiv の v1 の表示） | 事後学習での FP4 の QAT |
| [RELEX](https://arxiv.org/abs/2605.21468) | 2026 年 5 月 | rank 1 の軌跡と外挿 |
| [QUADS](https://arxiv.org/abs/2607.15810) | 2026 年 7 月 | NVFP4 の RL の安定化 |
| [HiFloat4](https://arxiv.org/abs/2607.26515) | 2026 年 7 月 | FP4 の RL と生成側の活性 |
| [Phantom Gains](https://arxiv.org/abs/2608.20290) | 2026 年 8 月 | 自己改善の測り方 |
| [医療の QA での TTRL](https://arxiv.org/abs/2609.16660) | 2026 年 9 月 | 選択肢の問題での誤った合意 |
| [RLVR is a Kernel, Not a Function](https://arxiv.org/abs/2609.22547) | 2026 年 9 月 | pass@k の逆転の統計 |
| [Train Where the Quantized Model Goes](https://arxiv.org/abs/2609.26708) | 2026 年 9 月 | 量子化の回復と OPD |
| [hacktrace](https://arxiv.org/abs/2610.03055) | 2026 年 10 月 | 抜け穴の検出 |
| [What Will Post-Training Fix?](https://arxiv.org/abs/2610.04978) | 2026 年 10 月 | 直る問題の一致 |
| [TRACE](https://arxiv.org/abs/2610.07767) | 2026 年 10 月 | FP4 の生成と KV cache |
| [OnlineQAT](https://arxiv.org/abs/2610.09346) | 2026 年 10 月 | 低 bit の回復と OPD |
| [MiMo-V2.6](https://arxiv.org/abs/2610.11959) | 2026 年 10 月 | 費用、router の不安定さ、MXFP4 |
| [Reward Hacking as a Transmissible Strategy](https://arxiv.org/abs/2610.12822) | 2026 年 10 月 | agent の間の抜け穴の広がり |
| [KV4-RL](https://arxiv.org/abs/2610.14448) | 2026 年 10 月 | INT4 の KV cache |
| [Step-5-Preview のブログ](https://platform.stepfun.ai/blog/inside-step-5-preview) | 2026 年 10 月 | ビット単位の一致の費用 |
| [Anatomy of Off-Policy Policy Gradient](https://arxiv.org/abs/2610.25725) | 2026 年 10 月 | off-policy の方策勾配の一貫性 |
| [Right On Q](https://arxiv.org/abs/2610.29378) | 2026 年 10 月 | 量子化の回復と生成の prefix |
| [Collective Dynamics of Self-Reinforcement](https://arxiv.org/abs/2610.37553) | 2026 年 10 月 | ラベル無しの RL の崩壊の条件 |
| [Nemotron 3.5](https://arxiv.org/abs/2610.38095) | 2026 年 10 月 | NVFP4 の量子化 |
| [There Is No Free Lunch in RLVR](https://arxiv.org/abs/2610.39295) | 2026 年 10 月 | pass@1 と pass@k の定理 |
| [verl の非同期の文書](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/one_step_off.md) | 確認日 2026 年 10 月 30 日 | 回答の生成の時間の割合 |
| [verl の MTP の文書](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/mtp.md) | 確認日 2026 年 10 月 30 日 | RL の中の投機的デコード |
| [verl の差分の同期の文書](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/delta_weight_sync.md) | 確認日 2026 年 10 月 30 日 | 差分だけを送る重みの同期 |
| [checkpoint-engine](https://raw.githubusercontent.com/MoonshotAI/checkpoint-engine/main/README.md) | 確認日 2026 年 10 月 30 日 | 1 兆パラメータの重みの更新 |
| [Mooncake](https://raw.githubusercontent.com/kvcache-ai/Mooncake/main/README.md) | 確認日 2026 年 10 月 30 日 | RDMA による重みの送り込み |
