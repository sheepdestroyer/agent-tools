#!/usr/bin/env python3
"""
Robust PR Review Skill using PyGithub.
Enforces "Push Before Trigger" and "The Loop" programmatically.
Outputs JSON to stdout for easy parsing by agents.
"""
import sys
import os
import json
import subprocess
import argparse
import time
from datetime import datetime, timezone
import re
from github import Github, GithubException, Auth

# Constants for Review Bots
REVIEW_COMMANDS = [
    "/gemini review",
    "@coderabbitai review",
    "@sourcery-ai review",
    "/review",  # Qodo
    "@ellipsis review this"
]

# Timeout constants for subprocess calls (in seconds)
GIT_SHORT_TIMEOUT = 10
GIT_FETCH_TIMEOUT = 30
GIT_PUSH_TIMEOUT = 60
GH_AUTH_TIMEOUT = 10
GH_REPO_VIEW_TIMEOUT = 30

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
        self.token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self.token:
            # Fallback to gh CLI for auth token if env var is missing
            try:
                res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True, timeout=GH_AUTH_TIMEOUT)
                self.token = res.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                print_error("No GITHUB_TOKEN found and 'gh' command failed or is not installed.")
        
        try:
            self.g = Github(auth=Auth.Token(self.token))
            self.repo = self._detect_repo()
            self._ensure_workspace()
        except (GithubException, OSError, ValueError) as e:
            print_error(f"Initialization failed: {e}")

    def _log(self, message):
        """Audit logging to stderr with timestamp."""
        timestamp = datetime.now(timezone.utc).isoformat()
        print(f"[{timestamp}] {message}", file=sys.stderr)

    def _ensure_workspace(self):
        """Enforces Rule 3: Artifact Hygiene. Creates agent-workspace/ if missing."""
        workspace = os.path.join(os.getcwd(), "agent-workspace")
        os.makedirs(workspace, exist_ok=True)

    def _detect_repo(self):
        """Auto-detects current repository from git remote (local check preferred)."""
        # 1. Try local git remote first (fast, no network)
        try:
            # Get origin URL
            res = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True, text=True, check=True, timeout=GIT_SHORT_TIMEOUT
            )
            url = res.stdout.strip()
            
            # Extract owner/repo using regex
            # Matches: https://github.com/owner/repo.git, git@github.com:owner/repo.git, etc.
            match = re.search(r"github\.com[:/]([^/.]+)/([^/.]+?)(?:\.git)?$", url)
            if match:
                full_name = f"{match.group(1)}/{match.group(2)}"
                return self.g.get_repo(full_name)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            # Ignore local errors and fall back to gh
            self._log("Local git remote check failed, falling back to 'gh'...")
            pass

        # 2. Fallback to gh CLI (slower, network dependent)
        try:
            res = subprocess.run(
                ["gh", "repo", "view", "--json", "owner,name"],
                capture_output=True, text=True, check=True, timeout=GH_REPO_VIEW_TIMEOUT
            )
            data = json.loads(res.stdout)
            full_name = f"{data['owner']['login']}/{data['name']}"
            return self.g.get_repo(full_name)
        except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, ValueError):
            raise RuntimeError("Error checking repository context: Ensure 'gh' is installed and you are in a git repository.")

    def _check_local_state(self):
        """
        Enforces:
        1. Clean git status (no uncommitted changes)
        2. Local branch is pushed (no diff with upstream)
        """
        # 1. Check for uncommitted changes
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True, timeout=GIT_SHORT_TIMEOUT)
        if status.stdout.strip():
            return False, "Uncommitted changes detected. Please commit or stash them first."

        # 2. Check if pushed to upstream
        try:
            # Fetch latest state from remote for accurate comparison
            subprocess.run(["git", "fetch"], capture_output=True, check=True, timeout=GIT_FETCH_TIMEOUT)

            # Get current branch
            branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True, timeout=GIT_SHORT_TIMEOUT).stdout.strip()
            
            # Check if upstream is configured
            upstream_proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "@{u}"], capture_output=True, text=True, timeout=GIT_SHORT_TIMEOUT)
            if upstream_proc.returncode != 0:
                return False, f"No upstream configured for branch '{branch}'. Please 'git push -u origin {branch}' first."
            
            # Check for unpushed commits and upstream changes
            # git rev-list --left-right --count @{u}...HEAD
            # Output: "behind  ahead" (e.g., "0  1")
            rev_list = subprocess.run(
                ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"], 
                capture_output=True, text=True, timeout=GIT_SHORT_TIMEOUT
            )
            if rev_list.returncode == 0:
                parts = rev_list.stdout.split()
                if len(parts) == 2:
                    behind, ahead = int(parts[0]), int(parts[1])
                    if ahead > 0:
                        return False, f"Local branch '{branch}' has {ahead} unpushed commit(s). You MUST push before triggering a review."
                    if behind > 0:
                        return False, f"Local branch '{branch}' is behind upstream by {behind} commit(s). Please pull."
            else:
                 # Fallback/General error
                 return False, f"Failed to check divergence: {rev_list.stderr.strip()}"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return False, f"Git check failed: {str(e)}"
            
        return True, "Code is clean and pushed."


    def safe_push(self):
        """Attempts to push changes safely, aborting if uncommitted changes exist. Ignores unpushed commits check."""
        self._log("Running safe_push verification...")
        
        # Only check for uncommitted changes, NOT for unpushed commits
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True, timeout=GIT_SHORT_TIMEOUT)
        if status.stdout.strip():
            return {"status": "error", "message": "Uncommitted changes detected. Please commit or stash them first."}

        # Check upstream configuration (optional but good for safety)
        try:
            branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True, timeout=GIT_SHORT_TIMEOUT).stdout.strip()
            # Just check if we can get upstream, if not we might need -u
            subprocess.run(["git", "rev-parse", "--abbrev-ref", "@{u}"], capture_output=True, text=True, timeout=GIT_SHORT_TIMEOUT, check=True)
        except subprocess.CalledProcessError:
            return {"status": "error", "message": f"No upstream configured for branch '{branch}'. Please 'git push -u origin {branch}' first."}
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Git operations timed out."}

        # Attempt push
        try:
            subprocess.run(["git", "push"], check=True, timeout=GIT_PUSH_TIMEOUT)
            return {"status": "success", "message": "Push successful."}
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return {"status": "error", "message": f"Push failed or timed out: {e}. You may need to pull changes first or check your connection."}

    def trigger_review(self, pr_number, wait_seconds=60):
        """
        1. Checks local state (Hard Constraint).
        2. Post comments to trigger bots.
        """
        # Step 1: Enforce Push
        is_safe, msg = self._check_local_state()
        if not is_safe:
            print_error(f"FAILED: {msg}\nTip: Use the 'safe_push' tool or run 'git push' manually.")

        self._log(f"State verified: {msg}")

        # Capture start time for status check
        start_time = datetime.now(timezone.utc)

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
            
            # Step 3: Auto-Wait and Check (Enforce Loop)
            if wait_seconds > 0:
                self._log("-" * 40)
                self._log(f"Auto-waiting {wait_seconds} seconds for initial feedback to ensure loop continuity...")
                try:
                     time.sleep(wait_seconds)
                except KeyboardInterrupt:
                     self._log("\nWait interrupted. checking status immediately...")

                self._log("-" * 40)
                self._log("Initial Status Check:")
                
                # Check status since start of trigger
                status_data = self.check_status(pr_number, since_iso=start_time.isoformat(), return_data=True)
            else:
                status_data = {"status": "skipped", "message": "Initial status check skipped due to wait_seconds=0."}
            
            return {
                "status": "success",
                "message": "Triggered reviews and performed initial status check.",
                "triggered_bots": triggered_bots,
                "initial_status": status_data
            }

        except GithubException as e:
            print_error(f"GitHub API Error: {e}")

    def check_status(self, pr_number, since_iso=None, return_data=False):
        """
        Stateless check of PR feedback using PyGithub.
        Returns and/or prints JSON summary of status.
        """
        def get_aware_utc_datetime(dt_obj):
            """Converts a naive datetime from PyGithub into a timezone-aware one."""
            if dt_obj is None:
                return None
            return dt_obj.replace(tzinfo=timezone.utc)

        try:
            pr = self.repo.get_pull(pr_number)
            
            since_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
            if since_iso:
                try:
                    if since_iso.endswith('Z'):
                        since_iso = since_iso[:-1] + '+00:00'
                    since_dt = datetime.fromisoformat(since_iso)
                    if since_dt.tzinfo is None:
                        since_dt = since_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    print(f"Warning: Invalid timestamp {since_iso}, ignoring.", file=sys.stderr)

            # Fetch comments (paginated by PyGithub automatically)
            new_feedback = []
            
            # 1. Issue Comments (General)
            issue = self.repo.get_issue(pr_number)
            for comment in issue.get_comments(since=since_dt):
                comment_dt = get_aware_utc_datetime(comment.updated_at)
                if comment_dt and comment_dt > since_dt:
                    new_feedback.append({
                        "type": "issue_comment",
                        "user": comment.user.login,
                        "body": comment.body,
                        "url": comment.html_url,
                        "created_at": comment.created_at.isoformat()
                    })

            # 2. Review Comments (Inline)
            for comment in pr.get_review_comments(since=since_dt):
                # Use updated_at to catch edits
                comment_dt = get_aware_utc_datetime(comment.updated_at)
                if comment_dt and comment_dt > since_dt:
                    new_feedback.append({
                        "type": "inline_comment",
                        "user": comment.user.login,
                        "body": comment.body,
                        "path": comment.path,
                        "line": comment.line,
                        "updated_at": comment_dt.isoformat(),
                        "url": comment.html_url
                    })

            # 3. Reviews (Approvals/changes requested) - Filter locally
            for review in pr.get_reviews():
                if review.submitted_at: # Ensure submitted_at is not None before processing
                    review_dt = get_aware_utc_datetime(review.submitted_at)
                    if review_dt and review_dt > since_dt:
                        new_feedback.append({
                            "type": "review_summary",
                            "user": review.user.login,
                            "state": review.state,
                            "body": review.body,
                            "created_at": review.submitted_at.isoformat()
                        })
            
            output = {
                "status": "success",
                "pr_number": pr_number,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "new_item_count": len(new_feedback),
                "items": new_feedback
            }
            
            if return_data:
                return output
            else:
                print_json(output)
                return output

        except GithubException as e:
            if return_data:
                raise e
            print_error(f"GitHub API Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="PR Skill Agent Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Trigger Review
    p_trigger = subparsers.add_parser("trigger_review", help="Trigger reviews safely")
    p_trigger.add_argument("pr_number", type=int)
    p_trigger.add_argument("--wait", type=int, default=180, help="Seconds to wait for initial feedback (default: 180)")

    # Status
    p_status = subparsers.add_parser("status", help="Check review status")
    p_status.add_argument("pr_number", type=int)
    p_status.add_argument("--since", help="ISO 8601 timestamp")

    # Safe Push
    subparsers.add_parser("safe_push", help="Push changes safely")

    args = parser.parse_args()
    
    try:
        mgr = ReviewManager()

        if args.command == "trigger_review":
            result = mgr.trigger_review(args.pr_number, wait_seconds=args.wait)
            print_json(result)
        elif args.command == "status":
            mgr.check_status(args.pr_number, args.since)
        elif args.command == "safe_push":
            result = mgr.safe_push()
            print_json(result)
            if result["status"] != "success":
                sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {str(e)}")

if __name__ == "__main__":
    main()
