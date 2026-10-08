<#
.SYNOPSIS
    Enable PowerShell Script Block Logging to capture script execution.
.DESCRIPTION
    Creates the registry keys needed to enable PowerShell Script Block Logging
    (Event ID 4104) and Script Block Invocation Logging. This will capture the
    content of every script block processed by PowerShell, which is critical
    for diagnosing infinite recursion crashes like the one that caused the
    0xC00000FD stack overflow on 2026-07-07.
.NOTES
    MUST be run as Administrator.
    Events appear in: Event Viewer > Applications and Services Logs >
    Microsoft-Windows-PowerShell/Operational (Event ID 4104)
#>

#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'

$regPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging'

try {
    # Create the key if it doesn't exist
    if (-not (Test-Path $regPath)) {
        New-Item -Path $regPath -Force | Out-Null
        Write-Output "Created registry key: $regPath"
    } else {
        Write-Output "Registry key already exists: $regPath"
    }

    # Enable Script Block Logging (1 = enabled)
    Set-ItemProperty -Path $regPath -Name 'EnableScriptBlockLogging' -Value 1 -Type DWord
    Write-Output "Set EnableScriptBlockLogging = 1"

    # Enable Script Block Invocation Logging
    Set-ItemProperty -Path $regPath -Name 'EnableScriptBlockInvocationLogging' -Value 1 -Type DWord
    Write-Output "Set EnableScriptBlockInvocationLogging = 1"

    # Verify
    $reg = Get-ItemProperty -Path $regPath
    Write-Output ""
    Write-Output "=== VERIFICATION ==="
    Write-Output ("  EnableScriptBlockLogging = " + $reg.EnableScriptBlockLogging)
    Write-Output ("  EnableScriptBlockInvocationLogging = " + $reg.EnableScriptBlockInvocationLogging)
    Write-Output ""
    Write-Output "SUCCESS: PowerShell Script Block Logging is now ENABLED"
    Write-Output ""
    Write-Output "To view captured events:"
    Write-Output "  1. Open Event Viewer (eventvwr.msc)"
    Write-Output "  2. Navigate to: Applications and Services Logs > Microsoft-Windows-PowerShell/Operational"
    Write-Output "  3. Filter by Event ID 4104 for script block content"
    Write-Output "  4. Look for scripts executing near the time of any future crash"
    Write-Output ""
    Write-Output "To disable later:"
    Write-Output "  Remove-Item -Path '$regPath' -Recurse -Force"

} catch {
    Write-Output ("ERROR: " + $_.Exception.Message)
    Write-Output "This script requires Administrator privileges."
    Write-Output "Right-click PowerShell > Run as Administrator, then re-run this script."
    exit 1
}
