# Sign AgriVisio binaries using a local Authenticode code signing certificate
$ErrorActionPreference = "Stop"

$subject = "CN=AgriVisio Precision Optics"
$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue | Where-Object { $_.Subject -eq $subject } | Select-Object -First 1

if (-not $cert) {
    Write-Host "Creating local Code Signing certificate for $subject..."
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $subject -CertStoreLocation Cert:\CurrentUser\My -HashAlgorithm SHA256 -NotAfter (Get-Date).AddYears(5)
    
    try {
        $storePub = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPublisher", "CurrentUser")
        $storePub.Open("ReadWrite")
        $storePub.Add($cert)
        $storePub.Close()
    } catch {
        Write-Warning "Could not add to TrustedPublisher store: $_"
    }
}

$filesToSign = @(
    "$PSScriptRoot\..\AgriVisio.exe",
    "$PSScriptRoot\..\AgriVisioSetup.exe",
    "$env:LOCALAPPDATA\Programs\AgriVisio\AgriVisio.exe"
)

foreach ($filePath in $filesToSign) {
    if (Test-Path $filePath) {
        Write-Host "Signing $filePath..."
        $sig = Set-AuthenticodeSignature -FilePath $filePath -Certificate $cert -HashAlgorithm SHA256
        Write-Host "Status: $($sig.Status)"
    }
}
