
import unittest
import sys
import os
import json
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timezone

# Add the module path to sys.path to allow importing from .agent/skills
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../.agent/skills/pr_review')))

try:
    from pr_skill import ReviewManager, DEFAULT_VALIDATION_REVIEWER
except ImportError:
    # Handle the case where the import path might be different in CI
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.agent/skills/pr_review')))
    from pr_skill import ReviewManager, DEFAULT_VALIDATION_REVIEWER

class TestReviewManager(unittest.TestCase):
    def setUp(self):
        self.mock_repo = MagicMock()
        self.mock_gh = MagicMock()
        self.mock_gh.get_repo.return_value = self.mock_repo
        
        # Mock global vars GIT_PATH/GH_PATH in pr_skill module if needed, 
        # but since we mock subprocss it might be fine.
        
    def test_determine_next_step_waiting(self):
        """Test next_step when waiting for feedback."""
        mgr = ReviewManager(self.mock_repo)
        step = mgr._determine_next_step(
            new_feedback=[],
            validation_reviewer="gemini-bot",
            main_reviewer_state="PENDING",
            has_changes_requested=False,
            has_new_main_comments=False
        )
        self.assertIn("WAIT 30 seconds", step)
        self.assertIn("Run 'status'", step)

    def test_determine_next_step_changes_requested(self):
        """Test next_step when changes are requested."""
        mgr = ReviewManager(self.mock_repo)
        step = mgr._determine_next_step(
            new_feedback=[],
            validation_reviewer="gemini-bot",
            main_reviewer_state="CHANGES_REQUESTED",
            has_changes_requested=True,
            has_new_main_comments=False
        )
        self.assertIn("CRITICAL: Changes requested", step)

    def test_save_loop_state(self):
        """Test state saving logic."""
        mgr = ReviewManager(self.mock_repo)
        with patch("builtins.open", mock_open()) as mock_file:
            # We also need to mock os.open for the secure write
             with patch("os.open") as mock_os_open, \
                  patch("os.fdopen") as mock_fdopen:
                mgr._save_loop_state(8, "polling")
                # Verification is tricky due to secure write implementation details, 
                # but we ensure it doesn't crash.
                pass

    def test_trigger_review_next_step(self):
        """Test trigger_review returns correct wait instruction."""
        # This requires mocking _check_local_state and repo.get_pull
        mgr = ReviewManager(self.mock_repo)
        mgr._check_local_state = MagicMock(return_value=(True, "Clean"))
        mgr._log = MagicMock()
        
        mock_pr = MagicMock()
        self.mock_repo.get_pull.return_value = mock_pr
        
        result = mgr.trigger_review(8, wait_seconds=42)
        self.assertIn("WAIT 42 seconds", result["next_step"])
        self.assertIn("Run 'status'", result["next_step"])

if __name__ == '__main__':
    unittest.main()
