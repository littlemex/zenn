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

- **DCGM exporter**: NVIDIA の Data Center GPU Manager が公開する GPU メトリクス（使用率 `DCGM_FI_DEV_GPU_UTIL`、温度、メモリ使用量、電力など）を Prometheus 形式で出す exporter。この book では **Basic04 で導入した NVIDIA GPU Operator に同梱**されており、GPU ノードが立つと自動的に各ノードで動きます。追加導入は不要です
- **Prometheus**: 各 exporter からメトリクスを定期的に収集（scrape）し、時系列データとして保持する
- **Grafana**: Prometheus のデータをダッシュボードとして可視化する UI

Prometheus と Grafana は [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) という Helm チャートでまとめて導入します。このチャートは Prometheus Operator・Grafana・node-exporter・kube-state-metrics・各種 Kubernetes ダッシュボードを一括で入れてくれるため、EKS の observability の定番です。

DCGM exporter のメトリクスを Prometheus に拾わせるには、GPU Operator が作る ServiceMonitor（または PodMonitor）を Prometheus が検出できるようにします。kube-prometheus-stack のデフォルトでは自身の Helm リリースが作った ServiceMonitor しか拾わない設定になっているため、`prometheusSpec.serviceMonitorSelectorNilUsesHelmValues: false` のように「全 namespace の ServiceMonitor を拾う」設定にしておくのがポイントです。

## 全体の中での位置付け

本章は Basic07（GPU ワークロード）の直後に置いています。Basic07 では on-demand の GPU 1 枚で vLLM の推論を動かしましたが、より大規模な学習・推論では Basic06 の Capacity Block で p5en x2 のような複数ノード・複数 GPU を確保します。いずれの場合も、その GPU が実際にどう使われているかは、そのままでは見えません。本章の observability を入れると、GPU の使用率がリアルタイムで Grafana に表示されます。「動かす（Basic07）→ 見える化する（Basic08）」という一続きの流れです。observability は基盤の構築・破棄とは独立しているため、必要なときだけ入れる運用でも構いません。

## 注意

**observability スタックは Terraform 管理外です。** この book の `infra/eks` モジュールは observability を含みません。Helm で別途入れるため、`terraform destroy`（Basic11）では消えません。クラスタごと消す場合は問題ありませんが、クラスタを残したまま observability だけ外すには `helm uninstall` を別途実行します。

**Prometheus の retention とリソースに注意します。** GPU メトリクスは系列数が多く（GPU 1 枚ごと × メトリクス種別）、保持期間を長くするとストレージを消費します。実験用途では retention を数日程度に抑え、Prometheus の memory limit を設定しておくのが無難です。

**Grafana の admin パスワードは Secret で管理します。** Helm values に平文で書くと Git に残るため、本番では External Secrets などで注入します。本章では検証用に values で指定する例を示しますが、パスワードは各自の値に置き換えてください。

# ワークショップ実施

## 1. kube-prometheus-stack を導入する

Helm リポジトリを追加し、`monitoring` namespace にインストールします。DCGM を含む全 namespace の ServiceMonitor を拾う設定にします。

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

Prometheus・Grafana・node-exporter・kube-state-metrics の Pod が `Running` になります（実機出力）。

```
NAME                                                READY   STATUS    RESTARTS   AGE
kps-grafana-xxxxxxxxxx-xxxxx                         3/3     Running   0          43h
kps-kube-prometheus-stack-operator-xxxxx            1/1     Running   0          43h
kps-kube-state-metrics-xxxxx                        1/1     Running   0          43h
kps-prometheus-node-exporter-xxxxx                  1/1     Running   0          43h
prometheus-kps-kube-prometheus-stack-prometheus-0   2/2     Running   0          43h
```

## 3. GPU メトリクスが収集されているか確認する

Prometheus に port-forward して、DCGM の GPU 使用率メトリクスを直接クエリします。

```bash
kubectl port-forward -n monitoring svc/kps-kube-prometheus-stack-prometheus 9090:9090 &
curl -s "http://localhost:9090/api/v1/query?query=DCGM_FI_DEV_GPU_UTIL" | python3 -m json.tool | head
```

Basic06 の Capacity Block で確保した p5en x2（H200 x8 x2）の環境では、**16 系列**の GPU メトリクスが返ります（gpu=0..7 が各ノード分）。これで 2 ノード 16 GPU すべてのメトリクスが収集されていることが確認できます。Basic07 の vLLM のように GPU 1 枚だけの場合は、1 系列だけが返ります。

## 4. Grafana の UI にアクセスする

Grafana に port-forward し、ブラウザで開きます。

```bash
kubectl port-forward -n monitoring svc/kps-grafana 3000:80 &
# ブラウザで http://localhost:3000 を開く
# ユーザー: admin / パスワード: 手順1で設定した値
```

ログイン後、左メニューの Dashboards から、kube-prometheus-stack が自動導入したダッシュボードが見えます（実機で 25 個）。

- `Kubernetes / Compute Resources / Node (Pods)` — ノード単位の CPU/メモリ
- `Node Exporter / Nodes` — ノードのハードウェアメトリクス
- `Kubernetes / Compute Resources / Namespace (Pods)` — namespace 単位のリソース

GPU 専用のダッシュボードは kube-prometheus-stack には含まれないため、[NVIDIA DCGM Exporter Dashboard（Grafana ID: 12239）](https://grafana.com/grafana/dashboards/12239-nvidia-dcgm-exporter-dashboard/) をインポートすると、GPU 使用率・温度・メモリ・電力のパネルが一式表示されます。

```
Dashboards → New → Import → 12239 → Prometheus データソースを選択 → Import
```

これで、GPU ワークロードの実行中に各 GPU がどれだけ使われているかを、時系列グラフで観測できます。Basic07 の vLLM 推論であれば 1 枚の GPU 使用率が、Capacity Block で確保したマルチノード学習であれば全 GPU の使用率が、それぞれ可視化されます。

# まとめ

本章では、kube-prometheus-stack を Helm で導入し、GPU Operator 同梱の DCGM exporter が公開する GPU メトリクスを Prometheus で収集、Grafana の UI で可視化するところまでを実行しました。`serviceMonitorSelectorNilUsesHelmValues: false` で DCGM の ServiceMonitor を拾わせるのがポイントで、Capacity Block で確保した p5en x2 の 16 GPU すべてのメトリクスが取得できることを実機で確認しました。observability は Terraform 管理外の追加コンポーネントなので、必要なときだけ入れる運用ができます。

# 参考資料

- [kube-prometheus-stack (Helm chart)](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
- [NVIDIA DCGM Exporter](https://github.com/NVIDIA/dcgm-exporter)
- [NVIDIA DCGM Exporter Dashboard (Grafana ID 12239)](https://grafana.com/grafana/dashboards/12239-nvidia-dcgm-exporter-dashboard/)
- [Prometheus Operator ServiceMonitor](https://prometheus-operator.dev/docs/operator/design/#servicemonitor)
