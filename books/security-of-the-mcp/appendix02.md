---
title: "§A02 MCP Auth 仕様の今後 v001: 2025/10"
free: true
---

___Appendix:___ _MCP Auth 仕様の今後_

---

本 Chapter は MCP Auth 仕様に関する今後の方向性について整理します。あくまで MCP 仕様に関する現状と今後の方向性を整理することが目的であり、個別サービスの実装依存の内容については議論しません。今後メインの Chapter に更新を毎回入れ続けるのはメンテナンスしきれないため、 Appendix に追加する形で Auth 仕様のみを追記していこうと思います。今回は MCP Auth に関して議論されている Specification Enhancement Proposal (SEP) を整理することで現状の課題と解決に関する提案をまとめます。それぞれの提案がどういう形で整理されて次期仕様になるのかは今後議論されていくでしょう。

## スコープに関する現状の課題と解決策

MCP の認可仕様は基本的なフレームワークを提供していますが、実際の運用において課題が浮かび上がってきています。

[SEP-835](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/835) としてスコープに関する議論があり、解決策が提案されています。まだ正式な仕様になっているものではありませんが今後の仕様の方向性に関しての一つの考え方として理解しておくことは有用です。

### 課題 1: スコープ選択の曖昧性

MCP Client が認可 Server にアクセストークンを要求する際、どのスコープを要求すべきかが明確ではありませんでした。スコープとは、 OAuth でアクセストークンに付与される「権限の範囲」のことです。例えば、 GitHub API では以下のようなスコープがあります。

  - `repo` - フルリポジトリアクセス
  - `public_repo` - パブリックリポジトリのみ
  - `user` - ユーザー情報アクセス
  - `read:user` - ユーザー情報読み取り専用
  - `admin:org` - 組織管理権限

現在の仕様では、 MCP Client は Protected Resource Metadata から `scopes_supported` を取得できますが、実際にどのスコープを最初に要求すべきかの優先順位が定義されていません。

```json
Protected Resource Metadata から取得できる情報：
{
  "scopes_supported": [
    "repo", 
    "public_repo", 
    "user", 
    "admin:org"
  ]
}
```

しかし、**どのスコープを最初に要求すべきか**の指針がありません。この結果、「エラーを避けるため」に利用可能なすべてのスコープを要求してしまいます。

```
❌ 悪い例：
scope = "repo public_repo user admin:org"
（すべてのスコープを要求）

✅ 良い例：
scope = "public_repo"
（最初は最小限から開始）
```

このように、最小権限の原則に反する過剰な権限付与が発生する可能性が高まります。

### 課題 2: エラーハンドリングの不一貫性

MCP Server が不十分なスコープでアクセスされた場合の対応が標準化されていません。 401 Unauthorized レスポンスには Resource Metadata が含まれますが、 403 Forbidden レスポンスには同様の情報が含まれていないため、 MCP Client が適切にスコープアップグレードを行うことが困難です。また、スコープが不足している場合の具体的な対処方法についても明確なガイダンスがありません。

### 課題 3: Client 種別による対応の違い

**OAuth フローの特性による制約の違い**

現在の MCP 仕様では、インタラクティブな認証が可能な MCP Client （authorization_code フロー）と、自動化された環境で動作する MCP Client （client_credentials フロー）を区別していませんでした。しかし、実際の運用では、これらの Client 種別は異なる制約と能力を持っており、同じ対応を期待することは現実的ではありません。

ちなみに MCP 仕様では認証は範囲外である、という文章と上記の文章に矛盾があると思われた方もいるかもしれませんが矛盾していません。**MCP 仕様の範囲外とされる認証**は、 MCP Client 自体の身元確認方法、 MCP Client がどのようにユーザーを認証するか、 MCP Server が内部的にどのように認証を行うか、などです。**MCP 仕様の範囲内とされる認可フロー内での認証**は、 OAuth 2.1 の authorization_code フローにおけるユーザー認証画面（認可 Server が提供）、 client_credentials フローにおける Client 認証（Client ID/Secret による）、認可 Server でのユーザー同意プロセス、などです。

**authorization_code フローの特性**

このフローを使用する MCP Client は以下の特徴を持ちます。

- **ユーザー介入が可能**: ブラウザを開いてユーザーに認証・同意を求めることができる
- **動的なスコープ拡張**: 実行時にスコープが不足した場合、ユーザーに追加の同意を求めて権限を拡張可能
- **柔軟なエラー回復**: 403 Forbidden エラー時に、ユーザーとのインタラクションを通じて問題を解決可能

**client_credentials フローの特性**

このフローを使用する MCP Client は以下の制約を持ちます。

- **ユーザー介入不可**: バックグラウンドで動作し、ユーザーとのインタラクションができない
- **事前設定依存**: 必要なスコープを事前に設定・取得しておく必要がある
- **限定的なエラー回復**: スコープ不足時の対処オプションが限られる

### SEP-835 による差別化された対応

SEP-835 では、Client 種別に応じた異なる対応を明確に定義しています。

- **authorization_code フロー**: スコープアップグレードを **SHOULD** （推奨）
- **client_credentials フロー**: スコープアップグレードを **MAY** （任意）

この区別により、各 Client 種別の制約と能力に応じた適切な実装指針が提供されます。

特に、 client_credentials フローを使用する Client は、ユーザーの介入なしに動作する必要があるため、インタラクティブなスコープアップグレードを行うことができません。このため、実装者は事前に適切なスコープを設定するか、エラー時の適切な処理を実装する必要があります。

### SEP-835 による解決アプローチ

これらの課題に対して、SEP-835 では以下の具体的な解決策を提案しています。この提案は、現在の仕様との後方互換性を完全に保持しながら、実装者の負担を軽減し、より安全で使いやすい認可体験を実現することを目指しています。

**優先度ベースのスコープ選択戦略**

SEP-835 では、 MCP Client がスコープを選択する際の明確な優先順位を定義しています。まず、 WWW-Authenticate ヘッダーの `scope` パラメータが優先されます。これは MCP Server が現在のリクエストに必要な具体的なスコープを直接指定できるためです。WWW-Authenticate ヘッダーにスコープ情報がない場合は、Protected Resource Metadata の `scopes_supported` をフォールバックとして使用します。

```mermaid
flowchart TD
    A[MCP Client がリソースにアクセス] --> B[401 Unauthorized レスポンス]
    B --> C{WWW-Authenticate ヘッダーに<br/>scope パラメータあり？}
    C -->|Yes| D[WWW-Authenticate の<br/>scope を使用]
    C -->|No| E[Protected Resource Metadata から<br/>scopes_supported を取得]
    D --> F[最小権限の原則に基づき<br/>必要最小限のスコープで認可要求]
    E --> F
    F --> G[認可 Server に認可要求]
    
    style D fill:#e8f5e8
    style E fill:#fff3e0
    style F fill:#e3f2fd
```

この戦略により、 MCP Client は常に必要最小限のスコープから開始し、必要に応じて段階的にスコープを拡張できるようになります。

**強化されたエラーハンドリング**

SEP-835 では、403 Forbidden レスポンスにも Resource Metadata を含めることが `SHOULD` です。これにより、MCP Client は 401 と 403 の両方のケースで一貫した方法でスコープアップグレードを実行できます。

```mermaid
sequenceDiagram
    participant Client as MCP Client
    participant Server as MCP Server
    participant AuthServer as 認可 Server
    
    Note over Client, Server: 初回アクセス（限定スコープ）
    Client->>Server: リクエスト（read スコープのみ）
    Server-->>Client: 200 OK（成功）
    
    Note over Client, Server: 追加権限が必要な操作
    Client->>Server: リクエスト（write 権限が必要）
    Server-->>Client: 403 Forbidden<br/>WWW-Authenticate: Bearer scope="read write"<br/>resource-metadata="https://server.example.com/.well-known/oauth-protected-resource"
    
    Note over Client: スコープアップグレード判断
    alt インタラクティブ Client
        Client->>AuthServer: 拡張スコープで再認可要求
        AuthServer-->>Client: 新しいアクセストークン
        Client->>Server: リクエスト（新しいトークン）
        Server-->>Client: 200 OK（成功）
    else Client Credentials Client
        Note over Client: 自動アップグレード試行 or<br/>エラー報告（実装依存）
    end
```

**Step-Up Authorization Flow**

SEP-835 では、実行時にスコープが不足した場合の Step-Up Authorization Flow が詳細に定義されています。このフローでは、Client が既にアクセストークンを持っているものの追加の権限が必要な場合に、Server は HTTP 403 Forbidden ステータスコードと `WWW-Authenticate` ヘッダーを返し、error="insufficient_scope" パラメータと必要なスコープを `scope` パラメータで指定します。Client はこのエラー情報を解析し、必要スコープを決定して決定されたスコープセットで再認証を開始し、新しい認証で元のリクエストを再試行します。ただし、Client はリトライ制限を実装し、同じリソースと操作の組み合わせでのスコープアップグレード試行を追跡して、繰り返し失敗を避ける必要があります。

** Client 種別に応じた差別化対応**

SEP-835 では、Client 種別に応じた異なる対応を明確に定義しています。

- **authorization_code フロー**: スコープアップグレードを **SHOULD**（推奨）として定義します。ユーザーの介入が可能なため、積極的にスコープアップグレードを試行することが期待されます。

- **client_credentials フロー**: スコープアップグレードを **MAY**（任意）として定義します。自動化環境での制約を考慮し、実装者の判断でアップグレードを試行するか、即座にエラーを報告するかを選択できます。

```mermaid
graph TD
    A[スコープ不足エラー発生] --> B{Client 種別}
    B -->|authorization_code<br/>フロー| C[SHOULD: スコープアップグレード試行]
    B -->|client_credentials<br/>フロー| D[MAY: 実装者判断]
    
    C --> E[ユーザー認証画面表示]
    E --> F[拡張スコープで再認可]
    F --> G[新しいアクセストークン取得]
    
    D --> H{実装方針}
    H -->|アップグレード| I[自動的にスコープ拡張要求]
    H -->|エラー報告| J[アプリケーションにエラー通知]
    
    I --> K[認可 Server に要求]
    K --> L{認可成功？}
    L -->|Yes| G
    L -->|No| J
    
    style C fill:#e8f5e8
    style D fill:#fff3e0
    style G fill:#e3f2fd
    style J fill:#ffebee
```

**セキュリティ強化とベストプラクティス**

SEP-835 では、新しい「スコープ最小化」セクションを追加し、以下のセキュリティベストプラクティスを明文化しています。

- **段階的権限拡張**: 最小限のスコープから開始し、必要に応じて段階的に権限を拡張する
- **リトライ制限**: 無限ループを防ぐためのリトライ回数制限の実装
- **キャッシュ管理**: 認証状態の適切なキャッシュ管理とセキュリティ考慮事項
- **監査ログ**: スコープアップグレードの記録と監査証跡の維持
- **PKCE 検証の強化**: MCP Client は認証開始前の PKCE サポート検証が必須となり、サポートが確認できない場合は認証を拒否する

### 実装における実践的な改善

SEP-835 の提案により、実装者は以下の具体的な改善を期待できます。

**開発者体験の向上**

従来は各 MCP Server に対してどのスコープを要求すべきかを事前に調査し、設定する必要がありましたが、 SEP-835 により、MCP Client は自動的に適切なスコープを判断し、必要に応じて段階的に権限を拡張できるようになります。これにより開発者は認可の詳細を意識することなく、MCP Server の機能に集中できます。

**セキュリティの向上**

最小権限の原則が自動的に適用されることで、過剰な権限付与によるセキュリティリスクが大幅に軽減されます。また、明確なエラーハンドリングにより、認可に関する問題の診断と解決が容易になります。

**運用の簡素化**

Client 種別に応じた適切な対応により、様々な環境での MCP 導入が容易になります。特に、自動化された環境での client_credentials フローの扱いが明確になることで、企業環境での MCP 採用が促進されることが期待されます。

## Client Registration の進化と新しいセキュリティアプローチ

MCP の認可において、Dynamic Client Registration （DCR）に関する課題が議論されています。これらの課題に対して具体的な解決策が提案されており、 2025 年 8 月の [MCP 公式ブログ記事「 Evolving OAuth Client Registration in the Model Context Protocol」](https://blog.modelcontextprotocol.io/posts/client_registration/) では、 Client Registration の進化について詳細に解説されています。

[MCP 公式仕様（2025-06-18 版）](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)では、DCR をサポートしない認可 Server に対して、**MCP Client が必ず代替手段を提供しなければならない**と明確に規定しています。

> **MCP clients and authorization servers SHOULD support the OAuth 2.0 Dynamic Client Registration Protocol RFC7591** to allow MCP clients to obtain OAuth client IDs without user interaction.
> 
> **Any authorization servers that do not support Dynamic Client Registration need to provide alternative ways to obtain a client ID** (and, if applicable, client credentials). For one of these authorization servers, **MCP clients will have to either:**
> 
> 1. **Hardcode a client ID** (and, if applicable, client credentials) specifically for the MCP client to use when interacting with that authorization server, or
> 2. **Present a UI to users** that allows them to enter these details, after registering an OAuth client themselves (e.g., through a configuration interface hosted by the server).

DCR のみに対応する Client 実装があるとします。 MCP 仕様では DCR は **SHOULD** であり **MUST** ではないにもかかわらず DCR のみに対応する場合、DCR が必要なユースケース以外でのユースケースの利便性が損なわれるため市場競争力を失う可能性があります。

### SEP-991 で議論されている DCR の具体的な課題

[SEP-991: Enable URL-based Client Registration using OAuth Client ID Metadata Documents(CIMD)](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/991) では、現在の DCR アプローチが抱える以下の具体的な課題が詳細に分析されています。

MCP は USB-C のようなプラグアンドプレイ体験であり、そのために手動設定などをすることなく「端子に刺せば使える」ような体験を提供することに重要な価値があります。しかし現在のアプローチにはいくつかの課題があります。

**課題 1: 事前設定なしの動的接続が事実上困難**

MCP が目指すプラグアンドプレイ体験が、現在の方式では事実上困難であることです。Client 開発者は存在するすべての Server を事前に知ることができないため事前登録は非現実的であり、その結果ユーザーは各 Server ごとに複雑な認可設定を手動で行う負担を強いられます。一方で、この問題を解決するための DCR は、Server 側に無制限のデータベース管理、有効期限処理、信頼性の検証困難な自己申告メタデータの判断という重い運用負荷を課します。つまり、MCP の核心的価値である「プラグアンドプレイ」体験が、認可の実装コストによって実現が困難になっています。

**課題 2: DCR の運用負荷と信頼性の問題**

DCR は理論的には動的接続の解決策に見えるものの、実際には Server 側に持続不可能な負荷を課し、根本的な信頼性の問題を抱えています。Server は制限のない数の Client 情報を永続的に管理し、有効期限とクリーンアップの複雑な運用を継続する必要がある一方で、Client が自己申告するメタデータの真正性を検証する技術的手段が存在しないため、悪意のある Client による偽装や大量登録攻撃に対して脆弱になります。さらに、現在の仕組みは Client が Server を信頼するメカニズムは提供するものの、Server が Client を信頼するための十分な仕組みが欠如しており、ドメインベースやオリジンベースの信頼モデルも存在しないため、結局は事前調整や手動での信頼関係構築が必要となり、MCP の動的接続という理念を実現しきれない構造となっています。

**課題 3: MCP エコシステムの特性との不整合**

MCP エコシステムは従来の OAuth モデルが想定する構造とは根本的に異なる特性を持っており、既存の認証フレームワークでは適切に対応できません。従来の OAuth は、Google 、Facebook 、Twitter といった少数の大手 API プロバイダーに対して多数のアプリケーションがアクセスするという構造（Few Servers, Many Clients）を前提としており、事前の開発者登録と静的な信頼関係の構築が一般的でした。しかし MCP では Web ブラウザと Web サイトの関係と同様に、少数の Client に対して圧倒的に多数の Server が存在する構造（Many Servers, Few Clients）を持ち、ユーザーが日常的に新しい Server を発見して動的に接続することが前提となっています。この環境では、Server 開発者は自身のリソースにアクセスできる Client を信頼できる限定されたセットに制限したいというニーズを持つ一方で、従来の OAuth の静的登録モデルでは MCP の動的で大規模な Server エコシステムに構造的に適合せず、認証・認可の複雑性が指数関数的に増大してしまいます。

### SEP-991: 課題に対する解決提案

これらの課題に対して、SEP-991 として、DCR の問題を解決する新しいアプローチが提案されています。

**SEP-991 のアプローチ**

SEP-991 では、DCR を **MAY** （任意）に格下げし、**Client ID Metadata Documents** を **SHOULD**（推奨）に格上げすることを提案しています。この新しい方式では、HTTPS URL を Client ID として使用し、その URL で JSON メタデータドキュメントを提供します。

```mermaid
sequenceDiagram
    participant User as ユーザー
    participant Client as MCP Client
    participant Server as 認可 Server
    participant Metadata as Metadata Endpoint<br/>(Client の HTTPS URL)
    participant Resource as MCP Server

    Note over Client,Metadata: Client がメタデータを<br/>https://app.example.com/oauth/metadata.json でホスト

    User->>Client: MCP Server への接続開始
    Client->>Server: 認可リクエスト<br/>client_id=https://app.example.com/oauth/metadata.json<br/>redirect_uri=http://localhost:3000/callback

    Note over Server: ユーザー認証

    Note over Server: URL 形式の client_id を検出

    Server->>Metadata: GET https://app.example.com/oauth/metadata.json
    Metadata-->>Server: JSON メタデータドキュメント<br/>{client_id, client_name, redirect_uris, ...}

    Note over Server: 検証：<br/>1. client_id が URL と一致<br/>2. redirect_uri が許可リストに含まれる<br/>3. ドキュメント構造が有効<br/>4. ドメインが信頼ポリシーで許可

    alt 検証成功
        Server->>User: client_name を表示した同意画面
        User->>Server: アクセス承認
        Server->>Client: redirect_uri 経由で認可コード
        Client->>Server: コードをトークンに交換<br/>client_id=https://app.example.com/oauth/metadata.json
        Server-->>Client: アクセストークン
        Client->>Resource: アクセストークン付き MCP リクエスト
        Resource-->>Client: MCP レスポンス
    else 検証失敗
        Server->>User: エラーレスポンス<br/>error=invalid_client または invalid_request
    end

    Note over Server: 将来のリクエストのために<br/>メタデータをキャッシュ（HTTP キャッシュヘッダーに従う）
```

**Client ID Metadata Documents がどう課題を解決するのか**

Client ID Metadata Documents は、前述の 3 つの課題を解決します。まず課題 1 に対しては、 Client が HTTPS URL でメタデータドキュメントを公開するだけで、 Server 側での事前登録が一切不要になることで動的接続が実現されます。これにより Client 開発者は登録する Server を事前に知る必要がなくなり、ユーザーも複雑な手動設定の負担から解放されます。課題 2 については、 Server が動的登録データベースを管理する必要がなくなり、有効期限管理やクリーンアップといった複雑な運用タスクが不要になる一方で、HTTPS URL という検証可能なアイデンティティにより Client の真正性を技術的に確認できるようになります。そして課題 3 に対しては、Server が独自の信頼基準（ドメイン許可リスト等）を柔軟に設定できるドメインベースの信頼モデルにより、Many Servers, Few Clients という MCP 特有の構造に適合した認可が可能になり、従来の OAuth の静的登録モデルの制約を克服できます。

**メタデータドキュメントの例**

```json
{
  "client_id": "https://app.example.com/oauth/client-metadata.json",
  "client_name": "Example MCP Client",
  "client_uri": "https://app.example.com",
  "logo_uri": "https://app.example.com/logo.png",
  "redirect_uris": [
    "http://127.0.0.1:3000/callback",
    "http://localhost:3000/callback"
  ],
  "grant_types": ["authorization_code"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
```

### Software Statements によるデスクトップアプリケーションのセキュリティ強化

ブログ記事では、Client ID Metadata Documents と組み合わせて使用できる **Software Statements** という新しいセキュリティメカニズムが提案されています。これは、デスクトップアプリケーションにおける Client 偽装攻撃を防ぐための重要な技術です。

**Trust Spectrum の概念**

ブログ記事では、偽装攻撃のコストと緩和策の複雑性を 2 つの軸で整理した「Trust Spectrum」が紹介されています。

- **低攻撃コスト/低緩和複雑性**: ドメインベース攻撃
  - 攻撃：悪意のあるコールバック URI を登録し、「Claude Desktop」を名乗る
  - 緩和策：信頼できるドメイン/URL の制限、未知ドメインへの警告表示
  - CIMD と DCR の両方で対応可能

- **中攻撃コスト/中緩和複雑性**: `localhost` 偽装攻撃
  - 攻撃：`localhost:8080` で悪意のあるアプリを実行し、正当な Client を偽装
  - 問題：デスクトップアプリは秘密を保持できず、身元証明が困難

- **高攻撃コスト/高緩和複雑性**: プラットフォーム証明アプリケーション
  - 攻撃：信頼できる認証機関によって署名された悪意のある Client
  - 緩和策：プラットフォームシステムレベルの証明（将来の課題）

**Software Statements の実装**

`localhost` 偽装攻撃を防ぐため、 Software Statements では以下の仕組みを提案しています。

1. **Client のバックエンドサービスでの JWKS ホスティング**
2. **Client 自身の認証フローを通じたユーザー認証**
3. **Client 所有のバックエンドサービスによる短期間の署名付き JWT 発行**
4. **OAuth フローでの JWT 含有**
5. **認可 Server による信頼できる JWKS に対する JWT 検証**

この仕組みにより、攻撃者は以下のいずれかを実行する必要があり、攻撃コストが大幅に増加します。

- Client のバックエンドインフラストラクチャの侵害
- Client の認証フローの偽装成功

**重要な点として、 Software Statements は DCR と CIMD の両方で使用可能**であり、競合する解決策ではなく相補的なセキュリティレイヤーとして機能します。

### セキュリティ考慮事項の具体化

ブログ記事では、 CIMD と Software Statements の実装における具体的なセキュリティ要件が明確化されています。

**CIMD 実装でのセキュリティ要件**

認可 Server が HTTPS URL からメタデータを取得する際、以下の対策が必須です。

- **SSRF 攻撃防止**: 内部ネットワークアクセスのブロック
- **タイムアウトとサイズ制限**: DoS 攻撃防止のための制限実装
- **レスポンス形式の厳密検証**: 悪意のあるメタデータによる攻撃防止
- **キャッシュ戦略**: パフォーマンス向上とセキュリティ考慮事項のバランス

**相補的アプローチの明確化**

ブログ記事では、これらの技術が相互に排他的ではなく、相補的に機能することが強調されています。

- **DCR の後方互換性維持**: 既存実装への影響を最小化
- **CIMD の新規実装推奨**: 運用負荷軽減のための推奨アプローチ
- **Software Statements の重ね合わせ**: 必要に応じたセキュリティ強化

この段階的なアプローチにより、実装者は自身の要件とリスク許容度に応じて適切なセキュリティレベルを選択できます。

## MCP に適した新しい Auth メカニズムの提案

> Dick Hardt: **I concluded that for a number of use cases, OAuth is not a good fit between the MCP Client and MCP Server.**

[SEP-1299](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1299) では、「OAuth は MCP Client と MCP Server 間でうまく適合していない」という言及があります。

OAuth と MCP の間には根本的な設計思想の違いが存在しています。 OAuth は事前登録された Client、既知のスコープと認可 Server、そして Web ブラウザベースの認可フローを前提として設計されています。一方で、MCP の現実は動的に発見される Client・Server 関係、未知のスコープと認可要件、そして CLI や音声インターフェースなどの多様な Client 環境という、OAuth の前提とは大きく異なる環境で運用されます。

この不適合性は、Dick Hardt が Hellō Admin APIs 用の MCP Server を実装した実体験によって明確に示されています。彼は MCP Authorization Specification に従おうとしましたが、OAuth の設計前提と MCP の現実との間にある根本的な齟齬により、結果的にその精神を回避せざるを得ませんでした。具体的には、無数の未知の Client を個別登録することが不可能なため常に同じ client_id を返し、Client が事前に必要なスコープを知ることができないため mcp スコープのみを許可し、CLI や音声インターフェースなど多様な実行環境に対応するため任意の redirect_uri を許可し、さらには Client 側でのトークン漏洩リスクを最小化するためリフレッシュトークンを無効化するという対応を取らざるを得ませんでした。

これらの回避策が必要だったという事実は、OAuth と MCP の根本的な不適合性を実証すると彼は考えています。つまり、OAuth を MCP に適用しようとすると、OAuth の本来の設計思想を歪めざるを得ず、結果的にセキュリティと使いやすさの両方を損なうことになります。この経験が、SEP-1299 において新しい認証・認可メカニズムの必要性を主張する根拠となっています。

### SEP-1299 で議論されている OAuth の具体的な課題

SEP-1299 では、現在の OAuth ベースの MCP 認証が抱える 7 つの根本的な課題が詳細に分析されています。

**課題 1: OAuth が Web ベースプロトコルであること**

OAuth は Web ブラウザでの認証フローを前提として設計されているため、音声インターフェースや CLI ツールなど、Web UI を持たないアプリケーションでは適切に機能しません。MCP の価値は多様なインターフェースでの AI エージェント利用にあるため、この制約は重大な障壁となります。

**課題 2: OAuth スコープの粗粒度アクセス制御**

OAuth のスコープは比較的粗い粒度でのアクセス制御しか提供できず、多くの企業アプリケーションが必要とする細かい権限制御やポリシーエンジンベースの動的アクセス制御には適していません。特に、ユーザーの役職、部門、プロジェクト参加状況などの文脈に基づく複雑な認可判定が困難です。

**課題 3: Dynamic Client Registration の実装困難性**

DCR は OAuth の標準的な実装パターンではなく、多くの既存の OAuth インフラストラクチャでサポートされていません。また、DCR の「オープンで認証不要な登録」という信頼モデルは、実際の企業環境での運用には適さず、セキュリティリスクを生じさせます。

**課題 4: 信頼できない Redirect URI の問題**

OAuth では通常、認可 Server が Client を識別する重要な手段として HTTPS の redirect URI を使用します。例えば `https://myapp.example.com/callback` のような HTTPS URL は、その Client が `myapp.example.com` ドメインを制御していることを証明し、認可 Server はこのドメインの信頼性に基づいて Client を識別できます。しかし MCP では、デスクトップアプリケーションや CLI ツールが多いため、 `http://localhost:3000/callback` や `myapp://oauth/callback` のような localhost や custom scheme を使用せざるを得ません。localhost は誰でも使用でき、custom scheme は複数のアプリケーションが同じスキームを登録できるため、認可 Server は Client の真の身元を確認できません。これにより、悪意のあるアプリケーションが正当な Client と同じ redirect URI を主張し、ユーザーが認可した際の認可コードを傍受する攻撃が可能になります。つまり、OAuth の基本的なセキュリティ前提である「redirect URI による Client 識別」が MCP 環境では機能しないという根本的な問題があります。

**課題 5: 単一認可メカニズムの制約**

現在の仕様では、MCP Server がユーザーと直接やり取りする唯一の方法は 401 レスポンスを返すことだけです。これにより、複数の認可 Server からアクセストークンを取得することや、下流の Server が独自のアクセストークンを取得することができず、より複雑な認可チェーンの構築が困難になります。

**課題 6: 実装の複雑性**

すべての MCP Client と Server の開発者が、OAuth の発見、登録、トークン管理を実装する必要があり、開発負荷が高くなります。また、相互運用性を確保するために、すべての Client がすべての拡張機能を実装する必要があります。

**課題 7: Bearer Token によるセキュリティリスク**

MCP-Session-Id と OAuth アクセストークンの両方が Bearer Token であり、どちらも盗取可能です。 Client の身元を信頼できるソースがなく、ユーザーの身元も単一のアクセストークンによって抽象化されているため、高価値な企業環境での採用には不十分なセキュリティレベルです。

### SEP-1299: 課題に対する解決提案

これらの課題に対して、SEP-1299 では OAuth 認可フロー管理を MCP Client から MCP Server に移行する根本的なアーキテクチャ変更を提案しています。

**提案 1: 暗号学的セッションバインディング**

MCP Client が Server との関係を開始する際に鍵ペアを生成し、秘密鍵の所有を証明して公開鍵を Server と共有します。その後のすべてのリクエストで秘密鍵の所有を証明することで、Server は OAuth アクセストークンではなく公開鍵を使ってユーザーとセッションのコンテキストを追跡できます。この仕組みには HTTP Message Signatures (RFC 9421) を使用します。

**提案 2: Server 主導のインタラクション要求**

Server はいつでも Client に対してユーザーとの直接インタラクションを要求できます。このインタラクション要求は MCP リクエストへのレスポンスとして、または SSE イベントとして送信できます。 Web インタラクションの場合、説明文と URL が含まれ、この URL は Server または Server の委任先に向けられます。

```mermaid
sequenceDiagram
    participant Client as MCP Client
    participant Server as MCP Server
    participant AS as 認可 Server
    participant Browser as ユーザーブラウザ
    participant RS as リソース Server

    Note over Client, Server: HTTP Signature による暗号学的バインディング

    Client->>Server: MCP リクエスト（HTTP Signature 付き）
    Server-->>Client: インタラクション要求 SSE

    Client->>Browser: インタラクション URL を開く

    Note over Server, Browser: Server 主導の OAuth 2.1 認可フロー
    Server-->>Browser: 認可 Server へリダイレクト
    Browser->>AS: 認可リクエスト
    AS-->>Browser: 認可コード
    Browser-->>Server: セッションバインディング付き認可コード
    
    Server->>AS: コードをトークンに交換
    AS-->>Server: アクセストークン

    Note over Server: 認可完了、Client に通知
    Server->>Client: 完了エンドポイントへ POST (id=request_id)
    
    Note over Client: 元のリクエストを再実行
    Client->>Server: MCP リクエスト（HTTP Signature 付き、リトライ）
    
    Note over Server: 認可済み、リクエスト完了可能
    Server->>RS: アクセストークン付きリクエスト
    RS-->>Server: リソースレスポンス
    Server-->>Client: MCP レスポンス
```

**Server 側認可管理の利点**

この新しいアプローチにより、以下の利点が実現されます。既存の OAuth フローとの後方互換性を保ちながら、 Client 側でのアクセストークンとリフレッシュトークンの管理が不要になり、トークン漏洩リスクが軽減されます。 MCP Server は事前登録された OAuth Client として動作でき、HTTPS redirect URI と Client Secret の適切な管理が可能になります。Client の実装が大幅に簡素化され、OAuth フロー、DCR、Server 発見、機密 Bearer Token の管理が不要になります。Server は既存のリソース用アクセス管理システムをそのまま使用でき、 DCR や OAuth 発見エンドポイントの展開が不要になります。企業は既存の認可メカニズム上に MCP Server を構築でき、細かい粒度のアクセス制御も実現できます。Server はいつでも認可を要求でき、段階的認証や段階的同意をサポートし、重要な操作で人間をループに含めることができます。Server は操作の機密性、Client の状態、IP 変更などの文脈的要因に基づいて認可判定を行い、コンテキストを失うことなく追加の認可や認証を要求できます。そして、Server は異なるバックエンドリソース用の複数のアクセストークンを管理し、Server 階層を通じて認可リクエストをチェーンできます。

### HTTP Message Signatures による実装詳細

SEP-1299 では、セキュリティと相互運用性のために HTTP Message Signatures の具体的なプロファイルを定義しています。

**鍵管理**

Client は MCP Server ごとに Ed25519 を使用して公開鍵・秘密鍵ペアを生成します。keyid は RFC 7638 に従った JWK thumbprint を使用し、MCP Server はこの keyid を使って Client インスタンスを識別します。

**Client 初期化**

Client は initialize リクエストの clientInfo に public_key と任意の interaction_completion_uri を含めます。

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "clientInfo": {
      "public_key": {
          "kty": "OKP",
          "crv": "Ed25519",
          "x": "base64url-encoded-public-key"
      },
      "interaction_completion_uri": "http://localhost:XXXX"
    }
  }
}
```

### SEP-1299 による課題解決の詳細分析

SEP-1299 の 2 つの提案が、上記 7 つの課題をどのように解決するかを以下の表で整理します。

| 課題 | 現在の OAuth での問題 | 提案 1: 暗号学的セッションバインディング | 提案 2: Server 主導のインタラクション要求 |
|------|---------------------|----------------------------------------|----------------------------------------|
| **課題 1: Web ベースプロトコル** | Web ブラウザでの認証フローが前提で、CLI や音声インターフェースに対応困難 | HTTP Message Signatures により、Web UI に依存しない認証が可能 | Server が適切なインターフェース（ Web 、 CLI 、音声等）を選択してインタラクション要求を送信可能 |
| **課題 2: 粗粒度アクセス制御** | OAuth スコープでは細かい権限制御やポリシーエンジンベースの制御が困難 | 公開鍵による Client 識別により、Server 側で文脈に応じた細かい権限判定が可能 | 操作ごとに動的にユーザー確認を求めることで、リアルタイムな権限制御を実現 |
| **課題 3: DCR 実装困難性** | DCR のサポート状況が限定的で、企業環境での運用に課題 | Client 登録が不要になり、公開鍵による識別で DCR の必要性を排除 | Server 側で認可フローを管理するため、Client 側での DCR 実装が不要 |
| **課題 4: 信頼できない Redirect URI** | localhost や custom scheme では Client の真の身元を確認できない | 暗号学的証明により Client の身元を確実に識別、redirect URI に依存しない | Server が信頼できる HTTPS エンドポイントで認可フローを管理、redirect URI の問題を回避 |
| **課題 5: 単一認可メカニズム** | 401 レスポンスのみで複数認可 Server や認可チェーンの構築が困難 | セッションバインディングにより複数の認可コンテキストを管理可能（Client の公開鍵 = セッション識別子として機能）| Server がいつでもインタラクションを要求でき、複数の認可フローや段階的認証を実現 |
| **課題 6: 実装の複雑性** | Client・Server 双方で OAuth の発見、登録、トークン管理の実装が必要 | Client は鍵ペア生成と署名のみ実装すれば良く、OAuth 関連の複雑な実装が不要 | Server 側で認可フローを一元管理するため、Client の実装負荷を大幅に軽減 |
| **課題 7: Bearer Token リスク** | アクセストークンと Session ID の両方が盗取可能で、セキュリティリスクが高い | 秘密鍵による proof-of-possession で盗取不可能な認証を実現 | Server 側でトークンを管理するため、Client 側でのトークン漏洩リスクを排除 |

この表が示すように、 SEP-1299 の 2 つの提案は相互に補完し合いながら、 OAuth の根本的な課題を包括的に解決します。提案 1 は技術的な認証基盤を提供し、提案 2 は柔軟な認可フローを実現することで、 MCP に最適化された新しい認証・認可メカニズムを構築しています。

### SEP-1299 に関する筆者の所管

***以降はあくまで筆者の一意見であるため参考程度に読んでください***

SEP-1299 は技術的に優れた解決策を提示している一方で、MCP Server の実装責務を大幅に増大させるという課題を抱えています。この複雑化は MCP の価値である気軽に Server を作成・公開・利用できる体験を損なう可能性があり、エコシステムの成長を阻害するリスクを内包していると考えます。

現在の業界動向を見ると、認証・認可を Gateway レイヤーで集約管理するデザインパターンが採用され初めています。このような環境下では、仕様が Gateway ベースのアーキテクチャとどのように調和すべきかという設計思想の検討が不可欠でしょう。

長期的な展望として、Web ブラウザが複雑な HTTP 通信を抽象化して Web サイト開発を簡素化したように、MCP Gateway レイヤーが認証・認可の複雑性を吸収し、背後の API Server は標準化されたフレームワークに基づいて構築される形態が主流になると予想しています。この進化により、開発者が直接的に MCP Server を実装する必要性は減少し、より高レベルな抽象化されたツールチェーンを通じて MCP エコシステムに参加する形態が一般化するのではないでしょうか。

**MCP Server の実装難易度の増大について**

SEP-1299 が提案する新しい認証メカニズムは、従来の OAuth ベースの実装と比較して MCP Server 側の実装負荷を増加させます。まず、HTTP Message Signatures の検証機能を実装する必要があり、これには RFC 9421 の仕様に準拠した署名検証ライブラリの統合、Ed25519 暗号化アルゴリズムのサポート、タイムスタンプベースのリプレイ攻撃防止機能、そして JWK thumbprint による Client 識別機能の実装が含まれます。これらの暗号学的機能は、従来の Bearer Token 検証よりもはるかに複雑であり、セキュリティ上の実装ミスが致命的な脆弱性につながるリスクを抱えています。

さらに、Server 主導のインタラクション要求機能を実装するためには、SSE による非同期通信の管理、ユーザーブラウザとの認可フロー制御、複数の認可 Server との連携機能、そして段階的認証や段階的同意のワークフロー管理といった、従来の MCP Server では不要だった複雑な状態管理機能を新たに開発する必要があります。特に、Client の公開鍵とセッション状態を永続化し、複数のリクエスト間で一貫性を保つためのセッション管理機能は、従来のステートレスな MCP Server 実装とは根本的に異なるアーキテクチャを要求し、これらは Gateway レイヤやクラウド等のインフラストラクチャレベルで担うのが適切でしょう。

広報互換性を保ちながら仕様策定が進むことを考えると、エンタープライズグレードの機能が必要のないケースでは SEP-1299 は Too much であり、既存仕様のままでも問題がないことを考えると、エンタープライズ向けでの `SHOULD` 要件などが新たに追加されるかもしれませんね。SEP-1299 の作者が [SEP-1415（HTTP Message Signatures のみ）](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1415) を別途切り出している点は、エンタープライズ仕様の段階的導入の現実的な必要性を認識しているからではないでしょうか。

**Gateway パターンにおける考慮事項**

企業環境において MCP を導入する際、Gateway パターンの採用は一般的になりつつありますが、SEP-1299 の暗号学的バインディング要件は従来の OAuth ベースの Gateway 実装とは異なる考慮事項を要求します。従来の OAuth 実装では、Gateway が上流の Client から受け取ったアクセストークンを下流の各 MCP Server に転送するか、または Gateway 自身が各 Server 用のアクセストークンを取得して管理するという比較的単純なモデルで対応できました。しかし SEP-1299 では、Client の秘密鍵による HTTP Message Signatures が前提となるため、Gateway が Client の秘密鍵を持たない限り、下流 Server への署名付きリクエストを代理で生成することができません。

この課題を解決するにはレイヤード認証モデルの採用が良いかもしれません。このモデルでは、Client と Gateway 間では SEP-1299 による強固な暗号学的認証を実装し、Gateway と MCP Server 間では信頼境界内での効率的な Identity 伝播を行い、MCP Server と外部 API 間ではゼロトラストの原則に基づく Token Exchange を実装します。

```mermaid
graph TB
    subgraph "Trust Boundary"
        direction TB
        Gateway[MCP Gateway]
        Server[MCP Server]
        Gateway -.->|"mTLS/JWT<br/>Identity Propagation"| Server
    end
    
    Client[MCP Client]
    IdP[Enterprise IdP]
    ExtAPI[External API]
    ExtAuth[External Auth Server]
    
    Client -->|"1. HTTP Message Signatures<br/>(SEP-1299)"| Gateway
    Gateway -.->|"2. Token Validation"| IdP
    Server -->|"3. Token Exchange<br/>(Scope Down)"| ExtAuth
    Server -->|"4. Scoped Token"| ExtAPI
    
    classDef trustBoundary fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    classDef external fill:#fff3e0,stroke:#e65100,stroke-width:2px
    classDef client fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    
    class Gateway,Server trustBoundary
    class ExtAPI,ExtAuth,IdP external
    class Client client
```

具体的には、Client は HTTP Message Signatures を使用して Gateway に対して認証を行い、Gateway はこの署名を検証して Client の身元を確認します。Gateway は検証済みの Client 情報とリクエストコンテキストを、mTLS や JWT などの信頼境界内で適切な認証方式を使用して MCP Server に伝達します。この段階では、Gateway と MCP Server が同一の信頼境界内に存在することを前提として、パフォーマンスを重視した Identity 伝播を行います。

```mermaid
sequenceDiagram
    participant C as MCP Client
    participant G as MCP Gateway
    participant S as MCP Server
    participant IdP as Enterprise IdP
    participant ExtAuth as External Auth Server
    participant ExtAPI as External API
    
    Note over C,G: Layer 1: Strong Cryptographic Authentication
    C->>G: HTTP Request with Message Signature (SEP-1299)
    G->>G: Verify Ed25519 Signature
    G->>IdP: Validate Client Identity (Optional)
    IdP-->>G: Identity Confirmation
    
    Note over G,S: Layer 2: Trust Boundary Identity Propagation
    G->>S: Forward Request with Identity Claims (mTLS/JWT)
    Note over G,S: Efficient communication within trust boundary
    
    Note over S,ExtAPI: Layer 3: Zero Trust Token Exchange
    S->>ExtAuth: Token Exchange Request (RFC 8693)
    Note over S,ExtAuth: Exchange: Gateway token → Scoped API token
    ExtAuth-->>S: Scoped Access Token (Minimal Privileges)
    
    S->>ExtAPI: API Request with Scoped Token
    ExtAPI-->>S: API Response
    
    S-->>G: MCP Response
    G-->>C: Final Response
```

MCP Server が外部 API にアクセスする必要がある場合、Token Exchange の仕組みを活用します。Token Exchange とは、既存のトークンを別のトークンに交換する OAuth 2.0 の拡張仕様であり、異なるセキュリティドメイン間でのアクセス権限の適切な委譲を可能にします。MCP Server は Gateway から受け取った Identity 情報を基に、外部 API の認証 Server に対して新しいアクセストークンを要求します。この際、最小権限の原則に従ってスコープをダウンし、外部 API へのアクセスに必要最小限の権限のみを含むトークンを取得します。

```mermaid
sequenceDiagram
    participant S as MCP Server
    participant AS as External Auth Server
    participant API as External API
    
    Note over S,AS: RFC 8693 Token Exchange Protocol
    
    S->>AS: POST /token
    Note over S,AS: grant_type=urn:ietf:params:oauth:grant-type:token-exchange<br/>subject_token=<gateway_token><br/>requested_token_type=urn:ietf:params:oauth:token-type:access_token<br/>scope=<minimal_required_scope>
    
    AS->>AS: Validate Subject Token
    AS->>AS: Apply Scope Reduction
    AS->>AS: Generate Scoped Token
    
    AS-->>S: Token Exchange Response
    Note over AS,S: access_token=<scoped_token><br/>token_type=Bearer<br/>expires_in=3600<br/>scope=<reduced_scope>
    
    S->>API: API Request with Scoped Token
    Note over S,API: Authorization: Bearer <scoped_token>
    
    API->>API: Validate Scoped Token
    API-->>S: API Response (Limited Access)
```

このアーキテクチャの利点は、各層で最適な認証方式を選択できることです。Client-Gateway 間では SEP-1299 の暗号学的保証により強固なセキュリティを実現し、Gateway-Server 間では信頼境界内での効率的な通信を維持し、Server-外部 API 間では Token Exchange によるゼロトラストセキュリティを実装できます。また、既存の企業インフラストラクチャとの統合も容易になり、Gateway レイヤーで認証・認可の複雑性を吸収することで、MCP Server の実装負荷を軽減できます。

このレイヤード認証モデルにより、SEP-1299 の技術的優位性を活かしながら、企業環境での実用性と Gateway パターンとの整合性を両立することが可能になります。特に、信頼境界の適切な設計により、セキュリティとパフォーマンスのバランスを取りながら、スケーラブルで運用しやすい MCP エコシステムを構築できるでしょう。

これらの技術的課題を考慮すると、SEP-1299 の採用は Gateway パターンを採用する企業環境において、既存のインフラストラクチャの大幅な再設計を要求する可能性があります。特に、既存の OAuth ベースの認証・認可システムとの互換性を維持しながら新しいメカニズムを段階的に導入するための移行戦略の策定が重要になるでしょう。

**Christian Posta の代替提案との比較**

Christian Posta は[彼のブログ記事](https://blog.christianposta.com/the-updated-mcp-oauth-spec-is-a-mess/)において、SEP-1299 とは異なるアプローチを提案しています。Posta の提案は、既存の企業インフラストラクチャを最大限活用することに重点を置いており、MCP Client から API Gateway、そして MCP Server へと続く階層的なアーキテクチャを採用しています。この構成では、API Gateway が Enterprise IdP と連携して認証・認可の集約管理を行い、MCP Server は純粋な Resource Server として位置づけられます。

> SEP-1299

```mermaid
graph TB
    Client2[MCP Client]
    Server2[MCP Server]
    AS[認可 Server]
    Browser[ユーザーブラウザ]
    Resources2[External Resources]
    
    Client2 -->|HTTP Signatures<br/>暗号学的バインディング| Server2
    Server2 <-->|Server 主導認可| AS
    Server2 <-->|インタラクション要求| Browser
    Server2 -->|直接アクセス| Resources2
    Client2 -.->|公開鍵共有| Server2
    Server2 -.->|セッション管理| Server2
    
    style Server2 fill:#e3f2fd
    style AS fill:#ffebee
```

> Posta

```mermaid
graph TB
    Client[MCP Client]
    Gateway[API Gateway]
    IdP[Enterprise IdP<br/>Auth0/Keycloak/Okta]
    Server[MCP Server<br/>Resource Server]
    Resources[External Resources]
    
    Client -->|OAuth 2.1 フロー| Gateway
    Gateway <-->|認証・認可| IdP
    Gateway -->|SPIFFE/mTLS<br/>検証済みトークン| Server
    Server -->|既存アクセス制御| Resources
    Gateway -.->|統一ポリシー管理| IdP
    Gateway -.->|ログ・監査| IdP
    
    style Gateway fill:#e8f5e8
    style IdP fill:#fff3e0

```

Posta のアプローチでは、Auth0、Keycloak、Okta などの企業で既に導入されている IdP システムを Authorization Server として活用し、従来の OAuth フロー（authorization code、client credentials など）をそのまま使用します。MCP Server 側では、複雑な認証フロー管理から完全に分離され、トークン検証のみを実装することで Stateless な実装を維持できます。API Gateway は、トークン検証を実行した後、Machine-to-Machine 認証（SPIFFE/mTLS）を使用して MCP Server と通信し、検証済みトークン情報を転送します。MCP Server 内では、この検証済みトークンに基づいて RBAC による認可判定を行い、既存のアクセス制御メカニズムと統合できます。

このアーキテクチャの最大の利点は、既存インフラとの完全互換性にあります。企業は既存の IdP システムをそのまま活用でき、既存の OAuth 実装・運用ノウハウを流用可能であり、セキュリティ監査・コンプライアンス要件への対応も容易になります。実装負荷も最小化され、MCP Server は Resource Server としてのみ機能すればよく、複雑な認証フロー実装が不要になります。運用面では、統一的な認証ポリシー管理、集約されたログ・監査機能、スケーラビリティの確保が可能になり、既存システムへの影響を最小化しながら段階的な MCP 導入が実現できます。

一方で、Posta の提案にも課題があります。API Gateway への依存により、Gateway が単一障害点になるリスクや、OAuth の根本的課題は未解決のままであり、MCP の動的発見モデルとの不適合は残存し、DCR や redirect_uri の問題は解決されません。Client の多様性（CLI、音声など）への対応も限定的です。

### SEP-646: Enterprise-Managed Authorization Profile for MCP

もう一つ別のエンタープライズ向け利用の課題解決に関する SEP を紹介しましょう。

#### OAuth プロファイルとは何か

SEP-646 の詳細に入る前に、まず「OAuth プロファイル」という概念について説明しておきます。

**OAuth プロファイルの基本概念**

OAuth プロファイルとは、OAuth 2.0 の基本仕様を特定の用途や環境に適用するための具体的な実装指針のことです。OAuth 2.0 は非常に柔軟なフレームワークとして設計されているため、そのままでは実装者が判断すべき選択肢が多すぎて、相互運用性の確保が困難になります。そこで、特定のユースケースに最適化された「プロファイル」を定義することで、実装の一貫性と相互運用性を実現します。

例えば、以下のような OAuth プロファイルが存在します。

- **OpenID Connect Core Profile**: Web アプリケーションでのユーザー認証に特化
- **OAuth 2.0 for Native Apps (RFC 8252)**: モバイルアプリやデスクトップアプリに特化  
- **OAuth 2.0 Device Authorization Grant (RFC 8628)**: テレビや IoT デバイスなど入力制限のあるデバイスに特化
- **Financial-grade API (FAPI)**: 金融機関での高セキュリティ要件に特化

**MCP における OAuth プロファイルの必要性**

MCP の文脈では、標準的な OAuth 2.0 の実装だけでは以下の課題があります。

1. **環境の多様性**: Web、CLI、音声インターフェースなど多様なクライアント環境
2. **動的発見**: 事前登録されていないサーバーとの動的な接続
3. **企業要件**: 既存の企業 IdP との統合、細かい権限制御の必要性
4. **セキュリティレベル**: 用途に応じた適切なセキュリティレベルの選択

これらの課題に対応するため、MCP では複数の OAuth プロファイルが提案されています。

- **標準 MCP プロファイル**: 基本的な MCP 認証・認可フロー（現在の仕様）
- **エンタープライズプロファイル**: 企業環境での統合を重視（SEP-646）
- **高セキュリティプロファイル**: 暗号学的認証を重視（SEP-1299 の一部）

**プロファイルの差し替えとは**

「OAuth プロファイルを差し替える」とは、同じ MCP システムにおいて、環境や要件に応じて異なる OAuth プロファイルを選択・適用することを意味します。例えば、

- **個人利用環境**: 標準 MCP プロファイルでシンプルな認証
- **企業環境**: エンタープライズプロファイル（SEP-646）で既存 IdP と統合

この柔軟性により、MCP は様々な環境や要件に適応できる汎用的なプロトコルとして機能します。

#### SEP-646 の位置づけ

[SEP-646: Enterprise-Managed Authorization Profile for MCP](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/646) は、企業環境での MCP 認証・認可を改善する重要な提案です。この提案は、既存の企業アイデンティティインフラストラクチャを活用して、安全で相互運用可能な MCP Client 認証を実現することを目的としています。

SEP-646 は、標準 MCP プロファイルの代替として機能するエンタープライズ向け OAuth プロファイルを定義しており、企業が既存の Auth0、Okta、Keycloak などの IdP システムをそのまま活用して MCP を導入できるようにします。

**SEP-646 が解決する具体的課題**

1. **手動設定の排除**: ユーザーが組織内の各 MCP Server に個別認証する必要を排除
2. **企業管理者の可視性**: 組織内での MCP Server 使用状況の把握と制御
3. **既存インフラ活用**: 企業の既存 IdP（Auth0、Okta、Keycloak 等）をそのまま利用
4. **ポリシー適用**: 部門やロール別のアクセス制御の実現

このドラフトの核心は「リソース Server と認可 Server が、企業 IdP に安全に認可を移譲する」ことを可能とする **Identity Assertion JWT Authorization Grant (ID-JAG)** メカニズムです。

### 背景と動機

SEP-646 は、企業環境における MCP 導入の現実的な課題に対処するために提案されました。エンドユーザーにとっては、組織内の個別サービスに対して MCP Client を手動で接続・認証する必要を排除し、企業管理者にとっては、組織内で使用可能な MCP Server の可視性と制御を実現することが主な目的です。

この提案は、既存の OAuth プロファイルを拡張するものであり、破壊的変更ではありません。企業 IdP 環境下での代替手段として機能し、MCP Client は必要に応じてこのプロファイルを選択できます。

### ID-JAG メカニズムの技術的詳細

**ID-JAG** は、 OAuth 2.0 Token Exchange（RFC 8693）を基盤として、企業 IdP からの身元証明を他の 認可 Server で使用可能にする仕組みです。

```mermaid
sequenceDiagram
    participant User as ユーザー
    participant Client as MCP Client
    participant IdP as Enterprise IdP
    participant MAS as MCP Authorization Server
    participant MRS as MCP Resource Server

    Note over User,IdP: Phase 1: 標準 OIDC 認証
    User->>Client: MCP Server への接続開始
    Client->>IdP: 認証リクエスト（標準OIDC）
    IdP->>User: 認証画面表示
    User->>IdP: 認証情報入力
    IdP-->>Client: ID Token

    Note over Client,IdP: Phase 2: ID-JAG 交換
    Client->>IdP: Token Exchange Request
    Note over Client,IdP: subject_token=ID_Token<br/>audience=MCP_Authorization_Server<br/>resource=MCP_Server_Resource_ID
    IdP->>IdP: ポリシー評価・権限判定
    IdP-->>Client: ID-JAG Token

    Note over Client,MAS: Phase 3: MCP Access Token 取得
    Client->>MAS: Token Request with ID-JAG
    MAS->>MAS: ID-JAG 検証
    MAS-->>Client: MCP Access Token

    Note over Client,MRS: Phase 4: MCP API 呼び出し
    loop MCP API アクセス
        Client->>MRS: MCP Request with Access Token
        MRS-->>Client: MCP Response
    end
```

### ID-JAG Token Exchange の詳細

ID-JAG の交換プロセスでは、以下のパラメータが使用されます。

| パラメータ | 必須性 | 説明 | 例 |
|-----------|--------|------|-----|
| `grant_type` | REQUIRED | `urn:ietf:params:oauth:grant-type:token-exchange` | 固定値 |
| `requested_token_type` | REQUIRED | `urn:ietf:params:oauth:token-type:id-jag` | 固定値 |
| `audience` | REQUIRED | MCP Authorization Server の Issuer URL | `https://auth.chat.example/` |
| `resource` | REQUIRED | MCP Server の RFC9728 Resource Identifier | `https://mcp.chat.example/` |
| `scope` | OPTIONAL | 要求するスコープのスペース区切りリスト | `chat.read chat.history` |
| `subject_token` | REQUIRED | ID Token（または SAML アサーション） | JWT 文字列 |
| `subject_token_type` | REQUIRED | `urn:ietf:params:oauth:token-type:id_token` (OIDC) | 固定値 |

**実際のHTTPリクエスト例**

```http
POST /oauth2/token HTTP/1.1
Host: acme.idp.example
Content-Type: application/x-www-form-urlencoded

grant_type=urn:ietf:params:oauth:grant-type:token-exchange
&requested_token_type=urn:ietf:params:oauth:token-type:id-jag
&audience=https://auth.chat.example/
&resource=https://mcp.chat.example/
&scope=chat.read+chat.history
&subject_token=eyJraWQiOiJzMTZ0cVNtODhwREo4VGZCXzdrSEtQ...
&subject_token_type=urn:ietf:params:oauth:token-type:id_token
&client_id=2ec954a1d60620116d36d9ceb7
&client_secret=a26d84873504215a34a86d52ef5cd64f4b76
```

**Token Exchange レスポンス例**

```http
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store
Pragma: no-cache

{
  "issued_token_type": "urn:ietf:params:oauth:token-type:id-jag",
  "access_token": "eyJhbGciOiJIUzI1NiIsI...",
  "token_type": "N_A",
  "scope": "chat.read chat.history",
  "expires_in": 300
}
```

### ID-JAG JWT の構造

ID-JAG Token は以下のクレームを含む JWT です。

```json
{
  "typ": "oauth-id-jag+jwt"
}
.
{
  "jti": "9e43f81b64a33f20116179",
  "iss": "https://acme.idp.example",
  "sub": "U019488227",
  "aud": "https://auth.chat.example/",
  "resource": "https://mcp.chat.example/",
  "client_id": "f53f191f9311af35",
  "exp": 1311281970,
  "iat": 1311280970,
  "scope": "chat.read chat.history"
}
.
signature
```

| クレーム | 必須性 | 説明 |
|---------|--------|------|
| `typ` | REQUIRED | `oauth-id-jag+jwt` (ヘッダー内) |
| `iss` | REQUIRED | 企業 IdP の Issuer URL |
| `sub` | REQUIRED | ユーザーの識別子 |
| `aud` | REQUIRED | MCP Authorization Server の URL |
| `resource` | REQUIRED | MCP Server のリソース識別子 |
| `client_id` | REQUIRED | MCP Client の識別子 |
| `jti` | REQUIRED | JWT の一意識別子 |
| `exp` | REQUIRED | 有効期限 (Unix timestamp) |
| `iat` | REQUIRED | 発行時刻 (Unix timestamp) |
| `scope` | OPTIONAL | 許可されたスコープ |

### JWT Authorization Grant による Access Token 取得

Phase 3 では、取得した ID-JAG Token を使用して MCP Authorization Server からアクセストークンを取得します。

**JWT Authorization Grant リクエスト例**

```http
POST /oauth2/token HTTP/1.1
Host: auth.chat.example
Authorization: Basic yZS1yYW5kb20tc2VjcmV0v3JOkF0XG5Qx2

grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
assertion=eyJhbGciOiJIUzI1NiIsI...
```

**Access Token レスポンス例**

```http
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store

{
  "token_type": "Bearer",
  "access_token": "2YotnFZFEjr1zCsicMWpAA",
  "expires_in": 86400,
  "scope": "chat.read chat.history"
}
```

### セキュリティ考慮事項

**Client 登録要件**

企業環境では、MCP Client は企業 IdP に事前登録される必要があります。また、MCP Authorization Server にも事前登録が必要で、IdP は MCP Client の `client_id` マッピングを把握している必要があります。

**企業ポリシーの適用範囲**

SEP-646 により、企業 IdP は以下のポリシーを適用できます：

- **ユーザーベースの制御**: 特定のユーザーグループのみ特定の MCP Server へのアクセスを許可
- **スコープベースの制御**: 部門やロールに応じたスコープの制限
- **リソースベースの制御**: アクセス可能な MCP Server の制限

**実装例：部門別アクセス制御**

```json
企業ポリシー例：
{
  "user_groups": {
    "engineering": {
      "allowed_resources": ["https://code.company.com/", "https://docs.company.com/"],
      "allowed_scopes": ["read", "write"]
    },
    "marketing": {
      "allowed_resources": ["https://docs.company.com/"],
      "allowed_scopes": ["read"]
    }
  }
}
```

### SEP-1299 との比較分析

SEP-646 と SEP-1299 は、MCP の認証・認可問題に対する根本的に異なるアプローチを提示しています。

#### 基本的なアプローチの違い

**SEP-1299: Server 主導の新しい認証メカニズム**
- 根本思想：OAuth は MCP に適合しないため、MCP 専用の新しい認証メカニズムを構築
- 責任の所在：MCP Server が認証・認可フローを主導
- 技術的基盤：HTTP Message Signatures + 暗号学的セッションバインディング

**SEP-646: 企業 IdP 活用による既存 OAuth 拡張**
- 根本思想：既存の企業インフラ（IdP）を最大限活用して OAuth を拡張
- 責任の所在：Enterprise IdP が認証・認可を集約管理
- 技術的基盤：OAuth Token Exchange + ID-JAG

#### 詳細比較表

| 観点 | SEP-1299 | SEP-646 |
|------|----------|---------|
| **認証フロー** | Client 公開鍵 → Server 主導認証 → SSE インタラクション | 標準 OIDC → Token Exchange → ID-JAG → Access Token |
| **実装負荷（Server）** | 非常に高い（HTTP Signatures、SSE管理、セッション状態） | 低い（標準Bearer Token 検証のみ） |
| **実装負荷（Client）** | 中程度（鍵ペア生成・管理、HTTP 署名） | 低い（標準 OIDC + Token Exchange API） |
| **既存インフラ統合** | 複雑（新しい標準、エコシステム分断リスク） | 容易（既存 IdP、OAuth 知識活用可能） |
| **解決する課題** | OAuth 根本的不適合（7 つの課題すべて） | 企業環境での手動設定排除、Cross-domain 認証 |
| **適用環境** | 新規構築、高セキュリティ要件 | 既存企業環境、段階的 MCP 導入 |
| **標準化状況** | 新規標準策定が必要 | 既存 IETF 標準（RFC 8693 等）活用 |
| **解決できない課題** | **7 つすべて解決** | **Dynamic Client Registration 問題**：DCR の運用負荷と信頼性問題は未解決<br/>**プラグアンドプレイ体験**：事前登録が必要で MCP の動的発見モデルに不適合<br/>**CLI・音声インターフェース対応**：Web ブラウザ前提の OAuth フローに依存<br/>**redirect URI 問題**：localhost や custom scheme の信頼性問題は残存<br/>**Many Servers, Few Clients 構造**：従来 OAuth モデルとの根本的不整合<br/>**細粒度アクセス制御**：OAuth スコープの粗粒度制御の限界<br/>**Bearer Token リスク**：トークン盗取可能性とセキュリティリスク |

#### Gateway パターンでの考慮事項

**SEP-1299 + Gateway**：
- 二重署名アーキテクチャが必要
- Client 身元証明の複雑な仕組みが必要
- 複数下流 Server からのインタラクション要求の調整が必要

**SEP-646 + Gateway**：
- 既存の API Gateway 機能をそのまま活用可能
- Token 検証・転送の標準的なパターンを使用
- Enterprise IdP との統合が自然

### 実装状況と実証

SEP-646 は理論的な提案にとどまらず、実際の実装と実証が進んでいます。

- **エンドツーエンド実装**: [Okta Cross-App Access MCP](https://github.com/oktadev/okta-cross-app-access-mcp) で実証済み
- **パートナー実装**: 複数のパートナーと進行中の実装

## まとめ

### MCP Auth 技術の全体像

本 Appendix で紹介した各 SEP の関係性と併用可能性を以下の図で整理します。

```mermaid
graph TB
    subgraph "現行の基盤技術層"
        OAuth[標準 OAuth 2.1<br/>基本認証・認可]
        DCR[Dynamic Client Registration<br/>動的 Client 登録]
    end
    
    subgraph "拡張・改善層"
        SEP835[SEP-835<br/>スコープ管理改善<br/>・最小権限原則<br/>・段階的権限拡張]
        SEP991[SEP-991<br/>CIMD<br/>・DCR 運用負荷解決<br/>・プラグ&プレイ実現]
        SS[Software Statements<br/>・Client 偽装防止<br/>・localhost 攻撃対策]
        SEP646[SEP-646<br/>企業プロファイル<br/>・既存 IdP 統合<br/>・企業ポリシー適用]
    end
    
    subgraph "代替アーキテクチャ層"
        SEP1299[SEP-1299<br/>新認証方式<br/>・OAuth 根本課題解決<br/>・暗号学的認証]
    end
    
    OAuth -->|"拡張"| SEP835
    DCR -->|"代替手段"| SEP991
    OAuth -->|"プロファイル"| SEP646
    SEP991 -->|"セキュリティ強化"| SS
    SEP1299 -.->|"完全置換"| OAuth
    
    style SEP835 fill:#2e7d32,color:#ffffff
    style SEP991 fill:#f57c00,color:#ffffff
    style SS fill:#ff6f00,color:#ffffff
    style SEP646 fill:#1976d2,color:#ffffff
    style SEP1299 fill:#c62828,color:#ffffff
    style OAuth fill:#424242,color:#ffffff
    style DCR fill:#616161,color:#ffffff
```

### 矢印の意味

- **拡張**: 既存技術の機能を改善・強化
- **代替手段**: DCR の運用負荷と信頼性問題を別のアプローチで解決
- **プロファイル**: 既存技術を特定環境に最適化
- **セキュリティ強化**: 追加のセキュリティレイヤーを提供
- **完全置換**: 既存技術を根本的に新しい方式で置き換え

### 技術選択の指針

SEP-646 は現実的にすぐに実装可能なアプローチとしてエンタープライズ企業で導入が進むと思われます。一方で SEP-646 は残存する課題全てに対処できるものではないため、課題の優先度に応じて SEP-1299 なのか別の SEP などでユースケースに応じた提案が進むのではないかと考えています。Rug Pull 攻撃に対処するクライアント真正性の確認やツール変更検知なども重要なテーマであり、最終的な仕様は非常に複雑なものとなる可能性がありそうです。USB も広報互換性を保ちながらニーズに対処してきて背景があり、IP コアの裏側の LSI 実装はもはや誰も一からバグ混入なしで作ることは不可能な状態なのではないかと思います。利用者にとって非常に簡単な仕組みの裏側のプロトコルは化け物のように複雑になる、というのはどのプロトコルでも共通するのかもしれませんね。
