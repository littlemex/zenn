---
title: "§09 A Deep Dive into Streamable HTTP!"
free: true
---

___Advanced Understanding of MCP:___ _Explanation of developer-oriented knowledge necessary to understand MCP vulnerabilities and countermeasures_

---

This chapter's explanation is based on the [specification](https://modelcontextprotocol.io/specification/2025-03-26) from 2025-03-26.

MCP Specification: **Base Protocol (We are here)**, Authorization, Client Features, Server Features, Security Best Practices

In this Chapter, we will explain the [transports](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports) of the Base Protocol. We discussed transports in Chapter 04, but this time we will explain them in more detail.

While JSON-RPC 2.0 is transport-independent, MCP defines two transport mechanisms for Client ↔︎ Server communication in its specification: [STDIO](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#stdio) and [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http). The specification defines how these transports should handle connections for message sending and receiving.

## Streamable HTTP

Streamable HTTP transport combines HTTP and [Server-Sent Events (SSE)](https://en.wikipedia.org/wiki/Server-sent_events) to enable bidirectional communication. While STDIO transport communicates bidirectionally through a subprocess using event handlers, Streamable HTTP communicates over a network. SSE is a technology for real-time communication from Server to Client over HTTP. MCP achieves the bidirectional communication required by using HTTP requests from Client to Server and SSE from Server to Client.

We'll explain the Streamable HTTP implementation in detail next time, but for now, let's explain an example implementation of SSE.

**About SSE**

A TypeScript sample code for SSE is provided at the end of this Chapter. In this implementation, `server.ts` starts a Server. This Server has an SSE endpoint at `/sse`. `client.ts` starts a Client. The Client sends an HTTP GET request to the Server's SSE endpoint with the `Accept: text/event-stream` header. The Server keeps this connection open and responds with status code `200`. From then on, the server uses the same connection to send data to the Client whenever an event occurs. In the example implementation, the Server sends the value of a counter to the Client every second as an event.

```bash:Server execution
$ ts-node server.ts 
Server started: http://localhost:3001
SSE endpoint: http://localhost:3001/sse
Client connected: New SSE session started
SSE event sent: Connection established message
SSE event sent: Count=1
SSE event sent: Count=2
SSE event sent: Count=3
^C
```

```bash:Client execution
$ ts-node client.ts 
SSE connection established
Received data: { message: 'Connection established' }
Received data: { count: 1, timestamp: '2025-06-10T01:48:46.018Z' }
Received data: { count: 2, timestamp: '2025-06-10T01:48:47.018Z' }
Received data: { count: 3, timestamp: '2025-06-10T01:48:48.019Z' }
Stream processing error: terminated
SSE connection ended
```

```mermaid
sequenceDiagram
    participant Client as Client
    participant Server as Server
    
    Client->>Server: HTTP GET /mcp<br>Accept: text/event-stream
    activate Server
    Server-->>Client: 200 OK<br>Content-Type: text/event-stream
    
    loop When events occur
        Server->>Client: id: event-123<br>data: {...}
    end
    
    Note over Client,Server: Connection is maintained for a long time
    
    Client->>Server: Connection disconnected
    deactivate Server
```

Since SSE is a simple mechanism built on top of HTTP, it can be implemented using basic APIs like fetch as long as you follow the appropriate headers and data format specifications. SSE is [standardized](https://html.spec.whatwg.org/multipage/server-sent-events.html#server-sent-events) by WHATWG.

**About Streamable HTTP**

The following illustrates the communication flow of Streamable HTTP using HTTP and SSE. We'll explain the details next time, but there is a single endpoint `/mcp`, with `POST` for JSON-RPC 2.0 message sending from Client to Server (arrows 1-4), and `GET` for SSE from Server to Client (arrows A-E).

```mermaid
graph TB
    subgraph "MCP Client"
        Client[Client code]
        FetchAPI[fetch API]
        Parser[eventsource-parser]
        StreamReader[ReadableStream Reader]
    end
    
    subgraph "HTTP/SSE Communication"
        HTTPPost[HTTP POST /mcp<br>Content-Type: application/json]
        SSEStream[HTTP GET /mcp<br>Accept: text/event-stream]
    end
    
    subgraph "MCP Server (Independent process)"
        Server[Server code]
        RPCHandler[JSON-RPC handler]
        EventEmitter[Event emitter]
    end
    
    %% Client → Server (JSON-RPC over HTTP POST)
    Client -->|"1. Generate JSON-RPC message"| FetchAPI
    FetchAPI -->|"2. HTTP POST {jsonrpc: ...}"| HTTPPost 
    HTTPPost -->|"3. Process request"| Server
    Server -->|"4. Method call"| RPCHandler
    
    %% Server → Client (SSE over HTTP GET)
    EventEmitter -->|"A. Event occurs"| Server
    Server -->|"B. Create SSE message"| SSEStream
    SSEStream -->|"C. Receive chunk"| StreamReader
    StreamReader -->|"D. Text decode"| Parser
    Parser -->|"E. Parse event"| Client

    %% Session management
    HTTPPost -.->|"mcp-session-id"| Client
    SSEStream -.->|"last-event-id"| Server
```

**Feature Comparison Table**

I've created a simple feature comparison table for STDIO and Streamable HTTP. Readers who have read this far should now understand what it means for MCP to be transport-independent. Transport can be STDIO, HTTP, WebSocket, or any method as long as it enables bidirectional communication between Server and Client in a way that meets the MCP specification.

| Feature | STDIO | Streamable HTTP |
|------|---------------------|---------------------------|
| Communication method | Standard input/output (stdin/stdout) | HTTP + SSE |
| Process | Launched as a subprocess | Independent process |
| Connection range | Local machine only | Network (local/remote) |
| Multiple connections | 1-to-1 connection only | Supports multiple Client connections |
| Reconnection feature | None | SSE resumption feature |
| Session management | Process lifetime | Session management possible |

## Summary

In this Chapter, we explained Streamable HTTP, one of the transports defined in the MCP Base Protocol. Understanding the conceptual part of the implementation can greatly change the resolution of security measures, so let's make sure to understand the code thoroughly. In the next Chapter, we will explain the Streamable HTTP implementation of the typescript-sdk.

## Sample Code

```bash
npm install express eventsource-parser node-fetch@3 typescript ts-node @types/express @types/node
```

```typescript:server.ts
import express from 'express';
import { SSEMessage, CONFIG } from './types';

const app = express();

// SSE endpoint
app.get(CONFIG.ENDPOINT, (req, res) => {
  console.log('Client connected: New SSE session started');
  
  // Set SSE headers
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  
  // Send initial message
  sendMessage(res, { message: "Connection established" });
  console.log('SSE event sent: Connection established message');
  
  // Send periodic messages
  let count = 0;
  const intervalId = setInterval(() => {
    const data = { 
      count: ++count, 
      timestamp: new Date().toISOString() 
    };
    sendMessage(res, data);
    console.log(`SSE event sent: Count=${count}`);
  }, CONFIG.INTERVAL_MS);
  
  // Handle client disconnection
  req.on('close', () => {
    clearInterval(intervalId);
    console.log('SSE session ended: Client disconnected');
  });
});

// Helper function to send messages
function sendMessage(res: express.Response, data: SSEMessage): void {
  const eventId = `event-${Date.now()}`;
  const message = `id: ${eventId}\ndata: ${JSON.stringify(data)}\n\n`;
  res.write(message);
}

// Start server
app.listen(CONFIG.PORT, () => {
  console.log(`Server started: http://${CONFIG.HOST}:${CONFIG.PORT}`);
  console.log(`SSE endpoint: http://${CONFIG.HOST}:${CONFIG.PORT}${CONFIG.ENDPOINT}`);
});
```

```typescript:client.ts
import { createParser, type EventSourceMessage } from 'eventsource-parser';
import { SSEMessage, CONFIG } from './types';

async function connectToSSE(): Promise<void> {
  try {
    const url = `http://${CONFIG.HOST}:${CONFIG.PORT}${CONFIG.ENDPOINT}`;
    const response = await fetch(url, {
      headers: { 'Accept': 'text/event-stream' }
    });
    
    if (!response.ok) {
      throw new Error(`HTTP error: ${response.status}`);
    }
    
    console.log('SSE connection established');
    await processEventStream(response);
    
  } catch (error) {
    console.error('Connection error:', error instanceof Error ? error.message : String(error));
  }
}

async function processEventStream(response: Response): Promise<void> {
  if (!response.body) {
    throw new Error('Response body is empty');
  }
  
  // Pass the ParserCallbacks object in the correct format
  const parser = createParser({
    onEvent(event: EventSourceMessage) {
      if (event.data) {
        try {
          const data = JSON.parse(event.data) as SSEMessage;
          console.log('Received data:', data);
        } catch {
          console.log('Raw data:', event.data);
        }
      }
    },
    onError(err) {
      console.error('Parse error:', err);
    }
  });
  
  const reader = response.body.getReader();
  
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      
      const chunk = new TextDecoder().decode(value);
      parser.feed(chunk);
    }
  } catch (error) {
    console.error('Stream processing error:', error instanceof Error ? error.message : String(error));
  } finally {
    console.log('SSE connection ended');
  }
}

// Start SSE connection
connectToSSE();
```

```typescript:types.ts
export interface SSEMessage {
  count?: number;
  timestamp?: string;
  message?: string;
}

export const CONFIG = {
  PORT: 3001,
  HOST: 'localhost',
  ENDPOINT: '/sse',
  INTERVAL_MS: 1000
};
```

```json:tsconfig.json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "esModuleInterop": true,
    "strict": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "outDir": "dist",
    "lib": ["ES2020", "DOM"]
  },
  "include": ["*.ts"]
}
```
