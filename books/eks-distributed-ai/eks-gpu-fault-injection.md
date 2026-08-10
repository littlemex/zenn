---
title: "Advanced03 - GPU 障害を注入して検知を確かめる"
free: true
---

本章では、[Basic08 - Observability を導入する](eks-observability) で有効化した Node Monitoring Agent（NMA）が、GPU の障害を本当に検知できるのかを、DCGM の fault injection 機能で擬似的な障害を注入して確かめます。実際の GPU を壊さずに XID エラーを注入し、NMA が `AcceleratedHardwareReady` を `False` に反転させ、Prometheus のアラートが発火するところまでを実機で追います。あわせて、NMA が GPU ノードで健全性を読むために内部でどう動いているか、この基盤の GPU taint との相性で何が起きるかも解説します。

:::message
本章は Basic08 で NMA と kube-prometheus-stack が動いていることを前提にします。fault injection は DCGM のキャッシュに偽の障害値を書き込むだけで、実際の GPU ハードウェアには影響しません。
:::

# 解説

## なぜ検知の「検証」が要るのか

監視は入れただけでは信用できません。とくに GPU 障害の検知は、実際に GPU が壊れる場面が滅多に来ないため、「入れたが実は動いていなかった」に最も陥りやすい領域です。分散学習では 1 枚の GPU の故障が NCCL の集合通信を全ランクでハングさせ、高価な Capacity Block の課金だけが無言で流れ続けます。その最悪シナリオを断ち切るための検知が、いざというときに本当に発火するのか。これを平常時に安全に確かめておくのが本章の目的です。

NVIDIA の DCGM には、GPU のテレメトリフィールドに任意の値を注入する fault injection 機能があります。これを使うと、実際の GPU を故障させることなく「XID 79 が起きた」「訂正不可能な ECC エラーが出た」という状態を DCGM のキャッシュ上に作り出せます。NMA はこの DCGM の値を読んで健全性を判定するため、注入した障害が NodeCondition の反転として観測できれば、検知経路が端から端まで生きていることの証明になります。

## NMA が GPU 健全性を読む仕組み

NMA が GPU の健全性をどこから読んでいるかを押さえておくと、注入をどこに打てばよいかがわかります。

NMA は各ノードで動くエージェント本体（`eks-node-monitoring-agent` DaemonSet）と、GPU の健全性を読むための DCGM サーバー（`dcgm-server` DaemonSet）の二つで構成されます。`dcgm-server` は NMA に同梱された `nv-hostengine`（DCGM のホストエンジン）をホストのポート 5555 で起動し、エージェント本体は同じノードの `localhost:5555` に接続して GPU のフィールド値を読みます。GPU の XID・ECC・NVLink といった障害はこの DCGM が拾い、エージェントが `AcceleratedHardwareReady` という NodeCondition に変換します。

ここで、Basic04 で導入した GPU Operator の dcgm-exporter との関係が問題になります。dcgm-exporter は使用率などの連続的なメトリクスを Prometheus に出すためのもので、既定では埋め込み（embedded）モードで動き、自分の中に DCGM を抱えてポート 9400 でメトリクスを公開します。NMA の `dcgm-server`（ポート 5555）とは別のポート・別の役割なので、両者は同じ GPU ノードで問題なく共存します。

:::message alert
GPU Operator には standalone DCGM を立てるオプション（`dcgm.enabled=true`）もありますが、これは有効化してはいけません。standalone DCGM も `nv-hostengine` をホストのポート 5555 で公開するため、NMA の `dcgm-server` と同じポートを奪い合い、後から起動した方が `Pending` のまま起動できず、GPU 健全性検知が沈黙します。この基盤では [`gpu-addons.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/gpu-addons.tf) で standalone DCGM を無効のままにし、メトリクスは埋め込みモードの dcgm-exporter に任せています。
:::

## GPU taint と dcgm-server の相性

この基盤では GPU ノードに `nvidia.com/gpu=:NoSchedule` の taint を付けて、GPU を要求しない一般の Pod が高価な GPU ノードに載らないようにしています。この taint が NMA の `dcgm-server` と相性の悪い挙動を引き起こします。

NMA を EKS アドオンとして導入すると、エージェント本体はすべての taint を tolerate するので GPU ノードに載りますが、同梱の `dcgm-server` DaemonSet は tolerations が空のまま出荷されます。その結果、taint を持つ GPU ノードに `dcgm-server` が載れず、GPU があるノードでこそ GPU 健全性を読めない、という状態になります。しかも執筆時点では、NMA アドオンの設定値（configurationValues）に `dcgm-server` の tolerations を指定する項目がありません。

そこでこの基盤では、NMA を EKS アドオンではなく Helm チャートで導入し、チャートが公開している `dcgmAgent.tolerations` に GPU taint への toleration を渡しています。これで `dcgm-server` が GPU ノードに載り、GPU 健全性検知が機能します。この設定は [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) にあります。

```hcl
resource "helm_release" "node_monitoring_agent" {
  # ...
  values = [yamlencode({
    dcgmAgent = {
      tolerations = [{
        key      = "nvidia.com/gpu"
        operator = "Exists"
        effect   = "NoSchedule"
      }]
    }
  })]
}
```

エージェント本体はデフォルトで全 taint を tolerate するため、明示的に上書きするのは `dcgm-server` の toleration だけに絞っています。これは NMA 側の設計上の見落とし（エージェント本体は全 taint を許容するのに `dcgm-server` だけ許容しない）と考えられ、将来 upstream で修正されればこの toleration ブロックは不要になります。

# ワークショップ実施

:::message
手順 1 から 5 は連続して実施してください。注入した値は DCGM のキャッシュに残り、手順 5 でクリアするまで保持されますが、途中で長時間中断すると `dcgm-server` の再起動などで状態が変わることがあります。また、本章で注入する GPU 障害でノードが Unhealthy になっても、Karpenter のノード自動修復（auto-repair）は無効にしてあるため、ノードが勝手に terminate されることはありません（理由は Basic08 参照）。
:::

## 1. 前提を確認し、対象ノードを決める

- Basic08 で NMA と kube-prometheus-stack が導入済みであること。
- `k` エイリアスと `KUBECONFIG` / `--context` は Basic01 で設定済みであること。
- GPU ノードが 1 台以上動いていること。Basic07 の vLLM や Basic05 の Capacity Block のいずれかを稼働させておきます。
- 手順 4 の JSON パースに `python3` を使います。無い場合は `dnf install -y python3` などで入れておきます。

まず、NMA のエージェント本体と `dcgm-server` の両方が動いていることを確認します。

```bash
k get ds -n kube-system eks-node-monitoring-agent dcgm-server
```

実機出力です。`dcgm-server` が GPU ノードの数だけ `DESIRED` に上がっていれば、GPU taint への toleration が効いています。両者の `NODE SELECTOR` はどちらも `kubernetes.io/os=linux` ですが、`dcgm-server` を GPU ノードだけに限定しているのは `NODE SELECTOR` ではなく nodeAffinity（GPU インスタンスタイプの許可リスト）なので、`DESIRED` はエージェント本体（全ノード）と `dcgm-server`（GPU ノードのみ）で異なります。

```text
NAME                        DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR            AGE
eks-node-monitoring-agent   4         4         4       4            4           kubernetes.io/os=linux   34m
dcgm-server                 1         1         1       1            1           kubernetes.io/os=linux   34m
```

前段の `dcgm-server` が `READY` 1 以上になっていることを確認してから進みます（0 のままなら GPU ノードが無いか toleration の問題で、ここから先は動きません）。以降の手順で使う `dcgm-server` の Pod 名と、それが載っているノード名を変数に取ります。ノード名をハードコードせず、注入する GPU と確認するノードを機械的に一致させるためです。

```bash
DCGM=$(k get pods -n kube-system -l k8s-app=dcgm-server \
  -o jsonpath='{.items[0].metadata.name}')
NODE=$(k get pod -n kube-system "$DCGM" -o jsonpath='{.spec.nodeName}')
[ -n "$DCGM" ] && [ -n "$NODE" ] \
  && echo "dcgm-server=$DCGM  node=$NODE" \
  || echo "dcgm-server が見つかりません。前段の DaemonSet の READY を確認してください"
```

この `$NODE` の `AcceleratedHardwareReady` が `True` であることを確認します。

```bash
k get node "$NODE" \
  -o jsonpath='{range .status.conditions[?(@.type=="AcceleratedHardwareReady")]}{.status}{" ("}{.reason}{")"}{end}{"\n"}'
```

実機出力です。

```text
True (NvidiaGPUIsReady)
```

`reason` が `NvidiaGPUIsReady` になっていれば、NMA が `dcgm-server` の `nv-hostengine` に接続して GPU を正常に読めています。もし `False (DCGMError)` であれば、`dcgm-server` が起動していない（前段の `DESIRED` が 0）か、standalone DCGM とポートが競合している可能性があります。

## 2. GPU 障害を注入する

`dcgm-server` の中の `dcgmi` で GPU を確認します。

```bash
k exec -n kube-system "$DCGM" -- dcgmi discovery -l
```

実機出力です。この検証では `gpu-ddp` プールで g5 インスタンス（NVIDIA A10G を 1 枚搭載）が起動していました。同じプールでも g6 が選ばれた場合は NVIDIA L4 と表示されるなど、起動したインスタンスタイプによって GPU 名は変わります。

```text
1 GPU found (Active).
+--------+---------------------------------------------------------+
| GPU ID | Device Information                                       |
+--------+---------------------------------------------------------+
| 0      | Name: NVIDIA A10G                                        |
|        | PCI Bus ID: 00000000:00:1E.0                            |
+--------+---------------------------------------------------------+
```

XID 79（GPU がバスから外れた、という致命的なエラー）を GPU 0 に注入します。`-f 230` は DCGM のフィールド ID で、`DCGM_FI_DEV_XID_ERRORS`（直近の XID エラー番号）を指します。

```bash
k exec -n kube-system "$DCGM" -- \
  dcgmi test --inject --gpuid 0 -f 230 -v 79
```

実機出力です。

```text
Successfully injected field info.
```

これは DCGM のキャッシュに「GPU 0 で XID 79 が起きた」という値を書き込んだだけで、実際の GPU は健全なままです。

## 3. NodeCondition が反転することを確認する

NMA は数十秒周期で DCGM を読んで健全性を再評価します。固定の `sleep` では評価周期の位相によって反転前の値を見てしまうことがあるので、反転するまでポーリングします。

```bash
for i in $(seq 1 12); do
  OUT=$(k get node "$NODE" \
    -o jsonpath='{range .status.conditions[?(@.type=="AcceleratedHardwareReady")]}{.status}{" reason="}{.reason}{" msg="}{.message}{"\n"}{end}')
  echo "$OUT"
  echo "$OUT" | grep -q NvidiaXID79Error && break
  [ "$i" = 12 ] && echo "120 秒以内に反転しませんでした。手順2の注入結果と dcgm-server のログを確認してください"
  sleep 10
done
```

実機出力です。注入した XID 79 を NMA が検知し、`AcceleratedHardwareReady` が `False` に反転しました。

```text
False reason=NvidiaXID79Error msg=detected XID-79 on the instance, review kernel logs for additional information.
```

`reason` が `NvidiaXID79Error` になっている点が重要です。注入した障害の種類が、そのまま NodeCondition の理由として現れています。

## 4. アラートが発火することを確認する

Basic08 の [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) が入れた PrometheusRule のうち、`AcceleratedHardwareUnhealthy`（critical）がこの反転で発火します。Prometheus に port-forward してアラートの状態を確認します。port-forward の確立を確実に待つため、ready エンドポイントが応答するまで待ってから叩きます。

```bash
k port-forward -n monitoring svc/kps-prometheus 9090:9090 &
for i in $(seq 1 30); do curl -sf http://localhost:9090/-/ready >/dev/null && break; sleep 1; done
curl -sf http://localhost:9090/-/ready >/dev/null \
  || echo "port-forward が確立できていません（この後のクエリもエラーになります）。jobs と 9090 の占有を確認し、解消してからやり直してください"

curl -s "http://localhost:9090/api/v1/alerts" \
  | python3 -c "
import json, sys
hits = [a for a in json.load(sys.stdin)['data']['alerts']
        if a['labels'].get('alertname') == 'AcceleratedHardwareUnhealthy']
if not hits:
    print('まだ届いていません（NodeCondition 反転 → scrape → ルール評価の遅延。30 秒後に再実行してください）')
for a in hits:
    print(a['labels']['alertname'], a['state'], a['labels'].get('node'))
"
```

ルールは `for: 2m` で定義しているので、反転直後は `pending`、2 分継続すると `firing` になります。まず `pending` を確認します。

```text
AcceleratedHardwareUnhealthy pending ip-10-0-8-26...
```

2 分ほど待って同じコマンドを再実行すると、`firing` に変わります（注入をクリアする手順 5 を先に実行すると条件が解消されて `firing` に至らないので、firing まで見届けたい場合は手順 5 の前に確認します）。

```text
AcceleratedHardwareUnhealthy firing ip-10-0-8-26...
```

`kube_node_status_condition` を直接見ると、`AcceleratedHardwareReady` の `status="true"` の系列が `0`（真ではない）になっていることも確認できます。

```bash
curl -s "http://localhost:9090/api/v1/query" \
  --data-urlencode 'query=kube_node_status_condition{condition="AcceleratedHardwareReady",status="true"}' \
  | python3 -c "import json,sys; r=json.load(sys.stdin)['data']['result']; [print(s['metric'].get('node'),'=',s['value'][1]) for s in r] or print('まだ反映されていません')"
```

```text
ip-10-0-8-26... = 0
```

これで、GPU 障害の注入から NodeCondition の反転、そして Prometheus アラートの発火までの検知経路が端から端まで生きていることを確認できました。

## 5. 注入をクリアして健全状態に戻す

注入した値は DCGM のキャッシュに残り続けるため、`dcgm-server` を再起動してキャッシュをクリアし、あわせてエージェント本体を再起動して接続をやり直させます。

```bash
k rollout restart ds -n kube-system dcgm-server
k rollout status ds -n kube-system dcgm-server

# 対象ノードのエージェント本体を再起動して新しい dcgm-server に接続し直す
k delete pod -n kube-system -l app.kubernetes.io/name=eks-node-monitoring-agent \
  --field-selector spec.nodeName="$NODE"
# 再起動が完了する（新しい Pod が Ready になる）まで待つ
k rollout status ds -n kube-system eks-node-monitoring-agent
```

しばらく待って `AcceleratedHardwareReady` が `True` に戻ることを確認します。

```bash
for i in $(seq 1 12); do
  OUT=$(k get node "$NODE" \
    -o jsonpath='{range .status.conditions[?(@.type=="AcceleratedHardwareReady")]}{.status}{" ("}{.reason}{")"}{end}{"\n"}')
  echo "$OUT"
  echo "$OUT" | grep -q "True (NvidiaGPUIsReady)" && break
  [ "$i" = 12 ] && echo "120 秒以内に戻りませんでした。dcgm-server とエージェントの Pod が Running か確認してください"
  sleep 10
done
```

実機出力です。

```text
True (NvidiaGPUIsReady)
```

port-forward はバックグラウンドで起動したので、確認が終わったら `jobs` で番号を確認して `kill %<番号>` で止めます。

なお `dcgm-server` を再起動したことで Pod 名が変わっているため、もう一度注入を試す場合は手順1の変数取得（`$DCGM` / `$NODE`）からやり直してください。古い `$DCGM` のまま `k exec` すると `NotFound` になります。

# まとめ

本章では、DCGM の fault injection で XID 79 を GPU に注入し、NMA が `AcceleratedHardwareReady` を `False`（`NvidiaXID79Error`）に反転させ、Prometheus の `AcceleratedHardwareUnhealthy` アラートが発火するところまでを実機で確かめました。実際の GPU を壊さずに検知経路の端から端までを検証できるのが fault injection の価値です。あわせて、NMA が GPU 健全性を `dcgm-server`（同梱の `nv-hostengine`、ポート 5555）から読むこと、GPU Operator の埋め込み dcgm-exporter（ポート 9400）とは共存する一方で standalone DCGM とはポートが競合すること、そして GPU taint のために `dcgm-server` へ toleration を渡す必要があり Helm 導入でそれを解決していることを見ました。検知が本当に動くことを平常時に確かめておけば、いざ GPU が壊れたときに「監視が入っていたのに気づけなかった」という事態を避けられます。

# 参考資料

- [Amazon EKS node monitoring agent](https://docs.aws.amazon.com/eks/latest/userguide/node-health.html)
- [Under the hood: Amazon EKS Auto Mode がノード障害を検知・修復・診断する仕組み](https://aws.amazon.com/jp/blogs/containers/under-the-hood-how-amazon-eks-auto-mode-detects-repairs-and-diagnoses-node-failures/)
- [NVIDIA DCGM](https://docs.nvidia.com/datacenter/dcgm/latest/)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
