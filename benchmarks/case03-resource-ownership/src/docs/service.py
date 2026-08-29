from dataclasses import dataclass, field

class UnauthorizedError(Exception): ...

@dataclass
class Document:
    id: str
    owner_id: str
    content: str
    shared_with: list[str] = field(default_factory=list)

def edit_document(user_id: str, doc: Document, new_content: str) -> Document:
    if user_id != doc.owner_id and user_id not in doc.shared_with:
        raise UnauthorizedError(f"User {user_id} cannot edit doc {doc.id}")
    doc.content = new_content
    return doc
