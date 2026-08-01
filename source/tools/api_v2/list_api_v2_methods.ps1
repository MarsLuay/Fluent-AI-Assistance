param(
    [string]$DllPath = 'C:\Program Files (x86)\Tecan\FluentControl\Tecan.VisionX.API.V2.dll'
)

$asm = [Reflection.Assembly]::LoadFrom($DllPath)
$rows = @()
foreach ($type in $asm.GetExportedTypes()) {
    foreach ($method in $type.GetMethods([Reflection.BindingFlags]'Public,Instance,Static,DeclaredOnly')) {
        if ($method.IsSpecialName) { continue }
        $params = ($method.GetParameters() | ForEach-Object { "$($_.ParameterType.Name) $($_.Name)" }) -join ', '
        $rows += [PSCustomObject]@{
            Type = $type.FullName
            Method = $method.Name
            Signature = "$($method.ReturnType.Name) $($method.Name)($params)"
            IsStatic = $method.IsStatic
        }
    }
}
$rows | Sort-Object Type, Method | ConvertTo-Json -Depth 4
