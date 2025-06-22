---
title: "§13 認可: Streamable HTTP 実装解説"
free: true
---

___MCP に関する実装理解編:___  _MCP の脆弱性と対策を実装するために必要な開発者向け知識の解説_

---

本章の説明は、2025-06-18 の[仕様](https://modelcontextprotocol.io/specification/2025-06-18)に基づきます。

MCP Specification: Base Protocol、**Authorization（今ここ）**、Client Features、Server Features、Security Best Practices

本 Chapter では Streamable HTTP の typescript-sdk(tag: 1.12.1) の [Client 実装](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/client/streamableHttp.ts) と [Server 実装](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/streamableHttp.ts) について解説します。**本 Chapter では Streamable HTTP のセキュリティ関連実装、とりわけ、認可、について主に解説します。** 前の Chapter で認可に関する仕様を解説しましたが、まだ実装側は完全に仕様に追従できていないケースもあるでしょう。認可 Server の実装詳細に関してはそもそも仕様外です。本 Chapter では **MCP Server 実装者の視点**で主に解説します。MCP Client や認可 Server を AWS でどのように実装すべきか、については今後解説します。

## 認可

**MCP Server が認可仕様の中で何をしなければならないのかの責務を整理**し、それぞれの責務に対しての typescript-sdk の実装状況を確認します。MCP Server が担う責務が認可フローの全体像の中でどこで何をしているのか、については Chapter 12 で既にまとめてあるので適宜そちらを参照してください。

### RFC9728: リソースメタデータの提供

| ID | 責務詳細 | SDK対応 |
|--|----------|------------|
| 01 | ___MUST:___ `/.well-known/oauth-protected-resource` エンドポイント実装 | ___対応あり[メソッド]:___ `mcpAuthMetadataRouter` |
| 02 | ___MUST:___ `authorization_servers` フィールド提供 | ___対応あり[スキーマ]:___ `OAuthProtectedResourceMetadataSchema` |
| 03 | ___MUST:___ `401 Unauthorized` レスポンス時に `WWW-Authenticate` ヘッダーでメタデータ URL を提供 | ___対応あり[メソッド]:___ `requireBearerAuth` |
| 04 | ___MUST:___ `resource` フィールド提供 | ___対応あり[スキーマ]:___ `OAuthProtectedResourceMetadataSchema` |
| 05 | ___SHOULD:___ `scopes_supported` フィールド提供 |  ___対応あり[スキーマ]:___ `OAuthProtectedResourceMetadataSchema` |
| 06 | ___SHOULD:___ `resource_name` フィールド提供 | ___対応あり[スキーマ]:___ `OAuthProtectedResourceMetadataSchema` |

RFC9728 において、MCP Server はリソース Server として機能します。リソース Server は、ツールなどの保護されたリソースへのアクセスを管理し、適切な認可を持つ Client のみがアクセスできるようにする役割を担います。MCP Server は、Client からのリクエストを受け取り、そのリクエストに含まれるアクセストークンを検証し、認可された操作のみを許可する責務があります。

RFC9728 で MCP Server は、自身に関するメタデータを提供することで、Client がどのように認証・認可を行うべきかを明示的に伝える必要があります。

MCP Server と Client との通信では、MCP Server はメタデータエンドポイント（`/.well-known/oauth-protected-resource`）を通じて自身の情報を提供します。このメタデータには、リソースの識別子 (`resource`)や、このリソースのトークンを発行できる認可 Server の URI (`authorization_servers`) などが含まれます。Client はこの情報を使用して、適切な認可 Server からトークンを取得し、MCP Server にアクセスします。

___ID 02, 04, 05, 06: OAuthProtectedResourceMetadataSchema___

このスキーマは RFC9728 に準拠したリソースメタデータの構造を定義しています。必須フィールドの `resource` と `authorization_servers` が定義されていますね。

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/shared/auth.ts#L3-L23

___ID 01: mcpAuthMetadataRouter___

この`mcpAuthMetadataRouter` メソッドはリソースメタデータエンドポイントを設定する Express ルーターを提供し、[`metadataHandler`](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/handlers/metadata.ts#L6-L19
) はメタデータを提供するエンドポイントハンドラーを提供します。`/.well-known/oauth-protected-resource` エンドポイントが実装されていますね。

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/router.ts#L188-L211

___ID 03: requireBearerAuth___

このメソッドは Bearer トークン検証を実施し、401 レスポンス時に適切なヘッダーを設定します。**1/** 認可ヘッダーから提供された`verifier`を使用してトークンを検証、**2/** 401 レスポンス時に `WWW-Authenticate` ヘッダーを設定、`resourceMetadataUrl` が指定されている場合、`resource_metadata`パラメータを含める、**3/** 認証成功時に `req.auth` に [`AuthInfo`](https://github.com/modelcontextprotocol/typescript-sdk/blob/0506addf35f422650658c5e665ea184e3115a184/src/server/auth/types.ts#L4) インタフェースで定義される認証情報を追加します。

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L40
https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L67-L79

### 2. トークン検証 (OAuth 2.1 / RFC8707)

| ID | 責務詳細 | SDK対応 |
|--|----------|------------|
| 07 | ___MUST:___ OAuth 2.1: Bearer トークンの検証 | ___対応あり[メソッド]:___ `requireBearerAuth` |
| 08 | ___MUST:___ OAuth 2.1: RFC8707: トークンの `audience` 検証 | ___実装必要:___ `verifyAccessToken` |
| 09 | ___MUST:___ OAuth 2.1: トークンの有効期限確認 | ___対応あり[メソッド]:___ `requireBearerAuth` |
| 10 | ___MUST:___ OAuth 2.1: スコープの検証 | ___対応あり[メソッド]:___ `requireBearerAuth` |
| 11 | ___SHOULD:___ OAuth 2.1: リソース固有のスコープ検証 | ___対応あり[メソッド]:___ `requireBearerAuth` |
| 12 | ___MUST:___ RFC8707: トークンの不正転用防止 | ___実装必要:___ `verifyAccessToken` |
| 13 | ___MUST:___ RFC8707: リソースインジケータ対応 | ___対応あり[パラメータ]:___ `AuthorizationParams` |

トークン検証は、MCP Server がリソース Server として担う最も重要な責務の一つです。OAuth 2.1 と RFC8707 に基づき、MCP Server はクライアントから提供されたアクセストークンを検証し、適切な認可を持つ Client のみがリソースにアクセスできるようにする必要があります。

MCP Server がトークンを検証する際、**1/** Client からのリクエストに含まれる Authorization ヘッダー から Bearer トークンを抽出、**2/** トークン形式を確認し、署名検証を実施、**3/** トークンの持つ audience や scope などの重要なクレームを検証し、最終的にトークンが有効であると判断された場合にのみ、リクエストされたリソースへのアクセスを許可します。

`audience` とはトークンが使用できる対象を指定するものです。MCP 仕様でも言及されている [Confused Deputy Problem](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization#confused-deputy-problem) と呼ばれるセキュリティ問題を防ぐために重要です。この Confused Deputy Problem は複数のツールを扱う MCP Server では特に配慮が重要です。例えば、Client がツール A とツール B の両方にアクセス権限を持っているとします。Client がツール A 用に取得したトークンをツール B に提示した場合、audience 検証がなければ、ツール B はそのトークンを受け入れてしまう可能性があります。これにより、Client は意図しない形でツール B のリソースにアクセスできてしまいます。`scope` は、このトークンで何ができるのかというトークン権限範囲を確認するプロセスです。例えば、`read` スコープを持つトークンは読み取り操作のみを許可され、`write` スコープを持つトークンは書き込み操作も許可されます。

___ID 07, 09, 10, 11, 12: requireBearerAuth___

**ID07:** **1/** `verifier.verifyAccessToken(token)` を呼び出してトークンを検証、**2/** 検証に成功すると `authInfo` を取得し、`req.auth` に設定

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L53

**ID09:** **1/** `authInfo.expiresAt` が存在する場合は現在時刻と比較、**2/** 有効期限切れの場合は `InvalidTokenError` をスロー

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L67-L69

**ID10:** **1/** `requiredScopes` パラメータで必要なスコープを指定、**2/** トークンのスコープに必要なスコープがすべて含まれているか確認し、不足している場合は`InsufficientScopeError`をスロー

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L56-L63

**ID11:** **1/** `requiredScopes` パラメータでリソース固有のスコープを指定可能、異なるエンドポイントに異なるスコープ要件を設定可能

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L12-L15

___ID 08, 12: verifyAccessToken___

`verifyAccessToken` はインターフェースとして定義されており、**RFC8707 に関するトークン検証は実装者が提供する必要があります。**MCP 仕様に追従する形で今後対応実装がなされると思われますが現状はトークンの audience (`aud` クレーム) を取得して自身のリソース URI と比較するなどの対応を入れる必要があります。

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/provider.ts#L77-L82

___ID 13: AuthorizationParams___

`resource?` をパラメータに追加する必要があります。tag `1.12.1` では実装がありませんが `1.13.0` では実装が入っていました。

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/provider.ts#L6-L11

## まとめ

typescript-sdk では MCP Server がリソース Server として機能するために必要な**MCP 仕様で定義される認可に関する実装が概ね対応されている**ことが確認できました。Audience 検証は、`verifyAccessToken` メソッドの実装で実装者が対応する必要がある点には注意してください。そして、MCP 仕様で明記されている HTTPS 通信の強制や TLS 証明書検証は SDK の範囲外のため適切に対応する必要があります。