# Windows PowerShell 5.1. Default: file/device checks only, no USB command sent.
[CmdletBinding()]
param(
    [ValidateSet('Verifier', 'Identifier', 'Flasher', 'Restaurer')]
    [string]$Action = 'Verifier',
    [ValidateSet('Officiel', 'Profil')]
    [string]$Variante = 'Officiel',
    [string]$ProfilManifest = '',
    [switch]$Utf8Console
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($Utf8Console) {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$run = $null

function Assert-FileHash([string]$Path, [string]$Hash) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Fichier absent : $Path" }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Hash) {
        throw "Empreinte SHA-256 inattendue : $Path. Aucun transfert autorise."
    }
}

function Get-AdapterInventory {
    # Count physical USB devices, not the duplicate HID/collection nodes.
    @([UsbInventory]::GetPresent() | Where-Object {
        $_ -match '^USB\\VID_' -and $_ -notmatch '&MI_' -and
        ($_ -match '^USB\\VID_2DC8&' -or
         $_ -match '^USB\\VID_0483&PID_(5750|5760|5001)\\')
    })
}

try {
    $experimental = $null
    $experimentalHash = $null
    $official = Join-Path $projectRoot 'local\original.dat'
    if ($Action -ne 'Identifier') {
        Assert-FileHash $official '0dc85f34bb0beba631076683c8ef3acc3e0d5babcfe62c5333b1af9acaee3798'
    }
    if ($Action -eq 'Flasher' -and $Variante -ne 'Profil') {
        throw 'Pour flasher un firmware personnalise, utiliser le configurateur ou fournir un manifeste de profil.'
    }
    if ($Variante -eq 'Profil' -and $Action -notin @('Restaurer', 'Identifier')) {
        if (-not $ProfilManifest) { throw 'Le manifeste du profil est requis.' }
        # Rebuild the bounded recipe and emulate it before trusting any custom DAT.
        # A hash supplied in a manifest alone is not sufficient authorization.
        $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            $python = @(Get-Command python -CommandType Application -ErrorAction Stop)[0].Source
        }
        Write-Host 'Reconstruction et tests ARM du profil personnalise...'
        $verification = & $python (Join-Path $PSScriptRoot 'custom_firmware.py') --manifest $ProfilManifest
        if ($LASTEXITCODE -ne 0) { throw 'Le profil personnalise a echoue a la verification.' }
        $verified = $verification | ConvertFrom-Json
        if ($verified.status -ne 'verified') { throw 'Verification de profil incomplete.' }
        $experimental = [string]$verified.firmware
        $experimentalHash = [string]$verified.sha256
        Write-Host ("Profil : {0} ; {1} controles hors materiel reussis." -f $verified.profile_name, $verified.checks)
        Write-Host ("Touches : {0}" -f $verified.mapping_summary)
    }
    # Restoring the original never depends on the experimental DAT being present.
    $filesToCheck = @()
    if ($Action -ne 'Identifier') { $filesToCheck += $official }
    if ($Variante -eq 'Profil' -and $Action -notin @('Restaurer', 'Identifier')) {
        Assert-FileHash $experimental $experimentalHash
        $filesToCheck += $experimental
    }
    foreach ($file in $filesToCheck) {
        $bytes = [IO.File]::ReadAllBytes($file)
        if ($bytes.Length -notin @(100380, 101404)) { throw "Taille DAT incorrecte : $file" }
        $expected = @(207, 0x08003400, ($bytes.Length-28), 0, 0, 0, 0)
        for ($i = 0; $i -lt 7; $i++) {
            if ([BitConverter]::ToUInt32($bytes, 4 * $i) -ne $expected[$i]) {
                throw "Entete DAT incorrect : $file"
            }
        }
    }
    if ($Action -ne 'Identifier') {
        Write-Host "Fichiers verifies (SHA-256 + entetes DAT). Variante : $Variante."
    }
    Add-Type -Path (Join-Path $PSScriptRoot 'UsbInventory.cs')
    $devices = @(Get-AdapterInventory)
    $boot = @($devices | Where-Object { $_ -match '^USB\\VID_0483&PID_5760\\' })
    Write-Host ("Peripheriques candidats : {0} ; adaptateurs en mode mise a jour 0483:5760 : {1}." -f $devices.Count, $boot.Count)
    if ($Action -eq 'Verifier') {
        Write-Host 'Verification terminee. Aucune commande USB envoyee.'
        if ($boot.Count -ne 1 -or $devices.Count -ne 1) {
            Write-Host 'Pour flasher : fermer le logiciel 8BitDo, debrancher les autres appareils 8BitDo,'
            Write-Host 'puis brancher cet adaptateur en maintenant son bouton d''appairage enfonce.'
        }
        exit 0
    }
    if ($boot.Count -ne 1 -or $devices.Count -ne 1) {
        throw 'Il faut exactement un adaptateur 0483:5760 fonctionnel et aucun autre appareil 8BitDo. Maintenir son bouton pendant le branchement USB.'
    }
    $updaters = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -match '8BitDo.*(Firmware|Updater)|8BitDoFirmware|Autoupdate'
    })
    if ($updaters.Count -gt 0) { throw 'Fermer l''application de mise a jour 8BitDo avant de continuer.' }
    Add-Type -Path (Join-Path $PSScriptRoot 'NativeFlasher.cs')
    if ($Action -eq 'Identifier') {
        Write-Host ([NativeFlasher]::Probe())
        Write-Host 'Identification integree terminee. Aucun firmware envoye.'
        exit 0
    }
    $source = if ($Action -eq 'Flasher') { $experimental } else { $official }
    $sourceHash = if ($Action -eq 'Flasher') { $experimentalHash } else { '0dc85f34bb0beba631076683c8ef3acc3e0d5babcfe62c5333b1af9acaee3798' }
    $logRoot = Join-Path $projectRoot 'local\flash_logs'
    $run = Join-Path $logRoot ((Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $run -Force | Out-Null
    $staged = Join-Path $run 'firmware.dat'
    Copy-Item -LiteralPath $source -Destination $staged
    Assert-FileHash $staged $sourceHash
    $selectedVariant = if ($Action -eq 'Restaurer') { 'Officiel' } else { $Variante }
    $request = "Action=$Action`r`nVariante=$selectedVariant`r`nSHA256=$((Get-FileHash -LiteralPath $staged).Hash)`r`n"
    if ($selectedVariant -eq 'Profil') {
        $request += "ProfilManifest=$ProfilManifest`r`nTouches=$($verified.mapping_summary)`r`n"
    }
    [IO.File]::WriteAllText((Join-Path $run 'request.txt'), $request)
    Write-Host "Updater integre ; action : $Action ; variante $selectedVariant, base 2.07. Ne pas debrancher pendant le transfert."
    Write-Host "Journaux : $run"
    $devices = @(Get-AdapterInventory)
    if ($devices.Count -ne 1 -or $devices[0] -notmatch '^USB\\VID_0483&PID_5760\\') {
        throw 'Les appareils USB ont change pendant la preparation. Aucun transfert lance.'
    }
    try {
        Start-Transcript -LiteralPath (Join-Path $run 'console.log') | Out-Null
        [NativeFlasher]::Run($staged, $sourceHash)
        [IO.File]::WriteAllText((Join-Path $run 'result.txt'), 'native_transfer_completed; hardware_function_untested')
    } finally {
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    }
    Write-Host 'Transfert integre termine. Tester cette variante sur le materiel.'
    exit 0
} catch {
    if ($run -and (Test-Path -LiteralPath $run -PathType Container)) {
        [IO.File]::WriteAllText((Join-Path $run 'result.txt'), ('FAILED: ' + $_.Exception.Message))
    }
    Write-Host ("ECHEC : " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
