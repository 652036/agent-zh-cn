#requires -Version 5.1
param(
    [ValidateSet('apply','revert','status','list')][string]$Cmd = 'apply',
    [ValidateSet('cursor','devin','windsurf','vscode')][string]$App,
    [string]$Path,
    [switch]$All,
    [switch]$Kill,
    [switch]$Restart,
    [switch]$NoLangpack,
    [switch]$Native
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# 主实现优先使用 Python；未安装 Python 时使用 PowerShell 原生实现。
if (-not $Native) {
    $pythonCommand = $null
    $pythonPrefix = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 --version 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $pythonCommand = 'py'; $pythonPrefix = @('-3') }
    }
    if (-not $pythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
        & python --version 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $pythonCommand = 'python' }
    }
    if ($pythonCommand) {
        $forward = @('-X','utf8',(Join-Path $Here 'agent_zh.py'),$Cmd)
        if ($App) { $forward += @('--app',$App) }
        if ($Path) { $forward += @('--path',$Path) }
        if ($All) { $forward += '--all' }
        if ($Kill) { $forward += '--kill' }
        if ($Restart) { $forward += '--restart' }
        if ($NoLangpack) { $forward += '--no-langpack' }
        & $pythonCommand @pythonPrefix @forward
        exit $LASTEXITCODE
    }
}

$Utf8 = New-Object Text.UTF8Encoding($false)
$Profiles = Get-Content -LiteralPath (Join-Path $Here 'src/apps.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$EntryPaths = @('out/vs/code/electron-sandbox/workbench/workbench.html',
    'out/vs/code/electron-browser/workbench/workbench.html',
    'out/vs/sessions/electron-browser/sessions.html','out/vs/sessions/electron-sandbox/sessions.html')
$Tag = '<script src="./agent-zh.js"></script>'
# 仅用于清理由早期 CursorZh 版本留下的旧注入，不再作为发布产物。
$OldCursorTag = '<script src="./cursor-zh.js"></script>'
$BackupBase = Join-Path $env:APPDATA 'AgentZh/backup'

function Read-Json([string]$File) {
    return Get-Content -LiteralPath $File -Raw -Encoding UTF8 | ConvertFrom-Json
}
Add-Type -AssemblyName System.Web.Extensions
$JsonData = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$JsonData.MaxJsonLength = [int]::MaxValue
$JsonData.RecursionLimit = 100
function Read-Data([string]$File) {
    # PowerShell 5.1 PSCustomObject cannot represent empty or case-distinct JSON keys.
    return ,$JsonData.DeserializeObject([IO.File]::ReadAllText($File))
}
function Get-Hash([byte[]]$Bytes, [switch]$Base64) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash($Bytes) } finally { $sha.Dispose() }
    if ($Base64) { return [Convert]::ToBase64String($hash).TrimEnd('=') }
    return ([BitConverter]::ToString($hash)).Replace('-','').ToLowerInvariant()
}
function Write-Atomic([string]$File, [byte[]]$Bytes) {
    $parent = Split-Path -Parent $File
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $tmp = Join-Path $parent ('.agent-zh-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllBytes($tmp,$Bytes)
        if ([IO.File]::Exists($File)) { [IO.File]::Replace($tmp,$File,[System.Management.Automation.Language.NullString]::Value) }
        else { [IO.File]::Move($tmp,$File) }
    } finally { if ([IO.File]::Exists($tmp)) { [IO.File]::Delete($tmp) } }
}
function Commit-Writes($Writes) {
    $old = @{}; $done = New-Object 'Collections.Generic.List[string]'
    foreach ($file in $Writes.Keys) { $old[$file] = if ([IO.File]::Exists($file)) { [IO.File]::ReadAllBytes($file) } else { $null } }
    try {
        foreach ($file in $Writes.Keys) {
            if ($null -eq $Writes[$file]) { if ([IO.File]::Exists($file)) { [IO.File]::Delete($file) } }
            else { Write-Atomic $file $Writes[$file] }
            $done.Add($file)
        }
    } catch {
        $originalError = $_; $rollbackErrors = @()
        for ($i=$done.Count-1; $i -ge 0; $i--) {
            $file=$done[$i]
            try {
                if ($null -eq $old[$file]) { if ([IO.File]::Exists($file)) { [IO.File]::Delete($file) } }
                else { Write-Atomic $file $old[$file] }
            } catch { $rollbackErrors += $_.Exception.Message }
        }
        if ($rollbackErrors.Count) { throw ('回滚不完整：' + ($rollbackErrors -join '; ')) }
        throw $originalError
    }
}
function Get-Roots([string]$Value) {
    if (Test-Path -LiteralPath $Value -PathType Leaf) { $Value=Split-Path -Parent $Value }
    if (-not (Test-Path -LiteralPath $Value -PathType Container)) { return }
    foreach ($manifest in Get-ChildItem -LiteralPath $Value -Filter '*.VisualElementsManifest.xml') {
        if ($manifest.Name.StartsWith('old_')) { continue }
        $text=[IO.File]::ReadAllText($manifest.FullName)
        foreach ($match in [regex]::Matches($text,'"([a-fA-F0-9]{8,40})[\\/]resources[\\/]app[\\/]')) {
            Join-Path $Value ($match.Groups[1].Value + '/resources/app')
        }
    }
    $Value
    Join-Path $Value 'resources/app'
}
function Find-Installations {
    $seen=@{}
    foreach ($property in $Profiles.PSObject.Properties) {
        $id=$property.Name; $profile=$property.Value
        if ($App -and $id -ne $App) { continue }
        $values=@()
        if ($Path) { $values+= $Path }
        else {
            foreach ($envName in @($id.ToUpperInvariant()+'_PATH',$id.ToUpperInvariant()+'_APP')) {
                $custom=[Environment]::GetEnvironmentVariable($envName)
                if ($custom) { $values+=$custom }
            }
            foreach ($base in @((Join-Path $env:LOCALAPPDATA 'Programs'),$env:ProgramFiles,${env:ProgramFiles(x86)})) {
                if ($base) { foreach ($name in $profile.windowsDirs) { $values+=Join-Path $base $name } }
            }
            foreach ($reg in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
                'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
                'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
                Get-ChildItem -LiteralPath $reg -ErrorAction SilentlyContinue | ForEach-Object {
                    $item=Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction SilentlyContinue
                    if ($item.DisplayName -and $item.DisplayName.IndexOf($profile.name,[StringComparison]::OrdinalIgnoreCase) -ge 0) {
                        if ($item.InstallLocation) { $values+=$item.InstallLocation }
                        elseif ($item.DisplayIcon) { $values+=($item.DisplayIcon -replace ',\d+$','').Trim('"') }
                    }
                }
            }
        }
        foreach ($value in $values) { foreach ($root in Get-Roots $value) {
            if (-not (Test-Path -LiteralPath (Join-Path $root 'product.json'))) { continue }
            try { $product=Read-Json (Join-Path $root 'product.json'); $package=Read-Json (Join-Path $root 'package.json') } catch { continue }
            if ($profile.applicationNames -notcontains $product.applicationName) { continue }
            $entries=@($EntryPaths | Where-Object { Test-Path -LiteralPath (Join-Path $root $_) })
            if (-not $entries.Count) { continue }
            $root=[IO.Path]::GetFullPath($root)
            if ($seen.ContainsKey($root)) { continue }; $seen[$root]=$true
            $install=Split-Path -Parent (Split-Path -Parent $root)
            if ((Split-Path -Leaf $install) -match '^[a-fA-F0-9]{8,40}$') {
                $parent=Split-Path -Parent $install
                foreach ($exe in $profile.executables) { if (Test-Path -LiteralPath (Join-Path $parent $exe) -PathType Leaf) { $install=$parent; break } }
            }
            $userData=Join-Path $env:APPDATA $profile.userData
            $extensions=Join-Path $env:USERPROFILE ($profile.dataFolder+'/extensions')
            $argvFile=Join-Path $env:USERPROFILE ($profile.dataFolder+'/argv.json')
            if (Test-Path -LiteralPath (Join-Path $install 'data') -PathType Container) {
                $userData=Join-Path $install 'data/user-data'; $extensions=Join-Path $install 'data/extensions'; $argvFile=Join-Path $userData 'argv.json'
            }
            [PSCustomObject]@{Id=$id; Profile=$profile; Root=$root; Install=$install; Entries=$entries;
                Version=$package.version; UserData=$userData; Extensions=$extensions; Argv=$argvFile}
        } }
    }
}
function Remove-ZhTag([string]$Text) {
    foreach ($t in @($Tag,$OldCursorTag)) {
        foreach ($prefix in @("`r`n`t","`n`t","`r`n","`n",'')) { $Text=$Text.Replace($prefix+$t,'') }
    }
    return $Text
}
function Inject-Html([string]$Text) {
    $clean=Remove-ZhTag $Text
    $match=[regex]::Match($clean,'<script\b[^>]*\bsrc=["'']\./(?:workbench|sessions)\.js["''][^>]*>\s*</script>')
    if (-not $match.Success) { throw '未识别该版本的启动脚本，未修改文件。' }
    $nl=if ($clean.Contains("`r`n")) { "`r`n" } else { "`n" }
    return $clean.Insert($match.Index+$match.Length,$nl+"`t"+$Tag)
}
function Update-Checksums([string]$Product, $Htmls) {
    $obj=$Product.TrimStart([char]0xFEFF) | ConvertFrom-Json
    foreach ($rel in $Htmls.Keys) {
        $key=$rel.Substring(4)
        if (-not $obj.checksums -or -not $obj.checksums.PSObject.Properties[$key]) { continue }
        $sum=Get-Hash $Htmls[$rel] -Base64
        $pattern='("'+[regex]::Escape($key)+'"\s*:\s*")[^"]*(")'
        $replacement='$1'+$sum+'$2'
        # MatchEvaluator keeps base64 values beginning with digits out of group syntax.
        $replace={param($m) $m.Groups[1].Value+$sum+$m.Groups[2].Value}
        $Product=[regex]::Replace($Product,$pattern,$replace)
    }
    return $Product
}
function Locale-Bytes([string]$File) {
    if (-not [IO.File]::Exists($File)) { return ,$Utf8.GetBytes("{`n  `"locale`": `"zh-cn`"`n}`n") }
    $text=[IO.File]::ReadAllText($File)
    $pattern='"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/|[{}\[\]:,]|\s+|[^\s{}\[\]:,/]+|.'
    $tokens=@([regex]::Matches($text,$pattern) | Where-Object { $_.Value.Trim() -and -not $_.Value.StartsWith('//') -and -not $_.Value.StartsWith('/*') })
    $plain=''; for ($i=0;$i -lt $tokens.Count;$i++) {
        if ($tokens[$i].Value -eq ',' -and $i+1 -lt $tokens.Count -and $tokens[$i+1].Value -in @('}',']')) { continue }
        $plain+=$tokens[$i].Value
    }
    $parsed=$plain | ConvertFrom-Json
    if (-not ($parsed -is [PSCustomObject])) { throw '语言配置必须是 JSON 对象。' }
    $depth=0; $hits=@()
    for ($i=0;$i -lt $tokens.Count;$i++) {
        $v=$tokens[$i].Value
        if ($v -in @('{','[')) { $depth++ }
        elseif ($v -in @('}',']')) { $depth-- }
        elseif ($depth -eq 1 -and $v -eq '"locale"' -and $i+2 -lt $tokens.Count -and $tokens[$i+1].Value -eq ':') { $hits+=,$tokens[$i+2] }
    }
    if ($hits.Count -gt 1) { throw '配置中存在重复 locale 字段。' }
    if ($hits.Count) {
        $m=$hits[0]; if (-not $m.Value.StartsWith('"')) { throw 'locale 必须是字符串。' }
        $text=$text.Remove($m.Index,$m.Length).Insert($m.Index,'"zh-cn"')
    } else {
        $comma=if ($tokens.Count -gt 2) { ',' } else { '' }
        $text=$text.Insert($tokens[0].Index+1,"`n  `"locale`": `"zh-cn`""+$comma)
    }
    return ,$Utf8.GetBytes($text)
}
function Get-Cli($Target) {
    foreach ($base in @((Join-Path $Target.Install 'bin'),(Join-Path $Target.Root 'bin'))) {
        foreach ($name in $Target.Profile.cliNames) {
            $cli=Join-Path $base ($name+'.cmd')
            if (Test-Path -LiteralPath $cli -PathType Leaf) { return $cli }
        }
    }
}
function Find-Pack($Target) {
    $matches=@()
    if (-not (Test-Path -LiteralPath $Target.Extensions)) { return $null }
    foreach ($folder in Get-ChildItem -LiteralPath $Target.Extensions -Directory -Filter 'ms-ceintl.vscode-language-pack-zh-hans-*') {
        try {
            $package=Read-Json (Join-Path $folder.FullName 'package.json')
            foreach ($loc in $package.contributes.localizations) {
                if ($loc.languageId -ne 'zh-cn') { continue }
                $paths=@{}
                foreach ($item in $loc.translations) {
                    $full=[IO.Path]::GetFullPath((Join-Path $folder.FullName $item.path))
                    if (-not $full.StartsWith($folder.FullName+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw '语言包路径越界。' }
                    if (Test-Path -LiteralPath $full -PathType Leaf) { $paths[$item.id]=$full }
                }
                if ($paths.ContainsKey('vscode')) {
                    $entry=@{extensions=@(@{version=$package.version;extensionIdentifier=@{id='ms-ceintl.vscode-language-pack-zh-hans'}}); translations=$paths;label='中文(简体)'}
                    $matches+= [PSCustomObject]@{Version=[version]$package.version; Entry=$entry; Translation=(Read-Data $paths['vscode'])}
                }
            }
        } catch { Write-Warning ('跳过无效语言包：'+$folder.Name+'；'+$_.Exception.Message) }
    }
    return $matches | Sort-Object Version -Descending | Select-Object -First 1
}
function Add-LanguageWrites($Target,$Writes) {
    foreach ($file in @($Target.Argv,(Join-Path $Target.UserData 'User/locale.json'))) { $Writes[$file]=Locale-Bytes $file }
    $pack=Find-Pack $Target
    if (-not $pack) { Write-Warning '未发现简体中文语言包，基础菜单可能仍为英文。'; return }
    $dictionary=New-Object 'Collections.Generic.Dictionary[string,string]'
    foreach ($name in @('zh-CN.json','common.json')) {
        $data=Read-Data (Join-Path $Here ('locales/'+$name))
        foreach ($section in @('phrase','short')) { foreach ($key in $data[$section].Keys) { $dictionary[$key]=$data[$section][$key] } }
    }
    $extra=Join-Path $Here ('locales/'+$Target.Id+'-nls.json')
    if (Test-Path -LiteralPath $extra) { $data=Read-Data $extra; foreach ($key in $data.Keys) { $dictionary[$key]=$data[$key] } }
    $metadataFile=Join-Path $Target.Root 'out/nls.metadata.json'; $count=0
    if (Test-Path -LiteralPath $metadataFile) {
        $metadata=Read-Data $metadataFile
        $contents=$pack.Translation['contents']
        $byText=New-Object 'Collections.Generic.Dictionary[string,object]'
        foreach ($name in $metadata['keys'].Keys) {
            $keys=@($metadata['keys'][$name]); $messages=@($metadata['messages'][$name])
            if (-not $contents.ContainsKey($name)) { continue }
            for ($i=0;$i -lt $keys.Count;$i++) {
                $key=if ($keys[$i] -is [string]) { $keys[$i] } else { $keys[$i]['key'] }
                if ($contents[$name].ContainsKey($key)) {
                    $english=$messages[$i]
                    if (-not $byText.ContainsKey($english)) { $byText[$english]=New-Object 'Collections.Generic.HashSet[string]' }
                    $byText[$english].Add([string]$contents[$name][$key]) | Out-Null
                }
            }
        }
        foreach ($english in $byText.Keys) {
            if ($byText[$english].Count -eq 1 -and -not $dictionary.ContainsKey($english)) { $dictionary[$english]=@($byText[$english])[0] }
        }
        foreach ($name in $metadata['keys'].Keys) {
            if (-not $contents.ContainsKey($name)) { $contents[$name]=New-Object 'Collections.Generic.Dictionary[string,object]' }
            $destination=$contents[$name]
            $keys=@($metadata['keys'][$name]); $messages=@($metadata['messages'][$name])
            for ($i=0;$i -lt $keys.Count;$i++) {
                $key=if ($keys[$i] -is [string]) { $keys[$i] } else { $keys[$i]['key'] }
                $english=$messages[$i]
                if (-not $destination.ContainsKey($key) -and $dictionary.ContainsKey($english)) {
                    $destination[$key]=$dictionary[$english]; $count++
                }
            }
        }
    }
    $merged=Join-Path $Target.UserData 'AgentZh/main.i18n.json'
    $Writes[$merged]=$Utf8.GetBytes(($pack.Translation | ConvertTo-Json -Depth 100 -Compress))
    $pack.Entry.translations['vscode']=$merged
    $pack.Entry['hash']=(Get-Hash $Writes[$merged]).Substring(0,32)
    $indexFile=Join-Path $Target.UserData 'languagepacks.json'
    $index=if (Test-Path -LiteralPath $indexFile) { Read-Json $indexFile } else { [PSCustomObject]@{} }
    $index | Add-Member -NotePropertyName 'zh-cn' -NotePropertyValue $pack.Entry -Force
    $Writes[$indexFile]=$Utf8.GetBytes(($index | ConvertTo-Json -Depth 100 -Compress))
    Write-Host "语言资源已索引，补充 $count 条文案。"
}
function Save-Backup($Target,$Writes) {
    $id=(Get-Hash ($Utf8.GetBytes($Target.Root))).Substring(0,12)
    $safeVersion=$Target.Version -replace '[^0-9A-Za-z._-]','_'
    $folder=Join-Path $BackupBase ($Target.Id+'/'+$id+'/'+$safeVersion+'/'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff'))
    $files=@(); $i=0
    foreach ($file in $Writes.Keys) {
        $item=@{path=$file;existed=[IO.File]::Exists($file)}
        if ($item.existed) {
            $bytes=[IO.File]::ReadAllBytes($file); $name=[string]$i+'-'+[IO.Path]::GetFileName($file)+'.orig'
            Write-Atomic (Join-Path $folder $name) $bytes
            $item.backup=$name; $item.sha256=Get-Hash $bytes
        }
        $files+=,$item; $i++
    }
    $manifest=@{installerVersion='2.0.0';app=$Target.Id;appVersion=$Target.Version;appPath=$Target.Root;files=$files}
    Write-Atomic (Join-Path $folder 'manifest.json') ($Utf8.GetBytes(($manifest | ConvertTo-Json -Depth 20)))
    Write-Host "备份：$folder"
}
function Get-TargetProcesses($Target,[string]$Name) {
    $expected=Join-Path $Target.Install ($Name+'.exe')
    foreach ($process in Get-Process -Name $Name -ErrorAction SilentlyContinue) {
        if (-not $process.Path) { throw '无法核对同名进程的路径，请手动退出该软件后重试。' }
        if ($process.Path.Equals($expected,[StringComparison]::OrdinalIgnoreCase)) { $process }
    }
}

try {
    if ($All -and ($App -or $Path)) { throw '-All 不能与 -App 或 -Path 同时使用。' }
    $targets=@(Find-Installations)
    if (-not $targets.Count) { throw '没有检测到受支持的软件。可用 -App 和 -Path 指定。' }
    if ($Cmd -in @('list','status')) {
        Write-Host 'Agent 汉化 2.0.0（PowerShell）'
        foreach ($target in $targets) {
            Write-Host "$($target.Id) $($target.Profile.name) $($target.Version) $($target.Root)"
            foreach ($rel in $target.Entries) {
                $entry=Join-Path $target.Root $rel; $raw=[IO.File]::ReadAllText($entry)
                $installed=$raw.Contains($Tag) -and (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $entry) 'agent-zh.js'))
                Write-Host "  $rel 已安装=$(if ($installed) { '是' } else { '否' })"
            }
        }; exit 0
    }
    if (-not $All -and $targets.Count -gt 1) {
        for ($i=0;$i -lt $targets.Count;$i++) { Write-Host "$($i+1). $($targets[$i].Profile.name) $($targets[$i].Version)" }
        $answer=Read-Host '选择软件编号（0 退出）'
        if ($answer -eq '0') { exit 0 }
        $number=0
        if (-not [int]::TryParse($answer,[ref]$number) -or $number -lt 1 -or $number -gt $targets.Count) { throw '无效的编号。' }
        $targets=@($targets[$number-1])
    }
    foreach ($target in $targets) {
        $processName=($target.Profile.executables | Where-Object { $_.EndsWith('.exe') } | Select-Object -First 1) -replace '\.exe$',''
        $processes=@(Get-TargetProcesses $target $processName)
        if ($processes.Count) {
            if (-not $Kill) { throw "请退出 $($target.Profile.name)，或加上 -Kill。" }
            $processes | Stop-Process -Force
            for ($i=0;$i -lt 40;$i++) { if (-not (Get-TargetProcesses $target $processName)) { break }; Start-Sleep -Milliseconds 250 }
            if (Get-TargetProcesses $target $processName) { throw '软件仍在运行，请手动退出。' }
        }
        $writes=[ordered]@{}; $htmls=@{}
        foreach ($rel in $target.Entries) {
            $entry=Join-Path $target.Root $rel; $raw=[IO.File]::ReadAllText($entry); $parent=Split-Path -Parent $entry
            if ($Cmd -eq 'apply') {
                $htmls[$rel]=$Utf8.GetBytes((Inject-Html $raw))
                $writes[$entry]=$htmls[$rel]
                $writes[(Join-Path $parent 'agent-zh.js')]=[IO.File]::ReadAllBytes((Join-Path $Here 'agent-zh.js'))
                if ($raw.Contains($OldCursorTag)) { $writes[(Join-Path $parent 'cursor-zh.js')]=$null }
            } elseif ($raw.Contains($Tag) -or $raw.Contains($OldCursorTag)) {
                $htmls[$rel]=$Utf8.GetBytes((Remove-ZhTag $raw)); $writes[$entry]=$htmls[$rel]
                if ($raw.Contains($Tag)) { $writes[(Join-Path $parent 'agent-zh.js')]=$null }
                if ($raw.Contains($OldCursorTag)) { $writes[(Join-Path $parent 'cursor-zh.js')]=$null }
            }
        }
        if ($htmls.Count) {
            $product=Join-Path $target.Root 'product.json'
            $writes[$product]=$Utf8.GetBytes((Update-Checksums ([IO.File]::ReadAllText($product)) $htmls))
        }
        if ($Cmd -eq 'apply') {
            if (-not $NoLangpack) {
                $cli=Get-Cli $target
                if ($cli) { & $cli --install-extension MS-CEINTL.vscode-language-pack-zh-hans; if ($LASTEXITCODE) { Write-Warning '语言包下载失败。' } }
            }
            Add-LanguageWrites $target $writes
            Save-Backup $target $writes
        } else {
            $indexFile=Join-Path $target.UserData 'languagepacks.json'; $merged=Join-Path $target.UserData 'AgentZh/main.i18n.json'
            if (Test-Path -LiteralPath $indexFile) {
                $index=Read-Json $indexFile
                if ($index.'zh-cn'.translations.vscode -eq $merged) {
                    $pack=Find-Pack $target
                    if ($pack) {
                        $pack.Entry.hash=(Get-Hash ($Utf8.GetBytes(($pack.Entry | ConvertTo-Json -Depth 100)))).Substring(0,32)
                        $index | Add-Member -NotePropertyName 'zh-cn' -NotePropertyValue $pack.Entry -Force
                    } else { $index.PSObject.Properties.Remove('zh-cn') }
                    $writes[$indexFile]=$Utf8.GetBytes(($index | ConvertTo-Json -Depth 100 -Compress)); $writes[$merged]=$null
                }
            }
        }
        Commit-Writes $writes
        $cmdLabel = switch ($Cmd) {
            'apply'  { '应用汉化' }
            'revert' { '移除汉化' }
            'status' { '查看状态' }
            'list'   { '列出软件' }
        }
        Write-Host "$($target.Profile.name)：$cmdLabel 完成。"
        if ($Restart) {
            $exe=Join-Path $target.Install ($processName+'.exe')
            if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe -WindowStyle Normal }
        }
    }
} catch { Write-Error $_; exit 1 }
