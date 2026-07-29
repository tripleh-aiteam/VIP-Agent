# =====================================================================
#  Register VIP auto-start  (RUN ONCE, in an ELEVATED / Administrator
#  PowerShell:  right-click PowerShell -> "Run as administrator", then
#     cd C:\Users\A\Desktop\VIP ;  .\register-autostart.ps1 )
#
#  Creates a Scheduled Task that launches start-vip-service.ps1 at boot,
#  WHETHER OR NOT a user is logged in (S4U, highest privileges), so a
#  reboot / power-blip brings the backend (:8000, reports ON) and the
#  dashboard (:3000) back automatically. Also persists the never-sleep /
#  hibernate-off power settings (these too require admin).
# =====================================================================
$ErrorActionPreference = "Stop"
$Root   = "C:\Users\A\Desktop\VIP"
$Script = Join-Path $Root "start-vip-service.ps1"
$User   = "$env:USERNAME"   # the interactive server account (e.g. A)

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Not elevated. Re-open PowerShell as Administrator and re-run."; exit 1
}

# --- Power: never sleep / hibernate on AC so 06:30 KST jobs always run ---
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off
Write-Host "[power] AC standby/hibernate disabled." -ForegroundColor Green

# --- Scheduled Task: auto-start at boot, whether logged in or not -------
$Action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Trigger.Delay = "PT30S"    # let network / Supabase come up first
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Highest
$Settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "VIP-Agent-AutoStart" -Description "Auto-start VIP backend (:8000, reports ON) + dashboard (:3000) at boot." `
    -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host "[task] Registered 'VIP-Agent-AutoStart'." -ForegroundColor Green
Write-Host "Test now (outside market hours):  Start-ScheduledTask -TaskName VIP-Agent-AutoStart" -ForegroundColor Cyan
Write-Host "Then verify:  Invoke-RestMethod http://localhost:8000/health" -ForegroundColor Cyan
