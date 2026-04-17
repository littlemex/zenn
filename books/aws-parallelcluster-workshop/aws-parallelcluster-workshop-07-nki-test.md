---
title: "Advanced 01: NKI Test"
free: true
---

# 解説

本章では、構築した Trainium2 クラスターで NKI（Neuron Kernel Interface）0.3.0 が正常に動作することを確認します。

## NKI とは

NKI は、AWS Neuron チップのハードウェアリソースに直接アクセスできる低レベル API です。

:::message
NKI は OpenAI Triton の抽象度、Cuda カーネルレベル、のどちらの書き方もできます。
:::

| フレームワーク | 抽象度 | 用途 |
|--------------|--------|------|
| PyTorch/JAX | 高 | 一般的な ML モデルの実装 |
| XLA/Neuron Compiler | 中 | 自動最適化 |
| **NKI** | 低 | カスタムカーネルの手動最適化 |

NKI を使用すると、メモリ階層（SBUF/PSUM/HBM）の明示的制御、演算エンジンの直接利用、カーネル融合によるメモリアクセス削減などが可能になります。

# ワークショップ実施

## 作業ディレクトリの作成

作業ディレクトリを作成します（HeadNode 上で実行）。

```bash
# PATH を設定
export PATH=/opt/slurm/bin:$PATH

# 作業ディレクトリ作成
mkdir -p ~/nki-test && cd ~/nki-test
```

## NKI テストスクリプトのダウンロード

HeadNode 上で GitHub から直接スクリプトをダウンロードします。

```bash
# NKI テストスクリプトをダウンロード
wget https://raw.githubusercontent.com/littlemex/samples/main/aws-neuron/nki-test/test_nki.py
wget https://raw.githubusercontent.com/littlemex/samples/main/aws-neuron/nki-test/run_nki_test_with_setup.sh
chmod +x run_nki_test_with_setup.sh
```

では実際にバッチジョブを投入して結果を確認してみましょう。すでにクラスター作成時の設定で Compute ノードには自動的に Neuron SDK 等がインストールされているはずですが、なんらかの理由で存在しない場合はセットアップした上で NKI のバージョンを確認する簡単なジョブです。

```bash
$ sbatch run_nki_test_with_setup.sbatch 
Submitted batch job 4
ubuntu@ip-10-0-0-186:~/nki-test$ tail -f logs/nki_test_4.out 
[OK] NKI version: 0.3.0+23928721754.g18aa1271
[INFO] Running NKI test
[INFO] Starting NKI import test...
[OK] NKI imported successfully
[OK] NKI version: 0.3.0+23928721754.g18aa1271
[OK] NKI language module imported successfully
[OK] PyTorch imported successfully
[OK] PyTorch version: 2.6.0+cu124
[OK] All imports successful - NKI 0.3.0 is operational
[INFO] NKI test job completed
```

## まとめ

本章では、Slurm ジョブとして Neuron SDK 2.29.0 を自動インストールし、NKI 0.3.0 が正常に動作することを確認しました。

Advanced 01 ワークショップを通じて、以下を学習しました。

**Getting Started**: ML Capacity Block 管理スクリプトを使用して、sa-east-1 リージョンで trn2.3xlarge を予約しました。

**Create Cluster**: ML Capacity Block を使用する ParallelCluster の設定で、**`CapacityType: CAPACITY_BLOCK` を queue-level に指定する必要がある**という重要なポイントを学びました。

**NKI Test**: Slurm ジョブで Neuron SDK を自動インストールする方法を学び、ML Capacity Block から起動した trn2.3xlarge 上で NKI 0.3.0 が正常に動作することを確認しました。

:::message alert
これで AWS Neuron、NKI を好きにいじって遊べる環境が整いました！Let's try NKI!
:::

## クリーンアップ

ワークショップ終了後、必要に応じてリソースを削除しておきましょう。

```bash
# クラスター削除
pcluster delete-cluster \
  --cluster-name ml-cluster \
  --region sa-east-1
```

インフラストラクチャの CloudFormation も削除しておきましょう。