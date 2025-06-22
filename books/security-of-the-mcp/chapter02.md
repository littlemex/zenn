---
title: "§02 MCP の前にそもそも Tool use とは何なのか？"
free: true
---

___MCP に関する理解編:___  _MCP の脆弱性と対策を理解するために必要な前提知識の解説_

---

ツール使用 (Tool use) は _Function calling_ と言う名称でも知られています。事前に定義された外部ツールや関数を呼び出すことで AI モデルの機能を拡張する能力を指します。AI モデルに一連の事前定義されたツールへのアクセスを提供し、必要に応じて呼び出すことができます。

> _引用: [Tool use basics](https://github.com/anthropics/courses/blob/master/tool_use/01_tool_use_overview.ipynb)_

![](/images/books/security-of-the-mcp/fig_c02_s01_01.png)

## ツール使用の仕組み

AI モデルのツール使用の能力について、AI モデル自身がツールを呼び出して使っていると思われている方がいるかもしれません。

少なくとも Anthropic の AI モデルに関して言えば、  
**AI モデル自身はツールへの直接的なアクセス権を有していませんし、直接的にツールを呼び出す処理も行なっていません。**

AI モデルに呼び出すことができるツールの一覧について伝え、実際のツールコードを実行し、その結果を AI モデルに伝えるのは AI モデルの外にある機能です。

> _引用: [Tool use basics](https://github.com/anthropics/courses/blob/master/tool_use/01_tool_use_overview.ipynb)_

![](/images/books/security-of-the-mcp/fig_c02_s01_02.png)

具体的なツール使用のステップを説明します。

**ステップ 1：AI モデルにツールとユーザープロンプトを提供する**

- **ツール指定:** AI モデルにアクセスさせたいツールのセットを定義します。これには、ツールの名前、説明、入力スキーマが含まれます。
- ___例: 「A 倉庫には商品 B は何個残っていますか？」___ などの一つ以上のツールを使用して回答する必要があるプロンプトを提供します。

以下は Amazon Bedrock の Tool use に関する[公式 Document](https://docs.aws.amazon.com/ja_jp/bedrock/latest/userguide/model-parameters-anthropic-claude-messages-tool-use.html) に記載のツール指定例です。

```json
// ツール指定
[
    {
        "name": "top_song",
        "description": "Get the most popular song played on a radio station.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sign": {
                    "type": "string",
                    "description": "The call sign for the radio station for which you want the most popular song. Example calls signs are WZPZ and WKRP."
                }
            },
            "required": [
                "sign"
            ]
        }
    }
]
```

**ステップ 2：ツール使用に関する AI モデル応答**

- AI モデルは入力プロンプトを評価し、使用可能なツールのいずれかがユーザーの質問やタスクに役立つかどうかを判断します。役立つ場合、どのツールをどの入力で使用するかも決定します。
- AI モデルは多くの場合で適切にフォーマットされた _ツール使用リクエスト_ を出力します。
- API レスポンスには、AI モデルが外部ツールを使用したいことを示す `stop_reason` として `tool_use` が含まれます。(Claude の場合)

_例: ツール使用リクエスト_

```json
{
  "stop_reason": "tool_use",
  "tool_use": {
    "name": "inventory_lookup",
    "input": {
      "product": "B"
    }
  }
}
```

以下は Amazon Bedrock の Tool use に関する[公式 Document](https://docs.aws.amazon.com/ja_jp/bedrock/latest/userguide/model-parameters-anthropic-claude-messages-tool-use.html) に記載の出力例です。

```json
// モデル出力
{
    "id": "msg_bdrk_01USsY5m3XRUF4FCppHP8KBx",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-sonnet-20240229",
    "stop_sequence": null,
    "usage": {
        "input_tokens": 375,
        "output_tokens": 36
    },
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_bdrk_01SnXQc6YVWD8Dom5jz7KhHy",
            "name": "top_song",
            "input": {
                "sign": "WZPZ"
            }
        }
    ],
    "stop_reason": "tool_use"
}
```

> 100% 適切な _ツール使用リクエスト_ を AI モデルが出力する保証はありません。

**ステップ 3：ツール入力を抽出し、コードを実行し、結果を返す**

- クライアント側で、 ステップ 2 で得られた _ツール使用リクエスト_ から _ツール名_ と _入力_ を抽出します。
- クライアント側で実際のツールコードを実行します。
- `tool_result` コンテンツブロックを含む新しいユーザーメッセージで会話を続けることで、結果を AI モデルに返します。

**ステップ 4：AI モデルがツール結果を使用して応答を作成する**

- ツール結果を受け取った後、AI モデルはその情報を使用して元のプロンプトに対する最終的な応答を作成します。

ここで _ステップ 3_、_ステップ 4_ は実はオプショナルです。つまり、`tool_result` で会話を継続しなければ、必要な _ツール名_、_入力_ のみを取得して終了することもできます。
**これは後の MCP の仕組みの説明で非常に重要な知識です!、必ず覚えておいてください!!**

## まとめ

本 Chapter では、MCP の仕組みの解説の前にその前提となる Tool use の仕組みについて簡単に触れました。このような仕組みであることが理解できるとモデルに無限にツールを持たせることが難しいことがわかるでしょう。ツールを確実に正しく利用してくれる保証はないため、モデルごとに何個までツールを使いこなせるのかの確認が必要です。ドメインに応じた現場の実験が精度向上にとって非常に重要でしょう。多数の Tool use が必要な場合には **1/** マルチエージェントコラボレーションを取り入れて、各エージェントが特定の Tool use に特化するような設計や、**2/** 多数のツール指定の情報をセマンティック検索で retrieve して、関連するツールのみをモデルに説明する、などいくつかのパターンがあります。