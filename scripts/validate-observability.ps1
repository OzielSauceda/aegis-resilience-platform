param([string]$Project = 'aegis-batch3-validation', [switch]$KeepRunning)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$client = New-Object System.Net.Http.HttpClient
$client.Timeout = [TimeSpan]::FromSeconds(5)
. "$PSScriptRoot/validation-helpers.ps1"

function Tag($Span, [string]$Key) {
    return ($Span.tags | Where-Object { $_.key -eq $Key } | Select-Object -First 1).value
}

function Wait-HTTP([string]$Uri) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try { $null = Invoke-RestMethod -Uri $Uri -TimeoutSec 2; return } catch { Start-Sleep -Seconds 1 }
    }
    throw "Endpoint did not become available: $Uri"
}

function Wait-Trace([string]$Order, [int]$Status) {
    $tags = [Uri]::EscapeDataString((@{'shopsim.order_id' = $Order} | ConvertTo-Json -Compress))
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $result = Invoke-RestMethod -Uri "http://127.0.0.1:16686/api/traces?service=checkout&tags=$tags&limit=20&lookback=1h" -TimeoutSec 3
        foreach ($trace in $result.data) {
            $root = @($trace.spans | Where-Object { $_.operationName -eq 'POST /checkout' })
            if ($root.Count -eq 1 -and (Tag $root[0] 'http.response.status_code') -eq $Status -and @($trace.spans).Count -eq 7) { return $trace }
        }
        Start-Sleep -Seconds 1
    }
    throw "No complete trace for $Order with root HTTP $Status"
}

function Verify-Trace($Trace, [int]$Status, [bool]$StorageFailure) {
    $spans = @($Trace.spans)
    Assert-Equal $spans.Count 7 'seven connected spans'
    $byID = @{}
    $rows = foreach ($span in $spans) {
        $byID[$span.spanID] = $span
        Assert-Equal $span.traceID $Trace.traceID 'shared Trace ID'
        if ($span.duration -le 0) { throw 'Non-positive span duration' }
        [pscustomobject]@{
            service = $Trace.processes.($span.processID).serviceName
            name = $span.operationName
            kind = Tag $span 'span.kind'
            span_id = $span.spanID
            parent_id = (@($span.references | Where-Object { $_.refType -eq 'CHILD_OF' }) | Select-Object -First 1).spanID
            duration_us = $span.duration
            http_status = Tag $span 'http.response.status_code'
            outcome = Tag $span 'shopsim.outcome'
            error = Tag $span 'error'
        }
    }
    Assert-Equal (($rows.service | Sort-Object -Unique) -join ',') 'checkout,inventory,payment' 'service identities'
    $root = @($rows | Where-Object { $_.name -eq 'POST /checkout' })[0]
    Assert-Equal $root.service 'checkout' 'root service'
    Assert-Equal $root.kind 'server' 'root kind'
    Assert-Equal ([string]$root.parent_id) '' 'root has no parent'
    Assert-Equal $root.http_status $Status 'root HTTP status'
    foreach ($service in 'inventory','payment') {
        $route = if ($service -eq 'inventory') { 'POST /reserve' } else { 'POST /charge' }
        $operation = if ($service -eq 'inventory') { 'redis.reserve' } else { 'postgresql.charge' }
        $server = @($rows | Where-Object { $_.name -eq $route -and $_.service -eq $service })
        $storage = @($rows | Where-Object { $_.name -eq $operation -and $_.service -eq $service })
        Assert-Equal $server.Count 1 "$service SERVER count"
        Assert-Equal $server[0].kind 'server' "$service SERVER kind"
        Assert-Equal $storage.Count 1 "$service storage span count"
        Assert-Equal $storage[0].kind 'client' "$service datastore CLIENT kind"
        Assert-Equal $storage[0].parent_id $server[0].span_id "$operation parent"
        $httpClient = @($rows | Where-Object { $_.span_id -eq $server[0].parent_id })
        Assert-Equal $httpClient.Count 1 "$service incoming parent exists"
        Assert-Equal $httpClient[0].kind 'client' "$service incoming parent kind"
        Assert-Equal $httpClient[0].service 'checkout' "$service HTTP CLIENT owner"
        Assert-Equal $httpClient[0].parent_id $root.span_id "$service HTTP CLIENT parent"
        Assert-Equal (Tag $byID[$httpClient[0].span_id] 'http.request.method') 'POST' "$service HTTP method"
        if ($service -eq 'payment' -and $StorageFailure) {
            Assert-Equal $storage[0].error $true 'PostgreSQL failure marked ERROR'
            Assert-Equal $server[0].http_status 503 'Payment failure status'
            Assert-Equal $httpClient[0].http_status 503 'Checkout observes Payment failure'
            if (@($byID[$storage[0].span_id].logs).Count -eq 0) { throw 'Missing datastore error event' }
        } else {
            Assert-Equal $server[0].http_status 200 "$service HTTP status"
            if ($storage[0].error -eq $true) { throw 'Successful datastore operation marked ERROR' }
        }
    }
    if ($StorageFailure) { Assert-Equal $root.error $true 'Checkout failure marked ERROR' }
    $serialized = $Trace | ConvertTo-Json -Depth 30
    if ($serialized -match 'shopsim-local|postgres://|redis://') { throw 'Connection details leaked into trace' }
    $rows | Sort-Object service,name | Format-Table -AutoSize | Out-Host
}

function Save-Trace($Trace, [string]$Name) {
    $Trace | ConvertTo-Json -Depth 30 | Set-Content -Encoding UTF8 (Join-Path $evidence "$Name.json")
    Write-Host "TRACE $Name $($Trace.traceID)"
}

Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $evidence = Join-Path (Get-Location) 'validation-artifacts/batch3'
    $null = New-Item -ItemType Directory -Force -Path $evidence
    Compose up -d --wait
    Wait-HTTP 'http://127.0.0.1:13133/'
    Wait-HTTP 'http://127.0.0.1:16686/api/services'
    $order = 'trace-' + [Guid]::NewGuid().ToString('N')
    $body = @{order_id=$order; sku='sku-001'; quantity=1; amount_cents=2500}
    $null = Request 8080 '/checkout' 200 $body
    $success = Wait-Trace $order 200
    Verify-Trace $success 200 $false
    Save-Trace $success 'success'
    $collectorLogs = (Compose logs --no-color otel-collector) -join "`n"
    if ($collectorLogs -notmatch '"spans": [1-9]') { throw 'Collector debug exporter did not report receiving spans' }
    Write-Host 'PASS Collector received spans'

    Compose stop postgres
    $body.order_id = "$order-failure"
    $stockBefore = [int](Compose exec -T redis redis-cli --raw GET stock:sku-001)
    $null = Request 8080 '/checkout' 502 $body
    $failure = Wait-Trace $body.order_id 502
    Verify-Trace $failure 502 $true
    Save-Trace $failure 'postgres-failure'
    Assert-Equal ([int](Compose exec -T redis redis-cli --raw GET stock:sku-001)) ($stockBefore-1) 'failed checkout retains reservation'
    Compose start postgres
    Wait-HTTP 'http://127.0.0.1:8081/readyz'
    $null = Request 8080 '/checkout' 200 $body
    $recovery = Wait-Trace $body.order_id 200
    Verify-Trace $recovery 200 $false
    Save-Trace $recovery 'recovery'
    Assert-Equal ([int](Compose exec -T redis redis-cli --raw GET stock:sku-001)) ($stockBefore-1) 'recovery did not reserve again'
    $sql = "SELECT count(*) FROM payments WHERE order_id='$($body.order_id)';"
    Assert-Equal ((Compose exec -T postgres psql -U shopsim -d shopsim -Atc $sql).Trim()) '1' 'one recovered payment'

    # Wait through Docker probe intervals and explicitly send extra probes.
    foreach ($port in 8080,8081,8082) { $null = Request $port '/healthz' 200 }
    foreach ($port in 8081,8082) { $null = Request $port '/readyz' 200 }
    Start-Sleep -Seconds 6
    foreach ($service in 'checkout','inventory','payment') {
        $recent = Invoke-RestMethod -Uri "http://127.0.0.1:16686/api/traces?service=$service&limit=100&lookback=1h"
        foreach ($trace in $recent.data) {
            foreach ($span in $trace.spans) {
                if ($span.operationName -match '/healthz|/readyz' -or (Tag $span 'url.path') -match '^/(healthz|readyz)$') { throw 'Probe trace noise detected' }
            }
        }
    }
    Write-Host 'PASS no health/readiness spans in Jaeger'

    Compose stop otel-collector
    Compose restart checkout inventory payment
    foreach ($port in 8080,8081,8082) { Wait-HTTP "http://127.0.0.1:$port/healthz" }
    foreach ($port in 8081,8082) { $null = Request $port '/readyz' 200 }
    $body.order_id = "$order-collector-down"
    $null = Request 8080 '/checkout' 200 $body
    $null = Request 8080 '/checkout' 200 $body
    Start-Sleep -Seconds 4
    Compose start otel-collector
    Wait-HTTP 'http://127.0.0.1:13133/'
    Write-Host 'PASS business operations and process startup without Collector'

    Compose stop jaeger
    $body.order_id = "$order-jaeger-down"
    foreach ($port in 8081,8082) { $null = Request $port '/readyz' 200 }
    $null = Request 8080 '/checkout' 200 $body
    Start-Sleep -Seconds 4
    Compose start jaeger
    Wait-HTTP 'http://127.0.0.1:16686/api/services'
    $body.order_id = "$order-restored"
    $null = Request 8080 '/checkout' 200 $body
    $restored = Wait-Trace $body.order_id 200
    Verify-Trace $restored 200 $false
    Save-Trace $restored 'restored'
    Compose logs --no-color checkout inventory payment otel-collector | Set-Content -Encoding UTF8 (Join-Path $evidence 'services.log')
    $containers = Compose ps -q
    & docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' @containers
    if ($LASTEXITCODE -ne 0) { throw 'docker stats failed' }
    Write-Host 'All observability validation checks passed. Evidence: validation-artifacts/batch3/'
} finally {
    try { if (-not $KeepRunning) { Compose down } } finally { $client.Dispose(); Pop-Location }
}
