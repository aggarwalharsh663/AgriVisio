Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

pythonwPath = scriptDir & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(pythonwPath) Then
    WshShell.Run """" & pythonwPath & """ """ & scriptDir & "\app_desktop.py""", 0, False
Else
    WshShell.Run "pythonw """ & scriptDir & "\app_desktop.py""", 0, False
End If
