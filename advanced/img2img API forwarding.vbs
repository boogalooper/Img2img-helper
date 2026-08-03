Option Explicit
On Error Resume Next

Dim appRef
Dim desc

Dim WshArguments, i, list, FSO, f, CurrentPath
set WshArguments=WScript.Arguments

Set appRef = CreateObject("Photoshop.Application")
Set desc = CreateObject("Photoshop.ActionDescriptor")
if WshArguments.count()> 0 then
    desc.putString appRef.stringIDToTypeID("args"), "--dialog"
End if
appRef.executeAction appRef.stringIDToTypeID("5f6f57dc-80c8-49b4-9ea9-405d132b7b30"), desc, 3
