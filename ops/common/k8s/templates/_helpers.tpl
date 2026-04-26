{{/*
공통 라벨 / 이름 헬퍼
*/}}
{{- define "app.fullname" -}}
{{- default .Chart.Name .Values.project | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "app.labels" -}}
app.kubernetes.io/name: {{ include "app.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
