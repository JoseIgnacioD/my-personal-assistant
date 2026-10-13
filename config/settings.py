"""Central configuration, read from .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

# Model profile from config/models.yaml (empty = the file's default_profile)
MODEL_PROFILE = os.getenv("MODEL_PROFILE", "")

N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://localhost:5678/webhook").rstrip("/")
N8N_SECRET = os.getenv("N8N_SECRET", "")

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8001"))
MCP_URL = f"http://{MCP_HOST}:{MCP_PORT}/mcp"
os.environ.setdefault("MCP_URL", MCP_URL)  # used by config/mcp_servers.yaml

# Master switch on the MCP server for actions with side effects (send email, create or
# move events). The user still approves each one (config/guardrails.yaml).
ALLOW_SIDE_EFFECTS = os.getenv("ALLOW_SIDE_EFFECTS", "true").lower() == "true"

DATA_DIR = ROOT / "data"

# Jarvis HTTP API (api/main.py), used by the n8n evaluation workflow
API_PORT = int(os.getenv("API_PORT", "8002"))
# 127.0.0.1 = only this computer; 0.0.0.0 = also reachable from Docker (n8n in a container)
API_HOST = os.getenv("API_HOST", "127.0.0.1")
