---
title: "§22 AgentCore Gateway 解説"
free: true
---

___MCP セキュリティに関する包括的な実装編:___ _MCP のセキュリティに対しての包括的な実装に関する解説_

---

FIXME: 以下のツール同期機能について調査して文章を相応しい箇所に追記して。

**ツール同期機能**

Gateway は MCP Server との連携において、重要なツール同期機能を提供します。

- **暗黙的同期（Implicit Synchronization）**: `CreateGatewayTarget` および `UpdateGatewayTarget` 操作中に自動的にツール発見とインデックス化が実行されます。Gateway は即座に MCP Server の tools/list 機能を呼び出し、利用可能なツールを取得して統合カタログで利用可能にします。

- **明示的同期（Explicit Synchronization）**: `SynchronizeGatewayTargets` API を呼び出すことで手動でツールカタログの更新をトリガーできます。MCP Server がツール定義を変更した際に使用します。この API は非同期で処理され、大規模なツールセットの場合は数分かかる場合があります。

同期機能により、Gateway はベクター埋め込みを事前計算してセマンティック検索を可能にし、正規化されたツールカタログを維持します。これにより、ユーザーは全てのターゲットタイプにわたって最新の利用可能なツールを発見し、呼び出すことができます。

**本 Chapter では Amazon Bedrock AgentCore Gateway について解説します。**

## AgentCore Gateway の概要

Amazon Bedrock AgentCore Gateway は、AI Agent が外部ツールやリソースに安全に MCP 接続するためのマネージドサービスです。現在プレビューリリース段階にあり、仕様は変更される可能性があります。概念アーキテクチャの MCPify Proxy に該当する機能です。

![220101](/images/books/security-of-the-mcp/fig_c22_s01_01.png)

**主要機能**

Gateway は複数の Target を持つことができ、各 Target は異なるタイプのリソースやサービスに接続できます。主な Target タイプは Lambda Target、Smithy API Model Target、OpenAPI Model Target の 3 種類です。Gateway 作成時には OAuth 認証設定が必須となり、認証サーバーの設定が必要です。

Gateway は以下の2つの MCP オペレーションを公開します。

1. **tools/list** - Gateway が提供するすべての利用可能なツールをリスト表示
2. **tools/call** - 提供された引数で特定のツールを呼び出し

## Target タイプ

![220102](/images/books/security-of-the-mcp/fig_c22_s01_02.png)

**Lambda Target**

Lambda Target では、独自の Lambda 関数を指定するか、システムが自動生成する Lambda を使用できます。**Tool Schema** を定義することで、各ツールの名前、説明、入力スキーマを明確に指定します。Lambda ARN を指定しない場合、システムが自動的に Lambda 関数を作成し、基本的なツール実装を提供します。

**API Model Target**

***Smithy API Model Target*** では、AWS サービスの API モデルを直接利用できます。デフォルトでは DynamoDB の API モデルが使用されますが、S3 URI からカスタム Smithy モデルを読み込むことも可能です。AWS の数百のサービスの Smithy API モデルが GitHub で公開されており、これらを活用することで AWS サービスとの統合が容易になります。

***OpenAPI Model Target*** では、外部 API との接続が可能です。API キー認証と OAuth 認証の両方をサポートしており、認証情報は Header や Query Parameter として設定できます。外部サービス API を Gateway 経由で AI Agent から利用できるようになります。

### AI Agent との統合方法

Gateway を AI Agent で使用する際は、Gateway URL とアクセストークンが必要です。Strands Agent フレームワークを使用する場合、MCP Client を初期化し、利用可能なツール一覧を取得してから Agent に統合します。Gateway は pagination をサポートしており、大量のツールがある場合でも効率的に取得できます。

## Gateway 概要

Gateway は MCP Client にとっては MCP プロトコルで疎通する MCP Server として見えます。AI Agent がツールを発見し相互作用するための標準化されたアクセスポイントを提供します。単一の Gateway は複数の Target を持つことができ、この設計により、一つのエンドポイントから多様なツールにアクセスできるようになります。

**外部リソースとのアクセス連携の要件**

Gateway が API や Lambda 関数を呼び出す際は、適切な認証と認可の仕組みが必要です。Smithy や Lambda Target の場合、Gateway は接続された実行ロールを使用してこれらの Target への認可を取得します。この実行ロールは AWS IAM による認証を経て、必要な権限を持つことで外部リソースへの安全なアクセスを実現します。

OpenAPI Target の場合、API キーや OAuth による認証情報と、それに基づく認可情報を格納する AgentCore Credential Provider を接続する必要があります。

**認証情報管理の仕組み**

Gateway が API や Lambda 関数を呼び出す際は、適切な認証情報が必要です。Smithy や Lambda Target の場合、Gateway は接続された実行ロールを使用してこれらの Target を呼び出します。

OpenAPI Target の場合、API キーや OAuth 認証情報を格納する [AgentCore Credential Provider](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-providers.html#:~:text=Resource%20credential%20providers%20in%20AgentCore,particular%20service%20or%20identity%20system.) を接続する必要があります。

詳細は Identity に関する Chapter で解説しますが、AgentCore Credential Provider は AgentCore Identity の一部として提供される Credential 情報管理サービスで、API Key、OAuth トークン、などを暗号化して保存し、必要な時に保存された情報を動的に取得します。つまり、AI エージェントが外部サービスにアクセスする際の Credential 情報を安全に管理・提供する AWS のマネージドサービスです。

## セキュリティ設計

Gateway を設定する前に、適切な権限の指定方法を理解することが重要です。これにより、Gateway を適切に保護できます。Inbound Auth はゲートウェイへの入力を制御し、Outbound Auth はバックエンドリソースへの安全な接続を確保します。

![220103](/images/books/security-of-the-mcp/fig_c22_s01_03.png)

**Gateway の権限管理**

Gateway を使用する際は、以下の 3 つの権限カテゴリを考慮する必要があります。

**Gateway アクセス権限（Inbound Auth）**

> MCP プロトコル経由で誰が何を呼び出せるかを制御

Gateway は MCP で指定された JWT トークンベースのアクセス制御を使用します。これらの設定は Gateway のプロパティとして指定する必要があります。標準的な AWS IAM メカニズムではなく、MCP プロトコルに準拠した認可方式を採用しています。

**Gateway 実行権限（Outbound Auth）**

> Gateway が他のリソースでアクションを実行するために必要な権限

Gateway 作成時には、Gateway が AWS リソースや外部サービスにアクセスするための実行ロールを提供する必要があります。このロールは、Gateway が他のサービスにリクエストを行う際の権限を定義します。

**Gateway セキュリティベストプラクティス**

Gateway の運用において、セキュリティを確保するためには複数の観点から包括的な対策を講じる必要があります。まず、最小権限の原則を徹底することが重要です。Gateway の機能に必要な権限のみを付与し、可能な限りワイルドカードではなく具体的なリソース ARN を使用することで、不要なアクセス権限を排除できます。また、権限の定期的なレビューと監査を実施することで、時間の経過とともに蓄積される不要な権限を特定し、適切に削除すべきです。

次に、機能別のロール分離を行うことで、セキュリティリスクを最小化できます。管理用と実行用で異なるロールを使用し、目的が異なる Gateway には別々のロールを作成することで、権限の混在を防ぎ、セキュリティインシデントの影響範囲を限定しましょう。

認証情報の管理においては、AgentCore Credential Provider を活用し、API キーと OAuth 認証情報を適切に管理することが不可欠です。AgentCore Credential Provider は AgentCOre 向けに設計されており、OAuth トークンの自動リフレッシュや認証情報の安全な取得を自動化します。これにより、認証情報の漏洩リスクを大幅に軽減できます。Token Vault という一時的なストレージを利用してアクセストークンの有効期間においてはトークンを Token Vault からとってくるためユーザーから毎回取得する必要がない点が便利です。

運用面では、Gateway 操作の CloudTrail ログ記録を有効化し、アクセスパターンと権限使用状況の定期的なレビューを行うことで、不正なアクセスや異常な操作を早期に検出できます。これらの監視と監査機能により、セキュリティインシデントの予防と迅速な対応が可能になります。

## セマンティック検索

セマンティック検索は、インテリジェントなツール発見を可能にし、通常のリストツールの制限（通常 100 程度）を超えることができます。この機能により以下が実現されます。

- **コンテキストに関連するツールサブセットの提供**
- **フォーカスされた関連結果によるツール選択精度の向上**
- **トークン処理の削減による推論パフォーマンスの向上**
- **全体的なオーケストレーション効率とレスポンス時間の改善**

セマンティック検索を有効にするには、`CreateGateway` リクエストの `protocolConfiguration` フィールドに `"searchType": "SEMANTIC"` を追加します。セマンティック検索は作成時のみ有効化可能で、後から更新することはできません。

```json
"protocolConfiguration": {
  "mcp": {
    "searchType": "SEMANTIC"
  }
}
```

Gateway は `x_amz_bedrock_agentcore_search` ツールを通じて組み込みのセマンティック検索機能を提供します。この機能により、Agent はすべての利用可能なツールを知る必要なく、特定のタスクに最も関連性の高いツールを見つけることができます。

**セマンティック検索の使用例**

```python
tool_name = "x_amz_bedrock_agentcore_search"
args = {"query": "How do I process images?"}
await session.call_tool(tool_name,arguments=args,...)
```

## MCP 仕様への準拠度の確認

この Gateway の Inbound Auth が MCP 仕様(2025-06-18) にどの程度沿っているのかを確認してみます。

### MUST 要件

MCP 仕様では以下の要件が必須とされていることを Chapter12 で解説しましたので必要に応じて確認しながら読みすすめてください。

**✅ OAuth 2.1 with PKCE** については、公開 Client 向けの Proof Key for Code Exchange サポートが必要です。これは認可コードの傍受や注入攻撃を防ぐため、Client がチャレンジペアを作成し、元のリクエスト者のみが認可コードをトークンと交換できることを保証する仕組みです。これは MCP Client と認可 Server 間の実装要件であり、Gateway 自体が直接実装する責務ではありません。Gateway は PKCE フローの結果として発行されたアクセストークンを受信し、検証する役割を担います。このアクセストークンの検証自体は上述した Outbound Auth で担うことが可能です。Amazon Cognito 自体は OAuth 2.1 with PKCE を完全に[サポート](https://docs.aws.amazon.com/ja_jp/cognito/latest/developerguide/using-pkce-in-authorization-code.html)しています。

**✅ RFC 8414 (OAuth 2.0 Authorization Server Metadata)** については、認可 Server の機能を自動発見するためのメタデータエンドポイントの提供が必要です。これには認可エンドポイント、トークン生成方法、サポートされる署名機能などが含まれます。これは認可 Server の責務です。Cognito は `https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration` の形式で RFC 8414 準拠のメタデータエンドポイントを提供しています。Gateway はこの認可 Server を使用するよう設定します。

**✅ RFC 9728 (OAuth 2.0 Protected Resource Metadata)** については、AgentCore Gateway の公式サンプル内の [OAuth フロー図](https://github.com/littlemex/amazon-bedrock-agentcore-samples/blob/main/01-tutorials/02-AgentCore-gateway/images/oauth-flow-gateway.png)において、メタデータ発見プロセスが明確に示されており、`/.well-known/oauth-authorization-server` エンドポイントを通じた標準的なメタデータ発見機能の実装が示唆されています。これにより、MCP Client は Gateway のリソースアクセス要件を動的に発見でき、適切な認証フローを自動的に選択することが可能になります。また、図から POP (Proof of Possession) メタデータの検証機能も実装されていると示唆され、トークンの適切な使用を保証しているようです。

> POP は OAuth 2.0 の拡張仕様で、アクセストークンの**所有証明**を行う仕組みです。通常の Bearer トークンと異なり、Client がそのトークンを正当に所有していることを公開鍵認証を用いて証明します。

**🔶 RFC 8707 (Resource Indicators for OAuth 2.0)** については、トークンを意図された対象者に明示的にバインドするリソースインジケーターのサポートが推奨されます。AgentCore Gateway は Inbound Auth 機能において、JWT トークンの `allowedAudience` パラメータによる audience 検証を実装しており、これにより各トークンが特定の Gateway リソース向けに発行されたことを確認できます。また、`allowedClients` による client ID 検証と Discovery URL を通じた認可サーバーメタデータの検証も行われ、audience 検証により RFC 8707 の要件に対して類似の効果を得ています。

### SHOULD 要件

**🔶 RFC 7591 (OAuth 2.0 Dynamic Client Registration Protocol)** については、MCP Client がユーザーの操作なしに OAuth Client ID を取得できるようにする動的 Client 登録プロトコルのサポートが推奨されます。これは MCP において重要な機能である理由として、Client が事前にすべての可能な MCP Server とその認可 Server を知ることができない場合があること、手動登録がユーザーにとって摩擦を生むこと、新しい MCP Server とその認可 Server へのシームレスな接続を可能にすること、認可 Server が独自の登録ポリシーを実装できることが挙げられます。

Gateway と連携する Cognito は AWS API を通じてプログラマティックな Client 登録をサポートしていますが、標準的な RFC 7591 エンドポイント（`/register`）は実装されていません。RFC 7591 に対応した認可 Server を使用することで標準準拠の DCR を実現できます。

AgentCore Gateway 側では、`UpdateGateway` API を通じて `allowedClients` の動的更新が可能です。具体的には、[`authorizerConfiguration`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CustomJWTAuthorizerConfiguration.html) パラメータ内の `customJWTAuthorizer` オブジェクトにおいて、`allowedClients` 配列を更新することで新しい Client ID を追加できます。ただし、この更新は配列全体の置き換えとなるため、既存の Client ID を保持したい場合は、事前に `GetGateway` API で現在の設定を取得し、新しい Client ID を既存のリストに追加する形で実装する必要があります。

RFC 7591 準拠の完全な自動化を実現するためには、認可 Server の変更または AWS Lambda による追加実装が必要です。認可 Server を RFC 7591 対応のものに変更する場合、標準的な DCR フローを直接利用できるため、最もシンプルなアプローチとなります。一方、Cognito を継続使用する場合は、AWS Lambda で RFC 7591 エンドポイントを実装し、動的登録成功時に `UpdateGateway` API で `allowedClients` を自動更新するプロキシ機能を構築することで対応可能です。

この Lambda プロキシアプローチでは、MCP Client からの動的登録リクエストを受信し、認可 Server への登録処理を実行した後、取得した Client ID を Gateway の `allowedClients` に追加する一連の処理を自動化します。実装時には、Gateway の現在の認可設定を取得し、`discoveryUrl` や `allowedAudience` といった既存の設定を保持しながら、`allowedClients` 配列のみを更新する必要があります。

DCR が完全に実装されていない環境においても、Gateway レベルでの論理的分離により部門別や環境別に複数の Gateway を運用することで粗粒度な制御を実現し、OAuth スコープを活用した機能レベルでの制御と組み合わせることで、読み取り専用、書き込み可能、管理者権限といった段階的な権限管理が可能になります。さらに、Target レベルでの Outbound Auth 設定を適切に行うことで、Gateway からバックエンドリソースへのアクセス制御を強化し、多層防御の観点からセキュリティを向上させることができます。

## まとめ

Amazon Bedrock AgentCore Gateway について解説しました。MCPify のためのゲートウェイとして認証・認可やセマンティック検索に対応しており、実運用レベルのケイパビリティがあることが確認できました。

AgentCore Gateway は、MCP 認可仕様の主要な MUST 要件（OAuth 2.1 with PKCE、RFC 8414、RFC 9728）に対応しており、基本的な標準準拠を実現しています。特に RFC 9728 対応により、MCP Client による動的なメタデータ発見が可能となり、標準的な OAuth 2.0 エコシステムとの統合が円滑に行えます。

一部の SHOULD 要件（RFC 7591 Dynamic Client Registration）や新しい仕様（RFC 8707 Resource Indicators）への対応については、プレビュー段階での今後の機能拡張が期待されます。現在の制約に対しては、自前での実装、Gateway の論理的分離やスコープベースの制御といった実用的な回避策により、企業レベルでの運用要件を満たすことが可能です。

> P.S 自分で MCPify Proxy の実装を進めていましたがとんでもないスピード感で MCPify のサービスが出てきたので驚いています。このような MCPify の仕様への追従がより進めばわざわざ自前で MCP Server を実装するケースは今後減るかもしれませんね。