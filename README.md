# Helm Chart for Mikrotik Backup

This chart deploys a Mikrotik backup solution on Kubernetes. It offers flexibility and various deployment options to suit different scenarios.

## Introduction 🚀

This project provides a Python script to automate backups of Mikrotik devices and store them securely on an S3-compatible bucket (like Wasabi, AWS S3, etc.). It supports scheduled backups via cron jobs, continuous execution as a daemon, and manual backups.

## Features ✨

* **Multiple Backups:** Backs up multiple Mikrotik routers in parallel.
* **S3 Storage:** Stores backups on an S3-compatible bucket.
* **Backup Rotation:** Manages the rotation of daily, monthly, and yearly backups.
* **Backup Formats:** Supports backups in plain text (`plain`) or JSON (`json`) format.
* **Daemon Mode:** Runs continuously as a daemon with configurable schedules.
* **CronJob Mode:** Runs backups based on a cron schedule.
* **Binary Backup:** Downloads the binary `.backup` file from the router.
* **Flexibility:** Configuration via TOML file or environment variables.
* **Detailed Logging:** For monitoring and troubleshooting.
* **Deployment:** Supports Docker Compose, Helm Chart for Kubernetes, and Systemd Timer.

## Deployment Options ⚙️

### 1. Docker Compose

The simplest way to run the script is using Docker Compose. The image will be pulled from GitHub Container Registry:

```bash
docker-compose up -d
```

By default, it uses the `latest` tag, but you can specify a version in your `docker-compose.yml`:

```yaml
services:
  mikrotik-backup:
    image: ghcr.io/thekoma/mikrotik-backup/app:v2024.11.7  # Replace with desired version
    # ... rest of the configuration
```

### 2. Helm Chart for Kubernetes

For Kubernetes deployments, a Helm Chart is available as an OCI artifact. You can install it directly from GitHub Container Registry:

```bash
# Add the repository
helm install mikrotik-backup oci://ghcr.io/thekoma/mikrotik-backup/charts --version <version>
```

For example, to install version 2024.11.7:
```bash
helm install mikrotik-backup oci://ghcr.io/thekoma/mikrotik-backup/charts --version v2024.11.6
```

> **Note**: The version follows the format `YYYY.MM.RELEASE` where:
> - `YYYY`: Current year
> - `MM`: Current month
> - `RELEASE`: Release number for the current month (starting from 0)

The container image is automatically pulled from `ghcr.io/thekoma/mikrotik-backup/app` with the matching version tag.

#### Configuration via `values.yaml`

The `values.yaml` file allows customization of the Helm Chart. Here's a breakdown of the parameters:

```yaml
# Application-specific configuration
deploymentMode: "cronjob" # "daemon" or "cronjob"

# Backup configuration
backup:
  times: ["02:00", "10:00", "18:00"] # Times to run backup in daemon mode (24h format)
  jobs: 4 # Number of parallel backup jobs
  format: "plain"  # or "json"
  localDir: "/tmp/mikrotik_backups" # Local directory for temporary backup storage

# SSH configuration
ssh:
  username: "backup" # SSH username
  keyPath: "/mikrotik-rsa" # Path to the SSH private key within the container
  existingSecret: "" # Name of an existing secret containing the SSH key (optional)
  secretKey: "ssh-private-key" # Key within the secret where the SSH private key is stored
  key: "" # Base64 encoded SSH private key (used if existingSecret is empty)

# Storage configuration (S3-compatible)
storage:
  type: "wasabi" # Type of S3 storage (e.g., "wasabi", "s3")
  bucket: "mikrotik-bck" # Bucket name
  endpoint: "https://s3.wasabisys.com" # Endpoint URL
  credentials:
    existingSecret: "" # Name of existing secret for credentials (optional)
    accessKeyKey: "access-key" # Key for access key in secret
    secretKeyKey: "secret-key" # Key for secret key in secret
    accessKey: "" # Access key (used if existingSecret is empty)
    secretKey: "" # Secret key (used if existingSecret is empty)

# Device configuration
devices:
  routers: []  # List of router IPs

# Retention configuration
retention:
  daily: 30 # Number of daily backups to keep
  monthly: 12 # Number of monthly backups to keep
  yearly: 5 # Number of yearly backups to keep

# Timezone configuration
timezone: "Europe/Rome"

# ... other Kubernetes-related configurations ...
```

### 3. Systemd Timer

For deployments on a traditional Linux server, you can use a Systemd timer. Create a service file and a timer file.

**Service file (`/etc/systemd/system/mikrotik-backup.service`):**

```ini
[Unit]
Description=Mikrotik Backup Service

[Service]
ExecStart=/usr/bin/python3 /path/to/backup.py # Replace with the actual path
User=root  # Specify the correct user
WorkingDirectory=/path/to/script  # Specify the working directory

[Install]
WantedBy=multi-user.target
```

**Timer file (`/etc/systemd/system/mikrotik-backup.timer`):**

```ini
[Unit]
Description=Mikrotik Backup Timer

[Timer]
OnCalendar=*-*-* 02:00:00  # Backup time
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start the service and timer:

```bash
systemctl enable mikrotik-backup.timer
systemctl start mikrotik-backup.timer
```

## Configuration 📝

The script uses a `config.toml` file for configuration. An example is available in `config.example.toml`. The default location is `/etc/mikrotik_backup.toml`, but can be overridden with the `-f` or `--config` command-line option.

### `config.toml` Structure

```toml
[storage]
type = "wasabi" # Storage type (optional)
bucket = "your-bucket-name"
endpoint = "your-endpoint-url"
access_key = "YOUR_ACCESS_KEY"
secret_key = "YOUR_SECRET_KEY"

[devices]
routers = ["192.168.1.1", "192.168.1.2"] # List of router IPs

[ssh]
username = "backup-user" # SSH username
key_path = "/path/to/your/ssh/key" # Path to the SSH private key

[backup]
local_dir = "/tmp/mikrotik-backups" # Local directory for temporary backups
filename = "backup.rsc" # Binary backup filename on the router
format = "plain" # Backup format: "plain" or "json"
jobs = 4 # Number of parallel jobs

[retention]
daily = 30 # Number of daily backups to retain
monthly = 12 # Number of monthly backups to retain
yearly = 5 # Number of yearly backups to retain

[logging]
level = "info" # Logging level: "debug", "info", "warning", "error"
```

## Creating a Backup User in RouterOS

1. **SSH into your Mikrotik router.**
2. **Create a new user:** `/user add name=backup group=read password=<your-password>` (replace `<your-password>` with a strong password).
3. **Add the user to the `system` group:** `/user group add name=system user=backup`
4. **Verify the user's groups:** `/user print detail where name=backup`

## Command-line Options ⌨️

| Option         | Description                                                                                           |
|-----------------|-------------------------------------------------------------------------------------------------------|
| `-f`, `--config`| Path to the configuration file (default: `/etc/mikrotik_backup.toml`).                              |
| `-d`, `--debug` | Enables debug logging.                                                                               |
| `-m`, `--mode` | Run mode: `once` (default) or `daemon`.                                                              |
| `-t`, `--times`| Times to run backup in daemon mode (24h format, space-separated. Example: "02:00 14:00").            |
| `-k`, `--key`  | Overrides the SSH key path from the configuration file.                                               |
| `-j`, `--jobs`  | Number of parallel jobs (default: from config or 2 * CPU cores).                                     |

## Contributions 🤝

Contributions are welcome! Open an issue or a pull request.

## License 📜

This project is released under the MIT License.

## Acknowledgments 🙏

Thanks to all contributors and maintainers of this project.
