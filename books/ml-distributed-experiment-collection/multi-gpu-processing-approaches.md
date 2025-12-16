
https://colossalai.org/docs/concepts/distributed_training


https://www.deepspeed.ai/posts/ ブログ deepspeed

https://zenn.dev/turing_motors/articles/d00c46a79dc976


https://www.dailydoseofds.com/a-beginner-friendly-guide-to-multi-gpu-model-training/



このうち、DeepSpeedの一番の特徴といわれるのがZeRO(Zero Redundancy Optimizer)です。

https://www.deepspeed.ai/assets/files/DeepSpeed_Overview_Japanese_2023Jun7th.pdf 全体の流れに良い

https://www.ariseanalytics.com/tech-info/20231220
https://docs.pytorch.org/tutorials/beginner/dist_overview.html


以下の記事をベースに整理するのが良いでしょう。
https://colossalai.org/docs/concepts/distributed_training
https://colossalai.org/docs/concepts/paradigms_of_parallelism
https://zenn.dev/turing_motors/articles/0e6e2baf72ebbc
https://zenn.dev/turing_motors/articles/04c1328bf6095a
https://zenn.dev/turing_motors/articles/da7fa101ecb9a1

Monarch についても調べてほしい。

実測までやりたい。


整理事項

- Parallelism の整理、有用な既存資料が多数あるので概要のみで良い。mermaid 図を用いて解説。
- ZeRO stage1-3の整理、これも有用な既存資料が多数あるので概要のみで良い。mermaid 図を用いて解説。
- 各種ツールの違い、DeepSpeed、Megatron-DeepSpeed、Megatron-LM、Nanotron、Picotron、Pytorch DDP、など。
- 特に大規模基盤モデルの分散学習は backpropagation 処理で双方向グラフになるはずなのでそこが難しいポイントであることの説明。bi-directional, interdependent
- 表層の Parallelism の説明だけだとつまらないので発展として Cuda Kernel レベルでどうやってうまく処理しているのかの参考記事や arxiv があれば調べて概要を説明してほしい。