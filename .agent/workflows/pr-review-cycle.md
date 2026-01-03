---
description: Official workflow for managing PR Review Cycles with AI bots (Gemini, CodeRabbit, Sourcery).
---

1.  **Preparation & Verification**
    *   Ensure all local changes are committed.
    *   **CRITICAL**: Run tests locally (`pytest`) and capture output in `tests/artifacts/`.
    *   **CRITICAL**: `git push` changes to the remote branch. *Never trigger a review on unpushed code.*

2.  **Trigger Reviews**
    *   Use the helper script to trigger reviews:
    ```bash
    python3 agent-tools/pr_helper.py trigger {PR_NUMBER}
    # Or manually:
    # gh pr comment {PR_NUMBER} --body="/gemini review"
    # gh pr comment {PR_NUMBER} --body="@coderabbitai review"
    # gh pr comment {PR_NUMBER} --body="@sourcery-ai review"
    ```

3.  **Monitor & Poll**
    *   Wait at least **3 minutes** before the first check.
    *   Use the helper script to monitor for feedback:
    ```bash
    python3 agent-tools/pr_helper.py monitor {PR_NUMBER} --since {TIMESTAMP} --output "agent-tools/agent-workspace/feedback.json"
    ```
    *   Alternatively, use Github MCP tool or `gh` CLI to check status manually if the script fails.

4.  **Analyze & Implement**
    *   Read `feedback.json`.
    *   Implement fixes for all valid issues.
    *   **Loop**: Return to Step 1 until "Ready to Merge".

> [!NOTE]
> **Pagination**: When using the `gh` CLI manually (e.g., `gh api`), ensure you use the `--paginate` flag to retrieve all comments. Default limits may hide critical feedback in long PRs.

> [!WARNING]
> **Timezones**: Always use **UTC** (Coordinated Universal Time) for all timestamps when interacting with the GitHub API. Ensure your datetime objects are timezone-aware (e.g., `tzinfo=timezone.utc`). Comparing naive (local) vs aware (API) datetimes causes crashes.

> [!CAUTION]
> **No Browser Usage**: Do **NOT** use browser tools (subagents, page reading) to interact with GitHub or monitor the PR. Use the `gh` CLI or MCP GitHub tools exclusively. Using the browser for GitHub is prohibited as it is inefficient and prone to error.
