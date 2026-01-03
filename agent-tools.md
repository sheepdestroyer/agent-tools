# Agent Tools: PR Review Helper

The `pr_helper.py` script is a unified command-line interface designed to streamline the PR review cycle for AI agents. It consolidates fetching feedback, monitoring for new reviews, triggering bot services, and verifying local fixes.

## Usage

```bash
python3 agent-tools/pr_helper.py [command] [args]
```

### Commands

#### 1. `trigger`
Posts the required comments to trigger AI reviews from Gemini and CodeRabbit.
```bash
python3 agent-tools/pr_helper.py trigger {PR_NUMBER}
```

#### 2. `monitor`
Polls GitHub for any new feedback (reviews, inline comments, or issue comments) created after a specific timestamp. Exits with code 0 when new feedback is found.
```bash
python3 agent-tools/pr_helper.py monitor {PR_NUMBER} --since {ISO_TIMESTAMP} --output {RESULT_FILE}
```
**Options:**
- `--since`: ISO 8601 timestamp (e.g., `2026-01-02T18:00:00Z`).
- `--timeout`: Maximum wait time in seconds (default: 1200).
- `--initial-wait`: Delay before the first check (default: 180).
- `--interval`: Delay between subsequent checks (default: 120).
- `--output`: File path to save the JSON feedback.

> [!WARNING]
> **Timestamp Precision**: Always use **UTC** timestamps (ending in `Z`) for the `--since` argument. GitHub API timestamps are UTC-aware. Providing a local time without conversion will result in missing feedback (e.g., if you are UTC+1, 21:00 local is 20:00 UTC). Use `datetime.now(timezone.utc).isoformat()`.

#### 3. `fetch`
Performs a one-shot retrieval of all new feedback since a timestamp.
```bash
python3 agent-tools/pr_helper.py fetch {PR_NUMBER} --since {ISO_TIMESTAMP}
```

#### 4. `verify`
Runs heuristic checks on local files against the feedback retrieved by `fetch` or `monitor`.
```bash
python3 agent-tools/pr_helper.py verify {RESULT_FILE}
```

## Workflow Integration

This tool is designed to support **The Loop Rule** documented in `AGENTS.md`. 
1. `push` changes.
2. `trigger` reviews.
3. `monitor` for feedback.
4. `verify` and fix.
5. Repeat.

> [!IMPORTANT]
> Always use `agent-tools/agent-workspace/` for temporary JSON and log files to keep the repository root clean.
