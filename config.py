"""Umumiy konfiguratsiya — barcha modullar shu yerdan import qiladi."""

import os

DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR", "/data"))
CONTROLLER_PORT = 6633
