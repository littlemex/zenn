---
title: "§09 Streamable HTTP にプチ Dive Deep!"
free: true
---

___MCP に関する発展理解編:___  _MCP の脆弱性と対策を理解するために必要な開発者向け知識の解説_

---

本章の説明は、2025-06-18 の[仕様](https://modelcontextprotocol.io/specification/2025-06-18)に基づきます。

MCP Specification: **Base Protocol（今ここ）**、Authorization、Client Features、Server Features、Security Best Practices

本 Chapter では Base Protocol の[トランスポート](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)について解説します。トランスポートについては Chapter04 で解説しましたが、今回はより詳細にトランスポートについて解説します。

JSON-RPC 2.0 はトランスポート非依存ですが、MCP の場合は [STDIO](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#stdio) と [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http) という Client ↔︎ Server 間通信のための二つのトランスポートメカニズムを仕様として定義しています。これらのトランスポートがメッセージの送受信でどのように接続を取り扱うべきであるかについて仕様で定義されています。

## Streamable HTTP の要素技術

Streamable HTTP トランスポートは、HTTP と [Server-Sent Events (SSE)](https://en.wikipedia.org/wiki/Server-sent_events) を組み合わせて双方向通信を実現します。STDIO トランスポートがサブプロセスを起動してイベントハンドラを介して双方向で通信するのに対し、Streamable HTTP はネットワーク経由で通信を行います。SSE は Server から Client へのリアルタイム通信を HTTP で実現するための技術です。Client から Server への HTTP リクエストと Server から Client への SSE を利用して MCP で必要な双方向通信を実現します。

**SSE について**

Typescript を用いた SSE サンプルコードを Chapter の最後に示しておきます。この実装では、`server.ts` で Server を起動します。この Server は `/sse` で SSE エンドポイントを持っています。`client.ts` で Client を起動します。Client は Server の SSE エンドポイントに `Accept: text/event-stream` ヘッダーを含めて HTTP GET リクエストを送信します。Server はこの接続を開いたまま保持し、ステータスコード `200` で応答します。以後サーバーは同じ接続を使ってイベントが発生するたびにデータを Client へ送信します。実装例では イベントとして 1 秒おきにカウンタの値を Client に送信します。


```bash:Server 実行
$ ts-node server.ts 
サーバー起動: http://localhost:3001
SSEエンドポイント: http://localhost:3001/sse
クライアント接続: 新しいSSEセッション開始
SSEイベント送信: 接続確立メッセージ
SSEイベント送信: カウント=1
SSEイベント送信: カウント=2
SSEイベント送信: カウント=3
^C
```

```bash:Client 実行
$ ts-node client.ts 
SSE接続確立
受信データ: { message: '接続確立' }
受信データ: { count: 1, timestamp: '2025-06-10T01:48:46.018Z' }
受信データ: { count: 2, timestamp: '2025-06-10T01:48:47.018Z' }
受信データ: { count: 3, timestamp: '2025-06-10T01:48:48.019Z' }
ストリーム処理エラー: terminated
SSE接続終了
```

```mermaid
sequenceDiagram
    participant Client as クライアント
    participant Server as サーバー
    
    Client->>Server: HTTP GET /mcp<br>Accept: text/event-stream
    activate Server
    Server-->>Client: 200 OK<br>Content-Type: text/event-stream
    
    loop イベント発生時
        Server->>Client: id: event-123<br>data: {...}
    end
    
    Note over Client,Server: 接続は長時間維持される
    
    Client->>Server: 接続切断
    deactivate Server
```

SSE はこのように HTTP 上に構築されたシンプルな仕組みなので、適切なヘッダーとデータ形式の仕様を守れば、fetch などの基本的な API を使って実装することができます。SSE は WHATWG によって[標準化](https://html.spec.whatwg.org/multipage/server-sent-events.html#server-sent-events)されています。

**Streamable HTTP について**

以下は、HTTP と SSE を使った Streamable HTTP の通信の流れを表しています。詳細については次回解説しますが、単一のエンドポイント `/mcp` があり、`POST` で Client から Server への JSON-RPC 2.0 メッセージ送信 (矢印 1-4)、`GET` で Server から Client への SSE (矢印 A-E)、を実現しています。

```mermaid
graph TB
    subgraph "MCP Client"
        Client[クライアントコード]
        FetchAPI[fetch API]
        Parser[eventsource-parser]
        StreamReader[ReadableStream Reader]
    end
    
    subgraph "HTTP/SSE 通信"
        HTTPPost[HTTP POST /mcp<br>Content-Type: application/json]
        SSEStream[HTTP GET /mcp<br>Accept: text/event-stream]
    end
    
    subgraph "MCP Server (独立プロセス)"
        Server[サーバーコード]
        RPCHandler[JSON-RPC ハンドラー]
        EventEmitter[イベントエミッター]
    end
    
    %% クライアント → サーバー (JSON-RPC over HTTP POST)
    Client -->|"1.JSON-RPC メッセージ生成"| FetchAPI
    FetchAPI -->|"2.HTTP POST {jsonrpc: ...}"| HTTPPost 
    HTTPPost -->|"3.リクエスト処理"| Server
    Server -->|"4.メソッド呼び出し"| RPCHandler
    
    %% サーバー → クライアント (SSE over HTTP GET)
    EventEmitter -->|"A.イベント発生"| Server
    Server -->|"B.SSE メッセージ作成"| SSEStream
    SSEStream -->|"C.チャンク受信"| StreamReader
    StreamReader -->|"D.テキストデコード"| Parser
    Parser -->|"E.イベント解析"| Client

    %% セッション管理
    HTTPPost -.->|"mcp-session-id"| Client
    SSEStream -.->|"last-event-id"| Server
```

**機能比較表**

STDIO と Streamable HTTP の簡単な機能比較表を作成しました。ここまで読まれた読者の方は MCP がトランスポート非依存であるということの意味がよくわかったと思います。トランスポートは STDIO HTTP、WebSocket などなんらかの方法で Server と Client の間で MCP 仕様を満たす形で双方向通信ができれば良いです。

| 特徴 | STDIO | Streamable HTTP |
|------|---------------------|---------------------------|
| 通信方式 | 標準入出力（stdin/stdout） | HTTP + SSE |
| プロセス | サブプロセスとして起動 | 独立したプロセス |
| 接続範囲 | ローカルマシン内のみ | ネットワーク経由（ローカル/リモート） |
| 複数接続 | 1 対 1 の接続のみ | 複数 Client 接続をサポート |
| 再接続機能 | なし | SSE の再開機能あり |
| セッション管理 | プロセスの生存期間 | セッション管理可能 |


## セキュリティ警告

Streamable HTTP の仕様には **DNS Rebinding という攻撃手法の対策について明示的に対応を求める**セクションがあります。まず端的にこの攻撃が成功すると何が起こるのかを説明しましょう。DNS Rebinding が成功すると悪意のある Web サイトの Javascript コードがローカル・リモート問わず MCP Server と通信できるようになります。つまり、**攻撃者の悪意のある Javascript コードが MCP Server のツールを利用できてしまいます**。とても危険なのでセキュリティ警告としてセクションを設けて仕様を定義するのは当然ですね。

DNS Rebinding は、Web ブラウザの Same-Origin Policy を回避する攻撃手法です。Same-Origin Policy は Web ブラウザに組み込まれたセキュリティ機能であり、ある Web ページ上のコードが異なるオリジンのリソースにアクセスすることを制限してくれます。

```mermaid
sequenceDiagram
    participant ブラウザ
    participant DNS
    participant 攻撃者 Server
    participant MCP Server
    
    Note over ブラウザ,MCP Server: 通常、ブラウザは同一オリジンポリシーにより<br>evil.example.com から localhost への直接アクセスは禁止
    
    ブラウザ->>DNS: evil.example.com の解決を要求
    DNS-->>ブラウザ: 攻撃者の Server IP (1.2.3.4)
    ブラウザ->>攻撃者 Server: evil.example.com にアクセス
    攻撃者 Server-->>ブラウザ: 悪意ある JavaScript を返す
    
    Note over 攻撃者 Server: DNS レコードを変更<br>evil.example.com → 127.0.0.1
    
    ブラウザ->>DNS: evil.example.com の再解決
    DNS-->>ブラウザ: 127.0.0.1 (MCP Server の IP)
    
    Note over ブラウザ: ブラウザは「evil.example.com」という<br>同じオリジンへのアクセスと認識
    
    ブラウザ->>MCP Server: evil.example.com として 127.0.0.1 にアクセス
    MCP Server-->>ブラウザ: レスポンス
```

攻撃者は最初に悪意ある Web サイトに訪問者を誘導し、その後 DNS レコードを動的に変更することで、ブラウザに同じドメイン名が今度は localhost の MCP Server を指すように仕向けます。これにより、悪意ある Web サイトのコードが、本来アクセスできないはずの MCP Server と通信できるようになります。

Streamable HTTP は HTTP と SSE を仕様してネットワーク経由で通信を行うため Web ブラウザからアクセスすることが可能であり、更には SSE で長時間接続を維持するため DNS Rebinding のリスクが非常に高いです。そのため仕様では 3 つのセキュリティ対策が求められています。

**1. Origin ヘッダー検証**

MCP Server は**全ての接続で Origin ヘッダーを検証しなければなりません**。このヘッダーはブラウザが自動的に設定し、リクエストの発信元を示します。Server は許可されたオリジンからのリクエストのみを受け付けるようにします。これにより、DNS Rebinding で悪意あるサイトからのリクエストが来た場合、その Origin が許可リストにないため拒否されます。

**2. localhost のみへのバインド**

ローカル実行時、MCP Server は `0.0.0.0` ではなく、localhost インターフェースのみにバインドすべきです。これにより、同じマシン上のプロセスからのみアクセス可能になり、ネットワーク経由の外部アクセスを防ぐことができます。

**3. 認証**

全ての接続に適切な認証メカニズムを実装すべきです。これによって未認証のアクセスを防ぎます。

## まとめ

本 Chapter では、MCP Base Protocol で定義されるトランスポートの一つである Streamable HTTP について解説しました。実装の概念部分が理解できているかいないかでセキュリティ対策に関する解像度が大きく変わってくると思いますのでしっかりとコードを理解しましょう。次 Chapter では typescript-sdk の Streamable HTTP 実装について解説します。

## サンプルコード

[こちら](https://github.com/littlemex/samples/blob/main/mcp_security_book/chapter09)に実装サンプルを配置しました。

https://github.com/littlemex/samples/blob/main/mcp_security_book/chapter09/README.md
