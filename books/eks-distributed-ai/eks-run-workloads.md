---
title: "Basic07 - 軽量 vLLM で推論を動かす"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.1](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.1)

本章では、Basic04 で用意した Karpenter・アクセラレータプールの上に、vLLM の OpenAI 互換サーバーをデプロイし、軽量な言語モデルで推論を動かします。高額な Capacity Block は使わず、g5 / g6 系の GPU を spot 優先（取れなければ on-demand にフォールバック）で使う、比較的試しやすい構成です。

:::message
本章は Capacity Block 不要です。Basic04 で定義した `gpu-ddp` プール（g6.2xlarge / g5.2xlarge などの小型 GPU、spot + on-demand フォールバック）をそのまま使い、GPU 1 枚で小さなモデルを動かします。「まず 1 枚の GPU で推論サーバーが起動する」ことを確認するのが目的で、マルチノードや大規模モデルは扱いません。
:::

# 解説

## 全体構成

本書で構築する基盤のうち、本章は Karpenter が spot 優先（on-demand フォールバックあり）で起動する GPU ノード 1 台に、推論サーバー（vLLM）の Pod を載せる構成です。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

図のアクセラレータプールのうち、EFA も Capacity Block も使わない最小の GPU 1 枚構成です。推論は単一ノードで完結するため、マルチノード通信（Basic06 の EFA）は不要です。

## これは何をするものか

[vLLM](https://github.com/vllm-project/vllm) は、PagedAttention による高スループットな LLM 推論エンジンです。OpenAI 互換の HTTP API（`/v1/models`、`/v1/chat/completions` など）を提供するため、既存の OpenAI クライアントからそのまま呼び出せます。

本章では、vLLM の公式イメージ `vllm/vllm-openai` を Kubernetes の `Deployment` として GPU ノードに載せ、軽量モデル `Qwen/Qwen2.5-0.5B-Instruct`（ゲートなし・小型で 1 枚の GPU に収まる）をサービングします。Helm チャート [`charts/experiments`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments) の `gpuServingVllm` ワークロードが GPU 向けの雛形で、`nodeSelector` でアクセラレータプールに載せ、`nvidia.com/gpu: 1` をリクエストします。ゲート付きモデルを使う場合は、事前に `hf-token` という Secret を作っておくと、コンテナが `HF_TOKEN` 環境変数として自動的に読み込みます（未作成でも Pod は起動し、ゲートなしモデルでは何も参照されません）。

推論は 1 ノードで完結するため、EFA も Capacity Block も不要です。Basic04 で `accelerator_pools` に GPU プールを定義してあれば、そのプールに Pod を投入するだけで Karpenter が GPU ノードを 1 台起動し、その上で vLLM が立ち上がります。

# ワークショップ実施

はじめにシェルを対象クラスタへ向けます。Basic01 手順 3 と同じ 5 行で、`AWS_PROFILE` と `CLUSTER_NAME` と `AWS_REGION`、それに 1 行目のチェックアウトのパスは自分のものに読み替えます。プロファイルを使わず環境変数やインスタンスロールで認証している場合は、`AWS_PROFILE` の行を削ります。

```bash
cd ~/distributed-ai-v0.2.1
export AWS_PROFILE=default
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
source infra/scripts/distai-env.sh
```

前提は次のとおりです。

| 前提 | どこで用意するか |
|---|---|
| `gpu-ddp` プールがあること | [Basic04](https://zenn.dev/tosshi/books/eks-distributed-ai/viewer/eks-accelerator-pools) |
| NVIDIA GPU Operator が動いていること | [Basic04](https://zenn.dev/tosshi/books/eks-distributed-ai/viewer/eks-accelerator-pools) |

## 1. 前提を確認する

次のコマンドは前提を 1 行ずつ OK か NG で表示します。NG が出たら、上の表の行に書いた場所を先に済ませてください。

```bash
k get nodepool gpu-ddp >/dev/null 2>&1 && echo "OK gpu-ddp プール" || echo "NG gpu-ddp プール"
k -n gpu-operator get deploy -o name 2>/dev/null | grep -q . && echo "OK GPU Operator" || echo "NG GPU Operator"
```

本章は既存のプールと GPU Operator の上に vLLM の Deployment を載せるだけなので、新しくインフラを足す操作はありません。確認するのは NodePool があることだけです。Karpenter は要求があってからノードを起動するので、`k get nodes -l karpenter.sh/nodepool=gpu-ddp` が 0 台でも正常で、本章の Deployment を投入した時点で起動します。

## 2. 作業用 namespace を用意する

Basic01 で作った作業用 namespace を使います。ターミナルを開き直した場合に備えて、ここで冪等に用意し直しておきます（すでに存在していてもエラーになりません）。

```bash
export NAMESPACE=distai
k create namespace "$NAMESPACE" --dry-run=client -o yaml | k apply -f -
```

実機出力（初回作成時）:

```text
namespace/distai created
```

:::message
**ゲート付きモデルを使う場合は、先に `hf-token` Secret を作ります。** 本章のモデル（Qwen2.5-0.5B-Instruct）はゲートなしなので不要ですが、Llama 系などゲート付きモデルに切り替える場合は、`$NAMESPACE`（`distai`）に次の Secret を作っておくと、コンテナが `HF_TOKEN` 環境変数として自動的に読み込みます。

```bash
k create secret generic hf-token -n "$NAMESPACE" --from-literal=token=<HuggingFace のトークン>
```

未作成でも Pod は起動し、ゲートなしモデルでは何も参照されません。
:::

## 3. vLLM の Deployment を投入する

この章では vLLM の公式イメージ `vllm/vllm-openai` をそのまま使うため、自前でのイメージビルドは不要です（`charts/experiments` の `gpuServingVllm.image` の既定値が公式イメージを指しています）。チャートをレンダリングして適用します。`nodeRole` には GPU プール名を渡します。プール名は `terraform.tfvars` の [`accelerator_pools`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/variables.tf) の map キーで、Karpenter がそれを [`node-role=<プール名>`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/karpenter-resources.tf) としてノードに付けるため、この値でプールを指定できます。以下は Basic04 で定義した `gpu-ddp` をそのまま使う例です。プール名を変えている場合は以降も読み替えてください。

投入コマンドの前に、この Deployment が指定する `memory` と `shmSize` が「何のメモリなのか」を確認しておきます。

### vLLM が使う 2 種類のメモリ

LLM のサービングでは、性質の異なる 2 つのメモリが登場します。読者が混同しやすい最大のポイントなので、先に切り分けます。

1 つ目は **GPU の VRAM** です。vLLM はモデルの重みと KV キャッシュを GPU の VRAM に置きます。これは後述のマニフェストが要求する `nvidia.com/gpu: 1` で GPU を 1 枚丸ごと確保した時点で決まる話で、**Kubernetes の `memory` リクエストの管理外**です。vLLM は起動時に、指定された割合（`gpuMemoryUtilization`、本チャートでは `0.85`。vLLM 自体の既定は `0.9`）まで VRAM を確保しますが、本章のモデル `Qwen2.5-0.5B-Instruct` は重みが fp16 で 1 GiB 程度と小さく、L4（24 GB VRAM）などの小型 GPU にも十分に収まります。つまり **本章で VRAM は問題になりません**。

2 つ目は **ノード（ホスト）の RAM** です。vLLM のプロセス自体は GPU とは別に、CPU 側の RAM も使います。Python と PyTorch のランタイム、HuggingFace からダウンロードしたモデルをロードするときの一時バッファ、トークナイザ、そして後述の `/dev/shm` などです。Pod の `memory` リクエスト／リミットが管理するのは**こちらの RAM だけ**です。**本節で問題になるのは、すべてこの RAM 側の話**です。

### /dev/shm と shmSize とは何か

`shmSize` は `/dev/shm`（共有メモリ）のサイズを指定するパラメータです。`/dev/shm` は Linux のプロセス間共有メモリで、実体はノードの RAM 上に作られる tmpfs です。ファイルのように見えますが、中身は RAM を消費します。vLLM（の下で動く PyTorch）は、ワーカープロセス間でテンソルを受け渡す経路や NCCL の共有メモリトランスポートに `/dev/shm` を使います。複数 GPU にまたがるテンソル並列で特に大量に必要になり、単一 GPU の本章では使用量はわずかです。

ここで問題になるのが、コンテナランタイムが用意する `/dev/shm` の既定サイズは **64 MiB しかない**という点です。vLLM にはこれでは不足することがありますが、Kubernetes には `docker run --shm-size` に相当するフィールドがありません。そこで **[`emptyDir: {medium: Memory}`](https://kubernetes.io/docs/concepts/storage/volumes/#emptydir) を `/dev/shm` にマウントして広げる**のが定石で、本チャートの `shmSize` はその `emptyDir` の `sizeLimit` を設定するパラメータです。`medium: Memory` の `emptyDir` は tmpfs なので、その使用量もノードの RAM を実際に消費します。

### チャートの既定値と、このプールに載らない理由

これらのリクエスト値は本書の Helm チャート `charts/experiments` の [`values.yaml`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/values.yaml) と [`gpu-serving-vllm.yaml`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/charts/experiments/templates/gpu-serving-vllm.yaml) にあり、`gpuServingVllm.memory` の既定は `48Gi`、`gpuServingVllm.shmSize` の既定は `8Gi` です。これは大きめの GPU ノードでの利用も想定した保守的な既定値で、本章の小型プール `gpu-ddp` には大きすぎます。

Pod の `memory` リクエストがどこから確保されるかを押さえます。スケジューラや Karpenter が Pod の要求と突き合わせるのは、ノードの物理 RAM そのものではなく **allocatable** です。allocatable は、ノードの物理 RAM から kubelet が OS・kubelet 自身・退避（eviction）閾値のために予約する分を引いた「Pod に割り当ててよい上限」で、物理 16 GiB のノードでも約 12.3 GiB しかありません。さらにそこへ GPU Operator の各種 Pod や CNI などの DaemonSet が先に一部を占有します。

CPU リクエストの既定値は `2` なので g6.2xlarge / g5.2xlarge（8 vCPU）のような小さい GPU ノードのプールでもそのまま載りますが、`memory=48Gi` は、`gpu-ddp` が選びうる最大タイプ（g6.2xlarge / g5.2xlarge、物理 32 GiB でも allocatable は 30 GiB 前後にとどまります）にすら届きません。プール内のどのタイプでも「載るノードがない」と判定されるため、Karpenter はノード（NodeClaim）を 1 つも作らず、Pod は解消されないまま `Pending` で止まります。エラーで落ちるのではなく、`k describe pod` の Event や Karpenter のログに `no instance type has enough resources` が出たまま進まない、という失敗です。

そこで最小タイプの g6.xlarge / g5.xlarge（物理 16 GiB）にも収まる値を明示します。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
helm dependency build charts/experiments
POOL=gpu-ddp
MODEL=Qwen/Qwen2.5-0.5B-Instruct
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true \
    --set gpuServingVllm.model="$MODEL" \
    --set gpuServingVllm.nodeRole=$POOL \
    --set gpuServingVllm.memory=8Gi \
    --set gpuServingVllm.shmSize=2Gi \
    | k apply -f -
```

`memory=8Gi` は、Qwen2.5-0.5B の重み（約 1 GiB）とランタイムのロードに十分な余裕を持たせた値です。`shmSize=2Gi` は、単一 GPU で `/dev/shm` の使用量がわずかであることを踏まえた値です。両方を小さく保つことで、最小タイプの allocatable 約 12.3 GiB に、先に起動している DaemonSet が確保する分を差し引いても収まることを実機で確認しています。DaemonSet が確保する量は GPU Operator や CNI、AMI、Kubernetes のバージョンで変わるので、まずこの値から始めて、`Pending` になったら `k describe pod` の Events で不足しているリソースを確かめてください。

### shmSize はスケジューリングに計上されないという罠

もう 1 つ、`memory` と `shmSize` の関係で踏みやすい罠があります。`shmSize` はノードの RAM を実際に消費するにもかかわらず、**申告した要求値（`memory`）と、実際の RAM 消費（`memory` + `/dev/shm` 使用量）が乖離します**。この差がトラブルの原因になります。

実消費の側では、`/dev/shm` の使用量は同じコンテナの `memory` リミットに対して計上されます。本チャートは `limits.memory` を `requests.memory` と同じ値に設定するため、`/dev/shm` の使用量とプロセスの使用量の合計がこのリミットを超えると、コンテナは OOMKill されます。加えて `/dev/shm` 自体が `shmSize` を超えた場合は、その tmpfs への書き込みが容量不足で失敗します。`Evicted` を探すのではなく、コンテナのログに出る書き込みエラーと、直前に述べた OOMKill を見てください。実務上は、**申告値ではなく実際の消費量で考え、`memory` と `shmSize` の合計を最小タイプの allocatable 未満に収める**のが安全です。たとえば `memory=12Gi` を指定して `shmSize` を既定の `8Gi` に残した場合を考えます。申告した要求値 `12Gi` は物理 16 GiB のノードの allocatable（約 12.3 GiB）ぎりぎりで、GPU Operator や CNI の DaemonSet が先に確保する分を引くと収まらないため `Pending` になります。物理 32 GiB のタイプに配置できたとしても、`/dev/shm` を含む実使用が `12Gi` のリミットを超えれば OOMKill されます。申告値と実際の消費量のどちらでも破綻し得るということです。

さらに厄介なのが、この乖離が Karpenter の起動ループを誘発する点です。

:::message alert
**メモリ不足の失敗の仕方は、CPU 不足とは異なります。** CPU リクエストがプール内の全タイプで allocatable を超えている場合、Karpenter は候補ゼロと判断してノードを起動しません。一方、メモリリクエストが上限ぎりぎりの値の場合、起動ループに陥ることがあります。Karpenter はノードを起動する前に、各インスタンスタイプの allocatable を推定してスケジューリングをシミュレーションします（この推定には [`VM_MEMORY_OVERHEAD_PERCENT`](https://karpenter.sh/docs/reference/settings/)、既定 7.5% などが使われます）。この推定 allocatable では「載る」と判定されても、実際に起動したノードの allocatable が推定より小さいと Pod は載らず、`Pending` が解消されないため「もう 1 台足せば載る」と解釈して起動を繰り返します。実際にこの種の設定で放置したところ **spot ノードが 11 台まで増えました**。spot でも課金は発生するため、`Pending` や再作成を繰り返す Pod を見つけたら NodeClaim の数を必ず確認してください。

```bash
k get nodeclaims -l karpenter.sh/nodepool=gpu-ddp
```

意図した台数を超えていれば、原因の Pod を削除してから NodeClaim を消します（Pod を残したまま NodeClaim を消すと、また起動し直します）。

```bash
k delete deploy gpu-vllm -n "$NAMESPACE"
k delete nodeclaims -l karpenter.sh/nodepool=gpu-ddp
```
:::

ノードの実際の allocatable は次のコマンドで確認できます。プールが複数のインスタンスタイプを並べている場合は、**最も小さいタイプに収まる値**を選んでください。

```bash
k get nodes -l node-role=gpu-ddp \
  -o custom-columns='NAME:.metadata.name,TYPE:.metadata.labels.node\.kubernetes\.io/instance-type,MEM:.status.allocatable.memory'
```

実機出力（g6.xlarge 1 台）:

```text
NAME                                          TYPE         MEM
ip-10-0-a-b.us-west-2.compute.internal        g6.xlarge    12925972Ki
```

:::message
既定のイメージタグは `vllm/vllm-openai:v0.6.3.post1` に固定済みです。`:latest` は使っていません。新しいバージョンの機能やモデル対応が必要な場合は `--set gpuServingVllm.image=vllm/vllm-openai:<タグ>` で明示的に指定します。
:::

## 4. GPU ノードの起動と Pod の Ready を待つ

```bash
k get nodeclaims -w
```

`-w` は watch なので、`READY` が `True` になったのを見たら `Ctrl-C` で抜けます。

実機出力（起動中 → Ready への遷移）:

```text
NAME            TYPE         CAPACITY    ZONE         NODE                                        READY   AGE
gpu-ddp-9clls   g6.xlarge    spot        us-west-2a                                               False   12s
gpu-ddp-9clls   g6.xlarge    spot        us-west-2a   ip-10-0-a-b.us-west-2.compute.internal      True    98s
```

```bash
k -n "$NAMESPACE" rollout status deploy/gpu-vllm --timeout=15m
```

GPU ノードの起動（数分）、vLLM イメージの pull、モデルのロードを経て、Pod が `1/1 Running` になります。

## 5. OpenAI 互換 API を呼び出す

port-forward してモデル一覧と推論を確認します。

```bash
k -n "$NAMESPACE" port-forward svc/gpu-vllm 8000:8000 &
sleep 2
curl -s localhost:8000/v1/models | python3 -m json.tool
```

`/v1/models` にサービング中のモデルが表示されます（実機出力です。一部フィールドは省略しています）。

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

続いて chat completion を呼び出します。

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"Hello"}],"max_tokens":80}' \
  | python3 -m json.tool
```

```json
{
    "id": "chat-2e2101b3831f47cda65280b7c97ae3dd",
    "object": "chat.completion",
    "created": 1786284157,
    "model": "Qwen/Qwen2.5-0.5B-Instruct",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello! How can I assist you today? I'm Qwen, an artificial intelligence language model created by Alibaba Cloud. How can I help you?",
                "tool_calls": []
            },
            "logprobs": null,
            "finish_reason": "stop",
            "stop_reason": null
        }
    ],
    "usage": {
        "prompt_tokens": 30,
        "total_tokens": 61,
        "completion_tokens": 31
    },
    "prompt_logprobs": null
}
```

応答本文と `usage`（`prompt_tokens` / `completion_tokens` / `total_tokens`）が返れば、`gpu-ddp` プールの 1 枚の GPU で vLLM の推論が動いていることが確認できます。

port-forward はバックグラウンド（`&`）で起動したので、確認が終わったら止めます。他にバックグラウンドジョブが無いか `jobs` で確認したうえで、対応する番号を `kill %<n>`（1 個だけなら `kill %1`）に指定します。

## 6. 後片付け

:::message
もし次の Observability をそのまま実施する場合はこの後片付けは Basic08 の後に実施してください。その間ずっと GPU ノードの課金は続くので、Basic08 を後日に回すならここで片付けてください (Basic08 の手順 3 で GPU のメトリクスが 0 系列になるのは、その場合の想定どおりです)。
:::

ターミナルを開き直した場合は、`cd "$(git rev-parse --show-toplevel)"/infra/eks` で移動したうえで `export NAMESPACE=distai` と `POOL=gpu-ddp` を実行し直します。削除コマンドも `charts/experiments` を相対パスで参照するので、ディレクトリが違うとチャートが見つかりません。`gpuServingVllm.nodeRole` が空だとチャートが `nodeRole is required` で止まり、削除もできません。

本章はノードを直接消しません。Pod を消せば、GPU ノードは [`consolidateAfter`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/karpenter-resources.tf)（本章で使う `gpu-ddp` プールでは 5 分）のアイドル後に Karpenter が回収します。`gpu-ddp` は spot 優先で確保するため、使った分だけの課金です。**課金が止まったことは、この NodeClaim が消えたかどうかで判断します。** ノードが残り続ける場合は、同じプールに別の Pod が載っています。Basic04 の TrainJob や GPU スモークの Pod を消し忘れていないかを `k get pods -A -o wide` で確認してください。Deployment・Service の名前は `--set` の値に関わらず変わらないため、`gpuServingVllm.enabled=true` と `nodeRole` だけを付けてレンダリングした結果を `k delete` に渡せば、投入時に指定した `model` や `memory` などの値を覚えておく必要なく、チャートが出力した全リソースを漏れなく削除できます。

```bash
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true --set gpuServingVllm.nodeRole=$POOL \
    | k delete -f -
k get nodeclaims -l karpenter.sh/nodepool=$POOL -w
```

ここで待つ時間は**合計で 10 分ほど**です。内訳は、空になってから `consolidateAfter` の 5 分を待って Karpenter が削除を決め、そこから drain と EC2 の終了で 5 分ほどです。実測では、ワークロードを消してから NodeClaim が消えるまで 10 分 5 秒でした。

紛らわしいのは、その 10 分の間 `k get nodeclaims` の表示が変わらないことです。削除が決まってノードに `karpenter.sh/disrupted` の taint が付いても、NodeClaim は消える瞬間まで `READY=True` のままなので、画面上は何も起きていないように見えます。途中の進み方を確かめたいときはノード側の taint か Karpenter のログを見ます。

```bash
k get node -l node-role=$POOL -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.taints[*].key}{"\n"}{end}'
k -n karpenter logs deploy/karpenter --tail=20 | grep -i disrupt
```

`disrupting node(s) ... Empty ... delete` の行が出ていれば、あとは終了を待つだけです。`-w` は watch なので、行が消えたのを見たら `Ctrl-C` で抜けます。プロンプトに戻る形で待ちたい場合は次を使います。

```bash
while k get nodeclaims -l karpenter.sh/nodepool=$POOL --no-headers 2>/dev/null | grep -q .; do sleep 15; done
k get nodes -l node-role=$POOL
```

NodePool 自体は残しておいてかまいません。Karpenter は要求があってからノードを起動するので、プールが存在するだけでは課金されません。

# まとめ

本章では、Capacity Block を使わずに spot 優先（on-demand フォールバックあり）の GPU ノード（g5 / g6 系）へ vLLM の OpenAI 互換サーバーをデプロイし、軽量モデルで推論が動くことを確認しました。単一 GPU で完結するため EFA は不要で、Basic04 で定義した GPU プールに Pod を投入するだけで Karpenter がノードを起動します。spot/on-demand どちらも容量が取れないときは AZ と instance type を複数許可する、メモリなどのリクエストをノードサイズに合わせる、動作確認が終わったら忘れずに Deployment を削除して GPU ノードを回収させる、という 3 点が実運用で押さえるべき点です。この上で大規模モデルやマルチノードの推論・学習に進む場合は、Basic05 の Capacity Block で GPU を確保します。

# 参考資料

- [vLLM](https://github.com/vllm-project/vllm)
- [vLLM OpenAI-Compatible Server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
