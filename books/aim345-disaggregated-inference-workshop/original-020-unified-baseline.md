---
title: "Original: 020 統合構成のベースラインを測る"
---

## はじめに

両方のスタックは準備できている。まずキャッシュが効くエージェント的トラフィックから始め、後で追加されるハンドオフと比較できる統合構成の結果を手に入れる。

目的は、両方のエンドポイントを検証して統合構成を測定することである。やることは、両方の構成に同じトラフィックファイルを生成すること、ルータごとに別々のポートフォワードを開いたままにしておくこと、統合構成でエージェント的トラフィックと長文脈トラフィックのベースラインを測ることの 3 つである。この章は「準備」「環境確認」と合わせて 8 分の共有時間になっている。

## 手順

1. エージェント的トラフィック (shape A) を生成する。

```bash
python3 2.generate-agentic.py --output traffic/a.json
```

g7.48xlarge での生成記録の例は次のとおりである (取得日: 2026-09-10)。記録されたファイルパスはローカルの `traffic/a.json` とは異なるが、タスク数・呼び出し数・トークン数は生成されたファイルの内容を示している。

```
{"file": "/tmp/g7-dual-instance/eks/paired/traffic/a.json", "task_count": 32, "planned_call_count": 311, "initial_input_tokens_min": 1024, "initial_input_tokens_max": 1024}
```

各タスクは 5 回から 15 回の呼び出しを順番に行い、それまでの応答と模擬的なツールの出力結果を保持する。隣り合うプロンプトは 50 パーセントから 90 パーセントのプレフィックス重複を目標にしているが、実際にどれだけ再利用されたかはサーバー側のキャッシュ済みトークン数が教えてくれる。

2. 長文脈トラフィック (shape B) を生成する。

```bash
python3 3.generate-long-context.py --output traffic/b.json
```

g7.48xlarge での生成記録の例は次のとおりである (取得日: 2026-09-10)。

```
{"file": "/tmp/g7-dual-instance/eks/paired/traffic/b.json", "task_count": 128, "planned_call_count": 128, "initial_input_tokens_min": 8192, "initial_input_tokens_max": 8192}
```

shape B は呼び出しごとに 8192 トークンの入力を送り、ウォームアップと測定でプレフィックスを変える。どちらの shape も呼び出しごとに 256 トークンの出力を要求する。

3. 新しい CloudShell タブで、統合構成用のポートフォワードを開く。

**Actions → New tab** を使い、「準備」の章で行ったディレクトリ移動・仮想環境の有効化・環境変数エクスポートの手順を繰り返す。このフォワードはそのタブで動かし続ける。

```bash
kubectl --context "$LAB_CONTEXT" -n "$LAB_UNIFIED_NAMESPACE" port-forward service/router 8000:8000
```

次に示す記録は g7.48xlarge でローカル TCP ポート 18000 を使った例であり、自分のコマンドではローカル TCP ポート 8000 を使う。コマンドタブに戻る前に、リスンしているポートを確認する。

```
Forwarding from <ip-address>:18000 -> 8000
Forwarding from [<loopback-ip>]:18000 -> 8000
Handling connection for 18000
```

4. 同じ環境を読み込んだ別の CloudShell タブで、分離構成用のポートフォワードを開く。

```bash
kubectl --context "$LAB_CONTEXT" -n "$LAB_DISAGG_NAMESPACE" port-forward service/router 8001:8000
```

このタブはローカル TCP ポート 8001 で動かし続け、すべての Python コマンドはコマンドタブに戻って実行する。

5. 両方のエンドポイントを検証し、開始時点の識別情報を保存する。

```bash
python3 paired.py verify --output results/paired-before
```

g7.48xlarge のエンドポイントレポートでは、次のフィールドが得られる。HTTP ステータスコード 200、1 構成あたり GPU 8 枚である (取得日: 2026-09-10)。

```
[
  {
    "http_status": 200,
    "gpus": 8,
    "nodes": [
      "<eks-node-a>"
    ]
  },
  {
    "http_status": 200,
    "gpus": 8,
    "nodes": [
      "<eks-node-b>"
    ]
  }
]
```

g7.24xlarge 相当の割り当てでは、各エンドポイントが 1 構成あたり GPU 4 枚を報告する。Pod の識別情報を含む完全なレポートは、最終的な比較のために保存しておく。

6. 統合構成のエージェント的ベースラインを測る。

```bash
python3 4.ramp.py --traffic traffic/a.json --url http://127.0.0.1:8000 --architecture unified --instance-type "$LAB_INSTANCE_TYPE" --region "$LAB_REGION" --evidence-scope mechanism-validation --gpus "$LAB_GPUS" --rates 0.1 --duration-s 30 --output results/paired-unified-a
```

g7.48xlarge で shape A を 0.1 tasks/s、30 秒の提供ウィンドウで測った要約フィールドの例は次のとおりである (取得日: 2026-09-10)。レイテンシは TTFT が ms、TPOT が ms/トークンで、達成率は無次元の割合である。

```
{
  "offered_tasks_per_s": 0.1,
  "planned_calls": 21,
  "completed_calls": 21,
  "failed_or_skipped_calls": 0,
  "p90_ttft_ms_success_only": 511.7614970076829,
  "p90_tpot_ms_success_only": 6.4847586274731395,
  "slo_attainment_fraction": 1.0,
  "meets_joint_slo": true,
  "useful_calls_per_s_per_gpu": 0.0875,
  "gpu_count": 8,
  "client_valid": true
}
```

自分の実行結果の TTFT、TPOT、キャッシュ済みトークン数、実際の calls/s、有効な calls/s/GPU を記録する。有効な呼び出しとは両方のレイテンシ上限を満たした呼び出しのことである。収集ツールは失敗した呼び出しとスキップされた呼び出しを、達成率の分母に含めたままにする。

7. 完了した統合構成のベースラインを収集する。

```bash
python3 5.collect.py --config config.unified.json --runs results/paired-unified-a results/paired-unified-b --output results/paired-unified-evidence
```

g7.48xlarge の完了した shape A の行から選んだ列の例は次のとおりである (取得日: 2026-09-10)。TTFT は ms、TPOT は ms/トークン、キャッシュの再利用はトークン数、スループットは calls/s または calls/s/GPU で表す。

```
architecture,shape,offered_tasks_per_s,p90_ttft_ms_success_only,p90_tpot_ms_success_only,cached_tokens_sum_reported,actual_sent_calls_per_s,useful_calls_per_s_per_gpu,gpu_count
unified,A-agentic,0.1,511.7614970076829,6.4847586274731395,39950,0.7,0.0875,8
```

収集ツールは完了した測定ファイルを読む。この時点で CSV には shape A が含まれる。両方のエンドポイントのために生成したトラフィックは変更しないまま保つ。

8. 統合構成の長文脈ベースラインを 0.1 tasks/s で測る。

```bash
python3 4.ramp.py --traffic traffic/b.json --url http://127.0.0.1:8000 --architecture unified --instance-type "$LAB_INSTANCE_TYPE" --region "$LAB_REGION" --evidence-scope mechanism-validation --gpus "$LAB_GPUS" --rates 0.1 --duration-s 30 --output results/paired-unified-b-lowrate
```

`results/paired-unified-b-lowrate/0.1-measured.json` から TTFT、TPOT、達成率を記録する。キャッシュ済みトークン数を shape A と比較する。整合させた長文脈の比較は「負荷を上げる」の章で 0.25 tasks/s から始まり、結果は別に保存する。

:::message alert
次の実行を始める前に、コマンドがリクエストのドレインを終えるまで待つこと。両方のルータのポートフォワードは動かし続ける。
:::

:::details ポートフォワードが使用中だと言われる場合
2026-09-10 の g7.48xlarge の記録では、ローカル TCP ポート 8000 で次のような衝突が報告されている。

```
Unable to listen on port 8000: Listeners failed to create with the following errors: [unable to create listener: Error listen tcp4 <ip-address>:8000: bind: address already in use unable to create listener: Error listen tcp6 [<loopback-ip>]:8000: bind: address already in use]
error: unable to listen on any of the requested ports: [{8000 8000}]
```

このセッションで開いた古いポートフォワードが別の CloudShell タブに残っていないか確認する。**Ctrl+C** でそのフォワードを止め、上のフォワードを再実行する。文書化されたローカル TCP ポート 8000 と 8001 を、ベンチマークの URL と揃えたままにする。
:::

:::details なぜこうなるか
選ばれた統合エンジンが既にプレフィックスを持っていれば、プレフィックスキャッシュが実効的な prefill の作業量を減らす。ラウンドロビンのルーティングはリクエストを 2 つのエンジンに振り分けるので、入力の重複と実際に観測されるキャッシュヒットは別の測定値になる。実効的なプロンプトが長くなると prefill の作業量が増え、共有している GPU 上で decode を遅らせることがある。チャンク化された prefill は、1 回あたりの中断の長さを減らす。
:::

## まとめ

両方のエンドポイントが応答し、開始時点の Pod の識別情報が保存されていること、統合構成の shape A と shape B のベースライン結果に共通 SLO・レイテンシ・キャッシュの観測が記録されていること、そして収集済みの shape A の行に有効な calls/s/GPU が含まれていることを確認する。次の「分離構成とハンドオフを確認する」の章に進む。
