{{/* ------------------------------------------------------------------------------------------
Credentials (values.yaml, `credentials`). For each one: which Secret and key hold it.
Order: its own existingSecret, its value, credentials.existingSecret, a generated value.
------------------------------------------------------------------------------------------- */}}

{{- define "workmind.credentialsSecretName" -}}
{{- include "workmind.componentName" (list . "credentials") -}}
{{- end -}}

{{/* generate: "password" (32 letters and digits), "aes" (32 random bytes, base64) or "".
required: the chart fails when nothing provides it. */}}
{{- define "workmind.credentialSpecs" -}}
{{- $v := .Values -}}
{{- $keycloakAdmin := or $v.keycloak.enabled $v.seed.enabled -}}
databaseAppPassword: {generate: password, required: true}
databaseOwnerPassword: {generate: password, required: true}
postgresPassword: {generate: {{ ternary "password" "" $v.postgresql.enabled | quote }}, required: {{ $v.postgresql.enabled }}}
keycloakDbPassword: {generate: {{ ternary "password" "" $v.keycloak.enabled | quote }}, required: {{ $v.keycloak.enabled }}}
openfgaDbPassword: {generate: {{ ternary "password" "" $v.openfga.enabled | quote }}, required: {{ $v.openfga.enabled }}}
{{- /* fromShared false: not read from credentials.existingSecret (it goes into URLs; an optional
reference to a missing key would leave "$(WM_VALKEY_PASSWORD)" in them). */}}
valkeyPassword: {generate: {{ ternary "password" "" (and $v.valkey.enabled $v.valkey.auth.enabled) | quote }}, required: {{ $v.valkey.auth.enabled }}, fromShared: {{ $v.valkey.auth.enabled }}}
openfgaApiKey: {generate: {{ ternary "password" "" $v.openfga.enabled | quote }}, required: true}
oidcClientSecret: {generate: {{ ternary "password" "" $v.keycloak.enabled | quote }}, required: true}
secretsKey: {generate: aes, required: true}
secretsEncryptionKey: {generate: aes, required: true}
secretsEncryptionPreviousKeys: {generate: "", required: false}
keycloakAdminPassword: {generate: {{ ternary "password" "" $v.keycloak.enabled | quote }}, required: {{ $keycloakAdmin }}}
demoPassword: {generate: "", required: false}
anthropicApiKey: {generate: "", required: false}
llmApiKey: {generate: "", required: false}
localLlmApiKey: {generate: "", required: false}
embeddingApiKey: {generate: "", required: false}
transcriptionApiKey: {generate: "", required: false}
smtpPassword: {generate: "", required: false}
googleClientSecret: {generate: "", required: false}
licenseKey: {generate: "", required: false}
mcpServers: {generate: "", required: false}
{{- end -}}

{{/* (list $ name) -> YAML {secret, key, optional}, or "" when nothing provides it. */}}
{{- define "workmind.credentialRef" -}}
{{- $root := index . 0 -}}
{{- $name := index . 1 -}}
{{- $spec := get (fromYaml (include "workmind.credentialSpecs" $root)) $name -}}
{{- $c := get $root.Values.credentials $name -}}
{{- if $c.existingSecret -}}
secret: {{ $c.existingSecret | quote }}
key: {{ $c.key | quote }}
optional: false
{{- else if $c.value -}}
secret: {{ include "workmind.credentialsSecretName" $root | quote }}
key: {{ $c.key | quote }}
optional: false
{{- else if and $root.Values.credentials.existingSecret (or (not (hasKey $spec "fromShared")) $spec.fromShared) -}}
secret: {{ $root.Values.credentials.existingSecret | quote }}
key: {{ $c.key | quote }}
optional: {{ not $spec.required }}
{{- else if $spec.generate -}}
secret: {{ include "workmind.credentialsSecretName" $root | quote }}
key: {{ $c.key | quote }}
optional: false
{{- else if $spec.required -}}
{{- fail (printf "credentials.%s is required: set its value or existingSecret, or credentials.existingSecret (docs/deploy/kubernetes.md, Secrets)" $name) -}}
{{- end -}}
{{- end -}}

{{/* True ("true") when the chart's own Secret holds this credential. */}}
{{- define "workmind.credentialInChartSecret" -}}
{{- $ref := include "workmind.credentialRef" . | fromYaml -}}
{{- if and $ref (eq $ref.secret (include "workmind.credentialsSecretName" (index . 0))) }}true{{ end -}}
{{- end -}}

{{/* (list $ name) -> the plain value for the chart's Secret: the configured value, else the value
already stored in the Secret (lookup), else a new random one. Remembered for the rest of the
render, so every template (and the migrate hook's own Secret) gets the same value. */}}
{{- define "workmind.credentialValue" -}}
{{- $root := index . 0 -}}
{{- $name := index . 1 -}}
{{- $c := get $root.Values.credentials $name -}}
{{- if $c.value -}}
{{- $c.value -}}
{{- else -}}
{{- if not (hasKey $root.Values "__workmindGenerated") -}}
{{- $_ := set $root.Values "__workmindGenerated" (dict) -}}
{{- end -}}
{{- $memo := get $root.Values "__workmindGenerated" -}}
{{- if not (hasKey $memo $name) -}}
{{- $existing := lookup "v1" "Secret" $root.Release.Namespace (include "workmind.credentialsSecretName" $root) -}}
{{- $stored := "" -}}
{{- if and $existing $existing.data (hasKey $existing.data $c.key) -}}
{{- $stored = index $existing.data $c.key | b64dec -}}
{{- end -}}
{{- if not $stored -}}
{{- $spec := get (fromYaml (include "workmind.credentialSpecs" $root)) $name -}}
{{- if eq $spec.generate "aes" -}}
{{- $stored = randBytes 32 -}}
{{- else -}}
{{- $stored = randAlphaNum 32 -}}
{{- end -}}
{{- end -}}
{{- $_ := set $memo $name $stored -}}
{{- end -}}
{{- get $memo $name -}}
{{- end -}}
{{- end -}}

{{/* (list $ name envName) -> one env entry reading the credential, or nothing. */}}
{{- define "workmind.credentialEnv" -}}
{{- $ref := include "workmind.credentialRef" (list (index . 0) (index . 1)) | fromYaml -}}
{{- if $ref -}}
- name: {{ index . 2 }}
  valueFrom:
    secretKeyRef:
      name: {{ $ref.secret }}
      key: {{ $ref.key }}
      {{- if $ref.optional }}
      optional: true
      {{- end }}
{{- end -}}
{{- end -}}

{{- define "workmind.credentialNames" -}}
{{- list "databaseAppPassword" "databaseOwnerPassword" "postgresPassword" "keycloakDbPassword" "openfgaDbPassword" "valkeyPassword" "openfgaApiKey" "oidcClientSecret" "secretsKey" "secretsEncryptionKey" "secretsEncryptionPreviousKeys" "keycloakAdminPassword" "demoPassword" "anthropicApiKey" "llmApiKey" "localLlmApiKey" "embeddingApiKey" "transcriptionApiKey" "smtpPassword" "googleClientSecret" "licenseKey" "mcpServers" | toJson -}}
{{- end -}}
