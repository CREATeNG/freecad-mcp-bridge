# Make a self-signed certificate with Windows PowerShell 5.1
# (.NET Framework 4.7.2 CertificateRequest). The key is created
# through CNG with plaintext export allowed, then written as
# PKCS#8 PEM. Hosts are separated by semicolons.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("RSA", "ECDSA")]
    [string]$KeyType,
    [Parameter(Mandatory = $true)]
    [string]$Hosts,
    [Parameter(Mandatory = $true)]
    [string]$CertPath,
    [Parameter(Mandatory = $true)]
    [string]$KeyPath,
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 3650)]
    [int]$ValidDays
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security

function New-ExportableCngKey([string]$kind) {
    $params = New-Object System.Security.Cryptography.CngKeyCreationParameters
    $params.ExportPolicy = (
        [System.Security.Cryptography.CngExportPolicies]::AllowPlaintextExport
    )
    $params.KeyUsage = [System.Security.Cryptography.CngKeyUsages]::AllUsages
    $params.Provider = (
        [System.Security.Cryptography.CngProvider]::MicrosoftSoftwareKeyStorageProvider
    )
    if ($kind -eq "RSA") {
        $length = [BitConverter]::GetBytes([int]4096)
        $prop = New-Object System.Security.Cryptography.CngProperty(
            "Length",
            $length,
            [System.Security.Cryptography.CngPropertyOptions]::None
        )
        $params.Parameters.Add($prop)
        $alg = [System.Security.Cryptography.CngAlgorithm]::Rsa
    } else {
        $alg = [System.Security.Cryptography.CngAlgorithm]::ECDsaP256
    }
    # $null becomes "" in PowerShell and collides with a stored key.
    return [System.Security.Cryptography.CngKey]::Create(
        $alg, [NullString]::Value, $params
    )
}

function ConvertTo-Pem([string]$label, [byte[]]$der) {
    $b64 = [Convert]::ToBase64String($der, "InsertLineBreaks")
    return "-----BEGIN $label-----`n$b64`n-----END $label-----`n"
}

$names = @($Hosts -split ";" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($names.Count -lt 1) {
    throw "At least one host is required."
}

$cng = New-ExportableCngKey $KeyType
try {
    if ($KeyType -eq "RSA") {
        $algo = New-Object System.Security.Cryptography.RSACng($cng)
        $request = New-Object `
            System.Security.Cryptography.X509Certificates.CertificateRequest(
            "CN=$($names[0])",
            $algo,
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
        )
    } else {
        $algo = New-Object System.Security.Cryptography.ECDsaCng($cng)
        $request = New-Object `
            System.Security.Cryptography.X509Certificates.CertificateRequest(
            "CN=$($names[0])",
            $algo,
            [System.Security.Cryptography.HashAlgorithmName]::SHA256
        )
    }

    $san = New-Object `
        System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder
    foreach ($name in $names) {
        $bare = $name
        if ($bare.StartsWith("[") -and $bare.EndsWith("]")) {
            $bare = $bare.Substring(1, $bare.Length - 2)
        }
        $parsed = $null
        if ([System.Net.IPAddress]::TryParse($bare, [ref]$parsed)) {
            $san.AddIpAddress($parsed)
        } else {
            $san.AddDnsName($bare)
        }
    }
    $request.CertificateExtensions.Add($san.Build())

    $basic = New-Object `
        System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension(
        $false, $false, 0, $true
    )
    $request.CertificateExtensions.Add($basic)

    $usageFlags = (
        [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature -bor
        [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyEncipherment
    )
    $usage = New-Object `
        System.Security.Cryptography.X509Certificates.X509KeyUsageExtension(
        $usageFlags, $true
    )
    $request.CertificateExtensions.Add($usage)

    $oids = New-Object System.Security.Cryptography.OidCollection
    [void]$oids.Add((New-Object System.Security.Cryptography.Oid(
        "1.3.6.1.5.5.7.3.1"
    )))
    $eku = New-Object `
        System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension(
        $oids, $false
    )
    $request.CertificateExtensions.Add($eku)

    # Valid for $ValidDays, as openssl's -days is. The period starts a
    # day early, against clock skew.
    $start = [DateTimeOffset]::UtcNow.AddDays(-1)
    $end = $start.AddDays($ValidDays)
    $cert = $request.CreateSelfSigned($start, $end)

    $pkcs8 = $cng.Export(
        [System.Security.Cryptography.CngKeyBlobFormat]::Pkcs8PrivateBlob
    )
    $certDer = $cert.RawData
} finally {
    if ($null -ne $algo) { $algo.Dispose() }
    $cng.Dispose()
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $KeyPath, (ConvertTo-Pem "PRIVATE KEY" $pkcs8), $utf8
)
[System.IO.File]::WriteAllText(
    $CertPath, (ConvertTo-Pem "CERTIFICATE" $certDer), $utf8
)
