---
title: "§07 A Deep Dive into STDIO!"
free: true
---

___Advanced Understanding of MCP:___ _Explanation of developer-oriented knowledge necessary to understand MCP vulnerabilities and countermeasures_

---

This chapter's explanation is based on the [specification](https://modelcontextprotocol.io/specification/2025-06-18) from 2025-06-18.

MCP Specification: **Base Protocol (We are here)**, Authorization, Client Features, Server Features, Security Best Practices

In this Chapter, we will explain the [transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) of the Base Protocol. We discussed transports in Chapter 04, but this time we will explain them in more detail.

While JSON-RPC 2.0 is transport-independent, MCP defines two transport mechanisms for Client ↔︎ Server communication in its specification: [STDIO](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#stdio) and [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http). The specification defines how these transports should handle connections for message sending and receiving.

## STDIO

In the case of STDIO transport, the Client launches the MCP Server as a subprocess. A subprocess is a mechanism to provide commands or programs **independently** from the main process. For example, in Node.js, this can be achieved through the `child_process` module. A sample code for subprocess generation using TypeScript is provided at the end of this Chapter. It's a simple example that just runs `echo -e hello\nmcp!` in a separate process.

```bash:Execution result
$ ts-node sample.ts
Command execution result:
hello
mcp!
```

```mermaid
graph TB
    subgraph "MCP Client"
        Client[Client code]
        StdoutListener["stdout.on()"]
    end
    
    subgraph "Subprocess communication"
        Stdin[stdin]
        Stdout[stdout]
    end
    
    subgraph "MCP Server (Subprocess)"
        Server[Server code]
    end
    
    Client -->|"Send message"| Stdin -->|"Input data"| Server
    Server -->|"Output message"| Stdout -->|"data event fired"| StdoutListener -->|"Execute callback"| Client

```

The ChildProcess class inherits from [`EventEmitter`](https://nodejs.org/ja/learn/asynchronous-work/the-nodejs-event-emitter). Therefore, it supports an event-driven model, where when some event occurs, it calls a function corresponding to that event. The `on(event, listener)` method can be used to register event listener functions for specific events. The ChildProcess object is associated with three streams: `child.stdout`, `child.stdin`, and `child.stderr`.

Every time the MCP Server writes something to the standard output, a `data` event is issued on the `stdout` stream object. The Client receives the data and performs some processing through an event listener that is triggered when the `data` event is issued.

https://github.com/littlemex/samples/blob/main/mcp-sec-book/chapter07/sample.ts#L13-L15

STDIO is a method where the Client and Server exchange data through a child process using such a mechanism. In practice, there are implementations that interpret messages in JSON RPC 2.0 format from the output. While we'll omit the Server-side implementation, messages from the Server are passed to the Client as messages through an event-driven approach.

**Error Handling**

When the Server encounters an error that prevents it from continuing to process messages, it should **1/** write an appropriate JSON-RPC error message to stdout, **2/** exit with a non-zero status code. The Client should monitor the server process and handle any unexpected termination.

**Security**

Implementers should be aware of the following considerations: **1/ Process Isolation** Ensure proper isolation of the Server subprocess to prevent privilege escalation, **2/ Resource Limiting** Implement timeouts and resource limits to prevent denial-of-service attacks, **3/ Input Validation** Validate all input from the subprocess to prevent injection attacks, **4/ Error Handling** Handle subprocess errors gracefully to prevent information leakage.

## Summary

In this Chapter, we explained STDIO, one of the transports defined in the MCP Base Protocol. Understanding the conceptual part of the implementation can greatly change the resolution of security measures, so let's make sure to understand the code thoroughly. In the next Chapter, we will explain the STDIO implementation of the typescript-sdk.

## Sample Code

The sample code is available [here](https://github.com/littlemex/samples/tree/main/mcp-sec-book/chapter07).

https://github.com/littlemex/samples/blob/main/mcp-sec-book/chapter07/sample.ts
