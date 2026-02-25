---
description: Official workflow for managing PR Review Cycles with AI bots (Gemini, CodeRabbit, Sourcery, Qodo, and Ellipsis).
---

## The Default PR Review Cycle
By default, the cycle consists of up to three phases.

### Phase 1: Offline Pre-Review Loop (Default for all new PRs)
Start with a standard "offline" loop. Work completely offline without pushing to GitHub.
1. **Prepare**: Run tests locally (`pytest`) and fix syntax errors.
2. **Trigger Offline Review**:
   ```bash
   python3 .agent/skills/pr_review/pr_skill.py trigger_review --offline
   ```
3. **Analyze & Implement**: Address the feedback and implement fixes.
4. **Loop**: Repeat this offline loop for up to **5 iterations** max (or until the local reviewer indicates the code is ready).

### Phase 2: Online Review Loop (Normal Mode)
Once the offline loop completes or reaches 5 iterations, switch to the normal online mode.
1. **Push Changes**: `git push` your changes to the remote branch. **MANDATORY**: *All test suites must pass before pushing changes.*
2. **Check Status**: `python3 .agent/skills/pr_review/pr_skill.py status {PR_NUMBER} --since {TIMESTAMP}`
3. **Trigger Reviews**: `python3 .agent/skills/pr_review/pr_skill.py trigger_review {PR_NUMBER}`
   *(This triggers GitHub bots and polls for feedback).*
4. **Analyze & Implement**: Address the feedback from GitHub bots.
5. **Loop**: Return to Step 1 (Push Changes) until "Ready to Merge".

### Phase 3: Local Mode Fallback (If Rate Limited)
If the main reviewer (e.g., `gemini-code-assist[bot]`) states that it is currently **rate limited**, switch to Local Mode for subsequent iterations:
1. **Push Changes**: `git push` your changes to the remote branch.
2. **Trigger Local Review**:
   ```bash
   python3 .agent/skills/pr_review/pr_skill.py trigger_review {PR_NUMBER} --local
   ```
   *In Local Mode, the script replaces the main reviewer with `gemini-cli-review` locally. It avoids triggering remote bots, but still waits (120 seconds) and fetches any independent human/bot comments from GitHub.*
3. **Analyze & Implement**: Address the combined local and GitHub feedback.
4. **Loop**: Return to Step 1 until "Ready to Merge".

## Compliance
> [!IMPORTANT]
> This workflow enforces the **Standards & Rules** defined in `.agent/rules/pr-standards.md`.
> *   **Push Before Trigger**: Enforced by `pr_skill.py` (except in Offline Mode).
> *   **The Loop**: Enforced by `pr_skill.py`.
> *   **Prohibitions**: Agents must **NEVER** merge, close, or delete branches.
