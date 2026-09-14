# Shared by the local validation scripts. The caller owns $Project and $client.
function Compose {
    $output = & docker compose -p $Project @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed: $args" }
    return $output
}

function Assert-Equal($Actual, $Expected, [string]$Label) {
    if ($Actual -ne $Expected) { throw "${Label}: expected '$Expected', got '$Actual'" }
    Write-Host "PASS ${Label}: $Actual"
}

function Request([int]$Port, [string]$Path, [int]$Expected, $Body = $null) {
    $uri = "http://127.0.0.1:$Port$Path"
    $content = $null
    if ($null -eq $Body) {
        $response = $client.GetAsync($uri).GetAwaiter().GetResult()
    } else {
        $content = New-Object System.Net.Http.StringContent(($Body | ConvertTo-Json -Compress), [Text.Encoding]::UTF8, 'application/json')
        $response = $client.PostAsync($uri, $content).GetAwaiter().GetResult()
    }
    try {
        $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        Assert-Equal ([int]$response.StatusCode) $Expected "$Path on $Port"
        return ($text | ConvertFrom-Json)
    } finally {
        $response.Dispose()
        if ($null -ne $content) { $content.Dispose() }
    }
}
