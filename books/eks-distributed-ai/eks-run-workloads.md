---
title: "Basic07 - 軽量 vLLM で推論を動かす"
free: true
---

本章では、Basic03-06 で用意した Karpenter・アクセラレータプールの上に、vLLM の OpenAI 互換サーバーをデプロイし、軽量な言語モデルで推論を動かします。高額な Capacity Block は使わず、g5 / g6e / g6 系の on-demand GPU で誰でも試せる構成です。Basic02 の CPU 分散学習に続く「GPU を使った推論の最小体験」として位置づけます。

:::message
本章は Capacity Block 不要です。g5.12xlarge / g6e.12xlarge / g6.12xlarge など on-demand で取れる GPU インスタンスで動きます。「まず 1 枚の GPU で推論サーバーが立つ」ことを確認するのが目的で、マルチノードや大規模モデルは扱いません。
:::

# 解説

## 全体構成

この book で構築する基盤のうち、本章は Karpenter が on-demand で起動する GPU ノード 1 台に、推論サーバー（vLLM）の Pod を載せる構成です。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

図のアクセラレータプールのうち、EFA も Capacity Block も使わない最小の GPU 1 枚構成です。推論は単一ノードで完結するため、マルチノード通信（Basic05 の EFA）は不要です。

## これは何をするものか

[vLLM](https://github.com/vllm-project/vllm) は、PagedAttention による高スループットな LLM 推論エンジンです。OpenAI 互換の HTTP API（`/v1/models`、`/v1/chat/completions` など）を提供するため、既存の OpenAI クライアントからそのまま呼び出せます。

本章では、vLLM の公式イメージ `vllm/vllm-openai` を Kubernetes の `Deployment` として GPU ノードに載せ、軽量モデル `Qwen/Qwen2.5-0.5B-Instruct`（ゲートなし・小型で 1 枚の GPU に収まる）をサービングします。モジュールには GPU 向けの雛形 [`gpu-serving-vllm.yaml.tpl`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/manifests/gpu-serving-vllm.yaml.tpl) があり、`nodeSelector` でアクセラレータプールに載せ、`nvidia.com/gpu: 1` をリクエストします。

推論は 1 ノードで完結するため、EFA も Capacity Block も要りません。Basic04 で `accelerator_pools` に GPU プールを定義してあれば、そのプールに Pod を投げるだけで Karpenter が GPU ノードを 1 台起動し、その上で vLLM が立ち上がります。

## 全体の中での位置付け

本章は「GPU を使った最小の動作確認」です。Basic02 では CPU で分散学習（DDP）を動かしました。本章はその GPU 版・推論版にあたり、「Karpenter が GPU ノードを on-demand で起動し、その上で GPU ワークロード（推論）が動く」ことを、Capacity Block の予約なしで確認できます。ここまでで「CPU 学習（Basic02）」「GPU 推論（本章）」の両方を最小コストで体験したことになり、Basic06 で扱った Capacity Block を使えば、これを大規模モデル・マルチノードに広げられる、という全体像が掴めます。

## 注意

**on-demand GPU は容量が取れないことがあります。** GPU インスタンスは人気が高く、特定の AZ・特定のタイプで一時的に `InsufficientInstanceCapacity` になることがあります。実際に本章の検証でも、単一 AZ・単一タイプ（g5.12xlarge / us-east-2b のみ）に固定したプールでは容量が取れず、Karpenter が「filtered out all instance types」を出し続けました。対策は Basic04 と同じで、**複数の AZ と複数の instance type を許可する**ことです。`accelerator_pools` の `zone` を変える、または `instance_types` に `g5.12xlarge` / `g6e.12xlarge` / `g6.12xlarge` のように複数を並べておくと、Karpenter が取れるものを選びます。検証では複数 AZ・複数タイプを許可したところ、別 AZ の g6e が確保できました。

**CPU リクエストは GPU ノードのサイズに対して現実的な値にします。** vLLM の Deployment に `cpu: 8` を要求すると、8 vCPU クラスのインスタンス（g6e.2xlarge など）ではシステム予約（kubelet・DaemonSet 等）を差し引くと足りず、`no instance type has enough resources` でスケジュールされません。実測では `cpu: 8` を `cpu: 4` に下げたところ g6e.2xlarge に載りました。GPU 台数（`nvidia.com/gpu`）に対して CPU/メモリを過剰に要求しないよう注意します。

**GPU Operator の初期化を待ちます。** Karpenter が GPU ノードを起動しても、NVIDIA GPU Operator が `nvidia.com/gpu` を advertise するまで数分かかります。それまで Pod は `Pending` のままですが、これは異常ではありません。

# ワークショップ実施

## 1. vLLM の Deployment を投入する

雛形をレンダリングして適用します。`__NODE_ROLE__` は GPU プール名（Basic04 で定義したもの、例 `gpu-dev`）に置き換えます。

```bash
NAMESPACE=<your-namespace>
MODEL=Qwen/Qwen2.5-0.5B-Instruct     # ゲートなし・小型
sed -e "s/__NAMESPACE__/${NAMESPACE}/g" -e "s#__MODEL__#${MODEL}#g" \
    -e "s/__NODE_ROLE__/gpu-dev/g" \
    gpu-serving-vllm.yaml.tpl | kubectl apply -f -
```

## 2. GPU ノードの起動と Pod の Ready を待つ

```bash
kubectl get nodeclaims -w        # gpu プールの NodeClaim が起動 → Ready
kubectl -n <your-namespace> rollout status deploy/gpu-vllm --timeout=15m
```

GPU ノードの起動（数分）、vLLM イメージの pull、モデルのロードを経て、Pod が `1/1 Running` になります。

## 3. OpenAI 互換 API を叩く

port-forward してモデル一覧と推論を確認します。

```bash
kubectl -n <your-namespace> port-forward svc/gpu-vllm 8000:8000 &
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

応答本文と `usage`（`prompt_tokens` / `completion_tokens` / `total_tokens`）が返れば、g6e / g5 クラスの 1 枚の GPU で vLLM の推論が動いていることが確認できます（実測で prompt 36 / completion 31 / total 67 トークンの応答を確認）。

## 4. 後片付け

推論サーバーを止めれば、GPU ノードは `consolidateAfter`（既定 5 分）のアイドル後に Karpenter が自動回収します。on-demand なので使った分だけの課金です。

```bash
kubectl delete deploy gpu-vllm -n <your-namespace>
kubectl delete svc gpu-vllm -n <your-namespace>
kubectl get nodeclaims -w        # GPU ノードが消えるのを確認
```

# まとめ

本章では、Capacity Block を使わずに on-demand の GPU ノード（g5 / g6e 系）へ vLLM の OpenAI 互換サーバーをデプロイし、軽量モデルで推論が動くことを確認しました。単一 GPU で完結するため EFA は不要で、Basic04 で定義した GPU プールに Pod を投げるだけで Karpenter がノードを起動します。on-demand の容量が取れないときは AZ と instance type を複数許可する、CPU リクエストをノードサイズに合わせる、という 2 点が実運用の勘所です。この上で大規模モデルやマルチノードの推論・学習に進む場合は、Basic06 の Capacity Block で GPU を確保します。

# 参考資料

- [vLLM](https://github.com/vllm-project/vllm)
- [vLLM OpenAI-Compatible Server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct)
- [対象マニフェスト gpu-serving-vllm.yaml.tpl](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/manifests/gpu-serving-vllm.yaml.tpl)
