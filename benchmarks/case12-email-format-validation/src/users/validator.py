import re

class InvalidEmailFormatError(Exception): ...

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def validate_user_email(email: str) -> str:
    if not EMAIL_REGEX.match(email):
        raise InvalidEmailFormatError(f"Malformed email address: {email}")
    return email.lower()
