---
title: "PGAS と BAR1: GPU どうしが相手のメモリに書けるまでを動かして確かめる"
emoji: "🪟"
type: "tech"
topics: ["GPU", "NVSHMEM", "RDMA", "EFA", "CUDA"]
published: false
---

## はじめに

NVSHMEM のような PGAS のライブラリでは、GPU が相手の GPU のメモリに、まるで自分のメモリのように書き込めます。その下では、PCIe を通る経路に限って、GPU のメモリを PCIe に見せる BAR1 という窓が働いています。この記事では、上の見え方から下の窓までを順にたどり、AWS の GPU インスタンスで実際に動かして確かめます。NVSHMEM そのものの入門は「[NVSHMEM 入門](https://zenn.dev/tosshi/articles/228e4225b659f7)」、NIC が GPU メモリに直接触る仕組みは「[GPUDirect RDMA 入門](https://zenn.dev/tosshi/articles/42f0ee03b328a4)」で説明しています。

## 全体の地図

![上に PGAS の見え方があり、その下に同じノードの NVLink、同じノードの PCIe、別のノードの 3 つの経路がある。PCIe と別のノードの経路は BAR1 を通って GPU メモリに届き、NVLink の経路は BAR1 を通らない](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/ead994bfcc12be/zenn/articles/ead994bfcc12be/pgas-map.png)

図は、この記事で見る層を上から並べたものです。プログラムから見えるのは一番上の PGAS の見え方だけです。実際にデータが通る経路は、相手がどこにいるかで 3 つに分かれます（この記事の g6e と g7 のように、GPU が CPU と PCIe でつながる場合）。

| 相手の場所 | 経路 | BAR1 を通るか | この記事での確認 |
|---|---|---|---|
| 同じノード、NVLink | 相手のメモリをマップし、ただの書き込みにする | 通らない | [ソースで確認](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/1a82fbfda8f321b056eb3568850f9316c5e465c5/src/nvidia/src/kernel/gpu/bus/arch/ampere/kern_bus_ga100.c#L52-L55)、実機は未計測 |
| 同じノード、PCIe | 相手のメモリをマップし、ただの書き込みにする | 相手の BAR1 を通る | put が届くことを g7.48xlarge で実測。BAR1 を通ることはドキュメントとソースで確認 |
| 別のノード | NIC が運ぶ | 相手の BAR1 を通る | g7.48xlarge の 1 台の中で NIC 経由に切り替え、put が届くことを実測。2 台の間は未計測 |

## PGAS: 全部の GPU のメモリを 1 つの住所録で

![4 つの PE がそれぞれ対称ヒープを持ち、その中の同じ場所に a と b がある。PE 2 の a は、名前と PE の番号だけで指せる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/ead994bfcc12be/zenn/articles/ead994bfcc12be/pgas-heap.png)

PGAS（Partitioned Global Address Space）は、全部の GPU のメモリを 1 つの大きな住所録のように扱う考え方です。NVSHMEM では、1 つの GPU を受け持つプロセスを PE と呼びます。全部の PE が同じ大きさの領域（対称ヒープ）を持ち、全員で同じ順に確保すれば、同じ変数は全員の同じ場所に並びます。そのため「PE 2 の a」と、名前と番号だけで相手の変数を指せます。

## 対称: 同じなのは位置

![既定の確保方式では 2 つの PE の a のアドレスが同じ値 0x10050003600 になった。確保方式を変えると 0x7f8bc0003600 と 0x7ff060003600 と値は違うが、末尾の 3600 は同じだった](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/ead994bfcc12be/zenn/articles/ead994bfcc12be/symmetric-offset.png)

対称ヒープの「対称」は、ヒープの先頭からの位置（オフセット）が全員で同じという意味です。図は、g7.48xlarge の 4 つの GPU で NVSHMEM 3.8.0 を動かし、確保した a のアドレスを表示した結果です。既定の確保方式（CUDA VMM）では、全員で同じ値になりました。CUDA VMM を切ると値はばらばらになりましたが、末尾は同じでした。NVSHMEM は起動時に各 PE のヒープの先頭を交換し、「相手の先頭 + 同じ位置」で相手の場所を計算しています（[NVSHMEM のソース](https://github.com/NVIDIA/nvshmem/blob/270759e5481b16ef5a71930e1d9b8df184cd7072/src/host/mem/mem_heap.cpp#L2389-L2410)）。

:::details 実行したコマンドと出力（g7.48xlarge、NVSHMEM 3.8.0）
4 つのプロセスを立て、それぞれが 1 つの GPU を受け持ちます。プログラムは記事の最後の details にあります。

```bash
export LD_LIBRARY_PATH=/root/nv13/nvshmem/lib:$LD_LIBRARY_PATH NVSHMEM_REMOTE_TRANSPORT=none
rm -f /dev/shm/nvshmem_uid
for i in 0 1 2 3; do RANK=$i NRANKS=4 ./heap_uid > out$i.txt 2>&1 & done; wait
grep -h "a =" out*.txt
```

```text
PE 0 (GPU 0): a = 0x10050003600  b - a = 1048576 bytes
PE 1 (GPU 1): a = 0x10050003600  b - a = 1048576 bytes
PE 2 (GPU 2): a = 0x10050003600  b - a = 1048576 bytes
PE 3 (GPU 3): a = 0x10050003600  b - a = 1048576 bytes
```

CUDA VMM を切った場合です。

```bash
export NVSHMEM_DISABLE_CUDA_VMM=1
rm -f /dev/shm/nvshmem_uid
for i in 0 1 2 3; do RANK=$i NRANKS=4 ./heap_uid > out$i.txt 2>&1 & done; wait
grep -h "a =" out*.txt
```

```text
PE 0 (GPU 0): a = 0x7f8bc0003600  b - a = 1048576 bytes
PE 1 (GPU 1): a = 0x7ff060003600  b - a = 1048576 bytes
PE 2 (GPU 2): a = 0x7f6460003600  b - a = 1048576 bytes
PE 3 (GPU 3): a = 0x7f61a4003600  b - a = 1048576 bytes
```
:::

## 同じノードの相手: マップして、ただの書き込みにする

![PE 0 のアドレス空間に、自分のヒープの後ろへ PE 1、PE 2、PE 3 のヒープが 256 GiB おきにマップされる。PE 1 への put は、マップした場所への書き込みになり、GPU 1 のメモリに届く](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/ead994bfcc12be/zenn/articles/ead994bfcc12be/peer-map.gif)

同じノードの GPU どうしなら、NVSHMEM は相手のヒープを自分のアドレス空間にマップします。GIF は、PE 0 から見たアドレスを実測の値で描いたものです。自分のヒープの後ろに、相手 3 つのヒープが 256 GiB ずつずらして並びました（[確保のコード](https://github.com/NVIDIA/nvshmem/blob/270759e5481b16ef5a71930e1d9b8df184cd7072/src/host/mem/mem_heap.cpp#L981-L988)）。マップした後は、相手への put（相手のメモリに値を書く操作）が GPU のただの書き込み命令になります。4 つの PE が隣に自分の番号を書くと、全員が正しい番号を受け取りました。

:::details 実行したコマンドと出力
相手のヒープを指すアドレスを、各 PE から問い合わせた結果です。

```text
PE 0:   nvshmem_ptr(a, PE 0) = 0x10050003600
PE 0:   nvshmem_ptr(a, PE 1) = 0x14050003600
PE 0:   nvshmem_ptr(a, PE 2) = 0x18050003600
PE 0:   nvshmem_ptr(a, PE 3) = 0x1c050003600
PE 1:   nvshmem_ptr(a, PE 0) = 0x1c050003600
PE 1:   nvshmem_ptr(a, PE 2) = 0x14050003600
```

隣の PE に自分の番号を put した結果です。

```text
PE 0 received 3 via put
PE 1 received 0 via put
PE 2 received 1 via put
PE 3 received 2 via put
```

NVSHMEM_DEBUG=INFO を付けて同じ起動をすると、直接つながる PE の一覧が出ます。

```text
NVSHMEM INFO P2P connected list: 0 1 2 3
```
:::

## PCIe の直接の読み書きは機種による

![g6e.12xlarge の 4 枚の L40S では、どの組も PCIe の直接の読み書きが Not supported だった。g7.48xlarge の 8 枚の RTX PRO 4500 では、すべての組が OK だった](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/ead994bfcc12be/zenn/articles/ead994bfcc12be/p2p-matrix.png)

NVLink の無い機種では、GPU どうしの直接の書き込みは PCIe を通ります。ただし、それが許されるかは機種で変わります。g6e.12xlarge では、どの組も「Not supported」でした。そのため 4 つの GPU に 1 つずつ PE を置いた NVSHMEM は、「相手の GPU に届かない」と言って起動しませんでした。g7.48xlarge では、8 枚のすべての組で直接の読み書きができました。

:::details 実行したコマンドと出力
```bash
nvidia-smi topo -p2p rw
```

g6e.12xlarge（L40S × 4、ドライバ 580.178.04）です。

```text
 	GPU0	GPU1	GPU2	GPU3
 GPU0	X	NS	NS	NS
 GPU1	NS	X	NS	NS
 GPU2	NS	NS	X	NS
 GPU3	NS	NS	NS	X
```

g6e.12xlarge で 4 PE の NVSHMEM を起動したときのエラーです。

```text
./src/host/topo/topo.cpp:746: [GPU 0] Peer GPU 1 is not accessible, exiting ...
./src/host/init/init.cu:1328: error status: 3 (NVSHMEMX_ERROR_NOT_SUPPORTED) building transport map failed
```

g7.48xlarge（RTX PRO 4500 Blackwell Server Edition × 8、ドライバ 595.91.07）では、すべての組が OK でした。
:::

## BAR1: GPU メモリを PCIe に見せる窓

![PCIe のアドレス空間に、GPU の BAR0 16 MiB、BAR1 64 GiB、BAR3 32 MiB が並ぶ。BAR1 は GPU メモリ 45 GiB への窓で、CPU や NIC やほかの GPU はここを読み書きする](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/ead994bfcc12be/zenn/articles/ead994bfcc12be/bar1-window.png)

PCIe の装置は、自分のレジスタやメモリを、PCIe のアドレスの区画として見せます。この区画を BAR（Base Address Register）と呼びます。GPU では、BAR0 が制御用のレジスタ、BAR1 が GPU メモリへの窓です。[NVIDIA のドキュメント](https://docs.nvidia.com/cuda/gpudirect-rdma/index.html)は、PCIe の装置は相手の BAR のアドレスへ、システムメモリと同じように読み書きできると書いています。g6e.12xlarge の L40S では、BAR1 が 64 GiB で、GPU メモリの 45 GiB より大きくなっていました。

:::details 実行したコマンドと出力（g6e.12xlarge）
```bash
lspci -vv -s 38:00.0 | grep Region
nvidia-smi -q -d MEMORY -i 0
```

```text
	Region 0: Memory at 93000000 (32-bit, non-prefetchable) [size=16M]
	Region 1: Memory at 23000000000 (64-bit, prefetchable) [size=64G]
	Region 3: Memory at 25040000000 (64-bit, prefetchable) [size=32M]
```

```text
    FB Memory Usage
        Total                                          : 46068 MiB
    BAR1 Memory Usage
        Total                                          : 65536 MiB
        Used                                           : 1 MiB
        Free                                           : 65535 MiB
```

g7.48xlarge では、GPU メモリ 32623 MiB に対して BAR1 が 32768 MiB でした。
:::

## NIC が GPU メモリに届く経路

![準備として、ヒープを NIC に登録し、ドライバが BAR1 のアドレスを渡し、NIC がそれを覚える。転送のたびに、NIC は BAR1 のアドレスへ DMA で書き、GPU メモリが書き換わる](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/ead994bfcc12be/zenn/articles/ead994bfcc12be/nic-path.png)

別のノードの GPU へは、NIC がデータを運びます。そのために、転送に先立って、ヒープを NIC に登録します。登録すると、GPU のドライバは GPU メモリに当たる BAR1 のアドレスを返し、NIC はそれを覚えます（dma-buf の経路は [UVM のソース](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/1a82fbfda8f321b056eb3568850f9316c5e465c5/kernel-open/nvidia-uvm/uvm_va_range_device_p2p.c#L233-L255)、従来の経路は [ドライバのソース](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/1a82fbfda8f321b056eb3568850f9316c5e465c5/src/nvidia/src/kernel/gpu/bus/p2p.c#L479-L487)）。あとは転送のたびに、NIC がそのアドレスへ DMA（CPU を通さない直接の読み書き）で書き込みます。

## 別のノードの相手: NIC が運ぶ（1 台の中で試す）

![直接の経路を切ると、相手のアドレスを問い合わせても空になる。GPU 4 の put は CPU のプロキシスレッドに渡り、プロキシスレッドが NIC に送らせ、NIC が GPU 5 の BAR1 を通して GPU 5 のメモリに書き込む（NIC から先はソースから描いたもの）](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/ead994bfcc12be/zenn/articles/ead994bfcc12be/remote-put.gif)

2 台目の g7.48xlarge は在庫が無く起動できなかったので、1 台の中で直接の経路を切り、NIC を通る経路に切り替えました。相手のアドレスを問い合わせると、空（NULL）が返りました。**相手のメモリを直接は指せなくなっても、同じ put が NIC を通って届きます**。この設定（libfabric の転送）の NVSHMEM では、GPU の put を CPU のプロキシスレッドが受け取り、EFA の NIC に送らせます。2 つの PE は、どちらも相手の番号を正しく受け取りました。NIC が相手の BAR1 に書くところは、この実行のログでは確かめておらず、図のその部分はソースから描いています。

:::details 実行したコマンドと出力（g7.48xlarge、GPU 4 と GPU 5）
```bash
unset NVSHMEM_DISABLE_CUDA_VMM
rm -f /dev/shm/nvshmem_uid
export NVSHMEM_DISABLE_P2P=1 NVSHMEM_REMOTE_TRANSPORT=libfabric NVSHMEM_LIBFABRIC_PROVIDER=efa FI_EFA_USE_DEVICE_RDMA=1 NVSHMEM_DEBUG=INFO
for i in 0 1; do CUDA_VISIBLE_DEVICES=$((4+i)) RANK=$i NRANKS=2 ./heap_uid > lf$i.txt 2>&1 & done; wait
```

```text
PE 0:   nvshmem_ptr(a, PE 1) = (nil)
PE 0 received 1 via put
PE 1:   nvshmem_ptr(a, PE 0) = (nil)
PE 1 received 0 via put
NVSHMEM INFO Selected remote transport: libfabric
Selected provider efa, fabric efa-direct, nic rdmap176s0, hmem yes domain 1/2
```
:::

## 窓が大きい GPU は、最初から全部をマップしておく

![BAR1 が GPU メモリより小さい GPU では、登録した分だけを窓にマップするので、使用量が増え、足りなくなることがある。BAR1 が GPU メモリ全体と予約の分を収められる GPU では、起動時に GPU メモリ全体をマップしておき、登録はそれを使い回す。g7.48xlarge では使用量は 0 から 3 MiB のままだった](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/articles/ead994bfcc12be/zenn/articles/ead994bfcc12be/bar1-static.png)

窓が GPU メモリより小さい GPU では、登録した分だけを窓にマップします。NVIDIA のドキュメントによると、Kepler 世代の GPU の BAR1 は最小で 256 MiB で、そのうち 32 MiB は内部用でした。ドライバのソースでは、窓に空きが無いと登録はエラーで止まります（[空きの確認](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/1a82fbfda8f321b056eb3568850f9316c5e465c5/src/nvidia/src/kernel/gpu/bus/p2p.c#L573-L580)）。BAR1 が GPU メモリ全体と予約の分を収められる GPU では（Turing 以降で、仮想化や機密計算を使わない場合）、ドライバが起動時に GPU メモリ全体を窓にマップしておきます。ドライバのソースは、これを static BAR1 と呼んでいます（[判定](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/1a82fbfda8f321b056eb3568850f9316c5e465c5/src/nvidia/src/kernel/gpu/bus/arch/turing/kern_bus_tu102.c#L391-L417)、[使い回し](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/1a82fbfda8f321b056eb3568850f9316c5e465c5/src/nvidia/src/kernel/gpu/bus/arch/maxwell/kern_bus_gm107.c#L3009-L3014)）。g7.48xlarge では、NIC を通して 1 GiB を送り続けても、PCIe で別の GPU に 4 GiB を書いても、BAR1 の使用量は 0〜3 MiB のままでした。この結果は static BAR1 と合いますが、g7 で static BAR1 が有効かどうかは、ドライバへの問い合わせでは直接確かめていません。

:::details 実行したコマンドと出力（g7.48xlarge）
NCCL を EFA 経由に強制して、GPU 4 と GPU 5 の間で 1 GiB の all_reduce を回しました。下の Used の行は、同じ実行中に 10 秒ごとに nvidia-smi -q -d MEMORY を実行して記録したもので、最後の行が all_reduce のログです。

```bash
CUDA_VISIBLE_DEVICES=4,5 NCCL_NET_GDR_LEVEL=SYS NCCL_P2P_DISABLE=1 NCCL_SHM_DISABLE=1 NCCL_DEBUG=INFO FI_PROVIDER=efa FI_EFA_USE_DEVICE_RDMA=1 ./all_reduce_perf -b 1G -e 1G -n 100 -g 2
```

```text
before (GPU4, GPU5):  Used : 0 MiB  Used : 0 MiB
t=10s:  Used : 0 MiB  Used : 0 MiB
t=20s:  Used : 0 MiB  Used : 0 MiB
t=30s:  Used : 0 MiB  Used : 0 MiB
NCCL INFO Channel 00/0 : 0[4] -> 1[5] [send] via NET/Libfabric/0/GDRDMA
```

GPU 0 から GPU 1 へ、PCIe の直接アクセスで 4 GiB を書いたときの出力です。各行の 2 つの数字の組は GPU 0 と GPU 1 の FB の使用量で、続く 2 つの Used は左が GPU 0、右が GPU 1 の BAR1 の使用量です。

```text
before: 0, 0 MiB 1, 0 MiB  Used : 0 MiB  Used : 0 MiB
allocated, peer off: 0, 3 MiB 1, 4363 MiB  Used : 1 MiB  Used : 3 MiB
peer on + written: 0, 267 MiB 1, 4363 MiB  Used : 3 MiB  Used : 3 MiB
```
:::

## 各層の受け持ち

| 層 | 受け持ち | 働くとき |
|---|---|---|
| PGAS（NVSHMEM） | 名前と PE の番号から、相手の場所を計算する | 毎回の put と get |
| マップ | 同じノードの相手のヒープを、自分のアドレスに並べる | 起動時と確保のとき |
| CPU のプロキシスレッドと NIC（libfabric や IBRC の場合） | マップできない相手へ、データを運ぶ | 別のノードの相手への put と get。IBGDA では GPU 自身が NIC に頼む |
| BAR1 | GPU メモリを PCIe のアドレスとして見せる | PCIe と NIC の経路で毎回。static BAR1 の GPU では起動時に全体をマップする |

:::details 実演に使ったプログラム（heap_uid.cu）
起動用のプログラムを使わずに、環境変数で番号を渡したプロセスを並べて NVSHMEM を初期化します。番号 0 のプロセスが共有の識別子をファイルに書き、ほかのプロセスはそれを読みます。

```cuda
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "nvshmem.h"
#include "nvshmemx.h"

__global__ void put_next(int *a, int mype, int npes) {
    nvshmem_int_p(a, mype, (mype + 1) % npes);
    nvshmem_quiet();
}

int main(void) {
    int rank = atoi(getenv("RANK")), nranks = atoi(getenv("NRANKS"));
    int ndev = 0;
    cudaGetDeviceCount(&ndev);
    cudaSetDevice(rank % ndev);
    cudaFree(0);

    nvshmemx_uniqueid_t uid = NVSHMEMX_UNIQUEID_INITIALIZER;
    const char *path = "/dev/shm/nvshmem_uid";
    if (rank == 0) {
        nvshmemx_get_uniqueid(&uid);
        FILE *f = fopen("/dev/shm/nvshmem_uid.tmp", "wb");
        fwrite(&uid, sizeof(uid), 1, f);
        fclose(f);
        rename("/dev/shm/nvshmem_uid.tmp", path);
    } else {
        FILE *f = NULL;
        while (!(f = fopen(path, "rb"))) usleep(10000);
        fread(&uid, sizeof(uid), 1, f);
        fclose(f);
    }
    nvshmemx_init_attr_t attr = NVSHMEMX_INIT_ATTR_INITIALIZER;
    nvshmemx_set_attr_uniqueid_args(rank, nranks, &uid, &attr);
    nvshmemx_init_attr(NVSHMEMX_INIT_WITH_UNIQUEID, &attr);

    int mype = nvshmem_my_pe(), npes = nvshmem_n_pes();
    int *a = (int *)nvshmem_malloc(1 << 20);
    int *b = (int *)nvshmem_malloc(1 << 20);
    nvshmem_barrier_all();
    for (int r = 0; r < npes; r++) {
        if (r == mype) {
            int dev; cudaGetDevice(&dev);
            printf("PE %d (GPU %d): a = %p  b - a = %ld bytes\n", mype, dev, (void *)a,
                   (long)((char *)b - (char *)a));
            for (int pe = 0; pe < npes; pe++)
                printf("PE %d:   nvshmem_ptr(a, PE %d) = %p\n", mype, pe, nvshmem_ptr(a, pe));
            fflush(stdout);
        }
        nvshmem_barrier_all();
    }
    put_next<<<1, 1>>>(a, mype, npes);
    cudaDeviceSynchronize();
    nvshmem_barrier_all();
    int got = -1;
    cudaMemcpy(&got, a, sizeof(int), cudaMemcpyDeviceToHost);
    printf("PE %d received %d via put\n", mype, got);
    fflush(stdout);
    nvshmem_free(b);
    nvshmem_free(a);
    nvshmem_finalize();
    if (rank == 0) unlink(path);
    return 0;
}
```

ビルドには、PyPI の nvidia-nvshmem-cu13 3.8.0 の wheel を展開した中身を使いました。

```bash
N=/root/nv13/nvshmem
nvcc -rdc=true -gencode=arch=compute_90,code=compute_90 -gencode=arch=compute_120,code=sm_120 -I $N/include heap_uid.cu -o heap_uid -L $N/lib -lnvshmem_host -lnvshmem_device
```
:::

## まとめ

PGAS の「名前と番号で相手に書ける」は、同じノードで直接の読み書きができる相手ならマップが、それ以外の相手（別のノード、直接の読み書きができない同じノードの GPU）なら NIC が支えます。どちらも PCIe の経路では、最後は相手の GPU の BAR1 という窓を通って GPU メモリに届きます。

## 参考

- [NVIDIA GPUDirect RDMA のドキュメント](https://docs.nvidia.com/cuda/gpudirect-rdma/index.html)
- [NVSHMEM のソース（v3.8.0-0）](https://github.com/NVIDIA/nvshmem/tree/270759e5481b16ef5a71930e1d9b8df184cd7072)、[相手の場所の計算](https://github.com/NVIDIA/nvshmem/blob/270759e5481b16ef5a71930e1d9b8df184cd7072/src/host/mem/mem_heap.cpp#L2389-L2410)、[ヒープの先頭の交換](https://github.com/NVIDIA/nvshmem/blob/270759e5481b16ef5a71930e1d9b8df184cd7072/src/host/mem/mem_heap.cpp#L430-L454)
- [NVIDIA open-gpu-kernel-modules（615.78.08）](https://github.com/NVIDIA/open-gpu-kernel-modules/tree/1a82fbfda8f321b056eb3568850f9316c5e465c5)
- [AWS EFA のドキュメント（対応インスタンスの表）](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/efa.html)
