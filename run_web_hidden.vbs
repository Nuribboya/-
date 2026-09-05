' run_web.bat을 창(터미널)이 아예 안 보이게 완전 백그라운드로 실행한다.
' 끄려면 작업관리자에서 python.exe 프로세스를 종료해야 한다 (Ctrl+C를 쓸 창이 없음).
Set objShell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
objShell.Run "cmd /c """ & scriptDir & "\run_web.bat""", 0, False
