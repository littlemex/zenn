---
title: "はじめに"
---

:::message
この書籍は [tiny-llm リポジトリ](https://github.com/skyzh/tiny-llm) のドキュメントの日本語翻訳です。
:::

# tiny-llm - 1 週間で LLM サービングを学ぶ

システムエンジニア向けの MLX を使った LLM サービングのコースです。このコードベースは、高レベルのニューラルネットワーク API を使わず、MLX の配列/行列 API のみをベースにしています。これにより、モデルサービング基盤をゼロから構築し、最適化について深く掘り下げることができます。

このコースの目標は、大規模言語モデル（例：Qwen2 モデル）を効率的にサービングする技術を学ぶことです。

**Week 1** では、Qwen2 モデルを使ってレスポンスを生成するために必要なコンポーネント（アテンション、RoPE など）を Python のみで実装します。**Week 2** では、vLLM に似ているが、よりシンプルな推論システム（KV キャッシュ、連続バッチング、フラッシュアテンションなど）を実装します。**Week 3** では、より高度なトピックと、モデルが外部世界とどのように相互作用するかをカバーします。

## なぜ MLX なのか

最近では、NVIDIA GPU をセットアップするよりも、macOS ベースのローカル開発環境を手に入れる方が簡単です。

## なぜ Qwen2 なのか

これは私が最初に触れた LLM です。vLLM のドキュメントで使われている定番の例でもあります。私は vLLM のソースコードを読むことに時間を費やし、それに関する知識を蓄積してきました。

## 書籍

tiny-llm の書籍（英語版）は [https://skyzh.github.io/tiny-llm/](https://skyzh.github.io/tiny-llm/) で公開されています。ガイドに従って、学習を始めることができます。

## コミュニティ

skyzh の Discord サーバーに参加して、tiny-llm コミュニティと一緒に学習できます。

[![Join skyzh's Discord Server](https://skyzh.github.io/tiny-llm/discord-badge.svg)](https://skyzh.dev/join/discord)

## ロードマップ

Week 1 は完成しています。Week 2 は進行中です。

| Week + Chapter | Topic                                                       | Code | Test | Doc | Doc JP |
| -------------- | ----------------------------------------------------------- | ---- | ---- | --- | ------ |
| 1.1            | Attention                                                   | ✅    | ✅   | ✅  | 🚧     |
| 1.2            | RoPE                                                        | ✅    | ✅   | ✅  | 🚧     |
| 1.3            | Grouped Query Attention                                     | ✅    | ✅   | ✅  | 🚧     |
| 1.4            | RMSNorm and MLP                                             | ✅    | ✅   | ✅  | 🚧     |
| 1.5            | Load the Model                                              | ✅    | ✅   | ✅  | 🚧     |
| 1.6            | Generate Responses (aka Decoding)                           | ✅    | ✅   | ✅  | 🚧     |
| 1.7            | Sampling                                                    | ✅    | ✅   | ✅  | 🚧     |
| 2.1            | Key-Value Cache                                             | ✅    | ✅   | ✅  | 🚧     |
| 2.2            | Quantized Matmul and Linear - CPU                           | ✅    | ✅   | 🚧  | 🚧     |
| 2.3            | Quantized Matmul and Linear - GPU                           | ✅    | ✅   | 🚧  | 🚧     |
| 2.4            | Flash Attention 2 - CPU                                     | ✅    | ✅   | 🚧  | 🚧     |
| 2.5            | Flash Attention 2 - GPU                                     | ✅    | ✅   | 🚧  | 🚧     |
| 2.6            | Continuous Batching                                         | ✅    | ✅   | ✅  | 🚧     |
| 2.7            | Chunked Prefill                                             | ✅    | ✅   | ✅  | 🚧     |
| 3.1            | Paged Attention - Part 1                                    | 🚧    | 🚧   | 🚧  | 🚧     |
| 3.2            | Paged Attention - Part 2                                    | 🚧    | 🚧   | 🚧  | 🚧     |
| 3.3            | MoE (Mixture of Experts)                                    | 🚧    | 🚧   | 🚧  | 🚧     |
| 3.4            | Speculative Decoding                                        | 🚧    | ✅   | 🚧  | 🚧     |
| 3.5            | RAG Pipeline                                                | 🚧    | 🚧   | 🚧  | 🚧     |
| 3.6            | AI Agent / Tool Calling                                     | 🚧    | 🚧   | 🚧  | 🚧     |
| 3.7            | Long Context                                                | 🚧    | 🚧   | 🚧  | 🚧     |

カバーされていないその他のトピック：量子化/圧縮された KV キャッシュ、プレフィックス/プロンプトキャッシュ、サンプリング、ファインチューニング、小さなカーネル（softmax、silu など）

## オリジナル版について

この書籍は、skyzh 氏による [tiny-llm](https://github.com/skyzh/tiny-llm) プロジェクトの日本語翻訳版です。オリジナルの英語版は[こちら](https://skyzh.github.io/tiny-llm/)で公開されています。
