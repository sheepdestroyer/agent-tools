## PR Review Cycle Autonomy

### Git Push & Authentication
- **Context**: `git push` behaviors in non-interactive environments.
- **Issue**: Credential prompts can cause hanging.
- **Mitigation**: The `pr-review-skill` safely wraps push operations to detect this state.

### Timestamp Management
- **Problem**: Inconsistent timezone handling between local scripts and the GitHub API (which uses UTC) can lead to missed feedback or script crashes.
- **Solution**: Always use **UTC** for all timestamp-related operations. In Python, use `datetime.now(timezone.utc)`.

### PR Polling
- **Context**: GitHub API feedback is asynchronous.
- **Reference**: See `pr-review-skill` in `rules/pr-standards.md` for polling intervals managed by the skill. Check `.agent/rules` (local) or `~/.gemini/rules` (global).

## Project Structure & Tooling
- **Assistant Tools**: `skills/pr_review/pr_skill.py` is the robust skill for safe pushing, triggering, and checking status of PRs. Check `.agent/skills` (local) or `~/.gemini/antigravity/skills` (global).
- **Workspace**: `agent-tools/agent-workspace/` is the designated area for operational logs and temporary files. Avoid the project root.
