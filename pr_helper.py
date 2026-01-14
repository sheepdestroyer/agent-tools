#!/usr/bin/env python3
"""
Unified helper script for managing the GitHub PR review cycle, 
including triggering reviews, monitoring feedback, and verifying fixes.
"""
import json
import time
import subprocess
import sys
import os
import argparse
from datetime import datetime, timezone
import re

# Centralized constants
def get_current_repo_context():
    """Attempts to detect the current repository owner and name using gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            owner_data = data.get("owner")
            owner = owner_data.get("login") if isinstance(owner_data, dict) else None
            return owner, data.get("name")
        elif result.stderr:
            print(f"Warning: 'gh repo view' failed: {result.stderr.strip()}", file=sys.stderr)
    except FileNotFoundError:
        print("Warning: GitHub CLI 'gh' not found. Please install it to use auto-detection.", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("Warning: 'gh repo view' timed out.", file=sys.stderr)
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        print(f"Warning: Could not auto-detect repository context: {e}", file=sys.stderr)
    return None, None

def run_gh_api(path, paginate=True):
    """Executes a GitHub API call using the gh CLI and returns the JSON response."""
    cmd = ["gh", "api", path]
    if paginate:
        cmd.append("--paginate")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error calling GitHub API: {e.stderr}", file=sys.stderr)
        return []
    except json.JSONDecodeError:
        print(f"Error decoding JSON from GitHub API at {path}", file=sys.stderr)
        return []

def get_all_feedback(pr_number, owner, repo):
    """Fetches Reviews, Inline Comments, and Issue Comments from GitHub."""
    base_path = f"repos/{owner}/{repo}"
    reviews = run_gh_api(f"{base_path}/pulls/{pr_number}/reviews")
    inline_comments = run_gh_api(f"{base_path}/pulls/{pr_number}/comments")
    issue_comments = run_gh_api(f"{base_path}/issues/{pr_number}/comments")
    return {
        "reviews": reviews,
        "inline_comments": inline_comments,
        "issue_comments": issue_comments
    }

def parse_ts(ts_str):
    """Parses ISO 8601 timestamp to datetime object."""
    if not ts_str:
        return None
    try:
        if ts_str.endswith('Z'):
            ts_str = ts_str[:-1] + '+00:00'
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
             dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None

def filter_feedback_since(feedback, since_iso):
    """Filters results to items newer than since_iso."""
    since_dt = parse_ts(since_iso)
    if not since_dt:
            print(
                f"Warning: invalid --since timestamp {since_iso!r}; "
                "falling back to 1970-01-01T00:00:00Z",
                file=sys.stderr,
            )
            since_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)

    new_items = []
    
    def process_items(items, label):
        count = 0
        for item in items:
            # GitHub uses multiple keys for timestamps
            ts_str = item.get('submitted_at') or item.get('updated_at') or item.get('created_at')
            item_dt = parse_ts(ts_str)
            
            if item_dt and item_dt > since_dt:
                new_items.append({**item, '_type': label})
                count += 1
        return count

    counts = {
        "reviews": process_items(feedback['reviews'], 'review_summary'),
        "inline": process_items(feedback['inline_comments'], 'inline_comment'),
        "general": process_items(feedback['issue_comments'], 'issue_comment')
    }
    return new_items, counts

def cmd_trigger(args):
    """Triggers reviews from Gemini, CodeRabbit, Sourcery, Qodo, and Ellipsis."""
    print(f"Triggering reviews for PR #{args.pr_number}...", file=sys.stderr)
    repo_flag = ["-R", f"{args.owner}/{args.repo}"]
    try:
        subprocess.run(["gh", "pr", "comment", str(args.pr_number), "--body", "/gemini review"] + repo_flag, check=True)
        subprocess.run(["gh", "pr", "comment", str(args.pr_number), "--body", "@coderabbitai review"] + repo_flag, check=True)
        subprocess.run(["gh", "pr", "comment", str(args.pr_number), "--body", "@sourcery-ai review"] + repo_flag, check=True)
        subprocess.run(["gh", "pr", "comment", str(args.pr_number), "--body", "/review"] + repo_flag, check=True)
        subprocess.run(["gh", "pr", "comment", str(args.pr_number), "--body", "@ellipsis review this"] + repo_flag, check=True)
        print("Reviews triggered successfully.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Error triggering reviews: {e}", file=sys.stderr)
        sys.exit(1)

def cmd_fetch(args):
    """One-shot fetch of all new feedback."""
    feedback = get_all_feedback(args.pr_number, args.owner, args.repo)
    new_items, counts = filter_feedback_since(feedback, args.since)
    
    if new_items:
        print(f"Found new feedback: {counts}", file=sys.stderr)
        if args.output:
            try:
                with open(args.output, 'w') as f:
                    json.dump(new_items, f, indent=2)
                print(f"Written to {args.output}", file=sys.stderr)
            except IOError as e:
                print(f"Error writing to {args.output}: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print(json.dumps(new_items, indent=2))
    else:
        print(f"No new feedback since {args.since}.", file=sys.stderr)

def cmd_monitor(args):
    """Polls for new feedback until timeout."""
    print(f"Monitoring PR #{args.pr_number} for activity since {args.since}...", file=sys.stderr)
    
    if args.initial_wait > 0:
        print(f"Waiting {args.initial_wait}s before first check...", file=sys.stderr)
        time.sleep(args.initial_wait)

    start_time = time.time()
    while time.time() - start_time < args.timeout:
        feedback = get_all_feedback(args.pr_number, args.owner, args.repo)
        new_items, counts = filter_feedback_since(feedback, args.since)
        
        if new_items:
            print(f"\nNew Feedback Detected: {counts}", file=sys.stderr)
            if args.output:
                try:
                    with open(args.output, 'w') as f:
                        json.dump(new_items, f, indent=2)
                    print(f"Successfully written to {args.output}", file=sys.stderr)
                except IOError as e:
                    print(f"Error writing to {args.output}: {e}", file=sys.stderr)
                    sys.exit(1)
            else:
                print(json.dumps(new_items, indent=2))
            sys.exit(0)
            
        print(".", end="", flush=True, file=sys.stderr)
        time.sleep(args.interval)

    print("\nTimeout reached. No new feedback detected.", file=sys.stderr)
    sys.exit(1)

def cmd_verify(args):
    """Heuristic verification of local files against recent comments and runs pytest."""
    
    # 1. Run pytest as the primary verification method
    print("Running pytest for primary verification...", file=sys.stderr)
    try:
        # We assume PYTHONPATH=. is needed as per project standards
        env = os.environ.copy()
        if "PYTHONPATH" in env:
             env["PYTHONPATH"] = f".:{env['PYTHONPATH']}"
        else:
             env["PYTHONPATH"] = "."
        
        result = subprocess.run([sys.executable, "-m", "pytest"], capture_output=True, text=True, check=False, env=env)
        if result.returncode == 0:
            print("  STATUS: PASS - All tests passed successfully", file=sys.stderr)
        else:
            print(f"  STATUS: FAIL - Tests failed (exit code {result.returncode})", file=sys.stderr)
            print(result.stdout, file=sys.stderr)
    except subprocess.SubprocessError as e:
        print(f"  STATUS: ERROR - Could not run pytest: {e}", file=sys.stderr)

    # 2. Heuristic check of local files against comments
    if not os.path.exists(args.file):
        print(f"\nNote: JSON feedback file '{args.file}' not found. Skipping heuristic checks.", file=sys.stderr)
        return
        
    try:
        with open(args.file) as f:
            comments = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error reading or parsing {args.file}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nVerifying feedback items from {args.file}...", file=sys.stderr)
    
    for c in comments:
        path = c.get('path')
        body = c.get('body', '')
        line = c.get('line')
        
        if not path:
            continue

        print(f"[{path}:{line}] {body[:60]}...", file=sys.stderr)
        # Rely on pytest for functional verification. 
        # Manual verification is still listed for transparency.
        print("  STATUS: PLEASE VERIFY WITH CORRESPONDING TEST CASE", file=sys.stderr)

def main():
    """Main entry point for pr_helper.py CLI."""
    parser = argparse.ArgumentParser(description='Unified PR Review Cycle Helper')
    parser.add_argument('--owner', help='GitHub repository owner')
    parser.add_argument('--repo', help='GitHub repository name')
    subparsers = parser.add_subparsers(dest='command', help='Sub-commands')

    # Trigger
    p_trigger = subparsers.add_parser('trigger', help='Trigger new reviews from bots')
    p_trigger.add_argument('pr_number', type=int)

    # Fetch
    p_fetch = subparsers.add_parser('fetch', help='One-shot fetch of new feedback')
    p_fetch.add_argument('pr_number', type=int)
    p_fetch.add_argument('--since', default="1970-01-01T00:00:00Z", help='ISO 8601 timestamp')
    p_fetch.add_argument('--output', help='File path to write JSON results')

    # Monitor
    p_monitor = subparsers.add_parser('monitor', help='Poll for new feedback until timeout')
    p_monitor.add_argument('pr_number', type=int)
    p_monitor.add_argument('--since', default="1970-01-01T00:00:00Z", help='ISO 8601 timestamp to filter new feedback from')
    p_monitor.add_argument('--timeout', type=int, default=1200)
    p_monitor.add_argument('--initial-wait', type=int, default=180)
    p_monitor.add_argument('--interval', type=int, default=120)
    p_monitor.add_argument('--output', help='File path to write JSON results')

    # Verify
    p_verify = subparsers.add_parser('verify', help='Heuristic verification of local fixes')
    p_verify.add_argument('file', help='JSON file containing comments (from fetch/monitor)')

    args = parser.parse_args()

    # Resolution Logic: Args -> Env Vars -> Auto-detection
    owner = args.owner or os.environ.get("GH_OWNER")
    repo = args.repo or os.environ.get("GH_REPO")

    if not owner or not repo:
        detected_owner, detected_repo = get_current_repo_context()
        owner = owner or detected_owner
        repo = repo or detected_repo

    args.owner = owner
    args.repo = repo

    if not args.owner or not args.repo:
        parser.error("Could not detect repository context and no --owner/--repo provided.\n"
                     "       Please run from within a git repository or set GH_OWNER/GH_REPO environment variables.")

    if args.command == 'trigger':
        cmd_trigger(args)
    elif args.command == 'fetch':
        cmd_fetch(args)
    elif args.command == 'monitor':
        cmd_monitor(args)
    elif args.command == 'verify':
        cmd_verify(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
