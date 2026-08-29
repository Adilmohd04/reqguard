from dataclasses import dataclass

class PermissionDeniedError(Exception): ...

@dataclass
class User:
    id: str
    role: str  # "admin" | "member" | "viewer"

@dataclass
class Org:
    id: str
    members: list[str]

def remove_member(actor: User, org: Org, member_id: str) -> None:
    if actor.role != "admin":
        raise PermissionDeniedError(f"User {actor.id} is not an admin")
    if member_id in org.members:
        org.members.remove(member_id)
