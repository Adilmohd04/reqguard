import pytest
from tenant.store import TenantStore, TenantRecord

def test_get_existing_record_same_tenant():
    store = TenantStore()
    store.add_record(TenantRecord(id="r1", tenant_id="t1", data="secret1"))
    rec = store.get_record("t1", "r1")
    assert rec.data == "secret1"

def test_get_missing_record_raises_key_error():
    store = TenantStore()
    with pytest.raises(KeyError):
        store.get_record("t1", "nonexistent")
# NOTE: Cross-tenant access is never tested.
