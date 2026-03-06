"""
Nive'secureAppLock - Universal app discovery.
Discovers installed Store/UWP apps and desktop apps on Windows.
"""

import os
import subprocess
import json
from dataclasses import dataclass

from utils.logger import setup_logger

logger = setup_logger()


@dataclass
class DiscoveredApp:
    """An installed application found on the system."""
    name: str
    process_names: list[str]
    launch_command: str
    is_store_app: bool
    pwa_app_id: str = ""           # Edge --app-id for PWA apps

    @property
    def sort_key(self) -> str:
        return self.name.lower()


# Friendly name overrides and extra process names for Store apps
_STORE_APP_OVERRIDES: dict[str, dict] = {
    "SpotifyAB.SpotifyMusic": {
        "name": "Spotify",
        "extra_processes": ["Spotify.exe"],
    },
    "Microsoft.ZuneMusic": {"name": "Media Player"},
    "Microsoft.ZuneVideo": {"name": "Movies & TV"},
    "Microsoft.WindowsSoundRecorder": {"name": "Sound Recorder"},
    "Microsoft.WindowsCamera": {"name": "Camera"},
    "Microsoft.WindowsCalculator": {"name": "Calculator"},
    "Microsoft.WindowsAlarms": {"name": "Alarms & Clock"},
    "Microsoft.WindowsMaps": {"name": "Maps"},
    "Microsoft.WindowsNotepad": {"name": "Notepad"},
    "Microsoft.WindowsFeedbackHub": {"name": "Feedback Hub"},
    "Microsoft.MicrosoftStickyNotes": {"name": "Sticky Notes"},
    "Microsoft.GamingApp": {"name": "Xbox App"},
    "Microsoft.ScreenSketch": {"name": "Snipping Tool"},
    "Microsoft.Todos": {"name": "Microsoft To Do"},
    "Microsoft.YourPhone": {"name": "Phone Link"},
    "Microsoft.Windows.Photos": {"name": "Photos"},
    "Microsoft.WindowsTerminal": {"name": "Windows Terminal"},
    "Microsoft.PowerAutomateDesktop": {"name": "Power Automate"},
    "Clipchamp.Clipchamp": {"name": "Clipchamp"},
    "microsoft.windowscommunicationsapps": {"name": "Mail & Calendar"},
    "Microsoft.Windows.DevHome": {"name": "Dev Home"},
    "Microsoft.GetHelp": {"name": "Get Help"},
    "MicrosoftCorporationII.QuickAssist": {"name": "Quick Assist"},
    "Microsoft.XboxGamingOverlay": {"name": "Game Bar"},
    "Microsoft.Paint": {"name": "Paint"},
    "Facebook.InstagramBeta": {
        "name": "Instagram",
        "extra_processes": ["msedge.exe"],
        "is_pwa": True,
        "pwa_app_id": "akpamiohjfcnimfljfndmaldlcfphjmp",
    },
    "Facebook.Facebook": {
        "name": "Facebook",
        "extra_processes": ["msedge.exe"],
        "is_pwa": True,
        "pwa_app_id": "mhnfclaomkfkepnljbglchmeipcdefka",
    },
}

# -- Exclusion lists -------------------------------------------------------

_STORE_EXCLUDE_PREFIXES = (
    "Microsoft.NET",
    "Microsoft.VCLibs",
    "Microsoft.UI.Xaml",
    "Microsoft.DirectX",
    "Microsoft.Services",
    "MicrosoftCorporationII.WinAppRuntime",
    "Microsoft.WindowsAppRuntime",
    "Microsoft.ApplicationCompatibility",
    "Microsoft.RawImageExtension",
    "Microsoft.WebMediaExtensions",
    "Microsoft.WebpImageExtension",
    "Microsoft.VP9VideoExtensions",
    "Microsoft.AV1VideoExtension",
    "Microsoft.MPEG2VideoExtension",
    "Microsoft.HEIFImageExtension",
    "Microsoft.HEVCVideoExtension",
    "Microsoft.AVCEncoderVideoExtension",
    "Microsoft.WidgetsPlatformRuntime",
    "Microsoft.Winget.Source",
    "Microsoft.LanguageExperiencePack",
    "Microsoft.StorePurchaseApp",
    "Microsoft.XboxIdentityProvider",
    "Microsoft.XboxGameOverlay",
    "Microsoft.XboxSpeechToTextOverlay",
    "Microsoft.Xbox.TCUI",
    "Microsoft.DesktopAppInstaller",
    "Microsoft.SecHealthUI",
    "Microsoft.StartExperiencesApp",
    "MicrosoftWindows.Client.WebExperience",
    "MicrosoftWindows.CrossDevice",
    "AppUp.",
    "ELANMicroelectronicsCorpo.",
    "AD2F1837.",
    "RealtekSemiconductorCorp.",
    "Microsoft.BingSearch",
    "Microsoft.BingNews",
    "Microsoft.BingWeather",
    "Microsoft.People",
)

_DESKTOP_EXCLUDE_EXES = {
    "cmd.exe", "powershell.exe", "control.exe", "pythonw.exe",
    "python.exe", "charmap.exe", "magnify.exe", "narrator.exe",
    "cleanmgr.exe", "dfrgui.exe", "iscsicpl.exe", "MdSched.exe",
    "appverif.exe", "odbcad32.exe", "AppVLP.exe", "launch.exe",
    "git-cmd.exe", "git-bash.exe", "rundll32.exe", "explorer.exe",
    "setup.exe", "perfmon.exe", "regedit.exe", "taskmgr.exe",
    "msconfig.exe", "msinfo32.exe", "psr.exe", "osk.exe",
    "mstsc.exe", "LiveCaptions.exe",
}

_DESKTOP_EXCLUDE_NAME_PARTS = (
    "uninstall", "readme", "help", "license", "release notes",
    "command prompt", "administrative", "developer ",
    "debuggable", "iSCSI", "locale builder", "database config",
    "database upgrade", "application verifier", "memory diagnostic",
    "disk cleanup", "ODBC", "live captions", "livecaptions",
    "recovery drive", "steps recorder", "system config",
    "system information", "task manager", "registry editor",
    "on-screen keyboard", "resource monitor", "remote desktop",
    "voiceaccess", "telemetry", "language preferences",
    "windows software development", "windows powershell ise",
    "windows app cert", "sql plus", "oracle instance",
    "visual studio installer",
)


def discover_store_apps() -> list[DiscoveredApp]:
    """Discover installed UWP/Store apps via PowerShell."""
    ps_script = r"""
$results = @()
Get-AppxPackage | Where-Object { $_.IsFramework -eq $false -and $_.SignatureKind -eq 'Store' } | ForEach-Object {
    try {
        $manifest = Get-AppxPackageManifest $_
        $displayName = $manifest.Package.Properties.DisplayName
        $appNode = $manifest.Package.Applications.Application | Select-Object -First 1
        $appId = $appNode.Id
        $exe = $appNode.Executable
        # Fallback: use cleaned package Name when display name is a resource ref
        if ($displayName -like 'ms-resource*') {
            $parts = $_.Name -split '\.'
            $displayName = $parts[-1]
        }
        if ($displayName -and $appId) {
            $results += [PSCustomObject]@{
                DisplayName = $displayName
                PackageName = $_.Name
                PackageFamilyName = $_.PackageFamilyName
                AppId = $appId
                Executable = if($exe){$exe}else{''}
            }
        }
    } catch {}
}
$results | ConvertTo-Json -Compress
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning("Store app discovery returned no data")
            return []

        data = json.loads(result.stdout.strip())
        if isinstance(data, dict):
            data = [data]

        apps = []
        for item in data:
            pkg_name = item.get("PackageName", "")
            if any(pkg_name.startswith(p) for p in _STORE_EXCLUDE_PREFIXES):
                continue

            display_name = item.get("DisplayName", pkg_name)
            pfn = item.get("PackageFamilyName", "")
            app_id = item.get("AppId", "App")
            exe = item.get("Executable", "")

            # Apply friendly name overrides
            overrides = _STORE_APP_OVERRIDES.get(pkg_name, {})
            if "name" in overrides:
                display_name = overrides["name"]

            launch_cmd = rf"shell:AppsFolder\{pfn}!{app_id}"

            process_names = []
            if exe:
                exe_basename = os.path.basename(exe)
                process_names.append(exe_basename)
                # Store apps often have both Name.exe and Name.Root.exe
                if exe_basename.lower().endswith(".root.exe"):
                    # Add the non-Root variant too
                    base_name = exe_basename[:-len(".Root.exe")] + ".exe"
                    process_names.append(base_name)
                else:
                    root_name = exe_basename.replace(".exe", ".Root.exe")
                    process_names.append(root_name)

            # Add any extra known process names from overrides
            for extra in overrides.get("extra_processes", []):
                if extra not in process_names:
                    process_names.append(extra)

            # Skip Store apps with no detectable process (PWAs, etc.)
            # UNLESS they have override entries with extra_processes
            if not process_names:
                continue

            pwa_id = overrides.get("pwa_app_id", "")

            apps.append(DiscoveredApp(
                name=display_name,
                process_names=process_names,
                launch_command=launch_cmd,
                is_store_app=True,
                pwa_app_id=pwa_id,
            ))

        return apps

    except Exception as e:
        logger.error("Store app discovery failed: %s", e)
        return []


def discover_desktop_apps() -> list[DiscoveredApp]:
    """Discover desktop apps from Start Menu shortcuts."""
    ps_script = r"""
$shell = New-Object -ComObject WScript.Shell
$results = @()
$paths = @(
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
)
foreach ($p in $paths) {
    Get-ChildItem $p -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $s = $shell.CreateShortcut($_.FullName)
            if ($s.TargetPath -and $s.TargetPath -like '*.exe') {
                $results += [PSCustomObject]@{
                    Name = $_.BaseName
                    Exe = [System.IO.Path]::GetFileName($s.TargetPath)
                    Target = $s.TargetPath
                }
            }
        } catch {}
    }
}
$results | Sort-Object Name -Unique | ConvertTo-Json -Compress
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning("Desktop app discovery returned no data")
            return []

        data = json.loads(result.stdout.strip())
        if isinstance(data, dict):
            data = [data]

        apps = []
        seen_exes = set()
        for item in data:
            name = item.get("Name", "")
            exe = item.get("Exe", "")
            target = item.get("Target", "")

            if not exe or not target:
                continue
            if exe.lower() in {e.lower() for e in _DESKTOP_EXCLUDE_EXES}:
                continue
            if any(part in name.lower() for part in _DESKTOP_EXCLUDE_NAME_PARTS):
                continue
            if exe.lower() in seen_exes:
                continue
            seen_exes.add(exe.lower())

            apps.append(DiscoveredApp(
                name=name,
                process_names=[exe],
                launch_command=target,
                is_store_app=False,
            ))

        return apps

    except Exception as e:
        logger.error("Desktop app discovery failed: %s", e)
        return []


def discover_all_apps() -> list[DiscoveredApp]:
    """Discover all installed apps (Store + desktop), sorted by name."""
    apps = discover_store_apps() + discover_desktop_apps()
    apps.sort(key=lambda a: a.sort_key)
    return apps
