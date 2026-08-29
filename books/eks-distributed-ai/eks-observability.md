---
title: "Basic08 - Observability を導入する"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、Basic07 で動かした GPU ワークロードを観測します。kube-prometheus-stack（Prometheus + Grafana）で、NVIDIA GPU Operator に同梱される DCGM exporter が公開する GPU メトリクス（使用率・温度・メモリなど）を Grafana の UI で確認し、あわせてノード障害の検知も見ていきます。

:::message
observability は `var.enable_observability = true`（既定で有効）で `terraform apply` に含まれます。Basic03 以降の apply を済ませていれば、`monitoring` namespace に Prometheus と Grafana がすでに動いています。
:::

# 解説

## 全体構成

本章は、これまで構築・実行してきた基盤全体を「観測する」レイヤーです。GPU ノード上の DCGM exporter がメトリクスを公開し、Prometheus がそれを収集、Grafana が可視化する、という標準的な構成を、Terraform で宣言的に組み込んでいます。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

アクセラレータノード（GPU）で動く DCGM exporter を起点に、`monitoring` namespace の Prometheus へメトリクスが流れ、Grafana がそれを可視化します。監視スタックのうち Prometheus・Grafana・Operator・kube-state-metrics は専用の Karpenter NodePool（`node-role=monitoring`）に常駐し、GPU ノードや system ノードの上では動きません。例外は node-exporter で、こちらは各ノードの指標を採るための DaemonSet なので、アクセラレータノードを含む全ノードに載ります。

## これは何をするものか

GPU を使った分散学習・推論では、「GPU が本当に使われているか」「メモリが溢れていないか」「特定の rank だけ遅れていないか」を把握することが、性能問題やハングの切り分けに直結します。これを可視化するのが本章の目的です。

構成要素は 3 つです。

- **DCGM exporter**: NVIDIA の Data Center GPU Manager が公開する GPU メトリクスを Prometheus 形式で公開する exporter です。NVIDIA GPU Operator に同梱されており、GPU ノードが起動すると各ノードで自動的に動きます
- **Prometheus**: 各 exporter からメトリクスを定期的に収集・時系列データ保持します
- **Grafana**: Prometheus のデータをダッシュボードとして可視化します

Prometheus と Grafana は [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) という Helm チャートでまとめて導入します。このチャートは Prometheus Operator・Grafana・node-exporter・kube-state-metrics・各種 Kubernetes ダッシュボードを一括で入れてくれます。本書では [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) がこのチャートを `helm_release` として管理し、GPU 用の ServiceMonitor・テナント別ダッシュボード・専用 NodePool までを一括で構築します。

## テナントごとに GPU を見る

:::message
マルチテナント機能は現在開発中です。
:::

マルチテナント設計では、チームごとに namespace を分けてダッシュボードを見たいという要求があるはずです。このマルチテナント機能は開発中で全体設計はまだ未完成ですが、現時点でも「どのチームの GPU か」を示す `tenant` ラベルを GPU メトリクスに付けて、Grafana の「GPU Utilization by Tenant」ダッシュボードのドロップダウンで自分の namespace を選ぶだけで自分のテナントの GPU だけを見られる、というところまでは動きます。

ここで「ラベルを付ける側」と「ラベルを使う側」を分けて理解すると、仕組みがはっきりします。

- **付ける側**、つまりノードにラベルを刻むのは observability ではなく、自作中の `karpenter-tenant-pools` CRD の役割です。テナントのプールで起動したノードに `tenantpools.dev/tenant=<namespace>` という**ノードラベル**を付けます。逆に言うと、`karpenter-tenant-pools` を使わずに起動したノードにはこのラベルは付かないので自分でつける必要があります。
- **使う側**、つまりメトリクスに写すのが observability の [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) が作る専用の DCGM ServiceMonitor です。ノードに付いている `tenantpools.dev/tenant` ラベルを読み取り、GPU メトリクスの `tenant` というラベルとして写します。ラベルを新たに生成しているのではなく、既にノードにあるラベルを拾ってメトリクスに転記しているだけです。

ここで ServiceMonitor という言葉が出てきたので、実態を補足します。ServiceMonitor は kube-prometheus-stack に含まれる Prometheus Operator が用意する Kubernetes の CRD で、平たく言えば **Prometheus に対する「どの Service を、どのポートで、何秒間隔で収集し、収集したメトリクスにどんなラベルを足すか」を宣言する収集指示書**です。設定を書かない Prometheus は設定ファイルに収集対象を静的に書きますが、Prometheus Operator はこの ServiceMonitor という Kubernetes リソースを見て収集設定を自動生成します。

したがって本章で「自前の ServiceMonitor」と呼んでいるのは、メトリクスを公開する dcgm-exporter は GPU Operator 同梱のものをそのまま使い、その exporter を **どう収集するか**という指示書だけを自分で書いた、という意味です。GPU Operator も標準の ServiceMonitor を出せますが、そこには後述の `attachMetadata` を指定できず `tenant` ラベルを写せないため、標準のものを無効化して、必要な relabeling を仕込んだ指示書に置き換えています。

つまり `tenant` ラベルの値は「付ける側」次第です。`karpenter-tenant-pools` でノードを起動していればそのメトリクスに `tenant=<namespace>` が入り、そうでなければ写す元が無いので `tenant` は空になります。

この「写す」を実現しているのが ServiceMonitor の `attachMetadata.node: true` と relabeling です。`attachMetadata.node: true` はスクレイプ対象のノードラベルを収集時のメタデータに載せる設定で、relabeling はそのメタデータに現れた `tenantpools.dev/tenant` を `tenant` というメトリクスラベルへコピーする処理です。GPU Operator 標準の ServiceMonitor では `attachMetadata` を設定できず、ノードラベルが収集メタデータに載らないためこのコピーが成立しません。そこで [`gpu-addons.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/gpu-addons.tf) 側では `dcgmExporter.serviceMonitor.enabled = false` として標準の ServiceMonitor を無効化し、observability.tf の自前 ServiceMonitor に一本化しています。

このとき自前 ServiceMonitor には、GPU Operator が使う名前 `nvidia-dcgm-exporter` をそのまま使ってはいけません。`dcgmExporter.serviceMonitor.enabled = false` の状態では、GPU Operator は「その名前の ServiceMonitor は存在すべきでない」と判断し、reconcile のたびに同名の ServiceMonitor を削除します。GPU ノードが入れ替わって GPU Operator が再度 reconcile するたびに自前 ServiceMonitor が消え、`DCGM_FI_DEV_GPU_UTIL` の系列がゼロになる、という分かりにくい事象に何度も遭遇しました。そこで自前側は `nvidia-dcgm-exporter-tenant` という別名にして、GPU Operator の管理対象から外しています。

```hcl
relabelings = [
  {
    # ノードラベル tenantpools.dev/tenant を tenant メトリクスラベルへコピーする
    action       = "replace"
    sourceLabels = [local.tenant_meta_label] # 既定なら __meta_kubernetes_node_label_tenantpools_dev_tenant
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

データ自体は 1 つの Prometheus に集約されるため、他テナントのデータも「見ようと思えば」見えます。強制的なデータ分離（テナントごとにクエリを分離する `prom-label-proxy` や Grafana の folder permission、Thanos/Mimir によるストレージ分離）は今後の課題として、まずは「ラベル `tenant` を分離の契約点として確立する」ところまでを作りました。

## 監視スタックの置き場所

Prometheus は PVC を持つ常駐の stateful なコンポーネントで、GPU ノードや system ノードには載せません。理由は 2 つあります。

- **system ノードには載せない**: system の managed nodegroup は「Karpenter が落ちてもクラスタが復旧できるための最小構成（kube-system と Karpenter controller）」だけを置く聖域です。Prometheus のような重い常駐物をここに載せると、この聖域の予測可能性が崩れます
- **GPU ノードには載せない**: GPU ノードは Capacity Block の期限やワークロード終了で消えるため、監視ごと消えてしまいます

そこで [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) は監視専用の Karpenter NodePool（`node-role=monitoring`）を作り、監視スタックをそこに固定します。この NodePool は `consolidationPolicy: WhenEmpty` で、Pod が完全に無くなったときだけノードを回収します。なおこのプールに taint は付けていません。`nodeSelector` は監視 Pod をこのノードに寄せる働きしかしないので、`nodeSelector` を持たない Pod がスケジューラの都合でこのノードに同居することは防げません。taint を付けない代わりに、監視スタックのリソース要求で必要分を確保する形にしています。

## ノード障害を検知する

メトリクスの可視化とあわせて、この基盤には EKS の Node Monitoring Agent（NMA）も組み込んであります。NMA は各ノードの GPU・カーネル・ネットワークなどの健全性を監視し、NodeCondition 結果を書き込むエージェントです。この NodeCondition は kube-state-metrics 経由で Prometheus に流れるので、GPU 障害を検知して、これまでと同じ Prometheus/Grafana の仕組みでアラート条件として扱えます。ただしこの基盤は Alertmanager を無効にしてあります（通知先を決めずに有効化しても通知を捨てるだけなので）。発火は Prometheus UI の Alerts で見える状態までで、Slack やメールへ飛ぶわけではありません。通知が必要になったら Alertmanager を有効にして通知先を設定します。[`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) には、そのためのアラートルールもあらかじめ入れてあります。

NMA は障害を「検知して知らせる」だけで、Karpenter によるノードの自動修復（auto-repair、不健全なノードを自動で terminate して置き換える機能）は意図的に無効にしています。高価な GPU ノードを止める・置き換えるという不可逆な判断は、人間やジョブ層に委ねる方針です。

NMA を GPU ノードで正しく動かすための設定、実際に GPU 障害を注入して検知が働くことをどう確かめるか、auto-repair を無効にした詳しい理由といった踏み込んだ話は、本書では扱いません。本章のワークショップでは、監視スタックと NMA がすでに動いていることの確認までを行います。

# ワークショップ実施

## 1. 前提を確認する

- `terraform apply` を実行済みであること
- Basic04 で NVIDIA GPU Operator 導入済み、Basic07 で vLLM サーバー起動済みであること
- `k` と `KUBECONFIG` が Basic01 手順 2 の 4 行で設定済みであること
- `jq` 導入済み

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

エージェント本体（`eks-node-monitoring-agent`）が全ノード分、GPU 健全性を読む `dcgm-server` が GPU ノード分だけ `READY` になっていれば、GPU 障害の検知経路が立ち上がっています。`dcgm-server` の `DESIRED` が 0 のときは、まず `k get nodes -L node-role` で GPU ノードが存在するかを確認してください。GPU ノードが無ければ 0 は正常です。GPU ノードがあるのに 0 のままなら、GPU ノードの taint への toleration が効いていない可能性があります。`k describe daemonset dcgm-server -n kube-system` の Events と、GPU ノードの taint を `k get nodes -o json` で突き合わせてください。

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

各系列に `node` ラベルが付いており、これは前掲の `__meta_kubernetes_node_name` を `node` に変換する relabeling が効いていることを示します。一方 `tenant` ラベルは、そのノードがテナント単位の NodePool で起動された場合にだけ namespace 名が入ります（先に触れた `karpenter-tenant-pools` を使ってテナントごとにプールを切る構成のことで、本書では Experiment01 として扱います）。Basic04/Basic07 で使う通常の GPU プール（`gpu-ddp`）のノードにはテナントラベルが無いため `(none)` になります。したがって tenant relabeling が実際に値を刻む様子と「GPU Utilization by Tenant」ダッシュボードでのテナント絞り込みは、Experiment01 のテナントプールでノードを起動して初めて確認できます。本手順の環境では tenant は `(none)` のみになる点に注意してください。

系列数は、クラスタに存在する GPU 総数（dcgm-exporter が動く GPU ノード上の GPU 枚数）と一致します。使用していない GPU も値 0 の系列として出ます。GPU ノードが 1 台も無ければ 0 系列になるので、その場合は `k get nodes -L node-role` で GPU ノードの有無を先に確認してください。なお GPU ノードが立った直後は、GPU Operator の dcgm-exporter が起動して初回 scrape が回るまで数分かかり、その間は 0 系列に見えることがあります。

DCGM の ServiceMonitor が Prometheus のターゲットとして認識されているかも確認できます。

```bash
curl -s "http://localhost:9090/api/v1/targets" \
  | jq -r '.data.activeTargets[] | select(.labels.job=="nvidia-dcgm-exporter") | "\(.labels.node)\t\(.health)"'
```

各 GPU ノードの dcgm-exporter が `up` であれば scrape に成功しています。

## 4. Grafana の UI にアクセスする

Grafana の admin パスワードは Terraform が自動生成し、Secret に保存しています。読者が値を手で書く場所はどこにもなく、`terraform output` で取り出します。ただし Terraform が生成した値なので tfstate には平文で入ります。Basic01 で state を S3 に置き、暗号化した状態で扱っているのはこのためです。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
terraform output -raw grafana_admin_password

k port-forward -n monitoring svc/kps-grafana 3000:80 &
for i in $(seq 1 30); do curl -sf http://localhost:3000/api/health >/dev/null && break; sleep 1; done
curl -sf http://localhost:3000/api/health >/dev/null \
  && echo "ブラウザで http://localhost:3000 を開く（ユーザー: admin / パスワード: 上で表示した値）" \
  || echo "port-forward が確立できていません。jobs と lsof -i:3000 で 3000 の占有を確認してください"
```

アクセス手順は `terraform output grafana_access` にもワンライナーで出力されます。

ログイン後、左メニューの Dashboards を開くと、kube-prometheus-stack が自動導入した Kubernetes 向けダッシュボード群に加えて、本書が配布したダッシュボードが表示されます。`GPU` フォルダには自作の「GPU Utilization by Tenant」と NVIDIA 公式の「NVIDIA DCGM Exporter Dashboard」、`Nodes` フォルダにはノードの CPU・メモリ・ディスク・ネットワークを網羅する「Node Exporter Full」が入っています。いずれも `terraform apply` で自動的に配置され、手動インポートは不要です。

「GPU Utilization by Tenant」ダッシュボードの上部にある `Tenant` ドロップダウンで自分の namespace を選ぶと、そのテナントの GPU だけの使用率・メモリ・SM クロック・温度・電力が表示されます。設定ファイルもクエリも書く必要はなく、ドロップダウンを選ぶだけです。`http://localhost:3000/d/gpu-tenant?var-tenant=team-a` のように URL に `var-tenant` を付ければ、選択済みの状態で共有もできます。

ダッシュボードがどう見えるかの参考として実画面を載せます。以下は、今回のワークショップ手順の A10G 1 枚構成とは異なり、Capacity Block で確保した p5en.48xlarge（H200 x8）の 1 ノードで GPU ワークロードを流しているときのものです。8 枚の GPU（GPU 0〜7）それぞれの温度・電力・SM クロック・使用率が個別に可視化されているのが読み取れます。

![NVIDIA DCGM Exporter Dashboard で p5en の 8 GPU を可視化した実画面](/images/books/eks-distributed-ai/ch7-dcgm-grafana.png)

これで、GPU ワークロードの実行中に各 GPU がどれだけ使われているかを、時系列グラフで観測できます。Basic07 の vLLM 推論であれば 1 枚の GPU 使用率が、Capacity Block で確保したマルチノード学習であれば全 GPU の使用率が、それぞれ可視化されます。

手順 3・4 でバックグラウンド（`&`）に起動した port-forward は、ターミナルを閉じるまで残り続けます。確認が終わったら `jobs` でジョブ番号を確認し、`kill %<番号>` で止めておきます（放置すると次の再実行時に 9090 / 3000 が使用中で port-forward が失敗します）。

:::message
**EFA のネットワークメトリクスは現時点では観測対象に含めていません。** EFA の帯域や RDMA の統計を Prometheus に取り込むには専用の EFA exporter が別途必要ですが、この基盤にはまだ導入しておらず、今後の対応予定です。**Neuron の Observability についても現状では未対応**で、今後対応予定です。
:::

## 5. observability を無効化する（任意）

observability が不要なクラスタでは、`terraform.tfvars` で無効化できます。

```hcl
enable_observability = false
```

この状態で `terraform apply` すると、kube-prometheus-stack・専用 NodePool・自前 DCGM ServiceMonitor・アラートルール・`monitoring` namespace （その中身ごと）といった observability 関連リソースがまとめて削除されます。`gp3` StorageClass は `observability_storage_class_create` が `true` のとき（既定）だけ削除対象で、既存のクラスを流用する設定にしていれば Terraform の管理外なので残ります。逆に、この構成が作った `gp3` を他のワークロードが使い始めていた場合、observability の無効化でそのクラスも消える点には注意してください。クラス名がクラスタ共通の名前なので起きうる巻き込みです。NMA は別の変数で制御しているため、これを止めるには `enable_node_monitoring_agent = false` も併せて設定します。GPU Operator 側の DCGM ServiceMonitor はもともと無効（`dcgmExporter.serviceMonitor.enabled = false`）のままで、こちらの変更は不要です。

:::message alert
GPU 障害の検知を自分で試す予定があるなら、本章の NMA とアラートルールが動いていることが前提になるので、この無効化は行わないでください。
:::

# まとめ

本章では、GPU Operator 同梱の DCGM exporter が公開する GPU メトリクスを Prometheus で収集、Grafana の UI で可視化しました。observability は `terraform apply` に含まれるため追加の導入操作は不要で、Prometheus/Grafana はすでにクラスタ上で動いています。ポイントは、GPU メトリクスに収集時点で `tenant` ラベルを刻印し、Grafana のドロップダウンでテナントごとに GPU を見られるようにしたこと、そして監視スタックを専用 NodePool・自動生成パスワード・専用 gp3 StorageClass で「宣言的に一発で立ち上がる」形に組み込んだことです。

# 参考資料

- [kube-prometheus-stack (Helm chart)](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
- [NVIDIA DCGM Exporter](https://github.com/NVIDIA/dcgm-exporter)
- [NVIDIA DCGM Exporter Dashboard (Grafana ID 12239)](https://grafana.com/grafana/dashboards/12239-nvidia-dcgm-exporter-dashboard/)
- [Node Exporter Full (Grafana ID 1860)](https://grafana.com/grafana/dashboards/1860-node-exporter-full/)
- [Prometheus Operator ServiceMonitor](https://prometheus-operator.dev/docs/getting-started/design/#servicemonitor)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
