#!/usr/bin/env python3
"""
Robust PR Review Skill using PyGithub.
Enforces "Push Before Trigger" and "The Loop" programmatically.
Outputs JSON to stdout for easy parsing by agents.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

from github import Auth, Github, GithubException

# Constants for Review Bots
REVIEW_COMMANDS = [
    "/gemini review",
    "@coderabbitai review",
    "@sourcery-ai review",
    "/review",  # Qodo
    "@ellipsis review this",
]

# Timeout constants for subprocess calls (in seconds)
GIT_SHORT_TIMEOUT = 10
GIT_FETCH_TIMEOUT = 30
GIT_PUSH_TIMEOUT = 60
GH_AUTH_TIMEOUT = 10
GH_REPO_VIEW_TIMEOUT = 30

# Resolve binary paths (BAN-B607) - Check existence in methods or main
GIT_PATH = shutil.which("git")
GH_PATH = shutil.which("gh")

# Polling constants for review feedback
# Configurable via environment variables
try:
    POLL_INTERVAL_SECONDS = max(
        int(os.environ.get("PR_REVIEW_POLL_INTERVAL", "30")), 1)
except ValueError:
    POLL_INTERVAL_SECONDS = 120

try:
    POLL_MAX_ATTEMPTS = max(
        int(os.environ.get("PR_REVIEW_POLL_MAX_ATTEMPTS", "12")), 1)
except ValueError:
    POLL_MAX_ATTEMPTS = 20

# Default Validation Reviewer (The bot/user that must approve)
DEFAULT_VALIDATION_REVIEWER = os.environ.get(
    "PR_REVIEW_VALIDATION_REVIEWER", "gemini-code-assist[bot]"
)

# Loop state file for crash recovery
LOOP_STATE_FILENAME = "loop_state.json"

# Common instructional strings for next_step
ACTION_INSTRUCTIONS = (
    "ANALYZE feedback -> FIX code -> SAFE_PUSH. DO NOT STOP. "
    "Pull with rebase to get the latest changes from the remote branch before starting to address code reviews, "
    "as bots may have since pushed formatting fixes to your previous changes. "
    "Be sure to address every comment and code review from all reviewers, ensure CI passes. "
    "Run and fix all available tests and Linting before pushing your next changes."
)

RATE_LIMIT_INSTRUCTION = " If main reviewer says it just became rate-limited, address remaining code reviews then stop there."


def print_json(data):
    """Helper to print JSON to stdout."""
    print(json.dumps(data, indent=2))


def print_error(message, code=1):
    """Helper to print error JSON to stdout and exit."""
    print_json({"status": "error", "message": message, "code": code})
    sys.exit(code)


class ReviewManager:
    def __init__(self):
        # Authenticate with GitHub
        self.token = os.environ.get(
            "GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self.token:
            # Fallback to gh CLI for auth token if env var is missing
            if not GH_PATH:
                print_error(
                    "No GITHUB_TOKEN found and 'gh' command not found in PATH.")
            try:
                res = self._run_gh_cmd(["auth", "token"])
                self.token = res.stdout.strip()
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                FileNotFoundError,
            ) as e:
                print_error(
                    f"No GITHUB_TOKEN found and 'gh' command failed: {e}")

        try:
            self.g = Github(auth=Auth.Token(self.token))
            self.repo = self._detect_repo()
            self._ensure_workspace()
        except (GithubException, OSError, ValueError) as e:
            # Mask token if present in error
            safe_msg = str(e)
            if self.token:
                safe_msg = safe_msg.replace(self.token, "********")
            print_error(f"Initialization failed: {safe_msg}")

    def _mask_token(self, text):
        """Redacts the GitHub token from the given text."""
        if not self.token or not text:
            return text
        return text.replace(self.token, "********")

    @staticmethod
    def _log(message):
        """Audit logging to stderr with timestamp."""
        timestamp = datetime.now(timezone.utc).isoformat()
        # Tag logs as [AUDIT] for compliance and easier filtering
        print(f"[{timestamp}] [AUDIT] {message}", file=sys.stderr)

    @staticmethod
    def _run_git_cmd(args, timeout=GIT_SHORT_TIMEOUT, check=True):
        """Helper to run git commands securely."""
        if not GIT_PATH:
            raise FileNotFoundError("git command not found")
        return subprocess.run(
            [GIT_PATH] + args,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )

    @staticmethod
    def _run_gh_cmd(args, timeout=GH_AUTH_TIMEOUT):
        """Helper to run gh commands securely."""
        if not GH_PATH:
            raise FileNotFoundError("gh command not found")
        return subprocess.run(
            [GH_PATH] + args,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )

    def _ensure_workspace(self):
        """Creates agent-workspace directory relative to repo root if possible."""
        try:
            # Try to find repo root
            root = self._run_git_cmd(
                ["rev-parse", "--show-toplevel"]).stdout.strip()
            if os.path.basename(root) == "agent-tools":
                self.workspace = os.path.join(root, "agent-workspace")
            else:
                self.workspace = os.path.join(
                    root, "agent-tools", "agent-workspace")
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to current directory logic
            self.workspace = os.path.join(os.getcwd(), "agent-workspace")

        os.makedirs(self.workspace, exist_ok=True)
        self.loop_state_file = os.path.join(
            self.workspace, LOOP_STATE_FILENAME)

    def _detect_repo(self):
        """Auto-detects current repository from git remote (local check preferred)."""
        # 1. Try local git remote first (fast, no network)
        try:
            # Get origin URL
            res = self._run_git_cmd(["config", "--get", "remote.origin.url"])
            url = res.stdout.strip()

            # Extract owner/repo using regex
            # Matches: https://github.com/owner/repo.git, git@github.com:owner/repo.git, etc.
            match = re.search(
                r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
            if match:
                full_name = f"{match.group(1)}/{match.group(2)}"
                return self.g.get_repo(full_name)
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
        ):
            # Ignore local errors and fall back to gh
            self._log("Local git remote check failed, falling back to 'gh'...")
            pass

        # 2. Fallback to gh CLI (slower, network dependent)
        try:
            res = self._run_gh_cmd(
                ["repo", "view", "--json", "owner,name"], timeout=GH_REPO_VIEW_TIMEOUT
            )
            data = json.loads(res.stdout)
            full_name = f"{data['owner']['login']}/{data['name']}"
            return self.g.get_repo(full_name)
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            ValueError,
        ):
            raise RuntimeError(
                "Error checking repository context: Ensure 'gh' is installed and you are in a git repository."
            ) from None

    def _save_loop_state(self, pr_number, since_iso, validation_reviewer, poll_attempt):
        """Save loop state to file for crash recovery."""
        state = {
            "pr_number": pr_number,
            "loop_started_at": datetime.now(timezone.utc).isoformat(),
            "last_poll_at": datetime.now(timezone.utc).isoformat(),
            "poll_attempt": poll_attempt,
            "validation_reviewer": validation_reviewer,
            "since_iso": since_iso,
            "last_status": "polling",
        }
        try:
            # Use os.open with O_CREAT and O_TRUNC to prevent following symlinks if file exists
            # This mitigates CWE-59 symlink attacks in shared directories
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self.loop_state_file, flags, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(state, f, indent=2)
        except OSError as e:
            self._log(f"Warning: Could not save loop state: {e}")

    def _load_loop_state(self):
        """Load loop state from file if it exists. Returns None if no state."""
        if not os.path.exists(self.loop_state_file):
            return None
        try:
            with open(self.loop_state_file, "r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._log(f"Warning: Could not load loop state: {e}")
            return None

    def _clear_loop_state(self):
        """Remove loop state file after successful completion."""
        try:
            if os.path.exists(self.loop_state_file):
                os.remove(self.loop_state_file)
                self._log("Loop state cleared.")
        except OSError as e:
            self._log(f"Warning: Could not clear loop state: {e}")

    def _interruptible_sleep(self, total_seconds, heartbeat_interval=10):
        """Sleep in small chunks with periodic heartbeat output."""
        elapsed = 0
        chunk = 5
        while elapsed < total_seconds:
            slept = min(chunk, total_seconds - elapsed)
            time.sleep(slept)
            elapsed += slept
            if elapsed % heartbeat_interval == 0 and elapsed < total_seconds:
                self._log(f"  ...waiting ({elapsed}s/{total_seconds}s)")

    def _verify_clean_git(self):
        """
        Helper to check that the working directory is clean and we are on a valid branch.
        Returns: (is_valid, branch_name_or_error_msg)
        """
        try:
            # 1. Check for uncommitted changes
            status_proc = self._run_git_cmd(["status", "--porcelain"])
            if status_proc.stdout.strip():
                return (
                    False,
                    "Uncommitted changes detected. Please commit or stash them first.",
                )

            # 2. Get current branch
            branch_proc = self._run_git_cmd(
                ["rev-parse", "--abbrev-ref", "HEAD"])
            branch = branch_proc.stdout.strip()
            if branch == "HEAD":
                return False, "Detached HEAD state detected. Please checkout a branch."

            return True, branch
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            return False, f"Git check failed: {self._mask_token(str(e))}"
        except subprocess.TimeoutExpired:
            return False, "Git check timed out."

    def _check_local_state(self):
        """
        Verifies:
        1. Clean git status (implemented in helper).
        2. Pushed to remote (upstream sync).
        """
        # 1. Check local cleanliness
        is_clean, branch_or_msg = self._verify_clean_git()
        if not is_clean:
            return False, branch_or_msg

        branch = branch_or_msg

        # 2. Check if pushed to upstream
        try:
            # Fetch latest state from remote for accurate comparison
            # Suppress stdout to avoid polluting structured output; inherit stderr so prompts/hangs remain visible
            # Fetch latest state from remote for accurate comparison
            # Suppress stdout to avoid polluting structured output; inherit stderr so prompts/hangs remain visible
            self._run_git_cmd(["fetch"], timeout=GIT_FETCH_TIMEOUT)

            # Get current branch
            branch = self._run_git_cmd(
                ["rev-parse", "--abbrev-ref", "HEAD"]
            ).stdout.strip()

            # Check if upstream is configured
            upstream_proc = self._run_git_cmd(
                ["rev-parse", "--abbrev-ref", "@{u}"], check=False
            )
            if upstream_proc.returncode != 0:
                return (
                    False,
                    f"No upstream configured for branch '{branch}'. Please 'git push -u origin {branch}' first.",
                )

            # Check for unpushed commits and upstream changes
            # git rev-list --left-right --count @{u}...HEAD
            # Output: "behind  ahead" (left=@{u}, right=HEAD)
            rev_list = self._run_git_cmd(
                ["rev-list", "--left-right", "--count", "@{u}...HEAD"], check=False
            )
            if rev_list.returncode == 0:
                try:
                    parts = rev_list.stdout.split()
                    if len(parts) == 2:
                        behind, ahead = int(parts[0]), int(parts[1])
                        if ahead > 0:
                            return (
                                False,
                                f"Local branch '{branch}' has {ahead} unpushed commit(s). You MUST push before triggering a review.",
                            )
                        if behind > 0:
                            return (
                                False,
                                f"Local branch '{branch}' is behind upstream by {behind} commit(s). Please pull.",
                            )
                    else:
                        return (
                            False,
                            f"Unexpected git rev-list output: '{rev_list.stdout.strip()}'",
                        )
                except ValueError:
                    return (
                        False,
                        f"Failed to parse git rev-list output: '{rev_list.stdout.strip()}'",
                    )
            else:
                # Fallback/General error
                return False, f"Failed to check divergence: {rev_list.stderr.strip()}"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return False, f"Git check failed: {self._mask_token(str(e))}"

        return True, "Code is clean and pushed."

    def safe_push(self):
        """Attempts to push changes safely, aborting if uncommitted changes exist. Ignores unpushed commits check."""
        self._log("Running safe_push verification...")

        # 1. Check local cleanliness using helper
        is_clean, branch_or_msg = self._verify_clean_git()
        if not is_clean:
            # Map helper error to JSON format
            return {
                "status": "error",
                "message": branch_or_msg,
                "next_step": "Ensure git is clean and valid.",
            }

        branch = branch_or_msg

        # Separately check upstream
        try:
            upstream_proc = self._run_git_cmd(
                ["rev-parse", "--abbrev-ref", "@{u}"], check=False
            )
            if upstream_proc.returncode != 0:
                return {
                    "status": "error",
                    "message": f"No upstream configured for branch '{branch}'. Please 'git push -u origin {branch}' first.",
                    "next_step": "Configure upstream and retry safe_push.",
                }
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Git upstream check timed out."}
        except (subprocess.CalledProcessError, FileNotFoundError):
            # This can happen if 'git rev-parse --abbrev-ref @{u}' fails for other reasons
            return {
                "status": "error",
                "message": f"Failed to determine upstream for branch '{branch}'. Please ensure it's configured.",
                "next_step": "Check git configuration and retry safe_push.",
            }

        # Attempt push
        try:
            self._run_git_cmd(["push"], timeout=GIT_PUSH_TIMEOUT)
            return {
                "status": "success",
                "message": "Push successful.",
                "next_step": "Run 'trigger_review' to start the review cycle.",
            }
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
        ) as e:
            # Mask token if present in error
            safe_err = self._mask_token(str(e))
            return {
                "status": "error",
                "message": f"Push failed or timed out: {safe_err}. You may need to pull changes first or check your connection.",
                "next_step": "Pull changes, resolve conflicts, and retry safe_push.",
            }

    def _poll_for_main_reviewer(
        self,
        pr_number,
        since_iso,
        validation_reviewer,
        max_attempts=None,
        poll_interval=None,
        start_attempt=1,
    ):
        """
        Polls until the main reviewer has provided feedback since the given timestamp.
        Enforces the Loop Rule: never return until main reviewer responds or timeout.

        Returns the status data from check_status once main reviewer feedback is detected.
        """
        max_attempts = POLL_MAX_ATTEMPTS if max_attempts is None else max_attempts
        poll_interval = (
            POLL_INTERVAL_SECONDS if poll_interval is None else poll_interval
        )

        # Initialize status_data to handle edge case where max_attempts is 0 or loop is interrupted
        status_data = None

        for i in range(max_attempts):
            attempt = start_attempt + i
            try:
                # Save state for crash recovery
                self._save_loop_state(
                    pr_number, since_iso, validation_reviewer, attempt
                )
                self._log(
                    f"Poll attempt {attempt} (resume+{i + 1}) / max {POLL_MAX_ATTEMPTS}..."
                )

                # Get current status
                status_data = self.check_status(
                    pr_number,
                    since_iso=since_iso,
                    return_data=True,
                    validation_reviewer=validation_reviewer,
                )

                # Check for any NEW feedback from main reviewer in items (filtered by since_iso)
                # IMPORTANT: Do NOT check main_reviewer_state here - that reflects ALL historical reviews
                # and would cause immediate exit if main reviewer ever commented before.
                # We only want to exit when the main reviewer has posted NEW feedback since trigger.
                main_reviewer_has_new_feedback = any(
                    item.get("user") == validation_reviewer
                    for item in status_data.get("items", [])
                )

                if main_reviewer_has_new_feedback:
                    main_reviewer_info = status_data.get("main_reviewer", {})
                    main_reviewer_state = main_reviewer_info.get(
                        "state", "PENDING")
                    self._log(
                        f"Main reviewer ({validation_reviewer}) has NEW feedback with state: {main_reviewer_state}"
                    )
                    # Clear state on successful completion
                    self._clear_loop_state()
                    return status_data

                # Not yet - wait and poll again with interruptible sleep
                if attempt < max_attempts:
                    self._log(
                        f"Main reviewer has not responded yet. Waiting {poll_interval}s before next poll..."
                    )
                    self._interruptible_sleep(poll_interval)
            except KeyboardInterrupt:
                self._log("\nPolling interrupted by user.")
                # Return distinct status for interruption vs timeout
                if status_data is None:
                    status_data = {
                        "status": "interrupted",
                        "message": "Polling interrupted before first check.",
                    }
                status_data["polling_interrupted"] = True
                status_data["next_step"] = (
                    "INTERRUPTED: Polling cancelled by user. Run 'resume' to continue from last checkpoint."
                )
                return status_data

        # Timeout - return status with warning
        self._log(
            f"WARNING: Main reviewer did not respond within {max_attempts * poll_interval}s timeout."
        )

        # Handle case where no polls were made (e.g., max_attempts was 0)
        if status_data is None:
            status_data = {
                "status": "error",
                "message": "Polling failed - no status data available.",
            }

        status_data["polling_timeout"] = True
        status_data["next_step"] = (
            f"TIMEOUT: {validation_reviewer} did not respond. Poll again with 'status' or investigate bot issues."
        )
        return status_data

    def resume_loop(self):
        """
        Resume polling from last checkpoint after a crash/interruption.
        Loads state from loop_state.json and continues where it left off.
        """
        state = self._load_loop_state()
        if state is None:
            return {
                "status": "no_state",
                "message": "No loop state found. Nothing to resume.",
                "next_step": "Run 'trigger_review' to start a new review cycle.",
            }

        pr_number = state.get("pr_number")
        since_iso = state.get("since_iso")
        validation_reviewer = state.get(
            "validation_reviewer", DEFAULT_VALIDATION_REVIEWER
        )
        last_attempt = state.get("poll_attempt", 1)

        self._log(
            f"Resuming loop for PR #{pr_number} from attempt {last_attempt}...")
        self._log(f"  since_iso: {since_iso}")
        self._log(f"  validation_reviewer: {validation_reviewer}")

        # Calculate remaining attempts (fix off-by-one: total should not exceed POLL_MAX_ATTEMPTS)
        remaining_attempts = max(0, POLL_MAX_ATTEMPTS - last_attempt)

        if remaining_attempts == 0:
            return {
                "status": "resumed",
                "message": f"No remaining attempts for PR #{pr_number}.",
                "pr_number": pr_number,
                "resumed_from_attempt": last_attempt,
                "initial_status": None,
                "next_step": "Increase POLL_MAX_ATTEMPTS or start a new cycle with 'trigger_review'.",
            }

        # Resume polling
        status_data = self._poll_for_main_reviewer(
            pr_number=pr_number,
            since_iso=since_iso,
            validation_reviewer=validation_reviewer,
            max_attempts=remaining_attempts,
            start_attempt=last_attempt + 1,
        )

        return {
            "status": "resumed",
            "message": f"Resumed loop for PR #{pr_number} from attempt {last_attempt}.",
            "pr_number": pr_number,
            "resumed_from_attempt": last_attempt,
            "initial_status": status_data,
            "next_step": status_data.get("next_step", "Check status and continue."),
        }

    def trigger_review(
        self,
        pr_number,
        wait_seconds=180,
        validation_reviewer=DEFAULT_VALIDATION_REVIEWER,
    ):
        """
        1. Checks local state (Hard Constraint).
        2. Post comments to trigger bots.
        3. Polls for main reviewer feedback.
        """
        # Step 1: Enforce Push
        is_safe, msg = self._check_local_state()
        if not is_safe:
            print_error(
                f"FAILED: {msg}\nTip: Use the 'safe_push' tool or run 'git push' manually."
            )

        # Step 2: Trigger Bots
        triggered_bots = []
        try:
            pr = self.repo.get_pull(pr_number)
            self._log(f"Triggering reviews on PR #{pr_number} ({pr.title})...")

            for cmd in REVIEW_COMMANDS:
                pr.create_issue_comment(cmd)
                self._log(f"  Posted: {cmd}")
                triggered_bots.append(cmd)

            self._log("All review bots triggered successfully.")

            # Step 3: Return Instructions (Non-Blocking)
            # We no longer poll inside the script to avoid timeouts and blocking the agent.
            # The agent must handle the wait and restart the cycle.
            self._log(
                f"All reviews triggered. Expecting feedback from {validation_reviewer}..."
            )

            return {
                "status": "triggered",
                "message": "Reviews triggered successfully. Bot is now waiting for feedback.",
                "triggered_bots": triggered_bots,
                "initial_status": None,
                "next_step": f"WAIT {wait_seconds} seconds (for bots to run), then run 'status' to poll for {validation_reviewer}'s review.",
            }

        except GithubException as e:
            print_error(f"GitHub API Error: {self._mask_token(str(e))}")

    @staticmethod
    def _get_aware_utc_datetime(dt_obj):
        """Helper: Converts a naive datetime from PyGithub into a timezone-aware one."""
        if dt_obj is None:
            return None
        if dt_obj.tzinfo is None:
            return dt_obj.replace(tzinfo=timezone.utc)
        return dt_obj.astimezone(timezone.utc)

    def _get_since_dt(self, since_iso):
        """Helper to parse since_iso into a timezone-aware datetime."""
        since_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
        if since_iso:
            try:
                # Handle Z suffix manually for older Python versions
                if since_iso.endswith("Z"):
                    since_iso = since_iso[:-1] + "+00:00"
                parsed_dt = datetime.fromisoformat(since_iso)
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                since_dt = parsed_dt
            except ValueError:
                self._log(f"Warning: Invalid timestamp {since_iso}, ignoring.")
        return since_dt

    @staticmethod
    def _analyze_main_reviewer(reviews, validation_reviewer):
        """
        Analyze reviews to determine main reviewer state and last approval time.
        Returns: (main_reviewer_state, main_reviewer_last_approval_dt)
        """
        main_reviewer_state = "PENDING"
        main_reviewer_last_approval_dt = None
        found_latest_state = False

        # Single pass: find latest state AND most recent approval timestamp
        for review in reversed(reviews):
            if review.user.login == validation_reviewer:
                if not found_latest_state:
                    main_reviewer_state = review.state
                    found_latest_state = True

                if (
                    main_reviewer_last_approval_dt is None
                    and review.state == "APPROVED"
                ):
                    main_reviewer_last_approval_dt = (
                        ReviewManager._get_aware_utc_datetime(
                            review.submitted_at)
                    )

                # Exit early if both are found
                if found_latest_state and main_reviewer_last_approval_dt is not None:
                    break

        return main_reviewer_state, main_reviewer_last_approval_dt

    def _check_new_main_reviewer_comments(
        self, new_feedback, validation_reviewer, last_approval_dt
    ):
        """Check if there are new comments from the main reviewer AFTER their last approval."""
        if not last_approval_dt:
            return False

        for item in new_feedback:
            is_main_reviewer = item.get("user") == validation_reviewer
            is_comment = item.get("type") in [
                "issue_comment", "inline_comment"]
            is_review_comment = (
                item.get("type") == "review_summary"
                and item.get("state") == "COMMENTED"
            )

            if is_main_reviewer and (is_comment or is_review_comment):
                created_at_val = item.get("created_at")
                if created_at_val:
                    try:
                        # Handle Z suffix manually
                        if created_at_val.endswith("Z"):
                            created_at_val = created_at_val[:-1] + "+00:00"
                        dt_val = datetime.fromisoformat(created_at_val)
                        comment_dt = ReviewManager._get_aware_utc_datetime(
                            dt_val)

                        if comment_dt >= last_approval_dt:
                            return True
                    except (ValueError, TypeError) as e:
                        self._log(
                            f"Warning: Could not parse date '{created_at_val}'. Error: {e!s}. Skipping item."
                        )
                        continue
        return False

    @staticmethod
    def _determine_next_step(
        new_feedback,
        validation_reviewer,
        main_reviewer_state,
        has_changes_requested,
        has_new_main_comments,
    ):
        """Generate the next_step instruction string."""
        if has_changes_requested:
            return f"CRITICAL: Changes requested by reviewer. {ACTION_INSTRUCTIONS}{RATE_LIMIT_INSTRUCTION}"
        elif has_new_main_comments:
            return f"New comments from {validation_reviewer} after approval. {ACTION_INSTRUCTIONS}{RATE_LIMIT_INSTRUCTION}"
        elif main_reviewer_state == "APPROVED":
            other_feedback = [
                item
                for item in new_feedback
                if not (
                    item.get("user") == validation_reviewer
                    and item.get("type") == "review_summary"
                    and item.get("state") == "APPROVED"
                )
            ]
            if other_feedback:
                return f"New feedback received. {ACTION_INSTRUCTIONS}"
            else:
                return "Validation Complete (STOP LOOP - DO NOT MERGE AUTONOMOUSLY). Notify User. Never merge or delete a branch on your own, if you believe the main reviewer said that the PR is ready, just stop and ask for Human review"
        elif new_feedback:
            # Count explicitly addressable comments
            comment_count = sum(
                1
                for item in new_feedback
                if item.get("type") in ["inline_comment", "issue_comment"]
            )
            if comment_count > 0:
                return f"{comment_count} new comments received. {ACTION_INSTRUCTIONS}"
            else:
                return f"New feedback received. {ACTION_INSTRUCTIONS}"
        else:
            return f"Waiting for approval from {validation_reviewer} (Current: {main_reviewer_state}). WAIT 30 seconds, then run 'status' to check again. Be autonomous, don't stop the cycle. You must regularly poll for the main-reviewer's review until it eventually gets posted, then continue."

    def _fetch_issue_comments(self, pr_number, since_dt):
        """Fetch and format issue comments."""
        issue = self.repo.get_issue(pr_number)
        comments = []
        for comment in issue.get_comments(since=since_dt):
            comments.append(
                {
                    "type": "issue_comment",
                    "user": comment.user.login,
                    "body": comment.body,
                    "url": comment.html_url,
                    "updated_at": (
                        ReviewManager._get_aware_utc_datetime(
                            comment.updated_at
                        ).isoformat()
                        if comment.updated_at
                        else None
                    ),
                    "created_at": ReviewManager._get_aware_utc_datetime(
                        comment.created_at
                    ).isoformat(),
                }
            )
        return comments

    @staticmethod
    def _fetch_review_comments(pr, since_dt):
        """Fetch and format review (inline) comments."""
        comments = []
        for comment in pr.get_review_comments():
            comment_dt = ReviewManager._get_aware_utc_datetime(
                comment.updated_at)
            if comment_dt and comment_dt >= since_dt:
                comments.append(
                    {
                        "type": "inline_comment",
                        "user": comment.user.login,
                        "body": comment.body,
                        "path": comment.path,
                        "line": comment.line,
                        "created_at": (
                            ReviewManager._get_aware_utc_datetime(
                                comment.created_at
                            ).isoformat()
                            if comment.created_at
                            else None
                        ),
                        "updated_at": comment_dt.isoformat(),
                        "url": comment.html_url,
                    }
                )
        return comments

    @staticmethod
    def _fetch_reviews(pr, since_dt):
        """Fetch and format review summaries."""
        reviews_data = []
        all_reviews_objects = list(pr.get_reviews())
        for review in all_reviews_objects:
            if review.submitted_at:
                review_dt = ReviewManager._get_aware_utc_datetime(
                    review.submitted_at)
                if review_dt and review_dt >= since_dt:
                    reviews_data.append(
                        {
                            "type": "review_summary",
                            "user": review.user.login,
                            "state": review.state,
                            "body": review.body,
                            "created_at": review_dt.isoformat(),
                        }
                    )
        return reviews_data, all_reviews_objects

    def check_status(
        self,
        pr_number,
        since_iso=None,
        return_data=False,
        validation_reviewer=DEFAULT_VALIDATION_REVIEWER,
    ):
        """
        Checks the status of the PR.
        If since_iso is provided, filters for events strictly AFTER that time.
        """
        try:
            pr = self.repo.get_pull(pr_number)
            since_dt = self._get_since_dt(since_iso)

            # 1. Fetch all feedback types
            issue_comments = self._fetch_issue_comments(pr_number, since_dt)
            review_comments = self._fetch_review_comments(pr, since_dt)
            reviews, all_reviews_objects = self._fetch_reviews(pr, since_dt)

            # Combine new feedback
            new_feedback = issue_comments + review_comments + reviews

            # Analysis
            main_state, last_approval = self._analyze_main_reviewer(
                all_reviews_objects, validation_reviewer
            )
            has_changes_requested = any(
                item.get("state") == "CHANGES_REQUESTED"
                and item.get("type") == "review_summary"
                for item in new_feedback
            )
            has_new_main_comments = self._check_new_main_reviewer_comments(
                new_feedback, validation_reviewer, last_approval
            )

            next_step = self._determine_next_step(
                new_feedback,
                validation_reviewer,
                main_state,
                has_changes_requested,
                has_new_main_comments,
            )

            output = {
                "status": "success",
                "pr_number": pr_number,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "new_item_count": len(new_feedback),
                "items": new_feedback,
                "main_reviewer": {"user": validation_reviewer, "state": main_state},
                "next_step": next_step,
            }

            if return_data:
                return output
            print_json(output)
            return output

        except GithubException as e:
            if return_data:
                raise
            print_error(f"GitHub API Error: {self._mask_token(str(e))}")


def main():
    parser = argparse.ArgumentParser(description="PR Skill Agent Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Trigger Review
    p_trigger = subparsers.add_parser(
        "trigger_review", help="Trigger reviews safely")
    p_trigger.add_argument("pr_number", type=int)
    p_trigger.add_argument(
        "--wait",
        type=int,
        default=180,
        help="Seconds to wait for initial feedback (default: 180)",
    )
    p_trigger.add_argument(
        "--validation-reviewer",
        default=DEFAULT_VALIDATION_REVIEWER,
        help="Username of the main reviewer that must approve",
    )

    # Status
    p_status = subparsers.add_parser("status", help="Check review status")
    p_status.add_argument("pr_number", type=int)
    p_status.add_argument("--since", help="ISO 8601 timestamp")
    p_status.add_argument(
        "--validation-reviewer",
        default=DEFAULT_VALIDATION_REVIEWER,
        help="Username of the main reviewer that must approve",
    )

    # Safe Push
    subparsers.add_parser("safe_push", help="Push changes safely")

    # Resume (crash recovery)
    subparsers.add_parser("resume", help="Resume polling from last checkpoint")

    args = parser.parse_args()

    try:
        mgr = ReviewManager()

        if args.command == "trigger_review":
            result = mgr.trigger_review(
                args.pr_number,
                wait_seconds=args.wait,
                validation_reviewer=args.validation_reviewer,
            )
            print_json(result)
        elif args.command == "status":
            mgr.check_status(
                args.pr_number, args.since, validation_reviewer=args.validation_reviewer
            )
        elif args.command == "safe_push":
            result = mgr.safe_push()
            print_json(result)
            if result["status"] != "success":
                sys.exit(1)
        elif args.command == "resume":
            result = mgr.resume_loop()
            print_json(result)
    except Exception as e:
        # Catch-all for unhandled exceptions to prevent raw tracebacks in JSON output
        # Log full traceback to stderr for debugging
        sys.stderr.write(f"CRITICAL ERROR: {e!s}\n")
        import traceback

        traceback.print_exc(file=sys.stderr)

        # Output clean JSON error
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "An internal error occurred. See stderr for details.",
                    "error_type": type(e).__name__,
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
