from dataclasses import dataclass

class InvalidTransferAmountError(Exception): ...

@dataclass
class BankAccount:
    id: str
    balance: float

def transfer_funds(source: BankAccount, target: BankAccount, amount: float) -> None:
    if amount <= 0:
        raise InvalidTransferAmountError(f"Transfer amount must be positive, got {amount}")
    if source.balance < amount:
        raise ValueError("Insufficient balance")
    source.balance -= amount
    target.balance += amount
