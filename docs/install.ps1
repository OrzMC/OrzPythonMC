# OrzMC 一键安装器(Windows)—— PowerShell 5.1+。
#
# 用法(主推,一条命令):
#   irm https://orzmc.github.io/OrzPythonMC/install.ps1 | iex
# 指定版本(绕过 GitHub API 限流):
#   $code = (irm https://orzmc.github.io/OrzPythonMC/install.ps1)
#   & ([scriptblock]::Create($code)) -version vX.Y.Z
# 或下载到本地后直接执行:
#   powershell -ExecutionPolicy Bypass -File install.ps1 -version vX.Y.Z
# 其它选项:-dir <path> / -no-modify-rc / -file <path>(本地安装,测试接缝)/ -uninstall / -help。
#
# 设计约束:
#   * 不用 param() 块——兼容 `irm | iex`(脚本内容被求值时无脚本参数表);
#     参数从 $args 手工解析 token。
#   * 不显式 exit——`irm | iex` 场景下 exit 会连宿主 PowerShell 窗口一起关掉。
#     错误一律 throw(终止异常):`powershell -File` 下未捕获异常退出码为 1,
#     iex 场景则只报错、宿主窗口保留。
#   * 共享状态统一用 $script: 前缀,保证 `-File`(脚本作用域)与
#     `irm | iex`(调用方全局作用域)两种调用方式行为一致。
#   * 编码自愈:GitHub Pages 对 .ps1 返回 octet-stream(无 charset),PS 5.1 的
#     irm 会逐字节(Latin-1)误解码,中文显示乱码(功能不受影响)。顶部自愈块探测
#     到误解码时,重抓自身 URL 恢复原始 UTF-8 源码后重新执行,任何环境都不乱码。

# ── 编码自愈(仅 PS 5.1 经 irm 按 octet-stream 逐字节误解码时触发)───────────────
# 探测:若下方中文探测串不含 CJK 字符(≥ U+2000)= 已被逐字节误解码。此时重抓自身
# URL,把逐字节字符反转回 UTF-8 字节恢复原始源码,带上原参数重新执行,中文输出恢复
# 正常。pwsh 与 -File(带 BOM)解码正确,探测为干净,不走此路径;重抓失败则降级继续。
$__probe = '已安装到'
$__needHeal = $true
foreach ($__c in $__probe.ToCharArray()) {
    if ([int][char]$__c -ge 0x2000) { $__needHeal = $false; break }
}
if ($__needHeal) {
    $__url = $env:ORZMC_INSTALL_URL
    if (-not $__url) { $__url = 'https://orzmc.github.io/OrzPythonMC/install.ps1' }
    try {
        $__raw = Invoke-RestMethod -Uri $__url
        $__bytes = New-Object byte[] $__raw.Length
        for ($__i = 0; $__i -lt $__raw.Length; $__i++) { $__bytes[$__i] = [byte][int][char]$__raw[$__i] }
        $__clean = [Text.Encoding]::UTF8.GetString($__bytes)
        if ($__clean -match 'OrzMC') {
            & ([scriptblock]::Create($__clean)) @args
            return
        }
    } catch {
        # 重抓失败:降级,用当前(乱码但功能正常)的脚本继续执行
    }
}
Remove-Variable __probe,__needHeal,__c -ErrorAction SilentlyContinue

$savedErrorActionPreference = $ErrorActionPreference
$savedProgressPreference = $ProgressPreference
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue' # 加速 Invoke-WebRequest 大文件下载

$Repo = 'OrzMC/OrzPythonMC'
$BaseUrl = "https://github.com/$Repo"
# ORZMC_API_LATEST 是测试/镜像接缝:指到不可用地址即可验证限流兜底。
if ($env:ORZMC_API_LATEST) { $ApiLatest = $env:ORZMC_API_LATEST }
else { $ApiLatest = "https://api.github.com/repos/$Repo/releases/latest" }

# 参数与运行期状态
$script:Version = ''
$script:Dir = ''
$script:NoRc = $false
$script:File = ''
$script:Mode = 'install'
$script:Url = ''
$script:Asset = ''
$script:Platform = ''
$script:InstallDir = ''
$script:Binary = ''
$script:PathLine = ''
$script:StateDir = ''
$script:Manifest = ''
$script:RootDir = ''
$script:RecordVersion = ''
$script:LatestTag = ''
$script:TempFile = ''

function die([string]$msg) {
    Write-Host "错误: $msg" -ForegroundColor Red
    throw 'OrzMC-Install-Failed'
}

function Show-Help {
    @'
OrzMC 一键安装器(Windows / PowerShell 5.1+)

用法:
  irm https://orzmc.github.io/OrzPythonMC/install.ps1 | iex
  powershell -ExecutionPolicy Bypass -File install.ps1
  # 指定版本(绕过 GitHub API 限流):
  powershell -ExecutionPolicy Bypass -File install.ps1 -version vX.Y.Z

选项:
  -version vX.Y.Z   安装指定版本(绕过 GitHub API 限流)
  -dir <path>       自定义安装目录(默认 %LOCALAPPDATA%\Programs\orzmc)
  -no-modify-rc     不修改用户 PATH(仅打印手动提示)
  -file <path>      从本地文件安装(测试 / 开发用)
  -uninstall        反向卸载:读 manifest 删二进制 + 还原 PATH(不删游戏数据;
                    常规卸载请用内置的 orzmc self-uninstall)
  -help             显示本帮助

卸载(内置,推荐):
  orzmc self-uninstall [--yes] [--remove-root] [--force]

升级(内置,也可重跑本命令覆盖安装):
  orzmc update [--check] [-v vX.Y.Z]
'@
}

# ── 平台探测 ────────────────────────────────────────────────────────────────
function Detect-Platform {
    # 32 位 PowerShell 下 PROCESSOR_ARCHITECTURE 是 x86,真实架构在 ARCHITEW6432
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    switch ($arch) {
        'AMD64' { $script:Asset = 'orzmc-windows-x86_64.exe'; $script:Platform = 'windows-x86_64' }
        'ARM64' { $script:Asset = 'orzmc-windows-arm64.exe'; $script:Platform = 'windows-arm64' }
        default { die "暂不支持该 Windows 架构: $arch(本安装器支持 x86_64 / arm64)" }
    }
}

# ── 用户目录 / state / 安装目录基路径(LOCALAPPDATA)────────────────────────
function User-Profile {
    $p = $env:USERPROFILE
    if (-not $p) { $p = [Environment]::GetFolderPath('UserProfile') } # 跨平台(Unix 取 home)
    return $p
}

function State-Base {
    if ($env:LOCALAPPDATA) { return $env:LOCALAPPDATA }
    return (Join-Path (User-Profile) 'AppData\Local')
}

# ── 下载地址解析 ────────────────────────────────────────────────────────────
# 最新版解析的兜底:releases/latest 会 302 到 releases/tag/<tag>。这个端点不是
# REST API,不受「60 次/时/IP」限流影响(CI runner 与共享 NAT 用户常被限流)。
function Get-ReleaseRedirectTag {
    try {
        $request = [System.Net.WebRequest]::Create("$BaseUrl/releases/latest")
        $request.AllowAutoRedirect = $false
        $request.Method = 'GET'
        $request.Timeout = 15000
        $response = $request.GetResponse()
        $location = [string]$response.Headers['Location']
        $response.Close()
    }
    catch {
        return ''
    }
    if ($location -match '/releases/tag/([^/?#]+)') {
        return $Matches[1]
    }
    return ''
}

function Resolve-Url {
    if ($script:File) { return } # 本地文件安装,无 URL
    if ($script:Version) {
        if (-not $script:Version.StartsWith('v')) { $script:Version = "v$script:Version" }
        $script:Url = "$BaseUrl/releases/download/$script:Version/$script:Asset"
        return
    }
    try {
        $release = Invoke-RestMethod -Uri $ApiLatest -Headers @{ 'User-Agent' = 'orzmc-installer' }
    }
    catch {
        # API 限流/网络失败 → 退回 302 端点,自己拼资产地址。
        $tag = Get-ReleaseRedirectTag
        if (-not $tag) {
            die "无法获取最新版本信息(网络问题或 GitHub API 限流)。请指定版本重试: -version vX.Y.Z"
        }
        $script:LatestTag = $tag
        $script:Url = "$BaseUrl/releases/download/$tag/$script:Asset"
        return
    }
    $match = @($release.assets | Where-Object { $_.name -eq $script:Asset })
    if (-not $match) {
        die "最新 release 中找不到资产 $($script:Asset)(可能尚未发布该平台产物)。请指定版本: -version vX.Y.Z"
    }
    $script:Url = $match[0].browser_download_url
    $script:LatestTag = [string]$release.tag_name
}

# ── 下载与校验 ──────────────────────────────────────────────────────────────
function Test-PeMagic([string]$path) {
    $fs = [System.IO.File]::OpenRead($path)
    try {
        $head = New-Object byte[] 2
        [void]$fs.Read($head, 0, 2)
    }
    finally {
        $fs.Dispose()
    }
    return ($head[0] -eq 0x4D -and $head[1] -eq 0x5A) # "MZ"
}

function Download {
    # 仅校验远程下载产物;--file(本地构建)跳过。
    # 用 [System.IO.Path]::GetTempPath():Windows 即 %TEMP%,且避免了
    # $env:TEMP 为空时 Join-Path 绑定 null 抛非终止错误的坑。
    $script:TempFile = Join-Path ([System.IO.Path]::GetTempPath()) "orzmc-dl-$script:Asset"
    if (Test-Path $script:TempFile) { Remove-Item -Force $script:TempFile }
    if ($script:File) {
        Copy-Item -Force $script:File $script:TempFile
    }
    else {
        try {
            Invoke-WebRequest -Uri $script:Url -OutFile $script:TempFile -UseBasicParsing
        }
        catch {
            Remove-Item -Force $script:TempFile -ErrorAction SilentlyContinue
            die "下载失败: $script:Url"
        }
    }
    if (-not (Test-Path $script:TempFile) -or (Get-Item $script:TempFile).Length -eq 0) {
        Remove-Item -Force $script:TempFile -ErrorAction SilentlyContinue
        die '下载产物为空,已中止安装'
    }
    # 仅校验远程下载产物;-file(本地构建 / 测试接缝)跳过。
    if (-not $script:File -and -not (Test-PeMagic $script:TempFile)) {
        Remove-Item -Force $script:TempFile -ErrorAction SilentlyContinue
        die '下载产物校验失败(非 PE 可执行文件,可能命中错误页/代理页)。已中止安装。'
    }
}

# ── 安装 ─────────────────────────────────────────────────────────────────────
function Install-Binary {
    [void](New-Item -ItemType Directory -Force -Path $script:InstallDir)
    Move-Item -Force $script:TempFile $script:Binary
}

# ── PATH 登记(幂等,只动用户 PATH)─────────────────────────────────────────
function Register-Path {
    $script:PathLine = ''
    if ($script:NoRc -or $env:ORZMC_NO_RC) { return }
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $tokens = @($user -split ';' | Where-Object { $_ })
    if ($tokens -contains $script:InstallDir) {
        $script:PathLine = $script:InstallDir # 已在用户 PATH,记录以便卸载时清理
        return
    }
    $tokens += $script:InstallDir
    # 尾部留一个 ';',让后续变量展开(%VAR% 以 ';' 结尾追加)保持兼容
    [Environment]::SetEnvironmentVariable('Path', (($tokens -join ';') + ';'), 'User')
    $script:PathLine = $script:InstallDir
}

# ── manifest 写入 ────────────────────────────────────────────────────────────
function Write-Manifest {
    $script:StateDir = Join-Path (State-Base) 'orzmc'
    $script:Manifest = Join-Path $script:StateDir 'install.conf'
    [void](New-Item -ItemType Directory -Force -Path $script:StateDir)
    if ($script:File) {
        $script:RecordVersion = 'local-build'
    }
    elseif ($script:LatestTag) {
        $script:RecordVersion = $script:LatestTag
    }
    else {
        $script:RecordVersion = $script:Version
    }
    $script:RootDir = if ($env:ORZMC_ROOT_DIR) { $env:ORZMC_ROOT_DIR } else { Join-Path (User-Profile) 'minecraft' }
    $lines = @(
        'tool=orzmc',
        'schema=1',
        "version=$script:RecordVersion",
        "platform=$script:Platform",
        "install_dir=$script:InstallDir",
        "binary=$script:Binary"
    )
    if ($script:Url) { $lines += "source=$script:Url" }
    # Windows 不写 path_file;卸载还原 PATH 只需 install_dir 这一个 token
    if ($script:PathLine) { $lines += "path_line=$script:PathLine" }
    $lines += "root_dir=$script:RootDir"
    # PS 5.1 Set-Content -Encoding UTF8 写 BOM;库侧 utf-8-sig 读取,兼容。
    Set-Content -Path $script:Manifest -Value $lines -Encoding UTF8
}

# ── 安装主流程 ───────────────────────────────────────────────────────────────
function Do-Install {
    Detect-Platform
    if ($env:ORZMC_BIN) {
        $script:InstallDir = $env:ORZMC_BIN
    }
    elseif ($script:Dir) {
        $script:InstallDir = $script:Dir
    }
    else {
        $script:InstallDir = Join-Path (State-Base) 'Programs\orzmc'
    }
    $script:Binary = Join-Path $script:InstallDir 'orzmc.exe'
    Resolve-Url
    Download
    Install-Binary
    Register-Path
    Write-Manifest

    Write-Host "[OK] orzmc 已安装到 $($script:Binary)" -ForegroundColor Green
    if ($script:PathLine) {
        Write-Host '已写入用户 PATH;请重新打开终端(或新开 PowerShell 窗口)后使用。'
    }
    else {
        Write-Host "未修改用户 PATH;若当前 PATH 不含 $($script:InstallDir),请手动添加:"
        Write-Host "  在「系统属性 → 环境变量」的用户 Path 中追加 $($script:InstallDir)"
    }
    Write-Host '升级:orzmc update(或重新执行本命令覆盖安装)。'
    Write-Host '卸载: orzmc self-uninstall --yes(游戏数据默认保留;--remove-root 连 ~\minecraft 一起删)'
}

# ── 卸载(shell 兜底;常规卸载请用 orzmc self-uninstall)──────────────────────
function Do-Uninstall {
    $script:StateDir = Join-Path (State-Base) 'orzmc'
    $script:Manifest = Join-Path $script:StateDir 'install.conf'
    if (-not (Test-Path $script:Manifest)) {
        Write-Host "未找到安装记录($script:Manifest)。若已安装,请运行 orzmc self-uninstall 或手动删除。"
        return
    }
    $fields = @{}
    foreach ($line in Get-Content $script:Manifest) {
        $kv = $line -split '=', 2
        if ($kv.Count -eq 2 -and $kv[0]) { $fields[$kv[0].Trim()] = $kv[1] }
    }
    $bin = $fields['binary']
    $installDir = $fields['install_dir']
    $pathLine = $fields['path_line']
    if ($bin -and (Test-Path $bin)) {
        Remove-Item -Force $bin
        Write-Host "已删除二进制: $bin"
    }
    if ($pathLine) {
        $user = [Environment]::GetEnvironmentVariable('Path', 'User')
        $tokens = @($user -split ';' | Where-Object { $_ })
        if ($tokens -contains $pathLine) {
            [Environment]::SetEnvironmentVariable('Path', (($tokens | Where-Object { $_ -ne $pathLine }) -join ';'), 'User')
            Write-Host "已还原用户 PATH:移除 $pathLine"
        }
        else {
            Write-Host "未在用户 PATH 中找到记录项 $pathLine,跳过还原"
        }
    }
    Remove-Item -Force $script:Manifest
    # 只 rmdir 空目录(state 目录 / 安装目录都是共享父目录,绝不整体删除)
    if ((Test-Path $script:StateDir) -and -not (Get-ChildItem -Force $script:StateDir)) {
        Remove-Item $script:StateDir -Force
    }
    if ($installDir -and (Test-Path $installDir) -and -not (Get-ChildItem -Force $installDir)) {
        Remove-Item $installDir -Force
    }
    Write-Host '卸载完成(游戏数据已保留)。'
}

# ── 参数解析(顶层手工解析,兼容 -File 与 irm|iex)───────────────────────────
try {
    $script:ArgList = @($args)
    $i = 0
    while ($i -lt $script:ArgList.Count) {
        $a = [string]$script:ArgList[$i]
        switch -Regex ($a) {
            '^-h$|^-help$|^--help$' { $script:Mode = 'help'; $i = $script:ArgList.Count; continue }
            '^-uninstall$|^--uninstall$' { $script:Mode = 'uninstall' }
            '^-no-modify-rc$|^--no-modify-rc$' { $script:NoRc = $true }
            '^-version$|^--version$' {
                $i++
                if ($i -ge $script:ArgList.Count) { die '-version 需要一个参数(如 -version vX.Y.Z)' }
                $script:Version = [string]$script:ArgList[$i]
            }
            '^-version=.*' { $script:Version = $a.Substring(9) }
            '^-dir$|^--dir$' {
                $i++
                if ($i -ge $script:ArgList.Count) { die '-dir 需要一个参数' }
                $script:Dir = [string]$script:ArgList[$i]
            }
            '^-dir=.*' { $script:Dir = $a.Substring(5) }
            '^-file$|^--file$' {
                $i++
                if ($i -ge $script:ArgList.Count) { die '-file 需要一个参数' }
                $script:File = [string]$script:ArgList[$i]
            }
            '^-file=.*' { $script:File = $a.Substring(6) }
            default { die "未知参数: $a(用 -help 查看用法)" }
        }
        $i++
    }

    # ── 入口 ──
    if ($script:Mode -eq 'help') {
        Show-Help
    }
    elseif ($script:Mode -eq 'uninstall') {
        Do-Uninstall
    }
    else {
        Do-Install
    }
}
finally {
    $ErrorActionPreference = $savedErrorActionPreference
    $ProgressPreference = $savedProgressPreference
}
