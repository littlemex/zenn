---
title: "§04 MCP の全体概要"
free: true
---

___MCP に関する理解編:___  _MCP の脆弱性と対策を理解するために必要な前提知識の解説_

---

本章の説明は、2025-06-18 の[仕様](https://modelcontextprotocol.io/specification/2025-06-18)に基づきます。

MCP を構成するコンポーネントは、MCP Client、MCP Server、MCP Host があります。本 Chapter では MCP の全体像を確認しましょう。

> MCP コンポーネント図

![040101](/images/books/security-of-the-mcp/fig_c04_s01_01.png)

## コンポーネント

**MCP Host**

MCP Host は、統合開発環境、AI アプリケーションなどです。Host はユーザーインターフェースを提供し、AI モデルとの対話を管理する役割を担います。ユーザー入力を受け取り、適切な形式で AI モデルに転送して結果を表示します。そして、複数の Client を作成および管理し、それらを通じて様々な MCP Server と通信することができます。さらに、Host は Client 接続の権限、セキュリティポリシーと同意要件を実施する重要な役割を担っています。また、ユーザー認証の決定を処理します。

**MCP Client**

MCP Client は、MCP Host アプリケーションによって生成され、MCP Server ごとに隔離された接続を維持します。__各 Client は特定の Server と1:1 の関係を持ち、Server ごとに 1 つのセッションを確立します。MCP Server との通信は JSON-RPC 2.0 ベースのメッセージを交換します。Client は接続時に Server とプロトコルのネゴシエーションと機能交換を処理し、双方向にプロトコルメッセージをルーティングします。

Client は __Roots、Sampling、Elicitation__ の 3 つの機能を持っています。これらの機能の利用可否は Client 側で設定し、Server との接続時に利用可否の情報を交換します。Server は利用可能な機能のみを利用することができます。

**Roots** はセキュリティ機能であり、設定に基づいて Server のファイルシステム操作範囲が明示的に制限されます。この機能によって認証情報のようなセンシティブ情報へのアクセスを防ぐことができます。**Sampling** は Server が間接的に LLM にアクセスするための機能です。なぜこの機能が必要なのでしょうか？ Server はツール提供が主要な責務であり、仕様上は LLM との直接の接続はありません。この機能により Server は Client を介して間接的に LLM を活用できます。**Elicitation** は Server から間接的にユーザーに情報入力を依頼する機能です。例えば、Server が Github のユーザーネームをユーザーに聞く必要がある場合に、Elicitation を利用してユーザーネームを得ることができます。

**MCP Server**

MCP Server は **Resources、Prompts、Tools** の 3 つの機能を Client に提供します。**Resources** は AI モデルが使用するためのプロンプトテンプレートを提供し、**Prompts** はユーザーのためのテンプレートメッセージを提供します。そして、**Tools** は AI モデルにツール使用に関する機能を提供します。

MCP Server がローカルマシンにあるケースしか見たことがない、使ったことがない、という方もいるかと思います。今後詳細に解説する Streamable HTTP はリモートに MCP Server が存在することもあり得ます。

**MCP 機能まとめ**

| 機能 | 役割 | Client 責務 | Server 責務 |
|------|------|------------|------------|
| Roots | Server のファイルシステム操作範囲を制限 | アクセス可能な範囲を定義・管理する | 定義された境界内でのみ操作を行う |
| Sampling | Server が LLM を利用 | LLM へのアクセスを管理 | 適切なプロンプトと引数を提供し、結果を処理 |
| Elicitation | ユーザーから構造化データを収集 | ユーザーに情報入力依頼、入力の検証と Server への送信 | スキーマを定義し、必要なデータ構造を明示する |
| Prompts | プロンプトテンプレートを提供 | テンプレートを取得し、ユーザーに提示して使用する | テンプレートを定義し、引数のスキーマを提供する |
| Resources | ファイルなどのリソースを提供 | リソースを取得し、適切に表示・利用する | リソースを管理し、URI を通じてアクセス可能にする |
| Tools | 外部システムとの対話機能を提供 | ツールを呼び出し、結果を処理・表示する | ツールの実装と入出力スキーマの定義を行う |

Client と Server は初期接続時に Capability Negotiation を利用してお互いに明示的にサポートする機能を宣言します。この初期宣言に基づいてセッション中に利用可能なプロトコル機能が決定されます。セッションの流れとしては、**1/** Host が Client を初期化し、**2/** Client が機能を指定して Server とのセッションを初期化、**3/** Server はサポートする機能で応答、してネゴシエーションが完了します。

## Base Protocol

MCP は [JSON-RPC 2.0](https://www.jsonrpc.org/specification) をベースプロトコルとして採用し、その上にツール利用のための拡張を行っています。詳細については JSON-RPC 2.0 仕様をご確認ください。プロトコルレベルでの JSON-RPC 2.0 との主な差分として、MCP はリクエスト ID が必須であり null が許容されない点です。メッセージ構造についてもリクエスト ID 重複が禁止されており、JSON-RPC 2.0 より多少制約が厳格になっています。

JSON-RPC 2.0 に対して MCP 特有の機能追加を行っていますが、この追加機能は主に JSON-RPC 2.0 で定義されるリクエストオブジェクトの _params_ や レスポンスオブジェクトの _result_ などを用いて実現されています。簡単にいうと、JSON-RPC 2.0 に用意された任意の値を取り扱える領域を使って MCP の追加仕様を定めています。

JSON-RPC 2.0 は HTTP、プロセス、などの裏側のトランスポート層（OSI 参照モデル的にはアプリケーション層も包含される）に依存しないプロトコルとなっています。わかりやすく言うと、**HTTP であろうと WebSocket であろうと、プロセス間通信であろうと同じデータフォーマットを利用できますよ**、と言うことです。

_Requests オブジェクト_ 

```json
{
  jsonrpc: "2.0";
  id: string | number;
  method: string;
  params?: { # この params 領域に MCP 独自の追加仕様を定義
    [key: string]: unknown;
  };
}
```

_Notifications オブジェクト(Requests のサブオブジェクト)_

```json
{ # id を持たない
  jsonrpc: "2.0";
  method: string;
  params?: {
    [key: string]: unknown;
  };
}
```

_Responses オブジェクト_

```json
{
  jsonrpc: "2.0";
  id: string | number;
  result?: {  # この result 領域に MCP 独自の追加仕様を定義
    [key: string]: unknown;
  }
  error?: {
    code: number;
    message: string;
    data?: unknown;
  }
}
```

### そのほかの MCP Protocol の機能仕様

**認証**

JSON-RPC 2.0 には定義がありませんが、`SHOULD` として対応が求められています。しかし認証メカニズムについてはまだ議論の途上のようです。

**スキーマ**

プロトコルの完全な仕様は、[Typescript スキーマ](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-03-26/schema.ts)として定義されています。

## まとめ

本 Chapter では、MCP に登場するコンポーネントと Client ↔︎ Server 間の Base Protocol について解説しました。MCP の Base Protocol は JSON-RPC 2.0 を基盤とし、追加の機能のためのスキーマの定義を提供しています。将来的なプロトコルの拡張については後方互換性を保つために JSON-RPC 2.0 を用いること自体は変わらないでしょう。以降の Chapter では MCP コンポーネントごとの機能詳細、Protocol のやり取りのシーケンス、などを解説します。