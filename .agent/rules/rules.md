# General Rules

## 1. Context7 MCP
*   **Mandate**: Always use **Context7 MCP** (`mcp_context7_*`) when available for retrieving documentation, resolving library IDs, or querying external knowledge.
    *   *Context*: Context7 provides up-to-date documentation and AI-optimized knowledge for libraries and frameworks.
*   **Priority**: Prefer Context7 over general web search or internal knowledge assumptions for library/framework details.

## 2. CLI Tool Verbosity
*   **Mandate**: When running verbose CLI linting or analysis tools (like `pylint`), you MUST use quiet or errors-only flags (e.g., `pylint -E` or `pylint --errors-only`) to reduce output volume.
    *   *Context*: Printing thousands of lines of style warnings into the context window wastes tokens and obscures critical failures.
*   **Priority**: Prioritize addressing actual errors over stylistic warnings during automated cycles unless specifically requested.

## 3. Safe Git Push Workflow
*   **Mandate**: You must ALWAYS use the safe-push pattern before pushing to a remote branch to prevent merge conflicts and push rejections. This means executing a fetch, followed by a rebase, and then a push:
    ```bash
    git fetch origin <branch> && git rebase origin/<branch> && git push origin <branch>
    ```
    *Note: You may use the global `git sync-push` alias if available, or `git pull --rebase`.*
    *   *Context*: Bots and continuous integration systems often push commits (like formatting or auto-fixes) directly to the remote branch while you are working. Blindly running `git push` without pulling and rebasing first will frequently lead to rejection errors and wasted context tokens trying to recover.
*   **Priority**: This pattern is a strict requirement for all automated git operations.
