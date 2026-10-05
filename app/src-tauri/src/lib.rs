//! Kosh desktop shell. The tax engine runs as a local child process (`python -m engine.rpc`)
//! that exchanges one JSON object per line over stdin/stdout. No network, no plugins: only the
//! Rust standard library starts and talks to the process.

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;

struct Engine {
    _child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

#[derive(Default)]
struct EngineState(Mutex<Option<Engine>>);

/// Python interpreter and working directory for the engine. Packaging a bundled interpreter is
/// Phase 5; until then these default to `python3` in the current directory.
fn spawn_engine() -> Result<Engine, String> {
    let python = std::env::var("KOSH_ENGINE_PYTHON").unwrap_or_else(|_| "python3".into());
    let mut command = Command::new(python);
    command
        .args(["-m", "engine.rpc"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    if let Ok(dir) = std::env::var("KOSH_ENGINE_DIR") {
        command.current_dir(dir);
    }
    let mut child = command.spawn().map_err(|e| format!("cannot start engine: {e}"))?;
    let stdin = child.stdin.take().ok_or("engine stdin unavailable")?;
    let stdout = child.stdout.take().ok_or("engine stdout unavailable")?;
    Ok(Engine { _child: child, stdin, stdout: BufReader::new(stdout) })
}

/// Send one JSON request line to the engine and return its JSON response line.
#[tauri::command]
fn engine_rpc(state: tauri::State<'_, EngineState>, request: String) -> Result<String, String> {
    let mut guard = state.0.lock().map_err(|_| "engine lock poisoned".to_string())?;
    if guard.is_none() {
        *guard = Some(spawn_engine()?);
    }
    let engine = guard.as_mut().ok_or("engine not running")?;
    let line = request.replace('\n', " ");
    let sent = writeln!(engine.stdin, "{line}").and_then(|_| engine.stdin.flush());
    let mut response = String::new();
    let read = sent.and_then(|_| engine.stdout.read_line(&mut response));
    match read {
        Ok(n) if n > 0 => Ok(response),
        _ => {
            *guard = None; // restart on the next request
            Err("engine process stopped".into())
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![engine_rpc])
        .run(tauri::generate_context!())
        .expect("error while running Kosh");
}
