from users.validator import validate_user_email

def test_valid_simple_email():
    assert validate_user_email("alice@example.com") == "alice@example.com"

def test_valid_subdomain_email():
    assert validate_user_email("bob@mail.company.org") == "bob@mail.company.org"
# NOTE: No test checks malformed email rejection.
