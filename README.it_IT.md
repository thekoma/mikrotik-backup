# Mikrotik Backup Solution

*For English documentation, see [README.md](README.md)*

Una soluzione completa per il backup automatizzato di dispositivi Mikrotik con supporto per storage S3-compatible.

## 🚀 Caratteristiche

- **Modalità Multiple**: Supporto per modalità daemon e cronjob
- **Backup Paralleli**: Esecuzione simultanea di backup da più router
- **Storage S3**: Supporto per qualsiasi storage S3-compatible (Wasabi, AWS S3, ecc.)
- **Gestione Retention**: Politica di retention configurabile per backup giornalieri, mensili e annuali
- **Sicurezza**: Supporto per secrets esistenti o configurazione inline
- **Flessibilità**: Configurazione tramite values o variabili d'ambiente

## 📋 Requisiti

- Kubernetes 1.16+
- Helm 3.0+
- Un bucket S3-compatible
- Accesso SSH ai router Mikrotik

## 🛠️ Installazione

### Utilizzo con Secrets Esistenti

1. Crea il secret per le credenziali:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: mikrotik-existing-credentials
type: Opaque
stringData:
  s3-type: "wasabi"
  s3-bucket: "your-bucket"
  s3-endpoint: "https://s3.wasabisys.com"
  access-key: "your-access-key"
  secret-key: "your-secret-key"
  username: "backup-user"
  ssh-key: |
    -----BEGIN OPENSSH PRIVATE KEY-----
    your-ssh-private-key
    -----END OPENSSH PRIVATE KEY-----
```

2. Installa il chart:

```bash
helm install mikrotik-backup oci://ghcr.io/thekoma/mikrotik-backup/charts \
  -f values-with-existing-secret.yaml
```

### Configurazione All-in-One

Per ambienti di test o sviluppo, puoi utilizzare la configurazione all-in-one:

```bash
helm install mikrotik-backup oci://ghcr.io/thekoma/mikrotik-backup/charts \
  -f values-all-in-one.yaml
```

### Utilizzo con Docker Compose

1. Prepara i file di configurazione:

```bash
# Crea il file di configurazione
cp config.example.toml config.toml
# Modifica il file con i tuoi parametri
nano config.toml

# Genera una chiave SSH per l'autenticazione
ssh-keygen -t ed25519 -f mikrotik-rsa -C "backup@mikrotik"
```

2. Avvia il container:

```bash
docker compose up -d
```

#### Configurazione Docker Compose

Il servizio può essere configurato in due modi:

1. **Usando config.toml** (raccomandato):
```yaml
services:
  mikrotik-backup:
    image: ghcr.io/thekoma/mikrotik-backup/app:latest
    volumes:
      - ./config.toml:/etc/mikrotik_backup.toml:ro
      - ./mikrotik-rsa:/mikrotik-rsa:ro
    environment:
      - TZ=Europe/Rome
```

2. **Usando variabili d'ambiente**:
```yaml
services:
  mikrotik-backup:
    image: ghcr.io/thekoma/mikrotik-backup/app:latest
    volumes:
      - ./mikrotik-rsa:/mikrotik-rsa:ro
    environment:
      - TZ=Europe/Rome
      - MIKROTIK_SSH_USER=backupper
      - MIKROTIK_S3_TYPE=wasabi
      - MIKROTIK_S3_BUCKET=mikrotik-bck
      - MIKROTIK_S3_ENDPOINT=https://s3.wasabisys.com
      - MIKROTIK_S3_ACCESS_KEY=your-access-key
      - MIKROTIK_S3_SECRET_KEY=your-secret-key
```

#### Parametri Docker Compose

| Parametro | Descrizione |
|-----------|-------------|
| `volumes` | Monta i file di configurazione e la chiave SSH |
| `environment` | Configura timezone e override delle configurazioni |
| `command` | Personalizza i parametri di esecuzione |
| `restart` | Politica di restart del container |

#### Variabili d'Ambiente Supportate

| Variabile | Descrizione | Default |
|-----------|-------------|---------|
| `MIKROTIK_SSH_USER` | Username SSH | `backupper` |
| `MIKROTIK_S3_TYPE` | Tipo di storage S3 | `wasabi` |
| `MIKROTIK_S3_BUCKET` | Nome del bucket | - |
| `MIKROTIK_S3_ENDPOINT` | Endpoint S3 | - |
| `MIKROTIK_S3_ACCESS_KEY` | Access key S3 | - |
| `MIKROTIK_S3_SECRET_KEY` | Secret key S3 | - |
| `TZ` | Timezone | `UTC` |

## ⚙️ Configurazione

### Modalità di Deployment

#### Daemon Mode
```yaml
deploymentMode: "daemon"
backup:
  times: ["02:00", "10:00", "18:00"]
  executeOnStart: true
```

#### CronJob Mode
```yaml
deploymentMode: "cronjob"
backup:
  times: ["02:00", "10:00", "18:00"]
```

### Storage Configuration

#### Con Secret Esistente
```yaml
storage:
  existingSecret: "mikrotik-existing-credentials"
```

#### Configurazione Inline
```yaml
storage:
  type: "wasabi"
  bucket: "mikrotik-bck"
  endpoint: "https://s3.wasabisys.com"
  s3Credentials:
    accessKey: "your-access-key"
    secretKey: "your-secret-key"
```

### SSH Configuration

#### Con Secret Esistente
```yaml
ssh:
  keyPath: "/mikrotik-rsa"
  existingSecret: "mikrotik-existing-credentials"
```

#### Configurazione Inline
```yaml
ssh:
  username: "backupper"
  keyPath: "/mikrotik-rsa"
  key: |
    -----BEGIN OPENSSH PRIVATE KEY-----
    your-ssh-private-key
    -----END OPENSSH PRIVATE KEY-----
```

### Retention Policy
```yaml
retention:
  daily: 30    # Mantiene 30 backup giornalieri
  monthly: 12  # Mantiene 12 backup mensili
  yearly: 5    # Mantiene 5 backup annuali
```

## 🔒 Sicurezza

### Best Practices

1. **Secrets**: Usa sempre secrets esistenti in produzione
2. **SSH**: Usa una chiave SSH dedicata con permessi minimi
3. **S3**: Crea un utente IAM dedicato con accesso solo al bucket necessario
4. **Network**: Limita l'accesso di rete ai soli router necessari

### Configurazione Router Mikrotik

1. Crea un gruppo dedicato:
```routeros
/user group add name=backup policy=read,test
```

2. Crea un utente dedicato:
```routeros
/user add name=backupper group=backup
```

3. Importa la chiave SSH:
```routeros
/user ssh-keys import public-key-file=mikrotik.pub user=backupper
```

## 🔍 Troubleshooting

### Problemi Comuni

1. **SSH Connection Failed**
   - Verifica che la chiave SSH sia corretta
   - Controlla i permessi dell'utente sul router
   - Verifica la connettività di rete

2. **S3 Upload Failed**
   - Verifica le credenziali S3
   - Controlla i permessi sul bucket
   - Verifica l'endpoint S3

3. **Pod Crashes**
   - Controlla i logs: `kubectl logs -n mikrotik deployment/mikrotik-backup`
   - Verifica la configurazione nel ConfigMap
   - Controlla le risorse disponibili

## 📊 Monitoraggio

Il backup fornisce logging dettagliato e statistiche:
- Numero di backup completati/falliti
- Dimensioni dei backup
- Tempi di esecuzione
- Stato della retention policy

## 🤝 Contributing

Le contribuzioni sono benvenute! Per favore:
1. Fai fork del repository
2. Crea un branch per le tue modifiche
3. Invia una Pull Request

## 📝 License

MIT License
