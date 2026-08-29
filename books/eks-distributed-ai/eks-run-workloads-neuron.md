---
title: "Basic09 - vLLM Neuron で推論を動かす"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、Basic07 で GPU 向けに vLLM 推論サーバーを立ち上げましたが、この章では vLLM と vLLM Neuron Plugin というものを使って AWS Trainium チップの上で LLM 推論を動かします。大きな違いは 2 点で、1 つはランタイム、もう 1 つはハードウェアの確保方法です。ランタイムは、Neuron が CUDA ではなく Neuron ランタイムで動くため、vLLM 本体に Neuron 対応を足す [vLLM Neuron plugin](https://github.com/aws-neuron/upstreaming-to-vllm) を同梱した Deep Learning Container（DLC）を使います。モデルはマルチモーダル（画像とテキスト）の `Qwen/Qwen3-VL-4B-Instruct` を使います。

:::message
本章は Basic05 の手順で確保した trn2 の Capacity Block ノードを前提にします。クラスタと Capacity Block は同じリージョンに無いといけないので、これまでの章と違うリージョンで trn2 を確保する場合は、Basic01 に戻ってそのリージョンにクラスタを作るところからやり直すことになります。作業量が大きいので、リージョンは Basic01 の時点で決めておくのが楽です。なお trn2.3xlarge は一部のリージョンで Spot も選べますが、本章では扱いません。今回はマルチノードや EFA も使いません。
:::

# 解説

## 全体構成

本章は、Capacity Block で確保した Trainium ノード 1 台に、vLLM Neuron plugin の推論サーバーの Pod を載せる構成です。Pod をそこに載せているのは `aws.amazon.com/neuron` というデバイス要求で、Capacity Block のノードそのものを名指しで指定しているわけではありません。同じクラスタに on-demand や Spot の Neuron プールも置いている場合は、`--set neuronVllmPlugin.nodeRole=<プール名>` でどのプールに載せるかを明示してください。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

図のアクセラレータプールのうち、Neuron の 1 ノード構成です。推論は単一ノードで完結し、ノード内の複数 NeuronCore を束ねるテンソル並列で 1 つのモデルを動かします。

## これは何をするものか

[AWS Neuron と Trainium の入門](https://zenn.dev/tosshi/articles/976e375fd47726)

[AWS Neuron の開発フロー](https://zenn.dev/tosshi/articles/545d60be7314cb)

詳細は割愛しますが AWS Neuron や Trainium については上記の記事などを確認してください。

[vLLM Neuron plugin の解説](https://zenn.dev/tosshi/articles/be22d1ace136a5)

vLLM Neuron plugin については上記の記事にまとめてあります。この plugin を同梱した AWS 公式 DLC (`public.ecr.aws/neuron/pytorch-inference-vllm-neuronx`) を Kubernetes の Deployment として trn2 ノードに載せ、Qwen3-VL モデルをサービングします。

GPU 版（Basic07）との対応関係は次のとおりです。

| 観点 | GPU 版 | Neuron 版 |
|---|---|---|
| ハードウェア確保 | spot 優先（on-demand フォールバック） | Capacity Block（Basic05）で確保した trn2 |
| コンテナイメージ | `vllm/vllm-openai`（CUDA） | `pytorch-inference-vllm-neuronx`（Neuron DLC と plugin） |
| アクセラレータ要求 | `nvidia.com/gpu: 1` | `aws.amazon.com/neuron: 1`（デバイス単位） |
| 並列度 | 単一 GPU | NeuronCore 4 個のテンソル並列（`--tensor-parallel-size 4`） |
| 初回起動時のコンパイル | CUDA グラフのキャプチャや torch.compile（比較的短時間） | モデル全体を NEFF へ AOT コンパイル（数分） |

初回起動時のコンパイルは GPU 版も無縁ではありません。vLLM は GPU でも起動時に CUDA グラフのキャプチャ（既定）や torch.compile によるコンパイルを行います。違いは度合いと方式で、Neuron はモデルの計算グラフ全体を NEFF（Neuron Executable File Format）へ AOT（事前）コンパイルするため、初回の所要時間が長く（実機で数分）なります。いずれの場合もコンパイル成果物は `VLLM_CACHE_ROOT` 配下に残り、残しておけば次回以降は再利用されて起動が速くなります。

# ワークショップ実施

## 1. 前提を確認する

- Basic05 の手順で trn2.3xlarge の Capacity Block をメルボルンリージョンで確保ずみ
- Basic05 の手順で `terraform apply` で NodePool 作成済み
- `k` と `KUBECONFIG` は Basic01 step 2 の 4 行で設定済み

trn2 の NodePool と、すでにノードが起動している場合の Neuron リソースを確認します。Karpenter は要求があってからノードを起こすので、手順 3 の Deployment を投入する前は `k get nodes` が空になるのが正常です。空だった場合は `k get nodepool` で NodePool の存在だけを確かめて手順 3 に進んでください。

```bash
k get nodes -l node.kubernetes.io/instance-type=trn2.3xlarge \
  -o custom-columns='NAME:.metadata.name,DEVICE:.status.capacity.aws\.amazon\.com/neuron,CORE:.status.capacity.aws\.amazon\.com/neuroncore'
```

実機出力（trn2.3xlarge 1 台）:

```text
NAME                                             DEVICE   CORE
ip-10-0-21-164.ap-southeast-4.compute.internal   1        4
```

`trn2.3xlarge` は Trainium2 デバイスを 1 個持ち、device plugin はそれを「デバイス 1 個」（`aws.amazon.com/neuron: 1`）かつ「NeuronCore 4 個」（`aws.amazon.com/neuroncore: 4`）として同時に advertise します。この 2 つの単位の違いが、手順 3 のチャート投入時に効いてきます。

:::message
`CORE` が `4` になるのは、このノードが論理 NeuronCore 設定 LNC=2（環境変数 `NEURON_LOGICAL_NC_CONFIG=2` 相当）で動作しているためです。もし環境によって `CORE` が `8`（LNC=1）と表示された場合は、テンソル並列数を advertise されたコア数（この例なら 8）に合わせます。チャートの既定は 4 なので、手順 3 のコマンドに `--set neuronVllmPlugin.tpSize=8` を足してください（この値がコンテナの `--tensor-parallel-size` になります）。
:::

## 2. 作業用 namespace を用意する

作業用 namespace を用意します。

```bash
export NAMESPACE=distai
k create namespace "$NAMESPACE" --dry-run=client -o yaml | k apply -f -
```

## 3. vLLM Neuron plugin の Deployment を投入する

投入する前に、Neuron 固有のポイントを順に押さえます。

### アクセラレータはデバイス単位で要求する

`trn2.3xlarge` の Trainium2 デバイス 1 個は、論理 NeuronCore 設定（LNC=2）のもとで 4 個の論理 NeuronCore として見えます。この 4 個に跨るテンソル並列（`--tensor-parallel-size 4`）で 1 つのモデルをサービングします。GPU 版の `nvidia.com/gpu: 1` に対応する Neuron 側の要求がこれです。並列はノード内の NeuronLink で完結するため EFA は不要です。

ここで重要なのが、アクセラレータの要求をコア単位（`aws.amazon.com/neuroncore`）ではなくデバイス単位（`aws.amazon.com/neuron`）で行う点です。理由は、vLLM Neuron plugin がテンソル並列（TP が 2 以上）でワーカーをマルチプロセス起動する際のデバイス可視性の扱いにあります。コア単位で要求すると device plugin が `NEURON_RT_VISIBLE_CORES` という環境変数を Pod に注入しますが、plugin のマルチプロセスワーカーはこの環境変数と両立せず、次のエラーで起動に失敗します。

```text
RuntimeError: NEURON_RT_VISIBLE_CORES cannot be used with multi-processing execution on vLLM.
Set NEURON_VISIBLE_DEVICES to control device visibility.
```

デバイス単位（`aws.amazon.com/neuron: 1`）で要求すると `NEURON_RT_VISIBLE_CORES` は注入されず、plugin はデバイス全体の 4 個のコアを使ってテンソル並列を組めます。本章のチャート（`neuronVllmPlugin`）がデバイス単位で要求しているのはこのためです。

### 単一デバイスノードでは Recreate 戦略を使う

trn2 ノードは Trainium デバイスが 1 個しかないため、Deployment の既定の RollingUpdate だと更新時に新旧 Pod がデバイスを取り合い、新 Pod が `Pending` のまま進まなくなります。旧 Pod を先に落としてデバイスを解放する `strategy.type: Recreate` を使います。

### 初回コンパイルとキャッシュ（VLLM_CACHE_ROOT）

前述のとおり Neuron は初回起動時にモデルを NEFF へコンパイルします。`VLLM_CACHE_ROOT` にコンパイル成果物の置き場を指定すると、次回以降はキャッシュから読み込まれ、コンパイルを飛ばして起動します。ここで注意したいのは、`VLLM_CACHE_ROOT` がキャッシュするのは NEFF 成果物であって、HuggingFace から取得するモデル本体（数 GB）ではない点です。本章のチャートが `emptyDir` に載せているのは `VLLM_CACHE_ROOT`、つまり NEFF の側だけです。モデル本体のダウンロード先（HuggingFace のキャッシュ）は指定していないので、コンテナの書き込みレイヤに落ちます。どちらも Pod と一緒に消えるため、Pod を作り直すと「モデルの再ダウンロード」と「NEFF の再コンパイル」の両方が発生します。Recreate 戦略のもとでは、Deployment を更新するたびにこの両方が走ります。永続ストレージにキャッシュを置けば、Pod やノードを跨いで NEFF を再利用できます。

### 環境変数と権限、初回のデプロイ期限

- `securityContext.capabilities.add: ["IPC_LOCK"]`: Neuron ランタイムが要求します。
- `progressDeadlineSeconds: 2400`: 初回は数 GB の DLC の pull、モデルのダウンロード、NEFF コンパイルが順に走り、Deployment 既定の進捗期限 600 秒を超えます。これを延ばしておかないと、後述の `rollout status` が待機途中で `ProgressDeadlineExceeded` により失敗します。

リソース要求も押さえておきます。このチャートは CPU を 8、メモリを request 24Gi / limit 96Gi で要求し、`/dev/shm` に 8Gi の tmpfs を割り当てます。Basic07 と同じく `/dev/shm` の使用量はコンテナのメモリ制限に計上されるので、Pod が `Pending` のときは Neuron デバイスの数だけでなく CPU とメモリの空きを、`OOMKilled` のときは `/dev/shm` を含む実使用を見てください。値は `--set neuronVllmPlugin.cpu=...` などで変えられます。

以上の設定はチャート（`charts/experiments` の `values.yaml`、`neuronVllmPlugin`）に定義されています。有効化してレンダリングし、適用します。`model` などを変えたい場合は `--set neuronVllmPlugin.model=...` で上書きできます。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
helm dependency build charts/experiments
helm template exp charts/experiments -n "$NAMESPACE" \
    --set neuronVllmPlugin.enabled=true \
    | k apply -f -
```

## 4. Pod の Ready を待つ

初回はモデルのダウンロードに続いて NEFF コンパイルが走るため、`Ready` まで GPU 版より時間がかかります。イメージがノードにキャッシュ済みの状態では約 8 分で `Ready` になりました。イメージが未キャッシュのノードでは、その pull ぶんさらに数分かかります。

```bash
k -n "$NAMESPACE" rollout status deploy/neuron-vllm --timeout=40m
```

途中経過はログで確認できます。

```bash
k -n "$NAMESPACE" logs deploy/neuron-vllm --tail=20 \
  | grep -E "Platform plugin neuron is activated|Vision warmup|Application startup complete"
```

実機出力（要所を抜粋）:

```text
INFO ... [__init__.py:238] Platform plugin neuron is activated
... neuron_worker.py:1614 -   Vision warmup [1/1]: bucket=2048, num_blocks=4
INFO ... Application startup complete.
```

Pod が `1/1 Running` になれば準備完了です。

## 5. OpenAI 互換 API を叩く

port-forward してモデル一覧と推論を確認します。

```bash
k -n "$NAMESPACE" port-forward svc/neuron-vllm 8000:8000 &
sleep 2
curl -s localhost:8000/v1/models | python3 -m json.tool
```

次は `/v1/models` の実機出力で、一部フィールドは省略しています。サービング中のモデルが表示されます。

```json
{
  "object": "list",
  "data": [
    {
      "id": "Qwen/Qwen3-VL-4B-Instruct",
      "object": "model",
      "max_model_len": 8192,
      "owned_by": "vllm"
    }
  ]
}
```

まずテキストのみの chat completion を叩きます。

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen3-VL-4B-Instruct","messages":[{"role":"user","content":"In one sentence, what is AWS Trainium?"}],"max_tokens":80}' \
  | python3 -m json.tool
```

次が実機出力の抜粋です。

```json
{
  "model": "Qwen/Qwen3-VL-4B-Instruct",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "AWS Trainium is Amazon Web Services’ family of AI accelerators designed to speed up machine learning workloads with high performance and efficiency."
      },
      "finish_reason": "stop"
    }
  ],
  "system_fingerprint": "vllm-0.21.0-tp4-f5b33cdd",
  "usage": { "prompt_tokens": 18, "completion_tokens": 28, "total_tokens": 46 }
}
```

`Qwen3-VL` はマルチモーダルなので、画像を与えた推論も確認します。`content` を配列にして、画像とテキストを並べます。

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"Qwen/Qwen3-VL-4B-Instruct",
    "messages":[{"role":"user","content":[
      {"type":"image_url","image_url":{"url":"http://images.cocodataset.org/val2017/000000039769.jpg"}},
      {"type":"text","text":"What animals are in this image and how many? Answer briefly."}
    ]}],
    "max_tokens":80}' \
  | python3 -m json.tool
```

次が実機出力の抜粋です。

```json
{
  "model": "Qwen/Qwen3-VL-4B-Instruct",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "2 cats" },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 323, "completion_tokens": 3, "total_tokens": 326 }
}
```

この画像はソファの上の猫 2 匹で、モデルは画像を正しく認識しています。テキストと画像の両方で応答と `usage` が返れば、trn2 の 1 ノード上で vLLM Neuron plugin のマルチモーダル推論が動いていることが確認できます。

## 6. 後片付け

推論サーバーを削除します。Capacity Block for ML の場合、インスタンスは期限で自動的に削除されます。

```bash
k delete deploy/neuron-vllm svc/neuron-vllm -n "$NAMESPACE"
```

# まとめ

本章では、Capacity Block で確保した Trainium ノードに vLLM Neuron plugin の OpenAI 互換サーバーをデプロイし、マルチモーダルモデル `Qwen3-VL-4B-Instruct` でテキストと画像の推論が動くことを確認しました。

# 参考資料

- [vLLM](https://github.com/vllm-project/vllm)
- [vLLM Neuron plugin（upstreaming-to-vllm）](https://github.com/aws-neuron/upstreaming-to-vllm)
- [AWS Neuron Documentation](https://awsdocs-neuron.readthedocs-hosted.com/)
- [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
