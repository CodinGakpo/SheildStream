from alerts.rules import scan_event


# --- signature hits ---------------------------------------------------------


def test_sqli_or_tautology_detected():
    assert "SQLI" in scan_event("q=' OR 1=1--", "curl/8.0")


def test_sqli_union_select_detected():
    assert "SQLI" in scan_event("id=1 UNION SELECT password FROM users", "")


def test_sqli_drop_table_detected():
    assert "SQLI" in scan_event("name=x; DROP TABLE users", "")


def test_xss_script_tag_detected():
    assert "XSS" in scan_event("c=<script>alert(1)</script>", "")


def test_xss_event_handler_detected():
    assert "XSS" in scan_event("img=<img src=x onerror=alert(1)>", "")


def test_path_traversal_literal_detected():
    assert "PATH_TRAVERSAL" in scan_event("path=../../etc/passwd", "")


def test_path_traversal_single_encoded_detected():
    assert "PATH_TRAVERSAL" in scan_event("path=%2e%2e%2fetc%2fpasswd", "")


def test_path_traversal_double_encoded_detected():
    # The obfuscation-resistant case: a naive literal '../' check misses this.
    assert "PATH_TRAVERSAL" in scan_event("path=%252e%252e%252fetc%252fpasswd", "")


def test_attack_in_user_agent_field_detected():
    # Scanning fields separately means a payload smuggled in the UA is caught.
    assert "XSS" in scan_event("q=hello", "<script>evil</script>")


# --- benign traffic does not false-positive ---------------------------------


def test_benign_query_no_match():
    assert scan_event("q=weather+today&page=2", "Mozilla/5.0") == []


def test_word_select_alone_is_not_flagged():
    # A search for the literal word "select" must not trip the SQLi rules —
    # the rules require SQL *syntax*, not just a keyword substring.
    assert "SQLI" not in scan_event("q=select a color", "")


# --- ReDoS resistance -------------------------------------------------------


def test_patterns_are_linear_on_adversarial_input():
    # Simple, anchored patterns must not blow up on a long hostile string.
    import time

    hostile = "a" * 50_000 + "!"
    start = time.perf_counter()
    scan_event(hostile, hostile)
    assert time.perf_counter() - start < 0.5  # generous; catastrophic backtracking would hang
