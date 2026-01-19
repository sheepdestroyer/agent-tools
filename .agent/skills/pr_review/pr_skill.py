#!/usr/bin/env python3
"""
Robust PR Review Skill using PyGithub.
Enforces "Push Before Trigger" and "The Loop" programmatically.
"""
import sys
import os
import json
import subprocess
import argparse
import time
from datetime import datetime, timezone, timedelta
from github import Github, GithubException, Auth

# Constants for Review Bots
REVIEW_COMMANDS = [
    "/gemini review",
    "@coderabbitai review",
    "@sourcery-ai review",
    "/review",  # Qodo
    "@ellipsis review this"
]

class ReviewManager:
    def __init__(self):
        # Authenticate with GitHub
        self.token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self.token:
            # Fallback to gh CLI for auth token if env var is missing
            try:
                res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
                self.token = res.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("Error: No GITHUB_TOKEN found and 'gh' command failed or is not installed.", file=sys.stderr)
                sys.exit(1)
        
        self.g = Github(auth=Auth.Token(self.token))
        self.repo = self._detect_repo()
        self._ensure_workspace()

    def _ensure_workspace(self):
        """Enforces Rule 3: Artifact Hygiene. Creates agent-workspace/ if missing."""
        workspace = os.path.join(os.getcwd(), "agent-workspace")
        os.makedirs(workspace, exist_ok=True)

    def _detect_repo(self):
        """Auto-detects current repository from git remote."""
        try:
            # Get remote URL
            res = subprocess.run(
                ["gh", "repo", "view", "--json", "owner,name"],
                capture_output=True, text=True, check=True
            )
            data = json.loads(res.stdout)
            full_name = f"{data['owner']['login']}/{data['name']}"
            return self.g.get_repo(full_name)
        except Exception as e:
            print(f"Error checking repository context: {e}", file=sys.stderr)
            sys.exit(1)

    def _check_local_state(self):
        """
        Enforces:
        1. Clean git status (no uncommitted changes)
        2. Local branch is pushed (no diff with upstream)
        """
        # 1. Check for uncommitted changes
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if status.stdout.strip():
            return False, "Uncommitted changes detected. Please commit or stash them first."

        # 2. Check if pushed to upstream
        # Get current branch
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
        
        # Check if upstream is configured
        upstream_proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "@{u}"], capture_output=True, text=True)
        if upstream_proc.returncode != 0:
             return False, f"No upstream configured for branch '{branch}'. Please 'git push -u origin {branch}' first."
        
        # Check for unpushed commits
        # git diff --quiet @{u} returns 0 if no diff, 1 if diff.
        diff = subprocess.run(["git", "diff", "--quiet", "@{u}"], capture_output=True)
        if diff.returncode != 0:
            return False, f"Local branch '{branch}' has unpushed changes. You MUST push before triggering a review."
            
        return True, "Code is clean and pushed."

    def safe_push(self):
        """Attempts to push changes safely, aborting if uncommitted changes exist."""
        print("Running safe push verification...", file=sys.stderr)
        
        # Only check for uncommitted changes, NOT for unpushed commits
        # (the whole point of safe_push is to push those commits!)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if status.stdout.strip():
            print("Error: Uncommitted changes detected. Please commit or stash them first.", file=sys.stderr)
            return False

        # Attempt push
        try:
            subprocess.run(["git", "push"], check=True)
            print("Push successful.", file=sys.stderr)
            return True
        except subprocess.CalledProcessError:
            print("Error: Push failed. You may need to pull changes first.", file=sys.stderr)
            return False

    def trigger_review(self, pr_number):
        """
        1. Checks local state (Hard Constraint).
        2. Post comments to trigger bots.
        """
        # Step 1: Enforce Push
        is_safe, msg = self._check_local_state()
        if not is_safe:
            print(f"FAILED: {msg}", file=sys.stderr)
            print("Tip: Use the 'safe_push' tool or run 'git push' manually.", file=sys.stderr)
            sys.exit(1)

        print(f"State verified: {msg}", file=sys.stderr)

        # Step 2: Trigger Bots
        try:
            pr = self.repo.get_pull(pr_number)
            print(f"Triggering reviews on PR #{pr_number} ({pr.title})...", file=sys.stderr)
            
            # Post one comment with all commands? Or separate? 
            # Separate is safer for parsing by bots.
            for cmd in REVIEW_COMMANDS:
                pr.create_issue_comment(cmd)
                print(f"  Posted: {cmd}", file=sys.stderr)
                
            print("All review bots triggered successfully.", file=sys.stderr)
            print("Please wait 2-3 minutes before checking status.", file=sys.stderr)

        except GithubException as e:
            print(f"GitHub API Error: {e}", file=sys.stderr)
            sys.exit(1)

    def check_status(self, pr_number, since_iso=None):
        """
        Stateless check of PR feedback using PyGithub.
        Returns JSON summary of status.
        """
        try:
            pr = self.repo.get_pull(pr_number)
            
            since_dt = datetime.min.replace(tzinfo=timezone.utc)
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
            for comment in pr.get_issue_comments():
                if comment.created_at.replace(tzinfo=timezone.utc) > since_dt:
                    new_feedback.append({
                        "type": "issue_comment",
                        "user": comment.user.login,
                        "body": comment.body,
                        "url": comment.html_url,
                        "created_at": comment.created_at.isoformat()
                    })

            # 2. Review Comments (Inline)
            for comment in pr.get_review_comments():
                if comment.created_at.replace(tzinfo=timezone.utc) > since_dt:
                    new_feedback.append({
                        "type": "inline_comment",
                        "user": comment.user.login,
                        "body": comment.body,
                        "path": comment.path,
                        "line": comment.line,
                        "created_at": comment.created_at.isoformat()
                    })

            # 3. Reviews (Approvals/changes requested)
            for review in pr.get_reviews():
                # Guard against None submitted_at (pending reviews)
                if review.submitted_at is None:
                    continue
                if review.submitted_at.replace(tzinfo=timezone.utc) > since_dt:
                    new_feedback.append({
                        "type": "review_summary",
                        "user": review.user.login,
                        "state": review.state,
                        "body": review.body,
                        "created_at": review.submitted_at.isoformat()
                    })
            
            # Return JSON
            output = {
                "pr_number": pr_number,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "new_item_count": len(new_feedback),
                "items": new_feedback
            }
            print(json.dumps(output, indent=2))

        except GithubException as e:
            print(f"GitHub API Error: {e}", file=sys.stderr)
            sys.exit(1)

    def wait_for_feedback(self, pr_number, timeout_minutes=15, interval_seconds=60):
        """
        Blocks and polls until new bot feedback is detected or timeout.
        Returns generated feedback status.
        """
        print(f"Waiting for feedback on PR #{pr_number} (Timeout: {timeout_minutes}m, Poll: {interval_seconds}s)...", file=sys.stderr)
        
        start_time = datetime.now(timezone.utc)
        end_time = start_time + timedelta(minutes=timeout_minutes)
        
        # Get baseline state
        try:
            pr = self.repo.get_pull(pr_number)
            baseline_comments = pr.comments + pr.review_comments
            baseline_updated = pr.updated_at.replace(tzinfo=timezone.utc)
            
            print(f"Baseline: {baseline_comments} comments, updated at {baseline_updated.isoformat()}", file=sys.stderr)

            while datetime.now(timezone.utc) < end_time:
                time.sleep(interval_seconds)
                
                # Refresh PR - getting the object again forces a refresh in PyGithub
                pr = self.repo.get_pull(pr_number) 
                
                current_comments = pr.comments + pr.review_comments
                current_updated = pr.updated_at.replace(tzinfo=timezone.utc)
                
                # Check for *any* change in update time or comment count
                if current_comments > baseline_comments or current_updated > baseline_updated:
                    print(f"Detected change! ({current_comments} comments, updated {current_updated.isoformat()})", file=sys.stderr)
                    # We return the status since the baseline, effectively capturing the new items.
                    self.check_status(pr_number, since_iso=baseline_updated.isoformat())
                    return

                print(".", end="", flush=True, file=sys.stderr)
        
        except GithubException as e:
             print(f"GitHub Error during wait: {e}", file=sys.stderr)
             sys.exit(1)

        print(f"\nTimeout reached ({timeout_minutes}m). No new feedback detected.", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="PR Skill Agent Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Trigger Review
    p_trigger = subparsers.add_parser("trigger_review", help="Trigger reviews safely")
    p_trigger.add_argument("pr_number", type=int)

    # Status
    p_status = subparsers.add_parser("status", help="Check review status")
    p_status.add_argument("pr_number", type=int)
    p_status.add_argument("--since", help="ISO 8601 timestamp")

    # Safe Push
    p_push = subparsers.add_parser("safe_push", help="Push changes safely")

    # Wait
    p_wait = subparsers.add_parser("wait", help="Block and wait for feedback")
    p_wait.add_argument("pr_number", type=int)
    p_wait.add_argument("--timeout", type=int, default=15, help="Timeout in minutes")
    p_wait.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")

    args = parser.parse_args()
    mgr = ReviewManager()

    if args.command == "trigger_review":
        mgr.trigger_review(args.pr_number)
    elif args.command == "status":
        mgr.check_status(args.pr_number, args.since)
    elif args.command == "safe_push":
        success = mgr.safe_push()
        if not success:
            sys.exit(1)
    elif args.command == "wait":
        mgr.wait_for_feedback(args.pr_number, args.timeout, args.interval)

if __name__ == "__main__":
    main()
