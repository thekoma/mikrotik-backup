#! /usr/bin/env python3

import argparse
import logging
import multiprocessing
import os
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import boto3
import paramiko
import schedule
import tomli
from colorama import Fore, Style, init

# Error messages
ERROR_MESSAGES = {
    "CONFIG_NOT_FOUND": "Configuration file not found: {}",
    "STORAGE_SECTION_MISSING": "Section 'storage' missing in configuration file",
    "MISSING_STORAGE_FIELDS": "Missing required fields in 'storage': {}",
    "ROUTER_LIST_MISSING": "Router list not configured in 'devices.routers'",
    "SSH_KEY_NOT_FOUND": "SSH key not found: {}",
    "INVALID_SSH_KEY": "Invalid SSH key: {}",
    "EXPORT_FAILED": "Export command failed with status {}: {}",
    "BACKUP_NOT_CREATED": "Backup file {} was not created on the router",
    "INVALID_ROUTER_LIST": "devices.routers must be a non-empty list of IP addresses",
    "CONFIG_VALUE_NOT_FOUND": "Required configuration '{}' not found in config file{}",
    "CONFIG_EXTRACTION_ERROR": "Error extracting configuration: {}",
    "SCHEDULER_ERROR": "Error in scheduler: {}",
}

# Inizializza l'parser degli argomenti
parser = argparse.ArgumentParser(description="MikroTik Backup Tool")
parser.add_argument(
    "-f",
    "--config",
    default="/etc/mikrotik_backup.toml",
    help="Path to configuration file (default: /etc/mikrotik_backup.toml)",
)
parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
parser.add_argument(
    "-m",
    "--mode",
    choices=["once", "daemon"],
    default="once",
    help="Run mode: once (default) or daemon",
)
parser.add_argument(
    "-t",
    "--times",
    nargs="+",
    default=["02:00"],
    help='Times to run backup in daemon mode (24h format, space separated. Example: "02:00 14:00 22:00")',
)
parser.add_argument("-k", "--key", help="Override SSH key path from config file")
parser.add_argument(
    "-j",
    "--jobs",
    type=int,
    default=None,
    help="Number of parallel jobs (default: from config or 2 * CPU cores)",
)
parser.add_argument(
    "--onstart", action="store_true", help="In daemon mode, execute a backup immediately on startup"
)
args = parser.parse_args()

# Configura immediatamente il logging se -d è specificato
if args.debug:
    logging.basicConfig(
        level="DEBUG",
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
logger = logging.getLogger(__name__)

init()  # Inizializza colorama

# Dopo gli import, aggiungiamo le definizioni dei colori e delle emoji
LOG_COLORS = {
    "DEBUG": Style.DIM + Fore.WHITE,  # Grigio chiaro
    "INFO": Style.NORMAL + Fore.WHITE,  # Bianco normale
    "WARNING": Style.NORMAL + "\033[38;5;208m",  # Arancione soft
    "ERROR": Style.NORMAL + "\033[38;5;196m",  # Rosso soft
    "CRITICAL": Style.BRIGHT + "\033[38;5;196m",  # Rosso brillante
}

LOG_EMOJI = {
    "DEBUG": "🔍",
    "INFO": "📝",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "💥",
}


class ColoredFormatter(logging.Formatter):
    def format(self, record):
        # Salva il messaggio originale
        original_msg = record.msg

        # Aggiungi emoji e colore
        color = LOG_COLORS.get(record.levelname, Fore.WHITE)
        emoji = LOG_EMOJI.get(record.levelname, "")

        # Formatta il messaggio con emoji e colore
        record.msg = f"{color}{emoji} {original_msg}{Style.RESET_ALL}"

        # Chiama il formatter originale
        result = super().format(record)

        # Ripristina il messaggio originale
        record.msg = original_msg
        return result


# Modifica la configurazione del logging
def setup_logging(level):
    formatter = ColoredFormatter(
        fmt="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.setLevel(level)

    # Rimuovi gli handler esistenti
    for h in logger.handlers:
        logger.removeHandler(h)

    logger.addHandler(handler)
    return logger


def validate_ssh_key(key_path):
    if not os.path.exists(key_path):
        raise FileNotFoundError(f"SSH key not found: {key_path}")
    try:
        # Prova prima con Ed25519Key
        paramiko.Ed25519Key.from_private_key_file(key_path)
    except Exception:
        try:
            # Se fallisce, prova con RSAKey
            paramiko.RSAKey.from_private_key_file(key_path)
        except Exception as e:
            raise ValueError(f"Invalid SSH key: {str(e)}")


# Configurazione default
DEFAULT_CONFIG = {
    "ssh": {"username": "backup", "key_path": "/root/.ssh/mikrotik_rsa"},
    "backup": {"local_dir": "/tmp/mikrotik_backups", "filename": "backup.rsc", "format": "plain"},
    "retention": {"daily": 30, "monthly": 12, "yearly": 5},
    "logging": {"level": "info"},
}

# Aggiungi questi log prima del try di caricamento config
logger = logging.getLogger(__name__)
logger.debug(f"Tentativo di apertura del file di configurazione: {args.config}")
logger.debug(f"Il file esiste? {os.path.exists(args.config)}")
logger.debug(f"Path assoluto: {os.path.abspath(args.config)}")
logger.debug(f"Directory corrente: {os.getcwd()}")


# Funzione per espandere le variabili d'ambiente nel contenuto del file
def expand_env_vars(config_str):
    """Expand environment variables in config string."""
    import os
    import re

    def replace_env_var(match):
        env_var = match.group(1)
        return os.environ.get(env_var, "")

    return re.sub(r"\${(\w+)}", replace_env_var, config_str)


# Carica e valida la configurazione
try:
    logger.debug(f"Permessi file: {oct(os.stat(args.config).st_mode)[-3:]}")
    logger.debug(f"Proprietario: {os.stat(args.config).st_uid}")

    with open(args.config, "rb") as f:
        config_str = f.read().decode()
        # Espandi le variabili d'ambiente nel contenuto del file
        config_str = expand_env_vars(config_str)
        config = tomli.loads(config_str)
        logger.debug("File caricato con tomli")

    # Merge con i default
    for section in DEFAULT_CONFIG:
        if section not in config:
            config[section] = {}
        for key, value in DEFAULT_CONFIG[section].items():
            config[section].setdefault(key, value)

    # Validazione campi obbligatori
    if "storage" not in config:
        raise ValueError(ERROR_MESSAGES["STORAGE_SECTION_MISSING"])

    required_storage_fields = ["bucket", "endpoint", "access_key", "secret_key"]
    missing_fields = [field for field in required_storage_fields if field not in config["storage"]]
    if missing_fields:
        raise ValueError(f"Missing required fields in 'storage': {', '.join(missing_fields)}")

    if "devices" not in config or "routers" not in config["devices"]:
        raise ValueError(ERROR_MESSAGES["ROUTER_LIST_MISSING"])

    # Validazione chiave SSH
    try:
        # Usa args.key se specificato, altrimenti usa il valore dal config
        ssh_key_to_validate = args.key if args.key else config["ssh"]["key_path"]
        logger.debug(f"Using SSH key path: {ssh_key_to_validate}")
        validate_ssh_key(ssh_key_to_validate)
    except FileNotFoundError as e:
        logger.error(f"SSH key error: {str(e)}")
        print(f"Error: SSH key not found: {ssh_key_to_validate}")
        exit(1)

except FileNotFoundError as e:
    logger.error(f"FileNotFoundError dettagliato: {str(e)}")
    print(f"Error: Configuration file not found: {args.config}")
    exit(1)
except Exception as e:
    logger.error(ERROR_MESSAGES["CONFIG_EXTRACTION_ERROR"].format(str(e)))
    sys.exit(1)

# Configurazione logging
logger = setup_logging("DEBUG" if args.debug else config["logging"]["level"].upper())

logger.debug("Configurazione caricata e valida con successo")


def get_config_value(config_dict, *keys, env_var=None, required=True, default=None):
    """
    Get configuration value from nested dict, falling back to environment variable.
    If not found and not required, returns default value.
    """
    # Try to get from config
    value = config_dict
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            value = None
            break

    # If not in config, try environment
    if value is None and env_var:
        value = os.environ.get(env_var)

    # If still not found, use default if not required
    if value is None and not required:
        value = default

    # If required and still not found, raise error
    if value is None and required:
        config_path = ".".join(keys)
        env_var_msg = f" or environment variable {env_var}" if env_var else ""
        error_msg = ERROR_MESSAGES["CONFIG_VALUE_NOT_FOUND"].format(config_path, env_var_msg)
        raise ValueError(error_msg)

    return value


# Estrai e valida le variabili di configurazione
try:
    # SSH settings
    SSH_USERNAME = get_config_value(config, "ssh", "username", env_var="MIKROTIK_SSH_USER")
    SSH_KEY_PATH = args.key if args.key else get_config_value(config, "ssh", "key_path")

    # Storage settings
    S3_TYPE = get_config_value(config, "storage", "type", env_var="MIKROTIK_S3_TYPE")
    S3_BUCKET_NAME = get_config_value(config, "storage", "bucket", env_var="MIKROTIK_S3_BUCKET")
    S3_ENDPOINT_URL = get_config_value(
        config, "storage", "endpoint", env_var="MIKROTIK_S3_ENDPOINT"
    )
    S3_ACCESS_KEY = get_config_value(
        config, "storage", "access_key", env_var="MIKROTIK_S3_ACCESS_KEY"
    )
    S3_SECRET_KEY = get_config_value(
        config, "storage", "secret_key", env_var="MIKROTIK_S3_SECRET_KEY"
    )

    # Backup settings
    BACKUP_DIR = get_config_value(config, "backup", "local_dir", default="/tmp/mikrotik_backups")
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Device settings
    ROUTER_IPS = get_config_value(config, "devices", "routers")
    if not isinstance(ROUTER_IPS, list) or not ROUTER_IPS:
        raise ValueError(ERROR_MESSAGES["INVALID_ROUTER_LIST"])

    # Retention settings
    RETENTION_DAILY = get_config_value(config, "retention", "daily", default=30)
    RETENTION_MONTHLY = get_config_value(config, "retention", "monthly", default=12)
    RETENTION_YEARLY = get_config_value(config, "retention", "yearly", default=5)

    # Jobs setting
    if args.jobs:
        BACKUP_JOBS = args.jobs
    else:
        BACKUP_JOBS = get_config_value(
            config, "backup", "jobs", default=multiprocessing.cpu_count() * 2
        )

    logger.info("Configuration loaded successfully:")
    logger.info(f"- Backup directory: {BACKUP_DIR}")
    logger.info(f"- Configured routers: {', '.join(ROUTER_IPS)}")
    logger.info(f"- Parallel jobs: {BACKUP_JOBS}")
    logger.info(f"- Retention policy: {RETENTION_DAILY}d/{RETENTION_MONTHLY}m/{RETENTION_YEARLY}y")

except Exception as e:
    logger.error(ERROR_MESSAGES["CONFIG_EXTRACTION_ERROR"].format(str(e)))
    sys.exit(1)

# Inizializza il client S3
s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT_URL,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
)
logger.debug("Client S3 initialized")


# Funzione per normalizzare il nome del dispositivo
def normalize_name(name):
    return name.lower().replace(".", "_").strip()


# Funzione per scaricare il backup da un router
def download_backup(hostname, ip):
    try:
        logger.info(f"{Fore.BLUE}🔄 Starting backup from {ip}{Style.RESET_ALL}")

        ssh = paramiko.SSHClient()
        # nosec: We trust our internal network and router fingerprints
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # nosec

        try:
            ssh.connect(
                hostname=ip,
                username=SSH_USERNAME,
                key_filename=SSH_KEY_PATH,
                allow_agent=False,
                look_for_keys=False,
            )

            # Ottieni il nome del dispositivo
            stdin, stdout, stderr = ssh.exec_command("/system identity print without-paging")
            device_name = stdout.readlines()[0].strip().split(":")[1].strip()
            logger.info(f"{Fore.CYAN}📱 Device name: {device_name}{Style.RESET_ALL}")

            # Ottieni il numero seriale
            stdin, stdout, stderr = ssh.exec_command("/system routerboard print")
            output = stdout.readlines()
            serial_number = None
            for line in output:
                if "serial-number" in line.lower():
                    serial_number = line.split(":")[1].strip()
                    break

            if not serial_number:
                logger.warning(
                    f"{Fore.YELLOW}⚠️ Unable to get serial number, using device name only{Style.RESET_ALL}"
                )
                device_id = normalize_name(device_name)
            else:
                logger.info(f"{Fore.CYAN}🔢 Serial number: {serial_number}{Style.RESET_ALL}")
                device_id = f"{normalize_name(device_name)}_{serial_number}"

            # Genera un nome file casuale per il backup sul router
            import uuid

            temp_filename = f"backup_{uuid.uuid4().hex}.rsc"

            # Esegui il comando di backup e aspetta che finisca
            backup_command = f"/export show-sensitive file={temp_filename}"
            logger.info(f"{Fore.CYAN}⚙️ Executing command: {backup_command}{Style.RESET_ALL}")
            stdin, stdout, stderr = ssh.exec_command(backup_command)

            # Aspetta che il comando finisca e controlla l'exit status
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                error_output = stderr.read().decode()
                raise Exception(f"Export command failed with status {exit_status}: {error_output}")

            # Aspetta un momento per essere sicuri che il file sia stato scritto
            time.sleep(2)

            # Verifica che il file esista
            stdin, stdout, stderr = ssh.exec_command(
                f'file print detail where name="{temp_filename}"'
            )
            if not stdout.read().decode().strip():
                raise Exception(f"Backup file {temp_filename} was not created on the router")

            # Scarica il file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            local_filename = os.path.join(BACKUP_DIR, f"{timestamp}_{device_id}.rsc")

            ftp_client = ssh.open_sftp()
            ftp_client.get(temp_filename, local_filename)
            ftp_client.close()

            # Elimina il file temporaneo dal router
            ssh.exec_command(f'file remove "{temp_filename}"')

            file_size = os.path.getsize(local_filename)
            logger.info(
                f"{Fore.GREEN}💾 Backup saved: {local_filename} ({file_size/1024:.2f} KB){Style.RESET_ALL}"
            )

            return [local_filename]

        finally:
            ssh.close()

    except Exception as e:
        logger.error(f"{Fore.RED}❌ Error during backup from {ip}: {str(e)}{Style.RESET_ALL}")
        return None


def manage_backup_rotation(s3_client, bucket_name, backup_file):
    try:
        today = datetime.now()
        year = today.strftime("%Y")
        month = today.strftime("%m")
        day = today.strftime("%d")

        base_name = os.path.basename(backup_file)

        # Upload daily backup
        daily_key = f"backups/daily/{year}/{month}/{day}/{base_name}"
        logger.info(f"{Fore.CYAN}📁 Uploading daily backup: {daily_key}{Style.RESET_ALL}")
        s3_client.upload_file(backup_file, bucket_name, daily_key)

        # Elimina i backup giornalieri più vecchi
        daily_objects = s3_client.list_objects_v2(Bucket=bucket_name, Prefix="backups/daily/").get(
            "Contents", []
        )
        if len(daily_objects) > int(RETENTION_DAILY):
            for obj in sorted(daily_objects, key=lambda x: x["LastModified"])[
                : -int(RETENTION_DAILY)
            ]:
                s3_client.delete_object(Bucket=bucket_name, Key=obj["Key"])
                logger.info(
                    f"{Fore.YELLOW}️  Deleted old daily backup: {obj['Key']}{Style.RESET_ALL}"
                )

        # Handle monthly backup
        if day == "01":
            monthly_key = f"backups/monthly/{year}/{month}/{base_name}"
            logger.info(f"{Fore.BLUE}📅 Uploading monthly backup: {monthly_key}{Style.RESET_ALL}")
            s3_client.upload_file(backup_file, bucket_name, monthly_key)

            monthly_objects = s3_client.list_objects_v2(
                Bucket=bucket_name, Prefix="backups/monthly/"
            ).get("Contents", [])
            if len(monthly_objects) > int(RETENTION_MONTHLY):
                for obj in sorted(monthly_objects, key=lambda x: x["LastModified"])[
                    : -int(RETENTION_MONTHLY)
                ]:
                    s3_client.delete_object(Bucket=bucket_name, Key=obj["Key"])
                    logger.info(
                        f"{Fore.YELLOW}🗑️  Deleted old monthly backup: {obj['Key']}{Style.RESET_ALL}"
                    )

        # Handle yearly backup
        if day == "01" and month == "01":
            yearly_key = f"backups/yearly/{year}/{base_name}"
            logger.info(f"{Fore.GREEN}📆 Uploading yearly backup: {yearly_key}{Style.RESET_ALL}")
            s3_client.upload_file(backup_file, bucket_name, yearly_key)

            yearly_objects = s3_client.list_objects_v2(
                Bucket=bucket_name, Prefix="backups/yearly/"
            ).get("Contents", [])
            if len(yearly_objects) > int(RETENTION_YEARLY):
                for obj in sorted(yearly_objects, key=lambda x: x["LastModified"])[
                    : -int(RETENTION_YEARLY)
                ]:
                    s3_client.delete_object(Bucket=bucket_name, Key=obj["Key"])
                    logger.info(
                        f"{Fore.YELLOW}🗑️  Deleted old yearly backup: {obj['Key']}{Style.RESET_ALL}"
                    )

        logger.info(f"{Fore.GREEN}✅ Backup rotation completed successfully{Style.RESET_ALL}")
    except Exception as e:
        logger.error(f"{Fore.RED}❌ Error during backup rotation: {str(e)}{Style.RESET_ALL}")
        raise


# Sposta questa funzione prima del blocco try principale
def get_backup_statistics(s3_client, bucket_name):
    try:
        stats = {
            "daily": {"count": 0, "dates": [], "sizes": []},
            "monthly": {"count": 0, "dates": [], "sizes": []},
            "yearly": {"count": 0, "dates": [], "sizes": []},
        }

        total_size = 0
        # Raccolta statistiche
        for backup_type in ["daily", "monthly", "yearly"]:
            response = s3_client.list_objects_v2(
                Bucket=bucket_name, Prefix=f"backups/{backup_type}/"
            )

            if "Contents" in response:
                objects = sorted(
                    response["Contents"], key=lambda x: x["LastModified"], reverse=True
                )

                stats[backup_type]["count"] = len(objects)
                for obj in objects[:5]:
                    date_str = obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S")
                    size_mb = obj["Size"] / (1024 * 1024)
                    total_size += obj["Size"]
                    stats[backup_type]["dates"].append(date_str)
                    stats[backup_type]["sizes"].append(size_mb)

        # Stampa statistiche
        print(f"\n{Fore.CYAN}╔{'═' * 58}╗")
        print(f"║{Fore.YELLOW}{'📊 Backup Statistics Overview':^58}{Fore.CYAN}║")
        print(f"╠{'═' * 58}╣")

        # Daily backups
        print(f"║{Fore.GREEN}{'📆 Daily Backups':^58}{Fore.CYAN}║")
        print(f"║{Fore.WHITE}{f'Total: {stats['daily']['count']} backups':^58}{Fore.CYAN}║")
        if stats["daily"]["dates"]:
            print(f"║{Fore.WHITE}{'Recent backups:':^58}{Fore.CYAN}║")
            for date, size in zip(stats["daily"]["dates"], stats["daily"]["sizes"]):
                print(f"║{Fore.YELLOW}{f'→ {date} ({size:.2f} MB)':^58}{Fore.CYAN}║")

        # Monthly backups
        print(f"╠{'═' * 58}╣")
        print(f"║{Fore.GREEN}{'📅 Monthly Backups':^58}{Fore.CYAN}║")
        print(f"║{Fore.WHITE}{f'Total: {stats['monthly']['count']} backups':^58}{Fore.CYAN}║")

        # Yearly backups
        print(f"╠{'═' * 58}╣")
        print(f"║{Fore.GREEN}{'📆 Yearly Backups':^58}{Fore.CYAN}║")
        print(f"║{Fore.WHITE}{f'Total: {stats['yearly']['count']} backups':^58}{Fore.CYAN}║")

        # Retention policy
        print(f"╠{'═' * 58}╣")
        print(f"║{Fore.YELLOW}{'⚙️ Retention Policy':^58}{Fore.CYAN}║")
        print(f"║{Fore.WHITE}{f'• Keeping {RETENTION_DAILY} daily backups':^58}{Fore.CYAN}║")
        print(f"║{Fore.WHITE}{f'• Keeping {RETENTION_MONTHLY} monthly backups':^58}{Fore.CYAN}║")
        print(f"║{Fore.WHITE}{f'• Keeping {RETENTION_YEARLY} yearly backups':^58}{Fore.CYAN}║")

        # Total storage
        print(f"╠{'═' * 58}╣")
        total_mb = total_size / (1024 * 1024)
        print(f"║{Fore.YELLOW}{f'💾 Total Storage Used: {total_mb:.2f} MB':^58}{Fore.CYAN}║")
        print(f"╚{'═' * 58}╝\n")

    except Exception as e:
        logger.error(f"{Fore.RED}❌ Error while retrieving statistics: {str(e)}{Style.RESET_ALL}")


def main():
    try:
        all_backup_files = []
        logger.info(f"Starting backup process with {args.jobs} parallel jobs")

        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            future_to_ip = {executor.submit(download_backup, ip, ip): ip for ip in ROUTER_IPS}

            for future in as_completed(future_to_ip):
                ip = future_to_ip[future]
                try:
                    backup_files = future.result()
                    if backup_files:
                        all_backup_files.extend(backup_files)
                        logger.info(f"Completed backup for {ip}")
                    else:
                        logger.error(f"Failed to backup {ip}")
                except Exception as e:
                    logger.error(f"Backup failed for {ip}: {str(e)}")

        # Resto del codice per l'archivio e upload
        if all_backup_files:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            tar_filename = f"{BACKUP_DIR}/{timestamp}_mikrotik_backups.tar.gz"

            logger.info(f"{Fore.CYAN}📦 Creating archive: {tar_filename}{Style.RESET_ALL}")
            logger.info(f"{Fore.CYAN}📋 Files included in archive:{Style.RESET_ALL}")
            total_size = 0
            for filename in all_backup_files:
                file_size = os.path.getsize(filename)
                total_size += file_size
                logger.info(
                    f"{Fore.CYAN}  - {os.path.basename(filename)} ({file_size/1024:.2f} KB){Style.RESET_ALL}"
                )

            with tarfile.open(tar_filename, "w:gz") as tar:
                for filename in all_backup_files:
                    tar.add(filename, arcname=os.path.basename(filename))

            archive_size = os.path.getsize(tar_filename)
            logger.info(f"{Fore.GREEN}✅ Archive created: {tar_filename}{Style.RESET_ALL}")
            logger.info(
                f"{Fore.GREEN}📊 Total files size: {total_size/1024:.2f} KB{Style.RESET_ALL}"
            )
            logger.info(
                f"{Fore.GREEN}📊 Compressed archive size: {archive_size/1024:.2f} KB{Style.RESET_ALL}"
            )

            manage_backup_rotation(s3, S3_BUCKET_NAME, tar_filename)
            get_backup_statistics(s3, S3_BUCKET_NAME)

            # Pulizia file locali
            logger.info("Cleaning up local files")
            for file in all_backup_files:
                os.remove(file)
            os.remove(tar_filename)
        else:
            logger.warning("No backups downloaded")

    except Exception as e:
        logger.error(f"Error during backup execution: {str(e)}")


if __name__ == "__main__":
    if args.mode == "daemon":
        logger.info(f"Running in daemon mode, scheduled for: {', '.join(args.times)}")

        for backup_time in args.times:
            schedule.every().day.at(backup_time).do(main)
            logger.debug(f"Scheduled backup for {backup_time}")

        if args.onstart:
            logger.info("Executing initial backup as requested by --onstart")
            main()

        while True:
            try:
                # Ottieni il prossimo job schedulato
                next_job = schedule.next_run()
                if next_job:
                    next_run = next_job.strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(
                        f"{Fore.CYAN}⏰ Next backup scheduled for: {next_run}{Style.RESET_ALL}"
                    )

                schedule.run_pending()
                time.sleep(60)  # Check every minute
            except KeyboardInterrupt:
                logger.info("Received shutdown signal, exiting...")
                sys.exit(0)
            except Exception as e:
                logger.error(ERROR_MESSAGES["SCHEDULER_ERROR"].format(str(e)))
                time.sleep(60)  # In caso di errore, aspetta comunque un minuto
    else:
        # Single run mode for cron/systemd/k8s
        main()
