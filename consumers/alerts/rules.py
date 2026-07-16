"""Tier 1 — OWASP signature engine.

Signature-based detection, deliberately narrow: it catches known, literal
attack shapes (the same approach a basic WAF ruleset takes) and shares that
approach's well-known limitation — a determined attacker can obfuscate a
payload (alternate encodings, case tricks, comment-splitting inside a
keyword) past a naive regex. That limitation is the entire reason this tier
is PAIRED with the statistical tier, not relied on alone; stating it plainly
is more defensible than overselling the regex's coverage.

Every pattern here is simple and bounded on purpose. This engine runs on
attacker-controlled input by design, so a pattern with nested quantifiers
(e.g. `(a+)+`) would itself be a denial-of-service vector against the alert
consumer via catastrophic backtracking — patterns are kept anchored and
linear, and tested against adversarially long inputs, not just happy-path
attack strings.
"""

import re

SQLI_PATTERNS = [
    re.compile(r"\bUNION\b.{1,40}?\bSELECT\b", re.IGNORECASE),  # bounded gap, no backtracking blowup
    re.compile(r"\bOR\b\s+\d+\s*=\s*\d+", re.IGNORECASE),       # the classic OR 1=1 tautology
    re.compile(r"(--|#|/\*)\s*$"),                              # trailing comment truncating the query
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
]

XSS_PATTERNS = [
    re.compile(r"<script[\s>]", re.IGNORECASE),
    re.compile(r"on(error|load|click|mouseover)\s*=", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
]

PATH_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./"),
    re.compile(r"%2e%2e%2f", re.IGNORECASE),        # single URL-encoded ../
    re.compile(r"%252e%252e%252f", re.IGNORECASE),  # double-encoded — a naive literal check misses this
]

RULESETS: dict[str, list[re.Pattern]] = {
    "SQLI": SQLI_PATTERNS,
    "XSS": XSS_PATTERNS,
    "PATH_TRAVERSAL": PATH_TRAVERSAL_PATTERNS,
}


def scan_event(query_string: str, user_agent: str) -> list[str]:
    """Return the rule names matched by this event's fields.

    The two attacker-influenced fields are scanned SEPARATELY rather than
    concatenated (as the guide does): concatenating would let one field's
    trailing content defeat another's `$`-anchored pattern (the SQL trailing
    comment rule, for one), and separate scans keep each pattern's anchoring
    semantics intact. A rule fires if any of its patterns hits either field.
    """
    matched = []
    for rule_name, patterns in RULESETS.items():
        if any(p.search(query_string) or p.search(user_agent) for p in patterns):
            matched.append(rule_name)
    return matched
