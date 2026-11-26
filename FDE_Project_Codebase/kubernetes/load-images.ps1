$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$archiveName = "vastra-kubernetes-images-$([guid]::NewGuid().ToString('N')).tar"
$archive = Join-Path $repoRoot $archiveName
$debugPod = $null

Push-Location $repoRoot
try {
    $node = kubectl get nodes -l node-role.kubernetes.io/control-plane -o "jsonpath={.items[0].metadata.name}"
    if ($LASTEXITCODE -ne 0 -or -not $node) {
        throw "Could not find a Kubernetes control-plane node."
    }

    kubectl create namespace vastra --dry-run=client -o yaml | kubectl apply -f -
    if ($LASTEXITCODE -ne 0) {
        throw "Could not ensure the vastra namespace exists."
    }

    kubectl label node $node vastra.local/image-cache=true --overwrite
    if ($LASTEXITCODE -ne 0) {
        throw "Could not label the local image-cache node."
    }

    docker --context default build -t vastra-legacy:local (Join-Path $repoRoot "FDE_Project_Codebase\vastra-legacy-lab")
    if ($LASTEXITCODE -ne 0) {
        throw "Building the legacy/partner image failed."
    }

    docker --context default build -t vastra-modern-api:local (Join-Path $repoRoot "FDE_Project_Codebase\vastra-modern-api")
    if ($LASTEXITCODE -ne 0) {
        throw "Building the modern API image failed."
    }

    docker --context default save --output $archive vastra-legacy:local vastra-modern-api:local
    if ($LASTEXITCODE -ne 0) {
        throw "Exporting the application images failed."
    }

    $debugOutput = kubectl debug "node/$node" -n vastra --image=alpine:3.21 --profile=sysadmin -- sleep 1800 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not start the temporary image-loader pod: $debugOutput"
    }

    $match = [regex]::Match(($debugOutput | Out-String), "debugging pod ([A-Za-z0-9-]+)")
    if (-not $match.Success) {
        throw "Could not identify the temporary image-loader pod: $debugOutput"
    }
    $debugPod = $match.Groups[1].Value

    kubectl wait --for=condition=Ready "pod/$debugPod" -n vastra --timeout=90s
    if ($LASTEXITCODE -ne 0) {
        throw "The temporary image-loader pod did not become ready."
    }

    kubectl exec -n vastra $debugPod -- sh -c "apk add --no-cache containerd-ctr"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the containerd image import utility."
    }

    kubectl cp $archiveName "vastra/${debugPod}:/tmp/vastra-kubernetes-images.tar"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not copy the application images into the Kubernetes node."
    }

    kubectl exec -n vastra $debugPod -- ctr --address /host/run/containerd/containerd.sock --namespace k8s.io images import /tmp/vastra-kubernetes-images.tar
    if ($LASTEXITCODE -ne 0) {
        throw "Importing application images into the Kubernetes node failed."
    }

    Write-Output "Application images are loaded on $node. Apply local-stack.yaml next."
}
finally {
    if ($debugPod) {
        kubectl delete pod $debugPod -n vastra --ignore-not-found
    }
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive
    }
    Pop-Location
}
