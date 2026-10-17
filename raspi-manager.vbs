' Launcher: opens the raspi-manager native app with no console window at all.
Option Explicit
Dim shell, fso, root, pythonw, code
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"
shell.CurrentDirectory = root

If Not fso.FileExists(pythonw) Then
    ' First start only: a visible console shows the dependency installation.
    code = shell.Run("cmd /c python -m uv sync", 1, True)
    If code <> 0 Or Not fso.FileExists(pythonw) Then
        MsgBox "Could not set up the environment. Is Python 3.12 installed and 'python -m uv' available?", 16, "raspi-manager"
        WScript.Quit 1
    End If
End If

' Window style 0 = hidden console, False = do not wait for the app to close.
shell.Run """" & pythonw & """ -m raspi.gui", 0, False
