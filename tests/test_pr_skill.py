import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure the module can be imported
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".agent", "skills",
                     "pr_review")),
)

import pr_skill  # noqa: E402
from pr_skill import ReviewManager  # noqa: E402


class TestPRReviewSkill(unittest.TestCase):

    @patch("pr_skill.subprocess.run")
    @patch("pr_skill.Auth.Token")
    @patch("pr_skill.Github")
    def test_offline_mode_success(self, mock_github, mock_auth, mock_run):
        """Test trigger_review in offline mode."""
        # Setup mocks
        mock_run.return_value = MagicMock(returncode=0,
                                          stdout="Mock feedback",
                                          stderr="")

        mgr = ReviewManager(local=False, offline=True, verbose=False)
        result = mgr.trigger_review(pr_number=None,
                                    wait_seconds=0,
                                    model="gemini-2.5-pro")

        self.assertIsNotNone(result)
        self.assertEqual(result.get("status"), "success")
        self.assertIn("Offline review completed locally",
                      result.get("message"))
        self.assertEqual(result["initial_status"]["new_item_count"], 1)
        self.assertEqual(result["initial_status"]["items"][0]["body"],
                         "Mock feedback")

    @patch("pr_skill.ReviewManager._detect_repo")
    @patch("pr_skill.subprocess.run")
    @patch("pr_skill.Auth.Token")
    @patch("pr_skill.Github")
    @patch("pr_skill.time.sleep")
    def test_local_mode_success(self, mock_sleep, mock_github, mock_auth,
                                mock_run, mock_detect_repo):
        """Test trigger_review in local mode."""
        # Setup mock for get_pull
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo
        mock_detect_repo.return_value = mock_repo

        # Setup run mocks for both git commands and local reviewer
        def run_side_effect(*args, **kwargs):
            mock_res = MagicMock()
            mock_res.returncode = 0
            if "git" in args[0]:
                mock_res.stdout = "" if "status" in args[0] else "main"
                if "rev-list" in args[0]:
                    mock_res.stdout = "0 0"
            else:
                mock_res.stdout = "Mock local feedback"
            return mock_res

        mock_run.side_effect = run_side_effect

        os.environ["GITHUB_TOKEN"] = "fake_token"
        mgr = ReviewManager(local=True, offline=False, verbose=False)
        mgr.g = mock_github.return_value
        mgr.repo = mock_repo

        # Setup mock status_data from check_status
        mgr.check_status = MagicMock(return_value={
            "status": "success",
            "items": [],
            "next_step": "Fix"
        })

        result = mgr.trigger_review(pr_number=123,
                                    wait_seconds=0,
                                    model="gemini-2.5-pro")

        self.assertIsNotNone(result)
        self.assertEqual(result.get("status"), "success")
        self.assertIn("Triggered local review", result.get("message"))
        self.assertTrue(mgr.check_status.called)

    @patch("pr_skill.ReviewManager._detect_repo")
    @patch("pr_skill.subprocess.run")
    @patch("pr_skill.Auth.Token")
    @patch("pr_skill.Github")
    def test_online_mode_trigger(self, mock_github, mock_auth, mock_run,
                                 mock_detect_repo):
        """Test trigger_review in online mode pushes and posts comments."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo
        mock_detect_repo.return_value = mock_repo
        mock_pr = MagicMock()
        mock_repo.get_pull.return_value = mock_pr

        def run_side_effect(*args, **kwargs):
            mock_res = MagicMock(
                returncode=0, stdout="0 0\n" if "rev-list" in args[0] else "")
            return mock_res

        mock_run.side_effect = run_side_effect
        os.environ["GITHUB_TOKEN"] = "fake_token"

        mgr = ReviewManager(local=False, offline=False, verbose=False)
        mgr.g = mock_github.return_value
        mgr.repo = mock_repo
        mgr._poll_for_main_reviewer = MagicMock(
            return_value={"status": "success"})

        result = mgr.trigger_review(123, wait_seconds=10)

        self.assertIsNotNone(result)
        self.assertEqual(result.get("status"), "success")
        self.assertTrue(mock_pr.create_issue_comment.called)
        self.assertTrue(mgr._poll_for_main_reviewer.called)


if __name__ == "__main__":
    unittest.main()
