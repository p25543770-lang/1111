# build-full-zip.ps1 — собирает ПОЛНЫЙ zip для импорта данных Mindustry прямо из папки игры.
#
# Зачем: встроенный экспорт игры (Настройки -> Данные -> Экспорт) у тебя падает
# на схемах с русскими именами, поэтому архив получается неполным/отсутствует.
# Этот скрипт пакует папку данных на уровне файловой системы — ему всё равно,
# какие там имена файлов, поэтому в архив попадёт ВСЁ: сохранения, схемы, моды,
# карты, превью, настройки.
#
# Как запустить: двойной клик по build-full-zip.bat (Windows).
# Или вручную:  powershell -ExecutionPolicy Bypass -File build-full-zip.ps1
#
# Полезные параметры:
#   -DataDir "D:\Steam\steamapps\common\Mindustry\saves"   указать папку вручную
#   -Out "D:\Mindustry-Data.zip"                           куда положить архив
#   -FixSchematics                                         переименовать схемы в латиницу
#                                                          (починит и экспорт, и загрузку схем)
#
# Полученный zip можно загрузить в игре: Настройки -> Данные -> Импорт.

param(
    [string]$DataDir,
    [string]$Out,
    [switch]$FixSchematics
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Write-Head($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "  [ok] $text"   -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [!]  $text"   -ForegroundColor Yellow }
function Write-Err($text)  { Write-Host "  [x]  $text"   -ForegroundColor Red }

# ---------- 1. ищем папку данных Mindustry ----------
function Get-SteamLibraryRoots {
    $roots = New-Object System.Collections.Generic.List[string]
    $steam = $null
    foreach ($key in @("HKCU:\Software\Valve\Steam", "HKLM:\SOFTWARE\WOW6432Node\Valve\Steam")) {
        try {
            $p = (Get-ItemProperty -Path $key -ErrorAction Stop).SteamPath
            if ($p) { $steam = $p; break }
        } catch {}
    }
    if ($steam) {
        $steam = $steam -replace "/", "\"
        $roots.Add($steam)
        $vdf = Join-Path $steam "steamapps\libraryfolders.vdf"
        if (Test-Path $vdf) {
            foreach ($m in [regex]::Matches((Get-Content $vdf -Raw), '"path"\s+"([^"]+)"')) {
                $roots.Add(($m.Groups[1].Value -replace "\\\\", "\"))
            }
        }
    }
    foreach ($guess in @(
        "C:\Program Files (x86)\Steam",
        "C:\Program Files\Steam",
        "D:\Steam", "D:\SteamLibrary", "D:\Games\Steam",
        "E:\Steam", "E:\SteamLibrary", "E:\Games\Steam"
    )) {
        if (Test-Path $guess) { $roots.Add($guess) }
    }
    return $roots | Select-Object -Unique
}

function Test-DataDir($dir) {
    if (-not $dir -or -not (Test-Path $dir)) { return $false }
    $p = (Resolve-Path $dir).Path
    # явные признаки папки данных
    foreach ($probe in @("settings.bin", "server_list.json", "last_log.txt")) {
        if (Test-Path (Join-Path $p $probe)) { return $true }
    }
    # не путать с папкой установки игры (Steam: ...\common\Mindustry, в ней лежит подпапка saves)
    foreach ($bad in @("Mindustry.jar", "Mindustry.exe", "jre", "assets")) {
        if (Test-Path (Join-Path $p $bad)) { return $false }
    }
    foreach ($probe in @("saves", "schematics", "mods", "maps", "previews", "assetCache")) {
        if (Test-Path (Join-Path $p $probe)) { return $true }
    }
    return $false
}

function Find-DataDir {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($DataDir) { $candidates.Add($DataDir) }

    # обычная (не Steam) установка
    $candidates.Add((Join-Path $env:APPDATA "Mindustry"))

    # Steam: <library>\steamapps\common\Mindustry\saves
    foreach ($root in Get-SteamLibraryRoots) {
        $candidates.Add((Join-Path $root "steamapps\common\Mindustry\saves"))
        $candidates.Add((Join-Path $root "steamapps\common\Mindustry"))
    }

    # portable-версия рядом со скриптом
    $candidates.Add((Join-Path $PSScriptRoot "saves"))
    $candidates.Add($PSScriptRoot)

    foreach ($c in $candidates) {
        if (Test-DataDir $c) { return (Resolve-Path $c).Path }
    }
    return $null
}

Write-Host "Сборка полного zip для импорта данных Mindustry" -ForegroundColor White

Write-Head "Ищу папку данных Mindustry"
$data = Find-DataDir
if (-not $data) {
    Write-Err "Не нашёл папку данных Mindustry."
    Write-Host "  Укажи её вручную, например:"
    Write-Host '  powershell -ExecutionPolicy Bypass -File build-full-zip.ps1 -DataDir "D:\Steam\steamapps\common\Mindustry\saves"'
    Write-Host "  Ориентир: в папке данных лежат settings.bin, server_list.json и папки saves, maps, mods, schematics."
    exit 1
}
Write-Ok "папка данных: $data"

# ---------- 2. что нашли внутри ----------
Write-Head "Содержимое папки данных"
$dirs = @("saves", "schematics", "mods", "maps", "previews", "assetCache", "screenshots", "be_builds")
$found = @{}
foreach ($d in $dirs) {
    $p = Join-Path $data $d
    $found[$d] = if (Test-Path $p) { @(Get-ChildItem -Path $p -File -Recurse -ErrorAction SilentlyContinue).Count } else { -1 }
    if ($found[$d] -ge 0) { Write-Ok ("{0,-12} {1} файл(ов)" -f $d, $found[$d]) }
    else { Write-Warn ("{0,-12} нет" -f $d) }
}
foreach ($f in @("settings.bin", "settings_backup.bin", "server_list.json", "last_log.txt")) {
    if (Test-Path (Join-Path $data $f)) { Write-Ok $f } else { Write-Warn "$f нет" }
}
if (-not (Test-Path (Join-Path $data "settings.bin"))) {
    Write-Warn "settings.bin отсутствует — игровой импорт такой архив не примет (скажет 'Not valid save data')."
}

# ---------- 3. чиним имена схем (если попросили) ----------
$map = @{
    'а'='a';'б'='b';'в'='v';'г'='g';'д'='d';'е'='e';'ё'='e';'ж'='zh';'з'='z';'и'='i';'й'='y';'к'='k'
    'л'='l';'м'='m';'н'='n';'о'='o';'п'='p';'р'='r';'с'='s';'т'='t';'у'='u';'ф'='f';'х'='h';'ц'='c'
    'ч'='ch';'ш'='sh';'щ'='sch';'ъ'='';'ы'='y';'ь'='';'э'='e';'ю'='yu';'я'='ya'
    'А'='A';'Б'='B';'В'='V';'Г'='G';'Д'='D';'Е'='E';'Ё'='E';'Ж'='Zh';'З'='Z';'И'='I';'Й'='Y';'К'='K'
    'Л'='L';'М'='M';'Н'='N';'О'='O';'П'='P';'Р'='R';'С'='S';'Т'='T';'У'='U';'Ф'='F';'Х'='H';'Ц'='C'
    'Ч'='Ch';'Ш'='Sh';'Щ'='Sch';'Ъ'='';'Ы'='Y';'Ь'='';'Э'='E';'Ю'='Yu';'Я'='Ya';'№'='N'
}

function ConvertTo-AsciiName($name) {
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $name.ToCharArray()) {
        $s = [string]$ch
        if ($map.ContainsKey($s)) { [void]$sb.Append($map[$s]) }
        elseif ([int][char]$ch -lt 128) { [void]$sb.Append($ch) }
        else { [void]$sb.Append("_") }
    }
    $res = $sb.ToString()
    $res = $res -replace '\s+', ' '
    $res = $res -replace '[\[\]<>:"/\\|?*]', '_'
    $res = $res.Trim(' ', '.', '_')
    if ($res.Length -gt 60) { $res = $res.Substring(0, 60) }
    if ([string]::IsNullOrWhiteSpace($res)) { $res = "schem" }
    return $res
}

$schematics = Join-Path $data "schematics"
if ($FixSchematics -and (Test-Path $schematics)) {
    Write-Head "Переименовываю схемы в латиницу (это чинит экспорт и загрузку схем)"
    $used = @{}
    foreach ($f in Get-ChildItem -Path $schematics -File -Filter *.msch) {
        $base = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
        $new  = ConvertTo-AsciiName $base
        $cand = $new; $i = 1
        while ($used.ContainsKey($cand.ToLower()) -or ($cand -ne $base -and (Test-Path (Join-Path $schematics ($cand + ".msch"))))) {
            $cand = "$new-$i"; $i++
        }
        $used[$cand.ToLower()] = $true
        if ($cand -ne $base) {
            try {
                Rename-Item -Path $f.FullName -NewName ($cand + ".msch")
                Write-Ok "$($f.Name)  ->  $cand.msch"
            } catch {
                Write-Warn "не смог переименовать $($f.Name): $($_.Exception.Message)"
            }
        }
    }
} elseif (Test-Path $schematics) {
    $bad = @(Get-ChildItem -Path $schematics -File -Filter *.msch | Where-Object { $_.Name -match '[^\x20-\x7E]' })
    if ($bad.Count -gt 0) {
        Write-Warn "схем с не-латинскими именами: $($bad.Count) — из-за них встроенный экспорт игры падает."
        Write-Warn "  Запусти с ключом -FixSchematics, чтобы переименовать их автоматически."
    }
}

# ---------- 4. собираем архив ----------
if (-not $Out) {
    $stamp = Get-Date -Format "yyyy-MM-dd"
    $Out = Join-Path ([Environment]::GetFolderPath("Desktop")) "Mindustry-Data-$stamp.zip"
}
if (Test-Path $Out) { Remove-Item $Out -Force }

$skipNames = @("steam_autocloud.vdf", "Thumbs.db", "desktop.ini", ".DS_Store", "last_log.txt")

Write-Head "Пакуем архив"
Write-Ok "файл: $Out"

$archive = [System.IO.Compression.ZipFile]::Open($Out, [System.IO.Compression.ZipArchiveMode]::Create)
$count = 0
try {
    $all = Get-ChildItem -Path $data -Recurse -File -ErrorAction SilentlyContinue |
           Where-Object { $skipNames -notcontains $_.Name }
    foreach ($f in $all) {
        $rel = $f.FullName.Substring($data.Length).TrimStart("\", "/").Replace("\", "/")
        $entry = $archive.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
        $entry.LastWriteTime = $f.LastWriteTime
        $in  = [System.IO.File]::OpenRead($f.FullName)
        $out = $entry.Open()
        try { $in.CopyTo($out) } finally { $out.Close(); $in.Close() }
        $count++
    }
} finally {
    $archive.Dispose()
}

# папка saves/ в архиве обязательна, даже если пустая
if (-not (Test-Path (Join-Path $data "saves"))) {
    Write-Warn "В папке данных нет подпапки saves — распакуй архив в папку данных вручную."
}

$size = [math]::Round((Get-Item $Out).Length / 1MB, 2)
Write-Head "Готово"
Write-Ok "файлов в архиве: $count"
Write-Ok "размер: $size МБ"
Write-Host ""
Write-Host "Как загрузить: открой Mindustry -> Настройки -> Данные -> Импорт -> выбери этот zip." -ForegroundColor White
Write-Host "(импорт заменит текущие сохранения и настройки игры)" -ForegroundColor DarkGray
