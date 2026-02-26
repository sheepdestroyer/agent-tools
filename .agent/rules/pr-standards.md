# PR Review Standards & Rules

> [!IMPORTANT]
> **Canonical Source**: This file defines the authoritative PR standards and rules.
> All agents MUST follow the standards defined here.

## 1. The Loop Rule
A Review Cycle is a **LOOP**, not a check.
*   **Definition**: A cycle is `Push -> Check Status -> Analyze -> Fix -> REPEAT`. Only trigger new reviews after a push, not repeatedly. *(Exception: The Phase 1 Offline Mode loop does not push).*
*   **Exit Condition**: You may ONLY exit the loop when the reviewer explicitly states "Ready to Merge", "No issues found", or if the very latest gemini-code-assist bot comment states it is currently rate limited (ignoring previous, expired warnings). *If rate limited, switch to Local Mode.*
*   **Prohibition**: Never stop after fixing issues without re-verifying with the bot. Never trigger new reviews without first checking existing feedback using `pr_skill.py status`.

## 2. Push Before Trigger
**STRICT RULE**: You MUST `git push` your changes BEFORE triggering an online or local review.
*   *Note: This rule is bypassed during the Phase 1 Offline Pre-Review loop where changes are reviewed completely locally before pushing.*
*   **Single Push Per Cycle**: Between online loops, only one `git push` must happen per cycle: just before triggering online reviews. Do NOT push intermediate offline fixes or CI check attempts individually. Batch them up.
*   **Mandatory Testing**: All test suites must pass before pushing changes.
*   Triggering a review on unpushed code results in outdated feedback and wastes API rate limits.
*   Always verify `git status` is clean and `git log` shows your commit before running `gh pr comment`.

## 3. Artifact Hygiene
*   **Test Artifacts**: All test output files (e.g., `pytest_output.txt`, `coverage.xml`) MUST be placed in `tests/artifacts/`.
*   **Root Directory**: Do NOT write temporary files, logs, or debug dumps to the repository root.
*   **Agent Workspace**: Use `agent-tools/agent-workspace/` for operational logs (e.g., `feedback.json`).

## 4. Polling & Wait Times
*   **Initial Wait**: Wait **at least 3 minutes** after requesting a review to allow bots to process.
*   **Poll Interval**: Check for feedback every **2 minutes**.
*   **Timeout**: Set a reasonable timeout (e.g., 15-25 minutes) to avoid infinite loops, but do not give up early.

## 5. Tool Usage
*   **Primary Tool**: Use `.agent/skills/pr_review/pr_skill.py` for triggering, status checks, and safe pushing.
*   **Fallback**: Use **GitHub MCP** tools as the first fallback. Use `gh` CLI only if MCP tools are unavailable or failing.
*   **Path Safety**: Ensure all file paths passed to tools are validated to be within the project root.

## 6. Bot Etiquette
*   **Gemini Code Assist**: Use `/gemini review` for general code review.
*   **CodeRabbit**: Use `@coderabbitai review` for deep static analysis and logical bugs.
*   **Sourcery**: Use `@sourcery-ai review` for Pythonic refactoring suggestions.
*   **Qodo**: Use `/review` for qodo-code-review.
*   **Ellipsis**: Use `@ellipsis review this` for ellipsis-dev reviews.
*   **Respect**: Address all actionable feedback. If a bot suggests a fix that is wrong, explain why in a comment or ignore it if trivial, but prefer to address it if possible.

## 7. CLI Pagination
*   **Mandatory Flag**: When using `gh api` to fetch comments or reviews, YOU MUST ALWAYS use `--paginate`.
    *   *Reason*: Large PRs often exceed the default page size (30 items). Without `--paginate`, validation cycles may miss critical feedback or approval states.

## 8. Timestamp Precision
*   **Timezones**: Always use **UTC** (Coordinated Universal Time) for all timestamps interaction with GitHub API.
*   **Awareness**: Ensure your datetime objects are checking timezone-aware (e.g., `tzinfo=timezone.utc`). Comparing naive (local) vs aware (API) datetimes causes crashes.
*   **Filtering**: When filtering comments by time (e.g., `--since`), provide the timestamp in ISO 8601 UTC format (`YYYY-MM-DDTHH:MM:SSZ`) to ensure accurate retrieval.

## 9. Agent Autonomy
*   **No Idling**: Agents must actively monitor PR status. Do NOT exit/notify the user just to wait for a bot or a long process.
*   **Polling Strategy**: Use GitHub MCP tools (`mcp_github_pull_request_read`) for reliable, non-blocking status polling. Wait ~3 minutes after triggering before first check, then poll every 2 minutes.
*   **Autonomous Action**: Agents are AUTHORIZED and REQUIRED to `git commit` and `git push` fixes autonomously if tests pass. Do not ask the user to push for you.
*   **Self-Correction**: If a tool fails (e.g., specific monitoring script), fallback to GitHub MCP or raw `gh` commands immediately.

## 10. Direct Tool Usage Only
**PROHIBITION**: You must **NEVER** use browser tools (including `browser_subagent`) to check Pull Request status, review comments, or checks on GitHub. 
*   **Reason**: Browser tools are inefficient and inconsistent for structured data extraction compared to the GitHub MCP server and the `gh` CLI.
*   **Mandate**: Use `.agent/skills/pr_review/pr_skill.py`, GitHub MCP tools, or `gh` CLI tool for all GitHub interactions. 

## 11. Critical Prohibitions
**ABSOLUTE RULE**: Agents must **NEVER** merge a PR, close a PR, or delete a PR's branch.
*   **Reason**: Merging, closing, and branch deletion are privileged actions reserved for human maintainers or specific CI/CD pipelines.
*   **Action**: If you believe a PR is ready to be merged or should be closed, you must notify the user and await instructions. **Do NOT** execute commands to merge, close, or delete the branch.
