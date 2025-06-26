---
title: "§02 What is Tool Use Before We Get to MCP?"
free: true
---

___Understanding MCP:___ _Explanation of prerequisite knowledge needed to understand MCP vulnerabilities and countermeasures_

---

Tool use is also known as _Function calling_. It refers to the ability to extend AI model functionality by calling predefined external tools or functions. You provide the AI model with access to a set of predefined tools that it can call as needed.

> _Quote: [Tool use basics](https://github.com/anthropics/courses/blob/master/tool_use/01_tool_use_overview.ipynb)_

![](/images/books/security-of-the-mcp/fig_c02_s01_01.png)

## How Tool Use Works

Regarding AI models' ability to use tools, you might think that the AI model itself is calling and using tools.

At least for Anthropic's AI models,  
**The AI model itself does not have direct access to tools, nor does it directly execute tool calls.**

The functionality that informs the AI model about available tools, executes the actual tool code, and communicates the results back to the AI model exists outside the AI model.

> _Quote: [Tool use basics](https://github.com/anthropics/courses/blob/master/tool_use/01_tool_use_overview.ipynb)_

![](/images/books/security-of-the-mcp/fig_c02_s01_02.png)

Let me explain the specific steps of tool use.

**Step 1: Provide the AI model with tools and a user prompt**

- **Tool specification:** Define the set of tools you want the AI model to have access to. This includes the tool's name, description, and input schema.
- Provide a prompt that requires the use of one or more tools to answer, such as _"How many items B are left in warehouse A?"_

Below is an example of tool specification from Amazon Bedrock's Tool use [official documentation](https://docs.aws.amazon.com/ja_jp/bedrock/latest/userguide/model-parameters-anthropic-claude-messages-tool-use.html):

```json
// Tool specification
[
    {
        "name": "top_song",
        "description": "Get the most popular song played on a radio station.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sign": {
                    "type": "string",
                    "description": "The call sign for the radio station for which you want the most popular song. Example calls signs are WZPZ and WKRP."
                }
            },
            "required": [
                "sign"
            ]
        }
    }
]
```

**Step 2: AI model response regarding tool use**

- The AI model evaluates the input prompt and determines whether any of the available tools would be helpful for the user's question or task. If so, it also decides which tool to use and with what inputs.
- The AI model often outputs a properly formatted _tool use request_.
- The API response includes `stop_reason` as `tool_use`, indicating that the AI model wants to use an external tool (in Claude's case).

_Example: Tool use request_

```json
{
  "stop_reason": "tool_use",
  "tool_use": {
    "name": "inventory_lookup",
    "input": {
      "product": "B"
    }
  }
}
```

Below is an example output from Amazon Bedrock's Tool use [official documentation](https://docs.aws.amazon.com/ja_jp/bedrock/latest/userguide/model-parameters-anthropic-claude-messages-tool-use.html):

```json
// Model output
{
    "id": "msg_bdrk_01USsY5m3XRUF4FCppHP8KBx",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-sonnet-20240229",
    "stop_sequence": null,
    "usage": {
        "input_tokens": 375,
        "output_tokens": 36
    },
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_bdrk_01SnXQc6YVWD8Dom5jz7KhHy",
            "name": "top_song",
            "input": {
                "sign": "WZPZ"
            }
        }
    ],
    "stop_reason": "tool_use"
}
```

> There is no 100% guarantee that the AI model will output a perfectly appropriate _tool use request_.

**Step 3: Extract tool input, execute code, and return results**

- On the client side, extract the _tool name_ and _input_ from the _tool use request_ obtained in Step 2.
- Execute the actual tool code on the client side.
- Return the results to the AI model by continuing the conversation with a new user message that includes a `tool_result` content block.

**Step 4: AI model creates a response using the tool results**

- After receiving the tool results, the AI model uses that information to create a final response to the original prompt.

Note that _Step 3_ and _Step 4_ are actually optional. That is, if you don't continue the conversation with `tool_result`, you can just get the _tool name_ and _input_ you need and end there.
**This is very important knowledge for explaining the MCP mechanism later! Please be sure to remember this!!**

## Summary

In this Chapter, before explaining the MCP mechanism, we briefly covered the mechanism of Tool use, which is a prerequisite. Understanding this mechanism makes it clear why it's difficult to give models unlimited tools. Since there's no guarantee that tools will be used correctly, it's necessary to verify how many tools each model can handle. Domain-specific field testing is very important for improving accuracy. If many Tool uses are needed, there are several patterns such as **1/** incorporating multi-agent collaboration where each agent specializes in specific Tool uses, or **2/** retrieving information about many tool specifications through semantic search and explaining only relevant tools to the model.
