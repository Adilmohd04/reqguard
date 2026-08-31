"""Terminal render of an analysis run. Owned by Track C.

Reference: docs/03-implementation-manual.md section 13.2 and Doc 1 section 16.

The block below is a specified interface, not decoration — ``Inconclusive``
prints even at zero, because hiding it when it is zero trains the reader not to
look for it when it isn't.
"""

from __future__ import annotations

from dataclasses import dataclass

from reqguard.models import Severity, Verdict

__all__ = ["Summary", "summarize", "render_terminal"]

_GAP_STATUSES = {"INSUFFICIENTLY_PROTECTED", "REPAIRED"}


@dataclass
class Summary:
    analyzed: int = 0
    protected: int = 0
    gaps: int = 0
    repaired: int = 0
    inconclusive: int = 0
    high_risk_gaps: int = 0
    medium_risk_gaps: int = 0
    low_risk_gaps: int = 0


def summarize(verdicts: list[Verdict]) -> Summary:
    s = Summary(analyzed=len(verdicts))
    for v in verdicts:
        if v.status == "PROTECTED":
            s.protected += 1
        elif v.status == "INCONCLUSIVE":
            s.inconclusive += 1
        elif v.status in _GAP_STATUSES:
            s.gaps += 1
            if v.status == "REPAIRED":
                s.repaired += 1
            sev = v.requirement.severity
            if sev is Severity.HIGH:
                s.high_risk_gaps += 1
            elif sev is Severity.MEDIUM:
                s.medium_risk_gaps += 1
            else:
                s.low_risk_gaps += 1
    return s


def render_terminal(verdicts: list[Verdict], report_dir: str = "./reqguard-report/") -> str:
    s = summarize(verdicts)
    lines = [
        "Analyzing requirements...       ✓  %d found" % s.analyzed,
        "Mapping implementation...       ✓",
        "Inspecting tests...             ✓",
        "Generating mutations...         ✓",
        "Running verification...         ✓  %d executed" % s.analyzed,
        "",
        f"Requirements analyzed: {s.analyzed}",
        f"Protected:             {s.protected}",
        f"Potential gaps:        {s.gaps}",
        f"Inconclusive:          {s.inconclusive}",
        "",
        # "severity", not "risk": severity is the requirement's own labeled
        # attribute (from analyze); ReqGuard proves the requirement is
        # unprotected, it does not independently assess real-world risk --
        # found via an outside review flagging "high-risk" as a stronger
        # claim than the evidence supports.
        f"High-severity gaps:    {s.high_risk_gaps}",
        f"Medium-severity gaps:  {s.medium_risk_gaps}",
    ]
    if s.repaired:
        lines.append(f"Repaired (test written): {s.repaired}")
    lines += ["", f"Report: {report_dir}"]
    return "\n".join(lines)
