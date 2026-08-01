---
title: "Basic10 - 共有ストレージ (Amazon EFS と Amazon FSx for Lustre)"
free: true
---

本章では、Karpenter がノードを入れ替えても失われないデータ層として、Amazon EFS（マルチ AZ の RWX キャッシュ）と Amazon FSx for Lustre（単一 AZ の高スループット・スクラッチ）を構成します。

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち **Amazon EFS と Amazon FSx for Lustre** の 2 つの共有ストレージです。図には Amazon FSx for OpenZFS も含まれていますが、これは実装（`infra/eks`）の既定構成で Amazon FSx for Lustre と対になる home・共有領域であり、詳しい解説は本章の対象外とします（ワークショップ手順では実機出力に登場します）。Karpenter が起動・削除する GPU/Neuron ノードの下に、ノードのライフサイクルとは独立して存在するデータ層を用意します。

## これは何をするものか

Karpenter は consolidate（アイドルノードの回収、Basic04 で見た `consolidateAfter` が該当）・drift（AMI 更新などの設定変更を検知した入れ替え）・expire（NodeClaim テンプレート側の `expireAfter` で TTL に達した入れ替え）でノードを次々に入れ替えます。この挙動自体はコスト最適化のために望ましいのですが、副作用として「Pod のローカルディスクに置いたデータはノードごと消える」という制約が生まれます。

具体的に困るのは次の 2 種類のデータです。

- **キャッシュ**: Hugging Face のモデルダウンロードキャッシュや、Neuron コンパイル済みの NEFF（Neuron Executable File Format）。これらは再生成可能ですが、再生成には数分から数十分かかります。ノードが入れ替わるたびに再コンパイルが走ると、実験のたびに待ち時間が積み重なります
- **学習データ・チェックポイント**: 大規模データセットの読み出しや、長時間ジョブの中間チェックポイント保存。こちらはスループットが要求されます

この 2 つの用途に対して、実装（`infra/eks`）は次の設計判断をしています。**既定で有効な共有ストレージは、Amazon FSx for Lustre（高スループット・スクラッチ、`fsx_enabled = true`）と Amazon FSx for OpenZFS（NFS ベースの home・共有領域、`openzfs_enabled = true`）という単一 AZ の 2 層**です。この 2 層はどのアクセラレータプールも 1 つの AZ に固定される（EFA/RDMA が AZ 内通信であり、Capacity Block も単一 AZ のため）前提と揃っており、awsome-distributed-ai の構成を踏襲しています。**Amazon EFS（`efs.tf`）はこの既定構成から降格された opt-in レイヤー**（`efs_enabled = false` が既定）で、マルチ AZ・RWX が必要な場合、具体的には Karpenter がノードを別 AZ に入れ替えても同じ HF キャッシュ・NEFF を読み続けたい場合にだけ有効化します。本章では、この 3 つの共有ストレージのうち **Amazon EFS と Amazon FSx for Lustre** を代表として解説します。

**Amazon EFS（`efs.tf`）** はマルチ AZ・ReadWriteMany（RWX）のファイルシステムです。private subnet ごとにマウントターゲットを配置するため、Capacity Block の GPU/Neuron ノードがどの AZ に居ても同じキャッシュをマウントできます。複数の推論・学習 Pod が同時に同じ HF キャッシュや NEFF を読みに来る RWX の要件にも合います。Pod Identity で `aws-efs-csi-driver` の Controller に IAM ロールを紐付け、Amazon EKS アドオンとして導入しますが、このアドオン自体は `efs_enabled` の値に関わらず常設され、ファイルシステム本体・マウントターゲット・アクセスポイント・static PV だけが `efs_enabled = true` のときに作られます。

**Amazon FSx for Lustre** は単一 AZ に固定された高スループット SSD のスクラッチ領域です（`fsx.tf`）。PERSISTENT_2 デプロイタイプを使い、既定で有効（`fsx_enabled = true`）です。単一 AZ である代わりに、Amazon EFS よりも高い読み書きスループットを持ち、大規模データセットの読み出しや学習チェックポイントの書き込みに向きます。

Amazon FSx for Lustre には Amazon EFS と決定的に違う制約があります。**aws-fsx-csi-driver は既存のファイルシステムに対する動的プロビジョニング（StorageClass 経由での PVC バインド）に対応していません。** ドライバが読むのは新規にファイルシステムを作成するためのパラメータのみで、既存 FS の `fileSystemId` を StorageClass に渡しても無視されるか、意図しない 2 つ目の（多くの場合 TB 単位で課金される）ファイルシステムが暗黙に作られてしまいます。そのため、この構成では Amazon EFS と同じ static provisioning のパターンを踏襲し、Terraform が作成した 1 つの Amazon FSx for Lustre ファイルシステムに対して固定の `volumeHandle` を持つ PersistentVolume（`fsx-training`）を 1 つだけ用意します。PVC 側はこの PV に名前でバインドします。Amazon FSx for OpenZFS（`openzfs.tf`）も同じ static provisioning パターンで、コード・設定・データセットのような小さいファイルが多い home・共有領域を担います。CSI ドライバが Amazon EKS アドオンとして提供されていないため、`aws-fsx-openzfs-csi-driver` は Helm リリースと Pod Identity のひも付けで導入している点が Amazon EFS・Amazon FSx for Lustre とは異なります。

なお、Mountpoint for Amazon S3 は本章には含めません。ワークロード層の関心事であり、cluster-infra が提供する責務の外にあると判断しています。

以降で実際の Terraform コードを引用しながら、なぜその値・その書き方にしているのかを見ていきます。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) です。

## Amazon EFS（マルチ AZ RWX）

Amazon EFS は [`efs.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/efs.tf) で構成します。ファイルシステム本体はこれだけです。

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

**`throughput_mode = "elastic"` を選んでいます。** プロビジョンドスループットを事前に見積もる必要がなく、ワークロードの読み書き量に合わせて自動でスケールします。HF キャッシュや NEFF の読み出しパターンは Pod の起動タイミングに依存してバースト的なので、固定のプロビジョンドスループットより elastic の方が運用の手間が少ない選択です。

**private subnet ごとに 1 つのマウントターゲットを置きます。** `count = length(module.vpc.private_subnets)` で、Basic01 の Amazon VPC が持つプライベートサブネットすべてにマウントターゲットを配置します。これにより、Capacity Block の予約 AZ が先頭 AZ と異なる場合や将来 AZ 構成を変えた場合でも、どの AZ のノードからも同じ Amazon EFS を同じパスでマウントできます。Amazon FSx for Lustre が単一 AZ に固定される点との対比が、マルチ AZ が要る用途で Amazon EFS を選ぶ決め手になります。

**アクセスポイントで POSIX 権限と root path を固定します。** `aws_efs_access_point.neuron_workspace` は `posix_user`（uid/gid 0）と `root_directory`（`/neuron-workspace`、`permissions 0755`）を持ち、コンテナが root で動く前提のワークスペースをファイルシステム内に切り出します。StorageClass の動的プロビジョニング（`provisioningMode = "efs-ap"`）は、この `neuron_workspace` アクセスポイントそのものを使い回すのではなく、同じ「アクセスポイント」という機能を PVC ごとに新規作成して使います。本章の静的 PV は、Terraform が作った `neuron_workspace` アクセスポイント 1 つを固定で指し続けます。

**CSI ドライバの削除タイミングは Karpenter のノード drain 待ちに従属します。** `aws_eks_addon.efs_csi_driver` には次のコメントが付いています。

```hcl
# Destroy ordering: null_resource.wait_for_node_drain (karpenter.tf) depends_on this
# addon, so it is removed only after the drain-wait completes. A Pod on a draining
# accelerator node may have an EFS-backed volume; removing the CSI driver first can stall
# that Pod's volume unmount, which stalls the drain the wait is trying to observe.
```

CSI ドライバを先に消してしまうと、drain 中の Pod が Amazon EFS ボリュームのアンマウントに失敗し、drain 自体が終わらなくなります。`terraform destroy` の順序をこのコメント 1 つで保証している、地味ですが壊れやすい依存関係です。

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

## Amazon FSx for Lustre と static provisioning 制約

Amazon FSx for Lustre は [`fsx.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/fsx.tf) で構成します。Amazon EFS との最大の違いは、**動的プロビジョニングの StorageClass が存在しない**ことです。ファイル冒頭のコメントにその理由が書かれています。

```hcl
# fsx.tf（冒頭コメント抜粋）
# Static provisioning only (mirrors efs.tf): Terraform creates ONE filesystem and a
# PersistentVolume with a fixed volumeHandle. There is no dynamic-provisioning StorageClass
# here — aws-fsx-csi-driver does not support binding a StorageClass to an EXISTING
# filesystem via a "fileSystemId" parameter (that key is not read by the driver; a PVC
# against such a StorageClass would either error or silently provision an unwanted second
# multi-TB filesystem).
```

`aws-fsx-csi-driver` の StorageClass は「新規にファイルシステムを作る」パラメータしか読めず、既存 FS の `fileSystemId` を渡しても無視されます。最悪の場合、意図せず 2 つ目の（TB 単位で課金される）ファイルシステムが暗黙に作られます。そのため Amazon EFS と同じ static provisioning のパターンを踏襲し、Terraform が作成した 1 つの Amazon FSx for Lustre に対して固定の `volumeHandle` を持つ PV を 1 つだけ用意します。

読みどころは次の 3 点です。

**セキュリティグループには自己参照ルールと双方向ルールの両方が必要です。** Lustre の LNET はクライアント・サーバの双方から接続が開始されるため、往路さえ許可すれば戻りは自動で通る通常のパターンでは足りず、AWS のドキュメントは SG ID ベースの双方向ルールを明示的に要求します。`fsx.tf` は Amazon FSx for Lustre 側 SG に自己参照ルール（`referenced_security_group_id = aws_security_group.fsx[0].id`）を 988 番と 1018-1023 番の両方に張り、さらに Amazon EKS ノード SG との間でも双方向にルールを張っています。ポート番号は `locals` にまとめており、これは 6 つの SG ルールでポート番号のリテラルを重複させないための書き方です。いずれか 1 つでポート番号を書き間違えると、`CreateFileSystem` が検証する双方向の要件が静かに崩れてしまいます。

```hcl
# fsx.tf（抜粋）
locals {
  fsx_lustre_port           = 988
  fsx_lustre_high_port_from = 1018
  fsx_lustre_high_port_to   = 1023
}

resource "aws_vpc_security_group_ingress_rule" "fsx_self_988" {
  count                        = var.fsx_enabled ? 1 : 0
  security_group_id            = aws_security_group.fsx[0].id
  description                  = "Lustre port 988 within the FSx security group (self-referencing)"
  from_port                    = local.fsx_lustre_port
  to_port                      = local.fsx_lustre_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.fsx[0].id
}
```

この自己参照ルールを忘れると、`CreateFileSystem` が `InvalidNetworkSettings`（"do not permit Lustre LNET network traffic on port 988"）で失敗します。SG ルール自体は正しくても、Amazon FSx for Lustre のネットワーク検証サービスへの**伝搬に時間がかかる**ため、`fsx.tf` は SG ルール作成後に `time_sleep.fsx_sg_propagation`（30 秒）を挟んでからファイルシステムを作成しています。

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

**静的 PV の `volumeAttributes` はキーが小文字でないと読まれません。** aws-fsx-csi-driver は `dnsname` と `mountname` という小文字キーしか読みません。

```hcl
# fsx.tf（抜粋）
volumeAttributes = {
  dnsname   = aws_fsx_lustre_file_system.training[0].dns_name
  mountname = aws_fsx_lustre_file_system.training[0].mount_name
}
```

キャメルケース（`dnsName`）で書いてしまうとドライバに黙って無視され、`NodeStageVolume` が "dnsname is not provided" で失敗し Pod が `ContainerCreating` のまま止まります。`volumeHandle` はあくまで Kubernetes 側の識別子で、マウント時に AWS API を呼んで解決されるわけではないため、`dnsname`/`mountname` を PV 側に自分で埋め込む必要がある、という構造上の理由です。

**IAM は `fsx:DescribeFileSystems` のみで足ります。** static provisioning では `CreateFileSystem`/`DeleteFileSystem`/`UpdateFileSystem` の権限は不要です。これらは動的プロビジョニングの専用コードパスで、固定 `volumeHandle` の PV では一度も呼ばれません。Amazon FSx for Lustre は ARN スコープのリソース権限をサポートしないため `Resource = "*"` になりますが、許可するアクションそのものを 1 つに絞ることで影響範囲を抑えています。

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

PERSISTENT_2 SSD の容量は 1,200 GiB か 2,400 GiB の倍数でしか指定できないという Amazon FSx for Lustre API の制約を、`terraform plan` の段階でエラーにします。これがないと、中途半端な値（例えば 3,000）を指定した場合の失敗が `CreateFileSystem` の API エラーとして apply の途中まで進んでから返ってきてしまいます。`fsx_subnet_index` にも負値を弾くバリデーションが `variables.tf` にあり、さらに `az.tf` の precondition で「解決済みの AZ 数より小さいか」という上限チェックが加わります。AZ 数はデータソースから解決されるため `variable` のバリデーションブロックでは表現できず、この上限チェックだけ `az.tf` 側に置かれています。

## 全体の中での位置付け

本章は、Basic01〜Basic04 で作った Amazon VPC・Amazon EKS コントロールプレーン・アクセラレータノードの土台の上に、ノードのライフサイクルから独立したデータ層を積む章です。Amazon EFS と Amazon FSx for Lustre はいずれも Terraform で 1 度作成すれば、その後の Karpenter によるノード入れ替えの影響を受けません。以降の章で GPU/Neuron ワークロードが HF キャッシュや NEFF、チェックポイントを読み書きする際の土台になります。

## 注意

**`fsx_subnet_index` とアクセラレータプールの `zone` の不一致に注意します。** Amazon FSx for Lustre は単一 AZ にしか存在せず、別 AZ からのマウントは動作こそしますが、AZ 間データ転送コストとレイテンシが発生します。`fsx_subnet_index` は、実際に Amazon FSx for Lustre を使うアクセラレータプールの `zone`（Basic04 参照）と揃えておきます。

**`prevent_destroy` は意図的に未設定です。** この構成は再現性を優先した使い捨て環境として設計されており、`terraform destroy` を実行すると Amazon FSx for Lustre ファイルシステムとその中のデータがそのまま削除されます。NEFF や HF キャッシュのような再生成可能なデータであれば問題ありませんが、チェックポイントなど失うと困るデータを長期間保持するクラスタでは `prevent_destroy = true` を設定すべきです。

**Amazon FSx for Lustre のサイズは 1,200 GiB か、2,400 GiB の倍数でしか指定できません。** PERSISTENT_2 SSD のストレージ容量は API レベルでこの制約を持ちます。`fsx_storage_capacity_gib` に中途半端な値（例えば 3,000）を設定すると、Terraform の変数バリデーションで即座に弾かれます。

**Amazon FSx for Lustre は有効な間、容量分の課金が常時発生します。** PERSISTENT_2 SSD は使用量ではなくプロビジョニングした容量に対して課金され続けるため、常時起動しておくコストは小さくありません。学習ジョブを実行する期間だけ `fsx_enabled = true` にして apply し、終わったら `false` に戻して destroy する運用が推奨されます。ただし `fsx_enabled = false` にすると、そのファイルシステム上のチェックポイントを含むすべてのデータが `terraform destroy`相当の処理で削除されます。**無効化の前に、必要なチェックポイントを Amazon S3 など別の場所へ退避してください。** また、ワークショップ手順で手動作成した `fsx-claim` などの PVC やそれを使う Pod が残っていると、Bound な PV の削除がファイナライザで止まり Terraform の apply が詰まることがあります。無効化の前に、手動で作成した PVC・Pod を先に削除してください。

**Hugging Face からのダウンロードで 429（Too Many Requests）が出る場合があります。** 多数の Pod が同時に同じモデルを HF Hub から直接ダウンロードすると、レート制限に当たりやすくなります。対策として、事前に 1 つの Pod で Amazon EFS 上にモデルをステージングしておき、各推論 Pod はそのローカルキャッシュを読むようにします。また `HF_HUB_DISABLE_XET=1` を設定して Xet 経由の転送を無効化すると、この種のエラーが解消するケースがあります。

# ワークショップ実施

## 1. Terraform の出力を確認する

```bash
cd infra/eks
terraform output
```

初回 apply の時点で、既定で有効な Amazon FSx for Lustre（`fsx_enabled = true`）と Amazon FSx for OpenZFS（`openzfs_enabled = true`）のファイルシステムはすでに作られています。Amazon EFS は既定で無効（`efs_enabled = false`、CSI ドライバだけは常設）なので、使う場合は `terraform.tfvars` に `efs_enabled = true` を設定してから `terraform apply` します。本章では代表として Amazon EFS と Amazon FSx for Lustre を扱います。

## 2. PersistentVolume と PVC を確認する

```bash
kubectl get pv
```

既定（`fsx_enabled = true` / `openzfs_enabled = true` / `efs_enabled = false`）で apply した直後の実機出力です。Amazon FSx for Lustre（`fsx-training`）と Amazon FSx for OpenZFS（`openzfs-shared`）の static PV が作られており、まだどの PVC も作っていないので `Available` です。

```text
NAME             CAPACITY   ACCESS MODES   RECLAIM POLICY   STATUS      CLAIM   STORAGECLASS   AGE
fsx-training     4800Gi     RWX            Retain           Available            <unset>        7h
openzfs-shared   256Gi      RWX            Retain           Available            <unset>        7h
```

Amazon EFS は既定で無効のため、この時点では EFS の PV は出てきません（後述の手順 4 で有効化すると `efs-neuron-workspace` が追加されます）。いずれの PV も `storageClassName` が空の static PV です。動的プロビジョニング用の `efs-shared` StorageClass は `efs_enabled = true` のときにだけ作られるため、この時点（Amazon EFS 無効）ではまだ存在しません。後述の手順 4 で Amazon EFS を有効化すると `efs-shared` も作られますが、この静的 PV とは別物である点に注意します。Amazon FSx for Lustre が `RWX` なのは、複数ノードの Pod から同時にチェックポイント書き込みやデータ読み出しができるようにするためです。

## 3. Amazon FSx for Lustre に書き込み、ノードを跨いでデータが残ることを確認する

PV は Terraform で作られていますが、PVC（Pod がマウントに使う参照）は手動で作ります。まず既定で有効な Amazon FSx for Lustre で試します。static PV に名前でバインドするため `volumeName` に PV 名を、`storageClassName` に空文字を指定します。

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: fsx-claim
  namespace: default
spec:
  accessModes: ["ReadWriteMany"]
  storageClassName: ""
  volumeName: fsx-training
  resources:
    requests:
      storage: 4800Gi
EOF
kubectl get pvc fsx-claim
```

`Bound` になったら、ファイルを書き込むテスト Pod を実行します。

```bash
kubectl run fsx-test --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"fsx-test","image":"busybox","command":["sh","-c","echo hello-fsx > /mnt/fsx/test.txt && cat /mnt/fsx/test.txt"],"volumeMounts":[{"name":"fsx","mountPath":"/mnt/fsx"}]}],"volumes":[{"name":"fsx","persistentVolumeClaim":{"claimName":"fsx-claim"}}]}}'
kubectl logs fsx-test
```

Pod のログに `hello-fsx` が出れば、Amazon FSx for Lustre のマウントと書き込みが成功しています。続いて Pod を削除し、別名の Pod から同じファイルを読み出します。

```bash
kubectl delete pod fsx-test
kubectl run fsx-test2 --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"fsx-test2","image":"busybox","command":["cat","/mnt/fsx/test.txt"],"volumeMounts":[{"name":"fsx","mountPath":"/mnt/fsx"}]}],"volumes":[{"name":"fsx","persistentVolumeClaim":{"claimName":"fsx-claim"}}]}}'
kubectl logs fsx-test2
```

別名の Pod でも `hello-fsx` が読み出せます。Pod（ひいてはノード）が入れ替わっても、共有ストレージ上のデータが残り続けることが確認できます。なお、この静的 PV には topology 制約が付いていないため、busybox Pod は任意の AZ にスケジュールされ得ます。Amazon FSx for Lustre と別 AZ に載った場合はクロス AZ 転送が発生しますが、この規模の確認作業では実害はほぼありません。

## 4. Amazon EFS を有効化する

Amazon EFS は既定で無効なので、マルチ AZ の RWX キャッシュを試すにはまず有効化します。`terraform.tfvars` に `efs_enabled = true` を設定して apply すると、Amazon EFS ファイルシステム・4 つの private subnet それぞれへのマウントターゲット・アクセスポイント・static PV（`efs-neuron-workspace`）が作られます。`terraform.tfvars` に既に `efs_enabled` のエントリがある場合は追記でなく書き換えます（同じキーを重複して書くと HCL のパースエラーになります）。

```bash
echo 'efs_enabled = true' >> terraform.tfvars  # 既存のエントリがある場合は追記でなく編集する
terraform apply
```

apply 完了後、マウントターゲットがすべて `available` になるまで数分かかります。PV が追加されたことを確認します。

```bash
kubectl get pv | grep efs
# efs-neuron-workspace   1000Gi   RWX   Retain   Available   ...
```

## 5. Amazon EFS 用の PVC を作成し、書き込みテストを行う

Amazon FSx と同じく、static PV に名前でバインドする PVC を作ります。1 つの PV は 1 つの PVC にしか bind しません（RWX でも同じで、複数 Pod から使えるだけです）。apply 直後の PV は `Available` なので、ここで作る `efs-claim` がそこに bind します。

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

Amazon EFS にファイルを書き込むテスト Pod を実行します。

```bash
kubectl run efs-test --restart=Never \
    --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"efs-test","image":"busybox","command":["sh","-c","echo hello > /mnt/efs/test.txt && cat /mnt/efs/test.txt"],"volumeMounts":[{"name":"efs","mountPath":"/mnt/efs"}]}],"volumes":[{"name":"efs","persistentVolumeClaim":{"claimName":"efs-claim"}}]}}'
```

Pod のログに `hello` が出力されれば、Amazon EFS のマウントと書き込みが成功しています。

```bash
kubectl logs efs-test
```

## 6. Pod を削除して再作成し、データが残ることを確認する

```bash
kubectl delete pod efs-test
kubectl run efs-test2 --restart=Never \
    --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"efs-test2","image":"busybox","command":["cat","/mnt/efs/test.txt"],"volumeMounts":[{"name":"efs","mountPath":"/mnt/efs"}]}],"volumes":[{"name":"efs","persistentVolumeClaim":{"claimName":"efs-claim"}}]}}'
kubectl logs efs-test2
```

別名の Pod でも `hello` が読み出せます。これが、Karpenter がノードを入れ替えても Pod が再スケジュールされた先で同じキャッシュを読み続けられる、という本章の要点そのものです。Amazon FSx（手順 3）と Amazon EFS（手順 5〜6）のどちらでも、Pod をまたいでデータが残ることを実機で確認できました。

# まとめ

本章では、Karpenter によるノード入れ替えから独立したデータ層として Amazon EFS と Amazon FSx for Lustre を構成しました。既定で有効な共有ストレージは Amazon FSx for Lustre と Amazon FSx for OpenZFS の単一 AZ 2 層であり、Amazon EFS はマルチ AZ・RWX が必要な場合に有効化する opt-in レイヤーです。Amazon EFS はマルチ AZ の RWX キャッシュとして HF キャッシュや NEFF に、Amazon FSx for Lustre は単一 AZ の高スループット・スクラッチとして大規模データセットやチェックポイントに向きます。Amazon FSx for Lustre は動的プロビジョニングに対応しないため static provisioning を用いる点、`fsx_subnet_index` とアクセラレータプールの `zone` を揃える点、`prevent_destroy` が未設定である点の 3 つを押さえておけば、以降の章で GPU/Neuron ワークロードがこの共有ストレージを安心して利用できます。

# 参考資料

- [Amazon EFS CSI Driver](https://github.com/kubernetes-sigs/aws-efs-csi-driver)
- [Amazon FSx for Lustre CSI Driver](https://github.com/kubernetes-sigs/aws-fsx-csi-driver)
- [Amazon FSx for Lustre ユーザーガイド](https://docs.aws.amazon.com/fsx/latest/LustreGuide/what-is.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
