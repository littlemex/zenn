---
title: "Awsome: miles の GRPO のテストケースを足す"
free: false
---

## はじめに

本章では、[miles](https://github.com/radixark/miles) の GRPO のテストケースを足した [Issue #1224](https://github.com/awslabs/awsome-distributed-ai/issues/1224) と [PR #1225](https://github.com/awslabs/awsome-distributed-ai/pull/1225) をまとめる。miles は SLIME を CUDA 13 と NVIDIA Blackwell 向けにフォークしたもので、このテストケースは前章の SLIME のテストケースと同じ形にそろえ、2 つを同じ手順で使って比べられるようにした。

:::details colocated と disaggregated
強化学習の後学習では、生成（rollout）と学習を同じ GPU で交互に行う colocated と、別々の GPU に分ける disaggregated の 2 つの構成がある。colocated は GPU を無駄なく使えるが、生成と学習の切り替えのたびにメモリを入れ替える。disaggregated は切り替えが要らないが、学習した重みを生成の側へネットワーク越しに送る。
:::

## Issue と PR の概要

![RayCluster の head は調整役で GPU を持たず、GPU のワーカーが SGLang の生成と Megatron の学習を受け持つ。FSx for Lustre と、クラスタの中でイメージを作る BuildKit の Job がそれを支える。CPU の報酬サービスは、報酬を別に計算する remote_rm の経路のための任意の部品で、学習の報酬の経路としては動かしていない。レビューの 18 件は、名前空間と head の取り違え、報酬の誤りを 0.0 として返すこと、イメージとドキュメントのずれ、検証の表の再現性などだった](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/awsome-miles-grpo/layout.png)

| 項目 | 内容 |
| --- | --- |
| 解決したいこと | CUDA 13 と Blackwell を対象にした SLIME のフォークの miles を、このリポジトリで SLIME と並べて使い、GPU に合うスタックを選べるようにする |
| やったこと | Dockerfile、RayCluster などの Kubernetes のマニフェスト、dense と MoE のレシピ、報酬サービス、変換と評価のスクリプト、README を足した |
| テスト | レビューの後、別のリージョンに一からクラスタを作り、README の手順どおりに dense と MoE の 4 つの構成、チェックポイントの往復の変換、報酬サービスを確かめた |
| マージ | [f342873](https://github.com/awslabs/awsome-distributed-ai/commit/f342873c8d170678a53231549295e1e5454eb279) |

## どうやったか

テストケースは [examples/training/miles](https://github.com/awslabs/awsome-distributed-ai/tree/f342873c8d170678a53231549295e1e5454eb279/examples/training/miles) にある。ベースのイメージは `radixark/miles` を digest で固定し、その上に AWS の EFA の層を足した。Kubernetes のマニフェストは `envsubst` で値を埋める形で、RayCluster、クラスタの中でイメージを作る BuildKit の Job、データを用意する Pod、報酬を別に計算する `remote_rm` の経路のための CPU の報酬サービスからなる。同梱のレシピは、既定ではルールに基づく報酬を学習の中で計算する。30B MoE は、最初のレシピでは rollout の後の最初の optimizer の step で GPU のメモリが足りなかった。そこでレシピに `--optimizer-cpu-offload` などを足し、optimizer をホストのメモリに逃がした。`run-on-cluster.sh` は、head の Pod の中からレシピを Ray の job として投げる。

## テストをどうしたか

最初の PR の説明には 9 行の結果の表を載せたが、レビューで、同梱した設定だけでは再現できない行があると指摘された。筆者は再現できない行を消し、レビューの後に別のリージョンに一からクラスタを作り、KubeRay を入れ、クラスタの中の BuildKit の Job でイメージを作り、README の Quick Start を step 0 から書いてあるとおりに辿った。以下は、筆者が p5en.48xlarge（1 ノードに H200 8 枚）で、構成ごとに別々に動かした結果で、[PR の最終の返答](https://github.com/awslabs/awsome-distributed-ai/pull/1225)に載せた。値は TensorBoard のイベントファイルから読み戻したもので、実行ごとに記録を分けてある。ループが最後まで回ったことを示す値で、収束を示すものではない。

| 構成 | rollout の回数 | reward | repetition | 時間 |
| --- | --- | --- | --- | --- |
| Qwen3-4B dense、colocated、1 ノード | 100 | 0.508 | 0.023 | 3 時間 15 分 |
| Qwen3-4B dense、disaggregated、2 ノード | 60 | 0.461 | 0.008 | 1 時間 50 分 |
| GLM-Z1-9B dense、colocated、TP=2、2 ノード | 100 | 0.672 | 0.008 | 2 時間 35 分 |
| Qwen3-30B-A3B MoE、colocated、EP のみ、2 ノード | 100 | 0.719 | 0.008 | 5 時間 5 分 |

どの構成も Ray の job は `succeeded` で終わった。チェックポイントは、Hugging Face から Megatron への変換と、4B の実行のチェックポイントからの逆の変換を、README のコマンドで確かめた。報酬サービスは、CPU のノードで 4 つのレプリカが Ready になることと、閾値を下げて readiness の動きを直接確かめた。学習の報酬の経路としては動かしていないので、miles の `remote_rm` が 503 を再試行するかは確かめていない。

4B の 100 回の rollout では、rollout の reward が平らなまま、AIME の評価が 0.596 から 0.333 に下がった。筆者は、応答の平均の長さが伸び、上限の 16384 トークンに届く応答の割合が 0.38 から 0.60 に増えたので、切り詰めと連動していると書いたが、原因は切り分けていない。TP と EP を組み合わせた MoE の構成で FlashInfer の融合を有効にすると出力が崩れること（レシピはこの構成で融合を切る）と、Qwen2.5-72B が試した構成では H200 16 枚に収まらないことは、結果ではなく制約として README の Known Issues に書いた。

## レビューで直したこと

KeitaW さんは、レビューの前に p6-b300.48xlarge の 2 ノードのクラスタで、Qwen3-4B の dense、colocated の構成を GPU 8 枚で最後まで動かしたと報告した。GRPO の生成、報酬、Megatron の学習、colocated の CUDA IPC での重みの同期は Blackwell でも動き、reward は 3 回の rollout で 0.4922、0.5156、0.5313 と動き、AIME の評価は 0.6125 だった。そのうえで、4 回に分けて 18 件の指摘があり、すべて対応した。主なものは次のとおりである。

| 指摘 | 対応 |
| --- | --- |
| `run-on-cluster.sh` が env のファイルの名前空間を読まず、`ray.io/node-type=head` の最初の Pod を選ぶので、別のクラスタの head に投げうる | [マージした版](https://github.com/awslabs/awsome-distributed-ai/blob/f342873c8d170678a53231549295e1e5454eb279/examples/training/miles/run-on-cluster.sh)は、名前空間をフラグ、シェルの変数、env のファイルから決め、`ray.io/cluster` のラベルで head を選び、ワーカーの状態も確かめる |
| 報酬サービスが、採点の例外をすべて reward 0.0 の 200 で返す。壊れたまま 0 の信号で学習が最後まで進む | [マージした版](https://github.com/awslabs/awsome-distributed-ai/blob/f342873c8d170678a53231549295e1e5454eb279/examples/training/miles/reward_service/app.py)は、採点に失敗したら 0.0 ではなく 503 を返して数え、既定で 20 回続けて失敗したら readiness を 503 にする。採点器は別のプロセスで動かし、時間の上限を付ける |
| `evaluate.sh` が文字列の一致で採点していて、数学的に同じ答えを誤りと数える | 報酬と同じ採点器（miles の `grade_answer_verl`）を使う |
| README が引用する miles の commit と、固定したイメージの中の commit が違う | イメージを固定しているものを正として表を直した |
| 検証の表の行のうち、同梱した設定から再現できないものがある | 再現できない行を消し、足りなかった GLM-Z1-9B の設定と実行の環境変数を足して測り直した |

**head の Pod の設定**。head のコンテナは GPU を持たず、`libcuda.so.1` も無い。一方で head は CPU を出しているので、Ray は CPU だけを求める制御用の actor（RolloutManager など）を head に置ける。RolloutManager は Megatron 経由で transformer_engine を import するので、KeitaW さんの実行では投入から 46 秒で `OSError: libcuda.so.1` で止まった。KeitaW さんは、head の `num-cpus` を `'0'` にして制御用の actor を head に置かせない回避策を示し、あわせて head にドライバを持たせる `NVIDIA_VISIBLE_DEVICES=none` を提案した。筆者が p5en.48xlarge で `none` を試すと、GPU Operator が CDI を有効にした環境では空のデバイス名に解決され、Pod が起動しなかった。そこで `num-cpus: '0'` を採り、その限界を設定の横に書いた。この設定は head にドライバを与えるものではなく、CPU を求める actor を head から外すだけである。RolloutManager を head に固定するオプションを使うと、CPU を 1 つ求める RolloutManager を CPU の無い head に置けず、進まなくなる。CPU を求めずに head に固定される actor（`MultiLoRAController`）には、この回避策は効かない。同梱のレシピはこの actor を作らないが、レシピを広げるときは、`libcuda.so.1` を使う actor の置き場所を確かめる。

再現できなかった GLM-Z1-9B の行は、実機で原因を探した。4B のレシピはモデルによらないはずだったが、Ray の実行環境の変数に `CUDA_DEVICE_MAX_CONNECTIONS` が無く、テンソル並列を使う 9B だけが Megatron の検査で止まっていた。4B は TP=1 なのでこの検査に届かず、気づかなかった。

## 技術的な要点

- **報酬サービスの誤りの返し方**。誤りを正常な値（0.0）として返すと、学習は止まらずに無意味な信号で進む。誤りはエラーの状態で返して数え、連続したら readiness で外す。liveness につなぐと、1 つの壊れた入力で Pod が再起動を繰り返す。呼び出す側がエラーを再試行しない場合は、1 件の失敗で rollout のバッチ全体が落ちうるので、呼び出す側の動きも確かめる。
- **Pod の選び方**。同じラベルで選ぶ Pod は、同じ名前空間の別のクラスタのものかもしれない。KubeRay のクラスタのラベルまで含めて選ぶ。
- **digest で固定したイメージの版**。ドキュメントの版や commit は、そのイメージの中を実際に見て書く。
- **envsubst の未定義の変数**。未定義の変数を空の文字列に置き換えて成功する。埋める前に、必要な変数がそろっているかを確かめる。
- **検証の表の範囲**。同梱した設定だけで再現できる行に限る。TP=1 の構成で通っても、TP>1 で初めて通る検査がある。

## まとめ

テストケースの価値は、読者が書いてあることを信じて動かせることにある。レビューの 18 件の多くは、動くかどうかではなく、書いてあることと実際がずれていないかの指摘で、それを実機で確かめて直した。
