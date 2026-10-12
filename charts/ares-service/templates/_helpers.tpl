{{- define "ares.name" -}}
{{- default .Release.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ares.labels" -}}
app.kubernetes.io/name: {{ include "ares.name" . }}
app.kubernetes.io/part-of: ares
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "ares.selector" -}}
app.kubernetes.io/name: {{ include "ares.name" . }}
{{- end -}}

{{- define "ares.sa" -}}
{{- default (include "ares.name" .) .Values.serviceAccount.name -}}
{{- end -}}
