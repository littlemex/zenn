---
title: "§99 リファレンス"
free: true
---

|ID|情報元|タイトル|
|:---|:---|:---|
|001|AWS|[Open Protocols for Agent Interoperability Part 2: Authentication on MCP](https://aws.amazon.com/jp/blogs/opensource/open-protocols-for-agent-interoperability-part-2-authentication-on-mcp/)|
|002|Amazon Science|[Amazon Nova and our commitment to responsible AI](https://www.amazon.science/blog/amazon-nova-and-our-commitment-to-responsible-ai)|
|003|AWS, Intuit|[Enterprise-Grade Security for the Model Context Protocol (MCP): Frameworks and Mitigation Strategies](https://arxiv.org/abs/2504.08623)|
||||



https://builder.aws.com/content/2s44xHTSbQgo2Ws2bJr6hZsECGr/building-a-serverless-remote-mcp-server-on-aws-part-1
https://builder.aws.com/content/2zfozhdXXheUaePfjVO1adHSKqg/unlock-the-power-of-aws-application-signals-with-the-cloudwatch-application-signals-mcp-server
https://builder.aws.com/content/2z4U3mMMIWtMkgRHPnquFhrT7Xn/ai-agents-design-patterns-auto-generate-mcp-server-in-minutes-using-strands-agents-sdk
https://builder.aws.com/content/2zQBE367D31d5OxS7SdLUYgrVSh/why-mcp-is-not-production-ready-yet
https://builder.aws.com/content/2zJTdRGobiH2RYtjHAW8hRqD0h6/agentic-genai-app-using-bedrock-mcp-servers-on-eks
https://builder.aws.com/content/2yot28gmJjlfuQZZpXBOBNAn3sf/building-web-applications-with-cloudformation-mcp-server-and-amazon-q-cli
https://builder.aws.com/content/2y0Hg7Isd8yvVImrf6U5u60ELPz/aws-strands-agents-building-and-connecting-your-first-mcp-server
https://builder.aws.com/content/2hXQCS6A6M0CMgixB3xkbwQlvPz/from-protocol-to-product-building-mcp-clients-with-amazon-bedrock-converse-api
https://builder.aws.com/content/2wrhMNnWgp3IvEEGTrftgk9o9hs/hands-on-mcp-extending-llms-with-real-time-data












## [2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/changelog)

### 主要な変更
- JSON-RPCバッチ処理のサポートを削除（PR #416）
- 構造化ツール出力のサポートを追加（PR #371）
- MCPサーバーをOAuth リソースサーバーとして分類し、対応する認可サーバーを発見するためのプロテクテッドリソースメタデータを追加（PR #338）
- 悪意のあるサーバーがアクセストークンを取得するのを防ぐため、RFC 8707に記述されているリソースインジケーターの実装をMCPクライアントに要求（PR #734）
- 認可仕様と新しいセキュリティベストプラクティスページでセキュリティに関する考慮事項とベストプラクティスを明確化
- エリシテーション（誘出）のサポートを追加し、インタラクション中にサーバーがユーザーから追加情報を要求できるようにする（PR #382）
- ツール呼び出し結果におけるリソースリンクのサポートを追加（PR #603）
- HTTPを使用する場合、後続のリクエストでMCP-Protocol-Versionヘッダーを介して交渉されたプロトコルバージョンの指定を要求（PR #548）
- ライフサイクル操作において「SHOULD（すべき）」を「MUST（必須）」に変更

### その他のスキーマ変更
- 追加のインターフェースタイプに_metaフィールドを追加（PR #710）し、適切な使用法を指定
- CompletionRequestにcontextフィールドを追加し、以前に解決された変数を含む補完リクエストを可能に（PR #598）
- 人間が読みやすい表示名のためのtitleフィールドを追加し、nameをプログラム的な識別子として使用できるようにする（PR #663）

### 完全な変更履歴
- 前回のプロトコルリビジョン以降に行われたすべての変更の完全なリストについては、GitHubを参照してください。
