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
| 01 | ___MUST:___ `/.well-known/oauth-protected-resource` エンドポイント実装 | ___対応あり[関数]:___ `mcpAuthMetadataRouter` |
| 02 | ___MUST:___ `authorization_servers` フィールド提供 | ___対応あり[スキーマ]:___ `OAuthProtectedResourceMetadataSchema` |
| 03 | ___MUST:___ `401 Unauthorized` レスポンス時に `WWW-Authenticate` ヘッダーでメタデータ URL を提供 | ___対応あり[関数]:___ `requireBearerAuth` |
| 04 | ___MUST:___ `resource` フィールド提供 | ___対応あり[スキーマ]:___ `OAuthProtectedResourceMetadataSchema` |
| 05 | ___SHOULD:___ `scopes_supported` フィールド提供 |  ___対応あり[スキーマ]:___ `OAuthProtectedResourceMetadataSchema` |
| 06 | ___SHOULD:___ `resource_name` フィールド提供 | ___対応あり[スキーマ]:___ `OAuthProtectedResourceMetadataSchema` |




### 必須実装要素
```json
{
  "resource": "https://mcp.example.com",
  "authorization_servers": ["https://auth.example.com"]
}
```

### 対応コード例
```typescript
app.use("/.well-known/oauth-protected-resource", (req, res) => {
  res.json({
    resource: "https://mcp.example.com",
    authorization_servers: ["https://auth.example.com"],
    scopes_supported: ["tools:read", "tools:execute"],
    resource_name: "Example MCP Server"
  });
});
```

## 2. トークン検証 (OAuth 2.1 / RFC8707)

**OAuth 2.1 Section 5.2: Resource Server Access Validation**
**RFC8707: Resource Indicators for OAuth 2.0**

### 責務
- Bearerトークンの検証
- トークンのaudience検証（RFC8707）
- トークンの有効期限確認
- スコープの検証
- 適切なエラーレスポンスの返却

### 必須実装要素
- トークンの検証ロジック
- audienceの厳格な検証（自身のリソースURIとの一致確認）
- 有効期限の確認
- スコープに基づくアクセス制御

### 対応コード例
```typescript
function verifyAccessToken(token) {
  const decoded = jwt.verify(token, publicKey);
  
  // audienceの検証（RFC8707）
  const audiences = Array.isArray(decoded.aud) ? decoded.aud : [decoded.aud];
  if (!audiences.includes("https://mcp.example.com")) {
    throw new Error("Token was not issued for this resource");
  }
  
  // 有効期限の確認
  if (decoded.exp < Math.floor(Date.now() / 1000)) {
    throw new Error("Token has expired");
  }
  
  return {
    clientId: decoded.client_id,
    scopes: decoded.scope.split(" ")
  };
}
```

## 3. エラーハンドリング (OAuth 2.1 Section 5.3)

**OAuth 2.1 Section 5.3: Error Response**

### 責務
- 適切なHTTPステータスコードの返却
- 標準化されたエラーレスポンスの提供
- `WWW-Authenticate`ヘッダーの設定

### 必須実装要素
- 401 Unauthorized: 認証が必要またはトークンが無効
- 403 Forbidden: スコープ不足または権限不足
- 400 Bad Request: リクエスト形式の問題

### 対応コード例
```typescript
function handleTokenError(res, error) {
  if (error.message === "Invalid token" || error.message === "Missing token") {
    res.set("WWW-Authenticate", 
      `Bearer error="invalid_token", error_description="${error.message}", resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"`
    );
    res.status(401).json({
      error: "invalid_token",
      error_description: error.message
    });
  } else if (error.message === "Insufficient scope") {
    res.set("WWW-Authenticate", 
      `Bearer error="insufficient_scope", error_description="${error.message}", resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"`
    );
    res.status(403).json({
      error: "insufficient_scope",
      error_description: error.message
    });
  } else {
    res.status(400).json({
      error: "invalid_request",
      error_description: error.message
    });
  }
}
```

## 4. セキュアな通信 (OAuth 2.1 Section 1.5)

**OAuth 2.1 Section 1.5: Communication Security**

### 責務
- HTTPS通信の強制（localhost除く）
- 安全なトークン送受信

### 必須実装要素
- HTTPS設定
- 適切なセキュリティヘッダー

### 対応コード例
```typescript
// HTTPS強制ミドルウェア
function requireHttps(req, res, next) {
  if (!req.secure && req.hostname !== "localhost" && req.hostname !== "127.0.0.1") {
    return res.redirect(`https://${req.hostname}${req.url}`);
  }
  next();
}

app.use(requireHttps);
app.use(helmet()); // セキュリティヘッダー設定
```

## 5. トークン受信と検証 (OAuth 2.1 Section 5.1)

**OAuth 2.1 Section 5.1: Resource Access Request**

### 責務
- Authorizationヘッダーからのトークン抽出
- トークンの形式検証
- クエリパラメータでのトークン受け取り禁止

### 必須実装要素
- Bearerトークンの抽出ロジック
- トークン形式の検証

### 対応コード例
```typescript
function extractBearerToken(req) {
  const authHeader = req.headers.authorization;
  if (!authHeader) {
    throw new Error("Missing Authorization header");
  }
  
  const [type, token] = authHeader.split(" ");
  if (type.toLowerCase() !== "bearer" || !token) {
    throw new Error("Invalid Authorization header format");
  }
  
  return token;
}
```

## 6. リソース固有のスコープ処理

**RFC8707: Resource Indicators for OAuth 2.0**

### 責務
- リソース固有のスコープ検証
- スコープに基づくアクセス制御

### 必須実装要素
- スコープ検証ロジック
- 機能ごとの必要スコープ定義

### 対応コード例
```typescript
function validateScope(requiredScopes, tokenScopes) {
  return requiredScopes.every(scope => tokenScopes.includes(scope));
}

app.use("/tools", (req, res, next) => {
  try {
    const token = extractBearerToken(req);
    const authInfo = verifyAccessToken(token);
    
    if (!validateScope(["tools:read"], authInfo.scopes)) {
      throw new Error("Insufficient scope");
    }
    
    req.auth = authInfo;
    next();
  } catch (error) {
    handleTokenError(res, error);
  }
});
```

## 7. トークン不正転用防止 (RFC8707)

**RFC8707: Resource Indicators for OAuth 2.0**

### 責務
- トークンのaudience検証
- トークンの不正転用防止
- 混同代理問題（confused deputy）の対策

### 必須実装要素
- 厳格なaudience検証
- トークンの使用範囲制限

### 対応コード例
```typescript
function validateTokenAudience(token, expectedResource) {
  const decoded = jwt.decode(token);
  const audiences = Array.isArray(decoded.aud) ? decoded.aud : [decoded.aud];
  
  // 正規化して比較
  const normalizedExpected = normalizeResourceUri(expectedResource);
  const normalizedAudiences = audiences.map(aud => normalizeResourceUri(aud));
  
  return normalizedAudiences.includes(normalizedExpected);
}

function normalizeResourceUri(uri) {
  const url = new URL(uri);
  return `${url.protocol}//${url.host}${url.pathname}`;
}
```

## 8. トークン漏洩対策

**OAuth 2.1 Section 7.1: Token Theft**

### 責務
- トークンの安全な保管
- トークンログの最小化
- トークン漏洩時の影響範囲の最小化

### 必須実装要素
- トークンの安全な取り扱い
- ログにトークンを記録しない
- 短命トークンの使用

### 対応コード例
```typescript
// トークンをログに記録しない
function sanitizeForLogging(req) {
  const sanitized = { ...req };
  if (sanitized.headers && sanitized.headers.authorization) {
    sanitized.headers.authorization = "Bearer [REDACTED]";
  }
  return sanitized;
}

// リクエストログ
app.use((req, res, next) => {
  console.log("Request:", sanitizeForLogging(req));
  next();
});
```

## 9. レート制限と乱用防止

**セキュリティベストプラクティス**

### 責務
- APIの乱用防止
- DoS攻撃対策
- リソース消費の制限

### 必須実装要素
- レート制限の実装
- 異常なトラフィックの検出

### 対応コード例
```typescript
const rateLimit = require("express-rate-limit");

app.use("/tools", rateLimit({
  windowMs: 15 * 60 * 1000, // 15分
  max: 100, // 15分あたり100リクエスト
  message: {
    error: "too_many_requests",
    error_description: "Rate limit exceeded"
  }
}));
```

## 10. リソースサーバーメタデータの更新

**RFC9728: OAuth 2.0 Protected Resource Metadata**

### 責務
- メタデータの定期的な更新
- 認可サーバー情報の最新化

### 必須実装要素
- メタデータ更新メカニズム
- 認可サーバー情報の管理

### 対応コード例
```typescript
// 設定の動的更新
function updateResourceMetadata(newAuthServers) {
  config.authorizationServers = newAuthServers;
}

// 定期的なメタデータ更新（例：認可サーバーの変更時）
function refreshMetadata() {
  // 認可サーバー情報の取得と更新
  fetchAuthServerInfo().then(servers => {
    updateResourceMetadata(servers);
  });
}
```

## まとめ: MCP Serverの責務とRFC対応表

| 責務 | 対応RFC | 重要度 |
|------|---------|--------|
| リソースメタデータの提供 | RFC9728 | 必須 (MUST) |
| トークン検証 | OAuth 2.1 / RFC8707 | 必須 (MUST) |
| エラーハンドリング | OAuth 2.1 Section 5.3 | 必須 (MUST) |
| セキュアな通信 | OAuth 2.1 Section 1.5 | 必須 (MUST) |
| トークン受信と検証 | OAuth 2.1 Section 5.1 | 必須 (MUST) |
| リソース固有のスコープ処理 | RFC8707 | 推奨 (SHOULD) |
| トークン不正転用防止 | RFC8707 | 必須 (MUST) |
| トークン漏洩対策 | OAuth 2.1 Section 7.1 | 推奨 (SHOULD) |
| レート制限と乱用防止 | セキュリティベストプラクティス | 推奨 (SHOULD) |
| リソースサーバーメタデータの更新 | RFC9728 | 推奨 (SHOULD) |

この整理により、MCP Serverがリソースサーバーとして担うべき責務と、それに対応するRFCが明確になります。記事を書く際には、これらの責務を中心に、実装方法や具体例を交えて解説することで、MCP Server実装者にとって有用な情報となるでしょう。

# 1. 認可仕様の実装要件

## 1.1 必須要件（MUST）

### Protected Resource Metadata
- RFC9728に準拠したリソースメタデータを提供する必要があります
- `/.well-known/oauth-protected-resource`エンドポイントを実装
- 401 Unauthorizedレスポンス時に`WWW-Authenticate`ヘッダーでメタデータURLを提供

### トークン検証
- アクセストークンが自身のリソースに対して発行されたものか検証
- トークンのaudience claimの検証
- トークンの有効期限の確認
- スコープの検証

### セキュリティ要件
- すべての認可サーバーエンドポイントはHTTPS必須
- リダイレクトURIはlocalhostまたはHTTPS必須
- トークンの安全な保管と管理
- トークンの不正転用防止

## 1.2 推奨要件（SHOULD）

### Dynamic Client Registration
- RFC7591に準拠したクライアント動的登録の実装
- クライアントメタデータの検証
- クライアントシークレットの有効期限管理

### トークン管理
- 短命のアクセストークン発行
- リフレッシュトークンのローテーション（公開クライアント向け）
- トークン失効エンドポイントの提供

# 2. TypeScript SDKの提供機能

## 2.1 コアインターフェース

### OAuthServerProvider
```typescript
interface OAuthServerProvider {
  clientsStore: OAuthRegisteredClientsStore;
  authorize(client, params, res): Promise<void>;
  challengeForAuthorizationCode(client, code): Promise<string>;
  exchangeAuthorizationCode(client, code, verifier?, uri?): Promise<OAuthTokens>;
  exchangeRefreshToken(client, token, scopes?): Promise<OAuthTokens>;
  verifyAccessToken(token): Promise<AuthInfo>;
  revokeToken?(client, request): Promise<void>;
}
```

### OAuthRegisteredClientsStore
```typescript
interface OAuthRegisteredClientsStore {
  getClient(clientId: string): Promise<OAuthClientInformationFull>;
  registerClient?(client: OAuthClientInformationFull): Promise<OAuthClientInformationFull>;
}
```

## 2.2 主要コンポーネント

### ルーター
- `mcpAuthRouter`: 標準的なOAuthエンドポイントを提供
- `mcpAuthMetadataRouter`: メタデータエンドポイントを提供

### ハンドラー
- `authorizationHandler`: 認可フロー
- `tokenHandler`: トークン発行・更新
- `clientRegistrationHandler`: クライアント登録
- `revocationHandler`: トークン失効
- `metadataHandler`: メタデータ提供

### ミドルウェア
- `authenticateClient`: クライアント認証
- `requireBearerAuth`: Bearerトークン検証
- `allowedMethods`: HTTPメソッド制限

## 2.3 実装サポート

### ProxyOAuthServerProvider
- 既存のOAuthサーバーへの委譲実装
- エンドポイントのプロキシ
- トークン検証の委譲

### エラー処理
- OAuth 2.1準拠のエラーレスポンス
- 標準エラーコードとメッセージ
- エラーURIサポート

# 3. 実装のポイント

1. **段階的実装**
   - まず必須要件（MUST）を実装
   - その後推奨要件（SHOULD）を追加
   - 最後にオプション機能を実装

2. **セキュリティ考慮**
   - トークンの適切な検証
   - PKCE必須化
   - HTTPSの強制
   - トークンの不正転用防止

3. **拡張性**
   - カスタムプロバイダーの実装
   - 既存認可サーバーとの統合
   - メタデータの拡張

4. **運用考慮**
   - レート制限の実装
   - ログ出力
   - エラーハンドリング
   - メトリクス収集

SDKは必要な機能を体系的に提供しており、MCP Server実装者は提供されたインターフェースとコンポーネントを利用することで、認可仕様に準拠したサーバーを効率的に実装できます。

# MCP認可仕様の実装 - SDKコードレベル解説

## 1. Protected Resource Metadata (RFC9728)

### 実装方法

```typescript
// mcpAuthMetadataRouter関数を使用してメタデータエンドポイントを設定
const app = express();
app.use(mcpAuthMetadataRouter({
  oauthMetadata: {
    issuer: "https://auth.example.com",
    authorization_endpoint: "https://auth.example.com/authorize",
    token_endpoint: "https://auth.example.com/token",
    // 他の必要なメタデータ
  },
  resourceServerUrl: new URL("https://mcp.example.com"),
  scopesSupported: ["read", "write"],
  resourceName: "Example MCP Server"
}));
```

### コード解説

`mcpAuthMetadataRouter`は以下のエンドポイントを自動的に設定します：

1. `/.well-known/oauth-protected-resource` - RFC9728に準拠したリソースメタデータを提供
2. `/.well-known/oauth-authorization-server` - 後方互換性のためのメタデータ

`bearerAuth.ts`では、401レスポンス時に`WWW-Authenticate`ヘッダーを設定：

```typescript
// bearerAuth.tsからの抜粋
const wwwAuthValue = resourceMetadataUrl
  ? `Bearer error="${error.errorCode}", error_description="${error.message}", resource_metadata="${resourceMetadataUrl}"`
  : `Bearer error="${error.errorCode}", error_description="${error.message}"`;
res.set("WWW-Authenticate", wwwAuthValue);
```

## 2. トークン検証

### 実装方法

```typescript
// bearerAuthミドルウェアを使用してトークンを検証
app.use("/protected", requireBearerAuth({
  verifier: authProvider,
  requiredScopes: ["read"],
  resourceMetadataUrl: "https://mcp.example.com/.well-known/oauth-protected-resource"
}));
```

### コード解説

`requireBearerAuth`ミドルウェアは以下の検証を行います：

```typescript
// bearerAuth.tsからの抜粋
const authInfo = await verifier.verifyAccessToken(token);

// スコープ検証
if (requiredScopes.length > 0) {
  const hasAllScopes = requiredScopes.every(scope =>
    authInfo.scopes.includes(scope)
  );
  if (!hasAllScopes) {
    throw new InsufficientScopeError("Insufficient scope");
  }
}

// 有効期限検証
if (!!authInfo.expiresAt && authInfo.expiresAt < Date.now() / 1000) {
  throw new InvalidTokenError("Token has expired");
}
```

トークン検証の実装は`OAuthServerProvider`インターフェースの`verifyAccessToken`メソッドで行います：

```typescript
// カスタム実装例
async verifyAccessToken(token: string): Promise<AuthInfo> {
  // JWTの場合
  const decoded = jwt.verify(token, publicKey, {
    algorithms: ["RS256"]
  });
  
  // audienceの検証（必須）
  if (decoded.aud !== "https://mcp.example.com") {
    throw new InvalidTokenError("Invalid audience");
  }
  
  return {
    token,
    clientId: decoded.client_id,
    scopes: decoded.scope.split(" "),
    expiresAt: decoded.exp
  };
}
```

## 3. Dynamic Client Registration (RFC7591)

### 実装方法

```typescript
// クライアント登録ストアの実装
const clientStore: OAuthRegisteredClientsStore = {
  async getClient(clientId) {
    // DBからクライアント情報を取得
    return db.clients.findOne({ client_id: clientId });
  },
  
  async registerClient(client) {
    // クライアント情報を保存
    return db.clients.insertOne(client);
  }
};

// 認可ルーターの設定
app.use(mcpAuthRouter({
  provider: authProvider,
  issuerUrl: new URL("https://auth.example.com"),
  baseUrl: new URL("https://auth.example.com"),
  scopesSupported: ["read", "write"],
  resourceName: "Example MCP Server"
}));
```

### コード解説

`clientRegistrationHandler`は以下の処理を行います：

```typescript
// register.tsからの抜粋
router.post("/", async (req, res) => {
  // クライアントメタデータのバリデーション
  const parseResult = OAuthClientMetadataSchema.safeParse(req.body);
  if (!parseResult.success) {
    throw new InvalidClientMetadataError(parseResult.error.message);
  }

  const clientMetadata = parseResult.data;
  const isPublicClient = clientMetadata.token_endpoint_auth_method === 'none'

  // クライアントIDとシークレットの生成
  const clientId = crypto.randomUUID();
  const clientSecret = isPublicClient
    ? undefined
    : crypto.randomBytes(32).toString('hex');
  const clientIdIssuedAt = Math.floor(Date.now() / 1000);

  // シークレットの有効期限設定
  const clientsDoExpire = clientSecretExpirySeconds > 0
  const secretExpiryTime = clientsDoExpire ? clientIdIssuedAt + clientSecretExpirySeconds : 0
  const clientSecretExpiresAt = isPublicClient ? undefined : secretExpiryTime

  // クライアント情報の作成と保存
  let clientInfo: OAuthClientInformationFull = {
    ...clientMetadata,
    client_id: clientId,
    client_secret: clientSecret,
    client_id_issued_at: clientIdIssuedAt,
    client_secret_expires_at: clientSecretExpiresAt,
  };

  clientInfo = await clientsStore.registerClient!(clientInfo);
  res.status(201).json(clientInfo);
});
```

## 4. 認可フロー (OAuth 2.1 + PKCE)

### 実装方法

```typescript
// 認可サーバープロバイダーの実装
const authProvider: OAuthServerProvider = {
  clientsStore,
  
  async authorize(client, params, res) {
    // 認可コードの生成
    const authCode = crypto.randomBytes(32).toString('hex');
    
    // コードチャレンジの保存
    await db.authCodes.insertOne({
      code: authCode,
      clientId: client.client_id,
      codeChallenge: params.codeChallenge,
      redirectUri: params.redirectUri,
      scopes: params.scopes,
      expiresAt: Date.now() + 600000 // 10分
    });
    
    // リダイレクト
    const redirectUrl = new URL(params.redirectUri);
    redirectUrl.searchParams.set('code', authCode);
    if (params.state) {
      redirectUrl.searchParams.set('state', params.state);
    }
    res.redirect(redirectUrl.toString());
  },
  
  async challengeForAuthorizationCode(client, code) {
    const authCode = await db.authCodes.findOne({ 
      code, 
      clientId: client.client_id 
    });
    return authCode?.codeChallenge || '';
  },
  
  // 他のメソッド実装...
};
```

### コード解説

認可フローは`authorizationHandler`で処理されます：

```typescript
// authorize.tsからの抜粋
router.all("/", async (req, res) => {
  // クライアントIDとリダイレクトURIの検証
  const result = ClientAuthorizationParamsSchema.safeParse(req.method === 'POST' ? req.body : req.query);
  if (!result.success) {
    throw new InvalidRequestError(result.error.message);
  }

  // 認可パラメータの検証
  const parseResult = RequestAuthorizationParamsSchema.safeParse(req.method === 'POST' ? req.body : req.query);
  if (!parseResult.success) {
    throw new InvalidRequestError(parseResult.error.message);
  }

  // スコープの検証
  if (scope !== undefined) {
    requestedScopes = scope.split(" ");
    const allowedScopes = new Set(client.scope?.split(" "));
    for (const scope of requestedScopes) {
      if (!allowedScopes.has(scope)) {
        throw new InvalidScopeError(`Client was not registered with scope ${scope}`);
      }
    }
  }

  // 認可処理の実行
  await provider.authorize(client, {
    state,
    scopes: requestedScopes,
    redirectUri: redirect_uri,
    codeChallenge: code_challenge,
  }, res);
});
```

トークン交換は`tokenHandler`で処理されます：

```typescript
// token.tsからの抜粋
// 認可コードの交換
const codeChallenge = await provider.challengeForAuthorizationCode(client, code);
if (!(await verifyChallenge(code_verifier, codeChallenge))) {
  throw new InvalidGrantError("code_verifier does not match the challenge");
}

const tokens = await provider.exchangeAuthorizationCode(
  client, 
  code, 
  skipLocalPkceValidation ? code_verifier : undefined,
  redirect_uri
);
res.status(200).json(tokens);
```

## 5. トークン管理

### 実装方法

```typescript
// トークン発行の実装例
async exchangeAuthorizationCode(client, code, codeVerifier, redirectUri) {
  // 認可コードの検証
  const authCode = await db.authCodes.findOne({ 
    code, 
    clientId: client.client_id,
    redirectUri
  });
  
  if (!authCode || authCode.expiresAt < Date.now()) {
    throw new InvalidGrantError("Invalid or expired authorization code");
  }
  
  // アクセストークンの生成
  const accessToken = jwt.sign({
    client_id: client.client_id,
    scope: authCode.scopes.join(" "),
    aud: "https://mcp.example.com" // audienceの設定（必須）
  }, privateKey, {
    algorithm: "RS256",
    expiresIn: 3600 // 1時間
  });
  
  // リフレッシュトークンの生成（公開クライアントの場合は回転式）
  const refreshToken = crypto.randomBytes(32).toString('hex');
  await db.refreshTokens.insertOne({
    token: refreshToken,
    clientId: client.client_id,
    scopes: authCode.scopes,
    expiresAt: Date.now() + 30 * 24 * 60 * 60 * 1000 // 30日
  });
  
  // 認可コードの削除（使い捨て）
  await db.authCodes.deleteOne({ code });
  
  return {
    access_token: accessToken,
    token_type: "Bearer",
    expires_in: 3600,
    refresh_token: refreshToken,
    scope: authCode.scopes.join(" ")
  };
}
```

### コード解説

トークン失効は`revocationHandler`で処理されます：

```typescript
// revoke.tsからの抜粋
router.post("/", async (req, res) => {
  const parseResult = OAuthTokenRevocationRequestSchema.safeParse(req.body);
  if (!parseResult.success) {
    throw new InvalidRequestError(parseResult.error.message);
  }

  await provider.revokeToken!(client, parseResult.data);
  res.status(200).json({});
});
```

## 6. セキュリティ実装

### HTTPS強制

```typescript
// router.tsからの抜粋
const checkIssuerUrl = (issuer: URL): void => {
  // Technically RFC 8414 does not permit a localhost HTTPS exemption, but this will be necessary for ease of testing
  if (issuer.protocol !== "https:" && issuer.hostname !== "localhost" && issuer.hostname !== "127.0.0.1") {
    throw new Error("Issuer URL must be HTTPS");
  }
  if (issuer.hash) {
    throw new Error(`Issuer URL must not have a fragment: ${issuer}`);
  }
  if (issuer.search) {
    throw new Error(`Issuer URL must not have a query string: ${issuer}`);
  }
}
```

### レート制限

```typescript
// token.tsからの抜粋
if (rateLimitConfig !== false) {
  router.use(rateLimit({
    windowMs: 15 * 60 * 1000, // 15分
    max: 50, // 15分で50リクエスト
    standardHeaders: true,
    legacyHeaders: false,
    message: new TooManyRequestsError('You have exceeded the rate limit for token requests').toResponseObject(),
    ...rateLimitConfig
  }));
}
```

## 7. プロキシ実装（既存認可サーバーとの統合）

```typescript
// 既存の認可サーバーへのプロキシ設定
const proxyProvider = new ProxyOAuthServerProvider({
  endpoints: {
    authorizationUrl: "https://auth.example.com/authorize",
    tokenUrl: "https://auth.example.com/token",
    revocationUrl: "https://auth.example.com/revoke",
    registrationUrl: "https://auth.example.com/register"
  },
  
  // トークン検証の実装
  async verifyAccessToken(token) {
    const response = await fetch("https://auth.example.com/introspect", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: new URLSearchParams({
        token,
        token_type_hint: "access_token"
      })
    });
    
    const data = await response.json();
    if (!data.active) {
      throw new InvalidTokenError("Token is inactive");
    }
    
    return {
      token,
      clientId: data.client_id,
      scopes: data.scope.split(" "),
      expiresAt: data.exp
    };
  },
  
  // クライアント情報取得の実装
  async getClient(clientId) {
    // クライアント情報の取得ロジック
    return db.clients.findOne({ client_id: clientId });
  }
});
```

### コード解説

`ProxyOAuthServerProvider`は既存の認可サーバーへの委譲を実装します：

```typescript
// proxyProvider.tsからの抜粋
async authorize(client, params, res) {
  const targetUrl = new URL(this._endpoints.authorizationUrl);
  const searchParams = new URLSearchParams({
    client_id: client.client_id,
    response_type: "code",
    redirect_uri: params.redirectUri,
    code_challenge: params.codeChallenge,
    code_challenge_method: "S256"
  });
  
  if (params.state) searchParams.set("state", params.state);
  if (params.scopes?.length) searchParams.set("scope", params.scopes.join(" "));
  
  targetUrl.search = searchParams.toString();
  res.redirect(targetUrl.toString());
}
```

## 8. 実装のポイント

### エラーハンドリング

```typescript
// errors.tsからの抜粋
export class OAuthError extends Error {
  constructor(
    public readonly errorCode: string,
    message: string,
    public readonly errorUri?: string
  ) {
    super(message);
    this.name = this.constructor.name;
  }

  toResponseObject(): OAuthErrorResponse {
    const response: OAuthErrorResponse = {
      error: this.errorCode,
      error_description: this.message
    };

    if (this.errorUri) {
      response.error_uri = this.errorUri;
    }

    return response;
  }
}
```

### 型安全性

```typescript
// auth.tsからの抜粋
export const OAuthProtectedResourceMetadataSchema = z
  .object({
    resource: z.string().url(),
    authorization_servers: z.array(z.string().url()).optional(),
    // 他のフィールド...
  })
  .passthrough();

export type OAuthProtectedResourceMetadata = z.infer<typeof OAuthProtectedResourceMetadataSchema>;
```

以上が、MCP認可仕様の主要な機能についてのSDKコードレベルでの解説です。SDKは認可仕様の実装に必要なコンポーネントを体系的に提供しており、これらを組み合わせることで、MCP Server実装者は効率的に仕様準拠のサーバーを構築できます。


おっしゃる通りです。RFC8707（Resource Indicators for OAuth 2.0）の実装は非常に重要です。この仕様はトークンの対象リソースを明示的に指定し、トークンの不正転用を防止する重要なセキュリティ機能です。

# RFC8707: Resource Indicators for OAuth 2.0 の実装

## 1. 概要

RFC8707は以下の目的で使用されます：
- トークンの使用対象を特定のMCPサーバーに限定
- 混同代理問題（confused deputy problem）の対策
- トークンの不正転用防止

## 2. SDKでの実装方法

### 2.1 リソースパラメータの処理

```typescript
// 認可リクエスト時のリソースパラメータ処理
// authorize.tsに追加する実装例
router.all("/", async (req, res) => {
  // 既存の検証コード...
  
  // リソースパラメータの取得と検証
  const resource = req.query.resource || req.body.resource;
  
  if (!resource) {
    throw new InvalidRequestError("resource parameter is required");
  }
  
  // リソースURIの検証（正規化）
  let resourceUri: URL;
  try {
    resourceUri = new URL(resource.toString());
    // フラグメントは許可されない
    if (resourceUri.hash) {
      throw new Error("Resource URI must not contain fragments");
    }
  } catch (error) {
    throw new InvalidRequestError("Invalid resource URI");
  }
  
  // 認可処理の実行（リソースパラメータを追加）
  await provider.authorize(client, {
    state,
    scopes: requestedScopes,
    redirectUri: redirect_uri,
    codeChallenge: code_challenge,
    resource: resourceUri.toString() // リソースURIを追加
  }, res);
});
```

### 2.2 AuthorizationParamsの拡張

```typescript
// provider.tsの拡張
export type AuthorizationParams = {
  state?: string;
  scopes?: string[];
  codeChallenge: string;
  redirectUri: string;
  resource: string; // リソースURIを追加
};
```

### 2.3 トークン発行時のリソース処理

```typescript
// トークン発行時にリソースを含める実装例
async exchangeAuthorizationCode(client, code, codeVerifier, redirectUri) {
  // 認可コードの検証
  const authCode = await db.authCodes.findOne({ 
    code, 
    clientId: client.client_id,
    redirectUri
  });
  
  if (!authCode || authCode.expiresAt < Date.now()) {
    throw new InvalidGrantError("Invalid or expired authorization code");
  }
  
  // アクセストークンの生成（リソースをaudienceに含める）
  const accessToken = jwt.sign({
    client_id: client.client_id,
    scope: authCode.scopes.join(" "),
    aud: authCode.resource, // リソースURIをaudienceとして設定
    iss: "https://auth.example.com" // 発行者
  }, privateKey, {
    algorithm: "RS256",
    expiresIn: 3600 // 1時間
  });
  
  // 残りのトークン発行処理...
  
  return {
    access_token: accessToken,
    token_type: "Bearer",
    expires_in: 3600,
    refresh_token: refreshToken,
    scope: authCode.scopes.join(" "),
    resource: authCode.resource // レスポンスにリソースURIを含める
  };
}
```

### 2.4 トークン検証時のリソース検証

```typescript
// トークン検証時にリソース（audience）を検証
async verifyAccessToken(token: string): Promise<AuthInfo> {
  try {
    // JWTの検証
    const decoded = jwt.verify(token, publicKey, {
      algorithms: ["RS256"]
    });
    
    // audienceの検証（必須）
    const serverUrl = "https://mcp.example.com"; // このMCPサーバーのURL
    
    // audienceが配列の場合と文字列の場合の両方に対応
    const audiences = Array.isArray(decoded.aud) ? decoded.aud : [decoded.aud];
    
    // このサーバーのURLがaudienceに含まれているか確認
    if (!audiences.includes(serverUrl)) {
      throw new InvalidTokenError("Token was not issued for this resource");
    }
    
    return {
      token,
      clientId: decoded.client_id,
      scopes: decoded.scope.split(" "),
      expiresAt: decoded.exp,
      // リソース情報を追加
      extra: {
        resource: serverUrl,
        issuer: decoded.iss
      }
    };
  } catch (error) {
    if (error instanceof InvalidTokenError) {
      throw error;
    }
    throw new InvalidTokenError("Invalid token");
  }
}
```

### 2.5 リソースメタデータへの反映

```typescript
// リソースメタデータにリソースURIを明示的に含める
const protectedResourceMetadata: OAuthProtectedResourceMetadata = {
  resource: "https://mcp.example.com", // このMCPサーバーの正規URI
  authorization_servers: [
    "https://auth.example.com"
  ],
  // 他のメタデータ...
};
```

## 3. ProxyOAuthServerProviderでのリソース処理

```typescript
// プロキシ実装でのリソース処理
async exchangeAuthorizationCode(
  client: OAuthClientInformationFull,
  authorizationCode: string,
  codeVerifier?: string,
  redirectUri?: string
): Promise<OAuthTokens> {
  const params = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: client.client_id,
    code: authorizationCode,
    // リソースパラメータを追加
    resource: "https://mcp.example.com" // このMCPサーバーのURL
  });

  // 他のパラメータ設定...

  const response = await fetch(this._endpoints.tokenUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: params.toString(),
  });

  // レスポンス処理...
}
```

## 4. 実装のポイント

### 4.1 リソースURIの正規化

```typescript
// リソースURIの正規化関数
function normalizeResourceUri(uri: string): string {
  const url = new URL(uri);
  
  // スキームとホストを小文字に変換
  const normalizedUrl = new URL(
    `${url.protocol.toLowerCase()}//${url.host.toLowerCase()}${url.pathname}${url.search}`
  );
  
  // 末尾のスラッシュを削除（セマンティック上重要でない場合）
  let path = normalizedUrl.pathname;
  if (path.length > 1 && path.endsWith('/')) {
    normalizedUrl.pathname = path.slice(0, -1);
  }
  
  return normalizedUrl.toString();
}
```

### 4.2 複数リソースの処理

```typescript
// 複数リソースをサポートする場合の実装例
router.all("/authorize", async (req, res) => {
  // 既存の検証コード...
  
  // 複数リソースパラメータの取得
  const resources = Array.isArray(req.query.resource) 
    ? req.query.resource 
    : (req.query.resource ? [req.query.resource] : []);
  
  if (resources.length === 0) {
    throw new InvalidRequestError("At least one resource parameter is required");
  }
  
  // リソースURIの検証
  const validatedResources = resources.map(resource => {
    try {
      const resourceUri = new URL(resource.toString());
      if (resourceUri.hash) {
        throw new Error("Resource URI must not contain fragments");
      }
      return resourceUri.toString();
    } catch (error) {
      throw new InvalidRequestError(`Invalid resource URI: ${resource}`);
    }
  });
  
  // 認可処理の実行
  await provider.authorize(client, {
    // 他のパラメータ...
    resources: validatedResources // 複数リソースを追加
  }, res);
});
```

### 4.3 トークン検証の厳格化

```typescript
// トークン検証の厳格化
function validateTokenAudience(token: any, expectedResource: string): boolean {
  // audienceが配列の場合と文字列の場合の両方に対応
  const audiences = Array.isArray(token.aud) ? token.aud : [token.aud];
  
  // 正規化して比較
  const normalizedExpected = normalizeResourceUri(expectedResource);
  const normalizedAudiences = audiences.map(aud => normalizeResourceUri(aud));
  
  return normalizedAudiences.includes(normalizedExpected);
}
```

RFC8707の実装は、トークンの不正転用を防止するための重要なセキュリティ機能です。MCP仕様では、クライアントはリソースパラメータを使用して、トークンの使用対象を明示的に指定し、サーバーはトークン検証時にこの情報を確認する必要があります。これにより、あるMCPサーバー向けに発行されたトークンが別のMCPサーバーで使用されることを防止できます。

## まとめ

本 Chapter Streamable HTTP の詳細実装について主に `認可` の側面から解説しました。

はい、その理解で正確です。TypeScript SDKは以下の点で認可仕様を満たす実装を可能にしています：

1. **インターフェース定義**
   - `OAuthServerProvider`、`OAuthRegisteredClientsStore`などの主要インターフェースが定義されており、実装者はこれらを実装することで認可仕様に準拠できます
   - 必要なメソッド（`verifyAccessToken`、`authorize`など）のシグネチャが明確に定義されています

2. **コンポーネント提供**
   - `mcpAuthRouter`、`mcpAuthMetadataRouter`などのルーターコンポーネントが提供されており、標準エンドポイントを簡単に設定できます
   - 各種ハンドラー（`authorizationHandler`、`tokenHandler`など）が提供されており、OAuth 2.1フローの実装が容易です

3. **拡張ポイント**
   - RFC8707（Resource Indicators）のような仕様に対応するための拡張ポイントが用意されています
   - 例えば、`AuthorizationParams`インターフェースを拡張して`resource`パラメータを追加できます

4. **実装の自由度**
   - SDKはインターフェースと基本コンポーネントを提供していますが、具体的な実装（トークン生成、検証ロジックなど）は実装者に委ねられています
   - これにより、各MCP Server実装者は自身のユースケースに合わせた実装が可能です

ただし、いくつかの点に注意が必要です：

1. **実装責任**
   - SDKはフレームワークを提供していますが、セキュリティ要件を満たす具体的な実装（トークンのaudience検証など）は実装者の責任です
   - 例えば、RFC8707に準拠するためには、トークン検証時にリソースURIの検証を実装する必要があります

2. **拡張が必要な部分**
   - 一部の機能（例：RFC8707のリソースパラメータ処理）については、SDKのインターフェースを拡張する必要があるかもしれません
   - 例示したコードの一部は、現在のSDKに追加実装が必要な部分を含んでいます

3. **統合の複雑さ**
   - 既存の認証システムと統合する場合、`ProxyOAuthServerProvider`のようなコンポーネントを使用できますが、適切な設定と実装が必要です

つまり、SDKは認可仕様を満たすための基盤とフレームワークを提供していますが、完全な実装を提供しているわけではありません。実装者はSDKを利用して、仕様に準拠した実装を行う必要があります。これは意図的な設計で、様々なユースケースや既存システムとの統合に柔軟に対応できるようになっています。


## SaaS コラム

本書では **SaaS コラム** で本文内容を補足する SaaS に関する解説を行います。

今回は SaaS に関する解説はありません。