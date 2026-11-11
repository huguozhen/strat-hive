"""关键写操作与请求回执在同一事务提交。"""

from hashlib import sha256
import json

from .rules import require


def recoverable_write(store, operation_id, operation, request_id, **inputs):
    """相同请求返回原提交结果，失败事务不会留下回执。"""
    with store.transaction(write=True) as tx:
        if request_id is None:
            return operation(tx, **inputs)

        require(isinstance(request_id, str) and bool(request_id.strip()) and len(request_id) <= 128,
                "invalid request ID")
        encoded = json.dumps([operation_id, inputs], sort_keys=True, default=str).encode()
        fingerprint = sha256(encoded).hexdigest()
        previous = tx.find("write_request", id=request_id)
        if previous:
            require(previous[0]["fingerprint"] == fingerprint,
                    "request ID was already used for different input", "request_conflict")
            return previous[0]["result"]

        result = operation(tx, **inputs)
        tx.add("write_request", id=request_id, fingerprint=fingerprint, result=result)
        return result
