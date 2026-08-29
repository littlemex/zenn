---
title: "Basic04 - GPU で分散学習を体験する"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、Basic02 で CPU だけだった分散学習(DDP)を GPU に載せ替えます。ここでは Karpenter の混在させた確保の実験も兼ねて、g5 と g6 を混ぜた Spot とオンデマンドを組み合わせた構成で 2 ノード GPU DDP を実行してみます。なお Spot を混ぜた DDP は本来、バッチ推論やデータ前処理に向くもので、大規模な分散学習では中断のたびに全 rank が最後のスナップショットからやり直しになるため実用的ではありません。本章はあくまで「すでに動かした DDP を GPU に載せ、Karpenter の混在させた確保を確かめる」実験としての位置づけです。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち **Karpenter が起動するアクセラレータノードのプール定義**です。Basic03 で入れた Karpenter が実際に GPU ノードを起動できるようにするのがゴールです。

## この章で試すこと

この章で試すのは Karpenter の混在させた確保、すなわち複数のインスタンスタイプと購入オプション（Spot とオンデマンド）を 1 つの NodePool に許可して、確保できるものからノードを起動する挙動です。単一のインスタンスタイプ・単一の購入オプションに絞ると、そのサイズや spot 在庫が足りないときに確保に失敗して先に進めなくなりますが、混在させておくと空いている組み合わせで埋められます。これは単発の実験ジョブなどでノードを確保したい場面で役に立ちます。ここではその挙動を、すでに動かした DDP を GPU に載せて確かめます。

本章のアプローチは次の通りです。

- g5(A10G)と g6(L4)を**異なる世代を混在させます**: 片方が取れなくても他方でノードが立ちます
- Spot を第一優先、オンデマンドをフォールバックにします: コストを抑えつつ 2 台確保します
- 単一 AZ に固定します: cross-AZ レイテンシの影響を避けます(マルチ AZ 構成も作り的には可能です)

:::message
本章の accelerator_pools capacity-mix 実装は、基本的なユースケースをカバーする初期バージョンです。今後、実運用に基づき EFA 対応の大規模訓練シナリオや Capacity Block との連携パターンを追加していきます。そもそも Terraform の領域で実装を頑張るものでもないので根本的な改善も検討中です。
:::

## accelerator_pools の設定

### accelerator_pools に 1 エントリ書くだけ

```hcl
accelerator_pools = {
  gpu-ddp = {
    instance_types      = ["g6.2xlarge", "g5.2xlarge", "g6.xlarge", "g5.xlarge"]
    device_plugin       = "nvidia"
    capacity_types      = ["spot", "on-demand"]
    efa_interface_count = 0
    labels              = { workload = "ddp-basic04" }
  }
}
```

この 1 エントリから、Karpenter の NodePool と EC2NodeClass が自動生成されます（この定義を実際にどのファイルに書くかは、後述のワークショップ手順で扱います）。

### 設定の意味

| フィールド | 値 | 効果 |
|---|---|---|
| instance_types | g6.2xlarge, g5.2xlarge, g6.xlarge, g5.xlarge | 4 種のうちキャパシティがあるものを Karpenter が選択 |
| capacity_types | ["spot", "on-demand"] | spot 優先、取れなければ on-demand にフォールバック |
| zones | 未指定(`local.azs[0]` に自動導出) | 共有ストレージと同じ AZ に寄せる。Basic05 の Capacity Block プールの AZ は予約から導出されるため、同じ AZ になるとは限らない |
| efa_interface_count | 0 | 小型 GPU に EFA なし(NCCL は TCP ソケット経由で通信) |
| labels | workload=ddp-basic04 | 追加のラベル。Pod からプールを選ぶ第一級の手段は、プール名(map のキー)がそのまま Karpenter のノードラベル `node-role=<プール名>` になる仕組みで、`labels` はさらに細かくルーティングしたいときの補助です |

### プール確保ロジックの全体像

設定がどう処理されるかを図にまとめました。

![accelerator_pools ロジックフロー](/images/books/eks-distributed-ai/accelerator-pools-logic.png)

入力(tfvars)から NodePool/EC2NodeClass のレンダリングまでは、正規化(pool_effective)と EFA 導出(EC2 API)が互いに独立して並行評価され、その両者を disruption preset が参照し、最後にすべてが Kubernetes リソース生成に流れ込むという依存関係で処理されます。

### インタラクティブシミュレータ

設定を変えるとアロケーション結果がどう変わるかを、以下のシミュレータで試せます。

@[codepen](https://codepen.io/larcpwpp-the-styleful/pen/019fbdc6-b9c2-7eb1-809e-3c95429b1d9e?default-tab=result)

インスタンスタイプ、capacity_types、zones を変えると、NodePool の requirement values、EFA topology、disruption 設定がリアルタイムで更新されます。このシミュレータは挙動のイメージをつかむための別実装であり、正は常に Terraform の実装です。両者がドリフトする可能性がある点にご注意ください。

# ワークショップ実施

## 1. 前提を確認する

- Karpenter が導入済み
- Basic02 で作った `ddp-sample` イメージ(ECR に push 済み)

NVIDIA GPU Operator は Basic03 の時点では入っていません。`accelerator_pools` に `device_plugin = "nvidia"` のプールが 1 つ以上あることを条件 ([`local.has_gpu_pool`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/locals.tf))に導入されるため、本章で初めてインストールされます。

プール定義は `terraform.tfvars` に直接書かず、専用ファイル `accelerator-pools.auto.tfvars` に置きます。`accelerator_pools` は 1 つの map 変数なので、`terraform.tfvars` にも書くと定義が 2 か所になります。このときエラーにはならず、[読み込み順](https://developer.hashicorp.com/terraform/language/values/variables#variable-definition-precedence)で後になる `*.auto.tfvars` の値が黙って優先され、`terraform.tfvars` に書いたほうは何も言われずに無視されます。気づけない事故になるので、定義箇所をこの 1 ファイルに集約し、`terraform.tfvars` 側には `accelerator_pools` を書かないのがポイントです（変数の `default = {}` があるので、ファイルが無い章でも apply は成功します）。`*.auto.tfvars` は Terraform が自動で読み込むため、`-var-file` の指定も不要です。

リポジトリにはコメント付きの雛形 [`accelerator-pools.tfvars.example`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/accelerator-pools.tfvars.example) があります。どんなプールが書けるかはこれを読むと分かるので、まず中身を眺めます。雛形は全プール例がコメントアウトされた空の map (`accelerator_pools = {}`) なので、これをそのままコピーして apply しても何も作られません。

本章で作るファイルは次の手順の heredoc で作成するため、雛形のコピーは不要です。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
cat accelerator-pools.tfvars.example
```

プールに書くインスタンスタイプは、クラスタのリージョンで提供されているものに限ります。提供されていない型を書くと、Karpenter がフォールバックする前に `terraform plan` がその型の照会で失敗します。書く前に確かめておきます。

```bash
aws ec2 describe-instance-type-offerings --region "$AWS_REGION" \
  --location-type availability-zone \
  --filters 'Name=instance-type,Values=g6.2xlarge,g5.2xlarge,g6.xlarge,g5.xlarge' \
  --query 'InstanceTypeOfferings[].{Type:InstanceType,AZ:Location}' --output table
```

表示されなかった型は次の定義から外します。本章ではこのファイルの中身を次の内容にします。

```bash
cat > accelerator-pools.auto.tfvars <<'EOF'
accelerator_pools = {
  gpu-ddp = {
    instance_types      = ["g6.2xlarge", "g5.2xlarge", "g6.xlarge", "g5.xlarge"]
    device_plugin       = "nvidia"
    capacity_types      = ["spot", "on-demand"]
    efa_interface_count = 0
    labels              = { workload = "ddp-basic04" }
  }
}
EOF
```

書き込めたら apply します。

`infra/eks` ディレクトリで apply します。この apply は NVIDIA GPU Operator の初導入を含むので 5 分前後かかります。終わったら NodePool と Operator の Pod を見て、次に進める状態かを確かめます。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
terraform apply
k get nodepool gpu-ddp
k get pods -n gpu-operator
```

`gpu-operator` の Pod がまだ `ContainerCreating` でも、NodePool が出来ていれば手順 2 に進めます。device plugin の DaemonSet は GPU ノードが起動してから載ります。

## 2. TrainJob で 2 ノード DDP を投入する

Kubeflow Trainer v2 の TrainJob は、Pod への `nodeSelector` や `tolerations` をトップレベルの専用フィールドとしては持たず（`spec.podSpecOverrides` で上書きする口はありますが）、Pod スペックの元になるのは Basic02 で確認した `ClusterTrainingRuntime`(`torch-distributed-eks`)が持ちます。そこに焼き込まれた `nodeSelector: { node-role: <値> }` がどのプールに載せるかを決めます。本構成では上書きに頼らず、Helm の `trainjobTrain.nodeRole` を `gpu-ddp` に切り替えて `torch-distributed-eks` を再レンダリングすることで GPU プールへ載せ替えます。

Basic02 と同じ [`ddp.py`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/manifests/ddp-sample/ddp.py)(CUDA が見えれば nccl backend を自動選択するコード)を、同じ `ddp-sample` イメージ(CUDA ベース)で動かすので、学習コード・イメージのどちらも変更不要です。変わるのは Helm に渡す値(`nodeRole`、`gpu.enabled`、`gpu.count`)だけです。

既存の TrainJob が残っていると spec が同一のため apply しても再実行されないので、作り直す前に削除します。イメージは Basic02 で push した `ddp-sample` で、`ECR_URL` の導出も Basic02 と同じです。

```bash
export NAMESPACE=distai
k delete trainjob ddp-trainjob --ignore-not-found

ECR_URL=$(terraform output -raw ddp_sample_ecr_url)
IMAGE=${ECR_URL}:v1

helm template exp charts/experiments -n "$NAMESPACE" \
    --set trainjobTrain.enabled=true \
    --set trainjobTrain.image="$IMAGE" \
    --set trainjobTrain.nodeRole=gpu-ddp \
    --set trainjobTrain.numNodes=2 \
    --set trainjobTrain.nprocPerNode=1 \
    --set trainjobTrain.gpu.enabled=true \
    --set trainjobTrain.gpu.count=1 \
    --set trainjobTrain.totalEpochs=20 \
    --set sharedStorage.existingClaimName=shared-claim \
    | k apply -f -
```

`trainjobTrain.nodeRole=gpu-ddp` が `torch-distributed-eks` の `nodeSelector.node-role` を `gpu-ddp` に、`trainjobTrain.gpu.enabled=true` が `resourcesPerNode.limits.nvidia.com/gpu` と `nvidia.com/gpu` taint への toleration を有効にします。TrainJob 自体の名前は常に `ddp-trainjob` です。

## 3. 実行と確認

TrainJob が展開する Pod は JobSet の規則で名付けられるため、Pod 名を固定値で指定せずラベルで選びます。

```bash
k get pods -o wide -l jobset.sigs.k8s.io/jobset-name=ddp-trainjob
k get trainjob ddp-trainjob -w
```

Karpenter が 2 台の g5/g6 ノードを起動し、それぞれに 1 GPU ずつ割り当てて DDP が走ります。確保の様子は NodeClaim で見えます。

```bash
k get nodeclaims -l karpenter.sh/nodepool=gpu-ddp
```

実機出力:

```text
NAME            TYPE         CAPACITY    ZONE         NODE                                        READY   AGE
gpu-ddp-9clls   g6.2xlarge   spot        us-west-2a   ip-10-0-0-254.us-west-2.compute.internal    True    3m
gpu-ddp-qhczq   g6.2xlarge   on-demand   us-west-2a   ip-10-0-27-109.us-west-2.compute.internal   True    3m
```

この例では 1 台目が spot、2 台目が on-demand になっています。Karpenter は許可した複数タイプ・複数 capacity-type の中から確保可能なものを選ぶため、spot の空きが足りないときは他タイプの spot を試し、それも取れなければ on-demand にフォールバックして台数を満たします。`CAPACITY` 列が 2 台で異なるのは失敗ではなく、単一タイプ・単一 capacity-type に固定していたら 2 台目が `Pending` のままだった状況を、混在させた確保が救っている状態です。空き状況によっては spot x2 で揃うこともあります。

rank 0(completion index 0)のログを追います。

```bash
SEL="jobset.sigs.k8s.io/jobset-name=ddp-trainjob,batch.kubernetes.io/job-completion-index=0"
until k get pods -l "$SEL" --no-headers 2>/dev/null | grep -q .; do sleep 5; done
k wait --for=condition=ready pod -l "$SEL" --timeout=15m
k logs -f --tail=-1 -l "$SEL"
```

`ddp.py` は CUDA が見えるとログに `backend=nccl cuda_available=True device_count=1` と出し、各 rank が `done` で終われば成功です。実機出力は次のようになります。

```text
[rank 0/2] backend=nccl cuda_available=True device_count=1
[rank 0/2] downloading MNIST to /shared/mnist-data
[rank 0/2] starting training: 20 epochs, batch_size 32
[rank 0/2] epoch 0 | steps 938 | loss 0.5391
[rank 0/2] epoch 0 | snapshot saved to /shared/output/trainjob-gpu-ddp/snapshot.pt
...
[rank 0/2] epoch 19 | steps 938 | loss 0.0331
[rank 0/2] done
```

（`/shared` に Basic02 の CPU 実行のスナップショットが残っていると、`resuming from snapshot at epoch <N>` と出て途中から再開することがあります。出力ディレクトリは `nodeRole` ごとに分かれる設計なので、CPU 版とは別の `trainjob-gpu-ddp` に保存されます。）

## 4. (任意) GPU スモークテストで確認する

Basic01 で紹介したインフラ層のスモークテストには、GPU ノードを実際に起動して確認する `--with-gpu` モードがあります。アクセラレータプールを定義した本章の段階で初めて実行できます。`--with-gpu` は Basic01 の 38 項目をもう一度回したうえで GPU の層を足すので、全部で 43 項目、9 分前後かかります。長いのは Karpenter が GPU ノードを起動する分と、最後の `gpu-serving-vllm` が vLLM を実際にデプロイして API を叩く分です。`--namespace` を明示するのは Basic01 と同じ理由です。このシェルには `NAMESPACE` が入っているので、渡さないとテストが作業 namespace を自分のものと解釈して停止します。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/tests
./run-tests.sh --with-gpu --gpu-count 1 --namespace distai-test
```

GPU の層は 5 項目です。実機出力から該当部分と集計を抜き出します。

```text
[INFO] --- gpu tests ---
pod/gpu-smoke-test created
[OK]   gpu-node-launch (114s)
[OK]   nvidia-smi-check (2s)
job.batch/cuda-vectoradd created
[OK]   cuda-vector-add (16s)
pod/gpu-fsx-mount-test created
[OK]   gpu-fsx-mount (40s)
service/gpu-vllm created
deployment.apps/gpu-vllm created
deployment "gpu-vllm" successfully rolled out
models ok: Qwen/Qwen2.5-0.5B-Instruct
text ok: 'Hello!'
[OK]   gpu-serving-vllm (172s)
--------------------------------------------------------------
PASS: 42  FAIL: 0  SKIP: 1  TOTAL: 43
```

`SKIP` の 1 件は Basic01 と同じ `registry-default-layer-attached` です。テストが作ったものは最後に namespace ごと消えるので、GPU ノードも Pod が無くなった時点で回収に入ります。

対象の NodePool は [`resolve_gpu_nodepool`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/tests/lib/resolve.sh) が NVIDIA GPU のプールから自動選択します（`--gpu-nodepool` で明示指定も可能）。スモーク Pod が要求するのは `nvidia.com/gpu` なので、Neuron のような非 NVIDIA のプールは候補になりません。`--gpu-count` には検証したい GPU 枚数を渡します（g6.2xlarge なら 1、g6e.12xlarge なら 4、p4d.24xlarge なら 8）。GPU テストで ICE（InsufficientInstanceCapacity）により起動できない場合は AWS 側のキャパシティ問題であり、インフラの不具合ではありません。

## 5. 後片付け

本章で起動する GPU ノードは、本書で最初に触れる高額なリソースです。確認が済んだら TrainJob を削除して、ノードが回収されることまで見届けてください。

```bash
k delete trainjob ddp-trainjob
k get nodeclaims -l karpenter.sh/nodepool=gpu-ddp -w
```

Pod が消えると NodePool の `consolidationPolicy` に従って Karpenter がノードを回収します。`gpu-ddp` の NodeClaim が無くなれば GPU の課金は止まります (フィルタを外すと CPU プールの NodeClaim も並ぶので、GPU の判定にはプール名で絞ります)。回収は非同期で数分かかるので、消えるまで待ってから次章に進みます。ここで NodeClaim が残り続けるのは、そのノードにまだ Pod が載っているときです (`karpenter.sh/do-not-disrupt` を付けた Pod が動き続けている場合も含みます)。`k get pods` で確認します (`k` の既定 namespace は Basic01 手順 2 の 4 行で `distai` に設定済みです)。

NodePool 自体は残しておいてかまいません。Karpenter は要求があってからノードを起動するので、プールが存在するだけでは課金されません。Basic07 と Basic08 はこの `gpu-ddp` プールをそのまま使います。

# 今の仕組みの限界

ここまでの `accelerator_pools`（Terraform の map 変数を専用 tfvars ファイルで管理する方式）は、一人ないし信頼できる少人数が同じ Terraform state を触る前提では十分に機能します。一方で、複数チームがひとつのクラスタを共有するマルチテナント運用に持ち込もうとすると、次の限界が見えてきます。

::::details マルチテナントで tfvars 方式では足りなくなる理由
- **ファイル単位の分離ができない**: `accelerator_pools` は単一の map 変数なので、チーム A とチーム B が別ファイルに `accelerator_pools = {...}` を書くと、ファイル名の辞書順で後になるほうが黙って勝ち、もう一方のプール定義は消えます。エラーにならないので気づけません。結局ひとつのファイルを全員で編集することになり、プルリクエストが恒常的にコンフリクトします。
- **RBAC で権限を絞れない**: Terraform state と AWS の認証情報を持つ人は、他チームのプール定義も含めて何でも書き換えられます。「チーム A は自分のプールだけ作れる」「Capacity Block の ID はプラットフォーム管理者が許可したものだけ使える」といった、Kubernetes の RBAC 相当の権限分離を tfvars では表現できません。
- **ノードの分離境界を宣言できない**: あるチームのプールで立てたノードに、他チームの Pod が載らないようにする taint／label の対応関係を、テナント自身が勝手に書き換えられないよう固定する仕組みがありません。tfvars は「誰が書いたか」を区別しないため、境界フィールド（テナント taint や `capacity-type: reserved` のピン留め）を守れません。
- **セルフサービスにならない**: 新しいプールが欲しいたびにプラットフォームチームへ tfvars の編集を依頼する運用になり、Kubernetes のマニフェストを `kubectl apply` する感覚での自助にはなりません。
::::

これらは Terraform の使い方の問題ではなく、「アクセラレータプールの確保」をマルチテナントのセルフサービスとして扱うには、Namespace 単位で分離され RBAC で保護される Kubernetes ネイティブな API（CRD）が要る、という構造的な要請です。本章の tfvars 方式は、まずは単一チームで確実に動かすための出発点と考えてください。今後改善を予定しています。

# まとめ

`accelerator_pools` に 1 エントリ書くだけで、GPU の世代が混在した DDP 環境が立ち上がりました。tfvars 方式は単一チームの検証には十分ですが、マルチテナント運用では RBAC やファイル分離の限界があり、そこは Kubernetes ネイティブな CRD で解く領域だと整理しました。

# 参考資料

- [Karpenter NodePools](https://karpenter.sh/docs/concepts/nodepools/)
- [Amazon EC2 スポットインスタンス](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html)
- [Kubeflow Trainer](https://www.kubeflow.org/docs/components/trainer/)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
- [実験ワークロードのチャート（charts/experiments）](https://github.com/littlemex/distributed-ai/tree/main/infra/eks/charts/experiments)
