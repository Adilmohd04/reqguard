from auth.service import User, Org, remove_member

def test_admin_can_remove_member():
    admin = User(id="u1", role="admin")
    org = Org(id="o1", members=["u2", "u3"])
    remove_member(admin, org, "u2")
    assert org.members == ["u3"]

def test_admin_remove_nonexistent_member():
    admin = User(id="u1", role="admin")
    org = Org(id="o1", members=["u3"])
    remove_member(admin, org, "u999")
    assert org.members == ["u3"]
# NOTE: No test verifies that non-admin users are rejected. That is the gap.
