---
name: pr-review-skill
description: Robust PR review management skill enforcing "The Loop" and "Push Before Trigger" rules.
---

# PR Review Skill

A robust skill for managing the Pull Request review cycle with AI agents. This skill enforces best practices (like pushing before triggering) programmatically, adhering to the standards in `.agent/rules/pr-standards.md`.

## Tools

### `safe_push`

Safely pushes local changes to the remote repository.
*   **Enforces**: Checks for uncommitted changes and unpushed commits.
*   **Returns**: Boolean success status.

```bash
python3 .agent/skills/pr_review/pr_skill.py safe_push
```

### `trigger_review`

Triggers new reviews from all configured bots (Gemini, CodeRabbit, Sourcery, etc.) on a specific PR.
*   **Parameters**: `pr_number` (integer)
*   **Constraints**: AUTO-RUNS `safe_push` checks before triggering. If the branch is not pushed, it FAILS and instructs you to push.
*   **Output**: Success message or error instruction.

```bash
python3 .agent/skills/pr_review/pr_skill.py trigger <PR_NUMBER>
```

### `check_status`

Checks for new feedback on a PR since a given timestamp.
*   **Parameters**:
    *   `pr_number` (integer)
    *   `since` (string, ISO 8601 timestamp, e.g., `2024-01-01T12:00:00Z`). Defaults to beginning of time if omitted.
*   **Behavior**: Stateless check. Returns JSON summary of new comments and reviews.
*   **Output**: JSON object with `items` list.

```bash
python3 .agent/skills/pr_review/pr_skill.py status <PR_NUMBER> --since <ISO_TIMESTAMP>
```

### `wait`

Blocks and waits for new feedback (comments or reviews) on a PR.
*   **Parameters**:
    *   `pr_number` (integer)
    *   `--timeout` (integer, default: 15): Timeout in minutes.
    *   `--interval` (integer, default: 60): Poll interval in seconds.
*   **Behavior**:
    *   Polls the PR every `interval` seconds.
    *   Returns status JSON immediately when a new comment or review is detected.
    *   Exits with error if timeout is reached.
*   **Usage**: Use this command to autonomously wait for bot feedback instead of exiting to the user.

```bash
python3 .agent/skills/pr_review/pr_skill.py wait <PR_NUMBER>
```

## Usage Example

1. **Push Changes**:
   ```bash
   python3 .agent/skills/pr_review/pr_skill.py safe_push
   ```

2. **Trigger Review**:
   ```bash
   python3 .agent/skills/pr_review/pr_skill.py trigger 123
   ```

3. **Check Status** (after waiting 2-3 minutes):
   ```bash
   python3 .agent/skills/pr_review/pr_skill.py status 123 --since 2024-01-01T12:00:00Z
   ```
