---
title: "Basic08 - Observability (Prometheus + Grafana で GPU を可視化する)"
free: true
---

本章では、Basic07 で動かした GPU ワークロードを「見える化」します。kube-prometheus-stack（Prometheus + Grafana）を導入し、NVIDIA GPU Operator に同梱される DCGM exporter が公開する GPU メトリクス（使用率・温度・メモリなど）を Grafana の UI で確認するところまでを扱います。

:::message
この observability スタックは Terraform モジュール（`infra/eks`）には含まれていません。ワークショップの中で Helm で追加導入するコンポーネントです。クラスタの基盤（Basic01-07）とは独立して、後から足したり外したりできます。
:::

# 解説

## 全体構成

本章は、これまで構築・実行してきた基盤全体を「観測する」レイヤーを足します。GPU ノード上の DCGM exporter がメトリクスを公開し、Prometheus がそれを収集、Grafana が可視化する、という標準的な構成です。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

図のアクセラレータノード（GPU）で動く DCGM exporter を起点に、`monitoring` namespace の Prometheus/Grafana へメトリクスが流れます。

## これは何をするものか

GPU を使った分散学習・推論では、「GPU が本当に使われているか」「メモリが溢れていないか」「特定の rank だけ遅れていないか」を把握することが、性能問題やハングの切り分けに直結します。これを可視化するのが本章の目的です。

構成要素は 3 つです。

- **DCGM exporter**: NVIDIA の Data Center GPU Manager が公開する GPU メトリクス（使用率 `DCGM_FI_DEV_GPU_UTIL`、温度、メモリ使用量、電力など）を Prometheus 形式で公開する exporter です。この book では **Basic04 で導入した NVIDIA GPU Operator に同梱**されており、GPU ノードが立つと自動的に各ノードで動きます。追加導入は不要です
- **Prometheus**: 各 exporter からメトリクスを定期的に収集（scrape）し、時系列データとして保持します
- **Grafana**: Prometheus のデータをダッシュボードとして可視化します

Prometheus と Grafana は [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) という Helm チャートでまとめて導入します。このチャートは Prometheus Operator・Grafana・node-exporter・kube-state-metrics・各種 Kubernetes ダッシュボードを一括で入れてくれるため、EKS の observability の定番です。

DCGM exporter のメトリクスを Prometheus に拾わせるには、2 つが噛み合う必要があります。1 つは exporter 側で ServiceMonitor が作られていること。GPU Operator の DCGM exporter は既定では ServiceMonitor を作らないため、この基盤では Basic04 の GPU Operator 導入時に、[`gpu-addons.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/gpu-addons.tf) の Helm values で `dcgmExporter.serviceMonitor.enabled = true` を設定して ServiceMonitor を出すようにしてあります。もう 1 つは Prometheus 側がそれを検出できること。kube-prometheus-stack のデフォルトは、自身の Helm リリースが付けたラベル（`release: kps`）を持つ ServiceMonitor しかラベルセレクタで拾いません。`prometheusSpec.serviceMonitorSelectorNilUsesHelmValues: false` にすると、このラベルセレクタが無効になり、ラベルに関係なくすべての ServiceMonitor を拾うようになります。namespace セレクタ（`serviceMonitorNamespaceSelector`）はデフォルトで全 namespace が対象なので、結果として GPU Operator が別 namespace に作った DCGM の ServiceMonitor も拾われます。この 2 つが揃って初めて GPU メトリクスが収集されます。

## 全体の中での位置付け

本章は Basic07（GPU ワークロード）の直後に置いています。Basic07 では on-demand の GPU 1 枚で vLLM の推論を動かしましたが、より大規模な学習・推論では Basic05 の Capacity Block で p5en x2 のような複数ノード・複数 GPU を確保します。いずれの場合も、その GPU が実際にどう使われているかは、そのままでは見えません。本章の observability を入れると、GPU の使用率がリアルタイムで Grafana に表示されます。「動かす（Basic07）→ 見える化する（Basic08）」という一続きの流れです。observability は基盤の構築・破棄とは独立しているため、必要なときだけ入れる運用でも構いません。

## 注意

**observability スタックは Terraform 管理外です。** この book の `infra/eks` モジュールは observability を含みません。Helm で別途入れるため、`terraform destroy`（Basic11）では消えません。クラスタごと消す場合は問題ありませんが、クラスタを残したまま observability だけ外すには次のように `helm uninstall` します。kube-prometheus-stack は uninstall 後も CRD（`prometheuses` / `servicemonitors` など）が残るので、完全に消したい場合は CRD も手で削除します。

```bash
helm uninstall kps -n monitoring
# CRD は uninstall では消えないので、必要なら明示削除する
kubectl get crd | grep -E 'monitoring.coreos.com' | awk '{print $1}' | xargs -r kubectl delete crd
```

**CRD の削除は、その CRD を使っているカスタムリソースをクラスタ全体からカスケード削除します。** 上記のコマンドで `servicemonitors.monitoring.coreos.com` を削除すると、GPU Operator が作った DCGM 用の ServiceMonitor もこのタイミングで一緒に消えます。observability をこの book のワークショップで一時的に外すだけなら実害はありませんが、GPU Operator の Helm values で `dcgmExporter.serviceMonitor.enabled` を有効にしたままこの CRD だけを消すと、次に kube-prometheus-stack を入れ直すまで ServiceMonitor が存在しない状態になります。observability を再導入しない方針であれば、この CRD 削除と合わせて Basic04 の GPU Operator 側の `dcgmExporter.serviceMonitor.enabled` も無効化しておくと構成の食い違いを避けられます。

**Prometheus の retention とリソースに注意します。** GPU メトリクスは系列数が多く（GPU 1 枚ごと × メトリクス種別）、保持期間を長くするとストレージを消費します。実験用途では retention を数日程度に抑え、Prometheus の memory limit を設定しておくのが無難です。

**Grafana の admin パスワードは Secret で管理します。** Helm values に平文で書くと Git に残るため、本番では External Secrets などで注入します。本章では検証用に values で指定する例を示しますが、`<your-password>` のプレースホルダのまま実行すると、その文字列自体が既知の弱いパスワードとしてそのまま設定されてしまうので、必ず各自の値に置き換えてください。`/tmp/kps-values.yaml` にも平文で残る点に注意してください。

# ワークショップ実施

## 1. kube-prometheus-stack を導入する

Helm リポジトリを追加し、`monitoring` namespace にインストールします。DCGM を含む全 namespace の ServiceMonitor を拾う設定にします。なお、Basic07 までのワークロードは `helm template | kubectl apply` でレンダリングして適用する「実験カタログ」方式でしたが、observability は使い捨ての実験ではなく常駐する運用コンポーネントで、CRD やフックを含むため、ここでは `helm install` でリリースとして管理します。

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

cat > /tmp/kps-values.yaml <<'EOF'
grafana:
  enabled: true
  adminPassword: <your-password>   # 各自の値に置き換える
  defaultDashboardsEnabled: true
  service:
    type: ClusterIP
  sidecar:
    dashboards:
      enabled: true
      label: grafana_dashboard
      labelValue: "1"
prometheus:
  prometheusSpec:
    scrapeInterval: 10s
    evaluationInterval: 30s
    retention: 3d
    serviceMonitorSelectorNilUsesHelmValues: false
    podMonitorSelectorNilUsesHelmValues: false
    ruleSelectorNilUsesHelmValues: false
    resources:
      requests: { cpu: 500m, memory: 2Gi }
      limits: { memory: 4Gi }
nodeExporter:
  enabled: true
kubeStateMetrics:
  enabled: true
EOF

helm install kps prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace -f /tmp/kps-values.yaml
```

## 2. 稼働を確認する

```bash
kubectl get pods -n monitoring
```

Prometheus・Grafana・node-exporter・kube-state-metrics の Pod が `Running` になります（実機出力。Pod 名の suffix やチャートバージョンは環境によって変わります）。

```text
NAME                                                READY   STATUS    RESTARTS   AGE
kps-grafana-xxxxxxxxxx-xxxxx                         3/3     Running   0          43h
kps-kube-prometheus-stack-operator-xxxxx            1/1     Running   0          43h
kps-kube-state-metrics-xxxxx                        1/1     Running   0          43h
kps-prometheus-node-exporter-xxxxx                  1/1     Running   0          43h
prometheus-kps-kube-prometheus-stack-prometheus-0   2/2     Running   0          43h
```

## 3. ServiceMonitor が拾われているか確認する

GPU メトリクスを直接クエリする前に、DCGM の ServiceMonitor がクラスタに存在し、Prometheus 側からターゲットとして認識されているかを確認します。ここが噛み合っていないと、次の手順で `DCGM_FI_DEV_GPU_UTIL` を叩いても結果が空になり、原因の切り分けがしにくくなります。

```bash
kubectl get servicemonitor -A | grep dcgm
```

GPU Operator の namespace（既定では `gpu-operator`）に DCGM 用の ServiceMonitor が 1 つ表示されます。表示されない場合は、Basic04 の GPU Operator 導入時に `dcgmExporter.serviceMonitor.enabled` が有効化されているかを確認してください。

続いて Prometheus の Targets 画面で、そのターゲットが実際に scrape できているかを見ます。

```bash
kubectl port-forward -n monitoring svc/kps-kube-prometheus-stack-prometheus 9090:9090 &
# ブラウザで http://localhost:9090/targets を開き、dcgm-exporter のターゲットが State: UP になっているか確認する
```

## 4. GPU メトリクスが収集されているか確認する

ServiceMonitor が拾われていることを確認したら、DCGM の GPU 使用率メトリクスを直接クエリします。

```bash
curl -s "http://localhost:9090/api/v1/query?query=DCGM_FI_DEV_GPU_UTIL" | python3 -m json.tool | head
```

返る系列数は、そのクラスタにある GPU の総数と一致します。GPU 1 枚ずつを数えるので、ノードが混在していれば合算されます。本書の検証時は Basic05 の Capacity Block の p4d.24xlarge（A100 x8）2 台と、Basic07 の vLLM が載った g6.xlarge（L4 x1）1 台が同時に動いていたため、**17 系列**が返りました。

系列数だけでは分かりにくいので、ノードと GPU モデルごとに数えると構成が見えます。

```bash
curl -s "http://localhost:9090/api/v1/query?query=DCGM_FI_DEV_GPU_UTIL" \
  | python3 -c "
import json, sys
from collections import Counter
r = json.load(sys.stdin)['data']['result']
print('系列数:', len(r))
c = Counter((m['metric'].get('Hostname'), m['metric'].get('modelName')) for m in r)
for (host, model), n in sorted(c.items()):
    print(f'  {model:26s} {n} GPU  ({host})')
"
```

実機出力:

```text
系列数: 17
  NVIDIA A100-SXM4-40GB      8 GPU  (ip-10-0-a-b...)
  NVIDIA A100-SXM4-40GB      8 GPU  (ip-10-0-c-d...)
  NVIDIA L4                  1 GPU  (ip-10-0-e-f...)
```

これで、Capacity Block で確保した 2 ノード 16 GPU と、別プールの推論ノード 1 GPU のすべてからメトリクスが集まっていることが確認できます。系列が返らない場合や期待より少ない場合は、手順 3 の Targets 画面で該当ノードの dcgm-exporter が `UP` になっているかを先に確認してください。

## 5. Grafana の UI にアクセスする

Grafana に port-forward し、ブラウザで開きます。

```bash
kubectl port-forward -n monitoring svc/kps-grafana 3000:80 &
# ブラウザで http://localhost:3000 を開く
# ユーザー: admin / パスワード: 手順1で設定した値
```

確認が終わったら、開いたままの port-forward プロセスを終了しておきます（`jobs` でジョブ番号を確認し、`kill %<番号>` します）。手順3・4も含め、`&` で起動した port-forward はターミナルを閉じるまで残り続けます。

ログイン後、左メニューの Dashboards から、kube-prometheus-stack が自動導入したダッシュボードが見えます（実機で 29 個。チャートのバージョンによって前後します）。

- `Kubernetes / Compute Resources / Node (Pods)`: ノード単位の CPU/メモリを表示します
- `Node Exporter / Nodes`: ノードのハードウェアメトリクスを表示します
- `Kubernetes / Compute Resources / Namespace (Pods)`: namespace 単位のリソースを表示します

GPU 専用のダッシュボードは kube-prometheus-stack には含まれないため、[NVIDIA DCGM Exporter Dashboard（Grafana ID: 12239）](https://grafana.com/grafana/dashboards/12239-nvidia-dcgm-exporter-dashboard/) をインポートすると、GPU 使用率・温度・メモリ・電力のパネルが一式表示されます。Dashboards → New → Import の画面で ID に `12239` を入力し、Prometheus データソースを選択して Import します。

これで、GPU ワークロードの実行中に各 GPU がどれだけ使われているかを、時系列グラフで観測できます。Basic07 の vLLM 推論であれば 1 枚の GPU 使用率が、Capacity Block で確保したマルチノード学習であれば全 GPU の使用率が、それぞれ可視化されます。

実際に Capacity Block で確保した p5en.48xlarge（H200 x8）の 1 ノードで GPU ワークロードを流しているときの DCGM ダッシュボードの実画面です。8 枚の GPU（GPU 0〜7）それぞれの温度・電力・SM クロック・使用率が個別に可視化され、使用率が最大 100 %、SM クロックが最大 1.98 GHz、電力が 1 枚あたり最大約 690 W に達していることが読み取れます。

![NVIDIA DCGM Exporter Dashboard で p5en の 8 GPU を可視化した実画面](/images/books/eks-distributed-ai/ch7-dcgm-grafana.png)

# まとめ

本章では、kube-prometheus-stack を Helm で導入し、GPU Operator 同梱の DCGM exporter が公開する GPU メトリクスを Prometheus で収集、Grafana の UI で可視化するところまでを実行しました。`serviceMonitorSelectorNilUsesHelmValues: false` で DCGM の ServiceMonitor を拾わせるのがポイントで、Capacity Block で確保した p5en x2 の 16 GPU すべてのメトリクスが取得できることを実機で確認しました。observability は Terraform 管理外の追加コンポーネントなので、必要なときだけ入れる運用ができます。

# 参考資料

- [kube-prometheus-stack (Helm chart)](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
- [NVIDIA DCGM Exporter](https://github.com/NVIDIA/dcgm-exporter)
- [NVIDIA DCGM Exporter Dashboard (Grafana ID 12239)](https://grafana.com/grafana/dashboards/12239-nvidia-dcgm-exporter-dashboard/)
- [Prometheus Operator ServiceMonitor](https://prometheus-operator.dev/docs/getting-started/design/#servicemonitor)
