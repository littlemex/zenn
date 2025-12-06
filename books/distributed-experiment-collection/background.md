---
title: "大規模基盤モデル学習の背景"
emoji: "🚀"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["aws", "sagemaker", "hyperpod", "ai", "ml"]
free: true
---

::::details 前提
:::message
**対象読者**: 大規模基盤モデルがどういうものかを理解している方、これからモデル学習を行う方
:::
:::message
**ライセンス**: © 2025 littlemex.
本文および自作図表: CC BY 4.0
※公式ドキュメントからの引用や翻訳部分は原典の著作権に従います。
引用画像: 各画像の出典に記載されたライセンスに従います。
:::
:::message
一部 AI を用いて文章を作成します。レビューは実施しますが、見逃せない重大な間違いなどがあれば[こちらのIssue](https://github.com/littlemex/samples/issues)から連絡をお願いします。
:::
::::

**本章では Amazon SageMaker HyperPod（以降 HyperPod）の説明と、2025 年の大規模基盤モデル学習に関する基礎知識を整理します。**

---

AWS Principle WW Solutions Architect,GenAI, Keita Watanabe さんの [Scalable Infrastructure for Large-Scale AI Training with AWS Sagemaker Hyperpod](https://speakerdeck.com/keitaw/scalable-infrastructure-for-large-scale-ai-training-with-aws-sagemaker-hyperpod-at-singapore-ai-hour) 資料の流れを参照しながら初学者向けに情報を整理します。

# バックグラウンド

## 前提知識

:::message
**Point !** ***大規模基盤モデル学習には大規模なコンピュートが必要***
:::
2025 年現在、Anthropic、OpenAI、Google、DeepSeek など多くのモデルプロバイダから毎月のように最先端モデルの発表があります。これらの最先端モデルの事前学習には総じて大規模なコンピュートを必要とします。

なぜ大規模なコンピュートが必要なのかを整理する前に、まずは事実としてモデル学習のためにどのくらい大規模なコンピュートを要求するのか確認してみましょう。以下の表は、代表的なモデルの事前学習時のコンピュートに関する情報を整理したものです。
::::details 事前学習とは
参考 URL
- [JSAI: 【LLM強化学習①】事前学習と事後学習](https://www.youtube.com/watch?v=nfLLfb3r6io)
- [2024年度 人工知能学会全国大会（第38回）チュートリアル講演１: 大規模言語モデルの開発](https://speakerdeck.com/chokkan/jsai2024-tutorial-llm?slide=15)
::::


| モデル | リリース時期 | 総パラメータ | アクティブ | 学習GPU時間 | 使用GPU | 学習トークン | 特記事項 |
|--------|-------------|-------------|-----------|-------------|---------|-------------|----------|
| **[Llama 2 7B](https://huggingface.co/meta-llama/Llama-2-7b)** | 2023年7月 | 7B | 7B | **0.184M** | A100-80GB | 2T | Dense、BF16 |
| **[Llama 2 13B](https://huggingface.co/meta-llama/Llama-2-13b)** | 2023年7月 | 13B | 13B | **0.369M** | A100-80GB | 2T | Dense、BF16 |
| **[Llama 2 70B](https://huggingface.co/meta-llama/Llama-2-70b)** | 2023年7月 | 70B | 70B | **1.72M** | A100-80GB | 2T | GQA、BF16 |
| **[Llama 3 8B](https://github.com/meta-llama/llama3)** | 2024年4月 | 8B | 8B | **推定1.0M** | H100-80GB | 15T+ | GQA、8K context |
| **[Llama 3 70B](https://github.com/meta-llama/llama3/blob/main/MODEL_CARD.md)** | 2024年4月 | 70B | 70B | **推定6.7M** | H100-80GB | 15T+ | GQA、8K context |
| **[Llama 3.1 8B](https://huggingface.co/meta-llama/Llama-3.1-8B)** | 2024年7月 | 8B | 8B | **1.46M** | H100-80GB | 15T+ | GQA、128K context |
| **[Llama 3.1 70B](https://huggingface.co/meta-llama/Llama-3.1-70B)** | 2024年7月 | 70B | 70B | **7.0M** | H100-80GB | 15T+ | GQA、128K context |
| **[Llama 3.1 405B](https://huggingface.co/meta-llama/Llama-3.1-405B)** | 2024年7月 | 405B | 405B | **30.84M** | H100-80GB | 15T+ | GQA、128K context |
| **[DeepSeek V3](https://huggingface.co/deepseek-ai/DeepSeek-V3)** | 2024年12月 | 671B | 37B | **2.788M** | H800-80GB | 14.8T | MoE、FP8学習 |
| **[Llama4 Maverick](https://huggingface.co/meta-llama/Llama-4-Maverick-17B-128E-Instruct)** | 2025年4月 | 400B | 17B | **2.38M** | H100-80GB | 未公開 | MoE |

::::details 注意事項
:::message alert
GPU を単純に増やせば増やすほど学習時間が単純計算で短縮されるわけではないことに注意が必要です。基本的に計算はノードに閉じていることが最も効率が良く、ノード間で通信をしだすと効率が落ちてきます。100 人それぞれが自分の中だけで考えて意思決定するのと、一箇所に集まった 100 人が議論した上で意思決定する、世界各地にいる 100 人が議論した上で意思決定する、では同じ 100 人による意思決定でも合意形成のためのエフォートが全然違いますよね。誤解を恐れずに言うとそれと同じことです。
:::
:::message alert
必ずしも最新モデルやパラメータ数の大きいモデルの方が性能が良いわけではありません。
以下のサイトにモデルの Intelligence, speed, price などの情報が掲載されており、参考情報として有用かと思います。
:::
https://artificialanalysis.ai/
::::

::::details 表記説明
表中の **M** は Million（百万）を表します。例えば 2.788M は 278 万 8 千、30.84M は 3,084 万を意味します。**GPU 時間**は、GPU 1 台が稼働した時間の累計です。例えば H100 1 台で 640 万時間かかる学習を、H100 100 台で並列実行すれば 6 万 4 千時間（約 7.3 年）で完了します。**使用GPU** の欄には、学習に使用された GPU の種類を記載しています。A100-80GB、H100-80GB、H800-80GB などがあり、数字が大きいほど新しい世代で性能が高くなります。

**学習トークン**の **T** は Trillion（兆）を表します。2T は 2 兆トークン、15T+ は 15 兆トークン以上を意味します。
::::

この表から最先端のモデルが如何に大規模なコンピュートを必要とするのか分かったのではないでしょうか。現実的に Llama 3 70B の GPU 時間を 1 ヶ月で達成しようとすると 1250 台の H100 を連携させながら学習する必要があります。AWS の p5.48xlarge だとオンデマンドで単純に計算すると約 440 億円必要です。

大規模基盤モデルの詳細について説明することが本書の目的ではなく、主に Hyperpod を用いた分散学習・推論の実験を行うことが目的です。背景情報や理論的説明については既に優れた資料が世の中に多数存在するためそれらの URL を雑に貼って次に進みます。

::::details 参考情報
**[2024年度 人工知能学会全国大会（第38回）チュートリアル講演１: 大規模言語モデルの開発](https://speakerdeck.com/chokkan/jsai2024-tutorial-llm?slide=15)**
大規模言語モデルの概要、事前学習・継続事前学習、インストラクションチューニング、アライメント、評価などについて整理されている講演資料。個人的には Chinchilla 則、その後の推論を重視した小さいモデルを多めのデータで訓練する [Beyond Chinchilla-Optimal](https://arxiv.org/abs/2401.00448) あたりの整理は重要に感じた。この辺りは実際に大規模な事前学習をやっていないと調整の感覚を得られなさそう。

**[進化する大規模言語モデル評価: Swallowプロジェクトにおける実践と知見](https://speakerdeck.com/chokkan/swallow-evaluation-instruct-wandb-fullyconnected2025)**
Swallow プロジェクトにおける実践と知見をシェア。主に評価に着目。

**[ 東工大Swallowプロジェクトにおける大規模日本語Webコーパスの構築](https://speakerdeck.com/aya_se/data-centric-ai-swallow-corpus-56e2869a-f9bd-46cb-b030-1012235c37f7)**
コーパス作成の苦労が滲んでいて非常に参考になる。
::::

## コンピュート要求

:::message
**Point !** ***大規模基盤モデル学習には大規模なコンピュートが必要***
:::

### ルーフラインモデルによる性能分析

大規模基盤モデルの学習では、メモリ帯域幅と計算性能という 2 つの要素が性能を決定します。ルーフラインモデル (Roofline Model) は、この 2 軸の関係を可視化し、どちらがボトルネックになっているかを判定する手法です。ルーフラインモデルの詳細は、[NVIDIA Nsight Compute によるパフォーマンス分析](https://developer.nvidia.com/blog/accelerating-hpc-applications-with-nsight-compute-roofline-analysis/)および [Hierarchical Roofline Analysis 論文 (arXiv:2009.05257)](https://arxiv.org/pdf/2009.05257) に記載されています。

```mermaid
graph TB
    subgraph "ルーフラインモデルの概念"
    A[ワークロード] --> B{演算強度<br/>OPs/Byte}
    B -->|低い| C[メモリバウンド<br/>メモリ帯域幅が<br/>ボトルネック]
    B -->|高い| D[コンピュートバウンド<br/>計算性能が<br/>ボトルネック]
    end
    
    C --> E[最適化方針:<br/>量子化、カーネル融合<br/>バッチサイズ増加]
    D --> F[最適化方針:<br/>計算効率化<br/>並列化改善]
```

演算強度 (Arithmetic Intensity) は、メモリアクセス 1 バイトあたりの演算回数 (OPs/Byte) として定義されます。演算強度が低い場合はメモリバウンド、高い場合はコンピュートバウンドとなります。深層学習の推論では、Prefill stage (初回入力処理) はコンピュートバウンド、Decode stage (逐次生成) はメモリバウンドになる傾向があります。このため、推論性能の最適化では Decode stage のメモリバウンド特性への対処が重要となります。

### メモリ要求: 1.2TB VRAM の内訳

Llama 3 70B を FP32 精度で学習する場合、約 1.2TB の VRAM が必要です。この内訳を理解することは、必要なハードウェア構成を計画する上で重要です。メモリ要求の計算方法は、[LLM Training Memory Optimization Guide](https://github.com/WhitePegasis/LLM-Training-Memory-and-Speed-Optimization-Guide) に詳細に記載されています。

```mermaid
graph TD
    A[総メモリ要求<br/>Llama 3 70B FP32<br/>1.2TB VRAM] --> B[Parameters<br/>280GB<br/>70B × 4 bytes]
    A --> C[Gradients<br/>280GB<br/>70B × 4 bytes]
    A --> D[Adam Optimizer States<br/>560GB<br/>70B × 4 bytes × 2]
    
    D --> E[Momentum<br/>280GB]
    D --> F[Variance<br/>280GB]
    
    style A fill:#e1f5ff
    style D fill:#fff4e1
```

#### Parameters (280 GB)

モデルの重みとバイアスを格納するメモリです。70B パラメータを FP32 (4 bytes) で格納すると、70B × 4 bytes = 280 GB となります。これは学習中常に GPU メモリに常駐する必要があります。FP16 や BF16 といった半精度形式を使用すると、このサイズは半分の 140 GB となりますが、数値精度とのトレードオフを考慮する必要があります。

#### Gradients (280 GB)

バックプロパゲーション時に計算される勾配を一時的に格納するメモリです。各パラメータに対して勾配が計算されるため、パラメータと同じサイズが必要となります。勾配は optimizer step でパラメータを更新した後にクリアされますが、計算中は GPU メモリを占有します。

#### Adam Optimizer States (560 GB)

Adam オプティマイザは各パラメータに対して 2 つの状態、momentum (1次モーメント) と variance (2次モーメント) を保持します。このため、70B パラメータ × 4 bytes × 2 = 560 GB のメモリが必要となります。SGD など単純なオプティマイザではこのサイズは小さくなりますが、Adam 系オプティマイザは収束性能の高さから大規模モデル学習で標準的に使用されています。

これらの合計が約 1.2TB となり、H100 80GB GPU であれば最低 15 台、実用的には余裕を持って 20 台以上の GPU が必要となります。実際には、この他に活性化関数 (Activations) の値を格納するメモリも必要となるため、さらに多くの GPU メモリが要求されます。

### Compute 要求: Scaling Law とトークン数の関係

モデル学習に必要な計算量は、以下の Scaling Law で推定できます。

**FLOPS = 6 × Parameters × Tokens**

この係数 6 は、Transformer アーキテクチャの 1 トークンあたりの計算量に由来します。forward pass で約 2 FLOPs/parameter、backward pass で約 4 FLOPs/parameter の計算が必要なため、合計で約 6 FLOPs/parameter となります。この計算方法は、[Chinchilla 論文](https://arxiv.org/abs/2203.15556)および [Scaling Law の解説記事](https://lifearchitect.ai/chinchilla/) で詳述されています。

```mermaid
graph LR
    subgraph "Scaling Law: FLOPS = 6 × Parameters × Tokens"
    A[Parameters<br/>例: 70B] --> D[総 FLOPS<br/>588 × 10²¹]
    B[Tokens<br/>例: 1.4T] --> D
    C[係数<br/>6 FLOPs/param/token] --> D
    end
    
    D --> E[必要な GPU 時間<br/>GPU 性能と台数から算出]
    D --> F[学習期間<br/>数週間〜数ヶ月]
    
    style D fill:#e1f5ff
```

#### Chinchilla Law: Compute-Optimal Training

2022 年の Chinchilla 論文 ([Training Compute-Optimal Large Language Models](https://arxiv.org/abs/2203.15556)) では、固定された計算予算の下で最適な学習を行うには、パラメータ数とトークン数を 1:20 の比率でスケールすべきことが示されました。

Llama 3 70B を Compute-Optimal に学習する場合の計算例を示します。

**パラメータ数**: 70B  
**Compute-Optimal トークン数**: 70B × 20 = 1.4T トークン  
**必要 FLOPS**: 6 × 70B × 1.4T = 588 × 10²¹ FLOPS

H100 GPU の理論演算性能は FP16/BF16 で約 989 TFLOPS (約 1 PFLOPS) です。実効性能を 50% と仮定すると、1 台あたり約 0.5 PFLOPS となります。この性能で 588 × 10²¹ FLOPS を処理するには、1 台で約 1,176 万時間 (約 1,342 年) かかる計算となります。

実際には GPU を並列化して学習時間を短縮します。例えば H100 を 1,000 台使用すると、理論上は約 1,176 時間 (約 49 日) で学習が完了します。ただし、通信オーバーヘッドや効率低下を考慮すると、実際の学習期間はこれより長くなります。

#### Beyond Chinchilla-Optimal: Inference-Optimal Training

2024 年以降の主流モデルは、推論コストを考慮した Inference-Optimal Training を採用しています。小さいモデルを多くのトークンで学習することで、学習コストは増加しますが、推論時の計算コストを大幅に削減できます。

この戦略の理論的根拠は、MosaicML の論文 ([Beyond Chinchilla-Optimal: Accounting for Inference in Language Model Scaling Laws](https://arxiv.org/abs/2401.00448)) で示されています。モデルは一度だけ学習されますが、何兆回も推論されます。このため、学習コストと推論コストの総和である生涯コスト (Lifetime Cost) を最小化する観点では、over-training (Chinchilla 比でより多くのトークンで学習) が最適解となります。

Llama 3.1 70B の実例を見てみましょう。

**パラメータ数**: 70B  
**実際の学習トークン数**: 15T+ トークン  
**Chinchilla-Optimal との比較**: 15T ÷ 1.4T ≈ 10.7 倍 (+970%)  
**必要 FLOPS**: 6 × 70B × 15T = 6,300 × 10²¹ FLOPS

学習に必要な FLOPS は Chinchilla-Optimal の約 10.7 倍に増加しますが、パラメータ数を抑えることで推論コストを削減できます。例えば、同じ性能を 200B パラメータのモデルで達成する場合と比較すると、推論時の計算量は約 1/3 となり、数千億回の推論を考慮すると総コストで有利になります。

この over-training の傾向は、前章の表で示した Llama 3.1 シリーズ、DeepSeek V3、Llama4 Maverick など、2024 年以降のモデルに共通して見られます。計算予算が十分にある場合、Chinchilla Law は学習時の計算効率を最適化しますが、実運用では推論コストを含めた生涯コストの最適化が重要となっています。


AWS が 2024 年に発表した [Amazon Nova](https://speakerdeck.com/keitaw/optimizing-foundation-model-development-with-amazon-sagemaker-hyperpod-insights-from-training-the-amazon-nova-model) は、HyperPod を活用した基盤モデル開発の成功事例です。渡辺啓太氏による報告では、数万 GPU、数千ホストという大規模環境において 3～4 ヶ月の連続学習を実施し、**40% のコスト削減**を達成しています。この成果は、HyperPod の自動故障復旧機能、最適化された分散学習フレームワーク、Amazon EC2 UltraClusters による高効率なリソース配置によって実現されました。

開発過程では 1 日に 10～20 回のハードウェア不良が発生する過酷な環境でしたが、HyperPod の包括的な故障対応メカニズムにより、学習の継続性が保たれました。焼きなまし（Burn-in）による事前検証、頻繁なチェックポイント保存、余剰ハードウェアの確保といったベストプラクティスが自動化されており、手動での運用負荷を大幅に削減しています。

## 分散ファイルストレージ要求

:::message
**Point !** ***大規模基盤モデル学習には、学習データとチェックポイント保存のための高性能分散ストレージが必要***
:::

大規模基盤モデルの学習では、数十から数百 TB に及ぶ学習データへの高速アクセスと、頻繁なチェックポイント保存が不可欠です。このセクションでは、代表的なデータセットのサイズと、チェックポイント要求について整理します。

### 代表的なデータセットのサイズ

学習に使用される代表的なデータセットのトークン数とサイズを以下の表にまとめます。

| データセット | トークン数 | サイズ | 特記事項 |
|-------------|-----------|--------|----------|
| **[WikiText-103](https://huggingface.co/datasets/Salesforce/wikitext)** | 103M | 750MB | 英語 Wikipedia から抽出 |
| **C4.EN** | 156B | 305GB | Common Crawl ベース |
| **[RedPajama-Data-1T](https://github.com/togethercomputer/RedPajama-Data)** | 1T | 5TB | 多様なソースから構成 |
| **[RedPajama-Data-v2](https://github.com/togethercomputer/RedPajama-Data)** | 30T | 170TB | 100B 文書、重複除去後 20B 文書 |
| **[llm-jp-corpus-v3](https://llm-jp.github.io/awesome-japanese-llm/en/)** | 2.1T | 推定 15TB | 日本語を含む多言語コーパス |
| **[llm-jp-corpus-v4 (日本語)](https://huggingface.co/llm-jp/llm-jp-modernbert-base)** | 0.69T | 3.4TB | 日本語のみ |
| **[Swallow-Corpus](https://speakerdeck.com/aya_se/data-centric-ai-swallow-corpus-56e2869a-f9bd-46cb-b030-1012235c37f7)** | 推定 1.7T | 推定 12TB | Common Crawl から 0.27% に精錬 |

これらのデータセットは、モデルの学習中に繰り返しアクセスされるため、高速な読み取り性能を持つ分散ストレージが必要となります。特に数千 GPU での学習では、全 GPU からの同時アクセスに耐えられる帯域幅が求められます。

### チェックポイント要求: サイズと保存頻度

チェックポイントは、学習の進捗状態を保存するもので、GPU 故障時の復旧に不可欠です。チェックポイントには、Parameters (モデルの重み) と Optimizer States (Adam オプティマイザの状態) が含まれます。

#### チェックポイントサイズの計算

BF16 精度 (2 bytes/parameter) で学習する場合、チェックポイントサイズは以下のように計算できます。

**チェックポイントサイズ = Parameters + Optimizer States**
- **Parameters**: モデルサイズ × 2 bytes
- **Optimizer States**: モデルサイズ × 2 bytes × 2 (momentum + variance)

```mermaid
graph TD
    A[チェックポイント<br/>総サイズ] --> B[Parameters<br/>2 bytes × N]
    A --> C[Optimizer States<br/>4 bytes × N]
    
    C --> D[Momentum<br/>2 bytes × N]
    C --> E[Variance<br/>2 bytes × N]
    
    style A fill:#e1f5ff
    style C fill:#fff4e1
```

具体的なモデルサイズでの試算は以下の通りです。

| モデルサイズ | Parameters | Optimizer States | チェックポイント総サイズ (BF16) |
|-------------|-----------|-----------------|---------------------------|
| **7B** | 14GB | 28GB | **42GB** |
| **13B** | 26GB | 52GB | **78GB** |
| **70B** | 140GB | 280GB | **420GB** |
| **405B** | 810GB | 1,620GB | **2.43TB** |

#### チェックポイント保存頻度

チェックポイントの保存頻度は、GPU の故障率に基づいて決定されます。[VAST Data の分析](https://www.vastdata.com/blog/optimizing-checkpoint-bandwidth-for-llm-training)によれば、Llama 3.1 405B の学習事例 (16,000 H100 GPU) では、平均故障間隔 (Mean Time To Interrupt) が 150 分でした。この場合、推奨されるチェックポイント間隔は、故障間隔の約 1/10、つまり 15 分となります。

より一般的には、以下の式でチェックポイント間隔を決定できます。

**推奨チェックポイント間隔 = 平均故障間隔 ÷ 10**

GPU 数が多いほど故障率は高くなるため、より頻繁なチェックポイント保存が必要となります。ただし、[Google Cloud の ML Goodput 最適化に関する分析](https://cloud.google.com/blog/products/ai-machine-learning/elastic-training-and-optimized-checkpointing-improve-ml-goodput)によれば、チェックポイント保存によるオーバーヘッドは 10% 以下に抑えることが推奨されます。これは、非同期チェックポイント技術を使用することで実現できます。

### 総ストレージ容量の概算

学習に必要な総ストレージ容量は、学習データとチェックポイントの両方を考慮する必要があります。

**総ストレージ容量 = 学習データサイズ + (チェックポイントサイズ × 保持世代数)**

例えば、Llama 3 70B を llm-jp-corpus-v3 (15TB) で学習する場合を考えます。

- **学習データ**: 15TB
- **チェックポイントサイズ**: 420GB (BF16)
- **保持世代数**: 3 世代 (最新、1つ前、2つ前)
- **チェックポイント合計**: 420GB × 3 = 1.26TB

**総ストレージ容量**: 15TB + 1.26TB ≈ **16.3TB**

実際には、中間生成物やログファイルなども考慮する必要があるため、20TB 程度の容量を確保することが推奨されます。

より大規模な Llama 3.1 405B での学習では、RedPajama-Data-v2 (170TB) を使用すると仮定した場合:

- **学習データ**: 170TB
- **チェックポイントサイズ**: 2.43TB (BF16)
- **保持世代数**: 3 世代
- **チェックポイント合計**: 2.43TB × 3 = 7.29TB

**総ストレージ容量**: 170TB + 7.29TB ≈ **177TB**

### 書き込み帯域幅の要求

チェックポイントの書き込み帯域幅は、保存頻度とチェックポイントサイズから計算できます。

**必要帯域幅 = チェックポイントサイズ ÷ (チェックポイント間隔 × (1 - オーバーヘッド率))**

Llama 3.1 405B の事例 (チェックポイント間隔 15 分、オーバーヘッド 10%) では:

**必要帯域幅** = 2.43TB ÷ (15分 × 60秒 × 0.9) ≈ 2.43TB ÷ 810秒 ≈ **3GB/s**

実際の運用では、以下の技術を組み合わせることで、効率的なチェックポイント管理を実現します。

```mermaid
graph LR
    A[GPU メモリ] -->|数秒| B[ノードローカル<br/>NVMe SSD]
    B -->|非同期<br/>数分| C[分散ストレージ<br/>FSx for Lustre]
    C -->|長期保存| D[S3]
    
    style B fill:#e1f5ff
    style C fill:#fff4e1
```

- **ノードローカル NVMe**: GPU から高速にチェックポイントを保存 (同期)
- **分散ストレージ (FSx for Lustre)**: ノードローカルから定期的にドレイン (非同期)
- **S3**: 長期保存用のアーカイブ

この 3 層構造により、GPU のブロッキング時間を最小化しながら、データの永続性を確保できます。Amazon FSx for Lustre は、S3 と統合されており、大規模データセットへの高速アクセスとチェックポイントの永続化を両立します。


ここまで


### 東京科学大学 Llama 3.3 Swallow 70B - 商用最大規模の日本語モデル

[東京科学大学（岡崎研究室・横田研究室）の Swallow プロジェクト](https://speakerdeck.com/aya_se/data-centric-ai-swallow-corpus-56e2869a-f9bd-46cb-b030-1012235c37f7)では、HyperPod を使用して Llama 3.3 Swallow 70B という 70B パラメータの日本語言語モデルを開発しました。このプロジェクトの特筆すべき点は、Common Crawl から 632 億ページを処理し、9 ステップの精密なフィルタリングを経て最終的に全体の **0.27%** にまで精錬した独自の「Swallow コーパス」を構築したことです。このコーパスは、商用利用可能な日本語学習データとして最大規模となっています。

Swallow プロジェクトは、HyperPod が単なるクラウドインフラを超えて、研究機関や企業が独自の基盤モデルを開発するための実用的なプラットフォームであることを示しています。特に、地域特化型モデルや専門領域に特化したモデル開発において、HyperPod の価値が実証されました。

## 第2部：2025 年の基盤モデル学習における課題



### 課題2：ネットワーク性能がボトルネックに

分散学習における GPU 間・インスタンス間の通信性能は、学習速度を決定する重要な要素です。[渡辺氏の報告](https://speakerdeck.com/keitaw/scalable-infrastructure-for-large-scale-ai-training-with-aws-sagemaker-hyperpod-at-singapore-ai-hour)によれば、Amazon EC2 の Elastic Fabric Adapter（EFA）を使用することで、PyTorch FSDP による GPT 事前学習において 512 GPU（64 インスタンス）構成で **2.5 倍から 25.6 倍のパフォーマンス向上**を達成しています。

EFA は SRD（Scalable Reliable Datagram）プロトコルを採用し、カーネルバイパスと GPU-direct RDMA により低レイテンシ・高スループットを実現しています。特に MoE アーキテクチャでは、エキスパート間の通信が頻繁に発生するため、ネットワーク性能が学習効率に直結します。従来の TCP/IP スタックを使用した場合と比較して、EFA は大規模分散学習において圧倒的な優位性を示しています。

### 課題3：大規模ストレージと頻繁なチェックポイント

基盤モデル学習には、大規模なコーパスデータと頻繁なチェックポイント保存のための高性能分散ストレージが必要です。

```mermaid
graph TD
    A[DeepSeek V3 671B パラメータ] --> B[推論時メモリ消費量]
    B --> C[Parameters: 1,343GB]
    B --> D[Activations: 200GB] 
    C --> E[合計 1,543GB VRAM]
    D --> E
    E --> F[H100 80GB × 20台分]
```


| データセット | トークン数 | サイズ |
|-------------|-----------|--------|
| Wikitext | 100M | 750MB |
| C4.EN | 156B | 305GB |
| RedPajama-Data-1T | 1T | 5TB |
| RedPajama-Data-v2 | 30T | 170TB |
| **DeepSeek V3 学習データ** | **14.8T** | **推定 100TB 以上** |

DeepSeek V3 の学習データは 14.8 兆トークン、推定 100TB 以上に達しています。さらに、Llama 3.3 70B のチェックポイントはパラメータ 420GB とオプティマイザー状態 560GB を含み、BLOOM 175B では単一チェックポイントが 2.2TB に達します。これらのデータを高速に読み書きできる分散ストレージが不可欠です。

### 課題4：ハードウェア故障との戦い

Amazon Nova の開発では、数万 GPU、数千ホストという規模で 3～4 ヶ月の連続学習を実施しましたが、**1 日に 10～20 回のハードウェア不良**が発生しました。主な不良として、宇宙線等による RAM のビット反転、検出困難な Silent Data Corruption（SDC）、GPU が認識されなくなる XID エラー、GPU とホスト間の通信障害である PCIe バス切断などが挙げられます。

これらの故障に対処するには、包括的なベストプラクティスが必要です。

```mermaid
sequenceDiagram
    participant T as 分散学習ジョブ
    participant F as ハードウェア故障
    participant R as 復旧システム
    participant C as チェックポイント
    
    T->>T: 学習実行中
    F->>T: GPU故障発生
    T->>C: 緊急チェックポイント保存
    R->>R: 故障ノード交換
    C->>T: 最新状態から復旧
    T->>T: 学習再開
```

## 第3部：Amazon EC2 UltraClusters と HyperPod のアーキテクチャ

### Amazon EC2 UltraClusters - 基盤モデル学習に最適化されたスーパーコンピュータ環境

[Amazon EC2 UltraClusters](https://speakerdeck.com/keitaw/scalable-infrastructure-for-large-scale-ai-training-with-aws-sagemaker-hyperpod-at-singapore-ai-hour) は、HyperPod の基盤となるインフラストラクチャです。Compute、Network、Storage の 3 つの主要コンポーネントから構成され、基盤モデル学習に必要なすべての要素を統合的に提供します。

Compute 層では、NVIDIA GPU インスタンス（P5、P6）と AWS Trainium インスタンス（TRN1、TRN2）を提供しています。P5.48xlarge は H100 80GB を 8 基搭載し、合計 640GB の GPU メモリを持ちます。P5e.48xlarge と P5en.48xlarge は H200 141GB を 8 基搭載し、1,128GB の GPU メモリを実現します。次世代の P6 では、B200 や GB200 を搭載し、さらに大規模なモデルに対応します。

Network 層の中核となる Elastic Fabric Adapter（EFA）は、MPI や NCCL のような分散学習ライブラリに最適化された専用ネットワークインターフェースです。SRD プロトコル、カーネルバイパス、GPU-direct RDMA により、512 GPU 構成で従来比 2.5 倍から 25.6 倍のパフォーマンス向上を実現しています。

Storage 層では、Amazon FSx for Lustre が高性能分散ファイルシステムを提供します。FSx for Lustre は Amazon S3 と統合され、大規模コーパスデータへの高速アクセスと、チェックポイントの永続化を両立します。これにより、数百 TB のデータセットに対しても、数千ノードからの同時アクセスを効率的に処理できます。

### HyperPod による分散学習ベストプラクティスの自動化

HyperPod は、Amazon Nova 開発で実証されたベストプラクティスを自動化し、運用負荷を大幅に削減します。ハードウェア不良を前提とした学習システムでは、焼きなまし（Burn-in）による事前検証でハードウェアの初期不良を検出し、チェックポイントの頻繁な保存により復旧時間を最小化し、余剰ハードウェアの確保で即座の交換を可能にします。

包括的なモニタリングシステムは、学習メトリクスの収集、通信性能の監視、ホスト状態の可視化、KPI 設定（goodput 等）を統合的に提供します。Prometheus、CloudWatch、Grafana との連携により、学習の進捗状況や潜在的な問題をリアルタイムで把握できます。

高速な障害対応メカニズムとして、問題発生時の迅速な失敗検出、起動時間の短縮、チェックポイント頻度の最適化が組み込まれています。これらの機能により、Amazon Nova の開発では 40% のコスト削減を達成しました。

### 自動故障復旧機能（Resiliency）の詳細

```mermaid
sequenceDiagram
    participant T as 学習ジョブ
    participant H as HyperPod
    participant N as ノード監視
    participant R as 自動復旧
    
    T->>H: 学習実行中
    N->>H: ノード不良検出
    H->>T: チェックポイント保存
    H->>R: 故障ノード交換開始
    R->>H: 健全ノード配備完了
    H->>T: チェックポイントから復旧
    T->>H: 学習再開
```

### オーケストレーションの選択：Slurm vs Amazon EKS

HyperPod は Slurm と Amazon EKS という 2 つのオーケストレーション方式を提供します。どちらを選択するかは、ワークロードの性質と組織の要件によって決まります。

| 特徴 | Slurm | Amazon EKS |
|------|-------|------------|
| **適用分野** | 伝統的な HPC ワークロード | コンテナ化されたモダンなワークロード |
| **ジョブスケジューリング** | Slurm の豊富なスケジューリング機能 | Kubernetes ネイティブなリソース管理 |
| **ノード管理** | Controller/Login/Compute の 3 層構成 | Kubernetes ノードとして統一管理 |
| **スケーリング** | 静的なリソース割り当て | 動的な容量管理 |
| **アクセス方法** | SSH/SSM 経由のノード直接アクセス | kubectl/SSH/SSM による柔軟なアクセス |
| **分散学習** | SMDDP ライブラリとの最適化 | Kubeflow PyTorchJob との連携 |
| **監視** | CloudWatch + カスタムメトリクス | Container Insights + Prometheus + Grafana |
| **適用例** | 長期間の基盤モデル学習 | 実験的なワークロードや推論処理 |

Slurm は伝統的な HPC 環境で広く使用されており、数週間から数ヶ月にわたる長期間の学習ジョブに適しています。Amazon SageMaker Distributed Data Parallel（SMDDP）ライブラリとの最適化により、安定したリソース配分と高い学習効率を実現します。一方、Amazon EKS はコンテナ化されたワークロードに最適で、柔軟なリソース管理と動的スケーリングを提供します。Kubernetes エコシステムとの連携により、学習と推論を同一クラスターで実行できる利点があります。

## 第4部：実例 - 東京科学大学 Llama 3.3 Swallow 70B プロジェクト

### プロジェクト概要

東京科学大学の岡崎研究室と横田研究室による [Swallow プロジェクト](https://speakerdeck.com/aya_se/data-centric-ai-swallow-corpus-56e2869a-f9bd-46cb-b030-1012235c37f7)は、HyperPod を活用した日本語基盤モデル開発の成功事例です。Llama 3.3 をベースに、独自に構築した「Swallow コーパス」で継続事前学習を行い、70B パラメータの日本語言語モデル「Llama 3.3 Swallow 70B」を開発しました。このモデルは、パラメータ数に対して高い日本語能力を発揮し、商用利用可能な日本語モデルとして注目されています。

### Swallow コーパスの構築 - 632 億ページから 0.27% への精錬

Swallow プロジェクトの中核となるのが、独自に構築した「Swallow コーパス」です。このコーパスは Common Crawl という商用利用可能な最大規模のウェブクローリングデータから、日本語テキストを抽出・精錬したものです。Common Crawl は 2,513 億ページ以上のアーカイブを提供していますが、日本語の割合は約 5% にすぎず、かつ低品質なテキストが多く含まれています。

服部翔氏の報告によれば、2020 年以降の 632 億ページを処理対象とし、9 つのステップを経て高品質な日本語コーパスを構築しました。最終的に、元のデータの **0.27%** にまで精錬されたコーパスが完成しています。この徹底的なフィルタリングプロセスが、Swallow モデルの高い日本語能力の基盤となっています。

### コーパス構築の 3 段階プロセス

コーパス構築は「テキスト抽出 + 日本語判定」「フィルタリング + 重複除去」「正規化 + フッター除去」の 3 段階で実施されました。

第 1 段階の Swallow-RAW では、WARC 形式のファイルから日本語テキストを抽出します。まず HTML の lang 属性とタイトル文のみを使用した迅速な日本語判定で処理対象を約 14 分の 1 に削減し、その後 Trafilatura によるテキスト抽出と、FastText ベースの精密な日本語判定を実施しました。WARC 形式を採用することで、既存の WET 形式経由のコーパス（CC-100、mC4、OSCAR）よりも高品質なテキスト抽出を実現しています。

第 2 段階の Swallow-CLEAN では、品質に基づくフィルタリングと重複除去を行いました。n-gram ベースのルールで繰り返し表現の多い文書を除去し、平仮名の割合やカタカナの割合などの独自ルールで低品質な文書を排除しました。重複除去には MinHash による特徴量集合の一致度計算を採用し、文字 5-gram の特徴量で重複を検知しています。古い時期のクローリングデータは重複により約 20% しか残らない結果となりました。さらに UT1 blocklist によるホスト名フィルタリングや NG 表現の除去により、有害なコンテンツを排除しました。

第 3 段階の Swallow-NORM では、正規化とフッター除去を実施しました。NFKC 正規化により全角・半角のアルファベットや仮名・カタカナ、記号を統一しましたが、その前処理として日本語特有の句読点問題に対処しています。「、」と「，」、「。」と「．」の使用頻度を比較し、文書ごとに統一することで、より一貫性のあるコーパスを構築しました。最後に、「無断転載を禁ず」といったフッターの典型的な表現を除去し、学習データとしての品質を高めています。

### HyperPod を活用した開発の意義

Swallow プロジェクトにおいて HyperPod は、大規模な継続事前学習を安定的に実行するためのプラットフォームとして機能しました。70B パラメータモデルの学習には数百 GPU の協調動作が必要であり、ハードウェア故障への対応や効率的な分散学習の実装が課題となります。HyperPod の自動故障復旧機能により、長期間の学習ジョブを中断することなく継続でき、研究者はモデルアーキテクチャやハイパーパラメータの最適化に集中できました。

このプロジェクトは、地域特化型の基盤モデル開発において HyperPod が実用的なソリューションであることを実証しています。日本語という特定言語に最適化されたモデルを構築するには、独自のコーパスと大規模な計算リソースが必要ですが、HyperPod はそのための包括的な環境を提供しました。

## 第5部：参考リソースと有用な情報集

### 主要リソース

1. **[AI on Amazon SageMaker HyperPod](https://awslabs.github.io/ai-on-sagemaker-hyperpod/)**
   - EKS および Slurm による基盤モデル学習の概要動画
   - 学習時間を最大 40% 短縮する方法

2. **[Amazon Nova 開発事例](https://speakerdeck.com/keitaw/optimizing-foundation-model-development-with-amazon-sagemaker-hyperpod-insights-from-training-the-amazon-nova-model)**
   - 実際の大規模基盤モデル開発での課題と解決策
   - ハードウェア故障対応のベストプラクティス

### モデル分析・比較リソース

3. **[Artificial Analysis - Model Leaderboards](https://artificialanalysis.ai/leaderboards/models)**
   - 最新モデルの性能・コスト分析
   - 客観的なベンチマーク比較データ

### ハンズオンワークショップ

- **[Amazon SageMaker HyperPod for Slurm](https://catalog.workshops.aws/sagemaker-hyperpod/en-US)**
- **[Amazon SageMaker HyperPod for Amazon EKS](https://catalog.workshops.aws/sagemaker-hyperpod-eks/en-US)**

### AWS 公式ドキュメント

- [SageMaker HyperPod クイックスタート](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-quickstart.html)
- [Slurm クラスター運用ガイド](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-operate-slurm.html)
- [EKS クラスター運用ガイド](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks.html)

## 第6部：次章への導入 - 実践への準備

### 本章のまとめ

Amazon SageMaker HyperPod は、以下の現代的課題に対する包括的なソリューションです：

- **規模の課題**: 671B パラメータモデルの効率的学習
- **コストの課題**: 40% のコスト削減を実現
- **可用性の課題**: 自動故障復旧による学習継続
- **柔軟性の課題**: Slurm と EKS による多様なワークロード対応

### 次章での実践内容


1. **HyperPod クラスターの実際の作成**
   - Slurm クラスターのセットアップ
   - EKS クラスターのセットアップ

2. **動作確認とテストジョブ実行**
   - 基本的な動作確認
   - 分散学習ジョブの実行

3. **監視とトラブルシューティング**
   - クラスター状態の監視
   - 一般的な問題の対処法

本章で学んだ理論的背景を基に、実際の HyperPod 環境での作業を体験していきましょう。

## 技術的補足：2025年基盤モデル学習の動向

### MoE アーキテクチャの普及

2025年の基盤モデルでは、Mixture-of-Experts (MoE) アーキテクチャが主流となっています。これにより、総パラメータ数は大幅に増加する一方で、推論時のアクティブパラメータ数は制御され、計算効率とモデル性能の両立が実現されています。

### 量子化技術の進歩

FP8、NVFP4 などの低精度データ型の活用により、メモリ効率と計算効率が大幅に向上しています。特に DeepSeek V3 の FP8 学習や Mistral Large 3 の NVFP4 対応など、実用レベルでの展開が進んでいます。

### 学習効率化の技術革新

Multi-Token Prediction (MTP)、auxiliary-loss-free load balancing など、新しい学習技術により、従来比で大幅なコスト削減が実現されています。

これらの技術動向を踏まえて、HyperPod は最新の基盤モデル学習要件に対応し続けています。
