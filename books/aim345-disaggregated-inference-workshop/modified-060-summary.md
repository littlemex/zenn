---
title: "Modified: 060 分離/非分離の判断をチームに持ち帰る"
---

## はじめに

両方の稼働中のスタックを測定し、自分のワークロードのためにどちらを残すかの暫定的な選択をした。モデル、GPU 割り当て、キャッシュとルーティングの方針、SLO と一緒にその選択を残しておけば、トラフィックが変わったときにチームが同じ比較をやり直せる。この章には 3 分が割り当てられている。

## 判断のルールを使う

自分のサービスが約束する上限のもとで、必要な実効 calls/s/GPU でより高い到着率まで条件を満たすアーキテクチャを選び、容量を計画する前に測り直して確認する。g7.24xlarge 相当の割り当てで 2026-10-03 に記録した測り直しでは、どの約束のもとでも分離が勝つことはなかった。Optional の「約束の地図を深く読む」の章がその理由を示している。

| ワークロードの条件 | すべき比較 |
|---|---|
| 実効的なプロンプトが短い、またはプレフィックスの再利用が多い | ハンドオフのコストと、キャッシュヒットの後に残る prefill の作業量を比較する |
| プロンプトが長く、prefill が待ち行列を作る | 両方の構成の達成率と実効 calls/s/GPU を比較する |
| prefill が混んでいるのに decode に余力がある | 独立した prefill のスケーリングを評価し、より大きな GPU 予算を記録する |
| モデル、プレフィックス再利用、到着パターンが変わる | 整合させたスイープをやり直し、新しい結果を採点し直し、判断を更新する |

## 結果を保存して片付ける

まだ実行していない場合は、Optional の「約束の地図を深く読む」の章の手順 4 と手順 5 を今実行しておくと、アーカイブにその結果も含められる。

結果、トラフィック、設定、参考実装のコミットを、`/tmp` のチェックアウトより長く残る CloudShell のホームディレクトリにアーカイブする。

```bash
cd /tmp/aim345-companion/examples/use-cases/disaggregated-inference-prefill-decode && git rev-parse HEAD > results/companion-commit.txt && tar -czf "$HOME/aim345-results.tgz" results traffic config.unified.json config.disaggregated.json && echo "Download file path: $HOME/aim345-results.tgz"
```

**Actions > Download file** と表示されたパスでダウンロードする。[早見表](/static/cheatsheet-aim345.md) とその [印刷用レンダリング](/static/cheatsheet-aim345.pdf)、完成させた判断表も残しておく。

**Ctrl+C** で両方のポートフォワードを止める。このペアを使い終えたら、ネームスペースと PriorityClass を削除する。

```bash
python3 paired.py cleanup
```

g7.48xlarge のペアでの片付けの記録例は次のとおりである (取得日: 2026-09-10)。ネームスペース名は自分の設定ファイルから来る。

```
Warning: deleting cluster-scoped resources, not scoped to the provided namespace
namespace "aim345-g7full-unified" deleted
Warning: deleting cluster-scoped resources, not scoped to the provided namespace
priorityclass.scheduling.k8s.io "aim345-g7full-unified-nonpreempting" deleted
Warning: deleting cluster-scoped resources, not scoped to the provided namespace
namespace "aim345-g7full-disaggregated" deleted
Warning: deleting cluster-scoped resources, not scoped to the provided namespace
priorityclass.scheduling.k8s.io "aim345-g7full-disaggregated-nonpreempting" deleted
```

専用ノードのモデルキャッシュは、再利用のために残り続ける。

## レイテンシの変化を説明する

:::details セッションの後に、それぞれのレイテンシの変化について調べる証拠
| 観測されたこと | 調べる証拠 | 対応 |
|---|---|---|
| 最初のリクエストで TTFT が高い | コールドスモークの記録とウォームアップ済みの測定ファイル | 同じウォームアップ状態の実行同士を比較する |
| TPOT は低いままなのに達成率が落ちる | TTFT、失敗・スキップした呼び出し、達成率の分母 | 両方の上限を満たす到着率を選ぶ |
| まとめて配置した場合にカウンタマップが空になる | `placement`、ライブのセレクタ、バックエンドのログ | ローカルなハンドオフであること、バイト測定が不可能であることを記録する |
| 別ノード間のハンドオフで正の RDMA バイトが出る | プロバイダ・GPU 登録のログ、リクエストの完了 | 転送の証拠を結果と一緒に残す |
| 理想的な転送時間は小さいのに TTFT の差が大きい | モデルの実行、スケジューリング、協調のトレース | 残りのコストすべてを接続のセットアップに割り当てずに調査する |
:::

## 自分のアカウントでこれを再現する

:::details 自分のアカウントで比較を再現する手順
[参考実装の README](https://github.com/awslabs/awsome-distributed-ai/blob/riv2026/aim345-content/examples/use-cases/disaggregated-inference-prefill-decode/README.md) に従い、次の順番で進める。

1. **前提条件と固定バージョン**: 同じアベイラビリティゾーンの GPU ノード、EFA インターフェース、互換性のあるドライバ、動作するデバイスプラグインを割り当てた EKS クラスタを用意する。割り当て可能な GPU/EFA 数を読み、モデルとキャッシュを用意済みのノードストレージに置く。
2. **イメージと設定の準備**: Python 環境を作り、`requirements.txt` をインストールし、固定版のエンジンと別の CPU ルータのイメージをビルドして検証し、自分のレジストリに公開し、イミュータブルなダイジェストと割り当てたノードで設定を埋める。
3. **別々の割り当て上にペアのエンドポイントを作る**: `config.example.json` を `config.unified.json` と `config.disaggregated.json` にコピーし、別々のネームスペース、重複しないノード、揃えた設定、1 構成あたり 1 ノードの `packed` 配置を設定する。`python3 paired.py render` を実行し、続けて `python3 paired.py deploy` を実行する。
4. **準備状態と転送**: 文書化された環境変数を読み込み、エンジンとルータを待ち、両方のポートフォワードを開き、`python3 paired.py verify --output results/paired-before` を実行し、README にある転送検証コマンドを実行する。
5. **トラフィックと収集**: 両方のトラフィックファイルを生成し、両方の shape を両方のエンドポイントに通し、エンジンの証拠と比較用 CSV を収集し、`python3 paired.py verify --output results/paired-after` を実行する。
6. **測定結果を読み、クロスオーバーを特定する**: 条件を満たす行から判断用ワークシートを埋め、同じ結果ディレクトリに対して `rescore.py` を実行し、どの約束のもとでどちらの構成が勝つかを見る。境界に近い到着率は逆順で測り直し、持続容量の推定として使う前に提供ウィンドウを延ばす。失敗しやすい高い到着率がそれより低い到着率の実行を止めてしまわないよう、それらのスイープには `--stop-failure-fraction 1` を渡し、30 秒の実行とは分けて採点し直す。
7. **片付けとローカルの確認**: 証拠を保存し、上の片付けコマンドでペアを削除し、参考実装のローカルチェックを実行する。自分が作ったインフラは、その固有のデプロイフローを通して削除する。
:::

[参考実装のコード](https://github.com/awslabs/awsome-distributed-ai/tree/riv2026/aim345-content/examples/use-cases/disaggregated-inference-prefill-decode)、[判断用ワークシート](https://github.com/awslabs/awsome-distributed-ai/blob/riv2026/aim345-content/examples/use-cases/disaggregated-inference-prefill-decode/DECISION-FRAMEWORK.md)、[検証記録](https://github.com/awslabs/awsome-distributed-ai/blob/riv2026/aim345-content/examples/use-cases/disaggregated-inference-prefill-decode/VALIDATION.md) で続きを読める。

## まとめ

60 分のセッションの後、別途用意されたフルの g7.48xlarge ペアで Optional の DeepSeek-V4-Flash の演習を進められる。この章で保存した結果と判断の記録は、トラフィックの変化に応じて同じ比較をやり直すための出発点になる。
