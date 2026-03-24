---
title: "Getting Started: インフラ構築"
type: "tech"
free: true
---

本章では、自己所有の AWS アカウントで AWS ParallelCluster ワークショップを進めるためのインフラを構築します。以下の手順を実施します。

1. CloudFormation で VPC・FSx ファイルシステムをデプロイ
2. AWS CloudShell を起動して接続確認

# 0. 事前準備

## AWS マネジメントコンソールへのサインイン

AWS アカウントに[サインイン](https://signin.aws.amazon.com/console)します。

## リージョンの選択

コンソール右上のリージョン選択メニューから **us-east-2（米国東部 オハイオ）** または **us-west-2（米国西部 オレゴン）** を選択します。

![AWSコンソール リージョン選択](/images/ml-parallelcluster-aws-console.png)

:::message
GPU インスタンスや Capacity Reservation がある場合は、それらが存在するリージョンを選択してください。以降の手順はすべて選択したリージョンで実施します。
:::

# 1. Core インフラのデプロイ

## 構築されるアーキテクチャ

![コアインフラアーキテクチャ](/images/ml-parallelcluster-core-infra-architecture.png)

CloudFormation テンプレート `parallelcluster-prerequisites.yaml` を使って以下のリソースを一括作成します。

| リソース | 説明 |
|--------|------|
| VPC | `10.0.0.0/16`（パブリック用）+ `10.1.0.0/16`（プライベート用）の 2 CIDR 構成 |
| パブリックサブネット | ヘッドノード配置用。インターネット GW に直接ルーティング |
| プライベートサブネット | コンピュートノード配置用。NAT Gateway 経由でのみ外部接続 |
| インターネット GW + NAT GW | パブリック→インターネット直通、プライベート→NAT 経由 |
| セキュリティグループ | EFA ノード間通信（全プロトコル）を許可 |
| S3 VPC エンドポイント | VPC 内から S3 へパブリック IP を経由せずアクセス |
| FSx for Lustre | 1.2 TB の高性能並列ファイルシステム（`/fsx`）|
| FSx for OpenZFS | ホームディレクトリ用共有ストレージ（`/home`）|

## デプロイ手順

1. この [URL](https://console.aws.amazon.com/cloudformation/home?#/stacks/quickcreate?templateUrl=https://awsome-distributed-training.s3.amazonaws.com/templates/parallelcluster-prerequisites.yaml&stackName=parallelcluster-prerequisites) を開いて CloudFormation のクイック作成画面にアクセスします

2. パラメータを確認します（基本的にデフォルトのままで問題ありません）

   | パラメータ | デフォルト値 | 備考 |
   |---------|-----------|------|
   | Stack name | `parallelcluster-prerequisites` | そのまま |
   | Name of your VPC | `AWS ParallelCluster` | そのまま |
   | Availability Zone Name | 選択必須 | GPU の Capacity Reservation がある場合はその AZ を選択 |
   | Create S3 Endpoint | `true` | そのまま |
   | Capacity (FSx Lustre) | `1200` GiB | 増設可能 |
   | PerUnitStorageThroughput | `250` MB/s/TiB | 高速化する場合は変更 |
   | HomeCapacity (FSx OpenZFS) | `512` GiB | そのまま |

3. 画面下部の「スタックの作成」をクリックします。

:::message
デプロイには数分かかります。`CREATE_COMPLETE` になるまでの間、次の CloudShell セットアップを並行して進めてください。
:::

## CloudFormation テンプレートの解説

::::details テンプレート

```yaml
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

AWSTemplateFormatVersion: '2010-09-09'
Description: >
  This CloudFormation stack creates all the necessary pre-requisites for AWS ParallelCluster,
  these include VPC and two Subnets, a security group and FSx Lustre Filesystem.
  A public subnet and a private subnet are created in an Availability Zone that you provide as a parameter.
  As part of the template you'll deploy an Internet Gateway and NAT Gateway in
  the public subnet. In addition you deploy endpoints for Amazon S3. The VPC contains 2 CIDR blocks with 10.0.0.0/16 and 10.1.0.0/16
  The first CIDR is used for the public subnet, the second is used for the private.
  The template creates an fsx lustre volume in the specified AZ with a default of
  1.2 TB storage which can be overridden by parameter.


####################
## Stack Metadata ##
####################

Metadata:
  AWS::CloudFormation::Interface:
    ParameterGroups:
      - Label:
          default: General configuration
        Parameters:
          - VPCName
      - Label:
          default: Availability Zone configuration for the subnets
        Parameters:
          - PrimarySubnetAZ
      - Label:
          default: Fsx Lustre storage size
        Parameters:
          - Capacity
      - Label:
          default: Network and S3 endpoints configuration
        Parameters:
          - CreateS3Endpoint
    ParameterLabels:
      VPCName:
        default: Name of your VPC
      PrimarySubnetAZ:
        default: Availability zone id to deploy the primary subnets
      CreateS3Endpoint:
        default: Create an S3 endpoint

######################
## Stack Parameters ##
######################

Parameters:
  VPCName:
    Description: Name of your VPC
    Default: 'AWS ParallelCluster'
    Type: String

  PrimarySubnetAZ:
    Description: Availability zone id in which the public subnet and primary private subnet will be created.
    Type: AWS::EC2::AvailabilityZone::Name

  CreateS3Endpoint:
    AllowedValues:
      - 'true'
      - 'false'
    Default: 'true'
    Description:
      Set to false if to avoid creating an S3 endpoint on your VPC.
    Type: String

  Capacity:
    Description: Storage capacity in GiB (1200 or increments of 2400)
    Type: Number
    Default: 1200
  
  PerUnitStorageThroughput:
    Description: Provisioned Read/Write (MB/s/TiB)
    Type: Number
    Default: 250
    AllowedValues:
      - 125
      - 250
      - 500
      - 1000
  
  Compression:
    Description: Data compression type
    Type: String
    AllowedValues:
      - "LZ4"
      - "NONE"
    Default: "LZ4"
  
  LustreVersion:
    Description: Lustre software version
    Type: String
    AllowedValues:
      - "2.15"
      - "2.12"
    Default: "2.15"

  HomeCapacity:
    Description: "Home directories storage capacity in GiB"
    Type: Number
    Default: 512

  HomeThroughput:
    Description: "Home directories storage throughput MB/s"
    Type: Number
    Default: 320


###############################
## Conditions for Parameters ##
###############################

Conditions:
  S3EndpointCondition: !Equals [!Ref 'CreateS3Endpoint', 'true']

#########################
## VPC & Network Setup ##
#########################

Mappings:
  Networking:
    VPC:
      CIDR0: 10.0.0.0/16
      CIDR1: 10.1.0.0/16

Resources:
  # Create a VPC
  VPC:
    Type: AWS::EC2::VPC
    Properties:
      EnableDnsSupport: true
      EnableDnsHostnames: true
      CidrBlock: !FindInMap [Networking, VPC, CIDR0]
      Tags:
        - Key: Name
          Value: AWS ParallelCluster VPC

  VpcCidrBlock:
    Type: AWS::EC2::VPCCidrBlock
    DependsOn: VPC
    Properties:
      VpcId: !Ref VPC
      CidrBlock: !FindInMap [Networking, VPC, CIDR1]


  # Create an IGW and add it to the VPC
  InternetGateway:
    Type: AWS::EC2::InternetGateway

  GatewayToInternet:
    Type: AWS::EC2::VPCGatewayAttachment
    Properties:
      VpcId: !Ref VPC
      InternetGatewayId: !Ref InternetGateway

  # Create a NAT GW then add it to the public subnet
  NATGateway:
    Type: AWS::EC2::NatGateway
    Properties:
      AllocationId: !GetAtt ElasticIP.AllocationId
      SubnetId: !Ref PublicSubnet

  ElasticIP:
    Type: AWS::EC2::EIP
    Properties:
      Domain: vpc

  SecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: Allow EFA communication for Multi-Node Parallel Batch jobs
      VpcId: !Ref VPC
  EFASecurityGroupIngress:
    Type: AWS::EC2::SecurityGroupIngress
    Properties:
      Description: All to all communication for EFA Ingress within Security Group
      IpProtocol: -1
      FromPort: -1
      ToPort: -1
      GroupId: !Ref SecurityGroup
      SourceSecurityGroupId: !Ref SecurityGroup
  EFASecurityGroupEgress:
    Type: AWS::EC2::SecurityGroupEgress
    Properties:
      Description: All to all communication for EFA Egress  within Security Group
      IpProtocol: -1
      FromPort: -1
      ToPort: -1
      GroupId: !Ref SecurityGroup
      DestinationSecurityGroupId: !Ref SecurityGroup
  EFASecurityGroupEgressECS:
    Type: AWS::EC2::SecurityGroupEgress
    Properties:
      Description: All to all communication for Egress to all
      IpProtocol: -1
      FromPort: -1
      ToPort: -1
      GroupId: !Ref SecurityGroup
      CidrIp: 0.0.0.0/0

  # Build the public subnet
  PublicSubnet:
    Type: AWS::EC2::Subnet
    DependsOn: VPC
    Properties:
      MapPublicIpOnLaunch: true
      VpcId: !Ref VPC
      CidrBlock: !Select [ 0, !Cidr [ !GetAtt VPC.CidrBlock, 2, 15 ]]
      AvailabilityZone: !Ref PrimarySubnetAZ
      Tags:
        - Key: Name
          Value: !Join [ ' ', [ !Ref VPCName, 'Public Subnet -', !Ref PrimarySubnetAZ ] ]

  # Create the primary private subnet
  PrimaryPrivateSubnet:
    Type: AWS::EC2::Subnet
    DependsOn: [VpcCidrBlock]
    Properties:
      VpcId: !Ref VPC
      CidrBlock: !Select [ 0, !Cidr [ !FindInMap [Networking, VPC, CIDR1], 2, 15 ]]
      AvailabilityZone: !Ref PrimarySubnetAZ
      Tags:
        - Key: Name
          Value: !Join [ ' ', [ !Ref VPCName, 'Private Subnet -', !Ref PrimarySubnetAZ ] ]

  # Create and set the public route table
  PublicRouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref VPC

  PublicRoute:
    Type: AWS::EC2::Route
    Properties:
      RouteTableId: !Ref PublicRouteTable
      DestinationCidrBlock: 0.0.0.0/0
      GatewayId: !Ref InternetGateway

  # Then the private route table
  PrivateRouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref VPC

  PrivateRouteToInternet:
    Type: AWS::EC2::Route
    Properties:
      RouteTableId: !Ref PrivateRouteTable
      DestinationCidrBlock: 0.0.0.0/0
      NatGatewayId: !Ref NATGateway

  # Associate the public route table to the public subnet
  PublicSubnetRouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PublicSubnet
      RouteTableId: !Ref PublicRouteTable

  # and the primary private subnet to the private route table
  PrimaryPrivateSubnetRTAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PrimaryPrivateSubnet
      RouteTableId: !Ref PrivateRouteTable

  # S3 endpoint
  S3Endpoint:
    Condition: S3EndpointCondition
    Type: AWS::EC2::VPCEndpoint
    Properties:
      PolicyDocument:
        Version: 2012-10-17
        Statement:
            - Effect: Allow
              Principal: '*'
              Action:
                - '*'
              Resource:
                - '*'
      RouteTableIds:
        - !Ref PublicRouteTable
        - !Ref PrivateRouteTable
      ServiceName: !Join
        - ''
        - - com.amazonaws.
          - !Ref AWS::Region
          - .s3
      VpcId: !Ref VPC

  FSxLFilesystem:
    Type: AWS::FSx::FileSystem
    DeletionPolicy: Delete
    UpdateReplacePolicy: Delete
    Properties:
      FileSystemType: LUSTRE
      StorageType: SSD
      FileSystemTypeVersion: !Ref LustreVersion
      StorageCapacity: !Ref Capacity
      SecurityGroupIds:
        - !Ref SecurityGroup
      SubnetIds:
        - !Ref PrimaryPrivateSubnet
      LustreConfiguration:
        DataCompressionType: !Ref Compression
        DeploymentType: PERSISTENT_2
        PerUnitStorageThroughput: !Ref PerUnitStorageThroughput
        MetadataConfiguration:
          Mode: AUTOMATIC


  OpenZFSFileSystem:
    Type: AWS::FSx::FileSystem
    Properties:
      FileSystemType: OPENZFS
      OpenZFSConfiguration: 
        AutomaticBackupRetentionDays: 30
        CopyTagsToBackups: Yes
        CopyTagsToVolumes: Yes
        DailyAutomaticBackupStartTime: '19:00'
        DeploymentType: SINGLE_AZ_HA_2
        Options: 
          - DELETE_CHILD_VOLUMES_AND_SNAPSHOTS
        RootVolumeConfiguration: 
          DataCompressionType: NONE
          NfsExports: 
            - ClientConfigurations: 
              -  Clients: '*'
                 Options: 
                    - rw
                    - no_root_squash
                    - crossmnt
        ThroughputCapacity: !Ref HomeThroughput
        WeeklyMaintenanceStartTime: '1:04:00'
      SecurityGroupIds: 
        - !Ref SecurityGroup
      StorageCapacity: !Ref HomeCapacity
      StorageType: SSD
      SubnetIds: 
        - !Ref PrimaryPrivateSubnet

#############
## Outputs ##
#############
Outputs:
  VPC:
    Value: !Ref VPC
    Description: ID of the VPC
    Export:
      Name: !Sub ${AWS::StackName}-VPC
  PublicSubnet:
    Value: !Ref PublicSubnet
    Description: ID of the public subnet
    Export:
      Name: !Sub ${AWS::StackName}-PublicSubnet
  PrimaryPrivateSubnet:
    Value: !Ref PrimaryPrivateSubnet
    Description: ID of the primary private subnet
    Export:
      Name: !Sub ${AWS::StackName}-PrimaryPrivateSubnet
  SecurityGroup:
    Value: !Ref SecurityGroup
    Description: SecurityGroup for Batch
    Export:
      Name: !Sub ${AWS::StackName}-SecurityGroup
  FSxLustreFilesystemMountname:
    Description: The ID of the FSxL filesystem that has been created
    Value: !GetAtt FSxLFilesystem.LustreMountName
    Export:
      Name: !Sub ${AWS::StackName}-FSxLustreFilesystemMountname
  FSxLustreFilesystemDNSname:
    Description: The DNS of the FSxL filesystem that has been created
    Value: !GetAtt FSxLFilesystem.DNSName
    Export:
      Name: !Sub ${AWS::StackName}-FSxLustreFilesystemDNSname
  FSxLustreFilesystemId:
    Description: The ID of the FSxL filesystem that has been created
    Value: !Ref FSxLFilesystem
    Export:
      Name: !Sub ${AWS::StackName}-FSxLustreFilesystemId
  FSxORootVolumeId:
    Description: The ID of Fsx OpenZFS root volume
    Value: !GetAtt OpenZFSFileSystem.RootVolumeId
    Export:
      Name: !Sub ${AWS::StackName}-FSxORootVolumeId
```

::::

:::message alert
`PERSISTENT_2` はクラスターを停止・削除してもデータが保持され続けます。**不要になったら FSx コンソールから手動で削除してください。** 削除するまで課金が継続します。
:::

# 2. AWS CloudShell の起動

AWS CloudShell はブラウザ上で動作するシェル環境です。AWS CLI などのツールがプリインストールされており、IAM 認証情報が自動設定されます。

![CloudShell](/images/ml-parallelcluster-cloud-shell.png)

## 起動方法

コンソール上部の検索バーで「CloudShell」と入力するか、ナビゲーションバーの CloudShell アイコンをクリックします。

![CloudShell を探す](/images/ml-parallelcluster-cloud-shell-find.png)

CloudShell が起動すると以下のようなターミナルが表示されます。

![CloudShell ターミナル](/images/ml-parallelcluster-cloud-shell-terminal.png)

:::message
ワークショップを実施するリージョン（us-east-2 または us-west-2）で CloudShell を起動してください。コンソール右上のリージョン表示で確認できます。
:::

## 接続確認

```bash
aws sts get-caller-identity
```

```text
{
    "UserId": "AIDAXXXXXXXXXXXXXXXXX",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/your-username"
}
```

:::message alert
このワークショップでは AdministratorAccess 相当の IAM 権限が必要です。権限が不足している場合は、EC2・CloudFormation・FSx・VPC・SSM への権限付与を確認してください。
:::