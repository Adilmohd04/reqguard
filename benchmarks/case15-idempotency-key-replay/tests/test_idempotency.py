from gateway.idempotency import IdempotencyStore

def test_new_key_executes_handler():
    store = IdempotencyStore()
    result = store.execute_or_replay("k1", lambda: "processed-1")
    assert result == "processed-1"

def test_different_keys_execute_independently():
    store = IdempotencyStore()
    assert store.execute_or_replay("k1", lambda: "val1") == "val1"
    assert store.execute_or_replay("k2", lambda: "val2") == "val2"
# NOTE: No test checks replay suppression of execution.
