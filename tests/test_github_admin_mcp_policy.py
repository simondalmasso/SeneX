import unittest

from senex_admin_mcp.github_policy import (
    branch_protection_payload,
    protection_summary,
)


class GitHubAdminPolicyTests(unittest.TestCase):
    def test_policy_is_hardcoded_and_non_destructive(self):
        policy = branch_protection_payload()
        self.assertEqual(policy["required_status_checks"]["contexts"], ["canonical-ci"])
        self.assertTrue(policy["required_status_checks"]["strict"])
        self.assertTrue(policy["enforce_admins"])
        self.assertEqual(
            policy["required_pull_request_reviews"]["required_approving_review_count"],
            0,
        )
        self.assertFalse(policy["allow_force_pushes"])
        self.assertFalse(policy["allow_deletions"])
        self.assertIsNone(policy["restrictions"])

    def test_summary_requires_all_expected_guards(self):
        response = {
            "required_status_checks": {"strict": True, "contexts": ["canonical-ci"]},
            "required_pull_request_reviews": {"required_approving_review_count": 0},
            "enforce_admins": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        }
        self.assertTrue(protection_summary(response)["pass"])

    def test_summary_fails_if_force_push_is_allowed(self):
        response = {
            "required_status_checks": {"strict": True, "contexts": ["canonical-ci"]},
            "required_pull_request_reviews": {"required_approving_review_count": 0},
            "enforce_admins": {"enabled": True},
            "allow_force_pushes": {"enabled": True},
            "allow_deletions": {"enabled": False},
        }
        self.assertFalse(protection_summary(response)["pass"])


if __name__ == "__main__":
    unittest.main()
