//! Chance Time desktop shell — process control for local bot + dashboard.
//!
//! Secrets stay on disk / .env under the project root. This app only spawns
//! `uv run chancetime ...` and opens http://127.0.0.1:8787.

use parking_lot::Mutex;
use serde::Serialize;
use std::io::{Read, Seek, SeekFrom};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, State,
};
use tauri_plugin_opener::OpenerExt;

const DASHBOARD_HOST: &str = "127.0.0.1";
const DASHBOARD_PORT: u16 = 8787;
const DASHBOARD_URL: &str = "http://127.0.0.1:8787";

/// True when system tray was created successfully (Linux needs libayatana-appindicator).
static TRAY_OK: AtomicBool = AtomicBool::new(false);

struct TrackedProc {
    child: Child,
    /// Process group id (Unix); same as child pid after setsid.
    pgid: u32,
}

struct ProcState {
    bot: Option<TrackedProc>,
    dash: Option<TrackedProc>,
    /// Path C: `chancetime crypto run …`
    crypto: Option<TrackedProc>,
    /// Path D: `chancetime exchange run …`
    exchange: Option<TrackedProc>,
    paper_mode: bool,
    project_root: PathBuf,
    last_bot_msg: String,
    last_dash_msg: String,
    last_crypto_msg: String,
    last_exchange_msg: String,
    /// "continuous" | "session" | ""
    bot_mode: String,
    bot_max_polls: Option<u32>,
    crypto_mode: String,
    crypto_max_polls: Option<u32>,
    crypto_paper_strategy: bool,
    exchange_mode: String,
    exchange_max_polls: Option<u32>,
    exchange_trade_signals: bool,
}

impl ProcState {
    fn new(project_root: PathBuf) -> Self {
        Self {
            bot: None,
            dash: None,
            crypto: None,
            exchange: None,
            paper_mode: true,
            project_root,
            last_bot_msg: String::new(),
            last_dash_msg: String::new(),
            last_crypto_msg: String::new(),
            last_exchange_msg: String::new(),
            bot_mode: String::new(),
            bot_max_polls: None,
            crypto_mode: String::new(),
            crypto_max_polls: None,
            crypto_paper_strategy: false,
            exchange_mode: String::new(),
            exchange_max_polls: None,
            exchange_trade_signals: false,
        }
    }
}

type Shared = Arc<Mutex<ProcState>>;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct StatusPayload {
    project_root: String,
    paper_mode: bool,
    bot_running: bool,
    dashboard_running: bool,
    dashboard_port_open: bool,
    dashboard_url: String,
    tray_ok: bool,
    last_bot_msg: String,
    last_dash_msg: String,
    bot_mode: String,
    bot_max_polls: Option<u32>,
    crypto_running: bool,
    exchange_running: bool,
    last_crypto_msg: String,
    last_exchange_msg: String,
    crypto_mode: String,
    crypto_max_polls: Option<u32>,
    crypto_paper_strategy: bool,
    exchange_mode: String,
    exchange_max_polls: Option<u32>,
    exchange_trade_signals: bool,
}

fn resolve_project_root() -> PathBuf {
    if let Ok(p) = std::env::var("CHANCETIME_ROOT") {
        let pb = PathBuf::from(p);
        if pb.is_dir() {
            return pb;
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        for dir in cwd.ancestors() {
            let py = dir.join("pyproject.toml");
            if py.is_file() {
                if let Ok(txt) = std::fs::read_to_string(&py) {
                    if txt.contains("name = \"chancetime\"") || txt.contains("name = 'chancetime'")
                    {
                        return dir.to_path_buf();
                    }
                }
            }
        }
        return cwd;
    }
    PathBuf::from(".")
}

fn desktop_log_dir(root: &Path) -> PathBuf {
    let dir = root.join("data").join("desktop-logs");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

fn port_open(host: &str, port: u16) -> bool {
    let addr: SocketAddr = match format!("{host}:{port}").parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok()
}

fn tail_file(path: &Path, max_bytes: u64) -> String {
    let Ok(mut f) = std::fs::File::open(path) else {
        return String::new();
    };
    let Ok(meta) = f.metadata() else {
        return String::new();
    };
    let len = meta.len();
    let start = len.saturating_sub(max_bytes);
    if f.seek(SeekFrom::Start(start)).is_err() {
        return String::new();
    }
    let mut buf = String::new();
    let _ = f.read_to_string(&mut buf);
    // If we started mid-line, drop first partial line
    if start > 0 {
        if let Some(i) = buf.find('\n') {
            buf = buf[i + 1..].to_string();
        }
    }
    buf
}

fn last_nonzero_lines(path: &Path, n: usize) -> String {
    let text = tail_file(path, 12_000);
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    let start = lines.len().saturating_sub(n);
    lines[start..].join("\n")
}

/// Resolve how to run the CLI.
///
/// Prefer **live project code** (``.venv`` / ``uv``) over a frozen sidecar so
/// a stale ``desktop/sidecar/chancetime-cli`` does not keep writing the old
/// single DB or serving the pre–dual-book dashboard.
///
/// Order:
/// 1. ``CHANCETIME_BIN`` (explicit override)
/// 2. ``.venv/bin/chancetime`` console script (correct Typer entry)
/// 3. ``.venv/bin/python -m chancetime`` (needs package ``__main__.py``)
/// 4. ``uv run chancetime``
/// 5. sidecar only if ``CHANCETIME_USE_SIDECAR=1``
fn resolve_runner(root: &Path) -> (String, Vec<String>) {
    if let Ok(bin) = std::env::var("CHANCETIME_BIN") {
        let p = PathBuf::from(bin.trim());
        if p.is_file() {
            return (p.display().to_string(), vec![]);
        }
    }
    // Console script from `uv sync` / pip install -e .
    let venv_cli = root.join(".venv/bin/chancetime");
    if venv_cli.is_file() {
        return (venv_cli.display().to_string(), vec![]);
    }
    let venv_py = root.join(".venv/bin/python");
    if venv_py.is_file() {
        return (
            venv_py.display().to_string(),
            vec!["-m".into(), "chancetime".into()],
        );
    }
    if which_cmd("uv") {
        return ("uv".into(), vec!["run".into(), "chancetime".into()]);
    }
    let use_sidecar = std::env::var("CHANCETIME_USE_SIDECAR")
        .map(|v| matches!(v.as_str(), "1" | "true" | "yes"))
        .unwrap_or(false);
    if use_sidecar {
        for candidate in [
            root.join("desktop/sidecar/chancetime-cli"),
            root.join("sidecar/chancetime-cli"),
        ] {
            if candidate.is_file() {
                return (candidate.display().to_string(), vec![]);
            }
        }
    }
    ("uv".into(), vec!["run".into(), "chancetime".into()])
}

fn which_cmd(name: &str) -> bool {
    std::env::var_os("PATH")
        .map(|paths| {
            std::env::split_paths(&paths).any(|dir| {
                let p = dir.join(name);
                p.is_file()
            })
        })
        .unwrap_or(false)
}

fn spawn_chancetime(root: &Path, cli_args: &[&str], log_stem: &str) -> Result<TrackedProc, String> {
    let log_dir = desktop_log_dir(root);
    let out_path = log_dir.join(format!("{log_stem}.stdout.log"));
    let err_path = log_dir.join(format!("{log_stem}.stderr.log"));
    let stdout = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&out_path)
        .map(Stdio::from)
        .unwrap_or_else(|_| Stdio::null());
    let stderr = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&err_path)
        .map(Stdio::from)
        .unwrap_or_else(|_| Stdio::null());

    let (prog, prefix) = resolve_runner(root);
    let sep = format!(
        "\n----- spawn {} {} runner={} {:?} args={:?} -----\n",
        log_stem,
        chrono_like_now(),
        prog,
        prefix,
        cli_args
    );
    let _ = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&err_path)
        .and_then(|mut f| {
            use std::io::Write;
            f.write_all(sep.as_bytes())
        });
    let mut cmd = Command::new(&prog);
    cmd.args(&prefix)
        .args(cli_args)
        .current_dir(root)
        .stdout(stdout)
        .stderr(stderr)
        .stdin(Stdio::null());

    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        unsafe {
            cmd.pre_exec(|| {
                if libc::setsid() == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }

    let child = cmd.spawn().map_err(|e| {
        format!(
            "failed to spawn `{prog} {:?} {:?}` from {}: {e}",
            prefix,
            cli_args,
            root.display()
        )
    })?;
    let pgid = child.id();
    Ok(TrackedProc { child, pgid })
}

/// Minimal timestamp without extra chrono dep.
fn chrono_like_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("unix={secs}")
}

fn child_running(proc: &mut Option<TrackedProc>) -> bool {
    match proc {
        None => false,
        Some(p) => match p.child.try_wait() {
            Ok(None) => true,
            Ok(Some(_)) => {
                *proc = None;
                false
            }
            Err(_) => {
                *proc = None;
                false
            }
        },
    }
}

fn kill_tracked(proc: &mut Option<TrackedProc>) {
    if let Some(mut p) = proc.take() {
        let pid = p.child.id();
        #[cfg(unix)]
        {
            // Negative pgid = whole process group (works when we used setsid)
            let pg = p.pgid as i32;
            unsafe {
                let _ = libc::kill(-pg, libc::SIGTERM);
                let _ = libc::kill(pid as i32, libc::SIGTERM);
            }
            std::thread::sleep(Duration::from_millis(300));
            unsafe {
                let _ = libc::kill(-pg, libc::SIGKILL);
                let _ = libc::kill(pid as i32, libc::SIGKILL);
            }
        }
        #[cfg(not(unix))]
        {
            let _ = p.child.kill();
        }
        let _ = p.child.wait();
    }
}

/// Pattern matching leftover bot/dashboard processes (venv, uv, python -m, sidecar).
const ORPHAN_AWK: &str = r#"/[.]venv[/]bin[/]chancetime|[.]venv[/]bin[/]python.*chancetime|python[0-9.]* -m chancetime|chancetime-cli|[/]uv run chancetime|uv run python.*chancetime/ && !/awk/ && !/chancetime-desktop/"#;

/// Kill orphan chancetime bot/dashboard processes not tracked by this shell.
/// Previous desktop restarts leave python/sidecar children holding port 8787.
fn kill_orphans_by_name() -> String {
    #[cfg(unix)]
    {
        let script = format!(
            r#"
set +e
ps -eo pid=,cmd= | awk '{pat} {{print $1}}' | while read -r p; do
  kill -TERM "$p" 2>/dev/null
done
sleep 0.35
ps -eo pid=,cmd= | awk '{pat} {{print $1}}' | while read -r p; do
  kill -KILL "$p" 2>/dev/null
done
# Broad pkill fallbacks (cmdline variants)
pkill -TERM -f 'chancetime run' 2>/dev/null
pkill -TERM -f 'chancetime-cli run' 2>/dev/null
pkill -TERM -f 'python -m chancetime' 2>/dev/null
pkill -TERM -f 'uv run chancetime' 2>/dev/null
sleep 0.2
pkill -KILL -f 'chancetime run' 2>/dev/null
pkill -KILL -f 'chancetime-cli run' 2>/dev/null
pkill -KILL -f 'python -m chancetime' 2>/dev/null
pkill -KILL -f 'uv run chancetime' 2>/dev/null
# Free API port
if command -v fuser >/dev/null 2>&1; then
  fuser -k {port}/tcp >/dev/null 2>&1
fi
ss -lptn "sport = :{port}" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | while read -r p; do
  kill -KILL "$p" 2>/dev/null
done
"#,
            pat = ORPHAN_AWK,
            port = DASHBOARD_PORT
        );
        let _ = Command::new("sh").args(["-c", &script]).status();
        let still = list_orphan_pids();
        let port = port_open(DASHBOARD_HOST, DASHBOARD_PORT);
        if still.is_empty() && !port {
            "orphans cleaned; no leftovers; port 8787 free".into()
        } else if still.is_empty() {
            format!("orphans cleaned; port 8787 {}", if port { "STILL OPEN" } else { "free" })
        } else {
            format!(
                "orphans cleaned; STILL ALIVE pids=[{}] port={}",
                still.join(","),
                if port { "open" } else { "free" }
            )
        }
    }
    #[cfg(not(unix))]
    {
        "orphan cleanup (unix only)".into()
    }
}

/// PIDs that still look like a Chance Time bot/dashboard after kill attempts.
fn list_orphan_pids() -> Vec<String> {
    #[cfg(unix)]
    {
        let script = format!(
            r#"ps -eo pid=,cmd= | awk '{pat} {{print $1}}'"#,
            pat = ORPHAN_AWK
        );
        let out = Command::new("sh")
            .args(["-c", &script])
            .output()
            .ok();
        let Some(out) = out else {
            return vec![];
        };
        String::from_utf8_lossy(&out.stdout)
            .lines()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .collect()
    }
    #[cfg(not(unix))]
    {
        vec![]
    }
}

/// After stop/kill: confirm nothing remains; surface leftover PIDs in UI.
fn verify_bots_dead() -> String {
    let still = list_orphan_pids();
    if still.is_empty() {
        "verified: no chancetime bot/dashboard processes".into()
    } else {
        format!(
            "WARNING still alive: pids=[{}] — check: pgrep -af chancetime",
            still.join(",")
        )
    }
}

fn refresh_tray_tooltip(app: &AppHandle) {
    let st = app.state::<Shared>();
    let payload = status_inner(&st);
    let tip = if payload.bot_running {
        format!(
            "Chance Time — BOT RUNNING ({}) — Quit tray menu to stop",
            if payload.bot_mode.is_empty() {
                "continuous"
            } else {
                &payload.bot_mode
            }
        )
    } else {
        "Chance Time — bot stopped".into()
    };
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_tooltip(Some(tip));
    }
}

/// After spawn, wait briefly; if process died, return log snippet as error.
fn verify_still_alive(
    proc: &mut Option<TrackedProc>,
    root: &Path,
    log_stem: &str,
    grace_ms: u64,
) -> Result<(), String> {
    std::thread::sleep(Duration::from_millis(grace_ms));
    if child_running(proc) {
        return Ok(());
    }
    let err_path = desktop_log_dir(root).join(format!("{log_stem}.stderr.log"));
    let out_path = desktop_log_dir(root).join(format!("{log_stem}.stdout.log"));
    let tail = last_nonzero_lines(&err_path, 12);
    let tail_out = last_nonzero_lines(&out_path, 6);
    let mut msg = format!("{log_stem} exited immediately.");
    if !tail.is_empty() {
        msg.push_str("\n--- stderr ---\n");
        msg.push_str(&tail);
    }
    if !tail_out.is_empty() {
        msg.push_str("\n--- stdout ---\n");
        msg.push_str(&tail_out);
    }
    if tail.contains("address already in use") {
        msg.push_str("\n(hint: something already holds the port — try Stop/Kill all, or free 8787)");
    }
    Err(msg)
}

fn status_inner(state: &Shared) -> StatusPayload {
    let mut s = state.lock();
    let bot = child_running(&mut s.bot);
    let mut dash = child_running(&mut s.dash);
    let crypto = child_running(&mut s.crypto);
    let exchange = child_running(&mut s.exchange);
    let port = port_open(DASHBOARD_HOST, DASHBOARD_PORT);
    // Port open counts as dashboard "up" even if we lost the Child handle
    if port {
        dash = true;
    }
    if !bot {
        s.bot_mode.clear();
        s.bot_max_polls = None;
    }
    if !crypto {
        s.crypto_mode.clear();
        s.crypto_max_polls = None;
    }
    if !exchange {
        s.exchange_mode.clear();
        s.exchange_max_polls = None;
    }
    StatusPayload {
        project_root: s.project_root.display().to_string(),
        paper_mode: s.paper_mode,
        bot_running: bot,
        dashboard_running: dash,
        dashboard_port_open: port,
        dashboard_url: DASHBOARD_URL.to_string(),
        tray_ok: TRAY_OK.load(Ordering::Relaxed),
        last_bot_msg: s.last_bot_msg.clone(),
        last_dash_msg: s.last_dash_msg.clone(),
        bot_mode: s.bot_mode.clone(),
        bot_max_polls: s.bot_max_polls,
        crypto_running: crypto,
        exchange_running: exchange,
        last_crypto_msg: s.last_crypto_msg.clone(),
        last_exchange_msg: s.last_exchange_msg.clone(),
        crypto_mode: s.crypto_mode.clone(),
        crypto_max_polls: s.crypto_max_polls,
        crypto_paper_strategy: s.crypto_paper_strategy,
        exchange_mode: s.exchange_mode.clone(),
        exchange_max_polls: s.exchange_max_polls,
        exchange_trade_signals: s.exchange_trade_signals,
    }
}

fn start_dashboard_inner(state: &Shared) -> Result<String, String> {
    {
        let mut s = state.lock();
        if child_running(&mut s.dash) {
            s.last_dash_msg = "already running (child)".into();
            return Ok("dashboard already running".into());
        }
    }
    // Stale servers serve old HTML (e.g. canvas chart). Free port and respawn fresh.
    if port_open(DASHBOARD_HOST, DASHBOARD_PORT) {
        let note = kill_orphans_by_name();
        std::thread::sleep(Duration::from_millis(400));
        if port_open(DASHBOARD_HOST, DASHBOARD_PORT) {
            return Err(format!(
                "port 8787 busy after cleanup ({note}). Run: fuser -k 8787/tcp"
            ));
        }
    }

    let root = {
        let s = state.lock();
        s.project_root.clone()
    };

    let child = spawn_chancetime(
        &root,
        &[
            "dashboard",
            "--host",
            DASHBOARD_HOST,
            "--port",
            &DASHBOARD_PORT.to_string(),
        ],
        "dashboard",
    )?;

    {
        let mut s = state.lock();
        s.dash = Some(child);
    }

    // uvicorn takes a moment; verify process + optionally port
    let alive = {
        let mut s = state.lock();
        let r = verify_still_alive(&mut s.dash, &root, "dashboard", 800);
        if let Err(ref e) = r {
            s.last_dash_msg = e.clone();
        }
        r
    };
    alive?;

    // Wait a bit more for bind
    for _ in 0..15 {
        if port_open(DASHBOARD_HOST, DASHBOARD_PORT) {
            let msg = format!("dashboard up at {DASHBOARD_URL}");
            state.lock().last_dash_msg = msg.clone();
            return Ok(msg);
        }
        std::thread::sleep(Duration::from_millis(200));
        let mut s = state.lock();
        if !child_running(&mut s.dash) {
            let err = verify_still_alive(&mut s.dash, &root, "dashboard", 0)
                .err()
                .unwrap_or_else(|| "dashboard died during bind".into());
            s.last_dash_msg = err.clone();
            return Err(err);
        }
    }

    let msg = format!(
        "dashboard process running; waiting for bind on {DASHBOARD_URL} (check data/desktop-logs/)"
    );
    state.lock().last_dash_msg = msg.clone();
    Ok(msg)
}

fn stop_dashboard_inner(state: &Shared) -> Result<String, String> {
    {
        let mut s = state.lock();
        kill_tracked(&mut s.dash);
    }
    // Always try to free 8787 — orphans from prior sessions are common
    let orphan = kill_orphans_by_name();
    let mut s = state.lock();
    if port_open(DASHBOARD_HOST, DASHBOARD_PORT) {
        s.last_dash_msg = format!("dashboard stop incomplete; {orphan}");
        return Ok(s.last_dash_msg.clone());
    }
    s.last_dash_msg = format!("dashboard stopped ({orphan})");
    Ok(s.last_dash_msg.clone())
}

fn start_bot_inner(
    state: &Shared,
    config: Option<String>,
    account: Option<String>,
    max_polls: Option<u32>,
) -> Result<String, String> {
    {
        let mut s = state.lock();
        if child_running(&mut s.bot) {
            s.last_bot_msg = "already running".into();
            return Ok("bot already running".into());
        }
    }
    let root = {
        let s = state.lock();
        s.project_root.clone()
    };
    let cfg = config.unwrap_or_else(|| "config/default.yaml".into());
    let mut args: Vec<String> = vec!["run".into()];
    let has_account = account.as_ref().map(|a| !a.is_empty()).unwrap_or(false);
    if has_account {
        args.push("--account".into());
        args.push(account.clone().unwrap_or_default());
    }
    if !has_account {
        args.push("--config".into());
        args.push(cfg.clone());
    } else if cfg != "config/default.yaml" {
        args.push("--config".into());
        args.push(cfg.clone());
    }
    // Session mode: stop after N polls. Continuous: omit --max-polls (runs until Stop).
    // Note: poll_interval_seconds in YAML is wait *between* polls, not total run time.
    let mode = if let Some(n) = max_polls {
        if n > 0 {
            args.push("--max-polls".into());
            args.push(n.to_string());
            "session"
        } else {
            "continuous"
        }
    } else {
        "continuous"
    };
    let arg_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    let child = spawn_chancetime(&root, &arg_refs, "bot")?;
    {
        let mut s = state.lock();
        s.bot = Some(child);
        s.bot_mode = mode.into();
        s.bot_max_polls = max_polls.filter(|n| *n > 0);
    }
    {
        let mut s = state.lock();
        if let Err(e) = verify_still_alive(&mut s.bot, &root, "bot", 600) {
            s.last_bot_msg = e.clone();
            s.bot_mode.clear();
            s.bot_max_polls = None;
            return Err(e);
        }
    }
    let msg = match (mode, max_polls) {
        ("session", Some(n)) => format!(
            "bot SESSION: {n} polls then stop (account={})",
            account.as_deref().unwrap_or("-")
        ),
        _ => format!(
            "bot CONTINUOUS until Stop (account={})",
            account.as_deref().unwrap_or("-")
        ),
    };
    state.lock().last_bot_msg = msg.clone();
    Ok(msg)
}

/// Stop bot + full orphan sweep (same cleanup as kill for bot processes).
fn stop_bot_inner(state: &Shared) -> Result<String, String> {
    {
        let mut s = state.lock();
        kill_tracked(&mut s.bot);
        s.bot_mode.clear();
        s.bot_max_polls = None;
    }
    // Same breadth as Kill all for bot orphans (previous sessions, uv/python variants)
    let orphan = kill_orphans_by_name();
    let verify = verify_bots_dead();
    let mut s = state.lock();
    s.last_bot_msg = format!("bot stopped — {orphan}; {verify}");
    Ok(s.last_bot_msg.clone())
}

fn kill_all_inner(state: &Shared) -> Result<String, String> {
    {
        let mut s = state.lock();
        kill_tracked(&mut s.bot);
        kill_tracked(&mut s.dash);
        kill_tracked(&mut s.crypto);
        kill_tracked(&mut s.exchange);
        s.bot_mode.clear();
        s.bot_max_polls = None;
        s.crypto_mode.clear();
        s.crypto_max_polls = None;
        s.exchange_mode.clear();
        s.exchange_max_polls = None;
        s.last_crypto_msg = "stopped (kill all)".into();
        s.last_exchange_msg = "stopped (kill all)".into();
    }
    let orphan = kill_orphans_by_name();
    let verify = verify_bots_dead();
    let mut s = state.lock();
    s.last_bot_msg = format!("killed — {verify}");
    s.last_dash_msg = orphan.clone();
    let port = port_open(DASHBOARD_HOST, DASHBOARD_PORT);
    Ok(format!(
        "kill all: tracked + orphans ({orphan}); {verify}; port 8787 {}",
        if port { "STILL OPEN" } else { "free" }
    ))
}

fn start_crypto_session_inner(
    state: &Shared,
    max_polls: Option<u32>,
    paper_strategy: bool,
    interval: Option<f64>,
) -> Result<String, String> {
    {
        let mut s = state.lock();
        if child_running(&mut s.crypto) {
            s.last_crypto_msg = "already running".into();
            return Ok("crypto session already running".into());
        }
        // Drop stale tracked handle if child already dead
        let _ = child_running(&mut s.crypto);
    }
    let root = state.lock().project_root.clone();
    // Kill orphan crypto sessions from prior desktop/CLI restarts (same paper DB)
    let _ = Command::new("pkill")
        .args(["-f", "chancetime crypto run"])
        .status();
    std::thread::sleep(Duration::from_millis(400));
    let mut args: Vec<String> = vec!["crypto".into(), "run".into()];
    let mode = if let Some(n) = max_polls {
        if n > 0 {
            args.push("--max-polls".into());
            args.push(n.to_string());
            "session"
        } else {
            "continuous"
        }
    } else {
        "continuous"
    };
    let iv = interval.unwrap_or(15.0);
    args.push("--interval".into());
    args.push(iv.to_string());
    if paper_strategy {
        args.push("--paper-strategy".into());
    }
    let arg_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    let child = spawn_chancetime(&root, &arg_refs, "crypto")?;
    {
        let mut s = state.lock();
        s.crypto = Some(child);
        s.crypto_mode = mode.into();
        s.crypto_max_polls = max_polls.filter(|n| *n > 0);
        s.crypto_paper_strategy = paper_strategy;
    }
    {
        let mut s = state.lock();
        if let Err(e) = verify_still_alive(&mut s.crypto, &root, "crypto", 800) {
            s.last_crypto_msg = e.clone();
            s.crypto_mode.clear();
            s.crypto_max_polls = None;
            return Err(e);
        }
    }
    let msg = match (mode, max_polls, paper_strategy) {
        ("session", Some(n), true) => {
            format!("crypto SESSION {n} polls · paper-strategy · interval={iv}s")
        }
        ("session", Some(n), false) => {
            format!("crypto SESSION {n} polls · shadow · interval={iv}s")
        }
        (_, _, true) => format!("crypto CONTINUOUS · paper-strategy · interval={iv}s"),
        _ => format!("crypto CONTINUOUS · shadow · interval={iv}s"),
    };
    state.lock().last_crypto_msg = msg.clone();
    Ok(msg)
}

fn stop_crypto_session_inner(state: &Shared) -> Result<String, String> {
    let mut s = state.lock();
    kill_tracked(&mut s.crypto);
    s.crypto_mode.clear();
    s.crypto_max_polls = None;
    s.last_crypto_msg = "crypto session stopped".into();
    Ok(s.last_crypto_msg.clone())
}

fn start_exchange_session_inner(
    state: &Shared,
    max_polls: Option<u32>,
    trade_signals: bool,
    interval: Option<f64>,
) -> Result<String, String> {
    {
        let mut s = state.lock();
        if child_running(&mut s.exchange) {
            s.last_exchange_msg = "already running".into();
            return Ok("exchange session already running".into());
        }
    }
    let root = state.lock().project_root.clone();
    let mut args: Vec<String> = vec!["exchange".into(), "run".into()];
    let mode = if let Some(n) = max_polls {
        if n > 0 {
            args.push("--max-polls".into());
            args.push(n.to_string());
            "session"
        } else {
            "continuous"
        }
    } else {
        "continuous"
    };
    let iv = interval.unwrap_or(20.0);
    args.push("--interval".into());
    args.push(iv.to_string());
    if trade_signals {
        args.push("--trade-signals".into());
    }
    let arg_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    let child = spawn_chancetime(&root, &arg_refs, "exchange")?;
    {
        let mut s = state.lock();
        s.exchange = Some(child);
        s.exchange_mode = mode.into();
        s.exchange_max_polls = max_polls.filter(|n| *n > 0);
        s.exchange_trade_signals = trade_signals;
    }
    {
        let mut s = state.lock();
        if let Err(e) = verify_still_alive(&mut s.exchange, &root, "exchange", 800) {
            s.last_exchange_msg = e.clone();
            s.exchange_mode.clear();
            s.exchange_max_polls = None;
            return Err(e);
        }
    }
    let msg = match (mode, max_polls, trade_signals) {
        ("session", Some(n), true) => {
            format!("exchange SESSION {n} polls · trade-signals · interval={iv}s")
        }
        ("session", Some(n), false) => {
            format!("exchange SESSION {n} polls · shadow · interval={iv}s")
        }
        (_, _, true) => format!("exchange CONTINUOUS · trade-signals · interval={iv}s"),
        _ => format!("exchange CONTINUOUS · shadow · interval={iv}s"),
    };
    state.lock().last_exchange_msg = msg.clone();
    Ok(msg)
}

fn stop_exchange_session_inner(state: &Shared) -> Result<String, String> {
    let mut s = state.lock();
    kill_tracked(&mut s.exchange);
    s.exchange_mode.clear();
    s.exchange_max_polls = None;
    s.last_exchange_msg = "exchange session stopped".into();
    Ok(s.last_exchange_msg.clone())
}

/// Run project CLI and capture stdout (for knobs / doctor).
/// Hard timeout so Ops buttons never spin forever if a child hangs.
fn cli_capture(root: &Path, cli_args: &[&str]) -> Result<String, String> {
    cli_capture_timeout(root, cli_args, Duration::from_secs(45))
}

fn cli_capture_timeout(
    root: &Path,
    cli_args: &[&str],
    timeout: Duration,
) -> Result<String, String> {
    use std::io::Read;
    use std::process::Stdio;
    use std::thread;
    use std::time::Instant;

    let (prog, prefix) = resolve_runner(root);
    let mut cmd = Command::new(&prog);
    cmd.args(&prefix)
        .args(cli_args)
        .current_dir(root)
        .env("CHANCETIME_QUIET", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to run {prog}: {e}"))?;
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let mut stdout = String::new();
                let mut stderr = String::new();
                if let Some(mut out) = child.stdout.take() {
                    let _ = out.read_to_string(&mut stdout);
                }
                if let Some(mut err) = child.stderr.take() {
                    let _ = err.read_to_string(&mut stderr);
                }
                let stdout = stdout.trim().to_string();
                let stderr = stderr.trim().to_string();
                if !status.success() {
                    return Err(format!(
                        "chancetime {:?} exit {}: {}\n{}",
                        cli_args, status, stderr, stdout
                    ));
                }
                return Ok(extract_json_payload(&stdout).unwrap_or(stdout));
            }
            Ok(None) if start.elapsed() >= timeout => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!(
                    "chancetime {:?} timed out after {}s",
                    cli_args,
                    timeout.as_secs()
                ));
            }
            Ok(None) => thread::sleep(Duration::from_millis(40)),
            Err(e) => return Err(format!("wait failed: {e}")),
        }
    }
}

/// If CLI mixed log lines with JSON, keep from first `{{` or `[`.
fn extract_json_payload(s: &str) -> Option<String> {
    let start = s.find(['{', '['])?;
    Some(s[start..].trim().to_string())
}

#[tauri::command]
fn get_status(state: State<'_, Shared>) -> StatusPayload {
    status_inner(&state)
}

#[tauri::command]
fn start_dashboard(state: State<'_, Shared>) -> Result<String, String> {
    start_dashboard_inner(&state)
}

#[tauri::command]
fn stop_dashboard(state: State<'_, Shared>) -> Result<String, String> {
    stop_dashboard_inner(&state)
}

#[tauri::command]
fn start_bot(
    app: AppHandle,
    state: State<'_, Shared>,
    config: Option<String>,
    account: Option<String>,
    max_polls: Option<u32>,
) -> Result<String, String> {
    let msg = start_bot_inner(&state, config, account, max_polls)?;
    refresh_tray_tooltip(&app);
    Ok(msg)
}

#[tauri::command]
fn stop_bot(app: AppHandle, state: State<'_, Shared>) -> Result<String, String> {
    let msg = stop_bot_inner(&state)?;
    refresh_tray_tooltip(&app);
    Ok(msg)
}

#[tauri::command]
fn kill_all(app: AppHandle, state: State<'_, Shared>) -> Result<String, String> {
    let msg = kill_all_inner(&state)?;
    refresh_tray_tooltip(&app);
    Ok(msg)
}

#[tauri::command]
fn set_paper_indicator(state: State<'_, Shared>, paper: bool) -> Result<String, String> {
    let mut s = state.lock();
    s.paper_mode = paper;
    Ok(if paper {
        "indicator: PAPER".into()
    } else {
        "indicator: LIVE (shell does not flip secrets; use .env + CLI risk ack)".into()
    })
}

#[tauri::command]
fn open_dashboard(app: AppHandle) -> Result<(), String> {
    if !port_open(DASHBOARD_HOST, DASHBOARD_PORT) {
        return Err(
            "dashboard port 8787 not open — click Start dashboard first".into(),
        );
    }
    app.opener()
        .open_url(DASHBOARD_URL, None::<&str>)
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn get_logs(state: State<'_, Shared>, which: String, lines: Option<usize>) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    let n = lines.unwrap_or(40).clamp(5, 200);
    let stem = match which.as_str() {
        "bot" | "dashboard" | "crypto" | "exchange" => which.as_str(),
        _ => {
            return Err(
                "which must be 'bot', 'dashboard', 'crypto', or 'exchange'".into(),
            )
        }
    };
    let err_path = desktop_log_dir(&root).join(format!("{stem}.stderr.log"));
    let out_path = desktop_log_dir(&root).join(format!("{stem}.stdout.log"));
    let mut parts = Vec::new();
    let e = last_nonzero_lines(&err_path, n);
    let o = last_nonzero_lines(&out_path, n.min(30));
    if !e.is_empty() {
        parts.push(format!("=== {stem} stderr ===\n{e}"));
    }
    if !o.is_empty() {
        parts.push(format!("=== {stem} stdout ===\n{o}"));
    }
    if parts.is_empty() {
        Ok(format!("(no {stem} logs yet under data/desktop-logs/)"))
    } else {
        Ok(parts.join("\n\n"))
    }
}

#[tauri::command]
fn start_crypto_session(
    state: State<'_, Shared>,
    max_polls: Option<u32>,
    paper_strategy: Option<bool>,
    interval: Option<f64>,
) -> Result<String, String> {
    start_crypto_session_inner(
        &state,
        max_polls,
        paper_strategy.unwrap_or(false),
        interval,
    )
}

#[tauri::command]
fn stop_crypto_session(state: State<'_, Shared>) -> Result<String, String> {
    stop_crypto_session_inner(&state)
}

#[tauri::command]
fn start_exchange_session(
    state: State<'_, Shared>,
    max_polls: Option<u32>,
    trade_signals: Option<bool>,
    interval: Option<f64>,
) -> Result<String, String> {
    start_exchange_session_inner(
        &state,
        max_polls,
        trade_signals.unwrap_or(false),
        interval,
    )
}

#[tauri::command]
fn stop_exchange_session(state: State<'_, Shared>) -> Result<String, String> {
    stop_exchange_session_inner(&state)
}

#[tauri::command]
fn get_user_knobs(state: State<'_, Shared>) -> Result<serde_json::Value, String> {
    let root = state.lock().project_root.clone();
    let txt = cli_capture(&root, &["user-config", "snapshot"])?;
    serde_json::from_str(&txt).map_err(|e| format!("parse knobs snapshot: {e}\n{txt}"))
}

#[tauri::command]
fn save_user_knobs_cmd(
    state: State<'_, Shared>,
    knobs: serde_json::Value,
) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    let payload = serde_json::to_string(&knobs).map_err(|e| e.to_string())?;
    // Write temp file to avoid shell escaping issues
    let tmp = root.join("data/desktop-logs/knobs-apply.json");
    if let Some(parent) = tmp.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    std::fs::write(&tmp, &payload).map_err(|e| e.to_string())?;
    let out = cli_capture(
        &root,
        &[
            "user-config",
            "apply",
            "--file",
            tmp.to_str().unwrap_or("data/desktop-logs/knobs-apply.json"),
        ],
    )?;
    Ok(out)
}

#[tauri::command]
fn run_doctor(state: State<'_, Shared>) -> Result<serde_json::Value, String> {
    let root = state.lock().project_root.clone();
    let txt = cli_capture(&root, &["doctor", "--json"])?;
    serde_json::from_str(&txt).map_err(|e| format!("parse doctor: {e}\n{txt}"))
}

#[tauri::command]
fn list_accounts_cmd(state: State<'_, Shared>) -> Result<serde_json::Value, String> {
    let root = state.lock().project_root.clone();
    let txt = cli_capture(&root, &["accounts"])?;
    // Text lines → JSON array for UI
    let rows: Vec<String> = txt.lines().map(|l| l.to_string()).collect();
    Ok(serde_json::json!({ "lines": rows, "raw": txt }))
}

#[tauri::command]
fn run_digest_cmd(
    state: State<'_, Shared>,
    account: Option<String>,
    send: Option<bool>,
) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    let acct = account.unwrap_or_else(|| "paper".into());
    if send.unwrap_or(false) {
        cli_capture(
            &root,
            &["digest", "--account", acct.as_str(), "--send"],
        )
    } else {
        cli_capture(&root, &["digest", "--account", acct.as_str()])
    }
}

#[tauri::command]
fn run_export_cmd(
    state: State<'_, Shared>,
    account: Option<String>,
    year: Option<i32>,
) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    let acct = account.unwrap_or_else(|| "paper".into());
    let mut args = vec!["export".to_string(), "--account".into(), acct];
    if let Some(y) = year {
        args.push("--year".into());
        args.push(y.to_string());
    }
    let owned: Vec<String> = args;
    let refs: Vec<&str> = owned.iter().map(|s| s.as_str()).collect();
    cli_capture(&root, &refs)
}

#[tauri::command]
fn list_history_cmd(state: State<'_, Shared>) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    cli_capture(&root, &["list-history"])
}

#[tauri::command]
fn record_history_cmd(state: State<'_, Shared>, source: Option<String>) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    let src = source.unwrap_or_else(|| "mock".into());
    cli_capture(
        &root,
        &["record-history", "--source", src.as_str(), "--limit", "40"],
    )
}

#[tauri::command]
fn list_presets_cmd(state: State<'_, Shared>) -> Result<serde_json::Value, String> {
    let root = state.lock().project_root.clone();
    let txt = cli_capture(&root, &["presets", "list", "--json"])?;
    serde_json::from_str(&txt).map_err(|e| format!("parse presets: {e}"))
}

#[tauri::command]
fn apply_preset_cmd(state: State<'_, Shared>, name: String) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    cli_capture(&root, &["presets", "apply", "--name", name.as_str()])
}

#[tauri::command]
async fn suggest_settings_cmd(
    state: State<'_, Shared>,
    account: Option<String>,
) -> Result<serde_json::Value, String> {
    let root = state.lock().project_root.clone();
    let acct = account.unwrap_or_else(|| "paper".into());
    let root_c = root.clone();
    let acct_c = acct.clone();
    let txt = tauri::async_runtime::spawn_blocking(move || {
        cli_capture_timeout(
            &root_c,
            &["suggest-settings", "--account", acct_c.as_str(), "--json"],
            Duration::from_secs(30),
        )
    })
    .await
    .map_err(|e| format!("suggest task join: {e}"))??;
    serde_json::from_str(&txt).map_err(|e| format!("parse suggestions: {e}\n{txt}"))
}

#[tauri::command]
fn apply_suggestion_cmd(
    state: State<'_, Shared>,
    account: Option<String>,
    suggestion_id: String,
) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    let acct = account.unwrap_or_else(|| "paper".into());
    cli_capture(
        &root,
        &[
            "suggest-settings",
            "--account",
            acct.as_str(),
            "--apply",
            suggestion_id.as_str(),
        ],
    )
}

/// Path C: `chancetime crypto …` (scan / run / status / hub).
#[tauri::command]
async fn crypto_cli_cmd(
    state: State<'_, Shared>,
    args: Vec<String>,
) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    if args.is_empty() {
        return Err("crypto_cli_cmd needs args e.g. [\"scan\"]".into());
    }
    let mut full = vec!["crypto".to_string()];
    full.extend(args);
    let root_c = root.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let refs: Vec<&str> = full.iter().map(|s| s.as_str()).collect();
        // Scans hit network — allow longer timeout
        cli_capture_timeout(&root_c, &refs, Duration::from_secs(120))
    })
    .await
    .map_err(|e| format!("crypto task join: {e}"))?
}

/// Path D: `chancetime exchange …` (scan / run / status / signals).
#[tauri::command]
async fn exchange_cli_cmd(
    state: State<'_, Shared>,
    args: Vec<String>,
) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    if args.is_empty() {
        return Err("exchange_cli_cmd needs args e.g. [\"scan\"]".into());
    }
    let mut full = vec!["exchange".to_string()];
    full.extend(args);
    let root_c = root.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let refs: Vec<&str> = full.iter().map(|s| s.as_str()).collect();
        cli_capture_timeout(&root_c, &refs, Duration::from_secs(120))
    })
    .await
    .map_err(|e| format!("exchange task join: {e}"))?
}

#[tauri::command]
async fn clear_book_cmd(
    state: State<'_, Shared>,
    account: Option<String>,
) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    let acct = account.unwrap_or_else(|| "paper".into());
    let root_c = root.clone();
    let acct_c = acct.clone();
    tauri::async_runtime::spawn_blocking(move || {
        cli_capture_timeout(
            &root_c,
            &["clear-book", "--account", acct_c.as_str(), "--yes"],
            Duration::from_secs(20),
        )
    })
    .await
    .map_err(|e| format!("clear-book task join: {e}"))?
}

#[tauri::command]
fn readiness_cmd(state: State<'_, Shared>) -> Result<serde_json::Value, String> {
    let root = state.lock().project_root.clone();
    let txt = cli_capture(&root, &["readiness", "--json"])?;
    serde_json::from_str(&txt).map_err(|e| format!("parse readiness: {e}"))
}

#[tauri::command]
fn sync_positions_cmd(
    state: State<'_, Shared>,
    account: Option<String>,
) -> Result<String, String> {
    let root = state.lock().project_root.clone();
    let acct = account.unwrap_or_else(|| "live".into());
    cli_capture(
        &root,
        &["sync-positions", "--account", acct.as_str()],
    )
}

fn try_setup_tray(app: &tauri::App) -> Result<(), String> {
    let show_i = MenuItem::with_id(app, "show", "Show window", true, None::<&str>)
        .map_err(|e| e.to_string())?;
    let dash_i = MenuItem::with_id(app, "dashboard", "Open monitor (browser)", true, None::<&str>)
        .map_err(|e| e.to_string())?;
    let start_bot_i =
        MenuItem::with_id(app, "start_bot", "Start bot (paper)", true, None::<&str>)
            .map_err(|e| e.to_string())?;
    let stop_bot_i = MenuItem::with_id(app, "stop_bot", "Stop bot", true, None::<&str>)
        .map_err(|e| e.to_string())?;
    let start_dash_i = MenuItem::with_id(
        app,
        "start_dash",
        "Start API server (monitor)",
        true,
        None::<&str>,
    )
    .map_err(|e| e.to_string())?;
    let kill_i = MenuItem::with_id(app, "kill", "Kill all (bot+API)", true, None::<&str>)
        .map_err(|e| e.to_string())?;
    let quit_i =
        MenuItem::with_id(app, "quit", "Quit", true, None::<&str>).map_err(|e| e.to_string())?;
    let menu = Menu::with_items(
        app,
        &[
            &show_i,
            &dash_i,
            &start_bot_i,
            &stop_bot_i,
            &start_dash_i,
            &kill_i,
            &quit_i,
        ],
    )
    .map_err(|e| e.to_string())?;

    let icon = app
        .default_window_icon()
        .ok_or_else(|| "no default window icon".to_string())?
        .clone();

    let build_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        TrayIconBuilder::with_id("main")
            .icon(icon)
            .menu(&menu)
            .tooltip("Chance Time — bot stopped")
            .on_menu_event(|app, event| match event.id.as_ref() {
                "show" => {
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.show();
                        let _ = w.set_focus();
                    }
                }
                "dashboard" => {
                    let _ = open_dashboard(app.clone());
                }
                "start_bot" => {
                    let st = app.state::<Shared>();
                    let _ = start_bot_inner(
                        &st,
                        Some("config/default.yaml".into()),
                        Some("paper".into()),
                        None, // continuous
                    );
                    refresh_tray_tooltip(app);
                }
                "stop_bot" => {
                    let st = app.state::<Shared>();
                    let _ = stop_bot_inner(&st);
                    refresh_tray_tooltip(app);
                }
                "start_dash" => {
                    let st = app.state::<Shared>();
                    let _ = start_dashboard_inner(&st);
                }
                "kill" => {
                    let st = app.state::<Shared>();
                    let _ = kill_all_inner(&st);
                    refresh_tray_tooltip(app);
                }
                "quit" => {
                    {
                        let st = app.state::<Shared>();
                        let _ = kill_all_inner(&st);
                    }
                    refresh_tray_tooltip(app);
                    app.exit(0);
                }
                _ => {}
            })
            .on_tray_icon_event(|tray, event| {
                if let TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                } = event
                {
                    let app = tray.app_handle();
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.show();
                        let _ = w.set_focus();
                    }
                }
            })
            .build(app)
    }));

    match build_result {
        Ok(Ok(_tray)) => Ok(()),
        Ok(Err(e)) => Err(format!("tray build error: {e}")),
        Err(_) => Err(
            "tray panic (missing libayatana-appindicator?). Install: sudo pacman -S libayatana-appindicator"
                .into(),
        ),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let root = resolve_project_root();
    let shared: Shared = Arc::new(Mutex::new(ProcState::new(root)));

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(shared)
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            match try_setup_tray(app) {
                Ok(()) => {
                    TRAY_OK.store(true, Ordering::Relaxed);
                    log::info!("system tray ready");
                }
                Err(e) => {
                    TRAY_OK.store(false, Ordering::Relaxed);
                    log::warn!("system tray unavailable ({e}). Window-only mode.");
                    eprintln!(
                        "[chancetime-desktop] tray unavailable: {e}\n\
                         Window-only mode (close = quit). For tray:\n\
                         sudo pacman -S --needed libayatana-appindicator"
                    );
                }
            }

            // Warm the local API server in the background (Monitor tab / optional browser).
            // Skip if port already open (another instance).
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(400));
                if port_open(DASHBOARD_HOST, DASHBOARD_PORT) {
                    return;
                }
                let st = handle.state::<Shared>();
                match start_dashboard_inner(&st) {
                    Ok(msg) => {
                        log::info!("auto-start API: {msg}");
                        eprintln!("[chancetime-desktop] {msg}");
                    }
                    Err(e) => {
                        log::warn!("auto-start API failed: {e}");
                        eprintln!("[chancetime-desktop] auto-start API failed: {e}");
                    }
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_status,
            start_dashboard,
            stop_dashboard,
            start_bot,
            stop_bot,
            kill_all,
            set_paper_indicator,
            open_dashboard,
            get_logs,
            get_user_knobs,
            save_user_knobs_cmd,
            run_doctor,
            list_accounts_cmd,
            run_digest_cmd,
            run_export_cmd,
            list_history_cmd,
            record_history_cmd,
            list_presets_cmd,
            apply_preset_cmd,
            suggest_settings_cmd,
            apply_suggestion_cmd,
            clear_book_cmd,
            readiness_cmd,
            sync_positions_cmd,
            crypto_cli_cmd,
            exchange_cli_cmd,
            start_crypto_session,
            stop_crypto_session,
            start_exchange_session,
            stop_exchange_session,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if TRAY_OK.load(Ordering::Relaxed) {
                    // Hide to tray — bot keeps running; tooltip warns if so
                    api.prevent_close();
                    let _ = window.hide();
                    refresh_tray_tooltip(window.app_handle());
                } else {
                    // No tray: closing the window quits and must kill children
                    let st = window.app_handle().state::<Shared>();
                    let _ = kill_all_inner(&st);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Chance Time desktop")
        .run(|app_handle, event| {
            // Any full process exit path — ensure bot/dashboard cannot outlive the shell
            if matches!(
                event,
                tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
            ) {
                let st = app_handle.state::<Shared>();
                let _ = kill_all_inner(&st);
            }
        });
}
