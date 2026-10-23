# Genera kernel/src/font8x16.h: fuente monoespaciada 8x16 para Latin-1 32..255,
# rasterizada con .NET (Consolas) sin dependencias externas.
Add-Type -AssemblyName System.Drawing
$font = New-Object System.Drawing.Font("Consolas", 12, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$out = New-Object System.Collections.Generic.List[string]
$out.Add("/* Generado por scripts/gen_font.ps1 (Consolas 8x16, Latin-1 32..255). No editar. */")
$out.Add("#ifndef ERLANOS_FONT8X16_H")
$out.Add("#define ERLANOS_FONT8X16_H")
$out.Add("#include ""erlan_rt.h""")
$out.Add("static const u8 font8x16[224][16] = {")
for ($ch = 32; $ch -le 255; $ch++) {
    $bmp = New-Object System.Drawing.Bitmap(8, 16)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::SingleBitPerPixelGridFit
    $g.Clear([System.Drawing.Color]::Black)
    $g.DrawString([string][char]$ch, $font, [System.Drawing.Brushes]::White, -1, 0)
    $bytes = @()
    for ($y = 0; $y -lt 16; $y++) {
        $b = 0
        for ($x = 0; $x -lt 8; $x++) {
            if ($bmp.GetPixel($x, $y).R -gt 128) { $b = $b -bor (0x80 -shr $x) }
        }
        $bytes += ("0x{0:x2}" -f $b)
    }
    $g.Dispose(); $bmp.Dispose()
    $label = "U+{0:X4}" -f $ch
    if ($ch -eq 92) { $label = "backslash" }
    if ($ch -eq 42) { $label = "asterisk" }
    if ($ch -eq 47) { $label = "slash" }
    $out.Add("    { " + ($bytes -join ", ") + " }, /* $ch '$label' */")
}
$out.Add("};")
$out.Add("#endif")
$path = Join-Path $PSScriptRoot "..\kernel\src\font8x16.h"
[System.IO.File]::WriteAllLines($path, $out, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "escrito $path"
