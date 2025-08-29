import asyncio
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import databases
import httpx
import routeros_api
import sqlalchemy
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ==========================================================
# Configuración general
# ==========================================================
DATABASE_FILE = "data/monitor360.db"
DATABASE_URL = f"sqlite:///{DATABASE_FILE}"

# Env y rutas para WireGuard en userspace (boringtun)
WG_ENV_BASE = {
    "WG_QUICK_USERSPACE_IMPLEMENTATION": "boringtun",
    "WG_ENDPOINT_RESOLUTION_RETRIES": "2",
    # Asegurar PATH con binarios instalados en Alpine
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}

# ==========================================================
# Base de datos (SQLAlchemy + databases)
# ==========================================================
database = databases.Database(DATABASE_URL)
metadata = sqlalchemy.MetaData()

credentials_table = sqlalchemy.Table(
    "credentials",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("name", sqlalchemy.String, unique=True),
    sqlalchemy.Column("username", sqlalchemy.String),
    sqlalchemy.Column("password", sqlalchemy.String),
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
)


# ==========================================================
# Esquema SQLite inicial (para despliegue limpio)
# ==========================================================

def init_db():
    os.makedirs(os.path.dirname(DATABASE_FILE), exist_ok=True)
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode = WAL;")
        except sqlite3.OperationalError:
            pass
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE,
                username TEXT,
                password TEXT
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS monitors (
                id INTEGER PRIMARY KEY,
                device_id TEXT UNIQUE,
                FOREIGN KEY (device_id) REFERENCES devices (id) ON DELETE CASCADE
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sensors (
                id INTEGER PRIMARY KEY,
                monitor_id INTEGER,
                sensor_type TEXT,
                name TEXT,
                config TEXT,
                FOREIGN KEY (monitor_id) REFERENCES monitors (id) ON DELETE CASCADE
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ping_results (
                id INTEGER PRIMARY KEY,
                sensor_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                latency_ms REAL,
                status TEXT,
                FOREIGN KEY (sensor_id) REFERENCES sensors (id) ON DELETE CASCADE
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ethernet_results (
                id INTEGER PRIMARY KEY,
                sensor_id INTEGER,
                timestamp DATETIME,
                status TEXT,
                speed TEXT,
                rx_bitrate TEXT,
                tx_bitrate TEXT,
                FOREIGN KEY (sensor_id) REFERENCES sensors (id) ON DELETE CASCADE
            );
            """
        )

        cursor.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_channels (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE,
                type TEXT,
                config TEXT
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY,
                sensor_id INTEGER,
                channel_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                details TEXT,
                FOREIGN KEY (sensor_id) REFERENCES sensors(id) ON DELETE CASCADE,
                FOREIGN KEY (channel_id) REFERENCES notification_channels(id) ON DELETE CASCADE
            );
            """
        )

        try:
            cursor.execute(
                "ALTER TABLE devices ADD COLUMN vpn_profile_id INTEGER REFERENCES vpn_profiles(id);"
            )
        except sqlite3.OperationalError:
            pass

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS vpn_profiles (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE,
                config_data TEXT,
                check_ip TEXT,
                is_default BOOLEAN DEFAULT 0
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                client_name TEXT,
                ip_address TEXT UNIQUE,
                mac_address TEXT,
                node TEXT,
                status TEXT,
                credential_id INTEGER,
                is_maestro BOOLEAN DEFAULT 0,
                maestro_id TEXT,
                vpn_profile_id INTEGER,
                FOREIGN KEY (credential_id) REFERENCES credentials (id),
                FOREIGN KEY (maestro_id) REFERENCES devices (id),
                FOREIGN KEY (vpn_profile_id) REFERENCES vpn_profiles(id)
            );
            """
        )

        conn.commit()


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
# Utilidades de shell (run_command)
# ==========================================================
async def run_command(command: List[str], env: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    """Ejecuta un comando en un hilo (no bloquea el loop). Devuelve (ok, salida)."""

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


# ==========================================================
# Gestión de WireGuard por perfil (persistente, userspace)
# ==========================================================
VPN_STATE: Dict[int, Dict[str, Any]] = {}


def _iface_name_for_profile(profile_id: int) -> str:
    return f"m360-p{profile_id}"


def _conf_path_for_profile(profile_id: int) -> str:
    return os.path.join(tempfile.gettempdir(), f"{_iface_name_for_profile(profile_id)}.conf")


def _normalize_wg_config(raw: str) -> str:
    # Evita resolvconf dentro del contenedor; deja el resto igual
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
    # Si existe, revisar estado UP
    return "state UP" in out or "UP" in out


async def ensure_vpn_up(profile_id: int) -> str:
    """Garantiza que el túnel del perfil esté arriba. Devuelve nombre de interfaz."""
    iface = _iface_name_for_profile(profile_id)
    st = VPN_STATE.get(profile_id)

    # Si ya lo marcamos arriba, verificar que realmente exista/esté UP
    if st and st.get("up"):
        if await _iface_exists_up(iface):
            st["refcount"] = st.get("refcount", 0) + 1
            VPN_STATE[profile_id] = st
            return iface
        else:
            # Marcar como no-up para relanzar
            st["up"] = False
            VPN_STATE[profile_id] = st

    # Cargar config desde DB
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

    # Levantar idempotente
    ok, out = await wg_cmd(["wg-quick", "up", conf_path])
    if not ok:
        # Puede que ya exista pero down a medias; intentar un pequeño retry check
        ok_show, _ = await wg_cmd(["wg", "show", iface])
        if not ok_show:
            # último intento: bajar por si quedó zombie y volver a subir
            await wg_cmd(["wg-quick", "down", conf_path])
            ok2, out2 = await wg_cmd(["wg-quick", "up", conf_path])
            if not ok2:
                raise HTTPException(status_code=500, detail=f"No se pudo activar túnel WG: {out2 or out}")

    # Espera activa a que la interfaz exista y suba (hasta 3s)
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

    # Si no subió, error
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
async def tcp_port_reachable(ip: str, port: int, timeout_s: float = 1.0) -> bool:
    def sync_try():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_s)
            try:
                s.connect((ip, port))
                return True
            except Exception:
                return False
    return await asyncio.to_thread(sync_try)


async def test_and_get_credential_id(ip_address: str) -> Optional[int]:
    all_creds = await database.fetch_all(credentials_table.select())
    if not all_creds:
        print("DEBUG: No hay credenciales en la base de datos para probar.")
        return None

    # Si el puerto 8728 no responde, no sigas intentando credenciales
    if not await tcp_port_reachable(ip_address, 8728, timeout_s=1.5):
        print(f"[REACH] {ip_address}:8728 inalcanzable ()")
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
            print(f"DEBUG: ¡Éxito! Conexión establecida con credencial '{cred['name']}'.")
            return cred["id"]
        except routeros_api.exceptions.RouterOsApiConnectionError as e:
            print(f"DEBUG: Fallo de conexión para '{cred['name']}'. Causa: {e}")
            continue
        except Exception as e:
            print(f"DEBUG: Error inesperado para '{cred['name']}'. Causa: {e}")
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
# WebSocket broadcast manager
# ==========================================================
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                self.disconnect(connection)


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

    def escape_markdown(text: str) -> str:
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

    text = (
        f" *Alerta: {escape_markdown(message_details['sensor_name'])}*\n\n"
        f"*Dispositivo:* {escape_markdown(message_details['client_name'])} ("
        f"{escape_markdown(message_details['ip_address'])})\n"
        f"*Motivo:* {escape_markdown(message_details['reason'])}"
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except httpx.RequestError as e:
            print(f"[ERROR Telegram] No se pudo enviar la notificación: {e}")
        except httpx.HTTPStatusError as e:
            print(f"[ERROR Telegram] Error de API: {e.response.status_code} - {e.response.text}")


async def send_notification(channel_id: int, message_details: dict):
    channel = await database.fetch_one(
        notification_channels_table.select().where(notification_channels_table.c.id == channel_id)
    )
    if not channel:
        return
    config = channel["config"] if isinstance(channel["config"], dict) else json.loads(channel["config"])  # type: ignore
    if channel["type"] == "webhook":
        await send_webhook_notification(config, message_details)
    elif channel["type"] == "telegram":
        await send_telegram_notification(config, message_details)


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
            await send_notification(alert["channel_id"], message)
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

    device_info = {
        "id": row["device_id"],
        "client_name": row["client_name"],
        "ip_address": row["ip_address"],
        "credential_id": row["credential_id"],
        "maestro_id": row["maestro_id"],
        "vpn_profile_id": row["vpn_profile_id"],
    }

    # Cancelar si ya existía
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
            print(
                f"[PING#{sensor_id}] modo=maestro_to_device pero el dispositivo no tiene maestro_id. Saliendo."
            )
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
            print(f"[PING#{sensor_id}] falta 'target_ip' en config para ping desde dispositivo. Saliendo.")
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

                # RouterOS ping (1 intento)
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
                await manager.broadcast(json.dumps(broadcast_data))

                await check_and_trigger_alerts(sensor_id, name, result_data, device_info, config)

            except Exception as e:
                print(f"[PING#{sensor_id}] Error en ciclo: {e}")
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

                if_data = api.get_resource("/interface/ethernet").get(name=interface_name)
                if if_data:
                    result_data["status"] = "link_up" if if_data[0].get("running") else "link_down"
                    result_data["speed"] = if_data[0].get("speed", "N/A")

                monitor = api.get_resource("/interface").call(
                    "monitor-traffic", {"interface": interface_name, "once": ""}
                )
                if monitor:
                    result_data["rx_bitrate"] = monitor[0].get("rx-bits-per-second", "0")
                    result_data["tx_bitrate"] = monitor[0].get("tx-bits-per-second", "0")

                await database.execute(
                    ethernet_results_table.insert().values(
                        sensor_id=sensor_id,
                        timestamp=datetime.now(timezone.utc),
                        **result_data,
                    )
                )

                broadcast_data = {
                    "sensor_id": sensor_id,
                    "sensor_type": "ethernet",
                    **result_data,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await manager.broadcast(json.dumps(broadcast_data))

                await check_and_trigger_alerts(sensor_id, name, result_data, device_info, config)

            except Exception as e:
                print(f"[ETH#{sensor_id}] Error en ciclo: {e}")
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
app = FastAPI(title="Monitor360 API", version="16.0.0")


@app.on_event("startup")
async def startup():
    init_db()
    await database.connect()

    # Lanzar tareas para sensores existentes
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================
# Endpoints: dispositivos / credenciales / monitores / sensores
# ==========================================================
@app.post("/api/devices/test_reachability")
async def test_device_reachability(test: IsolatedConnectionTest):
    credential_id: Optional[int] = None

    if test.vpn_profile_id:
        vpn = await database.fetch_one(
            vpn_profiles_table.select().where(vpn_profiles_table.c.id == test.vpn_profile_id)
        )
        if not vpn:
            raise HTTPException(status_code=404, detail="Perfil VPN no encontrado")

        config_path = os.path.join(tempfile.gettempdir(), f"m360-{uuid.uuid4().hex[:8]}.conf")
        try:
            with open(config_path, "w") as f:
                f.write(_normalize_wg_config(vpn["config_data"]))
            os.chmod(config_path, 0o600)

            ok, out = await wg_cmd(["wg-quick", "up", config_path])
            if not ok:
                raise HTTPException(status_code=500, detail=f"No se pudo activar la VPN: {out}")

            await asyncio.sleep(2)

            # Pre-chequeo de puerto antes de probar credenciales
            if not await tcp_port_reachable(test.ip_address, 8728, timeout_s=1.5):
                return {"reachable": False, "detail": "Host/API RouterOS inalcanzable a través del túnel."}

            credential_id = await test_and_get_credential_id(test.ip_address)

        finally:
            await wg_cmd(["wg-quick", "down", config_path])
            try:
                if os.path.exists(config_path):
                    os.remove(config_path)
            except Exception:
                pass

        if credential_id:
            return {"reachable": True, "credential_id": credential_id}
        else:
            return {
                "reachable": False,
                "detail": "Dispositivo no alcanzable o credenciales incorrectas a través de la VPN.",
            }

    elif test.maestro_id:
        raise HTTPException(status_code=501, detail="Conexión vía Maestro no implementada.")

    else:
        # Conexión directa
        if not await tcp_port_reachable(test.ip_address, 8728, timeout_s=1.5):
            return {"reachable": False, "detail": "Host/API RouterOS inalcanzable."}
        credential_id = await test_and_get_credential_id(test.ip_address)
        if credential_id:
            return {"reachable": True, "credential_id": credential_id}
        else:
            return {
                "reachable": False,
                "detail": "Dispositivo no alcanzable o credenciales incorrectas en la red local.",
            }


@app.put("/api/devices/{device_id}/associate_vpn")
async def associate_vpn_to_maestro(device_id: str, association: VpnAssociation):
    query = (
        devices_table.update()
        .where(devices_table.c.id == device_id)
        .values(vpn_profile_id=association.vpn_profile_id)
    )
    await database.execute(query)
    return {"message": "Asociación de VPN actualizada."}


@app.post("/api/credentials", status_code=201)
async def create_credential(cred: CredentialCreate):
    query = credentials_table.insert().values(
        name=cred.name, username=cred.username, password=cred.password
    )
    try:
        last_record_id = await database.execute(query)
        return {"id": last_record_id, **cred.dict()}
    except Exception:
        raise HTTPException(status_code=400, detail="Ya existe una credencial con ese nombre.")


@app.get("/api/credentials", response_model=List[CredentialResponse])
async def get_credentials():
    return await database.fetch_all(credentials_table.select())


@app.delete("/api/credentials/{credential_id}", status_code=204)
async def delete_credential(credential_id: int):
    await database.execute(
        credentials_table.delete().where(credentials_table.c.id == credential_id)
    )


@app.post("/api/devices/manual", status_code=201)
async def add_device_manually(device: ManualDevice):
    # Elegir VPN (explícita o default) solo para validación si se desea
    vpn_to_use = None
    if device.vpn_profile_id:
        vpn_to_use = await database.fetch_one(
            vpn_profiles_table.select().where(vpn_profiles_table.c.id == device.vpn_profile_id)
        )
        if not vpn_to_use:
            raise HTTPException(status_code=404, detail="Perfil VPN no encontrado")
    else:
        vpn_to_use = await database.fetch_one(
            vpn_profiles_table.select().where(vpn_profiles_table.c.is_default == True)
        )

    credential_id: Optional[int] = None

    if vpn_to_use:
        # Prueba a través del túnel temporal
        config_path = os.path.join(tempfile.gettempdir(), f"m360-{uuid.uuid4().hex[:8]}.conf")
        try:
            with open(config_path, "w") as f:
                f.write(_normalize_wg_config(vpn_to_use["config_data"]))
            os.chmod(config_path, 0o600)

            ok, out = await wg_cmd(["wg-quick", "up", config_path])
            if not ok:
                raise HTTPException(status_code=500, detail=f"No se pudo activar la VPN: {out}")

            await asyncio.sleep(2)

            if not await tcp_port_reachable(device.ip_address, 8728, timeout_s=1.5):
                raise HTTPException(status_code=502, detail="Host/API RouterOS inalcanzable a través del túnel.")

            credential_id = await test_and_get_credential_id(device.ip_address)
        finally:
            await wg_cmd(["wg-quick", "down", config_path])
            try:
                if os.path.exists(config_path):
                    os.remove(config_path)
            except Exception:
                pass
    else:
        # Directo LAN
        if not await tcp_port_reachable(device.ip_address, 8728, timeout_s=1.5):
            raise HTTPException(status_code=502, detail="Host/API RouterOS inalcanzable.")
        credential_id = await test_and_get_credential_id(device.ip_address)

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
        "maestro_id": device.maestro_id,
        "vpn_profile_id": device.vpn_profile_id if device.vpn_profile_id else (vpn_to_use["id"] if vpn_to_use else None),
    }

    try:
        await database.execute(devices_table.insert().values(**insert_values))
        created = await database.fetch_one(
            devices_table.select().where(devices_table.c.id == device_id)
        )
        return created
    except Exception:
        raise HTTPException(status_code=400, detail="Un dispositivo con esta IP ya existe.")


@app.get("/api/devices")
async def get_all_devices(is_maestro: Optional[bool] = None):
    query = devices_table.select()
    if is_maestro is not None:
        query = query.where(devices_table.c.is_maestro == is_maestro)
    return await database.fetch_all(query)


@app.put("/api/devices/{device_id}/promote", status_code=200)
async def promote_device_to_maestro(device_id: str):
    await database.execute(
        devices_table.update()
        .where(devices_table.c.id == device_id)
        .values(is_maestro=True, maestro_id=None)
    )
    return {"message": "Dispositivo promovido a Maestro."}


@app.delete("/api/devices/{device_id}", status_code=204)
async def remove_device_from_monitor(device_id: str):
    await database.execute(devices_table.delete().where(devices_table.c.id == device_id))


@app.get("/api/devices/search", response_model=List[dict])
async def search_monitored_devices(search: Optional[str] = None):
    if not search:
        return []
    search_term = f"%{search}%"
    query = devices_table.select().where(
        (devices_table.c.client_name.ilike(search_term))
        | (devices_table.c.ip_address.ilike(search_term))
    )
    return [dict(r._mapping) for r in await database.fetch_all(query)]


@app.post("/api/monitors", status_code=201)
async def create_monitor(monitor: MonitorCreate):
    try:
        last_id = await database.execute(
            monitors_table.insert().values(device_id=monitor.device_id)
        )
        return {"id": last_id, "device_id": monitor.device_id}
    except Exception:
        raise HTTPException(status_code=400, detail="Ya existe un monitor para este dispositivo.")


@app.post("/api/sensors", status_code=201)
async def add_sensor_to_monitor(sensor: SensorCreate):
    last_id = await database.execute(
        sensors_table.insert().values(
            monitor_id=sensor.monitor_id,
            sensor_type=sensor.sensor_type,
            name=sensor.name,
            config=json.dumps(sensor.config),
        )
    )
    asyncio.create_task(launch_sensor_task(last_id))
    return {"id": last_id, **sensor.dict(), "config": sensor.config}


@app.post("/api/sensors/{sensor_id}/restart")
async def restart_sensor(sensor_id: int):
    if sensor_id in running_tasks:
        try:
            running_tasks[sensor_id].cancel()
        except Exception:
            pass
        running_tasks.pop(sensor_id, None)
    asyncio.create_task(launch_sensor_task(sensor_id))
    return {"status": "restarted"}


@app.put("/api/sensors/{sensor_id}")
async def update_sensor(sensor_id: int, sensor_data: SensorUpdate):
    await database.execute(
        sensors_table.update()
        .where(sensors_table.c.id == sensor_id)
        .values(name=sensor_data.name, config=json.dumps(sensor_data.config))
    )
    asyncio.create_task(launch_sensor_task(sensor_id))
    return {"id": sensor_id, **sensor_data.dict()}


@app.get("/api/monitors")
async def get_all_monitors_with_sensors():
    query = """
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
    """
    results = await database.fetch_all(query)
    normalized = []
    for r in results:
        row = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        row["sensors"] = json.loads(row["sensors"]) if row.get("sensors") else []
        normalized.append(row)
    return normalized


@app.delete("/api/sensors/{sensor_id}", status_code=204)
async def delete_sensor(sensor_id: int):
    await database.execute(sensors_table.delete().where(sensors_table.c.id == sensor_id))
    if sensor_id in running_tasks:
        try:
            running_tasks[sensor_id].cancel()
        except Exception:
            pass
        running_tasks.pop(sensor_id, None)


@app.delete("/api/monitors/{monitor_id}", status_code=204)
async def delete_monitor_and_sensors(monitor_id: int):
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
async def get_sensor_details(sensor_id: int):
    query = (
        sqlalchemy.select(
            sensors_table,
            devices_table.c.client_name,
            devices_table.c.ip_address,
        )
        .select_from(sensors_table.join(monitors_table).join(devices_table))
        .where(sensors_table.c.id == sensor_id)
    )
    result = await database.fetch_one(query)
    if not result:
        raise HTTPException(status_code=404, detail="Sensor no encontrado")
    return result


@app.get("/api/sensors/{sensor_id}/history_range")
async def get_sensor_history(sensor_id: int, start: datetime, end: datetime):
    sensor = await database.fetch_one(
        sensors_table.select().where(sensors_table.c.id == sensor_id)
    )
    if not sensor:
        raise HTTPException(status_code=404, detail="Sensor no encontrado")
    history_table = {
        "ping": ping_results_table,
        "ethernet": ethernet_results_table,
    }.get(sensor["sensor_type"])
    if history_table is None:
        return []
    query = (
        history_table.select()
        .where(history_table.c.sensor_id == sensor_id)
        .where(history_table.c.timestamp.between(start, end))
    )
    return await database.fetch_all(query)


# ==========================================================
# Endpoints: canales / alertas / Telegram
# ==========================================================
@app.post("/api/channels", status_code=201)
async def create_channel(channel: NotificationChannelCreate):
    query = notification_channels_table.insert().values(
        name=channel.name, type=channel.type, config=json.dumps(channel.config)
    )
    last_record_id = await database.execute(query)
    return {**channel.dict(), "id": last_record_id}


@app.get("/api/channels")
async def get_channels():
    results = await database.fetch_all(notification_channels_table.select())
    return [dict(r._mapping) for r in results]


@app.delete("/api/channels/{channel_id}", status_code=204)
async def delete_channel(channel_id: int):
    await database.execute(
        notification_channels_table.delete().where(notification_channels_table.c.id == channel_id)
    )


@app.post("/api/channels/telegram/get_chats")
async def get_telegram_chats(token_data: TelegramToken):
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
async def get_alert_history():
    query = """
    SELECT h.id, h.timestamp, h.details, s.name as sensor_name, c.name as channel_name
      FROM alert_history h
      JOIN sensors s ON h.sensor_id = s.id
      JOIN notification_channels c ON h.channel_id = c.id
     ORDER BY h.timestamp DESC
     LIMIT 100
    """
    return await database.fetch_all(query)


# ==========================================================
# Endpoints: VPN perfiles
# ==========================================================
@app.post("/api/vpns", status_code=201)
async def create_vpn_profile(profile: VpnProfileCreate):
    query = vpn_profiles_table.insert().values(
        name=profile.name, config_data=profile.config_data, check_ip=profile.check_ip
    )
    try:
        last_record_id = await database.execute(query)
        return {**profile.dict(), "id": last_record_id}
    except Exception:
        raise HTTPException(status_code=400, detail="Ya existe un perfil de VPN con ese nombre.")


@app.get("/api/vpns")
async def get_all_vpn_profiles():
    query = vpn_profiles_table.select()
    return await database.fetch_all(query)


@app.put("/api/vpns/{profile_id}")
async def update_vpn_profile(profile_id: int, profile: VpnProfileUpdate):
    update_data = profile.dict(exclude_unset=True)

    if update_data.get("is_default") is True:
        # Desmarcar otros defaults
        await database.execute(vpn_profiles_table.update().values(is_default=False))
        update_data["is_default"] = True

    query = (
        vpn_profiles_table.update().where(vpn_profiles_table.c.id == profile_id).values(**update_data)
    )
    await database.execute(query)
    return {**update_data, "id": profile_id}


@app.delete("/api/vpns/{profile_id}", status_code=204)
async def delete_vpn_profile(profile_id: int):
    query_check = devices_table.select().where(devices_table.c.vpn_profile_id == profile_id)
    associated_device = await database.fetch_one(query_check)
    if associated_device:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No se puede eliminar. El perfil está en uso por el dispositivo "
                f"'{associated_device['client_name']}'."
            ),
        )
    query = vpn_profiles_table.delete().where(vpn_profiles_table.c.id == profile_id)
    await database.execute(query)
    return {}


# ==========================================================
# WebSocket endpoint
# ==========================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # mantenemos vivo si el front quiere enviar pings
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ==========================================================
# Endpoint de depuración de WG (opcional)
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
