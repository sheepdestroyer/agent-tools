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

3.  **Wait for Feedback (Autonomous)**
    *   Use the blocking `wait` command to poll for new feedback without exiting:
    ```bash
    python3 .agent/skills/pr_review/pr_skill.py wait {PR_NUMBER} --timeout 15
    ```
    *   This command blocks until feedback is detected or timeout is reached.

4.  **Analyze & Implement**
    *   Implement fixes for all valid issues.
    *   **Loop**: Return to Step 1 until "Ready to Merge".

## Compliance
> [!IMPORTANT]
> This workflow enforces the **Standards & Rules** defined in `.agent/rules/pr-standards.md`.
> *   **Push Before Trigger**: Enforced by `pr_skill.py`.
> *   **The Loop**: Enforced by `pr_skill.py`.
> *   **Prohibitions**: Agents must **NEVER** merge, close, or delete branches.
