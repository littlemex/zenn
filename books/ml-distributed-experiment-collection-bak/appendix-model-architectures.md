---
title: "Appendix: Model Architectures"
emoji: "🔧"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["aws", "sagemaker", "hyperpod", "distributed", "infrastructure"]
free: false
---

::::details MoE で All-to-All 通信が必要になる理由

## 概要

Mixture-of-Experts（MoE）モデルにおいて All-to-All 通信が必要になる理由を、従来の Dense モデルとの比較を通じて理解しましょう。

## Dense モデル vs MoE モデルの基本的な違い

### Dense モデルの動作

```mermaid
flowchart LR
    Input[入力トークン] --> GPU1[GPU_1<br/>FFN]
    Input --> GPU2[GPU_2<br/>FFN]
    Input --> GPU3[GPU_3<br/>FFN]
    
    GPU1 --> Out1[出力]
    GPU2 --> Out2[出力]
    GPU3 --> Out3[出力]
    
    style Input fill:#e1f5ff
    style GPU1 fill:#fff4e1
    style GPU2 fill:#fff4e1
    style GPU3 fill:#fff4e1
```

**特徴**
- 各 GPU で同じ FFN を実行（パラメータは複製）
- All-reduce での勾配同期のみ
- 通信パターンはシンプル

### MoE モデルの動作

```mermaid
flowchart TB
    Input[入力トークン] --> Router[Router]
    
    Router -.->|猫| E1[GPU_1<br/>Expert_1]
    Router -.->|が| E2[GPU_2<br/>Expert_2]
    Router -.->|好き| E3[GPU_3<br/>Expert_3]
    
    E1 --> Combine[統合]
    E2 --> Combine
    E3 --> Combine
    Combine --> Output[出力]
    
    style Router fill:#ffe1e1
    style E1 fill:#d4ffd4
    style E2 fill:#ffd4ff
    style E3 fill:#ffffd4
```

**特徴**
- 各エキスパートが異なる分野に特化
- トークンを適切なエキスパートに動的にルーティング
- **All-to-All 通信が必要**（トークンの分散と収集）

## All-to-All Dispatch: トークンをエキスパートに送る

```mermaid
sequenceDiagram
    participant GPU1 as GPU 1<br/>[Expert A, B]
    participant GPU2 as GPU 2<br/>[Expert C, D]
    participant GPU3 as GPU 3<br/>[Expert E, F]
    
    Note over GPU1,GPU3: 各GPUが独自にトークンを持っている
    
    GPU1->>GPU1: Token1 to Expert A (local)
    GPU1->>GPU2: Token2 to Expert C
    GPU1->>GPU3: Token3 to Expert E
    
    GPU2->>GPU1: Token4 to Expert B
    GPU2->>GPU2: Token5 to Expert D (local)
    GPU2->>GPU3: Token6 to Expert F
    
    GPU3->>GPU1: Token7 to Expert A
    GPU3->>GPU2: Token8 to Expert C  
    GPU3->>GPU3: Token9 to Expert E (local)
    
    Note over GPU1,GPU3: All-to-All Dispatch 完了<br/>各GPUが担当エキスパートの<br/>トークンを受信
```

## Expert 処理 + All-to-All Combine: 結果を元に戻す

```mermaid
sequenceDiagram
    participant GPU1 as GPU 1<br/>[Expert A, B]
    participant GPU2 as GPU 2<br/>[Expert C, D]  
    participant GPU3 as GPU 3<br/>[Expert E, F]
    
    Note over GPU1,GPU3: 各エキスパートが担当トークンを処理
    
    GPU1->>GPU1: Expert A processes Token1, Token7
    GPU1->>GPU1: Expert B processes Token4
    
    GPU2->>GPU2: Expert C processes Token2, Token8
    GPU2->>GPU2: Expert D processes Token5
    
    GPU3->>GPU3: Expert E processes Token3, Token9
    GPU3->>GPU3: Expert F processes Token6
    
    Note over GPU1,GPU3: 処理結果を元のデバイスに戻す<br/>All-to-All Combine
    
    GPU1->>GPU1: Token1 result (local)
    GPU2->>GPU1: Token2 result
    GPU3->>GPU1: Token3 result
    
    GPU1->>GPU2: Token4 result
    GPU2->>GPU2: Token5 result (local)
    GPU3->>GPU2: Token6 result
    
    GPU1->>GPU3: Token7 result
    GPU2->>GPU3: Token8 result
    GPU3->>GPU3: Token9 result (local)
    
    Note over GPU1,GPU3: All-to-All Combine 完了<br/>各GPUが元のトークン結果を取得
```

## 通信パターンの詳細分析

### Forward Pass での All-to-All パターン

```mermaid
flowchart TD
    subgraph Phase1["Phase 1: Token Dispatch All-to-All"]
        subgraph Before["送信前の状態"]
            GPU1Before[GPU 1<br/>Token A to Expert 3<br/>Token B to Expert 1<br/>Token C to Expert 2]
            GPU2Before[GPU 2<br/>Token D to Expert 1<br/>Token E to Expert 3<br/>Token F to Expert 2]  
            GPU3Before[GPU 3<br/>Token G to Expert 2<br/>Token H to Expert 1<br/>Token I to Expert 3]
        end
        
        Before --> DispatchComm[All-to-All<br/>Communication]
        
        subgraph After["受信後の状態"]
            GPU1After[GPU 1 Expert 1<br/>Token B local<br/>Token D from GPU2<br/>Token H from GPU3]
            GPU2After[GPU 2 Expert 2<br/>Token C from GPU1<br/>Token F local<br/>Token G from GPU3]
            GPU3After[GPU 3 Expert 3<br/>Token A from GPU1<br/>Token E from GPU2<br/>Token I local]
        end
        
        DispatchComm --> After
    end
    
    subgraph Phase2["Phase 2: Expert Processing"]
        GPU1After --> Process1[Expert 1<br/>処理]
        GPU2After --> Process2[Expert 2<br/>処理]
        GPU3After --> Process3[Expert 3<br/>処理]
    end
    
    subgraph Phase3["Phase 3: Result Combine All-to-All"]
        Process1 --> CombineComm[All-to-All<br/>Communication]
        Process2 --> CombineComm
        Process3 --> CombineComm
        
        subgraph Final["最終状態"]
            GPU1Final[GPU 1<br/>Token A result<br/>Token B result<br/>Token C result]
            GPU2Final[GPU 2<br/>Token D result<br/>Token E result<br/>Token F result]
            GPU3Final[GPU 3<br/>Token G result<br/>Token H result<br/>Token I result]
        end
        
        CombineComm --> Final
    end
    
    classDef gpu1Style fill:#e1f5ff,stroke:#333,stroke-width:2px
    classDef gpu2Style fill:#fff4e1,stroke:#333,stroke-width:2px
    classDef gpu3Style fill:#f0fff4,stroke:#333,stroke-width:2px
    classDef commStyle fill:#ffe1e1,stroke:#333,stroke-width:2px
    classDef processStyle fill:#d4ffd4,stroke:#333,stroke-width:2px
    
    class GPU1Before,GPU1After,GPU1Final gpu1Style
    class GPU2Before,GPU2After,GPU2Final gpu2Style
    class GPU3Before,GPU3After,GPU3Final gpu3Style
    class DispatchComm,CombineComm commStyle
    class Process1,Process2,Process3 processStyle
```

## なぜ All-to-All が必要なのか

### Dense モデルでの通信パターン

**Data Parallelism のみ**
- 各 GPU が同じモデルの複製を持つ
- 勾配計算後の All-reduce のみ
- 通信頻度: イテレーション終了時に 1 回

### MoE モデルでの通信パターン

**Expert Parallelism + Data Parallelism**
- 各エキスパートが特定の GPU にのみ存在
- トークンを物理的に適切な GPU に移動
- 処理結果を元の GPU に戻す
- **通信頻度: 各 MoE 層で 2 回（Dispatch + Combine）**

## 実際の性能データ

# DeepSeek-V3 での実測値（DeepEP ライブラリ）

### 通常の訓練・推論プリフィル用カーネル

**テスト環境**: H800（~160 GB/s NVLink）+ CX7 InfiniBand 400 Gb/s（~50 GB/s RDMA）
**設定**: 4096 tokens/batch, 7168 hidden, top-4 groups, top-8 experts, FP8 dispatching + BF16 combining

| タイプ | Expert 並列度 | Dispatch 帯域幅 | Combine 帯域幅 | ボトルネック |
|------|-------------|---------------|---------------|------------|
| **Intranode** | 8 | 153 GB/s | 158 GB/s | NVLink |
| **Internode** | 16 | 43 GB/s | 43 GB/s | RDMA |
| **Internode** | 32 | 58 GB/s | 57 GB/s | RDMA |  
| **Internode** | 64 | 51 GB/s | 50 GB/s | RDMA |

### 低レイテンシ推論デコード用カーネル

**設定**: 128 tokens/batch, 7168 hidden, top-8 experts, FP8 dispatching + BF16 combining

| Expert 並列度 | Dispatch | Combine | RDMA 帯域幅 |
|-------------|----------|---------|-------------|
| 8 | 77 μs | 114 μs | 98-127 GB/s |
| 16 | 118 μs | 195 μs | 63-74 GB/s |
| 32 | 155 μs | 273 μs | 48-53 GB/s |
| 64 | 173 μs | 314 μs | 43-46 GB/s |
| 128 | 192 μs | 369 μs | 39 GB/s |
| 256 | 194 μs | 360 μs | 39-40 GB/s |

**重要な観察**
- Intranode（NVLink）は Internode（RDMA）より約 3-4 倍高速
- Expert 並列度が増加すると、レイテンシも増加
- 大規模並列化では RDMA 帯域幅がボトルネックになる

## 通信量の分析

### MoE vs Dense モデルの通信量比較

```mermaid
flowchart LR
    subgraph Dense["Dense モデル"]
        DenseComp[計算<br/>全GPUで同じFFN]
        DenseComm[通信<br/>All-reduce<br/>イテレーション終了時のみ]
        
        DenseComp --> DenseComm
    end
    
    subgraph MoE["MoE モデル"]
        MoEDispatch[通信1<br/>All-to-All Dispatch<br/>各MoE層で実行]
        MoEComp[計算<br/>各GPUで異なるExpert]
        MoECombine[通信2<br/>All-to-All Combine<br/>各MoE層で実行]
        MoEAllReduce[通信3<br/>All-reduce<br/>イテレーション終了時]
        
        MoEDispatch --> MoEComp
        MoEComp --> MoECombine
        MoECombine --> MoEAllReduce
    end
    
    classDef compStyle fill:#e1f5ff,stroke:#333,stroke-width:2px
    classDef commStyle fill:#ffe1e1,stroke:#333,stroke-width:2px
    classDef allReduceStyle fill:#ffd4ff,stroke:#333,stroke-width:2px
    
    class DenseComp,MoEComp compStyle
    class DenseComm,MoEDispatch,MoECombine commStyle
    class MoEAllReduce allReduceStyle
```

**通信頻度の違い**
- **Dense モデル**: イテレーション終了時に All-reduce × 1 回
- **MoE モデル**: 各 MoE 層で All-to-All × 2 回 + イテレーション終了時に All-reduce × 1 回

### 通信量の計算例

**設定例**: 8 層の MoE モデル、64 GPU、4096 tokens/batch

```mermaid
flowchart TD
    subgraph Calculation["通信量の計算"]
        TokenSize[Token サイズ<br/>4096 tokens × 7168 hidden<br/>× 2 bytes FP16<br/>= 58.7 MB]
        
        PerLayer[1 MoE 層あたり<br/>Dispatch: 58.7 MB<br/>Combine: 58.7 MB<br/>合計: 117.4 MB]
        
        Total[全体通信量<br/>117.4 MB × 8 layers<br/>= 939.2 MB per iteration]
        
        TokenSize --> PerLayer
        PerLayer --> Total
        
        Compare[Dense モデル比較<br/>All-reduce のみ<br/>約 140 MB per iteration<br/>MoE は約 7 倍の通信量]
        
        Total --> Compare
    end
    
    classDef tokenStyle fill:#e1f5ff,stroke:#333,stroke-width:2px
    classDef layerStyle fill:#fff4e1,stroke:#333,stroke-width:2px
    classDef totalStyle fill:#f0fff4,stroke:#333,stroke-width:2px
    classDef compareStyle fill:#ffe1e1,stroke:#333,stroke-width:2px
    
    class TokenSize tokenStyle
    class PerLayer layerStyle
    class Total totalStyle
    class Compare compareStyle
```

## All-to-All が必要になる具体的な理由

### 1. Expert Parallelism による物理的分散

```mermaid
flowchart TB
    subgraph Scenario["シナリオ: 4つのトークンと3つのGPU"]
        subgraph InitialState["初期状態：各GPUが異なるトークンを持つ"]
            GPU1Init["GPU 1<br/>Token A: 猫<br/>Token B: 走る"]
            GPU2Init["GPU 2<br/>Token C: です<br/>Token D: 美しい"]
            GPU3Init["GPU 3<br/>（処理するトークンなし）"]
        end
        
        subgraph Routing["Router による判定"]
            Router["ゲーティングネットワーク<br/>の判定結果"]
            TokenA["Token A（猫）<br/>→ Expert 1（動物）"]
            TokenB["Token B（走る）<br/>→ Expert 2（動作）"]
            TokenC["Token C（です）<br/>→ Expert 3（文法）"]
            TokenD["Token D（美しい）<br/>→ Expert 2（動作）"]
            
            Router --> TokenA
            Router --> TokenB
            Router --> TokenC
            Router --> TokenD
        end
        
        subgraph ExpertLocation["エキスパートの配置"]
            GPU1Expert["GPU 1<br/>Expert 1（動物）"]
            GPU2Expert["GPU 2<br/>Expert 2（動作）"]
            GPU3Expert["GPU 3<br/>Expert 3（文法）"]
        end
        
        subgraph Problem["問題：トークンとエキスパートが<br/>異なるGPUに存在"]
            TokenA -.->|必要| GPU1Expert
            TokenB -.->|必要| GPU2Expert
            TokenC -.->|必要| GPU3Expert
            TokenD -.->|必要| GPU2Expert
            
            ProblemText["Token A は GPU 1 にあるが<br/>Token B は GPU 1 にあるが Expert 2 は GPU 2<br/>Token C は GPU 2 にあるが Expert 3 は GPU 3<br/>Token D は GPU 2 にあるが Expert 2 も GPU 2"]
        end
        
        InitialState --> Routing
        Routing --> ExpertLocation
        ExpertLocation --> Problem
    end
    
    classDef gpu1Style fill:#e1f5ff,stroke:#333,stroke-width:2px
    classDef gpu2Style fill:#fff4e1,stroke:#333,stroke-width:2px
    classDef gpu3Style fill:#f0fff4,stroke:#333,stroke-width:2px
    classDef routerStyle fill:#ffe1e1,stroke:#333,stroke-width:2px
    classDef problemStyle fill:#ffd4d4,stroke:#333,stroke-width:2px
    
    class GPU1Init,GPU1Expert,TokenA gpu1Style
    class GPU2Init,GPU2Expert,TokenB,TokenD gpu2Style
    class GPU3Init,GPU3Expert,TokenC gpu3Style
    class Router,TokenA,TokenB,TokenC,TokenD routerStyle
    class ProblemText problemStyle
```

### 2. All-to-All Dispatch による解決

```mermaid
flowchart TB
    subgraph DispatchSolution["All-to-All Dispatch による解決"]
        subgraph SendPhase["送信フェーズ"]
            GPU1Send["GPU 1 から送信<br/>Token A → GPU 1（local）<br/>Token B → GPU 2"]
            GPU2Send["GPU 2 から送信<br/>Token C → GPU 3<br/>Token D → GPU 2（local）"]
            GPU3Send["GPU 3 から送信<br/>（送信するトークンなし）"]
        end
        
        subgraph AllToAll["All-to-All Communication"]
            Communication[すべてのGPU間で<br/>同時にトークンを交換]
        end
        
        subgraph ReceivePhase["受信フェーズ"]
            GPU1Receive["GPU 1 が受信<br/>Token A（local）<br/>Expert 1 で処理準備完了"]
            GPU2Receive["GPU 2 が受信<br/>Token B（from GPU 1）<br/>Token D（local）<br/>Expert 2 で処理準備完了"]
            GPU3Receive["GPU 3 が受信<br/>Token C（from GPU 2）<br/>Expert 3 で処理準備完了"]
        end
        
        SendPhase --> AllToAll
        AllToAll --> ReceivePhase
        
        subgraph Result["結果：各GPUが担当エキスパートの<br/>すべてのトークンを取得"]
            Solved["全てのトークンが<br/>適切なエキスパートの場所に配置"]
        end
        
        ReceivePhase --> Result
    end
    
    classDef sendStyle fill:#e1f5ff,stroke:#333,stroke-width:2px
    classDef commStyle fill:#ffe1e1,stroke:#333,stroke-width:2px
    classDef receiveStyle fill:#f0fff4,stroke:#333,stroke-width:2px
    classDef resultStyle fill:#d4ffd4,stroke:#333,stroke-width:2px
    
    class GPU1Send,GPU2Send,GPU3Send sendStyle
    class Communication commStyle
    class GPU1Receive,GPU2Receive,GPU3Receive receiveStyle
    class Solved resultStyle
```

## なぜこの設計が効率的なのか

### 計算効率の向上

MoE では以下の理由により、通信コストを上回る計算効率が得られます。

**大きな行列演算への集約**
- 各エキスパートが複数 GPU からのトークンを処理
- 小さな行列演算を大きな行列演算に集約
- GPU の並列計算能力を最大限活用

**スパース アクティベーション**
- 各トークンは全エキスパートではなく top-k エキスパートのみを使用
- 計算量は線形増加、パラメータ数は指数的増加可能

## 実装上の課題と解決策

### Load Balancing の問題

```mermaid
flowchart TB
    subgraph Problem["問題: 負荷の不均衡"]
        Unbalanced[不均衡な配置<br/>Expert 1: 過負荷<br/>Expert 2: 軽負荷<br/>Expert 3: 未使用]
        
        Impact[影響<br/>一部GPUのボトルネック<br/>全体性能の低下<br/>エキスパート特化の阻害]
        
        Unbalanced --> Impact
    end
    
    subgraph Solution["解決策"]
        LoadBalanceLoss[Load Balance Loss<br/>均等配置を促進]
        
        GroupLimited[Group-Limited Gating<br/>DeepSeek-V3 手法<br/>グループ内での競合]
        
        DroplessToken[Dropless Token<br/>MegaBlocks 手法<br/>トークン廃棄なし]
        
        LoadBalanceLoss --> Balanced[バランスされた配置<br/>全エキスパートが<br/>適切に活用される]
        GroupLimited --> Balanced
        DroplessToken --> Balanced
    end
    
    classDef problemStyle fill:#ffe1e1,stroke:#333,stroke-width:2px
    classDef impactStyle fill:#ffd4d4,stroke:#333,stroke-width:2px
    classDef solutionStyle fill:#d4ffd4,stroke:#333,stroke-width:2px
    classDef balancedStyle fill:#e1f5ff,stroke:#333,stroke-width:2px
    
    class Unbalanced problemStyle
    class Impact impactStyle
    class LoadBalanceLoss,GroupLimited,DroplessToken solutionStyle
    class Balanced balancedStyle
```

## 参考文献とリソース

### 主要論文
- [Outrageously Large Neural Networks: The Sparsely-Gated Mixture-of-Experts Layer](https://arxiv.org/abs/1701.06538) - Google による MoE の基礎論文
- [RailS: Load Balancing for All-to-All Communication in Distributed MoE Training](https://arxiv.org/abs/2510.19262) - Rail アーキテクチャでの MoE 最適化
- [Chain-of-Experts: Unlocking the Communication Power of MoE Models](https://arxiv.org/abs/2506.18945) - 新しい MoE アーキテクチャ提案

### 実装とライブラリ
- [Training MoEs at Scale with PyTorch](https://pytorch.org/blog/training-moes/) - PyTorch + MegaBlocks による大規模 MoE 訓練
- [DeepEP: DeepSeek 高性能 All-to-All ライブラリ](https://github.com/deepseek-ai/DeepEP) - FP8 対応の最適化された実装
- [All-to-All Communication in MoE Training](https://apxml.com/courses/mixture-of-experts/chapter-4-scaling-moe-distributed-training/all-to-all-communication-moe) - 詳細な技術解説

### 商用実装
- [What Is Mixture of Experts (MoE) and How It Works?](https://www.nvidia.com/en-us/glossary/mixture-of-experts/) - NVIDIA による MoE 概要と GB200 NVL72 での最適化

## 結論

MoE モデルにおける All-to-All 通信は、Expert Parallelism を実現するために不可欠な仕組みです。Dense モデルと比較して通信量は増加しますが、エキスパートの専門化と大きな行列演算への集約により、全体的な計算効率の向上を実現しています。

特に大規模モデルでは、適切な Load Balancing と高速なネットワーク（NVLink、EFA）により、All-to-All 通信のオーバーヘッドを最小化しながら、モデルの表現能力を大幅に拡張することが可能になります。
::::
