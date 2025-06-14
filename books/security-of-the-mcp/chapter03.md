---
title: "§03 Tool use と MCP は結局何が違う？仕組みを比較"
free: true
---

___MCP に関する理解編:___  _MCP の脆弱性と対策を理解するために必要な前提知識の解説_

---

AI モデルが利用する Tool use と MCP の比較を行いましょう。

![030101](/images/books/security-of-the-mcp/fig_c03_s01_01.png)

まず Tool use についての要点をおさらいします。**1/ AI モデルはユーザーからのプロンプトを受け取ります**、**2/ AI モデルは必要なツールと入力を決定します**、その後、クライアント側でツール呼び出しを実行し、ツール実行結果を AI モデルに渡して最終的なレスポンスを生成します。

## MCP の仕組み

MCP の主要な目的は、Tool use ではプロバイダの実装依存となっている、1/ ツール定義、2/ ツール呼び出し処理、を標準化することにあります。ここで重要なのは、**AI モデルの能力は Tool use と本質的には変わらない**と言う点です。

![030102](/images/books/security-of-the-mcp/fig_c03_s01_02.png)

**0/** MCP Client は MCP Server から提供されるツール定義によって AI モデルへのツールの公開方法を標準化します。これによって AI モデルは利用可能なツールを容易に検出し、スキーマを理解してツールを使用できるようになります。**1/** MCP Client はユーザープロンプトを受け取ると AI モデルにプロンプトを渡します。**2/** Tool use 同様に AI モデルはプロンプトとツール定義に基づいて必要なツールと入力を決定します。その後、MCP Client で直接ツールを呼び出すのではなく、**5/** MCP Server に向けて _ツール使用リクエスト_、を実行します。**6/** MCP Client はツール実行結果を受け取り、**/8** AI モデルにツール実行結果とユーザープロンプトを渡し、**9/** 最終的なレスポンスを生成します。

もう少しわかりやすく MCP の Tool use との共通点を説明します。MCP Client に隠蔽されていますが、AI モデルが受け取る入力は、_ユーザープロンプト_ と _ツール定義_ です。そして、出力は _ツール使用リクエスト_ であり、使用すべきツールの名前とそのツールへの引数となる情報です。この部分は Tool use と同じです。

MCP Server は AI モデルとは切り離され、ツールの実際の呼び出し、ツール定義、に集中します。そして、MCP Client は AI モデルとの入出力、MCP Server とのツールに関するやりとり、ユーザーとの入出力、を担います。

## まとめ

本 Chapter では、Tool use と比較する形で MCP の仕組みについて解説しました。MCP は Client と Server に責務を分割したことで統一的なインタフェースでツールを利用できるようにしたことに価値があります。一方でコンポーネントが増え、コンポーネント間の通信の脆弱性や信頼関係の構築方法など別の考慮事項が発生します。以降の Chapter では MCP コンポーネントとそれぞれの持つ機能について解説します。

## SaaS コラム

本書では **SaaS コラム** で本文内容を補足する SaaS に関する解説を行います。

「SaaS is Dead」が話題です。SaaS は技術用語ではなくビジネスモデルを表していると捉えると、SaaS というビジネスモデルが死ぬということなのでしょうか。以下は筆者の独断と偏見による MCP と SaaS に関するお気持ち解説です。

SaaS の多くは、「作るのが手間な機能群」を、低い初期コストで迅速に導入でき、データ統合や分析機能をつけています。Vertical SaaS の場合はドメイン特化でこのような「作るのが手間な機能群」を提供してくれます。Horizontal SaaS の場合は OCR や音声認識、Security、ログ収集、などのドメインに依らず共通で利用できるような「作るのが手間な機能群」を提供してくれます。一方で、自社で実現するには R&D レベルの研究の蓄積が必要な機能、も存在します。このような Deep Tech な機能を提供する SaaS は既にその機能のための膨大なユーザーデータを用いた作り込みをしていることがほとんどであり、先行者優位性が強く働くため「SaaS is Dead」 することは当面ないでしょう。

MCP はツール提供の標準化に強い価値があります。これは SaaS の概念とよく似ており、「作るのが手間な機能群」を企業が Tool use のように内製開発するのではなく、SaaS として提供することでソフトウェア提供のビジネスモデルを標準化しています。SaaS ではなく MCP 経由でのサービス提供が新しいビジネスモデルになることは今後考えられます。これまでは人間が UI や API を通じて手動で SaaS を利用していましたが、もしかすると今後は AI Agent が MCP を通じて自動的に SaaS を複数組み合わせて使うようになるかもしれません。

どこまで AI モデルが進歩するのかに依存しますが、非常にプリミティブな機能のみを MCP として作成しておくことで、AI Agent がそれらを組み合わせることで SaaS の価値である、「作るのが手間な機能群」を全て再現することができるようになると、もしかすると 「SaaS is Dead」 になる日が来るのかもしれませんね。その時には SaaS 以外のあらゆるビジネスモデルや業界も何らかの影響を受けると思います。

[Software as a Service (SaaS) とは何ですか?](https://aws.amazon.com/jp/what-is/saas/)
