/*
 * SecureHelloAuth — Windows Hello biometric authentication service.
 *
 * Enterprise-grade companion for Nive'secureAppLock.  Uses the native
 * Windows.Security.Credentials.UI.UserConsentVerifier API to trigger
 * Windows Hello biometric authentication (fingerprint / face).
 *
 * Key features:
 *   - Win32 foreground window management: the Windows Security dialog
 *     appears immediately on top, just like the native lock screen.
 *   - Custom dialog message showing which app is being unlocked.
 *
 * Commands
 * --------
 *   SecureHelloAuth.exe verify [message]
 *       Triggers Windows Hello authentication with optional message.
 *       Exit 0 + stdout "SUCCESS"   → user verified
 *       Exit 1 + stdout "CANCELED"  → user cancelled the dialog
 *       Exit 1 + stdout "FAILED:…"  → verification failed
 *
 *   SecureHelloAuth.exe check
 *       Checks if Windows Hello is available on this device.
 *       Exit 0 + stdout "AVAILABLE"       → ready to use
 *       Exit 1 + stdout "UNAVAILABLE:…"   → not available (reason)
 *
 * Build
 * -----
 *   dotnet publish -c Release -r win-x64 --self-contained false
 */

using System;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using Windows.Security.Credentials.UI;

namespace SecureHelloAuth;

internal static class Program
{
    // -- Win32: foreground window management --------------------------------

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool AllowSetForegroundWindow(int dwProcessId);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll")]
    private static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

    [DllImport("user32.dll")]
    private static extern bool BringWindowToTop(IntPtr hWnd);

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetConsoleWindow();

    private const int ASFW_ANY = -1;

    private static async Task<int> Main(string[] args)
    {
        if (args.Length == 0)
        {
            Console.Error.WriteLine("Usage: SecureHelloAuth.exe <verify|check> [message]");
            return 2;
        }

        // Allow any process (including the credential UI host) to set
        // itself as the foreground window.
        AllowSetForegroundWindow(ASFW_ANY);

        return args[0].ToLowerInvariant() switch
        {
            "verify" => await VerifyAsync(args.Length > 1 ? args[1] : null),
            "check"  => await CheckAvailabilityAsync(),
            _        => PrintUsageError(args[0]),
        };
    }

    /// <summary>
    /// Aggressively grab foreground focus so the Windows Security dialog
    /// appears immediately on top, just like the native lock screen.
    /// Uses the AttachThreadInput technique to bypass Windows focus-theft
    /// protection that would otherwise send the dialog behind other windows.
    /// </summary>
    private static void EnsureForeground()
    {
        try
        {
            IntPtr fgWnd = GetForegroundWindow();
            IntPtr consoleWnd = GetConsoleWindow();
            uint currentThread = GetCurrentThreadId();

            if (fgWnd != IntPtr.Zero)
            {
                uint fgThread = GetWindowThreadProcessId(fgWnd, out _);

                if (fgThread != 0 && fgThread != currentThread)
                {
                    // Attach to the foreground thread's input queue so we
                    // can call SetForegroundWindow successfully.
                    AttachThreadInput(currentThread, fgThread, true);
                    try
                    {
                        if (consoleWnd != IntPtr.Zero)
                        {
                            BringWindowToTop(consoleWnd);
                            SetForegroundWindow(consoleWnd);
                        }
                    }
                    finally
                    {
                        AttachThreadInput(currentThread, fgThread, false);
                    }
                }
            }
            else if (consoleWnd != IntPtr.Zero)
            {
                SetForegroundWindow(consoleWnd);
            }
        }
        catch
        {
            // Non-critical: dialog may still appear, just might not auto-focus
        }
    }

    /// <summary>
    /// Trigger Windows Hello authentication (fingerprint / face / Hello PIN).
    /// </summary>
    private static async Task<int> VerifyAsync(string? customMessage)
    {
        try
        {
            // Grab foreground focus BEFORE triggering the dialog so
            // the fingerprint sensor activates immediately.
            EnsureForeground();

            string message = customMessage
                ?? "Nive'secureAppLock: Scan your fingerprint to unlock";

            var result = await UserConsentVerifier.RequestVerificationAsync(message);

            switch (result)
            {
                case UserConsentVerificationResult.Verified:
                    Console.WriteLine("SUCCESS");
                    return 0;

                case UserConsentVerificationResult.Canceled:
                    Console.WriteLine("CANCELED");
                    return 1;

                case UserConsentVerificationResult.NotConfiguredForUser:
                    Console.WriteLine("FAILED:NotConfiguredForUser");
                    return 1;

                case UserConsentVerificationResult.DeviceNotPresent:
                    Console.WriteLine("FAILED:DeviceNotPresent");
                    return 1;

                case UserConsentVerificationResult.DisabledByPolicy:
                    Console.WriteLine("FAILED:DisabledByPolicy");
                    return 1;

                case UserConsentVerificationResult.DeviceBusy:
                    Console.WriteLine("FAILED:DeviceBusy");
                    return 1;

                case UserConsentVerificationResult.RetriesExhausted:
                    Console.WriteLine("FAILED:RetriesExhausted");
                    return 1;

                default:
                    Console.WriteLine($"FAILED:{result}");
                    return 1;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"Error: {ex.Message}");
            Console.WriteLine($"FAILED:Exception");
            return 1;
        }
    }

    /// <summary>
    /// Check whether Windows Hello is configured and available.
    /// </summary>
    private static async Task<int> CheckAvailabilityAsync()
    {
        try
        {
            var availability = await UserConsentVerifier.CheckAvailabilityAsync();

            switch (availability)
            {
                case UserConsentVerifierAvailability.Available:
                    Console.WriteLine("AVAILABLE");
                    return 0;

                case UserConsentVerifierAvailability.DeviceNotPresent:
                    Console.WriteLine("UNAVAILABLE:DeviceNotPresent");
                    return 1;

                case UserConsentVerifierAvailability.NotConfiguredForUser:
                    Console.WriteLine("UNAVAILABLE:NotConfiguredForUser");
                    return 1;

                case UserConsentVerifierAvailability.DisabledByPolicy:
                    Console.WriteLine("UNAVAILABLE:DisabledByPolicy");
                    return 1;

                case UserConsentVerifierAvailability.DeviceBusy:
                    Console.WriteLine("UNAVAILABLE:DeviceBusy");
                    return 1;

                default:
                    Console.WriteLine($"UNAVAILABLE:{availability}");
                    return 1;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"Error: {ex.Message}");
            Console.WriteLine("UNAVAILABLE:Exception");
            return 1;
        }
    }

    private static int PrintUsageError(string unknown)
    {
        Console.Error.WriteLine($"Unknown command: {unknown}");
        Console.Error.WriteLine("Usage: SecureHelloAuth.exe <verify|check> [message]");
        return 2;
    }
}
