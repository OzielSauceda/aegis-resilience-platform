param([string]$Project = 'aegis-batch2-validation')

# Uses a separate Compose project and unique order IDs. Preserves named volumes.
# Requires the ShopSim images to have been built for this project first.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$client = New-Object System.Net.Http.HttpClient
$client.Timeout = [TimeSpan]::FromSeconds(5)

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

function Stock([string]$SKU) { return [int](Compose exec -T redis redis-cli --raw GET "stock:$SKU") }
function Ledger([string]$Order) {
    # Order IDs below are generated locally from GUIDs, not user-supplied SQL.
    return (Compose exec -T postgres psql -U shopsim -d shopsim -Atc "SELECT count(*) || ':' || min(amount_cents) FROM payments WHERE order_id = '$Order';").Trim()
}

Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    Compose up -d --wait
    Compose ps
    foreach ($port in 8080,8081,8082) { $null = Request $port '/healthz' 200 }
    foreach ($port in 8081,8082) { $null = Request $port '/readyz' 200 }

    # The build stage has Go, source, and downloaded modules for real-store tests.
    & docker build --target build -t aegis-shopsim-storage-tests -f shopsim/payment/Dockerfile .
    if ($LASTEXITCODE -ne 0) { throw 'test image build failed' }
    & docker run --rm --network "${Project}_default" --memory 1g --cpus 1 `
        -e 'GOMEMLIMIT=512MiB' `
        -e 'TEST_DATABASE_URL=postgres://shopsim:shopsim-local@postgres:5432/shopsim?sslmode=disable&connect_timeout=1' `
        -e 'TEST_REDIS_URL=redis://redis:6379/0' `
        aegis-shopsim-storage-tests go test -p 1 -v -count=1 ./shopsim/payment ./shopsim/inventory
    if ($LASTEXITCODE -ne 0) { throw 'real-store Go tests failed' }

    $order = 'validation-' + [Guid]::NewGuid().ToString('N')
    $body = @{ order_id = $order; sku = 'sku-001'; quantity = 2; amount_cents = 2500 }
    $before = Stock 'sku-001'
    $first = Request 8080 '/checkout' 200 $body
    Assert-Equal $first.status 'completed' 'checkout completed'
    Assert-Equal (Stock 'sku-001') ($before - 2) 'first reservation decremented stock'
    $second = Request 8080 '/checkout' 200 $body
    Assert-Equal $second.inventory.reservation_id $first.inventory.reservation_id 'same reservation ID'
    Assert-Equal $second.payment.payment_id $first.payment.payment_id 'same payment ID'
    Assert-Equal (Stock 'sku-001') ($before - 2) 'replay did not decrement stock'
    Assert-Equal (Ledger $order) '1:2500' 'one unchanged payment row'
    Assert-Equal ((Compose exec -T redis redis-cli --raw HGET "reservation:$order" sku).Trim()) 'sku-001' 'stored reservation SKU'
    Assert-Equal ((Compose exec -T redis redis-cli --raw HGET "reservation:$order" quantity).Trim()) '2' 'stored reservation quantity'

    $conflict = $body.Clone(); $conflict.amount_cents = 2600
    $null = Request 8080 '/checkout' 409 $conflict
    $conflict = $body.Clone(); $conflict.quantity = 3
    $null = Request 8080 '/checkout' 409 $conflict
    Assert-Equal (Stock 'sku-001') ($before - 2) 'conflicts preserved stock'
    Assert-Equal (Ledger $order) '1:2500' 'conflicts preserved ledger'
    $null = Request 8082 '/reserve' 409 @{order_id = "$order-empty"; sku = 'sku-003'; quantity = 26}

    Compose stop postgres
    $null = Request 8081 '/healthz' 200
    $null = Request 8081 '/readyz' 503
    $null = Request 8081 '/charge' 503 @{order_id = "$order-down"; amount_cents = 2500}
    $partial = @{order_id = "$order-partial"; sku = 'sku-002'; quantity = 1; amount_cents = 1000}
    $partialBefore = Stock 'sku-002'
    $null = Request 8080 '/checkout' 502 $partial
    Assert-Equal (Stock 'sku-002') ($partialBefore - 1) 'partial failure retains reservation'
    Compose up -d --wait
    $null = Request 8080 '/checkout' 200 $partial
    Assert-Equal (Stock 'sku-002') ($partialBefore - 1) 'manual replay after recovery did not reserve twice'
    Assert-Equal (Ledger $partial.order_id) '1:1000' 'recovered payment stored once'

    Compose stop redis
    $null = Request 8082 '/healthz' 200
    $null = Request 8082 '/readyz' 503
    $null = Request 8082 '/reserve' 503 @{order_id = "$order-redis-down"; sku = 'sku-001'; quantity = 1}
    Compose up -d --wait

    Compose restart checkout payment inventory postgres redis
    Compose up -d --wait
    $null = Request 8080 '/checkout' 200 $body
    Assert-Equal (Stock 'sku-001') ($before - 2) 'stock survived service and datastore restart'
    Assert-Equal (Ledger $order) '1:2500' 'ledger survived restart'
    Compose logs --no-color checkout payment inventory
    Write-Host 'All stateful validation checks passed.'
} finally {
    try { Compose down } finally { $client.Dispose(); Pop-Location }
}
