param(
    [switch]$Loop,
    [int]$ParentPid = 0
)

# Reads Windows Global System Media Transport Controls sessions and prints JSON.
# Works with Windows PowerShell 5.1 (built into Windows 10/11). Read-only.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
    function Await($op, $type) {
        $task = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
        $task.Wait(-1) | Out-Null
        $task.Result
    }
    $null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
    $null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime]
    $mgrType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]
    $propType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]

    function Get-SessionsJson {
        $out = @()
        try {
            $mgr = Await ($mgrType::RequestAsync()) $mgrType
            $sessions = $mgr.GetSessions()
        } catch {
            return '[]'
        }
        foreach ($s in $sessions) {
            try {
                $p = Await ($s.TryGetMediaPropertiesAsync()) $propType
                $status = ''
                try {
                    $status = $s.GetPlaybackInfo().PlaybackStatus.ToString()
                } catch {}
                $duration = 0
                try {
                    $tl = $s.GetTimelineProperties()
                    if ($tl -and $tl.EndTime) {
                        $duration = [int][Math]::Round($tl.EndTime.TotalSeconds)
                    }
                } catch {}
                $out += [pscustomobject]@{
                    app      = $s.SourceAppUserModelId
                    title    = $p.Title
                    artist   = $p.Artist
                    album    = $p.AlbumTitle
                    status   = $status
                    duration = $duration
                }
            } catch {}
        }
        if ($out.Count -eq 0) { '[]' } else { ConvertTo-Json -InputObject @($out) -Compress }
    }

    if ($Loop) {
        while ($true) {
            if ($ParentPid -gt 0) {
                try {
                    $parent = [System.Diagnostics.Process]::GetProcessById($ParentPid)
                    if ($parent.HasExited) { break }
                } catch {
                    break
                }
            }
            $json = Get-SessionsJson
            [Console]::WriteLine($json)
            [Console]::Out.Flush()
            Start-Sleep -Seconds 1
        }
    } else {
        $json = Get-SessionsJson
        [Console]::WriteLine($json)
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
