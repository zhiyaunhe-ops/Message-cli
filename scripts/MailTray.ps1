# Mail WebUI - system tray controller (no third-party dependency)
# Double click "MailTray.bat" on the Desktop to run this.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# 单例：避免重复启动多个托盘
$created = $false
$mtx = New-Object System.Threading.Mutex($true, "Global\MailWebUITray", [ref]$created)
if (-not $created) { exit }

$root = Split-Path -Parent $PSScriptRoot
$py   = "C:\Users\<user>\.workbuddy\binaries\python\envs\mailui\Scripts\python.exe"
$port = 8791
$url  = "http://127.0.0.1:$port/"
$log  = Join-Path $root "data\server.log"
$errLog = Join-Path $root "data\server.err.log"

function Get-ServerPid {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($c) { return ($c | Select-Object -First 1).OwningProcess }
    return $null
}

function Start-Server {
    if (Get-ServerPid) { return $false }
    Start-Process -FilePath $py `
        -ArgumentList @("run.py", "serve", "--host", "127.0.0.1", "--port", "$port") `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput $log -RedirectStandardError $errLog | Out-Null
    return $true
}

function Stop-Server {
    $p = Get-ServerPid
    if ($p) {
        Stop-Process -Id ($p | Select-Object -First 1) -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
}

function Test-ServerApi {
    try {
        $r = Invoke-RestMethod -Uri "$($url)api/status" -TimeoutSec 3
        return ($r.ok -eq $true)
    } catch { return $false }
}

# ---------------- tray ----------------
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Application
$icon.Text = "Mail WebUI"
$icon.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$miOpen = $menu.Items.Add("打开信箱")
$miOpen.add_Click({ Start-Process $url })

$miStatus = $menu.Items.Add("状态：检查中…")
$miStatus.Enabled = $false

$menu.Items.Add("-") | Out-Null

$miSync = $menu.Items.Add("立即同步邮件")
$miSync.add_Click({
    try {
        Invoke-RestMethod -Method Post -Uri "$($url)api/sync" -TimeoutSec 120 `
            -ContentType "application/json" `
            -Body '{"all_folders":true,"since":"2026-07-01","with_body":true}' | Out-Null
        $icon.ShowBalloonTip(3000, "Mail WebUI", "同步完成", "Info")
    } catch {
        $icon.ShowBalloonTip(3000, "Mail WebUI", "同步失败：$($_.Exception.Message)", "Error")
    }
    Update-Status
})

$miRestart = $menu.Items.Add("重启服务")
$miRestart.add_Click({
    Stop-Server
    Start-Server | Out-Null
    Start-Sleep -Seconds 2
    $icon.ShowBalloonTip(3000, "Mail WebUI", "服务已重启", "Info")
    Update-Status
})

$miStop = $menu.Items.Add("停止服务")
$miStop.add_Click({
    Stop-Server
    $icon.ShowBalloonTip(3000, "Mail WebUI", "服务已停止", "Info")
    Update-Status
})

$miLog = $menu.Items.Add("查看日志")
$miLog.add_Click({ if (Test-Path $log) { Start-Process notepad.exe $log } })

$menu.Items.Add("-") | Out-Null

$miExit = $menu.Items.Add("退出托盘（服务保持运行）")
$miExit.add_Click({
    $timer.Stop()
    $icon.Visible = $false
    $icon.Dispose()
    $context.ExitThread()
})

$icon.ContextMenuStrip = $menu
$icon.add_Click({
    param($s, $e)
    if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        if (Get-ServerPid) { Start-Process $url } else { Start-Server | Out-Null; Start-Sleep -Seconds 2; Start-Process $url }
        Update-Status
    }
})

function Update-Status {
    if (Get-ServerPid) {
        if (Test-ServerApi) {
            $icon.Text = "Mail WebUI - 运行中 ($port)"
            $miStatus.Text = "状态：运行中"
        } else {
            $icon.Text = "Mail WebUI - 启动中…"
            $miStatus.Text = "状态：启动中…"
        }
    } else {
        $icon.Text = "Mail WebUI - 已停止"
        $miStatus.Text = "状态：已停止"
    }
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick({ Update-Status })
$timer.Start()

# 启动服务并显示气泡提示
$started = Start-Server
Update-Status
$icon.ShowBalloonTip(4000, "Mail WebUI", "已启动 http://127.0.0.1:$port （左键图标打开，右键看菜单）", "Info")

$context = New-Object System.Windows.Forms.ApplicationContext
[System.Windows.Forms.Application]::Run($context)
