---
title: "Advanced03 - GPU 障害を注入して検知を確かめる"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、[Basic08 - Observability を導入する](eks-observability) で有効化した Node Monitoring Agent（NMA）が、GPU の障害を本当に検知できるのかを、DCGM の 障害注入 機能で擬似的な障害を注入して確かめます。実際の GPU を壊さずに XID エラーを注入し、NMA が `AcceleratedHardwareReady` を `False` に反転させ、Prometheus のアラートが発火するところまでを実機で追います。あわせて、NMA が GPU ノードで健全性を読むために内部でどう動いているか、この基盤の GPU taint との相性で何が起きるかも解説します。

:::message
本章は Basic08 で NMA と kube-prometheus-stack が動いていることを前提にします。障害注入 は DCGM のキャッシュに偽の障害値を書き込むだけで、実際の GPU ハードウェアには影響しません。
:::

# 解説

## なぜ検知の「検証」が要るのか

監視は導入しただけでは信用できません。とくに GPU 障害の検知は、実際に GPU が壊れる場面が滅多に来ないため、「入れたが実は動いていなかった」に最も陥りやすい領域です。分散学習では 1 枚の GPU の故障が NCCL の集合通信を全ランクでハングさせ、高価な Capacity Block の課金だけが無言で流れ続けます。その最悪シナリオを断ち切るための検知が、いざというときに本当に発火するのか。これを平常時に安全に確かめておくのが本章の目的です。

NVIDIA の DCGM には、GPU のテレメトリフィールドに任意の値を注入する 障害注入 機能があります。これを使うと、実際の GPU を故障させることなく「XID 79 が起きた」「訂正不可能な ECC エラーが出た」という状態を DCGM のキャッシュ上に作り出せます。NMA はこの DCGM の値を読んで健全性を判定するため、注入した障害が NodeCondition の反転として観測できれば、検知経路の一連の流れが正しく機能していることの証明になります。

## NMA が GPU 健全性を読む仕組み

NMA が GPU の健全性をどこから読んでいるかを押さえておくと、注入をどこに打てばよいかがわかります。

NMA は二つの DaemonSet で構成されます。エージェント本体である `eks-node-monitoring-agent` と、GPU の健全性を読むための DCGM サーバーである `dcgm-server` です。`dcgm-server` は NMA に同梱された DCGM のホストエンジン `nv-hostengine` をホストのポート 5555 で起動し、エージェント本体は同じノードの `localhost:5555` に接続して GPU のフィールド値を読みます。GPU の XID・ECC・NVLink といった障害はこの DCGM が拾い、エージェントが `AcceleratedHardwareReady` という NodeCondition に変換します。

ここで、Basic04 で導入した GPU Operator の dcgm-exporter との関係が問題になります。dcgm-exporter は使用率などの連続的なメトリクスを Prometheus に出すためのもので、既定では埋め込み（embedded）モードで動き、自分の中に DCGM を抱えてポート 9400 でメトリクスを公開します。NMA の `dcgm-server`（ポート 5555）とは別のポート・別の役割なので、両者は同じ GPU ノードで問題なく共存します。

:::message alert
GPU Operator には standalone DCGM を起動するオプション（`dcgm.enabled=true`）もありますが、この基盤では有効化していません。standalone DCGM の DaemonSet はホストのポート 5555 を `hostPort` で確保します。NMA の `dcgm-server` も同じ 5555 を要求するので、両者は同じポートを取り合い、後から起動した方が `Pending` のまま立ち上がれずに GPU 健全性検知が沈黙します (実機で確認しています)。そもそも AWS のドキュメントは、[既存の DCGM 導入と NMA の併用はできない](https://docs.aws.amazon.com/eks/latest/userguide/node-health-nma.html#node-monitoring-agent-configure)としています。GPU 健全性を読む役目は NMA の `dcgm-server` に任せれば十分なので、この基盤では [`gpu-addons.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/gpu-addons.tf) で standalone DCGM を無効のままにし、メトリクスは埋め込みモードの dcgm-exporter に任せています。
:::

## GPU taint と dcgm-server の相性

この基盤では GPU ノードに `nvidia.com/gpu=:NoSchedule` の taint を付けて、GPU を要求しない一般の Pod が高価な GPU ノードに載らないようにしています。この taint が NMA の `dcgm-server` と相性の悪い挙動を引き起こします。

NMA を導入すると、エージェント本体はすべての taint を tolerate するので GPU ノードに載りますが、同梱の `dcgm-server` DaemonSet は tolerations が空のまま出荷されます。その結果、taint を持つ GPU ノードに `dcgm-server` が載れず、まさに GPU があるノードで GPU 健全性を読めない、という状態になります。

そこでこの基盤では、NMA を EKS アドオンとして導入したうえで、アドオンの設定値（`configuration_values`）に `dcgmAgent.tolerations` を渡して GPU taint への toleration を与えています。渡すのは `nvidia.com/gpu` だけでは足りません。Capacity Block のノードは `capacity-reservation` taint も持つので、これを落とすと、前払いしている 本来必要な CB の GPU ノードに `dcgm-server` が載らず `Pending` のままになります。この基盤は `nvidia.com/gpu` と `capacity-reservation` に加えて、利用者が `accelerator_pools` で定義した taint の分も渡しています。なお `dcgmAgent.tolerations` は NMA アドオンの v1.3.0 以降のスキーマにある項目です。この基盤は既定ではアドオンのバージョンを固定せず EKS の既定に任せているので、[スキーマを確かめる](https://docs.aws.amazon.com/eks/latest/userguide/node-health-nma.html#node-monitoring-agent-configure)なら次のように叩きます。

```bash
aws eks describe-addon --cluster-name "$CLUSTER_NAME" --region "$AWS_REGION" \
  --addon-name eks-node-monitoring-agent --query 'addon.addonVersion' --output text

aws eks describe-addon-configuration --addon-name eks-node-monitoring-agent \
  --addon-version <上で出たバージョン>
```
固定したい場合は `node_monitoring_agent_version` に指定します。なお NMA 自体も [`enable_node_monitoring_agent`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/variables.tf) で切れるので、無効にしている環境では本章の手順 1 から進みません。これで `dcgm-server` が GPU ノードに載り、GPU 健全性検知が機能します。この設定は [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) にあります。

```hcl
resource "aws_eks_addon" "node_monitoring_agent" {
  cluster_name = module.eks.cluster_name
  addon_name   = "eks-node-monitoring-agent"

  configuration_values = jsonencode({
    dcgmAgent = {
      tolerations = local.dcgm_server_tolerations
    }
  })
}
```

渡している `local.dcgm_server_tolerations` は、`nvidia.com/gpu` と `capacity-reservation` に、利用者が `accelerator_pools` で定義した taint の分を足したものです。GPU ノードに付く taint の台帳と同じ場所で組み立てているので、プールに新しい taint を足したときに片方だけ更新されることがありません。ここを `nvidia.com/gpu` だけにすると、前払いしている Capacity Block の GPU ノードにこそ `dcgm-server` が載らず `Pending` のままになり、そのノードの `AcceleratedHardwareReady` が `False` に張り付いて延々とアラートが鳴ります。無条件の `Exists` にはせず対象を並べているのは、無関係な taint 付きノードにまで載らないようにするためです。

エージェント本体は既定で全 taint を tolerate するため、明示的に与えるのは `dcgm-server` の toleration だけに絞っています。これは [NMA](https://docs.aws.amazon.com/eks/latest/userguide/node-health-nma.html#node-monitoring-agent-configure) 側の設計上の見落とし（エージェント本体は全 taint を許容するのに `dcgm-server` だけ許容しない）と考えられ、将来 NMA 側の既定が修正されればこの `configuration_values` は不要になります。

## なぜ auto-repair を有効にしないか

NMA が出す NodeCondition は、Karpenter のノード自動修復（auto-repair）と組み合わせると、不健全なノードを自動で terminate して置き換えることもできます。しかしこの基盤では auto-repair を有効にせず、NMA には「検知して知らせる」役割だけを持たせています。

理由は、この基盤の主用途が tightly-coupled な同期集合通信（NCCL によるマルチノード学習）だからです。1 ノードを自動 terminate すると、まだ調査できたはずの高価な GPU ノードを人間の確認なしに失いますし、Capacity Block では同じ故障ホストを引き直して terminate と再起動を繰り返すループに陥る危険もあります。ノードを止める・置き換えるという不可逆な判断は、ジョブの状態を分かっている人間やジョブ層に委ねるのが安全です。まず検知を最速にし、terminate の引き金は握らせない、という切り分けです。この論点は [AWS の解説記事](https://aws.amazon.com/jp/blogs/containers/under-the-hood-how-amazon-eks-auto-mode-detects-repairs-and-diagnoses-node-failures/) が検知・修復・診断の仕組みとして詳しく説明しているので、あわせて参照してください。

# ワークショップ実施

:::message
手順 1 から 5 は連続して実施してください。注入した値は DCGM のキャッシュに残り、手順 5 でクリアするまで保持されますが、途中で長時間中断すると `dcgm-server` の再起動などで状態が変わることがあります。また、本章で注入する GPU 障害でノードが Unhealthy になっても、Karpenter のノード自動修復は無効にしてあるため、ノードが勝手に terminate されることはありません。この判断の理由は、解説パートの「なぜ auto-repair を有効にしないか」で説明しています。
:::

## 1. 前提を確認し、対象ノードを決める

- Basic08 で NMA と kube-prometheus-stack が導入済みであること。
- `k` と `KUBECONFIG` は Basic01 手順 2 の 4 行で設定済みであること。
- GPU ノードが 1 台以上動いていること。Basic07 の vLLM や Basic05 の Capacity Block のいずれかを稼働させておきます。
- 手順 4 の JSON パースに `python3` を使います。無い場合は `dnf install -y python3` などで入れておきます。

まず、NMA のエージェント本体と `dcgm-server` の両方が動いていることを確認します。

```bash
k get ds -n kube-system eks-node-monitoring-agent dcgm-server
```

実機出力です。`dcgm-server` が GPU ノードの数だけ `DESIRED` に上がっていれば、GPU taint への toleration が効いています。両者の `NODE SELECTOR` はどちらも `kubernetes.io/os=linux` ですが、`dcgm-server` を GPU ノードだけに限定しているのは `NODE SELECTOR` ではなく、GPU インスタンスタイプの許可リストを持つ nodeAffinity です。そのため `DESIRED` は、全ノードに載るエージェント本体と、GPU ノードだけに載る `dcgm-server` とで異なります。

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

Basic08 の [`observability.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/observability.tf) が入れた PrometheusRule のうち、`AcceleratedHardwareUnhealthy`（critical）がこの反転で発火します。Prometheus に port-forward してアラートの状態を確認します。port-forward の確立を確実に待つため、ready エンドポイントが応答するまで待ってから実行します。

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

これで、GPU 障害の注入から NodeCondition の反転、そして Prometheus アラートの発火までの検知経路の一連の流れが正しく機能していることを確認できました。

## 5. 注入をクリアして健全状態に戻す

注入した値は DCGM のキャッシュに残り続けるため、`dcgm-server` を再起動してキャッシュをクリアし、あわせてエージェント本体を再起動して接続をやり直させます。

```bash
k rollout restart ds -n kube-system dcgm-server
k rollout status ds -n kube-system dcgm-server

k delete pod -n kube-system -l app.kubernetes.io/name=eks-node-monitoring-agent \
  --field-selector spec.nodeName="$NODE"
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

なお `dcgm-server` を再起動したことで Pod 名が変わっているため、もう一度注入を試す場合は手順 1 の変数取得（`$DCGM` / `$NODE`）からやり直してください。古い `$DCGM` のまま `k exec` すると `NotFound` になります。

# まとめ

本章では、DCGM の 障害注入 で XID 79 を GPU に注入し、NMA が `AcceleratedHardwareReady` を理由 `NvidiaXID79Error` とともに `False` へ反転させ、Prometheus の `AcceleratedHardwareUnhealthy` アラートが発火するところまでを実機で確かめました。実際の GPU を壊さずに検知経路の端から端までを検証できるのが 障害注入 の価値です。あわせて次のことを見ました。NMA は GPU 健全性を、同梱の `nv-hostengine` をポート 5555 で動かす `dcgm-server` から読みます。これは GPU Operator の埋め込み dcgm-exporter がポート 9400 で公開するメトリクスとは別経路なので共存できますが、standalone DCGM は hostPort でホストのポート 5555 を確保するため競合します。そして GPU taint のために `dcgm-server` へ toleration を渡す必要があり、それを EKS アドオンの `configuration_values` で解決しています。検知が本当に動くことを平常時に確かめておけば、いざ GPU が壊れたときに「監視が入っていたのに気づけなかった」という事態を避けられます。

# 参考資料

- [Amazon EKS node monitoring agent](https://docs.aws.amazon.com/eks/latest/userguide/node-health.html)
- [Under the hood: Amazon EKS Auto Mode がノード障害を検知・修復・診断する仕組み](https://aws.amazon.com/jp/blogs/containers/under-the-hood-how-amazon-eks-auto-mode-detects-repairs-and-diagnoses-node-failures/)
- [NVIDIA DCGM](https://docs.nvidia.com/datacenter/dcgm/latest/)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
