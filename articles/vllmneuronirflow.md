---
title: "PyTorch の forward が Trainium バイナリになるまで vLLM Neuron のコンパイルフローを CPU で覗く"
emoji: "🧭"
type: "tech"
topics: ["AWS", "Neuron", "Trainium", "vLLM", "MLIR"]
published: false
---

## この記事について

前回の記事「vLLM Neuron プラグイン v0.21 を Trainium で実際に動かして分かったこと」では、
`torch.compile` から NEFF 生成までの流れを一言でまとめました。この記事はそこを掘り下げて、
PyTorch の `forward` が AWS Trainium の実行バイナリ（NEFF）になるまでの変換パイプラインを、
初学者向けに噛み砕いて解説します。

登場する用語は FX graph、XLA、HLO、MLIR、lowering など、コンパイラまわりのものが中心です。
どれも「知らないと呪文に見える」言葉ですが、順番に一つずつ意味を渡していきます。あわせて、
NeuronCore を使わず CPU だけで各段階を実ファイルとして観察する方法も紹介します。安価な CPU
インスタンスや手元の開発環境でも、内部で何が起きているかを自分の目で確認できます。

対象読者は、Trainium での LLM 推論に興味があるものの、コンパイラ内部の用語につまずいている方です。

:::message
この記事の内部フローとログは、vLLM Neuron `v0.21.0.1.0.0`（vLLM `0.21.0`、Neuron SDK `2.31.0`、
neuronx-cc `2.26`）を単一の `trn2.3xlarge` 上の CPU モードで実際に動かして確認したものです。
検証コードと再現手順は記事末尾のリポジトリにまとめています。
:::

## まず全体像 翻訳のリレー

細かい話に入る前に、この記事で追う流れを一枚の図で示します。ポイントは「Python のコードが、
段階的に機械寄りの表現へ翻訳されていく」という一本のリレーだと捉えることです。

![PyTorch forward が Trainium バイナリ (NEFF) になるまでの中間表現のリレー](/images/be22d1ace136a5-compile-flow.png)

左端が普段書く PyTorch の `forward`、右端が Trainium が直接実行できるバイナリ（NEFF）です。
その間に FX graph、HLO、MLIR という段階が挟まります。以降の章はこの図のどの部分を説明して
いるかを示しながら進めます。

## なぜコンパイルが要るのか

GPU で PyTorch を動かすとき、多くの人は 1 行ずつ即座に実行する「eager 実行」に慣れています。
コードを書いた順に演算が走り、結果がすぐ返ってきます。料理でいえば、レシピを 1 行ずつ読みながら
その場で作るようなものです。

一方 Trainium では、`forward` 全体を先にひとまとまりのグラフとして受け取り、まとめて最適化して
から実行します。レシピを最初に全部読んで、段取りを最適化してから調理を始めるイメージです。
この「全体を見てから最適化する」ために、Python の関数を一度グラフの形に変換する必要があります。
これがコンパイルであり、その過程で複数の中間表現を経由します。

なお、C 言語のコンパイルのように「Python 自体を機械語に翻訳する」わけではありません。翻訳する
のはモデルの `forward` に含まれる演算列（行列積や活性化関数など）です。

## 中間表現（IR）とは この記事の背骨

この記事にこれから何度も出てくる FX graph、HLO、MLIR は、すべて「中間表現（Intermediate
Representation, IR）」と呼ばれるものの一種です。ここだけ先に押さえておけば、後の章は「IR の
種類が変わっていくだけ」と理解できます。

中間表現とは、翻訳の途中経過を書き留めたメモのようなものです。人間が読む Python でもなく、
機械が直接実行するバイナリでもない、その間にある「変換しやすい形式」です。ソースコードから
いきなり機械語へ飛ぶのではなく、変換しやすい中間形式をいくつか経由することで、各段階が
それぞれの役割に集中できます。

この記事で登場する IR は次の 3 つです。段階が進むほど、機械（Trainium）に近い表現になります。

- FX graph … PyTorch の演算のまま、依存関係をグラフにしたもの
- HLO … ハードウェアに依存しない、より基本的な演算に落としたも
- MLIR … コンパイラ内部で、ハードウェア向け最適化のために IR を組み立てる枠組み

## torch.compile と TorchDynamo グラフを捕まえる

まず最初の変換です。入力は Python の `forward`、出力は FX graph です。

`torch.compile` を呼ぶと、内部で TorchDynamo という仕組みが動きます。TorchDynamo は CPython の
Frame Evaluation API（PEP 523）を使って、関数（フレーム）の評価そのものをフックし、その関数の
bytecode を解析します。かみ砕くと、モデルの `forward` の中身を読み解いて「どの演算が、どの順で、
どのデータに対して呼ばれるか」を拾い上げ、グラフとして組み立てるということです。

たとえば次のような極小モデルを考えます。

```python
import torch, torch.nn as nn

class Tiny(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc = nn.Linear(4, 3)
    def forward(self, x):
        a = self.fc(x)      # 行列積 + バイアス
        b = torch.relu(a)   # 活性化
        return b + 1.0      # 要素ごとの加算
```

補足として、Python コードの中に TorchDynamo が解析しきれない部分があると、そこでグラフが途切れます
（graph break と呼びます）。この場合、モデル全体が 1 つのグラフになるとは限らず、複数のグラフに
分かれることがあります。今回のような素直なモデルでは起きませんが、実践では意識しておくと役立つ
概念です。

## FX graph 最初の中間表現

TorchDynamo がトレースした結果は、FX graph という形式で表現されます。これが最初の中間表現です。
FX graph は計算グラフの一種で、ノードが演算、エッジ（矢印）がデータの流れを表します。ここで
「グラフ」は折れ線グラフではなく、演算の依存関係を表す図のことです。

抽象的な説明よりも実物を見た方が早いので、先ほどの `Tiny` を CPU モードでコンパイルし、FX graph
を出力してみました（手順は後述）。実際の出力がこちらです。

```text
placeholder  L_x_                          # 入力 x
placeholder  L_self_modules_fc_..._weight_ # 重み
placeholder  L_self_modules_fc_..._bias_   # バイアス
call_function  linear(x, weight, bias) -> a
call_function  relu(a)                  -> b
call_function  add(b, 1.0)              -> out
output  out
```

`forward` に書いた `fc(x)`、`relu(a)`、`b + 1.0` の 3 行が、そのまま `linear`、`relu`、`add`
という 3 つのノードになっているのが分かります。「1 演算 = 1 ノード」で、元コードとの対応が明快です。
ただしこの段階の演算は、まだ PyTorch の語彙のままです。

## XLA と HLO ハードウェア非依存の演算へ

次の変換です。入力は FX graph、出力は HLO です。

ここで XLA と HLO という 2 つの言葉が出てきます。混同しやすいので役割を先に区別します。

- XLA は「コンパイラ（翻訳する人）」の名前です。
- HLO は「XLA が使う言語（中間表現）」の名前です。

HLO は一般に High Level Operations の略と説明されます（資料によって表記に揺れがあります）。
「High」に違和感を持つ方もいますが、これは「ハードウェアの命令よりは高レベル」という意味です。
Python よりは低レベルだが、Trainium の生の命令よりは高レベル、という中間の位置づけです。

FX graph が XLA を経由して HLO に落とされると、PyTorch 独自の演算が、より基本的で
ハードウェア非依存な演算に置き換わります。先ほどの `Tiny` の HLO を実機で確認すると、次のような
対応が見られました。

- `linear` → `dot`（行列積）と `add`（バイアス加算）に分解
- `relu` → `maximum`（0 との要素ごとの最大値）
- あわせて `transpose` / `broadcast` / `reshape` などの整形演算

`relu` が `maximum` で表現されるのは象徴的です。ReLU は「0 未満を 0 にする」演算なので、
`maximum(x, 0)` と等価です。PyTorch の語彙から、より原始的な演算の組み合わせへと翻訳された、
という段階です。

## neuronx-cc の内部と MLIR

次が最後の変換です。入力は HLO、出力は NEFF です。この変換を担うのが Neuron のコンパイラ
`neuronx-cc` で、実際には次のようなコマンドで HLO を受け取り NEFF を吐きます（実機のログから）。

```bash
neuronx-cc compile graph.hlo --framework XLA --target trn2 --output graph.neff
```

この `neuronx-cc` の内部で MLIR が登場します。MLIR は、IR の共通の骨格（演算・型・属性を
表すデータ構造や構文）と、それを変換する道具立てを提供する枠組みです。特定の 1 つの IR を
指すのではなく、用途に応じた IR を組み立てるための土台だと捉えると混乱しません。関連する
用語も先に噛み砕いておきます。

- dialect（方言）… 用途別に定義された演算・型・属性の語彙セット。目的に応じた IR の種類だと
  考えればよいです。
- lowering（ローワリング）… 抽象的な表現を、より機械寄りの表現へ段階的に落としていくこと。

`neuronx-cc` の内部実装は公開されていませんが、コンパイラのログを読むと、内部のパイプラインと
MLIR パスの実行が確認できます。実機のログに出ていた処理を、順を追って並べます。

1. HLOToTensorizer … `hlo2penguin` というツールが HLO を Penguin script という内部表現に変換
   します。ここで MLIR のパス（たとえば RemoveOptimizationBarriers）が走り、HLO の命令数が
   19 から 14 に削減されるといった最適化が行われます。
2. Tensorizer … Penguin script からモデルを構築します。
3. WalrusDriver … スケジューリング、メモリ配置、Trainium 命令のコード生成を担います。
4. NeffWrapper … 生成した成果物を NEFF 形式にまとめます。

つまりコンパイラ全体のパイプラインは、ログの表現を借りると
`HLOToTensorizer → Frontend → StaticIOTranspose → WalrusDriver → NeffWrapper` という順序で
流れています。MLIR は、この一連の変換の中で「ハードウェア向けの最適化を段階的に lowering する」
基盤として使われています。

## NEFF 最終バイナリとコンパイルキャッシュ

パイプラインの終点が NEFF です。NEFF は Neuron Executable File Format の略で、Trainium 版の
`.exe` のようなものだと考えると分かりやすいです。ここまでの翻訳のリレーの成果物が、この 1 つの
バイナリに詰まっています。

NEFF を作ったら終わりではありません。実行時には Neuron Runtime がこの NEFF を NeuronCore に
ロードして走らせます。そして、この NEFF は `VLLM_CACHE_ROOT` で指定した場所にコンパイルキャッシュ
として保存されます。2 回目以降の起動では、同じ設定ならキャッシュ済みの NEFF を再利用するため、
起動が大幅に速くなります。前回の記事で「初回起動は数分かかるが 2 回目以降は速い」と書いた背景が
これです。

## CPU だけで各段階を観察する

ここまでの FX graph、HLO、NEFF は、すべて NeuronCore を使わずに CPU だけで生成・観察できます。
NeuronCore を確保していない安価な開発環境でも、内部で何が起きているかを実ファイルで確かめられます。
DLAMI 付属の venv があれば、次の手順で再現できます。

### FX graph を観察する

CPU モードでは、コンパイル backend が FX graph をそのまま実行に返します。そこにフックを差し込めば、
FX graph をそのまま表示できます。

```python
import os
os.environ["VLLM_NEURON_CPU_MODE"] = "1"   # NeuronCore を使わない
import torch, torch.nn as nn
import vllm_neuron  # "vllm_neuron" backend を登録

class Tiny(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc = nn.Linear(4, 3)
    def forward(self, x):
        return torch.relu(self.fc(x)) + 1.0

def inspect_backend(gm, example_inputs):
    gm.graph.print_tabular()   # FX graph を表形式で表示
    return gm.forward

cm = torch.compile(Tiny().eval(), backend=inspect_backend, fullgraph=True)
with torch.no_grad():
    cm(torch.randn(2, 4))
```

これを実行すると、先ほど見た `linear → relu → add` の FX graph が表形式で出力されます。

### HLO と NEFF を生成する

次に、CPU コンパイルモードで HLO と NEFF を実ファイルとして生成します。`VLLM_NEURON_CPU_COMPILE=1`
と `NEURON_PLATFORM_TARGET_OVERRIDE=trn2` を設定します。

```python
import os
os.environ["VLLM_NEURON_CPU_COMPILE"] = "1"
os.environ["NEURON_PLATFORM_TARGET_OVERRIDE"] = "trn2"
os.environ["VLLM_CACHE_ROOT"] = "/work/artifact_demo"  # 成果物の保存先
import torch, torch.nn as nn
import vllm_neuron

class Tiny(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc = nn.Linear(4, 3)
    def forward(self, x):
        return torch.relu(self.fc(x)) + 1.0

m = Tiny().eval().to("meta")               # パラメータを meta デバイスへ
cm = torch.compile(m, backend="vllm_neuron", fullgraph=True)
with torch.no_grad():
    cm(torch.randn(2, 4, device="meta"))   # 入力も meta
```

ここで登場する `meta` デバイスが、CPU で「形だけコンパイルする」ための鍵です。meta テンソルは
shape と dtype の情報だけを持ち、実データを持ちません。実際に計算するわけではなく、あくまで
コンパイルのために形状情報だけを流すので、NeuronCore もメモリ上の実データも不要になります。

コンパイルが終わると、`VLLM_CACHE_ROOT` の下に各段階の成果物が実ファイルで残ります。実機で
確認できた主なファイルは次の通りです。

- `fxgraph.txt` … FX graph
- `passes/00_original.txt` 〜 `05_*.txt` … FX graph の変換パスの各段
- `hlo_passes/step1_torch_xla_trace.hlo` 〜 `step4_*.hlo` … HLO への変換パスの各段
- `graph.hlo` … 最終的な HLO
- `command.txt` … 実際に実行された `neuronx-cc` のコマンド
- `graph.neff` … 最終バイナリ（NEFF）
- `log-neuron-cc.txt` … コンパイラのログ（前章の MLIR パスやパイプラインがここに出ます）

FX graph から HLO、そして NEFF まで、翻訳のリレーの各段が実ファイルとして手元に残るので、
一つずつ開いて中身を確かめられます。

## まとめ なぜ多段なのか

PyTorch の `forward` が Trainium のバイナリになるまで、FX graph、HLO、MLIR という中間表現を
経由することを見てきました。段階が進むほど機械に近い表現へ lowering されていき、最後に NEFF に
なります。

「なぜこんなに段階が多いのか」と感じるかもしれません。理由は役割分担です。フレームワーク側
（PyTorch、JAX など）とハードウェア側（Trainium、GPU など）を直接つなごうとすると、組み合わせの
数だけ変換を作る必要があります。間に共通の中間表現を挟むことで、各フレームワークは共通 IR まで
翻訳すればよく、各ハードウェアは共通 IR から翻訳すればよくなります。概念上は、組み合わせが
N×M から N+M に減るというのが多段構成の効きどころです（実際にはハードウェア固有の最適化などで
追加の作業は残りますが、方向性としてはこの分業が効きます）。

この記事では極小モデルで流れを追いましたが、実際の LLM でも通る道は同じです。手を動かして
各段を観察したい方は、下記のリポジトリに CPU モードでの再現手順をまとめています。中間表現が
どのように姿を変えていくかを、ぜひ自分の目で確かめてみてください。

今回の観察コードと再現手順（CPU モードで NeuronCore 不要）は
[aws-neuron-samples のリポジトリ](https://github.com/littlemex/aws-neuron-samples) で公開しています。
