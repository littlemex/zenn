---
title: "§05 JSON-RPC 2.0 仕様の日本語翻訳"
free: false
---

**JSON-RPC 2.0 の英語の原著を見やすくするスタイリング以外の手を加えない形で日本語に翻訳しました。** 本章の説明は、[JSON-PRC 2.0 Specification](https://www.jsonrpc.org/specification) に基づき、あくまで参考として原文を常に正とします。

原文日付：2010-03-26（2009-05-24版に基づく）、更新日：2013-01-04
著者：[JSON-RPC ワーキンググループ](https://groups.google.com/forum/#!forum/json-rpc) <json-rpc@googlegroups.com>

## 1 概要

JSON-RPC は、ステートレスで軽量なリモートプロシージャコール（RPC）プロトコルです。この仕様は主に、いくつかのデータ構造とその処理に関するルールを定義しています。同一プロセス内、ソケット経由、HTTP 経由、または様々なメッセージパッシング環境など、様々な環境で使用できるトランスポート非依存の概念です。データフォーマットとして [JSON](http://www.json.org)（[RFC 4627](http://www.ietf.org/rfc/rfc4627.txt)）を使用します。

シンプルであることを目指して設計されています！

## 2 規約

この文書内の「MUST（しなければならない）」、「MUST NOT（してはならない）」、「REQUIRED（必須）」、「SHALL（するものとする）」、「SHALL NOT（しないものとする）」、「SHOULD（すべきである）」、「SHOULD NOT（すべきでない）」、「RECOMMENDED（推奨）」、「MAY（してもよい）」、「OPTIONAL（任意）」という用語は、[RFC 2119](http://www.ietf.org/rfc/rfc2119.txt) に記述されている形で解釈されます。

JSON-RPC は JSON を利用するため、同じ型システムを持ちます（<http://www.json.org>または [RFC 4627](http://www.ietf.org/rfc/rfc4627.txt) を参照）。JSON は 4 つのプリミティブ型（文字列、数値、真偽値、Null）と 2 つの構造型（オブジェクトと配列）を表現できます。この仕様書での「プリミティブ」という用語は、これら 4 つのプリミティブ JSON 型のいずれかを指します。「構造型」という用語は、JSON の構造型のいずれかを指します。この文書で JSON の型に言及する場合、最初の文字は常に大文字です：Object、Array、String、Number、Boolean、Null。True と False も大文字で表記されます。

クライアントとサーバー間でやり取りされるすべてのメンバー名は、あらゆる種類の照合において大文字と小文字を区別するものと考えるべきです。関数、メソッド、プロシージャという用語は互換的に使用されると考えてよいでしょう。

クライアントはリクエストオブジェクトの発信元であり、レスポンスオブジェクトのハンドラーとして定義されます。サーバーはレスポンスオブジェクトの発信元であり、リクエストオブジェクトのハンドラーとして定義されます。

この仕様の一つの実装は、同時に他の異なるクライアントや同じクライアントに対して、これらの役割の両方を容易に果たすことができます。この仕様はそのような複雑さのレイヤーには対応していません。

## 3 互換性

JSON-RPC 2.0 のリクエストオブジェクトとレスポンスオブジェクトは、既存の JSON-RPC 1.0 クライアントやサーバーでは動作しない可能性があります。ただし、2.0 は常に「jsonrpc」という名前のメンバーを持ち、その値は「2.0」という文字列であるのに対し、1.0 はそうではないため、2 つのバージョンを区別するのは容易です。ほとんどの 2.0 実装は、1.0 のピアツーピアやクラスヒンティングの側面でなくても、1.0 オブジェクトの処理を検討すべきです。

## 4 リクエストオブジェクト

RPCコールは、サーバーにリクエストオブジェクトを送信することで表現されます。リクエストオブジェクトには以下のメンバーがあります：

_jsonrpc_
- JSON-RPCプロトコルのバージョンを指定する文字列。正確に「2.0」でなければなりません。

_method_
- 呼び出されるメソッドの名前を含む文字列。「rpc」という単語で始まり、その後にピリオド文字（U+002E、ASCII 46）が続くメソッド名は、RPC 内部メソッドと拡張のために予約されており、他の目的に使用してはなりません。

_params_
- メソッドの呼び出し中に使用されるパラメータ値を保持する構造化された値。このメンバーは省略してもかまいません。

_id_
- クライアントによって確立された識別子で、含まれる場合は文字列、数値、またはNULL値を含まなければなりません。含まれていない場合は通知と見なされます。通常、値は Null であるべきではなく[1]、数値は小数部分を含むべきではありません[2]。

サーバーは、含まれている場合、レスポンスオブジェクトで同じ値を返さなければなりません。このメンバーは、2 つのオブジェクト間のコンテキストを関連付けるために使用されます。

[1] リクエストオブジェクトの id メンバーの値として Null を使用することは推奨されません。これは、この仕様では id が不明なレスポンスに Null の値を使用するためです。また、JSON-RPC 1.0 では通知の id 値として Null を使用するため、処理に混乱を招く可能性があります。

[2] 小数部分は問題になる可能性があります。多くの 10 進小数は 2 進小数として正確に表現できないためです。

### 4.1 通知

通知は「id」メンバーを持たないリクエストオブジェクトです。通知であるリクエストオブジェクトは、クライアントが対応するレスポンスオブジェクトに関心がないことを示し、そのためクライアントにレスポンスオブジェクトを返す必要はありません。サーバーは、バッチリクエスト内のものを含め、通知に返信してはなりません。

通知は定義上確認できません。レスポンスオブジェクトが返されないためです。そのため、クライアントはエラー（例えば「Invalid params」、「Internal error」など）を認識できません。

### 4.2 パラメータ構造

存在する場合、RPC コールのパラメータは構造化された値として提供されなければなりません。位置による配列を通じて、または名前によるオブジェクトを通じて提供されます。

* 位置による：params は配列でなければならず、サーバーが期待する順序で値を含みます。
* 名前による：params はオブジェクトでなければならず、メンバー名はサーバーが期待するパラメータ名と一致します。期待される名前の欠如はエラーを生成する可能性があります。名前は、メソッドが期待するパラメータと大文字小文字を含めて正確に一致しなければなりません。

## 5 レスポンスオブジェクト

RPC コールが行われると、通知の場合を除き、サーバーはレスポンスで応答しなければなりません。レスポンスは単一の JSON オブジェクトとして表現され、以下のメンバーを持ちます：

_jsonrpc_
- JSON-RPC プロトコルのバージョンを指定する文字列。正確に「2.0」でなければなりません。

_result_
- このメンバーは成功時に必須です。
- メソッドの呼び出し中にエラーが発生した場合、このメンバーは存在してはなりません。
- このメンバーの値は、サーバー上で呼び出されたメソッドによって決定されます。

_error_
- このメンバーはエラー時に必須です。
- 呼び出し中にエラーが発生しなかった場合、このメンバーは存在してはなりません。
- このメンバーの値は、セクション 5.1 で定義されているオブジェクトでなければなりません。

_id_
- このメンバーは必須です。
- リクエストオブジェクトの id メンバーの値と同じでなければなりません。
- リクエストオブジェクトの id の検出中にエラーが発生した場合（例：解析エラー/無効なリクエスト）、Null でなければなりません。

result メンバーまたは error メンバーのいずれかを含める必要がありますが、両方のメンバーを含めてはなりません。

### 5.1 エラーオブジェクト

RPC コールがエラーに遭遇した場合、レスポンスオブジェクトは以下のメンバーを持つオブジェクトの値を持つ error メンバーを含まなければなりません：

_code_
- 発生したエラーの種類を示す数値。
- これは整数でなければなりません。

_message_
- エラーの簡潔な説明を提供する文字列。
- メッセージは簡潔な一文に限定すべきです。

_data_
- エラーに関する追加情報を含むプリミティブまたは構造化された値。
- これは省略可能です。
- このメンバーの値はサーバーによって定義されます（例：詳細なエラー情報、ネストされたエラーなど）。

-32768 から -32000 までのエラーコードは、事前定義されたエラー用に予約されています。この範囲内にあるが、以下で明示的に定義されていないコードは将来の使用のために予約されています。エラーコードは、次のURLで提案されている XML-RPC のものとほぼ同じです：<http://xmlrpc-epi.sourceforge.net/specs/rfc.fault_codes.php>

| コード           | メッセージ        | 意味                                                                                |
| --------------- | ---------------- | ---------------------------------------------------------------------------------- |
| -32700          | Parse error      | サーバーが無効な JSON を受信しました。JSON テキストの解析中にサーバーでエラーが発生しました。|
| -32600          | Invalid Request  | 送信された JSON は有効なリクエストオブジェクトではありません。|
| -32601          | Method not found | メソッドが存在しない/利用できません。|
| -32602          | Invalid params   | 無効なメソッドパラメータ。|
| -32603          | Internal error   | 内部 JSON-RPC エラー。|
| -32000〜-32099  | Server error     | 実装定義のサーバーエラー用に予約されています。|

残りの空間はアプリケーション定義のエラーに使用できます。

## 6 バッチ

複数のリクエストオブジェクトを同時に送信するために、クライアントはリクエストオブジェクトで満たされた配列を送信してもよいです。

すべてのバッチリクエストオブジェクトが処理された後、サーバーは対応するレスポンスオブジェクトを含む配列で応答する必要があります。通知を除き、各リクエストオブジェクトに対してレスポンスオブジェクトが存在すべきです。サーバーはバッチ RPC コールを一連の並行タスクとして処理し、任意の順序と任意の並列度で処理することができます。

バッチコールから返されるレスポンスオブジェクトは、配列内の任意の順序で返されることがあります。クライアントは、各オブジェクト内の id メンバーに基づいて、リクエストオブジェクトのセットと結果のレスポンスオブジェクトのセット間のコンテキストを一致させるべきです。

バッチ RPC コール自体が有効な JSON または少なくとも 1 つの値を持つ配列として認識されない場合、サーバーからのレスポンスは単一のレスポンスオブジェクトでなければなりません。クライアントに送信されるレスポンス配列内にレスポンスオブジェクトが含まれていない場合、サーバーは空の配列を返してはならず、何も返すべきではありません。

## 7 例

```
構文：
--> サーバーに送信されるデータ  
<-- クライアントに送信されるデータ
```

_位置パラメータを使用した RPC コール：_

```
--> {"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 1}  
<-- {"jsonrpc": "2.0", "result": 19, "id": 1}  
  
--> {"jsonrpc": "2.0", "method": "subtract", "params": [23, 42], "id": 2}
<-- {"jsonrpc": "2.0", "result": -19, "id": 2}
```

_名前付きパラメータを使用した RPC コール：_

```
--> {"jsonrpc": "2.0", "method": "subtract", "params": {"subtrahend": 23, "minuend": 42}, "id": 3}
<-- {"jsonrpc": "2.0", "result": 19, "id": 3}

--> {"jsonrpc": "2.0", "method": "subtract", "params": {"minuend": 42, "subtrahend": 23}, "id": 4}
<-- {"jsonrpc": "2.0", "result": 19, "id": 4}
```

_通知：_

```
--> {"jsonrpc": "2.0", "method": "update", "params": [1,2,3,4,5]}  
--> {"jsonrpc": "2.0", "method": "foobar"}
```

_存在しないメソッドの RPC コール：_

```
--> {"jsonrpc": "2.0", "method": "foobar", "id": "1"}  
<-- {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": "1"}
```

_無効なJSONを使用した RPC コール：_

```
--> {"jsonrpc": "2.0", "method": "foobar, "params": "bar", "baz]  
<-- {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": null}
```

_無効なリクエストオブジェクトを使用した RPC コール：_

```
--> {"jsonrpc": "2.0", "method": 1, "params": "bar"}  
<-- {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null}
```

_無効なJSONを使用した RPC コールバッチ：_

```
--> [
  {"jsonrpc": "2.0", "method": "sum", "params": [1,2,4], "id": "1"},
  {"jsonrpc": "2.0", "method"
]
<-- {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": null}
```

_空の配列を使用した RPC コール：_

```
--> []
<-- {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null}
```

_無効なバッチ（ただし空ではない）を使用した RPC コール：_

```
--> [1]  
<-- [
  {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null}
]
```

_無効なバッチを使用した RPC コール：_

```
--> [1,2,3]  
<-- [
  {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null},
  {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null},
  {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null}
]
```

_RPC コールバッチ：_

```
--> [  
        {"jsonrpc": "2.0", "method": "sum", "params": [1,2,4], "id": "1"},  
        {"jsonrpc": "2.0", "method": "notify_hello", "params": [7]},  
        {"jsonrpc": "2.0", "method": "subtract", "params": [42,23], "id": "2"},  
        {"foo": "boo"},  
        {"jsonrpc": "2.0", "method": "foo.get", "params": {"name": "myself"}, "id": "5"},  
        {"jsonrpc": "2.0", "method": "get_data", "id": "9"}   
    ]  
<-- [  
        {"jsonrpc": "2.0", "result": 7, "id": "1"},  
        {"jsonrpc": "2.0", "result": 19, "id": "2"},  
        {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null},  
        {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": "5"},  
        {"jsonrpc": "2.0", "result": ["hello", 5], "id": "9"}  
    ]
```

_すべて通知の RPC コールバッチ：_

```
--> [  
        {"jsonrpc": "2.0", "method": "notify_sum", "params": [1,2,4]},  
        {"jsonrpc": "2.0", "method": "notify_hello", "params": [7]}  
    ]  
<-- //すべての通知バッチに対しては何も返されません
```

## 8 拡張機能

「rpc.」で始まるメソッド名はシステム拡張用に予約されており、他の目的に使用してはなりません。各システム拡張は関連する仕様で定義されています。すべてのシステム拡張はオプションです。

---

Copyright (C) 2007-2010 by the JSON-RPC Working Group

この文書およびその翻訳は JSON-RPC の実装に使用することができ、コピーして他者に提供することができます。また、この文書に関するコメントや説明、または実装の支援を目的とした派生物は、上記の著作権表示とこの段落がすべてのコピーおよび派生物に含まれている限り、いかなる種類の制限もなく、準備、コピー、公開、配布することができます。ただし、この文書自体はいかなる方法でも修正してはなりません。

上記の限定的な許可は永続的であり、取り消されることはありません。

この文書および本書に含まれる情報は「現状のまま」提供され、明示または黙示を問わず、すべての保証は否認されます。これには、本書に含まれる情報の使用が権利を侵害しないという保証、または商品性や特定目的への適合性に関するいかなる黙示的保証も含まれますが、これらに限定されません。

---

サイトは [MPCM Technologies LLC](http://www.mpcm.com) の [Matt Morley](http://www.linkedin.com/in/matthewpetercharlesmorley) によって作成され、[JSON-RPCグーグルグループ](http://groups.google.com/group/json-rpc) の管理者です。

## まとめ

MCP の Protocol の基盤として用いられている JSON-RPC 2.0 の仕様を日本語翻訳しました。