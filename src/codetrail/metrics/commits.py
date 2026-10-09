"""Commits that explain why (design section 18.1).

A commit explains why when its body matches the target's `commit_why_pattern`; otherwise it has a body without a why,
or only a subject. A message gitleaks withheld is counted apart and left out of the share.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from codetrail.repo.history import WITHHELD_MESSAGE, Commit


@dataclass(frozen=True)
class CommitMetric:
    explains_why: int
    body_only: int
    subject_only: int
    withheld: int
    without_why: list[Commit]  # in the order given: newest first

    @property
    def measured(self) -> int:
        """The share's denominator: every commit but the withheld ones."""
        return self.explains_why + self.body_only + self.subject_only


def measure_commits(commits: Iterable[Commit], why: re.Pattern[str]) -> CommitMetric:
    explains_why = body_only = subject_only = withheld = 0
    without_why = []
    for commit in commits:
        if commit.subject == WITHHELD_MESSAGE:
            withheld += 1
        elif why.search(commit.body):
            explains_why += 1
        else:
            without_why.append(commit)
            if commit.body:
                body_only += 1
            else:
                subject_only += 1
    return CommitMetric(explains_why, body_only, subject_only, withheld, without_why)
