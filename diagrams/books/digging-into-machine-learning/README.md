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
| `afd-fig5-flowprefill.drawio` | `afd-fig5-flowprefill.png` | `serving-disaggregation` (軸 3 FlowPrefill) | FCFS / chunked prefill / FlowPrefill の 3 段比較と粒度・頻度の分離 |
| `afd-fig6-inversion.drawio` | `afd-fig6-inversion.png` | `serving-disaggregation` (交差の実装) | 入れ子の反転 (P/D が外 vs AFD が外・共有 expert プール) |
