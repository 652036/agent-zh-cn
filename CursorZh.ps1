#requires -Version 5.1
# Backward-compatible entry point for existing Cursor installations.
param([ValidateSet('apply','revert','status','list')][string]$Cmd='apply')
& (Join-Path $PSScriptRoot 'AgentZh.ps1') -Cmd $Cmd -App cursor -Kill -Restart
exit $LASTEXITCODE
