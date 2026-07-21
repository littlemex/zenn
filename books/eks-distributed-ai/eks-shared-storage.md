---
title: "Basic08 - 共有ストレージ (EFS と FSx for Lustre)"
free: true
---

本章では、Karpenter がノードを入れ替えても失われないデータ層として、EFS（マルチ AZ の RWX キャッシュ）と FSx for Lustre（単一 AZ の高スループット・スクラッチ）を構成します。

# 解説

## 全体構成

![EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち **EFS と FSx for Lustre** の 2 つの共有ストレージです。Karpenter が起動・削除する GPU/Neuron ノードの下に、ノードのライフサイクルとは独立して存在するデータ層を用意します。

## これは何をするものか

Karpenter は consolidate（アイドルノードの回収、Ch3 で見た `consolidateAfter` が該当）・drift（AMI 更新などの設定変更を検知した入れ替え）・expire（TTL 到達による入れ替え）でノードを次々に入れ替えます。この挙動自体はコスト最適化のために望ましいのですが、副作用として「Pod のローカルディスクに置いたデータはノードごと消える」という制約が生まれます。

具体的に困るのは次の 2 種類のデータです。

- **キャッシュ**: Hugging Face のモデルダウンロードキャッシュや、Neuron コンパイル済みの NEFF（Neuron Executable File Format）。これらは再生成可能ですが、再生成には数分から数十分かかります。ノードが入れ替わるたびに再コンパイルが走ると、実験のたびに待ち時間が積み重なります
- **学習データ・チェックポイント**: 大規模データセットの読み出しや、長時間ジョブの中間チェックポイント保存。こちらはスループットが要求されます

この 2 つの用途は特性が異なるため、本章では 2 種類の共有ストレージを使い分けます。

**EFS（`efs.tf`）** はマルチ AZ・ReadWriteMany（RWX）のファイルシステムです。private subnet ごとにマウントターゲットを配置するため、Capacity Block の GPU/Neuron ノードがどの AZ に居ても同じキャッシュをマウントできます。複数の推論・学習 Pod が同時に同じ HF キャッシュや NEFF を読みに来る RWX の要件にも合います。Pod Identity で `aws-efs-csi-driver` の Controller に IAM ロールを紐付け、EKS アドオンとして導入します。

**FSx for Lustre（`fsx.tf`）** は単一 AZ に固定された高スループット SSD のスクラッチ領域です。PERSISTENT_2 デプロイタイプを使い、既定では無効（`fsx_enabled = false`）になっています。単一 AZ である代わりに、EFS よりも高い読み書きスループットを持ち、大規模データセットの読み出しや学習チェックポイントの書き込みに向きます。

FSx には EFS と決定的に違う制約があります。**aws-fsx-csi-driver は既存のファイルシステムに対する動的プロビジョニング（StorageClass 経由での PVC バインド）に対応していません。** ドライバが読むのは新規にファイルシステムを作成するためのパラメータのみで、既存 FS の `fileSystemId` を StorageClass に渡しても無視されるか、意図しない 2 つ目の（多くの場合 TB 単位で課金される）ファイルシステムが暗黙に作られてしまいます。そのため、この構成では EFS と同じ static provisioning のパターンを踏襲し、Terraform が作成した 1 つの FSx ファイルシステムに対して固定の `volumeHandle` を持つ PersistentVolume（`fsx-training`）を 1 つだけ用意します。PVC 側はこの PV に名前でバインドします。

なお、KEDA（イベント駆動スケーリング）や Mountpoint for S3 は本章には含めません。これらはワークロード層の関心事であり、cluster-infra が提供する責務の外にあると判断しています。

以降で実際の Terraform コードを引用しながら、なぜその値・その書き方にしているのかを見ていきます。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/fix/eks-efa-verification-improvements/infra/eks) です。

## EFS（マルチ AZ RWX）

EFS は [`efs.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/efs.tf) で構成します。ファイルシステム本体はこれだけです。

```hcl
# efs.tf（抜粋）
resource "aws_efs_file_system" "shared" {
  count            = var.efs_enabled ? 1 : 0
  creation_token   = "${var.cluster_name}-shared"
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic" # scales with workload; no provisioned-throughput guesswork
  ...
}

resource "aws_efs_mount_target" "shared" {
  count           = var.efs_enabled ? length(module.vpc.private_subnets) : 0
  file_system_id  = aws_efs_file_system.shared[0].id
  subnet_id       = module.vpc.private_subnets[count.index]
  security_groups = [aws_security_group.efs[0].id]
}
```

読みどころは次の 4 点です。

**`throughput_mode = "elastic"`。** プロビジョンドスループットを事前に見積もる必要がなく、ワークロードの読み書き量に合わせて自動でスケールします。HF キャッシュや NEFF の読み出しパターンは Pod の起動タイミングに依存してバースト的なので、固定のプロビジョンドスループットより elastic の方が運用の手間が少ない選択です。

**private subnet ごとに 1 つのマウントターゲット。** `count = length(module.vpc.private_subnets)` で、Ch1 の VPC が持つプライベートサブネットすべてにマウントターゲットを配置します。これにより、Capacity Block の GPU/Neuron ノードがどの AZ に落ちても、同じ EFS を同じパスでマウントできます。FSx for Lustre が単一 AZ に固定される点との対比が、EFS を選ぶ決め手になります。

**アクセスポイントで POSIX 権限と root path を固定する。** `aws_efs_access_point.neuron_workspace` は `posix_user`（uid/gid 0）と `root_directory`（`/neuron-workspace`、`permissions 0755`）を持ち、コンテナが root で動く前提のワークスペースをファイルシステム内に切り出します。StorageClass の動的プロビジョニング（`provisioningMode = "efs-ap"`）はこのアクセスポイントの仕組みを使って PVC ごとに新しいディレクトリを掘りますが、本章の静的 PV はこのアクセスポイント 1 つを固定で指し続けます。

**CSI ドライバの削除タイミングは Karpenter のノード drain 待ちに従属する。** `aws_eks_addon.efs_csi_driver` には次のコメントが付いています。

```hcl
# Destroy ordering: null_resource.wait_for_node_drain (karpenter.tf) depends_on this
# addon, so it is removed only after the drain-wait completes. A Pod on a draining
# accelerator node may have an EFS-backed volume; removing the CSI driver first can stall
# that Pod's volume unmount, which stalls the drain the wait is trying to observe.
```

CSI ドライバを先に消してしまうと、drain 中の Pod が EFS ボリュームのアンマウントに失敗し、drain 自体が終わらなくなります。`terraform destroy` の順序をこのコメント 1 つで保証している、地味だが壊れやすい依存関係です。

Static Provisioning の PV（`efs_neuron_workspace_pv`）は `storageClassName = ""` にしている点も見逃せません。

```hcl
# efs.tf（抜粋）
resource "kubectl_manifest" "efs_neuron_workspace_pv" {
  ...
  spec = {
    ...
    # Empty storageClassName marks this a statically-provisioned PV: a PVC must bind by
    # volumeName, and the dynamic "efs-shared" StorageClass provisioner never acts on it.
    storageClassName = ""
    csi = {
      driver       = "efs.csi.aws.com"
      volumeHandle = "${aws_efs_file_system.shared[0].id}::${aws_efs_access_point.neuron_workspace[0].id}"
    }
  }
}
```

同じファイルシステムに対して動的プロビジョニング用の `efs-shared` StorageClass も定義していますが、この PV は空の `storageClassName` を持つため、PVC は名前（`volumeName`）で明示的にバインドしない限りこの PV には結びつきません。動的プロビジョナーがこの PV を横取りしてバインドし直す事故を防ぐための書き方です。

## FSx for Lustre と static provisioning 制約

FSx は [`fsx.tf`](https://github.com/littlemex/distributed-ai/blob/fix/eks-efa-verification-improvements/infra/eks/fsx.tf) で構成します。EFS との最大の違いは、**動的プロビジョニングの StorageClass が存在しない**ことです。ファイル冒頭のコメントにその理由が書かれています。

```hcl
# fsx.tf（冒頭コメント抜粋）
# Static provisioning only (mirrors efs.tf): Terraform creates ONE filesystem and a
# PersistentVolume with a fixed volumeHandle. There is no dynamic-provisioning StorageClass
# here — aws-fsx-csi-driver does not support binding a StorageClass to an EXISTING
# filesystem via a "fileSystemId" parameter (that key is not read by the driver; a PVC
# against such a StorageClass would either error or silently provision an unwanted second
# multi-TB filesystem).
```

`aws-fsx-csi-driver` の StorageClass は「新規にファイルシステムを作る」パラメータしか読めず、既存 FS の `fileSystemId` を渡しても無視されます。最悪の場合、意図せず 2 つ目の（TB 単位で課金される）ファイルシステムが暗黙に作られます。そのため EFS と同じ static provisioning のパターンを踏襲し、Terraform が作成した 1 つの FSx に対して固定の `volumeHandle` を持つ PV を 1 つだけ用意します。

読みどころは次の 3 点です。

**セキュリティグループは自己参照ルールと双方向ルールの両方が必要。** Lustre の LNET トラフィックはステートフルな SG の前提（往路を許可すれば戻りは自動で通る）に乗らず、AWS のドキュメントは SG ID ベースでの双方向ルールを明示的に要求します。`fsx.tf` は FSx 側 SG に自己参照ルール（`referenced_security_group_id = aws_security_group.fsx[0].id`）を 988 番と 1018-1023 番の両方に張り、さらに EKS ノード SG との間でも双方向にルールを張っています。

```hcl
# fsx.tf（抜粋）
resource "aws_vpc_security_group_ingress_rule" "fsx_self_988" {
  count                        = var.fsx_enabled ? 1 : 0
  security_group_id            = aws_security_group.fsx[0].id
  from_port                    = 988
  to_port                      = 988
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.fsx[0].id
}
```

この自己参照ルールを忘れると、`CreateFileSystem` が `InvalidNetworkSettings`（"do not permit Lustre LNET network traffic on port 988"）で失敗します。SG ルール自体は正しくても、FSx のネットワーク検証サービスへの**伝搬に時間がかかる**ため、`fsx.tf` は SG ルール作成後に `time_sleep.fsx_sg_propagation`（30 秒）を挟んでからファイルシステムを作成しています。

```hcl
# fsx.tf（抜粋）
resource "time_sleep" "fsx_sg_propagation" {
  count           = var.fsx_enabled ? 1 : 0
  create_duration = "30s"
  depends_on = [
    aws_vpc_security_group_ingress_rule.fsx_self_988,
    ...
  ]
}
```

コメントには「初回 apply が失敗し、再 apply では成功する」という実際の再現内容が記録されています。`depends_on` だけではAPI呼び出しの順序しか保証されず、SG ルールが検証サービスに伝搬し終わるまでは待ってくれないため、この `time_sleep` が初回 apply を決定的にしています。

**静的 PV の `volumeAttributes` はキーが小文字必須。** aws-fsx-csi-driver は `dnsname` と `mountname` という小文字キーしか読みません。

```hcl
# fsx.tf（抜粋）
volumeAttributes = {
  dnsname   = aws_fsx_lustre_file_system.training[0].dns_name
  mountname = aws_fsx_lustre_file_system.training[0].mount_name
}
```

キャメルケース（`dnsName`）で書いてしまうとドライバに黙って無視され、`NodeStageVolume` が "dnsname is not provided" で失敗し Pod が `ContainerCreating` のまま止まります。`volumeHandle` はあくまで Kubernetes 側の識別子で、マウント時に AWS API を呼んで解決されるわけではないため、`dnsname`/`mountname` を PV 側に自分で埋め込む必要がある、という構造上の理由です。

**IAM は `fsx:DescribeFileSystems` のみで足りる。** static provisioning では `CreateFileSystem`/`DeleteFileSystem`/`UpdateFileSystem` の権限は不要です。これらは動的プロビジョニングの専用コードパスで、固定 `volumeHandle` の PV では一度も呼ばれません。FSx は ARN スコープのリソース権限をサポートしないため `Resource = "*"` になりますが、許可するアクションそのものを 1 つに絞ることで影響範囲を抑えています。

`variables.tf` のバリデーションも、この構成の落とし穴をいくつか plan 時に潰しています。

```hcl
# variables.tf（抜粋）
variable "fsx_storage_capacity_gib" {
  ...
  validation {
    condition     = var.fsx_storage_capacity_gib == 1200 || (var.fsx_storage_capacity_gib >= 2400 && var.fsx_storage_capacity_gib % 2400 == 0)
    error_message = "fsx_storage_capacity_gib must be 1200, 2400, or a multiple of 2400 (PERSISTENT_2 SSD tier sizes)."
  }
}
```

PERSISTENT_2 SSD の容量は 1,200 GiB か 2,400 GiB の倍数でしか指定できないという FSx API の制約を、`terraform plan` の段階でエラーにします。これがないと、中間半端な値（例えば 3,000）を指定した場合の失敗が `CreateFileSystem` の API エラーとして apply の途中まで進んでから返ってきてしまいます。同様に `fsx_subnet_index` にも範囲チェックのバリデーションがあり、`module.vpc.private_subnets` の添字が範囲外になる前に弾かれます。

## 全体の中での位置付け

本章は、Ch1〜Ch3 で作った VPC・EKS コントロールプレーン・アクセラレータノードの土台の上に、ノードのライフサイクルから独立したデータ層を積む章です。EFS と FSx はいずれも Terraform で 1 度作成すれば、その後の Karpenter によるノード入れ替えの影響を受けません。以降の章で GPU/Neuron ワークロードが HF キャッシュや NEFF、チェックポイントを読み書きする際の土台になります。

## 注意

**`fsx_subnet_index` とアクセラレータプールの `zone` の不一致に注意します。** FSx for Lustre は単一 AZ にしか存在せず、別 AZ からのマウントは動作こそしますが、AZ 間データ転送コストとレイテンシが発生します。`fsx_subnet_index` は、実際に FSx を使うアクセラレータプールの `zone`（Ch3 参照）と揃えておきます。

**`prevent_destroy` は意図的に未設定です。** この構成は再現性を優先した使い捨て環境として設計されており、`terraform destroy` を実行すると FSx ファイルシステムとその中のデータがそのまま削除されます。NEFF や HF キャッシュのような再生成可能なデータであれば問題ありませんが、チェックポイントなど失うと困るデータを長期間保持するクラスタでは `prevent_destroy = true` を設定すべきです。

**FSx のサイズは 1,200 GiB か、2,400 GiB の倍数でしか指定できません。** PERSISTENT_2 SSD のストレージ容量は API レベルでこの制約を持ちます。`fsx_storage_capacity_gib` に中間半端な値（例えば 3,000）を設定すると、Terraform の変数バリデーションで即座に弾かれます。

**FSx は有効な間、容量分の課金が常時発生します。** PERSISTENT_2 SSD は使用量ではなくプロビジョニングした容量に対して課金され続けるため、常時起動しておくコストは小さくありません。学習ジョブを実行する期間だけ `fsx_enabled = true` にして apply し、終わったら `false` に戻して destroy する運用が推奨されます。

**Hugging Face からのダウンロードで 429（Too Many Requests）が出る場合があります。** 多数の Pod が同時に同じモデルを HF Hub から直接ダウンロードすると、レート制限に当たりやすくなります。対策として、事前に 1 つの Pod で EFS 上にモデルをステージングしておき、各推論 Pod はそのローカルキャッシュを読むようにします。また `HF_HUB_DISABLE_XET=1` を設定して Xet 経由の転送を無効化すると、この種のエラーが解消するケースがあります。

# ワークショップ実施

## 1. Terraform の出力を確認する

```bash
cd infra/eks
terraform output
```

`efs_enabled = true` は既定値なので、初回 apply 時点で EFS ファイルシステムはすでに作られています。FSx は既定で無効なので、有効にする場合は `terraform.tfvars` に `fsx_enabled = true` を追加してから `terraform apply` します。

## 2. PersistentVolume と PVC を確認する

```bash
kubectl get pv
```

実機出力:
```
NAME                   CAPACITY   ACCESS MODES   RECLAIM POLICY   STATUS   CLAIM                          AGE
efs-neuron-workspace   1000Gi     RWX            Retain           Bound    smollm-test/efs-shared-claim   3d
fsx-training           2400Gi     RWX            Retain           Bound    default/fsx-claim              3d
```

EFS（`efs-neuron-workspace`）と FSx（`fsx-training`）が共に `Retain` + `RWX` で `Bound` になっています。

```bash
kubectl get pvc -A
```

```
NAMESPACE     NAME               STATUS   VOLUME                 CAPACITY   ACCESS MODES   AGE
default       fsx-claim          Bound    fsx-training           2400Gi     RWX            3d
smollm-test   efs-shared-claim   Bound    efs-neuron-workspace   1000Gi     RWX            3d
```

どちらも `storageClassName` が空の static PV であり、動的プロビジョニングの StorageClass（`efs-shared`）とは別物である点に注意します。FSx が `RWX` なのは、複数ノードの Pod から同時にチェックポイント書き込みやデータ読み出しができるようにするためです。

## 3. EFS 用の PVC を作成し、書き込みテストを行う

PV は Terraform で作られていますが、PVC（Pod がマウントに使う参照）は手動で作る必要があります。

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: efs-claim
spec:
  accessModes: ["ReadWriteMany"]
  storageClassName: ""
  volumeName: efs-neuron-workspace
  resources:
    requests:
      storage: 1000Gi
EOF
```

PVC が `Bound` になったことを確認します。

```bash
kubectl get pvc efs-claim
```

EFS にファイルを書き込むテスト Pod を実行します。

```bash
kubectl run efs-test --restart=Never \
    --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"efs-test","image":"busybox","command":["sh","-c","echo hello > /mnt/efs/test.txt && cat /mnt/efs/test.txt"],"volumeMounts":[{"name":"efs","mountPath":"/mnt/efs"}]}],"volumes":[{"name":"efs","persistentVolumeClaim":{"claimName":"efs-claim"}}]}}'
```

Pod のログに `hello` が出力されれば、EFS のマウントと書き込みが成功しています。

```bash
kubectl logs efs-test
```

## 4. Pod を削除して再作成し、データが残ることを確認する

```bash
kubectl delete pod efs-test
kubectl run efs-test2 --restart=Never \
    --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"efs-test2","image":"busybox","command":["cat","/mnt/efs/test.txt"],"volumeMounts":[{"name":"efs","mountPath":"/mnt/efs"}]}],"volumes":[{"name":"efs","persistentVolumeClaim":{"claimName":"efs-claim"}}]}}'
kubectl logs efs-test2
```

別名の Pod でも `hello` が読み出せます。これが、Karpenter がノードを入れ替えても Pod が再スケジュールされた先で同じキャッシュを読み続けられる、という本章の要点そのものです。

# まとめ

本章では、Karpenter によるノード入れ替えから独立したデータ層として EFS と FSx for Lustre を構成しました。EFS はマルチ AZ の RWX キャッシュとして HF キャッシュや NEFF に、FSx は単一 AZ の高スループット・スクラッチとして大規模データセットやチェックポイントに向きます。FSx は動的プロビジョニングに対応しないため static provisioning を用いる点、`fsx_subnet_index` とアクセラレータプールの `zone` を揃える点、`prevent_destroy` が未設定である点の 3 つを押さえておけば、以降の章で GPU/Neuron ワークロードがこの共有ストレージを安心して利用できます。

# 参考資料

- [Amazon EFS CSI Driver](https://github.com/kubernetes-sigs/aws-efs-csi-driver)
- [Amazon FSx for Lustre CSI Driver](https://github.com/kubernetes-sigs/aws-fsx-csi-driver)
- [Amazon FSx for Lustre ユーザーガイド](https://docs.aws.amazon.com/fsx/latest/LustreGuide/what-is.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
