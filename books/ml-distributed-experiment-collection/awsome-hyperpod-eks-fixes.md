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

## 技術面接の準備 (FAQ)

この節は、本章の内容を技術面接で聞かれたときに答えるための想定問答である。システム設計面接の流れ (要件の確認、全体の設計、深掘り、トレードオフ、故障と検証) に沿って並べ、各問は英語の質問、日本語の質問、英語の回答、日本語の回答の順に書いた。回答は本章に書いた事実だけを使う。

:::details Q1. The error said urllib3 failed to import. What was actually failing?
**質問 (日本語)**

エラーは urllib3 の import の失敗だった。実際には何が失敗していたのか。

**Answer (English)**

Python's standard `ssl` module. The Lambda Layer bundled its own OpenSSL, and the function set `LD_LIBRARY_PATH=/opt/python/lib`. So python3.12's `_ssl` loaded the Layer's older `libcrypto` instead of the runtime's, and couldn't resolve the symbols it needs. The dynamic linker said so directly: `/opt/python/lib/libcrypto.so.3: version 'OPENSSL_3.3.0' not found`. The urllib3 error was just the top of the import chain. The general lesson: when the message names a high-level library, look for the dynamic linker error to find the layer that's really failing.

**回答 (日本語)**

Python の標準の `ssl` モジュールである。Lambda Layer が独自の OpenSSL を同梱していて、関数が `LD_LIBRARY_PATH=/opt/python/lib` を設定していたので、python3.12 の `_ssl` がランタイムではなく Layer の古い `libcrypto` を読み、必要なシンボルを解決できなかった。動的リンカがそれをそのまま言っていた。`/opt/python/lib/libcrypto.so.3: version 'OPENSSL_3.3.0' not found` である。urllib3 のエラーは import の連鎖の上端にすぎない。一般的な教訓は、エラーの文面が上の層のライブラリを指していても、動的リンカのエラーを探して実際に失敗している層を確かめることである。
:::

:::details Q2. Why a one-line fix in its own PR, separate from the RFC?
**質問 (日本語)**

なぜ 1 行の修正を、RFC と切り離した単独の PR にしたのか。

**Answer (English)**

`main` was broken at startup, and that could be fixed independently of the larger question of where to consolidate the duplicated installer. Removing the one `LD_LIBRARY_PATH` line from `helm-chart-stack.yaml` fixed it without touching the Layer or the function zip, so the diff stayed one line and easy to review. I was explicit in the Issue that this is a workaround, not the real fix. git history didn't explain why the variable had been added, so the evidence that nothing else needed it is the test: in the official Lambda runtime image the import succeeded without it, and an actual HyperPod on EKS creation completed.

**回答 (日本語)**

`main` が起動の時点で壊れていて、重複したインストーラをどこに寄せるかという大きな議論とは別に直せたからである。`helm-chart-stack.yaml` から `LD_LIBRARY_PATH` の 1 行を消せば、Layer と関数の zip に触れずに直り、差分は 1 行でレビューしやすい。Issue では、これは本当の修正ではなく回避策だとはっきり書いた。git の履歴からは、この環境変数が足された理由はわからなかったので、他に頼るものが無いことの根拠は試験である。Lambda の公式のランタイムのイメージでは変数が無くても import が通り、実際の HyperPod on EKS の作成も最後まで進んだ。
:::

:::details Q3. How did you test a Lambda startup failure?
**質問 (日本語)**

Lambda の起動の失敗をどう試したか。

**Answer (English)**

I put the repository's actual function and Layer into the official Lambda runtime image, `public.ecr.aws/lambda/python:3.12` on x86_64, and confirmed the import fails with the variable and succeeds without it. That reproduces the real runtime locally. Then I confirmed that an actual HyperPod on EKS creation got past this step and completed. I didn't test arm64, and I'd say so.

**回答 (日本語)**

リポジトリの関数と Layer の実物を、Lambda の公式のランタイムのイメージ `public.ecr.aws/lambda/python:3.12` (x86_64) に入れ、環境変数があると import に失敗し、無いと通ることを確かめた。これで実際のランタイムを手元で再現できる。そのうえで、実際の HyperPod on EKS の作成がこのステップを越えて最後まで進むことを確かめた。arm64 は試しておらず、そのことははっきり言う。
:::

:::details Q4. You found a duplicate installer in the service team's repo. What did you propose, and why?
**質問 (日本語)**

サービスチームのリポジトリに重複したインストーラを見つけた。何をなぜ提案したか。

**Answer (English)**

The same Helm installer existed in `aws/sagemaker-hyperpod-cluster-setup`. It sets the same `LD_LIBRARY_PATH`, but its Layer doesn't bundle `libcrypto` and `libssl`, so it worked while ours broke. The two had diverged in both Layer and function code. I proposed in an RFC to consolidate on the service team's repo, which the official documentation points to, and retire this repo's `cfn-templates/`. The proposal needs maintainer agreement, and it wouldn't force existing stacks to migrate. Updating them with the service team's template could replace the EKS or HyperPod cluster. We'd also need to decide whether to keep the S3 templates and Lambda zips those stacks still reference.

**回答 (日本語)**

同じ役割の Helm のインストーラが `aws/sagemaker-hyperpod-cluster-setup` にもあった。同じ `LD_LIBRARY_PATH` を設定しているが、Layer が `libcrypto` と `libssl` を同梱していないので、そちらは動き、こちらだけが壊れた。2 つは Layer と関数の中身がすでに分かれていた。RFC では、公式のドキュメントが案内しているサービスチームのリポジトリに寄せ、このリポジトリの `cfn-templates/` をやめる方向を提案した。やめるにはメンテナの合意が要り、既存のスタックの移行は強制しない。既存のスタックをサービスチームのテンプレートで更新すると EKS や HyperPod のクラスタが作り直されるおそれがあり、既存のスタックが参照する S3 のテンプレートや Lambda の zip を残すかも決める必要があるからである。
:::

:::details Q5. Why wait before switching to the service team's templates?
**質問 (日本語)**

なぜサービスチームのテンプレートへの切り替えを待つべきだとしたのか。

**Answer (English)**

Because the target had a worse default. This repo's templates enable continuous node provisioning, `NodeProvisioningMode: Continuous`, by default. The service team's default was empty, the older behavior where the whole cluster fails if one instance group can't be filled. The PR that changes that default was still open when I commented, so I proposed waiting until it, or an equivalent change, lands. A one-to-one comparison of all CloudFormation resources and a migration test of existing stacks haven't been done yet.

**回答 (日本語)**

寄せる先の既定が劣っていたからである。このリポジトリのテンプレートは、継続的なノードの準備 (`NodeProvisioningMode: Continuous`) が既定で有効である。サービスチームの既定は空で、1 つのインスタンスグループが埋まらないと全体が失敗する従来の方式だった。既定を変える PR はコメントを書いた時点で開いたままだったので、それか同等の変更が入るまで待つことを提案した。すべての CloudFormation のリソースの 1 対 1 の照合と、既存のスタックの移行の検証は、まだしていない。
:::

:::details Q6. The link migration looks trivial. Was there anything to learn?
**質問 (日本語)**

リンクの移行は簡単に見える。学ぶことはあったか。

**Answer (English)**

Two things. I changed five links in three files but deliberately left the workshop ID used in S3 object paths, because that's an object location, not a documentation link. And the reviewer found the same old URL in the runtime output of two scripts under `automate-smhp-eks/`, which the merged diff also changed. When migrating links, search strings that scripts print at runtime, not just the docs. I tested with `grep` for old and new URLs and checked rendering; no infrastructure was deployed for this one.

**回答 (日本語)**

2 つある。3 つのファイルの 5 か所のリンクを移したが、S3 のオブジェクトの場所に使われているワークショップの ID は、ドキュメントのリンクではないので、わざと変えなかった。また、レビューで同じ古い URL が `automate-smhp-eks/` の 2 つのスクリプトの実行時の表示に残っていると指摘され、マージした差分ではそれも変わっている。リンクを移すときは、ドキュメントだけでなく、スクリプトが実行時に出す文字列も探す。テストは `grep` で古い URL と新しい URL を確かめ、表示を確かめた。インフラのデプロイはしていない。
:::

:::details Q7. The Layer still ships an older OpenSSL. Who should fix that?
**質問 (日本語)**

Layer には古い OpenSSL が残ったままである。それは誰が直すべきか。

**Answer (English)**

I deliberately didn't touch the Layer or the function zip in the fix PR, to keep the change to one reviewable line. The longer-term answer is the RFC: consolidate on the service team's installer, whose Layer doesn't bundle `libcrypto` and `libssl`, and retire this repo's `cfn-templates/` once the maintainers agree and the provisioning default catches up. So I'd frame ownership as one installer with one owner, rather than patching two copies.

**回答 (日本語)**

修正の PR では、変更をレビューしやすい 1 行に留めるため、Layer と関数の zip にはわざと触れなかった。長い目での答えは RFC で、`libcrypto` と `libssl` を同梱しないサービスチームのインストーラに寄せ、メンテナの合意とノードの準備の既定が追いついた時点で、このリポジトリの `cfn-templates/` をやめる。2 つの写しをそれぞれ直すのではなく、1 つのインストーラに 1 つの持ち主を置く、という整理である。
:::

## まとめ

SSL の失敗は `LD_LIBRARY_PATH` の 1 行の削除で直し、インストーラを 2 か所で保守する重複の解消は、別の RFC として提案した。直すことと、どこに寄せるかを決めることを分けたので、利用者は動くテンプレートを使いながら、移行の条件を確かめられる。
