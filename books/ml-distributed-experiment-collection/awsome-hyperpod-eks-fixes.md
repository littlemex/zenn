---
title: "Awsome: HyperPod on EKS のテンプレートを直す"
free: false
---

## はじめに

本章では、SageMaker HyperPod を EKS で作るためのテンプレートまわりの 3 件をまとめる。[PR #1206](https://github.com/awslabs/awsome-distributed-ai/pull/1206) は Lambda の起動の失敗を直し、[Issue #1205](https://github.com/awslabs/awsome-distributed-ai/issues/1205)（RFC）は同じ仕組みの重複を解消する方向を提案し、[PR #1188](https://github.com/awslabs/awsome-distributed-ai/pull/1188) は古いワークショップのリンクを新しいドキュメントに移した。

:::details CloudFormation のカスタムリソースと Lambda Layer
CloudFormation は、テンプレートに書いた AWS のリソースを作る。AWS のリソースではない処理（ここでは Helm のチャートのインストール）は、カスタムリソースとして Lambda の関数に任せる。Lambda Layer は、関数とは別に配る追加のファイルで、ここでは git、helm、kubectl と、それらが使うライブラリが入っている。
:::

## Issue と PR の概要

| 番号 | 解決したいこと | やったこと | マージ |
| --- | --- | --- | --- |
| PR #1206 | `helm-chart-installer` の Lambda が起動の時点で `cannot import name 'ssl'` で失敗し、HyperPod on EKS の作成が止まる | テンプレートから `LD_LIBRARY_PATH` の 1 行を消した | [a5e8a1c](https://github.com/awslabs/awsome-distributed-ai/commit/a5e8a1c8569080fd219d1064a94d7cb3e51b2e2f) |
| Issue #1205 | 同じ役割の Helm のインストーラが、このリポジトリとサービスチームのリポジトリの 2 か所で別々に保守されている | サービスチームのリポジトリに寄せる提案（本章の執筆時点で開いたまま） | ― |
| PR #1188 | ドキュメントが、廃止されたワークショップのカタログの URL を指している | 新しいドキュメントのサイトの URL に移した | [fbc7184](https://github.com/awslabs/awsome-distributed-ai/commit/fbc71843add50e7c0be506027597493447741b2a) |

## PR #1206: なぜ ssl の import が失敗したか

![Lambda の python3.12 の _ssl は OPENSSL_3.3.0 を必要とするが、LD_LIBRARY_PATH が Layer の古い libcrypto を先に読ませて失敗する。1 行を消すとランタイムの libcrypto を読み、Layer の git、helm、kubectl はそのまま動く](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/ml-distributed-experiment-collection/zenn/books/ml-distributed-experiment-collection/awsome-hyperpod-eks-fixes/lambda-ssl.png)

エラーの文面は `urllib3` の import の失敗だが、実際に失敗していたのは Python の標準の `ssl` モジュールの読み込みである。Lambda Layer が独自の OpenSSL を同梱していて、関数が `LD_LIBRARY_PATH=/opt/python/lib` を設定していたので、python3.12 の `_ssl` がランタイムの `libcrypto` ではなく Layer の古い `libcrypto` を読み、必要な `OPENSSL_3.3.0` のシンボルを解決できなかった。

```
/opt/python/lib/libcrypto.so.3: version 'OPENSSL_3.3.0' not found
```

[helm-chart-stack.yaml](https://github.com/awslabs/awsome-distributed-ai/blob/a5e8a1c8569080fd219d1064a94d7cb3e51b2e2f/1.architectures/7.sagemaker-hyperpod-eks/cfn-templates/nested-stacks/helm-chart-stack.yaml) から、この環境変数の 1 行を消した。Layer と関数の zip は変えていない。

直し方を 1 行の削除に留め、RFC の議論から切り離して単独の PR にしたのは、`main` が起動の時点で壊れていて、重複をどこに寄せるかの議論とは別に直せるからである。Layer と関数の zip を変えないので、差分はテンプレートの 1 行で済む。git の履歴からは、この環境変数が足された理由はわからなかった。筆者は Issue で、この変更を本当の修正ではなく回避策と位置づけている。

テストは、このリポジトリの関数と Layer の実物を、Lambda の実際のランタイムのイメージ `public.ecr.aws/lambda/python:3.12`（x86_64）に入れて行った。arm64 では確かめていない。環境変数があると import に失敗し、無いと import が通ることを確かめた。そのうえで、実際の HyperPod on EKS の作成が、このステップを越えて最後まで進むことを確かめた。

## Issue #1205: 重複をどこに寄せるか

調べる中で、同じ役割の Helm のインストーラが、サービスチームの [aws/sagemaker-hyperpod-cluster-setup](https://github.com/aws/sagemaker-hyperpod-cluster-setup) にもあることがわかった。こちらも同じ `LD_LIBRARY_PATH` を設定しているが、Layer が `libcrypto` と `libssl` を同梱していない。筆者が両方の関数と Layer の実物を x86_64 の python3.12 のイメージで動かした範囲では、このリポジトリの側は同じ import のエラーになり、サービスチームの側（[36318df](https://github.com/aws/sagemaker-hyperpod-cluster-setup/blob/36318df66d8baf5dc74ac369a333295902c47dc0/eks/cloudformation/helm-chart-template.yaml#L154) の時点）は import が通った。2 つのインストーラは別々に作られていて、Layer と `lambda_function.py` の中身がすでに違う。そのため、同じ設定でも、OpenSSL を同梱した Layer を持つこのリポジトリの側だけが壊れた。この不具合は、重複が見つかるきっかけになった。

RFC では、公式のドキュメントが案内しているサービスチームのリポジトリに寄せ、このリポジトリの `cfn-templates/` をやめる方向を提案した。やめるにはこのリポジトリのメンテナの合意が要り、既存のスタックの移行は強制しない。既存のスタックをサービスチームのテンプレートでそのまま更新すると、EKS や HyperPod のクラスタが作り直されるおそれがあり、既存のスタックが参照する S3 の上のテンプレートや Lambda の zip を残すかも決める必要がある。

寄せる時期にも条件がある。このリポジトリのテンプレートは、継続的なノードの準備（`NodeProvisioningMode: Continuous`）が既定で有効である。サービスチームのリポジトリでは既定が空（1 つのインスタンスグループが埋まらないと全体が失敗する従来の方式）で、既定を変える [aws/sagemaker-hyperpod-cluster-setup#5](https://github.com/aws/sagemaker-hyperpod-cluster-setup/pull/5) は、コメントを書いた時点では開いたままだった。筆者は Issue へのコメントで、#5 か同等の変更が入るまで切り替えを待つべきだと提案した。すべての CloudFormation のリソースの 1 対 1 の照合と、既存のスタックを移す検証は、まだしていない。

## PR #1188: リンクの移行

README など 3 つのファイルの 5 か所のリンクを、新しいドキュメントのサイトに移した。S3 のオブジェクトの場所に使われているワークショップの ID は、ドキュメントのリンクではないので変えなかった。レビューで KeitaW さんから、同じ古い URL が `automate-smhp-eks/` の 2 つのスクリプトの実行時の表示にも残っていると指摘があり、[マージした差分](https://github.com/awslabs/awsome-distributed-ai/commit/fbc71843add50e7c0be506027597493447741b2a)ではその 2 つのスクリプトも変わっている。テストは、最初の 3 つのファイルで古い URL が `grep` で見つからないことと、新しい URL が 5 か所にあることを確かめ、Markdown の表示でリンクを確かめた。インフラのデプロイはしていない。

## 技術的な要点

- **`LD_LIBRARY_PATH` で起きる上書き**。ランタイムが持つライブラリを、同梱した古いライブラリで上書きすることがある。エラーの文面が上の層（ここでは urllib3）のものでも、動的リンカのエラーで実際に失敗している層を確かめる。
- **Lambda の起動の失敗の再現**。Lambda の公式のランタイムのイメージに、実物の関数と Layer を入れると、手元で再現できる。
- **同じ役割の実装が 2 か所にあるとき**。片方を直すだけでなく、どちらを正にするかを提案する。ただし、正にする側の既定が劣るなら、追いつくまで切り替えを待つ。
- **リンクの移行**。ドキュメントだけでなく、スクリプトが実行時に表示する文字列も探す。

## まとめ

SSL の失敗は `LD_LIBRARY_PATH` の 1 行の削除で直し、インストーラを 2 か所で保守する重複の解消は、別の RFC として提案した。直すことと、どこに寄せるかを決めることを分けたので、利用者は動くテンプレートを使いながら、移行の条件を確かめられる。
