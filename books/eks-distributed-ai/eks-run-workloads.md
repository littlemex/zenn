---
title: "Basic07 - 軽量 vLLM で推論を動かす"
free: true
---

本章では、Basic03-06 で用意した Karpenter・アクセラレータプールの上に、vLLM の OpenAI 互換サーバーをデプロイし、軽量な言語モデルで推論を動かします。高額な Capacity Block は使わず、g5 / g6e / g6 系の on-demand GPU で誰でも試せる構成です。Basic02 の CPU 分散学習に続く「GPU を使った推論の最小体験」として位置づけます。

:::message
本章は Capacity Block 不要です。Basic04 で定義した `gpu-ddp` プール（g6.2xlarge / g5.2xlarge などの小型 GPU、spot + on-demand フォールバック）をそのまま使い、GPU 1 枚で小さなモデルを動かします。「まず 1 枚の GPU で推論サーバーが立つ」ことを確認するのが目的で、マルチノードや大規模モデルは扱いません。
:::

# 解説

## 全体構成

この book で構築する基盤のうち、本章は Karpenter が on-demand で起動する GPU ノード 1 台に、推論サーバー（vLLM）の Pod を載せる構成です。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

図のアクセラレータプールのうち、EFA も Capacity Block も使わない最小の GPU 1 枚構成です。推論は単一ノードで完結するため、マルチノード通信（Basic06 の EFA）は不要です。

## これは何をするものか

[vLLM](https://github.com/vllm-project/vllm) は、PagedAttention による高スループットな LLM 推論エンジンです。OpenAI 互換の HTTP API（`/v1/models`、`/v1/chat/completions` など）を提供するため、既存の OpenAI クライアントからそのまま呼び出せます。

本章では、vLLM の公式イメージ `vllm/vllm-openai` を Kubernetes の `Deployment` として GPU ノードに載せ、軽量モデル `Qwen/Qwen2.5-0.5B-Instruct`（ゲートなし・小型で 1 枚の GPU に収まる）をサービングします。Helm チャート [`charts/experiments`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments) の `gpuServingVllm` ワークロードが GPU 向けの雛形で、`nodeSelector` でアクセラレータプールに載せ、`nvidia.com/gpu: 1` をリクエストします。ゲート付きモデルを使う場合は、事前に `hf-token` という Secret を作っておくと、コンテナが `HF_TOKEN` 環境変数として自動的に読み込みます（未作成でも Pod は起動し、ゲートなしモデルでは何も参照されません）。

推論は 1 ノードで完結するため、EFA も Capacity Block も要りません。Basic04 で `accelerator_pools` に GPU プールを定義してあれば、そのプールに Pod を投げるだけで Karpenter が GPU ノードを 1 台起動し、その上で vLLM が立ち上がります。

## 全体の中での位置付け

本章は「GPU を使った最小の動作確認」です。Basic02 では CPU で分散学習（DDP）を動かしました。本章はその GPU 版・推論版にあたり、「Karpenter が GPU ノードを on-demand で起動し、その上で GPU ワークロード（推論）が動く」ことを、Capacity Block の予約なしで確認できます。ここまでで「CPU 学習（Basic02）」「GPU 推論（本章）」の両方を最小コストで体験したことになり、Basic05 で扱った Capacity Block を使えば、これを大規模モデル・マルチノードに広げられる、という全体像が掴めます。

## 注意

**GPU の容量が取れないことがあります。** GPU インスタンスは人気が高く、特定の AZ・特定のタイプで一時的に `InsufficientInstanceCapacity` になり、起動に失敗し続けることがあります。また `accelerator_pools` の `instance_types` / `zones` の指定が狭すぎると、Karpenter が候補ゼロと判断して NodeClaim を作らないこともあります。いずれの場合も対策は Basic04 と同じで、`gpu-ddp` プールで `zones` を変えて別の AZ を試すか、`instance_types` に候補を複数並べて Karpenter に選択肢を与えます。Basic04 の `gpu-ddp` が最初から 4 タイプを並べているのはこのためです。実際に単一 AZ・単一タイプに固定していて容量が取れなかったケースでも、複数 AZ・複数タイプを許可したら確保できました。

**GPU Operator の初期化を待ちます。** Karpenter が GPU ノードを起動しても、NVIDIA GPU Operator が展開する device plugin が `nvidia.com/gpu` を advertise するまで数分かかります。それまで Pod は `Pending` のままですが、これは異常ではありません。

**CPU リクエストはノードサイズに合わせて調整します。** `gpuServingVllm` の CPU リクエスト既定値は `2` で、Qwen2.5-0.5B のような小型モデルであれば g6.2xlarge / g5.2xlarge（8 vCPU）のような小さい GPU ノードのプールでもそのまま載ります。大きいモデルをサービングする場合や、g6e.12xlarge（48 vCPU）のような大きいノードでより多くの CPU を割り当てたい場合は、`--set gpuServingVllm.cpu=<値>` で明示的に上げます。プールの割り当て可能 CPU を超える値を指定すると、Karpenter が `no instance type has enough resources` を出して Pod が永久に `Pending` になる点には注意します。

**GPU ノードは使い終わったら必ず回収させます。** `gpu-ddp` は g6.2xlarge / g5.2xlarge のような小型タイプを spot 優先で選ぶため 1 時間あたり 1 USD 未満で収まりますが、放置すれば積み上がります。より大きな GPU プール（g6e.12xlarge なら on-demand で 1 時間あたり 10 USD 前後）を使う場合はなおさらです。動作確認が終わったら必ず後述の手順5で Deployment を削除し、GPU ノードを Karpenter に回収させてください。

# ワークショップ実施

## 1. 作業用 namespace を用意する

Basic01 で作った作業用 namespace を使います。ターミナルを開き直した場合に備えて、ここで冪等に用意し直しておきます（すでに存在していてもエラーになりません）。

```bash
export NAMESPACE=distai
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
```

## 2. vLLM の Deployment を投入する

この章では vLLM の公式イメージ `vllm/vllm-openai` をそのまま使うため、自前でのイメージビルドは不要です（`charts/experiments` の `gpuServingVllm.image` の既定値が公式イメージを指しています）。チャートをレンダリングして適用します。`nodeRole` には GPU プール名を渡します。プール名は `terraform.tfvars` の `accelerator_pools` の map キーで、Karpenter がそれを `node-role=<プール名>` としてノードに付けるため、この値でプールを指定できます。以下は Basic04 で定義した `gpu-ddp` をそのまま使う例です。プール名を変えている場合は以降も読み替えてください。

```bash
cd infra/eks
MODEL=Qwen/Qwen2.5-0.5B-Instruct     # ゲートなし・小型
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true \
    --set gpuServingVllm.model="$MODEL" \
    --set gpuServingVllm.nodeRole=gpu-ddp \
    | kubectl apply -f -
```

CPU リクエストの既定値は `2` なので g6.2xlarge / g5.2xlarge（8 vCPU）のような小さい GPU ノードのプールでもそのまま載りますが、メモリのリクエスト既定値は `48Gi` で、`gpu-ddp` が選びうる g6.xlarge / g5.xlarge（4 vCPU / 16 GiB / GPU 1 枚）のような小さいノードには収まりません。この場合はメモリを下げますが、**下げる値は `/dev/shm` と合わせて決める必要があります**。

`gpuServingVllm` の `memory` はコンテナの requests・limits 両方に使われ、加えて `/dev/shm`（`shmSize`、既定 `8Gi`）が `emptyDir: {medium: Memory}` として**同じコンテナのメモリ上限にカウントされます**。つまり実際の要求は `memory + shmSize` です。16 GiB のノードの allocatable は約 12.4 GiB なので、`memory=12Gi` だけを指定して `shmSize` を既定の 8Gi に残すと合計 20Gi となり、**どのノードにも載りません**。

:::message alert
このとき Pod は単に Pending になるのではなく、**Karpenter がノードを起こし続けます**。スケジューラが「メモリ不足」と報告するため Karpenter は「もっと大きいノードを足せば載る」と解釈しますが、`instance_types` に並べたどのタイプでも足りないので、起動と失敗を繰り返します。実際にこの設定で放置したところ **spot ノードが 11 台まで増えました**（本来必要なのは 1 台です）。spot でも課金は発生するため、Pending の Pod を見つけたら NodeClaim の数を必ず確認してください。

```bash
kubectl get nodeclaims -l karpenter.sh/nodepool=gpu-ddp
```

意図した台数を超えていれば、原因の Pod を削除してから NodeClaim を消します（Pod を残したまま NodeClaim を消すと、また起動し直します）。

```bash
kubectl delete deploy gpu-vllm -n "$NAMESPACE"
kubectl delete nodeclaims -l karpenter.sh/nodepool=gpu-ddp
```
:::

小さいノードに載せるには、両方を下げて合計を allocatable 未満に収めます。実機では次の設定で g6.xlarge に載せて動作を確認しました。

```bash
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true \
    --set gpuServingVllm.model="$MODEL" \
    --set gpuServingVllm.nodeRole=gpu-ddp \
    --set gpuServingVllm.memory=8Gi \
    --set gpuServingVllm.shmSize=2Gi \
    | kubectl apply -f -
```

`8Gi + 2Gi = 10Gi` で、16 GiB ノードの allocatable（約 12.4 GiB）に収まります。ノードの実際の値は次のコマンドで確認できます。プールが複数のインスタンスタイプを並べている場合は、**最も小さいタイプに収まる値**を選んでください。

```bash
kubectl get nodes -l node-role=gpu-ddp \
  -o custom-columns='NAME:.metadata.name,TYPE:.metadata.labels.node\.kubernetes\.io/instance-type,MEM:.status.allocatable.memory'
```

:::message
既定のイメージタグは `vllm/vllm-openai:v0.6.3.post1` に固定済みです。`:latest` は使っていません。新しいバージョンの機能やモデル対応が必要な場合は `--set gpuServingVllm.image=vllm/vllm-openai:<タグ>` で明示的に指定します。
:::

## 3. GPU ノードの起動と Pod の Ready を待つ

```bash
kubectl get nodeclaims -w        # gpu プールの NodeClaim が起動 → Ready
kubectl -n "$NAMESPACE" rollout status deploy/gpu-vllm --timeout=15m
```

GPU ノードの起動（数分）、vLLM イメージの pull、モデルのロードを経て、Pod が `1/1 Running` になります。

## 4. OpenAI 互換 API を叩く

port-forward してモデル一覧と推論を確認します。

```bash
kubectl -n "$NAMESPACE" port-forward svc/gpu-vllm 8000:8000 &
sleep 2   # フォワード確立を待つ（省くと connection refused になることがあります）
curl -s localhost:8000/v1/models | python3 -m json.tool
```

`/v1/models` にサービング中のモデルが表示されます（実機出力です）。

```json
{
  "object": "list",
  "data": [
    {
      "id": "Qwen/Qwen2.5-0.5B-Instruct",
      "object": "model",
      "max_model_len": 4096,
      "owned_by": "vllm"
    }
  ]
}
```

続いて chat completion を叩きます。

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"Hello"}],"max_tokens":80}' \
  | python3 -m json.tool
```

応答本文と `usage`（`prompt_tokens` / `completion_tokens` / `total_tokens`）が返れば、`gpu-ddp` プールの 1 枚の GPU で vLLM の推論が動いていることが確認できます（実測で prompt 36 / completion 31 / total 67 トークンの応答を確認しました）。なお `/v1/models` に出る `max_model_len` は Qwen2.5-0.5B 本来の 32K ではなく、チャートが軽量検証向けに `--max-model-len` を控えめに指定した値です。

port-forward はバックグラウンド（`&`）で起動したので、確認が終わったら止めます。他にバックグラウンドジョブが無いか `jobs` で確認したうえで、対応する番号を `kill %<n>`（1 個だけなら `kill %1`）に指定します。

## 5. 後片付け

推論サーバーを止めれば、GPU ノードは `consolidateAfter`（本構成の NodePool では 5 分に設定）のアイドル後に Karpenter が自動回収します。on-demand なので使った分だけの課金です。Deployment・Service の名前は `--set` の値に関わらず変わらないため、`gpuServingVllm.enabled=true` と `nodeRole` だけを付けてレンダリングした結果を `kubectl delete` に渡せば、投入時に指定した `model` や `memory` などの値を覚えておく必要なく、チャートが出力した全リソースを取りこぼしなく削除できます。

```bash
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true --set gpuServingVllm.nodeRole=gpu-ddp \
    | kubectl delete -f -
kubectl get nodeclaims -w        # GPU ノードが消えるのを確認
```

# まとめ

本章では、Capacity Block を使わずに on-demand の GPU ノード（g5 / g6e 系）へ vLLM の OpenAI 互換サーバーをデプロイし、軽量モデルで推論が動くことを確認しました。単一 GPU で完結するため EFA は不要で、Basic04 で定義した GPU プールに Pod を投げるだけで Karpenter がノードを起動します。on-demand の容量が取れないときは AZ と instance type を複数許可する、メモリなどのリクエストをノードサイズに合わせる、動作確認が終わったら忘れずに Deployment を削除して GPU ノードを回収させる、という 3 点が実運用の勘所です。この上で大規模モデルやマルチノードの推論・学習に進む場合は、Basic05 の Capacity Block で GPU を確保します。

# 参考資料

- [vLLM](https://github.com/vllm-project/vllm)
- [vLLM OpenAI-Compatible Server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct)
- [対象ワークロード gpuServingVllm（charts/experiments）](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/gpu-serving-vllm.yaml)
