from datetime import datetime, timezone
from unittest import mock

import pytest

import jwt
from jwt.exceptions import ExpiredSignatureError


def test_decode_rejects_expiration_at_current_time() -> None:
    payload = {"exp": 1700000000}
    token = jwt.encode(payload, "secret", algorithm="HS256")
    current_time = datetime.fromtimestamp(1700000000, tz=timezone.utc)

    with mock.patch("jwt.api_jwt.datetime") as datetime_mock:
        datetime_mock.now.return_value = current_time

        with pytest.raises(ExpiredSignatureError):
            jwt.decode(
                token,
                "secret",
                algorithms=["HS256"],
                leeway=0,
                options={"verify_exp": True},
            )
