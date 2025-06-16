---
title: "§04 MCP 仕様の全体像"
free: true
---

___MCP に関する理解編:___  _MCP の脆弱性と対策を理解するために必要な前提知識の解説_

---

本章の説明は、2025-03-26 の[仕様](https://modelcontextprotocol.io/specification/2025-03-26)に基づきます。

MCP を構成する登場人物(コンポーネント)は、MCP Client、MCP Server、MCP Host があります。MCP Client と MCP Server については前回概要を説明しました。本 Chapter では以降の Chapter で脆弱性やその対策を検討するために必要な MCP 仕様の全体像を俯瞰します。

![040101](/images/books/security-of-the-mcp/fig_c04_s01_01.png)

## コンポーネント

**MCP Host**

MCP Host は、統合開発環境（IDE）、AI アプリケーションなどです。ホストはユーザーインターフェースを提供し、AI モデルとの対話を管理する役割を担います。ユーザー入力を受け取り、適切な形式で AI モデル に転送して結果を表示します。そして、複数の MCP Client を作成および管理し、それらを通じて様々な MCP Server と通信することができます。

**MCP Client**

MCP Client は、MCP Host アプリケーションによって生成され、MCP Server ごとに隔離された接続を維持します。Server との通信は JSON-RPC 2.0 ベースのメッセージを交換します。

**MCP Server**

MCP Server は Resources、Prompts、Tools を Clients に提供します。Resources は AI モデルが使用するための Context や Data を提供し、Prompts はユーザーのためのテンプレートメッセージを提供します。そして、Tools は AI モデルにツール使用に関する機能を提供します。Server はローカルプロセスまたはリモートサービスとして使用できます。

MCP Server がローカルマシンにあるケースしか見たことがない、使ったことがない、という方もいるかと思います。今後詳細に解説する Streamable HTTP はリモートに MCP Server が存在することもあり得ます。

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

**バッチ処理**

JSON-RPC 2.0 にも定義されていますが、複数のリクエストオブジェクトを同時に送信するために、クライアントはリクエストオブジェクトで満たされた配列を送信してもよいです。受信側はバッチ処理をサポートしなければなりません。

**認証**

JSON-RPC 2.0 には定義がありませんが、SHOULD として対応が求められています。しかし認証メカニズムについてはまだ議論の途上のようです。

**スキーマ**

プロトコルの完全な仕様は、[Typescript スキーマ](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-03-26/schema.ts)として定義されています。

## まとめ

本 Chapter では、MCP に登場するコンポーネントと Client ↔︎ Server 間の Base Protocol について解説しました。MCP の Base Protocol は JSON-RPC 2.0 を基盤とし、追加の機能のためのスキーマの定義を提供しています。将来的なプロトコルの拡張については後方互換性を保つために JSON-RPC 2.0 を用いること自体は変わらないでしょう。以降の Chapter では MCP コンポーネントごとの機能詳細、Protocol のやり取りのシーケンス、などを解説します。

## SaaS コラム

本書では **SaaS コラム** で本文内容を補足する SaaS に関する解説を行います。

今回は MCP を SaaS で用いる際の後方互換性について考察します。SaaS はテナント単位でユーザーを管理します。テナント単位でサービスのどのバージョンを使うのかを決めているようなケースもあります。例えば、部門 A は Version 1、部門 B は Version 2 を用いる、などです。さらには Isolation 手法としてインフラストラクチャレベルで分離している場合は、Version ごとにインフラストラクチャを切り替えなければなりません。

SaaS で AI Agent 機能を提供する際には、あえてリスクとその対策工数を受容してまでツール使用に MCP を利用すべきかについては十分な検討が必要です。この場合は、[Strands Agents](https://aws.amazon.com/jp/blogs/news/introducing-strands-agents-an-open-source-ai-agents-sdk/) などのフレームワークを用いて Tool use を利用すれば良いでしょう。そうすれば自社のアプリケーションコード同様のバージョン管理を AI Agent 機能に適用できます。

一方で MCP から自社 SaaS を呼び出すケースはどうでしょうか。この場合は、MCP からツールとして呼び出す SaaS のバージョンを指定する必要があるでしょう。MCP でどのようにバージョンニングするべきでしょうか。SaaS やツール、パッケージのバージョンイングはセキュリティに関わるため今後の Chapter で解説します。