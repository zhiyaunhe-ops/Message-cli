# Mail WebUI - silent starter (no window at all)
# Started by the Desktop shortcut; runs hidden, waits for the port,
# then opens the browser. Safe to run when the server is already up.
$root  = Split-Path -Parent $PSScriptRoot
$py    = "C:\Users\<user>\.workbuddy\binaries\python\envs\mailui\Scripts\python.exe"
$port  = 8791
$url   = "http://127.0.0.1:$port/"
$log   = Join-Path $root "data\server.log"
$errLog = Join-Path $root "data\server.err.log"

function Test-Port {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

if (-not (Test-Port)) {
    Start-Process -FilePath $py `
        -ArgumentList @("run.py", "serve", "--host", "127.0.0.1", "--port", "$port") `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput $log -RedirectStandardError $errLog | Out-Null

    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Port) { break }
    }
}

if (Test-Port) {
    Start-Process $url
} else {
    # 启动失败时留个可见提示，避免"静默地什么都没发生"
    $msg = "Mail WebUI failed to start on port $port.`nSee: $log"
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($msg, "Mail WebUI", "OK", "Error") | Out-Null
}
