---
title: "Advanced 01: Getting Started"
free: true
---

このワークショップでは、AWS Trainium2 インスタンス（trn2.3xlarge）と AWS ParallelCluster を組み合わせて、Neuron Kernel Interface（NKI）0.3.0 の実験環境を構築します。

本章では、環境準備の手順を説明します。

:::message alert
本資料では sa-east-1 リージョンを使用します。
:::

# 解説

https://zenn.dev/tosshi/books/aws-parallelcluster-workshop/viewer/aws-parallelcluster-workshop-01-getting-started

AWS ParallelCluster のインフラストラクチャの解説については上記の Basic01 - Getting Started を確認してください。

今回は Capacity Blocks for ML というリソース確保の仕組みを用いて trn2.3xlarge を確保、確保したリソースを AWS ParallelCluster で利用する手順を解説していきます。AWS 公式のワークショップでも Capacity Blocks for ML に関するセクションがあるので確認してみてください。

https://catalog.workshops.aws/ml-on-aws-parallelcluster/en-US/03-cluster/03-capacity-block

# ワークショップ実施

## Capacity Block for ML で trn2.3xlarge を確保

trn2.3xlarge を Capacity Block for ML を使用して事前に容量を確保します。リージョンは sa-east-1 を利用します。

Capacity Block for ML の確認、予約、管理を楽にするスクリプトセットを作成しました。

```bash
# リポジトリをクローン
cd ~/ && git clone https://github.com/littlemex/samples.git && cd samples/aws-neuron/ml-capacity-block
```

### 利用可能な Capacity Block for ML の確認

```bash
export CR_REGION="sa-east-1"
export CR_AZ=${CR_REGION}b
```

FSx for Lustre を使う場合、AZ を指定する必要があり、AZ が変わると FSx の AZ を変更しないといけなくなるため手間です。基本的には AZ は固定して環境を構築するのがおすすめです。

```bash
# デフォルト: sa-east-1, trn2.3xlarge
./mlcb.sh check --az ${CR_AZ}
```

```bash
[INFO] Checking ML Capacity Block availability
  Region: sa-east-1
  Instance Type: trn2.3xlarge
  Instance Count: 1
  Duration: 72 hours
  Filter AZ: sa-east-1b
  Search period: next 7 days
  Timezone: JST (Asia/Tokyo)

[INFO] Search period: 2026-04-18T05:43:34.000Z to 2026-04-25T05:43:34.000Z
[INFO] Searching for available capacity block offerings...

[SUCCESS] Available ML Capacity Block offerings in sa-east-1b:

Offering ID: cb-09e4c80d0c62ac1a6
  AZ: sa-east-1b
  Instance Count: 1
  Start: 2026-04-19 20:30:00 JST
  End: 2026-04-22 20:30:00 JST
  Duration: 72 hours
  Upfront Fee: $160.9200 USD

[INFO] To reserve capacity, run:
  ./mlcb.sh reserve <OFFERING_ID> ${CR_REGION}
```

### Capacity Block for ML の予約

利用可能なオファリング ID を使用して予約を作成します。

```bash
# オファリング ID を指定して予約
./mlcb.sh reserve cb-09e4c80d0c62ac1a6 ${CR_REGION}
```
```bash
[INFO] Reserving ML Capacity Block...
  Offering ID: cb-09e4c80d0c62ac1a6
  Region: sa-east-1

[INFO] Creating capacity reservation...

[SUCCESS] ML Capacity Block reserved successfully!

Reservation Details:
  Reservation ID: cr-04fd34f770eb8bf70
  State: payment-pending
  Instance Type: trn2.3xlarge
  Availability Zone: sa-east-1b
  Instance Count: 0
  Start: 2026-04-19 20:30:00 JST
  End: 2026-04-22 20:30:00 JST

[INFO] To list all reservations, run:
  ./mlcb.sh list sa-east-1
```

状態が `payment-pending` の場合、支払い確認に数分かかることがあります。数分後に再確認してください。

### 予約の確認

`CR_ID` は今後も利用する重要な ID なのでメモしておきましょう。

```bash
# 予約を一覧表示
export CR_ID="cr-04fd34f770eb8bf70"
./mlcb.sh list |grep ${CR_ID} -A 20
```
```bash
Reservation ID: cr-04fd34f770eb8bf70
  State: scheduled
  Instance Type: trn2.3xlarge
  AZ: sa-east-1b
  Instance Count: 0
  Available: 0
  Start: 2026-04-19 20:30:00 JST
  End: 2026-04-22 20:30:00 JST
```

`State` が `active` になれば、ParallelCluster で使用できます。`CapacityReservationId` を以下のように設定します。

## インフラのデプロイ

::::details デプロイ手順

```bash
export AWS_REGION=${CR_REGION}
echo "Deploying to region: ${AWS_REGION}, az: ${CR_AZ}"
```

:::message
`sa-east-1` では FSx OpenZFS の `deploymentType: SINGLE_AZ_HA_1` がサポートされていません。そのため設定を修正してからデプロイします。このようにリージョンによって対応状況が異なるケースがあるため確認することをお勧めします。
:::

```bash
# 設定を修正
curl -o /tmp/prerequisites-fixed.yaml https://raw.githubusercontent.com/littlemex/samples/main/aws-neuron/parallelcluster/parallelcluster-prerequisites.yaml

aws cloudformation create-stack \
  --stack-name parallelcluster-prerequisites \
  --template-body file:///tmp/prerequisites-fixed.yaml \
  --parameters ParameterKey=PrimarySubnetAZ,ParameterValue=${CR_AZ} \
  --capabilities CAPABILITY_IAM \
  --region ${AWS_REGION}

# Wait for completion (~5 minutes)
aws cloudformation wait stack-create-complete \
  --stack-name parallelcluster-prerequisites \
  --region ${AWS_REGION}
echo "Stack deployed successfully"
```
::::

## SSH キーペア作成

SSH キーペアは、HeadNode への SSH 接続に使用します。

:::message
EC2 キーペアはリージョンごとに管理されます。使用するリージョンで作成してください。
:::

```bash
# SSH キーペア作成
export KEY_NAME=pcluster-trn2-ws-key

cd ~/.ssh
aws ec2 create-key-pair \
  --key-name ${KEY_NAME} \
  --query KeyMaterial \
  --key-type ed25519 \
  --region ${AWS_REGION} \
  --output text > ${KEY_NAME}.pem

# パーミッション設定
chmod 600 ${KEY_NAME}.pem

# 作成確認
aws ec2 describe-key-pairs \
  --key-names ${KEY_NAME} \
  --region ${AWS_REGION} \
  --query 'KeyPairs[0].[KeyName,KeyType,KeyFingerprint]' \
  --output table
```

## まとめ

本章では、以下の環境準備を完了しました：

- インフラのデプロイ（VPC、サブネット）
- Capacity Block for ML の予約
- SSH キーペアの作成

次の章では、Capacity Block for ML で確保したリソースで ParallelCluster を構築します。

## 参考資料

- [AWS ParallelCluster ワークショップ Getting Started by @tosshi](https://zenn.dev/tosshi/books/aws-parallelcluster-workshop/viewer/aws-parallelcluster-workshop-01-getting-started)
- [AWS CLI Installation Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- [AWS ParallelCluster User Guide](https://docs.aws.amazon.com/parallelcluster/)
