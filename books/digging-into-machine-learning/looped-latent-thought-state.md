---
title: "思考の状態はどこに置かれるか: thinking、Looped Transformer、持ち越す潜在状態"
free: false
---

## はじめに

reasoning model の thinking と Looped Transformer は、どちらも答える前に計算を足す仕組みとして並べて語られることが多い。両者の違いは、ふつう思考を言葉にするかどうかとして説明される。しかしこの章で比べたいのは、言葉にするかどうかではない。**考えている途中の状態をどこに置き、それがどれだけ長く残るか**である。この見方を取ると、thinking と loop の違いに加えて、考え抜いた結果を次の request に持ち越すという 3 つ目の方向が同じ表に並ぶ。

この章では、モデルへの 1 回の推論の呼び出しを request と呼ぶ。1 つのエージェントの対話は、ふつう複数の request からなる。章の前半で thinking と Looped Transformer の状態の持ち方を比べ、表現力と実装の制約を整理する。後半では記憶の研究を手がかりに、考えた潜在状態を次の request に持ち越して再び計算するという方向を検討する。この 3 つ目の方向は完成した方式ではなく、関連研究から形を考える設計上の問いとして扱う。

読者には Transformer の層、KV cache、chain-of-thought (CoT) の知識を想定する。扱う論文の中心は 2024 年末から 2026 年 10 月 6 日までに arXiv に出たもので、背景として一部それ以前の論文も参照する。数値はすべて各論文の abstract か本文の報告値であり、筆者はどれも再現していない。

| 方向 | 途中の状態の置き場所 | 作業状態の大きさ | 残る期間 |
|---|---|---|---|
| thinking | 生成した token と、その KV cache | step ごとに伸びる | その request の間 |
| Looped Transformer | 各位置の hidden state | loop を何回回しても変わらない | その token の forward の間 |
| 持ち越す潜在状態 | 保存した hidden state や KV | 設計による | request をまたぐ |

この表は、途中の計算で直接書き換える作業状態を比べたもので、推論時のメモリの総量を比べたものではない。Looped Transformer の行で変わらないのは、各位置で更新し続ける hidden state の大きさである。後の token はその結果を KV cache を通して読むが、その KV cache は素朴な実装では pass ごとに増える。この点は後の節で扱う。また 3 つの行は互いに排他的な方式ではない。thinking と loop は計算を足す方向の違いであり、持ち越しは状態の寿命の違いなので、同じモデルの中で組み合わせられる。

![thinking は状態を token として伸ばし、loop は同じ大きさの hidden state を磨き、持ち越す潜在状態は request をまたいで残る](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/looped-latent-thought-state/state-placement.png)

図の右の列は既存の方式ではなく、この章が後半で検討する方向を表している。

## 計算を足す 2 つの方向

通常の Transformer は、1 token を出すのに L 層を 1 回ずつ通る。1 token あたりの計算の深さは L で固定される。難しい問題に計算を足す方法は大きく 2 つある。step の数を増やす方向と、同じ token のまま層を通る回数を増やす方向である。前者が thinking にあたり、後者が Looped Transformer にあたる。[Huginn の論文](https://arxiv.org/abs/2502.05171) も、自分たちの方式を、token を多く生成して計算を増やす主流の reasoning model と対比して位置づけている。

## thinking: step の列による状態の拡張

thinking では、モデルは途中の考えを token として生成し、その token は文脈に残る。後の step は、その token を attention で読み直せる。だから途中の状態は step を重ねるほど伸び、人間がそれを読むこともできる。

その代わりに、モデルは token を出すたびに確率分布から 1 つの token を選ぶ。[Reasoning by Superposition](https://arxiv.org/abs/2505.12514) は、有向グラフの到達可能性問題でこの選択の意味を理論的に調べた。思考を連続ベクトルのまま渡せば、各 step のベクトルが複数の探索の候補を重ね合わせて持てるので、2 層の Transformer がグラフの直径 $D$ 回の step で解ける。同論文が対比する離散 token の CoT では、重ね合わせた候補から 1 本の経路を選ぶので、探索が逐次的になる。定数の深さの Transformer に離散 token の CoT を使わせる場合、知られている最良の構成は頂点数 $n$ に対して $O(n^2)$ step を要する。ただしこれは既知の構成どうしの比較であり、離散 CoT が必ずそれだけの step を要するという下界は証明されていない。

step を増やす方向には、言語の token を生成する CoT のほかに、連続ベクトルの状態を次の入力位置へ渡す latent CoT もある。その代表が [Coconut](https://arxiv.org/abs/2412.06769) である。Coconut は最終層の hidden state を単語に戻さず、次の入力 embedding としてそのままモデルに戻す。状態を次の位置に積んでいく点では thinking と同じで、同じ位置で深さを足す loop とは違う。探索を要する論理推論の ProsQA では、正解率が CoT の 77.5% に対して Coconut が 97.0% だった。GSM8k では、Coconut が生成する token は CoT の 25 個に対して 8.2 個と少なかったが、正解率は CoT の 42.9% に対して 34.1% にとどまった。

## Looped Transformer: 同じ層の反復

Looped Transformer は、同じ重みのブロックを 1 token の forward の中で何度も通す。要点は、パラメータの数を増やさずに計算の深さを足せることである。繰り返すブロックが k 層なら、それを r 回通すモデルは、パラメータを k 層分しか持たないのに、1 token に kr 層分の計算を通す。この章では、繰り返すブロックを 1 回通ることを pass と呼ぶ。

[Huginn](https://arxiv.org/abs/2502.05171) はモデルを入口の prelude、繰り返す core、出口の coda の 3 つに分け、core $R$ を次の式で $r$ 回回す。

$$
s_i = R(e,\, s_{i-1}), \qquad i = 1, \dots, r
$$

$s_i$ は $i$ 回目の pass の後の hidden state で、$s_0$ は回し始めの状態である。$e$ は prelude が作った入力の表現で、pass のたびに core に入れ直す。[Yang ら](https://arxiv.org/abs/2311.12424) が loop の学習を安定させるために入れた input injection と同じ考え方である。Huginn は 3.5B パラメータのモデルを 800B token で学習した。推論時に $r$ を増やすと、50B パラメータ相当の計算量まで reasoning のベンチマークの成績が伸びたと報告している。

[Ouro](https://arxiv.org/abs/2510.25741) はこの方式を大きな事前学習の規模に広げた。7.7T token で学習した 1.4B と 2.6B の loop モデルが、幅広いベンチマークで最大 12B の SOTA のモデルに匹敵すると報告している。各 pass の後には、そこで loop を抜けるかを決める exit gate を置き、エントロピーで正則化した目的関数で、token ごとの深さの配分を学習させる。著者らは、この優位が知識を蓄える容量から来るのではなく、知識を組み合わせて使う能力から来ることを対照実験で示した。

![Looped Transformer は 1 token の中で同じ core を r 回通し、素朴には pass ごとに KV cache が増える](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/looped-latent-thought-state/looped-block.png)

図の上段は Huginn の 3 分割と Ouro の exit gate を 1 枚にまとめたもので、1 つのモデルの構成ではない。[Saunshi ら](https://arxiv.org/abs/2502.17416) は、加算や p-hop induction のような合成問題で、k 層を r 回回したモデルが kr 層のモデルにほぼ並び、k 層のままのモデルを大きく上回ることを示した。同じ論文は推論と暗記の間に二分があることも報告し、その両方に効く loop を使った正則化を提案している。

## loop と CoT の関係

Saunshi らは、loop モデルが内部で潜在的な思考を作り、$T$ 回の loop で $T$ step の CoT を再現できることも証明した。これは、層を定数個足し、埋め込みの次元を少し広げた loop モデルが CoT を構成的に模倣できるという片方向の結果である。同じ資源の任意の loop モデルが CoT の代わりになるという意味ではない。

そこで、loop が途中の結果をどれだけ保持できるかに目を向ける。[Zhang](https://arxiv.org/abs/2605.30757) は 2026 年 5 月の論文で、CoT と loop の違いを、書き換えられる状態をどれだけ持てるかで整理した。CoT は途中の状態を token として文脈に積むので、step を重ねるほど作業領域が広がる。loop は状態を hidden state に持つので、回す回数を増やしても作業領域は広がらない。小さな再帰状態に圧縮した loop は、何回回しても小さな作業領域で考える計算機のままである。標準的な複雑性の仮定の下では、そうした loop は logspace 還元の意味で P 完全な問題を判定できないが、多項式長の CoT は判定できる。

この分離は圧縮した loop に限った結果である。Zhang は、入力のすべての位置に状態を持つ loop を、作業領域の豊かな、scratchpad に近い側に置いている。Huginn や Ouro は各位置に状態を持つので、筆者はこちら側に入ると考える。筆者の読みでは、その作業領域の大きさは入力の位置の数で決まり、loop を回す回数では増えない。したがって loop を選ぶときは、必要な中間結果を保持できる作業領域があるかが 1 つの目安になる。ただし、容量が足りることだけで問題を解けるとは、この理論からは言えない。小さな再帰状態で容量が足りない場合、回す回数を増やしても不足は補えないので、thinking のように状態が伸びる仕組みか、より大きな潜在の作業領域を持つ設計を検討することになる。

## loop の実装上の課題

### 推論時: KV cache の共有

loop を回すと、1 token の計算が増えるだけでは済まない。Ouro のような loop モデルは pass ごとに KV cache を持つので、推論時のメモリが loop の回数に比例して増える。pass の間で KV を共有する考え方には先行例があり、[Mixture-of-Recursions](https://arxiv.org/abs/2507.10524) は 2025 年 7 月に、token ごとに再帰の深さを選ぶ仕組みとともに、最初の再帰の KV を使い回す変種を提案した。[MELT](https://arxiv.org/abs/2605.07721) は層ごとに 1 つの KV cache を全 pass で共有し、学習したゲートで更新する。MELT は Ouro の重みから軽い追加学習で作られ、同程度のサイズの通常の LLM と同程度のメモリで、それらの LLM を上回った。

2026 年 10 月 1 日に公開された [Looped Prediction Transformer (LPT)](https://arxiv.org/abs/2610.02383) は、MELT が学習済みの Ouro に後から共有を組み込んだのと違い、共有する前提でモデルを事前学習した。最初の pass だけが cache を書き、後の pass はその cache を読む。この点は Mixture-of-Recursions の変種と共通で、LPT では後の pass が自分用の短い窓も持つ。150M から 1B のモデルで、5 回の loop を回す hybrid 版は、同じサイズの通常の Transformer より FineWeb-Edu の validation perplexity を 1.12 から 1.82 低くし、文脈のメモリを 76 から 79% 少なくした。著者らは、共有によって品質が下がらずにむしろ上がった点を、予想外の結果として報告している。LPT のメモリは一定ではなく、後の pass の短い窓の分だけ増える。

### 学習時: 潜在の軌跡の評価

[RLTT](https://arxiv.org/abs/2602.10520) によれば、loop モデルを強化学習でさらに伸ばす試みは、それまでうまくいっていなかった。GRPO は、生成した回答への評価を使ってモデルを更新する強化学習の手法である。RLTT の著者らはその原因を、GRPO が最後の潜在状態にしか報酬を割り当てず、loop の内部の計算と合わない点に求めた。RLTT は報酬を潜在の軌跡全体に配る。著者らが報告した GRPO に対する MATH-500、AIME24/26、BeyondAIME の平均正解率の改善は、abstract の表記で、Ouro-1.4B の Thinking 版が +5.8%、2.6B の Thinking 版が +10.9% である。

## 記憶という別の軸

ここまでの thinking と loop は、どちらも計算を足す方向だった。記憶はそれとは別の軸にある。ここでは記憶を、いつ書き込むかと、何を持つかで分ける。まず事前学習で書いた知識を読む Engram を見て、次に推論中に文脈を覚える Titans と ATLAS を見る。この区別によって、知識や文脈を覚えることと、ある問題について考えた結果を残すことの違いがはっきりする。

北京大学と DeepSeek による [Engram](https://arxiv.org/abs/2601.07372) は、Transformer には知識を引く仕組みが無いので、計算で検索をまねている、という問題意識から出発した。Engram は各位置の直前の数 token の並び、つまり n-gram をハッシュして表を引き、$O(1)$ で embedding を取り出す。取り出した値には現在の hidden state から作るゲートで重みを付け、残差ストリームに足し込む。MoE は、複数の専門家のネットワークから一部を選んで計算する方式である。MoE の計算と Engram の記憶にパラメータをどう配るかには U 字型の最適点があった。活性化するパラメータを 3.8B にそろえ、262B token で事前学習した比較で、27B に広げた Engram は、パラメータと FLOPs をそろえた MoE を上回った。本文の Table 1 では、知識を問う MMLU が 3.0 上がっただけでなく、推論を問う BBH が 5.0、ARC-Challenge が 3.7 上がった。abstract では MMLU の改善を 3.4 としている。著者らは、Engram が序盤の層を静的な知識の組み立てから解放し、実質的にネットワークを深くしたと分析している。

Engram が持つのは、token の並びが何を指すかという知識である。この知識は事前学習で書かれ、推論中は読むだけで、考えた結果ではない。同じ論文は本文 3.1 節で、記憶にパラメータを寄せすぎると文脈に依存する推論が損なわれることを示し、記憶は計算の代わりにならないと書いている。

推論中に書き換わる記憶もある。Google の [Titans](https://arxiv.org/abs/2501.00663) は、attention を範囲は狭いが正確な短期記憶とみなし、推論中に過去の文脈を学習して覚える長期記憶のモジュールを足した。needle-in-haystack の課題では、2M token を超える文脈でも比較手法より高い正解率を保てたと報告している。後継の [ATLAS](https://arxiv.org/abs/2505.23735) は、BABILong の 10M token の文脈で、Titans に対して正解率で +80% の改善を報告している。

loop と記憶を 1 つのモデルに入れた研究もある。[Adaptive Loops and Memory in Transformers](https://arxiv.org/abs/2603.08391) は、回す回数を層ごとに学習する loop と、学習で書き込む gated memory bank を併せ持つモデルを調べた。パラメータと FLOPs をそろえて構成を変えた比較では、loop は主に数学の推論に効き、memory bank は常識問題の成績を取り戻した。両方を入れたモデルは、FLOPs をそろえた 3 倍の層のモデルを数学で上回った。モデルの内部では、序盤の層は loop も記憶もあまり使わず、後半の層は両方を多く使うという層ごとの特化が見られた。

![記憶は、いつ書くかと何を持つかで分かれ、考えた結果を潜在状態のまま request の間に持つ欄が空いている](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/looped-latent-thought-state/memory-map.png)

図の FlashMem は、回答中の一時的な推論状態から記憶を作るので、書き込む時点に合わせて答えている間の欄に置いた。作った記憶を使える期間をその request の中に限るという意味ではない。FlashMem は次の節で扱う。

## 考えた結果の持ち越し

冒頭の表の 3 行目に戻る。ある問題について loop を回して考え抜いた後の状態を、一時的な hidden state として捨てずに保存し、次の request をその状態から始められないか、という発想である。ここで保存するのは、何を言ったかではなく、考えた結果としてモデルがどういう状態になったかである。

この方向に近い研究を、token の間での状態の受け渡し、対話の記憶化、request をまたぐ保存と再利用の 3 つの観点から見る。3 つは排他的な段階ではない。最後に注目するのは、ある問題で得た潜在状態を保存し、後の request で再び loop の計算の出発点に使うことである。

1 つ目は、token の間での受け渡しである。[State Stream Transformer の V2](https://arxiv.org/abs/2605.00206) の著者らは、通常の Transformer が位置ごとに残差ストリームの中身を捨て、KV cache 以外の潜在的な文脈を次の位置で作り直している点を問題とした。SST V2 は、各層の FFN が作る潜在状態を次の位置へ流し込む。[Latent Recurrent Transformer (LRT)](https://arxiv.org/abs/2605.26797) は、前の token の上位層の hidden state を、次の token の記憶として渡す。BPB は 1 バイトのデータを予測するのに要するビット数で、小さいほど良い。1.3B と 2.1B のモデルで、LRT は実効的な計算量をそろえた比較で通常の Transformer より BPB を改善した。さらに LRT は 1 token に 1 回の forward のまま、3 回 loop する Transformer と BPB で並び、遅延の増加は通常の Transformer に対して 9% だった。

2 つ目は、エージェントの対話の記憶化である。[FlashMem](https://arxiv.org/abs/2601.05505) は、最後の hidden state がそれまでのやり取りを十分に表すという性質を使い、推論中の状態から直接記憶を作る。凍結した KV cache に attention して記憶を合成し、attention のエントロピーが高いとき、つまりモデルが迷っているときだけ統合を走らせる。重い手法と同等の性能で、推論の遅延を 5 分の 1 にした。

3 つ目は、request をまたぐ保存と再利用である。潜在の記憶を request をまたいで更新し続ける点では、[MemoryLLM](https://arxiv.org/abs/2402.04624) とその拡張の [M+](https://arxiv.org/abs/2502.00592) が近い。更新の頻度が違う記憶を重ねて継続学習を目指す [Nested Learning の Hope](https://arxiv.org/abs/2512.24695) も、記憶を更新し続けるという点で関係がある。ただしこれらが持つのは取り込んだ知識や文脈であり、考えた結論を保存してそこから再び loop を回すものではない。[Cartridges](https://arxiv.org/abs/2506.06266) は、文書の集まりごとに小さな KV cache を前もって学習しておき、推論時にそれを読み込む。合成した会話を使う self-study という方法で学習すると、長文脈のベンチマークで、文書の全文を文脈に入れる方式と同等の性能を保ちながら、メモリを 38.6 分の 1 にし、スループットを 26.4 倍にした。ただし Cartridges が持つのは文書の内容であり、その学習には全文を prefill するよりはるかに大きな計算がかかる。[sleep-time compute](https://arxiv.org/abs/2504.13171) は、質問が来る前に文脈について考えておく。著者らが作った Stateful GSM-Symbolic と Stateful AIME で、同じ正解率に達するのに要する質問時の計算を約 5 分の 1 に減らした。こちらは考えた結果を持ち越すが、持ち越すのは潜在状態ではなく前もって計算した中間生成物であり、効き目は次の質問を文脈から予測しやすいほど大きかった。

ある request の loop の後の潜在状態を保存し、別の request の loop の出発点として使う組み合わせは、筆者の調べた範囲では見当たらない。ここでいう出発点とは、保存した状態を新しい入力の $s_0$ に写すことで、保存した KV を外部の記憶として attention で読むこととは区別する。各位置に状態を持つ loop では、保存したときと使うときで入力の長さも中身も変わるので、保存した状態をどの位置に対応付けるかから決める必要がある。

![次の request へ考えた状態を渡す循環の各段には近い研究があるが、保存した潜在状態から再び loop を回す段が空いている](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/digging-into-machine-learning/zenn/books/digging-into-machine-learning/looped-latent-thought-state/thought-state-cycle.png)

この図は、持ち越しの循環を読み込み、loop、統合、保存の 4 段に分け、読み込みから統合までの各段に近い研究を置いたものである。上の 3 つの観点とは切り方が違う。保存の段の点線の枠は、保存そのものではなく、保存した潜在状態を次の request の loop の出発点に使う組み合わせが見当たらないことを表す。sleep-time compute を図に入れていないのは、持ち越すのが潜在状態ではないからである。

## 持ち越しの前の設計判断

潜在状態を request をまたいで持ち越すなら、設計の段階でいくつかのことを決める必要がある。以下は筆者の整理であり、どれも実験で確かめたものではない。

### モデルを更新したときの扱い

hidden state の読み方はモデルの重みに依存するので、重みを更新した後も保存した状態が同じ意味を持つ保証はない。保存した状態をモデルの版と紐付け、更新の後にも使えるかを検証するか、作り直すか、捨てるかを決める必要がある。

### 誤った状態の見つけ方

潜在状態は人が直接読めない。thinking の token は人が読めるが、Ouro の著者らは loop モデルの推論の跡が明示的な CoT より最終出力とよく一致すると報告している。読めることと、書かれた跡が実際に答えを導いた過程を表していることは同じではない。保存した状態が誤った結論を含んでいれば、次の request の思考はその誤りから出発するので、状態が後の回答に与える影響を確かめる手段が要る。

### 持ち越しが得になる条件

sleep-time compute の結果は、前もって考えることの効き目が、次の質問の予測しやすさとよく相関することを示している。Engram の結果も、記憶を増やせば計算が要らなくなるわけではないことを示している。持ち越した状態は次の思考の出発点にはなっても、思考そのものの代わりにはならないと考えるのが自然であり、持ち越さない場合と比べて正解率と計算量の両方で得になるかを確かめる必要がある。

## まとめ

thinking は途中の状態を step の列として伸ばし、Looped Transformer は同じ大きさの hidden state を回して計算を深くし、記憶の研究は知識や文脈をいつ書き込み、どこまで再利用するかを広げている。2026 年 10 月の時点で筆者の調べた範囲では、ある問題で得た潜在状態を request をまたいで保存し、その状態から再び loop を回す仕組みは確認できず、これはこれらの研究を踏まえて考えるべき設計上の問いとして残っている。

## 参考: この章で扱った研究

本文で扱った研究を探し直すための一覧である。DiscoLoop だけは本文で扱っていない関連研究で、loop の各 pass に、連続の hidden state に加えて離散の token embedding の経路を持たせる。

| 研究 | 公開 | この章での論点 |
|---|---|---|
| [MemoryLLM](https://arxiv.org/abs/2402.04624) | 2024 年 2 月 | request をまたいで更新する潜在の記憶 |
| [Coconut](https://arxiv.org/abs/2412.06769) | 2024 年 12 月 | hidden state を次の入力に戻す latent CoT |
| [Titans](https://arxiv.org/abs/2501.00663) | 2024 年 12 月 | 推論中に文脈を学習して覚える記憶 |
| [Huginn](https://arxiv.org/abs/2502.05171) | 2025 年 2 月 | 推論時に loop の回数を選べる |
| [Saunshi ら](https://arxiv.org/abs/2502.17416) | 2025 年 2 月 | loop による CoT の構成的な模倣 |
| [Reasoning by Superposition](https://arxiv.org/abs/2505.12514) | 2025 年 5 月 | 連続の思考が探索の候補を重ね合わせて持つ |
| [ATLAS](https://arxiv.org/abs/2505.23735) | 2025 年 5 月 | Titans の長期記憶の拡張 |
| [Cartridges](https://arxiv.org/abs/2506.06266) | 2025 年 6 月 | 文書ごとに学習した KV を読み込む |
| [Mixture-of-Recursions](https://arxiv.org/abs/2507.10524) | 2025 年 7 月 | token ごとの再帰の深さと KV の共有 |
| [Ouro](https://arxiv.org/abs/2510.25741) | 2025 年 10 月 | 7.7T token の事前学習と学習する exit gate |
| [Nested Learning と Hope](https://arxiv.org/abs/2512.24695) | 2025 年 12 月 | 更新の頻度が違う記憶を重ねる |
| [FlashMem](https://arxiv.org/abs/2601.05505) | 2026 年 1 月 | 推論中の状態から記憶を作る |
| [Engram](https://arxiv.org/abs/2601.07372) | 2026 年 1 月 | n-gram で引く静的な記憶 |
| [RLTT](https://arxiv.org/abs/2602.10520) | 2026 年 2 月 | 潜在の軌跡全体に報酬を配る |
| [Adaptive Loops and Memory](https://arxiv.org/abs/2603.08391) | 2026 年 3 月 | loop と memory bank の併用 |
| [SST V2](https://arxiv.org/abs/2605.00206) | 2026 年 4 月 | 潜在状態を位置の間で流す |
| [MELT](https://arxiv.org/abs/2605.07721) | 2026 年 5 月 | pass の間で KV を共有し一定のメモリにする |
| [LRT](https://arxiv.org/abs/2605.26797) | 2026 年 5 月 | 前の token の hidden state を記憶として渡す |
| [Zhang](https://arxiv.org/abs/2605.30757) | 2026 年 5 月 | 圧縮した loop と CoT の表現力の分離 |
| [DiscoLoop](https://arxiv.org/abs/2607.00341) | 2026 年 7 月 | loop に離散の token embedding の経路を足す |
| [LPT](https://arxiv.org/abs/2610.02383) | 2026 年 10 月 | 最初の pass が書く KV を後の pass が読む |
