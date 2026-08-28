---
title: "Basic10 - Amazon FSx for Lustre を導入する"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、Basic01 から Basic04 で構築した Amazon VPC・Amazon EKS コントロールプレーン・アクセラレータノードの土台の上に、Karpenter がノードを入れ替えても失われないデータ層として Amazon FSx for Lustre を構成します。Amazon FSx for Lustre は単一 AZ の高スループットなスクラッチおよびチェックポイント領域で、Terraform で 1 度作成すれば以降の Karpenter によるノード入れ替えの影響を受けません。

:::message
共有ストレージのうち Amazon FSx for OpenZFS と、静的 PersistentVolume・PersistentVolumeClaim の基本的な仕組みは Basic02 で解説済みです。本章ではそれらの再説明は行わず、Amazon FSx for Lustre 固有の特徴と制約に絞って扱います。Amazon EFS については本章の末尾で選択肢として簡単に触れるにとどめ、詳細は Neuron を扱う章に譲ります。
:::

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち Amazon FSx for Lustre です。Karpenter が起動・削除する GPU/Neuron ノードの下に、ノードのライフサイクルとは独立して存在するデータ層を用意します。実装の既定構成では Amazon FSx for Lustre がスクラッチ層、Amazon FSx for OpenZFS が home・共有領域を担う単一 AZ の 2 層構成です。OpenZFS 側は Basic02 で MNIST の学習成果を保存する既定バックエンドとして解説済みなので、本章は Lustre に集中します。

## これは何をするものか

Karpenter は consolidate・drift・expire でノードを次々に入れ替えます。この挙動はコスト最適化のために望ましい一方で、Pod のローカルディスクに置いたデータはノードごと消えるという制約を生みます。再生成に数分から数十分かかる Hugging Face のモデルキャッシュや Neuron コンパイル済みの NEFF、そして大規模データセットや学習チェックポイントを、ノードの寿命から切り離して保持する場所が要ります。これを担うのが共有ストレージです。

Amazon FSx for Lustre は、この用途のうち高いスループットが要る領域を受け持ちます。PERSISTENT_2 デプロイタイプの SSD ストレージを使い、実装では既定で有効です。大規模データセットの読み出しや学習チェックポイントの書き込みのように、帯域が性能を左右するワークロードに向きます。

以降で Amazon FSx for Lustre 固有の 3 つの論点を見ていきます。1 つ目は既存ファイルシステムには静的プロビジョニングしか使えないという CSI ドライバの制約、2 つ目は PersistentVolume と PersistentVolumeClaim がどう結びつくか、3 つ目は EFA によるさらに高いスループットの選択肢です。

## Terraform で作った既存ファイルシステムには静的プロビジョニングを使う

Amazon FSx for Lustre は [`fsx.tf`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/fsx.tf) で構成します。`aws-fsx-csi-driver` は動的プロビジョニングにも対応していますが、それは StorageClass 経由で PVC ごとに新規ファイルシステムを作成する用途に限られ、Terraform が作成済みの既存ファイルシステムに StorageClass をバインドすることはできません。

`aws-fsx-csi-driver` の StorageClass は新規にファイルシステムを作成するためのパラメータしか読みません。既存ファイルシステムの ID を StorageClass に渡しても無視され、最悪の場合は意図しない 2 つ目のファイルシステムが暗黙に作られ、TB 単位の課金が発生します。そのため、この実装では Terraform が作成した 1 つのファイルシステムに対して固定の `volumeHandle` を持つ PersistentVolume を 1 つだけ用意し、PVC はその PV に名前でバインドします。この静的プロビジョニングのパターンそのものは Basic02 の OpenZFS で扱ったものと同じで、PV が PVC を名前ではなく実体で覚える性質や `Released` からの復旧手順も Basic02 で解説済みです。

## Amazon FSx for Lustre 固有の PersistentVolume 設定

Amazon FSx for Lustre の静的 PV と PVC がどう結びつくかを図で整理します。

![Amazon FSx for Lustre の静的 PV と PVC の関係](/images/books/eks-distributed-ai/storage-pv-pvc.png)

PV は Terraform が管理し、基盤が存在する限り残ります。PVC は Pod がボリュームを掴むための参照で、ワークショップ手順の中で読者が 1 度だけ作成します。PVC の要求容量が PV の容量に収まりアクセスモードを満たしたうえで、PVC の `volumeName` が PV 名を指していると `Bound` になります。ここで押さえるべき Amazon FSx for Lustre 固有の点は次の 2 つです。

1 つ目は、PV の `volumeAttributes` のキーが小文字でなければ CSI ドライバに読まれないことです。`aws-fsx-csi-driver` は `dnsname` と `mountname` という小文字キーしか認識しません。

```hcl
# fsx.tf（抜粋）
volumeAttributes = {
  dnsname   = aws_fsx_lustre_file_system.training[0].dns_name
  mountname = aws_fsx_lustre_file_system.training[0].mount_name
}
```

キャメルケースの `dnsName` と書くとドライバに黙って無視され、`NodeStageVolume` が「dnsname is not provided」で失敗し、Pod は `ContainerCreating` のまま止まります。`volumeHandle` はあくまで Kubernetes 側の識別子であり、マウント時に AWS API を呼んで DNS 名を解決するわけではないため、`dnsname` と `mountname` を PV に自分で埋め込む必要があります。

2 つ目は、PV の `mountOptions` に `flock` を指定していることです。これは `aws-fsx-csi-driver` の静的プロビジョニングのサンプルが採用している設定で、Lustre 上でのファイルロックを有効にします。

```hcl
# fsx.tf（抜粋）
persistentVolumeReclaimPolicy = "Retain"
mountOptions                  = ["flock"]
```

`reclaimPolicy` は `Retain` です。`aws-fsx-csi-driver` の公式サンプルの `pv.yaml` も `Retain` を使っています。同ドライバの README のコード例には `Recycle` と書かれている箇所がありますが、`Recycle` は Kubernetes で非推奨となっており、Amazon FSx for Lustre の CSI ドライバは実装していないため機能しません。

## EFA でさらに高いスループットを得る

Amazon FSx for Lustre は Elastic Fabric Adapter（EFA）を有効にすると、通常の TCP マウントより大幅に高いクライアント単体スループットを得られます。AWS 公式の性能ガイドが示す 1 クライアントあたりの最大スループットは次のとおりです。

| ファイルシステム | クライアントのネットワークインターフェース | 最大スループット |
| --- | --- | --- |
| EFA 無効 | 任意 | 100 Gbps |
| EFA 有効 | EFA 非対応（ENA / ENA Express） | 100 Gbps |
| EFA 有効 | EFA | 700 Gbps |
| EFA 有効 | EFA + GPUDirect Storage | 1200 Gbps |

EFA は OS をバイパスし SRD プロトコルで RDMA 通信を行うため、CPU 負荷を下げつつスループットを上げ、テールレイテンシを縮めます。大規模モデルのチェックポイント読み込みのように、ファイルシステムのスループットがそのままジョブのコールドスタート時間に効く場面で価値が大きく、AWS は 10 GBps を超えるスループット容量を要する場合に EFA を推奨しています。ここで単位に注意が必要で、この 10 GBps はバイト毎秒で、ビット毎秒に直すと 80 Gbps にあたります。上の表の 100 Gbps などはビット毎秒なので、両者を混同しないでください。

表の 2 行目が示すとおり、ファイルシステムを EFA 有効にしても、クライアント側が EFA 対応でなければスループットは 100 Gbps に留まります。EFA の効果を得るにはファイルシステムとクライアントの両方の準備が要ります。なお EFA 有効時もメタデータサーバとの通信は TCP を使い、実データが EFA ネットワークを流れます。

ただし EFA 有効化には守るべき制約が複数あります。

- **デプロイタイプの制限** — EFA を有効化できるのは PERSISTENT_2 と Intelligent-Tiering のみです。本実装は PERSISTENT_2 固定なので、この条件は常に満たします。
- **作成時のみ設定可能** — `EfaEnabled` はファイルシステム作成時にしか指定できず、後から有効化できません。既存ファイルシステムに有効化するには作り直しが必要です。
- **メタデータと容量の下限** — EFA 有効時はメタデータを USER_PROVISIONED モードで最低 6000 IOPS、ストレージ容量を最低 4800 GiB でプロビジョニングする必要があります。
- **単一 AZ** — EFA デバイスは単一 AZ 内でのみ動作します。Amazon FSx for Lustre はもともと単一 AZ ですが、EFA を使う場合は、実際にマウントするアクセラレータプールの AZ もファイルシステムと同じ AZ に揃えることが必須になります。EFA を使わない場合のクロス AZ マウントは動作こそしますが、EFA データパスは AZ を跨げません。
- **EFA 対応インスタンスとクライアント側設定** — クライアント側は Nitro v4 世代以降の EFA 対応インスタンスであることに加え、EFA ドライバ・Lustre クライアント・LNET の EFA 設定をノード起動時に適用しておく必要があります。
- **セキュリティグループ** — EFA の SRD トラフィックは TCP のポート単位ルールでは表現できないため、EFA 有効ファイルシステムのセキュリティグループには、そのセキュリティグループ自身との間で全トラフィックを許可する自己参照ルールが別途必要になります。これは Lustre の 988・1018-1023 ポートのルールとは別の要件です。この自己参照ルールが効くのは、ファイルシステムとクライアントの EFA インターフェースが同じセキュリティグループに所属している場合なので、クライアント側を整備する際には、ノードの EFA インターフェースをこのセキュリティグループに参加させる構成もあわせて必要になります。

この実装では、ファイルシステム側の EFA 有効化を `terraform.tfvars` の `fsx_efa_enabled`（既定 `false`）で切り替えられるようにしてあります。`true` にすると `EfaEnabled` の付与、USER_PROVISIONED メタデータ 6000 IOPS、EFA 用の自己参照セキュリティグループルールが自動で構成され、容量と IOPS の下限は `terraform plan` の段階で検証されます。

:::message alert
`fsx_efa_enabled = true` はファイルシステム側の設定だけを行います。ノード側の EFA ドライバ・Lustre クライアント・LNET の EFA 設定と、ノードをファイルシステムのセキュリティグループに参加させる構成は本実装では導入していないため、この設定だけを有効にしてもクライアントは EFA データパスを使えません。ノード側の具体的な導入手順は、後述の参考資料に挙げた AWS の [FSx for Lustre ワークショップ](https://catalog.us-east-1.prod.workshops.aws/workshops/1152c25d-552e-4b9f-8cd0-875910071c54/en-US)の EFA および EKS+EFA のセクションを参照してください。加えて `EfaEnabled` は作成時のみ指定できる設定のため、稼働中のファイルシステムでこの値を切り替えると `terraform apply` はファイルシステムを再作成し、保存済みのデータはすべて失われます。切り替える場合は、事前に必要なデータを Amazon S3 などへ退避してください。今後の課題として EFA 利用有無をより柔軟に扱う仕組みを検討します。
:::

## EFA を共有ストレージと GPU 通信で共有するときの考え方

GPU 分散学習では NCCL の集合通信も EFA を使います。Amazon FSx for Lustre を EFA 有効にすると、ファイルシステムの I/O と NCCL 通信が同じノードの EFA を使うことになるため、両者の関係を理解しておく必要があります。

結論として、両者は同一インスタンスが持つ複数の EFA デバイスのうち別々のデバイスに分離して割り当てられます。AWS の EKS 向け FSx チューニング手順では、P5 系のように 32 枚のネットワークカードを持つインスタンスで、そのうち 8 枚だけを Lustre 用の LNET に割り当て、残りを NCCL の集合通信用に空けています。ただし機種によって既定の割り当ては異なり、P6-B300 では既定で EFA 対応カードすべてを Lustre に割り当てるため、複数ノード学習ではデバイスを明示的に分割する設定が推奨されます。単一ノードでチェックポイントを読み込むだけで NCCL 通信が発生しない場合は、全デバイスを Lustre に使って構いません。なお EFA と IP の通信はインスタンス全体の帯域を共有するため、デバイスを分離しても総帯域の上限は共通である点には留意してください。

# ワークショップ実施

本章の実機検証は、`terraform.tfvars` の既定値（`fsx_enabled = true` / `openzfs_enabled = true` / `efs_enabled = false` / `fsx_efa_enabled = false`）のまま実施します。以下の実機出力は `us-west-2` で採取した例で、読者が別リージョンで進める場合はリージョン名とファイルシステム ID が変わりますが、手順そのものは同じです。

## 1. 前提を確認する

- `terraform apply` 実行ずみ
- `k` と `KUBECONFIG` は Basic01 step 2 の 4 行で設定済み

```bash
export NAMESPACE=distai
k create namespace "$NAMESPACE" --dry-run=client -o yaml | k apply -f -
```

## 2. Terraform の出力を確認する

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
terraform output shared_storage
```

実機出力です。Amazon FSx for Lustre の `mount_name` は CSI ドライバが DNS 名と併せて必要とする値で、コンソールからは見つけにくいのでここに出しています。

```text
{
  "fsx_lustre" = {
    "dns_name" = "fs-0123456789abcdef1.fsx.us-west-2.amazonaws.com"
    "enabled" = true
    "id" = "fs-0123456789abcdef1"
    "mount_name" = "abcd1234"
    "persistent_volume" = "fsx-training"
    "storage_capacity" = "4800"
  }
  "fsx_openzfs" = {
    "dns_name" = "fs-0123456789abcdef2.fsx.us-west-2.amazonaws.com"
    "enabled" = true
    "id" = "fs-0123456789abcdef2"
    "persistent_volume" = "openzfs-shared"
    "storage_capacity" = "256"
  }
}
```

`enabled` がその層を使うかどうか、`persistent_volume` が静的 PV の名前です。

## 3. PersistentVolume を確認する

```bash
k get pv
```

既定で apply した直後の実機出力です。Amazon FSx for Lustre（`fsx-training`）と Amazon FSx for OpenZFS（`openzfs-shared`）の静的 PV が作られており、まだどの PVC も作っていないので `Available` です。

```text
NAME             CAPACITY   ACCESS MODES   RECLAIM POLICY   STATUS      CLAIM   STORAGECLASS   VOLUMEATTRIBUTESCLASS   REASON   AGE
fsx-training     4800Gi     RWX            Retain           Available                          <unset>                         7h
openzfs-shared   256Gi      RWX            Retain           Available                          <unset>                         7h
```

いずれも `storageClassName` が空の静的 PV です。アクセスモードが ReadWriteMany なのは、複数ノードの Pod から同時にチェックポイント書き込みやデータ読み出しができるようにするためです。

`STATUS` が `Available` ではなく `Bound` や `Released` になっている場合は、Basic02 で `fsx` バックエンドを試すなどして、この PV を掴んだ PVC が過去にあったことを意味します。その状態では次の手順で作る PVC が `Pending` のままになるため、先に PV を解放しておきます。

:::message
ここで注意したいのが、`kubectl get pv` で `STATUS=Available` に見えても、`CLAIM` 欄に別 namespace の PVC 名が残っていると、その PV は「その PVC 専用に予約された」状態で、別 namespace の PVC はバインドできず `Pending` のままになる点です。静的 PV は `Retain` なので、一度どれかの PVC がバインドすると `spec.claimRef` が残り続けるためです。この解放は手順を 1 つでも誤ると PVC を掴んだ Pod のファイナライザやテナントの ValidatingAdmissionPolicy でハマりやすいので、確実に済ませたい場合は次のスクリプトを使えます。

`--storage` には `fsx` と `openzfs` と `efs` のいずれかを指定します。解放できる状態であれば対象の PV を `Available` へ戻します。ただしその PV を待っている PVC が別にいる場合は `Available` を経ずにそちらへ再バインドし、それも成功として終わります (使われている状態に戻っただけなので、意図どおりです)。逆に、PVC を掴んでいる Pod が Deployment などのコントローラ管理下にある場合は、消しても作り直されて同じ PVC を掴み直すため、`--force` を付けても停止します。そのコントローラを先に止めてから再実行してください。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/scripts
./05-release-pv.sh --storage fsx
```

`05-release-pv.sh` は、PVC が既に消えている残骸なら claimRef を外して `Available` に戻し、PVC がまだ生きている場合は誤って壊さないよう `--force` を要求します。`reclaimPolicy` が `Retain` 以外の PV は拒否するため、データを削除してしまう事故も防げます。以下は、このスクリプトが内部で行っている手動手順です。
:::

手動で行う場合は、名前を直接書かず、PV から `claimRef` を引いて変数で扱います。

```bash
PV=fsx-training
PVC_NS=$(k get pv "$PV" -o jsonpath='{.spec.claimRef.namespace}')
PVC_NAME=$(k get pv "$PV" -o jsonpath='{.spec.claimRef.name}')
echo "この PV を掴んでいる PVC: ${PVC_NS}/${PVC_NAME}"
```

状態が `Bound` の場合は、掴んでいる PVC を消します。`reclaimPolicy=Retain` なので PV は消えず `Released` になります。

```bash
k delete pvc "$PVC_NAME" -n "$PVC_NS"
```

同じ namespace で同名の PVC を再作成して使い続ける場合は、`claimRef` の `uid` と `resourceVersion` だけを外します。

```bash
k patch pv "$PV" --type=json \
  -p '[{"op":"remove","path":"/spec/claimRef/uid"},{"op":"remove","path":"/spec/claimRef/resourceVersion"}]'
```

別の namespace の PVC で使いたい場合は、`claimRef` 全体を外して完全な `Available` に戻します。`uid` だけ外しても `claimRef` の namespace と name が残っていると、別 namespace の PVC は弾かれます。

```bash
k patch pv "$PV" --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'
```

`k delete pvc` が返ってこないときは、その PVC を使っている Pod がまだ残っています。PVC には `kubernetes.io/pvc-protection` ファイナライザが付いており、参照する Pod が 1 つでもある限り削除は完了しません。この場合は Pod を先に消します。

::::details PVC の削除が Pod で止まるときの対処
まず PVC を参照している Pod を洗い出します。

```bash
k get pods -n "$PVC_NS" -o json \
  | PVC_NAME="$PVC_NAME" python3 -c "
import json, sys, os
name = os.environ['PVC_NAME']
for p in json.load(sys.stdin).get('items', []):
    for v in p.get('spec', {}).get('volumes', []):
        if v.get('persistentVolumeClaim', {}).get('claimName') == name:
            print(p['metadata']['name'], p.get('status', {}).get('phase')); break
"
```

出てきた Pod を消すとファイナライザが外れ、PVC の削除が完了します。`Succeeded` や `Failed` のまま残っているのは、作成元の Job などが消えて後始末されなくなった残骸です。Pod を直接消します。

```bash
k delete pod -n "$PVC_NS" <上で出た Pod 名> --wait=false
```

Pod 自身が `Terminating` のまま消えないこともあります。多くは Job 管理の `batch.kubernetes.io/job-tracking` ファイナライザが残っているケースで、作成元の Job が既に無いと誰も外してくれません。終了済みの残骸 Pod に対してのみ、ファイナライザを手で外します。

```bash
k patch pod -n "$PVC_NS" <残骸 Pod 名> -p '{"metadata":{"finalizers":null}}' --type=merge
```

Experiment01 のマルチテナント ValidatingAdmissionPolicy を導入している場合、この patch が「pod may not set spec.nodeName directly」で拒否されることがあります。対象 namespace に一時的に `tenantpools.dev/excluded=true` ラベルを付けて VAP の対象から外し、patch を実行してからラベルを戻します。

```bash
k label namespace "$PVC_NS" tenantpools.dev/excluded=true --overwrite
k patch pod -n "$PVC_NS" <残骸 Pod 名> -p '{"metadata":{"finalizers":null}}' --type=merge
k label namespace "$PVC_NS" tenantpools.dev/excluded-
```
::::

:::message
PersistentVolume と PersistentVolumeClaim の namespace の考え方を整理しておきます。PV はクラスタスコープのリソースで namespace を持ちません。namespace を持つのは PVC のほうで、Pod は自分と同じ namespace の PVC しか参照できません。つまり「PVC と、それをマウントする Pod は必ず同じ namespace に置く」「PV はどの namespace の PVC からでも `volumeName` でバインドできる共通の存在」と理解しておくと混乱しません。本 book は作業用 namespace を `distai` に統一しているので、PVC も Pod も `distai` に作ります。`default` などにうっかり作ると、Basic11 の `04-teardown.sh --namespace distai` が対象にせず消し漏らし、Bound な PV が残って `terraform destroy` を止める原因になります。
:::

## 4. Amazon FSx for Lustre に書き込み、Pod を作り直してもデータが残ることを確認する

PV は Terraform で作られていますが、PVC は手動で作ります。静的 PV に名前でバインドするため `volumeName` に PV 名を、`storageClassName` に空文字を指定します。

```bash
k apply -n "$NAMESPACE" -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: fsx-claim
spec:
  accessModes: ["ReadWriteMany"]
  storageClassName: ""
  volumeName: fsx-training
  resources:
    requests:
      storage: 4800Gi
EOF
k get pvc fsx-claim -n "$NAMESPACE"
```

`Bound` になったら、ファイルを書き込むテスト Pod を実行します。

```bash
k run fsx-test -n "$NAMESPACE" --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"fsx-test","image":"busybox","command":["sh","-c","echo hello-fsx > /mnt/fsx/test.txt && cat /mnt/fsx/test.txt"],"volumeMounts":[{"name":"fsx","mountPath":"/mnt/fsx"}]}],"volumes":[{"name":"fsx","persistentVolumeClaim":{"claimName":"fsx-claim"}}]}}'
k wait --for=jsonpath='{.status.phase}'=Succeeded pod/fsx-test -n "$NAMESPACE" --timeout=180s
k logs fsx-test -n "$NAMESPACE"
```

初回はイメージの取得とそのノードでの初回マウントに数十秒かかり、Pod はしばらく `ContainerCreating` にとどまります。これは正常な待ち時間なので、上の `k wait` で完了を待ってからログを見ます。Pod のログに `hello-fsx` が出れば、マウントと書き込みが成功しています。続いて Pod を削除し、別名の Pod から同じファイルを読み出します。

```bash
k delete pod fsx-test -n "$NAMESPACE"
k run fsx-test2 -n "$NAMESPACE" --restart=Never --image=busybox \
    --overrides='{"spec":{"containers":[{"name":"fsx-test2","image":"busybox","command":["cat","/mnt/fsx/test.txt"],"volumeMounts":[{"name":"fsx","mountPath":"/mnt/fsx"}]}],"volumes":[{"name":"fsx","persistentVolumeClaim":{"claimName":"fsx-claim"}}]}}'
k wait --for=jsonpath='{.status.phase}'=Succeeded pod/fsx-test2 -n "$NAMESPACE" --timeout=180s
k logs fsx-test2 -n "$NAMESPACE"
```

別名の Pod でも `hello-fsx` が読み出せます。Pod やノードが入れ替わっても、共有ストレージ上のデータが残り続けることが確認できます。

## 5. 検証用リソースを削除する

検証が終わったら、テスト Pod と PVC を削除しておきます。Amazon FSx for Lustre を無効化する場合は、この削除を先に済ませておかないと、Bound な PV の削除がファイナライザで止まり `terraform apply` や `terraform destroy` が詰まることがあります。

```bash
k delete pod fsx-test2 -n "$NAMESPACE" --ignore-not-found
k delete pvc fsx-claim -n "$NAMESPACE" --ignore-not-found
```

Pod を先に、PVC を後に消すこの順序が大事です。逆にすると `k delete pvc` が Pod の残るあいだファイナライザで止まります。namespace 内の PVC をまとめて片付けたい場合は、Basic11 の `04-teardown.sh` に `--delete-pvcs` を付けると、その namespace の PVC を storage の種類によらず一括削除できます。削除が止まったときの原因の切り分けは、手順 3 の details で示した Pod の調べ方と同じです。

PV 自体は Terraform が管理しているため、この削除では消えません。`reclaimPolicy` が `Retain` なので、Bound だった PVC を削除しても PV は `Released` になります。同じ PV に再びバインドしたい場合の復旧手順は Basic02 で扱ったものと同じです。

:::message alert
Amazon FSx for Lustre は有効な間、プロビジョニングした容量分の課金が常時発生します。PERSISTENT_2 SSD は使用量ではなく容量に対して課金され続けるため、学習ジョブを実行する期間だけ `fsx_enabled = true` で apply し、終わったら `false` に戻して apply する運用が費用を抑えます。ただし無効化するとファイルシステム上のデータはすべて削除されるため、必要なチェックポイントは事前に Amazon S3 などへ退避してください。
:::

:::message
マルチ AZ で ReadWriteMany のキャッシュが必要な場合は、opt-in の Amazon EFS を選べます。`terraform.tfvars` で `efs_enabled = true` にして apply すると、ファイルシステムと private subnet ごとのマウントターゲット、静的 PV が作られ、Karpenter がノードを別 AZ に入れ替えても同じキャッシュを読み続けられます。CSI ドライバ自体は既定で常設されているため、有効化するのはファイルシステム本体だけです。Amazon EFS の詳しい構成と用途は、マルチ AZ での NEFF キャッシュ共有が要点になる Neuron の章で扱います。
:::

# まとめ

本章では、Karpenter によるノード入れ替えから独立したデータ層として Amazon FSx for Lustre を構成しました。既存ファイルシステムには静的プロビジョニングを用いる点、`volumeAttributes` のキーが小文字でないと読まれない点、`reclaimPolicy` は `Retain` が正しい点を押さえておけば、以降の章で GPU/Neuron ワークロードがこの共有ストレージを安心して利用できます。さらに高いスループットが必要な場合は EFA 有効化という選択肢があり、この実装では `fsx_efa_enabled` で切り替えられます。EFA 有効時はメタデータ 6000 IOPS・容量 4800 GiB・単一 AZ・ノード側の EFA 設定という制約を伴い、GPU 学習では NCCL 通信との EFA デバイス分離も検討することになります。

# 参考資料

- [Amazon FSx for Lustre CSI Driver](https://github.com/kubernetes-sigs/aws-fsx-csi-driver)
- [Amazon FSx for Lustre ユーザーガイド](https://docs.aws.amazon.com/fsx/latest/LustreGuide/what-is.html)
- [Amazon FSx for Lustre のクライアント単体スループット](https://docs.aws.amazon.com/fsx/latest/LustreGuide/performance.html)
- [Amazon FSx for Lustre で EFA を使う](https://docs.aws.amazon.com/fsx/latest/LustreGuide/configure-efa-clients.html)
- [Amazon FSx for Lustre Technical Enablement Workshop](https://catalog.us-east-1.prod.workshops.aws/workshops/1152c25d-552e-4b9f-8cd0-875910071c54/en-US)
- [Amazon EFS CSI Driver](https://github.com/kubernetes-sigs/aws-efs-csi-driver)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
