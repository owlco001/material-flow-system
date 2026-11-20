"""Synthetic device-only material payload budget, no production data or time thresholds."""
import json
import statistics
import time

from app import main as backend


def test_device_filter_keeps_only_target_payload_on_twenty_thousand_rows(record_property):
    c = backend.db()
    stamp = backend.now()
    order = "device-budget-order"
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?,?)", (order, order, "Synthetic", "IN_PROGRESS", stamp, stamp))
    c.executemany("INSERT INTO materials(id,code,name,unit) VALUES(?,?,?,?)",
                  ((f"device-budget-m{i}", f"DB-M{i:03}", "Synthetic material", "EA") for i in range(200)))
    for d in range(100):
        device = f"device-budget-d{d}"
        c.execute("INSERT INTO order_devices VALUES(?,?,?,?,?,?)", (device, order, "TYPE", f"DB-D{d:03}", d, stamp))
        c.executemany("INSERT INTO order_material_requirements VALUES(?,?,?,?,?,?,?,?,?,?)",
            ((f"device-budget-r{d}-{i}", order, device, f"device-budget-m{i}", 10, 5, 5, "OUT_OF_STOCK", stamp, stamp) for i in range(200)))
    c.commit()
    c.close()
    base = {"documentType": "PRODUCTION_ORDER", "documentNo": order}
    user = {"id": "synthetic", "role": "ADMIN"}
    samples = {}
    outputs = {}
    for label, extra in (("order", {}), ("device", {"deviceNo": "DB-D050"})):
        elapsed = []
        for _ in range(3):
            start = time.perf_counter()
            result = backend.material_status({**base, **extra}, user=user, x_request_id="synthetic")
            elapsed.append((time.perf_counter() - start) * 1000)
        outputs[label] = result
        samples[label] = statistics.median(elapsed)
    full, device = outputs["order"], outputs["device"]
    assert full["total"] == 20000 and len(full["items"]) == 20000
    assert device["total"] == 200 and len(device["items"]) == 200
    assert device["materialSummary"]["totalRequiredQuantity"] == 2000
    assert full["materialSummary"]["totalRequiredQuantity"] == 200000
    assert all(row["deviceNo"] == "DB-D050" for row in device["items"])
    sizes = {key: len(json.dumps(value, ensure_ascii=False).encode()) for key, value in outputs.items()}
    assert sizes["device"] * 30 < sizes["order"]
    for key in outputs:
        record_property(f"{key}_bytes", sizes[key])
        record_property(f"{key}_median_ms", round(samples[key], 3))
        record_property(f"{key}_rows", len(outputs[key]["items"]))
