"""Pure helpers for deriver queue-item retry classification and backoff.

This module holds small, side-effect-free functions used by the deriver's
failure-handling path to decide whether a failed representation task should be
retried (with exponential backoff) or dead-lettered, and to render a durable
error string from a possibly-wrapped exception.

The functions here intentionally have no database, network, or configuration
dependencies so they can be unit-tested in isolation. Tuning values (max
attempts, backoff base/cap/jitter) live in ``src.config`` and are passed in by
the caller.
"""

import random
from collections.abc import Callable

import tenacity

__all__ = [
    "unwrap_retry_error",
    "format_error",
    "is_retryable",
    "compute_backoff",
]

# Substrings (matched case-insensitively against the unwrapped cause's string
# representation) that mark a failure as deterministic and configuration-shaped.
# Retrying these wastes money and only delays the inevitable dead-letter, so
# they are dead-lettered on the first failure. Keep this list conservative:
# only add a signature when the failure is clearly permanent for a given input.
_NON_RETRYABLE_SUBSTRINGS: tuple[str, ...] = (
    "does not support stop sequences",
    "unsupported codex tool_choice shape",
    "does not support provider params",
    "maximum token limit",
)


def unwrap_retry_error(exc: BaseException) -> BaseException:
    """Peel ``tenacity.RetryError`` wrappers down to the real cause.

    ``tenacity`` reports an exhausted retry loop as a :class:`tenacity.RetryError`
    whose ``last_attempt.exception()`` holds the underlying failure. That cause
    may itself be another ``RetryError`` (nested retry loops), so unwrap
    recursively until a non-``RetryError`` is reached.

    Args:
        exc: The exception to unwrap. May be any ``BaseException``.

    Returns:
        The innermost non-``RetryError`` cause. If ``exc`` is not a
        ``RetryError`` it is returned unchanged. If a ``RetryError`` carries no
        recoverable underlying exception (no ``last_attempt`` or its
        ``exception()`` is ``None``), the ``RetryError`` itself is returned.
    """
    current = exc
    while isinstance(current, tenacity.RetryError):
        last_attempt = getattr(current, "last_attempt", None)
        if last_attempt is None:
            return current
        if not last_attempt.done():
            return current
        try:
            cause = last_attempt.exception()
        except Exception:
            # A Future whose result is not yet available (or otherwise raises)
            # offers no better cause than the RetryError we already hold.
            return current
        if cause is None:
            return current
        current = cause
    return current


def format_error(exc: BaseException, *, limit: int = 2000) -> str:
    """Render a durable, human-readable error string for the real cause.

    Unwraps ``tenacity.RetryError`` wrappers first so the stored string names
    the actual failure (for example ``ValidationException: ...``) instead of the
    opaque ``RetryError[<Future ...>]`` outer message.

    Args:
        exc: The exception to format. May be wrapped in ``RetryError``.
        limit: Maximum length of the returned string. Longer strings are
            truncated to this many characters.

    Returns:
        A string of the form ``"{ClassName}: {message}"`` for the unwrapped
        cause, truncated to ``limit`` characters.
    """
    cause = unwrap_retry_error(exc)
    rendered = f"{type(cause).__name__}: {cause}"
    if limit >= 0 and len(rendered) > limit:
        return rendered[:limit]
    return rendered


def is_retryable(exc: BaseException) -> bool:
    """Classify whether a failure should be retried or dead-lettered on sight.

    Deterministic, configuration-shaped failures (identified by an explicit
    deny-list of message substrings) return ``False`` and are dead-lettered
    immediately. Everything else — including unknown errors and transient
    provider failures such as timeouts and rate limits — returns ``True`` and is
    retried. Defaulting unknowns to retryable is the safe choice for a
    durability feature: better to retry than to lose memory.

    The check is performed against the unwrapped cause's string representation
    and is case-insensitive.

    Args:
        exc: The exception to classify. May be wrapped in ``RetryError``.

    Returns:
        ``False`` if the unwrapped cause matches a known non-retryable
        signature, ``True`` otherwise.
    """
    cause = unwrap_retry_error(exc)
    message = str(cause).lower()
    return all(signature not in message for signature in _NON_RETRYABLE_SUBSTRINGS)


def compute_backoff(
    attempts: int,
    base_seconds: float,
    cap_seconds: float,
    jitter: float,
    *,
    rng: Callable[[], float] | None = None,
) -> float:
    """Compute the backoff delay before the next retry attempt.

    The base delay grows exponentially with the attempt count and is clamped to
    ``cap_seconds``, then scaled by a random jitter factor in
    ``[1 - jitter, 1 + jitter]`` to avoid thundering-herd retries.

    Args:
        attempts: The attempt number that just failed (1-based). Values below 1
            are treated as 1.
        base_seconds: The base delay for the first attempt, in seconds.
        cap_seconds: The maximum un-jittered delay, in seconds.
        jitter: The jitter fraction in ``[0, 1]``. ``0`` disables jitter.
        rng: A zero-argument callable returning a float in ``[0, 1)`` used to
            derive the jitter multiplier deterministically. Injectable so tests
            are deterministic; defaults to the module-level random source.

    Returns:
        The backoff delay in seconds. Never negative.
    """
    source = rng if rng is not None else random.random
    exponent = max(attempts, 1) - 1
    raw = base_seconds * (2**exponent)
    capped = min(raw, cap_seconds)
    # Map rng() in [0, 1) to a multiplier in [1 - jitter, 1 + jitter].
    multiplier = (1.0 - jitter) + source() * (2.0 * jitter)
    delay = capped * multiplier
    return max(delay, 0.0)
