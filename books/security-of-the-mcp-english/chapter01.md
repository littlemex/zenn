---
title: "§01 Why is MCP Rapidly Gaining Popularity?"
free: true
---

___Understanding MCP:___ _Explanation of prerequisite knowledge needed to understand MCP vulnerabilities and countermeasures_

---

[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) announced by [Anthropic](https://www.anthropic.com/) is a unified interface for AI models to access various tools. It is likened to being the **USB-C standard** that connects AI models with tools.

![](/images/books/security-of-the-mcp/fig_c01_s01_01.png)

For example, when wanting to know today's weather in Tokyo, the AI model does not have information about today's Tokyo weather. In such cases, if there is a weather forecast MCP, the AI model can utilize the weather forecast MCP to collect information about today's Tokyo weather forecast and summarize it in an easy-to-understand way.

## Evolution of Tool Usage

You might think, _"Wait, couldn't AI models use weather forecast APIs as tools even before MCP came along?"_ **That's absolutely correct!**

MCP likely has no technical innovation. Anthropic announced ["Claude can now use tools"](https://www.anthropic.com/news/tool-use-ga) blog on May 31, 2024, and external tools became available through Claude on [Amazon Bedrock](https://aws.amazon.com/jp/bedrock/) as well. Going further back, [AI21 Labs](https://www.ai21.com/) mentioned in their 2022 paper ["Bibliography management: BibTeX"](https://arxiv.org/abs/2205.00445) that they implemented a feature called the Modular Reasoning Knowledge Language (MRKL) system in their Jurassic-X that supports external API calls.

**So why is the MCP ecosystem developing so rapidly?**

Let's consider a case where a company needs to implement M AI applications, and each application needs to use N tools. In this case, `M×N` custom integrations need to be built and maintained. With the emergence of MCP as a unified protocol interface for communication, this `M×N` complexity can be **reduced to `M+N`**. Many companies might be able to create new sales channels by making their services available via MCP. And users can integrate external tools into AI applications with simple implementation steps.

> _Quote: [Unlocking the power of Model Context Protocol (MCP) on AWS](https://aws.amazon.com/jp/blogs/machine-learning/unlocking-the-power-of-model-context-protocol-mcp-on-aws/)_

![](/images/books/security-of-the-mcp/fig_c01_s01_02.png)

## Convenience and Vulnerabilities

As mentioned above, MCP is highly convenient, and its potential as a sales channel from a business perspective cannot be ignored. This trend may accelerate as MCP's Marketplace further develops as a sales channel. However, **MCP has many reported security vulnerabilities**, and both MCP providers and users need to implement appropriate security measures. MCP's security specifications are not yet fully established, and comprehensive specifications and security best practices will be established in the future.

When considering `M×N` complexity, the complexity in the case of `M=1` is `N`. In this case, whether it is appropriate to accept vulnerabilities and use MCP should be carefully considered based on business requirements.

## Summary

In this Chapter, we briefly touched on MCP and the evolution of tool usage, and explained one example of why MCP is developing rapidly. We are in a transitional period where security measures have not caught up with rapid development, and I hope you recognize that **careful usage is required**, especially in enterprises.

A [post](https://x.com/MCP_Community/status/1934385740298985980) by `@MCP_Community` on X shows that there have been over 20,000 (presumably referring to the number of MCP Servers published) in about half a year since MCP's release, quantitatively confirming its rapid development.

![](/images/books/security-of-the-mcp/fig_c01_s01_03.png)
