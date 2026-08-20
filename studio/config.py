"""Studio sozlamalari — studio/config.json ga saqlanadi (gitignore).
SSH server ma'lumotlari, standart duration/seed, results papkasi, mavzu."""

import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULTS = {
    "ssh_host": "",
    "ssh_user": "incubation",
    "ssh_pass": "",
    "wsl_distro": "Ubuntu-24.04",
    "remote_repo": "/root/mininet",     # serverdagi repo yo'li
    "default_duration": 300,
    "default_seed": 42,
    "results_dir": "results",
    "theme": "dark",
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH) as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    merged = load_config()
    # faqat ma'lum kalitlarni qabul qilamiz
    for k in DEFAULTS:
        if k in cfg:
            merged[k] = cfg[k]
    with open(_CONFIG_PATH, "w") as f:
        json.dump(merged, f, indent=2)
    return merged
