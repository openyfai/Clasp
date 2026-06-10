"""
Sidecar Daemon for Kronos Sandboxes.

Listens on a Unix domain socket for signed execution payloads from the host
orchestrator. Rejects unauthenticated requests (fail-closed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("sidecar")

SOCKET_PATH = "/workspace/kronos.sock"
SOCKET_MODE = 0o660


def _validate_payload(payload: dict) -> tuple[str | None, str | None]:
    """Validate lease token and extract command. Returns (command, error)."""
    lease_token = payload.get("lease_token")
    if not lease_token:
        return None, "Missing lease_token — unauthenticated execution denied"

    from clasp.vendor.agent.security.lease import ActuationLease

    lease = ActuationLease.from_token(lease_token)
    if lease is None:
        return None, "Invalid or expired lease token"

    command = payload.get("command")
    if not command or not isinstance(command, str):
        return None, "No command provided"

    if not lease.validate("run_terminal_command"):
        return None, "Lease does not authorize run_terminal_command"

    return command, None


async def handle_client(reader, writer) -> None:
    try:
        data = await reader.read()
        if not data:
            return

        payload = json.loads(data.decode("utf-8"))
        command, error = _validate_payload(payload)
        if error:
            writer.write(json.dumps({"error": error, "exit_code": -1}).encode("utf-8"))
            await writer.drain()
            return

        log.info("Executing authenticated command: %s...", command[:50])

        workspace_cwd = os.environ.get("WORKSPACE_DIR", "/workspace")
        if not os.path.exists(workspace_cwd):
            workspace_cwd = os.getcwd()

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace_cwd,
        )

        stdout, stderr = await process.communicate()
        exit_code = process.returncode
        output = (stdout + stderr).decode("utf-8", errors="replace")

        response = {"output": output, "exit_code": exit_code}
        writer.write(json.dumps(response).encode("utf-8"))
        await writer.drain()
    except Exception as e:
        log.error("Error handling request: %s", e)
        error_resp = {
            "output": f"Sidecar Error: {e}\n{traceback.format_exc()}",
            "exit_code": -1,
        }
        writer.write(json.dumps(error_resp).encode("utf-8"))
        await writer.drain()
    finally:
        writer.close()


async def main() -> None:
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    server = await asyncio.start_unix_server(handle_client, path=SOCKET_PATH)
    os.chmod(SOCKET_PATH, SOCKET_MODE)
    log.info("Sidecar listening on %s (mode %o)", SOCKET_PATH, SOCKET_MODE)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
