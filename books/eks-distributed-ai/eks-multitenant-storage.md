---
title: "Advanced01 - 共有ストレージをマルチテナントで扱う"
free: true
---

本章では、Basic02 と Basic10 で扱った共有ストレージ（Amazon FSx for OpenZFS と Amazon FSx for Lustre）を、複数のチームや namespace で使うときの設計を扱います。Basic02・Basic10 では単一の利用者を前提に「共有」の最小構成を組みましたが、共有クラスタを複数テナントで使うと「どこまで分離できるのか」「どの層で分離するのか」を設計として決める必要が出てきます。

:::message
本章は Amazon FSx for OpenZFS と Amazon FSx for Lustre に絞ります。前提として Basic01 から Basic10 までのファイルシステムと静的 PersistentVolume が作成済みであること、`k` エイリアスと `--context` が設定済みであることを想定します。
:::

# 解説

## マルチテナントを 2 階層で捉える

「マルチテナント」を一括りにすると設計が破綻します。本章では次の 2 階層に分けて考えます。

- **階層 1: パス分離** — namespace ごとにデータの置き場所を分けるだけの分離です。テナントごとに別のディレクトリ（サブボリューム）を割り当てますが、同じファイルシステムを共有しているため、権限を絞らなければ相互にデータが見えます。運用規約による分離であり、強制力はありません。
- **階層 2: 強制隔離** — テナントごとに独立した領域を割り当て、他テナントの領域に触れられないようにする分離です。これは「領域の分離（PVC ごとに別ボリューム・別アクセスポイントを払い出す）」と「ID 制御（POSIX の uid/gid をテナントごとに強制する squash）」の 2 要素からなります。本章のワークショップでは前者（Amazon FSx for OpenZFS の動的子ボリュームによる領域の強制分離）を実演し、後者の ID 制御は NFS export の squash や Pod の `securityContext` と組み合わせる追加レイヤーとして解説します。

Basic02・Basic10 で作った「同一ファイルシステムを複数 namespace から使う」構成は階層 1 です。階層 2 まで踏み込むかどうかは、テナントが同じチーム内の区分なのか、互いに信頼しない別組織なのかで変わります。なお階層 2 の「強制力」は FSx 単体ではなく、誰が PV/PVC を作れるかという Kubernetes RBAC と、NFS export の絞り込みの組み合わせで担保される点に注意してください。

:::message alert
Kubernetes の namespace はそれ自体では強いセキュリティ境界ではありません。ストレージの隔離は、namespace に加えて POSIX 権限・RBAC・IAM・セキュリティグループ・Pod Security と組み合わせて初めて意味を持ちます。テナントが真の信頼境界（別顧客・別 KMS 鍵・別課金）であれば、同一ファイルシステム内の論理分離で頑張らず、ファイルシステムごと、場合によってはアカウントごとに分けてください。
:::

## 3 つのストレージの非対称

同じ「共有ストレージ」でも、マルチテナントの実現手段はバックエンドで大きく異なります。次の表は本 book の検証結果をまとめたものです。

| 観点 | FSx for OpenZFS | FSx for Lustre |
|---|---|---|
| 複数 namespace 共有（同一 FS を指す静的 PV を複数） | 可 | 可 |
| 動的な per-PVC 隔離（PVC ごとに独立領域を自動払い出し） | 可（子ボリューム） | 不可（動的は新規 FS 作成） |
| パス分離（階層 1） | 可 | 可 |
| ID 制御（squash/POSIX） | NFS export の squash | RootSquashConfiguration |
| per-PVC 容量クォータ | 可 | FS 単位の user/group/project quota（project は Lustre 2.15） |
| AZ | Multi-AZ / Single-AZ 選択可 | 単一 AZ |
| S3 へのコールド退避 | スナップショット（ローカル世代管理）+ Pod からの `aws s3 sync` | DRA（S3 と双方向 sync） |

要点は、**Amazon FSx for OpenZFS は既存ファイルシステムの配下に PVC ごとの子ボリュームを動的に切り出せる**のに対し、**Amazon FSx for Lustre は既存ファイルシステムを PVC 単位に分割できない**ことです。Lustre の動的プロビジョニングは PVC ごとに新しいファイルシステムを作成する動作で、作成に時間がかかり容量単位も大きいため、テナント分離の通常解にはなりません。したがって本章では、パス分離（階層 1）は両者で、per-PVC の動的隔離は OpenZFS でのみ実演し、Lustre は解説にとどめます。

もう 1 つ、AZ の扱いも設計事項です。Amazon FSx for Lustre は単一 AZ、Amazon FSx for OpenZFS も Single-AZ を選ぶとその AZ からのアクセスが前提になります。マルチテナントの共有クラスタでは、別 AZ のノードからマウントするとクロス AZ 転送コストとレイテンシが乗るため、`topology.kubernetes.io/zone` の nodeSelector や PV の `nodeAffinity` で、テナントの Pod をファイルシステムと同じ AZ に寄せます。

## 共有（パス分離）の仕組み

静的 PersistentVolume は 1 つの PersistentVolumeClaim としか結びつきません（1 対 1）。一方で、同じファイルシステムを指す静的 PV は名前を変えていくつでも作れます。`aws-fsx-csi-driver` も `aws-fsx-openzfs-csi-driver` も、静的プロビジョニングではマウント先を `volumeAttributes` の値（Lustre なら `dnsname`/`mountname`、OpenZFS なら別のキー）で決め、`volumeHandle` はマウントには使いません。そのため、マウント情報を同じにした PV を namespace の数だけ用意すれば、1 つのファイルシステムを複数 namespace が同時にマウントできます。動的プロビジョニングも独自コントローラも要りません。

この設計で 2 点、気をつけることがあります。1 つ目は `volumeHandle` を PV ごとに一意にすることです。kubelet は CSI ボリュームを「ドライバ名と `volumeHandle`」の組で識別するため、同じ `volumeHandle` を持つ PV の Pod が同一ノードに同居するとマウント管理が競合する恐れがあります。マウント先は `volumeAttributes` で決まるので、`volumeHandle` には元の値に接尾辞を付けるなど、任意の一意な文字列を与えれば十分です。ただしこの接尾辞つき handle が使えるのは、PV を `Retain` で運用して CSI の削除・拡張 API を呼ばせない前提のときだけです。`Delete` にすると、ドライバが handle を実リソース ID として解釈しようとして失敗します（本章の共有用 PV は `Retain` です）。2 つ目は PV に `claimRef` を焼き込むことです。`claimRef` のない空きの静的 PV は、条件が合えば意図しない namespace の PVC にバインドされ得ます。共有ファイルシステムを指す PV では、これはクロステナントのデータ露出につながるため、`claimRef` に namespace と名前を書いて予約状態にしておきます。

## 隔離（階層 2）の実現

パス分離だけでは相互にデータが見えます。階層 2 の隔離は次のように実現します。

- **FSx for OpenZFS**: 動的プロビジョニングで PVC ごとに独立した子ボリュームを切り出します。子ボリュームはそれぞれ別のデータセットで、`StorageCapacityQuotaGiB` による per-PVC の容量クォータを持ち、NFS export の squash 設定で uid/gid を制御できます。後述のワークショップで実演します。
- **FSx for Lustre**: 既存ファイルシステムを PVC 単位に分割できないため、パス分離（サブディレクトリ規約）に、`RootSquashConfiguration` による root squash と user/group/project クォータを組み合わせてソフトに隔離します。強制力は OpenZFS の子ボリュームより弱く、真の分離が要件なら Lustre はテナントごとにファイルシステムを分けます。

:::message
Amazon FSx for OpenZFS の動的プロビジョニングを使うには、CSI コントローラの IAM ロールに `fsx:CreateVolume`・`fsx:DeleteVolume`・`fsx:DescribeVolumes`・`fsx:TagResource` が必要です。静的マウントだけを想定した権限（describe のみ）だと、動的プロビジョニングは `AccessDeniedException` で失敗します。本 book の Terraform はこの権限を OpenZFS CSI ロールに付与します。
:::

## コールドデータの退避

学習データの正（source of truth）と最終成果物は、可用性とコストの観点から Amazon S3 に置くのが基本です。共有ファイルシステムはあくまでホット層で、そこから S3 へ退避する経路を設計します。

- **FSx for OpenZFS**: per-volume スナップショットでボリューム単位の世代管理・クローン・full-copy ができます。ただしスナップショットはファイルシステム内（`.zfs/snapshot`）に保存され、ファイルシステムと運命を共にする**ローカルな保護**です。S3 へコールド退避するには、Lustre の DRA のようなネイティブ連携が OpenZFS には無いため、ボリュームをマウントした Pod から `aws s3 sync` で S3 バケットへ明示的に書き出します。スナップショットで直近の状態をローカルに世代管理しつつ、`aws s3 sync` で S3 を正とする、という二段構えにします。
- **FSx for Lustre**: DRA（Data Repository Association）で Lustre のパスと S3 プレフィックスを関連付け、双方向に自動同期します。S3 にあるデータは Lustre から遅延ロード（HSM）で読め、Lustre に書いたデータは S3 に自動エクスポートされます。学習データを S3 に置き、成果物を S3 に逃がす定石です。DRA は PERSISTENT_2 デプロイタイプで使えます。

## アンチパターン

- namespace ごとに PVC を作っただけで「隔離できた」と考える（共有ファイルシステムならデータは相互可視です）。
- Amazon FSx for Lustre を汎用のマルチテナント NAS として使う（高スループットのスクラッチ用途に割り切ります）。
- 1 つの ReadWriteMany の PVC を全チームに配る。
- 動的プロビジョニングが常に安価で速いと考える（Lustre の動的はファイルシステムを量産し高コストです）。
- Kueue でストレージまで分離できると考える（Kueue は計算資源のキューイングで、ストレージは PVC・StorageClass・ResourceQuota・RBAC で設計します）。

容量の食い潰し対策として、Kubernetes 側では ResourceQuota で namespace ごとに上限を設定できます。ただし動的プロビジョニングでは PVC の要求容量が `1Gi` に固定される（実容量は StorageClass のクォータで決まる）ため、`<storageclass>.storageclass.storage.k8s.io/requests.storage` の集計は実消費と乖離します。実効的なのは `<storageclass>.storageclass.storage.k8s.io/persistentvolumeclaims` による PVC 個数の上限で、これに OpenZFS の per-PVC クォータ（1 個あたりの容量）を掛けて namespace の総容量を間接的に縛ります。Kueue は GPU/CPU など計算資源のキューイングを担い、ストレージ容量は対象外です。ストレージの容量とアクセス境界は PVC・StorageClass・ResourceQuota・RBAC で、計算資源は Kueue で、とレイヤーを分けて設計します。

# ワークショップ実施

以下は `ap-southeast-4` での実機出力例です。リージョンやファイルシステム ID は環境で変わりますが、手順は同じです。作業用 namespace は Basic02 と同じく既定を `distai` とし、テナント役の namespace を追加で作ります。

## 1. 前提を確認する

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
terraform output shared_storage
```

`fsx_openzfs` と `fsx_lustre` の `persistent_volume`（静的 PV 名）と `id` を控えておきます。Basic02・Basic10 で作成した静的 PV が存在することを確認します（既存の PVC にバインド済みで `Bound` になっていて構いません）。本章では、同じファイルシステムを指す 2 つ目の静的 PV を新しい namespace 用に追加で作ります。

```bash
k get pv
```

## 2. パス分離（階層 1）: 1 つの Lustre を複数 namespace で共有する

同じ Amazon FSx for Lustre を 2 つの namespace から使います。同一のファイルシステムを指す 2 つ目の静的 PV を、`volumeHandle` だけ一意にして作り、`claimRef` で予約します。

```bash
DNS=$(k get pv fsx-training -o jsonpath='{.spec.csi.volumeAttributes.dnsname}')
MOUNT=$(k get pv fsx-training -o jsonpath='{.spec.csi.volumeAttributes.mountname}')
HANDLE=$(k get pv fsx-training -o jsonpath='{.spec.csi.volumeHandle}')
CAP=$(k get pv fsx-training -o jsonpath='{.spec.capacity.storage}')

k create namespace team-b --dry-run=client -o yaml | k apply -f -
k apply -f - <<EOF
apiVersion: v1
kind: PersistentVolume
metadata:
  name: fsx-training-team-b
spec:
  capacity: { storage: ${CAP} }
  accessModes: ["ReadWriteMany"]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  mountOptions: ["flock"]
  claimRef: { namespace: team-b, name: fsx-claim }
  csi:
    driver: fsx.csi.aws.com
    volumeHandle: ${HANDLE}-team-b
    volumeAttributes:
      dnsname: ${DNS}
      mountname: ${MOUNT}
EOF

k apply -n team-b -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: fsx-claim
spec:
  accessModes: ["ReadWriteMany"]
  storageClassName: ""
  volumeName: fsx-training-team-b
  resources:
    requests:
      storage: ${CAP}
EOF
k wait --for=jsonpath='{.status.phase}'=Bound pvc/fsx-claim -n team-b --timeout=60s
```

`team-b` の Pod が自分のサブディレクトリに書き込み、既定 namespace（`distai`）の Pod から同じファイルが読めることを確認します。`distai` 側の `fsx-claim` は Basic10 の手順で作成済みの前提です。

```bash
k run writer -n team-b --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"w","image":"busybox","command":["sh","-c","mkdir -p /mnt/fsx/team-b && echo from-team-b > /mnt/fsx/team-b/note.txt"],"volumeMounts":[{"name":"fsx","mountPath":"/mnt/fsx"}]}],"volumes":[{"name":"fsx","persistentVolumeClaim":{"claimName":"fsx-claim"}}]}}'
k wait --for=jsonpath='{.status.phase}'=Succeeded pod/writer -n team-b --timeout=180s

k run reader -n distai --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"r","image":"busybox","command":["cat","/mnt/fsx/team-b/note.txt"],"volumeMounts":[{"name":"fsx","mountPath":"/mnt/fsx"}]}],"volumes":[{"name":"fsx","persistentVolumeClaim":{"claimName":"fsx-claim"}}]}}'
k wait --for=jsonpath='{.status.phase}'=Succeeded pod/reader -n distai --timeout=180s
k logs reader -n distai
```

`distai` 側の Pod のログに `from-team-b` が出れば、2 つの namespace が同じ 1 つのファイルシステムを共有できています。これは「共有」であって「隔離」ではありません。相互にデータが見える点を確認してください。Amazon FSx for OpenZFS も静的プロビジョニングの考え方は同じですが、`volumeAttributes` のキーがドライバごとに異なるため、この manifest をそのまま流用はできません。

後片付けをします。共有ファイルシステムに書いたデータ（`/mnt/fsx/team-b/note.txt`）は、PV・PVC・namespace を消してもファイルシステムに残り続けます。これはテナントのオフボーディングでまさに問題になる点なので、データも明示的に消します。

```bash
k run cleaner -n team-b --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"c","image":"busybox","command":["sh","-c","rm -rf /mnt/fsx/team-b"],"volumeMounts":[{"name":"fsx","mountPath":"/mnt/fsx"}]}],"volumes":[{"name":"fsx","persistentVolumeClaim":{"claimName":"fsx-claim"}}]}}'
k wait --for=jsonpath='{.status.phase}'=Succeeded pod/cleaner -n team-b --timeout=180s

k delete pod reader -n distai --ignore-not-found
k delete pod writer cleaner -n team-b --ignore-not-found
k delete pvc fsx-claim -n team-b --ignore-not-found
k delete pv fsx-training-team-b --ignore-not-found
k delete namespace team-b --ignore-not-found
```

## 3. per-PVC 隔離（階層 2）: OpenZFS 動的子ボリューム

パス分離では相互にデータが見えました。Amazon FSx for OpenZFS の動的プロビジョニングを使うと、PVC ごとに独立した子ボリューム（別々のデータセット）が親ファイルシステムの配下に作られ、既定では他テナントの子ボリュームはマウントされず相互に見えません。ただしこの分離を「強制」しているのは FSx 単体ではなく、PV/PVC を作れるのは誰かという Kubernetes RBAC と NFS export の設定です。StorageClass の `NfsExports` を `Clients: "*"` のままにすると VPC 内から子ボリュームを直接マウントする経路は塞げませんし、親ルートボリュームを `crossmnt` でマウントしている共有 PV があればそこから子ボリュームが見えます。本番では `Clients` をノードのサブネットに絞り、テナントに PV 作成権限を与えない RBAC と併せて初めて隔離が成立します。

まず StorageClass を作ります。`ParentVolumeId` には Basic02 で作った OpenZFS ファイルシステムのルートボリュームを指定します（`terraform output` の `fsx_openzfs` から取得できます）。`StorageCapacityQuotaGiB` が per-PVC の容量クォータになります。

```bash
ROOT_VOL=$(aws fsx describe-file-systems --file-system-ids <openzfs-fs-id> \
  --query 'FileSystems[0].OpenZFSConfiguration.RootVolumeId' --output text)

k apply -f - <<EOF
kind: StorageClass
apiVersion: storage.k8s.io/v1
metadata: { name: openzfs-sc }
provisioner: fsx.openzfs.csi.aws.com
parameters:
  ResourceType: "volume"
  ParentVolumeId: '"${ROOT_VOL}"'
  CopyTagsToSnapshots: 'false'
  DataCompressionType: '"NONE"'
  NfsExports: '[{"ClientConfigurations": [{"Clients": "*", "Options": ["rw","crossmnt"]}]}]'
  ReadOnly: 'false'
  RecordSizeKiB: '128'
  StorageCapacityReservationGiB: '-1'
  StorageCapacityQuotaGiB: '10'
  OptionsOnDeletion: '["DELETE_CHILD_VOLUMES_AND_SNAPSHOTS"]'
reclaimPolicy: Delete
allowVolumeExpansion: false
mountOptions: [ "nfsvers=4.1", "rsize=1048576", "wsize=1048576", "timeo=600" ]
EOF
```

2 つの namespace（`tenant-a` / `tenant-b`）にそれぞれ PVC を作ります。動的プロビジョニングでは PVC の要求容量は無視され、クォータは StorageClass の値が使われるため、要求は `1Gi` に固定します。

```bash
for ns in tenant-a tenant-b; do
  k create namespace $ns --dry-run=client -o yaml | k apply -f -
  k apply -n $ns -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: cache }
spec:
  accessModes: ["ReadWriteMany"]
  storageClassName: openzfs-sc
  resources: { requests: { storage: 1Gi } }
EOF
done
k wait --for=jsonpath='{.status.phase}'=Bound pvc/cache -n tenant-a --timeout=180s
k wait --for=jsonpath='{.status.phase}'=Bound pvc/cache -n tenant-b --timeout=180s
```

親ファイルシステムの配下に、PVC ごとの子ボリュームが 2 つ作られています。

```bash
aws fsx describe-volumes --filters Name=file-system-id,Values=<openzfs-fs-id> \
  --query 'Volumes[?OpenZFSConfiguration.ParentVolumeId==`'"$ROOT_VOL"'`].{name:Name,quota:OpenZFSConfiguration.StorageCapacityQuotaGiB,path:OpenZFSConfiguration.VolumePath}' \
  --output table
```

`pvc-...` という名前の子ボリュームが 2 つ、それぞれ独立したパスと 10 GiB のクォータを持って表示されます。相互不可視を確認します。`tenant-a` が書いたファイルは `tenant-b` からは見えません。

```bash
k run w -n tenant-a --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"w","image":"busybox","command":["sh","-c","echo secret-a > /cache/a.txt"],"volumeMounts":[{"name":"c","mountPath":"/cache"}]}],"volumes":[{"name":"c","persistentVolumeClaim":{"claimName":"cache"}}]}}'
k wait --for=jsonpath='{.status.phase}'=Succeeded pod/w -n tenant-a --timeout=180s

k run r -n tenant-b --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"r","image":"busybox","command":["sh","-c","ls /cache && (cat /cache/a.txt 2>&1 || echo NOT-VISIBLE)"],"volumeMounts":[{"name":"c","mountPath":"/cache"}]}],"volumes":[{"name":"c","persistentVolumeClaim":{"claimName":"cache"}}]}}'
k wait --for=jsonpath='{.status.phase}'=Succeeded pod/r -n tenant-b --timeout=180s
k logs r -n tenant-b
```

`tenant-b` のログに `a.txt` が現れず `NOT-VISIBLE` が出れば、PVC ごとに別々の子ボリュームへ分離できています。パス分離（手順 2）が相互可視だったのと対照的です。前述のとおり、この分離の強制力はテナントに PV 作成権限を与えない RBAC と `NfsExports` の絞り込みに依存する点を忘れないでください。

後片付けをします。`reclaimPolicy: Delete` と `OptionsOnDeletion` により、PVC を消すと子ボリュームも削除されます。CSI ロールに削除権限がないと PV が `Released` で残り、FSx にも子ボリュームが残って課金が続くため、実際に消えたことまで確認します。

```bash
k delete namespace tenant-a tenant-b
k get pv | grep pvc- || echo "no dynamic PV remaining"
aws fsx describe-volumes --filters Name=file-system-id,Values=<openzfs-fs-id> \
  --query 'Volumes[?OpenZFSConfiguration.ParentVolumeId==`'"$ROOT_VOL"'`].Name' --output text
k delete sc openzfs-sc
```

## 4. コールドデータ退避

### FSx for Lustre を DRA で S3 に同期する

Lustre のパスと S3 バケットを関連付けます。まず退避先の S3 バケットを用意します（既存のバケットがあればそれを使います）。`AutoImportPolicy` と `AutoExportPolicy` で双方向に自動同期します。

```bash
BUCKET=your-cold-storage-bucket
aws s3 mb s3://$BUCKET

aws fsx create-data-repository-association \
  --file-system-id <lustre-fs-id> \
  --file-system-path /s3data \
  --data-repository-path s3://$BUCKET \
  --batch-import-meta-data-on-create \
  --s3 'AutoImportPolicy={Events=[NEW,CHANGED,DELETED]},AutoExportPolicy={Events=[NEW,CHANGED,DELETED]}'
```

DRA の作成には数分かかります。`AVAILABLE` になるまで待ってから確認します。

```bash
ASSOC_ID=$(aws fsx describe-data-repository-associations \
  --filters Name=file-system-id,Values=<lustre-fs-id> \
  --query 'Associations[0].AssociationId' --output text)
aws fsx describe-data-repository-associations --association-ids "$ASSOC_ID" \
  --query 'Associations[0].Lifecycle' --output text
```

`AVAILABLE` になったら、Lustre をマウントした Pod（`distai` の `fsx-claim`）から双方向同期を確認します。S3 に置いたオブジェクトは `/mnt/fsx/s3data` 以下に現れ（初回アクセスで遅延ロード）、Lustre に書いたファイルは数十秒で S3 にエクスポートされます。

```bash
k run dra -n distai --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"d","image":"busybox","command":["sh","-c","echo from-lustre > /mnt/fsx/s3data/from-lustre.txt"],"volumeMounts":[{"name":"fsx","mountPath":"/mnt/fsx"}]}],"volumes":[{"name":"fsx","persistentVolumeClaim":{"claimName":"fsx-claim"}}]}}'
k wait --for=jsonpath='{.status.phase}'=Succeeded pod/dra -n distai --timeout=180s
sleep 60
aws s3 ls s3://$BUCKET/ --recursive   # from-lustre.txt が現れる
k delete pod dra -n distai --ignore-not-found
```

学習データの正を S3 に置き、DRA でホットな Lustre に取り込み、成果物を S3 に書き戻す、という往復がこれで成立します。

### FSx for OpenZFS をスナップショットでローカル保護する

ボリューム単位のスナップショットを取ります。手順 3 の子ボリュームはすでに削除済みなので、ここでは Basic02 で作成した OpenZFS のルートボリューム（`$ROOT_VOL`）を対象にします。

```bash
aws fsx create-snapshot --volume-id "$ROOT_VOL" --name checkpoint-snap
```

スナップショットはファイルシステム内に保存され、`restore-volume-from-snapshot` でその時点に戻せます。クローンボリューム（書き込み可能な複製）や full-copy ボリュームも作れます。

```bash
aws fsx restore-volume-from-snapshot \
  --volume-id <volume-id> --snapshot-id <fsvolsnap-id> \
  --options DELETE_INTERMEDIATE_SNAPSHOTS
```

スナップショットはファイルシステム内（`.zfs/snapshot`）に保存されるローカルな保護で、ファイルシステムが失われれば道連れになります。別 AZ・別リージョンにデータを残す目的には使えません。越境の退避は、次に示す S3 への同期で行います。

### FSx for OpenZFS を S3 に退避する

Amazon FSx for OpenZFS には Lustre の DRA のような S3 ネイティブ連携がありません。S3 へコールド退避するには、ボリュームをマウントした Pod から `aws s3 sync` で明示的に書き出します。ここでは Basic02 の共有 PVC（`shared-claim`、`/shared` にマウント）を `amazon/aws-cli` イメージの Pod でマウントし、S3 に同期します。Pod には S3 への書き込み権限（Pod Identity か、ノードのインスタンスロール）が必要です。

```bash
BUCKET=your-cold-storage-bucket   # 退避先。未作成なら aws s3 mb s3://$BUCKET

k run zfs-evac -n distai --restart=Never --image=amazon/aws-cli \
    --overrides='{"spec":{"containers":[{"name":"e","image":"amazon/aws-cli","command":["aws","s3","sync","/shared","s3://'"$BUCKET"'/openzfs-cold/"],"volumeMounts":[{"name":"zfs","mountPath":"/shared"}]}],"volumes":[{"name":"zfs","persistentVolumeClaim":{"claimName":"shared-claim"}}]}}'
k wait --for=jsonpath='{.status.phase}'=Succeeded pod/zfs-evac -n distai --timeout=300s
aws s3 ls s3://$BUCKET/openzfs-cold/ --recursive
k delete pod zfs-evac -n distai --ignore-not-found
```

`aws s3 sync` は差分だけを送るので、CronJob 化すればホット層の成果物を継続的に S3 へ逃がせます。Lustre は DRA でこれを自動化でき、OpenZFS は明示的な同期ジョブで行う、という違いです。

### 本章で作ったリソースの後片付け

本章の DRA・スナップショット・S3 バケット（と手順 3 の動的子ボリューム）は、消さないと課金・容量消費・S3 との同期が続きます。検証が終わったら片付けます。

```bash
aws fsx delete-data-repository-association \
  --association-id "$ASSOC_ID" --delete-data-in-file-system false
SNAP_ID=$(aws fsx describe-snapshots \
  --filters Name=volume-id,Values="$ROOT_VOL" \
  --query 'Snapshots[?Name==`checkpoint-snap`].SnapshotId' --output text)
aws fsx delete-snapshot --snapshot-id "$SNAP_ID"
aws s3 rm s3://$BUCKET --recursive && aws s3 rb s3://$BUCKET
```

:::message alert
動的子ボリューム・DRA・スナップショット・S3 に退避したオブジェクトはいずれも FSx / S3 の課金に直結します。テナントのセルフサービスで PVC を作らせる運用では、`reclaimPolicy` と `OptionsOnDeletion` の組み合わせ次第で PVC 削除がそのままデータ削除になる点にも注意してください。削除漏れも誤削除もどちらもリスクです。
:::

# まとめ

本章では、共有ストレージのマルチテナントを 2 階層（パス分離と ID 制御）で整理し、Amazon FSx for OpenZFS と Amazon FSx for Lustre の非対称を確認しました。パス分離は、同一ファイルシステムを指す静的 PV を namespace ごとに用意することで両者で実現できます（`volumeHandle` は一意に、`claimRef` で予約）。per-PVC の強制隔離は、Amazon FSx for OpenZFS の動的子ボリュームで実演し、相互不可視とクォータを確認しました。Amazon FSx for Lustre は既存ファイルシステムを PVC 単位に分割できないため、隔離が要件ならファイルシステムごとに分けるか、root squash とクォータでソフトに固めます。コールドデータは S3 を正とし、Amazon FSx for Lustre は DRA で、Amazon FSx for OpenZFS はスナップショットでローカルに世代管理しつつ Pod からの `aws s3 sync` で S3 に退避します。namespace はそれ自体では境界にならないこと、真の信頼境界ならファイルシステムやアカウントごとに分けることを、設計の前提に置いてください。

# 参考資料

- [Amazon FSx for OpenZFS のスナップショット](https://docs.aws.amazon.com/fsx/latest/OpenZFSGuide/snapshots-openzfs.html)
- [Amazon FSx for OpenZFS のデプロイタイプと可用性](https://docs.aws.amazon.com/fsx/latest/OpenZFSGuide/availability-durability.html)
- [aws-fsx-openzfs-csi-driver の動的プロビジョニング](https://github.com/kubernetes-sigs/aws-fsx-openzfs-csi-driver)
- [Amazon FSx for Lustre のデータリポジトリ連携（DRA）](https://docs.aws.amazon.com/fsx/latest/LustreGuide/create-dra-linked-data-repo.html)
- [Amazon FSx for Lustre のストレージクォータ（user/group/project）](https://docs.aws.amazon.com/fsx/latest/LustreGuide/lustre-quotas.html)
