---
title: "vLLM v0.15.0 リリースノート解説"
emoji: "⛳"
type: "idea" # tech: 技術記事 / idea: アイデア
topics: ["vllm", "llm", "gpu", "quantization", "mamba"]
published: true
---

## メモ（ここは筆者のメモ欄です）

- [ ] Mamba 正直なんでうまくいくのかよくわからない。。定常状態になると分布収束するということだから生成うまくいかないのでは。。過去情報指数減衰しそうな。。完全なマルコフ連鎖ではなく入力とセレクティブであり、並列スキャンでマシン力技しているから LSTM より性能頑張れるということかな。あんまわからん。

## はじめに

vLLM v0.15.0 が 2025 年 1 月 29 日にリリースされました。このバージョンは 335 コミット、158 人の貢献者による大規模アップデートであり、特に NVIDIA Blackwell（GB200 等）向けの最適化、マルチモーダルモデルの拡充、そして非同期処理の進化が目玉となっています。

https://github.com/vllm-project/vllm/releases/tag/v0.15.0

本記事では、v0.15.0 の主要機能を解説します。

## vLLM アーキテクチャ概要

vLLM は大規模言語モデル（LLM）の推論を高速化するフレームワークであり、複数の要素技術を組み合わせることで高性能な推論環境を提供しています。

### 主要コンポーネント

vLLM の推論パイプラインを支える主要コンポーネントとして、PagedAttention、Continuous Batching、Model Runner、Speculative Decoding などがあります。内部構成は以下が参考になります。

https://zenn.dev/tosshi/articles/f64ba0b86e330b

## リリース概要

v0.15.0 は 6 つの主要テーマで整理することができます。第一に、新規アーキテクチャと LoRA 対応モデルの大幅追加を含むモデルサポート拡張です。第二に、v0.14.0 の非同期スケジューリングをパイプライン並列対応に拡張した非同期処理とパイプライン並列の強化です。第三に、NVIDIA GB200/SM100F 向け FP4 量子化と FlashInfer 統合による Blackwell 世代 GPU 最適化です。第四に、ROCm、XPU、TPU それぞれの環境での性能向上を実現する AMD/Intel/TPU サポート強化です。第五に、MXFP4、FP8 KV キャッシュ、Intel Quantization Toolkit 統合を含む量子化機能の拡充です。第六に、Responses API、FIPS 準拠、非推奨機能の削除などの API 機能追加と破壊的変更です。

## 主要アップデート詳細

### モデルサポート拡張

#### 新規アーキテクチャ

v0.15.0 では複数の新規モデルアーキテクチャが追加されました。話題の Kimi-K2.5 ([Technical Report](https://github.com/MoonshotAI/Kimi-K2.5/blob/master/tech_report.pdf)) は長文脈処理に特化したアーキテクチャとして新たにサポートされています。マルチモーダル VLM では Molmo2、Step3vl 10B、Step1 が対応しました。また、GLM-Lite は軽量版 GLM モデルとして、Eagle2.5-8B VLM は視覚言語モデルとしてサポートされています。

https://github.com/MoonshotAI/Kimi-K2.5/tree/master

#### LoRA 拡張対応

LoRA（Low-Rank Adaptation）のサポートが拡張され、Nemotron-H、InternVL2、MiniMax M2 の各モデルに対応しました。LoRA については以下が参考になります。

https://zenn.dev/tosshi/articles/e43f0d9eb83601

#### Speculative Decoding 拡張

Speculative Decoding のサポートが拡張され、EAGLE3、Qwen3 VL MoE（Vision-Language MoE）、および各種ドラフトモデルで推論高速化が可能になりました。Speculative Decoding はドラフトモデルで複数トークンを先行生成し、メインモデルで検証することで推論を高速化する技術です。

ドラフトモデルを使わない Self Speculative Decoding for Diffusion Large Language Models などを見かけましたがちゃんと読んでません。にゃーん。

https://arxiv.org/html/2510.04147v1

#### 埋め込みモデル対応

埋め込みモデルとして BGE-M3 と ColBERT が新たにサポートされました。BGE-M3 はスパース埋め込みに対応しており、高次元空間での効率的な検索が可能です。ColBERT はコンテキスト依存埋め込みをサポートし、クエリとドキュメント間の詳細な類似度計算を実現します。

---

### エンジンコア改善

#### パイプライン並列での非同期スケジューリング（v0.14.0 からの進化）

v0.14.0 で非同期スケジューリングがデフォルト有効化されましたが、v0.15.0 ではパイプライン並列との併用が可能になりました。これにより、大規模モデルを複数 GPU に分割しながらも非同期処理の恩恵を受けられるようになっています。従来はパイプライン並列と非同期スケジューリングを同時に使用することができませんでしたが、この制約が解消されたことで、より柔軟なデプロイメント構成が可能になりました。

```python
engine_args = EngineArgs(
    async_scheduling=True,                          
    pipeline_parallel_size=4,                                                          
) 
```

#### Mamba Prefix Caching

Mamba は状態空間モデル（SSM）ベースのアーキテクチャであり、Transformer のアテンション機構を使用しません。[Mamba: Linear-Time Sequence Modeling with Selective State Spaces](https://arxiv.org/abs/2312.00752) で提案された Mamba は線形時間での系列モデリングを実現していますが、v0.14.0 まではプレフィックスの再利用が困難でした。v0.15.0 では block-aligned アプローチにより prefix caching を実現し、約 2 倍の高速化を達成しています。

https://arxiv.org/html/2312.00752v2

**Mamba の仕組み**

Mamba の状態更新は以下の[数学的な漸化式](https://arxiv.org/html/2312.00752)で定義されます。

$$
h_t = Ā·h_{t-1} + B̄·x_t
$$

ここで $h_t$ は時刻 $t$ の状態ベクトル（固定サイズ）、$x_t$ は現在の入力トークンです。この式が示す重要な性質は **Markovian（マルコフ性）**です。つまり、$h_t$ を計算するには $h_{t-1}$ と $x_t$ だけあれば良く、それ以前の状態 $h_1, h_2, ..., h_{t-2}$ は不要です。なぜなら、過去の全情報は既に $h_{t-1}$ に**圧縮されている**からです。

:::message
マルコフ性は名前を聞くと難しく聞こえますがマルコフ連鎖などは統計検定でも頻出の問題で、推移確率行列や同時確率関数、定常状態の導出は面白いので解いてみると良いです。Mamba 自体は単純なマルコフ連鎖ではないです。

https://www.data-arts.jp/jssc/grade1semi/2017-06/1/q11.html
:::

これは Transformer の KV キャッシュとは根本的に異なります。Transformer は全トークンの key と value を保存する必要があるため、メモリ使用量が O(sequence_length) で増加します。一方、Mamba は固定サイズの状態 $h_t$ のみを保持するため、メモリ使用量は **O(1)** です。

**なぜ Prefix Caching が困難だったのか**

Mamba の状態は Markovian であるため、理論的にはキャッシング可能です。しかし、実装上の課題がありました。Transformer の KV キャッシュはトークン単位で区切れますが、Mamba の状態は連続的に更新されるため、「どこで区切るか」が明確ではありませんでした。可変長のチャンクで処理すると、同じプレフィックスでも異なるチャンク分割になる可能性があり、チャンク単位でハッシュ値でキーを管理する実装であるため、可変長だとハッシュ値が変動するのでキャッシュの探索が困難でした。

https://github.com/vllm-project/vllm/blob/f176443446f659dbab5315e056e605d8984fd976/vllm/v1/core/kv_cache_manager.py#L184-L194

**Block-Aligned アプローチ：どうやってキャッシュを探すのか**

v0.15.0 では [PR #30877](https://github.com/vllm-project/vllm/pull/30877) により block-aligned アプローチが導入され、この問題が解決されました。このアプローチは `--enable-prefix-caching --mamba-cache-mode align` フラグで有効化できます。

キャッシュ探索の仕組みは以下の通りです。

1. **Block-Aligned スケジューリング**：トークン列を `block_size` の倍数単位で処理します
2. **決定的な状態計算**：同じトークン列を処理すれば、必ず同じ状態になります
  （ $h_t = Ā·h_{t-1} + B̄·x_t$ は決定的）
3. **ブロックハッシュの計算**：各ブロック（トークン列）からハッシュ値を計算します
4. **ハッシュマッチング**：新リクエストとキャッシュ済みのブロックハッシュを比較します
5. **キャッシュヒット**：ハッシュが一致すれば、そのブロックの最終状態を再利用できます

各スケジューリングステップ（例: 64 tokens = 4 blocks）では、内部的に $h_1$ → $h_2$ → ... → $h_{64}$ と状態が更新されます。しかし、Markovian 性により、中間状態 $h_1$, $h_2$, ..., $h_{63}$ は保存不要です。$h_{64}$ だけあれば、次のトークンから継続できるためです。

実装では、各スケジューリングステップで `chunk_len // block_size` 個のブロックを割り当てますが、最初の `(chunk_len // block_size) - 1` 個は null-block（プレースホルダー）として、最後のブロックのみに物理的に状態を保存します。これにより、メモリ使用量を最小化しながら、ブロック単位でのキャッシング機構を実現しています。

#### Session-based Streaming Input

Session-based Streaming Input が追加され、非同期ジェネレータを受け入れるストリーミング入力が可能になりました。これによりインタラクティブなワークロード（チャット、対話型エージェント）での応答性が向上します。従来のバッチ処理では全入力を事前に用意する必要がありましたが、セッションベースのストリーミングでは入力を段階的に供給できるため、リアルタイム性の高いアプリケーションに適しています。

#### Model Runner V2 の VLM 対応

Model Runner V2 が Vision-Language Model（VLM）に対応し、画像入力を伴う推論の実装が統一されました。これにより、テキストと画像を組み合わせたマルチモーダル推論が Model Runner V2 の統一的なインターフェースで実行可能になっています。

#### メモリ最適化

メモリ効率を向上させるための 2 つの最適化が実装されました。LoRA の inplace loading では、LoRA アダプターをメモリ上で直接読み込むことでメモリフットプリントを削減しています。また、KV キャッシュオフロード改善では、重複ロードを防止しメモリ転送を最小化することで、メモリとストレージ間のデータ移動コストを低減しています。

---

### 3. ハードウェア最適化

#### NVIDIA Blackwell 世代（GB200/SM100F）

##### FlashInfer MLA と TensorRT-LLM プリフィル

MLA を 高速実行するためのカーネル実装である FlashInfer MLA（Multi-Head Latent Attention）がデフォルトバックエンドに採用され、TensorRT-LLM がプリフィル（初期プロンプト処理）のデフォルトになりました。この変更は [PR #32339](https://github.com/vllm-project/vllm/pull/32339) で導入され、[PR #32615](https://github.com/vllm-project/vllm/pull/32615) で再適用されています。FlashInfer は [FlashInfer GitHub リポジトリ](https://github.com/flashinfer-ai/flashinfer)で開発されているアテンション計算の最適化ライブラリであり、DeepSeek の Multi-Latent Attention に対してネイティブサポートを提供しています。

従来の CUTLASS_MLA バックエンドから FlashInfer MLA への変更は、vLLM 開発チームによるベンチマークに基づいて決定されました。FlashInfer は v0.4.0 で Blackwell GPU（SM 10.0, 10.3, 12.0, 12.1）のサポートを追加しており、B200、B300、RTX 50 シリーズなどの新世代ハードウェアに最適化されています。

##### FP4 量子化で最大 65% 高速化

FP4（4 ビット浮動小数点）量子化が Blackwell SM100F で最適化され、最大 65% の性能向上を実現しています。この最適化は [PR #32520](https://github.com/vllm-project/vllm/pull/32520) で導入され、PTX 8.8 で有効化された 256 ビット幅の `load.global` 命令を活用することで実現されました。従来の実装では複数の小さなロード操作を発行していましたが、新しいカーネルではメモリ読み取りを単一の 256 ビットトランザクションに統合することで、グローバルメモリ命令のオーバーヘッドを削減し、L2 キャッシュ利用効率を向上させています。

ベンチマークは NVIDIA B200（Blackwell SM100F）ハードウェアで実施され、行列次元 N=8192、K=7168/14336/28672、バッチサイズ 1〜8192 の条件下で入力量子化レイテンシを測定しました。結果として、バッチサイズが大きくなるほど高速化が顕著になり、FlashInfer と比較して最大 2.33 倍の改善を達成しています。また、密モデル（Llama-3.3-70B）のエンドツーエンドテストでは、出力トークンスループットが 756 tokens/s から 785 tokens/s に向上し、約 4% の増加を記録しています。この最適化には CUDA 12.9 以上が必要であり、SM100F GPU でのみ有効です。

##### MoE 最適化

Grouped TopK kernel fusion により、MoE（Mixture of Experts）モデルのスループットが 1.2〜2% 向上しました。この最適化は Expert 選択のための TopK 演算を複数まとめて実行することで、カーネル起動のオーバーヘッドを削減しています。

##### Torch Compile 対応

`torch.compile` への対応が拡張され、`SiluAndMul`（活性化関数）と `QuantFP8`（FP8 量子化演算）が JIT コンパイルによる最適化の対象になりました。これにより、PyTorch の最新の最適化機構を活用した高速な推論が可能になっています。

---

#### AMD ROCm

ちょっとあんまりまとめる気が起きないので内容薄いです。

##### MoRI EP バックエンド

MoRI（Modular RDMA Interface）EP バックエンドが [PR #28664](https://github.com/vllm-project/vllm/pull/28664) で追加され、Expert Parallel（EP）通信が大幅に効率化されました。

##### コンシューマ GPU での Flash Attention Triton

RDNA3/RDNA4 アーキテクチャのコンシューマ GPU（RX 7000/8000 シリーズ）で、Triton 実装の Flash Attention が利用可能になりました。

##### FP4 対応

MLA（Multi-Head Latent Attention）のプロジェクション層 GEMMs で FP4 量子化がサポートされました。これにより、AMD GPU でも低精度演算による高速化が可能になっています。

---

#### その他プラットフォーム

ちょっとあんまりまとめる気が起きないので内容薄いです。

##### TPU
Google の TPU（Tensor Processing Unit）でパイプライン並列サポートが追加され、大規模モデルの TPU 展開が容易になりました。これにより、モデルを複数の TPU チップに分割して推論を実行できるようになっています。

##### Intel XPU
分散通信の改善により、Intel Data Center GPU Max（Ponte Vecchio）での複数 GPU 推論が高速化しました。

##### NUMA-aware CPU 加速
ARM 系 CPU 環境で NUMA（Non-Uniform Memory Access）を考慮した最適化が [PR #32792](https://github.com/vllm-project/vllm/pull/32792) で実装され、CPU での推論性能が大幅に向上しました。

---

### 4. 量子化機能の拡充

#### MXFP4 W4A16

MXFP4（Microscaling FP4）による W4A16（重み 4 ビット、アクティベーション 16 ビット）量子化が、compressed-tensors 形式の MoE モデルで利用可能になりました。MXFP4 はブロック単位でスケーリングファクターを適用することで、従来の FP4 量子化よりも高い精度を維持しながら低ビット化を実現する手法です。

#### Non-gated MoE 量子化

gate を持たない MoE モデルに対して、Marlin（4 ビット整数量子化）、NVFP4（NVIDIA FP4）、FP8（8 ビット浮動小数点）、INT8（8 ビット整数）の各量子化方式が対応しました。従来は gate 機構を持つ標準的な MoE モデルのみが量子化の対象でしたが、この拡張により多様な MoE アーキテクチャで量子化の恩恵を受けられるようになっています。

#### FP8 KV キャッシュのオプション追加

FP8 KV キャッシュに粒度オプションが追加され、Per-tensor（テンソル全体で 1 つのスケーリングファクター）と Per-attention-head（アテンションヘッドごと）の 2 つの粒度から選択できるようになりました。Per-tensor は実装が簡単でメモリ効率が高く、Per-attention-head はより細かい精度制御が可能です。これにより、精度と性能のトレードオフを細かく調整できるようになっています。

#### Intel Quantization Toolkit 統合

Intel 製量子化ツールキットが統合され、Intel XPU 向けの最適化された量子化が使用できるようになりました。これにより、Intel のハードウェア特性に最適化された量子化モデルを vLLM で直接使用できます。

---

### 5. API & フロントエンド機能

#### Responses API

部分的なメッセージ生成をサポートする Responses API が追加されました。この API を使用することで、ストリーミング生成中の中間結果を構造化された形で取得できるようになり、リアルタイムな UI 更新やプログレス表示が容易になります。

#### OpenAI API 拡張

OpenAI 互換 API に `skip_special_tokens` 設定が追加され、特殊トークン（`<|endoftext|>` 等）の出力制御が可能になりました。これにより、エンドユーザーに見せるべきでないシステムトークンを除外した出力を生成できます。

#### スコアリングエンドポイント

柔軟な入力フォーマットに対応したスコアリングエンドポイントが追加され、複数候補テキストのスコアリングが容易になりました。これにより、複数の生成候補から最適なものを選択する際の評価が効率的に行えます。

#### プロンプト前処理レンダリングエンドポイント

プロンプトテンプレートの事前レンダリングをテストできるエンドポイントが追加されました。これにより、実際の推論を実行する前にプロンプトの展開結果を確認でき、テンプレートのデバッグが容易になります。

#### FIPS 140-3 準拠ハッシュ

エンタープライズユーザー向けに、FIPS 140-3 準拠のハッシュオプションが追加されました。FIPS 140-3 は米国政府の暗号モジュール標準であり、セキュリティ要件の厳しい環境での利用が可能になっています。

---

### 6. 破壊的変更

v0.15.0 では後方互換性のない変更がいくつか導入されました。既存のコードベースを v0.15.0 にアップグレードする際は注意が必要です。

#### 削除されたメトリクス

Prometheus メトリクスとして提供されていた `vllm:time_per_output_token_seconds` が削除され、代わりに `vllm:inter_token_latency_seconds` を使用する必要があります。この変更は、トークン生成のレイテンシをより正確に測定するための改善です。

#### 削除された量子化方式

DeepSpeedFp8 と RTN（Round-to-Nearest）量子化が削除されました。これらの量子化方式を使用していた場合は、他の量子化方式（FP8、INT8、Marlin など）への移行が必要です。

#### 非推奨化

HQQ 量子化が非推奨化され、将来バージョンで削除予定です。HQQ を使用している場合は、早期に代替の量子化方式への移行を検討することが推奨されます。

#### 環境変数のクリーンアップ

古い環境変数が削除されました。詳細は [vLLM v0.15.0 公式リリースノート](https://github.com/vllm-project/vllm/releases/tag/v0.15.0)を参照してください。

---

### 7. 注目のバグ修正

v0.15.0 では重要なバグ修正が複数含まれています。Eagle ドラフトモデルの設定エラーが修正され、Speculative Decoding が正しく動作するようになりました。DeepSeek-V3.1 と DeepGEMM を組み合わせた際に発生していたスケール形状の不整合が解消され、安定した推論が可能になっています。また、Data Parallelism（DP）と MoE を組み合わせた分散推論で発生していたデッドロック問題が修正されました。構造化出力のバイトフォールバック処理も改善され、エンコーディングエラー時の処理がより堅牢になっています。

---

## まとめ

本リリースの主要な改善点として、Blackwell 世代 GPU での FP4 量子化による最大 65% の高速化、パイプライン並列と非同期スケジューリングの併用による大規模モデルでの並列処理の進化、Mamba prefix caching による約 2 倍の高速化などが挙げられます。Mamba prefix caching についてはよくわかっていなかったので勉強になりました。

### おまけ： vLLM と vLLM-Omni の関係性

:::message alert
vLLM-Omni v0.14.0 のリリース時の個人的な疑問解消のセクションなので vLLM v0.15.0 のリリースノート解説とは関係ありません。
:::

vLLM v0.15.0 と同時期（2025 年 1 月 31 日）に [vLLM-Omni v0.14.0](https://github.com/vllm-project/vllm-omni/releases/tag/v0.14.0) がリリースされました。vLLM と vLLM-Omni の関係性を理解することがこのセクションの目的です。

vLLM-Omni は [vLLM の拡張フレームワーク](https://github.com/vllm-project/vllm-omni)であり、vLLM のフォークではありません。vLLM が大規模言語モデルのテキスト生成に特化しているのに対し、vLLM-Omni はマルチモーダル推論（画像、動画、音声）と Non-autoregressive モデル（Diffusion Transformer）のサポートを追加しています。

vLLM-Omni は vLLM を内部的に直接使用していることが、実装ファイルから確認できました。

1. **コアクラスのモンキーパッチ適用** ([`vllm_omni/patch.py`](https://github.com/vllm-project/vllm-omni/blob/v0.14.0/vllm_omni/patch.py))
   - vLLM のコアコンポーネントをインポート：`TokensPrompt`, `MRotaryEmbedding`, `EngineCoreOutput`, `EngineCoreRequest`, `Request`
   - これらを Omni 拡張版（`OmniEngineCoreOutput`, `OmniTokensPrompt` など）で置き換えるモンキーパッチを適用
   - `sys.modules` をスキャンして vLLM モジュールを動的に拡張

2. **Worker クラスの直接継承** ([`vllm_omni/worker/gpu_ar_worker.py`](https://github.com/vllm-project/vllm-omni/blob/v0.14.0/vllm_omni/worker/gpu_ar_worker.py#L21))
   - Autoregressive ステージの Worker は vLLM の `GPUWorker` を直接継承
   - vLLM の分散環境初期化関数 `init_worker_distributed_environment` を使用

3. **Model Runner の拡張** ([`vllm_omni/worker/gpu_ar_model_runner.py`](https://github.com/vllm-project/vllm-omni/blob/v0.14.0/vllm_omni/worker/gpu_ar_model_runner.py))
   - vLLM の以下のコンポーネントをインポート：
     - `vllm.distributed.kv_transfer`（KV キャッシュ転送）
     - `vllm.v1.spec_decode.eagle.EagleProposer`（Speculative Decoding）
     - `vllm.v1.worker.gpu_model_runner`（Model Runner 基底クラス）
     - `vllm.forward_context`, `vllm.v1.core.sched.output` など

4. **Sampling Parameters の利用** ([`vllm_omni/entrypoints/omni.py`](https://github.com/vllm-project/vllm-omni/blob/ed89c8b0436999e9210f11363f4eb512330a9dfa/vllm_omni/entrypoints/omni.py#L17))
   - LLM ステージでは vLLM の `SamplingParams` をそのまま使用
   - vLLM のロガー `init_logger` も利用

これらの実装からわかるように、vLLM-Omni の Autoregressive 推論部分は vLLM のコードを直接継承・拡張しており、KV キャッシュ管理、PagedAttention、Speculative Decoding、分散通信などのコア技術をそのまま活用しています。

つまり vLLM 側でハードウェアバックエンドが追加されれば、vLLM-Omni の Autoregressive ステージでも自動的にそのハードウェアが利用可能になります。同様に、vLLM の KV キャッシュ管理や最適化の改善は、vLLM-Omni の AR 推論性能にも直接反映されるはずですが vLLM-Omni 側が vLLM 実装をどう取り込むかはわからないため vLLM をベースとして利用しつつ、独自の機能が追加されている想定で利用すると良いでしょう。

## 参考資料

本記事の執筆にあたり、以下の資料を参照しました。

### 公式ドキュメント
- [vLLM v0.15.0 公式リリースノート](https://github.com/vllm-project/vllm/releases/tag/v0.15.0) - 本記事の主要な情報源
- [vLLM v0.14.0 リリースノート](https://github.com/vllm-project/vllm/releases/tag/v0.14.0) - 前バージョンとの比較用
- [vLLM 公式ドキュメント](https://docs.vllm.ai/) - アーキテクチャと使用方法の詳細
- [vLLM-Omni v0.14.0 リリースノート](https://github.com/vllm-project/vllm-omni/releases/tag/v0.14.0) - マルチモーダル拡張フレームワーク
- [vLLM-Omni GitHub リポジトリ](https://github.com/vllm-project/vllm-omni) - vLLM のマルチモーダル推論拡張

### 技術資料
- [FlashInfer GitHub リポジトリ](https://github.com/flashinfer-ai/flashinfer) - FlashInfer の実装とドキュメント
- [Mamba: Linear-Time Sequence Modeling with Selective State Spaces](https://arxiv.org/abs/2312.00752) - Mamba アーキテクチャの原論文
- [NVIDIA Blackwell Architecture Whitepaper](https://www.nvidia.com/en-us/data-center/technologies/blackwell-architecture/) - Blackwell アーキテクチャの技術詳細
- [AMD ROCm Documentation](https://rocm.docs.amd.com/) - AMD GPU の開発環境とツール
- [ROCm/mori](https://github.com/ROCm/mori) - AMD の Modular RDMA Interface フレームワーク
