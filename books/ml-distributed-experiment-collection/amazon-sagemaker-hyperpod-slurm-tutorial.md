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
- [ ] 3. SSH 接続の設定
- [ ] 4. クラスターへの接続
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
なんのための作業か: SSH 接続に必要なクラスター ID、インスタンス ID、ノードグループ名を取得します。
:::

:::message
次のステップに進む条件: クラスター ID とインスタンス ID が正常に取得できること。
:::

クラスター ARN からクラスター ID を取得します。

```bash
aws sagemaker describe-cluster --cluster-name cpu-slurm-cluster --region us-east-1 --query 'ClusterArn' --output text
```

出力例は以下のようになります。

```
arn:aws:sagemaker:us-east-1:123456789012:cluster/q2vei6nzqldz
```

クラスター ID は ARN の最後の部分（`q2vei6nzqldz`）です。

次に、インスタンス ID とノードグループ名を取得します。

```bash
aws sagemaker list-cluster-nodes --cluster-name my-hyperpod-cluster
```

出力例は以下のようになります。

```json
{
    "ClusterNodeSummaries": [
        {
            "InstanceGroupName": "controller-machine",
            "InstanceId": "i-08982ccd4b6b34eb1",
            "InstanceType": "ml.c5.xlarge",
            "LaunchTime": "2024-12-16T10:00:00.000Z",
            "InstanceStatus": {
                "Status": "Running"
            }
        }
    ]
}
```

接続に必要な情報をメモします。この例では、クラスター ID が `q2vei6nzqldz`、ノードグループ名が `controller-machine`、インスタンス ID が `i-08982ccd4b6b34eb1` となります。
::::

::::details 3. easy_ssh.sh スクリプトによる自動設定

:::message
なんのための作業か: SSH 接続を簡単に設定するため、自動化スクリプトを使用します。このスクリプトは SSH 設定ファイルの作成と公開鍵の追加を自動的に行います。
:::

:::message
次のステップに進む条件: スクリプトが正常に実行され、`~/.ssh/config` にクラスターの設定が追加されること。
:::

easy_ssh.sh スクリプトをダウンロードします。

```bash
curl -O https://raw.githubusercontent.com/aws-samples/awsome-distributed-training/main/1.architectures/5.sagemaker-hyperpod/easy-ssh.sh
chmod +x easy-ssh.sh
```

SSH キーペアを生成します（既にある場合はスキップ）。

```bash
ssh-keygen -t rsa -b 4096 -f "$HOME/.ssh/id_rsa" -N ""
```

スクリプトを実行します。

```bash
./easy-ssh.sh -c controller-machine my-hyperpod-cluster
```

スクリプトは、クラスター情報の自動取得、`~/.ssh/config` への設定追加、そして SSH 公開鍵のクラスターへの追加を自動的に実行します。

プロンプトが表示されたら、両方とも `yes` と回答します。

```
Would you like to add my-hyperpod-cluster to ~/.ssh/config (yes/no)?
> yes

Do you want to add your SSH public key ~/.ssh/id_rsa.pub to the cluster (yes/no)?
> yes
```

出力例は以下のようになります。

```
     🚀 HyperPod Cluster Easy SSH Script! 🚀

Cluster id: q2vei6nzqldz
Instance id: i-08982ccd4b6b34eb1
Node Group: controller-machine

✅ adding my-hyperpod-cluster to ~/.ssh/config
✅ Your SSH public key ~/.ssh/id_rsa.pub has been added to the cluster.

Now you can run:
$ ssh my-hyperpod-cluster
```
::::

::::details 4. クラスターへの接続

:::message
なんのための作業か: 設定した SSH 接続を使用してクラスターに接続し、動作を確認します。
:::

:::message
次のステップに進む条件: SSH 接続が成功し、クラスターのシェルプロンプトが表示されること。
:::

SSH でクラスターに接続します。

```bash
ssh my-hyperpod-cluster
```

接続が成功すると、ubuntu ユーザーとしてログインします。

```
Welcome to Ubuntu 20.04.6 LTS (GNU/Linux 5.15.0-1052-aws x86_64)

ubuntu@ip-10-1-4-244:~$
```

クラスター情報を確認します。

```bash
hostname
whoami
```

出力例は以下のようになります。

```
ip-10-1-4-244
ubuntu
```
::::

## Slurm の基本操作

クラスターに接続したら、Slurm コマンドを使用してジョブを管理できます。

:::message
- [ ] 1. クラスターの状態確認
- [ ] 2. 簡単なジョブの実行
- [ ] 3. FSx for Lustre の確認
:::

::::details 1. クラスターの状態確認

:::message
なんのための作業か: Slurm クラスターのノード状態を確認し、Worker Node が正常に起動して利用可能な状態であることを確認します。
:::

:::message
次のステップに進む条件: `sinfo` コマンドでノードの STATE が "idle" または "alloc" と表示され、設定したノード数が確認できること。
:::

Slurm のノード状態を確認します。

```bash
sinfo
```

出力例は以下のようになります。

```
PARTITION AVAIL  TIMELIMIT  NODES  STATE NODELIST
compute*      up   infinite      2   idle worker-group-1-[1-2]
```

詳細なノード情報を確認します。

```bash
sinfo -N -l
```

出力例は以下のようになります。

```
NODELIST          NODES PARTITION       STATE CPUS    S:C:T MEMORY TMP_DISK WEIGHT AVAIL_FE REASON
worker-group-1-1      1  compute*        idle   36   36:1:1 192000        0      1   (null) none
worker-group-1-2      1  compute*        idle   36   36:1:1 192000        0      1   (null) none
```
::::

::::details 2. 簡単なジョブの実行

:::message
なんのための作業か: Slurm を使用して簡単なジョブを実行し、クラスターが正常に動作していることを確認します。
:::

:::message
次のステップに進む条件: ジョブが正常に実行され、全ノードからの出力が確認できること。
:::

`srun` コマンドで簡単なジョブを実行します。

```bash
srun hostname
```

出力例は以下のようになります。

```
ip-10-1-52-212
```

複数ノードで実行します。

```bash
srun --nodes=2 --ntasks-per-node=1 hostname
```

出力例は以下のようになります。

```
ip-10-1-52-212
ip-10-1-90-199
```

バッチジョブを作成して実行します。

```bash
cat << 'EOF' > test-job.sbatch
#!/bin/bash

#SBATCH --job-name=test-job
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=2
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

echo "=== ジョブ開始 ==="
echo "ジョブ ID: $SLURM_JOB_ID"
echo "ノード数: $SLURM_JOB_NUM_NODES"
echo "総タスク数: $SLURM_NTASKS"

srun hostname
srun date

echo "=== ジョブ終了 ==="
EOF
```

ジョブを投入します。

```bash
sbatch test-job.sbatch
```

ジョブの状態を確認します。

```bash
squeue
```

ジョブが完了したら、出力ファイルを確認します。

```bash
cat test-job_*.out
```
::::

::::details 3. FSx for Lustre の確認

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
10.1.71.197@tcp:/oyuutbev  1.2T  5.5G  1.2T   1% /fsx
```

FSx ディレクトリに移動します。

```bash
cd /fsx
ls -la
```

テストファイルを作成します。

```bash
echo "Hello FSx for Lustre" > /fsx/test.txt
cat /fsx/test.txt
```

出力例は以下のようになります。

```
Hello FSx for Lustre
```

テストファイルを削除します。

```bash
rm /fsx/test.txt
```
::::

## クラスターの削除

:::message
- [ ] 1. クラスターの削除実行
- [ ] 2. 削除の完了確認
:::

::::details 1. クラスターの削除実行

:::message
なんのための作業か: 使用が終わったクラスターを削除して、継続的なコストの発生を停止します。クラスターを削除すると、Controller Node、Login Node、Worker Node が全て削除されます。
:::

:::message
次のステップに進む条件: クラスター削除コマンドが正常に実行され、削除プロセスが開始されること。
:::

AWS CLI を使用してクラスターを削除します。

```bash
aws sagemaker delete-cluster --cluster-name my-hyperpod-cluster
```

または、AWS コンソールから削除できます。

1. [SageMaker HyperPod コンソール](https://console.aws.amazon.com/sagemaker/home#/cluster-management)にアクセス
2. 削除するクラスターを選択
3. 「Actions」→「Delete」をクリック
4. 確認ダイアログで「Delete」をクリック
::::

::::details 2. 削除の完了確認

:::message
なんのための作業か: クラスターの削除が完了したことを確認します。削除には 5 から 10 分かかります。
:::

:::message
次のステップに進む条件: クラスター一覧からクラスターが削除されるか、ステータスが「DELETE_COMPLETE」になること。
:::

削除の進行状況を確認します。

```bash
aws sagemaker describe-cluster --cluster-name my-hyperpod-cluster
```

クラスターが削除されると、以下のエラーが返されます。

```
An error occurred (ResourceNotFound) when calling the DescribeCluster operation: 
Could not find cluster 'arn:aws:sagemaker:ap-northeast-1:123456789012:cluster/my-hyperpod-cluster'
```

:::message
**Quick Setup で作成されたリソースについて**

Quick Setup で自動作成された VPC とサブネット、FSx for Lustre ファイルシステム、セキュリティグループといったリソースは、クラスター削除後も残る場合があります。

これらのリソースを削除する場合は、まず FSx コンソールで FSx for Lustre ファイルシステムを削除し、次に VPC コンソールで VPC を削除します。VPC を削除すると、関連するサブネット、ルートテーブル、インターネットゲートウェイも自動的に削除されます。削除前に、他のリソースがこれらを使用していないことを確認してください。
:::
::::

## まとめ

本章では、Amazon SageMaker HyperPod を Slurm オーケストレーションで実際に構築する手順を解説しました。Quick Setup を使用することで、複雑な設定を行うことなく、約 20 分でクラスターを起動できました。

本章で学んだ内容として、Quick Setup と AWS CLI の違いと選択基準、Quick Setup による簡単なクラスター作成、SSM Session Manager を使用した安全な接続、Slurm の基本操作とジョブ管理、そして FSx for Lustre の確認と使用方法について実践的に習得しました。

次章以降では、より高度な機能として、レジリエンシー、チェックポイント管理、動的なノード管理、オブザーバビリティなどについて解説します。
