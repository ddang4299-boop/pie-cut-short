# make_speech_sample.ps1 - build samples/speech_en_sample.mp4: real (synthetic) English speech
# from the built-in Windows TTS voice over a test-pattern video. Used to test transcription and
# moment proposals without a real recording. Windows only (System.Speech).
#   powershell -ExecutionPolicy Bypass -File scripts\make_speech_sample.ps1
param([string]$Out = "samples\speech_en_sample.mp4")

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null

$text = @"
Welcome back to the channel. Today I want to talk about the biggest mistake I made when I started making videos.

For two years I uploaded every single week, and my channel barely grew. I thought the problem was my camera. So I spent two thousand dollars on new gear. Nothing changed. Not one extra subscriber.

Then a friend watched my videos and said something that hurt. She said, nobody knows what this video is about until minute three. And she was right. I was spending the first three minutes saying hello, talking about my week, and asking people to subscribe.

So here is the tip. Put the most interesting sentence of your video in the first five seconds. Not the intro. Not the logo. The payoff, or at least the question that leads to it.

I tried it on my next video. Same camera, same room, same face. That video got ten times more views than anything I had made before. Ten times. In one week.

Here is a surprising fact. Most viewers decide whether to keep watching in less than three seconds. Three seconds is shorter than it takes to say good morning, how are you today.

Now, a funny story. The first time I tried a strong hook, I was so nervous that I said the hook, and then I said hello everyone, welcome to my channel, right after it. My editor laughed for ten minutes. We kept that clip as a blooper.

The second lesson is about endings. If your video ends without a clear point, people forget it. End with one sentence they can repeat to a friend. For example: your first five seconds are worth more than your camera.

Let me give you a quick checklist. One, start with the payoff. Two, cut every sentence that does not add something new. Three, end on a line people will remember.

That is it for today. If this helped you, try it on your next video and tell me what happened. See you next time.
"@

Add-Type -AssemblyName System.Speech
$wav = Join-Path $env:TEMP ("speech_sample_{0}.wav" -f $PID)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = 0
$synth.SetOutputToWaveFile($wav)
$synth.Speak($text)
$synth.Dispose()

$dur = & ffprobe -v error -show_entries format=duration -of csv=p=0 $wav
Write-Host "speech duration: $dur s"
& ffmpeg -v error -y -nostdin -f lavfi -i "testsrc2=size=1280x720:rate=30" -i $wav `
  -shortest -c:v libx264 -preset veryfast -crf 28 -pix_fmt yuv420p -c:a aac -b:a 128k -ar 48000 $Out
Remove-Item $wav -ErrorAction SilentlyContinue
Write-Host "wrote $Out"
