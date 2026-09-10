{{- define "convoscore.labels" -}}
app.kubernetes.io/name: convoscore
app.kubernetes.io/component: {{ .component }}
{{- end -}}
