---
title: "Basic08 - Observability を導入する"
free: true
---

本章では、Basic07 で動かした GPU ワークロードを「見える化」します。kube-prometheus-stack（Prometheus + Grafana）で、NVIDIA GPU Operator に同梱される DCGM exporter が公開する GPU メトリクス（使用率・温度・メモリなど）を Grafana の UI で確認し、あわせてノード障害の検知も見ていきます。

:::message
observability は `var.enable_observability = true`（既定で有効）で `terraform apply` に含まれます。Basic03 以降の apply を済ませていれば、`monitoring` namespace に Prometheus と Grafana がすでに立っています。observability の導入は Terraform（`terraform apply`）が自動で行うため、本章では手動でのインストール作業はなく、すでに導入されたものを確認して使っていきます。observability が追加される前のバージョンで apply 済みの場合は、リポジトリを `git pull` してから `cd infra/eks && terraform init -upgrade && terraform apply` を再実行してください（新しい provider が増えているため `init -upgrade` が要ります）。`monitoring` namespace・専用 NodePool・kube-prometheus-stack・NMA などが追加されます。
:::

# 解説

## 全体構成

本章は、これまで構築・実行してきた基盤全体を「観測する」レイヤーです。GPU ノード上の DCGM exporter がメトリクスを公開し、Prometheus がそれを収集、Grafana が可視化する、という標準的な構成を、Terraform で宣言的に組み込んでいます。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

アクセラレータノード（GPU）で動く DCGM exporter を起点に、`monitoring` namespace の Prometheus へメトリクスが流れ、Grafana がそれを可視化します。監視スタックは専用の Karpenter NodePool（`node-role=monitoring`）に常駐し、GPU ノードや system ノードとは分離しています。

## これは何をするものか

GPU を使った分散学習・推論では、「GPU が本当に使われているか」「メモリが溢れていないか」「特定の rank だけ遅れていないか」を把握することが、性能問題やハングの切り分けに直結します。これを可視化するのが本章の目的です。

構成要素は 3 つです。

- **DCGM exporter**: NVIDIA の Data Center GPU Manager が公開する GPU メトリクス（使用率 `DCGM_FI_DEV_GPU_UTIL`、温度、メモリ使用量、電力など）を Prometheus 形式で公開する exporter です。Basic04 で導入した NVIDIA GPU Operator に同梱されており、GPU ノードが立つと各ノードで自動的に動きます
- **Prometheus**: 各 exporter からメトリクスを定期的に収集（scrape）し、時系列データとして保持します
- **Grafana**: Prometheus のデータをダッシュボードとして可視化します

Prometheus と Grafana は [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) という Helm チャートでまとめて導入します。このチャートは Prometheus Operator・Grafana・node-exporter・kube-state-metrics・各種 Kubernetes ダッシュボードを一括で入れてくれるため、EKS の observability の定番です。この book では [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) がこのチャートを `helm_release` として管理し、GPU 用の ServiceMonitor・テナント別ダッシュボード・専用 NodePool までを一括で構築します。

## テナントごとに GPU を見る

本 book のマルチテナント設計（Experiment01 の `karpenter-tenant-pools`）では、各チームのノードに `tenantpools.dev/tenant=<namespace>` というラベルが付きます。observability はこのラベルを収集時に GPU メトリクスへ刻印し、Grafana の「GPU Utilization by Tenant」ダッシュボードのドロップダウンで自分の namespace を選ぶだけで、自分のテナントの GPU だけを見られるようにしています。

これを実現するのが、[`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) が作る専用の DCGM ServiceMonitor です。`attachMetadata.node: true` でノードラベルを収集メタデータに載せ、relabeling で `tenantpools.dev/tenant` ラベルを `tenant` というメトリクスラベルに変換します。GPU Operator 標準の ServiceMonitor では `attachMetadata` を設定できず、ノードラベルが収集メタデータに載らないためこの relabeling が成立しません。そこで [`gpu-addons.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/gpu-addons.tf) 側では `dcgmExporter.serviceMonitor.enabled = false` として標準の ServiceMonitor を無効化し、observability.tf の自前 ServiceMonitor に一本化しています。

```hcl
relabelings = [
  {
    # ノードラベル tenantpools.dev/tenant を tenant メトリクスラベルに変換する
    action       = "replace"
    sourceLabels = ["__meta_kubernetes_node_label_tenantpools_dev_tenant"]
    targetLabel  = "tenant"
  },
  {
    # どのノードの GPU かを示す node ラベルも付与する
    action       = "replace"
    sourceLabels = ["__meta_kubernetes_node_name"]
    targetLabel  = "node"
  },
]
```

データ自体は 1 つの Prometheus に集約されるため、他テナントのデータも「見ようと思えば」見えます。強制的なデータ分離（テナントごとにクエリを分離する `prom-label-proxy` や Grafana の folder permission、Thanos/Mimir によるストレージ分離）は今後の課題として、まずは「ラベル `tenant` を分離の契約点として確立する」ところまでを本章の範囲とします。

## 監視スタックの置き場所

Prometheus は PVC を持つ常駐の stateful なコンポーネントで、GPU ノードや system ノードには載せません。理由は 2 つあります。

- **system ノードには載せない**: system の managed nodegroup は「Karpenter が落ちてもクラスタが復旧できるための最小構成（kube-system と Karpenter controller）」だけを置く聖域です。Prometheus のような重い常駐物をここに載せると、この聖域の予測可能性が崩れます
- **GPU ノードには載せない**: GPU ノードは Capacity Block の期限やワークロード終了で消えるため、監視ごと消えてしまいます

そこで [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) は監視専用の Karpenter NodePool（`node-role=monitoring`）を作り、監視スタックをそこに固定します。この NodePool は `consolidationPolicy: WhenEmpty` で、Pod が完全に無くなったときだけノードを回収します。この設計には理由があり、詳しくは後述の「重要な注意点」で説明します。

## ノード障害を検知する

メトリクスの可視化とは別に、この基盤では EKS の Node Monitoring Agent（NMA）を有効化しています。NMA は各ノードで動くエージェントで、カーネル・コンテナランタイム・ネットワーク・ストレージ・アクセラレータの健全性を監視し、その結果を `KernelReady` や `AcceleratedHardwareReady` といった NodeCondition としてノードに書き込みます。GPU の場合は同梱の DCGM（Data Center GPU Manager）経由で XID エラー・訂正不可能な ECC エラー・NVLink 障害を検知します。dcgm-exporter が使用率などの連続的なメトリクスを流すのに対し、NMA は「このノードは健全か否か」という機械可読な単一の判定を出す点が異なります。

なお NMA は EKS アドオンではなく Helm チャートで導入しています。GPU ノードに taint を付けているこの基盤では、アドオン版では GPU 健全性を読むコンポーネントが GPU ノードに載れず検知が沈黙してしまうためで、この理由は Advanced03 で詳しく扱います。

この判定は kube-state-metrics 経由で Prometheus に流れるため、`kube_node_status_condition{condition="AcceleratedHardwareReady"}` のようなクエリでそのままアラートを組めます。この基盤では [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) が、GPU 障害を critical、その他の NodeCondition を warning、そして NMA 自身の DaemonSet がダウンした場合を warning とするアラートルールをあらかじめ入れてあります。dcgm-exporter のメトリクスと同じ Grafana に並べれば、NMA が異常と判定した瞬間に GPU で何が起きていたかを 1 画面で相関できます。

なぜこれを入れるかというと、分散学習の最悪のシナリオは「1 枚の GPU が故障し、NCCL の集合通信が全ランクでハングしたまま、高価な Capacity Block の時間課金だけが無言で溶けていく」ことだからです。NMA を入れると、この故障が数十秒から数分で NodeCondition として立ち上がり、人間がダッシュボードで気づくよりはるかに早く検知できます。実際に GPU 障害を擬似的に注入して、NMA が `AcceleratedHardwareReady` を `False` に反転させアラートが発火するところまでを試す手順は、Advanced03「GPU 障害を注入して検知を確かめる」で扱います。

ここで重要なのは、**検知だけを有効化し、Karpenter のノード自動修復（auto-repair）は有効化していない**という点です。auto-repair は不健全なノードを自動で terminate して置き換える機能ですが、この基盤の tightly-coupled な同期集合通信では、故障ノードを自動 terminate すると、まだ調査できたはずの高価なノードを人間の確認なしに潰したり、Capacity Block の同じ故障ホストを引き直して terminate と再起動を繰り返すループに陥る危険があります。そのため、ノードを止める・置き換えるという不可逆な判断は人間やジョブ層に委ね、NMA には「早く正確に知らせる」役割だけを持たせています。この判断の詳細な根拠は [AWS の解説記事](https://aws.amazon.com/jp/blogs/containers/under-the-hood-how-amazon-eks-auto-mode-detects-repairs-and-diagnoses-node-failures/) が検知・修復・診断の仕組みを詳しく説明しているので、あわせて参照してください。

# ワークショップ実施

## 1. 前提を確認する

- Basic03 以降で `terraform apply` を実行済みであること。observability は `var.enable_observability = true`（既定）で apply に含まれるため、この時点で `monitoring` namespace に監視スタックがすでに立っています。
- Basic04 で NVIDIA GPU Operator が導入済みであること。本章は GPU Operator が同梱する DCGM exporter を可視化します。
- `k` エイリアスと `KUBECONFIG` / `--context` は Basic01 で設定済みであること。
- GPU メトリクスに 0 以外の値が出るのは、GPU ノードが実際に GPU を使っているときです。Basic05 の Capacity Block や Basic07 の vLLM を稼働させたまま本章に進むと、手順 4 で実際の使用率を確認できます。
- 手順 3 で `python3`（JSON のパースに使用）を、手順 3 の後半（Prometheus の targets 確認）で `jq` を使います。この targets 確認だけは `jq` のフィルタ式が本体なので、`python3 -m json.tool`（整形のみ）では代替できません。手元に無い場合は `dnf install -y python3 jq` などで入れておきます。

## 2. 監視スタックが動いていることを確認する

まず `monitoring` namespace の Pod を確認します。

```bash
k get pods -n monitoring
```

Prometheus・Grafana・kube-prometheus-stack operator・kube-state-metrics・node-exporter の Pod が `Running` になります（実機出力です。Pod 名の suffix はチャートバージョンや環境で変わります）。

```text
NAME                                      READY   STATUS    RESTARTS   AGE
kps-grafana-xxxxxxxxxx-xxxxx              3/3     Running   0          29m
kps-kube-state-metrics-xxxxxxxxxx-xxxxx   1/1     Running   0          29m
kps-operator-xxxxxxxxxx-xxxxx             1/1     Running   0          29m
kps-prometheus-node-exporter-xxxxx        1/1     Running   0          29m
prometheus-kps-0                          2/2     Running   0          29m
```

監視スタックが `node-role=monitoring` の専用ノードに載っていることも確認できます。

```bash
k get pods -n monitoring -o wide --no-headers \
  | grep -E 'grafana|prometheus-kps-0|operator|state-metrics' \
  | awk '{print $1, $7}'
k get nodes -L node-role | grep monitoring
```

`node-exporter` だけは DaemonSet なので、GPU ノードや system ノードを含む全ノードに 1 つずつ載ります（これは正常です）。

ノード障害検知の NMA も動いていることを確認します。

```bash
k get ds -n kube-system eks-node-monitoring-agent dcgm-server
```

エージェント本体（`eks-node-monitoring-agent`）が全ノード分、GPU 健全性を読む `dcgm-server` が GPU ノード分だけ `READY` になっていれば、GPU 障害の検知経路が立ち上がっています。`dcgm-server` の `DESIRED` が 0 のときは、まず `k get nodes -L node-role` で GPU ノードが存在するかを確認してください。GPU ノードが無ければ 0 は正常です。GPU ノードがあるのに 0 のままなら、GPU ノードの taint への toleration が効いていない可能性があり、その原因と対処は Advanced03 で扱います。

## 3. GPU メトリクスが収集されているか確認する

Grafana を見る前に、DCGM の GPU メトリクスが Prometheus に届いているかをコマンドで確認します。ここが噛み合っていないと Grafana のパネルも空になるため、先に切り分けておきます。

```bash
k port-forward -n monitoring svc/kps-prometheus 9090:9090 &
for i in $(seq 1 30); do curl -sf http://localhost:9090/-/ready >/dev/null && break; sleep 1; done
curl -sf http://localhost:9090/-/ready >/dev/null \
  || echo "port-forward が確立できていません（この後のクエリもエラーになります）。jobs で状態を、lsof -i:9090 で 9090 の占有を確認し、解消してからやり直してください"
curl -s "http://localhost:9090/api/v1/query?query=DCGM_FI_DEV_GPU_UTIL" \
  | python3 -c "
import json, sys
r = json.load(sys.stdin)['data']['result']
print('系列数:', len(r))
for s in r[:8]:
    m = s['metric']
    print(f\"  tenant={m.get('tenant','') or '(none)'}  node={m.get('node','?')}  gpu={m.get('gpu','?')}\")
"
```

実機出力（Basic07 の vLLM が `gpu-ddp` プールの GPU 1 枚で動いている状態）:

```text
系列数: 1
  tenant=(none)  node=ip-10-0-8-26.us-east-2.compute.internal  gpu=0
```

各系列に `node` ラベルが付いており、これは前掲の `__meta_kubernetes_node_name` を `node` に変換する relabeling が効いていることを示します。一方 `tenant` ラベルは、そのノードが Experiment01 のテナントプールで起動された場合にだけ namespace 名が入ります。Basic04/Basic07 で使う通常の GPU プール（`gpu-ddp`）のノードにはテナントラベルが無いため `(none)` になります。したがって tenant relabeling が実際に値を刻む様子と「GPU Utilization by Tenant」ダッシュボードでのテナント絞り込みは、Experiment01 のテナントプールでノードを起動して初めて確認できます。本手順の環境では tenant は `(none)` のみになる点に注意してください。

系列数は、クラスタに存在する GPU 総数（dcgm-exporter が動く GPU ノード上の GPU 枚数）と一致します。使用していない GPU も値 0 の系列として出ます。GPU ノードが 1 台も無ければ 0 系列になるので、その場合は `k get nodes -L node-role` で GPU ノードの有無を先に確認してください。なお GPU ノードが立った直後は、GPU Operator の dcgm-exporter が起動して初回 scrape が回るまで数分かかり、その間は 0 系列に見えることがあります。

DCGM の ServiceMonitor が Prometheus のターゲットとして認識されているかも確認できます。

```bash
curl -s "http://localhost:9090/api/v1/targets" \
  | jq -r '.data.activeTargets[] | select(.labels.job=="nvidia-dcgm-exporter") | "\(.labels.node)\t\(.health)"'
```

各 GPU ノードの dcgm-exporter が `up` であれば scrape に成功しています。

## 4. Grafana の UI にアクセスする

Grafana の admin パスワードは Terraform が自動生成し、Secret に保存しています。平文でどこかに書く必要はなく、`terraform output` で取り出します。

```bash
cd infra/eks
terraform output -raw grafana_admin_password    # パスワードを表示

k port-forward -n monitoring svc/kps-grafana 3000:80 &
for i in $(seq 1 30); do curl -sf http://localhost:3000/api/health >/dev/null && break; sleep 1; done
curl -sf http://localhost:3000/api/health >/dev/null \
  && echo "ブラウザで http://localhost:3000 を開く（ユーザー: admin / パスワード: 上で表示した値）" \
  || echo "port-forward が確立できていません。jobs と lsof -i:3000 で 3000 の占有を確認してください"
```

アクセス手順は `terraform output grafana_access` にもワンライナーで出力されます。

ログイン後、左メニューの Dashboards を開くと、kube-prometheus-stack が自動導入した Kubernetes 向けダッシュボード群に加えて、`GPU` フォルダに本 book が配布した「GPU Utilization by Tenant」ダッシュボードが表示されます。

このダッシュボードの上部にある `Tenant` ドロップダウンで自分の namespace を選ぶと、そのテナントの GPU だけの使用率・メモリ・SM クロック・温度・電力が表示されます。設定ファイルもクエリも書く必要はなく、ドロップダウンを選ぶだけです。`http://localhost:3000/d/gpu-tenant?var-tenant=team-a` のように URL に `var-tenant` を付ければ、選択済みの状態で共有もできます。

GPU がまとまった枚数で動くとダッシュボードがどう見えるかの参考として、別環境での実画面を載せます。以下は、この手順の A10G 1 枚構成とは異なり、Capacity Block で確保した p5en.48xlarge（H200 x8）の 1 ノードで GPU ワークロードを流しているときのもので、ダッシュボードも Grafana に別途インポートした「NVIDIA DCGM Exporter Dashboard」です。8 枚の GPU（GPU 0〜7）それぞれの温度・電力・SM クロック・使用率が個別に可視化され、使用率が最大 100 %、SM クロックが最大 1.98 GHz、電力が 1 枚あたり最大約 690 W に達していることが読み取れます。

![NVIDIA DCGM Exporter Dashboard で p5en の 8 GPU を可視化した実画面](/images/books/eks-distributed-ai/ch7-dcgm-grafana.png)

これで、GPU ワークロードの実行中に各 GPU がどれだけ使われているかを、時系列グラフで観測できます。Basic07 の vLLM 推論であれば 1 枚の GPU 使用率が、Capacity Block で確保したマルチノード学習であれば全 GPU の使用率が、それぞれ可視化されます。

手順 3・4 でバックグラウンド（`&`）に起動した port-forward は、ターミナルを閉じるまで残り続けます。確認が終わったら `jobs` でジョブ番号を確認し、`kill %<番号>` で止めておきます（放置すると次の再実行時に 9090 / 3000 が使用中で port-forward が失敗します）。

## 5. observability を無効化する（任意）

observability が不要なクラスタでは、`terraform.tfvars` で無効化できます。

```hcl
enable_observability = false
```

この状態で `terraform apply` すると、kube-prometheus-stack・専用 NodePool・gp3 StorageClass・自前 DCGM ServiceMonitor・アラートルールといった observability 関連リソースがまとめて削除されます。NMA は別の変数で制御しているため、これを止めるには `enable_node_monitoring_agent = false` も併せて設定します。GPU Operator 側の DCGM ServiceMonitor はもともと無効（`dcgmExporter.serviceMonitor.enabled = false`）のままで、こちらの変更は不要です。

:::message alert
次章の Advanced03 は本章の NMA とアラートルールが動いていることを前提にします。Advanced03 を実施する予定があるなら、この無効化は行わないでください。
:::

# 重要な注意点

本章の observability を実運用・改変する際に踏みやすい落とし穴を挙げます。いずれも実際に動かして確認した事実に基づきます。

:::message alert
**監視スタックは専用 NodePool（`node-role=monitoring`, `WhenEmpty`）に載せます。共有 CPU プールに相乗りさせてはいけません。**
Prometheus と Grafana は PVC を持つ常駐の stateful なコンポーネントです。共有 CPU プールは idle ノードを短時間（この book では `consolidateAfter: 30s`）で回収する設定なので、ここに載せると再スケジュールや rollout で一時的に Pod が退いた隙にノードが回収され、PVC の再アタッチ待ちや監視の穴が生じます。専用 NodePool を `consolidationPolicy: WhenEmpty` にすると、Pod が完全に無くなったときだけ回収されるため、この隙間が生まれません。「常駐する層である」ことは Pod のアノテーションではなく NodePool の回収ポリシーで表現するのが正解です。

これは Helm でのインストール時にも効きます。kube-prometheus-stack は本体 Pod の前に短命の Job（証明書生成などの Helm フック）を起動することがあり、それが完了してノードが約 30 秒で回収されると本体 Pod が載る前にノードが消えてインストールが途中で止まります。この book では後述のとおり admission webhook 自体を無効化してこの Job を減らしていますが、`WhenEmpty` の専用 NodePool なら本体 Pod がスケジュールされるまでノードが保持されるので、フックの有無に関わらず安全です。
:::

:::message alert
**Grafana の admin パスワードは Terraform が自動生成します。本文に平文で書いたりプレースホルダを埋めたりしないでください。**
[`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) は `random_password` でパスワードを生成し、Kubernetes Secret に格納して Grafana に渡します。取得は `terraform output -raw grafana_admin_password` です。ただしこのパスワードは Terraform の state に平文で保存されるため、state は必ず暗号化されたバックエンド（S3 + SSE-KMS など）と最小権限の IAM で保護してください。
:::

:::message
**PVC 用に gp3 StorageClass を用意しています。**
EKS のクラスタには既定で in-tree の `gp2` StorageClass しか無いことがあります（本 book の検証クラスタもそうでした）。observability.tf は EBS CSI ドライバの `gp3` StorageClass を（既定クラスタを変えずに）作成し、Prometheus/Grafana の PVC がそれを使います。この StorageClass は `volumeBindingMode: WaitForFirstConsumer` で定義しているため、Karpenter が Pod の載る AZ にノードを起こし、その AZ にボリュームが作られます。「PVC が別 AZ に固定されてノードが起動しない」という事故を避けられます。
:::

:::message
**Experiment01 のマルチテナント VAP を導入している場合、`monitoring` namespace は除外対象にします。**
`karpenter-tenant-pools` の ValidatingAdmissionPolicy は「Pod は自分のテナントの taint しか tolerate できない」というルールを課します。監視スタックの node-exporter は DaemonSet で全ノードに載るためにあらゆる taint を tolerate する必要があり、このルールと衝突します。observability.tf は `monitoring` namespace に `tenantpools.dev/excluded=true` ラベルを付けて VAP の対象から外しています。監視はテナントのワークロードではなく基盤コンポーネントなので、この扱いが正しく、Experiment01 を導入していない環境ではこのラベルは単に無視されます。
:::

:::message
**Prometheus の admission webhook は無効化しています。**
kube-prometheus-stack の admission webhook は Helm のインストール時フックとして証明書生成 Job を動かしますが、これが特定の Helm バージョンでインストールを止めてしまう問題があるため、本 book では `admissionWebhooks.enabled = false` にしています。この webhook が担うのは `PrometheusRule` などの apply 時の早期検証だけで、CRD のスキーマ検証は Kubernetes API 側で常に効き、Prometheus Operator も不正な rule を reconcile 時に弾いて処理を続けるため、共有基盤全体が壊れることはありません。将来テナントが独自の rule を投入する運用を始める際は、cert-manager 経由（`admissionWebhooks.certManager.enabled = true`）で再有効化できます。
:::

# まとめ

本章では、`infra/eks` にデフォルトで組み込まれた observability を確認し、GPU Operator 同梱の DCGM exporter が公開する GPU メトリクスを Prometheus で収集、Grafana の UI で可視化しました。observability は `terraform apply` に含まれるため追加の導入操作は不要で、Prometheus/Grafana はすでにクラスタ上で動いています。ポイントは、GPU メトリクスに収集時点で `tenant` ラベルを刻印し、Grafana のドロップダウンでテナントごとに GPU を見られるようにしたこと、そして監視スタックを専用 NodePool・自動生成パスワード・専用 gp3 StorageClass で「宣言的に一発で立ち上がる」形に組み込んだことです。強制的なテナント分離は今後の課題として、まずは `tenant` ラベルを分離の契約点として確立しました。

# 参考資料

- [kube-prometheus-stack (Helm chart)](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
- [NVIDIA DCGM Exporter](https://github.com/NVIDIA/dcgm-exporter)
- [NVIDIA DCGM Exporter Dashboard (Grafana ID 12239)](https://grafana.com/grafana/dashboards/12239-nvidia-dcgm-exporter-dashboard/)
- [Prometheus Operator ServiceMonitor](https://prometheus-operator.dev/docs/getting-started/design/#servicemonitor)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
