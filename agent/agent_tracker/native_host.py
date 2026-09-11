from __future__ import annotations

import json
import os
from pathlib import Path
import re
import struct
import sys

from .core.client import Client


HOST_NAME = "com.soft.tracking"
EXTENSION_ID = "bjjdlmnghnhfnjlgacoijncoggpnfnjh"
ALLOWED_ORIGIN = "chrome-extension://" + EXTENSION_ID + "/"
FIREFOX_EXTENSION_ID = "tracking@salesof.tech"
MAX_MESSAGE = 1024 * 1024


def authorized_caller(origin, arguments=()):
    if origin == ALLOWED_ORIGIN:
        return True
    # Firefox passes the host manifest path followed by the add-on ID, not an origin.
    if not isinstance(origin, str) or not isinstance(arguments, (tuple, list)) or not arguments or arguments[0] != FIREFOX_EXTENSION_ID:
        return False
    path = Path(origin)
    return path.is_absolute() and path.name in (HOST_NAME + '.json', HOST_NAME + '.firefox.json')


def host_manifest(executable, engine='chromium'):
    manifest = dict(name=HOST_NAME, description='SOFT Tracking desktop connection',
                    path=str(executable), type='stdio')
    if engine == 'chromium':
        manifest['allowed_origins'] = [ALLOWED_ORIGIN]
    elif engine == 'gecko':
        manifest['allowed_extensions'] = [FIREFOX_EXTENSION_ID]
    else:
        raise ValueError('Unsupported browser engine')
    return manifest


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
    if not isinstance(message, dict):
        raise ValueError('Invalid native message')
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
        epoch = message.get('employee_epoch')
        if epoch is not None and (not isinstance(epoch, str) or not re.fullmatch('[a-f0-9]{32}', epoch)):
            raise ValueError('Invalid employee epoch')
        outbox = client.outbox_for_epoch(epoch)
        ids = []
        for event in events:
            event_id = event.get('event_id')
            if not isinstance(event_id, str) or not re.fullmatch('[a-f0-9]{32}', event_id):
                raise ValueError('Invalid event ID')
            if event.get("type") not in ("web_session", "link_click", "button_click", "field_change", "form_submit", "navigation"):
                raise ValueError("Unsupported browser event")
            if 'employee_epoch' in event:
                raise ValueError('Employee epoch belongs to the batch envelope')
        for event in events:
            outbox.push_payload(event)
            ids.append(event["event_id"])
        return {"ok": True, "employee_epoch": epoch if epoch is not None else client.state.get('legacy_employee_epoch'),
                "stored_event_ids": ids, "confirmed_event_ids": outbox.confirmed(ids), "rejected": outbox.rejections(ids)}
    raise ValueError("Unknown native action")


def main(origin: str, stdin=None, stdout=None, client=None, arguments=None) -> int:
    if not authorized_caller(origin, sys.argv[2:] if arguments is None else arguments):
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
