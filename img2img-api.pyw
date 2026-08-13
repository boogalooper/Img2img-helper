# -*- coding: utf-8 -*-
"""Локальный API-сервис img2img helper для Photoshop.

Сервис сохраняет существующий backend ComfyUI и добавляет независимый backend
Forge Neo с интерфейсами, описанными поставляемыми JSON-схемами.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import importlib
import io
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import mimetypes
import os
import queue
import re
import shutil
import socket
import struct
import sys
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


API_HOST = "127.0.0.1"
DEFAULT_COMFY_HOST = "127.0.0.1"
API_RECEIVE_PORT = 6370   # На этом порту Python принимает команды JSX.
API_REPLY_PORT = 6371     # На этот порт Python отправляет ответы JSX.
API_PROTOCOL = 3
VERSION = "0.196"

# Общая идентичность приложения и служебных путей.
APP = {
    "name": "img2img helper",
    "data_folder": "img2img helper",
    "schema_folder": "forge-schemas",
    "runtime_file": "runtime.json",
    "startup_file": "startup.json",
    "log_file": "img2img-api.log",
    "upload_subfolder": "img2img-helper",
}
APP_NAME = APP["name"]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FORGE_SCHEMA_DIRS = (
    SCRIPT_DIR / APP["schema_folder"],
    SCRIPT_DIR / "lib" / APP["schema_folder"],
    SCRIPT_DIR,
    SCRIPT_DIR / "lib",
)
FORGE_SCHEMA_KIND = "photoshop-helper-forge-schema"
FORGE_SCHEMA_VERSION = 1
FORGE_MODEL_HINTS_FILENAME = "forge_model_hints.json"
FORGE_MODEL_HINTS_KIND = "img2img-helper-forge-model-hints"
FORGE_MODEL_HINTS_VERSION = 2
FORGE_REFERENCE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


# Лимит одного JSON-сообщения от JSX.
MAX_API_MESSAGE = 32 * 1024 * 1024

# Handshake заменяет эти значения и сохраняет их в runtime.json.
DEFAULT_IDLE_TIMEOUT_SECONDS = 15 * 60
DEFAULT_BACKEND_MONITOR_INTERVAL_SECONDS = 5

# После запуска prompt история опрашивается чаще.
HISTORY_PREPARE_POLL_INTERVAL = 0.35
HISTORY_RESULT_POLL_INTERVAL = 0.20

# Максимальный возраст временных каталогов перед автоматической очисткой.
TEMP_MAX_AGE_SECONDS = 24 * 60 * 60
UPLOAD_SUBFOLDER = APP["upload_subfolder"]
OUTPUT_SUBFOLDER = "Img2imgHelper"

# Версия формата внутреннего кеша. При изменении структуры увеличить число.
CACHE_VERSION = 1
# Версия сокращённой /object_info-схемы рядом с анализом.
VALIDATION_SCHEMA_VERSION = 1
# Новый UUID сбрасывает только кэш анализа workflow.
ANALYZER_UUID = "8d1619b1-a414-4b9b-a5fa-14930ee013a9"

# Кэш ImageStitch ограничен числом элементов и размером.
IMAGESTITCH_CACHE_MAX_ITEMS = 12
IMAGESTITCH_CACHE_MAX_BYTES = 128 * 1024 * 1024

# Теги, добавляемые к имени ноды в ComfyUI.
TAG_PATTERNS = {
    "input": [r"#PS-INPUT\b"],
    "output": [r"#PS-OUTPUT\b"],
    "size": [r"#PS-SIZE\b"],
    "primary": [r"#PS-MAIN\b"],
    "ui": [r"#PS-UI\b"],
    "reference": [r"#PS-REF(?:ERENCE)?(?:-?\d+)?\b"],
    "mask": [r"#PS-MASK\b"],
}

# Семантические aliases. Они применяются не как жёсткая таблица конкретных
# моделей, а как один из признаков при анализе разных custom nodes.
CONTROL_ALIASES = {
    "checkpoint": {"ckpt_name", "checkpoint_name", "checkpoint", "model_name", "unet_name"},
    "vae": {"vae_name", "vae"},
    "text_encoder": {"clip_name", "clip_name1", "clip_name2", "text_encoder", "text_encoder1", "text_encoder2", "text_encoder_name", "clip_l_name", "clip_g_name", "t5_name", "lora_clip_name"},
    "lora": {"lora_name", "lora", "lora_model", "lora_model_name", "lora_file", "lora_filename"},
    "positive_prompt": {"text", "prompt", "positive", "positive_prompt"},
    "negative_prompt": {"negative", "negative_prompt", "text"},
    "steps": {"steps", "num_steps", "sampling_steps"},
    "cfg": {"cfg", "cfg_scale"},
    "guidance": {
        "guidance", "guidance_scale", "flux_guidance",
        "distilled_cfg", "distilled_cfg_scale",
    },
    "denoise": {"denoise", "denoise_strength", "strength"},
    "sampler": {"sampler_name", "sampler"},
    "scheduler": {"scheduler", "scheduler_name"},
    "seed": {"seed", "noise_seed"},
    # Часто регулируемые числовые параметры custom nodes. Здесь намеренно
    # отсутствуют неоднозначные одиночные имена вроде weight/strength/scale.
    "model_strength": {"strength_model", "model_strength", "lora_strength", "unet_strength"},
    "clip_strength": {"strength_clip", "clip_strength", "text_encoder_strength"},
    "conditioning_strength": {
        "control_strength", "controlnet_strength", "ipadapter_strength",
        "adapter_strength", "conditioning_strength", "reference_strength",
    },
    "start_percent": {"start_percent", "guidance_start", "start_step_percent"},
    "end_percent": {"end_percent", "guidance_end", "end_step_percent"},
    "mask_grow": {"grow_mask_by", "mask_grow", "expand_mask"},
    "mask_blur": {"mask_blur", "blur_radius", "feather", "feathering"},
    "detection_threshold": {
        "detection_threshold", "confidence", "confidence_threshold", "score_threshold",
    },
    "blend": {"blend_factor", "mix_factor", "opacity"},
    "variation_strength": {"variation_strength", "variation_amount", "subseed_strength"},
    "noise_strength": {"noise_strength", "noise_amount"},
    "tile_overlap": {"tile_overlap", "overlap_size"},
}

EXTRA_RECOMMENDED_CONTROL_ORDER = [
    "model_strength",
    "clip_strength",
    "conditioning_strength",
    "start_percent",
    "end_percent",
    "variation_strength",
    "noise_strength",
    "mask_grow",
    "mask_blur",
    "detection_threshold",
    "blend",
    "tile_overlap",
]

# Порядок стандартных контролов в Photoshop.
STANDARD_CONTROL_ORDER = [
    "checkpoint",
    "vae",
    "text_encoder",
    "positive_prompt",
    "lora",
    "negative_prompt",
    "sampler",
    "scheduler",
    "steps",
    "cfg",
    "guidance",
    "denoise",
] + EXTRA_RECOMMENDED_CONTROL_ORDER + [
    "seed",
]


# ============================================================================
# КАТАЛОГИ ПРИЛОЖЕНИЯ, ЛОГИРОВАНИЕ И ОБЩИЕ УТИЛИТЫ
# ============================================================================
def _local_appdata() -> Path:
    """Возвращает пользовательский каталог LocalAppData.

    На Windows обычно это ``C:\\Users\\...\\AppData\\Local``. Fallback нужен
    для тестирования на других ОС и не влияет на основное Windows-применение.
    """

    value = os.environ.get("LOCALAPPDATA")
    if value:
        return Path(value)
    return Path.home() / ".local" / "share"


APP_DIR = _local_appdata() / APP["data_folder"]
CACHE_DIR = APP_DIR / "cache"
WORKFLOW_CACHE_DIR = CACHE_DIR / "workflows"
TEMP_DIR = APP_DIR / "temp"
STATE_DIR = APP_DIR / "state"
RUNTIME_FILE = STATE_DIR / APP["runtime_file"]
STARTUP_FILE = STATE_DIR / APP["startup_file"]
LOG_FILE = APP_DIR / APP["log_file"]

for _directory in (APP_DIR, CACHE_DIR, WORKFLOW_CACHE_DIR, TEMP_DIR, STATE_DIR):
    _directory.mkdir(parents=True, exist_ok=True)


LOGGER = logging.getLogger(APP_NAME)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False
LOGGER.handlers[:] = []
_LOG_FORMAT = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s: %(message)s")

try:
    _file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    _file_handler.setFormatter(_LOG_FORMAT)
    LOGGER.addHandler(_file_handler)
except OSError:
    pass

if sys.stderr is not None:
    _console_handler = logging.StreamHandler(sys.stderr)
    _console_handler.setFormatter(_LOG_FORMAT)
    LOGGER.addHandler(_console_handler)

if not LOGGER.handlers:
    LOGGER.addHandler(logging.NullHandler())


STARTUP_PROCESS_STARTED_AT = time.time()
STARTUP_STATUS_LOCK = threading.Lock()
STARTUP_STATUS: Dict[str, Any] = {
    "status": "starting",
    "message": "Starting Python API",
}


def write_startup_status(status: str, message: str = "") -> None:
    """Atomically publish Python startup state for the waiting JSX process."""

    payload = {
        "status": str(status or "starting"),
        "message": str(message or ""),
        "started_at": STARTUP_PROCESS_STARTED_AT,
        "log_file": str(LOG_FILE),
    }
    temp_path = STARTUP_FILE.with_name(
        f".{STARTUP_FILE.name}.{os.getpid()}.tmp"
    )
    try:
        with STARTUP_STATUS_LOCK:
            STARTUP_STATUS.clear()
            STARTUP_STATUS.update(payload)
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_path, STARTUP_FILE)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        LOGGER.warning("Could not write Python startup status: %s", STARTUP_FILE)


def startup_status_snapshot() -> Dict[str, Any]:
    with STARTUP_STATUS_LOCK:
        return copy.deepcopy(STARTUP_STATUS)


def remove_startup_status() -> None:
    try:
        STARTUP_FILE.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("Could not remove Python startup status: %s", STARTUP_FILE)


def log_exception(prefix: str) -> None:
    """Выводит полный traceback в консоль при запуске через python.exe."""

    LOGGER.error("%s\n%s", prefix, traceback.format_exc())


def _subprocess_options() -> Dict[str, Any]:
    options: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creation_flags:
        options["creationflags"] = creation_flags
    return options


def _run_python_module(arguments: Sequence[str], timeout: int = 10 * 60) -> bool:
    command = [sys.executable, "-m"] + list(arguments)
    LOGGER.info("Starting Python command: %s", " ".join(command))
    try:
        completed = subprocess.run(command, timeout=timeout, **_subprocess_options())
    except Exception:
        log_exception("Could not start the Python command")
        return False
    output = str(completed.stdout or "").strip()
    if output:
        LOGGER.info("Python command output\n%s", output[-12000:])
    return completed.returncode == 0


def ensure_python_module(import_name: str, package_name: str = "") -> Any:
    """Импортирует модуль и при необходимости устанавливает его через pip."""

    try:
        return importlib.import_module(import_name)
    except ImportError:
        pass

    package = package_name or import_name
    LOGGER.info("Module %s was not found; starting automatic installation of %s", import_name, package)
    # Состояние installing публикуется только после реального ImportError.
    # Обычный запуск с уже установленным модулем не показывает этот этап JSX.
    write_startup_status("installing", package)

    if not _run_python_module(["pip", "--version"], timeout=60):
        LOGGER.info("pip is unavailable; running ensurepip")
        if not _run_python_module(["ensurepip", "--upgrade"], timeout=5 * 60):
            raise UserVisibleError(
                f"Could not prepare pip to install module {package}. "
                f"Details: {LOG_FILE}"
            )

    installed = _run_python_module(["pip", "install", "--disable-pip-version-check", package])
    if not installed:
        LOGGER.info("Regular installation failed; retrying with --user")
        installed = _run_python_module(
            ["pip", "install", "--user", "--disable-pip-version-check", package]
        )
    if not installed:
        raise UserVisibleError(
            f"Could not automatically install Python module {package}. "
            f"Check the internet connection and log: {LOG_FILE}"
        )

    importlib.invalidate_caches()
    try:
        module = importlib.import_module(import_name)
    except ImportError as exc:
        raise UserVisibleError(
            f"Module {package} was installed, but Python could not import it. "
            f"Restart {APP_NAME}. Log: {LOG_FILE}"
        ) from exc
    LOGGER.info("Module %s was installed and loaded successfully", package)
    write_startup_status("starting", "Preparing required Python modules")
    return module


DEEP_TRANSLATOR_MODULE: Any = None
PIL_IMAGE_MODULE: Any = None
PIL_IMAGE_OPS_MODULE: Any = None
WEBSOCKET_MODULE: Any = None


def prepare_required_modules() -> None:
    """Checks and installs all third-party modules required by the helper.

    The local API socket is already open while this function runs. JSX polls
    the lightweight ping command and receives installing only after a real
    ImportError. Other API commands remain gated until the state becomes ready.
    """

    global DEEP_TRANSLATOR_MODULE, PIL_IMAGE_MODULE, PIL_IMAGE_OPS_MODULE
    global WEBSOCKET_MODULE

    errors: List[str] = []

    try:
        DEEP_TRANSLATOR_MODULE = ensure_python_module(
            "deep_translator",
            "deep-translator",
        )
    except Exception as exc:
        errors.append(f"deep-translator: {exc}")

    try:
        PIL_IMAGE_MODULE = ensure_python_module("PIL.Image", "Pillow")
        # Pillow is already installed at this point; importing ImageOps should
        # not start another pip operation.
        PIL_IMAGE_OPS_MODULE = importlib.import_module("PIL.ImageOps")
    except Exception as exc:
        errors.append(f"Pillow: {exc}")

    # WebSocket улучшает только определение момента начала sampling. Если его
    # установить не удалось, генерация сохраняет полностью рабочий HTTP-путь.
    try:
        WEBSOCKET_MODULE = ensure_python_module("websocket", "websocket-client")
        if not hasattr(WEBSOCKET_MODULE, "create_connection"):
            raise UserVisibleError(
                "The installed websocket module is not websocket-client."
            )
    except Exception as exc:
        WEBSOCKET_MODULE = None
        LOGGER.warning(
            "websocket-client is unavailable; Comfy progress will use HTTP fallback: %s",
            exc,
        )

    if errors:
        raise UserVisibleError(
            "Could not prepare required Python modules:\n"
            + "\n".join(f"- {item}" for item in errors)
            + f"\n\nDetails: {LOG_FILE}"
        )

    LOGGER.info(
        "Required Python modules are ready: deep-translator, Pillow%s",
        ", websocket-client" if WEBSOCKET_MODULE is not None else "",
    )


def now_timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_workflow_id(relative_path: str) -> str:
    """ID зависит от относительного пути, а не от содержимого workflow.

    Благодаря этому редактирование JSON не создаёт новый профиль Photoshop.
    Хеш содержимого хранится отдельно и используется для сброса анализа.
    """

    normalized = relative_path.replace("\\", "/").lower().encode("utf-8")
    return "w_" + hashlib.sha1(normalized).hexdigest()[:16]


def json_dumps(value: Any) -> str:
    """Компактный UTF-8 JSON для HTTP API и внутренних файлов."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def api_json_dumps(value: Any) -> str:
    """ASCII-only JSON для Socket/eval JSON-стека ExtendScript.

    ensure_ascii=True экранирует не только U+2028/U+2029, но и NEL U+0085,
    нестандартные пробелы, emoji и любые другие Unicode-символы, которые
    отдельные версии ExtendScript могут неверно обработать внутри readln/eval.
    """

    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def format_http_error_body(raw_body: str, limit: int = 12000) -> str:
    """Преобразует JSON-тело HTTP-ошибки в читаемый многострочный текст.

    Forge и Comfy возвращают поля ``message``/``detail`` как JSON-строки,
    поэтому переносы в сыром HTTP body представлены последовательностями
    ``\n``. После json.loads они снова становятся настоящими переводами строк.
    Табуляция намеренно заменяется четырьмя пробелами, чтобы в диалоге JSX не
    было управляющего символа ``\t``. Не-JSON ответы сохраняются как текст.
    """

    text = str(raw_body or "").strip()
    if not text:
        return ""

    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        formatted = text
    else:
        if isinstance(payload, str):
            formatted = payload
        elif isinstance(payload, dict):
            parts: List[str] = []
            for key in ("error", "detail", "message", "body"):
                value = payload.get(key)
                if value is None or value == "" or value == [] or value == {}:
                    continue
                if isinstance(value, str):
                    rendered = value
                else:
                    rendered = json.dumps(value, ensure_ascii=False, indent=2)
                if key == "message":
                    parts.append(rendered)
                else:
                    parts.append(f"{key}: {rendered}")
            formatted = "\n\n".join(parts) if parts else text
        else:
            formatted = text

    # В окне ошибки сохраняем только переводы строк. Реальные табы и
    # оставшиеся текстовые escape-последовательности \t заменяем пробелами.
    formatted = formatted.replace("\r\n", "\n").replace("\r", "\n")
    formatted = formatted.replace("\t", "    ").replace("\\t", "    ")

    if len(formatted) > limit:
        formatted = formatted[:limit].rstrip() + "\n\n… message truncated"
    return formatted


def normalize_output_format(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "jpeg":
        text = "jpg"
    return "png" if text == "png" else "jpg"


def _detect_image_suffix(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content[:2] == b"\xff\xd8":
        return ".jpg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


def _save_image_content_for_photoshop(
    content: bytes,
    destination_without_suffix: Path,
    output_format: Any,
) -> Path:
    requested = normalize_output_format(output_format)
    detected_suffix = _detect_image_suffix(content)
    target_suffix = ".png" if requested == "png" else ".jpg"
    destination = destination_without_suffix.with_suffix(target_suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if detected_suffix == target_suffix:
        destination.write_bytes(content)
        return destination
    image_module = PIL_IMAGE_MODULE
    image_ops_module = PIL_IMAGE_OPS_MODULE
    if image_module is None or image_ops_module is None:
        raise UserVisibleError(
            "Pillow was not initialized during Python startup. "
            f"Restart {APP_NAME}. Log: {LOG_FILE}"
        )
    try:
        with image_module.open(io.BytesIO(content)) as source:
            source.load()
            image = image_ops_module.exif_transpose(source)
            if requested == "png":
                bands = image.getbands()
                if "A" in bands or image.mode in {"P", "LA"}:
                    image = image.convert("RGBA")
                else:
                    image = image.convert("RGB")
                image.save(str(destination), format="PNG", compress_level=6)
            else:
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(str(destination), format="JPEG", quality=95)
    except Exception as exc:
        raise UserVisibleError("Could not convert the generated image for Photoshop.") from exc
    return destination


def is_link(value: Any) -> bool:
    """Проверяет API-связь ComfyUI вида ["node_id", source_slot]."""

    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    )


def scalar_value(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def title_has_tag(title: str, tag_name: str) -> bool:
    for pattern in TAG_PATTERNS.get(tag_name, []):
        if re.search(pattern, title or "", flags=re.IGNORECASE):
            return True
    return False


def strip_helper_tags(title: str) -> str:
    result = title or ""
    for patterns in TAG_PATTERNS.values():
        for pattern in patterns:
            result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", result).strip()


def safe_filename(value: str, fallback: str = "result") -> str:
    result = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", value or "")
    result = result.strip(" ._")
    return result[:100] or fallback


def clamp_number(value: float, minimum: Optional[float], maximum: Optional[float]) -> float:
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def parse_user_float(value: Any) -> float:
    """Parse a user-entered number accepting both decimal separators."""

    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    return float(value)


def parse_user_int(value: Any) -> int:
    """Parse an integer without losing precision; ``12,0`` is accepted."""

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError
        return int(value)
    text = str(value).strip().replace(",", ".")
    match = re.fullmatch(r"([+-]?\d+)(?:\.0+)?", text)
    if not match:
        raise ValueError
    return int(match.group(1))


def deep_get(mapping: Dict[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def cleanup_old_temp_files() -> None:
    """Удаляет старые каталоги запросов, оставшиеся после аварий."""

    threshold = time.time() - TEMP_MAX_AGE_SECONDS
    try:
        for child in TEMP_DIR.iterdir():
            try:
                if child.stat().st_mtime < threshold:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Could not inspect temporary file: %s", child)
    except OSError:
        LOGGER.warning("Could not clean temporary folder %s", TEMP_DIR)


def _existing_directory(value: Any) -> Optional[Path]:
    if value in (None, ""):
        return None
    try:
        path = Path(str(value)).expanduser().resolve()
        return path if path.is_dir() else None
    except OSError:
        return None


def _cli_path(argv: Sequence[Any], option: str) -> Optional[Path]:
    for index, raw in enumerate(argv):
        value = str(raw)
        if value == option and index + 1 < len(argv):
            return Path(str(argv[index + 1])).expanduser()
        if value.startswith(option + "="):
            return Path(value.split("=", 1)[1]).expanduser()
    return None


def _windows_listener_executable(port: int) -> Optional[Path]:
    if os.name != "nt":
        return None
    try:
        output = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=5,
        )
        pid = None
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
                continue
            try:
                local_port = int(parts[1].rsplit(":", 1)[1])
            except (ValueError, IndexError):
                continue
            if local_port == int(port):
                pid = int(parts[4])
                break
        if pid is None:
            return None
        command = (
            '$p=Get-CimInstance Win32_Process -Filter "ProcessId=' + str(pid) + '";'
            'if($p){$p.ExecutablePath}'
        )
        executable = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=5,
        ).strip()
        executable_path = Path(executable).resolve()
        return executable_path if executable_path.is_file() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def normalize_comfy_host(value: Any) -> str:
    host = str(value or DEFAULT_COMFY_HOST).strip()
    if "://" in host:
        parsed = urllib.parse.urlparse(host)
        host = parsed.hostname or DEFAULT_COMFY_HOST
    return host.strip("[]/") or DEFAULT_COMFY_HOST


def is_local_comfy_host(host: str) -> bool:
    normalized = normalize_comfy_host(host).lower()
    if normalized in {"127.0.0.1", "localhost", "::1"}:
        return True
    try:
        target_addresses = {
            item[4][0].split("%", 1)[0]
            for item in socket.getaddrinfo(normalized, None)
        }
        local_addresses = {"127.0.0.1", "::1"}
        for local_name in {socket.gethostname(), socket.getfqdn()}:
            for item in socket.getaddrinfo(local_name, None):
                local_addresses.add(item[4][0].split("%", 1)[0])
        return bool(target_addresses & local_addresses)
    except OSError:
        return False


def _detect_comfy_folder(
    stats: Optional[Dict[str, Any]],
    host: str,
    port: int,
    option: str,
    default_name: str,
) -> Optional[Path]:
    if not is_local_comfy_host(host):
        return None

    argv = deep_get(stats or {}, ("system", "argv"), [])
    if not isinstance(argv, list):
        argv = []
    executable = _windows_listener_executable(port)
    roots: List[Path] = []

    script_arg = next((Path(str(item)) for item in argv if str(item).lower().endswith("main.py")), None)
    if script_arg:
        if script_arg.is_absolute() and script_arg.is_file():
            roots.append(script_arg.parent.resolve())
        elif executable:
            for base in (executable.parent, executable.parent.parent, executable.parent.parent.parent):
                candidates = [(base / script_arg).resolve()]
                if script_arg.name.lower() == "main.py":
                    candidates.append((base / "ComfyUI" / script_arg.name).resolve())
                for candidate in candidates:
                    if candidate.is_file():
                        roots.append(candidate.parent)

    for env_name in ("COMFYUI_PATH", "COMFYUI_DIR"):
        env_root = _existing_directory(os.environ.get(env_name))
        if env_root:
            roots.append(env_root)

    explicit = _cli_path(argv, option)
    if explicit:
        if explicit.is_absolute():
            found = _existing_directory(explicit)
            if found:
                return found
        for root in roots:
            found = _existing_directory(root / explicit)
            if found:
                return found

    for root in roots:
        found = _existing_directory(root / default_name)
        if found:
            return found
    return None


def detect_comfy_input_folder(
    stats: Optional[Dict[str, Any]],
    host: str,
    port: int,
) -> Optional[Path]:
    return _detect_comfy_folder(stats, host, port, "--input-directory", "input")


def detect_comfy_output_folder(
    stats: Optional[Dict[str, Any]],
    host: str,
    port: int,
) -> Optional[Path]:
    return _detect_comfy_folder(stats, host, port, "--output-directory", "output")


def cleanup_stale_comfy_outputs(output_folder: Optional[Path]) -> None:
    """Remove leftovers from the helper-owned ComfyUI output subfolder.

    Only files older than ``TEMP_MAX_AGE_SECONDS`` are removed. Current request
    files are cleaned separately by ``generation_context`` using request_id.
    """

    root = _existing_directory(output_folder)
    if not root:
        return
    base = (root / OUTPUT_SUBFOLDER).resolve()
    if not base.is_dir():
        return
    threshold = time.time() - TEMP_MAX_AGE_SECONDS
    try:
        for child in base.rglob("*"):
            try:
                if child.is_file() and child.stat().st_mtime < threshold:
                    child.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Could not inspect or delete old ComfyUI output file: %s", child)
        for child in sorted((item for item in base.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
            try:
                child.rmdir()
            except OSError:
                pass
    except OSError:
        LOGGER.warning("Could not inspect ComfyUI helper output folder: %s", base)


def _comfy_request_output_path(
    path: Path, output_folder: Optional[Path], request_id: str
) -> Optional[Path]:
    """Return resolved path only for this helper-owned Comfy request output."""

    root = _existing_directory(output_folder)
    prefix = safe_filename(request_id)
    if not root or not prefix:
        return None
    try:
        base = (root / OUTPUT_SUBFOLDER).resolve()
        resolved = Path(path).resolve()
        relative = resolved.relative_to(base)
    except (OSError, ValueError):
        return None
    if not any(str(part).startswith(prefix) for part in relative.parts):
        return None
    return resolved


def cleanup_comfy_request_outputs(
    output_folder: Optional[Path],
    request_id: str,
    preserve_path: Optional[Path] = None,
) -> None:
    root = _existing_directory(output_folder)
    if not root:
        return
    base = (root / OUTPUT_SUBFOLDER).resolve()
    if not base.is_dir():
        return
    prefix = safe_filename(request_id)
    if not prefix:
        return
    preserved = (
        _comfy_request_output_path(preserve_path, output_folder, request_id)
        if preserve_path is not None
        else None
    )
    try:
        for target in base.rglob("*"):
            try:
                resolved = target.resolve()
                if not resolved.is_file() or (resolved.parent != base and base not in resolved.parents):
                    continue
                relative = resolved.relative_to(base)
                if not any(str(part).startswith(prefix) for part in relative.parts):
                    continue
                if preserved is not None and resolved == preserved:
                    continue
                resolved.unlink(missing_ok=True)
            except (OSError, ValueError):
                LOGGER.warning("Could not delete ComfyUI generated output: %s", target)
        for child in sorted((item for item in base.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
            try:
                child.rmdir()
            except OSError:
                pass
    except OSError:
        LOGGER.warning("Could not clean ComfyUI output for request %s", request_id)


def cleanup_stale_comfy_uploads(input_folder: Path) -> None:
    base = input_folder / UPLOAD_SUBFOLDER
    if not base.is_dir():
        return
    threshold = time.time() - TEMP_MAX_AGE_SECONDS
    for child in base.iterdir():
        try:
            if child.is_file() and child.stat().st_mtime < threshold:
                child.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not delete old ComfyUI input file: %s", child)


def cleanup_uploaded_images(input_folder: Optional[Path], images: Sequence[Dict[str, Any]]) -> None:
    root = _existing_directory(input_folder)
    if not root:
        return
    base = (root / UPLOAD_SUBFOLDER).resolve()
    for image in images:
        if not isinstance(image, dict) or str(image.get("type", "input")) != "input":
            continue
        name = str(image.get("name") or "")
        subfolder = str(image.get("subfolder") or "").replace("\\", "/").strip("/")
        if not name or subfolder != UPLOAD_SUBFOLDER:
            continue
        target = (base / name).resolve()
        try:
            if target.parent != base:
                continue
            target.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not delete ComfyUI input file: %s", target)


COMFY_FOLDER_CLEANUP_LOCK = threading.Lock()
COMFY_FOLDER_CLEANUP_PATHS: Set[Tuple[str, str]] = set()


def schedule_comfy_folder_cleanup(
    input_folder: Optional[Path], output_folder: Optional[Path]
) -> None:
    """Schedule one age-limited cleanup for each detected local ComfyUI pair."""

    input_root = _existing_directory(input_folder)
    output_root = _existing_directory(output_folder)
    if not input_root and not output_root:
        return
    key = (
        os.path.normcase(str(input_root.resolve())) if input_root else "",
        os.path.normcase(str(output_root.resolve())) if output_root else "",
    )
    with COMFY_FOLDER_CLEANUP_LOCK:
        if key in COMFY_FOLDER_CLEANUP_PATHS:
            return
        COMFY_FOLDER_CLEANUP_PATHS.add(key)

    def worker() -> None:
        try:
            if input_root:
                cleanup_stale_comfy_uploads(input_root)
            if output_root:
                cleanup_stale_comfy_outputs(output_root)
        except Exception:
            log_exception("Background ComfyUI folder cleanup failed")

    threading.Thread(
        target=worker,
        name="ComfyFolderCleanup",
        daemon=True,
    ).start()


class UserVisibleError(RuntimeError):
    """Expected failure returned to JSX without a technical traceback."""

    def __init__(
        self,
        message: str,
        code: str = "",
        params: Optional[Sequence[Any]] = None,
    ) -> None:
        super().__init__(str(message or ""))
        self.code = str(code or "")
        self.params = [str(value) for value in params] if params else []


class CancelledError(UserVisibleError):
    """Генерация отменена пользователем."""


# ============================================================================
# HTTP-КЛИЕНТ COMFYUI
# Только транспорт: ping, upload, queue/history, interrupt и загрузка результата.
# Анализ и изменение workflow выполняются отдельными классами ниже.
# ============================================================================
class ComfyClient:
    """Минимальный HTTP-клиент ComfyUI."""

    def __init__(
        self,
        host: str = DEFAULT_COMFY_HOST,
        port: int = 8188,
        timeout: float = 15.0,
    ):
        self.host = normalize_comfy_host(host)
        self.port = int(port)
        self.timeout = float(timeout)
        url_host = f"[{self.host}]" if ":" in self.host else self.host
        self.base_url = f"http://{url_host}:{self.port}"

    def _url(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> bytes:
        request = urllib.request.Request(
            self._url(path),
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            details = format_http_error_body(body)
            suffix = f"\n\n{details}" if details else ""
            raise UserVisibleError(
                f"ComfyUI returned HTTP {exc.code} for {path}{suffix}"
            ) from exc
        except urllib.error.URLError as exc:
            raise UserVisibleError(
                f"Cannot connect to ComfyUI at {self.base_url}. "
                f"Make sure ComfyUI is running. ({exc.reason})"
            ) from exc

    def get_json(self, path: str, timeout: Optional[float] = None) -> Any:
        raw = self._request("GET", path, timeout=timeout)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UserVisibleError(f"ComfyUI returned invalid JSON for {path}.") from exc

    def post_json(self, path: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Any:
        raw = self._request(
            "POST",
            path,
            data=json_dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=timeout,
        )
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UserVisibleError(f"ComfyUI returned invalid JSON for {path}.") from exc

    def ping(self, timeout: float = 3.0) -> Dict[str, Any]:
        """Проверяет сервер. /system_stats заодно даёт версию/устройства."""

        data = self.get_json("/system_stats", timeout=timeout)
        return data if isinstance(data, dict) else {"available": True}

    def get_object_info(self) -> Dict[str, Any]:
        data = self.get_json("/object_info", timeout=60)
        if not isinstance(data, dict):
            raise UserVisibleError("The /object_info response has an unexpected format.")
        return data

    def upload_image(self, source: Path, remote_name: str, subfolder: str) -> Dict[str, Any]:
        """Загружает JPEG/изображение в ComfyUI/input через multipart/form-data."""

        if not source.exists():
            raise UserVisibleError(f"Input image was not found: {source}")

        boundary = "----Img2imgHelper" + uuid.uuid4().hex
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        file_bytes = source.read_bytes()

        parts: List[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ])

        add_field("overwrite", "true")
        add_field("type", "input")
        add_field("subfolder", subfolder)

        parts.extend([
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="image"; '
                f'filename="{remote_name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])

        raw = self._request(
            "POST",
            "/upload/image",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=120,
        )
        try:
            result = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise UserVisibleError("ComfyUI did not return uploaded image metadata.") from exc
        if not isinstance(result, dict) or not result.get("name"):
            raise UserVisibleError(f"Unexpected /upload/image response: {result!r}")
        return result

    def queue_prompt(self, workflow: Dict[str, Any], client_id: str, prompt_id: str) -> Dict[str, Any]:
        result = self.post_json(
            "/prompt",
            {
                "prompt": workflow,
                "client_id": client_id,
                "prompt_id": prompt_id,
            },
            timeout=60,
        )
        if not isinstance(result, dict):
            raise UserVisibleError("ComfyUI did not return queued task data.")
        if result.get("error"):
            raise UserVisibleError(self.format_prompt_error(result))
        if result.get("node_errors"):
            raise UserVisibleError(self.format_node_errors(result.get("node_errors")))
        if not result.get("prompt_id"):
            raise UserVisibleError(f"ComfyUI did not return prompt_id: {result!r}")
        return result

    def get_history(self, prompt_id: str) -> Dict[str, Any]:
        result = self.get_json(f"/history/{urllib.parse.quote(prompt_id)}", timeout=30)
        return result if isinstance(result, dict) else {}

    def get_queue(self) -> Dict[str, Any]:
        result = self.get_json("/queue", timeout=15)
        return result if isinstance(result, dict) else {}

    def interrupt(self, prompt_id: Optional[str] = None) -> None:
        payload = {"prompt_id": prompt_id} if prompt_id else {}
        try:
            self.post_json("/interrupt", payload, timeout=10)
        except UserVisibleError:
            self._request(
                "POST",
                "/interrupt",
                data=json_dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=10,
            )

    def delete_queued_prompt(self, prompt_id: str) -> None:
        self.post_json("/queue", {"delete": [prompt_id]}, timeout=15)

    def download_image_for_photoshop(
        self,
        image_info: Dict[str, Any],
        destination: Path,
        quality: int = 95,
        output_format: Any = "jpg",
        local_output_folder: Optional[Path] = None,
        request_id: str = "",
    ) -> Path:
        """Скачивает output в формате, удобном для размещения в Photoshop.

        ``jpg`` использует быстрый ``preview=jpeg`` и при необходимости
        локально конвертирует fallback PNG/WebP. ``png`` скачивает исходный
        output, чтобы по возможности сохранить transparency.
        """

        quality = max(1, min(100, int(quality)))
        requested_format = normalize_output_format(output_format)
        base_query = {
            "filename": image_info.get("filename", ""),
            "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "output"),
        }

        # Для локального ComfyUI читаем output прямо с диска. Любая ошибка,
        # нестандартный type или небезопасный путь оставляют проверенный /view.
        local_root = _existing_directory(local_output_folder)
        if local_root and str(base_query["type"]) == "output":
            try:
                root = local_root.resolve()
                local_source = (
                    root
                    / str(base_query["subfolder"] or "").replace("\\", "/").strip("/")
                    / str(base_query["filename"] or "")
                ).resolve()
                local_source.relative_to(root)
                if local_source.is_file():
                    # Если Comfy уже сохранил helper-owned output в нужном
                    # Photoshop формате, возвращаем этот файл напрямую.
                    # generation_context сохранит его до Place, а JSX удалит
                    # после успешного размещения. Читаем только magic header.
                    owned_source = _comfy_request_output_path(
                        local_source, local_root, request_id
                    )
                    if owned_source is not None:
                        with owned_source.open("rb") as stream:
                            detected_suffix = _detect_image_suffix(stream.read(12))
                        target_suffix = ".png" if requested_format == "png" else ".jpg"
                        if detected_suffix == target_suffix:
                            return owned_source
                    return _save_image_content_for_photoshop(
                        local_source.read_bytes(), destination, requested_format
                    )
            except (OSError, ValueError, UserVisibleError) as exc:
                LOGGER.debug("Direct ComfyUI output read failed; using /view: %s", exc)

        if requested_format == "png":
            raw = self._request(
                "GET",
                "/view?" + urllib.parse.urlencode(base_query),
                timeout=120,
            )
            if _detect_image_suffix(raw) == ".bin":
                raise UserVisibleError(
                    "ComfyUI returned an unknown image format through /view."
                )
            return _save_image_content_for_photoshop(raw, destination, "png")

        preview_raw: Optional[bytes] = None
        preview_query = dict(base_query)
        preview_query.update({"preview": f"jpeg;{quality}", "channel": "rgb"})
        try:
            preview_raw = self._request(
                "GET",
                "/view?" + urllib.parse.urlencode(preview_query),
                timeout=120,
            )
        except UserVisibleError:
            preview_raw = None

        if preview_raw and preview_raw[:2] == b"\xff\xd8":
            destination = destination.with_suffix(".jpg")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(preview_raw)
            return destination

        rgb_raw: Optional[bytes] = None
        rgb_query = dict(base_query)
        rgb_query["channel"] = "rgb"
        try:
            rgb_raw = self._request(
                "GET",
                "/view?" + urllib.parse.urlencode(rgb_query),
                timeout=120,
            )
        except UserVisibleError:
            rgb_raw = None

        if rgb_raw:
            if _detect_image_suffix(rgb_raw) == ".bin":
                raise UserVisibleError(
                    "ComfyUI returned an unknown image format through /view."
                )
            return _save_image_content_for_photoshop(rgb_raw, destination, "jpg")

        raw = self._request(
            "GET",
            "/view?" + urllib.parse.urlencode(base_query),
            timeout=120,
        )
        if _detect_image_suffix(raw) == ".bin":
            raise UserVisibleError(
                "ComfyUI returned an unknown image format through /view."
            )
        return _save_image_content_for_photoshop(raw, destination, "jpg")

    def format_node_errors(node_errors: Any) -> str:
        if not isinstance(node_errors, dict):
            return f"Workflow validation error: {node_errors}"
        lines = ["ComfyUI rejected the workflow:"]
        for node_id, data in node_errors.items():
            title = deep_get(data, ["class_type"], "")
            errors = data.get("errors", []) if isinstance(data, dict) else []
            lines.append(f"\nNode {node_id}{' — ' + str(title) if title else ''}")
            for item in errors:
                if isinstance(item, dict):
                    message = item.get("message") or item.get("type") or str(item)
                    details = item.get("details")
                    lines.append("  • " + str(message) + (f": {details}" if details else ""))
                else:
                    lines.append("  • " + str(item))
        return "\n".join(lines)

    @staticmethod
    def format_prompt_error(result: Dict[str, Any]) -> str:
        error = result.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or str(error)
            details = error.get("details")
            return str(message) + (f"\n{details}" if details else "")
        return str(error)


class ComfyProgressWatcher:
    """Observe sampler progress through ComfyUI WebSocket without owning result delivery."""

    MAX_MESSAGES_PER_POLL = 32

    def __init__(
        self,
        client: ComfyClient,
        client_id: str,
        sampler_node_ids: Sequence[Any],
    ) -> None:
        self.client = client
        self.client_id = str(client_id)
        self.sampler_node_ids = {
            str(item) for item in sampler_node_ids if str(item)
        }
        self.socket: Any = None
        self.sampler_entered: Optional[str] = None

    @property
    def can_track_sampling(self) -> bool:
        return self.socket is not None and bool(self.sampler_node_ids)

    def connect(self) -> bool:
        module = WEBSOCKET_MODULE
        if module is None or not self.sampler_node_ids:
            return False
        websocket_url = self.client.base_url.replace("http://", "ws://", 1)
        websocket_url += "/ws?" + urllib.parse.urlencode(
            {"clientId": self.client_id}
        )
        try:
            self.socket = module.create_connection(
                websocket_url,
                timeout=3,
                enable_multithread=False,
            )
            self.socket.settimeout(0.01)
            LOGGER.info(
                "Comfy progress WebSocket connected: samplers=%s",
                ",".join(sorted(self.sampler_node_ids)),
            )
            return True
        except Exception as exc:
            self.socket = None
            LOGGER.warning(
                "Comfy progress WebSocket is unavailable; using HTTP fallback: %s",
                exc,
            )
            return False

    def _disable(self, reason: Any) -> None:
        LOGGER.warning(
            "Comfy progress WebSocket disconnected; using HTTP fallback: %s",
            reason,
        )
        self.close()

    def sampling_started(self, prompt_id: str) -> bool:
        """Return true on sampler progress or after a non-reporting sampler exits."""

        if not self.can_track_sampling:
            return False
        module = WEBSOCKET_MODULE
        timeout_type = getattr(module, "WebSocketTimeoutException", ())
        for _ in range(self.MAX_MESSAGES_PER_POLL):
            try:
                raw = self.socket.recv()
            except Exception as exc:
                if timeout_type and isinstance(exc, timeout_type):
                    break
                self._disable(exc)
                return False
            if raw in (None, ""):
                self._disable("connection closed")
                return False
            if not isinstance(raw, str):
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            data = message.get("data")
            if not isinstance(data, dict):
                continue
            event_prompt_id = str(data.get("prompt_id") or "")
            if event_prompt_id and event_prompt_id != str(prompt_id):
                continue
            event_type = str(message.get("type") or "")
            node_id = str(data.get("node") or "")
            is_sampler_progress = node_id in self.sampler_node_ids or (
                not node_id and self.sampler_entered is not None
            )
            if event_type == "progress" and is_sampler_progress:
                try:
                    progress_value = float(data.get("value") or 0)
                except (TypeError, ValueError):
                    progress_value = 0
                if progress_value > 0:
                    return True
            elif event_type == "executing":
                if node_id in self.sampler_node_ids:
                    if self.sampler_entered and node_id != self.sampler_entered:
                        return True
                    self.sampler_entered = node_id
                elif self.sampler_entered:
                    # Custom sampler did not emit progress, but execution has
                    # already moved to the following node.
                    return True
            elif event_type == "execution_success":
                return True
        return False

    def close(self) -> None:
        current = self.socket
        self.socket = None
        if current is not None:
            try:
                current.close()
            except Exception:
                pass


@dataclass
# ============================================================================
# ХРАНИЛИЩЕ API-WORKFLOW
# ID зависит от относительного пути, а hash содержимого служит только для cache.
# ============================================================================
class WorkflowFile:
    workflow_id: str
    name: str
    relative_path: str
    absolute_path: Path
    size: int
    modified_ns: int
    # Хеш вычисляется только для выбранного workflow во время полного анализа.
    sha256: str = ""

    def public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.workflow_id,
            "name": self.name,
            "relative_path": self.relative_path,
        }


class WorkflowRepository:
    """Находит workflow в папке, выбранной пользователем.

    Быстрый путь принимает относительное имя выбранного workflow от JSX и не
    сканирует всю папку. Полное рекурсивное сканирование выполняется только при
    первом запуске, смене папки или нажатии кнопки обновления списка.
    """

    def __init__(self, folder: Path):
        self.folder = folder

    def ensure_folder(self) -> None:
        if not self.folder:
            raise UserVisibleError(
                "Workflow folder is not set.",
                "workflow_folder_not_selected",
            )
        if not self.folder.exists():
            raise UserVisibleError(
                f"Workflow folder does not exist: {self.folder}",
                "workflow_folder_missing",
                [self.folder],
            )
        if not self.folder.is_dir():
            raise UserVisibleError(
                f"Workflow path is not a folder: {self.folder}",
                "workflow_path_not_folder",
                [self.folder],
            )

    def _workflow_from_path(self, path: Path, *, compute_hash: bool = False) -> WorkflowFile:
        relative = path.relative_to(self.folder).as_posix()
        stat = path.stat()
        return WorkflowFile(
            workflow_id=stable_workflow_id(relative),
            name=path.stem,
            relative_path=relative,
            absolute_path=path,
            size=stat.st_size,
            modified_ns=getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            sha256=sha256_file(path) if compute_hash else "",
        )

    def list_workflows(self) -> List[WorkflowFile]:
        self.ensure_folder()
        result: List[WorkflowFile] = []
        for path in self.folder.rglob("*.json"):
            if any(part.startswith(".") for part in path.relative_to(self.folder).parts):
                continue
            if path.name.startswith("~") or path.name.endswith(".tmp.json"):
                continue
            try:
                # В списке нужны только метаданные. Содержимое и SHA-256 будут
                # прочитаны лишь для выбранного пользователем workflow.
                result.append(self._workflow_from_path(path, compute_hash=False))
            except OSError:
                LOGGER.warning("Could not read workflow: %s", path)
        result.sort(key=lambda item: item.relative_path.lower())
        return result

    def get(self, workflow_id: str, relative_path: str = "") -> WorkflowFile:
        self.ensure_folder()

        # Быстрый путь: JSX хранит relative_path выбранного workflow в .desc.
        # Проверяем, что путь не выходит за пределы разрешённой папки и что ID
        # совпадает. При успехе никакого rglob по остальным JSON не требуется.
        if relative_path:
            try:
                root = self.folder.resolve()
                candidate = (self.folder / relative_path).resolve()
                candidate.relative_to(root)
                if candidate.is_file() and candidate.suffix.lower() == ".json":
                    item = self._workflow_from_path(candidate, compute_hash=False)
                    if not workflow_id or item.workflow_id == workflow_id:
                        return item
            except (OSError, ValueError):
                pass

        # Резервный поиск нужен, если сохранённый relative_path недоступен.
        for item in self.list_workflows():
            if item.workflow_id == workflow_id:
                return item
        raise UserVisibleError(
            "The selected workflow is no longer present in the folder.",
            "selected_workflow_missing",
        )

    @staticmethod
    def ensure_hash(workflow_file: WorkflowFile) -> str:
        if not workflow_file.sha256:
            workflow_file.sha256 = sha256_file(workflow_file.absolute_path)
        return workflow_file.sha256

    @staticmethod
    def load_json(workflow_file: WorkflowFile) -> Dict[str, Any]:
        try:
            with workflow_file.absolute_path.open("r", encoding="utf-8-sig") as stream:
                data = json.load(stream)
        except json.JSONDecodeError as exc:
            raise UserVisibleError(
                f"JSON error in {workflow_file.relative_path}, line {exc.lineno}: {exc.msg}",
                "workflow_json_invalid",
                [workflow_file.relative_path, exc.lineno, exc.colno, exc.msg],
            ) from exc
        except OSError as exc:
            raise UserVisibleError(f"Could not read {workflow_file.absolute_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise UserVisibleError(
                "The API workflow root must be a JSON object.",
                "workflow_root_invalid",
                [workflow_file.relative_path],
            )
        return data


def write_json_atomic(
    path: Path,
    data: Dict[str, Any],
    description: str,
    error_code: str = "",
) -> None:
    """Write JSON beside the source and atomically replace the original file."""

    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise UserVisibleError(
            f"Could not write {description}:\n{path}\n\n"
            "The file or its folder may be protected from writing. Move the JSON "
            "to a writable folder or run Photoshop and the Python helper with "
            f"sufficient permissions.\n\n{exc}",
            error_code,
            [path, exc] if error_code else None,
        ) from exc


# ============================================================================
# НОРМАЛИЗАЦИЯ /object_info И ГРАФ WORKFLOW
# Эти классы дают анализатору единый доступ к типам inputs и связям между нодами.
# ============================================================================
class ObjectInfoSchema:
    """Обёртка над разными версиями ``/object_info``.

    Классические ноды возвращают ключ ``input`` с ``required/optional/hidden``.
    Новые определения v2 могут иметь немного другую форму. Здесь всё приводится
    к компактному описанию конкретного input.
    """

    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw

    def has_class(self, class_type: str) -> bool:
        return class_type in self.raw

    def class_info(self, class_type: str) -> Dict[str, Any]:
        info = self.raw.get(class_type, {})
        return info if isinstance(info, dict) else {}

    def display_name(self, class_type: str) -> str:
        info = self.class_info(class_type)
        return str(info.get("display_name") or info.get("name") or class_type)

    def is_output_node(self, class_type: str) -> bool:
        return bool(self.class_info(class_type).get("output_node"))

    def input_definition(self, class_type: str, input_name: str) -> Optional[Dict[str, Any]]:
        info = self.class_info(class_type)

        # Формат стандартного /object_info:
        # "input": {"required": {"steps": ["INT", {"min":1,...}]}}
        classic = info.get("input")
        if isinstance(classic, dict):
            for section in ("required", "optional", "hidden"):
                values = classic.get(section)
                if isinstance(values, dict) and input_name in values:
                    return self._normalize_definition(values[input_name], section)

        # Некоторые custom servers используют schema-поле "inputs".
        modern = info.get("inputs")
        if isinstance(modern, dict) and input_name in modern:
            value = modern[input_name]
            result = dict(value) if isinstance(value, dict) else {"raw": value}
            result.setdefault("section", "required")
            result.setdefault("type", result.get("type") or result.get("data_type"))
            return result

        return None

    @staticmethod
    def _normalize_definition(value: Any, section: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {"section": section, "raw": value}
        if isinstance(value, (list, tuple)) and value:
            type_value = value[0]
            options = value[1] if len(value) > 1 and isinstance(value[1], dict) else {}
            if isinstance(type_value, list):
                result["type"] = "ENUM"
                result["choices"] = list(type_value)
            else:
                result["type"] = str(type_value)
            result.update(options)
        elif isinstance(value, dict):
            result.update(value)
            result.setdefault("type", value.get("data_type"))
        else:
            result["type"] = str(value)
        return result

    def scalar_input_definitions(self, class_type: str) -> Dict[str, Dict[str, Any]]:
        info = self.class_info(class_type)
        result: Dict[str, Dict[str, Any]] = {}
        classic = info.get("input")
        if isinstance(classic, dict):
            for section in ("required", "optional"):
                values = classic.get(section)
                if isinstance(values, dict):
                    for name, value in values.items():
                        result[name] = self._normalize_definition(value, section)
        modern = info.get("inputs")
        if isinstance(modern, dict):
            for name in modern:
                definition = self.input_definition(class_type, name)
                if definition:
                    result[name] = definition
        return result


def build_validation_schema(
    workflow: Dict[str, Any],
    object_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Builds the small /object_info subset required by WorkflowPatcher.

    The disk analysis cache remains the primary fast path. Storing this subset
    prevents a cold Python process from inferring FLOAT/INT/ENUM semantics from
    current JSON literals when the full in-memory OBJECT_INFO_CACHE is empty.
    """

    source = ObjectInfoSchema(object_info)
    class_inputs: Dict[str, Set[str]] = {}

    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs")
        if not class_type or not isinstance(inputs, dict):
            continue
        class_inputs.setdefault(class_type, set()).update(str(name) for name in inputs)

    # Main LoadImage MASK is replaced by a temporary LoadImageMask only during
    # input_alpha generation, so that class may be absent from the source JSON.
    if source.has_class("LoadImageMask"):
        class_inputs.setdefault("LoadImageMask", set()).update(
            source.scalar_input_definitions("LoadImageMask").keys()
        )

    allowed_keys = {
        "section",
        "type",
        "choices",
        "min",
        "max",
        "step",
    }
    result: Dict[str, Any] = {}

    for class_type, input_names in class_inputs.items():
        if not source.has_class(class_type):
            continue
        compact_inputs: Dict[str, Any] = {}
        for input_name in sorted(input_names):
            definition = source.input_definition(class_type, input_name)
            if not isinstance(definition, dict):
                continue
            compact = {
                key: copy.deepcopy(definition[key])
                for key in allowed_keys
                if key in definition
            }
            if compact:
                compact_inputs[input_name] = compact
        result[class_type] = {"inputs": compact_inputs}

    return result


@dataclass(frozen=True)
class TargetBinding:
    node_id: str
    input_name: str

    def to_dict(self) -> Dict[str, str]:
        return {"node_id": self.node_id, "input": self.input_name}


@dataclass
class Candidate:
    id: str
    label: str
    targets: List[TargetBinding]
    score: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "targets": [target.to_dict() for target in self.targets],
            "score": self.score,
            "meta": self.meta,
        }


class WorkflowGraph:
    def __init__(self, workflow: Dict[str, Any]):
        self.workflow = workflow
        self.incoming: Dict[str, List[Tuple[str, int, str]]] = {}
        self.outgoing: Dict[str, List[Tuple[str, int, str]]] = {}
        for target_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                continue
            for input_name, value in inputs.items():
                if is_link(value):
                    source_id = str(value[0])
                    source_slot = int(value[1])
                    self.incoming.setdefault(str(target_id), []).append(
                        (source_id, source_slot, input_name)
                    )
                    self.outgoing.setdefault(source_id, []).append(
                        (str(target_id), source_slot, input_name)
                    )

    def upstream_nodes(self, start_id: str, max_depth: int = 20) -> Set[str]:
        visited: Set[str] = set()
        frontier = [(str(start_id), 0)]
        while frontier:
            node_id, depth = frontier.pop()
            if depth >= max_depth:
                continue
            for source_id, _, _ in self.incoming.get(node_id, []):
                if source_id not in visited:
                    visited.add(source_id)
                    frontier.append((source_id, depth + 1))
        return visited


# ============================================================================
# АНАЛИЗ COMFY WORKFLOW
# Находит input/mask/reference/output/size/sampler и UI-контролы. Автоматический
# выбор допускается только при однозначном безопасном кандидате; иначе используется
# source image или требуется явная настройка в Photoshop.
# ============================================================================
class WorkflowAnalyzer:
    """Преобразует произвольный API-workflow в понятный Photoshop профиль."""

    def __init__(self, workflow: Dict[str, Any], object_info: Dict[str, Any]):
        self.workflow = workflow
        self.schema = ObjectInfoSchema(object_info)
        self.graph = WorkflowGraph(workflow)
        self.diagnostics: List[Dict[str, Any]] = []


    def validate_api_format(self) -> None:
        if "nodes" in self.workflow and "links" in self.workflow:
            raise UserVisibleError(
                "The file uses the regular ComfyUI UI format. Open it in ComfyUI "
                "and choose Workflow/File → Export (API).",
                "api_workflow_required",
            )
        if not self.workflow:
            raise UserVisibleError("The workflow is empty.", "workflow_empty")

        invalid: List[str] = []
        missing_classes: Set[str] = set()
        for node_id, node in self.workflow.items():
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict) or not node.get("class_type"):
                invalid.append(str(node_id))
                continue
            class_type = str(node["class_type"])
            if not self.schema.has_class(class_type):
                missing_classes.add(class_type)

        if invalid:
            raise UserVisibleError(
                "Invalid API nodes without class_type/inputs: " + ", ".join(invalid[:20]),
                "invalid_api_nodes",
                [", ".join(invalid[:20])],
            )
        if missing_classes:
            for class_type in sorted(missing_classes):
                self.error(
                    f"The ComfyUI node is not installed: {class_type}",
                    "missing_node_class",
                    [class_type],
                )

    def node_title(self, node_id: str) -> str:
        node = self.workflow.get(str(node_id), {})
        meta = node.get("_meta", {}) if isinstance(node, dict) else {}
        custom = meta.get("title") if isinstance(meta, dict) else None
        class_type = str(node.get("class_type", "Unknown")) if isinstance(node, dict) else "Unknown"
        return str(custom or self.schema.display_name(class_type) or class_type)

    def node_label(self, node_id: str) -> str:
        """Returns a readable, unique label for a workflow node.

        ComfyUI API workflow keeps the user-visible node title in
        ``_meta.title``. Generic scalar controls previously ignored it and
        exposed only class names such as PrimitiveFloat. Keep class_type and
        node ID for diagnostics and duplicate titles, but put the familiar
        ComfyUI title first.
        """

        node = self.workflow[str(node_id)]
        class_type = str(node.get("class_type", "Unknown"))
        meta = node.get("_meta")
        title = (
            str(meta.get("title") or "").strip()
            if isinstance(meta, dict)
            else ""
        )
        if title and normalize_name(title) != normalize_name(class_type):
            return f"{title} — {class_type} [#{node_id}]"
        return f"{class_type} [#{node_id}]"

    def _add_diagnostic(
        self,
        level: str,
        message: str,
        code: str = "",
        params: Optional[Sequence[Any]] = None,
    ) -> None:
        item: Dict[str, Any] = {"level": level, "message": message}
        if code:
            item["code"] = code
        if params:
            item["params"] = [str(value) for value in params]
        self.diagnostics.append(item)

    def info(self, message: str, code: str = "", params: Optional[Sequence[Any]] = None) -> None:
        LOGGER.info("Workflow analysis: %s", message)
        self._add_diagnostic("info", message, code, params)

    def warning(self, message: str, code: str = "", params: Optional[Sequence[Any]] = None) -> None:
        LOGGER.warning("Workflow analysis: %s", message)
        self._add_diagnostic("warning", message, code, params)

    def error(self, message: str, code: str = "", params: Optional[Sequence[Any]] = None) -> None:
        LOGGER.error("Workflow analysis: %s", message)
        self._add_diagnostic("error", message, code, params)


    def input_candidates(self) -> List[Candidate]:
        result: List[Candidate] = []
        tagged_targets: List[TargetBinding] = []
        for node_id, node in self.workflow.items():
            class_type = str(node.get("class_type", ""))
            title = self.node_title(str(node_id))
            inputs = node.get("inputs", {})
            score = 0

            is_reference = title_has_tag(title, "reference")
            if title_has_tag(title, "input"):
                score += 1000
            class_norm = normalize_name(class_type)
            is_mask_loader = "loadimagemask" in class_norm or ("loadimage" in class_norm and "mask" in class_norm)
            if is_mask_loader:
                continue
            # Preserve the pre-existing input-candidate scoring for custom
            # LoadImage* classes. Only the exact standard LoadImage class is
            # marked for the new neutralization feature.
            is_load_image = class_norm == "loadimage"
            if "loadimage" in class_norm or "image_loader" in class_norm or class_norm == "imageinput":
                score += 300

            possible_names = []
            for input_name, value in inputs.items():
                if is_link(value):
                    continue
                definition = self.schema.input_definition(class_type, input_name) or {}
                input_type = str(definition.get("type", "")).upper()
                name_norm = normalize_name(input_name)
                if name_norm in {"image", "image_path", "filename", "file", "upload"}:
                    possible_names.append(input_name)
                    score += 60
                if input_type in {"IMAGEUPLOAD", "IMAGE"} and isinstance(value, str):
                    possible_names.append(input_name)
                    score += 100

            # У LoadImage тип поля часто COMBO/список файлов, поэтому class_type
            # является более надёжным признаком, а поле обычно называется image.
            if score and possible_names:
                unique = []
                for name in possible_names:
                    if name not in unique:
                        unique.append(name)
                for input_name in unique:
                    target = TargetBinding(str(node_id), input_name)
                    result.append(
                        Candidate(
                            id=f"{node_id}:{input_name}",
                            label=f"{self.node_label(str(node_id))} → {input_name}",
                            targets=[target],
                            score=score + (20 if input_name == "image" else 0) - (80 if is_reference else 0),
                            meta={"reference": is_reference, "tagged": title_has_tag(title, "input"), "load_image": is_load_image, "node_id": str(node_id), "input": input_name, "source_value": value},
                        )
                    )
                    if title_has_tag(title, "input"):
                        tagged_targets.append(target)
            elif title_has_tag(title, "input"):
                # Метка установлена, но поле не распознано: предлагаем все
                # локальные строковые поля, чтобы пользователь увидел проблему.
                for input_name, value in inputs.items():
                    if isinstance(value, str):
                        target = TargetBinding(str(node_id), input_name)
                        result.append(
                            Candidate(
                                id=f"{node_id}:{input_name}",
                                label=f"{self.node_label(str(node_id))} → {input_name}",
                                targets=[target],
                                score=score - (80 if is_reference else 0),
                                meta={"reference": is_reference, "tagged": title_has_tag(title, "input"), "load_image": is_load_image, "node_id": str(node_id), "input": input_name, "source_value": value},
                            )
                        )
                        tagged_targets.append(target)

        # Если пользователь отметил несколько LoadImage одинаковой меткой,
        # это обычно означает: одно JPEG из Photoshop нужно подставить во все
        # эти места (например, в основную и reference-ветку). Создаём один
        # логический кандидат с несколькими targets и даём ему самый высокий
        # приоритет. Отдельные ноды всё равно остаются в ручном списке.
        unique_tagged: List[TargetBinding] = []
        seen_tagged: Set[Tuple[str, str]] = set()
        for target in tagged_targets:
            key = (target.node_id, target.input_name)
            if key not in seen_tagged:
                seen_tagged.add(key)
                unique_tagged.append(target)
        if len(unique_tagged) > 1:
            joined_id = "tagged:" + "|".join(
                f"{target.node_id}:{target.input_name}" for target in unique_tagged
            )
            result.append(
                Candidate(
                    id=joined_id,
                    label=f"All #PS-INPUT nodes ({len(unique_tagged)})",
                    targets=unique_tagged,
                    score=5000,
                    meta={"tagged": True, "grouped": True, "reference": False},
                )
            )

        return sorted(result, key=lambda item: (-item.score, item.label.lower()))

    def input_alpha_mask_candidate(
        self, input_choice: Optional[Candidate]
    ) -> Optional[Candidate]:
        """Build the virtual MASK option belonging to one main image input."""

        input_nodes: List[str] = []
        if input_choice:
            for target in input_choice.targets:
                node_id = str(target.node_id)
                node = self.workflow.get(node_id, {})
                class_norm = normalize_name(node.get("class_type", "")) if isinstance(node, dict) else ""
                if "loadimage" in class_norm and "mask" not in class_norm and node_id not in input_nodes:
                    input_nodes.append(node_id)
        if not input_nodes:
            return None
        connected = any(
            any(
                source_slot == 1
                for _target_id, source_slot, _input_name in self.graph.outgoing.get(
                    node_id, []
                )
            )
            for node_id in input_nodes
        )
        return Candidate(
            id="input_alpha",
            label="Main LoadImage MASK",
            targets=[],
            score=4000 if connected else 200,
            meta={
                "mode": "input_alpha",
                "node_ids": input_nodes,
                "connected": connected,
            },
        )

    def load_image_mask_candidates(self) -> List[Candidate]:
        """Return workflow-wide LoadImageMask options independent of main input."""

        result: List[Candidate] = []
        tagged: List[Candidate] = []

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type", ""))
            class_norm = normalize_name(class_type)
            if "loadimagemask" not in class_norm and not ("loadimage" in class_norm and "mask" in class_norm):
                continue
            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                continue
            image_input = ""
            for input_name, value in inputs.items():
                if is_link(value):
                    continue
                if normalize_name(input_name) in {"image", "image_path", "filename", "file", "upload"} and isinstance(value, str):
                    image_input = input_name
                    break
            if not image_input:
                continue
            title = self.node_title(str(node_id))
            is_tagged = title_has_tag(title, "mask")
            connected = any(source_slot == 0 for _target_id, source_slot, _input_name in self.graph.outgoing.get(str(node_id), []))
            channel_targets: List[Dict[str, str]] = []
            if "channel" in inputs and not is_link(inputs.get("channel")):
                channel_targets.append(TargetBinding(str(node_id), "channel").to_dict())
            candidate = Candidate(
                id=f"{node_id}:{image_input}",
                label=f"{self.node_label(str(node_id))} → {image_input}",
                targets=[TargetBinding(str(node_id), image_input)],
                score=(5000 if is_tagged else 1000) + (100 if connected else 0),
                meta={
                    "mode": "load_image_mask",
                    "node_ids": [str(node_id)],
                    "channel_targets": channel_targets,
                    "connected": connected,
                    "tagged": is_tagged,
                },
            )
            result.append(candidate)
            if is_tagged:
                tagged.append(candidate)

        if len(tagged) > 1:
            targets: List[TargetBinding] = []
            channel_targets: List[Dict[str, str]] = []
            node_ids: List[str] = []
            for candidate in tagged:
                targets.extend(candidate.targets)
                channel_targets.extend(candidate.meta.get("channel_targets", []))
                node_ids.extend(candidate.meta.get("node_ids", []))
            result.append(
                Candidate(
                    id="mask_tagged:" + "|".join(f"{target.node_id}:{target.input_name}" for target in targets),
                    label=f"All #PS-MASK nodes ({len(tagged)})",
                    targets=targets,
                    score=6000,
                    meta={
                        "mode": "load_image_mask",
                        "node_ids": node_ids,
                        "channel_targets": channel_targets,
                        "connected": all(bool(candidate.meta.get("connected")) for candidate in tagged),
                        "tagged": True,
                    },
                )
            )

        return sorted(result, key=lambda item: (-item.score, item.label.lower()))

    def mask_candidates(
        self,
        input_choice: Optional[Candidate],
        common_candidates: Optional[Sequence[Candidate]] = None,
    ) -> List[Candidate]:
        """Combine the selected main-input MASK with common mask nodes."""

        result = list(
            common_candidates
            if common_candidates is not None
            else self.load_image_mask_candidates()
        )
        input_alpha = self.input_alpha_mask_candidate(input_choice)
        if input_alpha is not None:
            result.append(input_alpha)
        return sorted(result, key=lambda item: (-item.score, item.label.lower()))

    @staticmethod
    def choose_mask_candidate(candidates: List[Candidate], override_id: Optional[str]) -> Optional[Candidate]:
        if override_id:
            for candidate in candidates:
                if candidate.id == override_id:
                    return candidate
            return None
        tagged = [item for item in candidates if item.meta.get("tagged")]
        if tagged:
            return tagged[0]
        for candidate in candidates:
            if candidate.meta.get("mode") == "input_alpha" and candidate.meta.get("connected"):
                return candidate
        mask_nodes = [item for item in candidates if item.meta.get("mode") == "load_image_mask"]
        return mask_nodes[0] if len(mask_nodes) == 1 else None

    def output_candidates(self) -> List[Candidate]:
        result: List[Candidate] = []
        for node_id, node in self.workflow.items():
            class_type = str(node.get("class_type", ""))
            title = self.node_title(str(node_id))
            class_norm = normalize_name(class_type)
            score = 0
            if title_has_tag(title, "output"):
                score += 1000
            if self.schema.is_output_node(class_type):
                score += 300
            if "saveimage" in class_norm:
                score += 250
            elif "previewimage" in class_norm:
                score += 180
            elif "save" in class_norm and "image" in class_norm:
                score += 120

            if score:
                result.append(
                    Candidate(
                        id=str(node_id),
                        label=self.node_label(str(node_id)),
                        targets=[],
                        score=score,
                        meta={"node_id": str(node_id), "tagged": title_has_tag(title, "output")},
                    )
                )
        return sorted(result, key=lambda item: (-item.score, item.label.lower()))

    def _editable_numeric_target(self, node_id: str, input_name: str) -> Optional[TargetBinding]:
        """Находит реальное числовое поле, даже если width/height связаны.

        Пример:
            Resize.width <- PrimitiveInt.value

        В таком случае нужно менять не link в Resize, а value primitive-ноды.
        Поиск ограничен несколькими уровнями и выбирает очевидное локальное
        числовое поле источника.
        """

        current_id = str(node_id)
        current_input = input_name
        visited: Set[Tuple[str, str]] = set()
        for _ in range(8):
            key = (current_id, current_input)
            if key in visited:
                return None
            visited.add(key)
            node = self.workflow.get(current_id)
            if not isinstance(node, dict):
                return None
            inputs = node.get("inputs", {})
            value = inputs.get(current_input)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return TargetBinding(current_id, current_input)
            if not is_link(value):
                return None

            source_id = str(value[0])
            source = self.workflow.get(source_id)
            if not isinstance(source, dict):
                return None
            source_inputs = source.get("inputs", {})

            # Сначала очевидные названия primitive-поля.
            preferred = [
                current_input,
                "value",
                "integer",
                "number",
                "int",
                "float",
                "width",
                "height",
            ]
            for candidate_name in preferred:
                candidate_value = source_inputs.get(candidate_name)
                if isinstance(candidate_value, (int, float)) and not isinstance(candidate_value, bool):
                    return TargetBinding(source_id, candidate_name)

            # Не считаем произвольственное единственное число управляемым
            # width/height. Например, GetImageSize может получать изображение
            # от ResizeImageMaskNode, где единственное число — megapixels.
            # Запись пиксельной ширины в такое поле приводит к ошибкам вида
            # ``1040 bigger than max of 16``. Fallback разрешён только для
            # очевидных scalar/primitive-нод.
            numeric = [
                name
                for name, candidate_value in source_inputs.items()
                if isinstance(candidate_value, (int, float)) and not isinstance(candidate_value, bool)
            ]
            source_class_norm = normalize_name(source.get("class_type", ""))
            source_title_norm = normalize_name(self.node_title(source_id))
            scalar_source = any(
                token in source_class_norm or token in source_title_norm
                for token in (
                    "primitive", "integer", "float", "number",
                    "numeric", "scalar", "constant",
                )
            )
            if len(numeric) == 1 and scalar_source:
                return TargetBinding(source_id, numeric[0])

            # Продолжаем только через очевидную passthrough/reroute-ноду.
            # По произвольной связи IMAGE -> GetImageSize назад идти нельзя:
            # выходные width/height вычисляются нодой, а не принадлежат её
            # единственному входу.
            linked = [name for name, candidate_value in source_inputs.items() if is_link(candidate_value)]
            passthrough_source = any(
                token in source_class_norm or token in source_title_norm
                for token in ("reroute", "passthrough", "relay")
            )
            if len(linked) == 1 and passthrough_source:
                current_id, current_input = source_id, linked[0]
                continue
            return None
        return None

    def size_candidates(self) -> List[Candidate]:
        result: List[Candidate] = []
        for node_id, node in self.workflow.items():
            inputs = node.get("inputs", {})
            class_type = str(node.get("class_type", ""))
            title = self.node_title(str(node_id))
            width_name = next((name for name in inputs if normalize_name(name) in {"width", "target_width"}), None)
            height_name = next((name for name in inputs if normalize_name(name) in {"height", "target_height"}), None)
            if not width_name or not height_name:
                continue
            width_target = self._editable_numeric_target(str(node_id), width_name)
            height_target = self._editable_numeric_target(str(node_id), height_name)
            if not width_target or not height_target:
                continue
            # Width и height не могут указывать на одно и то же поле. Такая
            # пара всегда является ложным распознаванием вычисляемого размера.
            if width_target == height_target:
                continue
            score = 100
            if title_has_tag(title, "size"):
                score += 1000
            class_norm = normalize_name(class_type)
            if "resize" in class_norm or "scale" in class_norm:
                score += 220
            if "latent" in class_norm:
                score += 130
            result.append(
                Candidate(
                    id=f"{width_target.node_id}:{width_target.input_name}|{height_target.node_id}:{height_target.input_name}",
                    label=self.node_label(str(node_id)),
                    targets=[width_target, height_target],
                    score=score,
                    meta={
                        "owner_node_id": str(node_id),
                        "tagged": title_has_tag(title, "size"),
                        "width": width_target.to_dict(),
                        "height": height_target.to_dict(),
                    },
                )
            )
        return sorted(result, key=lambda item: (-item.score, item.label.lower()))

    def input_drives_sampler_latent(
        self,
        input_choice: Optional[Candidate],
        primary_sampler: Optional[str],
    ) -> bool:
        """Проверяет, задаётся ли размер латента входным изображением.

        Типичный img2img-граф выглядит так:

            LoadImage -> VAEEncode -> KSampler.latent_image

        В нём нет width/height, потому что размер латента уже равен размеру
        загруженного JPEG. Такой workflow полностью корректен и не должен
        блокироваться отсутствием size-ноды.
        """

        if not input_choice or not primary_sampler:
            return False
        sampler = self.workflow.get(str(primary_sampler), {})
        inputs = sampler.get("inputs", {}) if isinstance(sampler, dict) else {}
        image_nodes = {str(target.node_id) for target in input_choice.targets}
        for input_name, value in inputs.items():
            normalized = normalize_name(input_name)
            if normalized not in {"latent_image", "latent", "samples", "input_latent"}:
                continue
            if not is_link(value):
                continue
            latent_source = str(value[0])
            upstream = self.graph.upstream_nodes(latent_source)
            upstream.add(latent_source)
            if image_nodes.intersection(upstream):
                return True
        return False


    def sampler_candidates(self) -> List[Tuple[int, str]]:
        scored: List[Tuple[int, str]] = []
        for node_id, node in self.workflow.items():
            class_type = str(node.get("class_type", ""))
            class_norm = normalize_name(class_type)
            title = self.node_title(str(node_id))
            inputs = node.get("inputs", {})
            names = {normalize_name(name) for name in inputs}
            score = 0
            if title_has_tag(title, "primary"):
                score += 1000
            if "sampler" in class_norm:
                score += 250
            if names.intersection({"steps", "cfg", "denoise", "sampler_name", "scheduler"}):
                score += 180
            if names.intersection({"positive", "negative", "latent_image", "samples"}):
                score += 100
            if score:
                scored.append((score, str(node_id)))
        scored.sort(key=lambda item: (-item[0], int(item[1]) if item[1].isdigit() else item[1]))
        return scored

    def sampler_nodes(self) -> List[str]:
        return [node_id for _score, node_id in self.sampler_candidates()]

    def _find_upstream_text_candidates(
        self,
        source_id: str,
        semantic_id: str = "",
    ) -> List[Tuple[int, TargetBinding]]:
        candidates: List[Tuple[int, TargetBinding]] = []
        nodes = {source_id} | self.graph.upstream_nodes(source_id, max_depth=12)
        wants_negative = semantic_id == "negative_prompt"
        wants_positive = semantic_id == "positive_prompt"
        for node_id in nodes:
            node = self.workflow.get(node_id, {})
            class_type = str(node.get("class_type", ""))
            class_norm = normalize_name(class_type)
            title = self.node_title(node_id)
            title_norm = normalize_name(title)
            for input_name, value in node.get("inputs", {}).items():
                if not isinstance(value, str):
                    continue
                name_norm = normalize_name(input_name)
                definition = self.schema.input_definition(class_type, input_name) or {}
                type_norm = str(definition.get("type", "")).upper()
                score = 0
                if name_norm in {"text", "prompt", "positive", "negative", "positive_prompt", "negative_prompt"}:
                    score += 200
                if type_norm == "STRING":
                    score += 40
                if "textencode" in class_norm or "prompt" in class_norm:
                    score += 80
                if "prompt" in title_norm:
                    score += 60

                # Одна custom node может содержать prompt и negative_prompt.
                # Контекст ветки должен иметь больший приоритет, чем общий
                # признак STRING/TextEncode, иначе positive мог случайно
                # привязаться к negative_prompt и наоборот.
                is_negative_name = "negative" in name_norm
                is_positive_name = "positive" in name_norm
                is_generic_prompt = name_norm in {"text", "prompt"}
                if wants_negative:
                    if is_negative_name:
                        score += 500
                    elif is_positive_name:
                        score -= 1000
                    elif is_generic_prompt:
                        score += 40
                    if "negative" in title_norm:
                        score += 150
                    elif "positive" in title_norm:
                        score -= 300
                elif wants_positive:
                    if is_positive_name:
                        score += 500
                    elif is_negative_name:
                        score -= 1000
                    elif is_generic_prompt:
                        score += 120
                    if "positive" in title_norm:
                        score += 150
                    elif "negative" in title_norm:
                        score -= 300

                if score > 0:
                    candidates.append((score, TargetBinding(node_id, input_name)))
        candidates.sort(key=lambda item: (-item[0], item[1].node_id, item[1].input_name))
        return candidates

    def _branch_zeroes_conditioning(self, source_id: str) -> bool:
        """True when a conditioning branch intentionally contains no prompt.

        FLUX/Kontext workflows commonly derive KSampler.negative through
        ConditioningZeroOut from the positive conditioning. Traversing that
        branch to its upstream CLIPTextEncode would incorrectly expose the same
        text field as both Prompt and Negative prompt.
        """
        nodes = {str(source_id)} | self.graph.upstream_nodes(str(source_id), max_depth=12)
        for node_id in nodes:
            node = self.workflow.get(str(node_id), {})
            class_norm = normalize_name(node.get("class_type", ""))
            title_norm = normalize_name(self.node_title(str(node_id)))
            combined = class_norm + "_" + title_norm
            if "conditioningzeroout" in combined or "zerooutconditioning" in combined or "zeroconditioning" in combined:
                return True
        return False


    def _control_from_target(
        self,
        semantic_id: str,
        target: TargetBinding,
        *,
        label: Optional[str] = None,
        recommended: bool = True,
    ) -> Optional[Dict[str, Any]]:
        node = self.workflow.get(target.node_id)
        if not isinstance(node, dict):
            return None
        inputs = node.get("inputs", {})
        if target.input_name not in inputs or is_link(inputs[target.input_name]):
            return None
        value = inputs[target.input_name]
        class_type = str(node.get("class_type", ""))
        definition = self.schema.input_definition(class_type, target.input_name) or {}
        type_name = str(definition.get("type") or "").upper()
        choices = definition.get("choices")

        control_type = "text"
        multiline = bool(definition.get("multiline"))
        if isinstance(choices, list):
            control_type = "dropdown"
        # Явный тип из /object_info имеет приоритет над Python-типом значения
        # в workflow JSON. FLOAT со значением 1 сериализуется как int, но не
        # должен превращаться в integer-контрол с двумя позициями 0/1.
        elif type_name == "BOOLEAN":
            control_type = "checkbox"
        elif type_name == "INT":
            control_type = "integer"
        elif type_name == "FLOAT":
            control_type = "float"
        elif isinstance(value, bool):
            control_type = "checkbox"
        elif isinstance(value, int):
            control_type = "integer"
        elif isinstance(value, float):
            control_type = "float"
        elif multiline or semantic_id in {"positive_prompt", "negative_prompt"}:
            control_type = "multiline"
        elif isinstance(value, str):
            control_type = "text"
        else:
            return None

        title = strip_helper_tags(self.node_title(target.node_id))
        result: Dict[str, Any] = {
            "id": semantic_id,
            "label": label or self.pretty_control_label(semantic_id, target.input_name),
            "type": control_type,
            "value": value,
            "targets": [target.to_dict()],
            "node_id": target.node_id,
            "input": target.input_name,
            "node_title": title,
            "class_type": class_type,
            "recommended": recommended,
        }
        if isinstance(choices, list):
            result["items"] = choices
        for key in ("min", "max", "step", "round"):
            if key in definition:
                result[key] = definition[key]

        # ComfyUI обычно объявляет KSampler.steps с max=10000 или 1000. Для
        # Photoshop ScriptUI такой диапазон непрактичен: UI-ползунок steps всегда
        # ограничен 100, при этом WorkflowPatcher продолжает валидировать значение
        # по исходному /object_info конкретной ноды.
        if (
            semantic_id == "steps"
            or semantic_id.startswith("steps__")
            or normalize_name(target.input_name) in CONTROL_ALIASES["steps"]
        ):
            try:
                result["max"] = min(float(result.get("max", 100)), 100)
                if control_type == "integer":
                    result["max"] = int(result["max"])
                if isinstance(result.get("value"), (int, float)):
                    result["value"] = min(result["value"], result["max"])
            except (TypeError, ValueError):
                result["max"] = 100
        return result

    @staticmethod
    def pretty_control_label(semantic_id: str, input_name: str) -> str:
        labels = {
            "checkpoint": "Checkpoint",
            "vae": "VAE",
            "text_encoder": "Text encoder",
            "lora": "LoRA",
            "positive_prompt": "Prompt",
            "negative_prompt": "Negative prompt",
            "steps": "Steps",
            "cfg": "CFG",
            "guidance": "Guidance",
            "denoise": "Denoise",
            "sampler": "Sampler",
            "scheduler": "Scheduler",
            "seed": "Seed",
            "model_strength": "Model strength",
            "clip_strength": "CLIP strength",
            "conditioning_strength": "Conditioning strength",
            "start_percent": "Start percent",
            "end_percent": "End percent",
            "mask_grow": "Mask grow",
            "mask_blur": "Mask blur",
            "detection_threshold": "Detection threshold",
            "blend": "Blend",
            "variation_strength": "Variation strength",
            "noise_strength": "Noise strength",
            "tile_overlap": "Tile overlap",
        }
        return labels.get(semantic_id, input_name.replace("_", " ").capitalize())

    def discover_controls(self, primary_sampler: Optional[str]) -> List[Dict[str, Any]]:
        controls: Dict[str, Dict[str, Any]] = {}
        represented_targets: set[tuple[str, str]] = set()

        def remember_control(control: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not control:
                return None
            for item in control.get("targets", []) or []:
                node_id = str(item.get("node_id", ""))
                input_name = str(item.get("input", ""))
                if node_id and input_name:
                    represented_targets.add((node_id, input_name))
            return control

        loader_semantics = ("checkpoint", "vae", "text_encoder", "lora")
        loader_counts: Dict[str, int] = {key: 0 for key in loader_semantics}
        for node_id, node in self.workflow.items():
            class_norm = normalize_name(node.get("class_type", ""))
            title = strip_helper_tags(self.node_title(str(node_id)))
            for input_name, value in node.get("inputs", {}).items():
                if is_link(value) or not scalar_value(value):
                    continue
                normalized = normalize_name(input_name)
                semantic_id: Optional[str] = None
                if normalized in CONTROL_ALIASES["checkpoint"] or (
                    "checkpoint" in class_norm and "ckpt" in normalized
                ) or normalized in {"ckpt", "ckptname"}:
                    semantic_id = "checkpoint"
                elif normalized in CONTROL_ALIASES["vae"] or (
                    normalized.startswith("vae") and normalized.endswith("name")
                ):
                    semantic_id = "vae"
                elif normalized in CONTROL_ALIASES["text_encoder"] or (
                    ("clip" in normalized or "encoder" in normalized or normalized.startswith("t5"))
                    and normalized.endswith("name")
                ):
                    semantic_id = "text_encoder"
                else:
                    # LoRA-ноды разных пакетов используют не только lora_name,
                    # но и варианты вроде lora_model_name, lora_file_1 или
                    # lora_name_2. Выбираем только строковые поля самой LoRA-ноды;
                    # числовые strength_model/strength_clip обрабатываются ниже
                    # отдельными семантическими контролами.
                    lora_node = "lora" in class_norm or "lora" in normalize_name(title)
                    definition = self.schema.input_definition(str(node.get("class_type", "")), input_name) or {}
                    lora_choices = definition.get("choices")
                    lora_field = normalized in CONTROL_ALIASES["lora"] or (
                        lora_node
                        and isinstance(value, str)
                        and "lora" in normalized
                        and (
                            isinstance(lora_choices, list)
                            or any(token in normalized for token in ("name", "model", "file", "filename", "path"))
                        )
                    )
                    if lora_field:
                        semantic_id = "lora"
                if not semantic_id:
                    continue
                target = TargetBinding(str(node_id), input_name)
                count = loader_counts[semantic_id]
                control_id = semantic_id if count == 0 else f"{semantic_id}__node_{node_id}__{input_name}"
                label = self.pretty_control_label(semantic_id, input_name)
                if count > 0:
                    label += " — " + self.node_label(str(node_id))
                control = self._control_from_target(
                    control_id,
                    target,
                    label=label,
                    recommended=True,
                )
                if control:
                    control = remember_control(control)
                    controls[control_id] = control
                    loader_counts[semantic_id] += 1

        sampler_ids = self.sampler_nodes()
        ordered_sampler_ids = []
        if primary_sampler:
            ordered_sampler_ids.append(primary_sampler)
        ordered_sampler_ids.extend(node_id for node_id in sampler_ids if node_id != primary_sampler)

        for sampler_index, node_id in enumerate(ordered_sampler_ids):
            node = self.workflow[node_id]
            inputs = node.get("inputs", {})
            is_primary = sampler_index == 0
            sampler_input_preferences = {
                "steps": ["steps", "num_steps", "sampling_steps"],
                "cfg": ["cfg", "cfg_scale"],
                "guidance": ["guidance", "guidance_scale", "flux_guidance", "distilled_cfg", "distilled_cfg_scale"],
                "denoise": ["denoise", "denoise_strength", "strength"],
                "sampler": ["sampler_name", "sampler"],
                "scheduler": ["scheduler", "scheduler_name"],
                "seed": ["seed", "noise_seed"],
            }
            for semantic_id in ("steps", "cfg", "guidance", "denoise", "sampler", "scheduler", "seed"):
                aliases = CONTROL_ALIASES[semantic_id]
                matching_inputs = [
                    input_name
                    for input_name in inputs
                    if normalize_name(input_name) in aliases
                ]
                if not matching_inputs:
                    continue
                preference = sampler_input_preferences.get(semantic_id, [])
                matching_inputs.sort(
                    key=lambda name: (
                        preference.index(normalize_name(name))
                        if normalize_name(name) in preference
                        else len(preference),
                        normalize_name(name),
                    )
                )
                if len(matching_inputs) > 1:
                    self.warning(
                        f"Sampler #{node_id} contains several inputs matching {semantic_id}: "
                        + ", ".join(matching_inputs)
                        + f". {matching_inputs[0]} is used as the standard control; the others remain available as advanced fields.",
                        "sampler_inputs_ambiguous",
                        [node_id, semantic_id, ", ".join(matching_inputs), matching_inputs[0]],
                    )
                for input_name in matching_inputs:
                    value = inputs[input_name]
                    if is_link(value):
                        # Seed часто приходит из Primitive/RandomNoise-ноды.
                        # Для него прослеживаем link до реального локального INT.
                        if semantic_id != "seed":
                            continue
                        target = self._editable_numeric_target(node_id, input_name)
                        if not target:
                            continue
                    else:
                        target = TargetBinding(node_id, input_name)

                    control_id = semantic_id if is_primary else f"{semantic_id}__node_{node_id}"
                    label = self.pretty_control_label(semantic_id, input_name)
                    if not is_primary:
                        label += " — " + self.node_label(str(node_id))
                    control = self._control_from_target(
                        control_id,
                        target,
                        label=label,
                        # Seed доступен в списке параметров, но по
                        # умолчанию скрыт: невидимый seed Python сам
                        # рандомизирует перед каждой генерацией.
                        recommended=is_primary and semantic_id != "seed",
                    )
                    if control:
                        control = remember_control(control)
                        controls.setdefault(control_id, control)
                        break

        # Часто используемые числовые параметры custom nodes показываются по
        # умолчанию, но только по достаточно однозначным именам входов. Любые
        # остальные scalar inputs по-прежнему доступны в настройках workflow.
        extra_counts: Dict[str, int] = {key: 0 for key in EXTRA_RECOMMENDED_CONTROL_ORDER}
        for node_id, node in self.workflow.items():
            title = strip_helper_tags(self.node_title(str(node_id)))
            for input_name, value in node.get("inputs", {}).items():
                if is_link(value) or not scalar_value(value):
                    continue
                target_key = (str(node_id), str(input_name))
                if target_key in represented_targets:
                    continue
                normalized = normalize_name(input_name)
                semantic_id = next(
                    (
                        key
                        for key in EXTRA_RECOMMENDED_CONTROL_ORDER
                        if normalized in CONTROL_ALIASES[key]
                    ),
                    None,
                )
                if not semantic_id:
                    continue
                count = extra_counts[semantic_id]
                control_id = (
                    semantic_id
                    if count == 0
                    else f"{semantic_id}__node_{node_id}__{input_name}"
                )
                label = self.pretty_control_label(semantic_id, input_name)
                if count > 0:
                    label += " — " + self.node_label(str(node_id))
                control = self._control_from_target(
                    control_id,
                    TargetBinding(str(node_id), input_name),
                    label=label,
                    recommended=True,
                )
                if control:
                    control = remember_control(control)
                    controls[control_id] = control
                    extra_counts[semantic_id] += 1

        # ConditioningZeroOut или общая positive-нода не создают отдельное поле Negative prompt.
        if primary_sampler:
            sampler = self.workflow.get(primary_sampler, {})
            sampler_inputs = sampler.get("inputs", {})
            positive_target_key: Optional[Tuple[str, str]] = None
            for semantic_id, sampler_input_name in (
                ("positive_prompt", "positive"),
                ("negative_prompt", "negative"),
            ):
                value = sampler_inputs.get(sampler_input_name)
                if not is_link(value):
                    continue
                source_id = str(value[0])
                if semantic_id == "negative_prompt" and self._branch_zeroes_conditioning(source_id):
                    continue
                prompt_candidates = self._find_upstream_text_candidates(source_id, semantic_id)
                if not prompt_candidates:
                    continue
                if len(prompt_candidates) > 1 and prompt_candidates[0][0] == prompt_candidates[1][0]:
                    self.warning(
                        f"Several equivalent {semantic_id.replace('_', ' ')} fields were found upstream of sampler #{primary_sampler}. "
                        "The first deterministic match is used; rename or tag the intended node to make the workflow unambiguous.",
                        "prompt_fields_ambiguous",
                        [semantic_id.replace("_", " "), primary_sampler],
                    )
                target = prompt_candidates[0][1]
                target_key = (target.node_id, target.input_name)
                if semantic_id == "negative_prompt" and target_key == positive_target_key:
                    continue
                control = self._control_from_target(semantic_id, target)
                if control:
                    control = remember_control(control)
                    controls[semantic_id] = control
                    if semantic_id == "positive_prompt":
                        positive_target_key = target_key

        for node_id, node in self.workflow.items():
            title_norm = normalize_name(self.node_title(str(node_id)))
            class_norm = normalize_name(node.get("class_type", ""))
            for input_name, value in node.get("inputs", {}).items():
                if not isinstance(value, str):
                    continue
                input_norm = normalize_name(input_name)
                combined = "_".join((title_norm, class_norm, input_norm))
                semantic_id: Optional[str] = None
                if "negative" in combined:
                    semantic_id = "negative_prompt"
                elif "positive" in combined or "prompt" in combined or "textencode" in class_norm:
                    semantic_id = "positive_prompt"
                if semantic_id and semantic_id not in controls:
                    control = self._control_from_target(
                        semantic_id,
                        TargetBinding(str(node_id), input_name),
                    )
                    if control:
                        control = remember_control(control)
                        controls[semantic_id] = control

        for node_id, node in self.workflow.items():
            title = self.node_title(str(node_id))
            if not title_has_tag(title, "ui"):
                continue
            for input_name, value in node.get("inputs", {}).items():
                if not scalar_value(value) or is_link(value):
                    continue
                semantic_id = f"node_{node_id}__{input_name}"
                if semantic_id in controls or (str(node_id), str(input_name)) in represented_targets:
                    continue
                control = self._control_from_target(
                    semantic_id,
                    TargetBinding(str(node_id), input_name),
                    label=f"{self.node_label(str(node_id))}: {input_name}",
                    recommended=True,
                )
                if control:
                    control = remember_control(control)
                    controls[semantic_id] = control

        for node_id, node in self.workflow.items():
            for input_name, value in node.get("inputs", {}).items():
                if not scalar_value(value) or is_link(value):
                    continue
                # Служебные пути/префиксы не стоит предлагать по умолчанию.
                normalized = normalize_name(input_name)
                if normalized in {
                    "image", "filename_prefix", "upload", "file", "path",
                    "width", "height", "batch_size",
                }:
                    continue
                semantic_id = f"node_{node_id}__{input_name}"
                if semantic_id in controls or (str(node_id), str(input_name)) in represented_targets:
                    continue
                control = self._control_from_target(
                    semantic_id,
                    TargetBinding(str(node_id), input_name),
                    label=f"{self.node_label(str(node_id))}: {input_name}",
                    recommended=False,
                )
                if control:
                    control = remember_control(control)
                    controls[semantic_id] = control

        def order_key(control: Dict[str, Any]) -> Tuple[int, int, str]:
            control_id = control["id"]
            semantic_key = control_id.split("__", 1)[0]
            try:
                standard_index = STANDARD_CONTROL_ORDER.index(semantic_key)
            except ValueError:
                standard_index = 999
            return (
                standard_index,
                0 if control.get("recommended") else 1,
                str(control.get("label", "")).lower(),
            )

        return sorted(controls.values(), key=order_key)


    @staticmethod
    def find_candidate(candidates: List[Candidate], candidate_id: Optional[str]) -> Optional[Candidate]:
        if not candidate_id:
            return None
        for candidate in candidates:
            if candidate.id == candidate_id:
                return candidate
        return None

    @staticmethod
    def choose_unique_candidate(candidates: List[Candidate]) -> Optional[Candidate]:
        """Returns only a genuinely unambiguous automatic choice.

        Heuristic score differences are useful for sorting the settings list,
        but they are not sufficient to overwrite workflow fields safely. When
        several candidates exist, automatic selection is allowed only if one
        of them is explicitly tagged by the user.
        """

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        grouped = [candidate for candidate in candidates if candidate.meta.get("grouped")]
        if len(grouped) == 1:
            return grouped[0]
        tagged = [candidate for candidate in candidates if candidate.meta.get("tagged")]
        return tagged[0] if len(tagged) == 1 else None

    @staticmethod
    def choose_preferred_input_candidate(candidates: List[Candidate]) -> Optional[Candidate]:
        """Choose one Photoshop source while preserving deterministic priority.

        Explicit #PS-INPUT grouping/tagging wins. Without tags, semantic score,
        numeric node id and label provide a stable automatic default. Other
        LoadImage nodes keep their workflow or reference roles and remain
        editable in the JSX settings.
        """

        if not candidates:
            return None
        grouped = [candidate for candidate in candidates if candidate.meta.get("grouped")]
        if len(grouped) == 1:
            return grouped[0]
        tagged = [candidate for candidate in candidates if candidate.meta.get("tagged")]
        if len(tagged) == 1:
            return tagged[0]
        def priority(candidate: Candidate) -> Tuple[int, int, str]:
            node_id = str(candidate.meta.get("node_id") or "")
            numeric_node_id = int(node_id) if node_id.isdigit() else 2**31 - 1
            return (-int(candidate.score), numeric_node_id, candidate.label.lower())

        return sorted(candidates, key=priority)[0]

    def analyze(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        overrides = overrides or {}
        self.validate_api_format()

        input_candidates = self.input_candidates()
        output_candidates = self.output_candidates()
        size_candidates = self.size_candidates()
        scored_sampler_candidates = self.sampler_candidates()
        sampler_candidates = [node_id for _score, node_id in scored_sampler_candidates]

        # A source that actually feeds the primary sampler latent branch is a
        # better automatic Photoshop input than a detached/reference loader.
        primary_sampler = sampler_candidates[0] if sampler_candidates else None

        main_input_candidates = [item for item in input_candidates if not item.meta.get("reference")] or input_candidates
        input_override = str(overrides.get("input") or "")
        sampler_input_candidates = [
            item
            for item in main_input_candidates
            if primary_sampler and self.input_drives_sampler_latent(item, primary_sampler)
        ]
        input_choice = (
            self.find_candidate(input_candidates, input_override)
            if input_override
            else self.choose_preferred_input_candidate(sampler_input_candidates or main_input_candidates)
        )
        if input_override and not input_choice:
            self.error(
                "The node previously assigned the Photoshop image role no longer exists. Open Workflow settings and assign the role again.",
                "selected_input_missing",
            )

        # LoadImageMask nodes are common to every possible main input. Only the
        # virtual Main LoadImage MASK changes, so keep it in a separate map
        # instead of repeating every common candidate N times in the response.
        common_mask_candidates = self.load_image_mask_candidates()
        mask_candidates = self.mask_candidates(input_choice, common_mask_candidates)
        main_mask_candidates_by_input: Dict[str, Dict[str, Any]] = {}
        for candidate in input_candidates:
            candidate_mask = self.input_alpha_mask_candidate(candidate)
            if candidate_mask is not None:
                main_mask_candidates_by_input[candidate.id] = candidate_mask.to_dict()
        mask_override = str(overrides.get("mask") or "")
        mask_choice = self.choose_mask_candidate(mask_candidates, mask_override)
        if mask_override and not mask_choice:
            self.error(
                "The previously selected Inpaint mask option no longer exists. Open Workflow settings and select it again.",
                "selected_mask_missing",
            )

        reference_ids = overrides.get("references") if isinstance(overrides.get("references"), list) else []
        references_configured = overrides.get("references_configured") is True
        reference_choices: List[Candidate] = []
        found_reference_ids: Set[str] = set()
        input_target_keys = {
            (target.node_id, target.input_name)
            for target in input_choice.targets
        } if input_choice else set()
        for candidate in input_candidates:
            explicitly_selected = candidate.id in reference_ids
            automatically_selected = (
                not references_configured
                and not reference_ids
                and bool(candidate.meta.get("reference"))
            )
            if not explicitly_selected and not automatically_selected:
                continue
            if explicitly_selected:
                found_reference_ids.add(candidate.id)
            candidate_target_keys = {
                (target.node_id, target.input_name) for target in candidate.targets
            }
            if candidate_target_keys & input_target_keys:
                self.error(
                    f"{candidate.label} has conflicting image roles. Choose only one role in workflow settings.",
                    "image_role_conflict",
                    [candidate.label],
                )
                continue
            reference_choices.append(candidate)
        missing_reference_ids = [item for item in reference_ids if item not in found_reference_ids]
        if missing_reference_ids:
            self.warning(
                "Some selected reference inputs no longer exist and were ignored: "
                + ", ".join(missing_reference_ids[:10]),
                "selected_references_missing",
                [", ".join(missing_reference_ids[:10])],
            )

        # Keep explicit per-LoadImage empty roles in the analysis overrides so
        # stale ids and role conflicts are caught before prompt submission.
        empty_input_ids = overrides.get("empty_inputs") if isinstance(overrides.get("empty_inputs"), list) else []
        input_by_id = {candidate.id: candidate for candidate in input_candidates}
        managed_target_keys: Set[Tuple[str, str]] = set()
        if input_choice:
            managed_target_keys.update((target.node_id, target.input_name) for target in input_choice.targets)
        for candidate in reference_choices:
            managed_target_keys.update((target.node_id, target.input_name) for target in candidate.targets)
        missing_empty_ids: List[str] = []
        for empty_id in empty_input_ids:
            empty_candidate = input_by_id.get(str(empty_id))
            if not empty_candidate or empty_candidate.meta.get("grouped"):
                missing_empty_ids.append(str(empty_id))
                continue
            if not empty_candidate.meta.get("load_image"):
                self.error(
                    f"{empty_candidate.label} cannot use the empty-image role because it is not a standard LoadImage node.",
                    "empty_role_unsupported",
                    [empty_candidate.label],
                )
                continue
            empty_target_keys = {
                (target.node_id, target.input_name) for target in empty_candidate.targets
            }
            if empty_target_keys & managed_target_keys:
                self.error(
                    f"{empty_candidate.label} has conflicting image roles. Choose only one role in workflow settings.",
                    "image_role_conflict",
                    [empty_candidate.label],
                )
        if missing_empty_ids:
            self.warning(
                "Some LoadImage empty roles no longer exist and were ignored: "
                + ", ".join(missing_empty_ids[:10]),
                "empty_roles_missing",
                [", ".join(missing_empty_ids[:10])],
            )

        # Additional standard LoadImage nodes with no file stored in the API
        # JSON cannot provide useful workflow content. Treat them as empty by
        # default even when the settings window has never been opened. Main
        # Photoshop and Reference targets are excluded through managed keys.
        automatic_empty_input_ids: List[str] = []
        for candidate in input_candidates:
            if candidate.meta.get("grouped") or not candidate.meta.get("load_image"):
                continue
            source_value = str(candidate.meta.get("source_value") or "").strip()
            candidate_target_keys = {
                (target.node_id, target.input_name) for target in candidate.targets
            }
            if not source_value and not (candidate_target_keys & managed_target_keys):
                automatic_empty_input_ids.append(candidate.id)

        output_override = str(overrides.get("output") or "")
        output_choice = self.find_candidate(output_candidates, output_override) if output_override else self.choose_unique_candidate(output_candidates)
        if output_override and not output_choice:
            self.error(
                "The previously selected Output image no longer exists. Open Workflow settings and select it again.",
                "selected_output_missing",
            )
        elif not output_choice and output_candidates:
            self.error(
                "Several options were found for Output image. Select the required option in Workflow settings or add #PS-OUTPUT to the corresponding node title.",
                "output_ambiguous",
            )

        requested_size_mode = str(overrides.get("size_mode") or "auto").lower()
        if requested_size_mode not in {"auto", "source_image", "binding"}:
            requested_size_mode = "auto"
        size_override = str(overrides.get("size") or "")
        size_choice: Optional[Candidate] = None
        if requested_size_mode == "binding":
            if not size_override:
                self.error(
                    "Width / height fields is selected under Size control, but no field pair is specified.",
                    "size_binding_required",
                )
            else:
                size_choice = self.find_candidate(size_candidates, size_override)
                if not size_choice:
                    self.error(
                        "The previously selected Width / height fields pair no longer exists. Open Workflow settings and select it again.",
                        "selected_size_missing",
                    )
        elif requested_size_mode == "auto":
            size_choice = self.choose_unique_candidate(size_candidates)
            if not size_choice and size_candidates:
                self.warning(
                    "Several options were found for Width / height fields. Automatic Size control will use Input image size. "
                    "If the workflow requires specific fields to be changed, select a pair manually.",
                    "size_ambiguous",
                )

        # Primary sampler определяется анализатором автоматически. Все найденные
        # sampler-контролы всё равно выводятся отдельно; неоднозначность влияет
        # только на то, какой sampler считается главным и получает короткие ID.
        if len(scored_sampler_candidates) > 1 and scored_sampler_candidates[0][0] == scored_sampler_candidates[1][0]:
            self.warning(
                "Several equivalent sampler nodes were found. The first deterministic node is treated as primary. "
                "Add #PS-MAIN to the intended sampler title to make the choice explicit.",
                "sampler_ambiguous",
            )

        if not input_choice and not main_input_candidates:
            self.error(
                "No node was found that can use the Photoshop image role. Add #PS-INPUT to the required node title.",
                "input_not_found",
            )

        if not output_choice and not output_candidates:
            self.error(
                "No option was found for Output image. Add #PS-OUTPUT to the required Save Image or Preview Image node title.",
                "output_not_found",
            )

        # В source_image и в неоднозначном auto-режиме width/height не меняются.
        # Photoshop всё равно экспортирует JPEG нужного размера; дальнейшее
        # поведение определяется самим workflow.
        if (
            not size_choice
            and requested_size_mode == "auto"
            and not size_candidates
            and not self.input_drives_sampler_latent(input_choice, primary_sampler)
        ):
            self.info(
                "No option was found for Width / height fields. The script will send a correctly sized Photoshop image, "
                "but the final size depends on the workflow logic.",
                "size_not_found",
            )

        if not primary_sampler:
            self.info(
                "The primary sampler was not recognized; standard parameters may be incomplete.",
                "sampler_not_found",
            )

        controls = self.discover_controls(primary_sampler)
        bindings: Dict[str, Any] = {}
        if input_choice:
            bindings["input_image"] = [target.to_dict() for target in input_choice.targets]
        if reference_choices:
            bindings["reference_images"] = [
                {
                    "id": candidate.id,
                    "label": candidate.label,
                    "targets": [target.to_dict() for target in candidate.targets],
                    # Only the standard ComfyUI LoadImage participates in the
                    # new blank-on-None behavior. Custom reference loaders keep
                    # the pre-existing behavior when no file is supplied.
                    "load_image": bool(candidate.meta.get("load_image")),
                }
                for candidate in reference_choices
            ]
        if output_choice:
            bindings["output_image"] = {
                "node_id": output_choice.meta.get("node_id", output_choice.id),
            }
        if mask_choice:
            bindings["inpaint_mask"] = {
                "id": mask_choice.id,
                "mode": mask_choice.meta.get("mode", ""),
                "targets": [target.to_dict() for target in mask_choice.targets],
                "channel_targets": mask_choice.meta.get("channel_targets", []),
                "node_ids": mask_choice.meta.get("node_ids", []),
                "connected": bool(mask_choice.meta.get("connected")),
            }
        if size_choice:
            bindings["width"] = size_choice.meta["width"]
            bindings["height"] = size_choice.meta["height"]
            bindings["size"] = {
                "id": size_choice.id,
                "label": size_choice.label,
                "width": size_choice.meta["width"],
                "height": size_choice.meta["height"],
            }
        errors = [item for item in self.diagnostics if item["level"] == "error"]
        return {
            "valid": not errors,
            "analysis_uuid": ANALYZER_UUID,
            "size_selection_mode": requested_size_mode,
            "has_size_binding": bool(size_choice),
            "automatic_empty_inputs": automatic_empty_input_ids,
            "bindings": bindings,
            "controls": controls,
            "recommended_controls": [item["id"] for item in controls if item.get("recommended")],
            "candidates": {
                "input": [item.to_dict() for item in input_candidates],
                "mask": [item.to_dict() for item in common_mask_candidates],
                "main_mask_by_input": main_mask_candidates_by_input,
                "output": [item.to_dict() for item in output_candidates],
                "size": [item.to_dict() for item in size_candidates],
            },
            "diagnostics": self.diagnostics,
        }


# ============================================================================
# CACHE АНАЛИЗА И ПРИМЕНЕНИЕ ЗНАЧЕНИЙ К WORKFLOW
# Cache проверяется по размеру/mtime/UUID анализатора. WorkflowPatcher заново
# валидирует тип, диапазон и target перед каждым фактическим изменением JSON.
# ============================================================================
class SchemaCache:
    MAX_OVERRIDE_VARIANTS = 8

    @staticmethod
    def _override_digest(overrides: Optional[Dict[str, Any]]) -> str:
        if not overrides:
            return ""
        canonical = json.dumps(
            overrides, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def cache_path(
        self,
        workflow_id: str,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Path:
        digest = self._override_digest(overrides)
        if digest:
            return WORKFLOW_CACHE_DIR / f"{workflow_id}.bindings.{digest}.json"
        return WORKFLOW_CACHE_DIR / f"{workflow_id}.json"

    def _read_payload(
        self,
        workflow_file: WorkflowFile,
        overrides: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        path = self.cache_path(workflow_file.workflow_id, overrides)
        try:
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("cache_version") != CACHE_VERSION:
                return None
            if data.get("analyzer_uuid") != ANALYZER_UUID:
                return None
            if data.get("relative_path") != workflow_file.relative_path:
                return None
            if int(data.get("file_size", -1)) != int(workflow_file.size):
                return None
            if int(data.get("modified_ns", -1)) != int(workflow_file.modified_ns):
                return None
            stored_overrides = data.get("binding_overrides")
            if (stored_overrides or None) != (overrides or None):
                return None
            return data
        except Exception:
            LOGGER.warning("Corrupted workflow cache %s", path.name)
            return None

    def load_fast_bundle(
        self,
        workflow_file: WorkflowFile,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """Return current cached analysis and its compact validation schema."""

        data = self._read_payload(workflow_file, overrides)
        if not data:
            return None
        analysis = data.get("analysis")
        validation_schema = data.get("validation_schema")
        if (
            not isinstance(analysis, dict)
            or data.get("validation_schema_version") != VALIDATION_SCHEMA_VERSION
            or not isinstance(validation_schema, dict)
        ):
            return None
        workflow_file.sha256 = str(data.get("workflow_hash") or "")
        return analysis, validation_schema

    def save(
        self,
        workflow_file: WorkflowFile,
        analysis: Dict[str, Any],
        validation_schema: Dict[str, Any],
        overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        path = self.cache_path(workflow_file.workflow_id, overrides)
        payload = {
            "cache_version": CACHE_VERSION,
            "analyzer_uuid": ANALYZER_UUID,
            "validation_schema_version": VALIDATION_SCHEMA_VERSION,
            "relative_path": workflow_file.relative_path,
            "file_size": workflow_file.size,
            "modified_ns": workflow_file.modified_ns,
            "workflow_hash": WorkflowRepository.ensure_hash(workflow_file),
            "binding_overrides": overrides or None,
            "analysis": analysis,
            "validation_schema": validation_schema,
        }
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(path)
            if overrides:
                self._trim_override_variants(workflow_file.workflow_id)
        except OSError as exc:
            # Analysis is authoritative; its optional disk cache must never
            # make a valid workflow unusable on a read-only/full temp drive.
            LOGGER.warning("Could not save workflow cache %s: %s", path.name, exc)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _trim_override_variants(self, workflow_id: str) -> None:
        try:
            variants = sorted(
                WORKFLOW_CACHE_DIR.glob(f"{workflow_id}.bindings.*.json"),
                key=lambda item: item.stat().st_mtime_ns,
                reverse=True,
            )
            for stale in variants[self.MAX_OVERRIDE_VARIANTS:]:
                stale.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not trim binding caches for %s", workflow_id)

    def invalidate(self, workflow_id: str) -> None:
        try:
            self.cache_path(workflow_id).unlink(missing_ok=True)
            for path in WORKFLOW_CACHE_DIR.glob(f"{workflow_id}.bindings.*.json"):
                path.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not delete cache %s", workflow_id)


class WorkflowRuntimeCache:
    """Bounded process cache for parsed JSON and override-specific analysis."""

    MAX_WORKFLOWS = 8
    MAX_ANALYSES = 24

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workflows: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
        self._analyses: "OrderedDict[Tuple[Any, ...], Tuple[Dict[str, Any], Dict[str, Any]]]" = OrderedDict()
        self._sampler_nodes: "OrderedDict[Tuple[Any, ...], List[str]]" = OrderedDict()

    @staticmethod
    def _file_key(workflow_file: WorkflowFile) -> Tuple[Any, ...]:
        try:
            absolute = workflow_file.absolute_path.resolve()
        except OSError:
            absolute = workflow_file.absolute_path.absolute()
        return (
            workflow_file.workflow_id,
            os.path.normcase(str(absolute)),
            int(workflow_file.size),
            int(workflow_file.modified_ns),
        )

    @staticmethod
    def _overrides_key(overrides: Optional[Dict[str, Any]]) -> str:
        return json.dumps(
            overrides or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def _discard_stale_path_locked(self, file_key: Tuple[Any, ...]) -> None:
        absolute = file_key[1]
        stale_files = [
            key for key in self._workflows if key[1] == absolute and key != file_key
        ]
        for key in stale_files:
            self._workflows.pop(key, None)
        stale_analyses = [
            key for key in self._analyses
            if key[0][1] == absolute and key[0] != file_key
        ]
        for key in stale_analyses:
            self._analyses.pop(key, None)
        for key in [key for key in self._sampler_nodes if key[1] == absolute and key != file_key]:
            self._sampler_nodes.pop(key, None)

    def load_json(
        self, workflow_file: WorkflowFile, repository: WorkflowRepository
    ) -> Dict[str, Any]:
        file_key = self._file_key(workflow_file)
        with self._lock:
            cached = self._workflows.get(file_key)
            if cached is not None:
                self._workflows.move_to_end(file_key)
                return cached

        loaded = repository.load_json(workflow_file)
        with self._lock:
            self._discard_stale_path_locked(file_key)
            existing = self._workflows.get(file_key)
            if existing is not None:
                self._workflows.move_to_end(file_key)
                return existing
            self._workflows[file_key] = loaded
            while len(self._workflows) > self.MAX_WORKFLOWS:
                evicted_key, _ = self._workflows.popitem(last=False)
                for analysis_key in [
                    key for key in self._analyses if key[0] == evicted_key
                ]:
                    self._analyses.pop(analysis_key, None)
                self._sampler_nodes.pop(evicted_key, None)
        return loaded

    def get_sampler_node_ids(
        self, workflow_file: WorkflowFile, workflow_data: Dict[str, Any]
    ) -> List[str]:
        file_key = self._file_key(workflow_file)
        with self._lock:
            cached = self._sampler_nodes.get(file_key)
            if cached is not None:
                self._sampler_nodes.move_to_end(file_key)
                return list(cached)
        detected = WorkflowAnalyzer(workflow_data, {}).sampler_nodes()
        with self._lock:
            self._discard_stale_path_locked(file_key)
            self._sampler_nodes[file_key] = list(detected)
            self._sampler_nodes.move_to_end(file_key)
            while len(self._sampler_nodes) > self.MAX_WORKFLOWS:
                self._sampler_nodes.popitem(last=False)
        return list(detected)

    def get_analysis(
        self, workflow_file: WorkflowFile, overrides: Optional[Dict[str, Any]]
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        key = (self._file_key(workflow_file), self._overrides_key(overrides))
        with self._lock:
            bundle = self._analyses.get(key)
            if bundle is None:
                return None
            self._analyses.move_to_end(key)
            return copy.deepcopy(bundle)

    def put_analysis(
        self,
        workflow_file: WorkflowFile,
        overrides: Optional[Dict[str, Any]],
        analysis: Dict[str, Any],
        validation_schema: Dict[str, Any],
    ) -> None:
        file_key = self._file_key(workflow_file)
        key = (file_key, self._overrides_key(overrides))
        with self._lock:
            self._discard_stale_path_locked(file_key)
            self._analyses[key] = copy.deepcopy((analysis, validation_schema))
            self._analyses.move_to_end(key)
            while len(self._analyses) > self.MAX_ANALYSES:
                self._analyses.popitem(last=False)

    def invalidate(self, workflow_id: str) -> None:
        with self._lock:
            for key in [key for key in self._workflows if key[0] == workflow_id]:
                self._workflows.pop(key, None)
            for key in [key for key in self._analyses if key[0][0] == workflow_id]:
                self._analyses.pop(key, None)
            for key in [key for key in self._sampler_nodes if key[0] == workflow_id]:
                self._sampler_nodes.pop(key, None)


class WorkflowPatcher:
    def __init__(self, workflow: Dict[str, Any], object_info: Dict[str, Any]):
        self.workflow = copy.deepcopy(workflow)
        self.schema = ObjectInfoSchema(object_info)
        # Реальные seed, подставленные в текущую копию workflow. Они не нужны
        # основному интерфейсу, но записываются в metadata слоя для повторения.
        self.generated_seeds: Dict[str, int] = {}
        # Неприменённые необязательные параметры не должны теряться молча.
        # Список возвращается Photoshop вместе с успешным результатом.
        self.warnings: List[Dict[str, Any]] = []

    def warning(
        self,
        message: str,
        code: str = "",
        params: Optional[Sequence[Any]] = None,
    ) -> None:
        normalized = str(message or "").strip()
        if not normalized:
            return
        if any(str(item.get("message") or "") == normalized for item in self.warnings):
            return
        item: Dict[str, Any] = {"message": normalized}
        if code:
            item["code"] = code
        if params:
            item["params"] = [str(value) for value in params]
        self.warnings.append(item)

    def _resolve_target(self, target: Dict[str, str]) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
        """Возвращает (node, input_name, inputs) и централизованно проверяет binding.

        Этот маленький helper нужен, чтобы существовало два безопасных способа
        записи:

        * set_target() — обычное пользовательское поле, которое нужно привести
          к типу и проверить по /object_info;
        * set_target_raw() — значение, созданное самим сервером ComfyUI.

        В частности, LoadImage.image объявлен как COMBO со списком уже
        существующих файлов. Только что загруженный через /upload/image файл
        может ещё отсутствовать в закешированном /object_info, хотя ComfyUI его
        корректно примет. Поэтому путь загруженного изображения нельзя
        проверять как обычный статический enum.
        """

        node_id = str(target.get("node_id"))
        input_name = str(target.get("input"))
        node = self.workflow.get(node_id)
        if not isinstance(node, dict):
            raise UserVisibleError(
                f"A node disappeared from the workflow: {node_id}.",
                "workflow_target_node_missing",
                [node_id],
            )
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or input_name not in inputs:
            raise UserVisibleError(
                f"Node {node_id} lost input {input_name}.",
                "workflow_target_input_missing",
                [node_id, input_name],
            )
        return node, input_name, inputs

    def set_target(self, target: Dict[str, str], value: Any) -> None:
        """Записывает обычное поле с приведением типа и валидацией enum."""

        node, input_name, inputs = self._resolve_target(target)
        inputs[input_name] = self.coerce_value(node, input_name, value)

    def set_target_raw(self, target: Dict[str, str], value: Any) -> None:
        """Записывает доверенное runtime-значение без проверки статического COMBO.

        Использовать только для значений, полученных непосредственно от
        ComfyUI, например результата POST /upload/image. Пользовательские
        sampler/scheduler/model-поля проходят set_target().
        """

        _node, input_name, inputs = self._resolve_target(target)
        inputs[input_name] = value

    def coerce_value(self, node: Dict[str, Any], input_name: str, value: Any) -> Any:
        class_type = str(node.get("class_type", ""))
        definition = self.schema.input_definition(class_type, input_name) or {}
        type_name = str(definition.get("type", "")).upper()
        choices = definition.get("choices")

        if isinstance(choices, list):
            if value not in choices:
                # Сравнение строк позволяет пережить enum из чисел/Path-like.
                string_map = {str(item): item for item in choices}
                if str(value) in string_map:
                    value = string_map[str(value)]
                else:
                    raise UserVisibleError(
                        f"Invalid value {value!r} for {class_type}.{input_name}.",
                        "workflow_field_invalid_value",
                        [value, f"{class_type}.{input_name}"],
                    )
            return value

        current_value = node.get("inputs", {}).get(input_name)
        if type_name == "BOOLEAN" or isinstance(current_value, bool):
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)

        if type_name in {"INT", "FLOAT"} or isinstance(current_value, (int, float)):
            # Не выводим тип только из текущего JSON-литерала: FLOAT=1
            # выглядит как Python int, хотя нода принимает дробные значения.
            is_integer = type_name == "INT" or (
                type_name not in {"INT", "FLOAT"}
                and isinstance(current_value, int)
                and not isinstance(current_value, bool)
            )
            minimum = definition.get("min")
            maximum = definition.get("max")

            if is_integer:
                # Не переводим seed через float: float теряет точность выше
                # 2^53, тогда как ComfyUI часто допускает 64-bit INT.
                try:
                    number_int = parse_user_int(value)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise UserVisibleError(
                        f"Field {class_type}.{input_name} expects an integer, received {value!r}.",
                        "workflow_field_integer_expected",
                        [f"{class_type}.{input_name}", value],
                    ) from exc
                try:
                    minimum_int = int(minimum) if minimum is not None else None
                    maximum_int = int(maximum) if maximum is not None else None
                except (TypeError, ValueError):
                    minimum_int = maximum_int = None
                if minimum_int is not None:
                    number_int = max(minimum_int, number_int)
                if maximum_int is not None:
                    number_int = min(maximum_int, number_int)
                step = definition.get("step")
                if step not in (None, "", 0, 0.0):
                    try:
                        step_int = int(float(step))
                        if step_int > 1:
                            origin_int = minimum_int if minimum_int is not None else 0
                            quotient, remainder = divmod(number_int - origin_int, step_int)
                            if remainder * 2 >= step_int:
                                quotient += 1
                            number_int = quotient * step_int + origin_int
                    except (TypeError, ValueError, OverflowError):
                        pass
                if minimum_int is not None:
                    number_int = max(minimum_int, number_int)
                if maximum_int is not None:
                    number_int = min(maximum_int, number_int)
                return number_int

            try:
                number = parse_user_float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise UserVisibleError(
                    f"Field {class_type}.{input_name} expects a number, received {value!r}.",
                    "workflow_field_number_expected",
                    [f"{class_type}.{input_name}", value],
                ) from exc
            if not math.isfinite(number):
                raise UserVisibleError(
                    f"Field {class_type}.{input_name} expects a finite number, received {value!r}.",
                    "workflow_field_finite_expected",
                    [f"{class_type}.{input_name}", value],
                )
            try:
                minimum_float = float(minimum) if minimum is not None else None
                maximum_float = float(maximum) if maximum is not None else None
            except (TypeError, ValueError):
                minimum_float = maximum_float = None
            number = clamp_number(number, minimum_float, maximum_float)
            step = definition.get("step")
            if step not in (None, "", 0, 0.0):
                try:
                    step_value = float(step)
                    if step_value > 0:
                        origin = minimum_float if minimum_float is not None else 0.0
                        quotient = (number - origin) / step_value
                        number = math.floor(quotient + 0.5) * step_value + origin
                except (TypeError, ValueError, OverflowError):
                    pass
            number = clamp_number(number, minimum_float, maximum_float)
            return float(number)

        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _control_is_seed(control: Dict[str, Any]) -> bool:
        control_id = str(control.get("id") or "").lower()
        if control_id == "seed" or control_id.startswith("seed__"):
            return True
        for target in control.get("targets", []):
            if normalize_name(target.get("input", "")) in CONTROL_ALIASES["seed"]:
                return True
        return False

    def _random_seed_for_target(self, target: Dict[str, str]) -> int:
        node, input_name, _inputs = self._resolve_target(target)
        class_type = str(node.get("class_type", ""))
        definition = self.schema.input_definition(class_type, input_name) or {}
        try:
            minimum = int(definition.get("min", 0))
        except (TypeError, ValueError):
            minimum = 0
        try:
            maximum = int(definition.get("max", 0x7FFFFFFFFFFFFFFF))
        except (TypeError, ValueError):
            maximum = 0x7FFFFFFFFFFFFFFF
        # Ограничиваем 63 битами для совместимости с нодами, которые внутри
        # используют signed integer, даже если UI декларирует uint64.
        maximum = min(maximum, 0x7FFFFFFFFFFFFFFF)
        if maximum < minimum:
            maximum = minimum
        return minimum + (uuid.uuid4().int % (maximum - minimum + 1))

    def validate_dimension_target(self, target: Dict[str, str], value: int, semantic: str) -> Optional[str]:
        """Проверяет пиксельный width/height без молчаливого clamp.

        Обычные контролы могут безопасно ограничиваться min/max. Для размера
        это опасно: поле ``megapixels`` с max=16 нельзя превращать в width=1040.
        Поэтому до записи проверяем числовой тип и точный допустимый диапазон.
        """

        node, input_name, inputs = self._resolve_target(target)
        class_type = str(node.get("class_type", ""))
        definition = self.schema.input_definition(class_type, input_name) or {}
        type_name = str(definition.get("type", "")).upper()
        current = inputs.get(input_name)
        if type_name not in {"INT", "FLOAT"} and not (
            isinstance(current, (int, float)) and not isinstance(current, bool)
        ):
            return f"{semantic} target {class_type}.{input_name} is not numeric."
        try:
            minimum = float(definition["min"]) if definition.get("min") is not None else None
            maximum = float(definition["max"]) if definition.get("max") is not None else None
        except (TypeError, ValueError):
            minimum = maximum = None
        if minimum is not None and value < minimum:
            return f"{semantic}={value} is below the minimum {minimum:g} for {class_type}.{input_name}."
        if maximum is not None and value > maximum:
            return f"{semantic}={value} is above the maximum {maximum:g} for {class_type}.{input_name}."
        normalized_input = normalize_name(input_name)
        if any(token in normalized_input for token in ("megapixel", "percent", "ratio", "factor", "scale")):
            return f"{class_type}.{input_name} looks like a scale/ratio field, not a pixel {semantic} field."
        return None

    def _next_runtime_node_id(self) -> str:
        """Returns a numeric node id that cannot collide with the source workflow."""

        numeric_ids = []
        for node_id in self.workflow:
            try:
                numeric_ids.append(int(str(node_id)))
            except (TypeError, ValueError):
                continue
        candidate = max(numeric_ids or [0]) + 1
        while str(candidate) in self.workflow:
            candidate += 1
        return str(candidate)

    @staticmethod
    def _uploaded_remote_path(uploaded: Optional[Dict[str, Any]]) -> str:
        if not uploaded:
            return ""
        name = str(uploaded.get("name") or "")
        subfolder = str(uploaded.get("subfolder") or "")
        path = f"{subfolder}/{name}" if subfolder else name
        return path.replace("\\", "/")

    def _set_candidate_targets_raw(self, candidate: Dict[str, Any], remote_path: str) -> None:
        for target in candidate.get("targets", []):
            self.set_target_raw(target, remote_path)

    def _patch_input_alpha_with_mask_node(
        self,
        mask_binding: Dict[str, Any],
        mask_remote_path: str,
    ) -> None:
        """Replaces Main LoadImage MASK links with a temporary LoadImageMask.

        The original workflow remains untouched because WorkflowPatcher works on
        a deep copy. Only links that used output slot 1 (MASK) of the selected
        main LoadImage node are redirected; its IMAGE output still receives the
        normal Photoshop JPEG.
        """

        if not self.schema.has_class("LoadImageMask"):
            raise UserVisibleError(
                "ComfyUI does not provide the standard LoadImageMask node required "
                "to apply the Photoshop inpaint mask. Update ComfyUI or add a "
                "separate LoadImageMask node to the workflow.",
                "load_image_mask_unavailable",
            )

        source_node_ids = {
            str(node_id)
            for node_id in mask_binding.get("node_ids", [])
            if str(node_id)
        }
        if not source_node_ids:
            raise UserVisibleError(
                "The Main LoadImage MASK binding lost its source node. Reanalyze the workflow.",
                "main_mask_source_missing",
            )

        runtime_node_id = self._next_runtime_node_id()
        rewired = 0
        for node in self.workflow.values():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            for input_name, current in list(inputs.items()):
                if not is_link(current):
                    continue
                if str(current[0]) in source_node_ids and int(current[1]) == 1:
                    inputs[input_name] = [runtime_node_id, 0]
                    rewired += 1

        if not rewired:
            raise UserVisibleError(
                "The selected Inpaint mask is no longer used by the workflow. "
                "Reanalyze the workflow or fix the MASK connections in ComfyUI.",
                "selected_mask_no_longer_used",
            )

        self.workflow[runtime_node_id] = {
            "inputs": {
                "image": mask_remote_path,
                "channel": "red",
            },
            "class_type": "LoadImageMask",
            "_meta": {"title": "img2img helper temporary inpaint mask"},
        }

    def apply(
        self,
        *,
        bindings: Dict[str, Any],
        controls: List[Dict[str, Any]],
        control_values: Dict[str, Any],
        uploaded_image: Dict[str, Any],
        uploaded_mask: Optional[Dict[str, Any]],
        uploaded_references: Optional[Dict[str, Dict[str, Any]]],
        neutral_image: Optional[Dict[str, Any]],
        neutralized_inputs: Optional[List[Dict[str, Any]]],
        width: Optional[int],
        height: Optional[int],
        request_id: str,
        size_selection_mode: str = "auto",
    ) -> Dict[str, Any]:
        # LoadImage обычно принимает путь относительно ComfyUI/input. При
        # subfolder входит в путь, который получает LoadImage.
        remote_name = uploaded_image.get("name", "")
        subfolder = uploaded_image.get("subfolder", "")
        remote_path = f"{subfolder}/{remote_name}" if subfolder else remote_name
        remote_path = remote_path.replace("\\", "/")

        for target in bindings.get("input_image", []):
            # Путь вернул сам /upload/image. Он может отсутствовать в списке
            # COMBO из ранее полученного /object_info, поэтому записываем его
            # без enum-проверки.
            self.set_target_raw(target, remote_path)

        mask_binding = bindings.get("inpaint_mask") if isinstance(bindings.get("inpaint_mask"), dict) else {}
        if uploaded_mask:
            mask_name = uploaded_mask.get("name", "")
            mask_subfolder = uploaded_mask.get("subfolder", "")
            mask_remote_path = f"{mask_subfolder}/{mask_name}" if mask_subfolder else mask_name
            mask_remote_path = mask_remote_path.replace("\\", "/")
            if mask_binding.get("mode") == "load_image_mask":
                for target in mask_binding.get("targets", []):
                    self.set_target_raw(target, mask_remote_path)
                for target in mask_binding.get("channel_targets", []):
                    self.set_target(target, "red")
            elif mask_binding.get("mode") == "input_alpha":
                self._patch_input_alpha_with_mask_node(mask_binding, mask_remote_path)

        # A selected reference with no file receives the neutral image silently.
        # Unselected LoadImage candidates are neutralized only when explicitly requested.
        uploaded_references = uploaded_references or {}
        neutral_remote_path = self._uploaded_remote_path(neutral_image)
        for reference_binding in bindings.get("reference_images", []):
            binding_id = str(reference_binding.get("id") or "")
            uploaded_reference = uploaded_references.get(binding_id)
            if uploaded_reference:
                reference_path = self._uploaded_remote_path(uploaded_reference)
                for target in reference_binding.get("targets", []):
                    self.set_target_raw(target, reference_path)
            elif neutral_remote_path and reference_binding.get("load_image"):
                self._set_candidate_targets_raw(reference_binding, neutral_remote_path)

        for candidate in neutralized_inputs or []:
            if neutral_remote_path:
                self._set_candidate_targets_raw(candidate, neutral_remote_path)
        dimension_issues: List[str] = []
        if bindings.get("width"):
            if not width or width <= 0:
                dimension_issues.append("Photoshop did not provide a valid width.")
            else:
                issue = self.validate_dimension_target(bindings["width"], int(width), "width")
                if issue:
                    dimension_issues.append(issue)
        if bindings.get("height"):
            if not height or height <= 0:
                dimension_issues.append("Photoshop did not provide a valid height.")
            else:
                issue = self.validate_dimension_target(bindings["height"], int(height), "height")
                if issue:
                    dimension_issues.append(issue)
        apply_dimensions = not dimension_issues
        if dimension_issues:
            details = "\n• ".join(dimension_issues)
            if str(size_selection_mode or "auto") == "auto":
                self.warning(
                    "Automatic width/height binding was skipped because it cannot accept the requested Photoshop size:\n• "
                    + details
                    + "\nThe input image size is used instead.",
                    "generation_size_binding_skipped",
                )
                apply_dimensions = False
            else:
                raise UserVisibleError(
                    "The selected workflow size fields cannot accept the requested Photoshop size:\n• "
                    + details
                    + "\n\nOpen workflow settings and choose Input image size, Automatic, or another width/height pair.",
                    "selected_size_rejected",
                    [details],
                )
        if apply_dimensions and bindings.get("width"):
            self.set_target(bindings["width"], int(width))
        if apply_dimensions and bindings.get("height"):
            self.set_target(bindings["height"], int(height))

        controls_by_id = {item.get("id"): item for item in controls}
        patched_seed_targets: Set[Tuple[str, str]] = set()

        # Seed является особым параметром. Даже если пользователь не добавил
        # его в интерфейс, каждая генерация должна получить новое значение.
        # Если поле видно, введённое значение сохраняется до нажатия кнопки ↻.
        for control in controls:
            control_id = str(control.get("id") or "")
            if not self._control_is_seed(control):
                continue
            targets = control.get("targets", [])
            if not targets:
                if control_id in control_values:
                    self.warning(
                        f"Parameter {control_id} was not applied: the schema has no target inputs. "
                        "Reanalyze the workflow.",
                        "generation_parameter_no_targets",
                        [control_id],
                    )
                continue
            supplied = control_id in control_values and str(control_values[control_id]).strip().lower() not in {"", "random", "-1"}
            seed_value: Any = control_values.get(control_id) if supplied else self._random_seed_for_target(targets[0])
            for target in targets:
                self.set_target(target, seed_value)
                patched_seed_targets.add((str(target.get("node_id")), str(target.get("input"))))
            actual_seed = int(self.workflow[str(targets[0]["node_id"])]["inputs"][str(targets[0]["input"])])
            self.generated_seeds[control_id or "seed"] = actual_seed

        for control_id, value in control_values.items():
            control = controls_by_id.get(control_id)
            if not control:
                self.warning(
                    f"Parameter {control_id} was not applied: it is missing from the current schema. "
                    "Reanalyze the workflow.",
                    "generation_parameter_missing",
                    [control_id],
                )
                continue
            if self._control_is_seed(control):
                continue
            targets = control.get("targets", [])
            if not targets:
                self.warning(
                    f"Parameter {control_id} was not applied: the schema has no target inputs. "
                    "Reanalyze the workflow.",
                    "generation_parameter_no_targets",
                    [control_id],
                )
                continue
            for target in targets:
                self.set_target(target, value)

        # Страховочная проходка для нестандартных sampler-нод: любое локальное
        # поле seed/noise_seed, которое анализатор не оформил как control, тоже
        # рандомизируется. Связанные входы не трогаются.
        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs", {})
            for input_name, current_value in list(inputs.items()):
                key = (str(node_id), str(input_name))
                if key in patched_seed_targets or is_link(current_value):
                    continue
                if normalize_name(input_name) not in CONTROL_ALIASES["seed"]:
                    continue
                if not isinstance(current_value, int) or isinstance(current_value, bool):
                    continue
                target = {"node_id": str(node_id), "input": str(input_name)}
                seed_value = self._random_seed_for_target(target)
                self.set_target(target, seed_value)
                self.generated_seeds[f"node_{node_id}__{input_name}"] = int(inputs[input_name])

        # Чтобы результаты разных запросов не смешивались в output, меняем
        # filename_prefix только там, где это обычное локальное поле.
        for node in self.workflow.values():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs", {})
            if "filename_prefix" in inputs and not is_link(inputs["filename_prefix"]):
                inputs["filename_prefix"] = f"{OUTPUT_SUBFOLDER}/{safe_filename(request_id)}"

        return self.workflow



# ============================================================================
# FORGE NEO: HTTP-КЛИЕНТ, JSON-СХЕМЫ И КАТАЛОГИ
# JSON-схема описывает UI и отображение control -> payload/options. Каталоги
# checkpoint/module/vae/sampler и т. п. всегда обновляются из работающего Forge.
# ============================================================================
class ForgeClient:
    """Минимальный клиент Forge Neo /sdapi/v1 без A1111 fallback."""

    def __init__(self, host: str, port: int, timeout: float = 60.0):
        self.host = normalize_comfy_host(host)
        self.port = int(port)
        self.timeout = float(timeout)

    def _url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}/{str(path).lstrip('/')}"

    def _request(self, path: str, payload: Optional[Dict[str, Any]] = None,
                 timeout: Optional[float] = None) -> Any:
        data = None
        headers: Dict[str, str] = {}
        method = "GET"
        if payload is not None:
            data = json_dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = urllib.request.Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(exc)
            details = format_http_error_body(body)
            suffix = f"\n\n{details}" if details else ""
            raise UserVisibleError(f"Forge Neo HTTP {exc.code}{suffix}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UserVisibleError(f"Forge Neo is unavailable at {self.host}:{self.port}: {exc}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except Exception:
            return raw.decode("utf-8", errors="replace")

    def get_json(self, path: str, timeout: float = 30.0) -> Any:
        return self._request(path, timeout=timeout)

    def post_json(self, path: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Any:
        return self._request(path, payload=payload, timeout=timeout)

    def interrupt(self) -> None:
        try:
            self.post_json("sdapi/v1/interrupt", {}, timeout=10)
        except Exception:
            LOGGER.warning("Could not send interrupt to Forge Neo")


def current_forge_client() -> ForgeClient:
    return ForgeClient(RUNTIME.backend_host, RUNTIME.forge_port, timeout=RUNTIME.generation_timeout)


def _strip_checkpoint_hash(value: Any) -> str:
    return re.sub(r"\s+\[[^\]]+\]\s*$", "", str(value or "")).strip()


def _schema_deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        result = copy.deepcopy(base)
        for key, value in override.items():
            if key in result:
                result[key] = _schema_deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result
    if isinstance(override, list):
        return copy.deepcopy(override)
    return copy.deepcopy(override)


def _folder_contains_forge_schema(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    for path in folder.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and raw.get("kind") == FORGE_SCHEMA_KIND and raw.get("backend") == "forge":
            return True
    return False


def resolve_forge_schema_dir(schema_folder: Any = "") -> Path:
    raw = str(schema_folder or "").strip()
    if raw:
        folder = Path(raw).expanduser()
        if not folder.is_dir():
            raise UserVisibleError(
                f"Forge schema folder does not exist: {folder}",
                "forge_schema_folder_missing",
                [folder],
            )
        return folder.resolve()
    for folder in DEFAULT_FORGE_SCHEMA_DIRS:
        if _folder_contains_forge_schema(folder):
            return folder.resolve()
    raise UserVisibleError(
        "Forge schema folder was not found. Select it in the script settings.",
        "forge_schema_folder_not_selected",
    )


def _normalize_forge_model_hint_text(value: Any) -> str:
    """Normalize checkpoint names and rule tokens for filename-only matching."""

    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _forge_model_hint_basename(value: Any) -> str:
    """Return a portable basename even when a Windows path is parsed on another OS."""

    text = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." in text:
        text = text.rsplit(".", 1)[0]
    return text


def _load_forge_model_hints(schema_folder: Any = "") -> Dict[str, Any]:
    """Load the optional model helpTip database.

    This file is deliberately non-critical. Missing, malformed or incompatible
    data only disables model-specific helpTips and is written to the Python log.
    """

    try:
        schema_dir = resolve_forge_schema_dir(schema_folder)
        path = schema_dir / FORGE_MODEL_HINTS_FILENAME
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("root must be a JSON object")
        if data.get("kind") != FORGE_MODEL_HINTS_KIND:
            raise ValueError(f"unexpected kind: {data.get('kind')!r}")
        if int(data.get("version") or 0) != FORGE_MODEL_HINTS_VERSION:
            raise ValueError(
                f"unsupported version: {data.get('version')!r}; "
                f"expected {FORGE_MODEL_HINTS_VERSION}"
            )
        if not isinstance(data.get("profiles"), dict) or not isinstance(data.get("rules"), list):
            raise ValueError("profiles must be an object and rules must be an array")
        return data
    except Exception as exc:
        LOGGER.warning("Ignoring optional Forge model hints: %s", exc)
        return {}


def _forge_model_hint_rule_matches(target: str, match: Any) -> bool:
    if not target or not isinstance(match, dict):
        return False

    def tokens(key: str) -> List[str]:
        value = match.get(key)
        if not isinstance(value, list):
            return []
        return [
            token for token in (_normalize_forge_model_hint_text(item) for item in value)
            if token
        ]

    required = tokens("all")
    optional = tokens("any")
    excluded = tokens("not")
    starts = _normalize_forge_model_hint_text(match.get("starts"))
    if required and any(token not in target for token in required):
        return False
    if optional and not any(token in target for token in optional):
        return False
    if excluded and any(token in target for token in excluded):
        return False
    if starts and not target.startswith(starts):
        return False
    return bool(required or optional or starts)


def _render_forge_model_hint(rule: Dict[str, Any], profile: Dict[str, Any]) -> str:
    explicit = rule.get("help") or profile.get("help")
    if explicit:
        return str(explicit).strip()

    family = str(profile.get("family") or "").strip()
    label = str(rule.get("label") or family).strip()
    lines: List[str] = []
    if label and family and label.lower() != family.lower():
        lines.append(f"{label} — {family}")
    elif label or family:
        lines.append(label or family)

    support = str(profile.get("forge_support") or "").strip()
    if support:
        lines.append(f"Forge Neo support: {support}")

    settings = profile.get("settings") if isinstance(profile.get("settings"), dict) else {}
    setting_labels = (
        ("sampler", "Sampler"),
        ("scheduler", "Scheduler"),
        ("steps", "Steps"),
        ("cfg", "CFG"),
        ("distilled_cfg", "Distilled CFG"),
        ("shift", "Shift"),
        ("denoise", "Denoise"),
    )
    parts = []
    for key, caption in setting_labels:
        value = settings.get(key)
        if value not in (None, ""):
            parts.append(f"{caption}: {value}")
    if parts:
        lines.append(" · ".join(parts))

    notes: List[str] = []
    for value in (rule.get("note"), profile.get("note")):
        note = str(value or "").strip()
        if note and note not in notes:
            notes.append(note)
    lines.extend(notes)
    return "\n".join(line for line in lines if line).strip()


def _forge_model_help_tip(hints: Dict[str, Any], *candidates: Any) -> str:
    if not hints:
        return ""
    target = ""
    for candidate in candidates:
        basename = _forge_model_hint_basename(candidate)
        normalized = _normalize_forge_model_hint_text(basename)
        if normalized:
            target = normalized
            break
    if not target:
        return ""

    profiles = hints.get("profiles") if isinstance(hints.get("profiles"), dict) else {}
    for rule in hints.get("rules") if isinstance(hints.get("rules"), list) else []:
        if not isinstance(rule, dict) or not _forge_model_hint_rule_matches(target, rule.get("match")):
            continue
        profile = profiles.get(str(rule.get("profile") or ""))
        if not isinstance(profile, dict):
            LOGGER.warning("Forge model hint rule %s refers to missing profile %s", rule.get("id"), rule.get("profile"))
            return ""
        return _render_forge_model_hint(rule, profile)
    return ""


def _forge_catalog_with_model_hints(catalog: Dict[str, Any], schema_folder: Any = "") -> Dict[str, Any]:
    """Return a public catalog copy decorated from the current optional hints file.

    Checkpoint metadata stays cached, while editing forge_model_hints.json takes
    effect on the next catalog request without another /sd-models call.
    """

    result = copy.deepcopy(catalog)
    checkpoints = result.get("checkpoints")
    if not isinstance(checkpoints, list):
        return result
    hints = _load_forge_model_hints(schema_folder)
    for item in checkpoints:
        if not isinstance(item, dict):
            continue
        source = item.pop("_hint_source", "") or item.get("value") or item.get("label")
        item.pop("help", None)
        help_tip = _forge_model_help_tip(hints, source)
        if help_tip:
            item["help"] = help_tip
    return result


def _read_forge_schema_file(
    path: Path,
    schema_dir: Path,
    stack: Optional[Set[str]] = None,
    source: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stack = set(stack or set())
    if source is None:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            raise UserVisibleError(f"Could not read Forge schema: {path}") from exc
        except json.JSONDecodeError as exc:
            raise UserVisibleError(
                f"Invalid Forge schema JSON {path.name}: {exc}",
                "forge_schema_json_invalid",
                [path.name, exc.lineno, exc.colno, exc.msg],
            ) from exc
    else:
        data = copy.deepcopy(source)
    if not isinstance(data, dict):
        raise UserVisibleError(
            f"Forge schema {path.name} must be a JSON object.",
            "forge_schema_root_invalid",
            [path.name],
        )
    if data.get("kind") != FORGE_SCHEMA_KIND or str(data.get("backend")) != "forge":
        raise UserVisibleError(
            f"File {path.name} is not an {APP_NAME} Forge schema.",
            "forge_schema_kind_invalid",
            [path.name],
        )
    raw_schema_version = data.get("schema_version")
    try:
        schema_version = int(raw_schema_version or 0)
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version != FORGE_SCHEMA_VERSION:
        display_version = raw_schema_version if raw_schema_version not in (None, "") else 0
        raise UserVisibleError(
            f"Unsupported schema_version in {path.name}: {display_version}",
            "forge_schema_version_invalid",
            [path.name, display_version, FORGE_SCHEMA_VERSION],
        )
    parent_id = str(data.get("extends") or "").strip()
    if parent_id:
        if parent_id in stack:
            raise UserVisibleError(
                f"Circular Forge schema inheritance: {parent_id}",
                "forge_schema_inheritance_cycle",
                [parent_id],
            )
        parent_path = schema_dir / f"{safe_filename(parent_id, parent_id)}.json"
        if not parent_path.is_file():
            raise UserVisibleError(
                f"Base Forge schema was not found: {parent_id}",
                "forge_schema_base_missing",
                [parent_id],
            )
        stack.add(parent_id)
        parent = _read_forge_schema_file(parent_path, schema_dir, stack)
        data = _schema_deep_merge(parent, data)
    data.pop("abstract", None)
    data.pop("extends", None)
    return data


def list_forge_schemas(
    schema_folder: Any = "",
) -> Tuple[List[Dict[str, Any]], Path, List[Dict[str, Any]]]:
    """Lists usable Forge schemas and reports files that could not be loaded.

    Files that are valid JSON but are not img2img helper Forge schemas are
    ignored. A file that declares itself as a Forge schema is fully
    validated, including inheritance, version and numeric list metadata.
    """

    schema_dir = resolve_forge_schema_dir(schema_folder)
    items: List[Dict[str, Any]] = []
    invalid_schemas: List[Dict[str, Any]] = []

    def report_invalid(
        path: Path,
        message: str,
        code: str = "",
        params: Optional[Sequence[Any]] = None,
    ) -> None:
        rendered = str(message or "Unknown schema error").strip()
        item: Dict[str, Any] = {"file": path.name, "message": rendered}
        if code:
            item["code"] = str(code)
        if params:
            item["params"] = [str(value) for value in params]
        invalid_schemas.append(item)
        LOGGER.warning("Skipped invalid Forge schema %s: %s", path, rendered)

    for path in sorted(schema_dir.glob("*.json"), key=lambda item: item.name.lower()):
        # Optional checkpoint helpTip database is not a Forge UI schema. Skip it
        # before JSON parsing so even a malformed hints file stays non-critical.
        if path.name.lower() == FORGE_MODEL_HINTS_FILENAME.lower():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            report_invalid(path, f"Could not read the file: {exc}")
            continue
        except json.JSONDecodeError as exc:
            report_invalid(
                path,
                f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
                "forge_schema_json_invalid",
                [path.name, exc.lineno, exc.colno, exc.msg],
            )
            continue

        # The schema folder may contain unrelated JSON files. Ignore them unless
        # they explicitly identify themselves as Forge schemas for this helper.
        if not isinstance(raw, dict) or raw.get("kind") != FORGE_SCHEMA_KIND or raw.get("backend") != "forge":
            continue

        try:
            # Validate abstract base schemas too. They are not shown in the UI,
            # but a broken base would otherwise make all derived presets vanish
            # later with a less useful error.
            _read_forge_schema_file(path, schema_dir, source=raw)
            if raw.get("abstract"):
                continue
            # Runtime/profile identity is the JSON filename, not the optional
            # internal id. A copied schema may intentionally keep the same id.
            schema_id = path.stem
            order = int(raw.get("order") or 1000)
        except UserVisibleError as exc:
            report_invalid(path, str(exc), exc.code, exc.params)
            continue
        except (TypeError, ValueError) as exc:
            order_value = raw.get("order")
            report_invalid(
                path,
                str(exc),
                "forge_schema_order_invalid",
                [path.name, order_value],
            )
            continue

        items.append({
            "id": schema_id,
            "label": str(raw.get("label") or schema_id),
            "file": path.name,
            "order": order,
        })

    items.sort(key=lambda item: (item.get("order", 1000), item["label"].lower()))
    for item in items:
        item.pop("order", None)
    return items, schema_dir, invalid_schemas


def get_forge_schema(schema_id: str, schema_folder: Any = "") -> Dict[str, Any]:
    schema_id = str(schema_id or "").strip()
    schema_dir = resolve_forge_schema_dir(schema_folder)
    if not schema_id or Path(schema_id).name != schema_id or schema_id in {".", ".."}:
        raise UserVisibleError(
            f"Forge UI preset was not found: {schema_id}",
            "forge_schema_missing",
            [schema_id],
        )
    path = (schema_dir / f"{schema_id}.json").resolve()
    if path.parent != schema_dir or not path.is_file():
        raise UserVisibleError(
            f"Forge UI preset was not found: {schema_id}",
            "forge_schema_missing",
            [schema_id],
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise UserVisibleError(f"Could not read Forge schema: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UserVisibleError(
            f"Invalid Forge schema JSON {path.name}: {exc}",
            "forge_schema_json_invalid",
            [path.name, exc.lineno, exc.colno, exc.msg],
        ) from exc
    if not isinstance(raw, dict):
        raise UserVisibleError(
            f"Forge schema {path.name} must be a JSON object.",
            "forge_schema_root_invalid",
            [path.name],
        )
    if raw.get("kind") != FORGE_SCHEMA_KIND or raw.get("backend") != "forge":
        raise UserVisibleError(
            f"File {path.name} is not an {APP_NAME} Forge schema.",
            "forge_schema_kind_invalid",
            [path.name],
        )
    if raw.get("abstract"):
        raise UserVisibleError(
            f"Forge UI preset was not found: {schema_id}",
            "forge_schema_missing",
            [schema_id],
        )
    schema = _read_forge_schema_file(path, schema_dir, source=raw)
    schema["workspace_id"] = schema_id
    schema["workflow_id"] = "forge:" + schema_id
    schema["workflow_name"] = str(schema.get("label") or schema_id)
    schema["relative_path"] = path.name
    schema["valid"] = True
    schema["diagnostics"] = []
    default_size_multiple = 16
    try:
        size_multiple = int(schema.get("size_multiple", default_size_multiple))
    except (TypeError, ValueError):
        size_multiple = default_size_multiple
    schema["size_multiple"] = max(1, min(256, size_multiple))
    controls = schema.get("controls") if isinstance(schema.get("controls"), list) else []
    # Для Forge список полей главного окна полностью определяется visible
    # в JSON-схеме. ComfyUI по-прежнему формирует рекомендации анализатором.
    schema["recommended_controls"] = [
        str(control.get("id"))
        for control in controls
        if isinstance(control, dict)
        and control.get("id")
        and (bool(control.get("visible")) or bool(control.get("required_visible")))
    ]
    capabilities = schema.get("capabilities") if isinstance(schema.get("capabilities"), dict) else {}
    generation = schema.get("generation") if isinstance(schema.get("generation"), dict) else {}
    input_mode = str(generation.get("input_mode") or "img2img").strip().lower()
    image_stitch = schema.get("image_stitch") if isinstance(schema.get("image_stitch"), dict) else {}
    stitch_supported = _forge_bool(capabilities.get("image_stitch")) and input_mode != "single_image"
    capabilities["image_stitch"] = stitch_supported
    capabilities["max_image_inputs"] = _forge_image_stitch_limit(capabilities)
    schema["capabilities"] = capabilities
    if (
        stitch_supported
        and _forge_bool(image_stitch.get("visible"))
        and "image_stitch" not in schema["recommended_controls"]
    ):
        schema["recommended_controls"].append("image_stitch")
    schema["image_stitch_default"] = (
        _forge_bool(schema.get("image_stitch_default", False))
        if stitch_supported else False
    )
    schema.setdefault("bindings", {"reference_images": []})
    return schema


FORGE_CATALOG_SOURCES = {
    "checkpoints", "modules", "samplers", "schedulers", "loras", "upscalers"
}
FORGE_CATALOG_CACHE: Dict[str, Any] = {}
FORGE_CATALOG_CACHE_SERVER: Optional[Tuple[str, int]] = None
FORGE_CATALOG_CACHE_LOCK = threading.RLock()


def clear_forge_catalog_cache() -> None:
    global FORGE_CATALOG_CACHE_SERVER
    with FORGE_CATALOG_CACHE_LOCK:
        FORGE_CATALOG_CACHE.clear()
        FORGE_CATALOG_CACHE_SERVER = None


def _forge_catalog_server_key() -> Tuple[str, int]:
    return normalize_comfy_host(RUNTIME.backend_host), int(RUNTIME.forge_port)


def _update_forge_catalog_current(options: Dict[str, Any]) -> None:
    if not isinstance(options, dict):
        return
    with FORGE_CATALOG_CACHE_LOCK:
        FORGE_CATALOG_CACHE["current"] = {
            "checkpoint": _strip_checkpoint_hash(options.get("sd_model_checkpoint")),
            "modules": [
                str(item) for item in (options.get("forge_additional_modules") or [])
            ] if isinstance(options.get("forge_additional_modules"), list) else [],
        }


def forge_catalog(
    sources: Optional[Sequence[str]] = None, *, force: bool = False,
    schema_folder: Any = "",
) -> Dict[str, Any]:
    """Load only catalog sources required by the selected Forge schema.

    The process-level cache accumulates already loaded sources. A manual schema
    refresh forces only the requested sources. A backend probe clears the cache.
    """
    global FORGE_CATALOG_CACHE_SERVER

    requested = (
        set(FORGE_CATALOG_SOURCES)
        if sources is None
        else {str(item) for item in sources if str(item) in FORGE_CATALOG_SOURCES}
    )
    server_key = _forge_catalog_server_key()

    with FORGE_CATALOG_CACHE_LOCK:
        if FORGE_CATALOG_CACHE_SERVER != server_key:
            FORGE_CATALOG_CACHE.clear()
            FORGE_CATALOG_CACHE_SERVER = server_key

        if not requested:
            return _forge_catalog_with_model_hints(FORGE_CATALOG_CACHE, schema_folder)

        refresh_sources = set(requested) if force else {
            source for source in requested if source not in FORGE_CATALOG_CACHE
        }
        needs_options = bool(requested.intersection({"checkpoints", "modules"})) and (
            force or "current" not in FORGE_CATALOG_CACHE
        )
        client = current_forge_client()

        if needs_options:
            options = client.get_json("sdapi/v1/options", timeout=30)
            if not isinstance(options, dict):
                options = {}
            _update_forge_catalog_current(options)

        if "checkpoints" in refresh_sources:
            models = client.get_json("sdapi/v1/sd-models", timeout=60)
            model_items: List[Dict[str, Any]] = []
            for item in models if isinstance(models, list) else []:
                if not isinstance(item, dict):
                    continue
                title = _strip_checkpoint_hash(
                    item.get("title") or item.get("model_name") or item.get("filename")
                )
                if title:
                    # Keep only a private matching source in the process cache. It
                    # is removed before the catalog is returned to JSX.
                    hint_source = (
                        item.get("filename")
                        or item.get("model_name")
                        or item.get("title")
                        or title
                    )
                    model_items.append({
                        "label": title,
                        "value": title,
                        "_hint_source": str(hint_source or title),
                    })
            model_items.sort(key=lambda item: item["label"].lower())
            FORGE_CATALOG_CACHE["checkpoints"] = model_items

        if "modules" in refresh_sources:
            modules = client.get_json("sdapi/v1/sd-modules", timeout=60)
            module_items: List[Dict[str, str]] = []
            for item in modules if isinstance(modules, list) else []:
                if not isinstance(item, dict):
                    continue
                label = str(
                    item.get("model_name")
                    or Path(str(item.get("filename") or "")).name
                    or ""
                ).strip()
                value = str(item.get("filename") or item.get("model_name") or "")
                if label and value:
                    module_items.append({"label": label, "value": value})
            module_items.sort(key=lambda item: item["label"].lower())
            FORGE_CATALOG_CACHE["modules"] = module_items

        if "samplers" in refresh_sources:
            samplers = client.get_json("sdapi/v1/samplers", timeout=30)
            sampler_rows = samplers if isinstance(samplers, list) else []
            FORGE_CATALOG_CACHE["samplers"] = sorted({
                str(item.get("name"))
                for item in sampler_rows
                if isinstance(item, dict) and item.get("name")
            })

        if "schedulers" in refresh_sources:
            schedulers = client.get_json("sdapi/v1/schedulers", timeout=30)
            scheduler_rows = schedulers if isinstance(schedulers, list) else []
            FORGE_CATALOG_CACHE["schedulers"] = [
                str(item.get("label") or item.get("name"))
                for item in scheduler_rows
                if isinstance(item, dict) and (item.get("label") or item.get("name"))
            ]

        if "upscalers" in refresh_sources:
            try:
                upscalers = client.get_json("sdapi/v1/upscalers", timeout=30)
            except UserVisibleError:
                LOGGER.warning(
                    "Forge Neo did not return the upscaler list; the Upscaler schema will remain unavailable"
                )
                upscalers = []
            upscaler_items: List[Dict[str, str]] = []
            upscaler_names: Set[str] = set()
            for item in upscalers if isinstance(upscalers, list) else []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name or name in upscaler_names:
                    continue
                upscaler_names.add(name)
                upscaler_items.append({"label": name, "value": name})
            FORGE_CATALOG_CACHE["upscalers"] = upscaler_items

        if "loras" in refresh_sources:
            try:
                loras = client.get_json("sdapi/v1/loras", timeout=60)
            except UserVisibleError:
                LOGGER.warning("Forge Neo did not return the LoRA list; the LoRA button will be disabled")
                loras = []
            lora_names: Set[str] = set()
            for item in loras if isinstance(loras, list) else []:
                if isinstance(item, str):
                    name = item.strip()
                elif isinstance(item, dict):
                    name = str(
                        item.get("name")
                        or item.get("alias")
                        or item.get("model_name")
                        or item.get("title")
                        or item.get("filename")
                        or item.get("path")
                        or ""
                    ).strip()
                else:
                    name = ""
                if name:
                    lora_names.add(name)
            FORGE_CATALOG_CACHE["loras"] = sorted(lora_names, key=str.lower)

        return _forge_catalog_with_model_hints(FORGE_CATALOG_CACHE, schema_folder)

# ============================================================================
# FORGE: IMAGESTITCH, НОРМАЛИЗАЦИЯ И ПРОВЕРКА ЗНАЧЕНИЙ UI
# Финальная проверка здесь обязательна даже после JSX: Action/DESC могут содержать
# устаревшие значения, а каталог Forge способен измениться между запусками.
# ============================================================================
def _forge_image_stitch_limit(capabilities: Dict[str, Any]) -> int:
    try:
        value = int(capabilities.get("max_image_inputs") or 3)
    except (TypeError, ValueError):
        value = 3
    return max(1, min(3, value))


def _is_supported_forge_reference(path: Path) -> bool:
    return path.suffix.lower() in FORGE_REFERENCE_EXTENSIONS


def _file_data_url(path: Path) -> str:
    """Encodes a trusted generated image without changing its pixels."""

    try:
        content = path.read_bytes()
    except OSError as exc:
        raise UserVisibleError(f"Could not read image: {path}") from exc
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")


IMAGESTITCH_CACHE_LOCK = threading.RLock()
IMAGESTITCH_CACHE: "OrderedDict[Tuple[str, int, int], str]" = OrderedDict()
IMAGESTITCH_CACHE_BYTES = 0


def _imagestitch_cache_key(path: Path) -> Optional[Tuple[str, int, int]]:
    try:
        resolved = path.resolve()
        stat = resolved.stat()
        return (
            os.path.normcase(str(resolved)),
            int(stat.st_size),
            int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
        )
    except OSError:
        return None


def _imagestitch_cache_get(key: Optional[Tuple[str, int, int]]) -> Optional[str]:
    if key is None:
        return None
    with IMAGESTITCH_CACHE_LOCK:
        cached = IMAGESTITCH_CACHE.get(key)
        if cached is not None:
            IMAGESTITCH_CACHE.move_to_end(key)
        return cached


def _imagestitch_cache_put(key: Optional[Tuple[str, int, int]], value: str) -> None:
    global IMAGESTITCH_CACHE_BYTES
    if key is None or len(value) > IMAGESTITCH_CACHE_MAX_BYTES:
        return
    with IMAGESTITCH_CACHE_LOCK:
        # Новые size/mtime того же пути вытесняют старую нормализацию сразу.
        for stale_key in [item for item in IMAGESTITCH_CACHE if item[0] == key[0] and item != key]:
            IMAGESTITCH_CACHE_BYTES -= len(IMAGESTITCH_CACHE.pop(stale_key))
        previous = IMAGESTITCH_CACHE.pop(key, None)
        if previous is not None:
            IMAGESTITCH_CACHE_BYTES -= len(previous)
        IMAGESTITCH_CACHE[key] = value
        IMAGESTITCH_CACHE_BYTES += len(value)
        while (
            len(IMAGESTITCH_CACHE) > IMAGESTITCH_CACHE_MAX_ITEMS
            or IMAGESTITCH_CACHE_BYTES > IMAGESTITCH_CACHE_MAX_BYTES
        ):
            _, evicted_value = IMAGESTITCH_CACHE.popitem(last=False)
            IMAGESTITCH_CACHE_BYTES -= len(evicted_value)


def _forge_reference_data_url(path: Path) -> str:
    """Normalizes an external ImageStitch file before sending it to Forge.

    A filename extension is not proof that the file contains a decodable JPEG,
    PNG or WebP. Photoshop can open several variants that Forge/Pillow rejects,
    and users can also encounter incorrectly renamed files. ImageStitch calls
    ``decode_base64_to_image`` for every gallery item, so one invalid byte
    stream aborts the whole request with ``Invalid encoded image``.

    The standalone helper therefore decodes the source with Pillow first and
    serializes a clean PNG. This also removes metadata/container peculiarities
    while preserving RGB/RGBA pixels and transparency.
    """

    cache_key = _imagestitch_cache_key(path)
    cached = _imagestitch_cache_get(cache_key)
    if cached is not None:
        return cached

    image_module = PIL_IMAGE_MODULE
    image_ops_module = PIL_IMAGE_OPS_MODULE
    if image_module is None or image_ops_module is None:
        raise UserVisibleError(
            "Pillow was not initialized during Python startup. "
            f"Restart {APP_NAME}. Log: {LOG_FILE}"
        )
    try:
        with image_module.open(str(path)) as source:
            source.load()
            image = image_ops_module.exif_transpose(source)
            bands = image.getbands()
            if "A" in bands or image.mode in {"P", "LA"}:
                image = image.convert("RGBA")
            else:
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", compress_level=6)
            content = buffer.getvalue()
    except Exception as exc:
        raise UserVisibleError(
            f"ImageStitch could not decode the selected image: {path}",
            "image_stitch_decode_failed",
            [path],
        ) from exc

    # Local round-trip validation catches an incomplete/empty encoder result
    # before the request reaches Forge Neo.
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise UserVisibleError(
            f"ImageStitch could not prepare a valid PNG image: {path}",
            "image_stitch_prepare_failed",
            [path],
        )
    result = "data:image/png;base64," + base64.b64encode(content).decode("ascii")
    if _imagestitch_cache_key(path) == cache_key:
        _imagestitch_cache_put(cache_key, result)
    return result


def _decode_forge_image(value: Any, destination_without_suffix: Path) -> Path:
    raw_value = str(value or "")
    if "," in raw_value and raw_value.lower().startswith("data:"):
        raw_value = raw_value.split(",", 1)[1]
    try:
        content = base64.b64decode(raw_value)
    except Exception as exc:
        raise UserVisibleError("Forge Neo returned an invalid base64 image.") from exc
    if content.startswith(b"\x89PNG"):
        suffix = ".png"
    elif content.startswith(b"\xff\xd8"):
        suffix = ".jpg"
    elif content.startswith(b"RIFF") and b"WEBP" in content[:12]:
        suffix = ".webp"
    else:
        suffix = ".bin"
    destination = destination_without_suffix.with_suffix(suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return destination


_OPTION_MISSING = object()


def _schema_option_controls(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    controls = schema.get("controls") if isinstance(schema.get("controls"), list) else []
    result: List[Dict[str, Any]] = []
    for control in controls:
        if not isinstance(control, dict):
            continue
        option_key = str(control.get("option_key") or "").strip()
        if option_key:
            result.append(control)
    return result


def _apply_forge_options(
    client: ForgeClient,
    values: Dict[str, Any],
    schema: Dict[str, Any],
    runtime_catalog: Optional[Dict[str, Any]] = None,
) -> None:
    """Apply model, module, and schema-specific Forge options.

    Utility schemas such as Face Restore do not use a checkpoint or additional
    modules, so they must not clear the model state of a regular generation
    schema. Controls with ``option_key`` are persistent Forge settings: their
    selected values remain in ``/sdapi/v1/options`` after generation.
    """
    controls = schema.get("controls") if isinstance(schema.get("controls"), list) else []
    checkpoint_control: Optional[Dict[str, Any]] = None
    modules_control: Optional[Dict[str, Any]] = None
    option_controls = _schema_option_controls(schema)
    for control in controls:
        if not isinstance(control, dict):
            continue
        control_id = str(control.get("id") or "")
        source = str(control.get("source") or "")
        if checkpoint_control is None and (source == "checkpoints" or control_id == "checkpoint"):
            checkpoint_control = control
        if modules_control is None and (source == "modules" or control_id == "modules"):
            modules_control = control

    if checkpoint_control is None and modules_control is None and not option_controls:
        return

    options = client.get_json("sdapi/v1/options", timeout=30)
    if not isinstance(options, dict):
        options = {}
    changed = False

    runtime_catalog = runtime_catalog or {}

    if checkpoint_control is not None:
        checkpoint = _strip_checkpoint_hash(
            _forge_control_value(checkpoint_control, values, runtime_catalog)
        )
        if checkpoint and _strip_checkpoint_hash(options.get("sd_model_checkpoint")) != checkpoint:
            options["sd_model_checkpoint"] = checkpoint
            changed = True

    if modules_control is not None:
        modules = _forge_control_value(modules_control, values, runtime_catalog)
        normalized_modules = [str(item) for item in modules if str(item)] if isinstance(modules, list) else []
        current_modules = [
            str(item) for item in (options.get("forge_additional_modules") or [])
        ] if isinstance(options.get("forge_additional_modules"), list) else []
        if current_modules != normalized_modules:
            options["forge_additional_modules"] = normalized_modules
            changed = True

    for control in option_controls:
        control_id = str(control.get("id") or "")
        option_key = str(control.get("option_key") or "").strip()
        if not control_id or not option_key:
            continue
        desired_value = _forge_control_value(control, values, runtime_catalog)
        if options.get(option_key, _OPTION_MISSING) != desired_value:
            options[option_key] = desired_value
            changed = True

    if changed:
        client.post_json("sdapi/v1/options", options, timeout=5 * 60)
    _update_forge_catalog_current(options)


def _forge_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _forge_runtime_control_catalog(
    schema: Dict[str, Any], extra_sources: Optional[Sequence[str]] = None
) -> Dict[str, Any]:
    controls = schema.get("controls") if isinstance(schema.get("controls"), list) else []
    sources = {
        str(control.get("source") or "")
        for control in controls
        if isinstance(control, dict)
        and str(control.get("source") or "") in FORGE_CATALOG_SOURCES
    }
    sources.update(
        str(source) for source in (extra_sources or [])
        if str(source) in FORGE_CATALOG_SOURCES
    )
    return forge_catalog(sorted(sources), force=False) if sources else {}


def _forge_control_choices(
    control: Dict[str, Any], runtime_catalog: Optional[Dict[str, Any]] = None
) -> List[Any]:
    runtime_catalog = runtime_catalog or {}
    source = str(control.get("source") or "")
    items = runtime_catalog.get(source) if source and isinstance(runtime_catalog.get(source), list) else control.get("items")
    if not isinstance(items, list):
        return []
    result: List[Any] = []
    for item in items:
        if isinstance(item, dict):
            if "value" in item:
                result.append(item.get("value"))
            elif "label" in item:
                result.append(item.get("label"))
        else:
            result.append(item)
    return result


def _forge_match_choice(value: Any, choices: Sequence[Any], field_name: str) -> Any:
    for choice in choices:
        if value == choice:
            return choice
    string_matches = [choice for choice in choices if str(choice) == str(value)]
    if len(string_matches) == 1:
        return string_matches[0]
    raise UserVisibleError(
        f"Invalid value {value!r} for Forge field {field_name}.",
        "forge_field_invalid_value",
        [value, field_name],
    )


def _forge_int_bound(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, float):
            if not math.isfinite(value) or not value.is_integer():
                return None
            return int(value)
        return int(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None


def _forge_float_bound(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


# Приводит значение к точному JSON-типу схемы и повторно применяет choices,
# min/max/step. Большой Seed остаётся Python int и не проходит через float.
def _forge_coerce_control_value(
    control: Dict[str, Any],
    value: Any,
    runtime_catalog: Optional[Dict[str, Any]] = None,
) -> Any:
    control_id = str(control.get("id") or control.get("payload_key") or "unnamed")
    control_type = str(control.get("type") or "string").strip().lower()

    if control_type == "dropdown":
        choices = _forge_control_choices(control, runtime_catalog)
        if not choices:
            raise UserVisibleError(
                f"Forge field {control_id} has no available values.",
                "forge_field_no_values",
                [control_id],
            )
        return _forge_match_choice(value, choices, control_id)

    if control_type == "multiselect":
        if value is None:
            values: List[Any] = []
        elif isinstance(value, (list, tuple)):
            values = list(value)
        else:
            raise UserVisibleError(
                f"Forge field {control_id} expects a list of values.",
                "forge_field_list_expected",
                [control_id],
            )
        choices = _forge_control_choices(control, runtime_catalog)
        if values and not choices:
            raise UserVisibleError(
                f"Forge field {control_id} has no available values.",
                "forge_field_no_values",
                [control_id],
            )
        result: List[Any] = []
        seen: Set[Tuple[str, str]] = set()
        for item in values:
            matched = _forge_match_choice(item, choices, control_id) if choices else item
            key = (type(matched).__name__, str(matched))
            if key not in seen:
                seen.add(key)
                result.append(matched)
        return result

    if control_type in {"checkbox", "boolean", "bool"}:
        return _forge_bool(value)

    if control_type in {"integer", "int"}:
        try:
            number = parse_user_int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise UserVisibleError(
                f"Forge field {control_id} expects an integer, received {value!r}.",
                "forge_field_integer_expected",
                [control_id, value],
            ) from exc

        minimum = _forge_int_bound(control.get("min"))
        maximum = _forge_int_bound(control.get("max"))
        if minimum is not None:
            number = max(minimum, number)
        if maximum is not None:
            number = min(maximum, number)
        step = _forge_int_bound(control.get("step"))
        if step is not None and step > 1:
            origin = minimum if minimum is not None else 0
            quotient, remainder = divmod(number - origin, step)
            if remainder * 2 >= step:
                quotient += 1
            number = origin + quotient * step
        if minimum is not None:
            number = max(minimum, number)
        if maximum is not None:
            number = min(maximum, number)
        return number

    if control_type in {"float", "number"}:
        try:
            number = parse_user_float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise UserVisibleError(
                f"Forge field {control_id} expects a number, received {value!r}.",
                "forge_field_number_expected",
                [control_id, value],
            ) from exc
        if not math.isfinite(number):
            raise UserVisibleError(
                f"Forge field {control_id} expects a finite number, received {value!r}.",
                "forge_field_finite_expected",
                [control_id, value],
            )
        minimum = _forge_float_bound(control.get("min"))
        maximum = _forge_float_bound(control.get("max"))
        number = clamp_number(number, minimum, maximum)
        step = _forge_float_bound(control.get("step"))
        if step is not None and step > 0:
            origin = minimum if minimum is not None else 0.0
            quotient = (number - origin) / step
            if math.isfinite(quotient):
                number = math.floor(quotient + 0.5) * step + origin
        number = clamp_number(number, minimum, maximum)
        if not math.isfinite(number):
            raise UserVisibleError(
                f"Forge field {control_id} produced a non-finite number.",
                "forge_field_non_finite",
                [control_id],
            )
        return float(number)

    if value is None:
        return ""
    return str(value) if control_type in {"string", "multiline", "text"} else value


def _forge_control_value(
    control: Dict[str, Any],
    values: Dict[str, Any],
    runtime_catalog: Optional[Dict[str, Any]] = None,
) -> Any:
    control_id = str(control.get("id") or "")
    raw_value = values[control_id] if control_id in values else copy.deepcopy(control.get("value"))
    return _forge_coerce_control_value(control, raw_value, runtime_catalog)


def _normalize_forge_loras(
    items: Any, available: Optional[Sequence[Any]] = None
) -> List[Dict[str, Any]]:
    """Normalize LoRA values for both schema saving and generation.

    A non-empty Forge catalog also canonicalizes names and removes entries that
    disappeared from Forge. If the catalog is empty or unavailable, names are
    preserved so Forge can report the actual model-loading error.
    """

    catalog_names = [str(item).strip() for item in (available or []) if str(item).strip()]
    catalog_by_key = {name.lower(): name for name in catalog_names}
    restrict_to_catalog = bool(catalog_by_key)
    source = items if isinstance(items, list) else []
    normalized: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in source:
        name = ""
        weight: Any = 1.0
        if isinstance(item, str):
            text = item.strip().strip("<>")
            if text.lower().startswith("lora:"):
                text = text[5:]
            if ":" in text:
                candidate_name, candidate_weight = text.rsplit(":", 1)
                name = candidate_name.strip()
                try:
                    weight = float(candidate_weight)
                except (TypeError, ValueError, OverflowError):
                    weight = 1.0
            else:
                name = text.strip()
        elif isinstance(item, dict):
            name = str(
                item.get("name") or item.get("lora") or item.get("value")
                or item.get("label") or item.get("id") or item.get("file")
                or item.get("filename") or ""
            ).strip()
            weight = item.get("weight", item.get("scale", item.get("strength", 1.0)))
        if not name:
            continue
        if restrict_to_catalog:
            name = catalog_by_key.get(name.lower(), "")
            if not name:
                continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            numeric_weight = float(weight)
        except (TypeError, ValueError, OverflowError):
            numeric_weight = 1.0
        if not math.isfinite(numeric_weight):
            numeric_weight = 1.0
        normalized.append({
            "name": name,
            "weight": max(0.0, min(1.0, round(numeric_weight, 2))),
        })
    return normalized


def _forge_cfg_disables_negative_prompt(
    controls: Sequence[Any], values: Dict[str, Any], runtime_catalog: Dict[str, Any]
) -> bool:
    """Apply the Negative prompt rule only to ordinary CFG Scale."""

    for control in controls:
        if not isinstance(control, dict):
            continue
        control_id = str(control.get("id") or "")
        semantic_id = control_id.lower()
        input_name = str(control.get("input") or "").strip().lower()
        payload_key = str(control.get("payload_key") or "").strip().lower()
        if not (
            semantic_id == "cfg" or semantic_id.startswith("cfg__")
            or input_name in {"cfg", "cfg_scale"} or payload_key == "cfg_scale"
        ):
            continue
        try:
            cfg_value = float(_forge_control_value(control, values, runtime_catalog))
        except (TypeError, ValueError, OverflowError):
            return False
        return math.isfinite(cfg_value) and cfg_value <= 1.000000001
    return False


def _forge_lora_prompt_prefix(loras: Sequence[Dict[str, Any]]) -> str:
    tags: List[str] = []
    for item in loras:
        weight = f"{float(item['weight']):.2f}".rstrip("0").rstrip(".")
        tags.append(f"<lora:{item['name']}:{weight}>")
    return " ".join(tags)


def _forge_response_image(result: Any, response_keys: List[str]) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    for key in response_keys:
        value = result.get(key)
        if isinstance(value, list) and value:
            return str(value[0] or "") or None
        if isinstance(value, str) and value:
            return value
    return None


def run_forge_generation(task: Dict[str, Any]) -> None:
    with generation_context(task, "forge") as request_id:
        _run_forge_generation(task, request_id)


FORGE_POST_THREADS_LOCK = threading.Lock()
FORGE_POST_THREADS: Dict[str, threading.Thread] = {}


def forge_post_in_progress() -> bool:
    with FORGE_POST_THREADS_LOCK:
        return any(thread.is_alive() for thread in FORGE_POST_THREADS.values())


# Собирает payload только из разрешённых полей схемы. Значения скрытых контролов
# берутся из default схемы, а значения видимых — из проверенного словаря JSX.
def _run_forge_generation(task: Dict[str, Any], request_id: str) -> None:
    message = task.get("message") or {}
    schema_id = str(message.get("schema_id") or "")
    schema = get_forge_schema(schema_id, message.get("schema_folder"))
    input_path = Path(str(message.get("input") or ""))
    output_dir = TEMP_DIR
    values = message.get("values") if isinstance(message.get("values"), dict) else {}
    selected_loras = (
        message.get("selected_loras")
        if isinstance(message.get("selected_loras"), list)
        else []
    )
    width = int(message.get("width") or 0)
    height = int(message.get("height") or 0)
    if not input_path.is_file():
        raise UserVisibleError(f"Photoshop temporary file was not found: {input_path}")
    if width <= 0 or height <= 0:
        width, height = read_image_dimensions(input_path)
    if width <= 0 or height <= 0:
        raise UserVisibleError("Could not determine width/height for Forge Neo.")

    client = current_forge_client()
    runtime_catalog = _forge_runtime_control_catalog(
        schema, ["loras"] if selected_loras else None
    )
    _apply_forge_options(client, values, schema, runtime_catalog)
    raise_if_generation_cancelled(request_id)

    generation = schema.get("generation") if isinstance(schema.get("generation"), dict) else {}
    endpoint = str(generation.get("endpoint") or "sdapi/v1/img2img").lstrip("/")
    input_mode = str(generation.get("input_mode") or "img2img").strip().lower()
    input_key = str(generation.get("input_key") or ("image" if input_mode == "single_image" else "init_images"))
    response_keys = generation.get("response_keys")
    if not isinstance(response_keys, list) or not response_keys:
        response_keys = ["images", "image"]
    response_keys = [str(item) for item in response_keys if str(item)]

    controls = schema.get("controls") if isinstance(schema.get("controls"), list) else []
    controls_by_id = {
        str(control.get("id") or ""): control
        for control in controls
        if isinstance(control, dict) and control.get("id")
    }

    require_any = generation.get("require_any")
    if isinstance(require_any, list) and require_any:
        enabled = False
        for control_id in require_any:
            control_key = str(control_id)
            definition = controls_by_id.get(control_key, {})
            current_value = (
                _forge_control_value(definition, values, runtime_catalog)
                if definition else values.get(control_key, False)
            )
            if _forge_bool(current_value):
                enabled = True
                break
        if not enabled:
            raise UserVisibleError(
                str(generation.get("require_any_error") or "Select at least one processing mode."),
                "forge_processing_mode_required",
            )

    payload: Dict[str, Any]
    if input_mode == "single_image":
        payload = {input_key: _file_data_url(input_path)}
    else:
        payload = {"width": width, "height": height, "n_iter": 1}

    allowed = {
        "prompt", "negative_prompt", "sampler_name", "scheduler", "steps",
        "cfg_scale", "distilled_cfg_scale", "denoising_strength", "seed",
        "batch_size", "batch_count",
    }
    schema_allowed = generation.get("allowed_payload_fields")
    if isinstance(schema_allowed, list):
        allowed.update(str(item) for item in schema_allowed if str(item))

    negative_prompt_omitted = _forge_cfg_disables_negative_prompt(
        controls, values, runtime_catalog
    )
    for control in controls:
        if not isinstance(control, dict):
            continue
        control_id = str(control.get("id") or "")
        payload_key = str(control.get("payload_key") or "")
        if payload_key not in allowed:
            continue
        enabled_by = str(control.get("enabled_by") or "")
        if enabled_by:
            source_definition = controls_by_id.get(enabled_by, {})
            source_value = (
                _forge_control_value(source_definition, values, runtime_catalog)
                if source_definition else values.get(enabled_by, False)
            )
            if not _forge_bool(source_value):
                if payload_key == "negative_prompt":
                    negative_prompt_omitted = True
                continue
        # Negative prompt не отправляется при обычном CFG Scale <= 1.
        if payload_key == "negative_prompt" and negative_prompt_omitted:
            negative_prompt_omitted = True
            continue
        # Значение из JSX есть у видимого поля; для скрытого поля применяется
        # проверенное значение по умолчанию непосредственно из JSON-схемы.
        payload[payload_key] = _forge_control_value(control, values, runtime_catalog)

    fixed_values = schema.get("fixed_values") if isinstance(schema.get("fixed_values"), dict) else {}
    for key, value in fixed_values.items():
        if key in allowed:
            payload[key] = value
    if negative_prompt_omitted:
        payload.pop("negative_prompt", None)

    if selected_loras:
        normalized_loras = _normalize_forge_loras(
            selected_loras,
            runtime_catalog.get("loras") if isinstance(runtime_catalog.get("loras"), list) else None,
        )
        prefix = _forge_lora_prompt_prefix(normalized_loras)
        if prefix:
            prompt = str(payload.get("prompt") or "")
            payload["prompt"] = prefix + (" " + prompt if prompt else "")

    capabilities = schema.get("capabilities") if isinstance(schema.get("capabilities"), dict) else {}
    stitch_requested = (
        _forge_bool(values.get("image_stitch", schema.get("image_stitch_default", False)))
        and _forge_bool(capabilities.get("image_stitch"))
    )

    if input_mode != "single_image":
        payload.setdefault("prompt", "")
        if not negative_prompt_omitted:
            payload.setdefault("negative_prompt", "")
        payload.setdefault("sampler_name", "Euler a")
        payload.setdefault("scheduler", "Automatic")
        payload.setdefault("steps", 20)
        payload.setdefault("cfg_scale", 6)
        payload.setdefault("seed", -1)
        # В img2img выделение Photoshop остаётся основным init_image.
        # В txt2img оно задаёт только размер и область размещения результата.
        if input_mode != "txt2img":
            payload["init_images"] = [_file_data_url(input_path)]

    if stitch_requested:
        maximum = _forge_image_stitch_limit(capabilities)
        image_inputs = message.get("image_inputs") if isinstance(message.get("image_inputs"), list) else []
        encoded: List[str] = []
        seen: Set[str] = set()
        for raw in image_inputs:
            if len(encoded) >= maximum:
                break
            raw_path = str(raw or "").strip()
            if not raw_path:
                continue
            path = Path(raw_path)
            if not _is_supported_forge_reference(path):
                LOGGER.warning("Unsupported ImageStitch reference was ignored: %s", path)
                continue
            if not path.is_file():
                LOGGER.warning("Missing ImageStitch reference was ignored: %s", path)
                continue
            normalized = os.path.normcase(str(path.resolve()))
            if normalized in seen:
                continue
            seen.add(normalized)
            encoded.append(_forge_reference_data_url(path))

        # Пустые или полностью устаревшие списки не должны блокировать обычную
        # генерацию: в таком случае ImageStitch просто не активируется.
        if encoded:
            if input_mode == "single_image":
                raise UserVisibleError(
                    "ImageStitch cannot be used with this Forge schema.",
                    "forge_image_stitch_unsupported",
                )
            # Не заменяем alwayson_scripts целиком: схема может уже включать
            # другие Forge extensions, которые должны работать вместе с ImageStitch.
            alwayson_scripts = payload.get("alwayson_scripts")
            if not isinstance(alwayson_scripts, dict):
                alwayson_scripts = {}
            else:
                alwayson_scripts = copy.deepcopy(alwayson_scripts)
            # Forge Neo currently exposes three ImageStitch arguments:
            # enable, reference gallery and maximum side length. Passing all
            # three explicitly avoids dependence on Gradio/API default values.
            alwayson_scripts["ImageStitch Integrated"] = {
                "args": [True, encoded, 1024]
            }
            payload["alwayson_scripts"] = alwayson_scripts
        else:
            LOGGER.info("ImageStitch was requested without usable reference files; continuing without it.")

    # POST идёт в потоке, пока worker ждёт sampling_step через /progress.
    post_done = threading.Event()
    post_result: Dict[str, Any] = {"value": None, "error": None}

    def forge_post_worker() -> None:
        try:
            post_result["value"] = client.post_json(
                endpoint,
                payload,
                timeout=int(message.get("timeout") or RUNTIME.generation_timeout),
            )
        except Exception as exc:  # исключение повторно поднимет основной worker
            post_result["error"] = exc
        finally:
            post_done.set()
            with FORGE_POST_THREADS_LOCK:
                FORGE_POST_THREADS.pop(request_id, None)

    post_thread = threading.Thread(
        target=forge_post_worker,
        name=f"ForgeGeneration-{request_id[:8]}",
        daemon=True,
    )
    with FORGE_POST_THREADS_LOCK:
        FORGE_POST_THREADS[request_id] = post_thread
    try:
        post_thread.start()
    except Exception:
        with FORGE_POST_THREADS_LOCK:
            FORGE_POST_THREADS.pop(request_id, None)
        raise

    progress_mode = str(generation.get("progress_mode") or "sampling").strip().lower()
    progress_stage_started = False
    if progress_mode == "request_sent":
        # Endpoints such as extra-single-image do not publish sampling_step.
        # Their second progress segment starts as soon as the request is running.
        notify_generation_progress_ready(request_id, "forge")
        progress_stage_started = True

    next_progress_poll = 0.0
    while not post_done.is_set() and not progress_stage_started:
        touch_activity()
        raise_if_generation_cancelled(request_id)
        now = time.monotonic()
        if now >= next_progress_poll:
            next_progress_poll = now + 0.3
            try:
                if forge_sampling_has_started(client):
                    notify_generation_progress_ready(request_id, "forge")
                    progress_stage_started = True
                    break
            except UserVisibleError as exc:
                # Ошибка /progress не отменяет выполняющийся POST.
                LOGGER.debug("Forge progress polling failed: %s", exc)
        post_done.wait(timeout=0.05)

    if progress_stage_started:
        while not post_done.wait(timeout=0.1):
            touch_activity()
            raise_if_generation_cancelled(request_id)
    else:
        # При быстром ответе переключаем сегмент после POST.
        if post_result.get("error") is not None:
            raise post_result["error"]
        raise_if_generation_cancelled(request_id)
        notify_generation_progress_ready(request_id, "forge")

    if post_result.get("error") is not None:
        raise post_result["error"]
    result = post_result.get("value")
    raise_if_generation_cancelled(request_id)
    encoded_image = _forge_response_image(result, response_keys)
    if not encoded_image:
        raise UserVisibleError("Forge Neo did not return an image.")
    destination = _decode_forge_image(
        encoded_image,
        output_dir / f"{now_timestamp()}-{safe_filename(schema.get('label') or schema_id)}",
    )
    generated_seeds: Dict[str, Any] = {}
    info = result.get("info")
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except Exception:
            info = {}
    if isinstance(info, dict) and info.get("seed") is not None:
        generated_seeds["seed"] = info.get("seed")
    answer({
        "path": str(destination),
        "generated_seeds": generated_seeds,
    }, request_id=request_id)


@dataclass
# СОСТОЯНИЕ ПРОЦЕССА И ФОНОВЫЕ WORKERS
class RuntimeConfig:
    backend_host: str = DEFAULT_COMFY_HOST
    comfy_port: int = 8188
    forge_port: int = 7860
    comfy_input_folder: Optional[Path] = None
    comfy_output_folder: Optional[Path] = None
    workflows_folder: Path = Path.home() / "Documents" / "Comfy Workflows"
    generation_timeout: int = 20 * 60
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    backend_monitor_interval_seconds: int = DEFAULT_BACKEND_MONITOR_INTERVAL_SECONDS


@dataclass
class GenerationState:
    request_id: Optional[str] = None
    queued_request_id: Optional[str] = None
    backend: str = "comfy"
    prompt_id: Optional[str] = None
    input_folder: Optional[Path] = None
    output_folder: Optional[Path] = None
    uploaded_images: List[Dict[str, Any]] = field(default_factory=list)
    progress_watcher: Optional[ComfyProgressWatcher] = None
    preserved_output_path: Optional[Path] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    # ACK закрывает разрыв между двумя listener-стадиями JSX.
    ack_event: threading.Event = field(default_factory=threading.Event)
    active: bool = False
    queued: bool = False


RUNTIME = RuntimeConfig()
GENERATION = GenerationState()
LAST_ACTIVITY = time.monotonic()
LAST_ACTIVITY_LOCK = threading.Lock()

# Фоновый worker хранит последний статус обеих оболочек.
BACKEND_STATUS_LOCK = threading.Lock()
BACKEND_PROBE_LOCK = threading.Lock()
BACKEND_STATUS_CACHE: Optional[Dict[str, Any]] = None
BACKEND_STATUS_ENDPOINTS: Optional[Tuple[str, int, int]] = None
BACKEND_TEST_RESULTS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
BACKEND_MONITOR_WAKE = threading.Event()
BACKEND_MONITOR_START_LOCK = threading.Lock()
BACKEND_MONITOR_STARTED = False
# Локальные папки Comfy определяются один раз для каждого endpoint.
COMFY_INPUT_FOLDER_ENDPOINT: Optional[Tuple[str, int]] = None


@contextmanager
def generation_context(task: Dict[str, Any], backend: str):
    """Общий жизненный цикл одной задачи ComfyUI или Forge.

    Контекст инициализирует разделяемое состояние отмены/ACK до любой работы
    backend, а затем гарантированно очищает загруженные Comfy-файлы, отменённый
    request_id и все поля GENERATION. Backend-функции содержат только свою
    подготовку payload, ожидание прогресса и обработку результата.
    """

    request_id = str(task.get("request_id") or uuid.uuid4())
    task["request_id"] = request_id
    GENERATION.request_id = request_id
    GENERATION.queued_request_id = None
    GENERATION.prompt_id = None
    GENERATION.backend = backend
    GENERATION.input_folder = None
    GENERATION.output_folder = None
    GENERATION.uploaded_images = []
    GENERATION.progress_watcher = None
    GENERATION.preserved_output_path = None
    GENERATION.cancel_event.clear()
    GENERATION.ack_event.clear()
    GENERATION.active = True
    GENERATION.queued = False
    try:
        raise_if_generation_cancelled(request_id)
        yield request_id
    finally:
        try:
            if GENERATION.progress_watcher is not None:
                GENERATION.progress_watcher.close()
            cleanup_comfy_request_outputs(
                GENERATION.output_folder,
                request_id,
                preserve_path=GENERATION.preserved_output_path,
            )
            cleanup_uploaded_images(GENERATION.input_folder, GENERATION.uploaded_images)
        finally:
            with CANCELLED_REQUESTS_LOCK:
                CANCELLED_REQUESTS.discard(request_id)
            GENERATION.request_id = None
            GENERATION.queued_request_id = None
            GENERATION.prompt_id = None
            GENERATION.backend = "comfy"
            GENERATION.input_folder = None
            GENERATION.output_folder = None
            GENERATION.uploaded_images = []
            GENERATION.progress_watcher = None
            GENERATION.preserved_output_path = None
            GENERATION.active = False
            GENERATION.queued = False
            GENERATION.cancel_event.clear()
            GENERATION.ack_event.clear()
            touch_activity()


def touch_activity() -> None:
    global LAST_ACTIVITY
    with LAST_ACTIVITY_LOCK:
        LAST_ACTIVITY = time.monotonic()


REPLY_LOCK = threading.Lock()


# Ответы отправляются на отдельный listener JSX. ASCII-only JSON нужен из-за
# ограничений ExtendScript Socket/eval при Unicode control characters.
def send_data_to_jsx(message: Dict[str, Any], retries: int = 20) -> bool:
    """Отправляет один ASCII-only JSON-ответ локальному JSX listener."""

    try:
        payload = (api_json_dumps(message) + "\n").encode("ascii")
    except Exception:
        log_exception("Could not serialize the JSX response")
        return False

    LOGGER.info(
        "JSX response: type=%s request=%s bytes=%s transport=socket ascii=true",
        message.get("type"),
        message.get("request_id"),
        len(payload),
    )

    with REPLY_LOCK:
        for attempt in range(retries):
            try:
                with socket.create_connection(
                    (API_HOST, API_REPLY_PORT), timeout=2.0
                ) as sock:
                    sock.settimeout(10.0)
                    sock.sendall(payload)
                return True
            except OSError as exc:
                if attempt + 1 < retries:
                    time.sleep(0.05)
                else:
                    LOGGER.error(
                        "Could not send JSX response: type=%s request=%s error=%s",
                        message.get("type"),
                        message.get("request_id"),
                        exc,
                    )
    return False


def notify_generation_progress_ready(
    request_id: str,
    backend: str,
    prompt_id: str = "",
) -> None:
    """Переключает Photoshop с подготовительного progress-сегмента на основной.

    Python посылает ``message=init`` только когда backend действительно начал
    sampling/execution. Если задача завершилась слишком быстро и это состояние
    не удалось поймать polling-ом, вызов выполняется сразу после завершения, но
    до отправки итогового пути. На втором этапе JSX сначала открывает новый
    listener и только затем отправляет ACK, поэтому готовый результат не может
    потеряться в промежутке между двумя progress-сегментами.
    """

    raise_if_generation_cancelled(request_id)
    GENERATION.ack_event.clear()
    payload: Dict[str, Any] = {
        "protocol": API_PROTOCOL,
        "request_id": request_id,
        "type": "answer",
        "message": "init",
        "backend": backend,
    }
    if prompt_id:
        payload["prompt_id"] = prompt_id

    LOGGER.info(
        "Progress stage ready: backend=%s request=%s prompt=%s",
        backend,
        request_id,
        prompt_id or "-",
    )
    if not send_data_to_jsx(payload):
        raise UserVisibleError(
            "Could not switch Photoshop to the generation stage: "
            "the progress listener is unavailable."
        )

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if GENERATION.ack_event.wait(timeout=0.1):
            LOGGER.info("Progress ACK received: request=%s", request_id)
            return
        raise_if_generation_cancelled(request_id)

    # Потерянный ACK не должен навсегда блокировать генерацию. Обычно ACK
    # приходит уже после открытия listener второго сегмента; retries остаются
    # дополнительной страховкой от задержек локального socket-соединения.
    LOGGER.warning("Progress ACK timeout: request=%s; continuing", request_id)


def comfy_queue_contains_prompt(queue_state: Any, key: str, prompt_id: str) -> bool:
    if not isinstance(queue_state, dict):
        return False
    items = queue_state.get(key)
    if not isinstance(items, list):
        return False
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) > 1 and str(item[1]) == prompt_id:
            return True
    return False


def forge_sampling_has_started(client: ForgeClient) -> bool:
    progress = client.get_json(
        "sdapi/v1/progress?skip_current_image=true", timeout=5
    )
    if not isinstance(progress, dict):
        return False
    state = progress.get("state")
    if not isinstance(state, dict):
        state = {}
    try:
        return int(state.get("sampling_step") or 0) > 0
    except (TypeError, ValueError):
        return False


def answer(message: Any, request_id: Optional[str] = None) -> None:
    send_data_to_jsx(
        {
            "protocol": API_PROTOCOL,
            "request_id": request_id,
            "type": "answer",
            "message": message,
        }
    )


def error_answer(message: Any, request_id: Optional[str] = None) -> None:
    payload: Dict[str, Any] = {
        "protocol": API_PROTOCOL,
        "request_id": request_id,
        "type": "error",
        "message": str(message or ""),
    }
    if isinstance(message, UserVisibleError):
        if message.code:
            payload["code"] = message.code
        if message.params:
            payload["params"] = message.params
    send_data_to_jsx(payload)


def cancelled_answer(request_id: Optional[str] = None) -> None:
    send_data_to_jsx(
        {
            "protocol": API_PROTOCOL,
            "request_id": request_id,
            "type": "cancelled",
            "message": "",
        }
    )


OBJECT_INFO_LOCK = threading.Lock()
OBJECT_INFO_CACHE: Dict[str, Any] = {"value": None, "server": None}
SCHEMA_CACHE = SchemaCache()
WORKFLOW_RUNTIME_CACHE = WorkflowRuntimeCache()


def invalidate_workflow_cache(workflow_id: str) -> None:
    SCHEMA_CACHE.invalidate(workflow_id)
    WORKFLOW_RUNTIME_CACHE.invalidate(workflow_id)


def current_client() -> ComfyClient:
    return ComfyClient(RUNTIME.backend_host, RUNTIME.comfy_port)


def get_object_info(force: bool = False) -> Dict[str, Any]:
    server_key = f"{RUNTIME.backend_host}:{RUNTIME.comfy_port}"
    with OBJECT_INFO_LOCK:
        cached = OBJECT_INFO_CACHE.get("value")
        if cached is not None and OBJECT_INFO_CACHE.get("server") == server_key and not force:
            LOGGER.info("/object_info: cache used for %s", server_key)
            return cached
        started = time.monotonic()
        LOGGER.info("/object_info: request to %s", server_key)
        value = current_client().get_object_info()
        OBJECT_INFO_CACHE.update({"value": value, "server": server_key})
        LOGGER.info(
            "/object_info: received %s classes in %.2f s",
            len(value),
            time.monotonic() - started,
        )
        return value


# Нормализует ручные привязки от JSX. Отсутствие ключа означает automatic,
# тогда как source_image и binding являются осознанными пользовательскими режимами.
def normalize_binding_overrides(value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Удаляет пустые значения, которыми JSX обозначает автоматический выбор."""

    if not isinstance(value, dict):
        return None
    result: Dict[str, Any] = {}
    for key in ("input", "mask", "output"):
        item = value.get(key)
        if item not in (None, ""):
            result[key] = item
    size_mode = str(value.get("sizeMode") or value.get("size_mode") or "auto").lower()
    if size_mode not in {"auto", "source_image", "binding"}:
        size_mode = "auto"
    if size_mode != "auto":
        result["size_mode"] = size_mode
    if size_mode == "binding":
        size_id = value.get("size")
        if size_id not in (None, ""):
            result["size"] = size_id
    references = value.get("references")
    references_configured = (
        value.get("referencesConfigured") is True
        or value.get("references_configured") is True
    )
    normalized_references = (
        [str(item) for item in references if item not in (None, "")]
        if isinstance(references, list)
        else []
    )
    if normalized_references or references_configured:
        result["references"] = normalized_references
    if references_configured:
        # Distinguish the first automatic #PS-REF setup from an explicit user
        # choice to assign no LoadImage nodes to the Reference role.
        result["references_configured"] = True
    empty_inputs = value.get("emptyInputs")
    if not isinstance(empty_inputs, list):
        empty_inputs = value.get("empty_inputs")
    if isinstance(empty_inputs, list):
        # Preserve an explicit empty list: it means that every unassigned
        # LoadImage should keep the file stored in the workflow.
        result["empty_inputs"] = [str(item) for item in empty_inputs if item not in (None, "")]
    return result or None


def analyze_workflow(
    workflow_id: str,
    overrides: Optional[Dict[str, Any]] = None,
    force: bool = False,
    relative_path: str = "",
) -> Dict[str, Any]:
    started = time.monotonic()
    LOGGER.info(
        "Workflow analysis started: id=%s force=%s relative_path=%s",
        workflow_id,
        force,
        relative_path,
    )
    repository = WorkflowRepository(RUNTIME.workflows_folder)
    workflow_file = repository.get(workflow_id, relative_path=relative_path)
    overrides = normalize_binding_overrides(overrides)

    analysis = None
    validation_schema = None
    workflow_data: Optional[Dict[str, Any]] = None

    if force:
        WORKFLOW_RUNTIME_CACHE.invalidate(workflow_file.workflow_id)
    else:
        runtime_bundle = WORKFLOW_RUNTIME_CACHE.get_analysis(workflow_file, overrides)
        if runtime_bundle is not None:
            analysis, validation_schema = runtime_bundle
            LOGGER.info("Workflow analysis: process cache used")

    if analysis is None and not force:
        cached_bundle = SCHEMA_CACHE.load_fast_bundle(workflow_file, overrides)
        if cached_bundle is not None:
            analysis, validation_schema = cached_bundle
            LOGGER.info("Workflow analysis: disk cache used")
            WORKFLOW_RUNTIME_CACHE.put_analysis(
                workflow_file, overrides, analysis, validation_schema
            )

    if analysis is None:
        object_info = get_object_info(force=force)
        workflow_data = WORKFLOW_RUNTIME_CACHE.load_json(workflow_file, repository)
        LOGGER.info("Workflow analysis: JSON contains %s nodes", len(workflow_data))
        analysis = WorkflowAnalyzer(workflow_data, object_info).analyze(overrides)
        validation_schema = build_validation_schema(workflow_data, object_info)
        WORKFLOW_RUNTIME_CACHE.put_analysis(
            workflow_file, overrides, analysis, validation_schema
        )
        SCHEMA_CACHE.save(
            workflow_file, analysis, validation_schema, overrides
        )
    result = dict(analysis)
    result.update(
        {
            "workflow_id": workflow_file.workflow_id,
            "workflow_name": workflow_file.name,
            "relative_path": workflow_file.relative_path,
        }
    )
    LOGGER.info(
        "Workflow analysis completed in %.2f s: controls=%s valid=%s",
        time.monotonic() - started,
        len(result.get("controls", [])),
        result.get("valid"),
    )
    return result


def save_workflow_values(
    workflow_id: str,
    *,
    relative_path: str = "",
    overrides: Optional[Dict[str, Any]] = None,
    values: Optional[Dict[str, Any]] = None,
    destination_path: str = "",
) -> Dict[str, Any]:
    """Save visible UI values to the destination selected by Photoshop."""

    values = values if isinstance(values, dict) else {}
    if not values:
        raise UserVisibleError(
            "There are no visible workflow values to save.",
            "workflow_save_no_values",
        )

    repository = WorkflowRepository(RUNTIME.workflows_folder)
    workflow_file = repository.get(workflow_id, relative_path=relative_path)
    workflow_data = WORKFLOW_RUNTIME_CACHE.load_json(workflow_file, repository)
    normalized_overrides = normalize_binding_overrides(overrides)
    cached_bundle = WORKFLOW_RUNTIME_CACHE.get_analysis(
        workflow_file, normalized_overrides
    )
    if cached_bundle is not None:
        analysis, object_info = cached_bundle
    else:
        disk_bundle = SCHEMA_CACHE.load_fast_bundle(
            workflow_file, normalized_overrides
        )
        if disk_bundle is not None and disk_bundle[1] is not None:
            analysis, object_info = disk_bundle
        else:
            full_object_info = get_object_info(force=False)
            analysis = WorkflowAnalyzer(workflow_data, full_object_info).analyze(
                normalized_overrides
            )
            object_info = build_validation_schema(workflow_data, full_object_info)
            SCHEMA_CACHE.save(
                workflow_file, analysis, object_info, normalized_overrides
            )
        WORKFLOW_RUNTIME_CACHE.put_analysis(
            workflow_file, normalized_overrides, analysis, object_info
        )
    if not analysis.get("valid"):
        raise UserVisibleError(
            "The workflow cannot be saved because its current bindings are invalid.",
            "workflow_save_invalid_bindings",
        )

    controls = analysis.get("controls") if isinstance(analysis.get("controls"), list) else []
    controls_by_id = {
        str(control.get("id") or ""): control
        for control in controls
        if isinstance(control, dict) and control.get("id")
    }
    patcher = WorkflowPatcher(workflow_data, object_info)
    for raw_id, value in values.items():
        control_id = str(raw_id or "")
        control = controls_by_id.get(control_id)
        if not control:
            raise UserVisibleError(
                f"Could not save workflow field {control_id!r}: it is missing from the current analysis. "
                "Reanalyze the workflow and try again.",
                "workflow_save_field_missing",
                [control_id],
            )
        targets = control.get("targets") if isinstance(control.get("targets"), list) else []
        if not targets:
            raise UserVisibleError(
                f"Could not save workflow field {control_id!r}: it has no target inputs. "
                "Reanalyze the workflow and check its bindings.",
                "workflow_save_field_no_targets",
                [control_id],
            )
        for target in targets:
            patcher.set_target(target, value)

    raw_destination = str(destination_path or "").strip()
    if not raw_destination:
        raise UserVisibleError(
            "No destination file was selected for Save As. The source workflow was not changed.",
            "save_destination_missing",
        )
    destination = Path(raw_destination).expanduser()
    if destination.suffix.lower() != ".json":
        raise UserVisibleError(
            f"The workflow must be saved as a .json file:\n{destination}",
            "workflow_save_json_required",
            [destination],
        )
    write_json_atomic(
        destination, patcher.workflow, "workflow JSON", "workflow_save_write_failed"
    )
    try:
        saved_relative = destination.resolve().relative_to(repository.folder.resolve()).as_posix()
        invalidate_workflow_cache(stable_workflow_id(saved_relative))
    except (OSError, ValueError):
        pass
    if destination.resolve() == workflow_file.absolute_path.resolve():
        invalidate_workflow_cache(workflow_file.workflow_id)
    return {"path": str(destination)}


def save_forge_schema_values(
    schema_id: str,
    *,
    schema_folder: Any = "",
    values: Optional[Dict[str, Any]] = None,
    destination_path: str = "",
    selected_loras: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Save visible UI values to the destination selected by Photoshop."""

    values = values if isinstance(values, dict) else {}
    if not values:
        raise UserVisibleError(
            "There are no visible Forge schema values to save.",
            "forge_save_no_values",
        )

    items, schema_dir, _ = list_forge_schemas(schema_folder)
    item = next((entry for entry in items if str(entry.get("id") or "") == str(schema_id or "")), None)
    if not item:
        raise UserVisibleError(
            f"Forge UI preset was not found: {schema_id}",
            "forge_schema_missing",
            [schema_id],
        )
    path = schema_dir / str(item.get("file") or "")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise UserVisibleError(f"Could not read Forge schema: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UserVisibleError(
            f"Invalid Forge schema JSON {path.name}: {exc}",
            "forge_schema_json_invalid",
            [path.name, exc.lineno, exc.colno, exc.msg],
        ) from exc
    if not isinstance(raw, dict):
        raise UserVisibleError(
            f"Forge schema {path.name} must be a JSON object.",
            "forge_schema_root_invalid",
            [path.name],
        )

    raw_selected_loras = selected_loras if isinstance(selected_loras, list) else []

    raw_controls = raw.get("controls") if isinstance(raw.get("controls"), list) else []
    effective_schema = get_forge_schema(schema_id, schema_folder)
    effective_controls = effective_schema.get("controls") if isinstance(effective_schema.get("controls"), list) else []
    effective_by_id = {
        str(control.get("id") or ""): control
        for control in effective_controls
        if isinstance(control, dict) and control.get("id")
    }

    # If a requested visible control is inherited, materialize the complete
    # effective controls list in the copy. This preserves ``extends`` for other
    # fields while allowing every visible value to be stored locally.
    directly_defined = {
        str(control.get("id") or "")
        for control in raw_controls
        if isinstance(control, dict) and control.get("id")
    }
    requested_ids = {str(raw_id or "") for raw_id in values if str(raw_id or "") != "image_stitch"}
    controls = raw_controls
    if any(control_id not in directly_defined for control_id in requested_ids):
        controls = copy.deepcopy(effective_controls)
        raw["controls"] = controls

    controls_by_id = {
        str(control.get("id") or ""): control
        for control in controls
        if isinstance(control, dict) and control.get("id")
    }
    runtime_catalog = _forge_runtime_control_catalog(effective_schema)
    for raw_id, value in values.items():
        control_id = str(raw_id or "")
        if control_id == "image_stitch":
            capabilities = (
                effective_schema.get("capabilities")
                if isinstance(effective_schema.get("capabilities"), dict)
                else {}
            )
            if not _forge_bool(capabilities.get("image_stitch")):
                raise UserVisibleError(
                    "The selected Forge schema does not support ImageStitch.",
                    "forge_image_stitch_unsupported",
                )
            raw["image_stitch_default"] = _forge_bool(value)
            continue
        control = controls_by_id.get(control_id)
        if not control:
            raise UserVisibleError(
                f"Could not save Forge field {control_id!r}: it is absent from the selected schema.",
                "forge_save_field_missing",
                [control_id],
            )
        effective_control = effective_by_id.get(control_id, control)
        control["value"] = _forge_coerce_control_value(
            effective_control, value, runtime_catalog
        )

    raw_destination = str(destination_path or "").strip()
    if not raw_destination:
        raise UserVisibleError(
            "No destination file was selected for Save As. The source Forge schema was not changed.",
            "save_destination_missing",
        )
    destination = Path(raw_destination).expanduser()
    if destination.suffix.lower() != ".json":
        raise UserVisibleError(
            f"The Forge schema must be saved as a .json file:\n{destination}",
            "forge_save_json_required",
            [destination],
        )

    # Save As создаёт самостоятельную копию схемы. Для новой копии label
    # должен соответствовать имени, которое пользователь ввёл в стандартном
    # диалоге сохранения. Технический внутренний id намеренно не меняем.
    # При явной перезаписи исходного файла сохраняем его существующий label.
    try:
        is_source = destination.resolve() == path.resolve()
    except OSError:
        is_source = os.path.normcase(os.path.abspath(str(destination))) == os.path.normcase(os.path.abspath(str(path)))
    if not is_source:
        raw["label"] = destination.stem

    normalized_loras = _normalize_forge_loras(raw_selected_loras)
    if normalized_loras:
        raw["loras"] = normalized_loras
    else:
        raw.pop("loras", None)

    write_json_atomic(
        destination, raw, "Forge schema JSON", "forge_save_write_failed"
    )
    return {"path": str(destination)}


GENERATION_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue()
GENERATION_SUBMIT_LOCK = threading.Lock()
CANCELLED_REQUESTS: set[str] = set()
CANCELLED_REQUESTS_LOCK = threading.Lock()
WORKER_STOP = threading.Event()


def extract_history_entry(history: Dict[str, Any], prompt_id: str) -> Optional[Dict[str, Any]]:
    if prompt_id in history and isinstance(history[prompt_id], dict):
        return history[prompt_id]
    # Некоторые обёртки могут вернуть сам entry без внешнего prompt_id.
    if "outputs" in history or "status" in history:
        return history
    return None


def history_status(entry: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    status = entry.get("status")
    if isinstance(status, dict):
        status_str = str(status.get("status_str", "")).lower()
        completed = bool(status.get("completed"))
        messages = status.get("messages")
        if status_str in {"error", "failed"}:
            return True, f"ComfyUI completed the task with an error: {messages}"
        if completed or status_str in {"success", "completed"}:
            return True, None
    # Наличие outputs обычно означает, что история уже сформирована.
    if isinstance(entry.get("outputs"), dict) and entry.get("outputs"):
        return True, None
    return False, None


def select_output_image(
    history_entry: Dict[str, Any],
    output_binding: Dict[str, Any],
) -> Dict[str, Any]:
    outputs = history_entry.get("outputs", {})
    node_id = str(output_binding.get("node_id", ""))
    node_output = outputs.get(node_id)
    if not isinstance(node_output, dict):
        raise UserVisibleError(
            f"The workflow completed, but the selected Output image node #{node_id} is missing from history.",
            "output_image_missing_from_history",
            [node_id],
        )

    images = node_output.get("images")
    if not isinstance(images, list) or not images:
        # Некоторые custom nodes используют другие ключи. Ищем первый список
        # объектов с filename, но не прыгаем на другие output-ноды.
        for value in node_output.values():
            if isinstance(value, list) and value and isinstance(value[0], dict) and value[0].get("filename"):
                images = value
                break
    if not isinstance(images, list) or not images:
        raise UserVisibleError(
            f"The selected Output image node #{node_id} did not return an image. "
            "Select Save Image or Preview Image tagged #PS-OUTPUT.",
            "output_image_not_returned",
            [node_id],
        )
    image = images[0]
    if not isinstance(image, dict) or not image.get("filename"):
        raise UserVisibleError("The ComfyUI result metadata does not contain filename.")
    return image


def mark_request_cancelled(request_id: Optional[str]) -> str:
    requested = str(request_id or "")
    current = str(GENERATION.request_id or GENERATION.queued_request_id or "")
    # Ignore a delayed interrupt after its task has completed, and never retain
    # an ID belonging to a different task. This keeps the cancellation set
    # bounded even when JSX closes its listener before the interrupt arrives.
    if requested and (not current or requested != current):
        return ""
    normalized = requested or current
    if not normalized:
        return ""
    with CANCELLED_REQUESTS_LOCK:
        CANCELLED_REQUESTS.add(normalized)
    return normalized


def request_is_cancelled(request_id: str) -> bool:
    if GENERATION.cancel_event.is_set() and (
        not GENERATION.request_id or GENERATION.request_id == request_id
    ):
        return True
    with CANCELLED_REQUESTS_LOCK:
        return request_id in CANCELLED_REQUESTS


def raise_if_generation_cancelled(request_id: str) -> None:
    if request_is_cancelled(request_id):
        raise CancelledError("Generation was cancelled.")


def cancel_current_generation(request_id: Optional[str] = None) -> None:
    normalized = mark_request_cancelled(request_id)
    if not normalized:
        return

    GENERATION.cancel_event.set()
    prompt_id = GENERATION.prompt_id
    if GENERATION.backend == "forge":
        try:
            current_forge_client().interrupt()
        except Exception:
            LOGGER.warning("Forge Neo interrupt error")
        return
    if not prompt_id:
        # Отмена могла прийти во время анализа workflow или загрузки файлов,
        # до POST /prompt. Worker увидит CANCELLED_REQUESTS на следующей точке.
        return

    client = current_client()

    # Не посылаем глобальный interrupt вслепую: в общей очереди ComfyUI перед
    # нашим prompt может выполняться чужая задача. Сначала определяем состояние
    # конкретного prompt, затем удаляем pending или прерываем running.
    queue_known = False
    is_running = False
    is_pending = False
    try:
        queue_state = client.get_queue()
        running_items = queue_state.get("queue_running", [])
        pending_items = queue_state.get("queue_pending", [])
        is_running = any(
            isinstance(item, (list, tuple)) and len(item) > 1 and str(item[1]) == prompt_id
            for item in running_items
        )
        is_pending = any(
            isinstance(item, (list, tuple)) and len(item) > 1 and str(item[1]) == prompt_id
            for item in pending_items
        )
        queue_known = True
    except Exception:
        LOGGER.warning("Could not read the ComfyUI queue before cancellation")

    if is_pending or not queue_known:
        try:
            client.delete_queued_prompt(prompt_id)
        except Exception:
            LOGGER.warning("Could not remove the prompt from the ComfyUI queue")

    if is_running or not queue_known:
        try:
            client.interrupt(prompt_id)
        except Exception:
            log_exception("ComfyUI interrupt error")


def read_image_dimensions(path: Path) -> Tuple[int, int]:
    """Читает размеры JPEG или PNG без Pillow.

    Обычно width/height передаёт JSX. Парсер нужен как резерв для других
    клиентов и для source-image-size workflow.
    """

    try:
        with path.open("rb") as stream:
            signature = stream.read(24)
            if len(signature) >= 24 and signature[:8] == b"\x89PNG\r\n\x1a\n" and signature[12:16] == b"IHDR":
                width, height = struct.unpack(">II", signature[16:24])
                return int(width), int(height)

            if signature[:2] != b"\xff\xd8":
                return 0, 0
            stream.seek(2)
            sof_markers = {
                0xC0, 0xC1, 0xC2, 0xC3,
                0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB,
                0xCD, 0xCE, 0xCF,
            }
            while True:
                byte = stream.read(1)
                if not byte:
                    break
                if byte != b"\xff":
                    continue
                marker_byte = stream.read(1)
                while marker_byte == b"\xff":
                    marker_byte = stream.read(1)
                if not marker_byte:
                    break
                marker = marker_byte[0]
                if marker in {0xD8, 0xD9}:
                    continue
                length_raw = stream.read(2)
                if len(length_raw) != 2:
                    break
                segment_length = struct.unpack(">H", length_raw)[0]
                if segment_length < 2:
                    break
                if marker in sof_markers:
                    data = stream.read(5)
                    if len(data) != 5:
                        break
                    height, width = struct.unpack(">HH", data[1:5])
                    return int(width), int(height)
                stream.seek(segment_length - 2, 1)
    except OSError:
        pass
    return 0, 0

def run_generation(task: Dict[str, Any]) -> None:
    with generation_context(task, "comfy") as request_id:
        _run_comfy_generation(task, request_id)


_COMFY_FOLDER_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def verify_local_comfy_input_folder(
    client: ComfyClient, input_folder: Optional[Path]
) -> bool:
    """Confirm that the active ComfyUI serves files from the detected folder."""

    root = _existing_directory(input_folder)
    if not root:
        return False
    base = (root / UPLOAD_SUBFOLDER).resolve()
    probe = base / f".probe_{uuid.uuid4().hex}.png"
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(_COMFY_FOLDER_PROBE_PNG)
        raw = client._request(
            "GET",
            "/view?" + urllib.parse.urlencode({
                "filename": probe.name,
                "subfolder": UPLOAD_SUBFOLDER,
                "type": "input",
            }),
            timeout=5,
        )
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    except (OSError, UserVisibleError):
        return False
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def invalidate_detected_comfy_input_folder() -> None:
    global COMFY_INPUT_FOLDER_ENDPOINT
    RUNTIME.comfy_input_folder = None
    COMFY_INPUT_FOLDER_ENDPOINT = None


def stage_comfy_image(
    client: ComfyClient,
    source: Path,
    remote_name: str,
    input_folder: Optional[Path],
    direct_verified: bool,
) -> Dict[str, Any]:
    """Use a local Comfy input file/copy, with multipart upload as fallback."""

    root = _existing_directory(input_folder) if direct_verified else None
    if root:
        base = (root / UPLOAD_SUBFOLDER).resolve()
        temp_target: Optional[Path] = None
        try:
            base.mkdir(parents=True, exist_ok=True)
            resolved_source = source.resolve()
            if resolved_source.parent == base:
                return {
                    "name": resolved_source.name,
                    "subfolder": UPLOAD_SUBFOLDER,
                    "type": "input",
                }
            target = (base / safe_filename(remote_name)).resolve()
            if target.parent != base:
                raise OSError("Unsafe ComfyUI input target")
            temp_target = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            shutil.copy2(resolved_source, temp_target)
            os.replace(temp_target, target)
            return {
                "name": target.name,
                "subfolder": UPLOAD_SUBFOLDER,
                "type": "input",
            }
        except OSError as exc:
            LOGGER.debug("Direct ComfyUI input staging failed; using HTTP: %s", exc)
        finally:
            if temp_target is not None:
                try:
                    temp_target.unlink(missing_ok=True)
                except OSError:
                    pass
    return client.upload_image(source, remote_name, UPLOAD_SUBFOLDER)


# ============================================================================
# COMFY GENERATION
# Загружает временные изображения, применяет bindings/values к копии workflow,
# ставит prompt в очередь и возвращает только первое подходящее output-изображение.
# ============================================================================
def _create_blank_helper_image(request_id: str, width: int, height: int) -> Path:
    image_module = PIL_IMAGE_MODULE
    if image_module is None:
        raise UserVisibleError(
            "Pillow was not initialized during Python startup. "
            f"Restart {APP_NAME}. Log: {LOG_FILE}"
        )
    safe_width = max(1, int(width or 1))
    safe_height = max(1, int(height or 1))
    path = TEMP_DIR / safe_filename(f"blank_{request_id}.png")
    try:
        image_module.new("RGB", (safe_width, safe_height), (255, 255, 255)).save(
            path, format="PNG", compress_level=6
        )
    except Exception as exc:
        raise UserVisibleError(
            "Could not create the neutral image for an unused LoadImage input. "
            f"Check temporary-folder access and free disk space. Details: {LOG_FILE}"
        ) from exc
    return path


def _selected_empty_load_image_candidates(
    analysis: Dict[str, Any], candidate_ids: Sequence[str]
) -> List[Dict[str, Any]]:
    """Resolve explicit empty-image roles to physical standard LoadImage nodes."""

    candidates = analysis.get("candidates", {}) if isinstance(analysis, dict) else {}
    input_candidates = candidates.get("input", []) if isinstance(candidates, dict) else []
    selected = {str(item) for item in candidate_ids if str(item)}
    result: List[Dict[str, Any]] = []
    for candidate in input_candidates if isinstance(input_candidates, list) else []:
        if not isinstance(candidate, dict) or str(candidate.get("id") or "") not in selected:
            continue
        meta = candidate.get("meta") if isinstance(candidate.get("meta"), dict) else {}
        if meta.get("load_image") and not meta.get("grouped"):
            result.append(candidate)
    return result


def _stage_blank_helper_upload(
    client: ComfyClient,
    request_id: str,
    input_folder: Optional[Path],
    direct_input_verified: bool,
    width: int,
    height: int,
) -> Dict[str, Any]:
    blank_path = _create_blank_helper_image(request_id, width, height)
    try:
        return stage_comfy_image(
            client,
            blank_path,
            safe_filename(f"blank_{request_id}.png"),
            input_folder,
            direct_input_verified,
        )
    finally:
        try:
            blank_path.unlink(missing_ok=True)
        except OSError:
            pass


def _run_comfy_generation(task: Dict[str, Any], request_id: str) -> None:
    # generation_context отмечает задачу активной до анализа workflow и upload.
    # Поэтому interrupt первого progress-сегмента не теряется до prompt_id.
    message = task.get("message") or {}
    workflow_id = str(message.get("workflow_id") or "")
    input_path = Path(str(message.get("input") or ""))
    mask_path = Path(str(message.get("mask") or "")) if message.get("mask") else None
    inpaint_mode = str(message.get("inpaint_mode") or "")
    output_dir = TEMP_DIR
    width = int(message.get("width") or 0)
    height = int(message.get("height") or 0)
    relative_path = str(message.get("relative_path") or "")
    values = message.get("values") if isinstance(message.get("values"), dict) else {}
    reference_files = message.get("references") if isinstance(message.get("references"), list) else []
    overrides = normalize_binding_overrides(
        message.get("binding_overrides") if isinstance(message.get("binding_overrides"), dict) else None
    )
    if not input_path.is_file():
        raise UserVisibleError(f"Photoshop temporary file was not found: {input_path}")
    if inpaint_mode not in {"", "input_alpha", "load_image_mask"}:
        raise UserVisibleError(f"Unknown Comfy inpaint mode: {inpaint_mode}")
    if inpaint_mode and (mask_path is None or not mask_path.is_file()):
        raise UserVisibleError(f"Photoshop temporary mask was not found: {mask_path}")

    repository = WorkflowRepository(RUNTIME.workflows_folder)
    workflow_file = repository.get(workflow_id, relative_path=relative_path)
    workflow_data = WORKFLOW_RUNTIME_CACHE.load_json(workflow_file, repository)
    raise_if_generation_cancelled(request_id)

    analysis = None
    validation_schema = None

    runtime_bundle = WORKFLOW_RUNTIME_CACHE.get_analysis(workflow_file, overrides)
    if runtime_bundle is not None:
        analysis, validation_schema = runtime_bundle
        LOGGER.info("Comfy generation: process analysis cache used")

    if analysis is None:
        cached_bundle = SCHEMA_CACHE.load_fast_bundle(workflow_file, overrides)
        if cached_bundle is not None:
            analysis, validation_schema = cached_bundle
            LOGGER.info("Comfy generation: disk analysis cache used")
            WORKFLOW_RUNTIME_CACHE.put_analysis(
                workflow_file, overrides, analysis, validation_schema
            )

    if analysis is None:
        object_info = get_object_info(force=False)
        analysis = WorkflowAnalyzer(workflow_data, object_info).analyze(overrides)
        validation_schema = build_validation_schema(workflow_data, object_info)
        WORKFLOW_RUNTIME_CACHE.put_analysis(
            workflow_file, overrides, analysis, validation_schema
        )
        SCHEMA_CACHE.save(
            workflow_file, analysis, validation_schema, overrides
        )
    # WorkflowPatcher receives current ComfyUI type metadata from the compact cache.
    object_info = validation_schema or {}
    raise_if_generation_cancelled(request_id)
    if not analysis.get("valid"):
        messages = [
            item["message"]
            for item in analysis.get("diagnostics", [])
            if item.get("level") == "error"
        ]
        raise UserVisibleError(
            "The workflow is not ready to run:\n• " + "\n• ".join(messages),
            "workflow_not_ready",
        )

    mask_binding = analysis.get("bindings", {}).get("inpaint_mask")
    if inpaint_mode:
        if not isinstance(mask_binding, dict) or not mask_binding.get("mode"):
            raise UserVisibleError(
                "No suitable option was found for Inpaint mask. In Workflow settings, "
                "select the main LoadImage MASK or a LoadImageMask node.",
                "inpaint_mask_missing",
            )
        if str(mask_binding.get("mode")) != inpaint_mode:
            raise UserVisibleError(
                "The workflow mask configuration changed. Reopen the main script window.",
                "inpaint_mask_changed",
            )
        if not mask_binding.get("connected"):
            if inpaint_mode == "input_alpha":
                raise UserVisibleError(
                    "The main LoadImage MASK is not used by the workflow. Connect its MASK output "
                    "to the inpaint branch in ComfyUI or select another Inpaint mask option.",
                    "inpaint_main_mask_unused",
                )
            raise UserVisibleError(
                "The selected LoadImageMask MASK is not used by the workflow. Connect its MASK output "
                "to the inpaint branch in ComfyUI or select another Inpaint mask option.",
                "inpaint_node_mask_unused",
            )

    # Если JSX/другой клиент не передал dimensions и workflow наследует размер
    # входного изображения, берём его прямо из JPEG/PNG. Для workflow с явными
    # width/height отсутствие размеров остаётся ошибкой.
    if width <= 0 or height <= 0:
        width, height = read_image_dimensions(input_path)
    if (analysis.get("bindings", {}).get("width") or analysis.get("bindings", {}).get("height")) and (width <= 0 or height <= 0):
        raise UserVisibleError("The workflow requires width/height, but the input JPEG size could not be determined.")

    client = current_client()
    input_folder = _existing_directory(RUNTIME.comfy_input_folder)
    output_folder = _existing_directory(RUNTIME.comfy_output_folder)
    direct_input_verified = bool(
        input_folder and verify_local_comfy_input_folder(client, input_folder)
    )
    if input_folder and not direct_input_verified:
        LOGGER.warning(
            "Detected ComfyUI input folder is no longer served; using HTTP upload"
        )
        invalidate_detected_comfy_input_folder()
        input_folder = None
    GENERATION.input_folder = input_folder
    GENERATION.output_folder = output_folder

    input_suffix = input_path.suffix.lower() if input_path.suffix else ".jpg"
    remote_name = safe_filename(f"input_{request_id}{input_suffix}")
    uploaded = stage_comfy_image(
        client, input_path, remote_name, input_folder, direct_input_verified
    )
    GENERATION.uploaded_images.append(uploaded)
    raise_if_generation_cancelled(request_id)

    uploaded_mask: Optional[Dict[str, Any]] = None
    if inpaint_mode and mask_path is not None:
        mask_suffix = mask_path.suffix.lower() if mask_path.suffix else ".png"
        remote_mask_name = safe_filename(f"mask_{request_id}{mask_suffix}")
        uploaded_mask = stage_comfy_image(
            client, mask_path, remote_mask_name, input_folder, direct_input_verified
        )
        GENERATION.uploaded_images.append(uploaded_mask)
        raise_if_generation_cancelled(request_id)

    uploaded_references: Dict[str, Dict[str, Any]] = {}
    generation_warnings: List[Dict[str, Any]] = []
    def add_generation_warning(
        message: str,
        code: str = "",
        params: Optional[Sequence[Any]] = None,
    ) -> None:
        normalized = str(message or "").strip()
        if not normalized or any(
            str(item.get("message") or "") == normalized for item in generation_warnings
        ):
            return
        item: Dict[str, Any] = {"message": normalized}
        if code:
            item["code"] = code
        if params:
            item["params"] = [str(value) for value in params]
        generation_warnings.append(item)

    valid_reference_ids = {
        str(item.get("id"))
        for item in analysis.get("bindings", {}).get("reference_images", [])
        if isinstance(item, dict) and item.get("id")
    }
    for reference_index, reference in enumerate(reference_files):
        if not isinstance(reference, dict):
            add_generation_warning(
                f"Reference #{reference_index + 1} was not applied: invalid file metadata.",
                "generation_reference_invalid",
                [reference_index + 1],
            )
            continue
        binding_id = str(reference.get("binding_id") or "")
        reference_path = Path(str(reference.get("path") or ""))
        if not binding_id:
            add_generation_warning(
                f"Reference #{reference_index + 1} was not applied: binding_id is missing.",
                "generation_reference_binding_missing",
                [reference_index + 1],
            )
            continue
        if binding_id not in valid_reference_ids:
            add_generation_warning(
                f"Reference {binding_id} was not applied: the input is missing from the current schema. "
                "Reanalyze the workflow.",
                "generation_reference_input_missing",
                [binding_id],
            )
            continue
        if not reference_path.is_file():
            add_generation_warning(
                f"Reference {binding_id} was not applied: file not found ({reference_path}).",
                "generation_reference_file_missing",
                [binding_id, reference_path],
            )
            continue
        suffix = reference_path.suffix or ".jpg"
        remote_reference_name = safe_filename(f"reference_{reference_index + 1}_{request_id}{suffix}")
        uploaded_reference = stage_comfy_image(
            client,
            reference_path,
            remote_reference_name,
            input_folder,
            direct_input_verified,
        )
        uploaded_references[binding_id] = uploaded_reference
        GENERATION.uploaded_images.append(uploaded_reference)
        raise_if_generation_cancelled(request_id)

    reference_bindings = [
        item for item in analysis.get("bindings", {}).get("reference_images", [])
        if isinstance(item, dict)
    ]
    missing_selected_references = [
        item for item in reference_bindings
        if item.get("load_image")
        and str(item.get("id") or "")
        and str(item.get("id") or "") not in uploaded_references
    ]
    selected_empty_ids = list(overrides.get("empty_inputs") or []) if overrides else []
    for automatic_empty_id in analysis.get("automatic_empty_inputs", []):
        if automatic_empty_id not in selected_empty_ids:
            selected_empty_ids.append(automatic_empty_id)
    neutralized_inputs = _selected_empty_load_image_candidates(
        analysis, selected_empty_ids
    )

    neutral_image: Optional[Dict[str, Any]] = None
    if missing_selected_references or neutralized_inputs:
        neutral_image = _stage_blank_helper_upload(
            client, request_id, input_folder, direct_input_verified, width, height
        )
        GENERATION.uploaded_images.append(neutral_image)
        raise_if_generation_cancelled(request_id)

    patcher = WorkflowPatcher(workflow_data, object_info)
    patched = patcher.apply(
        bindings=analysis["bindings"],
        controls=analysis["controls"],
        control_values=values,
        uploaded_image=uploaded,
        uploaded_mask=uploaded_mask,
        uploaded_references=uploaded_references,
        neutral_image=neutral_image,
        neutralized_inputs=neutralized_inputs,
        width=width,
        height=height,
        request_id=request_id,
        size_selection_mode=str(analysis.get("size_selection_mode") or "auto"),
    )
    for warning_item in patcher.warnings:
        add_generation_warning(
            str(warning_item.get("message") or ""),
            str(warning_item.get("code") or ""),
            warning_item.get("params") if isinstance(warning_item.get("params"), list) else None,
        )
    for warning_item in generation_warnings:
        LOGGER.warning(
            "Generation parameter was not applied: %s",
            warning_item.get("message"),
        )

    raise_if_generation_cancelled(request_id)
    client_id = "photoshop-" + uuid.uuid4().hex
    prompt_id = str(uuid.uuid4())
    sampler_node_ids = WORKFLOW_RUNTIME_CACHE.get_sampler_node_ids(
        workflow_file, workflow_data
    )
    progress_watcher = ComfyProgressWatcher(
        client, client_id, sampler_node_ids
    )
    progress_watcher.connect()
    GENERATION.progress_watcher = progress_watcher

    GENERATION.prompt_id = prompt_id
    GENERATION.queued = True

    queue_result = client.queue_prompt(patched, client_id, prompt_id)
    actual_prompt_id = str(queue_result.get("prompt_id") or prompt_id)
    GENERATION.prompt_id = actual_prompt_id
    if request_is_cancelled(request_id):
        cancel_current_generation(request_id)
        raise CancelledError("Generation was cancelled.")

    # WebSocket задаёт границу инициализации; /history подтверждает результат.
    deadline = time.monotonic() + int(message.get("timeout") or RUNTIME.generation_timeout)
    history_entry: Optional[Dict[str, Any]] = None
    progress_stage_started = False

    while time.monotonic() < deadline:
        touch_activity()
        raise_if_generation_cancelled(request_id)

        if (
            not progress_stage_started
            and progress_watcher.sampling_started(actual_prompt_id)
        ):
            # После начала sampling WebSocket больше не нужен.
            progress_watcher.close()
            notify_generation_progress_ready(
                request_id, "comfy", actual_prompt_id
            )
            progress_stage_started = True

        history = client.get_history(actual_prompt_id)
        history_entry = extract_history_entry(history, actual_prompt_id)
        if history_entry:
            GENERATION.queued = False
            completed, history_error = history_status(history_entry)
            if history_error:
                raise UserVisibleError(history_error)
            if completed:
                if not progress_stage_started:
                    notify_generation_progress_ready(
                        request_id, "comfy", actual_prompt_id
                    )
                    progress_stage_started = True
                break

        if not progress_stage_started:
            try:
                queue_state = client.get_queue()
                if comfy_queue_contains_prompt(
                    queue_state, "queue_running", actual_prompt_id
                ):
                    GENERATION.queued = False
                    if not progress_watcher.can_track_sampling:
                        notify_generation_progress_ready(
                            request_id, "comfy", actual_prompt_id
                        )
                        progress_stage_started = True
            except UserVisibleError as exc:
                # Временная ошибка /queue не прерывает генерацию.
                LOGGER.debug("Comfy queue polling failed: %s", exc)

        time.sleep(
            HISTORY_RESULT_POLL_INTERVAL
            if progress_stage_started
            else HISTORY_PREPARE_POLL_INTERVAL
        )
    else:
        try:
            client.interrupt(actual_prompt_id)
        except Exception:
            pass
        raise UserVisibleError("Timed out while waiting for ComfyUI generation.")

    if not history_entry:
        raise UserVisibleError("ComfyUI completed the task, but history was not found.")

    image_info = select_output_image(history_entry, analysis["bindings"]["output_image"])
    output_format = normalize_output_format(message.get("output_format"))
    destination = output_dir / f"{now_timestamp()}-{safe_filename(workflow_file.name)}.{output_format}"
    destination = client.download_image_for_photoshop(
        image_info,
        destination,
        quality=95,
        output_format=output_format,
        local_output_folder=output_folder,
        request_id=request_id,
    )
    if _comfy_request_output_path(destination, output_folder, request_id) is not None:
        GENERATION.preserved_output_path = destination

    # Итоговый путь всегда отправляется после init/ACK.
    answer(
        {
            "path": str(destination),
            "prompt_id": actual_prompt_id,
            "workflow_hash": WorkflowRepository.ensure_hash(workflow_file),
            "generated_seeds": patcher.generated_seeds,
            "warnings": generation_warnings,
        },
        request_id=request_id,
    )


def generation_worker() -> None:
    while not WORKER_STOP.is_set():
        try:
            task = GENERATION_QUEUE.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            if str(task.get("type") or "") == "forge_generate":
                run_forge_generation(task)
            else:
                run_generation(task)
        except CancelledError:
            cancelled_answer(task.get("request_id"))
        except UserVisibleError as exc:
            LOGGER.warning("Generation error: %s", exc)
            error_answer(exc, task.get("request_id"))
        except Exception as exc:
            log_exception("Unhandled generation error")
            error_answer(f"Internal Python error: {exc}", task.get("request_id"))
        finally:
            # Состояние backend и временные Comfy-upload очищает
            # generation_context; worker отвечает только за очередь задач.
            GENERATION_QUEUE.task_done()


def _backend_probe_result(*, available: bool, details: Optional[Dict[str, Any]] = None,
                          checked: bool = True) -> Dict[str, Any]:
    return {
        "available": bool(available),
        "details": details or {},
        "checked": bool(checked),
        "checked_at": time.time() if checked else 0.0,
    }


def _compose_backend_status(comfy: Dict[str, Any], forge: Dict[str, Any]) -> Dict[str, Any]:
    available = [name for name, item in (("comfy", comfy), ("forge", forge)) if item.get("available")]
    mode = "both" if len(available) == 2 else (available[0] if available else "none")
    checked_at_values = [
        float(item.get("checked_at") or 0.0)
        for item in (comfy, forge)
        if item.get("checked")
    ]
    return {
        "mode": mode,
        "backends": {"comfy": comfy, "forge": forge},
        # Свежесть снимка определяется более старой проверкой.
        "checked_at": min(checked_at_values) if len(checked_at_values) == 2 else 0.0,
    }


def _probe_comfy_full(host: str, port: int, *, update_runtime: bool) -> Dict[str, Any]:
    global COMFY_INPUT_FOLDER_ENDPOINT
    endpoint = (normalize_comfy_host(host), int(port))
    try:
        stats = ComfyClient(host, int(port)).ping(timeout=2.0)
        if update_runtime and COMFY_INPUT_FOLDER_ENDPOINT == endpoint:
            input_folder = RUNTIME.comfy_input_folder
            output_folder = RUNTIME.comfy_output_folder
        else:
            input_folder = detect_comfy_input_folder(stats, host, int(port))
            output_folder = detect_comfy_output_folder(stats, host, int(port))
        if update_runtime:
            RUNTIME.comfy_input_folder = input_folder
            RUNTIME.comfy_output_folder = output_folder
            COMFY_INPUT_FOLDER_ENDPOINT = endpoint
            schedule_comfy_folder_cleanup(input_folder, output_folder)
        details = {
            "validated": True,
            "input_folder": str(input_folder or ""),
            "output_folder": str(output_folder or ""),
        }
        return _backend_probe_result(available=True, details=details)
    except Exception:
        if update_runtime:
            RUNTIME.comfy_input_folder = None
            RUNTIME.comfy_output_folder = None
            COMFY_INPUT_FOLDER_ENDPOINT = None
        return _backend_probe_result(available=False)


def _probe_comfy_light(host: str, port: int, previous: Dict[str, Any]) -> Dict[str, Any]:
    try:
        response = ComfyClient(host, int(port)).get_json("/prompt", timeout=2.0)
        if not isinstance(response, dict):
            raise UserVisibleError("ComfyUI health response is invalid.")
        details = copy.deepcopy(previous.get("details") or {})
        return _backend_probe_result(available=True, details=details)
    except Exception:
        return _backend_probe_result(available=False)


def _probe_comfy_regular(host: str, port: int, previous: Dict[str, Any], *,
                         update_runtime: bool) -> Dict[str, Any]:
    validated = bool(
        previous.get("available")
        and (previous.get("details") or {}).get("validated")
    )
    light = _probe_comfy_light(host, port, previous)
    if validated or not light.get("available"):
        if update_runtime and not light.get("available"):
            invalidate_detected_comfy_input_folder()
            RUNTIME.comfy_output_folder = None
        return light
    return _probe_comfy_full(host, port, update_runtime=update_runtime)


def _probe_forge_full(host: str, port: int) -> Dict[str, Any]:
    global FORGE_CATALOG_CACHE_SERVER
    try:
        client = ForgeClient(host, int(port), timeout=2.0)
        options = client.get_json("sdapi/v1/options", timeout=2.0)
        is_forge_neo = isinstance(options, dict) and "forge_additional_modules" in options
        details = {
            "forge_neo": bool(is_forge_neo),
            "validated": bool(is_forge_neo),
        }
        if is_forge_neo:
            # Переиспользуем /options при первой загрузке Forge schema.
            server_key = (normalize_comfy_host(host), int(port))
            with FORGE_CATALOG_CACHE_LOCK:
                if FORGE_CATALOG_CACHE_SERVER != server_key:
                    FORGE_CATALOG_CACHE.clear()
                    FORGE_CATALOG_CACHE_SERVER = server_key
            _update_forge_catalog_current(options)
        return _backend_probe_result(available=is_forge_neo, details=details)
    except Exception:
        return _backend_probe_result(available=False)


def _probe_forge_light(host: str, port: int, previous: Dict[str, Any]) -> Dict[str, Any]:
    try:
        client = ForgeClient(host, int(port), timeout=2.0)
        response = client.get_json(
            "sdapi/v1/progress?skip_current_image=true", timeout=2.0
        )
        if not isinstance(response, dict) or "progress" not in response:
            raise UserVisibleError("Forge Neo health response is invalid.")
        details = copy.deepcopy(previous.get("details") or {})
        return _backend_probe_result(available=True, details=details)
    except Exception:
        return _backend_probe_result(available=False)


def _probe_forge_regular(host: str, port: int, previous: Dict[str, Any]) -> Dict[str, Any]:
    details = previous.get("details") or {}
    validated = bool(
        previous.get("available")
        and details.get("validated")
        and details.get("forge_neo")
    )
    light = _probe_forge_light(host, port, previous)
    if validated or not light.get("available"):
        return light
    return _probe_forge_full(host, port)


# BACKEND DISCOVERY
def _probe_backends_unlocked(host: str, comfy_port: int, forge_port: int, *,
                             update_runtime: bool = False,
                             full_check: bool = False,
                             previous_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized_host = normalize_comfy_host(host)
    endpoints = _backend_endpoints(normalized_host, comfy_port, forge_port)
    previous_status = previous_status or _cached_backend_status(endpoints)
    previous_status = previous_status or _unchecked_backend_status()
    previous = previous_status["backends"]
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="BackendProbe") as executor:
        if full_check:
            comfy_future = executor.submit(
                _probe_comfy_full, normalized_host, int(comfy_port),
                update_runtime=update_runtime,
            )
            forge_future = executor.submit(
                _probe_forge_full, normalized_host, int(forge_port)
            )
        else:
            comfy_future = executor.submit(
                _probe_comfy_regular, normalized_host, int(comfy_port),
                previous["comfy"], update_runtime=update_runtime,
            )
            forge_future = executor.submit(
                _probe_forge_regular, normalized_host, int(forge_port),
                previous["forge"],
            )
        comfy = comfy_future.result()
        forge = forge_future.result()
    return _compose_backend_status(comfy, forge)


def probe_backends(host: str, comfy_port: int, forge_port: int, *,
                   update_runtime: bool = False) -> Dict[str, Any]:
    with BACKEND_PROBE_LOCK:
        return _probe_backends_unlocked(
            host, comfy_port, forge_port,
            update_runtime=update_runtime, full_check=True,
        )


def _backend_endpoints(host: str, comfy_port: int, forge_port: int) -> Tuple[str, int, int]:
    return normalize_comfy_host(host), int(comfy_port), int(forge_port)


def _store_backend_status(status: Dict[str, Any], endpoints: Tuple[str, int, int]) -> Dict[str, Any]:
    global BACKEND_STATUS_CACHE, BACKEND_STATUS_ENDPOINTS
    snapshot = copy.deepcopy(status)
    with BACKEND_STATUS_LOCK:
        BACKEND_STATUS_CACHE = snapshot
        BACKEND_STATUS_ENDPOINTS = endpoints
    return copy.deepcopy(snapshot)


def _cached_backend_status(
    endpoints: Tuple[str, int, int],
) -> Optional[Dict[str, Any]]:
    with BACKEND_STATUS_LOCK:
        if BACKEND_STATUS_ENDPOINTS != endpoints or BACKEND_STATUS_CACHE is None:
            return None
        return copy.deepcopy(BACKEND_STATUS_CACHE)


def _store_backend_test_result(
    status: Dict[str, Any], endpoints: Tuple[str, int, int]
) -> Dict[str, Any]:
    token = uuid.uuid4().hex
    snapshot = copy.deepcopy(status)
    response = copy.deepcopy(status)
    response["probe_token"] = token
    now = time.time()
    with BACKEND_STATUS_LOCK:
        for key in list(BACKEND_TEST_RESULTS):
            if now - float(BACKEND_TEST_RESULTS[key].get("created") or 0.0) > 600:
                BACKEND_TEST_RESULTS.pop(key, None)
        while len(BACKEND_TEST_RESULTS) >= 8:
            BACKEND_TEST_RESULTS.popitem(last=False)
        BACKEND_TEST_RESULTS[token] = {
            "status": snapshot,
            "endpoints": endpoints,
            "created": now,
        }
    return response


def _take_backend_test_result(
    token: str, endpoints: Tuple[str, int, int]
) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    with BACKEND_STATUS_LOCK:
        item = BACKEND_TEST_RESULTS.pop(token, None)
    if item is None or item.get("endpoints") != endpoints:
        return None
    if time.time() - float(item.get("created") or 0.0) > 600:
        return None
    return copy.deepcopy(item.get("status") or {})


def _apply_comfy_runtime_status(
    status: Dict[str, Any], endpoints: Tuple[str, int, int]
) -> None:
    global COMFY_INPUT_FOLDER_ENDPOINT
    comfy = (status.get("backends") or {}).get("comfy") or {}
    if comfy.get("checked") is False:
        return
    if not comfy.get("available"):
        RUNTIME.comfy_input_folder = None
        RUNTIME.comfy_output_folder = None
        COMFY_INPUT_FOLDER_ENDPOINT = None
        return
    details = comfy.get("details") or {}
    if not details.get("validated") or "input_folder" not in details:
        return
    input_value = str(details.get("input_folder") or "")
    output_value = str(details.get("output_folder") or "")
    input_folder = Path(input_value) if input_value else None
    output_folder = Path(output_value) if output_value else None
    comfy_endpoint = (endpoints[0], endpoints[1])
    changed = (
        COMFY_INPUT_FOLDER_ENDPOINT != comfy_endpoint
        or RUNTIME.comfy_input_folder != input_folder
        or RUNTIME.comfy_output_folder != output_folder
    )
    RUNTIME.comfy_input_folder = input_folder
    RUNTIME.comfy_output_folder = output_folder
    COMFY_INPUT_FOLDER_ENDPOINT = comfy_endpoint
    if changed:
        schedule_comfy_folder_cleanup(input_folder, output_folder)


def _refresh_backend_status(
    endpoints: Tuple[str, int, int],
    *,
    reuse_cached: bool,
    max_cache_age: Optional[float] = None,
) -> Dict[str, Any]:
    """Refresh both backends after rechecking the shared cache."""

    with BACKEND_PROBE_LOCK:
        cached = _cached_backend_status(endpoints)
        if reuse_cached:
            if cached is not None:
                checked_at = float(cached.get("checked_at") or 0.0)
                if max_cache_age is None or time.time() - checked_at < max_cache_age:
                    return cached
        status = _probe_backends_unlocked(
            *endpoints,
            update_runtime=True,
            previous_status=cached,
        )
        return _store_backend_status(status, endpoints)


def _refresh_selected_backend_status(
    endpoints: Tuple[str, int, int], backend: str
) -> Dict[str, Any]:
    """Check the selected backend and retain the other backend status.

    If a background monitor probe already owns the shared probe lock when this
    request arrives, its result is fresh enough to satisfy the live check. In
    that case do not immediately perform the same network probe a second time.
    """

    requested_at = time.time()
    with BACKEND_PROBE_LOCK:
        cached = _cached_backend_status(endpoints) or _unchecked_backend_status()
        selected = (cached.get("backends") or {}).get(backend) or {}
        selected_checked_at = float(selected.get("checked_at") or 0.0)
        if selected_checked_at >= requested_at and selected.get("checked") is not False:
            return cached

        host, comfy_port, forge_port = endpoints
        if backend == "comfy":
            comfy = _probe_comfy_regular(
                host, comfy_port, cached["backends"]["comfy"], update_runtime=True
            )
            forge = cached["backends"]["forge"]
        else:
            comfy = cached["backends"]["comfy"]
            forge = _probe_forge_regular(
                host, forge_port, cached["backends"]["forge"]
            )
        return _store_backend_status(
            _compose_backend_status(comfy, forge), endpoints
        )


def _refresh_changed_backend_status(
    previous_endpoints: Tuple[str, int, int],
    endpoints: Tuple[str, int, int],
    previous_status: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Fully validate changed endpoints and retain unchanged results."""

    with BACKEND_PROBE_LOCK:
        previous_status = previous_status or _unchecked_backend_status()
        previous = previous_status["backends"]
        old_host, old_comfy_port, old_forge_port = previous_endpoints
        host, comfy_port, forge_port = endpoints
        comfy_changed = (old_host, old_comfy_port) != (host, comfy_port)
        forge_changed = (old_host, old_forge_port) != (host, forge_port)
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="BackendSettingsProbe"
        ) as executor:
            comfy_future = None
            forge_future = None
            if comfy_changed:
                comfy_future = executor.submit(
                    _probe_comfy_full, host, comfy_port, update_runtime=True
                )
            elif previous["comfy"].get("checked") is False:
                comfy_future = executor.submit(
                    _probe_comfy_regular,
                    host,
                    comfy_port,
                    previous["comfy"],
                    update_runtime=True,
                )
            if forge_changed:
                forge_future = executor.submit(_probe_forge_full, host, forge_port)
            elif previous["forge"].get("checked") is False:
                forge_future = executor.submit(
                    _probe_forge_regular, host, forge_port, previous["forge"]
                )
            comfy = comfy_future.result() if comfy_future else previous["comfy"]
            forge = forge_future.result() if forge_future else previous["forge"]
        return _store_backend_status(
            _compose_backend_status(comfy, forge), endpoints
        )


def detect_backends() -> Dict[str, Any]:
    """Return the monitor snapshot; synchronously probe only on cache miss."""

    endpoints = _backend_endpoints(
        RUNTIME.backend_host, RUNTIME.comfy_port, RUNTIME.forge_port
    )
    cached = _cached_backend_status(endpoints)
    if cached is not None:
        return cached
    return _refresh_backend_status(endpoints, reuse_cached=True)


def _unchecked_backend_status() -> Dict[str, Any]:
    status = _compose_backend_status(
        _backend_probe_result(available=False, checked=False),
        _backend_probe_result(available=False, checked=False),
    )
    status["checked_at"] = 0.0
    return status


def apply_handshake(message: Dict[str, Any]) -> Dict[str, Any]:
    previous_endpoints = _backend_endpoints(
        RUNTIME.backend_host, RUNTIME.comfy_port, RUNTIME.forge_port
    )
    previous_status = _cached_backend_status(previous_endpoints)
    host = message.get("host")
    if host:
        RUNTIME.backend_host = normalize_comfy_host(host)
    if message.get("comfyPort"):
        RUNTIME.comfy_port = int(message["comfyPort"])
    if message.get("forgePort"):
        RUNTIME.forge_port = int(message["forgePort"])
    workflows_folder = message.get("workflowsFolder")
    if workflows_folder:
        RUNTIME.workflows_folder = Path(str(workflows_folder))
    if message.get("generationTimeout"):
        RUNTIME.generation_timeout = max(30, int(message["generationTimeout"]))
    if "pythonIdleTimeout" in message:
        RUNTIME.idle_timeout_seconds = max(
            0, min(7 * 24 * 60 * 60, int(message["pythonIdleTimeout"]))
        )
    if "backendMonitorInterval" in message:
        RUNTIME.backend_monitor_interval_seconds = max(
            2, min(300, int(message["backendMonitorInterval"]))
        )

    endpoints = _backend_endpoints(
        RUNTIME.backend_host, RUNTIME.comfy_port, RUNTIME.forge_port
    )
    endpoints_changed = previous_endpoints != endpoints
    if endpoints_changed:
        invalidate_detected_comfy_input_folder()
        RUNTIME.comfy_output_folder = None
    status_mode = str(message.get("backendStatusMode") or "cached").lower()
    verify_backend = str(message.get("verifyBackend") or "").strip().lower()
    tested_status = _take_backend_test_result(
        str(message.get("backendProbeToken") or ""), endpoints
    )
    if verify_backend in {"comfy", "forge"}:
        status = _refresh_selected_backend_status(endpoints, verify_backend)
    elif status_mode == "silent":
        status = _cached_backend_status(endpoints) or _unchecked_backend_status()
    elif tested_status is not None:
        status = _store_backend_status(tested_status, endpoints)
    elif endpoints_changed:
        status = _refresh_changed_backend_status(
            previous_endpoints, endpoints, previous_status
        )
    else:
        status = detect_backends()
    _apply_comfy_runtime_status(status, endpoints)
    BACKEND_MONITOR_WAKE.set()
    runtime_data = {
        "host": RUNTIME.backend_host,
        "comfy_port": RUNTIME.comfy_port,
        "forge_port": RUNTIME.forge_port,
        "comfy_input_folder": str(RUNTIME.comfy_input_folder or ""),
        "comfy_output_folder": str(RUNTIME.comfy_output_folder or ""),
        "workflows_folder": str(RUNTIME.workflows_folder),
        "generation_timeout": RUNTIME.generation_timeout,
        "idle_timeout_seconds": RUNTIME.idle_timeout_seconds,
        "backend_monitor_interval_seconds": RUNTIME.backend_monitor_interval_seconds,
    }
    try:
        RUNTIME_FILE.write_text(json.dumps(runtime_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        LOGGER.warning("Could not write runtime.json")
    return {
        "version": VERSION,
        "comfy_input_folder": str(RUNTIME.comfy_input_folder or ""),
        "mode": status.get("mode", "none"),
        "backends": status.get("backends", {}),
    }


# ДИСПЕТЧЕР КОМАНД JSX И ЛОКАЛЬНЫЙ SOCKET-СЕРВЕР
def handle_command(command: Dict[str, Any]) -> None:
    touch_activity()
    request_id = command.get("request_id")
    command_type = str(command.get("type") or "")
    message = command.get("message")
    command_started = time.monotonic()
    LOGGER.info("API command: type=%s request=%s", command_type, request_id)
    if not isinstance(message, dict):
        message = {}

    try:
        protocol = command.get("protocol")
        if str(protocol or "") != str(API_PROTOCOL):
            raise UserVisibleError(
                f"Incompatible API protocol version: {protocol}; expected {API_PROTOCOL}."
            )

        if command_type == "ping":
            startup = startup_status_snapshot()
            answer({
                "protocol": API_PROTOCOL,
                "startup_status": str(startup.get("status") or "starting"),
                "startup_message": str(startup.get("message") or ""),
                "startup_log_file": str(startup.get("log_file") or LOG_FILE),
            }, request_id)
            # После startup-ошибки следующий запуск повторит подготовку.
            if str(startup.get("status") or "") == "error":
                WORKER_STOP.set()
                BACKEND_MONITOR_WAKE.set()
                try:
                    with socket.create_connection((API_HOST, API_RECEIVE_PORT), timeout=1):
                        pass
                except OSError:
                    pass
            return

        startup = startup_status_snapshot()
        startup_state = str(startup.get("status") or "starting")
        if startup_state != "ready":
            if startup_state == "error":
                raise UserVisibleError(
                    str(startup.get("message") or "Python API startup failed.")
                )
            raise UserVisibleError("Python API is still initializing.")

        if command_type == "handshake":
            answer(apply_handshake(message), request_id)
            return

        if command_type == "probe_backends":
            # Ручная проверка сбрасывает Forge-каталог для нового endpoint.
            clear_forge_catalog_cache()
            endpoints = _backend_endpoints(
                str(message.get("host") or RUNTIME.backend_host),
                int(message.get("comfyPort") or RUNTIME.comfy_port),
                int(message.get("forgePort") or RUNTIME.forge_port),
            )
            status = probe_backends(
                *endpoints,
                update_runtime=False,
            )
            answer(_store_backend_test_result(status, endpoints), request_id)
            return

        if command_type == "workflow_list":
            repository = WorkflowRepository(RUNTIME.workflows_folder)
            workflows = [item.public_dict() for item in repository.list_workflows()]
            answer({"items": workflows, "folder": str(RUNTIME.workflows_folder)}, request_id)
            return

        if command_type in {"workflow_get", "workflow_reinitialize"}:
            workflow_id = str(message.get("workflow_id") or "")
            overrides = message.get("binding_overrides")
            if not isinstance(overrides, dict):
                overrides = None
            force = command_type == "workflow_reinitialize"
            if force:
                invalidate_workflow_cache(workflow_id)
            result = analyze_workflow(
                workflow_id,
                overrides=overrides,
                force=force,
                relative_path=str(message.get("relative_path") or ""),
            )
            if not result.get("valid", False):
                LOGGER.info(
                    "Invalid workflow returned as schema valid=false; diagnostics=%s",
                    len(result.get("diagnostics", [])),
                )
            answer(result, request_id)
            LOGGER.info(
                "Command %s completed in %.2f s",
                command_type,
                time.monotonic() - command_started,
            )
            return

        if command_type == "workflow_save_values":
            values = message.get("values") if isinstance(message.get("values"), dict) else {}
            overrides = message.get("binding_overrides") if isinstance(message.get("binding_overrides"), dict) else None
            answer(save_workflow_values(
                str(message.get("workflow_id") or ""),
                relative_path=str(message.get("relative_path") or ""),
                overrides=overrides,
                values=values,
                destination_path=str(message.get("destination_path") or ""),
            ), request_id)
            return

        if command_type == "forge_schema_save_values":
            values = message.get("values") if isinstance(message.get("values"), dict) else {}
            selected_loras = message.get("selected_loras") if isinstance(message.get("selected_loras"), list) else []
            answer(save_forge_schema_values(
                str(message.get("schema_id") or ""),
                schema_folder=message.get("schema_folder"),
                values=values,
                destination_path=str(message.get("destination_path") or ""),
                selected_loras=selected_loras,
            ), request_id)
            return

        if command_type == "forge_schema_list":
            items, schema_dir, invalid_schemas = list_forge_schemas(message.get("schema_folder"))
            answer({
                "items": items,
                "folder": str(schema_dir),
                "invalid_schemas": invalid_schemas,
            }, request_id)
            return

        if command_type == "forge_schema_get":
            answer(get_forge_schema(str(message.get("schema_id") or ""), message.get("schema_folder")), request_id)
            return

        if command_type == "forge_catalog":
            raw_sources = message.get("sources")
            sources = raw_sources if isinstance(raw_sources, list) else None
            answer(forge_catalog(
                sources,
                force=bool(message.get("force")),
                schema_folder=message.get("schema_folder"),
            ), request_id)
            return

        if command_type == "translate":
            source_text = str(message.get("text") or "").strip()
            if not source_text:
                answer("", request_id)
                return
            translation_module = DEEP_TRANSLATOR_MODULE
            if translation_module is None:
                raise UserVisibleError(
                    "deep-translator was not initialized during Python startup. "
                    f"Restart {APP_NAME}. Log: {LOG_FILE}"
                )
            try:
                translated = translation_module.GoogleTranslator(
                    source="auto", target="english"
                ).translate(source_text)
            except Exception as exc:
                LOGGER.exception("Prompt translation error")
                raise UserVisibleError(
                    f"Could not translate prompt: {exc}",
                    "translate_failed",
                    [exc],
                ) from exc
            answer(str(translated or ""), request_id)
            return

        if command_type in {"generate", "forge_generate"}:
            # Проверка и постановка должны быть атомарными: handle_client работает
            # в отдельных потоках, и два почти одновременных запроса не должны
            # пройти проверку GENERATION_QUEUE.empty() одновременно.
            with GENERATION_SUBMIT_LOCK:
                if (
                    GENERATION.active
                    or GENERATION.queued
                    or not GENERATION_QUEUE.empty()
                    or forge_post_in_progress()
                ):
                    raise UserVisibleError(
                        "The previous generation has not finished yet.",
                        "generation_already_running",
                    )
                # Резервируем единственный слот до queue.put(). Worker сначала
                # выставляет active=True и только затем снимает queued, поэтому
                # между приёмом команды и началом run_generation больше нет окна.
                GENERATION.queued = True
                GENERATION.queued_request_id = str(request_id or "")
                GENERATION_QUEUE.put(command)
                BACKEND_MONITOR_WAKE.set()
            # Первый ответ придёт из worker после успешного POST /prompt.
            return

        if command_type == "ack":
            # ACK нужен для двухстадийного listener-протокола. Отвечать на него
            # не требуется: JSX сразу открывает listener финальной стадии.
            ack_request_id = str(request_id or message.get("request_id") or "")
            if not GENERATION.request_id or not ack_request_id or ack_request_id == GENERATION.request_id:
                GENERATION.ack_event.set()
            return

        if command_type == "interrupt":
            interrupt_request_id = str(message.get("request_id") or request_id or "")
            cancel_current_generation(interrupt_request_id)
            # interrupt отправляется без listener: ответ не требуется.
            return

        raise UserVisibleError(f"Unknown API command: {command_type}")

    except UserVisibleError as exc:
        error_answer(exc, request_id)
    except Exception as exc:
        log_exception(f"Command error: {command_type}")
        error_answer(f"Internal Python error: {exc}", request_id)


def receive_json_message(client_socket: socket.socket) -> Dict[str, Any]:
    chunks: List[bytes] = []
    total = 0
    client_socket.settimeout(5.0)
    while True:
        chunk = client_socket.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_API_MESSAGE:
            raise UserVisibleError("Incoming API message is too large.")
        if b"\n" in chunk:
            break
    raw = b"".join(chunks).split(b"\n", 1)[0]
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise UserVisibleError("Python received invalid JSON from JSX.") from exc
    if not isinstance(value, dict):
        raise UserVisibleError("The API message root must be an object.")
    return value


def handle_client(client_socket: socket.socket) -> None:
    try:
        command = receive_json_message(client_socket)
        # JSX проверяет, открыт ли порт, коротким TCP connect/disconnect без
        # JSON. Idle watcher использует такой же пустой connect, чтобы разбудить
        # accept(). На пустое соединение нельзя отправлять ответ: listener JSX
        # ещё не открыт, и поток зря потратит время на повторные подключения.
        if command:
            handle_command(command)
    except UserVisibleError as exc:
        error_answer(exc)
    except Exception:
        log_exception("TCP client handling error")
        error_answer("Photoshop local connection to Python failed.")
    finally:
        try:
            client_socket.close()
        except OSError:
            pass


def backend_monitor_watcher() -> None:
    """Refresh both backend states without adding network work to JSX startup."""

    while not WORKER_STOP.is_set():
        interval = max(2, int(RUNTIME.backend_monitor_interval_seconds))
        endpoints = _backend_endpoints(
            RUNTIME.backend_host, RUNTIME.comfy_port, RUNTIME.forge_port
        )
        cached = _cached_backend_status(endpoints)
        checked_at = float(cached.get("checked_at") or 0.0) if cached else 0.0
        remaining = max(0.0, interval - (time.time() - checked_at)) if cached else 0.0
        if remaining > 0:
            BACKEND_MONITOR_WAKE.wait(remaining)
            BACKEND_MONITOR_WAKE.clear()
            continue
        if (
            GENERATION.active
            or GENERATION.queued
            or not GENERATION_QUEUE.empty()
            or forge_post_in_progress()
        ):
            BACKEND_MONITOR_WAKE.wait(min(1.0, float(interval)))
            BACKEND_MONITOR_WAKE.clear()
            continue
        # Даём новой генерации отменить фоновый probe.
        if BACKEND_MONITOR_WAKE.wait(0.1):
            BACKEND_MONITOR_WAKE.clear()
            continue
        if (
            GENERATION.active
            or GENERATION.queued
            or not GENERATION_QUEUE.empty()
            or forge_post_in_progress()
        ):
            continue
        try:
            _refresh_backend_status(
                endpoints,
                reuse_cached=True,
                max_cache_age=max(1.0, interval * 0.8),
            )
        except Exception:
            log_exception("Background backend status check failed")
            BACKEND_MONITOR_WAKE.wait(1.0)
            BACKEND_MONITOR_WAKE.clear()


def start_backend_monitor() -> None:
    global BACKEND_MONITOR_STARTED
    with BACKEND_MONITOR_START_LOCK:
        if BACKEND_MONITOR_STARTED:
            return
        BACKEND_MONITOR_STARTED = True
        monitor_thread = threading.Thread(
            target=backend_monitor_watcher,
            name="BackendMonitor",
            daemon=True,
        )
        monitor_thread.start()


def idle_watcher() -> None:
    while not WORKER_STOP.wait(5.0):
        with LAST_ACTIVITY_LOCK:
            idle = time.monotonic() - LAST_ACTIVITY
        if (
            RUNTIME.idle_timeout_seconds > 0
            and idle > RUNTIME.idle_timeout_seconds
            and not GENERATION.active
            and not GENERATION.queued
            and GENERATION_QUEUE.empty()
            and not forge_post_in_progress()
        ):
            LOGGER.info("Shutting down after %.0f seconds of inactivity", idle)
            WORKER_STOP.set()
            BACKEND_MONITOR_WAKE.set()
            # Пустое подключение будит accept().
            try:
                with socket.create_connection((API_HOST, API_RECEIVE_PORT), timeout=1):
                    pass
            except OSError:
                pass
            return


def load_runtime_file() -> None:
    try:
        if not RUNTIME_FILE.exists():
            return
        data = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
        RUNTIME.backend_host = normalize_comfy_host(data.get("host"))
        RUNTIME.comfy_port = int(data.get("comfy_port") or RUNTIME.comfy_port)
        RUNTIME.forge_port = int(data.get("forge_port") or RUNTIME.forge_port)
        input_folder = str(data.get("comfy_input_folder") or "")
        RUNTIME.comfy_input_folder = Path(input_folder) if input_folder else None
        output_folder = str(data.get("comfy_output_folder") or "")
        RUNTIME.comfy_output_folder = Path(output_folder) if output_folder else None
        folder = data.get("workflows_folder")
        if folder:
            RUNTIME.workflows_folder = Path(str(folder))
        RUNTIME.generation_timeout = int(data.get("generation_timeout") or RUNTIME.generation_timeout)
        RUNTIME.idle_timeout_seconds = max(
            0,
            min(
                7 * 24 * 60 * 60,
                int(data.get("idle_timeout_seconds", RUNTIME.idle_timeout_seconds)),
            ),
        )
        RUNTIME.backend_monitor_interval_seconds = max(
            2,
            min(
                300,
                int(
                    data.get(
                        "backend_monitor_interval_seconds",
                        RUNTIME.backend_monitor_interval_seconds,
                    )
                ),
            ),
        )
    except Exception:
        LOGGER.warning("Could not read runtime.json")


def initialize_server_runtime() -> None:
    """Prepare optional dependencies while the lightweight API is responsive."""

    try:
        prepare_required_modules()
        load_runtime_file()
    except Exception as exc:
        log_exception("Critical Python initialization error")
        write_startup_status("error", str(exc) or exc.__class__.__name__)
        return
    write_startup_status("ready", "Python API is ready")
    start_backend_monitor()
    # Временные файлы очищаются после публикации ready.
    try:
        cleanup_old_temp_files()
        schedule_comfy_folder_cleanup(
            RUNTIME.comfy_input_folder, RUNTIME.comfy_output_folder
        )
    except Exception:
        log_exception("Background temporary-file cleanup failed")


# Создаёт lock/runtime-файлы, запускает workers и принимает команды до idle timeout.
def start_local_server() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((API_HOST, API_RECEIVE_PORT))
    except OSError as exc:
        # Второй экземпляр не трогает lock активного процесса.
        LOGGER.error("Could not bind port %s: %s", API_RECEIVE_PORT, exc)
        write_startup_status(
            "error",
            f"Could not open local Python API port {API_RECEIVE_PORT}: {exc}",
        )
        try:
            server.close()
        except OSError:
            pass
        return

    server.listen(8)
    server.settimeout(1.0)
    write_startup_status("starting", "Preparing Python API")

    initialization_thread = threading.Thread(
        target=initialize_server_runtime,
        name="InitializationWorker",
        daemon=True,
    )
    initialization_thread.start()

    worker_thread = threading.Thread(target=generation_worker, name="GenerationWorker", daemon=True)
    worker_thread.start()
    watcher_thread = threading.Thread(target=idle_watcher, name="IdleWatcher", daemon=True)
    watcher_thread.start()

    LOGGER.info(
        "%s %s listener started. API %s:%s, host %s, ComfyUI port %s, log=%s",
        APP_NAME,
        VERSION,
        API_HOST,
        API_RECEIVE_PORT,
        RUNTIME.backend_host,
        RUNTIME.comfy_port,
        LOG_FILE,
    )

    try:
        while not WORKER_STOP.is_set():
            try:
                client_socket, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(
                target=handle_client,
                args=(client_socket,),
                name="APIClient",
                daemon=True,
            )
            thread.start()
    finally:
        WORKER_STOP.set()
        BACKEND_MONITOR_WAKE.set()
        cancel_current_generation()
        try:
            server.close()
        except OSError:
            pass
        remove_startup_status()
        LOGGER.info("%s stopped", APP_NAME)


if __name__ == "__main__":
    write_startup_status("starting", "Starting Python API")
    try:
        start_local_server()
    except Exception as exc:
        log_exception("Critical startup error")
        write_startup_status("error", str(exc) or exc.__class__.__name__)
