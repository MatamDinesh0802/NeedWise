{{/* HorizontalPodAutoscaler for a Deployment: (list $ component values) */}}
{{- define "needwise.hpa" -}}
{{- $root := index . 0 -}}
{{- $component := index . 1 -}}
{{- $a := (index . 2).autoscaling -}}
{{- if $a.enabled }}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "needwise.componentName" (list $root $component) }}
  labels:
    {{- include "needwise.labels" (list $root $component) | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "needwise.componentName" (list $root $component) }}
  minReplicas: {{ $a.minReplicas }}
  maxReplicas: {{ $a.maxReplicas }}
  metrics:
    {{- with $a.targetCPUUtilizationPercentage }}
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ . }}
    {{- end }}
    {{- with $a.targetMemoryUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {{ . }}
    {{- end }}
{{- end }}
{{- end -}}

{{/* PodDisruptionBudget: (list $ component values) */}}
{{- define "needwise.pdb" -}}
{{- $root := index . 0 -}}
{{- $component := index . 1 -}}
{{- $p := (index . 2).podDisruptionBudget -}}
{{- if $p.enabled }}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "needwise.componentName" (list $root $component) }}
  labels:
    {{- include "needwise.labels" (list $root $component) | nindent 4 }}
spec:
  {{- if hasKey $p "minAvailable" }}
  minAvailable: {{ $p.minAvailable }}
  {{- else }}
  maxUnavailable: {{ $p.maxUnavailable }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "needwise.selectorLabels" (list $root $component) | nindent 6 }}
{{- end }}
{{- end -}}

{{/* ClusterIP Service: (list $ component port [extra ports as list of dicts]) */}}
{{- define "needwise.service" -}}
{{- $root := index . 0 -}}
{{- $component := index . 1 -}}
{{- $svc := index . 2 -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "needwise.componentName" (list $root $component) }}
  labels:
    {{- include "needwise.labels" (list $root $component) | nindent 4 }}
  {{- with $svc.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  type: {{ default "ClusterIP" $svc.type }}
  {{- if $svc.headless }}
  clusterIP: None
  publishNotReadyAddresses: true
  {{- end }}
  selector:
    {{- include "needwise.selectorLabels" (list $root $component) | nindent 4 }}
  ports:
    {{- range $svc.ports }}
    - name: {{ .name }}
      port: {{ .port }}
      targetPort: {{ default .name .targetPort }}
      protocol: TCP
      {{- with .appProtocol }}
      appProtocol: {{ . }}
      {{- end }}
    {{- end }}
{{- end -}}
