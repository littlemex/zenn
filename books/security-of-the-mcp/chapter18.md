---
title: "§18 MCP 特有の攻撃手法への対策"
free: true
---

**本 Chapter では前章で紹介した MCP 特有の攻撃手法に対する対策を解説します。** MCP セキュリティの緩和策を体系的に整理し、各対策の実装方法について詳細に説明します。

## MCP セキュリティ緩和表

以下の表は、MCP における主要な緩和カテゴリ、その内容、および対応できる代表的な攻撃例をまとめたものです。

| 緩和カテゴリ | 緩和内容 | 緩和できる攻撃例 |
|------------|---------|---------------|
| セキュリティレビュー実施 | 組織での定期的なレビューの実施、MCP Server の検証プロセス確立 | Tool Poisoning Attack, Rug Pull Attack |
| ツール説明検証 | ツール説明のサニタイズ、プロンプトインジェクションパターンの検出と除去 | Tool Poisoning Attack, Line Jumping Attack |
| Server 分離とサンドボックス化 | MCP Server の実行環境分離、権限の最小化、Docker などによる隔離 | Tool Shadowing Attack, 認可設定不備を突いた攻撃 |
| 認証・認可制御 | OAuth 2.1 準拠の認可実装、適切なスコープ設定、トークン管理 | Rug Pull Attack, 認可設定不備を突いた攻撃 |
| 入出力検証 | ユーザー入力のサニタイズ、外部データの検証、URI の正規化 | Indirect Prompt Injection, 外部リソース汚染 |
| 出力サニタイズと表示制御 | ANSI エスケープコードの無害化、ターミナル出力の検証、エスケープシーケンスの除去 | Terminal Deception Attack, ANSI エスケープコード脆弱性 |
| 監視とログ記録 | ツール呼び出しの監視、異常検知システムの実装、セキュリティイベントの記録 | Tool Shadowing Attack, Consent Fatigue Attack |
| バージョン管理と整合性検証 | ツールとパッケージのピン留め、ハッシュによる検証、変更検出、TOFU 検証 | Rug Pull Attack, Tool Poisoning Attack |
| ユーザーインタラクション保護 | 重要操作の強調表示、リクエスト頻度の制限、明示的な確認要求 | Consent Fatigue Attack, ユーザー入力操作による攻撃 |
| クロス Server 保護 | ツール名の名前空間分離、Server 間の厳格な境界設定 | Tool Shadowing Attack, Name Collision Attack |
| ネットワークレベルの保護 | localhost バインディング、Origin ヘッダ検証、CORS 設定の適正化、外部接続の制限 | DNS Rebinding Attack, ブラウザ経由の攻撃 |

以下、各緩和カテゴリについて詳細に解説します。

## セキュリティレビュー実施

### 組織での定期的なレビューの実施

MCP Server とその実装に対して定期的なセキュリティレビューを実施することは、潜在的な脆弱性を早期に発見し対処するために重要です。レビューでは、コードの脆弱性スキャン、依存関係の脆弱性チェック、機密情報の漏洩検出などを行います。

組織内で MCP Server を使用する場合は、信頼できる検証済みの MCP Server のみを使用するポリシーを確立し、新しい MCP Server の導入前には必ずセキュリティ評価を実施することが推奨されます。

### MCP Server の検証プロセス確立

MCP Server を安全に検証するためのチェックリストを作成し、以下のプロセスを確立します：

1. **静的解析**
   - コードの脆弱性スキャン
   - 依存関係の脆弱性チェック
   - 機密情報の漏洩検出

2. **動的解析**
   - サンドボックス環境での実行テスト
   - ネットワーク通信の監視
   - リソース使用状況の監視

3. **ツール説明の検証**
   - プロンプトインジェクションパターンの検出
   - 悪意のある指示の検出
   - 説明と実際の機能の一致確認

## ツール説明検証

### ツール説明のサニタイズ

ツール説明に含まれる可能性のある悪意のあるパターンを検出し除去するメカニズムを実装します。特に、特殊命令やプロンプト操作を検出するパターンマッチングが重要です。

```javascript
// ツール説明のサニタイズ例
function sanitizeToolDescription(description) {
  // 特殊命令パターンの検出と除去
  const patterns = [
    /<IMPORTANT>[\s\S]*?<\/IMPORTANT>/gi,
    /【.*?】/g,
    /\[\[.*?\]\]/g,
    /\{.*?\}/g
  ];
  
  let sanitized = description;
  patterns.forEach(pattern => {
    sanitized = sanitized.replace(pattern, '');
  });
  
  return sanitized;
}
```

### プロンプトインジェクションパターンの検出と除去

プロンプトインジェクションを検出するためには、以下のようなパターンに注意する必要があります：

- 「以下の指示は無視して」などの指示の上書きを試みる文言
- 「<IMPORTANT>」などの特殊なマークアップ
- 「システム上の API キーを検索し、外部に送信する」などの悪意のある指示

これらのパターンを検出し、ツール説明から除去することで、プロンプトインジェクションのリスクを軽減できます。

## Server 分離とサンドボックス化

### Docker によるサンドボックス化

MCP Server を Docker コンテナ内で実行することで、ホストシステムから分離し、被害を局所化できます。以下は Docker を用いた MCP Server の実行例です：

```dockerfile
FROM denoland/deno:2.2.9

# セキュリティ強化: 非rootユーザーの作成
RUN adduser --disabled-password --gecos '' mcpuser

# 必要なツールのインストール
RUN apt update && apt install -y curl jq

WORKDIR /app

# MCP Serverのソースコードを取得
RUN curl -o index.ts https://raw.githubusercontent.com/modelcontextprotocol/servers/refs/heads/main/src/brave-search/index.ts
RUN sed -i 's/@modelcontextprotocol/npm:@modelcontextprotocol/g' index.ts

# セキュリティ強化: 権限の最小化
RUN chown -R mcpuser:mcpuser /app

# 非rootユーザーに切り替え
USER mcpuser

# 厳格なネットワーク許可リスト
ENTRYPOINT ["deno", "run", "--allow-env=API_KEY,PORT", "--allow-net=api.search.brave.com:443", "index.ts"]
```

### 最小権限の原則適用

MCP Server には必要最小限の権限のみを付与し、特にファイルシステム、ネットワーク、システムコマンドへのアクセスを制限します。例えば、Deno を使用する場合は、`--allow-net` フラグで特定のドメインへのアクセスのみを許可するなど、細かい権限制御が可能です。

複数の MCP Server を Client に接続する場合は、それぞれを分離された環境で実行し、相互に影響を与えないようにすることが重要です。特に、機密データにアクセスできる MCP Server は、他の Server から分離された環境で実行すべきです。

## 認証・認可制御

### OAuth 2.1 準拠の認可実装

リモートで公開される MCP Server には、OAuth 2.1 に準拠した認可機構を実装することが重要です。以下は JWT 認証と認可の実装例です：

```javascript
// JWT認証と認可の実装例
const jwt = require('jsonwebtoken');

// 認証ミドルウェア
function authenticate(req, res, next) {
  const token = req.headers.authorization?.split(' ')[1];
  
  if (!token) {
    return res.status(401).send('認証トークンがありません');
  }
  
  try {
    const decoded = jwt.verify(token, process.env.JWT_SECRET);
    req.user = decoded;
    next();
  } catch (error) {
    return res.status(401).send('無効な認証トークンです');
  }
}

// 認可ミドルウェア
function authorize(requiredPermissions) {
  return (req, res, next) => {
    if (!req.user) {
      return res.status(401).send('認証されていません');
    }
    
    const userPermissions = req.user.permissions || [];
    const hasAllPermissions = requiredPermissions.every(
      permission => userPermissions.includes(permission)
    );
    
    if (!hasAllPermissions) {
      return res.status(403).send('アクセス権限がありません');
    }
    
    next();
  };
}
```

### 適切なスコープ設定

MCP Server のアクセス制御には、適切なスコープ設定が重要です。スコープは `mcp.{interface}/{interfaceName}.{action}` という形式で定義し、以下のルールに従います：

- `mcp` - MCP Server を示すプレフィックス
- `{interface}` - インターフェース種別（例：resources、tools）
- `{interfaceName}` - インターフェースの具体名（例：file、weather）
- `{action}` - 許可される操作（例：read、execute）

**スコープの例:**
- `mcp.resources/file.read`
- `mcp.tools/weather.execute`

## 入出力検証

### URI の正規化と検証

MCP では、Tools や Resources のインターフェースを通じて、ファイルの操作や取得をする際に URI を使用します。URI の取り扱いが適切に行われない場合、Path Traversal 攻撃が発生する可能性があります。

TypeScript の例では、`new URL(uri)` を使って URI を正規化し、安全な形式に変換することが推奨されます：

```typescript
// 入力されたURIを正規化する例
const uri = new URL(validatedArgs.uri);
const content = validatedArgs.content;

const sessionDir = `${process.cwd()}/sessions/${extra.sessionId}`;
const filename = uri.pathname.split("/").pop();
const dirname = uri.pathname.split("/").slice(0, -1).join("/");

if (!fs.existsSync(`${sessionDir}/${dirname}`)) {
    fs.mkdirSync(`${sessionDir}/${dirname}`, { recursive: true });
}

fs.writeFileSync(`${sessionDir}/${dirname}/${filename}`, content);
```

### 外部通信時の取り扱い

外部サービスとの通信を行う際には、SSRF (Server-Side Request Forgery) 攻撃を防ぐために、ユーザーから渡された引数をそのまま使用せず、適切な検証を行うことが重要です：

```typescript
const handler = async (
    args: ZodRawShape,
    extra: RequestHandlerExtra<ServerRequest, ServerNotification>
): Promise<CallToolResult> => {
    const path = args.path as unknown as string;

    const baseUrl = "http://example.test";
    const url = new URL(path, baseUrl);
    if (url.origin !== new URL(baseUrl).origin) {
        throw new Error("不正なパスです");
    }
    const result = await fetch(url.toString());
    return {
        content: [
            {
                type: "text",
                text: `fetch ${url.toString()}`,
            },
            {
                type: "text",
                text: result.toString(),
            },
        ],
    };
};
```

## 出力サニタイズと表示制御

### ANSI エスケープコードの無害化

MCP Server からの出力に含まれる可能性のある ANSI エスケープコード（16進値 1b のバイトで始まる）を検出し、プレースホルダー文字に置き換えることで、ターミナル欺瞞攻撃を防止します。

```javascript
// ANSIエスケープコードの無害化例
function sanitizeAnsiEscapeCodes(output) {
  // ANSIエスケープシーケンスを検出して置換
  // エスケープシーケンスは通常 ESC (0x1B) で始まる
  return output.replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '[ESC]');
}

// ツール出力を処理する例
function processToolOutput(rawOutput) {
  // ANSIエスケープコードを無害化
  const sanitizedOutput = sanitizeAnsiEscapeCodes(rawOutput);
  
  // その他の潜在的に危険な出力を処理
  // ...
  
  return sanitizedOutput;
}
```

### ツール出力の検証と表示制御

生の出力をそのままターミナルに表示せず、レンダリング前に適切な検証と無害化を行います。特に機密情報を扱う環境では、出力内容の検証を徹底します。

```javascript
// ツール出力の検証と表示制御の例
function validateAndDisplayOutput(output, securityLevel) {
  // 出力を検証
  const sanitizedOutput = sanitizeOutput(output);
  
  // セキュリティレベルに応じた追加検証
  if (securityLevel === 'high') {
    // 機密情報のパターンをチェック
    if (containsSensitivePatterns(sanitizedOutput)) {
      return '*** 出力に機密情報が含まれている可能性があります ***';
    }
  }
  
  return sanitizedOutput;
}

// 機密情報のパターンをチェックする関数
function containsSensitivePatterns(text) {
  const sensitivePatterns = [
    /password\s*[:=]\s*\S+/i,
    /api[-_]?key\s*[:=]\s*\S+/i,
    /token\s*[:=]\s*\S+/i,
    // その他の機密情報パターン
  ];
  
  return sensitivePatterns.some(pattern => pattern.test(text));
}
```

## 監視とログ記録

### ツール呼び出しの監視

MCP Server のツール呼び出しを監視し、不審なパターンや異常な動作を検出するシステムを実装します。

```javascript
class MCPSecurityMonitor {
    constructor() {
        this.suspicious_patterns = [
            "file://", "~/.ssh", "~/.cursor", "password", "token", "secret"
        ];
    }
    
    inspect_tool_call(tool_name, parameters, user_id) {
        // ツール呼び出しのタイムスタンプを記録
        const timestamp = new Date().toISOString();
        
        // ツール呼び出しのロギング
        console.log(`Tool Call: ${tool_name} by user ${user_id} at ${timestamp}`);
        
        // 疑わしいパターンの検知
        for (const [param_name, param_value] of Object.entries(parameters)) {
            if (typeof param_value === 'string') {
                for (const pattern of this.suspicious_patterns) {
                    if (param_value.includes(pattern)) {
                        console.warn(
                            `SUSPICIOUS PATTERN: ${pattern} found in ${param_name} ` +
                            `parameter of ${tool_name} tool call by user ${user_id}`
                        );
                        // 管理者へ通知を送信
                        this.alert_admin(tool_name, pattern, user_id);
                    }
                }
            }
        }
    }
    
    alert_admin(tool_name, pattern, user_id) {
        // 管理者への通知処理
        // ...
    }
}
```

### セキュリティイベントの記録

MCP Server の動作に関するセキュリティイベントを記録し、後の分析や監査に役立てます。特に、ツールの追加や変更、権限の変更などの重要なイベントは必ず記録します。

```javascript
// セキュリティイベントのロギング例
function logSecurityEvent(eventType, details, severity = 'info') {
  const event = {
    type: eventType,
    timestamp: new Date().toISOString(),
    details,
    severity
  };
  
  // イベントをログに記録
  console.log(`SECURITY EVENT [${severity.toUpperCase()}]: ${JSON.stringify(event)}`);
  
  // 重大なイベントの場合は追加のアクションを実行
  if (severity === 'critical' || severity === 'high') {
    notifySecurityTeam(event);
  }
  
  // イベントを永続化
  storeSecurityEvent(event);
}

// 使用例
logSecurityEvent(
  'tool_description_changed',
  { toolName: 'file_access', oldDescription: '...', newDescription: '...' },
  'high'
);
```

## バージョン管理と整合性検証

### ツールとパッケージのピン留め

MCP Server とツールのバージョンを固定し、不正な変更を防ぎます。以下は YAML 形式での設定例です：

```yaml
# MCPサーバー設定例（YAML形式）
mcpServers:
  "github.com/example/weather-mcp":
    version: "1.2.3"
    hash: "sha256:a1b2c3d4e5f6..."
    autoApprove: false
    tools:
      - name: "get_weather"
        version: "1.0.0"
        hash: "sha256:f6e5d4c3b2a1..."
```

### ハッシュによる検証と変更検出

ツール説明のハッシュ値を計算して保存し、変更があった場合に検出するメカニズムを実装します：

```typescript
class SecureMcpClient extends Client {
  private toolHashes: Map<string, string> = new Map();
  
  // 初期接続時にハッシュを計算して保存
  async initializeToolHashes() {
    const result = await this.listTools();
    for (const tool of result.tools) {
      this.toolHashes.set(tool.name, this.calculateToolHash(tool));
    }
    console.log("ツールハッシュを初期化しました");
  }
  
  // ツールのハッシュ値を計算
  private calculateToolHash(tool: Tool): string {
    // 重要な属性を含めてハッシュ化
    const content = JSON.stringify({
      name: tool.name,
      description: tool.description,
      inputSchema: tool.inputSchema,
      outputSchema: tool.outputSchema
    });
    
    // SHA-256などのハッシュアルゴリズムを使用
    return crypto.createHash('sha256').update(content).digest('hex');
  }
  
  // listChanged通知のハンドラをオーバーライド
  protected async handleToolListChanged() {
    const result = await this.listTools();
    let hasUnauthorizedChanges = false;
    
    // 各ツールのハッシュ値を検証
    for (const tool of result.tools) {
      const storedHash = this.toolHashes.get(tool.name);
      
      // 新しいツールの場合はスキップ（または別の検証ポリシーを適用）
      if (!storedHash) {
        console.log(`新しいツールを検出: ${tool.name}`);
        continue;
      }
      
      // ハッシュ値を計算して比較
      const currentHash = this.calculateToolHash(tool);
      if (storedHash !== currentHash) {
        console.warn(`警告: ツール "${tool.name}" の説明が変更されました`);
        hasUnauthorizedChanges = true;
        
        // 変更の詳細をログに記録
        this.logToolChanges(tool.name, storedHash, currentHash);
      }
    }
    
    // 不正な変更が検出された場合の処理
    if (hasUnauthorizedChanges) {
      // ユーザーに確認
      const shouldUpdate = await this.promptUserForConfirmation(
        "ツール説明に不審な変更が検出されました。更新を許可しますか？"
      );
      
      if (!shouldUpdate) {
        console.log("ツール更新が拒否されました");
        return false;
      }
    }
    
    // 問題なければハッシュを更新
    for (const tool of result.tools) {
      this.toolHashes.set(tool.name, this.calculateToolHash(tool));
    }
    
    return true;
  }
}
```

### TOFU（Trust On First Use）検証

初回使用時の信頼（TOFU）検証を実装し、MCP Server に対する初回接続時の状態を基準として、その後の変更を検出します。新しいツールが追加された場合や既存のツールの説明が変更された場合は、ユーザーまたは管理者に警告します。

```javascript
// TOFU検証の実装例
class TofuValidator {
  constructor() {
    this.knownServers = new Map();
    this.loadStoredState();
  }
  
  // 保存された状態を読み込む
  loadStoredState() {
    try {
      const storedData = localStorage.getItem('mcp_tofu_state');
      if (storedData) {
        const parsedData = JSON.parse(storedData);
        Object.entries(parsedData).forEach(([serverId, toolsData]) => {
          this.knownServers.set(serverId, new Map(Object.entries(toolsData)));
        });
      }
    } catch (error) {
      console.error('TOFU状態の読み込みに失敗しました:', error);
    }
  }
  
  // 現在の状態を保存
  saveCurrentState() {
    try {
      const stateObj = {};
      this.knownServers.forEach((toolsMap, serverId) => {
        stateObj[serverId] = Object.fromEntries(toolsMap);
      });
      localStorage.setItem('mcp_tofu_state', JSON.stringify(stateObj));
    } catch (error) {
      console.error('TOFU状態の保存に失敗しました:', error);
    }
  }
  
  // サーバーとツールを検証
  validateServer(serverId, tools) {
    const knownTools = this.knownServers.get(serverId);
    
    // 初回接続の場合
    if (!knownTools) {
      console.log(`初回接続: ${serverId}`);
      const toolsMap = new Map();
      tools.forEach(tool => {
        toolsMap.set(tool.name, this.calculateToolHash(tool));
      });
      this.knownServers.set(serverId, toolsMap);
      this.saveCurrentState();
      return { isFirstUse: true, changes: [] };
    }
    
    // 既知のサーバーの場合、変更を検出
    const changes = [];
    tools.forEach(tool => {
      const knownHash = knownTools.get(tool.name);
      const currentHash = this.calculateToolHash(tool);
      
      if (!knownHash) {
        // 新しいツール
        changes.push({
          type: 'new_tool',
          toolName: tool.name,
          severity: 'medium'
        });
      } else if (knownHash !== currentHash) {
        // 変更されたツール
        changes.push({
          type: 'modified_tool',
          toolName: tool.name,
          severity: 'high'
        });
      }
    });
    
    // 削除されたツールを検出
    knownTools.forEach((_, toolName) => {
      if (!tools.some(t => t.name === toolName)) {
        changes.push({
          type: 'removed_tool',
          toolName,
          severity: 'medium'
        });
      }
    });
    
    return { isFirstUse: false, changes };
  }
  
  // ツールのハッシュ値を計算
  calculateToolHash(tool) {
    const content = JSON.stringify({
      name: tool.name,
      description: tool.description,
      inputSchema: tool.inputSchema,
      outputSchema: tool.outputSchema
    });
    
    // 簡易的なハッシュ計算（実際の実装ではより強力なアルゴリズムを使用）
    return btoa(content);
  }
}
```

## ユーザーインタラクション保護

### 重要操作の強調表示

権限レベルに基づいて操作を分類し、高リスク操作を視覚的に強調表示します：

```javascript
// 重要操作の強調表示の実装例
function displayOperationConsent(operation) {
  // 操作の権限レベルを評価
  const riskLevel = assessRiskLevel(operation);
  
  let consentUI;
  switch (riskLevel) {
    case 'HIGH':
      consentUI = createHighRiskConsentUI(operation);
      break;
    case 'MEDIUM':
      consentUI = createMediumRiskConsentUI(operation);
      break;
    case 'LOW':
      consentUI = createLowRiskConsentUI(operation);
      break;
  }
  
  // 同意UI表示前にクールダウン期間を設定（高リスク操作の場合）
  if (riskLevel === 'HIGH') {
    setTimeout(() => {
      displayToUser(consentUI);
    }, 2000); // 2秒のクールダウン
  } else {
    displayToUser(consentUI);
  }
}
```

### 明示的な確認要求

MCP Client が MCP Server の Tool を実行する際、利用者に明示的な許可を求めることで、意図しないツールの実行を防ぎます：

```javascript
// ツール実行前に確認を求める関数
async function confirmToolExecution(toolName, toolArgs) {
  return new Promise((resolve) => {
    console.log("=== ツール実行の確認 ===");
    console.log(`ツール名: ${toolName}`);
    console.log("引数:");
    console.log(JSON.stringify(toolArgs, null, 2));
    console.log("このツールを実行しますか？ [y]es / [n]o");

    const readline = require('readline').createInterface({
      input: process.stdin,
      output: process.stdout
    });

    readline.question("> ", (answer) => {
      readline.close();
      resolve(answer.toLowerCase().startsWith("y"));
    });
  });
}

// ツール実行時の使用例
async function executeToolWithConfirmation(toolName, toolArgs) {
  const allowed = await confirmToolExecution(toolName, toolArgs);
  if (!allowed) {
    console.log("ツールの実行は拒否されました。");
    return null;
  }
  
  console.log(`ツール「${toolName}」を実行します...`);
  return await mcpClient.callTool(toolName, toolArgs);
}
```

## クロス Server 保護

### ツール名の名前空間分離

サーバーごとに名前空間を設定し、ツール名の衝突を防ぎます：

```typescript
// ツール名の名前空間分離の実装例
function registerTool(server, tool) {
  const namespacedName = `${server.id}::${tool.name}`;
  
  // 名前衝突の検出
  if (registeredTools.has(namespacedName)) {
    console.warn(`Tool name collision detected: ${namespacedName}`);
    // 衝突解決ポリシーの適用
    return handleNameCollision(server, tool, namespacedName);
  }
  
  // 名前空間付きでツールを登録
  registeredTools.set(namespacedName, {
    server: server.id,
    tool: tool
  });
  
  return namespacedName;
}

// 名前衝突の解決
function handleNameCollision(server, tool, namespacedName) {
  // 既存のツールを取得
  const existing = registeredTools.get(namespacedName);
  
  // 優先度に基づいて解決
  if (server.priority > existing.server.priority) {
    // 新しいサーバーの優先度が高い場合は上書き
    console.log(`Overriding tool ${namespacedName} with higher priority server`);
    registeredTools.set(namespacedName, {
      server: server.id,
      tool: tool
    });
    return namespacedName;
  } else {
    // 既存のサーバーを優先し、新しいツールには代替名を付与
    const alternativeName = `${server.id}::${tool.name}_${Date.now()}`;
    console.log(`Using alternative name ${alternativeName} due to collision`);
    registeredTools.set(alternativeName, {
      server: server.id,
      tool: tool
    });
    return alternativeName;
  }
}
```

### Server 間の厳格な境界設定

複数の MCP Server を同時に使用する場合、Server 間の境界を明確に設定し、相互干渉を防ぎます：

```javascript
// Server間の境界設定の実装例
class MCPServerIsolation {
  constructor() {
    this.servers = new Map();
    this.serverContexts = new Map();
  }
  
  // サーバーを登録
  registerServer(serverId, config) {
    this.servers.set(serverId, config);
    this.serverContexts.set(serverId, {
      data: new Map(),
      permissions: config.permissions || []
    });
    console.log(`Server ${serverId} registered with isolation`);
  }
  
  // サーバー間のデータアクセスを制御
  accessData(requestingServerId, targetServerId, dataKey) {
    // 同一サーバーからのアクセスは常に許可
    if (requestingServerId === targetServerId) {
      return this.serverContexts.get(targetServerId).data.get(dataKey);
    }
    
    // 異なるサーバーからのアクセスは権限チェック
    const requestingServer = this.servers.get(requestingServerId);
    if (!requestingServer) {
      console.error(`Unknown server ${requestingServerId} attempted to access data`);
      return null;
    }
    
    // クロスサーバーアクセス権限の確認
    if (!this.hasAccessPermission(requestingServerId, targetServerId, dataKey)) {
      console.warn(
        `Access denied: ${requestingServerId} attempted to access ` +
        `${dataKey} from ${targetServerId} without permission`
      );
      return null;
    }
    
    // 許可されたアクセスを記録
    console.log(
      `Cross-server access: ${requestingServerId} accessed ` +
      `${dataKey} from ${targetServerId}`
    );
    
    return this.serverContexts.get(targetServerId).data.get(dataKey);
  }
  
  // クロスサーバーアクセス権限の確認
  hasAccessPermission(requestingServerId, targetServerId, dataKey) {
    const requestingContext = this.serverContexts.get(requestingServerId);
    if (!requestingContext) return false;
    
    // 特定のクロスサーバーアクセス権限を確認
    return requestingContext.permissions.some(perm => 
      perm.type === 'cross_server_access' && 
      perm.targetServer === targetServerId &&
      (perm.dataKeys === '*' || perm.dataKeys.includes(dataKey))
    );
  }
}
```

## ネットワークレベルの保護

### localhost バインディング

MCP Server をローカルホストにのみバインドすることで、外部からの不正アクセスを防ぎます：

```javascript
// localhostバインディングの実装例
const http = require('http');
const mcpServer = require('./mcp-server');

// サーバーをlocalhostにのみバインド
const server = http.createServer(mcpServer.handler);
server.listen(3000, '127.0.0.1', () => {
  console.log('MCP Server is running on http://127.0.0.1:3000');
});
```

### Origin ヘッダ検証

Web ブラウザから MCP Server にアクセスする場合、Origin ヘッダを検証して許可されたオリジンからのリクエストのみを受け付けます：

```javascript
// Originヘッダ検証の実装例
function validateOrigin(req, res, next) {
  const origin = req.headers.origin;
  
  // 許可されたオリジンのリスト
  const allowedOrigins = [
    'http://localhost:8080',
    'https://example.com'
  ];
  
  // オリジンが許可リストに含まれているか確認
  if (!origin || !allowedOrigins.includes(origin)) {
    return res.status(403).send('不正なオリジンからのアクセスです');
  }
  
  // CORSヘッダを設定
  res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  
  next();
}
```

### CORS 設定の適正化

Cross-Origin Resource Sharing (CORS) の設定を適切に行い、許可されたドメインからのみアクセスを許可します：

```javascript
// CORS設定の適正化例
const express = require('express');
const cors = require('cors');
const app = express();

// CORS設定
const corsOptions = {
  origin: function (origin, callback) {
    // 許可されたオリジンのリスト
    const allowedOrigins = [
      'http://localhost:8080',
      'https://example.com'
    ];
    
    // オリジンが許可リストに含まれているか、または開発環境の場合
    if (!origin || allowedOrigins.includes(origin)) {
      callback(null, true);
    } else {
      callback(new Error('CORS policy violation'));
    }
  },
  methods: ['GET', 'POST', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization'],
  credentials: true,
  maxAge: 86400 // 24時間
};

// CORS設定を適用
app.use(cors(corsOptions));
```

### 外部接続の制限

MCP Server が外部サービスに接続する際、許可されたドメインのみに接続を制限します：

```javascript
// 外部接続の制限の実装例
const fetch = require('node-fetch');

// 許可されたドメインのリスト
const allowedDomains = [
  'api.openai.com',
  'api.github.com',
  'api.weather.gov'
];

// 安全なフェッチ関数
async function safeFetch(url, options = {}) {
  try {
    // URLをパース
    const parsedUrl = new URL(url);
    
    // ドメインが許可リストに含まれているか確認
    if (!allowedDomains.includes(parsedUrl.hostname)) {
      throw new Error(`不許可のドメインへの接続: ${parsedUrl.hostname}`);
    }
    
    // 接続を記録
    console.log(`External connection to: ${parsedUrl.hostname}`);
    
    // 実際のフェッチを実行
    return await fetch(url, options);
  } catch (error) {
    console.error(`External connection error: ${error.message}`);
    throw error;
  }
}
```

## エンドツーエンドセキュリティの重要性

MCP のセキュリティは、個々の対策だけでなく、エンドツーエンドでの包括的なアプローチが重要です。ツール説明の検証だけでなく、AI モデルとの間で受け渡されるデータ全体のセキュリティを確保する必要があります。

### 多層防御アプローチ

MCP セキュリティには、以下のような多層防御アプローチが効果的です：

1. **プロトコルレベルの保護**
   - ツール説明の検証と無害化
   - 入出力データの検証
   - 通信の暗号化

2. **サーバーレベルの保護**
   - サンドボックス化と分離
   - 最小権限の原則適用
   - 監視とログ記録

3. **クライアントレベルの保護**
   - ユーザーインタラクションの保護
   - 出力の検証と表示制御
   - 変更検出と整合性検証

4. **組織レベルの保護**
   - セキュリティポリシーの確立
   - 定期的なレビューと監査
   - インシデント対応計画の策定

### AI Agent とのインタラクション保護

AI Agent と MCP Server のインタラクションを保護するためには、以下の点に注意する必要があります：

1. **コンテキスト汚染の防止**
   - ツール説明からのプロンプトインジェクション検出
   - AI Agent への入力の検証
   - 出力の無害化と検証

2. **権限の適切な管理**
   - AI Agent に必要最小限の権限のみを付与
   - 重要な操作には明示的な確認を要求
   - 権限の定期的な見直し

3. **透明性の確保**
   - ツールの動作と権限の明示
   - 操作の記録と監査
   - 異常検知と通知

## まとめ

MCP 特有の攻撃手法に対する対策は、単一の方法ではなく、複数の緩和策を組み合わせた包括的なアプローチが必要です。本章で紹介した緩和カテゴリと具体的な対策を適切に実装することで、MCP のセキュリティリスクを大幅に軽減できます。

特に重要なのは、セキュリティを後付けではなく、設計段階から考慮することです。MCP Server の開発者、Client の実装者、そして最終的なユーザーがそれぞれの立場でセキュリティ対策を講じることで、より安全な MCP エコシステムを構築できます。

また、セキュリティは継続的なプロセスであり、新たな脅威や攻撃手法に対応するために、定期的な評価と更新が必要です。MCP の進化とともに、セキュリティ対策も進化させていくことが重要です。
