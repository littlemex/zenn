---
title: "§06 MCP Lifecycle Defines Communication Phases!"
free: true
---

___Advanced Understanding of MCP:___ _Explanation of developer-oriented knowledge necessary to understand MCP vulnerabilities and countermeasures_

---

This chapter's explanation is based on the [specification](https://modelcontextprotocol.io/specification/2025-03-26) from 2025-03-26.

MCP Specification: **Base Protocol (We are here)**, Authorization, Client Features, Server Features, Security Best Practices

In this Chapter, we will explain the [lifecycle](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle) of the Base Protocol. You might not immediately grasp what "lifecycle" means. Before explaining the lifecycle, let's review what a Protocol is.

## What is a Protocol?

A [Protocol](https://en.wikipedia.org/wiki/Protocol) is essentially a set of rules and procedures that define how multiple entities should interact to accomplish certain tasks reliably. In information engineering, it often refers to "communication protocols" that define procedures for communication between multiple entities.

There are various types of protocols. For example, authentication protocols, protocols for distributed consensus between multiple entities, network protocols for node connections, etc., exist at various layers. For instance, in network protocols, there's a procedure called the initialization sequence during hardware power-on (Power-on/PON). This is a procedure to get the hardware-level delivery between sending and receiving routers to a Ready state. After startup, each router follows appropriate procedures to reach the Ready state by understanding its own state, the state of its peer, the error rate of the transmission path, etc. Network protocols also include RAS (Reliability, Availability and Serviceability) mechanism sequences that handle notifications, error handling methods, and failure level determination during errors, in addition to initialization sequences.

In summary, **a Protocol prepares all necessary data formats to fulfill required procedures and defines rules that explain how to use them**. The algorithms for utilizing the Protocol are left to the implementation. Even in network protocols, there are many peripheral functions not defined in the Protocol, such as fault detection and notification, routing decisions during initialization, retransmission control buffer management, etc., which are defined separately as functional specifications.

## What is the MCP Lifecycle?

**The MCP lifecycle defines a series of phases from connection establishment to termination as procedures within the MCP Protocol**. Understanding that all explanations in the [MCP specification](https://modelcontextprotocol.io/specification/2025-03-26) describe how to define data formats for each procedure and how to utilize them might make it easier to organize the information.

> In my personal observation, the MCP specification has many "SHOULD" statements and many descriptions that defer to the transport layer or implementation. Therefore, I recommend reading the specification while clearly distinguishing between functions that have definitions in the MCP message format and functions that are left to the transport layer or implementation.

> _Quote: [Lifecycle](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle)_

![060101](/images/books/security-of-the-mcp/fig_c06_s01_01.png)

The lifecycle has three Phases: 1/ Initialization, 2/ Operation, and 3/ Shutdown.

Initialize is identified by the _method_ in the request object being `initialize`. Operation encompasses all cases where the _method_ contains anything other than `initialize`. For Shutdown, no specific message is defined; either the Client or Server cleanly terminates the protocol connection using the underlying transport layer mechanism.

During the Initialization Phase, the Server and Client agree on 1/ Capability Negotiation and 2/ Version Negotiation. In Capability Negotiation, both Client and Server agree on the features available during the session. In Version Negotiation, they agree on the Protocol Version of MCP itself.

> Capabilities

| Category | Feature       | Description                                                           |
|----------|--------------|-----------------------------------------------------------------------|
| Client   | roots        | Ability to provide filesystem roots                                    |
| Client   | sampling     | Support for LLM sampling requests                                      |
| Client   | experimental | Describes support for non-standard experimental features                |
| Server   | prompts      | Provides prompt templates                                              |
| Server   | resources    | Provides readable resources                                            |
| Server   | tools        | Exposes callable tools                                                 |
| Server   | logging      | Outputs structured log messages                                        |
| Server   | experimental | Describes support for non-standard experimental features                |

### Example Objects in Initialization Phase

_Client: Request Object_

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {
      "roots": {
        "listChanged": true # Support for list change notifications (for prompts, resources, tools)
      },
      "sampling": {}
    },
    "clientInfo": {
      "name": "ExampleClient",
      "version": "1.0.0"
    }
  }
}
```

_Server: Response Object_

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": {
      "logging": {},
      "prompts": {
        "listChanged": true
      },
      "resources": {
        "subscribe": true, # Support for individual item change subscription (resources only)
        "listChanged": true
      },
      "tools": {
        "listChanged": true
      }
    },
    "serverInfo": {
      "name": "ExampleServer",
      "version": "1.0.0"
    },
    "instructions": "Optional instructions for the client"
  }
}
```

## Summary

In this Chapter, we explained what a Protocol is and the MCP lifecycle. In future Chapters, the Protocol specification will continue to explain data formats (object formats) and how to use them. Therefore, we tried to explain from the perspective of how to view Protocol specifications in general. If you've understood this far, you might find it faster to understand by reading the SDK implementation or official specification directly rather than reading the subsequent knowledge-based explanations.
