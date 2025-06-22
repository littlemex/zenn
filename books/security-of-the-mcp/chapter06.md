---
title: "§06 MCP ライフサイクルは通信フェーズ定義！"
free: true
---

___MCP に関する発展理解編:___  _MCP の脆弱性と対策を理解するために必要な開発者向け知識の解説_

---

本章の説明は、2025-03-26 の[仕様](https://modelcontextprotocol.io/specification/2025-03-26)に基づきます。

MCP Specification: **Base Protocol（今ここ）**、Authorization、Client Features、Server Features、Security Best Practices

本 Chapter では Base Protocol の[ライフサイクル](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle)について解説します。ライフサイクルと言われてもあまりピンとこないかもしれません。ライフサイクルを解説する前に Protocol についておさらいします。

## Protocol とは？

そもそも Protocol は「規約」、「手順」などと訳されます。つまり、[Protocol](https://ja.wikipedia.org/wiki/%E3%83%97%E3%83%AD%E3%83%88%E3%82%B3%E3%83%AB) は複数の対象が何らかの事柄を確実に実行するための手順を定めたものと言えます。情報工学の分野では「通信規約」などと訳され、複数の対象間の通信に関する手順を定めたものです。

Protocol には様々な種類があります。例えば、認証 Protocol、複数の対象間で分散合意をするための Protocol、ノード間接続のためのネットワーク Protocol、など様々な階層で Protocol が存在します。例えば、ネットワーク Protocol であれば、ハードウェア起動時（Power-on/PON）の初期化シーケンスと呼ばれる手順があります。これは、送信側のルーターと受信側のルーターの間でハードウェアレベルの送達を Ready 状態にするための手順です。それぞれのルーターが起動後に自身の状態、相手の状態、伝送路のエラーレートの状態、などを把握して適切な手順を踏んで Ready 状態まで進めます。ネットワーク Protocol の中には初期化シーケンス以外にもエラー時に、通知、エラー対処方法の決定、故障レベル判断、などを行う RAS(Reliability, Availability and Serviceability) 機構シーケンスもあります。

まとめると、**Protocol は必要な手順を全て満たすデータフォーマットを用意し、それをどう使うのかについての手順を説明した規約を定めたもの**、です。Protocol を使いこなすためのアルゴリズムについては実装に委ねられています。実際にネットワークプロトコルでも障害検出や通知、初期化時のルーティング判断、再送制御のバッファ管理など Protocol では定められていない周辺機能が多くあり、これらは機能仕様として別途定められます。

## MCP ライフサイクルとは何なのか？

**MCP のライフサイクルは MCP という Protocol の中で接続確立から終了までの一連のフェーズを手順として定めた**ものです。[MCP specification](https://modelcontextprotocol.io/specification/2025-03-26) のすべての説明は、手順ごとにデータフォーマットをどう定め、それらをどう使いこなすのか、ということを説明していることが理解できると、情報を整理しやすくなるのではないでしょうか。

> 個人的な感覚ですが、MCP の仕様は `SHOULD` が多く、HTTP などのトランスポート層や実装に委ねる記述が多いです。そのため、MCP のメッセージフォーマットで定義がある機能なのか、トランスポート層や実装側に委ねられている機能なのか、を明確に区別しながら仕様を読むことをお勧めします。

> _引用: [Lifecycle ](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle)_

![060101](/images/books/security-of-the-mcp/fig_c06_s01_01.png)

ライフサイクルの Phase には、1/ Initialization、2/ Operation、3/ Shutdown、があります。

Initialize に関してはリクエストオブジェクトの _method_ が `initialize` になっていることで判断できます。Operation については _method_ に `initialize` 以外が入っている場合の全てを示します。Shutdown については特定のメッセージは定義されておらず、Client または Server が一方的にプロトコル接続を基盤となるトランスポート層のメカニズムでクリーンに終了します。

Initialization Phase では、1/ Capability Negotiation、2/ Version Negotiation、を Server と Client の間で合意します。 Capability Negotiation では Client、Server 双方がセッション中に利用可能な機能について合意します。Version Negotiation では MCP 自体の Protocol Version を合意します。

> Capabilities

| カテゴリ   | 機能            | 説明                                                                 |
|------------|-----------------|----------------------------------------------------------------------|
| Client | roots          | ファイルシステムルートを提供する能力                                |
| Client | sampling       | LLM サンプリングリクエストのサポート                                  |
| Client | experimental   | 非標準の実験的機能のサポートを記述                                   |
| Server | prompts        | プロンプトテンプレートを提供                                         |
| Server | resources      | 読み取り可能なリソースを提供                                         |
| Server | tools          | 呼び出し可能なツールを公開                                           |
| Server | logging        | 構造化されたログメッセージを出力                                     |
| Server | experimental   | 非標準の実験的機能のサポートを記述                                   |

### Initialization Phase のオブジェクト例

_Client: リクエストオブジェクト_

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {
      "roots": {
        "listChanged": true # リスト変更通知のサポート（プロンプト、リソース、ツール向け）
      },
      "sampling": {}
    },
    "clientInfo": {
      "name": "ExampleClient",
      "version": "1.0.0"
    }
  }
}
```

_Server: レスポンスオブジェクト_

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": {
      "logging": {},
      "prompts": {
        "listChanged": true
      },
      "resources": {
        "subscribe": true, # 個別アイテムの変更購読サポート（リソース専用）
        "listChanged": true
      },
      "tools": {
        "listChanged": true
      }
    },
    "serverInfo": {
      "name": "ExampleServer",
      "version": "1.0.0"
    },
    "instructions": "Optional instructions for the client"
  }
}
```

## まとめ

本 Chapter では、Protocol とは何なのか、MCP ライフサイクル、について解説しました。今後の Chapter でも Protocol 仕様では、データフォーマット（オブジェクトフォーマット）とその使い方について説明されます。そのため、そもそも Protocol 仕様をどう見るべきなのか、という視点で解説してみました。ここまで理解できた方は以降の知識編の解説は読まずに SDK 実装や公式仕様を読み込んだ方が理解が早いかもしれません。