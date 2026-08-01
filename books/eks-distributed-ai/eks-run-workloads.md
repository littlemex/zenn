---
title: "Basic07 - 軽量 vLLM で推論を動かす"
free: true
---

本章では、Basic03-06 で用意した Karpenter・アクセラレータプールの上に、vLLM の OpenAI 互換サーバーをデプロイし、軽量な言語モデルで推論を動かします。高額な Capacity Block は使わず、g5 / g6e / g6 系の on-demand GPU で誰でも試せる構成です。Basic02 の CPU 分散学習に続く「GPU を使った推論の最小体験」として位置づけます。

:::message
本章は Capacity Block 不要です。Basic04 で定義した `gpu-dev` プール（g6e.12xlarge、on-demand）をそのまま使い、GPU 1 枚で小さなモデルを動かします。「まず 1 枚の GPU で推論サーバーが立つ」ことを確認するのが目的で、マルチノードや大規模モデルは扱いません。
:::

# 解説

## 全体構成

この book で構築する基盤のうち、本章は Karpenter が on-demand で起動する GPU ノード 1 台に、推論サーバー（vLLM）の Pod を載せる構成です。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

図のアクセラレータプールのうち、EFA も Capacity Block も使わない最小の GPU 1 枚構成です。推論は単一ノードで完結するため、マルチノード通信（Basic05 の EFA）は不要です。

## これは何をするものか

[vLLM](https://github.com/vllm-project/vllm) は、PagedAttention による高スループットな LLM 推論エンジンです。OpenAI 互換の HTTP API（`/v1/models`、`/v1/chat/completions` など）を提供するため、既存の OpenAI クライアントからそのまま呼び出せます。

本章では、vLLM の公式イメージ `vllm/vllm-openai` を Kubernetes の `Deployment` として GPU ノードに載せ、軽量モデル `Qwen/Qwen2.5-0.5B-Instruct`（ゲートなし・小型で 1 枚の GPU に収まる）をサービングします。Helm チャート [`charts/experiments`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments) の `gpuServingVllm` ワークロードが GPU 向けの雛形で、`nodeSelector` でアクセラレータプールに載せ、`nvidia.com/gpu: 1` をリクエストします。

推論は 1 ノードで完結するため、EFA も Capacity Block も要りません。Basic04 で `accelerator_pools` に GPU プールを定義してあれば、そのプールに Pod を投げるだけで Karpenter が GPU ノードを 1 台起動し、その上で vLLM が立ち上がります。

## 全体の中での位置付け

本章は「GPU を使った最小の動作確認」です。Basic02 では CPU で分散学習（DDP）を動かしました。本章はその GPU 版・推論版にあたり、「Karpenter が GPU ノードを on-demand で起動し、その上で GPU ワークロード（推論）が動く」ことを、Capacity Block の予約なしで確認できます。ここまでで「CPU 学習（Basic02）」「GPU 推論（本章）」の両方を最小コストで体験したことになり、Basic06 で扱った Capacity Block を使えば、これを大規模モデル・マルチノードに広げられる、という全体像が掴めます。

## 注意

**on-demand GPU は容量が取れないことがあります。** GPU インスタンスは人気が高く、特定の AZ・特定のタイプで一時的に `InsufficientInstanceCapacity` になり、Karpenter が「filtered out all instance types」を出し続けることがあります。対策は Basic04 と同じで、`accelerator_pools` の `gpu-dev` プールで `zone` を変えて別の AZ を試すか、`instance_types` に `g6e.12xlarge` / `g5.12xlarge` のように複数を並べて Karpenter に選択肢を与えます。実際に単一 AZ・単一タイプに固定していて容量が取れなかったケースでも、複数 AZ・複数タイプを許可したら確保できました。

**GPU Operator の初期化を待ちます。** Karpenter が GPU ノードを起動しても、NVIDIA GPU Operator が `nvidia.com/gpu` を advertise するまで数分かかります。それまで Pod は `Pending` のままですが、これは異常ではありません。

**小さいノードのプールでは CPU リクエストを下げます。** `gpuServingVllm` の CPU リクエスト既定値は `8` で、g6e.12xlarge（48 vCPU）のような大きいノードを想定しています。g6.2xlarge / g5.2xlarge（8 vCPU）のような小さい GPU ノードのプールに載せると、システム予約を差し引いた割り当て可能 CPU が 8 に届かず、Karpenter が `no instance type has enough resources` を出して Pod が永久に `Pending` になります。Qwen2.5-0.5B のような小型モデルは 2 vCPU でも動くため、小さいノードのプールでは後述の手順で `--set gpuServingVllm.cpu=2` を付けて明示的に下げます。

# ワークショップ実施

## 1. 作業用 namespace を用意する

Basic01 で作った作業用 namespace を使います。ターミナルを開き直した場合に備えて、ここで冪等に用意し直しておきます（すでに存在していてもエラーになりません）。

```bash
export NAMESPACE=distai
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
```

## 2. vLLM の Deployment を投入する

この章では vLLM の公式イメージ `vllm/vllm-openai` をそのまま使うため、自前でのイメージビルドは不要です（`charts/experiments` の `gpuServingVllm.image` の既定値が公式イメージを指しています）。チャートをレンダリングして適用します。`nodeRole` は GPU プール名（Basic04 で定義したもの、例 `gpu-dev`）に置き換えます。

```bash
cd infra/eks
MODEL=Qwen/Qwen2.5-0.5B-Instruct     # ゲートなし・小型
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true \
    --set gpuServingVllm.model="$MODEL" \
    --set gpuServingVllm.nodeRole=gpu-dev \
    | kubectl apply -f -
```

g6e.12xlarge のような大きいノードのプールではこれで載りますが、g6.2xlarge / g5.2xlarge（8 vCPU）のような小さい GPU ノードのプールに載せる場合は、CPU とメモリのリクエストをノードに収まる値まで下げます。実機では次の設定で g5.xlarge（4 vCPU / 16 GiB / A10G 1 枚）の spot ノードに載せて動作を確認しました。

```bash
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true \
    --set gpuServingVllm.model="$MODEL" \
    --set gpuServingVllm.nodeRole=gpu-ddp \
    --set gpuServingVllm.cpu=2 \
    --set gpuServingVllm.memory=12Gi \
    | kubectl apply -f -
```

:::message
既定のイメージタグは `vllm/vllm-openai:latest` です。バージョンによって必要な GPU メモリや挙動が変わることがあるため、再現性が要る場合は `--set gpuServingVllm.image=vllm/vllm-openai:<固定タグ>` で明示的にピン留めしておくと安全です。
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

`/v1/models` にサービング中のモデルが表示されます（実機出力）。

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

応答本文と `usage`（`prompt_tokens` / `completion_tokens` / `total_tokens`）が返れば、gpu-dev（g6e.12xlarge）の 1 枚の GPU で vLLM の推論が動いていることが確認できます（実測で prompt 36 / completion 31 / total 67 トークンの応答を確認）。なお `/v1/models` に出る `max_model_len` は Qwen2.5-0.5B 本来の 32K ではなく、チャートが軽量検証向けに `--max-model-len` を控えめに指定した値です。

port-forward はバックグラウンド（`&`）で起動したので、確認が終わったら `kill %1`（または `jobs` で番号を確認して `kill %<n>`）で止めます。

## 5. 後片付け

推論サーバーを止めれば、GPU ノードは `consolidateAfter`（本構成の NodePool では 5 分に設定）のアイドル後に Karpenter が自動回収します。on-demand なので使った分だけの課金です。投入時と同じ `--set` を付けてレンダリングした結果を `kubectl delete` に渡すと、Deployment・Service を含めチャートが出力した全リソースを取りこぼしなく削除できます。

```bash
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true --set gpuServingVllm.nodeRole=gpu-dev \
    | kubectl delete -f -
kubectl get nodeclaims -w        # GPU ノードが消えるのを確認
```

# まとめ

本章では、Capacity Block を使わずに on-demand の GPU ノード（g5 / g6e 系）へ vLLM の OpenAI 互換サーバーをデプロイし、軽量モデルで推論が動くことを確認しました。単一 GPU で完結するため EFA は不要で、Basic04 で定義した GPU プールに Pod を投げるだけで Karpenter がノードを起動します。on-demand の容量が取れないときは AZ と instance type を複数許可する、CPU リクエストをノードサイズに合わせる、という 2 点が実運用の勘所です。この上で大規模モデルやマルチノードの推論・学習に進む場合は、Basic06 の Capacity Block で GPU を確保します。

# 参考資料

- [vLLM](https://github.com/vllm-project/vllm)
- [vLLM OpenAI-Compatible Server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct)
- [対象ワークロード gpuServingVllm（charts/experiments）](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/gpu-serving-vllm.yaml)
