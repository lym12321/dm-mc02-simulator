//! Loopback HTTP/WebSocket adapter; the session thread owns native execution.
use super::{
    ide_export::{Document, SaveRequest},
    session::{LabSession, SessionError, SessionOptions},
};
use axum::{
    Json, Router,
    body::Bytes,
    extract::{
        DefaultBodyLimit, Multipart, Path, Request, State,
        ws::{Message, WebSocket, WebSocketUpgrade},
    },
    http::{HeaderValue, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use futures::{FutureExt, SinkExt, StreamExt, stream::FuturesUnordered};
use serde_json::{Map, Value, json};
use sim_core::Error;
use std::{
    collections::BTreeMap,
    io::{self, BufRead, Write},
    path::Path as FilePath,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::{io::AsyncWriteExt, net::TcpListener};
use uuid::Uuid;

const UPLOAD_LIMIT: usize = 64 * 1024 * 1024;
const SEND_TIMEOUT: Duration = Duration::from_secs(2);
pub const PROTOCOL: &str = "dm-mc02-lab/v1";

// Load only this fixed allowlist before readiness. HTTP requests never read disk.
const ASSET_FILES: &[(&str, &str)] = &[
    ("index.html", "text/html; charset=utf-8"),
    ("app.js", "text/javascript"),
    ("uart-console.js", "text/javascript"),
    ("board-view.js", "text/javascript"),
    ("buzzer-audio.js", "text/javascript"),
    ("board-model.js", "text/javascript"),
    ("board.svg", "image/svg+xml"),
    ("models/dm-mc02.glb", "model/gltf-binary"),
    ("style.css", "text/css"),
    ("vendor/mdui.js", "text/javascript"),
    ("vendor/mdui.css", "text/css"),
    ("vendor/xterm.js", "text/javascript"),
    ("vendor/xterm.css", "text/css"),
    ("vendor/addon-fit.js", "text/javascript"),
    ("vendor/three.js", "text/javascript"),
];

struct Assets(BTreeMap<&'static str, (&'static str, Bytes)>);
impl Assets {
    fn from_directory(directory: &FilePath) -> std::result::Result<Self, SessionError> {
        let root = directory.canonicalize().map_err(|error| {
            failed(Error::Admission(format!(
                "Lab assets directory {}: {error}",
                directory.display()
            )))
        })?;
        let mut assets = BTreeMap::new();
        for &(name, mime) in ASSET_FILES {
            let requested = root.join(name);
            let path = requested.canonicalize().map_err(|error| {
                failed(Error::Admission(format!(
                    "Lab asset {}: {error}",
                    requested.display()
                )))
            })?;
            if !path.starts_with(&root) || !path.is_file() {
                return Err(failed(Error::Admission(format!(
                    "Lab asset must be a file inside {}: {name}",
                    root.display()
                ))));
            }
            let bytes = std::fs::read(&path).map_err(failed)?;
            if bytes.is_empty() {
                return Err(failed(Error::Admission(format!(
                    "Lab asset is empty: {name}"
                ))));
            }
            assets.insert(name, (mime, Bytes::from(bytes)));
        }
        Ok(Self(assets))
    }

    fn response(&self, name: &str) -> Response {
        match self.0.get(name) {
            Some((mime, bytes)) => ([(header::CONTENT_TYPE, *mime)], bytes.clone()).into_response(),
            None => StatusCode::NOT_FOUND.into_response(),
        }
    }
}

struct HttpError(StatusCode, String);
impl IntoResponse for HttpError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":self.1}))).into_response()
    }
}
fn bad(error: impl std::fmt::Display) -> HttpError {
    HttpError(StatusCode::BAD_REQUEST, error.to_string())
}
fn internal(error: impl std::fmt::Display) -> HttpError {
    HttpError(StatusCode::INTERNAL_SERVER_ERROR, error.to_string())
}
fn failed(error: impl std::error::Error + Send + Sync + 'static) -> SessionError {
    SessionError {
        primary: Arc::new(error),
        notes: vec![],
        input: false,
    }
}
#[derive(Default)]
struct Resources {
    controller: Option<String>,
    upload: Option<tempfile::TempDir>,
}
struct WebState {
    session: Arc<LabSession>,
    assets: Assets,
    resources: Mutex<Resources>,
    upload_lock: tokio::sync::Mutex<()>,
    ide_save_busy: Arc<AtomicBool>,
    cancelled: Arc<AtomicBool>,
}
fn is_active(value: &Value) -> bool {
    matches!(
        value["status"].as_str(),
        Some("starting" | "paused" | "running" | "stopping")
    )
}

fn permitted_origin(request: &Request) -> std::result::Result<(), &'static str> {
    let host = request
        .headers()
        .get(header::HOST)
        .and_then(|value| value.to_str().ok())
        .ok_or("local Host required")?;
    let authority: axum::http::uri::Authority = host.parse().map_err(|_| "local Host required")?;
    if !authority.host().eq_ignore_ascii_case("localhost") && authority.host() != "127.0.0.1" {
        return Err("local Host required");
    }
    let origin = request.headers().get(header::ORIGIN);
    if (request.uri().path() == "/api/ide/save" && origin.is_none())
        || origin.is_some_and(|origin| origin != format!("http://{host}").as_str())
    {
        return Err("same-origin control required");
    }
    Ok(())
}
async fn local_origin(request: Request, next: Next) -> Response {
    let closes_rejected_body = matches!(request.uri().path(), "/api/firmware" | "/api/ide/save");
    let mut response = match permitted_origin(&request) {
        Ok(()) => next.run(request).await,
        Err(error) => HttpError(StatusCode::FORBIDDEN, error.into()).into_response(),
    };
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response.headers_mut().insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    // A rejected body may remain unread. Do not reuse that connection for the
    // next firmware or native-save request.
    if closes_rejected_body && !response.status().is_success() {
        response
            .headers_mut()
            .insert(header::CONNECTION, HeaderValue::from_static("close"));
    }
    response
}
async fn state(State(state): State<Arc<WebState>>) -> Json<Value> {
    Json(state.session.state())
}
async fn info() -> Json<Value> {
    Json(json!({
        "protocol": PROTOCOL,
        "backend_version": env!("CARGO_PKG_VERSION"),
        "capabilities": ["project", "firmware-upload", "ide-export", "native-ide-save", "websocket-controller", "external-assets", "stdin-shutdown"]
    }))
}
async fn project(State(state): State<Arc<WebState>>) -> Json<Value> {
    Json(state.session.project_info())
}
async fn save_project(
    State(state): State<Arc<WebState>>,
    Json(value): Json<Value>,
) -> std::result::Result<Json<Value>, HttpError> {
    state.session.save_project(value).map(Json).map_err(bad)
}
async fn ide(
    State(state): State<Arc<WebState>>,
    Json(value): Json<Value>,
) -> std::result::Result<Json<Value>, HttpError> {
    state.session.ide_config(value).map(Json).map_err(bad)
}

struct IdeSavePermit(Arc<AtomicBool>);
impl Drop for IdeSavePermit {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

async fn ide_save(
    State(state): State<Arc<WebState>>,
    Json(request): Json<SaveRequest>,
) -> std::result::Result<Json<super::ide_export::Outcome>, HttpError> {
    let preview = state
        .session
        .ide_config(serde_json::to_value(request.project).map_err(bad)?)
        .map_err(bad)?;
    let document = Document::from_preview(request.format, &preview);
    state
        .ide_save_busy
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .map_err(|_| {
            HttpError(
                StatusCode::CONFLICT,
                "另一个导出正在等待保存，请先完成或取消。".into(),
            )
        })?;
    let permit = IdeSavePermit(state.ide_save_busy.clone());
    let cancelled = state.cancelled.clone();
    let runtime = tokio::runtime::Handle::current();
    let (sender, receiver) = tokio::sync::oneshot::channel();
    // A standalone thread owns the dialog. Tokio shutdown never waits for it,
    // and the session mutexes have all been released before the dialog opens.
    std::thread::Builder::new()
        .name("lab-ide-save".into())
        .spawn(move || {
            // The portal backend uses Tokio on Unix; native Windows dialogs
            // are synchronous. Both stay off the HTTP runtime's worker thread.
            let _runtime = runtime.enter();
            let _permit = permit;
            let _ = sender.send(document.save_native(&cancelled));
        })
        .map_err(internal)?;
    tokio::select! {
        result = receiver => result.map_err(internal)?.map(Json).map_err(internal),
        _ = wait_for_shutdown(&state.cancelled) => Err(HttpError(
            StatusCode::SERVICE_UNAVAILABLE,
            "应用正在退出，已取消导出。".into(),
        )),
    }
}

async fn wait_for_shutdown(cancelled: &AtomicBool) {
    while !cancelled.load(Ordering::Acquire) {
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}
async fn index(State(state): State<Arc<WebState>>) -> Response {
    state.assets.response("index.html")
}
async fn asset(State(state): State<Arc<WebState>>, Path(name): Path<String>) -> Response {
    state.assets.response(&name)
}

async fn upload(
    State(state): State<Arc<WebState>>,
    multipart: std::result::Result<Multipart, axum::extract::multipart::MultipartRejection>,
) -> std::result::Result<Json<Value>, HttpError> {
    let _guard = state.upload_lock.lock().await;
    if is_active(&state.session.state()) {
        return Err(HttpError(
            StatusCode::CONFLICT,
            "stop the current experiment before uploading firmware".into(),
        ));
    }
    let mut multipart = multipart.map_err(bad)?;
    let temporary = tempfile::Builder::new()
        .prefix("dm-web-firmware.")
        .tempdir()
        .map_err(internal)?;
    let path = temporary.path().join("firmware.img");
    let mut field = multipart
        .next_field()
        .await
        .map_err(bad)?
        .ok_or_else(|| bad("one firmware file is required"))?;
    if field.name() != Some("firmware") {
        return Err(bad("one firmware file is required"));
    }
    let filename = field
        .file_name()
        .ok_or_else(|| bad("one firmware file is required"))?
        .replace('\\', "/");
    let name = filename.rsplit('/').next().unwrap();
    if name.is_empty() || name.chars().count() > 255 || name.chars().any(|ch| ch < ' ') {
        return Err(bad("invalid firmware filename"));
    }
    let mut file = tokio::fs::File::create(&path).await.map_err(internal)?;
    let mut size = 0;
    while let Some(chunk) = field
        .chunk()
        .await
        .map_err(|error| HttpError(error.status(), error.to_string()))?
    {
        size += chunk.len();
        if size > UPLOAD_LIMIT {
            return Err(HttpError(
                StatusCode::PAYLOAD_TOO_LARGE,
                "firmware exceeds64MiB".into(),
            ));
        }
        for chunk in chunk.chunks(65536) {
            file.write_all(chunk).await.map_err(internal)?;
        }
    }
    drop(field);
    file.flush().await.map_err(internal)?;
    drop(file);
    if size == 0 || multipart.next_field().await.map_err(bad)?.is_some() {
        return Err(bad("exactly one nonempty firmware file is required"));
    }
    let value = state
        .session
        .select_firmware(path, Some(name))
        .map_err(bad)?;
    let previous = state.resources.lock().unwrap().upload.replace(temporary);
    if let Some(previous) = previous {
        previous.close().map_err(internal)?;
    }
    Ok(Json(value))
}

#[derive(Debug)]
struct Envelope {
    id: String,
    session_id: Option<String>,
    action: String,
    args: Map<String, Value>,
}
fn envelope(text: &str) -> std::result::Result<Envelope, String> {
    let mut value: Map<String, Value> =
        serde_json::from_str(text).map_err(|error| error.to_string())?;
    if value.len() != 4
        || ["id", "session_id", "action", "args"]
            .iter()
            .any(|key| !value.contains_key(*key))
    {
        return Err("expected id, session_id, action and args".into());
    }
    let id = value
        .remove("id")
        .unwrap()
        .as_str()
        .filter(|id| (1..=128).contains(&id.chars().count()))
        .ok_or("id must be a nonempty string of at most128 characters")?
        .to_owned();
    let session_id = match value.remove("session_id").unwrap() {
        Value::Null => None,
        Value::String(id) if id.chars().count() <= 64 => Some(id),
        _ => return Err("invalid session_id".into()),
    };
    let action = value
        .remove("action")
        .unwrap()
        .as_str()
        .ok_or("action must be a string")?
        .to_owned();
    let Value::Object(args) = value.remove("args").unwrap() else {
        return Err("args must be an object".into());
    };
    Ok(Envelope {
        id,
        session_id,
        action,
        args,
    })
}
struct ReplyError {
    message: String,
    notes: Vec<String>,
}
impl From<SessionError> for ReplyError {
    fn from(error: SessionError) -> Self {
        Self {
            message: error.to_string(),
            notes: error.notes,
        }
    }
}
impl From<futures::channel::oneshot::Canceled> for ReplyError {
    fn from(error: futures::channel::oneshot::Canceled) -> Self {
        Self {
            message: error.to_string(),
            notes: vec![],
        }
    }
}
impl From<&str> for ReplyError {
    fn from(message: &str) -> Self {
        Self {
            message: message.into(),
            notes: vec![],
        }
    }
}
async fn command(state: Arc<WebState>, controller: String, request: Envelope) -> (Value, bool) {
    let is_stop = request.action == "stop";
    let result: std::result::Result<Value, ReplyError> = async {
        if matches!(request.action.as_str(), "stop" | "restart") {
            if (request.action == "stop" && !request.args.is_empty())
                || (request.action == "restart"
                    && !request.args.is_empty()
                    && !(request.args.len() == 1 && request.args.contains_key("config")))
                || (request.action == "restart"
                    && state.session.state()["session_id"] != json!(request.session_id))
            {
                return Err("invalid lifecycle request or stale session".into());
            }
            if request.action == "restart" {
                state
                    .session
                    .restart(
                        Value::Object(request.args),
                        request.session_id.as_deref(),
                        Some(&controller),
                    )
                    .await
                    .map_err(ReplyError::from)
            } else {
                // The exclusive live controller stops this workbench even if
                // reload has changed generation before its state frame arrives.
                Ok(state.session.request_stop(None).await??)
            }
        } else {
            state
                .session
                .submit(
                    &request.action,
                    Value::Object(request.args),
                    request.session_id.as_deref(),
                    Some(&controller),
                )
                .await?
                .map_err(ReplyError::from)
        }
    }
    .await;
    let response = match result {
        Ok(value) => {
            json!({"type":"command_result","id":request.id,"session_id":state.session.state()["session_id"],"ok":true,"accepted":true,"timestamp_ns":value.get("timestamp_ns").cloned().unwrap_or(Value::Null)})
        }
        Err(error) => {
            json!({"type":"command_result","id":request.id,"session_id":request.session_id,"ok":false,"accepted":false,"error":error.message,"notes":error.notes})
        }
    };
    (response, is_stop)
}
struct Controller {
    state: Arc<WebState>,
    id: String,
}
impl Drop for Controller {
    fn drop(&mut self) {
        self.state.session.disconnect(&self.id);
        let mut resources = self.state.resources.lock().unwrap();
        if resources.controller.as_deref() == Some(&self.id) {
            resources.controller = None;
        }
    }
}
async fn websocket(
    State(state): State<Arc<WebState>>,
    upgrade: WebSocketUpgrade,
) -> std::result::Result<Response, HttpError> {
    let controller = Uuid::new_v4().simple().to_string();
    {
        let mut resources = state.resources.lock().unwrap();
        if resources.controller.is_some() {
            return Err(HttpError(
                StatusCode::CONFLICT,
                "another page already controls this workbench".into(),
            ));
        }
        resources.controller = Some(controller.clone());
    }
    let guard = Controller {
        state,
        id: controller,
    };
    // The same guard also releases the claim when the native upgrade fails.
    let guard = Arc::new(Mutex::new(Some(guard)));
    let failed = guard.clone();
    Ok(upgrade
        .max_message_size(320 * 1024)
        .max_frame_size(320 * 1024)
        .on_failed_upgrade(move |_| {
            failed.lock().unwrap().take();
        })
        .on_upgrade(move |socket| async move {
            let controller = guard.lock().unwrap().take().unwrap();
            socket_loop(socket, controller).await;
        }))
}
async fn send(sink: &mut futures::stream::SplitSink<WebSocket, Message>, message: Message) -> bool {
    matches!(
        tokio::time::timeout(SEND_TIMEOUT, sink.send(message)).await,
        Ok(Ok(()))
    )
}
async fn send_json(
    sink: &mut futures::stream::SplitSink<WebSocket, Message>,
    value: Value,
) -> bool {
    send(sink, Message::Text(value.to_string().into())).await
}
async fn socket_loop(socket: WebSocket, controller: Controller) {
    let state = controller.state.clone();
    let (mut sink, mut stream) = socket.split();
    let mut pending = FuturesUnordered::new();
    let mut stop_pending = false;
    let mut ticker = tokio::time::interval(Duration::from_millis(100));
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut after = 0;
    let mut last_message = Instant::now();
    let mut ping_sent = None;
    loop {
        if state.cancelled.load(Ordering::Relaxed) {
            break;
        }
        tokio::select! {
            message = stream.next() => {
                let Some(Ok(message)) = message else { break; };
                last_message = Instant::now();
                match message {
                    Message::Close(_) => break,
                    Message::Pong(_) => ping_sent = None,
                    Message::Text(text) => {
                        match envelope(&text) {
                            Ok(request) if pending.len() >=64 && (request.action != "stop" || stop_pending) => {
                                if !send_json(&mut sink,json!({"type":"command_result","id":request.id,"ok":false,"accepted":false,"error":"too many pending requests"})).await { break; }
                            }
                            Ok(request) => {
                                stop_pending |= request.action == "stop";
                                pending.push(command(state.clone(),controller.id.clone(),request).boxed());
                            }
                            Err(error) => if !send_json(&mut sink,json!({"type":"command_result","id":null,"ok":false,"accepted":false,"error":error})).await { break; },
                        }
                    }
                    _ => (),
                }
            }
            Some((response,is_stop)) = pending.next(), if !pending.is_empty() => {
                if is_stop { stop_pending = false; }
                if !send_json(&mut sink,response).await || !send_json(&mut sink,state.session.state()).await { break; }
            }
            _ = ticker.tick() => {
                if ping_sent.is_some_and(|at:Instant| at.elapsed() > Duration::from_millis(7500)) { break; }
                if ping_sent.is_none() && last_message.elapsed() >= Duration::from_secs(15) {
                    if !send(&mut sink,Message::Ping(Vec::new().into())).await { break; }
                    ping_sent = Some(Instant::now());
                }
                if !send_json(&mut sink,state.session.state()).await { break; }
                for event in state.session.history(after) {
                    after = event["seq"].as_str().unwrap().parse().unwrap();
                    if state.cancelled.load(Ordering::Relaxed) || !send_json(&mut sink,event).await { return; }
                }
            }
        }
    }
    drop(pending);
    drop(controller); // Release momentary inputs before any closing socket IO.
    let _ = send(&mut sink, Message::Close(None)).await;
}

/// Run the desktop's private transport until its parent closes stdin or sends shutdown.
/// Uploaded firmware and native execution stay owned until cleanup completes.
pub fn run_desktop(
    options: SessionOptions,
    port: u16,
    assets_directory: &FilePath,
    cancelled: Arc<AtomicBool>,
) -> std::result::Result<(), SessionError> {
    let assets = Assets::from_directory(assets_directory)?;
    run_server(options, port, cancelled, assets)
}

fn monitor_stdin(
    cancelled: Arc<AtomicBool>,
) -> io::Result<std::sync::mpsc::Receiver<io::Result<()>>> {
    let (sender, receiver) = std::sync::mpsc::channel();
    // A plain thread avoids Tokio's non-cancellable blocking stdin task, which
    // otherwise prevents the runtime from exiting after Ctrl-C with stdin open.
    std::thread::Builder::new()
        .name("lab-parent-stdin".into())
        .spawn(move || {
            let mut input = io::stdin().lock();
            let mut bytes = Vec::new();
            let result = loop {
                bytes.clear();
                match input.read_until(b'\n', &mut bytes) {
                    Ok(0) => break Ok(()),
                    Ok(_) if bytes == b"shutdown\n" || bytes == b"shutdown\r\n" => break Ok(()),
                    Ok(_) => (),
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => (),
                    Err(error) => break Err(error),
                }
            };
            let _ = sender.send(result);
            cancelled.store(true, Ordering::Relaxed);
        })?;
    Ok(receiver)
}

fn run_server(
    options: SessionOptions,
    port: u16,
    cancelled: Arc<AtomicBool>,
    assets: Assets,
) -> std::result::Result<(), SessionError> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(failed)?;
    let session = Arc::new(LabSession::new(options)?);
    let state = Arc::new(WebState {
        session: session.clone(),
        assets,
        resources: Mutex::new(Resources::default()),
        upload_lock: tokio::sync::Mutex::new(()),
        ide_save_busy: Arc::new(AtomicBool::new(false)),
        cancelled: cancelled.clone(),
    });
    let router = Router::new()
        .route("/", get(index))
        .route("/{*asset}", get(asset))
        .route("/api/info", get(info))
        .route("/api/state", get(self::state))
        .route(
            "/api/project",
            get(project)
                .post(save_project)
                .layer(DefaultBodyLimit::max(256 * 1024)),
        )
        .route(
            "/api/ide",
            post(ide).layer(DefaultBodyLimit::max(256 * 1024)),
        )
        .route(
            "/api/ide/save",
            post(ide_save).layer(DefaultBodyLimit::max(256 * 1024)),
        )
        .route("/api/firmware", post(upload))
        .route("/api/ws", get(websocket))
        .layer(DefaultBodyLimit::max(UPLOAD_LIMIT + 65536))
        .layer(middleware::from_fn(local_origin))
        .with_state(state.clone());
    let execution: std::result::Result<(), SessionError> = runtime.block_on(async {
        let listener = TcpListener::bind(("127.0.0.1", port))
            .await
            .map_err(failed)?;
        let address = listener.local_addr().map_err(failed)?;
        let stdin = monitor_stdin(cancelled.clone()).map_err(failed)?;
        {
            let mut output = io::stdout().lock();
            serde_json::to_writer(
                &mut output,
                &json!({
                    "event": "lab-ready",
                    "protocol": PROTOCOL,
                    "url": format!("http://{address}"),
                    "pid": std::process::id(),
                }),
            )
            .map_err(failed)?;
            writeln!(output).map_err(failed)?;
            output.flush().map_err(failed)?;
        }
        axum::serve(listener, router)
            .with_graceful_shutdown(async move {
                while !cancelled.load(Ordering::Relaxed) {
                    tokio::time::sleep(Duration::from_millis(20)).await;
                }
            })
            .await
            .map_err(failed)?;
        match stdin.try_recv() {
            Ok(result) => result.map_err(failed),
            _ => Err(failed(Error::Interrupted(
                "desktop backend interrupted".into(),
            ))),
        }
    });
    let mut primary = execution.err();
    if let Err(error) = session.close() {
        if let Some(primary) = &mut primary {
            primary
                .notes
                .push(format!("desktop backend cleanup: {error}"));
            primary.notes.extend(error.notes);
        } else {
            primary = Some(error);
        }
    }
    let upload = state.resources.lock().unwrap().upload.take();
    if let Some(upload) = upload {
        if let Err(error) = upload.close() {
            if let Some(primary) = &mut primary {
                primary
                    .notes
                    .push(format!("uploaded firmware cleanup: {error}"));
            } else {
                primary = Some(failed(error));
            }
        }
    }
    match primary {
        Some(error) => Err(error),
        None => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::{envelope, permitted_origin};

    #[test]
    fn native_ide_save_requires_matching_origin_without_changing_preview_clients() {
        for (path, origin, allowed) in [
            ("/api/ide", None, true),
            ("/api/ide/save", None, false),
            ("/api/ide/save", Some("http://127.0.0.1:1234"), true),
            ("/api/ide/save", Some("http://127.0.0.1:9999"), false),
            ("/api/ide/save", Some("https://foreign.example"), false),
            ("/api/ide/save", Some("null"), false),
        ] {
            let mut request = axum::http::Request::builder()
                .uri(path)
                .header("Host", "127.0.0.1:1234");
            if let Some(origin) = origin {
                request = request.header("Origin", origin);
            }
            let request = request.body(axum::body::Body::empty()).unwrap();
            assert_eq!(
                permitted_origin(&request).is_ok(),
                allowed,
                "{path} {origin:?}"
            );
        }
    }

    #[test]
    fn envelope_requires_exact_fields_and_keeps_unicode_ids_and_object_args() {
        let value = envelope(
            r#"{"id":"请求","session_id":null,"action":"uart","args":{"data_b64":"AP8="}}"#,
        )
        .unwrap();
        assert_eq!(value.id, "请求");
        assert_eq!(value.args["data_b64"], "AP8=");
        for text in [
            r#"[]"#,
            r#"{"id":"x","action":"stop","args":{}}"#,
            r#"{"id":"x","session_id":null,"action":"stop","args":[],"extra":1}"#,
            r#"{"id":0,"session_id":null,"action":"stop","args":{}}"#,
        ] {
            assert!(envelope(text).is_err(), "{text}");
        }
    }
}
