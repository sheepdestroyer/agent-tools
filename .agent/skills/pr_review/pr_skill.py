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

# Polling constants for review feedback
# Configurable via environment variables
try:
    POLL_INTERVAL_SECONDS = max(
        int(os.environ.get("PR_REVIEW_POLL_INTERVAL", "30")), 1)
except ValueError:
    POLL_INTERVAL_SECONDS = 30

try:
    POLL_MAX_ATTEMPTS = max(
        int(os.environ.get("PR_REVIEW_POLL_MAX_ATTEMPTS", "20")), 1)
except ValueError:
    POLL_MAX_ATTEMPTS = 20

# Default Validation Reviewer (The bot/user that must approve)
DEFAULT_VALIDATION_REVIEWER = os.environ.get("PR_REVIEW_VALIDATION_REVIEWER",
                                             "gemini-code-assist[bot]")

# Common instructional strings for next_step
ACTION_INSTRUCTIONS = (
    "ANALYZE feedback -> FIX code -> DO NOT PUSH YET. "
    "Pull and merge latest changes from the remote branch before starting to address code reviews. "
    "1. Be sure to fetch, check, and address every comment and code review from all reviewers. "
    "2. Then, fetch and address any non-passing CI checks (e.g., using GitHub MCP tools like `pull_request_read` with method `get_status`, falling back to `gh` CLI if unavailable). "
    "3. Run and fix all available tests and Linting locally. "
    "4. JUST BEFORE pushing your next changes, run a max of 2 iterations of offline review (`--offline`) to catch any remaining issues locally. "
    "5. Finally, use SAFE_PUSH and trigger the next normal/local loop.")

RATE_LIMIT_INSTRUCTION = " If main reviewer says it just became rate-limited, address remaining code reviews then stop there."


def print_json(data):
    """Helper to print JSON to stdout."""
    print(json.dumps(data, indent=2))


def print_error(message, code=1):
    """Helper to print error JSON to stdout and exit."""
    print_json({"status": "error", "message": message, "code": code})
    sys.exit(code)


class ReviewManager:

    def __init__(self, local=False, offline=False):
        self.local = local
        self.offline = offline
        # Authenticate with GitHub
        self.token = os.environ.get("GITHUB_TOKEN") or os.environ.get(
            "GH_TOKEN")
        if not self.token and not self.offline:
            # Fallback to gh CLI for auth token if env var is missing
            try:
                res = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=GH_AUTH_TIMEOUT,
                )
                self.token = res.stdout.strip()
            except (
                    subprocess.CalledProcessError,
                    FileNotFoundError,
                    subprocess.TimeoutExpired,
            ) as e:
                print_error(
                    f"No GITHUB_TOKEN found and 'gh' command failed: {e}")

        try:
            if not self.offline:
                self.g = Github(auth=Auth.Token(self.token))
                self.repo = self._detect_repo()
            else:
                self.repo = None
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

    def _log(self, message):
        """Audit logging to stderr with timestamp."""
        timestamp = datetime.now(timezone.utc).isoformat()
        # Tag logs as [AUDIT] for compliance and easier filtering
        print(f"[{timestamp}] [AUDIT] {message}", file=sys.stderr)

    def _ensure_workspace(self):
        """Creates agent-workspace directory relative to repo root if possible."""
        try:
            # Try to find repo root
            root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if os.path.basename(root) == "agent-tools":
                self.workspace = os.path.join(root, "agent-workspace")
            else:
                self.workspace = os.path.join(root, "agent-tools",
                                              "agent-workspace")
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to current directory logic
            self.workspace = os.path.join(os.getcwd(), "agent-workspace")

        os.makedirs(self.workspace, exist_ok=True)

    def _detect_repo(self):
        """Auto-detects current repository from git remote (local check preferred)."""
        # 1. Try local git remote first (fast, no network)
        try:
            # Get origin URL
            res = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True,
                text=True,
                check=True,
                timeout=GIT_SHORT_TIMEOUT,
            )
            url = res.stdout.strip()

            # Extract owner/repo using regex
            # Matches: https://github.com/owner/repo.git, git@github.com:owner/repo.git, etc.
            match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$",
                              url)
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

        # 2. Fallback to gh CLI (slower, network dependent)
        try:
            res = subprocess.run(
                ["gh", "repo", "view", "--json", "owner,name"],
                capture_output=True,
                text=True,
                check=True,
                timeout=GH_REPO_VIEW_TIMEOUT,
            )
            data = json.loads(res.stdout)
            full_name = f"{data['owner']['login']}/{data['name']}"
            return self.g.get_repo(full_name)
        except (
                subprocess.CalledProcessError,
                FileNotFoundError,
                json.JSONDecodeError,
                ValueError,
        ):
            raise RuntimeError(
                "Error checking repository context: Ensure 'gh' is installed and you are in a git repository."
            ) from None

    def _verify_clean_git(self):
        """
        Helper to check that the working directory is clean and we are on a valid branch.
        Returns: (is_valid, branch_name_or_error_msg)
        """
        try:
            # 1. Check for uncommitted changes
            status_proc = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
                timeout=GIT_SHORT_TIMEOUT,
            )
            if status_proc.stdout.strip():
                return (
                    False,
                    "Uncommitted changes detected. Please commit or stash them first.",
                )

            # 2. Get current branch
            branch_proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=GIT_SHORT_TIMEOUT,
            )
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
            subprocess.run(
                ["git", "fetch"],
                check=True,
                timeout=GIT_FETCH_TIMEOUT,
                stdout=subprocess.DEVNULL,
            )

            # Get current branch
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=GIT_SHORT_TIMEOUT,
            ).stdout.strip()

            # Check if upstream is configured
            upstream_proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "@{u}"],
                capture_output=True,
                text=True,
                timeout=GIT_SHORT_TIMEOUT,
                check=False,
            )
            if upstream_proc.returncode != 0:
                return (
                    False,
                    f"No upstream configured for branch '{branch}'. Please 'git push -u origin {branch}' first.",
                )

            # Check for unpushed commits and upstream changes
            # git rev-list --left-right --count @{u}...HEAD
            # Output: "behind  ahead" (left=@{u}, right=HEAD)
            rev_list = subprocess.run(
                ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"],
                capture_output=True,
                text=True,
                timeout=GIT_SHORT_TIMEOUT,
                check=False,
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
            upstream_proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "@{u}"],
                capture_output=True,
                text=True,
                timeout=GIT_SHORT_TIMEOUT,
                check=False,
            )
            if upstream_proc.returncode != 0:
                return {
                    "status": "error",
                    "message":
                    f"No upstream configured for branch '{branch}'. Please 'git push -u origin {branch}' first.",
                    "next_step": "Configure upstream and retry safe_push.",
                }
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": "Git upstream check timed out."
            }
        except (subprocess.CalledProcessError, FileNotFoundError):
            # This can happen if 'git rev-parse --abbrev-ref @{u}' fails for other reasons
            return {
                "status": "error",
                "message":
                f"Failed to determine upstream for branch '{branch}'. Please ensure it's configured.",
                "next_step": "Check git configuration and retry safe_push.",
            }

        # Attempt push
        try:
            subprocess.run(["git", "push"],
                           check=True,
                           timeout=GIT_PUSH_TIMEOUT)
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
                "status":
                "error",
                "message":
                f"Push failed or timed out: {safe_err}. You may need to pull changes first or check your connection.",
                "next_step":
                "Pull changes, resolve conflicts, and retry safe_push.",
            }

    def _poll_for_main_reviewer(
        self,
        pr_number,
        since_iso,
        validation_reviewer,
        max_attempts=None,
        poll_interval=None,
    ):
        """
        Polls until the main reviewer has provided feedback since the given timestamp.
        Enforces the Loop Rule: never return until main reviewer responds or timeout.

        Returns the status data from check_status once main reviewer feedback is detected.
        """
        max_attempts = POLL_MAX_ATTEMPTS if max_attempts is None else max_attempts
        poll_interval = (POLL_INTERVAL_SECONDS
                         if poll_interval is None else poll_interval)

        # Initialize status_data to handle edge case where max_attempts is 0 or loop is interrupted
        status_data = None

        for attempt in range(1, max_attempts + 1):
            try:
                self._log(f"Poll attempt {attempt}/{max_attempts}...")

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
                    for item in status_data.get("items", []))

                if main_reviewer_has_new_feedback:
                    main_reviewer_info = status_data.get("main_reviewer", {})
                    main_reviewer_state = main_reviewer_info.get(
                        "state", "PENDING")
                    self._log(
                        f"Main reviewer ({validation_reviewer}) has NEW feedback with state: {main_reviewer_state}"
                    )
                    return status_data

                # Not yet - wait and poll again
                if attempt < max_attempts:
                    self._log(
                        f"Main reviewer has not responded yet. Waiting {poll_interval}s before next poll..."
                    )
                    time.sleep(poll_interval)
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
                    "INTERRUPTED: Polling cancelled by user. Resume with 'status' command."
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

    def trigger_review(  # skipcq: PY-R1000
        self,
        pr_number,
        wait_seconds=180,
        validation_reviewer=DEFAULT_VALIDATION_REVIEWER,
        model="gemini-3.1-pro-preview",
    ):
        """
        1. Checks local state (Hard Constraint).
        2. Post comments to trigger bots or run local/offline reviewer.
        3. Polls for main reviewer feedback.
        """
        local = self.local
        offline = self.offline
        # Step 1: Enforce Push
        if not offline:
            is_safe, msg = self._check_local_state()
            if not is_safe:
                print_error(
                    f"FAILED: {msg}\nTip: Use the 'safe_push' tool or run 'git push' manually."
                )
            self._log(f"State verified: {msg}")
        else:
            self._log(
                "Offline mode: Local state clean. Skipping remote state checks."
            )

        # Capture start time for status check
        start_time = datetime.now(timezone.utc)

        # Step 2: Trigger Bots or Run Local/Offline Reviewer
        triggered_bots = []
        local_review_item = None

        if local or offline:
            pr_label = f" PR #{pr_number}" if pr_number else " local changes"
            self._log(
                f"Triggering {'local' if local else 'offline'} review for{pr_label}..."
            )
            settings_path = os.path.join(os.path.dirname(__file__),
                                         "settings.json")
            settings = {
                "gemini_cli_channel": "preview",
                "local_model": model,
            }
            try:
                if os.path.exists(settings_path):
                    with open(settings_path, "r", encoding="utf-8") as f:
                        settings.update(json.load(f))
            except (OSError, json.JSONDecodeError) as e:
                self._log(f"Warning: Failed to load {settings_path}: {e}")

            channel = settings.get("gemini_cli_channel", "preview")
            pkg = f"@google/gemini-cli@{channel}"

            cmd = [
                "npx",
                "-y",
                pkg,
                "--approval-mode",
                "yolo",
                "--model",
                str(settings.get("local_model") or model),
                "--prompt",                "/code-review",
            ]
            self._log(
                f"  Running {'local' if local else 'offline'} reviewer: {cmd}")
            try:
                res = subprocess.run(cmd,
                                     check=False,
                                     capture_output=True,
                                     text=True,
                                     timeout=600)
                clean_stdout = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])",
                                      "", res.stdout)
                self._log(
                    f"{'Local' if local else 'Offline'} Review Feedback:\n{clean_stdout[:1000]}{'...' if len(clean_stdout) > 1000 else ''}"
                )
                if res.returncode != 0:
                    print_error(f"{'Local' if local else 'Offline'} reviewer failed with exit code {res.returncode}.\nSTDERR: {self._mask_token(res.stderr)}\nSTDOUT: {self._mask_token(res.stdout)}")
                if res.stderr:
                    self._log(
                        f"{'Local' if local else 'Offline'} Review Warnings/Errors:\n{self._mask_token(res.stderr)}"
                    )

                local_review_item = {
                    "type": "local_review" if local else "offline_review",
                    "user": "gemini-cli-review",
                    "body": clean_stdout,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                triggered_bots.append("/code-review")

                if offline:
                    return {
                        "status":
                        "success",
                        "message":
                        "Offline review completed locally.",
                        "triggered_bots":
                        triggered_bots,
                        "initial_status": {
                            "status":
                            "success",
                            "pr_number":
                            pr_number,
                            "checked_at_utc":
                            datetime.now(timezone.utc).isoformat(),
                            "new_item_count":
                            1,
                            "items": [local_review_item],
                            "main_reviewer": {
                                "user": "gemini-cli-review",
                                "state": "COMMENTED",
                            },
                        },
                        "next_step":
                        "Analyze the feedback in 'initial_status.items' and implement fixes. Proceed directly to Step 4 (Analyze & Implement).",
                    }
            except subprocess.TimeoutExpired as e:
                print_error(
                    f"{'Local' if local else 'Offline'} reviewer timed out after {e.timeout}s."
                )
            except FileNotFoundError as e:
                print_error(
                    f"{'Local' if local else 'Offline'} reviewer executable not found. Ensure npx/gemini-cli is installed. Error: {e}"
                )

        else:
            try:
                pr = self.repo.get_pull(pr_number)
                self._log(
                    f"Triggering reviews on PR #{pr_number} ({pr.title})...")
                for c in REVIEW_COMMANDS:
                    pr.create_issue_comment(c)
                    self._log(f"  Posted: {c}")
                    triggered_bots.append(c)
                self._log("All review bots triggered successfully.")
            except GithubException as e:
                print_error(f"GitHub API Error: {self._mask_token(str(e))}")
                return None

        # Step 3: Poll for Main Reviewer Response (Enforce Loop Rule)
        if local:
            wait_time = 120
            self._log("-" * 40)
            self._log(
                f"Auto-waiting {wait_time} seconds for independent online human/bot comments..."
            )
            try:
                time.sleep(wait_time)
            except KeyboardInterrupt:
                self._log("\nWait interrupted. Checking status immediately...")

            self._log("-" * 40)
            self._log("Fetching status from GitHub...")
            try:
                status_data = self.check_status(
                    pr_number,
                    since_iso=start_time.isoformat(),
                    return_data=True,
                    validation_reviewer=validation_reviewer,
                )
            except GithubException as e:
                self._log(f"GitHub polling failed: {e}")
                status_data = {
                    "status": "success",
                    "items": [],
                    "next_step": "Analyze feedback and implement fixes.",
                }

            if local_review_item:
                status_data["items"].insert(0, local_review_item)
                status_data["new_item_count"] = len(status_data["items"])
                status_data["main_reviewer"] = {
                    "user": "gemini-cli-review",
                    "state": "COMMENTED",
                }
                status_data["next_step"] = (
                    f"New feedback received. {ACTION_INSTRUCTIONS}")

            message = "Triggered local review and fetched online comments."

            return {
                "status":
                "success",
                "message":
                message,
                "triggered_bots":
                triggered_bots,
                "initial_status":
                status_data,
                "next_step":
                status_data.get("next_step",
                                "Analyze feedback and implement fixes."),
            }

        else:
            if wait_seconds > 0:
                self._log("-" * 40)
                self._log(
                    f"Auto-waiting {wait_seconds} seconds for initial bot responses..."
                )
                try:
                    time.sleep(wait_seconds)
                except KeyboardInterrupt:
                    self._log(
                        "\nWait interrupted. Checking status immediately...")

                self._log("-" * 40)
                self._log(
                    "Polling for main reviewer feedback (enforcing Loop Rule)..."
                )

                status_data = self._poll_for_main_reviewer(
                    pr_number=pr_number,
                    since_iso=start_time.isoformat(),
                    validation_reviewer=validation_reviewer,
                )
            else:
                status_data = {
                    "status":
                    "skipped",
                    "message":
                    "Initial status check skipped due to wait_seconds=0.",
                }

            message = "Triggered reviews."
            if wait_seconds > 0 and not status_data.get("polling_interrupted"):
                message = "Triggered reviews and polled for main reviewer feedback."
            elif status_data.get("polling_interrupted"):
                message = "Triggered reviews; polling was interrupted."
            elif wait_seconds <= 0:
                message = "Triggered reviews; polling was skipped."

            return {
                "status":
                "success",
                "message":
                message,
                "triggered_bots":
                triggered_bots,
                "initial_status":
                status_data,
                "next_step":
                status_data.get(
                    "next_step",
                    "Run 'status' to check for feedback. Be autonomous, don't stop the cycle. You must regularly poll for the main-reviewer's review until it eventually gets posted, then continue.",
                ),
            }

    def check_status(  # skipcq: PY-R1000
        self,
        pr_number,
        since_iso=None,
        return_data=False,
        validation_reviewer="gemini-code-assist[bot]",
    ):
        """
        Stateless check of PR feedback using PyGithub.
        Returns and/or prints JSON summary of status.
        """

        def get_aware_utc_datetime(dt_obj):
            """Converts a naive datetime from PyGithub into a timezone-aware one."""
            if dt_obj is None:
                return None
            if dt_obj.tzinfo is None:
                return dt_obj.replace(tzinfo=timezone.utc)
            return dt_obj.astimezone(timezone.utc)

        try:
            if getattr(self, "repo", None) is None:
                if return_data:
                    return {
                        "status":
                        "error",
                        "message":
                        "Status check is not supported in offline mode as it requires GitHub API access.",
                    }
                print_error(
                    "Status check is not supported in offline mode as it requires GitHub API access."
                )

            pr = self.repo.get_pull(pr_number)

            since_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
            if since_iso:
                try:
                    if since_iso.endswith("Z"):
                        since_iso = since_iso[:-1] + "+00:00"
                    since_dt = datetime.fromisoformat(since_iso)
                    if since_dt.tzinfo is None:
                        since_dt = since_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    # Log warning but continue
                    print(
                        f"[{datetime.now(timezone.utc).isoformat()}] [AUDIT] Warning: Invalid timestamp {since_iso}, ignoring.",
                        file=sys.stderr,
                    )

            # Fetch comments (paginated by PyGithub automatically)
            new_feedback = []

            # 1. Issue Comments (General)
            issue = self.repo.get_issue(pr_number)
            for comment in issue.get_comments(since=since_dt):
                new_feedback.append({
                    "type":
                    "issue_comment",
                    "user":
                    comment.user.login,
                    "body":
                    comment.body,
                    "url":
                    comment.html_url,
                    "updated_at":
                    (get_aware_utc_datetime(comment.updated_at).isoformat()
                     if comment.updated_at else None),
                    "created_at":
                    get_aware_utc_datetime(comment.created_at).isoformat(),
                })

            # 2. Review Comments (Inline)
            # Fetch all review comments to ensure we catch edits (since param might only check creation time)
            for comment in pr.get_review_comments():
                # Use updated_at to catch edits
                comment_dt = get_aware_utc_datetime(comment.updated_at)
                if comment_dt and comment_dt >= since_dt:
                    new_feedback.append({
                        "type":
                        "inline_comment",
                        "user":
                        comment.user.login,
                        "body":
                        comment.body,
                        "path":
                        comment.path,
                        "line":
                        comment.line,
                        "created_at": (get_aware_utc_datetime(
                            comment.created_at).isoformat()
                            if comment.created_at else None),
                        "updated_at":
                        comment_dt.isoformat(),
                        "url":
                        comment.html_url,
                    })

            # 3. Reviews (Approvals/changes requested) - Filter locally
            # Materialize list for multiple iteration
            reviews = list(pr.get_reviews())
            for review in reviews:
                if (review.submitted_at
                    ):  # Ensure submitted_at is not None before processing
                    review_dt = get_aware_utc_datetime(review.submitted_at)
                    if review_dt and review_dt >= since_dt:
                        new_feedback.append({
                            "type":
                            "review_summary",
                            "user":
                            review.user.login,
                            "state":
                            review.state,
                            "body":
                            review.body,
                            "created_at":
                            review_dt.isoformat(),
                        })

            # Determine next_step based on findings AND validation_reviewer
            next_step = "Wait for reviews."
            has_changes_requested = any(
                item.get("state") == "CHANGES_REQUESTED"
                for item in new_feedback
                if item.get("type") == "review_summary")

            # Check Main Reviewer Status
            # Track latest state AND most recent approval separately
            # (fixes bug where APPROVED -> COMMENTED leaves approval_dt as None)
            main_reviewer_state = "PENDING"
            main_reviewer_last_approval_dt = None

            # Single pass: find latest state AND most recent approval timestamp
            found_latest_state = False
            for review in reversed(reviews):
                if review.user.login == validation_reviewer:
                    if not found_latest_state:
                        main_reviewer_state = review.state
                        found_latest_state = True

                    if (main_reviewer_last_approval_dt is None
                            and review.state == "APPROVED"):
                        main_reviewer_last_approval_dt = get_aware_utc_datetime(
                            review.submitted_at)

                    # Exit early if both are found
                    if (found_latest_state
                            and main_reviewer_last_approval_dt is not None):
                        break

            # Check for comments from main_reviewer AFTER approval
            # Note: Only check if approval exists, not if current state is APPROVED
            # (fixes bug where APPROVED -> COMMENTED was not detected)
            has_new_main_reviewer_comments = False
            if main_reviewer_last_approval_dt:
                for item in new_feedback:
                    # Check for issue_comment, inline_comment, OR review comments (state=COMMENTED)
                    is_main_reviewer = item.get("user") == validation_reviewer
                    is_comment = item.get("type") in [
                        "issue_comment", "inline_comment"
                    ]
                    is_review_comment = (item.get("type") == "review_summary"
                                         and item.get("state") == "COMMENTED")

                    if is_main_reviewer and (is_comment or is_review_comment):
                        # Use helper for consistent parsing
                        # Only use created_at to avoid treating edits (updated_at) of old comments as new feedback
                        created_at_val = item.get("created_at")
                        if created_at_val:
                            try:
                                # created_at is always an ISO string from our processing
                                # Handle Z suffix for Python < 3.11 compatibility
                                dt_val = datetime.fromisoformat(
                                    created_at_val.replace("Z", "+00:00"))
                                comment_dt = get_aware_utc_datetime(dt_val)

                                # Use >= to catch comments made at the exact same second
                                if comment_dt >= main_reviewer_last_approval_dt:
                                    has_new_main_reviewer_comments = True
                                    break
                            except (ValueError, TypeError) as e:
                                self._log(
                                    f"Warning: Could not parse date '{created_at_val}'. Error: {e}. Skipping item."
                                )
                                continue

            if has_changes_requested:
                next_step = f"CRITICAL: Changes requested by reviewer. {ACTION_INSTRUCTIONS}{RATE_LIMIT_INSTRUCTION}"
            elif has_new_main_reviewer_comments:
                next_step = f"New comments from {validation_reviewer} after approval. {ACTION_INSTRUCTIONS}{RATE_LIMIT_INSTRUCTION}"
            elif main_reviewer_state == "APPROVED":
                # Check if there's any OTHER feedback besides the main reviewer's approval
                other_feedback = [
                    item for item in new_feedback
                    if not (item.get("user") == validation_reviewer
                            and item.get("type") == "review_summary"
                            and item.get("state") == "APPROVED")
                ]
                if other_feedback:
                    next_step = f"New feedback received. {ACTION_INSTRUCTIONS}"
                else:
                    next_step = "Validation Complete (STOP LOOP - DO NOT MERGE AUTONOMOUSLY). Notify User. Never merge or delete a branch on your own, if you believe the main reviewer said that the PR is ready, just stop and ask for Human review"
            elif new_feedback:
                next_step = f"New feedback received. {ACTION_INSTRUCTIONS}"
            else:
                next_step = f"Waiting for approval from {validation_reviewer} (Current: {main_reviewer_state}). Poll again. Be autonomous, don't stop the cycle. You must regularly poll for the main-reviewer's review until it eventually gets posted, then continue."

            output = {
                "status": "success",
                "pr_number": pr_number,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "new_item_count": len(new_feedback),
                "items": new_feedback,
                "main_reviewer": {
                    "user": validation_reviewer,
                    "state": main_reviewer_state,
                },
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
    p_trigger = subparsers.add_parser("trigger_review",
                                      help="Trigger reviews safely")
    p_trigger.add_argument("pr_number", type=int, nargs="?", default=None)
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
    p_trigger.add_argument(
        "--local",
        action="store_true",
        help="Run in local mode (runs gemini-cli-review, waits 120s for GitHub comments)",
    )
    p_trigger.add_argument(
        "--offline",
        action="store_true",
        help="Run completely offline without pushing to GitHub. Only runs gemini-cli-review locally.",
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

    args = parser.parse_args()

    try:
        local_mode = getattr(args, "local", False)
        offline_mode = getattr(args, "offline", False)
        if local_mode and offline_mode:
            parser.error("Cannot specify both --local and --offline")
        mgr = ReviewManager(local=local_mode, offline=offline_mode)

        if args.command == "trigger_review":
            if not offline_mode and (args.pr_number is None
                                     or args.pr_number <= 0):
                parser.error(
                    "pr_number is required unless --offline is specified")
            result = mgr.trigger_review(
                args.pr_number,
                wait_seconds=args.wait,
                validation_reviewer=args.validation_reviewer,
            )
            print_json(result)
        elif args.command == "status":
            mgr.check_status(args.pr_number,
                             args.since,
                             validation_reviewer=args.validation_reviewer)
        elif args.command == "safe_push":
            result = mgr.safe_push()
            print_json(result)
            if result["status"] != "success":
                sys.exit(1)
    except Exception as e:  # skipcq: PYL-W0703, PYL-W0718, PTC-W0045, BAN-B607
        # Catch-all for unhandled exceptions to prevent raw tracebacks in JSON output
        # Log full traceback to stderr for debugging
        error_msg = str(e)
        if "mgr" in locals() and hasattr(mgr, "_mask_token"):
            error_msg = mgr._mask_token(error_msg)
        sys.stderr.write(f"CRITICAL ERROR: {error_msg}\n")

        tb = traceback.format_exc()
        if "mgr" in locals() and hasattr(mgr, "_mask_token"):
            tb = mgr._mask_token(tb)
        sys.stderr.write(tb)

        # Output clean JSON error
        print(
            json.dumps({
                "status": "error",
                "message":
                "An internal error occurred. See stderr for details.",
                "error_type": type(e).__name__,
            }))
        sys.exit(1)


if __name__ == "__main__":
    main()
