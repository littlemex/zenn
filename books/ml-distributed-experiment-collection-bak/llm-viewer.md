# LLM Viewer 調査報告書

調査日時: 2025年12月19日  
調査URL: http://llm-viewer.com/  
ツール: Chrome DevTools (Browser Action)

---

## 概要

**LLM Viewer v0.4.0** は、Large Language Model（大規模言語モデル）の推論性能を分析・可視化するためのWebベースのツールです。

```mermaid
graph LR
    A[ユーザー] --> B[LLM Viewer]
    B --> C[API Server]
    C --> D[モデル計算グラフ]
    B --> E[Roofline Model]
    B --> F[計算グラフ可視化]
```

---

## UIレイアウト構成

```mermaid
graph TB
    subgraph "LLM Viewer UI構成"
        Header[ヘッダー<br/>Model/Hardware/Server選択]
        Left[左パネル<br/>設定エリア]
        Center[中央パネル<br/>Roofline Model]
        Right[右パネル<br/>計算グラフ]
    end
    
    Header --> Left
    Header --> Center
    Header --> Right
    
    Left --> L1[Inference Config]
    Left --> L2[Optimization Config]
    Left --> L3[Network-wise Analysis]
    
    L1 --> L1A[Stage選択]
    L1 --> L1B[Batchsize]
    L1 --> L1C[SeqLength]
    L1 --> L1D[Tensor Parallelism]
    
    L2 --> L2A[Weight Quantization]
    L2 --> L2B[Activation Quantization]
    L2 --> L2C[KV Cache Quantization]
    L2 --> L2D[FlashAttention]
    
    Center --> C1[性能グラフ<br/>OPs vs Arithmetic Intensity]
    
    Right --> R1[モデル階層構造]
    Right --> R2[各レイヤー詳細]
```

---

## 主要機能

### 1. モデル・ハードウェア選択

```mermaid
flowchart LR
    A[Model Selection] --> A1[meta-llama/Llama-2-7b-hf]
    A[Model Selection] --> A2[その他のモデル...]
    
    B[Hardware Selection] --> B1[nvidia_V100]
    B[Hardware Selection] --> B2[その他のGPU...]
    
    C[Server Selection] --> C1[api.llm-viewer.com]
```

**機能説明**:
- **Model**: 分析対象のLLMモデルを選択
- **Hardware**: 実行環境のハードウェアを選択
- **Server**: APIエンドポイントを選択（デフォルトはapi.llm-viewer.com）

### 2. Inference Config（推論設定）

| 設定項目 | 説明 | デフォルト値 |
|---------|------|-------------|
| Stage | 推論ステージ（Decode/Prefill/Chat） | Decode |
| Batchsize | バッチサイズ | 1 |
| SeqLength | シーケンス長 | 1024 |
| Tensor parallelism | テンソル並列度 | 1 |

### 3. Optimization Config（最適化設定）

```mermaid
graph TD
    A[Optimization Config] --> B[Weight Quantization]
    A --> C[Activation Quantization]
    A --> D[KV Cache Quantization]
    A --> E[FlashAttention]
    
    B --> B1[FP16/INT8/INT4など]
    C --> C1[FP16など]
    D --> D1[FP16など]
    E --> E1[有効/無効]
```

### 4. Roofline Model（性能分析）

**グラフの見方**:
- **X軸**: Arithmetic Intensity (OPs/byte) - 演算強度
- **Y軸**: Performance (OPs) - 性能

このグラフにより、選択したハードウェア上でのモデルの理論的な性能上限を可視化します。

### 5. 計算グラフビューア

モデルの内部構造を階層的に表示：

```mermaid
graph TD
    Input[input] --> Attention[attention layers]
    Attention --> MLP[MLP layers]
    MLP --> Norm[normalization]
    Norm --> Output[output]
    
    Attention --> A1[qk_proj]
    Attention --> A2[v_proj]
    Attention --> A3[o_proj]
    
    MLP --> M1[gate_proj]
    MLP --> M2[up_proj]
    MLP --> M3[down_proj]
```

---

## 使用フロー

```mermaid
sequenceDiagram
    participant U as ユーザー
    participant UI as LLM Viewer UI
    participant API as API Server
    participant DB as モデルDB
    
    U->>UI: 1. モデル選択
    U->>UI: 2. ハードウェア選択
    UI->>API: 3. get_graph リクエスト
    API->>DB: 4. モデル情報取得
    DB-->>API: 5. 計算グラフデータ
    API-->>UI: 6. グラフデータ返却
    UI-->>U: 7. Roofline Model表示
    UI-->>U: 8. 計算グラフ表示
    
    U->>UI: 9. 推論設定変更
    UI-->>U: 10. グラフ更新
    
    U->>UI: 11. 最適化設定変更
    UI-->>U: 12. 性能予測更新
```

---

## システムアーキテクチャ

```mermaid
graph TB
    subgraph "Frontend (React SPA)"
        UI[UI Components]
        State[State Management]
        Graph[Graph Renderer]
    end
    
    subgraph "Backend API"
        API[API Endpoint<br/>api.llm-viewer.com]
        Cache[Model Cache]
        Compute[Performance Calculator]
    end
    
    subgraph "Data Layer"
        Models[Model Definitions]
        HW[Hardware Specs]
        Profiles[Performance Profiles]
    end
    
    UI --> State
    State --> Graph
    UI --> API
    API --> Cache
    API --> Compute
    Compute --> Models
    Compute --> HW
    Compute --> Profiles
```

---

## 技術スタック

### フロントエンド
- **フレームワーク**: React
- **タイプ**: SPA (Single Page Application)
- **可視化**: カスタムグラフレンダリング

### バックエンド
- **APIエンドポイント**: `http://api.llm-viewer.com/get_graph`
- **通信**: REST API

### コンソールログから判明した情報
```
Header mounted
LeftControl onMounted meta-llama/Llama-2-7b-hf
graphUpdate http://api.llm-viewer.com/get_graph
```

---

## 主なユースケース

```mermaid
mindmap
  root((LLM Viewer<br/>ユースケース))
    性能予測
      本番環境での性能見積もり
      ボトルネック特定
      スループット計算
    最適化検討
      量子化効果の比較
      FlashAttention評価
      メモリ使用量分析
    ハードウェア選定
      コスト対性能比較
      GPU選定支援
      スケーリング計画
    教育・研究
      LLM内部構造理解
      性能特性学習
      論文執筆サポート
```

---

## 具体的な使い方

### ステップバイステップガイド

```mermaid
flowchart TD
    Start([開始]) --> Step1[1. モデル選択]
    Step1 --> Step2[2. ハードウェア選択]
    Step2 --> Step3[3. 推論設定調整<br/>Batchsize/SeqLength]
    Step3 --> Step4[4. 最適化オプション選択<br/>Quantization/FlashAttention]
    Step4 --> Step5{性能は満足?}
    Step5 -->|No| Step6[設定を調整]
    Step6 --> Step3
    Step5 -->|Yes| Step7[5. Roofline Model確認]
    Step7 --> Step8[6. 計算グラフで詳細確認]
    Step8 --> End([完了])
```

### 例：Llama-2-7bの性能分析

1. **モデル選択**: `meta-llama/Llama-2-7b-hf`
2. **ハードウェア**: `nvidia_V100`
3. **推論設定**:
   - Stage: `Decode`
   - Batchsize: `1`
   - SeqLength: `1024`
4. **最適化**:
   - Weight Quantization: `FP16`
   - FlashAttention: `有効`
5. **結果確認**:
   - Roofline Modelで性能上限を確認
   - 計算グラフでボトルネックレイヤーを特定

---

## データフロー

```mermaid
flowchart LR
    subgraph Input
        M[Model]
        H[Hardware]
        C[Config]
    end
    
    subgraph Processing
        API[API Server]
        Calc[Performance<br/>Calculator]
    end
    
    subgraph Output
        RM[Roofline<br/>Model]
        CG[Computation<br/>Graph]
        Met[Metrics]
    end
    
    M --> API
    H --> API
    C --> API
    API --> Calc
    Calc --> RM
    Calc --> CG
    Calc --> Met
```

---

## 性能指標の解釈

### Roofline Modelの読み方

```mermaid
graph LR
    A[低Arithmetic Intensity] -->|Memory Bound| B[メモリ律速]
    C[高Arithmetic Intensity] -->|Compute Bound| D[演算律速]
    
    B --> B1[データ転送が<br/>ボトルネック]
    D --> D1[演算能力が<br/>ボトルネック]
```

**最適化のヒント**:
- **Memory Bound**: 量子化やキャッシュ最適化が有効
- **Compute Bound**: より高性能なGPUへの移行を検討

---

## トラブルシューティング

### よくある問題

| 問題 | 原因 | 解決策 |
|------|------|--------|
| グラフが表示されない | APIへの接続失敗 | ネットワーク確認・サーバー選択変更 |
| 性能が異常に低い | 不適切な設定 | Batchsize/SeqLengthの調整 |
| 計算グラフが複雑すぎる | 大規模モデル | 検索機能で特定レイヤーに絞る |

---

## まとめ

LLM Viewerは、以下のような用途で非常に有用なツールです：

✅ **LLM推論性能の事前評価**  
✅ **最適化戦略の検討と比較**  
✅ **ハードウェアの選定支援**  
✅ **モデル構造の理解と教育**

特に本番環境でのデプロイ前に、様々な設定での性能を比較検討できる点が強力です。

---

## 参考情報

- **公式サイト**: http://llm-viewer.com/
- **APIエンドポイント**: http://api.llm-viewer.com/get_graph
- **バージョン**: v0.4.0
- **GitHub**: リンク情報は画面に表示あり
- **Project**: リンク情報は画面に表示あり
- **Paper**: リンク情報は画面に表示あり

---

## 調査方法

本調査は以下の手順で実施しました：

1. Chrome DevToolsを使用してサイトにアクセス
2. UIのスクリーンショット取得
3. コンソールログの確認
4. ネットワーク通信の分析
5. インタラクティブ要素の調査

---

*調査完了*

