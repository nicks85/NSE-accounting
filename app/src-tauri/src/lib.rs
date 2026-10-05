//! Kosh desktop shell. The tax engine runs as a local child process (`python -m engine.rpc`)
//! that exchanges one JSON object per line over stdin/stdout. No network, no plugins: only the
//! Rust standard library starts and talks to the process.

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// How long one request may take before the engine is considered stuck and restarted.
const TIMEOUT: Duration = Duration::from_secs(600);

struct Engine {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
}

impl Engine {
    /// Python interpreter and working directory for the engine. Packaging a bundled interpreter
    /// is Phase 5; until then these default to `python3` in the current directory.
    fn spawn() -> Result<Engine, String> {
        let python = std::env::var("KOSH_ENGINE_PYTHON").unwrap_or_else(|_| "python3".into());
        let mut command = Command::new(python);
        command
            .args(["-m", "engine.rpc"])
            .env("PYTHONUTF8", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        if let Ok(dir) = std::env::var("KOSH_ENGINE_DIR") {
            command.current_dir(dir);
        }
        let mut child = command.spawn().map_err(|e| format!("cannot start engine: {e}"))?;
        let stdin = child.stdin.take().ok_or_else(|| "engine stdin unavailable".to_string())?;
        let stdout = child.stdout.take().ok_or_else(|| "engine stdout unavailable".to_string())?;
        let (sender, lines) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(text) => {
                        if sender.send(text).is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(Engine { child, stdin, lines })
    }

    fn call(&mut self, request: &str) -> Result<String, String> {
        let line = request.replace(['\n', '\r'], " ");
        writeln!(self.stdin, "{line}")
            .and_then(|_| self.stdin.flush())
            .map_err(|e| format!("engine write failed: {e}"))?;
        self.lines
            .recv_timeout(TIMEOUT)
            .map_err(|e| format!("engine did not answer: {e}"))
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Default)]
struct EngineState(Arc<Mutex<Option<Engine>>>);

/// Send one JSON request line to the engine and return its JSON response line. Runs off the
/// main thread so a long calculation never freezes the window; a stuck or dead engine is
/// killed and restarted on the next request.
#[tauri::command]
async fn engine_rpc(state: tauri::State<'_, EngineState>, request: String) -> Result<String, String> {
    let shared = Arc::clone(&state.0);
    tauri::async_runtime::spawn_blocking(move || {
        let mut guard = shared.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if guard.is_none() {
            *guard = Some(Engine::spawn()?);
        }
        let engine = guard.as_mut().ok_or_else(|| "engine not running".to_string())?;
        match engine.call(&request) {
            Ok(response) => Ok(response),
            Err(error) => {
                *guard = None; // dropping the Engine kills the child process
                Err(error)
            }
        }
    })
    .await
    .map_err(|e| format!("engine task failed: {e}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![engine_rpc])
        .run(tauri::generate_context!())
        .expect("error while running Kosh");
}
