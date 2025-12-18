---
title: "Amazon SageMaker HyperPod Getting Started by SLURM"
emoji: "🚀"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["aws", "sagemaker", "hyperpod", "distributed", "infrastructure"]
free: true
---

::::details 前提
:::message
**対象読者**: 大規模基盤モデルがどういうものかを理解している方、これからモデル学習を行う方
:::
:::message
**ライセンス**: © 2025 littlemex.
本文および自作図表: CC BY 4.0
※公式ドキュメントからの引用や翻訳部分は原典の著作権に従います。
引用画像: 各画像の出典に記載されたライセンスに従います。
:::
:::message
一部 AI を用いて文章を作成します。レビューは実施しますが、見逃せない重大な間違いなどがあれば[こちらの Issue](https://github.com/littlemex/samples/issues) から連絡をお願いします。
:::
::::

本章では Amazon SageMaker HyperPod を Slurm オーケストレーションオプションで実際に試してみましょう。以下の AI on SageMaker HyperPod ページを参考に、日本語での説明と補足を加えて Getting Started を実施します。

:::message
実装が変更される可能性があるため必要に応じて公式ドキュメントを確認ください。
:::

https://awslabs.github.io/ai-on-sagemaker-hyperpod/docs/getting-started/orchestrated-by-slurm

::::details 参照ドキュメント

https://docs.aws.amazon.com/sagemaker/latest/dg/smcluster-getting-started-slurm.html
https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-run-jobs-slurm.html

より実践的な例とソリューションについては以下のリポジトリとワークショップを参考にしてください。
https://github.com/aws-samples/awsome-distributed-training/tree/main/1.architectures/5.sagemaker-hyperpod
https://catalog.workshops.aws/sagemaker-hyperpod

エラーが発生した際にはトラブルシューティングを参考にしてください。
https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-troubleshooting.html
::::

---

# Amazon SageMaker HyperPod (Orchestrated by Slurm) 環境の構築

本章では、Amazon SageMaker HyperPod Orchestrated by Slurm 環境の構築方法を解説します。本章では最低限の動作確認を目的とした構成を紹介します。すでに HyperPod や Slurm のアーキテクチャについては解説済みなので割愛します。

## クラスター作成方法の選択

Amazon SageMaker HyperPod クラスターの作成には、いくつかの方法があります。

:::message
本章では、初心者でも簡単に始められる **Quick Setup** を使用してクラスターを作成します。
:::

**Quick Setup（推奨）**

AWS コンソールから数クリックでクラスターを作成できる簡易セットアップです。VPC とサブネット、FSx for Lustre ファイルシステム（最適なパフォーマンス設定）、セキュリティグループとネットワーク設定、IAM 権限、Slurm の基本設定といったリソースが自動的にプロビジョニングされます。Quick Setup のメリットとして、設定ファイルの作成が不要であること、S3 バケットの作成やライフサイクルスクリプトのアップロードが不要であること、約 20 分でクラスターが利用可能になること、そして初心者でも簡単に始められることが挙げられます。

:::message
詳細な制御が必要な場合や自ら全体のインフラ環境を IaC で管理したい場合は[こちらのワークショップ](https://catalog.workshops.aws/sagemaker-hyperpod/en-US/00-setup/manual-cfn-deploy)を参考にすると良いでしょう。
:::

## 事前確認: インスタンスタイプとクォータ

:::message alert
**超重要: クラスター作成前に必ずクォータを確認してください**

SageMaker HyperPod クラスターの作成には、使用するインスタンスタイプごとにクォータ（利用上限）が設定されています。クォータが 0 のインスタンスタイプは使用できず、クラスター作成が失敗します。
:::

:::message
本チュートリアルでは `us-east-1` を利用します。リージョンごとにクォータや対応インスタンスを必ず確認してください。
:::

### クォータの確認方法

[Service Quotas コンソール（us-east-1）](https://us-east-1.console.aws.amazon.com/servicequotas/home/services/sagemaker/quotas?region=us-east-1)にアクセスし、検索バーに使用予定のインスタンスタイプ（例: `ml.c5.xlarge`）を入力して、「Applied quota value」を確認します。

### 動作確認用の推奨インスタンス構成

初回のクラスター作成の動作確認では、**GPU インスタンスではなく CPU インスタンスの使用を強く推奨**します。理由としては、**クォータ変更の容易性**: GPU インスタンス（ml.g5、ml.p4d など）はデフォルトでクォータが 0 の場合があり、クォータ引き上げに数日かかる可能性があります。**動作確認用途**: Slurm クラスターのコマンド確認、ジョブ管理の確認に GPU は不要です。

**本チュートリアルのインスタンス構成**

| グループ | インスタンスタイプ | 数 | 用途 |
|---------|-----------------|---|------|
| **Controller** | `ml.c5.xlarge` | 1 | Slurm コントローラー |
| **Login** | `ml.c5.xlarge` | 1 | SSH ログイン用 |
| **Worker** | `ml.c5.4xlarge` | 2 | 計算ワークロード |

::::details よくあるエラーと対処法

**エラー: `ResourceLimitExceeded`**

```
Resource handler returned message: "Limit exceeded for resource of type 
'AWS::SageMaker::Cluster'. Reason: ResourceLimitExceeded"
```

**原因**
- 使用しようとしているインスタンスタイプのクォータが 0 または不足している
- 前回失敗したクラスターがまだ削除されていない

**対処法**
1. クォータを確認（上記の方法）
2. クォータが 0 の場合は引き上げをリクエスト
3. 既存のクラスターを確認して削除
::::

## Quick Setup によるクラスター作成

:::message
- [ ] 1. AWS コンソールへのアクセス
- [ ] 2. Quick Setup の選択
- [ ] 3. クラスター基本設定の入力
- [ ] 4. クラスター作成の実行と確認
:::

::::details 1. AWS コンソールへのアクセス

:::message
なんのための作業か: Amazon SageMaker HyperPod のクラスター管理コンソールにアクセスし、新しいクラスターを作成する準備をします。
:::

:::message
次のステップに進む条件: SageMaker HyperPod のクラスター管理ページが表示され、「HyperPod クラスターを作成」ボタンが確認できること。
:::

:::message alert
**リージョンについて**

本章では us-east-1（バージニア北部）リージョンを使用します。Quick Setup は us-east-1 で利用可能です。
:::

1. [SageMaker HyperPod クラスター管理ページ（us-east-1）](https://us-east-1.console.aws.amazon.com/sagemaker/home?region=us-east-1#/cluster-management)にアクセスします。
2. 左側のナビゲーションペインで「HyperPod clusters」を選択します。
3. 右上の「HyperPod クラスターを作成」ボタンをクリックします。
::::

::::details 2. Quick Setup の選択

:::message
なんのための作業か: クラスター作成方法として Quick Setup を選択するための作業です。
:::

:::message
次のステップに進む条件: Quick Setup が選択され、設定入力画面が表示されること。
:::

クラスター作成画面では、2 つのオプションが表示されます。Quick Setup（推奨）は全ての依存リソースを自動的にプロビジョニングし、数クリックでクラスターを起動できる初心者に最適なオプションです。一方、Custom Setup は詳細な設定をカスタマイズでき、既存の VPC や FSx を使用可能なオプションです。

今回は「Quick Setup」を選択します。

![](/images/books/ml-distributed-experiment-collection/hyperpod-slurm-quick-setup-create-cluster-01.png)
::::


::::details 3. クラスター基本設定の入力

:::message
なんのための作業か: クラスターの名前、リージョン、インスタンスタイプなどの基本設定を入力します。これらの設定により、クラスターのリソースが決定されます。
:::

:::message
次のステップに進む条件: 全必須フィールドが入力され、バリデーションエラーがないこと。
:::

以下の情報を入力します。

**クラスター名**
- 例: `cpu-slurm-cluster`
- 英数字とハイフンのみ使用可能

**インスタンスグループ設定**

Quick Setup では、Controller、Login、Worker の3 つのインスタンスグループを作成します。

![](/images/books/ml-distributed-experiment-collection/hyperpod-slurm-quick-setup-create-cluster-02.png)
![](/images/books/ml-distributed-experiment-collection/hyperpod-slurm-quick-setup-create-cluster-03.png)
![](/images/books/ml-distributed-experiment-collection/hyperpod-slurm-quick-setup-create-cluster-04.png)

1. **Controller Group**
   - 役割: Slurm コントローラーノード（ジョブスケジューリング管理）
   - 推奨インスタンスタイプ: `ml.c5.xlarge`

2. **Login Group**
   - 役割: ユーザー SSH ログイン用エントリーポイント
   - 推奨インスタンスタイプ: `ml.c5.xlarge`
   - **重要**: このグループを省略すると、クラスター作成が失敗する場合があります

3. **Worker Group**
   - 役割: 実際の計算ワークロードを実行
   - 推奨インスタンスタイプ: `ml.c5.4xlarge`（動作確認用）
::::

::::details 4. クラスター作成の実行と確認
:::message
なんのための作業か: クラスターを作成し、作成状況を監視し、InService 状態になるまで待機します。
:::

:::message
次のステップに進む条件: クラスターのステータスが「InService」になること。通常 15 から 20 分かかります。
:::

1. インスタンスグループの作成が完了したら「送信」ボタンを押下します。
2. 正常に作成が完了するとステータスが「InService」になり、クラスター作成は完了です


![](/images/books/ml-distributed-experiment-collection/hyperpod-slurm-quick-setup-create-cluster-05.png)
![](/images/books/ml-distributed-experiment-collection/hyperpod-slurm-quick-setup-create-cluster-06.png)

::::

## クラスターへの接続

クラスターが InService 状態になったら、SSH で接続できます。

:::message
- [ ] 1. SSM Session Manager プラグインのインストール
- [ ] 2. クラスター情報の取得
- [ ] 3. easy_ssh.sh スクリプトによる自動設定
- [ ] 4. 接続確認
- [ ] 5. FSx for Lustre の確認
:::

::::details 1. SSM Session Manager プラグインのインストール

:::message
なんのための作業か: AWS Systems Manager Session Manager を使用してクラスターに安全に接続するため、必要なプラグインをインストールします。SSH ポートを開く必要がなく、IAM による認証のみで接続できます。
:::

:::message
次のステップに進む条件: `session-manager-plugin --version` コマンドが正常に実行され、バージョン情報が表示されること。
:::

使用している OS に応じて、Session Manager プラグインをインストールします。

**macOS（ARM64）の場合**

```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac_arm64/sessionmanager-bundle.zip" -o "sessionmanager-bundle.zip"
unzip sessionmanager-bundle.zip
sudo ./sessionmanager-bundle/install -i /usr/local/sessionmanagerplugin -b /usr/local/bin/session-manager-plugin
```

**macOS（x86_64）の場合**

```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac/sessionmanager-bundle.zip" -o "sessionmanager-bundle.zip"
unzip sessionmanager-bundle.zip
sudo ./sessionmanager-bundle/install -i /usr/local/sessionmanagerplugin -b /usr/local/bin/session-manager-plugin
```

**Amazon Linux 2 の場合**

```bash
sudo yum install -y https://s3.amazonaws.com/session-manager-downloads/plugin/latest/linux_64bit/session-manager-plugin.rpm
```

**Ubuntu の場合**

```bash
sudo curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "/tmp/session-manager-plugin.deb"
sudo dpkg -i /tmp/session-manager-plugin.deb
```

インストールを確認します。

```bash
session-manager-plugin --version
```
::::

::::details 2. クラスター情報の取得

:::message
なんのための作業か: SSH 接続に必要なクラスター ID、インスタンス ID、ノードグループ名を取得し、環境変数ファイルとして保存します。
:::

:::message
次のステップに進む条件: `hyperpod-cluster.env` ファイルが作成され、必要な環境変数がすべて含まれていること。
:::

以下のコマンドを実行して、クラスター情報を自動取得するスクリプトを作成し実行します。

```bash
# スクリプトを作成
cat > get-hyperpod-info.sh << 'SCRIPT_EOF'
#!/bin/bash

# HyperPod クラスター情報取得スクリプト
# 使用方法: ./get-hyperpod-info.sh <クラスター名> [リージョン]

set -e

# 引数チェック
if [ $# -lt 1 ]; then
    echo "使用方法: $0 <クラスター名> [リージョン]"
    echo "例: $0 cpu-slurm-cluster us-east-1"
    exit 1
fi

CLUSTER_NAME=$1
REGION=${2:-us-east-1}
OUTPUT_FILE="hyperpod-cluster.env"

echo "クラスター情報を取得しています..."
echo "クラスター名: $CLUSTER_NAME"
echo "リージョン: $REGION"
echo ""

# クラスター情報を取得
CLUSTER_ARN=$(aws sagemaker describe-cluster \
    --cluster-name "$CLUSTER_NAME" \
    --region "$REGION" \
    --query 'ClusterArn' \
    --output text 2>/dev/null)

if [ -z "$CLUSTER_ARN" ] || [ "$CLUSTER_ARN" == "None" ]; then
    echo "エラー: クラスター '$CLUSTER_NAME' が見つかりません"
    echo "既存のクラスターを確認してください:"
    echo "  aws sagemaker list-clusters --region $REGION"
    exit 1
fi

# クラスター ID を抽出
CLUSTER_ID=$(echo "$CLUSTER_ARN" | awk -F/ '{print $NF}')

echo "✓ クラスター ARN: $CLUSTER_ARN"
echo "✓ クラスター ID: $CLUSTER_ID"
echo ""

# ノード情報を取得
echo "ノード情報を取得しています..."
NODES_JSON=$(aws sagemaker list-cluster-nodes \
    --cluster-name "$CLUSTER_NAME" \
    --region "$REGION" \
    2>/dev/null)

if [ -z "$NODES_JSON" ]; then
    echo "エラー: ノード情報の取得に失敗しました"
    exit 1
fi

# Controller ノード情報を取得
CONTROLLER_INSTANCE_ID=$(echo "$NODES_JSON" | jq -r '.ClusterNodeSummaries[] | select(.InstanceGroupName | test("controller"; "i")) | .InstanceId' | head -n 1)
CONTROLLER_GROUP_NAME=$(echo "$NODES_JSON" | jq -r '.ClusterNodeSummaries[] | select(.InstanceGroupName | test("controller"; "i")) | .InstanceGroupName' | head -n 1)

# Login ノード情報を取得（存在する場合）
LOGIN_INSTANCE_ID=$(echo "$NODES_JSON" | jq -r '.ClusterNodeSummaries[] | select(.InstanceGroupName | test("login"; "i")) | .InstanceId' | head -n 1)
LOGIN_GROUP_NAME=$(echo "$NODES_JSON" | jq -r '.ClusterNodeSummaries[] | select(.InstanceGroupName | test("login"; "i")) | .InstanceGroupName' | head -n 1)

# Worker ノード情報を取得（最初の1つ）
WORKER_INSTANCE_ID=$(echo "$NODES_JSON" | jq -r '.ClusterNodeSummaries[] | select(.InstanceGroupName | test("worker"; "i")) | .InstanceId' | head -n 1)
WORKER_GROUP_NAME=$(echo "$NODES_JSON" | jq -r '.ClusterNodeSummaries[] | select(.InstanceGroupName | test("worker"; "i")) | .InstanceGroupName' | head -n 1)

echo "✓ Controller: $CONTROLLER_GROUP_NAME ($CONTROLLER_INSTANCE_ID)"
if [ -n "$LOGIN_INSTANCE_ID" ] && [ "$LOGIN_INSTANCE_ID" != "null" ]; then
    echo "✓ Login: $LOGIN_GROUP_NAME ($LOGIN_INSTANCE_ID)"
fi
if [ -n "$WORKER_INSTANCE_ID" ] && [ "$WORKER_INSTANCE_ID" != "null" ]; then
    echo "✓ Worker: $WORKER_GROUP_NAME ($WORKER_INSTANCE_ID)"
fi
echo ""

# 環境変数ファイルを作成
cat > "$OUTPUT_FILE" << ENV_EOF
# HyperPod Cluster 環境変数
# 生成日時: $(date)
# クラスター名: $CLUSTER_NAME

export HYPERPOD_CLUSTER_NAME="$CLUSTER_NAME"
export HYPERPOD_REGION="$REGION"
export HYPERPOD_CLUSTER_ARN="$CLUSTER_ARN"
export HYPERPOD_CLUSTER_ID="$CLUSTER_ID"
export HYPERPOD_CONTROLLER_INSTANCE_ID="$CONTROLLER_INSTANCE_ID"
export HYPERPOD_CONTROLLER_GROUP_NAME="$CONTROLLER_GROUP_NAME"
ENV_EOF

# Login ノードの情報を追加（存在する場合）
if [ -n "$LOGIN_INSTANCE_ID" ] && [ "$LOGIN_INSTANCE_ID" != "null" ]; then
    cat >> "$OUTPUT_FILE" << ENV_EOF
export HYPERPOD_LOGIN_INSTANCE_ID="$LOGIN_INSTANCE_ID"
export HYPERPOD_LOGIN_GROUP_NAME="$LOGIN_GROUP_NAME"
ENV_EOF
fi

# Worker ノードの情報を追加（存在する場合）
if [ -n "$WORKER_INSTANCE_ID" ] && [ "$WORKER_INSTANCE_ID" != "null" ]; then
    cat >> "$OUTPUT_FILE" << ENV_EOF
export HYPERPOD_WORKER_INSTANCE_ID="$WORKER_INSTANCE_ID"
export HYPERPOD_WORKER_GROUP_NAME="$WORKER_GROUP_NAME"
ENV_EOF
fi

echo "✅ クラスター情報を $OUTPUT_FILE に保存しました"
echo ""
echo "環境変数を読み込むには:"
echo "  source $OUTPUT_FILE"
echo ""
echo "読み込み後、以下のように使用できます:"
echo "  echo \$HYPERPOD_CLUSTER_ID"
echo "  echo \$HYPERPOD_CONTROLLER_INSTANCE_ID"
SCRIPT_EOF

# スクリプトに実行権限を付与
chmod +x get-hyperpod-info.sh

# スクリプトを実行（クラスター名を実際の名前に変更してください）
./get-hyperpod-info.sh cpu-slurm-cluster us-east-1

# 環境変数を読み込む
source hyperpod-cluster.env

# 確認
echo "環境変数が読み込まれました:"
echo "  HYPERPOD_CLUSTER_NAME: $HYPERPOD_CLUSTER_NAME"
echo "  HYPERPOD_CLUSTER_ID: $HYPERPOD_CLUSTER_ID"
echo "  HYPERPOD_CONTROLLER_INSTANCE_ID: $HYPERPOD_CONTROLLER_INSTANCE_ID"
```

出力例は以下のようになります。

```
クラスター情報を取得しています...
クラスター名: cpu-slurm-cluster
リージョン: us-east-1

✓ クラスター ARN: arn:aws:sagemaker:us-east-1:776010787911:cluster/abc123def456
✓ クラスター ID: abc123def456

ノード情報を取得しています...
✓ Controller: my-controller-group (i-0123456789abcdef0)
✓ Login: my-login-group (i-0fedcba9876543210)
✓ Worker: worker-group-1 (i-0abcdef123456789a)

✅ クラスター情報を hyperpod-cluster.env に保存しました

環境変数を読み込むには:
  source hyperpod-cluster.env

読み込み後、以下のように使用できます:
  echo $HYPERPOD_CLUSTER_ID
  echo $HYPERPOD_CONTROLLER_INSTANCE_ID
```

作成された `hyperpod-cluster.env` ファイルの内容例は以下のようになります。

```bash
# HyperPod Cluster 環境変数
# 生成日時: Wed Dec 18 15:30:00 UTC 2024
# クラスター名: cpu-slurm-cluster

export HYPERPOD_CLUSTER_NAME="cpu-slurm-cluster"
export HYPERPOD_REGION="us-east-1"
export HYPERPOD_CLUSTER_ARN="arn:aws:sagemaker:us-east-1:776010787911:cluster/abc123def456"
export HYPERPOD_CLUSTER_ID="abc123def456"
export HYPERPOD_CONTROLLER_INSTANCE_ID="i-0123456789abcdef0"
export HYPERPOD_CONTROLLER_GROUP_NAME="my-controller-group"
export HYPERPOD_LOGIN_INSTANCE_ID="i-0fedcba9876543210"
export HYPERPOD_LOGIN_GROUP_NAME="my-login-group"
export HYPERPOD_WORKER_INSTANCE_ID="i-0abcdef123456789a"
export HYPERPOD_WORKER_GROUP_NAME="worker-group-1"
```
::::

::::details 3. easy_ssh.sh スクリプトによる自動設定

:::message
なんのための作業か: SSH 接続を簡単に設定するため、自動化スクリプトを使用してクラスター接続します。このスクリプトは SSH 設定ファイルの作成と公開鍵の追加を自動的に行います。
:::

:::message
次のステップに進む条件: スクリプトが正常に実行され、`~/.ssh/config` にクラスターの設定が追加され、クラスター接続されること。
:::

前のステップで作成した環境変数を使用して、SSH 設定を自動化します。

easy_ssh.sh スクリプトをダウンロードします。

```bash
curl -O https://raw.githubusercontent.com/aws-samples/awsome-distributed-training/main/1.architectures/5.sagemaker-hyperpod/easy-ssh.sh
chmod +x easy-ssh.sh
```

SSH キーペアを生成します（既にある場合はスキップ）。

```bash
ssh-keygen -t rsa -b 4096 -f "$HOME/.ssh/id_rsa" -N ""
```

環境変数を使用してスクリプトを実行します。

```bash
# 環境変数が読み込まれていることを確認
source hyperpod-cluster.env

# Controller グループ名を使用してスクリプトを実行
./easy-ssh.sh -c $HYPERPOD_CONTROLLER_GROUP_NAME $HYPERPOD_CLUSTER_NAME
```

プロンプトが表示されたら、両方とも `yes` と回答します。

```
Would you like to add cpu-slurm-cluster to ~/.ssh/config (yes/no)?
> yes

Do you want to add your SSH public key ~/.ssh/id_rsa.pub to the cluster (yes/no)?
> yes
```

出力例は以下のようになります。

```
./easy-ssh.sh -c $HYPERPOD_CONTROLLER_GROUP_NAME $HYPERPOD_CLUSTER_NAME

=================================================

==== 🚀 HyperPod Cluster Easy SSH Script! 🚀 ====


=================================================
Cluster id: 7isg1upszym4
Instance id: i-0becf575c2a093233
Node Group: controller
SSH User: ubuntu
grep: /home/coder/.ssh/config: No such file or directory
Would you like to add cpu-slurm-cluster to ~/.ssh/config (yes/no)?
> yes
✅ adding cpu-slurm-cluster to ~/.ssh/config:
2. Do you want to add your SSH public key ~/.ssh/id_rsa.pub to user ubuntu on the cluster (yes/no)?
> yes
Adding ... ssh-rsa XXXXXX= coder@current-work

Starting session with SessionId: i-0390229058244a214-6d3giy7urclrt3vaepnaip2i3x


Exiting session with sessionId: i-0390229058244a214-6d3giy7urclrt3vaepnaip2i3x.

✅ Your SSH public key ~/.ssh/id_rsa.pub has been added to user ubuntu on the cluster.

Now you can run:

$ ssh cpu-slurm-cluster

Starting session with SessionId: i-0390229058244a214-yd6d3nz5ajyclrj3rh4evhqqge
#
```
::::

::::details 4. 接続確認

:::message
なんのための作業か: SSH 接続後の動作を確認します。
:::

:::message
次のステップに進む条件: SSH 接続が成功し、クラスターのシェルプロンプトが表示されること。
:::

```bash
hostname
whoami
```

出力例は以下のようになります。

```
ip-10-4-109-244
root
```

クラスターに接続したら、Slurm コマンドを使用してみましょう。

**Slurm のノード状態を確認します。**

```bash
sinfo
```

出力例は以下のようになります。

```
PARTITION     AVAIL  TIMELIMIT  NODES  STATE NODELIST
dev*             up   infinite      2   idle ip-10-4-33-25,ip-10-4-198-29
ml.c5.4xlarge    up   infinite      2   idle ip-10-4-33-25,ip-10-4-198-29
```

**詳細なノード情報を確認します。**

```bash
sinfo -N -l
```

出力例は以下のようになります。

```
Thu Dec 18 15:50:47 2025
NODELIST        NODES     PARTITION       STATE CPUS    S:C:T MEMORY TMP_DISK WEIGHT AVAIL_FE REASON              
ip-10-4-33-25       1          dev*        idle 16      1:8:2  32768        0      1   (null) none                
ip-10-4-33-25       1 ml.c5.4xlarge        idle 16      1:8:2  32768        0      1   (null) none                
ip-10-4-198-29      1          dev*        idle 16      1:8:2  32768        0      1   (null) none                
ip-10-4-198-29      1 ml.c5.4xlarge        idle 16      1:8:2  32768        0      1   (null) none  
```

**`srun` コマンドで簡単なジョブを実行します。**

```bash
srun hostname
```

出力例は以下のようになります。

```
ip-10-4-33-25
```

**複数ノードでジョブを実行します。**

```bash
srun --nodes=2 --ntasks-per-node=1 hostname
```

出力例は以下のようになります。

```
ip-10-4-33-25
ip-10-4-198-29
```

バッチジョブを作成して実行します。
::::

::::details 5. FSx for Lustre の確認

:::message
なんのための作業か: FSx for Lustre ファイルシステムが正常にマウントされ、読み書きができることを確認します。
:::

:::message
次のステップに進む条件: `/fsx` ディレクトリにアクセスでき、ファイルの作成と削除が正常に行えること。
:::

マウント状態を確認します。

```bash
df -h | grep fsx
```

出力例は以下のようになります。

```
10.4.212.16@tcp:/c2rqdamv  1.2T   29M  1.2T   1% /fsx
```

FSx ディレクトリに移動します。

```bash
cd /fsx
ls -la
```

```
total 102
drwxr-xr-x  6 root   root   33280 Dec 18 14:41 .
drwxr-xr-x 20 root   root    4096 Dec 18 14:38 ..
drwxrwxrwt  2 root   root   33280 Dec 18 14:41 enroot
drwxr-x---  7 ubuntu ubuntu 33280 Nov 14 21:26 ubuntu
```
::::

## クラスターの削除

:::message
- [ ] 1. クラスターの削除実行
:::

::::details 1. クラスターの削除実行

:::message
なんのための作業か: 使用が終わったクラスターを削除して、継続的なコストの発生を停止します。クラスターを削除すると、Controller Node、Login Node、Worker Node が全て削除されます。
:::

:::message
次のステップに進む条件: クラスター削除コマンドが正常に実行され、削除プロセスが開始されること。
:::

AWS CLI や Amazon SageMaker AI のコンソール、CloudFormation ページから削除することができます。
::::

## まとめ

本章では、Amazon SageMaker HyperPod を Slurm オーケストレーションで実際に構築する手順を解説しました。Quick Setup を使用することで、複雑な設定を行うことなく、約 20 分でクラスターを起動できました。起動したクラスターに SSM Session Manager を使用して安全に接続し、Slurm の基本操作とジョブ管理、そして FSx for Lustre の確認と使用方法について確認しました。通常であれば構築に関する知識習得や実際の構築作業に数週間を要してもおかしくありませんが、AWS のベストプラクティスに沿った Slurm 環境を数十分で作成することができるのは非常に魅力的ではないでしょうか。