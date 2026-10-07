---
title: "Modified: 030 分離構成とハンドオフを確認する"
---

## はじめに

統合構成のベースラインは手に入った。次は稼働中の分離構成が KV の状態をどのように受け渡ししているかを確認し、同じエージェント的トラフィックを送る。

目的は、ライブの転送セレクタ、パッケージのバージョン、完了したリクエストの前後のカウンタ記録を読むことである。やることは、設定された NIXL と LIBFABRIC の経路を検証すること、エンジンログと配置ごとの EFA の証拠を読むこと、同じトラフィックと GPU 予算を分離エンドポイントに通して測定することの 3 つである。この章には 8 分が割り当てられている。

## 手順

1. ストリーミングされたリクエストの前後で、ライブの転送経路を検証する。

```bash
python3 7.verify-transport.py --config config.disaggregated.json --url http://127.0.0.1:8001 --output results/paired-transport.json
```

g7.48xlarge の検証ツールの JSON から得られるフィールドの例は次のとおりである (取得日: 2026-09-10)。

```
{
  "instance_type": "g7.48xlarge",
  "engine_profile": "mainline",
  "response": {
    "ok": true,
    "input_tokens": 1024,
    "output_tokens": 4,
    "ttft_ms": 6456.251724041067,
    "tpot_ms": 4.987449307615559,
    "latency_ms": 6471.98179201223,
    "first_chunk_tokens": 1,
    "cached_tokens": 0,
    "text": " The provided text appears"
  },
  "status": "VALIDATED",
  "placement": "packed",
  "cross_node_efa_status": "UNVALIDATED: co-located prefill/decode"
}
```

保存した `results/paired-transport.json` の中の `launches` を読む。両方のプロセスが `--disaggregation-transfer-backend nixl`、`SGLANG_DISAGGREGATION_NIXL_BACKEND=LIBFABRIC`、`FI_PROVIDER=efa` を持っていることが条件である。本線のバージョンは SGLang が `0.5.12.post1`、両方の NIXL ディストリビューションが `1.1.0` である。

g7.48xlarge の起動記録には、両方のロールについて次の環境フィールドが含まれている (取得日: 2026-09-10)。`CUDA_VISIBLE_DEVICES` は GPU デバイスのインデックスを並べたものである。

```
[
  {
    "role": "prefill",
    "env": {
      "FI_PROVIDER": "efa",
      "SGLANG_DISAGGREGATION_NIXL_BACKEND": "LIBFABRIC",
      "CUDA_VISIBLE_DEVICES": "0,1,2,3"
    }
  },
  {
    "role": "decode",
    "env": {
      "FI_PROVIDER": "efa",
      "SGLANG_DISAGGREGATION_NIXL_BACKEND": "LIBFABRIC",
      "CUDA_VISIBLE_DEVICES": "4,5,6,7"
    }
  }
]
```

記録された両方の引数リストには、次の隣接するセレクタと値の組も含まれている。

```
"--disaggregation-transfer-backend",
"nixl"
```

2. 選択されたバックエンドについて、エンジンログを読む。

```bash
kubectl --context "$LAB_CONTEXT" -n "$LAB_DISAGG_NAMESPACE" logs deployment/engines -c engine
```

g7.48xlarge では LIBFABRIC のインスタンス化を示す次の行が見える (取得日: 2026-09-10)。

```
2026-09-10 10:44:00 NIXL INFO    _api.py:369 Backend LIBFABRIC was instantiated
2026-09-10 10:44:00 NIXL INFO    _api.py:369 Backend LIBFABRIC was instantiated
```

3. 保存した転送 JSON の中の `before_bytes`、`after_bytes`、`delta_bytes` を調べる。

キャプチャした g7.48xlarge のまとめて配置された実行では、次のようなカウンタ記録になる (取得日: 2026-09-10)。

```
{
  "before_bytes": {
    "engines-7bbbf4879-cnrgm": {}
  },
  "after_bytes": {
    "engines-7bbbf4879-cnrgm": {}
  },
  "delta_bytes": {
    "engines-7bbbf4879-cnrgm": {}
  },
  "placement": "packed",
  "cross_node_efa_status": "UNVALIDATED: co-located prefill/decode"
}
```

g7.24xlarge 相当の割り当てでも、空のカウンタマップが記録された。空のマップはバイト測定が存在しないことを意味する。これは測定不可として記録し、検証ツールが示すローカル配置を一緒に残す。

4. 分離構成のエージェント的ベースラインを測る。

```bash
python3 4.ramp.py --traffic traffic/a.json --url http://127.0.0.1:8001 --architecture disaggregated --instance-type "$LAB_INSTANCE_TYPE" --region "$LAB_REGION" --evidence-scope mechanism-validation --gpus "$LAB_GPUS" --rates 0.1 --duration-s 30 --output results/paired-disaggregated-a
```

g7.48xlarge で shape A を 0.1 tasks/s、30 秒の提供ウィンドウで測った要約フィールドの例は次のとおりである (取得日: 2026-09-10)。TTFT は ms、TPOT は ms/トークン、達成率は無次元である。

```
{
  "offered_tasks_per_s": 0.1,
  "planned_calls": 21,
  "completed_calls": 21,
  "failed_or_skipped_calls": 0,
  "p90_ttft_ms_success_only": 528.8613779703155,
  "p90_tpot_ms_success_only": 8.340230227137605,
  "slo_attainment_fraction": 1.0,
  "meets_joint_slo": true,
  "useful_calls_per_s_per_gpu": 0.08555020283906783,
  "gpu_count": 8,
  "client_valid": true
}
```

5. 分離構成と統合構成の p90 TTFT の差を ms で記録する。

ワークロード、キャッシュ済みトークンの観測、GPU 割り当てをその差と一緒に残す。g7.48xlarge の例では、分離構成の 528.8613779703155 ms と統合構成の 511.7614970076829 ms を比較する。

:::message
今回のペア配置では、prefill と decode はノード B を共有している。ローカルでのハンドオフが成功しても EFA のカウンタは変化しないことがあり、記録された空のマップは「0 バイトだった」という測定結果ではない。別ノード間の転送を確認するには、完了したリクエスト、プロバイダ側の証拠、正の RDMA バイト差分が必要である。
:::

:::details Mooncake や UCX が選ばれてリクエストが失敗する場合
`results/paired-transport.json` の `launches` と `versions` のセクション、続けて上のコマンドのエンジンログを読む。CLI のセレクタを省略すると Mooncake が選ばれ、環境変数のセレクタなしで `nixl` を指定すると UCX が選ばれる。両方のプロセスをこのページに必要なセレクタと比較する。検証をやり直す前に用意済みの設定に戻し、失敗したリクエストとログをセッションガイド用に残す。
:::

:::details 最初のリクエストの TTFT が高い場合
転送のスモークテストの結果はウォームアップ済みのベンチマークとは分けて扱う。キャプチャした g7.48xlarge の検証リクエストの TTFT は 6456.251724041067 ms だったのに対し、後の shape A ベンチマークの p90 TTFT は 528.8613779703155 ms だった。`4.ramp.py` から得た測定ファイルを比較していることを確認する。このコマンドは測定の前にウォームアップを実行する。カーネルのウォームアップ、実行、スケジューリングのすべてが最初のリクエストに影響する可能性がある。
:::

:::details なぜこうなるか
分離は prefill と decode の計算を分けるとともに、状態の転送とリクエストの協調が追加される。この圧縮された attention を使うモデルでは、論理的な KV のバイト数は入力トークン数 × レイヤー数 × (KV の潜在次元 + 回転キーの次元) × BF16 1 要素あたり 2 バイトで求まる。別ノード間の調査では、テンソル並列の複製と転送レイアウトを含めたこのモデル全体の量を、[参考実装の GPU バッファ測定](https://github.com/awslabs/awsome-distributed-ai/blob/riv2026/aim345-content/examples/use-cases/disaggregated-inference-prefill-decode/README.md#attribute-the-handoff-cost-and-prepare-connections) の帯域幅 (GiB/s) と比較する。理想的な転送時間が小さいままなら、残りは実行・スケジューリング・協調の調査に残る。

固定版のコネクタには、事前接続 (eager-bootstrap) を切り替えるオプションはない。独立した測定ツールの事前接続呼び出しと起動時のリクエストのプライミングは別の操作であり、プライミングはカーネルのウォームアップも兼ねる。
:::

## まとめ

転送の記録に、完了したレスポンス、両方のセレクタ、バージョン、バックエンドのログ、配置ごとのカウンタの範囲が含まれていることを確認する。両方のエージェント的な結果が同じトラフィックファイル、GPU 枚数、SLO を使っていること、そして TTFT の差を記録したことを確認する。次の「負荷を上げる」の章に進む。
