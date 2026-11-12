{{/* ------------------------------------------------------------------------------------------
Names and labels
------------------------------------------------------------------------------------------- */}}

{{- define "workmind.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "workmind.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* <fullname>-<component>, e.g. (include "workmind.componentName" (list $ "api")) */}}
{{- define "workmind.componentName" -}}
{{- $root := index . 0 -}}
{{- printf "%s-%s" (include "workmind.fullname" $root) (index . 1) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "workmind.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Selector labels: (list $ "component") */}}
{{- define "workmind.selectorLabels" -}}
{{- $root := index . 0 -}}
app.kubernetes.io/name: {{ include "workmind.name" $root }}
app.kubernetes.io/instance: {{ $root.Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{/* All labels: (list $ "component") */}}
{{- define "workmind.labels" -}}
{{- $root := index . 0 -}}
helm.sh/chart: {{ include "workmind.chart" $root }}
{{ include "workmind.selectorLabels" . }}
app.kubernetes.io/version: {{ $root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ $root.Release.Service }}
app.kubernetes.io/part-of: workmind
{{- with $root.Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "workmind.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "workmind.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* ------------------------------------------------------------------------------------------
Images: (list $ imageValues defaultTag)
------------------------------------------------------------------------------------------- */}}
{{- define "workmind.image" -}}
{{- $root := index . 0 -}}
{{- $image := index . 1 -}}
{{- $defaultTag := index . 2 -}}
{{- $registry := default $image.registry $root.Values.global.imageRegistry -}}
{{- $name := ternary (printf "%s/%s" $registry $image.repository) $image.repository (ne $registry "") -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $name $image.digest -}}
{{- else -}}
{{- printf "%s:%s" $name (toString (default $defaultTag $image.tag)) -}}
{{- end -}}
{{- end -}}

{{- define "workmind.apiImage" -}}
{{- include "workmind.image" (list . .Values.images.api .Chart.AppVersion) -}}
{{- end -}}

{{/* Pod fields shared by every pod of the chart: (list $ componentValues [serviceAccountName]).
Indent with nindent 6. */}}
{{- define "workmind.podCommon" -}}
{{- $root := index . 0 -}}
{{- $c := index . 1 -}}
{{- $sa := include "workmind.serviceAccountName" $root -}}
{{- if gt (len .) 2 }}{{ $sa = index . 2 }}{{ end -}}
serviceAccountName: {{ $sa }}
automountServiceAccountToken: false
{{- with $root.Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ if kindIs "map" . }}{{ .name }}{{ else }}{{ . }}{{ end }}
{{- end }}
{{- end }}
{{- with $c.priorityClassName }}
priorityClassName: {{ . }}
{{- end }}
{{- with $c.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $c.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $c.topologySpreadConstraints }}
topologySpreadConstraints:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* ------------------------------------------------------------------------------------------
Where the dependencies are (bundled or external)
------------------------------------------------------------------------------------------- */}}
{{- define "workmind.postgresHost" -}}
{{- if .Values.postgresql.enabled -}}
{{- include "workmind.componentName" (list . "postgresql") -}}
{{- else -}}
{{- required "database.host is required when postgresql.enabled is false" .Values.database.host -}}
{{- end -}}
{{- end -}}

{{- define "workmind.postgresPort" -}}
{{- if .Values.postgresql.enabled }}5432{{ else }}{{ .Values.database.port }}{{ end -}}
{{- end -}}

{{- define "workmind.valkeyHost" -}}
{{- if .Values.valkey.enabled -}}
{{- include "workmind.componentName" (list . "valkey") -}}
{{- else -}}
{{- required "valkey.external.host is required when valkey.enabled is false" .Values.valkey.external.host -}}
{{- end -}}
{{- end -}}

{{- define "workmind.valkeyPort" -}}
{{- if .Values.valkey.enabled }}6379{{ else }}{{ .Values.valkey.external.port }}{{ end -}}
{{- end -}}

{{- define "workmind.qdrantUrl" -}}
{{- if .Values.qdrant.enabled -}}
{{- printf "http://%s:6333" (include "workmind.componentName" (list . "qdrant")) -}}
{{- else -}}
{{- required "qdrant.external.url is required when qdrant.enabled is false" .Values.qdrant.external.url | trimSuffix "/" -}}
{{- end -}}
{{- end -}}

{{- define "workmind.openfgaUrl" -}}
{{- if .Values.openfga.enabled -}}
{{- printf "http://%s:8080" (include "workmind.componentName" (list . "openfga")) -}}
{{- else -}}
{{- required "openfga.external.url is required when openfga.enabled is false" .Values.openfga.external.url | trimSuffix "/" -}}
{{- end -}}
{{- end -}}

{{- define "workmind.keycloakUrl" -}}
{{- if .Values.keycloak.enabled -}}
{{- printf "http://%s:8080/auth" (include "workmind.componentName" (list . "keycloak")) -}}
{{- else -}}
{{- required "keycloak.external.url is required when keycloak.enabled is false" .Values.keycloak.external.url | trimSuffix "/" -}}
{{- end -}}
{{- end -}}

{{- define "workmind.embeddingBaseUrl" -}}
{{- if .Values.config.embedding.baseUrl -}}
{{- .Values.config.embedding.baseUrl -}}
{{- else if .Values.embeddings.enabled -}}
{{- printf "http://%s:8080/v1" (include "workmind.componentName" (list . "embeddings")) -}}
{{- end -}}
{{- end -}}

{{/* The API inside the cluster (the web app's server side, Keycloak's back-channel logout). */}}
{{- define "workmind.apiInternalUrl" -}}
{{- printf "http://%s.%s.svc:%v" (include "workmind.componentName" (list . "api")) .Release.Namespace .Values.api.service.port -}}
{{- end -}}

{{/* The response headers deploy/caddy/Caddyfile sets, for an HTTPRoute rule. X-Frame-Options:
nothing in WorkMind sets it, and Keycloak sets the same value. */}}
{{- define "workmind.httpRouteFilters" -}}
{{- if .Values.httpRoute.securityHeaders -}}
filters:
  - type: ResponseHeaderModifier
    responseHeaderModifier:
      set:
        - {name: X-Content-Type-Options, value: nosniff}
        - {name: Referrer-Policy, value: strict-origin-when-cross-origin}
        - {name: X-Frame-Options, value: SAMEORIGIN}
      remove: [Server]
{{- end -}}
{{- end -}}

{{/* Host of config.publicUrl, without a port. */}}
{{- define "workmind.publicHost" -}}
{{- $url := urlParse .Values.config.publicUrl -}}
{{- regexReplaceAll ":[0-9]+$" $url.host "" -}}
{{- end -}}

{{- define "workmind.publicUrl" -}}
{{- .Values.config.publicUrl | trimSuffix "/" -}}
{{- end -}}

{{/* ------------------------------------------------------------------------------------------
Pod building blocks
------------------------------------------------------------------------------------------- */}}

{{/* Pod security context: (list $ componentOverride) */}}
{{- define "workmind.podSecurityContext" -}}
{{- $root := index . 0 -}}
{{- toYaml (mergeOverwrite (deepCopy $root.Values.podSecurityContext) (deepCopy (index . 1))) -}}
{{- end -}}

{{/* Container security context: (list $ componentOverride) */}}
{{- define "workmind.securityContext" -}}
{{- $root := index . 0 -}}
{{- toYaml (mergeOverwrite (deepCopy $root.Values.securityContext) (deepCopy (index . 1))) -}}
{{- end -}}

{{/* The restricted container security context of the bundled services: (readOnlyRootFilesystem) */}}
{{- define "workmind.infraSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: {{ . }}
runAsNonRoot: true
capabilities:
  drop: [ALL]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Affinity of a pod that mounts the object storage claim: (list $ userAffinity). With a
ReadWriteOnce claim, every such pod must run on the same node: a required pod affinity to the
others does that (the first one may go anywhere: a pod matching its own term is allowed). */}}
{{- define "workmind.objectStorageAffinity" -}}
{{- $root := index . 0 -}}
{{- $affinity := deepCopy (index . 1) -}}
{{- $os := $root.Values.objectStorage -}}
{{- if and $os.colocatePods (not (has "ReadWriteMany" $os.accessModes)) -}}
{{- $term := dict "topologyKey" "kubernetes.io/hostname" "labelSelector" (dict "matchLabels" (dict "app.kubernetes.io/instance" $root.Release.Name "workmind.io/object-storage" "true")) -}}
{{- $podAffinity := get $affinity "podAffinity" | default dict -}}
{{- $required := get $podAffinity "requiredDuringSchedulingIgnoredDuringExecution" | default list -}}
{{- $_ := set $podAffinity "requiredDuringSchedulingIgnoredDuringExecution" (append $required $term) -}}
{{- $_ := set $affinity "podAffinity" $podAffinity -}}
{{- end -}}
{{- if $affinity }}{{ toYaml $affinity }}{{ end -}}
{{- end -}}

{{- define "workmind.objectStorageClaim" -}}
{{- default (include "workmind.componentName" (list . "objects")) .Values.objectStorage.existingClaim -}}
{{- end -}}

{{/* A Python loop in the API image that waits for TCP ports and HTTP URLs:
(list $ timeoutSeconds target...) where a target is tcp://host:port or an http(s) URL. */}}
{{- define "workmind.waitContainer" -}}
{{- $root := index . 0 -}}
- name: wait-for-dependencies
  image: {{ include "workmind.apiImage" $root }}
  imagePullPolicy: {{ $root.Values.images.api.pullPolicy }}
  command:
    - python
    - -c
    - |
      import socket, sys, time, urllib.request
      deadline = time.monotonic() + int(sys.argv[1])
      for target in sys.argv[2:]:
          while True:
              try:
                  if target.startswith("tcp://"):
                      host, port = target[6:].rsplit(":", 1)
                      socket.create_connection((host, int(port)), timeout=3).close()
                  else:
                      urllib.request.urlopen(target, timeout=5).close()
                  print("ready:", target, flush=True)
                  break
              except Exception as exc:
                  if time.monotonic() > deadline:
                      sys.exit(f"timed out waiting for {target}: {exc}")
                  print("waiting for", target, "-", exc, flush=True)
                  time.sleep(3)
  args:
    - {{ index . 1 | quote }}
    {{- range (slice . 2) }}
    - {{ . | quote }}
    {{- end }}
  securityContext:
    {{- include "workmind.securityContext" (list $root (dict)) | nindent 4 }}
  resources:
    requests: {cpu: 10m, memory: 64Mi}
    limits: {cpu: 200m, memory: 256Mi}
{{- end -}}

{{/* Checksums that roll the pods when settings or credentials change. Of the data only: the
labels carry the chart and app versions, and an upgrade that changes nothing else must not
restart Valkey, Keycloak or OpenFGA. */}}
{{- define "workmind.checksums" -}}
checksum/config: {{ (include (print .Template.BasePath "/configmap.yaml") . | fromYaml).data | toJson | sha256sum }}
checksum/credentials: {{ include "workmind.credentialsChecksum" . }}
{{- end -}}

{{/* The chart Secret's data (empty when every credential comes from your own Secrets). */}}
{{- define "workmind.credentialsChecksum" -}}
{{- (include (print .Template.BasePath "/secret.yaml") . | fromYaml).data | toJson | sha256sum -}}
{{- end -}}

{{/* ------------------------------------------------------------------------------------------
Environment of the WorkMind containers (api, worker, beat, seed). Non-secret settings come
from the ConfigMap (envFrom); credentials from Secrets; the connection URLs are put together
in the pod from the password variables (Kubernetes expands $(VAR) from earlier entries).
------------------------------------------------------------------------------------------- */}}
{{- define "workmind.appEnv" -}}
{{- $root := . -}}
{{- include "workmind.credentialEnv" (list $root "databaseAppPassword" "WM_DATABASE_APP_PASSWORD") }}
- name: DATABASE_URL
  value: {{ include "workmind.databaseUrl" (list $root $root.Values.database.appUser "WM_DATABASE_APP_PASSWORD") | quote }}
{{- $valkeyAuth := include "workmind.credentialEnv" (list $root "valkeyPassword" "WM_VALKEY_PASSWORD") }}
{{- with $valkeyAuth }}
{{ . }}
{{- end }}
- name: VALKEY_URL
  value: {{ include "workmind.valkeyUrl" (list $root (ne $valkeyAuth "") 0) | quote }}
- name: CELERY_BROKER_URL
  value: {{ include "workmind.valkeyUrl" (list $root (ne $valkeyAuth "") 1) | quote }}
{{- range $name, $env := dict "openfgaApiKey" "OPENFGA_API_KEY" "oidcClientSecret" "OIDC_CLIENT_SECRET" "secretsKey" "SECRETS_KEY" "secretsEncryptionKey" "SECRETS_ENCRYPTION_KEY" "secretsEncryptionPreviousKeys" "SECRETS_ENCRYPTION_PREVIOUS_KEYS" "anthropicApiKey" "ANTHROPIC_API_KEY" "llmApiKey" "LLM_API_KEY" "localLlmApiKey" "LOCAL_LLM_API_KEY" "embeddingApiKey" "EMBEDDING_API_KEY" "transcriptionApiKey" "TRANSCRIPTION_API_KEY" "smtpPassword" "SMTP_PASSWORD" "googleClientSecret" "GOOGLE_CLIENT_SECRET" "licenseKey" "LICENSE_KEY" "mcpServers" "MCP_SERVERS" }}
{{- with include "workmind.credentialEnv" (list $root $name $env) }}
{{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{/* OpenFGA's datastore settings: (list $ uriWithoutCredentials) */}}
{{- define "workmind.openfgaDatastoreEnv" -}}
{{- $root := index . 0 -}}
- name: OPENFGA_DATASTORE_ENGINE
  value: postgres
# No credentials in the URI: they come separately, so the password needs no URL encoding.
- name: OPENFGA_DATASTORE_URI
  value: {{ index . 1 | quote }}
- name: OPENFGA_DATASTORE_USERNAME
  value: {{ $root.Values.openfga.database.username | quote }}
{{ include "workmind.credentialEnv" (list $root "openfgaDbPassword" "OPENFGA_DATASTORE_PASSWORD") }}
{{- end -}}

{{/* (list $ user passwordVariable) */}}
{{- define "workmind.databaseUrl" -}}
{{- $root := index . 0 -}}
{{- $db := $root.Values.database -}}
{{- $url := printf "postgresql+asyncpg://%s:$(%s)@%s:%s/%s" (index . 1) (index . 2) (include "workmind.postgresHost" $root) (include "workmind.postgresPort" $root) $db.name -}}
{{- if $db.urlParams }}{{ printf "%s?%s" $url (trimPrefix "?" $db.urlParams) }}{{ else }}{{ $url }}{{ end -}}
{{- end -}}

{{/* (list $ withPassword databaseNumber) */}}
{{- define "workmind.valkeyUrl" -}}
{{- $root := index . 0 -}}
{{- $scheme := ternary "rediss" "redis" (and (not $root.Values.valkey.enabled) $root.Values.valkey.external.tls) -}}
{{- $auth := ternary ":$(WM_VALKEY_PASSWORD)@" "" (index . 1) -}}
{{- printf "%s://%s%s:%s/%d" $scheme $auth (include "workmind.valkeyHost" $root) (include "workmind.valkeyPort" $root) (index . 2) -}}
{{- end -}}

{{/* Settings and checks that fail early with a clear message. */}}
{{- define "workmind.validate" -}}
{{- if not (regexMatch "^https?://[^/\\s]+/?$" .Values.config.publicUrl) -}}
{{- fail "config.publicUrl must be a URL without a path, e.g. https://workmind.example.com" -}}
{{- end -}}
{{- if and .Values.postgresql.enabled (or (ne .Values.database.name "workmind") (ne .Values.database.appUser "workmind_app") (ne .Values.database.ownerUser "workmind_owner")) -}}
{{- fail "the bundled Postgres creates the database workmind with the roles workmind_app and workmind_owner: database.name, appUser and ownerUser can only change with an external database" -}}
{{- end -}}
{{- if and .Values.keycloak.enabled (or (ne .Values.keycloak.realm "workmind") (ne .Values.keycloak.oidcClientId "workmind-api")) -}}
{{- fail "the bundled Keycloak imports the realm workmind with the client workmind-api (files/keycloak/realm-workmind.json): keycloak.realm and oidcClientId can only change with an external Keycloak" -}}
{{- end -}}
{{- if and (eq .Values.config.embedding.provider "openai_compatible") (not (include "workmind.embeddingBaseUrl" .)) -}}
{{- fail "config.embedding.baseUrl is required when embeddings.enabled is false (or set config.embedding.provider: hash)" -}}
{{- end -}}
{{- if and .Values.seed.enabled (eq .Values.config.appEnv "production") -}}
{{- fail "seed.enabled loads demo data and logins: it refuses config.appEnv production (use demo or dev)" -}}
{{- end -}}
{{- if .Values.seed.enabled -}}
{{- $src := .Values.seed.data.source -}}
{{- if and (eq $src "image") (not .Values.seed.data.image.repository) -}}
{{- fail "seed.data.image.repository is required with seed.data.source image" -}}
{{- else if and (eq $src "archive") (not .Values.seed.data.archiveUrl) -}}
{{- fail "seed.data.archiveUrl is required with seed.data.source archive" -}}
{{- else if and (eq $src "existingClaim") (not .Values.seed.data.existingClaim) -}}
{{- fail "seed.data.existingClaim is required with seed.data.source existingClaim" -}}
{{- end -}}
{{- end -}}
{{- if and .Values.ingress.enabled .Values.httpRoute.enabled -}}
{{- fail "enable either ingress or httpRoute, not both" -}}
{{- end -}}
{{- if and .Values.httpRoute.enabled (not .Values.httpRoute.parentRefs) -}}
{{- fail "httpRoute.parentRefs is required with httpRoute.enabled (the Gateway to attach to)" -}}
{{- end -}}
{{- end -}}
