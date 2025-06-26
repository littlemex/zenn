---
title: "§08 STDIO Implementation Explained!"
free: true
---

___Understanding MCP Implementation:___ _Explanation of developer-oriented knowledge necessary to implement MCP vulnerabilities and countermeasures_

---

This chapter's explanation is based on the [specification](https://modelcontextprotocol.io/specification/2025-03-26) from 2025-03-26.

MCP Specification: **Base Protocol (We are here)**, Authorization, Client Features, Server Features, Security Best Practices

In this Chapter, we will explain the [Client implementation](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/client/stdio.ts) and [Server implementation](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/stdio.ts) of STDIO in typescript-sdk (tag: 1.12.1). As explained in Chapter 07, it's actually just calling a subprocess. It controls environment variables and receives commands and arguments to launch a subprocess.

Even with typescript-sdk, there may be vulnerable implementations. Since the implementation is still in its early stages, it should not be fully trusted, and vulnerability checks should be performed.

## Client Implementation

**1. Main Classes**

The `StdioClientTransport` class implements the `Transport` interface and manages communication with the MCP Server subprocess. The `StdioServerParameters` type defines how to launch the Server process.

```typescript
export type StdioServerParameters = {
  /** Executable to launch the Server */
  command: string;
  /** Command line arguments to pass to the executable */
  args?: string[];
  /** Environment variables to use when launching the process */
  env?: Record<string, string>;
  /** How to handle the child process's stderr */
  stderr?: IOType | Stream | number;
  /** Working directory when launching the process */
  cwd?: string;
};

/** STDIO Client transport: launches a subprocess and communicates via stdin/stdout */
export class StdioClientTransport implements Transport {
  // ...implementation details...
}
```

Concrete transport implementations such as `StdioClientTransport` and `StreamableHttpClientTransport` inherit this `Transport` interface. The `Transport` defines methods such as `start()`, `close()`, and `send(message: JSONRPCMessage)`. This abstraction hides the differences in how the transport layer implements connections.

**2. Environment Variable Inheritance**

This implementation inherits only the minimum necessary environment variables to the subprocess, similar to the default environment variable inheritance list of the `sudo` command. And it **excludes executable code to prevent command injection**. Care is taken to prevent API keys and other information from leaking to the subprocess. Environment variables defined by the user themselves are set in the subprocess.

```typescript
/** Default environment variables to inherit */
export const DEFAULT_INHERITED_ENV_VARS = process.platform === "win32" ? 
  [/* Windows environment variables */] : 
  [/* Unix environment variables */ "HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER"];

/** Returns a default environment object containing only environment variables deemed safe to inherit */
export function getDefaultEnvironment(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const key of DEFAULT_INHERITED_ENV_VARS) {
    const value = process.env[key];
    if (value === undefined) {
      continue;
    }
    if (value.startsWith("()")) {
      // Skip functions due to security risks such as command injection
      continue;
    }
    env[key] = value;
  }
  return env;
}
```

**3. Subprocess Launch and Communication**

This is the content we already explained in Chapter 07. It sets up event handlers.

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
    
    // Set up event handlers
    this._process.on("error", (error) => { /* Error handling */ });
    this._process.on("spawn", () => { resolve(); });
    this._process.on("close", (_code) => { /* Close handling */ });
    
    // Set up stdin/stdout/stderr event handlers
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

The [`option.stdio`](https://nodejs.org/api/child_process.html#optionsstdio) setting specifies how to handle the subprocess's standard input/output (stdin, stdout, stderr). `["pipe", "pipe", this._serverParams.stderr ?? "inherit"]` specifies the handling for `[stdin, stdout, stderr]` respectively.

Setting `pipe` creates a bidirectional pipe between the main process and the subprocess. By setting __stdin/stdout__ to `pipe`, JSON-RPC 2.0 messages can be programmatically controlled for sending and receiving. Setting __stderr__ to `inherit` by default allows the Server's logs and error messages to be displayed directly in the terminal.

Setting `shell` to `false` in the options is important for security. If set to `true`, it would launch a shell and execute user commands as strings directly, which poses a **risk of command injection**. This could lead to privilege escalation, backdoor installation, and other vulnerabilities.

**4. Message Processing**

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

When the MCP Server writes a JSON-RPC 2.0 message to `stdout`, as mentioned earlier, a `data` event is issued on the `child.stdout` stream, and the `data` event handler adds the data to `_readBuffer`. Then the `processReadBuffer()` method is called, which uses the `ReadBuffer` class to extract JSON-RPC 2.0 messages from the received byte stream. It then calls the `onmessage` callback. Then the `Protocol` class's `_onresponse` is executed, and processing is dispatched according to the contents of the JSON-RPC 2.0 message.

**5. Message Sending**

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

The `send()` method sends a JSON-RPC 2.0 message to the Server. It uses `serializeMessage` to serialize the message and writes it to the subprocess's standard input. For backpressure handling, if the write buffer is full, it waits for the `drain` event. Backpressure handling refers to congestion control. Essentially, when the receiver is about to be unable to handle more data, the sender waits a bit to send data out of consideration for the receiver. There are various implementation methods, but for example, when the receiver's buffer is about to become full, the receiver notifies the sender of the congestion state, and upon receiving this notification, the sender stores a certain amount of transmission data in a buffer.

## Server Implementation

You'll notice that the Server-side code is similar to the Client's. Therefore, we'll focus on explaining the differences. **1/ Input/output direction**: In the Client's case, `stdin` reads from the Server's `stdout` and writes to `stdin`. In the Server's case, it reads from its own `stdin` and writes to `stdout`. **2/ Process management**: The Client manages the subprocess and its lifecycle events, but the Server only deals with its own process and events. **3/ Termination handling**: The Client has the responsibility to terminate the subprocess. If you understand these differences, you should be able to read the code smoothly.

## STDIO Security

**Vulnerabilities**

In STDIO, **the risk of users themselves setting and executing untrusted commands or arguments cannot be prevented in principle**. For example, [`uvx`](https://docs.astral.sh/uv/guides/tools/), which is often seen in MCP configurations, is a command that directly uses Python packages. Using this could execute malicious libraries, potentially allowing arbitrary code execution on the system. In the current implementation, subprocesses run with the same user permissions as the parent process. If the Client has access permissions to the entire system, the subprocess executed with `uvx` will gain access permissions to the entire system. **It can collect all kinds of sensitive information such as SSH keys, passwords, environment variables, etc., and send that information externally**.

**Considering Countermeasures**

The `spawn` function can specify `uid/gid` as options, allowing for reducing the subprocess's permissions. However, this is not available on Windows. Creating users and groups to lower process permissions is necessary, and it doesn't provide complete isolation. These considerations may be why the SDK implementation doesn't control subprocess permissions.

In my personal opinion, I recommend not using STDIO casually just because there are many convenient packages. If you do use it, you need to fully understand the behavior of the commands you're executing. At the very least, avoid directly using packages like `uvx`. Even if a package seems harmless at first, there are attack methods that introduce vulnerabilities through later version updates. MCP Servers that use access keys set in environment variables pose a significant risk, so avoid using them. I recommend not using access keys at all.

As a technical **mitigation for STDIO vulnerabilities, using sandboxes is a good approach**. Docker is a good choice as it supports cross-platform. Other options for Linux include lightweight SUID programs like [Firejail](https://github.com/netblue30/firejail). It securely isolates target applications using Linux Namespaces and seccomp-bpf. Firejail can also be used in conjunction with containers.

**It is currently difficult to implement complete guardrails when using MCP in an organization**. If you allow the use of MCP Hosts like Cline or Cursor on local PCs, MCP configurations will depend on employee morals, and it's difficult to fully enforce technical risk mitigation methods even if they exist.

## Summary

In this Chapter, we explained the detailed implementation and security of STDIO. While I cannot provide a complete guide for organizational use as situations differ by organization, I hope I've conveyed the points to consider.

If I were to implement MCP security controls for an organization, I would block all MCP Host installations on local PCs by default and force users to use MCP in cloud-based sandbox instances. This would prevent MCP from accessing important confidential information on local PCs and provide users with isolated sandboxes at the instance level. Additionally, adding Firejail usage to the MCP Client's typescript-sdk would allow users to execute commands more securely without having to think about it. Make sure to check that there are no license issues with using such modified OSS in your organization.

Another approach would be to prepare your own artifact registry and force MCP Host and MCP scope package installations to be obtained from your company registry. This would reduce the risk of vulnerabilities being embedded in packages later and allow for controlled package provision.
