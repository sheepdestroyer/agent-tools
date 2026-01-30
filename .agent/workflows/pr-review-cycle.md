---
description: Official workflow for managing PR Review Cycles with AI bots (Gemini, CodeRabbit, Sourcery, Qodo, and Ellipsis).
---

1.  **Preparation & Verification**
    *   Ensure all local changes are committed.
    *   **CRITICAL**: Run tests locally (`pytest`) and capture output in `tests/artifacts/`.
    *   **MANDATORY**: *All test suites must pass before pushing changes.*
    *   **CRITICAL**: `git push` changes to the remote branch. *Never trigger a review on unpushed code.*

2.  **Check Existing Status FIRST**
    *   **ALWAYS** check for existing feedback before triggering new reviews:
    ```bash
    python3 ~/.gemini/antigravity/skills/pr_review/pr_skill.py status {PR_NUMBER} --since {TIMESTAMP}
    ```
    *   If there are unaddressed issues, skip to Step 4 (Analyze & Implement).
    *   Only proceed to Step 3 if no existing feedback or all feedback has been addressed.

3.  **Trigger Reviews (Only When Needed)**
    *   Use the robust skill to trigger reviews (automatically checks for unpushed changes):
    ```bash
    python3 ~/.gemini/antigravity/skills/pr_review/pr_skill.py trigger_review {PR_NUMBER}
    ```
    *   Wait **3 minutes** for bots to process, then return to Step 2.

4.  **Analyze & Implement**
    *   **Freshness**: Pull and merge latest changes from remote branch (bots may have pushed formatting fixes).
    *   **Completeness**: Address every comment and code review. Ensure CI passes.
    *   Review feedback and implement fixes for all valid issues.
    *   **Loop**: Return to Step 1 until "Ready to Merge".

## Compliance
> [!IMPORTANT]
> This workflow enforces the **Standards & Rules** defined in `~/.gemini/rules/pr-standards.md`.
> *   **Push Before Trigger**: Enforced by `pr_skill.py`.
> *   **The Loop**: Enforced by `pr_skill.py`.
> *   **Prohibitions**: Agents must **NEVER** merge, close, or delete branches.
