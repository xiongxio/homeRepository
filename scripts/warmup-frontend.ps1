param(
    [int]$Port = 5173,
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Continue"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$urls = @(
    "http://127.0.0.1:$Port/",
    "http://127.0.0.1:$Port/src/main.tsx",
    "http://127.0.0.1:$Port/src/index.css"
)

$allOk = $true
foreach ($url in $urls) {
    $ok = $false
    while (-not $ok -and (Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                $ok = $true
            }
        }
        catch {
            $status = $null
            if ($_.Exception.Response) {
                $status = [int]$_.Exception.Response.StatusCode
            }
            if ($status -eq 404) {
                # 404 means this dev-server-only module is not applicable
                # (for example when serving a built bundle).
                $ok = $true
            }
            else {
                Start-Sleep -Milliseconds 500
            }
        }
    }

    if (-not $ok) {
        $allOk = $false
        Write-Host "[WARN] Warm-up timed out: $url"
    }
}

if ($allOk) {
    exit 0
}
exit 1
