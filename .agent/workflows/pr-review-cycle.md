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
    python3 agent-tools/.agent/skills/pr_review/pr_skill.py trigger {PR_NUMBER}
    ```

3.  **Monitor Status (Stateless)**
    *   Wait at least **3 minutes** before the first check.
    *   Check for review status (returns immediately, does not hang):
    ```bash
    python3 agent-tools/.agent/skills/pr_review/pr_skill.py status {PR_NUMBER} --since {TIMESTAMP}
    ```
    *   Alternatively, use Github MCP tool or `gh` CLI to check status manually if the script fails.

4.  **Analyze & Implement**
    *   Read `feedback.json`.
    *   Implement fixes for all valid issues.
    *   **Loop**: Return to Step 1 until "Ready to Merge".

## Compliance
> [!IMPORTANT]
> This workflow enforces the **Standards & Rules** defined in `.agent/rules/pr-standards.md`.
> *   **Push Before Trigger**: Enforced by `pr_skill.py`.
> *   **The Loop**: Enforced by `pr_skill.py`.
> *   **Prohibitions**: Agents must **NEVER** merge, close, or delete branches.
