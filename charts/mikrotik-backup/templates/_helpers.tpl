{{/*
Expand the name of the chart.
*/}}
{{- define "mikrotik-backup.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "mikrotik-backup.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "mikrotik-backup.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "mikrotik-backup.labels" -}}
helm.sh/chart: {{ include "mikrotik-backup.chart" . }}
{{ include "mikrotik-backup.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "mikrotik-backup.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mikrotik-backup.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "mikrotik-backup.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "mikrotik-backup.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "mikrotik-backup.storageSecretName" -}}
{{- if .Values.storage.credentials.existingSecret }}
{{- .Values.storage.credentials.existingSecret }}
{{- else }}
{{- include "mikrotik-backup.fullname" . }}-credentials
{{- end }}
{{- end }}

{{- define "mikrotik-backup.sshSecretName" -}}
{{- if .Values.ssh.existingSecret }}
{{- .Values.ssh.existingSecret }}
{{- else }}
{{- include "mikrotik-backup.fullname" . }}-ssh
{{- end }}
{{- end }}
