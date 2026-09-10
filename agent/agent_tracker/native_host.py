from __future__ import annotations

import json
import os
import struct
import sys

from .core.client import Client


HOST_NAME = "com.soft.tracking"
EXTENSION_ID = "bjjdlmnghnhfnjlgacoijncoggpnfnjh"
ALLOWED_ORIGIN = "chrome-extension://" + EXTENSION_ID + "/"
MAX_MESSAGE = 1024 * 1024


def read_exact(stream, length):
    chunks = bytearray()
    while len(chunks) < length:
        part = stream.read(length - len(chunks))
        if not part:
            raise EOFError("Incomplete native message")
        chunks.extend(part)
    return bytes(chunks)


def read_message(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) < 4:
        header += read_exact(stream, 4 - len(header))
    length = struct.unpack("=I", header)[0]
    if not 0 < length <= MAX_MESSAGE:
        raise ValueError("Native message size limit")
    value = json.loads(read_exact(stream, length).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid native message")
    return value


def write_message(stream, value):
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_MESSAGE:
        raise ValueError("Native reply size limit")
    stream.write(struct.pack("=I", len(raw)))
    stream.write(raw)
    stream.flush()


def handle(client, message):
    action = message.get("action")
    if action == "status":
        if 'browser' in message:
            from .browser_health import receive
            receive(client, message['browser'])
        return {"ok": True, "status": client.status()}
    if action == "open":
        from .browser_health import open_desktop
        open_desktop()
        return {"ok": True}
    if action == "store":
        if not client.state.get("identity"):
            raise ValueError("Device not enrolled")
        events = message.get("events")
        if not isinstance(events, list) or len(events) > 100 or any(not isinstance(event, dict) for event in events):
            raise ValueError("Invalid event batch")
        ids = []
        for event in events:
            if event.get("type") not in ("web_session", "link_click", "button_click", "field_change", "form_submit", "navigation"):
                raise ValueError("Unsupported browser event")
            client.outbox.push_payload(event)
            ids.append(event["event_id"])
        return {"ok": True, "stored_event_ids": ids, "confirmed_event_ids": client.outbox.confirmed(ids), "rejected": client.outbox.rejections(ids)}
    raise ValueError("Unknown native action")


def main(origin: str, stdin=None, stdout=None, client=None) -> int:
    if origin != ALLOWED_ORIGIN:
        return 2
    if os.name == "nt" and stdin is None:
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    owned = client is None
    client = client or Client()
    try:
        while True:
            try:
                message = read_message(stdin)
                if message is None:
                    break
                write_message(stdout, handle(client, message))
            except (ValueError, OSError, EOFError) as error:
                client.state.set("error", "Browser collection stopped: local delivery failure")
                write_message(stdout, {"ok": False, "error": type(error).__name__})
                return 1
    finally:
        if owned:
            client.close()
    return 0
