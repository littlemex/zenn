---
title: "Basic00 - はじめに (なぜ Amazon EKS で分散 AI 基盤を作るのか)"
free: true
---

# この book について

この book は、NVIDIA GPU や AWS Trainium/AWS Inferentia（Neuron）を使った分散学習・推論の実験を、Amazon EKS 上で回すための基盤を Terraform で構築するワークショップです。Amazon VPC・Amazon EKS・Karpenter といった土台から始めて、`accelerator_pools` によるアクセラレータノードの動的プロビジョニング、EFA によるマルチノード通信、Capacity Block の取得、共有ストレージ、安全な破棄までを順に扱います。

対象モジュールは以下のリポジトリです。

https://github.com/littlemex/distributed-ai/tree/fix/eks-efa-verification-improvements/infra/eks

# なぜ Amazon EKS なのか — 推論と学習の両方を回す基盤として

分散学習・推論の基盤というと、まず思い浮かぶのは Slurm ベースの HPC クラスタ（AWS ParallelCluster や SageMaker HyperPod の Slurm モード）でしょう。実際、事前学習のような「大きな学習ジョブを 1 本、長時間流す」用途では Slurm は非常に強力です。ジョブスケジューラとして成熟しており、`sbatch` でジョブを投げれば計算資源を確保して実行してくれます。

ところが、扱うワークロードが「学習だけ」ではなくなると、Slurm ベースの構成は途端に窮屈になります。その典型が強化学習（RL）です。

## 強化学習は「推論」と「学習」を同時に回す

近年の LLM 向け強化学習、たとえば GRPO（Group Relative Policy Optimization）や PPO 系のアルゴリズムは、大きく 2 つのフェーズをループで回します。

- **rollout（推論）**: 現在のポリシーモデルで大量のサンプルを生成する。ここでは SGLang や vLLM のような推論エンジンが使われる
- **学習**: 生成したサンプルと報酬を使ってモデルを更新する。ここでは Megatron-LM のような学習フレームワークが使われる

つまり RL は、性質のまったく異なる 2 種類のワークロード（低レイテンシの推論サービングと、高スループットの分散学習）を、同じクラスタ上で交互に、あるいは同時に動かす必要があります。さらに実運用では、推論エンジンと学習エンジンの間で重み（weight）を同期し続けます。

こうした「推論エンジンと学習エンジンが混在し、動的に起動・停止する」ワークロードは、静的にノードを割り当ててバッチジョブを流す Slurm の世界観とは相性がよくありません。推論サーバーを常駐させたい、報酬計算を別ノードに逃がしたい、モデルサイズに応じて GPU 数を変えたい、といった要求に、ジョブスケジューラ中心の構成で応えるのは手間がかかります。

## Kubernetes（Amazon EKS）なら推論も学習も同じ土俵に乗る

一方、Kubernetes はもともと「多様なサービスを動的にスケジュールする」ためのプラットフォームです。推論サーバーは Deployment や KubeRay の Service として、学習ジョブは Job や MPIJob として、報酬モデルは別の Pod として、すべて同じクラスタ上で宣言的に扱えます。GPU/Neuron ノードは Karpenter が Pod の要求に応じて動的に起動し、使い終われば自動で回収します。

この book で作る Amazon EKS 基盤の価値は、まさにここにあります。「学習専用」でも「推論専用」でもなく、**推論と学習が混在する RL のようなワークロードを含めて、GPU/Neuron を柔軟に使い回せる土台を一度作っておく**ことです。一度この基盤を立てておけば、あとは Pod を投げるだけで、事前学習・ファインチューニング・推論サービング・強化学習のいずれも同じクラスタで実験できます。

:::message
Slurm が劣っているという話ではありません。単一の大規模学習ジョブを流すだけなら Slurm ベースの構成のほうがシンプルなこともあります。この book は「推論と学習が入り混じる実験を回したい」というユースケースに対して、Amazon EKS を土台に選ぶ理由と、その具体的な作り方を示すものです。
:::

# この book で学べること

- Terraform による Amazon EKS クラスタの構築（Basic01）
- GPU を使わない CPU での torchrun DDP 分散学習の体験（Basic02）
- Karpenter によるノードの動的プロビジョニング（Basic03）
- `accelerator_pools` という 1 つの変数だけで GPU/Neuron ノードを追加する仕組み（Basic04）
- EFA（Elastic Fabric Adapter）によるマルチノード NCCL 通信の検証（Basic05）
- Capacity Block（予約 GPU/AWS Trainium）の取得と組み込み（Basic06）
- 軽量 vLLM（OpenAI 互換サーバー）による GPU 推論の動作確認（Basic07）
- Prometheus + Grafana による GPU メトリクスの可視化（Basic08）
- AWS Trainium/AWS Inferentia（Neuron）対応の設計（Basic09）
- 共有ストレージ Amazon EFS / Amazon FSx for Lustre の使い分け（Basic10）
- 課金を取り残さない安全な破棄と、オプションの公開エンドポイント（Basic11）

# 必要なもの

- AWS アカウント
- AdministratorAccess 相当の IAM 権限（Amazon EKS・Amazon EC2・Amazon VPC・IAM・Amazon FSx・Amazon EFS の作成権限）
- ローカルまたは CloudShell に Terraform 1.5+ / AWS CLI v2 / kubectl / helm
- GPU/Neuron インスタンス（特に p5en などの Capacity Block）を使う場合はサービスクォータと予算の確認

:::message alert
この book が構築する Amazon EKS クラスタ・GPU/Neuron ノード・NAT ゲートウェイ・Amazon FSx などは、起動している間 AWS 利用料金が発生します。特に GPU/Neuron インスタンスと Capacity Block は高額です。実験が終わったら Basic11 の手順で必ず破棄してください。
:::

# アーキテクチャ概要

この book 全体で構築する分散 AI 基盤の全体像です。Amazon VPC の中に 2 つの AZ を張り、Amazon EKS コントロールプレーンの下で Karpenter が GPU/Neuron の各 NodePool を要求に応じて起動します。共有ストレージ（Amazon EFS / Amazon FSx）や Capacity Block の期限監視（Amazon EventBridge → Amazon SNS）といった周辺サービスも含みます。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

各コンポーネントは Basic01 以降の各章で 1 つずつ、実際の Terraform コードを引用しながら解説します。全体像を頭に入れたうえで、まずは Basic01 で土台となる Amazon EKS クラスタを立てるところから始めましょう。

# 参考資料

- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/fix/eks-efa-verification-improvements/infra/eks)
- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter 公式ドキュメント](https://karpenter.sh/)
- [awslabs/awsome-distributed-training](https://github.com/awslabs/awsome-distributed-training)
