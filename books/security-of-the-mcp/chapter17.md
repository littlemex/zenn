---
title: "§17 MCP 特有の攻撃手法"
free: true
---

___MCP セキュリティに関する包括的な整理編:___ _MCP のセキュリティに対しての包括的な解説_

---

**本 Chapter では MCP で報告されている具体的ないくつかの攻撃を紹介します。** その多くが Agentic AI 特有の不確定性に基づくものであることがよく理解できるでしょう。これらの脆弱性に対して具体的にどのような対策をするべきかについては次 Chapter で解説します。

---


より重要なセキュリティ脆弱性の紹介と対策

驚かすだけじゃなくて対策もちゃんと紹介するよ。

Tool Poisoning Attack について紹介、具体的な対策も紹介

rug pull, tool shadowing も紹介a


こういった第三者が開発したソースコードを読み込んで利用者環境で実行するという構図は、拡張機能やpipやnpmといったパッケージ管理システムと類似していて、この辺りで議論されてきた脅威や緩和策を流用することができます。例えば、しばしばニュースになる偽アプリやトロイの木馬については[Name CollisionやInstaller Spoofing](https://arxiv.org/html/2503.23278)といった研究がありますし、

    * 抽象化した緩和実装 3-4 章
    * onAWS の実装パターン 5-6 章
    * 合計 30 章弱


https://thehackernews.com/2025/07/critical-vulnerability-in-anthropics.html?m=1
https://gigazine.net/news/20250709-mcp-sql-leak/



複数の MCP Server を Client に繋げるのは一定のリスクがある。