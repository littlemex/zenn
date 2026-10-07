---
title: "Original: 010 稼働中の両方のスタックを確認する"
---

## はじめに

ターミナルはクラスタに到達できる状態になった。トラフィックを送る前に、2 つの構成が同じリソースを持ち、別々のノードで動いていることを確認する。

目的は、用意済みのデプロイを調べて両方のネームスペースが稼働していることを確かめることである。やることは、揃っているはずの設定ファイルを描画して比較すること、割り当てられたノードの一覧を読むこと、両方のネームスペースのエンジンとルータの Pod を確認することの 3 つである。この章は「準備」「統合構成のベースライン測定」と合わせて 8 分の共有時間になっている。

## 手順

1. 用意済みの設定を確認用に描画する。

```bash
python3 paired.py render
```

`results/paired/unified.json` と `results/paired/disaggregated.json` を開く。モデルのリビジョン、イメージのバージョン、キャッシュ方針、ルーティング方針、リソース要求を比較する。別々の Namespace オブジェクトと、プリエンプトしない PriorityClass オブジェクトになっていることを確認する。

2. 割り当てられたノードの一覧を読む。

```bash
export LAB_NODES="$(python3 -c 'import json; print(" ".join(json.load(open("config.unified.json"))["nodes"] + json.load(open("config.disaggregated.json"))["nodes"]))')"
kubectl --context "$LAB_CONTEXT" get nodes $LAB_NODES -o 'custom-columns=NODE:.metadata.name,TYPE:.metadata.labels.node\.kubernetes\.io/instance-type,GPUS:.status.allocatable.nvidia\.com/gpu,EFAS:.status.allocatable.vpc\.amazonaws\.com/efa'
```

g7.48xlarge のペアでは、割り当て可能なデバイス数は次のようになる (取得日: 2026-09-10)。

```
NODE                                         TYPE          GPUS   EFAS
<eks-node-a>   g7.48xlarge   8      2
<eks-node-b>   g7.48xlarge   8      2
```

ハードウェアによって `LAB_GPUS` の意味が変わる。g7.48xlarge では 1 構成あたり GPU 8 枚で、まとめて配置されたエンジン Pod はそれぞれ 2 つのプロセスを持ち、プロセスあたり GPU 4 枚、EFA デバイス 2 個を共有する。g7.24xlarge では 1 構成あたり GPU 4 枚で、プロセスあたり GPU 2 枚、EFA デバイス 1 個を共有する。以降に出てくる測定例のうち小さい方は、制約を加えた g7.48xlarge ホストで取った **g7.24xlarge 相当の割り当て** である。

3. 統合構成のネームスペースを確認する。

```bash
kubectl --context "$LAB_CONTEXT" -n "$LAB_UNIFIED_NAMESPACE" get pods -o wide
```

エンジンとルータが Running になっていて、コンテナが Ready になっていることを確認する。この確認画面は保存済みの Pod JSON から描画したもので、取得日は 2026-09-10 である。g7.48xlarge の場合は次のようになる。

![g7.48xlarge の統合構成 Pod の準備状況、保存済み JSON から 2026-09-10 に描画](/images/books/aim345-disaggregated-inference-workshop/shared-010-unified-pods-48.png)

g7.24xlarge 相当の割り当てでは次のようになる。

![g7.24xlarge 相当の割り当てにおける統合構成 Pod の準備状況、保存済み JSON から 2026-09-10 に描画](/images/books/aim345-disaggregated-inference-workshop/shared-010-unified-pods-24-equivalent.png)

4. 分離構成のネームスペースを確認する。

```bash
kubectl --context "$LAB_CONTEXT" -n "$LAB_DISAGG_NAMESPACE" get pods -o wide
```

もう一方の割り当てられたノードで、エンジンとルータが Ready になっていることを確認する。こちらも保存済みの Pod JSON から描画したもので、取得日は同じく 2026-09-10 である。g7.48xlarge の場合は次のようになる。

![g7.48xlarge の分離構成 Pod の準備状況、保存済み JSON から 2026-09-10 に描画](/images/books/aim345-disaggregated-inference-workshop/shared-010-disaggregated-pods-48.png)

g7.24xlarge 相当の割り当てでは次のようになる。

![g7.24xlarge 相当の割り当てにおける分離構成 Pod の準備状況、保存済み JSON から 2026-09-10 に描画](/images/books/aim345-disaggregated-inference-workshop/shared-010-disaggregated-pods-24-equivalent.png)

:::message alert
両方のスタックが同じ GPU 要求で準備できたときにだけ比較を始めること。セッションの間、両方のスタックを動かし続けること。
:::

:::details エンジン Pod が Pending のままの場合
フルの g7.48xlarge デプロイでは、メモリ要求が Kubernetes の割り当て可能メモリを超えたときに Pending のままになった。スケジューリングのイベントを確認する。

```bash
kubectl --context "$LAB_CONTEXT" -n "$LAB_UNIFIED_NAMESPACE" get events --sort-by=.lastTimestamp
```

描画済みファイルの中の CPU とメモリの要求を、ノードの一覧と比較する。スケジューリングのイベントと設定をセッションガイドに持っていき、用意済みのペアを修正してもらう。両方のネームスペースが準備できたら再開する。
:::

:::details なぜこうなるか
`paired.py` のヘルパーは、重複するノードの組やモデル・リソース設定の不一致を拒否する。まとめて配置する構成では、各スタックは 1 つのエンジン Pod の中に GPU リストが重ならない 2 つのプロセスを持つ。別々の割り当てにしておくことで、どちらの構成を測っている間も両方のエンドポイントを使える状態に保てる。Kubernetes は割り当て可能なリソースに対してスケジューリングを行い、これはノードの物理メモリより小さい。
:::

## まとめ

両方のネームスペースに、別々のノード上で Ready になったエンジンとルータが入っていること、そして配置・モデルのリビジョン・キャッシュとルーティングの方針・リージョン・ハードウェアのラベル・構成あたりの GPU 枚数をメモに残したことを確認する。次の「統合構成のベースライン測定」の章では、実際にトラフィックを流し始める。
