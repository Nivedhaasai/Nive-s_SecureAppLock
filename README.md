# Nive's_SecureAppLock

A Windows desktop application locker that prevents unauthorized access to sensitive apps using Windows Hello biometric authentication (fingerprint/face) with PIN fallback. Built for real-world use — runs silently in the background, survives force-close attempts, and locks apps the instant they're opened.

---

## About the Project

Nive's_SecureAppLock is a privacy-first, enterprise-grade Windows application locker that brings mobile-style biometric app locking to the desktop. It intercepts protected applications at the process level the moment they launch, authenticates the user through Windows Hello (fingerprint or facial recognition), and only then allows the app to run. If someone kills it from Task Manager, a tamper-proof watchdog relaunches it within seconds. It starts silently at login, lives in the system tray, and requires zero configuration beyond an initial PIN setup.

The project is built with a hybrid Python + C# architecture — Python drives the desktop UI (PyQt6), real-time process monitoring (WMI + psutil), and configuration management, while a companion C# service bridges the gap to the native Windows Hello biometric API (`UserConsentVerifier`) that Python cannot access directly.

---

## Motivation

On Android and iOS, app locks are everywhere — you open WhatsApp, your fingerprint is required, done. On Windows, this concept barely exists. The few tools that tried were either:

- **Outdated** — built for Windows 7, polling-based, easily bypassed.
- **No biometric support** — PIN or password only, no integration with the fingerprint reader already built into your laptop.
- **Admin-dependent** — needed elevated privileges or driver installs, which isn't practical for personal or shared machines.
- **Fragile** — could be killed from Task Manager with no recovery, defeating the entire purpose.

I wanted the exact same experience as a phone app lock: I open Instagram on my laptop, the fingerprint reader lights up, I scan my finger, and the app opens. No extra steps, no visible delay, and no way to bypass it without authenticating. That's what this project delivers.

The deeper engineering motivation was the challenge itself — Windows actively fights background apps that try to steal focus (and for good reason). Making the Windows Hello dialog appear instantly on top, without flicker, from a background Python process required going deep into Win32 API territory: `AllowSetForegroundWindow`, `AttachThreadInput`, window z-order manipulation, and a careful dance between PyQt6's event loop and native Windows dialogs.

---

## What Makes This Different 

| Aspect | Typical App Lockers | Nive'secureAppLock |
|--------|--------------------|-----------------------|
| **Authentication** | Password/PIN only | Windows Hello biometric (fingerprint/face) + PIN fallback |
| **Detection speed** | Polling every 1-5s | WMI event-driven (instant) + sub-second psutil poller |
| **Kill protection** | None — dies and stays dead | Detached watchdog process auto-relaunches in ~2s |
| **Focus management** | Dialog appears behind other windows | Win32 foreground tricks (AttachThreadInput, AllowSetForegroundWindow) — dialog appears instantly on top |
| **App support** | Desktop EXEs only | Universal — Store apps, UWP, PWAs (Instagram, Facebook), and desktop EXEs |
| **Re-lock behavior** | Manual or never | Automatic on window close via `EnumWindows` visibility check |
| **Architecture** | Single-language | Hybrid Python + C# — each language used where it's strongest |
| **Startup** | Manual launch | Silent auto-start via `pythonw.exe` + registry Run key |
| **Network dependency** | Some phone home | Completely offline — zero telemetry, zero external calls |

### Key Technical Novelties

1. **Python ↔ WinRT Bridge via C# Subprocess** — The `UserConsentVerifier` API is WinRT-only. Rather than pulling in heavy COM interop or pythonnet, I wrote a minimal C# console app that Python spawns as a subprocess. Communication is via stdout + exit code — simple, fast, and no shared memory or IPC complexity.

2. **Foreground Focus Engineering** — Windows has strict rules about which process can set the foreground window. A background Python process can't just pop up a dialog on top. The C# service calls `AllowSetForegroundWindow(ASFW_ANY)`, then uses `AttachThreadInput` to latch onto the current foreground thread's input queue before triggering the Windows Hello dialog. The result: the fingerprint prompt appears instantly, just like the laptop lock screen.

3. **Qt exec() Survival Trick** — When the tray menu triggers authentication, a `QDialog.exec()` blocks until the dialog closes. Windows Hello needs to run in a background thread while the dialog stays "alive." Calling `hide()` on a running `exec()` dialog terminates the event loop (undocumented PyQt6 behavior). The solution: set window opacity to 0 and push it behind all windows with `SetWindowPos(HWND_BOTTOM)` — invisible but alive.

4. **Smart Re-lock via Window Visibility** — Instead of naively re-locking when the process exits (which fails for apps like WhatsApp that keep background processes running), the watcher uses `win32gui.EnumWindows` to check if any matching process has a *visible, titled window*. No window? The app was closed — kill background processes silently and re-arm the lock. No false lock screen pop-ups.

5. **Universal App Discovery** — One unified picker that finds Store apps (via `Get-AppxPackage`), PWAs (Instagram runs as `msedge.exe` — needs special handling), and desktop apps (via registry `Uninstall` keys + Start Menu shortcuts). Correctly maps process names for apps that use multiple executables.

---

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────┐
│                   main.py                       │
│ App controller, single-instance mutex, watchdog │
├─────────┬──────────┬──────────┬─────────────────┤
│  UI     │  Auth    │ Monitor  │    Config       │
│ PyQt6   │ WinHello │   WMI    │   JSON-based    │
│         │  bcrypt  │  psutil  │                 │
└────┬────┴────┬─────┴────┬─────┴─────────────────┘
     │         │          │
     │    ┌────▼─────┐    │
     │    │ C# Svc   │    │
     │    │ WinRT    │    │
     │    │ UserCon- │    │
     │    │ sentVer- │    │
     │    │  ifier   │    │
     │    └──────────┘    │
     │                    │
     ▼                    ▼
  Qt Lock Screen    WMI Event Listener
  (fullscreen)      + psutil poller
```

### Core Flow

1. **Process Detection** — A WMI event listener watches for new processes in real-time. When a locked app starts, the watcher kills it immediately (before the window even renders) and emits a signal.

2. **Authentication** — The lock screen appears and triggers Windows Hello automatically. The fingerprint reader activates instantly — no extra clicks needed. If biometrics fail or aren't available, a PIN input appears as fallback.

3. **App Relaunch** — After successful authentication, the app is relaunched seamlessly. The user never has to go back and open it again manually.

4. **Auto Re-lock** — Once the user closes the app (no visible window detected), protection re-engages silently. Background processes are cleaned up without triggering the lock screen again.

### Tamper Protection

- **Watchdog process** — A separate detached process monitors the main app. If someone force-kills Nive'secureAppLock (via Task Manager, `taskkill`, etc.), the watchdog relaunches it within 2 seconds.
- **Graceful exit sentinel** — The only way to stop the app is to authenticate through the tray menu. This writes a sentinel file that tells the watchdog to stand down.
- **Single-instance mutex** — Prevents duplicate instances from running.

### Windows Hello Integration

The biggest technical hurdle. The `UserConsentVerifier` API lives in WinRT, which Python can't call directly. The solution:

- A small C# console app (`SecureHelloAuth.exe`) compiled against `net8.0-windows10.0.17763.0`.
- Python spawns it as a subprocess with `STARTUPINFO(SW_HIDE)` so no console window flashes.
- Before spawning, Python calls `AllowSetForegroundWindow()` to grant the C# process focus rights — this is critical because Windows normally blocks background processes from stealing focus.
- The C# service uses `AttachThreadInput` to the current foreground thread, ensuring the Windows Security dialog appears on top immediately.
- Result is communicated via stdout (`SUCCESS` / `CANCELED` / `FAILED:reason`) and exit code.

### Thread Safety

All background-to-UI communication uses `pyqtSignal`. The fingerprint check runs in a daemon thread and emits a signal when done — Qt marshals the callback to the main thread automatically. No `QTimer.singleShot(0, ...)` hacks.

The `AuthGateDialog` (tray menu authentication) uses a window opacity trick instead of `hide()` during fingerprint scans. Calling `hide()` on a `QDialog` running in `exec()` terminates the event loop prematurely, which I learned the hard way. Setting opacity to 0 and lowering the z-order via Win32 `SetWindowPos(HWND_BOTTOM)` keeps the dialog "alive" for exec() while making it invisible so the Windows Security dialog gets focus.

---

## Project Structure

```
SecureAppLock/
├── main.py                     # Entry point, app controller, watchdog spawner
├── watchdog.py                 # Tamper-proof watchdog (detached process)
├── requirements.txt            # Python dependencies
│
├── auth/
│   ├── fingerprint_auth.py     # Windows Hello via C# subprocess
│   └── pin_auth.py             # bcrypt PIN hashing and verification
│
├── config/
│   ├── config.py               # Dataclass-based config (load/save/defaults)
│   ├── config.json             # User config (gitignored)
│   └── config.example.json     # Template for new setups
│
├── monitor/
│   └── process_watcher.py      # WMI event listener + psutil fast poller
│
├── ui/
│   ├── lock_screen.py          # Fullscreen lock overlay (fingerprint + PIN)
│   ├── tray_icon.py            # System tray + AuthGateDialog
│   ├── settings_window.py      # Manage locked apps, change PIN
│   ├── setup_dialog.py         # First-run PIN setup
│   └── app_picker_dialog.py    # Searchable app picker (Store + desktop)
│
├── utils/
│   ├── logger.py               # Rotating file + console logging
│   ├── startup.py              # Registry-based auto-start (HKCU\Run)
│   ├── icon_extractor.py       # App icon extraction (Store + desktop)
│   └── app_discovery.py        # Discovers installed apps via PowerShell
│
├── service/
│   └── SecureHelloAuth/
│       ├── Program.cs          # C# Windows Hello authentication service
│       └── SecureHelloAuth.csproj
│
├── logs/                       # Runtime logs (gitignored)
├── LICENSE                     # MIT
└── .gitignore
```

---

## Setup

### Prerequisites

| Component | Version | Why |
|-----------|---------|-----|
| Python | 3.11+ | Main runtime |
| .NET SDK | 8.0+ | Building the C# auth service |
| Windows | 10 (1809+) or 11 | `UserConsentVerifier` API requirement |
| Windows Hello | Fingerprint or face enrolled | Settings → Accounts → Sign-in options |

### 1. Install Python Dependencies

```powershell
cd SecureAppLock
pip install -r requirements.txt
```

Installs: `PyQt6`, `psutil`, `bcrypt`, `pywin32`, `wmi`

### 2. Build the C# Authentication Service

```powershell
cd service\SecureHelloAuth
dotnet publish -c Release -r win-x64 --self-contained false
```

Output: `service\SecureHelloAuth\bin\Release\net8.0-windows10.0.17763.0\win-x64\publish\SecureHelloAuth.exe`

**Verify the build:**

```powershell
.\bin\Release\net8.0-windows10.0.17763.0\win-x64\publish\SecureHelloAuth.exe check
# → AVAILABLE (exit code 0)

.\bin\Release\net8.0-windows10.0.17763.0\win-x64\publish\SecureHelloAuth.exe verify
# → Opens Windows Hello dialog → scan finger → SUCCESS (exit code 0)
```

### 3. Run

```powershell
python main.py
```

On first run, a setup dialog asks you to create a 4–6 digit backup PIN. After that, the app drops into the system tray and starts protecting configured apps immediately.

---

## Usage

### Lock Screen

When you open a protected app:
1. The app is killed instantly (before the window renders).
2. Windows Hello fingerprint authentication starts automatically — the sensor lights up, you scan your finger.
3. On success, the app relaunches seamlessly.
4. On failure, a fullscreen lock screen appears with retry + PIN input options.
5. When you close the app, protection re-engages. Next time you open it, step 1 repeats.

### System Tray

Right-click the tray icon:

| Action | Description |
|--------|-------------|
| **Settings** | Manage locked apps, view configuration (auth required) |
| **Add App to Lock** | Searchable picker showing all installed apps (auth required) |
| **Change PIN** | Update your backup PIN (auth required) |
| **Enable/Disable Auto-Start** | Toggle Windows startup registration |
| **Exit** | Shuts down protection (auth required — watchdog respects this) |

Double-click the tray icon to open Settings.

### App Discovery

The app picker discovers applications from two sources:
- **Microsoft Store / UWP apps** — via `Get-AppxPackage` PowerShell command, including PWA apps (Instagram, Facebook)
- **Desktop apps** — via registry (`Uninstall` keys) and Start Menu shortcuts

Apps already in the locked list are filtered out.

---

## Configuration

Config lives in `config/config.json` (auto-created on first run):

```json
{
  "pin_hash": "$2b$12$...",
  "fingerprint_enabled": true,
  "auto_start": true,
  "locked_apps": [
    {
      "name": "WhatsApp",
      "process_names": ["WhatsApp.Root.exe", "WhatsApp.exe"],
      "launch_command": "shell:AppsFolder\\5319275A.WhatsAppDesktop_cw5n1h2txyewy!App",
      "is_store_app": true
    }
  ]
}
```

- `pin_hash` — bcrypt hash of the backup PIN. Never stored in plaintext.
- `fingerprint_enabled` — `true` to use Windows Hello as primary auth.
- `auto_start` — `true` to register in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
- `locked_apps` — Each entry has the app name, one or more process names to monitor, a launch command (either a `shell:AppsFolder\...` URI for Store apps or an EXE path), and a Store app flag.

---

## Security Model

- **PIN storage** — bcrypt with random salt. No plaintext PINs touch disk.
- **Biometric auth** — Delegated entirely to Windows Hello. No fingerprint data is captured, stored, or transmitted by this application. The OS handles all biometric data through the Trusted Platform Module (TPM).
- **Process killing** — Locked apps are killed via `psutil` before their window renders. WMI event detection + a fast psutil poller (sub-second) ensures there's no window of opportunity to interact with the app before authentication.
- **Tamper resistance** — Watchdog detached process monitors the main PID. Only an authenticated exit writes the sentinel file that stops the watchdog.
- **No network** — The application is entirely offline. No telemetry, no update checks, no external API calls.

---

## Technical Notes

- The WMI watcher tries `Win32_ProcessStartTrace` first (instant, needs admin). Falls back to `__InstanceCreationEvent` polling (~0.5s, works without admin).
- Auto re-lock checks for visible windows via `win32gui.EnumWindows`. If a locked app has no visible titled window (user closed it), background processes are killed silently and the app is re-locked. Safety timeout at 30 minutes.
- Store app / UWP launch uses `os.startfile()` with `shell:AppsFolder\...` URIs.
- The lock screen uses `WindowStaysOnTopHint` + `FramelessWindowHint` + `Tool` flags. F4+Alt and Escape are intercepted. `closeEvent` is ignored.
- Registry auto-start uses `pythonw.exe` (not `python.exe`) so no console window appears at login.

---

## Roadmap

- [ ] Per-app configurable lock timeout (e.g. "don't re-lock WhatsApp for 5 minutes after I close it")
- [ ] Custom app icons in the lock screen for unrecognized desktop apps
- [ ] Group policy / MDM support for enterprise deployment
- [ ] Tray notification when a locked app launch is blocked
- [ ] Optional face recognition as Windows Hello alternative on devices without fingerprint readers

---

## License

MIT — see [LICENSE](LICENSE).

---

Built by **Nivedhaa Sai S** — because privacy on Windows shouldn't require a phone.
