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

## インフラのデプロイ

::::details デプロイ手順

:::message alert
`sa-east-1` の場合、そのまま `parallelcluster-prerequisites.yaml` をデプロイすると FSx for Lustre の設定でエラーします。おそらく設定がこのリージョンでは存在しないのだと思われます。そのため今回 FSx for Lustre は特に使わないため設定ごと削除してしまいます。
:::

```bash
export AWS_REGION=sa-east-1

# リージョンの全 AZ を取得
export AZS=$(aws ec2 describe-availability-zones \
  --region ${AWS_REGION} \
  --query 'AvailabilityZones[?State==`available`].ZoneName' \
  --output text | tr '\t' ',')

export NUM_AZS=$(echo $AZS | tr ',' '\n' | wc -l | tr -d ' ')

echo "Region: ${AWS_REGION}"
echo "Available AZs: ${AZS}"
echo "Number of AZs: ${NUM_AZS}"

# テンプレートをダウンロード
curl -s https://raw.githubusercontent.com/awslabs/awsome-distributed-training/main/1.architectures/1.vpc_network/1.vpc-multi-az.yaml -o /tmp/vpc-multi-az.yaml

# CloudFormation スタック作成
aws cloudformation create-stack \
  --stack-name parallelcluster-prerequisites \
  --template-body file:///tmp/vpc-multi-az.yaml \
  --parameters \
    ParameterKey=VPCName,ParameterValue=ParallelCluster-VPC \
    ParameterKey=AvailabilityZones,ParameterValue=\"${AZS}\" \
    ParameterKey=NumberOfAZs,ParameterValue=${NUM_AZS} \
    ParameterKey=CreatePublicSubnets,ParameterValue=true \
    ParameterKey=CreateS3Endpoint,ParameterValue=true \
    ParameterKey=CreateDynamoDBEndpoint,ParameterValue=false \
  --capabilities CAPABILITY_IAM \
  --region ${AWS_REGION}

# Wait for completion (~5 minutes)
aws cloudformation wait stack-create-complete \
  --stack-name parallelcluster-prerequisites \
  --region ${AWS_REGION}
echo "Stack deployed successfully"
```
::::

## Capacity Block for ML で trn2.3xlarge を確保

trn2.3xlarge を Capacity Block for ML を使用して事前に容量を確保します。リージョンは sa-east-1 を利用します。

Capacity Block for ML の確認、予約、管理を楽にするスクリプトセットを作成しました。

```bash
# リポジトリをクローン
git clone https://github.com/littlemex/samples.git && cd samples/aws-neuron/ml-capacity-block
```

### 利用可能な Capacity Block for ML の確認

```bash
# デフォルト: sa-east-1, trn2.3xlarge, 24時間
./mlcb.sh check
```
```bash
[INFO] Checking ML Capacity Block availability for trn2.3xlarge in sa-east-1
[INFO] Duration: 24 hours

[INFO] Search period: 2026-04-15T06:34:39.000Z to 2026-04-22T06:34:39.000Z
[INFO] Searching for available capacity block offerings...

[SUCCESS] Available ML Capacity Block offerings:

Offering ID: cb-00830b6f52e1499a2
  AZ: sa-east-1b
  Start: 2026-04-16T11:30:00.000Z
  End: 2026-04-17T11:30:00.000Z
  Duration: 24 hours
  Upfront Fee: $53.6400 USD

[INFO] To reserve capacity, run:
  ./mlcb.sh reserve <OFFERING_ID> sa-east-1
```

### Capacity Block for ML の予約

利用可能なオファリング ID を使用して予約を作成します。

```bash
# オファリング ID を指定して予約
./mlcb.sh reserve cb-00830b6f52e1499a2 sa-east-1   
```
```bash
[INFO] Reserving ML Capacity Block...
  Offering ID: cb-00830b6f52e1499a2
  Region: sa-east-1
  Instance Count: 1

[INFO] Creating capacity reservation...

[SUCCESS] Capacity reservation created!

Reservation Details:
  Reservation ID: cr-051933077c5521e00
  State: payment-pending
  Instance Type: trn2.3xlarge
  Availability Zone: sa-east-1b
  Start Date: 2026-04-16T11:30:00.000Z
  End Date: 2026-04-17T11:30:00.000Z

[INFO] State is 'payment-pending'. Waiting for payment confirmation...
[INFO] This may take a few minutes.

[INFO] To check the current state, run:
  ./mlcb.sh list sa-east-1

[INFO] Once the state becomes 'active', you can use it in ParallelCluster:
  CapacityReservationTarget:
    CapacityReservationId: cr-051933077c5521e00

[INFO] To cancel this reservation (if needed), run:
  ./mlcb.sh cancel cr-051933077c5521e00 sa-east-1
```

状態が `payment-pending` の場合、支払い確認に数分かかることがあります。数分後に再確認してください。

### 予約の確認

```bash
# 予約を一覧表示
./mlcb.sh list
```
```bash
[INFO] Listing ML Capacity Block reservations in sa-east-1

Active ML Capacity Block Reservations:
========================================

Reservation ID: cr-0d2dc154a2679c429
  State: active
  Instance Type: trn2.3xlarge
  Availability Zone: sa-east-1b
  Instance Count: 2
  Start Date: 2026-04-14T11:30:00.000Z
  End Date: 2026-04-17T11:30:00.000Z
  Type: None

Reservation ID: cr-051933077c5521e00
  State: scheduled
  Instance Type: trn2.3xlarge
  Availability Zone: sa-east-1b
  Instance Count: 0
  Start Date: 2026-04-16T11:30:00.000Z
  End Date: 2026-04-17T11:30:00.000Z
  Type: None

[INFO] To cancel a reservation, run:
  ./mlcb.sh cancel <RESERVATION_ID> sa-east-1
```

`State` が `active` になれば、ParallelCluster で使用できます。`CapacityReservationId` を以下のように設定します。

```bash
export CR_ID="cr-0d2dc154a2679c429"
```

## SSH キーペア作成

SSH キーペアは、HeadNode への SSH 接続に使用します。

:::message
EC2 キーペアはリージョンごとに管理されます。使用するリージョンで作成してください。
:::

```bash
# SSH キーペア作成
export KEY_NAME=pcluster-trn2-key

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
