"""Keep top-level acceptance scripts out of pytest's import-time collection."""

collect_ignore = [
    "test_auth_security.py",
    "test_contract_acceptance.py",
    "test_order_material_status.py",
    "test_token_lifecycle.py",
]
