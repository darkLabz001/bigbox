import json
import requests
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

CONFIG_PATH = Path("/etc/bigbox/hashopolis.json")

@dataclass
class HashopolisConfig:
    api_url: str
    api_key: str

def get_config() -> Optional[HashopolisConfig]:
    if not CONFIG_PATH.exists():
        return None
    try:
        with CONFIG_PATH.open("r") as f:
            data = json.load(f)
            return HashopolisConfig(api_url=data["api_url"], api_key=data["api_key"])
    except Exception:
        return None

def upload_hash(pcapng_path: Path) -> bool:
    config = get_config()
    if not config:
        return False
    
    try:
        # Hashtopolis v1 API uses 'hashes' endpoint with specific task IDs or a VOUCHER
        # If we have a 'task_id' in config, we use that.
        url = f"{config.api_url}/api/v1/hashes"
        
        # Determine hash type based on extension
        hash_type = 22000 if pcapng_path.suffix == ".hc22000" else 2500
        
        data = {
            "hashTypeId": hash_type,
            "name": pcapng_path.name,
        }
        
        files = {
            'file': (pcapng_path.name, pcapng_path.open('rb'), 'application/octet-stream')
        }
        
        headers = {
            'X-API-KEY': config.api_key,
            'Accept': 'application/json'
        }
        
        response = requests.post(url, data=data, files=files, headers=headers, timeout=60)
        
        if response.status_code == 200:
            return True
        else:
            print(f"[hashopolis] upload failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[hashopolis] error: {e}")
        return False
