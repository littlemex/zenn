---
title: "distai-platform とこの本の地図"
---

## はじめに

この本は、分散学習・推論のための EKS 基盤 [littlemex/distai-platform](https://github.com/littlemex/distai-platform) で下された設計判断を、判断そのものだけでなく、退けた選択肢とその理由まで含めて記録する。ワークショップのような操作手順書ではなく、なぜその形になったのかを追う本である。読者は Kubernetes、EKS、Terraform、Karpenter の基礎を知っている前提で書く。基礎用語の説明は最小限にし、設計判断が依存する部分だけを説明する。

## 1. distai-platform とは何か

distai-platform は、NVIDIA の GPU と AWS Trainium (Neuron) を同じ基盤の上で等価に扱うことを目標にした、EKS 上の分散学習・推論基盤である。公開リポジトリとして育てられており、ライセンスは Apache License 2.0 である。

この基盤には隣接する 2 つのリポジトリがある。1 つは [littlemex/distributed-ai](https://github.com/littlemex/distributed-ai) で、長く保守する基盤のコードと、日付付きの 1 回限りの実験の記録が同居していたリポジトリである。distai-platform は当初、この distributed-ai から基盤の部分だけを引き継ぐ器として作られた。のちにこの方針は変わり、distributed-ai を参考資料として基盤を一から作り直すことになった (2 節)。もう 1 つは [awslabs/awsome-distributed-ai](https://github.com/awslabs/awsome-distributed-ai) で、AWS が公開している分散 AI の参考実装集である。distai-platform は、このアップストリームリポジトリの規約や文書の形をできるだけ手本にしながら育てる方針を取っている。ただし、distai-platform で作ったものを awsome-distributed-ai へ還元する範囲は後に絞られ、Neuron を前提にした提案は awsome-distributed-ai には出さず、還元は GPU と汎用の部分に限ることになった (11 章)。

| リポジトリ | 役割 | この本での扱い |
| --- | --- | --- |
| `littlemex/distributed-ai` | 基盤と実験が同居した元のリポジトリである。実験はこのまま残る | 参照元としてのみ登場する |
| `littlemex/distai-platform` | 長く保守する基盤だけを持つ、新しい公開リポジトリ | 本書が扱う本体 |
| `awslabs/awsome-distributed-ai` | AWS 公開の参考実装集であり、アップストリームにあたる | 規約の手本であり、還元先でもある |

<!-- FIG name="00-repo-relation" kind="relation" shows="distributed-ai (experiments kept), distai-platform (new long-lived baseline), awsome-distributed-ai (upstream reference and contribution target) and what moves between them" labels="distributed-ai, distai-platform, awsome-distributed-ai, infra/, investigations/, reference, contribute" -->
![3 つのリポジトリの関係を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/00-repo-relation.png)

## 2. 実験基盤をそのまま取り込まなかった理由

distributed-ai の `infra/` をそのまま取り込まなかった理由は、`infra/` の中心的な仕組み、つまり容量の組み合わせを 1 つの Terraform 変数で表す仕組みそのものが、作り直す対象だったことにある。distai-platform を作り始めたとき、最初に検討したのは distributed-ai の `infra/` ディレクトリをそのまま新しいリポジトリに写し、そこを起点に直していく進め方だった。これは Issue #2 として提案され、実際に [PR #14](https://github.com/littlemex/distai-platform/pull/14) として実装された。Issue #2 の受け入れ条件は 3 つで、`infra/` の中身とファイルのモードが元の commit と同一であること、アカウント ID やプライベート IP のような環境の値が含まれないこと、そしてこのツリーをテスト用クラスタに Terraform で apply してワークロードが動くことであった。

PR #14 がやったことは、文字どおり 1 文字も変えない移送である。[distributed-ai の commit `e092c3f`](https://github.com/littlemex/distributed-ai/tree/e092c3f54e2ebcee665389cbdfc17348f612c2d3/infra) にある 192 ファイルを新しいリポジトリにそのまま写し、`diff -r` で中身が 1 バイトも違わないこと、`git ls-files -s` でファイルの実行権限 (38 ファイル分) まで一致することを確かめた。公開してよいかどうかも調べ、アクセスキーや秘密鍵、プライベート IP、リソース ID は見つからず、12 桁の数字として検出されたのは AWS が公開している Deep Learning Containers のレジストリを指す ECR アカウント ID だけで、私的なアカウント ID の漏れはなかった。一方で、古いリポジトリへの URL が 10 か所、社内限定の用語が 2 か所見つかり、これらは state の分け方 (#5)、来歴 (#6)、監視 (#7)、権限 (#8)、CPU のプール (#10) などとあわせて、別の Issue (#5〜#13) で直す対象として切り出された。

この PR はレビューの時点では draft のままだった。中身が元と同一であることは確認できても、新しいリポジトリの場所から実際に `apply` して動くことまでは、まだ確認されていなかったからである。たとえば運用スクリプトの一部は古いリポジトリから release を取りに行く作りになっており、場所を移しても同じように動く保証がなかった。つまり Issue #2 は、3 つ目の受け入れ条件が最後まで満たされないまま閉じられたことになる。

この取り込みの進め方自体にも、Issue #2 と PR #14 の本文で退けた代案が 3 つある。1 つ目は、写すのと直すのを 1 つの PR で行う案である。最初の commit からきれいな状態になる長所はあるが、移したせいの差と直したせいの差を見分けられず、壊れたときに原因を切り分けられないので退けた。2 つ目は、`git filter-repo` で `infra/` の履歴ごと移す案である。`git blame` で元の書き手が分かる長所はあるが、実験の時代の commit とパスが付いてくるうえ、公開した履歴は後から書き換えられないので退けた。3 つ目は、実験も一緒に移す案である。1 か所で全部を見られる長所はあるが、寿命の違うものが同居するという元の問題がそのまま残るので退けた。

その後、Issue #17 で容量の混ぜ方の設計をやり直す検討が進み、最終的に Issue #2 と PR #14 はマージされずに閉じられた。PR #14 を閉じた際のコメントは次のとおりである (英語の原文を和訳した)。

> 基盤は distributed-ai の `infra/` を起点にするのではなく、`littlemex/distributed-ai@e092c3f` を参考資料として、一から作り直す。設計は Issue #17 で検討する。ブランチ `feat/import-infra` は参照用に残す。

Issue #2 も同じ趣旨で「Not planned」として閉じられた。この判断の核心は、distributed-ai の `infra/` が持つ中心的な仕組み、つまり容量の組み合わせを 1 つの Terraform 変数で表す仕組みそのものが、作り直す対象だったという点にある。Issue #17 が取り込みを退けた理由として挙げるのも、この 1 点だけである。無変更で写して基準にし、修正を別の PR に分ける進め方は、移送の差分と修正の差分を分けるという点ではすでに成り立っていた。問題は、直す対象が周辺の不具合ではなく中心の仕組みそのものだったことで、その場合は基準から差分を積み上げるより、元の commit を参照しながら新しく組むほうが筋がよいと判断されたと考えられる。取り込み用のブランチ `feat/import-infra` は参照用に残された。なお、取り込んだファイルを直す対象として切り出した Issue (#5〜#13) は、執筆時点では閉じられずに残っている。参照元を資料として残したまま、容量の設計は 2 章で見る四つの決定というフレームから組み直されている。

<!-- FIG name="00-import-then-redesign" kind="flow" shows="PR #14 copies infra/ unmodified and verifies it byte-identical, then Issue #2 and PR #14 are closed without merging in favour of a from-scratch redesign referencing the same commit" labels="PR #14 draft, diff -r IDENTICAL, git ls-files -s SAME-MODES, Issue #2 closed, Issue #17 redesign, e092c3f as reference" -->
![PR #14 の検証から閉鎖、設計し直しへの流れを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/00-import-then-redesign.png)

## 3. この本が答える問い

この本は、distai-platform を育てる過程で繰り返し出てきた設計上の問いを、章ごとに 1 つずつ掘り下げる。主な問いは次のとおりである。

- GPU と Trainium を「同じ形」で扱うとは、実装を 1 つにまとめることなのか、それとも別の意味なのか (1 章)。
- spot、on-demand、予約、Capacity Block が混ざる容量の悩みは、本当に 1 つの問題なのか (2 章、3 章)。
- どのリージョンでどれだけ容量が取れるかを、どうやって事前に見積もるのか (4 章)。
- デバイスプラグインや DRA を後から足すと、実際には何が壊れるのか (5 章から 7 章)。
- 1 つのセル (Terraform が作る 1 つのクラスタの器。1 章で定義する) を複数のチームで分け合うとき、分離と共有をどう両立させるのか (3b 章)。
- Terraform という基盤の層は、どこまで作りどこで止めるべきなのか (8 章)。
- 常設のテスト用クラスタと、実験ごとに使い捨てる専用クラスタを、どう組み合わせて実機の検証を計画し承認するのか (9 章)。
- 複数の役割に分かれた開発の進め方は、何を防ぐための仕組みなのか (10 章)。
- 自分たちで直したことのうち、どれをアップストリームに還元できるのか (11 章)。

## 4. 読み方の地図

各章は独立して読めるように書いているが、設計の筋を追う順に読むと理解しやすい。ただし章の順と設計の依存の向きは一致しない。Issue #17 (2 章と 3 章) は Issue #19 (5 章から 7 章) に依存すると明記しており、デバイスの扱い方がプールの規約を変えうるので、#19 の実験を先に行うことにしていた。2 章と 3 章のプールの規約は、5 章から 7 章の結果で確かめられる側にある。

| 章 | 内容 |
| --- | --- |
| 1. 設計原則 | GPU と Trainium を等価に扱うという原則そのものと、その適用範囲 |
| 2. 容量を混ぜる四つの決定 | 容量の悩みを 4 つの時間軸の決定に分解し、担当する道具を選ぶ |
| 3. 容量プールの規約 | NodePool と ResourceFlavor を結ぶ具体的な規約と、ワークロードごとの経路 |
| 3b. マルチテナンシー層の設計 | 1 つのセルを複数のチームで分け合うための権限とリソースの分離 |
| 4. 容量の地図 | リージョンと型ごとに、実際にどれだけ容量が取れるかの調査 |
| 5. device plugin と DRA の設計 | デバイスプラグインを後から足すと壊れる理由と、抽象化の候補の検討 |
| 6. DRA の実験 (NVIDIA) | GPU 向けの DRA を実機で検証した結果 |
| 7. DRA の実験 (Trainium) | Neuron 向けの DRA を実機で検証した結果と、GPU との比較 |
| 8. 基盤レイヤー | Terraform が作るものと作らないものの境界、破棄の順序、レビューの結果 |
| 9. 実機検証の運用 | 常設のテスト用クラスタと使い捨ての専用クラスタを組み合わせて検証を計画し承認する仕組み |
| 10. 開発の進め方 | 役割を分けた開発プロセスと、書き込んだ人によって扱いを分けるセキュリティ規則 |
| 11. アップストリームへの還元 | awsome-distributed-ai に返せる候補とその出し方 |
| 12. まとめ | 決定の一覧表と、まだ分かっていないこと |

<!-- FIG name="00-reading-map" kind="position" shows="chapter order and dependency: principles grounds every later chapter; four-decisions and capacity-pools feed the device-plugin and foundation chapters; experiments feed back into the design chapters they test" labels="01 Principles, 02 Four decisions, 03 Capacity pools, 04 Capacity map, 05 Device plugin/DRA design, 06-07 Experiments, 08 Foundation, 09 Verification, 10 Development process, 11 Upstream, 12 Summary" -->
![章の依存関係を示す地図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/00-reading-map.png)

## まとめ

distai-platform は、distributed-ai の実験と同居していた基盤を無変更で写すのではなく、その commit を参考資料として指差しながら一から作り直された、GPU と Trainium を等価に扱う EKS 基盤である。この判断のきっかけは、写すはずだった仕組み自体が設計をやり直す対象だったという点にある。次の章からは、作り直しの土台になった設計原則そのものを見ていく。
