param(
    [Parameter(Mandatory = $true)]
    [string]$LogFile,

    [Parameter(Mandatory = $true)]
    [string]$GroupTitle
)

function Escape-GithubMessage {
    param([string]$Value)
    $Value -replace '%', '%25' -replace "`r", '%0D' -replace "`n", '%0A'
}

if (-not (Test-Path -LiteralPath $LogFile)) {
    Write-Host "::error title=CI log missing::No CI test log at $LogFile"
    exit 1
}

Write-Host "::group::$GroupTitle"
$failed = $false
Get-Content -LiteralPath $LogFile | ForEach-Object {
    $line = $_
    Write-Host $line
    if ($line -like 'PASS *') {
        $msg = Escape-GithubMessage $line.Substring(5)
        Write-Host "::notice title=$GroupTitle::$msg"
    }
    elseif ($line -like 'FAIL *') {
        $msg = Escape-GithubMessage $line.Substring(5)
        Write-Host "::error title=$GroupTitle::$msg"
        $failed = $true
    }
}
Write-Host '::endgroup::'

if ($failed) {
    exit 1
}