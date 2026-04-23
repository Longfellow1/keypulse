"""
Tests for privacy detector patterns and desensitization.
Covers all secret formats that need to be redacted.
"""
import pytest
from keypulse.privacy.detectors import detect, has_sensitive_content
from keypulse.privacy.desensitizer import desensitize


class TestUUIDDetection:
    """Test UUID v4 pattern detection."""

    def test_detects_uuid_v4_lowercase(self):
        """Should detect UUID v4 in lowercase."""
        text = "API key is 93897140-abe8-450d-bdf8-cf2218b3715d"
        detections = detect(text)
        assert len(detections) == 1
        assert detections[0].pattern_name == "uuid_v4"

    def test_detects_uuid_v4_uppercase(self):
        """Should detect UUID v4 in uppercase."""
        text = "KEY: 93897140-ABE8-450D-BDF8-CF2218B3715D"
        detections = detect(text)
        assert len(detections) == 1
        assert detections[0].pattern_name == "uuid_v4"

    def test_does_not_detect_short_hex(self):
        """Should not detect incomplete hex strings as UUID."""
        text = "hash: 93897140-abe8-450d"
        detections = detect(text)
        assert len(detections) == 0

    def test_desensitize_removes_uuid(self):
        """Should replace UUID with [REDACTED]."""
        text = "Key: 93897140-abe8-450d-bdf8-cf2218b3715d secret"
        result = desensitize(text)
        assert "[REDACTED]" in result
        assert "93897140" not in result


class TestGitHubPATDetection:
    """Test GitHub Personal Access Token patterns."""

    def test_detects_ghp_classic_pat(self):
        """Should detect classic ghp_ PAT."""
        text = "token " + "ghp" + "_" + "rw1om51ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
        detections = detect(text)
        assert len(detections) >= 1
        assert any(d.pattern_name == "github_pat" for d in detections)

    def test_detects_github_pat_fine_grained(self):
        """Should detect fine-grained github_pat_ token."""
        text = "github" + "_" + "pat" + "_" + "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        detections = detect(text)
        assert len(detections) >= 1
        assert any(d.pattern_name == "github_pat" for d in detections)

    def test_desensitize_removes_ghp(self):
        """Should replace GitHub PAT with [REDACTED]."""
        text = "My token is " + "ghp" + "_" + "rw1om51ABCDEFGHIJKLMNOPQRSTUVWXYZabcd" + " ok"
        result = desensitize(text)
        assert "[REDACTED]" in result
        assert "ghp_" not in result
        assert "rw1om51" not in result


class TestOpenAIKeyDetection:
    """Test OpenAI/Anthropic API key patterns."""

    def test_detects_sk_anthropic_key(self):
        """Should detect sk-ant- anthropic key."""
        text = "key=sk-ant-abcdefghijklmnopqrst1234567890"
        detections = detect(text)
        assert len(detections) >= 1
        assert any(d.pattern_name == "openai_key" for d in detections)

    def test_detects_sk_openai_key(self):
        """Should detect sk- openai key."""
        text = "SK-aBcDeFgHiJkLmNoPqRsT12345678901234567890"
        detections = detect(text)
        assert len(detections) >= 1
        assert any(d.pattern_name == "openai_key" for d in detections)

    def test_desensitize_removes_sk_key(self):
        """Should replace sk- key with [REDACTED]."""
        text = "My key: sk-ant-abcdefghijklmnopqrst1234567890 end"
        result = desensitize(text)
        assert "[REDACTED]" in result
        assert "sk-" not in result


class TestSlackTokenDetection:
    """Test Slack token patterns."""

    def test_detects_slack_bot_token(self):
        """Should detect xoxb- slack bot token."""
        text = "bot token: xoxb-1234567890ABCDEFGHIJKLMNOPQRSTUVabcd"
        detections = detect(text)
        assert len(detections) >= 1
        # May be detected as api_key or slack_token, both are fine
        assert any(d.pattern_name in ("slack_token", "api_key") for d in detections)

    def test_detects_slack_user_token(self):
        """Should detect xoxp- slack user token."""
        text = "xoxp-123456789ABCDEFGHIJKLMNOP-QRST-UVWX"
        detections = detect(text)
        assert len(detections) >= 1
        assert any(d.pattern_name == "slack_token" for d in detections)

    def test_desensitize_removes_slack_token(self):
        """Should replace slack token with [REDACTED]."""
        text = "Token is xoxb-1234567890ABCDEFGHIJKLMNOPQRSTUVabcd and works"
        result = desensitize(text)
        assert "[REDACTED]" in result
        assert "xoxb-" not in result


class TestAWSKeyDetection:
    """Test AWS Access Key patterns."""

    def test_detects_aws_access_key(self):
        """Should detect AKIA AWS access key."""
        text = "AWS key " + "AKI" + "A1234567890ABCDEF" + " in use"
        detections = detect(text)
        assert len(detections) >= 1
        assert any(d.pattern_name == "aws_key" for d in detections)

    def test_desensitize_removes_aws_key(self):
        """Should replace AWS key with [REDACTED]."""
        text = "Access key: " + "AKI" + "A1234567890ABCDEF" + " secret"
        result = desensitize(text)
        assert "[REDACTED]" in result
        assert "AKIA" not in result


class TestJWTDetection:
    """Test JWT token pattern detection."""

    def test_detects_jwt_token(self):
        """Should detect JWT tokens."""
        text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        detections = detect(text)
        # JWT may also match other patterns
        assert len(detections) >= 1

    def test_desensitize_removes_jwt(self):
        """Should replace JWT with [REDACTED]."""
        text = "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = desensitize(text)
        assert "[REDACTED]" in result
        assert "eyJ" not in result


class TestMultipleSecrets:
    """Test detection and desensitization of multiple secrets."""

    def test_detects_multiple_secrets_in_text(self):
        """Should detect multiple different secret types."""
        text = "UUID: 93897140-abe8-450d-bdf8-cf2218b3715d and token " + "ghp" + "_" + "rw1om51ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
        detections = detect(text)
        assert len(detections) >= 2

    def test_desensitize_multiple_secrets(self):
        """Should redact all secret types."""
        text = "Key 93897140-abe8-450d-bdf8-cf2218b3715d and token " + "ghp" + "_" + "rw1om51ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        result = desensitize(text)
        assert "93897140" not in result
        assert "ghp_" not in result
        assert result.count("[REDACTED]") >= 2


class TestFalsePositivesAndEdgeCases:
    """Test that legitimate content is not mistakenly redacted."""

    def test_uuid_not_detected_in_commit_hash(self):
        """UUIDs should not match incomplete hex strings (edge case)."""
        # Commit hash is only 40 chars, not a valid UUID
        text = "Commit: abc123def456ghi789jkl012mno345pqr678"
        detections = detect(text)
        # This should not match UUID pattern (incorrect format)
        uuid_detections = [d for d in detections if d.pattern_name == "uuid_v4"]
        assert len(uuid_detections) == 0

    def test_normal_hex_not_flagged_as_secret(self):
        """Normal 40-char hex strings without context should not be redacted."""
        text = "Hash: 356a192b7913b04c54574d18c28d46e6395428ab"
        detections = detect(text)
        # This is a normal SHA1 hash, not a secret - should not match any secret patterns
        # Only email/phone/id_card/bank_card patterns should match, not token patterns
        secret_detections = [d for d in detections if "token" in d.pattern_name or "key" in d.pattern_name]
        assert len(secret_detections) == 0

    def test_normal_email_is_preserved_by_default(self):
        """Email detection uses separate redact_emails flag."""
        text = "Contact: user@example.com"
        result = desensitize(text, redact_emails=False)
        assert "user@example.com" in result

    def test_has_sensitive_content_returns_bool(self):
        """has_sensitive_content should return bool."""
        assert has_sensitive_content("key 93897140-abe8-450d-bdf8-cf2218b3715d") is True
        assert has_sensitive_content("just normal text") is False


class TestDesensitizeWithFlags:
    """Test desensitize with different redaction flags."""

    def test_redact_tokens_false_preserves_secrets(self):
        """When redact_tokens=False, token patterns should not be redacted."""
        text = "Token: " + "ghp" + "_" + "rw1om51ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
        result = desensitize(text, redact_tokens=False)
        assert "ghp_" in result

    def test_redact_tokens_true_redacts_all_token_patterns(self):
        """When redact_tokens=True, all token patterns should be redacted."""
        text = ("ghp" + "_" + "rw1om51ABCDEFGHIJKLMNOPQRSTUVWXYZabcd" + " " +
                "xox" + "b" + "-" + "1234567890ABCDEFGHIJKLMNOPQRSTUVabcd")
        result = desensitize(text, redact_tokens=True)
        assert "ghp_" not in result
        assert "xoxb-" not in result
        assert "[REDACTED]" in result
