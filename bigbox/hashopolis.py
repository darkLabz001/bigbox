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
        # Note: This is a placeholder for the specific Hashopolis / Hashtopolis API
        # Typically involves an upload to a task or a pre-configured VOUCHER
        files = {'file': open(pcapng_path, 'rb')}
        headers = {'X-API-KEY': config.api_key}
        response = requests.post(f"{config.api_url}/api/v1/hashes", files=files, headers=headers, timeout=30)
        return response.status_code == 200
    except Exception:
        return False
