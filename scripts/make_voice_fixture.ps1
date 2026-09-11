# Windows offline system voice. Generates only synthetic content for ASR testing.
param([string]$OutputPath = "$PSScriptRoot/../test-results/voice-demo.wav")
Add-Type -AssemblyName System.Speech
$fixturePath = [System.IO.Path]::GetFullPath($OutputPath)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($fixturePath)) | Out-Null
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $spanish = $speaker.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name.StartsWith('es') } | Select-Object -First 1
    if ($spanish) { $speaker.SelectVoice($spanish.VoiceInfo.Name) }
    $speaker.SetOutputToWaveFile($fixturePath, $format)
    $speaker.Speak('Visité Hospital Demo Aurora. Tienen dos equipos de resonancia de ocho años.')
} finally {
    $speaker.Dispose()
}
Write-Output "Synthetic WAV generated for local ASR verification."
