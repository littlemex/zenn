---
title: "2026 年後半の公開モデルの RL 事後学習と未解決の課題: 量子化、学習基盤、agent RL、評価"
free: false
---

## はじめに

この本の前の 2 つの章、[目的関数の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-objectives-and-estimators)と[訓練と推論のずれと OPD の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-mismatch-async-and-opd)では、GRPO の目的関数の部品と、訓練と推論のずれ、On-Policy Distillation（OPD）を、方策勾配の推定量として読んだ。この章では、2026 年後半の公開モデルがそれらの部品をどう組み合わせ、大規模に動かすときに何が問題になり、何がまだ分かっていないかを整理する。**公開モデルは同じ部品を組み合わせていて、差が出るのは周りの工学と環境である。時間と費用の大きな部分は回答の生成にあり、agent の RL では環境の多様さと報酬の信頼性が問題になり、RL で何を得たかの判定はまだ測り方に左右される**。

| 問い | 答え | 節 |
|---|---|---|
| 公開モデルは事後学習をどう組んでいるか | SFT、分野ごとの専門家の RL、多教師の OPD、という段の組み方が増えた。目的関数の土台は GRPO が多い | [公開モデルの組み方](#公開モデルは事後学習をどう組んでいるか) |
| 学習の時間と費用はどこに消え、低い精度で何が起きるか | 回答の生成が大きく、大きいモデルでは重みの送り込みにも時間がかかる。生成を FP4 にすると生成側と学習側の精度の組み合わせが問題になる | [大規模に動かす](#大規模に動かすと時間と費用と精度が問題になる) |
| agent の RL では何を増やせばよいか | 回答を増やす以外に、環境と課題の多様さと報酬の信頼性を調べる必要がある。Qwen3 の 14B までを調べた 1 本の研究では、scaling の指数は環境、課題、trajectory の順に大きかった | [agent の RL](#agent-の-rl-は環境と課題と報酬の信頼性で決まる) |
| RL は新しい能力を作るのか | 決着していない。pass@k の推定、問題の間の分散、対照の取り方で結論が変わる | [RL は何を得たと言えるか](#rl-は何を得たと言えるか) |

扱う資料は主に 2025 年後半から 2026 年 10 月までの arXiv の論文、技術報告、公式のブログ、学習基盤の文書で、2026 年 10 月 30 日と 31 日に確かめた。多くは査読や他の研究者による追試を経ていない。実験の数値は各資料の報告値で、筆者は再現していない。評価の誤差の例は筆者の計算である。

## 公開モデルは事後学習をどう組んでいるか

2026 年後半の公開モデルの報告のいくつかは、事後学習を 3 つの段で組んでいる。SFT で土台を作り、数学、コード、agent のような分野ごとに RL で専門家を育て、多教師の OPD で 1 つの生徒にまとめる。目的関数の土台には GRPO が多く、違いは比の扱い、ずれの扱い、教師の数と信号の粒度に出ている。

![SFT の土台から分野ごとの専門家を RL で作り、OPD で 1 つのモデルにまとめる模式図。RL の段では非同期の生成、訓練と推論のずれの扱い、sandbox の環境が、OPD の段では教師の数と信号の粒度が報告ごとに違う](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-open-models-and-open-problems/pipeline-2026h2.png)

[DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/DeepSeek_V41_Tech_Report.pdf) は最後の段で 40 を超える教師から語彙全体の分布で学び、[Kimi K3](https://arxiv.org/abs/2607.24653) は 3 つの分野と 3 段階の推論の量を掛け合わせた 9 個の専門家をまとめ、[Nemotron 3.5 Super](https://arxiv.org/abs/2610.38095) は RL の途中のチェックポイントから教師を分岐させ、前の章で見た教師と生徒の混合で負の信号に下限を付けた。[Nemotron 3 Ultra](https://arxiv.org/abs/2606.15007) も SFT と RL のあとに多教師の OPD を使っている。違う組み方もあり、[MiMo-V2.6](https://arxiv.org/abs/2610.11959) は複数の課題を 1 回の RL で混ぜてから OPD で足し、同じ Nemotron 3.5 の小さい Lightning には OPD の段が無い。

次の表は、前の 2 つの章で見た部品を、報告ごとに並べたものである。2026 年前半以前の行は、そのモデルの系列で目的関数や事後学習を書いた最新の開示か、いまの部品の出どころなので含めた。時期は arXiv の v1 かブログの公開日で、DeepSeek-V4 は v1 の日付が 2026 年 4 月と表示されて識別子の年月と一致せず、DeepSeek-V4.1-Flash は重みが公開された月である。「記載なし」はその資料に書かれていないことを指し、使っていないことを意味しない。RL の方法を書いていない報告（たとえば SFT と DPO で組んだと書く [LLM-jp-4.1](https://llm-jp.nii.ac.jp/blog/llm-jp-4-1/)）は表に入れていない。

| モデル | 資料の時期 | 目的関数 | 訓練と推論のずれ | 蒸留と OPD |
|---|---|---|---|---|
| [DeepSeek-R1](https://arxiv.org/abs/2501.12948) | 2025 年 1 月 | GRPO、KL の係数 0.001、クリップの幅 10 | 記載なし | 小さいモデルへの off-policy の蒸留 |
| [Qwen3](https://arxiv.org/abs/2505.09388) | 2025 年 5 月 | GRPO | 記載なし | 小さいモデルを off-policy から on-policy の蒸留で作る |
| [Magistral](https://arxiv.org/abs/2506.10910) | 2025 年 6 月 | GRPO、KL を除去、グループの token で平均、Clip-Higher | 記載なし | 記載なし |
| [MiniMax-M1](https://arxiv.org/abs/2506.13585)、[M2](https://arxiv.org/abs/2605.26494) | 2025 年 6 月、2026 年 5 月 | CISPO | 出力層の FP32（M1） | 記載なし |
| GSPO を使った Qwen3 の版 | 2025 年 7 月（[GSPO の論文](https://arxiv.org/abs/2507.18071)） | GSPO（どの版かは記載なし） | 記載なし | 記載なし |
| [GLM-4.5](https://arxiv.org/abs/2508.06471) | 2025 年 8 月 | GRPO、KL を除去 | 記載なし | 記載なし |
| [Ring-1T](https://arxiv.org/abs/2510.18855) | 2025 年 10 月 | GRPO、KL の係数 0 | IcePop | 記載なし |
| Qwen3-VL | 2025 年 11 月（[SAPO の論文](https://arxiv.org/abs/2511.20347)） | SAPO | 記載なし | 記載なし |
| [DeepSeek-V3.2](https://arxiv.org/abs/2512.02556) | 2025 年 12 月 | GRPO、標準偏差で割らない、KL の推定量の補正 | 回答単位の masking、Keep Routing、Keep Sampling Mask | 専門家のデータでの off-policy の蒸留 |
| [Olmo 3](https://arxiv.org/abs/2512.13961) | 2025 年 12 月 | GRPO、KL を除去、batch の token で平均 | TIS | 記載なし |
| [INTELLECT-3](https://arxiv.org/abs/2512.16144) | 2025 年 12 月 | 記載なし | IcePop の形の masking | 記載なし |
| [Nemotron 3](https://arxiv.org/abs/2512.20856) | 2025 年 12 月 | GRPO | masked importance sampling | 記載なし |
| [MiMo-V2-Flash](https://arxiv.org/abs/2601.02780) | 2026 年 1 月 | GRPO の advantage を OPD と併用 | R3、範囲外の token を捨てる | 多教師の OPD（MOPD） |
| [LongCat-Flash-Thinking-2601](https://arxiv.org/abs/2601.16725) | 2026 年 1 月 | GSPO（MoE と長い agent の trajectory での安定を理由に挙げる） | 記載なし | 記載なし |
| [Kimi K2.5](https://arxiv.org/abs/2602.02276) | 2026 年 2 月 | 記載の範囲では不明 | log の比による token の masking | 記載なし |
| [GLM-5](https://arxiv.org/abs/2602.15763) | 2026 年 2 月 | GRPO、KL を除去 | IcePop | 前の段を教師にする OPD |
| [Step 3.5 Flash](https://arxiv.org/abs/2602.10604) | 2026 年 2 月 | MIS-PO、価値の推定 | token と回答の 2 つの粒度の判定 | 記載なし |
| [Nemotron-Cascade 2](https://arxiv.org/abs/2603.19220) | 2026 年 3 月 | 段ごとの RL（Cascade RL） | 記載なし | 中間のモデルを教師にする多教師の OPD |
| [DeepSeek-V4](https://arxiv.org/abs/2606.19348) | 2026 年 4 月 | 専門家の RL に GRPO | ビット単位の一致を目指す決定的なカーネル | 10 以上の教師、語彙全体 |
| [Ling と Ring 2.6](https://arxiv.org/abs/2606.15079) | 2026 年 6 月 | 記載の範囲では不明 | KPop | 記載なし |
| [KAT-Coder-V2.5](https://arxiv.org/abs/2607.05471) | 2026 年 7 月 | PPO と critic | 会話の template を当て直さずに token の列のまま生成を呼ぶ | 多教師の OPD |
| [Kimi K3](https://arxiv.org/abs/2607.24653) | 2026 年 7 月 | 式は記載なし | 古いデータへの token ごとの正則化 | 9 個の専門家の OPD |
| [Granite 4.2](https://huggingface.co/blog/ibm-granite/granite-4-2) | 2026 年 8 月 | 非同期の GRPO | 記載なし | 記載なし |
| [DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/DeepSeek_V41_Tech_Report.pdf) | 2026 年 10 月 | GRPO | 区間ごとの経路をつなげる routing replay、古い token の masking | 40 を超える教師、語彙全体 |
| [MiMo-V2.6](https://arxiv.org/abs/2610.11959) | 2026 年 10 月 | GRPO と符号ごとの 4 つの境界 | 推論側の確率で直接割る | MOPD2（教師の回答や SFT のデータの途中までを prefix にして続きを生徒に書かせる） |
| [Step-5-Preview](https://platform.stepfun.ai/blog/inside-step-5-preview) | 2026 年 10 月 | 記載なし | ビット単位の一致、経路と候補の再生 | 記載なし |
| [Nemotron 3.5 Super](https://arxiv.org/abs/2610.38095) | 2026 年 10 月 | 非同期の GRPO | router replay、出力層の FP32 | 2 段の多教師の OPD |

長い agent の課題では、KAT-Coder-V2.5 が critic を使う PPO を選び、Step 3.5 Flash も文脈の長さで打ち切られた回答を価値の推定で補っている。回答が数百ターンに伸びると、1 つの回答に 1 つの報酬では信号が粗いので、critic で途中の状態を評価する設計と読める。critic を持つ PPO の系統を reasoning 向けに直した [VAPO](https://arxiv.org/abs/2504.05118) もあるが、7 つの技術を合わせた結果で、critic の有無だけを比べたものではない。問題ごとに回答の数を変える配分は、[目的関数の章](https://zenn.dev/tosshi/books/digging-into-machine-learning/viewer/rl-objectives-and-estimators#回答の数をどの問題に配るか)で扱った。

## 大規模に動かすと時間と費用と精度が問題になる

RL の 1 step は、回答の生成、採点、勾配の計算、重みの送り込みからなる。報告のある系では、時間と費用の大きな部分を回答の生成が占め、大きいモデルでは重みの送り込みにも数秒から数分かかる。生成を低い精度で速くしようとすると、生成側と学習側の精度の組み合わせが新しい問題になる。以下の報告は測る量と構成が違うので、数値を横に並べて比べることはできない。

### 時間と費用はどこに消えるか

| 項目 | 報告 |
|---|---|
| 生成の時間 | [verl の非同期の文書](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/one_step_off.md) は、DAPO の 32B の学習で回答の生成が時間の約 70% を占め、資源を増やしても生成の時間は縮まないと書いている。最も長い回答を待つ間に他の GPU が止まるためである |
| 費用の内訳 | [MiMo-V2.6](https://arxiv.org/abs/2610.11959) は、RL に Pro で 260 万ドル、Flash で 90 万ドルを使い、Pro の費用は生成 43.8%、学習 43.5%、採点 12.7% だった |
| 重みの送り込み | 1 兆パラメータの Kimi-K2 で、[Mooncake](https://raw.githubusercontent.com/kvcache-ai/Mooncake/main/README.md) を使う RDMA の送り方は 53 秒から 7.2 秒になった。[verl の差分の同期](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/delta_weight_sync.md) は Qwen3-235B-A22B の構成で 246〜266 秒を 11.4〜14.9 秒にした |
| 投機的デコード | [verl の MTP の文書](https://raw.githubusercontent.com/volcengine/verl/main/docs/advance/mtp.md) は、H20 で mimo-7B を SGLang で生成した例で、MTP（モデルに組み込んだ draft の層）を有効にすると生成の処理量が約 50% 下がったと書き、演算の性能が低い GPU では draft の検証の費用が節約を上回ると説明している |

MoE の router も RL の中で不安定になり、MiMo-V2.6 は router を学習させると最初の 20 step で expert の負荷の偏りが急に大きくなったので、RL の間は router を固定した。これは訓練と推論のずれの補正ではなく、重みの更新で経路が動くことを抑える操作である。agent の環境も学習の中身を変え、[Apertus 1.5](https://arxiv.org/abs/2610.15542) はツールのサーバーの処理能力が足りずにツールを使う問題を本番の RL から外し、ツール利用は SFT だけで学んだ。

### 生成を低い精度にすると何が起きるか

生成を FP8 や FP4 に下げて速くする研究がある。速さと精度への効果は方式と環境で変わり、生成側の誤差は訓練と推論の比 $\rho$（前の章）を 1 から離しうる。[HiFloat4 の論文](https://arxiv.org/abs/2607.26515) は、回答の生成と学習の両方を 4 bit で行う RL を調べ、劣化の主な原因は学習側の量子化ではなく、生成での活性の量子化だと報告した。活性の外れ値が値の範囲を広げるので、FP4 では多くの活性が 0 に潰れる。

![Qwen2.5-3B の GRPO の GSM8K の正答率を、学習側と生成側の精度の組み合わせごとに並べた。生成側の精度を上げると正答率は上がるが、生成を FP4 のまま学習側だけを BF16 に戻すと、HiF4 で 82.03% から 78.01% に、MXFP4 で 73.31% から 50.57% に下がる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/rl-open-models-and-open-problems/fp4-consistency.png)

Qwen2.5-3B の GRPO の GSM8K では、生成を FP4 のまま学習側だけを BF16 に戻すと、両方を FP4 にしたときより正答率が下がった。論文は、生成と学習の間のずれが大きくなるためだと説明し、生成の行列積にだけ疎な残差の補正を足す Rollout-ResQ で、BF16 との正答率の差を HiF4 で 4.9 ポイントから 1.1 ポイントに、MXFP4 で 13.6 ポイントから 5.3 ポイントに縮めた。対策も出ていて、[QUADS](https://arxiv.org/abs/2607.15810) は MoE の GRPO を NVFP4 で学習したときに 73.15%（BF16）から 51.37% に落ちた平均の pass@1 を、学習側の重みだけを量子化する QAT（量子化を意識した学習）と生成側の活性の補正で 72.86% に戻し、生成は FP8 より 16% 速くなった。[TRACE](https://arxiv.org/abs/2610.07767) は重み、活性、KV cache（attention が過去の token の情報を持っておく領域）を FP4 にして回答を生成しても、BF16 で生成したときと同程度の RL の性能を保ち、生成を BF16 の場合の最大 5.4 倍速くした。[KV4-RL](https://arxiv.org/abs/2610.14448) は KV cache を INT4 にして attention の sink と直近の位置だけを INT8 に残した。公開モデルでは、Kimi K3 が SFT の段から MXFP4 の QAT を続け、DeepSeek-V4 が事後学習の間に MoE の expert の重みを FP4 にする QAT を行い、Nemotron 3.5 は RL のあとで NVFP4 に量子化し、36 のベンチマークで BF16 に対する正確さの保持率の中央値が 99.62% だったと書いている。

量子化で落ちた reasoning の能力を取り戻すのにも OPD が使われている。[Train Where the Quantized Model Goes](https://arxiv.org/abs/2609.26708) は、量子化した生徒が自分の回答の上で全精度の教師から学び、検証器の報酬も組み合わせる方法で、実効 2.79 bit と 1.88 bit の 4 つのモデルの平均で MATH-500 での BF16 の性能の保持率を 35% から 70% に上げ、固定した回答で学ぶ蒸留（QAD）を同じ予算で続けるより改善が大きかったと報告した。[Right On Q](https://arxiv.org/abs/2610.29378) は、固定した回答で学ぶと量子化した生徒が実際に書く prefix と学習の prefix がずれ、回答が長いほど大きくなると書いている。前の章の off-policy の蒸留と OPD の違いが、量子化の回復でも現れている。

### 学習基盤の例: SkyRL

ここまでの部品を 1 つの学習基盤がどう実装しているかを、Berkeley Sky Computing Lab が Anyscale と開発する [SkyRL](https://github.com/NovaSky-AI/SkyRL) で見る。SkyRL は学習に Megatron と FSDP、回答の生成に vLLM を使い、[v0.4.0](https://blog.skyrl.ai/blog/skyrl-v0-4-0) には前の章の部品が設定として並んでいる。

| 部品 | v0.4.0 の実装と報告 |
|---|---|
| expert の経路の再生（R3） | 経路のデータを詰めた配列のまま vLLM から学習側に渡し、Nemotron 3 Super で R3 を使う学習の処理量を GPU あたり毎秒 111 token から 483 token にした |
| 候補の集合の再生 | DeepSeek-V3.2 の Keep Sampling Mask と同じ考え方で、vLLM が残した候補の集合の上で確率を正規化し直す。Nemotron 3 Super の Lila Sciences の課題で、R3 とこの機能の両方を使うと訓練と推論の log 確率の差は 0.1 未満にとどまって 160 step でも報酬が伸び、2 つとも使わない場合は 0.8 を超えて報酬が崩れた。2 つを分けた比較ではない |
| 重みの送り込み | 変わった部分だけを XOR の差分で送る方式（1 step で値が変わる重みは約 1〜3%）と、NIXL で学習側の全 rank から必要な分割だけを送る方式。後者は BF16 の Kimi K2 を H100 のノード 48 台の構成で 7.53 秒で送った |
| 低い精度 | Blockwise FP8 と MXFP8 での学習と生成で、報告の長い学習の実験では BF16 の収束に近いまま 1 step を最大 23% 短くした |

公開の当日から 501B-A23B の MoE である Beam に対応した [報告](https://blog.skyrl.ai/blog/day-0-beam) では、MXFP8 で生成の処理量が 44% 上がり、[100 万 token の文脈の報告](https://blog.skyrl.ai/blog/1m-context-glm-5-3-flash) は、GLM-5.3-Flash の疎な attention が密な計算に戻っていた問題などを直し、実際の文章で生じる expert の負荷の偏りにも対処して、1 本 100 万 token の学習を文脈の並列化 4 の構成で B200 のメモリに収めた。どちらも SkyRL のチームと共同の組織による報告である。

## agent の RL は環境と課題と報酬の信頼性で決まる

agent の RL の規模は、回答の数だけでは表せない。どれだけ多様な環境と課題で学ぶか、報酬が本当に課題の達成を測っているかを、回答の数とは別に調べる必要がある。

### 環境の規模と、データの何を増やすか

Kimi K3 は学習と評価の間に 1,505,678 個の image から累計 51,219,741 個の sandbox を作り、DeepSeek-V4.1-Flash は同時に動かす sandbox の需要が数百万に増え、KAT-Coder-V2.5 は 12 の言語で 100,000 を超える検証できる環境を作った。3 つの数は、累計、同時の需要、環境の種類と、数えているものが違う。学習基盤の細部も効き、KAT-Coder-V2.5 は推論のサーバーが会話の template を当て直すことで約 200 ターンの課題の約 40% の回答に token のずれが生じたと報告した。[Mercor と SkyRL の 397B の学習の手引き](https://www.mercor.com/blog/training-frontier-knowledge-work-agents-a-397b-rl-training-guide-with-skyrl/) も、RL の前に環境、harness、token の数え方を正すことを最初の段に置いている。同じ手引きは、完全な非同期の学習で同時に生成できる回答の数に、許す古さの step の数に 1 を足した数と minibatch の大きさと 1 問あたりの回答の数の積という上限があり（この設定では $(3+1) \times 16 \times 16 = 1024$ 本）、実際の同時の数は KV cache の容量で決まったと書いている。全変動距離の 2 値の近似で token を外す [DPPO](https://arxiv.org/abs/2602.04879) と GLM-5 の損失は、1 epoch の時点で誤差の範囲で同じ点だった。この手引きは、Mercor 自身の評価 APEX-Agents と同じ形の 1,928 個の課題で Qwen3.5-397B-A17B を学習し、学習に使っていない 480 個の評価の課題で Pass@1 が 16.11% から 27.29% に上がったと報告した。評価の作り手による報告である。

何を増やすべきかについては、データの側の scaling 則が出た。[How Data Scales in Agentic Reinforcement Learning](https://arxiv.org/abs/2610.23946) は、agent の RL のデータを課題、環境、trajectory の 3 つの軸に分け、軸ごとのべき乗則の指数を当てはめた。Qwen3 の 4B から 14B、GRPO と PPO、数学とコード、SWE、web、UI とツールの課題で、指数は環境、課題、trajectory の順に大きく（Qwen3-8B の GRPO の SWE で 0.39、0.24、0.10）、環境の優位は主に別のアプリやリポジトリのような世界の多様性から来ていた。論文は、この則から導いた配分が trajectory を増やす通常のやり方より最大 1.7 倍の計算効率になると報告している。RL の計算量の側では、[ScaleRL](https://arxiv.org/abs/2510.13786) が調べた設定で、 8B の dense のモデルで 1 回の RL を 100,000 GPU 時間まで伸ばし、50,000 GPU 時間までの結果から当てはめたシグモイドの曲線がその先まで当たることを示した。どちらも 1 本の論文の結果である。

### 報酬の抜け穴と、ラベル無しの RL

RL は報酬を最大にするので、報酬の作り方の穴を突く。[hacktrace](https://arxiv.org/abs/2610.03055) はコードの生成の途中の状態から不正を検出し、合格した解に占める不正の割合を 82〜91% から 1〜5% に下げた。一方で [Reward Hacking as Equilibrium under Finite Evaluation](https://arxiv.org/abs/2603.28063) は、評価が有限で最適化が効くといった条件のもとで、最適化された agent は評価に含まれない品質への努力を体系的に減らすと論じている。前者は特定の不正を減らせること、後者は評価の外の品質が残りうることを示していて、両立しうる。

正解のラベルを使わない RL では、[TTRL](https://arxiv.org/abs/2504.16084) が多数決の答えを疑似的な正解にして Qwen-2.5-Math-7B の AIME 2024 の pass@1 を約 211% 上げたと報告したあと、誤った答えに自信を持って収束するという報告が続いた（[CoVerRL](https://arxiv.org/abs/2603.17775) の consensus trap、[医療の QA の論文](https://arxiv.org/abs/2609.16660)）。統計の言葉では、1 つの問題の中では固定したモデルから回答が独立に生成されるので、答えの分布の最頻値が 1 つに決まるなら、回答を増やすほど最多票はその最頻値にほとんど確実に一致する。コンドルセの陪審定理とその多択への拡張（[List と Goodin](https://doi.org/10.1111/1467-9760.00128)）が最多票の正しさを保証するのは正しい答えが最も選ばれやすいときだけで、最頻値が誤る問題では、回答を増やすほど誤った答えが確実に疑似的な正解になる。[Collective Dynamics of Self-Reinforcement](https://arxiv.org/abs/2610.37553) は、この崩壊が問題の数が多いときにだけ起き、初めのモデルの確信度が高ければ起きないことを、パラメータを共有する多数の問題の模型の不安定性として説明した。模型と小さい transformer での結果である。

## RL は何を得たと言えるか

RL で評価の値が上がったとき、それを新しい能力の獲得と呼べるかは決着していない。判定には、pass@k の推定の仕方、問題の間と試行の中の 2 つの分散、自己改善を比べる対照、探索に使った計算を分けて調べる必要がある。

### pass@1 と pass@k は別の量で、誤差には 2 つの成分がある

問題 $q$ の正解率を $p_q$ とすると、pass@1 は $\mathbb{E}_q[p_q]$、pass@k は $\mathbb{E}_q[1 - (1-p_q)^k]$ で、$k$ が大きいと正解率が 0 より大きい問題の割合に近づく。RL が平均を上げながら一部の問題の小さい正解率を下げると、有限の $k$ で 2 つが逆に動く場合がある。[RLVR（正誤を判定できる報酬での RL）が base model の能力を超えるかを調べた論文](https://arxiv.org/abs/2504.13837) はこの逆転を報告し、[There Is No Free Lunch in RLVR](https://arxiv.org/abs/2610.39295) は RLVR を解き方の分布の指数的な傾きとして模型化して、その仮定のもとでは逆転を避けられないことを定理として示した。逆の向きでは、[ProRL](https://arxiv.org/abs/2505.24864) が、元のモデルの評価値が 0.00 だった課題で長い RL のあとに 7.91 を出したと報告している。$n$ 回の試行で正解が 0 回でも、正解率の 95% の信頼上限はおよそ $3/n$ で、0 とは言えない。

1 つの問題を同じ方策と生成の設定から独立に $n$ 回解き、$c$ 回正解したとすると、$1 \le k \le n$ では [Chen らの論文](https://arxiv.org/abs/2107.03374) の推定量 $1 - \binom{n-c}{k} / \binom{n}{k}$ は、すべての $k$ 回の組み合わせについての U 統計量で不偏であり、$c$ が 2 項分布の完備十分統計量なので、レーマン・シェフェの定理から UMVUE（一様最小分散不偏推定量）になる。素朴な $1 - (1 - c/n)^k$ はジェンセンの不等式から pass@k を小さく見積もる。次に pass@1 の推定の誤差を見る。母集団から独立に選んだ $Q$ 問を各 $n$ 回解き、問題ごとの正答率 $c_q/n$ を平均した $\bar{Y}$ の分散は、次の 2 つの成分に分かれ、$n$ を増やして減るのは第 2 項だけである。

$$
\operatorname{Var}(\bar{Y}) = \frac{1}{Q}\left( \operatorname{Var}_q(p_q) + \frac{\mathbb{E}_q[p_q (1 - p_q)]}{n} \right)
$$

筆者の計算では、正解率 0.9 と 0.1 の問題が半数ずつの母集団から 30 問を選ぶ模型で、$n = 16$ の標準誤差は第 2 項だけなら 1.4 ポイント、両方の項で 7.4 ポイントで、$n = 64$ でも 7.3 ポイントまでしか下がらない。2 つのモデルを同じ問題で比べると、問題ごとの正解率の相関が高いほど第 1 項が打ち消される。[RLVR is a Kernel, Not a Function](https://arxiv.org/abs/2609.22547) はこの対応のある比較に、すべての $k$ で同時に成り立つ同時信頼帯を付け、公開されている 5 組の RLVR のモデルの初めの評価では逆転は統計的に確かめられず、新しい問題を 32k token で評価し直すと 11 回から 61 回の試行の間で逆転が見つかったと報告した。逆転を見つけられないことは、逆転が無いことを意味しないとも書いている。

### 対照のある比較: 自己改善、どの問題が直るか、忘れにくさ

[Phantom Gains](https://arxiv.org/abs/2608.20290) は、Qwen3-8B の 3 回の自己学習を、同じ手順を通した凍結したモデルと比べて測り方の誤りを 7 つ挙げ、未学習のモデルの評価をまとめた基準に対する問題ごとの正確検定と偽発見率（FDR）の制御に替えると、外部の教師からの蒸留はめったに解けない問題を改善したが、3 つの形の自己学習は改善しなかったと報告した。[What Will Post-Training Fix?](https://arxiv.org/abs/2610.04978) は、2 つの base model からの 18 回の事後学習で、同じ base model からの独立な実行は直る問題で強く一致し（測定の雑音のもとで到達できる一致の上限が約 0.9）、2 つの base model の間の一致は相関 0.25 にとどまったと報告した。[RL's Razor](https://arxiv.org/abs/2509.04259) は、RL が SFT より以前の能力を忘れにくいことを、新しい課題で測った base model からの KL の小ささで説明し、LLM の実験での当てはめの決定係数は 0.71 だった。

### 探索を学習する方策から切り離す

RL は方策が生成した回答しか強められないので、新しい解き方の発見は探索に依存する。[Decoupling Exploration from Optimization in RLVR](https://arxiv.org/abs/2610.10536) は、新しさの報酬（回答の隠れ状態を入力にした固定のランダムなネットワークの出力を、学習する小さなネットワークで予測したときの誤差、RND）を DAPO の報酬に直接足すと、数学の正答率も一般の能力の評価もかえって下がったと報告した。そこで、新しさの報酬で学習した探索用の方策の正しい回答を別の生徒に蒸留し、生徒は新しさの報酬なしで RL する手順（ExpDis）を何回か繰り返す。RL の更新の回数を 300 回にそろえた比較で、Qwen3-1.7B の 5 つの主なベンチマークの平均の pass@1 は、DAPO の 45.63、4 倍の更新の DAPO の 47.28 に対して 51.81 で、pass@64 は DAPO の 74.64 に対して 83.04 だった。Qwen3-4B では pass@1 が 62.58 に対して 66.50 で、生徒の SFT（1 回あたり最大 500 本の回答で 2 epoch）はそろえた更新の回数の外にある。

## まとめ

この章の結果を、根拠の形で分ける。分け方は筆者のもので、2026 年 10 月 31 日の時点の判断である。

| 根拠の形 | 結果 |
|---|---|
| 複数の論文、設定はそれぞれ違う | 生成側の量子化の誤差を抑える設計が、低い精度の RL の性能の回復に効いた（[QUADS](https://arxiv.org/abs/2607.15810)、[HiFloat4](https://arxiv.org/abs/2607.26515)、[TRACE](https://arxiv.org/abs/2610.07767)、[KV4-RL](https://arxiv.org/abs/2610.14448)） |
| 1 本の論文（関連の指摘が別に 1 本） | 量子化の回復に、生徒自身の回答で学ぶ OPD が、固定した回答の蒸留より効いた（Train Where the Quantized Model Goes。Right On Q は固定した回答で prefix がずれる問題を指摘） |
| 複数の論文 | ラベル無しの RL は、多数決への収束で崩れることがある |
| 複数の資料、測り方は違う | 回答の生成が RL の時間や費用の大きな部分を占める |
| 1 本の論文 | 生成を FP4 のまま学習側だけを BF16 に戻すと、両方を FP4 にするより悪い（HiFloat4 の Qwen2.5-3B と GSM8K） |
| 1 本の論文 | 8B の dense のモデルの RL で、計算量に対する性能をシグモイドの曲線で外挿できた（ScaleRL） |
| 1 本の論文 | agent の RL のデータは、環境、課題、trajectory の順に scaling の指数が大きい（14B まで） |
| 1 本の論文 | 探索用の方策を分けて蒸留すると、同じ更新の回数の DAPO より pass@1 と pass@k の両方が上がる（4B まで、数学） |
| 主張が対立 | RL は新しい能力を作るか（No Free Lunch、What Will Post-Training Fix? と、ProRL、同時信頼帯の論文） |

| 今後検証すること | 理由 |
|---|---|
| 量子化の誤差と RL の安定性の関係 | 生成と学習の精度の組み合わせを系統的に測った研究がまだ少ない |
| agent の RL のデータの scaling 則の追試 | 2610.23946 は表 2 で指数の順序が 18 の設定で成り立つと書くが、表に並ぶのは 10 の設定で、回答の生成に使ったと書く vLLM 0.6.3 は 2025 年 4 月に公開された Qwen3 より前の版である |
| ExpDis の、SFT を含めた計算量での比較と大きいモデルでの結果 | 比較は RL の更新の回数でそろえていて、4B までのモデルと数学に限られる |
