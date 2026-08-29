---
title: "Basic00 - はじめに"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

# 本書について

本書は、ML 分散学習・推論の実験を、Amazon EKS 上で回すための基盤を Terraform で構築するワークショップです。Amazon VPC・Amazon EKS・Karpenter といった基盤から始めて、アクセラレータノードの動的プロビジョニング、EFA によるマルチノード通信、Capacity Block の取得、共有ストレージなどを扱います。

この基盤は、AWS が公開する分散学習リファレンス集 [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai) を参考にしながら組み立てています。まず NVIDIA GPU で動く構成を基本に据えつつ、AWS Trainium への対応も目指しています。

# 背景

分散学習・推論の基盤というと、代表的な選択肢は Slurm ベースの HPC クラスタでしょう。実際、事前学習のような用途では Slurm は非常に強力です。ジョブスケジューラとして成熟しており、`sbatch` でジョブを投げれば計算資源を確保して実行されます。

近年の LLM 向け強化学習、たとえば GRPO などのアルゴリズムは、大きく 2 つのフェーズを繰り返し実行します。1 つは rollout と呼ばれる推論のフェーズで、現在のポリシーモデルを使い大量のサンプルを生成します。ここでは [SGLang](https://github.com/sgl-project/sglang) や [vLLM](https://github.com/vllm-project/vllm) のような推論エンジンが使われます。もう 1 つは学習のフェーズで、生成したサンプルと報酬を使ってモデルを更新します。ここでは [Megatron-LM](https://github.com/NVIDIA/Megatron-LM) のような学習フレームワークが使われます。

つまり RL は、性質のまったく異なる 2 種類のワークロード、すなわち低レイテンシの推論サービングと、高スループットの分散学習を、同じクラスタ上で交互に、あるいは同時に動かす必要があります。さらに実運用では、推論エンジンと学習エンジンの間で重みを同期し続けます。こうした「推論エンジンと学習エンジンが混在し、動的に起動・停止する」ワークロードは、静的にノードを割り当ててバッチジョブを流す Slurm のが想定する使い方とは違ってきます。

# 要求を整理する

ワークロードが多様化し、動的に変わるという要求が増えてきている中で、この種の基盤が何を満たせなければならないのかを先に整理しておきます。次の表が、本書で作る基盤の設計目標です。

| 満たすべき要求 | なぜ必要か |
|---|---|
| 学習と推論の混在 | RL では推論 (rollout) と学習が同じループに同居する |
| CPU/GPU/Neuron の動的確保 | 高価な資源は必要なときだけ起動し、使い終わったらすぐ解放したい |
| マルチノード集団通信 | 1 台に載らないモデルを複数ノードで利用する |
| 購入オプション | Spot/On Demand/Capacity Block などへの対応 |
| 共有ストレージ | モデルやデータを複数 Pod で共有する |

Kubernetes はもともと「多様なサービスを動的にスケジュールする」ためのプラットフォームです。推論サーバーは Deployment や [KubeRay](https://github.com/ray-project/kuberay) の Service として、学習ジョブは Job や MPIJob として、すべて同じクラスタ上で宣言的に扱えます。アクセラレータノードは Karpenter が Pod の要求に応じて動的に起動し、使い終われば自動で回収します。そのため、上の要求を一度に満たす基盤として Amazon EKS を選びました。一度この基盤を立てておけば、あとは Pod を投入するだけで、事前学習・ファインチューニング・推論サービング・強化学習のいずれも同じクラスタで実験できます。

:::message
Slurm が劣っているという話ではありません。Slurm ベースの構成のほうがシンプルなこともあります。本書は「推論と学習が同じ環境で動く実験を回したい」というユースケースに対して、Amazon EKS を基盤に選ぶ理由と、その具体的な作り方を示すものです。
:::

以下は、AWS 公式が提供する Amazon SageMaker Hyperpod のガイドです。非常に参考になるので本書 と合わせて、もしくは先にこちらを実施しても良いと思います。

[AI on SageMaker HyperPod](https://awslabs.github.io/ai-on-sagemaker-hyperpod/)

# インフラ層とアプリ層の境界

基盤を作るうえで最初に決めたのが、どこまでを基盤が用意し、どこからを利用者が実装するか、という責務の境界です。この境界が曖昧だと、基盤が抱え込みすぎて変更しにくくなったり、逆に利用者が毎回インフラの毎回インフラ側の設定をする必要が出たりします。

![インフラ層とアプリ層の責務境界](/images/books/eks-distributed-ai/infra-app-boundary.png)

境界は Kubernetes の宣言的 API に置きました。利用者は「どんな Pod をいくつ、どの GPU で動かすか」を Job や RayCluster として宣言するだけでよく、その要求を満たすためのノード起動、EFA の構成、デバイスプラグインの導入、ストレージのマウントといった機構はすべて基盤側が提供します。[slime](https://github.com/THUDM/slime) (LLM 向け RL フレームワーク) による GRPO、[torchrun](https://pytorch.org/docs/stable/elastic/run.html) による DDP、vLLM による推論サービングといった「何を計算するか」は利用者の実装で、基盤はそれらが載る実行環境の提供に徹します。

この境界の考え方はスケーリングにも表れます。ノードのスケーリング、つまり Pod の要求に応じてノードを増減させることは Karpenter が担う基盤の責務です。一方で Pod 自体のスケーリング、たとえば [HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) や [KEDA](https://keda.sh/) によるレプリカ数の調整は、ワークロードの性質に依存するアプリ層で扱うことなので、この基盤の範疇には含めていません。

設計の目標と境界が決まっても、実際に手を動かすと、分散推論・学習の多様な要求に起因する問題に次々とぶつかります。現時点の構成は完璧なものではなく基本のテストケースが動いた状態のものです。様々なインスタンス、ネットワーク通信、アプリケーション、によって改善が必要になってくるはずですが、あくまでインフラストラクチャのリファレンスとして活用してください。

# 本書で学べること

今後変更される可能性があります。

- Terraform による Amazon EKS クラスタの構築 (Basic01)
- GPU を使わない CPU での torchrun DDP 分散学習の体験 (Basic02)
- Karpenter によるノードの動的プロビジョニング (Basic03)
- GPU ノードを追加して torchrun DDP 分散学習を体験 (Basic04)
- Capacity Block (予約 GPU/AWS Trainium) の取得と組み込み (Basic05)
- EFA (Elastic Fabric Adapter) によるマルチノード NCCL 通信の検証 (Basic06)
- 軽量 vLLM による GPU 推論の動作確認 (Basic07)
- Prometheus と Grafana による GPU メトリクスの可視化 (Basic08)
- AWS Trainium (AWS Neuron) 対応 (Basic09)
- Amazon FSx for Lustre による共有ストレージ設計 (Basic10)
- クラスタの後片付け (Basic11)

# 必要なもの

- AWS アカウント
- AdministratorAccess 相当の IAM 権限
- ローカルまたは CloudShell に Terraform 1.9+ / AWS CLI v2 / kubectl / helm / git / curl / python3 (後半 3 つも導入スクリプトが必須にしています)。Terraform のバージョンは [`versions.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/versions.tf) の `required_version` で決まっています。導入スクリプトはコマンドの有無だけを見てバージョンは見ないので、古い Terraform のままだと `terraform init` で初めて失敗します
- GPU/Neuron インスタンスを使う場合はサービスクォータと予算の確認

:::message alert
本書が構築する Amazon EKS クラスタ・アクセラレータノード・NAT ゲートウェイ・Amazon FSx などは、起動している間 AWS 利用料金が発生します。特にアクセラレータノードのインスタンスと Capacity Block は高額です。実験が終わったら必ず破棄してください。
:::

# アーキテクチャ概要

本書全体で構築する分散 AI 基盤の全体像です。Amazon VPC は既定でリージョンの全標準 AZ にまたがって作り (利用の申し込みが必要な AZ や Local Zone は含みません。使う AZ を絞ることもできます)、Amazon EKS コントロールプレーンの下で Karpenter がアクセラレータノードの各 NodePool を要求に応じて起動します。共有ストレージや Capacity Block といった分散 AI インフラのための周辺サービスも含みます。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

# 参考資料

- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter 公式ドキュメント](https://karpenter.sh/)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)
