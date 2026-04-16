{{/*
Expand the name of the chart.
*/}}
{{- define "bookstore.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We use the release name as the prefix.
*/}}
{{- define "bookstore.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Resolve the image tag: use component-level tag if set, otherwise fall back to global.imageTag.
Usage: {{ include "bookstore.imageTag" (dict "tag" .Values.api.image.tag "globalTag" .Values.global.imageTag) }}
*/}}
{{- define "bookstore.imageTag" -}}
{{- if .tag -}}
{{- .tag -}}
{{- else -}}
{{- .globalTag -}}
{{- end -}}
{{- end }}

{{/*
Common labels
*/}}
{{- define "bookstore.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels for a component
Usage: {{ include "bookstore.selectorLabels" (dict "fullname" (include "bookstore.fullname" .) "component" "api") }}
*/}}
{{- define "bookstore.selectorLabels" -}}
app: {{ .fullname }}-{{ .component }}
{{- end }}
