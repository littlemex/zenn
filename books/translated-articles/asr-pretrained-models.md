---
title: "音声認識のための事前学習済みモデル"
---

**翻訳元**: [Pre-trained models for automatic speech recognition - Hugging Face Audio Course](https://huggingface.co/learn/audio-course/chapter5/asr_models)

---

# 音声認識のための事前学習済みモデル

このセクションでは、`pipeline()` を使用して音声認識のための事前学習済みモデルを活用する方法について説明します。[Unit 2](../chapter2/asr_pipeline) では、`pipeline()` を音声認識タスクを実行するための簡単な方法として紹介しました。すべての前処理と後処理が内部で処理され、Hugging Face Hub 上の任意の事前学習済みチェックポイントを柔軟に実験できます。このユニットでは、さらに深く掘り下げて、音声認識モデルのさまざまな属性と、それらをどのように使用してさまざまなタスクに取り組むかを探ります。

Unit 3 で詳しく説明したように、音声認識モデルは大きく 2 つのカテゴリーに分類されます：

1. Connectionist Temporal Classification (CTC): _エンコーダーのみ_ のモデルで、線形分類（CTC）ヘッドが上に配置されています
2. Sequence-to-sequence (Seq2Seq): _エンコーダー-デコーダー_ モデルで、エンコーダーとデコーダーの間にクロスアテンション機構があります

2022 年以前は、CTC がより人気のあるアーキテクチャでした。Wav2Vec2、HuBERT、XLSR などのエンコーダーのみのモデルが、音声の事前学習 / ファインチューニングパラダイムにおいて画期的な成果を達成しました。Meta や Microsoft などの大企業は、エンコーダーを何日も何週間もかけて膨大な量のラベルなしオーディオデータで事前学習しました。その後、ユーザーは事前学習済みチェックポイントを取得し、わずか **10 分間** のラベル付き音声データで CTC ヘッドを使用してファインチューニングすることで、下流の音声認識タスクで強力なパフォーマンスを達成できました。

しかし、CTC モデルには欠点があります。エンコーダーにシンプルな線形レイヤーを追加すると、小さくて高速な全体的なモデルが得られますが、音声的なスペリングエラーが発生しやすくなります。以下で Wav2Vec2 モデルについてこれを実証します。

## CTC モデルの検証

[LibriSpeech ASR](hf-internal-testing/librispeech_asr_dummy) データセットの小さな抜粋を読み込んで、Wav2Vec2 の音声書き起こし機能を実証しましょう：

```python
from datasets import load_dataset

dataset = load_dataset(
    "hf-internal-testing/librispeech_asr_dummy", "clean", split="validation"
)
dataset
```

**出力: **
```
Dataset({
    features: ['file', 'audio', 'text', 'speaker_id', 'chapter_id', 'id'],
    num_rows: 73
})
```

73 個のオーディオサンプルのうちの 1 つを選んで、オーディオサンプルと書き起こしを確認できます：

```python
from IPython.display import Audio

sample = dataset[2]

print(sample["text"])
Audio(sample["audio"]["array"], rate=sample["audio"]["sampling_rate"])
```
**出力: **
```
HE TELLS US THAT AT THIS FESTIVE SEASON OF THE YEAR WITH CHRISTMAS AND ROAST BEEF LOOMING BEFORE US SIMILES DRAWN FROM EATING AND ITS RESULTS OCCUR MOST READILY TO THE MIND
```

素晴らしい！クリスマスとローストビーフ、いいですね！🎄 データサンプルを選択したので、次にファインチューニングされたチェックポイントを `pipeline()` に読み込みます。これには、LibriSpeech データの 100 時間でファインチューニングされた公式の [Wav2Vec2 base](facebook/wav2vec2-base-100h) チェックポイントを使用します：

```python
from transformers import pipeline

pipe = pipeline("automatic-speech-recognition", model="facebook/wav2vec2-base-100h")
```

次に、データセットから例を取り出して、その生データをパイプラインに渡します。`pipeline` は渡された辞書を *消費* します（つまり、再利用できません）ので、データのコピーを渡します。これにより、以下の例で同じオーディオサンプルを安全に再利用できます：

```python
pipe(sample["audio"].copy())
```
**出力: **
```
{"text": "HE TELLS US THAT AT THIS FESTIVE SEASON OF THE YEAR WITH CHRISTMAUS AND ROSE BEEF LOOMING BEFORE US SIMALYIS DRAWN FROM EATING AND ITS RESULTS OCCUR MOST READILY TO THE MIND"}
```

Wav2Vec2 モデルがこのサンプルをかなりうまく書き起こしていることがわかります。一見すると一般的に正確に見えます。ターゲットと予測を並べて違いを強調してみましょう：

```
Target:      HE TELLS US THAT AT THIS FESTIVE SEASON OF THE YEAR WITH CHRISTMAS AND ROAST BEEF LOOMING BEFORE US SIMILES DRAWN FROM EATING AND ITS RESULTS OCCUR MOST READILY TO THE MIND
Prediction:  HE TELLS US THAT AT THIS FESTIVE SEASON OF THE YEAR WITH **CHRISTMAUS** AND **ROSE** BEEF LOOMING BEFORE US **SIMALYIS** DRAWN FROM EATING AND ITS RESULTS OCCUR MOST READILY TO THE MIND
```

ターゲットテキストと予測された書き起こしを比較すると、すべての単語が正しく _聞こえる_ ことがわかりますが、一部は正確にスペルされていません。例えば：

* _CHRISTMAUS_ vs. _CHRISTMAS_
* _ROSE_ vs. _ROAST_
* _SIMALYIS_ vs. _SIMILES_

これは CTC モデルの欠点を浮き彫りにしています。CTC モデルは本質的に「音響のみ」のモデルです。つまり、オーディオ入力から隠れ状態表現を形成するエンコーダーと、隠れ状態を文字にマッピングする線形レイヤーで構成されています。

```mermaid
%%{init: {'theme': 'dark'}}%%
graph LR
    A[Audio Input] --> B[Encoder]
    B --> C[Hidden States]
    C --> D[Linear Layer]
    D --> E[Characters]

    style A fill: #1e3a5f,stroke: #4a90e2,color: #ffffff
    style B fill: #2d5a7b,stroke: #4a90e2,color: #ffffff
    style C fill: #3d6a8b,stroke: #4a90e2,color: #ffffff
    style D fill: #4d7a9b,stroke: #4a90e2,color: #ffffff
    style E fill: #1e3a5f,stroke: #4a90e2,color: #ffffff
```

これは、システムがほぼ完全に与えられた音響入力（オーディオの音声的な音）に基づいて予測を行うことを意味し、したがって音声的な方法でオーディオを書き起こす傾向があります（例: _CHRISTMAUS_）。前後の文字の言語モデリングコンテキストにはあまり重要性を与えないため、音声的なスペリングエラーが発生しやすくなります。より知的なモデルは、_CHRISTMAUS_ が英語の語彙に有効な単語ではないことを識別し、予測を行う際に _CHRISTMAS_ に修正するでしょう。また、予測には大きな 2 つの機能が欠けています。大文字小文字と句読点です。これにより、モデルの書き起こしの実世界のアプリケーションへの有用性が制限されます。

## Seq2Seq への移行

そこで Seq2Seq モデルの登場です！Unit 3 で概説したように、Seq2Seq モデルはクロスアテンション機構を介してリンクされたエンコーダーとデコーダーで形成されます。エンコーダーは以前と同じ役割を果たし、オーディオ入力の隠れ状態表現を計算しますが、デコーダーは **言語モデル** の役割を果たします。デコーダーは、エンコーダーからの隠れ状態表現の全体的なシーケンスを処理し、対応するテキスト書き起こしを生成します。オーディオ入力のグローバルコンテキストを持つデコーダーは、予測を行う際に言語モデリングコンテキストを使用でき、スペリングミスをその場で修正し、音声的な予測の問題を回避できます。

Seq2Seq モデルには 2 つの欠点があります：
1. デコードプロセスが一度に 1 ステップずつ行われるため、本質的にデコードが遅くなります
2. データに対する要求が高く、収束するために大幅に多くのトレーニングデータが必要です

特に、大量のトレーニングデータの必要性は、音声の Seq2Seq アーキテクチャの進歩におけるボトルネックとなっていました。ラベル付き音声データは入手が困難で、当時最大の注釈付きデータセットでもわずか 10,000 時間でした。これは 2022 年に **Whisper** のリリースによってすべて変わりました。Whisper は、OpenAI の Alec Radford らによって [2022 年 9 月](https://openai.com/blog/whisper/) に公開された音声認識用の事前学習済みモデルです。完全に **ラベルなし** のオーディオデータで事前学習された CTC の前身とは異なり、Whisper は膨大な量の **ラベル付き** オーディオ書き起こしデータ、正確には 680,000 時間で事前学習されています。

これは、Wav2Vec 2.0 のトレーニングに使用されたラベルなしオーディオデータ（60,000 時間）よりも桁違いに多いデータです。さらに、この事前学習データの 117,000 時間は多言語（または「非英語」）データです。これにより、96 以上の言語に適用できるチェックポイントが得られ、その多くは _低リソース_ と見られる言語、つまりトレーニングに適した大規模なデータコーパスが不足している言語です。

680,000 時間のラベル付き事前学習データにスケールアップすると、Whisper モデルは多くのデータセットとドメインに対して強力な汎化能力を示します。事前学習済みチェックポイントは、最先端のパイプラインシステムと競合する結果を達成し、LibriSpeech パイプラインの test-clean サブセットでほぼ 3%のワードエラーレート（WER）、TED-LIUM で 4.7% WER の新しい最先端を達成しています（[Whisper 論文](https://cdn.openai.com/papers/whisper.pdf) の表 8 を参照）。

特に重要なのは、Whisper の長時間オーディオサンプルを処理する能力、入力ノイズに対する堅牢性、大文字小文字と句読点が付いた書き起こしを予測する能力です。これにより、実世界の音声認識システムの実行可能な候補となります。

このセクションの残りの部分では、🤗 Transformers を使用して、事前学習済み Whisper モデルを音声認識に使用する方法を示します。多くの状況において、事前学習済み Whisper チェックポイントは非常に高性能で優れた結果を提供するため、音声認識の問題を解決するための最初のステップとして事前学習済みチェックポイントを試すことをお勧めします。ファインチューニングを通じて、事前学習済みチェックポイントを特定のデータセットや言語に適応させ、これらの結果をさらに改善できます。今後のサブセクションで、[ファインチューニング](fine-tuning) の方法を実演します。

Whisper チェックポイントは、さまざまなモデルサイズの 5 つの構成で提供されます。最小の 4 つは英語のみまたは多言語のデータでトレーニングされています。最大のチェックポイントは多言語のみです。9 つの事前学習済みチェックポイントはすべて [Hugging Face Hub](https://huggingface.co/models?search=openai/whisper) で利用可能です。チェックポイントは、Hub 上のモデルへのリンクとともに以下の表にまとめられています。「VRAM」は、最小バッチサイズ 1 でモデルを実行するために必要な GPU メモリを示します。「Rel Speed」は、最大モデルと比較したチェックポイントの相対速度です。この情報に基づいて、ハードウェアに最適なチェックポイントを選択できます。

| Size   | Parameters | VRAM / GB | Rel Speed | English-only                                         | Multilingual                                        |
|--------|------------|-----------|-----------|------------------------------------------------------|-----------------------------------------------------|
| tiny   | 39 M       | 1.4       | 32        | [✓](https://huggingface.co/openai/whisper-tiny.en)   | [✓](https://huggingface.co/openai/whisper-tiny)     |
| base   | 74 M       | 1.5       | 16        | [✓](https://huggingface.co/openai/whisper-base.en)   | [✓](https://huggingface.co/openai/whisper-base)     |
| small  | 244 M      | 2.3       | 6         | [✓](https://huggingface.co/openai/whisper-small.en)  | [✓](https://huggingface.co/openai/whisper-small)    |
| medium | 769 M      | 4.2       | 2         | [✓](https://huggingface.co/openai/whisper-medium.en) | [✓](https://huggingface.co/openai/whisper-medium)   |
| large  | 1550 M     | 7.5       | 1         | x                                                    | [✓](https://huggingface.co/openai/whisper-large-v2) |

[Whisper Base](https://huggingface.co/openai/whisper-base) チェックポイントを読み込みましょう。これは、以前に使用した Wav2Vec2 チェックポイントと同等のサイズです。多言語音声認識への移行を先取りして、base チェックポイントの多言語バリアントを読み込みます。また、利用可能な場合はモデルを GPU に、そうでない場合は CPU に読み込みます。その後、`pipeline()` は、必要に応じてすべての入力 / 出力を CPU から GPU に移動する処理を行います：

```python
import torch
from transformers import pipeline

device = "cuda:0" if torch.cuda.is_available() else "cpu"
pipe = pipeline(
    "automatic-speech-recognition", model="openai/whisper-base", device=device
)
```

素晴らしい！では、以前と同じようにオーディオを書き起こしましょう。唯一の変更は、追加の引数 `max_new_tokens` を渡すことです。これは、予測を行う際にモデルが生成する最大トークン数をモデルに伝えます：

```python
pipe(sample["audio"], max_new_tokens=256)
```
**出力: **
```
{'text': ' He tells us that at this festive season of the year, with Christmas and roast beef looming before us, similarly is drawn from eating and its results occur most readily to the mind.'}
```

簡単ですね！最初に気づくのは、大文字小文字と句読点の両方が存在することです。これにより、Wav2Vec2 の大文字小文字と句読点なしの書き起こしと比較して、書き起こしが読みやすくなります。書き起こしをターゲットと並べて見てみましょう：

```
Target:     HE TELLS US THAT AT THIS FESTIVE SEASON OF THE YEAR WITH CHRISTMAS AND ROAST BEEF LOOMING BEFORE US SIMILES DRAWN FROM EATING AND ITS RESULTS OCCUR MOST READILY TO THE MIND
Prediction: He tells us that at this festive season of the year, with **Christmas** and **roast** beef looming before us, **similarly** is drawn from eating and its results occur most readily to the mind.
```

Whisper は、Wav2Vec2 で見られた音声的なエラーを修正する素晴らしい仕事をしています。_Christmas_ と _roast_ の両方が正しくスペルされています。モデルはまだ _SIMILES_ に苦労しており、_similarly_ と誤って書き起こされていますが、今回は予測が英語の語彙からの有効な単語です。より大きな Whisper チェックポイントを使用すると、書き起こしエラーをさらに削減できますが、より多くの計算が必要になり、書き起こし時間が長くなります。

96 の言語を処理できるモデルが約束されているので、英語の音声認識を離れて世界に目を向けましょう 🌎！[Multilingual LibriSpeech](https://huggingface.co/datasets/facebook/multilingual_librispeech)（MLS）データセットは、LibriSpeech データセットの多言語版で、6 つの言語のラベル付きオーディオデータがあります。MLS データセットのスペイン語分割から 1 つのサンプルを読み込みます。_ストリーミング_ モードを使用して、データセット全体をダウンロードする必要がないようにします：

```python
dataset = load_dataset(
    "facebook/multilingual_librispeech", "spanish", split="validation", streaming=True
)
sample = next(iter(dataset))
```

再度、テキスト書き起こしを確認し、オーディオセグメントを聞いてみましょう：

```python
print(sample["text"])
Audio(sample["audio"]["array"], rate=sample["audio"]["sampling_rate"])
```
**出力: **
```
entonces te delelitarás en jehová y yo te haré subir sobre las alturas de la tierra y te daré á comer la heredad de jacob tu padre porque la boca de jehová lo ha hablado
```

これが、Whisper の書き起こしで目指しているターゲットテキストです。私たちのモデルは句読点と大文字小文字も予測するため、おそらくこれよりも良い結果が得られることがわかっていますが、どちらも参照には存在しません。オーディオサンプルをパイプラインに転送して、テキスト予測を取得しましょう。注意すべき点の 1 つは、パイプラインは入力したオーディオ入力の辞書を _消費_ するため、辞書を再利用できないことです。これを回避するために、オーディオサンプルの _コピー_ を渡すことで、以下のコード例で同じオーディオサンプルを再利用できます：

```python
pipe(sample["audio"].copy(), max_new_tokens=256, generate_kwargs={"task": "transcribe"})
```
**出力: **
```
{'text': ' Entonces te deleitarás en Jehová y yo te haré subir sobre las alturas de la tierra y te daré a comer la heredad de Jacob tu padre porque la boca de Jehová lo ha hablado.'}
```

素晴らしい。これは参照テキストと非常に似ています（句読点と大文字小文字があるため、間違いなく優れています！）。`"task"` を _生成キーワード引数_（generate kwarg）として転送したことに気づくでしょう。`"task"` を `"transcribe"` に設定すると、Whisper は _音声認識_ のタスクを実行するように強制されます。つまり、オーディオが話された言語と同じ言語でオーディオが書き起こされます。Whisper はまた、密接に関連する _音声翻訳_ のタスクを実行することもできます。スペイン語のオーディオを英語のテキストに翻訳できます。これを実現するには、`"task"` を `"translate"` に設定します：

```python
pipe(sample["audio"], max_new_tokens=256, generate_kwargs={"task": "translate"})
```
**出力: **
```
{'text': ' So you will choose in Jehovah and I will raise you on the heights of the earth and I will give you the honor of Jacob to your father because the voice of Jehovah has spoken to you.'}
```

音声認識と音声翻訳を切り替えることができるようになったので、ニーズに応じてタスクを選択できます。言語 X のオーディオから同じ言語 X のテキストへ認識するか（例: スペイン語のオーディオからスペイン語のテキストへ）、または任意の言語 X のオーディオから英語のテキストへ翻訳するか（例: スペイン語のオーディオから英語のテキストへ）のどちらかです。

`"task"` 引数が生成されるテキストのプロパティを制御するために使用される方法の詳細については、Whisper base モデルの[モデルカード](https://huggingface.co/openai/whisper-base#usage) を参照してください。

## 長時間書き起こしとタイムスタンプ

これまで、30 秒未満の短いオーディオサンプルの書き起こしに焦点を当ててきました。Whisper の魅力の 1 つは、長いオーディオサンプルで動作する能力であることを述べました。ここでそのタスクに取り組みましょう！

MLS データセットから連続するサンプルを連結して、長いオーディオファイルを作成しましょう。MLS データセットは、長いオーディオブックの録音を短いセグメントに分割してキュレーションされているため、サンプルを連結することは、より長いオーディオブックのパッセージを再構築する 1 つの方法です。その結果、結果のオーディオはサンプル全体で一貫しているはずです。

ターゲットのオーディオ長を 5 分に設定し、この値に達したらサンプルの連結を停止します：

```python
import numpy as np

target_length_in_m = 5

# convert from minutes to seconds (* 60) to num samples (* sampling rate)
sampling_rate = pipe.feature_extractor.sampling_rate
target_length_in_samples = target_length_in_m * 60 * sampling_rate

# iterate over our streaming dataset, concatenating samples until we hit our target
long_audio = []
for sample in dataset:
    long_audio.extend(sample["audio"]["array"])
    if len(long_audio) > target_length_in_samples:
        break

long_audio = np.asarray(long_audio)

# how did we do?
seconds = len(long_audio) / 16000
minutes, seconds = divmod(seconds, 60)
print(f"Length of audio sample is {minutes} minutes {seconds: .2f} seconds")
```
**出力: **
```
Length of audio sample is 5.0 minutes 17.22 seconds
```

素晴らしい！5 分 17 秒のオーディオを書き起こします。この長いオーディオサンプルを直接モデルに転送することには 2 つの問題があります：
1. Whisper は本質的に 30 秒のサンプルで動作するように設計されています。30 秒未満のものは沈黙で 30 秒にパディングされ、30 秒を超えるものは余分なオーディオをカットして 30 秒に切り詰められるため、オーディオを直接渡すと最初の 30 秒の書き起こしのみが得られます
2. トランスフォーマーネットワークのメモリはシーケンス長の 2 乗でスケールします。入力長を 2 倍にするとメモリ要件が 4 倍になるため、超長いオーディオファイルを渡すとメモリ不足（OOM）エラーが発生する可能性があります

🤗 Transformers で長時間書き起こしが機能する方法は、入力オーディオをより小さく管理しやすいセグメントに _チャンキング_ することです。各セグメントは前のセグメントとわずかにオーバーラップしています。これにより、境界でセグメントを正確につなぎ合わせることができます。セグメント間のオーバーラップを見つけて、それに応じて書き起こしをマージできるためです：

![チャンキングアルゴリズム](https://huggingface.co/blog/assets/49_asr_chunking/Striding.png)

サンプルをチャンキングすることの利点は、チャンク \\( i \\) の結果を必要とせずに後続のチャンク \\( i + 1 \\) を書き起こすことができることです。ステッチングはすべてのチャンクを書き起こした後にチャンクの境界で行われるため、どの順序でチャンクを書き起こすかは問題ではありません。アルゴリズムは完全に **ステートレス** であるため、チャンク \\( i \\) と同時にチャンク \\( i + 1 \\) を実行することもできます！これにより、チャンクを _バッチ化_ してモデルで並列に実行でき、順次書き起こすのに比べて大幅な計算速度の向上が得られます。🤗 Transformers でのチャンキングの詳細については、この[ブログ投稿](https://huggingface.co/blog/asr-chunking) を参照してください。

長時間書き起こしをアクティブにするには、パイプラインを呼び出すときに 1 つの追加引数を追加する必要があります。この引数 `chunk_length_s` は、チャンクされたセグメントの長さを秒単位で制御します。Whisper の場合、30 秒のチャンクが最適です。これは、Whisper が期待する入力長と一致するためです。

バッチ処理をアクティブにするには、引数 `batch_size` をパイプラインに渡す必要があります。すべてをまとめると、チャンキングとバッチ処理を使用して長いオーディオサンプルを次のように書き起こすことができます：

```python
pipe(
    long_audio,
    max_new_tokens=256,
    generate_kwargs={"task": "transcribe"},
    chunk_length_s=30,
    batch_size=8,
)
```
**出力: **
```
{'text': ' Entonces te deleitarás en Jehová, y yo te haré subir sobre las alturas de la tierra, y te daré a comer la
heredad de Jacob tu padre, porque la boca de Jehová lo ha hablado. nosotros curados. Todos nosotros nos descarriamos
como bejas, cada cual se apartó por su camino, mas Jehová cargó en él el pecado de todos nosotros...
```

ここでは出力全体を印刷しません。かなり長いためです（合計 312 単語）！16GB V100 GPU では、上記の行の実行には約 3.45 秒かかると予想されます。これは 317 秒のオーディオサンプルにしてはかなり良い結果です。CPU では、30 秒に近い時間がかかることが予想されます。

Whisper はまた、オーディオデータのセグメントレベルの _タイムスタンプ_ を予測することもできます。これらのタイムスタンプは、オーディオの短いパッセージの開始時刻と終了時刻を示し、書き起こしを入力オーディオと整列させるのに特に便利です。ビデオに字幕を提供したいとします。書き起こしのどの部分がビデオのどのセグメントに対応するかを知るために、これらのタイムスタンプが必要です。その時間に正しい書き起こしを表示するためです。

タイムスタンプ予測をアクティブにするのは簡単です。引数 `return_timestamps=True` を設定するだけです。タイムスタンプは、以前に使用したチャンキングとバッチ処理の方法と互換性があるため、以前の呼び出しにタイムスタンプ引数を追加するだけです：

```python
pipe(
    long_audio,
    max_new_tokens=256,
    generate_kwargs={"task": "transcribe"},
    chunk_length_s=30,
    batch_size=8,
    return_timestamps=True,
)["chunks"]
```
**出力: **
```
[{'timestamp': (0.0, 26.4),
  'text': ' Entonces te deleitarás en Jehová, y yo te haré subir sobre las alturas de la tierra, y te daré a comer la heredad de Jacob tu padre, porque la boca de Jehová lo ha hablado. nosotros curados. Todos nosotros nos descarriamos como bejas, cada cual se apartó por su camino,'},
 {'timestamp': (26.4, 32.48),
  'text': ' mas Jehová cargó en él el pecado de todos nosotros. No es que partas tu pan con el'},
 {'timestamp': (32.48, 38.4),
  'text': ' hambriento y a los hombres herrantes metas en casa, que cuando vieres al desnudo lo cubras y no'},
 ...
```

これで完了です！予測されたテキストと対応するタイムスタンプが得られました。

## まとめ

Whisper は、音声認識と翻訳のための強力な事前学習済みモデルです。Wav2Vec2 と比較して、句読点と大文字小文字を含む出力により、書き起こし精度が高くなっています。英語および 96 の他の言語の音声を書き起こすために使用でき、短いオーディオセグメントと _チャンキング_ による長いオーディオセグメントの両方に対応できます。これらの属性により、ファインチューニングの必要なしに、多くの音声認識と翻訳のタスクに対応できる実行可能なモデルとなっています。`pipeline()` メソッドは、生成された予測を制御できるワンライン API 呼び出しで推論を実行する簡単な方法を提供します。

Whisper モデルは多くの高リソース言語で非常に優れたパフォーマンスを発揮しますが、低リソース言語、つまり利用可能なトレーニングデータが少ない言語では、書き起こしと翻訳の精度が低くなります。また、特定の言語のさまざまなアクセントや方言によってパフォーマンスにばらつきがあり、さまざまな性別、人種、年齢、またはその他の人口統計基準の話者に対する精度が低くなります（[Whisper 論文](https://arxiv.org/pdf/2212.04356.pdf) を参照）。

低リソース言語、アクセント、または方言でのパフォーマンスを向上させるために、事前学習済み Whisper モデルを取得し、適切に選択されたデータの小さなコーパスでトレーニングすることができます。これは _ファインチューニング_ と呼ばれるプロセスです。わずか 10 時間の追加データで、低リソース言語での Whisper モデルのパフォーマンスを 100%以上改善できることを示します。次のセクションでは、ファインチューニング用のデータセットを選択するプロセスについて説明します。
