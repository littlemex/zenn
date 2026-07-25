# diagrams

各 book / article で使っている図版の draw.io ソース (`.drawio` XML) をここに保存する。
PNG は `images/` 配下に置いて記事から参照するが、その元データである編集可能な XML を
失わないよう、このディレクトリで版管理する。

## なぜ独立したディレクトリなのか

`books/<slug>/` や `articles/` の中に `.drawio` を置くと Zenn 側のビルドでエラーになる。
そのため図版ソースは Zenn が解釈しないこのトップレベル `diagrams/` に分離し、
配下の階層で「どの book / article のどの図か」を表現する。

## 階層規約

```
diagrams/
├── books/
│   └── <book-slug>/
│       └── <fig-name>.drawio        # images/books/<book-slug>/<fig-name>.png の元データ
└── articles/
    └── <article-slug>/
        └── <fig-name>.drawio        # images/<article-slug>/<fig-name>.png の元データ
```

`.drawio` のファイル名は、対応する PNG (`images/...` 側) と同じ basename に揃える。
これで PNG とソースが 1 対 1 で辿れる。

## 編集方法

`.drawio` は draw.io (diagrams.net) でそのまま開ける。編集したら PNG を書き出して
`images/` 側を更新し、`.drawio` と PNG を同じコミットで更新する。
