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

## 技術面接の準備 (FAQ)

この節は、本章の内容を技術面接で聞かれたときに答えるための想定問答である。システム設計面接の流れ (要件の確認、全体の設計、深掘り、トレードオフ、故障と検証) に沿って並べ、各問は英語の質問、日本語の質問、英語の回答、日本語の回答の順に書いた。回答は本章に書いた事実だけを使う。

:::details Q1. What was the requirement, and what is the overall architecture?
**質問 (日本語)**

要件は何で、全体の構成はどうなっているか。

**Answer (English)**

The repository's Prometheus and Grafana monitoring stack was written only for SageMaker HyperPod, so ParallelCluster compute nodes couldn't be monitored. The architecture is: node_exporter on each node exposes CPU, memory, disk and network metrics on port 9100; a Prometheus agent in the cluster scrapes every minute and remote-writes to Amazon Managed Service for Prometheus; Grafana reads from there. My design principle was to use the same metrics collection pipeline in both environments, and change only how node_exporter is installed on each node.

**回答 (日本語)**

リポジトリの Prometheus と Grafana の監視の仕組みが SageMaker HyperPod 向けにしか書かれておらず、ParallelCluster の計算ノードを監視できなかった。構成は、各ノードの node_exporter がポート 9100 で CPU、メモリ、ディスク、ネットワークの値を出し、クラスタの中の Prometheus の agent が 1 分ごとに読んで Amazon Managed Service for Prometheus に送り、Grafana がそこから表示する。設計の方針は、収集の側を 1 つにそろえ、ノードに exporter を入れる方法だけを環境ごとに分けることだった。
:::

:::details Q2. HyperPod runs node_exporter in a container. Why install a binary with systemd on ParallelCluster?
**質問 (日本語)**

HyperPod は node_exporter をコンテナで動かしている。なぜ ParallelCluster ではバイナリを systemd で動かしたのか。

**Answer (English)**

ParallelCluster prepares compute nodes with custom actions such as `OnNodeConfigured`, which runs at the end of node setup, so installing a binary there is the straightforward fit. The script can be referenced directly by its GitHub raw URL, so users don't need to stage it in S3. HyperPod's lifecycle script approach couldn't be reused as is.

**回答 (日本語)**

ParallelCluster は計算ノードの準備に `OnNodeConfigured` などのカスタムアクションを使い、`OnNodeConfigured` はノードの準備の最後に動くので、そこでバイナリを入れる形が素直である。スクリプトは GitHub の raw の URL からそのまま参照でき、利用者が S3 に置く手間も要らない。HyperPod のライフサイクルスクリプトの形はそのままでは使えなかった。
:::

:::details Q3. How did you make the install script safe to run in production?
**質問 (日本語)**

導入のスクリプトを本番で動かしても安全にするために、何をしたか。

**Answer (English)**

It downloads node_exporter (default 1.9.1) from GitHub releases, verifies it against the publisher's `sha256sums.txt`, and runs it as a systemd service under a dedicated user. Downloads are attempted up to three times in total, it supports x86_64 and aarch64, and it does nothing if the exporter is already running, because a cluster update can call it again. Review caught that `sha256sum --check` opens filenames relative to the current directory, so the check now runs inside the temp directory where the tarball is. One honest limit: a checksum file from the same release catches a corrupted download, not a compromised release.

**回答 (日本語)**

node_exporter (既定は 1.9.1) を GitHub のリリースから落とし、配布元の `sha256sums.txt` で検証し、専用のユーザーで systemd のサービスとして動かす。ダウンロードは初回を含めて 3 回まで試し、x86_64 と aarch64 の両方に対応し、すでに動いていれば何もしない。クラスタの更新で再び呼ばれることがあるからである。レビューでは、`sha256sum --check` が標準入力から読んだファイル名をカレントディレクトリから開くことを指摘され、tarball のある一時ディレクトリに移ってから検証する形にした。限界もはっきり言う。同じリリースのチェックサムの一覧で防げるのはダウンロードの破損で、配布元の改ざんではない。
:::

:::details Q4. What was wrong with the collector config?
**質問 (日本語)**

collector の設定の何が誤っていたか。

**Answer (English)**

Two things. `scrape_timeout` was 5 minutes while `scrape_interval` was 1 minute, which violates Prometheus's rule that the timeout must not exceed the interval; I set it to 30 seconds. And the `efa_node_exporter` job was filtered to specific GPU instance types, which dropped CPU, Trainium and Inferentia nodes; node_exporter is hardware-independent, so I removed the filter. To be precise, the Issue said Prometheus 3 would reject the old timeout, but I didn't verify that rejection in this PR.

**回答 (日本語)**

2 つある。`scrape_timeout` が 5 分で `scrape_interval` の 1 分より長く、timeout は interval 以下という Prometheus の決まりに反していたので、30 秒にした。また `efa_node_exporter` の job が特定の GPU のインスタンスタイプに絞られていて、CPU、Trainium、Inferentia のノードが外れていた。node_exporter はハードウェアに依存しないので絞り込みを外した。正確に言うと、Issue は Prometheus 3 系で古い timeout が弾かれると書いたが、この PR では弾かれることを確かめていない。
:::

:::details Q5. Installing the exporter wasn't enough. What else was needed?
**質問 (日本語)**

exporter を入れるだけでは足りなかった。ほかに何が要ったか。

**Answer (English)**

The network path. The agent has to reach port 9100 on each node, so the compute queue's `Networking.AdditionalSecurityGroups` must include the monitoring security group that the agent stack creates. In a monitoring design you check three things separately: how the exporter gets onto the node, what the collector targets, and whether the network path from the scraper to the node exists.

**回答 (日本語)**

ネットワークの経路である。agent が各ノードのポート 9100 に届く必要があるので、計算ノードのキューの `Networking.AdditionalSecurityGroups` に、agent のスタックが作る監視用のセキュリティグループを足す。監視の設計では、ノードへの導入の方法、収集の対象、読みに行く側からノードへのネットワークの経路、の 3 つを分けて確かめる。
:::

:::details Q6. How did you test it, and were there any discrepancies?
**質問 (日本語)**

どう試し、食い違いはあったか。

**Answer (English)**

The PR's test plan listed ParallelCluster 3.12.0 with two c5n.9xlarge nodes, but the actual record came from a ParallelCluster 3.14.2 cluster in ap-northeast-1. I recreated the agent stack; the cluster config already had this branch's script in `OnNodeConfigured`, and the `pcluster update-cluster` diff was one change, swapping the monitoring security group. After the update I started the compute fleet and checked Grafana. Another discrepancy: the PR description said node_exporter 1.8.2 while the script defaulted to 1.9.1. The reviewer treated the code as the source of truth, so 1.9.1 is what runs.

**回答 (日本語)**

PR の Test Plan は ParallelCluster 3.12.0 と c5n.9xlarge の 2 台を挙げたが、実際の記録は ap-northeast-1 の ParallelCluster 3.14.2 のクラスタで取った。agent のスタックを作り直し、クラスタの設定の `OnNodeConfigured` にはこのブランチのスクリプトがすでに入っていて、`pcluster update-cluster` の変更は監視用のセキュリティグループを差し替える 1 件だった。更新の後に計算のフリートを起動し、Grafana を確かめた。もう 1 つの食い違いは、PR の説明が node_exporter を 1.8.2 と書き、スクリプトの既定が 1.9.1 だったことである。レビューはコードを正とし、使われるのは 1.9.1 である。
:::

## まとめ

PR #1043 は、HyperPod と共通の監視の仕組みを ParallelCluster でも使えるように、計算ノードへの node_exporter の導入スクリプトを足し、collector の timeout と対象の選び方、導入の手順を直した。別の環境に監視を広げるときも、ノードへの導入の方法、収集の対象、ネットワークの経路を分けて確かめると役立つ。
