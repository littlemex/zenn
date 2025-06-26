---
title: "§13 Authorization: Streamable HTTP Implementation Explained"
free: true
---

___Understanding MCP Implementation:___  _Explanation of developer-oriented knowledge necessary to implement MCP vulnerabilities and countermeasures_

---

This chapter's explanation is based on the [specification](https://modelcontextprotocol.io/specification/2025-06-18) from 2025-06-18.

MCP Specification: Base Protocol, **Authorization (We are here)**, Client Features, Server Features, Security Best Practices

In this Chapter, we will explain the [Client implementation](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/client/streamableHttp.ts) and [Server implementation](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/streamableHttp.ts) of Streamable HTTP in typescript-sdk (tag: 1.12.1). **In this Chapter, we will mainly explain the security-related implementation of Streamable HTTP, particularly authorization.** In the previous Chapter, we explained the authorization specification, but implementation may not yet fully comply with the specification in some cases. The implementation details of the authorization server are outside the scope of the specification. This Chapter will mainly explain from the **perspective of MCP Server implementers**. How to implement MCP Client and authorization server in AWS will be explained in the future.

## Authorization

We will **organize the responsibilities that an MCP Server must fulfill within the authorization specification** and check the implementation status of typescript-sdk for each responsibility. For where and what responsibilities the MCP Server fulfills in the overall authorization flow, please refer to Chapter 12 as needed.

### RFC9728: Providing Resource Metadata

| ID | Responsibility Detail | SDK Support |
|--|----------|------------|
| 01 | ___MUST:___ Implement `/.well-known/oauth-protected-resource` endpoint | ___Supported[Method]:___ `mcpAuthMetadataRouter` |
| 02 | ___MUST:___ Provide `authorization_servers` field | ___Supported[Schema]:___ `OAuthProtectedResourceMetadataSchema` |
| 03 | ___MUST:___ Provide metadata URL in `WWW-Authenticate` header when responding with `401 Unauthorized` | ___Supported[Method]:___ `requireBearerAuth` |
| 04 | ___MUST:___ Provide `resource` field | ___Supported[Schema]:___ `OAuthProtectedResourceMetadataSchema` |
| 05 | ___SHOULD:___ Provide `scopes_supported` field |  ___Supported[Schema]:___ `OAuthProtectedResourceMetadataSchema` |
| 06 | ___SHOULD:___ Provide `resource_name` field | ___Supported[Schema]:___ `OAuthProtectedResourceMetadataSchema` |

In RFC9728, the MCP Server functions as a resource server. A resource server is responsible for managing access to protected resources such as tools and ensuring that only clients with appropriate authorization can access them. The MCP Server has the responsibility of receiving requests from clients, verifying the access tokens included in those requests, and allowing only authorized operations.

Under RFC9728, the MCP Server needs to explicitly communicate how authentication and authorization should be performed by providing metadata about itself.

In communication between the MCP Server and Client, the MCP Server provides information about itself through a metadata endpoint (`/.well-known/oauth-protected-resource`). This metadata includes the resource identifier (`resource`) and the URIs of authorization servers (`authorization_servers`) that can issue tokens for this resource. The Client uses this information to obtain tokens from the appropriate authorization server and access the MCP Server.

___ID 02, 04, 05, 06: OAuthProtectedResourceMetadataSchema___

This schema defines the structure of resource metadata compliant with RFC9728. The required fields `resource` and `authorization_servers` are defined.

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/shared/auth.ts#L3-L23

___ID 01: mcpAuthMetadataRouter___

This `mcpAuthMetadataRouter` method provides an Express router that sets up the resource metadata endpoint, and [`metadataHandler`](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/handlers/metadata.ts#L6-L19) provides an endpoint handler that provides metadata. The `/.well-known/oauth-protected-resource` endpoint is implemented.

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/router.ts#L188-L211

___ID 03: requireBearerAuth___

This method performs Bearer token verification and sets appropriate headers when responding with 401. **1/** Verifies the token using the `verifier` provided from the authorization header, **2/** Sets the `WWW-Authenticate` header when responding with 401, includes the `resource_metadata` parameter if `resourceMetadataUrl` is specified, **3/** Adds authentication information defined by the [`AuthInfo`](https://github.com/modelcontextprotocol/typescript-sdk/blob/0506addf35f422650658c5e665ea184e3115a184/src/server/auth/types.ts#L4) interface to `req.auth` when authentication is successful.

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L40
https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L67-L79

### 2. Token Verification (OAuth 2.1 / RFC8707)

| ID | Responsibility Detail | SDK Support |
|--|----------|------------|
| 07 | ___MUST:___ OAuth 2.1: Verify Bearer token | ___Supported[Method]:___ `requireBearerAuth` |
| 08 | ___MUST:___ OAuth 2.1: RFC8707: Verify token `audience` | ___Implementation needed:___ `verifyAccessToken` |
| 09 | ___MUST:___ OAuth 2.1: Check token expiration | ___Supported[Method]:___ `requireBearerAuth` |
| 10 | ___MUST:___ OAuth 2.1: Verify scopes | ___Supported[Method]:___ `requireBearerAuth` |
| 11 | ___SHOULD:___ OAuth 2.1: Verify resource-specific scopes | ___Supported[Method]:___ `requireBearerAuth` |
| 12 | ___MUST:___ RFC8707: Prevent token misuse | ___Implementation needed:___ `verifyAccessToken` |
| 13 | ___MUST:___ RFC8707: Support resource indicators | ___Supported[Parameter]:___ `AuthorizationParams` |

Token verification is one of the most important responsibilities that an MCP Server fulfills as a resource server. Based on OAuth 2.1 and RFC8707, the MCP Server needs to verify access tokens provided by clients and ensure that only clients with appropriate authorization can access resources.

When verifying tokens, the MCP Server **1/** extracts the Bearer token from the Authorization header in the request from the Client, **2/** checks the token format and performs signature verification, **3/** verifies important claims in the token such as audience and scope, and only allows access to the requested resource if the token is determined to be valid.

`audience` specifies the target for which the token can be used. It's important for preventing the [Confused Deputy Problem](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization#confused-deputy-problem) mentioned in the MCP specification. This Confused Deputy Problem requires special consideration in MCP Servers that handle multiple tools. For example, suppose a Client has access permissions for both Tool A and Tool B. If the Client presents a token obtained for Tool A to Tool B, without audience verification, Tool B might accept that token. This would allow the Client to access Tool B's resources in an unintended way. `scope` is the process of checking the token's permission range, which determines what can be done with this token. For example, a token with a `read` scope is only allowed read operations, while a token with a `write` scope is also allowed write operations.

___ID 07, 09, 10, 11, 12: requireBearerAuth___

**ID07:** **1/** Calls `verifier.verifyAccessToken(token)` to verify the token, **2/** On successful verification, obtains `authInfo` and sets it to `req.auth`

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L53

**ID09:** **1/** If `authInfo.expiresAt` exists, compares it with the current time, **2/** Throws `InvalidTokenError` if expired

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L67-L69

**ID10:** **1/** Specifies required scopes with the `requiredScopes` parameter, **2/** Checks if the token's scope includes all required scopes, throws `InsufficientScopeError` if any are missing

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L56-L63

**ID11:** **1/** Can specify resource-specific scopes with the `requiredScopes` parameter, can set different scope requirements for different endpoints

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/middleware/bearerAuth.ts#L12-L15

___ID 08, 12: verifyAccessToken___

`verifyAccessToken` is defined as an interface, and **token verification related to RFC8707 needs to be provided by the implementer.** While future implementations are expected to follow the MCP specification, currently you need to implement measures such as obtaining the token's audience (`aud` claim) and comparing it with your own resource URI.

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/provider.ts#L77-L82

___ID 13: AuthorizationParams___

You need to add `resource?` as a parameter. There is no implementation in tag `1.12.1`, but it was implemented in `1.13.0`.

https://github.com/modelcontextprotocol/typescript-sdk/blob/1.12.1/src/server/auth/provider.ts#L6-L11

## Summary

We have confirmed that typescript-sdk **largely supports the authorization-related implementations defined in the MCP specification** necessary for MCP Server to function as a resource server. Please note that audience verification needs to be implemented by the implementer in the `verifyAccessToken` method. Also, enforcing HTTPS communication and TLS certificate verification, which are explicitly mentioned in the MCP specification, are outside the scope of the SDK and need to be appropriately addressed.
