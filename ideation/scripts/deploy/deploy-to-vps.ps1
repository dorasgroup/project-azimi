param(
    [Parameter(Mandatory = $true)]
    [string]$SshUser,

    [string]$HostAddress = "5.10.249.198",

    [string]$RemoteDir = "/opt/ideation-rag",

    [string]$PackagePath = "D:\pr\07\dist\ideation-rag-vps.zip"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PackagePath)) {
    throw "Package not found: $PackagePath. Run scripts\deploy\package-vps.ps1 first."
}

$target = "$SshUser@$HostAddress"

ssh $target "mkdir -p $RemoteDir"
scp $PackagePath "${target}:/tmp/ideation-rag-vps.zip"
ssh $target "rm -rf $RemoteDir/* && unzip -o /tmp/ideation-rag-vps.zip -d $RemoteDir && chmod +x $RemoteDir/scripts/deploy/vps-first-run.sh"

Write-Host "Project uploaded to $target:$RemoteDir"
Write-Host "Next on VPS:"
Write-Host "cd $RemoteDir/infrastructure && nano .env && docker compose -f docker-compose.prod.yml up -d --build"
