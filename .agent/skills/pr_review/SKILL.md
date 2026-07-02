---
name: pr-review-skill
description: Robust PR review management skill enforcing "The Loop" and "Push Before Trigger" rules.
---

# PR Review Skill

A robust skill for managing the Pull Request review cycle with AI agents. This skill enforces best practices (like pushing before triggering) programmatically, adhering to the standards in `.agent/rules/pr-standards.md`.

> [!CAUTION]
> **The Loop Rule - CRITICAL**: 
> 1. **NEVER** call `notify_user` or exit during a review cycle until `next_step` indicates "Validation Complete".
> 2. **ALWAYS** check existing feedback with `status` BEFORE triggering new reviews.
> 3. Only trigger reviews after a fresh push, not repeatedly.
> Loop: `Push → Status Check → Analyze → Fix → Repeat`.

> [!CAUTION]
> **PROHIBITED ACTIONS**:
> - **NEVER** merge a PR autonomously.
> - **NEVER** close a PR autonomously.
> - **NEVER** delete a PR branch autonomously.
> When validation is complete, **Notify the User** to perform the merge.

## Agent Instructions

**ALWAYS** parse the JSON output from these tools. 
- If `status` is `error`, STOP and address the issue (e.g., commit changes, push branch).
- If `status` is `success`, proceed based on the `message` or `items`, **unless overridden by `next_step`**.
- In all cases, inspect `next_step`. If `next_step` contains "DO NOT MERGE", **Notify the User** and exit immediately, even if `status` is `success`.

## Tools

### `safe_push`

Safely pushes local changes to the remote repository.
*   **Enforces**: Checks for uncommitted changes before pushing.
*   **Returns**: JSON object with `status` ("success" or "error"), `message`, and `next_step`.

```bash
python3 .agent/skills/pr_review/pr_skill.py safe_push
```

### `trigger_review`

Triggers new reviews from all configured bots (Gemini, CodeRabbit, Sourcery, etc.) on a specific PR.
*   **Parameters**:
    *   `pr_number` (integer, optional if `--offline` is used)
    *   `--wait` (integer, optional): Seconds to wait for initial feedback (default: 180).
    *   `--local` (flag, optional): Runs a local review using `gemini-cli-review` instead of triggering remote bots. *Note: Local Mode still requires a GitHub remote, valid authentication, and a pushed/synced local branch. It solely bypasses posting comments to GitHub, but waits 120s to fetch independent online human/bot comments.*
    *   `--offline` (flag, optional): Runs using `gemini-cli-review` locally without pushing to GitHub or checking remote state. Note this still relies on network to download `@google/gemini-cli` and access the Gemini API.
*   **Constraints**: Validates local state (clean & pushed) before triggering. If checks fail, it returns error JSON (bypassed in `--offline` mode).
*   **Polling Behavior**: After the initial wait, the tool **polls until the main reviewer responds** (up to ~10 minutes). This enforces the Loop Rule - preventing premature exit before feedback is received.
*   **Output**: JSON object with `status`, `message`, `triggered_bots`, `initial_status`, and `next_step`.

```bash
python3 .agent/skills/pr_review/pr_skill.py trigger_review <PR_NUMBER> --wait 180
```

### `status`

Checks for new feedback on a PR since a given timestamp.
*   **Parameters**:
  - `pr_number` (integer)
  - `--since` (string, ISO 8601 timestamp, e.g., `2024-01-01T12:00:00Z`). Defaults to beginning of time if omitted.
  - `--validation-reviewer` (string, optional): Username of the reviewer whose approval is required (default: `gemini-code-assist[bot]`).
*   **Behavior**: Stateless check. Returns JSON summary of new comments and reviews.
*   **Output**: JSON object with `items` list, `main_reviewer` status, and `next_step` instructions.

```bash
python3 .agent/skills/pr_review/pr_skill.py status <PR_NUMBER> --since <ISO_TIMESTAMP>
```

## Usage Example

1. **Push Changes**:
   ```bash
   python3 .agent/skills/pr_review/pr_skill.py safe_push
   # output: {"status": "success", "message": "Push successful."}
   ```

2. **Trigger Review**:
   ```bash
   python3 .agent/skills/pr_review/pr_skill.py trigger_review 123
   # output: {"status": "success", "message": "...", "initial_status": {...}}
   ```

3. **Check Status** (after waiting 2-3 minutes):
   ```bash
   python3 .agent/skills/pr_review/pr_skill.py status 123 --since 2024-01-01T12:00:00Z
   # output: {"status": "success", "items": [...], ...}
   ```

> [!TIP]
> For reliable, non-blocking status polling, you can also use **GitHub MCP tools** (`mcp_github_pull_request_read`) directly if you prefer, but this script provides a unified JSON interface.
