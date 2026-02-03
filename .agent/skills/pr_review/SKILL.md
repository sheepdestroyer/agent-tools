---
name: pr-review-skill
description: Robust PR review management skill enforcing "The Loop" and "Push Before Trigger" rules.
---

# PR Review Skill

A robust skill for managing the Pull Request review cycle with AI agents. This skill enforces best practices (like pushing before triggering) programmatically, adhering to the standards in `~/.gemini/rules/pr-standards.md`.

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

### Mandatory Behavior Rules (Enforced by Tool Output)
1. **Autonomy**: "Be autonomous, don't stop the cycle. You must regularly poll for the main-reviewer's review until it eventually gets posted, then continue"
2. **Freshness**: "Pull with rebase to get the latest changes from the remote branch before starting to address code reviews, as bots may have since pushed formatting fixes to your previous changes"
3. **Completeness**: "Be sure to address all comments and code reviews from all reviewers, ensure CI passes"
4. **Quality**: "Be sure to run and fix all available tests and Linting before pushing your next changes"
5. **Rate Limits**: "If main reviewer says it just became rate-limited, address remaining code reviews then stop there"
6. **Prohibitions**: "Never merge or delete a branch on your own, if you believe the main reviewer said that the PR is ready, just stop and ask for Human review"
7. **Crash Recovery**: "If the loop dies unexpectedly, run `resume` to continue from the last checkpoint"

## Loop Resilience

The skill implements crash recovery to prevent lost progress:

*   **State Persistence**: Loop state is saved to `agent-workspace/loop_state.json` after each poll cycle
*   **Heartbeat Sleep**: Long waits are broken into 5-second chunks with progress output
*   **Resume Command**: If the loop is interrupted, run `resume` to continue from the last checkpoint

## Tools

### `safe_push`

Safely pushes local changes to the remote repository.
*   **Enforces**: Checks for uncommitted changes before pushing.
*   **Returns**: JSON object with `status` ("success" or "error"), `message`, and `next_step`.

```bash
python3 ~/.gemini/antigravity/skills/pr_review/pr_skill.py safe_push
```

### `trigger_review`

Triggers new reviews from all configured bots (Gemini, CodeRabbit, Sourcery, etc.) on a specific PR.
*   **Parameters**:
  *   `pr_number` (integer)
  *   `--wait` (integer, optional): Seconds to wait for initial feedback (default: 180).
*   **Constraints**: Validates local state (clean & pushed) before triggering. If checks fail, it returns error JSON.
*   **Polling Behavior**: After the initial wait, the tool **polls until the main reviewer responds** (up to ~10 minutes). This enforces the Loop Rule - preventing premature exit before feedback is received.
*   **Output**: JSON object with `status`, `message`, `triggered_bots`, `initial_status`, and `next_step`.

```bash
python3 ~/.gemini/antigravity/skills/pr_review/pr_skill.py trigger_review <PR_NUMBER> --wait 180
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
python3 ~/.gemini/antigravity/skills/pr_review/pr_skill.py status <PR_NUMBER> --since <ISO_TIMESTAMP>
```

## Usage Example

1. **Push Changes**:
   ```bash
   python3 ~/.gemini/antigravity/skills/pr_review/pr_skill.py safe_push
   # output: {"status": "success", "message": "Push successful."}
   ```

2. **Trigger Review**:
   ```bash
   python3 ~/.gemini/antigravity/skills/pr_review/pr_skill.py trigger_review 123
   # output: {"status": "success", "message": "...", "initial_status": {...}}
   ```

3. **Check Status** (after waiting 2-3 minutes):
   ```bash
   python3 ~/.gemini/antigravity/skills/pr_review/pr_skill.py status 123 --since 2024-01-01T12:00:00Z
   # output: {"status": "success", "items": [...], ...}
   ```

> [!TIP]
> For reliable, non-blocking status polling, you can also use **GitHub MCP tools** (`mcp_github_pull_request_read`) directly if you prefer, but this script provides a unified JSON interface.

4. **Resume After Crash**:
   ```bash
   python3 ~/.gemini/antigravity/skills/pr_review/pr_skill.py resume
   # output: {"status": "resumed", "message": "Resumed loop for PR #123..."}
   ```
