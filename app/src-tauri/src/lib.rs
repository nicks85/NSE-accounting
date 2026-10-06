//! Kosh desktop shell. The tax engine runs as a local child process — the bundled `kosh-engine`
//! sidecar (development builds may use `python -m engine.rpc`) — exchanging one JSON object per
//! line over stdin/stdout. No network: the only plugin is the native file dialog (local only).

use base64::Engine as _;
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
    /// The bundled `kosh-engine` sidecar next to the app executable.
    fn sidecar() -> Option<std::path::PathBuf> {
        let exe = std::env::current_exe().ok()?;
        let name = if cfg!(windows) { "kosh-engine.exe" } else { "kosh-engine" };
        let path = exe.parent()?.join(name);
        path.is_file().then_some(path)
    }

    /// Release builds run only the bundled sidecar: no environment overrides and no Python from
    /// PATH, so nothing outside the app can substitute the engine. Debug builds may use
    /// `KOSH_ENGINE_PYTHON` / `KOSH_ENGINE_DIR` to run `python -m engine.rpc` from a checkout.
    fn command() -> Result<Command, String> {
        if cfg!(debug_assertions) {
            if let Ok(python) = std::env::var("KOSH_ENGINE_PYTHON") {
                let mut command = Command::new(python);
                command.args(["-m", "engine.rpc"]);
                if let Ok(dir) = std::env::var("KOSH_ENGINE_DIR") {
                    command.current_dir(dir);
                }
                return Ok(command);
            }
        }
        Engine::sidecar()
            .map(Command::new)
            .ok_or_else(|| "the bundled engine (kosh-engine) is missing; reinstall Kosh".to_string())
    }

    fn spawn() -> Result<Engine, String> {
        let mut command = Engine::command()?;
        command
            .env("PYTHONUTF8", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
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
    tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
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

/// Largest export accepted for saving (base64 text of ~150 MB).
const MAX_SAVE_BASE64: usize = 200 * 1024 * 1024;

/// Save an export (ITR JSON or PDF) where the user chooses with the native "Save as" dialog.
/// Returns the saved path, or None if the user cancelled. The path comes from the dialog, never
/// from the web page, so the page can't write anywhere the user didn't pick.
#[tauri::command]
async fn save_file(
    app: tauri::AppHandle,
    name: String,
    data_base64: String,
) -> Result<Option<String>, String> {
    if data_base64.len() > MAX_SAVE_BASE64 {
        return Err("file too large to save".into());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data_base64.as_bytes())
        .map_err(|e| format!("bad file data: {e}"))?;
    tauri::async_runtime::spawn_blocking(move || -> Result<Option<String>, String> {
        use tauri_plugin_dialog::DialogExt;
        let Some(chosen) = app.dialog().file().set_file_name(name).blocking_save_file() else {
            return Ok(None);
        };
        let path = chosen.into_path().map_err(|e| format!("cannot save there: {e}"))?;
        std::fs::write(&path, bytes).map_err(|e| format!("cannot write {}: {e}", path.display()))?;
        Ok(Some(path.display().to_string()))
    })
    .await
    .map_err(|e| format!("save failed: {e}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![engine_rpc, save_file])
        .run(tauri::generate_context!())
        .expect("error while running Kosh");
}
