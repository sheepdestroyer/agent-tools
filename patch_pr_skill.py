import re

with open(".agent/skills/pr_review/pr_skill.py", "r") as f:
    content = f.read()

# 1. Update __init__
content = re.sub(
    r"def __init__\(self, local=False\):\n\s+self\.local = local",
    "def __init__(self, local=False, offline=False):\n        self.local = local\n        self.offline = offline",
    content
)
content = re.sub(
    r"        if not self\.token:\n\s+# Fallback to gh CLI",
    r"        if not self.token and not self.offline:\n            # Fallback to gh CLI",
    content
)
content = re.sub(
    r"        try:\n\s+self\.g = Github\(auth=Auth\.Token\(self\.token\)\)\n\s+self\.repo = self\._detect_repo\(\)\n\s+self\._ensure_workspace\(\)",
    r"        try:\n            if not self.offline:\n                self.g = Github(auth=Auth.Token(self.token))\n                self.repo = self._detect_repo()\n            else:\n                self.repo = None\n            self._ensure_workspace()",
    content
)

# 2. Extract and replace trigger_review
start_marker = "    def trigger_review("
end_marker = "    def check_status("

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

new_trigger_review = """    def trigger_review(
        self,
        pr_number,
        wait_seconds=180,
        validation_reviewer=DEFAULT_VALIDATION_REVIEWER,
        model="gemini-3.1-pro-preview",
    ):
        \"\"\"
        1. Checks local state (Hard Constraint).
        2. Post comments to trigger bots or run local/offline reviewer.
        3. Polls for main reviewer feedback.
        \"\"\"
        local = self.local
        offline = self.offline
        # Step 1: Enforce Push
        if not offline:
            is_safe, msg = self._check_local_state()
            if not is_safe:
                print_error(
                    f"FAILED: {msg}\\nTip: Use the 'safe_push' tool or run 'git push' manually."
                )
            self._log(f"State verified: {msg}")
        else:
            self._log("Offline mode: Local state clean. Skipping remote state checks.")

        # Capture start time for status check
        start_time = datetime.now(timezone.utc)

        # Step 2: Trigger Bots or Run Local/Offline Reviewer
        triggered_bots = []
        local_review_item = None

        if local or offline:
            pr_label = f" PR #{pr_number}" if pr_number else " local changes"
            self._log(f"Triggering {'local' if local else 'offline'} review for{pr_label}...")
            settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
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
                settings.get("local_model", model),
                "--prompt",
                "/code-review",
            ]
            self._log(f"  Running {'local' if local else 'offline'} reviewer: {cmd}")
            try:
                res = subprocess.run(cmd,
                                     check=True,
                                     capture_output=True,
                                     text=True,
                                     timeout=600)
                clean_stdout = re.sub(
                    r"\\x1B(?:[@-Z\\\\-_]|\\[[0-?]*[ -/]*[@-~])", "",
                    res.stdout)
                self._log(
                    f"{'Local' if local else 'Offline'} Review Feedback:\\n{clean_stdout[:1000]}{'...' if len(clean_stdout) > 1000 else ''}"
                )
                if res.stderr:
                    self._log(
                        f"{'Local' if local else 'Offline'} Review Warnings/Errors:\\n{self._mask_token(res.stderr)}"
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
                        "status": "success",
                        "message": "Offline review completed locally.",
                        "triggered_bots": triggered_bots,
                        "initial_status": {
                            "status": "success",
                            "pr_number": pr_number,
                            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                            "new_item_count": 1,
                            "items": [local_review_item],
                            "main_reviewer": {
                                "user": "gemini-cli-review",
                                "state": "COMMENTED",
                            },
                        },
                        "next_step": "Analyze the feedback in 'initial_status.items' and implement fixes. Proceed directly to Step 4 (Analyze & Implement).",
                    }
            except subprocess.TimeoutExpired as e:
                print_error(f"{'Local' if local else 'Offline'} reviewer timed out after {e.timeout}s.")
            except FileNotFoundError as e:
                print_error(f"{'Local' if local else 'Offline'} reviewer executable not found. Ensure npx/gemini-cli is installed. Error: {e}")
            except subprocess.CalledProcessError as e:
                print_error(f"{'Local' if local else 'Offline'} reviewer failed:\\nSTDERR: {self._mask_token(e.stderr)}\\nSTDOUT: {self._mask_token(e.stdout)}")
        else:
            try:
                pr = self.repo.get_pull(pr_number)
                self._log(f"Triggering reviews on PR #{pr_number} ({pr.title})...")
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
            self._log(f"Auto-waiting {wait_time} seconds for independent online human/bot comments...")
            try:
                time.sleep(wait_time)
            except KeyboardInterrupt:
                self._log("\\nWait interrupted. Checking status immediately...")
            
            self._log("-" * 40)
            self._log("Fetching status from GitHub...")
            status_data = self.check_status(
                pr_number,
                since_iso=start_time.isoformat(),
                return_data=True,
                validation_reviewer=validation_reviewer,
            )
            
            if local_review_item:
                status_data["items"].insert(0, local_review_item)
                status_data["new_item_count"] = len(status_data["items"])
                status_data["main_reviewer"] = {
                    "user": "gemini-cli-review",
                    "state": "COMMENTED"
                }
                status_data["next_step"] = f"New feedback received. {ACTION_INSTRUCTIONS}"
            
            message = "Triggered local review and fetched online comments."
            
            return {
                "status": "success",
                "message": message,
                "triggered_bots": triggered_bots,
                "initial_status": status_data,
                "next_step": status_data.get("next_step", "Analyze feedback and implement fixes."),
            }
            
        else:
            if wait_seconds > 0:
                self._log("-" * 40)
                self._log(f"Auto-waiting {wait_seconds} seconds for initial bot responses...")
                try:
                    time.sleep(wait_seconds)
                except KeyboardInterrupt:
                    self._log("\\nWait interrupted. Checking status immediately...")

                self._log("-" * 40)
                self._log("Polling for main reviewer feedback (enforcing Loop Rule)...")

                status_data = self._poll_for_main_reviewer(
                    pr_number=pr_number,
                    since_iso=start_time.isoformat(),
                    validation_reviewer=validation_reviewer,
                )
            else:
                status_data = {
                    "status": "skipped",
                    "message": "Initial status check skipped due to wait_seconds=0.",
                }

            message = "Triggered reviews."
            if wait_seconds > 0 and not status_data.get("polling_interrupted"):
                message = "Triggered reviews and polled for main reviewer feedback."
            elif status_data.get("polling_interrupted"):
                message = "Triggered reviews; polling was interrupted."
            elif wait_seconds <= 0:
                message = "Triggered reviews; polling was skipped."

            return {
                "status": "success",
                "message": message,
                "triggered_bots": triggered_bots,
                "initial_status": status_data,
                "next_step": status_data.get(
                    "next_step",
                    "Run 'status' to check for feedback. Be autonomous, don't stop the cycle. You must regularly poll for the main-reviewer's review until it eventually gets posted, then continue.",
                ),
            }

"""
content = content[:start_idx] + new_trigger_review + content[end_idx:]

# 3. Update main parser and logic
content = content.replace(
    'p_trigger.add_argument(\n        "--local",\n        action="store_true",\n        help="Run in local mode using only gemini-cli-review locally",\n    )',
    'p_trigger.add_argument(\n        "--local",\n        action="store_true",\n        help="Run in local mode (runs gemini-cli-review, waits 120s for GitHub comments)",\n    )\n    p_trigger.add_argument(\n        "--offline",\n        action="store_true",\n        help="Run completely offline without pushing to GitHub. Only runs gemini-cli-review locally.",\n    )'
)

content = content.replace(
    'local_mode = getattr(args, "local", False)\n        mgr = ReviewManager(local=local_mode)',
    'local_mode = getattr(args, "local", False)\n        offline_mode = getattr(args, "offline", False)\n        if local_mode and offline_mode:\n            parser.error("Cannot specify both --local and --offline")\n        mgr = ReviewManager(local=local_mode, offline=offline_mode)'
)

content = content.replace(
    'if not local_mode and (args.pr_number is None\n                                   or args.pr_number <= 0):\n                parser.error(\n                    "pr_number is required unless --local is specified")',
    'if not local_mode and not offline_mode and (args.pr_number is None or args.pr_number <= 0):\n                parser.error("pr_number is required unless --local or --offline is specified")'
)

with open(".agent/skills/pr_review/pr_skill.py", "w") as f:
    f.write(content)
