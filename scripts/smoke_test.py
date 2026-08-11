"""Quick liveness check against the control surface running inside Live.

Run:  python scripts/smoke_test.py
"""

import json
import socket
import struct
import sys
import uuid

HOST, PORT = "127.0.0.1", 9877


def send(sock: socket.socket, command: str, **params) -> dict:
    body = json.dumps({"type": command, "params": params, "id": str(uuid.uuid4())}).encode()
    sock.sendall(struct.pack(">I", len(body)) + body)
    header = b""
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            raise ConnectionError("connection closed")
        header += chunk
    length = struct.unpack(">I", header)[0]
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise ConnectionError("connection closed")
        payload += chunk
    return json.loads(payload.decode())


def main() -> None:
    try:
        sock = socket.create_connection((HOST, PORT), timeout=10)
    except OSError as e:
        print(f"FAIL: cannot connect to {HOST}:{PORT} — is Live running with AbletonMCP enabled? ({e})")
        sys.exit(1)

    with sock:
        response = send(sock, "ping")
        print(json.dumps(response, indent=2))
        result = response.get("result", {})
        if response.get("status") == "success" and result.get("pong"):
            print(f"\nOK: AbletonMCP v{result.get('version')} responding, "
                  f"{result.get('command_count')} commands registered.")
        else:
            print("\nFAIL: unexpected response")
            sys.exit(1)


if __name__ == "__main__":
    main()
