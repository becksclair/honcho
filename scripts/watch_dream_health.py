#!/usr/bin/env python3
"""Summarize Honcho dream/deriver health without dumping private memory text."""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import Counter
from dataclasses import dataclass

DEFAULT_DERIVER_CONTAINER = "honcho-deriver-1"
DEFAULT_DB_CONTAINER = "honcho-database-1"
DEFAULT_DB_USER = "honcho"
DEFAULT_DB_NAME = "honcho"

HARD_PROBLEM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bad_request", re.compile(r"\bBadRequest(?:Error)?\b", re.IGNORECASE)),
    ("retry_error", re.compile(r"\bRetryError\b", re.IGNORECASE)),
    ("auth_error", re.compile(r"\bAuthentication(?:Error)?\b", re.IGNORECASE)),
    ("model_error", re.compile(r"\bModelError\b", re.IGNORECASE)),
    ("traceback", re.compile(r"\bTraceback\b", re.IGNORECASE)),
)

SOFT_PROBLEM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("qwen_or_alibaba", re.compile(r"\b(qwen|alibaba)\b", re.IGNORECASE)),
    (
        "incomplete_specialist_output",
        re.compile(
            r"\b("
            r"i still need|need to create|i should create|i will create|"
            r"unable to create|could not create|failed to create|ran out|"
            r"not enough iterations|continue|next step"
            r")\b",
            re.IGNORECASE,
        ),
    ),
)

EVENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("minimal_deriver_perf_blocks", "PERFORMANCE - minimal_deriver_"),
    ("dream_completed", "Dream completed:"),
    ("dream_deduction_started", "Running deduction specialist"),
    ("dream_induction_started", "Running induction specialist"),
    ("tool_loop_cap_warnings", "Tool execution loop reached max iterations"),
    ("possibly_incomplete_specialist_endings", "Deduction completed:"),
    ("possibly_incomplete_specialist_endings", "Induction completed:"),
)

SANITIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{8}[A-Z]\b"), "[SPANISH_TAX_ID]"),
    (
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        "[EMAIL]",
    ),
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,36}\b", re.IGNORECASE),
        "[UUID]",
    ),
    (re.compile(r"\b(sk|fk|pk|rk)-[A-Za-z0-9_-]{12,}\b"), "[TOKEN]"),
    (re.compile(r"(?i)\b(bearer|token|api[_-]?key)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [TOKEN]"),
)


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    returncode: int


def run_command(args: list[str]) -> CommandResult:
    completed = subprocess.run(
        args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    return CommandResult(stdout=completed.stdout, returncode=completed.returncode)


def psql(
    *,
    db_container: str,
    db_user: str,
    db_name: str,
    sql: str,
) -> CommandResult:
    return run_command(
        [
            "docker",
            "exec",
            db_container,
            "psql",
            "-U",
            db_user,
            "-d",
            db_name,
            "-Atc",
            sql,
        ]
    )


def docker_logs(*, container: str, since: str) -> CommandResult:
    return run_command(["docker", "logs", "--since", since, container])


def sanitize(line: str) -> str:
    safe = line.strip()
    for pattern, replacement in SANITIZERS:
        safe = pattern.sub(replacement, safe)
    if len(safe) > 220:
        return safe[:217] + "..."
    return safe


def summarize_logs(log_text: str) -> tuple[Counter[str], list[str], bool, bool]:
    counts: Counter[str] = Counter()
    snippets: list[str] = []
    has_hard_problem = False
    has_soft_problem = False

    for line in log_text.splitlines():
        matched = False
        for name, needle in EVENT_PATTERNS:
            if needle in line:
                counts[name] += 1
                matched = True
                if name == "tool_loop_cap_warnings":
                    has_soft_problem = True

        for name, pattern in HARD_PROBLEM_PATTERNS:
            if pattern.search(line):
                counts[name] += 1
                matched = True
                has_hard_problem = True

        for name, pattern in SOFT_PROBLEM_PATTERNS:
            if pattern.search(line):
                counts[name] += 1
                matched = True
                has_soft_problem = True

        if matched:
            snippets.append(sanitize(line))

    return counts, snippets, has_hard_problem, has_soft_problem


def print_section(title: str, lines: list[str], *, empty_text: str) -> None:
    print(f"== {title} ==")
    if lines:
        for line in lines:
            print(line)
    else:
        print(empty_text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="15m",
        help="docker logs --since value",
    )
    parser.add_argument(
        "--deriver-container",
        default=DEFAULT_DERIVER_CONTAINER,
    )
    parser.add_argument(
        "--db-container",
        default=DEFAULT_DB_CONTAINER,
    )
    parser.add_argument(
        "--db-user",
        default=DEFAULT_DB_USER,
    )
    parser.add_argument(
        "--db-name",
        default=DEFAULT_DB_NAME,
    )
    args = parser.parse_args()

    queue_sql = """
select task_type || ':pending=' || count(*) from queue where processed=false group by task_type order by task_type;
select task_type || ':processed=' || count(*) from queue where processed=true group by task_type order by task_type;
select task_type || ':errors=' || count(*) from queue where coalesce(error,'') <> '' group by task_type order by task_type;
select 'active=' || count(*) from active_queue_sessions;
select work_unit_key || ' age=' || (now() - last_updated) from active_queue_sessions order by last_updated;
"""
    queue_result = psql(
        db_container=args.db_container,
        db_user=args.db_user,
        db_name=args.db_name,
        sql=queue_sql,
    )
    queue_lines = queue_result.stdout.strip().splitlines()
    if queue_result.returncode != 0:
        queue_lines = [f"psql_error={sanitize(queue_result.stdout)}"]

    logs_result = docker_logs(container=args.deriver_container, since=args.since)
    if logs_result.returncode != 0:
        log_counts = Counter({"docker_logs_error": 1})
        snippets = [f"docker_logs_error={sanitize(logs_result.stdout)}"]
        has_hard_problem = True
        has_soft_problem = False
    else:
        log_counts, snippets, has_hard_problem, has_soft_problem = summarize_logs(
            logs_result.stdout
        )

    if queue_result.returncode != 0:
        has_hard_problem = True

    print_section("queue", queue_lines, empty_text="(no rows)")

    log_lines = [f"{key}={value}" for key, value in sorted(log_counts.items())]
    print_section("recent logs", log_lines, empty_text="(no matched events)")
    print_section("sanitized snippets", snippets, empty_text="(none)")

    print("== verdict ==")
    if has_hard_problem:
        print("problem")
        return 2
    if has_soft_problem:
        print("warn")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
