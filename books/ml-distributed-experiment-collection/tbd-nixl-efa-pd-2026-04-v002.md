---
title: "NIXL over EFA による真の P/D 分離推論 Part 5.2 結果考察編"
emoji: "📈"
type: "tech"
topics: ["NIXL", "vLLM", "EFA", "ベンチマーク", "AWS"]
published: false
---

## はじめに

本記事は連載「NIXL over EFA による真の P/D 分離推論」の Part 5.2 結果考察編にあたる。Part 5.1 で測定プロトコルと実行フロー、Part 5.2 で等価性の統計的裏付けと物理経路の自己検証、という役割分担で、この記事は後者を担当する。

EFA と TCP を比較すれば差が出るはず、というのが自然な直感だろう。SRD をバックエンドとする EFA は損失耐性と低テールレイテンシの両方で ENA 上の TCP を凌駕するというのが、AWS 上の HPC / ML ワークロードにおける常識に近い。筆者自身、NIXL over EFA のセットアップに数週間を費やした時点でも、当然のように「差は出るはず」という仮説のもとで実験設計を組んでいた。

ところが SageMaker HyperPod EKS 上で P/D 分離推論を Qwen2.5 7B の長コンテキスト条件下で丁寧に走らせると、EFA/SRD 経路と TCP 経路はスループット / TTFT / TPOT のどの指標でも統計的に等価という結果になった。しかも 5 iteration の Pod 再デプロイ込みで iter 間再現性はスループットで σ/μ が 0.02% を下回り、観測された差はすべて TOST 等価性検定で ±5% マージン以内に収まる。

この記事は、その「差が出なかった」という結果を裏から覗き込んだ記録である。単純に等価という結論で閉じず本稿を一万字規模で展開するのは、この等価性が「EFA が意味ない」という乱暴な帰結に回収される前に、条件境界を丁寧に言語化しておきたかったからだ。compute-bound 領域では KV 転送経路が TTFT / TPOT を律速しないという等価性の意味を、測定の厳密性、物理経路の自己検証、反証可能性の提示まで含めて共有したい。

## 実験設定の要点

測定はすべて SageMaker HyperPod EKS 上で行った。Prefill ノードは g5.12xlarge で A10G を 4 枚、Decode ノードは g5.16xlarge で A10G を 1 枚、それぞれの Pod を NodeSelector で別ノードに明示的に pin している。モデルは Qwen/Qwen2.5-7B-Instruct、ワークロードは input 4096 tokens / output 64 tokens、num-prompts 100、warmup 20、concurrency は 16 と 32 の 2 水準を比較した。

比較する 2 バリアントは UCX のトランスポート設定のみを差し替えている。R3 は `UCX_TLS=srd,self,sm` で SRD over EFA、R4 は `UCX_TLS=tcp,self,sm` で TCP over ENA を強制する。`UCX_NET_DEVICES`、`FI_PROVIDER`、`FI_EFA_USE_DEVICE_RDMA` の 3 軸を一貫して切り替え、どちらかが中途半端に混ざることが無いようにした。NIXL は 0.9.0、UCX は 1.20.0、`kv_buffer_device=cpu` で運用している。A10G は GPUDirect RDMA に非対応なので、KV cache は host DRAM にステージングされてから UCX で運ばれる構成になる。

各 variant は 5 iteration を走らせ、iteration ごとに vLLM Pod を再デプロイすることで warm cache が揺らぎを隠さないようにした。乱数 seed は 42 に固定し、ワークロード側の揺らぎを抑えている。生計測値はすべて生ログに残し、後段で TOST equivalence、Welch t-test、bootstrap 95% CI を一括して通している。

## 結果の概観

まず mean ± std のレベル感を確認する。

| Variant | c | n | total_token_throughput | mean_ttft_ms | mean_tpot_ms | request_throughput |
|---|---|---|---|---|---|---|
| R3 EFA/SRD | 16 | 5 | 3577.9 ± 0.46 | 8301.1 ± 10.97 | 152.908 | 0.8601 |
| R4 TCP | 16 | 5 | 3577.7 ± 0.44 | 8310.3 ± 20.29 | 152.773 | 0.8600 |
| R3 EFA/SRD | 32 | 5 | 3688.1 ± 0.32 | 18271.8 ± 2.86 | 254.105 | 0.8866 |
| R4 TCP | 32 | 5 | 3688.1 ± 0.18 | 18270.8 ± 1.06 | 254.111 | 0.8866 |

concurrency 16 のスループットは小数第 1 位で一致し、concurrency 32 に至っては小数第 1 位の値が完全一致している。mean TTFT は 16 並列で 9 ms 程度、32 並列で 1 ms 程度の差しかない。mean TPOT は 0.1 ms のオーダーで食い違う。どの差も、本番ワークロードの SLA 閾値に載せるとノイズとしか分類できない規模である。

ここで重要なのは、差がゼロに見えるからといって自動的に「差がない」と結論してよいわけではない、という統計の基本である。次節では、その結論のためにどのような検定を積み上げたかを紐解いていく。

## 等価性をどう示したか

本節では 4 段のロードマップで論点を積む。TOST を等価性検定として選んだ理由、全 8 ケースでの TOST 判定、bootstrap 95% CI による分布仮定への頑健性、5 iter × 再デプロイ × seed 固定による iter 間再現性、の順に辿っていく。

### なぜ TOST なのか

Welch t-test で p 値が 0.05 を超えたからといって「差が無い」ことを示したことにはならない。通常の Welch t-test の帰無仮説は「差が無い（μ_A = μ_B）」であり、この帰無仮説を棄却できなかった状態が p>0.05 である。つまり p>0.05 は帰無仮説を棄却できなかったという事実に過ぎず、帰無仮説そのものを受容する根拠ではない。データ数が少なくて検出力が足りないケースでも p は大きくなるので、p>0.05 は「差が有ることを検出できなかった」以上のことを意味しない。

等価性を主張したいなら、TOST (two one-sided tests) を使うのが王道となる。TOST は通常の検定と帰無仮説の向きを反転させる。具体的には「差 ≤ −δ」と「差 ≥ +δ」という 2 本の片側帰無仮説を立て、それぞれ別個に有意水準 α で棄却する。2 本の両方が棄却されて初めて、差が [−δ, +δ] の範囲内に収まっていると能動的に主張できる。単一の AND 同時棄却ではなく、独立した 2 本の片側検定がいずれも棄却される必要がある点が肝心で、どちらか一方でも棄却できなければ等価性は主張できない。δ はドメイン知識で事前設計する等価マージンである。

本実験では ±5% を等価マージンとして事前に定義した。結果を見てから事後調整したものではない。スループットが 5% 食い違えばオートスケーリング閾値や SLA 評価に影響が出るが、それ以下は設計ノイズとして扱える、という相場感に基づく判断である。Pod の再デプロイを挟む iter 間変動を考えると ±1% や ±2% ではなく ±5% が現実的な水準だった。

### 全 8 ケースで TOST 等価性が成立

集約された TOST 結果を以下に示す。ここでは差 Δ、95% bootstrap CI、Welch t-test の p 値、TOST equivalence の判定、および Hedges g（サンプルサイズ補正付き効果量）を並べている。

| metric | c | Δ (R3−R4) | 95% CI | Welch p | TOST equiv | Hedges g |
|---|---|---|---|---|---|---|
| request_throughput | 16 | +0.000 | [−0.000, +0.000] | 0.621 | True | +0.29 |
| request_throughput | 32 | −0.000 | [−0.000, +0.000] | 0.730 | True | −0.21 |
| total_token_throughput | 16 | +0.15 | [−0.36, +0.65] | 0.621 | True | +0.29 |
| total_token_throughput | 32 | −0.06 | [−0.36, +0.21] | 0.730 | True | −0.21 |
| mean_ttft_ms | 16 | −9.18 ms | [−27.72, +8.36] | 0.407 | True | −0.51 |
| mean_ttft_ms | 32 | +1.05 ms | [−0.96, +3.70] | 0.475 | True | +0.44 |
| mean_tpot_ms | 16 | +0.14 ms | [−0.16, +0.43] | 0.443 | True | +0.47 |
| mean_tpot_ms | 32 | −0.01 ms | [−0.02, +0.00] | 0.333 | True | −0.60 |

TOST equiv が全 8 ケースで True になった。Welch t-test の p はいずれも 0.33 以上で、差が有るとは言えない。ここが重要なのだが、Welch の p>0.05 だけで安心するのではなく、TOST の等価判定と組み合わせて初めて「±5% の範囲内で等しいことが統計的に示された」と言える。表中の request_throughput の Δ は表示桁数の都合で +0.000 / −0.000 と丸められているが、元値は 16 並列で R3 が 0.8601、R4 が 0.8600 で、実差は 0.0001 req/s 相当にすぎない。この程度の桁が小数第 4 位でしか現れないこと自体が等価性の視覚的証拠になっている。

効果量 Hedges g は最大で 0.60 である。教科書的には「中程度」と書かれる値だが、n1=n2=5 という小標本では g の点推定の不確実性が大きい。この標本サイズでの近似 SE から 95% CI を見積もるとおおむね ±1.3 程度の幅を持ち得るため、「0.60」という一点を独立した指標として過大解釈するのは誤りで、TOST と Welch を一次判定軸に置いた上で g を参考として添える順序で読むべきである。絶対値で見ても「5% マージン内での 0.6 標準偏差分」であり、スループットの std 0.46 の環境で 0.27 相当、平均 3577 の 0.008% でしかない。

複数検定の観点も触れておきたい。本実験では 8 ケース（metric 4 種 × concurrency 2 水準）を同時に TOST に通しており、TOST が片側 2 本ずつなので実質 16 本の片側検定が並列に走っている。TOST の実効検定数を metric×concurrency の 8 本として扱う流儀もあるが、本稿は保守側に倒して片側 2 本 × 8 = 16 を Bonferroni 分母として扱う。α を 0.05 / 16 ≈ 0.003 まで厳しくしても、Welch 側の最大 p は 0.730、TOST 側も全ケースで境界から十分に離れて判定が成立しており、補正の影響は無視できる範囲である。

### bootstrap 95% CI の役割

TOST と Welch は分布の正規性に依存する部分があるため、念のため bootstrap 95% CI を 10000 resamples の percentile 法で並行して計算した。percentile 法を選んだのは、n=5 の小標本では BCa 法の加速度推定が不安定になりやすく、単純な分位点ベースのほうが挙動が素直だからである。分布フリーのアプローチで差の信頼区間を推定することで、仮に母集団分布に歪みがあっても結論が崩れないことを確認している。結果として、CI はいずれの指標でも等価マージン ±5% の内側に完全に収まっている。スループットに至っては CI の両端まで小数点第 1 位のオーダーで、平均値からの逸脱が極めて限定的であることを示している。

なお α=0.05 の TOST と厳密に等価な CI は片側 95%、すなわち両側 90% CI ⊂ ±δ という条件であり、95% CI ⊂ ±δ はこれより保守的な判断になる。本稿は判定を厳しめに倒すため 95% CI を採用し、それでも全指標で ±5% 内に収まることを確認している。

### 再現性を支える 5 iter × 再デプロイ × seed 固定

統計検定の前提条件として、iter 間の再現性が一定以上確保されていなければ何を測っているかわからなくなる。本実験では iteration ごとに Pod を再デプロイしているので、warm cache や serving worker の初期状態差が揺らぎの主要因になるのだが、スループットの iter 間変動は R3 の concurrency 16 で σ/μ が 0.02% を下回る水準（算出値は std 0.46 / mean 3577.9）にあり、R4 の concurrency 32 では 0.006% レベルまで下がっている。warmup_detector は throughput 系列の i1 について exclude 判定を出しているが、相対偏差は 0.003% 以下であり、MAD が極端に小さい環境下で z_robust が敏感に反応した結果と解釈できる。実質的には無視して差し支えなく、i1 exclude の有無で TOST 結論は変わらない。

seed 42 固定はワークロード生成側の乱数に効いている。同じプロンプト列が両 variant に入ることで、比較の対照性を担保している。

## 物理経路の自己検証

統計的に等価という結論だけでは、「UCX_TLS の切替が物理経路に届いていないのではないか」という自然な疑念が残る。もし R3 と R4 の両方で実態として TCP を叩いていたとしたら、それは「等価」ではなく「何も切り替えていない」ことになる。これを塞ぐためには、物理層の活動を独立に観測する必要がある。

本実験では efa-counter-reader という DaemonSet を各ノードに常駐させ、`/host/sys/class/infiniband/<dev>/ports/1/hw_counters/` 配下のカウンタを iteration の pre/post で差分取得している。Prefill 側の NIC は rdmap0s26、Decode 側は rdmap0s29 である。ここで狙うのは、R3 で EFA カウンタが確実に増え、R4 で完全にゼロになる、という positive / negative 両方の control を同時に成立させることである。

### R3 の iter ごとのカウンタ増分

R3 では 5 iteration すべてで tx_pkts、send_wrs、send_bytes、rx_pkts、recv_bytes が前後差分でプラスになっている。具体的には send_bytes が iteration あたり NIC 1 枚につき 2.1〜2.9 KB、tx_pkts が 30〜40 packets / NIC 程度のスケールで増える。prefill 側と decode 側の両方で activity が検出されており、経路が一方だけ死んでいる可能性も排除できた。

iter 4 だけ send_bytes が 2730 / 2870 と一段高い値を示しているが、これは Pod 再デプロイのタイミングで接続確立時のハンドシェイクパケット数が揺らいだ程度の差異であり、統計的な有意差を生むほどの規模ではない。

### R4 の完全なカウンタ静止

R4 では 5 iteration すべて、prefill / decode 両方、全カウンタについて delta=0 が成立した。つまり TCP バリアントのときは EFA デバイスが文字通り一度も叩かれていない。UCX_TLS の上書きが物理経路まで確実に反映されていることの最もクリーンな証拠となる。

positive control で活動を確認し、negative control で静止を確認する、というのは実験における基本動作だが、ソフトウェア層のフラグ切替が物理層にまで届いているかを示す手段として NIC ハードウェアカウンタの前後差分は極めて強い証拠になる。ここが崩れていないからこそ、統計的等価性の結論を「物理経路が違うのに等価」という強い意味で主張できる。

### カウンタ増分と KV 本体の桁違い

ここで一つ重要な観察がある。R3 の EFA カウンタ増分は iteration あたり数 KB のオーダーに留まる。ところが Qwen2.5 7B で input 4096 tokens × 100 prompts を prefill → decode で分離転送する場合、KV cache の実体サイズは GB オーダーに達する。Qwen2.5-7B は GQA（num_key_value_heads=4）を採用しているため、28 × 2 × 128 × 4 × 4096 × 100 × 2 Byte ≈ 約 23.5 GB となる（GQA を外して Q 側の head=32 のまま積む naive 計算だと約 188 GB）。PagedAttention 系のブロックページングでは典型的に 50〜70% の削減が報告されるが、それを加味しても十数 GB 以上のスケールで収まる水準である。いずれにせよ、カウンタに現れた数 KB と KV 本体の十数 GB 規模の間には 7 桁を超える規模の乖離があり、GQA を考慮してもしなくても結論の向きは変わらない。

この乖離をどう読むかは慎重にならなければならない。R3 の設定で実際にノード間を流れる bulk ペイロードがどこに乗っているかを、観測事実に基づいて絞り込む必要がある。UCX の transport 候補のうち `self` は同一プロセス内のループバック、`sm` は同一ホスト内プロセス間の shared memory であり、いずれもノード境界を越えない。prefill / decode が NodeSelector で別ノードに pin されている本構成では、ノード間の KV 本体が self / sm の上で流れることは物理的に起きない。従って R3 のノード間候補は UCX の srd（EFA SRD）か tcp のどちらか、R4 は tcp 一択となる。

ここが核心だが、R3 で EFA カウンタに現れるのが数 KB しかないという事実は、「KV 本体が srd に乗っているなら十数 GB 超のスケールの send_bytes 増分が立つはず」という期待と真っ向から反する。したがって「R3 の KV 本体が srd 経由で流れている」という素朴な仮説は、NIC カウンタの規模と整合しない。

srd 経由仮説を落としたあとに残るのは 2 系統である。一つは R3 でも実体として tcp パスが使われているパターン（UCX 内部で srd が選ばれず tcp にフォールバック、あるいは NIXL 層が UCX を経由しない別チャネルを使うケース）、もう一つは大ペイロードが NIC カウンタに乗らない経路（vLLM や NIXL の内部 bulk path、host DRAM を介して NIC カウンタを経由しない host 内 code path）を支配的に通っているパターンである。どちらが実態かの特定には内部プロファイリングが必要で、本 Part の射程外となる。ここで断言できるのは、R3 の EFA カウンタに現れた数 KB が side-channel 相当の小ペイロード（接続確立やメタデータ交換）の規模にとどまり、KV 本体の bulk 転送がカウンタに乗っていないという事実だけである。

## なぜ等価になるのか

### `kv_buffer_device=cpu` というパラメータの重さ

A10G は GPUDirect RDMA に非対応なので、本設定では `kv_buffer_device=cpu` を指定している。これにより KV cache は一度 host DRAM にステージングされる。UCX のトランスポート候補は `srd,self,sm` や `tcp,self,sm` のように `self` と `sm` を含んでいるが、前節で確認したとおり `self` は同一プロセス内ループバック、`sm` は同一ホスト内プロセス間の shared memory であり、prefill ノードと decode ノードが別ノードに pin されている本構成ではノード間の bulk 転送路としては使えない。従ってノード間の KV 本体は R3 では srd（本来の想定経路）、R4 では tcp のいずれかに流れることになる。

ここで「想定」と「観測」の乖離が生まれる。R3 で本来 srd 経由で流れるはずの十数 GB 規模の KV 本体は、EFA NIC カウンタに乗っていない。前節で論じた通り、R3 の実体は tcp フォールバックか、あるいは vLLM / NIXL 内部の UCX を経由しない bulk パス、あるいは host DRAM を介する NIC カウンタを経由しない host 内 code path のどれかになる。いずれの候補でも、EFA の RDMA 直接転送による帯域優位は実運用に届いていない。

`kv_buffer_device=cpu` という設定は、GPUDirect RDMA が使えない環境で採用される現実的なコンフィグであり、その代償として KV 本体の転送経路が host DRAM を挟む形になる。この構造が EFA の帯域優位を前段で吸収している、という解釈がカウンタの観測事実と整合する。これが等価性の第一の有力な解釈である。ただし実経路の特定には内部プロファイリングが必要で、本稿は実経路未特定のまま「どの候補でも EFA 帯域優位が実運用に届かない」という点で等価性結論が成立する、と述べるにとどめる。

### compute-bound / memory-bound という領域の支配

第二の要因は、ワークロードが prefill 側では compute に、decode 側では memory bandwidth に、それぞれ律速されていることである。7B × 4096 tokens × concurrency 数十という条件では、prefill 側の GPU FLOPs 消費が主ボトルネックになる。

具体的な数字でレベル感を確認する。concurrency 16 で TTFT が 8.3 秒、concurrency 32 で 18.3 秒。concurrency が 2 倍になると TTFT もほぼ 2 倍で伸びる、という線形の関係は、prefill が完全にキューで直列化されている、すなわち compute が飽和している典型的な振る舞いと解釈できる。本実験では GPU 利用率や MFU を直接ログしていないため断定ではなく解釈だが、network 経路の違いで数 ms から数十 ms の差を生んでも、8 秒スケールの prefill 時間の中では変動として観測できない、という方向の含意は動かない。

decode 側の TPOT は、prefill とは別の律速構造にある。1 トークン生成あたり 150〜250 ms の内訳は、attention 時の KV cache からのフェッチと各 layer の matmul が中心で、batch 内並列度が限定的な decode フェーズでは FLOPs より memory bandwidth のほうが先に飽和する、いわゆる memory-bound 領域に寄る。本来の memory-bound は GPU HBM bandwidth 律速を指すが、本構成では `kv_buffer_device=cpu` により KV cache が host DRAM に置かれるため、KV fetch に関してだけは PCIe 越しの host DRAM read が追加で入る。decode の KV fetch は per-token で毎ステップ発生するため、本構成では PCIe / host DRAM 経由の fetch が dominant になり得る。つまり本構成の decode は「HBM bandwidth 律速」と「KV fetch の PCIe / host DRAM 経路」の 2 層が重なった memory pressure を持つ。いずれにしても network 経路（EFA か TCP か）は HBM bandwidth や PCIe の host DRAM read そのものを速くも遅くもしないので、TPOT に差が現れない、という構造になる。

### 等価性の二重構造

この 2 つの要因は独立だが、両方が同時に効いていると考えるのが実態に近い。`kv_buffer_device=gpu` を使える GPU に変えても prefill compute-bound と decode memory-bound が解けなければ TTFT / TPOT に現れないし、逆にこれらの律速が解けても `kv_buffer_device=cpu` のままなら EFA の帯域優位は host DRAM ステージングに吸収される。等価性の結論は、この 2 つが重なった領域で成立すると読むのが観測と整合する。

## 対立仮説の検討

代表的な対立仮説を順に検討しておく。

第一に、「UCX_TLS の設定が上書きされず、両者とも同じ経路を叩いていた」という可能性。これは R4 で EFA カウンタが完全にゼロになっている事実で直接棄却される。TCP バリアントでは EFA NIC が本当に一度も使われていないのだから、切替は効いている。

第二に、「Pod 再デプロイの揺らぎが信号を埋めている」という可能性。iter 間変動が σ/μ で 0.02% を下回る再現性を示しており、もし有意な差があれば 0.02% オーダーの標準偏差で検出可能だったはずである。TTFT 9 ms の差は 8.3 秒の 0.11% に相当し、この環境の std の範囲内にある。つまり差が埋まっているのではなく、そもそも std 以下の差しかない、と解釈するのが妥当である。

第三に、「ワークロードが軽すぎて両者とも idle で回っていた」という可能性。絶対値のスループットが 3577〜3688 token/s で、concurrency 32 では request_throughput が 0.89 req/s まで上がっている。Pod 配置の通り prefill 4 GPU + decode 1 GPU を使い切った上での数字なので、少なくとも prefill 側は compute を飽和させている。idle が支配的という仮説は成立しない。

## この結果の境界を明確にする

一番重要なのは、この等価性が成立する条件の境界を読者が掴めるかどうかだ。本実験の結論は「g5 の A10G で `kv_buffer_device=cpu` 運用、input 4096 tokens、concurrency 16 から 32」という領域に限って主張できる。この範囲の外では、同じ結論が成立するとは限らない。

どの変数を動かせば差が出る可能性が上がるか、整理しておこう。

GPU を H100 / Trn1 / Trn2 のような GPUDirect RDMA 対応プラットフォームに置き換え、同時に `kv_buffer_device=gpu` を指定すれば、KV 転送が GPU メモリ間で直接 RDMA で運ばれる。具体的には KV cache サイズが GPU HBM 容量を超える閾値に近づいたあたりで、host DRAM ステージングを噛まない分だけ EFA の帯域が KV 本体のペイロードに効くようになり、等価性は崩れる可能性が高い。

入力長を 16k / 32k に伸ばせば、KV cache の総サイズが桁違いに大きくなる。prefill 時間も長くなるが、転送時間の絶対値も伸びるため、network path の差が相対的に見えやすくなる。加えて、host DRAM ステージング側の容量や PCIe 帯域が新たな制約になる領域も視野に入ってくる。

Tensor Parallel を跨いでの aggregate 帯域を測る構成でも差が出やすい。複数 GPU から同時に KV が吐き出される scaleout パターンでは、複数フローが同時に飽和した際の TCP のフェアネス劣化が輻輳制御越しに見えてくる領域になる。EFA SRD の多パス並列が効く領域である。

Multi-node multi-GPU へ拡げたスケールアウトでは、トラフィックが複数の NIC を同時に飽和させるケースが出てくる。multi-rail EFA で NIC を複数束ねたときの aggregate 帯域が飽和するレンジに入ると、TCP の公平性と SRD のスライスごとの独立配送の挙動差が効いてくる。

要するに、本実験は「compute が支配して network が表舞台に出てこない領域」にピン留めされた結果であり、「network が支配する領域」は意図的に切り分けて future work に回している。自分のワークロードが前者か後者かを切り分ける判断基準こそが、この結果を実務に移植するための鍵になる。

## 実務へのインプリケーション

設計判断の観点から読み替えると、本結果は次のことを言っている。

P/D 分離を導入したから network を高速化すれば TTFT が下がる、という単純な期待は、compute-bound 領域では裏切られる。network path の選択は TTFT / TPOT に対して寄与する領域を限定的にしか持たず、ボトルネック分析を先に済ませずに EFA を調達しても、その投資はここで観測されているような領域では回収できない。

逆方向の読み替えとして、本結果は TCP fallback を許容する構成領域が存在することを示してもいる。EFA 非対応リージョン、EFA SG 制限がある multi-tenant 環境、コスト最適化のために ENA 一本で回したい構成などで、TCP 経路を選んでも production 品質を落とさないという定量的根拠になる。EFA を必ず要求する設計原則を条件付きで緩めてよい、という意思決定に繋がる。

もうひとつの含意として、ワークロード設計の変更が network 投資を再評価させる、という点がある。long context 対応や TP 分割スケールアウトを進めれば今回の等価性は成り立たなくなる可能性があり、そのタイミングで EFA の必要性を再測定するべきである。ベンチマークは一度で完結せず、workload profile が変わるたびに回し直すべきものだ、という当然の原則ではあるが改めて強調しておきたい。

ベンダ調達や物理配置の設計文書でこの結論を引用するときは、本実験の条件（モデルサイズ、入力長、concurrency、GPU 種別、`kv_buffer_device` 設定）を必ず明記すること。条件を外した一般化は誤解を生む。

意思決定フローに落とし込むと次の順序で判断を積むのが実務的だろう。まずワークロードが compute-bound か memory-bound か bandwidth-bound かをプロファイルで切り分ける。prefill が compute を飽和させ、decode が host DRAM fetch に律速されている（かつ GPUDirect RDMA 非対応 GPU で `kv_buffer_device=cpu` が前提）ならば、本結果の等価性領域に入り、TCP 一本の構成も production 選択肢になる。次に GPUDirect RDMA 対応 GPU への移行、入力長 16k / 32k 化、TP 分割による aggregate 帯域の増加、multi-node スケールアウトのいずれかが yes なら、等価性の前提が崩れる可能性が高く、EFA SRD を再検証すべきである。いずれにも該当しなければ TCP 経路を production 採用してよい。この 2 段階フィルタを通してから基盤経路を決めるのが現実的な順序となる。

## Future Work と Limitations

本実験の結論は、以下の条件外での挙動を何も保証しない。

GPUDirect RDMA 対応 GPU（H100、Trn1、Trn2 など）で `kv_buffer_device=gpu` を有効にしたときの再評価は、最も優先度の高い future work である。この構成では KV 転送の実体がノード間 RDMA で直接流れるため、network path の差が素直に TTFT / TPOT に現れる可能性が高い。仮にその条件下でも等価性が維持されたなら、それは本当に EFA を常用しなくてよいことの強い証拠になる。

Super long context（16k / 32k 入力）と TP 分割の組み合わせも未検証である。入力長は KV cache サイズに線形で効き、concurrency や TP 分割はプレッシャーの種類を変えるため、これらの組み合わせで等価性が崩れる変曲点を特定したい。

Multi-node multi-GPU の aggregate 帯域観測も必要だ。本実験は 1 prefill ノード + 1 decode ノードの最小構成だが、実運用ではより大きなスケールアウトが使われる。複数 NIC 同時飽和の条件で EFA の多パス並列が効く領域を切り出したい。

LMCache や Mooncake のような KV cache 共有ミドルウェアは EFA との直結パスを持ち、NIXL と異なるプロトコルスタックで動く。これらとの比較は、NIXL 実装自体の transport 選択が妥当かを検証する上で重要である。

Open-loop ワークロード（request-rate ベース）での再評価も意味がある。本実験の closed-loop concurrency モデルは queue でリクエストを詰め込む形になるため、arrival burst の影響が丸まる。実プロダクション負荷は開系であり、TCP の輻輳制御や SRD のバックプレッシャ挙動の差は open-loop の方が現れやすい。

compute-bound / memory-bound のレジーム判定を GPU 利用率や MFU で直接示す作業も未実施である。今回は TTFT が concurrency に対して線形に伸びる観察と TPOT の桁感から間接的に推定しているが、GPU util や MFU を並行ログすることで「本当に compute を使い切っていたのか」を数値で裏取りするのが望ましい。

Limitations として、さらに以下の項目を明示しておく。第一に n=5 という小標本サイズは、TOST と bootstrap を相互補強しても検出力に限界があり、±5% マージンの内側で微細な系統差が存在しても検出しきれない可能性がある。より精密な等価性を主張するなら n を 15〜30 以上に引き上げたい。第二に本実験は単一リージョン・単一 AZ 内の cluster で完結しており、リージョン間遅延や AZ 跨ぎの挙動は対象外である。第三に Pod の再デプロイは同一 NodeSelector 範囲内で行われ、prefill / decode の物理個体は iter 間で固定されている。別ノードへの placement variation（ToR スイッチ構成や NIC 個体差）の影響は測っていない。第四に closed-loop concurrency モデルでの計測であり、open-loop（request-rate ベース）は未測定である。第五に HuggingFace cache の配置や同居テナントによる noisy neighbor 効果を統制しておらず、iter 間での cache warmth 変動や他 Pod からの PCIe / NIC 競合は系統誤差として残り得る。第六に UCX 内部ログ（`UCX_LOG_LEVEL=debug` による transport 選択トレース等）は取得しておらず、srd が実際に起動していたかをソフトウェア側から直接確認していない。第七に multi-rail EFA（複数 NIC を束ねた aggregate 構成）は検証していない。

これらの Limitations を明示的に書き出すのは、本結論の射程を誠実に限定するためだ。等価性の主張を過度に一般化しないためのブレーキとして、これらの条件はすべて future work に記録しておく。

## まとめ

SageMaker HyperPod EKS 上で Qwen2.5 7B の P/D 分離推論を Wave 3 条件で測定したところ、EFA/SRD 経路（R3）と TCP 経路（R4）は concurrency 16 と 32 のいずれにおいても、スループット・TTFT・TPOT の全 8 ケースで TOST 等価（±5% マージン、5 iter × Pod 再デプロイ × seed 固定）となった。EFA ハードウェアカウンタは R3 で iteration ごとに増加し、R4 で完全にゼロとなり、物理経路の切替は確実に効いていることが確認された。

この等価性は、`kv_buffer_device=cpu` による host DRAM ステージングと、7B × 4096 tokens × concurrency 数十という prefill compute-bound 領域の二重構造に由来する。EFA は NIXL のサイドチャネル相当の小ペイロード（数 KB 規模）として観測されるにとどまり、KV 本体の bulk 転送は NIC カウンタに現れない。その実経路が tcp フォールバック・内部 bulk path・host 内 code path のいずれなのかの特定は本 Part の射程外だが、どの候補であっても network path の違いが TTFT / TPOT を動かさないという結論は共通である。

実務的な帰結として、この領域では EFA を必須要件としない構成選択が定量的に裏付けられた。一方、GPUDirect RDMA 対応 GPU、super long context、TP 分割、multi-node scaleout、open-loop workload など、どれか一つでも条件が動けば等価性は成立しなくなる可能性が高い。この結論は領域限定の等価性であり、「EFA は意味ない」という一般化ではない。

最後に読者に残しておきたいのは、ベンチマーク結果を設計判断に移すときは、まず自分のワークロードが compute-bound か bandwidth-bound かを切り分け、次にその領域で network path の差が効くかを定量的に確認する、という順序である。本実験はその方法論の一つの例に過ぎず、同じ手順を別の条件で回し直すことで、自分の環境に固有の境界線を描くことができる。次の Part では、本 Part で保留した優先課題のうち「GPUDirect RDMA 対応 GPU（H100 / Trn1 / Trn2）で `kv_buffer_device=gpu` を有効化したときの再測定」を先に扱う予定である。その後の Part で UCX / NIXL 内部プロファイリングによる KV 本体の実経路特定に進み、カウンタに見えない bulk path の正体を追っていく。

