import pytest
from banking.transfer import BankAccount, transfer_funds

def test_valid_transfer_moves_funds():
    src = BankAccount(id="b1", balance=100.0)
    dst = BankAccount(id="b2", balance=20.0)
    transfer_funds(src, dst, 30.0)
    assert src.balance == 70.0
    assert dst.balance == 50.0

def test_insufficient_funds_raises_error():
    src = BankAccount(id="b1", balance=20.0)
    dst = BankAccount(id="b2", balance=0.0)
    with pytest.raises(ValueError):
        transfer_funds(src, dst, 50.0)
# NOTE: Zero and negative transfer amounts are not tested.
