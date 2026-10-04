---
title: "Awsome: ParallelCluster の計算ノードを監視する"
free: false
---

## はじめに

本章では、Prometheus と Grafana の監視の仕組みを AWS ParallelCluster の計算ノードでも使えるようにした [Issue #1042](https://github.com/awslabs/awsome-distributed-ai/issues/1042) と [PR #1043](https://github.com/awslabs/awsome-distributed-ai/pull/1043) をまとめる。

:::details node_exporter と Prometheus の agent
node_exporter は、Linux の CPU、メモリ、ディスク、ネットワークなどの値をポート 9100 で公開するプログラムである。Prometheus はそれを一定の間隔で読みに行き（scrape）、時系列として保存する。このリポジトリの監視の仕組みでは、Prometheus の agent がクラスタの中で各ノードを読み、値を Amazon Managed Service for Prometheus に送り、Grafana がそれを表示する。
:::

## Issue と PR の概要

![計算ノードでは OnNodeConfigured の段階で install-node-exporter.sh が node_exporter を入れ、Prometheus の agent が 1 分ごとに読んで Amazon Managed Service for Prometheus に送り、Grafana が保存された値を表示する](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/awsome-pcluster-monitoring/monitoring.png)

| 項目 | 内容 |
| --- | --- |
| 解決したいこと | 監視の仕組みが SageMaker HyperPod 向けにしか書かれておらず、ParallelCluster の計算ノードを監視できない |
| やったこと | ParallelCluster 用の node_exporter の導入スクリプトを足し、collector の設定の誤りを直し、README を両方の環境向けに書き直した |
| テスト | ap-northeast-1 の ParallelCluster 3.14.2 のクラスタで、導入のスクリプトを含む計算ノードの設定のまま、監視用のセキュリティグループを差し替えて更新し、Grafana の画面を確かめた |
| マージ | [edeb016](https://github.com/awslabs/awsome-distributed-ai/commit/edeb0163615b341389ccee7bb2609668be410e10) |

## 何を解決しようとしたか

HyperPod では、ライフサイクルスクリプトがコンテナで node_exporter を動かす。ParallelCluster は計算ノードの準備に `OnNodeConfigured` などのカスタムアクションを使うので、同じスクリプトをそのまま使えない。さらに collector の設定に 2 つの問題があった。`scrape_timeout: 5m` が `scrape_interval`（1 分）より長く、Prometheus の決まり（timeout は interval 以下）に反していた。`efa_node_exporter` の job が特定の GPU のインスタンスタイプだけに絞られていて、CPU、Trainium、Inferentia のノードが監視から外れていた。

## どうやったか

[install-node-exporter.sh](https://github.com/awslabs/awsome-distributed-ai/blob/edeb0163615b341389ccee7bb2609668be410e10/1.architectures/2.aws-parallelcluster/post-install-scripts/install-node-exporter.sh) を足した。node_exporter のバイナリ（既定は 1.9.1）を GitHub のリリースから落とし、配布元の `sha256sums.txt` で検証し、専用のユーザーで systemd のサービスとして動かす。ダウンロードは 3 回まで試し、x86_64 と aarch64 の両方に対応し、すでに動いていれば何もしない。[collector の設定](https://github.com/awslabs/awsome-distributed-ai/blob/edeb0163615b341389ccee7bb2609668be410e10/4.validation_and_observability/4.prometheus-grafana/1click-dashboards-deployment/prometheus-agent-collector.yaml) は、`scrape_timeout` を 30 秒にし、インスタンスタイプの絞り込みを外した。

ParallelCluster の側では、計算ノードのキューの `CustomActions.OnNodeConfigured` にこのスクリプトを足し、同じキューの `Networking.AdditionalSecurityGroups` に、agent のスタックが作る監視用のセキュリティグループを足す。agent がノードのポート 9100 に届くのは、このセキュリティグループがあるからである。

## なぜそうしたか

ParallelCluster の `OnNodeConfigured` はノードの準備の最後に動くスクリプトなので、バイナリを入れる形が素直である。スクリプトは GitHub の raw の URL からそのまま参照でき、S3 に置く手間も要らない。node_exporter はハードウェアに依存しないので、インスタンスタイプで絞る理由は無い。

レビューでは KeitaW さんから 3 回に分けて指摘があり、マージした版のスクリプトには次の 3 つが入っている。

- **スクリプトの置き場所**。最初は `utils/` に置いた。レビューで、`utils/` は手元で使う道具の置き場で、計算ノードの準備で動くスクリプトとは種類が違うと指摘され、`post-install-scripts/` に移した。
- **チェックサムの検証**。`sha256sum --check` は、標準入力から読んだファイル名をカレントディレクトリから開く。tarball は一時ディレクトリにあるので、`(cd "$TMP_DIR" && ... | sha256sum --check --status)` の形にした。
- **変数の引用符**。算術の比較の中の変数を引用符で囲み、ShellCheck の SC2086 にそろえた。

PR の説明は node_exporter の版を 1.8.2 と書いていて、スクリプトの既定の 1.9.1 とずれていた。レビューは「コードが正」としていて、使われるのは 1.9.1 である。README の `PCClusterNAME` のパラメータが、CloudFormation のテンプレートの中で `PARALLELCLUSTER_NAME` に正しくつながるかも問われ、筆者はテンプレートの該当行を示して確かめた。

## テストをどうしたか

PR の Test Plan は ParallelCluster 3.12.0 と c5n.9xlarge の計算ノード 2 台を挙げている。実際の記録は、ap-northeast-1 の ParallelCluster 3.14.2 のクラスタで取った。agent のスタックを作り直した。更新に使った設定には、`OnNodeConfigured` にこのブランチの導入のスクリプトがすでに入っていた。`pcluster update-cluster` の変更は、`AdditionalSecurityGroups` の監視用のセキュリティグループを、新しい agent のスタックのものに差し替える 1 件だった。更新の後に計算のフリートを起動し、Grafana の画面を確かめた。

## 技術的な要点

- **`sha256sum --check` が開くファイル**。標準入力から読んだファイル名を、カレントディレクトリから開く。ファイルのあるディレクトリに移ってから検証する。
- **Prometheus の timeout**。`scrape_timeout` は `scrape_interval` 以下にする。[設定の仕様](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config)がそう定めている。Issue は Prometheus 3 系の検証で問題になると書いたが、この PR では弾かれることを確かめていない。
- **何度動いても同じになる準備のスクリプト**。すでに動いていれば何もしないようにする。クラスタの更新で再び呼ばれることがある。
- **監視の経路**。exporter を入れるだけでは読めない。読みに行く側から届くように、セキュリティグループも足す。
- **2 つの環境で同じ監視**。収集の側は 1 つにそろえ、ノードに入れる方法だけを環境ごとに分ける。

## まとめ

PR #1043 は、HyperPod と共通の監視の仕組みを ParallelCluster でも使えるように、計算ノードへの node_exporter の導入スクリプトを足し、collector の timeout と対象の選び方、導入の手順を直した。別の環境に監視を広げるときも、ノードへの導入の方法、収集の対象、ネットワークの経路を分けて確かめると役立つ。
