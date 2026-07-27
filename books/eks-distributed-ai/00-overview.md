---
title: "Basic00 - はじめに (なぜ Amazon EKS で分散 AI 基盤を作るのか)"
free: true
---

# この book について

この book は、NVIDIA GPU や AWS Trainium/AWS Inferentia (Neuron) を使った分散学習・推論の実験を、Amazon EKS 上で回すための基盤を Terraform で構築するワークショップです。Amazon VPC・Amazon EKS・Karpenter といった土台から始めて、`accelerator_pools` によるアクセラレータノードの動的プロビジョニング、EFA によるマルチノード通信、Capacity Block の取得、共有ストレージ、安全な破棄までを順に扱います。

対象モジュールは [littlemex/distributed-ai の infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) です。

この基盤は、AWS が公開する分散学習リファレンス集 [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai) を参考にしながら組み立てています。まず NVIDIA GPU で動く構成を土台に据えつつ、AWS Trainium/AWS Inferentia (Neuron) でも同じワークフローを回せることを目指して、章立てとコードの改善を繰り返している段階にあります。そのため本 book は、既存のアプローチと優劣を比較して選定するためのものではなく、この基盤そのものをどう作り、どう育てているかを示すことに主眼を置いています。

# どういう課題から始まったのか

分散学習・推論の基盤というと、まず思い浮かぶのは Slurm ベースの HPC クラスタ (AWS ParallelCluster や SageMaker HyperPod の Slurm モード) でしょう。実際、事前学習のような「大きな学習ジョブを 1 本、長時間流す」用途では Slurm は非常に強力です。ジョブスケジューラとして成熟しており、`sbatch` でジョブを投げれば計算資源を確保して実行してくれます。

ところが、扱うワークロードが「学習だけ」ではなくなると、Slurm ベースの構成は途端に窮屈になります。その典型が強化学習 (RL) です。

近年の LLM 向け強化学習、たとえば GRPO (Group Relative Policy Optimization) や PPO 系のアルゴリズムは、大きく 2 つのフェーズをループで回します。ひとつは rollout と呼ばれる推論のフェーズで、現在のポリシーモデルを使い大量のサンプルを生成します。ここでは SGLang や vLLM のような推論エンジンが使われます。もうひとつは学習のフェーズで、生成したサンプルと報酬を使ってモデルを更新します。ここでは Megatron-LM のような学習フレームワークが使われます。

つまり RL は、性質のまったく異なる 2 種類のワークロード、すなわち低レイテンシの推論サービングと、高スループットの分散学習を、同じクラスタ上で交互に、あるいは同時に動かす必要があります。さらに実運用では、推論エンジンと学習エンジンの間で重みを同期し続けます。こうした「推論エンジンと学習エンジンが混在し、動的に起動・停止する」ワークロードは、静的にノードを割り当ててバッチジョブを流す Slurm の世界観とは相性がよくありません。

# では何を満たすべきか、要求を整理する

窮屈さの正体は、ワークロードの多様さと動的さにあります。そこで、この種の基盤が何を満たせなければならないのかを先に言語化しておきます。次の表が、この book で作る基盤の設計目標です。

| 満たすべき要求 | なぜ必要か | この基盤での満たし方 (担当章) |
|---|---|---|
| 学習と推論の混在 | RL では推論 (rollout) と学習が同じループに同居する | 同一クラスタに GPU プールと Neuron プールを共存させる (Basic04) |
| CPU/GPU/Neuron の動的確保 | 高価な資源は使うときだけ立てて即返したい | Karpenter が Pod の要求に応じノードを起動・回収する (Basic03) |
| マルチノード集団通信 | 1 台に載らないモデルを複数ノードで学習する | EFA と NCCL/Neuron を単一 AZ 内で束ねる (Basic05・09) |
| 予約枠 (Capacity Block) の活用 | 大規模 GPU は予約なしでは確保が事実上困難 | 予約の購入から期限監視までを組み込む (Basic06) |
| 共有ストレージ | モデルやデータを複数 Pod で共有する | Amazon EFS と Amazon FSx for Lustre を使い分ける (Basic10) |
| 課金を残さない破棄 | 実験が終わったら安全に消したい | ドレイン待機と VPC エンドポイントで孤立ノードを防ぐ (Basic11) |

Kubernetes はもともと「多様なサービスを動的にスケジュールする」ためのプラットフォームです。推論サーバーは Deployment や KubeRay の Service として、学習ジョブは Job や MPIJob として、すべて同じクラスタ上で宣言的に扱えます。GPU/Neuron ノードは Karpenter が Pod の要求に応じて動的に起動し、使い終われば自動で回収します。だから、上の要求を一度に満たす土台として Amazon EKS を選びました。一度この基盤を立てておけば、あとは Pod を投げるだけで、事前学習・ファインチューニング・推論サービング・強化学習のいずれも同じクラスタで実験できます。

:::message
Slurm が劣っているという話ではありません。単一の大規模学習ジョブを流すだけなら Slurm ベースの構成のほうがシンプルなこともあります。この book は「推論と学習が入り混じる実験を回したい」というユースケースに対して、Amazon EKS を土台に選ぶ理由と、その具体的な作り方を示すものです。
:::

# インフラ層とアプリ層の境界

基盤を作るうえで最初に決めたのが、どこまでを基盤が用意し、どこからを利用者が実装するか、という責務の境界です。この線引きが曖昧だと、基盤が抱え込みすぎて硬直したり、逆に利用者が毎回インフラの面倒を見る羽目になったりします。

![インフラ層とアプリ層の責務境界](/images/books/eks-distributed-ai/infra-app-boundary.png)

境界は Kubernetes の宣言的 API に置きました。利用者は「どんな Pod をいくつ、どの GPU で動かすか」を Job や RayCluster として宣言するだけでよく、その要求を満たすためのノード起動、EFA の構成、デバイスプラグインの導入、ストレージのマウントといった機構はすべて基盤側が提供します。slime (LLM 向け RL フレームワーク) による GRPO、torchrun による DDP、vLLM による推論サービングといった「何を計算するか」は利用者の実装で、基盤はそれらが載る土台に徹します。

この境界の考え方はスケーリングにも表れます。ノードのスケーリング、つまり Pod の要求に応じてノードを増減させることは Karpenter が担う基盤の責務です。一方で Pod 自体のスケーリング、たとえば HPA や KEDA によるレプリカ数の調整は、ワークロードの性質に依存するアプリ層の関心事なので、この基盤の範疇には含めていません。

:::message
Slurm や AWS ParallelCluster に慣れた方は、「ログインノードはどこにあるのか」と疑問に思うかもしれません。Slurm ではユーザーが head/login ノードに SSH でログインし、そこから `sbatch` でジョブを投入するため、外から到達できる常設のホストが必要でした。Amazon EKS ではクラスタの入口が Kubernetes API サーバーそのものであり、手元の kubectl から Job や Pod を宣言として投げ込むため、SSH 用の常設ホストは存在する必要がありません。共有ストレージ上のデータも、Amazon S3 との自動同期や一時的な Pod 経由で操作できるので、ログインノードを介して触る場面はないのです。この違いが、Basic01 で述べるパブリックサブネットを小さく (/24) 保てる理由のひとつでもあります。
:::

# やってみて、はまった点を踏まえてこうした

設計の目標と境界が決まっても、実際に手を動かすと、通常の Web アプリ向けクラスタでは出会わない問題に次々ぶつかりました。この book の各章は、そうした「はまった点」と、それを踏まえてどう作り直したかの記録でもあります。

たとえばアクセラレータノードは 1 台が消費する IP の桁が通常のノードと違います。原因は EFA のカード枚数ではなく VPC CNI (Pod に VPC の IP を直接割り当てるネットワークプラグイン) で、Pod 用のセカンダリ IP をノードごとに大量に先取りするため、サブネットを小さく切ると数台で枯渇します (詳細は Basic01)。EFA はセキュリティグループの設定を取りこぼすと「選ばれているのにデータが流れない」という診断困難な状態になります。Capacity Block は購入という操作自体が Terraform の外側で起こり、期限が切れればノードが失われます。そしてクラスタの破棄は、後始末のコントローラを消す順序を間違えるとノードが孤立して課金だけが残ります。

こうした罠のひとつひとつと対処は、それぞれの章で実際のコードとともに解説します。導入となるこの章では深追いせず、まずは「作る過程で何につまずき、なぜ今の設計になったのか」という視点を持って読み進めてください。

# この book で学べること

- Terraform による Amazon EKS クラスタの構築 (Basic01)
- GPU を使わない CPU での torchrun DDP 分散学習の体験 (Basic02)
- Karpenter によるノードの動的プロビジョニング (Basic03)
- `accelerator_pools` という 1 つの変数だけで GPU/Neuron ノードを追加する仕組み (Basic04)
- EFA (Elastic Fabric Adapter) によるマルチノード NCCL 通信の検証 (Basic05)
- Capacity Block (予約 GPU/AWS Trainium) の取得と組み込み (Basic06)
- 軽量 vLLM (OpenAI 互換サーバー) による GPU 推論の動作確認 (Basic07)
- Prometheus と Grafana による GPU メトリクスの可視化 (Basic08)
- AWS Trainium/AWS Inferentia (Neuron) 対応の設計 (Basic09)
- 共有ストレージ Amazon EFS / Amazon FSx for Lustre の使い分け (Basic10)
- 課金を取り残さない安全な破棄と、オプションの公開エンドポイント (Basic11)

# 必要なもの

- AWS アカウント
- AdministratorAccess 相当の IAM 権限 (Amazon EKS・Amazon EC2・Amazon VPC・IAM・Amazon FSx・Amazon EFS の作成権限)
- ローカルまたは CloudShell に Terraform 1.5+ / AWS CLI v2 / kubectl / helm
- GPU/Neuron インスタンス (特に p5en などの Capacity Block) を使う場合はサービスクォータと予算の確認

:::message alert
この book が構築する Amazon EKS クラスタ・GPU/Neuron ノード・NAT ゲートウェイ・Amazon FSx などは、起動している間 AWS 利用料金が発生します。特に GPU/Neuron インスタンスと Capacity Block は高額です。実験が終わったら Basic11 の手順で必ず破棄してください。
:::

# アーキテクチャ概要

この book 全体で構築する分散 AI 基盤の全体像です。Amazon VPC の中に 2 つの AZ を張り、Amazon EKS コントロールプレーンの下で Karpenter が GPU/Neuron の各 NodePool を要求に応じて起動します。共有ストレージ (Amazon EFS / Amazon FSx) や Capacity Block の期限監視 (Amazon EventBridge から Amazon SNS) といった周辺サービスも含みます。

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

全体図では作図の都合で共有ストレージを左の AZ-B に配置していますが、実際には FSx for Lustre は計算ノードと同じ AZ に作成します。Amazon EFS はマルチ AZ 対応なのでどちらの AZ からでもマウントできます。

各コンポーネントは Basic01 以降の各章で 1 つずつ、実際の Terraform コードを引用しながら解説します。全体像を頭に入れたうえで、まずは Basic01 で土台となる Amazon EKS クラスタを立てるところから始めましょう。

# 参考資料

- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter 公式ドキュメント](https://karpenter.sh/)
- [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai)
