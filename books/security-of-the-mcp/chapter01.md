---
title: "§01 MCP が急速に流行しているのはなぜなのか？"
free: true
---

___MCP に関する理解編:___  _MCP の脆弱性と対策を理解するために必要な前提知識の解説_

---

[Anthropic](https://www.anthropic.com/) が発表した [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) は、AI モデルが様々なツールにアクセスするための統一的なインタフェースです。AI モデルとツールを接続する **USB-C 規格**のようである、と例えられます。

![](/images/books/security-of-the-mcp/fig_c01_s01_01.png)

例えば、今日の東京の天気を知りたい時に AI モデルは今日の東京の天気の情報は持っていません。そんな時に天気予報 MCP があれば AI モデルは天気予報 MCP を活用して今日の東京の天気予報の情報を収集してわかりやすくまとめて教えてくれるでしょう。

## ツール使用の変遷

_「あれ、MCP が出てくる前から天気予報 API をツールとして AI モデルが利用することはできたのでは？」_ と思われた方がいるかもしれません。**それは完全に正しいです！**

MCP にはおそらく技術的な革新性はありません。Anthropic は 2024 年 5 月 31 日に[「Claude can now use tools」](https://www.anthropic.com/news/tool-use-ga) というブログを発表し、[Amazon Bedrock](https://aws.amazon.com/jp/bedrock/) でも Claude を通じて外部ツールを利用できるようになりました。さらに遡ると [AI21 Labs](https://www.ai21.com/) は 2022 年に出した[「Bibliography management: BibTeX」](https://arxiv.org/abs/2205.00445) の中で、Modular Reasoning Knowledge Language (MRKL) system という外部 API 呼び出しに対応する機能を自社の Jurassic-X に実装した、と記載されています。

**ではなぜ急速に MCP のエコシステムが発展しているのでしょうか？**

ある企業で M 個の AI アプリケーションを実装する必要があり、それぞれのアプリケーションで N 個のツールを利用する場合を考えてみましょう。この場合、`M×N` のカスタム統合を構築・維持する必要があります。MCP という統一的なプロトコルで通信するインタフェースが登場したことで、この `M×N` の複雑性を **`M+N` に軽減** することができます。多くの企業は自社サービスを MCP 経由で公開することで新規の販売チャネルを構築できるかもしれません。そして利用者は簡単な導入ステップで MCP を通じて外部ツールを AI アプリケーションに統合することができます。

> _引用: [Unlocking the power of Model Context Protocol (MCP) on AWS](https://aws.amazon.com/jp/blogs/machine-learning/unlocking-the-power-of-model-context-protocol-mcp-on-aws/)_

![](/images/books/security-of-the-mcp/fig_c01_s01_02.png)

## 利便性と脆弱性

MCP は上述した通り非常に利便性が高く、ビジネス的な側面からの販売チャネルとしての将来性も無視できません。MCP の Marketplace が販売チャネルとして更に発展することでこの流れは加速するかもしれません。一方で **MCP には多くのセキュリティ脆弱性が報告されており**、MCP の提供者、利用者、共にセキュリティの適切な対応を行う必要があります。MCP はセキュリティに関する仕様が完全には確立されておらず、今後包括的な仕様とセキュリティベストプラクティスが確立されるでしょう。

`M×N` 複雑性を考える際に、`M=1` のケースでの複雑性は `N` です。このケースであえて脆弱性を受容して MCP を利用することが適切なのか、ということについてはビジネス要求を踏まえてよく考える必要があるでしょう。

## まとめ

本 Chapter では MCP、そしてツール使用の変遷について簡単に触れ、MCP がなぜ急速に発展しているのかの一例を説明しました。急速な発展にセキュリティ対応が追いついていない過渡期であり、特に企業においては**慎重な利用が求められる**ことを認識いただければ幸いです。

X の `@MCP_Community` の[ポスト](https://x.com/MCP_Community/status/1934385740298985980)では MCP 公開以来半年程度で 2 万以上(MCP Server 公開数ということでしょう) となっており急速な発展を定量的にも確認できますね。

![](/images/books/security-of-the-mcp/fig_c01_s01_03.png)