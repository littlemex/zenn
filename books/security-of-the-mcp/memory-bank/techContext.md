# MCP Security Book Technical Context

## 使用技術

### 執筆環境
1. **ドキュメント管理**
   - Markdown: 基本的な文書フォーマット
   - Zenn: 技術書公開プラットフォーム
   - Git: バージョン管理

2. **図表作成**
   - Mermaid: ダイアグラム作成

### 開発環境
1. **プログラミング言語**
   - Python
   - 重要: uv を利用
   - JavaScript/TypeScript
   - その他主要言語（必要に応じて）

2. **フレームワーク**
   - FastAPI (Python)
   - Express (Node.js)
   - その他MCPサーバー実装に関連するフレームワーク

3. **開発ツール**
   - Visual Studio Code
   - Git
   - Docker

## 開発環境セットアップ

### 1. ドキュメント環境
```bash
# Zenn CLI
npm install -g zenn-cli

# その他必要なツール
npm install -g @mermaid-js/mermaid-cli
```

### 2. UV

```bash
# 新規作成時のコマンド
uv venv && source .venv/bin/activate
# uv での pip install に相当
uv sync
```

## 技術的制約


## 依存関係

### 1. 外部ライブラリ


### 2. Python パッケージ


## 開発ガイドライン

### 1. コーディング規約
- PEP 8 (Python)
- ESLint/Prettier (JavaScript/TypeScript)
- 各言語のベストプラクティスに従う

### 2. ドキュメント規約
- 日本語での記述
- 技術用語は必要に応じて英語を併記
- 図表を効果的に活用

### 3. セキュリティガイドライン
- OWASP セキュアコーディングガイドラインの遵守
- センシティブ情報の適切な取り扱い
- セキュリティレビューの実施

## ビルドとデプロイメント

### 1. ドキュメントビルド
```bash
# Zenn本のプレビュー
npx zenn preview

# 本の構築
npx zenn build
```

### 2. サンプルコードビルド
```bash
# TypeScriptビルド
npm run build

# Pythonパッケージビルド
python setup.py build
```

### 3. テスト実行
```bash
# JavaScriptテスト
npm test

# Pythonテスト
pytest
```