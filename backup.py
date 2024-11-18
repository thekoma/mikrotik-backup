#! /usr/bin/env python3

import logging
import os
from datetime import datetime
import paramiko
import boto3
import tarfile
from pathlib import Path
from colorama import init, Fore, Style
import tomli
import argparse
import time
import sys
import schedule
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

# Inizializza l'parser degli argomenti
parser = argparse.ArgumentParser(description='MikroTik Backup Tool')
parser.add_argument('-f', '--config', 
                    default='/etc/mikrotik_backup.toml',
                    help='Path to configuration file (default: /etc/mikrotik_backup.toml)')
parser.add_argument('-d', '--debug',
                    action='store_true',
                    help='Enable debug logging')
parser.add_argument('-m', '--mode',
                    choices=['once', 'daemon'],
                    default='once',
                    help='Run mode: once (default) or daemon')
parser.add_argument('-t', '--times',
                    nargs='+',
                    default=['02:00'],
                    help='Times to run backup in daemon mode (24h format, space separated. Example: "02:00 14:00 22:00")')
parser.add_argument('-k', '--key',
                    help='Override SSH key path from config file')
parser.add_argument('-j', '--jobs',
                    type=int,
                    default=None,
                    help='Number of parallel jobs (default: from config or 2 * CPU cores)')
args = parser.parse_args()

# Configura immediatamente il logging se -d è specificato
if args.debug:
    logging.basicConfig(
        level="DEBUG",
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
logger = logging.getLogger(__name__)

init()  # Inizializza colorama

def validate_ssh_key(key_path):
    if not os.path.exists(key_path):
        raise FileNotFoundError(f"SSH key not found: {key_path}")
    try:
        # Prova prima con Ed25519Key
        paramiko.Ed25519Key.from_private_key_file(key_path)
    except Exception as e:
        try:
            # Se fallisce, prova con RSAKey
            paramiko.RSAKey.from_private_key_file(key_path)
        except Exception as e:
            raise ValueError(f"Invalid SSH key: {str(e)}")

# Configurazione default
DEFAULT_CONFIG = {
    "ssh": {
        "username": "backup",
        "key_path": "/root/.ssh/mikrotik_rsa"
    },
    "backup": {
        "local_dir": "/tmp/mikrotik_backups",
        "filename": "backup.rsc",
        "format": "plain"
    },
    "retention": {
        "daily": 30,
        "monthly": 12,
        "yearly": 5
    },
    "logging": {
        "level": "info"
    }
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
        return os.environ.get(env_var, '')
    
    return re.sub(r'\${(\w+)}', replace_env_var, config_str)

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
        raise ValueError("Section 'storage' missing in configuration file")
    
    required_storage_fields = ["bucket", "endpoint", "access_key", "secret_key"]
    missing_fields = [field for field in required_storage_fields 
                     if field not in config["storage"]]
    if missing_fields:
        raise ValueError(f"Missing required fields in 'storage': {', '.join(missing_fields)}")
    
    if "devices" not in config or "routers" not in config["devices"]:
        raise ValueError("Router list not configured in 'devices.routers'")
    
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
    logger.error(f"Errore dettagliato: {str(e)}")
    logger.error(f"Tipo di errore: {type(e)}")
    print(f"Configuration error: {str(e)}")
    exit(1)

# Configurazione logging
log_level = "DEBUG" if args.debug else config["logging"]["level"].upper()
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

logger.debug("Configurazione caricata e valida con successo")

# Sostituisci le variabili d'ambiente con la configurazione TOML
SSH_USERNAME = config["ssh"]["username"]
SSH_KEY_PATH = args.key if args.key else config["ssh"]["key_path"]
BACKUP_DIR = config["backup"]["local_dir"]
BACKUP_FILENAME = config["backup"]["filename"]
BACKUP_FORMAT = config["backup"]["format"]

S3_BUCKET_NAME = config["storage"]["bucket"]
WASABI_ACCESS_KEY_ID = config["storage"]["access_key"]
WASABI_SECRET_ACCESS_KEY = config["storage"]["secret_key"]
WASABI_ENDPOINT_URL = config["storage"]["endpoint"]

ROUTER_IPS = config["devices"]["routers"]

RETENTION_DAILY = str(config["retention"]["daily"])
RETENTION_MONTHLY = str(config["retention"]["monthly"])
RETENTION_YEARLY = str(config["retention"]["yearly"])

logger.info(f"Configured Router IPs: {ROUTER_IPS}")

# Aggiungi queste variabili di ritenzione
RETENTION_DAILY = str(config["retention"]["daily"])
RETENTION_MONTHLY = str(config["retention"]["monthly"])
RETENTION_YEARLY = str(config["retention"]["yearly"])

logger.info(f"Selected backup format: {BACKUP_FORMAT}")

logger.debug("Environment variables loaded correctly")

# Create backup directory if it doesn't exist
os.makedirs(BACKUP_DIR, exist_ok=True)
logger.debug(f"Backup directory created: {BACKUP_DIR}")

# Crea un client S3 per Wasabi
s3 = boto3.client(
    's3',
    endpoint_url=WASABI_ENDPOINT_URL,
    aws_access_key_id=WASABI_ACCESS_KEY_ID,
    aws_secret_access_key=WASABI_SECRET_ACCESS_KEY
)
logger.debug("Client S3 initialized")

# Funzione per normalizzare il nome del dispositivo
def normalize_name(name):
  return name.lower().replace(".", "_").strip()

# Funzione per scaricare il backup da un router
def download_backup(hostname, ip):
    try:
        logger.info(f"{Fore.BLUE}🔄 Starting backup from {ip}{Style.RESET_ALL}")
        
        # Crea una nuova connessione SSH per questo thread
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh.connect(
                hostname=ip, 
                username=SSH_USERNAME, 
                key_filename=SSH_KEY_PATH, 
                allow_agent=False, 
                look_for_keys=False
            )
            
            # Ottieni il nome del dispositivo
            stdin, stdout, stderr = ssh.exec_command('/system identity print without-paging')
            device_name = stdout.readlines()[0].strip().split(':')[1].strip()
            logger.info(f"{Fore.CYAN}📱 Device name: {device_name}{Style.RESET_ALL}")
            
            # Ottieni il numero seriale
            stdin, stdout, stderr = ssh.exec_command('/system routerboard print')
            output = stdout.readlines()
            serial_number = None
            for line in output:
                if 'serial-number' in line.lower():
                    serial_number = line.split(':')[1].strip()
                    break
            
            if not serial_number:
                logger.warning(f"{Fore.YELLOW}⚠️ Unable to get serial number, using device name only{Style.RESET_ALL}")
                device_id = normalize_name(device_name)
            else:
                logger.info(f"{Fore.CYAN}🔢 Serial number: {serial_number}{Style.RESET_ALL}")
                device_id = f"{normalize_name(device_name)}_{serial_number}"
            
            # Prepara il comando di backup in base al formato
            if BACKUP_FORMAT == 'json':
                backup_command = '/export json'
                file_extension = 'json'
            else:
                backup_command = '/export'
                file_extension = 'rsc'
            
            # Esegui il comando di backup
            logger.info(f"{Fore.CYAN}⚙️ Esecuzione comando: {backup_command}{Style.RESET_ALL}")
            stdin, stdout, stderr = ssh.exec_command(backup_command)
            backup_content = stdout.read().decode()
            
            # Salva il backup localmente
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            local_filename = os.path.join(BACKUP_DIR, f"{timestamp}_{device_id}.{file_extension}")
            
            with open(local_filename, 'w') as f:
                f.write(backup_content)
            
            file_size = os.path.getsize(local_filename)
            logger.info(f"{Fore.GREEN}💾 Configuration backup saved: {local_filename} ({file_size/1024:.2f} KB){Style.RESET_ALL}")
            
            # Scarica anche il backup binario
            binary_backup_filename = os.path.join(BACKUP_DIR, f"{timestamp}_{device_id}.backup")
            ftp_client = ssh.open_sftp()
            ftp_client.get(BACKUP_FILENAME, binary_backup_filename)
            ftp_client.close()
            
            binary_size = os.path.getsize(binary_backup_filename)
            logger.info(f"{Fore.GREEN}💾 Binary backup saved: {binary_backup_filename} ({binary_size/1024:.2f} KB){Style.RESET_ALL}")
            
            return [local_filename, binary_backup_filename]
            
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
        daily_objects = s3_client.list_objects_v2(
            Bucket=bucket_name,
            Prefix="backups/daily/"
        ).get('Contents', [])
        if len(daily_objects) > int(RETENTION_DAILY):
            for obj in sorted(daily_objects, key=lambda x: x['LastModified'])[:-int(RETENTION_DAILY)]:
                s3_client.delete_object(Bucket=bucket_name, Key=obj['Key'])
                logger.info(f"{Fore.YELLOW}🗑️  Deleted old daily backup: {obj['Key']}{Style.RESET_ALL}")
        
        # Handle monthly backup
        if day == "01":
            monthly_key = f"backups/monthly/{year}/{month}/{base_name}"
            logger.info(f"{Fore.BLUE}📅 Uploading monthly backup: {monthly_key}{Style.RESET_ALL}")
            s3_client.upload_file(backup_file, bucket_name, monthly_key)
            
            monthly_objects = s3_client.list_objects_v2(
                Bucket=bucket_name,
                Prefix="backups/monthly/"
            ).get('Contents', [])
            if len(monthly_objects) > int(RETENTION_MONTHLY):
                for obj in sorted(monthly_objects, key=lambda x: x['LastModified'])[:-int(RETENTION_MONTHLY)]:
                    s3_client.delete_object(Bucket=bucket_name, Key=obj['Key'])
                    logger.info(f"{Fore.YELLOW}🗑️  Deleted old monthly backup: {obj['Key']}{Style.RESET_ALL}")
        
        # Handle yearly backup
        if day == "01" and month == "01":
            yearly_key = f"backups/yearly/{year}/{base_name}"
            logger.info(f"{Fore.GREEN}📆 Uploading yearly backup: {yearly_key}{Style.RESET_ALL}")
            s3_client.upload_file(backup_file, bucket_name, yearly_key)
            
            yearly_objects = s3_client.list_objects_v2(
                Bucket=bucket_name,
                Prefix="backups/yearly/"
            ).get('Contents', [])
            if len(yearly_objects) > int(RETENTION_YEARLY):
                for obj in sorted(yearly_objects, key=lambda x: x['LastModified'])[:-int(RETENTION_YEARLY)]:
                    s3_client.delete_object(Bucket=bucket_name, Key=obj['Key'])
                    logger.info(f"{Fore.YELLOW}🗑️  Deleted old yearly backup: {obj['Key']}{Style.RESET_ALL}")
            
        logger.info(f"{Fore.GREEN}✅ Backup rotation completed successfully{Style.RESET_ALL}")
    except Exception as e:
        logger.error(f"{Fore.RED}❌ Error during backup rotation: {str(e)}{Style.RESET_ALL}")
        raise

# Sposta questa funzione prima del blocco try principale
def get_backup_statistics(s3_client, bucket_name):
    try:
        stats = {
            'daily': {'count': 0, 'dates': []},
            'monthly': {'count': 0, 'dates': []},
            'yearly': {'count': 0, 'dates': []}
        }
        
        for backup_type in ['daily', 'monthly', 'yearly']:
            response = s3_client.list_objects_v2(
                Bucket=bucket_name,
                Prefix=f"backups/{backup_type}/"
            )
            
            if 'Contents' in response:
                objects = sorted(response['Contents'], 
                               key=lambda x: x['LastModified'],
                               reverse=True)
                
                stats[backup_type]['count'] = len(objects)
                # Prendi le date dai primi 5 backup per ogni tipo
                for obj in objects[:5]:
                    date_str = obj['LastModified'].strftime('%Y-%m-%d %H:%M:%S')
                    stats[backup_type]['dates'].append(date_str)
        
        print("\n=== Backup Statistics ===")
        print(f"\nDaily Backups:")
        print(f"Total: {stats['daily']['count']}")
        print("Last 5 backups:")
        for date in stats['daily']['dates']:
            print(f"  - {date}")
            
        print(f"\nMonthly Backups:")
        print(f"Total: {stats['monthly']['count']}")
        print("Last 5 backups:")
        for date in stats['monthly']['dates']:
            print(f"  - {date}")
            
        print(f"\nYearly Backups:")
        print(f"Total: {stats['yearly']['count']}")
        print("Last 5 backups:")
        for date in stats['yearly']['dates']:
            print(f"  - {date}")
            
        print("\nRetention Summary:")
        print(f"- Keeping {RETENTION_DAILY} daily backups")
        print(f"- Keeping {RETENTION_MONTHLY} monthly backups")
        print(f"- Keeping {RETENTION_YEARLY} yearly backups")
        print("========================\n")
        
    except Exception as e:
        print(f"Error while retrieving statistics: {e}")

def get_config_value(config_dict, *keys, env_var=None, required=True):
    """
    Get configuration value from nested dict, falling back to environment variable.
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
    
    # If required and still not found, raise error
    if value is None and required:
        config_path = '.'.join(keys)
        error_msg = f"Required configuration '{config_path}' not found in config file"
        if env_var:
            error_msg += f" or environment variable {env_var}"
        raise ValueError(error_msg)
    
    return value

def main():
    try:
        all_backup_files = []
        logger.info(f"Starting backup process with {args.jobs} parallel jobs")
        
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            future_to_ip = {
                executor.submit(download_backup, ip, ip): ip 
                for ip in ROUTER_IPS
            }
            
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
                logger.info(f"{Fore.CYAN}  - {os.path.basename(filename)} ({file_size/1024:.2f} KB){Style.RESET_ALL}")
            
            with tarfile.open(tar_filename, "w:gz") as tar:
                for filename in all_backup_files:
                    tar.add(filename, arcname=os.path.basename(filename))
            
            archive_size = os.path.getsize(tar_filename)
            logger.info(f"{Fore.GREEN}✅ Archive created: {tar_filename}{Style.RESET_ALL}")
            logger.info(f"{Fore.GREEN}📊 Total files size: {total_size/1024:.2f} KB{Style.RESET_ALL}")
            logger.info(f"{Fore.GREEN}📊 Compressed archive size: {archive_size/1024:.2f} KB{Style.RESET_ALL}")
            
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
    if args.mode == 'daemon':
        logger.info(f"Running in daemon mode, scheduled for: {', '.join(args.times)}")
        
        for backup_time in args.times:
            schedule.every().day.at(backup_time).do(main)
            logger.debug(f"Scheduled backup for {backup_time}")
        
        while True:
            try:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
            except KeyboardInterrupt:
                logger.info("Received shutdown signal, exiting...")
                sys.exit(0)
    else:
        # Single run mode for cron/systemd/k8s
        main()
