---
title: "Modified: 005 CloudShell を開いてクラスタに接続する"
---

## はじめに

配信スタックはすでに動いている。この章ではワークショップ用のアカウントを開き、ターミナルをそのクラスタにつなぐ。

目的は、CloudShell から両方の GPU ノードに到達できるようにし、スタックをデプロイしたときの設定を読み込むことである。やることは、割り当てられた AWS コンソールとリージョンを開くこと、CloudShell を EKS に接続すること、用意済みの参考実装のリビジョンをチェックアウトしてその環境を読み込むことの 3 つである。この章は「環境確認」「統合構成のベースライン測定」と合わせて 8 分の共有時間になっている。

## 手順

1. ワークショップのイベントページで **Open AWS console** を選ぶ。

2. コンソールのリージョンメニューで、イベントページに表示されているリージョンを選ぶ。

![コンソールのリージョンメニューに Europe (Spain) が表示されている例、2026-09-11 取得](/images/books/aim345-disaggregated-inference-workshop/shared-005-console-region.png)

この画面は Europe (Spain) の例である。自分のイベントのリージョンを選ぶこと。

3. コンソールのナビゲーションバーから **CloudShell** を開く。

![g7.48xlarge リハーサル用アカウントの CloudShell 歓迎ダイアログ、2026-09-11 取得](/images/books/aim345-disaggregated-inference-workshop/shared-005-cloudshell-first.png)

4. 歓迎ダイアログを閉じ、シェルのプロンプトが出るまで待つ。

![g7.48xlarge リハーサル用アカウントで準備ができた CloudShell のプロンプト、2026-09-11 取得](/images/books/aim345-disaggregated-inference-workshop/shared-005-cloudshell-ready.png)

5. **CloudFormation** を開き、自分のワークショップスタックを選んで **Outputs** タブを開く。

出力の中から `KubeconfigCommand` を探す。これにはクラスタ名とリージョンを含む `aws eks update-kubeconfig` コマンドが入っている。

6. `KubeconfigCommand` の値をそのまま CloudShell にコピーして実行する。

![g7.48xlarge リハーサル用ペアでの CloudShell kubeconfig コマンド、2026-09-11 取得](/images/books/aim345-disaggregated-inference-workshop/shared-005-cloudshell-kubeconfig.png)

この画面のコマンドは専用の kubeconfig ファイルを使っている。自分のスタックの出力は自分のアカウント用のコマンドを返す。

7. GPU ノードと、それぞれに割り当て可能なデバイス数を確認する。

```bash
kubectl get nodes -o 'custom-columns=NODE:.metadata.name,TYPE:.metadata.labels.node\.kubernetes\.io/instance-type,GPUS:.status.allocatable.nvidia\.com/gpu,EFAS:.status.allocatable.vpc\.amazonaws\.com/efa'
```

g7.48xlarge のペアでは、GPU ノードの行は次のように GPU と EFA のデバイス数を示す (取得日: 2026-09-10)。

```
NODE                                         TYPE          GPUS   EFAS
<eks-node-a>   g7.48xlarge   8      2
<eks-node-b>   g7.48xlarge   8      2
```

![g7.48xlarge ペアに対する CloudShell でのノード問い合わせ、2026-09-11 取得](/images/books/aim345-disaggregated-inference-workshop/shared-005-cloudshell-nodes.png)

GPU ノードが 2 台あることを確認する。g7.48xlarge ノードは GPU 8 枚と EFA デバイス 2 個、g7.24xlarge ノードは GPU 4 枚と EFA デバイス 1 個を持つ。CPU 用のシステムノードには GPU も EFA の割り当てもない。

8. `ControllerConfigCommand` のコマンドを使って、用意済みの設定をローカルファイルに保存する。

```bash
kubectl -n aim345-event-unified get configmap controller-config -o json > /tmp/aim345-controller-config.json
```

9. クラスタの準備段階で記録された参考実装のコミットを読み込む。コマンドの後半は、値が空のまま先に進まないようにする確認である。

```bash
COMPANION_COMMIT="$(python3 -c 'import json; print(json.load(open("/tmp/aim345-controller-config.json"))["data"]["companion-commit.txt"].strip())')" && export COMPANION_COMMIT
[ -n "$COMPANION_COMMIT" ] || { echo "STOP: COMPANION_COMMIT is empty; repeat step 8"; false; }
```

10. 参考実装のリポジトリをクローンする。

```bash
git clone --filter=blob:none --no-checkout https://github.com/awslabs/awsome-distributed-ai.git /tmp/aim345-companion
```

11. 用意済みのコミットを取得する。

```bash
git -C /tmp/aim345-companion fetch --depth=1 origin "$COMPANION_COMMIT"
```

12. そのコミットをチェックアウトする。

```bash
git -C /tmp/aim345-companion checkout --detach "$COMPANION_COMMIT"
```

13. 推論ラボのディレクトリに入る。

```bash
cd /tmp/aim345-companion/examples/use-cases/disaggregated-inference-prefill-decode
```

14. そのディレクトリに、用意済みの設定ファイルを復元する。

```bash
python3 - <<'CONFIG'
import json
from pathlib import Path
data = json.loads(Path("/tmp/aim345-controller-config.json").read_text())["data"]
for name in ("config.unified.json", "config.disaggregated.json"):
    Path(name).write_text(data[name])
CONFIG
```

15. Python の仮想環境を作る。

```bash
python3 -m venv .venv
```

16. 仮想環境を有効化する。

```bash
source .venv/bin/activate
```

17. 固定版のクライアント依存パッケージをインストールする。

```bash
python3 -m pip install -r requirements.txt
```

このチェックアウトが、後の章で使う採点し直しの道具を実行できることを確認する。

```bash
python3 rescore.py --help > /dev/null && echo "companion checkout ready" || { echo "STOP: this companion revision cannot run rescore.py; tell a facilitator"; false; }
```

`STOP` が表示された場合は、ファシリテータに修正済みのコミットを尋ね、手順 11 から 13 と手順 17 をやり直す。

18. コンテキスト、ネームスペース、インスタンスタイプ、1 構成あたりの GPU 枚数、リージョンを読み込む。最後の行はどれかの変数が空のまま先に進まないようにする確認である。

```bash
LAB_CONTEXT="$(python3 -c 'import json; print(json.load(open("config.unified.json"))["context"])')" && export LAB_CONTEXT
LAB_UNIFIED_NAMESPACE="$(python3 -c 'import json; print(json.load(open("config.unified.json"))["namespace"])')" && export LAB_UNIFIED_NAMESPACE
LAB_DISAGG_NAMESPACE="$(python3 -c 'import json; print(json.load(open("config.disaggregated.json"))["namespace"])')" && export LAB_DISAGG_NAMESPACE
LAB_INSTANCE_TYPE="$(python3 -c 'import json; print(json.load(open("config.unified.json"))["instance_type"])')" && export LAB_INSTANCE_TYPE
LAB_GPUS="$(python3 -c 'import json; print(2 * json.load(open("config.unified.json"))["gpus_per_worker"])')" && export LAB_GPUS
LAB_REGION="$(python3 -c 'import json; print(json.load(open("config.unified.json"))["context"].split(":")[3])')" && export LAB_REGION
[ -n "$LAB_CONTEXT" ] && [ -n "$LAB_UNIFIED_NAMESPACE" ] && [ -n "$LAB_DISAGG_NAMESPACE" ] && [ -n "$LAB_INSTANCE_TYPE" ] && [ -n "$LAB_GPUS" ] && [ -n "$LAB_REGION" ] || { echo "STOP: a LAB_ variable is empty; repeat this step in the lab directory"; false; }
```

:::message alert
CloudShell の環境が終わる前に結果を保存すること。参考実装のチェックアウトは `/tmp` の下にある。新しいターミナルを開くたびに、このページのディレクトリ移動・仮想環境の有効化・環境変数のエクスポートをやり直す必要がある。
:::

:::details ローカルターミナルを使う場合
Workshop Studio のアカウントページで **Get AWS CLI credentials** を開き、環境変数を設定するコマンドをローカルターミナルにコピーする。スタックの `KubeconfigCommand` を実行し、同じ ConfigMap・チェックアウト・環境変数の手順を続ける。ターミナルには AWS CLI、Git、Python、対応する `kubectl` が必要である。`ControllerConfigCommand` の出力はどちらのターミナルでも同じ設定を提供する。
:::

:::details なぜこうなるか
CloudShell はコンソールのセッションの ID を使う。Kubeconfig は `kubectl` がどの EKS クラスタに接続するかを伝え、用意済みの設定は配信用の別々のネームスペースとリソース要求を特定する。同じ ConfigMap から参考実装のコミットを読むことで、コントローラのスクリプトとデプロイ済みのペアを揃えられる。
:::

## まとめ

2 台の GPU ノードへ問い合わせて GPU と EFA の数を記録できること、そしてシェルに用意済みの設定ファイル、有効化済みの仮想環境、1 構成分の `LAB_GPUS` が揃っていることを確認する。次の「環境確認」の章では、両方のネームスペースが実際に準備できているかを見ていく。
