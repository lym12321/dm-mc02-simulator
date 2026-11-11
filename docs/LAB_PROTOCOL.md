# Lab desktop backend protocol

The Simulator desktop application owns one private `sim-lab-backend` process,
keeps its stdin pipe open, and opens the backend's loopback URL in its WebView.
Frontend source and assets live in `apps/lab/src/`. The public `sim` CLI has no
web/serve command and embeds no frontend resources.

## Start and stop a desktop session

```text
sim-lab-backend --port 0 --assets-dir <absolute-frontend-directory>
```

The desktop process accepts firmware, project, watch, QEMU and board settings.
Port zero selects an available port. `--assets-dir` is required.
After validation and binding, stdout contains one JSON line, flushed immediately:

```json
{"event":"lab-ready","protocol":"dm-mc02-lab/v1","url":"http://127.0.0.1:49152","pid":1234}
```

The caller must parse the URL rather than assume a port. It must check the
protocol before opening the page. Errors go to stderr; startup failure emits no
ready event. Startup creates an idle Lab session without starting QEMU.

Close the parent's stdin pipe, or write the exact line `shutdown\n` (CRLF is also
accepted), to stop. The explicit line supports desktop process APIs that retain
ownership of the stdin handle. The backend closes HTTP/WebSocket
connections, stops the active Lab session, waits for QEMU and native owner
cleanup, removes temporary firmware, and exits zero. Other stdin lines are ignored.
A stdin read error or cleanup failure exits nonzero. Ctrl-C remains supported
and retains the interrupted exit code. The desktop owner should wait for normal
exit before applying its own bounded process-tree termination fallback.

## Frontend assets

All nine files below must be nonempty regular files within the assets directory.
Paths that resolve outside that directory are rejected. Files are loaded once
before readiness; HTTP never reads a client-supplied filesystem path. Other
files in the directory are not served.

```text
index.html
app.js
uart-console.js
style.css
vendor/mdui.js
vendor/mdui.css
vendor/xterm.js
vendor/xterm.css
vendor/addon-fit.js
```

`/` serves `index.html`. Existing JavaScript and stylesheet URLs remain the same.
Releases preserve the third-party notices in `apps/lab/src/vendor/licenses/`.

## Version and transport

`GET /api/info` returns:

```json
{
  "protocol": "dm-mc02-lab/v1",
  "backend_version": "0.1.0",
  "capabilities": [
    "project", "firmware-upload", "ide-export", "websocket-controller",
    "external-assets", "stdin-shutdown"
  ]
}
```

`backend_version` is the actual Cargo package version; the value above is an
example. Capabilities describe the supported backend interface. Stdin ownership
and external assets apply to the private desktop process.

The server binds only to IPv4 loopback. Host must be `127.0.0.1` or `localhost`;
when Origin is present it must equal `http://{Host}`, including its port. The
desktop WebView must load the ready URL so its requests remain same-origin.
CORS and a Tauri-specific Origin exception are not enabled. Responses retain
`Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

Existing `/api/state`, `/api/project`, `/api/ide`, `/api/firmware` and `/api/ws`
routes keep their behavior. A single WebSocket controller owns board input; a
second controller receives HTTP 409. Each command has exactly four fields:

```json
{"id":"request-1","session_id":null,"action":"load","args":{}}
```

`args` is an object. Replies correlate `id` and report `ok`/`accepted`. Successful
replies carry the current session generation. After `load`, subsequent commands use the returned
`session_id`. Stale generations are rejected, except `stop` remains available to
stop the live board. Disconnect releases momentary inputs while preserving the
Lab session; the next controller can observe and stop it. Firmware uploads are
limited to one nonempty file of at most 64 MiB and require an inactive session.
