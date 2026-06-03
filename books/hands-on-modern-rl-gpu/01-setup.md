---
title: "Chapter 1: 環境への接続手順"
---


g7e.2xlarge (RTX PRO 6000 Blackwell 96GB) 上で動く code-server に接続するための手順を、3 つの経路で説明します。

## 接続経路の選択

| 経路 | 用途 | 認証 |
|---|---|---|
| **A. CloudFront URL (ブラウザ)** | 通常の作業。ブラウザで code-server を使う | Cognito Hosted UI (メール + パスワード) |
| **B. SSM ポート転送 (CLI)** | ブラウザでローカル `localhost:8080` から code-server を使う | code-server password (16 文字) |
| **C. SSM Session Manager (シェル)** | ターミナル直接操作、デバッグ | IAM 認証 (AWS 認証情報があれば即接続) |

それぞれの認証情報は CloudFormation スタック `rl-gpu-ws` の Outputs と Cognito スタック `rl-gpu-ws-cognito` から取得できます。

---

## 共通: AWS 認証情報の準備

すべての経路で AWS 認証情報が必要です。プロファイルが設定されている場合は `--profile` を使ってください。

```bash
# プロファイル指定 (推奨)
export AWS_PROFILE=claude-code
export AWS_REGION=ap-northeast-1

# 確認
aws sts get-caller-identity
```

期待される出力:

```json
{
    "UserId": "AROA...",
    "Account": "<your-account-id>",
    "Arn": "arn:aws:sts::<your-account-id>:assumed-role/Admin/..."
}
```

---

## 経路 A: CloudFront URL (ブラウザでアクセス)

### A-1. CloudFront URL の取得

```bash
aws cloudformation describe-stacks --region ap-northeast-1 \
  --stack-name rl-gpu-ws-frontend \
  --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDomainName`].OutputValue' \
  --output text
```

期待される出力:

```
<your-distribution>.cloudfront.net
```

ブラウザで `https://<your-distribution>.cloudfront.net/` を開きます。

### A-2. Cognito Hosted UI でログイン

CloudFront にアクセスすると、自動的に Cognito の Hosted UI にリダイレクトされます。

| 項目 | 値 |
|---|---|
| メール | `admin@example.com` (deploy 時に `--operator-email` で指定したもの) |
| パスワード | deploy 時に `--operator-password` で指定したもの |

セッションログに残らないようパスワードはここには記載しない。忘れた場合は

```bash
# Cognito に新しい一時パスワードを再設定
aws cognito-idp admin-set-user-password --region ap-northeast-1 \
  --user-pool-id $(aws cloudformation describe-stacks --region ap-northeast-1 \
    --stack-name rl-gpu-ws-cognito \
    --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text) \
  --username admin@example.com --password '<NEW_PASSWORD>' --permanent
```

他のユーザーを追加する場合は `aws cognito-idp admin-create-user` を使う。

### A-3. code-server のログイン画面

Cognito 認証を通過すると、code-server (VS Code Web) のログイン画面が出ます。
パスワードは Secrets Manager に保存されているので、以下のコマンドで取得します:

```bash
SECRET_ARN=$(aws secretsmanager list-secrets --region ap-northeast-1 \
  --query "SecretList[?contains(Name, 'CodeServerPassword') && Tags[?Key=='aws:cloudformation:stack-name' && Value=='rl-gpu-ws']].ARN | [0]" \
  --output text)
aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --region ap-northeast-1 \
  --query 'SecretString' --output text
```

ログイン後、`/work` または `/home/coder` (どちらも EFS 上) に移動して作業します。

---

## 経路 B: SSM ポート転送 (ローカル `localhost:8080`)

ブラウザは使うが Cognito ログインを挟みたくない場合、SSM ポート転送で直接 code-server に繋げます。

### B-1. デプロイ時に提供された wrapper を使う方法 (推奨)

```bash
cd /path/to/investigations/hands-on-modern-rl-gpu/gpu-cdk
bash scripts/deploy.sh --port-forward \
  --stack-name rl-gpu-ws \
  -p 8080:80 \
  -r ap-northeast-1
```

ターミナルがフォアグラウンドで張り付くので、別タブでブラウザから `http://localhost:8080` を開きます。Ctrl+C で終了。

### B-2. AWS CLI 直叩きで SSM ポート転送

```bash
# まずインスタンス ID を取得
INSTANCE_ID=$(aws cloudformation describe-stacks --region ap-northeast-1 \
  --stack-name rl-gpu-ws \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text)
echo $INSTANCE_ID  # <your-instance-id> など

# ポート転送開始
aws ssm start-session \
  --target $INSTANCE_ID \
  --region ap-northeast-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["80"],"localPortNumber":["8080"]}'
```

ブラウザで `http://localhost:8080` を開く → code-server のパスワード入力 → 完了。

### B-3. 必要な前提

- `session-manager-plugin` がローカルにインストールされていること
  - macOS: `brew install --cask session-manager-plugin` または公式の `.pkg`
- インスタンスに `AmazonSSMManagedInstanceCore` ポリシーがアタッチされていること (CDK で済み)

---

## 経路 C: SSM Session Manager (ターミナル直接操作)

サーバーログを見たい、パッケージを手動でインストールしたい、などの用途。

```bash
INSTANCE_ID=$(aws cloudformation describe-stacks --region ap-northeast-1 \
  --stack-name rl-gpu-ws \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text)

aws ssm start-session --target $INSTANCE_ID --region ap-northeast-1
```

接続後、`coder` ユーザーで作業する場合:

```bash
sudo -i -u coder
cd /home/coder
```

ホームディレクトリは EFS 上にあるので、インスタンスを停止/再作成しても消えません:

```
/home/coder -> /mnt/efs/rl-gpu-ws/home-coder
/work -> /mnt/efs/rl-gpu-ws/work
```

---

## ディレクトリ構成

| パス | 実体 | 用途 |
|---|---|---|
| `/` | EBS 500 GB | OS、DLAMI、Python 環境 (`/opt/pytorch` 等) |
| `/mnt/local` | NVMe Instance Store 1.7 TB | 高速一時保存 (再起動で消える可能性あり) |
| `/mnt/efs` | EFS (`fs-09df33d270bbb483c`) | 永続データ |
| `/home/coder` | → `/mnt/efs/rl-gpu-ws/home-coder` | code-server ユーザーのホーム |
| `/work` | → `/mnt/efs/rl-gpu-ws/work` | 共有作業ディレクトリ |

**重要**: ソースコード、訓練済みモデル、データセットは `/home/coder` か `/work` に置いてください。`/` 上のファイルはインスタンス再作成で失われます。

---

## GPU 動作確認

接続後、GPU が見えているか確認:

```bash
# シェルから
nvidia-smi

# DLAMI 付属の PyTorch venv から
source /opt/pytorch/bin/activate
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

期待される出力:

```
True NVIDIA RTX PRO 6000 Blackwell Server Edition
```

詳しい確認は Task Runner の `tasks-gpu/00-verify-gpu.json` を実行してください (詳細は [Chapter 2: clone と章ごとの venv セットアップ](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/02-clone-venv))。

---

## トラブルシューティング

### 502 Bad Gateway が出る

- code-server サービスが落ちている可能性。SSM 接続して確認:
  ```bash
  systemctl status code-server@coder
  systemctl restart code-server@coder
  ```

### Cognito ログイン後にループする

- ブラウザの Cookie を全削除し、CloudFront URL から再アクセス
- それでも駄目なら CloudFront Distribution の伝播待ち (最大 15 分)

### SSM port forward が "TargetNotConnected"

- インスタンスの SSM agent が Online か確認:
  ```bash
  aws ssm describe-instance-information --region ap-northeast-1 \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus'
  ```
- 起動直後 1-2 分は agent が registering 状態のことがある

### ターゲットグループに登録されていない (ALB 502)

EC2 が CFN で replace された後、ALB のターゲットグループから外れることがあります:

```bash
# 登録状態を確認
aws elbv2 describe-target-health --region ap-northeast-1 \
  --target-group-arn arn:aws:elasticloadbalancing:ap-northeast-1:<your-account-id>:targetgroup/<your-tg> \
  --query 'TargetHealthDescriptions[*].[Target.Id,TargetHealth.State]' --output text

# 手動登録 (必要なら)
INSTANCE_ID=$(aws cloudformation describe-stacks --region ap-northeast-1 \
  --stack-name rl-gpu-ws \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' --output text)
aws elbv2 register-targets --region ap-northeast-1 \
  --target-group-arn arn:aws:elasticloadbalancing:ap-northeast-1:<your-account-id>:targetgroup/<your-tg> \
  --targets Id=$INSTANCE_ID,Port=80
```

---

## 次のステップ

接続できたら [Chapter 2: clone と章ごとの venv セットアップ](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/02-clone-venv) に進み、`hands-on-modern-rl` を EFS 上に clone してください。
