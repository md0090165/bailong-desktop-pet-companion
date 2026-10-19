$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.DataWriter,Windows.Foundation,ContentType=WindowsRuntime]
function Await-Result($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetGenericArguments().Count -eq 1 } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}
try {
    $engines = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($_) })
    if ($engines.Count -eq 0) { throw 'No OCR language' }
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $texts = @()
    foreach ($encoded in $payload.images) {
        $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
        $writer = New-Object Windows.Storage.Streams.DataWriter($stream)
        try {
            $writer.WriteBytes([Convert]::FromBase64String($encoded))
            $null = Await-Result $writer.StoreAsync() ([uint32])
            $stream.Seek(0)
            $decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await-Result $decoder.GetSoftwareBitmapAsync() ([Windows.Graphics.Imaging.SoftwareBitmap])
            try {
                if ($bitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or $bitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) { throw 'Image too large' }
                $parts = @()
                foreach ($engine in $engines) {
                    $result = Await-Result $engine.RecognizeAsync($bitmap) ([Windows.Media.Ocr.OcrResult])
                    $parts += $result.Text
                }
                $texts += ($parts -join "`n")
            } finally { if ($bitmap) { $bitmap.Dispose() } }
        } finally { $writer.DetachStream() | Out-Null; $writer.Dispose(); $stream.Dispose() }
    }
    @{ ok=$true; texts=$texts; languages=@($engines | ForEach-Object { $_.RecognizerLanguage.LanguageTag }) } | ConvertTo-Json -Compress -Depth 4
} catch {
    # Never put image bytes or extracted text in errors/logs.
    '{"ok":false,"texts":[]}'
    exit 1
}
