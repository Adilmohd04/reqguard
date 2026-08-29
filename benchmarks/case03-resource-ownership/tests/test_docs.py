from docs.service import Document, edit_document

def test_owner_can_edit():
    doc = Document(id="d1", owner_id="user1", content="old")
    updated = edit_document("user1", doc, "new")
    assert updated.content == "new"

def test_shared_user_can_edit():
    doc = Document(id="d2", owner_id="user1", content="initial", shared_with=["user2"])
    updated = edit_document("user2", doc, "collaborative edit")
    assert updated.content == "collaborative edit"
# NOTE: No test checks unauthorized user modification attempt.
