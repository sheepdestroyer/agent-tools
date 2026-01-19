---
description: Official workflow for managing PR Review Cycles with AI bots (Gemini, CodeRabbit, Sourcery, Qodo, and Ellipsis).
---

1.  **Preparation & Verification**
    *   Ensure all local changes are committed.
    *   **CRITICAL**: Run tests locally (`pytest`) and capture output in `tests/artifacts/`.
    *   **MANDATORY**: *All test suites must pass before pushing changes.*
    *   **CRITICAL**: `git push` changes to the remote branch. *Never trigger a review on unpushed code.*

// turbo
2.  **Trigger Reviews (Robust)**
    *   Use the robust skill to trigger reviews (automatically checks for unpushed changes):
    ```bash
    python3 .agent/skills/pr_review/pr_skill.py trigger_review {PR_NUMBER}
    ```

3.  **Wait and Check Status**
    *   Wait **3 minutes** for bots to process.
    *   Use PR status check for quick verification:
    ```bash
    python3 .agent/skills/pr_review/pr_skill.py status {PR_NUMBER} --since {TIMESTAMP}
    ```
    *   Alternatively, use **GitHub MCP tools** (`mcp_github_pull_request_read`) for reliable, non-blocking status polling.

4.  **Analyze & Implement**
    *   Review feedback and implement fixes for all valid issues.
    *   **Loop**: Return to Step 1 until "Ready to Merge".

## Compliance
> [!IMPORTANT]
> This workflow enforces the **Standards & Rules** defined in `.agent/rules/pr-standards.md`.
> *   **Push Before Trigger**: Enforced by `pr_skill.py`.
> *   **The Loop**: Enforced by `pr_skill.py`.
> *   **Prohibitions**: Agents must **NEVER** merge, close, or delete branches.
