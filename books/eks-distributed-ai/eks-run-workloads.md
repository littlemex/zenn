---
title: "Basic07 - 軽量 vLLM で推論を動かす"
free: true
---

本章では、Basic03-06 で用意した Karpenter・アクセラレータプールの上に、vLLM の OpenAI 互換サーバーをデプロイし、軽量な言語モデルで推論を動かします。高額な Capacity Block は使わず、g5 / g6 系の GPU を spot 優先（取れなければ on-demand にフォールバック）で使う、誰でも試せる構成です。Basic02 の CPU 分散学習に続く「GPU を使った推論の最小体験」として位置づけます。

:::message
本章は Capacity Block 不要です。Basic04 で定義した `gpu-ddp` プール（g6.2xlarge / g5.2xlarge などの小型 GPU、spot + on-demand フォールバック）をそのまま使い、GPU 1 枚で小さなモデルを動かします。「まず 1 枚の GPU で推論サーバーが立つ」ことを確認するのが目的で、マルチノードや大規模モデルは扱いません。
:::

# 解説

## 全体構成

この book で構築する基盤のうち、本章は Karpenter が spot 優先（on-demand フォールバックあり）で起動する GPU ノード 1 台に、推論サーバー（vLLM）の Pod を載せる構成です。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

図のアクセラレータプールのうち、EFA も Capacity Block も使わない最小の GPU 1 枚構成です。推論は単一ノードで完結するため、マルチノード通信（Basic06 の EFA）は不要です。

## これは何をするものか

[vLLM](https://github.com/vllm-project/vllm) は、PagedAttention による高スループットな LLM 推論エンジンです。OpenAI 互換の HTTP API（`/v1/models`、`/v1/chat/completions` など）を提供するため、既存の OpenAI クライアントからそのまま呼び出せます。

本章では、vLLM の公式イメージ `vllm/vllm-openai` を Kubernetes の `Deployment` として GPU ノードに載せ、軽量モデル `Qwen/Qwen2.5-0.5B-Instruct`（ゲートなし・小型で 1 枚の GPU に収まる）をサービングします。Helm チャート [`charts/experiments`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments) の `gpuServingVllm` ワークロードが GPU 向けの雛形で、`nodeSelector` でアクセラレータプールに載せ、`nvidia.com/gpu: 1` をリクエストします。ゲート付きモデルを使う場合は、事前に `hf-token` という Secret を作っておくと、コンテナが `HF_TOKEN` 環境変数として自動的に読み込みます（未作成でも Pod は起動し、ゲートなしモデルでは何も参照されません）。

推論は 1 ノードで完結するため、EFA も Capacity Block も要りません。Basic04 で `accelerator_pools` に GPU プールを定義してあれば、そのプールに Pod を投げるだけで Karpenter が GPU ノードを 1 台起動し、その上で vLLM が立ち上がります。

## 全体の中での位置付け

本章は「GPU を使った最小の動作確認」です。Basic02 では CPU で分散学習（DDP）を動かしました。本章はその GPU 版・推論版にあたり、「Karpenter が GPU ノードを spot 優先で起動し、その上で GPU ワークロード（推論）が動く」ことを、Capacity Block の予約なしで確認できます。ここまでで「CPU 学習（Basic02）」「GPU 推論（本章）」の両方を最小コストで体験したことになり、Basic05 で扱った Capacity Block を使えば、これを大規模モデル・マルチノードに広げられる、という全体像が掴めます。

## 注意

:::message alert
**GPU の容量が取れないことがあります。** GPU インスタンスは人気が高く、特定の AZ・特定のタイプで一時的に `InsufficientInstanceCapacity` になり、起動に失敗し続けることがあります。また `accelerator_pools` の `instance_types` / `zones` の指定が狭すぎると、Karpenter が候補ゼロと判断して NodeClaim を作らないこともあります。いずれの場合も対策は Basic04 と同じで、`gpu-ddp` プールで `zones` を変えて別の AZ を試すか、`instance_types` に候補を複数並べて Karpenter に選択肢を与えます。Basic04 の `gpu-ddp` が最初から 4 タイプを並べているのはこのためです。実際に単一 AZ・単一タイプに固定していて容量が取れなかったケースでも、複数 AZ・複数タイプを許可したら確保できました。
:::

:::message
**GPU Operator の初期化を待ちます。** Karpenter が GPU ノードを起動しても、NVIDIA GPU Operator が展開する device plugin が `nvidia.com/gpu` を advertise するまで数分かかります。それまで Pod は `Pending` のままですが、これは異常ではありません。
:::

:::message
**CPU リクエストはノードサイズに合わせて調整します。** `gpuServingVllm` の CPU リクエスト既定値は `2` で、Qwen2.5-0.5B のような小型モデルであれば g6.2xlarge / g5.2xlarge（8 vCPU）のような小さい GPU ノードのプールでもそのまま載ります。大きいモデルをサービングする場合や、より大きな GPU プールでより多くの CPU を割り当てたい場合は、`--set gpuServingVllm.cpu=<値>` で明示的に上げます。プールの割り当て可能 CPU を超える値を指定すると、Karpenter が `no instance type has enough resources` を出して Pod が永久に `Pending` になる点には注意します。
:::

:::message alert
**GPU ノードは使い終わったら必ず回収させます。** `gpu-ddp` は g6.2xlarge / g5.2xlarge のような小型タイプを spot 優先で選ぶため、spot が確保できていれば 1 時間あたり 0.4 USD 前後で収まりますが、放置すれば積み上がります。spot が取れず on-demand にフォールバックした場合は 2〜3 倍になります（例えば g5.2xlarge の on-demand は 1 時間あたり約 1.21 USD）。より大きな GPU プール（g6e.12xlarge のような GPU、on-demand で 1 時間あたり 10 USD 前後）を使う場合はなおさらです。動作確認が終わったら必ず後述の手順 6 で Deployment を削除し、GPU ノードを Karpenter に回収させてください。
:::

# ワークショップ実施

## 1. 前提を確認する

- Basic04 で `gpu-ddp` プール（g6.2xlarge / g5.2xlarge / g6.xlarge / g5.xlarge、spot + on-demand フォールバック）を定義・apply 済みであること。
- `k` エイリアスと `KUBECONFIG` / `--context` は Basic01 で設定済みであること。
- NVIDIA GPU Operator は Basic04 の apply で導入済みであること（`device_plugin = "nvidia"` のプールが 1 つ以上あることが導入条件でした）。

本章は既存のプールと GPU Operator の上に vLLM の Deployment を載せるだけなので、新しくインフラを足す操作はありません。

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

この章では vLLM の公式イメージ `vllm/vllm-openai` をそのまま使うため、自前でのイメージビルドは不要です（`charts/experiments` の `gpuServingVllm.image` の既定値が公式イメージを指しています）。チャートをレンダリングして適用します。`nodeRole` には GPU プール名を渡します。プール名は `terraform.tfvars` の `accelerator_pools` の map キーで、Karpenter がそれを `node-role=<プール名>` としてノードに付けるため、この値でプールを指定できます。以下は Basic04 で定義した `gpu-ddp` をそのまま使う例です。プール名を変えている場合は以降も読み替えてください。

投入コマンドの前に、この Deployment が指定する `memory` と `shmSize` が「何のメモリなのか」を押さえておきます。ここを飛ばすと、後で出てくる「既定値のままだと失敗する」という話が、何が失敗するのか分からないまま進むことになります。

### vLLM が使う 2 種類のメモリ

LLM のサービングでは、性質の異なる 2 つのメモリが登場します。読者が混同しやすい最大のポイントなので、先に切り分けます。

1 つ目は **GPU の VRAM** です。vLLM はモデルの重みと KV キャッシュ（生成中のトークンの内部状態を貯める領域）を GPU の VRAM に置きます。これは後述のマニフェストが要求する `nvidia.com/gpu: 1` で GPU を 1 枚丸ごと確保した時点で決まる話で、**Kubernetes の `memory` リクエストの管理外**です。vLLM は起動時に、指定された割合（`gpuMemoryUtilization`、本チャートでは `0.85`。vLLM 自体の既定は `0.9`）まで VRAM を確保しにいきますが、本章のモデル `Qwen2.5-0.5B-Instruct` は重みが fp16 で 1 GiB 程度と小さく、L4（24 GB VRAM）などの小型 GPU にも余裕で収まります。つまり **本章で VRAM は問題になりません**。

2 つ目は **ノード（ホスト）の RAM** です。vLLM のプロセス自体は GPU とは別に、CPU 側の RAM も使います。Python と PyTorch のランタイム、HuggingFace からダウンロードしたモデルをロードするときの一時バッファ、トークナイザ、そして後述の `/dev/shm` などです。Pod の `memory` リクエスト／リミットが管理するのは**こちらの RAM だけ**です。**本節で問題になるのは、すべてこの RAM 側の話**です。0.5B の小さなモデルなのに `memory` の既定値が大きい、という後述の違和感は、この「GPU ではなくホスト RAM の予約値」だと分かれば解けます。

### /dev/shm と shmSize とは何か

`shmSize` は `/dev/shm`（共有メモリ）のサイズを指定するパラメータです。`/dev/shm` は Linux のプロセス間共有メモリで、実体はノードの RAM 上に作られる tmpfs です。ファイルのように見えますが、中身は RAM を消費します。vLLM（の下で動く PyTorch）は、ワーカープロセス間でテンソルを受け渡す経路や NCCL の共有メモリトランスポートに `/dev/shm` を使います。複数 GPU にまたがるテンソル並列で特に大量に必要になり、単一 GPU の本章では使用量はわずかです。

ここで問題になるのが、コンテナランタイムが用意する `/dev/shm` の既定サイズは **64 MiB しかない**という点です。vLLM にはこれでは不足することがありますが、Kubernetes には `docker run --shm-size` に相当するフィールドがありません。そこで **`emptyDir: {medium: Memory}` を `/dev/shm` にマウントして広げる**のが定石で、本チャートの `shmSize` はその `emptyDir` の `sizeLimit` を設定するパラメータです。`medium: Memory` の `emptyDir` は tmpfs なので、その使用量もノードの RAM を実際に消費します。

### チャートの既定値と、このプールに載らない理由

これらのリクエスト値は本 book の Helm チャート [`charts/experiments`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments) の `values.yaml` にあり、`gpuServingVllm.memory` の既定は `48Gi`、`gpuServingVllm.shmSize` の既定は `8Gi` です。これは大きめの GPU ノードでの利用も想定した保守的な既定値で、本章の小型プール `gpu-ddp` には大きすぎます。

Pod の `memory` リクエストがどこから確保されるかを押さえます。スケジューラや Karpenter が Pod の要求と突き合わせるのは、ノードの物理 RAM そのものではなく **allocatable** です。allocatable は、ノードの物理 RAM から kubelet が OS・kubelet 自身・退避（eviction）閾値のために予約する分を引いた「Pod に割り当ててよい上限」で、物理 16 GiB のノードでも約 12.3 GiB しかありません（実測値は本節末尾のコマンドで確認できます。予約量は AMI や kubelet 設定で多少変動します）。さらにそこへ GPU Operator の各種 Pod や CNI などの DaemonSet が先に一部を占有します。

したがって `memory=48Gi` は、`gpu-ddp` が選びうる最大タイプ（g6.2xlarge / g5.2xlarge、物理 32 GiB でも allocatable は 30 GiB 前後にとどまります）にすら届きません。プール内のどのタイプでも「載るノードがない」と判定されるため、Karpenter はノード（NodeClaim）を 1 つも作らず、Pod は解消されないまま `Pending` で止まります。これが「既定値のままだと失敗する」の正体です。エラーで落ちるのではなく、`k describe pod` の Event や Karpenter のログに `no instance type has enough resources` が出たまま進まない、という失敗です。

そこで最初から、最小タイプの g6.xlarge / g5.xlarge（物理 16 GiB）にも収まる値を明示します。

```bash
cd infra/eks
MODEL=Qwen/Qwen2.5-0.5B-Instruct     # ゲートなし・小型
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true \
    --set gpuServingVllm.model="$MODEL" \
    --set gpuServingVllm.nodeRole=gpu-ddp \
    --set gpuServingVllm.memory=8Gi \
    --set gpuServingVllm.shmSize=2Gi \
    | k apply -f -
```

`memory=8Gi` は、Qwen2.5-0.5B の重み（約 1 GiB）とランタイムのロードに十分な余裕を持たせた値です。`shmSize=2Gi` は、単一 GPU で `/dev/shm` の使用量がわずかであることを踏まえた値です。両方を小さく保つことで、最小タイプの allocatable 約 12.3 GiB に、DaemonSet の先客を差し引いても確実に収めています。

### shmSize はスケジューリングに計上されないという罠

もう 1 つ、`memory` と `shmSize` の関係で踏みやすい罠があります。`shmSize` で広げた `/dev/shm` は tmpfs としてノードの RAM を実際に消費するにもかかわらず、`emptyDir` の `sizeLimit` は Pod の `requests` には計上されず、スケジューラや Karpenter が見る「要求」に加算されません。つまり **帳簿上の要求（`memory`）と、実際の RAM 消費（`memory` + `/dev/shm` 使用量）が乖離します**。この乖離が事故の源です。

実消費の側では、`/dev/shm` の使用量は同じコンテナの `memory` リミットに対して計上されます。本チャートは `limits.memory` を `requests.memory` と同じ値（`$memory`）に設定するため、`/dev/shm` の使用量とプロセスの使用量の合計がこのリミットを超えると、コンテナは OOMKill されます。加えて `/dev/shm` 自体が `shmSize`（`sizeLimit`）を超えた場合は、クラスタの kubelet 設定に応じて `/dev/shm` への書き込みが失敗するか Pod が退避されます（どちらの形で顕在化するかはバージョン・設定で異なります）。だからチャートの `values.yaml` のコメントも「実際の需要は `memory` + `shmSize`」と保守的に注意しています。実務上は、**帳簿ではなく実消費で考え、`memory` と `shmSize` の合計を最小タイプの allocatable 未満に収める**のが安全です。たとえば `memory=12Gi` を指定して `shmSize` を既定の `8Gi` に残した場合、帳簿上の要求 `12Gi` は物理 16 GiB のノードの allocatable（約 12.3 GiB）ぎりぎりで、GPU Operator や CNI の DaemonSet が先に確保する分を引くと収まらないため `Pending` になり、物理 32 GiB のタイプに載ったとしても、`/dev/shm` を含む実使用が `12Gi` のリミットを超えれば OOMKill されます。帳簿と実消費のどちらの側でも破綻し得るということです。

さらに厄介なのが、この乖離が Karpenter の起動ループを誘発する点です。

:::message alert
**メモリ不足の失敗の仕方は、CPU 不足とは異なります。** CPU リクエストがプール内の全タイプで allocatable を超えている場合、Karpenter は候補ゼロと判断してノードを起動しません（Pod は `Pending` のまま増えません）。一方、メモリリクエストが境界的な値の場合、起動ループに陥ることがあります。Karpenter はノードを起こす前に、各インスタンスタイプの allocatable を推定してスケジューリングをシミュレーションします（この推定には `VM_MEMORY_OVERHEAD_PERCENT`、既定 7.5% などが使われます）。この推定 allocatable では「載る」と判定されても、実際に起動したノードの allocatable が推定より小さい（GPU 向け AMI は予約が大きめになりがちです）と Pod は載らず、`Pending` が解消されないため「もう 1 台足せば載る」と解釈して起動を繰り返します。実際にこの種の設定で放置したところ **spot ノードが 11 台まで増えました**（本来必要なのは 1 台です。このときはノードを追加しても `Pending` が解消されない状態が連鎖しました）。spot でも課金は発生するため、`Pending` や再作成を繰り返す Pod を見つけたら NodeClaim の数を必ず確認してください。

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
k get nodeclaims -w        # gpu プールの NodeClaim が起動 → Ready
```

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

## 5. OpenAI 互換 API を叩く

port-forward してモデル一覧と推論を確認します。

```bash
k -n "$NAMESPACE" port-forward svc/gpu-vllm 8000:8000 &
sleep 2   # フォワード確立を待つ（省くと connection refused になることがあります）
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

続いて chat completion を叩きます。

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"Hello"}],"max_tokens":80}' \
  | python3 -m json.tool
```

応答本文と `usage`（`prompt_tokens` / `completion_tokens` / `total_tokens`）が返れば、`gpu-ddp` プールの 1 枚の GPU で vLLM の推論が動いていることが確認できます（実測で prompt 36 / completion 31 / total 67 トークンの応答を確認しました）。なお `/v1/models` に出る `max_model_len` は Qwen2.5-0.5B 本来の 32K ではなく、チャートが軽量検証向けに `--max-model-len` を控えめに指定した値です。

port-forward はバックグラウンド（`&`）で起動したので、確認が終わったら止めます。他にバックグラウンドジョブが無いか `jobs` で確認したうえで、対応する番号を `kill %<n>`（1 個だけなら `kill %1`）に指定します。

## 6. 後片付け

推論サーバーを止めれば、GPU ノードは `consolidateAfter`（本構成の NodePool では 5 分に設定）のアイドル後に Karpenter が自動回収します。`gpu-ddp` は spot 優先で確保するため、使った分だけの課金です（spot が取れず on-demand にフォールバックしていた場合も、止めればそこで課金は止まります）。Deployment・Service の名前は `--set` の値に関わらず変わらないため、`gpuServingVllm.enabled=true` と `nodeRole` だけを付けてレンダリングした結果を `k delete` に渡せば、投入時に指定した `model` や `memory` などの値を覚えておく必要なく、チャートが出力した全リソースを取りこぼしなく削除できます。

```bash
helm template exp charts/experiments -n "$NAMESPACE" \
    --set gpuServingVllm.enabled=true --set gpuServingVllm.nodeRole=gpu-ddp \
    | k delete -f -
k get nodeclaims -w        # GPU ノードが消えるのを確認
```

# まとめ

本章では、Capacity Block を使わずに spot 優先（on-demand フォールバックあり）の GPU ノード（g5 / g6 系）へ vLLM の OpenAI 互換サーバーをデプロイし、軽量モデルで推論が動くことを確認しました。単一 GPU で完結するため EFA は不要で、Basic04 で定義した GPU プールに Pod を投げるだけで Karpenter がノードを起動します。spot/on-demand どちらも容量が取れないときは AZ と instance type を複数許可する、メモリなどのリクエストをノードサイズに合わせる、動作確認が終わったら忘れずに Deployment を削除して GPU ノードを回収させる、という 3 点が実運用の勘所です。この上で大規模モデルやマルチノードの推論・学習に進む場合は、Basic05 の Capacity Block で GPU を確保します。

# 参考資料

- [vLLM](https://github.com/vllm-project/vllm)
- [vLLM OpenAI-Compatible Server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
