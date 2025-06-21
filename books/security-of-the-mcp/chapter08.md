---
title: "§08 STDIO SDK 実装解説!"
free: true
---

___MCP に関する実装理解編:___  _MCP の脆弱性と対策を実装するために必要な開発者向け知識の解説_

---

本章の説明は、2025-03-26 の[仕様](https://modelcontextprotocol.io/specification/2025-03-26)に基づきます。

MCP Specification: **Base Protocol（今ここ）**、Authorization、Client Features、Server Features

本 Chapter では STDIO の typescript-sdk(tag: 1.12.1) の [Client 実装](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/client/stdio.ts) と [Server 実装](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/stdio.ts) について解説します。Chapter07 で解説した通り実際にはサブプロセスを呼んでいるだけです。環境変数などを制御し、コマンドと引数を受け取ってサブプロセスを起動します。

typescript-sdk であっても脆弱な実装が含まれている可能性があります。まだ実装は初期の段階であるため完全に信用するのではなく脆弱性チェックを行うべきでしょう。

## Client 実装

**1. 主要クラス**

`StdioClientTransport` クラスは、 `Transport` インターフェースを実装し、サブプロセスである MCP Server との通信を管理します。`StdioServerParameters` 型は、Server プロセスの起動方法を定義します。

```typescript
export type StdioServerParameters = {
  /** Server を起動するための実行可能ファイル */
  command: string;
  /** 実行可能ファイルに渡すコマンドライン引数 */
  args?: string[];
  /** プロセス起動時に使用する環境変数 */
  env?: Record<string, string>;
  /** 子プロセスの stderr の扱い方 */
  stderr?: IOType | Stream | number;
  /** プロセス起動時の作業ディレクトリ */
  cwd?: string;
};

/** STDIO Client トランスポート：サブプロセスを起動し、stdin/stdout を介して通信 */
export class StdioClientTransport implements Transport {
  // ...実装の詳細...
}
```

`StdioClientTransport`や `StreamableHttpClientTransport` などの具体的なトランスポート実装がこの `Transport` インターフェースを継承します。`start()`、`close()`、`send(message: JSONRPCMessage)` などを `Transport` ではメソッドとして定義しています。この抽象化によってトランスポート層の接続の実現方法の違いを隠蔽します。

**2. 環境変数の継承**

この実装では `sudo` コマンドのデフォルトの環境変数継承リストなどのように必要最低限の環境変数のみをサブプロセスに引き継ぎます。そして、**コマンドインジェクションなどを防ぐために実行可能コードの場合は除外**しています。API キーなどの情報がサブプロセスに漏れないようにケアされています。ユーザー自身が定義した環境変数はサブプロセスにセットされます。

```typescript
/** デフォルトで継承する環境変数 */
export const DEFAULT_INHERITED_ENV_VARS = process.platform === "win32" ? 
  [/* Windows 環境変数 */] : 
  [/* Unix 環境変数 */ "HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER"];

/** 安全に継承できると判断される環境変数のみを含むデフォルト環境オブジェクトを返す */
export function getDefaultEnvironment(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const key of DEFAULT_INHERITED_ENV_VARS) {
    const value = process.env[key];
    if (value === undefined) {
      continue;
    }
    if (value.startsWith("()")) {
      // 関数はコマンドインジェクションなどのセキュリティリスクのためスキップ
      continue;
    }
    env[key] = value;
  }
  return env;
}
```

**3. サブプロセスの起動と通信**

すでに Chapter07 で説明した内容ですね。イベントハンドラなどを設定しています。

```typescript
async start(): Promise<void> {
...
  return new Promise((resolve, reject) => {
    this._process = spawn(
      this._serverParams.command,
      this._serverParams.args ?? [],
      {
        env: this._serverParams.env ?? getDefaultEnvironment(),
        stdio: ["pipe", "pipe", this._serverParams.stderr ?? "inherit"],
        shell: false,
        signal: this._abortController.signal,
        windowsHide: process.platform === "win32" && isElectron(),
        cwd: this._serverParams.cwd,
      }
    );
    
    // イベントハンドラの設定
    this._process.on("error", (error) => { /* エラー処理 */ });
    this._process.on("spawn", () => { resolve(); });
    this._process.on("close", (_code) => { /* クローズ処理 */ });
    
    // stdin/stdout/stderrのイベントハンドラ設定
    this._process.stdin?.on("error", (error) => { this.onerror?.(error); });
    this._process.stdout?.on("data", (chunk) => {
      this._readBuffer.append(chunk);
      this.processReadBuffer();
    });
    this._process.stdout?.on("error", (error) => { this.onerror?.(error); });
...
  });
}
```

[`option.stdio`](https://nodejs.org/api/child_process.html#optionsstdio) の設定は、サブプロセスの標準入出力（stdin, stdout, stderr）の扱い方を指定するものです。`["pipe", "pipe", this._serverParams.stderr ?? "inherit"]` はそれぞれ `[stdin, stdout, stderr]` の扱い方を指定します。

`pipe` を指定すると、メインプロセスとサブプロセスの間に双方向パイプが作成されます。__stdin/stdout__ を `pipe` に設定することで、JSON-RPC 2.0 メッセージの送受信をプログラムで制御できます。__stderr__ をデフォルトで `inherit` に設定することで、Server のログやエラーメッセージが直接ターミナルに表示されます。

option で `shell` が `false` になっているのはセキュリティ上重要です。`true` の場合、シェルを起動してユーザーからのコマンドを文字列としてそのまま実行してしまうため、**コマンドインジェクションのリスク**があります。これによって権限昇格やバックドアのインストール、などが考えられます。

**4. メッセージ処理**

```typescript
private processReadBuffer() {
  while (true) {
    try {
      const message = this._readBuffer.readMessage();
      if (message === null) {
        break;
      }
      this.onmessage?.(message);
    } catch (error) {
      this.onerror?.(error as Error);
    }
  }
}
```

MCP Server が `stdout` に JSON-RPC 2.0 メッセージを書き込むと、前述した通り `child.stdout` ストリームで `data` イベントが発行され、`data` イベントハンドラがデータを `_readBuffer` に追加します。そして `processReadBuffer()` メソッドが呼び出され、`ReadBuffer` クラスを使用して、受信したバイトストリームから JSON-RPC 2.0 メッセージを抽出します。そして、`onmessage` コールバックを呼び出します。そして `Protocol` クラスの `_onresponse` が実行され、JSON-RPC 2.0 のメッセージの中身に応じた処理に振り分けられます。

**5. メッセージの送信**

```typescript
send(message: JSONRPCMessage): Promise<void> {
  return new Promise((resolve) => {
    if (!this._process?.stdin) {
      throw new Error("Not connected");
    }
    const json = serializeMessage(message);
    if (this._process.stdin.write(json)) {
      resolve();
    } else {
      this._process.stdin.once("drain", resolve);
    }
  });
}
```

`send()` メソッドは、JSON-RPC 2.0 メッセージを Server に送信します。`serializeMessage` を使用してメッセージをシリアライズし、サブプロセスの標準入力に書き込みます。バックプレッシャー処理のため、書き込みバッファが満杯の場合は `drain` イベントを待ちます。ちなみにバックプレッシャー処理とは、輻輳制御のことです。要は受信側がデータを受けきれなくなる状態になったら送信側は受信側を配慮してデータを送るのをちょっと待ちます。さまざまな実装方法がありますが、例えば、受信側のバッファが一杯になりそうになると、受信側から送信側に輻輳状態を通知して、送信側はこの通知を受けると送信データを溜めておくバッファに送信データを一定量溜めておきます。

## Server 実装

Server 側は Client と似たようなコードであることに気づくでしょう。そのため違いにフォーカスして説明します。**1/ 入出力方向**: Client の場合は `stdin` が Server の `stdout` から読み取り、`stdin` に書き込みます。Server の場合は 自身の `stdin` から読み取り、`stdout` に書き込みます。**2/ プロセス管理**: Client はサブプロセスとそのライフサイクルイベントを管理しますが Server は自身のプロセスとイベントのみを扱います。**3/ 終了処理**: Client はサブプロセスを終了させる責務を持ちます。あとはこれらの違いを理解しながらコードを読めばすんなりと理解できるでしょう。

## STDIO のセキュリティ

**脆弱性**

STDIO では**信頼できないコマンドや引数をユーザー自身が設定して実行すること自体のリスクは原理的に防げません**。例えば、MCP 設定でたびたび見かける [`uvx`](https://docs.astral.sh/uv/guides/tools/) は Python パッケージを直接利用するコマンドです。これを使うと悪意のあるライブラリが実行され、システム上で任意のコードが実行される可能性があります。現状の実装ではサブプロセスは親プロセスと同じユーザー権限で実行されます。Client がシステム全体へのアクセス権限を有している場合、`uvx` で実行されるサブプロセスはシステム全体へのアクセス権限を得てしまいます。**SSH 鍵、パスワード、環境変数、などあらゆる機密情報を収集することができ、外部にそれらの情報を送信することができます**。

**対策の検討**

`spawn` ではオプションとして `uid/gid` を指定することができるためサブプロセスの権限を下げることができます。ただしこれは Windows では使用できません。プロセスの権限を下げるためにユーザーやグループを作成する必要がありますし、完全な隔離を提供するわけではありません。これらの観点から SDK の実装でサブプロセスの権限を制御することはしていないのでしょう。

個人的な意見としては便利パッケージがたくさんあるからといって STDIO を安易に利用しないことをお勧めします。利用するとしても完全に実行するコマンドの挙動を理解している必要があります。少なくとも `uvx` のようにパッケージを直接利用することはやめておきましょう。最初は問題のないパッケージに見えていても後からバージョンアップで脆弱性を入れ込むような攻撃手法もあります。アクセスきーを環境変数に設定して利用する形式の MCP Server はリスクが大きいため利用しないようにしましょう。そもそもアクセスキーを利用しないことを推奨します。

STDIO 脆弱性についての技術的な**緩和策としてはサンドボックスを利用するのが良い**でしょう。Docker はクロスプラットフォーム対応しているため良い選択肢です。そのほか Linux であれば [Firejail](https://github.com/netblue30/firejail) などの軽量な SUID プログラムも選択肢となるでしょう。Linux の Namespace や seccomp-bpf を使ってターゲットアプリケーションをセキュアに隔離実行します。Firejail はコンテナと併用することも可能です。

**組織で MCP を利用する際に完全なガードレールを引くことは現状難しい**です。Cline や Cursor などの MCP Host 利用をローカル PC で許可している場合は、MCP 設定は社員のモラルに依存することになり、技術的なリスク軽減手法があったとしてもそれを強制し切ることは難しいです。

## まとめ

本 Chapter STDIO の詳細実装、セキュリティ、について解説しました。組織利用については組織ごとに状況が異なるため完全なガイドを提供することはできませんが、考慮すべきポイントはお伝えできたのではないでしょうか。

私自身が組織としての MCP に関するセキュリティ統制を取るのであれば、ローカル PC への MCP Host のインストールを全てデフォルトで Inbound Block し、MCP についてはクラウドベースのサンドボックスインスタンスでの利用をユーザーに強制します。これによってローカル PC の社外秘の重要情報に MCP はアクセスできなくなり、インスタンスレベルで隔離されたサンドボックスをユーザーに提供することができます。さらに MCP Client の typescript-sdk に Firejail 利用を追加で実装することでユーザーが意識せずともコマンドをよりセキュアに実行できます。このような改修した OSS の組織利用についてはライセンス的に問題がないことを確認しましょう。

そのほかの方法として自社のアーティファクトレジストリを用意し、MCP Host や MCP スコープのパッケージインストールを自社レジストリから取得するように強制するようなことは可能でしょう。これによって後からパッケージに脆弱性が埋め込まれるリスクを軽減し、統制の取れたパッケージ提供が可能となります。

## SaaS コラム

本書では **SaaS コラム** で本文内容を補足する SaaS に関する解説を行います。

今回は SaaS に関する解説はありません。