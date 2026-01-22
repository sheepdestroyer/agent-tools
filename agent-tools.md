# Agent Tools: PR Review Skill

The `pr_skill.py` script is a robust Agent Skill designed to streamline the PR review cycle. It uses strict programmatic checks ("The Loop") and `PyGithub` to ensure reliability.

## Usage

```bash
python3 .agent/skills/pr_review/pr_skill.py [command] [args]
```

### Tools

#### 1. `safe_push`
Safely pushes local changes to the remote repository, checking for uncommitted changes first.
```bash
python3 .agent/skills/pr_review/pr_skill.py safe_push
```

#### 2. `trigger_review`
Posts the required comments to trigger AI reviews from Gemini, CodeRabbit, Sourcery, Qodo, and Ellipsis.
**Constraint**: Automatically runs `safe_push` checks. Auto-fails if local branch is not pushed.
```bash
python3 .agent/skills/pr_review/pr_skill.py trigger_review {PR_NUMBER}
```

#### 3. `status`
Stateless check for review feedback using PyGithub. Returns JSON summary immediately (no hanging/polling).
```bash
python3 .agent/skills/pr_review/pr_skill.py status {PR_NUMBER} --since {ISO_TIMESTAMP}
```

## Workflow Integration

This tool is designed to support **The Loop Rule** documented in `AGENTS.md`. 
1. **Test**: Run all test suites.
2. `safe_push` changes (or use `trigger_review` which checks this).
3. `trigger_review` reviews.
4. `status` check after wait.
5. Fix issues.
6. Repeat.
