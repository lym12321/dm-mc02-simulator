//! Own one backend process, including startup, diagnostics and bounded shutdown.
use serde::Deserialize;
use std::{
    collections::VecDeque,
    fs::{self, OpenOptions},
    io::Write,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver},
    },
    time::Duration,
};
use tauri::{AppHandle, Manager, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::{
    ShellExt,
    process::{CommandChild, CommandEvent},
};

const PROTOCOL: &str = "dm-mc02-lab/v1";
const START_LIMIT: Duration = Duration::from_secs(20);
const STOP_LIMIT: Duration = Duration::from_secs(5);

#[derive(Default)]
pub struct Backend {
    child: Mutex<Option<CommandChild>>,
    exited: Mutex<Option<Receiver<()>>>,
    closing: AtomicBool,
}

#[derive(Deserialize)]
struct Ready {
    event: String,
    protocol: String,
    url: String,
    pid: u32,
}

fn ready_url(line: &[u8], pid: u32) -> Result<Url, String> {
    let ready: Ready =
        serde_json::from_slice(line).map_err(|e| format!("后端启动消息无效：{e}"))?;
    if ready.event != "lab-ready" || ready.protocol != PROTOCOL || ready.pid != pid {
        return Err("后端协议版本或进程标识不匹配，请重新准备后端运行包。".into());
    }
    let url = Url::parse(&ready.url).map_err(|e| e.to_string())?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port().is_none_or(|port| port == 0)
        || url.path() != "/"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("后端返回了无效的本机服务地址。".into());
    }
    Ok(url)
}

impl Backend {
    pub fn start(self: Arc<Self>, app: AppHandle) {
        if let Err(error) = self.start_inner(&app) {
            if self.closing.load(Ordering::Acquire) {
                return;
            }
            self.shutdown();
            show_failure(&app, &error);
        }
    }

    fn start_inner(self: &Arc<Self>, app: &AppHandle) -> Result<(), String> {
        let resource = app.path().resource_dir().map_err(|e| e.to_string())?;
        let runtime = resource.join("runtime");
        let binary = runtime.join(if cfg!(windows) {
            "sim-lab-backend.exe"
        } else {
            "sim-lab-backend"
        });
        let qemu = runtime.join("bin").join(if cfg!(windows) {
            "qemu-system-arm.exe"
        } else {
            "qemu-system-arm"
        });
        let assets = resource.join("lab");
        if !binary.is_file() || !qemu.is_file() || !assets.join("index.html").is_file() {
            return Err("运行文件不完整。请保留完整的 Simulator 目录，或重新安装应用。".into());
        }
        let log_dir = app.path().app_log_dir().map_err(|e| e.to_string())?;
        fs::create_dir_all(&log_dir).map_err(|e| e.to_string())?;
        let log_path = log_dir.join("backend.log");
        let mut log = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&log_path)
            .map_err(|e| e.to_string())?;
        // Spawn and register under the ownership lock so an immediate window close
        // cannot exit the app between CREATE_SUSPENDED and Job Object registration.
        let mut child_owner = self.child.lock().unwrap();
        if self.closing.load(Ordering::Acquire) {
            return Ok(());
        }
        let (mut events, child) = app
            .shell()
            .command(&binary)
            .args(["--port", "0", "--assets-dir"])
            .arg(&assets)
            .env("QEMU_SYSTEM_ARM", &qemu)
            .current_dir(&runtime)
            .set_process_group(true)
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| format!("无法启动仿真后端：{e}"))?;
        let pid = child.pid();
        let (ready_tx, ready_rx) = mpsc::channel();
        let (exit_tx, exit_rx) = mpsc::channel();
        *self.exited.lock().unwrap() = Some(exit_rx);
        *child_owner = Some(child);
        drop(child_owner);

        let owner = self.clone();
        let handle = app.clone();
        let diagnostic_path = log_path.clone();
        std::thread::spawn(move || {
            let mut ready_tx = Some(ready_tx);
            let mut tail = VecDeque::new();
            while let Some(event) = events.blocking_recv() {
                match event {
                    CommandEvent::Stdout(line) => {
                        if let Some(tx) = ready_tx.take() {
                            let _ = tx.send(ready_url(&line, pid));
                        }
                    }
                    CommandEvent::Stderr(line) => {
                        let _ = log.write_all(&line);
                        let _ = log.write_all(b"\n");
                        tail.push_back(String::from_utf8_lossy(&line).into_owned());
                        if tail.len() > 12 {
                            tail.pop_front();
                        }
                    }
                    CommandEvent::Error(error) => {
                        let _ = writeln!(log, "{error}");
                        if let Some(tx) = ready_tx.take() {
                            let _ = tx.send(Err(error));
                        }
                    }
                    CommandEvent::Terminated(status) => {
                        let exit = status
                            .code
                            .map_or_else(|| "无退出码".to_owned(), |code| format!("退出码 {code}"));
                        let detail = format!(
                            "仿真后端已退出（{}）。\n{}\n日志：{}",
                            exit,
                            tail.into_iter().collect::<Vec<_>>().join("\n"),
                            diagnostic_path.display()
                        );
                        let starting = ready_tx.is_some();
                        if let Some(tx) = ready_tx.take() {
                            let _ = tx.send(Err(detail.clone()));
                        }
                        let _ = exit_tx.send(());
                        if !starting && !owner.closing.load(Ordering::Acquire) {
                            owner.shutdown();
                            show_failure(&handle, &detail);
                        }
                        return;
                    }
                    _ => {}
                }
            }
            let _ = exit_tx.send(());
        });
        let url = ready_rx
            .recv_timeout(START_LIMIT)
            .map_err(|_| format!("等待仿真后端启动超时。\n日志：{}", log_path.display()))??;
        if !self.closing.load(Ordering::Acquire) {
            app.get_webview_window("main")
                .ok_or("实验台窗口已关闭")?
                .navigate(url)
                .map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    pub fn shutdown(&self) {
        if self.closing.swap(true, Ordering::AcqRel) {
            return;
        }
        let mut child = self.child.lock().unwrap();
        if let Some(mut process) = child.take() {
            let _ = process.write(b"shutdown\n");
            let stopped = self
                .exited
                .lock()
                .unwrap()
                .take()
                .is_some_and(|rx| rx.recv_timeout(STOP_LIMIT).is_ok());
            if !stopped {
                let _ = process.kill();
            }
        }
    }
}

fn show_failure(app: &AppHandle, message: &str) {
    // A separate local page cannot be overwritten by the asynchronous backend
    // navigation when a process exits immediately after its readiness message.
    if app.get_webview_window("failure").is_some() {
        return;
    }
    let message = serde_json::to_string(message).unwrap();
    let script = format!(
        "addEventListener('DOMContentLoaded',()=>{{document.querySelector('h1').textContent='实验台未能继续运行';document.querySelector('p').textContent={message};}});"
    );
    if WebviewWindowBuilder::new(app, "failure", WebviewUrl::App("index.html".into()))
        .title("DM-MC02 Simulator — 启动与运行诊断")
        .inner_size(900.0, 640.0)
        .initialization_script(script)
        .build()
        .is_ok()
        && let Some(window) = app.get_webview_window("main")
    {
        let _ = window.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn message(url: &str, protocol: &str, pid: u32) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "event": "lab-ready", "protocol": protocol, "url": url, "pid": pid
        }))
        .unwrap()
    }
    #[test]
    fn accepts_matching_loopback_readiness() {
        assert_eq!(
            ready_url(&message("http://127.0.0.1:12345", PROTOCOL, 42), 42)
                .unwrap()
                .port(),
            Some(12345)
        );
    }
    #[test]
    fn rejects_wrong_peer_and_version() {
        assert!(ready_url(&message("http://127.0.0.1:12345", "v2", 42), 42).is_err());
        assert!(ready_url(&message("http://127.0.0.1:12345", PROTOCOL, 41), 42).is_err());
    }
    #[test]
    fn rejects_unexpected_locations() {
        for url in [
            "https://127.0.0.1:12345",
            "http://example.com:12345",
            "http://127.0.0.1",
            "http://127.0.0.1:0",
            "http://x@127.0.0.1:12345",
            "http://127.0.0.1:12345/path",
            "http://127.0.0.1:12345/?key=x",
        ] {
            assert!(ready_url(&message(url, PROTOCOL, 42), 42).is_err(), "{url}");
        }
    }
}
