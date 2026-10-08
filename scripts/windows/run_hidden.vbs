' APEX hidden launcher (2026-10-07): runs its arguments with no console window
' (window style 0), waits for exit, and returns the child's exit code to Task
' Scheduler. Why: wsl.exe under an interactive-token task opened a console that
' took focus and minimised fullscreen programs every 15 min (cycle watch).
' Usage: wscript.exe run_hidden.vbs <program> [args...]
Dim sh, cmd, i
Set sh = CreateObject("WScript.Shell")
cmd = ""
For i = 0 To WScript.Arguments.Count - 1
    If InStr(WScript.Arguments(i), " ") > 0 Then
        cmd = cmd & """" & WScript.Arguments(i) & """ "
    Else
        cmd = cmd & WScript.Arguments(i) & " "
    End If
Next
WScript.Quit sh.Run(Trim(cmd), 0, True)
