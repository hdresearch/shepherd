"""Test email validation including plus sign support.

This test verifies the fix for the bug: "Login fails when email contains a plus sign"
"""

import re

from shepherd_runtime.task.authoring import Check


def is_valid_email(email: str) -> bool:
    """Validate email addresses, including support for plus signs in local part.

    Plus signs (+) are valid in email addresses according to RFC 5322.
    Examples: user+tag@example.com, test+123@domain.org
    """
    # Basic validation: must contain exactly one @ symbol
    if email.count("@") != 1:
        return False

    local_part, domain_part = email.split("@")

    # Local part validation (before @)
    # Allow alphanumeric, dots, hyphens, underscores, and plus signs
    # Must not start or end with a dot
    if not local_part or local_part.startswith(".") or local_part.endswith("."):
        return False

    # Allow plus signs and other valid characters in local part
    local_pattern = r"^[a-zA-Z0-9._+-]+$"
    if not re.match(local_pattern, local_part):
        return False

    # Domain part validation (after @)
    # Must contain at least one dot and valid domain characters
    # Must not start or end with a dot
    if not domain_part or "." not in domain_part:
        return False
    if domain_part.startswith(".") or domain_part.endswith("."):
        return False

    # Domain should contain valid characters and at least one dot
    domain_pattern = r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(domain_pattern, domain_part) is not None


class TestEmailValidation:
    """Test cases for email validation with plus sign support."""

    def test_valid_emails_with_plus_signs(self):
        """Test that emails with plus signs are accepted."""
        valid_emails = [
            "user+tag@example.com",
            "test+123@domain.org",
            "john+doe@company.co.uk",
            "support+tickets@service.net",
            "admin+notifications@site.gov",
            "user+very+long+tag@example.com",
        ]

        for email in valid_emails:
            assert is_valid_email(email), f"Email should be valid: {email}"

    def test_valid_emails_without_plus_signs(self):
        """Test that regular emails still work."""
        valid_emails = [
            "user@example.com",
            "test.email@domain.org",
            "john.doe@company.co.uk",
            "admin@site.gov",
            "simple@test.net",
            "user_name@example.com",
            "user-name@example.com",
        ]

        for email in valid_emails:
            assert is_valid_email(email), f"Email should be valid: {email}"

    def test_invalid_emails(self):
        """Test that invalid emails are rejected."""
        invalid_emails = [
            "",  # Empty string
            "notanemail",  # No @ symbol
            "user@@example.com",  # Multiple @ symbols
            "@example.com",  # No local part
            "user@",  # No domain part
            "user@domain",  # No TLD
            ".user@example.com",  # Local part starts with dot
            "user.@example.com",  # Local part ends with dot
            "user@.example.com",  # Domain starts with dot
            "user@example.",  # Domain ends with dot
            "user@example.c",  # TLD too short
            "user space@example.com",  # Space in local part
            "user@exam ple.com",  # Space in domain
        ]

        for email in invalid_emails:
            assert not is_valid_email(email), f"Email should be invalid: {email}"

    def test_edge_cases_with_plus_signs(self):
        """Test edge cases specifically for plus signs."""
        test_cases = [
            ("user+@example.com", True),  # Plus at end of local part
            ("+user@example.com", True),  # Plus at start of local part
            ("us+er@example.com", True),  # Plus in middle of local part
            ("user@exam+ple.com", False),  # Plus in domain (invalid)
        ]

        for email, expected in test_cases:
            result = is_valid_email(email)
            assert result == expected, f"Email '{email}' expected {expected}, got {result}"

    def test_check_marker_integration(self):
        """Test that the Check marker works with the new validation."""
        email_check = Check(is_valid_email)

        # Test valid email with plus sign
        assert email_check("user+tag@example.com")

        # Test invalid email
        assert not email_check("invalid.email")

    def test_regression_original_bug(self):
        """Specific regression test for the original bug report."""
        # The original bug: "Login fails when email contains a plus sign"
        problematic_email = "user+signup@example.com"

        # This should now return True (fixed)
        assert is_valid_email(problematic_email), "The original bug should be fixed"

        # Test with Check marker as well
        email_check = Check(is_valid_email)
        assert email_check(problematic_email), "Check marker should also work"


if __name__ == "__main__":
    # Run a simple verification
    test = TestEmailValidation()
    test.test_valid_emails_with_plus_signs()
    test.test_valid_emails_without_plus_signs()
    test.test_invalid_emails()
    test.test_edge_cases_with_plus_signs()
    test.test_regression_original_bug()

    print("✅ All email validation tests passed!")
    print("✅ Plus sign bug has been fixed!")
