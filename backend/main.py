import asyncio
import ssl
import json
import os
import re
import time
import socket
import subprocess
import sys
import base64
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Set

from dotenv import load_dotenv
import html
import databases
import httpx
import routeros_api
import sqlalchemy
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from fastapi import FastAPI, HTTPException, WebSocket, Query, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from pydantic import BaseModel
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# 🔐 Usamos SOLO PyJWT (NO python-jose)
import jwt  # PyJWT


load_dotenv()

# ==========================================================
# Configuración general: SUPABASE / POSTGRES
# ==========================================================
RAW_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def _strip_sslmode_anycase(url: str) -> str:
    try:
        p = urlparse(url)
        q = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True) if k.lower() != "sslmode"]
        return urlunparse(p._replace(query=urlencode(q, doseq=True)))
    except Exception:
        return url

def _to_asyncpg_base_dsn(url: str) -> str:
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    url = _strip_sslmode_anycase(url)
    try:
        p = urlparse(url)
        return urlunparse(p._replace(query=""))
    except Exception:
        return url

def _sanitize_dsn(url: str) -> str:
    """Oculta user/pass y query del DSN en logs."""
    try:
        p = urlparse(url)
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme}://{host}{port}{p.path}"
    except Exception:
        return "<hidden>"

if not RAW_DATABASE_URL:
    raise RuntimeError("DATABASE_URL no configurada.")

ASYNC_DATABASE_URL = _to_asyncpg_base_dsn(RAW_DATABASE_URL)  # ← SIN ?sslmode=...
IS_POSTGRES = ASYNC_DATABASE_URL.startswith("postgresql+asyncpg://")

# --- SSL para asyncpg (producción por defecto: verify-full con CA bundle) ---
DB_SSL_MODE = os.getenv("DB_SSL_MODE", "verify-ca").lower()
DB_SSL_CA = os.getenv("DB_SSL_CA", "").strip()  # deja vacío para usar solo el trust store del sistema

def _sanitize_dsn(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme}://{host}{port}{p.path}"
    except Exception:
        return "<hidden>"

print(f"[DB] DSN={_sanitize_dsn(RAW_DATABASE_URL)}  SSL_MODE={DB_SSL_MODE}  CA={(DB_SSL_CA or '-')}")

ssl_context: Optional[ssl.SSLContext] = None

if DB_SSL_MODE == "disable":
    ssl_context = None

elif DB_SSL_MODE == "require":
    # Cifra sin validar (equivalente a sslmode=require de libpq). Úsalo solo si no puedes validar.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ssl_context = ctx

elif DB_SSL_MODE in ("verify-ca", "verify-full"):
    # Cargamos el trust store por defecto (Mozilla). Si DB_SSL_CA apunta a un .pem, lo añadimos.
    ctx = ssl.create_default_context()
    if DB_SSL_CA:
        if not os.path.exists(DB_SSL_CA):
            raise FileNotFoundError(f"DB_SSL_CA no existe: {DB_SSL_CA}")
        ctx.load_verify_locations(cafile=DB_SSL_CA)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = (DB_SSL_MODE == "verify-full")
    ssl_context = ctx

else:
    raise RuntimeError(f"DB_SSL_MODE inválido: {DB_SSL_MODE}")
# Conexión DB única
database = databases.Database(ASYNC_DATABASE_URL, ssl=ssl_context)
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    connect_args={"ssl": ssl_context} if ssl_context else {},
    pool_pre_ping=True,
)

# ==========================================================
# Auth (Supabase JWT) – Compatibilidad HS256 + RS256
# ==========================================================
SUPABASE_PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF")
if not SUPABASE_PROJECT_REF:
    raise RuntimeError("SUPABASE_PROJECT_REF no configurado (ej: abcd1234).")

SUPABASE_URL = os.getenv("SUPABASE_URL", f"https://{SUPABASE_PROJECT_REF}.supabase.co").rstrip("/")

# HS256 (legacy) – solo funciona si seteás SUPABASE_JWT_SECRET
SUPABASE_JWT_SECRET = (os.getenv("SUPABASE_JWT_SECRET", "").strip())

# RS256 (nuevo) – JWKS de Supabase
JWKS_URL = f"{SUPABASE_URL}/auth/v1/keys"
try:
    _jwks_client = jwt.PyJWKClient(JWKS_URL)
except Exception as e:
    _jwks_client = None
    print(f"[JWT] No se inicializó JWKS; se intentará HS256 si hay secreto. Motivo: {e}")

bearer = HTTPBearer(auto_error=False)


def _decode_hs256(token: str) -> Optional[dict]:
    if not SUPABASE_JWT_SECRET:
        return None
    try:
        return jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
            leeway=300,
        )
    except Exception as e:
        print(f"[JWT HS256] inválido: {e}")
        return None


def _decode_rs256(token: str) -> Optional[dict]:
    if _jwks_client is None:
        return None
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
            leeway=300,
        )
    except Exception as e:
        print(f"[JWT RS256] inválido: {e}")
        return None


def _owner_from_token(token: str) -> str | None:
    """Devuelve el sub (owner_id) o None si no se pudo validar."""
    try:
        hdr = jwt.get_unverified_header(token)
        print(f"[JWT DEBUG] Header recibido: alg={hdr.get('alg')} kid={hdr.get('kid')}")
    except Exception as e:
        print(f"[JWT DEBUG] no pude leer header: {e}")

    # 1) Intento HS256
    if SUPABASE_JWT_SECRET:
        payload = _decode_hs256(token)
        if payload:
            print(f"[JWT DEBUG] HS256 payload sub={payload.get('sub')}")
            return payload.get("sub")

    # 2) Intento RS256
    payload = _decode_rs256(token)
    if payload:
        print(f"[JWT DEBUG] RS256 payload sub={payload.get('sub')}")
        return payload.get("sub")

    return None


def _extract_token_from_request(request: Request, creds: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    # 1) Authorization header
    if creds and (creds.scheme or "").lower() == "bearer" and creds.credentials:
        return creds.credentials
    # 2) Query param (?token=)
    if "token" in request.query_params:
        return request.query_params.get("token")
    # 3) Cookie
    ck = request.cookies.get("sb-access-token")
    if ck:
        return ck
    return None


async def get_owner_id(request: Request, creds: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    token = _extract_token_from_request(request, creds)

    if token:
        owner_id = _owner_from_token(token)
        if owner_id:
            return owner_id

        # hint útil cuando el token es HS256 y falta la secret
        try:
            alg = jwt.get_unverified_header(token).get("alg")
            if alg == "HS256" and not SUPABASE_JWT_SECRET:
                print("[JWT HINT] Token HS256 pero SUPABASE_JWT_SECRET no está seteada o es incorrecta.")
        except Exception:
            pass

        raise HTTPException(status_code=401, detail="Token inválido o expirado.")

    raise HTTPException(status_code=401, detail="Falta token (Authorization Bearer / ?token / cookie).")


# Env y rutas para WireGuard (userspace/boringtun)
# ==========================================================

WG_ENV_BASE = {
    "WG_QUICK_USERSPACE_IMPLEMENTATION": "boringtun",
    "WG_ENDPOINT_RESOLUTION_RETRIES": "2",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}

# ==========================================================
# Base de datos (SQLAlchemy)
# ==========================================================

metadata = sqlalchemy.MetaData()

credentials_table = sqlalchemy.Table(
    "credentials",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("name", sqlalchemy.String, unique=True),
    sqlalchemy.Column("username", sqlalchemy.String),
    sqlalchemy.Column("password", sqlalchemy.String),
    sqlalchemy.Column("owner_id", sqlalchemy.String),  # UUID texto
)

devices_table = sqlalchemy.Table(
    "devices",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.String, primary_key=True),
    sqlalchemy.Column("client_name", sqlalchemy.String),
    sqlalchemy.Column("ip_address", sqlalchemy.String, unique=True),
    sqlalchemy.Column("mac_address", sqlalchemy.String),
    sqlalchemy.Column("node", sqlalchemy.String),
    sqlalchemy.Column("status", sqlalchemy.String),
    sqlalchemy.Column("credential_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("credentials.id"), nullable=True),
    sqlalchemy.Column("is_maestro", sqlalchemy.Boolean, default=False),
    sqlalchemy.Column("maestro_id", sqlalchemy.String, sqlalchemy.ForeignKey("devices.id"), nullable=True),
    sqlalchemy.Column("vpn_profile_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("vpn_profiles.id"), nullable=True),
    sqlalchemy.Column("owner_id", sqlalchemy.String),  # UUID texto
)

monitors_table = sqlalchemy.Table(
    "monitors",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column(
        "device_id",
        sqlalchemy.String,
        sqlalchemy.ForeignKey("devices.id", ondelete="CASCADE"),
        unique=True,
    ),
    sqlalchemy.Column("owner_id", sqlalchemy.String),
)

sensors_table = sqlalchemy.Table(
    "sensors",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column(
        "monitor_id",
        sqlalchemy.Integer,
        sqlalchemy.ForeignKey("monitors.id", ondelete="CASCADE"),
    ),
    sqlalchemy.Column("sensor_type", sqlalchemy.String),
    sqlalchemy.Column("name", sqlalchemy.String),
    sqlalchemy.Column("config", sqlalchemy.JSON),
    sqlalchemy.Column("owner_id", sqlalchemy.String),
)

ping_results_table = sqlalchemy.Table(
    "ping_results",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column(
        "sensor_id",
        sqlalchemy.Integer,
        sqlalchemy.ForeignKey("sensors.id", ondelete="CASCADE"),
    ),
    sqlalchemy.Column("timestamp", sqlalchemy.DateTime, default=lambda: datetime.now(timezone.utc)),
    sqlalchemy.Column("latency_ms", sqlalchemy.Float),
    sqlalchemy.Column("status", sqlalchemy.String),
)

ethernet_results_table = sqlalchemy.Table(
    "ethernet_results",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column(
        "sensor_id",
        sqlalchemy.Integer,
        sqlalchemy.ForeignKey("sensors.id", ondelete="CASCADE"),
    ),
    sqlalchemy.Column("timestamp", sqlalchemy.DateTime),
    sqlalchemy.Column("status", sqlalchemy.String),
    sqlalchemy.Column("speed", sqlalchemy.String),
    sqlalchemy.Column("rx_bitrate", sqlalchemy.String),
    sqlalchemy.Column("tx_bitrate", sqlalchemy.String),
)

settings_table = sqlalchemy.Table(
    "settings",
    metadata,
    sqlalchemy.Column("key", sqlalchemy.String, primary_key=True),
    sqlalchemy.Column("value", sqlalchemy.String),
)

notification_channels_table = sqlalchemy.Table(
    "notification_channels",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("name", sqlalchemy.String, unique=True),
    sqlalchemy.Column("type", sqlalchemy.String),
    sqlalchemy.Column("config", sqlalchemy.JSON),
    sqlalchemy.Column("owner_id", sqlalchemy.String),
)

alert_history_table = sqlalchemy.Table(
    "alert_history",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column(
        "sensor_id",
        sqlalchemy.Integer,
        sqlalchemy.ForeignKey("sensors.id", ondelete="CASCADE"),
    ),
    sqlalchemy.Column(
        "channel_id",
        sqlalchemy.Integer,
        sqlalchemy.ForeignKey("notification_channels.id", ondelete="CASCADE"),
    ),
    sqlalchemy.Column("timestamp", sqlalchemy.DateTime, default=lambda: datetime.now(timezone.utc)),
    sqlalchemy.Column("details", sqlalchemy.String),
)

vpn_profiles_table = sqlalchemy.Table(
    "vpn_profiles",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("name", sqlalchemy.String, unique=True),
    sqlalchemy.Column("config_data", sqlalchemy.Text),
    sqlalchemy.Column("check_ip", sqlalchemy.String),
    sqlalchemy.Column("is_default", sqlalchemy.Boolean, default=False),
    sqlalchemy.Column("owner_id", sqlalchemy.String),
)

# ==========================================================
# Inicialización de esquema en Postgres (async)
# ==========================================================

async def init_db():
    async with async_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

# ==========================================================
# Modelos Pydantic
# ==========================================================

class CredentialCreate(BaseModel):
    name: str
    username: str
    password: str

class CredentialResponse(BaseModel):
    id: int
    name: str
    username: str

class ManualDevice(BaseModel):
    client_name: str
    ip_address: str
    mac_address: Optional[str] = ""
    node: Optional[str] = ""
    maestro_id: Optional[str] = None
    vpn_profile_id: Optional[int] = None

class MonitorCreate(BaseModel):
    device_id: str

class SensorCreate(BaseModel):
    monitor_id: int
    sensor_type: str
    name: str
    config: Dict[str, Any]

class SensorUpdate(BaseModel):
    name: str
    config: Dict[str, Any]

class NotificationChannelCreate(BaseModel):
    name: str
    type: str
    config: Dict[str, Any]

class TelegramToken(BaseModel):
    bot_token: str

class VpnProfileCreate(BaseModel):
    name: str
    config_data: str
    check_ip: str

class VpnProfileUpdate(BaseModel):
    name: Optional[str] = None
    config_data: Optional[str] = None
    check_ip: Optional[str] = None
    is_default: Optional[bool] = None

class VpnAssociation(BaseModel):
    vpn_profile_id: Optional[int]

class IsolatedConnectionTest(BaseModel):
    ip_address: str
    vpn_profile_id: Optional[int] = None
    maestro_id: Optional[str] = None

# ==========================================================
# Utilidades de shell / WireGuard
# ==========================================================

async def run_command(command: List[str], env: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    def sync_run() -> subprocess.CompletedProcess:
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            env=merged_env,
            startupinfo=startupinfo,
        )

    try:
        result = await asyncio.to_thread(sync_run)
        if result.returncode == 0:
            return True, result.stdout
        else:
            err = result.stderr or result.stdout
            print(f"Error en comando: {command} -> {err}\n")
            return False, err
    except FileNotFoundError:
        msg = f"Error: comando no encontrado: {command[0]}"
        print(msg)
        return False, msg
    except Exception as e:
        msg = f"Error inesperado ejecutando {command}: {e}"
        print(msg)
        return False, msg

async def wg_cmd(args: List[str]) -> Tuple[bool, str]:
    return await run_command(args, env=WG_ENV_BASE)

VPN_STATE: Dict[int, Dict[str, Any]] = {}

def _iface_name_for_profile(profile_id: int) -> str:
    return f"m360-p{profile_id}"

def _conf_path_for_profile(profile_id: int) -> str:
    return os.path.join(tempfile.gettempdir(), f"{_iface_name_for_profile(profile_id)}.conf")

def _normalize_wg_config(raw: str) -> str:
    out = []
    for ln in raw.splitlines():
        if ln.strip().lower().startswith("dns="):
            out.append(f"# {ln}")
        else:
            out.append(ln)
    return "\n".join(out) + "\n"

async def _iface_exists_up(iface: str) -> bool:
    ok, out = await wg_cmd(["ip", "link", "show", iface])
    if not ok:
        return False
    return "state UP" in out or "UP" in out

async def ensure_vpn_up(profile_id: int) -> str:
    iface = _iface_name_for_profile(profile_id)
    st = VPN_STATE.get(profile_id)

    if st and st.get("up"):
        if await _iface_exists_up(iface):
            st["refcount"] = st.get("refcount", 0) + 1
            VPN_STATE[profile_id] = st
            return iface
        else:
            st["up"] = False
            VPN_STATE[profile_id] = st

    vpn = await database.fetch_one(
        vpn_profiles_table.select().where(vpn_profiles_table.c.id == profile_id)
    )
    if not vpn:
        raise HTTPException(status_code=404, detail=f"Perfil VPN {profile_id} no encontrado")

    conf_path = _conf_path_for_profile(profile_id)
    try:
        with open(conf_path, "w") as f:
            f.write(_normalize_wg_config(vpn["config_data"]))
        os.chmod(conf_path, 0o600)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo escribir config WG: {e}")

    ok, out = await wg_cmd(["wg-quick", "up", conf_path])
    if not ok:
        ok_show, _ = await wg_cmd(["wg", "show", iface])
        if not ok_show:
            await wg_cmd(["wg-quick", "down", conf_path])
            ok2, out2 = await wg_cmd(["wg-quick", "up", conf_path])
            if not ok2:
                raise HTTPException(status_code=500, detail=f"No se pudo activar túnel WG: {out2 or out}")

    for _ in range(30):
        if await _iface_exists_up(iface):
            VPN_STATE[profile_id] = {
                "iface": iface,
                "conf_path": conf_path,
                "refcount": st.get("refcount", 0) + 1 if st else 1,
                "up": True,
            }
            print(f"[VPN] UP {iface} (perfil {profile_id})")
            return iface
        await asyncio.sleep(0.1)

    raise HTTPException(status_code=500, detail=f"Interfaz {iface} no está UP tras levantar WG")

async def release_vpn(profile_id: int):
    st = VPN_STATE.get(profile_id)
    if not st:
        return
    st["refcount"] = max(0, st.get("refcount", 0) - 1)
    VPN_STATE[profile_id] = st

async def teardown_all_vpns():
    for pid, st in list(VPN_STATE.items()):
        conf = st.get("conf_path")
        if conf and os.path.exists(conf):
            await wg_cmd(["wg-quick", "down", conf])
            try:
                os.remove(conf)
            except Exception:
                pass
        VPN_STATE[pid]["up"] = False
        print(f"[VPN] DOWN {st.get('iface')} (perfil {pid})")

# ==========================================================
# Utilidades de conectividad / Mikrotik
# ==========================================================

async def tcp_port_reachable(ip: str, port: int, timeout_s: float = 1.5) -> bool:
    def sync_try():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_s)
            try:
                s.connect((ip, port))
                return True
            except Exception:
                return False
    return await asyncio.to_thread(sync_try)

async def test_and_get_credential_id(ip_address: str, owner_id: str) -> Optional[int]:
    all_creds = await database.fetch_all(
        credentials_table.select().where(credentials_table.c.owner_id == owner_id)
    )
    if not all_creds:
        print("DEBUG: No hay credenciales para este owner.")
        return None

    if not await tcp_port_reachable(ip_address, 8728, timeout_s=1.5):
        print(f"[REACH] {ip_address}:8728 inalcanzable")
        return None

    for cred in all_creds:
        connection = None
        print(f"DEBUG: Probando credencial '{cred['name']}' para {ip_address}...")
        try:
            connection = routeros_api.RouterOsApiPool(
                ip_address,
                username=cred["username"],
                password=cred["password"],
                port=8728,
                plaintext_login=True,
                use_ssl=False,
            )
            api = connection.get_api()
            api.get_resource("/system/identity").get()
            print(f"DEBUG: ¡Éxito! Conexión con '{cred['name']}'.")
            return cred["id"]
        except routeros_api.exceptions.RouterOsApiConnectionError as e:
            print(f"DEBUG: Fallo de conexión '{cred['name']}'. Causa: {e}")
            continue
        except Exception as e:
            print(f"DEBUG: Error inesperado '{cred['name']}'. Causa: {e}")
            continue
        finally:
            if connection:
                try:
                    connection.disconnect()
                except Exception:
                    pass

    print(f"DEBUG: Se probaron todas las credenciales para {ip_address} sin éxito.")
    return None

# ==========================================================
# WebSocket broadcast manager (por owner)
# ==========================================================

# ==========================================================
# WebSocket broadcast manager (por owner) — DEBUG EXTENDIDO
# ==========================================================
class ConnectionManager:
    def __init__(self):
        # guardamos (websocket, owner_id_normalizado)
        self.active: Set[Tuple[WebSocket, str]] = set()

    async def connect(self, websocket: WebSocket, owner_id: str):
        oid = (str(owner_id) if owner_id is not None else "").strip().lower()
        await websocket.accept()
        self.active.add((websocket, oid))
        # por defecto, sin suscripciones específicas hasta que el cliente mande un mensaje
        websocket.scope["subs"] = set()
        print(f"[MANAGER] connect owners={len(self.active)} -> {oid}")

    def disconnect(self, websocket: WebSocket):
        removed = False
        for item in list(self.active):
            ws, _oid = item
            if ws is websocket:
                self.active.remove(item)
                removed = True
        if removed:
            print(f"[MANAGER] disconnect owners={len(self.active)}")

    async def broadcast_for(self, owner_id: str, message: str):
        """Primero intenta emitir por owner. Si nadie lo recibe (p.ej. owner legacy),
        hace fallback por suscripción: entrega a sockets con subscribe_all (subs=None)
        o que estén suscriptos explícitamente al sensor_id del payload.
        """
        target = (str(owner_id) if owner_id is not None else "").strip().lower()
        sent = mismatches = errs = 0

        for (ws, oid) in list(self.active):
            if oid != target:
                mismatches += 1
                continue
            try:
                await ws.send_text(message)
                sent += 1
            except Exception as e:
                errs += 1
                try:
                    self.active.remove((ws, oid))
                except Exception:
                    pass

        # Fallback si no hubo entregas por owner
        if sent == 0:
            sid = None
            try:
                payload = json.loads(message)
                sid = payload.get("sensor_id", None)
            except Exception:
                sid = None

            if sid is not None:
                sent_fb = errs_fb = 0
                for (ws, oid) in list(self.active):
                    subs = ws.scope.get("subs", set())
                    try:
                        # subscribe_all => subs is None
                        if subs is None or (isinstance(subs, set) and (len(subs) == 0 or sid in subs)):
                            await ws.send_text(message)
                            sent_fb += 1
                    except Exception:
                        errs_fb += 1
                        try:
                            self.active.remove((ws, oid))
                        except Exception:
                            pass
                print(f"[MANAGER] broadcast_fallback sid={sid} owner={target} sent={sent_fb} errs={errs_fb}")

        print(f"[MANAGER] broadcast_result owner={target} sent={sent} mismatch={mismatches} errors={errs}")

manager = ConnectionManager()
running_tasks: Dict[int, asyncio.Task] = {}
connection_pools: Dict[str, routeros_api.RouterOsApiPool] = {}

# ==========================================================
# Alertas y estado
# ==========================================================

last_alert_times: Dict[Tuple[int, str], datetime] = {}
last_known_statuses: Dict[int, Dict[str, Any]] = {}

async def send_webhook_notification(config: dict, message_details: dict):
    url = config.get("url")
    if not url:
        return
    text = (
        f"🚨 **Alerta: {message_details['sensor_name']}**\n"
        f"**Dispositivo:** {message_details['client_name']} ({message_details['ip_address']})\n"
        f"**Motivo:** {message_details['reason']}"
    )
    payload = {"content": text}
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, timeout=10)
        except httpx.RequestError as e:
            print(f"[ERROR Webhook] {e}")

async def send_telegram_notification(config: dict, message_details: dict):
    bot_token = config.get("bot_token")
    chat_id = config.get("chat_id")
    if not bot_token or not chat_id:
        return

    def esc(x: str) -> str:
        return html.escape(str(x), quote=False)

    text = (
        f"<b>Alerta: {esc(message_details['sensor_name'])}</b>\n\n"
        f"<b>Dispositivo:</b> {esc(message_details['client_name'])} ({esc(message_details['ip_address'])})\n"
        f"<b>Motivo:</b> {esc(message_details['reason'])}"
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except httpx.RequestError as e:
            print(f"[ERROR Telegram] {e}")
        except httpx.HTTPStatusError as e:
            print(f"[ERROR Telegram] {e.response.status_code} - {e.response.text}")

async def send_notification(channel_id: int, message_details: dict, owner_id: str):
    channel = await database.fetch_one(
        notification_channels_table.select().where(
            (notification_channels_table.c.id == channel_id) &
            (notification_channels_table.c.owner_id == owner_id)
        )
    )
    if not channel:
        print("[ALERT] Canal no encontrado o no pertenece al owner.")
        return
    cfg = channel["config"] if isinstance(channel["config"], dict) else json.loads(channel["config"])
    if channel["type"] == "webhook":
        await send_webhook_notification(cfg, message_details)
    elif channel["type"] == "telegram":
        await send_telegram_notification(cfg, message_details)

async def check_and_trigger_alerts(
    sensor_id: int,
    sensor_name: str,
    result: dict,
    device_info: dict,
    sensor_config: dict,
):
    alert_configs = sensor_config.get("alerts", [])
    if not alert_configs:
        return

    now = datetime.now(timezone.utc)
    for alert in alert_configs:
        alert_key = (sensor_id, alert["type"])
        cooldown = timedelta(minutes=int(alert.get("cooldown_minutes", 5)))
        last_alert_time = last_alert_times.get(alert_key)
        if last_alert_time and (now - last_alert_time) < cooldown:
            continue

        trigger = False
        reason = ""

        if alert["type"] == "timeout" and result.get("status") == "timeout":
            trigger = True
            reason = "El sensor ha entrado en estado de Timeout."
        elif alert["type"] == "high_latency" and result.get("status") == "ok":
            if result.get("latency_ms", 0) > alert.get("threshold_ms", 0):
                trigger = True
                reason = (
                    f"Latencia alta detectada: {result['latency_ms']:.2f} ms "
                    f"(Umbral: {alert['threshold_ms']} ms)."
                )
        elif alert["type"] == "speed_change":
            last_speed = last_known_statuses.get(sensor_id, {}).get("speed")
            current_speed = result.get("speed")
            if last_speed is not None and current_speed != last_speed:
                trigger = True
                reason = f"La velocidad del puerto cambió de {last_speed} a {current_speed}."
        elif alert["type"] == "traffic_threshold":
            threshold_bps = float(alert.get("threshold_mbps", 0)) * 1_000_000
            rx_bps = int(result.get("rx_bitrate", 0))
            tx_bps = int(result.get("tx_bitrate", 0))
            direction = alert.get("direction", "any")
            if (direction in ["any", "rx"] and rx_bps > threshold_bps):
                trigger = True
                reason = (
                    f"Tráfico de bajada superó el umbral: {(rx_bps/1_000_000):.2f} Mbps "
                    f"(Umbral: {alert['threshold_mbps']} Mbps)."
                )
            elif (direction in ["any", "tx"] and tx_bps > threshold_bps):
                trigger = True
                reason = (
                    f"Tráfico de subida superó el umbral: {(tx_bps/1_000_000):.2f} Mbps "
                    f"(Umbral: {alert['threshold_mbps']} Mbps)."
                )

        if trigger:
            message = {
                "sensor_name": sensor_name,
                "client_name": device_info["client_name"],
                "ip_address": device_info["ip_address"],
                "reason": reason,
            }
            await send_notification(alert["channel_id"], message, device_info["owner_id"])
            await database.execute(
                alert_history_table.insert().values(
                    sensor_id=sensor_id, channel_id=alert["channel_id"], details=json.dumps(message)
                )
            )
            last_alert_times[alert_key] = now

    if "speed" in result:
        last_known_statuses.setdefault(sensor_id, {})["speed"] = result["speed"]

# ==========================================================
# Tareas de sensores
# ==========================================================

async def launch_sensor_task(sensor_id: int):
    q = (
        sqlalchemy.select(
            sensors_table.c.id,
            sensors_table.c.name,
            sensors_table.c.sensor_type,
            sensors_table.c.config,
            devices_table.c.id.label("device_id"),
            devices_table.c.client_name,
            devices_table.c.ip_address,
            devices_table.c.credential_id,
            devices_table.c.maestro_id,
            devices_table.c.vpn_profile_id,
            devices_table.c.owner_id.label("device_owner_id"),
        )
        .select_from(sensors_table.join(monitors_table).join(devices_table))
        .where(sensors_table.c.id == sensor_id)
    )

    row = await database.fetch_one(q)
    if not row:
        print(f"[SENSORS] No encontrado sensor {sensor_id}")
        return

    cfg = row["config"]
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = {}
    elif not isinstance(cfg, dict):
        cfg = {}

    device_info = {
        "id": row["device_id"],
        "client_name": row["client_name"],
        "ip_address": row["ip_address"],
        "credential_id": row["credential_id"],
        "maestro_id": row["maestro_id"],
        "vpn_profile_id": row["vpn_profile_id"],
        "owner_id": row["device_owner_id"],
    }

    if sensor_id in running_tasks:
        try:
            running_tasks[sensor_id].cancel()
        except Exception:
            pass
        running_tasks.pop(sensor_id, None)

    if row["sensor_type"] == "ping":
        task = asyncio.create_task(run_ping_monitor(row["id"], row["name"], cfg, device_info))
    elif row["sensor_type"] == "ethernet":
        task = asyncio.create_task(run_ethernet_monitor(row["id"], row["name"], cfg, device_info))
    else:
        print(f"[SENSORS] Tipo desconocido {row['sensor_type']} para sensor {row['id']}")
        return

    running_tasks[row["id"]] = task
    print(f"[SENSORS] Task #{row['id']} lanzada ({row['sensor_type']})")

async def _ensure_origin_connectivity(origin_device_info: dict):
    pid = origin_device_info.get("vpn_profile_id")
    if pid:
        await ensure_vpn_up(pid)

async def _release_origin_connectivity(origin_device_info: dict):
    pid = origin_device_info.get("vpn_profile_id")
    if pid:
        await release_vpn(pid)

async def run_ping_monitor(sensor_id: int, name: str, config: dict, device_info: dict):
    interval = int(config.get("interval_sec", 60))
    latency_threshold_visual = int(config.get("latency_threshold_ms", 150))
    ping_type = config.get("ping_type", "maestro_to_device")

    origin_device_info: Dict[str, Any] = {}
    target_ip: str = ""

    if ping_type == "maestro_to_device":
        maestro_id = device_info.get("maestro_id")
        if not maestro_id:
            print(f"[PING#{sensor_id}] modo=maestro_to_device sin maestro_id. Saliendo.")
            return
        maestro_device = await database.fetch_one(
            devices_table.select().where(devices_table.c.id == maestro_id)
        )
        if not maestro_device:
            print(f"[PING#{sensor_id}] maestro_id {maestro_id} no encontrado. Saliendo.")
            return
        origin_device_info = dict(maestro_device._mapping)
        target_ip = device_info["ip_address"]
    else:
        origin_device_info = device_info
        target_ip = config.get("target_ip", "")
        if not target_ip:
            print(f"[PING#{sensor_id}] falta 'target_ip' en config. Saliendo.")
            return

    origin_ip = origin_device_info["ip_address"]
    print(f"[PING#{sensor_id}] INICIO origin={origin_ip} -> target={target_ip} tipo={ping_type}")

    await _ensure_origin_connectivity(origin_device_info)

    try:
        while sensor_id in running_tasks:
            current_status, current_latency = "error", 9999.0
            try:
                if origin_ip not in connection_pools:
                    cred = await database.fetch_one(
                        credentials_table.select().where(
                            credentials_table.c.id == origin_device_info["credential_id"]
                        )
                    )
                    if not cred:
                        raise Exception(f"Credenciales no encontradas para {origin_ip}")
                    connection_pools[origin_ip] = routeros_api.RouterOsApiPool(
                        origin_ip,
                        username=cred["username"],
                        password=cred["password"],
                        port=8728,
                        plaintext_login=True,
                        use_ssl=False,
                    )
                api = connection_pools[origin_ip].get_api()

                ping_result = api.get_resource("/").call("ping", {"address": target_ip, "count": "1"})
                if ping_result and ping_result[0].get("received") == "1":
                    time_str = ping_result[0].get("avg-rtt", "0ms")
                    seconds = 0
                    millis = 0
                    s_match = re.search(r"(\d+)s", time_str)
                    ms_match = re.search(r"(\d+)ms", time_str)
                    if s_match:
                        seconds = int(s_match.group(1))
                    if ms_match:
                        millis = int(ms_match.group(1))
                    current_latency = seconds * 1000 + millis
                    current_status = "high_latency" if current_latency > latency_threshold_visual else "ok"
                else:
                    current_status = "timeout"

                result_data = {"status": current_status, "latency_ms": current_latency}
                await database.execute(
                    ping_results_table.insert().values(
                        sensor_id=sensor_id, timestamp=datetime.now(timezone.utc), **result_data
                    )
                )

                broadcast_data = {
                    "sensor_id": sensor_id,
                    "sensor_type": "ping",
                    **result_data,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await manager.broadcast_for(device_info["owner_id"], json.dumps(broadcast_data))

                await check_and_trigger_alerts(sensor_id, name, result_data, device_info, config)

            except Exception as e:
                print(f"[PING#{sensor_id}] Error en ciclo: {e}")

                result_data = {"status": "timeout", "latency_ms": None}
                ts = datetime.now(timezone.utc)

                try:
                    await database.execute(
                        ping_results_table.insert().values(
                            sensor_id=sensor_id, timestamp=ts, **result_data
                        )
                    )
                except Exception as _db_e:
                    print(f"[PING#{sensor_id}] Error guardando timeout: {_db_e}")

                try:
                    await manager.broadcast_for(
                        device_info["owner_id"],
                        json.dumps({
                            "sensor_id": sensor_id,
                            "sensor_type": "ping",
                            **result_data,
                            "timestamp": ts.isoformat(),
                        })
                    )
                except Exception as _ws_e:
                    print(f"[PING#{sensor_id}] Error broadcast timeout: {_ws_e}")

                try:
                    await check_and_trigger_alerts(sensor_id, name, result_data, device_info, config)
                except Exception as _alert_e:
                    print(f"[PING#{sensor_id}] Error alerts timeout: {_alert_e}")

                if origin_ip in connection_pools:
                    try:
                        connection_pools[origin_ip].disconnect()
                    except Exception:
                        pass
                    connection_pools.pop(origin_ip, None)

            await asyncio.sleep(interval)
    finally:
        await _release_origin_connectivity(origin_device_info)
        print(f"[PING#{sensor_id}] FIN")

async def run_ethernet_monitor(sensor_id: int, name: str, config: dict, device_info: dict):
    interval = int(config.get("interval_sec", 30))
    interface_name = config.get("interface_name")
    device_ip = device_info["ip_address"]

    await _ensure_origin_connectivity(device_info)

    def _parse_link_up(val: Optional[str]) -> bool:
        if val is None:
            return False
        s = str(val).lower()
        return s in ("link-ok", "link_ok", "ok", "up", "running", "true", "yes")

    try:
        while sensor_id in running_tasks:
            result_data = {
                "status": "error",
                "speed": "N/A",
                "rx_bitrate": "0",
                "tx_bitrate": "0",
            }
            try:
                if device_ip not in connection_pools:
                    credential_id = device_info.get("credential_id")
                    if not credential_id:
                        raise Exception("Falta credential_id")
                    cred = await database.fetch_one(
                        credentials_table.select().where(credentials_table.c.id == credential_id)
                    )
                    if not cred:
                        raise Exception("Credenciales no encontradas")
                    connection_pools[device_ip] = routeros_api.RouterOsApiPool(
                        device_ip,
                        username=cred["username"],
                        password=cred["password"],
                        port=8728,
                        plaintext_login=True,
                        use_ssl=False,
                    )
                api = connection_pools[device_ip].get_api()

                # 1) ROS 7.x: monitor once
                speed_ok = False
                try:
                    eth_res = api.get_resource("/interface/ethernet")
                    mon = []
                    try:
                        mon = eth_res.call("monitor", {"numbers": interface_name, "once": ""})
                    except Exception:
                        mon = eth_res.call("monitor", {"interface": interface_name, "once": ""})

                    if mon:
                        m = mon[0]
                        if _parse_link_up(m.get("status")):
                            result_data["status"] = "link_up"
                        else:
                            result_data["status"] = "link_down"

                        rate = m.get("rate") or m.get("speed")
                        if rate:
                            result_data["speed"] = str(rate)
                            speed_ok = True
                except Exception as e_mon:
                    print(f"[ETH#{sensor_id}] monitor (ROS7) error: {e_mon}")

                # 2) Fallback ROS 6.x
                if not speed_ok:
                    try:
                        if_data = api.get_resource("/interface/ethernet").get(name=interface_name)
                        if if_data:
                            d = if_data[0]
                            result_data["status"] = "link_up" if d.get("running") else "link_down"
                            result_data["speed"] = d.get("speed", result_data["speed"]) or result_data["speed"]
                    except Exception as e_get:
                        print(f"[ETH#{sensor_id}] ethernet get fallback error: {e_get}")

                # 3) Tráfico
                try:
                    monitor = api.get_resource("/interface").call(
                        "monitor-traffic", {"interface": interface_name, "once": ""}
                    )
                    if monitor:
                        result_data["rx_bitrate"] = monitor[0].get("rx-bits-per-second", "0") or "0"
                        result_data["tx_bitrate"] = monitor[0].get("tx-bits-per-second", "0") or "0"
                except Exception as e_tra:
                    print(f"[ETH#{sensor_id}] monitor-traffic error: {e_tra}")

                # Persistencia + WS + alertas
                ts = datetime.now(timezone.utc)
                await database.execute(
                    ethernet_results_table.insert().values(
                        sensor_id=sensor_id,
                        timestamp=ts,
                        **result_data,
                    )
                )
                await manager.broadcast_for(
                    device_info["owner_id"],
                    json.dumps({
                        "sensor_id": sensor_id,
                        "sensor_type": "ethernet",
                        **result_data,
                        "timestamp": ts.isoformat(),
                    })
                )
                await check_and_trigger_alerts(sensor_id, name, result_data, device_info, config)

            except Exception as e:
                print(f"[ETH#{sensor_id}] Error en ciclo: {e}")

                ts = datetime.now(timezone.utc)
                result_data = {
                    "status": "link_down",
                    "speed": "N/A",
                    "rx_bitrate": "0",
                    "tx_bitrate": "0",
                }
                try:
                    await database.execute(
                        ethernet_results_table.insert().values(
                            sensor_id=sensor_id, timestamp=ts, **result_data
                        )
                    )
                    await manager.broadcast_for(
                        device_info["owner_id"],
                        json.dumps({
                            "sensor_id": sensor_id,
                            "sensor_type": "ethernet",
                            **result_data,
                            "timestamp": ts.isoformat(),
                        })
                    )
                    await check_and_trigger_alerts(sensor_id, name, result_data, device_info, config)
                except Exception as _e2:
                    print(f"[ETH#{sensor_id}] Error guardando/broadcast en error: {_e2}")

                if device_ip in connection_pools:
                    try:
                        connection_pools[device_ip].disconnect()
                    except Exception:
                        pass
                    connection_pools.pop(device_ip, None)

            await asyncio.sleep(interval)
    finally:
        await _release_origin_connectivity(device_info)

# ==========================================================
# FastAPI app
# ==========================================================

app = FastAPI(title="Monitor360 API", version="18.0.0-multi-tenant")

@app.on_event("startup")
async def startup():
    await init_db()
    await database.connect()

    # Lanzar tareas para sensores existentes (de todos los owners)
    rows = await database.fetch_all(sensors_table.select())
    for s in rows:
        try:
            asyncio.create_task(launch_sensor_task(s["id"]))
        except Exception as e:
            print(f"[SENSORS] No se pudo lanzar {s['id']}: {e}")

@app.on_event("shutdown")
async def shutdown():
    for pool in list(connection_pools.values()):
        try:
            pool.disconnect()
        except Exception:
            pass
    for task_id in list(running_tasks.keys()):
        try:
            running_tasks[task_id].cancel()
        except Exception:
            pass
    await teardown_all_vpns()
    await database.disconnect()
    await async_engine.dispose()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================
# Helpers SQL por owner
# ==========================================================

def _monitors_query_sql_for_owner(owner_id: str) -> Tuple[str, dict]:
    if IS_POSTGRES:
        sql = """
        SELECT
          m.id AS monitor_id,
          d.id AS device_id,
          d.client_name,
          d.ip_address,
          d.credential_id,
          d.maestro_id,
          d.vpn_profile_id,
          COALESCE(
            json_agg(
              json_build_object(
                'id', s.id,
                'name', s.name,
                'sensor_type', s.sensor_type,
                'config', s.config
              )
            ) FILTER (WHERE s.id IS NOT NULL),
            '[]'::json
          ) AS sensors
        FROM monitors m
        JOIN devices d ON m.device_id = d.id
        LEFT JOIN sensors s ON s.monitor_id = m.id
        WHERE d.owner_id = :owner_id
        GROUP BY m.id, d.id, d.client_name, d.ip_address, d.credential_id, d.maestro_id, d.vpn_profile_id
        ORDER BY m.id ASC
        """
        return sql, {"owner_id": owner_id}
    else:
        sql = """
        SELECT m.id as monitor_id,
               d.id as device_id,
               d.client_name,
               d.ip_address,
               d.credential_id,
               d.maestro_id,
               d.vpn_profile_id,
               (SELECT json_group_array(json_object('id', s.id, 'name', s.name, 'sensor_type', s.sensor_type, 'config', json(s.config)))
                  FROM sensors s WHERE s.monitor_id = m.id) as sensors
          FROM monitors m
          JOIN devices d ON m.device_id = d.id
         WHERE d.owner_id = :owner_id
        """
        return sql, {"owner_id": owner_id}

# ==========================================================
# Endpoints: dispositivos / credenciales / monitores / sensores
# ==========================================================

@app.post("/api/devices/test_reachability")
async def test_device_reachability(test: IsolatedConnectionTest, owner_id: str = Depends(get_owner_id)):
    if test.vpn_profile_id:
        vpn = await database.fetch_one(
            vpn_profiles_table.select().where(
                (vpn_profiles_table.c.id == test.vpn_profile_id) &
                (vpn_profiles_table.c.owner_id == owner_id)
            )
        )
        if not vpn:
            raise HTTPException(status_code=404, detail="Perfil VPN no encontrado.")
        try:
            await ensure_vpn_up(test.vpn_profile_id)
            await asyncio.sleep(1)
            credential_id = await test_and_get_credential_id(test.ip_address, owner_id)
        finally:
            await release_vpn(test.vpn_profile_id)

        if credential_id:
            return {"reachable": True, "credential_id": credential_id}
        else:
            return {"reachable": False, "detail": "No alcanzable o credenciales incorrectas vía VPN."}

    if test.maestro_id:
        maestro = await database.fetch_one(
            devices_table.select().where(
                (devices_table.c.id == test.maestro_id) &
                (devices_table.c.owner_id == owner_id)
            )
        )
        if not maestro:
            raise HTTPException(status_code=404, detail="Maestro no encontrado.")
        maestro_pid = maestro["vpn_profile_id"]
        if not maestro_pid:
            raise HTTPException(status_code=400, detail="El maestro no tiene un perfil de VPN asociado.")

        try:
            await ensure_vpn_up(maestro_pid)
            await asyncio.sleep(1)
            credential_id = await test_and_get_credential_id(test.ip_address, owner_id)
        finally:
            await release_vpn(maestro_pid)

        if credential_id:
            return {"reachable": True, "credential_id": credential_id, "used_profile_id": maestro_pid}
        else:
            return {"reachable": False, "detail": "No alcanzable vía túnel del maestro o credenciales inválidas.", "used_profile_id": maestro_pid}

    credential_id = await test_and_get_credential_id(test.ip_address, owner_id)
    if credential_id:
        return {"reachable": True, "credential_id": credential_id}
    else:
        return {"reachable": False, "detail": "No alcanzable o credenciales incorrectas en LAN."}

@app.put("/api/devices/{device_id}/associate_vpn")
async def associate_vpn_to_maestro(device_id: str, association: VpnAssociation, owner_id: str = Depends(get_owner_id)):
    dev = await database.fetch_one(
        devices_table.select().where(
            (devices_table.c.id == device_id) &
            (devices_table.c.owner_id == owner_id)
        )
    )
    if not dev:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")
    if association.vpn_profile_id is not None:
        vpn = await database.fetch_one(
            vpn_profiles_table.select().where(
                (vpn_profiles_table.c.id == association.vpn_profile_id) &
                (vpn_profiles_table.c.owner_id == owner_id)
            )
        )
        if not vpn:
            raise HTTPException(status_code=404, detail="Perfil VPN no encontrado.")

    query = (
        devices_table.update()
        .where((devices_table.c.id == device_id) & (devices_table.c.owner_id == owner_id))
        .values(vpn_profile_id=association.vpn_profile_id)
    )
    await database.execute(query)
    return {"message": "Asociación de VPN actualizada."}

@app.post("/api/credentials", status_code=201)
async def create_credential(cred: CredentialCreate, owner_id: str = Depends(get_owner_id)):
    query = credentials_table.insert().values(
        name=cred.name, username=cred.username, password=cred.password, owner_id=owner_id
    )
    try:
        last_record_id = await database.execute(query)
        return {"id": last_record_id, **cred.dict()}
    except Exception:
        raise HTTPException(status_code=400, detail="Ya existe una credencial con ese nombre.")

@app.get("/api/credentials", response_model=List[CredentialResponse])
async def get_credentials(owner_id: str = Depends(get_owner_id)):
    rows = await database.fetch_all(
        credentials_table.select().where(credentials_table.c.owner_id == owner_id)
    )
    return rows

@app.delete("/api/credentials/{credential_id}", status_code=204)
async def delete_credential(credential_id: int, owner_id: str = Depends(get_owner_id)):
    await database.execute(
        credentials_table.delete().where(
            (credentials_table.c.id == credential_id) &
            (credentials_table.c.owner_id == owner_id)
        )
    )

@app.post("/api/devices/manual", status_code=201)
async def add_device_manually(device: ManualDevice, owner_id: str = Depends(get_owner_id)):
    credential_id: Optional[int] = None
    vpn_to_use = None

    if device.vpn_profile_id:
        vpn_to_use = await database.fetch_one(
            vpn_profiles_table.select().where(
                (vpn_profiles_table.c.id == device.vpn_profile_id) &
                (vpn_profiles_table.c.owner_id == owner_id)
            )
        )
        if not vpn_to_use:
            raise HTTPException(status_code=404, detail="Perfil VPN no encontrado")
    elif device.maestro_id:
        maestro = await database.fetch_one(
            devices_table.select().where(
                (devices_table.c.id == device.maestro_id) &
                (devices_table.c.owner_id == owner_id)
            )
        )
        if not maestro:
            raise HTTPException(status_code=404, detail="Maestro no encontrado")
        if not maestro["vpn_profile_id"]:
            raise HTTPException(status_code=400, detail="El maestro no tiene un perfil de VPN asociado.")
        vpn_to_use = await database.fetch_one(
            vpn_profiles_table.select().where(
                (vpn_profiles_table.c.id == maestro["vpn_profile_id"]) &
                (vpn_profiles_table.c.owner_id == owner_id)
            )
        )
    else:
        vpn_to_use = await database.fetch_one(
            vpn_profiles_table.select().where(
                (vpn_profiles_table.c.is_default == True) &
                (vpn_profiles_table.c.owner_id == owner_id)
            )
        )

    if vpn_to_use:
        pid = vpn_to_use["id"]
        try:
            await ensure_vpn_up(pid)
            await asyncio.sleep(1)
            credential_id = await test_and_get_credential_id(device.ip_address, owner_id)
        finally:
            await release_vpn(pid)
    else:
        credential_id = await test_and_get_credential_id(device.ip_address, owner_id)

    if not credential_id:
        raise HTTPException(status_code=401, detail=f"Autenticación fallida en {device.ip_address}.")

    device_id = str(uuid.uuid4())
    insert_values = {
        "id": device_id,
        "client_name": device.client_name,
        "ip_address": device.ip_address,
        "mac_address": device.mac_address or "",
        "node": device.node or "",
        "status": "MANUAL",
        "credential_id": credential_id,
        "is_maestro": False,
        "maestro_id": (device.maestro_id or None),
        "vpn_profile_id": device.vpn_profile_id if device.vpn_profile_id else (vpn_to_use["id"] if vpn_to_use else None),
        "owner_id": owner_id,
    }

    try:
        await database.execute(devices_table.insert().values(**insert_values))
        created = await database.fetch_one(
            devices_table.select().where(
                (devices_table.c.id == device_id) &
                (devices_table.c.owner_id == owner_id)
            )
        )
        return created

    except IntegrityError as e:
        msg = str(getattr(e, "orig", e))
        print(f"[ADD_DEVICE IntegrityError] {msg}")
        if "unique" in msg.lower() and "ip_address" in msg:
            raise HTTPException(status_code=409, detail="Ya existe un dispositivo con esa IP.")
        if "devices_maestro_id_fkey" in msg:
            raise HTTPException(status_code=400, detail="El maestro referenciado no existe.")
        if "devices_vpn_profile_id_fkey" in msg:
            raise HTTPException(status_code=400, detail="El perfil VPN referenciado no existe.")
        if "devices_credential_id_fkey" in msg:
            raise HTTPException(status_code=400, detail="La credencial referenciada no existe.")
        raise HTTPException(status_code=400, detail=f"Error BD al insertar dispositivo: {msg}")

    except Exception as e:
        print(f"[ADD_DEVICE ERROR] {e!r}")
        raise HTTPException(status_code=400, detail=f"Error inesperado: {e}")

@app.get("/api/devices")
async def get_all_devices(is_maestro: Optional[bool] = None, owner_id: str = Depends(get_owner_id)):
    q = devices_table.select().where(devices_table.c.owner_id == owner_id)
    if is_maestro is not None:
        q = q.where(devices_table.c.is_maestro == is_maestro)
    return await database.fetch_all(q)

@app.put("/api/devices/{device_id}/promote", status_code=200)
async def promote_device_to_maestro(device_id: str, owner_id: str = Depends(get_owner_id)):
    await database.execute(
        devices_table.update()
        .where((devices_table.c.id == device_id) & (devices_table.c.owner_id == owner_id))
        .values(is_maestro=True, maestro_id=None)
    )
    return {"message": "Dispositivo promovido a Maestro."}

@app.delete("/api/devices/{device_id}", status_code=204)
async def remove_device_from_monitor(device_id: str, owner_id: str = Depends(get_owner_id)):
    # verifica propiedad
    dev = await database.fetch_one(
        devices_table.select().where(
            (devices_table.c.id == device_id) & (devices_table.c.owner_id == owner_id)
        )
    )
    if not dev:
        return
    # borra monitores (cascade sensores)
    await database.execute(
        monitors_table.delete().where(
            (monitors_table.c.device_id == device_id) & (monitors_table.c.owner_id == owner_id)
        )
    )
    # borra dispositivo
    await database.execute(
        devices_table.delete().where(
            (devices_table.c.id == device_id) & (devices_table.c.owner_id == owner_id)
        )
    )

@app.get("/api/devices/search", response_model=List[dict])
async def search_monitored_devices(search: Optional[str] = None, owner_id: str = Depends(get_owner_id)):
    if not search:
        return []
    search_term = f"%{search}%"
    q = devices_table.select().where(
        (devices_table.c.owner_id == owner_id) &
        ((devices_table.c.client_name.ilike(search_term)) | (devices_table.c.ip_address.ilike(search_term)))
    )
    return [dict(r._mapping) for r in await database.fetch_all(q)]

@app.post("/api/monitors", status_code=201)
async def create_monitor(monitor: MonitorCreate, owner_id: str = Depends(get_owner_id)):
    dev = await database.fetch_one(
        devices_table.select().where(
            (devices_table.c.id == monitor.device_id) &
            (devices_table.c.owner_id == owner_id)
        )
    )
    if not dev:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")

    try:
        last_id = await database.execute(
            monitors_table.insert().values(device_id=monitor.device_id, owner_id=owner_id)
        )
        return {"id": last_id, "device_id": monitor.device_id}
    except Exception:
        raise HTTPException(status_code=400, detail="Ya existe un monitor para este dispositivo.")

@app.post("/api/sensors", status_code=201)
async def add_sensor_to_monitor(sensor: SensorCreate, owner_id: str = Depends(get_owner_id)):
    mon = await database.fetch_one(
        monitors_table.select().where(
            (monitors_table.c.id == sensor.monitor_id) &
            (monitors_table.c.owner_id == owner_id)
        )
    )
    if not mon:
        raise HTTPException(status_code=404, detail="Monitor no encontrado.")

    last_id = await database.execute(
        sensors_table.insert().values(
            monitor_id=sensor.monitor_id,
            sensor_type=sensor.sensor_type,
            name=sensor.name,
            config=sensor.config,
            owner_id=owner_id,
        )
    )
    asyncio.create_task(launch_sensor_task(last_id))
    return {"id": last_id, **sensor.dict(), "config": sensor.config}

@app.post("/api/sensors/{sensor_id}/restart")
async def restart_sensor(sensor_id: int, owner_id: str = Depends(get_owner_id)):
    # verifica propiedad
    q = (
        sqlalchemy.select(sensors_table.c.id)
        .select_from(sensors_table.join(monitors_table, sensors_table.c.monitor_id == monitors_table.c.id))
        .where((sensors_table.c.id == sensor_id) & (monitors_table.c.owner_id == owner_id))
    )
    ok_row = await database.fetch_one(q)
    if not ok_row:
        raise HTTPException(status_code=404, detail="Sensor no encontrado.")

    if sensor_id in running_tasks:
        try:
            running_tasks[sensor_id].cancel()
        except Exception:
            pass
        running_tasks.pop(sensor_id, None)
    asyncio.create_task(launch_sensor_task(sensor_id))
    return {"status": "restarted"}

@app.put("/api/sensors/{sensor_id}")
async def update_sensor(sensor_id: int, sensor_data: SensorUpdate, owner_id: str = Depends(get_owner_id)):
    q_check = (
        sqlalchemy.select(sensors_table.c.id)
        .select_from(sensors_table.join(monitors_table, sensors_table.c.monitor_id == monitors_table.c.id))
        .where((sensors_table.c.id == sensor_id) & (monitors_table.c.owner_id == owner_id))
    )
    ok_row = await database.fetch_one(q_check)
    if not ok_row:
        raise HTTPException(status_code=404, detail="Sensor no encontrado.")

    await database.execute(
        sensors_table.update()
        .where(sensors_table.c.id == sensor_id)
        .values(name=sensor_data.name, config=sensor_data.config)
    )
    asyncio.create_task(launch_sensor_task(sensor_id))
    return {"id": sensor_id, **sensor_data.dict()}

@app.get("/api/monitors")
async def get_all_monitors_with_sensors(owner_id: str = Depends(get_owner_id)):
    sql, params = _monitors_query_sql_for_owner(owner_id)
    results = await database.fetch_all(sql, values=params)
    normalized = []
    for r in results:
        row = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        sensors_val = row.get("sensors")
        if isinstance(sensors_val, str):
            row["sensors"] = json.loads(sensors_val) if sensors_val else []
        else:
            row["sensors"] = sensors_val or []
        normalized.append(row)
    return normalized

@app.delete("/api/sensors/{sensor_id}", status_code=204)
async def delete_sensor(sensor_id: int, owner_id: str = Depends(get_owner_id)):
    q_check = (
        sqlalchemy.select(sensors_table.c.id)
        .select_from(sensors_table.join(monitors_table, sensors_table.c.monitor_id == monitors_table.c.id))
        .where((sensors_table.c.id == sensor_id) & (monitors_table.c.owner_id == owner_id))
    )
    ok_row = await database.fetch_one(q_check)
    if not ok_row:
        return
    await database.execute(sensors_table.delete().where(sensors_table.c.id == sensor_id))
    if sensor_id in running_tasks:
        try:
            running_tasks[sensor_id].cancel()
        except Exception:
            pass
        running_tasks.pop(sensor_id, None)

@app.delete("/api/monitors/{monitor_id}", status_code=204)
async def delete_monitor_and_sensors(monitor_id: int, owner_id: str = Depends(get_owner_id)):
    mon = await database.fetch_one(
        monitors_table.select().where(
            (monitors_table.c.id == monitor_id) & (monitors_table.c.owner_id == owner_id)
        )
    )
    if not mon:
        return
    sensors_to_stop = await database.fetch_all(
        sensors_table.select().where(sensors_table.c.monitor_id == monitor_id)
    )
    for sensor in sensors_to_stop:
        sid = sensor["id"] if isinstance(sensor, dict) else sensor.id
        if sid in running_tasks:
            try:
                running_tasks[sid].cancel()
            except Exception:
                pass
            running_tasks.pop(sid, None)
    await database.execute(monitors_table.delete().where(monitors_table.c.id == monitor_id))

@app.get("/api/sensors/{sensor_id}/details")
async def get_sensor_details(sensor_id: int, owner_id: str = Depends(get_owner_id)):
    query = (
        sqlalchemy.select(
            sensors_table,
            devices_table.c.client_name,
            devices_table.c.ip_address,
        )
        .select_from(sensors_table.join(monitors_table).join(devices_table))
        .where((sensors_table.c.id == sensor_id) & (monitors_table.c.owner_id == owner_id))
    )
    result = await database.fetch_one(query)
    if not result:
        raise HTTPException(status_code=404, detail="Sensor no encontrado")
    return result

@app.get("/api/sensors/{sensor_id}/history_range")
async def get_sensor_history(sensor_id: int, time_range: str = Query("24h"), owner_id: str = Depends(get_owner_id)):
    sensor = await database.fetch_one(
        sensors_table.select().where(
            (sensors_table.c.id == sensor_id)
        ).select_from(
            sensors_table.join(monitors_table, sensors_table.c.monitor_id == monitors_table.c.id)
        ).where(
            monitors_table.c.owner_id == owner_id
        )
    )
    if not sensor:
        raise HTTPException(status_code=404, detail="Sensor no encontrado")

    range_map = {"1h": 1, "12h": 12, "24h": 24, "7d": 168, "30d": 720}
    hours_to_subtract = range_map.get(time_range, 24)

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(hours=hours_to_subtract)

    history_table = {
        "ping": ping_results_table,
        "ethernet": ethernet_results_table,
    }.get(sensor["sensor_type"])

    if history_table is None:
        return []

    query = (
        history_table.select()
        .where(history_table.c.sensor_id == sensor_id)
        .where(history_table.c.timestamp.between(start_date, end_date))
        .order_by(history_table.c.timestamp.asc())
    )
    return await database.fetch_all(query)

# ==========================================================
# Endpoints: canales / alertas / Telegram
# ==========================================================

@app.post("/api/channels", status_code=201)
async def create_channel(channel: NotificationChannelCreate, owner_id: str = Depends(get_owner_id)):
    q = notification_channels_table.insert().values(
        name=channel.name, type=channel.type, config=channel.config, owner_id=owner_id
    )
    last_record_id = await database.execute(q)
    return {**channel.dict(), "id": last_record_id}

@app.get("/api/channels")
async def get_channels(owner_id: str = Depends(get_owner_id)):
    q = notification_channels_table.select().where(notification_channels_table.c.owner_id == owner_id)
    results = await database.fetch_all(q)
    return [dict(r._mapping) for r in results]

@app.delete("/api/channels/{channel_id}", status_code=204)
async def delete_channel(channel_id: int, owner_id: str = Depends(get_owner_id)):
    await database.execute(
        notification_channels_table.delete().where(
            (notification_channels_table.c.id == channel_id) &
            (notification_channels_table.c.owner_id == owner_id)
        )
    )

@app.post("/api/channels/telegram/get_chats")
async def get_telegram_chats(token_data: TelegramToken, owner_id: str = Depends(get_owner_id)):
    bot_token = token_data.bot_token
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Error de la API de Telegram: {data.get('description')}",
                )
            chats: Dict[str, Dict[str, Any]] = {}
            for update in data.get("result", []):
                message = update.get("message") or update.get("my_chat_member", {}).get("chat")
                if message:
                    chat = message.get("chat")
                    if chat:
                        chat_id = chat.get("id")
                        title = chat.get("title") or f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip()
                        if chat_id and title:
                            chats[chat_id] = {"id": chat_id, "title": title}
            return list(chats.values())
        except httpx.RequestError as e:
            raise HTTPException(status_code=500, detail=f"No se pudo conectar con la API de Telegram: {e}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error procesando la respuesta: {e}")

@app.get("/api/alerts/history")
async def get_alert_history(owner_id: str = Depends(get_owner_id)):
    query = """
    SELECT h.id, h.timestamp, h.details, s.name as sensor_name, c.name as channel_name
      FROM alert_history h
      JOIN sensors s ON h.sensor_id = s.id
      JOIN notification_channels c ON h.channel_id = c.id
     WHERE s.owner_id = :owner_id AND c.owner_id = :owner_id
     ORDER BY h.timestamp DESC
     LIMIT 100
    """
    return await database.fetch_all(query, values={"owner_id": owner_id})

# ==========================================================
# Endpoints: VPN perfiles
# ==========================================================

@app.post("/api/vpns", status_code=201)
async def create_vpn_profile(profile: VpnProfileCreate, owner_id: str = Depends(get_owner_id)):
    try:
        name = (profile.name or "").strip()
        res = await database.execute(
            vpn_profiles_table.insert().values(
                name=name,
                config_data=profile.config_data,
                check_ip=profile.check_ip or "",
                is_default=False,
                owner_id=owner_id,
            )
        )
        return {**profile.dict(), "id": res}
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Nombre de perfil duplicado.")
    except Exception as e:
        print(f"[DB ERROR] create_vpn_profile -> {e!r}")
        raise HTTPException(status_code=400, detail=f"Error creando perfil: {e}")

@app.get("/api/_debug/vpns_raw")
async def vpns_raw(owner_id: str = Depends(get_owner_id)):
    rows = await database.fetch_all(
        "select id, name, is_default, length(config_data) as cfg_len, check_ip from vpn_profiles where owner_id = :oid order by id",
        values={"oid": owner_id},
    )
    return [dict(r._mapping) for r in rows]

@app.get("/api/vpns")
async def get_all_vpn_profiles(owner_id: str = Depends(get_owner_id)):
    query = vpn_profiles_table.select().where(vpn_profiles_table.c.owner_id == owner_id)
    return await database.fetch_all(query)

@app.put("/api/vpns/{profile_id}")
async def update_vpn_profile(profile_id: int, profile: VpnProfileUpdate, owner_id: str = Depends(get_owner_id)):
    update_data = profile.dict(exclude_unset=True)

    if update_data.get("is_default") is True:
        await database.execute(
            vpn_profiles_table.update()
            .where(vpn_profiles_table.c.owner_id == owner_id)
            .values(is_default=False)
        )
        update_data["is_default"] = True

    q = (
        vpn_profiles_table.update()
        .where((vpn_profiles_table.c.id == profile_id) & (vpn_profiles_table.c.owner_id == owner_id))
        .values(**update_data)
    )
    await database.execute(q)
    return {**update_data, "id": profile_id}

@app.delete("/api/vpns/{profile_id}", status_code=204)
async def delete_vpn_profile(profile_id: int, owner_id: str = Depends(get_owner_id)):
    query_check = devices_table.select().where(
        (devices_table.c.vpn_profile_id == profile_id) & (devices_table.c.owner_id == owner_id)
    )
    associated_device = await database.fetch_one(query_check)
    if associated_device:
        raise HTTPException(
            status_code=400,
            detail=f"No se puede eliminar. El perfil está en uso por '{associated_device['client_name']}'.",
        )
    query = vpn_profiles_table.delete().where(
        (vpn_profiles_table.c.id == profile_id) & (vpn_profiles_table.c.owner_id == owner_id)
    )
    await database.execute(query)
    return {}

# ==========================================================
# WebSocket endpoint (token opcional en ?token=)
# ==========================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # --------- Autenticación por token en query ---------
    token = websocket.query_params.get("token")
    owner_id = _owner_from_token(token) if token else None
    if not owner_id:
        await websocket.close(code=4401, reason="unauthorized")
        return

    # --------- Helpercitos locales ---------
    async def _owner_sensors(sensor_ids: Optional[List[int]] = None) -> List[dict]:
        q = (
            sqlalchemy.select(sensors_table.c.id, sensors_table.c.sensor_type)
            .select_from(
                sensors_table.join(
                    monitors_table, sensors_table.c.monitor_id == monitors_table.c.id
                ).join(
                    devices_table, monitors_table.c.device_id == devices_table.c.id
                )
            )
            .where(devices_table.c.owner_id == owner_id)
        )
        rows = await database.fetch_all(q)
        sensors = [
            {
                "id": (r.id if hasattr(r, "id") else r[0]),
                "sensor_type": (r.sensor_type if hasattr(r, "sensor_type") else r[1]),
            }
            for r in rows
        ]
        if sensor_ids:
            sset = set(sensor_ids)
            sensors = [s for s in sensors if s["id"] in sset]
        return sensors

    async def _latest_for_sensor(sid: int, stype: str) -> Optional[dict]:
        if stype == "ping":
            row = await database.fetch_one(
                ping_results_table.select()
                .where(ping_results_table.c.sensor_id == sid)
                .order_by(ping_results_table.c.timestamp.desc())
                .limit(1)
            )
            if row:
                return {
                    "sensor_id": sid,
                    "sensor_type": "ping",
                    "status": row["status"],
                    "latency_ms": row["latency_ms"],
                    "timestamp": (
                        row["timestamp"].astimezone(timezone.utc).isoformat()
                        if isinstance(row["timestamp"], datetime)
                        else None
                    ),
                }
        elif stype == "ethernet":
            row = await database.fetch_one(
                ethernet_results_table.select()
                .where(ethernet_results_table.c.sensor_id == sid)
                .order_by(ethernet_results_table.c.timestamp.desc())
                .limit(1)
            )
            if row:
                return {
                    "sensor_id": sid,
                    "sensor_type": "ethernet",
                    "status": row["status"],
                    "speed": row["speed"],
                    "rx_bitrate": row["rx_bitrate"],
                    "tx_bitrate": row["tx_bitrate"],
                    "timestamp": (
                        row["timestamp"].astimezone(timezone.utc).isoformat()
                        if isinstance(row["timestamp"], datetime)
                        else None
                    ),
                }
        return None

    async def _send_initial_batch():
        # Si hay filtro en la conexión, respetarlo; si no, mandar todos los del owner
        sub_ids = websocket.scope.get("subs")
        sensors = await _owner_sensors(list(sub_ids) if sub_ids else None)

        items: List[dict] = []
        for s in sensors:
            last = await _latest_for_sensor(s["id"], s["sensor_type"])
            if last:
                items.append(last)
            else:
                # placeholder “pending” si aún no hay histórico
                now_iso = datetime.now(timezone.utc).isoformat()
                if s["sensor_type"] == "ping":
                    items.append({
                        "sensor_id": s["id"],
                        "sensor_type": "ping",
                        "status": "pending",
                        "latency_ms": None,
                        "timestamp": now_iso,
                    })
                elif s["sensor_type"] == "ethernet":
                    items.append({
                        "sensor_id": s["id"],
                        "sensor_type": "ethernet",
                        "status": "pending",
                        "speed": "N/A",
                        "rx_bitrate": "0",
                        "tx_bitrate": "0",
                        "timestamp": now_iso,
                    })

        try:
            print(f"[WS] initial_batch -> owner={owner_id} items={len(items)}")
            await websocket.send_text(json.dumps({
                "type": "sensor_batch",
                "items": items,
                "ts": datetime.now(timezone.utc).isoformat(),
            }))
        except Exception as e:
            print(f"[WS] error enviando sensor_batch: {e}")

    # --------- Registrar conexión ---------
    await manager.connect(websocket, owner_id)
    websocket.scope.setdefault("subs", None)
    print(f"[WS] CONNECT owner={owner_id} total_active={len(manager.active)}")

    try:
        # Handshake + READY + batch inicial AUTOMÁTICO
        await websocket.send_text(json.dumps({
            "type": "welcome",
            "hello": True,
            "ts": datetime.now(timezone.utc).isoformat(),
        }))
        await websocket.send_text(json.dumps({
            "type": "ready",
            "ts": datetime.now(timezone.utc).isoformat(),
        }))
        # Enviar el batch inicial sin esperar pedido del cliente
        await _send_initial_batch()

        # Bucle de mensajes del cliente
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                print(f"[WS] DISCONNECT owner={owner_id}")
                break

            try:
                msg = json.loads(raw)
            except Exception as e:
                print(f"[WS] msg no-JSON: {e}  raw={raw[:200]}")
                continue

            mtype = (msg.get("type") or "").lower()
            print(f"[WS] recv owner={owner_id} type={mtype} msg={msg}")

            if mtype == "ping":
                await websocket.send_text(json.dumps({
                    "type": "pong",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }))

            elif mtype == "subscribe_sensors":
                ids = msg.get("sensor_ids")
                if isinstance(ids, list) and all(isinstance(x, int) for x in ids):
                    websocket.scope["subs"] = set(ids)
                    print(f"[WS] subscribe_sensors owner={owner_id} n={len(ids)}")
                else:
                    websocket.scope["subs"] = None
                    print(f"[WS] subscribe_sensors owner={owner_id} -> limpiado (formato inválido)")

                await websocket.send_text(json.dumps({
                    "type": "ready",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }))
                # y mandamos batch tras suscripción
                await _send_initial_batch()

            elif mtype == "subscribe_all":
                websocket.scope["subs"] = None
                print(f"[WS] subscribe_all owner={owner_id}")
                await websocket.send_text(json.dumps({
                    "type": "ready",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }))
                await _send_initial_batch()

            elif mtype == "sync_request" and (msg.get("resource") == "sensors_latest"):
                print(f"[WS] sync_request owner={owner_id} -> sensors_latest")
                await _send_initial_batch()

            else:
                # Mensaje no reconocido
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "error": f"unknown_message_type: {mtype}",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }))

    finally:
        manager.disconnect(websocket)
        print(f"[WS] CLOSE owner={owner_id} total_active={len(manager.active)}")



# Endpoints de depuración (opcionales)
# ==========================================================

@app.get("/api/_debug/wg")
async def debug_wg():
    ip_link = await wg_cmd(["ip", "link", "show"])
    wg_show = await wg_cmd(["wg", "show"])
    return {
        "ip_link_ok": ip_link[0],
        "ip_link": ip_link[1],
        "wg_show_ok": wg_show[0],
        "wg_show": wg_show[1],
        "vpn_state": VPN_STATE,
    }

@app.get("/api/_debug/routes")
async def debug_routes():
    r1 = await wg_cmd(["ip", "-4", "route"])
    r2 = await wg_cmd(["ip", "-6", "route"])
    r3 = await wg_cmd(["ip", "rule"])
    return {
        "ipv4_route_ok": r1[0], "ipv4_route": r1[1],
        "ipv6_route_ok": r2[0], "ipv6_route": r2[1],
        "ip_rule_ok": r3[0],   "ip_rule": r3[1],
    }

@app.get("/api/debug/whoami")
async def whoami(user_id: str = Depends(get_owner_id)):
    return JSONResponse({"owner_id": user_id})

@app.get("/api/debug/dump-token")
async def dump_token(request: Request):
    """
    Devuelve el header y payload del JWT recibido en Authorization.
    Ojo: no valida la firma, es solo para debug.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return {"error": "Falta Authorization Bearer"}

    token = auth_header.split(" ", 1)[1]

    try:
        header_b64, payload_b64, _ = token.split(".")
        for_decode = lambda s: json.loads(
            base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()
        )
        header = for_decode(header_b64)
        payload = for_decode(payload_b64)
        return {"header": header, "payload": payload}
    except Exception as e:
        return {"error": f"No se pudo decodificar: {e}"}