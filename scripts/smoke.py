#!/usr/bin/env python3
"""Call the tools of a running cerase-calc container over MCP, as a client does.

The unit tests import server.py directly, so they cannot see an image whose
entrypoint, dependencies or bridge are broken. This talks to the container
through mcp-proxy's Streamable HTTP endpoint: the handshake, the tool list, one
exact answer and one refusal. Exit 0 when all four hold, 1 with the reason.

    python3 scripts/smoke.py [http://127.0.0.1:3000/mcp]

Standard library only, so it runs on a CI runner with nothing installed.
"""
from __future__ import annotations

import json
import sys
import urllib.request

ENDPOINT = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3000/mcp"
TOOLS = {"calculate", "sum", "product", "round"}


def _post(body: dict, session: str | None) -> tuple[dict, str | None]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8", "replace").strip()
        session = response.headers.get("Mcp-Session-Id") or session
    frames = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:") and line[5:].strip()]
    text = frames[-1] if frames else raw
    return (json.loads(text) if text else {}), session


def _call(session: str | None, request_id: int, name: str, arguments: dict) -> dict:
    answer, _ = _post(
        {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        session,
    )
    return answer.get("result") or {}


def main() -> int:
    handshake, session = _post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}},
        },
        None,
    )
    if "result" not in handshake:
        print(f"initialize answered {handshake}")
        return 1
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)

    listing, _ = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session)
    names = {tool["name"] for tool in listing.get("result", {}).get("tools", [])}
    if names != TOOLS:
        print(f"tools/list answered {sorted(names)}, expected {sorted(TOOLS)}")
        return 1

    exact = _call(session, 3, "calculate", {"expression": "0.1 + 0.2"})
    value = (exact.get("structuredContent") or {}).get("value")
    if exact.get("isError") or value != "0.3":
        print(f"calculate 0.1 + 0.2 answered {exact}")
        return 1

    refusal = _call(session, 4, "calculate", {"expression": "1,5"})
    text = " ".join(part.get("text", "") for part in refusal.get("content", []))
    if not refusal.get("isError") or "comma" not in text:
        print(f"calculate 1,5 was not refused with its reason: {refusal}")
        return 1

    print(f"{ENDPOINT}: {len(names)} tools; 0.1 + 0.2 = {value}; 1,5 refused: {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
