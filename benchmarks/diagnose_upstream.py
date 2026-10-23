"""Send one tool-calling request to the configured upstream and print the outcome.

Uses the proxy's own translator, so a failure here is a failure the proxy would
hit. Prints the HTTP status and the start of the body, never the credential.
Run before a benchmark to find a bad key, model id or schema in seconds.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

from meridian.runtime.proxy.openai_compat import from_openai_response, to_openai_request
from meridian.runtime.secrets import load_credential, redact

REQUEST = {
    "model": os.environ.get("MERIDIAN_UPSTREAM_MODEL", "llama-3.3-70b-versatile"),
    "max_tokens": 256,
    "system": "You are a careful assistant. Use tools when asked.",
    "messages": [{"role": "user", "content": "Call get_status for order 1042."}],
    "tools": [
        {
            "name": "get_status",
            "description": "Look up an order's status",
            "input_schema": {
                "type": "object",
                "properties": {"order_id": {"type": "integer"}},
                "required": ["order_id"],
            },
        }
    ],
}


def main() -> int:
    api = os.environ.get("MERIDIAN_UPSTREAM_API", "anthropic")
    base = os.environ.get("MERIDIAN_UPSTREAM_BASE_URL", "https://api.anthropic.com").rstrip("/")
    credential = load_credential()
    headers = {"content-type": "application/json", credential.header_name: credential.header_value}
    if api == "openai":
        url, payload = f"{base}/chat/completions", to_openai_request(REQUEST)
    else:
        url, payload = f"{base}/v1/messages", REQUEST
        headers["anthropic-version"] = "2023-06-01"
    response = httpx.post(url, json=payload, headers=headers, timeout=60)
    print(f"POST {url} -> {response.status_code}")
    print(redact(response.text[:800]))
    if response.status_code == 200 and api == "openai":
        print("translated:", json.dumps(from_openai_response(response.json()))[:400])
    if response.status_code != 200 and api == "openai":
        listing = httpx.get(f"{base}/models", headers=headers, timeout=30)
        if listing.status_code == 200:
            ids = sorted(m.get("id", "") for m in listing.json().get("data", []))
            print("models available to this key:", ", ".join(ids))
        else:
            print(f"GET {base}/models -> {listing.status_code}")
    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
