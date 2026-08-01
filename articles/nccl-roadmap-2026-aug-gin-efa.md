---
title: "NCCL Roadmap Aug-Oct 2026: GIN解説とEFA課題整理"
emoji: "🔧"
type: "tech"
topics: ["NCCL", "GPU", "EFA", "分散学習", "NVIDIA"]
published: false
---

# はじめに

NVIDIA が公開した [NCCL Roadmap: Aug-Oct 2026 (GitHub Issue #2272)](https://github.com/NVIDIA/nccl/issues/2272) から、大規模分散学習・推論に携わるエンジニアが押さえるべきポイントを3カテゴリに絞って解説する。

対象読者は NCCL を日常的に使う ML インフラエンジニア。GIN の基礎概念から、AWS EFA 固有の課題、そして今後のロードマップで特に影響の大きい3機能を取り上げる。

## GIN (GPU-Initiated Networking) とは何か

### 概要

GIN は NCCL 2.28.7 (2025年11月) で導入された Device API の通信モードの一つで、GPU カーネル内からネットワーク RDMA 操作を直接発行する仕組みである。従来の NCCL は「CPU がすべての通信をオーケストレーションする (host-initiated)」モデルだったが、GIN はこの CPU 介在を排除し、GPU スレッドが `put` / `get` / `signal` / `flush` といった one-sided RDMA 操作をカーネルコードから直接呼び出せるようにした。

技術的詳細は arXiv 論文 [GPU-Initiated Networking for NCCL (arXiv:2511.15076)](https://arxiv.org/abs/2511.15076) に記載されている。

### なぜ重要か

GIN が解決する問題は明確で、以下の3点に集約される。

- MoE (Mixture-of-Experts) の動的トークンルーティングのように、計算と通信が密結合し不定形の通信パターンが発生するワークロードで、CPU プロキシのオーバーヘッドがボトルネックになる
- 小メッセージ (4-128 バイト) のレイテンシを大幅に削減する: GDAKI バックエンドで往復 16.7us (NVSHMEM IBGDA 同等の 16.0us、従来 IBRC は 24.3us)
- SM 占有を削減し、モデル計算に GPU リソースを還元できる

### アーキテクチャ: 3層構造

GIN は以下の3層で構成される。

```
[CUDA Kernel]                    <- Device GIN API (put/get/signal/flush/wait)
      |
[GIN Backend]                    <- GDAKI (GPU->NIC 直接) or Proxy (GPU->CPU->NIC)
      |
[Network: IB/RoCE/EFA]
```

各層の役割は以下の通り。

1. **Device GIN API**: `ncclGin` クラスが提供する CUDA デバイス関数群。Signal (リモート通知) と Counter (ローカル完了追跡) で非同期完了を管理する
2. **GDAKI バックエンド**: GPU スレッドが RDMA WQE (Work Queue Entry) を直接構築し、NIC の doorbell レジスタに書き込む。CPU 介在ゼロ。ConnectX-6 Dx 以降が必要
3. **Proxy バックエンド**: GPU スレッドが GFD (128 バイトの Fast Descriptor) をロックフリーキューに書き込み、CPU 側の proxy スレッドがポーリングして IB verbs を発行する

### NCCL 2.31 での GIN 進化

| 機能 | 説明 |
|------|------|
| Simultaneous GIN proxy and GDAKI | 異なる communicator で Proxy と GDAKI を同時使用可能にする。ワークロード特性に応じた最適バックエンド選択が可能 |
| EFA GDA support in GIN | AWS EFA 上で GPU-initiated 通信を実現する新バックエンド (後述) |
| GIN over Sockets | IB/RoCE 不要で TCP 経由の GIN 動作。開発やテスト環境向け |

## AWS EFA 関連アップデートと現在の課題

### EFA + NCCL 統合の現状

AWS EFA (Elastic Fabric Adapter) は `aws-ofi-nccl` プラグイン経由で NCCL と統合されている。libfabric の Reliable Datagram エンドポイントにマッピングし、SRD (Scalable Reliable Datagram) プロトコルで通信する。

### NCCL 2.31 の目玉: EFA GDA support in GIN

GIN に `NCCL_GIN_TYPE_EFA_GDA` という EFA 専用バックエンドが追加される。GPU カーネルが CPU プロキシを介さず EFA 経由で直接 Put/Signal/Flush を発行可能になる。内部実装は `efa-dp-direct` ライブラリを NCCL に統合したもので、NVIDIA/nccl の PR #2273 系列で実装が入っている。

対応プラットフォームは P5en / P6-B200 / P6-B300 のみで、EFA ハードウェア完了カウンタが必要なため他のインスタンスタイプでは利用できない。

### 現在の課題

EFA + GIN の組み合わせにはまだ解決途上の課題が複数存在する。

| 課題 | 詳細 | 出典 |
|------|------|------|
| Signal coalescing と SRD 順序保証の衝突 | GIN proxy 経路で signal coalescing 導入後、受信側で barrier タイムアウト。SRD の非順序配送が coalesced-doorbell の前提と衝突する | [aws-ofi-nccl #1327](https://github.com/aws/aws-ofi-nccl/issues/1327) |
| GDR の PCIe 距離判定 | `ncclTopoCheckGdr()` が PCIe トポロジ距離で GDR 有効/無効を判定する。`NCCL_NET_GDR_LEVEL` の不適切な設定で意図しない無効化が発生しうる | NCCL `src/graph/paths.cc` |
| マルチレール対応の成熟度 | P5en の GPU あたり2レール構成向けに `rail = contextId % num_rails` で振り分けるロジックが入ったばかり | [aws-ofi-nccl PR #1292](https://github.com/aws/aws-ofi-nccl/pull/1292) |
| GDAKI doorbell 競合条件 | 並行 post で submitted_count がキャッシュされハングする問題。atomic load への修正で対処済み | [NVIDIA/nccl PR #2314](https://github.com/NVIDIA/nccl/pull/2314) |

### aws-ofi-nccl の直近リリース

- **v1.20.0** (2026-06-25): NCCL v2.30 対応、net v12 plugin インターフェース
- **v1.18.0**: P6-B300 カスタムチューナー追加
- **v1.17.0**: Control-over-Write プロトコル、P5en/P6 チューナープロファイル

## 注目機能3選

### Cost-model rearchitecture: アルゴリズム選択ロジック再設計

NCCL は alpha-beta モデル (`time = latency + dataSize / bandwidth`) で Ring/Tree/NVLS/CollNet/PAT とプロトコル (LL/LL128/Simple) の全組み合わせの予測時間を計算し最速を選ぶ。しかし GPU 世代ごとの経験的補正定数をハードコードする方式のため、新世代で不正確な選択が頻発している。

具体例として、Blackwell の symmetric ReduceScatter でモデルが「LD, 11 CTA」(33.12us) を選ぶが、実測では「LD, 64 CTA」(13.84us) が2.4倍速いという問題が報告されている ([#2305](https://github.com/NVIDIA/nccl/issues/2305))。修正 PR は「Blackwell, 1ノード, 2/4ランク」のみの狙い撃ちオーバーライドで、根本解決ではない ([#2307](https://github.com/NVIDIA/nccl/pull/2307))。

ロードマップでは NCCL 2.31 で「Begin redesigning NCCL's internal cost-model logic so algorithm selection can become more accurate and extensible」と明記されている。Symmetric kernel、GIN、カスタムカーネルフックの増加に伴い、拡張可能な設計への移行が急務となっている。

### NCCL Notify + RAS diagnostics: 障害検知・通知の構造化

10000+ GPU クラスタでのハング原因特定は現状極めて困難である。`NCCL_DEBUG=INFO` はイベントログであり「どのランクがどこで止まっているか」のグローバル可視性を持たない。RAS (Reliability, Availability, Serviceability) は NCCL 2.28 で導入された分散型健全性監視だが、以下の既知の限界がある。

- タイムアウトを見逃すケースがある ([#1730](https://github.com/NVIDIA/nccl/issues/1730))
- 遅延側ではなく先行側しか報告しない ([#1911](https://github.com/NVIDIA/nccl/issues/1911))
- enum 順序バグによるランク数不整合がある ([#2263](https://github.com/NVIDIA/nccl/pull/2263))

NCCL Notify は、RAS が検知した障害をアプリケーション側に構造化イベントとしてプッシュ通知し、自動リカバリワークフローをトリガーする仕組みである。ポーリング不要で障害検知を高速化する。

### Custom kernel hook: ユーザー定義アルゴリズムの登録

現在の NCCL は内蔵アルゴリズム (Ring/Tree/NVLS 等) からしかコストモデルが選択できない。MoE 専用の通信パターンや、ハードウェア固有の最適化を持つ独自カーネルを NCCL のランタイムに統合する手段がない。

Custom kernel hook は、ユーザーがカスタム通信カーネル/アルゴリズムを NCCL に登録し、コストモデルが実行時にそれを選択候補として扱えるようにする仕組みである。Q2 2026 ロードマップ ([#2090](https://github.com/NVIDIA/nccl/issues/2090)) では「Features Under Consideration」だったものが、Aug-Oct 2026 で確定ロードマップに格上げされた。

Device API (LSA/GIN/Multimem) で書いたカスタムカーネルを NCCL の統一コストモデルに乗せられるようになることで、「NCCL を置き換える」のではなく「NCCL を拡張する」アプローチが正式にサポートされる。DeepEP や NCCL-EP のような特化ライブラリの知見を NCCL 本体に還流させる道筋になる。

## まとめ

NCCL 2.31 以降のロードマップは、3つの大きな方向性を示している。

1. **GPU 主導通信の完成** (GIN + EFA GDA): CPU ボトルネックの完全排除。特に MoE ワークロードで劇的な効果がある
2. **拡張性の確保** (Custom kernel hook + Cost-model 再設計): ハードウェア世代やワークロードの多様化に耐える設計
3. **運用性の向上** (NCCL Notify + RAS): 万単位 GPU のクラスタ運用を現実的にする可観測性

AWS EFA ユーザーにとっては、EFA GDA による GIN サポートが最大のマイルストーンだが、SRD プロトコルとの相互作用に起因する課題がまだ解決途上である点は注視が必要である。
