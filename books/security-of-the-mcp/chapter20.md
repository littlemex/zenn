---
title: "§20 Amazon Bedrock AgentCore 実装"
free: true
---

___MCP セキュリティに関する包括的な設計編:___ _MCP のセキュリティに対しての包括的な設計に関する解説_

---

**本 Chapter では Amazon Bedrock AgentCore を用いた MCP の中央集中管理のための実装について解説します。** 

前の Chapter で MCP 中央集約的な管理の概念アーキテクチャを示しました。[Amazon Bedrock AgentCore](https://aws.amazon.com/jp/bedrock/agentcore/) を用いて概念アーキテクチャを実装する際のポイントを整理します。

> カラフルにしすぎた。。

![200101](/images/books/security-of-the-mcp/fig_c20_s01_01.png)

概念アーキテクチャに対して AWS サービスの範囲をマッピングしてみました。**Proxy** に該当する 3 種類の接続パターンを統一的に扱ったり、WAF、レートリミットを入れ込むようなレイヤは現状存在しないため、必要に応じて透過的な Proxy を追加する必要があります。

## Amazon Bedrock AgentCore の概要

***AgentCore Runtime***

Amazon Bedrock **AgentCore Runtime** という MCP Server コード、もしくは Docker コンテナイメージを作成すればすぐに MCP Server をサービングできる機能があります。サーバレスであり自前でインスタンスを持つ必要はありませんし、最大 8 時間の長時間実行に耐え、Streamable HTTP 形式にも対応しています。自作の MCP Server や 3rd Party MCP Server を **Internal MCP Servers** としてサービングすることができます。

***AgentCore Gateway***

Amazon Bedrock **AgentCore Gateway** という API や AWS Lambda ベースの既存のサービスなどを MCPify する **MCPify Proxy** に該当する機能です。

***AgentCore Identity***

Amazon Bedrock **AgentCore Identity** は、Inbound/Outbound Authorization の機能を提供します。

***AgentCore Observability***

Amazon Bedrock **AgentCore Observability** は、Observability 機能を提供し、Amazon CloudWatch でログやトレーシング、メトリクスを確認できます。

***Evaluation/Optimization***

Amazon Bedrock **AgentCore Observability** は OTEL 形式で 3rd Party の Langfuse や MLflow にトレースログ等を連携することができます。Amazon CloudWatch 単体で評価や改善のための機能を現時点では有さないため、Amazon SageMaker AI のマネージド MLflow 3.0 にトレースログを連携し、MLflow 側の機能で評価・改善を実施するのが良いでしょう。

***AI Guardrail***

AgentCore の機能ではありませんが AWS で AI Guardrail 機能を実現するサービスについて紹介しておきます。Amazon Bedrock Guardrails は **AI Guardrail** 機能を提供し、まだ一部機能制限はありますが日本語に対応しました。

***その他***

Amazon Bedrock AgentCore にはその他にも、AgentCore Memory、AgentCore Browser、AgentCore Interpreter などがあります。直接的に MCP と関係する機能ではありませんが AI Agent 実装には欠かせない機能です。まだ 2025/08 時点でプレビューであり、機能や価格等も変更の可能性がある点に注意してください。現状は python ベースでの SDK を提供していますが、Runtime の Docker 内ではカスタムコンテナを作れば自由な言語を使うことができます。

## AgentCore によるセキュリティ対策の実装

[Chapter 16](https://zenn.dev/tosshi/books/security-of-the-mcp/viewer/chapter16) で紹介した MCP セキュリティ対策を Amazon AgentCore でどのように実装できるかを以下の表にまとめました。

**1. MCP Server の緩和策: セグメンテーション**

| 対策例 | AgentCore 対応 | AgentCore 説明 |
|--------|---------------|---------------|
| 専用 MCP セキュリティゾーン | Runtime/Gateway | コンテナ化 + Runtime/Gateway 単位でコンポーネントを分離可能。今後 VPC に閉じたオプションがリリース予定。 |
| アプリケーション層フィルタリングゲートウェイ | - | 作り込みが必要。AWS WAF で MCP Protocol を DPI することは現状できない。 |
| エンドツーエンドの暗号化 | ALL | AgentCore は標準で [TLS 1.2+ を使用](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/data-protection.html)。Streamable HTTP 形式での通信も暗号化。PFS 対応。 |

**2. MCP Server の緩和策: アプリケーションゲートウェイセキュリティ制御**

| 対策例 | AgentCore 対応 | AgentCore 説明 |
|--------|---------------|---------------|
| 厳格なプロトコル検証 | Runtime/Gateway | MCP メッセージを検証し、不正な形式のリクエストは拒否。 |
| 脅威検出パターン | Observability + Bedrock Guardrails | Observability でパターンを監視、Bedrock Guardrails で悪意のあるコンテンツを検出・ブロック。 |
| レート制限 | Runtime/Gateway | Runtime/Gateway ともに API へのレート制限を[サービスクォータ](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html)として実装。 |
| 包括なリクエスト追跡 | Observability | Observability が分散トレーシングを自動的に実装。エンドツーエンドの可視性を提供。 |

**3. MCP Server の緩和策: 安全なコンテナ化とオーケストレーション**

| 対策例 | AgentCore対応 | AgentCore説明 |
|--------|---------------|---------------|
| 不変インフラストラクチャ | Runtime | AgentCore Runtime はコンテナ化で読み取り専用の実行環境を提供。 |
| 制限された機能 | - | 明示なし。 |
| リソースクォータ | Runtime | microVM により CPU、メモリ、ファイルシステムを完全に分離。 |
| Seccomp と AppArmor / SELinux | - | 明示なし。 |
| 定期的なスキャン | - | AWS ECR のイメージスキャン機能と連携して脆弱性を検出可能。 |

**4. MCP Server の緩和策: ホストベースのセキュリティモニタリング**

| 対策例 | AgentCore対応 | AgentCore説明 |
|--------|---------------|---------------|
| MCP 固有の動作ルール | Observability | Observability で MCP 固有のメトリクスとログを収集。カスタムアラートルールを設定可能。 |
| ファイル整合性モニタリング | - | Chapter18 で対策手法を紹介。 |
| メモリ分析 | Runtime | Amazon GuardDuty + Inspector と連携してランタイムの異常を予防・検出。 |

**5. MCP Server の緩和策: 強化された OAuth 2.0+ の実装**

| 対策例 | AgentCore対応 | AgentCore説明 |
|--------|---------------|---------------|
| 強力な Client とユーザー認証 | Identity | Identity が OAuth 2.0、OIDC、SAML をサポート。MFA も統合可能。 |
| 細かく範囲を限定したアクセストークン | Identity | Identity で細かいスコープ設定が可能。短命トークンの自動発行。 |
| オーディエンス制限 | Identity | トークンのオーディエンス制限を自動的に適用。特定の MCP リソースへのアクセスを制御。 |
| 送信者拘束トークン | Identity | mTLS クライアント証明書による代替実装でトークン盗難を防止。 |
| 定期的な鍵のローテーション | Identity + AWS KMS | AWS KMS と統合して自動的な鍵ローテーションを実現。 |

**6. MCP Server の緩和策: ツールとプロンプトのセキュリティ管理**

| 対策例 | AgentCore対応 | AgentCore説明 |
|--------|---------------|---------------|
| 堅牢なツール審査とオンボーディング | Observability | 技術テーマではないため割愛。 |
| ツール説明のためのコンテンツセキュリティポリシー | Observability | Chapter18 で対策手法を紹介。 |
| 高度なツール動作モニタリングと汚染検出 | Observability | Chapter18 で対策手法を紹介。 |

**7. MCP Client の緩和策: ジャストインタイムアクセスプロビジョニング**

| 対策例 | AgentCore対応 | AgentCore説明 |
|--------|---------------|---------------|
| 動的で時間制限のあるアクセス | Identity | Identity が一時的な認証情報の発行をサポート。セッションベースのアクセス制御。 |
| コンテキスト認識アクセス決定 | Identity + AWS IAM | AgentCore Identity と AWS IAM の条件付きアクセスポリシーを組み合わせて実現 |
| 目的駆動の認可 | Identity + Gateway | ツール呼び出しの目的を AgentCore Gateway で検証し、Identity で認可 |
| リアルタイムの取り消し | Identity | AgentCore Identity でトークンの即座の取り消しが可能 |

**8. MCP Client の緩和策: ツールソースと整合性の暗号検証**

| 対策例 | AgentCore対応 | AgentCore説明 |
|--------|---------------|---------------|
| 必須のコード署名 | - | Chapter18 で対策手法を紹介。 |
| 安全なツールレジストリ | - | - |
| サプライチェーンセキュリティ | - | Chapter18 で対策手法を紹介。 |

## まとめ

概念アーキテクチャを Amazon Bedrock AgentCore 実装にマッピングしてセキュリティ対策状況を整理しました。概ね必要なセキュリティ緩和対策がとられていることが確認できたのではないでしょうか。まだ Proxy 的に Runtime/Gateway を統合してユーザーごとにうまくアクセス制限したり、DPI するような仕組みはありませんが、3rd Party のサービスや OSS を使うことでカバーできるレベルではないでしょうか。以降の Chapter ではより詳細な AgentCore の各機能の説明をしていきます。