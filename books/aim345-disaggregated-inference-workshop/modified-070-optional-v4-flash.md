---
title: "Modified: 070 Optional: g7.48xlarge での DeepSeek-V4-Flash"
---

## はじめに

この演習に入る前に割り当てを確認する。V4-Flash はフルの g7.48xlarge ペアで、ノードあたり GPU 8 枚を必要とする。この演習は 60 分の本線には収まらない。

目的は、別途用意された V4-Flash の設定とその別ノード間ハンドオフを検証することである。やることは、この Optional なモデルを使う前にノードあたりの GPU メモリを確認すること、Optional なパッケージのプロファイルと別ノード間 RDMA のカウンタを検証すること、Optional の結果を本線のモデルとは分けて保つことの 3 つである。この演習は 60 分のセッションの外で行う。

:::message alert
以下の手順はすべて `config.v4.json` を読み、Optional なデプロイがすでに `Available` であることを前提にしている。このファイルもデプロイも、このワークショップのどこでも作られることはないので、手順 2 は両方が揃うまで `FileNotFoundError` で失敗する。まず「この演習の前に」の節に書かれているとおりに両方を用意すること。このプロファイルは各 g7.48xlarge ノードの GPU 8 枚と両方の EFA デバイスをすべて使うので、本線のペア配置と同時には動かせない。
:::

## この演習の前に

稼働中の本線のペアを保存して片付けた後に、次の 4 つを行う。Workshop Studio のアカウントにはコンテナのビルドホストがないため、エンジンのビルドと公開はアカウントの外でファシリテータが行う。

1. 到達可能な `v4-optional` のエンジンダイジェストを用意する。`Dockerfile.v4` を Docker のあるホストでビルドし、公開し、プッシュしたダイジェストを読み取るのはファシリテータの作業である。`FACILITATOR_RUNBOOK.md` の「Prepare the optional DeepSeek-V4-Flash exercise」の節に従う。この節は必要な SGLang、NIXL、EFA インストーラのバージョンを固定している。`verify_image.py` は資格のある 2 つのバージョンの組だけを受け付け、デプロイするイメージについて `v4-optional` を報告しなければならない。参考実装の README に記録されているエンジンのマニフェストダイジェストは著者自身のビルドのものであり、コンテナのビルドはビット単位で再現可能ではないため、自分のビルドは別のダイジェストになる。記録済みのものではなく、自分がプッシュしたダイジェストを使う。

2. 設定を作り、残っているすべての `REPLACE_WITH_` の値を置き換える。

```bash
cp config.v4.example.json config.v4.json
```

`deployment.py` は残っている `REPLACE_` の値をすべて拒否するので、`context`、`nodes` の両エントリ、`image`、`router_image` の 4 つはすべて必須である。Optional なモデルと `engine_profile` はそのまま残す。この例ではエンジンあたり vCPU 188、メモリ 672 GiB を要求している。別の場所にデプロイする前に、実際のノードの割り当て可能量を確認すること。

3. 60 分のセッションの外で、重みの 148.7 GiB とダウンロード・コンパイル用の空き容量を、各ノードの `model_cache_host_path` に用意する。既存の固定された本線のスナップショットとコンパイルキャッシュは置き換えずに保持する。

4. Optional なペアをデプロイし、`prefill-0`、`decode-0`、`router` を待つ。

```bash
python3 deployment.py disaggregated --config config.v4.json
```

`deployment.py` は、このプロファイルについて 2 番目の shape、まとめて配置する構成、GPU/EFA の部分的な要求を拒否する。下の手順 2 は同じ要求を `--render` で確認用に再描画するだけで、デプロイは行わない。

## 手順

1. 割り当てられた Optional なノードの GPU と EFA の割り当てを読む。

```bash
kubectl --context "$(python3 -c 'import json; print(json.load(open("config.v4.json"))["context"])')" get nodes $(python3 -c 'import json; print(" ".join(json.load(open("config.v4.json"))["nodes"]))') -o 'custom-columns=NODE:.metadata.name,TYPE:.metadata.labels.node\.kubernetes\.io/instance-type,GPUS:.status.allocatable.nvidia\.com/gpu,EFAS:.status.allocatable.vpc\.amazonaws\.com/efa'
```

g7.48xlarge のペアでは、次のデバイス数が得られる (取得日: 2026-09-10)。

```
NODE                                         TYPE          GPUS   EFAS
<eks-node-a>   g7.48xlarge   8      2
<eks-node-b>   g7.48xlarge   8      2
```

![V4-Flash の重み 148.7 GiB は g7.24xlarge 相当のメモリ 127.4 GiB を超えるが、g7.48xlarge は 254.9 GiB を提供する](/images/books/aim345-disaggregated-inference-workshop/diagram-v4-memory.png)

モデルの重みは 148.7 GiB を占める。g7.24xlarge 相当の割り当てでは、ノードあたり GPU 4 枚で合計 127.4 GiB しか提供されず、重みだけで 21.2 GiB 不足する。g7.48xlarge ノードは GPU 8 枚で 254.9 GiB を提供し、KV キャッシュとランタイムの割り当てに 106.2 GiB を残す。prefill と decode のそれぞれのエンジンは、自分専用の重みを必要とする。

2. 用意済みの Optional な要求を描画する。

```bash
python3 deployment.py disaggregated --config config.v4.json --render > results/v4-rendered.json
```

`results/v4-rendered.json` を調べ、フルの g7.48xlarge の GPU/EFA 割り当てと、別々になった prefill/decode のノードを確認する。この設定は SGLang バージョン `0.5.19`、両方の NIXL ディストリビューション `1.4.1` を使っている。

3. Optional なモデル、割り当て、エンドポイントの設定を読み込む。

```bash
export LAB_V4_MODEL="$(python3 -c 'import json; print(json.load(open("config.v4.json"))["model_id"])')"
export LAB_V4_REVISION="$(python3 -c 'import json; print(json.load(open("config.v4.json"))["model_revision"])')"
export LAB_V4_GPUS="$(python3 -c 'import json; print(2 * json.load(open("config.v4.json"))["gpus_per_worker"])')"
export LAB_V4_CONTEXT="$(python3 -c 'import json; print(json.load(open("config.v4.json"))["context"])')"
export LAB_V4_NAMESPACE="$(python3 -c 'import json; print(json.load(open("config.v4.json"))["namespace"])')"
export LAB_V4_INSTANCE_TYPE="$(python3 -c 'import json; print(json.load(open("config.v4.json"))["instance_type"])')"
export LAB_V4_REGION="$(python3 -c 'import json; print(json.load(open("config.v4.json"))["context"].split(":")[3])')"
```

4. 別のターミナルで、これらの設定を読み込んだ状態で Optional なルータのポートフォワードを開く。

```bash
kubectl --context "$LAB_V4_CONTEXT" -n "$LAB_V4_NAMESPACE" port-forward service/router 8010:8000
```

このフォワードはローカル TCP ポート 8010 で動かし続け、コマンド用のターミナルに戻る。

5. Optional なエンジンと別ノード間の転送を検証する。

```bash
python3 7.verify-transport.py --config config.v4.json --url http://127.0.0.1:8010 --output results/v4-transport.json
```

g7.48xlarge の Optional な転送記録から得られるフィールドの例は次のとおりである (取得日: 2026-09-10)。カウンタの値はバイト数である。

```
{
  "engine_profile": "v4-optional",
  "status": "VALIDATED",
  "delta_bytes": {
    "decode-0-685bddcbbd-578rt": {
      "/sys/class/infiniband/rdmap83s0/ports/1/hw_counters/rdma_write_bytes": 0,
      "/sys/class/infiniband/rdmap176s0/ports/1/hw_counters/rdma_write_bytes": 0
    },
    "prefill-0-6f9cd56fc9-nmgnb": {
      "/sys/class/infiniband/rdmap176s0/ports/1/hw_counters/rdma_write_bytes": 48611328,
      "/sys/class/infiniband/rdmap83s0/ports/1/hw_counters/rdma_write_bytes": 48624128
    }
  }
}
```

ストリーミングされたリクエストが完了し、インストールされたプロファイルが `v4-optional` であることを確認する。キャプチャした g7.48xlarge の prefill のカウンタは、EFA デバイスをまたいで 48611328 バイトと 48624128 バイト、合計 97235456 バイト増加した。プロバイダと GPU 登録のログは、このカウンタと一緒に残しておく。バイトの移動だけでは、ホスト側でのステージングを除外できない。

6. V4 のトークナイザを使って Optional なエージェント的トラフィックを生成する。

```bash
python3 2.generate-agentic.py --model "$LAB_V4_MODEL" --revision "$LAB_V4_REVISION" --output traffic/v4-a.json
```

g7.48xlarge の Optional なトラフィック記録の例は次のとおりである (取得日: 2026-09-10)。記録された出力パスはローカルのパスとは異なり、タスク・呼び出し・トークンの数が生成されたファイルの内容を示す。

```
{"file": "/tmp/g7-dual-instance/eks/v4/traffic/a.json", "task_count": 32, "planned_call_count": 311, "initial_input_tokens_min": 1024, "initial_input_tokens_max": 1024}
```

7. Optional な長文脈トラフィックを生成する。

```bash
python3 3.generate-long-context.py --model "$LAB_V4_MODEL" --revision "$LAB_V4_REVISION" --output traffic/v4-b.json
```

g7.48xlarge の Optional なトラフィック記録の例は次のとおりである (取得日: 2026-09-10)。

```
{"file": "/tmp/g7-dual-instance/eks/v4/traffic/b.json", "task_count": 128, "planned_call_count": 128, "initial_input_tokens_min": 8192, "initial_input_tokens_max": 8192}
```

8. 0.1 tasks/s で Optional な shape A を測る。

```bash
python3 4.ramp.py --traffic traffic/v4-a.json --url http://127.0.0.1:8010 --architecture disaggregated --instance-type "$LAB_V4_INSTANCE_TYPE" --region "$LAB_V4_REGION" --evidence-scope mechanism-validation --gpus "$LAB_V4_GPUS" --rates 0.1 --duration-s 30 --output results/v4-a
```

g7.48xlarge でノードペアをまたいで GPU 16 枚を使った結果の例は次のとおりである (取得日: 2026-09-10)。TTFT は ms、TPOT は ms/トークン、達成率は無次元である。

```
{
  "offered_tasks_per_s": 0.1,
  "planned_calls": 21,
  "completed_calls": 21,
  "failed_or_skipped_calls": 0,
  "p90_ttft_ms_success_only": 665.327119990252,
  "p90_tpot_ms_success_only": 20.503676874453532,
  "slo_attainment_fraction": 1.0,
  "meets_joint_slo": true,
  "useful_calls_per_s_per_gpu": 0.024349240724443794,
  "gpu_count": 16,
  "client_valid": true
}
```

9. 同じ提供到着率で Optional な shape B を測る。

```bash
python3 4.ramp.py --traffic traffic/v4-b.json --url http://127.0.0.1:8010 --architecture disaggregated --instance-type "$LAB_V4_INSTANCE_TYPE" --region "$LAB_V4_REGION" --evidence-scope mechanism-validation --gpus "$LAB_V4_GPUS" --rates 0.1 --duration-s 30 --output results/v4-b
```

g7.48xlarge で同じ GPU 16 枚の割り当てを使った結果の例は次のとおりである (取得日: 2026-09-10)。

```
{
  "offered_tasks_per_s": 0.1,
  "planned_calls": 3,
  "completed_calls": 3,
  "failed_or_skipped_calls": 0,
  "p90_ttft_ms_success_only": 3095.9469376496963,
  "p90_tpot_ms_success_only": 17.404169286601245,
  "slo_attainment_fraction": 0.3333333333333333,
  "meets_joint_slo": false,
  "useful_calls_per_s_per_gpu": 0.0020833333333333333,
  "gpu_count": 16,
  "client_valid": true
}
```

この g7.48xlarge の例では、計画した 3 件すべての呼び出しが完了したが、両方の上限を満たしたのは 33.33333333333333 パーセントだけだった。shape B は 0.1 tasks/s で共通 SLO を満たさなかった。

10. すべての結果を保ったまま、提供到着率を上げる。

```bash
python3 4.ramp.py --traffic traffic/v4-b.json --url http://127.0.0.1:8010 --architecture disaggregated --instance-type "$LAB_V4_INSTANCE_TYPE" --region "$LAB_V4_REGION" --evidence-scope mechanism-validation --gpus "$LAB_V4_GPUS" --rates 0.25 0.5 1 2 --duration-s 30 --output results/v4-sweep
```

11. Optional な測定結果とエンジンの証拠を収集する。

```bash
python3 5.collect.py --config config.v4.json --runs results/v4-a results/v4-b results/v4-sweep --output results/v4-evidence
```

`results/v4-evidence/summary.csv` を読み、条件を満たした測定済みの最も高い到着率、または条件を満たすものがなかったことを記録する。g7.48xlarge で記録された分離構成のスイープでは、長文脈の到着率は 1 つも条件を満たさなかった。対応する統合構成の順次実行は 0.25 tasks/s までは条件を満たし、0.5 tasks/s で初めて外れた。この V4 の比較は、V2-Lite-Chat のワークシートとは分けて保つ。

:::message alert
別途用意されたフルの g7.48xlarge ペアと `config.v4.json` だけでこの演習を続けること。各ノードは GPU 8 枚と EFA デバイス 2 個を提供する必要があり、本線の比較はすでに保存されていること。
:::

:::details 最初のリクエストが遅い場合
コールドリクエストをウォームアップ済みのベンチマークファイルと比較する。SGLang バージョン `0.5.19` を使った、以前の g7.48xlarge での統合構成の読み込みでは、2026-09-10 に次のロードログが記録された。

```
[2026-09-10 06:38:43 TP0] Load weight begin. avail mem=30.83 GB
[2026-09-10 06:39:14 TP0] Load weight end. elapsed=30.73 s, type=DeepseekV4ForCausalLM, quant=fp8, fmt=e4m3, avail mem=10.38 GB, mem usage=20.45 GB.
[2026-09-10 06:42:15] INFO:     Application startup complete.
[2026-09-10 06:42:24] The server is fired up and ready to roll!
```

g7.48xlarge でのそのコールドリクエストの TTFT は 11795.47555299996 ms、約 11.8 秒と記録された。

```
{
  "ok": true,
  "input_tokens": 1024,
  "output_tokens": 32,
  "ttft_ms": 11795.47555299996,
  "tpot_ms": 11.12819783871131,
  "latency_ms": 12140.498474000196
}
```

SLO の比較には、ウォームアップ後の `4.ramp.py` の測定ファイルを使う。この早期のコールドスモークの観測は、別の記録として残しておく。
:::

:::details なぜこうなるか
テンソル並列はエンジンをローカルの GPU に分割するが、prefill と decode はそれぞれモデルの複製を保持する。ロールをノード間で分けると、各ノードは自分専用の重み・KV キャッシュ・ランタイムのためのメモリを必要とする。g7.48xlarge のより大きなノードあたりの割り当てはこの重みを収められるようにし、別に固定された Optional なエンジンがモデルの実装を提供する。メモリに収まることと、リクエストが完了することは、レイテンシの目標を満たすこととは別の条件である。
:::

## まとめ

自分の割り当てとパッケージのプロファイルが Optional な設定と一致していること、別ノード間の転送記録に正の RDMA バイトが含まれていることを確認する。shape A、shape B、スイープの結果に、完了した呼び出しと SLO を満たさなかった結果の両方が残っていることを確認する。これで「まとめと再現手順」の章に戻る。
