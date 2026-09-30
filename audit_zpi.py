import json

from scanner.market_data import audit_endpoint


def run(path: str, params: dict | None = None):
    print("=" * 70)
    print(f"ENDPOINT: {path}")
    print(f"PARAMS  : {params or {}}")
    print("=" * 70)

    data = audit_endpoint(path, params or {})
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run("quote", {"code": "BBCA"})
