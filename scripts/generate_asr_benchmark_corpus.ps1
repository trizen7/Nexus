param(
    [string]$OutputDirectory = ".nexus-test/asr-benchmark/corpus"
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../$OutputDirectory"))
[System.IO.Directory]::CreateDirectory($root) | Out-Null
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($null -eq $ffmpeg) {
    $python = Join-Path $PSScriptRoot "../.nexus-test/asr-benchmark/venv/Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $python)) { throw "ffmpeg is unavailable" }
    $ffmpegPath = & $python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
} else {
    $ffmpegPath = $ffmpeg.Source
}

Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice.SelectVoice("Microsoft Huihui Desktop")
$voice.Rate = 1
$voice.Volume = 100

function Decode-Utf8([string]$Base64) {
    return [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($Base64))
}

$sentences = @(
    @{ Name = "phone-normal"; Text = Decode-Utf8 "5L2g5aW977yM5oiR546w5Zyo5q2j5Zyo5rWL6K+VIE5leHVzIOeahOWunuaXtuivremfs+ivhuWIq+OAguS7iuWkqeWkqeawlOS4jemUme+8jOaIkeS7rOe7p+e7reiuqOiuuueUteivnemAmuivneeahOWHhuehrueOh+WSjOWTjeW6lOmAn+W6puOAgg==" },
    @{ Name = "barge-in-first-char"; Text = Decode-Utf8 "5LiN5a+577yM5LiN5piv6L+Z5qC355qE77yM6K+35YWI5YGc5q2i5pyX6K+777yM54S25ZCO5ZCs5oiR5oqK6L+Z5Y+l6K+d6K+05a6M44CC" },
    @{ Name = "punctuation-semantics"; Text = Decode-Utf8 "5L2g56Gu5a6a5ZCX77yf5oiR5LiN5piv5Y+N5a+577yM5oiR5Y+q5piv5oOz6Zeu77ya5piO5aSp5LiL5Y2I5LiJ54K577yM6L+Y5piv5pma5LiK5LiD54K577yf" },
    @{ Name = "numbers-names"; Text = Decode-Utf8 "6K+35oqKIE5leHVzIOe9keWFs+WcsOWdgOaUueaIkOS4gOS5neS6jOeCueS4gOWFreWFq+eCueS4gOeCueS6jOWNge+8jOerr+WPo+WFq+WNg+mbtuWFq+WNgeOAgg==" }
)

$manifestSamples = @()
foreach ($item in $sentences) {
    $source = Join-Path $root "$($item.Name)-source.wav"
    $voice.SetOutputToWaveFile($source)
    $voice.Speak($item.Text)
    $voice.SetOutputToNull()

    $variants = @(
        @{ Suffix = "clean"; Filter = "aresample=16000" },
        @{ Suffix = "quiet"; Filter = "volume=0.12,aresample=16000" },
        @{ Suffix = "immediate"; Filter = "atrim=start=0.12,asetpts=PTS-STARTPTS,aresample=16000" }
    )
    foreach ($variant in $variants) {
        $name = "$($item.Name)-$($variant.Suffix)"
        $target = Join-Path $root "$name.wav"
        & $ffmpegPath -hide_banner -loglevel error -y -i $source -af $variant.Filter -ac 1 -ar 16000 -sample_fmt s16 $target
        if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed for $name" }
        $manifestSamples += @{ name = $name; audio = "$name.wav"; reference = $item.Text }
    }
    $name = "$($item.Name)-noisy"
    $target = Join-Path $root "$name.wav"
    & $ffmpegPath -hide_banner -loglevel error -y -i $source -f lavfi -i "anoisesrc=color=pink:sample_rate=16000:amplitude=0.04" -filter_complex "[0:a]volume=0.35,aresample=16000[speech];[speech][1:a]amix=inputs=2:duration=first:weights='1 1'" -ac 1 -ar 16000 -sample_fmt s16 $target
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed for $name" }
    $manifestSamples += @{ name = $name; audio = "$name.wav"; reference = $item.Text }
    Remove-Item -LiteralPath $source
}
$voice.Dispose()

$manifest = @{ samples = $manifestSamples } | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText((Join-Path $root "manifest.json"), $manifest, [System.Text.UTF8Encoding]::new($false))
Write-Output (Join-Path $root "manifest.json")
