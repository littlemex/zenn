---
title: "MCP 認証の仕組みと AI Agent との統合"
---

## 3. MCP 認証の仕組みと AI Agent との統合

前章では 3LO / 2LO の認証パターンを整理した。本章では、AI Agent がツールサーバーと通信するための標準プロトコルである **MCP (Model Context Protocol)** の認証仕様に焦点を当て、その仕組みと Agent 統合における課題を解説する。

---

### 3.1 MCP のセキュリティモデル（Host-Client-Server）

MCP は **Host-Client-Server** の 3 層アーキテクチャを採用しており、各コンポーネントにセキュリティ上の責務が明確に割り当てられている。

```mermaid
graph TB
    subgraph HostProcess["Application Host Process"]
        direction TB
        Host["Host<br/>- セキュリティポリシーの強制<br/>- ユーザー同意の管理<br/>- 認可判断"]
        C1["Client 1"]
        C2["Client 2"]
        C3["Client 3"]
        Host --> C1
        Host --> C2
        Host --> C3
    end

    subgraph Local["Local Machine"]
        S1["MCP Server 1<br/>Files & Git"]
        S2["MCP Server 2<br/>Database"]
        R1[("Local<br/>Resource A")]
        R2[("Local<br/>Resource B")]
        S1 <--> R1
        S2 <--> R2
    end

    subgraph Internet["Internet"]
        S3["MCP Server 3<br/>External APIs"]
        R3[("Remote<br/>Resource C")]
        S3 <--> R3
    end

    C1 -->|"1:1 Session"| S1
    C2 -->|"1:1 Session"| S2
    C3 -->|"1:1 Session<br/>+ OAuth 2.1"| S3
```

**[図 3-1] MCP Host-Client-Server アーキテクチャ図**

各コンポーネントの責務を整理すると次のようになる。

| コンポーネント | 主な責務 | セキュリティ上の役割 |
|-------------|---------|------------------|
| **Host** | 複数 Client の作成・管理、AI/LLM 統合の制御 | セキュリティポリシーの強制、ユーザー同意・認可判断の一元管理 |
| **Client** | Server との 1:1 セッション確立、プロトコルネゴシエーション | Server 間のセキュリティ境界の維持、メッセージルーティング |
| **Server** | Resources / Tools / Prompts の提供 | セキュリティ制約の遵守、最小限のコンテキスト受信 |

ここで重要な設計原則が 2 つある。

1. **Server はフル会話履歴を読めない** -- Server は必要なコンテキスト情報のみを受け取る。会話全体は Host 内に留まり、Server 間で情報が漏洩しない
2. **Host がセキュリティ境界を強制する** -- Server 間の相互作用は Host が制御し、ユーザー同意なしにデータが外部に流出することを防ぐ

この構造は、AgentCore が MCP Host として機能する際に重要な意味を持つ。AgentCore Gateway が Host の役割を担い、各 MCP Server への接続を Client 経由で管理する形が自然な統合パターンとなる。

---

### 3.2 OAuth 2.1 ベースの認証フロー

MCP の認証仕様（**Protocol Revision: 2025-11-25**）は OAuth 2.1 をベースとしている。認証は **OPTIONAL** だが、HTTP ベースのトランスポート（Streamable HTTP）を使用する場合は本仕様への準拠が推奨される。

:::message
MCP 認証の適用範囲:
- **HTTP トランスポート**: OAuth 2.1 仕様に準拠 (SHOULD)
- **STDIO トランスポート**: 環境変数からクレデンシャルを取得。OAuth 仕様には従わない
- **カスタムトランスポート**: そのプロトコルのベストプラクティスに従う (MUST)
:::

#### 準拠する標準仕様

MCP 認証は以下の標準仕様のサブセットを実装している。

| 標準仕様 | RFC / Draft | 用途 |
|---------|------------|------|
| **OAuth 2.1** | draft-ietf-oauth-v2-1-13 | 認証・認可の基盤プロトコル |
| **Resource Indicators** | RFC 8707 | トークンのリソースバインディング（Token Audience Binding） |
| **Protected Resource Metadata** | RFC 9728 | MCP Server が認可サーバー位置を通知 |
| **Authorization Server Metadata** | RFC 8414 | サーバーメタデータの発見（エンドポイント自動検出） |
| **Client ID Metadata Documents** | draft-00 | HTTPS URL をクライアント ID として使用（Pre-registration 不要） |
| **Dynamic Client Registration** | RFC 7591 | クライアントの動的登録（後方互換性のためサポート） |

#### Resource Indicators による Token Audience Binding（RFC 8707）

**Protocol Revision 2025-11-25 の重要な変更**: Client が Authorization Request と Token Request に **`resource` パラメータを含めることが MUST 要件**となりました。

```http
Authorization Request:
  GET /authorize?
    client_id=...
    &scope=...
    &resource=https%3A%2F%2Fmcp.example.com  ← 必須
    &code_challenge=...

Token Request:
  POST /token
    grant_type=authorization_code
    &code=...
    &resource=https%3A%2F%2Fmcp.example.com  ← 必須
```

**セキュリティ上の意義**: 発行されたトークンが特定の MCP Server にバインドされ、盗まれたトークンが異なる Server で使用されることを防止します（Token Audience Binding）。

:::message
`resource` パラメータには MCP Server の HTTPS URL を URL エンコードして指定します。複数の MCP Server にアクセスする場合は、Server ごとに個別のトークンを取得する必要があります。
:::

#### Grant Types の使い分け

MCP では、ユースケースに応じて 2 つの Grant Type を使い分ける。

| Grant Type | ユースケース | 典型例 |
|-----------|------------|--------|
| **Authorization Code** | Client がエンドユーザーの代理で動作 | Agent が SaaS の MCP ツールをユーザー権限で呼び出す |
| **Client Credentials** | Client が別のアプリケーション（M2M） | Agent が在庫確認 API を呼び出す（ユーザー偽装不要） |

これは前章の 3LO / 2LO の対応関係に一致する。Authorization Code Grant が 3LO、Client Credentials Grant が 2LO に相当する。

#### Client 登録方式の優先順位

MCP Client が新しい MCP Server に接続する際、以下の優先順位で Client 登録を試みます。

| 優先度 | 方式 | 説明 | 推奨環境 |
|-------|------|------|---------|
| 1 | **Pre-registered Credentials** | 事前に登録された Client ID / Secret | Enterprise（管理された環境） |
| 2 | **Client ID Metadata Documents** | HTTPS URL をクライアント ID として使用 | Open / Enterprise（Pre-registration 不要） |
| 3 | **Dynamic Client Registration** | RFC 7591 による動的登録 | 後方互換性のためサポート |
| 4 | **Manual Registration** | ユーザーが手動で Client ID を入力 | フォールバック |

#### Client ID Metadata Documents（新方式）

**Protocol Revision 2025-11-25 で新規追加**: MCP Client が HTTPS URL をクライアント ID として使用できます。

```json
{
  "client_id": "https://app.example.com/oauth/client-metadata.json",
  "client_name": "Example MCP Client",
  "redirect_uris": ["http://127.0.0.1:3000/callback"],
  "grant_types": ["authorization_code"],
  "token_endpoint_auth_method": "none"
}
```

Authorization Server は：
1. URL-formatted client_id を検出時、そのメタデータドキュメントを自動フェッチ
2. `client_id` と URL が一致することを検証
3. `redirect_uri` がメタデータに登録されていることを検証

**メリット**: Pre-registration なしで新規クライアントを自動許可可能。Trust Policies（Allowlist / Domain Age Validation 等）で制御可能。

:::message
Authorization Server が Client ID Metadata Documents をサポートしているかは、Authorization Server Metadata の `client_id_metadata_document_supported: true` で確認できます。
:::

#### Authorization Code Grant フロー

認証が必要な MCP Server に対して、Client が初めてリクエストを送信した場合のフローを示す。

::::details Authorization Code Grant の詳細フローと実装例

```mermaid
sequenceDiagram
    participant B as Browser
    participant C as MCP Client
    participant M as MCP Server
    participant A as Authorization Server

    C->>M: MCP Request
    M->>C: HTTP 401 Unauthorized<br/>WWW-Authenticate: Bearer resource_metadata="..."

    Note over C: Step 1: Protected Resource Metadata Discovery
    C->>M: GET /.well-known/oauth-protected-resource
    M->>C: 200 OK + {"authorization_servers": ["https://..."]}

    Note over C: Step 2: Authorization Server Metadata Discovery
    C->>A: GET /.well-known/oauth-authorization-server
    A->>C: 200 OK + Metadata Document

    Note over C: Step 3: Client Registration
    alt Client ID Metadata Documents サポート
        Note over C: client_id = https://app.example.com/metadata.json
    else Dynamic Client Registration
        C->>A: POST /register
        A->>C: Client Credentials (client_id)
    end

    Note over C: Step 4: PKCE パラメータ生成<br/>code_verifier + code_challenge

    C->>B: 認可 URL + code_challenge + resource を開く
    B->>A: GET /authorize?resource=https://mcp.example.com&...
    Note over A: ユーザーがログインして認可
    A->>B: auth code でリダイレクト
    B->>C: Authorization Code

    C->>A: Token Request (code + code_verifier + resource)
    A->>C: Access Token (+ Refresh Token)

    C->>M: MCP Request + Authorization: Bearer token
    Note over C,M: 標準 MCP メッセージ交換の開始
```

**[図 3-2] MCP Authorization Code Grant フロー（2025-11-25 仕様）**

このフローにおける重要なポイントは以下の通り。

**Resource Parameter が必須 (MUST)** -- Authorization Request と Token Request に `resource` パラメータを含めることが必須要件（RFC 8707）。トークンが特定の MCP Server にバインドされ、Token Audience Binding を実現する。

**Protected Resource Metadata Discovery** -- Client はまず `WWW-Authenticate` ヘッダーまたは `/.well-known/oauth-protected-resource` から Protected Resource Metadata を取得し、そこから Authorization Server の URL を抽出する（RFC 9728）。これにより Path component を含む複雑な Issuer URL にも対応可能。

**Client 登録方式の優先順位** -- Pre-registered Credentials → Client ID Metadata Documents → Dynamic Client Registration → Manual Registration の順で試行。Client ID Metadata Documents（HTTPS URL をクライアント ID として使用）が新しい推奨方式となり、Dynamic Client Registration（RFC 7591）は後方互換性のためサポート。

**PKCE が全クライアントで必須 (REQUIRED)** -- Public Client / Confidential Client を問わず、PKCE（Proof Key for Code Exchange）の使用が要求される。これにより Authorization Code のインターセプション攻撃を防止する。

**Bearer Token の送信** -- アクセストークンは全ての HTTP リクエストの `Authorization` ヘッダーに含める必要がある。同一セッション内でも省略は許されない。

```http
GET /v1/contexts HTTP/1.1
Host: mcp.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

::::

---

### 3.3 Access Token と ID Token の JWT 検証の違い

OAuth 2.1 / OIDC では、認証後に 2 種類のトークンが発行されます。

| トークン種別 | 用途 | 標準仕様 | 主要クレーム |
|------------|------|---------|-----------|
| **Access Token** | API アクセス用の認可トークン | OAuth 2.1 | `sub`, `scope`, `iss`, `exp` （aud は OPTIONAL） |
| **ID Token** | ユーザー認証情報を含む識別トークン | OIDC | `sub`, `iss`, `aud`, `exp`, `email` 等 |

MCP Server が Lambda Authorizer で JWT 検証を行う際、**どちらのトークンを使うかによって検証パラメータが異なります**。

::::details JWT 検証の実装例（Access Token / ID Token）

#### Access Token の検証

Access Token には `aud`（audience）クレームが含まれないことが多く、PyJWT で検証する際は `verify_aud: False` を設定します。

```python
import jwt
from jwt.algorithms import RSAAlgorithm

decoded = jwt.decode(
    token,
    signing_key.key,
    algorithms=["RS256"],
    issuer=expected_issuer,
    options={
        "verify_exp": True,
        "verify_aud": False  # Access Token は aud 検証を無効化
    }
)
```

`verify_aud: False` を設定しない場合、以下のエラーが発生します。

```
jwt.exceptions.InvalidAudienceError: Invalid audience
```

#### ID Token の検証

ID Token は OIDC 仕様により `aud` クレームが必須です。`aud` には発行対象のクライアント ID が含まれます。

```python
decoded = jwt.decode(
    token,
    signing_key.key,
    algorithms=["RS256"],
    issuer=expected_issuer,
    audience=expected_client_id,  # ID Token は aud 検証が必要
    options={
        "verify_exp": True,
        "verify_aud": True
    }
)
```

#### Lambda Authorizer でのトークン選択基準

MCP Server の Lambda Authorizer では、以下の基準でトークンを選択します。

| ユースケース | 推奨トークン | 理由 |
|------------|------------|------|
| MCP ツール呼び出しの認可のみ | Access Token | OAuth 2.1 の標準用途。scope による権限制御が可能 |
| ユーザー情報（email 等）が必要 | ID Token | email、name 等のユーザー属性を含む |
| カスタムクレーム（tenant_id 等）の伝播 | どちらでも可 | Cognito Pre Token Generation Lambda で両トークンに注入可能 |

AgentCore との統合では、カスタムクレーム（`tenant_id`, `agent_id`, `role` 等）を JWT に注入する必要があります。Cognito の Pre Token Generation Lambda（V2 Trigger）は、Access Token と ID Token の両方にカスタムクレームを注入できるため、どちらのトークンを使っても問題ありません。ただし、JWT 検証時の `verify_aud` パラメータは適切に設定する必要があります。

:::message
**Cognito Access Token の特殊性**: Cognito が発行する Access Token は、Resource Server スコープを要求した場合に `aud` クレームが含まれることがあります（Cognito の仕様上、`client_id` クレームが audience の代替として機能）。Lambda Authorizer の実装では、`verify_aud: False` に設定したうえで `client_id` を手動検証するアプローチが推奨されます。詳細な実装例は第 5 章を参照してください。
:::

::::

:::message
Lambda Authorizer の実装では、Authorization ヘッダーから取得したトークンが Access Token か ID Token かを判定するロジックが必要です。簡易的な判定方法として、トークンをデコードして `aud` クレームの有無を確認する方法があります。
:::

---

### 3.3.1 Scope Selection Strategy（最小権限の原則）

**Protocol Revision 2025-11-25 で新規追加**: MCP Client が Authorization Request で要求する Scope を選択する際の戦略が明確化されました。

#### Scope の選択優先度

```
優先度 1: WWW-Authenticate ヘッダーの `scope` パラメータ
優先度 2: Protected Resource Metadata の `scopes_supported`
優先度 3: `scopes_supported` が undefined の場合は省略
```

最小権限の原則に基づき、段階的に Scope を要求する設計です。

#### 実装例

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer
  scope="files: read files: write user: profile",
  resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"
```

Client は `files: read files: write user: profile` を Authorization Request の `scope` パラメータに含めます。

:::message
MCP Server は必要最小限の Scope のみを `WWW-Authenticate` ヘッダーで要求すべきです。過剰な Scope 要求はユーザーの同意取得を困難にし、セキュリティリスクを高めます。
:::

---

### 3.3.2 Step-Up Authorization（ランタイム Scope Challenge）

**Protocol Revision 2025-11-25 で新規追加**: 実行時にスコープ不足が検出された場合、Client が自動的に Step-Up Authorization を実施する仕組みが導入されました。

#### Scope Challenge の処理フロー

```mermaid
sequenceDiagram
    participant C as MCP Client
    participant M as MCP Server
    participant B as Browser
    participant A as Authorization Server

    C->>M: MCP Request (Bearer token with scope: files: read)
    M->>C: HTTP 403 Forbidden<br/>WWW-Authenticate: Bearer error="insufficient_scope"<br/>scope="files: read files: write"

    Note over C: 必要 Scope を抽出<br/>files: write が不足していることを検出

    C->>B: 追加 Scope を要求する認可フローを開始
    B->>A: GET /authorize?scope=files: read files: write&...
    Note over A: ユーザーが追加 Scope を認可
    A->>B: Authorization Code
    B->>C: Authorization Code
    C->>A: Token Request
    A->>C: New Access Token (files: read files: write)

    C->>M: MCP Request (Bearer token with full scope)
    M->>C: 200 OK + Response
```

**[図 3-3] Step-Up Authorization フロー**

#### 実装上の注意点

| 項目 | 推奨事項 |
|------|---------|
| **リトライ制限** | 無限ループを防ぐため、最大 2-3 回のリトライに制限 |
| **Scope 蓄積** | 以前のトークンの Scope に新しい Scope を追加して要求 |
| **ユーザー同意** | Step-Up 時も必ずユーザーに追加権限の同意を求める |
| **トークン管理** | 古いトークンは無効化し、新しいトークンで全リクエストを再実行 |

:::message alert
Step-Up Authorization は便利ですが、頻繁に発生するとユーザー体験を損ないます。初回認可時に必要十分な Scope を要求することが推奨されます。
:::

---

### 3.4 Third-Party Authorization（委任認可）

::::details Third-Party Authorization の詳細フロー

エンタープライズ環境では、MCP Server が自前の認可サーバーを持たず、Cognito や Okta などのサードパーティ認可サーバーに認証を委任するケースが多い。MCP 仕様はこのパターンを **Third-Party Authorization** としてサポートしている。

このフローでは、MCP Server が 2 つの役割を同時に担う。

- **OAuth Client**（サードパーティ認可サーバーに対して）
- **OAuth Authorization Server**（MCP Client に対して）

```mermaid
sequenceDiagram
    participant B as Browser
    participant C as MCP Client
    participant M as MCP Server
    participant T as Third-Party Auth Server<br/>(Cognito / Okta 等)

    C->>M: OAuth リクエスト開始
    M->>B: サードパーティ /authorize にリダイレクト
    B->>T: Authorization Request
    Note over T: ユーザーがログインして認可
    T->>B: MCP Server コールバックにリダイレクト
    B->>M: Authorization Code (from Third-Party)

    M->>T: Code をトークンに交換
    T->>M: Third-Party Access Token
    Note over M: MCP Token を生成<br/>(Third-Party Token にバインド)

    M->>B: MCP Client コールバックにリダイレクト
    B->>C: MCP Authorization Code
    C->>M: Code をトークンに交換
    M->>C: MCP Access Token
```

**[図 3-4] Third-Party Authorization フロー**

このフローにはセッションバインディングに関する厳格な要件がある。

- サードパーティトークンと発行した MCP トークン間の安全なマッピングを維持する **MUST**
- MCP トークンを受け入れる前にサードパーティトークンの状態を検証する **MUST**
- サードパーティトークンの有効期限とリニューアルを適切に処理する **MUST**

:::message alert
Third-Party Authorization では、MCP Server がトークンチェーンの中間点となる。サードパーティトークンが失効した場合、それにバインドされた MCP トークンも無効化する仕組みが必要。トークンライフサイクル管理の複雑さが増す点に注意。
:::

AgentCore との統合では、AWS Cognito を Third-Party Auth Server として使用し、MCP Server が Cognito のトークンと MCP トークンを橋渡しするパターンが考えられる。これにより、Cognito の User Pool で管理されたテナント情報やロール情報を、MCP セッション内に伝播させることが可能となる。

#### Third-Party Authorization のセキュリティ考慮事項（2025-11-25 追加）

**Protocol Revision 2025-11-25** で Third-Party Authorization に関するセキュリティ要件が明確化されました。

| セキュリティ要件 | 詳細 | 実装方法 |
|---------------|------|---------|
| **Session Binding** | サードパーティトークンと MCP トークンの安全なマッピング維持 (MUST) | Redis / DynamoDB でのセッション管理、暗号化ストレージ |
| **Token State Validation** | MCP トークン受け入れ前にサードパーティトークンの状態検証 (MUST) | Introspection Endpoint または Token Revocation List の確認 |
| **Authorization Server Abuse Protection (SSRF 対策)** | 任意の URL へのリダイレクトを防止 (MUST) | Allowlist ベースの Authorization Server URL 検証 |
| **Localhost Redirect URI Risks** | Localhost リダイレクトによる CSRF / Token 漏洩のリスク | Production 環境では Localhost を禁止、Development 環境のみ許可 |
| **Trust Policies** | Authorization Server の信頼性評価 | Domain Age Validation、Reputation Check、Manual Allowlist |

**Trust Policies の実装例**:

```python
# Enterprise 環境: Allowlist のみ許可
TRUSTED_AUTH_SERVERS = [
    "https://cognito.us-east-1.amazonaws.com",
    "https://okta.example.com",
    "https://auth0.example.com"
]

def validate_auth_server(issuer_url):
    if issuer_url not in TRUSTED_AUTH_SERVERS:
        raise SecurityError("Untrusted authorization server")
```

```python
# Open 環境: Domain Age 検証
import whois
from datetime import datetime, timedelta

def validate_auth_server_open(issuer_url):
    domain = extract_domain(issuer_url)
    w = whois.whois(domain)

    # ドメイン登録から 6 ヶ月以上経過していることを確認
    if datetime.now() - w.creation_date < timedelta(days=180):
        raise SecurityError("Domain too new")
```

:::message alert
**SSRF 対策は必須**: MCP Server が Third-Party Authorization をサポートする場合、ユーザーが指定した任意の URL にリダイレクトすることになります。これは SSRF（Server-Side Request Forgery）攻撃のリスクを伴います。Authorization Server URL の Allowlist による検証が **MUST** 要件です。
:::

::::

---

### 3.5 AI Agent 固有の認証課題

MCP 認証仕様は汎用的な OAuth 2.1 フローを前提としているが、AI Agent が MCP Client として動作する場合には、従来の Web アプリケーションとは異なる固有の課題が発生する。

| 課題 | 詳細 | 対策 |
|------|------|------|
| **ブラウザ操作の不可** | Authorization Code Grant はブラウザリダイレクトを前提とするが、Agent は自律的に動作しブラウザを操作できない | Host（AgentCore Gateway）がブラウザ操作を代行し、取得済みトークンを Agent に渡す。初回認可はユーザーが事前に完了しておく |
| **トークンライフサイクルの複雑化** | 長時間稼働する Agent はトークン期限切れに直面する。リフレッシュ処理中のリクエスト失敗への対処も必要 | Credential Manager でトークンを一元管理し、自動リフレッシュを実装。複数 MCP Server のトークンを並行管理 |
| **動的サーバー接続** | Agent が実行時に未知の MCP Server に接続する場合、Dynamic Client Registration が必要だが、セキュリティリスクを伴う | Host レベルで接続許可リスト（allowlist）を管理。ユーザー同意を事前に取得する仕組みを構築 |
| **ツール呼び出しの暗黙的認可** | MCP の原則はツール呼び出しに明示的なユーザー同意を要求するが、自律 Agent では毎回の同意取得が現実的でない | AgentCore Policy（Cedar）でツール単位の FGAC を設定し、事前承認されたツールのみ呼び出し可能にする |
| **サンプリングリクエストの制御** | Server が Client に LLM サンプリングを要求する場合、ユーザーの承認とプロンプト制御が必要 | Host が全サンプリングリクエストを仲介し、承認済みパターンのみ自動許可。それ以外はユーザーに確認 |
| **M2M 認証の権限肥大化** | Client Credentials Grant で Agent に付与された権限が過剰になるリスク | 最小権限の原則を徹底。Agent ごとにスコープを限定した Client Credentials を発行 |

**[表 3-1] AI Agent 固有の認証課題と対策**

---

#### MCP と AgentCore の認証モデル比較

MCP の認証モデルと AgentCore の認証モデルは、異なる設計思想に基づいている。両者を組み合わせることで、それぞれの弱点を補完できる。

| 比較項目 | MCP | AgentCore |
|---------|-----|-----------|
| **認証プロトコル** | OAuth 2.1 (Bearer Token) | IAM / STS (Sigv4 署名) |
| **クライアント登録** | Dynamic Client Registration (RFC 7591) | 事前設定（CDK / CloudFormation） |
| **トークン形式** | Bearer Token (JWT 等) | IAM 一時クレデンシャル |
| **認可の粒度** | サーバーレベル（スコープベース） | ツール単位（Cedar FGAC） |
| **マルチテナント** | Third-Party Authorization による委任 | リソースポリシー / FGAC / セッションタグ |
| **ツール認可** | Host レベルのユーザー同意 | Policy (Cedar) + Interceptor (Lambda) |
| **メタデータ発見** | RFC 8414 (自動検出) | AWS Service Endpoints (固定) |

**[表 3-2] MCP vs AgentCore 認証モデル比較表**

この比較から見える統合の方向性は次の通り。

1. **AgentCore が MCP Host として機能する**: AgentCore Gateway が Host の役割を担い、セキュリティポリシーの強制とユーザー同意の管理を一元化する
2. **OAuth 2.1 と IAM の橋渡し**: MCP Server から取得した Bearer Token と、AWS リソースアクセスに必要な IAM クレデンシャルを、Interceptor Lambda で変換・マッピングする
3. **Cedar ポリシーで MCP ツール認可を補完**: MCP のスコープベースの認可に加え、AgentCore Policy（Cedar）でツール単位の細粒度認可を重ね掛けする

:::message
MCP 認証は OPTIONAL だが、エンタープライズ環境ではセキュリティ要件として実装が推奨される。ただし、MCP 認証だけでは Agent 固有の課題（自律動作時の認可、テナント分離等）をカバーしきれない。AgentCore の 3 つのアクセス制御手法（次章で詳述）と組み合わせることで、4 層 Defense in Depth を実現する。
:::

---

### 3.6 セキュリティ考慮事項（2025-11-25 仕様準拠）

MCP Authorization 仕様（Protocol Revision 2025-11-25）では、以下のセキュリティ考慮事項が明記されています。

#### Token Audience Binding and Validation

| 項目 | 要件 | 実装方法 |
|------|------|---------|
| **Resource Parameter** | Authorization/Token Request に `resource` を含める (MUST) | RFC 8707 準拠、MCP Server HTTPS URL を指定 |
| **Token Validation** | 受け取ったトークンの `aud` が自身の Server URL と一致するか検証 | JWT `aud` クレーム検証 |
| **Scope-Resource Binding** | Token に含まれる Scope が要求した Resource に対して有効か検証 | Scope と Resource の対応関係を Authorization Server で管理 |

#### Token Theft Prevention

| 攻撃手法 | 対策 | 標準仕様 |
|---------|------|---------|
| **Authorization Code Interception** | PKCE の使用 (REQUIRED) | RFC 7636 |
| **Token Replay Attack** | Token Audience Binding、短い有効期限 | RFC 8707、OAuth 2.1 Best Practices |
| **Cross-Site Token Theft** | `redirect_uri` の厳格な検証 | OAuth 2.1 |
| **Token Leakage via Logs** | Bearer Token をログに記録しない、マスキング処理 | Implementation Best Practices |

#### Communication Security Requirements

| 要件 | 詳細 | 例外 |
|------|------|------|
| **HTTPS 必須** | すべての OAuth エンドポイントは HTTPS で提供 (MUST) | Localhost のみ HTTP 許可 |
| **TLS 1.2+** | TLS 1.2 以上を使用 (MUST) | TLS 1.0 / 1.1 は禁止 |
| **Certificate Validation** | サーバー証明書の検証を省略しない (MUST) | Self-signed 証明書は開発環境のみ |

#### Authorization Code Protection (PKCE)

| パラメータ | 説明 | 要件 |
|----------|------|------|
| **code_verifier** | 43-128 文字のランダム文字列 | REQUIRED for all clients |
| **code_challenge** | `SHA256(code_verifier)` の Base64 エンコード | REQUIRED |
| **code_challenge_method** | `S256` のみサポート | `plain` は非推奨 |

#### Open Redirection Prevention

| リスク | 対策 | 実装 |
|--------|------|------|
| **任意の URL へのリダイレクト** | `redirect_uri` の事前登録と厳格な一致検証 | Exact Match または Prefix Match |
| **Wildcard `redirect_uri`** | Wildcard は禁止 | 個別の URI を登録 |

#### Confused Deputy Problem

MCP Server が Third-Party Authorization を使用する場合、Confused Deputy Attack のリスクがあります。

**攻撃シナリオ**: 攻撃者が悪意ある Authorization Server URL を指定し、MCP Server を経由して内部ネットワークに SSRF 攻撃を仕掛ける

**対策**:
1. Authorization Server URL の Allowlist 検証 (MUST)
2. Private IP アドレス範囲への接続を禁止
3. DNS Rebinding 対策（固定 IP での検証）

#### Access Token Privilege Restriction

| 原則 | 実装方法 |
|------|---------|
| **最小権限の原則** | 必要最小限の Scope のみ要求 |
| **Scope の段階的要求** | Step-Up Authorization の活用 |
| **Token の短い有効期限** | Access Token の有効期限を 1 時間以内に設定 |
| **Refresh Token の安全な保管** | 暗号化ストレージ、定期的なローテーション |

:::message
**Protocol Revision 2025-11-25 の主要なセキュリティ強化**:
1. RFC 8707 Resource Indicators による Token Audience Binding の必須化
2. RFC 9728 Protected Resource Metadata による Discovery の強化
3. Third-Party Authorization における SSRF 対策の明確化
4. Client ID Metadata Documents による動的クライアント登録の簡素化
5. Step-Up Authorization による動的権限拡張の標準化
:::

---

### 3.7 Amazon Cognito での MCP 認証実装

本章で解説した MCP 認証仕様を AWS 環境で実装する際、**Amazon Cognito を Authorization Server として使用する場合の対応状況と実装方法**を整理します。

#### 3.7.1 Cognito の MCP 仕様対応状況

:::message
**検証済み**: 本セクションの内容は、2026-02-20 に AWS 環境で実際にデプロイして検証済みです（検証レポート: `/tests/e2e-cognito-resource-binding/VERIFICATION_RESULT.md`）。
:::

| MCP 仕様要件 | Cognito 対応状況 | 実装可能性 | 検証状況 |
|-------------|---------------|----------|---------|
| **RFC 8707 Resource Indicators** | **部分対応**（構文レベルのみ） | カスタムクレーム方式で回避可能 | 検証済み |
| **RFC 9728 Protected Resource Metadata** | N/A（MCP Server 側で実装） | FEASIBLE | 検証済み（MCP Server 実装） |
| **RFC 7591 Dynamic Client Registration** | 非対応（API Gateway + Lambda で実装可能） | FEASIBLE | 未検証 |
| **PKCE 必須化** | 対応 | 対応済み | 検証済み |
| **Implicit Grant 廃止** | 対応（設定で無効化） | 対応済み | 検証済み |

#### 3.7.2 RFC 8707 Resource Indicators の実装

**検証結果**: Cognito は RFC 8707 Resource Indicators を **構文レベルで部分的にサポート**していますが、**意味論レベルでは完全に対応していません**。

##### Cognito での RFC 8707 対応状況（検証済み）

```bash
# 1. Resource Server を作成
aws cognito-idp create-resource-server \
  --user-pool-id us-east-1_xxxxx \
  --identifier "https://mcp.example.com" \
  --name "MCP Server" \
  --scopes ScopeName=tools.read,Description="Read MCP tools"

# 2. Authorization Request に resource パラメータを含める
GET /oauth2/authorize?
  response_type=code&
  client_id=xxx&
  redirect_uri=https://app.example.com/callback&
  scope=openid+https://mcp.example.com/tools.read&
  resource=https://mcp.example.com&  # [検証済み] 受け入れられる
  code_challenge=xxx&
  code_challenge_method=S256
```

**検証結果** (2026-02-20):
- [OK] Authorization Endpoint は `resource` パラメータを受け入れる（HTTP 302、エラーなし）
- [OK] Token Endpoint も `resource` パラメータを受け入れる（`invalid_grant` エラーは code の問題）
- **[NG] Access Token の `aud` クレームには設定されない**

##### 実際の Access Token サンプル（検証時）

```json
{
  "sub": "84485438-e041-7055-047a-008da2d552db",
  "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_tLhdj5N0Q",
  "client_id": "15a9ric18rq5fgrbu561gj5o75",
  "token_use": "access",
  "scope": "aws.cognito.signin.user.admin",
  "auth_time": 1771570687,
  "exp": 1771574287,
  "iat": 1771570687,
  "jti": "e881f056-adea-464e-a0b4-d1794c8b96dd",
  "username": "84485438-e041-7055-047a-008da2d552db"
}
```

**重要**: `aud` クレームが存在しません。Cognito の Access Token は標準的に `aud` クレームを持ちません。

##### 回避策: Pre Token Generation Lambda V2 によるカスタムクレーム追加

Cognito の制約を回避するために、Pre Token Generation Lambda V2 でカスタムクレームを追加します。

```python
# Pre Token Generation Lambda V2 (検証済み実装)
def lambda_handler(event, context):
    event['response'] = {
        'claimsAndScopeOverrideDetails': {
            'accessTokenGeneration': {
                'claimsToAddOrOverride': {
                    # aud は上書き不可（Cognito が無視）
                    # カスタムクレームは追加可能
                    'mcp_resource': 'https://mcp.example.com',
                    'mcp_server_uri': 'https://mcp.example.com/sse'
                }
            }
        }
    }
    return event
```

**検証結果**: カスタムクレーム (`mcp_resource`, `mcp_server_uri`) は正常に Access Token に追加されました。

```json
{
  "sub": "84485438-e041-7055-047a-008da2d552db",
  "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_tLhdj5N0Q",
  "client_id": "15a9ric18rq5fgrbu561gj5o75",
  "mcp_resource": "https://mcp.example.com",
  "mcp_server_uri": "https://mcp.example.com/sse",
  "token_use": "access",
  "scope": "aws.cognito.signin.user.admin",
  "auth_time": 1771570687,
  "exp": 1771574287,
  "iat": 1771570687
}
```

**MCP Server 側の対応**: `aud` クレームの代わりに `mcp_resource` カスタムクレームを使用してリソース識別を行います。

##### 制約事項（検証済み）

| 項目 | 検証結果 |
|------|---------|
| `resource` パラメータ受容 | [OK] Authorization Endpoint と Token Endpoint の両方で受け入れる |
| Access Token の `aud` クレーム | [NG] 設定されない（Cognito の標準動作） |
| Pre Token Generation Lambda で aud 上書き | [NG] 無視される |
| カスタムクレーム追加 | [OK] 正常動作（mcp_resource 等を追加可能） |
| Client Credentials Grant | [PARTIAL] resource パラメータは受け入れるが aud には効果なし |
| Admin Auth Flow での Lambda trigger | [NG] 呼び出されない（Hosted UI 経由のみ） |

#### 3.7.3 RFC 9728 Protected Resource Metadata の実装

RFC 9728 は **MCP Server（Protected Resource）側で実装すべき仕様**であり、Cognito の責務ではありません。AgentCore Gateway または MCP Server アプリケーションで以下を実装します。

```python
# FastAPI での実装例
@app.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata():
    return {
        "resource": "https://mcp.example.com",
        "authorization_servers": [
            "https://cognito-idp.ap-northeast-1.amazonaws.com/ap-northeast-1_xxxxx"
        ],
        "scopes_supported": ["openid", "mcp: tools.read", "mcp: tools.execute"],
        "bearer_methods_supported": ["header"]
    }

@app.middleware("http")
async def add_www_authenticate(request, call_next):
    response = await call_next(request)
    if response.status_code == 401:
        response.headers["WWW-Authenticate"] = \
            'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'
    return response
```

#### 3.7.4 段階的な実装アプローチ

MCP 認証を Cognito で実装する際は、以下の 3 つのフェーズで段階的に対応することを推奨します。

##### Phase 1: 最小構成（カスタムクレーム方式、検証済み）

```
[MCP Client]
  |
  v
[Cognito User Pool]
  |-- Resource Server        (Scope 管理)
  |-- Pre Token Generation V2 (mcp_resource カスタムクレーム追加)
  |
  v
[MCP Server / AgentCore Gateway]
  |-- /.well-known/oauth-protected-resource  (RFC 9728)
  |-- トークン検証 (mcp_resource カスタムクレーム検証)
```

**対応範囲**（検証済み）:
- [対応] RFC 8707: **カスタムクレーム方式で回避**（`mcp_resource` クレームを使用）
- [対応] RFC 9728: MCP Server 側で実装
- [未対応] RFC 7591: ハードコード `client_id` で代替
- [対応] PKCE、OAuth 2.1 Authorization Code Grant

**注意**: Cognito は `aud` クレームを設定しないため、MCP Server 側で `mcp_resource` カスタムクレームを使用してリソース識別を行います。これは RFC 8707 の意図を部分的に実現する回避策です。

##### Phase 2: Dynamic Client Registration 追加

Phase 1 に加えて、API Gateway + Lambda で RFC 7591 互換の `/register` エンドポイントを実装します。

```
[API Gateway + Lambda]
  |-- POST /register
  |-- CreateUserPoolClient API 呼び出し
```

**追加対応**:
- [対応] RFC 7591: Lambda で DCR エンドポイント実装

##### Phase 3: 完全対応（必要に応じて）

Custom Authorization Server を API Gateway + Lambda で実装し、Cognito は ID Provider（認証のみ）として使用します。

**追加対応**:
- [対応] RFC 8707 完全対応（Token Endpoint の `resource` パラメータ含む）
- [対応] Client Credentials Grant での Resource Binding

#### 3.7.5 実装の詳細

Cognito Resource Binding、Pre Token Generation Lambda V2、Dynamic Client Registration の詳細な実装例は、以下のドキュメントを参照してください。

::::details 実装ガイドの参照先

**ドキュメント**: `/docs/research/COGNITO_MCP_WORKAROUNDS.md`

このドキュメントには以下が含まれます:
- Cognito Resource Binding の設定手順
- Pre Token Generation Lambda V2 の実装例（Python）
- RFC 9728 Protected Resource Metadata の実装例（FastAPI）
- Dynamic Client Registration エンドポイントの実装例（Lambda）
- API Gateway + Lambda Proxy パターン
- Custom Authorization Server パターン

::::

:::message
**検証完了**: 本書で解説している MCP 仕様（Protocol Revision 2025-11-25）と実装例は、Track 5 検証で RFC 原文との照合を完了しており、技術的に正確です。さらに、Cognito を使用した実際のデプロイ検証（E2E テスト）も 2026-02-20 に完了しています（検証レポート: `/tests/e2e-cognito-resource-binding/VERIFICATION_RESULT.md`）。Phase 1 のカスタムクレーム方式であれば、Cognito の標準機能（Pre Token Generation Lambda V2）のみで MCP 認証の主要要件を満たせることが**実環境で検証済み**です。
:::

---

次章では、AgentCore が提供する 3 つのアクセス制御手法 -- Inbound Authorization、AgentCore Policy、Gateway Interceptors -- について、それぞれの仕組みと使い分けを解説する。
