---
title: "§19 MCP Security アーキテクチャ設計"
free: true
---

___MCP セキュリティに関する包括的な設計編:___ _MCP のセキュリティに対しての包括的な設計に関する解説_

---

**本 Chapter では筆者が考える理想的な MCP の中央集中管理のためのアーキテクチャについて解説します。** 

MCP Server の提供者は MCP Server をこれまでに解説したセキュリティを意識しながら提供することが責務です。一方で組織として MCP Server を安全に利用する場合には考慮すべきことが山積していることを解説してきました。本 Chapter では MCP を組織で中央集中管理する際の利用者視点のセキュリティを考慮したアーキテクチャ全体の概念について整理し、特定のクラウドプロバイダーやツールに特化した内容については取り扱いません。

## 概念アーキテクチャ

![190101](/images/books/security-of-the-mcp/fig_c19_s01_01.png)

概念アーキテクチャの **Centrallized Management** が組織が中央集中的に管理するコンポーネントです。組織の利用者は [Kiro](https://aws.amazon.com/jp/blogs/news/introducing-kiro/) のような **MCP Host** を通じて MCP Server を利用します。

中央集中的に MCP Server を管理したいモチベーションについて考えてみましょう。ユーザーが個別で好き放題に MCP Server を利用することによる MCP 特有の脆弱性に対するリスクや、MCP Host、利用ユーザーやプロジェクトごとに毎回 MCP Server を設定する手間、などを考慮した場合に、組織として一元的にレビュー済みの MCP Server を提供することはセキュリティや運用上の意義があります。

多くの場合に組織ではデータの機密レベルに応じてユーザーへのアクセス権限のルールを定めます。手元の PC で好きな MCP Host から好きな MCP Server を自由に使わせる統制のレベルがあっても良いですし、データ分析などをする場合には閉域に近いサンドボックス環境を用意し、サンドボックス内に用意された MCP Host から管理者が提供する MCP Server のみを利用できるようにする統制のレベルもあるでしょう。

これから解説するアーキテクチャは、あくまで組織として中央集中的に MCP Server を管理するための一例の概念アーキテクチャであり、実際には組織構造やデータの機密レベル、プロジェクト等を考慮しながら自社にとって適切な構成を考える必要があります。

### MCP Server の提供方法

MCP Client にとっては、MCP Server が MCP 仕様に沿ってさえいれば裏側がどのような仕組みやインフラストラクチャで構築されているのかは  Don't care です。MCP Server を組織として一括で提供するにあたってどのような提供パターンがあるのか整理してみましょう。

![190102](/images/books/security-of-the-mcp/fig_c19_s01_02.png)

**1. 外部接続パターン**

2025/08 現在、多くの MCP Server が STDIO 形式で提供されていますが、Streamable HTTP 形式でも MCP Server を広く公開することができます。MCP Client から適切な認証・認可の上で外部 MCP Server(Streamable HTTP) に接続するパターンがあるでしょう。

**2. 内部接続パターン**

外部サービスが提供する STDIO 形式の MCP Server や、自社で開発した Streamable HTTP/STDIO の MCP Server を提供したいパターンがあるでしょう。STDIO の場合は Docker 化した上でクラウド上でサービングし、内部の Streamable HTTP 形式の MCP Server の場合は外部接続パターン同様に適切な認証・認可の上で接続します。

**3. MCPify パターン**

既存の内部・外部の API やすでに作ってある AI エージェントを MCP Server として提供したいパターンがあるでしょう。API などを MCP 化するという意味で [**MCPify**](https://catalog.us-east-1.prod.workshops.aws/workshops/015a2de4-9522-4532-b2eb-639280dc31d8/en-US/30-agentcore-gateway/31-transforming-lambda-to-mcp) という用語が使われ始めています。すでに OpenAPI スキーマ情報をもとに MCP Server に変換する仕組みが出てきており、これらの変換を担う **MCPify Proxy** コンポーネントを図に追加しました。

**その他のコンポーネント**

これらのパターンの全てで、1/ 適切な認証・認可の対応、2/ ユーザー側での接続切り替えを意識しない設計、WAF やレートリミットの導入、が必要です。**Proxy** はこれらの機能を担うコンポーネントです。そして **Observability**、**Audit**、**CI/CD** コンポーネントが必要でしょう。

### LLM の提供方法

MCP Host に寄りますがどのプロバイダーの LLM を利用するのか選択できることがあります。例えば、VSCode エクステンションの [Cline](https://github.com/cline/cline) はモデルプロバイダーを柔軟に選択できます。何かしらの LLM を MCP Host から利用するために組織は通常 LLM をサブスクリプションします。

![190103](/images/books/security-of-the-mcp/fig_c19_s01_03.png)

**Centralized Management** コンポーネント内でサブスクリプションした **LLM** の利用をユーザーごとに管理して提供することが望ましでしょう。こうすることで中央集約的に LLM の前段に **AI Guardrail** を配置することができます。AI Guardrail 自体にも不確定性があるため完璧な攻撃の検知と対処はできませんが、多層防御の視点で取り入れることには意義があります。AI Guardrail を入れるだけでは意味がなく、AI Guardrail と LLM の入出力を **Observability** コンポーネントでトーレスし、それらのデータを利用して **Evaluation/Optimization** することで AI Guardrail の精度を向上させる取り組みが研究・実装されて初めています。

## 各コンポーネントに求められる機能

**Proxy** は上述した接続パターンをプロキシとして受け付け、それぞれで必要な認可処理を実施します。自前で実装する場合にはサービスメッシュを用いたり AI API Gateway(ex. Kong, LiteLLM, Portkey など) などを用いることも選択肢になるでしょう。Streamable HTTP の場合、HTTP Base Protocol 上で構成される MCP プロトコルを Deep Packet Inspection して WAF やセッションに対するレートリミットなどの機能を持つことで一元的にアプリケーション層のフィルタリング機能を提供します。このコンポーネントに既知の攻撃シグネチャを検出する静的なルールを適用することも検討の余地があります。

**Inbound Authorization** は MCP Client から MCP Server へのアクセスを制御する認可機能を提供します。MCP の認可仕様に基づき、接続パターンに依らず一元的に認可機能を実装することで、安全なアクセス制御を実現します。[Chapter12](https://zenn.dev/tosshi/books/security-of-the-mcp/viewer/chapter12) で解説したので改めて確認してみてください。このパターンでは、中央のゲートウェイ層がすべての認可を処理し、認証済みのリクエストのみを MCP Server に転送します。

**Outbound Authorization** は MCP Server がバックエンドサービスやサードパーティ API にアクセスする際の認証・認可を管理するコンポーネントです。MCP Server は多くの場合、ユーザーに代わって外部サービスにアクセスする必要があり、このアクセスを安全に管理することが重要です。[Chapter12](https://zenn.dev/tosshi/books/security-of-the-mcp/viewer/chapter12) に MCP 仕様の課題としてまとめていますが、現時点では仕様範囲外のため独自で実装する必要があります。

**MCPify Proxy** は、上述した API 等を MCP 化する機能を提供するコンポーネントです。

**AI Guardrail** は、ルールベースや LLM によるプロンプトインジェクション等の緩和を目的としたガードレール機能を提供するコンポーネントです。AI Guardrails と LLM を合わせて **Centralized Management** 側で管理することで Tool Shadowing のような悪意のある MCP Server が Client を介して正規の MCP Server のツールを利用しようとするようなケースにおいて管理者側で許可していない悪意あるツール提供の検出や悪意あるツール呼び出しの検出の仕組みを構築することができます。

**Observability** は、非常に重要なコンポーネントであり、AI Gurardrail、LLM、MCP Server などのトーレーシングやメトリクス収集などを行います。これらはセキュリティ対策のためにも重要ですが、ガードレール精度の向上やプロンプトチューニングなどの性能向上面でも非常に重要です。

それ以外のコンポーネントについてはコンポーネント名が機能を説明しているため割愛します。

## まとめ

筆者の考える抽象レベルでの MCP Server の中央集中管理アーキテクチャを設計しましたが、もちろん完璧なものではなく一例として提示しました。次 Chapter ではこの概念レベルのアーキテクチャから実際に AWS でどう実現するのかという具体に落とし込んでいきます。