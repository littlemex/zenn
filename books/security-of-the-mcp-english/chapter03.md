---
title: "§03 Tool Use vs. MCP: Mechanism Comparison"
free: true
---

___Understanding MCP:___ _Explanation of prerequisite knowledge needed to understand MCP vulnerabilities and countermeasures_

---

This chapter's explanation is based on the [specification](https://modelcontextprotocol.io/specification/2025-03-26) from 2025-03-26.

MCP Specification: **Base Protocol (We are here)**, Authorization, Client Features, Server Features, Security Best Practices

Let's compare Tool use and MCP, both of which are used by AI models.

![030101](/images/books/security-of-the-mcp/fig_c03_s01_01.png)

First, let's review the key points about Tool use. **1/ The AI model receives a prompt from the user**, **2/ The AI model determines the necessary tools and inputs**, after which the client-side executes the tool call and passes the tool execution results to the AI model to generate the final response.

## How MCP Works

The main purpose of MCP is to standardize 1/ tool definitions and 2/ tool call processing, which are provider-dependent in Tool use. The important point here is that **the AI model's capabilities are essentially unchanged from Tool use**.

![030102](/images/books/security-of-the-mcp/fig_c03_s01_02.png)

**0/** The MCP Client standardizes how tools are exposed to the AI model through tool definitions provided by the MCP Server. This allows the AI model to easily discover available tools, understand their schemas, and use them. **1/** When the MCP Client receives a user prompt, it passes the prompt to the AI model. **2/** Similar to Tool use, the AI model determines the necessary tools and inputs based on the prompt and tool definitions. Then, instead of directly calling the tools in the MCP Client, **5/** it executes a _tool use request_ to the MCP Server. **6/** The MCP Client receives the tool execution results and **8/** passes the tool execution results and user prompt to the AI model, **9/** which generates the final response.

Let me explain the commonalities between MCP and Tool use more clearly. Although hidden by the MCP Client, the input that the AI model receives consists of the _user prompt_ and _tool definitions_. And the output is a _tool use request_, which contains the name of the tool to use and the information to be used as arguments for that tool. This part is the same as Tool use.

The MCP Server is separated from the AI model and focuses on the actual tool invocation and tool definitions. And the MCP Client handles input/output with the AI model, tool-related interactions with the MCP Server, and input/output with the user.

## Summary

In this Chapter, we explained the mechanism of MCP by comparing it with Tool use. The value of MCP lies in dividing responsibilities between Client and Server, allowing tools to be used through a unified interface. On the other hand, this increases the number of components, leading to new considerations such as vulnerabilities in communication between components and methods for establishing trust relationships. In the following Chapters, we will explain the MCP components and their functions.
