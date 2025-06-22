---
title: ""
---


    * onAWS の実装パターン 5-6 章
    * 合計 30 章弱


    API GW はセッション切っても問題ないから 30 s 制限的に問題ないかも？

基本的には MCP Server に認証認可の責務を直で持たせないことを薦める。local 実験のためには認証なしで利用したいこともある。

結論から言うと、Authorization Server を別途実装せず、既存のものを利用することで、MCP Server の認可を実装することができます。 その場合、MCP Server は Resource Server として機能し、Authorization Server から発行される Access Token を用いてアクセス制御を行います。

https://aws.amazon.com/jp/about-aws/whats-new/2025/06/express-js-developers-authorization-amazon-verified-permissions/
クライアントは verified permission で認証実装する。