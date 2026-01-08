---
title: "はじめに"
---

# tiny-llm - 1週間でLLMサービングを学ぶ

[![CI (main)](https://github.com/skyzh/tiny-llm/actions/workflows/main.yml/badge.svg)](https://github.com/skyzh/tiny-llm/actions/workflows/main.yml)

システムエンジニア向けのMLXを使ったLLMサービングのコースです。このコードベースは、高レベルのニューラルネットワークAPIを使わず、MLXの配列/行列APIのみをベースにしています。これにより、モデルサービング基盤をゼロから構築し、最適化について深く掘り下げることができます。

このコースの目標は、大規模言語モデル（例：Qwen2モデル）を効率的にサービングする技術を学ぶことです。

**Week 1**では、Qwen2モデルを使ってレスポンスを生成するために必要なコンポーネント（アテンション、RoPEなど）をPythonのみで実装します。**Week 2**では、vLLMに似ているが、よりシンプルな推論システム（KVキャッシュ、連続バッチング、フラッシュアテンションなど）を実装します。**Week 3**では、より高度なトピックと、モデルが外部世界とどのように相互作用するかをカバーします。

## なぜMLXなのか

最近では、NVIDIA GPUをセットアップするよりも、macOSベースのローカル開発環境を手に入れる方が簡単です。

## なぜQwen2なのか

これは私が最初に触れたLLMです。vLLMのドキュメントで使われている定番の例でもあります。私はvLLMのソースコードを読むことに時間を費やし、それに関する知識を蓄積してきました。

## 書籍

tiny-llmの書籍（英語版）は [https://skyzh.github.io/tiny-llm/](https://skyzh.github.io/tiny-llm/) で公開されています。ガイドに従って、学習を始めることができます。

## コミュニティ

skyzhのDiscordサーバーに参加して、tiny-llmコミュニティと一緒に学習できます。

[![Join skyzh's Discord Server](https://skyzh.github.io/tiny-llm/discord-badge.svg)](https://skyzh.dev/join/discord)

## ロードマップ

Week 1は完成しています。Week 2は進行中です。

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

カバーされていないその他のトピック：量子化/圧縮されたKVキャッシュ、プレフィックス/プロンプトキャッシュ、サンプリング、ファインチューニング、小さなカーネル（softmax、siluなど）

## オリジナル版について

この書籍は、skyzh氏による[tiny-llm](https://github.com/skyzh/tiny-llm)プロジェクトの日本語翻訳版です。オリジナルの英語版は[こちら](https://skyzh.github.io/tiny-llm/)で公開されています。
