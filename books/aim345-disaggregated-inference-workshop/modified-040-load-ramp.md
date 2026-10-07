---
title: "Modified: 040 負荷を上げて実効スループットを比べる"
---

## はじめに

低い到着率での測定は終わった。稼働中の各スタックに長文脈トラフィックを増やして流し、サービス目標を満たす最も高い到着率を見つける。

目的は、両方のエンドポイントに同じ負荷の掃き取り (スイープ) を実行し、比較用の CSV を作ることである。やることは、統合構成のスイープを実行してから分離構成のスイープを実行すること、レイテンシ・達成率・待ち行列・キャッシュの証拠を収集すること、両方のスタックが開始時点の識別情報を保ったままであることを確認することの 3 つである。この章には 26 分が割り当てられている。達成率と有効な calls/s/GPU で各到着率を判断し、提供ウィンドウの後に完了したリクエストも含める。

## 手順

1. ベースラインのスイープを実行したタブで、この章の最初に両方の長文脈スイープを始める。

```bash
python3 4.ramp.py --traffic traffic/b.json --url http://127.0.0.1:8000 --architecture unified --instance-type "$LAB_INSTANCE_TYPE" --region "$LAB_REGION" --evidence-scope mechanism-validation --gpus "$LAB_GPUS" --rates 0.25 0.5 1 2 4 --duration-s 30 --output results/paired-unified-b && python3 4.ramp.py --traffic traffic/b.json --url http://127.0.0.1:8001 --architecture disaggregated --instance-type "$LAB_INSTANCE_TYPE" --region "$LAB_REGION" --evidence-scope mechanism-validation --gpus "$LAB_GPUS" --rates 0.25 0.5 1 2 4 --duration-s 30 --output results/paired-disaggregated-b
```

各到着率でウォームアップ、測定、リクエストのドレインが終わるまで待つ。g7.24xlarge 相当の割り当てでは、2026-10-03 にクラスタ内のクライアントから実行した両方のスイープで 16.2 分かかった。このライブのスイープは 4 tasks/s で終わる。この後に出てくる 8 tasks/s の行は記録済みの測定例である。

2. スイープが動いている間に、手順 5 と手順 7 を読んでおく。

CloudShell は、コマンドが実行中であっても 20 分から 30 分アイドルが続くとセッションを終了する。10 分おきに各タブをクリックしておく。

3. 両方のスイープが終わったら、統合構成のエンジンの証拠を収集する。

```bash
python3 5.collect.py --config config.unified.json --runs results/paired-unified-a results/paired-unified-b --output results/paired-unified-evidence
```

6 つの到着率まで走らせた g7.48xlarge のキャプチャ (2026-09-10) における完了記録の例は次のとおりである。キャプチャされたパスはリハーサル用ディレクトリを指しているが、自分の出力先は `results/paired-unified-evidence` になる。

```
{"summary_csv": "/tmp/g7-dual-instance/eks/paired/unified-evidence/summary.csv", "measured_rate_rows": 7, "qualified_rates": "/tmp/g7-dual-instance/eks/paired/unified-evidence/qualified-rates.json"}
```

4. 分離構成のエンジンの証拠を収集する。

```bash
python3 5.collect.py --config config.disaggregated.json --runs results/paired-disaggregated-a results/paired-disaggregated-b --output results/paired-disaggregated-evidence
```

g7.48xlarge のキャプチャ (2026-09-10) における完了記録の例は次のとおりである。出力先は `results/paired-disaggregated-evidence` になる。

```
{"summary_csv": "/tmp/g7-dual-instance/eks/paired/disaggregated-evidence/summary.csv", "measured_rate_rows": 7, "qualified_rates": "/tmp/g7-dual-instance/eks/paired/disaggregated-evidence/qualified-rates.json"}
```

5. 比較用の CSV を作る。

```bash
python3 5.collect.py --runs results/paired-unified-a results/paired-unified-b results/paired-disaggregated-a results/paired-disaggregated-b --output results/paired-comparison
```

`results/paired-comparison/summary.csv` を開く。判断にはこの CSV を使い、エンジンごとの証拠ディレクトリは待ち行列とキャッシュのメトリクスを説明するために残す。以下の抜粋は、列の名前や値を変えずに判断に使う列だけを選んだものである。TTFT の列は ms、TPOT の列は ms/トークン、達成率は無次元の割合、実効スループットは calls/s/GPU である。

g7.48xlarge のペアで選んだ CSV の列の例は次のとおりである (取得日: 2026-09-10)。

```
architecture,shape,offered_tasks_per_s,p90_ttft_ms_success_only,p90_tpot_ms_success_only,slo_attainment_fraction,meets_joint_slo,useful_calls_per_s_per_gpu,gpu_count
unified,A-agentic,0.1,511.7614970076829,6.4847586274731395,1.0,True,0.0875,8
unified,B-long-context,0.25,801.4657630119473,6.350490180592911,1.0,True,0.0125,8
unified,B-long-context,0.5,809.6027661012145,8.710979172667743,1.0,True,0.04287890824353537,8
unified,B-long-context,1.0,801.4742636715723,10.85338436852337,1.0,True,0.10436596616671129,8
unified,B-long-context,2.0,808.7911936447547,22.010554023561816,1.0,True,0.19429408662126824,8
unified,B-long-context,4.0,1449.4285064231321,90.03541816445981,1.0,True,0.3638653388375643,8
unified,B-long-context,8.0,30289.59470980213,133.6719082508638,0.0,False,0.0,8
disaggregated,A-agentic,0.1,528.8613779703155,8.340230227137605,1.0,True,0.08555020283906783,8
disaggregated,B-long-context,0.25,1396.7697514100826,7.362498062681041,1.0,True,0.0125,8
disaggregated,B-long-context,0.5,1316.1102530835608,8.867265760679455,1.0,True,0.04246728688993012,8
disaggregated,B-long-context,1.0,1512.3019000180325,11.236880925557047,1.0,True,0.1026702465300911,8
disaggregated,B-long-context,2.0,2384.3459234963016,18.61734031745251,0.7254901960784313,False,0.13548210519848036,8
disaggregated,B-long-context,4.0,26586.201077472906,20.40654706686516,0.05454545454545454,False,0.01241305339087797,8
disaggregated,B-long-context,8.0,96870.18467967851,23.326831242964403,0.01652892561983471,False,0.0037098924518117328,8
```

![g7.48xlarge の長文脈レイテンシの列、記録済み CSV から 2026-09-10 に描画](/images/books/aim345-disaggregated-inference-workshop/shared-040-sweep-latency-48.png)

制約を加えた g7.48xlarge ホストでの g7.24xlarge 相当の割り当てで選んだ CSV の列の例は次のとおりである (取得日: 2026-09-10)。

```
architecture,shape,offered_tasks_per_s,p90_ttft_ms_success_only,p90_tpot_ms_success_only,slo_attainment_fraction,meets_joint_slo,useful_calls_per_s_per_gpu,gpu_count
unified,A-agentic,0.1,546.3708420284092,7.555192094022298,1.0,True,0.175,4
unified,B-long-context,0.25,1097.9928289307281,6.702578604203083,1.0,True,0.025,4
unified,B-long-context,0.5,1101.292107174139,10.92319541574255,1.0,True,0.08471814557003494,4
unified,B-long-context,1.0,1142.4448273296016,20.0151617136583,1.0,True,0.20306481544097899,4
unified,B-long-context,2.0,1468.1620907929034,62.38995766239788,1.0,True,0.35860889234303406,4
unified,B-long-context,4.0,22154.27292157547,126.52249387465417,0.0,False,0.0,4
unified,B-long-context,8.0,91537.4516678729,136.15826132944693,0.0,False,0.0,4
disaggregated,A-agentic,0.1,462.42919203359634,10.730423270176876,1.0,True,0.1582248227737101,4
disaggregated,B-long-context,0.25,1880.1187834116524,7.868467952928269,1.0,True,0.025,4
disaggregated,B-long-context,0.5,2144.86659399984,9.442606913930208,0.8181818181818182,False,0.06801927538446453,4
disaggregated,B-long-context,1.0,2631.8041415073044,16.965596544939807,0.5769230769230769,False,0.11493033243451878,4
disaggregated,B-long-context,2.0,10647.036571146104,19.20839926255319,0.0784313725490196,False,0.02271688854429907,4
disaggregated,B-long-context,4.0,60748.36791794661,26.811827372704798,0.01818181818181818,False,0.005245004596220686,4
disaggregated,B-long-context,8.0,167975.0959974517,24.582395780667223,0.004132231404958678,False,0.001190342985575559,4
```

![g7.24xlarge 相当の長文脈レイテンシの列、記録済み CSV から 2026-09-10 に描画](/images/books/aim345-disaggregated-inference-workshop/shared-040-sweep-latency-24-equivalent.png)

6. この章の 26 分目になる前に、最終的なエンドポイントの識別情報を保存する。

```bash
python3 paired.py verify --output results/paired-after
```

`results/paired-before/endpoints.json` と `results/paired-after/endpoints.json` を比較する。Pod の識別情報、ノードの配置、GPU 要求が実行全体を通して一致していることを確認する。

7. それぞれの構成が満たす最も高い長文脈の到着率をメモに記録する。

それぞれの構成の行を上から下に読んでいき、`client_valid=True` かつ `meets_joint_slo=True` でなくなった最初の行で止める。条件を満たす到着率は、その 1 つ手前の到着率である。g7.48xlarge では、統合構成は 4 tasks/s まで、分離構成は 1 task/s まで条件を満たした。g7.24xlarge 相当の割り当てでは、対応する到着率はそれぞれ 2 tasks/s と 0.25 tasks/s だった。

:::message alert
提供ウィンドウの 30 秒は、あくまでリクエストの到着を受け付ける時間である。残っているリクエストはドレインさせること。早く止めると、遅いリクエストが比較から外れてしまう。`client_valid=False` と記録された行は、どちらかを選ぶ前にやり直す必要がある。
:::

:::details スイープが早く止まる場合、またはコマンドがメッセージ付きで止まる場合
`4.ramp.py` は、計画した呼び出しの 4 分の 1 を超えて失敗した到着率の後で停止し、`Ramp stopped at configured failure fraction` と表示する。そのファイルは残しておく。その到着率は測定済みであり、目標を満たさなかったという意味だからである。その到着率が `client_valid=False` だった場合、またはスイープの途中でポートフォワードが切れていた場合は、クライアント側の経路が失敗している。フォワードを再度開き、ファシリテータに伝え、その測定を壁の特定には使わないこと。統合構成のコマンドがエラーで終了すると、分離構成のスイープは始まらない。`results/paired-disaggregated-b` がまだ存在しない場合は、単独で実行する。

```bash
python3 4.ramp.py --traffic traffic/b.json --url http://127.0.0.1:8001 --architecture disaggregated --instance-type "$LAB_INSTANCE_TYPE" --region "$LAB_REGION" --evidence-scope mechanism-validation --gpus "$LAB_GPUS" --rates 0.25 0.5 1 2 4 --duration-s 30 --output results/paired-disaggregated-b
```

`4.ramp.py` は、すでにスイープのファイルが入っている出力先を拒否し、`5.collect.py` も拒否したディレクトリの名前を表示する。このセッション中にスイープをやり直さないこと。代わりに、自分の割り当てに合う測定例の列を使って次のページを埋め、「記録済みの例」と明記する。この章の 22 分の時点でまだ分離構成のスイープが動いている場合は、その測定例で次のページを始め、スイープが終わったらここに戻る。
:::

:::details スイープが提供ウィンドウの後も続く場合
残っているリクエストを確認し、今のコマンドが完了するまで待つ。g7.24xlarge 相当の割り当てでの分離構成の 8 tasks/s の行では、提供ウィンドウが 30 秒だったにもかかわらず、ドレインを含む測定区間が 210.0234999739332 秒かかったと記録されている。この区間を説明するには `measurement_including_drain_s` と失敗・スキップされた呼び出し数を使う。完了した結果ファイルはそれぞれ保存しておき、スイープが戻ってきたら収集を続ける。
:::

:::details なぜこうなるか
別々のプールにしておくと、prefill が混んでいる間も decode は進み続けられるが、それぞれのプールの容量には限りがある。実効スループットとは、両方のレイテンシ上限を満たした呼び出し数を、ドレインを含む測定区間全体で割った値である。失敗したリクエスト、途中で切れたストリーム、タイムアウト、スキップされた依存呼び出しはどれも有効な仕事を生まず、達成率の分母に残り続ける。成功したリクエストだけのパーセンタイルは、こうした失敗を隠してしまうことがある。

g7.48xlarge の分離構成を 2 tasks/s で動かした実行は、p90 TPOT が 18.61734031745251 ms/トークンと低いにもかかわらず、p90 TTFT は 2384.3459234963016 ms、無次元の達成率は 0.7254901960784313 であり、共通の目標を満たさない。境界に近い到着率をより長いウィンドウで測り直すのはセッションの後に行う。そのコマンドは、Optional の「約束の地図を深く読む」の章の手順 4 に載せてある。
:::

## まとめ

CSV が同じモデル、ワークロード、GPU 枚数、リージョン、SLO で比較されていること、両方のスタックが開始時点の Pod の識別情報と GPU 要求を保ったままであること、そしてそれぞれの構成が満たした測定済みの最も高い長文脈到着率を記録したことを確認する。次の「どちらのスタックを残すか決める」の章に進む。
