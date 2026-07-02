// The cc-copilot GUI shell. On startup it spawns `cc-copilot serve` (the
// zero-dep JSON-RPC server over the Copilot facade) as a sidecar on an
// ephemeral port, reads the bound port from its stdout, and exposes it to the
// frontend via the `server_port` command. The Svelte frontend then drives the
// server over plain HTTP JSON-RPC at http://127.0.0.1:<port>/. On exit the
// sidecar is killed.
//
// The cc-copilot executable is resolved as: $CC_COPILOT_BIN env var, else the
// repo's `.venv/bin/python -m cccopilot serve` (dev). For a bundled app, set
// CC_COPILOT_BIN to the installed `cc-copilot` launcher at runtime.

use tauri::Manager;

use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

struct AppState {
    child: Mutex<Option<Child>>,
    port: u16,
}

#[tauri::command]
fn server_port(state: tauri::State<AppState>) -> u16 {
    state.port
}

fn parse_port(line: &str) -> Option<u16> {
    // "cc-copilot serve: listening on http://127.0.0.1:50198"
    line.split("127.0.0.1:")
        .nth(1)
        .and_then(|rest| rest.split(|c: char| !c.is_ascii_digit()).next())
        .and_then(|s| s.parse().ok())
}

fn spawn_serve() -> (Child, u16) {
    let manifest = env!("CARGO_MANIFEST_DIR"); // .../gui/src-tauri at compile time
    let repo = Path::new(manifest).join("../..");
    let default_bin = repo.join(".venv/bin/python");
    let bin = std::env::var("CC_COPILOT_BIN")
        .unwrap_or_else(|_| default_bin.to_string_lossy().into_owned());
    let mut cmd = Command::new(&bin);
    cmd.args(["-m", "cccopilot", "serve", "--port", "0"])
        .current_dir(&repo)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    let mut child = cmd.spawn().expect("failed to spawn cc-copilot serve");
    let stdout = child
        .stdout
        .take()
        .expect("cc-copilot serve had no stdout");
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(l) => {
                    if let Some(p) = parse_port(&l) {
                        let _ = tx.send(Ok(p));
                        return;
                    }
                }
                Err(_) => {
                    let _ = tx.send(Err(()));
                    return;
                }
            }
        }
        let _ = tx.send(Err(()));
    });
    let port = rx
        .recv_timeout(Duration::from_secs(15))
        .expect("cc-copilot serve did not start within 15s")
        .expect("cc-copilot serve did not report a port");
    (child, port)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let (child, port) = spawn_serve();
    tauri::Builder::default()
        .manage(AppState {
            child: Mutex::new(Some(child)),
            port,
        })
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![server_port])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            // kill the sidecar when the app exits so we never orphan a server
            if let tauri::RunEvent::ExitRequested { .. } = event {
                let state: tauri::State<AppState> = app.state();
                let taken = state.child.lock().unwrap().take();
                if let Some(mut child) = taken {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        });
}