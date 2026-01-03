# PR Review Standards & Rules

## 1. The Loop Rule
A Review Cycle is a **LOOP**, not a check.
*   **Definition**: A cycle is `Push -> Trigger -> Poll -> Analyze -> Fix -> REPEAT`.
*   **Exit Condition**: You may ONLY exit the loop when the reviewer explicitly states "Ready to Merge" or "No issues found".
*   **Prohibition**: Never stop after fixing issues without re-verifying with the bot.

## 2. Push Before Trigger
**STRICT RULE**: You MUST `git push` your changes BEFORE triggering a review.
*   Triggering a review on unpushed code results in outdated feedback and wastes API rate limits.
*   Always verify `git status` is clean and `git log` shows your commit before running `gh pr comment`.

## 3. Artifact Hygiene
*   **Test Artifacts**: All test output files (e.g., `pytest_output.txt`, `coverage.xml`) MUST be placed in `tests/artifacts/`.
*   **Root Directory**: Do NOT write temporary files, logs, or debug dumps to the repository root.
*   **Agent Workspace**: Use `agent-tools/agent-workspace/` for operational logs (e.g., `feedback.json`).

## 4. Polling & Wait Times
*   **Initial Wait**: Wait **at least 3 minutes** after requesting a review to allow bots to process.
*   **Poll Interval**: Check for feedback every **2 minutes`.
*   **Timeout**: Set a reasonable timeout (e.g., 15-25 minutes) to avoid infinite loops, but do not give up early.

## 5. Tool Usage
*   **Primary Tool**: Use `agent-tools/pr_helper.py` for triggering and monitoring.
*   **Fallback**: Use Github MCP or `gh` CLI directly if the helper encounters issues.
*   **Path Safety**: Ensure all file paths passed to tools are validated to be within the project root.

## 6. Bot Etiquette
*   **Gemini Code Assist**: Use `/gemini review` for general code review.
*   **CodeRabbit**: Use `@coderabbitai review` for deep static analysis and logical bugs.
*   **Sourcery**: Use `@sourcery-ai review` for Pythonic refactoring suggestions.
*   **Respect**: Address all actionable feedback. If a bot suggests a fix that is wrong, explain why in a comment or ignore it if trivial, but prefer to address it if possible.

## 7. CLI Pagination
*   **Mandatory Flag**: When using `gh api` to fetch comments or reviews, YOU MUST ALWAYS use `--paginate`.
    *   *Reason*: Large PRs often exceed the default page size (30 items). Without `--paginate`, validation cycles may miss critical feedback or approval states.

## 8. Timestamp Precision
*   **Timezones**: Always use **UTC** (Coordinated Universal Time) for all timestamps interaction with GitHub API.
*   **Awareness**: Ensure your datetime objects are checking timezone-aware (e.g., `tzinfo=timezone.utc`). Comparing naive (local) vs aware (API) datetimes causes crashes.
*   **Filtering**: When filtering comments by time (e.g., `--since`), provide the timestamp in ISO 8601 UTC format (`YYYY-MM-DDTHH:MM:SSZ`) to ensure accurate retrieval.

## 9. Agent Autonomy
*   **No Idling**: Agents must actively monitor polling scripts. Do NOT exit/notify the user just to wait for a bot or a long process.
*   **Autonomous Action**: Agents are AUTHORIZED and REQUIRED to `git commit` and `git push` fixes autonomously if tests pass. Do not ask the user to push for you.
*   **Self-Correction**: If a tool fails (e.g., specific monitoring script), fallback to raw `gh` commands immediately.

## 10. Direct Tool Usage Only
**PROHIBITION**: You must **NEVER** use browser tools (including `browser_subagent`) to check Pull Request status, review comments, or checks on GitHub. 
*   **Reason**: Browser tools are inefficient and inconsistent for structured data extraction compared to the `gh` CLI and the GitHub MCP server.
*   **Mandate**: Use `gh` CLI or MCP `pull_request_read` tools for all GitHub interactions.
