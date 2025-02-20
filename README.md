# Mikrotik Backup Solution

A complete solution for automated backup of Mikrotik devices with S3-compatible storage support.

## 🚀 Features

- **Multiple Modes**: Support for daemon and cronjob modes
- **Parallel Backups**: Simultaneous backup execution from multiple routers
- **S3 Storage**: Support for any S3-compatible storage (Wasabi, AWS S3, etc.)
- **Retention Management**: Configurable retention policy for daily, monthly, and yearly backups
- **Security**: Support for existing secrets or inline configuration
- **Flexibility**: Configuration via values or environment variables

## 📋 Requirements

- Kubernetes 1.16+
- Helm 3.0+
- An S3-compatible bucket
- SSH access to Mikrotik routers

## 🛠️ Installation

### Using Existing Secrets

1. Create the credentials secret:

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

2. Install the chart:

```bash
helm install mikrotik-backup oci://ghcr.io/thekoma/mikrotik-backup/charts \
  -f values-with-existing-secret.yaml
```

### All-in-One Configuration

For test or development environments, you can use the all-in-one configuration:

```bash
helm install mikrotik-backup oci://ghcr.io/thekoma/mikrotik-backup/charts \
  -f values-all-in-one.yaml
```

### Using Docker Compose

1. Prepare configuration files:

```bash
# Create configuration file
cp config.example.toml config.toml
# Edit file with your parameters
nano config.toml

# Generate SSH key for authentication
ssh-keygen -t ed25519 -f mikrotik-rsa -C "backup@mikrotik"
```

2. Start the container:

```bash
docker compose up -d
```

#### Docker Compose Configuration

The service can be configured in two ways:

1. **Using config.toml** (recommended):
```yaml
services:
  mikrotik-backup:
    image: ghcr.io/thekoma/mikrotik-backup/app:v2025.02.3
    volumes:
      - ./config.toml:/etc/mikrotik_backup.toml:ro
      - ./mikrotik-rsa:/mikrotik-rsa:ro
    environment:
      - TZ=Europe/Rome
```

2. **Using environment variables**:
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

#### Docker Compose Parameters

| Parameter | Description |
|-----------|-------------|
| `volumes` | Mount configuration files and SSH key |
| `environment` | Configure timezone and configuration overrides |
| `command` | Customize execution parameters |
| `restart` | Container restart policy |

#### Supported Environment Variables

| Variable | Description | Default |
|-----------|-------------|---------|
| `MIKROTIK_SSH_USER` | SSH Username | `backupper` |
| `MIKROTIK_S3_TYPE` | S3 storage type | `wasabi` |
| `MIKROTIK_S3_BUCKET` | Bucket name | - |
| `MIKROTIK_S3_ENDPOINT` | S3 endpoint | - |
| `MIKROTIK_S3_ACCESS_KEY` | S3 access key | - |
| `MIKROTIK_S3_SECRET_KEY` | S3 secret key | - |
| `TZ` | Timezone | `UTC` |

## ⚙️ Configuration

### Deployment Modes

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

#### With Existing Secret
```yaml
storage:
  existingSecret: "mikrotik-existing-credentials"
```

#### Inline Configuration
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

#### With Existing Secret
```yaml
ssh:
  keyPath: "/mikrotik-rsa"
  existingSecret: "mikrotik-existing-credentials"
```

#### Inline Configuration
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
  daily: 30    # Keep 30 daily backups
  monthly: 12  # Keep 12 monthly backups
  yearly: 5    # Keep 5 yearly backups
```

## 🔒 Security

### Best Practices

1. **Secrets**: Always use existing secrets in production
2. **SSH**: Use a dedicated SSH key with minimal permissions
3. **S3**: Create a dedicated IAM user with access only to the required bucket
4. **Network**: Limit network access to only necessary routers

### Mikrotik Router Configuration

1. Create a dedicated group:
```routeros
/user group add name=backup policy=read,test
```

2. Create a dedicated user:
```routeros
/user add name=backupper group=backup
```

3. Import SSH key:
```routeros
/user ssh-keys import public-key-file=mikrotik.pub user=backupper
```

## 🔍 Troubleshooting

### Common Issues

1. **SSH Connection Failed**
   - Verify SSH key is correct
   - Check user permissions on router
   - Verify network connectivity

2. **S3 Upload Failed**
   - Verify S3 credentials
   - Check bucket permissions
   - Verify S3 endpoint

3. **Pod Crashes**
   - Check logs: `kubectl logs -n mikrotik deployment/mikrotik-backup`
   - Verify configuration in ConfigMap
   - Check available resources

## 📊 Monitoring

The backup provides detailed logging and statistics:
- Number of completed/failed backups
- Backup sizes
- Execution times
- Retention policy status

## 🤝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a branch for your changes
3. Submit a Pull Request

## 📝 License

MIT License

*For Italian documentation, see [README.it_IT.md](README.it_IT.md)*
