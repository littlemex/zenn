---
title: ""
---


Enterprise-Grade Security for the Model Context Protocol (MCP): Frameworks and Mitigation Strategies
Author: Vineeth Sai Narajala, Amazon Web Services, Generative AI Security Engineer
著者の実績: Amazon の主力製品（Amazon Q および Bedrock）向けに、ガードレール、プロンプトインジェクション保護、コンピューティング分離、セッション管理などを含む包括的な GenAI セキュリティのベストプラクティスと標準の開発を先導し、組織全体にそれらを適用するためのメカニズムを実装。

上記 reference の脆弱性と対策をまとめ、実装は複数 reference から本資料作者が整理した内容を紹介する。
Agentic AI の対策の多くは既存セキュリティベストプラクティスに則る。本資料は既存の脆弱性とプラクティスについては概要のみを列挙し、MCP 特有の脆弱性に主に焦点を当てる。


maestro の紹介も実施する。

典型的なこれまでの攻撃、と、Agentic AI 特有の脆弱性は分けて考えよう。これまでのものは適切に対応しよう。
不確定性の部分をきっかけに古典的な攻撃に繋げる可能性もある。
https://blog.flatt.tech/entry/mcp_security_second



図を貼って、おおよそはすでに紹介しているけど、Agentic AI 特有の脆弱性があるから紹介するよ。



必須: DNSリバインディング攻撃対策




より重要なセキュリティ脆弱性の紹介と対策


    * 抽象化した緩和実装 3-4 章
    * onAWS の実装パターン 5-6 章
    * 合計 30 章弱
