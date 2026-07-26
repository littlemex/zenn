# Digging into Machine Learning — 図版ソース

book slug: `digging-into-machine-learning`

各 `.drawio` は書き出した PNG (`images/books/digging-into-machine-learning/*.png`) の元データ。

## 図版一覧

| .drawio | 対応 PNG | 使用チャプター | 図の内容 |
|---|---|---|---|
| `fig1-overview.drawio` | `fig1-overview.png` | `turboquant-polarquant` (はじめに) | TurboQuant / PolarQuant の全体像 |
| `fig2-polar.drawio` | `fig2-polar.png` | `turboquant-polarquant` (PolarQuant) | 直交座標 vs 極座標での量子化 |
| `fig3-twostage.drawio` | `fig3-twostage.png` | `turboquant-polarquant` (2 段構成) | TurboQuant の 2 段構成と Shannon 下界 |
| `fig4-qjl.drawio` | `fig4-qjl.png` | `turboquant-polarquant` (QJL) | QJL の符号 1 ビット復元と角度集中 |
| `afd-fig1-axes.drawio` | `afd-fig1-axes.png` | `serving-disaggregation` (地図) | 分離の 3 つの直交軸と 1 つの横断軸 |
| `afd-fig2-nesting.drawio` | `afd-fig2-nesting.png` | `serving-disaggregation` (地図) | 時間軸 (P/D) と空間軸 (AFD) の二重分離の入れ子 |
| `afd-fig3-comm.drawio` | `afd-fig3-comm.png` | `serving-disaggregation` (軸 2 AFD) | AFD の N2M/M2N は EP の all-to-all の非対称一般化 |
| `afd-fig4-impl.drawio` | `afd-fig4-impl.png` | `serving-disaggregation` (交差の実装) | vLLM と SGLang で分離をどこまで重ねられるか |
| `llm2026-fig1-matrix.drawio` | `llm2026-fig1-matrix.png` | `llm2026-overview` (収束マトリクス) | 主要モデルの 4 要素採用マトリクス |
| `llm2026-fig2-scatter.drawio` | `llm2026-fig2-scatter.png` | `llm2026-overview` (アクティブ比) | 世代別アクティブ比の散布図 |
| `llm2026-fig3-backbone.drawio` | `llm2026-fig3-backbone.png` | `llm2026-overview` (背骨の式) | 1 トークンの時間を決める背骨の式の分解 |
| `llm2026-fig4-roofline.drawio` | `llm2026-fig4-roofline.png` | `llm2026-overview` (ルーフライン) | prefill/decode の演算強度とルーフライン |
| `llm2026-fig5-waterfall.drawio` | `llm2026-fig5-waterfall.png` | `llm2026-overview` (削減ウォーターフォール) | 4 因子の乗法的削減ウォーターフォール |
| `llm2026-fig6-guarantee.drawio` | `llm2026-fig6-guarantee.png` | `llm2026-overview` (品質保証の非対称性) | 定理型 1 つ vs 経験則型 3 つの非対称性 |
| `llm2026-e1-timeline.drawio` | `llm2026-e1-timeline.png` | `llm2026-elements` (要素1 投機デコード) | ドラフト先読みと本体の並列検証タイムライン |
| `llm2026-e2-accept.drawio` | `llm2026-e2-accept.png` | `llm2026-elements` (要素1 分布保存) | min(p,q) の受理面積と残差再サンプル |
| `llm2026-e3-kvbytes.drawio` | `llm2026-e3-kvbytes.png` | `llm2026-elements` (要素2 KV バイト) | KV バイトの式と各因子を叩く手法の対応 |
| `llm2026-e4-memory.drawio` | `llm2026-e4-memory.png` | `llm2026-elements` (要素2 容量限界) | 追記型 KV-Cache vs 固定状態ホワイトボード |
| `llm2026-e5-union.drawio` | `llm2026-e5-union.png` | `llm2026-elements` (要素3 大バッチ希釈) | エキスパート和集合 u(B) の立ち上がり |
| `llm2026-e6-waterfill.drawio` | `llm2026-e6-waterfill.png` | `llm2026-elements` (要素4 逆注水) | 層別ビット配分の逆注水と確率的丸め |
