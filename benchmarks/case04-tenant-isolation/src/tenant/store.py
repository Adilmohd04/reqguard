from dataclasses import dataclass

class TenantAccessDeniedError(Exception): ...

@dataclass
class TenantRecord:
    id: str
    tenant_id: str
    data: str

class TenantStore:
    def __init__(self) -> None:
        self._records: dict[str, TenantRecord] = {}

    def add_record(self, record: TenantRecord) -> None:
        self._records[record.id] = record

    def get_record(self, tenant_id: str, record_id: str) -> TenantRecord:
        record = self._records.get(record_id)
        if record is None:
            raise KeyError(record_id)
        if record.tenant_id != tenant_id:
            raise TenantAccessDeniedError(f"Tenant {tenant_id} cannot access record of {record.tenant_id}")
        return record
