---
title: "Advanced02 - GDRCopy を有効にする"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.1.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.1.0)

本章では、[Basic06 - EFA でマルチノード通信を検証する](eks-efa-topology) の最後に触れた GDRCopy を実際にノードへ導入する。まず GPUDirect RDMA と GDRCopy が別物であることを整理し、EFA のマルチノード通信でそれぞれが果たす役割を押さえる。そのうえで Capacity Block の GPU AMI でなぜ GDRCopy が標準で載らないのかを見て、Terraform でノードに `gdrdrv` をロードする実装を読む。最後に GDRCopy が単体では確かに機能することを確かめたうえで、EFA のマルチノード通信でレイテンシがどう変わるのかを実機で測る。

:::message
本章は Basic06 で EFA のマルチノード通信が動いていることを前提とする。GDRCopy はその通信の一部を最適化する補助機構であり、EFA を有効にする手順そのものではない。
:::

# 解説

## GPUDirect RDMA と RDMA と GDRCopy の関係

用語が似ていて紛らわしいので、最初に三つの言葉を分けて定義する。

RDMA（Remote Direct Memory Access）は、リモートホストのメモリに CPU を介さず直接アクセスするネットワークの機構である。EFA は SRD（Scalable Reliable Datagram）という独自プロトコルでこの RDMA を提供する。片側が相手のメモリを直接 read/write するという意味論は InfiniBand や RoCE とほぼ同等だが、トランスポートの実装は SRD で異なり、対応する RDMA 操作もインスタンス世代に依存する。

GPUDirect RDMA は、その RDMA の転送先・転送元をホストメモリではなく GPU メモリに置き換えたものである。NIC（EFA）が PCI Express 経由で GPU メモリへ直接 DMA する。RDMA という土台の上で DMA 対象を GPU まで伸ばした拡張であり、大きなテンソルを転送する分散学習の集合通信は、この GPUDirect RDMA が帯域の本体を担う。

GDRCopy は、この二つとは別のライブラリである。GPU の BAR1 領域を CPU のアドレス空間にマッピングし、CPU が memcpy で GPU メモリを読み書きできるようにする。NIC が直接 DMA する GPUDirect RDMA とは対照的に、CPU が主導するコピー手段であり、主に小さなメッセージの受信コピーや制御パスのレイテンシ削減に使われる。

三者の関係を図にまとめると次のようになる。

![GPUDirect RDMA と GDRCopy の役割分担](/images/books/eks-distributed-ai/gdrcopy-roles.png)

## EFA の通信で GDRCopy がどこに効くか

libfabric の EFA プロバイダは、受信したデータを GPU メモリへ書き込むときにコピー経路を選ぶ。GDRCopy が使える場合は CPU が BAR1 マッピング経由で直接コピーする。使えない場合は、EFA デバイス経由のループバック read でホストのバウンスバッファに一度受けてから GPU メモリへコピーする代替経路にフォールバックする。これは libfabric 公式のビルドドキュメントに明記されている挙動である（[Building the EFA provider](https://github.com/ofiwg/libfabric/blob/main/prov/efa/docs/building.md) の `--with-gdrcopy` の項）。

ここで重要なのは、GDRCopy が効くのは小さいメッセージに限られるという点である。libfabric の EFA プロバイダは、GDRCopy を使う受信コピーのサイズ上限を環境変数で持ち、本書執筆時点のデフォルトは 32 KB 程度である。それより大きいバルク転送は GPUDirect RDMA が NIC から GPU メモリへ直接書き込むので、GDRCopy の有無に関係なく同じ経路を通る。したがって GDRCopy は小メッセージのコピーレイテンシを詰める補助であって、all-reduce のような大きなテンソルの集合通信の帯域には効かない。この点は本章の最後で実測して確かめる。

## GPU Operator では入らない理由

このワークショップのクラスタは、NVIDIA ドライバが AMI にプリインストールされた Capacity Block の GPU AMI を使う。そのため NVIDIA GPU Operator は `driver.enabled=false`（ドライバを Operator が管理しない）で動かしている。[Basic04](eks-accelerator-pools) で導入したこの構成が、GDRCopy にそのまま響く。

GPU Operator にも GDRCopy を有効化する `gdrcopy.enabled` というオプションがある。しかしこの GDRCopy コンポーネントは、Operator が管理するドライバ用 DaemonSet の中のサイドカーコンテナとして実装されている。ドライバを Operator が管理しない構成では、そのドライバ DaemonSet 自体が存在しないため、GDRCopy のサイドカーも起動しない。`gdrcopy.enabled=true` にしても何も起きない。この挙動は執筆時点で検証した GPU Operator のバージョンでのもので、将来変わる可能性はあるが、サイドカーがドライバ DaemonSet に同居する構造そのものは NVIDIA が [GPU Operator のドキュメント](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/) で説明している。

つまり AMI プリインストールドライバの構成では、GDRCopy はノード側で別途ロードするしかない。これが本章で扱う実装の出発点である。

## /dev/gdrdrv とは何か

GDRCopy が BAR1 マッピングを行うには、`gdrdrv` というカーネルモジュールがロードされ、そのインターフェースである `/dev/gdrdrv` というキャラクタデバイスが存在している必要がある。ユーザ空間のライブラリ（`libgdrapi`）はこのデバイスファイルを開いて GPU メモリの mmap をカーネルに要求する。EFA を使うとき、この GDRCopy の初期化を実際に行うのは libfabric の EFA プロバイダである。プロバイダは初期化時に `/dev/gdrdrv` を開けるかどうかで GDRCopy が使えるかを判定する。Basic06 のログに出た `Failed to open gdr handle` は、この `/dev/gdrdrv` が無くて初期化に失敗したという通知だった。

`gdrdrv` はドライバ本体（`nvidia.ko`）とは別のモジュールで、GPU ドライバをインストールしても自動では載らない。EKS の AL2023 GPU AMI（本書執筆時点のバージョン）にも `gdrdrv` は含まれておらず、ブート時に自動ロードもされない。したがって GDRCopy を使うには、このモジュールを自分でノードに載せる仕組みが要る。

:::message
GPUDirect RDMA（バルク転送の本体）は `gdrdrv` とは無関係に動く。EFA カーネルドライバが `nvidia.ko` の提供する GPU peer-to-peer DMA の関数を使って GPU メモリへの直接 DMA を確立するため、`gdrdrv` が無くても NCCL のログには `GPU Direct RDMA Enabled for HCA` が出る。`gdrdrv` が要るのはあくまで GDRCopy の側である。
:::

## AL2023 は gdrdrv をパッケージで提供する

`gdrdrv` を載せる方法として、NVIDIA の GDRCopy ソースを取得してカーネルモジュールをビルドする道もあるが、Amazon Linux 2023 ではもっと簡単な経路がある。AL2023 の標準リポジトリが `gdrcopy-kmod` という DKMS パッケージを提供しており、`dnf install -y gdrcopy-kmod` するだけでモジュールがビルドされる。しかもこのパッケージは `gdrcopy.service` という systemd ユニットを同梱していて、インストール時に自動で有効化される。このユニットは NVIDIA モジュールがロードされたあとに `gdrdrv` をロードし、`/dev/gdrdrv` を作り直す処理を毎回のブートで実行する。

この事実が実装を大きく単純化する。ノードに一度 `dnf install -y gdrcopy-kmod` を実行しさえすれば、モジュールのロードと再起動をまたいだ永続化は同梱の systemd ユニットが引き受ける。カーネルソースを自前でビルドする必要も、独自の modules-load 設定を書く必要もない。

# 実装

以上を踏まえ、`infra/eks` では `gdrcopy_mode` という変数一つで GDRCopy の導入方式を選べるようにしている。対象モジュールは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) である。

```hcl
# variables.tf
variable "gdrcopy_mode" {
  # "off"      : 何もしない（既定）。/dev/gdrdrv は無く、NCCL は GDRCopy 初期化に失敗する
  #              が、これは無害でバルク転送には影響しない。
  # "userdata" : 推奨。EC2NodeClass の userData で gdrcopy-kmod を dnf install する。
  #              宣言的で常駐 Pod を持たない。ノードの再作成で適用される。
  # "daemonset": 稼働中のノードに後から載せる場合の代替。
  type    = string
  default = "off"
}
```

## 方式 1: userData で入れる（推奨）

推奨は `userdata` である。EC2NodeClass の userData に、GDRCopy をインストールするシェルスクリプトを cloud-init の MIME マルチパートとして差し込む。ノードの起動時に一度だけ `dnf install -y gdrcopy-kmod` が走り、あとは同梱の `gdrcopy.service` がロードと永続化を担う。

```hcl
# karpenter-resources.tf（抜粋）
gdrcopy_install_script = <<-EOSH
  #!/bin/bash
  if dnf install -y gdrcopy-kmod; then
    systemctl enable --now gdrcopy.service || echo "gdrcopy: service start failed" >&2
  else
    echo "gdrcopy: dnf install gdrcopy-kmod failed; /dev/gdrdrv will be absent" >&2
  fi
EOSH
```

インストールに失敗してもノードの起動は止めない。GDRCopy は必須要件ではないため、`dnf install` がこけてもノードは正常に join させ、失敗はログに残すだけにしている。この install スクリプトは nvidia デバイスプラグインを使う GPU プールにだけ差し込まれる。Neuron プールの userData に混ぜても `gdrcopy-kmod` のインストールが空振りするだけなので、プールの `device_plugin` を見て nvidia のときだけ適用する。

## 方式 2: DaemonSet で入れる（稼働中ノード向けの代替）

ノードをすでに起動していて再作成できない場合の代替として、`daemonset` 方式も用意している。特権を持つ initContainer がホストに `chroot` して `dnf install` と `modprobe` を実行し、`gdrdrv` をロードしたら終了する。常駐するメインコンテナは非特権の待機プロセスにしてあり、ホストのファイルシステムもマウントしない。特権とホストルートマウントを一度きりの initContainer に閉じ込めることで、常時開いたままの攻撃面を小さくしている。

なお、この二つの方式と GPU Operator の GDRCopy サイドカーが同時に `gdrdrv` をロードしようとすると競合するため、`gdrcopy_mode` が `off` でないのに GPU Operator 側でもドライバ管理と GDRCopy を有効にした構成は、plan 時にエラーで弾くようにしている。

:::message
将来 EKS の AL2023 GPU AMI が `gdrdrv` を標準で載せるようになれば、この機能は不要になる。その時は `gdrcopy_mode` を `off` に戻すだけでよい。
:::

# ワークショップ実施

ここからは実機で GDRCopy を有効にし、その効果を測る。以降のコマンドは、これまでの章と同じく `k` で記述し、向き先と既定 namespace は Basic01 step 2 の 4 行で設定済みの前提である（ターミナルを開き直した場合はその 4 行をもう一度実行する）。手順は Basic05 で確保した GPU プールをそのまま使い、プール名やインスタンスタイプは環境変数に置いて読者の環境に読み替える。本章に載せる実測値は p5.48xlarge 2 ノード（H100 × 16）で取得したものだが、p5en など他の EFA 対応 GPU でも手順は変わらない。

## 1. 前提を確認する

- Basic05 で Capacity Block を確保済み（同一 AZ・2 台、EFA を複数枚持つ GPU インスタンス）。手順 5 の 2 ノード測定で必要
- Basic04/05 で GPU プール（Basic05 の例では `gpu-p5en`）を `accelerator-pools.auto.tfvars` に定義し `terraform apply` 済み
- Basic06 で 2 ノードの EFA 通信が動くことを確認済み。本章はその通信の一部を最適化する GDRCopy を足す章で、EFA を有効にする操作ではない
- `k` と向き先は Basic01 step 2 の 4 行で設定済み（本章のコマンドは `k` で記述する）

以降は対象プールと namespace を環境変数に置いておく。`POOL` と `ITYPE` は Basic05 で定義した自分のプール名・インスタンスタイプに読み替える（例では p5.48xlarge を使うが、p5en など他の EFA 対応 GPU でも同じ手順が通る）。

```bash
export NAMESPACE=distai
POOL=gpu-p5
ITYPE=p5.48xlarge
```

## 2. GDRCopy が無い状態を確認する

まず現状で GDRCopy が入っていないことを確認する。この時点ではまだノードが 1 台も起動していないので、`/dev/gdrdrv` はノードに出て行って確認する段階ではない。代わりに、GDRCopy の導入方式を決める `gdrcopy_mode` が既定の `off` であること（`gdrdrv-loader` の DaemonSet が存在しないこと）を確認する。`gdrcopy_mode` は入力変数なので `terraform output` には出ない。現在値は `terraform console` で引くか、`accelerator-pools.auto.tfvars` に書いていないこと（＝既定の `off`）で確認する。

`terraform console` が `"off"` を返し、`gdrdrv-loader` の DaemonSet が居なければ既定のままである。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
echo 'var.gdrcopy_mode' | terraform console
k get ds -n kube-system gdrdrv-loader 2>/dev/null || echo "no gdrdrv-loader (= off)"
```

`off` で `gdrdrv-loader` が無ければ、ノードには `gdrdrv` が載らず、NCCL は Basic06 で見た `Failed to open gdr handle` を出す状態である。

## 3. gdrcopy_mode を有効にしてノードを起こす

まず `gdrcopy_mode` を有効にして `gdrdrv` を載せる仕組みを入れる。本番運用の推奨は `userdata` だが、それはノードの再作成でしか反映されない。ここではこの後の手順で起動する GPU ノードにその場で載せるため、`daemonset` を `-var` の一時上書きで指定する。

```bash
k create namespace "$NAMESPACE" --dry-run=client -o yaml | k apply -f -

cd "$(git rev-parse --show-toplevel)"/infra/eks
terraform apply -var gdrcopy_mode=daemonset
```

:::message alert
`terraform.tfvars` に `gdrcopy_mode = "userdata"` を書き込むのは、恒久的にこの方式へ切り替えると決めたときだけにする。tfvars に書くと EC2NodeClass の userData が変わり、次回以降の `terraform apply` で Karpenter の drift 検知が既存ノードの置き換えを走らせる。Capacity Block 上で意図しないノード再作成が起きると、再取得に失敗して GPU を失うおそれがある。本章の検証は `-var` の一時上書きだけで行う。
:::

これで `gdrdrv-loader` の DaemonSet が入る。この時点ではまだ GPU ノードが 1 台も無いので配布先が無く待機状態になるが、次の手順以降で GPU Pod を投入すると Karpenter がノードを起こし、そのノードへ DaemonSet が自動配布されて initContainer が `gdrcopy-kmod` をインストールし `gdrdrv` をロードする。ロードの完了は、ノードが起動したあとに次で確認できる（2 ノードそろうのは手順 5 の測定 Pod を投入したあとになる）。

```bash
k -n kube-system logs -l app=gdrdrv-loader -c load-gdrdrv --tail=-1 \
  | grep -E "verified|already loaded"
```

各ノードで `verified: gdrdrv loaded, /dev/gdrdrv present`（または `already loaded`）が出れば成功である。

:::message
Basic06 では hugepages を要求しない warmup Pod で先にノードを起こしていたが、本章で使う GPU Pod（手順 4 の `copylat` プローブと手順 5 の torchrun 測定）はどちらも hugepages を要求しないので、warmup を挟まずそのままノードを起こせる。hugepages を要求する `ncclSshd` などを使う場合に warmup が要る理由と段取りは、Basic06 の details にまとめてある。
:::

## 4. GDRCopy が単体で機能することを確認する（ポジティブコントロール）

効果を測る前に、GDRCopy そのものが正しく動いていることを確かめておく。これをやらないと、後の比較で差が出なかったときに「効かない」のか「そもそも GDRCopy が動いていない」のかを区別できない。GDRCopy に付属する `copylat` は、CPU が BAR1 マッピング経由で GPU メモリへ書き込むレイテンシを測る。GPU 向けの DLC イメージにはこのツールが含まれるので、`/dev/gdrdrv` をマウントした GPU Pod を 1 つ立てて実行する。

```bash
cat <<EOF | k apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: gdrcopy-probe
  namespace: $NAMESPACE
spec:
  restartPolicy: Never
  nodeSelector: { node-role: $POOL }
  tolerations:
    - { key: nvidia.com/gpu, operator: Exists, effect: NoSchedule }
    - { key: capacity-reservation, operator: Exists, effect: NoSchedule }
  containers:
    - name: probe
      image: 763104351884.dkr.ecr.us-east-2.amazonaws.com/pytorch-training:2.10.0-gpu-py313-cu130-ubuntu22.04-ec2
      command: ["sleep", "3600"]
      securityContext: { privileged: true }
      resources: { limits: { nvidia.com/gpu: "1" } }
      volumeMounts:
        - { name: gdrdrv, mountPath: /dev/gdrdrv }
  volumes:
    - { name: gdrdrv, hostPath: { path: /dev/gdrdrv, type: CharDevice } }
EOF

k wait --for=condition=ready pod/gdrcopy-probe -n "$NAMESPACE" --timeout=10m
k exec gdrcopy-probe -n "$NAMESPACE" -- copylat
```

`image` は Basic06 の NCCL 測定で使った DLC に読み替える（レジストリのアカウント ID は同じで、リージョンとタグのバージョンサフィックスは自分の環境に合わせる。ここでは `copylat` を実行できればよいので、`pytorch-training` 系であればタグの細部は問わない）。`hostPath` でマウントしたキャラクタデバイス `/dev/gdrdrv` にコンテナ内からアクセスするため `privileged: true` を与えている点に注意する。実機出力は次のとおり。

```text
Test                    Size(B)   Avg.Time(us)
gdr_copy_to_mapping           1       0.2951
gdr_copy_to_mapping           8       0.2837
gdr_copy_to_mapping         256       0.2833
gdr_copy_to_mapping        1024       0.3186
gdr_copy_to_mapping        4096       0.4793
gdr_copy_to_mapping        8192       0.7943
```

1 バイトで 0.30 us、8 KB でも 0.79 us と、CPU から GPU メモリへの直接コピーが非常に低いレイテンシで動いている。GDRCopy が BAR1 マッピングを確立し、コピー経路として機能していることの直接の証拠である。この 0.3 us オーダーという値を覚えておく。次のマルチノード測定で、この最適化がなぜ表に出てこないのかの鍵になる。確認が終わったら `k delete pod gdrcopy-probe -n "$NAMESPACE"` で消しておく。

## 5. マルチノード通信でレイテンシを測る

GDRCopy が効くとすれば、小さいメッセージの受信コピーである。そこで小メッセージ中心の 2 ノード間 point-to-point レイテンシと all-reduce レイテンシを、`gdrdrv` をロードした状態（GDRCopy 有効）とアンロードした状態（無効）で測って並べる。

測定は 2 ノード 16 GPU の NCCL 通信で行う。手順 4 のプローブが 1 台目を起こしているので、torchrun の 2 ノード測定 Pod を投入するともう 1 台が起動し、DaemonSet が両ノードに `gdrdrv` を載せる。torchrun で `torch.distributed` の point-to-point（`isend`/`irecv` の ping-pong）と `all_reduce` を回し、各サイズについて 50 回の往復を 1 試行として 20 試行の中央値をとり、往復時間の半分を片道レイテンシとした。GDRCopy 有効の状態でひととおり測ったあと、`gdrdrv` をアンロードして無効の状態を作り、同じ測定を繰り返す。アンロードの手順と注意点（Pod の削除、DaemonSet の停止、特権の要件）は手順 7 にまとめてある。無効状態では `/dev/gdrdrv` が消えるので、測定 Pod は `/dev/gdrdrv` をマウントしない版を各条件で作り直す。

point-to-point（ノード間 EFA、rank 0 と別ノードの rank 8 の間）の結果を次に示す。

| メッセージサイズ | GDRCopy 有効 | GDRCopy 無効 |
|---|---|---|
| 8 B | 42.8 us | 44.9 us |
| 256 B | 39.9 us | 40.1 us |
| 4 KB | 41.2 us | 41.4 us |
| 32 KB | 55.0 us | 55.1 us |
| 128 KB | 67.5 us | 67.8 us |

all-reduce（16 GPU）の結果を次に示す。

| メッセージサイズ | GDRCopy 有効 | GDRCopy 無効 |
|---|---|---|
| 1 KB | 66.2 us | 65.9 us |
| 16 KB | 101.6 us | 104.1 us |
| 32 KB | 117.1 us | 113.8 us |
| 1 MB | 94.5 us | 93.9 us |

どちらも GDRCopy の有無で差が出ていない。試行間のばらつきが数 us あるため、32 KB で無効側がわずかに速いといった逆転も現れるが、これはばらつきの範囲であって有効・無効の系統差ではない。バルク側でも、8 GB の all-reduce の busbw は有効・無効ともに 481 GB/s で一致した。

手順 4 で GDRCopy 単体は 0.3 us で動くことを確認したうえで、なぜマルチノード通信では差が消えるのか。理由は二つある。一つは、EFA のノード間 point-to-point レイテンシが 40 us 前後で、これは EFA/SRD のネットワーク往復が支配的な値だという点である。GDRCopy が入れ替えるのは受信側のコピー経路の一部で、その経路自体が 0.3 us オーダーで動く。GDRCopy 有効化で変わりうるのは、この経路をフォールバック（ループバック read でバウンスバッファ経由）から差し替えたときの差分だが、いずれの経路もマイクロ秒オーダーであり、40 us のネットワーク往復の中に埋もれてしまう。もう一つは、NCCL の集合通信は小さいメッセージでも独自の低レイテンシプロトコル（LL/LL128）や GPUDirect RDMA の直接書き込みを使い、libfabric の eager 受信コピー経路（GDRCopy が置き換わる箇所）がクリティカルパスに乗りにくいという点である。

つまり GDRCopy は「入れておくと NCCL の初期化警告が消え、libfabric の小メッセージ受信コピーが速くなる」ものであって、all-reduce や NCCL の point-to-point のレイテンシを目に見えて改善する機構ではない。GDRCopy の効果が表に出るのは、EFA のネットワークレイテンシに対してコピーコストの比率が大きくなる場面である。具体的には、libfabric を直接叩く小メッセージ主体の通信や、MoE の all-to-all のように数十 KB 級のメッセージを大量にやり取りする通信、CPU 主導で GPU メモリの小規模な読み書きを繰り返す制御パスなどが該当する。標準的な分散学習の集合通信を回すうえでは、GDRCopy を入れる前に、まず GPUDirect RDMA が効いていること（次の手順）を確認するほうが効果が大きい。

:::message
本測定は本章の構成（p5.48xlarge 2 ノード、H100 × 16、この libfabric・aws-ofi-nccl のバージョン）での結果である。GDRCopy の効果はネットワークレイテンシとメッセージサイズの比で決まるため、レイテンシのより低いファブリックや、より小さいメッセージ主体のワークロードでは差が出る可能性がある。読者の環境で効果を確かめるには、手順 4 の `copylat` で GDRCopy 単体の動作を確認したうえで、自分の通信パターンで有効・無効を測るとよい。
:::

## 6. GPUDirect RDMA が効いていることを確認する

GDRCopy の有無に関わらず、EFA のバルク転送を支える GPUDirect RDMA が効いていることは NCCL のログで確認できる。この確認は NCCL ジョブを一度実行したあとに行う。`GPU Direct RDMA Enabled` は NCCL が GPU メモリを NIC に登録したときに出るログなので、ジョブを走らせる前や登録前のノードでは出ない。Basic06 の NCCL 測定ジョブ（`ncclTrainjob`）を流したあとに、その Pod のログを見る。

```bash
k -n "$NAMESPACE" logs -l jobset.sigs.k8s.io/jobset-name=nccl-trainjob --tail=-1 \
  | grep "GPU Direct RDMA Enabled"
```

```text
NET/Libfabric : GPU Direct RDMA Enabled for HCA 0 'rdmap85s0'
NET/Libfabric : GPU Direct RDMA Enabled for HCA 1 'rdmap87s0'
```

このクラスタの GPU ノードでは、`nvidia-peermem` という別モジュールを使わずに、NVIDIA のオープンソースカーネルモジュール（`nvidia.ko`）自身が公開する GPU peer-to-peer DMA の関数を EFA ドライバが直接使って GPUDirect RDMA を確立している。ホスト側を覗くと、`nvidia-peermem` はロードされていないのに EFA ドライバが peer memory を獲得している。ホストのカーネルモジュール一覧とカーネルログはノード上でしか見えないので、`hostPID` と host ルートマウントを持つ使い捨ての特権 Pod を 1 つ立てて `chroot` で確認する（`NODE` は対象ノード名に読み替える）。

```bash
NODE=$(k get nodes -l node-role=$POOL -o jsonpath='{.items[0].metadata.name}')
cat <<EOF | k apply -f -
apiVersion: v1
kind: Pod
metadata: { name: p2p-check, namespace: $NAMESPACE }
spec:
  restartPolicy: Never
  hostPID: true
  nodeSelector: { kubernetes.io/hostname: $NODE }
  tolerations:
    - { key: nvidia.com/gpu, operator: Exists, effect: NoSchedule }
    - { key: capacity-reservation, operator: Exists, effect: NoSchedule }
  containers:
    - name: c
      image: public.ecr.aws/amazonlinux/amazonlinux:2023
      securityContext: { privileged: true }
      command: ["/bin/bash","-c","chroot /host bash -c 'lsmod | grep -E \"nvidia_peermem|gdrdrv\" || echo no-peermem; dmesg | grep -i \"efa.*peer memory\" | tail -1'; sleep 60"]
      volumeMounts: [{ name: host, mountPath: /host }]
  volumes: [{ name: host, hostPath: { path: / } }]
EOF
k wait --for=condition=ready pod/p2p-check -n "$NAMESPACE" --timeout=3m
k logs p2p-check -n "$NAMESPACE"
```

```text
gdrdrv                 32768  2
nvidia              14422016  270 nvidia_uvm,gdrdrv,nvidia_modeset
efa: Acquired peer memory using P2P
```

`nvidia-peermem` の行が出ず、それでも `Acquired peer memory using P2P` が出ていれば、`nvidia.ko` 直結の経路で GPUDirect RDMA が効いている。GDRCopy とは独立にバルク転送が成立していることの裏付けになる。

## 7. 後始末をする

まず本章で立てた Pod を消す。GPU を占有したままだと後続のワークロードがスケジュールされない。

```bash
k delete pod gdrcopy-probe p2p-check -n "$NAMESPACE" --ignore-not-found
```

`gdrcopy_mode` を `off` に戻すと、Terraform が管理する `gdrdrv-loader` DaemonSet は消える。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
terraform apply -var gdrcopy_mode=off
```

ただしこれは Terraform 管理リソースの撤去であって、ホストの原状回復ではない。ノードには `gdrcopy-kmod` パッケージと有効化された `gdrcopy.service` が残り、ロード済みの `gdrdrv` も残る。`gdrcopy.service` が有効なままなので、ノードを再起動しても `/dev/gdrdrv` は復活する。これらは無害なので、通常は Basic06 と同じ `04-teardown.sh` でノードプールごと退避すれば十分である。Capacity Block のノードは予約期間の終了時に AWS 側で回収されるため、モジュールがホストに残ることを気にする必要はない。

手順 5 のように GDRCopy の有無を手作業で切り替えて測る場合にだけ、`gdrdrv` のアンロードが必要になる。その際は `/dev/gdrdrv` を開いている Pod を先にすべて消し（開いていると `Module is in use` で外せない）、`gdrdrv-loader` DaemonSet も止めてから（動いていると再ロードされる）、`CAP_SYS_MODULE` を持つ特権コンテキストで `rmmod gdrdrv` する。アンロード後は `/dev/gdrdrv` が消えるため、`hostPath` に `type: CharDevice` を指定した Pod は起動できなくなる点にも注意する。

# まとめ

本章では、EFA のマルチノード通信における GDRCopy の位置づけを整理し、実際にノードへ導入して効果を測った。GPUDirect RDMA が NIC から GPU メモリへ直接 DMA してバルク転送の帯域を担うのに対し、GDRCopy は CPU が BAR1 マッピング経由で小メッセージをコピーするための補助であり、両者は独立した別の機構である。EFA/NCCL がノード間で帯域を出すこと自体には GPUDirect RDMA で足りる。

導入面では、AMI プリインストールドライバの構成では GPU Operator の GDRCopy が使えないため、ノード側で `gdrdrv` を載せる必要があることを押さえた。AL2023 が `gdrcopy-kmod` を DKMS パッケージとして提供し、同梱の `gdrcopy.service` がロードと再起動をまたいだ永続化を引き受けるため、`dnf install` 一回で足りる。`infra/eks` ではこれを `gdrcopy_mode`（`userdata` / `daemonset`）で選べるようにした。

実測では、GDRCopy 単体は `copylat` で 0.3 us オーダーの低レイテンシコピーとして確かに動作した。それでも p5 2 ノードの point-to-point と all-reduce では、GDRCopy の有無でレイテンシに差が出なかった。EFA のノード間レイテンシが 40 us 前後で、GDRCopy が入れ替える受信コピー経路の差分がその中に埋もれるためである。GDRCopy の恩恵はネットワークレイテンシに対してコピーコストの比率が大きい通信に限られる。分散学習の集合通信を回すうえでは、GDRCopy を入れる前にまず `GPU Direct RDMA Enabled` が出ていることを確認するのが実務上の優先順位になる。

参考資料として、libfabric の EFA プロバイダのビルドオプションは [Building the EFA provider](https://github.com/ofiwg/libfabric/blob/main/prov/efa/docs/building.md) を、GPUDirect RDMA と GDRCopy の詳細は [NVIDIA GPUDirect RDMA](https://docs.nvidia.com/cuda/gpudirect-rdma/index.html) と [NVIDIA GDRCopy](https://github.com/NVIDIA/gdrcopy) を参照するとよい。実装は [infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) にある。
