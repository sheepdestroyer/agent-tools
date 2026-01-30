# General Rules

## 1. Compliance
> [!IMPORTANT]
> This document enforces the **Standards & Rules** defined in `~/.gemini/rules/pr-standards.md`.
> *   **Push Before Trigger**: Enforced by `pr_skill.py`.
> *   **The Loop**: Enforced by `pr_skill.py`.
> *   **Prohibitions**: Agents must **NEVER** merge, close, or delete branches.

## 2. Context7 MCP
*   **Mandate**: Always use **Context7 MCP** (`mcp_context7_*`) when available for retrieving documentation, resolving library IDs, or querying external knowledge.
    *   *Context*: Context7 provides up-to-date documentation and AI-optimized knowledge for libraries and frameworks.
*   **Priority**: Prefer Context7 over general web search or internal knowledge assumptions for library/framework details.
