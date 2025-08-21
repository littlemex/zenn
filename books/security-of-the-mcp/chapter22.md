---
title: "§22 Amazon Bedrock AgentCore Gateway"
free: true
---

___MCP セキュリティに関する包括的な実装編:___ _MCP のセキュリティに対しての包括的な実装に関する解説_

---

**本 Chapter では Amazon Bedrock AgentCore Gateway について解説します。** 

![200101](/images/books/security-of-the-mcp/fig_c20_s01_01.png)

## Amazon Bedrock AgentCore Gateway の概要

Amazon Bedrock AgentCore Gateway は、AI Agent が外部ツールやリソースに安全に接続するためのマネージドサービスです。現在プレビューリリース段階にあり、仕様は変更される可能性があります。Gateway は MCP（Model Context Protocol）を使用して、AI モデルと様々なデータソースやツールを橋渡しする役割を担います。

### 前提条件と必要な権限

Gateway を使用するには、AWS アカウントで以下の権限が必要です。IAM ロールの作成権限、Lambda 関数の作成権限、Cognito リソースの作成権限、そして Bedrock AgentCore API の使用権限です。開発環境では Python 3.6 以上と boto3 のインストールが必要となります。

### Gateway の基本構成

Gateway は複数の Target を持つことができ、各 Target は異なるタイプのリソースやサービスに接続します。主な Target タイプは Lambda Target、Smithy API Model Target、OpenAPI Model Target の 3 種類です。Gateway 作成時には OAuth 認証設定が必須となり、Cognito を使用した認証サーバーの設定が必要です。

### Lambda Target の特徴

Lambda Target では、独自の Lambda 関数を指定するか、システムが自動生成する Lambda を使用できます。Tool Schema を定義することで、各ツールの名前、説明、入力スキーマを明確に指定します。Lambda ARN を指定しない場合、システムが自動的に Lambda 関数を作成し、基本的なツール実装を提供します。

### API Model Target の活用

Smithy API Model Target では、AWS サービスの API モデルを直接利用できます。デフォルトでは DynamoDB の API モデルが使用されますが、S3 URI からカスタム Smithy モデルを読み込むことも可能です。AWS の数百のサービスの Smithy API モデルが GitHub で公開されており、これらを活用することで AWS サービスとの統合が容易になります。

OpenAPI Model Target では、外部 API との接続が可能です。API キー認証と OAuth 認証の両方をサポートしており、認証情報は Header や Query Parameter として設定できます。Brave Search のような外部サービスの API を Gateway 経由で AI Agent から利用できるようになります。

### Agent との統合方法

Gateway を AI Agent で使用する際は、Gateway URL とアクセストークンが必要です。Strands Agent フレームワークを使用する場合、MCP Client を初期化し、利用可能なツール一覧を取得してから Agent に統合します。Gateway は pagination をサポートしており、大量のツールがある場合でも効率的に取得できます。

## Gateway の核となる概念

### Gateway の役割と構造

Gateway は MCP Server として機能し、AI Agent がツールを発見し相互作用するための標準化されたアクセスポイントを提供します。単一の Gateway は複数の Target を持つことができ、各 Target は異なるツールまたはツールセットを表現します。この設計により、Agent は一つのエンドポイントから多様なツールにアクセスできるようになります。

### Gateway Target の詳細

Target は Gateway が Agent に提供する API や Lambda 関数を定義します。Target の種類には Lambda 関数、OpenAPI 仕様、Smithy モデル、その他のツール定義があります。各 Target は独立して設定でき、異なる認証方式や実行環境を持つことができます。

### 認証システムの要件

MCP は OAuth のみをサポートするため、すべての Gateway には OAuth Authorizer の接続が必須です。既存の OAuth 認証サーバーがない場合、Cognito を使用して認証サーバーを作成できます。この制約により、Gateway へのアクセスは適切に認証されたクライアントのみに制限されます。

### 認証情報管理の仕組み

Gateway が API や Lambda 関数を呼び出す際は、適切な認証情報が必要です。Smithy や Lambda Target の場合、Gateway は接続された実行ロールを使用してこれらの Target を呼び出します。OpenAPI Target の場合、API キーや OAuth 認証情報を格納する AgentCore Credential Provider を接続する必要があります。

## サポートされるツールタイプ

### OpenAPI 仕様による統合

既存の REST API を OpenAPI 仕様を提供することで MCP 互換ツールに変換できます。Gateway は MCP と REST 形式間の変換を自動的に処理し、既存の API インフラストラクチャを活用できます。この方式では、API の詳細な仕様定義が重要となり、適切なスキーマ定義により AI Agent が API を効果的に利用できるようになります。

### Lambda 関数の活用

Lambda 関数をツールとして接続することで、好みのプログラミング言語でカスタムビジネスロジックを実装できます。Gateway は Lambda 関数を呼び出し、レスポンスを MCP 形式に変換します。この方式では、複雑な処理ロジックや外部システムとの統合を Lambda 内で実装できる柔軟性があります。

### Smithy モデルによる定義

Smithy モデルを使用して API インターフェースを定義し、MCP 互換ツールを生成できます。Smithy は AWS サービスで使用されるサービスと SDK を定義するための言語です。Gateway は Smithy モデルを使用して AWS サービスやカスタム API と相互作用するツールを生成できます。この方式では、型安全性と一貫性のある API 定義が可能になります。

## Gateway の構築プロセス

### Gateway ワークフロー

Gateway の構築は以下の4つのステップで構成されます：

1. **ツールの作成** - OpenAPI 仕様（REST API用）や JSON スキーマ（Lambda 関数用）を使用してツールを定義します。これらの仕様は Amazon Bedrock AgentCore によって解析され、Gateway の作成に使用されます。

2. **Gateway エンドポイントの作成** - AWS コンソールまたは AWS SDK を使用して、MCP エントリーポイントとなる Gateway を作成します。各 API エンドポイントや関数は MCP 互換ツールとなり、MCP サーバー URL を通じて利用可能になります。Gateway のセキュリティには Inbound Auth を使用してアクセス制御を行います。

3. **Target の追加** - Gateway がリクエストを特定のツールにルーティングする方法を定義する Target を設定します。認証されたユーザーに代わってバックエンドリソースに安全に接続するために Outbound Auth を使用します。Inbound Auth と Outbound Auth を組み合わせることで、ユーザーとターゲットリソース間の安全なブリッジを構築し、IAM 認証情報と OAuth ベースの認証フローの両方をサポートします。

4. **Agent コードの更新** - Agent を Gateway エンドポイントに接続し、統一された MCP インターフェースを通じてすべての設定済みツールにアクセスできるようにします。

### セキュリティ設計の重要性

Gateway を設定する前に、適切な権限の指定方法を理解することが重要です。これにより、Gateway を適切に保護できます。Inbound Auth はゲートウェイへの入力を制御し、Outbound Auth はバックエンドリソースへの安全な接続を確保します。

## Gateway の権限管理

### 権限の3つのカテゴリ

Gateway を使用する際は、以下の3つの権限カテゴリを考慮する必要があります：

1. **Gateway 管理権限** - Gateway の作成と管理に必要な権限
2. **Gateway アクセス権限（Inbound Auth）** - MCP プロトコル経由で誰が何を呼び出せるかを制御
3. **Gateway 実行権限（Outbound Auth）** - Gateway が他のリソースやサービスでアクションを実行するために必要な権限

### Gateway 管理権限

Gateway の作成と管理には、以下のような IAM ポリシーが必要です：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:*Gateway*",
        "bedrock-agentcore:*WorkloadIdentity",
        "bedrock-agentcore:*CredentialProvider",
        "bedrock-agentcore:*Token*",
        "bedrock-agentcore:*Access*"
      ],
      "Resource": "arn:aws:bedrock-agentcore:*:*:*gateway*"
    }
  ]
}
```

関連サービスの追加権限も必要になる場合があります：
- S3 ベースの Target 設定時：`s3:GetObject`、`s3:PutObject`
- 暗号化操作：`kms:Encrypt`、`kms:Decrypt`、`kms:GenerateDataKey*`
- その他のサービス固有の権限

### Gateway アクセス権限（Inbound Auth）

他の AWS サービスとは異なり、Gateway は MCP で指定された JWT トークンベース認証を使用します。これらの設定は Gateway のプロパティとして指定する必要があります。標準的な AWS IAM メカニズムではなく、MCP プロトコルに準拠した認証方式を採用しています。

### Gateway 実行権限（Outbound Auth）

Gateway 作成時には、Gateway が AWS リソースや外部サービスにアクセスするための実行ロールを提供する必要があります。このロールは、Gateway が他のサービスにリクエストを行う際の権限を定義します。

実行ロールには、最低限以下の信頼ポリシーが必要です：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "GatewayAssumeRolePolicy",
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock-agentcore.amazonaws.com"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "{{accountId}}"
        },
        "ArnLike": {
          "aws:SourceArn": "arn:aws:bedrock-agentcore:{{region}}:{{accountId}}:gateway/{{gatewayName}}-*"
        }
      }
    }
  ]
}
```

### セキュリティベストプラクティス

1. **最小権限の原則**
   - Gateway の機能に必要な権限のみを付与
   - 可能な限りワイルドカードではなく具体的なリソース ARN を使用
   - 権限の定期的なレビューと監査

2. **機能別のロール分離**
   - 管理用と実行用で異なるロールを使用
   - 目的が異なる Gateway には別々のロールを作成

3. **認証情報の安全な保存**
   - API キーと OAuth 認証情報は AWS Secrets Manager に保存
   - 認証情報の定期的なローテーション

4. **監視と監査**
   - Gateway 操作の CloudTrail ログ記録を有効化
   - アクセスパターンと権限使用状況の定期的なレビュー

5. **ポリシーでの条件使用**
   - 権限の使用時期と方法を制限する条件を追加
   - 管理操作にはソース IP 制限の検討

## Inbound Auth（受信認証）の設定

### OAuth ベースの認証システム

Gateway を作成する前に、Gateway のターゲットにアクセスしようとする呼び出し元を検証するための Inbound Auth を設定する必要があります。Inbound Auth は OAuth 認証で動作し、クライアントアプリケーションは Gateway を使用する前に OAuth オーソライザーで認証を行う必要があります。

### 必要な設定項目

Gateway 作成時には以下の項目を指定する必要があります：

- **Discovery URL** - OpenID Connect 発見 URL のパターン `^.+/\.well-known/openid-configuration$` に一致する文字列
- **許可されたオーディエンス** または **許可されたクライアント** - 選択したアイデンティティプロバイダーに応じて、以下のいずれか一つ以上を指定
  - `Allowed audiences` - JWT トークンの許可されたオーディエンスのリスト
  - `Allowed clients` - 許可されたクライアント識別子のリスト

### セキュリティ上の注意事項

JWT トークンベースの Inbound Auth を使用すると、JWT トークンの一部のクレームが CloudTrail にログ記録されます。ログエントリには提供された Web アイデンティティトークンの Subject が含まれるため、このフィールドには個人識別情報（PII）を使用しないことが推奨されます。代わりに GUID やペアワイズ識別子の使用が推奨されています。

### サポートされるアイデンティティプロバイダー

#### Amazon Cognito EZ Auth（推奨）

AgentCore SDK を使用する場合、Cognito EZ Auth が OAuth 設定を自動的に構成できます：

```python
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient
client = GatewayClient()
client.create_oauth_authorizer_with_cognito("my-gateway")
```

#### Amazon Cognito（手動設定）

マシン間認証用の Cognito ユーザープールを作成する手順：

1. **ユーザープールの作成**
2. **リソースサーバーの作成** - スコープ（read、write など）を定義
3. **クライアントの作成** - `client_credentials` フローを使用
4. **ドメインの作成**
5. **Discovery URL の構築** - `https://cognito-idp.{region}.amazonaws.com/{UserPoolId}/.well-known/openid-configuration`

設定例：
```json
{
  "customJWTAuthorizer": {
    "discoveryUrl": "https://cognito-idp.us-west-2.amazonaws.com/user-pool-id/.well-known/openid-configuration",
    "allowedClients": ["client-id"]
  }
}
```

#### Auth0

Auth0 をアイデンティティプロバイダーとして使用する場合：

1. **API の作成** - Auth0 ダッシュボードで API を作成
2. **スコープの設定** - `invoke:gateway`、`read:gateway` などのスコープを追加
3. **Machine to Machine アプリケーションの作成**
4. **Discovery URL の構築** - `https://{your-domain}/.well-known/openid-configuration`

設定例：
```json
{
  "customJWTAuthorizer": {
    "discoveryUrl": "https://dev-example.us.auth0.com/.well-known/openid-configuration",
    "allowedAudience": ["gateway123"]
  }
}
```

### アクセストークンの取得

#### Cognito の場合
```bash
curl --http1.1 -X POST https://{UserPoolId}.auth.{region}.amazoncognito.com/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id={ClientId}&client_secret={ClientSecret}"
```

#### Auth0 の場合
```bash
curl --request POST \
  --url https://{your-domain}/oauth/token \
  --header 'content-type: application/json' \
  --data '{
    "client_id":"{ClientId}",
    "client_secret":"{ClientSecret}",
    "audience":"{ApiIdentifier}",
    "grant_type":"client_credentials",
    "scope": "invoke:gateway"
  }'
```

取得したアクセストークンは、Gateway へのリクエスト時にベアラートークンとして使用します。

## Outbound Auth（送信認証）の設定

### Outbound Auth の概要

Outbound Auth は、Inbound Auth で認証・認可されたユーザーに代わって、Gateway がターゲットに安全にアクセスするための仕組みです。AWS リソースや Lambda 関数には IAM 認証情報を使用し、その他のリソースには OAuth 2LO（2-Legged OAuth）や API キーを使用できます。

### Outbound Auth の作成手順

1. **サードパーティプロバイダーへの登録** - クライアントアプリケーションを登録
2. **Outbound Auth の作成** - クライアント ID とシークレットを使用
3. **Gateway Target の設定** - 作成した Outbound Auth を使用

### 認証プロバイダーの種類と設定

#### IAM ロールベース認証（GATEWAY_IAM_ROLE）

AWS リソース（Lambda 関数など）をツールとして登録する場合、Gateway の実行ロールに適切な権限が必要です。

**Target 設定例：**
```json
credentialProviderConfigurations=[{
  "credentialProviderType": "GATEWAY_IAM_ROLE"
}]
```

**実行ロールに必要な権限（Lambda の場合）：**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AmazonBedrockAgentCoreGatewayLambdaProd",
      "Effect": "Allow",
      "Action": [
        "lambda:InvokeFunction"
      ],
      "Resource": [
        "arn:aws:lambda:{{region}}:{{accountId}}:function:[[functionName]]:*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:ResourceAccount": "{{accountId}}"
        }
      }
    }
  ]
}
```

**Lambda 関数側のリソースベースポリシー：**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::{{accountId}}:role/{{GatewayExecutionRoleName}}"
      },
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:{{region}}:{{accountId}}:function:{{functionName}}"
    }
  ]
}
```

#### API キー認証（API_KEY）

API キーを使用してサービスと認証する場合の設定です。

**API キー認証プロバイダーの作成：**
```bash
aws acps create-api-key-credential-provider \
  --region us-east-1 \
  --credential-provider-name api-key-credential-provider \
  --api-key <API_KEY_VALUE>
```

**Target 設定例：**
```json
credentialProviderConfigurations=[{
  "credentialProviderType": "API_KEY",
  "credentialProvider": {
    "apiKeyCredentialProvider": {
      "providerArn": "{{credential-provider-arn}}",
      "credentialLocation": "HEADER",
      "credentialParameterName": "X-Subscription-Token"
    }
  }
}]
```

`credentialLocation` は `HEADER` または `QUERY_PARAMETER` を指定できます。

**実行ロールに必要な権限：**
```json
{
  "Sid": "GetResourceApiKey",
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:GetResourceApiKey"
  ],
  "Resource": [
    "{{credential-provider-arn}}"
  ]
}
```

#### OAuth 認証（OAUTH）

OAuth を使用してサービスと認証する場合の設定です。

**Discovery URL を使用した OAuth プロバイダーの作成：**
```bash
aws acps create-oauth2-credential-provider \
  --region us-east-1 \
  --credential-provider-name oauth-credential-provider \
  --credential-provider-type CustomOAuth2 \
  --o-auth2-provider-config-input '{
    "customOAuth2ProviderConfig": {
      "oauthDiscovery": {
        "discoveryUrl": "<DiscoveryUrl>"
      },
      "clientId": "<ClientId>",
      "clientSecret": "<ClientSecret>"
    }
  }'
```

**Target 設定例：**
```json
credentialProviderConfigurations=[{
  "credentialProviderType": "OAUTH",
  "credentialProvider": {
    "oauthCredentialProvider": {
      "providerArn": "{{credential-provider-arn}}",
      "scopes": ["scope1", "scope2"]
    }
  }
}]
```

**実行ロールに必要な権限：**
```json
{
  "Sid": "GetResourceOauth2Token",
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:GetResourceOauth2Token"
  ],
  "Resource": [
    "{{credential-provider-arn}}"
  ]
}
```

### Secrets Manager を使用する場合の追加権限

API キーや OAuth の認証情報を AWS Secrets Manager に保存している場合、実行ロールに以下の権限も必要です：

```json
{
  "Sid": "GetSecretValue",
  "Effect": "Allow",
  "Action": [
    "secretsmanager:GetSecretValue"
  ],
  "Resource": [
    "{{secrets-manager-arn}}"
  ]
}
```

### セキュリティ上の重要ポイント

1. **認証情報の安全な管理** - クライアントシークレットや API キーは Secrets Manager で管理
2. **最小権限の原則** - 実行ロールには必要最小限の権限のみを付与
3. **リソースベースポリシー** - Lambda などのリソース側でも適切なアクセス制御を設定
4. **スコープの制限** - OAuth の場合、必要最小限のスコープのみを要求

## Gateway の作成方法

### 作成方法の選択肢

Gateway の作成には以下の5つの方法があります：

1. **AgentCore SDK** - Python SDK を使用した簡単な作成
2. **CLI** - コマンドラインインターフェースを使用
3. **Console** - AWS マネジメントコンソールでの GUI 操作
4. **Boto3** - AWS SDK for Python を使用
5. **API** - REST API を直接呼び出し

### AgentCore SDK を使用した作成

Python SDK を使用した最も簡単な方法です：

```python
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

# Gateway クライアントの初期化
client = GatewayClient(region_name="us-west-2")

# EZ Auth - Cognito OAuth を自動設定
cognito_result = client.create_oauth_authorizer_with_cognito("my-gateway")

# Gateway の作成
gateway = client.create_mcp_gateway(
    name=None,  # Gateway 名（未指定の場合は自動生成）
    role_arn=None,  # 実行ロール ARN（未指定の場合は自動作成）
    authorizer_config=cognito_result["authorizer_config"],
    enable_semantic_search=True,  # セマンティック検索を有効化
)

print(f"MCP Endpoint: {gateway.get_mcp_url()}")
print(f"OAuth Credentials:")
print(f"  Client ID: {cognito_result['client_info']['client_id']}")
print(f"  Scope: {cognito_result['client_info']['scope']}")
```

### CLI を使用した作成

AgentCore CLI を使用した簡単な作成方法：

```bash
agentcore create_mcp_gateway \
  --name my-gateway \
  --target arn:aws:lambda:us-west-2:123456789012:function:MyFunction \
  --execution-role BedrockAgentCoreGatewayRole
```

CLI は以下を自動的に処理します：
- ARN パターンやファイル拡張子からのターゲットタイプの検出
- Cognito OAuth（EZ Auth）の設定
- AWS リージョンとアカウントの検出
- ロール名からの完全な ARN の構築

### Console を使用した作成

AWS マネジメントコンソールでの詳細な設定手順：

1. **Gateway 詳細の設定**
   - Gateway 名の入力
   - 説明（オプション）
   - 特別な指示やコンテキスト（オプション）
   - セマンティック検索の有効化（オプション）

2. **Inbound Identity の設定**
   - Discovery URL の入力（例：`https://auth.example.com/.well-known/openid-configuration`）
   - 許可されたオーディエンスの設定

3. **権限の設定**
   - サービスロールの選択または作成
   - KMS キーの設定（オプション）

4. **Target 設定**
   - Target 名と説明
   - Target タイプの選択（Lambda ARN または REST API）
   - Lambda の場合：Lambda ARN とツールスキーマ
   - REST API の場合：OpenAPI スキーマ
   - Outbound 認証の設定（オプション）

### Boto3 を使用した作成

AWS SDK for Python を使用した基本的な作成：

```python
import boto3

agentcore_client = boto3.client('bedrock-agentcore-control')

gateway = agentcore_client.create_gateway(
    name="ProductSearch",
    roleArn="arn:aws:iam::123456789012:role/MyRole",
    protocolType="MCP",
    authorizerType="CUSTOM_JWT",
    authorizerConfiguration={
        "customJWTAuthorizer": {
            "discoveryUrl": "https://cognito-idp.us-west-2.amazonaws.com/some-user-pool/.well-known/openid-configuration",
            "allowedClients": ["clientId"]
        }
    }
)
```

### API を使用した作成

REST API を直接呼び出しての作成：

```json
POST /gateways/ HTTP/1.1
Content-Type: application/json

{
    "name": "my-ai-gateway",
    "description": "Gateway for AI model interactions",
    "clientToken": "12345678-1234-1234-1234-123456789012",
    "roleArn": "arn:aws:iam::123456789012:role/AgentCoreGatewayRole",
    "protocolType": "MCP",
    "protocolConfiguration": {
        "mcp": {
            "version": "1.0",
            "searchType": "SEMANTIC"
        }
    },
    "authorizerConfiguration": {
        "customJWTAuthorizer": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "allowedAudience": ["api.example.com"],
            "allowedClients": ["client-app-123"]
        }
    },
    "encryptionKeyArn": "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012"
}
```

### Target の追加

Gateway 作成後、`CreateGatewayTarget` を使用して Target を追加できます：

```json
PUT /gateways/abc123def4/targets/ HTTP/1.1
Content-Type: application/json

{
  "name": "ProductCatalogAPI",
  "description": "Routes to product catalog and inventory service",
  "targetConfiguration": {
    "mcp": {
      "openApiSchema": {
        "s3Uri": "s3://retail-schemas-bucket/catalog/product-api.json"
      }
    }
  }
}
```

### セマンティック検索の有効化

セマンティック検索は、インテリジェントなツール発見を可能にし、通常のリストツールの制限（通常100程度）を超えることができます。この機能により以下が実現されます：

- **コンテキストに関連するツールサブセットの提供**
- **フォーカスされた関連結果によるツール選択精度の向上**
- **トークン処理の削減による推論パフォーマンスの向上**
- **全体的なオーケストレーション効率とレスポンス時間の改善**

セマンティック検索を有効にするには、`CreateGateway` リクエストの `protocolConfiguration` フィールドに `"searchType": "SEMANTIC"` を追加します：

```json
"protocolConfiguration": {
    "mcp": {
        "searchType": "SEMANTIC"
    }
}
```

**重要な注意点：**
- セマンティック検索は作成時のみ有効化可能で、後から更新することはできません
- セマンティック検索を有効にした Gateway を作成する場合、管理権限に `"SynchronizeGatewayTargets"` アクションが必要です

### Gateway エンドポイント URL

Gateway 作成後、エンドポイント URL は以下の形式になります：
```
https://{gatewayId}.gateway.{region}.amazonaws.com/mcp
```

## Gateway での MCP の使用

### MCP プロトコルの実装

Gateway は Model Context Protocol（MCP）を実装しており、Agent がツールを発見し呼び出すための標準化された方法を提供します。Gateway は MCP 仕様を実装した MCP クライアントと互換性があります。

### 主要な MCP オペレーション

Gateway は以下の2つの主要な MCP オペレーションを公開します：

1. **tools/list** - Gateway が提供するすべての利用可能なツールをリスト表示
2. **tools/call** - 提供された引数で特定のツールを呼び出し

### セマンティック検索機能

Gateway は `x_amz_bedrock_agentcore_search` ツールを通じて組み込みのセマンティック検索機能を提供します。この機能により、Agent はすべての利用可能なツールを知る必要なく、特定のタスクに最も関連性の高いツールを見つけることができます。

#### セマンティック検索の使用例

```python
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def search_tools(gateway_url, access_token, query):
    headers = {"Authorization": f"Bearer {access_token}"}
    async with streamablehttp_client(gateway_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # 組み込み検索ツールの使用
            response = await session.call_tool(
                "x_amz_bedrock_agentcore_search",
                arguments={"query": query}
            )
            
            # 検索結果の解析
            for content in response.content:
                results = json.loads(content.text)
                print(f"Found {len(results)} relevant tools for: {query}")
                for tool in results:
                    print(f"- {tool['name']}: {tool['description']}")

# 使用例
await search_tools(
    "https://gateway-id.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp",
    "your-access-token",
    "How do I process images?"
)
```

### MCP リクエストの認証

Gateway への MCP リクエストを行う前に、Gateway の Inbound Auth に設定されたアイデンティティプロバイダーから認証トークンを取得する必要があります。トークン取得プロセスはアイデンティティプロバイダーによって異なります：

- **Amazon Cognito** - クライアント認証情報フローで OAuth 2.0 トークンエンドポイントを使用
- **Auth0** - クライアント認証情報フローで OAuth 2.0 トークンエンドポイントを使用
- **その他のプロバイダー** - 各プロバイダーのドキュメントを参照

#### Amazon Cognito でのトークン取得

```python
import requests
import base64

def get_cognito_access_token(client_id, client_secret, cognito_domain):
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    
    response = requests.post(
        f"https://{cognito_domain}/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data={"grant_type": "client_credentials"}
    )
    
    return response.json()["access_token"]
```

#### 認証ヘッダーの設定

アクセストークンを取得したら、MCP リクエストの `Authorization` ヘッダーに含めます：

```
Authorization: Bearer YOUR_ACCESS_TOKEN
```

### セマンティック検索の利点

セマンティック検索機能は以下の利点を提供します：

1. **自然言語クエリ** - Agent は自然言語でツールを検索可能
2. **関連性の高い結果** - タスクに最も適したツールを自動的に発見
3. **スケーラビリティ** - 多数のツールがある場合でも効率的な検索
4. **コンテキスト理解** - クエリの意図を理解した検索結果の提供

この機能により、Agent は大量のツールの中から適切なものを効率的に見つけることができ、より高度なタスクの実行が可能になります。

## Gateway のイベントタイプと CloudTrail ログ

### 管理イベント（Management Events）

Gateway は以下の管理操作を CloudTrail に記録します：

- **Gateway 操作**
  - `CreateGateway` - 新しい Gateway の作成
  - `UpdateGateway` - 既存の Gateway の更新
  - `DeleteGateway` - Gateway の削除
  - `GetGateway` - Gateway 情報の取得
  - `ListGateways` - すべての Gateway のリスト表示

- **Gateway Target 操作**
  - `CreateGatewayTarget` - Gateway の新しい Target の作成
  - `UpdateGatewayTarget` - 既存の Gateway Target の更新
  - `DeleteGatewayTarget` - Gateway Target の削除
  - `GetGatewayTarget` - Gateway Target 情報の取得
  - `ListGatewayTargets` - Gateway のすべての Target のリスト表示

管理イベントは AWS アカウント作成時から自動的に有効になり、CloudTrail イベント履歴で過去90日間の記録を確認できます。90日を超える記録を保持するには、Trail または CloudTrail Lake イベントデータストアの作成が必要です。

### データイベント（Data Events）

データイベントはリソース上で実行される操作（データプレーン操作）に関する情報を提供します。これらは高頻度のアクティビティであることが多く、デフォルトでは記録されないため、明示的に有効化する必要があります。

#### Gateway のデータイベントタイプ

| データイベントタイプ（コンソール） | resources.type 値 | CloudTrail に記録される Data API |
|---|---|---|
| Bedrock-AgentCore gateway | `AWS::BedrockAgentCore::Gateway` | InvokeMcp |

### データイベントの ID 情報の特殊性

Gateway のデータイベントは、標準的な AWS データイベントとは ID 情報の保存方法が異なります：

- **JWT トークンベース認証** - Data API は MCP プロトコルに従い、SigV4 ではなく JWT トークンベース認証を使用
- **JWT クレームの記録** - 標準的な AWS ID 情報の代わりに、"sub" クレームを含む特定の JWT クレームを記録
- **PII の回避** - "sub" フィールドには個人識別情報（PII）を使用しないことが推奨され、代わりに GUID やペアワイズ識別子の使用が推奨

### エラー情報の記録方法

Gateway はエラー情報を特殊な方法で提供します：

- エラー情報は `responseElements` フィールドの一部として提供され、トップレベルの `errorCode` や `errorMessage` フィールドではない
- AccessDenied などの特定のエラータイプを検索する場合は、`responseElements` フィールドを解析する必要がある

### データイベントのルーティング

Gateway は認証に SigV4 認証情報ではなく JWT トークンを使用するため、データイベントはリソース所有者アカウントにのみルーティングされます。

## CloudTrail データイベントログの有効化

### データイベントログの設定

Gateway のリクエストに関する情報を取得するために CloudTrail データイベントを使用できます。Gateway の CloudTrail データイベントを有効にするには、Amazon S3 バケットに支援された Trail を CloudTrail で手動作成する必要があります。

### 重要な注意事項

1. **追加料金の発生** - データイベントログには追加料金が発生し、デフォルトでは記録されないため明示的に有効化が必要
2. **大量ログの生成** - 高負荷の Gateway では短時間で数千のログが生成される可能性があるため、有効化期間を慎重に検討する必要がある
3. **S3 バケットの選択** - 複数のリソースからのイベントを中央集約して分析しやすくするため、別の AWS アカウントのバケットの使用を検討

### 高度なイベントセレクターの使用

Gateway 操作のデータイベントをログ記録する場合、CloudTrail で高度なイベントセレクターを使用する必要があります。

#### AWS CLI を使用した設定

```bash
aws cloudtrail put-event-selectors \
  --trail-name brac-gateway-canary-trail-prod-us-east-1 \
  --region us-east-1 \
  --advanced-event-selectors '[
    {
      "Name": "GatewayDataEvents",
      "FieldSelectors": [
        {
          "Field": "eventCategory",
          "Equals": ["Data"]
        },
        {
          "Field": "resources.type",
          "Equals": ["AWS::BedrockAgentCore::Gateway"]
        }
      ]
    }
  ]'
```

#### AWS CDK を使用した設定

AWS CDK を使用して Gateway データイベント用の CloudTrail Trail を作成する例：

```typescript
import { Construct } from 'constructs';
import { Trail, CfnTrail } from 'aws-cdk-lib/aws-cloudtrail';
import { Bucket } from 'aws-cdk-lib/aws-s3';
import { Effect, PolicyStatement, ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import { RemovalPolicy } from 'aws-cdk-lib';

export class BedrockAgentCoreDataEventTrail extends Construct {
  public readonly trail: Trail;
  public readonly logsBucket: Bucket;
  
  constructor(scope: Construct, id: string, props: DataEventTrailProps) {
    super(scope, id);
    
    // CloudTrail ログ用の S3 バケット作成
    const bucketName = `brac-gateway-cloudtrail-logs-${props.account}-${props.region}`;
    this.logsBucket = new Bucket(this, 'CloudTrailLogsBucket', {
      bucketName,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    
    // CloudTrail バケットポリシーの追加
    this.logsBucket.addToResourcePolicy(
      new PolicyStatement({
        sid: 'AWSCloudTrailAclCheck',
        effect: Effect.ALLOW,
        principals: [new ServicePrincipal('cloudtrail.amazonaws.com')],
        actions: ['s3:GetBucketAcl'],
        resources: [this.logsBucket.bucketArn],
        conditions: {
          StringEquals: {
            'aws:SourceArn': `arn:aws:cloudtrail:${props.region}:${props.account}:trail/${trailName}`,
          },
        },
      }),
    );
    
    // CloudTrail Trail の作成
    this.trail = new Trail(this, 'GatewayDataEventTrail', {
      trailName,
      bucket: this.logsBucket,
      isMultiRegionTrail: props.isMultiRegionTrail ?? false,
      includeGlobalServiceEvents: props.includeGlobalServiceEvents ?? true,
      enableFileValidation: true,
    });
    
    // Bedrock Agent Core Gateway データイベント用の高度なイベントセレクター追加
    const cfnTrail = this.trail.node.defaultChild as CfnTrail;
    
    const advancedEventSelectors = [
      {
        fieldSelectors: [
          {
            field: 'eventCategory',
            equalTo: ['Data'],
          },
          {
            field: 'resources.type',
            equalTo: ['AWS::BedrockAgentCore::Gateway'],
          },
        ],
      },
    ];
    
    cfnTrail.eventSelectors = undefined;
    cfnTrail.advancedEventSelectors = advancedEventSelectors;
  }
}
```

### セキュリティ監査における活用

CloudTrail データイベントログは以下のセキュリティ監査に活用できます：

1. **アクセスパターンの分析** - 誰がいつ Gateway にアクセスしたかの追跡
2. **異常な活動の検出** - 通常とは異なるアクセスパターンや大量リクエストの検出
3. **権限の適切性確認** - 実際のアクセス権限使用状況の確認
4. **コンプライアンス要件の満足** - 監査ログの要件を満たすための証跡確保

### ログ分析のベストプラクティス

1. **自動化された分析** - Amazon Athena や AWS Glue を使用したログ分析の自動化
2. **アラート設定** - CloudWatch Logs Insights を使用した異常検出アラートの設定
3. **定期的なレビュー** - セキュリティチームによる定期的なログレビューの実施
4. **長期保存** - コンプライアンス要件に応じたログの長期保存戦略の策定

## まとめ

Amazon Bedrock AgentCore Gateway は、AI Agent が外部ツールやリソースに安全に接続するための包括的なマネージドサービスです。MCP プロトコルを実装し、OAuth ベースの認証システムを採用することで、セキュアで標準化されたツールアクセスを実現します。

### 主要な特徴

1. **多様な Target タイプのサポート** - Lambda、OpenAPI、Smithy モデルによる柔軟なツール統合
2. **強固なセキュリティ** - Inbound Auth と Outbound Auth による多層防御
3. **セマンティック検索** - AI による効率的なツール発見機能
4. **包括的な監査機能** - CloudTrail による詳細なログ記録とセキュリティ監査
5. **複数の作成方法** - SDK、CLI、Console、API による柔軟な構築オプション

### セキュリティ上の重要ポイント

- **最小権限の原則** の徹底
- **認証情報の安全な管理** と定期的なローテーション
- **包括的な監視とログ記録** による異常検出
- **PII の適切な取り扱い** と GUID の使用
- **定期的なセキュリティレビュー** と権限監査

Gateway を適切に設計・運用することで、AI Agent のセキュリティを確保しながら、強力なツール統合機能を活用できます。
