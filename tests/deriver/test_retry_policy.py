"""Unit tests for :mod:`src.deriver.retry_policy`.

These tests exercise pure functions with no database or network dependencies,
so they run without the compose stack. Backoff jitter is made deterministic via
an injected ``rng`` callable.
"""

import tenacity

from src.deriver.retry_policy import (
    compute_backoff,
    format_error,
    is_retryable,
    unwrap_retry_error,
)


def _make_retry_error(
    cause: BaseException, attempt_number: int = 1
) -> tenacity.RetryError:
    """Wrap ``cause`` in a ``tenacity.RetryError`` the way tenacity does."""
    future = tenacity.Future(attempt_number=attempt_number)
    future.set_exception(cause)
    return tenacity.RetryError(future)


class TestUnwrapRetryError:
    def test_non_retry_error_passthrough(self):
        exc = ValueError("plain error")
        assert unwrap_retry_error(exc) is exc

    def test_single_retry_error_unwrapped(self):
        cause = RuntimeError("real cause")
        wrapped = _make_retry_error(cause)
        assert unwrap_retry_error(wrapped) is cause

    def test_nested_retry_error_unwrapped(self):
        cause = KeyError("innermost")
        inner = _make_retry_error(cause)
        outer = _make_retry_error(inner)
        assert unwrap_retry_error(outer) is cause

    def test_deeply_nested_retry_error(self):
        cause = ValueError("bottom")
        level = _make_retry_error(cause)
        for _ in range(5):
            level = _make_retry_error(level)
        assert unwrap_retry_error(level) is cause

    def test_retry_error_without_exception_returns_self(self):
        # A Future that never had an exception set: exception() would raise, so
        # unwrap must fall back to the RetryError itself rather than blowing up.
        future = tenacity.Future(attempt_number=1)
        wrapped = tenacity.RetryError(future)
        assert unwrap_retry_error(wrapped) is wrapped


class TestFormatError:
    def test_formats_class_and_message(self):
        exc = ValueError("boom")
        assert format_error(exc) == "ValueError: boom"

    def test_unwraps_before_formatting(self):
        cause = RuntimeError("the real problem")
        wrapped = _make_retry_error(cause)
        # Must name the underlying cause, not the opaque RetryError wrapper.
        result = format_error(wrapped)
        assert result == "RuntimeError: the real problem"
        assert "RetryError" not in result

    def test_truncates_to_limit(self):
        exc = ValueError("x" * 5000)
        result = format_error(exc, limit=100)
        assert len(result) == 100

    def test_no_truncation_when_under_limit(self):
        exc = ValueError("short")
        result = format_error(exc, limit=2000)
        assert result == "ValueError: short"

    def test_default_limit(self):
        exc = ValueError("y" * 10000)
        result = format_error(exc)
        assert len(result) == 2000


class TestIsRetryable:
    def test_codex_stop_sequences_not_retryable(self):
        exc = ValueError("codex transport does not support stop sequences")
        assert is_retryable(exc) is False

    def test_codex_tool_choice_shape_not_retryable(self):
        exc = ValueError("Unsupported Codex tool_choice shape")
        assert is_retryable(exc) is False

    def test_codex_provider_params_not_retryable(self):
        exc = ValueError("codex transport does not support provider params: foo")
        assert is_retryable(exc) is False

    def test_maximum_token_limit_not_retryable(self):
        exc = ValueError(
            "Observation content exceeds maximum token limit of 2000 tokens"
        )
        assert is_retryable(exc) is False

    def test_case_insensitive_match(self):
        exc = ValueError("CODEX TRANSPORT DOES NOT SUPPORT STOP SEQUENCES")
        assert is_retryable(exc) is False

    def test_non_retryable_signature_when_wrapped_in_retry_error(self):
        cause = ValueError("Unsupported Codex tool_choice shape")
        wrapped = _make_retry_error(cause)
        assert is_retryable(wrapped) is False

    def test_rate_limit_is_retryable(self):
        exc = RuntimeError("429 Too Many Requests")
        assert is_retryable(exc) is True

    def test_timeout_is_retryable(self):
        exc = TimeoutError("request timed out after 30s")
        assert is_retryable(exc) is True

    def test_generic_error_is_retryable(self):
        exc = Exception("something unexpected happened")
        assert is_retryable(exc) is True

    def test_transient_error_wrapped_in_retry_error_is_retryable(self):
        cause = RuntimeError("502 Bad Gateway from provider")
        wrapped = _make_retry_error(cause)
        assert is_retryable(wrapped) is True

    def test_empty_message_is_retryable(self):
        exc = Exception()
        assert is_retryable(exc) is True


class TestComputeBackoff:
    def test_exponential_growth_no_jitter(self):
        # jitter=0 => multiplier is exactly 1.0 regardless of rng.
        assert compute_backoff(1, 30.0, 7200.0, 0.0, rng=lambda: 0.0) == 30.0
        assert compute_backoff(2, 30.0, 7200.0, 0.0, rng=lambda: 0.0) == 60.0
        assert compute_backoff(3, 30.0, 7200.0, 0.0, rng=lambda: 0.0) == 120.0
        assert compute_backoff(4, 30.0, 7200.0, 0.0, rng=lambda: 0.0) == 240.0

    def test_cap_applied(self):
        # 30 * 2**19 would be huge; cap clamps it.
        result = compute_backoff(20, 30.0, 7200.0, 0.0, rng=lambda: 0.0)
        assert result == 7200.0

    def test_attempts_below_one_treated_as_one(self):
        assert compute_backoff(0, 30.0, 7200.0, 0.0, rng=lambda: 0.0) == 30.0

    def test_jitter_lower_bound(self):
        # rng() == 0.0 => multiplier == 1 - jitter.
        result = compute_backoff(1, 100.0, 7200.0, 0.2, rng=lambda: 0.0)
        assert result == 80.0

    def test_jitter_upper_bound(self):
        # rng() approaching 1.0 => multiplier approaches 1 + jitter.
        result = compute_backoff(1, 100.0, 7200.0, 0.2, rng=lambda: 1.0)
        assert abs(result - 120.0) < 1e-9

    def test_jitter_midpoint(self):
        # rng() == 0.5 => multiplier == 1.0 exactly.
        result = compute_backoff(1, 100.0, 7200.0, 0.2, rng=lambda: 0.5)
        assert result == 100.0

    def test_jitter_bounds_across_many_samples(self):
        # With the default (real) rng, results must stay within the jitter band.
        base, cap, jitter = 30.0, 7200.0, 0.2
        for attempts in range(1, 8):
            expected_center = min(base * 2 ** (attempts - 1), cap)
            for _ in range(50):
                result = compute_backoff(attempts, base, cap, jitter)
                assert expected_center * (1 - jitter) - 1e-9 <= result
                assert result <= expected_center * (1 + jitter) + 1e-9

    def test_never_negative(self):
        result = compute_backoff(1, 30.0, 7200.0, 1.0, rng=lambda: 0.0)
        assert result >= 0.0
